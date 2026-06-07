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

from codex_refactor_loop.prompt_contracts import (  # noqa: E402
    GITHUB_POST_RULES_CONTRACT_TOKEN,
    inline_prompt_contracts,
)


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

    def test_rendered_direct_post_prompts_ban_zsh_readonly_status_exit_variable(self) -> None:
        for name in DIRECT_POST_PROMPTS:
            with self.subTest(prompt=name):
                body = (PROMPTS_DIR / name).read_text(encoding="utf-8")
                rendered = inline_prompt_contracts(body, skill_root=SKILL_ROOT)

                self.assertIn("zsh-safe 退出码变量", rendered)
                self.assertIn("禁止**用 `status`", rendered)
                self.assertIn("post_exit_code=$?", rendered)
                self.assertNotIn("status=$?", rendered)

    def test_decomposition_apply_prompt_contract_requires_plan_level_judge_fields(self) -> None:
        meta_judge = (PROMPTS_DIR / "meta-judge.md").read_text(encoding="utf-8")
        implement = (PROMPTS_DIR / "implement.md").read_text(encoding="utf-8")

        for needle in (
            'controller_action="apply_issue_decomposition_plan"',
            "plan_level_design_consensus_judge_artifact",
            "issue_decomposition_plan_path",
            "issue_decomposition_plan_digest",
            "issue_decomposition_proof",
            "first `META_JUDGE_DONE:consensus:decompose`",
            "solver artifacts",
            "prompt body",
            "validator output",
            "worker output",
            "`.refactor-loop/host.env`",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, meta_judge)
        for needle in (
            "worker 输出、validator 通过、`.refactor-loop/host.env`、prompt body 或第一次 `consensus:decompose` 均不是 apply 授权来源",
            'controller_action="apply_issue_decomposition_plan"',
            "plan-level judge artifact 结构字段 + validated plan digest/proof + #191 owner + live parent open/tracking + sentinel idempotency",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, implement)

    def test_prompts_use_scope_authorization_not_blanket_work_type_rejection(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        implement = (PROMPTS_DIR / "implement.md").read_text(encoding="utf-8")
        triage = (PROMPTS_DIR / "triage-external-issue.md").read_text(encoding="utf-8")
        architect = (PROMPTS_DIR / "reviewer-architect.md").read_text(encoding="utf-8")
        quality = (PROMPTS_DIR / "reviewer-quality.md").read_text(encoding="utf-8")
        fix = (PROMPTS_DIR / "review-fix.md").read_text(encoding="utf-8")
        combined = "\n".join((skill, implement, triage, architect, quality, fix))

        for needle in (
            "No unauthorized scope expansion",
            "source issue, consensus artifact, and `scope_paths`",
            "feature、bug、doc、refactor 或 governance 工作",
            "类别本身不是 reject 理由",
            "issue-authorized feature or bug diff is not drift by itself",
            "issue-authorized feature or bug work is allowed inside that boundary",
            "outside the authorized work-unit boundary",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, combined)

        forbidden_patterns = (
            r"No new " + r"features",
            r"不新增" + r"功能",
            r"product-feature" + r"-request",
            r"runtime-bug" + r"-report",
        )
        for pattern in forbidden_patterns:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, combined))


def github_post_section(body: str) -> str:
    match = re.search(r"(?ms)^## GitHub post.*?(?=^## |\Z)", body)
    return match.group(0) if match else ""


if __name__ == "__main__":
    unittest.main()
