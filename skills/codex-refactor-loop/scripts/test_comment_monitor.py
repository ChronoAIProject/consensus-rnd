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

from codex_refactor_loop import labels as label_catalog
from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.monitors.comment import CommentMonitor, is_controller_post
from codex_refactor_loop.ownership import OwnershipDecision, WorkTarget


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

    def test_targets_queries_canonical_and_legacy_managed_labels_for_issues_and_prs(self) -> None:
        monitor = CommentMonitor(self.ctx, interval=1)
        responses = {
            ("issue", label_catalog.MANAGED): "8\n2\n",
            ("issue", "auto-loop"): "2\n11\n",
            ("issue", "phase9-auto-solve"): "11\n",
            ("issue", "refactor-design-needed"): "",
            ("pr", label_catalog.MANAGED): "3\n8\n",
            ("pr", "auto-loop"): "1\n3\n",
            ("pr", "phase9-auto-solve"): "",
            ("pr", "refactor-design-needed"): "8\n",
        }
        calls: list[tuple[str, str]] = []

        def fake_run(command, cwd, *, check):
            del cwd, check
            self.assertEqual(command[0], "gh")
            kind = command[1]
            label = command[command.index("--label") + 1]
            calls.append((kind, label))
            return mock.Mock(returncode=0, stdout=responses[(kind, label)], stderr="")

        with mock.patch("codex_refactor_loop.monitors.comment._run", side_effect=fake_run):
            self.assertEqual(
                monitor.targets(),
                [("pr", "1"), ("issue", "2"), ("pr", "3"), ("issue", "8"), ("pr", "8"), ("issue", "11")],
            )

        expected_calls = {
            (kind, label)
            for kind in ("issue", "pr")
            for label in label_catalog.query_labels_for(label_catalog.MANAGED)
        }
        self.assertEqual(set(calls), expected_calls)
        self.assertEqual(len(calls), len(expected_calls))

    def test_team_comment_reacts_appends_pending_event_and_marks_seen(self) -> None:
        monitor = CommentMonitor(self.ctx, interval=1)
        calls: list[list[str]] = []
        owned = OwnershipDecision(True, "owned", WorkTarget("issue", 42), "maintainer", "maintainer", 1.0)

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
            with mock.patch("codex_refactor_loop.monitors.comment.GitHubWorkOwnership") as ownership_cls:
                ownership_cls.return_value.decide.return_value = owned
                monitor.tick()

        state = json.loads((self.tmp / ".refactor-loop" / "comment-monitor-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["99"], "seen")
        pending = (self.tmp / ".refactor-loop" / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("new-team-comment 42 maintainer 99", pending)
        self.assertTrue(any("reactions" in " ".join(call) for call in calls))

    def test_team_comment_fresh_foreign_or_unknown_ownership_fails_closed_without_reaction_or_seen_mark(self) -> None:
        cases = (
            OwnershipDecision(False, "foreign-fresh", WorkTarget("issue", 42), "other", "maintainer", 1.0),
            OwnershipDecision(False, "unknown-current-login", WorkTarget("issue", 42)),
        )
        for decision in cases:
            with self.subTest(reason=decision.reason):
                self._assert_team_comment_ownership_skip(decision)

    def _assert_team_comment_ownership_skip(self, decision: OwnershipDecision) -> None:
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
            return mock.Mock(returncode=1, stdout="", stderr="unexpected")

        with mock.patch("codex_refactor_loop.monitors.comment._run", side_effect=fake_run):
            with mock.patch("codex_refactor_loop.monitors.comment.GitHubWorkOwnership") as ownership_cls:
                ownership_cls.return_value.decide.return_value = decision
                monitor.tick()

        state = json.loads((self.tmp / ".refactor-loop" / "comment-monitor-state.json").read_text(encoding="utf-8"))
        self.assertNotIn("99", state)
        self.assertFalse(any("reactions" in " ".join(call) for call in calls))
        self.assertFalse((self.tmp / ".refactor-loop" / ".controller-pending-events.log").exists())

    def test_team_comment_stale_takeover_still_reacts(self) -> None:
        monitor = CommentMonitor(self.ctx, interval=1)
        calls: list[list[str]] = []
        stale = OwnershipDecision(True, "stale-takeover", WorkTarget("issue", 42), "other", "maintainer", 4.0)

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
                    stdout=json.dumps({"id": 101, "author": "maintainer", "body": "stale check", "created_at": "2026-05-29T00:00:00Z"}) + "\n",
                    stderr="",
                )
            if "reactions" in text:
                return mock.Mock(returncode=0, stdout="", stderr="")
            if "issue comment" in text:
                return mock.Mock(returncode=0, stdout="https://github.com/owner/repo/issues/42#issuecomment-102\n", stderr="")
            return mock.Mock(returncode=1, stdout="", stderr="unexpected")

        with mock.patch("codex_refactor_loop.monitors.comment._run", side_effect=fake_run):
            with mock.patch("codex_refactor_loop.monitors.comment.GitHubWorkOwnership") as ownership_cls:
                ownership_cls.return_value.decide.return_value = stale
                monitor.tick()

        state = json.loads((self.tmp / ".refactor-loop" / "comment-monitor-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["101"], "seen")
        pending = (self.tmp / ".refactor-loop" / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("new-team-comment 42 maintainer 101", pending)
        self.assertTrue(any("reactions" in " ".join(call) for call in calls))

    def test_pr_team_comment_uses_pr_ownership_and_gates_side_effects(self) -> None:
        cases = (
            (
                OwnershipDecision(True, "owned", WorkTarget("pr", 42), "maintainer", "maintainer", 1.0),
                True,
                "201",
            ),
            (
                OwnershipDecision(True, "stale-takeover", WorkTarget("pr", 42), "other", "maintainer", 4.0),
                True,
                "202",
            ),
            (
                OwnershipDecision(False, "foreign-fresh", WorkTarget("pr", 42), "other", "maintainer", 1.0),
                False,
                "203",
            ),
            (
                OwnershipDecision(False, "unknown-current-login", WorkTarget("pr", 42)),
                False,
                "204",
            ),
        )
        for decision, should_process, comment_id in cases:
            with self.subTest(reason=decision.reason):
                self._assert_pr_comment_ownership_gate(decision, should_process, comment_id)

    def _assert_pr_comment_ownership_gate(self, decision: OwnershipDecision, should_process: bool, comment_id: str) -> None:
        monitor = CommentMonitor(self.ctx, interval=1)
        calls: list[list[str]] = []
        pending_path = self.tmp / ".refactor-loop" / ".controller-pending-events.log"
        pending_path.unlink(missing_ok=True)

        def fake_run(command, cwd, *, check):
            del cwd, check
            calls.append(list(command))
            text = " ".join(command)
            if "issue list" in text:
                return mock.Mock(returncode=0, stdout="", stderr="")
            if "pr list" in text:
                return mock.Mock(returncode=0, stdout="42\n", stderr="")
            if "issues/42/comments" in text:
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps({"id": comment_id, "author": "maintainer", "body": "please check pr", "created_at": "2026-05-29T00:00:00Z"}) + "\n",
                    stderr="",
                )
            if "reactions" in text:
                return mock.Mock(returncode=0, stdout="", stderr="")
            if "issue comment" in text:
                return mock.Mock(returncode=1, stdout="", stderr="not an issue")
            if "pr comment" in text:
                return mock.Mock(returncode=0, stdout="https://github.com/owner/repo/pull/42#issuecomment-205\n", stderr="")
            return mock.Mock(returncode=1, stdout="", stderr="unexpected")

        with mock.patch("codex_refactor_loop.monitors.comment._run", side_effect=fake_run):
            with mock.patch("codex_refactor_loop.monitors.comment.GitHubWorkOwnership") as ownership_cls:
                ownership_cls.return_value.decide.return_value = decision
                monitor.tick()
                ownership_cls.return_value.decide.assert_called_once_with(WorkTarget("pr", 42))

        state = json.loads((self.tmp / ".refactor-loop" / "comment-monitor-state.json").read_text(encoding="utf-8"))
        if should_process:
            self.assertEqual(state[comment_id], "seen")
            pending = pending_path.read_text(encoding="utf-8")
            self.assertIn(f"new-team-comment 42 maintainer {comment_id}", pending)
            self.assertTrue(any("reactions" in " ".join(call) for call in calls))
        else:
            self.assertNotIn(comment_id, state)
            self.assertFalse(any("reactions" in " ".join(call) for call in calls))
            self.assertFalse(pending_path.exists())


class CommentMonitorSourceRegressionTests(unittest.TestCase):
    def test_forbidden_lifecycle_tokens_are_absent(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "monitors" / "comment.py").read_text(encoding="utf-8")
        for token in ("pr merge", "issue close", "git push", "git commit", "create release"):
            with self.subTest(token=token):
                self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main()
