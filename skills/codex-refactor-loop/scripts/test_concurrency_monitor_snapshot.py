#!/usr/bin/env python3
"""Behavior tests for concurrency_monitor statusline snapshots."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))


class ConcurrencyMonitorSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / ".refactor-loop" / "logs").mkdir(parents=True)
        self.env = mock.patch.dict(
            os.environ,
            {
                "REPO_ROOT": str(self.repo),
                "GH_REPO_SLUG": "owner/repo",
                "CODEX_FLOOR": "4",
            },
            clear=False,
        )
        self.env.start()
        from codex_refactor_loop.context import LoopContext
        from codex_refactor_loop.monitors.concurrency import ConcurrencyMonitor
        from codex_refactor_loop.ownership import OwnershipDecision, WorkTarget
        self.ctx = LoopContext.load(repo_root=self.repo)
        self.monitor = ConcurrencyMonitor(self.ctx)
        self.ownership = mock.patch(
            "codex_refactor_loop.monitors.concurrency.GitHubWorkOwnership.decide",
            return_value=OwnershipDecision(True, "owned", WorkTarget("issue", 1), "alice", "alice", 1.0),
        )
        self.ownership.start()

    def tearDown(self) -> None:
        self.ownership.stop()
        self.env.stop()
        self.tmp.cleanup()

    def snapshot_path(self) -> Path:
        return self.repo / ".refactor-loop" / "state" / "statusline-snapshot.json"

    def write_snapshot(self, *, now: datetime | None = None) -> dict:
        self.monitor.write_statusline_snapshot(
            actual=1,
            expected=1,
            p0_streak=0,
            last_p0_at=None,
            open_pr_count=0,
            open_issue_count=0,
            now=now,
        )
        return json.loads(self.snapshot_path().read_text(encoding="utf-8"))

    def fake_gh(self, cmd: list[str]) -> SimpleNamespace:
        if cmd[:3] == ["gh", "api", "user"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"login": "alice"}))
        if cmd[:3] in (["gh", "issue", "view"], ["gh", "pr", "view"]):
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"author": {"login": "alice"}, "updatedAt": "2026-05-29T00:00:00Z"}),
            )
        if cmd[:3] == ["gh", "issue", "list"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "number": 51,
                            "labels": [
                                {"name": "auto-loop"},
                                {"name": "🛠️ phase:implementing"},
                                {"name": "🤖 human:codex"},
                            ],
                        },
                        {
                            "number": 52,
                            "labels": [
                                {"name": "auto-loop"},
                                {"name": "⏸️ phase:blocked"},
                                {"name": "👤 human:需-maintainer-决策"},
                            ],
                        },
                    ]
                ),
            )
        if cmd[:3] == ["gh", "pr", "list"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "number": 9,
                            "labels": [
                                {"name": "auto-loop"},
                                {"name": "👀 phase:reviewing"},
                                {"name": "🤖 human:codex"},
                            ],
                        }
                    ]
                ),
            )
        if cmd[:2] == ["ps", "-eo"]:
            return SimpleNamespace(returncode=0, stdout="")
        return SimpleNamespace(returncode=1, stdout="")

    def test_tick_writes_snapshot_json_with_required_fields(self) -> None:
        with mock.patch.object(self.monitor, "run", side_effect=self.fake_gh):
            self.monitor.tick()

        data = json.loads(self.snapshot_path().read_text(encoding="utf-8"))
        for field in (
            "actual",
            "expected",
            "floor",
            "p0_streak",
            "freeze_minutes",
            "open_pr_count",
            "open_issue_count",
        ):
            with self.subTest(field=field):
                self.assertIn(field, data)
        self.assertEqual(data["actual"], 0)
        self.assertEqual(data["expected"], 2)
        self.assertEqual(data["floor"], 4)
        self.assertEqual(data["p0_streak"], 1)
        self.assertEqual(data["open_pr_count"], 1)
        self.assertEqual(data["open_issue_count"], 2)

    def test_snapshot_includes_last_p0_at_field(self) -> None:
        with mock.patch.object(self.monitor, "run", side_effect=self.fake_gh):
            self.monitor.tick()

        data = json.loads(self.snapshot_path().read_text(encoding="utf-8"))
        self.assertIn("last_p0_at", data)
        self.assertRegex(data["last_p0_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_non_p0_tick_snapshot_path(self) -> None:
        state_path = self.repo / ".refactor-loop" / ".concurrency-monitor-state.json"
        state_path.write_text(
            json.dumps({"zero_streak": 2, "last_p0_at": "2026-05-26T01:02:03Z"}),
            encoding="utf-8",
        )

        def fake_non_p0(cmd: list[str]) -> SimpleNamespace:
            if cmd[:3] == ["gh", "api", "user"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps({"login": "alice"}))
            if cmd[:3] in (["gh", "issue", "view"], ["gh", "pr", "view"]):
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({"author": {"login": "alice"}, "updatedAt": "2026-05-29T00:00:00Z"}),
                )
            if cmd[:3] == ["gh", "issue", "list"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps([]))
            if cmd[:3] == ["gh", "pr", "list"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        [
                            {
                                "number": 59,
                                "labels": [
                                    {"name": "auto-loop"},
                                    {"name": "👀 phase:reviewing"},
                                    {"name": "🤖 human:codex"},
                                ],
                            }
                        ]
                    ),
                )
            if cmd[:2] == ["ps", "-eo"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=f"bash {self.repo}/skills/codex-refactor-loop/scripts/consensus-rnd-cli spawn-codex --cd {self.repo}\n",
                )
            return SimpleNamespace(returncode=1, stdout="")

        with mock.patch.object(self.monitor, "run", side_effect=fake_non_p0):
            self.monitor.tick()

        data = json.loads(self.snapshot_path().read_text(encoding="utf-8"))
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(data["actual"], 1)
        self.assertEqual(data["expected"], 1)
        self.assertEqual(data["p0_streak"], 0)
        self.assertEqual(data["last_p0_at"], "2026-05-26T01:02:03Z")
        self.assertEqual(data["open_pr_count"], 1)
        self.assertEqual(data["open_issue_count"], 0)
        self.assertEqual(state["zero_streak"], 0)
        self.assertEqual(state["last_p0_at"], "2026-05-26T01:02:03Z")

    def test_snapshot_write_is_atomic(self) -> None:
        stop = threading.Event()
        errors: list[str] = []

        def reader() -> None:
            while not stop.is_set():
                path = self.snapshot_path()
                if not path.exists():
                    continue
                try:
                    json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    errors.append(str(exc))
                    stop.set()

        thread = threading.Thread(target=reader)
        thread.start()
        try:
            for index in range(200):
                self.monitor.write_statusline_snapshot(
                    actual=index,
                    expected=5,
                    p0_streak=index % 4,
                    last_p0_at=None,
                    open_pr_count=3,
                    open_issue_count=2,
                )
        finally:
            stop.set()
            thread.join(timeout=2)

        self.assertEqual(errors, [])
        data = json.loads(self.snapshot_path().read_text(encoding="utf-8"))
        self.assertEqual(data["actual"], 199)

    def test_snapshot_open_counts_match_peek(self) -> None:
        with mock.patch.object(self.monitor, "run", side_effect=self.fake_gh):
            items = self.monitor.list_auto_loop_issues()
            expected_prs = sum(1 for item in items if item["kind"] == "pr")
            expected_issues = sum(1 for item in items if item["kind"] == "issue")
            self.monitor.tick()

        data = json.loads(self.snapshot_path().read_text(encoding="utf-8"))
        self.assertEqual(data["open_pr_count"], expected_prs)
        self.assertEqual(data["open_issue_count"], expected_issues)
        self.assertEqual((expected_prs, expected_issues), (1, 2))

    def test_invalid_codex_floor_falls_back_to_default_5(self) -> None:
        with mock.patch.dict(os.environ, {"CODEX_FLOOR": "invalid"}, clear=False):
            data = self.write_snapshot()

        self.assertEqual(data["floor"], 5)

    def test_codex_floor_one_clamps_to_minimum_two(self) -> None:
        with mock.patch.dict(os.environ, {"CODEX_FLOOR": "1"}, clear=False):
            data = self.write_snapshot()

        self.assertEqual(data["floor"], 2)

    def test_marker_file_with_valid_marker_and_controlled_mtime_yields_freeze_minutes(self) -> None:
        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        marker = self.repo / ".refactor-loop" / "logs" / "worker.log"
        marker.write_text("PHASE: implementation progress\n", encoding="utf-8")
        os.utime(marker, (now.timestamp() - 7 * 60, now.timestamp() - 7 * 60))

        data = self.write_snapshot(now=now)

        self.assertEqual(data["freeze_minutes"], 7)

    def test_files_without_valid_markers_keep_freeze_minutes_zero(self) -> None:
        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        non_marker = self.repo / ".refactor-loop" / "logs" / "worker.log"
        non_marker.write_text("phase implementation progress without marker syntax\n", encoding="utf-8")
        os.utime(non_marker, (now.timestamp() - 30 * 60, now.timestamp() - 30 * 60))

        data = self.write_snapshot(now=now)

        self.assertEqual(data["freeze_minutes"], 0)


if __name__ == "__main__":
    unittest.main()
