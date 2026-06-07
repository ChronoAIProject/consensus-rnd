"""Actor-owned heartbeat lease helper for restart-helper-managed daemons."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable, TypeVar

from .context import LoopContext

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
        clock=time.time,
        sleeper=time.sleep,
    ) -> None:
        self.name = name or os.environ.get("RESTART_DAEMON_NAME") or Path(sys.argv[0]).stem
        root = self._resolve_repo_root(repo_root, heartbeat_file)
        default_heartbeat_file = root / ".refactor-loop" / "heartbeats" / f"{self.name}.ts" if root is not None else None
        env_heartbeat_file = None if repo_root is not None else os.environ.get("RESTART_DAEMON_HEARTBEAT_FILE")
        self.heartbeat_file = Path(
            heartbeat_file
            or env_heartbeat_file
            or default_heartbeat_file
        )
        self.heartbeat_interval = max(1, int(heartbeat_interval or os.environ.get("RESTART_DAEMON_HEARTBEAT_INTERVAL", "30")))
        self.clock = clock
        self.sleeper = sleeper

    @staticmethod
    def _resolve_repo_root(repo_root: Path | str | None, heartbeat_file: Path | str | None) -> Path | None:
        if repo_root is not None:
            return Path(repo_root).resolve()
        if heartbeat_file is not None or os.environ.get("RESTART_DAEMON_HEARTBEAT_FILE"):
            return None
        return LoopContext.load(env=os.environ, cwd=Path.cwd(), read_only=True).repo_root

    def beat(self) -> None:
        """Atomically write the current integer epoch heartbeat."""
        self.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.heartbeat_file.with_name(f".{self.heartbeat_file.name}.tmp.{os.getpid()}")
        tmp.write_text(f"{int(self.clock())}\n", encoding="utf-8")
        os.replace(tmp, self.heartbeat_file)

    def sleep_with_lease(self, seconds: int | float) -> None:
        """Sleep in bounded chunks and renew the actor-owned lease between chunks."""
        remaining = max(0.0, float(seconds))
        while remaining > 0:
            chunk = min(remaining, float(self.heartbeat_interval))
            self.sleeper(chunk)
            self.beat()
            remaining -= chunk

    def run_with_lease(self, callback: Callable[[], T]) -> T:
        """Renew the heartbeat periodically while callback is still running."""
        stop = threading.Event()

        def renew_lease() -> None:
            while not stop.wait(max(1.0, float(self.heartbeat_interval))):
                self.beat()

        renewer = threading.Thread(
            target=renew_lease,
            name=f"{self.name}-heartbeat-renewer",
            daemon=True,
        )
        renewer.start()
        try:
            return callback()
        finally:
            stop.set()
            renewer.join(timeout=1.0)


def beat(name: str | None = None, repo_root: Path | str | None = None) -> None:
    DaemonHeartbeatLease(name=name, repo_root=repo_root).beat()


__all__ = ["DaemonHeartbeatLease", "beat"]
