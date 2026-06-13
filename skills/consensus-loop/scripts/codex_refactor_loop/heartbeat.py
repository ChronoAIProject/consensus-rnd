"""Actor-owned heartbeat lease helper for restart-helper-managed daemons."""

from __future__ import annotations

import os
import hashlib
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, TypeVar

from .context import LoopContext
from .daemon_singleton import DaemonSingletonLock, DaemonSingletonMetadata
from .daemon_progress import begin_tick, complete_tick, fail_tick

T = TypeVar("T")

class DaemonHeartbeatLease:
    """Write and renew the daemon heartbeat from the actor process.

    """

    def __init__(
        self,
        name: str | None = None,
        repo_root: Path | str | None = None,
        *,
        heartbeat_file: Path | str | None = None,
        heartbeat_interval: int | None = None,
        singleton: bool | None = None,
        singleton_lock_file: Path | str | None = None,
        command_sha256: str | None = None,
        clock=time.time,
        sleeper=time.sleep,
    ) -> None:
        self.name = name or os.environ.get("RESTART_DAEMON_NAME") or Path(sys.argv[0]).stem
        root = self._resolve_repo_root(repo_root, heartbeat_file)
        self.repo_root = root
        default_heartbeat_file = root / ".refactor-loop" / "heartbeats" / f"{self.name}.ts" if root is not None else None
        env_heartbeat_file = None if repo_root is not None else os.environ.get("RESTART_DAEMON_HEARTBEAT_FILE")
        self.heartbeat_file = Path(
            heartbeat_file
            or env_heartbeat_file
            or default_heartbeat_file
        )
        self.heartbeat_interval = max(1, int(heartbeat_interval or os.environ.get("RESTART_DAEMON_HEARTBEAT_INTERVAL", "30")))
        self.singleton_enabled = (
            _truthy(os.environ.get("RESTART_DAEMON_SINGLETON_LOCK"))
            if singleton is None and repo_root is None and heartbeat_file is None
            else bool(singleton)
        )
        env_singleton_file = None if repo_root is not None else os.environ.get("RESTART_DAEMON_SINGLETON_LOCK_FILE")
        default_singleton_file = root / ".refactor-loop" / "locks" / f"{self.name}.singleton.lock" if root is not None else None
        self.singleton_lock_file = Path(singleton_lock_file or env_singleton_file or default_singleton_file) if self.singleton_enabled else None
        self.fingerprint_file = (
            root / ".refactor-loop" / "locks" / f"{self.name}.fingerprint.json"
            if root is not None
            else Path(os.environ.get("RESTART_DAEMON_FINGERPRINT_FILE", ""))
        )
        self.command_sha256 = command_sha256 or os.environ.get("RESTART_DAEMON_COMMAND_SHA256") or _command_digest(sys.argv)
        self.clock = clock
        self.sleeper = sleeper
        self._singleton_lock: DaemonSingletonLock | None = None

    @staticmethod
    def _resolve_repo_root(repo_root: Path | str | None, heartbeat_file: Path | str | None) -> Path | None:
        if repo_root is not None:
            return Path(repo_root).resolve()
        if heartbeat_file is not None or os.environ.get("RESTART_DAEMON_HEARTBEAT_FILE"):
            return None
        return LoopContext.load(env=os.environ, cwd=Path.cwd(), read_only=True).repo_root

    def beat(self) -> None:
        """Atomically write the current integer epoch heartbeat."""
        self._require_singleton_if_enabled()
        self.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.heartbeat_file.with_name(f".{self.heartbeat_file.name}.tmp.{os.getpid()}.{threading.get_ident()}")
        tmp.write_text(f"{int(self.clock())}\n", encoding="utf-8")
        os.replace(tmp, self.heartbeat_file)

    def sleep_with_lease(self, seconds: int | float) -> None:
        """Sleep in bounded chunks and renew the actor-owned lease between chunks."""
        remaining = max(0.0, float(seconds))
        self._require_singleton_if_enabled()
        while remaining > 0:
            chunk = min(remaining, float(self.heartbeat_interval))
            self.sleeper(chunk)
            self.beat()
            remaining -= chunk

    @contextmanager
    def daemon_lifetime(self) -> Iterator["DaemonHeartbeatLease"]:
        """Hold the daemon singleton lock for the actor lifetime."""
        if not self.singleton_enabled:
            yield self
            return
        if self.repo_root is None:
            raise RuntimeError("daemon singleton lock requires repo_root")
        if self.singleton_lock_file is None:
            raise RuntimeError("daemon singleton lock file is unresolved")
        metadata = DaemonSingletonMetadata.create(
            daemon_name=self.name,
            repo_root=self.repo_root,
            heartbeat_file=self.heartbeat_file,
            fingerprint_file=self.fingerprint_file,
            command_sha256=self.command_sha256,
            started_at=int(self.clock()),
        )
        lock = DaemonSingletonLock(self.singleton_lock_file, metadata)
        with lock:
            self._singleton_lock = lock
            try:
                yield self
            finally:
                self._singleton_lock = None

    def _require_singleton_if_enabled(self) -> None:
        if self.singleton_enabled and self._singleton_lock is None:
            raise RuntimeError(f"{self.name} heartbeat requires held daemon singleton lock")

    def run_tick(self, callback: Callable[[], T]) -> T:
        """Run one daemon tick with progress boundaries and a post-success heartbeat."""
        if self.repo_root is None:
            raise RuntimeError("daemon tick progress requires repo_root")
        progress = begin_tick(self.repo_root, self.name)
        try:
            result = callback()
        except Exception as exc:
            fail_tick(self.repo_root, progress, message=f"{type(exc).__name__}:{exc}")
            raise
        complete_tick(self.repo_root, progress)
        self.beat()
        return result


def beat(name: str | None = None, repo_root: Path | str | None = None) -> None:
    DaemonHeartbeatLease(name=name, repo_root=repo_root).beat()


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _command_digest(parts: list[str]) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return digest.hexdigest()


__all__ = ["DaemonHeartbeatLease", "beat"]
