#!/usr/bin/env python3
"""Source-regression test for generated run artifacts used as authority."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parents[0]
RUN_JUDGE_PATH_RE = re.compile(r"\.refactor-loop/runs/phase9-issue\d+-r\d+-judge\.md")
AUTHORITY_CONTEXT_RE = re.compile(
    r"详见|严格按|按 .*consensus|Implement plan|per\b|See\b|see\b|"
    r"authority|authorization|decision|依据|来源"
)


class GeneratedArtifactsNotAuthorityTests(unittest.TestCase):
    def scanned_paths(self) -> list[Path]:
        paths = [SKILL_ROOT / "SKILL.md"]
        paths.extend(sorted((SKILL_ROOT / "prompts").glob("*.md")))
        return paths

    def test_generated_judge_artifacts_are_not_authority_sources(self) -> None:
        failures: list[str] = []
        for path in self.scanned_paths():
            text = path.read_text(encoding="utf-8")
            for line_no, line in enumerate(text.splitlines(), start=1):
                if self._forbidden_generated_artifact_authority(line):
                    failures.append(f"{path.relative_to(SKILL_ROOT)}:{line_no}:{line.strip()}")
        self.assertEqual(failures, [])

    def test_runtime_output_and_tracking_paths_remain_allowed(self) -> None:
        allowed_lines = [
            "SOLVER_OUTPUT_PATH=.refactor-loop/runs/phase9-issue410-r1-judge.md",
            "Write `.refactor-loop/runs/phase9-issue410-r1-judge.md`:",
            "tracking lives in GitHub plus `.refactor-loop/runs/phase9-issue<N>-r<M>-*.md`",
        ]
        for line in allowed_lines:
            with self.subTest(line=line):
                self.assertFalse(self._forbidden_generated_artifact_authority(line))

    def test_debug_and_antipattern_examples_remain_allowed(self) -> None:
        allowed_lines = [
            "raw evidence awaiting mirror: .refactor-loop/runs/phase9-issue410-r1-judge.md",
            "<details><summary>本机调试线索</summary> .refactor-loop/runs/phase9-issue410-r1-judge.md",
            "❌ 用 `授权:.refactor-loop/runs/phase9-issueN-rM-judge.md` 这类本地路径当唯一来源",
        ]
        for line in allowed_lines:
            with self.subTest(line=line):
                self.assertFalse(self._forbidden_generated_artifact_authority(line))

    def _forbidden_generated_artifact_authority(self, line: str) -> bool:
        if not RUN_JUDGE_PATH_RE.search(line):
            return False
        if self._allowed_generated_artifact_context(line):
            return False
        return bool(AUTHORITY_CONTEXT_RE.search(line))

    def _allowed_generated_artifact_context(self, line: str) -> bool:
        if "SOLVER_OUTPUT_PATH=" in line or line.lstrip().startswith("Write "):
            return True
        if "tracking lives in GitHub" in line:
            return True
        if "raw evidence awaiting mirror" in line:
            return True
        if "<details><summary>本机调试线索</summary>" in line:
            return True
        if "❌" in line:
            return True
        return False


if __name__ == "__main__":
    unittest.main()
