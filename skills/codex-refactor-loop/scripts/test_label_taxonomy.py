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
        self.assertEqual(labels.phase_expected_workers(labels.PHASE_FIXING), 1)
        self.assertEqual(labels.actor_for_phase(labels.PHASE_REVIEWING), "reviewer-codex")

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


if __name__ == "__main__":
    unittest.main()
