#!/usr/bin/env python3
"""Read-only wakeup planner for codex-refactor-loop controllers.

"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from codex_refactor_loop import labels as label_catalog
from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.implement_lifecycle import (
    classify_implement_attempt,
    clear_redispatchable_implement_log,
    is_implement_log,
)
from codex_refactor_loop.implementation_pr_artifacts import (
    implementation_cluster_id,
    validate_implementation_pr_artifacts,
)
from codex_refactor_loop.managed_work_snapshot import load_open_managed_work_snapshot
from codex_refactor_loop.pr_checks import PrChecksProjection
from codex_refactor_loop.release.gate import decide_release_artifact
from codex_refactor_loop.restart import restart_managed_daemon_names
from codex_refactor_loop.transition_assessment import TransitionAssessmentReader, transition_rank_key
from codex_refactor_loop.worker_markers import (
    log_has_clean_exit,
    read_worker_terminal_marker,
)
from codex_refactor_loop.review_fix_dispatch import (
    ReviewThreadCompletionEvidence,
    validate_review_thread_completion,
)
from codex_refactor_loop.work_items import ManagedWorkProjection, extract_closing_issue_numbers, open_actionable_managed_items
from codex_refactor_loop.workflow_spec import WorkflowSpecError, load_validated_workflow_spec
from codex_refactor_loop.workflow_stages import assert_stage_slug


STALE_SECONDS = 90
META_ESCALATION_DEFAULT_HOURS = 24.0
PHASE_TO_STAGE = {
    label_catalog.PHASE_DESIGN_SOLVING: "design-consensus",
    label_catalog.PHASE_IMPLEMENTING: "implementation",
    label_catalog.PHASE_FIXING: "review-gate",
    label_catalog.PHASE_REVIEWING: "review-gate",
    label_catalog.PHASE_CI_RUNNING: "ci-watch",
    label_catalog.PHASE_PR_OPEN: "review-gate",
    label_catalog.PHASE_CONSENSUS_REACHED: "implementation",
    label_catalog.PHASE_BLOCKED: "bootstrap",
    label_catalog.PHASE_MERGED: "publish",
}
HARNESS_SPAWN_INTENT_FORBIDDEN_FIELDS = {
    "argv",
    "args",
    "shell",
    "cmd",
    "commands",
    "env",
    "git",
    "gh",
    "executor",
    "target_ref",
}
HARNESS_SPAWN_TARGET_TEXT_PATTERNS = (
    (re.compile(r"(?i)\bPR\s*#([1-9][0-9]*)\b"), "PR"),
    (re.compile(r"(?i)\bissue\s*#([1-9][0-9]*)\b"), "issue"),
    (re.compile(r"\bphase9-" + r"issue([1-9][0-9]*)-r[1-9][0-9]*-[A-Za-z][A-Za-z0-9_-]*\b"), "issue"),
    (re.compile(r"\b" + "review-" + r"pr([1-9][0-9]*)-[A-Za-z][A-Za-z0-9_-]*-r[1-9][0-9]*\b"), "PR"),
    (re.compile(r"\b" + "fix-" + r"pr([1-9][0-9]*)(?:-r|-round-)"), "PR"),
)
TERMINAL_HARNESS_SPAWN_INTENT_BLOCKED_REASONS = {"target_not_open:CLOSED", "target_not_open:MERGED"}
RUNNER_AUTHORITY = "wakeup-runner-396"
PLAN_AUTHORIZATION = "skills/codex-refactor-loop/authorizations/runtime-exceptions.md#wakeup-runner-396"
READ_ONLY_PLAN_AUTHORIZATION = "skills/codex-refactor-loop/authorizations/runtime-exceptions.md#maintainer-directive-wakeup-plan-script"
RUNNER_NAMED_HELPER_ACTIONS = {
    "spawn_codex_harness_background",
    "safe_push",
    "dispatch_consensus_implementation",
    "publish_implementation_output",
    "publish_worker_output_from_action",
    "dispatch_reviewers",
    "open_release_rollup_pr_from_action",
    "close_managed_item_from_drop_marker",
    "review_gate",
    "publish_release_candidate",
}


def _contained_execution_cd(ctx: LoopContext, text: str) -> Path:
    cd = Path(text).expanduser()
    if not cd.is_absolute():
        cd = ctx.repo_root / cd
    resolved = cd.resolve()
    try:
        resolved.relative_to(ctx.repo_root.resolve())
    except ValueError as exc:
        raise ValueError(f"cd escapes REPO_ROOT: {text!r}") from exc
    return resolved
EXECUTABLE_ACTION_KINDS = {
    "harness-spawn-intent",
    "repository-stalled-meta-reflector",
    "unpushed-worker-output",
    "completed-marker",
    "release-rollup-needed",
    "ci-red",
    "review-evidence-redispatch",
}
NON_ACTION_PHASE_LABELS = {
    label_catalog.PHASE_PR_OPEN: "pr-open",
    label_catalog.PHASE_CI_RUNNING: "ci-running",
    label_catalog.PHASE_BLOCKED: "blocked",
    label_catalog.PHASE_MERGED: "merged",
}
DESIGN_CONSENSUS_TERMINAL_PHASES = frozenset(
    {
        label_catalog.PHASE_CONSENSUS_REACHED,
        label_catalog.PHASE_IMPLEMENTING,
        label_catalog.PHASE_MERGED,
        label_catalog.PHASE_CLOSED,
    }
)
REVIEW_HEAD_RE = re.compile(r"(?im)^(?:reviewed[-_ ]?head[-_ ]?sha|head[-_ ]?sha|headRefOid|REVIEW_HEAD_SHA)\s*[:=]\s*([0-9a-f]{7,64})\s*$")
REVIEW_ARTIFACT_RE = re.compile(r"^review-pr([1-9][0-9]*)-([A-Za-z][A-Za-z0-9_-]*)-r([1-9][0-9]*)\.md$")
REVIEW_LOG_RE = re.compile(r"^review-pr([1-9][0-9]*)-([A-Za-z][A-Za-z0-9_-]*)-r([1-9][0-9]*)\.log$")
REQUIRED_REVIEW_ROLES = ("architect", "tests", "quality")
CONSENSUS_JUDGE_ARTIFACT_RE = re.compile(r"^phase9-issue([1-9][0-9]*)-r([1-9][0-9]*)-judge\.md$")
CONSENSUS_JUDGE_LOG_RE = re.compile(r"^phase9-issue([1-9][0-9]*)-r([1-9][0-9]*)-judge\.log$")
DESIGN_CONSENSUS_LOG_RE = re.compile(r"^phase9-issue([1-9][0-9]*)-r([1-9][0-9]*)-(minimal|structural|delete|judge)\.log$")
IMPLEMENT_PENDING_INTENT_PREFIX = "dispatch-consensus-implementation:"
IMPLEMENT_TASK_PREFIX = "implement-"


@dataclass(frozen=True)
class GhItem:
    kind: str
    number: int
    title: str
    labels: tuple[str, ...]
    head_ref: str | None = None
    head_sha: str = ""
    body: str = ""
    updated_at: str = ""

    @property
    def item(self) -> str:
        return f"{self.kind} #{self.number}"

    @property
    def milestone(self) -> bool:
        return label_catalog.MILESTONE_CURRENT in label_catalog.normalize_label_set(self.labels).canonical


@dataclass(frozen=True)
class CompletedMarkerCandidate:
    log_path: Path
    marker: str
    action: dict[str, Any]
    mtime: float


def run_json(cmd: list[str], *, cwd: Path) -> Any:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def load_host_workflow_projection(repo_root: Path) -> tuple[list[dict[str, Any]], str | None]:
    try:
        ctx = LoopContext.load(repo_root=repo_root, env=os.environ, cwd=repo_root, read_only=True)
        spec = load_validated_workflow_spec(ctx)
    except WorkflowSpecError as exc:
        return [], str(exc)
    except Exception as exc:
        return [], f"host workflow spec unavailable: {exc}"
    actions = [
        {
            "priority": 8,
            "kind": "host-workflow-event",
            "item": event.name,
            "phase": event.stage,
            "actor": event.actor,
            "status": event.status,
            "route": "host-workflow-status-projection",
            "no_lifecycle_authority": True,
        }
        for event in spec.events
    ]
    return actions, None


def _canonical_in_flight_for_log(log_path: Path, monitor: Any | None) -> bool:
    if monitor is None:
        return False
    try:
        lines = monitor.list_in_flight_codex_lines()
    except Exception:
        return False
    target = str(log_path)
    return any(target in line for line in lines)


def harness_spawn_intent_actions(
    repo_root: Path,
    ctx: LoopContext,
    monitor: Any | None = None,
    gh_items: list[GhItem] | None = None,
    gh_items_loaded: bool = False,
) -> list[dict[str, Any]]:
    pending_path = ctx.paths.pending_events
    if not pending_path.exists():
        return []
    try:
        lines = pending_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()
    terminal_blocked_intent_ids = _terminal_blocked_harness_spawn_intent_ids(lines)
    open_items = gh_items or []
    open_targets = _open_managed_targets(open_items) if gh_items_loaded else set()
    terminal_design_targets = _terminal_design_consensus_targets(open_items) if gh_items_loaded else set()
    for line in lines:
        if " HARNESS_SPAWN_INTENT " not in line:
            continue
        raw_json = line.split(" HARNESS_SPAWN_INTENT ", 1)[1]
        try:
            intent = json.loads(raw_json)
        except json.JSONDecodeError:
            actions.append(_invalid_harness_spawn_intent("invalid-json", line))
            continue
        if not isinstance(intent, dict):
            actions.append(_invalid_harness_spawn_intent("intent-not-object", line))
            continue
        intent_id = intent.get("intent_id")
        if not isinstance(intent_id, str) or not intent_id:
            actions.append(_invalid_harness_spawn_intent("missing-intent-id", line))
            continue
        if intent_id in seen:
            continue
        seen.add(intent_id)
        invalid_reason = _harness_spawn_intent_invalid_reason(intent)
        if invalid_reason:
            actions.append(_invalid_harness_spawn_intent(invalid_reason, line, intent_id=intent_id))
            continue
        try:
            cd = _contained_execution_cd(ctx, str(intent["cd"]))
            prompt = ctx.artifact_execution_path(str(intent["prompt"]))
            log_path = ctx.artifact_execution_path(str(intent["log"]))
        except Exception as exc:
            actions.append(_invalid_harness_spawn_intent(f"invalid-path:{exc}", line, intent_id=intent_id))
            continue
        _revive_stale_redispatchable_implement_log(log_path, monitor=monitor)
        if _harness_spawn_intent_log_suppresses_retry(log_path) or _canonical_in_flight_for_log(log_path, monitor):
            continue
        if _suppress_harness_spawn_intent(
            intent,
            terminal_blocked_intent_ids,
            open_targets,
            gh_items_loaded,
            terminal_design_targets,
        ):
            continue
        suppressed = _suppressed_consensus_implementation_spawn_intent(
            intent,
            repo_root,
            gh_items if gh_items_loaded else None,
            monitor,
        )
        if suppressed is not None:
            actions.append(
                _harness_spawn_intent_action(
                    intent,
                    intent_id,
                    cd,
                    prompt,
                    log_path,
                    line,
                    status_only=True,
                    suppressed_reason=suppressed,
                )
            )
            continue
        actions.append(
            _harness_spawn_intent_action(intent, intent_id, cd, prompt, log_path, line)
        )
    return actions


def _harness_spawn_intent_action(
    intent: dict[str, Any],
    intent_id: str,
    cd: Path,
    prompt: Path,
    log_path: Path,
    evidence: str,
    *,
    status_only: bool = False,
    suppressed_reason: str | None = None,
) -> dict[str, Any]:
    action = {
        "priority": 2,
        "kind": "harness-spawn-intent",
        "action_id": f"harness-spawn-intent:{intent_id}",
        "item": intent.get("task_id"),
        "phase": "work-intake",
        "actor": "controller",
        "route": intent.get("route"),
        "intent_id": intent_id,
        "source": intent.get("source"),
        "command": "spawn-codex",
        "controller_action": "spawn_codex_harness_background",
        "cd": str(cd),
        "prompt": str(prompt),
        "log": str(log_path),
        "stall": int(intent.get("stall", 5400)),
        "run_in_background_required": True,
        "no_lifecycle_authority": True,
        "reason": intent.get("reason"),
        "evidence": evidence,
        "source_artifact": ".refactor-loop/.controller-pending-events.log",
        "source_marker": evidence,
        "target_kind": "codex",
        "target_number": None,
        "target": {"kind": "codex", "task_id": str(intent.get("task_id") or intent_id)},
        "preconditions": ["active_controller_owner", "source_artifact_contains_evidence", "target_log_absent"],
        "runner_authority": RUNNER_AUTHORITY,
        "no_generic_command": True,
    }
    if status_only:
        action["status_only"] = True
        action["suppressed_reason"] = suppressed_reason
        action.pop("runner_authority", None)
        action.pop("no_generic_command", None)
    return action


def _harness_spawn_intent_log_suppresses_retry(log_path: Path) -> bool:
    if is_implement_log(log_path):
        return classify_implement_attempt(repo_root=_repo_root_from_log(log_path), log_path=log_path).in_flight
    if not log_path.exists():
        return False
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-10:]
    except OSError:
        return True
    for line in reversed(lines):
        if not line.startswith("EXIT="):
            continue
        return line.strip() == "EXIT=0"
    return True


def _repo_root_from_log(log_path: Path) -> Path:
    parts = log_path.resolve().parts
    try:
        index = parts.index(".refactor-loop")
    except ValueError:
        return log_path.resolve().parent
    return Path(*parts[:index])


def stale_revival_seconds() -> float:
    """Host-tunable idle threshold (default 3 hours) after which a stuck managed
    work item's blocking local evidence is treated as stale and re-triggered.
    `STALE_REVIVAL_HOURS` in host.env overrides it; missing/invalid/<=0 -> 3h."""
    raw = os.environ.get("STALE_REVIVAL_HOURS")
    try:
        hours = float(raw) if raw is not None and raw.strip() != "" else 3.0
    except (TypeError, ValueError):
        hours = 3.0
    if hours <= 0:
        hours = 3.0
    return hours * 3600.0


def meta_escalation_stuck_seconds() -> float:
    raw = os.environ.get("META_ESCALATION_STUCK_HOURS")
    try:
        hours = float(raw) if raw not in {None, ""} else META_ESCALATION_DEFAULT_HOURS
    except (TypeError, ValueError):
        hours = META_ESCALATION_DEFAULT_HOURS
    if hours <= 0:
        hours = META_ESCALATION_DEFAULT_HOURS
    return max(hours * 3600.0, stale_revival_seconds())


def _revive_stale_redispatchable_implement_log(
    log_path: Path, *, now: float | None = None, monitor: Any | None = None, force: bool = False
) -> bool:
    """Re-trigger a stuck implement by clearing its blocking local log. Covers two
    headless wedges: (1) a redispatchable attempt (partial/failed/markerless;
    clean :ok stale-base belongs to publish recovery, not redispatch), and
    (2) a dead worker whose log is still 'in_flight' with no terminal EXIT (the
    codex or its supervisor died mid-run, e.g. when daemons are killed). Without
    this the queued spawn intent's target_log_absent precondition never clears
    and the implement never re-dispatches.

    Automatic callers leave force=False: the log must be idle longer than
    stale_revival_seconds() (a live supervised codex cannot be silent past the
    no-output stall window, so a >threshold-stale in_flight log is a dead worker).
    The manual trigger passes force=True to revive now without waiting, but then
    an in_flight log is cleared only when a live-process check proves no codex is
    running it, so a genuinely running worker is never cleared."""
    if not is_implement_log(log_path) or not log_path.exists():
        return False
    if not force:
        try:
            age = (now if now is not None else time.time()) - log_path.stat().st_mtime
        except OSError:
            return False
        if age < stale_revival_seconds():
            return False
    if monitor is not None and _canonical_in_flight_for_log(log_path, monitor):
        return False
    repo_root = _repo_root_from_log(log_path)
    runner = lambda command: git_text(list(command), cwd=repo_root)  # noqa: E731
    state = classify_implement_attempt(
        repo_root=repo_root,
        log_path=log_path,
        integration_branch=_integration_branch_from_env(),
        command_runner=runner,
    )
    if _publish_recoverable_stale_base_implement(state):
        return False
    if state.redispatch:
        log_path.unlink(missing_ok=True)
        return True
    if state.in_flight:
        if force and monitor is None:
            return False
        log_path.unlink(missing_ok=True)
        return True
    return False


def _publish_recoverable_stale_base_implement(state: Any) -> bool:
    return (
        getattr(state, "redispatch", False)
        and getattr(state, "reason", "") == "stale_base"
        and str(getattr(state, "marker", "")).startswith("IMPLEMENT_DONE:")
        and str(getattr(state, "marker", "")).endswith(":ok")
    )


def force_revive_stuck_implements(repo_root: Path, *, monitor: Any | None = None) -> list[dict[str, str]]:
    """Manual trigger: clear every stuck implement log now (redispatchable or
    dead in_flight with no live worker), bypassing the stale_revival_seconds()
    age gate, so the next wakeup-runner tick re-dispatches them. Returns the
    revived targets with their pre-clear classification. A live codex (in the
    process inventory) is never cleared."""
    logs_dir = repo_root / ".refactor-loop" / "logs"
    revived: list[dict[str, str]] = []
    if not logs_dir.is_dir():
        return revived
    runner = lambda command: git_text(list(command), cwd=repo_root)  # noqa: E731
    for log_path in sorted(logs_dir.glob("implement-issue-*.log")):
        if not is_implement_log(log_path):
            continue
        before = classify_implement_attempt(
            repo_root=repo_root,
            log_path=log_path,
            integration_branch=_integration_branch_from_env(),
            command_runner=runner,
        )
        if _revive_stale_redispatchable_implement_log(log_path, monitor=monitor, force=True):
            revived.append({"log": log_path.name, "was": f"{before.status}:{before.reason}".strip(":")})
    return revived


def revive_implements_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="consensus-rnd-cli revive-implements",
        description="manual stale-revival: re-trigger stuck implement workers now (no age wait)",
    )
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args(argv)
    repo_root = resolve_repo_root(args.repo_root)
    monitor = import_concurrency_monitor(repo_root)
    revived = force_revive_stuck_implements(repo_root, monitor=monitor)
    print(json.dumps({"revived": revived, "count": len(revived)}, ensure_ascii=False, indent=2))
    return 0


def _terminal_blocked_harness_spawn_intent_ids(lines: list[str]) -> set[str]:
    ids: set[str] = set()
    prefix = "WAKEUP_RUNNER_BLOCKED:harness-spawn-intent:"
    for line in lines:
        if prefix not in line:
            continue
        tail = line.split(prefix, 1)[1].strip()
        for reason in TERMINAL_HARNESS_SPAWN_INTENT_BLOCKED_REASONS:
            suffix = f":{reason}"
            if tail.endswith(suffix):
                intent_id = tail[: -len(suffix)]
                if intent_id:
                    ids.add(intent_id)
                break
    return ids


def _open_managed_targets(items: list[GhItem]) -> set[tuple[str, int]]:
    return {(item.kind, item.number) for item in items if item.kind in {"PR", "issue"}}


def _open_managed_issue_numbers(items: list[GhItem]) -> set[int]:
    return {
        item.number
        for item in items
        if item.kind == "issue" and label_catalog.MANAGED in label_catalog.normalize_label_set(item.labels).canonical
    }


def _terminal_design_consensus_targets(items: list[GhItem]) -> set[tuple[str, int]]:
    return {
        (item.kind, item.number)
        for item in items
        if label_catalog.normalize_label_set(item.labels).phase in DESIGN_CONSENSUS_TERMINAL_PHASES
    }


def _suppress_harness_spawn_intent(
    intent: dict[str, Any],
    terminal_blocked_intent_ids: set[str],
    open_targets: set[tuple[str, int]],
    gh_items_loaded: bool,
    terminal_design_targets: set[tuple[str, int]] | None = None,
) -> bool:
    intent_id = str(intent.get("intent_id") or "")
    if intent_id in terminal_blocked_intent_ids:
        return True
    target = _harness_spawn_intent_target(intent)
    if gh_items_loaded and target is not None and target not in open_targets:
        return True
    if (
        gh_items_loaded
        and target is not None
        and target in (terminal_design_targets or set())
        and _is_design_consensus_solver_dispatch_intent(intent)
    ):
        return True
    return False


def _suppressed_consensus_implementation_spawn_intent(
    intent: dict[str, Any],
    repo_root: Path,
    gh_items: list[GhItem] | None,
    monitor: Any | None,
) -> str | None:
    issue = _consensus_implementation_spawn_intent_issue(intent)
    if issue is None:
        return None
    action = _consensus_implementation_action_for_intent(repo_root, issue)
    if not action:
        return "consensus_artifact_unavailable"
    reason = consensus_implementation_suppressed_reason(
        action,
        repo_root,
        gh_items,
        monitor,
        ignore_pending_implement_intent=True,
    )
    if reason in {"pending_implement_intent", None}:
        return None
    return reason


def _consensus_implementation_spawn_intent_issue(intent: dict[str, Any]) -> int | None:
    for field in ("intent_id", "action_id"):
        value = intent.get(field)
        if not isinstance(value, str):
            continue
        match = re.search(rf"(?:^|:){re.escape(IMPLEMENT_PENDING_INTENT_PREFIX)}([1-9][0-9]*)$", value)
        if match:
            return int(match.group(1))
    return None


def _consensus_implementation_action_for_intent(repo_root: Path, issue: int) -> dict[str, Any]:
    action = latest_consensus_implementation_for_issue(repo_root, issue)
    if action:
        action["target_kind"] = "issue"
        action["target_number"] = issue
        return action
    return {
        "target_kind": "issue",
        "target_number": issue,
        "iteration": str(issue),
        "cluster_id": f"issue-{issue}",
    }


def _is_design_consensus_solver_dispatch_intent(intent: dict[str, Any]) -> bool:
    if intent.get("controller_action") != "spawn_codex_harness_background":
        return False
    route = str(intent.get("route") or "")
    if route in {"design_consensus_issue_intake", "converge_to_next_solvers"}:
        return True
    task_id = str(intent.get("task_id") or "")
    return bool(re.fullmatch(r"phase9-issue[1-9][0-9]*-r[1-9][0-9]*-(minimal|structural|delete)", task_id))


def _harness_spawn_intent_target(intent: dict[str, Any]) -> tuple[str, int] | None:
    text_parts = []
    for field in ("task_id", "intent_id", "source", "route", "reason"):
        value = intent.get(field)
        if isinstance(value, str) and value:
            text_parts.append(value)
    text = " ".join(text_parts)
    for pattern, kind in HARNESS_SPAWN_TARGET_TEXT_PATTERNS:
        match = pattern.search(text)
        if match:
            return kind, int(match.group(1))
    return None


def _harness_spawn_intent_invalid_reason(intent: dict[str, Any]) -> str | None:
    forbidden = sorted(HARNESS_SPAWN_INTENT_FORBIDDEN_FIELDS.intersection(intent))
    if forbidden:
        return f"forbidden-fields:{','.join(forbidden)}"
    if intent.get("command") != "spawn-codex":
        return "command-not-spawn-codex"
    if intent.get("controller_action") != "spawn_codex_harness_background":
        return "controller-action-not-spawn-codex-background"
    for field in ("cd", "prompt", "log"):
        if not isinstance(intent.get(field), str) or not intent.get(field):
            return f"missing-{field}"
    try:
        stall = int(intent.get("stall", 5400))
    except (TypeError, ValueError):
        return "invalid-stall"
    if stall <= 0:
        return "invalid-stall"
    if intent.get("run_in_background_required") is not True:
        return "missing-background-requirement"
    if intent.get("no_lifecycle_authority") is not True:
        return "missing-no-lifecycle-authority"
    return None


def _invalid_harness_spawn_intent(reason: str, evidence: str, *, intent_id: str | None = None) -> dict[str, Any]:
    return {
        "priority": 2,
        "kind": "harness-spawn-intent-invalid",
        "item": intent_id,
        "phase": "bootstrap",
        "actor": "controller",
        "route": "harness-spawn-intent",
        "reason": reason,
        "evidence": evidence,
        "no_lifecycle_authority": True,
    }


def configured_floor() -> int:
    try:
        floor = int(os.environ.get("CODEX_FLOOR", "5"))
    except ValueError:
        floor = 5
    return max(2, floor)


def resolve_repo_root(arg_root: str | None) -> Path:
    ctx = LoopContext.load(repo_root=arg_root, env=os.environ, cwd=Path.cwd(), read_only=True)
    return ctx.repo_root


def import_concurrency_monitor(repo_root: Path) -> Any | None:
    os.environ["REPO_ROOT"] = str(repo_root)
    try:
        module_name = "codex_refactor_loop.monitors.concurrency"
        if module_name in sys.modules:
            return importlib.reload(sys.modules[module_name])
        return importlib.import_module(module_name)
    except Exception:
        return None


def build_concurrency_monitor(repo_root: Path, module: Any | None) -> Any | None:
    if module is None:
        return None
    try:
        return module.ConcurrencyMonitor(LoopContext.load(repo_root=repo_root))
    except Exception:
        return None


def canonical_actual_count(repo_root: Path, monitor: Any | None) -> int:
    if monitor is not None:
        try:
            return int(monitor.count_in_flight_codex())
        except Exception:
            pass
    env = os.environ.copy()
    env["REPO_ROOT"] = str(repo_root)
    script_path = Path(__file__).resolve().parents[1] / "consensus-rnd-cli"
    result = subprocess.run(
        [sys.executable, str(script_path), "concurrency", "--count-only"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        return 0


def canonical_expected_from_active_tasks(monitor: Any | None) -> tuple[int, list[dict[str, Any]]]:
    if monitor is None:
        return 0, []
    try:
        items = monitor.list_auto_loop_issues()
        expected, breakdown = monitor.compute_expected(items)
        return int(expected), list(breakdown)
    except Exception:
        return 0, []


def expected_from_open_items(items: list[GhItem]) -> tuple[int, list[dict[str, Any]]]:
    breakdown: list[dict[str, Any]] = []
    total = 0
    for item in ManagedWorkProjection(_projection_items(items)).effective_worker_items():
        labels = set(item.labels)
        if label_catalog.HUMAN_MAINTAINER_DECISION in label_catalog.normalize_label_set(labels).canonical:
            continue
        phase_label = item.phase or label_catalog.normalize_label_set(item.labels).phase or ""
        expected = label_catalog.phase_expected_workers(phase_label)
        if expected <= 0:
            continue
        breakdown.append({"id": f"#{item.number}", "kind": item.kind, "phase": phase_label, "expected": expected})
        total += expected
    return total, breakdown


def concurrency_plan(
    repo_root: Path,
    *,
    fixed_point: bool,
    gh_items: list[GhItem] | None = None,
    monitor: Any | None = None,
    concurrency_module: Any | None = None,
) -> dict[str, Any]:
    if concurrency_module is None:
        concurrency_module = import_concurrency_monitor(repo_root)
    if monitor is None:
        monitor = build_concurrency_monitor(repo_root, concurrency_module)
    actual = canonical_actual_count(repo_root, monitor)
    expected, breakdown = expected_from_open_items(gh_items or [])
    if expected == 0:
        expected, breakdown = canonical_expected_from_active_tasks(monitor)
    floor = configured_floor()
    target = max(floor, expected)
    deficit = max(0, target - actual)
    hard_gate_active = deficit > 0
    hard_gate_line = f"HARD_GATE:dispatch_required={deficit}" if hard_gate_active else None
    boundary = None
    if deficit > 0 and expected == 0 and concurrency_module is not None:
        try:
            boundary = concurrency_module.single_active_audit_boundary(repo_root, monitor, gh_items or [], None)
        except Exception:
            boundary = None
    if boundary is not None:
        hard_gate_active = False
        hard_gate_line = None
    return {
        "actual": actual,
        "expected_from_active_tasks": expected,
        "expected_breakdown": breakdown,
        "floor": floor,
        "target": target,
        "deficit": deficit,
        "fixed_point": fixed_point,
        "hard_gate": {
            "active": hard_gate_active,
            "dispatch_required": deficit if hard_gate_active else 0,
            "line": hard_gate_line,
            "semantics": (
                "controller must dispatch this many actionable managed issue/PR tasks or legal fallback issue production through audit before ending the wakeup"
                if hard_gate_active
                else None
            ),
            "reason": "single_active_audit_in_flight" if boundary is not None else None,
            "blocked_deficit": deficit if boundary is not None else 0,
            "boundary_task_id": boundary.task_id if boundary is not None else None,
        },
    }


def daemon_health(repo_root: Path, now: float | None = None) -> dict[str, Any]:
    if now is None:
        now = time.time()
    heartbeat_dir = repo_root / ".refactor-loop" / "heartbeats"
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    if heartbeat_dir.exists():
        for path in sorted(heartbeat_dir.glob("*.ts")):
            name = path.stem
            seen.add(name)
            try:
                raw = path.read_text(encoding="utf-8").strip()
                timestamp = int(raw)
                age = max(0, int(now - timestamp))
                status = "stale" if age > STALE_SECONDS else "fresh"
                items.append({"name": name, "status": status, "age_seconds": age})
            except (OSError, ValueError):
                items.append({"name": name, "status": "stale", "age_seconds": None})
    for name in restart_managed_daemon_names():
        if name not in seen:
            items.append({"name": name, "status": "missing", "age_seconds": None})
    needs_restart = any(item["status"] in {"stale", "missing"} for item in items)
    return {
        "stale_seconds": STALE_SECONDS,
        "items": sorted(items, key=lambda item: item["name"]),
        "ok": not needs_restart,
        "recommendation": "consensus-rnd-cli restart-daemons" if needs_restart else None,
    }


def is_clean_exit(log_path: Path) -> bool:
    return log_has_clean_exit(log_path)


def marker_from_completed_log(log_path: Path) -> str | None:
    _shared_reader_uses_done_prefix_fullmatch = "DONE_PREFIX_RE.fullmatch"
    marker = read_worker_terminal_marker(log_path)
    return marker.marker if marker.source == "log" else None


def completed_marker_actions(
    repo_root: Path,
    ctx: LoopContext | None = None,
    open_targets: set[tuple[str, int]] | None = None,
    gh_items: list[GhItem] | None = None,
    monitor: Any | None = None,
) -> list[dict[str, Any]]:
    logs_dir = repo_root / ".refactor-loop" / "logs"
    if not logs_dir.exists():
        return []
    if ctx is None:
        ctx = LoopContext.load(repo_root=repo_root, env=_repo_local_context_env(repo_root, os.environ), cwd=repo_root, read_only=True)
    candidates: list[CompletedMarkerCandidate] = []
    for log_path in sorted(logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True):
        marker = read_worker_terminal_marker(log_path).marker
        if not marker:
            continue
        if marker.startswith("AUDIT_DONE:none:0"):
            continue
        target_text = f"{log_path.name} {marker}"
        item = infer_item_from_text(target_text)
        action = {
            "priority": 3,
            "kind": "completed-marker",
            "action_id": f"completed-marker:{log_path.name}:{marker}",
            "item": item,
            "phase": phase_from_marker(marker),
            "actor": actor_from_marker(marker),
            "marker": marker,
            "evidence": str(log_path.relative_to(repo_root)),
            "source_artifact": str(log_path.relative_to(repo_root)),
            "source_marker": marker,
            "target_kind": _target_kind_from_item(item),
            "target_number": _target_number_from_item(item),
            "target": _target_from_item(item),
            "preconditions": ["active_controller_owner", "clean_exit_source_marker", "live_open_target_if_present"],
            "controller_action": controller_action_from_marker(marker),
            "runner_authority": RUNNER_AUTHORITY,
            "no_generic_command": True,
        }
        if action["controller_action"] == "publish_implementation_output":
            _attach_implementation_pr_artifacts(repo_root, action)
        target = _action_target_key(action)
        if (
            open_targets is not None
            and target is not None
            and target not in open_targets
            and not marker.startswith("META_JUDGE_DONE:consensus")
            and controller_action_from_marker(marker) != "close_managed_item_from_drop_marker"
        ):
            continue
        route = route_from_marker(marker)
        if route:
            action["route"] = route
        if marker.startswith("REVIEW_DONE"):
            head_sha = _review_done_action_head_sha(repo_root, log_path, marker, gh_items)
            if head_sha:
                action["head_sha"] = head_sha
        if marker.startswith("META_JUDGE_DONE:consensus"):
            consensus_fields = consensus_implementation_fields(repo_root, log_path, item)
            if consensus_fields:
                action.update(consensus_fields)
                action["preconditions"] = [
                    *action["preconditions"],
                    "durable_consensus_artifact",
                    "consensus_implementation_ready",
                ]
                _apply_consensus_implementation_readiness(action, repo_root, gh_items, monitor)
            else:
                action["status_only"] = True
                action["no_lifecycle_authority"] = True
                action.pop("runner_authority", None)
                action.pop("no_generic_command", None)
        if marker.startswith("FIX_DONE"):
            _apply_fix_done_review_thread_gate(repo_root, ctx, action)
        candidates.append(
            CompletedMarkerCandidate(
                log_path=log_path,
                marker=marker,
                action=action,
                mtime=_marker_mtime(log_path),
            )
        )
    return [candidate.action for candidate in _latest_completed_marker_candidates(candidates)]


def _repo_local_context_env(repo_root: Path, env: Mapping[str, str]) -> dict[str, str]:
    context_env = dict(env)
    raw = context_env.get("CONSENSUS_RND_HOST_ENV")
    if raw is None:
        return context_env
    root = repo_root.resolve()
    if _host_env_path_is_repo_local(root, raw):
        return context_env
    local_default = root / ".config" / "consensus-rnd" / "host.env"
    if local_default.is_file():
        context_env["CONSENSUS_RND_HOST_ENV"] = ".config/consensus-rnd/host.env"
    else:
        context_env.pop("CONSENSUS_RND_HOST_ENV", None)
    return context_env


def _host_env_path_is_repo_local(repo_root: Path, raw_value: str) -> bool:
    raw = raw_value.strip()
    if not raw:
        return False
    candidate = Path(raw).expanduser()
    if any(part == ".." for part in candidate.parts):
        return False
    path = (candidate if candidate.is_absolute() else repo_root / candidate).resolve()
    try:
        path.relative_to(repo_root)
    except ValueError:
        return False
    return path.is_file()


def _marker_mtime(log_path: Path) -> float:
    try:
        return log_path.stat().st_mtime
    except OSError:
        return 0.0


def _latest_completed_marker_candidates(candidates: list[CompletedMarkerCandidate]) -> list[CompletedMarkerCandidate]:
    latest_keys: dict[tuple[Any, ...], tuple[int, float]] = {}
    keyed: list[tuple[CompletedMarkerCandidate, tuple[Any, ...], tuple[int, float]] | None] = []
    for candidate in candidates:
        key = _completed_marker_latest_key(candidate)
        if key is None:
            keyed.append(None)
            continue
        rank = _completed_marker_latest_rank(candidate)
        keyed.append((candidate, key, rank))
        if key not in latest_keys or rank > latest_keys[key]:
            latest_keys[key] = rank

    kept: list[CompletedMarkerCandidate] = []
    for candidate, record in zip(candidates, keyed, strict=True):
        if record is None:
            kept.append(candidate)
            continue
        _, key, rank = record
        if rank == latest_keys.get(key):
            kept.append(candidate)
    return kept


def _completed_marker_latest_key(candidate: CompletedMarkerCandidate) -> tuple[Any, ...] | None:
    design_key = _design_consensus_marker_issue_key(candidate)
    if design_key is not None:
        return design_key
    action = candidate.action
    target = _action_target_key(action)
    if target is not None:
        return ("target", *target)
    return None


def _design_consensus_marker_issue_key(candidate: CompletedMarkerCandidate) -> tuple[str, str, int] | None:
    if phase_from_marker(candidate.marker) != "design-consensus":
        return None
    match = DESIGN_CONSENSUS_LOG_RE.fullmatch(candidate.log_path.name)
    if match is None:
        return None
    issue = int(match.group(1))
    target = _action_target_key(candidate.action)
    if target is not None and target != ("issue", issue):
        return None
    return ("design-consensus", "issue", issue)


def _completed_marker_latest_rank(candidate: CompletedMarkerCandidate) -> tuple[int, float]:
    round_no = _design_consensus_marker_round(candidate)
    if round_no is not None:
        return (round_no, candidate.mtime)
    return (0, candidate.mtime)


def _design_consensus_marker_round(candidate: CompletedMarkerCandidate) -> int | None:
    if phase_from_marker(candidate.marker) != "design-consensus":
        return None
    match = DESIGN_CONSENSUS_LOG_RE.fullmatch(candidate.log_path.name)
    return int(match.group(2)) if match else None


def _action_target_key(action: dict[str, Any]) -> tuple[str, int] | None:
    kind = action.get("target_kind")
    number = action.get("target_number")
    if kind in {"PR", "issue"} and isinstance(number, int):
        return kind, number
    return None


def _attach_implementation_pr_artifacts(repo_root: Path, action: dict[str, Any]) -> None:
    target = _action_target_key(action)
    if target is None or target[0] != "issue":
        return
    cluster_id = _implementation_cluster_id(action, target[1])
    title = repo_root / ".refactor-loop" / "runs" / f"implementation-pr-{cluster_id}-title.txt"
    body = repo_root / ".refactor-loop" / "runs" / f"implementation-pr-{cluster_id}-body.md"
    action["title_file"] = title.relative_to(repo_root).as_posix()
    action["body_file"] = body.relative_to(repo_root).as_posix()


def _apply_fix_done_review_thread_gate(repo_root: Path, ctx: LoopContext, action: dict[str, Any]) -> None:
    pr_number = action.get("target_number")
    if action.get("target_kind") != "PR" or not isinstance(pr_number, int):
        return
    evidence = _review_thread_completion_evidence(repo_root, ctx, pr_number)
    try:
        validate_review_thread_completion(evidence)
    except ValueError as exc:
        action["status_only"] = True
        action["no_lifecycle_authority"] = True
        action["route"] = "review-thread-completion-gate"
        action["blocked_reason"] = f"review_thread_completion_incomplete:{exc}"
        action["preconditions"] = [
            *action.get("preconditions", []),
            "review_thread_completion_evidence",
        ]
        action.pop("runner_authority", None)
        action.pop("no_generic_command", None)
        action.pop("controller_action", None)
    else:
        action["preconditions"] = [
            *action.get("preconditions", []),
            "review_thread_completion_evidence",
        ]


def _review_thread_completion_evidence(repo_root: Path, ctx: LoopContext, pr_number: int) -> ReviewThreadCompletionEvidence:
    artifact = repo_root / ".refactor-loop" / "state" / "review-thread-completion" / f"pr{pr_number}.json"
    data: dict[str, Any] = {}
    if artifact.is_file():
        try:
            loaded = json.loads(artifact.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            data = loaded
    review_thread_driven = bool(data.get("review_thread_driven"))
    thread_id = str(data.get("thread_id") or "")
    raw_escalation_evidence = str(data.get("escalation_evidence") or "")
    escalation_evidence = (
        raw_escalation_evidence
        if _has_clean_escalation_marker_source(repo_root, raw_escalation_evidence)
        else ""
    )
    live_original_thread_resolved = True
    if review_thread_driven and not escalation_evidence.strip():
        live_original_thread_resolved = _original_review_thread_is_resolved(ctx, pr_number, thread_id)
    return ReviewThreadCompletionEvidence(
        review_thread_driven=review_thread_driven,
        thread_id=thread_id,
        replied=bool(data.get("replied")),
        resolved=bool(data.get("resolved")) and live_original_thread_resolved,
        escalation_evidence=escalation_evidence,
    )


def _has_clean_escalation_marker_source(repo_root: Path, escalation_evidence: str) -> bool:
    marker = escalation_evidence.strip()
    if not marker.startswith("META_RESOLVED:escalate-human:"):
        return False
    logs_dir = repo_root / ".refactor-loop" / "logs"
    if not logs_dir.exists():
        return False
    for log_path in sorted(logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True):
        if marker_from_completed_log(log_path) == marker:
            return True
    return False


def _original_review_thread_is_resolved(ctx: LoopContext, pr_number: int, thread_id: str) -> bool:
    if not thread_id.strip():
        return False
    slug = str(ctx.host_env.get("GH_REPO_SLUG") or "").strip()
    owner, _, repo = slug.partition("/")
    if not owner or not repo:
        return False
    query = (
        "query($owner:String!,$repo:String!,$number:Int!,$after:String){ "
        "repository(owner:$owner,name:$repo){ pullRequest(number:$number){ "
        "reviewThreads(first:100, after:$after){ "
        "nodes{ id isResolved } pageInfo{ hasNextPage endCursor } "
        "} } } }"
    )
    after = ""
    while True:
        cmd = [
            "gh",
            "api",
            "graphql",
            "-f",
            f"owner={owner}",
            "-f",
            f"repo={repo}",
            "-F",
            f"number={pr_number}",
            "-f",
            f"query={query}",
        ]
        if after:
            cmd.extend(["-f", f"after={after}"])
        payload = run_json(cmd, cwd=ctx.repo_root)
        repository = ((payload or {}).get("data") or {}).get("repository")
        if not isinstance(repository, dict):
            return False
        pull_request = repository.get("pullRequest")
        if not isinstance(pull_request, dict):
            return False
        review_threads = pull_request.get("reviewThreads")
        if not isinstance(review_threads, dict):
            return False
        nodes = review_threads.get("nodes")
        if not isinstance(nodes, list):
            return False
        for node in nodes:
            if not isinstance(node, dict):
                return False
            if node.get("id") != thread_id:
                continue
            is_resolved = node.get("isResolved")
            return is_resolved if isinstance(is_resolved, bool) else False
        page_info = review_threads.get("pageInfo")
        if not isinstance(page_info, dict):
            return False
        has_next_page = page_info.get("hasNextPage")
        if not isinstance(has_next_page, bool):
            return False
        if not has_next_page:
            return False
        end_cursor = page_info.get("endCursor")
        if not isinstance(end_cursor, str) or not end_cursor:
            return False
        after = end_cursor


def consensus_implementation_fields(repo_root: Path, log_path: Path, item: str | None) -> dict[str, Any]:
    log_match = CONSENSUS_JUDGE_LOG_RE.fullmatch(log_path.name)
    if log_match is None:
        return {}
    issue, round_no = log_match.groups()
    if item and _target_number_from_item(item) != int(issue):
        return {}
    artifact = repo_root / ".refactor-loop" / "runs" / f"phase9-issue{issue}-r{round_no}-judge.md"
    if not artifact.is_file():
        return {}
    artifact_match = CONSENSUS_JUDGE_ARTIFACT_RE.fullmatch(artifact.name)
    if artifact_match is None or artifact_match.groups() != log_match.groups():
        return {}
    return _consensus_projection_from_artifact(repo_root, artifact, int(issue), int(round_no))


def _consensus_artifact_has_marker(path: Path) -> bool:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    return any(line.startswith("META_JUDGE_DONE:consensus") for line in lines[-10:])


def _consensus_artifact_facts(repo_root: Path, path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    rel_path = path.relative_to(repo_root).as_posix()
    if not _frontmatter_is_consensus(text):
        return {}
    if_consensus = _extract_section_text(text, "If consensus")
    if not if_consensus:
        return {}
    owner = _extract_implementation_owner(if_consensus)
    if owner is None:
        return {}
    cluster_id, design_decision_path = owner
    if design_decision_path != rel_path:
        return {}
    scope_paths = _extract_structured_consensus_field(if_consensus, "scope_paths")
    old_pattern = _extract_structured_consensus_field(if_consensus, "old_pattern")
    new_principle = _extract_structured_consensus_field(if_consensus, "new_principle")
    verification_hints = _extract_structured_consensus_field(if_consensus, "verification_hints")
    return {
        "cluster_id": cluster_id,
        "design_decision_path": design_decision_path,
        "scope_paths": scope_paths,
        "old_pattern": old_pattern,
        "new_principle": new_principle,
        "verification_hints": verification_hints,
    }


def _consensus_facts_complete(facts: dict[str, str]) -> bool:
    return all(
        str(facts.get(field) or "").strip()
        for field in ("cluster_id", "design_decision_path", "scope_paths", "old_pattern", "new_principle")
    )


def _extract_section_text(text: str, heading: str) -> str:
    pattern = re.compile(rf"(?ims)^##\s+{re.escape(heading)}\s*\n(.+?)(?=^##\s+|\Z)")
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _frontmatter_is_consensus(text: str) -> bool:
    if not text.startswith("---\n"):
        return False
    end = text.find("\n---", 4)
    if end < 0:
        return False
    values: dict[str, str] = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if sep:
            values[key.strip()] = value.strip()
    return values.get("decision") == "consensus" or values.get("verdict") == "consensus"


def _extract_implementation_owner(section: str) -> tuple[str, str] | None:
    match = re.search(
        r"(?im)^\s*-\s*Implementation owner:\s*dispatch implement codex with "
        r"cluster_id=([^,\s]+),\s*design_decision_path=([^\s]+)\s*$",
        section,
    )
    if not match:
        return None
    return match.group(1), match.group(2)


def _extract_structured_consensus_field(section: str, field: str) -> str:
    field_names = {"scope_paths", "old_pattern", "new_principle", "verification_hints"}
    lines = section.splitlines()
    start_re = re.compile(rf"^\s*(?:-\s*)?{re.escape(field)}\s*:\s*(.*)$")
    other_re = re.compile(
        r"^\s*(?:-\s*)?(?:" + "|".join(re.escape(name) for name in sorted(field_names - {field})) + r")\s*:\s*"
    )
    collected: list[str] = []
    collecting = False
    for line in lines:
        if not collecting:
            match = start_re.match(line)
            if not match:
                continue
            remainder = match.group(1).strip()
            if remainder:
                collected.append(remainder)
            collecting = True
            continue
        if other_re.match(line) or re.match(r"^\s*-\s*(?:Implementation owner|Add `|For large-issue)\b", line):
            break
        if re.match(r"^\s*-\s+[A-Za-z][A-Za-z0-9 _/-]*:", line):
            break
        collected.append(line.rstrip())
    return "\n".join(line.strip() for line in collected if line.strip())


def _consensus_projection_from_artifact(repo_root: Path, artifact: Path, issue: int, round_no: int) -> dict[str, Any]:
    match = CONSENSUS_JUDGE_ARTIFACT_RE.fullmatch(artifact.name)
    if match is None or int(match.group(1)) != issue or int(match.group(2)) != round_no:
        return {}
    if not _consensus_artifact_has_marker(artifact):
        return {}
    facts = _consensus_artifact_facts(repo_root, artifact)
    if not _consensus_facts_complete(facts):
        return {}
    rel = artifact.relative_to(repo_root).as_posix()
    if facts.get("design_decision_path") != rel:
        return {}
    return {
        "consensus_artifact": rel,
        "design_decision_path": rel,
        "consensus_issue": issue,
        "consensus_round": round_no,
        "cluster_id": facts["cluster_id"],
        "iteration": str(issue),
        "source_ref": f"gh-issue-{issue}",
        "scope_paths": facts["scope_paths"],
        "old_pattern": facts["old_pattern"],
        "new_principle": facts["new_principle"],
        "verification_hints": facts.get("verification_hints", ""),
    }


def infer_item_from_text(text: str) -> str | None:
    pr = re.search(r"\bpr[-_#]?(\d+)\b|PR #(\d+)", text, flags=re.IGNORECASE)
    if pr:
        return f"PR #{next(group for group in pr.groups() if group)}"
    issue = re.search(r"\bissue[-_#]?(\d+)\b|#(\d+)", text, flags=re.IGNORECASE)
    if issue:
        return f"issue #{next(group for group in issue.groups() if group)}"
    return None


def _reviewed_head_sha_from_log(log_path: Path) -> str:
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = REVIEW_HEAD_RE.search(text)
    return match.group(1) if match else ""


def _reviewed_head_sha_from_file(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = REVIEW_HEAD_RE.search(text)
    return match.group(1) if match else ""


def _review_done_action_head_sha(repo_root: Path, log_path: Path, marker: str, gh_items: list[GhItem] | None) -> str:
    match = re.match(r"^REVIEW_DONE:([1-9][0-9]*):([A-Za-z][A-Za-z0-9_-]*):(approve|comment|reject)(?::real)?$", marker)
    if match is None:
        return _reviewed_head_sha_from_log(log_path)
    pr_number = int(match.group(1))
    role = match.group(2)
    live_head = _gh_item_head_sha(gh_items, pr_number)
    heads = latest_reviewer_heads(repo_root, pr_number)
    role_head = heads.get(role, "")
    if role_head:
        return role_head
    if live_head and all(heads.get(required, "") == live_head for required in REQUIRED_REVIEW_ROLES):
        return live_head
    return _reviewed_head_sha_from_log(log_path)


def _gh_item_head_sha(gh_items: list[GhItem] | None, pr_number: int) -> str:
    if gh_items is None:
        return ""
    for item in gh_items:
        if item.kind == "PR" and item.number == pr_number:
            return item.head_sha
    return ""


def _reviewer_log_has_exit_zero(path: Path) -> bool:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    return any(line.strip() == "EXIT=0" for line in lines)


def _reviewer_log_has_valid_marker(path: Path, pr_number: int, role: str) -> bool:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    prefix = f"REVIEW_DONE:{pr_number}:{role}:"
    return sum(1 for line in lines if line.strip().startswith(prefix)) == 1


def latest_reviewer_heads(repo_root: Path, pr_number: int) -> dict[str, str]:
    by_role: dict[str, tuple[int, str]] = {}
    artifact_keys: set[tuple[str, int]] = set()
    runs_dir = repo_root / ".refactor-loop" / "runs"
    logs_dir = repo_root / ".refactor-loop" / "logs"
    prompts_dir = repo_root / ".refactor-loop" / "prompts"
    for path in sorted(runs_dir.glob(f"review-pr{pr_number}-*-r*.md")):
        match = REVIEW_ARTIFACT_RE.match(path.name)
        if not match or int(match.group(1)) != pr_number:
            continue
        role = match.group(2)
        round_number = int(match.group(3))
        artifact_keys.add((role, round_number))
        log_path = logs_dir / f"review-pr{pr_number}-{role}-r{round_number}.log"
        if not _reviewer_log_has_exit_zero(log_path):
            continue
        head_sha = _reviewed_head_sha_from_file(path) or _reviewed_head_sha_from_file(prompts_dir / path.name) or _reviewed_head_sha_from_file(log_path)
        if head_sha:
            existing = by_role.get(role)
            if existing is None or round_number >= existing[0]:
                by_role[role] = (round_number, head_sha)
    for path in sorted(logs_dir.glob(f"review-pr{pr_number}-*-r*.log")):
        match = REVIEW_LOG_RE.match(path.name)
        if not match or int(match.group(1)) != pr_number:
            continue
        role = match.group(2)
        round_number = int(match.group(3))
        if (role, round_number) in artifact_keys:
            continue
        if not _reviewer_log_has_exit_zero(path) or not _reviewer_log_has_valid_marker(path, pr_number, role):
            continue
        prompt_path = prompts_dir / path.with_suffix(".md").name
        head_sha = _reviewed_head_sha_from_file(path) or _reviewed_head_sha_from_file(prompt_path)
        if head_sha:
            existing = by_role.get(role)
            if existing is None or round_number >= existing[0]:
                by_role[role] = (round_number, head_sha)
    return {role: head_sha for role, (_round_number, head_sha) in by_role.items()}


def pending_review_spawn_exists(repo_root: Path, pr_number: int) -> bool:
    pending_path = repo_root / ".refactor-loop" / ".controller-pending-events.log"
    try:
        lines = pending_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    prefix = f"dispatch-reviewers:{pr_number}:"
    for line in lines:
        if " HARNESS_SPAWN_INTENT " not in line:
            continue
        try:
            intent = json.loads(line.split(" HARNESS_SPAWN_INTENT ", 1)[1])
        except json.JSONDecodeError:
            continue
        if not isinstance(intent, dict):
            continue
        intent_id = str(intent.get("intent_id") or "")
        if not intent_id.startswith(prefix):
            continue
        log_value = str(intent.get("log") or "")
        log_path = Path(log_value)
        if not log_path.is_absolute():
            log_path = repo_root / log_path
        if not _harness_spawn_intent_log_suppresses_retry(log_path):
            return True
    return False


def review_evidence_redispatch_actions(repo_root: Path, gh_items: list[GhItem]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for item in gh_items:
        if item.kind != "PR":
            continue
        projection = label_catalog.normalize_label_set(item.labels)
        if projection.phase not in {label_catalog.PHASE_REVIEWING, label_catalog.PHASE_PR_OPEN}:
            continue
        if not item.head_sha or pending_review_spawn_exists(repo_root, item.number):
            continue
        heads = latest_reviewer_heads(repo_root, item.number)
        stale_roles = [role for role in REQUIRED_REVIEW_ROLES if heads.get(role, "") != item.head_sha]
        if not stale_roles:
            continue
        actions.append(
            {
                "priority": 2,
                "kind": "review-evidence-redispatch",
                "action_id": f"review-evidence-redispatch:{item.number}:{item.head_sha}",
                "item": item.item,
                "phase": "review-gate",
                "actor": "controller",
                "route": "dispatch-reviewers",
                "controller_action": "dispatch_reviewers",
                "target_kind": "PR",
                "target_number": item.number,
                "target": {"kind": "PR", "number": item.number},
                "head_sha": item.head_sha,
                "stale_review_roles": stale_roles,
                "preconditions": ["active_controller_owner", "live_open_target_if_present", "missing_or_stale_reviewer_head_evidence"],
                "runner_authority": RUNNER_AUTHORITY,
                "no_generic_command": True,
            }
        )
    return actions


def phase_from_marker(marker: str) -> str:
    if marker.startswith("IMPLEMENT_DONE"):
        return "publish"
    if marker.startswith("REVIEW_DONE"):
        return "review-gate"
    if marker.startswith("FIX_DONE"):
        return "review-gate"
    if marker.startswith("TEST_ADD_DONE"):
        return "ci-watch"
    if marker.startswith("AUDIT_DONE"):
        return "work-intake"
    if marker.startswith(("SOLVER_DONE", "META_JUDGE_DONE", "META_RESOLVED")):
        return "design-consensus"
    if marker.startswith("VERIFY_DONE"):
        return "publish"
    return "work-intake"


def route_from_marker(marker: str) -> str | None:
    if marker.startswith("IMPLEMENT_DONE"):
        return "publish-or-review-gate"
    if not marker.startswith(
        (
            "AUDIT_DONE",
            "SOLVER_DONE",
            "META_JUDGE_DONE",
            "META_RESOLVED",
            "IMPLEMENT_DONE",
            "VERIFY_DONE",
            "REVIEW_DONE",
            "FIX_DONE",
            "TEST_ADD_DONE",
        )
    ):
        return "marker-route"
    return None


def actor_from_marker(marker: str) -> str:
    if marker.startswith("IMPLEMENT_DONE"):
        return "controller"
    if marker.startswith("REVIEW_DONE"):
        return "controller-or-fix-codex"
    if marker.startswith("FIX_DONE"):
        return "reviewer-codex"
    if marker.startswith("TEST_ADD_DONE"):
        return "controller"
    if marker.startswith("AUDIT_DONE"):
        return "controller"
    if marker.startswith(("SOLVER_DONE", "META_JUDGE_DONE", "META_RESOLVED")):
        return "design-consensus-router-or-controller"
    if marker.startswith("VERIFY_DONE"):
        return "controller"
    return "controller"


def pending_bootstrap_actions(ctx: LoopContext, health: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    repo_root = ctx.repo_root
    if not ctx.host_env:
        actions.append(
            {
                "priority": 1,
                "kind": "bootstrap",
                "item": None,
                "phase": "bootstrap",
                "actor": "controller",
                "reason": "missing host-owned consensus-rnd host.env; set CONSENSUS_RND_HOST_ENV to the runtime injection file",
            }
        )
    if health["recommendation"]:
        stale_names = [item["name"] for item in health["items"] if item["status"] in {"stale", "missing"}]
        actions.append(
            {
                "priority": 1,
                "kind": "bootstrap",
                "item": None,
                "phase": "bootstrap",
                "actor": "controller",
                "reason": "daemon heartbeat stale-or-missing",
                "route": "daemon-health",
                "suggested_command": "python3 <skill-root>/scripts/consensus-rnd-cli restart-daemons",
                "daemons": stale_names,
            }
        )
    pending_events = repo_root / ".refactor-loop" / ".controller-pending-events.log"
    concurrency_alert = repo_root / ".refactor-loop" / ".concurrency-alert.log"
    if not pending_events.exists() and not concurrency_alert.exists():
        actions.append(
            {
                "priority": 1,
                "kind": "wake-source",
                "item": None,
                "phase": "bootstrap",
                "actor": "controller",
                "route": "wake-source",
                "reason": "missing daemon-event surfaces; confirm Monitor bridge",
            }
        )
    return actions


def maintainer_comment_actions(repo_root: Path, gh_items: list[GhItem]) -> list[dict[str, Any]]:
    pending_path = repo_root / ".refactor-loop" / ".controller-pending-events.log"
    actions: list[dict[str, Any]] = []
    if pending_path.exists():
        try:
            lines = pending_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            lines = []
        for line in lines[-20:]:
            lowered = line.lower()
            if "maintainer" in lowered and "comment" in lowered:
                actions.append(
                    {
                        "priority": 2,
                        "kind": "maintainer-comment",
                        "item": infer_item_from_text(line),
                        "phase": "design-intake",
                        "actor": "controller",
                        "evidence": line,
                        "status_only": True,
                        "no_lifecycle_authority": True,
                    }
                )
    for item in gh_items:
        labels = set(item.labels)
        if label_catalog.HUMAN_MAINTAINER_DECISION in label_catalog.normalize_label_set(labels).canonical:
            actions.append(
                {
                    "priority": 2,
                    "kind": "maintainer-comment",
                    "item": item.item,
                    "phase": phase_from_labels(item.labels),
                    "actor": "controller",
                    "evidence": "human label present; sweep latest non-AI comments",
                    "status_only": True,
                    "no_lifecycle_authority": True,
                }
            )
    return actions


def no_gap_actions(repo_root: Path) -> list[dict[str, Any]]:
    alert_path = repo_root / ".refactor-loop" / ".concurrency-alert.log"
    if not alert_path.exists():
        return []
    try:
        lines = alert_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    actions: list[dict[str, Any]] = []
    for line in lines[-20:]:
        if "no-gap-violation" not in line:
            continue
        actions.append(
            {
                "priority": 5,
                "kind": "no-gap-violation",
                "action_id": f"no-gap-violation:{line}",
                "item": infer_item_from_text(line),
                "phase": "work-intake",
                "actor": "controller",
                "route": "no-gap-repair",
                "evidence": line,
                "source_artifact": ".refactor-loop/.concurrency-alert.log",
                "source_marker": line,
                "target_kind": _target_kind_from_item(infer_item_from_text(line)),
                "target_number": _target_number_from_item(infer_item_from_text(line)),
                "target": _target_from_item(infer_item_from_text(line)),
                "preconditions": ["active_controller_owner", "source_artifact_contains_evidence"],
                "status_only": True,
                "no_lifecycle_authority": True,
            }
        )
    return actions


def gh_args(slug: str | None) -> list[str]:
    return ["--repo", slug] if slug else []


def github_repo_slug() -> str | None:
    slug = os.environ.get("GH_REPO_SLUG")
    if slug:
        return slug
    repo = os.environ.get("GH_REPO")
    if repo and "/" in repo:
        return repo
    owner = os.environ.get("GH_OWNER")
    name = os.environ.get("GH_REPO_NAME") or repo
    if owner and name:
        return f"{owner}/{name}"
    return None


def load_github_items(repo_root: Path) -> list[GhItem]:
    items, _loaded_ok = load_github_items_with_status(repo_root)
    return items


def load_github_items_with_status(repo_root: Path) -> tuple[list[GhItem], bool]:
    ctx = LoopContext.load(repo_root=repo_root, env=os.environ, cwd=repo_root, read_only=True)
    snapshot = load_open_managed_work_snapshot(ctx)
    items: list[GhItem] = []
    if not snapshot.loaded_ok:
        print(
            snapshot.unavailable_diagnostic("wakeup-plan.load-github-items", target_context="projection-open-managed"),
            file=sys.stderr,
            flush=True,
        )
        return items, False
    for raw in snapshot.items:
        number = raw.number
        labels = tuple(str(label) for label in raw.labels if str(label))
        kind = raw.kind
        items.append(
            GhItem(
                kind=kind,
                number=number,
                title=raw.title,
                labels=labels,
                head_ref=raw.head_ref if kind == "PR" else None,
                head_sha=raw.head_sha if kind == "PR" else "",
                body=raw.body if kind == "PR" else "",
                updated_at=raw.updated_at,
            )
        )
    return items, True


def git_text(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def parse_worktree_branches(porcelain: str) -> dict[str, Path]:
    worktrees: dict[str, Path] = {}
    current: Path | None = None
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            current = Path(line.removeprefix("worktree "))
            continue
        if not line.startswith("branch ") or current is None:
            continue
        branch = line.removeprefix("branch refs/heads/")
        if branch and not branch.startswith("refs/"):
            worktrees[branch] = current
    return worktrees


def safe_head_ref(value: str | None) -> str | None:
    if not value or value.startswith("-"):
        return None
    if any(ch.isspace() or ord(ch) < 32 for ch in value):
        return None
    return value


def unpushed_worker_output_actions(repo_root: Path, gh_items: list[GhItem]) -> list[dict[str, Any]]:
    prs = [item for item in gh_items if item.kind == "PR" and safe_head_ref(item.head_ref)]
    if not prs:
        return []
    fetch = git_text(["git", "-C", str(repo_root), "fetch", "origin", "--quiet"], cwd=repo_root)
    if fetch.returncode != 0:
        return []
    listed = git_text(["git", "-C", str(repo_root), "worktree", "list", "--porcelain"], cwd=repo_root)
    if listed.returncode != 0:
        return []
    worktrees = parse_worktree_branches(listed.stdout)
    actions: list[dict[str, Any]] = []
    for item in prs:
        head_ref = safe_head_ref(item.head_ref)
        if not head_ref:
            continue
        worktree = worktrees.get(head_ref)
        if worktree is None:
            continue
        local = git_text(["git", "-C", str(worktree), "rev-parse", "--verify", "HEAD"], cwd=repo_root)
        remote_ref = f"refs/remotes/origin/{head_ref}"
        remote = git_text(["git", "-C", str(worktree), "rev-parse", "--verify", remote_ref], cwd=repo_root)
        count = git_text(["git", "-C", str(worktree), "rev-list", "--count", f"{remote_ref}..HEAD"], cwd=repo_root)
        if local.returncode != 0 or remote.returncode != 0 or count.returncode != 0:
            continue
        try:
            ahead_count = int(count.stdout.strip())
        except ValueError:
            continue
        if ahead_count <= 0:
            continue
        actions.append(
            {
                "priority": 3,
                "kind": "unpushed-worker-output",
                "action_id": f"unpushed-worker-output:{item.number}:{local.stdout.strip()}",
                "item": item.item,
                "phase": "publish",
                "route": "controller-push-required",
                "actor": "controller",
                "head_ref": head_ref,
                "worktree": str(worktree),
                "ahead_count": ahead_count,
                "local_head": local.stdout.strip(),
                "remote_head": remote.stdout.strip(),
                "line": f"UNPUSHED_WORKER_OUTPUT:{item.number}:{ahead_count}",
                "controller_action": "safe_push",
                "no_lifecycle_authority": True,
                "source_artifact": str(worktree),
                "source_marker": f"UNPUSHED_WORKER_OUTPUT:{item.number}:{ahead_count}",
                "target_kind": "PR",
                "target_number": item.number,
                "target": {"kind": "PR", "number": item.number},
                "preconditions": ["active_controller_owner", "verified_pr_head", "clean_scoped_diff"],
                "runner_authority": RUNNER_AUTHORITY,
                "no_generic_command": True,
            }
        )
    return actions


def ci_red_actions(repo_root: Path, items: list[GhItem]) -> list[dict[str, Any]]:
    slug = github_repo_slug()
    if not slug:
        return []
    projection = PrChecksProjection(cwd=repo_root)
    actions: list[dict[str, Any]] = []
    for item in items:
        if item.kind != "PR":
            continue
        status = projection.check_pr(slug, item.number)
        if not status.ok:
            continue
        fail_count = sum(1 for check in status.runs if check.bucket == "fail")
        if fail_count <= 0:
            continue
        actions.append(
            {
                "priority": 4,
                "kind": "ci-red",
                "action_id": f"ci-red:{item.number}:{status.head_sha}",
                "item": item.item,
                "phase": "ci-watch",
                "actor": "remote-ci-fix-codex",
                "fail_count": fail_count,
                "head_sha": status.head_sha,
                "check_names": [check.name for check in status.runs if check.bucket == "fail"],
                "source_artifact": "github-check-runs",
                "source_marker": f"ci-red:{item.number}:{status.head_sha}",
                "target_kind": "PR",
                "target_number": item.number,
                "target": {"kind": "PR", "number": item.number},
                "preconditions": ["active_controller_owner", "live_open_target", "checks_red"],
                "controller_action": "dispatch_remote_ci_fix",
                "runner_authority": RUNNER_AUTHORITY,
                "no_generic_command": True,
            }
        )
    return actions


def release_rollup_actions(repo_root: Path) -> list[dict[str, Any]]:
    pending_path = repo_root / ".refactor-loop" / ".controller-pending-events.log"
    if not pending_path.exists():
        return []
    try:
        lines = pending_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    actions: list[dict[str, Any]] = []
    latest_by_integration_sha: dict[str, tuple[dict[str, Any], str, str]] = {}
    for line in reversed(lines[-200:]):
        marker = "DEV_SYNC_PENDING:release-rollup-needed:"
        if marker not in line:
            continue
        event_json = line.split(marker, 1)[1].strip()
        try:
            event = json.loads(event_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        integration_sha = str(event.get("integration_sha") or "").strip()
        if not integration_sha:
            continue
        if integration_sha in latest_by_integration_sha:
            continue
        latest_by_integration_sha[integration_sha] = (event, event_json, line)
    for integration_sha, (event, event_json, line) in latest_by_integration_sha.items():
        if not _release_rollup_event_is_fresh(repo_root, event, integration_sha):
            continue
        actions.append(
            {
                "priority": 3,
                "kind": "release-rollup-needed",
                "action_id": f"release-rollup-needed:{integration_sha}",
                "item": "release rollup",
                "phase": "publish",
                "actor": "controller",
                "route": "release-rollup",
                "event": event,
                "event_json": event_json,
                "body_file": ".refactor-loop/runs/release-rollup-pr-body.md",
                "title": "Release rollup",
                "source_artifact": ".refactor-loop/.controller-pending-events.log",
                "source_marker": line,
                "target_kind": "release-rollup",
                "target_number": None,
                "target": {"kind": "release-rollup", "integration_sha": integration_sha},
                "preconditions": ["active_controller_owner", "source_artifact_contains_evidence", "release_rollup_event"],
                "controller_action": "open_release_rollup_pr_from_action",
                "runner_authority": RUNNER_AUTHORITY,
                "no_generic_command": True,
                "no_lifecycle_authority": True,
            }
        )
    return actions


def _release_rollup_event_is_fresh(repo_root: Path, event: dict[str, Any], integration_sha: str) -> bool:
    integration_branch = safe_head_ref(str(event.get("integration_branch") or ""))
    review_base_branch = safe_head_ref(str(event.get("review_base_branch") or ""))
    if not integration_branch or not review_base_branch:
        return True
    integration_ref = f"refs/remotes/origin/{integration_branch}"
    review_base_ref = f"refs/remotes/origin/{review_base_branch}"
    current_integration = git_text(["git", "-C", str(repo_root), "rev-parse", "--verify", integration_ref], cwd=repo_root)
    current_review_base = git_text(["git", "-C", str(repo_root), "rev-parse", "--verify", review_base_ref], cwd=repo_root)
    ahead = git_text(["git", "-C", str(repo_root), "rev-list", "--count", f"{review_base_ref}..{integration_ref}"], cwd=repo_root)
    if current_integration.returncode != 0 or current_review_base.returncode != 0 or ahead.returncode != 0:
        return True
    current_integration_sha = current_integration.stdout.strip()
    current_review_base_sha = current_review_base.stdout.strip()
    try:
        ahead_count = int(ahead.stdout.strip())
    except ValueError:
        return True
    return ahead_count > 0 and current_integration_sha == integration_sha and current_review_base_sha != current_integration_sha


def _worktrees_by_branch(repo_root: Path) -> dict[str, Path]:
    listed = git_text(["git", "-C", str(repo_root), "worktree", "list", "--porcelain"], cwd=repo_root)
    if listed.returncode != 0:
        return {}
    return parse_worktree_branches(listed.stdout)


def existing_issue_actions(items: list[GhItem], repo_root: Path | None = None) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    raw_by_key = {(item.kind.lower(), item.number): item for item in items}
    actionable = open_actionable_managed_items(_projection_items(items))
    ordered = sorted(actionable, key=lambda item: _existing_issue_sort_key(item, repo_root))
    for item in ordered:
        raw = raw_by_key.get((item.kind, item.number))
        title = raw.title if raw is not None else item.title
        milestone = label_catalog.MILESTONE_CURRENT in label_catalog.normalize_label_set(item.labels).canonical
        priority = 6 if milestone else 7
        item_name = f"{'PR' if item.kind == 'pr' else item.kind} #{item.number}"
        action = {
            "priority": priority,
            "kind": "existing-issue",
            "action_id": f"existing-issue:{item.kind}:{item.number}",
            "item": item_name,
            "phase": phase_from_labels(item.labels),
            "actor": actor_from_labels(item.labels, item.kind),
            "milestone": milestone,
            "title": title,
            "source_artifact": "github-open-managed-items",
            "source_marker": f"existing-issue:{item.kind}:{item.number}",
            "target_kind": "PR" if item.kind == "pr" else "issue",
            "target_number": item.number,
            "target": {"kind": "PR" if item.kind == "pr" else "issue", "number": item.number},
            "preconditions": ["active_controller_owner", "live_open_target"],
            "route": "design-consensus-status" if phase_from_labels(item.labels) == "design-consensus" else "existing-managed-item-status",
            "status_only": True,
            "no_lifecycle_authority": True,
        }
        if item.kind == "issue" and phase_from_labels(item.labels) == "implementation" and milestone:
            consensus_fields = latest_consensus_implementation_for_issue(repo_root, item.number) if repo_root else {}
            if consensus_fields:
                action.update(
                    {
                        "kind": "consensus-implementation-ready",
                        "action_id": f"consensus-implementation-ready:{item.number}:{consensus_fields['consensus_round']}",
                        "route": "dispatch-consensus-implementation",
                        "controller_action": "dispatch_consensus_implementation",
                        "preconditions": [
                            "active_controller_owner",
                            "live_open_target",
                            "durable_consensus_artifact",
                            "consensus_implementation_ready",
                        ],
                        "runner_authority": RUNNER_AUTHORITY,
                        "no_generic_command": True,
                        **consensus_fields,
                    }
                )
                _apply_consensus_implementation_readiness(action, repo_root, items, None)
                if action.get("consensus_implementation_ready") is True:
                    action.pop("status_only", None)
        actions.append(action)
    return actions


def repository_stalled_meta_reflector_actions(
    repo_root: Path,
    ctx: LoopContext,
    items: list[GhItem],
    monitor: Any | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    threshold_seconds = meta_escalation_stuck_seconds()
    stalled_items = _repository_stalled_items(items, threshold_seconds=threshold_seconds, now=now)
    if not stalled_items:
        return []
    prompt = (ctx.skill_root / "prompts" / "meta-reflector-repository-stalled.md").resolve()
    log = (repo_root / ".refactor-loop" / "logs" / "meta-reflector-repository-stalled.log").resolve()
    if not prompt.is_file():
        return []
    if _repository_stalled_meta_reflector_suppressed(repo_root, log, monitor):
        return []
    threshold_hours = _format_hours(threshold_seconds / 3600.0)
    return [
        {
            "priority": 8,
            "kind": "repository-stalled-meta-reflector",
            "action_id": "repository-stalled-meta-reflector",
            "intent_id": "repository-stalled-meta-reflector",
            "item": "repository stalled managed work",
            "phase": "design-consensus",
            "actor": "meta-reflector-codex",
            "route": "repository-stalled-meta-reflector",
            "source": "wakeup-plan",
            "command": "spawn-codex",
            "controller_action": "spawn_codex_harness_background",
            "cd": str(repo_root.resolve()),
            "prompt": str(prompt),
            "log": str(log),
            "stall": 5400,
            "run_in_background_required": True,
            "no_lifecycle_authority": True,
            "reason": "open managed issue/PR updatedAt exceeded META_ESCALATION_STUCK_HOURS effective threshold",
            "source_artifact": "github-open-managed-items",
            "source_marker": f"meta-escalation-long-stuck:{threshold_hours}",
            "target_kind": "codex",
            "target_number": None,
            "target": {"kind": "codex", "task_id": "meta-reflector-repository-stalled"},
            "preconditions": [
                "active_controller_owner",
                "live_open_targets",
                "long_stuck_threshold_exceeded",
                "recommendation_only",
            ],
            "runner_authority": RUNNER_AUTHORITY,
            "no_generic_command": True,
            "threshold_hours": threshold_hours,
            "stale_revival_hours": _format_hours(stale_revival_seconds() / 3600.0),
            "stalled_items": stalled_items,
        }
    ]


def _repository_stalled_items(items: list[GhItem], *, threshold_seconds: float, now: float | None = None) -> list[dict[str, Any]]:
    raw_by_key = {(item.kind.lower(), item.number): item for item in items}
    actionable = open_actionable_managed_items(_projection_items(items))
    result: list[dict[str, Any]] = []
    current = time.time() if now is None else now
    for item in sorted(actionable, key=lambda item: (0 if item.kind == "issue" else 1, item.number)):
        labels = label_catalog.normalize_label_set(item.labels).canonical
        if label_catalog.HUMAN_MAINTAINER_DECISION in labels:
            continue
        raw = raw_by_key.get((item.kind, item.number))
        if raw is None:
            continue
        updated_at = _parse_github_timestamp(raw.updated_at)
        if updated_at is None:
            continue
        age_seconds = max(0.0, current - updated_at)
        if age_seconds < threshold_seconds:
            continue
        result.append(
            {
                "kind": "PR" if item.kind == "pr" else "issue",
                "number": item.number,
                "title": raw.title,
                "phase": phase_from_labels(item.labels),
                "human": actor_from_labels(item.labels, item.kind),
                "updated_at": raw.updated_at,
                "stuck_hours": round(age_seconds / 3600.0, 2),
            }
        )
    return result


def _parse_github_timestamp(value: str) -> float | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _repository_stalled_meta_reflector_suppressed(repo_root: Path, log: Path, monitor: Any | None) -> bool:
    if _harness_spawn_intent_log_suppresses_retry(log):
        return True
    if _canonical_in_flight_for_log(log, monitor):
        return True
    pending = repo_root / ".refactor-loop" / ".controller-pending-events.log"
    if not pending.exists():
        return False
    try:
        lines = pending.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
    except OSError:
        return False
    return any("repository-stalled-meta-reflector" in line or "meta-reflector-repository-stalled" in line for line in lines)


def _format_hours(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:g}"


def _apply_consensus_implementation_readiness(
    action: dict[str, Any],
    repo_root: Path,
    gh_items: list[GhItem] | None,
    monitor: Any | None,
) -> None:
    reason = consensus_implementation_suppressed_reason(action, repo_root, gh_items, monitor)
    if not reason:
        action["consensus_implementation_ready"] = True
        return
    action["consensus_implementation_ready"] = False
    action["suppressed_reason"] = reason
    action["status_only"] = True
    action["no_lifecycle_authority"] = True
    action.pop("runner_authority", None)
    action.pop("no_generic_command", None)


def serialize_conflicting_consensus_implementation_actions(actions: list[dict[str, Any]]) -> None:
    executable: list[tuple[int, tuple[str, ...]]] = []
    for index, action in enumerate(actions):
        if action.get("controller_action") != "dispatch_consensus_implementation" or action.get("status_only"):
            continue
        scope = _normalized_consensus_scope_paths(action.get("scope_paths"))
        if any(_scope_paths_overlap(scope, other_scope) for _other_index, other_scope in executable):
            action["consensus_implementation_ready"] = False
            action["suppressed_reason"] = "scope_conflict_waiting"
            action["status_only"] = True
            action["no_lifecycle_authority"] = True
            action.pop("runner_authority", None)
            action.pop("no_generic_command", None)
            continue
        executable.append((index, scope))


def _normalized_consensus_scope_paths(raw_scope_paths: Any) -> tuple[str, ...]:
    paths: set[str] = set()
    for raw_line in str(raw_scope_paths or "").splitlines():
        path = _normalized_consensus_scope_path(raw_line)
        if path:
            paths.add(path)
    return tuple(sorted(paths))


def _normalized_consensus_scope_path(raw_line: str) -> str:
    text = raw_line.strip()
    if not text:
        return ""
    text = re.sub(r"^(?:[-*]\s+|\d+\.\s+)", "", text).strip()
    text = text.strip("`'\"")
    if not text or text.startswith("#"):
        return ""
    if "#" in text:
        text = text.split("#", 1)[0].strip()
    text = text.replace("\\", "/")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return ""
    return path.as_posix().rstrip("/")


def _scope_paths_overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    if not left or not right:
        return True
    return any(_scope_path_overlaps_one(left_path, right_path) for left_path in left for right_path in right)


def _scope_path_overlaps_one(left: str, right: str) -> bool:
    if left == right:
        return True
    return right.startswith(left + "/") or left.startswith(right + "/")


def consensus_implementation_suppressed_reason(
    action: dict[str, Any],
    repo_root: Path,
    gh_items: list[GhItem] | None = None,
    monitor: Any | None = None,
    *,
    ignore_pending_implement_intent: bool = False,
) -> str | None:
    target_kind = action.get("target_kind")
    target_number = action.get("target_number")
    if target_kind != "issue" or not isinstance(target_number, int):
        return "target_not_issue"
    if gh_items is not None:
        open_issues = _open_managed_issue_numbers(gh_items)
        if target_number not in open_issues:
            return "target_not_open"
        if _open_closing_pr_number(gh_items, target_number) is not None:
            return "open_closing_pr"
    branch = _canonical_consensus_implementation_branch(action)
    if not branch:
        return "invalid_iter_branch"
    lifecycle = classify_implement_attempt(
        repo_root=repo_root,
        action=action,
        integration_branch=_integration_branch_from_env(),
        command_runner=lambda command: git_text(list(command), cwd=repo_root),
    )
    if lifecycle.in_flight:
        return "in_flight_implement"
    if lifecycle.publish_ready or lifecycle.refresh_needed:
        return "implementation_ready_to_publish"
    if not ignore_pending_implement_intent and _pending_implement_intent_exists(repo_root, target_number, action):
        return "pending_implement_intent"
    if _in_flight_implement_exists(repo_root, action, monitor):
        return "in_flight_implement"
    if gh_items is not None and _open_pr_exists_for_branch(gh_items, branch):
        return "open_closing_pr"
    if _remote_iter_branch_exists(repo_root, branch) and not _local_iter_branch_exists(repo_root, branch):
        return "remote_iter_branch"
    return None


def _integration_branch_from_env() -> str:
    return str(os.environ.get("INTEGRATION_BRANCH") or "auto-refact-dev").strip()


def _canonical_consensus_implementation_branch(action: dict[str, Any]) -> str:
    iteration = str(action.get("iteration") or "").strip()
    cluster_id = str(action.get("cluster_id") or "").strip()
    if not SAFE_WORKTREE_ITERATION_RE.fullmatch(iteration) or not SAFE_WORKTREE_CLUSTER_RE.fullmatch(cluster_id):
        return ""
    return "refactor/" + f"iter{iteration}-{cluster_id}"


SAFE_WORKTREE_ITERATION_RE = re.compile(r"^[0-9]+$")
SAFE_WORKTREE_CLUSTER_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _open_closing_pr_number(items: list[GhItem], issue: int) -> int | None:
    for item in items:
        if item.kind != "PR":
            continue
        if label_catalog.MANAGED not in label_catalog.normalize_label_set(item.labels).canonical:
            continue
        if issue in extract_closing_issue_numbers(item.body):
            return item.number
    return None


def _local_iter_branch_exists(repo_root: Path, branch: str) -> bool:
    result = git_text(["git", "-C", str(repo_root), "rev-parse", "--verify", f"refs/heads/{branch}"], cwd=repo_root)
    return result.returncode == 0


def _remote_iter_branch_exists(repo_root: Path, branch: str) -> bool:
    result = git_text(["git", "-C", str(repo_root), "rev-parse", "--verify", f"refs/remotes/origin/{branch}"], cwd=repo_root)
    return result.returncode == 0


def _canonical_consensus_worktree_exists(repo_root: Path, action: dict[str, Any]) -> bool:
    iteration = str(action.get("iteration") or "").strip()
    cluster_id = str(action.get("cluster_id") or "").strip()
    if not iteration or not cluster_id:
        return False
    return (repo_root / ".worktrees" / f"iter{iteration}-{cluster_id}").is_dir()


def _implement_log_exists(repo_root: Path, action: dict[str, Any]) -> bool:
    cluster_id = str(action.get("cluster_id") or "").strip()
    if not cluster_id:
        return False
    return (repo_root / ".refactor-loop" / "logs" / f"implement-{cluster_id}.log").exists()


def _publish_ready_implementation_exists(repo_root: Path, action: dict[str, Any]) -> bool:
    return classify_implement_attempt(
        repo_root=repo_root,
        action=action,
        integration_branch=_integration_branch_from_env(),
        command_runner=lambda command: git_text(list(command), cwd=repo_root),
    ).publish_ready


def _canonical_implement_log_path(repo_root: Path, action: dict[str, Any]) -> Path:
    cluster_id = str(action.get("cluster_id") or "").strip()
    return repo_root / ".refactor-loop" / "logs" / f"implement-{cluster_id}.log"


def _canonical_consensus_worktree_path(repo_root: Path, action: dict[str, Any]) -> Path:
    iteration = str(action.get("iteration") or "").strip()
    cluster_id = str(action.get("cluster_id") or "").strip()
    return repo_root / ".worktrees" / f"iter{iteration}-{cluster_id}"


def _open_pr_exists_for_branch(items: list[GhItem], head_ref: str) -> bool:
    if not head_ref:
        return False
    for item in items:
        if item.kind != "PR":
            continue
        if label_catalog.MANAGED not in label_catalog.normalize_label_set(item.labels).canonical:
            continue
        if item.head_ref == head_ref:
            return True
    return False


def _pending_implement_intent_exists(repo_root: Path, issue: int, action: dict[str, Any]) -> bool:
    pending_path = repo_root / ".refactor-loop" / ".controller-pending-events.log"
    if not pending_path.exists():
        return False
    try:
        lines = pending_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    cluster_id = str(action.get("cluster_id") or "").strip()
    expected_ids = {f"{IMPLEMENT_PENDING_INTENT_PREFIX}{issue}"}
    if cluster_id:
        expected_ids.add(f"{IMPLEMENT_TASK_PREFIX}{cluster_id}")
    for line in lines:
        if " HARNESS_SPAWN_INTENT " not in line:
            continue
        try:
            intent = json.loads(line.split(" HARNESS_SPAWN_INTENT ", 1)[1])
        except json.JSONDecodeError:
            continue
        if not isinstance(intent, dict):
            continue
        intent_values = {str(intent.get("intent_id") or ""), str(intent.get("task_id") or "")}
        if expected_ids.intersection(intent_values):
            return True
    return False


def _in_flight_implement_exists(repo_root: Path, action: dict[str, Any], monitor: Any | None) -> bool:
    cluster_id = str(action.get("cluster_id") or "").strip()
    if not cluster_id:
        return False
    if monitor is None:
        return False
    try:
        lines = monitor.list_in_flight_codex_lines()
    except Exception:
        return False
    needles = (
        f"implement-{cluster_id}",
        f".refactor-loop/logs/implement-{cluster_id}.log",
        f".worktrees/iter{action.get('iteration')}-{cluster_id}",
    )
    return any("spawn-codex" in line and any(needle in line for needle in needles) for line in lines)


def latest_consensus_implementation_for_issue(repo_root: Path | None, issue: int) -> dict[str, Any]:
    if repo_root is None:
        return {}
    runs_dir = repo_root / ".refactor-loop" / "runs"
    if not runs_dir.exists():
        return {}
    candidates: list[tuple[int, Path]] = []
    for path in runs_dir.glob(f"phase9-issue{issue}-r*-judge.md"):
        match = CONSENSUS_JUDGE_ARTIFACT_RE.fullmatch(path.name)
        if match:
            candidates.append((int(match.group(2)), path))
    for round_no, artifact in sorted(candidates, reverse=True):
        projection = _consensus_projection_from_artifact(repo_root, artifact, issue, round_no)
        if projection:
            return projection
    return {}


def release_countdown_actions(repo_root: Path, items: list[GhItem], scorer: Any | None = None) -> list[dict[str, Any]]:
    targets = []
    for item in open_actionable_managed_items(_projection_items(items)):
        projection = label_catalog.normalize_label_set(item.labels)
        if label_catalog.MILESTONE_RELEASE_TARGET not in projection.canonical:
            continue
        targets.append(
            {
                "kind": "PR" if item.kind == "pr" else item.kind,
                "number": item.number,
                "item": f"{'PR' if item.kind == 'pr' else item.kind} #{item.number}",
                "title": item.title,
            }
        )
    activation = "explicit-target" if targets else "default-goal"
    milestone = None if targets else _default_goal_milestone(repo_root)

    score = _release_countdown_score(repo_root, scorer=scorer)
    signals = score.get("signals") if isinstance(score.get("signals"), dict) else {}
    red_signals = [name for name, signal in signals.items() if isinstance(signal, dict) and not signal.get("passed")]
    blocked = score.get("blocked_reasons")
    blocked_reasons = [str(reason) for reason in blocked] if isinstance(blocked, list) else red_signals
    release_goal = None
    if score:
        release_goal = {
            "from_version": score.get("from_version"),
            "to_version": score.get("to_version"),
            "countdown_to_version": score.get("to_version"),
            "stability_score": score.get("stability_score"),
            "ready": bool(score.get("ready")),
            "passed_signals": sum(1 for signal in signals.values() if isinstance(signal, dict) and signal.get("passed")),
            "total_signals": len(signals),
            "red_signals": red_signals,
            "blocked_reasons": blocked_reasons,
            "source": "release-gate",
        }
    return [
        {
            "priority": 8,
            "kind": "release-countdown",
            "phase": "publish",
            "actor": "controller",
            "route": "release-countdown-status",
            "status_only": True,
            "no_lifecycle_authority": True,
            "activation": activation,
            "goal": {
                "milestone": milestone,
                "release": release_goal,
            },
            "targets": targets,
            "from_version": score.get("from_version"),
            "to_version": score.get("to_version"),
            "stability_score": score.get("stability_score"),
            "ready": bool(score.get("ready")),
            "red_signals": red_signals,
            "blocked_reasons": blocked_reasons,
            "source": "release-gate",
        }
    ]


def _default_goal_milestone(repo_root: Path) -> dict[str, Any] | None:
    slug = github_repo_slug()
    if not slug:
        return None
    data = run_json(["gh", "api", f"repos/{slug}/milestones?state=open"], cwd=repo_root)
    if not isinstance(data, list):
        return None
    milestones: list[dict[str, Any]] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        try:
            number = int(raw["number"])
        except (KeyError, TypeError, ValueError):
            continue
        due_on = raw.get("due_on")
        milestones.append(
            {
                "number": number,
                "title": str(raw.get("title") or ""),
                "due_on": due_on if isinstance(due_on, str) and due_on else None,
            }
        )
    if not milestones:
        return None
    return min(milestones, key=lambda item: (item["due_on"] is None, item["due_on"] or "", item["number"]))


def _release_countdown_score(repo_root: Path, scorer: Any | None = None) -> dict[str, Any]:
    try:
        with contextlib.redirect_stdout(sys.stderr):
            score = (scorer or decide_release_artifact)(repo_root)
    except (KeyError, RuntimeError, ValueError) as exc:
        print(f"release-countdown: release goal unavailable: {exc}", file=sys.stderr)
        return {}
    return score if isinstance(score, dict) else {}


def has_dispatchable_action(actions: list[dict[str, Any]]) -> bool:
    return any(
        not action.get("status_only")
        and (
            action.get("kind") in EXECUTABLE_ACTION_KINDS
            or action.get("controller_action") in RUNNER_NAMED_HELPER_ACTIONS
        )
        for action in close_projection_actions(actions)
    )


def action_priority_sort_key(action: dict[str, Any]) -> tuple[int, int]:
    return (action_priority_class(action), int(action.get("priority", 99)))


def action_priority_class(action: dict[str, Any]) -> int:
    controller_action = action.get("controller_action")
    kind = action.get("kind")
    if kind == "maintainer-comment":
        return 1
    if kind == "unpushed-worker-output":
        return 2
    if kind == "completed-marker":
        return 3
    if kind == "ci-red":
        return 4
    if kind in {"no-gap-violation", "milestone"}:
        return 5
    if kind == "existing-issue":
        return 6
    if action.get("kind") == "harness-spawn-intent" and controller_action == "spawn_codex_harness_background":
        return 7
    if controller_action == "dispatch_consensus_implementation":
        return 7
    return 8


def controller_action_from_marker(marker: str) -> str:
    if marker.startswith("IMPLEMENT_DONE"):
        return "publish_implementation_output"
    if marker.startswith("REVIEW_DONE"):
        return "review_gate"
    if marker.startswith("FIX_DONE"):
        return "dispatch_reviewers"
    if marker.startswith("TEST_ADD_DONE"):
        return "dispatch_ci_watch"
    if marker.startswith("META_RESOLVED:drop:"):
        return "close_managed_item_from_drop_marker"
    if marker.startswith("META_JUDGE_DONE:consensus"):
        return "dispatch_consensus_implementation"
    if marker.startswith("AUDIT_DONE"):
        return "dispatch_work_intake"
    if marker.startswith("VERIFY_DONE"):
        return "dispatch_review_gate"
    return "dispatch_next_step_worker"


def _target_kind_from_item(item: str | None) -> str | None:
    if not item:
        return None
    lowered = item.lower()
    if lowered.startswith("pr #"):
        return "PR"
    if lowered.startswith("issue #"):
        return "issue"
    return None


def _target_number_from_item(item: str | None) -> int | None:
    if not item:
        return None
    match = re.search(r"#([1-9][0-9]*)", item)
    return int(match.group(1)) if match else None


def _target_from_item(item: str | None) -> dict[str, Any] | None:
    kind = _target_kind_from_item(item)
    number = _target_number_from_item(item)
    if kind is None or number is None:
        return None
    return {"kind": kind, "number": number}


def close_projection_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_close_projection_action(action) for action in actions]


def _close_projection_action(action: dict[str, Any]) -> dict[str, Any]:
    closed = dict(action)
    if closed.get("controller_action") == "dispatch_consensus_implementation" and not closed.get("consensus_artifact"):
        closed["status_only"] = True
        closed["no_lifecycle_authority"] = True
        closed.pop("runner_authority", None)
        closed.pop("no_generic_command", None)
        return closed
    source_marker = str(closed.get("source_marker") or "")
    if _design_consensus_marker_is_router_owned(source_marker):
        closed["status_only"] = True
        closed["no_lifecycle_authority"] = True
        closed.pop("runner_authority", None)
        closed.pop("no_generic_command", None)
        return closed
    if closed.get("kind") in EXECUTABLE_ACTION_KINDS and not closed.get("status_only"):
        closed.setdefault("runner_authority", RUNNER_AUTHORITY)
        closed.setdefault("preconditions", ["active_controller_owner"])
        closed.setdefault("source_artifact", closed.get("evidence") or closed.get("source") or closed.get("route") or "wakeup-plan")
        closed.setdefault("source_marker", closed.get("marker") or closed.get("line") or closed.get("evidence") or closed.get("source_marker"))
        closed.setdefault("target", _target_from_item(closed.get("item")))
        if "target_kind" not in closed:
            target = closed.get("target") if isinstance(closed.get("target"), dict) else {}
            closed["target_kind"] = target.get("kind")
        if "target_number" not in closed:
            target = closed.get("target") if isinstance(closed.get("target"), dict) else {}
            closed["target_number"] = target.get("number")
        if closed.get("controller_action") not in RUNNER_NAMED_HELPER_ACTIONS:
            closed["status_only"] = True
            closed["no_lifecycle_authority"] = True
            closed.pop("runner_authority", None)
            closed.pop("no_generic_command", None)
            return closed
        closed["no_generic_command"] = True
    else:
        closed.setdefault("status_only", True)
        closed.setdefault("no_lifecycle_authority", True)
    return closed


def _design_consensus_marker_is_router_owned(marker: str) -> bool:
    if marker.startswith("SOLVER_DONE"):
        return True
    if marker.startswith("META_JUDGE_DONE") and not marker.startswith("META_JUDGE_DONE:consensus"):
        return True
    if marker.startswith("META_RESOLVED") and not marker.startswith("META_RESOLVED:drop:"):
        return True
    return False


def suppress_stale_unexecutable_actions(
    actions: list[dict[str, Any]],
    *,
    repo_root: Path,
    gh_items: list[GhItem],
    gh_items_loaded: bool,
) -> None:
    if not gh_items_loaded:
        return
    open_targets = _open_managed_targets(gh_items)
    worktrees: dict[str, Path] | None = None
    for action in actions:
        if action.get("status_only"):
            continue
        if action.get("controller_action") == "publish_implementation_output" and worktrees is None:
            worktrees = _worktrees_by_branch(repo_root)
        reason = _stale_unexecutable_reason(action, repo_root, open_targets, worktrees or {}, gh_items)
        if not reason:
            continue
        action["status_only"] = True
        action["no_lifecycle_authority"] = True
        action["suppressed_reason"] = reason
        action.pop("runner_authority", None)
        action.pop("no_generic_command", None)


def _stale_unexecutable_reason(
    action: dict[str, Any],
    repo_root: Path,
    open_targets: set[tuple[str, int]],
    worktrees: dict[str, Path],
    gh_items: list[GhItem],
) -> str | None:
    controller_action = action.get("controller_action")
    if controller_action == "publish_implementation_output":
        return _stale_publish_implementation_reason(action, repo_root, open_targets, worktrees, gh_items)
    if controller_action == "close_managed_item_from_drop_marker":
        target = _action_target_key(action)
        if target is not None and target in open_targets:
            return "live_open_target"
    return None


def _stale_publish_implementation_reason(
    action: dict[str, Any],
    repo_root: Path,
    open_targets: set[tuple[str, int]],
    worktrees: dict[str, Path],
    gh_items: list[GhItem],
) -> str | None:
    target = _action_target_key(action)
    if target is not None and target not in open_targets:
        return "target_not_open"
    head_ref = _implementation_head_ref(action, target)
    if not head_ref:
        return "early_pr_missing"
    worktree = worktrees.get(head_ref)
    if worktree is None:
        return "early_pr_missing"
    state = classify_implement_attempt(
        repo_root=repo_root,
        action=action,
        log_path=(repo_root / str(action.get("source_artifact") or "")),
        integration_branch=_integration_branch_from_env(),
        command_runner=lambda command: git_text(list(command), cwd=repo_root),
    )
    if _publish_recoverable_stale_base_implement(state):
        state = replace(state, status="publish_ready")
    if state.redispatch:
        clear_redispatchable_implement_log(
            repo_root=repo_root,
            action=action,
            log_path=(repo_root / str(action.get("source_artifact") or "")),
            integration_branch=_integration_branch_from_env(),
            command_runner=lambda command: git_text(list(command), cwd=repo_root),
        )
        return f"implementation_redispatch:{state.reason}"
    if state.in_flight:
        return "in_flight_implement"
    artifact_reason = _implementation_pr_artifact_invalid_reason(action, repo_root)
    if artifact_reason:
        return artifact_reason
    match_error = _matching_open_pr_error(action, target, gh_items=gh_items, head_ref=head_ref, worktree=worktree)
    if match_error:
        return match_error
    action["head_ref"] = head_ref
    action["worktree"] = str(worktree)
    preconditions = list(action.get("preconditions") if isinstance(action.get("preconditions"), list) else [])
    for required in (
        "canonical_implementation_identity",
        "fresh_integration_base",
        "single_linked_managed_issue",
        "worker_authored_pr_artifacts",
        "exactly_one_matching_open_pr",
        "host_checks_green",
        "clean_scoped_diff",
    ):
        if required not in preconditions:
            preconditions.append(required)
    if "verified_pr_head" in preconditions:
        preconditions.remove("verified_pr_head")
    action["preconditions"] = preconditions
    return None


def _matching_open_pr_error(
    action: dict[str, Any],
    target: tuple[str, int] | None,
    *,
    gh_items: list[GhItem],
    head_ref: str,
    worktree: Path,
) -> str | None:
    if target is None or target[0] != "issue":
        return "single_linked_managed_issue_missing"
    matches = [item for item in gh_items if item.kind == "PR" and item.head_ref == head_ref]
    if not matches:
        return "early_pr_missing"
    if len(matches) > 1:
        return "multiple_matching_open_pr"
    pr = matches[0]
    normalized = label_catalog.normalize_label_set(pr.labels).canonical
    if label_catalog.MANAGED not in normalized:
        return "matching_pr_not_managed"
    if _single_linked_issue_from_body(pr.body) != target[1]:
        return "matching_pr_issue_mismatch"
    action["target_pr_number"] = pr.number
    return None


def _single_linked_issue_from_body(body: str) -> int | None:
    numbers = extract_closing_issue_numbers(body)
    return numbers[0] if len(numbers) == 1 else None


def _worktree_has_non_empty_diff(worktree: Path) -> bool:
    diff = git_text(["git", "-C", str(worktree), "diff", "HEAD", "--quiet"], cwd=worktree)
    return diff.returncode == 1


def _implementation_head_ref(action: dict[str, Any], target: tuple[str, int] | None) -> str | None:
    explicit = safe_head_ref(str(action.get("head_ref") or ""))
    if explicit:
        return explicit
    if target is None or target[0] != "issue":
        return None
    marker = str(action.get("source_marker") or "")
    candidates: list[str] = []
    marker_id = marker.removeprefix("IMPLEMENT_DONE:").removesuffix(":ok").strip(":")
    if marker_id:
        candidates.append(marker_id)
    candidates.append(f"issue-{target[1]}")
    candidates.append(f"issue{target[1]}")
    for candidate in candidates:
        normalized = candidate.replace("_", "-").strip("-")
        if not normalized:
            continue
        ref = safe_head_ref("refactor/" + f"iter{target[1]}-{normalized}")
        if ref:
            return ref
    return None


def _implementation_cluster_id(action: Mapping[str, Any], issue_target: int) -> str:
    return implementation_cluster_id(action, issue_target)


def _implementation_pr_artifact_invalid_reason(action: Mapping[str, Any], repo_root: Path) -> str | None:
    target = action.get("target_number")
    if not isinstance(target, int):
        return "implementation_pr_artifact_target_missing"
    validation = validate_implementation_pr_artifacts(repo_root, repo_root / ".refactor-loop" / "runs", action, target)
    return validation.reason


def restore_hard_gate_for_dispatchable_actions(concurrency: dict[str, Any], actions: list[dict[str, Any]]) -> None:
    hard_gate = concurrency.get("hard_gate", {})
    if hard_gate.get("reason") != "single_active_audit_in_flight":
        return
    if not has_dispatchable_action(actions):
        return
    deficit = int(concurrency.get("deficit", 0))
    hard_gate.update(
        {
            "active": deficit > 0,
            "dispatch_required": deficit if deficit > 0 else 0,
            "line": f"HARD_GATE:dispatch_required={deficit}" if deficit > 0 else None,
            "semantics": (
                "controller must dispatch this many actionable managed issue/PR tasks or legal fallback issue production through audit before ending the wakeup"
                if deficit > 0
                else None
            ),
            "reason": None,
            "blocked_deficit": 0,
            "boundary_task_id": None,
        }
    )


def _existing_issue_sort_key(item: Any, repo_root: Path | None) -> tuple[bool, int, float, int, int]:
    if repo_root is None:
        transition_key = (0, -0.0)
    else:
        transition_key = transition_rank_key(
            TransitionAssessmentReader.load_for_work_unit(
                repo_root,
                work_unit_id=f"issue-{item.number}",
                source_ref=f"gh-issue-{item.number}",
            )
        )
    milestone = label_catalog.MILESTONE_CURRENT in label_catalog.normalize_label_set(item.labels).canonical
    return (not milestone, transition_key[0], transition_key[1], 0 if item.kind == "issue" else 1, item.number)


def phase_from_labels(labels: tuple[str, ...]) -> str:
    phase = label_catalog.normalize_label_set(labels).phase
    if phase:
        stage = PHASE_TO_STAGE.get(phase, "work-intake")
        assert_stage_slug(stage)
        return stage
    return "work-intake"


def status_from_labels(labels: tuple[str, ...]) -> str | None:
    projection = label_catalog.normalize_label_set(labels)
    if projection.phase in NON_ACTION_PHASE_LABELS:
        return NON_ACTION_PHASE_LABELS[projection.phase]
    if not projection.phase:
        return "unlabeled-existing-issue"
    return None


def actor_from_labels(labels: tuple[str, ...], kind: str) -> str:
    projection = label_catalog.normalize_label_set(labels)
    if label_catalog.HUMAN_MAINTAINER_DECISION in projection.canonical or label_catalog.PHASE_BLOCKED in projection.canonical:
        return "controller"
    if projection.phase:
        actor = label_catalog.actor_for_phase(projection.phase)
        if actor:
            return actor
    return "controller-triage" if kind == "issue" else "reviewer-or-controller"


def _projection_items(items: list[GhItem]) -> list[dict[str, Any]]:
    return [
        {
            "kind": item.kind,
            "number": item.number,
            "labels": item.labels,
            "body": item.body,
            "state": "open",
            "title": item.title,
        }
        for item in items
    ]


def latest_controller_validated_audit_none(repo_root: Path) -> bool:
    logs_dir = repo_root / ".refactor-loop" / "logs"
    if not logs_dir.exists():
        return False
    audit_logs = sorted(logs_dir.glob("audit*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for log_path in audit_logs:
        marker = marker_from_completed_log(log_path)
        if not marker or not marker.startswith("AUDIT_DONE"):
            continue
        return marker.startswith("AUDIT_DONE:none:0")
    return False


def build_plan(repo_root: Path) -> dict[str, Any]:
    ctx = LoopContext.load(repo_root=repo_root, env=os.environ, cwd=repo_root, read_only=True)
    os.environ.update(ctx.env_for_subprocess())

    health = daemon_health(repo_root)
    gh_items, gh_items_loaded = load_github_items_with_status(repo_root)
    audit_none_fixed_point = latest_controller_validated_audit_none(repo_root)
    concurrency_module = import_concurrency_monitor(repo_root)
    monitor = build_concurrency_monitor(repo_root, concurrency_module)
    concurrency = concurrency_plan(
        repo_root,
        fixed_point=audit_none_fixed_point,
        gh_items=gh_items,
        monitor=monitor,
        concurrency_module=concurrency_module,
    )

    actions: list[dict[str, Any]] = []
    actions.extend(pending_bootstrap_actions(ctx, health))
    actions.extend(harness_spawn_intent_actions(repo_root, ctx, monitor, gh_items, gh_items_loaded))
    actions.extend(maintainer_comment_actions(repo_root, gh_items))
    actions.extend(unpushed_worker_output_actions(repo_root, gh_items))
    actions.extend(review_evidence_redispatch_actions(repo_root, gh_items if gh_items_loaded else []))
    completed_marker_open_targets = _open_managed_targets(gh_items) if gh_items_loaded else None
    actions.extend(completed_marker_actions(repo_root, ctx, completed_marker_open_targets, gh_items if gh_items_loaded else None, monitor))
    actions.extend(release_rollup_actions(repo_root))
    actions.extend(ci_red_actions(repo_root, gh_items))
    actions.extend(no_gap_actions(repo_root))
    host_actions, host_spec_error = load_host_workflow_projection(repo_root)
    if host_spec_error:
        actions.append(
            {
                "priority": 2,
                "kind": "host-workflow-spec-invalid",
                "item": None,
                "phase": "bootstrap",
                "actor": "controller",
                "route": "host-workflow-spec",
                "reason": host_spec_error,
                "no_lifecycle_authority": True,
            }
        )
    else:
        actions.extend(host_actions)
    actions.extend(release_countdown_actions(repo_root, gh_items))
    actions.extend(existing_issue_actions(gh_items, repo_root))
    if gh_items_loaded and not has_dispatchable_action(actions):
        actions.extend(repository_stalled_meta_reflector_actions(repo_root, ctx, gh_items, monitor))
    suppress_stale_unexecutable_actions(actions, repo_root=repo_root, gh_items=gh_items, gh_items_loaded=gh_items_loaded)
    actions.sort(key=action_priority_sort_key)
    serialize_conflicting_consensus_implementation_actions(actions)
    restore_hard_gate_for_dispatchable_actions(concurrency, actions)

    recommendation: str | None = None
    non_status_actions = [action for action in actions if action.get("kind") != "release-countdown"]
    if not non_status_actions:
        if concurrency["hard_gate"].get("reason") == "single_active_audit_in_flight":
            recommendation = "WAIT:single-active-audit"
        else:
            recommendation = "RECOMMEND:audit"

    return {
        "schema": "wakeup-plan",
        "repo_root": str(repo_root),
        "authorization": PLAN_AUTHORIZATION,
        "mode": "closed-action-projection",
        "apply_authority": "wakeup-runner-396-only",
        "no_lifecycle_authority": True,
        "daemon_health": health,
        "concurrency": concurrency,
        "hard_gate": concurrency["hard_gate"],
        "actions": close_projection_actions(actions),
        "recommendation": recommendation,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit a read-only prioritized wakeup plan as JSON.")
    parser.add_argument("--repo-root", help="Host repository root. Defaults to REPO_ROOT or cwd.")
    args = parser.parse_args(argv)
    repo_root = resolve_repo_root(args.repo_root)
    plan = build_plan(repo_root)
    print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
    hard_gate_line = plan["hard_gate"].get("line")
    if hard_gate_line:
        print(hard_gate_line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
