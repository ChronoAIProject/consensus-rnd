#!/usr/bin/env python3
"""Behavior tests for the Python progress reporter."""

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
from codex_refactor_loop.monitors.progress import ProgressReporter, exit_status, run_progress_reporter_reconcile_tick


class ProgressReporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="progress-reporter-test-"))
        (self.tmp / ".refactor-loop" / "logs").mkdir(parents=True)
        (self.tmp / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_exit_status_is_terminal_tail_tristate(self) -> None:
        cases = {
            "in_flight": "EXIT=0\nfiller\nfiller\nfiller\nfiller\nfiller\n",
            "exit_ok": "work\nEXIT=0\nDONE_AT=now\n",
            "exit_failed": "work\nEXIT=17\nDONE_AT=now\n",
        }
        for expected, text in cases.items():
            path = self.tmp / f"{expected}.log"
            path.write_text(text, encoding="utf-8")
            self.assertEqual(expected, exit_status(path))

    def test_generic_env_overrides_do_not_change_default_runtime_paths_or_interval(self) -> None:
        override_root = self.tmp / "override"
        with mock.patch.dict(
            os.environ,
            {
                "INTERVAL": "1",
                "STATE_DIR": str(override_root / "state"),
                "STATE_FILE": str(override_root / "state.json"),
                "LOG_DIR": str(override_root / "logs"),
                "PROMPTS_DIR": str(override_root / "prompts"),
            },
        ):
            reporter = ProgressReporter(self.ctx)

        self.assertEqual(600, reporter.interval)
        self.assertEqual(self.ctx.paths.refactor_loop, reporter.state_dir)
        self.assertEqual(self.ctx.paths.refactor_loop / "codex-progress-state.json", reporter.state_file)
        self.assertEqual(self.ctx.paths.refactor_loop / "logs", reporter.log_dir)
        self.assertFalse(override_root.exists())

    def test_explicit_interval_parameter_remains_test_seam(self) -> None:
        with mock.patch.dict(os.environ, {"INTERVAL": "1"}):
            reporter = ProgressReporter(self.ctx, interval=7)

        self.assertEqual(7, reporter.interval)

    def test_worker_log_status_does_not_create_edit_delete_or_read_progress_comments(self) -> None:
        log = self.tmp / ".refactor-loop" / "logs" / "fix-pr47-round2.log"
        log.write_text("important failure\nEXIT=17\n", encoding="utf-8")
        reporter = ProgressReporter(self.ctx)

        self.assertFalse(hasattr(reporter, "gh"))
        with mock.patch.object(reporter, "gh_api", side_effect=AssertionError("per-worker gh api comments are deleted")):
            reporter.record_worker_log_status("fix-pr47-round2", log)

        self.assertEqual({}, reporter._state())

    def test_tick_scans_worker_logs_without_per_worker_github_calls(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "phase9-issue81-r10-minimal.log").write_text("running\n", encoding="utf-8")
        reporter = ProgressReporter(self.ctx)

        with mock.patch("codex_refactor_loop.monitors.progress.graphql_headroom_ok", return_value=True):
            with mock.patch.object(reporter, "sync_global_status_card") as sync_global_status_card:
                self.assertFalse(hasattr(reporter, "gh"))
                with mock.patch.object(reporter, "gh_api", side_effect=AssertionError("per-worker gh api comments are deleted")):
                    run_progress_reporter_reconcile_tick(reporter)

        sync_global_status_card.assert_called_once()
        self.assertEqual({}, reporter._state())

    def test_global_status_card_disabled_noops_without_github(self) -> None:
        reporter = ProgressReporter(self.ctx)

        with mock.patch.object(reporter, "gh_api", side_effect=AssertionError("gh api should not be called")):
            reporter.sync_global_status_card()

        self.assertEqual({}, reporter._state())

    def test_global_status_card_non_owner_noops_without_patch(self) -> None:
        ctx = self._ctx_with_global_status()
        reporter = ProgressReporter(ctx)
        decision = mock.Mock(allowed=False, owner_device="device-a", status="not-owner", action="global-dashboard-status-card", lease_id="", expires_at="")

        with mock.patch("codex_refactor_loop.monitors.progress.require_active_controller", return_value=decision):
            with mock.patch.object(reporter, "gh_api", side_effect=AssertionError("patch should not be called")):
                reporter.sync_global_status_card()

        self.assertEqual({}, reporter._state())

    def test_global_status_card_same_hash_skip_does_not_patch(self) -> None:
        ctx = self._ctx_with_global_status(extra='export HOST_HOLISTIC_STATUS_INTERVAL_SECONDS="0"\n')
        reporter = ProgressReporter(ctx)
        body = "stable body\n"
        state_file = self.tmp / ".refactor-loop" / "codex-progress-state.json"
        state_file.write_text(
            json.dumps({"__global_dashboard_status_card__": {"last_md5": "placeholder", "last_synced_at": 0}}),
            encoding="utf-8",
        )
        state = json.loads(state_file.read_text(encoding="utf-8"))
        state["__global_dashboard_status_card__"]["last_md5"] = __import__(
            "codex_refactor_loop.monitors.progress", fromlist=["hash_body"]
        ).hash_body(body)
        state_file.write_text(json.dumps(state), encoding="utf-8")
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="global-dashboard-status-card", lease_id="", expires_at="")

        with mock.patch("codex_refactor_loop.monitors.progress.require_active_controller", return_value=decision):
            with mock.patch.object(reporter, "build_global_status_body", return_value=body):
                with mock.patch.object(reporter, "gh_api", side_effect=AssertionError("same hash should not patch")):
                    reporter.sync_global_status_card()

        self.assertEqual("issue-comment", reporter._state()["__global_dashboard_status_card__"]["kind"])

    def test_global_status_card_patches_fixed_issue_comment_only_after_return_validation(self) -> None:
        ctx = self._ctx_with_global_status()
        reporter = ProgressReporter(ctx)
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="global-dashboard-status-card", lease_id="", expires_at="")
        calls: list[list[str]] = []

        def fake_gh_api(args, check=True):
            del check
            calls.append(list(args))
            return mock.Mock(returncode=0, stdout=json.dumps({"id": 123, "html_url": "https://github.test/x#issuecomment-123"}), stderr="")

        with mock.patch("codex_refactor_loop.monitors.progress.require_active_controller", return_value=decision):
            with mock.patch.object(reporter, "build_global_status_body", return_value="changed body\n"):
                self.assertFalse(hasattr(reporter, "gh"))
                with mock.patch.object(reporter, "gh_api", side_effect=fake_gh_api):
                    reporter.sync_global_status_card()

        self.assertEqual(1, len(calls))
        self.assertEqual(["-X", "PATCH", "repos/owner/repo/issues/comments/123"], calls[0][:3])
        self.assertTrue(calls[0][3].startswith("-F"))
        state = reporter._state()["__global_dashboard_status_card__"]
        self.assertEqual("issue-comment", state["kind"])
        self.assertEqual("9", state["target"])
        self.assertEqual("123", state["comment_id"])

    def test_global_status_card_rejects_wrong_patch_object_and_records_secondary_backoff(self) -> None:
        ctx = self._ctx_with_global_status()
        reporter = ProgressReporter(ctx)
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="global-dashboard-status-card", lease_id="", expires_at="")
        result = mock.Mock(
            returncode=1,
            stdout="",
            stderr="You have exceeded a secondary rate limit and have been temporarily blocked from content creation",
        )

        with mock.patch("codex_refactor_loop.monitors.progress.require_active_controller", return_value=decision):
            with mock.patch.object(reporter, "build_global_status_body", return_value="changed body\n"):
                with mock.patch.object(reporter, "gh_api", return_value=result):
                    reporter.sync_global_status_card()

        backoff = json.loads((self.tmp / ".refactor-loop/state/secondary-mutation-backoff.json").read_text(encoding="utf-8"))
        self.assertEqual("secondary-content-creation-limit", backoff["contentCreation"]["reason"])
        self.assertNotIn("__global_dashboard_status_card__", reporter._state())

    def _ctx_with_global_status(self, *, extra: str = "") -> LoopContext:
        host_env = self.tmp / ".config" / "consensus-rnd" / "host.env"
        host_env.write_text(
            f'export REPO_ROOT="{self.tmp}"\n'
            'export GH_REPO_SLUG="owner/repo"\n'
            'export HOST_HOLISTIC_STATUS_ENABLE="true"\n'
            'export HOST_HOLISTIC_STATUS_ISSUE_NUMBER="9"\n'
            'export HOST_HOLISTIC_STATUS_COMMENT_ID="123"\n'
            f"{extra}",
            encoding="utf-8",
        )
        return LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})


