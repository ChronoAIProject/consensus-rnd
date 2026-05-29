#!/usr/bin/env python3
"""Source-regression tests for the Python restart supervisor."""

from __future__ import annotations

import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


class RestartSupervisorSourceRegressionTests(unittest.TestCase):
    def test_static_daemon_allowlist_and_forbidden_boundary(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "restart.py").read_text(encoding="utf-8")
        for daemon in ("concurrency_monitor", "comment-monitor", "codex-progress-reporter", "dev_sync_daemon", "phase9_router_daemon"):
            with self.subTest(daemon=daemon):
                self.assertIn(daemon, text)
        for forbidden in ("generic lifecycle actor", "tag/release publish", "create/merge/close PR"):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, text)
        self.assertNotIn("gh pr merge", text)
        self.assertNotIn("gh issue close", text)


if __name__ == "__main__":
    unittest.main()
