#!/usr/bin/env python3
"""Behavior tests for bounded review evidence recovery projection."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.review_evidence_recovery import (  # noqa: E402
    ReviewEvidenceRecoveryInput,
    ReviewEvidenceRecoveryLedgerRow,
    RepeatedReviewBlockerInput,
    ledger_row_from_mapping,
    project_review_evidence_recovery,
    project_repeated_review_blocker,
    repeated_review_blocker_key,
    review_recovery_attempt_key,
)
from codex_refactor_loop.review_gate_selection import ParsedGithubReviewEvidence  # noqa: E402


REQUIRED_ROLES = ("architect", "tests", "quality")
LIVE_HEAD = "a" * 40


def evidence(role: str, *, head_sha: str = LIVE_HEAD, valid: bool = True, reason: str = "") -> ParsedGithubReviewEvidence:
    return ParsedGithubReviewEvidence(
        role=role,
        round_number=1,
        verdict="approve" if role != "quality" else "comment",
        head_sha=head_sha,
        source="github:issues/comments",
        valid=valid,
        reason=reason,
        created_at=f"2026-06-12T00:0{REQUIRED_ROLES.index(role)}:00Z",
        comment_id=100 + REQUIRED_ROLES.index(role),
    )


def verdict_evidence(role: str, verdict: str, *, round_number: int, head_sha: str = LIVE_HEAD) -> ParsedGithubReviewEvidence:
    return ParsedGithubReviewEvidence(
        role=role,
        round_number=round_number,
        verdict=verdict,
        head_sha=head_sha,
        source="github:issues/comments",
        valid=True,
        created_at=f"2026-06-12T00:{round_number:02d}:00Z",
        comment_id=1000 + round_number + REQUIRED_ROLES.index(role),
    )


def terminal_failed_evidence(role: str, *, reason: str = "") -> ParsedGithubReviewEvidence:
    return ParsedGithubReviewEvidence(
        role=role,
        round_number=1,
        verdict="",
        head_sha=LIVE_HEAD,
        source="github:issues/comments",
        valid=False,
        reason=reason or f"terminal_failed:{role}",
        terminal_failed=True,
        created_at=f"2026-06-12T00:1{REQUIRED_ROLES.index(role)}:00Z",
        comment_id=200 + REQUIRED_ROLES.index(role),
    )


def recovery_input(**overrides: object) -> ReviewEvidenceRecoveryInput:
    values = {
        "pr_number": 480,
        "head_sha": LIVE_HEAD,
        "mergeable": "MERGEABLE",
        "merge_state_status": "CLEAN",
        "required_roles": REQUIRED_ROLES,
        "github_review_evidences": (),
        "pending_roles": (),
        "ledger_rows": (),
    }
    values.update(overrides)
    return ReviewEvidenceRecoveryInput(**values)


class ReviewEvidenceRecoveryTests(unittest.TestCase):
    def test_conflicting_or_dirty_pr_is_status_only_without_roles(self) -> None:
        for mergeable, merge_state in (("CONFLICTING", "CLEAN"), ("MERGEABLE", "DIRTY")):
            with self.subTest(mergeable=mergeable, merge_state=merge_state):
                projection = project_review_evidence_recovery(
                    recovery_input(mergeable=mergeable, merge_state_status=merge_state)
                )

                self.assertTrue(projection.status_only)
                self.assertEqual(projection.status_reason, "blocked:needs-conflict-resolution")
                self.assertEqual(projection.roles, ())
                self.assertEqual(projection.attempt_keys, ())

    def test_completed_authority_comes_only_from_github_visible_same_head_evidence(self) -> None:
        projection = project_review_evidence_recovery(
            recovery_input(github_review_evidences=(evidence("architect"), evidence("tests")))
        )

        self.assertFalse(projection.status_only)
        self.assertEqual(projection.roles, ("quality",))
        self.assertEqual(projection.reason_by_role, {"quality": "missing_github_review_evidence"})

    def test_pending_same_head_roles_are_not_redispatched(self) -> None:
        projection = project_review_evidence_recovery(
            recovery_input(
                github_review_evidences=(evidence("architect"),),
                pending_roles=("tests", "quality"),
            )
        )

        self.assertEqual(projection.roles, ())
        self.assertFalse(projection.status_only)
        self.assertEqual(projection.status_reason, "")

    def test_cap_counts_current_keys_and_legacy_applied_head_rows_once(self) -> None:
        key = review_recovery_attempt_key(480, "quality", LIVE_HEAD, "missing_github_review_evidence")
        projection = project_review_evidence_recovery(
            recovery_input(
                github_review_evidences=(evidence("architect"), evidence("tests")),
                ledger_rows=(
                    ReviewEvidenceRecoveryLedgerRow(
                        action_id="review-evidence-redispatch:480:" + LIVE_HEAD,
                        status="applied",
                        reason="",
                        kind="review-evidence-redispatch",
                    ),
                    ReviewEvidenceRecoveryLedgerRow(
                        action_id="review-evidence-redispatch:480:" + LIVE_HEAD,
                        status="applied",
                        reason="",
                        kind="review-evidence-redispatch",
                        attempt_keys=(key,),
                    ),
                    ReviewEvidenceRecoveryLedgerRow(
                        action_id="review-evidence-redispatch:480:" + LIVE_HEAD,
                        status="blocked",
                        reason="duplicate",
                        kind="review-evidence-redispatch",
                        attempt_keys=(key,),
                    ),
                ),
            )
        )

        self.assertEqual(projection.roles, ())
        self.assertTrue(projection.status_only)
        self.assertEqual(projection.status_reason, "capped:review-evidence-recovery")
        self.assertEqual(projection.capped_roles, ("quality",))
        self.assertEqual(projection.attempt_count_by_key[key], 2)

    def test_head_or_reason_change_resets_only_its_own_attempt_key(self) -> None:
        stale_key = review_recovery_attempt_key(480, "quality", "b" * 40, "missing_github_review_evidence")
        projection = project_review_evidence_recovery(
            recovery_input(
                github_review_evidences=(evidence("architect"), evidence("tests")),
                ledger_rows=(
                    ReviewEvidenceRecoveryLedgerRow(
                        action_id="review-evidence-redispatch:480:" + ("b" * 40),
                        status="applied",
                        reason="",
                        kind="review-evidence-redispatch",
                        attempt_keys=(stale_key,),
                    ),
                ),
            )
        )

        self.assertEqual(projection.roles, ("quality",))
        self.assertEqual(tuple(projection.attempt_count_by_key.values()), (0,))

    def test_stale_github_visible_evidence_uses_stale_attempt_key(self) -> None:
        projection = project_review_evidence_recovery(
            recovery_input(
                github_review_evidences=(
                    evidence("architect"),
                    evidence("tests"),
                    evidence("quality", head_sha="b" * 40),
                )
            )
        )

        expected_key = review_recovery_attempt_key(480, "quality", LIVE_HEAD, "stale_github_review_evidence")
        self.assertEqual(projection.roles, ("quality",))
        self.assertEqual(projection.reason_by_role, {"quality": "stale_github_review_evidence"})
        self.assertEqual(projection.attempt_keys, (expected_key,))

    def test_terminal_failed_github_visible_evidence_uses_terminal_failed_attempt_key(self) -> None:
        projection = project_review_evidence_recovery(
            recovery_input(
                github_review_evidences=(
                    evidence("architect"),
                    evidence("quality"),
                    terminal_failed_evidence("tests"),
                )
            )
        )

        expected_key = review_recovery_attempt_key(480, "tests", LIVE_HEAD, "terminal_failed_github_review_evidence")
        self.assertEqual(projection.roles, ("tests",))
        self.assertEqual(projection.reason_by_role, {"tests": "terminal_failed_github_review_evidence"})
        self.assertEqual(projection.attempt_keys, (expected_key,))

    def test_ledger_row_from_mapping_parses_attempt_keys_and_ignores_malformed_keys(self) -> None:
        parsed = ledger_row_from_mapping(
            {
                "action_id": "review-evidence-redispatch:480:" + LIVE_HEAD,
                "status": "applied",
                "reason": "done",
                "kind": "review-evidence-redispatch",
                "review_recovery_attempt_keys": [
                    review_recovery_attempt_key(480, "quality", LIVE_HEAD, "missing_github_review_evidence"),
                    7,
                ],
            }
        )
        malformed = ledger_row_from_mapping(
            {
                "action_id": "review-evidence-redispatch:480:" + LIVE_HEAD,
                "status": "applied",
                "review_recovery_attempt_keys": "not-a-list",
            }
        )

        self.assertEqual(parsed.action_id, "review-evidence-redispatch:480:" + LIVE_HEAD)
        self.assertEqual(parsed.status, "applied")
        self.assertEqual(parsed.reason, "done")
        self.assertEqual(parsed.kind, "review-evidence-redispatch")
        self.assertEqual(
            parsed.attempt_keys,
            (
                review_recovery_attempt_key(480, "quality", LIVE_HEAD, "missing_github_review_evidence"),
                "7",
            ),
        )
        self.assertEqual(malformed.attempt_keys, ())

    def test_repeated_same_head_reject_blocker_is_status_only(self) -> None:
        key = repeated_review_blocker_key(480, LIVE_HEAD, ("architect:reject", "quality:comment", "tests:approve"))

        projection = project_repeated_review_blocker(
            RepeatedReviewBlockerInput(
                pr_number=480,
                head_sha=LIVE_HEAD,
                required_roles=REQUIRED_ROLES,
                github_review_evidences=(
                    verdict_evidence("architect", "reject", round_number=3),
                    verdict_evidence("tests", "approve", round_number=3),
                    verdict_evidence("quality", "comment", round_number=3),
                    verdict_evidence("architect", "reject", round_number=4),
                    verdict_evidence("tests", "approve", round_number=4),
                    verdict_evidence("quality", "comment", round_number=4),
                ),
            )
        )

        self.assertTrue(projection.status_only)
        self.assertEqual(projection.status_reason, "repeated_review_blocker")
        self.assertEqual(projection.blocker_key, key)
        self.assertEqual(projection.signature, ("architect:reject", "quality:comment", "tests:approve"))
        self.assertEqual(projection.rounds, (3, 4))

    def test_repeated_all_comment_no_approval_is_human_blocked_without_autofix(self) -> None:
        projection = project_repeated_review_blocker(
            RepeatedReviewBlockerInput(
                pr_number=480,
                head_sha=LIVE_HEAD,
                required_roles=REQUIRED_ROLES,
                github_review_evidences=(
                    verdict_evidence("architect", "comment", round_number=7),
                    verdict_evidence("tests", "comment", round_number=7),
                    verdict_evidence("quality", "comment", round_number=7),
                    verdict_evidence("architect", "comment", round_number=8),
                    verdict_evidence("tests", "comment", round_number=8),
                    verdict_evidence("quality", "comment", round_number=8),
                ),
            )
        )

        self.assertTrue(projection.status_only)
        self.assertEqual(projection.status_reason, "explicit_approval_required")
        self.assertEqual(projection.blocker_key, f"480:{LIVE_HEAD}:architect:comment|quality:comment|tests:comment")
        self.assertEqual(projection.rounds, (7, 8))

    def test_old_head_repeated_blockers_do_not_block_fresh_head(self) -> None:
        projection = project_repeated_review_blocker(
            RepeatedReviewBlockerInput(
                pr_number=480,
                head_sha=LIVE_HEAD,
                required_roles=REQUIRED_ROLES,
                github_review_evidences=(
                    verdict_evidence("architect", "reject", round_number=7, head_sha="b" * 40),
                    verdict_evidence("tests", "approve", round_number=7, head_sha="b" * 40),
                    verdict_evidence("quality", "comment", round_number=7, head_sha="b" * 40),
                    verdict_evidence("architect", "reject", round_number=8, head_sha="b" * 40),
                    verdict_evidence("tests", "approve", round_number=8, head_sha="b" * 40),
                    verdict_evidence("quality", "comment", round_number=8, head_sha="b" * 40),
                ),
            )
        )

        self.assertFalse(projection.status_only)
        self.assertEqual(projection.status_reason, "")


if __name__ == "__main__":
    unittest.main()
