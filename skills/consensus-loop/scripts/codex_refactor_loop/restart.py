"""Restart-helper-managed daemon supervisor."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from .active_controller import require_active_controller, write_active_controller_status
from .context import LoopContext, LoopContextError
from .daemon_singleton import DaemonSingletonProjection, lock_path as daemon_singleton_lock_path, probe as probe_daemon_singleton
from .gh_accounting import accounting_env
from .runtime_retention import retain_runtime, runtime_retention_enabled
from .update_check import maybe_run_update_check


CLI_ENTRYPOINT_NAME = "consensus-rnd-cli"

DAEMON_COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("concurrency_monitor", ("python3", "{skill_root}/scripts/consensus-rnd-cli", "concurrency", "--daemon")),
    ("comment-monitor", ("python3", "{skill_root}/scripts/consensus-rnd-cli", "comment-monitor", "--daemon")),
    ("codex-progress-reporter", ("python3", "{skill_root}/scripts/consensus-rnd-cli", "progress-reporter", "--daemon")),
    ("dev_sync_daemon", ("python3", "{skill_root}/scripts/consensus-rnd-cli", "dev-sync", "--daemon")),
    (
        "phase9_router_daemon",
        ("python3", "{skill_root}/scripts/consensus-rnd-cli", "phase9-router", "--daemon", "--interval", "{phase9_router_interval_seconds}"),
    ),
    ("closed_label_reconciler", ("python3", "{skill_root}/scripts/consensus-rnd-cli", "closed-label-reconciler", "--daemon")),
    (
        "wakeup_runner_daemon",
        ("python3", "{skill_root}/scripts/consensus-rnd-cli", "wakeup-runner", "--daemon", "--interval-seconds", "{wakeup_runner_interval_seconds}"),
    ),
)

DAEMON_COMMAND_ENV_PLACEHOLDERS: dict[str, tuple[str, str]] = {
    "{phase9_router_interval_seconds}": ("PHASE9_ROUTER_INTERVAL_SECONDS", "120"),
    "{wakeup_runner_interval_seconds}": ("WAKEUP_RUNNER_INTERVAL_SECONDS", "120"),
}


SUPERVISOR_REPLACED_DAEMON_TARGETS = frozenset({"comment-monitor"})


def restart_managed_daemon_names() -> tuple[str, ...]:
    return tuple(name for name, _command in DAEMON_COMMANDS)


def restart_managed_daemon_names_for_context(ctx: LoopContext) -> tuple[str, ...]:
    return tuple(name for name, _command in restart_daemon_commands_for_context(ctx))


SUPERVISOR_DAEMON_COMMAND: tuple[str, tuple[str, ...]] = (
    "controller_tick_supervisor",
    ("python3", "-m", "codex_refactor_loop.supervisor", "--daemon"),
)


FORBIDDEN_LIFECYCLE_AUTHORITY = (
    "create/merge/close PR",
    "open/close issue",
    "tag/release publish",
    "generic lifecycle actor",
)


@dataclass(frozen=True)
class RestartConfig:
    heartbeat_fresh_seconds: int = int(os.environ.get("RESTART_DAEMONS_HEARTBEAT_FRESH_SECONDS", "90"))
    heartbeat_interval: int = int(os.environ.get("RESTART_DAEMONS_HEARTBEAT_INTERVAL", "30"))
    stop_grace_seconds: int = int(os.environ.get("RESTART_DAEMONS_STOP_GRACE_SECONDS", "5"))


@dataclass(frozen=True)
class DaemonTarget:
    name: str
    command: tuple[str, ...]
    pid_file: Path
    heartbeat_file: Path
    fingerprint_file: Path
    died_file: Path
    singleton_lock_file: Path


@dataclass(frozen=True)
class DaemonHeartbeatStatus:
    state: str
    age_seconds: int | None
    reason: str

    @property
    def fresh(self) -> bool:
        return self.state == "fresh"


@dataclass(frozen=True)
class DaemonLaunchFingerprint:
    daemon_name: str
    command: tuple[str, ...]
    entrypoint_sha256: str
    package_tree_sha256: str
    fingerprinted_files_count: int
    host_env_path: str
    host_env_sha256: str

    @classmethod
    def current(
        cls,
        ctx: LoopContext,
        daemon_name: str,
        command: Sequence[str],
        package_tree_sha256: str | None = None,
        fingerprinted_files_count: int | None = None,
    ) -> "DaemonLaunchFingerprint":
        if package_tree_sha256 is None or fingerprinted_files_count is None:
            package_tree_sha256, fingerprinted_files_count = _package_tree_digest(ctx)
        return cls(
            daemon_name=daemon_name,
            command=tuple(command),
            entrypoint_sha256=_file_digest(ctx.skill_root / "scripts" / CLI_ENTRYPOINT_NAME),
            package_tree_sha256=package_tree_sha256,
            fingerprinted_files_count=fingerprinted_files_count,
            host_env_path=str(ctx.host_env_location) if ctx.host_env_location is not None else "",
            host_env_sha256=_file_digest(ctx.host_env_location) if ctx.host_env_location is not None else "missing",
        )

    @classmethod
    def read(cls, path: Path) -> "DaemonLaunchFingerprint | None":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        try:
            daemon_name = raw["daemon_name"]
            command = raw["command"]
            entrypoint_sha256 = raw["entrypoint_sha256"]
            package_tree_sha256 = raw["package_tree_sha256"]
            fingerprinted_files_count = raw["fingerprinted_files_count"]
            host_env_path = raw["host_env_path"]
            host_env_sha256 = raw["host_env_sha256"]
        except KeyError:
            return None
        if (
            not isinstance(daemon_name, str)
            or not isinstance(command, list)
            or not all(isinstance(part, str) for part in command)
            or not isinstance(entrypoint_sha256, str)
            or not isinstance(package_tree_sha256, str)
            or not isinstance(fingerprinted_files_count, int)
            or not isinstance(host_env_path, str)
            or not isinstance(host_env_sha256, str)
        ):
            return None
        return cls(
            daemon_name=daemon_name,
            command=tuple(command),
            entrypoint_sha256=entrypoint_sha256,
            package_tree_sha256=package_tree_sha256,
            fingerprinted_files_count=fingerprinted_files_count,
            host_env_path=host_env_path,
            host_env_sha256=host_env_sha256,
        )

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def matches(self, other: "DaemonLaunchFingerprint") -> bool:
        return self == other

    def to_json(self) -> dict[str, Any]:
        return {
            "daemon_name": self.daemon_name,
            "command": list(self.command),
            "entrypoint_sha256": self.entrypoint_sha256,
            "package_tree_sha256": self.package_tree_sha256,
            "fingerprinted_files_count": self.fingerprinted_files_count,
            "host_env_path": self.host_env_path,
            "host_env_sha256": self.host_env_sha256,
        }


@dataclass(frozen=True)
class DaemonProcess:
    pid: int
    command: str
    ppid: int | None = None


@dataclass(frozen=True)
class DaemonInstanceProjection:
    name: str
    pid_file_pid: int | None
    live_wrapper_pids: tuple[int, ...]
    canonical_child_pids: tuple[int, ...]
    orphan_child_pids: tuple[int, ...]
    bounded_lock_holder_pids: tuple[int, ...]
    singleton: DaemonSingletonProjection
    process_inventory_status: str = "available"
    process_inventory_error: str = ""

    @property
    def live_managed_child_pids(self) -> tuple[int, ...]:
        return tuple(sorted(set(self.canonical_child_pids).union(self.orphan_child_pids)))

    @property
    def duplicate_wrapper_count(self) -> int:
        return max(0, len(self.live_wrapper_pids) - 1)

    @property
    def has_singleton_wrapper(self) -> bool:
        return len(self.live_wrapper_pids) == 1

    @property
    def orphan_lock_holder_pids(self) -> tuple[int, ...]:
        if self.has_singleton_wrapper:
            return ()
        return self.bounded_lock_holder_pids

    @property
    def singleton_holder_pids(self) -> tuple[int, ...]:
        holder = self.singleton.holder_pid
        if holder is None or holder <= 0:
            return ()
        return (holder,)

    @property
    def repair_pids(self) -> tuple[int, ...]:
        pids = set(self.live_wrapper_pids)
        pids.update(self.bounded_lock_holder_pids)
        if self.pid_file_pid is not None:
            pids.add(self.pid_file_pid)
        return tuple(sorted(pids))


@dataclass(frozen=True)
class DaemonProcessInventory:
    processes: tuple[DaemonProcess, ...]
    status: str = "available"
    error: str = ""

    @classmethod
    def collect(cls, *, command_runner=None) -> "DaemonProcessInventory":
        runner = command_runner or run_process_inventory
        try:
            result = runner(["ps", "-eo", "pid=,ppid=,command="])
        except Exception as exc:
            return cls((), status="unavailable", error=repr(exc))
        if result.returncode != 0:
            reason = (result.stderr or result.stdout or f"exit={result.returncode}").strip()
            return cls((), status="unavailable", error=reason or f"exit={result.returncode}")
        return cls(tuple(_parse_process_inventory(result.stdout)))

    def live_restart_wrappers(
        self,
        *,
        name: str,
        repo_root: Path,
        pid_file: Path,
        died_file: Path,
        command: Sequence[str],
        is_alive=None,
    ) -> tuple[int, ...]:
        if name not in restart_managed_daemon_names() and name != SUPERVISOR_DAEMON_COMMAND[0]:
            return ()
        alive = is_alive or pid_alive
        expected = RestartWrapperShape(
            name=name,
            repo_root=repo_root,
            pid_file=pid_file,
            died_file=died_file,
            command=tuple(command),
        )
        pids = []
        for process in self.processes:
            if process.pid <= 0 or not alive(process.pid):
                continue
            if not RestartWrapperShape.matches(process.command, expected):
                continue
            pids.append(process.pid)
        return tuple(sorted(set(pids)))

    def daemon_instance(
        self,
        *,
        name: str,
        repo_root: Path,
        pid_file: Path,
        died_file: Path,
        command: Sequence[str],
        lock_files: Sequence[Path] = (),
        singleton: DaemonSingletonProjection | None = None,
        is_alive=None,
    ) -> DaemonInstanceProjection:
        alive = is_alive or pid_alive
        singleton_projection = singleton or probe_daemon_singleton(repo_root, name)
        live_wrappers = self.live_restart_wrappers(
            name=name,
            repo_root=repo_root,
            pid_file=pid_file,
            died_file=died_file,
            command=command,
            is_alive=alive,
        )
        canonical_child_pids = self.canonical_child_pids(wrapper_pids=live_wrappers, is_alive=alive)
        orphan_child_pids = self.orphan_child_pids(
            name=name,
            command=command,
            canonical_child_pids=canonical_child_pids,
            is_alive=alive,
        )
        managed_child_pids = tuple(sorted(set(canonical_child_pids).union(orphan_child_pids)))
        lock_holder_pids = self.bounded_lock_holder_pids(
            name=name,
            child_pids=managed_child_pids,
            lock_files=lock_files,
            is_alive=alive,
        )
        return DaemonInstanceProjection(
            name=name,
            pid_file_pid=_read_pid(pid_file),
            live_wrapper_pids=live_wrappers,
            canonical_child_pids=canonical_child_pids,
            orphan_child_pids=orphan_child_pids,
            bounded_lock_holder_pids=lock_holder_pids,
            singleton=singleton_projection,
            process_inventory_status=self.status,
            process_inventory_error=self.error,
        )

    def canonical_child_pids(
        self,
        *,
        wrapper_pids: Sequence[int],
        is_alive=None,
    ) -> tuple[int, ...]:
        alive = is_alive or pid_alive
        wrappers = set(wrapper_pids)
        if not wrappers:
            return ()
        return tuple(
            sorted(
                {
                    process.pid
                    for process in self.processes
                    if process.pid > 0
                    and process.ppid in wrappers
                    and alive(process.pid)
                }
            )
        )

    def orphan_child_pids(
        self,
        *,
        name: str,
        command: Sequence[str],
        canonical_child_pids: Sequence[int],
        is_alive=None,
    ) -> tuple[int, ...]:
        if name not in restart_managed_daemon_names() and name != SUPERVISOR_DAEMON_COMMAND[0]:
            return ()
        alive = is_alive or pid_alive
        canonical = set(canonical_child_pids)
        pids = []
        for process in self.processes:
            if process.pid <= 0 or not alive(process.pid):
                continue
            if process.pid in canonical or process.ppid != 1:
                continue
            if not _restart_daemon_command_matches(process.command, command):
                continue
            pids.append(process.pid)
        return tuple(sorted(set(pids)))

    def live_managed_children(
        self,
        *,
        name: str,
        command: Sequence[str],
        is_alive=None,
    ) -> tuple[int, ...]:
        return self.orphan_child_pids(
            name=name,
            command=command,
            canonical_child_pids=(),
            is_alive=is_alive,
        )

    def bounded_lock_holder_pids(
        self,
        *,
        name: str,
        child_pids: Sequence[int],
        lock_files: Sequence[Path],
        is_alive=None,
    ) -> tuple[int, ...]:
        if name not in restart_managed_daemon_names() and name != SUPERVISOR_DAEMON_COMMAND[0]:
            return ()
        alive = is_alive or pid_alive
        child_pid_set = set(child_pids)
        holders = []
        for lock_file in lock_files:
            holder = _read_lock_holder_pid(lock_file)
            if holder is None or holder not in child_pid_set or not alive(holder):
                continue
            holders.append(holder)
        return tuple(sorted(set(holders)))

    def live_canonical_wrappers(
        self,
        *,
        name: str,
        repo_root: Path,
        pid_file: Path,
        died_file: Path,
        command: Sequence[str],
        is_alive=None,
    ) -> tuple[int, ...]:
        return self.live_restart_wrappers(
            name=name,
            repo_root=repo_root,
            pid_file=pid_file,
            died_file=died_file,
            command=command,
            is_alive=is_alive,
        )

@dataclass(frozen=True)
class ManagedChildCommandShape:
    @classmethod
    def matches(cls, command_line: str, expected: Sequence[str]) -> bool:
        return _restart_daemon_command_matches(command_line, expected)


@dataclass(frozen=True)
class RestartWrapperShape:
    name: str
    repo_root: Path
    pid_file: Path
    died_file: Path
    command: tuple[str, ...]

    @classmethod
    def matches(cls, command_line: str, expected: "RestartWrapperShape") -> bool:
        normalized = _normalize_process_command(command_line)
        actual_parts = normalized.split()
        if len(actual_parts) < 6 or not _is_python_launcher(actual_parts[0]) or actual_parts[1] != "-c":
            return False
        expected_tail = _normalize_process_command(
            " ".join([expected.name, str(expected.repo_root), str(expected.pid_file), str(expected.died_file)])
        )
        marker = f" {expected_tail} "
        if marker not in normalized:
            return False
        command_tail = normalized.rsplit(marker, 1)[1]
        return _restart_daemon_command_matches(command_tail, expected.command)


def daemon_target(ctx: LoopContext, name: str, command_template: Sequence[str]) -> DaemonTarget:
    command = tuple(_resolve_daemon_command_part(ctx, part) for part in command_template)
    return DaemonTarget(
        name=name,
        command=command,
        pid_file=ctx.paths.refactor_loop / "locks" / f"{name}.pid",
        heartbeat_file=ctx.paths.heartbeats / f"{name}.ts",
        fingerprint_file=ctx.paths.refactor_loop / "locks" / f"{name}.fingerprint.json",
        died_file=ctx.paths.logs / f"{name}.died",
        singleton_lock_file=daemon_singleton_lock_path(ctx.repo_root, name),
    )


def daemon_lock_files(ctx: LoopContext, name: str) -> tuple[Path, ...]:
    generic = (daemon_singleton_lock_path(ctx.repo_root, name),)
    relative_paths = {
        "dev_sync_daemon": (Path(".refactor-loop/dev-sync-daemon.lock"),),
        "phase9_router_daemon": (Path(".refactor-loop/phase9-router.lock"),),
    }.get(name, ())
    return generic + tuple(ctx.repo_root / relative_path for relative_path in relative_paths)


def daemon_targets(ctx: LoopContext, target: str = "all") -> tuple[DaemonTarget, ...]:
    names = {daemon_name for daemon_name, _command_template in DAEMON_COMMANDS}
    if target != "all" and target not in names:
        raise ValueError(f"unknown daemon target: {target}")
    return tuple(
        daemon_target(ctx, daemon_name, command_template)
        for daemon_name, command_template in restart_daemon_commands_for_context(ctx)
        if target == "all" or daemon_name == target
    )


def restart_daemon_commands_for_context(ctx: LoopContext) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not _truthy(ctx.host_env.get("CONTROLLER_TICK_SUPERVISOR_ENABLE")):
        return DAEMON_COMMANDS
    return tuple((name, command) for name, command in DAEMON_COMMANDS if name not in SUPERVISOR_REPLACED_DAEMON_TARGETS)


def read_daemon_pid(target: DaemonTarget) -> int | None:
    return _read_pid(target.pid_file)


def read_heartbeat_age_seconds(target: DaemonTarget, *, now: int | None = None) -> int | None:
    return read_heartbeat_status(target, RestartConfig(), now=now).age_seconds


def read_heartbeat_status(
    target: DaemonTarget,
    config: RestartConfig,
    *,
    now: int | None = None,
) -> DaemonHeartbeatStatus:
    current_time = int(time.time()) if now is None else now
    try:
        raw = target.heartbeat_file.read_text(encoding="utf-8").strip()
    except OSError:
        return DaemonHeartbeatStatus("missing", None, "heartbeat-missing")
    if not raw.isdigit():
        return DaemonHeartbeatStatus("malformed", None, "heartbeat-malformed")
    age = current_time - int(raw)
    if age < 0:
        return DaemonHeartbeatStatus("malformed", None, "heartbeat-future")
    if age >= config.heartbeat_fresh_seconds:
        return DaemonHeartbeatStatus("stale", age, f"heartbeat-stale:{age}s")
    return DaemonHeartbeatStatus("fresh", age, f"heartbeat-fresh:{age}s")


def read_stored_launch_fingerprint(target: DaemonTarget) -> DaemonLaunchFingerprint | None:
    return DaemonLaunchFingerprint.read(target.fingerprint_file)


def expected_launch_fingerprint(ctx: LoopContext, target: DaemonTarget) -> DaemonLaunchFingerprint:
    return DaemonLaunchFingerprint.current(ctx, target.name, target.command)


def heartbeat_is_fresh(target: DaemonTarget, config: RestartConfig, *, now: int | None = None) -> bool:
    return read_heartbeat_status(target, config, now=now).fresh


class RestartDaemonRuntime(Protocol):
    def now(self) -> int:
        ...

    def getpid(self) -> int:
        ...

    def sleep(self, seconds: float) -> None:
        ...

    def pid_alive(self, pid: int) -> bool:
        ...

    def collect_inventory(self) -> DaemonProcessInventory:
        ...

    def probe_singleton(self, target: DaemonTarget) -> DaemonSingletonProjection:
        ...

    def terminate_pid(self, pid: int, grace: int) -> None:
        ...

    def launch_wrapper(
        self,
        *,
        ctx: LoopContext,
        target: DaemonTarget,
        wrapper_code: str,
        env: dict[str, str],
        log_file: Path,
    ) -> Any:
        ...


class RealRestartDaemonRuntime:
    def now(self) -> int:
        return int(time.time())

    def getpid(self) -> int:
        return os.getpid()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def pid_alive(self, pid: int) -> bool:
        return pid_alive(pid)

    def collect_inventory(self) -> DaemonProcessInventory:
        return DaemonProcessInventory.collect()

    def probe_singleton(self, target: DaemonTarget) -> DaemonSingletonProjection:
        return probe_daemon_singleton(target.singleton_lock_file.parent.parent.parent, target.name)

    def terminate_pid(self, pid: int, grace: int) -> None:
        _terminate_pid(pid, grace)

    def launch_wrapper(
        self,
        *,
        ctx: LoopContext,
        target: DaemonTarget,
        wrapper_code: str,
        env: dict[str, str],
        log_file: Path,
    ) -> subprocess.Popen[bytes]:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_file.open("ab", buffering=0)
        try:
            return subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    wrapper_code,
                    target.name,
                    str(ctx.repo_root),
                    str(target.pid_file),
                    str(target.died_file),
                    *target.command,
                ],
                cwd=str(ctx.repo_root),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            log_handle.close()


class RestartDaemons:
    def __init__(
        self,
        ctx: LoopContext,
        config: RestartConfig | None = None,
        runtime: RestartDaemonRuntime | None = None,
    ) -> None:
        self.ctx = ctx
        self.config = config or RestartConfig()
        self.runtime = runtime or RealRestartDaemonRuntime()
        self.lock_dir = ctx.paths.refactor_loop / "locks" / "restart-daemons.lock"
        self._wrappers: list[Any] = []
        self._package_digest_cache: tuple[str, int] | None = None

    def run(self) -> int:
        self._prepare_dirs()
        decision = require_active_controller(self.ctx, "restart-daemons")
        write_active_controller_status(self.ctx, decision)
        if not decision.allowed:
            self._log(f"active_controller=noop:not-owner owner={decision.owner_device} status={decision.status}")
            return 0
        self._acquire_restart_lock()
        try:
            self._run_runtime_retention()
            if _truthy(self.ctx.host_env.get("CONTROLLER_TICK_SUPERVISOR_ENABLE")):
                self._stop_supervisor_replaced_daemons()
            for name, command in restart_daemon_commands_for_context(self.ctx):
                self.start_daemon(name, command)
            if _truthy(self.ctx.host_env.get("CONTROLLER_TICK_SUPERVISOR_ENABLE")):
                name, command = SUPERVISOR_DAEMON_COMMAND
                self.start_daemon(name, command)
        finally:
            self._release_restart_lock()
        self._run_update_check()
        return 0

    def _stop_supervisor_replaced_daemons(self) -> None:
        command_by_name = dict(DAEMON_COMMANDS)
        for name in sorted(SUPERVISOR_REPLACED_DAEMON_TARGETS):
            command_template = command_by_name.get(name)
            if command_template is None:
                continue
            target = daemon_target(self.ctx, name, command_template)
            inventory = self.runtime.collect_inventory()
            instance = inventory.daemon_instance(
                name=name,
                repo_root=self.ctx.repo_root,
                pid_file=target.pid_file,
                died_file=target.died_file,
                command=target.command,
                lock_files=daemon_lock_files(self.ctx, name),
                singleton=self.runtime.probe_singleton(target),
                is_alive=self.runtime.pid_alive,
            )
            pids = tuple(sorted(set(instance.singleton_holder_pids).union(instance.repair_pids)))
            self._terminate_pids(pids)
            target.pid_file.unlink(missing_ok=True)
            if pids:
                self._log(f"{name} stopped: supervisor-replaced pids={','.join(str(pid) for pid in pids)}")

    def start_daemon(self, name: str, command_template: Sequence[str]) -> None:
        target = daemon_target(self.ctx, name, command_template)
        pid_file = target.pid_file
        fingerprint_file = target.fingerprint_file
        hb_file = target.heartbeat_file
        log_file = self.ctx.paths.logs / f"{name}.log"
        died_file = target.died_file
        command = list(target.command)
        current_fingerprint = self._current_fingerprint(name, command)
        inventory = self.runtime.collect_inventory()
        instance = inventory.daemon_instance(
            name=name,
            repo_root=self.ctx.repo_root,
            pid_file=target.pid_file,
            died_file=died_file,
            command=target.command,
            lock_files=daemon_lock_files(self.ctx, name),
            singleton=self.runtime.probe_singleton(target),
            is_alive=self.runtime.pid_alive,
        )
        if instance.has_singleton_wrapper and self._singleton_check_fresh(target, current_fingerprint, instance):
            self._log(f"{name} skip: alive pid={pid_file.read_text(encoding='utf-8').strip()} heartbeat=fresh")
            return
        if self._singleton_lock_blocks_restart(instance):
            self._log(f"{name} skip: singleton-lock-held-without-reclaimable-holder reason={instance.singleton.reason}")
            return
        self._stop_existing_daemon(target, instance=instance)
        wrapper_code = WRAPPER_CODE
        env = accounting_env(
            self.ctx.env_for_subprocess(),
            skill_root=self.ctx.skill_root,
            repo_root=self.ctx.repo_root,
            source=f"daemon:{name}",
            force_source=True,
        )
        env.update(
            {
                "RESTART_DAEMON_NAME": name,
                "RESTART_DAEMON_HEARTBEAT_FILE": str(hb_file),
                "RESTART_DAEMON_HEARTBEAT_INTERVAL": str(self.config.heartbeat_interval),
                "RESTART_DAEMONS_HEARTBEAT_FRESH_SECONDS": str(self.config.heartbeat_fresh_seconds),
                "RESTART_DAEMONS_STOP_GRACE_SECONDS": str(self.config.stop_grace_seconds),
                "RESTART_DAEMON_SINGLETON_LOCK": "1",
                "RESTART_DAEMON_SINGLETON_LOCK_FILE": str(target.singleton_lock_file),
                "RESTART_DAEMON_FINGERPRINT_FILE": str(target.fingerprint_file),
                "RESTART_DAEMON_COMMAND_SHA256": _command_digest(target.command),
                "PYTHONPATH": f"{self.ctx.skill_root / 'scripts'}{os.pathsep}{env.get('PYTHONPATH', '')}",
            }
        )
        proc = self.runtime.launch_wrapper(
            ctx=self.ctx,
            target=target,
            wrapper_code=wrapper_code,
            env=env,
            log_file=log_file,
        )
        self._wrappers.append(proc)
        for _ in range(50):
            if _read_pid(pid_file) == proc.pid and hb_file.exists():
                break
            self.runtime.sleep(0.1)
        current_fingerprint.write(fingerprint_file)
        self._log(f"{name} restarted: wrapper_pid={proc.pid} heartbeat={hb_file}")

    def _prepare_dirs(self) -> None:
        for path in (self.ctx.paths.refactor_loop / "locks", self.ctx.paths.heartbeats, self.ctx.paths.logs):
            path.mkdir(parents=True, exist_ok=True)

    def _run_runtime_retention(self) -> None:
        try:
            result = retain_runtime(self.ctx.repo_root, enabled=runtime_retention_enabled(self.ctx))
        except Exception:
            self._log("runtime_retention warning: helper failed; continuing daemon restart")
            return
        suffix = " missing=true" if result.missing else ""
        self._log(
            "runtime_retention: "
            f"enabled={str(result.enabled).lower()} ttl_hours=24 deleted={result.deleted} kept={result.kept} "
            f"compacted_events={str(result.compacted_events).lower()} removed_worktrees={result.removed_worktrees} "
            f"pruned_worktrees={str(result.pruned_worktrees).lower()} target={result.target}{suffix}"
        )

    def _run_update_check(self) -> None:
        try:
            result = maybe_run_update_check(self.ctx, startup=True)
        except Exception as exc:
            self._log(f"update_check warning: {exc!r}")
            return
        status = result.get("status") if isinstance(result, dict) else "unknown"
        reason = result.get("reason") if isinstance(result, dict) else None
        suffix = f" reason={reason}" if reason else ""
        self._log(f"update_check: status={status}{suffix}")

    def _acquire_restart_lock(self) -> None:
        attempts = 0
        while True:
            try:
                self.lock_dir.mkdir()
                (self.lock_dir / "pid").write_text(f"{self.runtime.getpid()}\n", encoding="utf-8")
                return
            except FileExistsError:
                holder = _read_pid(self.lock_dir / "pid")
                if holder is None or not self.runtime.pid_alive(holder):
                    (self.lock_dir / "pid").unlink(missing_ok=True)
                    try:
                        self.lock_dir.rmdir()
                    except OSError:
                        pass
                    continue
                attempts += 1
                if attempts >= 30:
                    raise RuntimeError(f"restart-daemons lock held too long by pid={holder}")
                self.runtime.sleep(1)

    def _release_restart_lock(self) -> None:
        if self.lock_dir.is_dir() and _read_pid(self.lock_dir / "pid") == self.runtime.getpid():
            (self.lock_dir / "pid").unlink(missing_ok=True)
            try:
                self.lock_dir.rmdir()
            except OSError:
                pass

    def _singleton_check_fresh(
        self,
        target: DaemonTarget,
        current_fingerprint: DaemonLaunchFingerprint,
        instance: DaemonInstanceProjection,
    ) -> bool:
        pid = _read_pid(target.pid_file)
        stored_fingerprint = DaemonLaunchFingerprint.read(target.fingerprint_file)
        return (
            pid is not None
            and instance.live_wrapper_pids == (pid,)
            and self.runtime.pid_alive(pid)
            and self._heartbeat_is_fresh(target.name)
            and stored_fingerprint is not None
            and stored_fingerprint.matches(current_fingerprint)
            and instance.singleton.state == "held"
            and instance.singleton.holder_pid in set(instance.live_managed_child_pids)
        )

    def _heartbeat_is_fresh(self, name: str) -> bool:
        target = daemon_target(self.ctx, name, ())
        return heartbeat_is_fresh(target, self.config, now=self.runtime.now())

    def _stop_existing_daemon(self, target: DaemonTarget, *, instance: DaemonInstanceProjection) -> None:
        self._terminate_pids(instance.singleton_holder_pids)
        self._terminate_pids(pid for pid in instance.repair_pids if pid not in set(instance.singleton_holder_pids))
        target.pid_file.unlink(missing_ok=True)

    @staticmethod
    def _singleton_lock_blocks_restart(instance: DaemonInstanceProjection) -> bool:
        return instance.singleton.state == "held-malformed"

    def _terminate_pids(self, pids: Sequence[int]) -> None:
        for existing_pid in pids:
            if self.runtime.pid_alive(existing_pid):
                self.runtime.terminate_pid(existing_pid, self.config.stop_grace_seconds)

    def _fingerprint_path(self, name: str) -> Path:
        return self.ctx.paths.refactor_loop / "locks" / f"{name}.fingerprint.json"

    def _current_fingerprint(self, name: str, command: Sequence[str]) -> DaemonLaunchFingerprint:
        if self._package_digest_cache is None:
            self._package_digest_cache = _package_tree_digest(self.ctx)
        package_tree_sha256, fingerprinted_files_count = self._package_digest_cache
        return DaemonLaunchFingerprint.current(
            self.ctx,
            name,
            command,
            package_tree_sha256=package_tree_sha256,
            fingerprinted_files_count=fingerprinted_files_count,
        )

    @staticmethod
    def _log(message: str) -> None:
        print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {message}")


def _resolve_daemon_command_part(ctx: LoopContext, part: str) -> str:
    if part in DAEMON_COMMAND_ENV_PLACEHOLDERS:
        env_name, default = DAEMON_COMMAND_ENV_PLACEHOLDERS[part]
        return _positive_env_int(ctx, env_name, default)
    return part.replace("{skill_root}", str(ctx.skill_root)).replace("{repo_root}", str(ctx.repo_root))


def _positive_env_int(ctx: LoopContext, env_name: str, default: str) -> str:
    raw = ctx.host_env.get(env_name) or ctx.env_for_subprocess().get(env_name, default)
    try:
        parsed = int(str(raw))
    except ValueError:
        return default
    return str(parsed) if parsed > 0 else default


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


WRAPPER_CODE = "from codex_refactor_loop.restart import _run_restart_wrapper; import sys; raise SystemExit(_run_restart_wrapper(sys.argv[1:]))"


class RestartWrapperChild(Protocol):
    def poll(self) -> int | None:
        ...

    def terminate(self) -> None:
        ...

    def wait(self, timeout: float | None = None) -> int | None:
        ...

    def kill(self) -> None:
        ...


def _run_restart_wrapper(
    argv: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    popen: Callable[[Sequence[str]], RestartWrapperChild] | None = None,
    sleeper: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
    getpid: Callable[[], int] | None = None,
    max_supervision_cycles: int | None = None,
) -> int:
    if len(argv) < 5:
        raise RuntimeError("restart wrapper requires name, repo_root, pid_file, died_file, and command")
    source_env = os.environ if env is None else env
    runtime = _RestartWrapperRuntime(
        name=argv[0],
        repo_root=Path(argv[1]),
        pid_file=Path(argv[2]),
        died_file=Path(argv[3]),
        command=tuple(argv[4:]),
        heartbeat_file=Path(source_env.get("RESTART_DAEMON_HEARTBEAT_FILE", "")),
        heartbeat_fresh_seconds=_positive_int(source_env.get("RESTART_DAEMONS_HEARTBEAT_FRESH_SECONDS"), 90),
        heartbeat_interval=_positive_int(source_env.get("RESTART_DAEMON_HEARTBEAT_INTERVAL"), 30),
        stop_grace_seconds=_positive_int(source_env.get("RESTART_DAEMONS_STOP_GRACE_SECONDS"), 5),
        popen=popen or (lambda command: subprocess.Popen(list(command))),
        sleeper=sleeper or time.sleep,
        clock=clock or time.time,
        getpid=getpid or os.getpid,
    )
    return runtime.run(max_supervision_cycles=max_supervision_cycles)


class _RestartWrapperRuntime:
    def __init__(
        self,
        *,
        name: str,
        repo_root: Path,
        pid_file: Path,
        died_file: Path,
        command: tuple[str, ...],
        heartbeat_file: Path,
        heartbeat_fresh_seconds: int,
        heartbeat_interval: int,
        stop_grace_seconds: int,
        popen: Callable[[Sequence[str]], RestartWrapperChild],
        sleeper: Callable[[float], None],
        clock: Callable[[], float],
        getpid: Callable[[], int],
    ) -> None:
        self.name = name
        self.repo_root = repo_root
        self.pid_file = pid_file
        self.died_file = died_file
        self.command = command
        self.heartbeat_file = heartbeat_file
        self.heartbeat_fresh_seconds = heartbeat_fresh_seconds
        self.heartbeat_interval = heartbeat_interval
        self.stop_grace_seconds = stop_grace_seconds
        self.popen = popen
        self.sleeper = sleeper
        self.clock = clock
        self.getpid = getpid
        self.child: RestartWrapperChild | None = None
        self.child_spawned_at: float | None = None

    def run(self, *, max_supervision_cycles: int | None) -> int:
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.pid_file.write_text(f"{self.getpid()}\n", encoding="utf-8")
        previous_cwd = Path.cwd()
        os.chdir(self.repo_root)
        try:
            self._supervise(max_supervision_cycles=max_supervision_cycles)
        except KeyboardInterrupt:
            self._cleanup(130)
            return 130
        except SystemExit:
            self._cleanup(143)
            raise
        finally:
            if max_supervision_cycles is None:
                self._cleanup(0)
            os.chdir(previous_cwd)
        return 0

    def _supervise(self, *, max_supervision_cycles: int | None) -> None:
        poll_interval = max(1.0, min(float(self.heartbeat_interval), float(self.heartbeat_fresh_seconds) / 2.0))
        self._launch_child()
        cycles = 0
        while max_supervision_cycles is None or cycles < max_supervision_cycles:
            cycles += 1
            if self.child is None:
                raise RuntimeError(f"{self.name} restart wrapper child is not running")
            exit_code = self.child.poll()
            if exit_code is not None:
                self._log(f"child exited exit={exit_code}; restarting same command")
                self.sleeper(poll_interval)
                self._launch_child()
                continue
            failure = self._heartbeat_failure_reason()
            if failure is not None:
                if self._heartbeat_failure_has_generation_grace(failure):
                    self.sleeper(poll_interval)
                    continue
                self._log(f"{failure}; terminating child and restarting same command")
                self._terminate_child()
                self.sleeper(poll_interval)
                self._launch_child()
                continue
            self.sleeper(poll_interval)

    def _launch_child(self) -> None:
        self.child = self.popen(self.command)
        self.child_spawned_at = self.clock()

    def _heartbeat_failure_reason(self) -> str | None:
        status = read_heartbeat_status(
            DaemonTarget(
                name=self.name,
                command=self.command,
                pid_file=self.pid_file,
                heartbeat_file=self.heartbeat_file,
                fingerprint_file=self.pid_file.with_suffix(".fingerprint.json"),
                died_file=self.died_file,
                singleton_lock_file=self.repo_root / ".refactor-loop" / "locks" / f"{self.name}.singleton.lock",
            ),
            RestartConfig(heartbeat_fresh_seconds=self.heartbeat_fresh_seconds),
            now=int(self.clock()),
        )
        return None if status.fresh else status.reason

    def _heartbeat_failure_has_generation_grace(self, failure: str) -> bool:
        if failure != "heartbeat-missing" and not failure.startswith("heartbeat-stale:"):
            return False
        if self.child_spawned_at is None:
            return False
        child_age = self.clock() - self.child_spawned_at
        return child_age < float(self.heartbeat_fresh_seconds)

    def _terminate_child(self) -> None:
        if self.child is None or self.child.poll() is not None:
            return
        try:
            self.child.terminate()
            self.child.wait(timeout=self.stop_grace_seconds)
        except Exception as terminate_exc:
            self._log(f"child terminate failed reason={terminate_exc!r}; attempting kill")
            try:
                self.child.kill()
                self.child.wait(timeout=1)
            except Exception as kill_exc:
                self._log(f"child kill failed reason={kill_exc!r}; aborting wrapper restart")
                raise RuntimeError(f"{self.name} child termination failed after kill: {kill_exc!r}") from kill_exc

    def _cleanup(self, exit_code: int) -> None:
        self._terminate_child()
        try:
            if self.pid_file.read_text(encoding="utf-8").strip() == str(self.getpid()):
                self.pid_file.unlink()
        except OSError:
            pass
        self._log(f"wrapper exited (exit={exit_code})")

    def _log(self, reason: str) -> None:
        self.died_file.parent.mkdir(parents=True, exist_ok=True)
        with self.died_file.open("a", encoding="utf-8") as handle:
            handle.write(f"daemon {self.name} {reason} at {_utc_log_ts()}\n")


def _positive_int(raw: str | None, default: int) -> int:
    try:
        parsed = int(raw or "")
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if _reap_child_if_exited(pid):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def run_process_inventory(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True)


def _parse_process_inventory(stdout: str) -> list[DaemonProcess]:
    processes: list[DaemonProcess] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            raw_pid, raw_ppid, command = stripped.split(None, 2)
        except ValueError:
            continue
        if not raw_pid.isdigit() or not raw_ppid.isdigit():
            continue
        processes.append(DaemonProcess(int(raw_pid), command, int(raw_ppid)))
    return processes


def _normalize_process_command(command: str) -> str:
    return " ".join(command.split())


def _restart_daemon_command_matches(actual_tail: str, expected: Sequence[str]) -> bool:
    actual_parts = _normalize_process_command(actual_tail).split()
    expected_parts = [_normalize_process_command(part) for part in expected]
    if len(actual_parts) != len(expected_parts):
        return False
    for index, expected_part in enumerate(expected_parts):
        actual_part = actual_parts[index]
        if index == 0 and _is_python_launcher(actual_part) and _is_python_launcher(expected_part):
            continue
        if index == 1 and _is_consensus_cli_path(actual_part) and _is_consensus_cli_path(expected_part):
            continue
        if actual_part != expected_part:
            return False
    return True


def _is_python_launcher(value: str) -> bool:
    name = Path(value).name
    if name == "Python":
        return True
    if name in {"python", "python3"}:
        return True
    if not name.startswith("python3."):
        return False
    suffix = name.removeprefix("python3.")
    return bool(suffix) and suffix.isdigit()


def _is_consensus_cli_path(value: str) -> bool:
    return Path(value).name == CLI_ENTRYPOINT_NAME


def _read_pid(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(raw) if raw.isdigit() else None


def _read_lock_holder_pid(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if raw.isdigit():
        return int(raw)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        for key in ("actor_pid", "holder_pid", "pid"):
            value = payload.get(key)
            if isinstance(value, int) and value > 0:
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped.startswith("pid="):
            continue
        value = stripped.removeprefix("pid=").strip()
        if value.isdigit():
            return int(value)
    return None


def _command_digest(command: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for part in command:
        digest.update(str(part).encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return digest.hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return "missing"
    return digest.hexdigest()


def _package_tree_digest(ctx: LoopContext) -> tuple[str, int]:
    root = ctx.skill_root / "scripts" / "codex_refactor_loop"
    files = [ctx.skill_root / "scripts" / CLI_ENTRYPOINT_NAME]
    if root.is_dir():
        files.extend(sorted(path for path in root.rglob("*.py") if path.is_file()))
    digest = hashlib.sha256()
    count = 0
    for path in files:
        if not path.is_file():
            continue
        rel = path.relative_to(ctx.skill_root / "scripts").as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        try:
            data = path.read_bytes()
        except OSError:
            continue
        digest.update(hashlib.sha256(data).hexdigest().encode("ascii"))
        digest.update(b"\0")
        count += 1
    return digest.hexdigest(), count


def _terminate_pid(pid: int, grace: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        return
    deadline = time.time() + grace
    while time.time() < deadline:
        if _reap_child_if_exited(pid):
            return
        if not pid_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    _reap_child_if_exited(pid)


def _reap_child_if_exited(pid: int) -> bool:
    try:
        waited, _status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return False
    return waited == pid


def _utc_log_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restart restart-helper-managed daemons")
    parser.parse_args(argv)
    try:
        ctx = LoopContext.load(cwd=os.getcwd())
        return RestartDaemons(ctx).run()
    except LoopContextError as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 2
    except RuntimeError as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
