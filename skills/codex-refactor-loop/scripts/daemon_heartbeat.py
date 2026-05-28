#!/usr/bin/env python3
"""Actor-owned heartbeat lease helper for restart-helper-managed daemons."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


# Refactor (iter1/issue-143):
#   Old pattern: restart-daemons.sh wrapper sidecar wrote heartbeat while actor loop could hang.
#   New principle: actor-owned DaemonHeartbeatLease beats after daemon tick/caught exception and during lease sleep.
#   Keeps .refactor-loop/heartbeats/<daemon>.ts integer epoch, 90s stale consumers, statusline, and auto-release compatibility.
#   Refactor helper, no behavior change outside heartbeat ownership.
class DaemonHeartbeatLease:
    """Write and renew the daemon heartbeat from the actor process."""

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
        root = Path(repo_root or os.environ.get("REPO_ROOT", ".")).resolve()
        self.heartbeat_file = Path(
            heartbeat_file
            or os.environ.get("RESTART_DAEMON_HEARTBEAT_FILE")
            or root / ".refactor-loop" / "heartbeats" / f"{self.name}.ts"
        )
        self.heartbeat_interval = max(1, int(heartbeat_interval or os.environ.get("RESTART_DAEMON_HEARTBEAT_INTERVAL", "30")))
        self.clock = clock
        self.sleeper = sleeper

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


def beat(name: str | None = None, repo_root: Path | str | None = None) -> None:
    DaemonHeartbeatLease(name=name, repo_root=repo_root).beat()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write one actor-owned daemon heartbeat")
    parser.add_argument("--beat", action="store_true", help="write one heartbeat and exit")
    parser.add_argument("--name", help="daemon name; defaults to RESTART_DAEMON_NAME or argv[0] stem")
    parser.add_argument("--repo-root", help="host repository root; defaults to REPO_ROOT")
    parser.add_argument("--heartbeat-file", help="explicit heartbeat file path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.beat:
        raise SystemExit("--beat is required; this helper does not daemonize")
    lease = DaemonHeartbeatLease(
        name=args.name,
        repo_root=args.repo_root,
        heartbeat_file=args.heartbeat_file,
    )
    lease.beat()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
