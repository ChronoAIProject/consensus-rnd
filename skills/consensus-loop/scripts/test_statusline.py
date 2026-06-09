#!/usr/bin/env python3
"""Behavior tests for the consensus-rnd-cli statusline operation."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
CLI = SCRIPT_DIR / "consensus-rnd-cli"


class StatuslineCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="statusline-test-"))
        (self.tmp / ".refactor-loop" / "state").mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_snapshot(self, payload: dict[str, object]) -> None:
        path = self.tmp / ".refactor-loop" / "state" / "statusline-snapshot.json"
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    def run_statusline(self) -> str:
        result = subprocess.run(
            [sys.executable, str(CLI), "statusline"],
            env={"REPO_ROOT": str(self.tmp)},
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def test_statusline_runs_under_200ms(self) -> None:
        self.write_snapshot({"actual": 7, "expected": 5, "floor": 4, "p0_streak": 0, "open_pr_count": 5, "open_issue_count": 4, "freeze_minutes": 0})
        start = time.monotonic()
        output = self.run_statusline()
        elapsed_ms = (time.monotonic() - start) * 1000
        self.assertLess(elapsed_ms, 200, output)

    def test_entrypoint_statusline_does_not_import_full_router(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-X",
                "importtime",
                str(CLI),
                "statusline",
            ],
            env={"REPO_ROOT": str(self.tmp)},
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("⏸ no-snapshot", result.stdout)
        self.assertNotIn("codex_refactor_loop.cli", result.stderr)

    def test_no_snapshot_returns_placeholder(self) -> None:
        self.assertEqual("⏸ no-snapshot", self.run_statusline())

    def test_healthy_state_shows_actual_floor(self) -> None:
        self.write_snapshot({"actual": 7, "expected": 5, "floor": 4, "p0_streak": 0, "open_pr_count": 5, "open_issue_count": 4, "freeze_minutes": 0})
        self.assertIn("7/4", self.run_statusline())

    def test_below_floor_shows_warning_icon(self) -> None:
        self.write_snapshot({"actual": 2, "expected": 5, "floor": 4, "p0_streak": 0, "open_pr_count": 5, "open_issue_count": 4, "freeze_minutes": 0})
        self.assertIn("⚠", self.run_statusline())

    def test_freeze_minutes_over_10_shows_stuck(self) -> None:
        self.write_snapshot({"actual": 7, "expected": 5, "floor": 4, "p0_streak": 0, "open_pr_count": 5, "open_issue_count": 4, "freeze_minutes": 15})
        self.assertIn("[STUCK 15m]", self.run_statusline())

    def test_p0_streak_over_2_shows_highlight(self) -> None:
        self.write_snapshot({"actual": 7, "expected": 5, "floor": 4, "p0_streak": 3, "open_pr_count": 5, "open_issue_count": 4, "freeze_minutes": 0})
        self.assertIn("P0×3", self.run_statusline())

    def test_all_daemons_healthy_shows_full_count(self) -> None:
        self.write_snapshot({"actual": 7, "expected": 5, "floor": 4, "p0_streak": 0, "open_pr_count": 5, "open_issue_count": 4, "freeze_minutes": 0, "daemons_healthy": 5, "daemons_total": 5})
        output = self.run_statusline()
        self.assertIn("d:5/5", output)
        self.assertNotIn("⚠", output)

    def test_stale_daemon_shows_warning_icon(self) -> None:
        self.write_snapshot({"actual": 7, "expected": 5, "floor": 4, "p0_streak": 0, "open_pr_count": 5, "open_issue_count": 4, "freeze_minutes": 0, "daemons_healthy": 3, "daemons_total": 5})
        output = self.run_statusline()
        self.assertIn("d:3/5", output)
        self.assertIn("⚠", output)

    def test_no_daemons_field_omits_segment(self) -> None:
        self.write_snapshot({"actual": 7, "expected": 5, "floor": 4, "p0_streak": 0, "open_pr_count": 5, "open_issue_count": 4, "freeze_minutes": 0})
        self.assertNotIn(" d:", self.run_statusline())

    def test_update_available_renders_latest_version_from_snapshot(self) -> None:
        self.write_snapshot({"actual": 7, "expected": 5, "floor": 4, "p0_streak": 0, "open_pr_count": 5, "open_issue_count": 4, "freeze_minutes": 0, "update_available": True, "update_latest_version": "1.0.0-rc.1"})
        self.assertIn("up:v1.0.0-rc.1", self.run_statusline())

    def test_display_only_github_login_renders_without_authority_label(self) -> None:
        self.write_snapshot(
            {
                "actual": 7,
                "expected": 5,
                "floor": 4,
                "p0_streak": 0,
                "open_pr_count": 5,
                "open_issue_count": 4,
                "freeze_minutes": 0,
                "current_github_login": "octocat",
                "identity_authority": "display-only",
            }
        )

        output = self.run_statusline()

        self.assertIn("gh:octocat", output)
        self.assertNotIn("owner_login", output)


if __name__ == "__main__":
    unittest.main()
