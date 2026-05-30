#!/usr/bin/env python3
"""Behavior tests for the Python comment monitor."""

from __future__ import annotations

import json
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

from codex_refactor_loop import labels as label_catalog
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
            self.assertEqual(monitor.targets(), ["1", "2", "3", "8", "11"])

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

        def fake_run(command, cwd, *, check):
            del cwd, check
            calls.append(list(command))
            text = " ".join(command)
            if "reactions" in text:
                return mock.Mock(returncode=0, stdout="", stderr="")
            if "issue comment" in text:
                return mock.Mock(returncode=0, stdout="https://github.com/owner/repo/issues/42#issuecomment-100\n", stderr="")
            return mock.Mock(returncode=1, stdout="", stderr="unexpected")

        with mock.patch("codex_refactor_loop.monitors.comment._run", side_effect=fake_run):
            monitor.handle_comment("42", {"id": 99, "author": "maintainer", "body": "please check", "created_at": "2026-05-29T00:00:00Z"})

        state = json.loads((self.tmp / ".refactor-loop" / "comment-monitor-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["99"], "seen")
        pending = (self.tmp / ".refactor-loop" / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("new-team-comment 42 maintainer 99", pending)
        self.assertTrue(any("reactions" in " ".join(call) for call in calls))

    # Refactor (impl/issue191-single-active-controller): Old pattern: comment
    # monitor instances on multiple devices could all mutate GitHub for one
    # maintainer comment. New principle: non-owner remains read-only/noop.
    def test_non_owner_team_comment_does_not_react_post_or_mark_seen(self) -> None:
        monitor = CommentMonitor(self.ctx, interval=1)
        decision = mock.Mock(allowed=False, owner_device="device-a", status="not-owner", action="comment-monitor-write", lease_id="", expires_at="")

        with mock.patch("codex_refactor_loop.monitors.comment.require_active_controller", return_value=decision):
            with mock.patch.object(monitor, "gh", side_effect=AssertionError("gh should not be called")):
                with mock.patch.object(monitor, "gh_api", side_effect=AssertionError("gh api should not be called")):
                    monitor.handle_comment("42", {"id": 99, "author": "maintainer", "body": "please check"})

        self.assertFalse(monitor.seen("99"))
        self.assertFalse((self.tmp / ".refactor-loop" / ".controller-pending-events.log").exists())

    def test_owner_team_comment_keeps_existing_github_write_path(self) -> None:
        monitor = CommentMonitor(self.ctx, interval=1)
        decision = mock.Mock(allowed=True, owner_device="device-b", status="owner", action="comment-monitor-write", lease_id="lease", expires_at="")
        gh_calls: list[list[str]] = []
        api_calls: list[list[str]] = []

        with mock.patch("codex_refactor_loop.monitors.comment.require_active_controller", return_value=decision):
            with mock.patch.object(
                monitor,
                "gh",
                side_effect=lambda args, check=True: gh_calls.append(list(args)) or subprocess.CompletedProcess(args, 0, "https://github.test/comment\n", ""),
            ):
                with mock.patch.object(
                    monitor,
                    "gh_api",
                    side_effect=lambda args, check=True: api_calls.append(list(args)) or subprocess.CompletedProcess(args, 0, "", ""),
                ):
                    monitor.handle_comment("42", {"id": 99, "author": "maintainer", "body": "please check"})

        self.assertTrue(any("reactions" in call[0] for call in api_calls), api_calls)
        self.assertTrue(any(call[:2] == ["issue", "comment"] for call in gh_calls), gh_calls)
        self.assertTrue(monitor.seen("99"))

    def test_updated_at_unchanged_skips_comments_graphql_query(self) -> None:
        monitor = CommentMonitor(self.ctx, interval=1)
        state_path = self.tmp / ".refactor-loop" / "comment-monitor-state.json"
        state_path.write_text(json.dumps({"_item_updated": {"42": "2026-05-30T00:00:00Z"}}), encoding="utf-8")
        counts = {"comments": 0}

        def fake_graphql(query, variables):
            del variables
            if "comments(last: 20)" in query:
                counts["comments"] += 1
                return mock.Mock(returncode=0, stdout=json.dumps(self._comments_payload([])), stderr="")
            return mock.Mock(returncode=0, stdout=json.dumps(self._search_payload("42", "2026-05-30T00:00:00Z")), stderr="")

        with mock.patch.object(monitor, "_gh_graphql", side_effect=fake_graphql):
            monitor.tick()

        self.assertEqual(counts["comments"], 0)

    def test_updated_at_forward_fetches_once_and_only_processes_new_comments(self) -> None:
        monitor = CommentMonitor(self.ctx, interval=1)
        state_path = self.tmp / ".refactor-loop" / "comment-monitor-state.json"
        state_path.write_text(json.dumps({"100": "seen", "_item_updated": {"42": "2026-05-30T00:00:00Z"}}), encoding="utf-8")
        counts = {"comments": 0}
        reactions: list[str] = []

        def fake_graphql(query, variables):
            del variables
            if "comments(last: 20)" in query:
                counts["comments"] += 1
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(
                        self._comments_payload(
                            [
                                {"databaseId": 100, "author": {"login": "maintainer"}, "body": "old", "createdAt": "2026-05-30T00:00:00Z"},
                                {"databaseId": 101, "author": {"login": "maintainer"}, "body": "new", "createdAt": "2026-05-30T00:01:00Z"},
                            ]
                        )
                    ),
                    stderr="",
                )
            return mock.Mock(returncode=0, stdout=json.dumps(self._search_payload("42", "2026-05-30T00:02:00Z")), stderr="")

        def fake_gh_api(args, *, check=True):
            del check
            if "reactions" in args[0]:
                reactions.append(args[0])
                return mock.Mock(returncode=0, stdout="", stderr="")
            return mock.Mock(returncode=1, stdout="", stderr="unexpected")

        with (
            mock.patch.object(monitor, "_gh_graphql", side_effect=fake_graphql),
            mock.patch.object(monitor, "gh_api", side_effect=fake_gh_api),
            mock.patch.object(monitor, "post_banner") as post_banner,
        ):
            monitor.tick()

        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(counts["comments"], 1)
        self.assertEqual(reactions, ["repos/owner/repo/issues/comments/101/reactions"])
        post_banner.assert_called_once()
        self.assertEqual(state["100"], "seen")
        self.assertEqual(state["101"], "seen")
        self.assertEqual(state["_item_updated"]["42"], "2026-05-30T00:02:00Z")

    def test_first_seen_item_fetches_comments_once(self) -> None:
        monitor = CommentMonitor(self.ctx, interval=1)
        counts = {"comments": 0}

        def fake_graphql(query, variables):
            del variables
            if "comments(last: 20)" in query:
                counts["comments"] += 1
                return mock.Mock(returncode=0, stdout=json.dumps(self._comments_payload([])), stderr="")
            return mock.Mock(returncode=0, stdout=json.dumps(self._search_payload("42", "2026-05-30T00:00:00Z")), stderr="")

        with mock.patch.object(monitor, "_gh_graphql", side_effect=fake_graphql):
            monitor.tick()

        state = json.loads((self.tmp / ".refactor-loop" / "comment-monitor-state.json").read_text(encoding="utf-8"))
        self.assertEqual(counts["comments"], 1)
        self.assertEqual(state["_item_updated"]["42"], "2026-05-30T00:00:00Z")

    def test_last_updated_at_persists_and_reload_skips_unchanged_item(self) -> None:
        counts = {"comments": 0}

        def fake_graphql(query, variables):
            del variables
            if "comments(last: 20)" in query:
                counts["comments"] += 1
                return mock.Mock(returncode=0, stdout=json.dumps(self._comments_payload([])), stderr="")
            return mock.Mock(returncode=0, stdout=json.dumps(self._search_payload("42", "2026-05-30T00:00:00Z")), stderr="")

        first = CommentMonitor(self.ctx, interval=1)
        with mock.patch.object(first, "_gh_graphql", side_effect=fake_graphql):
            first.tick()
        self.assertEqual(counts["comments"], 1)

        second = CommentMonitor(self.ctx, interval=1)
        with mock.patch.object(second, "_gh_graphql", side_effect=fake_graphql):
            second.tick()

        state = json.loads((self.tmp / ".refactor-loop" / "comment-monitor-state.json").read_text(encoding="utf-8"))
        self.assertEqual(counts["comments"], 1)
        self.assertEqual(state["_item_updated"]["42"], "2026-05-30T00:00:00Z")

    def test_failed_comments_query_does_not_advance_last_updated_at(self) -> None:
        monitor = CommentMonitor(self.ctx, interval=1)

        def fake_graphql(query, variables):
            del variables
            if "comments(last: 20)" in query:
                return mock.Mock(returncode=1, stdout="", stderr="rate limited")
            return mock.Mock(returncode=0, stdout=json.dumps(self._search_payload("42", "2026-05-30T00:00:00Z")), stderr="")

        with mock.patch.object(monitor, "_gh_graphql", side_effect=fake_graphql):
            monitor.tick()

        state = json.loads((self.tmp / ".refactor-loop" / "comment-monitor-state.json").read_text(encoding="utf-8"))
        self.assertNotIn("_item_updated", state)

    def test_comment_monitor_lookback_is_limited_to_updated_search_qualifier(self) -> None:
        monitor = CommentMonitor(self.ctx, interval=1)
        captured: list[str] = []

        def fake_graphql(query, variables):
            del query
            captured.append(variables["searchQuery"])
            return mock.Mock(returncode=0, stdout=json.dumps({"data": {"search": {"nodes": []}}}), stderr="")

        with (
            mock.patch.dict(os.environ, {"COMMENT_MONITOR_LOOKBACK": "2026-05-01"}),
            mock.patch.object(monitor, "_gh_graphql", side_effect=fake_graphql),
        ):
            monitor.tick()

        self.assertTrue(captured)
        self.assertTrue(all("updated:>=2026-05-01" in query for query in captured))
        self.assertTrue(all('label:"' in query for query in captured))

        captured.clear()
        with (
            mock.patch.dict(os.environ, {"COMMENT_MONITOR_LOOKBACK": "updated:>=2026-05-02"}),
            mock.patch.object(monitor, "_gh_graphql", side_effect=fake_graphql),
        ):
            monitor.tick()

        self.assertTrue(captured)
        self.assertTrue(all("updated:>=2026-05-02" in query for query in captured))

    def _search_payload(self, number: str, updated_at: str) -> dict[str, object]:
        return {
            "data": {
                "search": {
                    "nodes": [
                        {
                            "number": int(number),
                            "updatedAt": updated_at,
                        }
                    ]
                }
            }
        }

    def _comments_payload(self, nodes: list[dict[str, object]]) -> dict[str, object]:
        return {"data": {"repository": {"issueOrPullRequest": {"comments": {"nodes": nodes}}}}}


class CommentMonitorSourceRegressionTests(unittest.TestCase):
    def test_forbidden_lifecycle_tokens_are_absent(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "monitors" / "comment.py").read_text(encoding="utf-8")
        for token in ("pr merge", "issue close", "git push", "git commit", "create release"):
            with self.subTest(token=token):
                self.assertNotIn(token, text)

    def test_updated_at_comment_query_throttle_is_locked(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "monitors" / "comment.py").read_text(encoding="utf-8")
        self.assertIn("last_updated_at", text)
        self.assertIn("updated_at > previous", text)
        self.assertIn("comments(last: 20)", text)

    def test_comment_monitor_lookback_surface_is_locked(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "monitors" / "comment.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("COMMENT_MONITOR_LOOKBACK", "")', text)
        self.assertIn('raw.startswith("updated:")', text)
        self.assertIn('return f"updated:>={raw}"', text)


if __name__ == "__main__":
    unittest.main()
