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
from dataclasses import dataclass, replace
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import restart
from codex_refactor_loop.context import LoopContext, LoopContextError
from codex_refactor_loop.daemon_progress import begin_tick, complete_tick, fail_tick
from codex_refactor_loop.daemon_singleton import DaemonSingletonMetadata, DaemonSingletonProjection, METADATA_FIELD_ORDER, read_metadata
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
MACOS_FRAMEWORK_PYTHON = "/Library/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python"


@dataclass
class FakeWrapper:
    pid: int


@dataclass
class FakeCommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


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


class AdvancingClock:
    def __init__(self, now: float) -> None:
        self.now = now
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeRestartDaemonRuntime:
    def __init__(self, now: int = 1_700_000_000) -> None:
        self._now = now
        self._pid = 9000
        self._next_pid = 10000
        self.child_pids_by_wrapper: dict[int, int] = {}
        self.child_commands: dict[int, str] = {}
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
        processes = [DaemonProcess(pid, command, 1) for pid, command in sorted(self.wrapper_commands.items())]
        for wrapper_pid, child_pid in sorted(self.child_pids_by_wrapper.items()):
            if wrapper_pid in self.wrapper_commands:
                processes.append(DaemonProcess(child_pid, self.child_commands[child_pid], wrapper_pid))
        return DaemonProcessInventory(tuple(processes))

    def probe_singleton(self, target) -> DaemonSingletonProjection:
        metadata = read_metadata(target.singleton_lock_file)
        if metadata is None:
            state = "missing" if not target.singleton_lock_file.exists() else "held-malformed"
            return DaemonSingletonProjection(target.singleton_lock_file, state, None, False, f"lock-{state}")
        if metadata.actor_pid in self.live_pids:
            return DaemonSingletonProjection(target.singleton_lock_file, "held", metadata.actor_pid, True, "lock-held", metadata)
        return DaemonSingletonProjection(target.singleton_lock_file, "free", metadata.actor_pid, True, "lock-free", metadata)

    def terminate_pid(self, pid: int, grace: int) -> None:
        self.terminated.append((pid, grace))
        self.live_pids.discard(pid)
        self.wrapper_commands.pop(pid, None)
        child_pid = self.child_pids_by_wrapper.pop(pid, None)
        if child_pid is not None:
            self.live_pids.discard(child_pid)
            self.child_commands.pop(child_pid, None)

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
        child_pid = self._next_pid
        self._next_pid += 1
        self.live_pids.add(pid)
        self.live_pids.add(child_pid)
        self.child_pids_by_wrapper[pid] = child_pid
        target.pid_file.parent.mkdir(parents=True, exist_ok=True)
        target.pid_file.write_text(f"{pid}\n", encoding="utf-8")
        target.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
        target.heartbeat_file.write_text(f"{self._now}\n", encoding="utf-8")
        complete_tick(ctx.repo_root, begin_tick(ctx.repo_root, target.name, now=self._now, pid=child_pid), now=self._now)
        starts = ctx.paths.logs / f"{target.name}.starts"
        starts.parent.mkdir(parents=True, exist_ok=True)
        with starts.open("a", encoding="utf-8") as handle:
            handle.write(f"{pid}\n")
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.touch()
        self.launch_envs[target.name] = dict(env)
        self.wrapper_commands[pid] = self.canonical_command(ctx, target.name, target.command, pid=pid)
        self.child_commands[child_pid] = " ".join(target.command)
        write_singleton_metadata(
            ctx.repo_root,
            target,
            actor_pid=child_pid,
            command_sha256=env.get("RESTART_DAEMON_COMMAND_SHA256", ""),
            started_at=self._now,
        )
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

    def framework_python_wrapper_command(self, ctx: LoopContext, name: str, command: tuple[str, ...]) -> str:
        target = restart.daemon_target(ctx, name, command)
        framework_child = (MACOS_FRAMEWORK_PYTHON, *command[1:])
        parts = (
            MACOS_FRAMEWORK_PYTHON,
            "-c",
            restart.WRAPPER_CODE,
            name,
            str(ctx.repo_root),
            str(target.pid_file),
            str(target.died_file),
            *framework_child,
        )
        return " ".join(parts)

    def framework_python_child_command(self, command: tuple[str, ...]) -> str:
        return " ".join((MACOS_FRAMEWORK_PYTHON, *command[1:]))


