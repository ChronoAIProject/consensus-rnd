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
from typing import Any, Sequence

from .active_controller import require_active_controller, write_active_controller_status
from .context import LoopContext, LoopContextError
from .gh_accounting import accounting_env
from .retention import retain_logs
from .update_check import maybe_run_update_check


CLI_ENTRYPOINT_NAME = "consensus-rnd-cli"

DAEMON_COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("concurrency_monitor", ("python3", "{skill_root}/scripts/consensus-rnd-cli", "concurrency", "--daemon")),
    ("comment-monitor", ("python3", "{skill_root}/scripts/consensus-rnd-cli", "comment-monitor", "--daemon")),
    ("codex-progress-reporter", ("python3", "{skill_root}/scripts/consensus-rnd-cli", "progress-reporter", "--daemon")),
    ("dev_sync_daemon", ("python3", "{skill_root}/scripts/consensus-rnd-cli", "dev-sync", "--daemon")),
    ("phase9_router_daemon", ("python3", "{skill_root}/scripts/consensus-rnd-cli", "phase9-router", "--daemon")),
    ("closed_label_reconciler", ("python3", "{skill_root}/scripts/consensus-rnd-cli", "closed-label-reconciler", "--daemon")),
    ("wakeup_runner_daemon", ("python3", "{skill_root}/scripts/consensus-rnd-cli", "wakeup-runner", "--daemon")),
)

def restart_managed_daemon_names() -> tuple[str, ...]:
    return tuple(name for name, _command in DAEMON_COMMANDS)


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
    """refactor helper, no behavior change outside read-only status projection."""

    name: str
    command: tuple[str, ...]
    pid_file: Path
    heartbeat_file: Path
    fingerprint_file: Path
    died_file: Path


@dataclass(frozen=True)
class DaemonLaunchFingerprint:
    """refactor helper, no behavior change outside restart skip eligibility."""

    daemon_name: str
    command: tuple[str, ...]
    entrypoint_sha256: str
    package_tree_sha256: str
    fingerprinted_files_count: int

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
        except KeyError:
            return None
        if (
            not isinstance(daemon_name, str)
            or not isinstance(command, list)
            or not all(isinstance(part, str) for part in command)
            or not isinstance(entrypoint_sha256, str)
            or not isinstance(package_tree_sha256, str)
            or not isinstance(fingerprinted_files_count, int)
        ):
            return None
        return cls(
            daemon_name=daemon_name,
            command=tuple(command),
            entrypoint_sha256=entrypoint_sha256,
            package_tree_sha256=package_tree_sha256,
            fingerprinted_files_count=fingerprinted_files_count,
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
        }


@dataclass(frozen=True)
class DaemonProcess:
    """refactor helper, no behavior change outside duplicate reconciliation."""

    pid: int
    command: str


@dataclass(frozen=True)
class DaemonProcessInventory:
    """refactor helper, no behavior change outside restart helper duplicate detection."""

    processes: tuple[DaemonProcess, ...]

    @classmethod
    def collect(cls, *, command_runner=None) -> "DaemonProcessInventory":
        runner = command_runner or run_process_inventory
        result = runner(["ps", "-eo", "pid=,command="])
        if result.returncode != 0:
            return cls(())
        return cls(tuple(_parse_process_inventory(result.stdout)))

    def live_canonical_wrappers(
        self,
        *,
        name: str,
        repo_root: Path,
        pid_file: Path,
        died_file: Path,
        command: Sequence[str],
    ) -> tuple[int, ...]:
        expected_suffix = _normalize_process_command(" ".join([name, str(repo_root), str(pid_file), str(died_file), *command]))
        pids = []
        for process in self.processes:
            if process.pid <= 0 or not pid_alive(process.pid):
                continue
            command_line = _normalize_process_command(process.command)
            if f"{sys.executable} -c " not in command_line:
                continue
            if not command_line.endswith(expected_suffix):
                continue
            pids.append(process.pid)
        return tuple(sorted(set(pids)))


def daemon_target(ctx: LoopContext, name: str, command_template: Sequence[str]) -> DaemonTarget:
    command = tuple(
        part.replace("{skill_root}", str(ctx.skill_root)).replace("{repo_root}", str(ctx.repo_root))
        for part in command_template
    )
    return DaemonTarget(
        name=name,
        command=command,
        pid_file=ctx.paths.refactor_loop / "locks" / f"{name}.pid",
        heartbeat_file=ctx.paths.heartbeats / f"{name}.ts",
        fingerprint_file=ctx.paths.refactor_loop / "locks" / f"{name}.fingerprint.json",
        died_file=ctx.paths.logs / f"{name}.died",
    )


