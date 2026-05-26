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

    def write_dispatch(self, priority: str, task_id: str, reason: str | None = None) -> Path:
        priority_dir = self.refactor_loop / "dispatch-queue" / priority
        priority_dir.mkdir(parents=True, exist_ok=True)
        prompt = self.refactor_loop / "prompts" / f"{task_id}.md"
        log = self.refactor_loop / "logs" / f"{task_id}.log"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        log.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("prompt\n", encoding="utf-8")
        payload = {
            "task_id": task_id,
            "cd": str(self.repo),
            "prompt": str(prompt),
            "log": str(log),
            "stall": 5400,
            "queued_at": "2026-05-26T07:25:00Z",
            "reason": reason or f"{task_id} needed",
        }
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


if __name__ == "__main__":
    unittest.main()
