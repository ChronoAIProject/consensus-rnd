#!/usr/bin/env python3
"""Suite-level guard against daemon leaks rooted at this test worktree."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
CLI = SCRIPT_DIR / "consensus-rnd-cli"


class DaemonLeakGuardTests(unittest.TestCase):
    def test_phase9_router_daemon_does_not_leak_from_this_worktree_cli(self) -> None:
        result = subprocess.run(
            ["ps", "-eo", "pid=,command="],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        leaked = [
            line.strip()
            for line in result.stdout.splitlines()
            if str(CLI) in line and " phase9-router --daemon" in line
        ]
        self.assertEqual([], leaked, "phase9-router daemon leaked from this test worktree CLI")


if __name__ == "__main__":
    unittest.main()
