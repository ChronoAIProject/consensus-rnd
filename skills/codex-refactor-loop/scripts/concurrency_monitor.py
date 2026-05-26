#!/usr/bin/env python3
"""
concurrency_monitor.py — 监控 active work 是否出现 0 codex gap

。

设计:
- 周期 60s tick
- 从 GitHub 推算 期望并发数(每个 active phase issue/PR contribute 1)
  - 🔍 design-solving       → 1 solver-round OR 1 judge OR 1 reflector
  - 🔧 fixing               → 1 fix codex
  - 👀 reviewing            → 3 reviewer(每 r1 round)
  - 🛠️ implementing         → 1 implement codex
  - ⚙️ ci-running           → 0(等 CI,无需 codex)
  - 🚀 pr-open(待派 reviewer) → 0~3
- Compare with loop-owned spawn-codex registry files.
- 只监控 no-gap:P0 `expected > 0 and actual == 0` 立即告警

主动介入修复:
1. 写告警到 `.refactor-loop/.concurrency-alert.log`(append + timestamp + 详情)
2. 写到 `.refactor-loop/.controller-pending-events.log`(controller 下次 wakeup 处理)
3. log 详细 expected breakdown(哪些 issue/PR 缺 codex)
4. 不读取 floor 配置,不判断 floor deficit,不自动 spawn codex

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
REPO_ROOT = git_repo_root()
GH_REPO_SLUG = github_repo_slug()
ALERT_LOG = REPO_ROOT / ".refactor-loop" / ".concurrency-alert.log"
PENDING_EVENTS = REPO_ROOT / ".refactor-loop" / ".controller-pending-events.log"
SPAWNED_DIR = REPO_ROOT / ".refactor-loop" / "spawned"
HEARTBEAT_FILE = REPO_ROOT / ".refactor-loop" / "heartbeats" / "concurrency_monitor.py.ts"

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

# consecutive no-gap counter persisted in state file
STATE_FILE = REPO_ROOT / ".refactor-loop" / ".concurrency-monitor-state.json"


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


def write_heartbeat() -> None:
    # Refactor (iter4/spawn-codex-pid-registry):
    #   Old pattern: controller health checked daemon names via process-table grep.
    #   New principle: daemon writes repo-local heartbeat timestamp; controller uses heartbeat-mtime <90s.
    HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT_FILE.write_text(f"{int(time.time())}\n", encoding="utf-8")


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


def _read_pid_registry(path: Path) -> dict[str, str]:
    # Refactor (iter4/issue52-r1):
    #   Old pattern: monitor inferred spawned codex state from process-table text.
    #   New principle: parse the repo-local spawn-codex registry schema mechanically.
    entry: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            entry[key.strip()] = value.strip()
    return entry


def _pid_alive(pid_text: str | None) -> bool:
    # Refactor (iter4/issue52-r1):
    #   Old pattern: any matching command line could count as active work.
    #   New principle: a registry entry counts only while its recorded child PID is alive.
    if not pid_text:
        return False
    try:
        pid = int(pid_text)
    except ValueError:
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_command(pid_text: str | None) -> str:
    try:
        pid = int(pid_text or "")
    except ValueError:
        return ""
    if pid <= 0:
        return ""
    r = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    if r.returncode != 0:
        return ""
    return r.stdout.strip()


def _pid_looks_like_codex(pid_text: str | None) -> bool:
    # Refactor (iter4/issue52-r4):
    #   Old pattern: any live PID with a repo-local log could count as loop work.
    #   New principle: registry entries count only when the live PID command still looks like codex.
    command = _pid_command(pid_text)
    if not command:
        return False
    return "codex" in command.lower()


def _is_repo_log(log_text: str | None) -> bool:
    # Refactor (iter4/issue52-r1):
    #   Old pattern: repo scoping came from grep text that could cross-match other worktrees.
    #   New principle: validate the registry log path resolves under this REPO_ROOT.
    if not log_text:
        return False
    try:
        log_path = Path(log_text).expanduser()
        if not log_path.is_absolute():
            log_path = (REPO_ROOT / log_path).resolve()
        else:
            log_path = log_path.resolve()
        repo_root = REPO_ROOT.resolve()
        log_path.relative_to(repo_root)
    except (OSError, ValueError):
        return False
    return True


def _registry_entry_alive(path: Path) -> bool:
    # Refactor (iter4/issue52-r1):
    #   Old pattern: spawned state had no single trust boundary for repo/log/pid checks.
    #   New principle: central helper accepts only live, repo-local, in-repo-log registry entries.
    try:
        entry = _read_pid_registry(path)
    except OSError:
        return False
    repo_text = entry.get("repo_root")
    if repo_text:
        try:
            if Path(repo_text).expanduser().resolve() != REPO_ROOT.resolve():
                return False
        except OSError:
            return False
    pid_text = entry.get("pid")
    return _pid_alive(pid_text) and _pid_looks_like_codex(pid_text) and _is_repo_log(entry.get("log"))


def count_in_flight_codex() -> int:
    """Count THIS repo's in-flight loop codex from the local spawn registry."""
    # Refactor (iter4/issue52-r1):
    #   Old pattern: monitor 用 ps grep | grep REPO_ROOT 计 in-flight codex(false-positive 跨 host project)
    #   New principle: spawn-codex.sh 自维护 PID registry,monitor / router 读 .refactor-loop/spawned/*.pid 校验,本仓库脚本闭环
    if not SPAWNED_DIR.exists():
        return 0
    return sum(1 for path in SPAWNED_DIR.glob("*.pid") if _registry_entry_alive(path))


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
    #   Old: 四个 Human label(含两个 🆘),no-gap/escalation 判定散落
    #   New principle: 恰好两个 active Human label;causes 移到 reason surface(#15 structural 共识)
    breakdown = []
    total = 0
    for it in items:
        if it["human"] == "👤 human:需-maintainer-决策":
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
    # Refactor (iter3/skill-concurrency-floor-enforcement):
    #   Old pattern: concurrency_monitor 有误导性 low-threshold 路径,CODEX_FLOOR 强制职责不清
    #   New principle: monitor 保持 no-gap-only;删 stale low-threshold 路径;CODEX_FLOOR 补给仅 controller wakeup step 1.5;SKILL 澄清职责(#14 delete 共识)
    state = load_state()
    zero_streak = int(state.get("zero_streak", 0))

    items = list_auto_loop_issues()
    expected, breakdown = compute_expected(items)
    actual = count_in_flight_codex()

    log(f"actual={actual} expected={expected} zero_streak={zero_streak}")

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

    save_state(state)


def main() -> None:
    log(f"concurrency_monitor (Python) started: interval={INTERVAL}s")
    while True:
        try:
            write_heartbeat()
            tick()
        except Exception as e:
            log(f"EXCEPTION in tick: {e!r}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
