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
  nohup bash -c 'source .refactor-loop/host.env && exec python3 <skill-root>/scripts/dev_sync_daemon.py' \
    >> .refactor-loop/logs/dev-sync-daemon.log 2>&1 &
  disown

⟦AI:AUTO-LOOP⟧
"""

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from contextlib import contextmanager
import fcntl
import json


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


def skill_root() -> Path:
    # Refactor (iter3/skill-skill-root-contract): Old pattern: .claude/skills hardcoded lookup. New principle: self-locate from this script path, with optional validated CODEX_REFACTOR_LOOP_SKILL_ROOT override.
    """Return this installed skill root, failing closed on invalid overrides."""
    override = os.environ.get("CODEX_REFACTOR_LOOP_SKILL_ROOT")
    root = Path(override).expanduser().resolve() if override else Path(__file__).resolve().parents[1]
    required = (
        root / "SKILL.md",
        root / "scripts" / "spawn-codex.sh",
        root / "prompts",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        source = "CODEX_REFACTOR_LOOP_SKILL_ROOT" if override else "__file__"
        raise RuntimeError(f"invalid codex-refactor-loop skill root from {source}: missing {', '.join(missing)}")
    return root


INTERVAL = int(os.environ.get("INTERVAL", "600"))
SKILL_ROOT = skill_root()
MAIN_REPO = git_repo_root()
WORKTREE = Path(os.environ.get("WORKTREE", f"{MAIN_REPO}-wt-dev-sync"))
INTEGRATION = os.environ.get("INTEGRATION_BRANCH") or os.environ.get("INTEGRATION") or "auto-refact-dev"
REVIEW_BASE = os.environ.get("REVIEW_BASE_BRANCH") or os.environ.get("REVIEW_BASE") or "dev"
SPAWN_CODEX = SKILL_ROOT / "scripts" / "spawn-codex.sh"
LOCK_FILE = MAIN_REPO / ".refactor-loop" / "dev-sync-daemon.lock"
PENDING_EVENTS_FILE = MAIN_REPO / ".refactor-loop" / ".controller-pending-events.log"
HEARTBEAT_FILE = MAIN_REPO / ".refactor-loop" / "heartbeats" / "dev_sync_daemon.py.ts"


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


def write_heartbeat() -> None:
    # Refactor (iter4/spawn-codex-pid-registry):
    #   Old pattern: controller health checked daemon names via process-table grep.
    #   New principle: daemon writes repo-local heartbeat timestamp; controller uses heartbeat-mtime <90s.
    HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT_FILE.write_text(f"{int(time.time())}\n", encoding="utf-8")


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
    """Return whether this repo already has a dev-sync resolver supervisor."""
    # Refactor (iter4/spawn-codex-pid-registry):
    #   Old pattern: process-table command parsing for dev-sync spawn-codex.sh supervisors.
    #   New principle: inspect this repo's .refactor-loop/spawned/dev-sync-codex-*.pid registry.
    spawned_dir = MAIN_REPO / ".refactor-loop" / "spawned"
    return spawned_dir.exists() and any(spawned_dir.glob("dev-sync-codex-*.pid"))


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
    """Detect an in-progress merge in normal checkouts and linked worktrees."""
    # Refactor (iter3/skill-hygiene-scripts):
    #   Old: checked cwd/.git/MERGE_HEAD, but linked worktrees store .git as a pointer file.
    #   New: ask git for the checkout-specific git-path for MERGE_HEAD/MERGE_MSG.
    #   This keeps conflicted syncs dispatching the resolver instead of looking merely dirty.
    for name in ("MERGE_HEAD", "MERGE_MSG"):
        r = run(["git", "-C", str(cwd), "rev-parse", "--git-path", name])
        if r.returncode == 0 and Path(r.stdout.strip()).exists():
            return True
    return False


@dataclass(frozen=True)
class RollupAdoption:
    pr_number: int | None
    old_head: str
    expected_remote_sha: str


@dataclass(frozen=True)
class RollupDetection:
    status: str
    adoption: RollupAdoption | None = None


# Refactor (iter4/skill-dev-sync-state-machine): Old pattern: 散落 active controller-owned sync recipe + 隐含 daemon transition. New principle: named IntegrationSyncDaemonV1 state machine boundary,resolver/rollup/push 全部 daemon-owned,controller 只 verify(#27 structural B 共识)
class IntegrationSyncDaemonV1:
    """Narrow state machine for integration-branch sync transitions."""

    def __init__(
        self,
        *,
        worktree: Path = WORKTREE,
        main_repo: Path = MAIN_REPO,
        integration: str = INTEGRATION,
        review_base: str = REVIEW_BASE,
        command_runner=run,
        logger=log,
        ensure_worktree_fn=ensure_worktree,
        merge_detector=merge_in_progress,
        dirty_detector=working_tree_dirty,
        resolver_in_flight=codex_resolve_in_flight,
        resolver_dispatcher=dispatch_codex_resolve,
    ) -> None:
        self.worktree = worktree
        self.main_repo = main_repo
        self.integration = integration
        self.review_base = review_base
        self.run = command_runner
        self.log = logger
        self.ensure_worktree = ensure_worktree_fn
        self.merge_in_progress = merge_detector
        self.working_tree_dirty = dirty_detector
        self.codex_resolve_in_flight = resolver_in_flight
        self.dispatch_codex_resolve = resolver_dispatcher
        self.pending_events_file = main_repo / ".refactor-loop" / ".controller-pending-events.log"

    def append_pending_event(self, reason: str, detail: str) -> None:
        self.pending_events_file.parent.mkdir(parents=True, exist_ok=True)
        with self.pending_events_file.open("a", encoding="utf-8") as fh:
            fh.write(f"DEV_SYNC_PENDING:{reason}:{detail}\n")

    def local_ahead_count(self, cwd: Path) -> int:
        result = self.run(
            ["git", "rev-list", "--count", f"origin/{self.integration}..HEAD"],
            cwd=cwd,
        )
        try:
            return int(result.stdout.strip())
        except ValueError:
            self.append_pending_event("local-ahead-unknown", result.stderr.strip()[:160])
            return 0

    def remote_integration_sha(self, cwd: Path) -> str | None:
        result = self.run(["git", "rev-parse", f"origin/{self.integration}"], cwd=cwd)
        if result.returncode != 0:
            self.append_pending_event("remote-sha-unknown", result.stderr.strip()[:160])
            return None
        return result.stdout.strip()

    def push_clean_local_ahead_before_reset(self, cwd: Path) -> bool:
        ahead_n = self.local_ahead_count(cwd)
        if ahead_n <= 0:
            return False
        self.log(f"local HEAD is ahead of origin/{self.integration} by {ahead_n} commits; pushing before reset")
        push = self.run(["git", "push", "origin", f"HEAD:{self.integration}"], cwd=cwd)
        if push.returncode == 0:
            self.log(f"pushed resolver/local-ahead HEAD → origin/{self.integration}")
        else:
            self.log(f"FAIL push local-ahead: {push.stderr.strip()[:120]}")
            self.append_pending_event("local-ahead-push-failed", push.stderr.strip()[:160])
        return True

    def detect_merged_rollup(self, cwd: Path) -> RollupDetection | None:
        already_adopted = self.run(
            ["git", "merge-base", "--is-ancestor", f"origin/{self.review_base}", f"origin/{self.integration}"],
            cwd=cwd,
        )
        if already_adopted.returncode == 0:
            return None

        expected_remote_sha = self.remote_integration_sha(cwd)
        if not expected_remote_sha:
            return RollupDetection("ambiguous")

        query = self.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "merged",
                "--head",
                self.integration,
                "--base",
                self.review_base,
                "--limit",
                "1",
                "--json",
                "number,headRefOid,mergedAt",
            ],
            cwd=self.main_repo,
        )
        if query.returncode != 0 or not query.stdout.strip():
            return None
        try:
            rows = json.loads(query.stdout)
        except json.JSONDecodeError:
            self.append_pending_event("rollup-adoption-ambiguous", "gh-json-decode-failed")
            return RollupDetection("ambiguous")
        if not rows:
            return None
        row = rows[0]
        old_head = (row.get("headRefOid") or "").strip()
        if not old_head:
            self.append_pending_event("rollup-adoption-ambiguous", "missing-headRefOid")
            return RollupDetection("ambiguous")
        ancestor = self.run(
            ["git", "merge-base", "--is-ancestor", old_head, f"origin/{self.integration}"],
            cwd=cwd,
        )
        if ancestor.returncode != 0:
            self.append_pending_event("rollup-adoption-ambiguous", f"old-head-not-ancestor:{old_head}")
            return RollupDetection("ambiguous")
        return RollupDetection(
            "adopt",
            RollupAdoption(
                pr_number=row.get("number"),
                old_head=old_head,
                expected_remote_sha=expected_remote_sha,
            ),
        )

    def adopt_merged_rollup(self, cwd: Path, adoption: RollupAdoption) -> bool:
        if not adoption.expected_remote_sha:
            self.append_pending_event("rollup-adoption-ambiguous", "missing-expected-remote-sha")
            return True

        replay_count = self.run(
            ["git", "rev-list", "--count", f"{adoption.old_head}..origin/{self.integration}"],
            cwd=cwd,
        )
        try:
            replay_n = int(replay_count.stdout.strip())
        except ValueError:
            self.append_pending_event("rollup-adoption-ambiguous", "post-rollup-count-unknown")
            return True

        if replay_n == 0:
            reset = self.run(["git", "reset", "--hard", f"origin/{self.review_base}"], cwd=cwd)
            if reset.returncode != 0:
                self.append_pending_event("rollup-adoption-reset-failed", reset.stderr.strip()[:160])
                return True
        else:
            reset = self.run(["git", "reset", "--hard", f"origin/{self.integration}"], cwd=cwd)
            if reset.returncode != 0:
                self.append_pending_event("rollup-adoption-reset-failed", reset.stderr.strip()[:160])
                return True
            rebase = self.run(
                ["git", "rebase", "--rebase-merges", "--onto", f"origin/{self.review_base}", adoption.old_head],
                cwd=cwd,
            )
            if rebase.returncode != 0:
                self.append_pending_event("rollup-adoption-replay-conflict", rebase.stderr.strip()[:160])
                return True

        push = self.run(
            [
                "git",
                "push",
                f"--force-with-lease=refs/heads/{self.integration}:{adoption.expected_remote_sha}",
                "origin",
                f"HEAD:{self.integration}",
            ],
            cwd=cwd,
        )
        if push.returncode == 0:
            self.log(f"adopted merged rollup PR #{adoption.pr_number or '?'} onto origin/{self.integration}")
        else:
            self.append_pending_event("rollup-adoption-push-failed", push.stderr.strip()[:160])
        return True

    def reset_to_remote(self, cwd: Path) -> bool:
        result = self.run(["git", "reset", "--hard", f"origin/{self.integration}"], cwd=cwd)
        if result.returncode != 0:
            self.log(f"FAIL reset to origin/{self.integration}: {result.stderr.strip()[:120]}")
            return False
        return True

    def forward_sync_review_base(self, cwd: Path) -> None:
        behind = self.run(["git", "rev-list", "--count", f"HEAD..origin/{self.review_base}"], cwd=cwd).stdout.strip()
        try:
            behind_n = int(behind)
        except ValueError:
            behind_n = 0

        if behind_n == 0:
            self.log(f"up-to-date with origin/{self.review_base}")
            return

        self.log(f"behind origin/{self.review_base} by {behind_n} commits, attempting sync")

        ff = self.run(["git", "merge", "--ff-only", f"origin/{self.review_base}"], cwd=cwd)
        if ff.returncode == 0 and ("Fast-forward" in ff.stdout or "Already up to date" in ff.stdout):
            self.log(f"ff-merged with origin/{self.review_base} (+{behind_n} commits)")
            push = self.run(["git", "push", "origin", f"HEAD:{self.integration}"], cwd=cwd)
            if push.returncode == 0:
                self.log(f"pushed HEAD → origin/{self.integration}")
            else:
                self.log(f"FAIL push: {push.stderr.strip()[:120]}")
            return

        self.log("ff-only not possible, attempting no-ff merge")
        merge = self.run(
            [
                "git",
                "merge",
                "--no-ff",
                "-m",
                f"Sync {self.integration} with {self.review_base} (auto by dev_sync_daemon)",
                f"origin/{self.review_base}",
            ],
            cwd=cwd,
        )
        if merge.returncode == 0:
            self.log(f"no-ff merge-committed +{behind_n} commits")
            push = self.run(["git", "push", "origin", f"HEAD:{self.integration}"], cwd=cwd)
            if push.returncode == 0:
                self.log(f"pushed HEAD → origin/{self.integration}")
            else:
                self.log(f"FAIL push after merge: {push.stderr.strip()[:120]}")
            return

        if self.merge_in_progress(cwd):
            self.log("CONFLICT detected (merge in progress)")
            if not self.codex_resolve_in_flight():
                self.dispatch_codex_resolve()
            else:
                self.log("codex already resolving, skip this tick")
        else:
            self.log(f"FAIL merge but no MERGE_HEAD: {merge.stderr.strip()[:120]}")

    def tick(self) -> None:
        cwd = self.worktree
        if not cwd.exists():
            self.log(f"worktree {cwd} missing, attempting create")
            if not self.ensure_worktree():
                return

        self.run(["git", "fetch", "origin", "--quiet"], cwd=cwd)

        if self.merge_in_progress(cwd):
            if self.codex_resolve_in_flight():
                self.log("skip: merge in progress + codex resolving")
            else:
                self.log("WARN: merge in progress but no codex running — dispatching")
                self.dispatch_codex_resolve()
            return

        if self.working_tree_dirty(cwd):
            self.log("skip: worktree dirty (no merge in progress)")
            return

        if self.push_clean_local_ahead_before_reset(cwd):
            return

        rollup = self.detect_merged_rollup(cwd)
        if rollup and rollup.status == "adopt" and rollup.adoption:
            self.adopt_merged_rollup(cwd, rollup.adoption)
            return
        if rollup and rollup.status == "ambiguous":
            return

        if not self.reset_to_remote(cwd):
            return

        self.forward_sync_review_base(cwd)


def local_ahead_count(cwd: Path) -> int:
    return IntegrationSyncDaemonV1().local_ahead_count(cwd)


def tick() -> None:
    IntegrationSyncDaemonV1().tick()


def main() -> None:
    log(f"dev_sync_daemon (Python) started: interval={INTERVAL}s worktree={WORKTREE} {REVIEW_BASE} → {INTEGRATION}")
    if not ensure_worktree():
        log("FATAL: cannot ensure worktree, exiting")
        sys.exit(1)
    while True:
        try:
            write_heartbeat()
            tick()
        except Exception as e:
            log(f"EXCEPTION in tick: {e!r}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    with singleton_lock():
        main()
