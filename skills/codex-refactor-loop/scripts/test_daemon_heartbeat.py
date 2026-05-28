#!/usr/bin/env python3
"""Behavior tests for actor-owned daemon heartbeat lease helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from daemon_heartbeat import DaemonHeartbeatLease


SCRIPT_DIR = Path(__file__).resolve().parent


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

    def test_shell_lease_atomic_beat_and_chunked_sleep(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "REPO_ROOT": str(self.repo),
                "RESTART_DAEMON_NAME": "shell-daemon",
                "RESTART_DAEMON_HEARTBEAT_INTERVAL": "1",
            }
        )
        script = f"""
set -u
source "{SCRIPT_DIR / "daemon_heartbeat.sh"}"
sleep() {{ :; }}
daemon_heartbeat_beat
first="$(cat "$REPO_ROOT/.refactor-loop/heartbeats/shell-daemon.ts")"
daemon_heartbeat_sleep 3
last="$(cat "$REPO_ROOT/.refactor-loop/heartbeats/shell-daemon.ts")"
case "$first:$last" in
  *[!0-9:]*|"") exit 3 ;;
esac
find "$REPO_ROOT/.refactor-loop/heartbeats" -name "*.tmp.*" -print
"""
        result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, check=False)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("", result.stdout.strip())
        heartbeat = self.repo / ".refactor-loop" / "heartbeats" / "shell-daemon.ts"
        self.assertTrue(heartbeat.read_text(encoding="utf-8").strip().isdigit())


if __name__ == "__main__":
    unittest.main()
