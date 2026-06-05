#!/usr/bin/env python3
"""Behavior and source-regression tests for GraphQL budget backoff."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
OLD_BACKOFF_PREFIX = "graphql-" + "backoff: skipping"
OLD_BACKOFF_DETAIL = "remaining" + "<threshold"

from codex_refactor_loop import github_budget
from codex_refactor_loop.closed_label_reconciler import ClosedLabelReconciler
from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.monitors.comment import CommentMonitor
from codex_refactor_loop.monitors.concurrency import ConcurrencyMonitor
from codex_refactor_loop.monitors.progress import ProgressReporter
from codex_refactor_loop.phase9.router import Phase9Router
from codex_refactor_loop.wakeup_runner import WakeupRunner


class GraphqlBudgetGuardTests(unittest.TestCase):
    def tearDown(self) -> None:
        github_budget.reset_graphql_budget_cache()

    def test_remaining_below_threshold_returns_false(self) -> None:
        calls: list[list[str]] = []

        def runner(command):
            calls.append(list(command))
            return subprocess.CompletedProcess(command, 0, json.dumps({"resources": {"graphql": {"remaining": 499}}}), "")

        self.assertFalse(github_budget.graphql_headroom_ok(min_remaining=500, cache_ttl_seconds=25, runner=runner, now=100))
        self.assertEqual(calls, [["gh", "api", "rate_limit"]])

    def test_remaining_at_threshold_returns_true(self) -> None:
        def runner(command):
            return subprocess.CompletedProcess(command, 0, json.dumps({"resources": {"graphql": {"remaining": 500}}}), "")

        self.assertTrue(github_budget.graphql_headroom_ok(min_remaining=500, cache_ttl_seconds=25, runner=runner, now=100))

    def test_cache_hit_does_not_repeat_rate_limit_lookup(self) -> None:
        calls: list[list[str]] = []

        def runner(command):
            calls.append(list(command))
            return subprocess.CompletedProcess(command, 0, json.dumps({"resources": {"graphql": {"remaining": 700}}}), "")

        self.assertTrue(github_budget.graphql_headroom_ok(min_remaining=500, cache_ttl_seconds=25, runner=runner, now=100))
        self.assertTrue(github_budget.graphql_headroom_ok(min_remaining=500, cache_ttl_seconds=25, runner=runner, now=120))
        self.assertEqual(len(calls), 1)

    def test_rate_limit_read_failure_fails_open(self) -> None:
        def runner(command):
            return subprocess.CompletedProcess(command, 1, "", "temporary failure")

        self.assertTrue(github_budget.graphql_headroom_ok(min_remaining=500, cache_ttl_seconds=25, runner=runner, now=100))


class GraphqlBackoffBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        github_budget.reset_graphql_budget_cache()
        self.tmp = Path(tempfile.mkdtemp(prefix="graphql-budget-backoff-"))
        (self.tmp / ".refactor-loop").mkdir()
        (self.tmp / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\nexport MAINTAINER_WHITELIST="maintainer"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)
        github_budget.reset_graphql_budget_cache()

    def test_comment_monitor_low_headroom_skips_tick_and_logs_backoff(self) -> None:
        monitor = CommentMonitor(self.ctx, interval=1)
        out = io.StringIO()
        with (
            mock.patch("codex_refactor_loop.monitors.comment.graphql_headroom_ok", return_value=False),
            mock.patch.object(monitor, "_poll_once", side_effect=AssertionError("GraphQL work should be skipped")),
            redirect_stdout(out),
        ):
            monitor.tick()

        self.assertIn("comment-monitor: tick skip:graphql-backoff remaining=unknown", out.getvalue())
        self.assertNotIn(OLD_BACKOFF_PREFIX, out.getvalue())

    def test_comment_monitor_high_headroom_runs_tick_normally(self) -> None:
        monitor = CommentMonitor(self.ctx, interval=1)
        with (
            mock.patch("codex_refactor_loop.monitors.comment.graphql_headroom_ok", return_value=True),
            mock.patch.object(monitor, "_poll_once", return_value={"targets": 2, "fetched": 1, "comments": 3}) as poll_once,
            redirect_stdout(out := io.StringIO()),
        ):
            monitor.tick()

        poll_once.assert_called_once()
        self.assertIn("comment-monitor: tick noop:poll-complete targets=2 fetched=1 comments=3", out.getvalue())

    def test_concurrency_top_up_low_headroom_defers_spawn_intent_without_consuming_queue(self) -> None:
        monitor = ConcurrencyMonitor(self.ctx)
        queue_dir = self.tmp / ".refactor-loop" / "dispatch-queue" / "p0"
        queue_dir.mkdir(parents=True)
        dispatch_file = queue_dir / "task.dispatch.json"
        dispatch_file.write_text(
            json.dumps(
                {
                    "task_id": "phase9-issue1-r1-minimal",
                    "cd": str(self.tmp),
                    "prompt": str(self.tmp / ".refactor-loop" / "prompts" / "task.md"),
                    "log": str(self.tmp / ".refactor-loop" / "logs" / "task.log"),
                    "reason": "test",
                }
            ),
            encoding="utf-8",
        )

        out = io.StringIO()
        with (
            mock.patch("codex_refactor_loop.monitors.concurrency.graphql_headroom_ok", return_value=False),
            mock.patch.object(monitor, "dispatch_one_from_queue", side_effect=AssertionError("spawn dispatch should be deferred")),
            redirect_stdout(out),
        ):
            actual = monitor.top_up_from_dispatch_queue(0, 1)

        self.assertEqual(actual, 0)
        self.assertTrue(dispatch_file.exists())
        self.assertIn("concurrency: tick skip:graphql-backoff remaining=unknown", out.getvalue())
        self.assertNotIn(OLD_BACKOFF_PREFIX, out.getvalue())
        pending = (self.tmp / ".refactor-loop" / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("DISPATCH_BACKOFF:graphql-headroom-low", pending)

    def test_wakeup_runner_low_headroom_skips_plan_apply_without_consume(self) -> None:
        runner = WakeupRunner(self.ctx, plan_loader=lambda _repo: {"schema": "wakeup-plan"}, supervisor=mock.Mock(), actions=mock.Mock())
        out = io.StringIO()
        with (
            mock.patch("codex_refactor_loop.wakeup_runner.graphql_headroom_ok", return_value=False),
            redirect_stdout(out),
        ):
            results = runner.run_once()

        self.assertEqual(results[0].status, "skipped")
        self.assertEqual(results[0].reason, "graphql-backoff")
        self.assertEqual("", out.getvalue())

    def test_wakeup_runner_spawn_low_headroom_records_backoff_and_does_not_spawn(self) -> None:
        supervisor = mock.Mock()
        runner = WakeupRunner(self.ctx, supervisor=supervisor, actions=mock.Mock())
        prompt = self.tmp / ".refactor-loop" / "prompts" / "spawn.md"
        log = self.tmp / ".refactor-loop" / "logs" / "spawn.log"
        prompt.parent.mkdir(parents=True)
        log.parent.mkdir(parents=True)
        prompt.write_text("run\n", encoding="utf-8")
        action = {
            "action_id": "spawn-low-headroom",
            "controller_action": "spawn_codex_harness_background",
            "runner_authority": "wakeup-runner-396",
            "no_generic_command": True,
            "preconditions": ["target_log_absent"],
            "source_marker": "IMPLEMENT_DONE:0:ok",
            "cd": str(self.tmp),
            "prompt": str(prompt),
            "log": str(log),
        }

        out = io.StringIO()
        with (
            mock.patch("codex_refactor_loop.wakeup_runner.graphql_headroom_ok", return_value=False),
            redirect_stdout(out),
        ):
            result = runner.apply_action(action)

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "helper_exit:3")
        self.assertIn("wakeup-runner: tick skip:graphql-backoff remaining=unknown", out.getvalue())
        self.assertNotIn(OLD_BACKOFF_PREFIX, out.getvalue())
        pending = (self.tmp / ".refactor-loop" / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("WAKEUP_RUNNER_SPAWN_BACKOFF:spawn-low-headroom:graphql-headroom-low", pending)
        supervisor.supervise.assert_not_called()

    def test_progress_reporter_low_headroom_skips_post_or_update(self) -> None:
        reporter = ProgressReporter(self.ctx, interval=1)
        reporter.log_dir.mkdir(parents=True, exist_ok=True)
        log = reporter.log_dir / "fix-pr434-round-1.log"
        log.write_text("still running\n", encoding="utf-8")

        out = io.StringIO()
        with (
            mock.patch("codex_refactor_loop.monitors.progress.graphql_headroom_ok", return_value=False),
            mock.patch.object(reporter, "post_or_update", side_effect=AssertionError("progress post/update should be skipped")),
            redirect_stdout(out),
        ):
            reporter.tick()

        self.assertIn("progress-reporter: tick skip:graphql-backoff remaining=unknown", out.getvalue())
        self.assertNotIn(OLD_BACKOFF_PREFIX, out.getvalue())

    def test_closed_label_reconciler_low_headroom_skips_active_controller_and_plan_collection(self) -> None:
        reconciler = ClosedLabelReconciler(self.ctx)
        out = io.StringIO()
        with (
            mock.patch("codex_refactor_loop.closed_label_reconciler.graphql_headroom_ok", return_value=False),
            mock.patch(
                "codex_refactor_loop.closed_label_reconciler.require_active_controller",
                side_effect=AssertionError("active-controller check should be skipped"),
            ),
            mock.patch.object(reconciler, "collect_plans", side_effect=AssertionError("plan collection should be skipped")),
            redirect_stdout(out),
        ):
            result = reconciler.run_once()

        self.assertEqual(result, 0)
        self.assertIn("closed-label-reconciler: tick skip:graphql-backoff remaining=unknown", out.getvalue())
        self.assertNotIn(OLD_BACKOFF_PREFIX, out.getvalue())

    def test_phase9_router_low_headroom_skips_marker_collection_and_dispatch(self) -> None:
        router = Phase9Router(ctx=self.ctx, command_runner=mock.Mock())
        out = io.StringIO()
        with (
            mock.patch("codex_refactor_loop.phase9.router.graphql_headroom_ok", return_value=False),
            mock.patch(
                "codex_refactor_loop.phase9.router.require_active_controller",
                side_effect=AssertionError("active-controller check should be skipped"),
            ),
            mock.patch.object(router, "_collect_markers", side_effect=AssertionError("marker collection should be skipped")),
            mock.patch.object(router, "_dispatch_design_issue_intake", side_effect=AssertionError("dispatch should be skipped")),
            redirect_stdout(out),
        ):
            router.tick()

        self.assertIn("phase9-router: tick skip:graphql-backoff remaining=unknown", out.getvalue())
        self.assertNotIn(OLD_BACKOFF_PREFIX, out.getvalue())


class GraphqlBudgetSourceRegressionTests(unittest.TestCase):
    def test_daemon_tick_entries_check_graphql_headroom_before_work(self) -> None:
        checks = {
            "monitors/comment.py": 'if not graphql_headroom_ok(cwd=self.ctx.repo_root, env=self.ctx.env_for_subprocess()):\n            self._log_tick_status("skip:graphql-backoff remaining=unknown")\n            return\n        summary = self._poll_once()',
            "monitors/progress.py": 'if not graphql_headroom_ok(cwd=self.ctx.repo_root, env=self.ctx.env_for_subprocess()):\n            self.log_tick_status("skip:graphql-backoff remaining=unknown")\n            return\n        for log in sorted(self.log_dir.glob("*.log")):',
            "closed_label_reconciler.py": 'if not graphql_headroom_ok(cwd=self.ctx.repo_root, env=self.ctx.env_for_subprocess()):\n            _log_tick_status("closed-label-reconciler", "skip:graphql-backoff remaining=unknown")\n            return 0\n        decision = require_active_controller',
            "phase9/router.py": 'if not graphql_headroom_ok(cwd=self.ctx.repo_root, env=self.ctx.env_for_subprocess()):\n            self._log_tick_status("skip:graphql-backoff remaining=unknown")\n            return\n        decision = require_active_controller',
            "wakeup_runner.py": 'if not graphql_headroom_ok(cwd=self.ctx.repo_root, env=self.ctx.env_for_subprocess()):\n            return [RunnerResult("", "skipped", "graphql-backoff")]\n        owner = require_active_controller',
        }
        for rel_path, needle in checks.items():
            with self.subTest(rel_path=rel_path):
                text = (SCRIPT_DIR / "codex_refactor_loop" / rel_path).read_text(encoding="utf-8")
                self.assertIn(needle, text)

    def test_budget_guard_keeps_rate_limit_endpoint_without_stdout_backoff_helper(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "github_budget.py").read_text(encoding="utf-8")
        self.assertIn('["gh", "api", "rate_limit"]', text)
        self.assertIn("remaining = (((payload.get(\"resources\") or {}).get(\"graphql\") or {}).get(\"remaining\")", text)
        self.assertIn("return True", text)
        self.assertNotIn("log_" + "graphql_backoff", text)
        self.assertNotIn(OLD_BACKOFF_PREFIX, text)
        self.assertNotIn(OLD_BACKOFF_DETAIL, text)

    def test_spawn_side_backoff_is_locked(self) -> None:
        concurrency = (SCRIPT_DIR / "codex_refactor_loop" / "monitors" / "concurrency.py").read_text(encoding="utf-8")
        runner = (SCRIPT_DIR / "codex_refactor_loop" / "wakeup_runner.py").read_text(encoding="utf-8")
        self.assertIn("DISPATCH_BACKOFF:graphql-headroom-low", concurrency)
        self.assertIn("WAKEUP_RUNNER_SPAWN_BACKOFF", runner)
