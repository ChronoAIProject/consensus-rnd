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
from codex_refactor_loop.closed_phase_labels import (
    ClosedPhaseLabelPlan,
    has_exactly_one_terminal_phase,
    labels_after_plan,
    plan_closed_phase_labels,
)
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

    def test_legacy_phase_alias_removal_uses_live_alias_not_missing_canonical(self) -> None:
        plan = plan_closed_phase_labels(
            kind="issue",
            number=20,
            state="CLOSED",
            labels=["auto-loop", "🛠️ phase:implementing", "🤖 human:auto-推进"],
            linked_merged=True,
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(labels.PHASE_MERGED, plan.terminal_phase)
        self.assertEqual((labels.PHASE_MERGED,), plan.add_labels)
        self.assertEqual(("🛠️ phase:implementing",), plan.remove_labels)
        self.assertNotIn(labels.PHASE_IMPLEMENTING, plan.remove_labels)

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

    def test_existing_merged_phase_is_preserved_as_terminal_evidence(self) -> None:
        plan = plan_closed_phase_labels(
            kind="issue",
            number=18,
            state="CLOSED",
            labels=[labels.MANAGED, labels.PHASE_MERGED, labels.HUMAN_AUTO],
            merged=False,
            linked_merged=False,
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(labels.PHASE_MERGED, plan.terminal_phase)
        self.assertFalse(plan.needs_edit)
        self.assertEqual("existing-merged-evidence", plan.reason)

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

    def test_has_exactly_one_terminal_phase_helper_is_phase_only(self) -> None:
        self.assertTrue(
            has_exactly_one_terminal_phase(
                [labels.MANAGED, labels.PHASE_CLOSED],
                labels.PHASE_CLOSED,
            )
        )
        self.assertFalse(
            has_exactly_one_terminal_phase(
                [labels.MANAGED, labels.PHASE_CLOSED, labels.PHASE_MERGED],
                labels.PHASE_CLOSED,
            )
        )
        self.assertFalse(
            has_exactly_one_terminal_phase(
                [labels.MANAGED, labels.PHASE_MERGED],
                labels.PHASE_CLOSED,
            )
        )

    def test_closed_phase_plan_preserves_canonical_human_labels(self) -> None:
        source_labels = [
            labels.MANAGED,
            labels.PHASE_REVIEWING,
            labels.HUMAN_AUTO,
            labels.HUMAN_MAINTAINER_DECISION,
            "🆘 human:卡死",
        ]
        plan = plan_closed_phase_labels(
            kind="issue",
            number=19,
            state="CLOSED",
            labels=source_labels,
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        after = labels_after_plan(source_labels, plan)
        self.assertIn(labels.HUMAN_AUTO, after)
        self.assertIn(labels.HUMAN_MAINTAINER_DECISION, after)
        self.assertNotIn("🆘 human:卡死", after)


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
            ("label", "list", "--json", "name", "--limit", "1000"): [{"name": name} for name in labels.canonical_labels()],
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
        view_counts: dict[tuple[str, ...], int] = {}

        def fake_gh(args: tuple[str, ...] | list[str], *, check: bool = True) -> CompletedProcess[str]:
            command = tuple(args)
            if command == ("issue", "view", "21", "--json", "number,state,labels"):
                count = view_counts.get(command, 0)
                view_counts[command] = count + 1
                item = issue_row if count == 0 else gh_json_responses[command]
                return CompletedProcess(["gh", *command], 0, json.dumps(item), "")
            if command == ("pr", "view", "22", "--json", "number,state,labels,mergedAt"):
                count = view_counts.get(command, 0)
                view_counts[command] = count + 1
                item = pr_row if count == 0 else gh_json_responses[command]
                return CompletedProcess(["gh", *command], 0, json.dumps(item), "")
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

    def test_closed_managed_item_without_human_label_warns_skips_and_continues(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="closed-label-reconciler", lease_id="lease", expires_at="")
        missing_human_row = {
            "number": 31,
            "state": "CLOSED",
            "labels": [
                {"name": labels.MANAGED},
                {"name": labels.PHASE_REVIEWING},
                {"name": labels.STUCK},
            ],
            "title": "old closed issue without human label",
        }
        normal_row = {
            "number": 32,
            "state": "CLOSED",
            "labels": [
                {"name": labels.MANAGED},
                {"name": labels.PHASE_REVIEWING},
                {"name": labels.HUMAN_AUTO},
            ],
            "title": "normal closed issue",
        }
        gh_json_responses = {
            ("label", "list", "--json", "name", "--limit", "1000"): [{"name": name} for name in labels.canonical_labels()],
            ("issue", "list", "--label", labels.MANAGED, "--state", "closed", "--limit", "100", "--json", "number,state,labels,title"): [missing_human_row, normal_row],
            ("issue", "list", "--label", "auto-loop", "--state", "closed", "--limit", "100", "--json", "number,state,labels,title"): [missing_human_row, normal_row],
            ("pr", "list", "--label", labels.MANAGED, "--state", "closed", "--limit", "100", "--json", "number,state,labels,title,mergedAt"): [],
            ("pr", "list", "--label", "auto-loop", "--state", "closed", "--limit", "100", "--json", "number,state,labels,title,mergedAt"): [],
            ("pr", "list", "--label", labels.MANAGED, "--state", "merged", "--limit", "100", "--json", "number,state,labels,title,mergedAt"): [],
            ("pr", "list", "--label", "auto-loop", "--state", "merged", "--limit", "100", "--json", "number,state,labels,title,mergedAt"): [],
            ("pr", "list", "--state", "merged", "--search", "in:body Closes #32", "--limit", "1", "--json", "number,mergedAt"): [],
            ("issue", "view", "32", "--json", "number,state,labels"): {
                "number": 32,
                "state": "CLOSED",
                "labels": [
                    {"name": labels.MANAGED},
                    {"name": labels.PHASE_CLOSED},
                    {"name": labels.HUMAN_AUTO},
                ],
            },
        }
        edit_commands: list[tuple[str, ...]] = []
        view_counts: dict[tuple[str, ...], int] = {}

        def fake_gh(args: tuple[str, ...] | list[str], *, check: bool = True) -> CompletedProcess[str]:
            command = tuple(args)
            if command == ("issue", "view", "32", "--json", "number,state,labels"):
                count = view_counts.get(command, 0)
                view_counts[command] = count + 1
                item = normal_row if count == 0 else gh_json_responses[command]
                return CompletedProcess(["gh", *command], 0, json.dumps(item), "")
            if command in gh_json_responses:
                return CompletedProcess(["gh", *command], 0, json.dumps(gh_json_responses[command]), "")
            if len(command) >= 2 and command[1] == "list":
                return CompletedProcess(["gh", *command], 0, "[]", "")
            edit_commands.append(command)
            return CompletedProcess(["gh", *command], 0, "", "")

        reconciler = ClosedLabelReconciler(self.ctx)
        with mock.patch("codex_refactor_loop.closed_label_reconciler.require_active_controller", return_value=decision):
            with mock.patch.object(reconciler, "_gh", side_effect=fake_gh):
                with mock.patch("builtins.print") as print_mock:
                    self.assertEqual(0, reconciler.run_once())

        print_mock.assert_any_call(
            "closed-label-reconciler warn: issue #31 "
            "expected exactly one canonical human label, got 0; skipping phase reconciliation"
        )
        self.assertEqual(
            [
                (
                    "issue",
                    "edit",
                    "32",
                    "--add-label",
                    labels.PHASE_CLOSED,
                    "--remove-label",
                    labels.PHASE_REVIEWING,
                )
            ],
            edit_commands,
        )
        self.assertNotIn(("issue", "view", "31", "--json", "number,state,labels"), view_counts)

    def test_legacy_emoji_label_host_writes_existing_alias_without_canonical_retry_noise(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="closed-label-reconciler", lease_id="lease", expires_at="")
        issue_row = {
            "number": 33,
            "state": "CLOSED",
            "labels": [
                {"name": "auto-loop"},
                {"name": "🛠️ phase:implementing"},
                {"name": "🤖 human:auto-推进"},
            ],
            "title": "legacy closed issue",
        }
        after_row = {
            "number": 33,
            "state": "CLOSED",
            "labels": [
                {"name": "auto-loop"},
                {"name": "🎉 phase:merged"},
                {"name": "🤖 human:auto-推进"},
            ],
        }
        gh_json_responses = {
            ("label", "list", "--json", "name", "--limit", "1000"): [
                {"name": "auto-loop"},
                {"name": "🛠️ phase:implementing"},
                {"name": "🎉 phase:merged"},
                {"name": "🤖 human:auto-推进"},
            ],
            ("issue", "list", "--label", labels.MANAGED, "--state", "closed", "--limit", "100", "--json", "number,state,labels,title"): [],
            ("issue", "list", "--label", "auto-loop", "--state", "closed", "--limit", "100", "--json", "number,state,labels,title"): [issue_row],
            ("pr", "list", "--label", labels.MANAGED, "--state", "closed", "--limit", "100", "--json", "number,state,labels,title,mergedAt"): [],
            ("pr", "list", "--label", "auto-loop", "--state", "closed", "--limit", "100", "--json", "number,state,labels,title,mergedAt"): [],
            ("pr", "list", "--label", labels.MANAGED, "--state", "merged", "--limit", "100", "--json", "number,state,labels,title,mergedAt"): [],
            ("pr", "list", "--label", "auto-loop", "--state", "merged", "--limit", "100", "--json", "number,state,labels,title,mergedAt"): [],
            ("pr", "list", "--state", "merged", "--search", "in:body Closes #33", "--limit", "1", "--json", "number,mergedAt"): [{"number": 70, "mergedAt": "2026-05-31T00:00:00Z"}],
            ("issue", "view", "33", "--json", "number,state,labels"): after_row,
        }
        edit_commands: list[tuple[str, ...]] = []
        view_counts: dict[tuple[str, ...], int] = {}

        def fake_gh(args: tuple[str, ...] | list[str], *, check: bool = True) -> CompletedProcess[str]:
            command = tuple(args)
            if command == ("issue", "view", "33", "--json", "number,state,labels"):
                count = view_counts.get(command, 0)
                view_counts[command] = count + 1
                item = issue_row if count == 0 else after_row
                return CompletedProcess(["gh", *command], 0, json.dumps(item), "")
            if command in gh_json_responses:
                return CompletedProcess(["gh", *command], 0, json.dumps(gh_json_responses[command]), "")
            if len(command) >= 2 and command[1] == "list":
                return CompletedProcess(["gh", *command], 0, "[]", "")
            edit_commands.append(command)
            if any(label.startswith("crnd:") for label in command):
                return CompletedProcess(["gh", *command], 1, "", "'crnd:phase:implementing' not found")
            return CompletedProcess(["gh", *command], 0, "", "")

        reconciler = ClosedLabelReconciler(self.ctx)
        with mock.patch("codex_refactor_loop.closed_label_reconciler.require_active_controller", return_value=decision):
            with mock.patch.object(reconciler, "_gh", side_effect=fake_gh):
                self.assertEqual(0, reconciler.run_once())
                self.assertEqual(0, reconciler.run_once())

        self.assertEqual(
            [
                (
                    "issue",
                    "edit",
                    "33",
                    "--add-label",
                    "🎉 phase:merged",
                    "--remove-label",
                    "🛠️ phase:implementing",
                )
            ],
            edit_commands,
        )

    def test_unresolvable_terminal_label_warns_skips_and_does_not_retry_failed_edit(self) -> None:
        plan = ClosedPhaseLabelPlan(
            kind="issue",
            number=34,
            terminal_phase=labels.PHASE_CLOSED,
            add_labels=(labels.PHASE_CLOSED,),
            remove_labels=("🛠️ phase:implementing",),
            reason="closed-no-merged-evidence",
        )
        gh_json_responses = {
            ("label", "list", "--json", "name", "--limit", "1000"): [
                {"name": "auto-loop"},
                {"name": "🛠️ phase:implementing"},
                {"name": "🤖 human:auto-推进"},
            ],
            ("issue", "view", "34", "--json", "number,state,labels"): {
                "number": 34,
                "state": "CLOSED",
                "labels": [
                    {"name": "auto-loop"},
                    {"name": "🛠️ phase:implementing"},
                    {"name": "🤖 human:auto-推进"},
                ],
            },
            ("pr", "list", "--state", "merged", "--search", "in:body Closes #34", "--limit", "1", "--json", "number,mergedAt"): [],
        }
        edit_commands: list[tuple[str, ...]] = []

        def fake_gh(args: tuple[str, ...] | list[str], *, check: bool = True) -> CompletedProcess[str]:
            command = tuple(args)
            if command in gh_json_responses:
                return CompletedProcess(["gh", *command], 0, json.dumps(gh_json_responses[command]), "")
            edit_commands.append(command)
            return CompletedProcess(["gh", *command], 1, "", "'crnd:phase:closed' not found")

        reconciler = ClosedLabelReconciler(self.ctx)
        with mock.patch.object(reconciler, "_gh", side_effect=fake_gh):
            with mock.patch("builtins.print") as print_mock:
                self.assertIsNone(reconciler.apply_plan(plan))
                self.assertIsNone(reconciler.apply_plan(plan))

        warning = (
            "closed-label-reconciler warn: issue #34 "
            f"cannot resolve host label for terminal phase {labels.PHASE_CLOSED}; skipping phase reconciliation"
        )
        print_mock.assert_any_call(warning)
        self.assertEqual(1, [call.args[0] for call in print_mock.call_args_list].count(warning))
        self.assertEqual([], edit_commands)

    def test_apply_rechecks_live_closed_state_and_skips_stale_open_item(self) -> None:
        plan = ClosedPhaseLabelPlan(
            kind="issue",
            number=24,
            terminal_phase=labels.PHASE_CLOSED,
            add_labels=(labels.PHASE_CLOSED,),
            remove_labels=(labels.PHASE_REVIEWING,),
            reason="closed-no-merged-evidence",
        )
        gh_json_responses = {
            ("issue", "view", "24", "--json", "number,state,labels"): {
                "number": 24,
                "state": "OPEN",
                "labels": [
                    {"name": labels.MANAGED},
                    {"name": labels.PHASE_REVIEWING},
                    {"name": labels.HUMAN_AUTO},
                ],
            },
            ("pr", "list", "--state", "merged", "--search", "in:body Closes #24", "--limit", "1", "--json", "number,mergedAt"): [],
        }
        edit_commands: list[tuple[str, ...]] = []

        def fake_gh(args: tuple[str, ...] | list[str], *, check: bool = True) -> CompletedProcess[str]:
            command = tuple(args)
            if command in gh_json_responses:
                return CompletedProcess(["gh", *command], 0, json.dumps(gh_json_responses[command]), "")
            edit_commands.append(command)
            return CompletedProcess(["gh", *command], 0, "", "")

        reconciler = ClosedLabelReconciler(self.ctx)
        with mock.patch.object(reconciler, "_gh", side_effect=fake_gh):
            self.assertIsNone(reconciler.apply_plan(plan))

        self.assertEqual([], edit_commands)

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

    def test_verify_plan_is_phase_only_and_does_not_require_human_label(self) -> None:
        plan = ClosedPhaseLabelPlan(
            kind="issue",
            number=25,
            terminal_phase=labels.PHASE_CLOSED,
            add_labels=(labels.PHASE_CLOSED,),
            remove_labels=(labels.PHASE_REVIEWING,),
            reason="closed-no-merged-evidence",
        )
        reconciler = ClosedLabelReconciler(self.ctx)
        with mock.patch.object(
            reconciler,
            "_view_item",
            return_value={
                "number": 25,
                "state": "CLOSED",
                "labels": [
                    {"name": labels.MANAGED},
                    {"name": labels.PHASE_CLOSED},
                ],
            },
        ):
            reconciler.verify_plan(plan)

    def test_run_once_catches_single_item_failure_and_continues(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="closed-label-reconciler", lease_id="lease", expires_at="")
        failing = ClosedPhaseLabelPlan(
            kind="issue",
            number=26,
            terminal_phase=labels.PHASE_CLOSED,
            add_labels=(labels.PHASE_CLOSED,),
            remove_labels=(labels.PHASE_REVIEWING,),
            reason="closed-no-merged-evidence",
        )
        continuing = ClosedPhaseLabelPlan(
            kind="issue",
            number=27,
            terminal_phase=labels.PHASE_CLOSED,
            add_labels=(labels.PHASE_CLOSED,),
            remove_labels=(labels.PHASE_FIXING,),
            reason="closed-no-merged-evidence",
        )
        reconciler = ClosedLabelReconciler(self.ctx)

        def apply_plan(plan: ClosedPhaseLabelPlan) -> ClosedPhaseLabelPlan:
            if plan.number == 26:
                raise RuntimeError("single item failed")
            return plan

        with mock.patch("codex_refactor_loop.closed_label_reconciler.require_active_controller", return_value=decision):
            with mock.patch.object(reconciler, "collect_plans", return_value=(failing, continuing)):
                with mock.patch.object(reconciler, "apply_plan", side_effect=apply_plan) as apply_mock:
                    with mock.patch.object(reconciler, "verify_plan") as verify_mock:
                        self.assertEqual(0, reconciler.run_once())

        self.assertEqual([mock.call(failing), mock.call(continuing)], apply_mock.mock_calls)
        verify_mock.assert_called_once_with(continuing)

    def test_daemon_run_once_renews_heartbeat_during_tick(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="closed-label-reconciler", lease_id="lease", expires_at="")
        plans = (
            ClosedPhaseLabelPlan(
                kind="issue",
                number=28,
                terminal_phase=labels.PHASE_CLOSED,
                add_labels=(),
                remove_labels=(),
                reason="closed-no-merged-evidence",
            ),
            ClosedPhaseLabelPlan(
                kind="issue",
                number=29,
                terminal_phase=labels.PHASE_CLOSED,
                add_labels=(labels.PHASE_CLOSED,),
                remove_labels=(labels.PHASE_FIXING,),
                reason="closed-no-merged-evidence",
            ),
        )
        beats: list[str] = []
        reconciler = ClosedLabelReconciler(self.ctx, dry_run=True)

        with mock.patch("codex_refactor_loop.closed_label_reconciler.require_active_controller", return_value=decision):
            with mock.patch.object(reconciler, "collect_plans", return_value=plans):
                self.assertEqual(0, reconciler.run_once(beat=lambda: beats.append("beat")))

        self.assertEqual(["beat", "beat", "beat"], beats)


if __name__ == "__main__":
    unittest.main()
