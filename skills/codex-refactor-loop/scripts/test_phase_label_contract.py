#!/usr/bin/env python3
"""Source-regression tests for declaration-only phase-label contract."""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.phase_label_contract import PHASE_LABEL_CONTRACT


class PhaseLabelContractTests(unittest.TestCase):
    def test_contract_is_declaration_only(self) -> None:
        self.assertEqual(
            {
                "contract_owner",
                "open_managed_reader",
                "closed_reconciler_planner",
                "execution_import_policy",
            },
            set(PHASE_LABEL_CONTRACT),
        )
        self.assertEqual(
            "codex_refactor_loop.managed_work_snapshot.ManagedWorkSnapshot",
            PHASE_LABEL_CONTRACT["open_managed_reader"],
        )
        self.assertIn("must not import", PHASE_LABEL_CONTRACT["execution_import_policy"])

        source = SCRIPT_DIR / "codex_refactor_loop" / "phase_label_contract.py"
        module = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(module):
            self.assertNotIsInstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda))

    def test_label_mutation_execution_modules_do_not_import_contract(self) -> None:
        execution_modules = [
            SCRIPT_DIR / "codex_refactor_loop" / "closed_label_reconciler.py",
            SCRIPT_DIR / "codex_refactor_loop" / "closed_phase_labels.py",
            SCRIPT_DIR / "codex_refactor_loop" / "wakeup_runner.py",
            SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py",
        ]
        for path in execution_modules:
            with self.subTest(path=path.relative_to(SCRIPT_DIR)):
                self.assertNotIn("phase_label_contract", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
