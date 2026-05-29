#!/usr/bin/env python3
"""Behavior tests for Python controller actions."""

from __future__ import annotations

import json
import shutil
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

    def test_apply_marker_rejects_unbounded_paths(self) -> None:
        self.assertEqual(2, self.actions.apply_dev_sync_request_marker("DEV_SYNC_REQUEST:/tmp/out.json"))
        self.assertEqual(2, self.actions.apply_triage_decision_marker("TRIAGE_DECISION_DONE:x:accept:/tmp/out.json"))

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
            pr_num, _url = self.actions.open_release_rollup_pr_from_pending_event(json.dumps(event), "body.md")

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
                        self.actions.open_release_rollup_pr_from_pending_event(event_json, "body.md")

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
                self.actions.open_release_rollup_pr_from_pending_event(json.dumps(event), "body.md")

        self.assertIn(["push", "origin", "abc123:refs/heads/rollup/abc123"], git_calls)
        self.assertFalse(any(call[:2] == ["pr", "create"] for call in gh_calls), gh_calls)


class ControllerActionsSourceRegressionTests(unittest.TestCase):
    def test_required_lifecycle_helpers_exist(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        for needle in ("merge_pr", "open_pr_with_label", "open_release_rollup_pr_from_pending_event", "safe_worktree", "record_recent_pr_merge", "apply_dev_sync_request_marker", "apply_triage_decision_marker", "render_template"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_rollup_helper_uses_throwaway_head_ref(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        self.assertIn('rollup_head = f"rollup/{integration_sha}"', text)
        self.assertIn('f"{integration_sha}:refs/heads/{rollup_head}"', text)
        self.assertNotIn('head=integration_branch', text)


if __name__ == "__main__":
    unittest.main()
