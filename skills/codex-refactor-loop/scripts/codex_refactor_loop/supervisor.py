"""Narrow controller tick supervisor for named handlers."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol, Sequence

from .context import LoopContext, LoopContextError
from .heartbeat import DaemonHeartbeatLease
from .projections import ProjectionRequest, SharedControllerProjection, collect_shared_controller_projection
from .restart import restart_managed_daemon_names
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
        for item in self.queue.drain(limit=limit):
            handler = self.handlers.get(item.handler)
            if handler is None:
                skipped.append(TickHandlerResult(item.handler, item.key, "skipped", "unknown-handler"))
                continue
            try:
                self.legacy_guard.assert_supervisor_allowed(handler.name)
            except RuntimeError as exc:
                skipped.append(TickHandlerResult(item.handler, item.key, "skipped", str(exc)))
                continue
            processed.append(handler.handle(item=item, projection=projection))
        return ControllerTickResult(tuple(processed), tuple(skipped))


def build_projection_loader(ctx: LoopContext, queue: KeyOnlyWorkQueue) -> ProjectionLoader:
    def load(request: ProjectionRequest) -> SharedControllerProjection:
        return collect_shared_controller_projection(ctx, request, workqueue_keys=queue.keys())

    return load


def build_legacy_guard(ctx: LoopContext) -> LegacyDaemonModeGuard:
    return LegacyDaemonModeGuard.from_context(ctx, legacy_daemon_names=restart_managed_daemon_names())


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def run_empty_supervisor_daemon(ctx: LoopContext, *, interval_seconds: int = 120) -> int:
    queue = KeyOnlyWorkQueue()
    supervisor = ControllerTickSupervisor(
        handlers=(),
        queue=queue,
        projection_loader=build_projection_loader(ctx, queue),
        legacy_guard=build_legacy_guard(ctx),
    )
    heartbeat = DaemonHeartbeatLease("controller_tick_supervisor", ctx.repo_root)
    while True:
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
        queue = KeyOnlyWorkQueue()
        ControllerTickSupervisor(
            handlers=(),
            queue=queue,
            projection_loader=build_projection_loader(ctx, queue),
            legacy_guard=build_legacy_guard(ctx),
        ).tick()
        return 0
    return run_empty_supervisor_daemon(ctx)


if __name__ == "__main__":
    raise SystemExit(main())
