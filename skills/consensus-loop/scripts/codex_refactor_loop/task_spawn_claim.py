"""Local atomic claim store for codex task spawns."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .worker_markers import _marker_from_companion_artifact, read_worker_terminal_marker


SAFE_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")


class TaskSpawnClaimError(RuntimeError):
    """Raised when task-spawn claim metadata cannot be trusted."""


@dataclass(frozen=True)
class TaskSpawnClaim:
    task_id: str
    lock_path: Path
    acquired: bool


class TaskSpawnClaimMetadataError(TaskSpawnClaimError):
    def __init__(self, reason: str, lock_path: Path, detail: object = "") -> None:
        self.reason = reason
        suffix = f": {detail}" if detail else ""
        super().__init__(f"spawn claim metadata {reason}: {lock_path}{suffix}")


@dataclass(frozen=True)
class SpawnTaskLockMetadata:
    task_id: str
    log_path: Path
    pid: int
    acquired_at: str


class TaskSpawnClaimStore:
    def __init__(self, repo_root: Path) -> None:
        self.lock_dir = repo_root / ".refactor-loop" / "locks" / "spawn-tasks"

    def acquire(self, task_id: str, *, log_path: Path) -> TaskSpawnClaim:
        safe_task_id = safe_task_id_from_task(task_id)
        lock_path = self.lock_dir / f"{safe_task_id}.lock"
        log_path = log_path.resolve()
        self._ensure_lock_dir()
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                if self._existing_claim_is_recyclable(lock_path, task_id=safe_task_id, log_path=log_path):
                    self._remove_recyclable_lock(lock_path)
                    continue
                return TaskSpawnClaim(safe_task_id, lock_path, acquired=False)
            except OSError as exc:
                raise TaskSpawnClaimError(f"spawn claim lock create failed: {lock_path}: {exc}") from exc
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "task_id": safe_task_id,
                            "log_path": str(log_path),
                            "pid": os.getpid(),
                            "acquired_at": _utc_now(),
                        },
                        handle,
                        sort_keys=True,
                    )
                    handle.write("\n")
            except OSError as exc:
                cleanup_error = None
                try:
                    lock_path.unlink()
                except OSError as unlink_exc:
                    cleanup_error = unlink_exc
                if cleanup_error is not None:
                    raise TaskSpawnClaimError(
                        f"spawn claim metadata write failed and cleanup failed: {lock_path}: write={exc}; cleanup={cleanup_error}"
                    ) from exc
                raise TaskSpawnClaimError(f"spawn claim metadata write failed: {lock_path}: {exc}") from exc
            return TaskSpawnClaim(safe_task_id, lock_path, acquired=True)

    def _ensure_lock_dir(self) -> None:
        try:
            self.lock_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise TaskSpawnClaimError(f"spawn claim lock directory unavailable: {self.lock_dir}: {exc}") from exc

    def _existing_claim_is_recyclable(self, lock_path: Path, *, task_id: str, log_path: Path) -> bool:
        metadata = self._read_metadata(lock_path)
        if metadata.task_id != task_id or str(metadata.log_path) != str(log_path):
            raise TaskSpawnClaimError(f"spawn claim metadata mismatch: {lock_path}")
        if _worker_terminal_completion_marker_found(metadata.log_path):
            return True
        return not _holder_process_alive(metadata)

    def _read_metadata(self, lock_path: Path) -> SpawnTaskLockMetadata:
        return read_spawn_task_lock_metadata(lock_path)

    def _remove_recyclable_lock(self, lock_path: Path) -> None:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise TaskSpawnClaimError(f"spawn claim lock recycle failed: {lock_path}: {exc}") from exc


def safe_task_id_from_task(task_id: str) -> str:
    if not isinstance(task_id, str):
        raise TaskSpawnClaimError("spawn claim task id must be a string")
    if "/" in task_id or "\\" in task_id:
        raise TaskSpawnClaimError(f"invalid spawn claim task id: {task_id!r}")
    safe = re.sub(r"[^A-Za-z0-9_.:-]+", "-", task_id).strip("-._:")
    if not SAFE_TASK_ID_RE.fullmatch(safe):
        raise TaskSpawnClaimError(f"invalid spawn claim task id: {task_id!r}")
    return safe


def read_spawn_task_lock_metadata(lock_path: Path) -> SpawnTaskLockMetadata:
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise TaskSpawnClaimMetadataError("unreadable", lock_path, exc) from exc
    except json.JSONDecodeError as exc:
        raise TaskSpawnClaimMetadataError("malformed", lock_path, exc) from exc
    if not isinstance(payload, dict):
        raise TaskSpawnClaimMetadataError("malformed", lock_path, type(payload).__name__)
    task_id = payload.get("task_id")
    log_path = payload.get("log_path")
    pid = payload.get("pid")
    acquired_at = payload.get("acquired_at")
    if not isinstance(task_id, str) or not isinstance(log_path, str) or not log_path:
        raise TaskSpawnClaimMetadataError("malformed", lock_path)
    try:
        safe_task_id = safe_task_id_from_task(task_id)
    except TaskSpawnClaimError as exc:
        raise TaskSpawnClaimMetadataError("malformed", lock_path, exc) from exc
    if safe_task_id != task_id:
        raise TaskSpawnClaimMetadataError("malformed", lock_path, task_id)
    if lock_path.name != f"{safe_task_id}.lock":
        raise TaskSpawnClaimMetadataError("basename_mismatch", lock_path, task_id)
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise TaskSpawnClaimMetadataError("malformed", lock_path, "pid")
    if not isinstance(acquired_at, str) or not acquired_at.strip():
        raise TaskSpawnClaimMetadataError("malformed", lock_path, "acquired_at")
    return SpawnTaskLockMetadata(task_id=safe_task_id, log_path=Path(log_path), pid=pid, acquired_at=acquired_at)


def spawn_task_log_has_exit_marker(path: Path) -> bool:
    return _log_has_exit_marker(path)


def spawn_task_holder_alive(metadata: SpawnTaskLockMetadata) -> bool:
    return _holder_process_alive(metadata)


def _worker_terminal_completion_marker_found(path: Path) -> bool:
    marker_read = read_worker_terminal_marker(path)
    if marker_read.found:
        return True
    if not path.is_file():
        return _marker_from_companion_artifact(path).found
    return spawn_task_log_has_exit_marker(path)


def _holder_process_alive(metadata: SpawnTaskLockMetadata) -> bool:
    pid = metadata.pid
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except ProcessLookupError:
        return False
    return True


def _log_has_exit_marker(path: Path) -> bool:
    try:
        tail = path.read_text(encoding="utf-8", errors="replace").splitlines()[-10:]
    except FileNotFoundError:
        return False
    except OSError:
        raise TaskSpawnClaimError(f"spawn claim recycle log unreadable: {path}")
    return any(line.startswith("EXIT=") for line in tail)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
