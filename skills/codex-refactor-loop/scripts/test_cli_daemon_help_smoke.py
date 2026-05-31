"""Smoke test: every consensus-rnd-cli daemon/operation subcommand parses --help cleanly.

Refactor (iter1/issue-160-phase4):
  Old pattern: the all-Python migration's full test suite passed even though
    sync/dev.py main() referenced argparse without importing it, because no
    test exercised the daemon main() arg-parser; the daemon only crash-looped
    at runtime (NameError) where a stale heartbeat surfaced it.
  New principle: a CLI smoke test invokes `consensus-rnd-cli <op> --help` for
    every daemon/operation subcommand and asserts a clean exit with no import
    error, so a missing import in any command main() fails a test, not a live
    daemon.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

CLI = Path(__file__).resolve().parent / "consensus-rnd-cli"

# Operations whose main() builds an arg parser / imports modules at entry.
OPERATIONS = [
    "dev-sync",
    "concurrency",
    "phase9-router",
    "comment-monitor",
    "closed-label-reconciler",
    "progress-reporter",
    "release-gate",
    "statusline",
    "daemon-status",
    "restart-daemons",
    "spawn-codex",
    "wakeup-plan",
    "peek",
]


class CliDaemonHelpSmokeTests(unittest.TestCase):
    def test_every_operation_help_parses_without_import_error(self) -> None:
        offenders = []
        for op in OPERATIONS:
            result = subprocess.run(
                [sys.executable, str(CLI), op, "--help"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            blob = result.stdout + result.stderr
            if result.returncode != 0 or "usage:" not in blob or "NameError" in blob or "ImportError" in blob or "ModuleNotFoundError" in blob:
                reason = blob.strip().splitlines()[-1] if blob.strip() else f"returncode={result.returncode}"
                offenders.append(f"{op}: {reason}")
        self.assertEqual(offenders, [], f"command main() import errors: {offenders}")

    def test_peek_python_invocation_parses_help(self) -> None:
        # Refactor (issue-303): controller docs invoke the Python CLI directly,
        # so keep the targeted `python3 consensus-rnd-cli peek --help` path
        # covered even if the broader operation list changes.
        result = subprocess.run(
            [sys.executable, str(CLI), "peek", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        blob = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, blob)
        self.assertIn("usage:", blob)
        self.assertIn("peek", blob)


if __name__ == "__main__":
    unittest.main()
