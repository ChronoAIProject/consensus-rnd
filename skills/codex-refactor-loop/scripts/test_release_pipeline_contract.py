#!/usr/bin/env python3
"""Contract tests for the minimal skill release pipeline."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]
BUMP_PATH = REPO_ROOT / ".github/scripts/bump_version.py"
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/release.yml"
REQUIRED_CHECKS_PATH = REPO_ROOT / "skills/codex-refactor-loop/scripts/codex_refactor_loop/release/required_checks.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_bump_module():
    spec = importlib.util.spec_from_file_location("bump_version", BUMP_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def copy_release_pipeline_repo() -> tempfile.TemporaryDirectory[str]:
    tmp = tempfile.TemporaryDirectory()
    shutil.copytree(REPO_ROOT, Path(tmp.name) / "repo", ignore=shutil.ignore_patterns(".git"))
    return tmp


class ReleasePipelineContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bump = load_bump_module()
        self.workflow = read(WORKFLOW_PATH)
        self.required_checks = read(REQUIRED_CHECKS_PATH)

    def test_nested_manifest_resolution_and_semver_bumping(self) -> None:
        data = {"plugins": [{"version": "1.2.3"}]}
        self.assertEqual(self.bump.resolve_field(data, "plugins.0.version"), "1.2.3")
        self.assertEqual(self.bump.bump_semver("1.2.3", "patch"), "1.2.4")
        self.assertEqual(self.bump.bump_semver("1.2.3", "minor"), "1.3.0")
        self.assertEqual(self.bump.bump_semver("1.2.3", "major"), "2.0.0")

    def test_desync_refuses_before_write_and_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(REPO_ROOT, root / "repo", ignore=shutil.ignore_patterns(".git"))
            repo = root / "repo"
            pkg = repo / "package.json"
            original = read(pkg)
            data = json.loads(original)
            data["version"] = "9.9.9"
            pkg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            result = subprocess.run(
                ["python3", ".github/scripts/bump_version.py", "--version", "1.0.0"],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(read(pkg))["version"], "9.9.9")

            pkg.write_text(original, encoding="utf-8")
            before = read(pkg)
            result = subprocess.run(
                ["python3", ".github/scripts/bump_version.py", "--dry-run", "--level", "patch", "--read-version"],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(read(pkg), before)

            result = subprocess.run(
                ["python3", ".github/scripts/bump_version.py", "--version", "1.0.0", "--read-version"],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "1.0.0")
            self.assertEqual(json.loads(read(pkg))["version"], "1.0.0")

    def test_manifest_sync_write_covers_all_mapped_platform_manifests(self) -> None:
        paths = [
            ".version-bump.json",
            "package.json",
            ".claude-plugin/plugin.json",
            ".claude-plugin/marketplace.json",
            ".codex-plugin/plugin.json",
            ".cursor-plugin/plugin.json",
            "gemini-extension.json",
            ".github/scripts/bump_version.py",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for relative in paths:
                source = REPO_ROOT / relative
                target = repo / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

            result = subprocess.run(
                ["python3", ".github/scripts/bump_version.py", "--version", "1.0.0"],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            result = subprocess.run(
                ["python3", ".github/scripts/bump_version.py", "--read-version"],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "1.0.0")

            mapping = json.loads(read(repo / ".version-bump.json"))
            for item in mapping["files"]:
                with self.subTest(path=item["path"], field=item["field"]):
                    data = json.loads(read(repo / item["path"]))
                    self.assertEqual(self.bump.resolve_field(data, item["field"]), "1.0.0")

            marketplace = json.loads(read(repo / ".claude-plugin/marketplace.json"))
            self.assertEqual(marketplace["plugins"][0]["version"], "1.0.0")

    def test_level_and_version_cli_rejection_does_not_mutate_mapped_manifests(self) -> None:
        with copy_release_pipeline_repo() as tmp:
            repo = Path(tmp) / "repo"
            before = self.snapshot_mapped_manifest_versions(repo)
            result = subprocess.run(
                ["python3", ".github/scripts/bump_version.py", "--level", "patch", "--version", "1.0.0"],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--level and --version are mutually exclusive", result.stderr)
            self.assertEqual(self.snapshot_mapped_manifest_versions(repo), before)

    def test_invalid_exact_version_cli_rejection_does_not_mutate_mapped_manifests(self) -> None:
        for version in ("v1.0.0", "1.2"):
            with self.subTest(version=version), copy_release_pipeline_repo() as tmp:
                repo = Path(tmp) / "repo"
                before = self.snapshot_mapped_manifest_versions(repo)
                result = subprocess.run(
                    ["python3", ".github/scripts/bump_version.py", "--version", version],
                    cwd=repo,
                    text=True,
                    capture_output=True,
                    check=False,
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"invalid semver: {version}", result.stderr)
                self.assertEqual(self.snapshot_mapped_manifest_versions(repo), before)

    def test_workflow_contract(self) -> None:
        self.assertIn("name: release", self.workflow)
        self.assertNotIn("push:", self.workflow)
        self.assertIn("workflow_run:", self.workflow)
        self.assertIn("consensus-rnd-ci", self.workflow)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", self.workflow)
        self.assertIn("github.event.workflow_run.head_branch == 'dev'", self.workflow)
        self.assertIn("github.event.workflow_run.head_sha || github.sha", self.workflow)
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertNotIn("version:", self.workflow)
        self.assertNotIn("bump:", self.workflow)
        self.assertNotIn("workflow_call", self.workflow)
        self.assertNotIn("schedule:", self.workflow)
        self.assertNotIn("npm publish", self.workflow)
        self.assertNotIn("checks.listForRef", self.workflow)
        self.assertNotIn("const required", self.workflow)
        self.assertIn("release-required-checks", self.workflow)
        self.assertIn("GH_TOKEN", self.workflow)
        self.assertIn("bump_version.py --check --read-version", self.workflow)

    def test_release_guards_and_rejected_files(self) -> None:
        self.assertFalse((REPO_ROOT / ".github/scripts/manifest_version_map.py").exists())
        self.assertFalse((REPO_ROOT / ".github/scripts/release_preflight.py").exists())
        for needle in (
            "required release checks",
            "already exists; no-op",
            "is not newer than latest tag",
            "steps.mode.outputs.dry_run != 'true'",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.workflow)
        for needle in ("contract-tests", "manifest-version-sync", "skill-degradation", "REQUIRED_RELEASE_CHECKS"):
            with self.subTest(required_check_projection=needle):
                self.assertIn(needle, self.required_checks)

    def snapshot_mapped_manifest_versions(self, repo: Path) -> dict[tuple[str, str], str]:
        mapping = json.loads(read(repo / ".version-bump.json"))
        snapshot = {}
        for item in mapping["files"]:
            data = json.loads(read(repo / item["path"]))
            snapshot[(item["path"], item["field"])] = self.bump.resolve_field(data, item["field"])
        return snapshot


if __name__ == "__main__":
    unittest.main()
