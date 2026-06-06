#!/usr/bin/env python3
"""Behavior tests for patrol finding fingerprints."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.patrol import PatrolFinding


class PatrolFingerprintTests(unittest.TestCase):
    def test_fingerprint_is_stable_and_ignores_evidence_churn(self) -> None:
        first = PatrolFinding(
            kind="exception-log",
            source=".refactor-loop/logs/a.log",
            summary="runtime log reports exception signals in a.log",
            severity="high",
            evidence=("Traceback line 1",),
        )
        second = PatrolFinding(
            kind=first.kind,
            source=first.source,
            summary=first.summary,
            severity=first.severity,
            evidence=("Traceback line 2",),
        )

        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertRegex(first.fingerprint, r"^[0-9a-f]{16}$")

    def test_fingerprint_changes_for_different_patrol_source(self) -> None:
        first = PatrolFinding("exception-log", ".refactor-loop/logs/a.log", "a failed", "high", ("failed",))
        second = PatrolFinding("exception-log", ".refactor-loop/logs/b.log", "b failed", "high", ("failed",))

        self.assertNotEqual(first.fingerprint, second.fingerprint)


if __name__ == "__main__":
    unittest.main()