def write_singleton_metadata(
    repo_root: Path,
    target,
    *,
    actor_pid: int,
    command_sha256: str | None = None,
    started_at: int,
) -> DaemonSingletonMetadata:
    metadata = DaemonSingletonMetadata.create(
        daemon_name=target.name,
        repo_root=repo_root,
        heartbeat_file=target.heartbeat_file,
        fingerprint_file=target.fingerprint_file,
        command_sha256=command_sha256 if command_sha256 is not None else restart._command_digest(target.command),
        actor_pid=actor_pid,
        started_at=started_at,
    )
    target.singleton_lock_file.write_text(metadata.canonical_json_line(), encoding="utf-8")
    return metadata


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
        clean_env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PHASE9_ROUTER_INTERVAL_SECONDS", "WAKEUP_RUNNER_INTERVAL_SECONDS"}
        }
        clean_env["CONSENSUS_RND_HOST_ENV"] = str(self.host_env_path)
        self.env_patch = mock.patch.dict(
            os.environ,
            clean_env,
            clear=True,
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
        def fake_probe(repo_root: Path, name: str) -> DaemonSingletonProjection:
            target = restart.daemon_target(self.ctx, name, FAKE_COMMAND)
            return self.runtime.probe_singleton(target)

        with mock.patch("codex_refactor_loop.restart.DAEMON_COMMANDS", tuple((name, FAKE_COMMAND) for name in DAEMON_NAMES)):
            with mock.patch("codex_refactor_loop.daemon_status.DaemonProcessInventory.collect", return_value=collected):
                with mock.patch("codex_refactor_loop.daemon_status.probe_daemon_singleton", side_effect=fake_probe):
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

    def singleton_path(self, name: str) -> Path:
        return self.repo / ".refactor-loop" / "locks" / f"{name}.singleton.lock"

    def fingerprint_path(self, name: str) -> Path:
        return self.repo / ".refactor-loop" / "locks" / f"{name}.fingerprint.json"

    def heartbeat_path(self, name: str) -> Path:
        return self.repo / ".refactor-loop" / "heartbeats" / f"{name}.ts"

    def stale_heartbeat(self, name: str) -> None:
        self.heartbeat_path(name).write_text(f"{self.runtime.now() - 120}\n", encoding="utf-8")

    def ctx_with_restart_admission_override(
        self,
        *,
        host_env_location: Path | None | object = ...,
        host_env: dict[str, str] | object = ...,
        gh_repo_slug: str | None | object = ...,
    ) -> LoopContext:
        kwargs = {}
        if host_env_location is not ...:
            kwargs["host_env_location"] = host_env_location
        if host_env is not ...:
            kwargs["host_env"] = host_env
        if gh_repo_slug is not ...:
            kwargs["gh_repo_slug"] = gh_repo_slug
        return replace(self.ctx, **kwargs)

    def assert_restart_run_rejects_before_write_side_supervision(self, ctx: LoopContext, pattern: str) -> None:
        helper = RestartDaemons(ctx, self.config, runtime=self.runtime)
        with (
            mock.patch.object(helper, "_prepare_dirs") as prepare_dirs,
            mock.patch("codex_refactor_loop.restart.require_active_controller") as active_controller,
            mock.patch.object(helper, "_run_runtime_retention") as retention,
            mock.patch.object(helper, "start_daemon") as start_daemon,
            mock.patch.object(helper, "_run_update_check") as update_check,
        ):
            with self.assertRaisesRegex(LoopContextError, pattern):
                helper.run()

        prepare_dirs.assert_not_called()
        active_controller.assert_not_called()
        retention.assert_not_called()
        start_daemon.assert_not_called()
        update_check.assert_not_called()

    def assert_direct_start_daemon_rejects_before_launch_probe(self, ctx: LoopContext, pattern: str) -> None:
        helper = RestartDaemons(ctx, self.config, runtime=self.runtime)
        with mock.patch.object(self.runtime, "collect_inventory") as collect_inventory:
            with self.assertRaisesRegex(LoopContextError, pattern):
                helper.start_daemon("concurrency_monitor", FAKE_COMMAND)

        collect_inventory.assert_not_called()
        self.assertEqual(0, self.start_count("concurrency_monitor"))

    def test_singleton_metadata_canonical_json_matches_documented_field_order(self) -> None:
        target = restart.daemon_target(self.ctx, "concurrency_monitor", FAKE_COMMAND)
        metadata = write_singleton_metadata(self.ctx.repo_root, target, actor_pid=12345, started_at=self.runtime.now())

        payload = json.loads(target.singleton_lock_file.read_text(encoding="utf-8"), object_pairs_hook=dict)

        self.assertEqual(list(METADATA_FIELD_ORDER), list(payload))
        self.assertEqual("$REPO_ROOT", payload["repo_root"])
        self.assertEqual("$REPO_ROOT/.refactor-loop/heartbeats/concurrency_monitor.ts", payload["heartbeat_file"])
        self.assertEqual("$REPO_ROOT/.refactor-loop/locks/concurrency_monitor.fingerprint.json", payload["fingerprint_file"])
        self.assertNotIn(str(self.ctx.repo_root), target.singleton_lock_file.read_text(encoding="utf-8"))
        self.assertEqual(metadata, read_metadata(target.singleton_lock_file))

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

    def test_restart_daemons_requires_host_owned_host_env_before_supervision(self) -> None:
        valid_host_env = dict(self.ctx.host_env)
        cases = (
            (
                "missing-locator",
                self.ctx_with_restart_admission_override(host_env_location=None),
                "CONSENSUS_RND_HOST_ENV",
            ),
            (
                "empty-host-env",
                self.ctx_with_restart_admission_override(host_env={}),
                "host.env is empty",
            ),
            (
                "missing-host-env-repo-root",
                self.ctx_with_restart_admission_override(host_env={"GH_REPO_SLUG": "example/repo"}),
                "REPO_ROOT",
            ),
            (
                "mismatched-host-env-repo-root",
                self.ctx_with_restart_admission_override(host_env={**valid_host_env, "REPO_ROOT": str(self.tmp_root / "other")}),
                "REPO_ROOT",
            ),
            (
                "missing-gh-repo-slug",
                self.ctx_with_restart_admission_override(
                    host_env={"REPO_ROOT": str(self.repo)},
                    gh_repo_slug=None,
                ),
                "GH_REPO_SLUG",
            ),
            (
                "invalid-gh-repo-slug",
                self.ctx_with_restart_admission_override(
                    host_env={**valid_host_env, "GH_REPO_SLUG": "repo-only"},
                    gh_repo_slug="repo-only",
                ),
                "GH_REPO_SLUG",
            ),
            (
                "too-many-gh-repo-slug-segments",
                self.ctx_with_restart_admission_override(
                    host_env={**valid_host_env, "GH_REPO_SLUG": "owner/repo/extra"},
                    gh_repo_slug="owner/repo/extra",
                ),
                "GH_REPO_SLUG",
            ),
        )

        for name, ctx, pattern in cases:
            with self.subTest(name=name):
                self.assert_restart_run_rejects_before_write_side_supervision(ctx, pattern)

    def test_direct_start_daemon_requires_same_host_env_admission(self) -> None:
        ctx = self.ctx_with_restart_admission_override(host_env_location=None)

        self.assert_direct_start_daemon_rejects_before_launch_probe(ctx, "CONSENSUS_RND_HOST_ENV")

    def test_restart_daemons_base_admission_does_not_require_maintainer_whitelist(self) -> None:
        self.host_env_path.write_text(
            f'export REPO_ROOT="{self.repo}"\nexport GH_REPO_SLUG="example/repo"\n',
            encoding="utf-8",
        )
        ctx = LoopContext.load(repo_root=self.repo, skill_root=self.skill, env={"CONSENSUS_RND_HOST_ENV": str(self.host_env_path)})
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="restart-daemons", lease_id="lease", expires_at="")
        calls: list[str] = []

        def fake_start(helper: RestartDaemons, name: str, command: tuple[str, ...]) -> None:
            calls.append(name)

        with mock.patch("codex_refactor_loop.restart.require_active_controller", return_value=decision):
            with mock.patch("codex_refactor_loop.restart.retain_runtime", return_value=self.noop_retention()):
                with mock.patch.object(RestartDaemons, "start_daemon", fake_start):
                    helper = RestartDaemons(ctx, self.config, runtime=self.runtime)
                    self.assertEqual(0, helper.run())

        self.assertEqual(list(DAEMON_NAMES), calls)

    def test_restart_daemon_intervals_resolve_from_host_env_with_default_fallback(self) -> None:
        phase9_template = next(command for name, command in DAEMON_COMMANDS if name == "phase9_router_daemon")
        wakeup_template = next(command for name, command in DAEMON_COMMANDS if name == "wakeup_runner_daemon")
        default_phase9 = restart.daemon_target(self.ctx, "phase9_router_daemon", phase9_template)
        default_wakeup = restart.daemon_target(self.ctx, "wakeup_runner_daemon", wakeup_template)

        self.assertEqual("120", default_phase9.command[-1])
        self.assertEqual("60", default_wakeup.command[-1])

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
        self.assertEqual("60", invalid_wakeup.command[-1])

    def test_restart_managed_daemon_names_projects_daemon_commands(self) -> None:
        self.assertEqual(tuple(name for name, _command in DAEMON_COMMANDS), restart_managed_daemon_names())
        self.assertEqual(7, len(restart_managed_daemon_names()))
        self.assertIn("closed_label_reconciler", restart_managed_daemon_names())
        self.assertIn("wakeup_runner_daemon", restart_managed_daemon_names())
        self.assertNotIn("patrol_inspector_daemon", restart_managed_daemon_names())
        self.assertNotIn("controller_tick_supervisor", restart_managed_daemon_names())
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

        self.assertEqual([name for name in DAEMON_NAMES if name != "comment-monitor"] + ["controller_tick_supervisor"], calls)
        self.assertEqual(DAEMON_NAMES, restart_managed_daemon_names())
        self.assertEqual(
            tuple(name for name in DAEMON_NAMES if name != "comment-monitor"),
            restart.restart_managed_daemon_names_for_context(ctx),
        )

    def test_supervisor_enabled_mode_stops_legacy_comment_monitor_before_starting_supervisor(self) -> None:
        decision = mock.Mock(allowed=True, owner_device="device-a", status="owner", action="restart-daemons", lease_id="lease", expires_at="")
        self.run_helper()
        old_comment_pid = self.read_pid("comment-monitor")
        self.host_env_path.write_text(
            f'export REPO_ROOT="{self.repo}"\n'
            'export GH_REPO_SLUG="example/repo"\n'
            'export MAINTAINER_WHITELIST="maintainer"\n'
            'export CONTROLLER_TICK_SUPERVISOR_ENABLE="true"\n',
            encoding="utf-8",
        )
        ctx = LoopContext.load(repo_root=self.repo, skill_root=self.skill, env={"CONSENSUS_RND_HOST_ENV": str(self.host_env_path)})

        with mock.patch("codex_refactor_loop.restart.DAEMON_COMMANDS", tuple((name, FAKE_COMMAND) for name in DAEMON_NAMES)):
            with mock.patch("codex_refactor_loop.restart.require_active_controller", return_value=decision):
                with mock.patch("codex_refactor_loop.restart.retain_runtime", return_value=self.noop_retention()):
                    helper = RestartDaemons(ctx, self.config, runtime=self.runtime)
                    self.assertEqual(0, helper.run())

        self.assertIn((old_comment_pid, self.config.stop_grace_seconds), self.runtime.terminated)
        self.assertFalse((self.repo / ".refactor-loop" / "locks" / "comment-monitor.pid").exists())
        self.assertEqual(1, self.start_count("comment-monitor"))
        self.assertEqual(1, self.start_count("controller_tick_supervisor"))

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

    def test_restarts_when_progress_missing_even_with_fresh_heartbeat(self) -> None:
        self.run_helper()
        old_pid = self.read_pid("concurrency_monitor")
        (self.repo / ".refactor-loop" / "state" / "daemon-tick-progress" / "concurrency_monitor.json").unlink()
        self.heartbeat_path("concurrency_monitor").write_text(f"{self.runtime.now()}\n", encoding="utf-8")

        self.run_helper()

        self.assertNotEqual(old_pid, self.read_pid("concurrency_monitor"))
        self.assertEqual(2, self.start_count("concurrency_monitor"))

    def test_restarts_when_progress_overdue_even_with_fresh_heartbeat(self) -> None:
        self.run_helper()
        old_pid = self.read_pid("comment-monitor")
        progress = begin_tick(self.repo, "comment-monitor", now=self.runtime.now() - 700, pid=old_pid + 1)
        complete_tick(self.repo, progress, now=self.runtime.now() - 700)
        self.heartbeat_path("comment-monitor").write_text(f"{self.runtime.now()}\n", encoding="utf-8")

        self.run_helper()

        self.assertNotEqual(old_pid, self.read_pid("comment-monitor"))
        self.assertEqual(2, self.start_count("comment-monitor"))

    def test_restarts_when_progress_failed_even_with_fresh_heartbeat(self) -> None:
        self.run_helper()
        old_pid = self.read_pid("phase9_router_daemon")
        progress = begin_tick(self.repo, "phase9_router_daemon", now=self.runtime.now(), pid=old_pid + 1)
        fail_tick(self.repo, progress, now=self.runtime.now(), message="RuntimeError:boom")
        self.heartbeat_path("phase9_router_daemon").write_text(f"{self.runtime.now()}\n", encoding="utf-8")

        self.run_helper()

        self.assertNotEqual(old_pid, self.read_pid("phase9_router_daemon"))
        self.assertEqual(2, self.start_count("phase9_router_daemon"))

    def test_wrapper_self_heals_child_exit_with_same_resolved_command(self) -> None:
        heartbeat = self.heartbeat_path("comment-monitor")
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        heartbeat.write_text(f"{self.runtime.now()}\n", encoding="utf-8")
        clock = AdvancingClock(float(self.runtime.now()))
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
            sleeper=clock.sleep,
            clock=clock,
            getpid=lambda: 12345,
            max_supervision_cycles=2,
        )

        self.assertEqual([FAKE_COMMAND, FAKE_COMMAND], launches)
        self.assertEqual([1.0, 1.0], clock.sleeps)
        died = (self.repo / ".refactor-loop" / "logs" / "comment-monitor.died").read_text(encoding="utf-8")
        self.assertIn("child exited exit=7; restarting same command", died)
        self.assertEqual(0, children[0].terminated)
        self.assertEqual(f"{self.runtime.now()}\n", heartbeat.read_text(encoding="utf-8"))

    def test_wrapper_graces_inherited_stale_and_missing_heartbeat_until_current_child_window_expires(self) -> None:
        cases = {
            "stale": f"{self.runtime.now() - 120}\n",
            "missing": None,
        }
        for case, heartbeat_text in cases.items():
            with self.subTest(case=case):
                target_root = self.repo / case
                heartbeat = target_root / ".refactor-loop" / "heartbeats" / "phase9_router_daemon.ts"
                if heartbeat_text is not None:
                    heartbeat.parent.mkdir(parents=True, exist_ok=True)
                    heartbeat.write_text(heartbeat_text, encoding="utf-8")
                clock = AdvancingClock(float(self.runtime.now()))
                launches: list[tuple[str, ...]] = []
                children = [FakeChild([None]), FakeChild([None])]

                def popen(command: tuple[str, ...]) -> FakeChild:
                    launches.append(command)
                    if len(launches) == 2:
                        heartbeat.parent.mkdir(parents=True, exist_ok=True)
                        heartbeat.write_text(f"{int(clock())}\n", encoding="utf-8")
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
                        "RESTART_DAEMONS_HEARTBEAT_FRESH_SECONDS": "3",
                        "RESTART_DAEMONS_STOP_GRACE_SECONDS": "1",
                    },
                    popen=popen,
                    sleeper=clock.sleep,
                    clock=clock,
                    getpid=lambda: 22222,
                    max_supervision_cycles=5,
                )

                self.assertEqual([FAKE_COMMAND, FAKE_COMMAND], launches)
                self.assertEqual([1.0, 1.0, 1.0, 1.0, 1.0], clock.sleeps)
                self.assertEqual(1, children[0].terminated)
                self.assertEqual([1], children[0].wait_timeouts)
                died = (target_root / ".refactor-loop" / "logs" / "phase9_router_daemon.died").read_text(encoding="utf-8")
                self.assertIn("terminating child and restarting same command", died)
                self.assertEqual(f"{self.runtime.now() + 4}\n", heartbeat.read_text(encoding="utf-8"))

    def test_wrapper_rereads_heartbeat_each_cycle_and_fresh_touch_stops_stale_kill(self) -> None:
        heartbeat = self.heartbeat_path("phase9_router_daemon")
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        heartbeat.write_text(f"{self.runtime.now() - 120}\n", encoding="utf-8")
        launches: list[tuple[str, ...]] = []
        child = FakeChild([None, None])

        def popen(command: tuple[str, ...]) -> FakeChild:
            launches.append(command)
            return child

        def sleeper(_seconds: float) -> None:
            self.runtime._now += 31
            heartbeat.write_text(f"{self.runtime.now()}\n", encoding="utf-8")

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
            sleeper=sleeper,
            clock=lambda: self.runtime.now(),
            getpid=lambda: 22223,
            max_supervision_cycles=2,
        )

        self.assertEqual([FAKE_COMMAND], launches)
        self.assertEqual(0, child.terminated)
        self.assertEqual(f"{self.runtime.now()}\n", heartbeat.read_text(encoding="utf-8"))

    def test_wrapper_restarts_immediately_for_malformed_and_future_heartbeat_without_writing_it(self) -> None:
        cases = {
            "malformed": "not-a-timestamp\n",
            "future": f"{self.runtime.now() + 120}\n",
        }
        for case, heartbeat_text in cases.items():
            with self.subTest(case=case):
                target_root = self.repo / case
                heartbeat = target_root / ".refactor-loop" / "heartbeats" / "phase9_router_daemon.ts"
                heartbeat.parent.mkdir(parents=True, exist_ok=True)
                heartbeat.write_text(heartbeat_text, encoding="utf-8")
                original_heartbeat = heartbeat.read_text(encoding="utf-8")
                clock = AdvancingClock(float(self.runtime.now()))
                launches: list[tuple[str, ...]] = []
                children = [FakeChild([None]), FakeChild([None])]

                def popen(command: tuple[str, ...]) -> FakeChild:
                    launches.append(command)
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
                    sleeper=clock.sleep,
                    clock=clock,
                    getpid=lambda: 22222,
                    max_supervision_cycles=1,
                )

                self.assertEqual([FAKE_COMMAND, FAKE_COMMAND], launches)
                self.assertEqual([1.0], clock.sleeps)
                self.assertEqual(1, children[0].terminated)
                self.assertEqual([1], children[0].wait_timeouts)
                died = (target_root / ".refactor-loop" / "logs" / "phase9_router_daemon.died").read_text(encoding="utf-8")
                if case == "future":
                    self.assertIn("heartbeat-future; terminating child and restarting same command", died)
                else:
                    self.assertIn("heartbeat-malformed; terminating child and restarting same command", died)
                self.assertEqual(original_heartbeat, heartbeat.read_text(encoding="utf-8"))

    def test_wrapper_logs_and_aborts_when_child_cannot_be_killed(self) -> None:
        heartbeat = self.heartbeat_path("phase9_router_daemon")
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        heartbeat.write_text(f"{self.runtime.now() - 120}\n", encoding="utf-8")
        clock = AdvancingClock(float(self.runtime.now()))
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
                    "RESTART_DAEMONS_HEARTBEAT_FRESH_SECONDS": "1",
                    "RESTART_DAEMONS_STOP_GRACE_SECONDS": "1",
                },
                popen=popen,
                sleeper=clock.sleep,
                clock=clock,
                getpid=lambda: 33333,
                max_supervision_cycles=2,
            )

        self.assertEqual([FAKE_COMMAND], launches)
        self.assertEqual(1, child.terminated)
        self.assertEqual(1, child.killed)
        died = (self.repo / ".refactor-loop" / "logs" / "phase9_router_daemon.died").read_text(encoding="utf-8")
        self.assertIn("heartbeat-stale:121s; terminating child and restarting same command", died)
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
        old_child_pid = self.runtime.child_pids_by_wrapper[old_pid]
        duplicate_pids = (424242, 424243, 424244)
        command = self.runtime.canonical_command(self.ctx, "concurrency_monitor", FAKE_COMMAND)
        self.runtime.live_pids.update(duplicate_pids)
        full_inventory = list(self.runtime.collect_inventory().processes)
        full_inventory.extend(DaemonProcess(pid, command, 1) for pid in duplicate_pids)
        self.runtime.inventory_override = DaemonProcessInventory(
            tuple(full_inventory)
        )

        self.run_helper()

        self.assertEqual((old_child_pid, self.config.stop_grace_seconds), self.runtime.terminated[0])
        for pid in (old_pid, *duplicate_pids):
            self.assertIn((pid, self.config.stop_grace_seconds), self.runtime.terminated)
        self.assert_start_count("concurrency_monitor", 2)
        self.assertNotEqual(old_pid, self.read_pid("concurrency_monitor"))

    def test_orphan_managed_child_lock_holder_is_reaped_and_restarted_once(self) -> None:
        self.run_helper()
        old_wrapper_pid = self.read_pid("phase9_router_daemon")
        orphan_child_pid = 515151
        target = restart.daemon_target(self.ctx, "phase9_router_daemon", FAKE_COMMAND)
        target.singleton_lock_file.write_text(
            json.dumps(
                DaemonSingletonMetadata.create(
                    daemon_name=target.name,
                    repo_root=self.ctx.repo_root,
                    heartbeat_file=target.heartbeat_file,
                    fingerprint_file=target.fingerprint_file,
                    command_sha256=restart._command_digest(target.command),
                    actor_pid=orphan_child_pid,
                    started_at=self.runtime.now(),
                ).to_json(),
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self.runtime.live_pids.discard(old_wrapper_pid)
        self.runtime.wrapper_commands.pop(old_wrapper_pid, None)
        self.runtime.live_pids.add(orphan_child_pid)
        self.runtime.inventory_override = DaemonProcessInventory((DaemonProcess(orphan_child_pid, " ".join(target.command), 1),))

        helper = RestartDaemons(self.ctx, self.config, runtime=self.runtime)
        helper.start_daemon("phase9_router_daemon", FAKE_COMMAND)

        new_pid = self.read_pid("phase9_router_daemon")
        self.assertEqual([(orphan_child_pid, self.config.stop_grace_seconds)], self.runtime.terminated)
        self.assertNotEqual(old_wrapper_pid, new_pid)
        self.assert_start_count("phase9_router_daemon", 2)

    def test_lockless_orphan_managed_children_are_reaped_on_restart(self) -> None:
        self.run_helper()
        old_wrapper_pid = self.read_pid("phase9_router_daemon")
        old_child_pid = self.runtime.child_pids_by_wrapper[old_wrapper_pid]
        orphan_child_pid = 515152
        target = restart.daemon_target(self.ctx, "phase9_router_daemon", FAKE_COMMAND)
        self.stale_heartbeat("phase9_router_daemon")
        self.runtime.live_pids.update({old_wrapper_pid, old_child_pid, orphan_child_pid})
        self.runtime.inventory_override = DaemonProcessInventory(
            (
                DaemonProcess(old_wrapper_pid, self.runtime.canonical_command(self.ctx, "phase9_router_daemon", FAKE_COMMAND), 1),
                DaemonProcess(old_child_pid, " ".join(target.command), old_wrapper_pid),
                DaemonProcess(orphan_child_pid, " ".join(target.command), 1),
            )
        )

        helper = RestartDaemons(self.ctx, self.config, runtime=self.runtime)
        with mock.patch("builtins.print") as print_mock:
            helper.start_daemon("phase9_router_daemon", FAKE_COMMAND)

        self.assertIn((old_child_pid, self.config.stop_grace_seconds), self.runtime.terminated)
        self.assertIn((old_wrapper_pid, self.config.stop_grace_seconds), self.runtime.terminated)
        self.assertIn((orphan_child_pid, self.config.stop_grace_seconds), self.runtime.terminated)
        self.assertTrue(
            any("ORPHAN_REAP daemon=phase9_router_daemon pids=515152" in str(call.args[0]) for call in print_mock.mock_calls)
        )
        self.assert_start_count("phase9_router_daemon", 2)

    def test_duplicate_canonical_matching_ignores_other_repo_and_non_allowlist_commands(self) -> None:
        command = self.runtime.canonical_command(self.ctx, "concurrency_monitor", FAKE_COMMAND)
        other_repo = command.replace(str(self.ctx.repo_root), "/tmp/other-repo")
        non_allowlist = self.runtime.canonical_command(self.ctx, "not_allowlisted", FAKE_COMMAND)
        inventory = DaemonProcessInventory(
            (
                DaemonProcess(111, command, 1),
                DaemonProcess(222, other_repo, 1),
                DaemonProcess(333, non_allowlist, 1),
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

    def test_daemon_instance_projects_wrappers_children_and_bounded_lock_holders(self) -> None:
        target = restart.daemon_target(self.ctx, "phase9_router_daemon", FAKE_COMMAND)
        wrapper_command = self.runtime.canonical_command(self.ctx, "phase9_router_daemon", FAKE_COMMAND)
        lock_holder = self.repo / ".refactor-loop" / "phase9-router.lock"
        lock_holder.write_text("pid=222\n", encoding="utf-8")
        other_lock = self.repo / ".refactor-loop" / "other.lock"
        other_lock.write_text("333\n", encoding="utf-8")
        inventory = DaemonProcessInventory(
            (
                DaemonProcess(111, wrapper_command, 1),
                DaemonProcess(222, " ".join(target.command), 111),
                DaemonProcess(333, " ".join(target.command), 1),
                DaemonProcess(444, "python3 unrelated.py", 1),
            )
        )
        self.runtime.live_pids.update({111, 222, 333, 444})

        projection = inventory.daemon_instance(
            name="phase9_router_daemon",
            repo_root=self.ctx.repo_root,
            pid_file=target.pid_file,
            died_file=target.died_file,
            command=target.command,
            lock_files=(lock_holder,),
            is_alive=self.runtime.pid_alive,
        )

        self.assertEqual((111,), projection.live_wrapper_pids)
        self.assertEqual((222,), projection.canonical_child_pids)
        self.assertEqual((333,), projection.orphan_child_pids)
        self.assertEqual((222, 333), projection.live_managed_child_pids)
        self.assertEqual((222,), projection.bounded_lock_holder_pids)
        self.assertEqual((111, 222), projection.repair_pids)
        self.assertEqual((), projection.singleton_holder_pids)

    def test_orphan_child_matching_requires_current_absolute_skill_command_path(self) -> None:
        template = ("python3", "{skill_root}/scripts/consensus-rnd-cli", "phase9-router", "--daemon")
        target = restart.daemon_target(self.ctx, "phase9_router_daemon", template)
        other_skill_command = " ".join(target.command).replace(
            str(self.ctx.skill_root / "scripts" / "consensus-rnd-cli"),
            "/tmp/other-skill/scripts/consensus-rnd-cli",
        )
        inventory = DaemonProcessInventory(
            (
                DaemonProcess(111, " ".join(target.command), 1),
                DaemonProcess(222, other_skill_command, 1),
            )
        )
        self.runtime.live_pids.update({111, 222})

        projection = inventory.daemon_instance(
            name="phase9_router_daemon",
            repo_root=self.ctx.repo_root,
            pid_file=target.pid_file,
            died_file=target.died_file,
            command=target.command,
            is_alive=self.runtime.pid_alive,
        )

        self.assertEqual((111,), projection.orphan_child_pids)

    def test_daemon_instance_projects_lockless_orphans_excluding_pid_file_and_locks(self) -> None:
        target = restart.daemon_target(self.ctx, "phase9_router_daemon", FAKE_COMMAND)
        target.pid_file.parent.mkdir(parents=True, exist_ok=True)
        target.pid_file.write_text("333\n", encoding="utf-8")
        lock_holder = self.repo / ".refactor-loop" / "phase9-router.lock"
        lock_holder.write_text("pid=444\n", encoding="utf-8")
        inventory = DaemonProcessInventory(
            (
                DaemonProcess(333, " ".join(target.command), 1),
                DaemonProcess(444, " ".join(target.command), 1),
                DaemonProcess(555, " ".join(target.command), 1),
            )
        )
        self.runtime.live_pids.update({333, 444, 555})

        projection = inventory.daemon_instance(
            name="phase9_router_daemon",
            repo_root=self.ctx.repo_root,
            pid_file=target.pid_file,
            died_file=target.died_file,
            command=target.command,
            lock_files=(lock_holder,),
            is_alive=self.runtime.pid_alive,
        )

        self.assertEqual((333, 444, 555), projection.orphan_child_pids)
        self.assertEqual((444,), projection.bounded_lock_holder_pids)
        self.assertEqual((), projection.singleton_holder_pids)

    def test_daemon_instance_detects_macos_shaped_wrapper_child_by_ppid(self) -> None:
        target = restart.daemon_target(self.ctx, "phase9_router_daemon", FAKE_COMMAND)
        wrapper_command = self.runtime.canonical_command(self.ctx, "phase9_router_daemon", FAKE_COMMAND)
        inventory = DaemonProcessInventory(
            (
                DaemonProcess(848, wrapper_command, 1),
                DaemonProcess(857, " ".join(("python3", "/different/install/consensus-rnd-cli", "phase9-router", "--daemon")), 848),
            )
        )
        self.runtime.live_pids.update({848, 857})

        projection = inventory.daemon_instance(
            name="phase9_router_daemon",
            repo_root=self.ctx.repo_root,
            pid_file=target.pid_file,
            died_file=target.died_file,
            command=target.command,
            lock_files=(),
            is_alive=self.runtime.pid_alive,
        )

        self.assertEqual((848,), projection.live_wrapper_pids)
        self.assertEqual((857,), projection.canonical_child_pids)
        self.assertEqual((), projection.orphan_child_pids)
        self.assertEqual((857,), projection.live_managed_child_pids)

    def test_process_inventory_collect_parses_pid_ppid_and_command_rows(self) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str]) -> FakeCommandResult:
            calls.append(command)
            return FakeCommandResult(
                returncode=0,
                stdout=(
                    " 848     1 "
                    "/usr/bin/python3 -c wrapper phase9_router_daemon /repo /pid /died python3 cli\n"
                    " 857   848 python3 /tmp/consensus-rnd-cli "
                    "phase9-router --daemon\n"
                ),
            )

        inventory = DaemonProcessInventory.collect(command_runner=runner)

        self.assertEqual([["ps", "-eo", "pid=,ppid=,command="]], calls)
        self.assertEqual("available", inventory.status)
        self.assertEqual("", inventory.error)
        self.assertEqual(
            (
                DaemonProcess(
                    848,
                    "/usr/bin/python3 -c wrapper phase9_router_daemon /repo /pid /died python3 cli",
                    1,
                ),
                DaemonProcess(
                    857,
                    "python3 /tmp/consensus-rnd-cli " + "phase9-router --daemon",
                    848,
                ),
            ),
            inventory.processes,
        )

    def test_process_inventory_collect_ignores_malformed_pid_or_ppid_rows(self) -> None:
        def runner(_command: list[str]) -> FakeCommandResult:
            return FakeCommandResult(
                returncode=0,
                stdout=(
                    " 111 1 python3 good.py\n"
                    "not-a-pid 1 python3 bad.py\n"
                    "222 not-a-ppid python3 bad.py\n"
                    "333\n"
                    "444 1\n"
                    "\n"
                ),
            )

        inventory = DaemonProcessInventory.collect(command_runner=runner)

        self.assertEqual((DaemonProcess(111, "python3 good.py", 1),), inventory.processes)
        self.assertEqual("available", inventory.status)

    def test_process_inventory_collect_nonzero_result_is_unavailable_with_diagnostic(self) -> None:
        def runner(_command: list[str]) -> FakeCommandResult:
            return FakeCommandResult(returncode=2, stderr="ps denied\n")

        inventory = DaemonProcessInventory.collect(command_runner=runner)

        self.assertEqual((), inventory.processes)
        self.assertEqual("unavailable", inventory.status)
        self.assertEqual("ps denied", inventory.error)

    def test_process_inventory_collect_runner_exception_is_unavailable_with_diagnostic(self) -> None:
        def runner(_command: list[str]) -> FakeCommandResult:
            raise RuntimeError("ps exploded")

        inventory = DaemonProcessInventory.collect(command_runner=runner)

        self.assertEqual((), inventory.processes)
        self.assertEqual("unavailable", inventory.status)
        self.assertEqual("RuntimeError('ps exploded')", inventory.error)

    def test_restart_wrapper_matching_accepts_skill_root_and_command_normalization_variance(self) -> None:
        template = ("python3", "{skill_root}/scripts/consensus-rnd-cli", "phase9-router", "--daemon", "--interval", "120")
        target = restart.daemon_target(self.ctx, "phase9_router_daemon", template)
        old_skill_command = self.runtime.canonical_command(self.ctx, "phase9_router_daemon", template).replace(
            str(self.skill / "scripts" / "consensus-rnd-cli"),
            "/opt/old-skill/scripts/consensus-rnd-cli",
        )
        spaced_command = f"  {old_skill_command.replace(' ', '   ')}  "
        inventory = DaemonProcessInventory((DaemonProcess(444, spaced_command, 1),))
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

    def test_restart_wrapper_matching_accepts_macos_framework_python_launcher(self) -> None:
        target = restart.daemon_target(self.ctx, "phase9_router_daemon", FAKE_COMMAND)
        command = self.runtime.framework_python_wrapper_command(self.ctx, "phase9_router_daemon", FAKE_COMMAND)
        inventory = DaemonProcessInventory((DaemonProcess(445, command, 1),))
        self.runtime.live_pids.add(445)

        live = inventory.live_restart_wrappers(
            name="phase9_router_daemon",
            repo_root=self.ctx.repo_root,
            pid_file=target.pid_file,
            died_file=target.died_file,
            command=target.command,
            is_alive=self.runtime.pid_alive,
        )

        self.assertEqual((445,), live)

    def test_restart_wrapper_matching_rejects_non_python_launcher(self) -> None:
        target = restart.daemon_target(self.ctx, "phase9_router_daemon", FAKE_COMMAND)
        command = self.runtime.framework_python_wrapper_command(self.ctx, "phase9_router_daemon", FAKE_COMMAND)
        command = command.replace(MACOS_FRAMEWORK_PYTHON, "/usr/bin/ruby", 1)
        inventory = DaemonProcessInventory((DaemonProcess(446, command, 1),))
        self.runtime.live_pids.add(446)

        live = inventory.live_restart_wrappers(
            name="phase9_router_daemon",
            repo_root=self.ctx.repo_root,
            pid_file=target.pid_file,
            died_file=target.died_file,
            command=target.command,
            is_alive=self.runtime.pid_alive,
        )

        self.assertEqual((), live)

    def test_restart_wrapper_matching_rejects_unknown_daemon_even_with_matching_shape(self) -> None:
        command = self.runtime.canonical_command(self.ctx, "not_allowlisted", FAKE_COMMAND)
        target = restart.daemon_target(self.ctx, "concurrency_monitor", FAKE_COMMAND)
        inventory = DaemonProcessInventory((DaemonProcess(555, command, 1),))
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

    def test_fresh_pid_heartbeat_fingerprint_requires_live_child_held_singleton_lock(self) -> None:
        self.run_helper()
        old_pid = self.read_pid("concurrency_monitor")
        target = restart.daemon_target(self.ctx, "concurrency_monitor", FAKE_COMMAND)
        target.singleton_lock_file.unlink()
        self.assertEqual("missing", self.runtime.probe_singleton(target).state)

        helper = RestartDaemons(self.ctx, self.config, runtime=self.runtime)
        helper.start_daemon("concurrency_monitor", FAKE_COMMAND)

        self.assertNotEqual(old_pid, self.read_pid("concurrency_monitor"))
        self.assertEqual([(old_pid, self.config.stop_grace_seconds)], self.runtime.terminated)
        self.assert_start_count("concurrency_monitor", 2)

    def test_fresh_singleton_skips_with_macos_framework_python_wrapper_and_child(self) -> None:
        target = restart.daemon_target(self.ctx, "concurrency_monitor", FAKE_COMMAND)
        pid = 505050
        target.pid_file.parent.mkdir(parents=True, exist_ok=True)
        target.pid_file.write_text(f"{pid}\n", encoding="utf-8")
        target.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
        target.heartbeat_file.write_text(f"{self.runtime.now()}\n", encoding="utf-8")
        restart.DaemonLaunchFingerprint.current(self.ctx, "concurrency_monitor", target.command).write(target.fingerprint_file)
        complete_tick(
            self.ctx.repo_root,
            begin_tick(self.ctx.repo_root, "concurrency_monitor", now=self.runtime.now(), pid=505051),
            now=self.runtime.now(),
        )
        write_singleton_metadata(self.ctx.repo_root, target, actor_pid=505051, started_at=self.runtime.now())
        self.runtime.live_pids.update({pid, 505051})
        self.runtime.inventory_override = DaemonProcessInventory(
            (
                DaemonProcess(pid, self.runtime.framework_python_wrapper_command(self.ctx, "concurrency_monitor", FAKE_COMMAND), 1),
                DaemonProcess(505051, self.runtime.framework_python_child_command(target.command), pid),
            )
        )

        helper = RestartDaemons(self.ctx, self.config, runtime=self.runtime)
        helper.start_daemon("concurrency_monitor", FAKE_COMMAND)

        self.assertEqual(pid, self.read_pid("concurrency_monitor"))
        self.assert_start_count("concurrency_monitor", 0)
        self.assertEqual([], self.runtime.terminated)

    def test_fresh_singleton_keeps_orphan_child_diagnostic_without_reap_or_restart(self) -> None:
        self.run_helper()
        wrapper_pid = self.read_pid("concurrency_monitor")
        child_pid = self.runtime.child_pids_by_wrapper[wrapper_pid]
        orphan_child_pid = 535353
        target = restart.daemon_target(self.ctx, "concurrency_monitor", FAKE_COMMAND)
        self.runtime.live_pids.add(orphan_child_pid)
        self.runtime.inventory_override = DaemonProcessInventory(
            (
                DaemonProcess(wrapper_pid, self.runtime.canonical_command(self.ctx, "concurrency_monitor", FAKE_COMMAND), 1),
                DaemonProcess(child_pid, " ".join(target.command), wrapper_pid),
                DaemonProcess(orphan_child_pid, " ".join(target.command), 1),
            )
        )

        helper = RestartDaemons(self.ctx, self.config, runtime=self.runtime)
        helper.start_daemon("concurrency_monitor", FAKE_COMMAND)

        self.assertEqual(wrapper_pid, self.read_pid("concurrency_monitor"))
        self.assertEqual([], self.runtime.terminated)
        self.assert_start_count("concurrency_monitor", 1)

    def test_orphan_holding_singleton_with_live_wrapper_is_reaped_and_restarted(self) -> None:
        # #885: an old-instance orphan child (ppid=1) holds the singleton lock and
        # renews the heartbeat while a current wrapper+child also exist. The fresh
        # heartbeat must not let restart skip: the orphan is the live actor running
        # stale code, so it must be reaped and the daemon restarted once.
        self.run_helper()
        wrapper_pid = self.read_pid("concurrency_monitor")
        child_pid = self.runtime.child_pids_by_wrapper[wrapper_pid]
        orphan_child_pid = 545454
        target = restart.daemon_target(self.ctx, "concurrency_monitor", FAKE_COMMAND)
        write_singleton_metadata(self.ctx.repo_root, target, actor_pid=orphan_child_pid, started_at=self.runtime.now())
        self.runtime.live_pids.add(orphan_child_pid)
        self.runtime.inventory_override = DaemonProcessInventory(
            (
                DaemonProcess(wrapper_pid, self.runtime.canonical_command(self.ctx, "concurrency_monitor", FAKE_COMMAND), 1),
                DaemonProcess(child_pid, " ".join(target.command), wrapper_pid),
                DaemonProcess(orphan_child_pid, " ".join(target.command), 1),
            )
        )

        helper = RestartDaemons(self.ctx, self.config, runtime=self.runtime)
        helper.start_daemon("concurrency_monitor", FAKE_COMMAND)

        self.assertIn((orphan_child_pid, self.config.stop_grace_seconds), self.runtime.terminated)
        self.assert_start_count("concurrency_monitor", 2)

    def test_held_malformed_singleton_lock_skips_restart_without_reaping_or_launching(self) -> None:
        self.run_helper()
        wrapper_pid = self.read_pid("concurrency_monitor")
        target = restart.daemon_target(self.ctx, "concurrency_monitor", FAKE_COMMAND)
        original_pid_file = target.pid_file.read_text(encoding="utf-8")
        target.singleton_lock_file.write_text("{not-json\n", encoding="utf-8")
        self.stale_heartbeat("concurrency_monitor")
        before_start_count = self.start_count("concurrency_monitor")

        helper = RestartDaemons(self.ctx, self.config, runtime=self.runtime)
        helper.start_daemon("concurrency_monitor", FAKE_COMMAND)

        self.assertEqual(original_pid_file, target.pid_file.read_text(encoding="utf-8"))
        self.assertEqual(wrapper_pid, self.read_pid("concurrency_monitor"))
        self.assertEqual([], self.runtime.terminated)
        self.assert_start_count("concurrency_monitor", before_start_count)
        self.assertEqual("held-malformed", self.runtime.probe_singleton(target).state)

    def test_free_singleton_lock_metadata_is_diagnostic_and_allows_restart(self) -> None:
        self.run_helper()
        old_wrapper_pid = self.read_pid("concurrency_monitor")
        old_child_pid = self.runtime.child_pids_by_wrapper[old_wrapper_pid]
        target = restart.daemon_target(self.ctx, "concurrency_monitor", FAKE_COMMAND)
        self.runtime.live_pids.discard(old_child_pid)
        self.runtime.child_pids_by_wrapper.pop(old_wrapper_pid)
        self.runtime.child_commands.pop(old_child_pid)
        self.stale_heartbeat("concurrency_monitor")
        self.assertEqual("free", self.runtime.probe_singleton(target).state)

        helper = RestartDaemons(self.ctx, self.config, runtime=self.runtime)
        helper.start_daemon("concurrency_monitor", FAKE_COMMAND)

        new_wrapper_pid = self.read_pid("concurrency_monitor")
        self.assertNotEqual(old_wrapper_pid, new_wrapper_pid)
        self.assertEqual([(old_wrapper_pid, self.config.stop_grace_seconds)], self.runtime.terminated)
        self.assert_start_count("concurrency_monitor", 2)
        self.assertEqual("held", self.runtime.probe_singleton(target).state)

    def test_macos_framework_python_orphan_child_lock_holder_is_reaped_and_restarted_once(self) -> None:
        self.run_helper()
        old_wrapper_pid = self.read_pid("phase9_router_daemon")
        orphan_child_pid = 525252
        target = restart.daemon_target(self.ctx, "phase9_router_daemon", FAKE_COMMAND)
        write_singleton_metadata(self.ctx.repo_root, target, actor_pid=orphan_child_pid, started_at=self.runtime.now())
        self.runtime.live_pids.discard(old_wrapper_pid)
        self.runtime.wrapper_commands.pop(old_wrapper_pid, None)
        self.runtime.live_pids.add(orphan_child_pid)
        self.runtime.inventory_override = DaemonProcessInventory(
            (DaemonProcess(orphan_child_pid, self.runtime.framework_python_child_command(target.command), 1),)
        )

        helper = RestartDaemons(self.ctx, self.config, runtime=self.runtime)
        helper.start_daemon("phase9_router_daemon", FAKE_COMMAND)

        new_pid = self.read_pid("phase9_router_daemon")
        self.assertEqual(
            [(orphan_child_pid, self.config.stop_grace_seconds)],
            [item for item in self.runtime.terminated if item[0] == orphan_child_pid],
        )
        self.assertNotEqual(old_wrapper_pid, new_pid)
        self.assert_start_count("phase9_router_daemon", 2)

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
                DaemonProcess(old_pid, command, 1),
                DaemonProcess(duplicate_pid, command, 1),
            )
        )

        report = self.collect_status_with_fake_allowlist(inventory)

        by_name = {daemon.name: daemon for daemon in report.daemons}
        self.assertEqual("stale", by_name["concurrency_monitor"].status)
        self.assertEqual(1, by_name["concurrency_monitor"].duplicate_canonical_wrappers)
        self.assertEqual(old_pid, self.read_pid("concurrency_monitor"))
        self.assert_start_count("concurrency_monitor", 1)

    def test_daemon_status_reports_orphan_child_lock_holder_stale_without_repair(self) -> None:
        self.run_helper()
        old_pid = self.read_pid("phase9_router_daemon")
        orphan_child_pid = 616161
        target = restart.daemon_target(self.ctx, "phase9_router_daemon", FAKE_COMMAND)
        (self.repo / ".refactor-loop" / "state").mkdir(parents=True, exist_ok=True)
        (self.repo / ".refactor-loop" / "state" / "active-controller-status.json").write_text(
            json.dumps({"active_controller": "owner"}) + "\n",
            encoding="utf-8",
        )
        (self.repo / ".refactor-loop" / "phase9-router.lock").write_text(f"pid={orphan_child_pid}\n", encoding="utf-8")
        self.runtime.live_pids.discard(old_pid)
        self.runtime.wrapper_commands.pop(old_pid, None)
        self.runtime.live_pids.add(orphan_child_pid)
        inventory = DaemonProcessInventory((DaemonProcess(orphan_child_pid, " ".join(target.command), 1),))

        report = self.collect_status_with_fake_allowlist(inventory)

        daemon = next(item for item in report.daemons if item.name == "phase9_router_daemon")
        payload = daemon.to_json()
        self.assertEqual("stale", daemon.status)
        self.assertEqual("orphan-lock-holders:1", daemon.stale_reason)
        self.assertEqual([orphan_child_pid], payload["managed_child_pids"])
        self.assertEqual([], payload["canonical_child_pids"])
        self.assertEqual([orphan_child_pid], payload["orphan_child_pids"])
        self.assertEqual([orphan_child_pid], payload["bounded_lock_holder_pids"])
        self.assertNotIn("orphan_managed_child_pids", payload)
        self.assertEqual(old_pid, self.read_pid("phase9_router_daemon"))
        self.assert_start_count("phase9_router_daemon", 1)

    def test_daemon_status_reports_running_for_macos_framework_python_wrapper_and_child(self) -> None:
        target = restart.daemon_target(self.ctx, "phase9_router_daemon", FAKE_COMMAND)
        pid = 606060
        child_pid = 606061
        target.pid_file.parent.mkdir(parents=True, exist_ok=True)
        target.pid_file.write_text(f"{pid}\n", encoding="utf-8")
        target.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
        target.heartbeat_file.write_text(f"{self.runtime.now()}\n", encoding="utf-8")
        restart.DaemonLaunchFingerprint.current(self.ctx, "phase9_router_daemon", target.command).write(target.fingerprint_file)
        complete_tick(
            self.ctx.repo_root,
            begin_tick(self.ctx.repo_root, "phase9_router_daemon", now=self.runtime.now(), pid=child_pid),
            now=self.runtime.now(),
        )
        write_singleton_metadata(self.ctx.repo_root, target, actor_pid=child_pid, started_at=self.runtime.now())
        (self.repo / ".refactor-loop" / "state").mkdir(parents=True, exist_ok=True)
        (self.repo / ".refactor-loop" / "state" / "active-controller-status.json").write_text(
            json.dumps({"active_controller": "owner"}) + "\n",
            encoding="utf-8",
        )
        self.runtime.live_pids.update({pid, child_pid})
        inventory = DaemonProcessInventory(
            (
                DaemonProcess(pid, self.runtime.framework_python_wrapper_command(self.ctx, "phase9_router_daemon", FAKE_COMMAND), 1),
                DaemonProcess(child_pid, self.runtime.framework_python_child_command(target.command), pid),
            )
        )

        report = self.collect_status_with_fake_allowlist(inventory)

        daemon = next(item for item in report.daemons if item.name == "phase9_router_daemon")
        payload = daemon.to_json()
        self.assertEqual("running", daemon.status)
        self.assertEqual([child_pid], payload["managed_child_pids"])
        self.assertEqual([child_pid], payload["canonical_child_pids"])
        self.assertEqual([], payload["orphan_child_pids"])
        self.assertEqual([child_pid], payload["bounded_lock_holder_pids"])

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
            "DaemonInstanceProjection",
            "canonical_child_pids",
            "orphan_child_pids",
            "live_managed_children",
            "bounded_lock_holder_pids",
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
