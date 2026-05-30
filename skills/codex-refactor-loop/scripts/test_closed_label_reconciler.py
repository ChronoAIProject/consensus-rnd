#!/usr/bin/env python3
"""Behavior tests for the #238 closed-label-reconciler."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import labels
from codex_refactor_loop.closed_label_reconciler import ClosedLabelReconciler
from codex_refactor_loop.closed_phase_labels import labels_after_plan, plan_closed_phase_labels
from codex_refactor_loop.context import LoopContext


class ClosedPhaseProjectionTests(unittest.TestCase):
    def test_merged_pr_projects_phase_merged_and_removes_inflight_cleanup(self) -> None:
        plan = plan_closed_phase_labels(
            kind="pr",
            number=10,
            state="MERGED",
            labels=[labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO, labels.STUCK, "🆘 human:卡死"],
            merged=True,
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(labels.PHASE_MERGED, plan.terminal_phase)
        self.assertEqual((labels.PHASE_MERGED,), plan.add_labels)
        self.assertIn(labels.PHASE_REVIEWING, plan.remove_labels)
        self.assertIn(labels.STUCK, plan.remove_labels)
        self.assertIn("🆘 human:卡死", plan.remove_labels)

    def test_closed_unmerged_pr_projects_phase_closed(self) -> None:
        plan = plan_closed_phase_labels(
            kind="pr",
            number=11,
            state="CLOSED",
            labels=[labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO],
            merged=False,
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(labels.PHASE_CLOSED, plan.terminal_phase)
        self.assertEqual((labels.PHASE_CLOSED,), plan.add_labels)
        self.assertEqual((labels.PHASE_REVIEWING,), plan.remove_labels)

    def test_closed_not_planned_issue_projects_phase_closed(self) -> None:
        plan = plan_closed_phase_labels(
            kind="issue",
            number=12,
            state="CLOSED",
            labels=[labels.MANAGED, labels.NO_FRAMING, labels.PHASE_DESIGN_SOLVING, labels.HUMAN_AUTO],
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(labels.PHASE_CLOSED, plan.terminal_phase)
        self.assertEqual((labels.PHASE_CLOSED,), plan.add_labels)

    def test_completed_issue_with_merged_evidence_projects_phase_merged(self) -> None:
        plan = plan_closed_phase_labels(
            kind="issue",
            number=13,
            state="CLOSED",
            labels=[labels.MANAGED, labels.PHASE_CLOSED, labels.HUMAN_AUTO],
            linked_merged=True,
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(labels.PHASE_MERGED, plan.terminal_phase)
        self.assertEqual((labels.PHASE_MERGED,), plan.add_labels)
        self.assertEqual((labels.PHASE_CLOSED,), plan.remove_labels)

    def test_idempotent_terminal_phase_has_no_edit_plan(self) -> None:
        plan = plan_closed_phase_labels(
            kind="issue",
            number=14,
            state="CLOSED",
            labels=[labels.MANAGED, labels.PHASE_CLOSED, labels.HUMAN_AUTO],
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertFalse(plan.needs_edit)
        self.assertEqual(tuple(sorted([labels.MANAGED, labels.PHASE_CLOSED, labels.HUMAN_AUTO])), labels_after_plan([labels.MANAGED, labels.PHASE_CLOSED, labels.HUMAN_AUTO], plan))

    def test_open_and_unmanaged_items_are_noop(self) -> None:
        self.assertIsNone(
            plan_closed_phase_labels(
                kind="issue",
                number=15,
                state="OPEN",
                labels=[labels.MANAGED, labels.PHASE_FIXING, labels.HUMAN_AUTO],
            )
        )
        self.assertIsNone(
            plan_closed_phase_labels(
                kind="issue",
                number=16,
                state="CLOSED",
                labels=[labels.PHASE_FIXING, labels.HUMAN_AUTO],
            )
        )


class ClosedLabelReconcilerBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="closed-label-reconciler-test-"))
        self.repo = self.tmp_root / "repo"
        self.repo.mkdir()
        (self.repo / ".refactor-loop").mkdir()
        (self.repo / ".refactor-loop" / "host.env").write_text(
            f'export REPO_ROOT="{self.repo}"\nexport GH_REPO_SLUG="owner/repo"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.repo, skill_root=SCRIPT_DIR.parent)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_non_owner_never_runs_gh_edit(self) -> None:
        decision = mock.Mock(allowed=False, owner_device="other", status="not-owner", action="closed-label-reconciler", lease_id="", expires_at="")
        reconciler = ClosedLabelReconciler(self.ctx)
        with mock.patch("codex_refactor_loop.closed_label_reconciler.require_active_controller", return_value=decision):
            with mock.patch.object(reconciler, "_gh") as gh:
                self.assertEqual(0, reconciler.run_once())

        gh.assert_not_called()
        status = json.loads((self.repo / ".refactor-loop" / "state" / "active-controller-status.json").read_text(encoding="utf-8"))
        self.assertEqual("noop:not-owner", status["active_controller"])

    def test_post_edit_rejects_multiple_phase_labels(self) -> None:
        plan = plan_closed_phase_labels(
            kind="issue",
            number=17,
            state="CLOSED",
            labels=[labels.MANAGED, labels.PHASE_FIXING, labels.HUMAN_AUTO],
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        reconciler = ClosedLabelReconciler(self.ctx)
        with mock.patch.object(
            reconciler,
            "_view_item",
            return_value={
                "number": 17,
                "state": "CLOSED",
                "labels": [
                    {"name": labels.MANAGED},
                    {"name": labels.PHASE_FIXING},
                    {"name": labels.PHASE_CLOSED},
                    {"name": labels.HUMAN_AUTO},
                ],
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "post-edit label invariant failed"):
                reconciler.verify_plan(plan)


if __name__ == "__main__":
    unittest.main()
