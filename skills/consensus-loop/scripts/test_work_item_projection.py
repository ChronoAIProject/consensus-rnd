#!/usr/bin/env python3
"""Behavior tests for managed work-item linkage projection."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import labels
from codex_refactor_loop.work_items import (
    DESIGN_CONSENSUS_TERMINAL_PHASES,
    ManagedWorkProjection,
    design_consensus_terminal_source,
    extract_closing_issue_numbers,
    linkage_mismatches,
)


class WorkItemProjectionTests(unittest.TestCase):
    def test_extract_closing_issue_numbers_dedupes_pr_body_refs(self) -> None:
        body = "Closes #239\n\nnotes\n\ncloses #240\nCloses #239\n"

        self.assertEqual(extract_closing_issue_numbers(body), (239, 240))

    def test_effective_worker_items_filters_represented_parent_issue(self) -> None:
        items = [
            {
                "kind": "issue",
                "number": 239,
                "labels": (labels.MANAGED, labels.PHASE_IMPLEMENTING, labels.HUMAN_AUTO),
            },
            {
                "kind": "pr",
                "number": 255,
                "labels": (labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO),
                "body": "## PR\n\nCloses #239\n",
            },
        ]

        projection = ManagedWorkProjection(items)

        self.assertEqual(projection.represented_issue_numbers(), frozenset({239}))
        self.assertEqual([(item.kind, item.number) for item in projection.effective_worker_items()], [("pr", 255)])

    def test_duplicate_open_pr_parent_link_keeps_issue_visible(self) -> None:
        items = [
            {
                "kind": "issue",
                "number": 239,
                "labels": (labels.MANAGED, labels.PHASE_IMPLEMENTING, labels.HUMAN_AUTO),
            },
            {
                "kind": "pr",
                "number": 255,
                "labels": (labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO),
                "body": "## PR\n\nCloses #239\n",
            },
            {
                "kind": "pr",
                "number": 256,
                "labels": (labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO),
                "body": "## PR\n\nCloses #239\n",
            },
        ]

        projection = ManagedWorkProjection(items)
        mismatches = "\n".join(linkage_mismatches(items))

        self.assertIn("issue #239 is closed by multiple open managed PRs (#255,#256)", mismatches)
        self.assertNotIn(239, projection.represented_issue_numbers())
        self.assertEqual(
            [(item.kind, item.number) for item in projection.effective_worker_items()],
            [("issue", 239), ("pr", 255), ("pr", 256)],
        )

    def test_design_consensus_terminal_source_covers_phase_labels(self) -> None:
        expected = {
            labels.PHASE_CONSENSUS_REACHED,
            labels.PHASE_IMPLEMENTING,
            labels.PHASE_PR_OPEN,
            labels.PHASE_MERGED,
            labels.PHASE_CLOSED,
        }

        self.assertEqual(DESIGN_CONSENSUS_TERMINAL_PHASES, frozenset(expected))
        for phase in expected:
            with self.subTest(phase=phase):
                self.assertEqual(
                    design_consensus_terminal_source(239, labels=(labels.MANAGED, phase, labels.HUMAN_AUTO)),
                    f"phase-label:{phase}",
                )

        self.assertIsNone(
            design_consensus_terminal_source(
                239,
                labels=(labels.MANAGED, labels.PHASE_DESIGN_SOLVING, labels.HUMAN_AUTO),
            )
        )

    def test_design_consensus_terminal_source_uses_exactly_one_open_managed_closing_pr(self) -> None:
        self.assertEqual(
            design_consensus_terminal_source(
                239,
                items=(
                    {
                        "kind": "PR",
                        "number": 255,
                        "labels": (labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO),
                        "body": "Closes #239\n",
                    },
                ),
            ),
            "open-managed-closing-pr:255",
        )

    def test_design_consensus_terminal_source_rejects_ambiguous_closing_prs(self) -> None:
        items = (
            {
                "kind": "PR",
                "number": 255,
                "labels": (labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO),
                "body": "Closes #239\n",
            },
            {
                "kind": "PR",
                "number": 256,
                "labels": (labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO),
                "body": "Closes #239\n",
            },
        )

        self.assertIsNone(design_consensus_terminal_source(239, items=items))

    def test_linkage_mismatch_reports_missing_multiple_and_merged_links(self) -> None:
        items = [
            {
                "kind": "issue",
                "number": 10,
                "labels": (labels.MANAGED, labels.PHASE_IMPLEMENTING, labels.HUMAN_AUTO),
            },
            {
                "kind": "issue",
                "number": 11,
                "labels": (labels.MANAGED, labels.PHASE_IMPLEMENTING, labels.HUMAN_AUTO),
            },
            {
                "kind": "pr",
                "number": 20,
                "labels": (labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO),
                "body": "",
            },
            {
                "kind": "pr",
                "number": 21,
                "labels": (labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO),
                "body": "Closes #11\n",
                "state": "merged",
            },
            {
                "kind": "pr",
                "number": 22,
                "labels": (labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO),
                "body": "Closes #12\nCloses #13\n",
            },
        ]

        mismatches = "\n".join(linkage_mismatches(items))

        self.assertIn("PR #20 has no `Closes #N` parent link", mismatches)
        self.assertIn("issue #10", mismatches)
        self.assertIn("PR #21 is merged but issue is still open", mismatches)
        self.assertIn("PR #22 has multiple `Closes #N` parent links (#12,#13)", mismatches)

    def test_source_contract_names_shared_projection_boundary(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "work_items.py").read_text(encoding="utf-8")

        self.assertIn("class ManagedWorkProjection", source)
        self.assertIn("def extract_closing_issue_numbers", source)
        self.assertIn("def represented_issue_numbers", source)
        self.assertIn("def effective_worker_items", source)
        self.assertIn("def linkage_mismatches", source)
        self.assertIn("def design_consensus_terminal_source", source)
        self.assertIn("Closes\\s+#", source)


if __name__ == "__main__":
    unittest.main()
