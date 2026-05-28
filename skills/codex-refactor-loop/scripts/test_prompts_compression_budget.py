import pathlib
import unittest

PROMPTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "prompts"
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
    "reviewer-architect.md": ["REVIEW_DONE:architect:"],
    "reviewer-tests.md": ["REVIEW_DONE:tests:"],
    "reviewer-quality.md": ["REVIEW_DONE:quality:"],
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

    def test_sentinel_in_github_posting_prompts(self):
        github_post_files = [
            "design-issue-body.md",
            "design-issue-reply.md",
            "_github-post-rules.md",
        ]
        for fname in github_post_files:
            body = (PROMPTS_DIR / fname).read_text()
            self.assertIn("⟦AI:AUTO-LOOP⟧", body, f"{fname} missing sentinel")


if __name__ == "__main__":
    unittest.main()
