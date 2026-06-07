#!/usr/bin/env python3
"""Behavior tests for the patrol inspector."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.patrol import PatrolInspector, PatrolInspectorConfig, _patrol_daemon_heartbeat_lease, main


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, str]] = []

    def publish(self, *, fingerprint: str, title: str, body: str):
        self.published.append((fingerprint, title, body))
        return type("Issue", (), {"number": 100 + len(self.published)})()


class PatrolInspectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="patrol-inspector-test-"))
        for rel in (".config/consensus-rnd", ".refactor-loop/logs", ".refactor-loop/runs", ".refactor-loop/state"):
            (self.tmp / rel).mkdir(parents=True, exist_ok=True)
        (self.tmp / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\n'
            'export GH_REPO_SLUG="owner/repo"\n'
            'export PATROL_INSPECTOR_ENABLE="true"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_collects_local_exception_runtime_and_snapshot_findings(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "router.log").write_text("ok\nEXIT=1\n", encoding="utf-8")
        (self.tmp / ".refactor-loop" / "runs" / "implement-issue-1.md").write_text("IMPLEMENT_DONE:issue-1:ok\n", encoding="utf-8")
        (self.tmp / ".refactor-loop" / "state" / "wakeup-plan.json").write_text(
            json.dumps({"status": "error", "reason": "bad"}) + "\n",
            encoding="utf-8",
        )
        items = [{"kind": "issue", "number": 5, "labels": ("crnd:lifecycle:managed",), "title": "missing phase"}]

        findings = PatrolInspector(self.ctx, github_items=items).collect_findings()

        self.assertEqual(
            {"exception-log", "runtime-artifact", "projection", "managed-snapshot"},
            {finding.kind for finding in findings},
        )

    def test_clean_exit_worker_log_ignores_prompt_and_diff_exception_words(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "worker.log").write_text(
            "\n".join(
                (
                    "Prompt says RuntimeError should be preserved in the diff.",
                    "This prose mentions exception, fatal, and failed states.",
                    "-raise RuntimeError('old')",
                    "+raise RuntimeError('new')",
                    "EXIT=0",
                )
            )
            + "\n",
            encoding="utf-8",
        )

        findings = PatrolInspector(self.ctx, github_items=()).collect_findings()

        self.assertEqual([], [finding for finding in findings if finding.kind == "exception-log"])

    def test_log_exception_words_without_bounded_diagnostic_do_not_create_findings(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "router.log").write_text(
            "\n".join(
                (
                    "command failed after retries",
                    "fallback prose says the task failed but no runtime crash was raised",
                    "docs/runtime-exceptions.md",
                    "authorization prose mentions exception handling boundaries",
                    "diff --git a/runtime-exceptions.md b/runtime-exceptions.md",
                    "+ failed markdown checklist item",
                    "path-only failed-state.md",
                )
            )
            + "\n",
            encoding="utf-8",
        )

        findings = PatrolInspector(self.ctx, github_items=()).collect_findings()

        self.assertNotIn("exception-log", {finding.kind for finding in findings})

    def test_clean_review_reject_prose_with_clean_exit_does_not_create_exception_log(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "worker.log").write_text(
            "\n".join(
                (
                    "---",
                    "verdict: reject",
                    "---",
                    "This review rejects the patch because failed tests are mentioned in source prose.",
                    "Traceback and RuntimeError examples belong to the review body, not issue intake.",
                    "FATAL: quoted diagnostic from the diff",
                    "EXIT=0",
                )
            )
            + "\n",
            encoding="utf-8",
        )

        findings = PatrolInspector(self.ctx, github_items=()).collect_findings()

        self.assertEqual([], [finding for finding in findings if finding.kind == "exception-log"])

    def test_clean_exit_worker_log_reports_standalone_post_failure(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "worker.log").write_text(
            "RuntimeError appears only in prompt prose\nPOST_FAILED: gh comment failed\nEXIT=0\n",
            encoding="utf-8",
        )

        findings = PatrolInspector(self.ctx, github_items=()).collect_findings()

        exception_findings = [finding for finding in findings if finding.kind == "exception-log"]
        self.assertEqual(1, len(exception_findings))
        self.assertEqual(("POST_FAILED: gh comment failed",), exception_findings[0].evidence)

    def test_nonzero_exit_worker_log_creates_exception_log_finding(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "router.log").write_text(
            "\n".join(
                (
                    "worker output",
                    "diagnostic text",
                    "EXIT=1",
                )
            )
            + "\n",
            encoding="utf-8",
        )

        findings = PatrolInspector(self.ctx, github_items=()).collect_findings()

        exception_findings = [finding for finding in findings if finding.kind == "exception-log"]
        self.assertEqual(1, len(exception_findings))
        self.assertEqual(("worker output", "diagnostic text", "EXIT=1"), exception_findings[0].evidence)
        self.assertIn("EXIT=1", exception_findings[0].summary)

    def test_spawn_failed_terminal_envelope_creates_finding_with_spawn_evidence(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "worker.log").write_text(
            "setup\nSPAWN_FAILED=missing executable\nEXIT=127\n",
            encoding="utf-8",
        )

        findings = PatrolInspector(self.ctx, github_items=()).collect_findings()

        exception_findings = [finding for finding in findings if finding.kind == "exception-log"]
        self.assertEqual(1, len(exception_findings))
        self.assertEqual(("SPAWN_FAILED=missing executable", "EXIT=127"), exception_findings[0].evidence)
        self.assertIn("EXIT=127", exception_findings[0].summary)

    def test_stall_kill_terminal_envelope_creates_finding(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "worker.log").write_text(
            "progress update\nSTALL_KILL_AFTER=600\nEXIT=137\n",
            encoding="utf-8",
        )

        findings = PatrolInspector(self.ctx, github_items=()).collect_findings()

        exception_findings = [finding for finding in findings if finding.kind == "exception-log"]
        self.assertEqual(1, len(exception_findings))
        self.assertEqual(("STALL_KILL_AFTER=600", "EXIT=137"), exception_findings[0].evidence)
        self.assertIn("EXIT=137", exception_findings[0].summary)

    def test_log_traceback_block_without_exit_is_reported_as_bounded_evidence(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "router.log").write_text(
            "\n".join(
                (
                    "before",
                    "Traceback (most recent call last):",
                    '  File "worker.py", line 4, in <module>',
                    "    main()",
                    "ValueError: broken",
                    "after",
                )
            )
            + "\n",
            encoding="utf-8",
        )

        findings = PatrolInspector(self.ctx, github_items=()).collect_findings()

        exception_findings = [finding for finding in findings if finding.kind == "exception-log"]
        self.assertEqual(1, len(exception_findings))
        self.assertEqual(
            (
                "Traceback (most recent call last):",
                '  File "worker.py", line 4, in <module>',
                "    main()",
                "ValueError: broken",
            ),
            exception_findings[0].evidence,
        )

    def test_non_clean_worker_log_reports_terminal_exit_window(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "worker.log").write_text(
            "Traceback (most recent call last):\nRuntimeError: broken\nEXIT=1\n",
            encoding="utf-8",
        )

        findings = PatrolInspector(self.ctx, github_items=()).collect_findings()

        exception_findings = [finding for finding in findings if finding.kind == "exception-log"]
        self.assertEqual(1, len(exception_findings))
        self.assertEqual(("Traceback (most recent call last):", "RuntimeError: broken", "EXIT=1"), exception_findings[0].evidence)

    def test_log_command_failure_summary_is_reported(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "router.log").write_text(
            "command failed: exit=2 cmd=python3 -m pytest\n",
            encoding="utf-8",
        )

        findings = PatrolInspector(self.ctx, github_items=()).collect_findings()

        exception_findings = [finding for finding in findings if finding.kind == "exception-log"]
        self.assertEqual(1, len(exception_findings))
        self.assertEqual(("command failed: exit=2 cmd=python3 -m pytest",), exception_findings[0].evidence)

    def test_daemon_log_without_exit_still_reports_fatal_line(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "router.log").write_text(
            "FATAL: route failed\nfailed: unable to publish status\n",
            encoding="utf-8",
        )

        findings = PatrolInspector(self.ctx, github_items=()).collect_findings()

        exception_findings = [finding for finding in findings if finding.kind == "exception-log"]
        self.assertEqual(1, len(exception_findings))
        self.assertEqual(("FATAL: route failed",), exception_findings[0].evidence)

    def test_run_once_publishes_findings_and_writes_dashboard_state(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "router.log").write_text("EXIT=1\n", encoding="utf-8")
        publisher = FakePublisher()
        inspector = PatrolInspector(
            self.ctx,
            config=PatrolInspectorConfig(enabled=True, interval_seconds=7200, max_findings=25),
            publisher=publisher,
            github_items=(),
        )

        self.assertEqual(0, inspector.run_once())

        self.assertEqual(1, len(publisher.published))
        state = json.loads((self.tmp / ".refactor-loop" / "state" / "patrol-inspector.json").read_text(encoding="utf-8"))
        self.assertEqual("ok", state["status"])
        self.assertEqual(1, len(state["findings"]))
        self.assertEqual(1, len(state["published"]))

    def test_snapshot_load_failure_is_visible_and_blocks_publication(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "router.log").write_text("EXIT=1\n", encoding="utf-8")
        publisher = FakePublisher()
        inspector = PatrolInspector(
            self.ctx,
            config=PatrolInspectorConfig(enabled=True, interval_seconds=7200, max_findings=25),
            publisher=publisher,
        )

        with patch("codex_refactor_loop.patrol.load_github_items_with_status", side_effect=ValueError("bad snapshot")):
            with self.assertRaisesRegex(RuntimeError, "patrol managed snapshot load failed"):
                inspector.run_once()

        self.assertEqual([], publisher.published)
        state = json.loads((self.tmp / ".refactor-loop" / "state" / "patrol-inspector.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", state["status"])
        self.assertIn("bad snapshot", state["reason"])
        self.assertEqual([], state["published"])

    def test_unavailable_snapshot_status_is_visible_and_blocks_publication(self) -> None:
        publisher = FakePublisher()
        inspector = PatrolInspector(
            self.ctx,
            config=PatrolInspectorConfig(enabled=True, interval_seconds=7200, max_findings=25),
            publisher=publisher,
        )

        with patch("codex_refactor_loop.patrol.load_github_items_with_status", return_value=([], False)):
            with self.assertRaisesRegex(RuntimeError, "loaded_ok_false"):
                inspector.run_once()

        self.assertEqual([], publisher.published)
        state = json.loads((self.tmp / ".refactor-loop" / "state" / "patrol-inspector.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", state["status"])
        self.assertIn("loaded_ok_false", state["reason"])

    def test_unreadable_log_input_is_visible_and_blocks_publication(self) -> None:
        log_path = self.tmp / ".refactor-loop" / "logs" / "router.log"
        log_path.write_text("RuntimeError: broken\n", encoding="utf-8")
        publisher = FakePublisher()
        inspector = PatrolInspector(
            self.ctx,
            config=PatrolInspectorConfig(enabled=True, interval_seconds=7200, max_findings=25),
            publisher=publisher,
            github_items=(),
        )

        with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
            with self.assertRaisesRegex(RuntimeError, "patrol input read failed: source=.refactor-loop/logs/router.log"):
                inspector.run_once()

        self.assertEqual([], publisher.published)
        state = json.loads((self.tmp / ".refactor-loop" / "state" / "patrol-inspector.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", state["status"])
        self.assertIn(".refactor-loop/logs/router.log", state["reason"])

    def test_unreadable_run_artifact_is_visible_and_blocks_publication(self) -> None:
        artifact_path = self.tmp / ".refactor-loop" / "runs" / "implement-issue-1.md"
        artifact_path.write_text("IMPLEMENT_DONE:issue-1:ok\n", encoding="utf-8")
        publisher = FakePublisher()
        inspector = PatrolInspector(
            self.ctx,
            config=PatrolInspectorConfig(enabled=True, interval_seconds=7200, max_findings=25),
            publisher=publisher,
            github_items=(),
        )
        original_read_text = Path.read_text

        def fail_run_artifact(path: Path, *args, **kwargs) -> str:
            if path.name == artifact_path.name and path.parent.name == "runs":
                raise OSError("stale handle")
            return original_read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", fail_run_artifact):
            with self.assertRaisesRegex(RuntimeError, "patrol input read failed: source=.refactor-loop/runs/implement-issue-1.md"):
                inspector.run_once()

        self.assertEqual([], publisher.published)
        state = json.loads((self.tmp / ".refactor-loop" / "state" / "patrol-inspector.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", state["status"])
        self.assertIn(".refactor-loop/runs/implement-issue-1.md", state["reason"])

    def test_malformed_wakeup_plan_is_visible_and_blocks_publication(self) -> None:
        (self.tmp / ".refactor-loop" / "state" / "wakeup-plan.json").write_text("{not json\n", encoding="utf-8")
        publisher = FakePublisher()
        inspector = PatrolInspector(
            self.ctx,
            config=PatrolInspectorConfig(enabled=True, interval_seconds=7200, max_findings=25),
            publisher=publisher,
            github_items=(),
        )

        with self.assertRaisesRegex(RuntimeError, "patrol input JSON malformed: source=.refactor-loop/state/wakeup-plan.json"):
            inspector.run_once()

        self.assertEqual([], publisher.published)
        state = json.loads((self.tmp / ".refactor-loop" / "state" / "patrol-inspector.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", state["status"])
        self.assertIn(".refactor-loop/state/wakeup-plan.json", state["reason"])

    def test_malformed_peek_projection_is_visible_and_blocks_publication(self) -> None:
        (self.tmp / ".refactor-loop" / "state" / "peek.json").write_text("{not json\n", encoding="utf-8")
        publisher = FakePublisher()
        inspector = PatrolInspector(
            self.ctx,
            config=PatrolInspectorConfig(enabled=True, interval_seconds=7200, max_findings=25),
            publisher=publisher,
            github_items=(),
        )

        with self.assertRaisesRegex(RuntimeError, "patrol input JSON malformed: source=.refactor-loop/state/peek.json"):
            inspector.run_once()

        self.assertEqual([], publisher.published)
        state = json.loads((self.tmp / ".refactor-loop" / "state" / "patrol-inspector.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", state["status"])
        self.assertIn(".refactor-loop/state/peek.json", state["reason"])

    def test_daemon_heartbeat_lease_uses_restart_helper_name_and_context_root(self) -> None:
        lease = _patrol_daemon_heartbeat_lease(self.ctx)

        self.assertEqual("patrol_inspector_daemon", lease.name)
        self.assertEqual(self.ctx.repo_root / ".refactor-loop" / "heartbeats" / "patrol_inspector_daemon.ts", lease.heartbeat_file)

    def test_daemon_main_constructs_context_bound_heartbeat_lease(self) -> None:
        class FakeLease:
            def __init__(self) -> None:
                self.beats = 0

            def beat(self) -> None:
                self.beats += 1

            def sleep_with_lease(self, _seconds: int) -> None:
                raise KeyboardInterrupt()

        fake_lease = FakeLease()
        decisions = type(
            "Decision",
            (),
            {"allowed": False, "owner_device": "other", "status": "not-owner", "action": "patrol-inspector", "lease_id": "", "expires_at": ""},
        )()
        with patch("codex_refactor_loop.patrol.require_active_controller", return_value=decisions):
            with patch("codex_refactor_loop.patrol._patrol_daemon_heartbeat_lease", return_value=fake_lease) as lease_factory:
                with patch("codex_refactor_loop.patrol.LoopContext.load", return_value=self.ctx):
                    with self.assertRaises(KeyboardInterrupt):
                        main(["--daemon", "--interval-seconds", "1"])

        lease_factory.assert_called_once()
        self.assertEqual(self.ctx.repo_root, lease_factory.call_args.args[0].repo_root)


if __name__ == "__main__":
    unittest.main()
