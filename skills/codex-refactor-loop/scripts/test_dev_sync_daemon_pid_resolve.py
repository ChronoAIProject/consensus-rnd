#!/usr/bin/env python3
"""Behavior tests for dev_sync_daemon resolver PID registry detection."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[3]
os.environ.setdefault("REPO_ROOT", str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dev_sync_daemon


class DevSyncDaemonPidResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / ".refactor-loop" / "logs").mkdir(parents=True)
        (self.repo / ".refactor-loop" / "spawned").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_pid_file(self, pid: int) -> Path:
        reg = self.repo / ".refactor-loop" / "spawned" / "dev-sync-codex-test.pid"
        reg.write_text(
            f"pid={pid}\n"
            f"repo_root={self.repo}\n"
            f"log={self.repo / '.refactor-loop' / 'logs' / 'dev-sync-codex-test.log'}\n",
            encoding="utf-8",
        )
        return reg

    def resolve_in_flight(self) -> bool:
        with mock.patch.object(dev_sync_daemon, "MAIN_REPO", self.repo):
            return dev_sync_daemon.codex_resolve_in_flight()

    def terminate_process(self, proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def test_codex_resolve_skips_stale_pid_file(self) -> None:
        self.write_pid_file(999_999_999)

        self.assertFalse(self.resolve_in_flight())

    def test_codex_resolve_skips_dead_pid_file(self) -> None:
        proc = subprocess.Popen(["/bin/sleep", "0.1"])
        proc.wait(timeout=5)
        self.write_pid_file(proc.pid)

        self.assertFalse(self.resolve_in_flight())

    def test_codex_resolve_skips_foreign_pid_file(self) -> None:
        proc = subprocess.Popen(["/bin/sleep", "5"])
        self.addCleanup(self.terminate_process, proc)
        self.write_pid_file(proc.pid)

        self.assertFalse(self.resolve_in_flight())

    def test_codex_resolve_counts_live_codex_pid(self) -> None:
        fake_codex = self.repo / "codex"
        fake_codex.symlink_to("/bin/sleep")
        proc = subprocess.Popen([str(fake_codex), "5"])
        self.addCleanup(self.terminate_process, proc)
        self.write_pid_file(proc.pid)

        self.assertTrue(self.resolve_in_flight())


if __name__ == "__main__":
    unittest.main()
