#!/usr/bin/env python3
"""Source-regression: marker-only prompts must explicitly ban lifecycle gh operations."""

from __future__ import annotations

import unittest
import re
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__)
PROMPTS_DIR = SCRIPT_PATH.parents[1] / "prompts"
sys.path.insert(0, str(SCRIPT_PATH.parent))

from codex_refactor_loop.cli import COMMANDS
from codex_refactor_loop.prompt_contracts import GITHUB_POST_RULES_CONTRACT_TOKEN

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
    "gh pr edit --remove-label",
    "gh pr create",
    "gh pr merge",
    "gh pr close",
    "gh issue create",
    "gh issue close",
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

LOCAL_COMMAND_ROSTER_SNIPPETS = (
    "可调:",
    "不可调:",
    "你能调的 gh 命令",
    "你不能调的(controller 边界)",
)

DIRECT_POST_COMMAND_ROSTER_SNIPPETS = (
    "gh issue/pr comment",
    "gh pr edit --body-file",
    "gh api .../reactions",
    "mktemp",
    "git commit/push/checkout",
    "git merge/reset/rebase",
    "gh pr create/merge/close",
    "gh issue create/close",
    "gh issue edit --add-label",
    "gh issue edit --remove-label",
    "gh pr edit --add-label",
    "gh pr edit --remove-label",
)


def lifecycle_token_pattern(token: str) -> re.Pattern[str]:
    command, verb = token.split(" ", 1)
    if command == "git":
        return re.compile(r"`git [^`\n]*\b" + re.escape(verb) + r"\b[^`\n]*`|" + re.escape(token))
    return re.compile(r"`[^`\n]*\b" + re.escape(token) + r"\b[^`\n]*`|" + re.escape(token))


def prompt_paths() -> list[Path]:
    return sorted(path for path in PROMPTS_DIR.glob("*.md") if path.name not in {"_github-post-rules.md", "patrol-analysis.md"})


def marker_only_prompt_paths() -> list[Path]:
    return [path for path in prompt_paths() if not re.search(r"(?m)^## GitHub post", path.read_text(encoding="utf-8"))]


def direct_post_prompt_paths() -> list[Path]:
    return [path for path in prompt_paths() if re.search(r"(?m)^## GitHub post", path.read_text(encoding="utf-8"))]


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


def github_post_section(body: str) -> str:
    match = re.search(r"(?ms)^## GitHub post.*?(?=^## |\Z)", body)
    if not match:
        return ""
    return match.group(0)


class MarkerOnlyPromptsGhBanTests(unittest.TestCase):
    # Refactor (iter6/issue-118):
    #   Old pattern: SKILL.md/REFERENCE.md 维护 posting-mode prompt filename roster,会漂移
    #   New principle: prompt-self-declaration consensus: 删 roster,posting mode 由 prompt body 派生 + inventory tests 强制。详见 .refactor-loop/runs/phase9-issue118-r3-judge.md
    # Refactor (iter1/issue-323):
    #   Old pattern: posting-capable prompt 各自复制本地 GitHub 命令 allow/deny roster,弱于 marker-only ban 且与 shared _github-post-rules.md 漂移;design-issue-reply.md posting-path 自相矛盾
    #   New principle: command roster 唯一 owner=prompts/_github-post-rules.md(补完整);7 个 direct-post prompt 删本地 可调/不可调 roster 只留 shared 引用;design-issue-reply.md 选 worker direct-post 唯一成功路径(POSTED/POST_FAILED,删 DESIGN_REPLY_READY relay);source-regression 从 prompt body 派生 posting mode;SKILL.md 不改、不新增 runtime wrapper/registry
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

    def test_worker_prompt_authority_is_not_cli_command_authority(self) -> None:
        prompt_roles = {
            "audit",
            "implement",
            "meta-judge",
            "meta-reflector-stalled",
            "remote-ci-fix",
            "review-fix",
            "reviewer-architect",
            "reviewer-quality",
            "reviewer-tests",
            "solver-delete",
            "solver-minimal",
            "solver-structural",
            "test-add",
            "triage-external-issue",
            "verify",
        }
        self.assertTrue(prompt_roles)
        self.assertFalse(prompt_roles & set(COMMANDS))

    def test_direct_post_prompts_reference_shared_contract_without_local_roster(self) -> None:
        paths = direct_post_prompt_paths()
        self.assertGreater(len(paths), 0)
        for path in paths:
            body = path.read_text(encoding="utf-8")
            section = github_post_section(body)
            with self.subTest(prompt=path.name):
                self.assertIn(GITHUB_POST_RULES_CONTRACT_TOKEN, section)
                self.assertNotIn("prompts/_github-post-rules.md", body)
                for snippet in LOCAL_COMMAND_ROSTER_SNIPPETS:
                    self.assertNotIn(snippet, section)

    def test_direct_post_prompts_do_not_copy_command_tokens(self) -> None:
        paths = direct_post_prompt_paths()
        self.assertGreater(len(paths), 0)
        for path in paths:
            section = github_post_section(path.read_text(encoding="utf-8"))
            with self.subTest(prompt=path.name):
                for snippet in DIRECT_POST_COMMAND_ROSTER_SNIPPETS:
                    self.assertNotIn(snippet, section)

    def test_shared_github_post_rules_own_complete_command_roster(self) -> None:
        body = (PROMPTS_DIR / "_github-post-rules.md").read_text(encoding="utf-8")
        allowed = re.search(r"(?ms)^## Allowed `gh` Commands.*?(?=^## |\Z)", body)
        forbidden = re.search(r"(?ms)^## Forbidden Commands \(Controller Boundary\).*?(?=^## |\Z)", body)
        self.assertIsNotNone(allowed)
        self.assertIsNotNone(forbidden)

        for needle in (
            "gh issue view",
            "gh issue comment",
            "gh pr view",
            "gh pr comment",
            "gh pr edit --body-file",
            "gh api ...",
            "mktemp",
        ):
            with self.subTest(section="allowed", needle=needle):
                self.assertIn(needle, allowed.group(0))
        for needle in (
            "git merge",
            "git reset",
            "git rebase",
            "gh pr close",
            "gh issue edit --add-label",
            "gh issue edit --remove-label",
            "gh pr edit --add-label",
            "gh pr edit --remove-label",
        ):
            with self.subTest(section="forbidden", needle=needle):
                self.assertIn(needle, forbidden.group(0))

    def test_design_issue_reply_has_single_worker_post_success_path(self) -> None:
        body = (PROMPTS_DIR / "design-issue-reply.md").read_text(encoding="utf-8")
        section = github_post_section(body)
        self.assertIn("## GitHub post", body)
        self.assertIn(GITHUB_POST_RULES_CONTRACT_TOKEN, section)
        self.assertNotIn("prompts/_github-post-rules.md", body)
        self.assertNotIn("controller 会读这个文件", body)
        self.assertNotIn("DESIGN_REPLY_READY", body)
        self.assertIn("POSTED:design-reply", body)
        self.assertIn("POST_FAILED:design-reply", body)
        self.assertIn("DESIGN_REPLY_SKIPPED", body)


if __name__ == "__main__":
    unittest.main()
