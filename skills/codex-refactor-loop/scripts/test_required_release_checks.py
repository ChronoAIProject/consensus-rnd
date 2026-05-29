#!/usr/bin/env python3
"""Behavior tests for the shared release required-check projection."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
NOW = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
sys.path.insert(0, str(SCRIPT_PATH.parent))

from codex_refactor_loop.release.required_checks import (
    REQUIRED_RELEASE_CHECKS,
    ReleaseRequiredChecksProjection,
    isoformat,
)


def check_run(name: str, *, conclusion: str = "success", status: str = "completed", at: datetime = NOW) -> dict[str, str]:
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "created_at": isoformat(at),
        "started_at": isoformat(at),
        "completed_at": isoformat(at) if status == "completed" else "",
    }


class FakeRunner:
    def __init__(self, payloads: list[object] | None = None, *, returncode: int = 0, stdout: str | None = None) -> None:
        self.payloads = list(payloads or [])
        self.returncode = returncode
        self.stdout = stdout
        self.commands: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(cmd)
        if self.returncode:
            return subprocess.CompletedProcess(cmd, self.returncode, stdout="", stderr="simulated failure")
        if self.stdout is not None:
            return subprocess.CompletedProcess(cmd, 0, stdout=self.stdout, stderr="")
        payload = self.payloads.pop(0) if self.payloads else {"check_runs": []}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")


class ReleaseRequiredChecksProjectionTests(unittest.TestCase):
    def test_all_required_exact_check_run_names_success(self) -> None:
        runner = FakeRunner([{"check_runs": [check_run(name) for name in REQUIRED_RELEASE_CHECKS]}])
        projection = ReleaseRequiredChecksProjection(runner=runner, now=lambda: NOW)

        status = projection.check_ref("owner/repo", "dev", since=NOW - timedelta(hours=2))

        self.assertTrue(status.passed)
        self.assertEqual(status.reason, None)
        self.assertTrue(all(status.checks.values()))
        self.assertEqual(runner.commands[0][2], "repos/owner/repo/commits/dev/check-runs")

    def test_missing_required_check_fails_closed(self) -> None:
        runner = FakeRunner([{"check_runs": [check_run("consensus-rnd-ci")]}])
        projection = ReleaseRequiredChecksProjection(runner=runner, now=lambda: NOW)

        status = projection.check_ref("owner/repo", "dev", since=NOW - timedelta(hours=2))

        self.assertFalse(status.passed)
        self.assertEqual(status.reason, "missing_required_checks_recent_green")
        self.assertFalse(status.checks["contract-tests"])

    def test_latest_red_beats_older_success_for_same_name(self) -> None:
        old = NOW - timedelta(minutes=30)
        runner = FakeRunner([{"check_runs": [
            check_run("contract-tests", at=old),
            check_run("contract-tests", conclusion="failure", at=NOW),
            check_run("manifest-version-sync"),
            check_run("skill-degradation"),
        ]}])
        projection = ReleaseRequiredChecksProjection(runner=runner, now=lambda: NOW)

        status = projection.check_ref("owner/repo", "dev", since=NOW - timedelta(hours=2))

        self.assertFalse(status.passed)
        self.assertEqual(status.reason, "ci_red")
        self.assertEqual(status.red_checks, [{"name": "contract-tests", "conclusion": "failure"}])

    def test_pending_required_check_fails_after_timeout(self) -> None:
        runner = FakeRunner([{"check_runs": [
            check_run("contract-tests", status="in_progress", conclusion=""),
            check_run("manifest-version-sync"),
            check_run("skill-degradation"),
        ]}])
        projection = ReleaseRequiredChecksProjection(runner=runner, now=lambda: NOW)

        status = projection.check_ref("owner/repo", "dev", since=NOW - timedelta(hours=2), wait_seconds=0)

        self.assertFalse(status.passed)
        self.assertEqual(status.reason, "pending_required_checks")
        self.assertEqual(status.pending_checks, ["contract-tests"])

    def test_stale_completed_check_fails_closed(self) -> None:
        stale = NOW - timedelta(hours=3)
        runner = FakeRunner([{"check_runs": [
            check_run("contract-tests", at=stale),
            check_run("manifest-version-sync"),
            check_run("skill-degradation"),
        ]}])
        projection = ReleaseRequiredChecksProjection(runner=runner, now=lambda: NOW)

        status = projection.check_ref("owner/repo", "dev", since=NOW - timedelta(hours=2))

        self.assertFalse(status.passed)
        self.assertEqual(status.reason, "stale_required_checks")
        self.assertEqual(status.stale_checks, ["contract-tests"])

    def test_invalid_json_and_api_failure_fail_closed(self) -> None:
        cases = (
            (FakeRunner(stdout="{not-json"), "invalid_json"),
            (FakeRunner(returncode=2), "api_failure"),
        )
        for runner, expected in cases:
            with self.subTest(expected=expected):
                projection = ReleaseRequiredChecksProjection(runner=runner, now=lambda: NOW)

                status = projection.check_ref("owner/repo", "dev", since=NOW - timedelta(hours=2))

                self.assertFalse(status.passed)
                self.assertEqual(status.reason, expected)


if __name__ == "__main__":
    unittest.main()
