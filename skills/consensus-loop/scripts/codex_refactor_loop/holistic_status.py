"""Shared read-only holistic status projection."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import labels as label_catalog
from .context import LoopContext, LoopContextError
from .daemon_status import collect as collect_daemon_status
from .work_items import ManagedItem, ManagedWorkProjection, extract_closing_issue_numbers
from .wakeup_plan import GhItem, load_github_items


REASON_IN_FLIGHT = "in-flight"
REASON_DISPATCHABLE = "dispatchable-awaiting-worker"
REASON_REPRESENTED_BY_PR = "represented-by-open-pr"
REASON_MAINTAINER_DECISION = "maintainer-decision"
REASON_BLOCKED = "blocked-phase"
REASON_PR_OPEN = "pr-open-awaiting-review-or-ci"
REASON_CI_RUNNING = "ci-running"
REASON_NO_WORKER_CAPACITY = "no-worker-capacity"
REASON_TERMINAL = "terminal-open-state"
REASON_NO_PHASE = "no-phase-label"


@dataclass(frozen=True)
class HolisticDependency:
    source: str
    target: str
    reason: str


@dataclass(frozen=True)
class HolisticWorkItem:
    kind: str
    number: int
    title: str
    phase: str
    human: str
    reason: str
    expected_workers: int
    dependencies: tuple[str, ...]

    @property
    def ref(self) -> str:
        prefix = "PR" if self.kind == "pr" else "issue"
        return f"{prefix} #{self.number}"


@dataclass(frozen=True)
class HolisticThroughput:
    actual_workers: int
    floor: int
    expected_workers: int
    target_workers: int
    deficit: int
    queued_dispatches: int


@dataclass(frozen=True)
class HolisticStatusProjection:
    generated_at: str
    throughput: HolisticThroughput
    open_items: tuple[HolisticWorkItem, ...]
    dependencies: tuple[HolisticDependency, ...]
    daemon_summary: tuple[str, ...]
    patrol_summary: tuple[str, ...]
    statusline: Mapping[str, Any]
    recent_merges: int


def collect(
    ctx: LoopContext,
    *,
    monitor: Any | None = None,
    github_items: Iterable[GhItem | ManagedItem | Mapping[str, object]] | None = None,
) -> HolisticStatusProjection:
    raw_items = tuple(_load_items(ctx, github_items))
    managed = ManagedWorkProjection(raw_items)
    throughput = _throughput(ctx, managed, monitor)
    dependencies = _dependencies(managed)
    open_items = tuple(_project_item(item, managed, throughput) for item in managed.items if item.state == "open")
    return HolisticStatusProjection(
        generated_at=_utc_now(),
        throughput=throughput,
        open_items=open_items,
        dependencies=dependencies,
        daemon_summary=_daemon_summary(ctx),
        patrol_summary=_patrol_summary(ctx.paths.state / "patrol-inspector.json"),
        statusline=_read_json(ctx.paths.statusline_snapshot),
        recent_merges=_recent_merge_count(ctx.paths.recent_pr_merges),
    )


def render_markdown(projection: HolisticStatusProjection) -> str:
    t = projection.throughput
    lines = [
        "## Holistic Status",
        "",
        f"Generated: `{projection.generated_at}`",
        "",
        "### Throughput",
        "",
        f"- Workers: actual `{t.actual_workers}` / target `{t.target_workers}` / floor `{t.floor}`",
        f"- Expected from open managed work: `{t.expected_workers}`",
        f"- Dispatch deficit: `{t.deficit}`",
        f"- Dispatch queue depth: `{t.queued_dispatches}`",
        "",
        "### Daemons",
        "",
    ]
    lines.extend(f"- {item}" for item in projection.daemon_summary)
    if not projection.daemon_summary:
        lines.append("- daemon status unavailable")
    lines.extend(["", "### Patrol", ""])
    if projection.patrol_summary:
        lines.extend(f"- {item}" for item in projection.patrol_summary)
    else:
        lines.append("- patrol state unavailable")
    lines.extend(["", "### Open Managed Items", ""])
    if projection.open_items:
        for item in projection.open_items:
            deps = ", ".join(item.dependencies) if item.dependencies else "-"
            lines.append(
                f"- {item.ref}: reason `{item.reason}`; phase `{item.phase or '-'}`; "
                f"expected `{item.expected_workers}`; deps `{deps}`; {item.title[:90]}"
            )
    else:
        lines.append("- no open managed items")
    lines.extend(["", "### Dependencies", ""])
    if projection.dependencies:
        lines.extend(
            f"- {dep.source} -> {dep.target}: `{dep.reason}`"
            for dep in projection.dependencies
        )
    else:
        lines.append("- no dependency edges")
    lines.extend(
        [
            "",
            "### Recent Merge Signal",
            "",
            f"- Recent PR merge records: `{projection.recent_merges}`",
        ]
    )
    return "\n".join(lines) + "\n"


def render_peek_summary(projection: HolisticStatusProjection) -> list[str]:
    t = projection.throughput
    blocked = sum(1 for item in projection.open_items if item.reason in {REASON_BLOCKED, REASON_MAINTAINER_DECISION})
    dispatchable = sum(
        1
        for item in projection.open_items
        if item.reason in {REASON_DISPATCHABLE, REASON_NO_WORKER_CAPACITY}
    )
    lines = [
        "▍Holistic status:",
        f"  workers actual={t.actual_workers} target={t.target_workers} floor={t.floor} deficit={t.deficit} queue={t.queued_dispatches}",
        f"  open_items={len(projection.open_items)} dispatchable={dispatchable} blocked={blocked} dependencies={len(projection.dependencies)} patrol={len(projection.patrol_summary)}",
    ]
    for item in projection.open_items[:5]:
        deps = ",".join(item.dependencies) if item.dependencies else "-"
        lines.append(f"  • {item.ref} reason={item.reason} phase={item.phase or '-'} deps={deps}")
    if len(projection.open_items) > 5:
        lines.append(f"  • ... {len(projection.open_items) - 5} more")
    return lines


def _load_items(
    ctx: LoopContext,
    github_items: Iterable[GhItem | ManagedItem | Mapping[str, object]] | None,
) -> tuple[ManagedItem, ...]:
    source = tuple(github_items) if github_items is not None else tuple(_load_github_items_with_ctx(ctx))
    rows: list[ManagedItem | Mapping[str, object]] = []
    for item in source:
        if isinstance(item, ManagedItem):
            rows.append(item)
        elif isinstance(item, GhItem):
            rows.append(
                {
                    "kind": "pr" if item.kind == "PR" else "issue",
                    "number": item.number,
                    "labels": item.labels,
                    "body": item.body,
                    "state": "open",
                    "title": item.title,
                }
            )
        else:
            rows.append(item)
    return ManagedWorkProjection(rows).items


def _load_github_items_with_ctx(ctx: LoopContext) -> tuple[GhItem, ...]:
    old_env = os.environ.copy()
    os.environ.update(ctx.env_for_subprocess())
    try:
        return tuple(load_github_items(ctx.repo_root))
    finally:
        os.environ.clear()
        os.environ.update(old_env)


def _throughput(ctx: LoopContext, managed: ManagedWorkProjection, monitor: Any | None) -> HolisticThroughput:
    actual = _actual_workers(ctx, monitor)
    floor = _configured_floor(ctx)
    expected = sum(
        max(0, label_catalog.phase_expected_workers(item.phase))
        for item in managed.effective_worker_items()
        if item.state == "open" and item.human != label_catalog.HUMAN_MAINTAINER_DECISION
    )
    display_target = sum(
        max(0, label_catalog.phase_expected_workers(item.phase))
        for item in managed.items
        if item.state == "open"
    )
    return HolisticThroughput(
        actual_workers=actual,
        floor=floor,
        expected_workers=expected,
        target_workers=display_target,
        deficit=max(0, display_target - actual),
        queued_dispatches=_dispatch_queue_depth(ctx),
    )


def _project_item(
    item: ManagedItem,
    managed: ManagedWorkProjection,
    throughput: HolisticThroughput,
) -> HolisticWorkItem:
    deps = tuple(dep.target for dep in _dependencies_for_item(item, managed))
    expected = max(0, label_catalog.phase_expected_workers(item.phase))
    return HolisticWorkItem(
        kind=item.kind,
        number=item.number,
        title=item.title,
        phase=item.phase,
        human=item.human,
        reason=_reason(item, managed, expected, throughput),
        expected_workers=expected,
        dependencies=deps,
    )


def _reason(
    item: ManagedItem,
    managed: ManagedWorkProjection,
    expected: int,
    throughput: HolisticThroughput,
) -> str:
    if item.kind == "issue" and item.number in managed.represented_issue_numbers():
        return REASON_REPRESENTED_BY_PR
    if item.human == label_catalog.HUMAN_MAINTAINER_DECISION:
        return REASON_MAINTAINER_DECISION
    if not item.phase:
        return REASON_NO_PHASE
    if item.phase == label_catalog.PHASE_BLOCKED:
        return REASON_BLOCKED
    if item.phase == label_catalog.PHASE_PR_OPEN:
        return REASON_PR_OPEN
    if item.phase == label_catalog.PHASE_CI_RUNNING:
        return REASON_CI_RUNNING
    if item.phase in {label_catalog.PHASE_MERGED, label_catalog.PHASE_CLOSED}:
        return REASON_TERMINAL
    if expected <= 0:
        return REASON_IN_FLIGHT
    if throughput.deficit > 0:
        return REASON_NO_WORKER_CAPACITY
    return REASON_DISPATCHABLE


def _dependencies(managed: ManagedWorkProjection) -> tuple[HolisticDependency, ...]:
    deps: list[HolisticDependency] = []
    for link in managed.links:
        deps.append(HolisticDependency(source=f"PR #{link.pr_number}", target=f"issue #{link.issue_number}", reason=link.source))
    return tuple(deps)


def _dependencies_for_item(item: ManagedItem, managed: ManagedWorkProjection) -> tuple[HolisticDependency, ...]:
    ref = f"{'PR' if item.kind == 'pr' else 'issue'} #{item.number}"
    deps = [dep for dep in _dependencies(managed) if dep.source == ref or dep.target == ref]
    if item.kind == "pr":
        for number in extract_closing_issue_numbers(item.body):
            target = f"issue #{number}"
            if all(dep.target != target for dep in deps):
                deps.append(HolisticDependency(source=ref, target=target, reason="pr-body-closing-ref"))
    return tuple(deps)


def _actual_workers(ctx: LoopContext, monitor: Any | None) -> int:
    if monitor is not None:
        try:
            return int(monitor.count_in_flight_codex())
        except Exception:
            pass
    result = _run_cli(ctx, "concurrency", "--count-only")
    try:
        return int((result.stdout or "0").strip().splitlines()[-1])
    except (IndexError, ValueError):
        return 0


def _dispatch_queue_depth(ctx: LoopContext) -> int:
    try:
        return sum(1 for _path in ctx.paths.dispatch_queue.glob("*/*.dispatch.json"))
    except OSError:
        return 0


def _configured_floor(ctx: LoopContext) -> int:
    raw = ctx.env_for_subprocess().get("CODEX_FLOOR", "5")
    try:
        value = int(raw)
    except ValueError:
        value = 5
    return max(2, value)


def _daemon_summary(ctx: LoopContext) -> tuple[str, ...]:
    try:
        report = collect_daemon_status(repo_root=ctx.repo_root, skill_root=ctx.skill_root)
    except Exception:
        return ()
    items: list[str] = []
    for daemon in report.daemons:
        suffix = ""
        if daemon.status == "stale":
            age = daemon.heartbeat_age_seconds if daemon.heartbeat_age_seconds is not None else "?"
            suffix = f" reason={daemon.stale_reason or 'stale'} age={age}s"
        items.append(f"{daemon.name}: {daemon.status}{suffix}")
    return tuple(items)


def _patrol_summary(path: Path) -> tuple[str, ...]:
    data = _read_json(path)
    status = data.get("status")
    if not isinstance(status, str) or not status:
        return ()
    findings = data.get("findings")
    count = len(findings) if isinstance(findings, list) else 0
    published = data.get("published")
    published_count = len(published) if isinstance(published, list) else 0
    return (f"patrol-inspector: {status} findings={count} published={published_count}",)


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _recent_merge_count(path: Path) -> int:
    data = _read_json(path)
    for key in ("merges", "items", "recent_merges"):
        value = data.get(key)
        if isinstance(value, list):
            return len(value)
    return 0


def _run_cli(ctx: LoopContext, *args: str) -> subprocess.CompletedProcess[str]:
    script = ctx.skill_root / "scripts" / "consensus-rnd-cli"
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(ctx.repo_root),
        env=ctx.env_for_subprocess(),
        capture_output=True,
        text=True,
        check=False,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="consensus-rnd-cli holistic-status",
        description="render the shared read-only holistic status card",
    )
    parser.parse_args(argv)
    try:
        ctx = LoopContext.load(read_only=True, allow_git_root_fallback=True, cwd=os.getcwd())
    except LoopContextError as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 2
    print(render_markdown(collect(ctx)), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
