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
from codex_refactor_loop.github_actor import GitHubAuthenticatedActor, write_github_actor_diagnostics_status


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

    def test_diagnostics_reads_login_without_repo_permission_or_authority(self) -> None:
        diagnostics = GitHubAuthenticatedActor(self.ctx, runner=self.runner).diagnostics()

        self.assertEqual(self.commands, [["gh", "api", "user"]])
        self.assertEqual("controller-bot", diagnostics.current_github_login)
        self.assertEqual("display-only", diagnostics.identity_authority)
        self.assertEqual("ok", diagnostics.status)
        self.assertEqual(
            {
                "current_github_login": "controller-bot",
                "identity_authority": "display-only",
                "github_login_status": "ok",
            },
            diagnostics.to_status_fields(),
        )

    def test_diagnostics_failure_is_local_status_not_admission_failure(self) -> None:
        self.user = subprocess.CompletedProcess(["gh", "api", "user"], 1, "", "bad credentials")

        diagnostics = GitHubAuthenticatedActor(self.ctx, runner=self.runner).diagnostics()

        self.assertEqual(self.commands, [["gh", "api", "user"]])
        self.assertEqual("", diagnostics.current_github_login)
        self.assertEqual("display-only", diagnostics.identity_authority)
        self.assertEqual("unavailable", diagnostics.status)
        self.assertIn("bad credentials", diagnostics.error)

    def test_diagnostics_runner_exception_is_unavailable_display_only_status(self) -> None:
        commands: list[list[str]] = []

        def failing_runner(cmd: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            commands.append(list(cmd))
            raise OSError("network down")

        diagnostics = GitHubAuthenticatedActor(self.ctx, runner=failing_runner).diagnostics()

        self.assertEqual(commands, [["gh", "api", "user"]])
        self.assertEqual("", diagnostics.current_github_login)
        self.assertEqual("display-only", diagnostics.identity_authority)
        self.assertEqual("unavailable", diagnostics.status)
        self.assertIn("network down", diagnostics.error)

    def test_diagnostics_invalid_user_json_is_invalid_display_only_status(self) -> None:
        self.user = subprocess.CompletedProcess(["gh", "api", "user"], 0, "{not json", "")

        diagnostics = GitHubAuthenticatedActor(self.ctx, runner=self.runner).diagnostics()

        self.assertEqual(self.commands, [["gh", "api", "user"]])
        self.assertEqual("", diagnostics.current_github_login)
        self.assertEqual("display-only", diagnostics.identity_authority)
        self.assertEqual("invalid", diagnostics.status)
        self.assertIn("invalid gh api user JSON", diagnostics.error)

    def test_diagnostics_missing_or_blank_login_is_missing_display_only_status(self) -> None:
        for payload in ({}, {"login": " "}):
            with self.subTest(payload=payload):
                self.commands.clear()
                self.user = subprocess.CompletedProcess(["gh", "api", "user"], 0, json.dumps(payload), "")

                diagnostics = GitHubAuthenticatedActor(self.ctx, runner=self.runner).diagnostics()

                self.assertEqual(self.commands, [["gh", "api", "user"]])
                self.assertEqual("", diagnostics.current_github_login)
                self.assertEqual("display-only", diagnostics.identity_authority)
                self.assertEqual("missing", diagnostics.status)
                self.assertIn("authenticated login missing", diagnostics.error)

    def test_diagnostics_helper_writes_rebuildable_local_status(self) -> None:
        diagnostics = write_github_actor_diagnostics_status(self.ctx, runner=self.runner)

        payload = json.loads((self.tmp / ".refactor-loop" / "state" / "active-controller-status.json").read_text(encoding="utf-8"))
        self.assertEqual("controller-bot", diagnostics.current_github_login)
        self.assertEqual("controller-bot", payload["current_github_login"])
        self.assertEqual("display-only", payload["identity_authority"])
        self.assertEqual("ok", payload["github_login_status"])

    def test_admission_fails_closed_without_authenticated_login(self) -> None:
        self.user = subprocess.CompletedProcess(["gh", "api", "user"], 0, "{}", "")

        with self.assertRaisesRegex(RuntimeError, "authenticated login missing"):
            GitHubAuthenticatedActor(self.ctx, runner=self.runner).require_admission("merge-pr")

    def test_admission_fails_closed_when_authenticated_user_api_fails(self) -> None:
        self.user = subprocess.CompletedProcess(["gh", "api", "user"], 1, "", "bad credentials")

        with self.assertRaisesRegex(RuntimeError, "gh api user failed: bad credentials"):
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

    def test_admission_fails_closed_when_repo_permission_api_fails(self) -> None:
        self.repo = subprocess.CompletedProcess(
            ["gh", "api", "repos/owner/repo/collaborators/controller-bot/permission"],
            1,
            "",
            "permission denied",
        )

        with self.assertRaisesRegex(RuntimeError, "collaborators/controller-bot/permission failed: permission denied"):
            GitHubAuthenticatedActor(self.ctx, runner=self.runner).require_admission("open-pr")

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
            "owner" + "_login",
            "GitHubActorIdentityProjection",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
