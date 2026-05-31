#!/usr/bin/env python3
"""Behavior tests for consensus-rnd-cli check-degradation."""

from __future__ import annotations

import json
import importlib
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
    scripts_dir = str(SCRIPT_PATH.parent)
    if sys.path[0] != scripts_dir:
        sys.path.insert(0, scripts_dir)
    importlib.invalidate_caches()
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

    # Refactor (iter259/issue-259):
    #   Old pattern: check-degradation --static 把 downstream/plugin host root 当 source tree 扫描,吐 skills/codex-refactor-loop/... required-file false-positive(每 tick rc=1)
    #   New principle: degradation.py 内加私有 not-source-repo guard:无 source sentinels 时 rc=0 + reason not-source-repo;source repo candidate 仍 fail-closed;不新增 SourceRepoValidationContext,不改 manifest.py
    def test_static_checker_treats_plugin_host_root_as_not_source_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            host = Path(tmp) / "host"
            host.mkdir()

            result = subprocess.run(
                [sys.executable, str(CHECKER_PATH), "check-degradation", "--static", "--repo-root", str(host)],
                cwd=host,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("not-source-repo", result.stdout)
        self.assertNotIn("required-file: skills/codex-refactor-loop", result.stdout)

    def test_static_checker_reports_json_for_plugin_host_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            host = Path(tmp) / "host"
            host.mkdir()

            result = subprocess.run(
                [sys.executable, str(CHECKER_PATH), "check-degradation", "--static", "--json", "--repo-root", str(host)],
                cwd=host,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), {"ok": True, "reason": "not-source-repo", "findings": []})
        self.assertEqual(result.stderr, "")

    def test_static_checker_discovers_plugin_host_root_as_not_source_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            host = Path(tmp) / "host"
            host.mkdir()

            result = subprocess.run(
                [sys.executable, str(CHECKER_PATH), "check-degradation", "--static"],
                cwd=host,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("not-source-repo", result.stdout)
        self.assertNotIn("required-file: skills/codex-refactor-loop", result.stdout)

    def test_run_static_treats_plugin_host_root_as_not_source_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            host = Path(tmp) / "host"
            host.mkdir()

            findings = self.checker_module.SkillDriftChecker(host).run_static()

        self.assertEqual(findings, [])

    def test_static_checker_fails_closed_for_damaged_source_repo_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / ".version-bump.json").write_text('{"files": []}\n', encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(CHECKER_PATH), "check-degradation", "--static", "--repo-root", str(repo)],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("required-file: skills/codex-refactor-loop", result.stdout)
        self.assertNotIn("not-source-repo", result.stdout)

    def test_static_checker_discovered_source_repo_candidate_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            (repo / ".version-bump.json").write_text('{"files": []}\n', encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(CHECKER_PATH), "check-degradation", "--static"],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("required-file: skills/codex-refactor-loop", result.stdout)
        self.assertNotIn("not-source-repo", result.stdout)

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

    def test_static_checker_does_not_require_reference_md(self) -> None:
        with copy_minimal_repo() as tmp:
            repo = Path(tmp) / "repo"
            reference = repo / "skills/codex-refactor-loop/REFERENCE.md"
            if reference.exists():
                reference.unlink()

            findings = self.checker_module.SkillDriftChecker(repo).run_static()

        self.assertFalse(any(f.path.endswith("REFERENCE.md") for f in findings), [f.as_dict() for f in findings])

    def test_checker_detects_missing_source_repo_validation_contract(self) -> None:
        with copy_minimal_repo() as tmp:
            repo = Path(tmp) / "repo"
            skill = repo / "skills/codex-refactor-loop/SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8").replace(
                    "## Skill degradation source-repo validation",
                    "## Removed heading",
                ),
                encoding="utf-8",
            )

            findings = self.checker_module.SkillDriftChecker(repo).run_static()

        self.assertTrue(any(f.check == "skill-named-exception" for f in findings))
        self.assertTrue(any("source-repo validation" in f.message for f in findings))

    def test_checker_detects_missing_single_file_reference_contract(self) -> None:
        with copy_minimal_repo() as tmp:
            repo = Path(tmp) / "repo"
            skill = repo / "skills/codex-refactor-loop/SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8")
                .replace("single controller contract and detailed reference", "split reference contract")
                .replace("## Detailed reference", "## Removed detailed reference"),
                encoding="utf-8",
            )

            findings = self.checker_module.SkillDriftChecker(repo).run_static()

        self.assertTrue(any(f.check == "reference-contract" and "single controller contract" in f.message for f in findings))
        self.assertTrue(any(f.check == "reference-contract" and "## Detailed reference" in f.message for f in findings))

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

    def test_checker_detects_downstream_runtime_watch_marker_drift(self) -> None:
        with copy_minimal_repo() as tmp:
            repo = Path(tmp) / "repo"
            monitor = repo / "skills/codex-refactor-loop/scripts/codex_refactor_loop/monitors/concurrency.py"
            monitor.write_text(monitor.read_text(encoding="utf-8") + "\nrun_skill_degradation_check()\n", encoding="utf-8")

            findings = self.checker_module.SkillDriftChecker(repo).run_static()

        self.assertTrue(any(f.check == "downstream-runtime-surface" and "run_skill_degradation_check" in f.message for f in findings))

    def test_checker_allows_forbidden_terms_only_in_denial_context(self) -> None:
        with copy_minimal_repo() as tmp:
            repo = Path(tmp) / "repo"
            skill = repo / "skills/codex-refactor-loop/SKILL.md"
            skill.write_text(skill.read_text(encoding="utf-8") + "\nRuntime creates DegradationCheck objects.\n", encoding="utf-8")

            findings = self.checker_module.SkillDriftChecker(repo).run_static()

        self.assertTrue(any(f.check == "forbidden-surface" and "DegradationCheck" in f.message for f in findings))

    def test_checker_detects_skill_degradation_check_surface(self) -> None:
        with copy_minimal_repo() as tmp:
            repo = Path(tmp) / "repo"
            skill = repo / "skills/codex-refactor-loop/SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8") + "\nRuntime creates SkillDegradationCheck objects.\n",
                encoding="utf-8",
            )

            findings = self.checker_module.SkillDriftChecker(repo).run_static()

        self.assertTrue(any(f.check == "forbidden-surface" and "SkillDegradation" in f.message for f in findings))

    def test_checker_detects_work_unit_replacement_surface(self) -> None:
        with copy_minimal_repo() as tmp:
            repo = Path(tmp) / "repo"
            skill = repo / "skills/codex-refactor-loop/SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8") + "\nRuntime creates WorkUnitReplacement objects.\n",
                encoding="utf-8",
            )

            findings = self.checker_module.SkillDriftChecker(repo).run_static()

        self.assertTrue(any(f.check == "forbidden-surface" and "WorkUnit" in f.message for f in findings))


if __name__ == "__main__":
    unittest.main()
