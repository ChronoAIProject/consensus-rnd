#!/usr/bin/env python3
"""Behavior tests for concurrency_monitor statusline snapshots."""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
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
        sys.modules.pop("concurrency_monitor", None)
        self.monitor = importlib.import_module("concurrency_monitor")

    def tearDown(self) -> None:
        self.env.stop()
        sys.modules.pop("concurrency_monitor", None)
        self.tmp.cleanup()

    def snapshot_path(self) -> Path:
        return self.repo / ".refactor-loop" / "state" / "statusline-snapshot.json"

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


if __name__ == "__main__":
    unittest.main()
