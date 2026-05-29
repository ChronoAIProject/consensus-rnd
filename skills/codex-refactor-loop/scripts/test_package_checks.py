#!/usr/bin/env python3
"""Behavior tests for packaged read-only checks."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]
DEGRADATION_MODULE = REPO_ROOT / "skills/codex-refactor-loop/scripts/codex_refactor_loop/checks/degradation.py"
MANIFEST_MODULE = REPO_ROOT / "skills/codex-refactor-loop/scripts/codex_refactor_loop/checks/manifest.py"

sys.path.insert(0, str(SCRIPT_PATH.parent))

from codex_refactor_loop.checks import degradation, manifest
from codex_refactor_loop.context import LoopContext

EXPECTED_VERSION_RECORDS = {
    ("package.json", "version"),
    (".claude-plugin/plugin.json", "version"),
    (".claude-plugin/marketplace.json", "plugins.0.version"),
    (".codex-plugin/plugin.json", "version"),
    (".cursor-plugin/plugin.json", "version"),
    ("gemini-extension.json", "version"),
}


def write_json(root: Path, relative_path: str, value: object) -> None:
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def copy_minimal_degradation_repo() -> tempfile.TemporaryDirectory[str]:
    tmp = tempfile.TemporaryDirectory()
    repo = Path(tmp.name) / "repo"
    paths = [
        ".github/workflows/consensus-rnd-ci.yml",
        ".github/workflows/release.yml",
        "skills/codex-refactor-loop/SKILL.md",
        "skills/codex-refactor-loop/host.env.example",
        "skills/codex-refactor-loop/scripts/consensus-rnd-cli",
        "skills/codex-refactor-loop/scripts/codex_refactor_loop/release/gate.py",
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


def write_manifest_fixture(root: Path, *, gemini_version: str = "1.2.3", include_gemini_field: bool = True) -> None:
    files = [
        {"path": "package.json", "field": "version"},
        {"path": ".claude-plugin/plugin.json", "field": "version"},
        {"path": ".claude-plugin/marketplace.json", "field": "plugins.0.version"},
        {"path": ".codex-plugin/plugin.json", "field": "version"},
        {"path": ".cursor-plugin/plugin.json", "field": "version"},
        {"path": "gemini-extension.json", "field": "version"},
    ]
    write_json(root, ".version-bump.json", {"files": files})
    write_json(root, "package.json", {"version": "1.2.3"})
    write_json(root, ".claude-plugin/plugin.json", {"version": "1.2.3"})
    write_json(root, ".claude-plugin/marketplace.json", {"plugins": [{"version": "1.2.3"}]})
    write_json(root, ".codex-plugin/plugin.json", {"version": "1.2.3"})
    write_json(root, ".cursor-plugin/plugin.json", {"version": "1.2.3"})
    gemini_manifest = {"version": gemini_version} if include_gemini_field else {"name": "consensus-rnd"}
    write_json(root, "gemini-extension.json", gemini_manifest)


class PackageChecksTests(unittest.TestCase):
    def test_degradation_static_checker_passes_current_repo_via_loop_context(self) -> None:
        ctx = LoopContext.load(repo_root=REPO_ROOT, read_only=True)

        findings = degradation.run_static_check(ctx=ctx)

        self.assertEqual(findings, [])
        self.assertEqual(degradation.format_findings(findings), "skill-degradation: ok")

    def test_degradation_checker_detects_required_marker_and_forbidden_surface_drift(self) -> None:
        with copy_minimal_degradation_repo() as tmp:
            repo = Path(tmp) / "repo"
            skill = repo / "skills/codex-refactor-loop/SKILL.md"
            skill.write_text(
                skill.read_text(encoding="utf-8").replace(
                    "## Named runtime exception — skill degradation watch(per #66)",
                    "## Removed heading",
                )
                + "\nRuntime creates DegradationCheck objects.\n",
                encoding="utf-8",
            )

            findings = degradation.SkillDriftChecker(repo).run_static()

        self.assertTrue(any(f.check == "skill-named-exception" for f in findings))
        self.assertTrue(any(f.check == "forbidden-surface" and "DegradationCheck" in f.message for f in findings))

    def test_degradation_source_preserves_issue66_contract_literals_and_forbidden_boundaries(self) -> None:
        source = DEGRADATION_MODULE.read_text(encoding="utf-8")
        evaluated_markers = "\n".join(
            [
                *degradation.REQUIRED_SKILL_MARKERS,
                *degradation.REQUIRED_DETAILED_REFERENCE_MARKERS,
                *degradation.REQUIRED_HOST_ENV_MARKERS,
                *degradation.REQUIRED_MONITOR_MARKERS,
                *degradation.REQUIRED_CI_MARKERS,
                *degradation.REQUIRED_RELEASE_MARKERS,
                *degradation.REQUIRED_RELEASE_PROJECTION_MARKERS,
                *degradation.REQUIRED_RELEASE_GATE_MARKERS,
            ]
        )
        for required in (
            "skill-degradation",
            "manifest-version-sync",
            ".refactor-loop/runs/phase9-issue66-r8-judge.md",
            ".refactor-loop/.degradation-alert.log",
            ".refactor-loop/.controller-pending-events.log",
            "DEGRADATION_WATCH_INTERVAL_SECONDS",
            "consensus-rnd-cli check-degradation --static",
            "source mutation",
            "git reset/rebase/merge/push",
            "GitHub issue/PR/body/label lifecycle mutation",
            "codex dispatch",
            "standalone daemon creation",
            "WorkUnit/schema/envelope changes",
            "auto-" + "clean root garbage",
            "auto-" + "fix API",
        ):
            with self.subTest(required=required):
                self.assertIn(required, evaluated_markers)

        self.assertIn("Old: scripts/check_skill_degradation.py", source)
        self.assertIn("New: expose the same read-only checks", source)

        for forbidden in (
            "subprocess.run(",
            "os.system",
            "gh pr create",
            "gh pr merge",
            "git push",
            "git commit",
            "dispatch_queue(",
            "Popen(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_manifest_records_match_version_bump_and_accept_loop_context(self) -> None:
        ctx = LoopContext.load(repo_root=REPO_ROOT, read_only=True)

        records = manifest.check_manifest_version_sync(ctx=ctx)

        self.assertEqual({(record.path, record.field) for record in records}, EXPECTED_VERSION_RECORDS)
        self.assertEqual(len({record.value for record in records}), 1)
        self.assertIn("MANIFEST_VERSION_SYNC_OK:", manifest.format_success(records))

    def test_manifest_mismatch_and_missing_field_fail_closed_with_legacy_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_manifest_fixture(repo, gemini_version="9.9.9")

            with self.assertRaisesRegex(
                manifest.ManifestVersionSyncError,
                r"gemini-extension\.json:version=9\.9\.9",
            ):
                manifest.check_manifest_version_sync(repo)

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            write_manifest_fixture(repo, include_gemini_field=False)

            with self.assertRaisesRegex(
                manifest.ManifestVersionSyncError,
                r"gemini-extension\.json:version: missing field segment 'version'",
            ):
                manifest.check_manifest_version_sync(repo)

    def test_manifest_source_has_no_lifecycle_or_mutation_authority(self) -> None:
        source = MANIFEST_MODULE.read_text(encoding="utf-8")
        self.assertIn("MANIFEST_VERSION_SYNC_OK", source)
        for forbidden in (
            "write_text",
            "os.replace",
            "subprocess",
            "gh pr",
            "git push",
            "git commit",
            "dispatch_queue(",
            "pending_events(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
