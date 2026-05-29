#!/usr/bin/env python3
"""Behavior tests for consensus-rnd-cli check-degradation."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]
CHECKER_PATH = SCRIPT_PATH.with_name("consensus-rnd-cli")


def load_checker_module():
    from codex_refactor_loop.checks import degradation
    return degradation


def copy_minimal_repo() -> tempfile.TemporaryDirectory[str]:
    tmp = tempfile.TemporaryDirectory()
    repo = Path(tmp.name) / "repo"
    paths = [
        ".github/workflows/consensus-rnd-ci.yml",
        ".github/workflows/release.yml",
        "skills/codex-refactor-loop/SKILL.md",
        "skills/codex-refactor-loop/host.env.example",
        "skills/codex-refactor-loop/scripts/consensus-rnd-cli",
        "skills/codex-refactor-loop/scripts/codex_refactor_loop/release/gate.py",
        "skills/codex-refactor-loop/scripts/codex_refactor_loop/release/required_checks.py",
        "skills/codex-refactor-loop/scripts/codex_refactor_loop/checks/degradation.py",
        "skills/codex-refactor-loop/scripts/codex_refactor_loop/monitors/concurrency.py",
        "skills/codex-refactor-loop/scripts/codex_refactor_loop/peek.py",
    ]
    for relative in paths:
        source = REPO_ROOT / relative
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return tmp


class SkillDegradationCheckerBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.checker_module = load_checker_module()

    def test_static_checker_passes_current_repo(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CHECKER_PATH), "check-degradation", "--static", "--repo-root", str(REPO_ROOT)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("skill-degradation: ok", result.stdout)

    def test_checker_detects_missing_named_exception(self) -> None:
        with copy_minimal_repo() as tmp:
            repo = Path(tmp) / "repo"
            skill = repo / "skills/codex-refactor-loop/SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8").replace(
                    "## Named runtime exception — skill degradation watch(per #66)",
                    "## Removed heading",
                ),
                encoding="utf-8",
            )

            findings = self.checker_module.SkillDriftChecker(repo).run_static()

        self.assertTrue(any(f.check == "skill-named-exception" for f in findings))
        self.assertTrue(any("skill degradation watch" in f.message for f in findings))

    def test_checker_detects_forbidden_runtime_file(self) -> None:
        with copy_minimal_repo() as tmp:
            repo = Path(tmp) / "repo"
            forbidden = repo / "skills/codex-refactor-loop/scripts/degradation_watchdog.py"
            forbidden.write_text("print('forbidden')\n", encoding="utf-8")

            findings = self.checker_module.SkillDriftChecker(repo).run_static()

        self.assertTrue(any(f.check == "forbidden-runtime-file" and "degradation_watchdog.py" in f.path for f in findings))

    def test_checker_detects_release_projection_missing_skill_degradation(self) -> None:
        with copy_minimal_repo() as tmp:
            repo = Path(tmp) / "repo"
            projection = repo / "skills/codex-refactor-loop/scripts/codex_refactor_loop/release/required_checks.py"
            projection.write_text(projection.read_text(encoding="utf-8").replace(', "skill-degradation"', ""), encoding="utf-8")

            findings = self.checker_module.SkillDriftChecker(repo).run_static()

        self.assertTrue(any(f.check in {"release-gate", "release-workflow"} for f in findings))

    def test_checker_allows_forbidden_terms_only_in_denial_context(self) -> None:
        with copy_minimal_repo() as tmp:
            repo = Path(tmp) / "repo"
            skill = repo / "skills/codex-refactor-loop/SKILL.md"
            skill.write_text(skill.read_text(encoding="utf-8") + "\nRuntime creates DegradationCheck objects.\n", encoding="utf-8")

            findings = self.checker_module.SkillDriftChecker(repo).run_static()

        self.assertTrue(any(f.check == "forbidden-surface" and "DegradationCheck" in f.message for f in findings))


if __name__ == "__main__":
    unittest.main()
