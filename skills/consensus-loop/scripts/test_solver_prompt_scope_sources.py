#!/usr/bin/env python3
"""Source-regression tests for Consensus-rnd Phase design-consensus solver source contracts."""

from __future__ import annotations

import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__)
SKILL_ROOT = SCRIPT_PATH.parents[1]
PROMPTS_DIR = SKILL_ROOT / "prompts"
SOLVER_PROMPTS = (
    "solver-minimal.md",
    "solver-structural.md",
    "solver-delete.md",
)


class SolverPromptScopeSourceTests(unittest.TestCase):
    def test_solver_prompts_accept_audit_or_issue_sources(self) -> None:
        for prompt_name in SOLVER_PROMPTS:
            prompt = (PROMPTS_DIR / prompt_name).read_text(encoding="utf-8")
            with self.subTest(prompt=prompt_name):
                required = (
                    "WORK_UNIT_SOURCE_REF",
                    "source_ref",
                    "gh issue view ${ISSUE_NUMBER}",
                    "GitHub issue body/comments",
                    "gh-issue-<N>",
                    "audit-iter-${ITERATION}.md if present",
                    "do not fabricate audit artifacts",
                )
                for needle in required:
                    self.assertIn(needle, prompt)
                self.assertNotIn(
                    "`$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}.md` — cluster spec",
                    prompt,
                )

    def test_solver_prompts_do_not_require_audit_evidence_for_issue_driven_work(self) -> None:
        prompt_expectations = {
            "solver-minimal.md": (
                "For audit-backed sources, verify the cited audit `evidence:` file:line.",
                "For issue-driven sources, verify the cited files, symbols, problem statement, or repo rule",
            ),
            "solver-structural.md": (
                "Require an audit `evidence:` block only for audit-backed sources",
                "do not fabricate one for issue-driven work",
            ),
            "solver-delete.md": (
                "from the current work-unit source",
                "If no local audit artifact exists, do not fail into invented audit content",
            ),
        }
        for prompt_name, required in prompt_expectations.items():
            prompt = (PROMPTS_DIR / prompt_name).read_text(encoding="utf-8")
            with self.subTest(prompt=prompt_name):
                for needle in required:
                    self.assertIn(needle, prompt)

    # Refactor (iter364/issue364):
    #   Old pattern: Path-A solvers dispatched with --cd $REPO_ROOT (integration checkout) can't see work-unit source when the issue references files on a divergent non-integration branch, emitting spurious no-plan and wasting rounds.
    #   New principle: Contract-only source locator: SKILL solver source contract + 3 solver prompts document a read-only source-locator recipe (git show <ref>:<path> / raw URL / gh api / host.env), classify missing/invalid locator as source-location-missing-or-invalid; NO new projection/parser/header/module.
    def test_solver_prompts_document_divergent_source_locator_contract(self) -> None:
        for prompt_name in SOLVER_PROMPTS:
            prompt = (PROMPTS_DIR / prompt_name).read_text(encoding="utf-8")
            with self.subTest(prompt=prompt_name):
                for needle in (
                    "source locator",
                    "current checkout",
                    "read-only",
                    "git show <ref>:<path>",
                    "raw URL",
                    "gh api",
                    "explicit host-owned file named by `CONSENSUS_RND_HOST_ENV`",
                    "must not fetch/checkout/switch/merge/rebase/reset",
                    "must not create source worktree/add-dir",
                    "source-location-missing-or-invalid",
                ):
                    self.assertIn(needle, prompt)


if __name__ == "__main__":
    unittest.main()
