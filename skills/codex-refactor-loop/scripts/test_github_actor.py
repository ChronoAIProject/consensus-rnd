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
        self.user = subprocess.CompletedProcess(["gh", "api", "user"], 0, json.dumps({"login": "controller-bot"}), "")
        self.repo = subprocess.CompletedProcess(
            ["gh", "api", "repos/owner/repo/collaborators/controller-bot/permission"],
            0,
            json.dumps({"permission": "write"}),
            "",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def runner(self, cmd: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        command = list(cmd)
        self.commands.append(command)
        if command == ["gh", "api", "user"]:
            return self.user
        if command == ["gh", "api", "repos/owner/repo/collaborators/controller-bot/permission"]:
            return self.repo
        return subprocess.CompletedProcess(command, 99, "", "unexpected command")

    def test_admission_reads_authenticated_actor_and_repo_permission(self) -> None:
        admission = GitHubAuthenticatedActor(self.ctx, runner=self.runner).require_admission("post-banner")

        self.assertEqual(
            self.commands,
            [
                ["gh", "api", "user"],
                ["gh", "api", "repos/owner/repo/collaborators/controller-bot/permission"],
            ],
        )
        self.assertEqual(admission.login, "controller-bot")
        self.assertEqual(admission.repo_slug, "owner/repo")
        self.assertEqual(admission.permission, "write")
        self.assertEqual(admission.source, "gh-api")
        self.assertFalse(any(command[1:3] in (["issue", "view"], ["pr", "view"], ["issue", "edit"], ["pr", "edit"]) for command in self.commands))

    def test_admission_fails_closed_without_authenticated_login(self) -> None:
        self.user = subprocess.CompletedProcess(["gh", "api", "user"], 0, "{}", "")

        with self.assertRaisesRegex(RuntimeError, "authenticated login missing"):
            GitHubAuthenticatedActor(self.ctx, runner=self.runner).require_admission("merge-pr")

    def test_admission_fails_closed_without_write_permission(self) -> None:
        self.repo = subprocess.CompletedProcess(
            ["gh", "api", "repos/owner/repo/collaborators/controller-bot/permission"],
            0,
            json.dumps({"permission": "read"}),
            "",
        )

        with self.assertRaisesRegex(RuntimeError, "lacks write permission"):
            GitHubAuthenticatedActor(self.ctx, runner=self.runner).require_admission("merge-pr")

    def test_admission_accepts_collaborator_permission_values_at_or_above_write(self) -> None:
        for permission in ("write", "maintain", "admin"):
            with self.subTest(permission=permission):
                self.commands.clear()
                self.repo = subprocess.CompletedProcess(
                    ["gh", "api", "repos/owner/repo/collaborators/controller-bot/permission"],
                    0,
                    json.dumps({"permission": permission}),
                    "",
                )

                admission = GitHubAuthenticatedActor(self.ctx, runner=self.runner).require_admission("open-pr")

                self.assertEqual(admission.permission, permission)

    def test_admission_rejects_repo_metadata_permission_fallbacks(self) -> None:
        self.repo = subprocess.CompletedProcess(
            ["gh", "api", "repos/owner/repo/collaborators/controller-bot/permission"],
            0,
            json.dumps({"viewer_permission": "WRITE", "permissions": {"push": True}}),
            "",
        )

        with self.assertRaisesRegex(RuntimeError, "repo permission missing"):
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
