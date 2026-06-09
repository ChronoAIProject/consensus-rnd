#!/usr/bin/env python3
"""Behavior tests for the package concurrency monitor module."""

from __future__ import annotations

import io
import json
import os
import subprocess as real_subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
ORIGINAL_PATH = os.environ.get("PATH", "")
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop import labels
from codex_refactor_loop.monitors import concurrency
from codex_refactor_loop.monitors.concurrency import ConcurrencyMonitor


class PackageConcurrencyMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / ".refactor-loop" / "logs").mkdir(parents=True)
        self.env = mock.patch.dict(
            os.environ,
            {
                "REPO_ROOT": str(self.repo),
                "GH_REPO_SLUG": "owner/repo",
                "CODEX_FLOOR": "2",
                "DEGRADATION_WATCH_INTERVAL_SECONDS": "0",
            },
            clear=True,
        )
        self.env.start()
        concurrency._DEFAULT_MONITOR = None
        self.ctx = LoopContext.load(repo_root=self.repo)
        self.monitor = ConcurrencyMonitor(self.ctx)
        self.refactor_loop = self.repo / ".refactor-loop"

    def tearDown(self) -> None:
        concurrency._DEFAULT_MONITOR = None
        self.env.stop()
        self.tmp.cleanup()

    def write_dispatch(self, task_id: str, *, priority: str = "p0", cd: Path | None = None) -> Path:
        priority_dir = self.refactor_loop / "dispatch-queue" / priority
        priority_dir.mkdir(parents=True, exist_ok=True)
        prompt = self.refactor_loop / "prompts" / f"{task_id}.md"
        log = self.refactor_loop / "logs" / f"{task_id}.log"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("prompt\n", encoding="utf-8")
        cd = cd or self.repo / ".worktrees" / task_id
        payload = {
            "task_id": task_id,
            "cd": str(cd),
            "prompt": str(prompt),
            "log": str(log),
            "queued_at": "2026-05-26T07:25:00Z",
            "reason": f"{task_id} needed",
        }
        path = priority_dir / f"{task_id}.dispatch.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def write_daemon_self_drive_heartbeats(self, *, now: int = 1_780_000_000, age: int = 10) -> None:
        heartbeats = self.refactor_loop / "heartbeats"
        heartbeats.mkdir(parents=True, exist_ok=True)
        for daemon in ("phase9_router_daemon", "wakeup_runner_daemon"):
            (heartbeats / f"{daemon}.ts").write_text(str(now - age), encoding="utf-8")

    def append_pending_harness_intent(
        self,
        task_id: str,
        *,
        ts: str = "2026-05-26T07:25:00Z",
        intent_id: str | None = None,
        source: str = "phase9-router",
    ) -> None:
        payload = {
            "intent_id": intent_id or f"harness-spawn-intent:test:{task_id}",
            "task_id": task_id,
            "source": source,
            "route": "test",
            "command": "spawn-codex",
            "controller_action": "spawn_codex_harness_background",
            "log": f".refactor-loop/logs/{task_id}.log",
            "reason": f"issue #{task_id.removeprefix('phase9-issue')} dispatch",
        }
        pending = self.refactor_loop / ".controller-pending-events.log"
        pending.parent.mkdir(parents=True, exist_ok=True)
        with pending.open("a", encoding="utf-8") as handle:
            handle.write(f"{ts} HARNESS_SPAWN_INTENT {json.dumps(payload, sort_keys=True)}\n")

    def append_phase9_ledger_intent(
        self,
        task_id: str,
        *,
        ts: str = "2026-05-26T07:29:00Z",
    ) -> None:
        key = task_id.removeprefix("phase9-issue").replace("-r", "-", 1)
        payload = {
            "key": key,
            "marker": "SOLVER_DONE:minimal:ready",
            "log_path": f".refactor-loop/logs/{task_id}.log",
            "dispatched_at": ts,
            "dispatch_state": "harness-intent",
        }
        ledger = self.refactor_loop / "phase9-router-ledger.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def test_package_module_imports_without_repo_root_side_effect(self) -> None:
        self.env.stop()
        try:
            concurrency._DEFAULT_MONITOR = None
            self.assertIsNotNone(concurrency.PHASE_EXPECTED)
        finally:
            self.env.start()

    def test_compute_expected_preserves_phase_and_human_label_semantics(self) -> None:
        items = [
            {"number": 160, "kind": "issue", "phase": labels.PHASE_IMPLEMENTING, "human": labels.HUMAN_AUTO, "labels": [labels.MANAGED, labels.PHASE_IMPLEMENTING, labels.HUMAN_AUTO]},
            {"number": 161, "kind": "pr", "phase": labels.PHASE_REVIEWING, "human": labels.HUMAN_AUTO, "labels": [labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO]},
            {"number": 162, "kind": "issue", "phase": labels.PHASE_FIXING, "human": labels.HUMAN_MAINTAINER_DECISION, "labels": [labels.MANAGED, labels.PHASE_FIXING, labels.HUMAN_MAINTAINER_DECISION]},
            {"number": 163, "kind": "pr", "phase": "⚙️ phase:ci-running", "human": "🤖 human:auto-推进", "labels": ["auto-loop", "⚙️ phase:ci-running", "🤖 human:auto-推进"]},
        ]

        expected, breakdown = self.monitor.compute_expected(items)

        self.assertEqual(expected, 2)
        self.assertEqual(
            breakdown,
            [
                {"id": "#160", "kind": "issue", "phase": labels.PHASE_IMPLEMENTING, "expected": 1},
                {"id": "#161", "kind": "pr", "phase": labels.PHASE_REVIEWING, "expected": 1},
            ],
        )

    def test_compute_expected_folds_parent_issue_represented_by_child_pr_body(self) -> None:
        items = [
            {
                "number": 239,
                "kind": "issue",
                "phase": labels.PHASE_IMPLEMENTING,
                "human": labels.HUMAN_AUTO,
                "labels": [labels.MANAGED, labels.PHASE_IMPLEMENTING, labels.HUMAN_AUTO],
            },
            {
                "number": 255,
                "kind": "pr",
                "phase": labels.PHASE_REVIEWING,
                "human": labels.HUMAN_AUTO,
                "labels": [labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO],
                "body": "## PR\n\nCloses #239\n",
            },
        ]

        expected, breakdown = self.monitor.compute_expected(items)

        self.assertEqual(expected, 1)
        self.assertEqual(
            breakdown,
            [{"id": "#255", "kind": "pr", "phase": labels.PHASE_REVIEWING, "expected": 1}],
        )

    def test_compute_expected_skips_draft_release_rollup_but_keeps_review_prs(self) -> None:
        items = [
            {
                "number": 572,
                "kind": "pr",
                "phase": labels.PHASE_REVIEWING,
                "human": labels.HUMAN_AUTO,
                "labels": [labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO],
                "head_ref": "rollup/integration-sha",
                "is_draft": True,
            },
            {
                "number": 573,
                "kind": "pr",
                "phase": labels.PHASE_REVIEWING,
                "human": labels.HUMAN_AUTO,
                "labels": [labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO],
                "head_ref": "refactor/iter573-fix",
                "is_draft": True,
            },
            {
                "number": 574,
                "kind": "pr",
                "phase": labels.PHASE_REVIEWING,
                "human": labels.HUMAN_AUTO,
                "labels": [labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO],
                "head_ref": "rollup/integration-sha-2",
                "is_draft": False,
            },
        ]

        expected, breakdown = self.monitor.compute_expected(items)

        self.assertEqual(expected, 2)
        self.assertEqual(
            breakdown,
            [
                {"id": "#573", "kind": "pr", "phase": labels.PHASE_REVIEWING, "expected": 1},
                {"id": "#574", "kind": "pr", "phase": labels.PHASE_REVIEWING, "expected": 1},
            ],
        )

    def test_cli_count_and_list_use_canonical_spawn_filter(self) -> None:
        repo = self.ctx.repo_root
        fake_ps = (
            f"bash consensus-rnd-cli spawn-codex --cd {repo} --prompt /tmp/a.md --log /tmp/a.log\n"
            f"bash -c consensus-rnd-cli spawn-codex --cd {repo} --prompt /tmp/a.md --log /tmp/a.log\n"
            f"bash consensus-rnd-cli spawn-codex --cd {repo} --prompt /tmp/b.md --log /tmp/b.log\n"
            "bash consensus-rnd-cli spawn-codex --cd /Users/other-host/repo --prompt /tmp/c.md --log /tmp/c.log\n"
        )

        with mock.patch.object(concurrency, "_run", return_value=SimpleNamespace(stdout=fake_ps, returncode=0)):
            captured = io.StringIO()
            with mock.patch.object(sys, "stdout", captured):
                self.assertEqual(concurrency.main(["--count-only"]), 0)
            self.assertEqual(captured.getvalue().strip(), "2")

            captured = io.StringIO()
            with mock.patch.object(sys, "stdout", captured):
                self.assertEqual(concurrency.main(["--list-codex"]), 0)
            lines = [line for line in captured.getvalue().splitlines() if line.strip()]
            self.assertEqual(len(lines), 2)
            self.assertTrue(all(str(repo) in line for line in lines))
            self.assertTrue(all(" -c " not in line for line in lines))

    def test_tick_p0_no_gap_fires_topup_and_writes_exact_event_tokens(self) -> None:
        # Refactor (iterissue-330/issue-330):
        #   Old pattern: daemon nohup spawn bypassed the harness-visible contract; command could mean argv/shell.
        #   New principle: HARNESS_SPAWN_INTENT.command is closed enum Literal['spawn-codex']; argv is built by controller/harness.
        self.write_dispatch("fix-pr160-round-1-a")
        self.write_dispatch("fix-pr160-round-1-b")
        calls: list[list[str]] = []
        counts = [0, 1, 2]

        def fake_popen(cmd: list[str], **_: object) -> object:
            calls.append(cmd)
            return object()

        with mock.patch.object(concurrency.subprocess, "Popen", side_effect=fake_popen):
            with mock.patch.object(self.monitor, "count_in_flight_codex", side_effect=lambda: counts.pop(0)):
                with mock.patch.object(
                    self.monitor,
                    "list_auto_loop_issues",
                    return_value=[{"number": 160, "kind": "pr", "phase": labels.PHASE_FIXING, "human": labels.HUMAN_AUTO}],
                ):
                    self.monitor.tick()

        self.assertEqual(calls, [])
        alert = (self.refactor_loop / ".concurrency-alert.log").read_text(encoding="utf-8")
        self.assertIn("P0 no-gap-violation: 0 codex with 1 active task(s)", alert)
        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("concurrency-alert P0 no-gap-violation: 0 codex with 1 active task(s)", events)
        self.assertIn("HARNESS_SPAWN_INTENT", events)
        self.assertIn('"command": "spawn-codex"', events)
        self.assertIn("DISPATCH_INTENT:fix-pr160-round-1-a:p0:fix-pr160-round-1-a needed", events)
        self.assertNotIn("DISPATCH_INTENT:fix-pr160-round-1-b:p0:fix-pr160-round-1-b needed", events)
        self.assertTrue((self.refactor_loop / "dispatch-queue" / "p0" / "fix-pr160-round-1-b.dispatch.json").exists())
        self.assertNotIn("DISPATCH_FIRED", events)

    def test_zero_codex_with_fresh_daemon_self_drive_intent_is_transient_not_p0(self) -> None:
        now = datetime(2026, 5, 26, 7, 30, 0, tzinfo=timezone.utc)
        self.write_daemon_self_drive_heartbeats(now=int(now.timestamp()), age=20)
        self.append_pending_harness_intent("phase9-issue160-r1-minimal", ts="2026-05-26T07:29:00Z")

        with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=0):
            with mock.patch.object(
                self.monitor,
                "list_auto_loop_issues",
                return_value=[{"number": 160, "kind": "issue", "phase": labels.PHASE_DESIGN_SOLVING, "human": labels.HUMAN_AUTO}],
            ):
                with mock.patch.object(concurrency, "time") as fake_time:
                    fake_time.time.return_value = now.timestamp()
                    self.monitor.tick()

        self.assertFalse((self.refactor_loop / ".concurrency-alert.log").exists())
        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("ZERO_CODEX_CLASSIFICATION:daemon-self-drive-transient", events)
        payload = json.loads((self.refactor_loop / "state" / "statusline-snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["zero_codex_classification"]["classification"], "daemon-self-drive-transient")
        self.assertEqual(payload["p0_streak"], 0)

    def test_daemon_self_drive_heartbeat_uses_canonical_freshness_for_status(self) -> None:
        now = datetime(2026, 5, 26, 7, 30, 0, tzinfo=timezone.utc)
        task_id = "phase9-issue160-r1-minimal"
        fixed_datetime = mock.Mock(wraps=datetime)
        fixed_datetime.now.return_value = now
        for age, canonical_stale, expected_classification in (
            (89, False, "daemon-self-drive-transient"),
            (90, True, "p0"),
        ):
            with self.subTest(age=age):
                (self.refactor_loop / ".concurrency-alert.log").unlink(missing_ok=True)
                (self.refactor_loop / ".controller-pending-events.log").unlink(missing_ok=True)
                (self.refactor_loop / "state" / "statusline-snapshot.json").unlink(missing_ok=True)
                self.write_daemon_self_drive_heartbeats(now=int(now.timestamp()), age=age)
                self.append_pending_harness_intent(task_id, ts="2026-05-26T07:29:00Z")

                canonical = self.monitor.read_daemon_heartbeats(now=now.timestamp())
                classification = self.monitor.classify_zero_codex_self_drive(
                    breakdown=[{"id": "#160", "kind": "issue", "phase": labels.PHASE_DESIGN_SOLVING, "expected": 1}],
                    now=now.timestamp(),
                )

                self.assertEqual(canonical["phase9_router_daemon"]["stale"], canonical_stale)
                self.assertEqual(classification.classification, expected_classification)
                self.assertEqual(classification.heartbeat_threshold_seconds, concurrency.HEARTBEAT_STALE_SECONDS)

                with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=0):
                    with mock.patch.object(
                        self.monitor,
                        "list_auto_loop_issues",
                        return_value=[{"number": 160, "kind": "issue", "phase": labels.PHASE_DESIGN_SOLVING, "human": labels.HUMAN_AUTO}],
                    ):
                        with mock.patch.object(concurrency, "time") as fake_time:
                            with mock.patch.object(concurrency, "datetime", fixed_datetime):
                                fake_time.time.return_value = now.timestamp()
                                self.monitor.tick()

                payload = json.loads((self.refactor_loop / "state" / "statusline-snapshot.json").read_text(encoding="utf-8"))
                self.assertEqual(payload["daemons"]["phase9_router_daemon"]["stale"], canonical_stale)
                self.assertEqual(payload["daemons"]["wakeup_runner_daemon"]["stale"], canonical_stale)
                self.assertEqual(payload["zero_codex_classification"]["classification"], expected_classification)

    def test_zero_codex_with_stale_daemon_heartbeat_fails_closed_to_p0(self) -> None:
        now = datetime(2026, 5, 26, 7, 30, 0, tzinfo=timezone.utc)
        self.write_daemon_self_drive_heartbeats(now=int(now.timestamp()), age=500)
        self.append_pending_harness_intent("phase9-issue160-r1-minimal", ts="2026-05-26T07:29:00Z")

        with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=0):
            with mock.patch.object(
                self.monitor,
                "list_auto_loop_issues",
                return_value=[{"number": 160, "kind": "issue", "phase": labels.PHASE_DESIGN_SOLVING, "human": labels.HUMAN_AUTO}],
            ):
                with mock.patch.object(concurrency, "time") as fake_time:
                    fake_time.time.return_value = now.timestamp()
                    self.monitor.tick()

        alert = (self.refactor_loop / ".concurrency-alert.log").read_text(encoding="utf-8")
        self.assertIn("P0 no-gap-violation: 0 codex with 1 active task(s)", alert)
        self.assertIn("stale-heartbeat:phase9_router_daemon", alert)

    def test_zero_codex_with_fresh_heartbeats_but_stale_or_missing_intent_fails_closed_to_p0(self) -> None:
        now = datetime(2026, 5, 26, 7, 30, 0, tzinfo=timezone.utc)
        for case, ts, expected_reason in (
            ("missing", None, "missing-fresh-item-matching-intent"),
            ("stale", "2026-05-26T07:20:00Z", "stale-unconsumed-intent"),
        ):
            with self.subTest(case=case):
                (self.refactor_loop / ".concurrency-alert.log").unlink(missing_ok=True)
                (self.refactor_loop / ".controller-pending-events.log").unlink(missing_ok=True)
                self.write_daemon_self_drive_heartbeats(now=int(now.timestamp()), age=20)
                if ts is not None:
                    self.append_pending_harness_intent("phase9-issue160-r1-minimal", ts=ts)

                with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=0):
                    with mock.patch.object(
                        self.monitor,
                        "list_auto_loop_issues",
                        return_value=[{"number": 160, "kind": "issue", "phase": labels.PHASE_DESIGN_SOLVING, "human": labels.HUMAN_AUTO}],
                    ):
                        with mock.patch.object(concurrency, "time") as fake_time:
                            fake_time.time.return_value = now.timestamp()
                            self.monitor.tick()

                alert = (self.refactor_loop / ".concurrency-alert.log").read_text(encoding="utf-8")
                self.assertIn("P0 no-gap-violation: 0 codex with 1 active task(s)", alert)
                self.assertIn(expected_reason, alert)

    def test_hard_gate_transient_supply_counts_distinct_fresh_phase9_pending_targets(self) -> None:
        now = datetime(2026, 5, 26, 7, 30, 0, tzinfo=timezone.utc)
        self.append_pending_harness_intent("phase9-issue160-r1-minimal", ts="2026-05-26T07:29:00Z")
        self.append_pending_harness_intent("phase9-issue161-r1-minimal", ts="2026-05-26T07:29:00Z")
        self.append_pending_harness_intent("phase9-issue162-r1-minimal", ts="2026-05-26T07:29:00Z", source="concurrency-monitor")

        supply = self.monitor.hard_gate_transient_supply(
            breakdown=[
                {"id": "#160", "kind": "issue", "phase": labels.PHASE_DESIGN_SOLVING, "expected": 1},
                {"id": "#161", "kind": "issue", "phase": labels.PHASE_DESIGN_SOLVING, "expected": 1},
                {"id": "#162", "kind": "issue", "phase": labels.PHASE_DESIGN_SOLVING, "expected": 1},
            ],
            target=3,
            actual=1,
            queue_empty=True,
            now=now.timestamp(),
        )

        self.assertEqual(supply.supply, 2)
        self.assertEqual(len(supply.targets), 2)

    def test_hard_gate_transient_supply_ignores_consumed_phase9_intent(self) -> None:
        now = datetime(2026, 5, 26, 7, 30, 0, tzinfo=timezone.utc)
        task_id = "phase9-issue160-r1-minimal"
        self.append_pending_harness_intent(task_id, ts="2026-05-26T07:29:00Z")
        (self.refactor_loop / "logs" / f"{task_id}.log").write_text("already targeted\n", encoding="utf-8")

        supply = self.monitor.hard_gate_transient_supply(
            breakdown=[{"id": "#160", "kind": "issue", "phase": labels.PHASE_DESIGN_SOLVING, "expected": 1}],
            target=3,
            actual=1,
            queue_empty=True,
            now=now.timestamp(),
        )

        self.assertEqual(supply.supply, 0)
        self.assertIsNone(supply.blocked_reason)

    def test_zero_codex_malformed_intent_fails_closed_to_p0(self) -> None:
        now = datetime(2026, 5, 26, 7, 30, 0, tzinfo=timezone.utc)
        self.write_daemon_self_drive_heartbeats(now=int(now.timestamp()), age=20)
        pending = self.refactor_loop / ".controller-pending-events.log"
        pending.parent.mkdir(parents=True, exist_ok=True)
        pending.write_text("2026-05-26T07:29:00Z HARNESS_SPAWN_INTENT {bad json\n", encoding="utf-8")

        with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=0):
            with mock.patch.object(
                self.monitor,
                "list_auto_loop_issues",
                return_value=[{"number": 160, "kind": "issue", "phase": labels.PHASE_DESIGN_SOLVING, "human": labels.HUMAN_AUTO}],
            ):
                with mock.patch.object(concurrency, "time") as fake_time:
                    fake_time.time.return_value = now.timestamp()
                    self.monitor.tick()

        alert = (self.refactor_loop / ".concurrency-alert.log").read_text(encoding="utf-8")
        self.assertIn("P0 no-gap-violation: 0 codex with 1 active task(s)", alert)
        self.assertIn("malformed-harness-spawn-intent", alert)

    def test_zero_codex_suppressed_intent_fails_closed_to_p0(self) -> None:
        now = datetime(2026, 5, 26, 7, 30, 0, tzinfo=timezone.utc)
        for case, setup, expected_reason in (
            (
                "terminal-block-closed",
                lambda task_id: self._append_terminal_block(task_id, "target_not_open:CLOSED"),
                "suppressed-intent:terminal-block",
            ),
            (
                "terminal-block-merged",
                lambda task_id: self._append_terminal_block(task_id, "target_not_open:MERGED"),
                "suppressed-intent:terminal-block",
            ),
            (
                "target-log",
                lambda task_id: self._write_target_log(task_id),
                "suppressed-intent:target-log",
            ),
            (
                "spawn-claim",
                lambda task_id: self._write_spawn_claim(task_id),
                "suppressed-intent:spawn-claim",
            ),
        ):
            with self.subTest(case=case):
                (self.refactor_loop / ".concurrency-alert.log").unlink(missing_ok=True)
                (self.refactor_loop / ".controller-pending-events.log").unlink(missing_ok=True)
                task_id = "phase9-issue160-r1-minimal"
                (self.refactor_loop / "logs" / f"{task_id}.log").unlink(missing_ok=True)
                (self.refactor_loop / "locks" / "spawn-tasks" / f"{task_id}.lock").unlink(missing_ok=True)
                self.write_daemon_self_drive_heartbeats(now=int(now.timestamp()), age=20)
                self.append_pending_harness_intent(task_id, ts="2026-05-26T07:29:00Z")
                setup(task_id)

                with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=0):
                    with mock.patch.object(
                        self.monitor,
                        "list_auto_loop_issues",
                        return_value=[{"number": 160, "kind": "issue", "phase": labels.PHASE_DESIGN_SOLVING, "human": labels.HUMAN_AUTO}],
                    ):
                        with mock.patch.object(concurrency, "time") as fake_time:
                            fake_time.time.return_value = now.timestamp()
                            self.monitor.tick()

                alert = (self.refactor_loop / ".concurrency-alert.log").read_text(encoding="utf-8")
                self.assertIn("P0 no-gap-violation: 0 codex with 1 active task(s)", alert)
                self.assertIn(expected_reason, alert)

    def test_zero_codex_suppressed_phase9_ledger_intent_fails_closed_to_p0(self) -> None:
        now = datetime(2026, 5, 26, 7, 30, 0, tzinfo=timezone.utc)
        for case, setup, expected_reason in (
            (
                "terminal-block-closed",
                lambda task_id: self._append_terminal_block(task_id, "target_not_open:CLOSED"),
                "suppressed-intent:terminal-block",
            ),
            (
                "terminal-block-merged",
                lambda task_id: self._append_terminal_block(task_id, "target_not_open:MERGED"),
                "suppressed-intent:terminal-block",
            ),
            (
                "target-log",
                lambda task_id: self._write_target_log(task_id),
                "suppressed-intent:target-log",
            ),
            (
                "spawn-claim",
                lambda task_id: self._write_spawn_claim(task_id),
                "suppressed-intent:spawn-claim",
            ),
        ):
            with self.subTest(case=case):
                (self.refactor_loop / ".concurrency-alert.log").unlink(missing_ok=True)
                (self.refactor_loop / ".controller-pending-events.log").unlink(missing_ok=True)
                (self.refactor_loop / "phase9-router-ledger.jsonl").unlink(missing_ok=True)
                task_id = "phase9-issue160-r1-minimal"
                (self.refactor_loop / "logs" / f"{task_id}.log").unlink(missing_ok=True)
                (self.refactor_loop / "locks" / "spawn-tasks" / f"{task_id}.lock").unlink(missing_ok=True)
                self.write_daemon_self_drive_heartbeats(now=int(now.timestamp()), age=20)
                self.append_phase9_ledger_intent(task_id, ts="2026-05-26T07:29:00Z")
                ledger_row = json.loads((self.refactor_loop / "phase9-router-ledger.jsonl").read_text(encoding="utf-8"))
                self.assertNotIn("intent_id", ledger_row)
                self.assertNotIn("task_id", ledger_row)
                setup(task_id)

                with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=0):
                    with mock.patch.object(
                        self.monitor,
                        "list_auto_loop_issues",
                        return_value=[{"number": 160, "kind": "issue", "phase": labels.PHASE_DESIGN_SOLVING, "human": labels.HUMAN_AUTO}],
                    ):
                        with mock.patch.object(concurrency, "time") as fake_time:
                            fake_time.time.return_value = now.timestamp()
                            self.monitor.tick()

                alert = (self.refactor_loop / ".concurrency-alert.log").read_text(encoding="utf-8")
                self.assertIn("P0 no-gap-violation: 0 codex with 1 active task(s)", alert)
                self.assertIn(expected_reason, alert)

    def test_zero_codex_target_log_clean_marker_in_window_is_completed_marker_transient(self) -> None:
        now = datetime(2026, 5, 26, 7, 30, 0, tzinfo=timezone.utc)
        task_id = "phase9-issue160-r1-minimal"
        self.write_daemon_self_drive_heartbeats(now=int(now.timestamp()), age=20)
        self.append_pending_harness_intent(task_id, ts="2026-05-26T07:29:00Z")
        self._write_clean_terminal_marker_log(
            task_id,
            "SOLVER_DONE:minimal:ready",
            mtime=now.timestamp() - 30,
        )

        self._run_zero_codex_tick(now=now)

        self.assertFalse((self.refactor_loop / ".concurrency-alert.log").exists())
        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("ZERO_CODEX_CLASSIFICATION:daemon-self-drive-transient", events)
        payload = json.loads((self.refactor_loop / "state" / "statusline-snapshot.json").read_text(encoding="utf-8"))
        classification = payload["zero_codex_classification"]
        self.assertEqual(classification["classification"], "daemon-self-drive-transient")
        self.assertEqual(classification["reason"], "completed-marker-awaiting-consumption")
        self.assertIn("completed-target-log", ",".join(classification["evidence"]))

    def test_zero_codex_target_log_clean_marker_out_of_window_is_p0(self) -> None:
        now = datetime(2026, 5, 26, 7, 30, 0, tzinfo=timezone.utc)
        task_id = "phase9-issue160-r1-minimal"
        self.write_daemon_self_drive_heartbeats(now=int(now.timestamp()), age=20)
        self.append_pending_harness_intent(task_id, ts="2026-05-26T07:29:00Z")
        window = self.monitor.daemon_self_drive_consumption_window_seconds()
        self._write_clean_terminal_marker_log(
            task_id,
            "SOLVER_DONE:minimal:ready",
            mtime=now.timestamp() - window - 1,
        )

        self._run_zero_codex_tick(now=now)

        alert = (self.refactor_loop / ".concurrency-alert.log").read_text(encoding="utf-8")
        self.assertIn("P0 no-gap-violation: 0 codex with 1 active task(s)", alert)
        self.assertIn("stale-completed-marker", alert)

    def test_zero_codex_target_log_no_marker_remains_p0(self) -> None:
        now = datetime(2026, 5, 26, 7, 30, 0, tzinfo=timezone.utc)
        task_id = "phase9-issue160-r1-minimal"
        self.write_daemon_self_drive_heartbeats(now=int(now.timestamp()), age=20)
        self.append_pending_harness_intent(task_id, ts="2026-05-26T07:29:00Z")
        self._write_target_log(task_id)

        self._run_zero_codex_tick(now=now)

        alert = (self.refactor_loop / ".concurrency-alert.log").read_text(encoding="utf-8")
        self.assertIn("P0 no-gap-violation: 0 codex with 1 active task(s)", alert)
        self.assertIn("suppressed-intent:target-log", alert)

    def test_zero_codex_completed_marker_heartbeat_stale_is_p0(self) -> None:
        now = datetime(2026, 5, 26, 7, 30, 0, tzinfo=timezone.utc)
        task_id = "phase9-issue160-r1-minimal"
        self.write_daemon_self_drive_heartbeats(now=int(now.timestamp()), age=concurrency.HEARTBEAT_STALE_SECONDS)
        self.append_pending_harness_intent(task_id, ts="2026-05-26T07:29:00Z")
        self._write_clean_terminal_marker_log(
            task_id,
            "SOLVER_DONE:minimal:ready",
            mtime=now.timestamp() - 30,
        )

        self._run_zero_codex_tick(now=now)

        alert = (self.refactor_loop / ".concurrency-alert.log").read_text(encoding="utf-8")
        self.assertIn("P0 no-gap-violation: 0 codex with 1 active task(s)", alert)
        self.assertIn("stale-heartbeat:phase9_router_daemon", alert)
        self.assertNotIn("completed-marker-awaiting-consumption", alert)

    def test_zero_codex_phase9_ledger_target_log_clean_marker_in_window_is_completed_marker_transient(self) -> None:
        now = datetime(2026, 5, 26, 7, 30, 0, tzinfo=timezone.utc)
        task_id = "phase9-issue160-r1-minimal"
        self.write_daemon_self_drive_heartbeats(now=int(now.timestamp()), age=20)
        self.append_phase9_ledger_intent(task_id, ts="2026-05-26T07:29:00Z")
        self._write_clean_terminal_marker_log(
            task_id,
            "SOLVER_DONE:minimal:ready",
            mtime=now.timestamp() - 30,
        )

        self._run_zero_codex_tick(now=now)

        self.assertFalse((self.refactor_loop / ".concurrency-alert.log").exists())
        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("ZERO_CODEX_CLASSIFICATION:daemon-self-drive-transient", events)
        payload = json.loads((self.refactor_loop / "state" / "statusline-snapshot.json").read_text(encoding="utf-8"))
        classification = payload["zero_codex_classification"]
        self.assertEqual(classification["reason"], "completed-marker-awaiting-consumption")
        self.assertIn("completed-target-log", ",".join(classification["evidence"]))

    def test_zero_codex_target_log_missing_exit_with_visible_marker_gets_one_flush_transient(self) -> None:
        now = datetime(2026, 5, 26, 7, 30, 0, tzinfo=timezone.utc)
        task_id = "phase9-issue160-r1-minimal"
        self.write_daemon_self_drive_heartbeats(now=int(now.timestamp()), age=20)
        self.append_pending_harness_intent(task_id, ts="2026-05-26T07:29:00Z")
        self._write_marker_log_without_exit(
            task_id,
            "SOLVER_DONE:minimal:ready",
            mtime=now.timestamp() - 30,
        )

        self._run_zero_codex_tick(now=now)

        self.assertFalse((self.refactor_loop / ".concurrency-alert.log").exists())
        payload = json.loads((self.refactor_loop / "state" / "statusline-snapshot.json").read_text(encoding="utf-8"))
        classification = payload["zero_codex_classification"]
        self.assertEqual(classification["classification"], "daemon-self-drive-transient")
        self.assertEqual(classification["reason"], "target-log-awaiting-terminal-flush")
        self.assertIn("target-log-flush", ",".join(classification["evidence"]))

        self._run_zero_codex_tick(now=now)

        alert = (self.refactor_loop / ".concurrency-alert.log").read_text(encoding="utf-8")
        self.assertIn("P0 no-gap-violation: 0 codex with 1 active task(s)", alert)
        self.assertIn("suppressed-intent:target-log", alert)

        for case, state_zero_streak, age, expected_reason in (
            ("second-zero-streak", 1, 30, "suppressed-intent:target-log"),
            ("aged-mtime", 0, concurrency.HEARTBEAT_STALE_SECONDS + 1, "suppressed-intent:target-log"),
        ):
            with self.subTest(case=case):
                (self.refactor_loop / ".concurrency-alert.log").unlink(missing_ok=True)
                (self.refactor_loop / ".controller-pending-events.log").unlink(missing_ok=True)
                (self.refactor_loop / "state" / "statusline-snapshot.json").unlink(missing_ok=True)
                self.write_daemon_self_drive_heartbeats(now=int(now.timestamp()), age=20)
                self.append_pending_harness_intent(task_id, ts="2026-05-26T07:29:00Z")
                self._write_marker_log_without_exit(
                    task_id,
                    "SOLVER_DONE:minimal:ready",
                    mtime=now.timestamp() - age,
                )
                self.monitor.save_state({"zero_streak": state_zero_streak})

                self._run_zero_codex_tick(now=now)

                alert = (self.refactor_loop / ".concurrency-alert.log").read_text(encoding="utf-8")
                self.assertIn("P0 no-gap-violation: 0 codex with 1 active task(s)", alert)
                self.assertIn(expected_reason, alert)

    def test_zero_codex_target_log_missing_exit_requires_strict_visible_marker(self) -> None:
        now = datetime(2026, 5, 26, 7, 30, 0, tzinfo=timezone.utc)
        for case, marker in (
            ("wrong-role", "SOLVER_DONE:delete:ready"),
            ("malformed", "REVIEW_DONE:bad"),
        ):
            with self.subTest(case=case):
                (self.refactor_loop / ".concurrency-alert.log").unlink(missing_ok=True)
                (self.refactor_loop / ".controller-pending-events.log").unlink(missing_ok=True)
                (self.refactor_loop / "state" / "statusline-snapshot.json").unlink(missing_ok=True)
                self.monitor.save_state({"zero_streak": 0})
                task_id = "phase9-issue160-r1-minimal"
                self.write_daemon_self_drive_heartbeats(now=int(now.timestamp()), age=20)
                self.append_pending_harness_intent(task_id, ts="2026-05-26T07:29:00Z")
                self._write_marker_log_without_exit(
                    task_id,
                    marker,
                    mtime=now.timestamp() - 30,
                )

                self._run_zero_codex_tick(now=now)

                alert = (self.refactor_loop / ".concurrency-alert.log").read_text(encoding="utf-8")
                self.assertIn("P0 no-gap-violation: 0 codex with 1 active task(s)", alert)
                self.assertIn("suppressed-intent:target-log", alert)
                self.assertNotIn("target-log-awaiting-terminal-flush", alert)

    def test_zero_codex_target_log_nonzero_exit_with_visible_marker_is_p0(self) -> None:
        now = datetime(2026, 5, 26, 7, 30, 0, tzinfo=timezone.utc)
        task_id = "phase9-issue160-r1-minimal"
        self.write_daemon_self_drive_heartbeats(now=int(now.timestamp()), age=20)
        self.append_pending_harness_intent(task_id, ts="2026-05-26T07:29:00Z")
        self._write_marker_log_with_exit(
            task_id,
            "SOLVER_DONE:minimal:ready",
            exit_code=1,
            mtime=now.timestamp() - 30,
        )

        self._run_zero_codex_tick(now=now)

        alert = (self.refactor_loop / ".concurrency-alert.log").read_text(encoding="utf-8")
        self.assertIn("P0 no-gap-violation: 0 codex with 1 active task(s)", alert)
        self.assertIn("suppressed-intent:target-log", alert)
        self.assertNotIn("target-log-awaiting-terminal-flush", alert)

    def test_zero_codex_pending_flush_transient_does_not_mask_ledger_hard_failure(self) -> None:
        now = datetime(2026, 5, 26, 7, 30, 0, tzinfo=timezone.utc)
        task_id = "phase9-issue160-r1-minimal"
        self.write_daemon_self_drive_heartbeats(now=int(now.timestamp()), age=20)
        self.append_pending_harness_intent(task_id, ts="2026-05-26T07:29:00Z")
        self._write_marker_log_without_exit(
            task_id,
            "SOLVER_DONE:minimal:ready",
            mtime=now.timestamp() - 30,
        )
        (self.refactor_loop / "phase9-router-ledger.jsonl").write_text("{bad json\n", encoding="utf-8")

        self._run_zero_codex_tick(now=now)

        alert = (self.refactor_loop / ".concurrency-alert.log").read_text(encoding="utf-8")
        self.assertIn("P0 no-gap-violation: 0 codex with 1 active task(s)", alert)
        self.assertIn("malformed-phase9-ledger", alert)
        self.assertNotIn("target-log-awaiting-terminal-flush", alert)

    def test_zero_codex_pending_completed_marker_does_not_mask_ledger_hard_failure(self) -> None:
        now = datetime(2026, 5, 26, 7, 30, 0, tzinfo=timezone.utc)
        task_id = "phase9-issue160-r1-minimal"
        self.write_daemon_self_drive_heartbeats(now=int(now.timestamp()), age=20)
        self.append_pending_harness_intent(task_id, ts="2026-05-26T07:29:00Z")
        self._write_clean_terminal_marker_log(
            task_id,
            "SOLVER_DONE:minimal:ready",
            mtime=now.timestamp() - 30,
        )
        (self.refactor_loop / "phase9-router-ledger.jsonl").write_text("{bad json\n", encoding="utf-8")

        self._run_zero_codex_tick(now=now)

        alert = (self.refactor_loop / ".concurrency-alert.log").read_text(encoding="utf-8")
        self.assertIn("P0 no-gap-violation: 0 codex with 1 active task(s)", alert)
        self.assertIn("malformed-phase9-ledger", alert)
        self.assertNotIn("completed-marker-awaiting-consumption", alert)

    def _append_terminal_block(self, task_id: str, reason: str) -> None:
        pending = self.refactor_loop / ".controller-pending-events.log"
        with pending.open("a", encoding="utf-8") as handle:
            handle.write(f"2026-05-26T07:29:10Z WAKEUP_RUNNER_BLOCKED:harness-spawn-intent:test:{task_id}:{reason}\n")

    def _run_zero_codex_tick(self, *, now: datetime) -> None:
        with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=0):
            with mock.patch.object(
                self.monitor,
                "list_auto_loop_issues",
                return_value=[{"number": 160, "kind": "issue", "phase": labels.PHASE_DESIGN_SOLVING, "human": labels.HUMAN_AUTO}],
            ):
                with mock.patch.object(concurrency, "time") as fake_time:
                    fake_time.time.return_value = now.timestamp()
                    self.monitor.tick()

    def _write_target_log(self, task_id: str) -> None:
        log_path = self.refactor_loop / "logs" / f"{task_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("reserved\n", encoding="utf-8")

    def _write_clean_terminal_marker_log(self, task_id: str, marker: str, *, mtime: float) -> None:
        log_path = self.refactor_loop / "logs" / f"{task_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(f"worker output\n{marker}\nEXIT=0\n", encoding="utf-8")
        os.utime(log_path, (mtime, mtime))

    def _write_marker_log_without_exit(self, task_id: str, marker: str, *, mtime: float) -> None:
        log_path = self.refactor_loop / "logs" / f"{task_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(f"worker output\n{marker}\n", encoding="utf-8")
        os.utime(log_path, (mtime, mtime))

    def _write_marker_log_with_exit(self, task_id: str, marker: str, *, exit_code: int, mtime: float) -> None:
        log_path = self.refactor_loop / "logs" / f"{task_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(f"worker output\n{marker}\nEXIT={exit_code}\n", encoding="utf-8")
        os.utime(log_path, (mtime, mtime))

    def _write_spawn_claim(self, task_id: str) -> None:
        lock_dir = self.refactor_loop / "locks" / "spawn-tasks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        (lock_dir / f"{task_id}.lock").write_text("{}\n", encoding="utf-8")

    def test_mutable_dispatch_to_repo_root_is_rejected_with_narrow_allowlist_event(self) -> None:
        self.write_dispatch("fix-pr160-round-2", cd=self.repo)
        calls: list[list[str]] = []

        with mock.patch.object(concurrency.subprocess, "Popen", side_effect=lambda cmd, **_: calls.append(cmd)):
            fired = self.monitor.dispatch_one_from_queue()

        self.assertIsNone(fired)
        self.assertEqual(calls, [])
        rejected = self.refactor_loop / "dispatch-rejected" / "fix-pr160-round-2.json"
        payload = json.loads(rejected.read_text(encoding="utf-8"))
        self.assertEqual(payload["reject_reason"], "repo-root-cd")
        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("DISPATCH_REJECTED:fix-pr160-round-2:p0:main-worktree-cd:repo-root-cd", events)

    def test_snapshot_reads_fresh_heartbeats_from_ts_files(self) -> None:
        now = datetime(2026, 5, 26, 19, 0, 0, tzinfo=timezone.utc)
        heartbeats = self.refactor_loop / "heartbeats"
        heartbeats.mkdir(parents=True)
        (heartbeats / "concurrency_monitor.ts").write_text(str(int(now.timestamp() - 5)), encoding="utf-8")
        (heartbeats / "dev_sync_daemon.ts").write_text(str(int(now.timestamp() - 300)), encoding="utf-8")

        self.monitor.write_statusline_snapshot(
            actual=2,
            expected=1,
            p0_streak=0,
            last_p0_at=None,
            open_pr_count=1,
            open_issue_count=2,
            now=now,
        )

        payload = json.loads((self.refactor_loop / "state" / "statusline-snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["daemons_total"], 2)
        self.assertEqual(payload["daemons_healthy"], 1)
        self.assertFalse(payload["daemons"]["concurrency_monitor"]["stale"])
        self.assertTrue(payload["daemons"]["dev_sync_daemon"]["stale"])

    def test_readonly_cli_flags_allow_git_root_fallback_but_daemon_fails_closed(self) -> None:
        real_subprocess.run(["git", "init", "-q"], cwd=str(self.repo), check=True)
        env = {key: value for key, value in os.environ.items() if key not in {"REPO_ROOT", "CONSENSUS_RND_HOST_ENV"}}
        env.pop("ALLOW_GIT_ROOT_FALLBACK", None)
        env["PATH"] = ORIGINAL_PATH
        env["PYTHONPATH"] = str(SCRIPT_DIR)

        result = real_subprocess.run(
            [sys.executable, "-m", "codex_refactor_loop.monitors.concurrency", "--count-only"],
            cwd=str(self.repo),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        result = real_subprocess.run(
            [sys.executable, "-m", "codex_refactor_loop.monitors.concurrency"],
            cwd="/tmp",
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("REPO_ROOT is unset", result.stderr)

    def test_source_forbidden_lifecycle_tokens_stay_outside_concurrency_monitor(self) -> None:
        source = Path(concurrency.__file__).read_text(encoding="utf-8")
        forbidden = (
            "gh issue close",
            "gh pr close",
            "gh pr merge",
            "git commit",
            "git push",
            "git tag",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
