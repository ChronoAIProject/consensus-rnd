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

from codex_refactor_loop.context import LoopContext, LoopContextError


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
        with self.assertRaisesRegex(LoopContextError, "REPO_ROOT is unset"):
            LoopContext.load(env={}, cwd=self.repo)


if __name__ == "__main__":
    unittest.main()
