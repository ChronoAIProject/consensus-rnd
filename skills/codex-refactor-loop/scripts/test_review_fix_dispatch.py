#!/usr/bin/env python3
"""Behavior tests for review-fix dispatch rendering."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        (self.tmp / ".refactor-loop" / "logs").mkdir(parents=True)
        (self.tmp / ".refactor-loop" / "runs").mkdir(parents=True)
        (self.tmp / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".config" / "consensus-rnd" / "host.env").write_text(
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
        self.assertEqual(spec.as_render_env()["FIX_OUTPUT_PATH"], ".refactor-loop/runs/fix-pr269-round-1-report.md")

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

    def test_controller_render_review_fix_prompt_headless_binds_all_template_values(self) -> None:
        for role in ("architect", "tests", "quality"):
            (self.tmp / ".refactor-loop" / "runs" / f"review-pr269-{role}-r1.md").write_text(
                f"---\nverdict: reject\n---\nREVIEW_DONE:269:{role}:reject\n",
                encoding="utf-8",
            )
            (self.tmp / ".refactor-loop" / "logs" / f"review-pr269-{role}-r1.log").write_text(
                f"REVIEW_DONE:269:{role}:reject\nEXIT=0\n",
                encoding="utf-8",
            )
            (self.tmp / ".refactor-loop" / "runs" / f"review-pr269-{role}-r2.md").write_text(
                f"---\nverdict: reject\n---\nREVIEW_DONE:269:{role}:reject\n",
                encoding="utf-8",
            )
            (self.tmp / ".refactor-loop" / "logs" / f"review-pr269-{role}-r2.log").write_text(
                f"REVIEW_DONE:269:{role}:reject\nEXIT=0\n",
                encoding="utf-8",
            )

        def fake_run(argv, cwd, capture_output, text, check):
            self.assertEqual(
                argv,
                [
                    "gh",
                    "pr",
                    "view",
                    "269",
                    "--repo",
                    "example/repo",
                    "--json",
                    "title,headRefName,baseRefName",
                ],
            )
            return subprocess.CompletedProcess(
                argv,
                0,
                '{"title":"Fix render env","headRefName":"fix/review","baseRefName":"main"}',
                "",
            )

        actions = ControllerActions(
            LoopContext.load(
                repo_root=self.tmp,
                skill_root=SCRIPT_DIR.parent,
                env={"REPO_ROOT": str(self.tmp), "GH_REPO_SLUG": "example/repo"},
            )
        )
        with mock.patch("codex_refactor_loop.controller_actions.subprocess.run", side_effect=fake_run):
            spec = actions.render_review_fix_prompt(269, 2)

        rendered = (self.tmp / spec.prompt_path).read_text(encoding="utf-8")
        self.assertNotIn("${", rendered)
        self.assertIn("PR **269** (`Fix render env`). Round **2** of max **3**", rendered)
        self.assertIn("origin/main...origin/fix/review", rendered)
        self.assertIn("- `.refactor-loop/runs/review-pr269-architect-r2.md`", rendered)
        self.assertIn("- `.refactor-loop/runs/review-pr269-tests-r2.md`", rendered)
        self.assertIn("- `.refactor-loop/runs/review-pr269-quality-r2.md`", rendered)
        self.assertIn("# Fix report for PR 269 round 2", rendered)

    def test_controller_render_review_fix_prompt_uses_latest_complete_log_when_artifact_missing(self) -> None:
        for role in ("architect", "tests", "quality"):
            (self.tmp / ".refactor-loop" / "runs" / f"review-pr269-{role}-r1.md").write_text(
                f"---\nverdict: reject\n---\nREVIEW_DONE:269:{role}:reject\n",
                encoding="utf-8",
            )
            (self.tmp / ".refactor-loop" / "logs" / f"review-pr269-{role}-r1.log").write_text(
                f"REVIEW_DONE:269:{role}:reject\nEXIT=0\n",
                encoding="utf-8",
            )
            (self.tmp / ".refactor-loop" / "logs" / f"review-pr269-{role}-r3.log").write_text(
                f"REVIEW_DONE:269:{role}:reject\nEXIT=0\n",
                encoding="utf-8",
            )

        with mock.patch.object(
            self.actions,
            "gh",
            return_value=subprocess.CompletedProcess(
                ["gh"],
                0,
                '{"title":"Fallback","headRefName":"head","baseRefName":"base"}',
                "",
            ),
        ):
            spec = self.actions.render_review_fix_prompt(269, 1)

        rendered = (self.tmp / spec.prompt_path).read_text(encoding="utf-8")
        self.assertNotIn("${", rendered)
        self.assertIn("- `.refactor-loop/logs/review-pr269-architect-r3.log`", rendered)
        self.assertIn("- `.refactor-loop/logs/review-pr269-tests-r3.log`", rendered)
        self.assertIn("- `.refactor-loop/logs/review-pr269-quality-r3.log`", rendered)


if __name__ == "__main__":
    unittest.main()
