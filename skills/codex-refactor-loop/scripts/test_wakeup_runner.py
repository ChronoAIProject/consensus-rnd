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
from codex_refactor_loop.wakeup_runner import WakeupRunner


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

    def safe_push(self, remote: str = "origin", branch: str = "") -> int:
        self.calls.append(("safe_push", {"remote": remote, "branch": branch}))
        return self.safe_push_code

    def publish_worker_output_from_action(self, action: dict) -> int:
        self.calls.append(("publish_worker_output_from_action", dict(action)))
        return self.publish_code

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

    def run_result(self, plan: dict, *, gh_state: str = "OPEN", gh_head_ref: str = "refactor/iter77-worker", actions=None) -> list:
        def command_runner(command):
            if command[:3] == ["gh", "issue", "view"] or command[:3] == ["gh", "pr", "view"]:
                if "headRefName" in command:
                    return subprocess.CompletedProcess(command, 0, gh_head_ref + "\n", "")
                return subprocess.CompletedProcess(command, 0, gh_state + "\n", "")
            if command[:3] == ["git", "-C", str(self.repo / ".worktrees" / "pr77")]:
                return subprocess.CompletedProcess(command, 0, "", "")
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

    def test_valid_harness_spawn_executes_through_checked_supervisor(self) -> None:
        results = self.run_result(self.base_plan(self.spawn_action()))

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(len(self.supervisor.calls), 1)
        self.assertEqual(self.supervisor.calls[0]["stdin"], self.repo / ".refactor-loop/prompts/task.md")

    def test_forbidden_fields_fail_closed(self) -> None:
        results = self.run_result(self.base_plan(self.spawn_action(argv=["gh", "pr", "merge"])))

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "forbidden_fields:argv")
        self.assertEqual(self.supervisor.calls, [])

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
        self.assertEqual(actions.calls, [("safe_push", {"remote": "origin", "branch": "refactor/iter77-worker"})])

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
        )

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "close_managed_item_from_drop_marker")

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


if __name__ == "__main__":
    unittest.main()
