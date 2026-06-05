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
    RECENT_CLOSED_MANAGED_WINDOW_LIMIT,
    closed_reconcile_candidate_queries,
    closed_reconcile_candidate_query_labels,
    has_exactly_one_terminal_phase,
    item_matches_closed_reconcile_query,
    labels_after_plan,
    plan_closed_reconcile_candidate,
    plan_closed_phase_labels,
)
from codex_refactor_loop.context import LoopContext


class ClosedPhaseProjectionTests(unittest.TestCase):
    def test_merged_pr_projects_phase_merged_and_removes_canonical_inflight_labels(self) -> None:
        plan = plan_closed_phase_labels(
            kind="pr",
            number=10,
            state="MERGED",
            labels=[labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO, labels.STUCK, "historical-residue"],
            merged=True,
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(labels.PHASE_MERGED, plan.terminal_phase)
        self.assertEqual((labels.PHASE_MERGED,), plan.add_labels)
        self.assertIn(labels.PHASE_REVIEWING, plan.remove_labels)
        self.assertIn(labels.STUCK, plan.remove_labels)
        self.assertNotIn("historical-residue", plan.remove_labels)

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

    def test_historical_residue_without_canonical_managed_label_is_unmanaged(self) -> None:
        plan = plan_closed_phase_labels(
            kind="issue",
            number=20,
            state="CLOSED",
            labels=["auto-loop", "🛠️ phase:implementing", "🤖 human:auto-推进"],
            linked_merged=True,
        )

        self.assertIsNone(plan)

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

    def test_closed_reconcile_candidate_projection_filters_terminal_complete_history(self) -> None:
        terminal_complete = {
            "number": 41,
            "state": "CLOSED",
            "labels": [
                {"name": labels.MANAGED},
                {"name": labels.PHASE_CLOSED},
                {"name": labels.HUMAN_AUTO},
            ],
        }
        dirty = {
            "number": 42,
            "state": "CLOSED",
            "labels": [
                {"name": labels.MANAGED},
                {"name": labels.PHASE_CLOSED},
                {"name": labels.STUCK},
                {"name": labels.HUMAN_AUTO},
            ],
        }

        self.assertIsNone(plan_closed_reconcile_candidate("issue", terminal_complete))
        self.assertIsNotNone(plan_closed_reconcile_candidate("issue", dirty))
        self.assertIn(labels.PHASE_REVIEWING, closed_reconcile_candidate_query_labels())
        self.assertIn(labels.STUCK, closed_reconcile_candidate_query_labels())
        self.assertNotIn(labels.PHASE_CLOSED, closed_reconcile_candidate_query_labels())
        self.assertNotIn(labels.PHASE_MERGED, closed_reconcile_candidate_query_labels())
        self.assertEqual("20", RECENT_CLOSED_MANAGED_WINDOW_LIMIT)

    def test_closed_reconcile_candidate_queries_prove_managed_membership(self) -> None:
        queries = closed_reconcile_candidate_queries("issue", "closed")
        dirty_queries = [query for query in queries if query.dirty_label is not None]
        recent_queries = [query for query in queries if query.dirty_label is None]
        managed_labels = set(labels.query_labels_for(labels.MANAGED))

        self.assertTrue(dirty_queries)
        self.assertEqual(len(managed_labels), len(recent_queries))
        self.assertTrue(all(query.managed_label in managed_labels for query in queries))
        self.assertTrue(all(query.limit == "100" for query in dirty_queries))
        self.assertTrue(all(query.limit == RECENT_CLOSED_MANAGED_WINDOW_LIMIT for query in recent_queries))
        self.assertTrue(all("--label" in query.gh_args("number,state,labels") for query in dirty_queries))
        self.assertTrue(all("--search" in query.gh_args("number,state,labels") for query in dirty_queries))
        self.assertTrue(all(query.dirty_label != labels.MANAGED for query in dirty_queries))

    def test_candidate_match_filters_search_noise_and_recent_terminal_history(self) -> None:
        queries = closed_reconcile_candidate_queries("issue", "closed")
        reviewing_query = next(query for query in queries if query.dirty_label == labels.PHASE_REVIEWING)
        recent_query = next(query for query in queries if query.dirty_label is None)
        missing_terminal = {
            "number": 50,
            "state": "CLOSED",
            "labels": [
                {"name": labels.MANAGED},
                {"name": labels.HUMAN_AUTO},
            ],
        }
        terminal_complete = {
            "number": 51,
            "state": "CLOSED",
            "labels": [
                {"name": labels.MANAGED},
                {"name": labels.PHASE_CLOSED},
                {"name": labels.HUMAN_AUTO},
            ],
        }
        search_noise = {
            "number": 52,
            "state": "CLOSED",
            "labels": [
                {"name": labels.MANAGED},
                {"name": labels.PHASE_CLOSED},
                {"name": labels.HUMAN_AUTO},
            ],
        }
        unmanaged_dirty = {
            "number": 53,
            "state": "CLOSED",
            "labels": [
                {"name": labels.PHASE_REVIEWING},
                {"name": labels.HUMAN_AUTO},
            ],
        }

        self.assertTrue(item_matches_closed_reconcile_query("issue", missing_terminal, recent_query))
        self.assertFalse(item_matches_closed_reconcile_query("issue", terminal_complete, recent_query))
        self.assertFalse(item_matches_closed_reconcile_query("issue", search_noise, reviewing_query))
        self.assertFalse(item_matches_closed_reconcile_query("issue", unmanaged_dirty, reviewing_query))
        self.assertIsNone(plan_closed_reconcile_candidate("issue", unmanaged_dirty))

    def test_closed_phase_plan_preserves_canonical_human_labels(self) -> None:
        source_labels = [
            labels.MANAGED,
            labels.PHASE_REVIEWING,
            labels.HUMAN_AUTO,
            labels.HUMAN_MAINTAINER_DECISION,
            "historical-residue",
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
        self.assertIn("historical-residue", after)


class ClosedLabelReconcilerBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="closed-label-reconciler-test-"))
        self.repo = self.tmp_root / "repo"
        self.repo.mkdir()
        (self.repo / ".refactor-loop").mkdir()
        (self.repo / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.repo / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.repo}"\nexport GH_REPO_SLUG="owner/repo"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.repo, skill_root=SCRIPT_DIR.parent, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})

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
        (self.repo / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.repo / ".config" / "consensus-rnd" / "host.env").write_text(
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

    def test_collect_plans_filters_recent_managed_terminal_complete_before_view_or_link_probe(self) -> None:
        dirty_row = {
            "number": 35,
            "state": "CLOSED",
            "labels": [
                {"name": labels.MANAGED},
                {"name": labels.PHASE_REVIEWING},
                {"name": labels.HUMAN_AUTO},
            ],
            "title": "dirty closed issue",
        }
        terminal_complete = {
            "number": 36,
            "state": "CLOSED",
            "labels": [
                {"name": labels.MANAGED},
                {"name": labels.PHASE_CLOSED},
                {"name": labels.HUMAN_AUTO},
            ],
            "title": "terminal complete closed issue",
        }
        calls: list[tuple[str, ...]] = []

        def fake_gh(args: tuple[str, ...] | list[str], *, check: bool = True) -> CompletedProcess[str]:
            command = tuple(args)
            calls.append(command)
            if command == (
                "issue",
                "list",
                "--label",
                labels.MANAGED,
                "--state",
                "closed",
                "--limit",
                RECENT_CLOSED_MANAGED_WINDOW_LIMIT,
                "--json",
                "number,state,labels,title",
            ):
                return CompletedProcess(["gh", *command], 0, json.dumps([dirty_row, terminal_complete]), "")
            if command == (
                "pr",
                "list",
                "--state",
                "merged",
                "--search",
                "in:body Closes #35",
                "--limit",
                "1",
                "--json",
                "number,mergedAt",
            ):
                return CompletedProcess(["gh", *command], 0, "[]", "")
            if len(command) >= 2 and command[1] == "list":
                return CompletedProcess(["gh", *command], 0, "[]", "")
            return CompletedProcess(["gh", *command], 0, "{}", "")

        reconciler = ClosedLabelReconciler(self.ctx)
        with mock.patch.object(reconciler, "_gh", side_effect=fake_gh):
            plans = reconciler.collect_plans()

        self.assertEqual((35,), tuple(plan.number for plan in plans))
        self.assertIn(
            (
                "pr",
                "list",
                "--state",
                "merged",
                "--search",
                "in:body Closes #35",
                "--limit",
                "1",
                "--json",
                "number,mergedAt",
            ),
            calls,
        )
        self.assertNotIn(
            (
                "pr",
                "list",
                "--state",
                "merged",
                "--search",
                "in:body Closes #36",
                "--limit",
                "1",
                "--json",
                "number,mergedAt",
            ),
            calls,
        )
        self.assertFalse(any(command[:3] == ("issue", "view", "36") for command in calls), calls)

    def test_collect_plans_never_returns_unmanaged_closed_dirty_search_noise(self) -> None:
        unmanaged_dirty = {
            "number": 37,
            "state": "CLOSED",
            "labels": [
                {"name": labels.PHASE_REVIEWING},
                {"name": labels.HUMAN_AUTO},
            ],
            "title": "unmanaged closed issue",
        }
        managed_dirty = {
            "number": 38,
            "state": "CLOSED",
            "labels": [
                {"name": labels.MANAGED},
                {"name": labels.PHASE_REVIEWING},
                {"name": labels.HUMAN_AUTO},
            ],
            "title": "managed closed issue",
        }
        calls: list[tuple[str, ...]] = []

        def fake_gh(args: tuple[str, ...] | list[str], *, check: bool = True) -> CompletedProcess[str]:
            command = tuple(args)
            calls.append(command)
            if command[:2] == ("issue", "list"):
                self.assertIn("--label", command)
                self.assertIn(command[command.index("--label") + 1], labels.query_labels_for(labels.MANAGED))
                if "--search" in command and labels.PHASE_REVIEWING in " ".join(command):
                    return CompletedProcess(["gh", *command], 0, json.dumps([unmanaged_dirty, managed_dirty]), "")
                return CompletedProcess(["gh", *command], 0, "[]", "")
            if command == (
                "pr",
                "list",
                "--state",
                "merged",
                "--search",
                "in:body Closes #38",
                "--limit",
                "1",
                "--json",
                "number,mergedAt",
            ):
                return CompletedProcess(["gh", *command], 0, "[]", "")
            if len(command) >= 2 and command[1] == "list":
                return CompletedProcess(["gh", *command], 0, "[]", "")
            return CompletedProcess(["gh", *command], 0, "{}", "")

        reconciler = ClosedLabelReconciler(self.ctx)
        with mock.patch.object(reconciler, "_gh", side_effect=fake_gh):
            plans = reconciler.collect_plans()

        self.assertEqual((38,), tuple(plan.number for plan in plans))
        self.assertFalse(any(command[:3] == ("issue", "view", "37") for command in calls), calls)
        self.assertTrue(
            any(
                command[:2] == ("issue", "list")
                and "--label" in command
                and command[command.index("--label") + 1] in labels.query_labels_for(labels.MANAGED)
                and "--search" in command
                for command in calls
            ),
            calls,
        )

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
                {"name": "historical-residue"},
                {"name": labels.HUMAN_AUTO},
            ],
            "title": "closed pr",
        }
        gh_json_responses = {
            ("label", "list", "--json", "name", "--limit", "1000"): [{"name": name} for name in labels.canonical_labels()],
            ("issue", "list", "--label", labels.PHASE_REVIEWING, "--state", "closed", "--limit", "100", "--json", "number,state,labels,title"): [issue_row],
            ("pr", "list", "--label", labels.PHASE_FIXING, "--state", "closed", "--limit", "100", "--json", "number,state,labels,title,mergedAt"): [pr_row],
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
            if command[:2] == ("issue", "list"):
                self.assertIn("--label", command)
                self.assertIn(command[command.index("--label") + 1], labels.query_labels_for(labels.MANAGED))
                if "--search" in command and labels.STUCK in " ".join(command):
                    return CompletedProcess(["gh", *command], 0, json.dumps([issue_row]), "")
                return CompletedProcess(["gh", *command], 0, "[]", "")
            if command[:2] == ("pr", "list") and "--label" in command:
                self.assertIn("--label", command)
                self.assertIn(command[command.index("--label") + 1], labels.query_labels_for(labels.MANAGED))
                if "--search" in command and labels.PHASE_FIXING in " ".join(command):
                    return CompletedProcess(["gh", *command], 0, json.dumps([pr_row]), "")
                return CompletedProcess(["gh", *command], 0, "[]", "")
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
                ),
            ],
            edit_commands,
        )
        status = json.loads((self.repo / ".refactor-loop" / "state" / "active-controller-status.json").read_text(encoding="utf-8"))
        self.assertEqual("owner", status["active_controller"])

    def test_closed_managed_item_without_human_label_reconciles_phase_and_preserves_human_labels(self) -> None:
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
            ("pr", "list", "--state", "merged", "--search", "in:body Closes #32", "--limit", "1", "--json", "number,mergedAt"): [],
            ("pr", "list", "--state", "merged", "--search", "in:body Closes #31", "--limit", "1", "--json", "number,mergedAt"): [],
            ("issue", "view", "31", "--json", "number,state,labels"): {
                "number": 31,
                "state": "CLOSED",
                "labels": [
                    {"name": labels.MANAGED},
                    {"name": labels.PHASE_CLOSED},
                ],
            },
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
            if command[:2] == ("issue", "list"):
                self.assertIn("--label", command)
                self.assertIn(command[command.index("--label") + 1], labels.query_labels_for(labels.MANAGED))
                if "--search" in command and labels.PHASE_REVIEWING in " ".join(command):
                    return CompletedProcess(["gh", *command], 0, json.dumps([missing_human_row, normal_row]), "")
                if "--search" in command and labels.STUCK in " ".join(command):
                    return CompletedProcess(["gh", *command], 0, json.dumps([missing_human_row]), "")
                return CompletedProcess(["gh", *command], 0, "[]", "")
            if command[:2] == ("pr", "list") and "--label" in command:
                self.assertIn("--label", command)
                self.assertIn(command[command.index("--label") + 1], labels.query_labels_for(labels.MANAGED))
                return CompletedProcess(["gh", *command], 0, "[]", "")
            if command == ("issue", "view", "31", "--json", "number,state,labels"):
                count = view_counts.get(command, 0)
                view_counts[command] = count + 1
                item = missing_human_row if count == 0 else gh_json_responses[command]
                return CompletedProcess(["gh", *command], 0, json.dumps(item), "")
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

        printed = "\n".join(str(call.args[0]) for call in print_mock.call_args_list if call.args)
        self.assertNotIn("expected exactly one canonical human label", printed)
        self.assertEqual(
            [
                (
                    "issue",
                    "edit",
                    "31",
                    "--add-label",
                    labels.PHASE_CLOSED,
                    "--remove-label",
                    labels.STUCK,
                    "--remove-label",
                    labels.PHASE_REVIEWING,
                ),
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
        for command in edit_commands:
            self.assertFalse(any(str(part).startswith("crnd:human:") for part in command), command)

    def test_historical_residue_labels_are_not_reconciler_candidates(self) -> None:
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
        edit_commands: list[tuple[str, ...]] = []
        calls: list[tuple[str, ...]] = []

        def fake_gh(args: tuple[str, ...] | list[str], *, check: bool = True) -> CompletedProcess[str]:
            command = tuple(args)
            calls.append(command)
            if command[:2] == ("issue", "list"):
                self.assertIn("--label", command)
                self.assertEqual(command[command.index("--label") + 1], labels.MANAGED)
                return CompletedProcess(["gh", *command], 0, "[]", "")
            if len(command) >= 2 and command[1] == "list":
                return CompletedProcess(["gh", *command], 0, "[]", "")
            edit_commands.append(command)
            return CompletedProcess(["gh", *command], 0, "", "")

        reconciler = ClosedLabelReconciler(self.ctx)
        with mock.patch("codex_refactor_loop.closed_label_reconciler.require_active_controller", return_value=decision):
            with mock.patch.object(reconciler, "_gh", side_effect=fake_gh):
                self.assertEqual(0, reconciler.run_once())

        self.assertEqual([], edit_commands)
        self.assertFalse(any("auto-loop" in command for command in calls))
        self.assertFalse(any("🛠️ phase:implementing" in command for command in calls))

    def test_unresolvable_terminal_label_warns_skips_and_does_not_retry_failed_edit(self) -> None:
        plan = ClosedPhaseLabelPlan(
            kind="issue",
            number=34,
            terminal_phase=labels.PHASE_CLOSED,
            add_labels=(labels.PHASE_CLOSED,),
            remove_labels=(labels.PHASE_IMPLEMENTING,),
            reason="closed-no-merged-evidence",
        )
        gh_json_responses = {
            ("label", "list", "--json", "name", "--limit", "1000"): [
                {"name": labels.MANAGED},
                {"name": labels.PHASE_IMPLEMENTING},
                {"name": labels.HUMAN_AUTO},
            ],
            ("issue", "view", "34", "--json", "number,state,labels"): {
                "number": 34,
                "state": "CLOSED",
                "labels": [
                    {"name": labels.MANAGED},
                    {"name": labels.PHASE_IMPLEMENTING},
                    {"name": labels.HUMAN_AUTO},
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
