#!/usr/bin/env python3
"""Git-backed source publication boundary contract tests."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]
README = REPO_ROOT / "README.md"


class SourcePublicationBoundaryContract(unittest.TestCase):
    # Refactor (iter317/issue-317):
    #   Old pattern: tracked .claude/settings.json 把本地 Claude Code 运行时配置混进发布产物(违反发布产物边界 / 本地运行时不当事实源)
    #   New principle: 删 tracked .claude/settings.json + .gitignore 忽略;独立 test_source_publication_boundary.py git-backed contract test 作唯一 guard;不扩 skill-degradation 权限
    # Refactor (iter374/issue-374):
    #   Old pattern: 仓库 track .claude/skills -> ../skills symlink(在 .claude-plugin 之外),形成第二个 Claude 专属 skills 入口,偏离各平台共享 skills/ 靠根 manifest 指向的约定。
    #   New principle: 删除 tracked .claude/skills 改为 ignored local setup:git rm .claude/skills;.gitignore 改为忽略 .claude/skills(本机 setup 不发布);README 补 1 句本地可手动建 symlink;反转 source-publication-boundary 测试断言 git check-ignore .claude/skills 成功且 git ls-files .claude 不含 skills。根 .claude-plugin manifest 仍为唯一 Claude 入口。不改 CLAUDE.md philosophy。
    def run_git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_claude_settings_json_is_not_tracked_and_is_ignored(self) -> None:
        tracked = self.run_git("ls-files", "--error-unmatch", ".claude/settings.json")
        self.assertNotEqual(0, tracked.returncode, tracked.stdout)

        ignored = self.run_git("check-ignore", ".claude/settings.json")
        self.assertEqual(0, ignored.returncode, ignored.stderr)
        self.assertEqual(".claude/settings.json", ignored.stdout.strip())

    def test_claude_skills_link_is_not_tracked_and_is_ignored(self) -> None:
        tracked = self.run_git("ls-files", "--error-unmatch", ".claude/skills")
        self.assertNotEqual(0, tracked.returncode, tracked.stdout)

        ignored = self.run_git("check-ignore", ".claude/skills")
        self.assertEqual(0, ignored.returncode, ignored.stderr)
        self.assertEqual(".claude/skills", ignored.stdout.strip())

    def test_no_tracked_claude_skill_surface_remains(self) -> None:
        tracked = self.run_git("ls-files", ".claude")
        self.assertEqual(0, tracked.returncode, tracked.stderr)
        tracked_paths = set(tracked.stdout.splitlines())

        self.assertNotIn(".claude/skills", tracked_paths)
        self.assertNotIn(".claude/settings.json", tracked_paths)
        self.assertNotIn(".claude/settings.local.json", tracked_paths)

    def test_readme_documents_local_claude_symlink_as_unpublished_setup(self) -> None:
        readme = README.read_text(encoding="utf-8")

        self.assertIn("`.claude/skills -> ../skills`", readme)
        self.assertIn("ignored", readme)
        self.assertIn("not a published Claude entrypoint", readme)

    def test_publication_boundary_contract_is_release_gated(self) -> None:
        workflow = (REPO_ROOT / ".github/workflows/consensus-rnd-ci.yml").read_text(encoding="utf-8")
        required_checks = (
            REPO_ROOT
            / "skills/codex-refactor-loop/scripts/codex_refactor_loop/release/required_checks.py"
        ).read_text(encoding="utf-8")

        self.assertIn("contract-tests:", workflow)
        self.assertIn(
            "python3 -m unittest discover -s skills/codex-refactor-loop/scripts -p 'test_*.py'",
            workflow,
        )
        self.assertIn("HOST_GITHUB_RELEASE_REQUIRED_CHECKS", required_checks)
        self.assertIn("required_release_checks", required_checks)
        self.assertNotIn('"contract-tests"', required_checks)
        self.assertNotIn('"skill-degradation"', required_checks)


if __name__ == "__main__":
    unittest.main()
