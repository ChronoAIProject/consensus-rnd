#!/usr/bin/env python3
"""Behavior tests for implementation PR artifact validation."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.implementation_pr_artifacts import validate_implementation_pr_artifacts  # noqa: E402

FINAL_SENTINEL = "\u27e6AI:AUTO-LOOP\u27e7"
ZH_CHANGED_CONTENT = "- \u66f4\u65b0 implementation PR artifact validator"
ZH_TEST_CONTENT = "- \u5355\u5143\u6d4b\u8bd5\u901a\u8fc7"
ZH_DEVIATION_CONTENT = "- \u65e0"
ZH_CHANGED_HEADING = "## \u4fee\u6539\u6587\u4ef6"
ZH_TEST_HEADING = "## \u6d4b\u8bd5\u7ed3\u679c"
ZH_DEVIATION_HEADING = "## deviation \u8bb0\u5f55"


class ImplementationPrArtifactsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)
        self.runs = self.repo / ".refactor-loop" / "runs"
        self.runs.mkdir(parents=True)
        self.title = self.runs / "implementation-pr-issue-77-title.txt"
        self.body = self.runs / "implementation-pr-issue-77-body.md"
        self.action = {
            "source_marker": "IMPLEMENT_DONE:issue-77:ok",
            "title_file": ".refactor-loop/runs/implementation-pr-issue-77-title.txt",
            "body_file": ".refactor-loop/runs/implementation-pr-issue-77-body.md",
        }

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def write_artifacts(self, body: str, title: str = "Fix publish artifact validation\n") -> None:
        self.title.write_text(title, encoding="utf-8")
        self.body.write_text(body, encoding="utf-8")

    def body_with_content(self, *, changed: str, tests: str, deviations: str) -> str:
        return (
            "## Changed files\n\n"
            f"{changed}\n\n"
            "## Test results\n\n"
            f"{tests}\n\n"
            "## Deviations\n\n"
            f"{deviations}\n\n"
            "Closes #77\n\n"
            f"{FINAL_SENTINEL}\n"
        )

    def validate(self) -> str | None:
        return validate_implementation_pr_artifacts(self.repo, self.runs, self.action, 77).reason

    def test_fixed_required_section_markers_allow_english_and_chinese_content(self) -> None:
        cases = (
            self.body_with_content(
                changed="- skills/codex-refactor-loop/scripts/codex_refactor_loop/implementation_pr_artifacts.py",
                tests="- python3 -m unittest skills/codex-refactor-loop/scripts/test_implementation_pr_artifacts.py",
                deviations="- none",
            ),
            self.body_with_content(
                changed=ZH_CHANGED_CONTENT,
                tests=ZH_TEST_CONTENT,
                deviations=ZH_DEVIATION_CONTENT,
            ),
        )
        for body in cases:
            with self.subTest(body=body):
                self.write_artifacts(body)
                self.assertIsNone(self.validate())

    def test_fixed_required_section_markers_reject_missing_or_translated_heading(self) -> None:
        valid_body = self.body_with_content(changed="- x", tests="- true", deviations="- none")
        for heading in ("## Changed files", "## Test results", "## Deviations"):
            with self.subTest(heading=heading):
                self.write_artifacts(valid_body.replace(heading, f"{heading} details"))
                self.assertEqual("implementation_pr_body_required_section_missing", self.validate())

        self.write_artifacts(
            valid_body.replace("## Changed files", ZH_CHANGED_HEADING).replace("## Test results", ZH_TEST_HEADING).replace(
                "## Deviations", ZH_DEVIATION_HEADING
            )
        )
        self.assertEqual("implementation_pr_body_required_section_missing", self.validate())

    def test_language_independent_placeholder_title_and_body_headings_are_rejected(self) -> None:
        valid_body = self.body_with_content(changed="- x", tests="- true", deviations="- none")
        self.write_artifacts(valid_body, title="Implement issue #77\n")
        self.assertEqual("implementation_pr_title_placeholder", self.validate())

        self.write_artifacts(f"## Implement issue #77\n\n{valid_body}")
        self.assertEqual("implementation_pr_body_placeholder", self.validate())


if __name__ == "__main__":
    unittest.main()
