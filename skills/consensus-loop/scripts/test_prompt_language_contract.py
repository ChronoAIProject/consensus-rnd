#!/usr/bin/env python3
"""Behavior tests for prompt source language and rendered work language."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
PROMPTS_DIR = SKILL_ROOT / "prompts"
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext  # noqa: E402
from codex_refactor_loop.controller_actions import ControllerActions  # noqa: E402
from codex_refactor_loop.prompt_contracts import inline_prompt_contracts  # noqa: E402

HAN_START = "\u4e00"
HAN_END = "\u9fff"
SENTINEL = "⟦AI:AUTO-LOOP⟧"

TOUCHED_PROMPTS_WITH_ENGLISH_SOURCE_INSTRUCTIONS = (
    "_github-post-rules.md",
    "_reasoning-discipline.md",
    "audit.md",
    "design-issue-body.md",
    "design-issue-reply.md",
    "implement.md",
    "implementation-pr-artifact-repair.md",
    "meta-judge.md",
    "meta-reflector-repository-stalled.md",
    "remote-ci-fix.md",
    "review-fix.md",
    "reviewer-architect.md",
    "reviewer-quality.md",
    "reviewer-tests.md",
    "solver-delete.md",
    "solver-minimal.md",
    "solver-structural.md",
    "test-add.md",
    "triage-external-issue.md",
    "verify.md",
)

PROMPTS_EXPECTED_TO_REFERENCE_WORK_LANGUAGE = (
    "_github-post-rules.md",
    "design-issue-body.md",
    "design-issue-reply.md",
    "implementation-pr-artifact-repair.md",
    "meta-judge.md",
    "meta-reflector-repository-stalled.md",
    "review-fix.md",
    "reviewer-architect.md",
    "reviewer-quality.md",
    "reviewer-tests.md",
    "solver-delete.md",
    "solver-minimal.md",
    "solver-structural.md",
    "triage-external-issue.md",
)

MARKER_ONLY_PROMPTS_RENDERED_UNDER_DEFAULT_EN = (
    "audit.md",
    "implement.md",
    "remote-ci-fix.md",
    "test-add.md",
    "verify.md",
)

LANGUAGE_AGNOSTIC_CONTRACT_PROMPTS = (
    "implement.md",
    "remote-ci-fix.md",
    "review-fix.md",
    "reviewer-architect.md",
    "reviewer-tests.md",
    "solver-delete.md",
    "solver-structural.md",
)

FORBIDDEN_HOST_FRAMEWORK_PROMPT_LITERALS = (
    "--type cs",
    "[Skip]",
    'Trait("Category","Manual")',
    "Assert.True",
    ".Should().Be",
    ".Should().NotBeNull",
    "source.Should().NotContain",
    "AddX_WhenY_ShouldZ",
    "test_polling_allowlist",
    "cluster-016",
    "cluster-018",
    "IAsyncEnumerable",
    "Channel",
    "actor inbox",
    "*WriteActor",
    "*ReadActor",
    "*Store",
)


def has_han(text: str) -> bool:
    return any(HAN_START <= char <= HAN_END for char in text)


def active_prompt_findings(text: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    in_html_comment = False
    in_fenced_block = False
    in_protocol_section = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("<!--"):
            in_html_comment = True
        if in_html_comment:
            if "-->" in stripped:
                in_html_comment = False
            continue
        if stripped.startswith("```"):
            in_fenced_block = not in_fenced_block
            continue
        if in_fenced_block:
            continue
        if stripped.startswith("## "):
            in_protocol_section = stripped.startswith(
                (
                    "## Marker emission allowlist",
                    "## AI content identifier",
                    "## Marker contract",
                )
            )
        if in_protocol_section:
            continue
        if not has_han(line):
            continue
        if SENTINEL in line:
            continue
        findings.append((line_number, line))
    return findings


class PromptLanguageContractTests(unittest.TestCase):
    def test_touched_prompt_active_instruction_prose_is_english_source(self) -> None:
        for name in TOUCHED_PROMPTS_WITH_ENGLISH_SOURCE_INSTRUCTIONS:
            with self.subTest(prompt=name):
                body = (PROMPTS_DIR / name).read_text(encoding="utf-8")

                self.assertEqual([], active_prompt_findings(body))

    def test_work_language_is_the_only_github_facing_prose_driver(self) -> None:
        for name in PROMPTS_EXPECTED_TO_REFERENCE_WORK_LANGUAGE:
            with self.subTest(prompt=name):
                body = (PROMPTS_DIR / name).read_text(encoding="utf-8")
                rendered_contracts = inline_prompt_contracts(body, skill_root=SKILL_ROOT)
                self.assertIn("${HOST_WORK_LANGUAGE}", rendered_contracts)
                self.assertTrue(
                    "do not add a mandatory parallel English section" in rendered_contracts
                    or "do not add a parallel English or Chinese section" in rendered_contracts
                )
                self.assertNotIn("_en", body)
                self.assertNotIn("_zh", body)

    def test_default_rendered_marker_only_prompts_have_no_chinese_instruction_bias(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="prompt-language-contract-"))
        try:
            with mock.patch.dict("os.environ", {}, clear=True):
                ctx = LoopContext.load(repo_root=tmp, skill_root=SKILL_ROOT, env={"REPO_ROOT": str(tmp)})
                for name in MARKER_ONLY_PROMPTS_RENDERED_UNDER_DEFAULT_EN:
                    with self.subTest(prompt=name):
                        output = tmp / name
                        ControllerActions(ctx).render_template(str(PROMPTS_DIR / name), str(output), env={})
                        rendered = output.read_text(encoding="utf-8")
                        self.assertEqual([], active_prompt_findings(rendered))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_language_agnostic_prompts_do_not_reintroduce_host_framework_literals(self) -> None:
        for name in LANGUAGE_AGNOSTIC_CONTRACT_PROMPTS:
            body = (PROMPTS_DIR / name).read_text(encoding="utf-8")
            findings = [
                literal
                for literal in FORBIDDEN_HOST_FRAMEWORK_PROMPT_LITERALS
                if literal in body
            ]

            with self.subTest(prompt=name):
                self.assertEqual([], findings)


if __name__ == "__main__":
    unittest.main()
