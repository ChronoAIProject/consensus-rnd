#!/usr/bin/env python3
"""Behavior tests for controller release publisher."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]
NOW = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
sys.path.insert(0, str(SCRIPT_PATH.parent))

from codex_refactor_loop.release.publish_preflight import PublishPreflightResult
from codex_refactor_loop.release.publisher import ReleasePublisher


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


class FakePreflight:
    def __init__(self, result: PublishPreflightResult) -> None:
        self.result = result
        self.calls: list[tuple[str | Path, str]] = []

    def validate(self, *, candidate_path: str | Path, target_ref: str, manifest_version: str | None = None) -> PublishPreflightResult:
        self.calls.append((candidate_path, target_ref))
        return self.result


class FakeRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, cmd: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(cmd))
        if cmd[:3] == ["gh", "release", "create"]:
            return subprocess.CompletedProcess(list(cmd), 0, stdout="https://github.test/release/v2.0.0\n", stderr="")
        return subprocess.CompletedProcess(list(cmd), 0, stdout="", stderr="")


def allowed_result(repo: Path) -> PublishPreflightResult:
    candidate = {"to_version": "2.0.0", "target_ref": "abc123"}
    decision = {"to_version": "2.0.0", "ready": True}
    return PublishPreflightResult(
        allowed=True,
        reasons=(),
        candidate=candidate,
        decision=decision,
        candidate_path=repo / ".refactor-loop/state/release-candidate.json",
        decision_path=repo / ".refactor-loop/state/release-decision.json",
        target_ref="abc123",
        version="2.0.0",
        candidate_digest="digest123",
    )


class ReleasePublisherTests(unittest.TestCase):
    def test_publisher_runs_preflight_before_mutation(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            preflight = FakePreflight(allowed_result(repo))
            runner = FakeRunner()
            publisher = ReleasePublisher(repo, preflight=preflight, runner=runner, now=lambda: NOW)

            result = publisher.publish(target_ref="abc123")

            self.assertTrue(result.published)
            self.assertEqual(preflight.calls, [(".refactor-loop/state/release-candidate.json", "abc123")])
            self.assertEqual(runner.commands[0], ["python3", ".github/scripts/bump_version.py", "--version", "2.0.0"])

    def test_publisher_records_result_and_uses_exact_tag_target(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            preflight = FakePreflight(allowed_result(repo))
            runner = FakeRunner()
            publisher = ReleasePublisher(repo, preflight=preflight, runner=runner, now=lambda: NOW)

            result = publisher.publish(target_ref="abc123")

            self.assertTrue(result.published)
            self.assertEqual(result.tag, "v2.0.0")
            self.assertEqual(result.target_ref, "abc123")
            self.assertIn(["gh", "release", "create", "v2.0.0", "--target", "abc123", "--generate-notes"], runner.commands)
            payload = read_json(repo / ".refactor-loop/state/release-publish-result.json")
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertEqual(payload["tag"], "v2.0.0")
            self.assertEqual(payload["target_ref"], "abc123")
            self.assertEqual(payload["candidate_digest"], "digest123")
            self.assertEqual(payload["release_url"], "https://github.test/release/v2.0.0")

    def test_publisher_never_runs_when_preflight_denies(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            denied = PublishPreflightResult(
                allowed=False,
                reasons=("host_opt_in_not_true",),
                candidate={},
                decision={},
                candidate_path=repo / ".refactor-loop/state/release-candidate.json",
                decision_path=repo / ".refactor-loop/state/release-decision.json",
                target_ref="abc123",
                version="2.0.0",
                candidate_digest="",
            )
            runner = FakeRunner()
            publisher = ReleasePublisher(repo, preflight=FakePreflight(denied), runner=runner, now=lambda: NOW)

            result = publisher.publish(target_ref="abc123")

            self.assertFalse(result.published)
            self.assertEqual(result.reasons, ("host_opt_in_not_true",))
            self.assertEqual(runner.commands, [])
            self.assertFalse((repo / ".refactor-loop/state/release-publish-result.json").exists())


if __name__ == "__main__":
    unittest.main()
