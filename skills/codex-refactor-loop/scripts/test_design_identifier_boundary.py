#!/usr/bin/env python3
"""Source-regression guard for versioned design identifiers."""

from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]
VERSION_BUMP = REPO_ROOT / ".version-bump.json"
STATE_FILE = REPO_ROOT / ".refactor-loop" / "state.json"

TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".sh",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
}
EXCLUDED_PREFIXES = (
    ".git/",
    ".worktrees/",
    ".refactor-loop/runs/",
    ".refactor-loop/prompts/",
)
VERSIONED_IDENTIFIER_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*V[0-9]+\b")
STATE_SCHEMA_FIELD_RE = re.compile(r"\b(?:work_unit_)?" + "schema" + r"_version\b")


def git_tracked_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return [REPO_ROOT / line for line in result.stdout.splitlines() if line]


def repo_relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def version_bump_release_fields() -> set[tuple[str, str]]:
    data = json.loads(VERSION_BUMP.read_text(encoding="utf-8"))
    return {
        (entry["path"], entry["field"])
        for entry in data["files"]
    }


def version_bump_allowed_lines() -> set[tuple[str, int]]:
    allowed = {(repo_relative(VERSION_BUMP), index) for index, _line in enumerate(VERSION_BUMP.read_text(encoding="utf-8").splitlines(), 1)}
    for relative, field in version_bump_release_fields():
        path = REPO_ROOT / relative
        parts = field.split(".")
        leaf = parts[-1]
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if f'"{leaf}"' in line:
                allowed.add((relative, index))
    return allowed


def is_source_path(path: Path) -> bool:
    relative = repo_relative(path)
    if any(relative.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
        return False
    if path.is_symlink():
        return False
    return path.suffix in TEXT_SUFFIXES


def queue_container_names() -> set[str]:
    return {"clusters_planned", "clusters_active", "clusters_done", "clusters_failed"}


class DesignIdentifierBoundaryTests(unittest.TestCase):
    def test_tracked_source_has_no_versioned_design_identifiers(self) -> None:
        allowed_lines = version_bump_allowed_lines()
        offenders: list[str] = []
        for path in git_tracked_paths():
            if not is_source_path(path):
                continue
            relative = repo_relative(path)
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for index, line in enumerate(lines, 1):
                if (relative, index) in allowed_lines:
                    continue
                matches = [
                    *VERSIONED_IDENTIFIER_RE.findall(line),
                    *STATE_SCHEMA_FIELD_RE.findall(line),
                ]
                if matches:
                    offenders.append(f"{relative}:{index}: {', '.join(matches)}")

        self.assertEqual([], offenders)

    def test_release_semver_carveout_is_limited_to_version_bump_targets(self) -> None:
        fields = version_bump_release_fields()
        self.assertEqual(
            {
                ("package.json", "version"),
                (".claude-plugin/plugin.json", "version"),
                (".claude-plugin/marketplace.json", "plugins.0.version"),
                (".codex-plugin/plugin.json", "version"),
                (".cursor-plugin/plugin.json", "version"),
                ("gemini-extension.json", "version"),
            },
            fields,
        )

    def test_live_state_uses_shape_not_version_field(self) -> None:
        if not STATE_FILE.exists():
            self.skipTest("local controller state file is absent")

        data: Any = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)
        self.assertFalse(STATE_SCHEMA_FIELD_RE.search("\n".join(data.keys())))
        self.assertLessEqual(queue_container_names(), set(data))


if __name__ == "__main__":
    unittest.main()
