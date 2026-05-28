#!/usr/bin/env python3
"""
concurrency_monitor.py — monitor active work for zero-codex gaps.

Design:
- Tick every 60 seconds.
- Infer expected concurrency from GitHub, with each active phase issue/PR
  contributing one unit.
  - 🔍 design-solving       → 1 solver-round OR 1 judge OR 1 reflector
  - 🔧 fixing               → 1 fix codex
  - 👀 reviewing            → 3 reviewers per r1 round
  - 🛠️ implementing         → 1 implement codex
  - ⚙️ ci-running           → 0 (waiting for CI, no codex required)
  - 🚀 pr-open              → 0~3 while waiting to dispatch reviewers
- Compare with loop-owned spawn-codex wrapper processes.
- Monitor no-gap P0: alert immediately when `expected > 0 and actual == 0`.
- When actual < CODEX_FLOOR and dispatch-queue is non-empty, automatically
  consume queued work up to the floor.

Active repair:
1. Append alerts with timestamp and details to `.refactor-loop/.concurrency-alert.log`.
2. Append controller events to `.refactor-loop/.controller-pending-events.log`
   for the next wakeup.
3. Log a detailed expected breakdown showing which issues/PRs lack codex.
4. Automatically dispatch queued codex from `.refactor-loop/dispatch-queue/<priority>/`
   and archive an audit trail.

Launch:
  nohup python3 .claude/skills/codex-refactor-loop/scripts/concurrency_monitor.py \\
    >> .refactor-loop/logs/concurrency-monitor.log 2>&1 &
  disown

⟦AI:AUTO-LOOP⟧
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from daemon_heartbeat import DaemonHeartbeatLease
from repo_config import github_repo_slug


def git_repo_root() -> Path:
    """Return the host repository root from env, or explicit interactive fallback."""
    env_root = os.environ.get("REPO_ROOT")
    if env_root:
        return Path(env_root)
    if os.environ.get("ALLOW_GIT_ROOT_FALLBACK") != "1":
        raise RuntimeError(
            "REPO_ROOT is unset; source .refactor-loop/host.env or set "
            "ALLOW_GIT_ROOT_FALLBACK=1 for interactive use"
        )
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise RuntimeError("REPO_ROOT is unset and git rev-parse --show-toplevel failed")
    return Path(r.stdout.strip())


INTERVAL = int(os.environ.get("INTERVAL", "60"))
# Read-only CLI flags (--count-only / --list-codex / --once) are the canonical
# CLI surface controllers MUST call (per SKILL.md "Concurrency Floor"). They
# read process state only and never write, so let them fall back to
# `git rev-parse --show-toplevel` when REPO_ROOT is not exported. The daemon
# (no flags) still requires explicit REPO_ROOT to avoid host-fact leakage.
_CLI_READONLY_FLAGS = {"--count-only", "--list-codex", "--once"}
if any(arg in _CLI_READONLY_FLAGS for arg in sys.argv[1:]):
    os.environ.setdefault("ALLOW_GIT_ROOT_FALLBACK", "1")
REPO_ROOT = git_repo_root()
GH_REPO_SLUG = github_repo_slug()
ALERT_LOG = REPO_ROOT / ".refactor-loop" / ".concurrency-alert.log"
DEGRADATION_ALERT_LOG = REPO_ROOT / ".refactor-loop" / ".degradation-alert.log"
PENDING_EVENTS = REPO_ROOT / ".refactor-loop" / ".controller-pending-events.log"
STATUSLINE_SNAPSHOT = REPO_ROOT / ".refactor-loop" / "state" / "statusline-snapshot.json"
HEARTBEATS_DIR = REPO_ROOT / ".refactor-loop" / "heartbeats"
HEARTBEAT_STALE_SECONDS = 90
PROGRESS_MARKER_RE = re.compile(r"\b(PHASE|REVIEW|FIX|META)[A-Z_]*:")
DISPATCH_QUEUE = REPO_ROOT / ".refactor-loop" / "dispatch-queue"
DISPATCH_DISPATCHED = REPO_ROOT / ".refactor-loop" / "dispatch-dispatched"
SPAWN_CODEX = Path(__file__).resolve().parent / "spawn-codex.sh"
PRIORITIES = ("p0", "p1", "p2")

# Phase label -> expected codex count per active issue/PR.
PHASE_EXPECTED = {
    "🔍 phase:design-solving": 1,  # At least 1 codex (solver round / judge / reflector).
    "🔧 phase:fixing": 1,
    "👀 phase:reviewing": 1,        # At least 1 reviewer.
    "🛠️ phase:implementing": 1,
    # The following phases do not expect codex.
    "⚙️ phase:ci-running": 0,
    "🚀 phase:pr-open": 0,
    "✅ phase:consensus-reached": 0,
    "🎉 phase:merged": 0,
    "⏸️ phase:blocked": 0,
}

# consecutive no-gap counter persisted in state file
STATE_FILE = REPO_ROOT / ".refactor-loop" / ".concurrency-monitor-state.json"
CHECK_SKILL_DEGRADATION = Path(__file__).resolve().parent / "check_skill_degradation.py"


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def configured_floor() -> int:
    try:
        floor = int(os.environ.get("CODEX_FLOOR", "5"))
    except ValueError:
        floor = 5
    return max(2, floor)


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(s: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(s, indent=2))


def codex_floor() -> int:
    try:
        floor = int(os.getenv("CODEX_FLOOR", "5"))
    except ValueError:
        floor = 5
    return max(floor, 2)


def newest_progress_marker_at() -> float | None:
    newest: float | None = None
    for directory in (REPO_ROOT / ".refactor-loop" / "logs", REPO_ROOT / ".refactor-loop" / "runs"):
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


def freeze_minutes(now: float | None = None) -> int:
    marker_at = newest_progress_marker_at()
    if marker_at is None:
        return 0
    if now is None:
        now = time.time()
    return max(0, int((now - marker_at) / 60))


def read_daemon_heartbeats(now: float | None = None) -> dict[str, dict]:
    """Discover heartbeat files and report age/stale per daemon.

    Each `.ts` file under HEARTBEATS_DIR is treated as one daemon. The file name
    minus `.ts` is the daemon key. A missing/malformed/empty file is reported
    as `stale=True` with `age_seconds=None`. Discovery is dynamic so adding a
    new daemon that writes a heartbeat is automatically surfaced; no caller
    list to keep in sync.
    """
    if now is None:
        now = time.time()
    result: dict[str, dict] = {}
    if not HEARTBEATS_DIR.exists():
        return result
    for hb in sorted(HEARTBEATS_DIR.glob("*.ts")):
        name = hb.stem
        try:
            raw = hb.read_text(encoding="utf-8").strip()
            ts = int(raw)
            age = max(0, int(now - ts))
            result[name] = {"age_seconds": age, "stale": age >= HEARTBEAT_STALE_SECONDS}
        except (OSError, ValueError):
            result[name] = {"age_seconds": None, "stale": True}
    return result


def write_statusline_snapshot(
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
    """Write the Claude Code statusline snapshot atomically."""
    if now is None:
        now = datetime.now(timezone.utc)
    if daemons is None:
        daemons = read_daemon_heartbeats(now=now.timestamp())
    healthy = sum(1 for d in daemons.values() if not d["stale"])
    payload = {
        "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "actual": actual,
        "expected": expected,
        "floor": codex_floor(),
        "p0_streak": p0_streak,
        "last_p0_at": last_p0_at,
        "freeze_minutes": freeze_minutes(now.timestamp()),
        "open_pr_count": open_pr_count,
        "open_issue_count": open_issue_count,
        "daemons": daemons,
        "daemons_healthy": healthy,
        "daemons_total": len(daemons),
    }
    STATUSLINE_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUSLINE_SNAPSHOT.with_name(f".{STATUSLINE_SNAPSHOT.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.rename(tmp, STATUSLINE_SNAPSHOT)


def count_in_flight_codex() -> int:
    """Count THIS repo's in-flight loop codex only — scoped by absolute REPO_ROOT.

    Cross-host over-count bug: two codex-refactor-loop loops on one machine share
    the relative `.refactor-loop/` substring, so a relative-only filter counts the
    OTHER host's codex too (observed actual=8 when this repo had 1) -> floor looks
    permanently "full" and this repo can starve below its minimum. Scope by the
    absolute REPO_ROOT: spawn-codex.sh always carries it (caller passes an absolute
    --cd; inside worktree paths `<repo>/.worktrees/...` are REPO_ROOT-prefixed too), so a
    foreign loop at a different path is correctly excluded.

    Contract: callers MUST pass an absolute --cd (and absolute --log/--add-dir) so
    REPO_ROOT appears in the process cmdline. A relative --cd breaks this scope.

    De-dup: each spawned codex shows up as TWO `spawn-codex.sh`-bearing processes —
    the real supervisor (`bash <path>/spawn-codex.sh --cd ...`) AND a shell `-c`
    wrapper that echoes the whole command (the Claude Code harness's background-task
    wrapper, or any `bash -c "...spawn-codex.sh..."`). Counting both double-counts,
    so a floor of 2 would be "satisfied" by a single real codex. Exclude any
    cmdline containing ` -c ` so only the real supervisor is counted (1 per codex);
    spawn-codex.sh itself never takes ` -c ` flags.
    """
    repo = str(REPO_ROOT)
    n = 0
    for line in run(["ps", "-eo", "command="]).stdout.splitlines():
        if "spawn-codex.sh" not in line:
            continue
        if repo not in line:
            continue  # another host's loop on the same machine — not ours
        if " -c " in line:
            continue  # shell -c wrapper / harness command-echo, not the real supervisor
        n += 1
    return n


def list_auto_loop_issues() -> list[dict]:
    """[{number, kind:issue|pr, phase_label, human_label}]"""
    items: list[dict] = []
    for kind, gh_cmd in (("issue", "issue"), ("pr", "pr")):
        cmd = ["gh", gh_cmd, "list"]
        if GH_REPO_SLUG:
            cmd.extend(["--repo", GH_REPO_SLUG])
        cmd.extend([
            "--label", "auto-loop", "--state", "open",
            "--json", "number,labels", "--limit", "100",
        ])
        r = run(cmd)
        if r.returncode != 0:
            continue
        try:
            arr = json.loads(r.stdout)
        except Exception:
            continue
        for entry in arr:
            num = entry.get("number")
            labels = [l.get("name", "") for l in entry.get("labels", [])]
            phase = next((l for l in labels if l.startswith(("🔍", "🔧", "👀", "🛠️", "⚙️", "🚀", "✅", "🎉", "⏸️"))), "")
            human = next((l for l in labels if l.startswith(("🤖", "👤", "🆘"))), "")
            items.append({"number": num, "kind": kind, "phase": phase, "human": human})
    return items


def compute_expected(items: list[dict]) -> tuple[int, list[dict]]:
    """Return (total_expected, breakdown)."""
    # Refactor (iter3/skill-human-label-taxonomy):
    #   Old: four Human labels, including two 🆘 labels, scattered no-gap and
    #   escalation decisions across the codebase.
    #   New principle: exactly two active Human labels; causes move to the
    #   reason surface (#15 structural consensus).
    breakdown = []
    total = 0
    for it in items:
        if it["human"] == "👤 human:需-maintainer-决策":
            # Waiting for human intervention; no codex expected.
            continue
        n = PHASE_EXPECTED.get(it["phase"], 0)
        if n > 0:
            breakdown.append({"id": f"#{it['number']}", "kind": it["kind"], "phase": it["phase"], "expected": n})
            total += n
    return total, breakdown


def write_alert(msg: str, detail: dict) -> None:
    ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = utc_ts()
    line = f"[{ts}] {msg} | detail={json.dumps(detail, ensure_ascii=False)}"
    with ALERT_LOG.open("a") as f:
        f.write(line + "\n")
    # Notify the controller via the events log.
    with PENDING_EVENTS.open("a") as f:
        f.write(f"{ts} concurrency-alert {msg}\n")


def write_pending_event(event: str) -> None:
    PENDING_EVENTS.parent.mkdir(parents=True, exist_ok=True)
    with PENDING_EVENTS.open("a") as f:
        f.write(f"{utc_ts()} {event}\n")


# Refactor (iter5/cluster-issue66-skill-degradation):
# Old: no standalone watchdog, no DegradationCheck protocol, no plugin registry,
# and no GitHub auto-open path.
# New: runtime monitoring stays a concurrency_monitor throttled hook that calls
# the single-file static checker and emits local alerts only; it is not an
# independent watchdog.
def degradation_watch_interval_seconds() -> int:
    raw = os.environ.get("DEGRADATION_WATCH_INTERVAL_SECONDS", "0")
    try:
        interval = int(raw)
    except ValueError:
        interval = 1800
    return max(0, interval)


def degradation_watch_timeout_seconds() -> int:
    raw = os.environ.get("DEGRADATION_WATCH_TIMEOUT_SECONDS", "30")
    try:
        timeout = int(raw)
    except ValueError:
        timeout = 30
    return max(1, timeout)


def run_skill_degradation_check() -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(CHECK_SKILL_DEGRADATION),
            "--static",
            "--repo-root",
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=degradation_watch_timeout_seconds(),
    )


def write_degradation_alert(result: subprocess.CompletedProcess | None, error: str | None = None) -> None:
    DEGRADATION_ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
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
    with DEGRADATION_ALERT_LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{ts}] {summary} | detail={json.dumps(detail, ensure_ascii=False)}\n")
    write_pending_event(f"{summary} log=.refactor-loop/.degradation-alert.log")


def maybe_run_skill_degradation_watch(state: dict) -> None:
    interval = degradation_watch_interval_seconds()
    if interval <= 0:
        return
    now = int(time.time())
    last = int(state.get("last_degradation_watch_at", 0) or 0)
    if last and now - last < interval:
        return
    state["last_degradation_watch_at"] = now
    try:
        result = run_skill_degradation_check()
    except subprocess.TimeoutExpired as exc:
        write_degradation_alert(None, error=f"timeout after {exc.timeout}s")
        log(f"skill-degradation-alert timeout after {exc.timeout}s; see {DEGRADATION_ALERT_LOG}")
        return
    except Exception as exc:
        write_degradation_alert(None, error=repr(exc))
        log(f"skill-degradation-alert exception={exc!r}; see {DEGRADATION_ALERT_LOG}")
        return
    if result.returncode != 0:
        write_degradation_alert(result)
        log(f"skill-degradation-alert returncode={result.returncode}; see {DEGRADATION_ALERT_LOG}")


def dispatch_queue_files() -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for priority in PRIORITIES:
        priority_dir = DISPATCH_QUEUE / priority
        if not priority_dir.is_dir():
            continue
        files.extend((priority, path) for path in sorted(priority_dir.glob("*.dispatch.json")))
    return files


def dispatch_queue_empty() -> bool:
    return not dispatch_queue_files()


def next_dispatch_file() -> tuple[str, Path] | None:
    files = dispatch_queue_files()
    if not files:
        return None
    return files[0]


def archive_dispatched(path: Path, payload: dict, task_id: str) -> Path:
    DISPATCH_DISPATCHED.mkdir(parents=True, exist_ok=True)
    payload["dispatch_at"] = utc_ts()
    payload["source_dispatch_file"] = str(path)
    archive = DISPATCH_DISPATCHED / f"{task_id}.json"
    if archive.exists():
        stamp = payload["dispatch_at"].replace(":", "").replace("-", "")
        archive = DISPATCH_DISPATCHED / f"{task_id}-{stamp}.json"
    archive.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.unlink()
    return archive


def launch_dispatch(payload: dict) -> None:
    subprocess.Popen(
        [
            "nohup",
            str(SPAWN_CODEX),
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


# Refactor (iter4/concurrency-auto-topup):
#   Old pattern: controller-only dispatch; monitor could report deficits but did not consume queued work.
#   New principle: monitor may fire one queued dispatch from the narrow dispatch-queue allowlist and archive it.
def dispatch_one_from_queue() -> tuple[str, str, str] | None:
    next_file = next_dispatch_file()
    if next_file is None:
        return None
    priority, path = next_file
    payload = json.loads(path.read_text(encoding="utf-8"))
    task_id = str(payload.get("task_id") or path.name.removesuffix(".dispatch.json"))
    reason = str(payload.get("reason", ""))
    payload["task_id"] = task_id
    payload["priority"] = priority
    launch_dispatch(payload)
    archive_dispatched(path, payload, task_id)
    write_pending_event(f"DISPATCH_FIRED:{task_id}:{priority}:{reason}")
    log(f"DISPATCH_FIRED:{task_id}:{priority}:{reason}")
    return task_id, priority, reason


# Refactor (iter4/concurrency-auto-topup):
#   Old pattern: monitor only alerted; actual<floor waited for the LLM controller's next wakeup.
#   New principle: monitor automatically consumes dispatch-queue entries until the floor is satisfied or queue is empty.
def top_up_from_dispatch_queue(actual: int, floor: int) -> int:
    if actual >= floor:
        return actual
    max_dispatches = floor - actual
    for _ in range(max_dispatches):
        fired = dispatch_one_from_queue()
        if fired is None:
            break
        actual = count_in_flight_codex()
        if actual >= floor:
            break
    return actual


# Refactor (iter4/concurrency-auto-topup):
#   Old pattern: single no-gap sentinel path could alert and leave deficit repair to a later controller wakeup.
#   New principle: no-gap alerting continues into deficit detection so queued work can be fired in the same tick.
def tick() -> None:
    # Refactor (iter4/issue51-r3-consensus):
    #   Old pattern: no ambient visibility; maintainer had to run peek.sh manually.
    #   New principle: concurrency_monitor tick atomically writes
    #   statusline-snapshot.json, and statusline.sh reads it in < 200ms;
    #   no new daemon and no checked-in installer
    #   (per #51 r3 META_JUDGE_DONE:consensus:C-minimal-statusline-via-concurrency_monitor-snapshot).
    state = load_state()
    zero_streak = int(state.get("zero_streak", 0))
    maybe_run_skill_degradation_watch(state)

    items = list_auto_loop_issues()
    expected, breakdown = compute_expected(items)
    actual = count_in_flight_codex()
    open_pr_count = sum(1 for item in items if item["kind"] == "pr")
    open_issue_count = sum(1 for item in items if item["kind"] == "issue")
    floor = configured_floor()

    log(f"actual={actual} expected={expected} floor={floor} zero_streak={zero_streak}")

    # P0 no-gap rule: any active task with zero loop-owned codex alerts immediately.
    if expected > 0 and actual == 0:
        zero_streak += 1
        state["zero_streak"] = zero_streak
        p0_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        state["last_p0_at"] = p0_at
        detail = {
            "actual": 0,
            "expected": expected,
            "breakdown": breakdown,
            "zero_streak": zero_streak,
            "severity": "P0",
            "rule": "no-gap-violation",
        }
        write_alert(f"P0 no-gap-violation: 0 codex with {expected} active task(s)", detail)
        log(f"P0 ALERT: 0 codex but {expected} active task(s);streak={zero_streak};see {ALERT_LOG}")
        # P0 + queued dispatch: fire topup immediately in same tick (per #57 auto-topup contract)
        if not dispatch_queue_empty():
            actual = top_up_from_dispatch_queue(actual, max(expected, configured_floor()))
        write_statusline_snapshot(
            actual=actual,
            expected=expected,
            p0_streak=zero_streak,
            last_p0_at=p0_at,
            open_pr_count=open_pr_count,
            open_issue_count=open_issue_count,
        )
        save_state(state)
        return
    else:
        state["zero_streak"] = 0

    target = max(expected, floor)
    if actual < target:
        if dispatch_queue_empty():
            write_pending_event(f"CONCURRENCY_LOW:actual={actual} expected={expected} queue=0")
            log(f"CONCURRENCY_LOW:actual={actual} expected={expected} queue=0")
        else:
            actual = top_up_from_dispatch_queue(actual, target)

    write_statusline_snapshot(
        actual=actual,
        expected=expected,
        p0_streak=0,
        last_p0_at=state.get("last_p0_at"),
        open_pr_count=open_pr_count,
        open_issue_count=open_issue_count,
    )
    save_state(state)


# Refactor (iter4/skill-count-cli-canonical): Old pattern: daemon-only mode,
# with no one-shot CLI, so controllers that needed the current codex count had
# to run ps | grep manually and could drift from the count_in_flight_codex
# algorithm (per 2026-05-26 maintainer-directive). New principle: expose
# --count-only / --list-codex so any caller can reuse the canonical algorithm
# directly. The daemon main loop remains the default behavior.
def list_in_flight_codex_lines() -> list[str]:
    repo = str(REPO_ROOT)
    lines: list[str] = []
    for line in run(["ps", "-eo", "command="]).stdout.splitlines():
        if "spawn-codex.sh" not in line:
            continue
        if repo not in line:
            continue
        if " -c " in line:
            continue
        lines.append(line)
    return lines


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="concurrency monitor + canonical codex counter")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--count-only", action="store_true", help="print canonical in-flight codex count and exit")
    mode.add_argument("--list-codex", action="store_true", help="print one supervisor cmdline per line and exit")
    mode.add_argument("--once", action="store_true", help="run one tick and exit (no daemon loop)")
    args = parser.parse_args(argv)

    if args.count_only:
        print(count_in_flight_codex())
        return 0
    if args.list_codex:
        for line in list_in_flight_codex_lines():
            print(line)
        return 0
    if args.once:
        tick()
        return 0

    log(f"concurrency_monitor (Python) started: interval={INTERVAL}s")
    # Refactor (iter1/issue-143):
    #   Old pattern: restart wrapper sidecar refreshed heartbeat even if this loop hung.
    #   New principle: actor loop beats after tick/caught exception, then lease-sleeps.
    #   CLI one-shot/count/list modes do not enter the lease loop.
    lease = DaemonHeartbeatLease("concurrency_monitor", REPO_ROOT)
    while True:
        try:
            tick()
        except Exception as e:
            log(f"EXCEPTION in tick: {e!r}")
        lease.beat()
        lease.sleep_with_lease(INTERVAL)


if __name__ == "__main__":
    raise SystemExit(main())
