"""Apply the #396 wakeup-plan closed action projection."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .active_controller import require_active_controller, write_active_controller_status
from . import labels
from .context import LoopContext
from .controller_actions import ControllerActions
from .gh_invoke import build_gh_argv
from .github_budget import graphql_headroom_ok, log_graphql_backoff
from .heartbeat import DaemonHeartbeatLease
from .implement_lifecycle import classify_implement_attempt, clear_redispatchable_implement_log, is_implement_log
from .pr_checks import PrChecksProjection
from .processes import ProcessSupervisor, launch_spawn_codex_supervisor
from .release.publish_preflight import ReleasePublishPreflight
from .state import read_json
from .wakeup_plan import build_plan, consensus_implementation_suppressed_reason


RUNNER_AUTHORITY = "wakeup-runner-396"
APPLY_AUTHORITY = "wakeup-runner-396-only"
FORBIDDEN_ACTION_FIELDS = {
    "argv",
    "args",
    "shell",
    "cmd",
    "command_line",
    "commands",
    "env",
    "git",
    "gh",
    "executor",
    "lifecycle_authority",
    "lifecycle_owner",
}
REQUIRED_REVIEW_ROLES = ("architect", "tests", "quality")
REVIEW_DONE_RE = re.compile(r"^REVIEW_DONE:([1-9][0-9]*):([A-Za-z][A-Za-z0-9_-]*):(approve|comment|reject)$")
REVIEW_ARTIFACT_RE = re.compile(r"^review-pr([1-9][0-9]*)-([A-Za-z][A-Za-z0-9_-]*)-r([1-9][0-9]*)\.md$")
REVIEW_LOG_RE = re.compile(r"^review-pr([1-9][0-9]*)-([A-Za-z][A-Za-z0-9_-]*)-r([1-9][0-9]*)\.log$")
REVIEW_HEAD_RE = re.compile(r"(?im)^(?:reviewed[-_ ]?head[-_ ]?sha|head[-_ ]?sha|headRefOid|REVIEW_HEAD_SHA)\s*[:=]\s*([0-9a-f]{7,64})\s*$")
CONSENSUS_JUDGE_ARTIFACT_RE = re.compile(r"^phase9-issue([1-9][0-9]*)-r([1-9][0-9]*)-judge\.md$")
TARGET_TEXT_PATTERNS = (
    (re.compile(r"(?i)\bPR\s*#([1-9][0-9]*)\b"), "PR"),
    (re.compile(r"(?i)\bissue\s*#([1-9][0-9]*)\b"), "issue"),
    (re.compile(r"\bphase9-" + r"issue([1-9][0-9]*)-r[1-9][0-9]*-[A-Za-z][A-Za-z0-9_-]*\b"), "issue"),
    (re.compile(r"\breview-pr([1-9][0-9]*)-[A-Za-z][A-Za-z0-9_-]*-r[1-9][0-9]*\b"), "PR"),
    (re.compile(r"\bfix-pr([1-9][0-9]*)(?:-r|-round-)"), "PR"),
)
SUPPORTED_CONTROLLER_ACTIONS = {
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
# Worker-dispatch (non-lifecycle) controller actions that may batch up to the
# per-tick spawn budget. Both directly spawn codex workers and carry no
# lifecycle authority, so batching them only fills the concurrency floor faster
# and never batches review/merge/close/release lifecycle actions.
SPAWN_BATCH_CONTROLLER_ACTIONS = frozenset(
    {"spawn_codex_harness_background"}
)


@dataclass(frozen=True)
class RunnerResult:
    action_id: str
    status: str
    reason: str = ""


@dataclass(frozen=True)
class WakeupApplyBudget:
    spawn_budget: int
    source: str

    @classmethod
    def from_plan(cls, plan: Mapping[str, Any]) -> "WakeupApplyBudget":
        hard_gate = plan.get("hard_gate")
        if not isinstance(hard_gate, Mapping):
            concurrency_hard_gate = plan.get("concurrency")
            if isinstance(concurrency_hard_gate, Mapping):
                hard_gate = concurrency_hard_gate.get("hard_gate")
        concurrency = plan.get("concurrency")
        if not isinstance(hard_gate, Mapping) or not isinstance(concurrency, Mapping):
            return cls.legacy()
        if hard_gate.get("active") is not True:
            return cls.legacy()
        dispatch_required = _positive_int(hard_gate.get("dispatch_required"))
        deficit = _positive_int(concurrency.get("deficit"))
        if dispatch_required is None or deficit is None:
            return cls.legacy()
        return cls(min(dispatch_required, deficit), "hard_gate.dispatch_required/concurrency.deficit")

    @classmethod
    def legacy(cls) -> "WakeupApplyBudget":
        return cls(1, "legacy-single-apply")

    def is_spawn_action(self, action: Mapping[str, Any]) -> bool:
        return action.get("controller_action") in SPAWN_BATCH_CONTROLLER_ACTIONS


@dataclass(frozen=True)
class ReviewEvidence:
    role: str
    round_number: int
    verdict: str
    head_sha: str
    source: str
    valid: bool = True
    reason: str = ""


@dataclass(frozen=True)
class ReviewGateSnapshot:
    verdicts_by_role: dict[str, str]
    heads_by_role: dict[str, str]
    live_head_sha: str
    invalid: list[str]

    @property
    def all_present(self) -> bool:
        return all(role in self.verdicts_by_role for role in REQUIRED_REVIEW_ROLES)

    @property
    def approve(self) -> int:
        return sum(1 for verdict in self.verdicts_by_role.values() if verdict == "approve")

    @property
    def reject(self) -> int:
        return sum(1 for verdict in self.verdicts_by_role.values() if verdict == "reject")

    @property
    def comment(self) -> int:
        return sum(1 for verdict in self.verdicts_by_role.values() if verdict == "comment")

    @property
    def reviewed_head_sha(self) -> str:
        if not self.live_head_sha:
            return ""
        for role in REQUIRED_REVIEW_ROLES:
            if self.heads_by_role.get(role) != self.live_head_sha:
                return ""
        return self.live_head_sha

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdicts": self.verdicts_by_role,
            "heads_by_role": self.heads_by_role,
            "all_present": self.all_present,
            "approve": self.approve,
            "reject": self.reject,
            "comment": self.comment,
            "reviewed_head_sha": self.reviewed_head_sha,
            "live_head_sha": self.live_head_sha,
            "invalid": self.invalid,
        }


class WakeupRunner:
    def __init__(
        self,
        ctx: LoopContext,
        *,
        dry_run: bool = False,
        plan_loader: Callable[[Path], Mapping[str, Any]] | None = None,
        actions: ControllerActions | None = None,
        supervisor: ProcessSupervisor | None = None,
        command_runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.ctx = ctx
        self.dry_run = dry_run
        self.plan_loader = plan_loader or (lambda repo_root: build_plan(repo_root))
        self.actions = actions or ControllerActions(ctx)
        self.supervisor = supervisor or ProcessSupervisor()
        self.command_runner = command_runner or self._run_command
        self.ledger_path = ctx.paths.state / "wakeup-runner-ledger.jsonl"
        self.pending_events_path = ctx.paths.pending_events

    def run_once(self) -> list[RunnerResult]:
        if not graphql_headroom_ok(cwd=self.ctx.repo_root, env=self.ctx.env_for_subprocess()):
            log_graphql_backoff("wakeup-runner")
            return [RunnerResult("", "skipped", "graphql-backoff")]
        owner = require_active_controller(self.ctx, "wakeup-runner")
        write_active_controller_status(self.ctx, owner)
        if not owner.allowed:
            result = RunnerResult("", "noop", f"not-owner:{owner.status}")
            self._record(result, action=None)
            return [result]
        plan = dict(self.plan_loader(self.ctx.repo_root))
        plan_error = self._validate_plan(plan)
        if plan_error:
            result = RunnerResult("", "blocked", plan_error)
            self._record(result, action=None)
            return [result]
        budget = WakeupApplyBudget.from_plan(plan)
        results: list[RunnerResult] = []
        applied_spawns = 0
        for action in plan.get("actions", []):
            if not isinstance(action, dict) or action.get("status_only") is True:
                continue
            is_spawn_action = budget.is_spawn_action(action)
            consumes_spawn_budget = is_spawn_action or self._uses_spawn_budget(action)
            if consumes_spawn_budget and applied_spawns >= budget.spawn_budget:
                continue
            result = self.apply_action(action)
            results.append(result)
            if result.status == "skipped" and consumes_spawn_budget:
                continue
            if result.status != "applied":
                if result.status in {"blocked", "skipped"} and not consumes_spawn_budget:
                    continue
                if result.status == "blocked" and consumes_spawn_budget and not _spawn_launch_failure(result):
                    continue
                break
            if consumes_spawn_budget:
                applied_spawns += 1
                continue
            break
        return results

    def _uses_spawn_budget(self, action: Mapping[str, Any]) -> bool:
        controller_action = str(action.get("controller_action") or "")
        if controller_action == "dispatch_reviewers":
            return True
        if controller_action == "review_gate":
            if self._validate_action(action) is not None:
                return False
            return self._review_gate_decision(action).get("decision") == "FIX"
        return False

    def apply_action(self, action: Mapping[str, Any]) -> RunnerResult:
        action_id = str(action.get("action_id") or "")
        if not action_id:
            return self._blocked(action, "missing_action_id")
        if self._ledger_has(action):
            return self._record(RunnerResult(action_id, "skipped", "duplicate"), action)
        error = self._validate_action(action)
        if error:
            return self._blocked(action, error)
        if self.dry_run:
            return self._record(RunnerResult(action_id, "dry-run"), action)

        controller_action = str(action.get("controller_action") or "")
        try:
            exit_code = self._dispatch(controller_action, action)
        except Exception as exc:
            return self._blocked(action, f"exception:{exc}")
        status = "applied" if exit_code == 0 else "blocked"
        reason = "" if exit_code == 0 else f"helper_exit:{exit_code}"
        if exit_code != 0:
            self._append_pending_event(f"WAKEUP_RUNNER_HELPER_EXIT:{action_id}:{controller_action}:{exit_code}")
        return self._record(RunnerResult(action_id, status, reason), action)

    def _validate_plan(self, plan: Mapping[str, Any]) -> str | None:
        if plan.get("schema") != "wakeup-plan":
            return "schema_mismatch"
        if plan.get("mode") != "closed-action-projection":
            return "mode_not_closed_action_projection"
        if plan.get("apply_authority") != APPLY_AUTHORITY:
            return "apply_authority_mismatch"
        if plan.get("no_lifecycle_authority") is not True:
            return "missing_no_lifecycle_authority"
        if not isinstance(plan.get("actions"), list):
            return "actions_not_list"
        return None

    def _validate_action(self, action: Mapping[str, Any]) -> str | None:
        forbidden = sorted(FORBIDDEN_ACTION_FIELDS.intersection(action))
        if forbidden:
            return "forbidden_fields:" + ",".join(forbidden)
        if "target_ref" in action and action.get("controller_action") != "publish_release_candidate":
            return "forbidden_fields:target_ref"
        if action.get("runner_authority") != RUNNER_AUTHORITY:
            return "runner_authority_mismatch"
        if action.get("no_generic_command") is not True:
            return "missing_no_generic_command"
        if not isinstance(action.get("preconditions"), list) or not action.get("preconditions"):
            return "missing_preconditions"
        if not action.get("source_marker") and not action.get("source_artifact"):
            return "missing_source_evidence"
        evidence_error = self._validate_evidence(action)
        if evidence_error:
            return evidence_error
        target_error = self._validate_target(action)
        if target_error:
            return target_error
        return self._validate_controller_action(action)

    def _validate_evidence(self, action: Mapping[str, Any]) -> str | None:
        source_artifact = str(action.get("source_artifact") or "")
        source_marker = str(action.get("source_marker") or "")
        if source_artifact.startswith(".refactor-loop/") and source_marker:
            path = self.ctx.repo_root / source_artifact
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return "source_artifact_missing"
            if source_marker not in text:
                return "source_marker_missing"
        if "clean_exit_source_marker" in action.get("preconditions", []):
            path = self.ctx.repo_root / source_artifact
            if not _source_log_has_clean_marker(path, source_marker):
                return "clean_exit_marker_missing"
        return None

    def _validate_target(self, action: Mapping[str, Any]) -> str | None:
        target = self._github_target(action)
        if target is not None:
            kind, number = target
            if number <= 0:
                return "target_number_missing"
            live = self._live_target_state(kind.lower(), number)
            if live not in {"OPEN", "open"}:
                return f"target_not_open:{live or 'unknown'}"
        return None

    def _github_target(self, action: Mapping[str, Any]) -> tuple[str, int] | None:
        kind = action.get("target_kind")
        number = action.get("target_number")
        if kind in {"PR", "issue"} and isinstance(number, int):
            return str(kind), number
        target = action.get("target")
        if isinstance(target, Mapping):
            target_kind = target.get("kind")
            target_number = target.get("number")
            if target_kind in {"PR", "issue"} and isinstance(target_number, int):
                return str(target_kind), target_number
        text_parts = []
        for field in ("item", "action_id", "source_marker", "source_artifact", "prompt", "log"):
            value = action.get(field)
            if isinstance(value, str) and value:
                text_parts.append(value)
        target_from_text = _target_from_text(" ".join(text_parts))
        return target_from_text

    def _validate_controller_action(self, action: Mapping[str, Any]) -> str | None:
        controller_action = str(action.get("controller_action") or "")
        if controller_action not in SUPPORTED_CONTROLLER_ACTIONS:
            return f"unsupported_controller_action:{controller_action or 'missing'}"
        if controller_action == "spawn_codex_harness_background":
            return self._validate_spawn_codex(action)
        if controller_action == "safe_push":
            return self._validate_safe_push(action)
        if controller_action == "review_gate":
            return self._validate_review_gate(action)
        if controller_action == "publish_release_candidate":
            return self._validate_release(action)
        if controller_action == "dispatch_consensus_implementation":
            return self._validate_consensus_implementation(action)
        if controller_action == "publish_implementation_output":
            return self._validate_publish_implementation(action)
        if controller_action == "dispatch_reviewers":
            return self._validate_dispatch_reviewers(action)
        if controller_action == "open_release_rollup_pr_from_action":
            return self._validate_release_rollup(action)
        if controller_action == "close_managed_item_from_drop_marker":
            return self._validate_close_managed_drop(action)
        return None

    def _validate_spawn_codex(self, action: Mapping[str, Any]) -> str | None:
        preconditions = action.get("preconditions")
        if not isinstance(preconditions, list) or "target_log_absent" not in preconditions:
            return "spawn_missing_precondition:target_log_absent"
        log = Path(str(action.get("log") or ""))
        if self._spawn_log_suppresses_retry(log):
            return "target_log_exists"
        return None

    def _validate_safe_push(self, action: Mapping[str, Any]) -> str | None:
        preconditions = action.get("preconditions")
        if not isinstance(preconditions, list):
            return "safe_push_missing_preconditions"
        for required in ("verified_pr_head", "clean_scoped_diff"):
            if required not in preconditions:
                return f"safe_push_missing_precondition:{required}"
        target = action.get("target_number")
        if not isinstance(target, int):
            return "safe_push_target_missing"
        head_ref = str(action.get("head_ref") or "").strip()
        if not _safe_branch_name(head_ref):
            return "safe_push_invalid_head_ref"
        live_head = self._pr_head_ref(target)
        if live_head != head_ref:
            return f"safe_push_stale_head:{live_head or 'unknown'}"
        worktree = Path(str(action.get("worktree") or ""))
        if not worktree.is_absolute() or not worktree.is_dir():
            return "safe_push_worktree_missing"
        try:
            worktree.resolve().relative_to((self.ctx.repo_root / ".worktrees").resolve())
        except ValueError:
            return "safe_push_worktree_outside_controller_owned_root"
        clean = self.command_runner(["git", "-C", str(worktree), "diff", "--quiet"])
        if clean.returncode != 0:
            return "safe_push_dirty_scoped_diff"
        return None

    def _validate_close_managed_drop(self, action: Mapping[str, Any]) -> str | None:
        preconditions = action.get("preconditions")
        if not isinstance(preconditions, list):
            return "close_managed_drop_missing_preconditions"
        for required in ("active_controller_owner", "live_open_target", "live_managed_target"):
            if required not in preconditions:
                return f"close_managed_drop_missing_precondition:{required}"
        if not str(action.get("source_marker") or "").startswith("META_RESOLVED:drop:"):
            return "close_managed_drop_invalid_marker"
        kind = str(action.get("target_kind") or "").lower()
        if kind not in {"issue", "pr"}:
            return "close_managed_drop_invalid_target_kind"
        number = action.get("target_number")
        if not isinstance(number, int):
            return "close_managed_drop_target_missing"
        if not self._live_target_has_managed_label(kind, number):
            return "close_managed_drop_target_not_managed"
        return None

    def _validate_review_gate(self, action: Mapping[str, Any]) -> str | None:
        decision = self._review_gate_decision(action)
        if decision["decision"] in {"MERGE", "MERGE_WITH_COMMENTS", "FIX"}:
            return None
        reason = str(decision["reason"] or "")
        return str(decision["decision"]) + (f":{reason}" if reason else "")

    def _validate_release(self, action: Mapping[str, Any]) -> str | None:
        candidate_path = str(action.get("candidate_path") or ".refactor-loop/state/release-candidate.json")
        target_ref = str(action.get("target_ref") or "")
        if not target_ref:
            return "release_target_ref_missing"
        result = ReleasePublishPreflight(self.ctx.repo_root).validate(candidate_path=candidate_path, target_ref=target_ref)
        if not result.allowed:
            return "release_preflight_denied:" + ",".join(result.reasons)
        return None

    def _validate_consensus_implementation(self, action: Mapping[str, Any]) -> str | None:
        preconditions = action.get("preconditions")
        if not isinstance(preconditions, list) or "durable_consensus_artifact" not in preconditions:
            return "consensus_implementation_missing_precondition:durable_consensus_artifact"
        artifact_error = self._validate_consensus_artifact(action)
        if artifact_error:
            return artifact_error
        for field in ("design_decision_path", "scope_paths", "old_pattern", "new_principle", "cluster_id", "iteration"):
            if not str(action.get(field) or "").strip():
                return f"consensus_implementation_missing_field:{field}"
        if str(action.get("design_decision_path") or "") != str(action.get("consensus_artifact") or ""):
            return "consensus_implementation_design_path_mismatch"
        readiness_reason = consensus_implementation_suppressed_reason(dict(action), self.ctx.repo_root)
        if readiness_reason:
            return f"consensus_implementation_not_ready:{readiness_reason}"
        return None

    def _validate_consensus_artifact(self, action: Mapping[str, Any]) -> str | None:
        raw_artifact = str(action.get("consensus_artifact") or "")
        if not raw_artifact:
            return "consensus_artifact_missing"
        try:
            artifact = self.ctx.artifact_execution_path(raw_artifact)
        except LoopContextError:
            return "consensus_artifact_invalid_path"
        try:
            artifact.relative_to((self.ctx.repo_root / ".refactor-loop" / "runs").resolve())
        except ValueError:
            return "consensus_artifact_outside_runs"
        issue = action.get("consensus_issue")
        round_no = action.get("consensus_round")
        if not isinstance(issue, int) or not isinstance(round_no, int):
            return "consensus_artifact_identity_missing"
        if action.get("target_kind") != "issue" or action.get("target_number") != issue:
            return "consensus_artifact_target_mismatch"
        identity = CONSENSUS_JUDGE_ARTIFACT_RE.fullmatch(artifact.name)
        if identity is None or int(identity.group(1)) != issue or int(identity.group(2)) != round_no:
            return "consensus_artifact_identity_mismatch"
        try:
            lines = artifact.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return "consensus_artifact_missing"
        if not any(line.startswith("META_JUDGE_DONE:consensus") for line in lines[-10:]):
            return "consensus_artifact_marker_missing"
        return None

    def _validate_publish_implementation(self, action: Mapping[str, Any]) -> str | None:
        marker = str(action.get("source_marker") or "")
        if not marker.startswith("IMPLEMENT_DONE:") or not marker.endswith(":ok"):
            return "publish_implementation_invalid_marker"
        preconditions = action.get("preconditions")
        if not isinstance(preconditions, list):
            return "publish_implementation_missing_preconditions"
        for required in (
            "clean_exit_source_marker",
            "canonical_implementation_identity",
            "fresh_integration_base",
            "clean_scoped_diff",
            "host_checks_green",
            "single_linked_managed_issue",
            "no_duplicate_open_pr",
        ):
            if required not in preconditions:
                return f"publish_implementation_missing_precondition:{required}"
        if action.get("target_kind") != "issue" or not isinstance(action.get("target_number"), int):
            return "publish_implementation_target_missing"
        if not self._live_target_has_managed_label("issue", int(action["target_number"])):
            return "publish_implementation_target_not_managed"
        duplicate_error = self._validate_no_duplicate_open_pr(action)
        if duplicate_error:
            return duplicate_error
        return self._validate_implementation_worktree(action)

    def _validate_dispatch_reviewers(self, action: Mapping[str, Any]) -> str | None:
        if action.get("target_kind") != "PR" or not isinstance(action.get("target_number"), int):
            return "dispatch_reviewers_target_missing"
        return None

    def _validate_release_rollup(self, action: Mapping[str, Any]) -> str | None:
        preconditions = action.get("preconditions")
        if not isinstance(preconditions, list) or "release_rollup_event" not in preconditions:
            return "release_rollup_missing_precondition:release_rollup_event"
        event = action.get("event")
        if not isinstance(event, dict):
            return "release_rollup_event_missing"
        if not str(event.get("integration_sha") or "").strip():
            return "release_rollup_integration_sha_missing"
        body_file = self.ctx.repo_root / str(action.get("body_file") or "")
        if not body_file.is_file():
            return "release_rollup_body_missing"
        return None

    def _validate_no_duplicate_open_pr(self, action: Mapping[str, Any]) -> str | None:
        head_ref = str(action.get("head_ref") or "").strip()
        if not _safe_branch_name(head_ref):
            return "publish_implementation_invalid_head_ref"
        result = self.command_runner(["gh", "pr", "list", "--state", "open", "--head", head_ref, "--json", "number"])
        if result.returncode != 0:
            return "publish_implementation_duplicate_pr_unavailable"
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return "publish_implementation_duplicate_pr_invalid_json"
        if not isinstance(payload, list):
            return "publish_implementation_duplicate_pr_invalid_json"
        if payload:
            return "publish_implementation_duplicate_open_pr"
        return None

    def _validate_implementation_worktree(self, action: Mapping[str, Any]) -> str | None:
        head_ref = str(action.get("head_ref") or "").strip()
        if not _safe_branch_name(head_ref):
            return "publish_implementation_invalid_head_ref"
        worktree = Path(str(action.get("worktree") or ""))
        if not worktree.is_absolute() or not worktree.is_dir():
            return "publish_implementation_worktree_missing"
        try:
            worktree.resolve().relative_to((self.ctx.repo_root / ".worktrees").resolve())
        except ValueError:
            return "publish_implementation_worktree_outside_controller_owned_root"
        identity_error = self._validate_canonical_implementation_identity(action, worktree, head_ref)
        if identity_error:
            return identity_error
        base_error = self._validate_fresh_implementation_base(worktree)
        if base_error:
            return base_error
        diff = self.command_runner(["git", "-C", str(worktree), "diff", "--quiet"])
        if diff.returncode == 0:
            return "publish_implementation_empty_scoped_diff"
        if diff.returncode != 1:
            return "publish_implementation_diff_unavailable"
        return None

    def _validate_canonical_implementation_identity(self, action: Mapping[str, Any], worktree: Path, head_ref: str) -> str | None:
        target = action.get("target_number")
        if not isinstance(target, int):
            return "publish_implementation_target_missing"
        marker = str(action.get("source_marker") or "")
        marker_id = marker.removeprefix("IMPLEMENT_DONE:").removesuffix(":ok").strip(":")
        candidate = marker_id.replace("_", "-").strip("-") or f"issue-{target}"
        expected_head = f"refactor/iter{target}-{candidate}"
        expected_worktree = (self.ctx.repo_root / ".worktrees" / f"iter{target}-{candidate}").resolve()
        if head_ref != expected_head or worktree.resolve() != expected_worktree:
            return "publish_implementation_noncanonical_identity"
        branch = self.command_runner(["git", "-C", str(worktree), "rev-parse", "--abbrev-ref", "HEAD"])
        if branch.returncode != 0 or branch.stdout.strip() != head_ref:
            return "publish_implementation_noncanonical_identity"
        return None

    def _validate_fresh_implementation_base(self, worktree: Path) -> str | None:
        integration = str(self.ctx.env_for_subprocess().get("INTEGRATION_BRANCH") or "").strip()
        if not integration:
            return "publish_implementation_integration_branch_missing"
        merge_base = self.command_runner(["git", "-C", str(worktree), "merge-base", "HEAD", f"origin/{integration}"])
        current = self.command_runner(["git", "-C", str(worktree), "rev-parse", "--verify", f"origin/{integration}"])
        if merge_base.returncode != 0 or current.returncode != 0:
            return "publish_implementation_base_unavailable"
        if merge_base.stdout.strip() != current.stdout.strip():
            return "publish_implementation_stale_base"
        return None

    def _dispatch(self, controller_action: str, action: Mapping[str, Any]) -> int:
        if controller_action == "spawn_codex_harness_background":
            return self._spawn_codex(action)
        if controller_action == "safe_push":
            return self.actions.safe_push(branch=str(action.get("head_ref") or ""), worktree=str(action.get("worktree") or ""))
        if controller_action == "dispatch_consensus_implementation":
            return self.actions.dispatch_consensus_implementation(dict(action))
        if controller_action == "publish_implementation_output":
            return self.actions.publish_implementation_output(dict(action))
        if controller_action == "publish_worker_output_from_action":
            return self.actions.publish_worker_output_from_action(dict(action))
        if controller_action == "dispatch_reviewers":
            return self.actions.dispatch_reviewers(dict(action))
        if controller_action == "open_release_rollup_pr_from_action":
            return self.actions.open_release_rollup_pr_from_action(dict(action))
        if controller_action == "close_managed_item_from_drop_marker":
            return self.actions.close_managed_item_from_drop_marker(dict(action))
        if controller_action == "review_gate":
            decision = self._review_gate_decision(action)
            if decision["decision"] == "FIX":
                return self._dispatch_review_fix(int(action["target_number"]))
            if decision["decision"] in {"MERGE", "MERGE_WITH_COMMENTS"}:
                return self.actions.merge_pr(str(action["target_number"]))
            self._append_pending_event(
                f"WAKEUP_RUNNER_REVIEW_GATE_WAIT:{action.get('target_number')}:{decision['decision']}:{decision['reason']}"
            )
            return 3
        if controller_action == "publish_release_candidate":
            result = self.actions.publish_release_candidate(
                candidate_path=str(action.get("candidate_path") or ".refactor-loop/state/release-candidate.json"),
                target_ref=str(action.get("target_ref") or ""),
            )
            return 0 if result.published else 3
        self._append_pending_event(f"WAKEUP_RUNNER_UNAPPLIED:{controller_action}:{action.get('action_id')}")
        return 0

    def _spawn_codex(self, action: Mapping[str, Any]) -> int:
        if not graphql_headroom_ok(cwd=self.ctx.repo_root, env=self.ctx.env_for_subprocess()):
            self._append_pending_event(f"WAKEUP_RUNNER_SPAWN_BACKOFF:{action.get('action_id', '')}:graphql-headroom-low")
            log_graphql_backoff("wakeup-runner")
            return 3
        cd = Path(str(action.get("cd") or self.ctx.repo_root))
        prompt = Path(str(action.get("prompt") or ""))
        log = Path(str(action.get("log") or ""))
        stall = int(action.get("stall") or 5400)
        if not prompt.is_file():
            return 2
        self._clear_redispatchable_spawn_log(log)
        exit_code = self._launch_spawn_codex_supervisor(cd=cd, prompt=prompt, log=log, stall=stall)
        if exit_code != 0:
            self._append_pending_event(f"WAKEUP_RUNNER_SPAWN_LAUNCH_EXIT:{action.get('action_id', '')}:{exit_code}")
        return exit_code

    def _spawn_log_suppresses_retry(self, log: Path) -> bool:
        if is_implement_log(log):
            state = classify_implement_attempt(repo_root=self.ctx.repo_root, log_path=log, command_runner=self.command_runner)
            return state.in_flight or state.publish_ready
        return _spawn_log_suppresses_retry(log)

    def _clear_redispatchable_spawn_log(self, log: Path) -> None:
        if not is_implement_log(log):
            return
        clear_redispatchable_implement_log(repo_root=self.ctx.repo_root, log_path=log, command_runner=self.command_runner)

    def _launch_spawn_codex_supervisor(self, *, cd: Path, prompt: Path, log: Path, stall: int) -> int:
        return launch_spawn_codex_supervisor(
            repo_root=self.ctx.repo_root,
            cd=cd,
            prompt=prompt,
            log=log,
            stall=stall,
            env=self.ctx.env_for_subprocess(),
        )

    def _dispatch_review_fix(self, pr_number: int) -> int:
        round_number = self._next_fix_round(pr_number)
        spec = self.actions.render_review_fix_prompt(pr_number, round_number)
        return launch_spawn_codex_supervisor(
            repo_root=self.ctx.repo_root,
            cd=self.ctx.repo_root,
            prompt=self.ctx.repo_root / spec.prompt_path,
            log=self.ctx.repo_root / spec.log_path,
            stall=5400,
        )

    def _review_gate_decision(self, action: Mapping[str, Any]) -> dict[str, Any]:
        target = action.get("target_number")
        if not isinstance(target, int):
            return {"decision": "WAIT_OR_REDISPATCH", "reason": "review_target_missing"}
        gate = self._review_gate(target)
        if gate["invalid"]:
            return {"decision": "WAIT_OR_REDISPATCH", "reason": f"invalid_reviewer_evidence:{gate['invalid'][0]}", "gate": gate}
        if not gate["all_present"]:
            return {"decision": "WAIT_OR_REDISPATCH", "reason": "missing_reviewers", "gate": gate}
        action_head = str(action.get("head_sha") or "").strip()
        live_head = str(gate.get("live_head_sha") or "").strip()
        if not action_head:
            return {"decision": "WAIT_OR_REDISPATCH", "reason": "missing_action_reviewed_head_sha", "gate": gate}
        if not live_head:
            return {"decision": "WAIT_OR_REDISPATCH", "reason": "missing_live_head_sha", "gate": gate}
        if action_head != live_head:
            return {"decision": "WAIT_OR_REDISPATCH", "reason": "action_head_mismatch", "gate": gate}
        ci_error = self._review_gate_ci_error(target, live_head)
        if ci_error:
            return {"decision": "WAIT_OR_REDISPATCH", "reason": ci_error, "gate": gate}
        mergeability_error = self._review_gate_mergeability_error(target)
        if mergeability_error:
            return {"decision": "WAIT_OR_REDISPATCH", "reason": mergeability_error, "gate": gate}
        if gate["reject"] > 0:
            return {"decision": "FIX", "reason": "", "gate": gate}
        if gate["approve"] < 1:
            return {"decision": "WAIT_EXPLICIT_APPROVAL", "reason": "no_approval", "gate": gate}
        decision = "MERGE" if gate["comment"] == 0 else "MERGE_WITH_COMMENTS"
        return {"decision": decision, "reason": "", "gate": gate}

    def _review_gate(self, pr_number: int) -> dict[str, Any]:
        evidences = self._latest_review_evidence_by_role(pr_number)
        verdicts = {role: evidence.verdict for role, evidence in evidences.items() if evidence.valid}
        invalid = [evidence.reason or f"invalid:{evidence.role}" for evidence in evidences.values() if not evidence.valid]
        heads = {role: evidence.head_sha for role, evidence in evidences.items() if evidence.valid and evidence.head_sha}
        live_head = self._pr_head_sha(pr_number)
        for role in REQUIRED_REVIEW_ROLES:
            if role not in verdicts:
                continue
            role_head = heads.get(role, "")
            if not role_head:
                invalid.append(f"missing_reviewed_head_sha:{role}")
            elif live_head and role_head != live_head:
                invalid.append(f"stale_reviewed_head_sha:{role}")
        return ReviewGateSnapshot(
            verdicts_by_role=verdicts,
            heads_by_role=heads,
            live_head_sha=live_head,
            invalid=invalid,
        ).as_dict()

    def _latest_review_evidence_by_role(self, pr_number: int) -> dict[str, ReviewEvidence]:
        latest: dict[str, ReviewEvidence] = {}
        duplicate_keys: set[tuple[str, int]] = set()
        for evidence in self._review_evidences(pr_number):
            key = (evidence.role, evidence.round_number)
            if key in duplicate_keys:
                continue
            existing = latest.get(evidence.role)
            if existing is not None and existing.round_number == evidence.round_number:
                latest[evidence.role] = ReviewEvidence(
                    role=evidence.role,
                    round_number=evidence.round_number,
                    verdict="",
                    head_sha="",
                    source=evidence.source,
                    valid=False,
                    reason=f"duplicate_reviewer_evidence:{evidence.role}",
                )
                duplicate_keys.add(key)
                continue
            if existing is None or evidence.round_number > existing.round_number:
                latest[evidence.role] = evidence
        return latest

    def _review_evidences(self, pr_number: int) -> list[ReviewEvidence]:
        evidences: list[ReviewEvidence] = []
        artifact_keys: set[tuple[str, int]] = set()
        for path in sorted(self.ctx.paths.runs.glob(f"review-pr{pr_number}-*-r*.md")):
            match = REVIEW_ARTIFACT_RE.match(path.name)
            if not match or int(match.group(1)) != pr_number:
                continue
            role = match.group(2)
            round_number = int(match.group(3))
            evidence = self._review_evidence_from_artifact(path, pr_number, role, round_number)
            evidences.append(evidence)
            artifact_keys.add((role, round_number))
        for path in sorted(self.ctx.paths.logs.glob(f"review-pr{pr_number}-*-r*.log")):
            match = REVIEW_LOG_RE.match(path.name)
            if not match or int(match.group(1)) != pr_number:
                continue
            role = match.group(2)
            round_number = int(match.group(3))
            if (role, round_number) in artifact_keys:
                continue
            evidences.append(self._review_evidence_from_log(path, pr_number, role, round_number))
        return evidences

    def _review_evidence_from_artifact(self, path: Path, pr_number: int, role: str, round_number: int) -> ReviewEvidence:
        text = path.read_text(encoding="utf-8", errors="replace")
        companion_log = self.ctx.paths.logs / f"review-pr{pr_number}-{role}-r{round_number}.log"
        if not _log_has_exit_zero(companion_log):
            return ReviewEvidence(role, round_number, "", "", str(path), False, f"missing_exit_zero:{role}")
        verdict_lines = re.findall(r"(?m)^verdict:\s*([A-Za-z][A-Za-z0-9_-]*)\s*$", text)
        if len(verdict_lines) != 1:
            return ReviewEvidence(role, round_number, "", "", str(path), False, f"invalid_verdict_count:{role}")
        verdict = verdict_lines[0]
        if verdict not in {"approve", "comment", "reject"}:
            return ReviewEvidence(role, round_number, "", "", str(path), False, f"invalid_verdict:{role}")
        marker_count = sum(
            1
            for line in text.splitlines()
            if REVIEW_DONE_RE.match(line.strip()) and line.strip().split(":")[1:3] == [str(pr_number), role]
        )
        if marker_count > 1:
            return ReviewEvidence(role, round_number, "", "", str(path), False, f"duplicate_review_marker:{role}")
        prompt_path = self.ctx.paths.prompts / path.name
        return ReviewEvidence(role, round_number, verdict, self._review_head_sha_for(prompt_path, companion_log, text), str(path))

    def _review_evidence_from_log(self, path: Path, pr_number: int, role: str, round_number: int) -> ReviewEvidence:
        text = path.read_text(encoding="utf-8", errors="replace")
        if not _log_has_exit_zero(path):
            return ReviewEvidence(role, round_number, "", "", str(path), False, f"missing_exit_zero:{role}")
        verdicts: list[str] = []
        invalid_marker = False
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("REVIEW_DONE:"):
                continue
            match = REVIEW_DONE_RE.match(stripped)
            if not match:
                invalid_marker = True
                continue
            if match.group(1) == str(pr_number) and match.group(2) == role:
                verdicts.append(match.group(3))
        if invalid_marker:
            return ReviewEvidence(role, round_number, "", "", str(path), False, f"invalid_review_marker:{role}")
        if len(verdicts) != 1:
            return ReviewEvidence(role, round_number, "", "", str(path), False, f"invalid_review_marker_count:{role}")
        prompt_path = self.ctx.paths.prompts / path.with_suffix(".md").name
        return ReviewEvidence(role, round_number, verdicts[0], self._review_head_sha_for(prompt_path, path, text), str(path))

    def _review_head_sha_for(self, prompt_path: Path, log_path: Path, evidence_text: str) -> str:
        head_sha = _extract_review_head_sha(evidence_text)
        if head_sha:
            return head_sha
        for path in (prompt_path, log_path):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            head_sha = _extract_review_head_sha(text)
            if head_sha:
                return head_sha
        return ""

    def _review_gate_ci_error(self, pr_number: int, live_head_sha: str) -> str | None:
        if not self.ctx.gh_repo_slug:
            return "missing_gh_repo_slug"
        status = PrChecksProjection(runner=self.command_runner).check_pr(self.ctx.gh_repo_slug, pr_number)
        if not status.ok:
            return f"ci_unavailable:{status.reason or 'unknown'}"
        if status.head_sha != live_head_sha:
            return "ci_stale_head_sha"
        if not status.runs:
            return "ci_missing_checks"
        if any(run.bucket == "pending" for run in status.runs):
            return "ci_pending"
        if any(run.bucket == "fail" for run in status.runs):
            return "ci_failed"
        return None

    def _review_gate_mergeability_error(self, pr_number: int) -> str | None:
        result = self.command_runner(["gh", "pr", "view", str(pr_number), "--json", "mergeable,isDraft"])
        if result.returncode != 0:
            return "mergeability_unavailable"
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return "mergeability_invalid_json"
        if not isinstance(payload, dict):
            return "mergeability_invalid_json"
        if payload.get("mergeable") != "MERGEABLE":
            return "non_mergeable_pr"
        return None

    def _next_fix_round(self, pr_number: int) -> int:
        rounds = []
        for path in self.ctx.paths.runs.glob(f"fix-pr{pr_number}-round-*-report.md"):
            match = re.search(rf"fix-pr{pr_number}-round-([1-9][0-9]*)-report\.md$", path.name)
            if match:
                rounds.append(int(match.group(1)))
        return (max(rounds) if rounds else 0) + 1

    def _live_target_state(self, kind: str, number: int) -> str:
        if self.ctx.gh_repo_slug:
            endpoint = "pulls" if kind == "pr" else "issues"
            result = self.command_runner(["gh", "api", f"repos/{self.ctx.gh_repo_slug}/{endpoint}/{number}"])
            if result.returncode == 0:
                try:
                    payload = json.loads(result.stdout or "{}")
                except json.JSONDecodeError:
                    return ""
                if isinstance(payload, dict):
                    if kind == "pr" and payload.get("merged") is True:
                        return "MERGED"
                    state = str(payload.get("state") or "").strip()
                    return state.upper() if state else ""
        result = self.command_runner(["gh", kind, "view", str(number), "--json", "state", "--jq", ".state"])
        return result.stdout.strip() if result.returncode == 0 else ""

    def _live_target_has_managed_label(self, kind: str, number: int) -> bool:
        result = self.command_runner(["gh", kind, "view", str(number), "--json", "labels,body"])
        if result.returncode != 0:
            return False
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return False
        raw_labels = payload.get("labels")
        if not isinstance(raw_labels, list):
            return False
        names = [item.get("name") for item in raw_labels if isinstance(item, dict)]
        return labels.MANAGED in labels.normalize_label_set(names).canonical

    def _pr_head_sha(self, pr_number: int) -> str:
        result = self.command_runner(["gh", "pr", "view", str(pr_number), "--json", "headRefOid", "--jq", ".headRefOid"])
        return result.stdout.strip() if result.returncode == 0 else ""

    def _pr_head_ref(self, pr_number: int) -> str:
        result = self.command_runner(["gh", "pr", "view", str(pr_number), "--json", "headRefName", "--jq", ".headRefName"])
        return result.stdout.strip() if result.returncode == 0 else ""

    def _run_command(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        full = build_gh_argv(self.ctx.gh_repo_slug, command)
        return subprocess.run(full, cwd=str(self.ctx.repo_root), capture_output=True, text=True, check=False)

    def _ledger_has(self, action: Mapping[str, Any]) -> bool:
        action_id = str(action.get("action_id") or "")
        if not self.ledger_path.exists():
            return False
        for line in self.ledger_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("action_id") != action_id:
                continue
            status = row.get("status")
            if status == "dry-run":
                return True
            if status == "applied":
                if self._applied_spawn_is_stale(action):
                    self._append_pending_event(f"WAKEUP_RUNNER_STALE_SPAWN_LEDGER:{action_id}:target-log-absent")
                    continue
                return True
            if status == "blocked" and _terminal_blocked_reason(str(row.get("reason") or "")):
                return True
        return False

    def _applied_spawn_is_stale(self, action: Mapping[str, Any]) -> bool:
        if action.get("controller_action") != "spawn_codex_harness_background":
            return False
        log = Path(str(action.get("log") or ""))
        return not log.is_absolute() or not log.exists()

    def _blocked(self, action: Mapping[str, Any], reason: str) -> RunnerResult:
        self._append_pending_event(f"WAKEUP_RUNNER_BLOCKED:{action.get('action_id', '')}:{reason}")
        return self._record(RunnerResult(str(action.get("action_id") or ""), "blocked", reason), action)

    def _record(self, result: RunnerResult, action: Mapping[str, Any] | None) -> RunnerResult:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "action_id": result.action_id,
            "status": result.status,
            "reason": result.reason,
            "kind": action.get("kind") if action else None,
        }
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        return result

    def _append_pending_event(self, line: str) -> None:
        self.pending_events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.pending_events_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _source_log_has_clean_marker(path: Path, marker: str) -> bool:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = None
    if lines is not None:
        try:
            marker_index = max(index for index, line in enumerate(lines) if marker in line)
            exit_index = max(index for index, line in enumerate(lines) if line == "EXIT=0")
            if marker_index < exit_index:
                return True
        except ValueError:
            pass
    return _implement_run_artifact_has_marker(path, marker)


def _implement_run_artifact_has_marker(log_path: Path, marker: str) -> bool:
    """Revalidation fallback mirroring wakeup_plan's implement artifact-marker
    recovery: a clean-exit implement worker may emit its IMPLEMENT_DONE marker
    only into the run artifact (runs/implement-issue-<id>.md) instead of the log
    tail, because codex stdout marker placement is not reliable. Accept the
    marker from the artifact for clean-exit implement-issue logs only, so the
    detection (wakeup_plan) and revalidation (wakeup_runner) sides stay
    consistent and a markerless-log implement does not block publish."""
    name = log_path.name
    if not (name.startswith("implement-issue-") and name.endswith(".log")):
        return False
    if not _log_has_exit_zero(log_path):
        return False
    artifact = log_path.parent.parent / "runs" / f"{name[: -len('.log')]}.md"
    try:
        return any(marker in line for line in artifact.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return False


def _log_has_exit_zero(path: Path) -> bool:
    try:
        return any(line == "EXIT=0" for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-5:])
    except OSError:
        return False


def _spawn_log_suppresses_retry(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-10:]
    except OSError:
        return True
    for line in reversed(lines):
        if not line.startswith("EXIT="):
            continue
        return line.strip() == "EXIT=0"
    return True


def _safe_branch_name(value: str) -> bool:
    return bool(value) and not value.startswith("-") and not any(ch.isspace() or ord(ch) < 32 for ch in value)


def _extract_review_head_sha(text: str) -> str:
    match = REVIEW_HEAD_RE.search(text)
    return match.group(1) if match else ""


def _target_from_text(text: str) -> tuple[str, int] | None:
    for pattern, kind in TARGET_TEXT_PATTERNS:
        match = pattern.search(text)
        if match:
            return kind, int(match.group(1))
    return None


def _terminal_blocked_reason(reason: str) -> bool:
    return reason in {"target_not_open:CLOSED", "target_not_open:MERGED"}


def _spawn_launch_failure(result: RunnerResult) -> bool:
    return result.reason.startswith("helper_exit:") or result.reason.startswith("exception:")


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _run_once_with_periodic_heartbeat(
    run_once: Callable[[], list[RunnerResult]],
    lease: DaemonHeartbeatLease,
) -> list[RunnerResult]:
    stop = threading.Event()

    def renew_lease() -> None:
        while not stop.wait(max(1.0, float(lease.heartbeat_interval))):
            lease.beat()

    renewer = threading.Thread(target=renew_lease, name="wakeup-runner-heartbeat-renewer", daemon=True)
    renewer.start()
    try:
        return run_once()
    finally:
        stop.set()
        renewer.join(timeout=1.0)


def load_plan_file(path: Path) -> Mapping[str, Any]:
    data = read_json(path, {})
    return data if isinstance(data, dict) else {}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="consensus-rnd-cli wakeup-runner")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--daemon", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repo-root")
    parser.add_argument("--plan-file")
    parser.add_argument("--interval-seconds", type=int, default=60)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.plan_file and not args.dry_run:
        sys.stderr.write("FATAL: --plan-file is dry-run/test-only and cannot apply side effects\n")
        return 2
    try:
        ctx = LoopContext.load(repo_root=args.repo_root, read_only=bool(args.dry_run), allow_git_root_fallback=bool(args.dry_run), cwd=os.getcwd())
    except LoopContextError as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 2
    plan_loader = (lambda _repo_root: load_plan_file(Path(args.plan_file))) if args.plan_file else None
    runner = WakeupRunner(ctx, dry_run=bool(args.dry_run), plan_loader=plan_loader)
    if args.daemon:
        lease = DaemonHeartbeatLease("wakeup_runner_daemon", ctx.repo_root)
        lease.beat()
        interval = max(1, int(args.interval_seconds))
        while True:
            results = _run_once_with_periodic_heartbeat(runner.run_once, lease)
            _log_tick_status("wakeup-runner", _wakeup_tick_action(results))
            lease.sleep_with_lease(interval)
    results = runner.run_once()
    _log_tick_status("wakeup-runner", _wakeup_tick_action(results))
    blocked = [result for result in results if result.status == "blocked"]
    return 3 if blocked else 0


def _wakeup_tick_action(results: Sequence[RunnerResult]) -> str:
    if not results:
        return "noop:no-actions"
    by_status: dict[str, int] = {}
    for result in results:
        by_status[result.status] = by_status.get(result.status, 0) + 1
    counts = ",".join(f"{key}={by_status[key]}" for key in sorted(by_status))
    # Surface blocked/skipped actions that would otherwise be hidden behind a
    # successful dispatch, so a single tick line shows what was applied AND what
    # was rejected (and why) without grepping the ledger. graphql-backoff is a
    # whole-tick gate, not a per-action reject, so it is reported on its own.
    notable = [
        result
        for result in results
        if result.status in ("blocked", "skipped")
        and result.reason != "graphql-backoff"
        and (result.reason or result.action_id)
    ]

    def _notable_suffix() -> str:
        if not notable:
            return ""
        head = notable[0]
        detail = f"{head.status}:{head.reason}" if head.reason else f"{head.status}:{head.action_id}"
        if head.reason and head.action_id:
            detail += f"({head.action_id[:56]})"
        more = f"+{len(notable) - 1}" if len(notable) > 1 else ""
        return f" | {detail}{more}"

    applied_spawns = [
        result
        for result in results
        if result.status == "applied"
        and (
            result.action_id.startswith("harness-spawn-intent:")
            or result.action_id.startswith("design-consensus-spawn:")
            or result.action_id.startswith("spawn:")
        )
    ]
    if applied_spawns:
        first_spawn = applied_spawns[0]
        suffix = f"+{len(applied_spawns) - 1}" if len(applied_spawns) > 1 else ""
        return f"dispatched {first_spawn.action_id or 'spawn'}{suffix}{_notable_suffix()} [{counts}]"
    first = results[0]
    if first.status == "applied":
        return f"dispatched {first.action_id or 'action'}{_notable_suffix()} [{counts}]"
    if first.status == "skipped" and first.reason == "graphql-backoff":
        return f"skip:graphql-backoff [{counts}]"
    if first.status == "noop":
        return f"noop:{first.reason or 'idle'}{_notable_suffix()} [{counts}]"
    if first.status == "blocked":
        head = notable[0] if notable else first
        detail = f"blocked:{head.reason or head.action_id or 'unknown'}"
        if head.reason and head.action_id:
            detail += f"({head.action_id[:56]})"
        more = f"+{len(notable) - 1}" if len(notable) > 1 else ""
        return f"{detail}{more} [{counts}]"
    return f"noop:{first.status} [{counts}]"


def _log_tick_status(daemon: str, action: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {daemon}: tick {action}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
