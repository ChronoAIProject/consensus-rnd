#!/usr/bin/env python3
# Refactor (iter4/skill-worktree-inside-repo): Old pattern: sibling `<repo>-wt-<name>/`. New principle: inside `<repo>/.worktrees/<name>/` + gitignored.
"""
dev_sync_daemon.py — detect integration sync needs and emit controller requests

daemon 跑在独立 worktree,main repo controller 工作不受 daemon 状态干扰。

设计:
- 独立 worktree:$REPO_ROOT/.worktrees/dev-sync(off auto-refact-dev)
- 600s 周期 check `git rev-list HEAD..origin/dev`
- behind>0:emit IntegrationSyncRequest for controller-owned apply
- conflict exists:spawn resolver worker; controller helper applies lifecycle after marker
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

from integration_sync_requests import IntegrationSyncRequest, write_request_artifact


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
WORKTREE = Path(os.environ.get("WORKTREE", str(MAIN_REPO / ".worktrees" / "dev-sync")))
INTEGRATION = os.environ.get("INTEGRATION_BRANCH") or os.environ.get("INTEGRATION") or "auto-refact-dev"
REVIEW_BASE = os.environ.get("REVIEW_BASE_BRANCH") or os.environ.get("REVIEW_BASE") or "dev"
SPAWN_CODEX = SKILL_ROOT / "scripts" / "spawn-codex.sh"
LOCK_FILE = MAIN_REPO / ".refactor-loop" / "dev-sync-daemon.lock"
PENDING_EVENTS_FILE = MAIN_REPO / ".refactor-loop" / ".controller-pending-events.log"
RELEASE_ROLLUP_MIN_COMMITS = int(os.environ.get("RELEASE_ROLLUP_MIN_COMMITS", "1"))
RELEASE_ROLLUP_COOLDOWN_SECONDS = int(os.environ.get("RELEASE_ROLLUP_COOLDOWN_SECONDS", "21600"))

# IntegrationSyncDaemon(per #53/#70) is a no-lifecycle detector/write-artifact
# runtime. It may read refs, compare ancestry, dispatch conflict resolution, write
# IntegrationSyncRequest artifacts, and append pending events. Controller-owned
# helpers hold the lifecycle apply boundary.


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
    Controller apply helpers later map detached HEAD back to the integration branch.
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


def codex_resolve_in_flight() -> bool:
    """Return whether this repo already has a dev-sync resolver supervisor."""
    # Refactor (iter3/skill-hygiene-scripts):
    #   Old: relative pgrep matched any host containing .refactor-loop/dev-sync-codex.
    #   New: inspect process commands and require this repo/worktree absolute scope.
    #   This mirrors count_in_flight_codex() so sibling host repos cannot suppress dispatch.
    repo = str(MAIN_REPO)
    worktree = str(WORKTREE)
    for line in run(["ps", "-eo", "command="]).stdout.splitlines():
        if "spawn-codex.sh" not in line:
            continue
        if "dev-sync-codex-" not in line:
            continue
        if repo not in line and worktree not in line:
            continue
        if " -c " in line:
            continue
        return True
    return False


def dispatch_codex_resolve() -> None:
    """Spawn a codex to resolve the in-progress merge conflicts in WORKTREE."""
    ts = int(time.time())
    prompt_file = MAIN_REPO / ".refactor-loop" / "prompts" / f"dev-sync-conflict-{ts}.md"
    log_file = MAIN_REPO / ".refactor-loop" / "logs" / f"dev-sync-codex-{ts}.log"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_body = f"""# 任务:resolve merge conflict on {INTEGRATION} ← origin/{REVIEW_BASE} sync

## Context

dev_sync_daemon (Python) 在独立 worktree `{WORKTREE}` 检测到冲突状态。你在该
worktree 内 resolve + `git add`,然后写 marker;controller apply helper 后续完成
fixed lifecycle apply。

## 任务

1. `cd {WORKTREE}`
2. `git status` 看 conflict 文件
3. 读每个冲突文件,理解 `origin/{REVIEW_BASE}` 改动 vs `{INTEGRATION}` 改动
4. 合并(保留两者实质改动:tests / new files / docs / production code)
5. `git add <files>`(已 resolve 的)
6. `$BUILD_CMD` 验证编译(失败修)
7. 完成 marker:`DEV_SYNC_RESOLVED:<files-resolved>` 或 `DEV_SYNC_BLOCKED:<reason>`

## 硬约束

