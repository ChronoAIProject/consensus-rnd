#!/usr/bin/env python3
"""Behavior tests for owner-local reconcile tick wrappers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import closed_label_reconciler
from codex_refactor_loop import wakeup_runner
from codex_refactor_loop.monitors import comment, concurrency, progress
from codex_refactor_loop.phase9 import router
from codex_refactor_loop.sync import dev


class FakeTickOwner:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def tick(self) -> None:
        self.calls.append("tick")


class FakeRunOnceOwner:
    def __init__(self) -> None:
        self.beat = None
        self.calls = 0

    def run_once(self, beat=None):
        self.calls += 1
        self.beat = beat
        return "result"


class ReconcileTickScaffoldTests(unittest.TestCase):
    def test_owner_local_reconcile_tick_wrappers_delegate_to_owner_tick(self) -> None:
        wrappers = (
            comment.run_comment_monitor_reconcile_tick,
            progress.run_progress_reporter_reconcile_tick,
            concurrency.run_concurrency_reconcile_tick,
            dev.run_dev_sync_reconcile_tick,
            router.run_phase9_router_reconcile_tick,
        )
        for wrapper in wrappers:
            with self.subTest(wrapper=wrapper.__name__):
                owner = FakeTickOwner()
                wrapper(owner)
                self.assertEqual(["tick"], owner.calls)

    def test_closed_label_reconciler_wrapper_preserves_run_once_result_and_beat(self) -> None:
        owner = FakeRunOnceOwner()
        beat = object()

        result = closed_label_reconciler.run_closed_label_reconciler_reconcile_tick(owner, beat=beat)

        self.assertEqual("result", result)
        self.assertEqual(1, owner.calls)
        self.assertIs(beat, owner.beat)

    def test_wakeup_runner_wrapper_preserves_run_once_result(self) -> None:
        owner = FakeRunOnceOwner()

        result = wakeup_runner.run_wakeup_runner_reconcile_tick(owner)

        self.assertEqual("result", result)
        self.assertEqual(1, owner.calls)

    def test_concurrency_module_tick_reuses_owner_local_wrapper(self) -> None:
        owner = FakeTickOwner()

        with mock.patch.object(concurrency, "_default_monitor", return_value=owner):
            with mock.patch.object(concurrency, "run_concurrency_reconcile_tick") as wrapper:
                concurrency.tick()

        wrapper.assert_called_once_with(owner)

    def test_dev_module_tick_reuses_owner_local_wrapper(self) -> None:
        owner = FakeTickOwner()
        config = mock.Mock()

        with mock.patch.object(dev.IntegrationSyncDaemon, "from_config", return_value=owner):
            with mock.patch.object(dev, "run_dev_sync_reconcile_tick") as wrapper:
                dev.tick(config)

        wrapper.assert_called_once_with(owner)

    def test_no_shared_tick_result_or_registry_modules_exist(self) -> None:
        package_root = SCRIPT_DIR / "codex_refactor_loop"
        forbidden = {
            "reconcile_ticks.py",
            "reconcile_registry.py",
            "tick_helpers.py",
            "tick_outcome.py",
            "tick_outcomes.py",
        }

        self.assertEqual([], sorted(path.name for path in package_root.rglob("*.py") if path.name in forbidden))


if __name__ == "__main__":
    unittest.main()
