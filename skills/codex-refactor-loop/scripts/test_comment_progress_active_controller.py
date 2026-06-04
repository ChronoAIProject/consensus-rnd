#!/usr/bin/env python3
"""Owner-gate tests for comment/progress GitHub mutations."""

from __future__ import annotations

import os
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
from codex_refactor_loop.monitors.comment import CommentMonitor
from codex_refactor_loop.monitors.progress import ProgressReporter


class CommentProgressActiveControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="comment-progress-active-"))
        (self.tmp / ".refactor-loop" / "logs").mkdir(parents=True)
        (self.tmp / ".refactor-loop" / "prompts").mkdir(parents=True)
        (self.tmp / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\nexport MAINTAINER_WHITELIST="maintainer"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def decision(self, allowed: bool) -> mock.Mock:
        return mock.Mock(
            allowed=allowed,
            owner_device="device-a" if not allowed else "device-b",
            status="not-owner" if not allowed else "owner",
            action="write",
            lease_id="lease",
            expires_at="",
        )

    def test_non_owner_comment_monitor_does_not_react_post_or_mark_seen(self) -> None:
        monitor = CommentMonitor(self.ctx)
        gh_calls: list[list[str]] = []
        api_calls: list[list[str]] = []

        with mock.patch("codex_refactor_loop.monitors.comment.require_active_controller", return_value=self.decision(False)):
            with mock.patch.object(monitor, "gh", side_effect=lambda args, check=True: gh_calls.append(list(args)) or subprocess.CompletedProcess(args, 0, "", "")):
                with mock.patch.object(monitor, "gh_api", side_effect=lambda args, check=True: api_calls.append(list(args)) or subprocess.CompletedProcess(args, 0, "", "")):
                    monitor.handle_comment("191", {"id": 123, "author": "maintainer", "body": "please continue"})

        self.assertEqual(gh_calls, [])
        self.assertEqual(api_calls, [])
        self.assertFalse(monitor.seen("123"))

    def test_owner_comment_monitor_keeps_existing_react_and_banner_path(self) -> None:
        monitor = CommentMonitor(self.ctx)
        gh_calls: list[list[str]] = []
        api_calls: list[list[str]] = []

        def fake_gh(args, check=True):
            gh_calls.append(list(args))
            return subprocess.CompletedProcess(args, 0, "https://github.test/comment\n", "")

        def fake_api(args, check=True):
            api_calls.append(list(args))
            return subprocess.CompletedProcess(args, 0, "", "")

        with mock.patch("codex_refactor_loop.monitors.comment.require_active_controller", return_value=self.decision(True)):
            with mock.patch("codex_refactor_loop.monitors.comment.GitHubAuthenticatedActor.require_admission") as admission:
                with mock.patch.object(monitor, "gh", side_effect=fake_gh), mock.patch.object(monitor, "gh_api", side_effect=fake_api):
                    monitor.handle_comment("191", {"id": 123, "author": "maintainer", "body": "please continue"})

        admission.assert_called_once_with("comment-monitor-write")
        self.assertTrue(any("reactions" in call[0] for call in api_calls))
        self.assertTrue(any(call[:2] == ["issue", "comment"] for call in gh_calls))
        self.assertTrue(monitor.seen("123"))

    def test_owner_comment_monitor_fails_closed_when_github_actor_admission_fails(self) -> None:
        monitor = CommentMonitor(self.ctx)

        with mock.patch("codex_refactor_loop.monitors.comment.require_active_controller", return_value=self.decision(True)):
            with mock.patch(
                "codex_refactor_loop.monitors.comment.GitHubAuthenticatedActor.require_admission",
                side_effect=RuntimeError("github-authenticated-actor:comment-monitor-write: denied"),
            ):
                with mock.patch.object(monitor, "gh", side_effect=AssertionError("gh should not be called")):
                    with mock.patch.object(monitor, "gh_api", side_effect=AssertionError("gh api should not be called")):
                        monitor.handle_comment("191", {"id": 123, "author": "maintainer", "body": "please continue"})

        self.assertFalse(monitor.seen("123"))

    def test_non_owner_progress_reporter_does_not_create_edit_or_delete_comments(self) -> None:
        log = self.tmp / ".refactor-loop" / "logs" / "phase9-issue191-r2-minimal.log"
        log.write_text("running\n", encoding="utf-8")
        reporter = ProgressReporter(self.ctx)

        with mock.patch("codex_refactor_loop.monitors.progress.require_active_controller", return_value=self.decision(False)):
            with mock.patch.object(reporter, "gh", side_effect=AssertionError("gh should not be called")):
                with mock.patch.object(reporter, "gh_api", side_effect=AssertionError("gh api should not be called")):
                    reporter.post_or_update(log.stem, log)

        self.assertEqual({}, reporter._state())

    def test_owner_progress_reporter_keeps_existing_comment_path(self) -> None:
        log = self.tmp / ".refactor-loop" / "logs" / "phase9-issue191-r2-minimal.log"
        log.write_text("running\n", encoding="utf-8")
        reporter = ProgressReporter(self.ctx)
        gh_calls: list[list[str]] = []

        def fake_gh(args, check=True):
            gh_calls.append(list(args))
            if args[:2] == ["pr", "view"]:
                return subprocess.CompletedProcess(args, 1, "", "not a pr")
            return subprocess.CompletedProcess(args, 0, "https://github.com/owner/repo/issues/191#issuecomment-55\n", "")

        with mock.patch("codex_refactor_loop.monitors.progress.require_active_controller", return_value=self.decision(True)):
            with mock.patch.object(reporter, "gh", side_effect=fake_gh):
                reporter.post_or_update(log.stem, log)

        self.assertTrue(any(call[:2] == ["issue", "comment"] for call in gh_calls), gh_calls)
        self.assertIn(log.stem, reporter._state())


if __name__ == "__main__":
    unittest.main()
