"""Concurrency monitor daemon and canonical loop-owned codex counter."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from ..active_controller import require_active_controller, write_active_controller_status
from ..context import LoopContext, LoopContextError
from ..github_budget import graphql_headroom_ok
from ..heartbeat import DaemonHeartbeatLease
from .. import labels as label_catalog
from ..state import read_json, write_json
from ..update_check import parse_time
from ..work_items import ManagedWorkProjection, has_open_actionable_managed_work


PROGRESS_MARKER_RE = re.compile(r"\b(PHASE|REVIEW|FIX|META)[A-Z_]*:")
HEARTBEAT_STALE_SECONDS = 90
PRIORITIES = ("p0", "p1", "p2")
MUTABLE_DISPATCH_PREFIXES = ("implement-", "fix-pr", "remote-ci-fix", "test-add-", "verify-", "hotfix-")
MAIN_READONLY_DISPATCH_PREFIXES = ("audit-", "phase9-issue", "solver-", "meta-judge-", "review-pr", "reviewer-pr")
MAIN_READONLY_DISPATCH_PATTERNS = (
    re.compile(r"^audit-iter-[0-9]+[A-Za-z0-9._-]*$"),
    re.compile(r"^phase9-issue[0-9]+-r[0-9]+-(?:minimal|structural|delete|judge|reflector)$"),
    re.compile(r"^solver-issue[0-9]+-r[0-9]+-(?:minimal|structural|delete)$"),
    re.compile(r"^meta-judge-issue[0-9]+-r[0-9]+$"),
    re.compile(r"^review-pr[0-9]+(?:-[A-Za-z][A-Za-z0-9_-]*)?(?:-r[0-9]+)?$"),
    re.compile(r"^reviewer-pr[0-9]+(?:-[A-Za-z][A-Za-z0-9_-]*)?(?:-r[0-9]+)?$"),
)

PHASE_EXPECTED = {label: label_catalog.phase_expected_workers(label) for label in label_catalog.labels_for_group("phase")}
AUDIT_TASK_ID_RE = re.compile(r"(?<![A-Za-z0-9_-])(audit-iter-[0-9]+)(?:\.log|\.md|\b)")

_DEFAULT_MONITOR: ConcurrencyMonitor | None = None


@dataclass(frozen=True)
class Boundary:
    """Active audit task boundary used to avoid duplicate fallback dispatch."""

    task_id: str
    evidence: str


def utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    ts = utc_ts()
    print(f"[{ts}] {msg}", flush=True)


def log_tick_status(action: str) -> None:
    log(f"concurrency: tick {action}")


def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)


def _queue_state_empty(repo_root: Path, monitor: Any | None, queue_state: Any | None) -> bool:
    if isinstance(queue_state, bool):
        return queue_state
    if isinstance(queue_state, (list, tuple, set, dict)):
        return len(queue_state) == 0
    if monitor is not None:
        try:
            return bool(monitor.dispatch_queue_empty())
        except Exception:
            pass
    queue_root = repo_root / ".refactor-loop" / "dispatch-queue"
    if not queue_root.exists():
        return True
    return not any(queue_root.glob("*/*.dispatch.json"))


def _active_audit_task_from_lines(lines: Sequence[str]) -> Boundary | None:
    for line in lines:
        match = AUDIT_TASK_ID_RE.search(line)
        if match:
            return Boundary(task_id=match.group(1), evidence=line)
    return None


def single_active_audit_boundary(
    repo_root: Path,
    monitor: Any | None,
    gh_items: Any | None,
    queue_state: Any | None,
) -> Boundary | None:
    if has_open_actionable_managed_work(gh_items or []):
        return None
    if not _queue_state_empty(repo_root, monitor, queue_state):
        return None
    lines: list[str] = []
    if monitor is not None:
        try:
            lines = list(monitor.list_in_flight_codex_lines())
        except Exception:
            lines = []
    if not lines:
        return None
    return _active_audit_task_from_lines(lines)


class ConcurrencyMonitor:
    def __init__(self, ctx: LoopContext, *, interval: int | None = None) -> None:
        self.ctx = ctx
        self.repo_root = ctx.repo_root
        self.gh_repo_slug = ctx.gh_repo_slug
        self.interval = int(interval or os.environ.get("INTERVAL", "60"))
        self.alert_log = ctx.paths.refactor_loop / ".concurrency-alert.log"
        self.pending_events = ctx.paths.pending_events
        self.statusline_snapshot = ctx.paths.statusline_snapshot
        self.heartbeats_dir = ctx.paths.heartbeats
        self.dispatch_queue = ctx.paths.dispatch_queue
        self.dispatch_dispatched = ctx.paths.dispatch_dispatched
        self.dispatch_rejected = ctx.paths.dispatch_rejected
        self.state_file = ctx.paths.refactor_loop / ".concurrency-monitor-state.json"
        self._last_top_up_dispatches = 0

    def run(self, cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return _run(cmd)

    def configured_floor(self) -> int:
        try:
            floor = int(os.environ.get("CODEX_FLOOR", "5"))
        except ValueError:
            floor = 5
        return max(2, floor)

    def codex_floor(self) -> int:
        return self.configured_floor()

    def load_state(self) -> dict:
        state = read_json(self.state_file, {})
        return state if isinstance(state, dict) else {}

    def save_state(self, state: dict) -> None:
        write_json(self.state_file, state)

    def newest_progress_marker_at(self) -> float | None:
        newest: float | None = None
        for directory in (self.ctx.paths.logs, self.ctx.paths.runs):
            if not directory.exists():
                continue
            for path in directory.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if not PROGRESS_MARKER_RE.search(text):
                    continue
                mtime = path.stat().st_mtime
                newest = mtime if newest is None else max(newest, mtime)
        return newest

    def freeze_minutes(self, now: float | None = None) -> int:
        marker_at = self.newest_progress_marker_at()
        if marker_at is None:
            return 0
        if now is None:
            now = time.time()
        return max(0, int((now - marker_at) / 60))

    def read_daemon_heartbeats(self, now: float | None = None) -> dict[str, dict]:
        if now is None:
            now = time.time()
        result: dict[str, dict] = {}
        if not self.heartbeats_dir.exists():
            return result
        for heartbeat in sorted(self.heartbeats_dir.glob("*.ts")):
            name = heartbeat.stem
            try:
                raw = heartbeat.read_text(encoding="utf-8").strip()
                ts = int(raw)
                age = max(0, int(now - ts))
                result[name] = {"age_seconds": age, "stale": age >= HEARTBEAT_STALE_SECONDS}
            except (OSError, ValueError):
                result[name] = {"age_seconds": None, "stale": True}
        return result

    def write_statusline_snapshot(
        self,
        *,
        actual: int,
        expected: int,
        p0_streak: int,
        last_p0_at: str | None,
        open_pr_count: int,
        open_issue_count: int,
        daemons: dict[str, dict] | None = None,
        now: datetime | None = None,
    ) -> None:
        if now is None:
            now = datetime.now(timezone.utc)
        if daemons is None:
            daemons = self.read_daemon_heartbeats(now=now.timestamp())
        healthy = sum(1 for daemon in daemons.values() if not daemon["stale"])
        payload = {
            "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "actual": actual,
            "expected": expected,
            "floor": self.codex_floor(),
            "p0_streak": p0_streak,
            "last_p0_at": last_p0_at,
            "freeze_minutes": self.freeze_minutes(now.timestamp()),
            "open_pr_count": open_pr_count,
            "open_issue_count": open_issue_count,
            "daemons": daemons,
            "daemons_healthy": healthy,
            "daemons_total": len(daemons),
        }
        update_projection = self.read_update_projection(now=now)
        if update_projection:
            payload.update(update_projection)
        self.statusline_snapshot.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.statusline_snapshot.with_name(f".{self.statusline_snapshot.name}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.statusline_snapshot)

    def read_update_projection(self, *, now: datetime) -> dict[str, object]:
        raw = read_json(self.ctx.paths.state / "update-check.json", {})
        if not isinstance(raw, dict):
            return {}
        if raw.get("status") in {"disabled", "unknown"}:
            return {}
        checked_at = parse_time(raw.get("checked_at"))
        if checked_at is None:
            return {}
        interval = raw.get("interval_seconds")
        ttl = int(interval) if isinstance(interval, int) and interval > 0 else 21600
        if now - checked_at > timedelta(seconds=ttl * 2):
            return {}
        if raw.get("update_available") is not True:
            return {}
        latest = raw.get("latest_version")
        source = raw.get("update_source")
        release_url = raw.get("release_url")
        if not isinstance(latest, str) or not latest:
            return {}
        projection: dict[str, object] = {
            "update_available": True,
            "update_latest_version": latest,
            "update_checked_at": raw["checked_at"],
        }
        if isinstance(source, str) and source:
            projection["update_source"] = source
        if isinstance(release_url, str) and release_url:
            projection["update_release_url"] = release_url
        return projection

    def list_in_flight_codex_lines(self) -> list[str]:
        lines: list[str] = []
        for line in self.run(["ps", "-eo", "command="]).stdout.splitlines():
            if "consensus-rnd-cli" not in line or "spawn-codex" not in line:
                continue
            if " -c " in line:
                continue
            try:
                tokens = shlex.split(line)
            except ValueError:
                continue
            try:
                spawn_index = tokens.index("spawn-codex")
            except ValueError:
                continue
            try:
                cd_index = tokens.index("--cd", spawn_index + 1)
            except ValueError:
                continue
            if cd_index + 1 >= len(tokens):
                continue
            cd_token = Path(tokens[cd_index + 1]).expanduser()
            if not cd_token.is_absolute():
                cd_token = self.repo_root / cd_token
            try:
                cd_path = cd_token.resolve()
            except OSError:
                continue
            if not (cd_path == self.repo_root or self.repo_root in cd_path.parents):
                continue
            lines.append(line)
        return lines

    def count_in_flight_codex(self) -> int:
        return len(self.list_in_flight_codex_lines())

    def _gh_list_by_label(self, kind: str, query_label: str) -> list[dict]:
        cmd = ["gh", kind, "list"]
        if self.gh_repo_slug:
            cmd.extend(["--repo", self.gh_repo_slug])
        json_fields = "number,labels,body" if kind == "pr" else "number,labels"
        cmd.extend([
            "--label",
            query_label,
            "--state",
            "open",
            "--json",
            json_fields,
            "--limit",
            "100",
        ])
        result = self.run(cmd)
        if result.returncode != 0:
            return []
        try:
            rows = json.loads(result.stdout)
        except Exception:
            return []
        return rows if isinstance(rows, list) else []

    def list_auto_loop_issues(self) -> list[dict]:
        items: list[dict] = []
        seen: set[tuple[str, int]] = set()
        for kind in ("issue", "pr"):
            rows: list[dict] = []
            for query_label in label_catalog.query_labels_for(label_catalog.MANAGED):
                rows.extend(self._gh_list_by_label(kind, query_label))
            for entry in rows:
                try:
                    num = int(entry.get("number"))
                except (TypeError, ValueError):
                    continue
                key = (kind, num)
                if key in seen:
                    continue
                seen.add(key)
                label_names = [label.get("name", "") for label in entry.get("labels", [])]
                projection = label_catalog.normalize_label_set(label_names)
                phase = projection.phase or ""
                human = projection.human or ""
                items.append(
                    {
                        "number": num,
                        "kind": kind,
                        "phase": phase,
                        "human": human,
                        "labels": label_names,
                        "body": str(entry.get("body") or ""),
                        "state": "open",
                    }
                )
        return items

    def compute_expected(self, items: list[dict]) -> tuple[int, list[dict]]:
        breakdown = []
        total = 0
        for item in ManagedWorkProjection(items).effective_worker_items():
            if label_catalog.HUMAN_MAINTAINER_DECISION in label_catalog.normalize_label_set([item.human]).canonical:
                continue
            phase = label_catalog.normalize_label_set([str(item.phase)]).phase or ""
            expected = label_catalog.phase_expected_workers(phase)
            if expected > 0:
                breakdown.append({"id": f"#{item.number}", "kind": item.kind, "phase": phase, "expected": expected})
                total += expected
        return total, breakdown

    def write_pending_event(self, event: str) -> None:
        self.pending_events.parent.mkdir(parents=True, exist_ok=True)
        with self.pending_events.open("a", encoding="utf-8") as handle:
            handle.write(f"{utc_ts()} {event}\n")

    def write_alert(self, msg: str, detail: dict) -> None:
        self.alert_log.parent.mkdir(parents=True, exist_ok=True)
        ts = utc_ts()
        line = f"[{ts}] {msg} | detail={json.dumps(detail, ensure_ascii=False)}"
        with self.alert_log.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        with self.pending_events.open("a", encoding="utf-8") as handle:
            handle.write(f"{ts} concurrency-alert {msg}\n")

    def dispatch_queue_files(self) -> list[tuple[str, Path]]:
        files: list[tuple[str, Path]] = []
        for priority in PRIORITIES:
            priority_dir = self.dispatch_queue / priority
            if not priority_dir.is_dir():
                continue
            files.extend((priority, path) for path in sorted(priority_dir.glob("*.dispatch.json")))
        return files

    def dispatch_queue_empty(self) -> bool:
        return not self.dispatch_queue_files()

    def archive_dispatched(self, path: Path, payload: dict, task_id: str) -> Path:
        self.dispatch_dispatched.mkdir(parents=True, exist_ok=True)
        payload["dispatch_at"] = utc_ts()
        payload["source_dispatch_file"] = str(path)
        archive = self.dispatch_dispatched / f"{task_id}.json"
        if archive.exists():
            stamp = payload["dispatch_at"].replace(":", "").replace("-", "")
            archive = self.dispatch_dispatched / f"{task_id}-{stamp}.json"
        archive.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        path.unlink()
        return archive

    def append_harness_spawn_intent(self, payload: dict, task_id: str, priority: str, reason: str) -> dict[str, object]:
        intent = {
            "intent_id": f"dispatch:{task_id}",
            "source": "concurrency-monitor",
            "route": "dispatch-queue",
            "task_id": task_id,
            "priority": priority,
            "command": "spawn-codex",
            "controller_action": "spawn_codex_harness_background",
            "cd": self.ctx.durable_artifact_path(Path(str(payload["cd"]))),
            "prompt": self.ctx.durable_artifact_path(Path(str(payload["prompt"]))),
            "log": self.ctx.durable_artifact_path(Path(str(payload["log"]))),
            "stall": int(payload.get("stall", 5400)),
            "reason": reason,
            "queued_at": utc_ts(),
            "run_in_background_required": True,
            "no_lifecycle_authority": True,
        }
        self.write_pending_event(f"HARNESS_SPAWN_INTENT {json.dumps(intent, ensure_ascii=False, sort_keys=True)}")
        return intent

    def validate_dispatch_cwd(self, payload: dict, task_id: str) -> tuple[bool, str]:
        cd_raw = payload.get("cd")
        if not cd_raw:
            return False, "missing-cd"
        cd = Path(str(cd_raw))
        if not cd.is_absolute():
            return False, "relative-cd"

        repo_root = self.repo_root.resolve()
        worktrees_root = (self.repo_root / ".worktrees").resolve()
        cd_resolved = cd.resolve()

        try:
            cd_resolved.relative_to(repo_root)
        except ValueError:
            return False, "outside-repo"

        if any(pattern.fullmatch(task_id) for pattern in MAIN_READONLY_DISPATCH_PATTERNS):
            return True, "main-readonly-prefix"

        if cd_resolved == repo_root:
            return False, "repo-root-cd"
        try:
            cd_resolved.relative_to(worktrees_root)
        except ValueError:
            return False, "outside-worktrees"
        if cd_resolved == worktrees_root:
            return False, "worktrees-root-cd"
        return True, "worktrees-cd"

    def archive_rejected(self, path: Path, payload: dict, task_id: str, priority: str, reason: str) -> Path:
        self.dispatch_rejected.mkdir(parents=True, exist_ok=True)
        payload["rejected_at"] = utc_ts()
        payload["reject_reason"] = reason
        payload["priority"] = priority
        payload["source_dispatch_file"] = str(path)
        archive = self.dispatch_rejected / f"{task_id}.json"
        if archive.exists():
            stamp = payload["rejected_at"].replace(":", "").replace("-", "")
            archive = self.dispatch_rejected / f"{task_id}-{stamp}.json"
        archive.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        path.unlink()
        return archive

    def dispatch_one_from_queue(self) -> tuple[str, str, str] | None:
        decision = require_active_controller(self.ctx, "concurrency-dispatch")
        write_active_controller_status(self.ctx, decision)
        if not decision.allowed:
            log(f"active_controller=noop:not-owner action=concurrency-dispatch owner={decision.owner_device}")
            return None
        for priority, path in self.dispatch_queue_files():
            payload = json.loads(path.read_text(encoding="utf-8"))
            task_id = str(payload.get("task_id") or path.name.removesuffix(".dispatch.json"))
            reason = str(payload.get("reason", ""))
            payload["task_id"] = task_id
            payload["priority"] = priority
            ok, reject_reason = self.validate_dispatch_cwd(payload, task_id)
            if not ok:
                self.archive_rejected(path, payload, task_id, priority, reject_reason)
                event = f"DISPATCH_REJECTED:{task_id}:{priority}:main-worktree-cd:{reject_reason}"
                self.write_pending_event(event)
                log(event)
                continue
            intent = self.append_harness_spawn_intent(payload, task_id, priority, reason)
            payload["intent_id"] = intent["intent_id"]
            payload["intent_queued_at"] = intent["queued_at"]
            payload["dispatch_state"] = "harness-intent"
            self.archive_dispatched(path, payload, task_id)
            self.write_pending_event(f"DISPATCH_INTENT:{task_id}:{priority}:{reason}")
            log(f"DISPATCH_INTENT:{task_id}:{priority}:{reason}")
            return task_id, priority, reason
        return None

    def top_up_from_dispatch_queue(self, actual: int, floor: int) -> int:
        self._last_top_up_dispatches = 0
        if actual >= floor:
            return actual
        if not graphql_headroom_ok(cwd=self.ctx.repo_root, env=self.ctx.env_for_subprocess()):
            self.write_pending_event("DISPATCH_BACKOFF:graphql-headroom-low")
            log_tick_status("skip:graphql-backoff remaining=unknown")
            return actual
        max_dispatches = floor - actual
        dispatched = 0
        for _ in range(max_dispatches):
            fired = self.dispatch_one_from_queue()
            if fired is None:
                break
            dispatched += 1
            actual = self.count_in_flight_codex()
            if actual >= floor:
                break
        self._last_top_up_dispatches = dispatched
        return actual

    def tick(self) -> None:
        state = self.load_state()
        zero_streak = int(state.get("zero_streak", 0))
        decision = require_active_controller(self.ctx, "concurrency-tick")
        write_active_controller_status(self.ctx, decision)
        owner_allowed = decision.allowed

        items = self.list_auto_loop_issues()
        expected, breakdown = self.compute_expected(items)
        actual = self.count_in_flight_codex()
        open_pr_count = sum(1 for item in items if item["kind"] == "pr")
        open_issue_count = sum(1 for item in items if item["kind"] == "issue")
        floor = self.configured_floor()

        log(f"actual={actual} expected={expected} floor={floor} zero_streak={zero_streak}")

        if expected > 0 and actual == 0:
            zero_streak += 1
            state["zero_streak"] = zero_streak
            p0_at = utc_ts()
            state["last_p0_at"] = p0_at
            detail = {
                "actual": 0,
                "expected": expected,
                "breakdown": breakdown,
                "zero_streak": zero_streak,
                "severity": "P0",
                "rule": "no-gap-violation",
            }
            self.write_alert(f"P0 no-gap-violation: 0 codex with {expected} active task(s)", detail)
            log(f"P0 ALERT: 0 codex but {expected} active task(s);streak={zero_streak};see {self.alert_log}")
            dispatched = 0
            if owner_allowed and not self.dispatch_queue_empty():
                actual = self.top_up_from_dispatch_queue(actual, max(expected, self.configured_floor()))
                dispatched = self._last_top_up_dispatches
            if dispatched:
                log_tick_status(f"dispatched {dispatched} spawn-intent")
            else:
                log_tick_status(f"blocked:p0-no-gap-violation expected={expected}")
            self.write_statusline_snapshot(
                actual=actual,
                expected=expected,
                p0_streak=zero_streak,
                last_p0_at=p0_at,
                open_pr_count=open_pr_count,
                open_issue_count=open_issue_count,
            )
            self.save_state(state)
            return

        state["zero_streak"] = 0
        target = max(expected, floor)
        tick_action = "noop:at-or-above-floor"
        if actual < target:
            if self.dispatch_queue_empty():
                deficit = target - actual
                boundary = None
                if expected == 0:
                    boundary = single_active_audit_boundary(self.repo_root, self, items, True)
                if owner_allowed and boundary is not None:
                    event = (
                        "WAIT:single-active-audit:dispatch_required=0:"
                        f"actual={actual} expected={expected} queue=0 blocked_deficit={deficit}"
                    )
                    self.write_pending_event(event)
                    log(event)
                    tick_action = f"blocked:single-active-audit deficit={deficit}"
                elif owner_allowed:
                    self.write_pending_event(f"HARD_GATE:dispatch_required={deficit}:actual={actual} expected={expected} queue=0")
                    log(f"HARD_GATE:dispatch_required={deficit}:actual={actual} expected={expected} queue=0")
                    tick_action = f"blocked:dispatch-required deficit={deficit}"
                else:
                    log(
                        "active_controller=noop:not-owner "
                        f"action=concurrency-tick dispatch_required={deficit} owner={decision.owner_device}"
                    )
                    tick_action = f"noop:not-owner deficit={deficit}"
            else:
                if owner_allowed:
                    actual = self.top_up_from_dispatch_queue(actual, target)
                    dispatched = self._last_top_up_dispatches
                    tick_action = f"dispatched {dispatched} spawn-intent" if dispatched else "blocked:dispatch-queue-present-no-dispatch"
                else:
                    log(f"active_controller=noop:not-owner action=concurrency-top-up owner={decision.owner_device}")
                    tick_action = "noop:not-owner"
        log_tick_status(tick_action)

        self.write_statusline_snapshot(
            actual=actual,
            expected=expected,
            p0_streak=0,
            last_p0_at=state.get("last_p0_at"),
            open_pr_count=open_pr_count,
            open_issue_count=open_issue_count,
        )
        self.save_state(state)

    def run_forever(self) -> int:
        log(f"concurrency_monitor (Python) started: interval={self.interval}s")
        lease = DaemonHeartbeatLease("concurrency_monitor", self.repo_root)
        while True:
            try:
                self.tick()
            except Exception as exc:
                log(f"EXCEPTION in tick: {exc!r}")
            lease.beat()
            lease.sleep_with_lease(self.interval)


def load_monitor(*, read_only: bool = False, allow_git_root_fallback: bool | None = None, cwd: str | Path | None = None) -> ConcurrencyMonitor:
    ctx = LoopContext.load(read_only=read_only, allow_git_root_fallback=allow_git_root_fallback, cwd=cwd)
    return ConcurrencyMonitor(ctx)


def _default_monitor(*, read_only: bool = False) -> ConcurrencyMonitor:
    global _DEFAULT_MONITOR
    if _DEFAULT_MONITOR is None:
        _DEFAULT_MONITOR = load_monitor(read_only=read_only, cwd=os.getcwd())
    return _DEFAULT_MONITOR


def configured_floor() -> int:
    return _default_monitor().configured_floor()


def codex_floor() -> int:
    return _default_monitor().codex_floor()


def load_state() -> dict:
    return _default_monitor().load_state()


def save_state(state: dict) -> None:
    _default_monitor().save_state(state)


def newest_progress_marker_at() -> float | None:
    return _default_monitor().newest_progress_marker_at()


def freeze_minutes(now: float | None = None) -> int:
    return _default_monitor().freeze_minutes(now=now)


def read_daemon_heartbeats(now: float | None = None) -> dict[str, dict]:
    return _default_monitor().read_daemon_heartbeats(now=now)


def write_statusline_snapshot(**kwargs: object) -> None:
    _default_monitor().write_statusline_snapshot(**kwargs)


def count_in_flight_codex() -> int:
    return _default_monitor(read_only=True).count_in_flight_codex()


def list_in_flight_codex_lines() -> list[str]:
    return _default_monitor(read_only=True).list_in_flight_codex_lines()


def list_auto_loop_issues() -> list[dict]:
    return _default_monitor().list_auto_loop_issues()


def compute_expected(items: list[dict]) -> tuple[int, list[dict]]:
    return _default_monitor().compute_expected(items)


def write_alert(msg: str, detail: dict) -> None:
    _default_monitor().write_alert(msg, detail)


def write_pending_event(event: str) -> None:
    _default_monitor().write_pending_event(event)


def dispatch_queue_files() -> list[tuple[str, Path]]:
    return _default_monitor().dispatch_queue_files()


def dispatch_queue_empty() -> bool:
    return _default_monitor().dispatch_queue_empty()


def archive_dispatched(path: Path, payload: dict, task_id: str) -> Path:
    return _default_monitor().archive_dispatched(path, payload, task_id)


def validate_dispatch_cwd(payload: dict, task_id: str) -> tuple[bool, str]:
    return _default_monitor().validate_dispatch_cwd(payload, task_id)


def archive_rejected(path: Path, payload: dict, task_id: str, priority: str, reason: str) -> Path:
    return _default_monitor().archive_rejected(path, payload, task_id, priority, reason)


def dispatch_one_from_queue() -> tuple[str, str, str] | None:
    return _default_monitor().dispatch_one_from_queue()


def top_up_from_dispatch_queue(actual: int, floor: int) -> int:
    return _default_monitor().top_up_from_dispatch_queue(actual, floor)


def tick() -> None:
    _default_monitor().tick()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="concurrency monitor + canonical codex counter")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--count-only", action="store_true", help="print canonical in-flight codex count and exit")
    mode.add_argument("--list-codex", action="store_true", help="print one supervisor cmdline per line and exit")
    mode.add_argument("--once", action="store_true", help="run one tick and exit (no daemon loop)")
    mode.add_argument("--daemon", action="store_true", help="run persistently")
    args = parser.parse_args(argv)

    read_only_fallback = args.count_only or args.list_codex or args.once
    try:
        monitor = load_monitor(read_only=read_only_fallback, allow_git_root_fallback=read_only_fallback, cwd=os.getcwd())
    except LoopContextError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    if args.count_only:
        print(monitor.count_in_flight_codex())
        return 0
    if args.list_codex:
        for line in monitor.list_in_flight_codex_lines():
            print(line)
        return 0
    if args.once:
        monitor.tick()
        return 0
    return monitor.run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
