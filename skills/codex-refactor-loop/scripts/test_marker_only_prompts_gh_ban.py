#!/usr/bin/env python3
"""Source-regression: marker-only prompts must explicitly ban lifecycle gh operations."""

from __future__ import annotations

import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__)
PROMPTS_DIR = SCRIPT_PATH.parents[1] / "prompts"

MARKER_ONLY_PROMPTS = (
    "audit.md",
    "implement.md",
    "verify.md",
    "remote-ci-fix.md",
    "test-add.md",
)

REQUIRED_BAN_SUBSTRINGS = (
    "Marker/artifact-only",
    "GitHub",
)

REQUIRED_BAN_PATTERNS = (
    r"(git commit/push/checkout/merge/reset/rebase|commit, push, checkout.*merge)",
    r"(PR create/merge/close|create PR, merge, close issues/PRs)",
    r"(issue create/close|close issues/PRs)",
    r"(label edits|edit labels)",
)

FORBIDDEN_DIRECT_LIFECYCLE_SNIPPETS = (
    "gh issue edit --add-label",
    "gh issue edit --remove-label",
    "gh pr edit --add-label",
    "gh pr create",
    "git commit",
    "git push",
    "git checkout",
    "git merge",
    "git reset",
    "git rebase",
)


class MarkerOnlyPromptsGhBanTests(unittest.TestCase):
    def test_each_marker_only_prompt_has_complete_ban_section(self) -> None:
        for filename in MARKER_ONLY_PROMPTS:
            with self.subTest(prompt=filename):
                path = PROMPTS_DIR / filename
                self.assertTrue(path.exists(), f"missing prompt: {filename}")
                body = path.read_text(encoding="utf-8")
                for needle in REQUIRED_BAN_SUBSTRINGS:
                    self.assertIn(
                        needle,
                        body,
                        f"{filename} 缺少必备字面 `{needle}`(iter5 ban section regression)",
                    )
                for pattern in REQUIRED_BAN_PATTERNS:
                    self.assertRegex(
                        body,
                        pattern,
                        f"{filename} 缺少必备 lifecycle ban pattern `{pattern}`",
                    )

    def test_marker_only_prompts_preserve_sentinel_contract(self) -> None:
        for filename in MARKER_ONLY_PROMPTS:
            with self.subTest(prompt=filename):
                body = (PROMPTS_DIR / filename).read_text(encoding="utf-8")
                self.assertIn("⟦AI:AUTO-LOOP⟧", body)
                self.assertIn("末尾独立一行", body)

    def test_lifecycle_tokens_only_appear_in_ban_lines(self) -> None:
        for filename in MARKER_ONLY_PROMPTS:
            body = (PROMPTS_DIR / filename).read_text(encoding="utf-8")
            for token in FORBIDDEN_DIRECT_LIFECYCLE_SNIPPETS:
                for line in body.splitlines():
                    if token not in line:
                        continue
                    with self.subTest(prompt=filename, token=token, line=line):
                        self.assertRegex(line, r"不可调|禁止|不得|marker/artifact-only|controller|Do not|Forbidden")


if __name__ == "__main__":
    unittest.main()
