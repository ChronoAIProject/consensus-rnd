#!/usr/bin/env python3
"""Behavior tests for default issue intake claim helper."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from typing import Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import labels  # noqa: E402
from codex_refactor_loop.context import LoopContext  # noqa: E402
from codex_refactor_loop.default_issue_intake import (  # noqa: E402
    CLAIM_MARKER,
    STOP_MARKER,
    DefaultIssueIntakeClaim,
    default_issue_intake_enabled,
)


class FakeGh:
    def __init__(self, *, issue: dict, comments: list[dict] | None = None, comment_returncode: int = 0, comment_stderr: str = "") -> None:
        self.issue = issue
        self.comments = list(comments or [])
        self.comment_returncode = comment_returncode
        self.comment_stderr = comment_stderr
        self.calls: list[list[str]] = []
        self.body_files: list[str] = []

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        argv = list(command)
        self.calls.append(argv)
        if argv[:3] == ["gh", "api", "repos/owner/repo/issues/77"]:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(self.issue), stderr="")
        if argv[:3] == ["gh", "api", "repos/owner/repo/issues/77/comments?per_page=100"]:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(self.comments), stderr="")
        if argv[:3] == ["gh", "issue", "comment"]:
            self.body_files.append(Path(argv[-1]).read_text(encoding="utf-8"))
            return subprocess.CompletedProcess(argv, self.comment_returncode, stdout="", stderr=self.comment_stderr)
        if argv[:3] == ["gh", "issue", "edit"]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="unexpected command")


class DefaultIssueIntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / ".config" / "consensus-rnd").mkdir(parents=True)
        self.host_env = self.repo / ".config" / "consensus-rnd" / "host.env"
        self.host_env.write_text(
            f"REPO_ROOT={self.repo}\nGH_REPO_SLUG=owner/repo\nDEFAULT_ISSUE_INTAKE_ENABLE=true\n",
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(
            repo_root=self.repo,
            env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"},
            cwd=self.repo,
            read_only=True,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_missing_or_empty_enable_defaults_true(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(default_issue_intake_enabled({}))
            self.assertTrue(default_issue_intake_enabled({"DEFAULT_ISSUE_INTAKE_ENABLE": ""}))
            self.assertFalse(default_issue_intake_enabled({"DEFAULT_ISSUE_INTAKE_ENABLE": "false"}))

    def test_no_prior_claim_writes_claim_and_design_labels(self) -> None:
        fake = FakeGh(issue={"state": "open", "labels": []})
        result = DefaultIssueIntakeClaim(self.ctx, actor_login="alice", command_runner=fake).apply(77)

        self.assertEqual("applied", result.status)
        self.assertEqual("claim_created", result.reason)
        self.assertIn(CLAIM_MARKER, fake.body_files[0])
        edit_calls = [call for call in fake.calls if call[:3] == ["gh", "issue", "edit"]]
        self.assertEqual(1, len(edit_calls))
        self.assertEqual(",".join(labels.design_issue_label_bundle()), edit_calls[0][-1])

    def test_earlier_other_claim_writes_stop_only(self) -> None:
        fake = FakeGh(
            issue={"state": "open", "labels": []},
            comments=[
                {
                    "body": CLAIM_MARKER,
                    "created_at": "2026-06-07T01:00:00Z",
                    "user": {"login": "bob"},
                }
            ],
        )
        result = DefaultIssueIntakeClaim(self.ctx, actor_login="alice", command_runner=fake).apply(77)

        self.assertEqual("stopped", result.status)
        self.assertEqual("bob", result.first_claimer)
        self.assertIn(STOP_MARKER, fake.body_files[0])
        self.assertFalse(any(call[:3] == ["gh", "issue", "edit"] for call in fake.calls))

    def test_failed_claim_comment_records_secondary_backoff_without_label_mutation(self) -> None:
        fake = FakeGh(
            issue={"state": "open", "labels": []},
            comment_returncode=1,
            comment_stderr="You have exceeded a secondary rate limit",
        )

        with self.assertRaisesRegex(RuntimeError, "claim comment failed"):
            DefaultIssueIntakeClaim(self.ctx, actor_login="alice", command_runner=fake).apply(77)

        self.assertTrue(any(call[:3] == ["gh", "issue", "comment"] for call in fake.calls))
        self.assertFalse(any(call[:3] == ["gh", "issue", "edit"] for call in fake.calls))
        backoff = json.loads((self.repo / ".refactor-loop/state/secondary-mutation-backoff.json").read_text(encoding="utf-8"))
        self.assertEqual("default-issue-intake-claim comment", backoff["contentCreation"]["operation"])
        self.assertEqual("secondary-content-creation-limit", backoff["contentCreation"]["reason"])

    def test_managed_or_pr_targets_noop_before_comments(self) -> None:
        for issue, reason in (
            ({"state": "open", "labels": [{"name": labels.MANAGED}]}, "target_already_managed"),
            ({"state": "open", "labels": [], "pull_request": {"url": "x"}}, "target_is_pr"),
        ):
            with self.subTest(reason=reason):
                fake = FakeGh(issue=issue)
                result = DefaultIssueIntakeClaim(self.ctx, actor_login="alice", command_runner=fake).apply(77)
                self.assertEqual("noop", result.status)
                self.assertEqual(reason, result.reason)
                self.assertFalse(any("/comments" in " ".join(call) for call in fake.calls))


if __name__ == "__main__":
    unittest.main()
