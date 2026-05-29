"""Concurrency monitor daemon and canonical loop-owned codex counter."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ..context import LoopContext, LoopContextError
from ..heartbeat import DaemonHeartbeatLease
from ..ownership import GitHubWorkOwnership, WorkTargetResolver
from ..state import read_json, write_json


PROGRESS_MARKER_RE = re.compile(r"\b(PHASE|REVIEW|FIX|META)[A-Z_]*:")
DEGRADATION_ALERT_LOG = ".refactor-loop/.degradation-alert.log"
HEARTBEAT_STALE_SECONDS = 90
PRIORITIES = ("p0", "p1", "p2")
MUTABLE_DISPATCH_PREFIXES = ("implement-", "fix-pr", "remote-ci-fix", "test-add-", "verify-", "hotfix-")
MAIN_READONLY_DISPATCH_PREFIXES = ("audit-", "phase9-issue", "solver-", "meta-judge-", "review-pr", "reviewer-pr")

PHASE_EXPECTED = {
    "🔍 phase:design-solving": 1,
    "🔧 phase:fixing": 1,
    "👀 phase:reviewing": 1,
    "🛠️ phase:implementing": 1,
    "⚙️ phase:ci-running": 0,
    "🚀 phase:pr-open": 0,
    "✅ phase:consensus-reached": 0,
    "🎉 phase:merged": 0,
    "⏸️ phase:blocked": 0,
}

_DEFAULT_MONITOR: ConcurrencyMonitor | None = None


def utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    ts = utc_ts()
    print(f"[{ts}] {msg}", flush=True)


def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)


class ConcurrencyMonitor:
    def __init__(self, ctx: LoopContext, *, interval: int | None = None) -> None:
        self.ctx = ctx
        self.repo_root = ctx.repo_root
        self.gh_repo_slug = ctx.gh_repo_slug
        self.interval = int(interval or os.environ.get("INTERVAL", "60"))
        self.alert_log = ctx.paths.refactor_loop / ".concurrency-alert.log"
        self.degradation_alert_log = ctx.repo_root / DEGRADATION_ALERT_LOG
        self.pending_events = ctx.paths.pending_events
        self.statusline_snapshot = ctx.paths.statusline_snapshot
        self.heartbeats_dir = ctx.paths.heartbeats
        self.dispatch_queue = ctx.paths.dispatch_queue
        self.dispatch_dispatched = ctx.paths.dispatch_dispatched
        self.dispatch_rejected = ctx.paths.dispatch_rejected
        self.state_file = ctx.paths.refactor_loop / ".concurrency-monitor-state.json"
        self.cli = ctx.skill_root / "scripts" / "consensus-rnd-cli"

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
        self.statusline_snapshot.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.statusline_snapshot.with_name(f".{self.statusline_snapshot.name}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.statusline_snapshot)

    def list_in_flight_codex_lines(self) -> list[str]:
        lines: list[str] = []
        for line in self.run(["ps", "-eo", "command="]).stdout.splitlines():
            if "consensus-rnd-cli" not in line or "spawn-codex" not in line:
                continue
            if " -c " in line:
                continue
            tokens = line.split()
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
            try:
                cd_path = Path(tokens[cd_index + 1]).expanduser().resolve()
            except OSError:
                continue
            if not (cd_path == self.repo_root or self.repo_root in cd_path.parents):
                continue
            lines.append(line)
        return lines

    def count_in_flight_codex(self) -> int:
        return len(self.list_in_flight_codex_lines())

    def list_auto_loop_issues(self) -> list[dict]:
        items: list[dict] = []
        for kind, gh_cmd in (("issue", "issue"), ("pr", "pr")):
            cmd = ["gh", gh_cmd, "list"]
            if self.gh_repo_slug:
                cmd.extend(["--repo", self.gh_repo_slug])
            cmd.extend([
                "--label",
                "auto-loop",
                "--state",
                "open",
                "--json",
                    "number,labels,author,updatedAt",
                "--limit",
                "100",
            ])
            result = self.run(cmd)
            if result.returncode != 0:
                continue
            try:
                rows = json.loads(result.stdout)
            except Exception:
                continue
            for entry in rows:
                num = entry.get("number")
                labels = [label.get("name", "") for label in entry.get("labels", [])]
                phase = next((label for label in labels if label.startswith(("🔍", "🔧", "👀", "🛠️", "⚙️", "🚀", "✅", "🎉", "⏸️"))), "")
                human = next((label for label in labels if label.startswith(("🤖", "👤", "🆘"))), "")
                items.append({"number": num, "kind": kind, "phase": phase, "human": human, "github_target": {"kind": kind, "number": num}})
        return items

    def compute_expected(self, items: list[dict]) -> tuple[int, list[dict]]:
        # Refactor (iter/issue-193):
        #   Old pattern: active-task expected count treated fresh foreign
        #   author.login items as local work and could trigger duplicate spawn.
        #   New principle: count only owned or 3h-stale GitHub-native targets.
        breakdown = []
        total = 0
        for item in items:
            if item["human"] == "👤 human:需-maintainer-决策":
                continue
            if not self.dispatch_ownership_allowed(item):
                continue
            expected = PHASE_EXPECTED.get(item["phase"], 0)
            if expected > 0:
                breakdown.append({"id": f"#{item['number']}", "kind": item["kind"], "phase": item["phase"], "expected": expected})
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

    def degradation_watch_interval_seconds(self) -> int:
        raw = os.environ.get("DEGRADATION_WATCH_INTERVAL_SECONDS", "1800")
        try:
            interval = int(raw)
        except ValueError:
            interval = 1800
        return max(0, interval)

    def degradation_watch_timeout_seconds(self) -> int:
        raw = os.environ.get("DEGRADATION_WATCH_TIMEOUT_SECONDS", "30")
        try:
            timeout = int(raw)
        except ValueError:
            timeout = 30
        return max(1, timeout)

    # Refactor (iter5/cluster-issue66-skill-degradation):
    #   Old: no standalone watchdog, no DegradationCheck protocol, no plugin registry, and no GitHub auto-open path.
    #   New: runtime monitoring stays a concurrency monitor throttled hook that calls the single-file static checker and emits local alerts only; it is not an independent watchdog.
    def run_skill_degradation_check(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(self.cli),
                "check-degradation",
                "--static",
                "--repo-root",
                str(self.repo_root),
            ],
            capture_output=True,
            text=True,
            timeout=self.degradation_watch_timeout_seconds(),
            check=False,
        )

    def write_degradation_alert(self, result: subprocess.CompletedProcess[str] | None, error: str | None = None) -> None:
        self.degradation_alert_log.parent.mkdir(parents=True, exist_ok=True)
        ts = utc_ts()
        if result is None:
            detail = {"error": error or "unknown"}
            summary = "skill-degradation-alert checker-error"
        else:
            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            detail = {
                "returncode": result.returncode,
                "stdout_tail": stdout[-4000:],
                "stderr_tail": stderr[-4000:],
            }
            summary = f"skill-degradation-alert returncode={result.returncode}"
        with self.degradation_alert_log.open("a", encoding="utf-8") as handle:
            handle.write(f"[{ts}] {summary} | detail={json.dumps(detail, ensure_ascii=False)}\n")
        self.write_pending_event(f"{summary} log=.refactor-loop/.degradation-alert.log")

    def maybe_run_skill_degradation_watch(self, state: dict) -> None:
        interval = self.degradation_watch_interval_seconds()
        if interval <= 0:
            return
        now = int(time.time())
        last = int(state.get("last_degradation_watch_at", 0) or 0)
        if last and now - last < interval:
            return
        state["last_degradation_watch_at"] = now
        try:
            result = self.run_skill_degradation_check()
        except subprocess.TimeoutExpired as exc:
            self.write_degradation_alert(None, error=f"timeout after {exc.timeout}s")
            log(f"skill-degradation-alert timeout after {exc.timeout}s; see {self.degradation_alert_log}")
            return
        except Exception as exc:
            self.write_degradation_alert(None, error=repr(exc))
            log(f"skill-degradation-alert exception={exc!r}; see {self.degradation_alert_log}")
            return
        if result.returncode != 0:
            self.write_degradation_alert(result)
            log(f"skill-degradation-alert returncode={result.returncode}; see {self.degradation_alert_log}")

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

    # Refactor (iter6/issue-133):
    #   Old pattern: concurrency monitor passed queue payload[cd] straight to spawn-codex.sh --cd, letting a mutable task run in the repo-root/main worktree.
    #   New principle: structural consensus dispatch queue mutable-prefix cwd guard, no shared workspace policy. See .refactor-loop/runs/phase9-issue133-r4-judge.md.
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

        if task_id.startswith(MAIN_READONLY_DISPATCH_PREFIXES):
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

    # Refactor (iter6/issue-133):
    #   Old pattern: concurrency monitor passed queue payload[cd] straight to spawn-codex.sh --cd, letting a mutable task run in the repo-root/main worktree.
    #   New principle: structural consensus dispatch queue mutable-prefix cwd guard, no shared workspace policy. See .refactor-loop/runs/phase9-issue133-r4-judge.md.
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

    def launch_dispatch(self, payload: dict) -> None:
        subprocess.Popen(
            [
                "nohup",
                str(self.cli),
                "spawn-codex",
                "--cd",
                str(payload["cd"]),
                "--prompt",
                str(payload["prompt"]),
                "--log",
                str(payload["log"]),
                "--stall",
                str(payload.get("stall", 5400)),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def dispatch_ownership_allowed(self, payload: dict) -> bool:
        # Refactor (iter/issue-193):
        #   Old pattern: dispatch queue side effects were gated only by local
        #   queue/cwd state, not GitHub-native work ownership.
        #   New principle: issue/PR targets must pass author.login ownership
        #   or updatedAt 3h stale takeover before spawn.
        target = WorkTargetResolver.from_payload(payload)
        if target is None or not self.gh_repo_slug or "github_target" not in payload:
            return True
        decision = GitHubWorkOwnership(self.gh_repo_slug, cwd=self.repo_root).decide(target)
        return decision.allowed

    # Refactor (iter6/issue-133):
    #   Old pattern: concurrency monitor passed queue payload[cd] straight to spawn-codex.sh --cd, letting a mutable task run in the repo-root/main worktree.
    #   New principle: structural consensus dispatch queue mutable-prefix cwd guard, no shared workspace policy. See .refactor-loop/runs/phase9-issue133-r4-judge.md.
    def dispatch_one_from_queue(self) -> tuple[str, str, str] | None:
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
            if not self.dispatch_ownership_allowed(payload):
                event = f"DISPATCH_SKIPPED_FOREIGN_OWNER:{task_id}:{priority}:ownership-not-allowed"
                self.write_pending_event(event)
                log(event)
                continue
            self.launch_dispatch(payload)
            self.archive_dispatched(path, payload, task_id)
            self.write_pending_event(f"DISPATCH_FIRED:{task_id}:{priority}:{reason}")
            log(f"DISPATCH_FIRED:{task_id}:{priority}:{reason}")
            return task_id, priority, reason
        return None

    # Refactor (iter4/concurrency-auto-topup):
    #   Old pattern: monitor only alerted; actual<floor waited for the LLM controller's next wakeup.
    #   New principle: monitor automatically consumes dispatch-queue entries until the floor is satisfied or queue is empty.
    def top_up_from_dispatch_queue(self, actual: int, floor: int) -> int:
        if actual >= floor:
            return actual
        max_dispatches = floor - actual
        for _ in range(max_dispatches):
            fired = self.dispatch_one_from_queue()
            if fired is None:
                break
            actual = self.count_in_flight_codex()
            if actual >= floor:
                break
        return actual

    # Refactor (iter4/concurrency-auto-topup):
    #   Old pattern: single no-gap sentinel path could alert and leave deficit repair to a later controller wakeup.
    #   New principle: no-gap alerting continues into deficit detection so queued work can be fired in the same tick.
    def tick(self) -> None:
        state = self.load_state()
        zero_streak = int(state.get("zero_streak", 0))
        self.maybe_run_skill_degradation_watch(state)

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
            if not self.dispatch_queue_empty():
                actual = self.top_up_from_dispatch_queue(actual, max(expected, self.configured_floor()))
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
        if actual < target:
            if self.dispatch_queue_empty():
                deficit = target - actual
                self.write_pending_event(f"HARD_GATE:dispatch_required={deficit}:actual={actual} expected={expected} queue=0")
                log(f"HARD_GATE:dispatch_required={deficit}:actual={actual} expected={expected} queue=0")
            else:
                actual = self.top_up_from_dispatch_queue(actual, target)

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


def degradation_watch_interval_seconds() -> int:
    return _default_monitor().degradation_watch_interval_seconds()


def degradation_watch_timeout_seconds() -> int:
    return _default_monitor().degradation_watch_timeout_seconds()


def run_skill_degradation_check() -> subprocess.CompletedProcess[str]:
    return _default_monitor().run_skill_degradation_check()


def write_degradation_alert(result: subprocess.CompletedProcess[str] | None, error: str | None = None) -> None:
    _default_monitor().write_degradation_alert(result, error=error)


def maybe_run_skill_degradation_watch(state: dict) -> None:
    _default_monitor().maybe_run_skill_degradation_watch(state)


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


def launch_dispatch(payload: dict) -> None:
    _default_monitor().launch_dispatch(payload)


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
