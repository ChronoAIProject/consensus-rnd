#!/usr/bin/env python3
"""Source-regression tests for the test-owned controller runtime inventory."""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.controller_runtime_inventory import CONTROLLER_RUNTIME_INVENTORY
from codex_refactor_loop.restart import restart_managed_daemon_names


ALLOWED_FIELDS = {
    "daemon_name",
    "owner_module",
    "cli_command",
    "tick_import_path",
    "authority_note",
    "test_owner",
}

FORBIDDEN_FIELDS = {
    "callable",
    "argv",
    "shell",
    "cmd",
    "command",
    "env",
    "git",
    "gh",
    "executor",
    "dispatch",
    "authorization",
    "pending_events",
    "state_transition",
    "lifecycle_owner",
}


class ControllerRuntimeInventoryTests(unittest.TestCase):
    def test_inventory_is_data_only_and_matches_restart_managed_daemons(self) -> None:
        rows = list(CONTROLLER_RUNTIME_INVENTORY)

        self.assertEqual(list(restart_managed_daemon_names()), [row["daemon_name"] for row in rows])
        for row in rows:
            with self.subTest(row=row["daemon_name"]):
                self.assertEqual(ALLOWED_FIELDS, set(row))
                self.assertTrue(all(isinstance(value, str) and value for value in row.values()))
                self.assertTrue(row["tick_import_path"].startswith(row["owner_module"] + ".run_"))
                self.assertTrue(row["tick_import_path"].endswith("_reconcile_tick"))
                self.assertNotIn(row["daemon_name"], row["authority_note"])
                self.assertTrue(row["test_owner"].startswith("skills/consensus-loop/scripts/test_"))

    def test_inventory_source_has_no_executable_or_authorization_fields(self) -> None:
        source = SCRIPT_DIR / "codex_refactor_loop" / "controller_runtime_inventory.py"
        module = ast.parse(source.read_text(encoding="utf-8"))
        literal_keys: set[str] = set()
        for node in ast.walk(module):
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        literal_keys.add(key.value)
            self.assertNotIsInstance(node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))

        self.assertTrue(literal_keys)
        self.assertTrue(literal_keys <= ALLOWED_FIELDS)
        self.assertTrue(FORBIDDEN_FIELDS.isdisjoint(literal_keys))

    def test_runtime_execution_modules_do_not_import_inventory(self) -> None:
        runtime_paths = [
            SCRIPT_DIR / "codex_refactor_loop" / "restart.py",
            SCRIPT_DIR / "codex_refactor_loop" / "monitors" / "comment.py",
            SCRIPT_DIR / "codex_refactor_loop" / "monitors" / "progress.py",
            SCRIPT_DIR / "codex_refactor_loop" / "monitors" / "concurrency.py",
            SCRIPT_DIR / "codex_refactor_loop" / "sync" / "dev.py",
            SCRIPT_DIR / "codex_refactor_loop" / "phase9" / "router.py",
            SCRIPT_DIR / "codex_refactor_loop" / "closed_label_reconciler.py",
            SCRIPT_DIR / "codex_refactor_loop" / "wakeup_runner.py",
        ]
        for path in runtime_paths:
            with self.subTest(path=path.relative_to(SCRIPT_DIR)):
                self.assertNotIn("controller_runtime_inventory", path.read_text(encoding="utf-8"))

    def test_inventory_does_not_define_generated_runtime_residue_or_cleanup_surface(self) -> None:
        source = SCRIPT_DIR / "codex_refactor_loop" / "controller_runtime_inventory.py"
        text = source.read_text(encoding="utf-8")

        for token in (
            "__pycache__",
            "cleanup",
            "retention",
            ".refactor-loop",
            "pending-events",
            "pending_events",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main()
