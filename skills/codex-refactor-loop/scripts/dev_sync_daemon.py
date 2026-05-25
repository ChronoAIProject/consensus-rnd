#!/usr/bin/env python3
"""
dev_sync_daemon.py — 自动 merge origin/dev → auto-refact-dev 的 daemon

daemon 跑在独立 worktree,main repo controller 工作不受 daemon 的 merge 状态干扰。

设计:
- 独立 worktree:$REPO_ROOT-wt-dev-sync(off auto-refact-dev)
- 600s 周期 check `git rev-list HEAD..origin/dev`
- behind>0:try ff-only → no-ff merge → 冲突 spawn-codex resolve
- 成功 → push origin auto-refact-dev → main repo controller 下次 fetch 拉到
- main repo working tree 不被 merge 状态污染

启动:
  nohup python3 .claude/skills/codex-refactor-loop/scripts/dev_sync_daemon.py \
    >> .refactor-loop/logs/dev-sync-daemon.log 2>&1 &
  disown

⟦AI:AUTO-LOOP⟧
"""

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager
import fcntl


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


INTERVAL = int(os.environ.get("INTERVAL", "600"))
MAIN_REPO = git_repo_root()
WORKTREE = Path(os.environ.get("WORKTREE", f"{MAIN_REPO}-wt-dev-sync"))
INTEGRATION = os.environ.get("INTEGRATION_BRANCH") or os.environ.get("INTEGRATION") or "auto-refact-dev"
REVIEW_BASE = os.environ.get("REVIEW_BASE_BRANCH") or os.environ.get("REVIEW_BASE") or "develop"
SPAWN_CODEX = MAIN_REPO / ".claude" / "skills" / "codex-refactor-loop" / "scripts" / "spawn-codex.sh"
LOCK_FILE = MAIN_REPO / ".refactor-loop" / "dev-sync-daemon.lock"


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


