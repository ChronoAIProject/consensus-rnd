#!/usr/bin/env python3
"""Source-regression tests for the consensus-loop skill basename migration."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
SKILL_ROOT = REPO_ROOT / "skills" / "consensus-loop"
MIGRATION = SKILL_ROOT / "migrations" / "2026-06-09-skill-basename-rename.json"

sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.restart import DAEMON_COMMANDS, daemon_target


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
        offenders: list[str] = []
        for path in REPO_ROOT.rglob("*"):
            if path.is_dir() or ".git" in path.parts:
                continue
            relative = path.relative_to(REPO_ROOT).as_posix()
            if relative in allowed:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if "skills/codex-refactor-loop" in text:
                offenders.append(relative)

        self.assertEqual([], offenders)

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
