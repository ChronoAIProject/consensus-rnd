#!/usr/bin/env python3
"""Behavior tests for LoopContext host boundary loading."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import HostEnvLocator, LoopContext, LoopContextError


class LoopContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="loop-context-test-"))
        self.repo = self.tmp_root / "repo"
        self.repo.mkdir()
        (self.repo / ".refactor-loop").mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def write_host_env(self, text: str) -> None:
        (self.repo / ".refactor-loop" / "host.env").write_text(text, encoding="utf-8")

    def write_host_owned_env(self, text: str, relative: str = ".config/consensus-rnd/host.env") -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_loads_host_env_and_stable_paths(self) -> None:
        self.write_host_env(
            "\n".join(
                (
                    f'export REPO_ROOT="{self.repo}"',
                    'export GH_REPO_SLUG="owner/repo"',
                    'export MAINTAINER_WHITELIST="alice,bob"',
                    "",
                )
            )
        )

        ctx = LoopContext.load(cwd=self.repo, env={})

        self.assertEqual(self.repo.resolve(), ctx.repo_root)
        self.assertEqual("owner/repo", ctx.gh_repo_slug)
        self.assertEqual("alice,bob", ctx.host_env["MAINTAINER_WHITELIST"])
        repo = self.repo.resolve()
        self.assertEqual(repo / ".refactor-loop" / "dispatch-queue", ctx.paths.dispatch_queue)
        self.assertEqual(repo / ".refactor-loop" / ".controller-pending-events.log", ctx.paths.pending_events)
        self.assertEqual(repo / ".refactor-loop" / "state" / "statusline-snapshot.json", ctx.paths.statusline_snapshot)
        self.assertEqual(repo / ".refactor-loop" / "state" / "recent-pr-merges.json", ctx.paths.recent_pr_merges)

    def test_explicit_consensus_rnd_host_env_takes_precedence_over_refactor_loop(self) -> None:
        self.write_host_env(
            "\n".join(
                (
                    f'export REPO_ROOT="{self.repo}"',
                    'export GH_REPO_SLUG="legacy/repo"',
                )
            )
        )
        explicit = self.write_host_owned_env(
            "\n".join(
                (
                    f'export REPO_ROOT="{self.repo}"',
                    'export GH_REPO_SLUG="owner/repo"',
                    'export BUILD_CMD="make build"',
                )
            )
        )

        ctx = LoopContext.load(cwd=self.repo, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})

        self.assertEqual("owner/repo", ctx.gh_repo_slug)
        self.assertEqual("make build", ctx.host_env["BUILD_CMD"])
        self.assertEqual(explicit.resolve(), HostEnvLocator.resolve(self.repo, {"CONSENSUS_RND_HOST_ENV": str(explicit)}, self.repo).path)

    def test_consensus_rnd_host_env_accepts_repo_contained_absolute_path(self) -> None:
        explicit = self.write_host_owned_env(
            "\n".join(
                (
                    f'export REPO_ROOT="{self.repo}"',
                    'export GH_REPO_SLUG="owner/repo"',
                )
            )
        )

        ctx = LoopContext.load(cwd=self.repo, env={"CONSENSUS_RND_HOST_ENV": str(explicit)})

        self.assertEqual(self.repo.resolve(), ctx.repo_root)
        self.assertEqual("owner/repo", ctx.gh_repo_slug)

    def test_consensus_rnd_host_env_rejects_repo_outside_and_parent_segments(self) -> None:
        outside = self.tmp_root / "outside.env"
        outside.write_text(f'export REPO_ROOT="{self.repo}"\n', encoding="utf-8")

        cases = (str(outside), "../outside.env", "", ".config/../host.env")
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(LoopContextError):
                    LoopContext.load(cwd=self.repo, env={"CONSENSUS_RND_HOST_ENV": raw})

    def test_legacy_refactor_loop_host_env_still_loads(self) -> None:
        self.write_host_env(
            "\n".join(
                (
                    f'export REPO_ROOT="{self.repo}"',
                    'export GH_REPO_SLUG="legacy/repo"',
                )
            )
        )

        ctx = LoopContext.load(cwd=self.repo, env={})

        self.assertEqual("legacy/repo", ctx.gh_repo_slug)

    def test_invalid_repo_root_override_fails_closed(self) -> None:
        other = self.tmp_root / "other"
        other.mkdir()
        self.write_host_env(f'export REPO_ROOT="{other}"\n')

        with self.assertRaisesRegex(LoopContextError, "points outside resolved repo root"):
            LoopContext.load(repo_root=self.repo, cwd=self.repo, env={})

    def test_invalid_github_slug_fails_closed(self) -> None:
        self.write_host_env(f'export REPO_ROOT="{self.repo}"\nexport GH_REPO_SLUG="repo-only"\n')

        with self.assertRaisesRegex(LoopContextError, "GH_REPO_SLUG must be OWNER/REPO"):
            LoopContext.load(cwd=self.repo, env={})

    def test_allow_git_root_fallback_is_read_only_only(self) -> None:
        completed = subprocess.CompletedProcess(["git"], 0, stdout=f"{self.repo}\n", stderr="")
        env = {"ALLOW_GIT_ROOT_FALLBACK": "1"}
        with mock.patch("codex_refactor_loop.context.subprocess.run", return_value=completed):
            ctx = LoopContext.load(env=env, read_only=True, cwd=self.repo)
        self.assertEqual(self.repo.resolve(), ctx.repo_root)
        self.assertEqual("git", ctx.repo_root_source)

        with mock.patch("codex_refactor_loop.context.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(LoopContextError, "only allowed for read-only"):
                LoopContext.load(env=env, read_only=False, cwd=self.repo)

    def test_missing_repo_root_fails_without_fallback(self) -> None:
        with self.assertRaisesRegex(LoopContextError, "host-owned consensus-rnd host.env"):
            LoopContext.load(env={}, cwd=self.repo)

    def test_durable_artifact_path_writes_posix_repo_relative_text(self) -> None:
        ctx = LoopContext.load(repo_root=self.repo, env={})
        path = self.repo / ".refactor-loop" / "logs" / "x.log"
        self.assertEqual(".refactor-loop/logs/x.log", ctx.durable_artifact_path(path))
        self.assertNotIn(str(self.repo), ctx.durable_artifact_path(path))

    def test_durable_artifact_path_rejects_repo_outside_paths(self) -> None:
        ctx = LoopContext.load(repo_root=self.repo, env={})
        outside = self.tmp_root / "outside.log"
        with self.assertRaisesRegex(LoopContextError, "outside REPO_ROOT"):
            ctx.durable_artifact_path(outside)

    def test_artifact_execution_path_resolves_against_repo_root(self) -> None:
        ctx = LoopContext.load(repo_root=self.repo, env={})
        self.assertEqual(
            self.repo.resolve() / ".refactor-loop" / "logs" / "x.log",
            ctx.artifact_execution_path(".refactor-loop/logs/x.log"),
        )

    def test_artifact_execution_path_rejects_absolute_or_parent_escape(self) -> None:
        ctx = LoopContext.load(repo_root=self.repo, env={})
        for text in ("/tmp/x", "../x", ".refactor-loop/../outside", r".refactor-loop\\logs\\x.log", ""):
            with self.subTest(text=text):
                with self.assertRaisesRegex(LoopContextError, "repo-relative POSIX"):
                    ctx.artifact_execution_path(text)

    def test_artifact_execution_path_rejects_symlink_escape_after_resolve(self) -> None:
        ctx = LoopContext.load(repo_root=self.repo, env={})
        outside_dir = self.tmp_root / "outside"
        outside_dir.mkdir()
        (self.repo / "link").symlink_to(outside_dir, target_is_directory=True)

        with self.assertRaisesRegex(LoopContextError, "escapes REPO_ROOT"):
            ctx.artifact_execution_path("link/out.log")


if __name__ == "__main__":
    unittest.main()
