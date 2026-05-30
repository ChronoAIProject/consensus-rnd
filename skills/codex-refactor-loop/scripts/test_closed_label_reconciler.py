#!/usr/bin/env python3
"""Behavior tests for the #238 closed-label-reconciler."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import labels
from codex_refactor_loop.closed_label_reconciler import ClosedLabelReconciler
from codex_refactor_loop.closed_phase_labels import ClosedPhaseLabelPlan, labels_after_plan, plan_closed_phase_labels
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

    def test_owner_without_repo_slug_noops_before_gh_calls(self) -> None:
        (self.repo / ".refactor-loop" / "host.env").write_text(
            f'export REPO_ROOT="{self.repo}"\n',
            encoding="utf-8",
        )
        ctx = LoopContext.load(repo_root=self.repo, skill_root=SCRIPT_DIR.parent, env={})
        self.assertIsNone(ctx.gh_repo_slug)
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="closed-label-reconciler", lease_id="lease", expires_at="")
        reconciler = ClosedLabelReconciler(ctx)

        with mock.patch("codex_refactor_loop.closed_label_reconciler.require_active_controller", return_value=decision):
            with mock.patch.object(reconciler, "_gh") as gh:
                self.assertEqual(0, reconciler.run_once())

        gh.assert_not_called()
        status = json.loads((self.repo / ".refactor-loop" / "state" / "active-controller-status.json").read_text(encoding="utf-8"))
        self.assertEqual("owner", status["active_controller"])

    def test_dry_run_prints_plan_without_apply_verify_or_gh_mutation(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="closed-label-reconciler", lease_id="lease", expires_at="")
        plan = ClosedPhaseLabelPlan(
            kind="issue",
            number=23,
            terminal_phase=labels.PHASE_CLOSED,
            add_labels=(labels.PHASE_CLOSED,),
            remove_labels=(labels.PHASE_REVIEWING,),
            reason="closed-no-merged-evidence",
        )
        reconciler = ClosedLabelReconciler(self.ctx, dry_run=True)

        with mock.patch("codex_refactor_loop.closed_label_reconciler.require_active_controller", return_value=decision):
            with mock.patch.object(reconciler, "collect_plans", return_value=(plan,)):
                with mock.patch.object(reconciler, "apply_plan") as apply_plan:
                    with mock.patch.object(reconciler, "verify_plan") as verify_plan:
                        with mock.patch.object(reconciler, "_gh") as gh:
                            with mock.patch("builtins.print") as print_mock:
                                self.assertEqual(0, reconciler.run_once())

        apply_plan.assert_not_called()
        verify_plan.assert_not_called()
        gh.assert_not_called()
        print_mock.assert_any_call(
            "closed-label-reconciler dry-run: issue #23 "
            f"terminal={labels.PHASE_CLOSED} add={labels.PHASE_CLOSED} "
            f"remove={labels.PHASE_REVIEWING} reason=closed-no-merged-evidence"
        )

    def test_owner_run_lists_closed_managed_items_edits_and_reverifies(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="closed-label-reconciler", lease_id="lease", expires_at="")
        issue_row = {
            "number": 21,
            "state": "CLOSED",
            "labels": [
                {"name": labels.MANAGED},
                {"name": labels.PHASE_REVIEWING},
                {"name": labels.STUCK},
                {"name": labels.HUMAN_AUTO},
            ],
            "title": "closed issue",
        }
        pr_row = {
            "number": 22,
            "state": "CLOSED",
            "mergedAt": None,
            "labels": [
                {"name": labels.MANAGED},
                {"name": labels.PHASE_FIXING},
                {"name": "🆘 human:卡死"},
                {"name": labels.HUMAN_AUTO},
            ],
            "title": "closed pr",
        }
        gh_json_responses = {
            ("issue", "list", "--label", labels.MANAGED, "--state", "closed", "--limit", "100", "--json", "number,state,labels,title"): [issue_row],
            ("issue", "list", "--label", "auto-loop", "--state", "closed", "--limit", "100", "--json", "number,state,labels,title"): [issue_row],
            ("pr", "list", "--label", labels.MANAGED, "--state", "closed", "--limit", "100", "--json", "number,state,labels,title,mergedAt"): [pr_row],
            ("pr", "list", "--label", "auto-loop", "--state", "closed", "--limit", "100", "--json", "number,state,labels,title,mergedAt"): [pr_row],
            ("pr", "list", "--label", labels.MANAGED, "--state", "merged", "--limit", "100", "--json", "number,state,labels,title,mergedAt"): [],
            ("pr", "list", "--label", "auto-loop", "--state", "merged", "--limit", "100", "--json", "number,state,labels,title,mergedAt"): [],
            ("pr", "list", "--state", "merged", "--search", "in:body Closes #21", "--limit", "1", "--json", "number,mergedAt"): [{"number": 30, "mergedAt": "2026-05-31T00:00:00Z"}],
            ("issue", "view", "21", "--json", "number,state,labels"): {
                "number": 21,
                "state": "CLOSED",
                "labels": [
                    {"name": labels.MANAGED},
                    {"name": labels.PHASE_MERGED},
                    {"name": labels.HUMAN_AUTO},
                ],
            },
            ("pr", "view", "22", "--json", "number,state,labels,mergedAt"): {
                "number": 22,
                "state": "CLOSED",
                "mergedAt": None,
                "labels": [
                    {"name": labels.MANAGED},
                    {"name": labels.PHASE_CLOSED},
                    {"name": labels.HUMAN_AUTO},
                ],
            },
        }
        edit_commands: list[tuple[str, ...]] = []

        def fake_gh(args: tuple[str, ...] | list[str], *, check: bool = True) -> CompletedProcess[str]:
            command = tuple(args)
            if command in gh_json_responses:
                return CompletedProcess(["gh", *command], 0, json.dumps(gh_json_responses[command]), "")
            if len(command) >= 2 and command[1] == "list":
                return CompletedProcess(["gh", *command], 0, "[]", "")
            edit_commands.append(command)
            return CompletedProcess(["gh", *command], 0, "", "")

        reconciler = ClosedLabelReconciler(self.ctx)
        with mock.patch("codex_refactor_loop.closed_label_reconciler.require_active_controller", return_value=decision):
            with mock.patch.object(reconciler, "_gh", side_effect=fake_gh):
                self.assertEqual(0, reconciler.run_once())

        self.assertEqual(
            [
                (
                    "issue",
                    "edit",
                    "21",
                    "--add-label",
                    labels.PHASE_MERGED,
                    "--remove-label",
                    labels.STUCK,
                    "--remove-label",
                    labels.PHASE_REVIEWING,
                ),
                (
                    "pr",
                    "edit",
                    "22",
                    "--add-label",
                    labels.PHASE_CLOSED,
                    "--remove-label",
                    labels.PHASE_FIXING,
                    "--remove-label",
                    "🆘 human:卡死",
                ),
            ],
            edit_commands,
        )
        status = json.loads((self.repo / ".refactor-loop" / "state" / "active-controller-status.json").read_text(encoding="utf-8"))
        self.assertEqual("owner", status["active_controller"])

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
