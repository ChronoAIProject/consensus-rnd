#!/usr/bin/env python3
"""Behavior tests for canonical-only label protocol drift handling."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import labels


class LabelMigrationTests(unittest.TestCase):
    def test_historical_non_crnd_labels_do_not_normalize_to_loop_labels(self) -> None:
        residue = [
            "auto-loop",
            "phase9-auto-solve",
            "refactor-design-needed",
            "auto-loop-triage",
            "needs-human-review",
        ]

        projection = labels.normalize_label_set(residue)

        self.assertEqual(projection.canonical, frozenset())
        self.assertEqual(projection.unknown_crnd, frozenset())

    def test_unknown_crnd_labels_still_fail_closed(self) -> None:
        projection = labels.normalize_label_set(["crnd:phase:not-registered", "auto-loop"])

        self.assertEqual(projection.canonical, frozenset())
        self.assertEqual(projection.unknown_crnd, frozenset({"crnd:phase:not-registered"}))

    def test_github_plan_does_not_emit_alias_migration_surface(self) -> None:
        plan = labels.migration_plan(["auto-loop", "phase9-auto-solve", labels.MANAGED])

        payload = plan.as_dict()
        self.assertEqual(set(payload), {"create", "update", "unknown_crnd", "external_defaults"})
        self.assertEqual(plan.unknown_crnd, ())


if __name__ == "__main__":
    unittest.main()
