#!/usr/bin/env python3
"""Behavior tests for actor-owned daemon heartbeat lease helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.heartbeat import DaemonHeartbeatLease


class DaemonHeartbeatLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="daemon-heartbeat-test-"))
        self.repo = self.tmp_root / "repo"
        (self.repo / ".refactor-loop" / "heartbeats").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_python_lease_atomic_beat_and_chunked_sleep(self) -> None:
        times = iter([1000, 1001, 1002, 1003])
        slept: list[float] = []
        lease = DaemonHeartbeatLease(
            "python-daemon",
            self.repo,
            heartbeat_interval=2,
            clock=lambda: next(times),
            sleeper=slept.append,
        )

        lease.beat()
        self.assertEqual("1000", (self.repo / ".refactor-loop" / "heartbeats" / "python-daemon.ts").read_text().strip())

        lease.sleep_with_lease(5)

        self.assertEqual([2.0, 2.0, 1.0], slept)
        self.assertEqual("1003", (self.repo / ".refactor-loop" / "heartbeats" / "python-daemon.ts").read_text().strip())
        self.assertEqual([], list((self.repo / ".refactor-loop" / "heartbeats").glob("*.tmp.*")))

    def test_lease_requires_repo_root_context_or_explicit_heartbeat_file(self) -> None:
        old_env = os.environ.copy()
        try:
            os.environ.clear()
            with self.assertRaisesRegex(Exception, "REPO_ROOT is unset"):
                DaemonHeartbeatLease("python-daemon")

            explicit = self.tmp_root / "explicit.ts"
            lease = DaemonHeartbeatLease("python-daemon", heartbeat_file=explicit, clock=lambda: 1000)
            lease.beat()
            self.assertEqual("1000", explicit.read_text(encoding="utf-8").strip())

            (self.repo / ".refactor-loop" / "host.env").write_text(f'export REPO_ROOT="{self.repo}"\n', encoding="utf-8")
            os.environ["REPO_ROOT"] = str(self.repo)
            lease = DaemonHeartbeatLease("python-daemon", clock=lambda: 1001)
            lease.beat()
            self.assertEqual("1001", (self.repo / ".refactor-loop" / "heartbeats" / "python-daemon.ts").read_text().strip())
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_heartbeat_source_has_no_cwd_repo_root_default(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "heartbeat.py").read_text(encoding="utf-8")
        self.assertIn("LoopContext.load", source)
        self.assertNotIn('os.environ.get("REPO_ROOT", ".")', source)

if __name__ == "__main__":
    unittest.main()
