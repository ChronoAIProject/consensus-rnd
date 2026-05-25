#!/usr/bin/env python3
"""Source contract tests for the codex-refactor-loop entrypoint split."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__)
SKILL_ROOT = SCRIPT_PATH.parents[1]
SKILL_MD = SKILL_ROOT / "SKILL.md"
REFERENCE_MD = SKILL_ROOT / "REFERENCE.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class SkillEntrypointContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill = read(SKILL_MD)
        self.reference = read(REFERENCE_MD)

    def test_frontmatter_contract_is_minimal_and_trigger_only(self) -> None:
        match = re.match(r"\A---\n(?P<body>.*?)\n---\n", self.skill, flags=re.DOTALL)
        self.assertIsNotNone(match)
        body = match.group("body")
        lines = body.splitlines()

        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "name: codex-refactor-loop")
        self.assertTrue(lines[1].startswith("description: Use when "))
        self.assertLessEqual(len(body), 1024)

    def test_entrypoint_line_budget_and_controller_contract_headings(self) -> None:
        line_count = len(self.skill.splitlines())

        self.assertGreaterEqual(line_count, 600)
        self.assertLessEqual(line_count, 850)
        for heading in (
            "## Controller Contract Index",
            "## Host 配置(通用化注入点)",
            "## Phase Index",
            "## Phase 0 — Bootstrap (first wakeup only)",
            "## Loop control",
            "## Label 系统 — 强制",
            "## Hard rules (controller-level, propagated into every codex prompt)",
            "## 工作语言规则(源码内英文,源码外中文)",
            "## Files",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, self.skill)

    def test_mandatory_local_invariants_remain_in_entrypoint(self) -> None:
        required = (
            "⟦AI:AUTO-LOOP⟧",
            "GitHub 是系统状态唯一显示面",
            "Controller = pure orchestration",
            "first wakeup",
            "Phase 0",
            "phase routing",
            "3/3",
            "CODEX_FLOOR",
            "floor",
            "label",
            "spawn",
            "Hard rules",
            "Source files are English-only; external user-facing artifacts are 中文 by default",
            "No mandatory parallel English section",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, self.skill)

    def test_first_wakeup_bootstrap_obligations_are_ordered_in_skill_alone(self) -> None:
        phase0 = self.skill.split("## Phase 0 — Bootstrap (first wakeup only)", 1)[1]
        phase0 = phase0.split("## Phase Routing", 1)[0]
        obligations = (
            "source .refactor-loop/host.env",
            "fail closed",
            "ProjectRulesFixedPointEnsurer",
            "initialize state",
            "integration branch",
            "ensure labels",
            "ensure all 5 daemons",
            "dispatch producer",
            "confirm a wake source",
        )
        cursor = -1
        for obligation in obligations:
            index = phase0.find(obligation)
            with self.subTest(obligation=obligation):
                self.assertNotEqual(index, -1)
                self.assertGreater(index, cursor)
            cursor = index

    def test_heavy_reference_material_is_not_in_entrypoint(self) -> None:
        heavy_markers = (
            "## 📊 当前状态 — <phase>",
            "## 🆘 状态卡片 — 共识机制无法继续收敛",
            '"schema_version": 1',
            "## WorkUnitV1 contract",
            "## Batching heuristics",
            "## Recovery playbook",
            "gh label create",
            "Poll every 60s; emit one event per failed check",
            "历史 bilingual 规则的位置",
        )
        for marker in heavy_markers:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, self.skill)
                self.assertIn(marker, self.reference)

    def test_entrypoint_uses_lazy_reference_links_only(self) -> None:
        self.assertNotIn("@REFERENCE.md", self.skill)
        self.assertNotRegex(self.skill, r"\]\(/Users/[^)]+REFERENCE\.md")
        self.assertRegex(self.skill, r"\(REFERENCE\.md#[^)]+\)")


if __name__ == "__main__":
    unittest.main()