def daemon_targets(ctx: LoopContext, target: str = "all") -> tuple[DaemonTarget, ...]:
    names = {daemon_name for daemon_name, _command_template in DAEMON_COMMANDS}
    if target != "all" and target not in names:
        raise ValueError(f"unknown daemon target: {target}")
    return tuple(
        daemon_target(ctx, daemon_name, command_template)
        for daemon_name, command_template in DAEMON_COMMANDS
        if target == "all" or daemon_name == target
    )


def read_daemon_pid(target: DaemonTarget) -> int | None:
    return _read_pid(target.pid_file)


def read_heartbeat_age_seconds(target: DaemonTarget, *, now: int | None = None) -> int | None:
    try:
        raw = target.heartbeat_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw.isdigit():
        return None
    age = (int(time.time()) if now is None else now) - int(raw)
    return age if age >= 0 else None


def read_stored_launch_fingerprint(target: DaemonTarget) -> DaemonLaunchFingerprint | None:
    return DaemonLaunchFingerprint.read(target.fingerprint_file)


def expected_launch_fingerprint(ctx: LoopContext, target: DaemonTarget) -> DaemonLaunchFingerprint:
    return DaemonLaunchFingerprint.current(ctx, target.name, target.command)


def heartbeat_is_fresh(target: DaemonTarget, config: RestartConfig, *, now: int | None = None) -> bool:
    age = read_heartbeat_age_seconds(target, now=now)
    return age is not None and age < config.heartbeat_fresh_seconds


