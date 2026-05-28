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

    def write_dispatch(
        self,
        priority: str,
        task_id: str,
        reason: str | None = None,
        *,
        include_task_id: bool = True,
        cd: Path | None = None,
    ) -> Path:
        priority_dir = self.refactor_loop / "dispatch-queue" / priority
        priority_dir.mkdir(parents=True, exist_ok=True)
        prompt = self.refactor_loop / "prompts" / f"{task_id}.md"
        log = self.refactor_loop / "logs" / f"{task_id}.log"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        log.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("prompt\n", encoding="utf-8")
        if cd is None:
            cd = self.repo if task_id.startswith(self.monitor.MAIN_READONLY_DISPATCH_PREFIXES) else self.repo / ".worktrees" / task_id
        payload = {
            "cd": str(cd),
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

    def assert_dispatch_rejected(self, task_id: str, reason: str, calls: list[list[str]]) -> None:
        self.assertEqual(calls, [])
        self.assertFalse((self.refactor_loop / "dispatch-queue" / "p0" / f"{task_id}.dispatch.json").exists())
        rejected = self.refactor_loop / "dispatch-rejected" / f"{task_id}.json"
        self.assertTrue(rejected.exists())
        payload = json.loads(rejected.read_text(encoding="utf-8"))
        self.assertEqual(payload["reject_reason"], reason)
        self.assertEqual(payload["priority"], "p0")
        self.assertIn("source_dispatch_file", payload)
        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn(f"DISPATCH_REJECTED:{task_id}:p0:main-worktree-cd:{reason}", events)

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

    # Refactor (iter6/issue-133):
    #   Old pattern: concurrency_monitor 把 queue payload[cd] 直接交给 spawn-codex.sh --cd,可让 mutable task 跑在 repo-root/main worktree
    #   New principle: structural consensus: dispatch queue mutable-prefix cwd guard,无 shared workspace policy。详见 .refactor-loop/runs/phase9-issue133-r4-judge.md
    def test_rejects_mutable_dispatch_to_repo_root(self) -> None:
        self.write_dispatch("p0", "fix-pr44-round-3", cd=self.repo)
        calls: list[list[str]] = []

        with mock.patch.object(self.monitor.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            fired = self.monitor.dispatch_one_from_queue()

        self.assertIsNone(fired)
        self.assert_dispatch_rejected("fix-pr44-round-3", "repo-root-cd", calls)

    # Refactor (iter6/issue-133):
    #   Old pattern: concurrency_monitor 把 queue payload[cd] 直接交给 spawn-codex.sh --cd,可让 mutable task 跑在 repo-root/main worktree
    #   New principle: structural consensus: dispatch queue mutable-prefix cwd guard,无 shared workspace policy。详见 .refactor-loop/runs/phase9-issue133-r4-judge.md
    def test_rejects_dispatch_missing_cd(self) -> None:
        path = self.write_dispatch("p0", "fix-pr44-round-3")
        payload = json.loads(path.read_text(encoding="utf-8"))
        del payload["cd"]
        path.write_text(json.dumps(payload), encoding="utf-8")
        calls: list[list[str]] = []

        with mock.patch.object(self.monitor.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            fired = self.monitor.dispatch_one_from_queue()

        self.assertIsNone(fired)
        self.assert_dispatch_rejected("fix-pr44-round-3", "missing-cd", calls)

    # Refactor (iter6/issue-133):
    #   Old pattern: concurrency_monitor 把 queue payload[cd] 直接交给 spawn-codex.sh --cd,可让 mutable task 跑在 repo-root/main worktree
    #   New principle: structural consensus: dispatch queue mutable-prefix cwd guard,无 shared workspace policy。详见 .refactor-loop/runs/phase9-issue133-r4-judge.md
    def test_rejects_dispatch_relative_cd(self) -> None:
        self.write_dispatch("p0", "fix-pr44-round-3", cd=Path(".worktrees/fix-pr44-round-3"))
        calls: list[list[str]] = []

        with mock.patch.object(self.monitor.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            fired = self.monitor.dispatch_one_from_queue()

        self.assertIsNone(fired)
        self.assert_dispatch_rejected("fix-pr44-round-3", "relative-cd", calls)

    # Refactor (iter6/issue-133):
    #   Old pattern: concurrency_monitor 把 queue payload[cd] 直接交给 spawn-codex.sh --cd,可让 mutable task 跑在 repo-root/main worktree
    #   New principle: structural consensus: dispatch queue mutable-prefix cwd guard,无 shared workspace policy。详见 .refactor-loop/runs/phase9-issue133-r4-judge.md
    def test_rejects_dispatch_cd_outside_repo(self) -> None:
        outside_repo = self.repo.parent / "outside-repo"
        self.write_dispatch("p0", "fix-pr44-round-3", cd=outside_repo)
        calls: list[list[str]] = []

        with mock.patch.object(self.monitor.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            fired = self.monitor.dispatch_one_from_queue()

        self.assertIsNone(fired)
        self.assert_dispatch_rejected("fix-pr44-round-3", "outside-repo", calls)

    # Refactor (iter6/issue-133):
    #   Old pattern: concurrency_monitor 把 queue payload[cd] 直接交给 spawn-codex.sh --cd,可让 mutable task 跑在 repo-root/main worktree
    #   New principle: structural consensus: dispatch queue mutable-prefix cwd guard,无 shared workspace policy。详见 .refactor-loop/runs/phase9-issue133-r4-judge.md
    def test_rejects_mutable_dispatch_inside_repo_but_outside_worktrees(self) -> None:
        self.write_dispatch("p0", "fix-pr44-round-3", cd=self.repo / "tmp" / "fix-pr44-round-3")
        calls: list[list[str]] = []

        with mock.patch.object(self.monitor.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            fired = self.monitor.dispatch_one_from_queue()

        self.assertIsNone(fired)
        self.assert_dispatch_rejected("fix-pr44-round-3", "outside-worktrees", calls)

    # Refactor (iter6/issue-133):
    #   Old pattern: concurrency_monitor 把 queue payload[cd] 直接交给 spawn-codex.sh --cd,可让 mutable task 跑在 repo-root/main worktree
    #   New principle: structural consensus: dispatch queue mutable-prefix cwd guard,无 shared workspace policy。详见 .refactor-loop/runs/phase9-issue133-r4-judge.md
    def test_rejects_mutable_dispatch_to_worktrees_root(self) -> None:
        self.write_dispatch("p0", "fix-pr44-round-3", cd=self.repo / ".worktrees")
        calls: list[list[str]] = []

        with mock.patch.object(self.monitor.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            fired = self.monitor.dispatch_one_from_queue()

        self.assertIsNone(fired)
        self.assert_dispatch_rejected("fix-pr44-round-3", "worktrees-root-cd", calls)

    # Refactor (iter6/issue-133):
    #   Old pattern: concurrency_monitor 把 queue payload[cd] 直接交给 spawn-codex.sh --cd,可让 mutable task 跑在 repo-root/main worktree
    #   New principle: structural consensus: dispatch queue mutable-prefix cwd guard,无 shared workspace policy。详见 .refactor-loop/runs/phase9-issue133-r4-judge.md
    def test_accepts_mutable_dispatch_inside_worktrees(self) -> None:
        worktree = self.repo / ".worktrees" / "fix-pr44-round-3"
        self.write_dispatch("p0", "fix-pr44-round-3", cd=worktree)
        calls: list[list[str]] = []

        with mock.patch.object(self.monitor.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            fired = self.monitor.dispatch_one_from_queue()

        self.assertEqual(fired, ("fix-pr44-round-3", "p0", "fix-pr44-round-3 needed"))
        self.assertEqual(len(calls), 1)
        cd_index = calls[0].index("--cd")
        self.assertEqual(calls[0][cd_index + 1], str(worktree))
        self.assertTrue((self.refactor_loop / "dispatch-dispatched" / "fix-pr44-round-3.json").exists())
        self.assertFalse((self.refactor_loop / "dispatch-rejected").exists())

    # Refactor (iter6/issue-133):
    #   Old pattern: concurrency_monitor 把 queue payload[cd] 直接交给 spawn-codex.sh --cd,可让 mutable task 跑在 repo-root/main worktree
    #   New principle: structural consensus: dispatch queue mutable-prefix cwd guard,无 shared workspace policy。详见 .refactor-loop/runs/phase9-issue133-r4-judge.md
    def test_allows_main_readonly_dispatch_prefixes(self) -> None:
        task_ids = ("audit-iter-5", "phase9-issue133-r4-minimal", "review-pr44-tests")

        for task_id in task_ids:
            with self.subTest(task_id=task_id):
                self.write_dispatch("p0", task_id, cd=self.repo)
                calls: list[list[str]] = []

                with mock.patch.object(self.monitor.subprocess, "Popen", side_effect=self.fake_popen(calls)):
                    fired = self.monitor.dispatch_one_from_queue()

                self.assertEqual(fired, (task_id, "p0", f"{task_id} needed"))
                self.assertEqual(len(calls), 1)
                self.assertTrue((self.refactor_loop / "dispatch-dispatched" / f"{task_id}.json").exists())

    # Refactor (iter6/issue-133):
    #   Old pattern: concurrency_monitor 把 queue payload[cd] 直接交给 spawn-codex.sh --cd,可让 mutable task 跑在 repo-root/main worktree
    #   New principle: structural consensus: dispatch queue mutable-prefix cwd guard,无 shared workspace policy。详见 .refactor-loop/runs/phase9-issue133-r4-judge.md
    def test_rejected_dispatch_does_not_block_next_queue_item(self) -> None:
        self.write_dispatch("p0", "fix-pr44-round-3", cd=self.repo)
        self.write_dispatch("p0", "fix-pr44-round-4", cd=self.repo / ".worktrees" / "fix-pr44-round-4")
        calls: list[list[str]] = []

        with mock.patch.object(self.monitor.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            fired = self.monitor.dispatch_one_from_queue()

        self.assertEqual(fired, ("fix-pr44-round-4", "p0", "fix-pr44-round-4 needed"))
        self.assertEqual(len(calls), 1)
        self.assertTrue((self.refactor_loop / "dispatch-rejected" / "fix-pr44-round-3.json").exists())
        self.assertTrue((self.refactor_loop / "dispatch-dispatched" / "fix-pr44-round-4.json").exists())
        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("DISPATCH_REJECTED:fix-pr44-round-3:p0:main-worktree-cd:repo-root-cd", events)
        self.assertIn("DISPATCH_FIRED:fix-pr44-round-4:p0:fix-pr44-round-4 needed", events)

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

    # Refactor (iter4/skill-count-cli-canonical): Old pattern: controller ran
    # ps | grep manually and spawn-codex.sh reimplemented count_in_flight_codex,
    # making drift from the daemon algorithm likely.
    # New principle: expose `--count-only` / `--list-codex` so the controller
    # can directly reuse the daemon's canonical algorithm
    # (per 2026-05-26 maintainer-directive).
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

    # Refactor (cli-readonly-fallback): Old pattern: --count-only / --list-codex /
    # --once raised RuntimeError at module load when REPO_ROOT was unset, even
    # though SKILL.md mandates them as canonical CLI for controllers (which
    # often invoke from a repo dir without sourcing host.env first). New
    # principle: read-only CLI flags auto-set ALLOW_GIT_ROOT_FALLBACK so the
    # CLI works without env priming; daemon mode (no flag) stays strict.
    def test_readonly_cli_flags_auto_allow_git_root_fallback_when_repo_root_unset(self) -> None:
        import subprocess as real_subprocess
        real_subprocess.run(["git", "init", "-q"], cwd=str(self.repo), check=True)
        env = {k: v for k, v in os.environ.items() if k != "REPO_ROOT"}
        env.pop("ALLOW_GIT_ROOT_FALLBACK", None)
        env["PATH"] = os.environ.get("PATH", "")

        script = Path(self.monitor.__file__)
        for flag in ("--count-only", "--list-codex"):
            with self.subTest(flag=flag):
                result = real_subprocess.run(
                    [sys.executable, str(script), flag],
                    cwd=str(self.repo),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    msg=f"{flag} should not fail without REPO_ROOT; stderr={result.stderr[:300]}",
                )

    def test_daemon_mode_without_repo_root_still_fails_closed(self) -> None:
        import subprocess as real_subprocess
        env = {k: v for k, v in os.environ.items() if k != "REPO_ROOT"}
        env.pop("ALLOW_GIT_ROOT_FALLBACK", None)
        env["PATH"] = os.environ.get("PATH", "")

        script = Path(self.monitor.__file__)
        result = real_subprocess.run(
            [sys.executable, str(script)],
            cwd="/tmp",
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("REPO_ROOT is unset", result.stderr)

    def test_degradation_hook_writes_alert_and_existing_pending_event_only_on_failure(self) -> None:
        os.environ["DEGRADATION_WATCH_INTERVAL_SECONDS"] = "60"
        self.monitor = importlib.reload(self.monitor)
        state: dict[str, object] = {}
        result = self.monitor.subprocess.CompletedProcess(["checker"], 1, stdout="bad drift\n", stderr="")

        with mock.patch.object(self.monitor, "run_skill_degradation_check", return_value=result):
            self.monitor.maybe_run_skill_degradation_watch(state)

        alert = (self.refactor_loop / ".degradation-alert.log").read_text(encoding="utf-8")
        self.assertIn("skill-degradation-alert returncode=1", alert)
        self.assertIn("bad drift", alert)
        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("skill-degradation-alert returncode=1 log=.refactor-loop/.degradation-alert.log", events)
        self.assertFalse((self.repo / "skills").exists())
        self.assertFalse((self.refactor_loop / "dispatch-dispatched").exists())

    def test_maybe_run_skill_degradation_watch_emits_alert_on_timeout(self) -> None:
        os.environ["DEGRADATION_WATCH_INTERVAL_SECONDS"] = "60"
        self.monitor = importlib.reload(self.monitor)
        state: dict[str, object] = {}
        timeout = self.monitor.subprocess.TimeoutExpired(cmd=["checker"], timeout=7)

        with mock.patch.object(self.monitor, "run_skill_degradation_check", side_effect=timeout):
            self.monitor.maybe_run_skill_degradation_watch(state)

        alert = (self.refactor_loop / ".degradation-alert.log").read_text(encoding="utf-8")
        self.assertIn("skill-degradation-alert checker-error", alert)
        self.assertIn('"error": "timeout after 7s"', alert)
        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("skill-degradation-alert checker-error log=.refactor-loop/.degradation-alert.log", events)

    def test_maybe_run_skill_degradation_watch_emits_alert_on_generic_exception(self) -> None:
        os.environ["DEGRADATION_WATCH_INTERVAL_SECONDS"] = "60"
        self.monitor = importlib.reload(self.monitor)
        state: dict[str, object] = {}

        with mock.patch.object(
            self.monitor,
            "run_skill_degradation_check",
            side_effect=RuntimeError("checker crashed"),
        ):
            self.monitor.maybe_run_skill_degradation_watch(state)

        alert = (self.refactor_loop / ".degradation-alert.log").read_text(encoding="utf-8")
        self.assertIn("skill-degradation-alert checker-error", alert)
        self.assertIn("\"error\": \"RuntimeError('checker crashed')\"", alert)
        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("skill-degradation-alert checker-error log=.refactor-loop/.degradation-alert.log", events)

    def test_degradation_hook_is_throttled(self) -> None:
        os.environ["DEGRADATION_WATCH_INTERVAL_SECONDS"] = "60"
        self.monitor = importlib.reload(self.monitor)
        state = {"last_degradation_watch_at": 1_000}

        with mock.patch.object(self.monitor.time, "time", return_value=1_030):
            with mock.patch.object(self.monitor, "run_skill_degradation_check") as run_check:
                self.monitor.maybe_run_skill_degradation_watch(state)

        run_check.assert_not_called()
        self.assertFalse((self.refactor_loop / ".degradation-alert.log").exists())

    def test_degradation_hook_success_writes_no_alert(self) -> None:
        os.environ["DEGRADATION_WATCH_INTERVAL_SECONDS"] = "60"
        self.monitor = importlib.reload(self.monitor)
        state: dict[str, object] = {}
        result = self.monitor.subprocess.CompletedProcess(["checker"], 0, stdout="skill-degradation: ok\n", stderr="")

        with mock.patch.object(self.monitor, "run_skill_degradation_check", return_value=result):
            self.monitor.maybe_run_skill_degradation_watch(state)

        self.assertFalse((self.refactor_loop / ".degradation-alert.log").exists())
        self.assertFalse((self.refactor_loop / ".controller-pending-events.log").exists())

    def test_degradation_hook_disabled_by_zero_interval(self) -> None:
        os.environ["DEGRADATION_WATCH_INTERVAL_SECONDS"] = "0"
        self.monitor = importlib.reload(self.monitor)

        with mock.patch.object(self.monitor, "run_skill_degradation_check") as run_check:
            self.monitor.maybe_run_skill_degradation_watch({})

        run_check.assert_not_called()


class SnapshotDaemonHealthFieldTests(unittest.TestCase):
    """Producer side of the statusline daemon-health extension.

    Daemon heartbeat staleness is collected by concurrency_monitor and surfaced
    in the snapshot, so the consumer (statusline.sh) does not need to enumerate
    daemons itself. Discovery is dynamic via heartbeat file presence.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.old_env = os.environ.copy()
        os.environ["REPO_ROOT"] = str(self.repo)
        os.environ["CODEX_FLOOR"] = "2"
        sys.path.insert(0, str(SCRIPT_DIR))
        import concurrency_monitor

        self.monitor = importlib.reload(concurrency_monitor)
        self.heartbeats = self.repo / ".refactor-loop" / "heartbeats"
        self.heartbeats.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)
        try:
            sys.path.remove(str(SCRIPT_DIR))
        except ValueError:
            pass
        self.tmp.cleanup()

    def _write_heartbeat(self, name: str, age_seconds: int, now: float) -> None:
        (self.heartbeats / f"{name}.ts").write_text(str(int(now - age_seconds)))

    def test_read_daemon_heartbeats_fresh_is_not_stale(self) -> None:
        now = 1_000_000.0
        self._write_heartbeat("concurrency_monitor", 10, now)
        result = self.monitor.read_daemon_heartbeats(now=now)
        self.assertEqual(result["concurrency_monitor"]["age_seconds"], 10)
        self.assertFalse(result["concurrency_monitor"]["stale"])

    def test_read_daemon_heartbeats_old_is_stale(self) -> None:
        now = 1_000_000.0
        self._write_heartbeat("dev_sync_daemon", 200, now)
        result = self.monitor.read_daemon_heartbeats(now=now)
        self.assertTrue(result["dev_sync_daemon"]["stale"])
        self.assertEqual(result["dev_sync_daemon"]["age_seconds"], 200)

    def test_read_daemon_heartbeats_malformed_is_stale(self) -> None:
        (self.heartbeats / "comment-monitor.ts").write_text("not-a-number\n")
        result = self.monitor.read_daemon_heartbeats(now=1_000_000.0)
        self.assertTrue(result["comment-monitor"]["stale"])
        self.assertIsNone(result["comment-monitor"]["age_seconds"])

    def test_read_daemon_heartbeats_missing_dir_returns_empty(self) -> None:
        # Wipe heartbeats dir; ensure no crash and empty result.
        import shutil

        shutil.rmtree(self.heartbeats)
        self.assertEqual(self.monitor.read_daemon_heartbeats(now=1_000_000.0), {})

    def test_read_daemon_heartbeats_discovers_dynamically(self) -> None:
        # New daemon name not in any hard-coded list should appear automatically.
        now = 1_000_000.0
        self._write_heartbeat("future_daemon", 5, now)
        result = self.monitor.read_daemon_heartbeats(now=now)
        self.assertIn("future_daemon", result)

    def test_snapshot_includes_daemons_map_and_counts(self) -> None:
        from datetime import datetime, timezone

        now_dt = datetime(2026, 5, 26, 19, 0, 0, tzinfo=timezone.utc)
        now_ts = now_dt.timestamp()
        self._write_heartbeat("concurrency_monitor", 5, now_ts)
        self._write_heartbeat("comment-monitor", 5, now_ts)
        self._write_heartbeat("dev_sync_daemon", 300, now_ts)  # stale
        self.monitor.write_statusline_snapshot(
            actual=7,
            expected=5,
            p0_streak=0,
            last_p0_at=None,
            open_pr_count=2,
            open_issue_count=4,
            now=now_dt,
        )
        snap_path = self.repo / ".refactor-loop" / "state" / "statusline-snapshot.json"
        payload = json.loads(snap_path.read_text())
        self.assertEqual(payload["daemons_total"], 3)
        self.assertEqual(payload["daemons_healthy"], 2)
        self.assertIn("daemons", payload)
        self.assertTrue(payload["daemons"]["dev_sync_daemon"]["stale"])
        self.assertFalse(payload["daemons"]["concurrency_monitor"]["stale"])

    def test_snapshot_with_no_heartbeats_writes_empty_daemons(self) -> None:
        from datetime import datetime, timezone

        # Ensure no heartbeats present.
        for hb in self.heartbeats.glob("*.ts"):
            hb.unlink()
        self.monitor.write_statusline_snapshot(
            actual=3,
            expected=3,
            p0_streak=0,
            last_p0_at=None,
            open_pr_count=0,
            open_issue_count=0,
            now=datetime(2026, 5, 26, 19, 0, 0, tzinfo=timezone.utc),
        )
        snap_path = self.repo / ".refactor-loop" / "state" / "statusline-snapshot.json"
        payload = json.loads(snap_path.read_text())
        self.assertEqual(payload["daemons"], {})
        self.assertEqual(payload["daemons_total"], 0)
        self.assertEqual(payload["daemons_healthy"], 0)


if __name__ == "__main__":
    unittest.main()
