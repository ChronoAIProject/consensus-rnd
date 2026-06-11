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
from .daemon_singleton import probe as probe_daemon_singleton
from .restart import (
    DaemonProcessInventory,
    DaemonTarget,
    RestartConfig,
    daemon_lock_files,
    daemon_targets,
    expected_launch_fingerprint,
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
    managed_child_pids: tuple[int, ...] = ()
    canonical_child_pids: tuple[int, ...] = ()
    orphan_child_pids: tuple[int, ...] = ()
    bounded_lock_holder_pids: tuple[int, ...] = ()
    singleton_lock_path: str = ""
    singleton_lock_state: str = ""
    singleton_lock_holder_pid: int | None = None
    singleton_lock_metadata_valid: bool = False
    singleton_lock_reason: str = ""
    process_inventory_status: str = "available"
    process_inventory_error: str = ""
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
            "managed_child_pids": list(self.managed_child_pids),
            "canonical_child_pids": list(self.canonical_child_pids),
            "orphan_child_pids": list(self.orphan_child_pids),
            "bounded_lock_holder_pids": list(self.bounded_lock_holder_pids),
            "singleton_lock_path": self.singleton_lock_path,
            "singleton_lock_state": self.singleton_lock_state,
            "singleton_lock_holder_pid": self.singleton_lock_holder_pid,
            "singleton_lock_metadata_valid": self.singleton_lock_metadata_valid,
            "singleton_lock_reason": self.singleton_lock_reason,
            "process_inventory_status": self.process_inventory_status,
            "process_inventory_error": self.process_inventory_error,
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
    instance = inventory.daemon_instance(
        name=target.name,
        repo_root=ctx.repo_root,
        pid_file=target.pid_file,
        died_file=target.died_file,
        command=target.command,
        lock_files=daemon_lock_files(ctx, target.name),
        singleton=probe_daemon_singleton(ctx.repo_root, target.name),
        is_alive=pid_alive,
    )
    duplicate_count = instance.duplicate_wrapper_count
    orphan_lock_holder_count = len(instance.orphan_lock_holder_pids)
    running = (
        pid is not None
        and instance.live_wrapper_pids == (pid,)
        and pid_alive(pid)
        and heartbeat_fresh
        and fingerprint_current
        and duplicate_count == 0
        and orphan_lock_holder_count == 0
        and instance.process_inventory_status == "available"
        and instance.singleton.state == "held"
        and instance.singleton.holder_pid in set(instance.live_managed_child_pids)
    )
    status = _daemon_status(
        active_status["active_controller"],
        pid,
        running,
        heartbeat_fresh,
        fingerprint_current,
        duplicate_count,
        orphan_lock_holder_count,
        instance.process_inventory_status,
        instance.singleton.state,
    )
    stale_reason = _stale_reason(
        status=status,
        pid=pid,
        heartbeat_reason=heartbeat.reason,
        heartbeat_fresh=heartbeat_fresh,
        fingerprint_current=fingerprint_current,
        duplicate_count=duplicate_count,
        orphan_lock_holder_count=orphan_lock_holder_count,
        process_inventory_status=instance.process_inventory_status,
        process_inventory_error=instance.process_inventory_error,
        singleton_lock_state=instance.singleton.state,
        singleton_lock_reason=instance.singleton.reason,
    )
    return DaemonStatusProjection(
        name=target.name,
        status=status,
        pid=pid,
        heartbeat_age_seconds=heartbeat_age,
        heartbeat_fresh=heartbeat_fresh,
        fingerprint_current=fingerprint_current,
        duplicate_canonical_wrappers=duplicate_count,
        managed_child_pids=instance.live_managed_child_pids,
        canonical_child_pids=instance.canonical_child_pids,
        orphan_child_pids=instance.orphan_child_pids,
        bounded_lock_holder_pids=instance.bounded_lock_holder_pids,
        singleton_lock_path=str(instance.singleton.lock_path),
        singleton_lock_state=instance.singleton.state,
        singleton_lock_holder_pid=instance.singleton.holder_pid,
        singleton_lock_metadata_valid=instance.singleton.metadata_valid,
        singleton_lock_reason=instance.singleton.reason,
        process_inventory_status=instance.process_inventory_status,
        process_inventory_error=instance.process_inventory_error,
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
    orphan_lock_holder_count: int,
    process_inventory_status: str,
    singleton_lock_state: str,
) -> str:
    if active_controller.startswith("noop:not-owner"):
        return "not-owner"
    if running:
        return "running"
    if process_inventory_status != "available":
        return "stale"
    if singleton_lock_state == "held-malformed":
        return "stale"
    if orphan_lock_holder_count:
        return "stale"
    if pid is None:
        return "dead"
    if not heartbeat_fresh or not fingerprint_current or duplicate_count or orphan_lock_holder_count:
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
    orphan_lock_holder_count: int,
    process_inventory_status: str,
    process_inventory_error: str,
    singleton_lock_state: str,
    singleton_lock_reason: str,
) -> str:
    if status == "running":
        return ""
    if status == "not-owner":
        return "not-owner"
    if process_inventory_status != "available":
        reason = process_inventory_error or "unknown"
        return f"process-inventory-unavailable:{reason}"
    if singleton_lock_state == "held-malformed":
        return singleton_lock_reason
    if orphan_lock_holder_count:
        return f"orphan-lock-holders:{orphan_lock_holder_count}"
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
