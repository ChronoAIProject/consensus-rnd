"""Bounded reviewer redispatch projection from GitHub-visible evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence

from .review_gate_selection import select_latest_live_head_review_evidence


DEFAULT_REVIEW_RECOVERY_CAP = 2
RECOVERY_KIND = "review-evidence-redispatch"
CONFLICT_BLOCK_REASON = "blocked:needs-conflict-resolution"
CAPPED_STATUS_REASON = "capped:review-evidence-recovery"
MISSING_GITHUB_EVIDENCE_REASON = "missing_github_review_evidence"


class ReviewEvidenceLike(Protocol):
    role: str
    round_number: int
    head_sha: str
    valid: bool
    pending: bool
    terminal_failed: bool
    reason: str
    created_at: str
    source_index: int
    comment_id: int | None


@dataclass(frozen=True)
class ReviewEvidenceRecoveryLedgerRow:
    action_id: str
    status: str
    reason: str = ""
    kind: str = ""
    attempt_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewEvidenceRecoveryInput:
    pr_number: int
    head_sha: str
    mergeable: str = ""
    merge_state_status: str = ""
    required_roles: Sequence[str] = ()
    github_review_evidences: Sequence[ReviewEvidenceLike] = ()
    pending_roles: Sequence[str] = ()
    ledger_rows: Sequence[ReviewEvidenceRecoveryLedgerRow] = ()
    cap: int = DEFAULT_REVIEW_RECOVERY_CAP


@dataclass(frozen=True)
class ReviewEvidenceRecoveryProjection:
    roles: tuple[str, ...]
    reason_by_role: dict[str, str]
    attempt_keys: tuple[str, ...]
    attempt_count_by_key: dict[str, int]
    cap: int
    status_only: bool = False
    status_reason: str = ""
    capped_roles: tuple[str, ...] = ()
    complete_roles: tuple[str, ...] = ()
    pending_roles: tuple[str, ...] = ()


def review_recovery_attempt_key(pr_number: int, role: str, head_sha: str, invalid_reason_family: str) -> str:
    return f"{pr_number}:{role}:{head_sha}:{invalid_reason_family}"


def ledger_row_from_mapping(row: Mapping[str, object]) -> ReviewEvidenceRecoveryLedgerRow:
    keys_value = row.get("review_recovery_attempt_keys")
    keys = tuple(str(key) for key in keys_value) if isinstance(keys_value, list) else ()
    return ReviewEvidenceRecoveryLedgerRow(
        action_id=str(row.get("action_id") or ""),
        status=str(row.get("status") or ""),
        reason=str(row.get("reason") or ""),
        kind=str(row.get("kind") or ""),
        attempt_keys=keys,
    )


def project_review_evidence_recovery(request: ReviewEvidenceRecoveryInput) -> ReviewEvidenceRecoveryProjection:
    cap = request.cap if request.cap > 0 else DEFAULT_REVIEW_RECOVERY_CAP
    required_roles = tuple(request.required_roles)
    head_sha = request.head_sha.strip()
    if _conflict_blocked(request.mergeable, request.merge_state_status):
        return ReviewEvidenceRecoveryProjection(
            roles=(),
            reason_by_role={},
            attempt_keys=(),
            attempt_count_by_key={},
            cap=cap,
            status_only=True,
            status_reason=CONFLICT_BLOCK_REASON,
        )

    selection = select_latest_live_head_review_evidence(
        request.github_review_evidences,
        live_head_sha=head_sha,
        required_roles=required_roles,
    )
    complete_roles = tuple(role for role in required_roles if role in selection.by_role)
    pending_roles = tuple(role for role in required_roles if role in set(request.pending_roles))
    reason_by_role: dict[str, str] = {}
    candidate_keys: dict[str, str] = {}
    for role in required_roles:
        if role in selection.by_role or role in request.pending_roles:
            continue
        reason = _reason_family_for_role(role, selection.invalid, selection.terminal_failed_roles)
        reason_by_role[role] = reason
        candidate_keys[role] = review_recovery_attempt_key(request.pr_number, role, head_sha, reason)

    attempt_count_by_key = {
        key: _attempt_count_for_key(key, role, request)
        for role, key in candidate_keys.items()
    }
    capped_roles = tuple(
        role
        for role in required_roles
        if role in candidate_keys and attempt_count_by_key[candidate_keys[role]] >= cap
    )
    roles = tuple(role for role in required_roles if role in candidate_keys and role not in capped_roles)
    attempt_keys = tuple(candidate_keys[role] for role in roles)

    if roles:
        return ReviewEvidenceRecoveryProjection(
            roles=roles,
            reason_by_role={role: reason_by_role[role] for role in roles},
            attempt_keys=attempt_keys,
            attempt_count_by_key=attempt_count_by_key,
            cap=cap,
            capped_roles=capped_roles,
            complete_roles=complete_roles,
            pending_roles=pending_roles,
        )
    if capped_roles:
        return ReviewEvidenceRecoveryProjection(
            roles=(),
            reason_by_role=reason_by_role,
            attempt_keys=(),
            attempt_count_by_key=attempt_count_by_key,
            cap=cap,
            status_only=True,
            status_reason=CAPPED_STATUS_REASON,
            capped_roles=capped_roles,
            complete_roles=complete_roles,
            pending_roles=pending_roles,
        )
    return ReviewEvidenceRecoveryProjection(
        roles=(),
        reason_by_role={},
        attempt_keys=(),
        attempt_count_by_key=attempt_count_by_key,
        cap=cap,
        complete_roles=complete_roles,
        pending_roles=pending_roles,
    )


def _conflict_blocked(mergeable: str, merge_state_status: str) -> bool:
    return mergeable.upper() == "CONFLICTING" or merge_state_status.upper() == "DIRTY"


def _reason_family_for_role(role: str, invalid: Sequence[str], terminal_failed_roles: Sequence[str]) -> str:
    if role in terminal_failed_roles:
        return "terminal_failed_github_review_evidence"
    suffix = f":{role}"
    role_reasons = [reason for reason in invalid if reason.endswith(suffix)]
    if not role_reasons:
        return MISSING_GITHUB_EVIDENCE_REASON
    family = role_reasons[-1].rsplit(":", 1)[0]
    if family == "stale_reviewed_head_sha":
        return "stale_github_review_evidence"
    return family or MISSING_GITHUB_EVIDENCE_REASON


def _attempt_count_for_key(key: str, role: str, request: ReviewEvidenceRecoveryInput) -> int:
    count = 0
    legacy_seen = False
    legacy_action_id = f"{RECOVERY_KIND}:{request.pr_number}:{request.head_sha}"
    for row in request.ledger_rows:
        if row.kind and row.kind != RECOVERY_KIND:
            continue
        if row.status != "applied":
            continue
        if key in row.attempt_keys:
            count += 1
            continue
        if not row.attempt_keys and row.action_id == legacy_action_id and not legacy_seen:
            legacy_seen = True
            count += 1
    return count
