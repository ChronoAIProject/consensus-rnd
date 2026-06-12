"""Shared review-gate evidence selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


class ReviewEvidenceLike(Protocol):
    role: str
    round_number: int
    head_sha: str
    valid: bool
    pending: bool
    terminal_failed: bool
    reason: str


@dataclass(frozen=True)
class SelectedReviewEvidence:
    by_role: dict[str, ReviewEvidenceLike]
    invalid: list[str]
    pending: list[str]
    terminal_failed_roles: list[str]
    complete_round: int | None


def select_latest_live_head_review_evidence(
    evidences: Sequence[ReviewEvidenceLike],
    *,
    live_head_sha: str,
    required_roles: Sequence[str],
) -> SelectedReviewEvidence:
    selected: dict[str, ReviewEvidenceLike] = {}
    invalid: list[str] = []
    pending: list[str] = []
    terminal_failed_roles: list[str] = []
    max_round: int | None = None

    for role in required_roles:
        role_items = [evidence for evidence in evidences if evidence.role == role]
        live_items = [evidence for evidence in role_items if evidence.head_sha and evidence.head_sha == live_head_sha]
        selectable = [
            evidence
            for evidence in live_items
            if evidence.valid and not evidence.pending and not evidence.terminal_failed
        ]
        if selectable:
            highest_round = max(evidence.round_number for evidence in selectable)
            latest = [evidence for evidence in selectable if evidence.round_number == highest_round]
            if len(latest) > 1:
                invalid.append(f"duplicate_reviewer_evidence:{role}")
                continue
            evidence = latest[0]
            selected[role] = evidence
            max_round = evidence.round_number if max_round is None else max(max_round, evidence.round_number)
            continue

        if live_items:
            highest_live_round = max(evidence.round_number for evidence in live_items)
            latest_live = [evidence for evidence in live_items if evidence.round_number == highest_live_round]
            evidence = latest_live[0]
            if evidence.pending:
                pending.append(evidence.reason or f"pending:{role}")
            elif evidence.terminal_failed:
                terminal_failed_roles.append(role)
                invalid.append(evidence.reason or f"terminal_failed:{role}")
            elif not evidence.valid:
                invalid.append(evidence.reason or f"invalid:{role}")
            continue

        missing_head_valid = [
            evidence
            for evidence in role_items
            if evidence.valid and not evidence.pending and not evidence.terminal_failed and not evidence.head_sha
        ]
        if missing_head_valid:
            invalid.append(f"missing_reviewed_head_sha:{role}")
            continue

        invalid_items = [
            evidence
            for evidence in role_items
            if not evidence.valid and not evidence.pending and not evidence.terminal_failed
        ]
        if invalid_items:
            evidence = max(invalid_items, key=lambda item: item.round_number)
            invalid.append(evidence.reason or f"invalid:{role}")
            continue

        stale_head_valid = [
            evidence
            for evidence in role_items
            if evidence.valid and not evidence.pending and not evidence.terminal_failed and evidence.head_sha
        ]
        if stale_head_valid:
            invalid.append(f"stale_reviewed_head_sha:{role}")

    complete_round = max_round if all(role in selected for role in required_roles) else None
    return SelectedReviewEvidence(
        by_role=selected,
        invalid=invalid,
        pending=pending,
        terminal_failed_roles=terminal_failed_roles,
        complete_round=complete_round,
    )
