#!/usr/bin/env python3
"""Contract tests for the minimal skill release pipeline."""

from __future__ import annotations

import importlib.util
import json
import re
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
REQUIRED_CHECKS_PATH = REPO_ROOT / "skills/consensus-loop/scripts/codex_refactor_loop/release/required_checks.py"
RELEASE_GATE_PATH = REPO_ROOT / "skills/consensus-loop/scripts/codex_refactor_loop/release/gate.py"
PUBLISH_PREFLIGHT_PATH = REPO_ROOT / "skills/consensus-loop/scripts/codex_refactor_loop/release/publish_preflight.py"
PUBLISHER_PATH = REPO_ROOT / "skills/consensus-loop/scripts/codex_refactor_loop/release/publisher.py"


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
        self.release_gate = read(RELEASE_GATE_PATH)
        self.required_checks = read(REQUIRED_CHECKS_PATH)
        self.publish_preflight = read(PUBLISH_PREFLIGHT_PATH)
        self.publisher = read(PUBLISHER_PATH)

    def test_nested_manifest_resolution_and_semver_bumping(self) -> None:
        data = {"plugins": [{"version": "1.2.3"}]}
        self.assertEqual(self.bump.resolve_field(data, "plugins.0.version"), "1.2.3")
        self.assertEqual(self.bump.bump_semver("1.2.3", "patch"), "1.2.4")
        self.assertEqual(self.bump.bump_semver("1.2.3", "minor"), "1.3.0")
        self.assertEqual(self.bump.bump_semver("1.2.3", "major"), "2.0.0")
        self.assertEqual(self.bump.bump_semver("1.2.3-beta.1+build.5", "patch"), "1.2.4")

    def test_semver_parser_accepts_prerelease_and_build_metadata(self) -> None:
        for version in ("1.0.0", "1.0.0-beta.1", "1.0.0-rc.1+build.5"):
            with self.subTest(version=version):
                self.assertEqual(self.bump.parse_version(version), (1, 0, 0))

    def test_semver_parser_rejects_invalid_versions(self) -> None:
        for version in ("1.0", "1.0.0.0", "1.0.0-", "v1.0.0", ""):
            with self.subTest(version=version):
                with self.assertRaisesRegex(ValueError, f"invalid semver: {re.escape(version)}"):
                    self.bump.parse_version(version)

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
            "skills/consensus-loop/VERSION.json",
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
        executable_workflow = "\n".join(line for line in self.workflow.splitlines() if not line.lstrip().startswith("#"))
        self.assertIn("name: release", self.workflow)
        self.assertNotIn("push:", executable_workflow)
        self.assertNotIn("workflow_run:", executable_workflow)
        self.assertNotIn("contents: write", executable_workflow)
        self.assertNotIn("gh release create", executable_workflow)
        self.assertNotIn("git tag", executable_workflow)
        self.assertNotIn("steps.mode.outputs.dry_run != 'true'", executable_workflow)
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertIn("contents: read", self.workflow)
        self.assertIn("checks: read", self.workflow)
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
            "controller-only publication",
            "禁 gh release create",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.workflow)
        for needle in ("HOST_GITHUB_RELEASE_REQUIRED_CHECKS", "required_release_checks", "ReleaseRequiredChecksProjection"):
            with self.subTest(required_check_projection=needle):
                self.assertIn(needle, self.required_checks)
        for forbidden in ('"contract-tests"', '"manifest-version-sync"', '"skill-degradation"'):
            with self.subTest(forbidden_runtime_default=forbidden):
                self.assertNotIn(forbidden, self.required_checks)

    def test_release_gate_has_no_clean_room_internal_signal_or_artifact(self) -> None:
        expected_signals = (
            "required_checks_recent_green",
            "no_open_blocked_pr",
            "no_human_decision_label",
            "no_phase8_reject_churn",
            "p0_alert_streak_ok",
            "recent_pr_merges_min",
            "fresh_heartbeats",
            "no_unresolved_human_escalation",
        )
        for signal in expected_signals:
            with self.subTest(signal=signal):
                self.assertIn(f'"{signal}"', self.release_gate)
        signal_block = self.release_gate.split("SIGNAL_NAMES = (", 1)[1].split(")", 1)[0]
        self.assertEqual(signal_block.count('"'), len(expected_signals) * 2)
        for forbidden in ("clean_room", "clean-room", "host-fixture-smoke", "clean-room-", "cleanroom"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.release_gate)

    def test_release_publish_preflight_source_guards_candidate_artifact_path(self) -> None:
        for needle in (
            "path.is_absolute()",
            "candidate_path_absolute",
            '".." in candidate.parts',
            "candidate_path_outside_repo",
            "raw_candidate = None if candidate_path_error else read_json",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.publish_preflight)

    def test_release_publisher_source_gates_fresh_tag_target_before_release_create(self) -> None:
        for needle in (
            "ReleaseRequiredChecksProjection",
            "def _ensure_fresh_release_commit_checks_green",
            "GH_REPO_SLUG is required for fresh release commit check gate",
            "projection.check_ref(repo_slug, release_target_ref",
            "release_push_started_at = None if state.skip_bump_commit else self.now()",
            "since=release_push_started_at",
            "_ensure_fresh_release_commit_checks_green(release_target_ref, since=release_push_started_at)",
            "ReleaseCommitTransaction",
            "transaction.publish(",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.publisher)

        push_index = self.publisher.index("transaction.publish(")
        gate_index = self.publisher.index("self._ensure_fresh_release_commit_checks_green(release_target_ref")
        release_index = self.publisher.index('["gh", "release", "create"')
        result_write_index = self.publisher.index("write_json(self.result_path, payload)")
        self.assertLess(push_index, gate_index)
        self.assertLess(gate_index, release_index)
        self.assertLess(release_index, result_write_index)

    def test_release_publisher_reentry_contract_is_private_and_gated(self) -> None:
        for needle in (
            "ReleasePublicationState",
            'ReleasePublicationPhase = Literal["first_run", "already_bumped"]',
            '["git", "show", "-s", "--format=%s", "HEAD"]',
            'self._is_only_manifest_version_mismatch(result)',
            "manifest_versions != {version}",
            "self._current_commit_subject() != expected_subject",
            "skip_bump_commit=True",
            "already_bumped_reentry",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.publisher)
        self.assertNotIn('"ReleasePublicationState"', self.publisher.split("__all__", 1)[-1])
        self.assertNotIn("release-publish-state.json", self.publisher)
        self.assertNotIn("proof ticket", self.publisher.lower())

        inspect_index = self.publisher.index("state = self._inspect_publication_state(result)")
        bump_index = self.publisher.index("transaction.publish(")
        skip_index = self.publisher.index("if not state.skip_bump_commit:")
        gate_index = self.publisher.index("self._ensure_fresh_release_commit_checks_green(release_target_ref")
        release_index = self.publisher.index('["gh", "release", "create"')
        self.assertLess(inspect_index, skip_index)
        self.assertLess(skip_index, bump_index)
        self.assertLess(bump_index, gate_index)
        self.assertLess(gate_index, release_index)

    def test_release_publisher_remote_history_reentry_is_fixed_string_candidate_recall(self) -> None:
        for needle in (
            "def _remote_history_release_proof",
            "rollup_history_proof = self._remote_history_release_proof([proof.ref for proof in rollup_proofs], version)",
            'history_proof = self._remote_history_release_proof([f"origin/{branch}"], version)',
            '["git", "log", "--format=%H", "--fixed-strings", "--grep", self._release_bump_subject(version), remote_ref]',
            "HEX_SHA_RE.fullmatch(sha)",
            '["git", "show", "-s", "--format=%s", sha]',
            "len(candidates) != 1",
            "RemoteReleaseProof(ref=sha, sha=sha)",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.publisher)
        rollup_history_index = self.publisher.index("rollup_history_proof = self._remote_history_release_proof")
        history_index = self.publisher.index('history_proof = self._remote_history_release_proof([f"origin/{branch}"], version)')
        rollup_index = self.publisher.index("rollup_proofs = self._remote_rollup_release_proofs()")
        rollup_state_index = self.publisher.index("self._remote_already_bumped_state_from_proof(rollup_history_proof")
        state_index = self.publisher.index("self._remote_already_bumped_state_from_proof(history_proof")
        manifest_index = self.publisher.index("self._remote_mapped_manifest_versions(proof.ref)")
        self.assertLess(rollup_index, history_index)
        self.assertLess(rollup_index, rollup_history_index)
        self.assertLess(rollup_history_index, rollup_state_index)
        self.assertLess(rollup_state_index, history_index)
        self.assertLess(history_index, state_index)
        self.assertLess(state_index, manifest_index)
        self.assertNotIn("--max-count", self.publisher)
        self.assertNotIn('"git tag"', self.publisher)

    def snapshot_mapped_manifest_versions(self, repo: Path) -> dict[tuple[str, str], str]:
        mapping = json.loads(read(repo / ".version-bump.json"))
        snapshot = {}
        for item in mapping["files"]:
            data = json.loads(read(repo / item["path"]))
            snapshot[(item["path"], item["field"])] = self.bump.resolve_field(data, item["field"])
        return snapshot


if __name__ == "__main__":
    unittest.main()
