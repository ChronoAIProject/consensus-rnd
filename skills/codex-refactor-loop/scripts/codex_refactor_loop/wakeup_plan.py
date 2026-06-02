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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codex_refactor_loop import labels as label_catalog
from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.pr_checks import PrChecksProjection
from codex_refactor_loop.release.gate import decide_release_artifact
from codex_refactor_loop.restart import restart_managed_daemon_names
from codex_refactor_loop.transition_assessment import TransitionAssessmentReader, transition_rank_key
from codex_refactor_loop.work_items import ManagedWorkProjection, open_actionable_managed_items
from codex_refactor_loop.workflow_spec import WorkflowSpecError, load_validated_workflow_spec
from codex_refactor_loop.workflow_stages import assert_stage_slug


STALE_SECONDS = 90
MARKER_TAIL_LINES = 30
DONE_PREFIXES = (
    "AUDIT_DONE",
    "SOLVER_DONE",
    "META_JUDGE_DONE",
    "META_RESOLVED",
    "IMPLEMENT_DONE",
    "VERIFY_DONE",
    "REVIEW_DONE",
    "FIX_DONE",
    "TEST_ADD_DONE",
    "TRIAGE_DECISION_DONE",
)
DONE_PREFIX_RE = re.compile(r"^(?:" + "|".join(re.escape(prefix) for prefix in DONE_PREFIXES) + r")(?::[^\s`]+)*$")
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
    "unpushed-worker-output",
    "completed-marker",
    "release-rollup-needed",
    "ci-red",
}
NON_ACTION_PHASE_LABELS = {
    label_catalog.PHASE_PR_OPEN: "pr-open",
    label_catalog.PHASE_CI_RUNNING: "ci-running",
    label_catalog.PHASE_BLOCKED: "blocked",
    label_catalog.PHASE_MERGED: "merged",
}
REVIEW_HEAD_RE = re.compile(r"(?im)^(?:reviewed[-_ ]?head[-_ ]?sha|head[-_ ]?sha|headRefOid|REVIEW_HEAD_SHA)\s*[:=]\s*([0-9a-f]{7,64})\s*$")
CONSENSUS_JUDGE_ARTIFACT_RE = re.compile(r"^phase9-issue([1-9][0-9]*)-r([1-9][0-9]*)-judge\.md$")
CONSENSUS_JUDGE_LOG_RE = re.compile(r"^phase9-issue([1-9][0-9]*)-r([1-9][0-9]*)-judge\.log$")


@dataclass(frozen=True)
class GhItem:
    kind: str
    number: int
    title: str
    labels: tuple[str, ...]
    head_ref: str | None = None
    body: str = ""

    @property
    def item(self) -> str:
        return f"{self.kind} #{self.number}"

    @property
    def milestone(self) -> bool:
        return label_catalog.MILESTONE_CURRENT in label_catalog.normalize_label_set(self.labels).canonical


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
    open_targets = _open_managed_targets(gh_items or []) if gh_items_loaded else set()
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
        if log_path.exists() or _canonical_in_flight_for_log(log_path, monitor):
            continue
        if _suppress_harness_spawn_intent(intent, terminal_blocked_intent_ids, open_targets, gh_items_loaded):
            continue
        actions.append(
            {
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
                "evidence": line,
                "source_artifact": ".refactor-loop/.controller-pending-events.log",
                "source_marker": line,
                "target_kind": "codex",
                "target_number": None,
                "target": {"kind": "codex", "task_id": str(intent.get("task_id") or intent_id)},
                "preconditions": ["active_controller_owner", "source_artifact_contains_evidence", "target_log_absent"],
                "runner_authority": RUNNER_AUTHORITY,
                "no_generic_command": True,
            }
        )
    return actions


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


def _suppress_harness_spawn_intent(
    intent: dict[str, Any],
    terminal_blocked_intent_ids: set[str],
    open_targets: set[tuple[str, int]],
    gh_items_loaded: bool,
) -> bool:
    intent_id = str(intent.get("intent_id") or "")
    if intent_id in terminal_blocked_intent_ids:
        return True
    target = _harness_spawn_intent_target(intent)
    if gh_items_loaded and target is not None and target not in open_targets:
        return True
    return False


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
    tail = tail_lines(log_path, 5)
    return any(line == "EXIT=0" for line in tail)


