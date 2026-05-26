#!/usr/bin/env python3
"""Source-regression: SKILL.md Concurrency Floor 段必须明文 in-flight 不算 actionable marker."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__)
SKILL_ROOT = SCRIPT_PATH.parents[1]
SKILL_MD = SKILL_ROOT / "SKILL.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class FloorFillNotOptionalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill = read(SKILL_MD)
        match = re.search(
            r"## Concurrency Floor\s*\n(?P<body>.*?)\n## ",
            self.skill,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match, "Concurrency Floor section missing")
        self.section = match.group("body")

    def test_refactor_self_doc_block_present(self) -> None:
        self.assertIn(
            "Refactor (iter4/skill-floor-fill-not-optional)",
            self.section,
            "Refactor self-doc block missing — 2026-05-26 maintainer-directive 落地需保留",
        )

    def test_actionable_marker_bounded_by_exit_zero(self) -> None:
        self.assertRegex(
            self.section,
            r"Actionable marker.*EXIT=0",
            "Concurrency Floor 段必须把 actionable marker 绑到 EXIT=0,防止 in-flight 被误当 actionable",
        )

    def test_in_flight_codex_explicitly_not_actionable(self) -> None:
        self.assertIn("in-flight codex", self.section)
        self.assertIn("不是 actionable marker", self.section)

    def test_default_action_envsubst_audit(self) -> None:
        for required in ("envsubst", "audit-iter-N", "harness background task"):
            with self.subTest(required=required):
                self.assertIn(required, self.section)

    def test_rationalization_wording_blocked(self) -> None:
        # 维护者实证识别的 typical defer wording 必须显式列入禁止集
        for phrase in ("派 audit 重", "target stale", "等 cascade", "和已有工作冲突"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.section, f"防御 wording 缺失: {phrase}")


if __name__ == "__main__":
    unittest.main()
