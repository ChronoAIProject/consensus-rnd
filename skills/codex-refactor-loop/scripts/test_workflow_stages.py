#!/usr/bin/env python3
"""Behavior tests for the closed workflow stage registry."""

from __future__ import annotations

import re
import sys
import unittest
import ast
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.workflow_stages import (  # noqa: E402
    WORKFLOW_STAGES,
    assert_stage_slug,
    format_stage,
    stage_by_slug,
)
from codex_refactor_loop.wakeup_plan import phase_from_labels, phase_from_marker  # noqa: E402


EXPECTED_SLUGS = (
    "bootstrap",
    "work-intake",
    "implementation",
    "verification",
    "publish",
    "ci-watch",
    "integration-sync",
    "design-intake",
    "review-gate",
    "design-consensus",
)


class WorkflowStageRegistryTests(unittest.TestCase):
    def test_stage_slugs_are_closed_unique_non_numeric(self) -> None:
        slugs = tuple(stage.slug for stage in WORKFLOW_STAGES)

        self.assertEqual(slugs, EXPECTED_SLUGS)
        self.assertEqual(len(slugs), len(set(slugs)))
        for stage in WORKFLOW_STAGES:
            with self.subTest(slug=stage.slug):
                self.assertRegex(stage.slug, r"^[a-z]+(?:-[a-z]+)*$")
                self.assertNotRegex(stage.slug, r"\d")
                self.assertEqual(format_stage(stage), f"Consensus-rnd Phase {stage.slug}")
                self.assertEqual(format_stage(stage.slug), f"Consensus-rnd Phase {stage.slug}")
                self.assertEqual(stage_by_slug(stage.slug), stage)

    def test_stage_by_slug_fails_closed(self) -> None:
        numeric_display = "Phase " + "9"
        for bad_slug in ("", numeric_display, "design", "stage-9", "issue-182"):
            with self.subTest(bad_slug=bad_slug):
                with self.assertRaises(ValueError):
                    stage_by_slug(bad_slug)
                with self.assertRaises(ValueError):
                    assert_stage_slug(bad_slug)

    def test_legacy_numbers_are_private_migration_metadata(self) -> None:
        legacy_numbers = [stage.legacy_number for stage in WORKFLOW_STAGES]

        self.assertEqual(sorted(legacy_numbers), list(range(10)))
        for stage in WORKFLOW_STAGES:
            with self.subTest(slug=stage.slug):
                self.assertNotRegex(format_stage(stage), re.compile(r"\bPhase\s+[0-9]\b"))
                self.assertNotIn(str(stage.legacy_number), format_stage(stage))

    def test_label_and_wakeup_mappings_use_registered_stage_slugs(self) -> None:
        mapped = (
            phase_from_marker("AUDIT_DONE:ok"),
            phase_from_marker("IMPLEMENT_DONE:ok"),
            phase_from_marker("VERIFY_DONE:ok"),
            phase_from_marker("REVIEW_DONE:12:architect:approve"),
            phase_from_marker("FIX_DONE:12:round-1"),
            phase_from_marker("TEST_ADD_DONE:12:ok"),
            phase_from_marker("SOLVER_DONE:minimal:propose"),
            phase_from_marker("META_JUDGE_DONE:consensus:framing"),
            phase_from_labels(("🔍 phase:design-solving",)),
            phase_from_labels(("🛠️ phase:implementing",)),
            phase_from_labels(("🔧 phase:fixing",)),
            phase_from_labels(("👀 phase:reviewing",)),
            phase_from_labels(("🚀 phase:pr-open",)),
            phase_from_labels(("✅ phase:consensus-reached",)),
            phase_from_labels(("⚙️ phase:ci-running",)),
            phase_from_labels(("⏸️ phase:blocked",)),
            phase_from_labels(("🎉 phase:merged",)),
            phase_from_labels(("auto-loop",)),
        )
        for slug in mapped:
            with self.subTest(slug=slug):
                assert_stage_slug(slug)

    def test_wakeup_plan_phase_literals_are_registered_stage_slugs(self) -> None:
        path = SCRIPT_DIR / "codex_refactor_loop" / "wakeup_plan.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        phase_values: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value == "phase"
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                    ):
                        phase_values.add(value.value)
            if isinstance(node, ast.FunctionDef) and node.name.startswith("phase_from_"):
                for child in ast.walk(node):
                    if (
                        isinstance(child, ast.Return)
                        and isinstance(child.value, ast.Constant)
                        and isinstance(child.value.value, str)
                    ):
                        phase_values.add(child.value.value)

        self.assertTrue(phase_values)
        for slug in sorted(phase_values):
            with self.subTest(slug=slug):
                assert_stage_slug(slug)

    def test_wakeup_plan_non_stage_routes_do_not_use_phase_field(self) -> None:
        path = SCRIPT_DIR / "codex_refactor_loop" / "wakeup_plan.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        phase_return_values: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("phase_from_"):
                for child in ast.walk(node):
                    if (
                        isinstance(child, ast.Return)
                        and isinstance(child.value, ast.Constant)
                        and isinstance(child.value.value, str)
                    ):
                        phase_return_values.add(child.value.value)

        for non_stage in (
            "publish-or-review-gate",
            "marker-route",
            "daemon-health",
            "wake-source",
            "no-gap-repair",
            "ci-running",
            "blocked",
            "merged",
            "unlabeled-existing-issue",
        ):
            with self.subTest(non_stage=non_stage):
                self.assertNotRegex(source, rf'"phase":\s*"{re.escape(non_stage)}"')
                self.assertNotIn(non_stage, phase_return_values)


if __name__ == "__main__":
    unittest.main()
