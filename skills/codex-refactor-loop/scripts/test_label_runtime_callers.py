#!/usr/bin/env python3
"""Runtime caller behavior for canonical and legacy label reads."""

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
    def test_concurrency_expected_dual_reads_legacy_and_canonical(self) -> None:
        monitor = object.__new__(concurrency.ConcurrencyMonitor)
        items = [
            {"number": 1, "kind": "issue", "phase": "🔧 phase:fixing", "human": "🤖 human:auto-推进"},
            {"number": 2, "kind": "pr", "phase": labels.PHASE_REVIEWING, "human": labels.HUMAN_AUTO},
            {"number": 3, "kind": "issue", "phase": "🛠️ phase:implementing", "human": "👤 human:需-maintainer-决策"},
        ]

        expected, breakdown = concurrency.ConcurrencyMonitor.compute_expected(monitor, items)

        self.assertEqual(expected, 2)
        self.assertEqual(
            breakdown,
            [
                {"id": "#1", "kind": "issue", "phase": labels.PHASE_FIXING, "expected": 1},
                {"id": "#2", "kind": "pr", "phase": labels.PHASE_REVIEWING, "expected": 1},
            ],
        )

    def test_wakeup_phase_and_actor_dual_read(self) -> None:
        self.assertEqual(wakeup_plan.phase_from_labels(("🔍 phase:design-solving",)), "phase-9-design-solving")
        self.assertEqual(wakeup_plan.phase_from_labels((labels.PHASE_CONSENSUS_REACHED,)), "phase-2-implementing")
        self.assertEqual(wakeup_plan.actor_from_labels(("👤 human:需-maintainer-决策",), "issue"), "controller")
        self.assertEqual(wakeup_plan.actor_from_labels((labels.PHASE_FIXING, labels.HUMAN_AUTO), "PR"), "fix-codex")


if __name__ == "__main__":
    unittest.main()
