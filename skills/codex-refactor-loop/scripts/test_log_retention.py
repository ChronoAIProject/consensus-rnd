#!/usr/bin/env python3
"""Compatibility tests for consensus-rnd-cli log-retention."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
CLI = SCRIPT_DIR / "consensus-rnd-cli"

sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import runtime_retention
from codex_refactor_loop.cli import COMMANDS


class LogRetentionCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="log-retention-alias-test-"))
        self.repo = self.tmp_root / "repo"
        (self.repo / ".refactor-loop").mkdir(parents=True)
        (self.repo / ".refactor-loop" / "host.env").write_text(
            f'export REPO_ROOT="{self.repo}"\nexport RUNTIME_RETENTION_ENABLE="false"\n',
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_log_retention_cli_is_runtime_retention_alias(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CLI), "log-retention"],
            cwd=self.repo,
            env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": os.environ.get("PYTHONPATH", "")},
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("runtime_retention: enabled=false", result.stdout)
        self.assertEqual(COMMANDS["log-retention"].handler, COMMANDS["runtime-retention"].handler)
        self.assertEqual(24, runtime_retention.RETENTION_TTL_HOURS)

    def test_log_retention_wrapper_has_no_independent_owner_logic(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "retention.py").read_text(encoding="utf-8")
        self.assertIn("Compatibility alias for RuntimeRetention", text)
        self.assertIn("runtime_retention_main", text)
        for forbidden in ("path.unlink", "time.time", "subprocess.run", "worktree", "gh ", "git ", "commit", "push", "merge", "label"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
