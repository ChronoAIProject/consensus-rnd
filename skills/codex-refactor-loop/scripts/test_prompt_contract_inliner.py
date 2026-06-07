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