class RestartDaemons:
    def __init__(self, ctx: LoopContext, config: RestartConfig | None = None) -> None:
        self.ctx = ctx
        self.config = config or RestartConfig()
        self.lock_dir = ctx.paths.refactor_loop / "locks" / "restart-daemons.lock"
        self._wrappers: list[subprocess.Popen[bytes]] = []
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
            self._run_log_retention()
            for name, command in DAEMON_COMMANDS:
                self.start_daemon(name, command)
        finally:
            self._release_restart_lock()
        self._run_update_check()
        return 0

    def start_daemon(self, name: str, command_template: Sequence[str]) -> None:
        target = daemon_target(self.ctx, name, command_template)
        pid_file = target.pid_file
        fingerprint_file = target.fingerprint_file
        hb_file = target.heartbeat_file
        log_file = self.ctx.paths.logs / f"{name}.log"
        died_file = target.died_file
        command = list(target.command)
        current_fingerprint = self._current_fingerprint(name, command)
        inventory = DaemonProcessInventory.collect()
        duplicates_remain = self._reconcile_duplicate_canonical_wrappers(
            name,
            command,
            current_fingerprint=current_fingerprint,
            inventory=inventory,
            pid_file=pid_file,
            died_file=died_file,
        )
        if duplicates_remain:
            self._log(f"{name} skip: duplicate canonical wrappers reconciled; waiting for next tick")
            return
        if self._singleton_check_fresh(name, current_fingerprint):
            self._log(f"{name} skip: alive pid={pid_file.read_text(encoding='utf-8').strip()} heartbeat=fresh")
            return
        self._stop_existing_daemon(name)
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
                "PYTHONPATH": f"{self.ctx.skill_root / 'scripts'}{os.pathsep}{env.get('PYTHONPATH', '')}",
            }
        )
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_file.open("ab", buffering=0)
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                wrapper_code,
                name,
                str(self.ctx.repo_root),
                str(pid_file),
                str(died_file),
                *command,
            ],
            cwd=str(self.ctx.repo_root),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        self._wrappers.append(proc)
        log_handle.close()
        for _ in range(50):
            if _read_pid(pid_file) == proc.pid and hb_file.exists():
                break
            time.sleep(0.1)
        current_fingerprint.write(fingerprint_file)
        self._log(f"{name} restarted: wrapper_pid={proc.pid} heartbeat={hb_file}")

    def _prepare_dirs(self) -> None:
        for path in (self.ctx.paths.refactor_loop / "locks", self.ctx.paths.heartbeats, self.ctx.paths.logs):
            path.mkdir(parents=True, exist_ok=True)

    def _run_log_retention(self) -> None:
        try:
            deleted, kept, target, missing = retain_logs(self.ctx.repo_root)
        except Exception:
            self._log("log_retention warning: helper failed; continuing daemon restart")
            return
        suffix = " missing=true" if missing else ""
        self._log(f"log_retention: ttl_hours=24 deleted={deleted} kept={kept} target={target}{suffix}")

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
                (self.lock_dir / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
                return
            except FileExistsError:
                holder = _read_pid(self.lock_dir / "pid")
                if holder is None or not pid_alive(holder):
                    (self.lock_dir / "pid").unlink(missing_ok=True)
                    try:
                        self.lock_dir.rmdir()
                    except OSError:
                        pass
                    continue
                attempts += 1
                if attempts >= 30:
                    raise RuntimeError(f"restart-daemons lock held too long by pid={holder}")
                time.sleep(1)

    def _release_restart_lock(self) -> None:
        if self.lock_dir.is_dir() and _read_pid(self.lock_dir / "pid") == os.getpid():
            (self.lock_dir / "pid").unlink(missing_ok=True)
            try:
                self.lock_dir.rmdir()
            except OSError:
                pass

    def _singleton_check_fresh(self, name: str, current_fingerprint: DaemonLaunchFingerprint) -> bool:
        pid = _read_pid(self.ctx.paths.refactor_loop / "locks" / f"{name}.pid")
        stored_fingerprint = DaemonLaunchFingerprint.read(self._fingerprint_path(name))
        return (
            pid is not None
            and pid_alive(pid)
            and self._heartbeat_is_fresh(name)
            and stored_fingerprint is not None
            and stored_fingerprint.matches(current_fingerprint)
        )

    def _reconcile_duplicate_canonical_wrappers(
        self,
        name: str,
        command: Sequence[str],
        *,
        current_fingerprint: DaemonLaunchFingerprint,
        inventory: DaemonProcessInventory,
        pid_file: Path,
        died_file: Path,
    ) -> bool:
        live = inventory.live_canonical_wrappers(
            name=name,
            repo_root=self.ctx.repo_root,
            pid_file=pid_file,
            died_file=died_file,
            command=command,
        )
        if len(live) <= 1:
            return False
        keeper = self._canonical_wrapper_keeper(name, live)
        pid_file.write_text(f"{keeper}\n", encoding="utf-8")
        current_fingerprint.write(self._fingerprint_path(name))
        for pid in live:
            if pid != keeper:
                _terminate_pid(pid, self.config.stop_grace_seconds)
        return True

    def _canonical_wrapper_keeper(self, name: str, live: Sequence[int]) -> int:
        pid = _read_pid(self.ctx.paths.refactor_loop / "locks" / f"{name}.pid")
        if pid in live and self._heartbeat_is_fresh(name):
            stored_fingerprint = DaemonLaunchFingerprint.read(self._fingerprint_path(name))
            if stored_fingerprint is not None:
                current_command = next((command for daemon, command in DAEMON_COMMANDS if daemon == name), ())
                resolved = tuple(
                    part.replace("{skill_root}", str(self.ctx.skill_root)).replace("{repo_root}", str(self.ctx.repo_root))
                    for part in current_command
                )
                if stored_fingerprint.matches(self._current_fingerprint(name, resolved)):
                    return pid
        return min(live)

    def _heartbeat_is_fresh(self, name: str) -> bool:
        target = daemon_target(self.ctx, name, ())
        return heartbeat_is_fresh(target, self.config)

    def _stop_existing_daemon(self, name: str) -> None:
        pid_file = self.ctx.paths.refactor_loop / "locks" / f"{name}.pid"
        pid = _read_pid(pid_file)
        if pid is not None and pid_alive(pid):
            _terminate_pid(pid, self.config.stop_grace_seconds)
        pid_file.unlink(missing_ok=True)

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


WRAPPER_CODE = r'''
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

name, repo_root, pid_file, died_file, *command = sys.argv[1:]
pid_path = Path(pid_file)
died_path = Path(died_file)
child = None

def cleanup(exit_code):
    global child
    if child is not None and child.poll() is None:
        try:
            child.terminate()
            child.wait(timeout=5)
        except Exception:
            try:
                child.kill()
            except Exception:
                pass
    try:
        if pid_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
            pid_path.unlink()
    except Exception:
        pass
    died_path.parent.mkdir(parents=True, exist_ok=True)
    with died_path.open("a", encoding="utf-8") as handle:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        handle.write(f"daemon {name} wrapper exited at {ts} (exit={exit_code})\n")

def terminate(_signum, _frame):
    cleanup(143)
    raise SystemExit(143)

signal.signal(signal.SIGTERM, terminate)
signal.signal(signal.SIGINT, terminate)
pid_path.parent.mkdir(parents=True, exist_ok=True)
pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
os.chdir(repo_root)
child = subprocess.Popen(command)
exit_code = child.wait()
cleanup(exit_code)
raise SystemExit(exit_code)
'''


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
            raw_pid, command = stripped.split(None, 1)
        except ValueError:
            continue
        if not raw_pid.isdigit():
            continue
        processes.append(DaemonProcess(int(raw_pid), command))
    return processes


def _normalize_process_command(command: str) -> str:
    return " ".join(command.split())


def _read_pid(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(raw) if raw.isdigit() else None


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
