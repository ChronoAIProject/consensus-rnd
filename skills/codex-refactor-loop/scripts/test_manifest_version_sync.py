#!/usr/bin/env python3
"""Behavior tests for check_manifest_version_sync.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from check_manifest_version_sync import (
    ManifestVersionSyncError,
    check_manifest_version_sync,
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


if __name__ == "__main__":
    unittest.main()
