#!/usr/bin/env python3
"""Behavior tests for render-time prompt contract inlining."""

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
from codex_refactor_loop.prompt_contracts import (  # noqa: E402
    GITHUB_POST_RULES_CONTRACT_TOKEN,
    PromptContractError,
    REASONING_DISCIPLINE_CONTRACT_TOKEN,
    inline_prompt_contracts,
)

SKILL_ROOT = SCRIPT_DIR.parent
DIRECT_POST_PROMPTS = (
    "reviewer-tests.md",
    "reviewer-architect.md",
    "reviewer-quality.md",
    "review-fix.md",
    "solver-minimal.md",
    "solver-structural.md",
    "solver-delete.md",
    "meta-judge.md",
    "design-issue-reply.md",
    "triage-external-issue.md",
)


class PromptContractInlinerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="prompt-contract-test-"))
        self.skill_root = self.tmp / "skill"
        (self.skill_root / "prompts").mkdir(parents=True)
        (self.skill_root / "prompts" / "_github-post-rules.md").write_text(
            "# GitHub post rules\n\n## Body\n\nFollow `${HOST_WORK_LANGUAGE}`.\n",
            encoding="utf-8",
        )
        (self.skill_root / "prompts" / "_reasoning-discipline.md").write_text(
            "# Reasoning discipline contract\n\nAesthetic/adversarial\n\nASSUMED-UNVERIFIED\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_inlines_fixed_github_post_rules_token(self) -> None:
        rendered = inline_prompt_contracts(
            f"before\n{GITHUB_POST_RULES_CONTRACT_TOKEN}\nafter\n",
            skill_root=self.skill_root,
        )

        self.assertIn("# GitHub post rules", rendered)
        self.assertIn("## Body", rendered)
        self.assertIn("${HOST_WORK_LANGUAGE}", rendered)
        self.assertNotIn(GITHUB_POST_RULES_CONTRACT_TOKEN, rendered)

    def test_unknown_contract_token_fails_closed(self) -> None:
        with self.assertRaisesRegex(PromptContractError, "unknown prompt contract token"):
            inline_prompt_contracts("{{OTHER_CONTRACT}}\n", skill_root=self.skill_root)

    def test_missing_github_post_rules_file_fails_closed(self) -> None:
        (self.skill_root / "prompts" / "_github-post-rules.md").unlink()

        with self.assertRaisesRegex(PromptContractError, "missing GitHub post rules contract"):
            inline_prompt_contracts(GITHUB_POST_RULES_CONTRACT_TOKEN, skill_root=self.skill_root)

    def test_inlines_multiple_fixed_contract_tokens(self) -> None:
        rendered = inline_prompt_contracts(
            f"{GITHUB_POST_RULES_CONTRACT_TOKEN}\n---\n{REASONING_DISCIPLINE_CONTRACT_TOKEN}\n",
            skill_root=self.skill_root,
        )

        self.assertIn("# GitHub post rules", rendered)
        self.assertIn("Aesthetic/adversarial", rendered)
        self.assertIn("ASSUMED-UNVERIFIED", rendered)
        self.assertNotIn(GITHUB_POST_RULES_CONTRACT_TOKEN, rendered)
        self.assertNotIn(REASONING_DISCIPLINE_CONTRACT_TOKEN, rendered)

    def test_missing_reasoning_discipline_file_fails_closed(self) -> None:
        (self.skill_root / "prompts" / "_reasoning-discipline.md").unlink()

        with self.assertRaisesRegex(PromptContractError, "missing reasoning discipline contract"):
            inline_prompt_contracts(REASONING_DISCIPLINE_CONTRACT_TOKEN, skill_root=self.skill_root)

    def test_direct_post_prompts_render_selected_host_work_language(self) -> None:
        ctx = LoopContext.load(repo_root=self.tmp, skill_root=SKILL_ROOT, env={"REPO_ROOT": str(self.tmp)})
        actions = ControllerActions(ctx)

        for language in ("en", "zh"):
            for prompt_name in DIRECT_POST_PROMPTS:
                with self.subTest(language=language, prompt=prompt_name):
                    output = self.tmp / f"{language}-{prompt_name}"
                    actions.render_template(
                        str(SKILL_ROOT / "prompts" / prompt_name),
                        str(output),
                        env={"HOST_WORK_LANGUAGE": language},
                    )

                    rendered = output.read_text(encoding="utf-8")
                    self.assertIn(f"follow `{language}`", rendered)
                    self.assertNotIn("follow ``", rendered)
                    self.assertNotIn("body in ``", rendered)
                    self.assertNotIn("${HOST_WORK_LANGUAGE}", rendered)
                    self.assertNotIn("$HOST_WORK_LANGUAGE", rendered)

                    if prompt_name == "design-issue-reply.md":
                        self.assertIn(f"body in `{language}`", rendered)


if __name__ == "__main__":
    unittest.main()
