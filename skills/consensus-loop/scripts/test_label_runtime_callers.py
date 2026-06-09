#!/usr/bin/env python3
"""Runtime caller behavior for canonical-only label reads."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import labels
from codex_refactor_loop.monitors import concurrency
from codex_refactor_loop import wakeup_plan


class LabelRuntimeCallerTests(unittest.TestCase):
    def test_concurrency_expected_ignores_historical_residue_and_reads_canonical(self) -> None:
        monitor = object.__new__(concurrency.ConcurrencyMonitor)
        items = [
            {"number": 1, "kind": "issue", "phase": "🔧 phase:fixing", "human": "🤖 human:auto-推进", "labels": ["auto-loop", "🔧 phase:fixing", "🤖 human:auto-推进"]},
            {"number": 2, "kind": "pr", "phase": labels.PHASE_REVIEWING, "human": labels.HUMAN_AUTO, "labels": [labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO]},
            {"number": 3, "kind": "issue", "phase": labels.PHASE_IMPLEMENTING, "human": labels.HUMAN_MAINTAINER_DECISION, "labels": [labels.MANAGED, labels.PHASE_IMPLEMENTING, labels.HUMAN_MAINTAINER_DECISION]},
        ]

        expected, breakdown = concurrency.ConcurrencyMonitor.compute_expected(monitor, items)

        self.assertEqual(expected, 1)
        self.assertEqual(
            breakdown,
            [
                {"id": "#2", "kind": "pr", "phase": labels.PHASE_REVIEWING, "expected": 1},
            ],
        )

    def test_wakeup_phase_and_actor_are_canonical_only(self) -> None:
        self.assertEqual(wakeup_plan.phase_from_labels(("🔍 phase:design-solving",)), "work-intake")
        self.assertEqual(wakeup_plan.phase_from_labels((labels.PHASE_CONSENSUS_REACHED,)), "implementation")
        self.assertEqual(wakeup_plan.actor_from_labels(("👤 human:需-maintainer-决策",), "issue"), "controller-triage")
        self.assertEqual(wakeup_plan.actor_from_labels((labels.PHASE_FIXING, labels.HUMAN_AUTO), "PR"), "fix-codex")


if __name__ == "__main__":
    unittest.main()
