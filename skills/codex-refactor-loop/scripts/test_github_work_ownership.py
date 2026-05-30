#!/usr/bin/env python3
"""Behavior tests for GitHub-native work ownership."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from codex_refactor_loop.ownership import GitHubWorkOwnership, WorkTarget, WorkTargetResolver


NOW = datetime(2026, 5, 30, 0, 0, tzinfo=timezone.utc)


class FakeGh:
    def __init__(self, *, login: str = "alice", author: str = "alice", updated_at: str = "2026-05-29T23:00:00Z") -> None:
        self.login = login
        self.author = author
        self.updated_at = updated_at
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if command[:3] == ["gh", "api", "user"]:
            return subprocess.CompletedProcess(command, 0, json.dumps({"login": self.login}), "")
        if command[:3] in (["gh", "issue", "view"], ["gh", "pr", "view"]):
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({"author": {"login": self.author}, "updatedAt": self.updated_at}),
                "",
            )
        if command[:3] in (["gh", "issue", "comment"], ["gh", "pr", "comment"]):
            return subprocess.CompletedProcess(command, 0, "comment-url\n", "")
        return subprocess.CompletedProcess(command, 1, "", "unexpected command")


class GitHubWorkOwnershipTests(unittest.TestCase):
    def ownership(self, fake: FakeGh) -> GitHubWorkOwnership:
        return GitHubWorkOwnership("owner/repo", cwd=Path(tempfile.gettempdir()), command_runner=fake)

    def test_allows_current_login_author(self) -> None:
        fake = FakeGh(login="alice", author="alice")

        decision = self.ownership(fake).decide(WorkTarget("issue", 193), now=NOW)

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "owned")

    def test_skips_fresh_foreign_author(self) -> None:
        fake = FakeGh(login="alice", author="bob", updated_at="2026-05-29T23:30:00Z")

        decision = self.ownership(fake).decide(WorkTarget("pr", 44), now=NOW)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "foreign-fresh")

    def test_allows_foreign_stale_after_three_hours(self) -> None:
        fake = FakeGh(login="alice", author="bob", updated_at="2026-05-29T20:30:00Z")

        decision = self.ownership(fake).decide(WorkTarget("issue", 44), now=NOW)

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "stale-takeover")
        self.assertGreaterEqual(decision.age_hours, 3)

    def test_unknown_current_login_fails_closed(self) -> None:
        fake = FakeGh(login="", author="bob")

        decision = self.ownership(fake).decide(WorkTarget("issue", 44), now=NOW)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "unknown-current-login")

    def test_helper_uses_read_only_github_fields_only(self) -> None:
        fake = FakeGh(login="alice", author="bob")

        self.ownership(fake).decide(WorkTarget("pr", 9), now=NOW)

        rendered = "\n".join(" ".join(command) for command in fake.commands)
        self.assertIn("gh api user", rendered)
        self.assertIn("gh pr view 9 --json author,updatedAt --repo owner/repo", rendered)
        for forbidden in ("git ", "issue edit", "pr edit", "label", "body-file"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, rendered)

    def test_takeover_comment_is_visibility_only_and_has_sentinel(self) -> None:
        fake = FakeGh(login="alice", author="bob", updated_at="2026-05-29T20:00:00Z")
        ownership = self.ownership(fake)
        decision = ownership.decide(WorkTarget("issue", 44), now=NOW)

        body = ownership.takeover_comment(decision)

        self.assertIn("issue #44", body)
        self.assertIn("author.login", body)
        self.assertIn("updatedAt", body)
        self.assertIn("visibility only", body)
        self.assertTrue(body.rstrip().endswith("⟦AI:AUTO-LOOP⟧"))

    def test_post_takeover_notice_comments_on_target_only_for_stale_takeover(self) -> None:
        fake = FakeGh(login="alice", author="bob", updated_at="2026-05-29T20:00:00Z")
        ownership = self.ownership(fake)
        decision = ownership.decide(WorkTarget("pr", 44), now=NOW)

        self.assertTrue(ownership.post_takeover_notice(decision))

        rendered = "\n".join(" ".join(command) for command in fake.commands)
        self.assertIn("gh pr comment 44 --body", rendered)
        self.assertIn("--repo owner/repo", rendered)
        self.assertIn("Stale takeover notice", rendered)

    def test_post_takeover_notice_fails_closed_when_gh_comment_fails(self) -> None:
        def failing_comment(command: list[str]) -> subprocess.CompletedProcess[str]:
            if command[:3] == ["gh", "api", "user"]:
                return subprocess.CompletedProcess(command, 0, json.dumps({"login": "alice"}), "")
            if command[:3] == ["gh", "issue", "view"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps({"author": {"login": "bob"}, "updatedAt": "2026-05-29T20:00:00Z"}),
                    "",
                )
            if command[:3] == ["gh", "issue", "comment"]:
                return subprocess.CompletedProcess(command, 1, "", "comment failed")
            return subprocess.CompletedProcess(command, 1, "", "unexpected command")

        ownership = GitHubWorkOwnership("owner/repo", cwd=Path(tempfile.gettempdir()), command_runner=failing_comment)
        decision = ownership.decide(WorkTarget("issue", 44), now=NOW)

        self.assertFalse(ownership.post_takeover_notice(decision))

    def test_post_takeover_notice_noops_for_owned_decision(self) -> None:
        fake = FakeGh(login="alice", author="alice")
        ownership = self.ownership(fake)
        decision = ownership.decide(WorkTarget("issue", 44), now=NOW)
        command_count = len(fake.commands)

        self.assertTrue(ownership.post_takeover_notice(decision))

        self.assertEqual(len(fake.commands), command_count)

    def test_target_resolver_accepts_existing_payload_surfaces(self) -> None:
        self.assertEqual(WorkTargetResolver.from_payload({"github_target": {"kind": "issue", "number": 193}}), WorkTarget("issue", 193))
        self.assertEqual(WorkTargetResolver.from_payload({"pr_number": 7}), WorkTarget("pr", 7))
        self.assertEqual(WorkTargetResolver.from_payload({"task_id": "phase9-issue193-r4-minimal"}), WorkTarget("issue", 193))
        self.assertEqual(WorkTargetResolver.from_payload({"task_id": "fix-pr44-round-1"}), WorkTarget("pr", 44))


if __name__ == "__main__":
    unittest.main()
