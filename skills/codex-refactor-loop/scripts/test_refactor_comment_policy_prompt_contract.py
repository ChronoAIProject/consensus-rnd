#!/usr/bin/env python3
"""Source-regression tests for HOST_REFACTOR_COMMENT_POLICY prompt behavior."""

from __future__ import annotations

import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
SKILL_ROOT = SCRIPT_PATH.parents[1]
PROMPTS_DIR = SKILL_ROOT / "prompts"
SKILL_MD = SKILL_ROOT / "SKILL.md"


def read_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def read_skill() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


class RefactorCommentPolicyPromptContractTests(unittest.TestCase):
    def test_github_state_contract_requires_pr_review_thread_closure(self) -> None:
        skill = read_skill()

        self.assertIn("## GitHub State Contract", skill)
        self.assertIn("| PR review comment fix |", skill)
        self.assertIn("Completion includes review-thread closure", skill)
        self.assertIn("fixes driven by PR review comments are incomplete", skill)
        self.assertIn("original thread is replied to and resolved", skill)
        self.assertIn("or explicitly escalated", skill)

    def test_review_fix_prompt_requires_seeded_review_thread_completion(self) -> None:
        review_fix = read_prompt("review-fix.md")

        self.assertIn("Close review-thread completion evidence when seeded", review_fix)
        self.assertIn(".refactor-loop/state/review-thread-completion/pr${PR_NUMBER}.json", review_fix)
        self.assertIn("reply to that original PR review thread", review_fix)
        self.assertIn("resolve the thread", review_fix)
        self.assertIn("set `replied=true` and `resolved=true`", review_fix)
        self.assertIn("FIX_BLOCKED:${PR_NUMBER}:round-${FIX_ROUND}:other:review-thread-completion", review_fix)
        self.assertIn("separate clean-exit `.refactor-loop/logs/*.log` meta-layer artifact", review_fix)
        self.assertIn("META_RESOLVED:escalate-human:<short>", review_fix)

    def test_default_policy_is_none_and_external_rationale(self) -> None:
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
                self.assertIn("missing/empty/default", text)
                self.assertIn("`none`", text)
                self.assertIn("self-doc-comment", text)

        self.assertIn("missing/empty/default/`none` 归一化为 `none`", implement)
        self.assertIn("MUST NOT add `Refactor (...)`, `Old pattern`, `New principle`, or `iterN/cluster`", implement)
        self.assertIn("refactor self-doc: not applicable (HOST_REFACTOR_COMMENT_POLICY=none)", implement)
        self.assertIn("新增极小辅助类型的注释也必须遵守 `${HOST_REFACTOR_COMMENT_POLICY}`", implement)
        self.assertIn("不得写 `refactor helper`, `no behavior change`, `Old`, `New`, 或 `iterN`", implement)
        self.assertIn("只写面向业务行为的准确英文说明", implement)
        self.assertIn("仅 explicit `self-doc-comment` 时才按第 2 条既有 Refactor self-documentation 格式写注释", implement)
        self.assertNotIn('新增极小辅助类型须注释 "refactor helper, no behavior change"', implement)
        self.assertIn("missing Refactor self-documentation is not a defect and must not trigger rework", verify)
        self.assertIn("absence is compliant, rationale belongs in external artifacts", architect)
        self.assertIn("missing/illegible self-doc must not be a reject reason", quality)
        self.assertIn("keep rationale in the fix report/external artifact", review_fix)

    def test_explicit_self_doc_policy_preserves_old_new_requirement(self) -> None:
        implement = read_prompt("implement.md")
        verify = read_prompt("verify.md")
        quality = read_prompt("reviewer-quality.md")
        review_fix = read_prompt("review-fix.md")

        self.assertIn("`self-doc-comment`：被重构的每个类/关键方法必须", implement)
        self.assertIn("Refactor (iter${ITERATION}/${CLUSTER_ID}):", implement)
        self.assertIn("Old pattern: ${OLD_PATTERN}", implement)
        self.assertIn("New principle: ${NEW_PRINCIPLE}", implement)
        self.assertIn("源码注释必须 English-only", implement)
        self.assertIn("do not replace it with issue-only identities", implement)
        self.assertIn("缺失任何一处且无合理 not-applicable 说明 → 标记缺陷", verify)
        self.assertIn("HOST_REFACTOR_COMMENT_POLICY=self-doc-comment", quality)
        self.assertIn("must be present and clear", quality)
        self.assertIn("Non-canonical marker identity is a fixable process defect", quality)
        self.assertIn("Preserve/add refactor self-doc comments only when `${HOST_REFACTOR_COMMENT_POLICY}=self-doc-comment`", review_fix)
        self.assertIn("non-canonical marker identity is (A) fixable in-scope", review_fix)
        self.assertIn("Do not emit `FIX_BLOCKED:human-decision` for deterministic marker normalization", review_fix)

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
        self.assertIn("missing/empty/default/`none` normalizes to `none`: absence is compliant", architect)
        self.assertIn("new Old/New/iteration refactor-history source comments must be rejected", architect)

        review_fix = read_prompt("review-fix.md")
        self.assertIn("When `${HOST_REFACTOR_COMMENT_POLICY}` is missing/empty/default/`none`", review_fix)
        self.assertIn("classify it as a host-policy conflict/false-positive", review_fix)

    def test_none_policy_disables_missing_self_doc_rejects(self) -> None:
        verify = read_prompt("verify.md")
        quality = read_prompt("reviewer-quality.md")

        self.assertIn("missing Refactor self-documentation is not a defect and must not trigger rework", verify)
        self.assertIn("missing/illegible self-doc must not be a reject reason", quality)
        self.assertIn("Under missing/empty/default/`HOST_REFACTOR_COMMENT_POLICY=none`, missing/illegible self-doc alone is not a reject reason", quality)
        self.assertIn("Still comment/reject for naming, dead code, complexity, scope creep", quality)

        forbidden_unconditional = (
            "the cluster mandates `// Refactor (iterN/cluster-XXX):` Old/New blocks",
            "missing/illegible self-doc on a major refactor, or scope creep",
            "empty/`self-doc-comment`",
            "empty/`self-doc-comment` normalizes to `self-doc-comment`",
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
                "meta-reflector-stalled.md",
            )
        )
        for token in forbidden_unconditional:
            with self.subTest(token=token):
                self.assertNotIn(token, combined)

    def test_deterministic_marker_normalization_routes_to_fix(self) -> None:
        reflector = read_prompt("meta-reflector-stalled.md")

        self.assertIn("deterministic in-scope text normalization", reflector)
        self.assertIn("non-canonical refactor marker identity", reflector)
        self.assertIn("META_RESOLVED:retry-fix:<exact normalization instruction>", reflector)
        self.assertIn("not human escalation", reflector)

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