- ❌ branch lifecycle apply / abort / destructive checkout reset(controller 控)
- ❌ 删任一边的实质改动(test / production code / docs / proto)
- ❌ 不 commit;只 resolve + stage + marker
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


# Refactor (iter4/skill-dev-sync-state-machine): Old pattern: 散落 active controller-owned sync recipe + 隐含 daemon transition. New principle: named IntegrationSyncDaemon state machine boundary.
# Refactor (iter5/issue70-structural-delete-controller-apply): Old pattern: daemon-owned lifecycle apply. New principle: daemon detects and emits IntegrationSyncRequest; controller owns apply.
# Refactor (iter5/issue107-python-identifier-rename): Old pattern: version suffix in daemon class/schema names (IntegrationSyncDaemonV1, IntegrationSyncRequestV1). New principle: naked responsibility names carry stable artifact intent; compatibility/version policy lives in contracts/tests, not identifier suffixes.
class IntegrationSyncDaemon:
    """Narrow detector for integration-branch sync transitions."""

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
        release_rollup_min_commits: int = RELEASE_ROLLUP_MIN_COMMITS,
        release_rollup_cooldown_seconds: int = RELEASE_ROLLUP_COOLDOWN_SECONDS,
        now_provider=None,
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
        self.release_rollup_min_commits = release_rollup_min_commits
        self.release_rollup_cooldown_seconds = release_rollup_cooldown_seconds
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def append_pending_event(self, reason: str, detail: str) -> None:
        self.pending_events_file.parent.mkdir(parents=True, exist_ok=True)
        with self.pending_events_file.open("a", encoding="utf-8") as fh:
            fh.write(f"DEV_SYNC_PENDING:{reason}:{detail}\n")

    def emit_sync_request(self, request: IntegrationSyncRequest) -> Path:
        path = write_request_artifact(self.main_repo, request)
        rel = path.relative_to(self.main_repo)
        self.pending_events_file.parent.mkdir(parents=True, exist_ok=True)
        with self.pending_events_file.open("a", encoding="utf-8") as fh:
            fh.write(f"DEV_SYNC_REQUEST:{rel.as_posix()}\n")
        self.log(f"emitted IntegrationSyncRequest {rel.as_posix()}")
        return path

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

    def remote_branch_sha(self, cwd: Path, branch: str) -> str | None:
        result = self.run(["git", "rev-parse", f"origin/{branch}"], cwd=cwd)
        if result.returncode != 0:
            self.log(f"skip release-rollup detector: origin/{branch} sha unavailable")
            return None
        return result.stdout.strip()

    def release_rollup_ahead_count(self, cwd: Path) -> int:
        result = self.run(
            ["git", "rev-list", "--count", f"origin/{self.review_base}..origin/{self.integration}"],
            cwd=cwd,
        )
        try:
            return int(result.stdout.strip())
        except ValueError:
            self.log("skip release-rollup detector: ahead count unavailable")
            return 0

    def has_open_release_rollup_pr(self) -> bool:
        result = self.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "open",
                "--head",
                self.integration,
                "--base",
                self.review_base,
                "--limit",
                "1",
                "--json",
                "number,headRefName,baseRefName",
            ],
            cwd=self.main_repo,
        )
        if result.returncode != 0:
            self.log("skip release-rollup detector: open PR query failed")
            return True
        try:
            rows = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            self.log("skip release-rollup detector: open PR query returned invalid JSON")
            return True
        return bool(rows)

    def release_rollup_event_recently_emitted(self, integration_sha: str, now: datetime) -> bool:
        if self.release_rollup_cooldown_seconds <= 0 or not self.pending_events_file.exists():
            return False
        for line in self.pending_events_file.read_text(encoding="utf-8", errors="replace").splitlines():
            prefix = "DEV_SYNC_PENDING:release-rollup-needed:"
            if not line.startswith(prefix):
                continue
            try:
                event = json.loads(line[len(prefix):])
            except json.JSONDecodeError:
                continue
            if event.get("integration_sha") != integration_sha:
                continue
            detected_at = event.get("detected_at")
            if not detected_at:
                return True
            try:
                prior = datetime.fromisoformat(detected_at.replace("Z", "+00:00"))
            except ValueError:
                return True
            if (now - prior).total_seconds() <= self.release_rollup_cooldown_seconds:
                return True
        return False

    # Refactor (iter5/issue-65-release-rollup-pending-event):
    #   Old pattern: no release-rollup detection when integration was ahead of the review base without an open PR.
    #   New principle: detect ahead + no open PR, then emit DEV_SYNC_PENDING:release-rollup-needed:<json>.
    def detect_release_rollup_needed(self, cwd: Path) -> bool:
        if self.release_rollup_min_commits <= 0:
            return False
        ahead_count = self.release_rollup_ahead_count(cwd)
        if ahead_count < self.release_rollup_min_commits:
            return False

        integration_sha = self.remote_branch_sha(cwd, self.integration)
        review_base_sha = self.remote_branch_sha(cwd, self.review_base)
        if not integration_sha or not review_base_sha:
            return False

        if self.has_open_release_rollup_pr():
            return False

        now = self.now_provider()
        if self.release_rollup_event_recently_emitted(integration_sha, now):
            self.log(f"release-rollup pending event already emitted for {integration_sha}")
            return False

        event = {
            "integration_branch": self.integration,
            "review_base_branch": self.review_base,
            "integration_sha": integration_sha,
            "review_base_sha": review_base_sha,
            "ahead_count": ahead_count,
            "detected_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "reason": "integration-ahead-review-base-without-open-rollup-pr",
        }
        self.append_pending_event("release-rollup-needed", json.dumps(event, sort_keys=True))
        return True

    def request_clean_local_ahead(self, cwd: Path) -> bool:
        ahead_n = self.local_ahead_count(cwd)
        if ahead_n <= 0:
            return False
        head = self.run(["git", "rev-parse", "HEAD"], cwd=cwd)
        expected = self.remote_integration_sha(cwd)
        if head.returncode != 0 or not expected:
            self.append_pending_event("local-ahead-request-ambiguous", "missing-head-or-remote")
            return True
        self.emit_sync_request(
            IntegrationSyncRequest(
                kind="push-local-ahead",
                integration_branch=self.integration,
                review_base_branch=self.review_base,
                worktree_head=head.stdout.strip(),
                expected_remote_sha=expected,
                evidence={"ahead_count": ahead_n, "reason": "local-head-ahead-of-integration"},
            )
        )
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

    def request_merged_rollup_adoption(self, cwd: Path, adoption: RollupAdoption) -> bool:
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

        head = self.run(["git", "rev-parse", "HEAD"], cwd=cwd)
        if head.returncode != 0:
            self.append_pending_event("rollup-adoption-ambiguous", "head-unknown")
            return True
        self.emit_sync_request(
            IntegrationSyncRequest(
                kind="adopt-merged-rollup",
                integration_branch=self.integration,
                review_base_branch=self.review_base,
                worktree_head=head.stdout.strip(),
                expected_remote_sha=adoption.expected_remote_sha,
                old_rollup_head=adoption.old_head,
                old_rollup_ahead_count=replay_n,
                pr_number=adoption.pr_number,
                evidence={"reason": "merged-rollup-adoption", "replay_count": replay_n},
            )
        )
        return True

    def forward_sync_review_base(self, cwd: Path) -> None:
        behind = self.run(["git", "rev-list", "--count", f"HEAD..origin/{self.review_base}"], cwd=cwd).stdout.strip()
        try:
            behind_n = int(behind)
        except ValueError:
            behind_n = 0

        if behind_n == 0:
            self.detect_release_rollup_needed(cwd)
            self.log(f"up-to-date with origin/{self.review_base}")
            return

        self.log(f"behind origin/{self.review_base} by {behind_n} commits, emitting controller request")
        head = self.run(["git", "rev-parse", "HEAD"], cwd=cwd)
        expected = self.remote_integration_sha(cwd)
        if head.returncode != 0 or not expected:
            self.append_pending_event("forward-sync-request-ambiguous", "missing-head-or-remote")
            return
        self.emit_sync_request(
            IntegrationSyncRequest(
                kind="forward-sync-review-base",
                integration_branch=self.integration,
                review_base_branch=self.review_base,
                worktree_head=head.stdout.strip(),
                expected_remote_sha=expected,
                evidence={"behind_count": behind_n, "reason": "review-base-ahead-of-integration"},
            )
        )

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

        if self.request_clean_local_ahead(cwd):
            return

        rollup = self.detect_merged_rollup(cwd)
        if rollup and rollup.status == "adopt" and rollup.adoption:
            self.request_merged_rollup_adoption(cwd, rollup.adoption)
            return
        if rollup and rollup.status == "ambiguous":
            return

        self.forward_sync_review_base(cwd)


def local_ahead_count(cwd: Path) -> int:
    return IntegrationSyncDaemon().local_ahead_count(cwd)


def tick() -> None:
    IntegrationSyncDaemon().tick()


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
