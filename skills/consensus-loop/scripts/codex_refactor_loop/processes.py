"""Process supervision primitives for consensus-loop spawns."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence


STALL_EXIT_CODE = 137
TIMEOUT_EXIT_CODE = STALL_EXIT_CODE


@dataclass(frozen=True)
class ProcessSupervisor:
    """Supervise a command by process exit or total wall-clock timeout.

    The `stall` argument is a compatibility name for the total wall-clock
    runtime limit in seconds. It is not a log-idle timeout.
    """

    poll_interval: float = 1.0
    clock: callable = time.time
    sleeper: callable = time.sleep

    def supervise(
        self,
        command: Sequence[str],
        *,
        stdin: Path,
        log: Path,
        stall: int,
        preamble: str = "",
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> int:
        if not stdin.is_file():
            raise ValueError(f"prompt file not found: {stdin}")
        if stall <= 0:
            raise ValueError(f"total wall-clock timeout must be positive: {stall}")
        log.parent.mkdir(parents=True, exist_ok=True)
        if log.exists() and not _has_exit_marker(log):
            _rotate_unfinished_log(log)
        log.write_text(preamble, encoding="utf-8")

        with stdin.open("rb") as in_handle, log.open("ab", buffering=0) as log_handle:
            try:
                proc = subprocess.Popen(
                    list(command),
                    stdin=in_handle,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=dict(env) if env is not None else None,
                    cwd=str(cwd) if cwd is not None else None,
                )
            except OSError as exc:
                _append(log, f"SPAWN_FAILED={exc}\nEXIT=127\nDONE_AT={_utc_now()}\n")
                return 127
            start = self.clock()
            timed_out = False
            try:
                while proc.poll() is None:
                    self.sleeper(self.poll_interval)
                    if proc.poll() is not None:
                        break
                    if self.clock() - start >= stall:
                        _append(log, f"TIMEOUT_KILL_AFTER={stall}s\nTIMEOUT_KILL_AT={_utc_now()}\n")
                        kill_process_group(proc.pid)
                        timed_out = True
                        break
                exit_code = proc.wait()
            finally:
                if proc.poll() is None:
                    kill_process_group(proc.pid)
                    proc.wait()
            if timed_out:
                exit_code = TIMEOUT_EXIT_CODE
        _append(log, f"EXIT={exit_code}\nDONE_AT={_utc_now()}\n")
        return exit_code


def launch_spawn_codex_supervisor(
    *,
    repo_root: Path,
    skill_root: Path,
    cd: Path,
    prompt: Path,
    log: Path,
    stall: int,
    add_dirs: Sequence[Path] = (),
    env: Mapping[str, str] | None = None,
    stdout_to_log: bool = False,
) -> int:
    """Launch the blocking spawn-codex supervisor outside the daemon process."""
    if not prompt.is_file():
        return 2
    if stall <= 0:
        raise ValueError(f"total wall-clock timeout must be positive: {stall}")
    repo_root = repo_root.resolve()
    skill_root = skill_root.resolve()
    cli = skill_root / "scripts" / "consensus-rnd-cli"
    if not cli.is_file():
        diagnostic = f"SPAWN_SUPERVISOR_CLI_MISSING:{cli}\n"
        log.parent.mkdir(parents=True, exist_ok=True)
        _append(log, diagnostic)
        sys.stderr.write(diagnostic)
        return 127
    command = [
        str(cli),
        "spawn-codex",
        "--cd",
        str(cd),
    ]
    for directory in add_dirs:
        command.extend(["--add-dir", str(directory)])
    command.extend(
        [
            "--prompt",
            str(prompt),
            "--log",
            str(log),
            "--stall",
            str(stall),
        ]
    )
    log.parent.mkdir(parents=True, exist_ok=True)
    if stdout_to_log:
        handle = log.open("ab", buffering=0)
        try:
            subprocess.Popen(
                command,
                cwd=str(repo_root),
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=dict(env) if env is not None else None,
            )
        finally:
            handle.close()
    else:
        subprocess.Popen(
            command,
            cwd=str(repo_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=dict(env) if env is not None else None,
        )
    return 0


def prompt_file_from_text(text: str) -> Path:
    fd, name = tempfile.mkstemp(prefix="codex-prompt.", dir="/tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.write("\n")
    return Path(name)


def kill_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        return


def _has_exit_marker(path: Path) -> bool:
    try:
        tail = path.read_text(encoding="utf-8", errors="replace").splitlines()[-5:]
    except OSError:
        return False
    return any(line.startswith("EXIT=") for line in tail)


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def _rotate_unfinished_log(path: Path) -> None:
    rotated = path.with_name(f"{path.name}.unfinished.{os.getpid()}")
    path.replace(rotated)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
