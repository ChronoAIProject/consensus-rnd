#!/usr/bin/env python3
"""Release preflight tests for wakeup-runner."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.release.gate import canonical_digest, isoformat
from codex_refactor_loop.release.publish_preflight import ReleasePublishPreflight as RealReleasePublishPreflight
from codex_refactor_loop.wakeup_runner import WakeupRunner


class FakeActions:
    def __init__(self) -> None:
        self.published = 0

    def publish_release_candidate(self, *, candidate_path: str, target_ref: str):
        self.published += 1
        return type("Result", (), {"published": True})()


class WakeupRunnerReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        for rel in (".refactor-loop/state", ".refactor-loop/logs"):
            (self.repo / rel).mkdir(parents=True, exist_ok=True)
        (self.repo / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.repo / ".config/consensus-rnd/host.env").write_text(f'export REPO_ROOT="{self.repo}"\nexport GH_REPO_SLUG="owner/repo"\n', encoding="utf-8")
        self.ctx = LoopContext.load(repo_root=self.repo, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})
        log = self.repo / ".refactor-loop/logs/release.log"
        log.write_text("release-ready\nEXIT=0\n", encoding="utf-8")
        self.action = {
            "kind": "completed-marker",
            "action_id": "release:1",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "clean_exit_source_marker"],
            "source_artifact": ".refactor-loop/logs/release.log",
            "source_marker": "release-ready",
            "target_kind": None,
            "target_number": None,
            "target": None,
            "controller_action": "publish_release_candidate",
            "candidate_path": ".refactor-loop/state/release-candidate.json",
            "target_ref": "origin/dev",
            "no_generic_command": True,
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_release(self, allowed: bool) -> tuple[object, FakeActions]:
        actions = FakeActions()
        preflight_result = type("Preflight", (), {"allowed": allowed, "reasons": () if allowed else ("pending_required_checks",)})()
        with mock.patch("codex_refactor_loop.wakeup_runner.ReleasePublishPreflight") as preflight:
            preflight.return_value.validate.return_value = preflight_result
            runner = WakeupRunner(
                self.ctx,
                plan_loader=lambda _repo: {
                    "schema": "wakeup-plan",
                    "mode": "closed-action-projection",
                    "apply_authority": "wakeup-runner-396-only",
                    "no_lifecycle_authority": True,
                    "actions": [self.action],
                },
                actions=actions,
                command_runner=lambda command: subprocess.CompletedProcess(command, 0, "OPEN\n", ""),
            )
            return runner.run_once()[0], actions

    def write_remote_reentry_fixtures(self, *, current_version: str = "2.0.0-beta.4", to_version: str = "2.0.0-beta.4") -> None:
        (self.repo / ".config/consensus-rnd/host.env").write_text(
            f'export REPO_ROOT="{self.repo}"\n'
            'export GH_REPO_SLUG="owner/repo"\n'
            'export RELEASE_AUTO_ENABLE="true"\n'
            'export INTEGRATION_BRANCH="auto-refact-dev"\n'
            'export HOST_GITHUB_RELEASE_REQUIRED_CHECKS="ci"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.repo, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})
        (self.repo / ".version-bump.json").write_text(
            json.dumps({"files": [{"path": "package.json", "field": "version"}]}),
            encoding="utf-8",
        )
        (self.repo / "package.json").write_text(json.dumps({"version": current_version}), encoding="utf-8")
        now = datetime.now(timezone.utc)
        decision = {
            "from_version": "2.0.0-beta.3",
            "to_version": to_version,
            "bump_type": "patch",
            "coordinate_policy": None,
            "ready": True,
            "signals": {
                "required_checks_recent_green": {
                    "passed": True,
                    "branches": {"dev": {"ci": True}, "auto-refact-dev": {"ci": True}},
                },
            },
        }
        candidate = {
            "schema": "decision-artifact-only/v2",
            "generated_at": isoformat(now),
            "expires_at": isoformat(now + timedelta(minutes=60)),
            "decision_artifact": ".refactor-loop/state/release-decision.json",
            "from_version": "2.0.0-beta.3",
            "to_version": to_version,
            "bump_type": "patch",
            "coordinate_policy": None,
            "ready": True,
            "target_ref": "origin/dev",
            "required_signals": decision["signals"],
            "decision_digest": canonical_digest(decision),
            "publish_preflight": "controller-release-publish-preflight",
            "lifecycle_owner": "controller",
        }
        (self.repo / ".refactor-loop/state/release-decision.json").write_text(json.dumps(decision), encoding="utf-8")
        (self.repo / ".refactor-loop/state/release-candidate.json").write_text(json.dumps(candidate), encoding="utf-8")
        self.action["target_ref"] = "origin/dev"

    def run_remote_reentry_release(
        self,
        *,
        remote_sha: str = "1234567890abcdef1234567890abcdef12345678",
        remote_subject: str = "Release v2.0.0-beta.4",
        remote_manifest_version: str = "2.0.0-beta.4",
        current_version: str = "2.0.0-beta.4",
        extra_denial: str | None = None,
    ) -> tuple[object, FakeActions, list[list[str]]]:
        self.write_remote_reentry_fixtures(current_version=current_version)
        actions = FakeActions()
        commands: list[list[str]] = []

        def command_runner(command):
            commands.append(list(command))
            if command == ["git", "show", "-s", "--format=%s", "HEAD"]:
                return subprocess.CompletedProcess(command, 0, "daemon hotfix\n", "")
            if command == ["git", "fetch", "origin", "auto-refact-dev"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            if command == ["git", "rev-parse", "origin/auto-refact-dev"]:
                return subprocess.CompletedProcess(command, 0, remote_sha + "\n", "")
            if command == ["git", "show", "-s", "--format=%s", "origin/auto-refact-dev"]:
                return subprocess.CompletedProcess(command, 0, remote_subject + "\n", "")
            if command == ["git", "show", "origin/auto-refact-dev:package.json"]:
                return subprocess.CompletedProcess(command, 0, json.dumps({"version": remote_manifest_version}), "")
            if command == ["git", "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/rollup"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.dict(
            "os.environ",
            {"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"},
            clear=False,
        ):
            preflight_patch = mock.patch("codex_refactor_loop.wakeup_runner.ReleasePublishPreflight")
            if extra_denial is not None:
                preflight = preflight_patch.start()
                real_preflight = RealReleasePublishPreflight(
                    self.ctx.repo_root,
                    now=lambda: datetime.now(timezone.utc),
                    runner=lambda command, _cwd: command_runner(command),
                )
                real_result = real_preflight.validate(candidate_path=".refactor-loop/state/release-candidate.json", target_ref="origin/dev")
                preflight.return_value.validate.return_value = replace(
                    real_result,
                    allowed=False,
                    reasons=(*real_result.reasons, extra_denial),
                )

            try:
                runner = WakeupRunner(
                    self.ctx,
                    plan_loader=lambda _repo: {
                        "schema": "wakeup-plan",
                        "mode": "closed-action-projection",
                        "apply_authority": "wakeup-runner-396-only",
                        "no_lifecycle_authority": True,
                        "actions": [self.action],
                    },
                    actions=actions,
                    command_runner=command_runner,
                )
                return runner.run_once()[0], actions, commands
            finally:
                if extra_denial is not None:
                    preflight_patch.stop()

    def test_release_calls_publisher_only_after_322_preflight_allowed(self) -> None:
        result, actions = self.run_release(True)

        self.assertEqual(result.status, "applied")
        self.assertEqual(actions.published, 1)

    def test_release_pending_red_missing_fails_closed(self) -> None:
        result, actions = self.run_release(False)

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "release_preflight_denied:pending_required_checks")
        self.assertEqual(actions.published, 0)

    def test_release_manifest_mismatch_remote_reentry_proof_allows_publisher_dispatch(self) -> None:
        result, actions, commands = self.run_remote_reentry_release()

        self.assertEqual(result.status, "applied")
        self.assertEqual(actions.published, 1)
        self.assertIn(["git", "fetch", "origin", "auto-refact-dev"], commands)
        self.assertIn(["git", "show", "origin/auto-refact-dev:package.json"], commands)
        self.assertFalse(any(command[:3] == ["gh", "release", "create"] for command in commands))

    def test_release_manifest_mismatch_remote_reentry_requires_unique_denial(self) -> None:
        result, actions, _commands = self.run_remote_reentry_release(extra_denial="host_opt_in_not_true")

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "release_preflight_denied:manifest_version_mismatch,host_opt_in_not_true")
        self.assertEqual(actions.published, 0)

    def test_release_manifest_mismatch_remote_reentry_without_proof_fails_closed(self) -> None:
        result, actions, _commands = self.run_remote_reentry_release(remote_subject="not a release commit")

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "release_preflight_denied:manifest_version_mismatch")
        self.assertEqual(actions.published, 0)


if __name__ == "__main__":
    unittest.main()
