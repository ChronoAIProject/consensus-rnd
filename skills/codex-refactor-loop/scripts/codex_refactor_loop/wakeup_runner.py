"""Apply the #396 wakeup-plan closed action projection."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .active_controller import require_active_controller, write_active_controller_status
from . import labels
from .context import LoopContext, LoopContextError
from .controller_actions import ControllerActions
from .heartbeat import DaemonHeartbeatLease
from .pr_checks import PrChecksProjection
from .processes import ProcessSupervisor
from .release.publish_preflight import ReleasePublishPreflight
from .state import read_json
from .wakeup_plan import build_plan


RUNNER_AUTHORITY = "wakeup-runner-396"
APPLY_AUTHORITY = "wakeup-runner-396-only"
FORBIDDEN_ACTION_FIELDS = {"argv", "args", "shell", "cmd", "commands", "env", "git", "gh", "executor"}
REQUIRED_REVIEW_ROLES = ("architect", "tests", "quality")
REVIEW_DONE_RE = re.compile(r"^REVIEW_DONE:([1-9][0-9]*):([A-Za-z][A-Za-z0-9_-]*):(approve|comment|reject)$")
REVIEW_ARTIFACT_RE = re.compile(r"^review-pr([1-9][0-9]*)-([A-Za-z][A-Za-z0-9_-]*)-r([1-9][0-9]*)\.md$")
REVIEW_LOG_RE = re.compile(r"^review-pr([1-9][0-9]*)-([A-Za-z][A-Za-z0-9_-]*)-r([1-9][0-9]*)\.log$")
REVIEW_HEAD_RE = re.compile(r"(?im)^(?:reviewed[-_ ]?head[-_ ]?sha|head[-_ ]?sha|headRefOid|REVIEW_HEAD_SHA)\s*[:=]\s*([0-9a-f]{7,64})\s*$")
SUPPORTED_CONTROLLER_ACTIONS = {
    "spawn_codex_harness_background",
    "safe_push",
    "publish_worker_output_from_action",
    "close_managed_item_from_drop_marker",
    "review_gate",
    "publish_release_candidate",
}


@dataclass(frozen=True)
class RunnerResult:
    action_id: str
    status: str
    reason: str = ""


@dataclass(frozen=True)
class ReviewEvidence:
    role: str
    round_number: int
    verdict: str
    head_sha: str
    source: str
    valid: bool = True
    reason: str = ""


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
        results: list[RunnerResult] = []
        for action in plan.get("actions", []):
            if not isinstance(action, dict) or action.get("status_only") is True:
                continue
            result = self.apply_action(action)
            results.append(result)
            if result.status == "applied":
                break
        return results

    def apply_action(self, action: Mapping[str, Any]) -> RunnerResult:
        action_id = str(action.get("action_id") or "")
        if not action_id:
            return self._blocked(action, "missing_action_id")
        if self._ledger_has(action_id):
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
        kind = action.get("target_kind")
        number = action.get("target_number")
        if kind in {"PR", "issue"}:
            if not isinstance(number, int):
                return "target_number_missing"
            live = self._live_target_state(str(kind).lower(), number)
            if live not in {"OPEN", "open"}:
                return f"target_not_open:{live or 'unknown'}"
        return None

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
        if controller_action == "close_managed_item_from_drop_marker":
            return self._validate_close_managed_drop(action)
        return None

    def _validate_spawn_codex(self, action: Mapping[str, Any]) -> str | None:
        preconditions = action.get("preconditions")
        if not isinstance(preconditions, list) or "target_log_absent" not in preconditions:
            return "spawn_missing_precondition:target_log_absent"
        log = Path(str(action.get("log") or ""))
        if log.exists():
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

    def _dispatch(self, controller_action: str, action: Mapping[str, Any]) -> int:
        if controller_action == "spawn_codex_harness_background":
            return self._spawn_codex(action)
        if controller_action == "safe_push":
            return self.actions.safe_push(branch=str(action.get("head_ref") or ""), worktree=str(action.get("worktree") or ""))
        if controller_action == "publish_worker_output_from_action":
            return self.actions.publish_worker_output_from_action(dict(action))
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
        cd = Path(str(action.get("cd") or self.ctx.repo_root))
        prompt = Path(str(action.get("prompt") or ""))
        log = Path(str(action.get("log") or ""))
        stall = int(action.get("stall") or 5400)
        if not prompt.is_file():
            return 2
        return self.supervisor.supervise(["codex", "exec", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check", "-C", str(cd), "-"], stdin=prompt, log=log, stall=stall)

    def _dispatch_review_fix(self, pr_number: int) -> int:
        round_number = self._next_fix_round(pr_number)
        spec = self.actions.render_review_fix_prompt(pr_number, round_number)
        return self.supervisor.supervise(
            ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check", "-C", str(self.ctx.repo_root), "-"],
            stdin=self.ctx.repo_root / spec.prompt_path,
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
        reviewed_head = str(gate.get("reviewed_head_sha") or "").strip()
        live_head = str(gate.get("live_head_sha") or "").strip()
        if not action_head:
            return {"decision": "WAIT_OR_REDISPATCH", "reason": "missing_action_reviewed_head_sha", "gate": gate}
        if not reviewed_head:
            return {"decision": "WAIT_OR_REDISPATCH", "reason": "missing_reviewer_head_sha", "gate": gate}
        if action_head != reviewed_head:
            return {"decision": "WAIT_OR_REDISPATCH", "reason": "reviewed_head_mismatch", "gate": gate}
        if not live_head:
            return {"decision": "WAIT_OR_REDISPATCH", "reason": "missing_live_head_sha", "gate": gate}
        if reviewed_head != live_head:
            return {"decision": "WAIT_OR_REDISPATCH", "reason": "stale_head_sha", "gate": gate}
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
        evidences = self._latest_review_round(pr_number)
        verdicts = {role: evidence.verdict for role, evidence in evidences.items() if evidence.valid}
        invalid = [evidence.reason or f"invalid:{evidence.role}" for evidence in evidences.values() if not evidence.valid]
        head_values = {evidence.head_sha for evidence in evidences.values() if evidence.valid and evidence.head_sha}
        if len(head_values) > 1:
            invalid.append("duplicate_reviewed_head_sha")
        reviewed_head_sha = next(iter(head_values), "") if len(head_values) == 1 else ""
        return {
            "verdicts": verdicts,
            "all_present": all(role in verdicts for role in REQUIRED_REVIEW_ROLES),
            "approve": sum(1 for verdict in verdicts.values() if verdict == "approve"),
            "reject": sum(1 for verdict in verdicts.values() if verdict == "reject"),
            "comment": sum(1 for verdict in verdicts.values() if verdict == "comment"),
            "reviewed_head_sha": reviewed_head_sha,
            "live_head_sha": self._pr_head_sha(pr_number),
            "invalid": invalid,
        }

    def _latest_review_round(self, pr_number: int) -> dict[str, ReviewEvidence]:
        by_round: dict[int, dict[str, ReviewEvidence]] = {}
        for evidence in self._review_evidences(pr_number):
            round_bucket = by_round.setdefault(evidence.round_number, {})
            existing = round_bucket.get(evidence.role)
            if existing is not None:
                round_bucket[evidence.role] = ReviewEvidence(
                    role=evidence.role,
                    round_number=evidence.round_number,
                    verdict="",
                    head_sha="",
                    source=evidence.source,
                    valid=False,
                    reason=f"duplicate_reviewer_evidence:{evidence.role}",
                )
                continue
            round_bucket[evidence.role] = evidence
        if not by_round:
            return {}
        return by_round[max(by_round)]

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
        return ReviewEvidence(role, round_number, verdict, _extract_review_head_sha(text), str(path))

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
        return ReviewEvidence(role, round_number, verdicts[0], _extract_review_head_sha(text), str(path))

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
        if payload.get("isDraft") is True:
            return "pr_draft"
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
        full = [str(part) for part in command]
        if full and full[0] == "gh" and self.ctx.gh_repo_slug and "--repo" not in full:
            full[1:1] = ["--repo", self.ctx.gh_repo_slug]
        return subprocess.run(full, cwd=str(self.ctx.repo_root), capture_output=True, text=True, check=False)

    def _ledger_has(self, action_id: str) -> bool:
        if not self.ledger_path.exists():
            return False
        for line in self.ledger_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("action_id") == action_id and row.get("status") in {"applied", "dry-run"}:
                return True
        return False

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
        return False
    try:
        marker_index = max(index for index, line in enumerate(lines) if marker in line)
    except ValueError:
        return False
    try:
        exit_index = max(index for index, line in enumerate(lines) if line == "EXIT=0")
    except ValueError:
        return False
    return marker_index < exit_index


def _log_has_exit_zero(path: Path) -> bool:
    try:
        return any(line == "EXIT=0" for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-5:])
    except OSError:
        return False


def _safe_branch_name(value: str) -> bool:
    return bool(value) and not value.startswith("-") and not any(ch.isspace() or ord(ch) < 32 for ch in value)


def _extract_review_head_sha(text: str) -> str:
    match = REVIEW_HEAD_RE.search(text)
    return match.group(1) if match else ""


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
            runner.run_once()
            lease.sleep_with_lease(interval)
    results = runner.run_once()
    blocked = [result for result in results if result.status == "blocked"]
    return 3 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
