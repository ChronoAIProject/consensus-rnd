#!/usr/bin/env python3
"""Behavior tests for Python controller actions."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.controller_actions import ControllerActions


class ControllerActionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="controller-actions-test-"))
        (self.tmp / ".refactor-loop" / "state").mkdir(parents=True)
        (self.tmp / ".refactor-loop" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\n',
            encoding="utf-8",
        )
        self.actions = ControllerActions(LoopContext.load(repo_root=self.tmp))
        self.pr_body = self.tmp / "pr-body.md"
        self.pr_body.write_text("## 🤖 PR ready\n\n自包含正文。\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_recent_pr_merge_writes_rolling_artifact(self) -> None:
        facts = {
            "number": 7,
            "mergedAt": "2026-05-29T00:00:00Z",
            "mergeCommit": {"oid": "abc123"},
            "baseRefName": "dev",
            "headRefName": "feature",
        }
        with mock.patch.object(self.actions, "gh", return_value=mock.Mock(returncode=0, stdout=json.dumps(facts), stderr="")):
            self.actions.record_recent_pr_merge("7")
        data = json.loads((self.tmp / ".refactor-loop" / "state" / "recent-pr-merges.json").read_text(encoding="utf-8"))
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["merges"][0]["sha"], "abc123")

    def test_triage_apply_marker_rejects_unbounded_paths(self) -> None:
        self.assertEqual(2, self.actions.apply_triage_decision_marker("TRIAGE_DECISION_DONE:x:accept:/tmp/out.json"))

    def test_triage_apply_marker_accepts_valid_marker_through_internal_apply_path(self) -> None:
        runs = self.tmp / ".refactor-loop" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        comment = runs / "triage-comment.md"
        comment.write_text("comment\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        decision = runs / "triage-issue-53.json"
        decision.write_text(
            json.dumps(
                {
                    "schema": "ManualIssueTriageDecision",
                    "issue_number": 53,
                    "verdict": "reject",
                    "body_artifact_path": "",
                    "comment_artifact_path": ".refactor-loop/runs/triage-comment.md",
                    "add_labels": [],
                    "remove_labels": ["auto-loop-triage"],
                    "sentinel_present": True,
                    "lifecycle_owner": "controller",
                    "lifecycle_authority": False,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        calls: list[list[str]] = []

        def fake_gh(args: list[str], *, repo: Path, repo_slug: str | None = None) -> subprocess.CompletedProcess[str]:
            self.assertEqual(self.tmp.resolve(), repo)
            self.assertEqual("owner/repo", repo_slug)
            calls.append(args)
            return subprocess.CompletedProcess(["gh", *args], 0, "", "")

        marker = "TRIAGE_DECISION_DONE:53:reject:.refactor-loop/runs/triage-issue-53.json"
        with mock.patch("codex_refactor_loop.triage.current_labels", lambda _config, _issue: ["auto-loop-triage"]):
            with mock.patch("codex_refactor_loop.triage.run_gh", fake_gh):
                with mock.patch(
                    "codex_refactor_loop.controller_actions.subprocess.run",
                    side_effect=AssertionError("controller marker path must not shell through apply-triage"),
                ):
                    self.assertEqual(0, self.actions.apply_triage_decision_marker(marker))

        self.assertEqual(
            calls,
            [
                ["issue", "comment", "53", "--body-file", str(comment.resolve())],
                ["issue", "edit", "53", "--remove-label", "auto-loop-triage"],
            ],
        )
        applied = json.loads(
            (runs / "triage-decisions-applied" / "triage-issue-53.applied.json").read_text(encoding="utf-8")
        )
        self.assertEqual("applied", applied["status"])
        self.assertEqual("reject", applied["reason"])

    def test_open_release_rollup_pr_uses_throwaway_head_and_preserves_integration_ref(self) -> None:
        event = {
            "integration_branch": "auto-refact-dev",
            "review_base_branch": "dev",
            "integration_sha": "abc123",
        }
        git_calls: list[list[str]] = []
        gh_calls: list[list[str]] = []

        def fake_git(args: list[str], *, check: bool = True) -> mock.Mock:
            git_calls.append(args)
            if args[:4] == ["ls-remote", "--exit-code", "--heads", "origin"]:
                return mock.Mock(returncode=0, stdout="abc123\trefs/heads/auto-refact-dev\n", stderr="")
            if args == ["push", "origin", "abc123:refs/heads/rollup/abc123"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            return mock.Mock(returncode=1, stdout="", stderr="unexpected git call")

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:2] == ["pr", "create"]:
                return mock.Mock(returncode=0, stdout="https://github.com/owner/repo/pull/77\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.actions, "git", side_effect=fake_git), mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            pr_num, _url = self.actions.open_release_rollup_pr_from_pending_event(json.dumps(event), str(self.pr_body))

        self.assertEqual(77, pr_num)
        self.assertIn(["push", "origin", "abc123:refs/heads/rollup/abc123"], git_calls)
        create_call = next(call for call in gh_calls if call[:2] == ["pr", "create"])
        self.assertIn("--head", create_call)
        self.assertEqual("rollup/abc123", create_call[create_call.index("--head") + 1])
        self.assertNotEqual("auto-refact-dev", create_call[create_call.index("--head") + 1])

    def test_open_release_rollup_pr_fails_closed_before_push_or_pr_create(self) -> None:
        cases = (
            ("invalid-json", "{not-json", []),
            ("non-object", "[]", []),
            ("missing-sha", json.dumps({"integration_branch": "auto-refact-dev", "review_base_branch": "dev"}), []),
            (
                "unsafe-sha",
                json.dumps({"integration_branch": "auto-refact-dev", "review_base_branch": "dev", "integration_sha": "abc/123"}),
                [],
            ),
            (
                "missing-remote-branch",
                json.dumps({"integration_branch": "auto-refact-dev", "review_base_branch": "dev", "integration_sha": "abc123"}),
                [mock.Mock(returncode=2, stdout="", stderr="not found")],
            ),
            (
                "stale-sha",
                json.dumps({"integration_branch": "auto-refact-dev", "review_base_branch": "dev", "integration_sha": "abc123"}),
                [mock.Mock(returncode=0, stdout="def456\trefs/heads/auto-refact-dev\n", stderr="")],
            ),
        )
        for name, event_json, git_results in cases:
            with self.subTest(name=name):
                git_calls: list[list[str]] = []
                gh_calls: list[list[str]] = []

                def fake_git(args: list[str], *, check: bool = True) -> mock.Mock:
                    git_calls.append(args)
                    if git_results:
                        return git_results.pop(0)
                    return mock.Mock(returncode=1, stdout="", stderr="unexpected git call")

                def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
                    gh_calls.append(args)
                    return mock.Mock(returncode=0, stdout="", stderr="")

                with mock.patch.object(self.actions, "git", side_effect=fake_git), mock.patch.object(self.actions, "gh", side_effect=fake_gh):
                    with self.assertRaises(RuntimeError):
                        self.actions.open_release_rollup_pr_from_pending_event(event_json, str(self.pr_body))

                self.assertFalse(any(call[:1] == ["push"] for call in git_calls), git_calls)
                self.assertFalse(any(call[:2] == ["pr", "create"] for call in gh_calls), gh_calls)

    def test_open_release_rollup_pr_rejects_bad_body_before_git_push_or_pr_create(self) -> None:
        event = {
            "integration_branch": "auto-refact-dev",
            "review_base_branch": "dev",
            "integration_sha": "abc123",
        }
        bad_body = self.tmp / "bad-rollup-body.md"
        bad_body.write_text("## 🤖 rollup\n\n授权:.refactor-loop/runs/phase9-issue192-r1-judge.md\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        git_calls: list[list[str]] = []
        gh_calls: list[list[str]] = []

        def fake_git(args: list[str], *, check: bool = True) -> mock.Mock:
            git_calls.append(args)
            return mock.Mock(returncode=1, stdout="", stderr="unexpected git call")

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.actions, "git", side_effect=fake_git), mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            with self.assertRaisesRegex(RuntimeError, "local .refactor-loop artifact path"):
                self.actions.open_release_rollup_pr_from_pending_event(json.dumps(event), str(bad_body))

        self.assertFalse(any(call[:1] == ["push"] for call in git_calls), git_calls)
        self.assertFalse(any(call[:2] == ["pr", "create"] for call in gh_calls), gh_calls)

    def test_open_release_rollup_pr_failed_push_does_not_create_pr(self) -> None:
        event = {
            "integration_branch": "auto-refact-dev",
            "review_base_branch": "dev",
            "integration_sha": "abc123",
        }
        git_calls: list[list[str]] = []
        gh_calls: list[list[str]] = []

        def fake_git(args: list[str], *, check: bool = True) -> mock.Mock:
            git_calls.append(args)
            if args[:4] == ["ls-remote", "--exit-code", "--heads", "origin"]:
                return mock.Mock(returncode=0, stdout="abc123\trefs/heads/auto-refact-dev\n", stderr="")
            if args == ["push", "origin", "abc123:refs/heads/rollup/abc123"]:
                return mock.Mock(returncode=1, stdout="", stderr="push failed")
            return mock.Mock(returncode=1, stdout="", stderr="unexpected git call")

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.actions, "git", side_effect=fake_git), mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            with self.assertRaisesRegex(RuntimeError, "push failed"):
                self.actions.open_release_rollup_pr_from_pending_event(json.dumps(event), str(self.pr_body))

        self.assertIn(["push", "origin", "abc123:refs/heads/rollup/abc123"], git_calls)
        self.assertFalse(any(call[:2] == ["pr", "create"] for call in gh_calls), gh_calls)

    def test_open_pr_with_label_fails_closed_before_create_for_path_only_authority(self) -> None:
        bad_body = self.tmp / "bad-pr-body.md"
        bad_body.write_text("## 🤖 PR ready\n\n授权:.refactor-loop/runs/phase9-issue192-r1-judge.md\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            return mock.Mock(returncode=0, stdout="https://github.com/owner/repo/pull/77\n", stderr="")

        with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            with self.assertRaisesRegex(RuntimeError, "local .refactor-loop artifact path"):
                self.actions.open_pr_with_label("title", str(bad_body), head="refactor/branch")

        self.assertFalse(any(call[:2] == ["pr", "create"] for call in gh_calls), gh_calls)

    def test_open_pr_with_label_accepts_self_contained_body(self) -> None:
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:2] == ["pr", "create"]:
                return mock.Mock(returncode=0, stdout="https://github.com/owner/repo/pull/77\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            pr_num, _url = self.actions.open_pr_with_label("title", str(self.pr_body), head="refactor/branch")

        self.assertEqual(77, pr_num)
        self.assertTrue(any(call[:2] == ["pr", "create"] for call in gh_calls), gh_calls)


class ControllerActionsSourceRegressionTests(unittest.TestCase):
    def test_required_lifecycle_helpers_exist(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        for needle in ("merge_pr", "open_pr_with_label", "open_release_rollup_pr_from_pending_event", "safe_worktree", "record_recent_pr_merge", "apply_triage_decision_marker", "render_template"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        self.assertIn("validate_self_contained_github_body", text)

    def test_legacy_dev_sync_request_controller_apply_is_removed(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        self.assertNotIn("apply_dev_sync_request_marker", text)
        self.assertNotIn("DEV_SYNC_REQUEST:", text)
        self.assertNotIn("apply-sync", text)

    def test_rollup_helper_uses_throwaway_head_ref(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        self.assertIn('rollup_head = f"rollup/{integration_sha}"', text)
        self.assertIn('f"{integration_sha}:refs/heads/{rollup_head}"', text)
        self.assertNotIn('head=integration_branch', text)


if __name__ == "__main__":
    unittest.main()
