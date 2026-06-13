#!/usr/bin/env python3
"""Behavior tests for publish verification evidence reuse."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Mapping
from unittest import mock

import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import publish_verification


class PublishVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="publish-verification-test-"))
        self.worktree = self.tmp / ".worktrees" / "iter77-issue-77"
        self.worktree.mkdir(parents=True)
        self.env = {
            "BUILD_CMD": "make build",
            "TEST_CMD": "make test",
            "CONSENSUS_RND_HOST_ENV": str(self.tmp / ".config" / "consensus-rnd" / "host.env"),
        }
        self.identity = {
            "repo_root": self.tmp,
            "worktree": self.worktree,
            "issue": "77",
            "action": "publish_implementation_output",
            "head_ref": "refactor/iter77-issue-77",
            "head_sha": "a" * 40,
            "env": self.env,
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fresh_build_and_test_writes_ok_evidence(self) -> None:
        calls: list[tuple[str, Path, Path]] = []

        def fake_run(command: str, *, cwd: Path, env: Mapping[str, str], log: Path) -> int:
            calls.append((command, cwd, log))
            log.write_text(f"COMMAND={command}\nEXIT=0\n", encoding="utf-8")
            return 0

        with mock.patch("codex_refactor_loop.publish_verification.run_fixed_host_command", side_effect=fake_run):
            result = publish_verification.verify_or_run(**self.identity)

        self.assertTrue(result.ok)
        self.assertEqual("verified", result.reason)
        self.assertEqual(
            [("make build", self.worktree, result.evidence_file.with_name(f"{result.evidence_file.stem}-BUILD_CMD.log")),
             ("make test", self.worktree, result.evidence_file.with_name(f"{result.evidence_file.stem}-TEST_CMD.log"))],
            calls,
        )
        payload = json.loads(result.evidence_file.read_text(encoding="utf-8"))
        self.assertEqual("ok", payload["status"])
        self.assertEqual("verified", payload["reason"])
        self.assertEqual("77", payload["issue"])
        self.assertEqual(str(self.worktree.resolve()), payload["worktree"])
        self.assertEqual(["BUILD_CMD", "TEST_CMD"], [item["name"] for item in payload["commands"]])
        self.assertTrue(all(item["exit"] == 0 and item["exit_marker"] is True for item in payload["commands"]))

    def test_exact_matching_evidence_is_reused_without_running_commands(self) -> None:
        evidence = self._write_ok_evidence()

        with mock.patch(
            "codex_refactor_loop.publish_verification.run_fixed_host_command",
            side_effect=AssertionError("matching evidence must not rerun host commands"),
        ):
            result = publish_verification.verify_or_run(**self.identity)

        self.assertTrue(result.ok)
        self.assertEqual("reused", result.reason)
        self.assertEqual(evidence, result.evidence_file)

    def test_mismatched_head_sha_is_rejected_and_commands_rerun(self) -> None:
        self._write_ok_evidence(head_sha="b" * 40)
        commands: list[str] = []

        with mock.patch("codex_refactor_loop.publish_verification.run_fixed_host_command", side_effect=self._successful_command(commands)):
            result = publish_verification.verify_or_run(**self.identity)

        self.assertTrue(result.ok)
        self.assertEqual("verified", result.reason)
        self.assertEqual(["make build", "make test"], commands)
        payload = json.loads(result.evidence_file.read_text(encoding="utf-8"))
        self.assertEqual("a" * 40, payload["head_sha"])

    def test_mismatched_command_digest_is_rejected_and_commands_rerun(self) -> None:
        self._write_ok_evidence(command_digest="stale-digest")
        commands: list[str] = []

        with mock.patch("codex_refactor_loop.publish_verification.run_fixed_host_command", side_effect=self._successful_command(commands)):
            result = publish_verification.verify_or_run(**self.identity)

        self.assertTrue(result.ok)
        self.assertEqual("verified", result.reason)
        self.assertEqual(["make build", "make test"], commands)

    def test_mismatched_worktree_is_rejected_and_commands_rerun(self) -> None:
        self._write_ok_evidence(worktree=str((self.tmp / ".worktrees" / "other").resolve()))
        commands: list[str] = []

        with mock.patch("codex_refactor_loop.publish_verification.run_fixed_host_command", side_effect=self._successful_command(commands)):
            result = publish_verification.verify_or_run(**self.identity)

        self.assertTrue(result.ok)
        self.assertEqual("verified", result.reason)
        self.assertEqual(["make build", "make test"], commands)

    def test_mismatched_issue_action_or_command_list_rejects_reuse(self) -> None:
        cases = (
            ("issue", {"issue": "88"}),
            ("action", {"action": "other_action"}),
            ("missing-build", {"commands": [{"name": "TEST_CMD", "exit": 0, "exit_marker": True}]}),
            (
                "extra-command",
                {
                    "commands": [
                        {"name": "BUILD_CMD", "exit": 0, "exit_marker": True},
                        {"name": "TEST_CMD", "exit": 0, "exit_marker": True},
                        {"name": "EXTRA_CMD", "exit": 0, "exit_marker": True},
                    ]
                },
            ),
        )
        for name, overrides in cases:
            with self.subTest(name=name):
                shutil.rmtree(self.tmp / ".refactor-loop", ignore_errors=True)
                self._write_ok_evidence(**overrides)
                commands: list[str] = []
                with mock.patch(
                    "codex_refactor_loop.publish_verification.run_fixed_host_command",
                    side_effect=self._successful_command(commands),
                ):
                    result = publish_verification.verify_or_run(**self.identity)
                self.assertTrue(result.ok)
                self.assertEqual("verified", result.reason)
                self.assertEqual(["make build", "make test"], commands)

    def test_missing_exit_zero_or_nonzero_exit_rejects_reuse(self) -> None:
        cases = (
            ("missing-exit-marker", {"commands": [{"name": "BUILD_CMD", "exit": 0, "exit_marker": False}, {"name": "TEST_CMD", "exit": 0, "exit_marker": True}]}),
            ("nonzero-exit", {"commands": [{"name": "BUILD_CMD", "exit": 0, "exit_marker": True}, {"name": "TEST_CMD", "exit": 2, "exit_marker": True}]}),
            ("failed-status", {"status": "failed", "reason": "TEST_CMD-failed:2"}),
        )
        for name, overrides in cases:
            with self.subTest(name=name):
                shutil.rmtree(self.tmp / ".refactor-loop", ignore_errors=True)
                self._write_ok_evidence(**overrides)
                commands: list[str] = []
                with mock.patch(
                    "codex_refactor_loop.publish_verification.run_fixed_host_command",
                    side_effect=self._successful_command(commands),
                ):
                    result = publish_verification.verify_or_run(**self.identity)
                self.assertTrue(result.ok)
                self.assertEqual("verified", result.reason)
                self.assertEqual(["make build", "make test"], commands)

    def test_missing_build_or_test_command_fails_closed_before_ok(self) -> None:
        for missing_name in ("BUILD_CMD", "TEST_CMD"):
            with self.subTest(missing_name=missing_name):
                shutil.rmtree(self.tmp / ".refactor-loop", ignore_errors=True)
                env = dict(self.env)
                env[missing_name] = ""
                commands: list[str] = []
                with mock.patch(
                    "codex_refactor_loop.publish_verification.run_fixed_host_command",
                    side_effect=self._successful_command(commands),
                ):
                    result = publish_verification.verify_or_run(**{**self.identity, "env": env})

                self.assertFalse(result.ok)
                self.assertEqual("failed", result.status)
                self.assertEqual(f"missing-{missing_name}", result.reason)
                self.assertNotEqual("ok", json.loads(result.evidence_file.read_text(encoding="utf-8")).get("status"))

    def test_command_without_exit_zero_marker_fails_closed(self) -> None:
        def fake_run(command: str, *, cwd: Path, env: Mapping[str, str], log: Path) -> int:
            log.write_text(f"COMMAND={command}\nDONE\n", encoding="utf-8")
            return 0

        with mock.patch("codex_refactor_loop.publish_verification.run_fixed_host_command", side_effect=fake_run):
            result = publish_verification.verify_or_run(**self.identity)

        self.assertFalse(result.ok)
        self.assertEqual("BUILD_CMD-failed:0", result.reason)
        payload = json.loads(result.evidence_file.read_text(encoding="utf-8"))
        self.assertEqual("failed", payload["status"])
        self.assertEqual(False, payload["commands"][0]["exit_marker"])

    def test_nonzero_command_exit_fails_closed(self) -> None:
        def fake_run(command: str, *, cwd: Path, env: Mapping[str, str], log: Path) -> int:
            log.write_text(f"COMMAND={command}\nEXIT=2\n", encoding="utf-8")
            return 2

        with mock.patch("codex_refactor_loop.publish_verification.run_fixed_host_command", side_effect=fake_run):
            result = publish_verification.verify_or_run(**self.identity)

        self.assertFalse(result.ok)
        self.assertEqual("BUILD_CMD-failed:2", result.reason)
        payload = json.loads(result.evidence_file.read_text(encoding="utf-8"))
        self.assertEqual("failed", payload["status"])
        self.assertEqual(2, payload["commands"][0]["exit"])

    def _write_ok_evidence(self, **overrides: object) -> Path:
        path = publish_verification.evidence_path(self.tmp, "77", "refactor/iter77-issue-77")
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {
            "version": publish_verification.VERIFY_VERSION,
            "issue": "77",
            "action": "publish_implementation_output",
            "head_ref": "refactor/iter77-issue-77",
            "worktree": str(self.worktree.resolve()),
            "head_sha": "a" * 40,
            "command_digest": publish_verification.command_digest(self.env),
            "commands": [
                {"name": "BUILD_CMD", "exit": 0, "exit_marker": True},
                {"name": "TEST_CMD", "exit": 0, "exit_marker": True},
            ],
            "status": "ok",
            "reason": "verified",
        }
        payload.update(overrides)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def _successful_command(self, commands: list[str]) -> object:
        def fake_run(command: str, *, cwd: Path, env: Mapping[str, str], log: Path) -> int:
            commands.append(command)
            log.write_text(f"COMMAND={command}\nEXIT=0\n", encoding="utf-8")
            return 0

        return fake_run


if __name__ == "__main__":
    unittest.main()
