#!/usr/bin/env python3
"""Source-regression test for numeric workflow stage display names."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parents[0]


class NoNumericWorkflowPhaseNamesTests(unittest.TestCase):
    def normative_paths(self) -> list[Path]:
        paths = [SKILL_ROOT / "SKILL.md"]
        paths.extend(sorted((SKILL_ROOT / "prompts").glob("*.md")))
        paths.extend(sorted((SKILL_ROOT / "scripts" / "codex_refactor_loop").rglob("*.py")))
        paths.extend(sorted((SKILL_ROOT / "scripts").glob("test_*.py")))
        return paths

    def test_normative_sources_do_not_use_numeric_workflow_phase_names(self) -> None:
        pattern = re.compile(r"\bPhase\s+[0-9]\b")
        failures: list[str] = []
        for path in self.normative_paths():
            text = path.read_text(encoding="utf-8")
            for line_no, line in enumerate(text.splitlines(), start=1):
                if not pattern.search(line):
                    continue
                if self._allowed_compatibility_line(path, line):
                    continue
                failures.append(f"{path.relative_to(SKILL_ROOT)}:{line_no}:{line.strip()}")
        self.assertEqual(failures, [])

    def _allowed_compatibility_line(self, path: Path, line: str) -> bool:
        relative = path.relative_to(SKILL_ROOT).as_posix()
        if relative == "scripts/codex_refactor_loop/workflow_stages.py" and "legacy_number" in line:
            return True
        if ".refactor-loop/runs/phase9-issue" in line:
            return True
        if "phase9-router" in line:
            return True
        return False


if __name__ == "__main__":
    unittest.main()
