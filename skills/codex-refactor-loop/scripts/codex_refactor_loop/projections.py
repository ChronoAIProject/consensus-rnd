"""Shared read-only controller projection built from owner-local fact sources."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .context import LoopContext
from .daemon_status import DaemonStatusReport, collect as collect_daemon_status
from .managed_work_snapshot import ManagedWorkSnapshotResult, load_open_managed_work_snapshot


@dataclass(frozen=True)
class ProjectionRequest:
    include_managed_work: bool = True
    include_daemon_status: bool = True
    include_statusline: bool = True
    include_workqueue_keys: bool = True
    daemon_target: str = "all"


@dataclass(frozen=True)
class ManagedWorkSummary:
    loaded_ok: bool
    source: str
    reason: str | None
    age_seconds: float | None
    open_issue_count: int
    open_pr_count: int

    def to_json(self) -> dict[str, Any]:
        return {
            "loaded_ok": self.loaded_ok,
            "source": self.source,
            "reason": self.reason,
            "age_seconds": self.age_seconds,
            "open_issue_count": self.open_issue_count,
            "open_pr_count": self.open_pr_count,
        }


@dataclass(frozen=True)
class DaemonFleetSummary:
    active_controller: str
    generated_at: str
    total: int
    running: int
    stale: int
    dead: int
    not_owner: int

    def to_json(self) -> dict[str, Any]:
        return {
            "active_controller": self.active_controller,
            "generated_at": self.generated_at,
            "total": self.total,
            "running": self.running,
            "stale": self.stale,
            "dead": self.dead,
            "not_owner": self.not_owner,
        }


@dataclass(frozen=True)
class SharedControllerProjection:
    repo_root: str
    generated_at: str
    request: ProjectionRequest
    managed_work: ManagedWorkSummary | None
    daemon_fleet: DaemonFleetSummary | None
    statusline: Mapping[str, Any]
    workqueue_keys: tuple[str, ...]
    no_lifecycle_authority: bool = True
    not_host_production_ssot: bool = True

    def to_json(self) -> dict[str, Any]:
        return {
            "repo_root": self.repo_root,
            "generated_at": self.generated_at,
            "request": {
                "include_managed_work": self.request.include_managed_work,
                "include_daemon_status": self.request.include_daemon_status,
                "include_statusline": self.request.include_statusline,
                "include_workqueue_keys": self.request.include_workqueue_keys,
                "daemon_target": self.request.daemon_target,
            },
            "managed_work": self.managed_work.to_json() if self.managed_work is not None else None,
            "daemon_fleet": self.daemon_fleet.to_json() if self.daemon_fleet is not None else None,
            "statusline": dict(self.statusline),
            "workqueue_keys": list(self.workqueue_keys),
            "no_lifecycle_authority": self.no_lifecycle_authority,
            "not_host_production_ssot": self.not_host_production_ssot,
        }


ManagedWorkLoader = Callable[[LoopContext], ManagedWorkSnapshotResult]
DaemonStatusCollector = Callable[..., DaemonStatusReport]


def collect_shared_controller_projection(
    ctx: LoopContext,
    request: ProjectionRequest | None = None,
    *,
    managed_work_loader: ManagedWorkLoader = load_open_managed_work_snapshot,
    daemon_status_collector: DaemonStatusCollector = collect_daemon_status,
    workqueue_keys: tuple[str, ...] = (),
) -> SharedControllerProjection:
    selected = request or ProjectionRequest()
    return SharedControllerProjection(
        repo_root=str(ctx.repo_root),
        generated_at=_utc_now(),
        request=selected,
        managed_work=_managed_work_summary(managed_work_loader(ctx)) if selected.include_managed_work else None,
        daemon_fleet=_daemon_fleet_summary(
            daemon_status_collector(
                selected.daemon_target,
                repo_root=ctx.repo_root,
                skill_root=ctx.skill_root,
            )
        )
        if selected.include_daemon_status
        else None,
        statusline=_read_json_object(ctx.paths.statusline_snapshot) if selected.include_statusline else {},
        workqueue_keys=tuple(workqueue_keys) if selected.include_workqueue_keys else (),
    )


def _managed_work_summary(snapshot: ManagedWorkSnapshotResult) -> ManagedWorkSummary:
    open_issue_count = sum(1 for item in snapshot.items if item.kind == "issue")
    open_pr_count = sum(1 for item in snapshot.items if item.kind == "PR")
    return ManagedWorkSummary(
        loaded_ok=snapshot.loaded_ok,
        source=snapshot.source,
        reason=snapshot.reason,
        age_seconds=snapshot.age_seconds,
        open_issue_count=open_issue_count,
        open_pr_count=open_pr_count,
    )


def _daemon_fleet_summary(report: DaemonStatusReport) -> DaemonFleetSummary:
    return DaemonFleetSummary(
        active_controller=report.active_controller,
        generated_at=report.generated_at,
        total=len(report.daemons),
        running=sum(1 for daemon in report.daemons if daemon.status == "running"),
        stale=sum(1 for daemon in report.daemons if daemon.status == "stale"),
        dead=sum(1 for daemon in report.daemons if daemon.status == "dead"),
        not_owner=sum(1 for daemon in report.daemons if daemon.status == "not-owner"),
    )


def _read_json_object(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