class ProgressReporterSourceRegressionTests(unittest.TestCase):
    def test_forbidden_lifecycle_tokens_are_absent(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "monitors" / "progress.py").read_text(encoding="utf-8")
        for token in ("git commit", "git push", "pr merge", "issue close", "create release"):
            with self.subTest(token=token):
                self.assertNotIn(token, text)
        self.assertIn("TEST_NO_LOOP", text)

    def test_global_status_writer_source_boundary(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "monitors" / "progress.py").read_text(encoding="utf-8")

        for token in (
            "global-dashboard-status-card",
            "HOST_HOLISTIC_STATUS_ENABLE",
            "HOST_HOLISTIC_STATUS_ISSUE_NUMBER",
            "HOST_HOLISTIC_STATUS_COMMENT_ID",
            "HOST_HOLISTIC_STATUS_INTERVAL_SECONDS",
            "require_active_controller",
            '"-X", "PATCH"',
            "issues/comments",
            "render_holistic_markdown",
            "_valid_fixed_comment_patch",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)
        for forbidden in ("discussion", "gh issue comment", "gh pr comment", "issue/body", "pulls/comments"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text.lower())
        executable = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        for token in (
            'os.environ.get("INTERVAL"',
            'os.environ.get("STATE_DIR"',
            'os.environ.get("STATE_FILE"',
            'os.environ.get("LOG_DIR"',
            'os.environ.get("PROMPTS_DIR"',
            "PROGRESS_REPORTER_INTERVAL",
            "extract_tail",
            "parse_target",
            "parse_kind",
            "_create_comment",
            "post_or_update",
            "prompts_dir",
            '"DELETE"',
            '"POST"',
            '["issue", "comment"',
            '["pr", "comment"',
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, executable)


if __name__ == "__main__":
    unittest.main()
