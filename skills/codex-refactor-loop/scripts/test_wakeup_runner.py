#!/usr/bin/env python3
"""Behavior tests for the #396 wakeup runner."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop import labels
from codex_refactor_loop.wakeup_runner import WakeupRunner, main as wakeup_runner_main


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


class WakeupRunnerBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        for rel in (".refactor-loop/state", ".refactor-loop/logs", ".refactor-loop/prompts", ".refactor-loop/runs"):
            (self.repo / rel).mkdir(parents=True, exist_ok=True)
        (self.repo / ".refactor-loop/host.env").write_text(
            f'export REPO_ROOT="{self.repo}"\n'
            'export GH_REPO_SLUG="owner/repo"\n'
            'export INTEGRATION_BRANCH="auto-refact-dev"\n'
            'export REVIEW_BASE_BRANCH="dev"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.repo)
        self.supervisor = FakeSupervisor()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_result(
        self,
        plan: dict,
        *,
        gh_state: str | None = "OPEN",
        gh_labels: list[str] | None = None,
        gh_head_ref: str = "refactor/iter77-worker",
        git_diff_code: int = 0,
        duplicate_prs: list[dict] | None = None,
        actions=None,
    ) -> list:
        def command_runner(command):
            if command[:4] == ["gh", "pr", "list", "--state"]:
                return subprocess.CompletedProcess(command, 0, json.dumps(duplicate_prs or []), "")
            if command[:3] == ["gh", "issue", "view"] or command[:3] == ["gh", "pr", "view"]:
                if "labels,body" in command:
                    live_labels = gh_labels if gh_labels is not None else [labels.MANAGED]
                    payload = {"labels": [{"name": name} for name in live_labels], "body": ""}
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
                if "headRefName" in command:
                    return subprocess.CompletedProcess(command, 0, gh_head_ref + "\n", "")
                if gh_state is None:
                    return subprocess.CompletedProcess(command, 1, "", "not found")
                return subprocess.CompletedProcess(command, 0, gh_state + "\n", "")
            if command[:3] == ["git", "-C", str(self.repo / ".worktrees" / "pr77")]:
                return subprocess.CompletedProcess(command, git_diff_code, "", "")
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

    def spawn_action(self, **overrides) -> dict:
        prompt = self.repo / ".refactor-loop/prompts/task.md"
        prompt.write_text("hello\n", encoding="utf-8")
        pending = self.repo / ".refactor-loop/.controller-pending-events.log"
        marker = "intent-marker"
        pending.write_text(marker + "\n", encoding="utf-8")
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
        return action

    def review_gate_action(self, **overrides) -> dict:
        marker = "REVIEW_GATE_READY:77"
        pending = self.repo / ".refactor-loop/.controller-pending-events.log"
        pending.write_text(marker + "\n", encoding="utf-8")
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
        worktree = self.repo / ".worktrees" / "pr77"
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
                "verified_pr_head",
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
            "head_ref": "refactor/iter77-worker",
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
            "preconditions": ["active_controller_owner", "clean_exit_source_marker", "live_open_target_if_present", "durable_consensus_artifact"],
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

    def test_valid_harness_spawn_executes_through_checked_supervisor(self) -> None:
        results = self.run_result(self.base_plan(self.spawn_action()))

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(len(self.supervisor.calls), 1)
        self.assertEqual(self.supervisor.calls[0]["stdin"], self.repo / ".refactor-loop/prompts/task.md")

    def test_harness_spawn_existing_target_log_blocks_before_supervisor(self) -> None:
        actions = FakeActions()
        action = self.spawn_action(action_id="spawn:target-log-exists")
        Path(action["log"]).write_text("existing worker\n", encoding="utf-8")

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assert_blocked_before_dispatch(results, "spawn:target-log-exists", "target_log_exists", actions)

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
                        "verified_pr_head",
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
                "duplicate-pr",
                self.implementation_output_action(action_id="publish-implementation:duplicate-pr"),
                "publish_implementation_duplicate_open_pr",
                1,
                [{"number": 99}],
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

    def test_dispatch_consensus_implementation_blocks_invalid_durable_artifact(self) -> None:
        outside = self.repo / "outside-consensus.md"
        outside.write_text("META_JUDGE_DONE:consensus:structural\n", encoding="utf-8")
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

    def test_dispatch_reviewers_routes_to_named_helper_after_pr_target_validation(self) -> None:
        actions = FakeActions()

        results = self.run_result(self.base_plan(self.reviewer_dispatch_action()), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "dispatch_reviewers")

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
        self.assertNotIn("HeadlessLifecycleAction", source)
        self.assertNotIn("headless_actions", source)

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
        first = self.run_result(plan)
        second = self.run_result(plan)

        self.assertEqual(first[0].status, "applied")
        self.assertEqual(second[0].status, "skipped")
        self.assertEqual(len(self.supervisor.calls), 1)

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
