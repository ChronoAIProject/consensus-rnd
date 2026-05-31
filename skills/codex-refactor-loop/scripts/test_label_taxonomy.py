#!/usr/bin/env python3
"""Behavior tests for the loop-owned GitHub label catalog."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import labels


class LabelTaxonomyTests(unittest.TestCase):
    def test_catalog_uses_closed_crnd_grammar(self) -> None:
        self.assertEqual(labels.CANONICAL_PREFIX, "crnd")
        self.assertEqual(labels.LABEL_GROUPS, ("phase", "human", "lifecycle", "triage", "milestone"))
        self.assertEqual(len(labels.canonical_labels()), len(set(labels.canonical_labels())))
        for name in labels.canonical_labels():
            with self.subTest(name=name):
                name.encode("ascii")
                self.assertRegex(name, r"^crnd:(phase|human|lifecycle|triage|milestone):[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")

    def test_unknown_crnd_fails_closed_and_external_defaults_are_non_routing(self) -> None:
        projection = labels.normalize_label_set(["crnd:phase:typo", "enhancement", "bug"])

        self.assertEqual(projection.unknown_crnd, frozenset({"crnd:phase:typo"}))
        self.assertEqual(projection.canonical, frozenset())
        self.assertTrue(labels.is_allowed_external("enhancement"))
        self.assertEqual(labels.phase_expected_workers("enhancement"), 0)
        self.assertIsNone(labels.actor_for_phase("wontfix"))

    def test_group_helpers_and_expected_axes_are_catalog_backed(self) -> None:
        self.assertEqual(labels.canonical_name("phase", "design-solving"), labels.PHASE_DESIGN_SOLVING)
        self.assertIn(labels.HUMAN_AUTO, labels.labels_for_group("human"))
        self.assertIn(labels.PHASE_CLOSED, labels.labels_for_group("phase"))
        self.assertEqual(labels.MILESTONE_RELEASE_TARGET, "crnd:milestone:release-target")
        self.assertIn(labels.MILESTONE_RELEASE_TARGET, labels.labels_for_group("milestone"))
        labels.PHASE_CLOSED.encode("ascii")
        self.assertEqual(labels.phase_expected_workers(labels.PHASE_FIXING), 1)
        self.assertEqual(labels.actor_for_phase(labels.PHASE_REVIEWING), "reviewer-codex")
        self.assertEqual(labels.actor_for_phase(labels.PHASE_CLOSED), "closed-label-reconciler")

    def test_release_target_uses_existing_milestone_grammar_without_groupless_exception(self) -> None:
        self.assertEqual(labels.LABEL_GROUPS, ("phase", "human", "lifecycle", "triage", "milestone"))
        self.assertRegex(labels.MILESTONE_RELEASE_TARGET, labels.CANONICAL_RE)
        self.assertIsNone(re.fullmatch(labels.CANONICAL_RE, "crnd:release-target"))

        projection = labels.normalize_label_set([labels.MILESTONE_CURRENT, labels.MILESTONE_RELEASE_TARGET])

        self.assertEqual(
            projection.canonical,
            frozenset({labels.MILESTONE_CURRENT, labels.MILESTONE_RELEASE_TARGET}),
        )

    def test_managed_query_labels_include_canonical_and_legacy_aliases(self) -> None:
        self.assertEqual(
            labels.query_labels_for(labels.MANAGED),
            (labels.MANAGED, "auto-loop", "phase9-auto-solve", "refactor-design-needed"),
        )

    def test_exactly_one_phase_and_human_invariant(self) -> None:
        ok, errors = labels.validate_exactly_one_phase_human(
            [labels.MANAGED, labels.PHASE_DESIGN_SOLVING, labels.HUMAN_AUTO]
        )
        self.assertTrue(ok, errors)

        ok, errors = labels.validate_exactly_one_phase_human(
            [labels.PHASE_DESIGN_SOLVING, labels.PHASE_FIXING, labels.HUMAN_AUTO]
        )
        self.assertFalse(ok)
        self.assertTrue(any("phase" in error for error in errors))

    def test_catalog_validator_rejects_bad_slug(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid label slug"):
            labels.canonical_name("phase", "BadSlug")
        self.assertTrue(re.fullmatch(labels.SLUG_RE, "resume-requested"))

    def test_closed_phase_is_canonical_terminal_phase_exclusive_protocol_label(self) -> None:
        self.assertEqual(labels.PHASE_CLOSED, "crnd:phase:closed")
        specs = [spec for spec in labels.LABEL_SPECS if spec.name == labels.PHASE_CLOSED]
        self.assertEqual(1, len(specs))
        self.assertEqual("phase", specs[0].exclusive_axis)
        self.assertIn("terminal protocol", specs[0].description)


if __name__ == "__main__":
    unittest.main()
