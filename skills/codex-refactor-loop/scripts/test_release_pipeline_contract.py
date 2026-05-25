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


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_bump_module():
    spec = importlib.util.spec_from_file_location("bump_version", BUMP_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ReleasePipelineContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bump = load_bump_module()
        self.workflow = read(WORKFLOW_PATH)

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

    def test_workflow_contract(self) -> None:
        self.assertIn("name: release", self.workflow)
        self.assertIn("push:", self.workflow)
        self.assertIn("branches:", self.workflow)
        self.assertIn("- dev", self.workflow)
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertNotIn("version:", self.workflow)
        self.assertNotIn("bump:", self.workflow)
        self.assertNotIn("workflow_call", self.workflow)
        self.assertNotIn("schedule:", self.workflow)
        self.assertNotIn("npm publish", self.workflow)
        self.assertIn("bump_version.py --check --read-version", self.workflow)

    def test_release_guards_and_rejected_files(self) -> None:
        self.assertFalse((REPO_ROOT / ".github/scripts/manifest_version_map.py").exists())
        self.assertFalse((REPO_ROOT / ".github/scripts/release_preflight.py").exists())
        for needle in (
            "contract-tests",
            "manifest-version-sync",
            "required check",
            "already exists; no-op",
            "is not newer than latest tag",
            "steps.mode.outputs.dry_run != 'true'",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.workflow)


if __name__ == "__main__":
    unittest.main()
