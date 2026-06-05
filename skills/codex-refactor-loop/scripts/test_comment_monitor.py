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
from test_support.authorization_projection import project_python


def comment_monitor_projection():
    text = (SCRIPT_DIR / "codex_refactor_loop" / "monitors" / "comment.py").read_text(encoding="utf-8")
    return project_python(text)


class CommentMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="comment-monitor-test-"))
        (self.tmp / ".refactor-loop").mkdir()
        (self.tmp / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\nexport MAINTAINER_WHITELIST="maintainer"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fails_closed_without_maintainer_whitelist(self) -> None:
        (self.tmp / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\n',
            encoding="utf-8",
        )
        ctx = LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})
        with self.assertRaisesRegex(RuntimeError, "MAINTAINER_WHITELIST"):
            CommentMonitor(ctx)

    def test_controller_post_filter_covers_sentinel_and_banner_prefix(self) -> None:
        self.assertTrue(is_controller_post("hello", "body\n⟦AI:AUTO-LOOP⟧"))
        self.assertTrue(is_controller_post("## 📊 status", "body"))
        self.assertFalse(is_controller_post("plain maintainer note", "plain maintainer note"))

    def test_generic_env_overrides_do_not_change_default_state_file_or_interval(self) -> None:
        override_root = self.tmp / "override"
        with mock.patch.dict(
            os.environ,
            {
                "COMMENT_MONITOR_INTERVAL": "",
                "STATE_FILE": str(override_root / "state.json"),
                "INTERVAL": "1",
                "COMMENT_MONITOR_INTERVAL": "",
            },
        ):
            monitor = CommentMonitor(self.ctx)

        self.assertEqual(self.ctx.paths.refactor_loop / "comment-monitor-state.json", monitor.state_file)
        self.assertEqual(30, monitor.interval)
        self.assertFalse(override_root.exists())

    def test_comment_monitor_interval_registered_env_still_applies(self) -> None:
        with mock.patch.dict(os.environ, {"COMMENT_MONITOR_INTERVAL": "45", "INTERVAL": "1"}):
            monitor = CommentMonitor(self.ctx)

        self.assertEqual(45, monitor.interval)

    def test_explicit_state_file_and_interval_parameters_remain_test_seams(self) -> None:
        state_file = self.tmp / "explicit-state.json"
        with mock.patch.dict(os.environ, {"COMMENT_MONITOR_INTERVAL": "45", "STATE_FILE": str(self.tmp / "ignored.json")}):
            monitor = CommentMonitor(self.ctx, state_file=state_file, interval=9)

        self.assertEqual(state_file, monitor.state_file)
        self.assertEqual(9, monitor.interval)

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
            if command == ["gh", "api", "user"]:
                return mock.Mock(returncode=0, stdout=json.dumps({"login": "controller-bot"}), stderr="")
            if command == ["gh", "api", "repos/owner/repo/collaborators/controller-bot/permission"]:
                return mock.Mock(returncode=0, stdout=json.dumps({"permission": "write"}), stderr="")
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
            with mock.patch("codex_refactor_loop.monitors.comment.GitHubAuthenticatedActor.require_admission") as admission:
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

        admission.assert_called_once_with("comment-monitor-write")
        self.assertTrue(any("reactions" in call[0] for call in api_calls), api_calls)
        self.assertTrue(any(call[:2] == ["issue", "comment"] for call in gh_calls), gh_calls)
        self.assertTrue(monitor.seen("99"))

    def test_updated_at_unchanged_skips_comments_rest_query(self) -> None:
        monitor = CommentMonitor(self.ctx, interval=1)
        state_path = self.tmp / ".refactor-loop" / "comment-monitor-state.json"
        state_path.write_text(json.dumps({"_item_updated": {"42": "2026-05-30T00:00:00Z"}}), encoding="utf-8")
        calls: list[str] = []

        def fake_gh_api(args, *, check=True):
            del check
            calls.append(args[0])
            if args[0].endswith("/comments?per_page=20"):
                return mock.Mock(returncode=0, stdout=json.dumps([]), stderr="")
            return mock.Mock(returncode=0, stdout=json.dumps([self._active_item("42", "2026-05-30T00:00:00Z")]), stderr="")

        with mock.patch.object(monitor, "gh_api", side_effect=fake_gh_api):
            monitor.tick()

        self.assertFalse(any(call.endswith("/comments?per_page=20") for call in calls))

    def test_updated_at_forward_fetches_once_and_only_processes_new_comments(self) -> None:
        monitor = CommentMonitor(self.ctx, interval=1)
        state_path = self.tmp / ".refactor-loop" / "comment-monitor-state.json"
        state_path.write_text(json.dumps({"100": "seen", "_item_updated": {"42": "2026-05-30T00:00:00Z"}}), encoding="utf-8")
        calls: list[str] = []
        reactions: list[str] = []

        def fake_gh_api(args, *, check=True):
            del check
            calls.append(args[0])
            if args[0].endswith("/comments?per_page=20"):
                return mock.Mock(
                    returncode=0,
                    stdout=json.dumps(
                        [
                            {"id": 100, "user": {"login": "maintainer"}, "body": "old", "created_at": "2026-05-30T00:00:00Z"},
                            {"id": 101, "user": {"login": "maintainer"}, "body": "new", "created_at": "2026-05-30T00:01:00Z"},
                        ]
                    ),
                    stderr="",
                )
            if "reactions" in args[0]:
                reactions.append(args[0])
                return mock.Mock(returncode=0, stdout="", stderr="")
            return mock.Mock(returncode=0, stdout=json.dumps([self._active_item("42", "2026-05-30T00:02:00Z")]), stderr="")

        with (
            mock.patch.object(monitor, "gh_api", side_effect=fake_gh_api),
            mock.patch("codex_refactor_loop.monitors.comment.GitHubAuthenticatedActor.require_admission"),
            mock.patch.object(monitor, "post_banner") as post_banner,
        ):
            monitor.tick()

        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(sum(call.endswith("/comments?per_page=20") for call in calls), 1)
        self.assertEqual(reactions, ["repos/owner/repo/issues/comments/101/reactions"])
        post_banner.assert_called_once()
        self.assertEqual(state["100"], "seen")
        self.assertEqual(state["101"], "seen")
        self.assertEqual(state["_item_updated"]["42"], "2026-05-30T00:02:00Z")

    def test_first_seen_item_fetches_comments_once(self) -> None:
        monitor = CommentMonitor(self.ctx, interval=1)
        calls: list[str] = []

        def fake_gh_api(args, *, check=True):
            del check
            calls.append(args[0])
            if args[0].endswith("/comments?per_page=20"):
                return mock.Mock(returncode=0, stdout=json.dumps([]), stderr="")
            return mock.Mock(returncode=0, stdout=json.dumps([self._active_item("42", "2026-05-30T00:00:00Z")]), stderr="")

        with mock.patch.object(monitor, "gh_api", side_effect=fake_gh_api):
            monitor.tick()

        state = json.loads((self.tmp / ".refactor-loop" / "comment-monitor-state.json").read_text(encoding="utf-8"))
        self.assertEqual(sum(call.endswith("/comments?per_page=20") for call in calls), 1)
        self.assertEqual(state["_item_updated"]["42"], "2026-05-30T00:00:00Z")

    def test_last_updated_at_persists_and_reload_skips_unchanged_item(self) -> None:
        calls: list[str] = []

        def fake_gh_api(args, *, check=True):
            del check
            calls.append(args[0])
            if args[0].endswith("/comments?per_page=20"):
                return mock.Mock(returncode=0, stdout=json.dumps([]), stderr="")
            return mock.Mock(returncode=0, stdout=json.dumps([self._active_item("42", "2026-05-30T00:00:00Z")]), stderr="")

        first = CommentMonitor(self.ctx, interval=1)
        with mock.patch.object(first, "gh_api", side_effect=fake_gh_api):
            first.tick()
        self.assertEqual(sum(call.endswith("/comments?per_page=20") for call in calls), 1)

        second = CommentMonitor(self.ctx, interval=1)
        with mock.patch.object(second, "gh_api", side_effect=fake_gh_api):
            second.tick()

        state = json.loads((self.tmp / ".refactor-loop" / "comment-monitor-state.json").read_text(encoding="utf-8"))
        self.assertEqual(sum(call.endswith("/comments?per_page=20") for call in calls), 1)
        self.assertEqual(state["_item_updated"]["42"], "2026-05-30T00:00:00Z")

    def test_failed_comments_query_does_not_advance_last_updated_at(self) -> None:
        monitor = CommentMonitor(self.ctx, interval=1)

        def fake_gh_api(args, *, check=True):
            del check
            if args[0].endswith("/comments?per_page=20"):
                return mock.Mock(returncode=1, stdout="", stderr="rate limited")
            return mock.Mock(returncode=0, stdout=json.dumps([self._active_item("42", "2026-05-30T00:00:00Z")]), stderr="")

        with mock.patch.object(monitor, "gh_api", side_effect=fake_gh_api):
            monitor.tick()

        state = json.loads((self.tmp / ".refactor-loop" / "comment-monitor-state.json").read_text(encoding="utf-8"))
        self.assertNotIn("_item_updated", state)

    def test_comment_monitor_lookback_is_limited_to_updated_search_qualifier(self) -> None:
        monitor = CommentMonitor(self.ctx, interval=1)
        captured: list[str] = []

        def fake_gh_api(args, *, check=True):
            del check
            captured.append(args[0])
            return mock.Mock(returncode=0, stdout=json.dumps([self._active_item("42", "2026-04-30T00:00:00Z")]), stderr="")

        with (
            mock.patch.dict(os.environ, {"COMMENT_MONITOR_LOOKBACK": "2026-05-01"}),
            mock.patch.object(monitor, "gh_api", side_effect=fake_gh_api),
        ):
            monitor.tick()

        self.assertTrue(captured)
        self.assertTrue(all("/issues?state=open&labels=" in query for query in captured))
        self.assertEqual(monitor._last_updated_at(), {})

        captured.clear()
        with (
            mock.patch.dict(os.environ, {"COMMENT_MONITOR_LOOKBACK": "updated:>=2026-05-02"}),
            mock.patch.object(monitor, "gh_api", side_effect=fake_gh_api),
        ):
            monitor.tick()

        self.assertTrue(captured)
        self.assertEqual(monitor._last_updated_at(), {})

    def _active_item(self, number: str, updated_at: str) -> dict[str, object]:
        return {"number": int(number), "updated_at": updated_at}


