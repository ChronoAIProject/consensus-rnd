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
    r"not allowed|marker/artifact-only|lifecycle[^.\n]*label[^.\n]*controller|"
    r"controller[^.\n]*(owns|owner|拥有|归|创 PR)"
)

REQUIRED_LIFECYCLE_COMMAND_TOKENS = (
    "gh pr create",
    "gh pr merge",
    "gh pr close",
    "gh issue create",
    "gh issue close",
    "gh issue edit --add-label",
    "gh issue edit --remove-label",
    "gh pr edit --add-label",
    "gh pr edit --remove-label",
    "git commit",
    "git push",
    "git checkout",
    "git merge",
    "git reset",
    "git rebase",
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


def lifecycle_token_pattern(token: str) -> re.Pattern[str]:
    command, verb = token.split(" ", 1)
    if command == "git":
        return re.compile(r"`git [^`\n]*\b" + re.escape(verb) + r"\b[^`\n]*`|" + re.escape(token))
    return re.compile(r"`[^`\n]*\b" + re.escape(token) + r"\b[^`\n]*`|" + re.escape(token))


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


def ban_section(body: str) -> str:
    match = re.search(r"(?ms)^## codex .+?(?=^## |\Z)", body)
    if match:
        return match.group(0)
    return body


class MarkerOnlyPromptsGhBanTests(unittest.TestCase):
    # Refactor (iter6/issue-118):
    #   Old pattern: SKILL.md/REFERENCE.md 维护 posting-mode prompt filename roster,会漂移
    #   New principle: prompt-self-declaration consensus: 删 roster,posting mode 由 prompt body 派生 + inventory tests 强制。详见 .refactor-loop/runs/phase9-issue118-r3-judge.md
    def test_marker_only_prompts_with_ban_block_have_complete_lifecycle_ban(self) -> None:
        paths = prompts_with_marker_only_ban()
        self.assertGreater(len(paths), 0)
        for path in paths:
            with self.subTest(prompt=path.name):
                body = path.read_text(encoding="utf-8")
                section = ban_section(body)
                self.assertIn("marker/artifact-only", body)
                for needle in REQUIRED_LIFECYCLE_COMMAND_TOKENS:
                    self.assertRegex(
                        section,
                        lifecycle_token_pattern(needle),
                        f"{path.name} missing required ban-section token `{needle}`",
                    )

    def test_lifecycle_tokens_only_appear_in_ban_lines(self) -> None:
        for path in prompts_with_marker_only_ban():
            body = path.read_text(encoding="utf-8")
            section = ban_section(body)
            for token in REQUIRED_LIFECYCLE_COMMAND_TOKENS:
                pattern = lifecycle_token_pattern(token)
                for line in section.splitlines():
                    if not pattern.search(line):
                        continue
                    with self.subTest(prompt=path.name, token=token, line=line):
                        self.assertRegex(line, DENIAL_OR_CONTROLLER_OWNER_RE)
                affirmative_lines = [
                    line
                    for line in body.splitlines()
                    if pattern.search(line) and not DENIAL_OR_CONTROLLER_OWNER_RE.search(line)
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
