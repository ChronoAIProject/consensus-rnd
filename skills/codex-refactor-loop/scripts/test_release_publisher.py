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
        "skills/codex-refactor-loop/VERSION.json",
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
        self.failures: dict[tuple[str, ...], subprocess.CompletedProcess[str]] = {}
        self.rev_list_stdout = "0\n"
        self.head_sha = "bumpcommit456"

    def __call__(self, cmd: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        command = list(cmd)
        self.commands.append(command)
        failure = self.failures.get(tuple(command))
        if failure is not None:
            return failure
        if command[:3] == ["git", "rev-list", "--count"]:
            return subprocess.CompletedProcess(command, 0, stdout=self.rev_list_stdout, stderr="")
        if command == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout=f"{self.head_sha}\n", stderr="")
        if command[:3] == ["gh", "release", "create"]:
            return subprocess.CompletedProcess(command, 0, stdout="https://github.test/release/v2.0.0\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


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


def expected_success_commands() -> list[list[str]]:
    return [
        ["python3", ".github/scripts/bump_version.py", "--version", "2.0.0"],
        [
            "git",
            "add",
            ".version-bump.json",
            "package.json",
            ".claude-plugin/plugin.json",
            ".claude-plugin/marketplace.json",
            ".codex-plugin/plugin.json",
            ".cursor-plugin/plugin.json",
            "gemini-extension.json",
            "skills/codex-refactor-loop/VERSION.json",
        ],
        ["git", "commit", "-m", "Release v2.0.0"],
        ["git", "rev-parse", "HEAD"],
        ["git", "fetch", "origin", "HEAD"],
        ["git", "rev-list", "--count", "HEAD..origin/HEAD"],
        ["git", "push", "origin", "HEAD"],
        ["gh", "release", "create", "v2.0.0", "--target", "bumpcommit456", "--generate-notes"],
    ]


def expected_violating_pre_bump_tag_command() -> list[str]:
    return [
        ["gh", "release", "create", "v2.0.0", "--target", "abc123", "--generate-notes"],
    ][0]


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
            self.assertEqual(runner.commands, expected_success_commands())

    def test_publisher_records_result_and_uses_bump_commit_tag_target(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            preflight = FakePreflight(allowed_result(repo))
            runner = FakeRunner()
            publisher = ReleasePublisher(repo, preflight=preflight, runner=runner, now=lambda: NOW)

            result = publisher.publish(target_ref="abc123")

            self.assertTrue(result.published)
            self.assertEqual(result.tag, "v2.0.0")
            self.assertEqual(result.target_ref, "bumpcommit456")
            self.assertIn(["git", "commit", "-m", "Release v2.0.0"], runner.commands)
            self.assertIn(["git", "rev-parse", "HEAD"], runner.commands)
            self.assertIn(["git", "push", "origin", "HEAD"], runner.commands)
            self.assertEqual(
                runner.commands[-1],
                ["gh", "release", "create", "v2.0.0", "--target", "bumpcommit456", "--generate-notes"],
            )
            self.assertNotIn(expected_violating_pre_bump_tag_command(), runner.commands)
            payload = read_json(repo / ".refactor-loop/state/release-publish-result.json")
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertEqual(payload["tag"], "v2.0.0")
            self.assertEqual(payload["target_ref"], "bumpcommit456")
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

    def test_publisher_refuses_when_release_commit_is_behind_origin_head(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            runner = FakeRunner()
            runner.rev_list_stdout = "2\n"
            publisher = ReleasePublisher(repo, preflight=FakePreflight(allowed_result(repo)), runner=runner, now=lambda: NOW)

            with self.assertRaisesRegex(RuntimeError, "safe push refused"):
                publisher.publish(target_ref="abc123")

            self.assertEqual(
                runner.commands,
                expected_success_commands()[:6],
            )
            self.assertNotIn(["git", "push", "origin", "HEAD"], runner.commands)
            self.assertNotIn(expected_violating_pre_bump_tag_command(), runner.commands)
            self.assertFalse((repo / ".refactor-loop/state/release-publish-result.json").exists())

    def test_publisher_stops_on_command_failure_before_release_creation(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            runner = FakeRunner()
            failed_add = [
                "git",
                "add",
                ".version-bump.json",
                "package.json",
                ".claude-plugin/plugin.json",
                ".claude-plugin/marketplace.json",
                ".codex-plugin/plugin.json",
                ".cursor-plugin/plugin.json",
                "gemini-extension.json",
                "skills/codex-refactor-loop/VERSION.json",
            ]
            runner.failures[tuple(failed_add)] = subprocess.CompletedProcess(failed_add, 1, stdout="", stderr="add failed")
            publisher = ReleasePublisher(repo, preflight=FakePreflight(allowed_result(repo)), runner=runner, now=lambda: NOW)

            with self.assertRaisesRegex(RuntimeError, "add failed"):
                publisher.publish(target_ref="abc123")

            self.assertEqual(runner.commands, expected_success_commands()[:2])
            self.assertNotIn(expected_violating_pre_bump_tag_command(), runner.commands)
            self.assertFalse((repo / ".refactor-loop/state/release-publish-result.json").exists())


if __name__ == "__main__":
    unittest.main()
