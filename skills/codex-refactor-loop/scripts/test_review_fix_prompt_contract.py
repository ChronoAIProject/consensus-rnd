#!/usr/bin/env python3
"""Source-regression tests for the review-fix prompt contract."""

from __future__ import annotations

import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent


class ReviewFixPromptContractTests(unittest.TestCase):
    def test_review_fix_prompt_has_single_positive_report_destination(self) -> None:
        text = (SKILL_ROOT / "prompts" / "review-fix.md").read_text(encoding="utf-8")

        self.assertIn("Write fix artifact", text)
        self.assertIn("`${FIX_OUTPUT_PATH}`", text)
        self.assertIn(".refactor-loop/runs/", text)
        self.assertIn("FIX_BLOCKED:env-missing:FIX_OUTPUT_PATH", text)
        self.assertIn("repo root `FIX_REPORT.md`", text)
        self.assertNotIn("Record in `FIX_REPORT.md`", text)
        self.assertNotIn("Write FIX_REPORT", text)

    def test_skill_fix_retry_loop_uses_rendered_fix_output_path(self) -> None:
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("render_review_fix_prompt", text)
        self.assertIn(".refactor-loop/runs/fix-pr${PR}-round-${N}-report.md", text)
        self.assertIn("fix artifact excerpt from `${FIX_OUTPUT_PATH}`", text)
        self.assertNotIn("writes `FIX_REPORT.md`", text)


if __name__ == "__main__":
    unittest.main()
