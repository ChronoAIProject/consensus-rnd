#!/usr/bin/env python3
"""Behavior tests for ensure_project_rules_fixed_points.py."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ensure_project_rules_fixed_points import (
    CANONICAL_BODY,
    CANONICAL_HASH,
    END_MARKER,
    OLD_CANONICAL_BODY,
    ProjectRulesFixedPointEnsurer,
    START_RE,
    sha256_text,
)

SCRIPT_PATH = Path(__file__).with_name("ensure_project_rules_fixed_points.py")


class ProjectRulesFixedPointEnsurerTests(unittest.TestCase):
    # Refactor (iter1/host-claude-md-fixed-points):
    #   Old pattern: host 的 PROJECT_RULES/CLAUDE.md 不保证基础不动点(泛化理论)在场,跑 loop 时基础理论未被可靠加载
    #   New principle: Phase 0 ProjectRulesFixedPointEnsurer 幂等向 $PROJECT_RULES 写入带 sentinel 的 managed 不动点区块(consensus:minimal,不覆盖 host 已有内容)
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.rules = self.repo / "CLAUDE.md"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def ensure(self, project_rules: str = "CLAUDE.md") -> str:
        return ProjectRulesFixedPointEnsurer(str(self.repo), project_rules).ensure()

    def run_cli(self, env_updates: dict[str, str] | None = None, *, clear_rules_env: bool = True) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if clear_rules_env:
            env.pop("REPO_ROOT", None)
            env.pop("PROJECT_RULES", None)
        if env_updates:
            env.update(env_updates)
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_cli_default_project_rules_writes_and_reports_status(self) -> None:
        self.rules.write_text("# Host rules\nExisting text.\n", encoding="utf-8")

        first = self.run_cli({"REPO_ROOT": str(self.repo)})

        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stdout, "PROJECT_RULES_FIXED_POINT:updated\n")
        self.assertEqual(first.stderr, "")
        self.assertIn(CANONICAL_BODY, self.rules.read_text(encoding="utf-8"))

        second = self.run_cli({"REPO_ROOT": str(self.repo)})

        self.assertEqual(second.returncode, 0)
        self.assertEqual(second.stdout, "PROJECT_RULES_FIXED_POINT:already-current\n")
        self.assertEqual(second.stderr, "")

    def test_cli_explicit_project_rules_targets_nested_file(self) -> None:
        nested_rules = self.repo / "docs" / "RULES.md"
        nested_rules.parent.mkdir()
        nested_rules.write_text("# Nested host rules\n", encoding="utf-8")

        result = self.run_cli({"REPO_ROOT": str(self.repo), "PROJECT_RULES": "docs/RULES.md"})

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "PROJECT_RULES_FIXED_POINT:updated\n")
        self.assertEqual(result.stderr, "")
        self.assertIn(CANONICAL_BODY, nested_rules.read_text(encoding="utf-8"))
        self.assertFalse(self.rules.exists())

    def test_cli_missing_repo_root_fails_closed_without_modifying_files(self) -> None:
        original = "# Host rules\n"
        self.rules.write_text(original, encoding="utf-8")

        result = self.run_cli()

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("PROJECT_RULES_FIXED_POINT_ERROR: REPO_ROOT is required", result.stderr)
        self.assertEqual(self.rules.read_text(encoding="utf-8"), original)

    def test_first_append_adds_one_managed_block(self) -> None:
        self.rules.write_text("# Host rules\nExisting text.\n", encoding="utf-8")

        status = self.ensure()

        text = self.rules.read_text(encoding="utf-8")
        self.assertEqual(status, "updated")
        self.assertTrue(text.startswith("# Host rules\nExisting text.\n\n\n"))
        self.assertEqual(text.count("consensus-rnd:foundational-invariants:start"), 1)
        self.assertEqual(text.count(END_MARKER), 1)
        self.assertIn(f"sha256={CANONICAL_HASH}", text)
        self.assertIn(CANONICAL_BODY, text)

    def test_repeated_ensure_is_byte_stable(self) -> None:
        self.rules.write_text("# Host rules\n", encoding="utf-8")
        self.ensure()
        once = self.rules.read_bytes()

        status = self.ensure()

        self.assertEqual(status, "already-current")
        self.assertEqual(self.rules.read_bytes(), once)

    def test_preserves_content_outside_managed_block(self) -> None:
        prefix = "# Host rules\nKeep this.\n"
        suffix = "\n## Host extension\nKeep that.\n"
        block = ProjectRulesFixedPointEnsurer(str(self.repo))._managed_block()
        self.rules.write_text(prefix + "\n\n" + block + suffix, encoding="utf-8")

        self.ensure()

        text = self.rules.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(prefix))
        self.assertTrue(text.endswith(suffix))

    def test_missing_rules_file_is_refused(self) -> None:
        with self.assertRaisesRegex(Exception, "does not exist"):
            self.ensure()

    def test_empty_rules_file_is_refused(self) -> None:
        self.rules.write_text("", encoding="utf-8")

        with self.assertRaisesRegex(Exception, "empty"):
            self.ensure()

    def test_unreadable_rules_file_is_refused(self) -> None:
        self.rules.write_text("# Host rules\n", encoding="utf-8")
        self.rules.chmod(0)
        try:
            with self.assertRaisesRegex(Exception, "unreadable"):
                self.ensure()
        finally:
            self.rules.chmod(0o600)

    def test_path_escape_is_refused(self) -> None:
        self.rules.write_text("# Host rules\n", encoding="utf-8")

        with self.assertRaisesRegex(Exception, "must not contain|escapes"):
            ProjectRulesFixedPointEnsurer(str(self.repo), "../CLAUDE.md")

    def test_absolute_path_outside_repo_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as outside_tmp:
            outside_rules = Path(outside_tmp) / "CLAUDE.md"
            outside_rules.write_text("# Outside host rules\n", encoding="utf-8")

            with self.assertRaisesRegex(Exception, "escapes REPO_ROOT"):
                ProjectRulesFixedPointEnsurer(str(self.repo), str(outside_rules))

    def test_duplicate_marker_is_refused_without_changes(self) -> None:
        block = ProjectRulesFixedPointEnsurer(str(self.repo))._managed_block()
        original = "# Host rules\n\n" + block + "\n\n" + block
        self.rules.write_text(original, encoding="utf-8")

        with self.assertRaisesRegex(Exception, "duplicate"):
            self.ensure()

        self.assertEqual(self.rules.read_text(encoding="utf-8"), original)

    def test_unpaired_marker_is_refused_without_changes(self) -> None:
        original = "# Host rules\n\n<!-- consensus-rnd:foundational-invariants:start version=1 sha256=" + ("0" * 64) + " -->\n"
        self.rules.write_text(original, encoding="utf-8")

        with self.assertRaisesRegex(Exception, "missing or unbalanced"):
            self.ensure()

        self.assertEqual(self.rules.read_text(encoding="utf-8"), original)

    def test_manual_edit_inside_block_fails_closed(self) -> None:
        block = ProjectRulesFixedPointEnsurer(str(self.repo))._managed_block()
        original = "# Host rules\n\n" + block.replace("FI-007 删除优先", "FI-007 手工改动")
        self.rules.write_text(original, encoding="utf-8")

        with self.assertRaisesRegex(Exception, "hash mismatch"):
            self.ensure()

        self.assertEqual(self.rules.read_text(encoding="utf-8"), original)

    def test_hash_valid_unknown_block_version_fails_closed(self) -> None:
        unknown_body = "## 共识研发不动点（由 consensus-rnd 管理）\n\n- FI-999 未知版本。\n"
        unknown_hash = sha256_text(unknown_body)
        unknown_block = (
            f"<!-- consensus-rnd:foundational-invariants:start version=1 sha256={unknown_hash} -->\n"
            f"{unknown_body}"
            f"{END_MARKER}"
        )
        original = "# Host rules\n\n" + unknown_block
        self.rules.write_text(original, encoding="utf-8")

        with self.assertRaisesRegex(Exception, "unknown managed block version"):
            self.ensure()

        self.assertEqual(self.rules.read_text(encoding="utf-8"), original)

    def test_known_old_hash_upgrades_only_managed_block(self) -> None:
        old_hash = sha256_text(OLD_CANONICAL_BODY)
        old_block = (
            f"<!-- consensus-rnd:foundational-invariants:start version=1 sha256={old_hash} -->\n"
            f"{OLD_CANONICAL_BODY}"
            f"{END_MARKER}"
        )
        original = "# Host rules\n\n" + old_block + "\n\n## Host extension\n"
        self.rules.write_text(original, encoding="utf-8")

        status = self.ensure()

        text = self.rules.read_text(encoding="utf-8")
        self.assertEqual(status, "updated")
        self.assertTrue(text.startswith("# Host rules\n\n"))
        self.assertTrue(text.endswith("\n\n## Host extension\n"))
        self.assertIn(CANONICAL_BODY, text)
        self.assertEqual(START_RE.search(text).group(1), CANONICAL_HASH)


if __name__ == "__main__":
    unittest.main()
