#!/usr/bin/env python3
"""Behavior tests for canonical daemon tick progress artifacts."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import sys


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.daemon_progress import (
    DaemonProgressBudget,
    begin_tick,
    classify_progress,
    complete_tick,
    fail_tick,
    progress_path,
    read_progress,
)


class DaemonProgressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="daemon-progress-test-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tick_progress_uses_single_canonical_path(self) -> None:
        progress = begin_tick(self.tmp, "wakeup_runner_daemon", now=1000, pid=42)

        path = self.tmp / ".refactor-loop" / "state" / "daemon-tick-progress" / "wakeup_runner_daemon.json"
        self.assertEqual(path, progress_path(self.tmp, "wakeup_runner_daemon"))
        self.assertTrue(path.is_file())
        self.assertFalse((self.tmp / ".refactor-loop" / "state" / "daemon-progress").exists())
        self.assertEqual("begin", progress.status)
        self.assertEqual("1000-42", progress.tick_id)
        self.assertEqual(progress, read_progress(self.tmp, "wakeup_runner_daemon"))

    def test_complete_and_fail_are_terminal_progress_states(self) -> None:
        progress = begin_tick(self.tmp, "phase9_router_daemon", now=1000, pid=77)
        complete = complete_tick(self.tmp, progress, now=1010)

        self.assertEqual("complete", complete.status)
        self.assertEqual(1010, complete.completed_at)
        healthy = classify_progress(
            self.tmp,
            "phase9_router_daemon",
            now=1015,
            budget=DaemonProgressBudget(completed_max_age_seconds=60, in_progress_max_age_seconds=15),
        )
        self.assertEqual("complete", healthy.state)
        self.assertTrue(healthy.healthy)
        self.assertEqual(5, healthy.age_seconds)

        failed = fail_tick(self.tmp, progress, now=1020, message="RuntimeError: long failure detail")
        self.assertEqual("fail", failed.status)
        health = classify_progress(
            self.tmp,
            "phase9_router_daemon",
            now=1021,
            budget=DaemonProgressBudget(completed_max_age_seconds=60, in_progress_max_age_seconds=15),
        )
        self.assertEqual("failed", health.state)
        self.assertFalse(health.healthy)
        self.assertIn("RuntimeError", health.reason)

    def test_complete_and_begin_use_separate_freshness_budgets(self) -> None:
        progress = begin_tick(self.tmp, "closed_label_reconciler", now=1000, pid=88)
        complete_tick(self.tmp, progress, now=1000)
        budget = DaemonProgressBudget(completed_max_age_seconds=2400, in_progress_max_age_seconds=600)

        healthy = classify_progress(self.tmp, "closed_label_reconciler", now=2711, budget=budget)

        self.assertEqual("complete", healthy.state)
        self.assertTrue(healthy.healthy)
        self.assertEqual(1711, healthy.age_seconds)

        begin_tick(self.tmp, "closed_label_reconciler", now=3000, pid=89)
        overdue = classify_progress(self.tmp, "closed_label_reconciler", now=3600, budget=budget)

        self.assertEqual("overdue", overdue.state)
        self.assertFalse(overdue.healthy)
        self.assertEqual("progress-overdue:600s", overdue.reason)

    def test_progress_overdue_and_malformed_fail_closed(self) -> None:
        progress = begin_tick(self.tmp, "concurrency_monitor", now=1000, pid=99)
        health = classify_progress(
            self.tmp,
            "concurrency_monitor",
            now=1100,
            budget=DaemonProgressBudget(completed_max_age_seconds=60, in_progress_max_age_seconds=60),
        )

        self.assertEqual("overdue", health.state)
        self.assertEqual("progress-overdue:100s", health.reason)

        path = progress_path(self.tmp, progress.daemon_name)
        path.write_text(json.dumps({"daemon_name": "other", "status": "complete"}) + "\n", encoding="utf-8")
        malformed = classify_progress(
            self.tmp,
            "concurrency_monitor",
            now=1100,
            budget=DaemonProgressBudget(completed_max_age_seconds=60, in_progress_max_age_seconds=60),
        )
        self.assertEqual("malformed", malformed.state)

    def test_budget_rejects_non_positive_limits(self) -> None:
        with self.assertRaisesRegex(ValueError, "completed_max_age_seconds"):
            DaemonProgressBudget(completed_max_age_seconds=0, in_progress_max_age_seconds=60)
        with self.assertRaisesRegex(ValueError, "in_progress_max_age_seconds"):
            DaemonProgressBudget(completed_max_age_seconds=60, in_progress_max_age_seconds=0)


if __name__ == "__main__":
    unittest.main()