def run(cmd: list[str], cwd: Path | None = None, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


@contextmanager
def singleton_lock():
    """Hold a non-blocking daemon singleton lock for this process lifetime."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log(f"FATAL: another dev_sync_daemon holds {LOCK_FILE}")
            sys.exit(2)
        fh.write(f"pid={os.getpid()}\n")
        fh.flush()
        yield


def ensure_worktree() -> bool:
    """Ensure the daemon's dedicated worktree exists (detached HEAD off INTEGRATION).

    git 不允许两个 worktree checkout 同 branch,所以 daemon 用 detached HEAD。
    Push 时 `git push origin HEAD:INTEGRATION` 显式映射回 branch。
    """
    if not WORKTREE.exists():
        log(f"creating worktree {WORKTREE} (detached off origin/{INTEGRATION})")
        # fetch 先,确保 origin/INTEGRATION 是最新
        run(["git", "fetch", "origin", "--quiet"], cwd=MAIN_REPO)
        r = run(["git", "worktree", "add", "--detach", str(WORKTREE),
                 f"origin/{INTEGRATION}"], cwd=MAIN_REPO)
        if r.returncode != 0:
            log(f"FATAL: git worktree add failed: {r.stderr.strip()}")
            return False
    return True


def reset_to_remote(cwd: Path) -> bool:
    """每 tick 开始 reset 到 origin/INTEGRATION,确保 base 最新。"""
    run(["git", "fetch", "origin", "--quiet"], cwd=cwd)
    r = run(["git", "reset", "--hard", f"origin/{INTEGRATION}"], cwd=cwd)
    if r.returncode != 0:
        log(f"FAIL reset to origin/{INTEGRATION}: {r.stderr.strip()[:120]}")
        return False
    return True


def codex_resolve_in_flight() -> bool:
    r = run(["pgrep", "-f", r"\.refactor-loop/(logs|prompts)/dev-sync-codex-"])
    return r.returncode == 0


def dispatch_codex_resolve() -> None:
    """Spawn a codex to resolve the in-progress merge conflicts in WORKTREE."""
    ts = int(time.time())
    prompt_file = MAIN_REPO / ".refactor-loop" / "prompts" / f"dev-sync-conflict-{ts}.md"
    log_file = MAIN_REPO / ".refactor-loop" / "logs" / f"dev-sync-codex-{ts}.log"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_body = f"""# 任务:resolve merge conflict on {INTEGRATION} ← origin/{REVIEW_BASE} sync

## Context

dev_sync_daemon (Python) 在独立 worktree `{WORKTREE}` 做 merge,遇到冲突。你
在该 worktree 内 resolve + `git add` + `git merge --continue`,**不 push**
(daemon 后续 push)。

## 任务

1. `cd {WORKTREE}`
2. `git status` 看 conflict 文件
3. 读每个冲突文件,理解 `origin/{REVIEW_BASE}` 改动 vs `{INTEGRATION}` 改动
4. 合并(保留两者实质改动:tests / new files / docs / production code)
5. `git add <files>`(已 resolve 的)
6. `git merge --continue`(default merge message,带 `Sync {INTEGRATION} with {REVIEW_BASE}`)
7. `$BUILD_CMD` 验证编译(失败修)
8. 完成 marker:`DEV_SYNC_RESOLVED:<files-resolved>` 或 `DEV_SYNC_BLOCKED:<reason>`

## 硬约束

- ❌ `git push` / `git merge --abort` / `git reset --hard`(daemon 控)
- ❌ 删任一边的实质改动(test / production code / docs / proto)
- ❌ 不 commit before `git merge --continue`(merge 自动 commit)
- ❌ 写新产线代码(只 resolve + build verify)
- proto 字段冲突(同字段号 不同语义)→ `DEV_SYNC_BLOCKED:proto-schema-conflict`
- 整个工作在 worktree 内完成,不动 main repo `{MAIN_REPO}`

完成时把 marker 写 stdout(daemon 读 log):
- 成功:`DEV_SYNC_RESOLVED:<file1>,<file2>,...`
- 阻塞:`DEV_SYNC_BLOCKED:<reason>:<short>`

⟦AI:AUTO-LOOP⟧
"""
    prompt_file.write_text(prompt_body)
    log(f"dispatching codex: prompt={prompt_file} log={log_file}")
    # spawn-codex.sh 在 main repo,但 --cd 指 worktree
    subprocess.Popen(
        ["nohup", str(SPAWN_CODEX),
         "--cd", str(WORKTREE),
         "--add-dir", str(MAIN_REPO),
         "--prompt", str(prompt_file),
         "--log", str(log_file),
         "--stall", "5400"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def working_tree_dirty(cwd: Path) -> bool:
    r1 = run(["git", "diff", "--quiet"], cwd=cwd)
    r2 = run(["git", "diff", "--cached", "--quiet"], cwd=cwd)
    return r1.returncode != 0 or r2.returncode != 0


def merge_in_progress(cwd: Path) -> bool:
    return (cwd / ".git" / "MERGE_HEAD").exists() or (cwd / ".git" / "MERGE_MSG").exists()


def tick() -> None:
    cwd = WORKTREE
    if not cwd.exists():
        log(f"worktree {cwd} missing, attempting create")
        if not ensure_worktree():
            return

    if merge_in_progress(cwd):
        if codex_resolve_in_flight():
            log("skip: merge in progress + codex resolving")
        else:
            log("WARN: merge in progress but no codex running — dispatching")
            dispatch_codex_resolve()
        return

    if working_tree_dirty(cwd):
        log("skip: worktree dirty (no merge in progress)")
        return

    # 每 tick reset 到 origin/INTEGRATION 确保最新(detached HEAD)
    if not reset_to_remote(cwd):
        return

    behind = run(["git", "rev-list", "--count", f"HEAD..origin/{REVIEW_BASE}"], cwd=cwd).stdout.strip()
    try:
        behind_n = int(behind)
    except ValueError:
        behind_n = 0

    if behind_n == 0:
        log(f"up-to-date with origin/{REVIEW_BASE}")
        return

    log(f"behind origin/{REVIEW_BASE} by {behind_n} commits, attempting sync")

    # 尝试 ff-only(detached HEAD push 用 HEAD:branch 映射)
    r = run(["git", "merge", "--ff-only", f"origin/{REVIEW_BASE}"], cwd=cwd)
    if r.returncode == 0 and ("Fast-forward" in r.stdout or "Already up to date" in r.stdout):
        log(f"ff-merged with origin/{REVIEW_BASE} (+{behind_n} commits)")
        push = run(["git", "push", "origin", f"HEAD:{INTEGRATION}"], cwd=cwd)
        if push.returncode == 0:
            log(f"pushed HEAD → origin/{INTEGRATION}")
        else:
            log(f"FAIL push: {push.stderr.strip()[:120]}")
        return

    # ff-only 失败 → no-ff merge
    log("ff-only not possible, attempting no-ff merge")
    r = run(["git", "merge", "--no-ff", "-m",
             f"Sync {INTEGRATION} with {REVIEW_BASE} (auto by dev_sync_daemon)",
             f"origin/{REVIEW_BASE}"], cwd=cwd)
    if r.returncode == 0:
        log(f"no-ff merge-committed +{behind_n} commits")
        push = run(["git", "push", "origin", f"HEAD:{INTEGRATION}"], cwd=cwd)
        if push.returncode == 0:
            log(f"pushed HEAD → origin/{INTEGRATION}")
        else:
            log(f"FAIL push after merge: {push.stderr.strip()[:120]}")
        return

    # 冲突
    if merge_in_progress(cwd):
        log("CONFLICT detected (merge in progress)")
        if not codex_resolve_in_flight():
            dispatch_codex_resolve()
        else:
            log("codex already resolving, skip this tick")
    else:
        log(f"FAIL merge but no MERGE_HEAD: {r.stderr.strip()[:120]}")


def main() -> None:
    log(f"dev_sync_daemon (Python) started: interval={INTERVAL}s worktree={WORKTREE} {REVIEW_BASE} → {INTEGRATION}")
    if not ensure_worktree():
        log("FATAL: cannot ensure worktree, exiting")
        sys.exit(1)
    while True:
        try:
            tick()
        except Exception as e:
            log(f"EXCEPTION in tick: {e!r}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    with singleton_lock():
        main()
