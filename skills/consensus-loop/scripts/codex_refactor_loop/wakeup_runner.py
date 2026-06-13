"""Apply the #396 wakeup-plan closed action projection."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .active_controller import require_active_controller, write_active_controller_status
from . import labels
from .consensus_gate import (
    ConsensusGateProofError,
    consensus_gate_digest,
    consensus_gate_file_digest,
    validate_consensus_gate_proof,
)
from .context import LoopContext, LoopContextError
from .controller_actions import ControllerActions
from .controller_actions import MERGE_READY_STATE
from .cross_instance_stand_down import check_cross_instance_admission
from .github_actor import GitHubAuthenticatedActor
from .gh_invoke import build_gh_argv
from .github_budget import graphql_headroom_ok
from .heartbeat import DaemonHeartbeatLease
from .implement_lifecycle import (
    _implement_run_artifact_done_marker,
    classify_implement_attempt,
    clear_redispatchable_implement_log,
    committed_implementation_delta,
    implement_attempt_is_terminal_or_noop_completion,
    is_implement_log,
)
from .implementation_pr_artifacts import validate_implementation_pr_artifacts
from .issue_decomposition import (
    IssueDecompositionBackoff,
    IssueDecompositionError,
    issue_decomposition_apply_proof_matches,
    issue_decomposition_plan_file_digest,
    load_issue_decomposition_plan,
    parse_issue_decomposition_tracking_comments,
    reconcile_issue_decomposition_tracking_children,
)
from .pr_checks import PrMergeReadinessProjection
from .processes import ProcessSupervisor, launch_spawn_codex_supervisor
from .review_gate_selection import extract_review_head_sha, parse_github_review_evidence, select_latest_live_head_review_evidence
from .release.gate import AutoReleaseGate
from .release.commits import write_release_commits
from .release.candidate_liveness import classify_release_candidate_liveness
from .release.publish_preflight import ReleasePublishPreflight
from .release.publisher import ReleasePublisher
from .release.required_checks import required_release_checks
from .safe_progress_scheduler import MEDIUM_NON_SPAWN_LIMIT_PER_TICK, validate_runner_action
from .state import read_json
from .work_items import extract_closing_issue_numbers
from .wakeup_plan import (
    ARCHIVED_INVALID_HARNESS_SPAWN_INTENT_MARKER,
    INVALID_HARNESS_SPAWN_INTENT_SOURCE_ARTIFACT,
    INVALID_HARNESS_SPAWN_INTENT_SOURCE_MARKER,
    build_plan,
    consensus_implementation_suppressed_reason,
    archived_invalid_harness_spawn_intent_markers,
    live_valid_harness_spawn_intent,
    zero_code_implementation_completion_proven,
)
from .worker_markers import read_worker_terminal_marker


RUNNER_AUTHORITY = "wakeup-runner-396"
APPLY_AUTHORITY = "wakeup-runner-396-only"
INVALID_HARNESS_CLEANUP_LIMIT_PER_TICK = 50
REBASE_RESOLVE_HELPER_EXIT_SUPPRESSION_THRESHOLD = 3
# #876: deterministic-fail backoff for publish_implementation_output. After this
# many recorded helper_exit failures for the same action_id, retries are paced to
# at most one per cooldown window measured from the latest failure, instead of a
# 15-minute publish-verify storm every tick. Time-based so it self-resets: once a
# retry stops failing (or the cooldown elapses with no fresh failure) the action
# is no longer backed off, so a later fix is never permanently blocked.
PUBLISH_HELPER_EXIT_BACKOFF_THRESHOLD = 3
PUBLISH_HELPER_EXIT_BACKOFF_COOLDOWN_SECONDS = 1800
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
# Worker-dispatch (non-lifecycle) controller actions that may batch up to the
# per-tick spawn budget. Both directly spawn codex workers and carry no
# lifecycle authority, so batching them only fills the concurrency floor faster
# and never batches review/merge/close/release lifecycle actions.
SPAWN_BATCH_CONTROLLER_ACTIONS = frozenset(
    {"spawn_codex_harness_background"}
)
SAME_TICK_TERMINAL_PR_WORKER_ACTIONS = frozenset(
    {
        "dispatch_reviewers",
        "publish_review_fix_output_from_action",
        "review_gate",
    }
)
REMOTE_CI_FIX_ATTEMPT_CAP = 2
REMOTE_CI_FIX_DONE_RE = re.compile(r"^REMOTE_CI_FIX_DONE:([^:]+):(ok|infra|blocked)$")
SAFE_CI_CHECK_TOKEN_RE = re.compile(r"[^A-Za-z0-9._-]+")
NON_BLOCKING_VALIDATION_REASONS = frozenset({"publish_implementation_empty_scoped_diff"})
NON_BLOCKING_SPAWN_VALIDATION_REASONS = frozenset({"target_log_exists"})
STAND_DOWN_MANAGED_WRITE_ACTIONS = frozenset(
    {
        "safe_push",
        "dispatch_consensus_implementation",
        "publish_implementation_output",
        "publish_worker_output_from_action",
        "publish_review_fix_output_from_action",
        "dispatch_reviewers",
        "dispatch_remote_ci_fix",
        "dispatch_pr_rebase_resolve",
        "commit_push_resolved_pr_rebase",
        "close_managed_item_from_drop_marker",
        "review_gate",
        "apply_issue_decomposition_plan",
        "apply_default_issue_intake_claim",
    }
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
    hard_gate_active: bool

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
        return cls(min(dispatch_required, deficit), "hard_gate.dispatch_required/concurrency.deficit", True)

    @classmethod
    def legacy(cls) -> "WakeupApplyBudget":
        return cls(1, "legacy-single-apply", False)

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
    terminal_failed: bool = False
    pending: bool = False
    created_at: str = ""
    source_index: int = 0
    comment_id: int | None = None


@dataclass(frozen=True)
class ReviewGateSnapshot:
    verdicts_by_role: dict[str, str]
    heads_by_role: dict[str, str]
    live_head_sha: str
    invalid: list[str]
    pending: list[str]
    terminal_failed_roles: list[str]

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
            "pending": self.pending,
            "terminal_failed_roles": self.terminal_failed_roles,
        }


@dataclass(frozen=True)
class ReviewGateCandidate:
    evidences_by_role: dict[str, ReviewEvidence]
    invalid: list[str]
    pending: list[str]
    terminal_failed_roles: list[str]
    complete_round: int | None = None


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
        self._same_tick_terminal_prs: set[int] = set()

    def run_once(self) -> list[RunnerResult]:
        self._same_tick_terminal_prs = set()
        if not graphql_headroom_ok(cwd=self.ctx.repo_root, env=self.ctx.env_for_subprocess()):
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
        applied_medium_non_spawns = 0
        archived_invalid_harness_cleanups = 0
        worker_top_up_only = False
        for action in self._actions_for_apply(plan.get("actions", []), budget):
            if not isinstance(action, dict) or action.get("status_only") is True:
                continue
            is_invalid_harness_cleanup = _is_invalid_harness_cleanup(action)
            if is_invalid_harness_cleanup and archived_invalid_harness_cleanups >= INVALID_HARNESS_CLEANUP_LIMIT_PER_TICK:
                continue
            if self._same_tick_terminal_target_reason(action):
                results.append(self.apply_action(action))
                continue
            is_spawn_action = budget.is_spawn_action(action)
            consumes_spawn_budget = is_spawn_action or self._uses_spawn_budget(action)
            if (
                action.get("risk_tier") == "medium"
                and not consumes_spawn_budget
                and applied_medium_non_spawns >= MEDIUM_NON_SPAWN_LIMIT_PER_TICK
            ):
                continue
            if worker_top_up_only and not consumes_spawn_budget and not _is_invalid_harness_cleanup(action):
                continue
            if consumes_spawn_budget and applied_spawns >= budget.spawn_budget:
                continue
            result = self.apply_action(action)
            results.append(result)
            if is_invalid_harness_cleanup and result.reason.startswith("archived_invalid_harness_spawn_intent:"):
                archived_invalid_harness_cleanups += 1
                continue
            if result.status == "skipped" and consumes_spawn_budget:
                continue
            if result.status != "applied":
                if result.status in {"blocked", "skipped"} and not consumes_spawn_budget:
                    continue
                if result.status == "blocked" and consumes_spawn_budget:
                    if not is_spawn_action or not _spawn_launch_failure(result):
                        continue
                break
            if consumes_spawn_budget:
                applied_spawns += 1
                continue
            if result.status == "applied" and action.get("risk_tier") == "medium":
                applied_medium_non_spawns += 1
            if budget.hard_gate_active and applied_spawns < budget.spawn_budget:
                worker_top_up_only = True
                continue
            break
        return results

    def _actions_for_apply(self, actions: Sequence[Any], budget: WakeupApplyBudget) -> list[Any]:
        if not budget.hard_gate_active:
            return sorted(actions, key=self._ordinary_apply_priority)
        ordered = sorted(actions, key=self._hard_gate_apply_priority)
        return self._with_merge_gate_preempting_same_pr_worker_dispatch(ordered)

    def _ordinary_apply_priority(self, action: Any) -> int:
        if isinstance(action, Mapping) and _is_invalid_harness_cleanup(action):
            return 0
        return 1

    def _hard_gate_apply_priority(self, action: Any) -> tuple[int, int]:
        if not isinstance(action, Mapping):
            return (4, 0)
        if _is_invalid_harness_cleanup(action):
            return (0, 0)
        if _publishes_review_fix_output(action):
            return (1, 0)
        if _unblocks_pr_mergeability(action) or _unblocks_release_rollup(action):
            return (2, 0)
        if _is_reviewer_harness_spawn_intent(action):
            return (3, 0)
        return (4, 0)

    def _uses_spawn_budget(self, action: Mapping[str, Any]) -> bool:
        controller_action = str(action.get("controller_action") or "")
        if controller_action in {"dispatch_reviewers", "dispatch_remote_ci_fix"}:
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
        suppressed_exit_count = self._rebase_resolve_helper_exit_2_blocked_count(action)
        if suppressed_exit_count >= REBASE_RESOLVE_HELPER_EXIT_SUPPRESSION_THRESHOLD:
            self._append_pending_event(
                "WAKEUP_RUNNER_HELPER_EXIT_SUPPRESSED:"
                f"{action_id}:dispatch_pr_rebase_resolve:2:{suppressed_exit_count}"
            )
            return self._record(RunnerResult(action_id, "skipped", "suppressed:helper_exit:2"), action)
        if self._publish_helper_exit_backoff_active(action):
            self._append_pending_event(
                f"WAKEUP_RUNNER_PUBLISH_BACKOFF:{action_id}:publish_implementation_output:"
                f"{PUBLISH_HELPER_EXIT_BACKOFF_COOLDOWN_SECONDS}"
            )
            return self._record(RunnerResult(action_id, "skipped", "backoff:publish_helper_exit"), action)
        if self._ledger_suppresses_retry(action):
            return self._record(RunnerResult(action_id, "skipped", "duplicate"), action)
        if action.get("kind") == "harness-spawn-intent-invalid":
            return self._apply_invalid_harness_spawn_intent(action, action_id)
        error = self._validate_action(action)
        if error:
            if error == "issue_decomposition_duplicate_sentinel" or error in NON_BLOCKING_VALIDATION_REASONS:
                return self._record(RunnerResult(action_id, "skipped", error), action)
            if (
                action.get("controller_action") == "spawn_codex_harness_background"
                and error in NON_BLOCKING_SPAWN_VALIDATION_REASONS
            ):
                return self._record(RunnerResult(action_id, "skipped", error), action)
            return self._blocked(action, error)
        same_tick_terminal_reason = self._same_tick_terminal_target_reason(action)
        if same_tick_terminal_reason:
            return self._record(RunnerResult(action_id, "skipped", same_tick_terminal_reason), action)
        if self.dry_run:
            return self._record(RunnerResult(action_id, "dry-run"), action)
        stand_down_reason = self._cross_instance_stand_down_reason(action)
        if stand_down_reason:
            return self._record(RunnerResult(action_id, "skipped", stand_down_reason), action)
        if action.get("capability") == "release-rollup-body":
            self._prepare_release_rollup_body_prompt(action)
        if action.get("capability") == "implementation-pr-artifact-repair":
            self._prepare_implementation_pr_artifact_repair_prompt(action)

        controller_action = str(action.get("controller_action") or "")
        try:
            exit_code = self._dispatch(controller_action, action)
        except Exception as exc:
            return self._blocked(action, f"exception:{exc}")
        if exit_code == 4 and controller_action == "apply_issue_decomposition_plan":
            return RunnerResult(action_id, "skipped", "graphql-backoff:apply_issue_decomposition_plan")
        status = "applied" if exit_code == 0 else "blocked"
        reason = "" if exit_code == 0 else f"helper_exit:{exit_code}"
        if exit_code == 0 and controller_action == "dispatch_consensus_implementation":
            self._append_consensus_implementation_dispatch_receipt(action)
        if exit_code != 0:
            self._append_pending_event(f"WAKEUP_RUNNER_HELPER_EXIT:{action_id}:{controller_action}:{exit_code}")
        return self._record(RunnerResult(action_id, status, reason), action)

    def _with_merge_gate_preempting_same_pr_worker_dispatch(self, actions: list[Any]) -> list[Any]:
        merge_gate_by_pr: dict[int, Any] = {}
        for action in actions:
            if not isinstance(action, Mapping):
                continue
            pr_number = self._merge_capable_review_gate_target(action)
            if pr_number is not None and pr_number not in merge_gate_by_pr:
                merge_gate_by_pr[pr_number] = action
        if not merge_gate_by_pr:
            return actions

        moved: set[int] = set()
        ordered: list[Any] = []
        for action in actions:
            if not isinstance(action, Mapping):
                ordered.append(action)
                continue
            pr_number = self._same_tick_terminal_pr_worker_target(action)
            gate = merge_gate_by_pr.get(pr_number) if pr_number is not None else None
            if gate is not None and id(gate) not in moved and action is not gate:
                ordered.append(gate)
                moved.add(id(gate))
            if id(action) not in moved:
                ordered.append(action)
                moved.add(id(action))
        return ordered

    def _merge_capable_review_gate_target(self, action: Mapping[str, Any]) -> int | None:
        if str(action.get("controller_action") or "") != "review_gate":
            return None
        target = action.get("target_number")
        if not isinstance(target, int) or target <= 0:
            return None
        if self._validate_action(action) is not None:
            return None
        decision = self._review_gate_decision(action)
        if decision.get("decision") in {"MERGE", "MERGE_WITH_COMMENTS"}:
            return target
        return None

    def _same_tick_terminal_target_reason(self, action: Mapping[str, Any]) -> str:
        pr_number = self._same_tick_terminal_pr_worker_target(action)
        if pr_number is None or pr_number not in self._same_tick_terminal_prs:
            return ""
        return "same_tick_terminal_target:MERGED"

    def _same_tick_terminal_pr_worker_target(self, action: Mapping[str, Any]) -> int | None:
        controller_action = str(action.get("controller_action") or "")
        if controller_action == "spawn_codex_harness_background":
            if not (_is_reviewer_harness_spawn_intent(action) or _is_fix_harness_spawn_intent(action)):
                return None
        elif controller_action not in SAME_TICK_TERMINAL_PR_WORKER_ACTIONS:
            return None
        target = self._github_target(action)
        if target is None:
            return None
        kind, number = target
        if kind.lower() != "pr" or number <= 0:
            return None
        return number

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
        safe_progress_error = validate_runner_action(action)
        if safe_progress_error:
            return safe_progress_error
        forbidden = _forbidden_action_field_paths(action)
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
        if (
            "clean_exit_source_marker" in action.get("preconditions", [])
            and action.get("controller_action") != "publish_release_candidate"
        ):
            path = self.ctx.repo_root / source_artifact
            if action.get("controller_action") in {"commit_push_resolved_pr_rebase", "dispatch_pr_rebase_resolve"}:
                if _source_log_has_clean_rebase_resolve_marker(path, source_marker):
                    return None
                return "clean_exit_marker_missing"
            if not _source_log_has_clean_marker(path, source_marker):
                return "clean_exit_marker_missing"
            return None
        if source_artifact.startswith(".refactor-loop/") and source_marker:
            path = self.ctx.repo_root / source_artifact
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return "source_artifact_missing"
            if source_marker not in text:
                return "source_marker_missing"
        return None

    def _apply_invalid_harness_spawn_intent(self, action: Mapping[str, Any], action_id: str) -> RunnerResult:
        error = self._validate_invalid_harness_spawn_intent(action)
        if error:
            return self._blocked(action, error)
        evidence_digest = str(action.get("evidence_digest") or "")
        if not evidence_digest:
            return self._blocked(action, "missing_evidence_digest")
        reason = _single_line(str(action.get("reason") or "unknown"))
        self._append_pending_event(f"{ARCHIVED_INVALID_HARNESS_SPAWN_INTENT_MARKER}:{evidence_digest}:{reason}")
        return self._record(RunnerResult(action_id, "skipped", f"archived_invalid_harness_spawn_intent:{reason}"), action)

    def _validate_invalid_harness_spawn_intent(self, action: Mapping[str, Any]) -> str | None:
        safe_progress_error = validate_runner_action(action)
        if safe_progress_error:
            return safe_progress_error
        forbidden = _forbidden_action_field_paths(action)
        if forbidden:
            return "forbidden_fields:" + ",".join(forbidden)
        if action.get("runner_authority") != RUNNER_AUTHORITY:
            return "runner_authority_mismatch"
        if action.get("no_generic_command") is not True:
            return "missing_no_generic_command"
        if action.get("no_lifecycle_authority") is not True:
            return "missing_no_lifecycle_authority"
        if action.get("controller_action") != "archive_invalid_harness_spawn_intent":
            return "invalid_harness_spawn_intent_controller_action_mismatch"
        if action.get("source_artifact") != INVALID_HARNESS_SPAWN_INTENT_SOURCE_ARTIFACT:
            return "invalid_harness_spawn_intent_source_artifact_mismatch"
        if action.get("source_marker") != INVALID_HARNESS_SPAWN_INTENT_SOURCE_MARKER:
            return "invalid_harness_spawn_intent_source_marker_mismatch"
        if not isinstance(action.get("evidence_digest"), str) or not action.get("evidence_digest"):
            return "missing_evidence_digest"
        return self._validate_evidence(action)

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
        if controller_action == "dispatch_release_candidate":
            return self._validate_release_dispatch(action)
        if controller_action == "publish_release_candidate":
            return self._validate_release(action)
        if controller_action == "apply_issue_decomposition_plan":
            return self._validate_issue_decomposition_apply(action)
        if controller_action == "apply_default_issue_intake_claim":
            return self._validate_default_issue_intake_claim(action)
        if controller_action == "dispatch_consensus_implementation":
            return self._validate_consensus_implementation(action)
        if controller_action == "publish_implementation_output":
            return self._validate_publish_implementation(action)
        if controller_action == "publish_review_fix_output_from_action":
            return self._validate_publish_review_fix_output(action)
        if controller_action == "dispatch_reviewers":
            return self._validate_dispatch_reviewers(action)
        if controller_action == "dispatch_remote_ci_fix":
            return self._validate_dispatch_remote_ci_fix(action)
        if controller_action == "dispatch_pr_rebase_resolve":
            return self._validate_dispatch_pr_rebase_resolve(action)
        if controller_action == "commit_push_resolved_pr_rebase":
            return self._validate_commit_push_resolved_pr_rebase(action)
        if controller_action == "open_release_rollup_pr_from_action":
            return self._validate_release_rollup(action)
        if controller_action == "auto_merge_release_rollup_pr_from_action":
            return self._validate_release_rollup_auto_merge(action)
        if controller_action == "close_managed_item_from_drop_marker":
            return self._validate_close_managed_drop(action)
        return None

    def _validate_spawn_codex(self, action: Mapping[str, Any]) -> str | None:
        preconditions = action.get("preconditions")
        if not isinstance(preconditions, list) or "target_log_absent" not in preconditions:
            return "spawn_missing_precondition:target_log_absent"
        if action.get("capability") == "release-rollup-body":
            body_error = self._validate_release_rollup_body_spawn(action)
            if body_error:
                return body_error
        if action.get("capability") == "implementation-pr-artifact-repair":
            repair_error = self._validate_implementation_pr_artifact_repair_spawn(action)
            if repair_error:
                return repair_error
        log = Path(str(action.get("log") or ""))
        if self._spawn_log_suppresses_retry(log):
            return "target_log_exists"
        return None

    def _validate_release_rollup_body_spawn(self, action: Mapping[str, Any]) -> str | None:
        preconditions = action.get("preconditions")
        if not isinstance(preconditions, list):
            return "release_rollup_body_missing_preconditions"
        for required in ("release_rollup_event", "target_body_absent"):
            if required not in preconditions:
                return f"release_rollup_body_missing_precondition:{required}"
        event = action.get("event")
        if not isinstance(event, dict):
            return "release_rollup_body_event_missing"
        if not str(event.get("integration_sha") or "").strip():
            return "release_rollup_body_integration_sha_missing"
        body_file = self.ctx.repo_root / str(action.get("body_file") or "")
        try:
            body_file.resolve().relative_to(self.ctx.paths.runs.resolve())
        except ValueError:
            return "release_rollup_body_output_outside_runs"
        if body_file.is_file():
            return "release_rollup_body_exists"
        if Path(str(action.get("prompt") or "")).resolve() != (self.ctx.paths.prompts / "release-rollup-body.md").resolve():
            return "release_rollup_body_prompt_mismatch"
        return None

    def _prepare_release_rollup_body_prompt(self, action: Mapping[str, Any]) -> None:
        self.actions.render_release_rollup_body_prompt(action)

    def _validate_implementation_pr_artifact_repair_spawn(self, action: Mapping[str, Any]) -> str | None:
        preconditions = action.get("preconditions")
        if not isinstance(preconditions, list):
            return "implementation_pr_artifact_repair_missing_preconditions"
        for required in (
            "clean_exit_source_marker",
            "implementation_pr_artifacts_missing_or_invalid",
            "publish_implementation_output_status_only",
        ):
            if required not in preconditions:
                return f"implementation_pr_artifact_repair_missing_precondition:{required}"
        issue = action.get("issue_number")
        if not isinstance(issue, int) or issue <= 0:
            return "implementation_pr_artifact_repair_issue_missing"
        cluster_id = str(action.get("cluster_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", cluster_id):
            return "implementation_pr_artifact_repair_cluster_invalid"
        for field, suffix in (("title_file", "-title.txt"), ("body_file", "-body.md")):
            artifact = self.ctx.repo_root / str(action.get(field) or "")
            try:
                artifact.resolve().relative_to(self.ctx.paths.runs.resolve())
            except ValueError:
                return f"implementation_pr_artifact_repair_{field}_outside_runs"
            if not artifact.name.startswith(f"implementation-pr-{cluster_id}") or not artifact.name.endswith(suffix):
                return f"implementation_pr_artifact_repair_{field}_mismatch"
        prompt = Path(str(action.get("prompt") or ""))
        expected_prompt = self.ctx.paths.prompts / f"implementation-pr-artifacts-{cluster_id}.md"
        if prompt.resolve() != expected_prompt.resolve():
            return "implementation_pr_artifact_repair_prompt_mismatch"
        return None

    def _prepare_implementation_pr_artifact_repair_prompt(self, action: Mapping[str, Any]) -> None:
        self.actions.render_implementation_pr_artifact_repair_prompt(action)

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
        marker = str(action.get("source_marker") or "")
        zero_code_completion = "zero_code_implementation_completion" in preconditions
        if zero_code_completion:
            if not re.fullmatch(r"IMPLEMENT_DONE:issue-?[1-9][0-9]*:ok", marker):
                return "close_managed_drop_invalid_zero_code_marker"
            target_number = action.get("target_number")
            if not isinstance(target_number, int):
                return "close_managed_drop_target_missing"
            state = classify_implement_attempt(
                repo_root=self.ctx.repo_root,
                action=action,
                log_path=self.ctx.repo_root / str(action.get("source_artifact") or ""),
                integration_branch=str(self.ctx.host_env.get("INTEGRATION_BRANCH") or "auto-refact-dev"),
                command_runner=self.command_runner,
            )
            if not zero_code_implementation_completion_proven(
                action,
                self.ctx.repo_root,
                target_number,
                state,
                require_action_proof=True,
            ):
                return f"close_managed_drop_zero_code_not_empty_scoped_diff:{state.status}:{state.reason}"
        elif not marker.startswith("META_RESOLVED:drop:"):
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
        preflight = ReleasePublishPreflight(self.ctx.repo_root, runner=self._run_release_command)
        result = preflight.validate(candidate_path=candidate_path, target_ref=target_ref)
        if not result.allowed:
            if result.reasons == ("manifest_version_mismatch",) and ReleasePublisher(
                self.ctx.repo_root, preflight=preflight, runner=self._run_release_command
            ).can_validate_remote_reentry(result):
                return None
            return "release_preflight_denied:" + ",".join(result.reasons)
        return None

    def _run_release_command(self, command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return self.command_runner(command)

    def _validate_release_dispatch(self, action: Mapping[str, Any]) -> str | None:
        preconditions = action.get("preconditions")
        if not isinstance(preconditions, list):
            return "release_dispatch_missing_preconditions"
        for required in ("release_auto_opt_in", "release_gate_ready", "decision_artifact_only"):
            if required not in preconditions:
                return f"release_dispatch_missing_precondition:{required}"
        if self.ctx.host_env.get("RELEASE_AUTO_ENABLE") != "true":
            return "release_auto_opt_in_missing"
        candidate = self.ctx.paths.state / "release-candidate.json"
        if candidate.exists():
            decision = read_json(self.ctx.paths.state / "release-decision.json", {})
            liveness = classify_release_candidate_liveness(self.ctx.repo_root, read_json(candidate, {}), decision)
            if liveness.blocks_dispatch:
                return "release_candidate_already_exists"
            if not liveness.stale_reason or liveness.stale_reason not in preconditions:
                return "release_candidate_already_exists"
        if not str(action.get("from_version") or "").strip() or not str(action.get("to_version") or "").strip():
            return "release_dispatch_version_missing"
        return None

    def _validate_issue_decomposition_apply(self, action: Mapping[str, Any]) -> str | None:
        kind = action.get("kind")
        if kind not in (None, "completed-marker"):
            return f"unsupported_kind:{kind}"
        preconditions = action.get("preconditions")
        if not isinstance(preconditions, list):
            return "issue_decomposition_missing_preconditions"
        for required in (
            "clean_exit_source_marker",
            "durable_consensus_artifact",
            "plan_level_design_consensus_judge_artifact",
            "issue_decomposition_plan_digest_match",
            "live_parent_open_tracking",
            "github_sentinel_idempotency_owner",
        ):
            if required not in preconditions:
                return f"issue_decomposition_missing_precondition:{required}"
        target = action.get("target_number")
        if action.get("target_kind") != "issue" or not isinstance(target, int):
            return "issue_decomposition_parent_target_missing"
        if not self._live_target_has_managed_label("issue", target):
            return "issue_decomposition_parent_not_managed"
        plan_path = str(action.get("issue_decomposition_plan_path") or "")
        if not plan_path:
            return "issue_decomposition_plan_path_missing"
        try:
            plan = load_issue_decomposition_plan(self.ctx, plan_path)
            digest = issue_decomposition_plan_file_digest(self.ctx, plan_path)
        except IssueDecompositionError as exc:
            return f"issue_decomposition_plan_invalid:{exc}"
        if plan.parent_issue != target:
            return "issue_decomposition_parent_mismatch"
        if digest != str(action.get("issue_decomposition_plan_digest") or ""):
            return "issue_decomposition_digest_mismatch"
        consensus_artifact = str(action.get("consensus_artifact") or "")
        if not consensus_artifact or plan.source_consensus_artifact != consensus_artifact:
            return "issue_decomposition_consensus_artifact_mismatch"
        artifact_error = self._validate_consensus_artifact(action)
        if artifact_error:
            return "issue_decomposition_" + artifact_error
        plan_level_artifact = str(action.get("plan_level_design_consensus_judge_artifact") or "")
        if plan_level_artifact != consensus_artifact:
            return "issue_decomposition_plan_level_judge_artifact_mismatch"
        plan_level_log = _plan_level_judge_log_path(self.ctx.repo_root, plan_level_artifact)
        if plan_level_log is None:
            return "issue_decomposition_plan_level_judge_artifact_mismatch"
        if str(action.get("source_artifact") or "") != plan_level_log.relative_to(self.ctx.repo_root).as_posix():
            return "issue_decomposition_plan_level_judge_source_mismatch"
        if not _source_log_has_clean_marker(plan_level_log, str(action.get("source_marker") or "")):
            return "issue_decomposition_plan_level_judge_marker_missing"
        proof = str(action.get("issue_decomposition_proof") or "")
        if not issue_decomposition_apply_proof_matches(
            proof,
            consensus_artifact=consensus_artifact,
            plan_path=plan_path,
            digest=digest,
            parent_issue=target,
        ):
            return "issue_decomposition_proof_mismatch"
        proof_error = self._validate_issue_decomposition_consensus_gate_proof(action, digest)
        if proof_error:
            return proof_error
        tracking_error = self._issue_decomposition_tracking_error(plan, digest)
        if tracking_error:
            return tracking_error
        return None

    def _validate_issue_decomposition_consensus_gate_proof(
        self,
        action: Mapping[str, Any],
        digest: str,
    ) -> str | None:
        raw_proof_text = action.get("consensus_gate_proof")
        if not isinstance(raw_proof_text, str) or not raw_proof_text.strip():
            return "issue_decomposition_consensus_gate_proof_missing"
        try:
            raw_proof = json.loads(raw_proof_text)
        except json.JSONDecodeError as exc:
            return f"issue_decomposition_consensus_gate_proof_invalid:{exc}"
        if not isinstance(raw_proof, Mapping):
            return "issue_decomposition_consensus_gate_proof_invalid:ConsensusGateProof must be a JSON object"
        consensus_artifact = str(action.get("consensus_artifact") or "")
        plan_path = str(action.get("issue_decomposition_plan_path") or "")
        issue = action.get("consensus_issue")
        if not isinstance(issue, int):
            return "issue_decomposition_consensus_gate_proof_target_mismatch"
        target_payload = {
            "target_kind": "issue-decomposition-plan",
            "parent_issue": issue,
            "consensus_artifact": consensus_artifact,
            "plan_path": plan_path,
            "plan_digest": digest,
        }
        artifact_digests: dict[str, str] = {}
        try:
            artifact_digests[consensus_artifact] = consensus_gate_file_digest(self.ctx.artifact_execution_path(consensus_artifact))
            artifact_digests[plan_path] = digest
            validate_consensus_gate_proof(
                raw_proof,
                target_kind="issue-decomposition-plan",
                target_ref=plan_path,
                target_digest=consensus_gate_digest(target_payload),
                artifact_digests=artifact_digests,
                required_scope_paths=[".refactor-loop/runs"],
            )
        except (ConsensusGateProofError, LoopContextError, OSError) as exc:
            return f"issue_decomposition_consensus_gate_proof_invalid:{exc}"
        return None

    def _validate_default_issue_intake_claim(self, action: Mapping[str, Any]) -> str | None:
        if action.get("kind") != "default-issue-intake-claim":
            return f"unsupported_kind:{action.get('kind')}"
        preconditions = action.get("preconditions")
        if not isinstance(preconditions, list):
            return "default_issue_intake_missing_preconditions"
        for required in (
            "active_controller_owner",
            "default_issue_intake_enabled",
            "live_open_target",
            "non_pr_issue",
            "target_not_managed",
            "github_comment_claim_protocol",
        ):
            if required not in preconditions:
                return f"default_issue_intake_missing_precondition:{required}"
        target = action.get("target_number")
        if action.get("target_kind") != "issue" or not isinstance(target, int):
            return "default_issue_intake_target_missing"
        if str(self.ctx.host_env.get("DEFAULT_ISSUE_INTAKE_ENABLE") or "").strip().lower() in {"false", "0", "no", "off"}:
            return "default_issue_intake_disabled"
        issue_error = self._default_issue_intake_live_issue_error(target)
        if issue_error:
            return issue_error
        if self._live_target_has_managed_label("issue", target):
            return "default_issue_intake_target_already_managed"
        return None

    def _default_issue_intake_live_issue_error(self, issue_number: int) -> str | None:
        if not self.ctx.gh_repo_slug:
            return "default_issue_intake_missing_gh_repo_slug"
        result = self.command_runner(["gh", "api", f"repos/{self.ctx.gh_repo_slug}/issues/{issue_number}"])
        if result.returncode != 0:
            return "default_issue_intake_issue_unavailable"
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return "default_issue_intake_issue_invalid_json"
        if not isinstance(payload, dict):
            return "default_issue_intake_issue_invalid_json"
        if str(payload.get("state") or "").strip().lower() != "open":
            return "default_issue_intake_target_not_open"
        if isinstance(payload.get("pull_request"), Mapping):
            return "default_issue_intake_target_is_pr"
        return None

    def _issue_decomposition_tracking_error(self, plan: Any, digest: str) -> str | None:
        parent_result = self.command_runner(["gh", "issue", "view", str(plan.parent_issue), "--json", "comments"])
        if parent_result.returncode != 0:
            return "issue_decomposition_parent_comments_unavailable"
        try:
            payload = json.loads(parent_result.stdout or "{}")
        except json.JSONDecodeError:
            return "issue_decomposition_parent_comments_invalid_json"
        comments = payload.get("comments") if isinstance(payload, dict) else None
        if not isinstance(comments, list):
            return "issue_decomposition_parent_comments_invalid_json"
        projection = parse_issue_decomposition_tracking_comments(
            [comment for comment in comments if isinstance(comment, Mapping)],
            expected_parent_issue=plan.parent_issue,
            expected_digest=digest,
        )
        if projection.conflicts:
            return "issue_decomposition_parent_tracking_conflict"
        try:
            reconcile_issue_decomposition_tracking_children(plan, digest, projection)
        except IssueDecompositionError:
            return "issue_decomposition_parent_tracking_conflict"
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
            "clean_scoped_diff",
            "single_linked_managed_issue",
            "worker_authored_pr_artifacts",
            "no_conflicting_open_implementation_pr",
        ):
            if required not in preconditions:
                return f"publish_implementation_missing_precondition:{required}"
        if action.get("target_kind") != "issue" or not isinstance(action.get("target_number"), int):
            return "publish_implementation_target_missing"
        if not self._live_target_has_managed_label("issue", int(action["target_number"])):
            return "publish_implementation_target_not_managed"
        artifact_error = self._validate_implementation_pr_artifacts(action)
        if artifact_error:
            return artifact_error
        worktree_error = self._validate_implementation_worktree(action)
        if worktree_error:
            return worktree_error
        return self._validate_matching_open_implementation_pr(action)

    def _validate_implementation_pr_artifacts(self, action: Mapping[str, Any]) -> str | None:
        target = action.get("target_number")
        if not isinstance(target, int):
            return "publish_implementation_target_missing"
        validation = validate_implementation_pr_artifacts(self.ctx.repo_root, self.ctx.paths.runs, action, target)
        if not validation.reason:
            return None
        return _publish_implementation_artifact_reason(validation.reason)

    def _validate_dispatch_reviewers(self, action: Mapping[str, Any]) -> str | None:
        if action.get("target_kind") != "PR" or not isinstance(action.get("target_number"), int):
            return "dispatch_reviewers_target_missing"
        return None

    def _validate_publish_review_fix_output(self, action: Mapping[str, Any]) -> str | None:
        if action.get("target_kind") != "PR" or not isinstance(action.get("target_number"), int):
            return "publish_review_fix_output_target_missing"
        marker = str(action.get("source_marker") or "")
        if not marker.startswith("FIX_DONE:"):
            return "publish_review_fix_output_marker_missing"
        return None

    def _validate_dispatch_remote_ci_fix(self, action: Mapping[str, Any]) -> str | None:
        if action.get("target_kind") != "PR" or not isinstance(action.get("target_number"), int):
            return "dispatch_remote_ci_fix_target_missing"
        marker = str(action.get("source_marker") or "")
        if marker.startswith("REMOTE_CI_FIX_DONE:"):
            match = REMOTE_CI_FIX_DONE_RE.fullmatch(marker)
            if match is None:
                return "dispatch_remote_ci_fix_invalid_marker"
            return None
        preconditions = action.get("preconditions")
        if not isinstance(preconditions, list):
            return "dispatch_remote_ci_fix_missing_preconditions"
        for required in ("active_controller_owner", "live_open_target", "target_required_checks_red"):
            if required not in preconditions:
                return f"dispatch_remote_ci_fix_missing_precondition:{required}"
        if not str(action.get("head_sha") or "").strip():
            return "dispatch_remote_ci_fix_head_sha_missing"
        if not str(action.get("check_name") or "").strip():
            return "dispatch_remote_ci_fix_check_name_missing"
        return None

    def _validate_dispatch_pr_rebase_resolve(self, action: Mapping[str, Any]) -> str | None:
        if action.get("target_kind") != "PR" or not isinstance(action.get("target_number"), int):
            return "dispatch_pr_rebase_resolve_target_missing"
        preconditions = action.get("preconditions")
        if not isinstance(preconditions, list):
            return "dispatch_pr_rebase_resolve_missing_preconditions"
        required_sets = (
            ("active_controller_owner", "live_managed_target", "base_ahead_pr_branch"),
            (
                "active_controller_owner",
                "clean_exit_source_marker",
                "live_managed_target",
                "false_done_unresolved_or_not_commit_ready",
            ),
        )
        recovery_marker = str(action.get("source_marker") or "")
        is_false_done_recovery = (
            str(action.get("action_id") or "").startswith("dispatch-pr-rebase-resolve:false-done:")
            or action.get("reason") == "rebase_resolve_done_without_resolved_merge"
            or recovery_marker.startswith("REBASE_RESOLVE_DONE:")
        )
        allowed_required_sets = (required_sets[1],) if is_false_done_recovery else (required_sets[0],)
        if not any(all(required in preconditions for required in required_set) for required_set in allowed_required_sets):
            missing = [required for required in allowed_required_sets[0] if required not in preconditions]
            return f"dispatch_pr_rebase_resolve_missing_precondition:{missing[0]}"
        target = int(action["target_number"])
        if not self._live_target_has_managed_label("pr", target):
            return "dispatch_pr_rebase_resolve_target_not_managed"
        head_ref = str(action.get("head_ref") or "").strip()
        if not _managed_pr_head_ref(head_ref):
            return "dispatch_pr_rebase_resolve_invalid_head_ref"
        live_head = self._pr_head_ref(target)
        if live_head != head_ref:
            return f"dispatch_pr_rebase_resolve_stale_head:{live_head or 'unknown'}"
        return None

    def _validate_commit_push_resolved_pr_rebase(self, action: Mapping[str, Any]) -> str | None:
        if action.get("target_kind") != "PR" or not isinstance(action.get("target_number"), int):
            return "commit_push_resolved_pr_rebase_target_missing"
        preconditions = action.get("preconditions")
        if not isinstance(preconditions, list):
            return "commit_push_resolved_pr_rebase_missing_preconditions"
        for required in ("active_controller_owner", "clean_exit_source_marker"):
            if required not in preconditions:
                return f"commit_push_resolved_pr_rebase_missing_precondition:{required}"
        marker = str(action.get("source_marker") or "")
        if not (
            re.fullmatch(r"REBASE_RESOLVE_DONE:[1-9][0-9]*:[^\s`]+", marker)
            or re.fullmatch(r"REBASE_RESOLVE_BLOCKED:[1-9][0-9]*:(?:conflict|human-decision|build-broken|other):[^\n]+", marker)
        ):
            return "commit_push_resolved_pr_rebase_invalid_marker"
        target = int(action["target_number"])
        if not self._live_target_has_managed_label("pr", target):
            return "commit_push_resolved_pr_rebase_target_not_managed"
        head_ref = str(action.get("head_ref") or "").strip()
        if not _managed_pr_head_ref(head_ref):
            return "commit_push_resolved_pr_rebase_invalid_head_ref"
        live_head = self._pr_head_ref(target)
        if live_head != head_ref:
            return f"commit_push_resolved_pr_rebase_stale_head:{live_head or 'unknown'}"
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

    def _validate_release_rollup_auto_merge(self, action: Mapping[str, Any]) -> str | None:
        if action.get("target_kind") != "PR" or not isinstance(action.get("target_number"), int):
            return "rollup_auto_merge_target_missing"
        preconditions = action.get("preconditions")
        if not isinstance(preconditions, list):
            return "rollup_auto_merge_missing_preconditions"
        for required in (
            "active_controller_owner",
            "live_open_target",
            "rollup_head_prefix",
            "review_base_target",
            "required_checks_green_exact_head",
            "rollup_auto_merge_enabled",
        ):
            if required not in preconditions:
                return f"rollup_auto_merge_missing_precondition:{required}"
        head_ref = str(action.get("head_ref") or "")
        if not _safe_branch_name(head_ref) or not head_ref.startswith("rollup/"):
            return "rollup_auto_merge_invalid_head_ref"
        head_sha = str(action.get("head_sha") or "").strip()
        if not re.fullmatch(r"[0-9A-Za-z._-]+", head_sha):
            return "rollup_auto_merge_invalid_head_sha"
        base_ref = str(action.get("base_ref") or "").strip()
        expected_base = str(self.ctx.host_env.get("REVIEW_BASE_BRANCH") or "").strip()
        if not expected_base or base_ref != expected_base:
            return "rollup_auto_merge_base_mismatch"
        return None

    def _validate_matching_open_implementation_pr(self, action: Mapping[str, Any]) -> str | None:
        head_ref = str(action.get("head_ref") or "").strip()
        if not _safe_branch_name(head_ref):
            return "publish_implementation_invalid_head_ref"
        result = self.command_runner(["gh", "pr", "list", "--state", "open", "--head", head_ref, "--json", "number,baseRefName,headRefName,labels,body"])
        if result.returncode != 0:
            return "publish_implementation_matching_pr_unavailable"
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return "publish_implementation_matching_pr_invalid_json"
        if not isinstance(payload, list):
            return "publish_implementation_matching_pr_invalid_json"
        if len(payload) == 0:
            return None
        if len(payload) > 1:
            return "publish_implementation_multiple_matching_open_pr"
        pr = payload[0]
        if not isinstance(pr, dict) or not isinstance(pr.get("number"), int):
            return "publish_implementation_matching_pr_invalid_json"
        if str(pr.get("headRefName") or "") != head_ref:
            return "publish_implementation_matching_pr_head_mismatch"
        base = str(pr.get("baseRefName") or "")
        integration_branch = str(getattr(self.actions, "integration_branch", "") or self.ctx.host_env.get("INTEGRATION_BRANCH", "")).strip()
        if integration_branch and base != integration_branch:
            return "publish_implementation_matching_pr_base_mismatch"
        raw_labels = pr.get("labels")
        if not isinstance(raw_labels, list):
            return "publish_implementation_matching_pr_not_managed"
        names = [item.get("name") for item in raw_labels if isinstance(item, dict)]
        if labels.MANAGED not in labels.normalize_label_set(names).canonical:
            return "publish_implementation_matching_pr_not_managed"
        target = action.get("target_number")
        if not isinstance(target, int):
            return "publish_implementation_target_missing"
        if _single_linked_issue_from_body(str(pr.get("body") or "")) != target:
            return "publish_implementation_matching_pr_issue_mismatch"
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
        status = self.command_runner(["git", "-C", str(worktree), "status", "--porcelain"])
        if status.returncode != 0:
            return "publish_implementation_diff_unavailable"
        if status.stdout.strip():
            return None
        diff = self.command_runner(["git", "-C", str(worktree), "diff", "HEAD", "--quiet"])
        if diff.returncode == 0:
            integration_branch = str(getattr(self.actions, "integration_branch", "") or self.ctx.host_env.get("INTEGRATION_BRANCH", "")).strip()
            if integration_branch:
                committed_delta = committed_implementation_delta(worktree, integration_branch, self.command_runner)
                if committed_delta is True:
                    return None
                if committed_delta is None:
                    return "publish_implementation_diff_unavailable"
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
        if controller_action == "publish_review_fix_output_from_action":
            fix_publish_rc = self._commit_and_push_review_fix_output(action)
            if fix_publish_rc != 0:
                return fix_publish_rc
            return self.actions.dispatch_reviewers(dict(action))
        if controller_action == "dispatch_reviewers":
            if str(action.get("source_marker") or "").startswith("FIX_DONE"):
                fix_publish_rc = self._commit_and_push_review_fix_output(action)
                if fix_publish_rc != 0:
                    return fix_publish_rc
            return self.actions.dispatch_reviewers(dict(action))
        if controller_action == "dispatch_remote_ci_fix":
            return self._dispatch_remote_ci_fix(action)
        if controller_action == "dispatch_pr_rebase_resolve":
            return self.actions.dispatch_pr_rebase_resolve(dict(action))
        if controller_action == "commit_push_resolved_pr_rebase":
            return self.actions.commit_push_resolved_pr_rebase(dict(action))
        if controller_action == "open_release_rollup_pr_from_action":
            return self.actions.open_release_rollup_pr_from_action(dict(action))
        if controller_action == "auto_merge_release_rollup_pr_from_action":
            return self.actions.auto_merge_release_rollup_pr_from_action(dict(action))
        if controller_action == "close_managed_item_from_drop_marker":
            return self.actions.close_managed_item_from_drop_marker(dict(action))
        if controller_action == "review_gate":
            decision = self._review_gate_decision(action)
            if decision["decision"] == "FIX":
                return self._dispatch_review_fix(int(action["target_number"]))
            if decision["decision"] in {"MERGE", "MERGE_WITH_COMMENTS"}:
                merge_rc = self.actions.merge_pr(str(action["target_number"]))
                if merge_rc == 0 and isinstance(action.get("target_number"), int):
                    self._same_tick_terminal_prs.add(int(action["target_number"]))
                return merge_rc
            self._append_pending_event(
                f"WAKEUP_RUNNER_REVIEW_GATE_WAIT:{action.get('target_number')}:{decision['decision']}:{decision['reason']}"
            )
            return 3
        if controller_action == "dispatch_release_candidate":
            return self._dispatch_release_candidate(action)
        if controller_action == "publish_release_candidate":
            result = self.actions.publish_release_candidate(
                candidate_path=str(action.get("candidate_path") or ".refactor-loop/state/release-candidate.json"),
                target_ref=str(action.get("target_ref") or ""),
            )
            return 0 if result.published else 3
        if controller_action == "apply_issue_decomposition_plan":
            try:
                self.actions.apply_issue_decomposition_plan(str(action.get("issue_decomposition_plan_path") or ""))
            except IssueDecompositionBackoff as exc:
                action_id = str(action.get("action_id") or "")
                diagnostic = _single_line(exc.diagnostic)
                self._append_pending_event(f"ISSUE_DECOMPOSITION_BACKOFF:{action_id}:{diagnostic}")
                return 4
            return 0
        if controller_action == "apply_default_issue_intake_claim":
            target = action.get("target_number")
            if not isinstance(target, int):
                return 2
            self.actions.apply_default_issue_intake_claim(target)
            return 0
        self._append_pending_event(f"WAKEUP_RUNNER_UNAPPLIED:{controller_action}:{action.get('action_id')}")
        return 0

    def _cross_instance_stand_down_reason(self, action: Mapping[str, Any]) -> str:
        controller_action = str(action.get("controller_action") or "")
        if controller_action not in STAND_DOWN_MANAGED_WRITE_ACTIONS:
            return ""
        target = self._github_target(action)
        if target is None:
            return ""
        kind, number = target
        normalized_kind = "pr" if kind == "PR" else kind.lower()
        actor = getattr(self.actions, "github_actor", None) or GitHubAuthenticatedActor(
            self.ctx,
            runner=lambda command, cwd: self.command_runner(command),
        )
        try:
            admission = actor.require_admission(f"wakeup-runner:{controller_action}")
        except RuntimeError as exc:
            return f"cross_instance_stand_down:{normalized_kind}:{number}:unavailable:{_single_line(str(exc))}"
        current_login = str(getattr(admission, "login", "") or "").strip()
        if not current_login:
            return f"cross_instance_stand_down:{normalized_kind}:{number}:unavailable:missing_current_login"
        if hasattr(self.actions, "cross_instance_admission"):
            result = self.actions.cross_instance_admission(
                normalized_kind,
                number,
                current_login,
                datetime.now(timezone.utc),
            )
        else:
            result = check_cross_instance_admission(
                self.ctx,
                normalized_kind,
                number,
                current_login,
                datetime.now(timezone.utc),
                runner=lambda command, cwd: self.command_runner(command),
            )
        if result.status == "allowed":
            return ""
        line = (
            f"CROSS_INSTANCE_STAND_DOWN:{controller_action}:{normalized_kind}:{number}:"
            f"current={current_login}:other={result.other_login}:source={result.source}:created_at={result.created_at}"
        )
        if result.reason:
            line = f"{line}:reason={_single_line(result.reason)}"
        self._append_pending_event(line)
        return (
            f"cross_instance_stand_down:{normalized_kind}:{number}:"
            f"{result.status}:{result.source}:{result.other_login or 'unknown'}:{_single_line(result.reason)}"
        )

    def _dispatch_release_candidate(self, action: Mapping[str, Any]) -> int:
        previous_env = os.environ.copy()
        try:
            os.environ.update(self.ctx.env_for_subprocess())
            release_target_ref = str(os.environ.get("RELEASE_TARGET_REF") or "").strip()
            if not release_target_ref:
                review_base = str(os.environ.get("REVIEW_BASE_BRANCH") or "").strip()
                if not review_base:
                    raise RuntimeError("missing required host branch env: REVIEW_BASE_BRANCH")
                release_target_ref = review_base if review_base.startswith("origin/") else f"origin/{review_base}"
            write_release_commits(
                self.ctx.repo_root,
                review_base_branch=str(os.environ.get("REVIEW_BASE_BRANCH") or "").strip(),
                target_ref=release_target_ref,
                fetch_tags=True,
            )
            gate = AutoReleaseGate(self.ctx.repo_root)
            stability = gate.compute_stability(
                min_recent_merges=_release_int(self.ctx.host_env.get("RELEASE_AUTO_MIN_MERGES"), 1)
            )
            decision = gate.decide_release(
                stability,
                _release_int(self.ctx.host_env.get("RELEASE_AUTO_MIN_INTERVAL_HOURS"), 24),
            )
            if decision.get("ready") is not True:
                self._append_pending_event("WAKEUP_RUNNER_RELEASE_DISPATCH_BLOCKED:not-ready")
                return 3
            if (
                str(decision.get("from_version") or "") != str(action.get("from_version") or "")
                or str(decision.get("to_version") or "") != str(action.get("to_version") or "")
            ):
                self._append_pending_event("WAKEUP_RUNNER_RELEASE_DISPATCH_BLOCKED:version-mismatch")
                return 3
            stale_reason = self._current_release_dispatch_stale_reason(action)
            if stale_reason:
                self._append_pending_event(
                    f"WAKEUP_RUNNER_RELEASE_DISPATCH_STALE_CANDIDATE:{action.get('action_id')}:{stale_reason}"
                )
            gate.dispatch_release(decision)
            return 0
        finally:
            os.environ.clear()
            os.environ.update(previous_env)

    def _spawn_codex(self, action: Mapping[str, Any]) -> int:
        if not graphql_headroom_ok(cwd=self.ctx.repo_root, env=self.ctx.env_for_subprocess()):
            self._append_pending_event(f"WAKEUP_RUNNER_SPAWN_BACKOFF:{action.get('action_id', '')}:graphql-headroom-low")
            _log_tick_status("wakeup-runner", "skip:graphql-backoff remaining=unknown")
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
            return state.in_flight or state.publish_ready or implement_attempt_is_terminal_or_noop_completion(state)
        return _spawn_log_suppresses_retry(log)

    def _clear_redispatchable_spawn_log(self, log: Path) -> None:
        if not is_implement_log(log):
            return
        clear_redispatchable_implement_log(repo_root=self.ctx.repo_root, log_path=log, command_runner=self.command_runner)

    def _launch_spawn_codex_supervisor(self, *, cd: Path, prompt: Path, log: Path, stall: int) -> int:
        return launch_spawn_codex_supervisor(
            repo_root=self.ctx.repo_root,
            skill_root=self.ctx.skill_root,
            cd=cd,
            prompt=prompt,
            log=log,
            stall=stall,
            env=self.ctx.env_for_subprocess(),
        )

    def _dispatch_review_fix(self, pr_number: int) -> int:
        round_number = self._next_fix_round(pr_number)
        spec = self.actions.render_review_fix_prompt(pr_number, round_number)
        worktree = self._review_fix_worktree(pr_number)
        if worktree is None:
            return 3
        return launch_spawn_codex_supervisor(
            repo_root=self.ctx.repo_root,
            skill_root=self.ctx.skill_root,
            cd=worktree,
            prompt=self.ctx.repo_root / spec.prompt_path,
            log=self.ctx.repo_root / spec.log_path,
            stall=5400,
            add_dirs=(self.ctx.repo_root,),
        )

    def _dispatch_remote_ci_fix(self, action: Mapping[str, Any]) -> int:
        marker = str(action.get("source_marker") or "")
        if marker.startswith("REMOTE_CI_FIX_DONE:"):
            match = REMOTE_CI_FIX_DONE_RE.fullmatch(marker)
            if match is None:
                return 2
            if match.group(2) != "ok":
                self._append_pending_event(
                    f"WAKEUP_RUNNER_REMOTE_CI_FIX_NOT_PUBLISHABLE:{action.get('target_number')}:{match.group(1)}:{match.group(2)}"
                )
                return 3
            return self._commit_and_push_remote_ci_fix_output(action)
        return self._spawn_remote_ci_fix(action)

    def _spawn_remote_ci_fix(self, action: Mapping[str, Any]) -> int:
        target = action.get("target_number")
        if not isinstance(target, int) or target <= 0:
            return 2
        check_name = str(action.get("check_name") or "").strip()
        head_sha = str(action.get("head_sha") or "").strip()
        if not check_name or not head_sha:
            return 2
        attempt_key = _remote_ci_attempt_key(target, head_sha, check_name)
        attempts = self._remote_ci_fix_attempt_count(attempt_key)
        if attempts >= REMOTE_CI_FIX_ATTEMPT_CAP:
            self._append_pending_event(f"WAKEUP_RUNNER_REMOTE_CI_FIX_RETRY_CAP:{attempt_key}:{attempts}")
            return 3
        head_ref = self._pr_head_ref_from_json(target)
        if not head_ref:
            self._append_pending_event(f"WAKEUP_RUNNER_REMOTE_CI_FIX_HEAD_REF_MISSING:{target}")
            return 3
        worktree = self._worktree_for_branch(head_ref)
        if worktree is None:
            self._append_pending_event(f"WAKEUP_RUNNER_REMOTE_CI_FIX_WORKTREE_MISSING:{target}:{head_ref}")
            return 3
        prompt = self._render_remote_ci_fix_prompt(action, target, check_name, head_sha, head_ref, worktree, attempts + 1)
        log = self.ctx.paths.logs / f"remote-ci-fix-pr{target}-{_safe_ci_check_token(check_name)}-{head_sha[:12]}-a{attempts + 1}.log"
        self._record_remote_ci_fix_attempt(attempt_key)
        exit_code = launch_spawn_codex_supervisor(
            repo_root=self.ctx.repo_root,
            skill_root=self.ctx.skill_root,
            cd=worktree,
            prompt=prompt,
            log=log,
            stall=5400,
            add_dirs=(self.ctx.repo_root,),
            env=self.ctx.env_for_subprocess(),
        )
        return exit_code

    def _render_remote_ci_fix_prompt(
        self,
        action: Mapping[str, Any],
        target: int,
        check_name: str,
        head_sha: str,
        head_ref: str,
        worktree: Path,
        attempt: int,
    ) -> Path:
        token = _safe_ci_check_token(check_name)
        sha_short = head_sha[:12]
        prompt = self.ctx.paths.prompts / f"remote-ci-fix-pr{target}-{token}-{sha_short}-a{attempt}.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        self.actions.render_template(
            str(self.ctx.skill_root / "prompts" / "remote-ci-fix.md"),
            str(prompt),
            env={
                "PR_NUMBER": str(target),
                "CHECK_NAME": check_name,
                "RUN_URL": str(action.get("run_url") or ""),
                "FAILURE_LOG_PATH": str(action.get("failure_log_path") or ""),
                "WORKTREE_PATH": str(worktree),
                "BRANCH": head_ref,
                "BASE_BRANCH": str(getattr(self.actions, "integration_branch", "") or getattr(self.actions, "review_base_branch", "")),
                "SHA_SHORT": sha_short,
                "PROJECT_RULES": "CLAUDE.md",
                "HOST_REFACTOR_COMMENT_POLICY": "none",
                "HOST_WORK_LANGUAGE": self.ctx.host_env.get("HOST_WORK_LANGUAGE") or "en",
            },
        )
        self._replace_remote_ci_fix_shell_defaults(prompt)
        self._ensure_remote_ci_fix_prompt_fully_rendered(prompt)
        return prompt

    def _replace_remote_ci_fix_shell_defaults(self, prompt: Path) -> None:
        text = prompt.read_text(encoding="utf-8")
        text = text.replace("${PROJECT_RULES:-CLAUDE.md}", "CLAUDE.md")
        prompt.write_text(text, encoding="utf-8")

    def _ensure_remote_ci_fix_prompt_fully_rendered(self, prompt: Path) -> None:
        text = prompt.read_text(encoding="utf-8")
        unresolved = sorted(set(re.findall(r"\$\{[^}]+\}", text)))
        if unresolved:
            raise RuntimeError(f"remote-ci-fix prompt render left unresolved placeholders: {', '.join(unresolved)}")

    def _commit_and_push_remote_ci_fix_output(self, action: Mapping[str, Any]) -> int:
        target = action.get("target_number")
        if not isinstance(target, int) or target <= 0:
            return 2
        worktree = self._review_fix_worktree(target)
        if worktree is None:
            return 3
        head_ref = self._pr_head_ref_from_json(target)
        if not head_ref:
            return 3
        precommit_admission = self.actions._require_branch_push_admission_or_return(
            "remote-ci-fix-output",
            head_ref,
            worktree,
        )
        if precommit_admission is not None:
            return precommit_admission
        status = self.command_runner(["git", "-C", str(worktree), "status", "--porcelain"])
        if status.returncode != 0:
            self._append_pending_event(f"WAKEUP_RUNNER_REMOTE_CI_FIX_STATUS_FAILED:{target}")
            return 2
        if not status.stdout.strip():
            return 0
        if self.command_runner(["git", "-C", str(worktree), "add", "-A"]).returncode != 0:
            return 2
        commit = self.command_runner(
            ["git", "-C", str(worktree), "commit", "-m", f"PR #{target} remote-ci-fix output"]
        )
        if commit.returncode != 0:
            self._append_pending_event(f"WAKEUP_RUNNER_REMOTE_CI_FIX_COMMIT_FAILED:{target}")
            return 2
        return self.actions.safe_push(branch=head_ref, worktree=worktree)

    def _review_fix_worktree(self, pr_number: int) -> Path | None:
        head_ref = self._pr_head_ref_from_json(pr_number)
        if not head_ref:
            self._append_pending_event(f"WAKEUP_RUNNER_REVIEW_FIX_HEAD_REF_MISSING:{pr_number}")
            return None
        worktree = self._worktree_for_branch(head_ref)
        if worktree is None:
            self._append_pending_event(f"WAKEUP_RUNNER_REVIEW_FIX_WORKTREE_MISSING:{pr_number}:{head_ref}")
            return None
        return worktree

    def _pr_head_ref_from_json(self, pr_number: int) -> str:
        result = self.command_runner(["gh", "pr", "view", str(pr_number), "--json", "headRefName"])
        if result.returncode != 0:
            return ""
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return ""
        value = payload.get("headRefName") if isinstance(payload, dict) else None
        return value.strip() if isinstance(value, str) else ""

    def _commit_and_push_review_fix_output(self, action: Mapping[str, Any]) -> int:
        """Commit and push uncommitted review-fix output before re-review.

        Headless gap: a fix codex emits FIX_DONE but workers never commit, so the
        fix sits uncommitted in the PR worktree and dispatch_reviewers would re-review
        the stale head forever. Mirror the interactive controller, which commits and
        pushes the fix codex's changes to the PR head before re-dispatching reviewers.
        A clean worktree (already committed or no changes) is a no-op.
        """
        target = action.get("target_number")
        if not isinstance(target, int) or target <= 0:
            return 2
        worktree = self._review_fix_worktree(target)
        if worktree is None:
            return 3
        head_ref = self._pr_head_ref_from_json(target)
        if not head_ref:
            return 3
        precommit_admission = self.actions._require_branch_push_admission_or_return(
            "review-fix-output",
            head_ref,
            worktree,
        )
        if precommit_admission is not None:
            return precommit_admission
        status = self.command_runner(["git", "-C", str(worktree), "status", "--porcelain"])
        if status.returncode != 0:
            self._append_pending_event(f"WAKEUP_RUNNER_REVIEW_FIX_STATUS_FAILED:{target}")
            return 2
        if not status.stdout.strip():
            return 0
        if self.command_runner(["git", "-C", str(worktree), "add", "-A"]).returncode != 0:
            return 2
        commit = self.command_runner(
            ["git", "-C", str(worktree), "commit", "-m", f"PR #{target} review-fix output"]
        )
        if commit.returncode != 0:
            self._append_pending_event(f"WAKEUP_RUNNER_REVIEW_FIX_COMMIT_FAILED:{target}")
            return 2
        return self.actions.safe_push(branch=head_ref, worktree=worktree)

    def _worktree_for_branch(self, branch: str) -> Path | None:
        result = self.command_runner(["git", "-C", str(self.ctx.repo_root), "worktree", "list", "--porcelain"])
        if result.returncode != 0:
            return None
        worktrees_root = (self.ctx.repo_root / ".worktrees").resolve()
        current_path: Path | None = None
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                current_path = Path(line.removeprefix("worktree ").strip())
                continue
            if line.startswith("branch ") and current_path is not None:
                listed_branch = line.removeprefix("branch ").strip()
                if listed_branch == f"refs/heads/{branch}":
                    resolved = current_path.resolve()
                    if _is_relative_to(resolved, worktrees_root) and resolved != worktrees_root:
                        return resolved
                current_path = None
        return None

    def _review_gate_decision(self, action: Mapping[str, Any]) -> dict[str, Any]:
        target = action.get("target_number")
        if not isinstance(target, int):
            return {"decision": "WAIT_OR_REDISPATCH", "reason": "review_target_missing"}
        gate = self._review_gate(target)
        if gate.get("pending"):
            return {"decision": "WAIT_OR_REDISPATCH", "reason": f"pending_reviewer_evidence:{gate['pending'][0]}", "gate": gate}
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
        mergeability_error = self._review_gate_mergeability_error(target)
        if mergeability_error:
            return {"decision": "WAIT_OR_REDISPATCH", "reason": mergeability_error, "gate": gate}
        if gate["reject"] > 0:
            return {"decision": "FIX", "reason": "", "gate": gate}
        ci_error = self._review_gate_ci_error(target, live_head)
        if ci_error:
            return {"decision": "WAIT_OR_REDISPATCH", "reason": ci_error, "gate": gate}
        if gate["approve"] < 1:
            return {"decision": "WAIT_EXPLICIT_APPROVAL", "reason": "no_approval", "gate": gate}
        decision = "MERGE" if gate["comment"] == 0 else "MERGE_WITH_COMMENTS"
        return {"decision": decision, "reason": "", "gate": gate}

    def _review_gate(self, pr_number: int) -> dict[str, Any]:
        live_head = self._pr_head_sha(pr_number)
        candidate = self._review_gate_candidate_evidence(pr_number, live_head)
        evidences = candidate.evidences_by_role
        verdicts = {role: evidence.verdict for role, evidence in evidences.items() if evidence.valid}
        heads = {role: evidence.head_sha for role, evidence in evidences.items() if evidence.valid and evidence.head_sha}
        return ReviewGateSnapshot(
            verdicts_by_role=verdicts,
            heads_by_role=heads,
            live_head_sha=live_head,
            invalid=candidate.invalid,
            pending=candidate.pending,
            terminal_failed_roles=candidate.terminal_failed_roles,
        ).as_dict()

    def _review_gate_candidate_evidence(self, pr_number: int, live_head_sha: str) -> ReviewGateCandidate:
        selection = select_latest_live_head_review_evidence(
            self._review_evidences(pr_number),
            live_head_sha=live_head_sha,
            required_roles=REQUIRED_REVIEW_ROLES,
        )
        return ReviewGateCandidate(
            dict(selection.by_role),
            selection.invalid,
            selection.pending,
            selection.terminal_failed_roles,
            complete_round=selection.complete_round,
        )

    def _review_evidences(self, pr_number: int) -> list[ReviewEvidence]:
        github_evidences = self._github_review_evidences(pr_number)
        if github_evidences is not None:
            return github_evidences
        return [
            ReviewEvidence(
                role,
                1,
                "",
                "",
                "github:issues/comments",
                False,
                f"github_review_comments_unavailable:{role}",
            )
            for role in REQUIRED_REVIEW_ROLES
        ]

    def _github_review_evidences(self, pr_number: int) -> list[ReviewEvidence] | None:
        if not self.ctx.gh_repo_slug:
            return None
        result = self.command_runner(
            ["gh", "api", f"repos/{self.ctx.gh_repo_slug}/issues/{pr_number}/comments?per_page=100", "--paginate", "--slurp"]
        )
        if result.returncode != 0:
            return None
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return _invalid_github_review_evidences("github_review_comments_invalid_json")
        comments = _github_comment_items(payload)
        if comments is None:
            return _invalid_github_review_evidences("github_review_comments_invalid_json")
        evidences: list[ReviewEvidence] = []
        for index, comment in enumerate(comments):
            if not isinstance(comment, Mapping):
                continue
            body = str(comment.get("body") or "")
            created_at = str(comment.get("created_at") or comment.get("createdAt") or "")
            comment_id = _github_comment_id(comment)
            evidence = _review_evidence_from_github_comment(
                body,
                pr_number,
                source=f"github:issues/comments[{index}]",
                created_at=created_at,
                source_index=index + 1,
                comment_id=comment_id,
            )
            if evidence is not None:
                evidences.append(evidence)
        return evidences

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
        status = PrMergeReadinessProjection(
            runner=self.command_runner,
            required_checks=required_release_checks(self.ctx.host_env),
        ).check_pr(self.ctx.gh_repo_slug, pr_number)
        merge_state = status.merge_state_status.strip().upper()
        if not status.ok:
            if merge_state and merge_state != MERGE_READY_STATE:
                return f"merge_state_{merge_state.lower()}"
            return f"ci_unavailable:{status.reason or 'unknown'}"
        if status.head_sha != live_head_sha:
            return "ci_stale_head_sha"
        if merge_state != MERGE_READY_STATE:
            return f"merge_state_{merge_state.lower() or 'missing'}"
        if status.missing_required:
            return "required_ci_missing"
        if status.required_pending:
            return "required_ci_pending"
        if status.required_failed:
            return "required_ci_failed"
        if not status.required_check_names:
            return "required_ci_missing_configuration"
        return None

    def _review_gate_mergeability_error(self, pr_number: int) -> str | None:
        result = self.command_runner(["gh", "pr", "view", str(pr_number), "--json", "mergeable,isDraft,changedFiles"])
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
        changed_files = payload.get("changedFiles")
        if not isinstance(changed_files, int) or changed_files < 0:
            return "changed_files_unavailable"
        if changed_files == 0:
            return "empty_diff_pr"
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

    def _ledger_suppresses_retry(self, action: Mapping[str, Any]) -> bool:
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
                stale_release_reason = self._release_dispatch_stale_ledger_reason(action)
                if stale_release_reason:
                    self._append_pending_event(
                        f"WAKEUP_RUNNER_STALE_RELEASE_DISPATCH_LEDGER:{action_id}:{stale_release_reason}"
                    )
                    continue
                stale_spawn_reason = self._applied_spawn_stale_reason(action)
                if stale_spawn_reason:
                    self._append_pending_event(f"WAKEUP_RUNNER_STALE_SPAWN_LEDGER:{action_id}:{stale_spawn_reason}")
                    continue
                if self._applied_row_current_effect_suppresses_retry(action):
                    return True
            if status == "blocked" and _terminal_blocked_reason(str(row.get("reason") or "")):
                return True
            if (
                status == "skipped"
                and action.get("kind") == "harness-spawn-intent-invalid"
                and str(row.get("reason") or "").startswith("archived_invalid_harness_spawn_intent:")
            ):
                return True
        return False

    def _rebase_resolve_helper_exit_2_blocked_count(self, action: Mapping[str, Any]) -> int:
        action_id = str(action.get("action_id") or "")
        if not action_id or action.get("controller_action") != "dispatch_pr_rebase_resolve":
            return 0
        if not self.ledger_path.exists():
            return 0
        count = 0
        for line in self.ledger_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("action_id") != action_id:
                continue
            if row.get("status") == "blocked" and row.get("reason") == "helper_exit:2":
                count += 1
        return count

    def _publish_helper_exit_backoff_active(self, action: Mapping[str, Any]) -> bool:
        """#876: pace retries of a deterministically-failing publish helper.

        Once ``publish_implementation_output`` has recorded at least
        ``PUBLISH_HELPER_EXIT_BACKOFF_THRESHOLD`` ``helper_exit:*`` failures for an
        action_id, skip retrying until ``PUBLISH_HELPER_EXIT_BACKOFF_COOLDOWN_SECONDS``
        have elapsed since the latest failure. This stops the every-tick 15-minute
        publish-verify storm while still retrying once per cooldown, so a later fix
        (or transient recovery) is never permanently blocked. Legacy rows without a
        parseable ``ts`` fail open (do not trigger backoff)."""
        action_id = str(action.get("action_id") or "")
        if not action_id or action.get("controller_action") != "publish_implementation_output":
            return False
        if not self.ledger_path.exists():
            return False
        count = 0
        latest_ts: datetime | None = None
        for line in self.ledger_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("action_id") != action_id:
                continue
            if row.get("status") != "blocked" or not str(row.get("reason") or "").startswith("helper_exit:"):
                continue
            count += 1
            ts_text = str(row.get("ts") or "")
            try:
                ts = datetime.strptime(ts_text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if latest_ts is None or ts > latest_ts:
                latest_ts = ts
        if count < PUBLISH_HELPER_EXIT_BACKOFF_THRESHOLD or latest_ts is None:
            return False
        elapsed = (datetime.now(timezone.utc) - latest_ts).total_seconds()
        return elapsed < PUBLISH_HELPER_EXIT_BACKOFF_COOLDOWN_SECONDS

    def _applied_row_current_effect_suppresses_retry(self, action: Mapping[str, Any]) -> bool:
        controller_action = str(action.get("controller_action") or "")
        if controller_action == "spawn_codex_harness_background":
            return self._applied_spawn_stale_reason(action) == ""
        if controller_action == "dispatch_release_candidate":
            return self._release_dispatch_stale_ledger_reason(action) == ""
        if controller_action == "dispatch_consensus_implementation":
            return self._consensus_implementation_current_effect_suppresses_retry(action)
        if controller_action == "publish_review_fix_output_from_action":
            return self._publish_review_fix_current_effect_suppresses_retry(action)
        if controller_action == "dispatch_reviewers":
            return self._dispatch_reviewers_current_effect_suppresses_retry(action)
        if _is_remote_ci_red_dispatch(action):
            return False
        return False

    def _consensus_implementation_current_effect_suppresses_retry(self, action: Mapping[str, Any]) -> bool:
        if consensus_implementation_suppressed_reason(dict(action), self.ctx.repo_root):
            return True
        receipt = self._consensus_implementation_dispatch_receipt(action)
        try:
            text = self.pending_events_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return receipt in text.splitlines()

    def _append_consensus_implementation_dispatch_receipt(self, action: Mapping[str, Any]) -> None:
        self._append_pending_event(self._consensus_implementation_dispatch_receipt(action))

    def _consensus_implementation_dispatch_receipt(self, action: Mapping[str, Any]) -> str:
        action_id = str(action.get("action_id") or "")
        target = action.get("target_number")
        cluster_id = str(action.get("cluster_id") or "").strip()
        return f"WAKEUP_RUNNER_CONSENSUS_IMPLEMENTATION_DISPATCHED:{target}:{cluster_id}:{action_id}"

    def _publish_review_fix_current_effect_suppresses_retry(self, action: Mapping[str, Any]) -> bool:
        target = action.get("target_number")
        if not isinstance(target, int) or target <= 0:
            return False
        projected_head = str(action.get("head_sha") or "").strip()
        live_head = self._pr_head_sha(target)
        if not live_head:
            return False
        if projected_head and live_head != projected_head:
            return True
        worktree = self._review_fix_worktree(target)
        if worktree is None:
            return False
        status = self.command_runner(["git", "-C", str(worktree), "status", "--porcelain"])
        if status.returncode != 0:
            return False
        return not status.stdout.strip()

    def _dispatch_reviewers_current_effect_suppresses_retry(self, action: Mapping[str, Any]) -> bool:
        target = action.get("target_number")
        if not isinstance(target, int) or target <= 0:
            return False
        marker = str(action.get("source_marker") or "")
        if marker.startswith("FIX_DONE:"):
            return self._publish_review_fix_current_effect_suppresses_retry(action)
        if action.get("kind") == "review-evidence-redispatch" or action.get("stale_review_roles"):
            projected_head = str(action.get("head_sha") or "").strip()
            if not projected_head:
                return False
            gate = self._review_gate(target)
            live_head = str(gate.get("live_head_sha") or "").strip()
            if live_head and live_head != projected_head:
                return True
            stale_roles = action.get("stale_review_roles")
            roles = [str(role) for role in stale_roles] if isinstance(stale_roles, list) else list(REQUIRED_REVIEW_ROLES)
            heads = gate.get("heads_by_role")
            if not isinstance(heads, Mapping):
                return False
            return all(
                str(heads.get(role) or "") == projected_head
                or self._pending_review_spawn_intent_exists(target, role, projected_head)
                for role in roles
            )
        return False

    def _pending_review_spawn_intent_exists(self, pr_number: int, role: str, projected_head: str) -> bool:
        try:
            lines = self.pending_events_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return False
        archived_invalid_markers = archived_invalid_harness_spawn_intent_markers(lines)
        prefix = f"dispatch-reviewers:{pr_number}:{role}:"
        for line in lines:
            if " HARNESS_SPAWN_INTENT " not in line:
                continue
            try:
                intent = json.loads(line.split(" HARNESS_SPAWN_INTENT ", 1)[1])
            except json.JSONDecodeError:
                continue
            if not isinstance(intent, dict):
                continue
            if not live_valid_harness_spawn_intent(self.ctx, line, intent, archived_invalid_markers):
                continue
            if not str(intent.get("intent_id") or "").startswith(prefix):
                continue
            prompt_path = self._path_from_intent_value(intent.get("prompt"))
            log_path = self._path_from_intent_value(intent.get("log"))
            intent_head = self._review_head_sha_for(prompt_path, log_path, "")
            if intent_head == projected_head:
                return True
        return False

    def _path_from_intent_value(self, value: Any) -> Path:
        path = Path(str(value or ""))
        return path if path.is_absolute() else self.ctx.repo_root / path

    def _release_dispatch_stale_ledger_reason(self, action: Mapping[str, Any]) -> str:
        return self._current_release_dispatch_stale_reason(action)

    def _current_release_dispatch_stale_reason(self, action: Mapping[str, Any]) -> str:
        if action.get("kind") != "release-gate-dispatch":
            return ""
        if action.get("controller_action") != "dispatch_release_candidate":
            return ""
        preconditions = action.get("preconditions")
        if not isinstance(preconditions, list):
            return ""
        candidate = self.ctx.paths.state / "release-candidate.json"
        decision = read_json(self.ctx.paths.state / "release-decision.json", {})
        liveness = classify_release_candidate_liveness(self.ctx.repo_root, read_json(candidate, {}), decision)
        if liveness.blocks_dispatch or not liveness.stale_reason:
            return ""
        if liveness.stale_reason not in preconditions:
            return ""
        return liveness.stale_reason

    @property
    def remote_ci_fix_attempts_path(self) -> Path:
        return self.ctx.paths.state / "remote-ci-fix-attempts.json"

    def _remote_ci_fix_attempt_count(self, attempt_key: str) -> int:
        attempts = self._read_remote_ci_fix_attempts()
        value = attempts.get(attempt_key)
        return value if isinstance(value, int) and value > 0 else 0

    def _record_remote_ci_fix_attempt(self, attempt_key: str) -> None:
        attempts = self._read_remote_ci_fix_attempts()
        attempts[attempt_key] = self._remote_ci_fix_attempt_count(attempt_key) + 1
        self.remote_ci_fix_attempts_path.parent.mkdir(parents=True, exist_ok=True)
        self.remote_ci_fix_attempts_path.write_text(json.dumps(attempts, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    def _read_remote_ci_fix_attempts(self) -> dict[str, int]:
        try:
            payload = json.loads(self.remote_ci_fix_attempts_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        attempts: dict[str, int] = {}
        for key, value in payload.items():
            if isinstance(key, str) and isinstance(value, int) and value > 0:
                attempts[key] = value
        return attempts

    def _applied_spawn_stale_reason(self, action: Mapping[str, Any]) -> str:
        if action.get("controller_action") != "spawn_codex_harness_background":
            return ""
        log = Path(str(action.get("log") or ""))
        if not log.is_absolute() or not log.exists():
            return "target-log-absent"
        if is_implement_log(log):
            state = classify_implement_attempt(repo_root=self.ctx.repo_root, log_path=log, command_runner=self.command_runner)
            if state.redispatch and not implement_attempt_is_terminal_or_noop_completion(state):
                return f"target-log-redispatchable:{state.reason}"
            return ""
        if _phase9_solver_log_is_clean_markerless(log):
            return ""
        if _spawn_log_suppresses_retry(log):
            return ""
        return "target-log-terminal-failed"

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
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        return result

    def _append_pending_event(self, line: str) -> None:
        self.pending_events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.pending_events_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _source_log_has_clean_marker(path: Path, marker: str) -> bool:
    marker_read = read_worker_terminal_marker(path)
    if marker_read.marker == marker:
        return True
    if marker_read.reason != "duplicate_or_conflicting_log_marker":
        return False
    if not is_implement_log(path):
        return False
    return _implement_run_artifact_done_marker(path) == marker


def _phase9_solver_log_is_clean_markerless(path: Path) -> bool:
    if re.fullmatch(r"phase9-issue[1-9][0-9]*-r[1-9][0-9]*-(?:minimal|structural|delete)\.log", path.name) is None:
        return False
    marker_read = read_worker_terminal_marker(path)
    return marker_read.reason == "marker_missing"


def _plan_level_judge_log_path(repo_root: Path, artifact_path: str) -> Path | None:
    artifact = repo_root / artifact_path
    match = CONSENSUS_JUDGE_ARTIFACT_RE.fullmatch(artifact.name)
    if match is None:
        return None
    return repo_root / ".refactor-loop" / "logs" / f"{artifact.name.removesuffix('.md')}.log"


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


def _source_log_has_clean_rebase_resolve_marker(path: Path, marker: str) -> bool:
    if not marker.startswith(("REBASE_RESOLVE_DONE:", "REBASE_RESOLVE_BLOCKED:")):
        return False
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    if not any(line.strip() == "EXIT=0" for line in lines[-30:]):
        return False
    return any(line.strip().strip("`") == marker for line in lines[-30:])


def _safe_branch_name(value: str) -> bool:
    return bool(value) and not value.startswith("-") and not any(ch.isspace() or ord(ch) < 32 for ch in value)


def _safe_ci_check_token(check_name: str) -> str:
    token = SAFE_CI_CHECK_TOKEN_RE.sub("-", check_name.strip()).strip("-")
    return token or "check"


def _remote_ci_attempt_key(pr_number: int, head_sha: str, check_name: str) -> str:
    return f"pr{pr_number}:{head_sha}:{_safe_ci_check_token(check_name)}"


def _is_remote_ci_red_dispatch(action: Mapping[str, Any]) -> bool:
    return action.get("kind") == "ci-red" and action.get("controller_action") == "dispatch_remote_ci_fix"


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _extract_review_head_sha(text: str) -> str:
    return extract_review_head_sha(text)


def _extract_review_round(text: str) -> int | None:
    from .review_gate_selection import extract_review_round

    return extract_review_round(text)


def _github_comment_items(payload: object) -> list[object] | None:
    if isinstance(payload, list):
        if all(isinstance(page, list) for page in payload):
            return [item for page in payload for item in page]
        return payload
    if isinstance(payload, Mapping):
        comments = payload.get("comments")
        if isinstance(comments, list):
            return comments
    return None


def _github_comment_id(comment: Mapping[str, Any]) -> int | None:
    raw_comment_id = comment.get("id")
    if isinstance(raw_comment_id, int) and raw_comment_id > 0:
        return raw_comment_id
    if isinstance(raw_comment_id, str) and raw_comment_id.isdigit():
        comment_id = int(raw_comment_id)
        return comment_id if comment_id > 0 else None
    return None


def _invalid_github_review_evidences(reason: str) -> list[ReviewEvidence]:
    return [
        ReviewEvidence(
            role,
            1,
            "",
            "",
            "github:issues/comments",
            False,
            f"{reason}:{role}",
        )
        for role in REQUIRED_REVIEW_ROLES
    ]


def _review_evidence_from_github_comment(
    body: str,
    pr_number: int,
    *,
    source: str,
    created_at: str = "",
    source_index: int = 0,
    comment_id: int | None = None,
) -> ReviewEvidence | None:
    parsed = parse_github_review_evidence(
        body,
        pr_number,
        source=source,
        created_at=created_at,
        source_index=source_index,
        comment_id=comment_id,
    )
    if parsed is None:
        return None
    return ReviewEvidence(
        parsed.role,
        parsed.round_number,
        parsed.verdict,
        parsed.head_sha,
        parsed.source,
        parsed.valid,
        parsed.reason,
        parsed.terminal_failed,
        parsed.pending,
        parsed.created_at,
        parsed.source_index,
        parsed.comment_id,
    )


def _single_linked_issue_from_body(body: str) -> int | None:
    numbers = extract_closing_issue_numbers(body)
    return numbers[0] if len(numbers) == 1 else None


def _target_from_text(text: str) -> tuple[str, int] | None:
    for pattern, kind in TARGET_TEXT_PATTERNS:
        match = pattern.search(text)
        if match:
            return kind, int(match.group(1))
    return None


def _managed_pr_head_ref(value: str) -> bool:
    return bool(re.fullmatch(r"refactor/iter[1-9][0-9]*-[A-Za-z0-9._-]+", value))


def _unblocks_pr_mergeability(action: Mapping[str, Any]) -> bool:
    return str(action.get("controller_action") or "") in {
        "dispatch_pr_rebase_resolve",
        "commit_push_resolved_pr_rebase",
    }


def _publishes_review_fix_output(action: Mapping[str, Any]) -> bool:
    return str(action.get("controller_action") or "") == "publish_review_fix_output_from_action"


def _unblocks_release_rollup(action: Mapping[str, Any]) -> bool:
    return str(action.get("controller_action") or "") in {
        "open_release_rollup_pr_from_action",
        "auto_merge_release_rollup_pr_from_action",
    }


def _is_reviewer_harness_spawn_intent(action: Mapping[str, Any]) -> bool:
    if action.get("kind") != "harness-spawn-intent":
        return False
    if action.get("controller_action") != "spawn_codex_harness_background":
        return False
    if action.get("route") == "dispatch-reviewers":
        return True
    return str(action.get("intent_id") or "").startswith("dispatch-reviewers:")


def _is_fix_harness_spawn_intent(action: Mapping[str, Any]) -> bool:
    if action.get("kind") != "harness-spawn-intent":
        return False
    if action.get("controller_action") != "spawn_codex_harness_background":
        return False
    if action.get("route") in {"review-fix", "dispatch-review-fix"}:
        return True
    intent_id = str(action.get("intent_id") or "")
    task_id = str(action.get("task_id") or "")
    return intent_id.startswith("fix-pr") or task_id.startswith("fix-pr")


def _terminal_blocked_reason(reason: str) -> bool:
    return reason in {"target_not_open:CLOSED", "target_not_open:MERGED"}


def _publish_implementation_artifact_reason(reason: str) -> str:
    prefix = "implementation_pr_"
    if not reason.startswith(prefix):
        return reason
    local = reason.removeprefix(prefix)
    return "publish_implementation_" + local


def _spawn_launch_failure(result: RunnerResult) -> bool:
    return result.reason.startswith("helper_exit:") or result.reason.startswith("exception:")


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _release_int(value: Any, default: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def load_plan_file(path: Path) -> Mapping[str, Any]:
    data = read_json(path, {})
    return data if isinstance(data, dict) else {}


def run_wakeup_runner_reconcile_tick(runner: WakeupRunner) -> list[RunnerResult]:
    return runner.run_once()


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
        lease = DaemonHeartbeatLease("wakeup_runner_daemon", ctx.repo_root, singleton=True)
        interval = max(1, int(args.interval_seconds))
        with lease.daemon_lifetime():
            lease.beat()
            while True:
                results = lease.run_tick(lambda: run_wakeup_runner_reconcile_tick(runner))
                _log_tick_status("wakeup-runner", _wakeup_tick_action(results))
                lease.sleep_with_lease(interval)
    results = run_wakeup_runner_reconcile_tick(runner)
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
        and result.reason not in {"graphql-backoff", "graphql-backoff:apply_issue_decomposition_plan"}
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
        return f"skip:graphql-backoff remaining=unknown [{counts}]"
    if first.status == "skipped" and first.reason == "graphql-backoff:apply_issue_decomposition_plan":
        return f"skip:graphql-backoff:apply_issue_decomposition_plan [{counts}]"
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


def _forbidden_action_field_paths(value: Any, prefix: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_path = f"{prefix}.{key}" if prefix else str(key)
            if key in FORBIDDEN_ACTION_FIELDS:
                paths.append(key_path)
            paths.extend(_forbidden_action_field_paths(child, key_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            paths.extend(_forbidden_action_field_paths(child, f"{prefix}[{index}]"))
    return sorted(paths)


def _log_tick_status(daemon: str, action: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {daemon}: tick {action}", flush=True)


def _single_line(value: str) -> str:
    return " ".join(str(value).split())[:240]


def _is_invalid_harness_cleanup(action: Mapping[str, Any]) -> bool:
    return action.get("kind") == "harness-spawn-intent-invalid"


def _reviewer_log_terminal_failed(path: Path) -> bool:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    for line in reversed(lines):
        text = line.strip()
        if not text.startswith("EXIT="):
            continue
        try:
            return int(text.removeprefix("EXIT=")) != 0
        except ValueError:
            return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
