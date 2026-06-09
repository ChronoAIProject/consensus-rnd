#!/usr/bin/env python3
"""Behavior tests for consensus-rnd-cli restart-daemons."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import restart
from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.daemon_status import DaemonStatusProjection, collect as collect_daemon_status
from codex_refactor_loop.restart import (
    DAEMON_COMMANDS,
    DaemonProcess,
    DaemonProcessInventory,
    RestartConfig,
    RestartDaemons,
    daemon_targets,
    restart_managed_daemon_names,
)
from codex_refactor_loop.runtime_retention import RuntimeRetentionResult


DAEMON_NAMES = restart_managed_daemon_names()
FAKE_COMMAND = (sys.executable, "-m", "codex_refactor_loop.fake_daemon")


@dataclass
class FakeWrapper:
    pid: int


class FakeChild:
    def __init__(
        self,
        polls: list[int | None],
        *,
        terminate_error: Exception | None = None,
        kill_error: Exception | None = None,
    ) -> None:
        self.polls = list(polls)
        self.terminate_error = terminate_error
        self.kill_error = kill_error
        self.terminated = 0
        self.killed = 0
        self.wait_timeouts: list[float | None] = []

    def poll(self) -> int | None:
        if self.polls:
            return self.polls.pop(0)
        return None

    def terminate(self) -> None:
        self.terminated += 1
        if self.terminate_error is not None:
            raise self.terminate_error
        self.polls = [0]

    def wait(self, timeout: float | None = None) -> int | None:
        self.wait_timeouts.append(timeout)
        return 0

    def kill(self) -> None:
        self.killed += 1
        if self.kill_error is not None:
            raise self.kill_error
        self.polls = [137]


class FakeRestartDaemonRuntime:
    def __init__(self, now: int = 1_700_000_000) -> None:
        self._now = now
        self._pid = 9000
        self._next_pid = 10000
        self.live_pids: set[int] = set()
        self.wrapper_commands: dict[int, str] = {}
        self.terminated: list[tuple[int, int]] = []
        self.sleeps: list[float] = []
        self.inventory_override: DaemonProcessInventory | None = None
        self.launch_envs: dict[str, dict[str, str]] = {}

    def now(self) -> int:
        return self._now

    def getpid(self) -> int:
        return self._pid

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def pid_alive(self, pid: int) -> bool:
        return pid in self.live_pids or pid == self._pid

    def collect_inventory(self) -> DaemonProcessInventory:
        if self.inventory_override is not None:
            return self.inventory_override
        return DaemonProcessInventory(
            tuple(DaemonProcess(pid, command) for pid, command in sorted(self.wrapper_commands.items()))
        )

    def terminate_pid(self, pid: int, grace: int) -> None:
        self.terminated.append((pid, grace))
        self.live_pids.discard(pid)
        self.wrapper_commands.pop(pid, None)

    def launch_wrapper(
        self,
        *,
        ctx: LoopContext,
        target,
        wrapper_code: str,
        env: dict[str, str],
        log_file: Path,
    ) -> FakeWrapper:
        pid = self._next_pid
        self._next_pid += 1
        self.live_pids.add(pid)
        target.pid_file.parent.mkdir(parents=True, exist_ok=True)
        target.pid_file.write_text(f"{pid}\n", encoding="utf-8")
        target.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
        target.heartbeat_file.write_text(f"{self._now}\n", encoding="utf-8")
        starts = ctx.paths.logs / f"{target.name}.starts"
        starts.parent.mkdir(parents=True, exist_ok=True)
        with starts.open("a", encoding="utf-8") as handle:
            handle.write(f"{pid}\n")
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.touch()
        self.launch_envs[target.name] = dict(env)
        self.wrapper_commands[pid] = self.canonical_command(ctx, target.name, target.command, pid=pid)
        return FakeWrapper(pid)

    def canonical_command(self, ctx: LoopContext, name: str, command: tuple[str, ...], *, pid: int = 111) -> str:
        target = restart.daemon_target(ctx, name, command)
        parts = (
            sys.executable,
            "-c",
            restart.WRAPPER_CODE,
            name,
            str(ctx.repo_root),
            str(target.pid_file),
            str(target.died_file),
            *target.command,
        )
        return " ".join(parts)


class RestartDaemonsBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="restart-daemons-test-"))
        self.repo = self.tmp_root / "repo"
        self.skill = self.tmp_root / "skill"
        for rel in (".refactor-loop/logs", ".refactor-loop/locks", ".refactor-loop/heartbeats"):
            (self.repo / rel).mkdir(parents=True, exist_ok=True)
        (self.repo / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.skill / "scripts").mkdir(parents=True)
        (self.skill / "scripts" / "consensus-rnd-cli").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        self.host_env_path = self.repo / ".config" / "consensus-rnd" / "host.env"
        self.host_env_path.write_text(
            f'export REPO_ROOT="{self.repo}"\nexport GH_REPO_SLUG="example/repo"\nexport MAINTAINER_WHITELIST="maintainer"\n',
            encoding="utf-8",
        )
        self.env_patch = mock.patch.dict(
            os.environ,
            {"CONSENSUS_RND_HOST_ENV": str(self.host_env_path)},
        )
        self.env_patch.start()
        self.ctx = LoopContext.load(repo_root=self.repo, skill_root=self.skill)
        self.config = RestartConfig(heartbeat_fresh_seconds=30, heartbeat_interval=1, stop_grace_seconds=1)
        self.runtime = FakeRestartDaemonRuntime()

    def tearDown(self) -> None:
        self.env_patch.stop()
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def run_helper(self) -> RestartDaemons:
        with mock.patch("codex_refactor_loop.restart.DAEMON_COMMANDS", tuple((name, FAKE_COMMAND) for name in DAEMON_NAMES)):
            with mock.patch("codex_refactor_loop.restart.retain_runtime", return_value=self.noop_retention()):
                helper = RestartDaemons(self.ctx, self.config, runtime=self.runtime)
                helper.run()
                return helper

    def collect_status_with_fake_allowlist(self, inventory: DaemonProcessInventory | None = None):
        collected = inventory or self.runtime.collect_inventory()
        with mock.patch("codex_refactor_loop.restart.DAEMON_COMMANDS", tuple((name, FAKE_COMMAND) for name in DAEMON_NAMES)):
            with mock.patch("codex_refactor_loop.daemon_status.DaemonProcessInventory.collect", return_value=collected):
                with mock.patch("codex_refactor_loop.daemon_status.pid_alive", self.runtime.pid_alive):
                    with mock.patch("codex_refactor_loop.restart.pid_alive", self.runtime.pid_alive):
                        with mock.patch("codex_refactor_loop.restart.time.time", return_value=self.runtime.now()):
                            return collect_daemon_status(repo_root=self.repo, skill_root=self.skill)

    def noop_retention(self) -> RuntimeRetentionResult:
        return RuntimeRetentionResult(False, 0, 0, False, 0, False, self.repo / ".refactor-loop", False)

    def start_count(self, name: str) -> int:
        path = self.repo / ".refactor-loop" / "logs" / f"{name}.starts"
        return len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0

    def file_sha256(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def assert_start_count(self, name: str, expected: int) -> None:
        self.assertEqual(expected, self.start_count(name))

    def read_pid(self, name: str) -> int:
        return int((self.repo / ".refactor-loop" / "locks" / f"{name}.pid").read_text(encoding="utf-8").strip())

    def fingerprint_path(self, name: str) -> Path:
        return self.repo / ".refactor-loop" / "locks" / f"{name}.fingerprint.json"

    def heartbeat_path(self, name: str) -> Path:
        return self.repo / ".refactor-loop" / "heartbeats" / f"{name}.ts"

    def stale_heartbeat(self, name: str) -> None:
        self.heartbeat_path(name).write_text(f"{self.runtime.now() - 120}\n", encoding="utf-8")

    def test_restart_commands_use_single_cli_entrypoint_and_daemon_flag(self) -> None:
        commands_by_name = dict(DAEMON_COMMANDS)

        self.assertEqual(tuple(commands_by_name), DAEMON_NAMES)
        self.assertEqual(7, len(commands_by_name))
        self.assertIn("closed_label_reconciler", DAEMON_NAMES)
        self.assertIn("wakeup_runner_daemon", DAEMON_NAMES)
        self.assertNotIn("patrol_inspector_daemon", commands_by_name)
        for name, command in commands_by_name.items():
            joined = " ".join(command)
            self.assertIn("consensus-rnd-cli", joined, name)
            self.assertIn("--daemon", command)
        self.assertEqual("closed-label-reconciler", commands_by_name["closed_label_reconciler"][2])
        self.assertEqual(("phase9-router", "--daemon", "--interval", "{phase9_router_interval_seconds}"), commands_by_name["phase9_router_daemon"][2:])
        self.assertEqual(("wakeup-runner", "--daemon", "--interval-seconds", "{wakeup_runner_interval_seconds}"), commands_by_name["wakeup_runner_daemon"][2:])

    def test_restart_daemon_intervals_resolve_from_host_env_with_default_fallback(self) -> None:
        phase9_template = next(command for name, command in DAEMON_COMMANDS if name == "phase9_router_daemon")
        wakeup_template = next(command for name, command in DAEMON_COMMANDS if name == "wakeup_runner_daemon")
        default_phase9 = restart.daemon_target(self.ctx, "phase9_router_daemon", phase9_template)
        default_wakeup = restart.daemon_target(self.ctx, "wakeup_runner_daemon", wakeup_template)

        self.assertEqual("120", default_phase9.command[-1])
        self.assertEqual("120", default_wakeup.command[-1])

        self.host_env_path.write_text(
            f'export REPO_ROOT="{self.repo}"\n'
            'export GH_REPO_SLUG="example/repo"\n'
            'export MAINTAINER_WHITELIST="maintainer"\n'
            'export PHASE9_ROUTER_INTERVAL_SECONDS="45"\n'
            'export WAKEUP_RUNNER_INTERVAL_SECONDS="75"\n',
            encoding="utf-8",
        )
        ctx = LoopContext.load(
            repo_root=self.repo,
            skill_root=self.skill,
            env={"CONSENSUS_RND_HOST_ENV": str(self.host_env_path)},
        )
        host_phase9 = restart.daemon_target(ctx, "phase9_router_daemon", phase9_template)
        host_wakeup = restart.daemon_target(ctx, "wakeup_runner_daemon", wakeup_template)

        self.assertEqual("45", host_phase9.command[-1])
        self.assertEqual("75", host_wakeup.command[-1])
        runtime = FakeRestartDaemonRuntime()
        helper = RestartDaemons(ctx, self.config, runtime=runtime)
        helper.start_daemon("phase9_router_daemon", phase9_template)
        helper.start_daemon("wakeup_runner_daemon", wakeup_template)
        self.assertEqual("45", runtime.launch_envs["phase9_router_daemon"]["PHASE9_ROUTER_INTERVAL_SECONDS"])
        self.assertEqual("75", runtime.launch_envs["wakeup_runner_daemon"]["WAKEUP_RUNNER_INTERVAL_SECONDS"])

        self.host_env_path.write_text(
            f'export REPO_ROOT="{self.repo}"\n'
            'export GH_REPO_SLUG="example/repo"\n'
            'export MAINTAINER_WHITELIST="maintainer"\n'
            'export PHASE9_ROUTER_INTERVAL_SECONDS="0"\n'
            'export WAKEUP_RUNNER_INTERVAL_SECONDS="not-an-int"\n',
            encoding="utf-8",
        )
        invalid_ctx = LoopContext.load(
            repo_root=self.repo,
            skill_root=self.skill,
            env={"CONSENSUS_RND_HOST_ENV": str(self.host_env_path)},
        )
        invalid_phase9 = restart.daemon_target(invalid_ctx, "phase9_router_daemon", phase9_template)
        invalid_wakeup = restart.daemon_target(invalid_ctx, "wakeup_runner_daemon", wakeup_template)

        self.assertEqual("120", invalid_phase9.command[-1])
        self.assertEqual("120", invalid_wakeup.command[-1])

    def test_restart_managed_daemon_names_projects_daemon_commands(self) -> None:
        self.assertEqual(tuple(name for name, _command in DAEMON_COMMANDS), restart_managed_daemon_names())
        self.assertEqual(7, len(restart_managed_daemon_names()))
        self.assertIn("closed_label_reconciler", restart_managed_daemon_names())
        self.assertIn("wakeup_runner_daemon", restart_managed_daemon_names())
        self.assertNotIn("patrol_inspector_daemon", restart_managed_daemon_names())
        patched = (
            ("first", ("python3", "first")),
            ("second", ("python3", "second")),
        )
        with mock.patch("codex_refactor_loop.restart.DAEMON_COMMANDS", patched):
            self.assertEqual(("first", "second"), restart.restart_managed_daemon_names())

    def test_controller_tick_supervisor_restart_target_is_host_opt_in_only(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="restart-daemons", lease_id="lease", expires_at="")
        calls: list[str] = []

        def fake_start(helper: RestartDaemons, name: str, command: tuple[str, ...]) -> None:
            calls.append(name)

        with mock.patch("codex_refactor_loop.restart.require_active_controller", return_value=decision):
            with mock.patch("codex_refactor_loop.restart.retain_runtime", return_value=self.noop_retention()):
                with mock.patch.object(RestartDaemons, "start_daemon", fake_start):
                    helper = RestartDaemons(self.ctx, self.config, runtime=self.runtime)
                    self.assertEqual(0, helper.run())
        self.assertNotIn("controller_tick_supervisor", calls)

        self.host_env_path.write_text(
            f'export REPO_ROOT="{self.repo}"\n'
            'export GH_REPO_SLUG="example/repo"\n'
            'export MAINTAINER_WHITELIST="maintainer"\n'
            'export CONTROLLER_TICK_SUPERVISOR_ENABLE="true"\n',
            encoding="utf-8",
        )
        ctx = LoopContext.load(repo_root=self.repo, skill_root=self.skill, env={"CONSENSUS_RND_HOST_ENV": str(self.host_env_path)})
        calls.clear()
        with mock.patch("codex_refactor_loop.restart.require_active_controller", return_value=decision):
            with mock.patch("codex_refactor_loop.restart.retain_runtime", return_value=self.noop_retention()):
                with mock.patch.object(RestartDaemons, "start_daemon", fake_start):
                    helper = RestartDaemons(ctx, self.config, runtime=self.runtime)
                    self.assertEqual(0, helper.run())

        self.assertEqual([*DAEMON_NAMES, "controller_tick_supervisor"], calls)
        self.assertEqual(DAEMON_NAMES, restart_managed_daemon_names())

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
        self.assertEqual([], self.runtime.sleeps)

    def test_restarts_dead_wrapper_pid_even_with_fresh_heartbeat(self) -> None:
        self.run_helper()
        old_pid = self.read_pid("concurrency_monitor")
        self.heartbeat_path("concurrency_monitor").write_text(f"{self.runtime.now()}\n", encoding="utf-8")
        self.runtime.live_pids.discard(old_pid)
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

    def test_restarts_when_host_env_content_fingerprint_changes_without_leaking_values(self) -> None:
        self.run_helper()
        old_pid = self.read_pid("concurrency_monitor")
        before = json.loads(self.fingerprint_path("concurrency_monitor").read_text(encoding="utf-8"))

        self.host_env_path.write_text(
            f'export REPO_ROOT="{self.repo}"\n'
            'export GH_REPO_SLUG="example/repo"\n'
            'export MAINTAINER_WHITELIST="maintainer"\n'
            'export PHASE9_ROUTER_INTERVAL_SECONDS="45"\n',
            encoding="utf-8",
        )
        ctx = LoopContext.load(
            repo_root=self.repo,
            skill_root=self.skill,
            env={"CONSENSUS_RND_HOST_ENV": str(self.host_env_path)},
        )
        with mock.patch("codex_refactor_loop.restart.DAEMON_COMMANDS", tuple((name, FAKE_COMMAND) for name in DAEMON_NAMES)):
            helper = RestartDaemons(ctx, self.config, runtime=self.runtime)
            helper.start_daemon("concurrency_monitor", FAKE_COMMAND)

        after = json.loads(self.fingerprint_path("concurrency_monitor").read_text(encoding="utf-8"))
        self.assertNotEqual(old_pid, self.read_pid("concurrency_monitor"))
        self.assertEqual(2, self.start_count("concurrency_monitor"))
        self.assertEqual(str(self.host_env_path.resolve()), after["host_env_path"])
        self.assertNotEqual(before["host_env_sha256"], after["host_env_sha256"])
        fingerprint_text = json.dumps(after, sort_keys=True)
        self.assertNotIn("MAINTAINER_WHITELIST", fingerprint_text)
        self.assertNotIn("maintainer", fingerprint_text)
        self.assertNotIn("PHASE9_ROUTER_INTERVAL_SECONDS", fingerprint_text)

    def test_restarts_when_host_env_locator_fingerprint_changes(self) -> None:
        self.run_helper()
        old_pid = self.read_pid("comment-monitor")
        next_host_env = self.repo / ".config" / "consensus-rnd" / "alternate-host.env"
        next_host_env.write_text(self.host_env_path.read_text(encoding="utf-8"), encoding="utf-8")
        ctx = LoopContext.load(
            repo_root=self.repo,
            skill_root=self.skill,
            env={"CONSENSUS_RND_HOST_ENV": str(next_host_env)},
        )

        with mock.patch("codex_refactor_loop.restart.DAEMON_COMMANDS", tuple((name, FAKE_COMMAND) for name in DAEMON_NAMES)):
            helper = RestartDaemons(ctx, self.config, runtime=self.runtime)
            helper.start_daemon("comment-monitor", FAKE_COMMAND)

        fingerprint = json.loads(self.fingerprint_path("comment-monitor").read_text(encoding="utf-8"))
        self.assertNotEqual(old_pid, self.read_pid("comment-monitor"))
        self.assertEqual(2, self.start_count("comment-monitor"))
        self.assertEqual(str(next_host_env.resolve()), fingerprint["host_env_path"])
        self.assertEqual(self.file_sha256(next_host_env), fingerprint["host_env_sha256"])

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
                self.assertIn("host_env_path", repaired)
                self.assertIn("host_env_sha256", repaired)

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
        self.heartbeat_path("codex-progress-reporter").unlink()
        self.run_helper()
        self.assertEqual(2, self.start_count("codex-progress-reporter"))

    def test_restarts_when_heartbeat_malformed(self) -> None:
        self.run_helper()
        self.heartbeat_path("phase9_router_daemon").write_text("not-a-timestamp\n", encoding="utf-8")
        self.run_helper()
        self.assertEqual(2, self.start_count("phase9_router_daemon"))

    def test_wrapper_self_heals_child_exit_with_same_resolved_command(self) -> None:
        heartbeat = self.heartbeat_path("comment-monitor")
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        heartbeat.write_text(f"{self.runtime.now()}\n", encoding="utf-8")
        launches: list[tuple[str, ...]] = []
        children = [FakeChild([7]), FakeChild([None])]

        def popen(command: tuple[str, ...]) -> FakeChild:
            launches.append(command)
            return children[len(launches) - 1]

        restart._run_restart_wrapper(
            [
                "comment-monitor",
                str(self.repo),
                str(self.repo / ".refactor-loop" / "locks" / "comment-monitor.pid"),
                str(self.repo / ".refactor-loop" / "logs" / "comment-monitor.died"),
                *FAKE_COMMAND,
            ],
            env={
                "RESTART_DAEMON_HEARTBEAT_FILE": str(heartbeat),
                "RESTART_DAEMON_HEARTBEAT_INTERVAL": "1",
                "RESTART_DAEMONS_HEARTBEAT_FRESH_SECONDS": "30",
                "RESTART_DAEMONS_STOP_GRACE_SECONDS": "1",
            },
            popen=popen,
            sleeper=lambda _seconds: None,
            clock=lambda: self.runtime.now(),
            getpid=lambda: 12345,
            max_supervision_cycles=2,
        )

        self.assertEqual([FAKE_COMMAND, FAKE_COMMAND], launches)
        died = (self.repo / ".refactor-loop" / "logs" / "comment-monitor.died").read_text(encoding="utf-8")
        self.assertIn("child exited exit=7; restarting same command", died)
        self.assertEqual(0, children[0].terminated)
        self.assertEqual(f"{self.runtime.now()}\n", heartbeat.read_text(encoding="utf-8"))

    def test_wrapper_self_heals_stale_missing_and_malformed_heartbeat_without_writing_it(self) -> None:
        cases = {
            "stale": f"{self.runtime.now() - 120}\n",
            "missing": None,
            "malformed": "not-a-timestamp\n",
        }
        for case, heartbeat_text in cases.items():
            with self.subTest(case=case):
                target_root = self.repo / case
                heartbeat = target_root / ".refactor-loop" / "heartbeats" / "phase9_router_daemon.ts"
                if heartbeat_text is not None:
                    heartbeat.parent.mkdir(parents=True, exist_ok=True)
                    heartbeat.write_text(heartbeat_text, encoding="utf-8")
                launches: list[tuple[str, ...]] = []
                children = [FakeChild([None]), FakeChild([None])]

                def popen(command: tuple[str, ...]) -> FakeChild:
                    launches.append(command)
                    if len(launches) == 2:
                        heartbeat.parent.mkdir(parents=True, exist_ok=True)
                        heartbeat.write_text(f"{self.runtime.now()}\n", encoding="utf-8")
                    return children[len(launches) - 1]

                restart._run_restart_wrapper(
                    [
                        "phase9_router_daemon",
                        str(target_root),
                        str(target_root / ".refactor-loop" / "locks" / "phase9_router_daemon.pid"),
                        str(target_root / ".refactor-loop" / "logs" / "phase9_router_daemon.died"),
                        *FAKE_COMMAND,
                    ],
                    env={
                        "RESTART_DAEMON_HEARTBEAT_FILE": str(heartbeat),
                        "RESTART_DAEMON_HEARTBEAT_INTERVAL": "1",
                        "RESTART_DAEMONS_HEARTBEAT_FRESH_SECONDS": "30",
                        "RESTART_DAEMONS_STOP_GRACE_SECONDS": "1",
                    },
                    popen=popen,
                    sleeper=lambda _seconds: None,
                    clock=lambda: self.runtime.now(),
                    getpid=lambda: 22222,
                    max_supervision_cycles=3 if case == "missing" else 2,
                )

                self.assertEqual([FAKE_COMMAND, FAKE_COMMAND], launches)
                self.assertEqual(1, children[0].terminated)
                self.assertEqual([1], children[0].wait_timeouts)
                died = (target_root / ".refactor-loop" / "logs" / "phase9_router_daemon.died").read_text(encoding="utf-8")
                self.assertIn("terminating child and restarting same command", died)
                self.assertEqual(f"{self.runtime.now()}\n", heartbeat.read_text(encoding="utf-8"))

    def test_wrapper_logs_and_aborts_when_child_cannot_be_killed(self) -> None:
        heartbeat = self.heartbeat_path("phase9_router_daemon")
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        heartbeat.write_text(f"{self.runtime.now() - 120}\n", encoding="utf-8")
        launches: list[tuple[str, ...]] = []
        child = FakeChild(
            [None],
            terminate_error=RuntimeError("term denied"),
            kill_error=RuntimeError("kill denied"),
        )

        def popen(command: tuple[str, ...]) -> FakeChild:
            launches.append(command)
            return child

        with self.assertRaisesRegex(RuntimeError, "child termination failed after kill"):
            restart._run_restart_wrapper(
                [
                    "phase9_router_daemon",
                    str(self.repo),
                    str(self.repo / ".refactor-loop" / "locks" / "phase9_router_daemon.pid"),
                    str(self.repo / ".refactor-loop" / "logs" / "phase9_router_daemon.died"),
                    *FAKE_COMMAND,
                ],
                env={
                    "RESTART_DAEMON_HEARTBEAT_FILE": str(heartbeat),
                    "RESTART_DAEMON_HEARTBEAT_INTERVAL": "1",
                    "RESTART_DAEMONS_HEARTBEAT_FRESH_SECONDS": "30",
                    "RESTART_DAEMONS_STOP_GRACE_SECONDS": "1",
                },
                popen=popen,
                sleeper=lambda _seconds: None,
                clock=lambda: self.runtime.now(),
                getpid=lambda: 33333,
                max_supervision_cycles=1,
            )

        self.assertEqual([FAKE_COMMAND], launches)
        self.assertEqual(1, child.terminated)
        self.assertEqual(1, child.killed)
        died = (self.repo / ".refactor-loop" / "logs" / "phase9_router_daemon.died").read_text(encoding="utf-8")
        self.assertIn("heartbeat-stale:120s; terminating child and restarting same command", died)
        self.assertIn("child terminate failed reason=RuntimeError('term denied'); attempting kill", died)
        self.assertIn("child kill failed reason=RuntimeError('kill denied'); aborting wrapper restart", died)

    def test_restarts_when_pid_dead(self) -> None:
        self.runtime.live_pids.discard(999999)
        (self.repo / ".refactor-loop" / "locks" / "dev_sync_daemon.pid").write_text("999999\n", encoding="utf-8")
        self.heartbeat_path("dev_sync_daemon").write_text(f"{self.runtime.now()}\n", encoding="utf-8")
        self.run_helper()
        self.assertEqual(1, self.start_count("dev_sync_daemon"))

    def test_duplicate_canonical_wrappers_are_reaped_before_fresh_spawn(self) -> None:
        self.run_helper()
        old_pid = self.read_pid("concurrency_monitor")
        duplicate_pids = (424242, 424243, 424244)
        command = self.runtime.canonical_command(self.ctx, "concurrency_monitor", FAKE_COMMAND)
        self.runtime.live_pids.update(duplicate_pids)
        full_inventory = list(self.runtime.collect_inventory().processes)
        full_inventory.extend(DaemonProcess(pid, command) for pid in duplicate_pids)
        self.runtime.inventory_override = DaemonProcessInventory(
            tuple(full_inventory)
        )

        self.run_helper()

        self.assertEqual(
            [(pid, self.config.stop_grace_seconds) for pid in (old_pid, *duplicate_pids)],
            self.runtime.terminated,
        )
        self.assert_start_count("concurrency_monitor", 2)
        self.assertNotEqual(old_pid, self.read_pid("concurrency_monitor"))

    def test_duplicate_canonical_matching_ignores_other_repo_and_non_allowlist_commands(self) -> None:
        command = self.runtime.canonical_command(self.ctx, "concurrency_monitor", FAKE_COMMAND)
        other_repo = command.replace(str(self.ctx.repo_root), "/tmp/other-repo")
        non_allowlist = self.runtime.canonical_command(self.ctx, "not_allowlisted", FAKE_COMMAND)
        inventory = DaemonProcessInventory(
            (
                DaemonProcess(111, command),
                DaemonProcess(222, other_repo),
                DaemonProcess(333, non_allowlist),
            )
        )
        self.runtime.live_pids.update({111, 222, 333})

        live = inventory.live_canonical_wrappers(
            name="concurrency_monitor",
            repo_root=self.ctx.repo_root,
            pid_file=self.ctx.paths.refactor_loop / "locks" / "concurrency_monitor.pid",
            died_file=self.ctx.paths.logs / "concurrency_monitor.died",
            command=FAKE_COMMAND,
            is_alive=self.runtime.pid_alive,
        )

        self.assertEqual((111,), live)

    def test_restart_wrapper_matching_accepts_skill_root_and_command_normalization_variance(self) -> None:
        template = ("python3", "{skill_root}/scripts/consensus-rnd-cli", "phase9-router", "--daemon", "--interval", "120")
        target = restart.daemon_target(self.ctx, "phase9_router_daemon", template)
        old_skill_command = self.runtime.canonical_command(self.ctx, "phase9_router_daemon", template).replace(
            str(self.skill / "scripts" / "consensus-rnd-cli"),
            "/opt/old-skill/scripts/consensus-rnd-cli",
        )
        spaced_command = f"  {old_skill_command.replace(' ', '   ')}  "
        inventory = DaemonProcessInventory((DaemonProcess(444, spaced_command),))
        self.runtime.live_pids.add(444)

        live = inventory.live_restart_wrappers(
            name="phase9_router_daemon",
            repo_root=self.ctx.repo_root,
            pid_file=target.pid_file,
            died_file=target.died_file,
            command=target.command,
            is_alive=self.runtime.pid_alive,
        )

        self.assertEqual((444,), live)

    def test_restart_wrapper_matching_rejects_unknown_daemon_even_with_matching_shape(self) -> None:
        command = self.runtime.canonical_command(self.ctx, "not_allowlisted", FAKE_COMMAND)
        target = restart.daemon_target(self.ctx, "concurrency_monitor", FAKE_COMMAND)
        inventory = DaemonProcessInventory((DaemonProcess(555, command),))
        self.runtime.live_pids.add(555)

        live = inventory.live_restart_wrappers(
            name="not_allowlisted",
            repo_root=self.ctx.repo_root,
            pid_file=target.pid_file,
            died_file=target.died_file,
            command=target.command,
            is_alive=self.runtime.pid_alive,
        )

        self.assertEqual((), live)

    def test_fresh_singleton_skips_when_one_current_wrapper_is_proven(self) -> None:
        self.run_helper()
        old_pid = self.read_pid("concurrency_monitor")

        self.run_helper()

        self.assertEqual(old_pid, self.read_pid("concurrency_monitor"))
        self.assert_start_count("concurrency_monitor", 1)
        self.assertEqual([], self.runtime.terminated)

    def test_non_owner_restart_daemons_writes_noop_and_starts_no_daemons(self) -> None:
        decision = mock.Mock(allowed=False, owner_device="device-a", status="not-owner", action="restart-daemons", lease_id="lease", expires_at="")
        with mock.patch("codex_refactor_loop.restart.require_active_controller", return_value=decision):
            helper = RestartDaemons(self.ctx, self.config, runtime=self.runtime)
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
            with mock.patch("codex_refactor_loop.restart.retain_runtime", return_value=self.noop_retention()):
                with mock.patch.object(RestartDaemons, "start_daemon", fake_start):
                    with mock.patch("codex_refactor_loop.restart.maybe_run_update_check", return_value={"status": "disabled", "reason": "noop"}) as update:
                        helper = RestartDaemons(self.ctx, self.config, runtime=self.runtime)
                        self.assertEqual(0, helper.run())

        self.assertEqual(list(DAEMON_NAMES), calls)
        update.assert_called_once_with(self.ctx, startup=True)

        with mock.patch("codex_refactor_loop.restart.require_active_controller", return_value=decision):
            with mock.patch("codex_refactor_loop.restart.retain_runtime", return_value=self.noop_retention()):
                with mock.patch.object(RestartDaemons, "start_daemon", fake_start):
                    with mock.patch("codex_refactor_loop.restart.maybe_run_update_check", side_effect=RuntimeError("network")):
                        helper = RestartDaemons(self.ctx, self.config, runtime=self.runtime)
                        self.assertEqual(0, helper.run())

    def test_daemon_status_reports_running_stale_dead_and_not_owner_without_repair(self) -> None:
        self.run_helper()
        (self.repo / ".refactor-loop" / "state").mkdir(parents=True, exist_ok=True)
        (self.repo / ".refactor-loop" / "state" / "active-controller-status.json").write_text(
            json.dumps({"active_controller": "owner"}) + "\n",
            encoding="utf-8",
        )
        report = self.collect_status_with_fake_allowlist()
        by_name = {daemon.name: daemon for daemon in report.daemons}
        self.assertEqual("owner", report.active_controller)
        self.assertEqual("running", by_name["concurrency_monitor"].status)

        self.stale_heartbeat("comment-monitor")
        self.fingerprint_path("phase9_router_daemon").unlink()
        dead_pid = self.read_pid("dev_sync_daemon")
        self.runtime.live_pids.discard(dead_pid)
        (self.repo / ".refactor-loop" / "locks" / "closed_label_reconciler.pid").unlink()

        report = self.collect_status_with_fake_allowlist()
        by_name = {daemon.name: daemon for daemon in report.daemons}
        self.assertEqual("stale", by_name["comment-monitor"].status)
        self.assertEqual("stale", by_name["phase9_router_daemon"].status)
        self.assertEqual("dead", by_name["dev_sync_daemon"].status)
        self.assertEqual("dead", by_name["closed_label_reconciler"].status)
        self.assertEqual(1, self.start_count("comment-monitor"))
        self.assertIsInstance(by_name["concurrency_monitor"], DaemonStatusProjection)

        (self.repo / ".refactor-loop" / "state" / "active-controller-status.json").write_text(
            json.dumps({"active_controller": "noop:not-owner"}) + "\n",
            encoding="utf-8",
        )
        report = self.collect_status_with_fake_allowlist()
        self.assertEqual({"not-owner"}, {daemon.status for daemon in report.daemons})

    def test_daemon_status_reports_duplicate_wrappers_without_repair(self) -> None:
        self.run_helper()
        old_pid = self.read_pid("concurrency_monitor")
        duplicate_pid = 424242
        command = self.runtime.canonical_command(self.ctx, "concurrency_monitor", FAKE_COMMAND)
        self.runtime.live_pids.add(duplicate_pid)
        inventory = DaemonProcessInventory(
            (
                DaemonProcess(old_pid, command),
                DaemonProcess(duplicate_pid, command),
            )
        )

        report = self.collect_status_with_fake_allowlist(inventory)

        by_name = {daemon.name: daemon for daemon in report.daemons}
        self.assertEqual("stale", by_name["concurrency_monitor"].status)
        self.assertEqual(1, by_name["concurrency_monitor"].duplicate_canonical_wrappers)
        self.assertEqual(old_pid, self.read_pid("concurrency_monitor"))
        self.assert_start_count("concurrency_monitor", 1)

    def test_daemon_status_resolves_static_allowlist_targets(self) -> None:
        targets = daemon_targets(self.ctx)
        self.assertEqual(DAEMON_NAMES, tuple(target.name for target in targets))
        for target in targets:
            with self.subTest(target=target.name):
                self.assertIn("consensus-rnd-cli", " ".join(target.command))
                self.assertEqual((self.repo / ".refactor-loop" / "locks" / f"{target.name}.pid").resolve(), target.pid_file)
                self.assertEqual((self.repo / ".refactor-loop" / "heartbeats" / f"{target.name}.ts").resolve(), target.heartbeat_file)

        one = daemon_targets(self.ctx, "comment-monitor")
        self.assertEqual(("comment-monitor",), tuple(target.name for target in one))
        with self.assertRaises(ValueError):
            daemon_targets(self.ctx, "not-allowlisted")

    def test_suite_level_global_ps_leak_guard_is_deleted(self) -> None:
        guard = SCRIPT_DIR / "test_zz_daemon_leak_guard.py"
        self.assertFalse(guard.exists())
        for path in (
            SCRIPT_DIR / "test_restart_daemons.py",
            SCRIPT_DIR / "test_cli_daemon_help_smoke.py",
            SCRIPT_DIR / "test_cli_command_router.py",
        ):
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("ps " + "-eo", source)
                self.assertNotIn(" phase9-router " + "--daemon", source)

    def test_restart_helper_source_mentions_launch_fingerprint_contract(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "restart.py").read_text(encoding="utf-8")
        for needle in (
            "DaemonLaunchFingerprint",
            "DaemonTarget",
            "RestartDaemonRuntime",
            "RealRestartDaemonRuntime",
            "daemon_targets",
            "read_daemon_pid",
            "read_heartbeat_age_seconds",
            "read_heartbeat_status",
            "DaemonHeartbeatStatus",
            "expected_launch_fingerprint",
            "DaemonProcessInventory",
            "_run_restart_wrapper",
            "child exited exit=",
            "terminating child and restarting same command",
            ".fingerprint.json",
            "package_tree_sha256",
            "entrypoint_sha256",
            "host_env_path",
            "host_env_sha256",
            "pid_alive(pid)",
            "FORBIDDEN_LIFECYCLE_AUTHORITY",
            "def restart_managed_daemon_names(",
            "return tuple(name for name, _command in DAEMON_COMMANDS)",
        ):
            self.assertIn(needle, source)
        self.assertIn("heartbeat_file", source)
        self.assertIn("RESTART_DAEMON_HEARTBEAT_FILE", source)
        self.assertIn("RESTART_DAEMON_HEARTBEAT_INTERVAL", source)
        self.assertIn("PHASE9_ROUTER_INTERVAL_SECONDS", source)
        self.assertIn("WAKEUP_RUNNER_INTERVAL_SECONDS", source)
        self.assertIn("SUPERVISOR_DAEMON_COMMAND", source)
        self.assertIn("CONTROLLER_TICK_SUPERVISOR_ENABLE", source)
        self.assertIn("codex_refactor_loop.supervisor", source)
        self.assertNotIn("PATROL_INSPECTOR_INTERVAL_SECONDS", source)
        self.assertNotIn("patrol_inspector_daemon", source)
        history_forbidden = ("Refactor" + " (", "Old " + "pattern", "New " + "principle")
        for needle in history_forbidden:
            with self.subTest(needle=needle):
                self.assertNotIn(needle, source)
        for forbidden in ("gh issue", "gh pr", "git fetch", "git push", "git merge"):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("RESTART_MANAGED_DAEMON_NAMES", source)

    def test_default_restart_unit_tests_do_not_active_use_real_process_harness(self) -> None:
        source = Path(__file__).read_text(encoding="utf-8")
        forbidden = (
            "FAKE_DAEMON",
            "signal.pause(",
            "subprocess.Popen(",
            "os.kill(",
            "os.waitpid(",
            "proc.wait(",
            "proc.kill(",
            "time.sleep(",
        )
        lines = source.splitlines()
        guard_start = next(index for index, line in enumerate(lines) if "forbidden = (" in line)
        guard_end = next(index for index in range(guard_start + 1, len(lines)) if lines[index].strip() == ")")
        active_source = "\n".join(lines[:guard_start] + lines[guard_end + 1 :])
        for needle in forbidden:
            with self.subTest(needle=needle):
                self.assertNotIn(needle, active_source)
        history_forbidden = ("Refactor" + " (", "Old " + "pattern", "New " + "principle")
        for needle in history_forbidden:
            with self.subTest(needle=needle):
                self.assertNotIn(needle, source)


if __name__ == "__main__":
    unittest.main()
