#!/usr/bin/env python3
"""Behavior tests for non-blocking publish diagnostic evidence."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

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

    def test_diagnostic_records_configured_commands_without_running_them(self) -> None:
        result = publish_verification.verify_or_run(**self.identity)

        self.assertFalse(result.ok)
        self.assertEqual("diagnostic", result.status)
        self.assertEqual("post_pr_non_blocking", result.reason)
        payload = json.loads(result.evidence_file.read_text(encoding="utf-8"))
        self.assertEqual("diagnostic", payload["status"])
        self.assertEqual("post_pr_non_blocking", payload["reason"])
        self.assertIs(payload["blocking"], False)
        self.assertEqual("77", payload["issue"])
        self.assertEqual("publish_implementation_output", payload["action"])
        self.assertEqual("refactor/iter77-issue-77", payload["head_ref"])
        self.assertEqual(str(self.worktree.resolve()), payload["worktree"])
        self.assertEqual(["BUILD_CMD", "TEST_CMD"], [item["name"] for item in payload["commands"]])
        self.assertEqual([True, True], [item["configured"] for item in payload["commands"]])
        self.assertEqual(
            ['bash -lc "$BUILD_CMD"', 'bash -lc "$TEST_CMD"'],
            [item["suggested_invocation"] for item in payload["commands"]],
        )
        self.assertFalse(list(result.evidence_file.parent.glob("*.log")))

    def test_missing_commands_are_diagnostic_not_fail_closed(self) -> None:
        env = dict(self.env)
        env["TEST_CMD"] = ""

        result = publish_verification.verify_or_run(**{**self.identity, "env": env})

        self.assertFalse(result.ok)
        self.assertEqual("diagnostic", result.status)
        payload = json.loads(result.evidence_file.read_text(encoding="utf-8"))
        by_name = {item["name"]: item for item in payload["commands"]}
        self.assertTrue(by_name["BUILD_CMD"]["configured"])
        self.assertFalse(by_name["TEST_CMD"]["configured"])
        self.assertNotIn("suggested_invocation", by_name["TEST_CMD"])

    def test_diagnostic_overwrites_stale_blocking_evidence_for_same_identity(self) -> None:
        path = publish_verification.evidence_path(self.tmp, "77", "refactor/iter77-issue-77")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "issue": "77",
                    "action": "publish_implementation_output",
                    "head_ref": "refactor/iter77-issue-77",
                    "worktree": str(self.worktree.resolve()),
                    "head_sha": "a" * 40,
                    "command_digest": "stale",
                    "commands": [{"name": "BUILD_CMD", "exit": 2, "exit_marker": True}],
                    "status": "failed",
                    "reason": "BUILD_CMD-failed:2",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = publish_verification.verify_or_run(**self.identity)

        self.assertEqual(path, result.evidence_file)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(publish_verification.VERIFY_VERSION, payload["version"])
        self.assertEqual("diagnostic", payload["status"])
        self.assertEqual("post_pr_non_blocking", payload["reason"])
        self.assertNotIn("exit", payload["commands"][0])
        self.assertNotIn("exit_marker", payload["commands"][0])


if __name__ == "__main__":
    unittest.main()
