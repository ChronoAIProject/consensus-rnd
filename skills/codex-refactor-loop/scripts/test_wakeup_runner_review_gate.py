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

    def write_review(self, role: str, verdict: str, *, head_sha: str = "a" * 40, round_number: int = 1, exit_zero: bool = True) -> None:
        (self.repo / ".refactor-loop/runs" / f"review-pr12-{role}-r{round_number}.md").write_text(
            f"---\nverdict: {verdict}\n---\nhead_sha: {head_sha}\nREVIEW_DONE:12:{role}:{verdict}\n",
            encoding="utf-8",
        )
        (self.repo / ".refactor-loop/logs" / f"review-pr12-{role}-r{round_number}.log").write_text(
            f"head_sha: {head_sha}\nREVIEW_DONE:12:{role}:{verdict}\n" + ("EXIT=0\n" if exit_zero else "EXIT=1\n"),
            encoding="utf-8",
        )

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
        mergeable: str = "MERGEABLE",
        is_draft: bool = False,
    ) -> object:
        def command_runner(command):
            if command[:3] == ["gh", "pr", "view"] and ".state" in command:
                return subprocess.CompletedProcess(command, 0, "OPEN\n", "")
            if command[:3] == ["gh", "pr", "view"] and ".headRefOid" in command:
                return subprocess.CompletedProcess(command, 0, live_head + "\n", "")
            if command[:3] == ["gh", "pr", "view"] and "mergeable,isDraft" in command:
                return subprocess.CompletedProcess(command, 0, json.dumps({"mergeable": mergeable, "isDraft": is_draft}), "")
            if command[:2] == ["gh", "api"] and command[2] == "repos/owner/repo/pulls/12":
                return subprocess.CompletedProcess(command, 0, json.dumps({"state": "open", "head": {"sha": live_head}}), "")
            if command[:2] == ["gh", "api"] and command[2] == f"repos/owner/repo/commits/{live_head}/check-runs":
                payload = {"check_runs": [{"name": "ci", "status": check_status, "conclusion": check_conclusion}]}
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
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
        self.assertEqual(self.supervisor.calls, 0)

    def test_missing_reviewer_fails_closed(self) -> None:
        self.write_review("architect", "approve")
        self.write_review("tests", "comment")

        result = self.run_action()

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "WAIT_OR_REDISPATCH:missing_reviewers")
        self.assertEqual(self.actions.merged, [])

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

    def test_review_gate_uses_latest_round_per_role_after_partial_redispatch(self) -> None:
        stale = "b" * 40
        self.write_review("architect", "approve", head_sha=stale, round_number=1)
        self.write_review("tests", "approve", head_sha="a" * 40, round_number=1)
        self.write_review("quality", "comment", head_sha="a" * 40, round_number=1)
        self.write_review("architect", "reject", head_sha="a" * 40, round_number=2)

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            result = self.run_action()

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.actions.merged, [])
        self.assertEqual(self.actions.rendered, [(12, 1)])
        launch.assert_called_once()
        self.assertEqual(self.supervisor.calls, 0)

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

        result = self.run_action()

        self.assertEqual(result.status, "applied")
        self.assertEqual(self.actions.merged, ["12"])

    def test_ci_pending_or_failed_fails_closed_without_merge(self) -> None:
        for status, conclusion, reason in (
            ("queued", "", "ci_pending"),
            ("completed", "failure", "ci_failed"),
        ):
            with self.subTest(reason=reason):
                self.actions.merged.clear()
                self.write_review("architect", "approve")
                self.write_review("tests", "approve")
                self.write_review("quality", "comment")

                result = self.run_action(self.action(action_id=f"review:12:{reason}"), check_status=status, check_conclusion=conclusion)

                self.assertEqual(result.status, "blocked")
                self.assertEqual(result.reason, f"WAIT_OR_REDISPATCH:{reason}")
                self.assertEqual(self.actions.merged, [])

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

        result = self.run_action()

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "WAIT_OR_REDISPATCH:invalid_reviewer_evidence:invalid_verdict:quality")
        self.assertEqual(self.actions.merged, [])

    def test_reviewer_artifact_without_clean_exit_waits_without_merge(self) -> None:
        self.write_review("architect", "approve")
        self.write_review("tests", "approve")
        self.write_review("quality", "comment", exit_zero=False)

        result = self.run_action()

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "WAIT_OR_REDISPATCH:invalid_reviewer_evidence:missing_exit_zero:quality")
        self.assertEqual(self.actions.merged, [])

    def test_review_gate_source_locks_latest_evidence_per_role(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "wakeup_runner.py").read_text(encoding="utf-8")
        self.assertIn("def _latest_review_evidence_by_role(", source)
        self.assertIn("evidences = self._latest_review_evidence_by_role(pr_number)", source)
        self.assertIn("evidence.round_number > existing.round_number", source)

    def test_review_gate_source_does_not_treat_draft_as_mergeability_blocker(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "wakeup_runner.py").read_text(encoding="utf-8")
        method = source[source.index("    def _review_gate_mergeability_error") : source.index("    def _next_fix_round")]
        self.assertIn('"mergeable,isDraft"', method)
        self.assertNotIn("pr_draft", method)


if __name__ == "__main__":
    unittest.main()
