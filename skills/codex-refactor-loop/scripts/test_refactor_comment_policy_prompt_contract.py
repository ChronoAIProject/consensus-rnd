#!/usr/bin/env python3
"""Source-regression tests for HOST_REFACTOR_COMMENT_POLICY prompt behavior."""

from __future__ import annotations

import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
SKILL_ROOT = SCRIPT_PATH.parents[1]
PROMPTS_DIR = SKILL_ROOT / "prompts"


def read_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


class RefactorCommentPolicyPromptContractTests(unittest.TestCase):
    def test_default_policy_preserves_old_new_requirement(self) -> None:
        implement = read_prompt("implement.md")
        verify = read_prompt("verify.md")
        architect = read_prompt("reviewer-architect.md")
        quality = read_prompt("reviewer-quality.md")
        review_fix = read_prompt("review-fix.md")

        for name, text in {
            "implement.md": implement,
            "verify.md": verify,
            "reviewer-architect.md": architect,
            "reviewer-quality.md": quality,
            "review-fix.md": review_fix,
        }.items():
            with self.subTest(prompt=name):
                self.assertIn("${HOST_REFACTOR_COMMENT_POLICY}", text)
                self.assertIn("empty/`self-doc-comment`", text)
                self.assertIn("self-doc-comment", text)

        self.assertIn("Refactor (iter${ITERATION}/${CLUSTER_ID}):", implement)
        self.assertIn("Old pattern: ${OLD_PATTERN}", implement)
        self.assertIn("New principle: ${NEW_PRINCIPLE}", implement)
        self.assertIn("缺失任何一处且无合理 not-applicable 说明 → 标记缺陷", verify)
        self.assertIn("must be present and clear", quality)
        self.assertIn("Preserve/add refactor self-doc comments only when", review_fix)

    def test_none_policy_forbids_refactor_history_source_comments(self) -> None:
        expected = (
            "`none`",
            "MUST NOT add `Refactor (...)`, `Old pattern`, `New principle`, or `iterN/cluster`",
            "refactor self-doc: not applicable (HOST_REFACTOR_COMMENT_POLICY=none)",
        )
        implement = read_prompt("implement.md")
        for token in expected:
            with self.subTest(token=token):
                self.assertIn(token, implement)

        verify = read_prompt("verify.md")
        self.assertIn("新增 `Refactor (...)`, `Old pattern`, `New principle`, or `iterN/cluster` refactor-history source comments → 标记缺陷", verify)
        self.assertIn("refactor self-doc: not applicable (HOST_REFACTOR_COMMENT_POLICY=none)", verify)

        architect = read_prompt("reviewer-architect.md")
        self.assertIn("`none`: absence is compliant", architect)
        self.assertIn("new Old/New/iteration refactor-history source comments must be rejected", architect)

        review_fix = read_prompt("review-fix.md")
        self.assertIn("When `${HOST_REFACTOR_COMMENT_POLICY}=none`", review_fix)
        self.assertIn("classify it as a host-policy conflict/false-positive", review_fix)

    def test_none_policy_disables_missing_self_doc_rejects(self) -> None:
        verify = read_prompt("verify.md")
        quality = read_prompt("reviewer-quality.md")

        self.assertIn("missing Refactor self-documentation is not a defect and must not trigger rework", verify)
        self.assertIn("missing/illegible self-doc must not be a reject reason", quality)
        self.assertIn("Under `HOST_REFACTOR_COMMENT_POLICY=none`, missing/illegible self-doc alone is not a reject reason", quality)
        self.assertIn("still comment/reject for naming, dead code, complexity, scope creep", quality)

        forbidden_unconditional = (
            "the cluster mandates `// Refactor (iterN/cluster-XXX):` Old/New blocks",
            "missing/illegible self-doc on a major refactor, or scope creep",
            "缺失任何一处且无合理 not-applicable 说明 → 标记缺陷。\n- 检查改动是否真正消除了",
        )
        combined = "\n".join(
            read_prompt(name)
            for name in (
                "implement.md",
                "verify.md",
                "reviewer-architect.md",
                "reviewer-quality.md",
                "review-fix.md",
            )
        )
        for token in forbidden_unconditional:
            with self.subTest(token=token):
                self.assertNotIn(token, combined)

    def test_invalid_refactor_comment_policy_fails_closed(self) -> None:
        for name in (
            "implement.md",
            "verify.md",
            "reviewer-architect.md",
            "reviewer-quality.md",
            "review-fix.md",
        ):
            text = read_prompt(name)
            with self.subTest(prompt=name):
                self.assertIn("invalid", text)
                self.assertIn("fail-closed", text)
                self.assertIn("do not guess", text.lower())


if __name__ == "__main__":
    unittest.main()
