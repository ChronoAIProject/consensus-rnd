#!/usr/bin/env python3
"""Review truth-table tests for wakeup-runner."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.wakeup_runner import WakeupRunner


class FakeActions:
    def __init__(self) -> None:
        self.merged: list[str] = []
        self.rendered: list[tuple[int, int]] = []

    def merge_pr(self, pr: str, linked_issue: str = "") -> int:
        self.merged.append(pr)
        return 0

    def render_review_fix_prompt(self, pr_number: int, round_number: int):
        self.rendered.append((pr_number, round_number))
        return type("Spec", (), {"prompt_path": ".refactor-loop/prompts/fix.md", "log_path": ".refactor-loop/logs/fix.log"})()


class FakeSupervisor:
    def __init__(self) -> None:
        self.calls = 0

    def supervise(self, *args, **kwargs) -> int:
        self.calls += 1
        return 0


class WakeupRunnerReviewGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        for rel in (".refactor-loop/state", ".refactor-loop/logs", ".refactor-loop/prompts", ".refactor-loop/runs"):
            (self.repo / rel).mkdir(parents=True, exist_ok=True)
        (self.repo / ".refactor-loop/host.env").write_text(f'export REPO_ROOT="{self.repo}"\nexport GH_REPO_SLUG="owner/repo"\n', encoding="utf-8")
        self.ctx = LoopContext.load(repo_root=self.repo)
        (self.repo / ".refactor-loop/prompts/fix.md").write_text("fix\n", encoding="utf-8")
        self.actions = FakeActions()
        self.supervisor = FakeSupervisor()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_review(self, role: str, verdict: str) -> None:
        (self.repo / ".refactor-loop/runs" / f"review-pr12-{role}-r1.md").write_text(
            f"---\nverdict: {verdict}\n---\n",
            encoding="utf-8",
        )

    def action(self) -> dict:
        log = self.repo / ".refactor-loop/logs/review-pr12-architect-r1.log"
        log.write_text("REVIEW_DONE:12:architect:approve\nEXIT=0\n", encoding="utf-8")
        return {
            "kind": "completed-marker",
            "action_id": "review:12",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "clean_exit_source_marker", "live_open_target"],
            "source_artifact": ".refactor-loop/logs/review-pr12-architect-r1.log",
            "source_marker": "REVIEW_DONE:12:architect:approve",
            "target_kind": "PR",
            "target_number": 12,
            "target": {"kind": "PR", "number": 12},
            "controller_action": "review_gate",
            "no_generic_command": True,
        }

    def run_action(self) -> object:
        def command_runner(command):
            if command[:3] == ["gh", "pr", "view"] and ".state" in command:
                return subprocess.CompletedProcess(command, 0, "OPEN\n", "")
            if command[:3] == ["gh", "pr", "view"] and ".headRefOid" in command:
                return subprocess.CompletedProcess(command, 0, "head-sha\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        runner = WakeupRunner(
            self.ctx,
            plan_loader=lambda _repo: {
                "schema": "wakeup-plan",
                "mode": "closed-action-projection",
                "apply_authority": "wakeup-runner-396-only",
                "no_lifecycle_authority": True,
                "actions": [self.action()],
            },
            actions=self.actions,
            supervisor=self.supervisor,
            command_runner=command_runner,
        )
        return runner.run_once()[0]

    def test_review_gate_merge_only_when_reject_zero_approve_one_and_all_present(self) -> None:
        self.write_review("architect", "approve")
        self.write_review("tests", "comment")
        self.write_review("quality", "comment")

        result = self.run_action()

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.actions.merged, ["12"])
        self.assertEqual(self.supervisor.calls, 0)

    def test_reject_dispatches_fix_not_merge(self) -> None:
        self.write_review("architect", "approve")
        self.write_review("tests", "reject")
        self.write_review("quality", "comment")

        result = self.run_action()

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.actions.merged, [])
        self.assertEqual(self.actions.rendered, [(12, 1)])
        self.assertEqual(self.supervisor.calls, 1)

    def test_missing_reviewer_fails_closed(self) -> None:
        self.write_review("architect", "approve")
        self.write_review("tests", "comment")

        result = self.run_action()

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "review_gate_missing_reviewers")
        self.assertEqual(self.actions.merged, [])


if __name__ == "__main__":
    unittest.main()
