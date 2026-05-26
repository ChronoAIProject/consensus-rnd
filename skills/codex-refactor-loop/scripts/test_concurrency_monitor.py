#!/usr/bin/env python3
"""Behavior tests for concurrency_monitor dispatch queue auto-topup."""

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent


class ConcurrencyMonitorDispatchQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.old_env = os.environ.copy()
        os.environ["REPO_ROOT"] = str(self.repo)
        os.environ["CODEX_FLOOR"] = "2"
        sys.path.insert(0, str(SCRIPT_DIR))
        import concurrency_monitor

        self.monitor = importlib.reload(concurrency_monitor)
        self.refactor_loop = self.repo / ".refactor-loop"

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)
        try:
            sys.path.remove(str(SCRIPT_DIR))
        except ValueError:
            pass
        self.tmp.cleanup()

    def write_dispatch(self, priority: str, task_id: str, reason: str | None = None, *, include_task_id: bool = True) -> Path:
        priority_dir = self.refactor_loop / "dispatch-queue" / priority
        priority_dir.mkdir(parents=True, exist_ok=True)
        prompt = self.refactor_loop / "prompts" / f"{task_id}.md"
        log = self.refactor_loop / "logs" / f"{task_id}.log"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        log.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("prompt\n", encoding="utf-8")
        payload = {
            "cd": str(self.repo),
            "prompt": str(prompt),
            "log": str(log),
            "stall": 5400,
            "queued_at": "2026-05-26T07:25:00Z",
            "reason": reason or f"{task_id} needed",
        }
        if include_task_id:
            payload["task_id"] = task_id
        path = priority_dir / f"{task_id}.dispatch.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def fake_popen(self, calls: list[list[str]]):
        def _fake_popen(cmd: list[str], **_: object) -> object:
            calls.append(cmd)
            return object()

        return _fake_popen

    def test_monitor_dispatches_from_queue_when_below_floor(self) -> None:
        self.write_dispatch("p1", "fix-pr44-round-3")
        self.write_dispatch("p1", "audit-iter-5")
        calls: list[list[str]] = []

        with mock.patch.object(self.monitor.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=0):
                self.monitor.top_up_from_dispatch_queue(actual=0, floor=2)

        self.assertEqual(len(calls), 2)
        self.assertEqual(list((self.refactor_loop / "dispatch-queue" / "p1").glob("*.dispatch.json")), [])
        archived = sorted((self.refactor_loop / "dispatch-dispatched").glob("*.json"))
        self.assertEqual([p.name for p in archived], ["audit-iter-5.json", "fix-pr44-round-3.json"])

    def test_monitor_respects_priority_order(self) -> None:
        self.write_dispatch("p2", "p2-task")
        self.write_dispatch("p1", "p1-task")
        self.write_dispatch("p0", "p0-task")
        calls: list[list[str]] = []

        with mock.patch.object(self.monitor.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            self.monitor.dispatch_one_from_queue()

        self.assertEqual(len(calls), 1)
        self.assertTrue(any(arg.endswith("p0-task.md") for arg in calls[0]))
        self.assertFalse((self.refactor_loop / "dispatch-queue" / "p0" / "p0-task.dispatch.json").exists())
        self.assertTrue((self.refactor_loop / "dispatch-queue" / "p1" / "p1-task.dispatch.json").exists())

    def test_monitor_does_not_overshoot_floor(self) -> None:
        for i in range(5):
            self.write_dispatch("p1", f"task-{i}")
        calls: list[list[str]] = []

        with mock.patch.object(self.monitor.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            self.monitor.top_up_from_dispatch_queue(actual=2, floor=2)

        self.assertEqual(calls, [])
        remaining = list((self.refactor_loop / "dispatch-queue" / "p1").glob("*.dispatch.json"))
        self.assertEqual(len(remaining), 5)

    def test_monitor_emits_concurrency_low_when_queue_empty(self) -> None:
        with mock.patch.object(self.monitor, "list_auto_loop_issues", return_value=[]):
            with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=0):
                self.monitor.tick()

        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("CONCURRENCY_LOW:actual=0 expected=0 queue=0", events)

    def test_monitor_archives_dispatched_json_with_timestamp(self) -> None:
        self.write_dispatch("p0", "fix-pr44-round-3", reason="PR #44 r3 fix needed")
        calls: list[list[str]] = []

        with mock.patch.object(self.monitor.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            self.monitor.dispatch_one_from_queue()

        archive = self.refactor_loop / "dispatch-dispatched" / "fix-pr44-round-3.json"
        self.assertTrue(archive.exists())
        payload = json.loads(archive.read_text(encoding="utf-8"))
        self.assertEqual(payload["task_id"], "fix-pr44-round-3")
        self.assertEqual(payload["priority"], "p0")
        self.assertRegex(payload["dispatch_at"], r"^20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("DISPATCH_FIRED:fix-pr44-round-3:p0:PR #44 r3 fix needed", events)

    def test_tick_p0_no_gap_with_queued_dispatch_fires_topup(self) -> None:
        self.write_dispatch("p0", "fix-pr57-round-1-a")
        self.write_dispatch("p0", "fix-pr57-round-1-b")
        calls: list[list[str]] = []
        counts = [0, 1, 2]

        with mock.patch.object(self.monitor.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            with mock.patch.object(self.monitor, "count_in_flight_codex", side_effect=lambda: counts.pop(0)):
                with mock.patch.object(
                    self.monitor,
                    "list_auto_loop_issues",
                    return_value=[{"number": 57, "kind": "pr", "phase": "🔧 phase:fixing", "human": "🤖 human:auto-推进"}],
                ):
                    self.monitor.tick()

        self.assertEqual(len(calls), 2)
        alert = (self.refactor_loop / ".concurrency-alert.log").read_text(encoding="utf-8")
        self.assertIn("P0 no-gap-violation", alert)
        state = json.loads((self.refactor_loop / ".concurrency-monitor-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["zero_streak"], 1)
        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("DISPATCH_FIRED:fix-pr57-round-1-a:p0:fix-pr57-round-1-a needed", events)
        self.assertIn("DISPATCH_FIRED:fix-pr57-round-1-b:p0:fix-pr57-round-1-b needed", events)

    def test_tick_below_floor_with_non_empty_queue_dispatches(self) -> None:
        for i in range(3):
            self.write_dispatch("p1", f"floor-task-{i}")
        calls: list[list[str]] = []
        counts = [2, 3, 4]
        os.environ["CODEX_FLOOR"] = "4"
        self.monitor = importlib.reload(self.monitor)

        with mock.patch.object(self.monitor.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            with mock.patch.object(self.monitor, "count_in_flight_codex", side_effect=lambda: counts.pop(0)):
                with mock.patch.object(self.monitor, "list_auto_loop_issues", return_value=[]):
                    self.monitor.tick()

        self.assertEqual(len(calls), 2)
        remaining = list((self.refactor_loop / "dispatch-queue" / "p1").glob("*.dispatch.json"))
        self.assertEqual(len(remaining), 1)

    def test_tick_dispatches_toward_expected_count_not_just_floor(self) -> None:
        """When expected > floor, tick fires deficit toward expected (not just floor)."""
        for i in range(4):
            self.write_dispatch("p1", f"expected-task-{i}")
        active_items = [
            {"number": i, "kind": "issue", "phase": "🔧 phase:fixing", "human": "🤖 human:auto-推进"}
            for i in range(4)
        ]
        calls: list[list[str]] = []
        counts = [2, 3, 4]

        with mock.patch.object(self.monitor.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            with mock.patch.object(self.monitor, "count_in_flight_codex", side_effect=lambda: counts.pop(0)):
                with mock.patch.object(self.monitor, "list_auto_loop_issues", return_value=active_items):
                    self.monitor.tick()

        self.assertEqual(len(calls), 2)
        remaining = list((self.refactor_loop / "dispatch-queue" / "p1").glob("*.dispatch.json"))
        self.assertEqual(len(remaining), 2)

    def test_dispatch_json_without_stall_uses_default_5400(self) -> None:
        """A dispatch JSON missing the `stall` field falls back to --stall 5400 on launch."""
        dispatch = self.write_dispatch("p1", "default-stall-task")
        payload = json.loads(dispatch.read_text(encoding="utf-8"))
        del payload["stall"]
        dispatch.write_text(json.dumps(payload), encoding="utf-8")
        calls: list[list[str]] = []

        with mock.patch.object(self.monitor.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            self.monitor.dispatch_one_from_queue()

        self.assertEqual(len(calls), 1)
        stall_index = calls[0].index("--stall")
        self.assertEqual(calls[0][stall_index + 1], "5400")

    def test_configured_floor_invalid_falls_back(self) -> None:
        os.environ["CODEX_FLOOR"] = "abc"
        self.monitor = importlib.reload(self.monitor)

        self.assertEqual(self.monitor.configured_floor(), 5)

    def test_configured_floor_below_minimum_clamps(self) -> None:
        os.environ["CODEX_FLOOR"] = "0"
        self.monitor = importlib.reload(self.monitor)

        self.assertEqual(self.monitor.configured_floor(), 2)

    def test_archive_collision_writes_timestamp_suffix(self) -> None:
        self.write_dispatch("p0", "collision-task")
        dispatched = self.refactor_loop / "dispatch-dispatched"
        dispatched.mkdir(parents=True, exist_ok=True)
        (dispatched / "collision-task.json").write_text("{}\n", encoding="utf-8")
        calls: list[list[str]] = []

        with mock.patch.object(self.monitor.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            with mock.patch.object(self.monitor, "utc_ts", return_value="2026-05-26T08:09:10Z"):
                self.monitor.dispatch_one_from_queue()

        self.assertTrue((dispatched / "collision-task.json").exists())
        suffixed = dispatched / "collision-task-20260526T080910Z.json"
        self.assertTrue(suffixed.exists())
        payload = json.loads(suffixed.read_text(encoding="utf-8"))
        self.assertEqual(payload["task_id"], "collision-task")

    def test_dispatch_one_derives_task_id_from_filename(self) -> None:
        self.write_dispatch("p2", "filename-task", include_task_id=False)
        calls: list[list[str]] = []

        with mock.patch.object(self.monitor.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            fired = self.monitor.dispatch_one_from_queue()

        self.assertEqual(fired, ("filename-task", "p2", "filename-task needed"))
        self.assertEqual(len(calls), 1)
        archive = self.refactor_loop / "dispatch-dispatched" / "filename-task.json"
        self.assertTrue(archive.exists())
        payload = json.loads(archive.read_text(encoding="utf-8"))
        self.assertEqual(payload["task_id"], "filename-task")

    # Refactor (iter4/skill-count-cli-canonical): Old pattern: controller 手 ps | grep
    # spawn-codex.sh 重新实现 count_in_flight_codex 逻辑 -> 容易跟 daemon 算法漂移。
    # New principle: 暴露 `--count-only` / `--list-codex` 让 controller 直接复用 daemon
    # 自带的 canonical 算法(per 2026-05-26 maintainer-directive)。
    def test_count_only_cli_prints_canonical_in_flight_codex_count(self) -> None:
        import io
        fake_ps = (
            f"bash spawn-codex.sh --cd {self.repo} --prompt /tmp/a.md --log /tmp/a.log\n"
            f"bash -c spawn-codex.sh --cd {self.repo} --prompt /tmp/a.md --log /tmp/a.log\n"
            f"bash spawn-codex.sh --cd {self.repo} --prompt /tmp/b.md --log /tmp/b.log\n"
            "bash spawn-codex.sh --cd /Users/other-host/repo --prompt /tmp/c.md --log /tmp/c.log\n"
        )
        captured = io.StringIO()
        with mock.patch.object(
            self.monitor.subprocess,
            "run",
            return_value=mock.Mock(stdout=fake_ps, returncode=0),
        ), mock.patch.object(sys, "stdout", captured):
            exit_code = self.monitor.main(["--count-only"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(captured.getvalue().strip(), "2")

    def test_list_codex_cli_prints_one_supervisor_per_line(self) -> None:
        import io
        fake_ps = (
            f"bash spawn-codex.sh --cd {self.repo} --prompt /tmp/a.md --log /tmp/a.log\n"
            f"bash -c spawn-codex.sh --cd {self.repo} --prompt /tmp/a.md --log /tmp/a.log\n"
            f"bash spawn-codex.sh --cd {self.repo} --prompt /tmp/b.md --log /tmp/b.log\n"
        )
        captured = io.StringIO()
        with mock.patch.object(
            self.monitor.subprocess,
            "run",
            return_value=mock.Mock(stdout=fake_ps, returncode=0),
        ), mock.patch.object(sys, "stdout", captured):
            exit_code = self.monitor.main(["--list-codex"])

        self.assertEqual(exit_code, 0)
        lines = [line for line in captured.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertIn("spawn-codex.sh", line)
            self.assertIn(str(self.repo), line)
            self.assertNotIn(" -c ", line)


if __name__ == "__main__":
    unittest.main()
