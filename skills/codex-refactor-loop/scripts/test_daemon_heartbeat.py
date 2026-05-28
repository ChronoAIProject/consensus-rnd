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
        chunks_file = self.tmp_root / "shell-chunks"
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
date_values="$REPO_ROOT/.refactor-loop/date-values"
printf '%s\\n' 100 101 102 103 > "$date_values"
date() {{
  local value
  value="$(sed -n '1p' "$date_values")"
  sed -n '2,$p' "$date_values" > "$date_values.next"
  mv "$date_values.next" "$date_values"
  printf '%s\\n' "$value"
}}
sleep() {{ printf '%s\\n' "$1" >> "{chunks_file}"; }}
daemon_heartbeat_beat
daemon_heartbeat_sleep 3
find "$REPO_ROOT/.refactor-loop/heartbeats" -name "*.tmp.*" -print
"""
        result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, check=False)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("", result.stdout.strip())
        heartbeat = self.repo / ".refactor-loop" / "heartbeats" / "shell-daemon.ts"
        self.assertEqual("1\n1\n1\n", chunks_file.read_text(encoding="utf-8"))
        self.assertEqual("103", heartbeat.read_text(encoding="utf-8").strip())


if __name__ == "__main__":
    unittest.main()
