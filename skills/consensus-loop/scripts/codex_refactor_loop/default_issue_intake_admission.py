"""Read-only admission for default issue intake claims."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import labels
from .context import LoopContext
from .daemon_progress import read_progress
from .default_issue_intake import default_issue_intake_enabled


DEFAULT_ACTIVE_DESIGN_CAP = 3
DEFAULT_CLAIM_COOLDOWN_SECONDS = 3600
CLAIM_STATE_PATH = ".refactor-loop/state/default-issue-intake-claims.json"
UPSTREAM_PROGRESS_DAEMONS = (
    "phase9_router_daemon",
    "wakeup_runner_daemon",
    "concurrency_monitor",
)
ADMISSION_PRECONDITIONS = (
    "default_issue_intake_admission_active_cap",
    "default_issue_intake_admission_cooldown",
    "default_issue_intake_admission_upstream_idle",
    "default_issue_intake_admission_concrete_work_unit",
)
CONTAINER_TITLE_RE = re.compile(r"^\s*(?:epic|tracking|container)\s*(?::|\b)", re.IGNORECASE)


@dataclass(frozen=True)
class DefaultIssueIntakeCandidate:
    number: int
    title: str
    labels: tuple[str, ...]
    updated_at: str = ""
    is_pr: bool = False
    state: str = "open"


@dataclass(frozen=True)
class DefaultIssueIntakeAdmissionDecision:
    accepted: bool
    reason: str
    preconditions: tuple[str, ...]
    proof: dict[str, Any]

    def as_action_payload(self) -> dict[str, Any]:
        return {
            "status": "accepted" if self.accepted else "rejected",
            "reason": self.reason,
            "proof": dict(self.proof),
        }


class DefaultIssueIntakeAdmission:
    def __init__(
        self,
        ctx: LoopContext,
        *,
        managed_items: Sequence[Mapping[str, Any] | Any],
        pending_spawn_intents: Sequence[Mapping[str, Any]],
        now_iso: str | None = None,
    ) -> None:
        self.ctx = ctx
        self.managed_items = tuple(managed_items)
        self.pending_spawn_intents = tuple(pending_spawn_intents)
        self.now = _parse_time(now_iso) if now_iso else datetime.now(timezone.utc)

    def evaluate(self, candidate: DefaultIssueIntakeCandidate) -> DefaultIssueIntakeAdmissionDecision:
        proof = self._base_proof(candidate)
        if not default_issue_intake_enabled(self.ctx.host_env):
            return self._reject("disabled", proof)
        if candidate.number <= 0 or candidate.state.lower() != "open":
            return self._reject("target_not_open", proof)
        if candidate.is_pr:
            return self._reject("target_is_pr", proof)
        if labels.MANAGED in labels.normalize_label_set(candidate.labels).canonical:
            return self._reject("target_already_managed", proof)
        if not issue_title_is_concrete_work_unit(candidate.title):
            proof["concrete_work_unit"] = False
            return self._reject("not_concrete_work_unit", proof)

        active_design = _active_design_solving_count(self.managed_items)
        active_cap = _positive_host_int(
            self.ctx.host_env.get("DEFAULT_ISSUE_INTAKE_ACTIVE_DESIGN_CAP"),
            DEFAULT_ACTIVE_DESIGN_CAP,
        )
        proof["active_design_solving"] = active_design
        proof["active_design_cap"] = active_cap
        if active_design >= active_cap:
            return self._reject("active_design_cap_reached", proof)

        remaining = self._claim_cooldown_remaining_seconds()
        proof["claim_cooldown_remaining_seconds"] = remaining
        if remaining > 0:
            return self._reject("claim_cooldown_active", proof)

        upstream_state = self._upstream_state()
        proof["upstream_state"] = upstream_state
        proof["pending_spawn_intents"] = len(self.pending_spawn_intents)
        if upstream_state != "upstream_idle":
            return self._reject("upstream_not_idle", proof)

        return DefaultIssueIntakeAdmissionDecision(True, "", ADMISSION_PRECONDITIONS, proof)

    def _base_proof(self, candidate: DefaultIssueIntakeCandidate) -> dict[str, Any]:
        return {
            "candidate_issue": candidate.number,
            "enabled": default_issue_intake_enabled(self.ctx.host_env),
            "active_design_solving": 0,
            "active_design_cap": _positive_host_int(
                self.ctx.host_env.get("DEFAULT_ISSUE_INTAKE_ACTIVE_DESIGN_CAP"),
                DEFAULT_ACTIVE_DESIGN_CAP,
            ),
            "claim_cooldown_seconds": _positive_host_int(
                self.ctx.host_env.get("DEFAULT_ISSUE_INTAKE_CLAIM_COOLDOWN_SECONDS"),
                DEFAULT_CLAIM_COOLDOWN_SECONDS,
            ),
            "claim_cooldown_remaining_seconds": 0,
            "upstream_state": "upstream_idle",
            "pending_spawn_intents": len(self.pending_spawn_intents),
            "concrete_work_unit": issue_title_is_concrete_work_unit(candidate.title),
        }

    def _claim_cooldown_remaining_seconds(self) -> int:
        cooldown = _positive_host_int(
            self.ctx.host_env.get("DEFAULT_ISSUE_INTAKE_CLAIM_COOLDOWN_SECONDS"),
            DEFAULT_CLAIM_COOLDOWN_SECONDS,
        )
        if cooldown <= 0:
            return 0
        repo_root = _ctx_repo_root(self.ctx)
        if repo_root is None:
            return 0
        last_claimed_at = _last_claimed_at(repo_root / CLAIM_STATE_PATH)
        if last_claimed_at is None:
            return 0
        elapsed = int((self.now - last_claimed_at).total_seconds())
        if elapsed >= cooldown:
            return 0
        return max(0, cooldown - max(0, elapsed))

    def _upstream_state(self) -> str:
        if self.pending_spawn_intents:
            return "pending_spawn_intent"
        repo_root = _ctx_repo_root(self.ctx)
        if repo_root is None:
            return "upstream_idle"
        for daemon_name in UPSTREAM_PROGRESS_DAEMONS:
            progress = read_progress(repo_root, daemon_name)
            if progress is None:
                continue
            if progress.status != "complete":
                return f"daemon_progress_incomplete:{daemon_name}"
        return "upstream_idle"

    def _reject(self, reason: str, proof: dict[str, Any]) -> DefaultIssueIntakeAdmissionDecision:
        return DefaultIssueIntakeAdmissionDecision(False, reason, (), proof)


def issue_title_is_concrete_work_unit(title: str) -> bool:
    text = str(title or "").strip()
    if not text:
        return False
    return CONTAINER_TITLE_RE.search(text) is None


def _active_design_solving_count(items: Sequence[Mapping[str, Any] | Any]) -> int:
    count = 0
    for item in items:
        if _item_kind(item) != "issue":
            continue
        label_names = _item_labels(item)
        if labels.MANAGED not in labels.normalize_label_set(label_names).canonical:
            continue
        if labels.normalize_label_set(label_names).phase == labels.PHASE_DESIGN_SOLVING:
            count += 1
    return count


def _item_kind(item: Mapping[str, Any] | Any) -> str:
    if isinstance(item, Mapping):
        return str(item.get("kind") or "").strip().lower()
    return str(getattr(item, "kind", "") or "").strip().lower()


def _item_labels(item: Mapping[str, Any] | Any) -> tuple[str, ...]:
    raw = item.get("labels") if isinstance(item, Mapping) else getattr(item, "labels", ())
    if isinstance(raw, (list, tuple, set)):
        return tuple(str(label) for label in raw if str(label))
    return ()


def _positive_host_int(value: object, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if parsed < 0:
        return default
    return parsed


def _ctx_repo_root(ctx: LoopContext) -> Path | None:
    raw = getattr(ctx, "repo_root", None)
    if isinstance(raw, Path):
        return raw
    if isinstance(raw, str) and raw.strip():
        return Path(raw)
    return None


def _last_claimed_at(path: Path) -> datetime | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, Mapping):
        return None
    raw = data.get("last_claimed_at")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return _parse_time(raw)


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.max.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
