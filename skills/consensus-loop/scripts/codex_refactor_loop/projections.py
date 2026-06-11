"""Shared read-only controller projection built from owner-local fact sources."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .context import LoopContext
from .daemon_status import DaemonStatusReport, collect as collect_daemon_status
from .managed_work_snapshot import DEFAULT_TTL_SECONDS, ManagedWorkSnapshotResult, load_open_managed_work_snapshot


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
class _FreshnessSource:
    source: str
    loaded_ok: bool
    reason: str | None
    age_seconds: float | None
    next_retry_after_seconds: float | None
    stale: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "loaded_ok": self.loaded_ok,
            "reason": self.reason,
            "age_seconds": self.age_seconds,
            "next_retry_after_seconds": self.next_retry_after_seconds,
            "stale": self.stale,
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
    freshness_sources: tuple[_FreshnessSource, ...] = ()
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
            "freshness": _freshness_json(self.generated_at, self.freshness_sources),
            "no_lifecycle_authority": self.no_lifecycle_authority,
            "not_host_production_ssot": self.not_host_production_ssot,
        }


@dataclass(frozen=True)
class ProjectionFreshnessCheck:
    ready: bool
    status: str
    reason: str


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
    generated_at = _utc_now()
    managed_result = managed_work_loader(ctx) if selected.include_managed_work else None
    daemon_report = (
        daemon_status_collector(
            selected.daemon_target,
            repo_root=ctx.repo_root,
            skill_root=ctx.skill_root,
        )
        if selected.include_daemon_status
        else None
    )
    statusline, statusline_freshness = (
        _read_statusline_snapshot(ctx.paths.statusline_snapshot)
        if selected.include_statusline
        else ({}, _disabled_freshness("statusline_snapshot"))
    )
    projected_workqueue_keys = tuple(workqueue_keys) if selected.include_workqueue_keys else ()
    return SharedControllerProjection(
        repo_root=str(ctx.repo_root),
        generated_at=generated_at,
        request=selected,
        managed_work=_managed_work_summary(managed_result) if managed_result is not None else None,
        daemon_fleet=_daemon_fleet_summary(daemon_report) if daemon_report is not None else None,
        statusline=statusline,
        workqueue_keys=projected_workqueue_keys,
        freshness_sources=(
            _managed_work_freshness(managed_result)
            if managed_result is not None
            else _disabled_freshness("managed_work_snapshot"),
            _daemon_fleet_freshness(daemon_report) if daemon_report is not None else _disabled_freshness("daemon_status"),
            statusline_freshness,
            _workqueue_freshness(selected.include_workqueue_keys, projected_workqueue_keys),
        ),
    )


def check_projection_freshness(
    projection: SharedControllerProjection,
    *,
    required_sources: tuple[str, ...],
) -> ProjectionFreshnessCheck:
    freshness_by_source = {source.source: source for source in projection.freshness_sources}
    for required_source in required_sources:
        source = freshness_by_source.get(required_source)
        if source is None:
            return ProjectionFreshnessCheck(False, "blocked", f"projection-missing:{required_source}")
        if not source.loaded_ok:
            suffix = f":{source.reason}" if source.reason else ""
            return ProjectionFreshnessCheck(False, "blocked", f"projection-failed:{required_source}{suffix}")
        if source.stale:
            suffix = f":{source.reason}" if source.reason else ""
            return ProjectionFreshnessCheck(False, "backoff", f"projection-stale:{required_source}{suffix}")
    return ProjectionFreshnessCheck(True, "ready", "projection-ready")


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


def _managed_work_freshness(snapshot: ManagedWorkSnapshotResult) -> _FreshnessSource:
    source = snapshot.source
    reason = snapshot.reason or source
    stale = source == "cache:stale"
    return _FreshnessSource(
        source="managed_work_snapshot",
        loaded_ok=snapshot.loaded_ok,
        reason=reason,
        age_seconds=snapshot.age_seconds,
        next_retry_after_seconds=_managed_work_next_retry(snapshot),
        stale=stale,
    )


def _managed_work_next_retry(snapshot: ManagedWorkSnapshotResult) -> float | None:
    if not snapshot.loaded_ok or snapshot.source == "cache:stale":
        return 0.0
    if snapshot.source == "cache:fresh" and snapshot.age_seconds is not None:
        return max(0.0, float(DEFAULT_TTL_SECONDS) - snapshot.age_seconds)
    return None


def _daemon_fleet_freshness(report: DaemonStatusReport) -> _FreshnessSource:
    stale_count = sum(1 for daemon in report.daemons if daemon.status in {"stale", "dead"})
    not_owner_count = sum(1 for daemon in report.daemons if daemon.status == "not-owner")
    reason = None
    if stale_count:
        reason = f"daemon-unhealthy:{stale_count}"
    elif not_owner_count:
        reason = f"daemon-not-owner:{not_owner_count}"
    return _FreshnessSource(
        source="daemon_status",
        loaded_ok=True,
        reason=reason,
        age_seconds=0.0,
        next_retry_after_seconds=0.0 if stale_count else None,
        stale=stale_count > 0,
    )


def _workqueue_freshness(include_workqueue_keys: bool, keys: tuple[str, ...]) -> _FreshnessSource:
    if not include_workqueue_keys:
        return _disabled_freshness("key_only_workqueue")
    return _FreshnessSource(
        source="key_only_workqueue",
        loaded_ok=True,
        reason=f"queued:{len(keys)}",
        age_seconds=None,
        next_retry_after_seconds=None,
    )


def _disabled_freshness(source: str) -> _FreshnessSource:
    return _FreshnessSource(
        source=source,
        loaded_ok=True,
        reason="disabled-by-request",
        age_seconds=None,
        next_retry_after_seconds=None,
    )


def _freshness_json(generated_at: str, sources: tuple[_FreshnessSource, ...]) -> dict[str, Any]:
    return {
        "generated_at": generated_at,
        "sources": [source.to_json() for source in sources],
        "overall_loaded_ok": all(source.loaded_ok for source in sources),
        "failed_source_count": sum(1 for source in sources if not source.loaded_ok),
        "stale_source_count": sum(1 for source in sources if source.stale),
    }


def _read_statusline_snapshot(path: Path) -> tuple[Mapping[str, Any], _FreshnessSource]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        reason = "missing-optional-statusline-snapshot"
        _log_statusline_read_issue(path, reason)
        return {}, _FreshnessSource("statusline_snapshot", True, reason, None, None)
    except OSError as exc:
        reason = f"read-error:{type(exc).__name__}"
        _log_statusline_read_issue(path, reason)
        return {}, _FreshnessSource("statusline_snapshot", False, reason, None, 0.0)
    except json.JSONDecodeError as exc:
        reason = f"json-error:line={exc.lineno}:column={exc.colno}"
        _log_statusline_read_issue(path, reason)
        return {}, _FreshnessSource("statusline_snapshot", False, reason, None, 0.0)
    if not isinstance(payload, dict):
        reason = f"not-json-object:type={type(payload).__name__}"
        _log_statusline_read_issue(path, reason)
        return {}, _FreshnessSource("statusline_snapshot", False, reason, None, 0.0)
    return payload, _FreshnessSource("statusline_snapshot", True, None, None, None)


def _log_statusline_read_issue(path: Path, reason: str) -> None:
    sys.stderr.write(f"SHARED_PROJECTION_STATUSLINE_READ_FAILED path={path} reason={reason}\n")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
