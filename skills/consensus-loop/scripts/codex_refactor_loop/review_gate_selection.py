"""Shared review-gate evidence selection."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol, Sequence


REVIEW_DONE_RE = re.compile(r"^REVIEW_DONE:([1-9][0-9]*):([A-Za-z][A-Za-z0-9_-]*):(approve|comment|reject)$")
REVIEW_HEAD_RE = re.compile(r"(?im)^(?:reviewed[-_ ]?head[-_ ]?sha|head[-_ ]?sha|headRefOid|REVIEW_HEAD_SHA)\s*[:=]\s*([0-9a-f]{7,64})\s*$")
REVIEW_ROUND_RE = re.compile(r"(?im)^(?:review[-_ ]?round|round)\s*[:=]\s*([1-9][0-9]*)\s*$")
AI_SENTINEL = "⟦AI:AUTO-LOOP⟧"


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
class SelectedReviewEvidence:
    by_role: dict[str, ReviewEvidenceLike]
    invalid: list[str]
    pending: list[str]
    terminal_failed_roles: list[str]
    complete_round: int | None


@dataclass(frozen=True)
class ParsedGithubReviewEvidence:
    role: str
    round_number: int
    verdict: str
    head_sha: str
    source: str
    valid: bool = True
    reason: str = ""
    terminal_failed: bool = False
    pending: bool = False
    created_at: str = ""
    source_index: int = 0
    comment_id: int | None = None


def _evidence_created_at(evidence: ReviewEvidenceLike) -> str:
    return str(getattr(evidence, "created_at", "") or "")


def _evidence_source_index(evidence: ReviewEvidenceLike) -> int:
    value = getattr(evidence, "source_index", 0)
    return value if isinstance(value, int) else 0


def _evidence_comment_id(evidence: ReviewEvidenceLike) -> int:
    value = getattr(evidence, "comment_id", None)
    return value if isinstance(value, int) else 0


def _has_ordering_metadata(evidence: ReviewEvidenceLike) -> bool:
    return bool(_evidence_created_at(evidence)) or _evidence_comment_id(evidence) > 0 or _evidence_source_index(evidence) > 0


def _recency_key(evidence: ReviewEvidenceLike) -> tuple[str, int, int]:
    return (_evidence_created_at(evidence), _evidence_comment_id(evidence), _evidence_source_index(evidence))


def extract_review_head_sha(text: str) -> str:
    match = REVIEW_HEAD_RE.search(text)
    return match.group(1) if match else ""


def extract_review_round(text: str) -> int | None:
    match = REVIEW_ROUND_RE.search(text)
    if match is None:
        return None
    return int(match.group(1))


def _has_final_ai_sentinel(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return bool(lines) and lines[-1] == AI_SENTINEL


def _loose_review_marker_role(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("REVIEW_DONE:"):
            continue
        parts = stripped.split(":")
        if len(parts) >= 3:
            return parts[2]
    return ""


def _parsed_github_review_evidence(
    role: str,
    round_number: int,
    verdict: str,
    head_sha: str,
    source: str,
    valid: bool = True,
    reason: str = "",
    *,
    created_at: str = "",
    source_index: int = 0,
    comment_id: int | None = None,
) -> ParsedGithubReviewEvidence:
    return ParsedGithubReviewEvidence(
        role,
        round_number,
        verdict,
        head_sha,
        source,
        valid,
        reason,
        created_at=created_at,
        source_index=source_index,
        comment_id=comment_id,
    )


def parse_github_review_evidence(
    body: str,
    pr_number: int,
    *,
    source: str,
    created_at: str = "",
    source_index: int = 0,
    comment_id: int | None = None,
) -> ParsedGithubReviewEvidence | None:
    if "REVIEW_DONE:" not in body:
        return None
    parsed_head_sha = extract_review_head_sha(body)
    parsed_round_number = extract_review_round(body) or 1
    marker_matches = [REVIEW_DONE_RE.fullmatch(line.strip()) for line in body.splitlines()]
    marker_matches = [match for match in marker_matches if match is not None]
    if not marker_matches:
        role = _loose_review_marker_role(body) or "github"
        return _parsed_github_review_evidence(role, parsed_round_number, "", parsed_head_sha, source, False, f"invalid_review_marker:{role}", created_at=created_at, source_index=source_index, comment_id=comment_id)
    marker_keys = {(match.group(1), match.group(2), match.group(3)) for match in marker_matches}
    marker = marker_matches[0]
    role = marker.group(2)
    verdict = marker.group(3)
    if len(marker_keys) != 1:
        return _parsed_github_review_evidence(role, parsed_round_number, "", parsed_head_sha, source, False, f"invalid_review_marker:{role}", created_at=created_at, source_index=source_index, comment_id=comment_id)
    if marker.group(1) != str(pr_number):
        return _parsed_github_review_evidence(role, parsed_round_number, "", parsed_head_sha, source, False, f"invalid_review_marker:{role}", created_at=created_at, source_index=source_index, comment_id=comment_id)
    if not _has_final_ai_sentinel(body):
        return _parsed_github_review_evidence(role, parsed_round_number, verdict, parsed_head_sha, source, False, f"missing_final_ai_sentinel:{role}", created_at=created_at, source_index=source_index, comment_id=comment_id)
    round_number = parsed_round_number
    head_sha = parsed_head_sha
    if not head_sha:
        return _parsed_github_review_evidence(role, round_number, verdict, "", source, False, f"missing_reviewed_head_sha:{role}", created_at=created_at, source_index=source_index, comment_id=comment_id)
    return _parsed_github_review_evidence(role, round_number, verdict, head_sha, source, created_at=created_at, source_index=source_index, comment_id=comment_id)


def _append_block_for_evidence(
    evidence: ReviewEvidenceLike,
    *,
    role: str,
    live_head_sha: str,
    invalid: list[str],
    pending: list[str],
    terminal_failed_roles: list[str],
) -> None:
    if evidence.pending:
        pending.append(evidence.reason or f"pending:{role}")
    elif evidence.terminal_failed:
        terminal_failed_roles.append(role)
        invalid.append(evidence.reason or f"terminal_failed:{role}")
    elif not evidence.valid:
        invalid.append(evidence.reason or f"invalid:{role}")
    elif not evidence.head_sha:
        invalid.append(f"missing_reviewed_head_sha:{role}")
    elif evidence.head_sha != live_head_sha:
        invalid.append(f"stale_reviewed_head_sha:{role}")


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
        ordered_items = [evidence for evidence in role_items if _has_ordering_metadata(evidence)]
        unordered_items = [evidence for evidence in role_items if not _has_ordering_metadata(evidence)]
        max_ordered_round = max((evidence.round_number for evidence in ordered_items), default=0)
        max_unordered_round = max((evidence.round_number for evidence in unordered_items), default=0)
        if ordered_items and max_ordered_round >= max_unordered_round:
            highest_key = max(_recency_key(evidence) for evidence in ordered_items)
            latest_ordered = [evidence for evidence in ordered_items if _recency_key(evidence) == highest_key]
            if len(latest_ordered) > 1:
                invalid.append(f"duplicate_reviewer_evidence:{role}")
                continue
            evidence = latest_ordered[0]
            if evidence.valid and not evidence.pending and not evidence.terminal_failed and evidence.head_sha == live_head_sha:
                selected[role] = evidence
                max_round = evidence.round_number if max_round is None else max(max_round, evidence.round_number)
            else:
                _append_block_for_evidence(
                    evidence,
                    role=role,
                    live_head_sha=live_head_sha,
                    invalid=invalid,
                    pending=pending,
                    terminal_failed_roles=terminal_failed_roles,
                )
            continue

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
            _append_block_for_evidence(
                evidence,
                role=role,
                live_head_sha=live_head_sha,
                invalid=invalid,
                pending=pending,
                terminal_failed_roles=terminal_failed_roles,
            )
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
