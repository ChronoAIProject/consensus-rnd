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
from .retention import retain_logs
from .update_check import maybe_run_update_check


# Refactor (issue-160/phase2-shell-runtime): Old: restart-daemons.sh owned the
# singleton wrappers. New: Python keeps the same five-daemon static allowlist,
# actor-owned heartbeat contract, and no lifecycle authority outside local
# wrapper pid/heartbeat files.
CLI_ENTRYPOINT_NAME = "consensus-rnd-cli"

DAEMON_COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("concurrency_monitor", ("python3", "{skill_root}/scripts/consensus-rnd-cli", "concurrency", "--daemon")),
    ("comment-monitor", ("python3", "{skill_root}/scripts/consensus-rnd-cli", "comment-monitor", "--daemon")),
    ("codex-progress-reporter", ("python3", "{skill_root}/scripts/consensus-rnd-cli", "progress-reporter", "--daemon")),
    ("dev_sync_daemon", ("python3", "{skill_root}/scripts/consensus-rnd-cli", "dev-sync", "--daemon")),
    ("phase9_router_daemon", ("python3", "{skill_root}/scripts/consensus-rnd-cli", "phase9-router", "--daemon")),
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


# Refactor (iter204/issue-204):
#   Old pattern: restart-daemons kill daemon 后读 stale pidfile + 90s 内 heartbeat 误判存活、跳过 respawn(实测手 kill 5 daemon 后未 respawn 造成 outage);且无代码变更重启(daemon import 缓存旧代码)。
#   New principle: 按 r2 consensus structural 锁定:引入 restart-daemons 代码指纹 artifact(检测 daemon 脚本 mtime/hash vs 启动时,变更则 force-restart)+ 值对象边界,kill 后不误判 stale-pid 存活。配套 behavior(指纹变更触发 restart、kill 后正确 respawn)+ source-regression 测试。不扩大 process authority surface。
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


# Refactor (iter204/issue-204):
#   Old pattern: restart-daemons kill daemon 后读 stale pidfile + 90s 内 heartbeat 误判存活、跳过 respawn(实测手 kill 5 daemon 后未 respawn 造成 outage);且无代码变更重启(daemon import 缓存旧代码)。
#   New principle: 按 r2 consensus structural 锁定:引入 restart-daemons 代码指纹 artifact(检测 daemon 脚本 mtime/hash vs 启动时,变更则 force-restart)+ 值对象边界,kill 后不误判 stale-pid 存活。配套 behavior(指纹变更触发 restart、kill 后正确 respawn)+ source-regression 测试。不扩大 process authority surface。
class RestartDaemons:
    def __init__(self, ctx: LoopContext, config: RestartConfig | None = None) -> None:
        self.ctx = ctx
        self.config = config or RestartConfig()
        self.lock_dir = ctx.paths.refactor_loop / "locks" / "restart-daemons.lock"
        self._wrappers: list[subprocess.Popen[bytes]] = []
        self._package_digest_cache: tuple[str, int] | None = None

    def run(self) -> int:
        self._prepare_dirs()
        # Refactor (impl/issue191-single-active-controller): Old pattern: every
        # device could restart all five controller write daemons. New principle:
        # only the active-controller owner starts or maintains those daemons;
        # non-owners leave local noop status and do not kill/start wrappers.
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
        pid_file = self.ctx.paths.refactor_loop / "locks" / f"{name}.pid"
        fingerprint_file = self._fingerprint_path(name)
        hb_file = self.ctx.paths.heartbeats / f"{name}.ts"
        log_file = self.ctx.paths.logs / f"{name}.log"
        command = [
            part.replace("{skill_root}", str(self.ctx.skill_root)).replace("{repo_root}", str(self.ctx.repo_root))
            for part in command_template
        ]
        current_fingerprint = self._current_fingerprint(name, command)
        if self._singleton_check_fresh(name, current_fingerprint):
            self._log(f"{name} skip: alive pid={pid_file.read_text(encoding='utf-8').strip()} heartbeat=fresh")
            return
        self._stop_existing_daemon(name)
        wrapper_code = WRAPPER_CODE
        env = self.ctx.env_for_subprocess()
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
                str(self.ctx.paths.logs / f"{name}.died"),
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
        # Refactor (issue231-update-check):
        #   Old pattern: restart-daemons maintained only daemon wrappers and had
        #   no startup projection for installed skill version drift.
        #   New principle: after the fixed five-daemon pass, run the opt-in
        #   notify-only probe and log warnings without blocking daemon restart.
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

    def _heartbeat_is_fresh(self, name: str) -> bool:
        hb = self.ctx.paths.heartbeats / f"{name}.ts"
        try:
            raw = hb.read_text(encoding="utf-8").strip()
            if not raw.isdigit():
                return False
            age = int(time.time()) - int(raw)
        except OSError:
            return False
        return age >= 0 and age < self.config.heartbeat_fresh_seconds

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
