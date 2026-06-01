#!/usr/bin/env python3
"""Behavior tests for Python controller actions."""

from __future__ import annotations

import json
import ast
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import labels
from codex_refactor_loop.banners import BannerRequest
from codex_refactor_loop.cli import COMMANDS
from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.controller_actions import ControllerActions
from codex_refactor_loop.git import Git
from codex_refactor_loop.release.publisher import ReleasePublishResult


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
        self.pr_body.write_text("## 🤖 PR ready\n\nSelf-contained body.\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")

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

    def test_branch_configuration_ignores_legacy_alias_env(self) -> None:
        with mock.patch.dict(os.environ, {"INTEGRATION": "legacy-integration", "REVIEW_BASE": "legacy-review"}, clear=True):
            actions = ControllerActions(LoopContext.load(repo_root=self.tmp, env={}, cwd=self.tmp))

        self.assertEqual("auto-refact-dev", actions.integration_branch)
        self.assertEqual("dev", actions.review_base_branch)

    def test_branch_configuration_prefers_host_env_canonical_over_legacy_env(self) -> None:
        (self.tmp / ".refactor-loop" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\n'
            'export INTEGRATION_BRANCH="canonical-integration"\n'
            'export REVIEW_BASE_BRANCH="canonical-review"\n',
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, {"INTEGRATION": "legacy-integration", "REVIEW_BASE": "legacy-review"}, clear=True):
            actions = ControllerActions(LoopContext.load(repo_root=self.tmp, env={}, cwd=self.tmp))

        self.assertEqual("canonical-integration", actions.integration_branch)
        self.assertEqual("canonical-review", actions.review_base_branch)

    def test_pr_open_helpers_do_not_use_legacy_branch_alias_values(self) -> None:
        body = self.tmp / "body.md"
        body.write_text("PR body.\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"INTEGRATION": "legacy-integration", "REVIEW_BASE": "legacy-review"}, clear=True):
            actions = ControllerActions(LoopContext.load(repo_root=self.tmp, env={}, cwd=self.tmp))
        gh_calls: list[list[str]] = []
        git_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:2] == ["pr", "create"]:
                return mock.Mock(returncode=0, stdout="https://github.com/owner/repo/pull/77\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        def fake_git(args: list[str], *, check: bool = True) -> mock.Mock:
            git_calls.append(args)
            if args[:3] == ["ls-remote", "--exit-code", "--heads"]:
                return mock.Mock(returncode=0, stdout="abc123\trefs/heads/auto-refact-dev\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(actions, "_require_owner_or_raise", return_value=None):
            with mock.patch.object(actions, "gh", side_effect=fake_gh), mock.patch.object(actions, "git", side_effect=fake_git):
                actions.open_pr_with_label("Title", str(body), head="feature")
                actions.open_release_rollup_pr_from_pending_event(json.dumps({"integration_sha": "abc123"}), str(body))

        pr_creates = [call for call in gh_calls if call[:2] == ["pr", "create"]]
        self.assertEqual("auto-refact-dev", pr_creates[0][pr_creates[0].index("--base") + 1])
        self.assertEqual("dev", pr_creates[1][pr_creates[1].index("--base") + 1])
        self.assertIn(["ls-remote", "--exit-code", "--heads", "origin", "auto-refact-dev"], git_calls)
        self.assertFalse(any("legacy-" in " ".join(call) for call in gh_calls + git_calls))

    def pending_events(self) -> str:
        path = self.tmp / ".refactor-loop" / ".controller-pending-events.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def banner_request(self, **overrides: object) -> BannerRequest:
        values = {
            "target": "77",
            "kind": "pr",
            "role": "implement",
            "detail": "issue-371",
            "log": "/tmp/implement-371.log",
            "cd": "/repo/.worktrees/iter371-issue371",
            "stall": 5400,
        }
        values.update(overrides)
        return BannerRequest(**values)

    def test_post_status_banner_owner_posts_after_active_controller_gate(self) -> None:
        gh_calls: list[list[str]] = []
        captured_body = ""

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            nonlocal captured_body
            gh_calls.append(args)
            body_path = Path(args[-1])
            captured_body = body_path.read_text(encoding="utf-8")
            return mock.Mock(returncode=0, stdout="https://github.com/owner/repo/pull/77#issuecomment-1\n", stderr="")

        decision = mock.Mock(
            allowed=True,
            owner_device="device-a",
            status="owner",
            action="post-banner",
            lease_id="lease-1",
            expires_at="2026-06-01T00:00:00Z",
        )
        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
                url = self.actions.post_status_banner(self.banner_request())

        self.assertEqual(url, "https://github.com/owner/repo/pull/77#issuecomment-1")
        self.assertEqual(gh_calls[0][:3], ["pr", "comment", "77"])
        self.assertEqual(gh_calls[0][-2], "--body-file")
        self.assertFalse(Path(gh_calls[0][-1]).exists())
        self.assertIn("⟦AI:AUTO-LOOP⟧", captured_body)
        status = json.loads((self.tmp / ".refactor-loop" / "state" / "active-controller-status.json").read_text(encoding="utf-8"))
        self.assertEqual("owner", status["active_controller"])
        self.assertEqual("post-banner", status["action"])

    def test_post_status_banner_gh_failure_reports_output_and_removes_tempfile(self) -> None:
        cases = (
            ("stderr", "permission denied\n", "", "permission denied"),
            ("stdout", "", "api unavailable\n", "api unavailable"),
        )
        for label, stderr, stdout, expected in cases:
            with self.subTest(label=label):
                gh_calls: list[list[str]] = []
                body_path: Path | None = None

                def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
                    nonlocal body_path
                    gh_calls.append(args)
                    body_path = Path(args[-1])
                    self.assertTrue(body_path.exists())
                    return mock.Mock(returncode=1, stdout=stdout, stderr=stderr)

                decision = mock.Mock(
                    allowed=True,
                    owner_device="device-a",
                    status="owner",
                    action="post-banner",
                    lease_id="lease-1",
                    expires_at="2026-06-01T00:00:00Z",
                )
                with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
                    with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
                        with self.assertRaisesRegex(RuntimeError, f"post_status_banner: {re.escape(expected)}"):
                            self.actions.post_status_banner(self.banner_request())

                self.assertEqual(1, len(gh_calls))
                self.assertIsNotNone(body_path)
                assert body_path is not None
                self.assertFalse(body_path.exists())

    def test_post_status_banner_non_owner_does_not_call_gh_or_create_tempfile(self) -> None:
        decision = mock.Mock(
            allowed=False,
            owner_device="device-a",
            status="not-owner",
            action="post-banner",
            lease_id="lease-1",
            expires_at="2026-06-01T00:00:00Z",
        )
        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch("codex_refactor_loop.controller_actions.tempfile.NamedTemporaryFile", side_effect=AssertionError("tempfile should not be created")):
                with mock.patch.object(self.actions, "gh", side_effect=AssertionError("gh should not be called")):
                    with self.assertRaisesRegex(RuntimeError, "active_controller=noop:not-owner action=post-banner"):
                        self.actions.post_status_banner(self.banner_request())

        status = json.loads((self.tmp / ".refactor-loop" / "state" / "active-controller-status.json").read_text(encoding="utf-8"))
        self.assertEqual("noop:not-owner", status["active_controller"])
        self.assertFalse((self.tmp / ".refactor-loop" / ".controller-pending-events.log").exists())

    def test_post_status_banner_invalid_target_blocks_before_tempfile_or_gh(self) -> None:
        invalid_targets = ("", "0", "01", "https://github.com/owner/repo/pull/77", "refactor/branch")
        for target in invalid_targets:
            with self.subTest(target=target):
                (self.tmp / ".refactor-loop" / ".controller-pending-events.log").unlink(missing_ok=True)
                with mock.patch("codex_refactor_loop.controller_actions.tempfile.NamedTemporaryFile", side_effect=AssertionError("tempfile should not be created")):
                    with mock.patch.object(self.actions, "gh", side_effect=AssertionError("gh should not be called")):
                        with self.assertRaisesRegex(RuntimeError, "post-banner: invalid pr target from argument"):
                            self.actions.post_status_banner(self.banner_request(target=target))
                self.assertIn(
                    "CONTROLLER_ACTION_BLOCKED:invalid-github-target:post-banner:pr:argument",
                    self.pending_events(),
                )

    # Refactor (iter276/issue-276): Old pattern: controller lifecycle targets
    # accepted empty or non-canonical GitHub ids before gh calls. New principle:
    # fail closed on non-canonical GitHub ids and leave a pending-event audit.
    def test_merge_pr_rejects_invalid_pr_targets_before_gh_or_git(self) -> None:
        invalid_targets = ("", " ", "0", "-1", "abc", "01", "https://github.com/owner/repo/pull/77")
        for target in invalid_targets:
            with self.subTest(target=target):
                (self.tmp / ".refactor-loop" / ".controller-pending-events.log").unlink(missing_ok=True)
                with mock.patch.object(self.actions, "gh", side_effect=AssertionError("gh should not be called")):
                    with mock.patch.object(self.actions, "git", side_effect=AssertionError("git should not be called")):
                        self.assertEqual(1, self.actions.merge_pr(target))
                self.assertIn(
                    "CONTROLLER_ACTION_BLOCKED:invalid-github-target:merge-pr:pr:argument",
                    self.pending_events(),
                )

    def test_apply_human_label_rejects_invalid_pr_targets_before_gh_or_git(self) -> None:
        with mock.patch.object(self.actions, "gh", side_effect=AssertionError("gh should not be called")):
            with mock.patch.object(self.actions, "git", side_effect=AssertionError("git should not be called")):
                self.assertEqual(2, self.actions.apply_human_label_or_skip("01", "META_RESOLVED:escalate-human:reason"))
        self.assertIn(
            "CONTROLLER_ACTION_BLOCKED:invalid-github-target:apply-human-label:pr:argument",
            self.pending_events(),
        )

    def test_merge_pr_rejects_invalid_linked_issue_before_gh_or_git(self) -> None:
        with mock.patch.object(self.actions, "gh", side_effect=AssertionError("gh should not be called")):
            with mock.patch.object(self.actions, "git", side_effect=AssertionError("git should not be called")):
                self.assertEqual(1, self.actions.merge_pr("77", linked_issue="01"))
        self.assertIn(
            "CONTROLLER_ACTION_BLOCKED:invalid-github-target:merge-pr:issue:argument",
            self.pending_events(),
        )

    def test_merge_pr_rejects_invalid_body_linked_issue_before_merge_or_label_edit(self) -> None:
        cases = ("Closes #abc\n", "Closes #\n", "Closes #0\n", "Closes #01\n")
        for body in cases:
            with self.subTest(body=body.strip()):
                (self.tmp / ".refactor-loop" / ".controller-pending-events.log").unlink(missing_ok=True)
                gh_calls: list[list[str]] = []

                def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
                    gh_calls.append(args)
                    if args[:5] == ["pr", "view", "77", "--json", "body"]:
                        return mock.Mock(returncode=0, stdout=body, stderr="")
                    raise AssertionError(f"unexpected gh side effect after invalid body link: {args}")

                with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
                    with mock.patch.object(self.actions, "git", side_effect=AssertionError("git should not be called")):
                        self.assertEqual(1, self.actions.merge_pr("77"))

                self.assertEqual([["pr", "view", "77", "--json", "body", "--jq", ".body"]], gh_calls)
                self.assertIn(
                    "CONTROLLER_ACTION_BLOCKED:invalid-github-target:close:issue:body-link",
                    self.pending_events(),
                )

    def test_merge_pr_accepts_valid_explicit_linked_issue_target(self) -> None:
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:2] == ["pr", "merge"]:
                return mock.Mock(returncode=0, stdout="Merged pull request #77\n", stderr="")
            if args[:5] == ["pr", "view", "77", "--json", "number,mergedAt,mergeCommit,baseRefName,headRefName"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "number": 77,
                            "mergedAt": "2026-05-29T00:00:00Z",
                            "mergeCommit": {"oid": "abc123"},
                            "baseRefName": "dev",
                            "headRefName": "impl/issue239",
                        }
                    ),
                    stderr="",
                )
            if args[:5] == ["pr", "view", "77", "--json", "headRefName"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            self.assertEqual(0, self.actions.merge_pr("77", linked_issue="239"))

        ready_index = gh_calls.index(["pr", "view", "77", "--json", "isDraft", "--jq", ".isDraft"])
        merge_index = gh_calls.index(["pr", "merge", "77", "--squash", "--delete-branch"])
        self.assertLess(ready_index, merge_index)
        self.assertIn(["pr", "merge", "77", "--squash", "--delete-branch"], gh_calls)
        self.assertFalse(any(call[:5] == ["pr", "view", "77", "--json", "body"] for call in gh_calls), gh_calls)
        self.assertTrue(any(call[:3] == ["pr", "edit", "77"] for call in gh_calls), gh_calls)
        self.assertTrue(any(call[:3] == ["issue", "close", "239"] for call in gh_calls), gh_calls)
        issue_edit = next(call for call in gh_calls if call[:3] == ["issue", "edit", "239"])
        self.assertEqual(labels.PHASE_MERGED, issue_edit[issue_edit.index("--add-label") + 1])

    def test_merge_pr_marks_draft_ready_before_merge(self) -> None:
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:5] == ["pr", "view", "77", "--json", "body"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            if args[:5] == ["pr", "view", "77", "--json", "isDraft"]:
                return mock.Mock(returncode=0, stdout="true\n", stderr="")
            if args[:3] == ["pr", "ready", "77"]:
                return mock.Mock(returncode=0, stdout="Ready\n", stderr="")
            if args[:2] == ["pr", "merge"]:
                return mock.Mock(returncode=0, stdout="Merged pull request #77\n", stderr="")
            if args[:5] == ["pr", "view", "77", "--json", "number,mergedAt,mergeCommit,baseRefName,headRefName"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "number": 77,
                            "mergedAt": "2026-05-29T00:00:00Z",
                            "mergeCommit": {"oid": "abc123"},
                            "baseRefName": "dev",
                            "headRefName": "impl/issue300",
                        }
                    ),
                    stderr="",
                )
            if args[:5] == ["pr", "view", "77", "--json", "headRefName"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            self.assertEqual(0, self.actions.merge_pr("77"))

        self.assertIn(["pr", "ready", "77"], gh_calls)
        self.assertLess(gh_calls.index(["pr", "ready", "77"]), gh_calls.index(["pr", "merge", "77", "--squash", "--delete-branch"]))

    def test_merge_pr_ready_failure_fails_closed_before_merge_side_effects(self) -> None:
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:5] == ["pr", "view", "77", "--json", "body"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            if args[:5] == ["pr", "view", "77", "--json", "isDraft"]:
                return mock.Mock(returncode=0, stdout="true\n", stderr="")
            if args[:3] == ["pr", "ready", "77"]:
                return mock.Mock(returncode=9, stdout="", stderr="ready failed")
            raise AssertionError(f"unexpected gh side effect after ready failure: {args}")

        with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            with mock.patch.object(self.actions, "git", side_effect=AssertionError("git should not be called")):
                self.assertEqual(9, self.actions.merge_pr("77"))

        self.assertIn(["pr", "ready", "77"], gh_calls)
        self.assertFalse(any(call[:2] == ["pr", "merge"] for call in gh_calls), gh_calls)
        self.assertFalse((self.tmp / ".refactor-loop" / "state" / "recent-pr-merges.json").exists())

    def test_merge_pr_failure_surfaces_blocked_by_host_policy_without_cleanup(self) -> None:
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:5] == ["pr", "view", "77", "--json", "body"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            if args[:5] == ["pr", "view", "77", "--json", "isDraft"]:
                return mock.Mock(returncode=0, stdout="false\n", stderr="")
            if args[:2] == ["pr", "merge"]:
                return mock.Mock(returncode=9, stdout="", stderr="merge blocked by host policy")
            raise AssertionError(f"unexpected gh side effect after merge failure: {args}")

        with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            with mock.patch.object(self.actions, "git", side_effect=AssertionError("git should not be called")):
                self.assertEqual(9, self.actions.merge_pr("77"))

        self.assertIn(["pr", "merge", "77", "--squash", "--delete-branch"], gh_calls)
        self.assertFalse(any(call[:2] == ["pr", "edit"] for call in gh_calls), gh_calls)
        self.assertFalse((self.tmp / ".refactor-loop" / "state" / "recent-pr-merges.json").exists())
        self.assertIn("CONTROLLER_ACTION_BLOCKED:blocked-by-host-policy:merge-pr:pr:77", self.pending_events())

    def test_merge_pr_already_ready_merges_without_pr_ready_call(self) -> None:
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:5] == ["pr", "view", "77", "--json", "body"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            if args[:5] == ["pr", "view", "77", "--json", "isDraft"]:
                return mock.Mock(returncode=0, stdout="false\n", stderr="")
            if args[:2] == ["pr", "merge"]:
                return mock.Mock(returncode=0, stdout="Merged pull request #77\n", stderr="")
            if args[:5] == ["pr", "view", "77", "--json", "number,mergedAt,mergeCommit,baseRefName,headRefName"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "number": 77,
                            "mergedAt": "2026-05-29T00:00:00Z",
                            "mergeCommit": {"oid": "abc123"},
                            "baseRefName": "dev",
                            "headRefName": "impl/issue300",
                        }
                    ),
                    stderr="",
                )
            if args[:5] == ["pr", "view", "77", "--json", "headRefName"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            self.assertEqual(0, self.actions.merge_pr("77"))

        self.assertIn(["pr", "view", "77", "--json", "isDraft", "--jq", ".isDraft"], gh_calls)
        self.assertFalse(any(call[:2] == ["pr", "ready"] for call in gh_calls), gh_calls)
        self.assertIn(["pr", "merge", "77", "--squash", "--delete-branch"], gh_calls)

    def test_open_pr_with_label_rejects_malformed_create_url_before_post_create_edit(self) -> None:
        cases = (
            ("missing-url", "created pull request 77\n", "failed to extract PR num", False),
            ("zero-pr", "https://github.com/owner/repo/pull/0\n", "invalid pr target", True),
            ("leading-zero-pr", "https://github.com/owner/repo/pull/077\n", "invalid pr target", True),
        )
        for name, output, expected_error, expects_invalid_target_event in cases:
            with self.subTest(name=name):
                (self.tmp / ".refactor-loop" / ".controller-pending-events.log").unlink(missing_ok=True)
                gh_calls: list[list[str]] = []

                def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
                    gh_calls.append(args)
                    if args[:2] == ["pr", "create"]:
                        return mock.Mock(returncode=0, stdout=output, stderr="")
                    raise AssertionError(f"unexpected post-create gh call: {args}")

                with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
                    with self.assertRaisesRegex(RuntimeError, expected_error):
                        self.actions.open_pr_with_label("title", str(self.pr_body), head="refactor/branch")

                self.assertEqual(1, sum(1 for call in gh_calls if call[:2] == ["pr", "create"]))
                self.assertFalse(any(call[:2] == ["pr", "edit"] for call in gh_calls), gh_calls)
                invalid_target_event = "CONTROLLER_ACTION_BLOCKED:invalid-github-target:open-pr:pr:github-pr-create-url"
                if expects_invalid_target_event:
                    self.assertIn(invalid_target_event, self.pending_events())
                else:
                    self.assertNotIn(invalid_target_event, self.pending_events())

    def test_record_recent_pr_merge_rejects_invalid_argument_before_gh_or_projection(self) -> None:
        with mock.patch.object(self.actions, "gh", side_effect=AssertionError("gh should not be called")):
            with self.assertRaisesRegex(RuntimeError, "invalid pr target"):
                self.actions.record_recent_pr_merge("01")
        self.assertFalse((self.tmp / ".refactor-loop" / "state" / "recent-pr-merges.json").exists())
        self.assertIn(
            "CONTROLLER_ACTION_BLOCKED:invalid-github-target:record-recent-pr-merge:pr:argument",
            self.pending_events(),
        )

    def test_record_recent_pr_merge_rejects_invalid_github_fact_number_before_projection(self) -> None:
        facts = {
            "number": "01",
            "mergedAt": "2026-05-29T00:00:00Z",
            "mergeCommit": {"oid": "abc123"},
            "baseRefName": "dev",
            "headRefName": "feature",
        }
        with mock.patch.object(self.actions, "gh", return_value=mock.Mock(returncode=0, stdout=json.dumps(facts), stderr="")):
            with self.assertRaisesRegex(RuntimeError, "invalid pr target"):
                self.actions.record_recent_pr_merge("7")
        self.assertFalse((self.tmp / ".refactor-loop" / "state" / "recent-pr-merges.json").exists())
        self.assertIn(
            "CONTROLLER_ACTION_BLOCKED:invalid-github-target:record-recent-pr-merge:pr:github-facts",
            self.pending_events(),
        )

    # Refactor (impl/issue191-single-active-controller): Old pattern:
    # lifecycle helpers could mutate GitHub/git from any controller device. New
    # principle: non-owner helpers fail closed before gh/git mutations.
    def test_non_owner_lifecycle_helpers_fail_closed_before_gh_or_git(self) -> None:
        decision = mock.Mock(allowed=False, owner_device="device-a", status="not-owner", action="controller", lease_id="", expires_at="")
        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch.object(self.actions, "gh", side_effect=AssertionError("gh mutation should not be called")):
                with mock.patch.object(self.actions, "git", side_effect=AssertionError("git mutation should not be called")):
                    self.assertEqual(3, self.actions.merge_pr("7"))
                    self.assertEqual(3, self.actions.safe_push())
                    self.assertEqual(3, self.actions.safe_sync_main())
                    self.assertEqual(3, self.actions.apply_human_label_or_skip("7", "META_RESOLVED:escalate-human:reason"))
                    self.assertEqual(3, self.actions.apply_triage_decision_marker("TRIAGE_DECISION_DONE:53:reject:.refactor-loop/runs/x.json"))
                    with self.assertRaisesRegex(RuntimeError, "active_controller=noop:not-owner"):
                        self.actions.open_pr_with_label("title", str(self.pr_body), head="branch")
                    with self.assertRaisesRegex(RuntimeError, "active_controller=noop:not-owner"):
                        self.actions.open_design_issue_with_labels("title", str(self.pr_body))
                    with self.assertRaisesRegex(RuntimeError, "active_controller=noop:not-owner"):
                        self.actions.open_release_rollup_pr_from_pending_event("{}", str(self.pr_body))
                    with self.assertRaisesRegex(RuntimeError, "active_controller=noop:not-owner"):
                        self.actions.publish_release_candidate(target_ref="abc")

        status = json.loads((self.tmp / ".refactor-loop" / "state" / "active-controller-status.json").read_text(encoding="utf-8"))
        self.assertEqual("noop:not-owner", status["active_controller"])

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
                    "remove_labels": [labels.TRIAGE_PENDING],
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
        self.assertIn("--draft", create_call)
        self.assertIn("--head", create_call)
        self.assertEqual("rollup/abc123", create_call[create_call.index("--head") + 1])
        self.assertNotEqual("auto-refact-dev", create_call[create_call.index("--head") + 1])

    def test_safe_worktree_rejects_unsafe_iteration_and_cluster_fields(self) -> None:
        cases = (
            ("x1", "issue-81"),
            ("1/2", "issue-81"),
            ("1", ""),
            ("1", "issue 81"),
            ("1", "issue/81"),
            ("1", "issue;81"),
            ("1", "issue$81"),
        )
        for iteration, cluster in cases:
            with self.subTest(iteration=iteration, cluster=cluster):
                with self.assertRaisesRegex(ValueError, "safe_worktree"):
                    self.actions.safe_worktree(iteration, cluster, "dev")

    def test_git_safe_worktree_rejects_unsafe_iteration_and_cluster_fields(self) -> None:
        git = Git(self.tmp)
        for iteration, cluster in (("x1", "issue-81"), ("1", "issue/81"), ("1", "issue 81")):
            with self.subTest(iteration=iteration, cluster=cluster):
                with self.assertRaisesRegex(ValueError, "safe_worktree"):
                    git.safe_worktree(iteration, cluster, "dev")

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
        bad_body.write_text("## 🤖 rollup\n\nAuthority: .refactor-loop/runs/phase9-issue192-r1-judge.md\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
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
        bad_body.write_text("## 🤖 PR ready\n\nAuthority: .refactor-loop/runs/phase9-issue192-r1-judge.md\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
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
        create_call = next(call for call in gh_calls if call[:2] == ["pr", "create"])
        self.assertIn("--draft", create_call)
        edit_call = next(call for call in gh_calls if call[:2] == ["pr", "edit"])
        self.assertEqual("77", edit_call[2])
        self.assertEqual(
            ",".join((labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO)),
            edit_call[edit_call.index("--add-label") + 1],
        )
        self.assertNotIn("auto-loop", edit_call)
        self.assertNotIn("🚀 phase:pr-open", edit_call)
        self.assertFalse(any(call[:2] == ["issue", "edit"] for call in gh_calls), gh_calls)

    def test_open_pr_with_label_moves_linked_parent_issue_to_pr_open(self) -> None:
        self.pr_body.write_text(
            "## 🤖 PR ready\n\nSelf-contained body.\n\nCloses #239\n\n⟦AI:AUTO-LOOP⟧\n",
            encoding="utf-8",
        )
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:2] == ["pr", "create"]:
                return mock.Mock(returncode=0, stdout="https://github.com/owner/repo/pull/77\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            pr_num, _url = self.actions.open_pr_with_label("title", str(self.pr_body), head="refactor/branch")

        self.assertEqual(77, pr_num)
        issue_edit = next(call for call in gh_calls if call[:3] == ["issue", "edit", "239"])
        removed = [issue_edit[index + 1] for index, value in enumerate(issue_edit) if value == "--remove-label"]
        self.assertIn(labels.PHASE_IMPLEMENTING, removed)
        self.assertIn(labels.HUMAN_MAINTAINER_DECISION, removed)
        self.assertIn(labels.STUCK, removed)
        self.assertEqual(
            ",".join((labels.PHASE_PR_OPEN, labels.HUMAN_AUTO, labels.MANAGED)),
            issue_edit[issue_edit.index("--add-label") + 1],
        )

    def write_design_issue_body(self) -> Path:
        body = self.tmp / "design-issue-body.md"
        body.write_text(
            "## 🤖 Design issue\n\n"
            "### TL;DR\n"
            "- Self-contained design body.\n\n"
            "<details>\n"
            "<summary>内联 artifact 1: decision.md</summary>\n\n"
            "```markdown\n"
            "consensus artifact text\n"
            "```\n\n"
            "</details>\n\n"
            "⟦AI:AUTO-LOOP⟧\n",
            encoding="utf-8",
        )
        return body

    def test_open_design_issue_with_labels_uses_catalog_bundle_and_body_file(self) -> None:
        body = self.write_design_issue_body()
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:2] == ["issue", "create"]:
                return mock.Mock(returncode=0, stdout="https://github.com/owner/repo/issues/297\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            number, url = self.actions.open_design_issue_with_labels("[refactor-design] issue-297", str(body))

        self.assertEqual(297, number)
        self.assertEqual("https://github.com/owner/repo/issues/297", url)
        self.assertEqual(len(gh_calls), 1)
        create = gh_calls[0]
        self.assertEqual(create[:2], ["issue", "create"])
        self.assertEqual(",".join(labels.design_issue_label_bundle()), create[create.index("--label") + 1])
        self.assertEqual(str(body), create[create.index("--body-file") + 1])

    def test_open_design_issue_with_labels_rejects_bad_body_before_create(self) -> None:
        bad_body = self.tmp / "bad-design-body.md"
        bad_body.write_text("## body\n\nAuthority: .refactor-loop/runs/x.md\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            return mock.Mock(returncode=0, stdout="https://github.com/owner/repo/issues/297\n", stderr="")

        with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            with self.assertRaisesRegex(RuntimeError, "local .refactor-loop artifact path"):
                self.actions.open_design_issue_with_labels("title", str(bad_body))

        self.assertFalse(gh_calls)

    def test_open_design_issue_with_labels_is_internal_not_public_cli(self) -> None:
        self.assertNotIn("open-design-issue", COMMANDS)

    def test_open_pr_with_label_does_not_guess_when_body_closes_multiple_issues(self) -> None:
        self.pr_body.write_text(
            "## 🤖 PR ready\n\nSelf-contained body.\n\nCloses #239\nCloses #240\n\n⟦AI:AUTO-LOOP⟧\n",
            encoding="utf-8",
        )
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:2] == ["pr", "create"]:
                return mock.Mock(returncode=0, stdout="https://github.com/owner/repo/pull/77\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            pr_num, _url = self.actions.open_pr_with_label("title", str(self.pr_body), head="refactor/branch")

        self.assertEqual(77, pr_num)
        self.assertFalse(any(call[:2] == ["issue", "edit"] for call in gh_calls), gh_calls)

    def test_open_pr_with_label_rejects_invalid_body_linked_issue_before_create(self) -> None:
        cases = ("Closes #abc\n", "Closes #\n", "Closes #0\n", "Closes #01\n")
        for body_link in cases:
            with self.subTest(body=body_link.strip()):
                (self.tmp / ".refactor-loop" / ".controller-pending-events.log").unlink(missing_ok=True)
                self.pr_body.write_text(
                    f"## 🤖 PR ready\n\nSelf-contained body.\n\n{body_link}\n⟦AI:AUTO-LOOP⟧\n",
                    encoding="utf-8",
                )
                with mock.patch.object(self.actions, "gh", side_effect=AssertionError("gh should not be called")):
                    with self.assertRaisesRegex(RuntimeError, "invalid issue target from body-link"):
                        self.actions.open_pr_with_label("title", str(self.pr_body), head="refactor/branch")
                self.assertIn(
                    "CONTROLLER_ACTION_BLOCKED:invalid-github-target:open-pr:issue:body-link",
                    self.pending_events(),
                )

    def test_merge_pr_closes_single_linked_issue_from_body(self) -> None:
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:5] == ["pr", "view", "77", "--json", "body"]:
                return mock.Mock(returncode=0, stdout="Ready.\n\nCloses #239\n", stderr="")
            if args[:2] == ["pr", "merge"]:
                return mock.Mock(returncode=0, stdout="Merged pull request #77\n", stderr="")
            if args[:5] == ["pr", "view", "77", "--json", "number,mergedAt,mergeCommit,baseRefName,headRefName"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "number": 77,
                            "mergedAt": "2026-05-29T00:00:00Z",
                            "mergeCommit": {"oid": "abc123"},
                            "baseRefName": "dev",
                            "headRefName": "impl/issue239",
                        }
                    ),
                    stderr="",
                )
            if args[:5] == ["pr", "view", "77", "--json", "headRefName"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            self.assertEqual(0, self.actions.merge_pr("77"))

        self.assertIn(["pr", "merge", "77", "--squash", "--delete-branch"], gh_calls)
        self.assertTrue(any(call[:3] == ["issue", "close", "239"] for call in gh_calls), gh_calls)
        issue_edit = next(call for call in gh_calls if call[:3] == ["issue", "edit", "239"])
        self.assertEqual(labels.PHASE_MERGED, issue_edit[issue_edit.index("--add-label") + 1])

    def test_merge_pr_accepts_body_link_with_escaped_newline_boundary(self) -> None:
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:5] == ["pr", "view", "77", "--json", "body"]:
                return mock.Mock(returncode=0, stdout="Ready.\n\nCloses #239\\n", stderr="")
            if args[:2] == ["pr", "merge"]:
                return mock.Mock(returncode=0, stdout="Merged pull request #77\n", stderr="")
            if args[:5] == ["pr", "view", "77", "--json", "number,mergedAt,mergeCommit,baseRefName,headRefName"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "number": 77,
                            "mergedAt": "2026-05-29T00:00:00Z",
                            "mergeCommit": {"oid": "abc123"},
                            "baseRefName": "dev",
                            "headRefName": "impl/issue239",
                        }
                    ),
                    stderr="",
                )
            if args[:5] == ["pr", "view", "77", "--json", "headRefName"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            self.assertEqual(0, self.actions.merge_pr("77"))

        self.assertIn(["pr", "merge", "77", "--squash", "--delete-branch"], gh_calls)
        self.assertTrue(any(call[:3] == ["issue", "close", "239"] for call in gh_calls), gh_calls)

    def test_merge_pr_does_not_guess_issue_when_body_closes_multiple_issues(self) -> None:
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:5] == ["pr", "view", "77", "--json", "body"]:
                return mock.Mock(returncode=0, stdout="Closes #239\nCloses #240\n", stderr="")
            if args[:2] == ["pr", "merge"]:
                return mock.Mock(returncode=0, stdout="Merged pull request #77\n", stderr="")
            if args[:5] == ["pr", "view", "77", "--json", "number,mergedAt,mergeCommit,baseRefName,headRefName"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "number": 77,
                            "mergedAt": "2026-05-29T00:00:00Z",
                            "mergeCommit": {"oid": "abc123"},
                            "baseRefName": "dev",
                            "headRefName": "impl/issue239",
                        }
                    ),
                    stderr="",
                )
            if args[:5] == ["pr", "view", "77", "--json", "headRefName"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            self.assertEqual(0, self.actions.merge_pr("77"))

        self.assertIn(["pr", "merge", "77", "--squash", "--delete-branch"], gh_calls)
        self.assertTrue(any(call[:3] == ["pr", "edit", "77"] for call in gh_calls), gh_calls)
        self.assertFalse(any(call[:2] == ["issue", "close"] for call in gh_calls), gh_calls)
        self.assertFalse(any(call[:2] == ["issue", "edit"] for call in gh_calls), gh_calls)

    def test_publish_release_candidate_requires_explicit_or_env_target_ref(self) -> None:
        with mock.patch.dict("codex_refactor_loop.controller_actions.os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "RELEASE_TARGET_REF is required"):
                self.actions.publish_release_candidate()

    def test_publish_release_candidate_uses_release_target_ref_env_when_omitted(self) -> None:
        result = ReleasePublishResult(
            published=True,
            reasons=(),
            tag="v2.0.0",
            target_ref="env-ref",
            version="2.0.0",
            release_url="https://github.test/release/v2.0.0",
            result_path=self.tmp / ".refactor-loop/state/release-publish-result.json",
        )
        publisher = mock.Mock()
        publisher.publish.return_value = result

        with mock.patch.dict("codex_refactor_loop.controller_actions.os.environ", {"RELEASE_TARGET_REF": "env-ref"}, clear=True):
            with mock.patch("codex_refactor_loop.controller_actions.ReleasePublisher", return_value=publisher) as publisher_type:
                actual = self.actions.publish_release_candidate()

        self.assertIs(actual, result)
        publisher_type.assert_called_once_with(self.actions.ctx.repo_root)
        publisher.publish.assert_called_once_with(
            candidate_path=".refactor-loop/state/release-candidate.json",
            target_ref="env-ref",
        )

    def test_publish_release_candidate_forwards_explicit_target_ref_and_candidate_path(self) -> None:
        result = ReleasePublishResult(
            published=True,
            reasons=(),
            tag="v2.0.0",
            target_ref="explicit-ref",
            version="2.0.0",
            release_url="https://github.test/release/v2.0.0",
            result_path=self.tmp / ".refactor-loop/state/release-publish-result.json",
        )
        publisher = mock.Mock()
        publisher.publish.return_value = result

        with mock.patch.dict("codex_refactor_loop.controller_actions.os.environ", {"RELEASE_TARGET_REF": "env-ref"}, clear=True):
            with mock.patch("codex_refactor_loop.controller_actions.ReleasePublisher", return_value=publisher) as publisher_type:
                actual = self.actions.publish_release_candidate(
                    candidate_path=".refactor-loop/state/custom-candidate.json",
                    target_ref="explicit-ref",
                )

        self.assertIs(actual, result)
        publisher_type.assert_called_once_with(self.actions.ctx.repo_root)
        publisher.publish.assert_called_once_with(
            candidate_path=".refactor-loop/state/custom-candidate.json",
            target_ref="explicit-ref",
        )

    def write_host_workflow_spec(self, data: dict) -> ControllerActions:
        (self.tmp / "workflow.json").write_text(json.dumps(data), encoding="utf-8")
        ctx = LoopContext.load(
            repo_root=self.tmp,
            env={"REPO_ROOT": str(self.tmp), "GH_REPO_SLUG": "owner/repo", "HOST_WORKFLOW_SPEC": "workflow.json"},
        )
        return ControllerActions(ctx)

    def valid_host_prompt_spec(self) -> dict:
        (self.tmp / "prompts").mkdir(exist_ok=True)
        (self.tmp / "prompts" / "host-render.md").write_text(
            "Host ${HOST_NAME} handles {{work_unit_id}} from {{cluster_id}}.\n",
            encoding="utf-8",
        )
        return {"prompt_bindings": {"host:render": "prompts/host-render.md"}}

    def test_render_template_resolves_host_prompt_binding_from_valid_workflow_spec(self) -> None:
        actions = self.write_host_workflow_spec(self.valid_host_prompt_spec())
        output = self.tmp / "rendered.md"

        actions.render_template(
            "host:render",
            str(output),
            env={"HOST_NAME": "example-host", "WORK_UNIT_ID": "issue-219", "CLUSTER_ID": "cluster-219"},
        )

        self.assertEqual(output.read_text(encoding="utf-8"), "Host example-host handles issue-219 from cluster-219.\n")

    def test_render_template_rejects_unknown_host_prompt_binding(self) -> None:
        actions = self.write_host_workflow_spec(self.valid_host_prompt_spec())
        output = self.tmp / "rendered.md"

        with self.assertRaisesRegex(RuntimeError, "unknown host prompt binding: host:missing"):
            actions.render_template("host:missing", str(output))

        self.assertFalse(output.exists())

    def test_render_template_rejects_invalid_host_workflow_spec(self) -> None:
        actions = self.write_host_workflow_spec({"prompt_bindings": {"host:render": "../outside.md"}})
        output = self.tmp / "rendered.md"

        with self.assertRaisesRegex(RuntimeError, "prompt binding path must be repo-relative POSIX text"):
            actions.render_template("host:render", str(output))

        self.assertFalse(output.exists())


class ControllerActionsSourceRegressionTests(unittest.TestCase):
    def test_required_lifecycle_helpers_exist(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        for needle in ("merge_pr", "open_pr_with_label", "open_design_issue_with_labels", "open_release_rollup_pr_from_pending_event", "safe_worktree", "record_recent_pr_merge", "apply_triage_decision_marker", "render_template"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        self.assertIn("validate_self_contained_github_body", text)

    def test_status_banner_action_is_owner_gated_and_uses_gh_comment_command(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        method = text[text.index("def post_status_banner") : text.index("    def safe_sync_main")]
        for needle in (
            "def post_status_banner(self, request: BannerRequest) -> str:",
            'self._require_owner_or_raise("post-banner")',
            "_normalize_lifecycle_target_or_raise(",
            "build_status_banner(normalized)",
            "tempfile.NamedTemporaryFile",
            "gh_comment_command(normalized, Path(tmp))",
            "self.gh(",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        self.assertLess(method.index('self._require_owner_or_raise("post-banner")'), method.index("_normalize_lifecycle_target_or_raise("))
        self.assertLess(method.index("_normalize_lifecycle_target_or_raise("), method.index("tempfile.NamedTemporaryFile"))
        self.assertLess(method.index("tempfile.NamedTemporaryFile"), method.index("self.gh("))

    def test_no_legacy_branch_alias_reads(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        for forbidden in (
            'os.environ.get("INTEGRATION")',
            'os.environ.get("REVIEW_BASE")',
            '"INTEGRATION"',
            '"REVIEW_BASE"',
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

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

    def test_merge_pr_uses_single_linked_issue_parser_for_body_linkage(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        self.assertIn('body = self.gh(["pr", "view", pr_target, "--json", "body", "--jq", ".body"], check=False).stdout', text)
        self.assertIn('linked_issue = self._single_body_linked_issue_or_block(body, action="close")', text)
        self.assertIn("return str(numbers[0]) if len(numbers) == 1 else \"\"", text)

    def test_body_linked_issue_parser_validates_malformed_closing_refs(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        self.assertIn("BODY_CLOSING_ISSUE_TARGET_RE", text)
        self.assertIn("source=\"body-link\"", text)
        self.assertIn("CONTROLLER_ACTION_BLOCKED:invalid-github-target:{action}:{kind}:{source}", text)

    def test_issue_300_draft_pr_ready_before_merge_contract(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        merge_contract = text[text.index("    def _ensure_pr_ready_for_merge") : text.index("    def open_pr_with_label")]
        self.assertNotIn("Refactor (issue-300)", merge_contract)
        self.assertNotIn("Old pattern", merge_contract)
        self.assertNotIn("New principle", merge_contract)
        self.assertIn('"pr", "create", "--draft"', text)
        self.assertIn('def _ensure_pr_ready_for_merge(self, pr_target: str) -> int:', text)
        self.assertIn('"pr", "view", pr_target, "--json", "isDraft", "--jq", ".isDraft"', text)
        self.assertIn('"pr", "ready", pr_target', text)
        self.assertIn("ready = self._ensure_pr_ready_for_merge(pr_target)", text)
        self.assertLess(text.index("ready = self._ensure_pr_ready_for_merge(pr_target)"), text.index('"pr", "merge", pr_target'))

    def test_merge_pr_uses_non_admin_merge_and_surfaces_host_policy_block(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        self.assertIn('["pr", "merge", pr_target, "--squash", "--delete-branch"]', text)
        self.assertIn("blocked-by-host-policy", text)
        self.assertNotIn("--admin", text)
        self.assertNotIn("ReviewGateAction", text)
        self.assertNotIn("review_gate.py", text)

    def test_lifecycle_gh_subject_slots_use_normalized_target_locals(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        allowed = {"pr_target", "issue_target"}
        raw = {"pr", "pr_number", "linked_issue", "pr_num"}
        offenders: list[str] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "gh"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "self"
            ):
                continue
            if not node.args or not isinstance(node.args[0], ast.List):
                continue
            items = node.args[0].elts
            if len(items) < 3:
                continue
            first = items[0].value if isinstance(items[0], ast.Constant) else None
            second = items[1].value if isinstance(items[1], ast.Constant) else None
            if (first, second) not in {
                ("pr", "view"),
                ("pr", "edit"),
                ("pr", "merge"),
                ("issue", "close"),
                ("issue", "edit"),
            }:
                continue
            subject = items[2]
            if isinstance(subject, ast.Name):
                if subject.id not in allowed:
                    offenders.append(f"line {node.lineno}: {first} {second} uses {subject.id}")
            elif isinstance(subject, ast.Call) and isinstance(subject.func, ast.Name) and subject.func.id == "str":
                if subject.args and isinstance(subject.args[0], ast.Name):
                    offenders.append(f"line {node.lineno}: {first} {second} uses str({subject.args[0].id})")
            else:
                offenders.append(f"line {node.lineno}: {first} {second} uses non-local subject")

        for name in raw:
            self.assertFalse(any(f"uses {name}" in offender or f"str({name})" in offender for offender in offenders))
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
