"""Restart-helper-managed daemon supervisor."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .context import LoopContext, LoopContextError
from .retention import retain_logs


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


class RestartDaemons:
    def __init__(self, ctx: LoopContext, config: RestartConfig | None = None) -> None:
        self.ctx = ctx
        self.config = config or RestartConfig()
        self.lock_dir = ctx.paths.refactor_loop / "locks" / "restart-daemons.lock"
        self._wrappers: list[subprocess.Popen[bytes]] = []

    def run(self) -> int:
        self._prepare_dirs()
        self._acquire_restart_lock()
        try:
            self._run_log_retention()
            for name, command in DAEMON_COMMANDS:
                self.start_daemon(name, command)
        finally:
            self._release_restart_lock()
        return 0

    def start_daemon(self, name: str, command_template: Sequence[str]) -> None:
        pid_file = self.ctx.paths.refactor_loop / "locks" / f"{name}.pid"
        hb_file = self.ctx.paths.heartbeats / f"{name}.ts"
        log_file = self.ctx.paths.logs / f"{name}.log"
        if self._singleton_check_fresh(name):
            self._log(f"{name} skip: alive pid={pid_file.read_text(encoding='utf-8').strip()} heartbeat=fresh")
            return
        self._stop_existing_daemon(name)
        command = [
            part.replace("{skill_root}", str(self.ctx.skill_root)).replace("{repo_root}", str(self.ctx.repo_root))
            for part in command_template
        ]
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

    def _singleton_check_fresh(self, name: str) -> bool:
        pid = _read_pid(self.ctx.paths.refactor_loop / "locks" / f"{name}.pid")
        return pid is not None and pid_alive(pid) and self._heartbeat_is_fresh(name)

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
