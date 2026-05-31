#!/usr/bin/env python3
"""Behavior tests for review-fix dispatch rendering."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.controller_actions import ControllerActions
from codex_refactor_loop.review_fix_dispatch import ReviewFixDispatchSpec


class ReviewFixDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="review-fix-dispatch-test-"))
        (self.tmp / ".refactor-loop").mkdir(parents=True)
        (self.tmp / ".refactor-loop" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\n',
            encoding="utf-8",
        )
        self.actions = ControllerActions(
            LoopContext.load(
                repo_root=self.tmp,
                skill_root=SCRIPT_DIR.parent,
                env={"REPO_ROOT": str(self.tmp)},
            )
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_spec_for_round_uses_canonical_runs_report_path(self) -> None:
        spec = ReviewFixDispatchSpec.for_round(269, 1)

        self.assertEqual(spec.fix_output_path, ".refactor-loop/runs/fix-pr269-round-1-report.md")
        self.assertEqual(spec.prompt_path, ".refactor-loop/prompts/fixes/fix-pr269-round-1.md")
        self.assertEqual(spec.log_path, ".refactor-loop/logs/fix-pr269-round-1.log")
        self.assertEqual(spec.as_render_env(), {"FIX_OUTPUT_PATH": ".refactor-loop/runs/fix-pr269-round-1-report.md"})

    def test_validate_rejects_root_report_and_path_escape(self) -> None:
        invalid = (
            "FIX_REPORT.md",
            "./FIX_REPORT.md",
            "/tmp/FIX_REPORT.md",
            ".refactor-loop/../FIX_REPORT.md",
            ".refactor-loop/logs/fix-pr269-round-1-report.md",
            ".refactor-loop/runs/fix-pr269-r1.md",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    ReviewFixDispatchSpec.validate_fix_output_path(value)

    def test_controller_render_review_fix_prompt_injects_fix_output_path(self) -> None:
        spec = self.actions.render_review_fix_prompt(
            269,
            1,
            env={
                "PR_NUMBER": "269",
                "PR_TITLE": "Review fix render",
                "FIX_ROUND": "1",
                "MAX_FIX_ROUNDS": "3",
                "BASE_BRANCH": "dev",
                "HEAD_BRANCH": "impl/issue269",
                "REVIEW_ARCHITECT_PATH": ".refactor-loop/runs/review-pr269-architect-r1.md",
                "REVIEW_TESTS_PATH": ".refactor-loop/runs/review-pr269-tests-r1.md",
                "REVIEW_QUALITY_PATH": ".refactor-loop/runs/review-pr269-quality-r1.md",
                "AUDIT_PATH": ".refactor-loop/runs/audit.md",
                "IMPLEMENT_SUMMARY_PATH": ".refactor-loop/runs/implement.md",
                "PROJECT_RULES": "CLAUDE.md",
                "HOST_REFACTOR_COMMENT_POLICY": "self-doc-comment",
            },
        )

        self.assertEqual(spec.fix_output_path, ".refactor-loop/runs/fix-pr269-round-1-report.md")
        prompt = self.tmp / ".refactor-loop" / "prompts" / "fixes" / "fix-pr269-round-1.md"
        rendered = prompt.read_text(encoding="utf-8")
        self.assertIn(".refactor-loop/runs/fix-pr269-round-1-report.md", rendered)
        self.assertNotIn("${FIX_OUTPUT_PATH}", rendered)
        self.assertTrue(prompt.exists())


if __name__ == "__main__":
    unittest.main()
