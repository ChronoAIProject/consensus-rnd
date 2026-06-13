#!/usr/bin/env python3
"""Source-regression tests for the consensus-loop skill basename migration."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
SKILL_ROOT = REPO_ROOT / "skills" / "consensus-loop"
MIGRATION = SKILL_ROOT / "migrations" / "2026-06-09-skill-basename-rename.json"

sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.restart import DAEMON_COMMANDS, daemon_target


OLD_SKILL_PATH_LITERAL = "skills/codex-refactor-loop"


def _git_tracked_files(repo_root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    )
    return [
        repo_root / path.decode("utf-8")
        for path in result.stdout.split(b"\0")
        if path
    ]


def _old_skill_path_literal_offenders(repo_root: Path, allowed: set[str]) -> list[str]:
    offenders: list[str] = []
    for path in _git_tracked_files(repo_root):
        if not path.exists() or path.is_dir():
            continue
        relative = path.relative_to(repo_root).as_posix()
        if relative in allowed:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if OLD_SKILL_PATH_LITERAL in text:
            offenders.append(relative)
    return offenders


class SkillBasenameMigrationTests(unittest.TestCase):
    def test_skill_directory_moved_without_compat_stub(self) -> None:
        self.assertTrue((SKILL_ROOT / "SKILL.md").is_file())
        self.assertFalse((REPO_ROOT / "skills" / "codex-refactor-loop").exists())

    def test_version_map_points_at_new_skill_manifest(self) -> None:
        version_map = json.loads((REPO_ROOT / ".version-bump.json").read_text(encoding="utf-8"))
        records = {(record["path"], record["field"]) for record in version_map["files"]}

        self.assertIn(("skills/consensus-loop/VERSION.json", "version"), records)
        self.assertNotIn(("skills/codex-refactor-loop/VERSION.json", "version"), records)

    def test_ci_and_release_workflows_call_new_skill_cli(self) -> None:
        for workflow in (
            REPO_ROOT / ".github" / "workflows" / "consensus-rnd-ci.yml",
            REPO_ROOT / ".github" / "workflows" / "release.yml",
        ):
            text = workflow.read_text(encoding="utf-8")
            with self.subTest(workflow=workflow.name):
                self.assertIn("skills/consensus-loop/scripts/consensus-rnd-cli", text)
                self.assertNotIn("skills/codex-refactor-loop/scripts/consensus-rnd-cli", text)

    def test_migration_artifact_records_old_to_new_boundary(self) -> None:
        migration = json.loads(MIGRATION.read_text(encoding="utf-8"))

        self.assertEqual("skill-basename-migration", migration["schema"])
        self.assertEqual("2026-06-09", migration["date"])
        self.assertEqual("codex-refactor-loop", migration["old_basename"])
        self.assertEqual("consensus-loop", migration["new_basename"])
        self.assertEqual("skills/codex-refactor-loop", migration["old_path"])
        self.assertEqual("skills/consensus-loop", migration["new_path"])
        self.assertTrue(migration["no_compat_stub"])
        self.assertIn("consensus-rnd-cli", migration["preserved"])
        self.assertIn("codex_refactor_loop python package", migration["preserved"])

    def test_no_functional_old_skill_path_literals_remain(self) -> None:
        allowed = {
            "skills/consensus-loop/migrations/2026-06-09-skill-basename-rename.json",
            "skills/consensus-loop/scripts/test_skill_basename_migration.py",
        }
        self.assertEqual([], _old_skill_path_literal_offenders(REPO_ROOT, allowed))

    def test_old_skill_path_scan_ignores_untracked_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            subprocess.run(["git", "-C", str(repo_root), "init", "-q"], check=True)
            runtime_file = repo_root / ".refactor-loop" / "runs" / "scratch.md"
            runtime_file.parent.mkdir(parents=True)
            runtime_file.write_text(OLD_SKILL_PATH_LITERAL, encoding="utf-8")

            self.assertEqual([], _old_skill_path_literal_offenders(repo_root, set()))

    def test_old_skill_path_scan_reports_tracked_source_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            subprocess.run(["git", "-C", str(repo_root), "init", "-q"], check=True)
            source_file = repo_root / "docs" / "source.md"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(OLD_SKILL_PATH_LITERAL, encoding="utf-8")
            subprocess.run(["git", "-C", str(repo_root), "add", "docs/source.md"], check=True)

            self.assertEqual(
                ["docs/source.md"],
                _old_skill_path_literal_offenders(repo_root, set()),
            )

    def test_daemon_commands_render_new_skill_cli_entrypoint(self) -> None:
        ctx = LoopContext.load(repo_root=REPO_ROOT, skill_root=SKILL_ROOT, env={})
        expected = str(SKILL_ROOT / "scripts" / "consensus-rnd-cli")
        forbidden = str(REPO_ROOT / "skills" / "codex-refactor-loop" / "scripts" / "consensus-rnd-cli")

        for name, command_template in DAEMON_COMMANDS:
            command = daemon_target(ctx, name, command_template).command
            with self.subTest(daemon=name):
                self.assertIn(expected, command)
                self.assertNotIn(forbidden, command)


if __name__ == "__main__":
    unittest.main()
