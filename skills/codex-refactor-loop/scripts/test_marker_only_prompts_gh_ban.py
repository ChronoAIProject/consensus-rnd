#!/usr/bin/env python3
"""Source-regression: marker-only prompts must explicitly ban lifecycle gh operations."""

from __future__ import annotations

import unittest
import re
from pathlib import Path


SCRIPT_PATH = Path(__file__)
PROMPTS_DIR = SCRIPT_PATH.parents[1] / "prompts"

DENIAL_OR_CONTROLLER_OWNER_RE = re.compile(
    r"禁止|不可调|不得|不能|不要|Forbidden|forbidden|Do NOT|do not|must not|"
    r"not allowed|marker/artifact-only|lifecycle / label .*controller|controller[^.\n]*(owns|owner|拥有|归|创 PR)"
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

AFFIRMATIVE_DIRECT_POST_SNIPPETS = (
    "You DO post to GitHub directly",
    "自己调 `gh` post",
    "Post 后打印 `POSTED:",
)


def prompt_paths() -> list[Path]:
    return sorted(path for path in PROMPTS_DIR.glob("*.md") if path.name != "_github-post-rules.md")


def marker_only_prompt_paths() -> list[Path]:
    return [path for path in prompt_paths() if not re.search(r"(?m)^## GitHub post", path.read_text(encoding="utf-8"))]


def prompts_with_marker_only_ban() -> list[Path]:
    paths = []
    for path in marker_only_prompt_paths():
        body = path.read_text(encoding="utf-8")
        if "## codex " in body or "marker/artifact-only" in body:
            paths.append(path)
    return paths


class MarkerOnlyPromptsGhBanTests(unittest.TestCase):
    # Refactor (iter6/issue-118):
    #   Old pattern: SKILL.md/REFERENCE.md 维护 posting-mode prompt filename roster,会漂移
    #   New principle: prompt-self-declaration consensus: 删 roster,posting mode 由 prompt body 派生 + inventory tests 强制。详见 DESIGN_DECISION_PATH
    def test_marker_only_prompts_with_ban_block_have_complete_lifecycle_ban(self) -> None:
        paths = prompts_with_marker_only_ban()
        self.assertGreater(len(paths), 0)
        for path in paths:
            with self.subTest(prompt=path.name):
                body = path.read_text(encoding="utf-8")
                for needle in REQUIRED_BAN_SUBSTRINGS:
                    self.assertIn(
                        needle,
                        body,
                        f"{path.name} missing required ban-section token `{needle}`",
                    )
                self.assertRegex(body, r"(?m)^## codex .+$")

    def test_refactor_self_doc_block_present(self) -> None:
        for path in prompts_with_marker_only_ban():
            with self.subTest(prompt=path.name):
                body = path.read_text(encoding="utf-8")
                self.assertIn(
                    "Refactor (iter5/prompt-gh-ban-marker-only)",
                    body,
                    f"{path.name} missing Refactor self-doc block",
                )

    def test_lifecycle_tokens_only_appear_in_ban_lines(self) -> None:
        for path in prompts_with_marker_only_ban():
            body = path.read_text(encoding="utf-8")
            ban_section_match = re.search(r"(?ms)^## codex .+?(?=^## |\Z)", body)
            self.assertIsNotNone(ban_section_match, path.name)
            ban_section = ban_section_match.group(0)
            for token in FORBIDDEN_DIRECT_LIFECYCLE_SNIPPETS:
                for line in ban_section.splitlines():
                    if token not in line:
                        continue
                    with self.subTest(prompt=path.name, token=token, line=line):
                        self.assertRegex(line, DENIAL_OR_CONTROLLER_OWNER_RE)
                affirmative_lines = [
                    line
                    for line in body.splitlines()
                    if token in line and not DENIAL_OR_CONTROLLER_OWNER_RE.search(line)
                ]
                self.assertEqual(
                    affirmative_lines,
                    [],
                    f"{path.name} mentions forbidden lifecycle token `{token}` outside denial/controller-owner context",
                )

    def test_all_marker_only_prompts_forbid_affirmative_direct_post_wording(self) -> None:
        paths = marker_only_prompt_paths()
        self.assertGreater(len(paths), 0)
        for path in paths:
            body = path.read_text(encoding="utf-8")
            with self.subTest(prompt=path.name):
                self.assertNotIn("## GitHub post", body)
                self.assertIn("⟦AI:AUTO-LOOP⟧", body)
                for snippet in AFFIRMATIVE_DIRECT_POST_SNIPPETS:
                    self.assertNotIn(snippet, body)


if __name__ == "__main__":
    unittest.main()
