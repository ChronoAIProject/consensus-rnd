#!/usr/bin/env python3
"""Behavior tests for controller release publisher."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from unittest import mock

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]
NOW = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)
sys.path.insert(0, str(SCRIPT_PATH.parent))

from codex_refactor_loop.release.gate import isoformat
from codex_refactor_loop.release.publish_preflight import PublishPreflightResult, canonical_digest
from codex_refactor_loop.release.publisher import ReleasePublisher
FIXTURE_RELEASE_CHECKS = ("contract-tests", "manifest-version-sync", "skill-degradation")


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
    (repo / ".refactor-loop").mkdir(parents=True, exist_ok=True)
    (repo / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
    (repo / ".config/consensus-rnd/host.env").write_text(
        'export RELEASE_AUTO_ENABLE=true\n'
        'export GH_REPO_SLUG="owner/repo"\n'
        'export HOST_GITHUB_RELEASE_REQUIRED_CHECKS="contract-tests,manifest-version-sync,skill-degradation"\n',
        encoding="utf-8",
    )
    return tmp


def release_env():
    return mock.patch.dict(
        os.environ,
        {
            "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env",
            "ACTIVE_CONTROLLER_DEVICE_ID": "device-a",
        },
        clear=False,
    )


class FakePreflight:
    def __init__(self, result: PublishPreflightResult) -> None:
        self.result = result
        self.calls: list[dict[str, str | Path | None]] = []

    def validate(self, *, candidate_path: str | Path, target_ref: str, manifest_version: str | None = None) -> PublishPreflightResult:
        self.calls.append(
            {
                "candidate_path": candidate_path,
                "target_ref": target_ref,
                "manifest_version": manifest_version,
            }
        )
        return self.result


class FakeRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.failures: dict[tuple[str, ...], subprocess.CompletedProcess[str]] = {}
        self.rev_list_stdout = "0\n"
        self.head_sha = "bumpcommit456"
        self.head_subject = "Release v2.0.0-beta.4"
        self.remote_branch = "auto-refact-dev"
        self.remote_sha = ""
        self.remote_subject = "Release v2.0.0-beta.4"
        self.remote_manifest_versions: dict[str, str] = {}
        self.check_status = "green"
        self.check_completed_at = "2026-05-30T12:01:00Z"

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
        if command == ["git", "rev-parse", f"origin/{self.remote_branch}"]:
            return subprocess.CompletedProcess(command, 0, stdout=f"{self.remote_sha}\n", stderr="")
        if command == ["git", "show", "-s", "--format=%s", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout=f"{self.head_subject}\n", stderr="")
        if command == ["git", "show", "-s", "--format=%s", f"origin/{self.remote_branch}"]:
            return subprocess.CompletedProcess(command, 0, stdout=f"{self.remote_subject}\n", stderr="")
        if command[:2] == ["git", "show"] and len(command) == 3 and command[2].startswith(f"origin/{self.remote_branch}:"):
            relative = command[2].split(":", 1)[1]
            return subprocess.CompletedProcess(command, 0, stdout=self._remote_manifest_payload(cwd, relative), stderr="")
        if command in (expected_check_runs_command(self.head_sha), expected_check_runs_command(self.remote_sha)):
            if self.check_status == "api_failure":
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="api failed")
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(self._check_runs_payload()), stderr="")
        if command[:3] == ["gh", "release", "create"]:
            return subprocess.CompletedProcess(command, 0, stdout="https://github.test/release/v2.0.0\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def _check_runs_payload(self) -> list[dict[str, object]]:
        check_runs = []
        for name in FIXTURE_RELEASE_CHECKS:
            status = "completed"
            conclusion = "success"
            if self.check_status == "pending":
                status = "in_progress"
                conclusion = ""
            elif self.check_status == "red":
                conclusion = "failure"
            check_runs.append(
                {
                    "name": name,
                    "status": status,
                    "conclusion": conclusion,
                    "completed_at": self.check_completed_at,
                }
            )
        if self.check_status == "missing":
            check_runs.pop()
        return [{"check_runs": check_runs}]

    def _remote_manifest_payload(self, cwd: Path, relative: str) -> str:
        data = read_json(cwd / relative)
        version = self.remote_manifest_versions.get(relative)
        if version is None:
            return json.dumps(data)
        mapping = read_json(cwd / ".version-bump.json")
        assert isinstance(mapping, dict)
        field = None
        for item in mapping["files"]:
            if item["path"] == relative:
                field = item["field"]
                break
        if field is None:
            return json.dumps(data)
        current = data
        parts = field.split(".")
        for part in parts[:-1]:
            current = current[int(part)] if isinstance(current, list) else current[part]
        last = parts[-1]
        if isinstance(current, list):
            current[int(last)] = version
        else:
            current[last] = version
        return json.dumps(data)


def allowed_result(repo: Path, version: str = "2.0.0-beta.4") -> PublishPreflightResult:
    candidate = {"to_version": version, "target_ref": "abc123"}
    decision = {"to_version": version, "ready": True}
    return PublishPreflightResult(
        allowed=True,
        reasons=(),
        candidate=candidate,
        decision=decision,
        candidate_path=repo / ".refactor-loop/state/release-candidate.json",
        decision_path=repo / ".refactor-loop/state/release-decision.json",
        target_ref="abc123",
        version=version,
        candidate_digest="digest123",
    )


def manifest_mismatch_result(repo: Path, version: str = "2.0.0-beta.4") -> PublishPreflightResult:
    result = allowed_result(repo, version=version)
    return PublishPreflightResult(
        allowed=False,
        reasons=("manifest_version_mismatch",),
        candidate=result.candidate,
        decision=result.decision,
        candidate_path=result.candidate_path,
        decision_path=result.decision_path,
        target_ref=result.target_ref,
        version=result.version,
        candidate_digest=result.candidate_digest,
    )


def mixed_manifest_mismatch_result(repo: Path, version: str = "2.0.0-beta.4") -> PublishPreflightResult:
    result = allowed_result(repo, version=version)
    return PublishPreflightResult(
        allowed=False,
        reasons=("manifest_version_mismatch", "host_opt_in_not_true"),
        candidate=result.candidate,
        decision=result.decision,
        candidate_path=result.candidate_path,
        decision_path=result.decision_path,
        target_ref=result.target_ref,
        version=result.version,
        candidate_digest=result.candidate_digest,
    )


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
    }


def write_ready_artifacts(
    repo: Path,
    *,
    from_version: str = "2.0.0-beta.3",
    version: str = "2.0.0-beta.4",
    target_ref: str = "abc123",
) -> None:
    set_mapped_version(repo, from_version)
    decision = {
        "from_version": from_version,
        "to_version": version,
        "bump_type": "patch",
        "signals": green_required_signals(),
        "ready": True,
    }
    candidate = {
        "schema": "decision-artifact-only/v2",
        "generated_at": isoformat(NOW),
        "expires_at": isoformat(NOW.replace(hour=13)),
        "decision_artifact": ".refactor-loop/state/release-decision.json",
        "from_version": from_version,
        "to_version": version,
        "bump_type": "patch",
        "ready": True,
        "target_ref": target_ref,
        "required_signals": decision["signals"],
        "decision_digest": canonical_digest(decision),
        "publish_preflight": "controller-release-publish-preflight",
        "lifecycle_owner": "controller",
    }
    write_json(repo / ".refactor-loop/state/release-decision.json", decision)
    write_json(repo / ".refactor-loop/state/release-candidate.json", candidate)


def expected_success_commands(version: str = "2.0.0-beta.4", prerelease: bool = True) -> list[list[str]]:
    release_command = ["gh", "release", "create", f"v{version}", "--target", "bumpcommit456", "--generate-notes"]
    if prerelease:
        release_command.append("--prerelease")
    return [
        ["python3", ".github/scripts/bump_version.py", "--version", version],
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
        ["git", "commit", "-m", f"Release v{version}"],
        ["git", "rev-parse", "HEAD"],
        ["git", "fetch", "origin", "HEAD"],
        ["git", "rev-list", "--count", "HEAD..origin/HEAD"],
        ["git", "push", "origin", "HEAD"],
        expected_check_runs_command("bumpcommit456"),
        release_command,
    ]


def expected_reentry_success_commands(version: str = "2.0.0-beta.4", prerelease: bool = True) -> list[list[str]]:
    release_command = ["gh", "release", "create", f"v{version}", "--target", "bumpcommit456", "--generate-notes"]
    if prerelease:
        release_command.append("--prerelease")
    return [
        ["git", "show", "-s", "--format=%s", "HEAD"],
        ["git", "rev-parse", "HEAD"],
        ["git", "fetch", "origin", "HEAD"],
        ["git", "rev-list", "--count", "HEAD..origin/HEAD"],
        ["git", "push", "origin", "HEAD"],
        expected_check_runs_command("bumpcommit456"),
        release_command,
    ]


def expected_remote_reentry_success_commands(version: str = "2.0.0-beta.4", remote_sha: str = "remotecommit789", prerelease: bool = True) -> list[list[str]]:
    release_command = ["gh", "release", "create", f"v{version}", "--target", remote_sha, "--generate-notes"]
    if prerelease:
        release_command.append("--prerelease")
    remote_ref = "origin/auto-refact-dev"
    return [
        ["git", "fetch", "origin", "auto-refact-dev"],
        ["git", "rev-parse", remote_ref],
        ["git", "show", "-s", "--format=%s", remote_ref],
        ["git", "show", f"{remote_ref}:package.json"],
        ["git", "show", f"{remote_ref}:.claude-plugin/plugin.json"],
        ["git", "show", f"{remote_ref}:.claude-plugin/marketplace.json"],
        ["git", "show", f"{remote_ref}:.codex-plugin/plugin.json"],
        ["git", "show", f"{remote_ref}:.cursor-plugin/plugin.json"],
        ["git", "show", f"{remote_ref}:gemini-extension.json"],
        ["git", "show", f"{remote_ref}:skills/codex-refactor-loop/VERSION.json"],
        expected_check_runs_command(remote_sha),
        release_command,
    ]


def expected_check_runs_command(sha: str) -> list[str]:
    return ["gh", "api", f"repos/owner/repo/commits/{sha}/check-runs", "--paginate", "--slurp"]


def expected_violating_pre_bump_tag_command() -> list[str]:
    return [
        ["gh", "release", "create", "v2.0.0-beta.4", "--target", "abc123", "--generate-notes", "--prerelease"],
    ][0]


class ReleasePublisherTests(unittest.TestCase):
    # Refactor (iter1/issue-322):
    #   Old pattern: ReleasePublisher had commit/push/gh-release authority only in SKILL prose.
    #   New principle: release-publication-322 mirrors exact commands and forbidden lifecycle surfaces.
    def test_publisher_runs_preflight_before_mutation(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            preflight = FakePreflight(allowed_result(repo))
            runner = FakeRunner()
            publisher = ReleasePublisher(repo, preflight=preflight, runner=runner, now=lambda: NOW)

            with release_env():
                result = publisher.publish(target_ref="abc123")

            self.assertTrue(result.published)
            self.assertEqual(
                preflight.calls,
                [
                    {
                        "candidate_path": ".refactor-loop/state/release-candidate.json",
                        "target_ref": "abc123",
                        "manifest_version": None,
                    }
                ],
            )
            self.assertEqual(runner.commands, expected_success_commands())

    def test_release_publication_command_allowlist_is_exact(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            runner = FakeRunner()
            publisher = ReleasePublisher(repo, preflight=FakePreflight(allowed_result(repo)), runner=runner, now=lambda: NOW)

            with release_env():
                result = publisher.publish(target_ref="abc123")

            self.assertTrue(result.published)
            self.assertEqual(runner.commands, expected_success_commands())

    def test_release_publication_forbids_extra_lifecycle_commands(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            runner = FakeRunner()
            publisher = ReleasePublisher(repo, preflight=FakePreflight(allowed_result(repo)), runner=runner, now=lambda: NOW)

            with release_env():
                publisher.publish(target_ref="abc123")

            serialized = [" ".join(command) for command in runner.commands]
            forbidden_exact = {
                "git tag",
                "git merge",
                "git rebase",
                "git reset",
                "gh release edit",
                "gh release delete",
                "gh release upload",
                "gh issue create",
                "gh issue close",
                "gh issue edit",
                "gh pr create",
                "gh pr close",
                "gh pr edit",
                "gh pr merge",
            }
            for forbidden in forbidden_exact:
                with self.subTest(forbidden=forbidden):
                    self.assertFalse(any(command.startswith(forbidden) for command in serialized), serialized)
            self.assertFalse(any(command[:2] == ["git", "push"] and "--force" in command for command in runner.commands))
            self.assertEqual(
                [command for command in runner.commands if command[:3] == ["gh", "release", "create"]],
                [expected_success_commands()[-1]],
            )

    def test_publisher_checks_fresh_release_commit_before_release_creation(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            runner = FakeRunner()
            publisher = ReleasePublisher(repo, preflight=FakePreflight(allowed_result(repo)), runner=runner, now=lambda: NOW)

            with release_env():
                result = publisher.publish(target_ref="abc123")

            self.assertTrue(result.published)
            check_index = runner.commands.index(expected_check_runs_command("bumpcommit456"))
            push_index = runner.commands.index(["git", "push", "origin", "HEAD"])
            release_index = runner.commands.index(expected_success_commands()[-1])
            self.assertLess(push_index, check_index)
            self.assertLess(check_index, release_index)

    def test_publisher_refuses_release_creation_when_fresh_commit_checks_are_not_green(self) -> None:
        for check_status, reason in (
            ("pending", "pending_required_checks"),
            ("red", "ci_red"),
            ("missing", "missing_required_checks_recent_green"),
            ("api_failure", "api_failure"),
        ):
            with self.subTest(check_status=check_status), copy_repo_fixture() as tmp:
                repo = Path(tmp) / "repo"
                runner = FakeRunner()
                runner.check_status = check_status
                publisher = ReleasePublisher(repo, preflight=FakePreflight(allowed_result(repo)), runner=runner, now=lambda: NOW)

                with release_env():
                    with self.assertRaisesRegex(RuntimeError, reason):
                        with release_env():
                            publisher.publish(target_ref="abc123")

                self.assertIn(["git", "push", "origin", "HEAD"], runner.commands)
                self.assertIn(expected_check_runs_command("bumpcommit456"), runner.commands)
                self.assertFalse(any(command[:3] == ["gh", "release", "create"] for command in runner.commands))
                self.assertFalse((repo / ".refactor-loop/state/release-publish-result.json").exists())

    def test_already_bumped_reentry_skips_bump_add_commit_and_creates_release_after_green(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            set_mapped_version(repo, "2.0.0-beta.4")
            runner = FakeRunner()
            publisher = ReleasePublisher(
                repo,
                preflight=FakePreflight(manifest_mismatch_result(repo)),
                runner=runner,
                now=lambda: NOW,
            )

            with release_env():
                result = publisher.publish(target_ref="abc123")

            self.assertTrue(result.published)
            self.assertEqual(result.target_ref, "bumpcommit456")
            self.assertEqual(runner.commands, expected_reentry_success_commands())
            self.assertFalse(any(command[:2] == ["python3", ".github/scripts/bump_version.py"] for command in runner.commands))
            self.assertFalse(any(command[:2] == ["git", "add"] for command in runner.commands))
            self.assertFalse(any(command[:2] == ["git", "commit"] for command in runner.commands))
            payload = read_json(repo / ".refactor-loop/state/release-publish-result.json")
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertEqual(payload["target_ref"], "bumpcommit456")

    def test_remote_already_bumped_reentry_skips_push_when_local_head_is_behind(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            runner = FakeRunner()
            runner.rev_list_stdout = "2\n"
            runner.remote_sha = "remotecommit789"
            runner.remote_manifest_versions = {
                "package.json": "2.0.0-beta.4",
                ".claude-plugin/plugin.json": "2.0.0-beta.4",
                ".claude-plugin/marketplace.json": "2.0.0-beta.4",
                ".codex-plugin/plugin.json": "2.0.0-beta.4",
                ".cursor-plugin/plugin.json": "2.0.0-beta.4",
                "gemini-extension.json": "2.0.0-beta.4",
                "skills/codex-refactor-loop/VERSION.json": "2.0.0-beta.4",
            }
            publisher = ReleasePublisher(repo, preflight=FakePreflight(allowed_result(repo)), runner=runner, now=lambda: NOW)

            with mock.patch.dict(os.environ, {"INTEGRATION_BRANCH": "auto-refact-dev"}, clear=False):
                with release_env():
                    result = publisher.publish(target_ref="abc123")

            self.assertTrue(result.published)
            self.assertEqual(result.target_ref, "remotecommit789")
            self.assertEqual(runner.commands, expected_remote_reentry_success_commands())
            self.assertFalse(any(command[:2] == ["python3", ".github/scripts/bump_version.py"] for command in runner.commands))
            self.assertFalse(any(command[:2] == ["git", "add"] for command in runner.commands))
            self.assertFalse(any(command[:2] == ["git", "commit"] for command in runner.commands))
            self.assertNotIn(["git", "push", "origin", "HEAD"], runner.commands)
            self.assertIn(expected_check_runs_command("remotecommit789"), runner.commands)
            payload = read_json(repo / ".refactor-loop/state/release-publish-result.json")
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertEqual(payload["target_ref"], "remotecommit789")

    def test_real_preflight_reentry_after_initial_push_failure_skips_bump_add_commit(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_ready_artifacts(repo, from_version="2.0.0-beta.3", version="2.0.0-beta.4")
            set_mapped_version(repo, "2.0.0-beta.4")
            runner = FakeRunner()
            runner.head_subject = "Release v2.0.0-beta.4"
            publisher = ReleasePublisher(repo, runner=runner, now=lambda: NOW)

            with release_env():
                result = publisher.publish(target_ref="abc123")

            self.assertTrue(result.published)
            self.assertEqual(result.target_ref, "bumpcommit456")
            self.assertEqual(runner.commands, expected_reentry_success_commands())
            self.assertFalse(any(command[:2] == ["python3", ".github/scripts/bump_version.py"] for command in runner.commands))
            self.assertFalse(any(command[:2] == ["git", "add"] for command in runner.commands))
            self.assertFalse(any(command[:2] == ["git", "commit"] for command in runner.commands))

    def test_already_bumped_reentry_accepts_exact_sha_green_checks_completed_before_rerun_now(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            set_mapped_version(repo, "2.0.0-beta.4")
            runner = FakeRunner()
            runner.check_completed_at = "2026-05-30T11:59:00Z"
            publisher = ReleasePublisher(
                repo,
                preflight=FakePreflight(manifest_mismatch_result(repo)),
                runner=runner,
                now=lambda: NOW,
            )

            with release_env():
                result = publisher.publish(target_ref="abc123")

            self.assertTrue(result.published)
            self.assertEqual(result.target_ref, "bumpcommit456")
            check_command = expected_check_runs_command("bumpcommit456")
            release_command = expected_reentry_success_commands()[-1]
            check_index = runner.commands.index(check_command)
            release_index = runner.commands.index(release_command)
            self.assertLess(check_index, release_index)
            self.assertEqual(release_command[4:6], ["--target", "bumpcommit456"])
            self.assertEqual(runner.commands, expected_reentry_success_commands())

    def test_first_run_still_rejects_checks_older_than_push_started_at(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            runner = FakeRunner()
            runner.check_completed_at = "2026-05-30T11:59:00Z"
            publisher = ReleasePublisher(repo, preflight=FakePreflight(allowed_result(repo)), runner=runner, now=lambda: NOW)

            with self.assertRaisesRegex(RuntimeError, "stale_required_checks"):
                with release_env():
                    publisher.publish(target_ref="abc123")

            self.assertIn(expected_check_runs_command("bumpcommit456"), runner.commands)
            self.assertFalse(any(command[:3] == ["gh", "release", "create"] for command in runner.commands))
            self.assertFalse((repo / ".refactor-loop/state/release-publish-result.json").exists())

    def test_already_bumped_reentry_denies_mixed_manifest_mismatch_reasons_without_commands(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            set_mapped_version(repo, "2.0.0-beta.4")
            runner = FakeRunner()
            runner.head_subject = "Release v2.0.0-beta.4"
            publisher = ReleasePublisher(
                repo,
                preflight=FakePreflight(mixed_manifest_mismatch_result(repo)),
                runner=runner,
                now=lambda: NOW,
            )

            with release_env():
                result = publisher.publish(target_ref="abc123")

            self.assertFalse(result.published)
            self.assertEqual(result.reasons, ("manifest_version_mismatch", "host_opt_in_not_true"))
            self.assertEqual(runner.commands, [])
            self.assertFalse((repo / ".refactor-loop/state/release-publish-result.json").exists())

    def test_already_bumped_reentry_pending_fails_closed_without_result_and_can_retry_green(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            set_mapped_version(repo, "2.0.0-beta.4")
            preflight = FakePreflight(manifest_mismatch_result(repo))
            pending_runner = FakeRunner()
            pending_runner.check_status = "pending"
            publisher = ReleasePublisher(repo, preflight=preflight, runner=pending_runner, now=lambda: NOW)

            with self.assertRaisesRegex(RuntimeError, "pending_required_checks"):
                with release_env():
                    publisher.publish(target_ref="abc123")

            self.assertFalse(any(command[:3] == ["gh", "release", "create"] for command in pending_runner.commands))
            self.assertFalse((repo / ".refactor-loop/state/release-publish-result.json").exists())

            green_runner = FakeRunner()
            retry = ReleasePublisher(repo, preflight=preflight, runner=green_runner, now=lambda: NOW)
            with release_env():
                result = retry.publish(target_ref="abc123")

            self.assertTrue(result.published)
            self.assertEqual(green_runner.commands, expected_reentry_success_commands())

    def test_already_bumped_reentry_red_checks_fail_closed_without_release(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            set_mapped_version(repo, "2.0.0-beta.4")
            runner = FakeRunner()
            runner.check_status = "red"
            publisher = ReleasePublisher(
                repo,
                preflight=FakePreflight(manifest_mismatch_result(repo)),
                runner=runner,
                now=lambda: NOW,
            )

            with self.assertRaisesRegex(RuntimeError, "ci_red"):
                with release_env():
                    publisher.publish(target_ref="abc123")

            self.assertIn(expected_check_runs_command("bumpcommit456"), runner.commands)
            self.assertFalse(any(command[:3] == ["gh", "release", "create"] for command in runner.commands))
            self.assertFalse((repo / ".refactor-loop/state/release-publish-result.json").exists())

    def test_manifest_mismatch_without_target_version_manifests_fails_closed(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            set_mapped_version(repo, "2.0.0-beta.3")
            runner = FakeRunner()
            publisher = ReleasePublisher(
                repo,
                preflight=FakePreflight(manifest_mismatch_result(repo, version="2.0.0-beta.4")),
                runner=runner,
                now=lambda: NOW,
            )

            with release_env():
                result = publisher.publish(target_ref="abc123")

            self.assertFalse(result.published)
            self.assertEqual(result.reasons, ("manifest_version_mismatch",))
            self.assertEqual(runner.commands, [])
            self.assertFalse((repo / ".refactor-loop/state/release-publish-result.json").exists())

    def test_target_version_manifests_without_release_head_subject_fail_closed(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            set_mapped_version(repo, "2.0.0-beta.4")
            runner = FakeRunner()
            runner.head_subject = "not a release commit"
            publisher = ReleasePublisher(
                repo,
                preflight=FakePreflight(manifest_mismatch_result(repo)),
                runner=runner,
                now=lambda: NOW,
            )

            with release_env():
                result = publisher.publish(target_ref="abc123")

            self.assertFalse(result.published)
            self.assertEqual(result.reasons, ("manifest_version_mismatch",))
            self.assertEqual(runner.commands, [["git", "show", "-s", "--format=%s", "HEAD"]])
            self.assertFalse(any(command[:3] == ["gh", "release", "create"] for command in runner.commands))
            self.assertFalse((repo / ".refactor-loop/state/release-publish-result.json").exists())

    def test_publisher_requires_repo_slug_for_fresh_commit_check_gate(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            (repo / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
            (repo / ".config/consensus-rnd/host.env").write_text("", encoding="utf-8")
            runner = FakeRunner()
            publisher = ReleasePublisher(repo, preflight=FakePreflight(allowed_result(repo)), runner=runner, now=lambda: NOW)

            with mock.patch.dict(os.environ, {"GH_REPO_SLUG": ""}, clear=False):
                with self.assertRaisesRegex(RuntimeError, "GH_REPO_SLUG is required"):
                    with release_env():
                        publisher.publish(target_ref="abc123")

            self.assertIn(["git", "push", "origin", "HEAD"], runner.commands)
            self.assertFalse(any(command[:2] == ["gh", "api"] for command in runner.commands))
            self.assertFalse(any(command[:3] == ["gh", "release", "create"] for command in runner.commands))
            self.assertFalse((repo / ".refactor-loop/state/release-publish-result.json").exists())

    def test_publisher_records_result_and_uses_bump_commit_tag_target(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            preflight = FakePreflight(allowed_result(repo))
            runner = FakeRunner()
            publisher = ReleasePublisher(repo, preflight=preflight, runner=runner, now=lambda: NOW)

            with release_env():
                result = publisher.publish(target_ref="abc123")

            self.assertTrue(result.published)
            self.assertEqual(result.tag, "v2.0.0-beta.4")
            self.assertEqual(result.target_ref, "bumpcommit456")
            self.assertIn(["git", "commit", "-m", "Release v2.0.0-beta.4"], runner.commands)
            self.assertIn(["git", "rev-parse", "HEAD"], runner.commands)
            self.assertIn(["git", "push", "origin", "HEAD"], runner.commands)
            self.assertEqual(
                runner.commands[-1],
                [
                    "gh",
                    "release",
                    "create",
                    "v2.0.0-beta.4",
                    "--target",
                    "bumpcommit456",
                    "--generate-notes",
                    "--prerelease",
                ],
            )
            self.assertNotIn(expected_violating_pre_bump_tag_command(), runner.commands)
            payload = read_json(repo / ".refactor-loop/state/release-publish-result.json")
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertEqual(payload["tag"], "v2.0.0-beta.4")
            self.assertEqual(payload["target_ref"], "bumpcommit456")
            self.assertEqual(payload["candidate_digest"], "digest123")
            self.assertEqual(payload["release_url"], "https://github.test/release/v2.0.0")

    def test_publisher_does_not_mark_ga_release_as_prerelease(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            preflight = FakePreflight(allowed_result(repo, version="1.0.0"))
            runner = FakeRunner()
            publisher = ReleasePublisher(repo, preflight=preflight, runner=runner, now=lambda: NOW)

            with release_env():
                result = publisher.publish(target_ref="abc123")

            self.assertTrue(result.published)
            self.assertEqual(result.tag, "v1.0.0")
            self.assertEqual(runner.commands, expected_success_commands(version="1.0.0", prerelease=False))
            self.assertNotIn("--prerelease", runner.commands[-1])

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

            with release_env():
                result = publisher.publish(target_ref="abc123")

            self.assertFalse(result.published)
            self.assertEqual(result.reasons, ("host_opt_in_not_true",))
            self.assertEqual(runner.commands, [])
            self.assertFalse((repo / ".refactor-loop/state/release-publish-result.json").exists())

    def test_publisher_does_not_define_github_actor_or_write_permit_authority(self) -> None:
        source = (SCRIPT_PATH.parent / "codex_refactor_loop" / "release" / "publisher.py").read_text(encoding="utf-8")
        for forbidden in (
            "GitHubAuthenticatedActor",
            "ControllerWritePermit",
            "GitHubWritePermit",
            "author.login",
            "updatedAt",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_publisher_refuses_when_release_commit_is_behind_origin_head(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            runner = FakeRunner()
            runner.rev_list_stdout = "2\n"
            publisher = ReleasePublisher(repo, preflight=FakePreflight(allowed_result(repo)), runner=runner, now=lambda: NOW)

            with self.assertRaisesRegex(RuntimeError, "safe push refused"):
                with release_env():
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
                with release_env():
                    publisher.publish(target_ref="abc123")

            self.assertEqual(runner.commands, expected_success_commands()[:2])
            self.assertNotIn(expected_violating_pre_bump_tag_command(), runner.commands)
            self.assertFalse((repo / ".refactor-loop/state/release-publish-result.json").exists())


if __name__ == "__main__":
    unittest.main()
