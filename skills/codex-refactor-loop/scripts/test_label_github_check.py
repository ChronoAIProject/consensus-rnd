#!/usr/bin/env python3
"""Behavior tests for read-only GitHub label drift planning."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import labels


class LabelGithubCheckTests(unittest.TestCase):
    def live_catalog(self) -> list[dict[str, str]]:
        return [
            {"name": spec.name, "description": spec.description, "color": spec.color}
            for spec in labels.LABEL_SPECS
        ]

    def test_clean_catalog_has_no_create_update_or_unknown_crnd(self) -> None:
        plan = labels.migration_plan(self.live_catalog() + [{"name": "enhancement"}, {"name": "wontfix"}])

        self.assertEqual(plan.create, ())
        self.assertEqual(plan.update, ())
        self.assertEqual(plan.unknown_crnd, ())
        self.assertEqual(plan.external_defaults, ("enhancement", "wontfix"))

    def test_missing_canonical_label_is_planned_for_create(self) -> None:
        live = [item for item in self.live_catalog() if item["name"] != labels.PHASE_FIXING]

        plan = labels.migration_plan(live)

        self.assertIn(labels.PHASE_FIXING, [spec.name for spec in plan.create])

    def test_wrong_description_or_color_is_planned_for_update(self) -> None:
        live = self.live_catalog()
        live[0] = {**live[0], "description": "wrong", "color": "ffffff"}

        plan = labels.migration_plan(live)

        self.assertEqual([spec.name for spec in plan.update], [labels.LABEL_SPECS[0].name])

    def test_unknown_crnd_fails_closed(self) -> None:
        plan = labels.migration_plan(self.live_catalog() + [{"name": "crnd:phase:not-registered"}])

        self.assertEqual(plan.unknown_crnd, ("crnd:phase:not-registered",))

    def test_legacy_aliases_are_reported_as_migrations(self) -> None:
        plan = labels.migration_plan(self.live_catalog() + [{"name": "auto-loop"}, {"name": "phase9-auto-solve"}])

        migrations = {step.live_label: step.add_labels for step in plan.alias_migrations}
        self.assertEqual(migrations["auto-loop"], (labels.MANAGED,))
        self.assertEqual(
            set(migrations["phase9-auto-solve"]),
            {labels.MANAGED, labels.PHASE_DESIGN_SOLVING, labels.HUMAN_AUTO},
        )


if __name__ == "__main__":
    unittest.main()
