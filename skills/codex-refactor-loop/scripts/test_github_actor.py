#!/usr/bin/env python3
"""Behavior tests for read-only GitHub authenticated actor admission."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.github_actor import GitHubAuthenticatedActor


class GitHubAuthenticatedActorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="github-actor-test-"))
        (self.tmp / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})
        self.commands: list[list[str]] = []
        self.auth_status = subprocess.CompletedProcess(["gh", "auth", "status"], 0, "", "")
        self.user = subprocess.CompletedProcess(["gh", "api", "user"], 0, json.dumps({"login": "controller-bot"}), "")
        self.repo = subprocess.CompletedProcess(
            ["gh", "api", "repos/owner/repo"],
            0,
            json.dumps({"viewer_permission": "WRITE"}),
            "",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def runner(self, cmd: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        command = list(cmd)
        self.commands.append(command)
        if command == ["gh", "auth", "status"]:
            return self.auth_status
        if command == ["gh", "api", "user"]:
            return self.user
        if command == ["gh", "api", "repos/owner/repo"]:
            return self.repo
        return subprocess.CompletedProcess(command, 99, "", "unexpected command")

    def test_admission_reads_authenticated_actor_and_repo_permission(self) -> None:
        GitHubAuthenticatedActor(self.ctx, runner=self.runner).require_admission("post-banner")

        self.assertEqual(
            self.commands,
            [
                ["gh", "auth", "status"],
                ["gh", "api", "user"],
                ["gh", "api", "repos/owner/repo"],
            ],
        )

    def test_admission_fails_closed_without_authenticated_login(self) -> None:
        self.user = subprocess.CompletedProcess(["gh", "api", "user"], 0, "{}", "")

        with self.assertRaisesRegex(RuntimeError, "authenticated login missing"):
            GitHubAuthenticatedActor(self.ctx, runner=self.runner).require_admission("merge-pr")

    def test_admission_fails_closed_without_write_permission(self) -> None:
        self.repo = subprocess.CompletedProcess(
            ["gh", "api", "repos/owner/repo"],
            0,
            json.dumps({"viewer_permission": "READ"}),
            "",
        )

        with self.assertRaisesRegex(RuntimeError, "lacks write permission"):
            GitHubAuthenticatedActor(self.ctx, runner=self.runner).require_admission("merge-pr")

    def test_admission_accepts_permissions_object_fallback(self) -> None:
        self.repo = subprocess.CompletedProcess(
            ["gh", "api", "repos/owner/repo"],
            0,
            json.dumps({"permissions": {"push": True}}),
            "",
        )

        GitHubAuthenticatedActor(self.ctx, runner=self.runner).require_admission("open-pr")

    def test_admission_module_does_not_define_lifecycle_write_permits(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "github_actor.py").read_text(encoding="utf-8")
        for forbidden in (
            "ControllerWritePermit",
            "GitHubWritePermit",
            "lifecycle_authority",
            "lifecycle_owner",
            "author.login",
            "updatedAt",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
