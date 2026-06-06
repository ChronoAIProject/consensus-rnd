#!/usr/bin/env python3
"""Behavior tests for codex_refactor_loop/controller_actions.py safe_push + safe_sync_main helpers.

Refactor (iter4/skill-safe-push-helper): Old pattern: controller manually ran
pull --rebase after non-fast-forward push. New principle: safe_push includes
fetch and rebase --autostash when needed before push; safe_sync_main
proactively catches up with remote before commit, avoiding the rebase path.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]
sys.path.insert(0, str(REPO_ROOT / "skills" / "codex-refactor-loop" / "scripts"))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.controller_actions import ControllerActions


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


class SafePushHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bare = self.root / "remote.git"
        self.local = self.root / "local"
        self.other = self.root / "other"

        # init bare remote
        subprocess.run(["git", "init", "--bare", "-b", "main", str(self.bare)], check=True, capture_output=True)

        # init local with one commit + push
        subprocess.run(["git", "init", "-b", "main", str(self.local)], check=True, capture_output=True)
        self._configure_user(self.local)
        (self.local / "README.md").write_text("v0\n", encoding="utf-8")
        git(self.local, "add", ".")
        git(self.local, "commit", "-m", "init")
        git(self.local, "remote", "add", "origin", str(self.bare))
        git(self.local, "push", "-u", "origin", "main")

        # clone "other" peer that will create the divergence (e.g., dev_sync_daemon)
        subprocess.run(["git", "clone", str(self.bare), str(self.other)], check=True, capture_output=True)
        self._configure_user(self.other)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _configure_user(self, path: Path) -> None:
        git(path, "config", "user.email", "test@example.com")
        git(path, "config", "user.name", "Test")

    def _run_helper(self, fn_call: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        host_env = self.local / ".refactor-loop" / "host.env"
        host_env.parent.mkdir(parents=True, exist_ok=True)
        host_env.write_text(f"export REPO_ROOT={self.local}\n", encoding="utf-8")
        env.update(
            {
                "REPO_ROOT": str(self.local),
                "CONSENSUS_RND_HOST_ENV": ".refactor-loop/host.env",
            }
        )
        parts = fn_call.split()
        stdout = StringIO()
        stderr = StringIO()
        old_env = os.environ.copy()
        try:
            os.environ.clear()
            os.environ.update(env)
            actions = ControllerActions(LoopContext.load(env=env, cwd=self.local))
            remote = parts[1] if len(parts) > 1 else "origin"
            branch = parts[2] if len(parts) > 2 else ""
            with redirect_stdout(stdout), redirect_stderr(stderr):
                if parts[0] == "safe_push":
                    returncode = actions.safe_push(remote, branch)
                else:
                    returncode = actions.safe_sync_main(remote, branch)
        finally:
            os.environ.clear()
            os.environ.update(old_env)
        return subprocess.CompletedProcess(
            ["controller-internal", *parts],
            returncode,
            stdout.getvalue(),
            stderr.getvalue(),
        )

    def test_safe_push_succeeds_when_remote_up_to_date(self) -> None:
        (self.local / "a.txt").write_text("a", encoding="utf-8")
        git(self.local, "add", ".")
        git(self.local, "commit", "-m", "add a")

        result = self._run_helper("safe_push origin main")

        self.assertEqual(result.returncode, 0, f"stderr={result.stderr}")
        bare_log = git(self.bare, "log", "--oneline", "main")
        self.assertIn("add a", bare_log.stdout)

    def test_safe_push_auto_rebases_when_remote_advances(self) -> None:
        # Simulate dev_sync_daemon pushing a sibling commit while we work locally.
        (self.other / "remote-side.txt").write_text("remote\n", encoding="utf-8")
        git(self.other, "add", ".")
        git(self.other, "commit", "-m", "remote-side change")
        push = git(self.other, "push", "origin", "main")
        self.assertEqual(push.returncode, 0, push.stderr)

        # Local makes its own commit oblivious to the remote advance.
        (self.local / "local-side.txt").write_text("local\n", encoding="utf-8")
        git(self.local, "add", ".")
        git(self.local, "commit", "-m", "local-side change")

        # Without safe_push, plain push would be rejected.
        plain_push = git(self.local, "push", "origin", "main")
        self.assertNotEqual(plain_push.returncode, 0)
        self.assertIn("rejected", plain_push.stderr + plain_push.stdout)

        # safe_push must auto-rebase then push.
        result = self._run_helper("safe_push origin main")
        self.assertEqual(result.returncode, 0, f"stdout={result.stdout}\nstderr={result.stderr}")
        bare_log = git(self.bare, "log", "--oneline", "main")
        self.assertIn("local-side change", bare_log.stdout)
        self.assertIn("remote-side change", bare_log.stdout)

    def test_safe_sync_main_fast_forwards_local_to_remote(self) -> None:
        (self.other / "x.txt").write_text("x", encoding="utf-8")
        git(self.other, "add", ".")
        git(self.other, "commit", "-m", "remote-only")
        git(self.other, "push", "origin", "main")

        local_before = git(self.local, "rev-parse", "HEAD").stdout.strip()

        result = self._run_helper("safe_sync_main origin main")
        self.assertEqual(result.returncode, 0, result.stderr)

        local_after = git(self.local, "rev-parse", "HEAD").stdout.strip()
        self.assertNotEqual(local_before, local_after, "safe_sync_main should advance local HEAD")
        log = git(self.local, "log", "--oneline").stdout
        self.assertIn("remote-only", log)

    def test_safe_sync_main_noop_when_already_current(self) -> None:
        result = self._run_helper("safe_sync_main origin main")
        self.assertEqual(result.returncode, 0)
        self.assertIn("already up to date", result.stdout)

    def test_safe_push_aborts_on_detached_head(self) -> None:
        commit_sha = git(self.local, "rev-parse", "HEAD").stdout.strip()
        git(self.local, "checkout", "--detach", commit_sha)

        result = self._run_helper("safe_push origin")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot determine branch", result.stderr)


if __name__ == "__main__":
    unittest.main()