class CommentMonitorSourceRegressionTests(unittest.TestCase):
    def test_forbidden_lifecycle_tokens_are_absent(self) -> None:
        projection = comment_monitor_projection()
        for token in ("pr merge", "issue close", "git push", "git commit", "create release"):
            with self.subTest(token=token):
                self.assertNotIn(token, projection.string_literals)

    def test_updated_at_comment_query_throttle_is_locked(self) -> None:
        projection = comment_monitor_projection()
        self.assertIn("_last_updated_at", projection.function_names)
        self.assertIn("_should_fetch_comments", projection.function_names)
        self.assertTrue(any(value.endswith("/comments?per_page=20") for value in projection.string_literals))
        self.assertNotIn("_gh_graphql", projection.function_names)
        self.assertNotIn("graphql", {value.lower() for value in projection.string_literals})

    def test_comment_monitor_lookback_surface_is_locked(self) -> None:
        projection = comment_monitor_projection()
        self.assertIn("COMMENT_MONITOR_LOOKBACK", projection.env_get_names)
        self.assertIn("updated:", projection.string_literals)
        self.assertIn("updated:>=", projection.string_literals)
        self.assertIn("_lookback_minimum_updated_at", projection.function_names)

    def test_generic_env_override_surfaces_are_not_consumed(self) -> None:
        projection = comment_monitor_projection()
        for env_name in ("STATE_FILE", "INTERVAL", "PROGRESS_REPORTER_INTERVAL"):
            with self.subTest(env_name=env_name):
                self.assertNotIn(env_name, projection.env_get_names)
        self.assertIn("COMMENT_MONITOR_INTERVAL", projection.env_get_names)


if __name__ == "__main__":
    unittest.main()
