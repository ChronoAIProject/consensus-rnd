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

    def test_registered_commands_are_python_handlers_not_shell_scripts(self) -> None:
        for name, spec in COMMANDS.items():
            with self.subTest(command=name):
                self.assertTrue(callable(spec.handler))
                self.assertTrue(spec.handler.__module__.startswith("codex_refactor_loop"))

    def test_peek_command_uses_registered_python_handler(self) -> None:
        router = RuntimeCommandRouter(script_dir=SCRIPT_DIR)
        handler = mock.Mock(return_value=0)
        with mock.patch.dict("codex_refactor_loop.cli.COMMANDS", {"peek": COMMANDS["peek"].__class__(handler, "peek", True), **{k: v for k, v in COMMANDS.items() if k != "peek"}}):
            self.assertEqual(0, router.run("peek", ["--flag"]))
        handler.assert_called_once_with(["--flag"])

    def test_controller_actions_dispatch_to_python_action_handler(self) -> None:
        router = RuntimeCommandRouter(script_dir=SCRIPT_DIR)
        handler = mock.Mock(return_value=0)
        with mock.patch.dict("codex_refactor_loop.cli.COMMANDS", {"merge-pr": COMMANDS["merge-pr"].__class__(handler, "merge")}):
            self.assertEqual(0, router.run("merge-pr", ["123", "45"]))
        handler.assert_called_once_with(["merge-pr", "123", "45"])


if __name__ == "__main__":
    unittest.main()
