#!/usr/bin/env python3
"""Source-regression tests for public consensus-loop positioning."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
SKILL_ROOT = REPO_ROOT / "skills" / "consensus-loop"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def frontmatter(text: str) -> str:
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise AssertionError("missing SKILL.md frontmatter")
    return parts[1]


class RepositoryPublicDocsTests(unittest.TestCase):
    def assert_issue_pr_engine_positioning(self, text: str, *, label: str) -> None:
        body = normalized(text)
        self.assertRegex(
            body,
            r"repo-owned github issues?/prs?|repo-owned github issue/pr|managed github issue/pr",
            msg=f"{label} must lead with repo-owned managed GitHub issue/PR work",
        )
        self.assertIn("managed", body, msg=f"{label} must keep the managed-work boundary visible")
        self.assertRegex(
            body,
            r"audit/refactor[^.|\n]*fallback|audit[^.|\n]*fallback[^.|\n]*producer",
            msg=f"{label} must keep audit/refactor as fallback producer, not the main path",
        )

    def assert_honest_scope_boundary(self, text: str, *, label: str) -> None:
        body = normalized(text)
        self.assertIn("bounded repo-owned work", body, msg=f"{label} must state bounded scope")
        self.assertRegex(body, r"feature.*bug.*documentation.*governance.*refactor")
        self.assertRegex(body, r"projects.*milestones.*assignee.*discussions")
        for boundary in ("label-taxonomy", "issue/pr body", "tag/release"):
            self.assertIn(boundary, body, msg=f"{label} must name {boundary} as out of scope")
        self.assertRegex(body, r"custom lifecycle|自定义 lifecycle")
        for forbidden in (
            "any github situation",
            "any github work",
            "arbitrary github situation",
            "任何 github 情况",
            "任意 github 情况",
        ):
            self.assertNotIn(forbidden, body, msg=f"{label} overclaims scope")

    def test_skill_frontmatter_positions_consensus_loop_as_issue_pr_engine(self) -> None:
        fm = frontmatter(read(SKILL_ROOT / "SKILL.md"))

        self.assertLessEqual(len(fm), 1024)
        self.assert_issue_pr_engine_positioning(fm, label="SKILL frontmatter")

    def test_readme_pair_positions_main_path_and_honest_boundary(self) -> None:
        for path in (REPO_ROOT / "README.md", REPO_ROOT / "README.zh-CN.md"):
            text = read(path)
            with self.subTest(path=path.name):
                self.assert_issue_pr_engine_positioning(text, label=path.name)
                self.assert_honest_scope_boundary(text, label=path.name)

    def test_skill_trigger_section_states_honest_boundary(self) -> None:
        skill = read(SKILL_ROOT / "SKILL.md")
        section = skill.split("## Main path and fallback producer", 1)[1].split("## Operational names", 1)[0]

        self.assert_issue_pr_engine_positioning(section, label="SKILL trigger section")
        self.assert_honest_scope_boundary(section, label="SKILL trigger section")

    def test_platform_manifests_expose_issue_pr_first_copy(self) -> None:
        manifest_paths = (
            REPO_ROOT / ".codex-plugin/plugin.json",
            REPO_ROOT / ".claude-plugin/plugin.json",
            REPO_ROOT / ".claude-plugin/marketplace.json",
            REPO_ROOT / ".cursor-plugin/plugin.json",
            REPO_ROOT / "gemini-extension.json",
            REPO_ROOT / "package.json",
        )
        for path in manifest_paths:
            text = json.dumps(json.loads(read(path)), ensure_ascii=False)
            with self.subTest(path=path.as_posix()):
                self.assert_issue_pr_engine_positioning(text, label=path.as_posix())

        gemini = read(REPO_ROOT / "GEMINI.md")
        self.assert_issue_pr_engine_positioning(gemini, label="GEMINI.md")


if __name__ == "__main__":
    unittest.main()
