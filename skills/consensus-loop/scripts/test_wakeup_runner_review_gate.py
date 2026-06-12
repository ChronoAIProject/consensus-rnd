#!/usr/bin/env python3
"""Review truth-table tests for wakeup-runner."""

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
from codex_refactor_loop.cross_instance_stand_down import CrossInstanceAdmission
from codex_refactor_loop.github_actor import GitHubActorAdmission
from codex_refactor_loop.wakeup_runner import ReviewEvidence, WakeupRunner


class FakeActions:
    def __init__(self) -> None:
        self.merged: list[str] = []
        self.rendered: list[tuple[int, int]] = []
        self.github_actor = self

    def require_admission(self, action: str) -> GitHubActorAdmission:
        return GitHubActorAdmission(login="controller-bot", repo_slug="owner/repo", permission="write")

    def cross_instance_admission(self, kind: str, target: str | int, current_login: str, now) -> CrossInstanceAdmission:
        return CrossInstanceAdmission("allowed", "test-allowed")

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
        (self.repo / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.repo / ".config/consensus-rnd/host.env").write_text(f'export REPO_ROOT="{self.repo}"\nexport GH_REPO_SLUG="owner/repo"\n', encoding="utf-8")
        self.ctx = LoopContext.load(repo_root=self.repo, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})
        (self.repo / ".refactor-loop/prompts/fix.md").write_text("fix\n", encoding="utf-8")
        self.pr_worktree = self.repo / ".worktrees" / "pr12"
        self.pr_worktree.mkdir(parents=True, exist_ok=True)
        self.actions = FakeActions()
        self.supervisor = FakeSupervisor()
        self.github_comments: list[dict[str, object]] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def github_review_comment(self, role: str, verdict: str, *, head_sha: str = "a" * 40, round_number: int = 1) -> dict[str, object]:
        return {
            "body": (
                f"review_round: {round_number}\n"
                f"head_sha: {head_sha}\n"
                f"REVIEW_DONE:12:{role}:{verdict}\n\n"
                "⟦AI:AUTO-LOOP⟧"
            )
        }

    def write_review(
        self,
        role: str,
        verdict: str,
        *,
        head_sha: str = "a" * 40,
        round_number: int = 1,
        exit_zero: bool = True,
        post_comment: bool = True,
    ) -> None:
        (self.repo / ".refactor-loop/runs" / f"review-pr12-{role}-r{round_number}.md").write_text(
            f"---\nverdict: {verdict}\n---\nhead_sha: {head_sha}\nREVIEW_DONE:12:{role}:{verdict}\n",
            encoding="utf-8",
        )
        (self.repo / ".refactor-loop/logs" / f"review-pr12-{role}-r{round_number}.log").write_text(
            f"head_sha: {head_sha}\nREVIEW_DONE:12:{role}:{verdict}\n" + ("EXIT=0\n" if exit_zero else "EXIT=1\n"),
            encoding="utf-8",
        )
        if post_comment:
            marker_prefix = f"REVIEW_DONE:12:{role}:"
            round_prefix = f"review_round: {round_number}\n"
            self.github_comments = [
                comment
                for comment in self.github_comments
                if marker_prefix not in str(comment.get("body") or "") or not str(comment.get("body") or "").startswith(round_prefix)
            ]
            self.github_comments.append(self.github_review_comment(role, verdict, head_sha=head_sha, round_number=round_number))

    def action(self, **overrides) -> dict:
        log = self.repo / ".refactor-loop/logs/review-pr12-architect-r1.log"
        log.write_text(f"head_sha: {'a' * 40}\nREVIEW_DONE:12:architect:approve\nEXIT=0\n", encoding="utf-8")
        action = {
            "kind": "completed-marker",
            "action_id": "review:12",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "clean_exit_source_marker", "live_open_target"],
            "source_artifact": ".refactor-loop/logs/review-pr12-architect-r1.log",
            "source_marker": "REVIEW_DONE:12:architect:approve",
            "head_sha": "a" * 40,
            "target_kind": "PR",
            "target_number": 12,
            "target": {"kind": "PR", "number": 12},
            "controller_action": "review_gate",
            "no_generic_command": True,
        }
        action.update(overrides)
        return action

    def run_action(
        self,
        action: dict | None = None,
        *,
        live_head: str = "a" * 40,
        check_status: str = "completed",
        check_conclusion: str = "success",
        check_name: str = "ci",
        required_checks: tuple[str, ...] = ("ci",),
        mergeable: str = "MERGEABLE",
        is_draft: bool = False,
        changed_files: int = 1,
    ) -> object:
        def command_runner(command):
            repo_root = self.ctx.repo_root
            if command[:3] == ["gh", "pr", "view"] and ".state" in command:
                return subprocess.CompletedProcess(command, 0, "OPEN\n", "")
            if command[:3] == ["gh", "pr", "view"] and ".headRefOid" in command:
                return subprocess.CompletedProcess(command, 0, live_head + "\n", "")
            if command[:3] == ["gh", "pr", "view"] and "headRefName" in command and "--jq" not in command:
                return subprocess.CompletedProcess(command, 0, json.dumps({"headRefName": "refactor/pr12"}), "")
            if command[:3] == ["gh", "pr", "view"] and "mergeable,isDraft,changedFiles" in command:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps({"mergeable": mergeable, "isDraft": is_draft, "changedFiles": changed_files}),
                    "",
                )
            if command[:2] == ["gh", "api"] and command[2] == "repos/owner/repo/issues/12/comments?per_page=100":
                return subprocess.CompletedProcess(command, 0, json.dumps([self.github_comments]), "")
            if command[:2] == ["gh", "api"] and command[2] == "repos/owner/repo/pulls/12":
                return subprocess.CompletedProcess(command, 0, json.dumps({"state": "open", "head": {"sha": live_head}}), "")
            if command[:3] == ["gh", "pr", "view"] and "baseRefName,headRefOid,mergeStateStatus" in command:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps({"baseRefName": "main", "headRefOid": live_head, "mergeStateStatus": "DIRTY"}),
                    "",
                )
            if command[:2] == ["gh", "api"] and command[2] == "repos/owner/repo/branches/main/protection/required_status_checks":
                return subprocess.CompletedProcess(command, 0, json.dumps({"contexts": list(required_checks)}), "")
            if command[:2] == ["gh", "api"] and command[2] == "repos/owner/repo/rules/branches/main":
                return subprocess.CompletedProcess(command, 1, "", "404 Not Found")
            if command[:2] == ["gh", "api"] and command[2] == f"repos/owner/repo/commits/{live_head}/check-runs":
                payload = {"check_runs": [{"name": check_name, "status": check_status, "conclusion": check_conclusion}]}
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
            if command == ["git", "-C", str(repo_root), "worktree", "list", "--porcelain"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    f"worktree {repo_root}\nbranch refs/heads/main\n\n"
                    f"worktree {self.pr_worktree.resolve()}\nbranch refs/heads/refactor/pr12\n\n",
                    "",
                )
            return subprocess.CompletedProcess(command, 0, "", "")

        if action is None:
            action = self.action()
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
            supervisor=self.supervisor,
            command_runner=command_runner,
        )
        return runner.run_once()[0]

    def run_action_with_comment_read(self, result: subprocess.CompletedProcess[str]) -> object:
        def command_runner(command):
            command = list(command)
            repo_root = self.ctx.repo_root
            if command[:3] == ["gh", "pr", "view"] and ".state" in command:
                return subprocess.CompletedProcess(command, 0, "OPEN\n", "")
            if command[:3] == ["gh", "pr", "view"] and ".headRefOid" in command:
                return subprocess.CompletedProcess(command, 0, "a" * 40 + "\n", "")
            if command[:2] == ["gh", "api"] and command[2] == "repos/owner/repo/issues/12/comments?per_page=100":
                return result
            if command[:2] == ["gh", "api"] and command[2] == "repos/owner/repo/pulls/12":
                return subprocess.CompletedProcess(command, 0, json.dumps({"state": "open", "head": {"sha": "a" * 40}}), "")
            if command == ["git", "-C", str(repo_root), "worktree", "list", "--porcelain"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    f"worktree {repo_root}\nbranch refs/heads/main\n\n"
                    f"worktree {self.pr_worktree.resolve()}\nbranch refs/heads/refactor/pr12\n\n",
                    "",
                )
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

    def assert_github_comment_gate_blocks(self, comments: list[dict[str, object]], reason: str) -> None:
        self.github_comments = [
            self.github_review_comment("architect", "approve"),
            self.github_review_comment("quality", "comment"),
        ]
        self.github_comments.extend(comments)

        result = self.run_action()

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, f"WAIT_OR_REDISPATCH:invalid_reviewer_evidence:{reason}")
        self.assertEqual(self.actions.merged, [])

    def runner_for_gate(self, *, live_head: str = "a" * 40) -> WakeupRunner:
        def command_runner(command):
            if command[:3] == ["gh", "pr", "view"] and ".headRefOid" in command:
                return subprocess.CompletedProcess(command, 0, live_head + "\n", "")
            if command[:3] == ["gh", "pr", "view"] and "mergeable,isDraft,changedFiles" in command:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps({"mergeable": "MERGEABLE", "isDraft": False, "changedFiles": 1}),
                    "",
                )
            if command[:2] == ["gh", "api"] and command[2] == "repos/owner/repo/branches/main/protection/required_status_checks":
                return subprocess.CompletedProcess(command, 0, json.dumps({"contexts": []}), "")
            if command[:2] == ["gh", "api"] and command[2] == "repos/owner/repo/rules/branches/main":
                return subprocess.CompletedProcess(command, 1, "", "404 Not Found")
            if command[:2] == ["gh", "api"] and command[2] == "repos/owner/repo/issues/12/comments?per_page=100":
                return subprocess.CompletedProcess(command, 0, json.dumps([self.github_comments]), "")
            return subprocess.CompletedProcess(command, 0, "", "")

        return WakeupRunner(
            self.ctx,
            actions=self.actions,
            supervisor=self.supervisor,
            command_runner=command_runner,
        )

    def test_review_gate_merge_only_when_reject_zero_approve_one_and_all_present(self) -> None:
        self.write_review("architect", "approve")
        self.write_review("tests", "comment")
        self.write_review("quality", "comment")

        result = self.run_action()

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.actions.merged, ["12"])
        self.assertEqual(self.supervisor.calls, 0)

    def test_managed_draft_with_green_review_gate_reaches_merge_decision(self) -> None:
        self.write_review("architect", "approve")
        self.write_review("tests", "approve")
        self.write_review("quality", "comment")

        result = self.run_action(is_draft=True)

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.actions.merged, ["12"])
        self.assertEqual(self.supervisor.calls, 0)

    def test_approved_zero_file_pr_waits_without_merge(self) -> None:
        self.write_review("architect", "approve")
        self.write_review("tests", "approve")
        self.write_review("quality", "comment")

        result = self.run_action(changed_files=0)

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "WAIT_OR_REDISPATCH:empty_diff_pr")
        self.assertEqual(self.actions.merged, [])
        self.assertEqual(self.supervisor.calls, 0)

    def test_reject_dispatches_fix_not_merge(self) -> None:
        self.write_review("architect", "approve")
        self.write_review("tests", "reject")
        self.write_review("quality", "comment")

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            result = self.run_action()

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.actions.merged, [])
        self.assertEqual(self.actions.rendered, [(12, 1)])
        launch.assert_called_once()
        self.assertEqual(Path(launch.call_args.kwargs["prompt"]).resolve(), (self.repo / ".refactor-loop/prompts/fix.md").resolve())
        self.assertEqual(Path(launch.call_args.kwargs["log"]).resolve(), (self.repo / ".refactor-loop/logs/fix.log").resolve())
        self.assertEqual(Path(launch.call_args.kwargs["cd"]).resolve(), self.pr_worktree.resolve())
        self.assertEqual(self.supervisor.calls, 0)

    def test_review_gate_assembles_latest_live_head_per_role_across_rounds_and_routes_reject_to_fix(self) -> None:
        live = "c" * 40
        self.write_review("tests", "approve", head_sha="d" * 40, round_number=13)
        self.write_review("tests", "reject", head_sha=live, round_number=14)
        self.write_review("quality", "comment", head_sha=live, round_number=14)
        self.write_review("architect", "reject", head_sha=live, round_number=15)
        self.write_review("architect", "approve", head_sha=live, round_number=16)

        gate = self.runner_for_gate(live_head=live)._review_gate(12)
        self.assertTrue(gate["all_present"])
        self.assertEqual(gate["reviewed_head_sha"], live)
        self.assertEqual(gate["reject"], 1)
        self.assertEqual(gate["approve"], 1)
        self.assertEqual(gate["comment"], 1)

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            result = self.run_action(self.action(head_sha=live), live_head=live, required_checks=())

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.actions.merged, [])
        self.assertEqual(self.actions.rendered, [(12, 1)])
        launch.assert_called_once()

    def test_review_gate_latest_same_role_verdict_supersedes_older_same_head_verdict(self) -> None:
        live = "e" * 40
        self.write_review("architect", "reject", head_sha=live, round_number=15)
        self.write_review("architect", "approve", head_sha=live, round_number=16)
        self.write_review("tests", "approve", head_sha=live, round_number=14)
        self.write_review("quality", "comment", head_sha=live, round_number=14)

        gate = self.runner_for_gate(live_head=live)._review_gate(12)
        self.assertEqual(gate["verdicts"]["architect"], "approve")
        self.assertEqual(gate["reject"], 0)
        self.assertEqual(gate["approve"], 2)

        result = self.run_action(self.action(head_sha=live), live_head=live, required_checks=())

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.actions.merged, ["12"])
        self.assertEqual(self.actions.rendered, [])

    def test_review_gate_newer_pending_wave_does_not_block_completion_when_role_has_valid_verdict(self) -> None:
        live = "f" * 40
        evidences = [
            ReviewEvidence("architect", 4, "approve", live, "test"),
            ReviewEvidence("architect", 5, "approve", live, "test", pending=True, reason="pending:architect"),
            ReviewEvidence("architect", 5, "approve", live, "test", pending=True, reason="pending:architect"),
            ReviewEvidence("tests", 4, "approve", live, "test"),
            ReviewEvidence("quality", 4, "comment", live, "test"),
        ]
        runner = self.runner_for_gate(live_head=live)
        with (
            mock.patch.object(runner, "_review_evidences", return_value=evidences),
            mock.patch.object(runner, "_review_gate_ci_error", return_value=None),
        ):
            decision = runner._review_gate_decision(self.action(head_sha=live))

        self.assertEqual(decision["decision"], "MERGE_WITH_COMMENTS")
        self.assertTrue(decision["gate"]["all_present"])
        self.assertEqual(decision["gate"]["verdicts"]["architect"], "approve")
        self.assertEqual(decision["gate"]["reviewed_head_sha"], live)
        self.assertEqual(decision["gate"]["invalid"], [])
        self.assertEqual(decision["gate"]["pending"], [])

    def test_review_gate_duplicate_same_role_same_round_fails_closed(self) -> None:
        live = "a" * 40
        self.github_comments = [
            self.github_review_comment("architect", "approve", head_sha=live, round_number=2),
            self.github_review_comment("architect", "reject", head_sha=live, round_number=2),
            self.github_review_comment("tests", "approve", head_sha=live, round_number=2),
            self.github_review_comment("quality", "comment", head_sha=live, round_number=2),
        ]

        result = self.run_action()

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "WAIT_OR_REDISPATCH:invalid_reviewer_evidence:duplicate_reviewer_evidence:architect")
        self.assertEqual(self.actions.merged, [])
        self.assertEqual(self.actions.rendered, [])

    def test_review_gate_role_with_only_stale_head_remains_incomplete(self) -> None:
        self.write_review("architect", "approve")
        self.write_review("tests", "approve")
        self.write_review("quality", "comment", head_sha="b" * 40)

        result = self.run_action()

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "WAIT_OR_REDISPATCH:invalid_reviewer_evidence:stale_reviewed_head_sha:quality")
        self.assertEqual(self.actions.merged, [])
        self.assertEqual(self.actions.rendered, [])

    def test_review_gate_role_with_only_pending_live_head_blocks(self) -> None:
        live = "a" * 40
        evidences = [
            ReviewEvidence("architect", 4, "approve", live, "test"),
            ReviewEvidence("tests", 4, "approve", live, "test"),
            ReviewEvidence("quality", 5, "comment", live, "test", pending=True, reason="pending:quality"),
        ]
        runner = self.runner_for_gate(live_head=live)
        with mock.patch.object(runner, "_review_evidences", return_value=evidences):
            decision = runner._review_gate_decision(self.action(head_sha=live))

        self.assertEqual(decision["decision"], "WAIT_OR_REDISPATCH")
        self.assertEqual(decision["reason"], "pending_reviewer_evidence:pending:quality")
        self.assertEqual(self.actions.merged, [])
        self.assertEqual(self.actions.rendered, [])

    def test_missing_reviewer_fails_closed(self) -> None:
        self.write_review("architect", "approve")
        self.write_review("tests", "comment")

        result = self.run_action()

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "WAIT_OR_REDISPATCH:missing_reviewers")
        self.assertEqual(self.actions.merged, [])

    def test_github_comment_api_unavailable_fails_closed_without_local_fallback(self) -> None:
        for role, verdict in (("architect", "approve"), ("tests", "approve"), ("quality", "comment")):
            self.write_review(role, verdict, post_comment=False)

        result = self.run_action_with_comment_read(
            subprocess.CompletedProcess(
                ["gh", "api", "repos/owner/repo/issues/12/comments?per_page=100", "--paginate", "--slurp"],
                1,
                "",
                "temporary comments API failure",
            )
        )

        self.assertEqual(result.status, "blocked")
        self.assertEqual(
            result.reason,
            "WAIT_OR_REDISPATCH:invalid_reviewer_evidence:github_review_comments_unavailable:architect",
        )
        self.assertEqual(self.actions.merged, [])

    def test_github_comment_invalid_json_or_shape_fails_closed(self) -> None:
        cases = (
            subprocess.CompletedProcess(
                ["gh", "api", "repos/owner/repo/issues/12/comments?per_page=100", "--paginate", "--slurp"],
                0,
                "{not-json",
                "",
            ),
            subprocess.CompletedProcess(
                ["gh", "api", "repos/owner/repo/issues/12/comments?per_page=100", "--paginate", "--slurp"],
                0,
                json.dumps({"items": []}),
                "",
            ),
        )
        for completed in cases:
            with self.subTest(stdout=completed.stdout):
                self.actions.merged.clear()
                result = self.run_action_with_comment_read(completed)

                self.assertEqual(result.status, "blocked")
                self.assertEqual(
                    result.reason,
                    "WAIT_OR_REDISPATCH:invalid_reviewer_evidence:github_review_comments_invalid_json:architect",
                )
                self.assertEqual(self.actions.merged, [])

    def test_malformed_github_comment_evidence_fails_closed_without_merge(self) -> None:
        invalid_cases = (
            (
                "missing_final_ai_sentinel:tests",
                {"body": f"review_round: 1\nhead_sha: {'a' * 40}\nREVIEW_DONE:12:tests:approve"},
            ),
            (
                "missing_review_round:tests",
                {"body": f"head_sha: {'a' * 40}\nREVIEW_DONE:12:tests:approve\n\n⟦AI:AUTO-LOOP⟧"},
            ),
            (
                "missing_reviewed_head_sha:tests",
                {"body": "review_round: 1\nREVIEW_DONE:12:tests:approve\n\n⟦AI:AUTO-LOOP⟧"},
            ),
            (
                "invalid_review_marker:tests",
                {"body": f"review_round: 1\nhead_sha: {'a' * 40}\nREVIEW_DONE:13:tests:approve\n\n⟦AI:AUTO-LOOP⟧"},
            ),
            (
                "invalid_review_marker:tests",
                {"body": f"review_round: 1\nhead_sha: {'a' * 40}\nREVIEW_DONE:12:tests:banana\n\n⟦AI:AUTO-LOOP⟧"},
            ),
            (
                "invalid_review_marker:tests",
                {
                    "body": (
                        f"review_round: 1\nhead_sha: {'a' * 40}\n"
                        "REVIEW_DONE:12:tests:approve\n"
                        "REVIEW_DONE:12:tests:reject\n\n"
                        "⟦AI:AUTO-LOOP⟧"
                    )
                },
            ),
        )
        for reason, comment in invalid_cases:
            with self.subTest(reason=reason):
                self.actions.merged.clear()
                self.assert_github_comment_gate_blocks([comment], reason)

    def test_missing_action_reviewed_head_fails_closed_without_merge(self) -> None:
        self.write_review("architect", "approve")
        self.write_review("tests", "approve")
        self.write_review("quality", "comment")
        action = self.action()
        action.pop("head_sha")

        result = self.run_action(action)

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "WAIT_OR_REDISPATCH:missing_action_reviewed_head_sha")
        self.assertEqual(self.actions.merged, [])

    def test_missing_required_reviewer_head_fails_closed_without_merge(self) -> None:
        self.write_review("architect", "approve")
        self.write_review("tests", "approve", head_sha="")
        self.write_review("quality", "comment")

        result = self.run_action()

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "WAIT_OR_REDISPATCH:invalid_reviewer_evidence:missing_reviewed_head_sha:tests")
        self.assertEqual(self.actions.merged, [])

    def test_stale_required_reviewer_head_fails_closed_without_merge(self) -> None:
        self.write_review("architect", "approve")
        self.write_review("tests", "approve", head_sha="b" * 40)
        self.write_review("quality", "comment")

        result = self.run_action()

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "WAIT_OR_REDISPATCH:invalid_reviewer_evidence:stale_reviewed_head_sha:tests")
        self.assertEqual(self.actions.merged, [])

    def test_stale_action_head_fails_closed_without_merge(self) -> None:
        self.write_review("architect", "approve", head_sha="a" * 40)
        self.write_review("tests", "approve", head_sha="a" * 40)
        self.write_review("quality", "comment", head_sha="a" * 40)

        result = self.run_action(self.action(head_sha="b" * 40), live_head="a" * 40)

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "WAIT_OR_REDISPATCH:action_head_mismatch")
        self.assertEqual(self.actions.merged, [])

    def test_stale_reviewed_head_fails_closed_without_merge(self) -> None:
        self.write_review("architect", "approve", head_sha="b" * 40)
        self.write_review("tests", "approve", head_sha="b" * 40)
        self.write_review("quality", "comment", head_sha="b" * 40)

        result = self.run_action(self.action(head_sha="b" * 40), live_head="a" * 40)

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "WAIT_OR_REDISPATCH:invalid_reviewer_evidence:stale_reviewed_head_sha:architect")
        self.assertEqual(self.actions.merged, [])

    def test_review_gate_higher_valid_same_head_supersedes_lower_complete_round(self) -> None:
        self.write_review("architect", "approve", head_sha="a" * 40, round_number=4)
        self.write_review("tests", "approve", head_sha="a" * 40, round_number=4)
        self.write_review("quality", "comment", head_sha="a" * 40, round_number=4)
        self.write_review("architect", "reject", head_sha="a" * 40, round_number=5)

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            result = self.run_action()

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.actions.merged, [])
        self.assertEqual(self.actions.rendered, [(12, 1)])
        launch.assert_called_once()

    def test_review_gate_lower_complete_reject_is_not_masked_by_higher_pending(self) -> None:
        self.write_review("architect", "reject", head_sha="a" * 40, round_number=4)
        self.write_review("tests", "approve", head_sha="a" * 40, round_number=4)
        self.write_review("quality", "comment", head_sha="a" * 40, round_number=4)
        (self.repo / ".refactor-loop/logs/review-pr12-tests-r5.log").write_text(
            f"head_sha: {'a' * 40}\nREVIEW_DONE:12:tests:approve\n",
            encoding="utf-8",
        )

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            result = self.run_action()

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.actions.merged, [])
        self.assertEqual(self.actions.rendered, [(12, 1)])
        launch.assert_called_once()

    def test_review_gate_no_complete_round_waits_on_candidate_missing_role(self) -> None:
        self.write_review("architect", "approve", head_sha="a" * 40, round_number=4)
        self.write_review("tests", "approve", head_sha="a" * 40, round_number=4)
        (self.repo / ".refactor-loop/logs/review-pr12-architect-r5.log").write_text(
            f"head_sha: {'a' * 40}\nREVIEW_DONE:12:architect:approve\n",
            encoding="utf-8",
        )

        result = self.run_action()

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "WAIT_OR_REDISPATCH:missing_reviewers")
        runner = WakeupRunner(
            self.ctx,
            actions=self.actions,
            supervisor=self.supervisor,
            command_runner=lambda command: subprocess.CompletedProcess(
                command,
                0,
                json.dumps([self.github_comments]) if command[:2] == ["gh", "api"] else "a" * 40 + "\n",
                "",
            ),
        )
        gate = runner._review_gate(12)
        self.assertEqual({"architect": "a" * 40, "tests": "a" * 40}, gate["heads_by_role"])
        self.assertNotIn("quality", gate["heads_by_role"])

    def test_missing_artifact_head_recovers_from_controller_rendered_prompt(self) -> None:
        for role, verdict in (("architect", "approve"), ("tests", "approve"), ("quality", "comment")):
            (self.repo / ".refactor-loop/prompts" / f"review-pr12-{role}-r1.md").write_text(
                f"head_sha: {'a' * 40}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/runs" / f"review-pr12-{role}-r1.md").write_text(
                f"---\nverdict: {verdict}\n---\nREVIEW_DONE:12:{role}:{verdict}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/logs" / f"review-pr12-{role}-r1.log").write_text(
                f"REVIEW_DONE:12:{role}:{verdict}\nEXIT=0\n",
                encoding="utf-8",
            )
            self.github_comments.append(self.github_review_comment(role, verdict))

        result = self.run_action()

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.actions.merged, ["12"])

    def test_review_artifact_verdict_uses_shared_completion_marker_not_marker_verdict(self) -> None:
        for role, verdict in (("architect", "approve"), ("tests", "approve"), ("quality", "comment")):
            (self.repo / ".refactor-loop/runs" / f"review-pr12-{role}-r1.md").write_text(
                f"---\nverdict: {verdict}\n---\nhead_sha: {'a' * 40}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/logs" / f"review-pr12-{role}-r1.log").write_text(
                "clean log with marker in artifact only\nEXIT=0\n",
                encoding="utf-8",
            )
        (self.repo / ".refactor-loop/runs/review-pr12-architect-r1.md").write_text(
            f"---\nverdict: approve\n---\nhead_sha: {'a' * 40}\nREVIEW_DONE:12:architect:reject\n",
            encoding="utf-8",
        )
        for role, verdict in (("tests", "approve"), ("quality", "comment")):
            with (self.repo / ".refactor-loop/runs" / f"review-pr12-{role}-r1.md").open("a", encoding="utf-8") as handle:
                handle.write(f"REVIEW_DONE:12:{role}:{verdict}\n")
        self.github_comments.extend(
            [
                self.github_review_comment("architect", "approve"),
                self.github_review_comment("tests", "approve"),
                self.github_review_comment("quality", "comment"),
            ]
        )

        result = self.run_action()

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.actions.merged, ["12"])

    def test_conflicting_review_completion_marker_fails_closed(self) -> None:
        self.write_review("architect", "approve")
        self.write_review("tests", "approve")
        self.write_review("quality", "comment")
        (self.repo / ".refactor-loop/logs/review-pr12-quality-r1.log").write_text(
            f"head_sha: {'a' * 40}\n"
            "REVIEW_DONE:12:quality:comment\n"
            "REVIEW_DONE:12:quality:reject\n"
            "EXIT=0\n",
            encoding="utf-8",
        )

        result = self.run_action()

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.actions.merged, ["12"])

    def test_target_required_ci_pending_or_failed_fails_closed_without_merge(self) -> None:
        for status, conclusion, reason in (
            ("queued", "", "required_ci_pending"),
            ("completed", "failure", "required_ci_failed"),
        ):
            with self.subTest(reason=reason):
                self.actions.merged.clear()
                self.github_comments.clear()
                self.write_review("architect", "approve")
                self.write_review("tests", "approve")
                self.write_review("quality", "comment")

                result = self.run_action(self.action(action_id=f"review:12:{reason}"), check_status=status, check_conclusion=conclusion)

                self.assertEqual(result.status, "blocked")
                self.assertEqual(result.reason, f"WAIT_OR_REDISPATCH:{reason}")
                self.assertEqual(self.actions.merged, [])

    def test_missing_target_required_ci_fails_closed_without_merge(self) -> None:
        self.write_review("architect", "approve")
        self.write_review("tests", "approve")
        self.write_review("quality", "comment")

        result = self.run_action(self.action(action_id="review:12:required-ci-missing"), check_name="docs")

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "WAIT_OR_REDISPATCH:required_ci_missing")
        self.assertEqual(self.actions.merged, [])

    def test_advisory_ci_pending_or_failed_does_not_block_merge(self) -> None:
        for status, conclusion in (
            ("queued", ""),
            ("completed", "failure"),
        ):
            with self.subTest(status=status, conclusion=conclusion):
                self.actions.merged.clear()
                self.github_comments.clear()
                self.write_review("architect", "approve")
                self.write_review("tests", "approve")
                self.write_review("quality", "comment")

                result = self.run_action(
                    self.action(action_id=f"review:12:advisory:{status}:{conclusion or 'pending'}"),
                    check_status=status,
                    check_conclusion=conclusion,
                    required_checks=(),
                )

                self.assertEqual(result.status, "applied")
                self.assertEqual(self.actions.merged, ["12"])

    def test_non_mergeable_pr_fails_closed_without_merge(self) -> None:
        self.write_review("architect", "approve")
        self.write_review("tests", "approve")
        self.write_review("quality", "comment")

        result = self.run_action(mergeable="CONFLICTING")

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "WAIT_OR_REDISPATCH:non_mergeable_pr")
        self.assertEqual(self.actions.merged, [])

    def test_invalid_reviewer_evidence_waits_without_merge(self) -> None:
        self.write_review("architect", "approve")
        self.write_review("tests", "approve")
        (self.repo / ".refactor-loop/runs" / "review-pr12-quality-r1.md").write_text(
            "---\nverdict: banana\n---\nhead_sha: " + "a" * 40 + "\n",
            encoding="utf-8",
        )
        (self.repo / ".refactor-loop/logs" / "review-pr12-quality-r1.log").write_text(
            "head_sha: " + "a" * 40 + "\nREVIEW_DONE:12:quality:banana\nEXIT=0\n",
            encoding="utf-8",
        )
        self.github_comments.append({"body": f"review_round: 1\nhead_sha: {'a' * 40}\nREVIEW_DONE:12:quality:banana\n\n⟦AI:AUTO-LOOP⟧"})

        result = self.run_action()

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "WAIT_OR_REDISPATCH:invalid_reviewer_evidence:invalid_review_marker:quality")
        self.assertEqual(self.actions.merged, [])

    def test_reviewer_artifact_without_clean_exit_waits_without_merge(self) -> None:
        self.write_review("architect", "approve")
        self.write_review("tests", "approve")
        self.write_review("quality", "comment", exit_zero=False)

        result = self.run_action()

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.actions.merged, ["12"])

    def test_pending_reviewer_log_waits_then_valid_completion_merges_original_round(self) -> None:
        self.write_review("architect", "approve")
        self.write_review("tests", "comment")
        (self.repo / ".refactor-loop/logs" / "review-pr12-quality-r1.log").write_text(
            f"head_sha: {'a' * 40}\nREVIEW_DONE:12:quality:comment\n",
            encoding="utf-8",
        )

        pending = self.run_action()
        self.assertEqual(pending.status, "blocked")
        self.assertEqual(pending.reason, "WAIT_OR_REDISPATCH:missing_reviewers")
        self.assertEqual(self.actions.merged, [])
        self.github_comments.append(self.github_review_comment("quality", "comment"))

        (self.repo / ".refactor-loop/runs" / "review-pr12-quality-r1.md").write_text(
            f"---\nverdict: comment\n---\nhead_sha: {'a' * 40}\nREVIEW_DONE:12:quality:comment\n",
            encoding="utf-8",
        )
        (self.repo / ".refactor-loop/logs" / "review-pr12-quality-r1.log").write_text(
            f"head_sha: {'a' * 40}\nREVIEW_DONE:12:quality:comment\nEXIT=0\n",
            encoding="utf-8",
        )

        completed = self.run_action()
        self.assertEqual(completed.status, "applied")
        self.assertEqual(self.actions.merged, ["12"])
        self.assertEqual(self.supervisor.calls, 0)

    def test_review_gate_source_does_not_treat_draft_as_mergeability_blocker(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "wakeup_runner.py").read_text(encoding="utf-8")
        method = source[source.index("    def _review_gate_mergeability_error") : source.index("    def _next_fix_round")]
        self.assertIn('"mergeable,isDraft,changedFiles"', method)
        self.assertNotIn("pr_draft", method)


if __name__ == "__main__":
    unittest.main()
