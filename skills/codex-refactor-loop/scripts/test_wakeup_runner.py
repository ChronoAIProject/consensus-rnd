#!/usr/bin/env python3
"""Behavior tests for the #396 wakeup runner."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading as real_threading
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

REAL_CONDITION = real_threading.Condition
REAL_EVENT = real_threading.Event
REAL_THREAD = real_threading.Thread

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop import labels
from codex_refactor_loop.wakeup_runner import (
    WakeupRunner,
    RunnerResult,
    _log_tick_status,
    _run_once_with_periodic_heartbeat,
    _wakeup_tick_action,
    main as wakeup_runner_main,
    _source_log_has_clean_marker,
)


class SourceMarkerRevalidationFallbackTests(unittest.TestCase):
    def test_revalidation_falls_back_to_implement_run_artifact_for_markerless_log(self) -> None:
        # Symmetric to wakeup_plan's detection fallback: a clean-exit implement
        # worker may emit IMPLEMENT_DONE only into its run artifact, so source-
        # marker revalidation must accept it from runs/implement-issue-<id>.md
        # rather than rejecting publish as clean_exit_marker_missing.
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            logs = repo / ".refactor-loop" / "logs"
            runs = repo / ".refactor-loop" / "runs"
            logs.mkdir(parents=True)
            runs.mkdir(parents=True)
            log = logs / "implement-issue-421.log"
            log.write_text("chatter, no standalone marker\nEXIT=0\nDONE_AT=x\n", encoding="utf-8")
            (runs / "implement-issue-421.md").write_text(
                "body\n⟦AI:AUTO-LOOP⟧\nIMPLEMENT_DONE:issue-421:ok\n", encoding="utf-8"
            )
            self.assertTrue(_source_log_has_clean_marker(log, "IMPLEMENT_DONE:issue-421:ok"))
            # scope guard: a non-implement log must not fall back to an artifact
            other = logs / "audit-iter-9.log"
            other.write_text("x\nEXIT=0\n", encoding="utf-8")
            (runs / "audit-iter-9.md").write_text("IMPLEMENT_DONE:issue-9:ok\n", encoding="utf-8")
            self.assertFalse(_source_log_has_clean_marker(other, "IMPLEMENT_DONE:issue-9:ok"))
            # scope guard: an unclean implement log must not fall back
            unclean = logs / "implement-issue-777.log"
            unclean.write_text("crash\nEXIT=1\n", encoding="utf-8")
            (runs / "implement-issue-777.md").write_text("IMPLEMENT_DONE:issue-777:ok\n", encoding="utf-8")
            self.assertFalse(_source_log_has_clean_marker(unclean, "IMPLEMENT_DONE:issue-777:ok"))


class FakeSupervisor:
    def __init__(self) -> None:
        self.calls = []

    def supervise(self, command, *, stdin, log, stall, preamble=""):
        self.calls.append({"command": list(command), "stdin": stdin, "log": log, "stall": stall})
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        Path(log).write_text("EXIT=0\n", encoding="utf-8")
        return 0


class FakeActions:
    def __init__(self, *, safe_push_code: int = 0, publish_code: int = 0, close_code: int = 0) -> None:
        self.safe_push_code = safe_push_code
        self.publish_code = publish_code
        self.close_code = close_code
        self.calls: list[tuple[str, object]] = []

    def safe_push(self, remote: str = "origin", branch: str = "", worktree: str | Path | None = None) -> int:
        self.calls.append(("safe_push", {"remote": remote, "branch": branch, "worktree": str(worktree or "")}))
        return self.safe_push_code

    def publish_worker_output_from_action(self, action: dict) -> int:
        self.calls.append(("publish_worker_output_from_action", dict(action)))
        return self.publish_code

    def publish_implementation_output(self, action: dict) -> int:
        self.calls.append(("publish_implementation_output", dict(action)))
        return self.publish_code

    def dispatch_consensus_implementation(self, action: dict) -> int:
        self.calls.append(("dispatch_consensus_implementation", dict(action)))
        return 0

    def dispatch_reviewers(self, action: dict) -> int:
        self.calls.append(("dispatch_reviewers", dict(action)))
        return 0

    def open_release_rollup_pr_from_action(self, action: dict) -> int:
        self.calls.append(("open_release_rollup_pr_from_action", dict(action)))
        return 0

    def close_managed_item_from_drop_marker(self, action: dict) -> int:
        self.calls.append(("close_managed_item_from_drop_marker", dict(action)))
        return self.close_code

    def merge_pr(self, target: str) -> int:
        self.calls.append(("merge_pr", target))
        return 0

    def render_review_fix_prompt(self, pr_number: int, round_number: int):
        raise AssertionError("review fix should not be dispatched")

    def publish_release_candidate(self, *, candidate_path: str, target_ref: str):
        raise AssertionError("release publish should not be dispatched")


class FakeReviewFixActions(FakeActions):
    def __init__(self, repo: Path) -> None:
        super().__init__()
        self.repo = repo
        self.rendered: list[tuple[int, int]] = []

    def render_review_fix_prompt(self, pr_number: int, round_number: int):
        self.rendered.append((pr_number, round_number))
        prompt_path = ".refactor-loop/prompts/fixes/fix-pr77-round-1.md"
        log_path = ".refactor-loop/logs/fix-pr77-round-1.log"
        (self.repo / prompt_path).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / prompt_path).write_text("headless rendered prompt\n", encoding="utf-8")
        return type("Spec", (), {"prompt_path": prompt_path, "log_path": log_path})()


class FakeHeartbeatLease:
    heartbeat_interval = 7

    def __init__(self) -> None:
        self.beats = 0
        self.coordinator: HeartbeatCoordinator | None = None

    def beat(self) -> None:
        self.beats += 1
        if self.coordinator is not None:
            self.coordinator.record_beat()


class HeartbeatCoordinator:
    def __init__(self) -> None:
        self.condition = REAL_CONDITION()
        self.run_once_entered = False
        self.beat_during_run_once = False
        self.stop_requested = False
        self.waiting_for_stop = False

    def enter_run_once(self) -> None:
        with self.condition:
            self.run_once_entered = True
            self.condition.notify_all()

    def record_beat(self) -> None:
        with self.condition:
            if self.run_once_entered and not self.stop_requested:
                self.beat_during_run_once = True
            self.condition.notify_all()

    def wait_for_heartbeat_while_run_once_is_pending(self) -> bool:
        with self.condition:
            return self.condition.wait_for(lambda: self.beat_during_run_once, timeout=1.0)

    def request_stop(self) -> None:
        with self.condition:
            self.stop_requested = True
            self.condition.notify_all()


class ScriptedEvent:
    instances: list["ScriptedEvent"] = []
    coordinator: HeartbeatCoordinator | None = None

    def __init__(self) -> None:
        self.wait_timeouts: list[float] = []
        self.set_called = False
        ScriptedEvent.instances.append(self)

    def wait(self, timeout: float | None = None) -> bool:
        self.wait_timeouts.append(float(timeout or 0))
        if ScriptedEvent.coordinator is None:
            return True
        coordinator = ScriptedEvent.coordinator
        with coordinator.condition:
            if not coordinator.run_once_entered:
                coordinator.condition.wait_for(lambda: coordinator.run_once_entered or coordinator.stop_requested, timeout=1.0)
            if coordinator.stop_requested:
                return True
            if not coordinator.beat_during_run_once:
                return False
            coordinator.waiting_for_stop = True
            coordinator.condition.notify_all()
            coordinator.condition.wait_for(lambda: coordinator.stop_requested, timeout=1.0)
            return True

    def set(self) -> None:
        self.set_called = True
        if ScriptedEvent.coordinator is not None:
            ScriptedEvent.coordinator.request_stop()


class InlineThread:
    instances: list["InlineThread"] = []

    def __init__(self, *, target, name: str, daemon: bool) -> None:
        self.target = target
        self.name = name
        self.daemon = daemon
        self.started = False
        with mock.patch("threading.Event", REAL_EVENT):
            self.thread = REAL_THREAD(target=self.target, name=name, daemon=daemon)
        self.join_timeouts: list[float] = []
        InlineThread.instances.append(self)

    def start(self) -> None:
        self.started = True
        self.thread.start()

    def join(self, timeout: float | None = None) -> None:
        self.join_timeouts.append(float(timeout or 0))
        self.thread.join(timeout=timeout)


class WakeupRunnerBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        for rel in (".refactor-loop/state", ".refactor-loop/logs", ".refactor-loop/prompts", ".refactor-loop/runs"):
            (self.repo / rel).mkdir(parents=True, exist_ok=True)
        (self.repo / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.repo / ".config/consensus-rnd/host.env").write_text(
            f'export REPO_ROOT="{self.repo}"\n'
            'export GH_REPO_SLUG="owner/repo"\n'
            'export INTEGRATION_BRANCH="auto-refact-dev"\n'
            'export REVIEW_BASE_BRANCH="dev"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.repo, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})
        self.supervisor = FakeSupervisor()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_run_command_injects_gh_repo_only_in_valid_subcommand_position(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            calls.append(list(command))
            return subprocess.CompletedProcess(command, 0, "", "")

        runner = WakeupRunner(self.ctx)
        with mock.patch("codex_refactor_loop.wakeup_runner.subprocess.run", side_effect=fake_run):
            runner._run_command(["gh", "api", "repos/owner/repo/pulls/77"])
            runner._run_command(["gh", "pr", "view", "77", "--json", "mergeable,isDraft"])
            runner._run_command(["gh", "issue", "view", "53", "--json", "state"])

        self.assertEqual(calls[0], ["gh", "api", "repos/owner/repo/pulls/77"])
        self.assertEqual(calls[1], ["gh", "pr", "view", "77", "--repo", "owner/repo", "--json", "mergeable,isDraft"])
        self.assertEqual(calls[2], ["gh", "issue", "view", "53", "--repo", "owner/repo", "--json", "state"])
        for command in calls:
            self.assertNotEqual(command[:2], ["gh", "--repo"])

    def test_run_command_preserves_existing_gh_repo_flag(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            calls.append(list(command))
            return subprocess.CompletedProcess(command, 0, "", "")

        runner = WakeupRunner(self.ctx)
        with mock.patch("codex_refactor_loop.wakeup_runner.subprocess.run", side_effect=fake_run):
            runner._run_command(["gh", "pr", "view", "77", "--repo", "other/repo", "--json", "state"])

        self.assertEqual(calls, [["gh", "pr", "view", "77", "--repo", "other/repo", "--json", "state"]])

    def run_result(
        self,
        plan: dict,
        *,
        gh_state: str | None = "OPEN",
        gh_labels: list[str] | None = None,
        gh_head_ref: str = "refactor/iter77-worker",
        git_diff_code: int = 0,
        duplicate_prs: list[dict] | None = None,
        implementation_base: tuple[str, str] = ("base-sha", "base-sha"),
        actions=None,
    ) -> list:
        def command_runner(command):
            if command[:2] == ["gh", "api"]:
                endpoint = str(command[2]) if len(command) > 2 else ""
                if endpoint == "repos/owner/repo/pulls/77":
                    if gh_state is None:
                        return subprocess.CompletedProcess(command, 1, "", "not found")
                    payload = {"state": str(gh_state).lower(), "head": {"sha": "a" * 40}}
                    if gh_state == "MERGED":
                        payload = {"state": "closed", "merged": True, "head": {"sha": "a" * 40}}
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
                if endpoint == f"repos/owner/repo/commits/{'a' * 40}/check-runs":
                    payload = [{"check_runs": [{"name": "ci", "status": "completed", "conclusion": "success"}]}]
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
                if "/pulls/" in endpoint or "/issues/" in endpoint:
                    if gh_state is None:
                        return subprocess.CompletedProcess(command, 1, "", "not found")
                    payload = {"state": str(gh_state).lower()}
                    if gh_state == "MERGED":
                        payload = {"state": "closed", "merged": True}
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
                return subprocess.CompletedProcess(command, 0, "{}", "")
            if command[:4] == ["gh", "pr", "list", "--state"]:
                return subprocess.CompletedProcess(command, 0, json.dumps(duplicate_prs or []), "")
            if command[:3] == ["gh", "issue", "view"] or command[:3] == ["gh", "pr", "view"]:
                if "labels,body" in command:
                    live_labels = gh_labels if gh_labels is not None else [labels.MANAGED]
                    payload = {"labels": [{"name": name} for name in live_labels], "body": ""}
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
                if "headRefName" in command:
                    if "--jq" not in command:
                        return subprocess.CompletedProcess(command, 0, json.dumps({"headRefName": gh_head_ref}), "")
                    return subprocess.CompletedProcess(command, 0, gh_head_ref + "\n", "")
                if ".headRefOid" in command:
                    return subprocess.CompletedProcess(command, 0, "a" * 40 + "\n", "")
                if "mergeable,isDraft" in command:
                    return subprocess.CompletedProcess(command, 0, json.dumps({"mergeable": "MERGEABLE", "isDraft": False}), "")
                if gh_state is None:
                    return subprocess.CompletedProcess(command, 1, "", "not found")
                return subprocess.CompletedProcess(command, 0, gh_state + "\n", "")
            git_cwd = Path(command[2]).resolve() if len(command) >= 3 and command[:2] == ["git", "-C"] else None
            repo_root = self.ctx.repo_root
            if git_cwd == (self.repo / ".worktrees" / "pr77").resolve():
                return subprocess.CompletedProcess(command, git_diff_code, "", "")
            if git_cwd == (self.repo / ".worktrees" / "iter77-issue-77").resolve():
                if command[3:] == ["diff", "HEAD", "--quiet"]:
                    return subprocess.CompletedProcess(command, git_diff_code, "", "")
                if command[3:] == ["rev-parse", "--abbrev-ref", "HEAD"]:
                    return subprocess.CompletedProcess(command, 0, "refactor/iter77-issue-77\n", "")
                if command[3:] == ["merge-base", "HEAD", "origin/auto-refact-dev"]:
                    return subprocess.CompletedProcess(command, 0, implementation_base[0] + "\n", "")
                if command[3:] == ["rev-parse", "--verify", "origin/auto-refact-dev"]:
                    return subprocess.CompletedProcess(command, 0, implementation_base[1] + "\n", "")
            if git_cwd == repo_root.resolve() and command[3:] == ["worktree", "list", "--porcelain"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    f"worktree {repo_root}\nbranch refs/heads/auto-refact-dev\n\n"
                    f"worktree {repo_root / '.worktrees' / 'iter77-worker'}\nbranch refs/heads/{gh_head_ref}\n\n",
                    "",
                )
            return subprocess.CompletedProcess(command, 0, "", "")

        runner = WakeupRunner(
            self.ctx,
            plan_loader=lambda _repo: plan,
            actions=actions,
            supervisor=self.supervisor,
            command_runner=command_runner,
        )
        return runner.run_once()

    def base_plan(self, action: dict) -> dict:
        return {
            "schema": "wakeup-plan",
            "mode": "closed-action-projection",
            "apply_authority": "wakeup-runner-396-only",
            "no_lifecycle_authority": True,
            "actions": [action],
        }

    def batch_plan(self, actions: list[dict], *, dispatch_required: object, deficit: object, active: bool = True) -> dict:
        return {
            "schema": "wakeup-plan",
            "mode": "closed-action-projection",
            "apply_authority": "wakeup-runner-396-only",
            "no_lifecycle_authority": True,
            "concurrency": {"deficit": deficit},
            "hard_gate": {"active": active, "dispatch_required": dispatch_required},
            "actions": actions,
        }

    def spawn_action(self, **overrides) -> dict:
        prompt = self.repo / ".refactor-loop/prompts/task.md"
        prompt.write_text("hello\n", encoding="utf-8")
        marker = "intent-marker"
        action = {
            "kind": "harness-spawn-intent",
            "action_id": "spawn:1",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "source_artifact_contains_evidence", "target_log_absent"],
            "source_artifact": ".refactor-loop/.controller-pending-events.log",
            "source_marker": marker,
            "target_kind": "codex",
            "target_number": None,
            "target": {"kind": "codex", "task_id": "task"},
            "controller_action": "spawn_codex_harness_background",
            "no_generic_command": True,
            "cd": str(self.repo),
            "prompt": str(prompt),
            "log": str(self.repo / ".refactor-loop/logs/task.log"),
            "stall": 30,
        }
        action.update(overrides)
        pending = self.repo / ".refactor-loop/.controller-pending-events.log"
        with pending.open("a", encoding="utf-8") as handle:
            handle.write(marker + "\n")
        return action

    def review_gate_action(self, **overrides) -> dict:
        marker = "REVIEW_GATE_READY:77"
        pending = self.repo / ".refactor-loop/.controller-pending-events.log"
        with pending.open("a", encoding="utf-8") as handle:
            handle.write(marker + "\n")
        action = {
            "kind": "review-gate",
            "action_id": "review-gate:77:sha",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "live_open_target"],
            "source_artifact": ".refactor-loop/.controller-pending-events.log",
            "source_marker": marker,
            "target_kind": "PR",
            "target_number": 77,
            "target": {"kind": "PR", "number": 77},
            "controller_action": "review_gate",
            "no_generic_command": True,
            "head_sha": "a" * 40,
        }
        action.update(overrides)
        return action

    def close_action(self, **overrides) -> dict:
        marker = "META_RESOLVED:drop:no-action"
        pending = self.repo / ".refactor-loop/.controller-pending-events.log"
        pending.write_text(marker + "\n", encoding="utf-8")
        action = {
            "kind": "completed-marker",
            "action_id": "close-managed-item:53",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "live_open_target", "live_managed_target"],
            "source_artifact": ".refactor-loop/.controller-pending-events.log",
            "source_marker": marker,
            "target_kind": "issue",
            "target_number": 53,
            "target": {"kind": "issue", "number": 53},
            "controller_action": "close_managed_item_from_drop_marker",
            "no_generic_command": True,
        }
        action.update(overrides)
        return action

    def assert_blocked_ledger(self, action_id: str, reason: str) -> None:
        rows = [
            json.loads(line)
            for line in (self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertIn(
            {"action_id": action_id, "status": "blocked", "reason": reason},
            [{"action_id": row["action_id"], "status": row["status"], "reason": row["reason"]} for row in rows],
        )

    def assert_blocked_event(self, action_id: str, reason: str) -> None:
        pending = (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn(f"WAKEUP_RUNNER_BLOCKED:{action_id}:{reason}", pending)

    def assert_blocked_before_dispatch(self, results: list, action_id: str, reason: str, actions: FakeActions) -> None:
        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, reason)
        self.assertEqual(actions.calls, [])
        self.assertEqual(self.supervisor.calls, [])
        self.assert_blocked_ledger(action_id, reason)
        self.assert_blocked_event(action_id, reason)

    def worker_output_action(self, **overrides) -> dict:
        worktree = self.repo / ".worktrees" / "pr77"
        worktree.mkdir(parents=True, exist_ok=True)
        marker = "UNPUSHED_WORKER_OUTPUT:77:2"
        action = {
            "kind": "unpushed-worker-output",
            "action_id": "unpushed-worker-output:77:local-sha",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "verified_pr_head", "clean_scoped_diff"],
            "source_artifact": str(worktree),
            "source_marker": marker,
            "target_kind": "PR",
            "target_number": 77,
            "target": {"kind": "PR", "number": 77},
            "controller_action": "safe_push",
            "no_generic_command": True,
            "head_ref": "refactor/iter77-worker",
            "worktree": str(worktree),
        }
        action.update(overrides)
        return action

    def implementation_output_action(self, **overrides) -> dict:
        worktree = self.repo / ".worktrees" / "iter77-issue-77"
        worktree.mkdir(parents=True, exist_ok=True)
        marker = "IMPLEMENT_DONE:issue-77:ok"
        log = self.repo / ".refactor-loop/logs/implement-issue77.log"
        log.write_text(f"{marker}\nEXIT=0\n", encoding="utf-8")
        action = {
            "kind": "completed-marker",
            "action_id": "completed-marker:implement-issue77.log:IMPLEMENT_DONE:issue-77:ok",
            "runner_authority": "wakeup-runner-396",
            "preconditions": [
                "active_controller_owner",
                "clean_exit_source_marker",
                "canonical_implementation_identity",
                "fresh_integration_base",
                "clean_scoped_diff",
                "host_checks_green",
                "single_linked_managed_issue",
                "no_duplicate_open_pr",
            ],
            "source_artifact": ".refactor-loop/logs/implement-issue77.log",
            "source_marker": marker,
            "target_kind": "issue",
            "target_number": 77,
            "target": {"kind": "issue", "number": 77},
            "controller_action": "publish_implementation_output",
            "no_generic_command": True,
            "head_ref": "refactor/iter77-issue-77",
            "worktree": str(worktree),
        }
        action.update(overrides)
        return action

    def release_rollup_action(self, **overrides) -> dict:
        body = self.repo / ".refactor-loop/runs/release-rollup-pr-body.md"
        body.write_text("## rollup\n\nbody\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        marker = 'DEV_SYNC_PENDING:release-rollup-needed:{"integration_sha":"abc123"}'
        pending = self.repo / ".refactor-loop/.controller-pending-events.log"
        pending.write_text(marker + "\n", encoding="utf-8")
        action = {
            "kind": "release-rollup-needed",
            "action_id": "release-rollup-needed:abc123",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "source_artifact_contains_evidence", "release_rollup_event"],
            "source_artifact": ".refactor-loop/.controller-pending-events.log",
            "source_marker": marker,
            "target_kind": "release-rollup",
            "target_number": None,
            "target": {"kind": "release-rollup", "integration_sha": "abc123"},
            "controller_action": "open_release_rollup_pr_from_action",
            "no_generic_command": True,
            "event": {"integration_sha": "abc123"},
            "body_file": ".refactor-loop/runs/release-rollup-pr-body.md",
        }
        action.update(overrides)
        return action

    def reviewer_dispatch_action(self, **overrides) -> dict:
        marker = "FIX_DONE:414:round-2:applied-1:rejected-0:blocked-0"
        log = self.repo / ".refactor-loop/logs/fix-pr77-r3.log"
        log.write_text(f"{marker}\nEXIT=0\n", encoding="utf-8")
        action = {
            "kind": "completed-marker",
            "action_id": "completed-marker:fix-pr77-r3.log:FIX_DONE:414:round-2:applied-1:rejected-0:blocked-0",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "clean_exit_source_marker", "live_open_target_if_present"],
            "source_artifact": ".refactor-loop/logs/fix-pr77-r3.log",
            "source_marker": marker,
            "target_kind": "PR",
            "target_number": 77,
            "target": {"kind": "PR", "number": 77},
            "controller_action": "dispatch_reviewers",
            "no_generic_command": True,
        }
        action.update(overrides)
        return action

    def consensus_action(self, **overrides) -> dict:
        artifact = self.repo / ".refactor-loop/runs/phase9-issue20-r5-judge.md"
        artifact.write_text(
            "## PROJECT_RULES clause violated\nold\n\n"
            "## Concrete plan\n- `skills/codex-refactor-loop/scripts/codex_refactor_loop/wakeup_plan.py`: scope.\n\n"
            "META_JUDGE_DONE:consensus:structural\n",
            encoding="utf-8",
        )
        marker = "META_JUDGE_DONE:consensus:structural"
        log = self.repo / ".refactor-loop/logs/phase9-issue20-r5-judge.log"
        log.write_text(f"{marker}\nEXIT=0\n", encoding="utf-8")
        action = {
            "kind": "completed-marker",
            "action_id": "completed-marker:phase9-issue20-r5-judge.log:META_JUDGE_DONE:consensus:structural",
            "runner_authority": "wakeup-runner-396",
            "preconditions": [
                "active_controller_owner",
                "clean_exit_source_marker",
                "live_open_target_if_present",
                "durable_consensus_artifact",
                "consensus_implementation_ready",
            ],
            "source_artifact": ".refactor-loop/logs/phase9-issue20-r5-judge.log",
            "source_marker": marker,
            "target_kind": "issue",
            "target_number": 20,
            "target": {"kind": "issue", "number": 20},
            "controller_action": "dispatch_consensus_implementation",
            "no_generic_command": True,
            "consensus_artifact": ".refactor-loop/runs/phase9-issue20-r5-judge.md",
            "design_decision_path": ".refactor-loop/runs/phase9-issue20-r5-judge.md",
            "consensus_issue": 20,
            "consensus_round": 5,
            "cluster_id": "issue-20",
            "iteration": "20",
            "scope_paths": "- skills/codex-refactor-loop/scripts/codex_refactor_loop/wakeup_plan.py",
            "old_pattern": "old",
            "new_principle": "new",
        }
        action.update(overrides)
        return action

    def design_consensus_spawn_action(self, **overrides) -> dict:
        prompt = self.repo / ".refactor-loop/prompts/phase9/phase9-issue104-r2-judge.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("design consensus\n", encoding="utf-8")
        pending = self.repo / ".refactor-loop/.controller-pending-events.log"
        marker = "HARNESS_SPAWN_INTENT phase9-issue104-r2-judge"
        pending.write_text(marker + "\n", encoding="utf-8")
        action = {
            "kind": "harness-spawn-intent",
            "action_id": "harness-spawn-intent:phase9-router:104:2:judge",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "source_artifact_contains_evidence", "target_log_absent"],
            "source_artifact": ".refactor-loop/.controller-pending-events.log",
            "source_marker": marker,
            "target_kind": "codex",
            "target_number": None,
            "target": {"kind": "codex", "task_id": "phase9-issue104-r2-judge"},
            "controller_action": "spawn_codex_harness_background",
            "no_generic_command": True,
            "cd": str(self.repo),
            "prompt": str(prompt),
            "log": str(self.repo / ".refactor-loop/logs/phase9-issue104-r2-judge.log"),
            "stall": 30,
        }
        action.update(overrides)
        return action

    def test_valid_harness_spawn_executes_through_checked_supervisor(self) -> None:
        with mock.patch("codex_refactor_loop.processes.subprocess.Popen") as popen:
            results = self.run_result(self.base_plan(self.spawn_action()))

        self.assertEqual(results[0].status, "applied")
        popen.assert_called_once()
        args, kwargs = popen.call_args
        command = args[0]
        self.assertIn("spawn-codex", command)
        self.assertIn(str(self.repo.resolve()), command[0])
        self.assertEqual(command[command.index("--cd") + 1], str(self.repo))
        self.assertEqual(command[command.index("--prompt") + 1], str(self.repo / ".refactor-loop/prompts/task.md"))
        self.assertEqual(command[command.index("--log") + 1], str(self.repo / ".refactor-loop/logs/task.log"))
        self.assertEqual(command[command.index("--stall") + 1], "30")
        self.assertTrue(kwargs["start_new_session"])
        popen.return_value.wait.assert_not_called()
        popen.return_value.poll.assert_not_called()

    def test_wakeup_runner_batches_spawn_actions_up_to_hard_gate_dispatch_required(self) -> None:
        actions = [self.spawn_action(action_id=f"spawn:{index}", log=str(self.repo / f".refactor-loop/logs/task-{index}.log")) for index in range(3)]

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.batch_plan(actions, dispatch_required=3, deficit=5))

        self.assertEqual([result.status for result in results], ["applied", "applied", "applied"])
        self.assertEqual([result.action_id for result in results], ["spawn:0", "spawn:1", "spawn:2"])
        self.assertEqual(launch.call_count, 3)

    def test_wakeup_runner_spawn_batch_does_not_overshoot_dispatch_required(self) -> None:
        actions = [self.spawn_action(action_id=f"spawn:{index}", log=str(self.repo / f".refactor-loop/logs/task-{index}.log")) for index in range(4)]

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.batch_plan(actions, dispatch_required=2, deficit=4))

        self.assertEqual([result.action_id for result in results], ["spawn:0", "spawn:1"])
        self.assertEqual([result.status for result in results], ["applied", "applied"])
        self.assertEqual(launch.call_count, 2)

    def test_wakeup_runner_spawn_batch_uses_deficit_as_upper_bound(self) -> None:
        actions = [self.spawn_action(action_id=f"spawn:{index}", log=str(self.repo / f".refactor-loop/logs/task-{index}.log")) for index in range(3)]

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.batch_plan(actions, dispatch_required=5, deficit=2))

        self.assertEqual([result.action_id for result in results], ["spawn:0", "spawn:1"])
        self.assertEqual(launch.call_count, 2)

    def test_wakeup_runner_headless_harness_spawn_intents_launch_to_deficit(self) -> None:
        actions = [
            self.design_consensus_spawn_action(
                action_id=f"harness-spawn-intent:phase9-router:{issue}:1:minimal",
                target={"kind": "codex", "task_id": f"phase9-issue{issue}-r1-minimal"},
                prompt=str(self.repo / ".refactor-loop/prompts/phase9/phase9-issue104-r2-judge.md"),
                log=str(self.repo / f".refactor-loop/logs/phase9-issue{issue}-r1-minimal.log"),
            )
            for issue in (104, 105, 106)
        ]

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.batch_plan(actions, dispatch_required=2, deficit=2), gh_state="OPEN", actions=FakeActions())

        self.assertEqual([result.status for result in results], ["applied", "applied"])
        self.assertEqual([result.action_id for result in results], [actions[0]["action_id"], actions[1]["action_id"]])
        self.assertEqual(launch.call_count, 2)

    def test_wakeup_runner_stale_applied_spawn_ledger_retries_headless_intent(self) -> None:
        action = self.design_consensus_spawn_action(action_id="harness-spawn-intent:phase9-router:104:1:minimal-retry")
        ledger = self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl"
        ledger.write_text(
            json.dumps({"action_id": action["action_id"], "status": "applied", "reason": "", "kind": "harness-spawn-intent"})
            + "\n",
            encoding="utf-8",
        )

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.base_plan(action), gh_state="OPEN", actions=FakeActions())

        self.assertEqual(results[0].status, "applied")
        launch.assert_called_once()
        pending = (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("WAKEUP_RUNNER_STALE_SPAWN_LEDGER:harness-spawn-intent:phase9-router:104:1:minimal-retry:target-log-absent", pending)

    def test_wakeup_runner_spawn_duplicate_does_not_block_later_spawn_batch(self) -> None:
        duplicate = self.spawn_action(action_id="spawn:duplicate", log=str(self.repo / ".refactor-loop/logs/duplicate.log"))
        later = self.spawn_action(action_id="spawn:later", log=str(self.repo / ".refactor-loop/logs/later.log"))
        Path(duplicate["log"]).write_text("SPAWN\n", encoding="utf-8")
        ledger = self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl"
        ledger.write_text(
            json.dumps({"action_id": duplicate["action_id"], "status": "applied", "reason": "", "kind": "harness-spawn-intent"})
            + "\n",
            encoding="utf-8",
        )

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.batch_plan([duplicate, later], dispatch_required=2, deficit=2))

        self.assertEqual([result.status for result in results], ["skipped", "applied"])
        self.assertEqual([result.action_id for result in results], ["spawn:duplicate", "spawn:later"])
        launch.assert_called_once()

    def test_wakeup_runner_missing_or_invalid_budget_keeps_single_apply_compatibility(self) -> None:
        cases = (
            ("missing", self.base_plan),
            ("inactive", lambda actions: self.batch_plan(actions, dispatch_required=2, deficit=2, active=False)),
            ("zero", lambda actions: self.batch_plan(actions, dispatch_required=0, deficit=2)),
            ("non-int", lambda actions: self.batch_plan(actions, dispatch_required="2", deficit=2)),
            ("missing-deficit", lambda actions: self.batch_plan(actions, dispatch_required=2, deficit=None)),
        )
        for name, plan_factory in cases:
            with self.subTest(name=name):
                actions = [
                    self.spawn_action(action_id=f"{name}:spawn:0", log=str(self.repo / f".refactor-loop/logs/{name}-0.log")),
                    self.spawn_action(action_id=f"{name}:spawn:1", log=str(self.repo / f".refactor-loop/logs/{name}-1.log")),
                ]
                plan = plan_factory(actions) if name != "missing" else self.base_plan(actions[0])
                if name == "missing":
                    plan["actions"] = actions
                with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
                    results = self.run_result(plan)

                self.assertEqual([result.action_id for result in results], [actions[0]["action_id"]])
                self.assertEqual(launch.call_count, 1)

    def test_wakeup_runner_non_spawn_action_does_not_consume_batch_budget(self) -> None:
        first = self.close_action(action_id="close-managed-item:53:first")
        second = self.close_action(action_id="close-managed-item:53:second")
        actions = FakeActions()

        results = self.run_result(self.batch_plan([first, second], dispatch_required=3, deficit=3), actions=actions)

        self.assertEqual([result.action_id for result in results], ["close-managed-item:53:first"])
        self.assertEqual([result.status for result in results], ["applied"])
        self.assertEqual([call[0] for call in actions.calls], ["close_managed_item_from_drop_marker"])

    def test_wakeup_runner_blocked_lifecycle_action_does_not_dead_stop_later_spawn_batch(self) -> None:
        blocked = self.implementation_output_action(
            action_id="publish-implementation:missing-verified-head-before-spawn",
            preconditions=[
                "active_controller_owner",
                "clean_exit_source_marker",
                "clean_scoped_diff",
                "host_checks_green",
                "single_linked_managed_issue",
                "no_duplicate_open_pr",
            ],
        )
        later = self.spawn_action(action_id="spawn:after-blocked-lifecycle")
        actions = FakeActions()

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.batch_plan([blocked, later], dispatch_required=2, deficit=2), actions=actions)

        self.assertEqual(
            [(result.action_id, result.status, result.reason) for result in results],
            [
                (
                    "publish-implementation:missing-verified-head-before-spawn",
                    "blocked",
                    "publish_implementation_missing_precondition:canonical_implementation_identity",
                ),
                ("spawn:after-blocked-lifecycle", "applied", ""),
            ],
        )
        self.assertEqual(actions.calls, [])
        launch.assert_called_once()
        self.assert_blocked_event(
            "publish-implementation:missing-verified-head-before-spawn",
            "publish_implementation_missing_precondition:canonical_implementation_identity",
        )

    def test_wakeup_runner_rejects_design_consensus_redispatch_actions(self) -> None:
        issue = 496
        (self.repo / f".refactor-loop/logs/phase9-issue{issue}-r1-delete.log").write_text(
            "SOLVER_DONE:delete:abstain:genuine-gap\nEXIT=0\n", encoding="utf-8"
        )
        action = {
            "kind": "completed-marker",
            "action_id": f"completed-marker:phase9-issue{issue}-r1-delete.log:SOLVER_DONE:delete:abstain:genuine-gap",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "clean_exit_source_marker", "live_open_target"],
            "source_artifact": f".refactor-loop/logs/phase9-issue{issue}-r1-delete.log",
            "source_marker": "SOLVER_DONE:delete:abstain:genuine-gap",
            "target_kind": "issue",
            "target_number": issue,
            "target": {"kind": "issue", "number": issue},
            "controller_action": "dispatch_design_consensus",
            "no_generic_command": True,
        }
        actions = FakeActions()

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(
                self.batch_plan([action], dispatch_required=1, deficit=1),
                gh_state="OPEN",
                actions=actions,
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action_id, action["action_id"])
        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "unsupported_controller_action:dispatch_design_consensus")
        launch.assert_not_called()

    def test_wakeup_runner_blocked_non_spawn_actions_do_not_dead_stop_spawn_batch(self) -> None:
        blocked_publish = self.implementation_output_action(
            action_id="publish-implementation:missing-verified-head-before-spawns",
            preconditions=[
                "active_controller_owner",
                "clean_exit_source_marker",
                "clean_scoped_diff",
                "host_checks_green",
                "single_linked_managed_issue",
                "no_duplicate_open_pr",
            ],
        )
        spawns = [
            self.spawn_action(
                action_id=f"spawn:after-blockers:{index}",
                log=str(self.repo / f".refactor-loop/logs/after-blockers-{index}.log"),
            )
            for index in range(3)
        ]
        blocked_close = self.close_action(
            action_id="close-managed-item:53:closed-before-spawns",
            preconditions=["active_controller_owner", "live_managed_target"],
        )
        (self.repo / ".refactor-loop/.controller-pending-events.log").write_text(
            "META_RESOLVED:drop:no-action\nintent-marker\n",
            encoding="utf-8",
        )
        actions = FakeActions()

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(
                self.batch_plan([blocked_publish, blocked_close, *spawns], dispatch_required=3, deficit=3),
                actions=actions,
            )

        self.assertEqual(
            [(result.action_id, result.status, result.reason) for result in results],
            [
                (
                    "publish-implementation:missing-verified-head-before-spawns",
                    "blocked",
                    "publish_implementation_missing_precondition:canonical_implementation_identity",
                ),
                (
                    "close-managed-item:53:closed-before-spawns",
                    "blocked",
                    "close_managed_drop_missing_precondition:live_open_target",
                ),
                ("spawn:after-blockers:0", "applied", ""),
                ("spawn:after-blockers:1", "applied", ""),
                ("spawn:after-blockers:2", "applied", ""),
            ],
        )
        self.assertEqual(actions.calls, [])
        self.assertEqual(launch.call_count, 3)
        self.assert_blocked_event(
            "publish-implementation:missing-verified-head-before-spawns",
            "publish_implementation_missing_precondition:canonical_implementation_identity",
        )
        self.assert_blocked_event(
            "close-managed-item:53:closed-before-spawns",
            "close_managed_drop_missing_precondition:live_open_target",
        )

    def test_wakeup_runner_tick_reports_later_solver_launch_after_blocked_lifecycle(self) -> None:
        results = [
            RunnerResult(
                "publish-implementation:missing-verified-head-before-spawn",
                "blocked",
                "publish_implementation_missing_precondition:canonical_implementation_identity",
            ),
            RunnerResult("harness-spawn-intent:phase9-router:493:1:minimal", "applied", ""),
            RunnerResult("harness-spawn-intent:phase9-router:493:1:structural", "applied", ""),
            RunnerResult("harness-spawn-intent:phase9-router:493:1:delete", "applied", ""),
        ]

        action = _wakeup_tick_action(results)
        # still reports the dispatched spawn as the headline
        self.assertIn("dispatched harness-spawn-intent:phase9-router:493:1:minimal+2", action)
        # and now also surfaces the blocked lifecycle action that used to be hidden
        self.assertIn("blocked:publish_implementation_missing_precondition", action)
        # and the per-status counts for the whole tick
        self.assertIn("[applied=3,blocked=1]", action)

    def test_wakeup_runner_blocked_non_spawn_can_continue_to_one_later_lifecycle_action(self) -> None:
        blocked = self.implementation_output_action(
            action_id="publish-implementation:missing-verified-head-before-close",
            preconditions=[
                "active_controller_owner",
                "clean_exit_source_marker",
                "clean_scoped_diff",
                "host_checks_green",
                "single_linked_managed_issue",
                "no_duplicate_open_pr",
            ],
        )
        close = self.close_action(action_id="close-managed-item:53:after-blocked-publish")
        actions = FakeActions()

        results = self.run_result(self.batch_plan([blocked, close], dispatch_required=2, deficit=2), actions=actions)

        self.assertEqual(
            [(result.action_id, result.status) for result in results],
            [
                ("publish-implementation:missing-verified-head-before-close", "blocked"),
                ("close-managed-item:53:after-blocked-publish", "applied"),
            ],
        )
        self.assertEqual([call[0] for call in actions.calls], ["close_managed_item_from_drop_marker"])

    def test_wakeup_runner_lifecycle_review_gate_not_starved_after_spawn_batch(self) -> None:
        for role, verdict in (("architect", "approve"), ("tests", "approve"), ("quality", "comment")):
            (self.repo / ".refactor-loop/prompts" / f"review-pr77-{role}-r1.md").write_text(
                f"head_sha: {'a' * 40}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/runs" / f"review-pr77-{role}-r1.md").write_text(
                f"---\nverdict: {verdict}\n---\nREVIEW_DONE:77:{role}:{verdict}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/logs" / f"review-pr77-{role}-r1.log").write_text(
                f"REVIEW_DONE:77:{role}:{verdict}\nEXIT=0\n",
                encoding="utf-8",
            )
        first = self.spawn_action(action_id="spawn:before-review-gate:1", log=str(self.repo / ".refactor-loop/logs/spawn-before-review-1.log"))
        second = self.spawn_action(action_id="spawn:before-review-gate:2", log=str(self.repo / ".refactor-loop/logs/spawn-before-review-2.log"))
        gate = self.review_gate_action(action_id="review-gate:77:after-spawns")
        actions = FakeActions()

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.batch_plan([first, second, gate], dispatch_required=2, deficit=2), actions=actions)

        self.assertEqual(
            [(result.action_id, result.status) for result in results],
            [
                ("spawn:before-review-gate:1", "applied"),
                ("spawn:before-review-gate:2", "applied"),
                ("review-gate:77:after-spawns", "applied"),
            ],
        )
        self.assertEqual(launch.call_count, 2)
        self.assertEqual(actions.calls, [("merge_pr", "77")])

    def test_wakeup_runner_headless_review_fix_dispatch_uses_fully_rendered_prompt(self) -> None:
        worktree = self.repo / ".worktrees" / "iter77-worker"
        worktree.mkdir(parents=True, exist_ok=True)

        for role, verdict in (("architect", "approve"), ("tests", "reject"), ("quality", "comment")):
            (self.repo / ".refactor-loop/prompts" / f"review-pr77-{role}-r1.md").write_text(
                f"head_sha: {'a' * 40}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/runs" / f"review-pr77-{role}-r1.md").write_text(
                f"---\nverdict: {verdict}\n---\nREVIEW_DONE:77:{role}:{verdict}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/logs" / f"review-pr77-{role}-r1.log").write_text(
                f"REVIEW_DONE:77:{role}:{verdict}\nEXIT=0\n",
                encoding="utf-8",
            )
        gate = self.review_gate_action(action_id="review-gate:77:headless-fix")
        actions = FakeReviewFixActions(self.repo)

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.base_plan(gate), gh_state="OPEN", actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.rendered, [(77, 1)])
        launch.assert_called_once()
        self.assertEqual(Path(launch.call_args.kwargs["cd"]).resolve(), worktree.resolve())
        self.assertNotEqual(Path(launch.call_args.kwargs["cd"]).resolve(), self.repo.resolve())
        self.assertEqual(tuple(Path(path).resolve() for path in launch.call_args.kwargs["add_dirs"]), (self.ctx.repo_root.resolve(),))
        prompt = Path(launch.call_args.kwargs["prompt"])
        self.assertEqual(prompt.resolve(), (self.repo / ".refactor-loop/prompts/fixes/fix-pr77-round-1.md").resolve())
        self.assertNotIn("${", prompt.read_text(encoding="utf-8"))

    def test_wakeup_runner_headless_review_fix_fails_closed_when_pr_worktree_missing(self) -> None:
        for role, verdict in (("architect", "approve"), ("tests", "reject"), ("quality", "comment")):
            (self.repo / ".refactor-loop/prompts" / f"review-pr77-{role}-r1.md").write_text(
                f"head_sha: {'a' * 40}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/runs" / f"review-pr77-{role}-r1.md").write_text(
                f"---\nverdict: {verdict}\n---\nREVIEW_DONE:77:{role}:{verdict}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/logs" / f"review-pr77-{role}-r1.log").write_text(
                f"REVIEW_DONE:77:{role}:{verdict}\nEXIT=0\n",
                encoding="utf-8",
            )
        gate = self.review_gate_action(action_id="review-gate:77:missing-fix-worktree")
        actions = FakeReviewFixActions(self.repo)

        def command_runner(command):
            repo_root = self.ctx.repo_root
            if command == ["gh", "api", "repos/owner/repo/pulls/77"]:
                return subprocess.CompletedProcess(command, 0, json.dumps({"state": "open", "head": {"sha": "a" * 40}}), "")
            if command[:3] == ["gh", "api", f"repos/owner/repo/commits/{'a' * 40}/check-runs"]:
                payload = [{"check_runs": [{"name": "ci", "status": "completed", "conclusion": "success"}]}]
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
            if command[:3] == ["gh", "pr", "view"]:
                if "headRefName" in command and "--jq" not in command:
                    return subprocess.CompletedProcess(command, 0, json.dumps({"headRefName": "refactor/iter77-worker"}), "")
                if ".headRefOid" in command:
                    return subprocess.CompletedProcess(command, 0, "a" * 40 + "\n", "")
                if "mergeable,isDraft" in command:
                    return subprocess.CompletedProcess(command, 0, json.dumps({"mergeable": "MERGEABLE", "isDraft": False}), "")
            if command == ["git", "-C", str(repo_root), "worktree", "list", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, f"worktree {repo_root}\nbranch refs/heads/auto-refact-dev\n\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        runner = WakeupRunner(
            self.ctx,
            plan_loader=lambda _repo: self.base_plan(gate),
            actions=actions,
            supervisor=self.supervisor,
            command_runner=command_runner,
        )

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = runner.run_once()

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "helper_exit:3")
        launch.assert_not_called()
        pending = (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("WAKEUP_RUNNER_REVIEW_FIX_WORKTREE_MISSING:77:refactor/iter77-worker", pending)

    def test_wakeup_runner_skips_blocked_spawn_validation_and_scans_later_actions(self) -> None:
        first = self.spawn_action(action_id="spawn:first", log=str(self.repo / ".refactor-loop/logs/first.log"))
        blocked = self.spawn_action(action_id="spawn:blocked", log=str(self.repo / ".refactor-loop/logs/blocked.log"))
        later = self.spawn_action(action_id="spawn:later", log=str(self.repo / ".refactor-loop/logs/later.log"))
        Path(blocked["log"]).write_text("already claimed\n", encoding="utf-8")

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.batch_plan([first, blocked, later], dispatch_required=3, deficit=3))

        self.assertEqual([result.action_id for result in results], ["spawn:first", "spawn:blocked", "spawn:later"])
        self.assertEqual([result.status for result in results], ["applied", "blocked", "applied"])
        self.assertEqual(results[1].reason, "target_log_exists")
        self.assertEqual(launch.call_count, 2)
        self.assert_blocked_event("spawn:blocked", "target_log_exists")

    def test_wakeup_runner_stops_on_spawn_launch_failure_without_scanning_later_actions(self) -> None:
        first = self.spawn_action(action_id="spawn:launch-fails", log=str(self.repo / ".refactor-loop/logs/launch-fails.log"))
        later = self.spawn_action(action_id="spawn:after-launch-failure", log=str(self.repo / ".refactor-loop/logs/after-launch-failure.log"))

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=3) as launch:
            results = self.run_result(self.batch_plan([first, later], dispatch_required=2, deficit=2))

        self.assertEqual([result.action_id for result in results], ["spawn:launch-fails"])
        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "helper_exit:3")
        self.assertEqual(launch.call_count, 1)

    def test_wakeup_runner_records_helper_exit_source_for_spawn_supervisor_failure(self) -> None:
        action = self.spawn_action(action_id="spawn:supervisor-exit-3")

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=3):
            results = self.run_result(self.base_plan(action))

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "helper_exit:3")
        pending = (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("WAKEUP_RUNNER_SPAWN_LAUNCH_EXIT:spawn:supervisor-exit-3:3", pending)
        self.assertIn("WAKEUP_RUNNER_HELPER_EXIT:spawn:supervisor-exit-3:spawn_codex_harness_background:3", pending)

    def test_harness_spawn_existing_target_log_blocks_before_supervisor(self) -> None:
        actions = FakeActions()
        action = self.spawn_action(action_id="spawn:target-log-exists")
        Path(action["log"]).write_text("existing worker\n", encoding="utf-8")

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assert_blocked_before_dispatch(results, "spawn:target-log-exists", "target_log_exists", actions)

    def test_harness_spawn_failed_target_log_retries_supervisor(self) -> None:
        action = self.spawn_action(action_id="spawn:failed-target-log")
        Path(action["log"]).write_text("SPAWN_FAILED=codex missing\nEXIT=127\n", encoding="utf-8")

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.base_plan(action), actions=FakeActions())

        self.assertEqual([(result.action_id, result.status, result.reason) for result in results], [("spawn:failed-target-log", "applied", "")])
        launch.assert_called_once()

    def test_forbidden_fields_fail_closed(self) -> None:
        for field in ("argv", "command_line", "lifecycle_authority", "lifecycle_owner", "target_ref"):
            with self.subTest(field=field):
                results = self.run_result(self.base_plan(self.spawn_action(action_id=f"forbidden:{field}", **{field: "forbidden"})))

                self.assertEqual(results[0].status, "blocked")
                self.assertEqual(results[0].reason, f"forbidden_fields:{field}")
                self.assertEqual(self.supervisor.calls, [])

    def test_malformed_plan_envelope_blocks_before_dispatch_and_records_ledger(self) -> None:
        actions = FakeActions()
        plan = self.base_plan(self.spawn_action())
        plan["schema"] = "wrong-schema"

        results = self.run_result(plan, actions=actions)

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "schema_mismatch")
        self.assertEqual(actions.calls, [])
        self.assertEqual(self.supervisor.calls, [])
        self.assert_blocked_ledger("", "schema_mismatch")

    def test_malformed_action_authorization_blocks_before_dispatch_and_records_event(self) -> None:
        cases = [
            ("runner-authority", self.spawn_action(action_id="auth:runner", runner_authority="controller"), "runner_authority_mismatch"),
            ("generic-command", self.spawn_action(action_id="auth:generic", no_generic_command=False), "missing_no_generic_command"),
            ("preconditions", self.spawn_action(action_id="auth:preconditions", preconditions=[]), "missing_preconditions"),
        ]

        for name, action, reason in cases:
            with self.subTest(name=name):
                actions = FakeActions()
                results = self.run_result(self.base_plan(action), actions=actions)
                self.assert_blocked_before_dispatch(results, action["action_id"], reason, actions)

    def test_non_owner_noops(self) -> None:
        with mock.patch("codex_refactor_loop.wakeup_runner.require_active_controller") as owner:
            owner.return_value = type("Decision", (), {"allowed": False, "status": "not-owner", "action": "wakeup-runner", "owner_device": "other", "lease_id": "", "expires_at": ""})()

            results = self.run_result(self.base_plan(self.spawn_action()))

        self.assertEqual(results[0].status, "noop")
        self.assertEqual(self.supervisor.calls, [])

    def test_missing_evidence_fails_closed(self) -> None:
        action = self.spawn_action(source_marker="missing")

        results = self.run_result(self.base_plan(action))

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "source_marker_missing")

    def test_missing_clean_exit_source_marker_blocks_before_dispatch_and_records_event(self) -> None:
        source = self.repo / ".refactor-loop/logs/worker.log"
        marker = "IMPLEMENT_DONE:pr-77:ok"
        source.write_text(marker + "\n", encoding="utf-8")
        action = self.spawn_action(
            action_id="clean-exit:missing",
            preconditions=["active_controller_owner", "clean_exit_source_marker"],
            source_artifact=".refactor-loop/logs/worker.log",
            source_marker=marker,
        )
        actions = FakeActions()

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assert_blocked_before_dispatch(results, "clean-exit:missing", "clean_exit_marker_missing", actions)

    def test_unknown_executable_controller_action_fails_closed_without_unapplied_success(self) -> None:
        action = self.spawn_action(controller_action="dispatch_remote_ci_fix", action_id="ci-red:31:sha")

        results = self.run_result(self.base_plan(action))

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "unsupported_controller_action:dispatch_remote_ci_fix")
        ledger = (self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl").read_text(encoding="utf-8")
        self.assertIn('"status": "blocked"', ledger)
        self.assertNotIn('"status": "applied"', ledger)
        pending = (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("WAKEUP_RUNNER_BLOCKED:ci-red:31:sha:unsupported_controller_action:dispatch_remote_ci_fix", pending)
        self.assertNotIn("WAKEUP_RUNNER_UNAPPLIED", pending)

    def test_safe_push_routes_to_named_helper_after_head_and_clean_diff_revalidation(self) -> None:
        actions = FakeActions()

        results = self.run_result(self.base_plan(self.worker_output_action()), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(
            actions.calls,
            [
                (
                    "safe_push",
                    {
                        "remote": "origin",
                        "branch": "refactor/iter77-worker",
                        "worktree": str(self.repo / ".worktrees" / "pr77"),
                    },
                )
            ],
        )

    def test_safe_push_helper_nonzero_exit_maps_to_blocked(self) -> None:
        actions = FakeActions(safe_push_code=7)

        results = self.run_result(self.base_plan(self.worker_output_action(action_id="unpushed-worker-output:77:other")), actions=actions)

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "helper_exit:7")

    def test_publish_implementation_output_delegated_fallback_is_retryable(self) -> None:
        actions = FakeActions(publish_code=75)
        action = self.implementation_output_action(action_id="publish-implementation:delegated-fallback")

        first = self.run_result(self.base_plan(action), git_diff_code=1, actions=actions)
        second = self.run_result(self.base_plan(action), git_diff_code=1, actions=actions)

        self.assertEqual(first[0].status, "delegated")
        self.assertEqual(first[0].reason, "publish_implementation_fallback_delegated")
        self.assertEqual(second[0].status, "delegated")
        self.assertEqual([call[0] for call in actions.calls], ["publish_implementation_output", "publish_implementation_output"])
        ledger_rows = [
            json.loads(line)
            for line in (self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual([row["status"] for row in ledger_rows], ["delegated", "delegated"])
        pending_path = self.repo / ".refactor-loop/.controller-pending-events.log"
        pending = pending_path.read_text(encoding="utf-8") if pending_path.exists() else ""
        self.assertNotIn("WAKEUP_RUNNER_HELPER_EXIT", pending)

    def test_safe_push_stale_head_blocks_before_helper(self) -> None:
        actions = FakeActions()

        results = self.run_result(self.base_plan(self.worker_output_action(action_id="unpushed-worker-output:77:stale")), gh_head_ref="other/ref", actions=actions)

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "safe_push_stale_head:other/ref")
        self.assertEqual(actions.calls, [])

    def test_closed_or_unavailable_live_target_blocks_before_lifecycle_helpers(self) -> None:
        cases = [
            (
                "safe-push-closed-pr",
                lambda: self.worker_output_action(action_id="live-target:safe-push-closed"),
                "CLOSED",
                "target_not_open:CLOSED",
            ),
            (
                "merge-closed-pr",
                lambda: self.review_gate_action(action_id="live-target:merge-closed"),
                "CLOSED",
                "target_not_open:CLOSED",
            ),
            (
                "close-unavailable-issue",
                lambda: self.close_action(action_id="live-target:close-unavailable"),
                None,
                "target_not_open:unknown",
            ),
        ]

        for name, action_factory, gh_state, reason in cases:
            with self.subTest(name=name):
                action = action_factory()
                actions = FakeActions()
                results = self.run_result(self.base_plan(action), gh_state=gh_state, actions=actions)
                self.assert_blocked_before_dispatch(results, action["action_id"], reason, actions)

    def test_closed_pr_review_and_fix_dispatch_skip_terminal_before_spawn(self) -> None:
        cases = (
            ("review-dispatch", self.reviewer_dispatch_action(action_id="stale-review:423"), "CLOSED"),
            ("fix-dispatch", self.review_gate_action(action_id="stale-fix:423"), "MERGED"),
        )
        for name, action, gh_state in cases:
            with self.subTest(name=name):
                actions = FakeActions()
                results = self.run_result(self.base_plan(action), gh_state=gh_state, actions=actions)

                self.assert_blocked_before_dispatch(results, action["action_id"], f"target_not_open:{gh_state}", actions)

    def test_closed_pr_nested_target_mapping_blocks_before_dispatch(self) -> None:
        actions = FakeActions()
        action = self.reviewer_dispatch_action(
            action_id="nested-target:review-pr423",
            target_kind=None,
            target_number=None,
            target={"kind": "PR", "number": 423},
        )

        results = self.run_result(self.base_plan(action), gh_state="CLOSED", actions=actions)

        self.assert_blocked_before_dispatch(results, action["action_id"], "target_not_open:CLOSED", actions)

    def test_closed_pr_review_and_fix_text_targets_block_before_dispatch(self) -> None:
        cases = (
            (
                "review-pr-text",
                self.reviewer_dispatch_action(
                    action_id="completed-marker:review-pr423-architect-r1.log:REVIEW_DONE",
                    target_kind=None,
                    target_number=None,
                    target=None,
                ),
                "CLOSED",
                "target_not_open:CLOSED",
            ),
            (
                "fix-pr-text",
                self.review_gate_action(
                    action_id="completed-marker:fix-pr423-round-1.log:FIX_DONE",
                    target_kind=None,
                    target_number=None,
                    target=None,
                ),
                "MERGED",
                "target_not_open:MERGED",
            ),
        )
        for name, action, gh_state, reason in cases:
            with self.subTest(name=name):
                actions = FakeActions()
                results = self.run_result(self.base_plan(action), gh_state=gh_state, actions=actions)

                self.assert_blocked_before_dispatch(results, action["action_id"], reason, actions)

    def test_closed_issue_design_consensus_spawn_skips_terminal_before_supervisor(self) -> None:
        actions = FakeActions()
        action = self.design_consensus_spawn_action()

        results = self.run_result(self.base_plan(action), gh_state="CLOSED", actions=actions)

        self.assert_blocked_before_dispatch(results, action["action_id"], "target_not_open:CLOSED", actions)

    def test_open_issue_design_consensus_spawn_still_applies(self) -> None:
        action = self.design_consensus_spawn_action(action_id="harness-spawn-intent:phase9-router:104:2:judge-open")

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.base_plan(action), gh_state="OPEN", actions=FakeActions())

        self.assertEqual(results[0].status, "applied")
        launch.assert_called_once()

    def test_terminal_closed_target_block_suppresses_next_tick_retry(self) -> None:
        actions = FakeActions()
        action = self.reviewer_dispatch_action(action_id="stale-review:423:dedupe")
        plan = self.base_plan(action)

        first = self.run_result(plan, gh_state="CLOSED", actions=actions)
        second = self.run_result(plan, gh_state="CLOSED", actions=actions)

        self.assertEqual(first[0].status, "blocked")
        self.assertEqual(first[0].reason, "target_not_open:CLOSED")
        self.assertEqual(second[0].status, "skipped")
        self.assertEqual(second[0].reason, "duplicate")
        self.assertEqual(actions.calls, [])

    def test_safe_push_missing_or_invalid_preconditions_block_at_runner_gate_before_helper(self) -> None:
        outside = self.repo / "outside-worktree"
        outside.mkdir()
        cases = [
            (
                "missing-verified-head",
                self.worker_output_action(
                    action_id="safe-push:missing-verified-head",
                    preconditions=["active_controller_owner", "clean_scoped_diff"],
                ),
                "safe_push_missing_precondition:verified_pr_head",
                0,
            ),
            (
                "missing-clean-diff",
                self.worker_output_action(
                    action_id="safe-push:missing-clean-diff",
                    preconditions=["active_controller_owner", "verified_pr_head"],
                ),
                "safe_push_missing_precondition:clean_scoped_diff",
                0,
            ),
            (
                "invalid-head-ref",
                self.worker_output_action(action_id="safe-push:invalid-head-ref", head_ref="-bad"),
                "safe_push_invalid_head_ref",
                0,
            ),
            (
                "missing-worktree",
                self.worker_output_action(action_id="safe-push:missing-worktree", worktree=str(self.repo / ".worktrees" / "missing")),
                "safe_push_worktree_missing",
                0,
            ),
            (
                "outside-worktree",
                self.worker_output_action(action_id="safe-push:outside-worktree", worktree=str(outside)),
                "safe_push_worktree_outside_controller_owned_root",
                0,
            ),
            (
                "dirty-diff",
                self.worker_output_action(action_id="safe-push:dirty-diff"),
                "safe_push_dirty_scoped_diff",
                1,
            ),
        ]

        for name, action, reason, git_diff_code in cases:
            with self.subTest(name=name):
                actions = FakeActions()
                results = self.run_result(self.base_plan(action), git_diff_code=git_diff_code, actions=actions)
                self.assert_blocked_before_dispatch(results, action["action_id"], reason, actions)

    def test_publish_worker_output_action_routes_to_named_helper(self) -> None:
        actions = FakeActions()
        action = self.spawn_action(
            kind="completed-marker",
            action_id="completed-marker:implement.log:IMPLEMENT_DONE:real",
            controller_action="publish_worker_output_from_action",
        )

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "publish_worker_output_from_action")

    def test_publish_implementation_output_routes_to_named_helper(self) -> None:
        actions = FakeActions()

        results = self.run_result(
            self.base_plan(self.implementation_output_action()),
            git_diff_code=1,
            actions=actions,
        )

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "publish_implementation_output")

    def test_publish_implementation_output_blocks_before_helper_without_g3_preconditions(self) -> None:
        cases = (
            (
                "bad-marker",
                self.implementation_output_action(
                    action_id="publish-implementation:bad-marker",
                    source_marker="IMPLEMENT_DONE:issue-77:partial",
                ),
                "source_marker_missing",
                1,
                None,
            ),
            (
                "missing-host-checks",
                self.implementation_output_action(
                    action_id="publish-implementation:missing-host-checks",
                    preconditions=[
                        "active_controller_owner",
                        "clean_exit_source_marker",
                        "canonical_implementation_identity",
                        "fresh_integration_base",
                        "clean_scoped_diff",
                        "single_linked_managed_issue",
                        "no_duplicate_open_pr",
                    ],
                ),
                "publish_implementation_missing_precondition:host_checks_green",
                1,
                None,
            ),
            (
                "not-managed",
                self.implementation_output_action(action_id="publish-implementation:not-managed"),
                "publish_implementation_target_not_managed",
                1,
                None,
            ),
            (
                "empty-diff",
                self.implementation_output_action(action_id="publish-implementation:empty-diff"),
                "publish_implementation_empty_scoped_diff",
                0,
                None,
            ),
        )
        for name, action, reason, git_diff_code, duplicate_prs in cases:
            with self.subTest(name=name):
                actions = FakeActions()
                gh_labels = [] if name == "not-managed" else None
                results = self.run_result(
                    self.base_plan(action),
                    git_diff_code=git_diff_code,
                    duplicate_prs=duplicate_prs,
                    gh_labels=gh_labels,
                    actions=actions,
                )
                self.assert_blocked_before_dispatch(results, action["action_id"], reason, actions)

    def test_publish_implementation_output_allows_existing_open_pr_for_helper_reuse(self) -> None:
        actions = FakeActions()
        action = self.implementation_output_action(action_id="publish-implementation:existing-pr")

        results = self.run_result(
            self.base_plan(action),
            git_diff_code=1,
            duplicate_prs=[{"number": 99}],
            actions=actions,
        )

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "publish_implementation_output")

    def test_wakeup_runner_source_locks_publish_stale_base_recovery_delegation(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "wakeup_runner.py").read_text(encoding="utf-8")
        publish_validator = source[source.index("    def _validate_publish_implementation") : source.index("    def _validate_dispatch_reviewers")]
        worktree_validator = source[source.index("    def _validate_implementation_worktree") : source.index("    def _validate_canonical_implementation_identity")]
        duplicate_validator = source[source.index("    def _validate_no_duplicate_open_pr") : source.index("    def _validate_implementation_worktree")]
        self.assertNotIn("publish_implementation_stale_base", publish_validator + worktree_validator)
        self.assertNotIn("merge-base", publish_validator + worktree_validator)
        self.assertNotIn("publish_implementation_duplicate_open_pr", duplicate_validator)
        self.assertIn('["git", "-C", str(worktree), "diff", "HEAD", "--quiet"]', worktree_validator)

    def test_dispatch_consensus_implementation_revalidates_durable_artifact_before_helper(self) -> None:
        actions = FakeActions()
        action = self.consensus_action()

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "dispatch_consensus_implementation")

    def test_dispatch_consensus_implementation_blocks_precondition_string_only_projection(self) -> None:
        actions = FakeActions()
        action = self.consensus_action(
            action_id="consensus:string-only",
            preconditions=["active_controller_owner", "live_open_target", "consensus_artifact_present"],
            consensus_artifact="",
            design_decision_path="",
            scope_paths="",
            old_pattern="",
            new_principle="",
        )

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assert_blocked_before_dispatch(
            results,
            "consensus:string-only",
            "consensus_implementation_missing_precondition:durable_consensus_artifact",
            actions,
        )

    def test_dispatch_consensus_implementation_blocks_missing_required_projection_field(self) -> None:
        actions = FakeActions()
        action = self.consensus_action(action_id="consensus:missing-scope", scope_paths="")

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assert_blocked_before_dispatch(
            results,
            "consensus:missing-scope",
            "consensus_implementation_missing_field:scope_paths",
            actions,
        )

    def test_dispatch_consensus_implementation_blocks_design_path_mismatch(self) -> None:
        actions = FakeActions()
        action = self.consensus_action(
            action_id="consensus:design-path-mismatch",
            design_decision_path=".refactor-loop/runs/other.md",
        )

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assert_blocked_before_dispatch(
            results,
            "consensus:design-path-mismatch",
            "consensus_implementation_design_path_mismatch",
            actions,
        )

    def test_dispatch_consensus_implementation_blocks_invalid_durable_artifact(self) -> None:
        outside = self.repo / "outside-consensus.md"
        outside.write_text("META_JUDGE_DONE:consensus:structural\n", encoding="utf-8")
        bad_basename = self.repo / ".refactor-loop/runs/xphase9-issue20-r5-judge.md"
        bad_basename.write_text("META_JUDGE_DONE:consensus:structural\n", encoding="utf-8")
        cases = [
            (
                "outside-runs",
                lambda: self.consensus_action(
                    action_id="consensus:outside-runs",
                    consensus_artifact="outside-consensus.md",
                    design_decision_path="outside-consensus.md",
                ),
                "consensus_artifact_outside_runs",
            ),
            (
                "target-mismatch",
                lambda: self.consensus_action(action_id="consensus:target-mismatch", target_number=21),
                "consensus_artifact_target_mismatch",
            ),
            (
                "identity-mismatch",
                lambda: self.consensus_action(action_id="consensus:identity-mismatch", consensus_round=6),
                "consensus_artifact_identity_mismatch",
            ),
            (
                "bad-basename",
                lambda: self.consensus_action(
                    action_id="consensus:bad-basename",
                    consensus_artifact=".refactor-loop/runs/xphase9-issue20-r5-judge.md",
                    design_decision_path=".refactor-loop/runs/xphase9-issue20-r5-judge.md",
                ),
                "consensus_artifact_identity_mismatch",
            ),
            (
                "missing-marker",
                lambda: self.consensus_action(action_id="consensus:missing-marker"),
                "consensus_artifact_marker_missing",
            ),
        ]
        for name, action_factory, reason in cases:
            with self.subTest(name=name):
                action = action_factory()
                if name == "missing-marker":
                    (self.repo / ".refactor-loop/runs/phase9-issue20-r5-judge.md").write_text(
                        "no consensus marker\n",
                        encoding="utf-8",
                    )
                actions = FakeActions()
                results = self.run_result(self.base_plan(action), actions=actions)
                self.assert_blocked_before_dispatch(results, action["action_id"], reason, actions)

    def test_dispatch_consensus_implementation_redispatches_markerless_implement_log(self) -> None:
        actions = FakeActions()
        action = self.consensus_action(action_id="consensus:existing-log")
        (self.repo / ".refactor-loop/logs/implement-issue-20.log").write_text("worker finished without marker\nEXIT=0\n", encoding="utf-8")

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "dispatch_consensus_implementation")

    def test_dispatch_consensus_implementation_blocks_inflight_implement_log(self) -> None:
        actions = FakeActions()
        action = self.consensus_action(action_id="consensus:inflight-log")
        log = self.repo / ".refactor-loop/logs/implement-issue-20.log"
        log.write_text("worker still running\n", encoding="utf-8")

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assert_blocked_before_dispatch(results, action["action_id"], "consensus_implementation_not_ready:in_flight_implement", actions)
        self.assertTrue(log.exists())

    def test_failed_consensus_implementation_redispatch_clears_log_and_spawn_launches(self) -> None:
        failed_log = self.repo / ".refactor-loop/logs/implement-issue-20.log"
        failed_log.write_text("old failed run\nEXIT=1\n", encoding="utf-8")
        worktree = self.repo / ".worktrees" / "iter20-issue-20"

        class ClearingActions(FakeActions):
            def __init__(inner_self, ctx: LoopContext) -> None:
                super().__init__()
                inner_self.ctx = ctx

            def dispatch_consensus_implementation(inner_self, action: dict) -> int:
                inner_self.calls.append(("dispatch_consensus_implementation", dict(action)))
                failed_log.unlink(missing_ok=True)
                prompt = inner_self.ctx.paths.prompts / "implement-issue-20.md"
                prompt.write_text("fresh prompt\n", encoding="utf-8")
                inner_self.ctx.paths.pending_events.write_text(
                    "2026-06-01T00:00:00Z HARNESS_SPAWN_INTENT "
                    + json.dumps(
                        {
                            "intent_id": "dispatch-consensus-implementation:20",
                            "source": "controller-actions",
                            "route": "dispatch-consensus-implementation",
                            "task_id": "implement-issue-20",
                            "priority": "p1",
                            "command": "spawn-codex",
                            "controller_action": "spawn_codex_harness_background",
                            "cd": str(worktree.resolve()),
                            "prompt": ".refactor-loop/prompts/implement-issue-20.md",
                            "log": ".refactor-loop/logs/implement-issue-20.log",
                            "stall": 5400,
                            "reason": "issue #20 consensus implementation",
                            "run_in_background_required": True,
                            "no_lifecycle_authority": True,
                        },
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return 0

        actions = ClearingActions(self.ctx)
        dispatch_action = self.consensus_action(action_id="consensus:failed-log")
        dispatch_results = self.run_result(self.base_plan(dispatch_action), actions=actions)

        self.assertEqual(dispatch_results[0].status, "applied")
        self.assertFalse(failed_log.exists())

        spawn_plan = self.base_plan(
            {
                "kind": "harness-spawn-intent",
                "action_id": "harness-spawn-intent:dispatch-consensus-implementation:20",
                "runner_authority": "wakeup-runner-396",
                "preconditions": ["active_controller_owner", "source_artifact_contains_evidence", "target_log_absent"],
                "source_artifact": ".refactor-loop/.controller-pending-events.log",
                "source_marker": "HARNESS_SPAWN_INTENT",
                "target_kind": "codex",
                "target_number": None,
                "target": {"kind": "codex", "task_id": "implement-issue-20"},
                "controller_action": "spawn_codex_harness_background",
                "no_generic_command": True,
                "cd": str(worktree),
                "prompt": str(self.repo / ".refactor-loop/prompts/implement-issue-20.md"),
                "log": str(failed_log),
                "stall": 5400,
            }
        )
        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            spawn_results = self.run_result(spawn_plan, actions=FakeActions())

        self.assertEqual(spawn_results[0].status, "applied")
        launch.assert_called_once()
        self.assertEqual(Path(launch.call_args.kwargs["log"]).resolve(), failed_log.resolve())

    def test_spawn_apply_clears_terminal_markerless_implement_log_before_launch(self) -> None:
        log = self.repo / ".refactor-loop/logs/implement-issue-20.log"
        log.write_text("old terminal markerless run\nEXIT=0\n", encoding="utf-8")
        action = self.spawn_action(
            action_id="spawn:implement-issue-20",
            target={"kind": "codex", "task_id": "implement-issue-20"},
            log=str(log),
        )

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.base_plan(action), actions=FakeActions())

        self.assertEqual(results[0].status, "applied")
        self.assertFalse(log.exists())
        launch.assert_called_once()

    def test_spawn_apply_preserves_inflight_implement_log(self) -> None:
        log = self.repo / ".refactor-loop/logs/implement-issue-20.log"
        log.write_text("worker still running\n", encoding="utf-8")
        actions = FakeActions()
        action = self.spawn_action(
            action_id="spawn:implement-issue-20",
            target={"kind": "codex", "task_id": "implement-issue-20"},
            log=str(log),
        )

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.base_plan(action), actions=actions)

        self.assert_blocked_before_dispatch(results, action["action_id"], "target_log_exists", actions)
        self.assertTrue(log.exists())
        launch.assert_not_called()

    def test_publish_ready_implementation_routes_to_publish_helper(self) -> None:
        actions = FakeActions()

        results = self.run_result(self.base_plan(self.implementation_output_action()), actions=actions, git_diff_code=1)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "publish_implementation_output")

    def test_clean_implementation_on_stale_base_routes_to_publish_helper_for_recovery(self) -> None:
        actions = FakeActions()

        results = self.run_result(
            self.base_plan(self.implementation_output_action()),
            actions=actions,
            git_diff_code=1,
            implementation_base=("old-base", "new-base"),
        )

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "publish_implementation_output")

    def test_publish_implementation_output_does_not_block_stale_base_before_helper(self) -> None:
        actions = FakeActions()

        def command_runner(command):
            if command[:2] == ["gh", "api"]:
                endpoint = str(command[2]) if len(command) > 2 else ""
                if "/issues/" in endpoint:
                    return subprocess.CompletedProcess(command, 0, json.dumps({"state": "open"}), "")
                return subprocess.CompletedProcess(command, 0, "{}", "")
            if command[:3] == ["gh", "issue", "view"]:
                return subprocess.CompletedProcess(command, 0, json.dumps({"labels": [{"name": labels.MANAGED}], "body": ""}), "")
            if command[:4] == ["gh", "pr", "list", "--state"]:
                return subprocess.CompletedProcess(command, 0, "[]", "")
            if command[:3] == ["git", "-C", str(self.repo / ".worktrees" / "iter77-issue-77")]:
                if command[3:] == ["rev-parse", "--abbrev-ref", "HEAD"]:
                    return subprocess.CompletedProcess(command, 0, "refactor/iter77-issue-77\n", "")
                if command[3:] == ["merge-base", "HEAD", "origin/auto-refact-dev"]:
                    return subprocess.CompletedProcess(command, 0, "old-base\n", "")
                if command[3:] == ["rev-parse", "--verify", "origin/auto-refact-dev"]:
                    return subprocess.CompletedProcess(command, 0, "new-base\n", "")
                if command[3:] == ["diff", "HEAD", "--quiet"]:
                    return subprocess.CompletedProcess(command, 1, "", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        runner = WakeupRunner(
            self.ctx,
            plan_loader=lambda _repo: self.base_plan(self.implementation_output_action()),
            actions=actions,
            supervisor=self.supervisor,
            command_runner=command_runner,
        )

        results = runner.run_once()

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "publish_implementation_output")

    def test_dispatch_reviewers_routes_to_named_helper_after_pr_target_validation(self) -> None:
        actions = FakeActions()

        results = self.run_result(self.base_plan(self.reviewer_dispatch_action()), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "dispatch_reviewers")

    def test_review_evidence_redispatch_routes_to_named_helper_after_pr_target_validation(self) -> None:
        actions = FakeActions()
        action = self.reviewer_dispatch_action(
            kind="review-evidence-redispatch",
            action_id="review-evidence-redispatch:77:" + "a" * 40,
            source_artifact="wakeup-plan",
            source_marker="review-evidence-redispatch",
            head_sha="a" * 40,
            stale_review_roles=["architect", "tests"],
            preconditions=[
                "active_controller_owner",
                "live_open_target_if_present",
                "missing_or_stale_reviewer_head_evidence",
            ],
        )

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "dispatch_reviewers")
        self.assertEqual(actions.calls[0][1]["stale_review_roles"], ["architect", "tests"])

    def test_dispatch_reviewers_blocks_missing_or_non_pr_target_before_helper(self) -> None:
        cases = (
            (
                "missing-target",
                self.reviewer_dispatch_action(
                    action_id="dispatch-reviewers:missing-target",
                    target_kind=None,
                    target_number=None,
                    target=None,
                ),
                "dispatch_reviewers_target_missing",
            ),
            (
                "issue-target",
                self.reviewer_dispatch_action(
                    action_id="dispatch-reviewers:issue-target",
                    target_kind="issue",
                    target_number=77,
                    target={"kind": "issue", "number": 77},
                ),
                "dispatch_reviewers_target_missing",
            ),
        )
        for name, action, reason in cases:
            with self.subTest(name=name):
                actions = FakeActions()
                results = self.run_result(self.base_plan(action), actions=actions)
                self.assert_blocked_before_dispatch(results, action["action_id"], reason, actions)

    def test_fix_done_dispatch_commits_and_pushes_fix_output_before_review(self) -> None:
        # Headless gap fix: a FIX_DONE re-review must first commit+push the fix
        # codex's uncommitted worktree output, else reviewers re-review the stale
        # head forever and the reject never converges.
        worktree = self.repo / ".worktrees" / "iter77-issue-77"
        worktree.mkdir(parents=True, exist_ok=True)
        seen: list[list[str]] = []

        def command_runner(command):
            cmd = [str(part) for part in command]
            seen.append(cmd)
            if cmd[:3] == ["gh", "pr", "view"]:
                return subprocess.CompletedProcess(cmd, 0, '{"headRefName": "refactor/iter77-issue-77"}', "")
            if "status" in cmd and "--porcelain" in cmd:
                return subprocess.CompletedProcess(cmd, 0, " M skills/x.py\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        actions = FakeActions()
        runner = WakeupRunner(self.ctx, actions=actions, command_runner=command_runner)
        with mock.patch.object(runner, "_review_fix_worktree", return_value=worktree):
            rc = runner._dispatch("dispatch_reviewers", self.reviewer_dispatch_action(target_number=77))

        self.assertEqual(rc, 0)
        git_subcmds = [cmd[3] for cmd in seen if cmd[0] == "git" and len(cmd) > 3]
        self.assertIn("add", git_subcmds)
        self.assertIn("commit", git_subcmds)
        self.assertEqual(len([call for call in actions.calls if call[0] == "safe_push"]), 1)
        self.assertEqual(actions.calls[-1][0], "dispatch_reviewers")

    def test_fix_done_dispatch_clean_worktree_skips_commit_and_reviews(self) -> None:
        # A clean fix worktree (no uncommitted changes) is a no-op: re-review
        # directly without an empty commit or push.
        worktree = self.repo / ".worktrees" / "iter77-issue-77"
        worktree.mkdir(parents=True, exist_ok=True)
        seen: list[list[str]] = []

        def command_runner(command):
            cmd = [str(part) for part in command]
            seen.append(cmd)
            if cmd[:3] == ["gh", "pr", "view"]:
                return subprocess.CompletedProcess(cmd, 0, '{"headRefName": "refactor/iter77-issue-77"}', "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        actions = FakeActions()
        runner = WakeupRunner(self.ctx, actions=actions, command_runner=command_runner)
        with mock.patch.object(runner, "_review_fix_worktree", return_value=worktree):
            rc = runner._dispatch("dispatch_reviewers", self.reviewer_dispatch_action(target_number=77))

        self.assertEqual(rc, 0)
        git_subcmds = [cmd[3] for cmd in seen if cmd[0] == "git" and len(cmd) > 3]
        self.assertNotIn("commit", git_subcmds)
        self.assertEqual([call for call in actions.calls if call[0] == "safe_push"], [])
        self.assertEqual(actions.calls[-1][0], "dispatch_reviewers")

    def test_release_rollup_routes_to_named_helper_after_event_body_validation(self) -> None:
        actions = FakeActions()

        results = self.run_result(self.base_plan(self.release_rollup_action()), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "open_release_rollup_pr_from_action")

    def test_release_rollup_blocks_missing_event_fields_before_helper(self) -> None:
        body = self.repo / ".refactor-loop/runs/release-rollup-pr-body.md"
        cases = (
            (
                "missing-precondition",
                self.release_rollup_action(
                    action_id="rollup:missing-precondition",
                    preconditions=["active_controller_owner", "source_artifact_contains_evidence"],
                ),
                "release_rollup_missing_precondition:release_rollup_event",
            ),
            (
                "missing-event",
                self.release_rollup_action(action_id="rollup:missing-event", event=None),
                "release_rollup_event_missing",
            ),
            (
                "missing-sha",
                self.release_rollup_action(action_id="rollup:missing-sha", event={"integration_sha": ""}),
                "release_rollup_integration_sha_missing",
            ),
            (
                "missing-body",
                self.release_rollup_action(action_id="rollup:missing-body", body_file=".refactor-loop/runs/missing-rollup.md"),
                "release_rollup_body_missing",
            ),
        )
        self.assertTrue(body.is_file())
        for name, action, reason in cases:
            with self.subTest(name=name):
                actions = FakeActions()
                results = self.run_result(self.base_plan(action), actions=actions)
                self.assert_blocked_before_dispatch(results, action["action_id"], reason, actions)

    def test_daemon_run_once_periodically_renews_heartbeat_during_long_tick(self) -> None:
        ScriptedEvent.instances = []
        ScriptedEvent.coordinator = HeartbeatCoordinator()
        InlineThread.instances = []
        lease = FakeHeartbeatLease()
        lease.coordinator = ScriptedEvent.coordinator
        expected = [object()]

        with mock.patch("codex_refactor_loop.wakeup_runner.threading.Event", ScriptedEvent), mock.patch(
            "codex_refactor_loop.wakeup_runner.threading.Thread",
            InlineThread,
        ):

            def run_once():
                ScriptedEvent.coordinator.enter_run_once()
                self.assertTrue(ScriptedEvent.coordinator.wait_for_heartbeat_while_run_once_is_pending())
                return expected

            result = _run_once_with_periodic_heartbeat(run_once, lease)

        self.assertIs(result, expected)
        self.assertGreaterEqual(lease.beats, 1)
        self.assertTrue(ScriptedEvent.coordinator.beat_during_run_once)
        self.assertGreaterEqual(ScriptedEvent.instances[0].wait_timeouts.count(7.0), 1)
        self.assertTrue(ScriptedEvent.instances[0].set_called)
        self.assertTrue(InlineThread.instances[0].started)
        self.assertTrue(InlineThread.instances[0].daemon)
        self.assertEqual(InlineThread.instances[0].name, "wakeup-runner-heartbeat-renewer")
        self.assertEqual(InlineThread.instances[0].join_timeouts, [1.0])
        ScriptedEvent.coordinator = None

    def test_daemon_run_once_periodic_heartbeat_propagates_exception_and_stops_thread(self) -> None:
        ScriptedEvent.instances = []
        ScriptedEvent.coordinator = HeartbeatCoordinator()
        InlineThread.instances = []
        lease = FakeHeartbeatLease()
        lease.coordinator = ScriptedEvent.coordinator

        with mock.patch("codex_refactor_loop.wakeup_runner.threading.Event", ScriptedEvent), mock.patch(
            "codex_refactor_loop.wakeup_runner.threading.Thread",
            InlineThread,
        ):

            def run_once():
                ScriptedEvent.coordinator.enter_run_once()
                self.assertTrue(ScriptedEvent.coordinator.wait_for_heartbeat_while_run_once_is_pending())
                raise RuntimeError("boom")

            with self.assertRaisesRegex(RuntimeError, "boom"):
                _run_once_with_periodic_heartbeat(run_once, lease)

        self.assertGreaterEqual(lease.beats, 1)
        self.assertTrue(ScriptedEvent.coordinator.beat_during_run_once)
        self.assertTrue(ScriptedEvent.instances[0].set_called)
        self.assertTrue(InlineThread.instances[0].started)
        self.assertEqual(InlineThread.instances[0].join_timeouts, [1.0])
        ScriptedEvent.coordinator = None

    def test_wakeup_runner_source_locks_named_g1_g3_helper_allowlist(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "wakeup_runner.py").read_text(encoding="utf-8")
        for helper in (
            "dispatch_consensus_implementation",
            "publish_implementation_output",
            "dispatch_reviewers",
            "open_release_rollup_pr_from_action",
        ):
            with self.subTest(helper=helper):
                self.assertIn(helper, source)
        for forbidden in ("command_line", "lifecycle_authority", "lifecycle_owner"):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, source)
        self.assertNotIn("dispatch_next_step_worker", source)
        self.assertNotIn("HeadlessLifecycleAction", source)
        self.assertNotIn("headless_actions", source)

    def test_wakeup_runner_source_locks_blocked_non_spawn_scan_invariant(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "wakeup_runner.py").read_text(encoding="utf-8")
        run_once = source[source.index("    def run_once(self) -> list[RunnerResult]:") : source.index("    def apply_action", source.index("    def run_once(self) -> list[RunnerResult]:"))]

        self.assertIn('if result.status in {"blocked", "skipped"} and not consumes_spawn_budget:', run_once)
        self.assertIn("continue", run_once)
        self.assertIn("consumes_spawn_budget = is_spawn_action or self._uses_spawn_budget(action)", run_once)
        self.assertIn("if consumes_spawn_budget and applied_spawns >= budget.spawn_budget:", run_once)
        self.assertNotIn("if applied_spawns > 0 and not is_spawn_action:", run_once)
        self.assertIn('controller_action == "dispatch_reviewers"', run_once)
        self.assertIn('controller_action == "review_gate"', run_once)
        self.assertIn('.get("decision") == "FIX"', run_once)
        self.assertNotIn("blocked_non_spawn_before_spawn", run_once)

    def test_wakeup_runner_daemon_long_tick_heartbeat_source_contract(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "wakeup_runner.py").read_text(encoding="utf-8")
        helper_start = source.index("def _run_once_with_periodic_heartbeat(")
        helper_end = source.index("\ndef load_plan_file", helper_start)
        helper = source[helper_start:helper_end]
        daemon_branch = source[source.index("    if args.daemon:") : source.index("    results = runner.run_once()")]

        for needle in (
            "def _run_once_with_periodic_heartbeat(",
            "threading.Event()",
            "threading.Thread(",
            "daemon=True",
            "lease.heartbeat_interval",
            "lease.beat()",
            "return run_once()",
            "finally:",
            "stop.set()",
            "renewer.join(timeout=1.0)",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, helper)
        self.assertIn("results = _run_once_with_periodic_heartbeat(runner.run_once, lease)", daemon_branch)
        self.assertIn("_log_tick_status(\"wakeup-runner\", _wakeup_tick_action(results))", daemon_branch)
        self.assertIn("lease.sleep_with_lease(interval)", daemon_branch)
        self.assertNotIn("_run_once_with_periodic_heartbeat(runner.run_once, lease)", source[source.index("    results = runner.run_once()") :])

    def test_close_managed_item_from_drop_marker_routes_to_named_helper(self) -> None:
        actions = FakeActions()
        action = self.spawn_action(
            kind="completed-marker",
            action_id="completed-marker:judge.log:META_RESOLVED:drop:no-action",
            controller_action="close_managed_item_from_drop_marker",
            source_artifact="drop-marker-artifact",
            source_marker="META_RESOLVED:drop:no-action",
            target_kind="issue",
            target_number=53,
            target={"kind": "issue", "number": 53},
            preconditions=["active_controller_owner", "live_open_target", "live_managed_target"],
        )

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "close_managed_item_from_drop_marker")

    def test_close_managed_item_from_drop_marker_blocks_non_managed_open_target_before_helper(self) -> None:
        actions = FakeActions()
        action = self.close_action(action_id="close-managed-item:non-managed")

        results = self.run_result(self.base_plan(action), gh_labels=[], actions=actions)

        self.assert_blocked_before_dispatch(
            results,
            "close-managed-item:non-managed",
            "close_managed_drop_target_not_managed",
            actions,
        )

    def test_idempotency_ledger_suppresses_duplicate_apply(self) -> None:
        plan = self.base_plan(self.spawn_action())
        with mock.patch("codex_refactor_loop.processes.subprocess.Popen") as popen:
            first = self.run_result(plan)
        (self.repo / ".refactor-loop/logs/task.log").write_text("SPAWN\n", encoding="utf-8")
        second = self.run_result(plan)

        self.assertEqual(first[0].status, "applied")
        self.assertEqual(second[0].status, "skipped")
        popen.assert_called_once()

    def test_wakeup_runner_tick_status_line_format(self) -> None:
        out = StringIO()
        with redirect_stdout(out):
            _log_tick_status("wakeup-runner", "dispatched spawn:1")

        self.assertRegex(
            out.getvalue().strip(),
            r"^\[[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\] wakeup-runner: tick dispatched spawn:1$",
        )

    def test_refactor_loop_host_env_is_not_production_topology_ssot(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "wakeup_runner.py").read_text(encoding="utf-8")

        self.assertNotIn('".refactor-loop/host.env"', source)
        self.assertIn("LoopContext.load", source)

    def test_close_managed_item_source_regression_revalidates_managed_label_before_dispatch(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "wakeup_runner.py").read_text(encoding="utf-8")
        method = source[source.index("    def _validate_close_managed_drop") : source.index("    def _validate_review_gate")]
        self.assertIn('"live_managed_target"', method)
        self.assertIn('"labels,body"', source)
        self.assertIn("labels.normalize_label_set", source)
        self.assertIn("labels.MANAGED", source)
        self.assertNotIn('"crnd:lifecycle:managed"', method)
        self.assertLess(source.index("return self._validate_close_managed_drop(action)"), source.index("return self.actions.close_managed_item_from_drop_marker(dict(action))"))

    def test_plan_file_is_dry_run_only(self) -> None:
        plan_path = self.repo / "plan.json"
        plan_path.write_text(json.dumps(self.base_plan(self.spawn_action())), encoding="utf-8")

        exit_code = wakeup_runner_main(["--once", "--repo-root", str(self.repo), "--plan-file", str(plan_path)])

        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
