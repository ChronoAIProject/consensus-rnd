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
    ManagedWorkProjection,
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
        self.assertIn("Closes\\s+#", source)


if __name__ == "__main__":
    unittest.main()
