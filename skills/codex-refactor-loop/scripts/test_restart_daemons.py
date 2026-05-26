#!/usr/bin/env python3
"""Behavior tests for restart-daemons.sh."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
HELPER = SCRIPT_DIR / "restart-daemons.sh"
DAEMON_NAMES = (
    "concurrency_monitor",
    "comment-monitor",
    "codex-progress-reporter",
    "dev_sync_daemon",
    "triage-monitor",
)

# Sleep allowlist for this test file:
# - The dummy daemons must stay alive long enough for restart-daemons.sh to observe them.
# - Polling waits for the helper to create pid/heartbeat files across process boundaries.
DUMMY_DAEMON_SLEEP_SECONDS = 0.1
SHELL_DAEMON_SLEEP_SECONDS = 1
POLL_INTERVAL_SECONDS = 0.1
RACE_SETTLE_SECONDS = 0.5


PYTHON_DAEMON = f"""#!/usr/bin/env python3
import os
import signal
import sys
import time
from pathlib import Path

repo = Path(os.environ["REPO_ROOT"])
name = os.environ.get("RESTART_DAEMON_NAME", Path(sys.argv[0]).stem)
with (repo / ".refactor-loop" / "logs" / f"{{name}}.starts").open("a", encoding="utf-8") as fh:
    fh.write(f"{{os.getpid()}}\\n")

running = True
def stop(_signum, _frame):
    global running
    running = False
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

while running:
    time.sleep({DUMMY_DAEMON_SLEEP_SECONDS})
"""


SHELL_DAEMON = f"""#!/usr/bin/env bash
set -u
echo "$$" >> "$REPO_ROOT/.refactor-loop/logs/${{RESTART_DAEMON_NAME}}.starts"
trap 'exit 0' TERM INT
while true; do sleep {SHELL_DAEMON_SLEEP_SECONDS}; done
"""


class RestartDaemonsBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="restart-daemons-test-"))
        self.repo = self.tmp_root / "repo"
        self.skill = self.tmp_root / "skill"
        for rel in (
            ".refactor-loop/logs",
            ".refactor-loop/locks",
            ".refactor-loop/heartbeats",
        ):
            (self.repo / rel).mkdir(parents=True, exist_ok=True)
        (self.skill / "scripts").mkdir(parents=True, exist_ok=True)
        shutil.copy2(HELPER, self.skill / "scripts" / "restart-daemons.sh")
        (self.skill / "scripts" / "restart-daemons.sh").chmod(0o755)
        (self.repo / ".refactor-loop" / "host.env").write_text(
            "\n".join(
                (
                    f'export REPO_ROOT="{self.repo}"',
                    'export GH_REPO_SLUG="example/repo"',
                    'export MAINTAINER_WHITELIST="maintainer"',
                    "",
                )
            ),
            encoding="utf-8",
        )
        for script in ("concurrency_monitor.py", "dev_sync_daemon.py"):
            self._write_executable(self.skill / "scripts" / script, PYTHON_DAEMON)
        for script in ("comment-monitor.sh", "codex-progress-reporter.sh", "triage-monitor.sh"):
            self._write_executable(self.skill / "scripts" / script, SHELL_DAEMON)

    def tearDown(self) -> None:
        self._cleanup_daemons()
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def _write_executable(self, path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def _cleanup_daemons(self) -> None:
        lock_dir = self.repo / ".refactor-loop" / "locks"
        if not lock_dir.exists():
            return
        for pid_file in lock_dir.glob("*.pid"):
            pid = self._read_pid(pid_file)
            if pid is not None:
                self._kill_process(pid)

    def _read_pid(self, pid_file: Path) -> int | None:
        try:
            raw = pid_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        if not raw.isdigit():
            return None
        return int(raw)

    def _kill_process(self, pid: int) -> None:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except PermissionError:
            return
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if not self._pid_alive(pid):
                return
            time.sleep(POLL_INTERVAL_SECONDS)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def _pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _run_helper(self) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.pop("REPO_ROOT", None)
        env.update(
            {
                "RESTART_DAEMONS_HEARTBEAT_FRESH_SECONDS": "30",
                "RESTART_DAEMONS_HEARTBEAT_INTERVAL": "1",
                "RESTART_DAEMONS_STOP_GRACE_SECONDS": "1",
            }
        )
        return subprocess.run(
            ["bash", str(self.skill / "scripts" / "restart-daemons.sh")],
            cwd=self.repo,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

    def _start_count(self, name: str) -> int:
        starts = self.repo / ".refactor-loop" / "logs" / f"{name}.starts"
        if not starts.exists():
            return 0
        return len(starts.read_text(encoding="utf-8").splitlines())

    def _wait_for_starts(self, name: str, expected: int) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self._start_count(name) >= expected:
                return
            time.sleep(POLL_INTERVAL_SECONDS)
        self.fail(f"timed out waiting for {name} starts >= {expected}")

    def test_idempotent_when_daemon_fresh(self) -> None:
        self._run_helper()
        self._wait_for_starts("concurrency_monitor", 1)

        self._run_helper()

        self.assertEqual(1, self._start_count("concurrency_monitor"))

    def test_restarts_when_heartbeat_stale(self) -> None:
        self._run_helper()
        self._wait_for_starts("comment-monitor", 1)
        stale_ts = int(time.time()) - 120
        (self.repo / ".refactor-loop" / "heartbeats" / "comment-monitor.ts").write_text(
            f"{stale_ts}\n",
            encoding="utf-8",
        )

        self._run_helper()

        self._wait_for_starts("comment-monitor", 2)
        self.assertEqual(2, self._start_count("comment-monitor"))

    def test_restarts_when_pid_dead(self) -> None:
        (self.repo / ".refactor-loop" / "locks" / "dev_sync_daemon.pid").write_text(
            "999999\n",
            encoding="utf-8",
        )
        (self.repo / ".refactor-loop" / "heartbeats" / "dev_sync_daemon.ts").write_text(
            f"{int(time.time())}\n",
            encoding="utf-8",
        )

        self._run_helper()

        self._wait_for_starts("dev_sync_daemon", 1)
        pid = self._read_pid(self.repo / ".refactor-loop" / "locks" / "dev_sync_daemon.pid")
        self.assertIsNotNone(pid)
        self.assertTrue(self._pid_alive(pid))

    def test_no_double_spawn_under_race(self) -> None:
        env = os.environ.copy()
        env.pop("REPO_ROOT", None)
        env.update(
            {
                "RESTART_DAEMONS_HEARTBEAT_FRESH_SECONDS": "30",
                "RESTART_DAEMONS_HEARTBEAT_INTERVAL": "1",
                "RESTART_DAEMONS_STOP_GRACE_SECONDS": "1",
            }
        )
        command = ["bash", str(self.skill / "scripts" / "restart-daemons.sh")]

        first = subprocess.Popen(command, cwd=self.repo, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        second = subprocess.Popen(command, cwd=self.repo, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        first_stdout, first_stderr = first.communicate(timeout=10)
        second_stdout, second_stderr = second.communicate(timeout=10)
        self.assertEqual(0, first.returncode, first_stdout + first_stderr)
        self.assertEqual(0, second.returncode, second_stdout + second_stderr)

        self._wait_for_starts("triage-monitor", 1)
        time.sleep(RACE_SETTLE_SECONDS)
        self.assertEqual(1, self._start_count("triage-monitor"))
        self.assertEqual(1, self._start_count("concurrency_monitor"))


if __name__ == "__main__":
    unittest.main()
