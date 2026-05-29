#!/usr/bin/env python3
"""Behavior tests for consensus-rnd-cli restart-daemons."""

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
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop import restart
from codex_refactor_loop.restart import DAEMON_COMMANDS, RestartConfig, RestartDaemons


DAEMON_NAMES = tuple(name for name, _command in DAEMON_COMMANDS)
FAKE_DAEMON = """import os, signal, sys, time
from pathlib import Path
repo = Path(os.environ["REPO_ROOT"])
name = os.environ["RESTART_DAEMON_NAME"]
hb = Path(os.environ["RESTART_DAEMON_HEARTBEAT_FILE"])
hb.parent.mkdir(parents=True, exist_ok=True)
hb.write_text(str(int(os.environ.get("TEST_HEARTBEAT_EPOCH", str(int(time.time()))))) + "\\n")
(repo / ".refactor-loop" / "logs" / f"{name}.starts").open("a", encoding="utf-8").write(str(os.getpid()) + "\\n")
running = True
def stop(_signum, _frame):
    global running
    running = False
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
while running:
    time.sleep(0.05)
"""


class RestartDaemonsBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="restart-daemons-test-"))
        self.repo = self.tmp_root / "repo"
        self.skill = self.tmp_root / "skill"
        for rel in (".refactor-loop/logs", ".refactor-loop/locks", ".refactor-loop/heartbeats"):
            (self.repo / rel).mkdir(parents=True, exist_ok=True)
        (self.skill / "scripts").mkdir(parents=True)
        (self.skill / "scripts" / "consensus-rnd-cli").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        (self.repo / ".refactor-loop" / "host.env").write_text(
            f'export REPO_ROOT="{self.repo}"\nexport GH_REPO_SLUG="example/repo"\nexport MAINTAINER_WHITELIST="maintainer"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.repo, skill_root=self.skill)
        self.config = RestartConfig(heartbeat_fresh_seconds=30, heartbeat_interval=1, stop_grace_seconds=1)
        self.helpers: list[RestartDaemons] = []

    def tearDown(self) -> None:
        for helper in self.helpers:
            for proc in helper._wrappers:
                self.terminate_proc(proc)
        for pid_file in (self.repo / ".refactor-loop" / "locks").glob("*.pid"):
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
            except Exception:
                continue
            self.terminate(pid)
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def run_helper(self) -> subprocess.CompletedProcess[str]:
        command = (sys.executable, "-c", FAKE_DAEMON)
        with mock.patch("codex_refactor_loop.restart.DAEMON_COMMANDS", tuple((name, command) for name in DAEMON_NAMES)):
            with mock.patch("codex_refactor_loop.restart.retain_logs", return_value=(0, 0, self.repo / ".refactor-loop" / "logs", False)):
                helper = RestartDaemons(self.ctx, self.config)
                self.helpers.append(helper)
                helper.run()
        return subprocess.CompletedProcess(["restart-daemons"], 0, "", "")

    def start_count(self, name: str) -> int:
        path = self.repo / ".refactor-loop" / "logs" / f"{name}.starts"
        return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0

    def read_pid(self, name: str) -> int:
        return int((self.repo / ".refactor-loop" / "locks" / f"{name}.pid").read_text(encoding="utf-8").strip())

    def stale_heartbeat(self, name: str) -> None:
        (self.repo / ".refactor-loop" / "heartbeats" / f"{name}.ts").write_text(f"{int(time.time()) - 120}\n", encoding="utf-8")

    def terminate(self, pid: int) -> None:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.time() + 2
        while time.time() < deadline:
            if restart._reap_child_if_exited(pid):
                return
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        restart._reap_child_if_exited(pid)

    def terminate_proc(self, proc: subprocess.Popen[bytes]) -> None:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass

    def test_restart_commands_use_single_cli_entrypoint_and_daemon_flag(self) -> None:
        for _name, command in DAEMON_COMMANDS:
            joined = " ".join(command)
            self.assertIn("consensus-rnd-cli", joined)
            self.assertIn("--daemon", command)
        self.assertEqual({name for name, _command in DAEMON_COMMANDS}, set(DAEMON_NAMES))

    def test_help_exits_without_starting_daemons(self) -> None:
        with mock.patch.object(restart.RestartDaemons, "run") as run:
            with self.assertRaises(SystemExit) as raised:
                restart.main(["--help"])
        self.assertEqual(0, raised.exception.code)
        run.assert_not_called()

    def test_idempotent_when_daemon_fresh(self) -> None:
        self.run_helper()
        self.run_helper()
        self.assertEqual(1, self.start_count("concurrency_monitor"))

    def test_restarts_when_heartbeat_stale(self) -> None:
        self.run_helper()
        old_pid = self.read_pid("comment-monitor")
        self.stale_heartbeat("comment-monitor")
        self.run_helper()
        new_pid = self.read_pid("comment-monitor")
        self.assertNotEqual(old_pid, new_pid)
        self.assertEqual(2, self.start_count("comment-monitor"))

    def test_restarts_when_heartbeat_missing(self) -> None:
        self.run_helper()
        (self.repo / ".refactor-loop" / "heartbeats" / "codex-progress-reporter.ts").unlink()
        self.run_helper()
        self.assertEqual(2, self.start_count("codex-progress-reporter"))

    def test_restarts_when_heartbeat_malformed(self) -> None:
        self.run_helper()
        (self.repo / ".refactor-loop" / "heartbeats" / "phase9_router_daemon.ts").write_text("not-a-timestamp\n", encoding="utf-8")
        self.run_helper()
        self.assertEqual(2, self.start_count("phase9_router_daemon"))

    def test_restarts_when_pid_dead(self) -> None:
        (self.repo / ".refactor-loop" / "locks" / "dev_sync_daemon.pid").write_text("999999\n", encoding="utf-8")
        (self.repo / ".refactor-loop" / "heartbeats" / "dev_sync_daemon.ts").write_text(f"{int(time.time())}\n", encoding="utf-8")
        self.run_helper()
        self.assertEqual(1, self.start_count("dev_sync_daemon"))


if __name__ == "__main__":
    unittest.main()
