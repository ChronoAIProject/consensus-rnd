#!/usr/bin/env python3
"""Source-regression tests for the GitHub label protocol contract."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import labels


class LabelContractSourceTests(unittest.TestCase):
    def test_skill_names_catalog_and_does_not_restore_bootstrap_truth_table(self) -> None:
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("codex_refactor_loop.labels", text)
        self.assertIn("crnd:<group>:<slug>", text)
        self.assertIn("phase|human|lifecycle|triage|milestone", text)
        self.assertIn("external_defaults", text)
        self.assertNotIn('for l in "🔍 phase:design-solving"', text)
        self.assertNotIn("grep -E '^(🔍|✅|🛠️|🚀|👀|🔧|⚙️|🎉|⏸️) phase:'", text)

    def test_canonical_crnd_literals_are_registered(self) -> None:
        allowed_paths = {
            SCRIPT_DIR / "codex_refactor_loop" / "labels.py",
            Path(__file__).resolve(),
        }
        registered = set(labels.canonical_labels())
        pattern = re.compile(r"crnd:[a-z]+:[a-z0-9-]+")
        for path in list((SCRIPT_DIR / "codex_refactor_loop").rglob("*.py")) + [SKILL_ROOT / "SKILL.md"] + list((SKILL_ROOT / "prompts").glob("*.md")):
            if path in allowed_paths:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for literal in pattern.findall(text):
                with self.subTest(path=path.relative_to(SKILL_ROOT), literal=literal):
                    self.assertIn(literal, registered)

    def test_runtime_code_has_no_legacy_routing_literals_outside_catalog(self) -> None:
        allow = {
            SCRIPT_DIR / "codex_refactor_loop" / "labels.py",
            SCRIPT_DIR / "test_label_migration.py",
        }
        forbidden = ("auto-loop-triage", "phase9-auto-solve", "refactor-design-needed")
        for path in (SCRIPT_DIR / "codex_refactor_loop").rglob("*.py"):
            if path in allow:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for token in forbidden:
                with self.subTest(path=path.relative_to(SKILL_ROOT), token=token):
                    self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main()
