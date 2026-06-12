"""Actor-held singleton locks for restart-managed daemon lifetimes."""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

METADATA_FIELD_ORDER = (
    "daemon_name",
    "repo_root",
    "actor_pid",
    "heartbeat_file",
    "fingerprint_file",
    "command_sha256",
    "started_at",
    "lock_generation",
)


@dataclass(frozen=True)
class DaemonSingletonMetadata:
    daemon_name: str
    repo_root: str
    actor_pid: int
    heartbeat_file: str
    fingerprint_file: str
    command_sha256: str
    started_at: int
    lock_generation: int

    @classmethod
    def create(
        cls,
        *,
        daemon_name: str,
        repo_root: Path,
        heartbeat_file: Path,
        fingerprint_file: Path,
        command_sha256: str,
        actor_pid: int | None = None,
        started_at: int | None = None,
    ) -> "DaemonSingletonMetadata":
        return cls(
            daemon_name=daemon_name,
            repo_root=str(repo_root.resolve()),
            actor_pid=actor_pid if actor_pid is not None else os.getpid(),
            heartbeat_file=str(heartbeat_file),
            fingerprint_file=str(fingerprint_file),
            command_sha256=command_sha256,
            started_at=started_at if started_at is not None else int(time.time()),
            lock_generation=1,
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "DaemonSingletonMetadata":
        daemon_name = payload.get("daemon_name")
        repo_root = payload.get("repo_root")
        actor_pid = payload.get("actor_pid")
        heartbeat_file = payload.get("heartbeat_file")
        fingerprint_file = payload.get("fingerprint_file")
        command_sha256 = payload.get("command_sha256")
        started_at = payload.get("started_at")
        lock_generation = payload.get("lock_generation")
        if (
            not isinstance(daemon_name, str)
            or not daemon_name
            or not isinstance(repo_root, str)
            or not repo_root
            or not isinstance(actor_pid, int)
            or actor_pid <= 0
            or not isinstance(heartbeat_file, str)
            or not heartbeat_file
            or not isinstance(fingerprint_file, str)
            or not isinstance(command_sha256, str)
            or not isinstance(started_at, int)
            or started_at <= 0
            or not isinstance(lock_generation, int)
            or lock_generation <= 0
        ):
            raise ValueError("malformed daemon singleton metadata")
        return cls(
            daemon_name=daemon_name,
            repo_root=repo_root,
            actor_pid=actor_pid,
            heartbeat_file=heartbeat_file,
            fingerprint_file=fingerprint_file,
            command_sha256=command_sha256,
            started_at=started_at,
            lock_generation=lock_generation,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "daemon_name": self.daemon_name,
            "repo_root": self.repo_root,
            "actor_pid": self.actor_pid,
            "heartbeat_file": self.heartbeat_file,
            "fingerprint_file": self.fingerprint_file,
            "command_sha256": self.command_sha256,
            "started_at": self.started_at,
            "lock_generation": self.lock_generation,
        }

    def canonical_json_line(self) -> str:
        return json.dumps(self.to_json(), separators=(",", ":")) + "\n"


@dataclass(frozen=True)
class DaemonSingletonProjection:
    lock_path: Path
    state: str
    holder_pid: int | None
    metadata_valid: bool
    reason: str
    metadata: DaemonSingletonMetadata | None = None

    @property
    def held(self) -> bool:
        return self.state in {"held", "held-malformed"}


class DaemonSingletonLock(AbstractContextManager["DaemonSingletonLock"]):
    def __init__(self, path: Path, metadata: DaemonSingletonMetadata) -> None:
        self.path = path
        self.metadata = metadata
        self._handle = None

    def __enter__(self) -> "DaemonSingletonLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RuntimeError(f"daemon singleton lock held: {self.path}") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(self.metadata.canonical_json_line())
        handle.flush()
        os.fsync(handle.fileno())
        self._handle = handle
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        handle = self._handle
        self._handle = None
        if handle is not None:
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            finally:
                handle.close()
        return False


def lock_path(repo_root: Path, daemon_name: str) -> Path:
    return repo_root / ".refactor-loop" / "locks" / f"{daemon_name}.singleton.lock"


def read_metadata(path: Path) -> DaemonSingletonMetadata | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return DaemonSingletonMetadata.from_mapping(payload)
    except ValueError:
        return None


def probe(repo_root: Path, daemon_name: str) -> DaemonSingletonProjection:
    path = lock_path(repo_root, daemon_name)
    if not path.exists():
        return DaemonSingletonProjection(path, "missing", None, False, "lock-missing")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                metadata = read_metadata(path)
                if metadata is None:
                    return DaemonSingletonProjection(path, "held-malformed", None, False, "lock-held-metadata-malformed")
                return DaemonSingletonProjection(path, "held", metadata.actor_pid, True, "lock-held", metadata)
            else:
                try:
                    fcntl.flock(handle, fcntl.LOCK_UN)
                finally:
                    pass
                metadata = read_metadata(path)
                return DaemonSingletonProjection(path, "free", metadata.actor_pid if metadata else None, metadata is not None, "lock-free", metadata)
    except OSError as exc:
        return DaemonSingletonProjection(path, "probe-error", None, False, f"probe-error:{type(exc).__name__}")


__all__ = [
    "DaemonSingletonLock",
    "DaemonSingletonMetadata",
    "DaemonSingletonProjection",
    "METADATA_FIELD_ORDER",
    "lock_path",
    "probe",
    "read_metadata",
]
