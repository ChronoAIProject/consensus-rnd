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
    "## codex 工具边界(强制)",
    "iter5/prompt-gh-ban-marker-only",
    "marker/artifact-only",
    "gh pr create",
    "gh pr merge",
    "gh pr close",
    "gh issue create",
    "gh issue close",
    "gh issue edit --add-label",
    "gh issue edit --remove-label",
    "gh pr edit --add-label",
    "gh pr edit --remove-label",
    "lifecycle / label 决策归 controller",
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

    def test_refactor_self_doc_block_present(self) -> None:
        for filename in MARKER_ONLY_PROMPTS:
            with self.subTest(prompt=filename):
                body = (PROMPTS_DIR / filename).read_text(encoding="utf-8")
                self.assertIn(
                    "Refactor (iter5/prompt-gh-ban-marker-only)",
                    body,
                    f"{filename} 缺少 Refactor self-doc 块",
                )


if __name__ == "__main__":
    unittest.main()
