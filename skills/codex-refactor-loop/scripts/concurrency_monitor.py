#!/usr/bin/env python3
"""
concurrency_monitor.py — 监控 codex 并发数,不足则告警 + 主动介入修复

。

设计:
- 周期 300s tick
- 从 GitHub 推算 期望并发数(每个 active phase issue/PR contribute 1)
  - 🔍 design-solving       → 1 solver-round OR 1 judge OR 1 reflector
  - 🔧 fixing               → 1 fix codex
  - 👀 reviewing            → 3 reviewer(每 r1 round)
  - 🛠️ implementing         → 1 implement codex
  - ⚙️ ci-running           → 0(等 CI,无需 codex)
  - 🚀 pr-open(待派 reviewer) → 0~3
- Compare with loop-owned spawn-codex wrapper processes.
- 不足(实际 < 期望 * 0.5)且持续 2 个 tick → 告警

主动介入修复:
1. 写告警到 `.refactor-loop/.concurrency-alert.log`(append + timestamp + 详情)
2. 写到 `.refactor-loop/.controller-pending-events.log`(controller 下次 wakeup 处理)
3. log 详细 expected breakdown(哪些 issue/PR 缺 codex)
4. 不自动 spawn codex(business logic 在 controller,不在 daemon)

启动:
  nohup python3 .claude/skills/codex-refactor-loop/scripts/concurrency_monitor.py \\
    >> .refactor-loop/logs/concurrency-monitor.log 2>&1 &
  disown

⟦AI:AUTO-LOOP⟧
"""

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


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


def github_repo_slug() -> str | None:
    slug = os.environ.get("GH_REPO_SLUG")
    if slug:
        return slug
    repo = os.environ.get("GH_REPO")
    if repo and "/" in repo:
        return repo
    owner = os.environ.get("GH_OWNER")
    name = os.environ.get("GH_REPO_NAME") or repo
    if owner and name:
        return f"{owner}/{name}"
    return None


INTERVAL = int(os.environ.get("INTERVAL", "60"))  # 
MIN_PARALLEL = int(os.environ.get("MIN_PARALLEL", "1"))  # 任何 active task 都至少要 1 codex
REPO_ROOT = git_repo_root()
GH_REPO_SLUG = github_repo_slug()
ALERT_LOG = REPO_ROOT / ".refactor-loop" / ".concurrency-alert.log"
PENDING_EVENTS = REPO_ROOT / ".refactor-loop" / ".controller-pending-events.log"

# phase label → 期望 codex 数(per active issue/PR)
PHASE_EXPECTED = {
    "🔍 phase:design-solving": 1,  # 至少 1 codex(solver round / judge / reflector)
    "🔧 phase:fixing": 1,
    "👀 phase:reviewing": 1,        # 至少 1 reviewer
    "🛠️ phase:implementing": 1,
    # 下列 phase 不期望 codex:
    "⚙️ phase:ci-running": 0,
    "🚀 phase:pr-open": 0,
    "✅ phase:consensus-reached": 0,
    "🎉 phase:merged": 0,
    "⏸️ phase:blocked": 0,
}

# consecutive low-tick counter persisted in state file
STATE_FILE = REPO_ROOT / ".refactor-loop" / ".concurrency-monitor-state.json"


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


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


def count_in_flight_codex() -> int:
    r = run(["ps", "-eo", "command="])
    n = 0
    for line in r.stdout.splitlines():
        if "spawn-codex.sh" not in line:
            continue
        if ".refactor-loop/logs/" in line or ".refactor-loop/prompts/" in line:
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
    breakdown = []
    total = 0
    for it in items:
        if it["human"] in ("👤 human:需-maintainer-决策", "🆘 human:卡死", "🆘 human:卡死-需-rework"):
            # 等人介入,不期望 codex
            continue
        n = PHASE_EXPECTED.get(it["phase"], 0)
        if n > 0:
            breakdown.append({"id": f"#{it['number']}", "kind": it["kind"], "phase": it["phase"], "expected": n})
            total += n
    return total, breakdown


def write_alert(msg: str, detail: dict) -> None:
    ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] {msg} | detail={json.dumps(detail, ensure_ascii=False)}"
    with ALERT_LOG.open("a") as f:
        f.write(line + "\n")
    # 通知 controller(events log)
    with PENDING_EVENTS.open("a") as f:
        f.write(f"{ts} concurrency-alert {msg}\n")


def tick() -> None:
    state = load_state()
    low_streak = int(state.get("low_streak", 0))
    zero_streak = int(state.get("zero_streak", 0))

    items = list_auto_loop_issues()
    expected, breakdown = compute_expected(items)
    actual = count_in_flight_codex()

    log(f"actual={actual} expected={expected} low_streak={low_streak} zero_streak={zero_streak}")

    # P0 no-gap rule: any active task with zero loop-owned codex alerts immediately.
    if expected > 0 and actual == 0:
        zero_streak += 1
        state["zero_streak"] = zero_streak
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
        save_state(state)
        return
    else:
        state["zero_streak"] = 0

    # 阈值:实际 < ceil(expected/2) 算 low
    threshold = max(1, (expected + 1) // 2) if expected > 0 else 0
    if expected == 0:
        # 无 active task,don't alert
        state["low_streak"] = 0
        save_state(state)
        return

    if actual < threshold:
        low_streak += 1
        state["low_streak"] = low_streak
        # >= 2 consecutive low tick → alert(避免 reporter 临时未识别 codex 误报)
        if low_streak >= 2:
            detail = {
                "actual": actual,
                "expected": expected,
                "threshold": threshold,
                "breakdown": breakdown,
                "low_streak": low_streak,
            }
            write_alert(f"codex-concurrency-low actual={actual} expected={expected}", detail)
            log(f"ALERT: actual={actual} < threshold={threshold} (streak={low_streak}); see {ALERT_LOG}")
    else:
        if low_streak > 0:
            log(f"recovered: actual={actual} >= threshold={threshold}")
        state["low_streak"] = 0
    save_state(state)


def main() -> None:
    log(f"concurrency_monitor (Python) started: interval={INTERVAL}s")
    while True:
        try:
            tick()
        except Exception as e:
            log(f"EXCEPTION in tick: {e!r}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
