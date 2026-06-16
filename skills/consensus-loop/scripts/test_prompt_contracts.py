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

                self.assertIn("zsh-safe exit-code variables", rendered)
                self.assertIn("**do not** use `status`", rendered)
                self.assertIn("post_exit_code=$?", rendered)
                self.assertNotIn("status=$?", rendered)

    def test_reviewer_prompts_share_one_github_visible_evidence_tail_contract(self) -> None:
        shared = (PROMPTS_DIR / "_github-post-rules.md").read_text(encoding="utf-8")
        for needle in (
            "Reviewer GitHub-visible evidence tail",
            "body/evidence",
            "`review_round: <round>`",
            "`head_sha: <sha>`",
            "`REVIEW_DONE:<PR>:<role>:<verdict>`",
            "final standalone AI sentinel",
            "No marker or body may appear after the sentinel",
        ):
            with self.subTest(shared_needles=needle):
                self.assertIn(needle, shared)

        for name in ("reviewer-architect.md", "reviewer-tests.md", "reviewer-quality.md"):
            body = (PROMPTS_DIR / name).read_text(encoding="utf-8")
            rendered = inline_prompt_contracts(body, skill_root=SKILL_ROOT)
            role = name.removeprefix("reviewer-").removesuffix(".md")
            with self.subTest(prompt=name):
                self.assertIn("Reviewer GitHub-visible evidence tail", rendered)
                self.assertIn(f"`REVIEW_DONE:${{PR_NUMBER}}:{role}:<verdict>`", body)
                self.assertNotIn("Your GitHub PR comment must include `review_round", body)

    def test_prompts_do_not_keep_hidden_legacy_contract_anchor_blocks(self) -> None:
        for path in PROMPTS_DIR.glob("*.md"):
            body = path.read_text(encoding="utf-8")
            with self.subTest(prompt=path.name):
                self.assertNotIn("Legacy contract anchors for source-regression compatibility", body)
                self.assertNotIn("legacy-section-headings", body)

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
            "Worker output, validator success, `.refactor-loop/host.env`, the prompt body, or the first `consensus:decompose` are not apply authorization sources",
            'controller_action="apply_issue_decomposition_plan"',
            "plan-level judge artifact structure fields plus validated plan digest/proof plus #191 owner plus live parent open/tracking plus sentinel idempotency",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, implement)

    def test_decomposition_tracking_grammar_is_reserved_to_helper(self) -> None:
        shared = (PROMPTS_DIR / "_github-post-rules.md").read_text(encoding="utf-8")
        self.assertIn("Decomposition tracking grammar is helper-owned", shared)
        self.assertIn("must not write `<!-- crnd:issue-decomposition-tracking -->`", shared)
        self.assertIn("`IssueDecompositionChild fingerprint:`", shared)

        for name in ("solver-minimal.md", "solver-structural.md", "solver-delete.md", "meta-judge.md"):
            body = (PROMPTS_DIR / name).read_text(encoding="utf-8")
            with self.subTest(prompt=name):
                self.assertIn("must not emit parent tracking blocks or child fingerprint lines", body)
                self.assertNotIn("<!-- crnd:issue-decomposition-tracking -->", body)

    def test_prompts_use_scope_authorization_not_blanket_work_type_rejection(self) -> None:
        forbidden_patterns = (
            r"No new " + r"features",
            r"不新增" + r"功能",
            r"product-feature" + r"-request",
            r"runtime-bug" + r"-report",
        )
        prompt_expectations = {
            "SKILL.md": (
                "No unauthorized scope expansion",
                "source issue, consensus artifact, and `scope_paths`",
                "Issue-authorized feature, bug, doc, refactor, and governance work may proceed",
            ),
            "prompts/audit.md": (
                "No authority expansion",
                "current audit/issue-authorized violation or work-unit scope",
            ),
            "prompts/implement.md": (
                "Do not expand authority",
                "current work unit authorized by the source issue, consensus artifact, and `scope_paths`",
                "feature, bug, doc, refactor, and governance work",
            ),
            "prompts/triage-external-issue.md": (
                "bounded `scope_paths`, desired end state / invariant, and verification hints",
                "The category itself is not a reject reason",
                "no-bounded-work-unit",
            ),
            "prompts/reviewer-architect.md": (
                "source issue, consensus artifact, PR diff intent, and declared `scope_paths`",
                "issue-authorized feature or bug diff is not drift by itself",
                "outside the authorized work-unit boundary",
            ),
            "prompts/reviewer-quality.md": (
                "source issue, consensus artifact, PR diff intent, and declared `scope_paths`",
                "issue-authorized feature or bug work is allowed inside that boundary",
                "unauthorized scope expansion into unrelated cleanup",
            ),
            "prompts/review-fix.md": (
                "source issue, consensus artifact, PR diff intent, and `scope_paths`",
                "outside the authorized work-unit boundary",
                "delete this authorized capability entirely",
            ),
        }

        for name, required_needles in prompt_expectations.items():
            path = SKILL_ROOT / name if name == "SKILL.md" else SKILL_ROOT / name
            body = path.read_text(encoding="utf-8")

            for needle in required_needles:
                with self.subTest(prompt=name, needle=needle):
                    self.assertIn(needle, body)
            for pattern in forbidden_patterns:
                with self.subTest(prompt=name, pattern=pattern):
                    self.assertIsNone(re.search(pattern, body))


def github_post_section(body: str) -> str:
    match = re.search(r"(?ms)^## GitHub post.*?(?=^## |\Z)", body)
    return match.group(0) if match else ""


if __name__ == "__main__":
    unittest.main()
