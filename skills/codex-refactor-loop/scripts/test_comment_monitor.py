#!/usr/bin/env python3
"""Behavior tests for the Python comment monitor."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.monitors.comment import CommentMonitor, is_controller_post


class CommentMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="comment-monitor-test-"))
        (self.tmp / ".refactor-loop").mkdir()
        (self.tmp / ".refactor-loop" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\nexport MAINTAINER_WHITELIST="maintainer"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fails_closed_without_maintainer_whitelist(self) -> None:
        (self.tmp / ".refactor-loop" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\n',
            encoding="utf-8",
        )
        ctx = LoopContext.load(repo_root=self.tmp)
        with self.assertRaisesRegex(RuntimeError, "MAINTAINER_WHITELIST"):
            CommentMonitor(ctx)

    def test_controller_post_filter_covers_sentinel_and_banner_prefix(self) -> None:
        self.assertTrue(is_controller_post("hello", "body\n⟦AI:AUTO-LOOP⟧"))
        self.assertTrue(is_controller_post("## 📊 status", "body"))
        self.assertFalse(is_controller_post("plain maintainer note", "plain maintainer note"))

    def test_team_comment_reacts_appends_pending_event_and_marks_seen(self) -> None:
        monitor = CommentMonitor(self.ctx, interval=1)
        calls: list[list[str]] = []

        def fake_run(command, cwd, *, check):
            del cwd, check
            calls.append(list(command))
            text = " ".join(command)
            if "issue list" in text:
                return mock.Mock(returncode=0, stdout="42\n", stderr="")
            if "pr list" in text:
                return mock.Mock(returncode=0, stdout="", stderr="")
            if "issues/42/comments" in text:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps({"id": 99, "author": "maintainer", "body": "please check", "created_at": "2026-05-29T00:00:00Z"}) + "\n",
                    stderr="",
                )
            if "reactions" in text:
                return mock.Mock(returncode=0, stdout="", stderr="")
            if "issue comment" in text:
                return mock.Mock(returncode=0, stdout="https://github.com/owner/repo/issues/42#issuecomment-100\n", stderr="")
            return mock.Mock(returncode=1, stdout="", stderr="unexpected")

        with mock.patch("codex_refactor_loop.monitors.comment._run", side_effect=fake_run):
            monitor.tick()

        state = json.loads((self.tmp / ".refactor-loop" / "comment-monitor-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["99"], "seen")
        pending = (self.tmp / ".refactor-loop" / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("new-team-comment 42 maintainer 99", pending)
        self.assertTrue(any("reactions" in " ".join(call) for call in calls))


class CommentMonitorSourceRegressionTests(unittest.TestCase):
    def test_forbidden_lifecycle_tokens_are_absent(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "monitors" / "comment.py").read_text(encoding="utf-8")
        for token in ("pr merge", "issue close", "git push", "git commit", "create release"):
            with self.subTest(token=token):
                self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main()