def tail_lines(path: Path, count: int) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").splitlines()[-count:]
    except OSError:
        return []


def marker_from_completed_log(log_path: Path) -> str | None:
    if not is_clean_exit(log_path):
        return None
    tail = tail_lines(log_path, MARKER_TAIL_LINES)
    try:
        exit_index = max(index for index, line in enumerate(tail) if line.strip() == "EXIT=0")
    except ValueError:
        return None
    before_exit = tail[:exit_index]
    for line in reversed(before_exit):
        stripped = line.strip()
        if not stripped:
            continue
        marker = _extract_completed_marker_line(stripped)
        if marker:
            return marker
        break
    for index, line in enumerate(before_exit):
        if "⟦AI:AUTO-LOOP⟧" not in line:
            continue
        for candidate in before_exit[index + 1 : index + 4]:
            marker = _extract_completed_marker_line(candidate.strip())
            if marker:
                return marker
    return None


def _extract_completed_marker_line(text: str) -> str | None:
    stripped = text.strip()
    if stripped.startswith("+") and not stripped.startswith("+++"):
        stripped = stripped[1:].strip()
    stripped = stripped.strip("`")
    if not stripped:
        return None
    if "<" in stripped and ">" in stripped:
        return None
    if any(stripped.startswith(f"{prefix}:") for prefix in DONE_PREFIXES):
        return stripped
    if DONE_PREFIX_RE.fullmatch(stripped):
        return stripped
    return None


def completed_marker_actions(repo_root: Path) -> list[dict[str, Any]]:
    logs_dir = repo_root / ".refactor-loop" / "logs"
    if not logs_dir.exists():
        return []
    actions: list[dict[str, Any]] = []
    for log_path in sorted(logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True):
        marker = marker_from_completed_log(log_path)
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
        route = route_from_marker(marker)
        if route:
            action["route"] = route
        if marker.startswith("REVIEW_DONE"):
            head_sha = _reviewed_head_sha_from_log(log_path)
            if head_sha:
                action["head_sha"] = head_sha
        if marker.startswith("META_JUDGE_DONE:consensus"):
            consensus_fields = consensus_implementation_fields(repo_root, log_path, item)
            if consensus_fields:
                action.update(consensus_fields)
                action["preconditions"] = [
                    *action["preconditions"],
                    "durable_consensus_artifact",
                ]
        actions.append(action)
    return actions


def consensus_implementation_fields(repo_root: Path, log_path: Path, item: str | None) -> dict[str, Any]:
    match = CONSENSUS_JUDGE_LOG_RE.fullmatch(log_path.name)
    if match is None:
        return {}
    issue, round_no = match.groups()
    if item and _target_number_from_item(item) != int(issue):
        return {}
    artifact = repo_root / ".refactor-loop" / "runs" / f"phase9-issue{issue}-r{round_no}-judge.md"
    if not artifact.is_file():
        return {}
    facts = _consensus_artifact_facts(repo_root, artifact)
    if not _consensus_artifact_has_marker(artifact):
        return {}
    if not _consensus_facts_complete(facts):
        return {}
    return {
        "consensus_artifact": artifact.relative_to(repo_root).as_posix(),
        "design_decision_path": artifact.relative_to(repo_root).as_posix(),
        "consensus_issue": int(issue),
        "consensus_round": int(round_no),
        "cluster_id": f"issue-{issue}",
        "iteration": issue,
        "source_ref": f"gh-issue-{issue}",
        **facts,
    }


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
    scope_paths = _extract_scope_paths(text)
    old_pattern = _extract_section_text(text, "PROJECT_RULES clause violated") or _extract_section_text(text, "Recommended framing")
    new_principle = _extract_section_text(text, "Concrete plan")
    verification_hints = _extract_section_text(text, "Tests to add") or _extract_section_text(text, "Concrete plan")
    if not scope_paths:
        issue_match = CONSENSUS_JUDGE_ARTIFACT_RE.fullmatch(path.name)
        if issue_match:
            solver_paths = [
                repo_root / ".refactor-loop" / "runs" / f"phase9-issue{issue_match.group(1)}-r{issue_match.group(2)}-{role}.md"
                for role in ("minimal", "structural", "delete")
            ]
            scope_paths = _extract_solver_scope_paths(solver_paths)
    return {
        "scope_paths": scope_paths,
        "old_pattern": old_pattern,
        "new_principle": new_principle,
        "verification_hints": verification_hints,
    }


