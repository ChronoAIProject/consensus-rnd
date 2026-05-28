#!/usr/bin/env python3
"""Behavior tests for check_skill_degradation.py."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]
CHECKER_PATH = SCRIPT_PATH.with_name("check_skill_degradation.py")


def load_checker_module():
    spec = importlib.util.spec_from_file_location("check_skill_degradation", CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def copy_minimal_repo() -> tempfile.TemporaryDirectory[str]:
    tmp = tempfile.TemporaryDirectory()
    repo = Path(tmp.name) / "repo"
    paths = [
        ".github/workflows/consensus-rnd-ci.yml",
        ".github/workflows/release.yml",
        "skills/codex-refactor-loop/SKILL.md",
        "skills/codex-refactor-loop/REFERENCE.md",
        "skills/codex-refactor-loop/host.env.example",
        "skills/codex-refactor-loop/scripts/auto_release_gate.py",
        "skills/codex-refactor-loop/scripts/check_skill_degradation.py",
        "skills/codex-refactor-loop/scripts/concurrency_monitor.py",
        "skills/codex-refactor-loop/scripts/peek.sh",
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
            [sys.executable, str(CHECKER_PATH), "--static", "--repo-root", str(REPO_ROOT)],
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

    def test_checker_detects_release_gate_missing_skill_degradation(self) -> None:
        with copy_minimal_repo() as tmp:
            repo = Path(tmp) / "repo"
            gate = repo / "skills/codex-refactor-loop/scripts/auto_release_gate.py"
            gate.write_text(gate.read_text(encoding="utf-8").replace(', "skill-degradation"', ""), encoding="utf-8")

            findings = self.checker_module.SkillDriftChecker(repo).run_static()

        self.assertTrue(any(f.check == "release-gate" for f in findings))

    def test_checker_allows_forbidden_terms_only_in_denial_context(self) -> None:
        with copy_minimal_repo() as tmp:
            repo = Path(tmp) / "repo"
            reference = repo / "skills/codex-refactor-loop/REFERENCE.md"
            reference.write_text(reference.read_text(encoding="utf-8") + "\nRuntime creates DegradationCheck objects.\n", encoding="utf-8")

            findings = self.checker_module.SkillDriftChecker(repo).run_static()

        self.assertTrue(any(f.check == "forbidden-surface" and "DegradationCheck" in f.message for f in findings))


if __name__ == "__main__":
    unittest.main()
