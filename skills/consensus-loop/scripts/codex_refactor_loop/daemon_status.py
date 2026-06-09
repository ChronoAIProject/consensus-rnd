"""Read-only status projection for restart-helper-managed daemons."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .context import LoopContext, LoopContextError
from .restart import (
    DaemonProcessInventory,
    DaemonTarget,
    RestartConfig,
    daemon_targets,
    expected_launch_fingerprint,
    heartbeat_is_fresh,
    pid_alive,
    read_daemon_pid,
    read_heartbeat_age_seconds,
    read_heartbeat_status,
    read_stored_launch_fingerprint,
)


@dataclass(frozen=True)
class DaemonStatusProjection:
    name: str
    status: str
    pid: int | None
    heartbeat_age_seconds: int | None
    heartbeat_fresh: bool
    fingerprint_current: bool
    duplicate_canonical_wrappers: int
    active_controller: str
    heartbeat_status: str = ""
    stale_reason: str = ""
    current_github_login: str = ""
    identity_authority: str = "display-only"

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "pid": self.pid,
            "heartbeat_age_seconds": self.heartbeat_age_seconds,
            "heartbeat_status": self.heartbeat_status,
            "stale_reason": self.stale_reason,
            "heartbeat_fresh": self.heartbeat_fresh,
            "fingerprint_current": self.fingerprint_current,
            "duplicate_canonical_wrappers": self.duplicate_canonical_wrappers,
            "active_controller": self.active_controller,
            "current_github_login": self.current_github_login,
            "identity_authority": self.identity_authority,
        }


@dataclass(frozen=True)
class DaemonStatusReport:
    repo_root: str
    active_controller: str
    generated_at: str
    daemons: tuple[DaemonStatusProjection, ...]
    current_github_login: str = ""
    identity_authority: str = "display-only"

    def to_json(self) -> dict[str, Any]:
        return {
            "repo_root": self.repo_root,
            "active_controller": self.active_controller,
            "current_github_login": self.current_github_login,
            "identity_authority": self.identity_authority,
            "generated_at": self.generated_at,
            "daemons": [daemon.to_json() for daemon in self.daemons],
        }


def collect(target: str = "all", *, repo_root: str | Path | None = None, skill_root: str | Path | None = None) -> DaemonStatusReport:
    ctx = LoopContext.load(repo_root=repo_root, skill_root=skill_root, read_only=True, allow_git_root_fallback=True)
    config = RestartConfig()
    active_status = _cached_active_controller_status(ctx.paths.state / "active-controller-status.json")
    inventory = DaemonProcessInventory.collect()
    projections = tuple(
        _project_target(ctx, config, inventory, daemon, active_status)
        for daemon in daemon_targets(ctx, target)
    )
    return DaemonStatusReport(
        repo_root=str(ctx.repo_root),
        active_controller=active_status["active_controller"],
        current_github_login=active_status["current_github_login"],
        identity_authority=active_status["identity_authority"],
        generated_at=_utc_now(),
        daemons=projections,
    )


def _project_target(
    ctx: LoopContext,
    config: RestartConfig,
    inventory: DaemonProcessInventory,
    target: DaemonTarget,
    active_status: dict[str, str],
) -> DaemonStatusProjection:
    pid = read_daemon_pid(target)
    heartbeat = read_heartbeat_status(target, config)
    heartbeat_age = heartbeat.age_seconds
    heartbeat_fresh = heartbeat.fresh
    stored = read_stored_launch_fingerprint(target)
    expected = expected_launch_fingerprint(ctx, target)
    fingerprint_current = stored is not None and stored.matches(expected)
    live_wrappers = inventory.live_restart_wrappers(
        name=target.name,
        repo_root=ctx.repo_root,
        pid_file=target.pid_file,
        died_file=target.died_file,
        command=target.command,
    )
    duplicate_count = max(0, len(live_wrappers) - 1)
    running = pid is not None and pid_alive(pid) and heartbeat_fresh and fingerprint_current and duplicate_count == 0
    status = _daemon_status(active_status["active_controller"], pid, running, heartbeat_fresh, fingerprint_current, duplicate_count)
    stale_reason = _stale_reason(
        status=status,
        pid=pid,
        heartbeat_reason=heartbeat.reason,
        heartbeat_fresh=heartbeat_fresh,
        fingerprint_current=fingerprint_current,
        duplicate_count=duplicate_count,
    )
    return DaemonStatusProjection(
        name=target.name,
        status=status,
        pid=pid,
        heartbeat_age_seconds=heartbeat_age,
        heartbeat_fresh=heartbeat_fresh,
        fingerprint_current=fingerprint_current,
        duplicate_canonical_wrappers=duplicate_count,
        active_controller=active_status["active_controller"],
        heartbeat_status=heartbeat.state,
        stale_reason=stale_reason,
        current_github_login=active_status["current_github_login"],
        identity_authority=active_status["identity_authority"],
    )


def _daemon_status(
    active_controller: str,
    pid: int | None,
    running: bool,
    heartbeat_fresh: bool,
    fingerprint_current: bool,
    duplicate_count: int,
) -> str:
    if active_controller.startswith("noop:not-owner"):
        return "not-owner"
    if running:
        return "running"
    if pid is None:
        return "dead"
    if not heartbeat_fresh or not fingerprint_current or duplicate_count:
        return "stale"
    if not pid_alive(pid):
        return "dead"
    return "stale"


def _stale_reason(
    *,
    status: str,
    pid: int | None,
    heartbeat_reason: str,
    heartbeat_fresh: bool,
    fingerprint_current: bool,
    duplicate_count: int,
) -> str:
    if status == "running":
        return ""
    if status == "not-owner":
        return "not-owner"
    if pid is None:
        return "pid-missing"
    if not pid_alive(pid):
        return "pid-dead"
    if not heartbeat_fresh:
        return heartbeat_reason
    if not fingerprint_current:
        return "fingerprint-mismatch"
    if duplicate_count:
        return f"duplicate-wrappers:{duplicate_count}"
    return "stale"


def _cached_active_controller_status(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    active_controller = payload.get("active_controller")
    current_login = payload.get("current_github_login")
    identity_authority = payload.get("identity_authority")
    return {
        "active_controller": active_controller if isinstance(active_controller, str) and active_controller else "unknown",
        "current_github_login": current_login if isinstance(current_login, str) else "",
        "identity_authority": identity_authority if isinstance(identity_authority, str) and identity_authority else "display-only",
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read restart-helper-managed daemon status")
    parser.add_argument("target", nargs="?", default="all", help="daemon name or all")
    parser.add_argument("--json", action="store_true", help="print JSON status")
    args = parser.parse_args(argv)
    try:
        report = collect(args.target)
    except ValueError as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 2
    except LoopContextError as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 2
    if args.json:
        print(json.dumps(report.to_json(), sort_keys=True))
    else:
        for daemon in report.daemons:
            pid = daemon.pid if daemon.pid is not None else "-"
            age = daemon.heartbeat_age_seconds if daemon.heartbeat_age_seconds is not None else "-"
            print(f"{daemon.name}\t{daemon.status}\tpid={pid}\theartbeat_age={age}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
