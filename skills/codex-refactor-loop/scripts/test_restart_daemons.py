#!/usr/bin/env python3
"""Behavior tests for consensus-rnd-cli restart-daemons."""

from __future__ import annotations

import os
import json
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
(repo / ".refactor-loop" / "logs" / f"{name}.starts").open("a", encoding="utf-8").write(str(os.getpid()) + "\\n")
hb.write_text(str(int(os.environ.get("TEST_HEARTBEAT_EPOCH", str(int(time.time()))))) + "\\n")
running = True
def stop(_signum, _frame):
    global running
    running = False
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
while running:
    signal.pause()
"""


# Refactor (iter204/issue-204):
#   Old pattern: restart-daemons kill daemon 后读 stale pidfile + 90s 内 heartbeat 误判存活、跳过 respawn(实测手 kill 5 daemon 后未 respawn 造成 outage);且无代码变更重启(daemon import 缓存旧代码)。
#   New principle: 按 r2 consensus structural 锁定:引入 restart-daemons 代码指纹 artifact(检测 daemon 脚本 mtime/hash vs 启动时,变更则 force-restart)+ 值对象边界,kill 后不误判 stale-pid 存活。配套 behavior(指纹变更触发 restart、kill 后正确 respawn)+ source-regression 测试。不扩大 process authority surface。
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

    def assert_start_count(self, name: str, expected: int) -> None:
        self.assertEqual(expected, self.start_count(name))

    def read_pid(self, name: str) -> int:
        return int((self.repo / ".refactor-loop" / "locks" / f"{name}.pid").read_text(encoding="utf-8").strip())

    def fingerprint_path(self, name: str) -> Path:
        return self.repo / ".refactor-loop" / "locks" / f"{name}.fingerprint.json"

    def stale_heartbeat(self, name: str) -> None:
        (self.repo / ".refactor-loop" / "heartbeats" / f"{name}.ts").write_text(f"{int(time.time()) - 120}\n", encoding="utf-8")

    def terminate(self, pid: int) -> None:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            os.waitpid(pid, 0)
            return
        except ChildProcessError:
            pass
        if not restart.pid_alive(pid):
            return
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
        self.assertEqual(6, len(DAEMON_COMMANDS))
        self.assertIn("closed_label_reconciler", DAEMON_NAMES)
        for _name, command in DAEMON_COMMANDS:
            joined = " ".join(command)
            self.assertIn("consensus-rnd-cli", joined)
            self.assertIn("--daemon", command)
        self.assertIn(("closed_label_reconciler", ("python3", "{skill_root}/scripts/consensus-rnd-cli", "closed-label-reconciler", "--daemon")), DAEMON_COMMANDS)
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
        self.assertTrue(self.fingerprint_path("concurrency_monitor").exists())

    def test_restarts_dead_wrapper_pid_even_with_fresh_heartbeat(self) -> None:
        self.run_helper()
        self.assert_start_count("concurrency_monitor", 1)
        old_pid = self.read_pid("concurrency_monitor")
        (self.repo / ".refactor-loop" / "heartbeats" / "concurrency_monitor.ts").write_text(f"{int(time.time())}\n", encoding="utf-8")
        self.terminate(old_pid)
        self.run_helper()
        new_pid = self.read_pid("concurrency_monitor")
        self.assertNotEqual(old_pid, new_pid)
        self.assert_start_count("concurrency_monitor", 2)

    def test_restarts_when_launch_fingerprint_changes(self) -> None:
        self.run_helper()
        old_pid = self.read_pid("comment-monitor")
        (self.skill / "scripts" / "consensus-rnd-cli").write_text("#!/usr/bin/env python3\nprint('changed')\n", encoding="utf-8")
        self.run_helper()
        new_pid = self.read_pid("comment-monitor")
        self.assertNotEqual(old_pid, new_pid)
        self.assertEqual(2, self.start_count("comment-monitor"))

    def test_restarts_when_package_tree_fingerprint_changes(self) -> None:
        self.run_helper()
        old_pid = self.read_pid("dev_sync_daemon")
        package_dir = self.skill / "scripts" / "codex_refactor_loop"
        package_dir.mkdir(parents=True)
        (package_dir / "sentinel_module.py").write_text("VALUE = 'changed'\n", encoding="utf-8")
        self.run_helper()
        new_pid = self.read_pid("dev_sync_daemon")
        self.assertNotEqual(old_pid, new_pid)
        self.assertEqual(2, self.start_count("dev_sync_daemon"))

    def test_restarts_when_fingerprint_missing(self) -> None:
        self.run_helper()
        self.fingerprint_path("codex-progress-reporter").unlink()
        self.run_helper()
        self.assertEqual(2, self.start_count("codex-progress-reporter"))
        self.assertTrue(self.fingerprint_path("codex-progress-reporter").exists())

    def test_restarts_when_fingerprint_malformed(self) -> None:
        self.run_helper()
        self.fingerprint_path("phase9_router_daemon").write_text("{bad json\n", encoding="utf-8")
        self.run_helper()
        self.assertEqual(2, self.start_count("phase9_router_daemon"))
        self.assertIn("package_tree_sha256", self.fingerprint_path("phase9_router_daemon").read_text(encoding="utf-8"))

    def test_restarts_when_fingerprint_valid_json_has_malformed_schema(self) -> None:
        self.run_helper()
        cases = (
            ("concurrency_monitor", lambda valid: {key: value for key, value in valid.items() if key != "entrypoint_sha256"}),
            ("comment-monitor", lambda valid: {**valid, "command": "python3 consensus-rnd-cli comment-monitor --daemon"}),
        )
        old_pids: dict[str, int] = {}
        for name, malformed in cases:
            old_pids[name] = self.read_pid(name)
            path = self.fingerprint_path(name)
            valid = json.loads(path.read_text(encoding="utf-8"))
            path.write_text(json.dumps(malformed(valid), sort_keys=True) + "\n", encoding="utf-8")

        self.run_helper()

        for name, old_pid in old_pids.items():
            with self.subTest(name=name):
                self.assertNotEqual(old_pid, self.read_pid(name))
                self.assertEqual(2, self.start_count(name))
                repaired = json.loads(self.fingerprint_path(name).read_text(encoding="utf-8"))
                self.assertIsInstance(repaired["command"], list)
                self.assertIn("entrypoint_sha256", repaired)

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

    # Refactor (impl/issue191-single-active-controller): Old pattern:
    # restart-daemons started controller write daemons on every device. New
    # principle: non-owner restart-daemons writes active_controller=noop and
    # starts no write daemon.
    def test_non_owner_restart_daemons_writes_noop_and_starts_no_daemons(self) -> None:
        decision = mock.Mock(allowed=False, owner_device="device-a", status="not-owner", action="restart-daemons", lease_id="lease", expires_at="")
        with mock.patch("codex_refactor_loop.restart.require_active_controller", return_value=decision):
            helper = RestartDaemons(self.ctx, self.config)
            helper.run()

        self.assertEqual([], helper._wrappers)
        status = json.loads((self.repo / ".refactor-loop" / "state" / "active-controller-status.json").read_text(encoding="utf-8"))
        self.assertEqual("noop:not-owner", status["active_controller"])
        for name in DAEMON_NAMES:
            self.assertEqual(0, self.start_count(name))

    def test_owner_restart_daemons_starts_static_allowlist(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="restart-daemons", lease_id="lease", expires_at="")
        with mock.patch("codex_refactor_loop.restart.require_active_controller", return_value=decision):
            self.run_helper()

        for name in DAEMON_NAMES:
            self.assertEqual(1, self.start_count(name))

    def test_update_check_runs_after_static_daemon_pass_and_is_nonblocking(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="restart-daemons", lease_id="lease", expires_at="")
        calls: list[str] = []

        def fake_start(helper: RestartDaemons, name: str, command: tuple[str, ...]) -> None:
            calls.append(name)

        with mock.patch("codex_refactor_loop.restart.require_active_controller", return_value=decision):
            with mock.patch("codex_refactor_loop.restart.retain_logs", return_value=(0, 0, self.repo / ".refactor-loop" / "logs", False)):
                with mock.patch.object(RestartDaemons, "start_daemon", fake_start):
                    with mock.patch("codex_refactor_loop.restart.maybe_run_update_check", return_value={"status": "disabled", "reason": "noop"}) as update:
                        helper = RestartDaemons(self.ctx, self.config)
                        self.assertEqual(0, helper.run())

        self.assertEqual(list(DAEMON_NAMES), calls)
        update.assert_called_once_with(self.ctx, startup=True)

        with mock.patch("codex_refactor_loop.restart.require_active_controller", return_value=decision):
            with mock.patch("codex_refactor_loop.restart.retain_logs", return_value=(0, 0, self.repo / ".refactor-loop" / "logs", False)):
                with mock.patch.object(RestartDaemons, "start_daemon", fake_start):
                    with mock.patch("codex_refactor_loop.restart.maybe_run_update_check", side_effect=RuntimeError("network")):
                        helper = RestartDaemons(self.ctx, self.config)
                        self.assertEqual(0, helper.run())

    def test_restart_helper_source_mentions_launch_fingerprint_contract(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "restart.py").read_text(encoding="utf-8")
        for needle in (
            "DaemonLaunchFingerprint",
            ".fingerprint.json",
            "package_tree_sha256",
            "entrypoint_sha256",
            "pid_alive(pid)",
            "actor-owned heartbeat",
            "FORBIDDEN_LIFECYCLE_AUTHORITY",
        ):
            self.assertIn(needle, source)
        for forbidden in ("gh issue", "gh pr", "git fetch", "git push", "git merge"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
