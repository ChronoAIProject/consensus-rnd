#!/usr/bin/env python3
"""Repository public documentation boundary tests."""

from __future__ import annotations

import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__)
REPO_ROOT = SCRIPT_PATH.parents[3]
README = REPO_ROOT / "README.md"
README_ZH = REPO_ROOT / "README.zh-CN.md"
CLAUDE = REPO_ROOT / "CLAUDE.md"
AGENTS = REPO_ROOT / "AGENTS.md"
SKILL = REPO_ROOT / "skills" / "codex-refactor-loop" / "SKILL.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def heading_order(markdown: str) -> list[str]:
    return [line.strip() for line in markdown.splitlines() if line.startswith("## ")]


class RepositoryPublicDocsTests(unittest.TestCase):
    def test_readme_pair_exists_and_cross_links(self) -> None:
        self.assertTrue(README.exists())
        self.assertTrue(README_ZH.exists())
        self.assertIn("[README.zh-CN.md](./README.zh-CN.md)", read(README))
        self.assertIn("[README.md](./README.md)", read(README_ZH))
        self.assertIn("English canonical public identity document", read(README))
        self.assertIn("中文 companion public identity document", read(README_ZH))

    def test_language_boundary_visible(self) -> None:
        skill = read(SKILL)
        for needle in (
            "README.md` is English canonical",
            "README.zh-CN.md` is the 中文 companion",
            "GitHub issue/PR/commit/design artifacts remain 中文 by default",
            "Public identity README pair",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, skill)

    def test_root_markdown_closure_and_agents_symlink(self) -> None:
        allowed = {
            "AGENTS.md",
            "CLAUDE.md",
            "GEMINI.md",
            "README.md",
            "README.zh-CN.md",
        }
        if (REPO_ROOT / "CHANGELOG.md").exists():
            allowed.add("CHANGELOG.md")
        actual = {path.name for path in REPO_ROOT.glob("*.md")}
        self.assertEqual(actual, allowed)
        self.assertTrue(AGENTS.is_symlink())
        self.assertEqual(AGENTS.readlink(), Path("CLAUDE.md"))
        self.assertIn("README.zh-CN.md", read(CLAUDE))

    def test_public_identity_carveout_bounded_in_claude_and_skill(self) -> None:
        claude = read(CLAUDE)
        skill = read(SKILL)
        for text in (claude, skill):
            with self.subTest(document=("CLAUDE.md" if text == claude else "SKILL.md")):
                self.assertIn("README pair", text)
                self.assertIn("only English-canonical public-doc carve-out", text)
                self.assertIn("GitHub issue/PR/commit/design artifact", text)
        self.assertNotIn("INSTALL.md", read(README))
        self.assertEqual(1, read(README).count("SKILL.md#downstream-install-walkthrough"))

    def test_readme_pair_introduces_consensus_engine_and_skills(self) -> None:
        readme = read(README)
        readme_zh = read(README_ZH)
        for needle in (
            "cross-platform Agent Skills publication repository",
            "product identity is a **consensus engine**",
            "biased independent solvers",
            "meta-judge converges",
            "implementation runs",
            "independent reviewers",
            "`codex-refactor-loop`",
            "`sshx`",
            "Claude Code, Codex, Cursor, and Gemini",
            "host-provided `host.env` facts",
        ):
            with self.subTest(readme_needle=needle):
                self.assertIn(needle, readme)
        for needle in (
            "跨平台 Agent Skills 发布仓库",
            "产品身份是**共识引擎**",
            "偏置独立的多角度 solver",
            "meta-judge 收敛",
            "随后实现",
            "多 reviewer",
            "`codex-refactor-loop`",
            "`sshx`",
            "Claude Code / Codex / Cursor / Gemini",
            "host 通过 `host.env` 注入",
        ):
            with self.subTest(readme_zh_needle=needle):
                self.assertIn(needle, readme_zh)

    def test_readme_pair_has_prominent_risk_warning(self) -> None:
        readme = read(README)
        readme_zh = read(README_ZH)
        self.assertIn("## Risks", readme)
        self.assertIn("## ⚠️ 风险提示", readme_zh)
        for needle in (
            "Autonomous writes",
            "without per-action human confirmation",
            "API and compute cost",
            "six GitHub-polling daemons",
            "Automatic releases",
            "RELEASE_AUTO_ENABLE=true",
            "Bad published tags are abandoned",
            "Host boundary",
            "active-controller lease",
            "Experimental scope",
            "opt in",
        ):
            with self.subTest(readme_risk=needle):
                self.assertIn(needle, readme)
        for needle in (
            "自治写操作",
            "没有逐动作人工确认",
            "API/算力成本",
            "6 个 daemon 轮询 GitHub",
            "自动发版",
            "RELEASE_AUTO_ENABLE=true",
            "坏版即弃",
            "host 边界",
            "active-controller lease",
            "适用范围",
            "opt in",
        ):
            with self.subTest(readme_zh_risk=needle):
                self.assertIn(needle, readme_zh)

    def test_readme_links_downstream_walkthrough_once(self) -> None:
        readme = read(README)
        self.assertEqual(1, readme.count("./skills/codex-refactor-loop/SKILL.md#downstream-install-walkthrough"))
        self.assertEqual(0, read(README_ZH).count("SKILL.md#downstream-install-walkthrough"))
        self.assertIn("Downstream Host Setup", readme)
        self.assertEqual(
            heading_order(readme),
            [
                "## What It Provides",
                "## Core",
                "## Risks",
                "## Quick Start",
                "## Architecture",
                "## Roadmap",
                "## License",
            ],
        )
        self.assertEqual(
            heading_order(read(README_ZH)),
            [
                "## 提供什么",
                "## 核心",
                "## ⚠️ 风险提示",
                "## 快速开始",
                "## 架构",
                "## 路线",
                "## License",
            ],
        )


if __name__ == "__main__":
    unittest.main()
