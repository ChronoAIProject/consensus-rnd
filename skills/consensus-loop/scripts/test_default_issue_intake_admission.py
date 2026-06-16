#!/usr/bin/env python3
"""Behavior tests for default issue intake read-only admission."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import labels  # noqa: E402
from codex_refactor_loop.context import LoopContext  # noqa: E402
from codex_refactor_loop.daemon_progress import begin_tick  # noqa: E402
from codex_refactor_loop.default_issue_intake_admission import (  # noqa: E402
    DefaultIssueIntakeAdmission,
    DefaultIssueIntakeCandidate,
    UPSTREAM_PROGRESS_DAEMONS,
    issue_title_is_concrete_work_unit,
)


class DefaultIssueIntakeAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        for rel in (".config/consensus-rnd", ".refactor-loop/state", ".refactor-loop/logs"):
            (self.repo / rel).mkdir(parents=True, exist_ok=True)
        (self.repo / ".config/consensus-rnd/host.env").write_text(
            f"REPO_ROOT={self.repo}\n"
            "GH_REPO_SLUG=owner/repo\n"
            "DEFAULT_ISSUE_INTAKE_ENABLE=true\n"
            "DEFAULT_ISSUE_INTAKE_ACTIVE_DESIGN_CAP=2\n"
            "DEFAULT_ISSUE_INTAKE_CLAIM_COOLDOWN_SECONDS=3600\n",
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(
            repo_root=self.repo,
            env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"},
            cwd=self.repo,
            read_only=True,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_accepts_open_issue_when_all_read_only_backpressure_facts_pass(self) -> None:
        admission = DefaultIssueIntakeAdmission(
            self.ctx,
            managed_items=[
                self.managed_issue(11, labels.PHASE_PR_OPEN),
                self.managed_issue(12, labels.PHASE_MERGED),
            ],
            pending_spawn_intents=[],
        )

        decision = admission.evaluate(
            DefaultIssueIntakeCandidate(
                number=77,
                title="Fix release gate stuck after stale rollup",
                labels=(),
                updated_at="2026-06-07T00:00:00Z",
                is_pr=False,
                state="open",
            )
        )

        self.assertTrue(decision.accepted)
        self.assertEqual("", decision.reason)
        self.assertIn("default_issue_intake_admission_active_cap", decision.preconditions)
        self.assertEqual(0, decision.proof["active_design_solving"])
        self.assertEqual(2, decision.proof["active_design_cap"])
        self.assertEqual("upstream_idle", decision.proof["upstream_state"])

    def test_rejects_when_active_design_solving_reaches_cap(self) -> None:
        admission = DefaultIssueIntakeAdmission(
            self.ctx,
            managed_items=[
                self.managed_issue(11, labels.PHASE_DESIGN_SOLVING),
                self.managed_issue(12, labels.PHASE_DESIGN_SOLVING),
            ],
            pending_spawn_intents=[],
        )

        decision = admission.evaluate(self.candidate())

        self.assertFalse(decision.accepted)
        self.assertEqual("active_design_cap_reached", decision.reason)
        self.assertEqual(2, decision.proof["active_design_solving"])

    def test_rejects_when_recent_claim_comment_is_inside_cooldown(self) -> None:
        (self.repo / ".refactor-loop/state/default-issue-intake-claims.json").write_text(
            json.dumps({"last_claimed_at": "2026-06-07T00:30:00Z"}),
            encoding="utf-8",
        )
        admission = DefaultIssueIntakeAdmission(
            self.ctx,
            managed_items=[],
            pending_spawn_intents=[],
            now_iso="2026-06-07T01:00:00Z",
        )

        decision = admission.evaluate(self.candidate())

        self.assertFalse(decision.accepted)
        self.assertEqual("claim_cooldown_active", decision.reason)
        self.assertEqual(1800, decision.proof["claim_cooldown_remaining_seconds"])

    def test_admits_with_pending_spawn_intent_when_design_cap_has_room(self) -> None:
        admission = DefaultIssueIntakeAdmission(
            self.ctx,
            managed_items=[],
            pending_spawn_intents=[{"controller_action": "dispatch_consensus_implementation"}],
        )

        decision = admission.evaluate(self.candidate())

        self.assertTrue(decision.accepted)
        self.assertEqual("", decision.reason)
        self.assertEqual("upstream_idle", decision.proof["upstream_state"])
        self.assertEqual(1, decision.proof["pending_spawn_intents"])

    def test_ignores_stale_pending_spawn_intents_targeting_closed_managed_work(self) -> None:
        admission = DefaultIssueIntakeAdmission(
            self.ctx,
            managed_items=[],
            pending_spawn_intents=[
                {"intent_id": "historical-pr", "task_id": "review-pr123-tests-r1"},
                {"intent_id": "historical-issue", "task_id": "phase9-issue124-r1-minimal"},
            ],
        )

        decision = admission.evaluate(self.candidate())

        self.assertEqual("upstream_idle", admission._upstream_state())
        self.assertTrue(decision.accepted)
        self.assertEqual("", decision.reason)
        self.assertEqual("upstream_idle", decision.proof["upstream_state"])

    def test_admits_with_live_pending_spawn_intent_targeting_open_managed_work(self) -> None:
        admission = DefaultIssueIntakeAdmission(
            self.ctx,
            managed_items=[{"kind": "PR", "number": 123, "labels": [labels.MANAGED]}],
            pending_spawn_intents=[{"intent_id": "live-pr", "task_id": "review-pr123-tests-r1"}],
        )

        decision = admission.evaluate(self.candidate())

        self.assertEqual("upstream_idle", admission._upstream_state())
        self.assertTrue(decision.accepted)
        self.assertEqual("", decision.reason)
        self.assertEqual("upstream_idle", decision.proof["upstream_state"])

    def test_admits_with_unresolvable_pending_spawn_intent(self) -> None:
        admission = DefaultIssueIntakeAdmission(
            self.ctx,
            managed_items=[],
            pending_spawn_intents=[{"intent_id": "opaque-worker", "task_id": "custom-worker"}],
        )

        decision = admission.evaluate(self.candidate())

        self.assertEqual("upstream_idle", admission._upstream_state())
        self.assertTrue(decision.accepted)
        self.assertEqual("", decision.reason)
        self.assertEqual("upstream_idle", decision.proof["upstream_state"])

    def test_pending_spawn_intent_still_rejects_when_active_design_cap_reached(self) -> None:
        admission = DefaultIssueIntakeAdmission(
            self.ctx,
            managed_items=[
                self.managed_issue(11, labels.PHASE_DESIGN_SOLVING),
                self.managed_issue(12, labels.PHASE_DESIGN_SOLVING),
            ],
            pending_spawn_intents=[{"controller_action": "dispatch_consensus_implementation"}],
        )

        decision = admission.evaluate(self.candidate())

        self.assertFalse(decision.accepted)
        self.assertEqual("active_design_cap_reached", decision.reason)
        self.assertEqual(2, decision.proof["active_design_solving"])
        self.assertEqual(1, decision.proof["pending_spawn_intents"])

    def test_rejects_when_daemon_progress_is_in_progress(self) -> None:
        begin_tick(self.repo, "phase9_router_daemon", now=1000, pid=42)
        admission = DefaultIssueIntakeAdmission(
            self.ctx,
            managed_items=[],
            pending_spawn_intents=[],
        )

        decision = admission.evaluate(self.candidate())

        self.assertFalse(decision.accepted)
        self.assertEqual("upstream_not_idle", decision.reason)
        self.assertEqual("daemon_progress_incomplete:phase9_router_daemon", decision.proof["upstream_state"])

    def test_accepts_when_wakeup_runner_progress_is_in_progress(self) -> None:
        begin_tick(self.repo, "wakeup_runner_daemon", now=1000, pid=42)
        admission = DefaultIssueIntakeAdmission(
            self.ctx,
            managed_items=[],
            pending_spawn_intents=[],
        )

        decision = admission.evaluate(self.candidate())

        self.assertTrue(decision.accepted)
        self.assertEqual("upstream_idle", decision.proof["upstream_state"])

    def test_rejects_when_concurrency_monitor_progress_is_in_progress(self) -> None:
        begin_tick(self.repo, "concurrency_monitor", now=1000, pid=42)
        admission = DefaultIssueIntakeAdmission(
            self.ctx,
            managed_items=[],
            pending_spawn_intents=[],
        )

        decision = admission.evaluate(self.candidate())

        self.assertFalse(decision.accepted)
        self.assertEqual("upstream_not_idle", decision.reason)
        self.assertEqual("daemon_progress_incomplete:concurrency_monitor", decision.proof["upstream_state"])

    def test_upstream_progress_daemons_exclude_wakeup_runner_executor(self) -> None:
        self.assertEqual(("phase9_router_daemon", "concurrency_monitor"), UPSTREAM_PROGRESS_DAEMONS)

    def test_rejects_epic_tracking_and_container_titles_as_not_concrete_work_units(self) -> None:
        admission = DefaultIssueIntakeAdmission(self.ctx, managed_items=[], pending_spawn_intents=[])

        for title in (
            "EPIC: consolidate runtime governance",
            "Tracking issue for release cleanup",
            "Container: controller backlog",
        ):
            with self.subTest(title=title):
                decision = admission.evaluate(self.candidate(title=title))
                self.assertFalse(decision.accepted)
                self.assertEqual("not_concrete_work_unit", decision.reason)

    def test_title_classifier_accepts_specific_work_and_rejects_containers(self) -> None:
        self.assertTrue(issue_title_is_concrete_work_unit("Fix stale default intake projection"))
        self.assertFalse(issue_title_is_concrete_work_unit("EPIC: intake backlog"))

    def candidate(self, *, title: str = "Fix stale default intake projection") -> DefaultIssueIntakeCandidate:
        return DefaultIssueIntakeCandidate(
            number=77,
            title=title,
            labels=(),
            updated_at="2026-06-07T00:00:00Z",
            is_pr=False,
            state="open",
        )

    def managed_issue(self, number: int, phase: str) -> dict[str, object]:
        return {"kind": "issue", "number": number, "labels": [labels.MANAGED, phase]}


if __name__ == "__main__":
    unittest.main()
