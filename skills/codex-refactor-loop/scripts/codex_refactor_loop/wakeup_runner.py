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
from .context import LoopContext, LoopContextError
from .controller_actions import ControllerActions
from .heartbeat import DaemonHeartbeatLease
from .processes import ProcessSupervisor
from .release.publish_preflight import ReleasePublishPreflight
from .state import read_json
from .wakeup_plan import build_plan


RUNNER_AUTHORITY = "wakeup-runner-396"
APPLY_AUTHORITY = "wakeup-runner-396-only"
FORBIDDEN_ACTION_FIELDS = {"argv", "args", "shell", "cmd", "commands", "env", "git", "gh", "executor"}
REQUIRED_REVIEW_ROLES = ("architect", "tests", "quality")
REVIEW_DONE_RE = re.compile(r"^REVIEW_DONE:([1-9][0-9]*):([A-Za-z][A-Za-z0-9_-]*):(approve|comment|reject)$")


@dataclass(frozen=True)
class RunnerResult:
    action_id: str
    status: str
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
        if controller_action == "review_gate":
            return self._validate_review_gate(action)
        if controller_action == "publish_release_candidate":
            return self._validate_release(action)
        return None

    def _validate_review_gate(self, action: Mapping[str, Any]) -> str | None:
        target = action.get("target_number")
        if not isinstance(target, int):
            return "review_target_missing"
        gate = self._review_gate(target)
        if not gate["all_present"]:
            return "review_gate_missing_reviewers"
        if gate["reject"] > 0:
            return None
        if gate["approve"] < 1:
            return "review_gate_no_approval"
        if str(gate.get("head_sha") or "") and str(action.get("head_sha") or gate.get("head_sha")) != gate["head_sha"]:
            return "review_gate_stale_head"
        return None

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
            return self.actions.safe_push(branch=str(action.get("head_ref") or ""))
        if controller_action == "publish_worker_output_from_action":
            return self.actions.publish_worker_output_from_action(dict(action))
        if controller_action == "close_managed_item_from_drop_marker":
            return self.actions.close_managed_item_from_drop_marker(dict(action))
        if controller_action == "review_gate":
            gate = self._review_gate(int(action["target_number"]))
            if gate["reject"] > 0:
                return self._dispatch_review_fix(int(action["target_number"]))
            return self.actions.merge_pr(str(action["target_number"]))
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

    def _review_gate(self, pr_number: int) -> dict[str, Any]:
        verdicts: dict[str, str] = {}
        for role in REQUIRED_REVIEW_ROLES:
            verdict = self._review_verdict_from_artifact(pr_number, role) or self._review_verdict_from_log(pr_number, role)
            if verdict:
                verdicts[role] = verdict
        return {
            "verdicts": verdicts,
            "all_present": all(role in verdicts for role in REQUIRED_REVIEW_ROLES),
            "approve": sum(1 for verdict in verdicts.values() if verdict == "approve"),
            "reject": sum(1 for verdict in verdicts.values() if verdict == "reject"),
            "comment": sum(1 for verdict in verdicts.values() if verdict == "comment"),
            "head_sha": self._pr_head_sha(pr_number),
        }

    def _review_verdict_from_artifact(self, pr_number: int, role: str) -> str | None:
        pattern = f"review-pr{pr_number}-{role}-r*.md"
        candidates = sorted(self.ctx.paths.runs.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in candidates:
            text = path.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"(?m)^verdict:\s*(approve|comment|reject)\s*$", text)
            if match:
                return match.group(1)
        return None

    def _review_verdict_from_log(self, pr_number: int, role: str) -> str | None:
        candidates = sorted(self.ctx.paths.logs.glob(f"review-pr{pr_number}-{role}*.log"), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in candidates:
            if not _log_has_exit_zero(path):
                continue
            for line in reversed(path.read_text(encoding="utf-8", errors="replace").splitlines()):
                match = REVIEW_DONE_RE.match(line.strip())
                if match and match.group(1) == str(pr_number) and match.group(2) == role:
                    return match.group(3)
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

    def _pr_head_sha(self, pr_number: int) -> str:
        result = self.command_runner(["gh", "pr", "view", str(pr_number), "--json", "headRefOid", "--jq", ".headRefOid"])
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
