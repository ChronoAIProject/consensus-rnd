#!/usr/bin/env python3
"""Source-regression tests for checked-in prompt contract tokens."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
PROMPTS_DIR = SKILL_ROOT / "prompts"
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.prompt_contracts import GITHUB_POST_RULES_CONTRACT_TOKEN  # noqa: E402


DIRECT_POST_PROMPTS = (
    "solver-minimal.md",
    "solver-structural.md",
    "solver-delete.md",
    "meta-judge.md",
    "reviewer-architect.md",
    "reviewer-tests.md",
    "reviewer-quality.md",
    "review-fix.md",
    "design-issue-reply.md",
    "triage-external-issue.md",
)


class PromptContractsTests(unittest.TestCase):
    def test_direct_post_prompts_declare_one_fixed_contract_token(self) -> None:
        for name in DIRECT_POST_PROMPTS:
            with self.subTest(prompt=name):
                body = (PROMPTS_DIR / name).read_text(encoding="utf-8")
                section = github_post_section(body)
                self.assertIn("## GitHub post", section)
                self.assertEqual(section.count(GITHUB_POST_RULES_CONTRACT_TOKEN), 1)
                self.assertNotIn("prompts/_github-post-rules.md", body)

    def test_only_known_contract_token_is_checked_in(self) -> None:
        tokens: set[str] = set()
        for path in PROMPTS_DIR.glob("*.md"):
            tokens.update(re.findall(r"{{[A-Z0-9_]+_CONTRACT}}", path.read_text(encoding="utf-8")))

        self.assertEqual(tokens, {GITHUB_POST_RULES_CONTRACT_TOKEN})


def github_post_section(body: str) -> str:
    match = re.search(r"(?ms)^## GitHub post.*?(?=^## |\Z)", body)
    return match.group(0) if match else ""


if __name__ == "__main__":
    unittest.main()
