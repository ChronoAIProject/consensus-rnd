#!/usr/bin/env python3
"""Behavior tests for the patrol-owned issue publisher."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import labels
from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.patrol_issue_publisher import (
    PATROL_LABEL_BUNDLE,
    PatrolIssuePublisher,
    ensure_fingerprint_line,
    fingerprint_marker,
)


class FakeGhRunner:
    def __init__(self, search_payload: object, create_stdout: str = "https://github.com/o/r/issues/42\n") -> None:
        self.search_payload = search_payload
        self.create_stdout = create_stdout
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        command = list(argv)
        self.calls.append(command)
        if command[:3] == ["gh", "issue", "list"]:
            return subprocess.CompletedProcess(command, 0, json.dumps(self.search_payload), "")
        if command[:3] == ["gh", "issue", "create"]:
            return subprocess.CompletedProcess(command, 0, self.create_stdout, "")
        if command[:3] == ["gh", "issue", "edit"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 1, "", "unexpected")


class PatrolIssuePublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="patrol-publisher-test-"))
        (self.tmp / ".config" / "consensus-rnd").mkdir(parents=True)
        host_env = self.tmp / ".config" / "consensus-rnd" / "host.env"
        host_env.write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_uses_fixed_patrol_design_label_bundle(self) -> None:
        runner = FakeGhRunner([])
        issue = PatrolIssuePublisher(self.ctx, command_runner=runner).publish(
            fingerprint="abc123",
            title="巡检发现: runtime failed [abc123]",
            body="body\n",
        )

        self.assertEqual(42, issue.number)
        create = next(call for call in runner.calls if call[:3] == ["gh", "issue", "create"])
        self.assertIn("--repo", create)
        self.assertIn("owner/repo", create)
        for label in PATROL_LABEL_BUNDLE:
            with self.subTest(label=label):
                self.assertIn(label, create)
        self.assertEqual(
            (labels.MANAGED, labels.PHASE_DESIGN_SOLVING, labels.HUMAN_AUTO, labels.TRIAGE_PENDING),
            PATROL_LABEL_BUNDLE,
        )

    def test_existing_fingerprint_updates_body_only(self) -> None:
        runner = FakeGhRunner(
            [
                {
                    "number": 77,
                    "title": "old",
                    "body": fingerprint_marker("abc123"),
                    "labels": [{"name": labels.MANAGED}],
                    "state": "open",
                }
            ]
        )

        issue = PatrolIssuePublisher(self.ctx, command_runner=runner).publish(
            fingerprint="abc123",
            title="new",
            body="new body\n",
        )

        self.assertEqual(77, issue.number)
        edit = next(call for call in runner.calls if call[:3] == ["gh", "issue", "edit"])
        self.assertEqual("77", edit[3])
        self.assertIn("--body-file", edit)
        self.assertNotIn("--label", edit)
        self.assertFalse(any(call[:3] == ["gh", "issue", "create"] for call in runner.calls))

    def test_ambiguous_fingerprint_fails_closed(self) -> None:
        payload = [
            {"number": 1, "title": "a", "body": fingerprint_marker("dup"), "labels": [], "state": "open"},
            {"number": 2, "title": "b", "body": fingerprint_marker("dup"), "labels": [], "state": "open"},
        ]
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            PatrolIssuePublisher(self.ctx, command_runner=FakeGhRunner(payload)).publish(
                fingerprint="dup",
                title="x",
                body="body\n",
            )

    def test_body_fingerprint_line_is_single_durable_marker(self) -> None:
        body = ensure_fingerprint_line("one\ncrnd:patrol:fingerprint:old\n", "new")

        self.assertNotIn("old", body)
        self.assertEqual(1, body.count("crnd:patrol:fingerprint:"))
        self.assertTrue(body.endswith("crnd:patrol:fingerprint:new\n"))


if __name__ == "__main__":
    unittest.main()
