#!/usr/bin/env python3
"""End-to-end review gate tests for wakeup-plan projection plus wakeup-runner apply."""

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
from codex_refactor_loop.controller_actions import ControllerActions
from codex_refactor_loop.wakeup_plan import GhItem, completed_marker_actions
from codex_refactor_loop.wakeup_runner import WakeupRunner


HEAD_SHA = "a" * 40
REVIEW_ROLES = ("architect", "tests", "quality")


class FakeActions:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.merged: list[str] = []
        self.rendered_fixes: list[tuple[int, int]] = []

    def merge_pr(self, pr: str, linked_issue: str = "") -> int:
        self.merged.append(pr)
        return 0

    def render_review_fix_prompt(self, pr_number: int, round_number: int):
        self.rendered_fixes.append((pr_number, round_number))
        prompt = self.repo / ".refactor-loop/prompts/fix.md"
        log = self.repo / ".refactor-loop/logs/fix.log"
        prompt.write_text("fix\n", encoding="utf-8")
        return type(
            "Spec",
            (),
            {"prompt_path": ".refactor-loop/prompts/fix.md", "log_path": ".refactor-loop/logs/fix.log"},
        )()


class AllowingGitHubActor:
    def require_admission(self, action: str) -> None:
        pass


class ReviewGateEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        for rel in (".refactor-loop/state", ".refactor-loop/logs", ".refactor-loop/prompts", ".refactor-loop/runs"):
            (self.repo / rel).mkdir(parents=True, exist_ok=True)
        (self.repo / ".refactor-loop/host.env").write_text(
            f'export REPO_ROOT="{self.repo}"\n'
            'export GH_REPO_SLUG="owner/repo"\n'
            'export INTEGRATION_BRANCH="canonical-integration"\n'
            'export REVIEW_BASE_BRANCH="canonical-review"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.repo, env={"CONSENSUS_RND_HOST_ENV": ".refactor-loop/host.env"}, cwd=self.repo)
        self.actions = FakeActions(self.repo)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_review_set(self, verdicts: dict[str, str]) -> None:
        for role in REVIEW_ROLES:
            verdict = verdicts[role]
            (self.repo / ".refactor-loop/prompts" / f"review-pr480-{role}-r1.md").write_text(
                f"head_sha: {HEAD_SHA}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/runs" / f"review-pr480-{role}-r1.md").write_text(
                f"---\nverdict: {verdict}\n---\nREVIEW_DONE:480:{role}:{verdict}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/logs" / f"review-pr480-{role}-r1.log").write_text(
                f"REVIEW_DONE:480:{role}:{verdict}\nEXIT=0\n",
                encoding="utf-8",
            )

    def project_review_gate_action(self) -> dict:
        gh_items = [
            GhItem(
                kind="PR",
                number=480,
                title="review gate target",
                labels=("crnd:lifecycle:managed", "crnd:phase:reviewing", "crnd:human:auto"),
                head_ref="impl/pr480",
                head_sha=HEAD_SHA,
            )
        ]
        actions = completed_marker_actions(self.repo, {("PR", 480)}, gh_items, None)
        review_actions = [action for action in actions if action.get("controller_action") == "review_gate"]
        self.assertEqual(len(review_actions), 1, json.dumps(actions, sort_keys=True))
        action = review_actions[0]
        self.assertEqual(action["head_sha"], HEAD_SHA)
        return action

    def apply_action(self, action: dict, transient_pull_failure: bool = False, is_draft: bool = False):
        pull_attempts = 0

        def command_runner(command):
            nonlocal pull_attempts
            command = list(command)
            if command[:3] == ["gh", "pr", "view"] and ".state" in command:
                return subprocess.CompletedProcess(command, 0, "OPEN\n", "")
            if command[:3] == ["gh", "pr", "view"] and ".headRefOid" in command:
                return subprocess.CompletedProcess(command, 0, HEAD_SHA + "\n", "")
            if command[:3] == ["gh", "pr", "view"] and "mergeable,isDraft" in command:
                return subprocess.CompletedProcess(command, 0, json.dumps({"mergeable": "MERGEABLE", "isDraft": is_draft}), "")
            if command[:2] == ["gh", "api"] and command[2] == "repos/owner/repo/pulls/480":
                pull_attempts += 1
                if transient_pull_failure and pull_attempts == 2:
                    return subprocess.CompletedProcess(command, 1, "", "temporary pull read failure")
                return subprocess.CompletedProcess(command, 0, json.dumps({"state": "open", "head": {"sha": HEAD_SHA}}), "")
            if command[:2] == ["gh", "api"] and command[2] == f"repos/owner/repo/commits/{HEAD_SHA}/check-runs":
                payload = {"check_runs": [{"name": "ci", "status": "completed", "conclusion": "success"}]}
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
            return subprocess.CompletedProcess(command, 0, "", "")

        runner = WakeupRunner(
            self.ctx,
            plan_loader=lambda _repo: {
                "schema": "wakeup-plan",
                "mode": "closed-action-projection",
                "apply_authority": "wakeup-runner-396-only",
                "no_lifecycle_authority": True,
                "actions": [action],
            },
            actions=self.actions,
            command_runner=command_runner,
        )
        return runner.run_once()[0]

    def test_review_gate_e2e_reject_projects_head_and_dispatches_fix(self) -> None:
        self.write_review_set({"architect": "approve", "tests": "reject", "quality": "approve"})
        action = self.project_review_gate_action()

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            result = self.apply_action(action)

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.actions.rendered_fixes, [(480, 1)])
        self.assertEqual(self.actions.merged, [])
        launch.assert_called_once()

    def test_review_gate_e2e_all_approve_projects_head_and_merges(self) -> None:
        self.write_review_set({"architect": "approve", "tests": "approve", "quality": "approve"})
        action = self.project_review_gate_action()

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            result = self.apply_action(action)

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.actions.rendered_fixes, [])
        self.assertEqual(self.actions.merged, ["480"])
        launch.assert_not_called()

    def test_review_gate_e2e_managed_draft_reaches_real_ready_then_merge(self) -> None:
        self.write_review_set({"architect": "approve", "tests": "approve", "quality": "comment"})
        action = self.project_review_gate_action()
        gh_calls: list[list[str]] = []

        real_actions = ControllerActions(self.ctx, github_actor=AllowingGitHubActor())

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:5] == ["pr", "view", "480", "--json", "body"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            if args[:5] == ["pr", "view", "480", "--json", "isDraft"]:
                return mock.Mock(returncode=0, stdout="true\n", stderr="")
            if args == ["pr", "view", "480", "--json", "labels,body"]:
                return mock.Mock(returncode=0, stdout=json.dumps({"labels": [{"name": labels.MANAGED}], "body": ""}), stderr="")
            if args == ["pr", "ready", "480"]:
                return mock.Mock(returncode=0, stdout="Ready\n", stderr="")
            if args == ["pr", "merge", "480", "--squash", "--delete-branch"]:
                return mock.Mock(returncode=0, stdout="Merged pull request #480\n", stderr="")
            if args[:5] == ["pr", "view", "480", "--json", "number,mergedAt,mergeCommit,baseRefName,headRefName"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "number": 480,
                            "mergedAt": "2026-06-03T00:00:00Z",
                            "mergeCommit": {"oid": "abc123"},
                            "baseRefName": "canonical-review",
                            "headRefName": "impl/pr480",
                        }
                    ),
                    stderr="",
                )
            if args[:5] == ["pr", "view", "480", "--json", "headRefName"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            if args[:3] == ["pr", "edit", "480"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected gh call: {args}")

        with mock.patch.object(real_actions, "gh", side_effect=fake_gh):
            with mock.patch.object(real_actions, "git", side_effect=AssertionError("git should not be called")):
                runner = WakeupRunner(
                    self.ctx,
                    plan_loader=lambda _repo: {
                        "schema": "wakeup-plan",
                        "mode": "closed-action-projection",
                        "apply_authority": "wakeup-runner-396-only",
                        "no_lifecycle_authority": True,
                        "actions": [action],
                    },
                    actions=real_actions,
                    command_runner=lambda command: self._review_gate_command(command, is_draft=True),
                )
                result = runner.run_once()[0]

        self.assertEqual(result.status, "applied")
        ready_index = gh_calls.index(["pr", "ready", "480"])
        merge_index = gh_calls.index(["pr", "merge", "480", "--squash", "--delete-branch"])
        self.assertLess(ready_index, merge_index)

    def _review_gate_command(self, command, *, is_draft: bool = False):
        command = list(command)
        if command[:3] == ["gh", "pr", "view"] and ".state" in command:
            return subprocess.CompletedProcess(command, 0, "OPEN\n", "")
        if command[:3] == ["gh", "pr", "view"] and ".headRefOid" in command:
            return subprocess.CompletedProcess(command, 0, HEAD_SHA + "\n", "")
        if command[:3] == ["gh", "pr", "view"] and "mergeable,isDraft" in command:
            return subprocess.CompletedProcess(command, 0, json.dumps({"mergeable": "MERGEABLE", "isDraft": is_draft}), "")
        if command[:2] == ["gh", "api"] and command[2] == "repos/owner/repo/pulls/480":
            return subprocess.CompletedProcess(command, 0, json.dumps({"state": "open", "head": {"sha": HEAD_SHA}}), "")
        if command[:2] == ["gh", "api"] and command[2] == f"repos/owner/repo/commits/{HEAD_SHA}/check-runs":
            payload = {"check_runs": [{"name": "ci", "status": "completed", "conclusion": "success"}]}
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def test_review_gate_e2e_all_approve_uses_valid_run_command_argv_and_merges(self) -> None:
        self.write_review_set({"architect": "approve", "tests": "approve", "quality": "approve"})
        action = self.project_review_gate_action()
        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            command = list(command)
            calls.append(command)
            if command[:2] == ["gh", "--repo"]:
                return subprocess.CompletedProcess(command, 1, "", "unknown flag: --repo")
            if command == ["gh", "api", "rate_limit"]:
                payload = {"resources": {"graphql": {"remaining": 5000}}}
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
            if command == ["gh", "pr", "view", "480", "--repo", "owner/repo", "--json", "state", "--jq", ".state"]:
                return subprocess.CompletedProcess(command, 0, "OPEN\n", "")
            if command == ["gh", "pr", "view", "480", "--repo", "owner/repo", "--json", "headRefOid", "--jq", ".headRefOid"]:
                return subprocess.CompletedProcess(command, 0, HEAD_SHA + "\n", "")
            if command == ["gh", "api", "repos/owner/repo/pulls/480"]:
                return subprocess.CompletedProcess(command, 0, json.dumps({"state": "open", "head": {"sha": HEAD_SHA}}), "")
            if command == ["gh", "api", f"repos/owner/repo/commits/{HEAD_SHA}/check-runs", "--paginate", "--slurp"]:
                payload = {"check_runs": [{"name": "ci", "status": "completed", "conclusion": "success"}]}
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
            if command == ["gh", "pr", "view", "480", "--repo", "owner/repo", "--json", "mergeable,isDraft"]:
                return subprocess.CompletedProcess(command, 0, json.dumps({"mergeable": "MERGEABLE", "isDraft": False}), "")
            return subprocess.CompletedProcess(command, 1, "", f"unexpected command: {command!r}")

        runner = WakeupRunner(
            self.ctx,
            plan_loader=lambda _repo: {
                "schema": "wakeup-plan",
                "mode": "closed-action-projection",
                "apply_authority": "wakeup-runner-396-only",
                "no_lifecycle_authority": True,
                "actions": [action],
            },
            actions=self.actions,
        )
        with mock.patch("codex_refactor_loop.wakeup_runner.subprocess.run", side_effect=fake_run):
            result = runner.run_once()[0]

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.actions.rendered_fixes, [])
        self.assertEqual(self.actions.merged, ["480"])
        self.assertIn(["gh", "api", "repos/owner/repo/pulls/480"], calls)
        self.assertIn(["gh", "api", f"repos/owner/repo/commits/{HEAD_SHA}/check-runs", "--paginate", "--slurp"], calls)
        self.assertIn(["gh", "pr", "view", "480", "--repo", "owner/repo", "--json", "mergeable,isDraft"], calls)
        for command in calls:
            self.assertNotEqual(command[:2], ["gh", "--repo"])

    def test_review_gate_e2e_transient_pull_read_still_merges(self) -> None:
        self.write_review_set({"architect": "approve", "tests": "approve", "quality": "approve"})
        action = self.project_review_gate_action()

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            result = self.apply_action(action, transient_pull_failure=True)

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.actions.rendered_fixes, [])
        self.assertEqual(self.actions.merged, ["480"])
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
