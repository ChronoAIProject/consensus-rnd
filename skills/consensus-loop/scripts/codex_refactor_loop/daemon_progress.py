"""Canonical per-tick progress artifact for restart-managed daemons."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


PROGRESS_DIR_NAME = "daemon-tick-progress"
PROGRESS_FIELD_ORDER = (
    "daemon_name",
    "status",
    "tick_id",
    "started_at",
    "updated_at",
    "completed_at",
    "pid",
    "message",
)
VALID_PROGRESS_STATUSES = frozenset({"begin", "complete", "fail"})
_DAEMON_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class DaemonTickProgress:
    daemon_name: str
    status: str
    tick_id: str
    started_at: int
    updated_at: int
    completed_at: int | None
    pid: int
    message: str = ""

    @classmethod
    def begin(
        cls,
        daemon_name: str,
        *,
        now: int | None = None,
        pid: int | None = None,
        message: str = "",
    ) -> "DaemonTickProgress":
        timestamp = _timestamp(now)
        process_id = pid if pid is not None else os.getpid()
        return cls(
            daemon_name=_validate_daemon_name(daemon_name),
            status="begin",
            tick_id=f"{timestamp}-{process_id}",
            started_at=timestamp,
            updated_at=timestamp,
            completed_at=None,
            pid=process_id,
            message=_clean_message(message),
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "DaemonTickProgress":
        daemon_name = payload.get("daemon_name")
        status = payload.get("status")
        tick_id = payload.get("tick_id")
        started_at = payload.get("started_at")
        updated_at = payload.get("updated_at")
        completed_at = payload.get("completed_at")
        pid = payload.get("pid")
        message = payload.get("message", "")
        if (
            not isinstance(daemon_name, str)
            or not _valid_daemon_name(daemon_name)
            or status not in VALID_PROGRESS_STATUSES
            or not isinstance(tick_id, str)
            or not tick_id
            or not isinstance(started_at, int)
            or started_at <= 0
            or not isinstance(updated_at, int)
            or updated_at < started_at
            or not (completed_at is None or isinstance(completed_at, int))
            or (isinstance(completed_at, int) and completed_at < started_at)
            or not isinstance(pid, int)
            or pid <= 0
            or not isinstance(message, str)
        ):
            raise ValueError("malformed daemon tick progress")
        if status == "begin" and completed_at is not None:
            raise ValueError("malformed daemon tick progress")
        if status in {"complete", "fail"} and completed_at is None:
            raise ValueError("malformed daemon tick progress")
        return cls(
            daemon_name=daemon_name,
            status=status,
            tick_id=tick_id,
            started_at=started_at,
            updated_at=updated_at,
            completed_at=completed_at,
            pid=pid,
            message=message,
        )

    def complete(self, *, now: int | None = None, message: str = "") -> "DaemonTickProgress":
        return self._terminal("complete", now=now, message=message)

    def fail(self, *, now: int | None = None, message: str = "") -> "DaemonTickProgress":
        return self._terminal("fail", now=now, message=message)

    def _terminal(self, status: str, *, now: int | None, message: str) -> "DaemonTickProgress":
        timestamp = max(self.started_at, _timestamp(now))
        return DaemonTickProgress(
            daemon_name=self.daemon_name,
            status=status,
            tick_id=self.tick_id,
            started_at=self.started_at,
            updated_at=timestamp,
            completed_at=timestamp,
            pid=self.pid,
            message=_clean_message(message),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "daemon_name": self.daemon_name,
            "status": self.status,
            "tick_id": self.tick_id,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "pid": self.pid,
            "message": self.message,
        }


@dataclass(frozen=True)
class DaemonProgressHealth:
    state: str
    age_seconds: int | None
    reason: str

    @property
    def healthy(self) -> bool:
        return self.state == "complete"


def progress_path(repo_root: Path, daemon_name: str) -> Path:
    return repo_root / ".refactor-loop" / "state" / PROGRESS_DIR_NAME / f"{_validate_daemon_name(daemon_name)}.json"


def write_progress(repo_root: Path, progress: DaemonTickProgress) -> None:
    path = progress_path(repo_root, progress.daemon_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(progress.to_json(), separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_progress(repo_root: Path, daemon_name: str) -> DaemonTickProgress | None:
    path = progress_path(repo_root, daemon_name)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        progress = DaemonTickProgress.from_mapping(payload)
    except ValueError:
        return None
    if progress.daemon_name != daemon_name:
        return None
    return progress


def begin_tick(repo_root: Path, daemon_name: str, *, now: int | None = None, pid: int | None = None) -> DaemonTickProgress:
    progress = DaemonTickProgress.begin(daemon_name, now=now, pid=pid)
    write_progress(repo_root, progress)
    return progress


def complete_tick(repo_root: Path, progress: DaemonTickProgress, *, now: int | None = None, message: str = "") -> DaemonTickProgress:
    completed = progress.complete(now=now, message=message)
    write_progress(repo_root, completed)
    return completed


def fail_tick(repo_root: Path, progress: DaemonTickProgress, *, now: int | None = None, message: str = "") -> DaemonTickProgress:
    failed = progress.fail(now=now, message=message)
    write_progress(repo_root, failed)
    return failed


def classify_progress(
    repo_root: Path,
    daemon_name: str,
    *,
    now: int | None = None,
    max_age_seconds: int,
) -> DaemonProgressHealth:
    if max_age_seconds <= 0:
        raise ValueError(f"max_age_seconds must be positive: {max_age_seconds}")
    current_time = _timestamp(now)
    path = progress_path(repo_root, daemon_name)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DaemonProgressHealth("missing", None, "progress-missing")
    except OSError as exc:
        return DaemonProgressHealth("malformed", None, f"progress-unreadable:{type(exc).__name__}")
    except json.JSONDecodeError:
        return DaemonProgressHealth("malformed", None, "progress-malformed")
    if not isinstance(payload, dict):
        return DaemonProgressHealth("malformed", None, "progress-malformed")
    try:
        progress = DaemonTickProgress.from_mapping(payload)
    except ValueError:
        return DaemonProgressHealth("malformed", None, "progress-malformed")
    if progress.daemon_name != daemon_name:
        return DaemonProgressHealth("malformed", None, "progress-daemon-mismatch")
    age = current_time - progress.updated_at
    if age < 0:
        return DaemonProgressHealth("malformed", None, "progress-future")
    if progress.status == "begin":
        if age >= max_age_seconds:
            return DaemonProgressHealth("overdue", age, f"progress-overdue:{age}s")
        return DaemonProgressHealth("in-progress", age, f"progress-in-progress:{age}s")
    if progress.status == "fail":
        return DaemonProgressHealth("failed", age, f"progress-failed:{_clean_message(progress.message) or progress.tick_id}")
    if age >= max_age_seconds:
        return DaemonProgressHealth("overdue", age, f"progress-overdue:{age}s")
    return DaemonProgressHealth("complete", age, f"progress-complete:{age}s")


def _timestamp(value: int | None) -> int:
    return int(time.time()) if value is None else int(value)


def _validate_daemon_name(value: str) -> str:
    if not _valid_daemon_name(value):
        raise ValueError(f"invalid daemon name: {value!r}")
    return value


def _valid_daemon_name(value: str) -> bool:
    return bool(_DAEMON_NAME_RE.fullmatch(value))


def _clean_message(value: str) -> str:
    return " ".join(str(value or "").split())[:240]


__all__ = [
    "DaemonProgressHealth",
    "DaemonTickProgress",
    "begin_tick",
    "classify_progress",
    "complete_tick",
    "fail_tick",
    "progress_path",
    "read_progress",
    "write_progress",
]
