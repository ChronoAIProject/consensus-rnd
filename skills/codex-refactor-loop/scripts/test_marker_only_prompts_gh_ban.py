#!/usr/bin/env python3
"""Source-regression: marker-only prompts must explicitly ban lifecycle gh operations."""

from __future__ import annotations

import unittest
import re
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
    "## codex ",
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
    "git commit/push/checkout/merge/reset/rebase",
    "lifecycle / label ",
    "controller",
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
                        f"{filename} missing required ban-section token `{needle}`",
                    )
                self.assertRegex(body, r"(?m)^## codex .+$")

    def test_refactor_self_doc_block_present(self) -> None:
        for filename in MARKER_ONLY_PROMPTS:
            with self.subTest(prompt=filename):
                body = (PROMPTS_DIR / filename).read_text(encoding="utf-8")
                self.assertIn(
                    "Refactor (iter5/prompt-gh-ban-marker-only)",
                    body,
                    f"{filename} missing Refactor self-doc block",
                )

    def test_lifecycle_tokens_only_appear_in_ban_lines(self) -> None:
        for filename in MARKER_ONLY_PROMPTS:
            body = (PROMPTS_DIR / filename).read_text(encoding="utf-8")
            ban_section_match = re.search(r"(?ms)^## codex .+?(?=^## |\Z)", body)
            self.assertIsNotNone(ban_section_match, filename)
            ban_section = ban_section_match.group(0)
            for token in FORBIDDEN_DIRECT_LIFECYCLE_SNIPPETS:
                for line in body.splitlines():
                    if token not in line:
                        continue
                    with self.subTest(prompt=filename, token=token, line=line):
                        self.assertTrue(
                            line in ban_section
                            or "## 红线" in body[: body.index(line)]
                            or "## Marker emission allowlist" in body[: body.index(line)]
                        )


if __name__ == "__main__":
    unittest.main()
