#!/usr/bin/env python3
"""Behavior tests for the patrol inspector."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.patrol import PatrolInspector, PatrolInspectorConfig, render_issue_body, _patrol_daemon_heartbeat_lease, main
from codex_refactor_loop.patrol_analysis import (
    CodexPatrolAnalysisProvider,
    PatrolAnalysisDecision,
    PatrolCandidateSignal,
    load_patrol_analysis_decision,
    patrol_analysis_env,
)


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, str]] = []

    def publish(self, *, fingerprint: str, title: str, body: str):
        self.published.append((fingerprint, title, body))
        return type("Issue", (), {"number": 100 + len(self.published)})()


class FakeAnalysisProvider:
    def __init__(self, decisions: list[PatrolAnalysisDecision] | None = None) -> None:
        self.decisions = decisions or []
        self.signals: list[PatrolCandidateSignal] = []

    def analyze(self, signal: PatrolCandidateSignal) -> PatrolAnalysisDecision:
        self.signals.append(signal)
        if self.decisions:
            return self.decisions.pop(0)
        return PatrolAnalysisDecision(
            is_real_issue=True,
            summary=signal.summary,
            severity=signal.severity,
            root_cause="analysis root cause",
            recommendation="analysis recommendation",
            rationale="analysis rationale",
        )


class ExplodingAnalysisProvider:
    def analyze(self, signal: PatrolCandidateSignal) -> PatrolAnalysisDecision:
        raise AssertionError(f"analysis provider should not run for {signal.source}")


def false_decision() -> PatrolAnalysisDecision:
    return PatrolAnalysisDecision(
        is_real_issue=False,
        summary="not a real issue",
        severity="low",
        root_cause="fixture or prompt noise",
        recommendation="do not publish",
        rationale="analysis classified the candidate as noise",
    )


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

        signals = PatrolInspector(self.ctx, github_items=items).collect_candidate_signals()

        self.assertEqual(
            {"exception-log", "runtime-artifact", "projection", "managed-snapshot"},
            {signal.kind for signal in signals},
        )

    def test_findings_require_real_issue_analysis_decision(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "router.log").write_text("RuntimeError: fixture example\n", encoding="utf-8")
        analyzer = FakeAnalysisProvider([false_decision()])

        findings = PatrolInspector(self.ctx, analysis_provider=analyzer, github_items=()).collect_findings()

        self.assertEqual(1, len(analyzer.signals))
        self.assertEqual((), findings)

    def test_disabled_run_does_not_spawn_analysis(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "router.log").write_text("EXIT=1\n", encoding="utf-8")
        inspector = PatrolInspector(
            self.ctx,
            config=PatrolInspectorConfig(enabled=False, interval_seconds=7200, max_findings=25),
            analysis_provider=ExplodingAnalysisProvider(),
            github_items=(),
        )

        self.assertEqual(0, inspector.run_once())

        state = json.loads((self.tmp / ".refactor-loop" / "state" / "patrol-inspector.json").read_text(encoding="utf-8"))
        self.assertEqual("disabled", state["status"])

    def test_not_active_controller_owner_does_not_spawn_analysis(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "router.log").write_text("EXIT=1\n", encoding="utf-8")
        inspector = PatrolInspector(
            self.ctx,
            config=PatrolInspectorConfig(enabled=True, interval_seconds=7200, max_findings=25),
            analysis_provider=ExplodingAnalysisProvider(),
            github_items=(),
        )
        decisions = type(
            "Decision",
            (),
            {"allowed": False, "owner_device": "other", "status": "not-owner", "action": "patrol-inspector", "lease_id": "", "expires_at": ""},
        )()

        with patch("codex_refactor_loop.patrol.require_active_controller", return_value=decisions):
            self.assertEqual(0, inspector.run_once())

        state = json.loads((self.tmp / ".refactor-loop" / "state" / "patrol-inspector.json").read_text(encoding="utf-8"))
        self.assertEqual("noop:not-owner", state["status"])

    def test_analysis_decision_constructs_publishable_finding_without_raw_evidence(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "router.log").write_text("RuntimeError: secret raw line\n", encoding="utf-8")
        analyzer = FakeAnalysisProvider(
            [
                PatrolAnalysisDecision(
                    is_real_issue=True,
                    summary="runtime route failed",
                    severity="high",
                    root_cause="router hit a persistent runtime failure",
                    recommendation="inspect the route helper and add regression coverage",
                    rationale="the signal is operational, not prompt text",
                )
            ]
        )

        findings = PatrolInspector(self.ctx, analysis_provider=analyzer, github_items=()).collect_findings()

        self.assertEqual(1, len(findings))
        body = render_issue_body(findings[0])
        self.assertIn("router hit a persistent runtime failure", body)
        self.assertIn("inspect the route helper", body)
        self.assertNotIn("RuntimeError: secret raw line", body)

    def test_load_patrol_analysis_decision_requires_structured_boolean_gate(self) -> None:
        path = self.tmp / ".refactor-loop" / "runs" / "decision.json"
        path.write_text(
            json.dumps(
                {
                    "is_real_issue": True,
                    "summary": "real runtime problem",
                    "severity": "high",
                    "root_cause": "root cause",
                    "recommendation": "fix it",
                    "rationale": "not fixture text",
                }
            ),
            encoding="utf-8",
        )

        decision = load_patrol_analysis_decision(path)

        self.assertTrue(decision.is_real_issue)
        self.assertEqual("real runtime problem", decision.summary)

    def test_invalid_patrol_analysis_decision_fails_closed(self) -> None:
        path = self.tmp / ".refactor-loop" / "runs" / "decision.json"
        path.write_text(json.dumps({"summary": "missing gate"}), encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "missing boolean is_real_issue"):
            load_patrol_analysis_decision(path)

    def test_codex_analysis_provider_writes_candidate_prompt_and_reads_decision(self) -> None:
        class FakeSupervisor:
            def __init__(self) -> None:
                self.stdin: Path | None = None
                self.command = None
                self.cwd: Path | None = None
                self.env: dict[str, str] | None = None
                self.log: Path | None = None
                self.stall: int | None = None

            def supervise(self, command, *, stdin, log, stall, env, cwd=None) -> int:
                self.command = command
                self.stdin = stdin
                self.log = log
                self.stall = stall
                self.env = dict(env)
                self.cwd = cwd
                output_path = Path(command[command.index("--output-last-message") + 1])
                output_path.write_text(
                    json.dumps(
                        {
                            "is_real_issue": True,
                            "summary": "analyzed patrol signal",
                            "severity": "medium",
                            "root_cause": "analysis root",
                            "recommendation": "analysis recommendation",
                            "rationale": "analysis rationale",
                        }
                    ),
                    encoding="utf-8",
                )
                return 0

        supervisor = FakeSupervisor()
        provider = CodexPatrolAnalysisProvider(self.ctx, supervisor=supervisor)

        decision = provider.analyze(
            PatrolCandidateSignal(
                kind="exception-log",
                source=".refactor-loop/logs/router.log",
                summary="runtime signal",
                severity="high",
                evidence=("RuntimeError: raw diagnostic",),
            )
        )

        self.assertEqual("analyzed patrol signal", decision.summary)
        self.assertIsNotNone(supervisor.stdin)
        self.assertIsNotNone(supervisor.log)
        self.assertIsNotNone(supervisor.cwd)
        self.assertIsNotNone(supervisor.env)
        self.assertEqual(5400, supervisor.stall)
        self.assertEqual("patrol-analysis", supervisor.stdin.parent.name)
        self.assertEqual(".md", supervisor.stdin.suffix)
        self.assertEqual("logs", supervisor.log.parent.name)
        self.assertTrue(supervisor.log.name.startswith("patrol-analysis-"))
        output_arg = str(supervisor.command[supervisor.command.index("--output-last-message") + 1])
        self.assertTrue(output_arg.endswith(".json"))
        self.assertIn(".refactor-loop/runs/patrol-analysis/", output_arg)
        self.assertIn("--sandbox", supervisor.command)
        self.assertIn("read-only", supervisor.command)
        self.assertIn("--ephemeral", supervisor.command)
        self.assertIn("--ignore-user-config", supervisor.command)
        self.assertIn("--ignore-rules", supervisor.command)
        self.assertIn("--skip-git-repo-check", supervisor.command)
        self.assertIn("--output-last-message", supervisor.command)
        self.assertEqual("patrol-analysis-cwd", supervisor.cwd.name)
        self.assertEqual("patrol-analysis-codex-home", Path(supervisor.env["CODEX_HOME"]).name)
        self.assertNotIn("GH_REPO_SLUG", supervisor.env)
        prompt = supervisor.stdin.read_text(encoding="utf-8")
        self.assertIn("RuntimeError: raw diagnostic", prompt)
        self.assertIn("Do not copy raw log lines", prompt)

    def test_patrol_analysis_env_strips_github_git_and_lifecycle_credentials(self) -> None:
        source_env = {
            "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env",
            "GH_TOKEN": "secret-gh-token",
            "GITHUB_TOKEN": "secret-github-token",
            "GIT_ASKPASS": "/tmp/askpass",
            "SSH_AUTH_SOCK": "/tmp/ssh.sock",
            "ACTIVE_CONTROLLER_DEVICE_ID": "device",
            "PATH": "/usr/bin",
            "LANG": "C.UTF-8",
        }
        with patch.dict("os.environ", source_env, clear=False):
            ctx = LoopContext.load(repo_root=self.tmp, env=source_env)
            env = patrol_analysis_env(ctx)

        self.assertEqual(str(self.tmp.resolve()), env["REPO_ROOT"])
        self.assertIn("PATH", env)
        self.assertEqual("C.UTF-8", env["LANG"])
        self.assertEqual("patrol-analysis-codex-home", Path(env["HOME"]).name)
        self.assertEqual(env["HOME"], env["CODEX_HOME"])
        for forbidden in (
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "GH_REPO_SLUG",
            "GIT_ASKPASS",
            "SSH_AUTH_SOCK",
            "ACTIVE_CONTROLLER_DEVICE_ID",
            "CONSENSUS_RND_HOST_ENV",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, env)

    def test_codex_analysis_provider_failure_fails_closed_with_log_path(self) -> None:
        class FailingSupervisor:
            def supervise(self, command, *, stdin, log, stall, env, cwd=None) -> int:
                return 23

        provider = CodexPatrolAnalysisProvider(self.ctx, supervisor=FailingSupervisor(), command_builder=lambda output: ("fake-codex", str(output)))

        with self.assertRaisesRegex(RuntimeError, r"patrol analysis failed: source=.refactor-loop/logs/router.log exit=23 log=.*patrol-analysis-.*\.log"):
            provider.analyze(
                PatrolCandidateSignal(
                    kind="exception-log",
                    source=".refactor-loop/logs/router.log",
                    summary="runtime signal",
                    severity="high",
                    evidence=("RuntimeError: raw diagnostic",),
                )
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

        findings = PatrolInspector(self.ctx, analysis_provider=FakeAnalysisProvider(), github_items=()).collect_findings()

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

        findings = PatrolInspector(self.ctx, analysis_provider=FakeAnalysisProvider(), github_items=()).collect_findings()

        self.assertNotIn("exception-log", {finding.kind for finding in findings})

    def test_clean_exit_log_body_failure_prose_does_not_create_findings(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "implement-issue-612.log").write_text(
            "\n".join(
                (
                    "The implementation discussed failed checks in old logs.",
                    "No exception was raised by this worker.",
                    "command failed appears in quoted markdown prose.",
                    "EXIT=0",
                )
            )
            + "\n",
            encoding="utf-8",
        )

        findings = PatrolInspector(self.ctx, analysis_provider=FakeAnalysisProvider(), github_items=()).collect_findings()

        self.assertNotIn("exception-log", {finding.kind for finding in findings})

    def test_clean_exit_log_body_failure_prose_does_not_create_findings(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "implement-issue-612.log").write_text(
            "\n".join(
                (
                    "The implementation discussed failed checks in old logs.",
                    "No exception was raised by this worker.",
                    "command failed appears in quoted markdown prose.",
                    "EXIT=0",
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

        findings = PatrolInspector(self.ctx, analysis_provider=FakeAnalysisProvider(), github_items=()).collect_findings()

        self.assertNotIn("exception-log", {finding.kind for finding in findings})

    def test_clean_exit_log_still_reports_structured_exception_signal(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "implement-issue-612.log").write_text(
            "\n".join(
                (
                    "worker body mentions failed validation",
                    "ValueError: broken",
                    "EXIT=0",
                )
            )
            + "\n",
            encoding="utf-8",
        )

        signals = PatrolInspector(self.ctx, github_items=()).collect_candidate_signals()

        exception_signals = [signal for signal in signals if signal.kind == "exception-log"]
        self.assertEqual(1, len(exception_signals))
        self.assertEqual(("ValueError: broken",), exception_signals[0].evidence)

    def test_clean_exit_worker_log_reports_standalone_post_failure(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "worker.log").write_text(
            "RuntimeError appears only in prompt prose\nPOST_FAILED: gh comment failed\nEXIT=0\n",
            encoding="utf-8",
        )

        signals = PatrolInspector(self.ctx, github_items=()).collect_candidate_signals()

        exception_signals = [signal for signal in signals if signal.kind == "exception-log"]
        self.assertEqual(1, len(exception_signals))
        self.assertEqual(("POST_FAILED: gh comment failed",), exception_signals[0].evidence)

    def test_clean_exit_worker_log_ignores_indented_prompt_post_failure_example(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "worker.log").write_text(
            "echo \"POST_FAILED:issue-1\"\n  POST_FAILED: prompt template example\nEXIT=0\n",
            encoding="utf-8",
        )

        findings = PatrolInspector(self.ctx, analysis_provider=FakeAnalysisProvider(), github_items=()).collect_findings()

        self.assertEqual([], [finding for finding in findings if finding.kind == "exception-log"])

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

        signals = PatrolInspector(self.ctx, github_items=()).collect_candidate_signals()

        exception_signals = [signal for signal in signals if signal.kind == "exception-log"]
        self.assertEqual(1, len(exception_signals))
        self.assertEqual(("worker output", "diagnostic text", "EXIT=1"), exception_signals[0].evidence)
        self.assertIn("EXIT=1", exception_signals[0].summary)

    def test_spawn_failed_terminal_envelope_creates_finding_with_spawn_evidence(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "worker.log").write_text(
            "setup\nSPAWN_FAILED=missing executable\nEXIT=127\n",
            encoding="utf-8",
        )

        signals = PatrolInspector(self.ctx, github_items=()).collect_candidate_signals()

        exception_signals = [signal for signal in signals if signal.kind == "exception-log"]
        self.assertEqual(1, len(exception_signals))
        self.assertEqual(("SPAWN_FAILED=missing executable", "EXIT=127"), exception_signals[0].evidence)
        self.assertIn("EXIT=127", exception_signals[0].summary)

    def test_timeout_kill_terminal_envelope_creates_finding(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "worker.log").write_text(
            "progress update\nTIMEOUT_KILL_AFTER=600s\nEXIT=137\n",
            encoding="utf-8",
        )

        signals = PatrolInspector(self.ctx, github_items=()).collect_candidate_signals()

        exception_signals = [signal for signal in signals if signal.kind == "exception-log"]
        self.assertEqual(1, len(exception_signals))
        self.assertEqual(("TIMEOUT_KILL_AFTER=600s", "EXIT=137"), exception_signals[0].evidence)
        self.assertIn("EXIT=137", exception_signals[0].summary)

    def test_legacy_stall_kill_terminal_envelope_still_creates_finding(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "worker.log").write_text(
            "progress update\nSTALL_KILL_AFTER=600\nEXIT=137\n",
            encoding="utf-8",
        )

        signals = PatrolInspector(self.ctx, github_items=()).collect_candidate_signals()

        exception_signals = [signal for signal in signals if signal.kind == "exception-log"]
        self.assertEqual(1, len(exception_signals))
        self.assertEqual(("STALL_KILL_AFTER=600", "EXIT=137"), exception_signals[0].evidence)
        self.assertIn("EXIT=137", exception_signals[0].summary)

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

        signals = PatrolInspector(self.ctx, github_items=()).collect_candidate_signals()

        exception_signals = [signal for signal in signals if signal.kind == "exception-log"]
        self.assertEqual(1, len(exception_signals))
        self.assertEqual(
            (
                "Traceback (most recent call last):",
                '  File "worker.py", line 4, in <module>',
                "    main()",
                "ValueError: broken",
            ),
            exception_signals[0].evidence,
        )

    def test_non_clean_worker_log_reports_terminal_exit_window(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "worker.log").write_text(
            "Traceback (most recent call last):\nRuntimeError: broken\nEXIT=1\n",
            encoding="utf-8",
        )

        signals = PatrolInspector(self.ctx, github_items=()).collect_candidate_signals()

        exception_signals = [signal for signal in signals if signal.kind == "exception-log"]
        self.assertEqual(1, len(exception_signals))
        self.assertEqual(("Traceback (most recent call last):", "RuntimeError: broken", "EXIT=1"), exception_signals[0].evidence)

    def test_log_command_failure_summary_is_reported(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "router.log").write_text(
            "command failed: exit=2 cmd=python3 -m pytest\n",
            encoding="utf-8",
        )

        signals = PatrolInspector(self.ctx, github_items=()).collect_candidate_signals()

        exception_signals = [signal for signal in signals if signal.kind == "exception-log"]
        self.assertEqual(1, len(exception_signals))
        self.assertEqual(("command failed: exit=2 cmd=python3 -m pytest",), exception_signals[0].evidence)

    def test_nonzero_exit_status_is_reported(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "router.log").write_text(
            "EXIT=2\n",
            encoding="utf-8",
        )

        signals = PatrolInspector(self.ctx, github_items=()).collect_candidate_signals()

        exception_signals = [signal for signal in signals if signal.kind == "exception-log"]
        self.assertEqual(1, len(exception_signals))
        self.assertEqual(("EXIT=2",), exception_signals[0].evidence)

    def test_nonzero_exited_status_is_reported(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "router.log").write_text(
            "spawn supervisor: process exited 127\n",
            encoding="utf-8",
        )

        signals = PatrolInspector(self.ctx, github_items=()).collect_candidate_signals()

        exception_signals = [signal for signal in signals if signal.kind == "exception-log"]
        self.assertEqual(1, len(exception_signals))
        self.assertEqual(("spawn supervisor: process exited 127",), exception_signals[0].evidence)

    def test_daemon_log_without_exit_still_reports_fatal_line(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "router.log").write_text(
            "FATAL: route failed\nfailed: unable to publish status\n",
            encoding="utf-8",
        )

        signals = PatrolInspector(self.ctx, github_items=()).collect_candidate_signals()

        exception_signals = [signal for signal in signals if signal.kind == "exception-log"]
        self.assertEqual(1, len(exception_signals))
        self.assertEqual(("FATAL: route failed",), exception_signals[0].evidence)

    def test_run_once_publishes_findings_and_writes_dashboard_state(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "router.log").write_text("EXIT=1\n", encoding="utf-8")
        publisher = FakePublisher()
        inspector = PatrolInspector(
            self.ctx,
            config=PatrolInspectorConfig(enabled=True, interval_seconds=7200, max_findings=25),
            publisher=publisher,
            analysis_provider=FakeAnalysisProvider(),
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
            analysis_provider=FakeAnalysisProvider(),
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
            analysis_provider=FakeAnalysisProvider(),
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
            analysis_provider=FakeAnalysisProvider(),
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
            analysis_provider=FakeAnalysisProvider(),
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
            analysis_provider=FakeAnalysisProvider(),
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
            analysis_provider=FakeAnalysisProvider(),
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
            calls: list[tuple[object, object]] = []

            def __init__(self, name: object, repo_root: object) -> None:
                self.calls.append((name, repo_root))
                self.beats = 0

            def beat(self) -> None:
                self.beats += 1

            def sleep_with_lease(self, _seconds: int) -> None:
                raise KeyboardInterrupt()

        decisions = type(
            "Decision",
            (),
            {"allowed": False, "owner_device": "other", "status": "not-owner", "action": "patrol-inspector", "lease_id": "", "expires_at": ""},
        )()
        with patch("codex_refactor_loop.patrol.require_active_controller", return_value=decisions):
            with patch("codex_refactor_loop.patrol.DaemonHeartbeatLease", FakeLease):
                with patch("codex_refactor_loop.patrol.LoopContext.load", return_value=self.ctx):
                    with self.assertRaises(KeyboardInterrupt):
                        main(["--daemon", "--interval-seconds", "1"])

        self.assertEqual([("patrol_inspector_daemon", self.ctx.repo_root)], FakeLease.calls)

    def test_daemon_tick_failure_logs_heartbeat_sleeps_and_continues_without_publication(self) -> None:
        class FakeLease:
            instance = None

            def __init__(self, _name: object, _repo_root: object) -> None:
                self.beats = 0
                self.sleeps: list[int] = []
                FakeLease.instance = self

            def beat(self) -> None:
                self.beats += 1

            def sleep_with_lease(self, seconds: int) -> None:
                self.sleeps.append(seconds)
                if len(self.sleeps) >= 2:
                    raise KeyboardInterrupt()

        class FakePublisher:
            instance = None

            def __init__(self, _ctx: LoopContext) -> None:
                self.published: list[tuple[str, str, str]] = []
                FakePublisher.instance = self

            def publish(self, *, fingerprint: str, title: str, body: str):
                self.published.append((fingerprint, title, body))
                return type("Issue", (), {"number": 1})()

        decisions = type(
            "Decision",
            (),
            {"allowed": True, "owner_device": "device", "status": "owner", "action": "patrol-inspector", "lease_id": "", "expires_at": ""},
        )()
        stderr = StringIO()
        with patch("codex_refactor_loop.patrol.require_active_controller", return_value=decisions):
            with patch("codex_refactor_loop.patrol.DaemonHeartbeatLease", FakeLease):
                with patch("codex_refactor_loop.patrol.PatrolIssuePublisher", FakePublisher):
                    with patch("codex_refactor_loop.patrol.LoopContext.load", return_value=self.ctx):
                        with patch("codex_refactor_loop.patrol.load_github_items_with_status", side_effect=[([], False), ([], True)]) as load_items:
                            with redirect_stderr(stderr):
                                with self.assertRaises(KeyboardInterrupt):
                                    main(["--daemon", "--interval-seconds", "3"])

        self.assertEqual(2, load_items.call_count)
        self.assertIsNotNone(FakeLease.instance)
        self.assertEqual(2, FakeLease.instance.beats)
        self.assertEqual([3, 3], FakeLease.instance.sleeps)
        self.assertIsNotNone(FakePublisher.instance)
        self.assertEqual([], FakePublisher.instance.published)
        self.assertIn("patrol-inspector daemon tick failed: RuntimeError:", stderr.getvalue())
        self.assertIn("loaded_ok_false", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
