#!/usr/bin/env python3
"""Behavior and source-regression tests for consensus-rnd-cli log-retention."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
CLI = SCRIPT_DIR / "consensus-rnd-cli"

sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.retention import RETENTION_TTL_HOURS, retain_logs


class LogRetentionBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="log-retention-test-"))
        self.repo = self.tmp_root / "repo"
        self.logs = self.repo / ".refactor-loop" / "logs"
        self.logs.mkdir(parents=True)
        (self.repo / ".refactor-loop" / "host.env").write_text(f'export REPO_ROOT="{self.repo}"\n', encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def write_file(self, rel: str, text: str, age_hours: float) -> Path:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        ts = time.time() - age_hours * 60 * 60
        os.utime(path, (ts, ts))
        return path

    def run_cli(self, *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        run_env = os.environ.copy()
        run_env.pop("REPO_ROOT", None)
        if env:
            run_env.update(env)
        return subprocess.run(
            [sys.executable, str(CLI), "log-retention"],
            cwd=cwd or self.repo,
            env=run_env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_deletes_only_log_files_older_than_24h(self) -> None:
        old_log = self.write_file(".refactor-loop/logs/old.log", "done\nEXIT=0\n", 25)
        young_log = self.write_file(".refactor-loop/logs/young.log", "done\nEXIT=0\n", 1)
        old_non_log = self.write_file(".refactor-loop/logs/old.txt", "keep\n", 25)
        run_artifact = self.write_file(".refactor-loop/runs/old.log", "keep\n", 25)
        deleted, kept, target, missing = retain_logs(self.repo)
        self.assertEqual((deleted, missing), (1, False))
        self.assertEqual(target.resolve(), self.logs.resolve())
        self.assertGreaterEqual(kept, 2)
        self.assertFalse(old_log.exists())
        self.assertTrue(young_log.exists())
        self.assertTrue(old_non_log.exists())
        self.assertTrue(run_artifact.exists())

    def test_cli_uses_host_env_and_reports_summary(self) -> None:
        self.write_file(".refactor-loop/logs/old.log", "done\nEXIT=0\n", 25)
        result = self.run_cli()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("log_retention: ttl_hours=24 deleted=1", result.stdout)

    def test_refuses_without_repo_root_or_host_env(self) -> None:
        isolated = self.tmp_root / "isolated"
        isolated.mkdir()
        result = self.run_cli(cwd=isolated)
        self.assertEqual(2, result.returncode)
        self.assertIn("REPO_ROOT is unset", result.stderr)

    def test_missing_log_directory_is_noop(self) -> None:
        shutil.rmtree(self.logs)
        deleted, kept, target, missing = retain_logs(self.repo)
        self.assertEqual((deleted, kept, target.resolve(), missing), (0, 0, self.logs.resolve(), True))

    def test_keeps_symlink_and_non_regular_log_paths(self) -> None:
        old_target = self.write_file(".refactor-loop/logs/target.log", "target\n", 25)
        symlink_log = self.logs / "linked.log"
        symlink_log.symlink_to(old_target)
        fifo_log = self.logs / "pipe.log"
        os.mkfifo(fifo_log)
        deleted, kept, _target, _missing = retain_logs(self.repo)
        self.assertEqual(deleted, 1)
        self.assertEqual(kept, 2)
        self.assertTrue(symlink_log.is_symlink())
        self.assertTrue(fifo_log.exists())
        self.assertFalse(old_target.exists())


class LogRetentionSourceRegressionTests(unittest.TestCase):
    def test_helper_contract_is_narrow_direct_delete_only(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "retention.py").read_text(encoding="utf-8")
        self.assertIn("RETENTION_TTL_HOURS = 24", text)
        self.assertIn(".refactor-loop", text)
        self.assertIn("logs", text)
        self.assertIn("path.unlink", text)
        for token in ("archive", "last_processed", "while True", "nohup", "gh ", "git ", "commit", "push", "merge", "label"):
            with self.subTest(token=token):
                self.assertNotIn(token, text)

    def test_restart_helper_hooks_retention_before_daemon_start(self) -> None:
        restart = (SCRIPT_DIR / "codex_refactor_loop" / "restart.py").read_text(encoding="utf-8")
        self.assertIn("_run_log_retention", restart)
        self.assertLess(restart.index("self._run_log_retention()"), restart.index("for name, command in DAEMON_COMMANDS"))
        self.assertIn("log_retention warning: helper failed; continuing daemon restart", restart)
        self.assertEqual(RETENTION_TTL_HOURS, 24)


if __name__ == "__main__":
    unittest.main()
