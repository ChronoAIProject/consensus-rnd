"""Narrow controller tick supervisor for named handlers."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol, Sequence

from .context import LoopContext, LoopContextError
from .heartbeat import DaemonHeartbeatLease
from .monitors.comment import CommentMonitor, run_comment_monitor_reconcile_tick
from .projections import ProjectionRequest, SharedControllerProjection, check_projection_freshness, collect_shared_controller_projection
from .restart import restart_managed_daemon_names_for_context
from .workqueue import KeyOnlyWorkQueue, TickWorkItem


HANDLER_LEGACY_DAEMON_TARGETS: Mapping[str, str] = {
    "comment-monitor": "comment-monitor",
    "concurrency": "concurrency_monitor",
    "phase9-router": "phase9_router_daemon",
    "progress-reporter": "codex-progress-reporter",
    "wakeup-runner": "wakeup_runner_daemon",
}

FORBIDDEN_LIFECYCLE_AUTHORITY = (
    "generic executor",
    "pending-events authority",
    "host production SSOT",
    "GitHub/git lifecycle authority",
)
NON_ACTION_HANDLER_STATUSES = frozenset({"backoff", "blocked", "noop"})
FORBIDDEN_CONTRACT_FIELDS = frozenset(
    {
        "argv",
        "cmd",
        "command_line",
        "commands",
        "env",
        "executor",
        "gh",
        "git",
        "lifecycle_authority",
        "lifecycle_owner",
        "owner",
        "pending_events_authority",
        "shell",
    }
)


class TickHandler(Protocol):
    name: str

    def handle(self, *, item: TickWorkItem, projection: SharedControllerProjection) -> "TickHandlerResult":
        ...


@dataclass(frozen=True)
class TickHandlerResult:
    handler: str
    key: str
    status: str
    reason: str = ""


@dataclass(frozen=True)
class TickHandlerContract:
    handler: str
    required_projection_sources: tuple[str, ...]
    delegated_helper: str
    replaced_legacy_daemon_target: str
    net_deletion_target: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "TickHandlerContract":
        forbidden = sorted(FORBIDDEN_CONTRACT_FIELDS & set(payload))
        if forbidden:
            raise ValueError(f"tick handler contract cannot carry authority fields={forbidden}")
        extra = set(payload) - {
            "handler",
            "required_projection_sources",
            "delegated_helper",
            "replaced_legacy_daemon_target",
            "net_deletion_target",
        }
        if extra:
            raise ValueError(f"tick handler contract has unexpected fields={sorted(extra)}")
        required_sources = payload.get("required_projection_sources")
        if not isinstance(required_sources, (list, tuple)) or not all(isinstance(item, str) for item in required_sources):
            raise ValueError("tick handler contract requires string required_projection_sources")
        values = {
            name: payload.get(name)
            for name in (
                "handler",
                "delegated_helper",
                "replaced_legacy_daemon_target",
                "net_deletion_target",
            )
        }
        if not all(isinstance(value, str) and value for value in values.values()):
            raise ValueError("tick handler contract requires non-empty string identity fields")
        return cls(
            handler=str(values["handler"]),
            required_projection_sources=tuple(required_sources),
            delegated_helper=str(values["delegated_helper"]),
            replaced_legacy_daemon_target=str(values["replaced_legacy_daemon_target"]),
            net_deletion_target=str(values["net_deletion_target"]),
        )


COMMENT_MONITOR_HANDLER_CONTRACT = TickHandlerContract(
    handler="comment-monitor",
    required_projection_sources=("managed_work_snapshot",),
    delegated_helper="run_comment_monitor_reconcile_tick",
    replaced_legacy_daemon_target="comment-monitor",
    net_deletion_target="restart.py daemon target comment-monitor",
)


@dataclass(frozen=True)
class ControllerTickResult:
    processed: tuple[TickHandlerResult, ...]
    skipped: tuple[TickHandlerResult, ...]


@dataclass(frozen=True)
class LegacyDaemonModeGuard:
    supervisor_enabled: bool
    legacy_daemon_names: tuple[str, ...]

    @classmethod
    def from_context(cls, ctx: LoopContext, *, legacy_daemon_names: Sequence[str]) -> "LegacyDaemonModeGuard":
        return cls(
            supervisor_enabled=_truthy(ctx.host_env.get("CONTROLLER_TICK_SUPERVISOR_ENABLE")),
            legacy_daemon_names=tuple(legacy_daemon_names),
        )

    def assert_supervisor_allowed(self, handler_name: str) -> None:
        legacy_target = HANDLER_LEGACY_DAEMON_TARGETS.get(handler_name)
        if legacy_target is None:
            return
        if not self.supervisor_enabled:
            raise RuntimeError(f"legacy-daemon-mode active for handler={handler_name} target={legacy_target}")
        if legacy_target in self.legacy_daemon_names:
            raise RuntimeError(f"supervisor target conflicts with legacy daemon target={legacy_target}")


ProjectionLoader = Callable[[ProjectionRequest], SharedControllerProjection]


class ControllerTickSupervisor:
    def __init__(
        self,
        *,
        handlers: Sequence[TickHandler],
        queue: KeyOnlyWorkQueue,
        projection_loader: ProjectionLoader,
        legacy_guard: LegacyDaemonModeGuard,
        request: ProjectionRequest | None = None,
    ) -> None:
        self.handlers = {handler.name: handler for handler in handlers}
        self.queue = queue
        self.projection_loader = projection_loader
        self.legacy_guard = legacy_guard
        self.request = request or ProjectionRequest()

    def tick(self, *, limit: int | None = None) -> ControllerTickResult:
        projection = self.projection_loader(self.request)
        processed: list[TickHandlerResult] = []
        skipped: list[TickHandlerResult] = []
        drained = self.queue.drain(limit=limit)
        for item in drained:
            handler = self.handlers.get(item.handler)
            if handler is None:
                result = TickHandlerResult(item.handler, item.key, "skipped", "unknown-handler")
                skipped.append(result)
                _log_tick_skip(result, processed_count=len(processed), skipped_count=len(skipped), drained_count=len(drained))
                continue
            try:
                self.legacy_guard.assert_supervisor_allowed(handler.name)
            except RuntimeError as exc:
                result = TickHandlerResult(item.handler, item.key, "skipped", str(exc))
                skipped.append(result)
                _log_tick_skip(result, processed_count=len(processed), skipped_count=len(skipped), drained_count=len(drained))
                continue
            result = handler.handle(item=item, projection=projection)
            if result.status in NON_ACTION_HANDLER_STATUSES:
                skipped.append(result)
                _log_tick_skip(result, processed_count=len(processed), skipped_count=len(skipped), drained_count=len(drained))
                continue
            processed.append(result)
        return ControllerTickResult(tuple(processed), tuple(skipped))


class CommentMonitorTickHandler:
    name = COMMENT_MONITOR_HANDLER_CONTRACT.handler

    def __init__(self, monitor: CommentMonitor, *, contract: TickHandlerContract = COMMENT_MONITOR_HANDLER_CONTRACT) -> None:
        self.monitor = monitor
        self.contract = contract

    def handle(self, *, item: TickWorkItem, projection: SharedControllerProjection) -> TickHandlerResult:
        check = check_projection_freshness(
            projection,
            required_sources=self.contract.required_projection_sources,
        )
        if not check.ready:
            return TickHandlerResult(item.handler, item.key, check.status, check.reason)
        run_comment_monitor_reconcile_tick(self.monitor)
        return TickHandlerResult(item.handler, item.key, "handled", self.contract.delegated_helper)


def build_projection_loader(ctx: LoopContext, queue: KeyOnlyWorkQueue) -> ProjectionLoader:
    def load(request: ProjectionRequest) -> SharedControllerProjection:
        return collect_shared_controller_projection(ctx, request, workqueue_keys=queue.keys())

    return load


def build_legacy_guard(ctx: LoopContext) -> LegacyDaemonModeGuard:
    return LegacyDaemonModeGuard.from_context(ctx, legacy_daemon_names=restart_managed_daemon_names_for_context(ctx))


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _log_tick_skip(
    result: TickHandlerResult,
    *,
    processed_count: int,
    skipped_count: int,
    drained_count: int,
) -> None:
    sys.stderr.write(
        "CONTROLLER_TICK_SUPERVISOR_SKIP "
        f"handler={result.handler} "
        f"key={result.key} "
        f"status={result.status} "
        f"reason={result.reason!r} "
        f"processed={processed_count} "
        f"skipped={skipped_count} "
        f"drained={drained_count}\n"
    )


def run_empty_supervisor_daemon(ctx: LoopContext, *, interval_seconds: int = 120) -> int:
    queue = KeyOnlyWorkQueue()
    monitor = CommentMonitor(ctx)
    supervisor = ControllerTickSupervisor(
        handlers=(CommentMonitorTickHandler(monitor),),
        queue=queue,
        projection_loader=build_projection_loader(ctx, queue),
        legacy_guard=build_legacy_guard(ctx),
    )
    heartbeat = DaemonHeartbeatLease("controller_tick_supervisor", ctx.repo_root)
    while True:
        queue.enqueue("comment-monitor", "maintainer-comments")
        supervisor.tick()
        heartbeat.beat()
        heartbeat.sleep_with_lease(interval_seconds)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the internal controller tick supervisor")
    parser.add_argument("--daemon", action="store_true")
    args = parser.parse_args(argv)
    try:
        ctx = LoopContext.load(cwd=".")
    except LoopContextError as exc:
        print(f"FATAL: {exc}", flush=True)
        return 2
    if not args.daemon:
        queue = KeyOnlyWorkQueue((TickWorkItem.create(handler="comment-monitor", key="maintainer-comments"),))
        monitor = CommentMonitor(ctx)
        ControllerTickSupervisor(
            handlers=(CommentMonitorTickHandler(monitor),),
            queue=queue,
            projection_loader=build_projection_loader(ctx, queue),
            legacy_guard=build_legacy_guard(ctx),
        ).tick()
        return 0
    return run_empty_supervisor_daemon(ctx)


if __name__ == "__main__":
    raise SystemExit(main())
