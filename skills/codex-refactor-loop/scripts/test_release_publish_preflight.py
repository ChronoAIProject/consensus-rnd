#!/usr/bin/env python3
"""Behavior tests for controller release publish preflight."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]
NOW = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
sys.path.insert(0, str(SCRIPT_PATH.parent))

from codex_refactor_loop.release.gate import isoformat
from codex_refactor_loop.release.publish_preflight import ReleasePublishPreflight, canonical_digest


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def copy_repo_fixture() -> tempfile.TemporaryDirectory[str]:
    tmp = tempfile.TemporaryDirectory()
    repo = Path(tmp.name) / "repo"
    for relative in (
        ".version-bump.json",
        "package.json",
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        ".codex-plugin/plugin.json",
        ".cursor-plugin/plugin.json",
        "gemini-extension.json",
    ):
        source = REPO_ROOT / relative
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return tmp


def set_mapped_version(repo: Path, version: str) -> None:
    mapping = read_json(repo / ".version-bump.json")
    assert isinstance(mapping, dict)
    for item in mapping["files"]:
        path = repo / item["path"]
        data = read_json(path)
        current = data
        parts = item["field"].split(".")
        for part in parts[:-1]:
            current = current[int(part)] if isinstance(current, list) else current[part]
        last = parts[-1]
        if isinstance(current, list):
            current[int(last)] = version
        else:
            current[last] = version
        write_json(path, data)


def write_host_opt_in(repo: Path, enabled: bool = True) -> None:
    (repo / ".refactor-loop").mkdir(parents=True, exist_ok=True)
    (repo / ".refactor-loop/host.env").write_text(
        f"export RELEASE_AUTO_ENABLE={'true' if enabled else 'false'}\n",
        encoding="utf-8",
    )


def green_required_signals() -> dict[str, object]:
    return {
        "required_checks_recent_green": {
            "passed": True,
            "branches": {
                "dev": {
                    "contract-tests": True,
                    "manifest-version-sync": True,
                    "skill-degradation": True,
                },
                "auto-refact-dev": {
                    "contract-tests": True,
                    "manifest-version-sync": True,
                    "skill-degradation": True,
                },
            },
        },
        "no_open_blocked_pr": {"passed": True},
        "no_human_decision_label": {"passed": True},
        "no_phase8_reject_churn": {"passed": True},
        "p0_alert_streak_ok": {"passed": True},
        "recent_pr_merges_min": {"passed": True},
        "fresh_heartbeats": {"passed": True},
        "no_unresolved_human_escalation": {"passed": True},
    }


def write_ready_artifacts(
    repo: Path,
    *,
    version: str = "2.0.0",
    target_ref: str = "abc123",
    generated_at: datetime = NOW,
    expires_at: datetime | None = None,
    signals: dict[str, object] | None = None,
) -> None:
    set_mapped_version(repo, version)
    decision = {
        "from_version": "1.9.9",
        "to_version": version,
        "bump_type": "patch",
        "commits": [{"sha": "abc", "subject": "fix: release"}],
        "decided_at": isoformat(generated_at),
        "stability_score": 100,
        "signals": signals or green_required_signals(),
        "ready": True,
        "blocked_reasons": [],
        "release_interval": {"passed": True},
    }
    candidate = {
        "schema": "decision-artifact-only/v2",
        "generated_at": isoformat(generated_at),
        "expires_at": isoformat(expires_at or (generated_at + timedelta(minutes=120))),
        "decision_artifact": ".refactor-loop/state/release-decision.json",
        "from_version": decision["from_version"],
        "to_version": version,
        "bump_type": "patch",
        "ready": True,
        "target_ref": target_ref,
        "required_signals": decision["signals"],
        "decision_digest": canonical_digest(decision),
        "publish_preflight": "controller-release-publish-preflight",
        "host_opt_in": "RELEASE_AUTO_ENABLE=true",
        "lifecycle_owner": "controller",
    }
    write_json(repo / ".refactor-loop/state/release-decision.json", decision)
    write_json(repo / ".refactor-loop/state/release-candidate.json", candidate)


class ReleasePublishPreflightTests(unittest.TestCase):
    def test_missing_candidate_or_decision_fails_closed(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_host_opt_in(repo)
            preflight = ReleasePublishPreflight(repo, now=lambda: NOW)

            missing_candidate = preflight.validate(target_ref="abc123")
            self.assertFalse(missing_candidate.allowed)
            self.assertIn("missing_candidate", missing_candidate.reasons)
            self.assertIn("missing_decision", missing_candidate.reasons)

            write_ready_artifacts(repo)
            (repo / ".refactor-loop/state/release-decision.json").unlink()
            missing_decision = preflight.validate(target_ref="abc123")
            self.assertFalse(missing_decision.allowed)
            self.assertIn("missing_decision", missing_decision.reasons)

    def test_host_opt_in_missing_or_false_fails_closed(self) -> None:
        for enabled in (None, False):
            with self.subTest(enabled=enabled), copy_repo_fixture() as tmp:
                repo = Path(tmp) / "repo"
                if enabled is not None:
                    write_host_opt_in(repo, enabled=enabled)
                write_ready_artifacts(repo)

                result = ReleasePublishPreflight(repo, now=lambda: NOW).validate(target_ref="abc123")

                self.assertFalse(result.allowed)
                self.assertIn("host_opt_in_not_true", result.reasons)

    def test_ready_candidate_allows_matching_ref_version_and_green_checks(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_host_opt_in(repo)
            write_ready_artifacts(repo, version="2.0.0", target_ref="abc123")

            result = ReleasePublishPreflight(repo, now=lambda: NOW).validate(target_ref="abc123")

            self.assertTrue(result.allowed, result.reasons)
            self.assertEqual(result.version, "2.0.0")
            self.assertEqual(result.target_ref, "abc123")

    def test_candidate_path_must_be_repo_relative_artifact(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_host_opt_in(repo)
            write_ready_artifacts(repo)
            outside = repo.parent / "release-candidate.json"
            shutil.copy2(repo / ".refactor-loop/state/release-candidate.json", outside)

            absolute = ReleasePublishPreflight(repo, now=lambda: NOW).validate(
                candidate_path=outside,
                target_ref="abc123",
            )
            parent_traversal = ReleasePublishPreflight(repo, now=lambda: NOW).validate(
                candidate_path="../release-candidate.json",
                target_ref="abc123",
            )

            self.assertFalse(absolute.allowed)
            self.assertIn("candidate_path_absolute", absolute.reasons)
            self.assertIn("missing_candidate", absolute.reasons)
            self.assertFalse(parent_traversal.allowed)
            self.assertIn("candidate_path_outside_repo", parent_traversal.reasons)
            self.assertIn("missing_candidate", parent_traversal.reasons)

    def test_old_candidate_schema_invalid_for_publish(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_host_opt_in(repo)
            write_ready_artifacts(repo)
            candidate_path = repo / ".refactor-loop/state/release-candidate.json"
            candidate = read_json(candidate_path)
            assert isinstance(candidate, dict)
            candidate["schema"] = "decision-artifact-only/v1"
            for field in ("target_ref", "expires_at", "required_signals", "decision_digest"):
                candidate.pop(field, None)
            write_json(candidate_path, candidate)

            result = ReleasePublishPreflight(repo, now=lambda: NOW).validate(target_ref="abc123")

            self.assertFalse(result.allowed)
            self.assertIn("old_candidate_schema", result.reasons)
            self.assertTrue(any(reason.startswith("missing_candidate_fields:") for reason in result.reasons))

    def test_stale_candidate_fails_closed(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_host_opt_in(repo)
            write_ready_artifacts(
                repo,
                generated_at=NOW - timedelta(minutes=121),
                expires_at=NOW - timedelta(minutes=1),
            )

            result = ReleasePublishPreflight(repo, now=lambda: NOW).validate(target_ref="abc123")

            self.assertFalse(result.allowed)
            self.assertIn("candidate_stale", result.reasons)
            self.assertIn("candidate_expired", result.reasons)

    def test_manifest_version_mismatch_fails_closed(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_host_opt_in(repo)
            write_ready_artifacts(repo, version="2.0.0")
            set_mapped_version(repo, "1.9.9")

            result = ReleasePublishPreflight(repo, now=lambda: NOW).validate(target_ref="abc123")

            self.assertFalse(result.allowed)
            self.assertIn("manifest_version_mismatch", result.reasons)

    def test_target_ref_mismatch_fails_closed(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_host_opt_in(repo)
            write_ready_artifacts(repo, target_ref="abc123")

            result = ReleasePublishPreflight(repo, now=lambda: NOW).validate(target_ref="def456")

            self.assertFalse(result.allowed)
            self.assertIn("target_ref_mismatch", result.reasons)

    def test_red_decision_signal_or_required_check_fails_closed(self) -> None:
        signals = green_required_signals()
        assert isinstance(signals["required_checks_recent_green"], dict)
        signals["required_checks_recent_green"]["branches"]["dev"]["contract-tests"] = False
        signals["fresh_heartbeats"] = {"passed": False, "reason": "heartbeat_stale"}
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_host_opt_in(repo)
            write_ready_artifacts(repo, signals=signals)

            result = ReleasePublishPreflight(repo, now=lambda: NOW).validate(target_ref="abc123")

            self.assertFalse(result.allowed)
            self.assertIn("required_signal_red:fresh_heartbeats", result.reasons)
            self.assertIn("required_check_red:contract-tests", result.reasons)
            self.assertIn("decision_signal_red:fresh_heartbeats", result.reasons)


if __name__ == "__main__":
    unittest.main()