def _consensus_facts_complete(facts: dict[str, str]) -> bool:
    return all(str(facts.get(field) or "").strip() for field in ("scope_paths", "old_pattern", "new_principle"))


def _extract_scope_paths(text: str) -> str:
    bullet_paths: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- `"):
            continue
        match = re.match(r"- `([^`]+)`:", stripped)
        if match:
            bullet_paths.append(match.group(1))
    if bullet_paths:
        return "\n".join(f"- {path}" for path in bullet_paths)
    match = re.search(r"(?ims)^scope_paths\s*:\s*(.+?)(?:\n\n|^old_pattern\s*:|^new_principle\s*:|^verification)", text)
    if match:
        return match.group(1).strip()
    return ""


def _extract_solver_scope_paths(paths: list[Path]) -> str:
    values: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        extracted = _extract_scope_paths(path.read_text(encoding="utf-8", errors="replace"))
        for line in extracted.splitlines():
            value = line.removeprefix("- ").strip()
            if value and value not in seen:
                seen.add(value)
                values.append(value)
    return "\n".join(f"- {value}" for value in values)


def _extract_section_text(text: str, heading: str) -> str:
    pattern = re.compile(rf"(?ims)^##\s+{re.escape(heading)}\s*\n(.+?)(?=^##\s+|\Z)")
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


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
    slug = github_repo_slug()
    items: list[GhItem] = []
    loaded_ok = True
    for kind, gh_kind in (("issue", "issue"), ("PR", "pr")):
        rows: list[dict[str, Any]] = []
        json_fields = "number,title,labels,headRefName,body" if kind == "PR" else "number,title,labels"
        for query_label in label_catalog.query_labels_for(label_catalog.MANAGED):
            command = ["gh", gh_kind, "list", *gh_args(slug), "--label", query_label, "--state", "open", "--json", json_fields]
            data = run_json(command, cwd=repo_root)
            if isinstance(data, list):
                rows.extend(item for item in data if isinstance(item, dict))
            else:
                loaded_ok = False
        seen: set[int] = set()
        for raw in rows:
            try:
                number = int(raw["number"])
            except (KeyError, TypeError, ValueError):
                continue
            if number in seen:
                continue
            seen.add(number)
            labels = tuple(
                label.get("name", "")
                for label in raw.get("labels", [])
                if isinstance(label, dict) and label.get("name")
            )
            items.append(
                GhItem(
                    kind=kind,
                    number=number,
                    title=str(raw.get("title") or ""),
                    labels=labels,
                    head_ref=(str(raw.get("headRefName") or "") or None) if kind == "PR" else None,
                    body=str(raw.get("body") or "") if kind == "PR" else "",
                )
            )
    return items, loaded_ok


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
    seen: set[str] = set()
    for line in reversed(lines[-200:]):
        marker = "DEV_SYNC_PENDING:release-rollup-needed:"
        if marker not in line:
            continue
        event_json = line.split(marker, 1)[1].strip()
        if event_json in seen:
            continue
        seen.add(event_json)
        try:
            event = json.loads(event_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        integration_sha = str(event.get("integration_sha") or "").strip()
        if not integration_sha:
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
                        "preconditions": ["active_controller_owner", "live_open_target", "durable_consensus_artifact"],
                        "runner_authority": RUNNER_AUTHORITY,
                        "no_generic_command": True,
                        **consensus_fields,
                    }
                )
                action.pop("status_only", None)
        actions.append(action)
    return actions


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
        facts = _consensus_artifact_facts(repo_root, artifact)
        if not _consensus_artifact_has_marker(artifact) or not _consensus_facts_complete(facts):
            continue
        rel = artifact.relative_to(repo_root).as_posix()
        return {
            "consensus_artifact": rel,
            "design_decision_path": rel,
            "consensus_issue": issue,
            "consensus_round": round_no,
            "cluster_id": f"issue-{issue}",
            "iteration": str(issue),
            "source_ref": f"gh-issue-{issue}",
            **facts,
        }
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
    if marker.startswith(("SOLVER_DONE", "META_JUDGE_DONE", "META_RESOLVED")):
        return "dispatch_design_consensus"
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
    actions.extend(completed_marker_actions(repo_root))
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
    actions.sort(key=lambda action: action["priority"])
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
