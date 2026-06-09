"""Release-candidate handoff liveness classification."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..state import read_json


RELEASE_CANDIDATE_STALE_REASONS = frozenset(
    {
        "release_candidate_expired",
        "release_candidate_generated_stale",
        "release_candidate_consumed_by_publish_result",
        "release_candidate_decision_mismatch",
        "release_candidate_target_ref_invalid",
    }
)
GENERATION_LIVE_SECONDS = 120 * 60


@dataclass(frozen=True)
class ReleaseCandidateLiveness:
    blocks_dispatch: bool
    stale_reason: str | None = None


def classify_release_candidate_liveness(
    repo_root: Path,
    candidate: Any,
    decision: Any,
    *,
    now: datetime | None = None,
) -> ReleaseCandidateLiveness:
    current_time = _utc(now)
    if not isinstance(candidate, dict):
        return ReleaseCandidateLiveness(blocks_dispatch=True)
    if _candidate_consumed_by_publish_result(repo_root, candidate):
        return ReleaseCandidateLiveness(
            blocks_dispatch=False,
            stale_reason="release_candidate_consumed_by_publish_result",
        )
    if _target_ref_invalid(candidate):
        return ReleaseCandidateLiveness(
            blocks_dispatch=False,
            stale_reason="release_candidate_target_ref_invalid",
        )
    expiry = _candidate_expiry(candidate)
    if expiry is False:
        return ReleaseCandidateLiveness(blocks_dispatch=True)
    if expiry is not None and expiry <= current_time:
        return ReleaseCandidateLiveness(blocks_dispatch=False, stale_reason="release_candidate_expired")
    generated_at = _candidate_generated_at(candidate)
    if generated_at is False:
        return ReleaseCandidateLiveness(blocks_dispatch=True)
    if generated_at is not None:
        age_seconds = (current_time - generated_at).total_seconds()
        if age_seconds < 0:
            return ReleaseCandidateLiveness(blocks_dispatch=True)
        if age_seconds > GENERATION_LIVE_SECONDS:
            return ReleaseCandidateLiveness(
                blocks_dispatch=False,
                stale_reason="release_candidate_generated_stale",
            )
    if isinstance(decision, dict) and decision:
        if _decision_mismatch(candidate, decision):
            return ReleaseCandidateLiveness(
                blocks_dispatch=False,
                stale_reason="release_candidate_decision_mismatch",
            )
    return ReleaseCandidateLiveness(blocks_dispatch=True)


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _candidate_expiry(candidate: dict[str, Any]) -> datetime | bool | None:
    raw = candidate.get("expires_at")
    if raw is None:
        return None
    parsed = _parse_time(raw)
    return parsed if parsed is not None else False


def _candidate_generated_at(candidate: dict[str, Any]) -> datetime | bool | None:
    raw = candidate.get("generated_at")
    if raw is None:
        return None
    parsed = _parse_time(raw)
    return parsed if parsed is not None else False


def _target_ref_invalid(candidate: dict[str, Any]) -> bool:
    if "target_ref" not in candidate:
        return True
    target_ref = candidate.get("target_ref")
    return not isinstance(target_ref, str) or not target_ref.strip()


def _decision_mismatch(candidate: dict[str, Any], decision: dict[str, Any]) -> bool:
    if candidate.get("ready") is not True:
        return True
    for field in ("from_version", "to_version", "bump_type", "coordinate_policy"):
        if candidate.get(field) != decision.get(field):
            return True
    expected_digest = candidate.get("decision_digest")
    if not isinstance(expected_digest, str) or not expected_digest:
        return False
    return _canonical_digest(decision) != expected_digest


def _candidate_consumed_by_publish_result(repo_root: Path, candidate: dict[str, Any]) -> bool:
    to_version = str(candidate.get("to_version") or "").strip()
    if not to_version:
        return False
    result = read_json(repo_root / ".refactor-loop" / "state" / "release-publish-result.json", {})
    if not isinstance(result, dict):
        return False
    version = str(result.get("version") or "").strip()
    tag = str(result.get("tag") or "").strip()
    return version == to_version or tag == f"v{to_version}"


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical_digest(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "RELEASE_CANDIDATE_STALE_REASONS",
    "ReleaseCandidateLiveness",
    "classify_release_candidate_liveness",
]
