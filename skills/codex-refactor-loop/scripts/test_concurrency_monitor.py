#!/usr/bin/env python3
"""Behavior tests for concurrency_monitor PID registry counting."""

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

import concurrency_monitor


class ConcurrencyMonitorPidRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / ".refactor-loop" / "logs").mkdir(parents=True)
        (self.repo / ".refactor-loop" / "spawned").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_pid_file(self, pid_text: str, log_path: Path | None = None, *, empty: bool = False) -> Path:
        reg = self.repo / ".refactor-loop" / "spawned" / "test-codex.pid"
        if empty:
            reg.write_text("", encoding="utf-8")
            return reg
        if log_path is None:
            log_path = self.repo / ".refactor-loop" / "logs" / "test-codex.log"
        reg.write_text(
            f"pid={pid_text}\n"
            f"repo_root={self.repo}\n"
            f"log={log_path}\n",
            encoding="utf-8",
        )
        return reg

    def count_in_flight(self) -> int:
        with (
            mock.patch.object(concurrency_monitor, "REPO_ROOT", self.repo),
            mock.patch.object(
                concurrency_monitor,
                "SPAWNED_DIR",
                self.repo / ".refactor-loop" / "spawned",
            ),
        ):
            return concurrency_monitor.count_in_flight_codex()

    def terminate_process(self, proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def start_fake_codex(self) -> subprocess.Popen:
        fake_codex = self.repo / "codex"
        fake_codex.symlink_to("/bin/sleep")
        proc = subprocess.Popen([str(fake_codex), "5"])
        self.addCleanup(self.terminate_process, proc)
        return proc

    def test_count_in_flight_includes_live_pid_file(self) -> None:
        proc = self.start_fake_codex()
        self.write_pid_file(str(proc.pid))

        self.assertEqual(self.count_in_flight(), 1)

    def test_count_in_flight_skips_stale_pid_file(self) -> None:
        self.write_pid_file("999999999")

        self.assertEqual(self.count_in_flight(), 0)

    def test_count_in_flight_skips_dead_pid_file(self) -> None:
        proc = subprocess.Popen(["/bin/sleep", "0.1"])
        proc.wait(timeout=5)
        self.write_pid_file(str(proc.pid))

        self.assertEqual(self.count_in_flight(), 0)

    def test_count_in_flight_skips_malformed_pid_file(self) -> None:
        cases = [
            ("empty", "", True),
            ("non_numeric", "not-a-pid", False),
            ("zero", "0", False),
        ]
        for name, pid_text, empty in cases:
            with self.subTest(name=name):
                spawned = self.repo / ".refactor-loop" / "spawned"
                for path in spawned.glob("*.pid"):
                    path.unlink()
                self.write_pid_file(pid_text, empty=empty)

                self.assertEqual(self.count_in_flight(), 0)

    def test_count_in_flight_skips_foreign_pid_file(self) -> None:
        proc = subprocess.Popen(["/bin/sleep", "5"])
        self.addCleanup(self.terminate_process, proc)
        self.write_pid_file(str(proc.pid))

        self.assertEqual(self.count_in_flight(), 0)

    def test_count_in_flight_skips_repo_escaping_log_path(self) -> None:
        proc = self.start_fake_codex()
        outside_log = Path(self.tmp.name).parent / "outside-codex.log"
        self.write_pid_file(str(proc.pid), outside_log)

        self.assertEqual(self.count_in_flight(), 0)


if __name__ == "__main__":
    unittest.main()
