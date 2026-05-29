#!/usr/bin/env python3
"""Behavior tests for the codex_loop.py command router."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.cli import COMMANDS, RuntimeCommandRouter


CODEX_LOOP = SCRIPT_DIR / "codex_loop.py"


class RuntimeCommandRouterTests(unittest.TestCase):
    def test_each_public_operation_is_registered(self) -> None:
        self.assertEqual(
            {
                "spawn-codex",
                "peek",
                "restart-daemons",
                "statusline",
                "comment-monitor",
                "progress-reporter",
                "merge-pr",
                "open-pr",
            },
            set(COMMANDS),
        )

    def test_help_imports_lightly_and_does_not_run_legacy_scripts(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CODEX_LOOP), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("codex-refactor-loop controller command router", result.stdout)

    def test_unknown_command_exits_2(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CODEX_LOOP), "does-not-exist"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("unknown command: does-not-exist", result.stderr)

    def test_shell_commands_delegate_to_existing_scripts(self) -> None:
        router = RuntimeCommandRouter(script_dir=SCRIPT_DIR)
        with mock.patch("codex_refactor_loop.cli.subprocess.call", return_value=0) as call:
            self.assertEqual(0, router.run("peek", ["--flag"]))
        call.assert_called_once_with(["bash", str(SCRIPT_DIR / "peek.sh"), "--flag"])

    def test_controller_lib_commands_delegate_to_existing_functions(self) -> None:
        router = RuntimeCommandRouter(script_dir=SCRIPT_DIR)
        with mock.patch("codex_refactor_loop.cli.subprocess.call", return_value=0) as call:
            self.assertEqual(0, router.run("merge-pr", ["123", "45"]))
        argv = call.call_args.args[0]
        self.assertEqual(["bash", "-c"], argv[:2])
        self.assertIn("controller_lib.sh", argv[2])
        self.assertIn("merge_pr '123' '45'", argv[2])


if __name__ == "__main__":
    unittest.main()
