import pathlib
import unittest

PROMPTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "prompts"
SKILL_DIR = PROMPTS_DIR.parent
LINE_BUDGET = 1250
REQUIRED_TOKENS_PER_FILE = {
    "solver-minimal.md": ["SOLVER_DONE:minimal:"],
    "solver-structural.md": ["SOLVER_DONE:structural:"],
    "solver-delete.md": ["SOLVER_DONE:delete:"],
    "meta-judge.md": ["META_JUDGE_DONE:"],
    "implement.md": ["IMPLEMENT_DONE:"],
    "verify.md": ["VERIFY_DONE:"],
    "audit.md": ["AUDIT_DONE:"],
    "review-fix.md": ["FIX_DONE:"],
    "reviewer-architect.md": ["REVIEW_DONE:${PR_NUMBER}:architect:<verdict>"],
    "reviewer-tests.md": ["REVIEW_DONE:${PR_NUMBER}:tests:<verdict>"],
    "reviewer-quality.md": ["REVIEW_DONE:${PR_NUMBER}:quality:<verdict>"],
    "test-add.md": ["TEST_ADD_DONE:"],
    "meta-reflector-stalled.md": ["META_RESOLVED:"],
}
FORBIDDEN_TOKENS = [
    "PromptPartialV1",
    "render_prompt",
    "PromptPartial",
    "deferred-as-issue",
    "tracking-issue",
]
FORBIDDEN_DELETE_SOLVER_VOCABULARY = [
    "delete/defer",
    "delete / defer",
    "collapse-and-redirect",
]
GITHUB_POST_LANGUAGE_FILES = [
    "_github-post-rules.md",
    "design-issue-body.md",
    "design-issue-reply.md",
]
CHINESE_SCAFFOLD_FILES = {
    "design-issue-body.md": ["## 摘要", "## 需要维护者确认"],
    "design-issue-reply.md": ["GitHub-facing reply body must be 中文 by default"],
    "triage-external-issue.md": ["中文 `human_brief`", "中文 GitHub-facing comment artifact"],
}


class PromptsCompressionBudgetTests(unittest.TestCase):
    def test_total_lines_under_budget(self):
        total = sum(len(p.read_text().splitlines()) for p in PROMPTS_DIR.glob("*.md"))
        self.assertLessEqual(total, LINE_BUDGET, f"total {total} > budget {LINE_BUDGET}")

    def test_marker_tokens_preserved(self):
        for fname, tokens in REQUIRED_TOKENS_PER_FILE.items():
            body = (PROMPTS_DIR / fname).read_text()
            for tok in tokens:
                self.assertIn(tok, body, f"{fname} missing marker token {tok!r}")

    def test_no_forbidden_render_layer_tokens(self):
        for p in PROMPTS_DIR.glob("*.md"):
            body = p.read_text()
            for tok in FORBIDDEN_TOKENS:
                self.assertNotIn(tok, body, f"{p.name} still contains forbidden token {tok!r}")

    def test_delete_solver_vocabulary_fact_sources_are_synced(self):
        fact_sources = [
            SKILL_DIR / "SKILL.md",
            SKILL_DIR / "REFERENCE.md",
            *PROMPTS_DIR.glob("*.md"),
        ]
        for p in fact_sources:
            body = p.read_text()
            for tok in FORBIDDEN_DELETE_SOLVER_VOCABULARY:
                self.assertNotIn(tok, body, f"{p.name} still contains stale delete-solver vocabulary {tok!r}")

    def test_sentinel_in_github_posting_prompts(self):
        for fname in GITHUB_POST_LANGUAGE_FILES:
            body = (PROMPTS_DIR / fname).read_text()
            self.assertIn("⟦AI:AUTO-LOOP⟧", body, f"{fname} missing sentinel")

    def test_github_posting_language_contract_preserved(self):
        required = [
            "中文 by default",
            "identifiers / paths / quoted rule text remain verbatim inline",
            "no mandatory parallel English section",
        ]
        shared = (PROMPTS_DIR / "_github-post-rules.md").read_text()
        self.assertIn("GitHub-facing comments / PR bodies are 中文 by default", shared)
        self.assertIn("PROJECT_RULES/AGENTS quotes also stay verbatim", shared)
        for fname in GITHUB_POST_LANGUAGE_FILES:
            body = (PROMPTS_DIR / fname).read_text()
            for token in required:
                self.assertIn(token, body, f"{fname} missing language token {token!r}")

    def test_direct_post_templates_keep_chinese_scaffold(self):
        for fname, tokens in CHINESE_SCAFFOLD_FILES.items():
            body = (PROMPTS_DIR / fname).read_text()
            for token in tokens:
                self.assertIn(token, body, f"{fname} missing Chinese scaffold token {token!r}")

    def test_compressed_prompts_keep_refactor_self_doc(self):
        for p in PROMPTS_DIR.glob("*.md"):
            body = p.read_text()
            self.assertIn("Refactor (", body, f"{p.name} missing compression self-doc")


if __name__ == "__main__":
    unittest.main()
