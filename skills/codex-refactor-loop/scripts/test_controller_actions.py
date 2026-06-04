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
from typing import Mapping
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
from codex_refactor_loop.prompt_contracts import GITHUB_POST_RULES_CONTRACT_TOKEN
from codex_refactor_loop.release.publisher import ReleasePublishResult
from codex_refactor_loop.wakeup_plan import harness_spawn_intent_actions


class AllowingGitHubActor:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def require_admission(self, action: str) -> None:
        self.actions.append(action)


class ControllerActionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="controller-actions-test-"))
        (self.tmp / ".refactor-loop" / "state").mkdir(parents=True)
        (self.tmp / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\n'
            'export INTEGRATION_BRANCH="canonical-integration"\n'
            'export REVIEW_BASE_BRANCH="canonical-review"\n'
            'export BUILD_CMD="true"\n'
            'export TEST_CMD="python3 -m unittest discover -s skills/codex-refactor-loop/scripts -p \'test_*.py\'"\n'
            'export HOST_REFACTOR_COMMENT_POLICY="none"\n',
            encoding="utf-8",
        )
        self.actor = AllowingGitHubActor()
        self.actions = ControllerActions(
            LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}),
            github_actor=self.actor,
        )
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
            actions = ControllerActions(
                LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}, cwd=self.tmp),
                github_actor=AllowingGitHubActor(),
            )

        self.assertEqual("canonical-integration", actions.integration_branch)
        self.assertEqual("canonical-review", actions.review_base_branch)

    def test_branch_configuration_fails_closed_without_canonical_env(self) -> None:
        (self.tmp / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\n',
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, {"INTEGRATION": "legacy-integration", "REVIEW_BASE": "legacy-review"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "missing required host branch env"):
                ControllerActions(LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}, cwd=self.tmp))

    def test_branch_configuration_prefers_host_env_canonical_over_legacy_env(self) -> None:
        (self.tmp / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\n'
            'export INTEGRATION_BRANCH="canonical-integration"\n'
            'export REVIEW_BASE_BRANCH="canonical-review"\n',
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, {"INTEGRATION": "legacy-integration", "REVIEW_BASE": "legacy-review"}, clear=True):
            actions = ControllerActions(
                LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}, cwd=self.tmp),
                github_actor=AllowingGitHubActor(),
            )

        self.assertEqual("canonical-integration", actions.integration_branch)
        self.assertEqual("canonical-review", actions.review_base_branch)

    def test_controller_actions_source_locks_named_wakeup_runner_helpers(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        for helper in (
            "def dispatch_consensus_implementation",
            "def publish_implementation_output",
            "def open_release_rollup_pr_from_action",
            "HARNESS_SPAWN_INTENT",
        ):
            with self.subTest(helper=helper):
                self.assertIn(helper, source)

    def test_pr_open_helpers_do_not_use_legacy_branch_alias_values(self) -> None:
        body = self.tmp / "body.md"
        body.write_text("PR body.\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"INTEGRATION": "legacy-integration", "REVIEW_BASE": "legacy-review"}, clear=True):
            actions = ControllerActions(
                LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}, cwd=self.tmp),
                github_actor=AllowingGitHubActor(),
            )
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
                return mock.Mock(returncode=0, stdout="abc123\trefs/heads/canonical-integration\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(actions, "_require_owner_or_raise", return_value=None):
            with mock.patch.object(actions, "gh", side_effect=fake_gh), mock.patch.object(actions, "git", side_effect=fake_git):
                actions.open_pr_with_label("Title", str(body), head="feature")
                actions.open_release_rollup_pr_from_pending_event(json.dumps({"integration_sha": "abc123"}), str(body))

        pr_creates = [call for call in gh_calls if call[:2] == ["pr", "create"]]
        self.assertEqual("canonical-integration", pr_creates[0][pr_creates[0].index("--base") + 1])
        self.assertEqual("canonical-review", pr_creates[1][pr_creates[1].index("--base") + 1])
        self.assertIn(["ls-remote", "--exit-code", "--heads", "origin", "canonical-integration"], git_calls)
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
        self.assertNotIn(str(self.tmp), captured_body)
        self.assertNotIn("/repo/", captured_body)
        self.assertNotIn("工作目录", captured_body)
        status = json.loads((self.tmp / ".refactor-loop" / "state" / "active-controller-status.json").read_text(encoding="utf-8"))
        self.assertEqual("owner", status["active_controller"])
        self.assertEqual("post-banner", status["action"])
        self.assertEqual(self.actor.actions, ["post-banner"])

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
            if args == ["pr", "view", "77", "--json", "labels,body"]:
                return mock.Mock(returncode=0, stdout=json.dumps({"labels": [{"name": labels.MANAGED}], "body": ""}), stderr="")
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
        self.assertLess(gh_calls.index(["pr", "view", "77", "--json", "labels,body"]), gh_calls.index(["pr", "ready", "77"]))
        self.assertLess(gh_calls.index(["pr", "ready", "77"]), gh_calls.index(["pr", "merge", "77", "--squash", "--delete-branch"]))

    def test_merge_pr_non_managed_draft_fails_closed_before_ready_or_merge(self) -> None:
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:5] == ["pr", "view", "77", "--json", "body"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            if args[:5] == ["pr", "view", "77", "--json", "isDraft"]:
                return mock.Mock(returncode=0, stdout="true\n", stderr="")
            if args == ["pr", "view", "77", "--json", "labels,body"]:
                return mock.Mock(returncode=0, stdout=json.dumps({"labels": [{"name": "human-owned"}], "body": ""}), stderr="")
            raise AssertionError(f"unexpected gh side effect for non-managed draft: {args}")

        with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            with mock.patch.object(self.actions, "git", side_effect=AssertionError("git should not be called")):
                self.assertEqual(2, self.actions.merge_pr("77"))

        self.assertIn(["pr", "view", "77", "--json", "labels,body"], gh_calls)
        self.assertFalse(any(call[:2] == ["pr", "ready"] for call in gh_calls), gh_calls)
        self.assertFalse(any(call[:2] == ["pr", "merge"] for call in gh_calls), gh_calls)
        self.assertIn("CONTROLLER_ACTION_BLOCKED:target-not-managed:merge-pr:pr:77", self.pending_events())

    def test_merge_pr_ready_failure_fails_closed_before_merge_side_effects(self) -> None:
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:5] == ["pr", "view", "77", "--json", "body"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            if args[:5] == ["pr", "view", "77", "--json", "isDraft"]:
                return mock.Mock(returncode=0, stdout="true\n", stderr="")
            if args == ["pr", "view", "77", "--json", "labels,body"]:
                return mock.Mock(returncode=0, stdout=json.dumps({"labels": [{"name": labels.MANAGED}], "body": ""}), stderr="")
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

    def test_publish_worker_output_from_action_pushes_from_validated_worktree(self) -> None:
        worktree = self.tmp / ".worktrees" / "pr77"
        worktree.mkdir(parents=True)
        action = {"head_ref": "refactor/iter77-worker", "worktree": str(worktree)}
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="publish-worker-output", lease_id="lease", expires_at="soon")
        calls: list[list[str]] = []

        def fake_run(args: list[str], **_kwargs: object) -> mock.Mock:
            calls.append(args)
            if args == ["git", "-C", str(worktree), "diff", "--quiet"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            if args == ["git", "-C", str(worktree), "fetch", "origin", "refactor/iter77-worker"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            if args == ["git", "-C", str(worktree), "rev-list", "--count", "HEAD..origin/refactor/iter77-worker"]:
                return mock.Mock(returncode=0, stdout="0\n", stderr="")
            if args == ["git", "-C", str(worktree), "push", "origin", "refactor/iter77-worker"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            if args[:3] == ["git", "-C", str(self.tmp)]:
                raise AssertionError("publish-worker-output must not push controller repo HEAD")
            raise AssertionError(f"unexpected git command: {args!r}")

        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch("codex_refactor_loop.controller_actions.subprocess.run", side_effect=fake_run):
                self.assertEqual(0, self.actions.publish_worker_output_from_action(action))

        self.assertEqual(
            calls,
            [
                ["git", "-C", str(worktree), "diff", "--quiet"],
                ["git", "-C", str(worktree), "fetch", "origin", "refactor/iter77-worker"],
                ["git", "-C", str(worktree), "rev-list", "--count", "HEAD..origin/refactor/iter77-worker"],
                ["git", "-C", str(worktree), "push", "origin", "refactor/iter77-worker"],
            ],
        )

    def test_publish_worker_output_from_action_rejects_invalid_head_ref_before_git(self) -> None:
        worktree = self.tmp / ".worktrees" / "pr77"
        worktree.mkdir(parents=True)
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="publish-worker-output", lease_id="lease", expires_at="soon")

        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch("codex_refactor_loop.controller_actions.subprocess.run", side_effect=AssertionError("git diff should not run")):
                with mock.patch.object(self.actions, "safe_push", side_effect=AssertionError("safe_push should not run")):
                    self.assertEqual(2, self.actions.publish_worker_output_from_action({"head_ref": "-bad", "worktree": str(worktree)}))

    def test_publish_worker_output_from_action_rejects_non_absolute_or_outside_worktree(self) -> None:
        outside = self.tmp / "outside"
        outside.mkdir()
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="publish-worker-output", lease_id="lease", expires_at="soon")

        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch("codex_refactor_loop.controller_actions.subprocess.run", side_effect=AssertionError("git diff should not run")):
                with mock.patch.object(self.actions, "safe_push", side_effect=AssertionError("safe_push should not run")):
                    self.assertEqual(2, self.actions.publish_worker_output_from_action({"head_ref": "refactor/iter77", "worktree": "relative"}))
                    self.assertEqual(2, self.actions.publish_worker_output_from_action({"head_ref": "refactor/iter77", "worktree": str(outside)}))

    def test_publish_worker_output_from_action_rejects_dirty_worktree_before_safe_push(self) -> None:
        worktree = self.tmp / ".worktrees" / "pr77"
        worktree.mkdir(parents=True)
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="publish-worker-output", lease_id="lease", expires_at="soon")

        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch("codex_refactor_loop.controller_actions.subprocess.run", return_value=mock.Mock(returncode=1, stdout="", stderr="dirty")):
                with mock.patch.object(self.actions, "safe_push", side_effect=AssertionError("safe_push should not run")):
                    self.assertEqual(2, self.actions.publish_worker_output_from_action({"head_ref": "refactor/iter77", "worktree": str(worktree)}))

    def test_publish_worker_output_from_action_non_owner_noops_before_git(self) -> None:
        worktree = self.tmp / ".worktrees" / "pr77"
        worktree.mkdir(parents=True)
        decision = mock.Mock(allowed=False, owner_device="device-b", status="not-owner", action="publish-worker-output", lease_id="", expires_at="")

        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch("codex_refactor_loop.controller_actions.subprocess.run", side_effect=AssertionError("git diff should not run")):
                with mock.patch.object(self.actions, "safe_push", side_effect=AssertionError("safe_push should not run")):
                    self.assertEqual(3, self.actions.publish_worker_output_from_action({"head_ref": "refactor/iter77", "worktree": str(worktree)}))

    def test_publish_implementation_output_commits_pushes_opens_pr_then_dispatches_reviewers(self) -> None:
        worktree = self.tmp / ".worktrees" / "iter77-issue-77"
        worktree.mkdir(parents=True)
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="publish-implementation-output", lease_id="lease", expires_at="soon")
        sequence: list[str] = []
        action = {
            "source_marker": "IMPLEMENT_DONE:issue-77:ok",
            "target_kind": "issue",
            "target_number": 77,
            "linked_issue": 77,
            "head_ref": "refactor/iter77-issue-77",
            "worktree": str(worktree),
        }

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            if args == ["issue", "view", "77", "--json", "labels,body"]:
                return mock.Mock(returncode=0, stdout=json.dumps({"labels": [{"name": labels.MANAGED}], "body": ""}), stderr="")
            if args[:4] == ["pr", "list", "--state", "open"]:
                return mock.Mock(returncode=0, stdout="[]", stderr="")
            raise AssertionError(f"unexpected gh call: {args}")

        def fake_run(args: list[str], **kwargs: object) -> mock.Mock:
            if args[:2] == ["bash", "-lc"]:
                sequence.append(f"host:{args[2]}")
                return mock.Mock(returncode=0, stdout="", stderr="")
            if args == ["git", "-C", str(worktree), "diff", "--quiet"]:
                sequence.append("git:diff")
                return mock.Mock(returncode=1, stdout="", stderr="")
            if args == ["git", "-C", str(worktree), "rev-parse", "--abbrev-ref", "HEAD"]:
                sequence.append("git:branch")
                return mock.Mock(returncode=0, stdout="refactor/iter77-issue-77\n", stderr="")
            if args == ["git", "-C", str(worktree), "merge-base", "HEAD", "origin/canonical-integration"]:
                sequence.append("git:merge-base")
                return mock.Mock(returncode=0, stdout="base-sha\n", stderr="")
            if args == ["git", "-C", str(worktree), "rev-parse", "--verify", "origin/canonical-integration"]:
                sequence.append("git:origin-base")
                return mock.Mock(returncode=0, stdout="base-sha\n", stderr="")
            if args == ["git", "-C", str(worktree), "add", "-A"]:
                sequence.append("git:add")
                return mock.Mock(returncode=0, stdout="", stderr="")
            if args == ["git", "-C", str(worktree), "commit", "-m", "实施 issue #77"]:
                sequence.append("git:commit")
                return mock.Mock(returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected subprocess call: {args!r}")

        def fake_safe_push(*, branch: str, worktree: Path) -> int:
            sequence.append("safe_push")
            self.assertEqual("refactor/iter77-issue-77", branch)
            return 0

        def fake_open(title: str, body_file: str, base: str | None = None, head: str = "") -> tuple[int, str]:
            sequence.append("open_pr")
            self.assertEqual("实施 issue #77", title)
            self.assertEqual("canonical-integration", base)
            self.assertEqual("refactor/iter77-issue-77", head)
            body = Path(body_file).read_text(encoding="utf-8")
            self.assertIn("## 实施 issue #77", body)
            self.assertIn("Closes #77", body)
            self.assertTrue(body.splitlines()[-1] == "⟦AI:AUTO-LOOP⟧")
            return 414, "https://github.com/owner/repo/pull/414"

        def fake_dispatch(review_action: Mapping[str, object]) -> int:
            sequence.append("dispatch_reviewers")
            self.assertEqual({"target_kind": "PR", "target_number": 414}, dict(review_action))
            return 0

        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
                with mock.patch("codex_refactor_loop.controller_actions.subprocess.run", side_effect=fake_run):
                    with mock.patch.object(self.actions, "safe_push", side_effect=fake_safe_push):
                        with mock.patch.object(self.actions, "open_pr_with_label", side_effect=fake_open):
                            with mock.patch.object(self.actions, "dispatch_reviewers", side_effect=fake_dispatch):
                                self.assertEqual(0, self.actions.publish_implementation_output(action))

        self.assertEqual(
            sequence,
            [
                "git:branch",
                "git:merge-base",
                "git:origin-base",
                "host:true",
                "host:python3 -m unittest discover -s skills/codex-refactor-loop/scripts -p 'test_*.py'",
                "git:diff",
                "git:add",
                "git:commit",
                "safe_push",
                "open_pr",
                "dispatch_reviewers",
            ],
        )

    def test_publish_implementation_output_rejects_duplicate_pr_before_commit(self) -> None:
        worktree = self.tmp / ".worktrees" / "iter77-issue-77"
        worktree.mkdir(parents=True)
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="publish-implementation-output", lease_id="lease", expires_at="soon")

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            if args == ["issue", "view", "77", "--json", "labels,body"]:
                return mock.Mock(returncode=0, stdout=json.dumps({"labels": [{"name": labels.MANAGED}], "body": ""}), stderr="")
            if args[:4] == ["pr", "list", "--state", "open"]:
                return mock.Mock(returncode=0, stdout=json.dumps([{"number": 414}]), stderr="")
            raise AssertionError(f"unexpected gh call: {args}")

        action = {
            "source_marker": "IMPLEMENT_DONE:issue-77:ok",
            "target_kind": "issue",
            "target_number": 77,
            "head_ref": "refactor/iter77-issue-77",
            "worktree": str(worktree),
        }
        def fake_run(args: list[str], **kwargs: object) -> mock.Mock:
            if args == ["git", "-C", str(worktree), "rev-parse", "--abbrev-ref", "HEAD"]:
                return mock.Mock(returncode=0, stdout="refactor/iter77-issue-77\n", stderr="")
            if args == ["git", "-C", str(worktree), "merge-base", "HEAD", "origin/canonical-integration"]:
                return mock.Mock(returncode=0, stdout="base-sha\n", stderr="")
            if args == ["git", "-C", str(worktree), "rev-parse", "--verify", "origin/canonical-integration"]:
                return mock.Mock(returncode=0, stdout="base-sha\n", stderr="")
            raise AssertionError(f"commit should not run: {args!r}")

        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
                with mock.patch("codex_refactor_loop.controller_actions.subprocess.run", side_effect=fake_run):
                    self.assertEqual(2, self.actions.publish_implementation_output(action))

    def test_dispatch_consensus_implementation_renders_prompt_from_durable_artifact_fields(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="dispatch-consensus-implementation", lease_id="lease", expires_at="soon")
        worktree = self.tmp / ".worktrees" / "iter413-issue-413"
        render_calls: list[dict[str, str]] = []

        action = {
            "target_kind": "issue",
            "target_number": 413,
            "consensus_artifact": ".refactor-loop/runs/phase9-issue413-r5-judge.md",
            "design_decision_path": ".refactor-loop/runs/phase9-issue413-r5-judge.md",
            "scope_paths": "- skills/codex-refactor-loop/scripts/codex_refactor_loop/wakeup_plan.py",
            "old_pattern": "old",
            "new_principle": "new",
            "verification_hints": "python3 -m unittest",
            "cluster_id": "issue-413",
            "iteration": "413",
            "source_ref": "gh-issue-413",
        }

        def fake_render(_template: str, output_path: str, env: Mapping[str, str] | None = None) -> None:
            assert env is not None
            render_calls.append(dict(env))
            Path(output_path).write_text("rendered prompt\n", encoding="utf-8")

        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch.object(self.actions, "fresh_safe_worktree", return_value=(worktree, "refactor/iter413-issue-413")) as safe_worktree:
                with mock.patch.object(self.actions, "render_template", side_effect=fake_render):
                    self.assertEqual(0, self.actions.dispatch_consensus_implementation(action))

        safe_worktree.assert_called_once_with("413", "issue-413", "canonical-integration")
        self.assertEqual(render_calls[0]["DESIGN_DECISION_PATH"], ".refactor-loop/runs/phase9-issue413-r5-judge.md")
        self.assertEqual(render_calls[0]["SCOPE_PATHS"], "- skills/codex-refactor-loop/scripts/codex_refactor_loop/wakeup_plan.py")
        self.assertEqual(render_calls[0]["OLD_PATTERN"], "old")
        self.assertEqual(render_calls[0]["NEW_PRINCIPLE"], "new")
        pending = self.pending_events()
        self.assertIn("HARNESS_SPAWN_INTENT", pending)
        self.assertIn('"intent_id": "dispatch-consensus-implementation:413"', pending)
        self.assertIn('"task_id": "implement-issue-413"', pending)

    def test_dispatch_consensus_implementation_intent_round_trips_through_wakeup_plan(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="dispatch-consensus-implementation", lease_id="lease", expires_at="soon")
        worktree = self.tmp / ".worktrees" / "iter413-issue-413"
        action = {
            "target_kind": "issue",
            "target_number": 413,
            "consensus_artifact": ".refactor-loop/runs/phase9-issue413-r5-judge.md",
            "design_decision_path": ".refactor-loop/runs/phase9-issue413-r5-judge.md",
            "scope_paths": "- skills/codex-refactor-loop/scripts/codex_refactor_loop/wakeup_plan.py",
            "old_pattern": "old",
            "new_principle": "new",
            "verification_hints": "python3 -m unittest",
            "cluster_id": "issue-413",
            "iteration": "413",
            "source_ref": "gh-issue-413",
        }

        def fake_render(_template: str, output_path: str, env: Mapping[str, str] | None = None) -> None:
            Path(output_path).write_text("rendered prompt\n", encoding="utf-8")

        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch.object(self.actions, "fresh_safe_worktree", return_value=(worktree, "refactor/iter413-issue-413")):
                with mock.patch.object(self.actions, "render_template", side_effect=fake_render):
                    self.assertEqual(0, self.actions.dispatch_consensus_implementation(action))

        pending = self.pending_events()
        self.assertRegex(pending, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z HARNESS_SPAWN_INTENT ")
        self.assertIn(" HARNESS_SPAWN_INTENT ", pending)

        ctx = LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}, cwd=self.tmp, read_only=True)
        projected = harness_spawn_intent_actions(self.tmp, ctx, monitor=None, gh_items=[], gh_items_loaded=False)

        self.assertEqual(1, len(projected), projected)
        action = projected[0]
        self.assertEqual("harness-spawn-intent", action["kind"])
        self.assertEqual("dispatch-consensus-implementation:413", action["intent_id"])
        self.assertEqual("implement-issue-413", action["item"])
        self.assertEqual("controller-actions", action["source"])
        self.assertIn(" HARNESS_SPAWN_INTENT ", action["evidence"])

    def test_dispatch_consensus_implementation_rejects_empty_plan_fields_before_worktree(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="dispatch-consensus-implementation", lease_id="lease", expires_at="soon")
        action = {
            "target_kind": "issue",
            "target_number": 413,
            "consensus_artifact": ".refactor-loop/runs/phase9-issue413-r5-judge.md",
            "design_decision_path": "",
            "scope_paths": "",
            "old_pattern": "",
            "new_principle": "",
            "cluster_id": "issue-413",
            "iteration": "413",
        }

        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch.object(self.actions, "safe_worktree", side_effect=AssertionError("safe_worktree should not run")):
                with mock.patch.object(self.actions, "render_template", side_effect=AssertionError("render_template should not run")):
                    self.assertEqual(2, self.actions.dispatch_consensus_implementation(action))

        self.assertNotIn("HARNESS_SPAWN_INTENT", self.pending_events())

    def test_dispatch_consensus_implementation_rejects_not_ready_target_before_worktree(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="dispatch-consensus-implementation", lease_id="lease", expires_at="soon")

        def action_for(reason: str) -> dict[str, object]:
            return {
                "target_kind": "issue",
                "target_number": 413,
                "consensus_artifact": ".refactor-loop/runs/phase9-issue413-r5-judge.md",
                "design_decision_path": ".refactor-loop/runs/phase9-issue413-r5-judge.md",
                "scope_paths": "- skills/codex-refactor-loop/scripts/codex_refactor_loop/wakeup_plan.py",
                "old_pattern": "old",
                "new_principle": "new",
                "verification_hints": "python3 -m unittest",
                "cluster_id": f"issue-413-{reason}",
                "iteration": "413",
                "source_ref": "gh-issue-413",
            }

        def fake_git_text(args: list[str], *, cwd: Path) -> mock.Mock:
            command = " ".join(args)
            if "refs/heads/refactor/iter413-issue-413-local_iter_branch" in command:
                return mock.Mock(returncode=0, stdout="local-sha\n", stderr="")
            if "refs/remotes/origin/refactor/iter413-issue-413-remote_iter_branch" in command:
                return mock.Mock(returncode=0, stdout="remote-sha\n", stderr="")
            return mock.Mock(returncode=1, stdout="", stderr="")

        for reason in ("pending_implement_intent", "remote_iter_branch"):
            with self.subTest(reason=reason):
                shutil.rmtree(self.tmp / ".worktrees", ignore_errors=True)
                shutil.rmtree(self.tmp / ".refactor-loop" / "logs", ignore_errors=True)
                (self.tmp / ".refactor-loop" / "logs").mkdir(parents=True, exist_ok=True)
                (self.tmp / ".refactor-loop" / ".controller-pending-events.log").unlink(missing_ok=True)
                action = action_for(reason)
                cluster_id = str(action["cluster_id"])
                if reason == "pending_implement_intent":
                    pending = {
                        "intent_id": "dispatch-consensus-implementation:413",
                        "task_id": f"implement-{cluster_id}",
                    }
                    (self.tmp / ".refactor-loop" / ".controller-pending-events.log").write_text(
                        f"2026-06-01T00:00:00Z HARNESS_SPAWN_INTENT {json.dumps(pending)}\n",
                        encoding="utf-8",
                    )
                pending_before = self.pending_events()
                with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
                    with mock.patch("codex_refactor_loop.wakeup_plan.git_text", side_effect=fake_git_text):
                        with mock.patch.object(self.actions, "fresh_safe_worktree", side_effect=AssertionError("fresh_safe_worktree should not run")):
                            with mock.patch.object(self.actions, "render_template", side_effect=AssertionError("render_template should not run")):
                                self.assertEqual(2, self.actions.dispatch_consensus_implementation(action))

                self.assertEqual(pending_before, self.pending_events())

    def test_dispatch_consensus_implementation_resets_markerless_local_attempt(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="dispatch-consensus-implementation", lease_id="lease", expires_at="soon")
        action = {
            "target_kind": "issue",
            "target_number": 413,
            "consensus_artifact": ".refactor-loop/runs/phase9-issue413-r5-judge.md",
            "design_decision_path": ".refactor-loop/runs/phase9-issue413-r5-judge.md",
            "scope_paths": "- skills/codex-refactor-loop/scripts/codex_refactor_loop/wakeup_plan.py",
            "old_pattern": "old",
            "new_principle": "new",
            "verification_hints": "python3 -m unittest",
            "cluster_id": "issue-413",
            "iteration": "413",
            "source_ref": "gh-issue-413",
        }
        worktree = self.tmp / ".worktrees" / "iter413-issue-413"
        worktree.mkdir(parents=True)
        (self.tmp / ".refactor-loop" / "logs").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".refactor-loop" / "logs" / "implement-issue-413.log").write_text("old output\nEXIT=0\n", encoding="utf-8")

        def fake_render(_template: str, output_path: str, env: Mapping[str, str] | None = None) -> None:
            Path(output_path).write_text("rendered prompt\n", encoding="utf-8")

        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch.object(self.actions, "fresh_safe_worktree", return_value=(worktree, "refactor/iter413-issue-413")) as fresh_safe_worktree:
                with mock.patch.object(self.actions, "render_template", side_effect=fake_render):
                    self.assertEqual(0, self.actions.dispatch_consensus_implementation(action))

        fresh_safe_worktree.assert_called_once_with("413", "issue-413", "canonical-integration")
        self.assertIn("HARNESS_SPAWN_INTENT", self.pending_events())

    def test_dispatch_consensus_implementation_clears_failed_log_before_fresh_spawn_intent(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="dispatch-consensus-implementation", lease_id="lease", expires_at="soon")
        action = {
            "target_kind": "issue",
            "target_number": 493,
            "consensus_artifact": ".refactor-loop/runs/phase9-issue493-r5-judge.md",
            "design_decision_path": ".refactor-loop/runs/phase9-issue493-r5-judge.md",
            "scope_paths": "- skills/codex-refactor-loop/scripts/codex_refactor_loop/wakeup_plan.py",
            "old_pattern": "old",
            "new_principle": "new",
            "verification_hints": "python3 -m unittest",
            "cluster_id": "issue-493",
            "iteration": "493",
            "source_ref": "gh-issue-493",
        }
        worktree = self.tmp / ".worktrees" / "iter493-issue-493"
        log = self.tmp / ".refactor-loop" / "logs" / "implement-issue-493.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("old failed run\nEXIT=1\n", encoding="utf-8")

        def fake_render(_template: str, output_path: str, env: Mapping[str, str] | None = None) -> None:
            Path(output_path).write_text("rendered prompt\n", encoding="utf-8")

        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch.object(self.actions, "fresh_safe_worktree", return_value=(worktree, "refactor/iter493-issue-493")):
                with mock.patch.object(self.actions, "render_template", side_effect=fake_render):
                    self.assertEqual(0, self.actions.dispatch_consensus_implementation(action))

        self.assertFalse(log.exists())
        projected = harness_spawn_intent_actions(
            self.tmp,
            LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}, cwd=self.tmp, read_only=True),
            monitor=None,
            gh_items=[],
            gh_items_loaded=False,
        )
        self.assertEqual(1, len(projected), projected)
        self.assertEqual(str(log.resolve()), projected[0]["log"])

    def test_dispatch_consensus_implementation_preserves_inflight_and_publish_ready_logs(self) -> None:
        for name, contents in (
            ("in-flight", "worker still running\n"),
            ("publish-ready", "IMPLEMENT_DONE:issue-493:ok\nEXIT=0\n"),
        ):
            with self.subTest(name=name):
                log = self.tmp / ".refactor-loop" / "logs" / "implement-issue-493.log"
                log.parent.mkdir(parents=True, exist_ok=True)
                log.write_text(contents, encoding="utf-8")
                action = {"target_number": 493, "cluster_id": "issue-493"}
                if name == "publish-ready":
                    worktree = self.tmp / ".worktrees" / "iter493-issue-493"
                    worktree.mkdir(parents=True, exist_ok=True)

                    def fake_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
                        if command[-2:] == ["--abbrev-ref", "HEAD"]:
                            return subprocess.CompletedProcess(command, 0, "refactor/iter493-issue-493\n", "")
                        if command[-3:] == ["merge-base", "HEAD", "origin/canonical-integration"]:
                            return subprocess.CompletedProcess(command, 0, "base\n", "")
                        if command[-2:] == ["--verify", "origin/canonical-integration"]:
                            return subprocess.CompletedProcess(command, 0, "base\n", "")
                        if command[-2:] == ["diff", "--quiet"]:
                            return subprocess.CompletedProcess(command, 1, "", "")
                        return subprocess.CompletedProcess(command, 0, "", "")

                    with mock.patch.object(self.actions, "_git_lifecycle_command", side_effect=fake_command):
                        self.actions._clear_stale_implement_log_for_fresh_dispatch(log, action)
                else:
                    self.actions._clear_stale_implement_log_for_fresh_dispatch(log, action)

                self.assertTrue(log.exists())
                log.unlink(missing_ok=True)

    def test_dispatch_reviewers_renders_three_role_prompts_with_pr_facts(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="dispatch-reviewers", lease_id="lease", expires_at="soon")
        render_envs: list[dict[str, str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            if args == ["pr", "view", "77", "--json", "title,baseRefName,headRefName,headRefOid"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps({"title": "Fix wakeup runner", "baseRefName": "dev", "headRefName": "refactor/issue413", "headRefOid": "a" * 40}),
                    stderr="",
                )
            raise AssertionError(f"unexpected gh call: {args}")

        def fake_render(_template: str, output_path: str, env: Mapping[str, str] | None = None) -> None:
            assert env is not None
            render_envs.append(dict(env))
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text("review prompt\n", encoding="utf-8")

        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
                with mock.patch.object(self.actions, "render_template", side_effect=fake_render):
                    self.assertEqual(0, self.actions.dispatch_reviewers({"target_kind": "PR", "target_number": 77}))

        self.assertEqual([".refactor-loop/runs/review-pr77-architect-r1.md", ".refactor-loop/runs/review-pr77-tests-r1.md", ".refactor-loop/runs/review-pr77-quality-r1.md"], [env["REVIEW_OUTPUT_PATH"] for env in render_envs])
        self.assertTrue(all(env["BASE_BRANCH"] == "dev" and env["HEAD_BRANCH"] == "refactor/issue413" for env in render_envs))
        self.assertTrue(all(env["HEAD_SHA"] == "a" * 40 for env in render_envs))
        pending = self.pending_events()
        for role in ("architect", "tests", "quality"):
            self.assertIn(f'"intent_id": "dispatch-reviewers:77:{role}:r1"', pending)
        intents = [json.loads(line.split(" HARNESS_SPAWN_INTENT ", 1)[1]) for line in pending.splitlines() if " HARNESS_SPAWN_INTENT " in line]
        self.assertTrue(all(Path(str(intent["cd"])).is_absolute() for intent in intents))
        self.assertTrue(all(intent["cd"] == str(self.tmp.resolve()) for intent in intents))

    def test_dispatch_reviewers_redispatches_only_stale_roles_and_skips_pending_intents(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="dispatch-reviewers", lease_id="lease", expires_at="soon")
        existing_intent = {
            "intent_id": "dispatch-reviewers:77:architect:r1",
            "controller_action": "spawn_codex_harness_background",
        }
        (self.tmp / ".refactor-loop" / ".controller-pending-events.log").write_text(
            f"2026-06-01T00:00:00Z HARNESS_SPAWN_INTENT {json.dumps(existing_intent, sort_keys=True)}\n",
            encoding="utf-8",
        )
        render_envs: list[dict[str, str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            if args == ["pr", "view", "77", "--json", "title,baseRefName,headRefName,headRefOid"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps({"title": "Fix wakeup runner", "baseRefName": "dev", "headRefName": "refactor/issue413", "headRefOid": "a" * 40}),
                    stderr="",
                )
            raise AssertionError(f"unexpected gh call: {args}")

        def fake_render(_template: str, output_path: str, env: Mapping[str, str] | None = None) -> None:
            assert env is not None
            render_envs.append(dict(env))
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text("review prompt\n", encoding="utf-8")

        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
                with mock.patch.object(self.actions, "render_template", side_effect=fake_render):
                    self.assertEqual(
                        0,
                        self.actions.dispatch_reviewers(
                            {
                                "target_kind": "PR",
                                "target_number": 77,
                                "stale_review_roles": ["architect", "tests"],
                                "head_sha": "a" * 40,
                            }
                        ),
                    )

        self.assertEqual([".refactor-loop/runs/review-pr77-tests-r1.md"], [env["REVIEW_OUTPUT_PATH"] for env in render_envs])
        self.assertEqual(["a" * 40], [env["HEAD_SHA"] for env in render_envs])
        pending = self.pending_events()
        self.assertEqual(1, pending.count('"intent_id": "dispatch-reviewers:77:architect:r1"'))
        self.assertIn('"intent_id": "dispatch-reviewers:77:tests:r1"', pending)
        self.assertNotIn('"intent_id": "dispatch-reviewers:77:quality:r1"', pending)

    def test_dispatch_reviewers_redispatch_uses_next_round_after_completed_stale_logs(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="dispatch-reviewers", lease_id="lease", expires_at="soon")
        for role in ("architect", "tests"):
            (self.tmp / ".refactor-loop" / "runs").mkdir(parents=True, exist_ok=True)
            (self.tmp / ".refactor-loop" / "logs").mkdir(parents=True, exist_ok=True)
            (self.tmp / ".refactor-loop" / "runs" / f"review-pr77-{role}-r1.md").write_text(
                f"---\nhead_sha: {'b' * 40}\nverdict: approve\n---\nREVIEW_DONE:77:{role}:approve\n",
                encoding="utf-8",
            )
            (self.tmp / ".refactor-loop" / "logs" / f"review-pr77-{role}-r1.log").write_text(
                f"head_sha: {'b' * 40}\nREVIEW_DONE:77:{role}:approve\nEXIT=0\n",
                encoding="utf-8",
            )
        existing_intent = {
            "intent_id": "dispatch-reviewers:77:architect:r2",
            "controller_action": "spawn_codex_harness_background",
        }
        (self.tmp / ".refactor-loop" / ".controller-pending-events.log").write_text(
            f"2026-06-01T00:00:00Z HARNESS_SPAWN_INTENT {json.dumps(existing_intent, sort_keys=True)}\n",
            encoding="utf-8",
        )
        render_envs: list[dict[str, str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            if args == ["pr", "view", "77", "--json", "title,baseRefName,headRefName,headRefOid"]:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps({"title": "Fix wakeup runner", "baseRefName": "dev", "headRefName": "refactor/issue413", "headRefOid": "a" * 40}),
                    stderr="",
                )
            raise AssertionError(f"unexpected gh call: {args}")

        def fake_render(_template: str, output_path: str, env: Mapping[str, str] | None = None) -> None:
            assert env is not None
            render_envs.append(dict(env))
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text("review prompt\n", encoding="utf-8")

        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
                with mock.patch.object(self.actions, "render_template", side_effect=fake_render):
                    self.assertEqual(
                        0,
                        self.actions.dispatch_reviewers(
                            {
                                "target_kind": "PR",
                                "target_number": 77,
                                "stale_review_roles": ["architect", "tests"],
                                "head_sha": "a" * 40,
                            }
                        ),
                    )

        self.assertEqual([".refactor-loop/runs/review-pr77-tests-r2.md"], [env["REVIEW_OUTPUT_PATH"] for env in render_envs])
        pending = self.pending_events()
        self.assertEqual(1, pending.count('"intent_id": "dispatch-reviewers:77:architect:r2"'))
        self.assertIn('"intent_id": "dispatch-reviewers:77:tests:r2"', pending)
        self.assertNotIn('"intent_id": "dispatch-reviewers:77:tests:r1"', pending)
        tests_intent = [
            json.loads(line.split(" HARNESS_SPAWN_INTENT ", 1)[1])
            for line in pending.splitlines()
            if '"intent_id": "dispatch-reviewers:77:tests:r2"' in line
        ][0]
        self.assertEqual(tests_intent["task_id"], "review-pr77-tests-r2")
        self.assertEqual(tests_intent["log"], ".refactor-loop/logs/review-pr77-tests-r2.log")
        self.assertEqual(tests_intent["cd"], str(self.tmp.resolve()))
        self.assertTrue(Path(str(tests_intent["cd"])).is_absolute())

    def test_dispatch_reviewers_fails_closed_when_pr_head_missing(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="dispatch-reviewers", lease_id="lease", expires_at="soon")

        for facts in (
            {"title": "PR", "baseRefName": "dev", "headRefName": "", "headRefOid": "a" * 40},
            {"title": "PR", "baseRefName": "dev", "headRefName": "refactor/issue413", "headRefOid": ""},
        ):
            with self.subTest(facts=facts):
                with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
                    with mock.patch.object(
                        self.actions,
                        "gh",
                        return_value=mock.Mock(returncode=0, stdout=json.dumps(facts), stderr=""),
                    ):
                        with mock.patch.object(self.actions, "render_template", side_effect=AssertionError("render_template should not run")):
                            self.assertEqual(2, self.actions.dispatch_reviewers({"target_kind": "PR", "target_number": 77}))

                self.assertNotIn("HARNESS_SPAWN_INTENT", self.pending_events())

    def test_dispatch_reviewers_source_requires_controller_head_oid_binding(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        self.assertIn('"title,baseRefName,headRefName,headRefOid"', source)
        self.assertIn('"HEAD_SHA": head_sha', source)
        self.assertIn("def _next_review_round(", source)
        self.assertIn("round_number = self._next_review_round(pr_target, role)", source)
        self.assertIn("intent_id=f\"dispatch-reviewers:{pr_target}:{role}:r{round_number}\"", source)
        self.assertIn('"cd": str(cd.resolve())', source)
        for prompt_name in ("reviewer-architect.md", "reviewer-tests.md", "reviewer-quality.md"):
            with self.subTest(prompt=prompt_name):
                prompt = (SCRIPT_DIR.parent / "prompts" / prompt_name).read_text(encoding="utf-8")
                self.assertIn("head_sha: ${HEAD_SHA}", prompt)

    def test_open_release_rollup_pr_from_action_passes_event_json_body_and_title(self) -> None:
        event = {"integration_sha": "abc123", "integration_branch": "auto-refact-dev"}
        body = ".refactor-loop/runs/release-rollup-pr-body.md"
        calls: list[tuple[str, str, str]] = []

        def fake_open(event_json: str, body_file: str, *, title: str = "Release rollup") -> tuple[int, str]:
            calls.append((event_json, body_file, title))
            return 77, "https://github.com/owner/repo/pull/77"

        with mock.patch.object(self.actions, "open_release_rollup_pr_from_pending_event", side_effect=fake_open):
            self.assertEqual(0, self.actions.open_release_rollup_pr_from_action({"event": event, "body_file": body, "title": "Custom rollup"}))

        self.assertEqual(calls, [(json.dumps(event, sort_keys=True), body, "Custom rollup")])

    def test_open_release_rollup_pr_from_action_propagates_helper_failure(self) -> None:
        with mock.patch.object(self.actions, "open_release_rollup_pr_from_pending_event", side_effect=RuntimeError("stale sha")):
            with self.assertRaisesRegex(RuntimeError, "stale sha"):
                self.actions.open_release_rollup_pr_from_action({"event": {"integration_sha": "abc123"}, "body_file": "body.md"})

    def test_close_managed_item_from_drop_marker_closes_issue_and_pr_with_drop_marker(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="close-managed-drop", lease_id="lease", expires_at="soon")
        calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            calls.append(args)
            if args[:5] == ["issue", "view", "53", "--json", "labels,body"]:
                return mock.Mock(returncode=0, stdout=json.dumps({"labels": [{"name": labels.MANAGED}], "body": ""}), stderr="")
            if args[:5] == ["pr", "view", "77", "--json", "labels,body"]:
                return mock.Mock(returncode=0, stdout=json.dumps({"labels": [{"name": labels.MANAGED}], "body": ""}), stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
                self.assertEqual(0, self.actions.close_managed_item_from_drop_marker({"source_marker": "META_RESOLVED:drop:no-action", "target_kind": "issue", "target_number": 53}))
                self.assertEqual(0, self.actions.close_managed_item_from_drop_marker({"source_marker": "META_RESOLVED:drop:no-action", "target_kind": "PR", "target_number": 77}))

        self.assertEqual(calls[0], ["issue", "view", "53", "--json", "labels,body"])
        self.assertEqual(calls[1][:3], ["issue", "close", "53"])
        self.assertIn("--reason", calls[1])
        self.assertEqual(calls[2], ["pr", "view", "77", "--json", "labels,body"])
        self.assertEqual(calls[3][:3], ["pr", "close", "77"])
        self.assertIn("--comment", calls[3])

    def test_close_managed_item_from_drop_marker_blocks_non_managed_live_target_before_close(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="close-managed-drop", lease_id="lease", expires_at="soon")
        calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            calls.append(args)
            if args[:5] == ["issue", "view", "53", "--json", "labels,body"]:
                return mock.Mock(returncode=0, stdout=json.dumps({"labels": [], "body": ""}), stderr="")
            raise AssertionError(f"unexpected gh call: {args}")

        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
                self.assertEqual(2, self.actions.close_managed_item_from_drop_marker({"source_marker": "META_RESOLVED:drop:no-action", "target_kind": "issue", "target_number": 53}))

        self.assertEqual([["issue", "view", "53", "--json", "labels,body"]], calls)
        self.assertIn(
            "CONTROLLER_ACTION_BLOCKED:target-not-managed:close-managed-drop:issue:53",
            self.pending_events(),
        )

    def test_close_managed_item_from_drop_marker_rejects_invalid_marker_or_target(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="close-managed-drop", lease_id="lease", expires_at="soon")

        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch.object(self.actions, "gh", side_effect=AssertionError("gh should not run")):
                self.assertEqual(2, self.actions.close_managed_item_from_drop_marker({"source_marker": "META_RESOLVED:retry-fix:no-action", "target_kind": "issue", "target_number": 53}))
                self.assertEqual(2, self.actions.close_managed_item_from_drop_marker({"source_marker": "META_RESOLVED:drop:no-action", "target_kind": "issue", "target_number": "01"}))

        self.assertIn(
            "CONTROLLER_ACTION_BLOCKED:invalid-github-target:close-managed-drop:issue:wakeup-runner-action",
            self.pending_events(),
        )

    def test_close_managed_item_from_drop_marker_non_owner_noops_before_gh(self) -> None:
        decision = mock.Mock(allowed=False, owner_device="device-b", status="not-owner", action="close-managed-drop", lease_id="", expires_at="")

        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch.object(self.actions, "gh", side_effect=AssertionError("gh should not run")):
                self.assertEqual(3, self.actions.close_managed_item_from_drop_marker({"source_marker": "META_RESOLVED:drop:no-action", "target_kind": "issue", "target_number": 53}))

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

    def test_git_fresh_safe_worktree_resets_existing_branch_and_worktree_to_origin_base(self) -> None:
        calls: list[list[str]] = []

        class RecordingGit(Git):
            def run(self, args: list[str], *, check: bool = True) -> mock.Mock:
                calls.append(args)
                return mock.Mock(returncode=0, stdout="", stderr="")

        worktree, branch = RecordingGit(self.tmp).fresh_safe_worktree("413", "issue-413", "auto-refact-dev")

        self.assertEqual(worktree, self.tmp / ".worktrees" / "iter413-issue-413")
        self.assertEqual(branch, "refactor/iter413-issue-413")
        self.assertEqual(
            calls,
            [
                ["fetch", "origin", "auto-refact-dev"],
                ["worktree", "remove", str(self.tmp / ".worktrees" / "iter413-issue-413"), "--force"],
                ["branch", "-D", "refactor/iter413-issue-413"],
                ["worktree", "add", "-b", "refactor/iter413-issue-413", str(self.tmp / ".worktrees" / "iter413-issue-413"), "origin/auto-refact-dev"],
            ],
        )

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

    def test_apply_issue_decomposition_plan_creates_children_with_design_bundle_and_comments_parent_only(self) -> None:
        consensus = ".refactor-loop/runs/phase9-issue403-r6-judge.md"
        (self.tmp / ".refactor-loop" / "runs").mkdir(parents=True, exist_ok=True)
        (self.tmp / consensus).write_text("consensus artifact\n", encoding="utf-8")

        def write_child(name: str, scope: str, non_goals: str) -> str:
            path = f".refactor-loop/runs/{name}.md"
            (self.tmp / path).write_text(
                "## child\n\n"
                "Parent issue: #403\n"
                f"Source consensus artifact: {Path(consensus).name}\n"
                f"Scope: {scope}\n"
                f"Non-goals: {non_goals}\n\n"
                "<details>\n<summary>内联 artifact 1: decision.md</summary>\n\n"
                "```markdown\nraw decision\n```\n\n</details>\n\n"
                "⟦AI:AUTO-LOOP⟧\n",
                encoding="utf-8",
            )
            return path

        parent_comment = ".refactor-loop/runs/parent-comment.md"
        (self.tmp / parent_comment).write_text("Parent issue: #403\n\nChildren opened.\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        plan_path = self.tmp / ".refactor-loop" / "runs" / "decomposition-plan.json"
        plan_path.write_text(
            json.dumps(
                {
                    "schema": "IssueDecompositionPlan",
                    "parent_issue": 403,
                    "source_consensus_artifact": consensus,
                    "children": [
                        {
                            "slug": "first-child",
                            "title": "First child",
                            "scope": "First bounded scope",
                            "non_goals": "No parent close",
                            "body_artifact_path": write_child("child-one", "First bounded scope", "No parent close"),
                        },
                        {
                            "slug": "second-child",
                            "title": "Second child",
                            "scope": "Second bounded scope",
                            "non_goals": "No public issue factory",
                            "body_artifact_path": write_child("child-two", "Second bounded scope", "No public issue factory"),
                        },
                    ],
                    "parent_update": {"comment_artifact_path": parent_comment},
                }
            ),
            encoding="utf-8",
        )
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:2] == ["issue", "create"]:
                number = 501 + len([call for call in gh_calls if call[:2] == ["issue", "create"]])
                return mock.Mock(returncode=0, stdout=f"https://github.com/owner/repo/issues/{number}\n", stderr="")
            return mock.Mock(returncode=0, stdout="https://github.com/owner/repo/issues/403#issuecomment-1\n", stderr="")

        with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            created = self.actions.apply_issue_decomposition_plan(str(plan_path))

        self.assertEqual((502, "https://github.com/owner/repo/issues/502"), created[0])
        self.assertEqual(3, len(gh_calls))
        creates = [call for call in gh_calls if call[:2] == ["issue", "create"]]
        self.assertEqual(2, len(creates))
        for create in creates:
            self.assertEqual(",".join(labels.design_issue_label_bundle()), create[create.index("--label") + 1])
        self.assertEqual(["issue", "comment", "403", "--body-file", parent_comment], gh_calls[-1])
        forbidden_calls = {("issue", "close"), ("issue", "reopen"), ("issue", "edit")}
        self.assertFalse(any(tuple(call[:2]) in forbidden_calls for call in gh_calls), gh_calls)

    def test_apply_issue_decomposition_plan_reports_parent_comment_failure_after_children_created(self) -> None:
        consensus = ".refactor-loop/runs/phase9-issue403-r6-judge.md"
        (self.tmp / ".refactor-loop" / "runs").mkdir(parents=True, exist_ok=True)
        (self.tmp / consensus).write_text("consensus artifact\n", encoding="utf-8")

        def write_child(name: str, scope: str, non_goals: str) -> str:
            path = f".refactor-loop/runs/{name}.md"
            (self.tmp / path).write_text(
                "## child\n\n"
                "Parent issue: #403\n"
                f"Source consensus artifact: {Path(consensus).name}\n"
                f"Scope: {scope}\n"
                f"Non-goals: {non_goals}\n\n"
                "<details>\n<summary>内联 artifact 1: decision.md</summary>\n\n"
                "```markdown\nraw decision\n```\n\n</details>\n\n"
                "⟦AI:AUTO-LOOP⟧\n",
                encoding="utf-8",
            )
            return path

        parent_comment = ".refactor-loop/runs/parent-comment.md"
        (self.tmp / parent_comment).write_text("Parent issue: #403\n\nChildren opened.\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        plan_path = self.tmp / ".refactor-loop" / "runs" / "decomposition-plan.json"
        plan_path.write_text(
            json.dumps(
                {
                    "schema": "IssueDecompositionPlan",
                    "parent_issue": 403,
                    "source_consensus_artifact": consensus,
                    "children": [
                        {
                            "slug": "first-child",
                            "title": "First child",
                            "scope": "First bounded scope",
                            "non_goals": "No parent close",
                            "body_artifact_path": write_child("child-one", "First bounded scope", "No parent close"),
                        },
                        {
                            "slug": "second-child",
                            "title": "Second child",
                            "scope": "Second bounded scope",
                            "non_goals": "No public issue factory",
                            "body_artifact_path": write_child("child-two", "Second bounded scope", "No public issue factory"),
                        },
                    ],
                    "parent_update": {"comment_artifact_path": parent_comment},
                }
            ),
            encoding="utf-8",
        )
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:2] == ["issue", "create"]:
                number = 600 + len([call for call in gh_calls if call[:2] == ["issue", "create"]])
                return mock.Mock(returncode=0, stdout=f"https://github.com/owner/repo/issues/{number}\n", stderr="")
            if args[:2] == ["issue", "comment"]:
                return mock.Mock(returncode=1, stdout="", stderr="parent comment denied\n")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            with self.assertRaisesRegex(
                RuntimeError,
                "apply_issue_decomposition_plan: parent comment failed: parent comment denied",
            ):
                self.actions.apply_issue_decomposition_plan(str(plan_path))

        self.assertEqual(3, len(gh_calls))
        creates = [call for call in gh_calls if call[:2] == ["issue", "create"]]
        self.assertEqual(2, len(creates))
        self.assertEqual(["issue", "comment", "403", "--body-file", parent_comment], gh_calls[-1])
        for create in creates:
            self.assertEqual(",".join(labels.design_issue_label_bundle()), create[create.index("--label") + 1])
        forbidden_calls = {("issue", "close"), ("issue", "reopen"), ("issue", "edit")}
        self.assertFalse(any(tuple(call[:2]) in forbidden_calls for call in gh_calls), gh_calls)

    def test_apply_issue_decomposition_plan_is_active_controller_only_and_not_public_cli(self) -> None:
        decision = mock.Mock(
            allowed=False,
            owner_device="device-a",
            status="not-owner",
            action="apply-issue-decomposition-plan",
            lease_id="lease-1",
            expires_at="2026-06-01T00:00:00Z",
        )
        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch.object(self.actions, "gh", side_effect=AssertionError("gh should not be called")):
                with self.assertRaisesRegex(RuntimeError, "active_controller=noop:not-owner action=apply-issue-decomposition-plan"):
                    self.actions.apply_issue_decomposition_plan(".refactor-loop/runs/missing-plan.json")

        self.assertNotIn("apply-decomposition", COMMANDS)
        self.assertNotIn("open-child-issue", COMMANDS)
        self.assertNotIn("apply-issue-decomposition-plan", COMMANDS)

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
        self.assertIn("publish-release", self.actor.actions)
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
        self.assertIn("publish-release", self.actor.actions)
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
        return ControllerActions(ctx, github_actor=AllowingGitHubActor())

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

    def test_render_template_inlines_github_post_rules_contract(self) -> None:
        template = self.tmp / "template.md"
        output = self.tmp / "rendered.md"
        template.write_text(f"## GitHub post\n\n{GITHUB_POST_RULES_CONTRACT_TOKEN}\n", encoding="utf-8")

        self.actions.render_template(str(template), str(output))

        rendered = output.read_text(encoding="utf-8")
        self.assertIn("# GitHub post rules", rendered)
        self.assertIn("## Body 结构", rendered)
        self.assertNotIn(GITHUB_POST_RULES_CONTRACT_TOKEN, rendered)
        self.assertNotIn("prompts/_github-post-rules.md", rendered)

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
            'self._require_github_actor_or_raise("post-banner")',
            "build_status_banner(normalized)",
            "tempfile.NamedTemporaryFile",
            "gh_comment_command(normalized, Path(tmp))",
            "self.gh(",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        self.assertLess(method.index('self._require_owner_or_raise("post-banner")'), method.index("_normalize_lifecycle_target_or_raise("))
        self.assertLess(method.index("_normalize_lifecycle_target_or_raise("), method.index('self._require_github_actor_or_raise("post-banner")'))
        self.assertLess(method.index('self._require_github_actor_or_raise("post-banner")'), method.index("tempfile.NamedTemporaryFile"))
        self.assertNotIn("_github_actor_admission_required", text)
        self.assertLess(method.index("tempfile.NamedTemporaryFile"), method.index("self.gh("))

    def test_github_actor_admission_stays_after_active_controller_gate(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        checks = {
            "apply_human_label_or_skip": (
                'self._require_owner_or_return("controller-label", code=3)',
                'self._require_github_actor_or_return("controller-label", code=3)',
                'self.gh(["pr", "edit", pr_target',
            ),
            "publish_release_candidate": (
                'self._require_owner_or_raise("publish-release")',
                'self._require_github_actor_or_raise("publish-release")',
                "publisher.publish(",
            ),
            "post_status_banner": (
                'self._require_owner_or_raise("post-banner")',
                'self._require_github_actor_or_raise("post-banner")',
                "tempfile.NamedTemporaryFile",
            ),
            "merge_pr": (
                'self._require_owner_or_return("merge-pr", code=3)',
                'self._require_github_actor_or_return("merge-pr", code=3)',
                'ready = self._ensure_pr_ready_for_merge(pr_target)',
            ),
            "open_pr_with_label": (
                'self._require_owner_or_raise("open-pr")',
                'self._require_github_actor_or_raise("open-pr")',
                'self.gh(["pr", "create"',
            ),
            "open_design_issue_with_labels": (
                'self._require_owner_or_raise("open-design-issue")',
                'self._require_github_actor_or_raise("open-design-issue")',
                'self.gh(',
            ),
            "apply_issue_decomposition_plan": (
                'self._require_owner_or_raise("apply-issue-decomposition-plan")',
                'self._require_github_actor_or_raise("apply-issue-decomposition-plan")',
                "for child in plan.children:",
            ),
            "apply_triage_decision_marker": (
                'self._require_owner_or_return("apply-triage", code=3)',
                'self._require_github_actor_or_return("apply-triage", code=3)',
                "return apply_decision(",
            ),
            "close_managed_item_from_drop_marker": (
                'self._require_owner_or_return("close-managed-drop", code=3)',
                'self._require_github_actor_or_return("close-managed-drop", code=3)',
                'self.gh(["',
            ),
        }
        for method_name, (owner_gate, actor_gate, first_mutation) in checks.items():
            with self.subTest(method=method_name):
                method = text[text.index(f"    def {method_name}") :]
                next_method = re.search(r"(?m)^    def [a-zA-Z0-9_]+", method[len(f"    def {method_name}") :])
                if next_method:
                    method = method[: len(f"    def {method_name}") + next_method.start()]
                self.assertIn(owner_gate, method)
                self.assertIn(actor_gate, method)
                self.assertIn(first_mutation, method)
                self.assertLess(method.index(owner_gate), method.index(actor_gate))
                self.assertLess(method.index(actor_gate), method.index(first_mutation))

    def test_source_comments_do_not_use_refactor_history_when_policy_none(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        self.assertNotIn("refactor helper", text)
        self.assertNotIn("no behavior change", text)

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
        self.assertNotIn("stale draft PR state", text)
        self.assertIn('"pr", "create", "--draft"', text)
        self.assertIn('def _ensure_pr_ready_for_merge(self, pr_target: str) -> int:', text)
        self.assertIn('"pr", "view", pr_target, "--json", "isDraft", "--jq", ".isDraft"', text)
        self.assertIn('self._live_target_has_managed_label(kind="pr", target=pr_target)', text)
        self.assertIn("CONTROLLER_ACTION_BLOCKED:target-not-managed:merge-pr:pr:", text)
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

    def test_close_managed_drop_source_regression_revalidates_canonical_managed_label(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        method = source[source.index("    def close_managed_item_from_drop_marker") : source.index("    def _live_target_has_managed_label")]
        helper = source[source.index("    def _live_target_has_managed_label") : source.index("    def render_template")]

        self.assertIn("_live_target_has_managed_label", method)
        self.assertIn("CONTROLLER_ACTION_BLOCKED:target-not-managed:close-managed-drop", method)
        self.assertIn('"labels,body"', helper)
        self.assertIn("labels.normalize_label_set", helper)
        self.assertIn("labels.MANAGED", helper)
        self.assertNotIn('"crnd:lifecycle:managed"', method + helper)
        self.assertLess(method.index("_live_target_has_managed_label"), method.index('"issue", "close"'))
        self.assertLess(method.index("_live_target_has_managed_label"), method.index('"pr", "close"'))


if __name__ == "__main__":
    unittest.main()
