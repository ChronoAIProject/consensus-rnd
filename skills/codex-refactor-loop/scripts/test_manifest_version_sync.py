#!/usr/bin/env python3
"""Behavior tests for check_manifest_version_sync.py."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from check_manifest_version_sync import (
    ManifestVersionSyncError,
    check_manifest_version_sync,
    _load_json,
    load_manifest_records,
)

SCRIPT_PATH = Path(__file__).with_name("check_manifest_version_sync.py")
REPO_ROOT = SCRIPT_PATH.parents[3]

EXPECTED_VERSION_RECORDS = {
    ("package.json", "version"),
    (".claude-plugin/plugin.json", "version"),
    (".claude-plugin/marketplace.json", "plugins.0.version"),
    (".codex-plugin/plugin.json", "version"),
    (".cursor-plugin/plugin.json", "version"),
    ("gemini-extension.json", "version"),
}


class ManifestVersionSyncTests(unittest.TestCase):
    def write_json(self, root: Path, relative_path: str, value: object) -> None:
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def write_fixture(self, root: Path, *, gemini_version: str = "1.2.3", include_gemini_field: bool = True) -> None:
        files = [
            {"path": "package.json", "field": "version"},
            {"path": ".claude-plugin/plugin.json", "field": "version"},
            {"path": ".claude-plugin/marketplace.json", "field": "plugins.0.version"},
            {"path": ".codex-plugin/plugin.json", "field": "version"},
            {"path": ".cursor-plugin/plugin.json", "field": "version"},
            {"path": "gemini-extension.json", "field": "version"},
        ]
        self.write_json(root, ".version-bump.json", {"files": files})
        self.write_json(root, "package.json", {"version": "1.2.3"})
        self.write_json(root, ".claude-plugin/plugin.json", {"version": "1.2.3"})
        self.write_json(root, ".claude-plugin/marketplace.json", {"plugins": [{"version": "1.2.3"}]})
        self.write_json(root, ".codex-plugin/plugin.json", {"version": "1.2.3"})
        self.write_json(root, ".cursor-plugin/plugin.json", {"version": "1.2.3"})
        gemini_manifest = {"version": gemini_version} if include_gemini_field else {"name": "consensus-rnd"}
        self.write_json(root, "gemini-extension.json", gemini_manifest)

    def write_single_record_fixture(
        self,
        root: Path,
        *,
        manifest: object,
        path: str = "manifest.json",
        field: str = "version",
    ) -> None:
        self.write_json(root, ".version-bump.json", {"files": [{"path": path, "field": field}]})
        self.write_json(root, path, manifest)

    def test_real_repo_version_bump_entries_all_resolve_to_one_version(self) -> None:
        records = load_manifest_records(REPO_ROOT)

        self.assertEqual({(record.path, record.field) for record in records}, EXPECTED_VERSION_RECORDS)
        self.assertEqual(len({record.value for record in records}), 1)

    def test_mismatched_temp_fixture_reports_path_and_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.write_fixture(repo, gemini_version="9.9.9")

            with self.assertRaisesRegex(
                ManifestVersionSyncError,
                r"gemini-extension\.json:version=9\.9\.9",
            ):
                check_manifest_version_sync(repo)

    def test_missing_field_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.write_fixture(repo, include_gemini_field=False)

            with self.assertRaisesRegex(
                ManifestVersionSyncError,
                r"gemini-extension\.json:version: missing field segment 'version'",
            ):
                check_manifest_version_sync(repo)

    def test_load_json_fails_closed_for_missing_file_and_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)

            with self.assertRaisesRegex(ManifestVersionSyncError, "missing file:"):
                _load_json(repo / "missing.json")

            invalid = repo / "invalid.json"
            invalid.write_text("{not valid json\n", encoding="utf-8")
            with self.assertRaisesRegex(ManifestVersionSyncError, "invalid JSON in"):
                _load_json(invalid)

    def test_version_bump_schema_fails_closed(self) -> None:
        cases = [
            ("missing files", {}, "expected top-level files list"),
            ("non-list files", {"files": {"path": "manifest.json", "field": "version"}}, "expected top-level files list"),
            ("non-object entry", {"files": ["manifest.json"]}, "files.0 must be an object"),
            ("missing path", {"files": [{"field": "version"}]}, "files.0.path must be a non-empty string"),
            ("empty path", {"files": [{"path": "", "field": "version"}]}, "files.0.path must be a non-empty string"),
            ("non-string path", {"files": [{"path": 7, "field": "version"}]}, "files.0.path must be a non-empty string"),
            ("missing field", {"files": [{"path": "manifest.json"}]}, "files.0.field must be a non-empty string"),
            ("empty field", {"files": [{"path": "manifest.json", "field": ""}]}, "files.0.field must be a non-empty string"),
            ("non-string field", {"files": [{"path": "manifest.json", "field": 7}]}, "files.0.field must be a non-empty string"),
        ]
        for label, version_bump, message in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                self.write_json(repo, ".version-bump.json", version_bump)

                with self.assertRaisesRegex(ManifestVersionSyncError, message):
                    load_manifest_records(repo)

    def test_dotted_field_resolution_fails_closed(self) -> None:
        cases = [
            (
                "non-numeric list index",
                {"plugins": [{"version": "1.2.3"}]},
                "plugins.zero.version",
                "expected numeric list index",
            ),
            (
                "out-of-range list index",
                {"plugins": [{"version": "1.2.3"}]},
                "plugins.1.version",
                "list index 1 out of range",
            ),
            (
                "scalar traversal",
                {"version": "1.2.3"},
                "version.major",
                "cannot resolve segment 'major' through str",
            ),
        ]
        for label, manifest, field, message in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                self.write_single_record_fixture(repo, manifest=manifest, field=field)

                with self.assertRaisesRegex(ManifestVersionSyncError, message):
                    load_manifest_records(repo)

    def test_version_values_must_be_non_empty_strings(self) -> None:
        cases = [
            ("empty string", {"version": ""}),
            ("null", {"version": None}),
            ("numeric", {"version": 123}),
        ]
        for label, manifest in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                self.write_single_record_fixture(repo, manifest=manifest)

                with self.assertRaisesRegex(
                    ManifestVersionSyncError,
                    "manifest.json:version: version value must be a non-empty string",
                ):
                    check_manifest_version_sync(repo)

    def test_cli_smoke_real_repo_succeeds(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MANIFEST_VERSION_SYNC_OK:", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_cli_mismatched_manifest_fixture_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for relative_path, _field in EXPECTED_VERSION_RECORDS:
                source = REPO_ROOT / relative_path
                target = repo / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            shutil.copyfile(REPO_ROOT / ".version-bump.json", repo / ".version-bump.json")

            gemini_path = repo / "gemini-extension.json"
            gemini_manifest = json.loads(gemini_path.read_text(encoding="utf-8"))
            gemini_manifest["version"] = "999.999.999"
            gemini_path.write_text(json.dumps(gemini_manifest, indent=2) + "\n", encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "--repo-root", str(repo)],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("MANIFEST_VERSION_SYNC_ERROR", result.stderr)
        self.assertIn("gemini-extension.json:version=999.999.999", result.stderr)
        self.assertNotIn("MANIFEST_VERSION_SYNC_OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
