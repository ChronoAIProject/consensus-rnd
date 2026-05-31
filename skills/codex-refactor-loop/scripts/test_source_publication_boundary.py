#!/usr/bin/env python3
"""Git-backed source publication boundary contract tests."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]


class SourcePublicationBoundaryContract(unittest.TestCase):
    # Refactor (iter317/issue-317):
    #   Old pattern: tracked .claude/settings.json 把本地 Claude Code 运行时配置混进发布产物(违反发布产物边界 / 本地运行时不当事实源)
    #   New principle: 删 tracked .claude/settings.json + .gitignore 忽略;独立 test_source_publication_boundary.py git-backed contract test 作唯一 guard;不扩 skill-degradation 权限
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

    def test_tracked_claude_surface_is_only_skill_link(self) -> None:
        tracked = self.run_git("ls-files", ".claude")
        self.assertEqual(0, tracked.returncode, tracked.stderr)
        tracked_paths = set(tracked.stdout.splitlines())

        self.assertEqual({".claude/skills"}, tracked_paths)
        self.assertNotIn(".claude/settings.json", tracked_paths)
        self.assertNotIn(".claude/settings.local.json", tracked_paths)

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
