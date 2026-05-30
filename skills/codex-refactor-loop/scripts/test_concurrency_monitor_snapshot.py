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

from codex_refactor_loop import labels as label_catalog


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
        self.ctx = LoopContext.load(repo_root=self.repo)
        self.monitor = ConcurrencyMonitor(self.ctx)

    def tearDown(self) -> None:
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

    def write_update_state(self, payload: dict[str, object]) -> None:
        path = self.repo / ".refactor-loop" / "state" / "update-check.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    def fake_gh(self, cmd: list[str]) -> SimpleNamespace:
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

    def test_list_auto_loop_issues_queries_canonical_and_legacy_managed_labels_once(self) -> None:
        responses = {
            ("issue", label_catalog.MANAGED): [
                {
                    "number": 71,
                    "labels": [
                        {"name": label_catalog.MANAGED},
                        {"name": label_catalog.PHASE_IMPLEMENTING},
                        {"name": label_catalog.HUMAN_AUTO},
                    ],
                }
            ],
            ("issue", "auto-loop"): [
                {
                    "number": 71,
                    "labels": [
                        {"name": label_catalog.MANAGED},
                        {"name": label_catalog.PHASE_IMPLEMENTING},
                        {"name": label_catalog.HUMAN_AUTO},
                    ],
                },
                {
                    "number": 72,
                    "labels": [
                        {"name": "auto-loop"},
                        {"name": "🔧 phase:fixing"},
                        {"name": "🤖 human:codex"},
                    ],
                },
            ],
            ("issue", "phase9-auto-solve"): [],
            ("issue", "refactor-design-needed"): [],
            ("pr", label_catalog.MANAGED): [
                {
                    "number": 73,
                    "labels": [
                        {"name": label_catalog.MANAGED},
                        {"name": label_catalog.PHASE_REVIEWING},
                        {"name": label_catalog.HUMAN_AUTO},
                    ],
                }
            ],
            ("pr", "auto-loop"): [
                {
                    "number": 73,
                    "labels": [
                        {"name": label_catalog.MANAGED},
                        {"name": label_catalog.PHASE_REVIEWING},
                        {"name": label_catalog.HUMAN_AUTO},
                    ],
                }
            ],
            ("pr", "phase9-auto-solve"): [],
            ("pr", "refactor-design-needed"): [
                {
                    "number": 74,
                    "labels": [
                        {"name": "refactor-design-needed"},
                        {"name": "🔍 phase:design-solving"},
                        {"name": "🤖 human:auto-推进"},
                    ],
                }
            ],
        }
        calls: list[tuple[str, str]] = []

        def fake_by_label(cmd: list[str]) -> SimpleNamespace:
            self.assertEqual(cmd[:2], ["gh", cmd[1]])
            kind = cmd[1]
            label = cmd[cmd.index("--label") + 1]
            calls.append((kind, label))
            return SimpleNamespace(returncode=0, stdout=json.dumps(responses[(kind, label)]))

        with mock.patch.object(self.monitor, "run", side_effect=fake_by_label):
            items = self.monitor.list_auto_loop_issues()

        self.assertEqual(
            [(item["kind"], item["number"], item["phase"]) for item in items],
            [
                ("issue", 71, label_catalog.PHASE_IMPLEMENTING),
                ("issue", 72, label_catalog.PHASE_FIXING),
                ("pr", 73, label_catalog.PHASE_REVIEWING),
                ("pr", 74, label_catalog.PHASE_DESIGN_SOLVING),
            ],
        )
        expected_calls = {
            (kind, label)
            for kind in ("issue", "pr")
            for label in label_catalog.query_labels_for(label_catalog.MANAGED)
        }
        self.assertEqual(set(calls), expected_calls)
        self.assertEqual(len(calls), len(expected_calls))

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

    def test_snapshot_projects_only_fresh_positive_update_state(self) -> None:
        now = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
        self.write_update_state(
            {
                "status": "ok",
                "checked_at": "2026-05-31T11:00:00Z",
                "interval_seconds": 21600,
                "update_available": True,
                "latest_version": "1.0.0-rc.1",
                "update_source": "github-release",
                "release_url": "https://example/release",
            }
        )

        data = self.write_snapshot(now=now)

        self.assertTrue(data["update_available"])
        self.assertEqual("1.0.0-rc.1", data["update_latest_version"])
        self.assertEqual("2026-05-31T11:00:00Z", data["update_checked_at"])
        self.assertEqual("github-release", data["update_source"])
        self.assertEqual("https://example/release", data["update_release_url"])

    def test_snapshot_omits_disabled_unknown_and_stale_update_state(self) -> None:
        now = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)
        cases = (
            {"status": "disabled", "checked_at": "2026-05-31T11:00:00Z", "update_available": True, "latest_version": "1.0.0"},
            {"status": "unknown", "checked_at": "2026-05-31T11:00:00Z", "update_available": True, "latest_version": "1.0.0"},
            {"status": "ok", "checked_at": "2026-05-30T00:00:00Z", "interval_seconds": 10, "update_available": True, "latest_version": "1.0.0"},
            {"status": "ok", "checked_at": "2026-05-31T11:00:00Z", "interval_seconds": 21600, "update_available": False, "latest_version": "1.0.0"},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                self.write_update_state(payload)
                data = self.write_snapshot(now=now)
                self.assertNotIn("update_available", data)

    def test_non_p0_tick_snapshot_path(self) -> None:
        state_path = self.repo / ".refactor-loop" / ".concurrency-monitor-state.json"
        state_path.write_text(
            json.dumps({"zero_streak": 2, "last_p0_at": "2026-05-26T01:02:03Z"}),
            encoding="utf-8",
        )

        def fake_non_p0(cmd: list[str]) -> SimpleNamespace:
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
