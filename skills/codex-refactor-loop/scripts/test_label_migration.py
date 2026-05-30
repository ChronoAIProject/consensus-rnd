#!/usr/bin/env python3
"""Behavior tests for legacy label dual-read migration."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import labels


class LabelMigrationTests(unittest.TestCase):
    def assert_alias(self, legacy: str, expected: set[str]) -> None:
        projection = labels.normalize_label_set([legacy])
        self.assertEqual(projection.canonical, frozenset(expected))
        if expected:
            self.assertIn(legacy, projection.legacy)

    def test_required_legacy_aliases_normalize_to_canonical_labels(self) -> None:
        cases = {
            "auto-loop": {labels.MANAGED},
            "phase9-auto-solve": {labels.MANAGED, labels.PHASE_DESIGN_SOLVING, labels.HUMAN_AUTO},
            "refactor-design-needed": {labels.MANAGED, labels.PHASE_DESIGN_SOLVING, labels.HUMAN_AUTO},
            "auto-loop-triage": {labels.TRIAGE_PENDING},
            "auto-loop-resume": {labels.TRIAGE_RESUME_REQUESTED},
            "auto-loop-stuck": {labels.STUCK},
            "needs-human-review": {labels.HUMAN_MAINTAINER_DECISION},
            "👤 human:需-maintainer-决策": {labels.HUMAN_MAINTAINER_DECISION},
            "wontfix-no-framing": {labels.NO_FRAMING},
            "🎯 milestone": {labels.MILESTONE_CURRENT},
            "🔍 phase:design-solving": {labels.PHASE_DESIGN_SOLVING},
            "✅ phase:consensus-reached": {labels.PHASE_CONSENSUS_REACHED},
            "🛠️ phase:implementing": {labels.PHASE_IMPLEMENTING},
            "🚀 phase:pr-open": {labels.PHASE_PR_OPEN},
            "👀 phase:reviewing": {labels.PHASE_REVIEWING},
            "🔧 phase:fixing": {labels.PHASE_FIXING},
            "⚙️ phase:ci-running": {labels.PHASE_CI_RUNNING},
            "🎉 phase:merged": {labels.PHASE_MERGED},
            "⏸️ phase:blocked": {labels.PHASE_BLOCKED},
            "🤖 human:auto-推进": {labels.HUMAN_AUTO},
        }
        for legacy, expected in cases.items():
            with self.subTest(legacy=legacy):
                self.assert_alias(legacy, expected)

    def test_cleanup_only_aliases_do_not_create_routing_semantics(self) -> None:
        projection = labels.normalize_label_set(["🆘 human:卡死", "🆘 human:卡死-需-rework"])

        self.assertEqual(projection.canonical, frozenset())
        self.assertEqual(projection.cleanup_only, frozenset({"🆘 human:卡死", "🆘 human:卡死-需-rework"}))

    def test_migration_plan_adds_canonical_before_alias_removal(self) -> None:
        plan = labels.migration_plan(["auto-loop", "🔍 phase:design-solving", "🆘 human:卡死"])

        by_live = {step.live_label: step for step in plan.alias_migrations}
        self.assertEqual(by_live["auto-loop"].add_labels, (labels.MANAGED,))
        self.assertEqual(by_live["auto-loop"].remove_labels, ("auto-loop",))
        self.assertEqual(
            by_live["auto-loop"].order,
            ("add-canonical", "reread-live-labels", "validate-exactly-one-phase-human", "remove-alias"),
        )
        self.assertEqual(by_live["🆘 human:卡死"].add_labels, ())
        self.assertEqual(by_live["🆘 human:卡死"].remove_labels, ("🆘 human:卡死",))


if __name__ == "__main__":
    unittest.main()
