#!/usr/bin/env python3
"""Release preflight tests for wakeup-runner."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
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

    def test_release_calls_publisher_only_after_322_preflight_allowed(self) -> None:
        result, actions = self.run_release(True)

        self.assertEqual(result.status, "applied")
        self.assertEqual(actions.published, 1)

    def test_release_pending_red_missing_fails_closed(self) -> None:
        result, actions = self.run_release(False)

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.reason, "release_preflight_denied:pending_required_checks")
        self.assertEqual(actions.published, 0)


if __name__ == "__main__":
    unittest.main()
