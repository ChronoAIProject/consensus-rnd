#!/usr/bin/env python3
"""Source-regression guard for project-rules test discipline."""

from __future__ import annotations

import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]
PROJECT_RULES = REPO_ROOT / "CLAUDE.md"
FACT_SOURCE_UNIQUENESS = "事实源" + "唯一性"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class ProjectRulesTestDisciplineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = read(PROJECT_RULES)

    def test_verifiability_clause_is_behavior_first(self) -> None:
        clause = self._bullet("变更必须可验证")

        for required in (
            "行为约束默认由 behavior test 或端到端可观察输入 / 输出 / 副作用验证",
            "source-regression / 段落 lint 仅用于跨 artifact 一致性、授权边界、" + FACT_SOURCE_UNIQUENESS + "、owner-local public / parsed / authorization interface、必备 anchor / path 存在",
            "若断言只因实现重构而必须同步修改且不验证行为或授权边界,应替换为 behavior test 或语义化的跨 artifact 一致性断言",
        ):
            with self.subTest(required=required):
                self.assertIn(required, clause)

        self.assertNotIn("behavior test / source-regression test / 段落 lint", clause)

    def test_skill_change_discipline_limits_source_regression_to_interfaces(self) -> None:
        clause = self._bullet("行为变更必须优先配套")

        for required in (
            "behavior test",
            "端到端可观察输入 / 输出 / 副作用断言",
            "source-regression test",
            "narrow allowlist",
            "授权来源 path",
            FACT_SOURCE_UNIQUENESS,
            "owner-local public / parsed / authorization interface",
            "必备 anchor / path",
        ):
            with self.subTest(required=required):
                self.assertIn(required, clause)

        for forbidden_detail in (
            "私有常量名",
            "私有函数 / 类位置",
            "局部变量",
            "局部控制流",
            "单行源码原文",
        ):
            with self.subTest(forbidden_detail=forbidden_detail):
                self.assertIn(forbidden_detail, clause)
        self.assertIn("禁止精确锁", clause)

    def test_risk_scaled_testing_keeps_source_regression_secondary(self) -> None:
        clause = self._bullet("测试按风险扩展")

        self.assertIn("窄文档 / anchor / 授权边界改动可用 source-regression 覆盖", clause)
        self.assertIn("共享脚本、跨 skill 流程或可观察行为改动必须补 behavior test", clause)
        self.assertIn("source-regression 不得替代行为验证", clause)
        self.assertIn("不得把纯实现细节变成" + "事实源", clause)

    def _bullet(self, title: str) -> str:
        prefixes = (f"- **{title}**", f"- {title}")
        for line in self.rules.splitlines():
            if line.startswith(prefixes):
                return line
        raise AssertionError(f"missing CLAUDE.md bullet: {title}")


if __name__ == "__main__":
    unittest.main()
