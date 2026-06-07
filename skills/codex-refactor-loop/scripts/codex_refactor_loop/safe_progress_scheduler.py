"""Risk admission for closed wakeup actions and dispatch payload metadata."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence


WorkUnitRiskTier = Literal["low", "medium", "high"]
ExecutionPolicy = Literal["auto", "cautious", "blocked"]

OWNER = "safe_progress_scheduler"
BLOCKED_QUEUE_PATH = Path(".refactor-loop/state/safe-progress-blocked-queue.json")
MEDIUM_NON_SPAWN_LIMIT_PER_TICK = 1
MEDIUM_DISPATCH_LIMIT_PER_TICK = 1

FORBIDDEN_METADATA_FIELDS = frozenset(
    {
        "cmd",
        "argv",
        "args",
        "shell",
        "command_line",
        "commands",
        "env",
        "git",
        "gh",
        "executor",
        "lifecycle_authority",
        "lifecycle_owner",
    }
)
SPAWN_ONLY_CONTROLLER_ACTION = "spawn_codex_harness_background"
KNOWN_CONTROLLER_ACTIONS = frozenset(
    {
        SPAWN_ONLY_CONTROLLER_ACTION,
        "safe_push",
        "dispatch_consensus_implementation",
        "publish_implementation_output",
        "publish_worker_output_from_action",
        "publish_review_fix_output_from_action",
        "dispatch_reviewers",
        "dispatch_remote_ci_fix",
        "dispatch_pr_rebase_resolve",
        "commit_push_resolved_pr_rebase",
        "open_release_rollup_pr_from_action",
        "close_managed_item_from_drop_marker",
        "review_gate",
        "auto_merge_release_rollup_pr_from_action",
        "dispatch_release_candidate",
        "publish_release_candidate",
        "apply_issue_decomposition_plan",
        "apply_default_issue_intake_claim",
    }
)
READ_ONLY_DISPATCH_PATTERNS = (
    "audit-iter-",
    "phase9-" + "issue",
    "solver-" + "issue",
    "meta-judge-" + "issue",
    "review-" + "pr",
    "reviewer-" + "pr",
)
MUTABLE_DISPATCH_PATTERNS = (
    "implement-",
    "fix-" + "pr",
    "remote-ci-fix",
    "test-add-",
    "verify-",
    "hotfix-",
)


@dataclass(frozen=True)
class BlockedQueueItem:
    schema: str
    work_unit_id: str
    source: str
    risk_tier: WorkUnitRiskTier
    blocker_reason: str
    unblock_evidence_required: str
    last_evaluated_at: str
    next_eligible_evaluation_condition: str
    action_id: str
    target: Any
    controller_action: str
    no_lifecycle_authority: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SafeProgressDecision:
    risk_tier: WorkUnitRiskTier
    execution_policy: ExecutionPolicy
    executable: bool
    blocker_reason: str = ""
    forbidden_fields: tuple[str, ...] = ()
    blocked_item: BlockedQueueItem | None = None


@dataclass(frozen=True)
class SafeProgressProjection:
    actions: list[dict[str, Any]]
    blocked_queue: list[dict[str, Any]]
    counters: dict[str, int]
    medium_non_spawn_limit_per_tick: int = MEDIUM_NON_SPAWN_LIMIT_PER_TICK
    medium_dispatch_limit_per_tick: int = MEDIUM_DISPATCH_LIMIT_PER_TICK

    def as_dict(self) -> dict[str, Any]:
        return {
            "owner": OWNER,
            "actions": self.actions,
            "blocked_queue": self.blocked_queue,
            "counters": self.counters,
            "medium_non_spawn_limit_per_tick": self.medium_non_spawn_limit_per_tick,
            "medium_dispatch_limit_per_tick": self.medium_dispatch_limit_per_tick,
            "no_lifecycle_authority": True,
        }


def classify_wakeup_action(action: Mapping[str, Any]) -> SafeProgressDecision:
    now = _utc_ts()
    forbidden = _forbidden_field_paths(action)
    if forbidden:
        return _blocked_decision(
            action,
            now=now,
            reason="forbidden_fields:" + ",".join(forbidden),
            forbidden_fields=forbidden,
        )
    if "no_lifecycle_authority" in action and action.get("no_lifecycle_authority") is not True:
        return _blocked_decision(action, now=now, reason="invalid_no_lifecycle_authority")
    declared_risk = _declared_risk(action.get("risk_tier"))
    declared_policy = _declared_policy(action.get("execution_policy"))
    if declared_risk == "high" or declared_policy == "blocked":
        return _blocked_decision(action, now=now, reason="declared_high_or_blocked")
    if declared_risk == "medium":
        return SafeProgressDecision("medium", "cautious", True)
    if declared_risk == "low":
        return SafeProgressDecision("low", declared_policy or "auto", True)
    if action.get("status_only") is True or not action.get("controller_action"):
        return SafeProgressDecision("low", "auto", True)
    controller_action = str(action.get("controller_action") or "")
    if controller_action == SPAWN_ONLY_CONTROLLER_ACTION:
        return SafeProgressDecision("low", "auto", True)
    if controller_action in KNOWN_CONTROLLER_ACTIONS:
        return SafeProgressDecision("medium", "cautious", True)
    return _blocked_decision(action, now=now, reason=f"unknown_executable_action:{controller_action or 'missing'}")


def classify_dispatch_payload(payload: Mapping[str, Any], *, task_id: str) -> SafeProgressDecision:
    now = _utc_ts()
    forbidden = _forbidden_field_paths(payload)
    if forbidden:
        return _blocked_decision(
            payload,
            now=now,
            reason="forbidden_fields:" + ",".join(forbidden),
            forbidden_fields=forbidden,
            action_id=f"dispatch:{task_id}",
            work_unit_id=task_id,
        )
    if "no_lifecycle_authority" in payload and payload.get("no_lifecycle_authority") is not True:
        return _blocked_decision(
            payload,
            now=now,
            reason="invalid_no_lifecycle_authority",
            action_id=f"dispatch:{task_id}",
            work_unit_id=task_id,
        )
    controller_action = str(payload.get("controller_action") or SPAWN_ONLY_CONTROLLER_ACTION)
    if controller_action != SPAWN_ONLY_CONTROLLER_ACTION:
        return _blocked_decision(
            payload,
            now=now,
            reason=f"dispatch_controller_action_not_spawn:{controller_action}",
            action_id=f"dispatch:{task_id}",
            work_unit_id=task_id,
        )
    declared_risk = _declared_risk(payload.get("risk_tier"))
    if declared_risk == "high":
        return _blocked_decision(
            payload,
            now=now,
            reason="declared_high_or_blocked",
            action_id=f"dispatch:{task_id}",
            work_unit_id=task_id,
        )
    if declared_risk == "medium" or task_id.startswith(MUTABLE_DISPATCH_PATTERNS):
        return SafeProgressDecision("medium", "cautious", True)
    if declared_risk == "low" or task_id.startswith(READ_ONLY_DISPATCH_PATTERNS):
        return SafeProgressDecision("low", "auto", True)
    return SafeProgressDecision("low", "auto", True)


def project_wakeup_actions(actions: Sequence[Mapping[str, Any]], *, now: str) -> SafeProgressProjection:
    executable_actions: list[dict[str, Any]] = []
    blocked_queue: list[dict[str, Any]] = []
    counters = {"low": 0, "medium": 0, "high": 0, "blocked": 0}
    for action in actions:
        mutable_action = dict(action)
        decision = classify_wakeup_action(mutable_action)
        counters[decision.risk_tier] += 1
        if not decision.executable:
            counters["blocked"] += 1
            item = decision.blocked_item or _blocked_item(mutable_action, now, decision.blocker_reason)
            blocked_queue.append(item.as_dict())
            continue
        mutable_action["risk_tier"] = decision.risk_tier
        mutable_action["execution_policy"] = decision.execution_policy
        executable_actions.append(mutable_action)
    return SafeProgressProjection(executable_actions, blocked_queue, counters)


def validate_runner_action(action: Mapping[str, Any]) -> str | None:
    if action.get("risk_tier") == "high":
        return "risk_tier_high"
    if action.get("risk_tier") == "medium" and action.get("execution_policy") != "cautious":
        return "medium_requires_cautious_execution_policy"
    decision = classify_wakeup_action(action)
    if decision.risk_tier == "high" or not decision.executable:
        return decision.blocker_reason or "risk_tier_high"
    return None


def write_blocked_queue(repo_root: Path, blocked_queue: Sequence[Mapping[str, Any]], *, now: str) -> Path:
    items = [dict(item) for item in blocked_queue]
    path = repo_root / BLOCKED_QUEUE_PATH
    document = {
        "schema": "safe-progress-blocked-queue",
        "generated_at": now,
        "owner": OWNER,
        "items": items,
        "counters": {
            "items": len(items),
            "high": sum(1 for item in items if item.get("risk_tier") == "high"),
        },
        "no_lifecycle_authority": True,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def _blocked_decision(
    action: Mapping[str, Any],
    *,
    now: str,
    reason: str,
    forbidden_fields: Sequence[str] = (),
    action_id: str | None = None,
    work_unit_id: str | None = None,
) -> SafeProgressDecision:
    item = _blocked_item(
        action,
        now,
        reason,
        action_id=action_id,
        work_unit_id=work_unit_id,
    )
    return SafeProgressDecision("high", "blocked", False, reason, tuple(forbidden_fields), item)


def _blocked_item(
    action: Mapping[str, Any],
    now: str,
    reason: str,
    *,
    action_id: str | None = None,
    work_unit_id: str | None = None,
) -> BlockedQueueItem:
    target = action.get("target")
    if target is None and action.get("target_kind") and action.get("target_number"):
        target = {"kind": action.get("target_kind"), "number": action.get("target_number")}
    return BlockedQueueItem(
        schema="safe-progress-blocked-queue-item",
        work_unit_id=work_unit_id or _work_unit_id(action),
        source=str(action.get("source_artifact") or action.get("source") or action.get("route") or "safe-progress-admission"),
        risk_tier="high",
        blocker_reason=reason,
        unblock_evidence_required="replace the source projection with low/medium metadata and no forbidden command/lifecycle fields",
        last_evaluated_at=now,
        next_eligible_evaluation_condition="next wakeup-plan or concurrency-dispatch tick after source metadata changes",
        action_id=action_id or str(action.get("action_id") or action.get("intent_id") or ""),
        target=target,
        controller_action=str(action.get("controller_action") or ""),
        no_lifecycle_authority=True,
    )


def _work_unit_id(action: Mapping[str, Any]) -> str:
    item = action.get("item")
    if isinstance(item, str) and item:
        return item
    target = action.get("target")
    if isinstance(target, Mapping):
        kind = str(target.get("kind") or "")
        number = target.get("number")
        task_id = target.get("task_id")
        if kind and isinstance(number, int):
            return f"{kind}-{number}"
        if isinstance(task_id, str) and task_id:
            return task_id
    action_id = action.get("action_id")
    return str(action_id or "unknown")


def _forbidden_field_paths(value: Any, prefix: str = "") -> tuple[str, ...]:
    paths: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            key_path = f"{prefix}.{key_text}" if prefix else key_text
            if key_text in FORBIDDEN_METADATA_FIELDS:
                paths.append(key_path)
            paths.extend(_forbidden_field_paths(child, key_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            paths.extend(_forbidden_field_paths(child, f"{prefix}[{index}]"))
    return tuple(sorted(paths))


def _declared_risk(value: Any) -> WorkUnitRiskTier | None:
    return value if value in {"low", "medium", "high"} else None


def _declared_policy(value: Any) -> ExecutionPolicy | None:
    return value if value in {"auto", "cautious", "blocked"} else None


def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
