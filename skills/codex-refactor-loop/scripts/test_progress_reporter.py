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
from codex_refactor_loop.monitors.progress import ProgressReporter, exit_status


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
        self.assertEqual(self.ctx.paths.refactor_loop / "prompts", reporter.prompts_dir)
        self.assertFalse(override_root.exists())

    def test_explicit_interval_parameter_remains_test_seam(self) -> None:
        with mock.patch.dict(os.environ, {"INTERVAL": "1"}):
            reporter = ProgressReporter(self.ctx, interval=7)

        self.assertEqual(7, reporter.interval)

    def test_exit_failed_posts_and_keeps_failed_state(self) -> None:
        log = self.tmp / ".refactor-loop" / "logs" / "fix-pr47-round2.log"
        log.write_text("important failure\nEXIT=17\n", encoding="utf-8")
        reporter = ProgressReporter(self.ctx)

        def fake_run(command, cwd, *, check):
            del cwd, check
            text = " ".join(command)
            if "api repos/owner/repo/issues/47" in text:
                return mock.Mock(returncode=0, stdout=json.dumps({"pull_request": {}}), stderr="")
            if "pr comment 47" in text:
                return mock.Mock(returncode=0, stdout="https://github.com/owner/repo/pull/47#issuecomment-24680\n", stderr="")
            return mock.Mock(returncode=1, stdout="", stderr="")

        with mock.patch("codex_refactor_loop.monitors.progress._run", side_effect=fake_run):
            reporter.post_or_update("fix-pr47-round2", log)
            reporter.post_or_update("fix-pr47-round2", log)

        state = json.loads((self.tmp / ".refactor-loop" / "codex-progress-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["fix-pr47-round2"]["finished"], "failed")
        self.assertEqual(state["fix-pr47-round2"]["comment_id"], 24680)

    def test_in_flight_progress_body_omits_raw_log_tail(self) -> None:
        log = self.tmp / ".refactor-loop" / "logs" / "phase9-issue81-r10-minimal.log"
        log.write_text("secret raw worker prose\nSOLVER_DONE:minimal:echo\n", encoding="utf-8")
        reporter = ProgressReporter(self.ctx)

        body = reporter.build_body(log.stem, log, "false")

        self.assertIn("Task id: `phase9-issue81-r10-minimal`", body)
        self.assertIn("Raw log tail is intentionally omitted", body)
        self.assertNotIn("secret raw worker prose", body)
        self.assertNotIn("SOLVER_DONE:minimal:echo", body)

    def test_failed_progress_body_keeps_bounded_tail_as_exception_diagnostic(self) -> None:
        log = self.tmp / ".refactor-loop" / "logs" / "fix-pr47-round2.log"
        log.write_text("important failure diagnostic\nEXIT=17\n", encoding="utf-8")
        reporter = ProgressReporter(self.ctx)

        body = reporter.build_body(log.stem, log, "failed")

        self.assertIn("异常诊断 tail (non-zero EXIT only)", body)
        self.assertIn("important failure diagnostic", body)

    def test_orphan_delete_retry_keeps_state_when_delete_fails_and_comment_exists(self) -> None:
        state_file = self.tmp / ".refactor-loop" / "codex-progress-state.json"
        state_file.write_text(json.dumps({"fix-pr47-r1": {"target": "47", "kind": "pr", "comment_id": 123, "last_md5": "x", "finished": "false"}}), encoding="utf-8")
        log = self.tmp / ".refactor-loop" / "logs" / "fix-pr47-r1.log"
        log.write_text("done\nEXIT=0\n", encoding="utf-8")
        reporter = ProgressReporter(self.ctx)

        def fake_run(command, cwd, *, check):
            del cwd, check
            text = " ".join(command)
            if "-X DELETE" in text:
                return mock.Mock(returncode=1, stdout="", stderr="rate limit")
            if "issues/comments/123" in text:
                return mock.Mock(returncode=0, stdout="{}", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("codex_refactor_loop.monitors.progress._run", side_effect=fake_run):
            reporter.post_or_update("fix-pr47-r1", log)

        state = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["fix-pr47-r1"]["comment_id"], 123)
        self.assertEqual(state["fix-pr47-r1"]["finished"], "false")

    # Refactor (impl/issue191-single-active-controller): Old pattern:
    # progress reporters on multiple devices could create/edit/delete GitHub
    # comments. New principle: non-owner progress reporter does not mutate
    # GitHub state.
    def test_non_owner_progress_reporter_does_not_call_github_or_write_state(self) -> None:
        log = self.tmp / ".refactor-loop" / "logs" / "phase9-issue191-r2-minimal.log"
        log.write_text("running\n", encoding="utf-8")
        reporter = ProgressReporter(self.ctx)
        decision = mock.Mock(allowed=False, owner_device="device-a", status="not-owner", action="progress-reporter-write", lease_id="", expires_at="")

        with mock.patch("codex_refactor_loop.monitors.progress.require_active_controller", return_value=decision):
            with mock.patch.object(reporter, "gh", side_effect=AssertionError("gh should not be called")):
                with mock.patch.object(reporter, "gh_api", side_effect=AssertionError("gh api should not be called")):
                    reporter.post_or_update(log.stem, log)

        self.assertEqual({}, reporter._state())

    def test_owner_progress_reporter_keeps_existing_create_comment_path(self) -> None:
        log = self.tmp / ".refactor-loop" / "logs" / "phase9-issue191-r2-minimal.log"
        log.write_text("running\n", encoding="utf-8")
        reporter = ProgressReporter(self.ctx)
        decision = mock.Mock(allowed=True, owner_device="device-b", status="owner", action="progress-reporter-write", lease_id="lease", expires_at="")
        gh_calls: list[list[str]] = []

        def fake_gh(args, check=True):
            gh_calls.append(list(args))
            return mock.Mock(returncode=0, stdout="https://github.com/owner/repo/issues/191#issuecomment-55\n", stderr="")

        with mock.patch("codex_refactor_loop.monitors.progress.require_active_controller", return_value=decision):
            with mock.patch.object(reporter, "gh_api", return_value=mock.Mock(returncode=0, stdout=json.dumps({}), stderr="")):
                with mock.patch.object(reporter, "gh", side_effect=fake_gh):
                    reporter.post_or_update(log.stem, log)

        self.assertTrue(any(call[:2] == ["issue", "comment"] for call in gh_calls), gh_calls)
        self.assertIn(log.stem, reporter._state())

    def test_parse_kind_uses_rest_issue_pull_request_projection(self) -> None:
        reporter = ProgressReporter(self.ctx)
        calls: list[list[str]] = []

        def fake_gh_api(args, check=True):
            del check
            calls.append(list(args))
            if args == ["repos/owner/repo/issues/47"]:
                return mock.Mock(returncode=0, stdout=json.dumps({"number": 47, "pull_request": {"url": "x"}}), stderr="")
            if args == ["repos/owner/repo/issues/48"]:
                return mock.Mock(returncode=0, stdout=json.dumps({"number": 48}), stderr="")
            return mock.Mock(returncode=1, stdout="", stderr="")

        with mock.patch.object(reporter, "gh_api", side_effect=fake_gh_api):
            self.assertEqual("pr", reporter.parse_kind("47"))
            self.assertEqual("issue", reporter.parse_kind("48"))
            self.assertEqual("issue", reporter.parse_kind("49"))

        self.assertEqual(
            calls,
            [
                ["repos/owner/repo/issues/47"],
                ["repos/owner/repo/issues/48"],
                ["repos/owner/repo/issues/49"],
            ],
        )

    def test_parse_kind_does_not_use_pr_view(self) -> None:
        reporter = ProgressReporter(self.ctx)

        with mock.patch.object(reporter, "gh", side_effect=AssertionError("gh pr view must not be called")):
            with mock.patch.object(reporter, "gh_api", return_value=mock.Mock(returncode=0, stdout=json.dumps({"pull_request": {}}), stderr="")):
                self.assertEqual("pr", reporter.parse_kind("47"))

    def test_parse_target_accepts_exact_owner_local_log_names(self) -> None:
        reporter = ProgressReporter(self.ctx)
        cases = {
            "review-pr47-quality-r2": "47",
            "fix-pr47-r2": "47",
            "fix-pr47-round2": "47",
            "fix-pr47-round-2": "47",
            "fix-pr47-round-2-retry": "47",
            "phase9-issue81-r10-minimal": "81",
            "phase9-issue81-r10-structural": "81",
            "phase9-issue81-r10-delete": "81",
            "phase9-issue81-r10-judge": "81",
            "phase9-issue81-r10-reflector": "81",
        }
        for base, expected in cases.items():
            with self.subTest(base=base):
                self.assertEqual(expected, reporter.parse_target(base))

    def test_parse_target_rejects_malformed_near_misses_without_prompt_fallback(self) -> None:
        reporter = ProgressReporter(self.ctx)
        for base in (
            "review-pr47",
            "review-pr47-quality",
            "fix-pr47",
            "fix-pr47-r",
            "phase9-issueX-r10-minimal",
            "phase9-issue81-r10-architect",
            "phase9-issue81-anything",
        ):
            with self.subTest(base=base):
                self.assertEqual("", reporter.parse_target(base))

    def test_parse_target_uses_prompt_fallback_only_when_prompt_exists(self) -> None:
        reporter = ProgressReporter(self.ctx)
        base = "phase9-issue81-r10-architect"
        self.assertEqual("", reporter.parse_target(base))
        prompt = self.tmp / ".refactor-loop" / "prompts" / f"{base}.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("Discuss #80 then final target #81.\n", encoding="utf-8")
        self.assertEqual("81", reporter.parse_target(base))

    def test_global_status_card_disabled_noops_without_github(self) -> None:
        reporter = ProgressReporter(self.ctx)

        with mock.patch.object(reporter, "gh_api", side_effect=AssertionError("gh api should not be called")):
            reporter.sync_global_status_card()

        self.assertEqual({}, reporter._state())

    def test_global_status_card_non_owner_noops_without_patch(self) -> None:
        host_env = self.tmp / ".config" / "consensus-rnd" / "host.env"
        host_env.write_text(
            f'export REPO_ROOT="{self.tmp}"\n'
            'export GH_REPO_SLUG="owner/repo"\n'
            'export HOST_HOLISTIC_STATUS_ENABLE="true"\n'
            'export HOST_HOLISTIC_STATUS_ISSUE_NUMBER="9"\n'
            'export HOST_HOLISTIC_STATUS_COMMENT_ID="123"\n',
            encoding="utf-8",
        )
        ctx = LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})
        reporter = ProgressReporter(ctx)
        decision = mock.Mock(allowed=False, owner_device="device-a", status="not-owner", action="global-dashboard-status-card", lease_id="", expires_at="")

        with mock.patch("codex_refactor_loop.monitors.progress.require_active_controller", return_value=decision):
            with mock.patch.object(reporter, "gh_api", side_effect=AssertionError("patch should not be called")):
                reporter.sync_global_status_card()

        self.assertEqual({}, reporter._state())

    def test_global_status_card_same_hash_skip_does_not_patch(self) -> None:
        host_env = self.tmp / ".config" / "consensus-rnd" / "host.env"
        host_env.write_text(
            f'export REPO_ROOT="{self.tmp}"\n'
            'export GH_REPO_SLUG="owner/repo"\n'
            'export HOST_HOLISTIC_STATUS_ENABLE="true"\n'
            'export HOST_HOLISTIC_STATUS_ISSUE_NUMBER="9"\n'
            'export HOST_HOLISTIC_STATUS_COMMENT_ID="123"\n'
            'export HOST_HOLISTIC_STATUS_INTERVAL_SECONDS="0"\n',
            encoding="utf-8",
        )
        ctx = LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})
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

    def test_global_status_card_patches_fixed_issue_comment_only(self) -> None:
        host_env = self.tmp / ".config" / "consensus-rnd" / "host.env"
        host_env.write_text(
            f'export REPO_ROOT="{self.tmp}"\n'
            'export GH_REPO_SLUG="owner/repo"\n'
            'export HOST_HOLISTIC_STATUS_ENABLE="true"\n'
            'export HOST_HOLISTIC_STATUS_ISSUE_NUMBER="9"\n'
            'export HOST_HOLISTIC_STATUS_COMMENT_ID="123"\n',
            encoding="utf-8",
        )
        ctx = LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})
        reporter = ProgressReporter(ctx)
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="global-dashboard-status-card", lease_id="", expires_at="")
        calls: list[list[str]] = []

        def fake_gh_api(args, check=True):
            del check
            calls.append(list(args))
            return mock.Mock(returncode=0, stdout="{}", stderr="")

        with mock.patch("codex_refactor_loop.monitors.progress.require_active_controller", return_value=decision):
            with mock.patch.object(reporter, "build_global_status_body", return_value="changed body\n"):
                with mock.patch.object(reporter, "gh", side_effect=AssertionError("writer must not use gh comment create")):
                    with mock.patch.object(reporter, "gh_api", side_effect=fake_gh_api):
                        reporter.sync_global_status_card()

        self.assertEqual(1, len(calls))
        self.assertEqual(["-X", "PATCH", "repos/owner/repo/issues/comments/123"], calls[0][:3])
        self.assertTrue(calls[0][3].startswith("-F"))
        state = reporter._state()["__global_dashboard_status_card__"]
        self.assertEqual("issue-comment", state["kind"])
        self.assertEqual("9", state["target"])
        self.assertEqual("123", state["comment_id"])


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
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)
        for forbidden in ("discussion", "gh issue comment", "gh pr comment", "issue/body", "pulls/comments"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text.lower())
        executable = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn(r"^phase9-issue([0-9]+).*", executable)
        for token in (
            'os.environ.get("INTERVAL"',
            'os.environ.get("STATE_DIR"',
            'os.environ.get("STATE_FILE"',
            'os.environ.get("LOG_DIR"',
            'os.environ.get("PROMPTS_DIR"',
            "PROGRESS_REPORTER_INTERVAL",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, executable)
        self.assertIn('f"repos/{self.repo}/issues/{target}"', text)
        self.assertNotIn('["pr", "view"', text)


if __name__ == "__main__":
    unittest.main()
