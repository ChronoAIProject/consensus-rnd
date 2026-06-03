"""Process supervision primitives for codex-refactor-loop spawns."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence


STALL_EXIT_CODE = 137


@dataclass(frozen=True)
class ProcessSupervisor:
    """Supervise a command by log liveness, not wall-clock duration."""

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
    ) -> int:
        if not stdin.is_file():
            raise ValueError(f"prompt file not found: {stdin}")
        if stall <= 0:
            raise ValueError(f"stall must be positive: {stall}")
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
                )
            except OSError as exc:
                _append(log, f"SPAWN_FAILED={exc}\nEXIT=127\nDONE_AT={_utc_now()}\n")
                return 127
            last_size = _log_size(log)
            last_output_at = self.clock()
            stalled = False
            try:
                while proc.poll() is None:
                    self.sleeper(self.poll_interval)
                    size = _log_size(log)
                    if size != last_size:
                        last_size = size
                        last_output_at = self.clock()
                        continue
                    if self.clock() - last_output_at >= stall:
                        _append(log, f"STALL_KILL_AFTER={stall}s\nSTALL_KILL_AT={_utc_now()}\n")
                        kill_process_group(proc.pid)
                        stalled = True
                        break
                exit_code = proc.wait()
            finally:
                if proc.poll() is None:
                    kill_process_group(proc.pid)
                    proc.wait()
            if stalled:
                exit_code = STALL_EXIT_CODE
        _append(log, f"EXIT={exit_code}\nDONE_AT={_utc_now()}\n")
        return exit_code


def launch_spawn_codex_supervisor(
    *,
    repo_root: Path,
    cd: Path,
    prompt: Path,
    log: Path,
    stall: int,
    add_dirs: Sequence[Path] = (),
    stdout_to_log: bool = False,
) -> int:
    """Launch the blocking spawn-codex supervisor outside the daemon process."""
    if not prompt.is_file():
        return 2
    if stall <= 0:
        raise ValueError(f"stall must be positive: {stall}")
    repo_root = repo_root.resolve()
    command = [
        str(repo_root / "skills" / "codex-refactor-loop" / "scripts" / "consensus-rnd-cli"),
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


def _log_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def _rotate_unfinished_log(path: Path) -> None:
    rotated = path.with_name(f"{path.name}.unfinished.{os.getpid()}")
    path.replace(rotated)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
