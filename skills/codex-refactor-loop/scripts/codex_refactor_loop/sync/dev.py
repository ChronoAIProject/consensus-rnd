"""Dev integration sync daemon.

"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..active_controller import require_active_controller, write_active_controller_status
from ..context import LoopContext, LoopContextError
from ..heartbeat import DaemonHeartbeatLease
from .executor import IntegrationSyncExecutor
from .operations import IntegrationSyncOperation, write_operation_artifact


DEFAULT_INTERVAL_SECONDS = 600
DEFAULT_RELEASE_ROLLUP_COOLDOWN_SECONDS = 21600
DEFAULT_RELEASE_ROLLUP_MIN_COMMITS = 1

if False:
    Refactor (iter373/issue-373)


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


def run(cmd: list[str], cwd: Path | None = None, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


def _required_branch_env(merged_env: dict[str, str]) -> tuple[str, str]:
    integration = str(merged_env.get("INTEGRATION_BRANCH", "")).strip()
    review_base = str(merged_env.get("REVIEW_BASE_BRANCH", "")).strip()
    missing = [name for name, value in (("INTEGRATION_BRANCH", integration), ("REVIEW_BASE_BRANCH", review_base)) if not value]
    if missing:
        raise LoopContextError(f"missing required host branch env: {', '.join(missing)}")
    return integration, review_base


def load_dev_sync_config(env: dict[str, str] | None = None, cwd: Path | str | None = None) -> "DevSyncConfig":
    source_env = dict(os.environ if env is None else env)
    ctx = LoopContext.load(env=source_env, cwd=cwd or os.getcwd())
    merged_env = {**source_env, **ctx.host_env}
    integration, review_base = _required_branch_env(merged_env)
    worktree = ctx.repo_root / ".worktrees" / "dev-sync"
    interval = int(source_env.get("INTERVAL", str(DEFAULT_INTERVAL_SECONDS)))
    release_rollup_min_commits = int(source_env.get("RELEASE_ROLLUP_MIN_COMMITS", str(DEFAULT_RELEASE_ROLLUP_MIN_COMMITS)))
    release_rollup_cooldown_seconds = int(
        source_env.get("RELEASE_ROLLUP_COOLDOWN_SECONDS", str(DEFAULT_RELEASE_ROLLUP_COOLDOWN_SECONDS))
    )
    return DevSyncConfig(
        context=ctx,
        interval=interval,
        worktree=worktree,
        integration=integration,
        review_base=review_base,
        release_rollup_min_commits=release_rollup_min_commits,
        release_rollup_cooldown_seconds=release_rollup_cooldown_seconds,
    )


@dataclass(frozen=True)
class DevSyncConfig:
    context: LoopContext
    interval: int
    worktree: Path
    integration: str
    review_base: str
    release_rollup_min_commits: int
    release_rollup_cooldown_seconds: int

    @property
    def main_repo(self) -> Path:
        return self.context.repo_root

    @property
    def skill_root(self) -> Path:
        return self.context.skill_root

    @property
    def spawn_codex(self) -> Path:
        return self.skill_root / "scripts" / "consensus-rnd-cli"

    @property
    def lock_file(self) -> Path:
        return self.main_repo / ".refactor-loop" / "dev-sync-daemon.lock"

    @property
    def pending_events_file(self) -> Path:
        return self.context.paths.pending_events


@contextmanager
def singleton_lock(lock_file: Path, logger: Callable[[str], None] = log):
    """Hold a non-blocking daemon singleton lock for this process lifetime."""
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger(f"FATAL: another dev_sync_daemon holds {lock_file}")
            sys.exit(2)
        fh.write(f"pid={os.getpid()}\n")
        fh.flush()
        yield


def ensure_worktree(
    *,
    worktree: Path,
    main_repo: Path,
    integration: str,
    command_runner=run,
    logger: Callable[[str], None] = log,
) -> bool:
    """Ensure the daemon's dedicated detached worktree exists."""
    if not worktree.exists():
        logger(f"creating worktree {worktree} (detached off origin/{integration})")
        command_runner(["git", "fetch", "origin", "--quiet"], cwd=main_repo)
        result = command_runner(
            ["git", "worktree", "add", "--detach", str(worktree), f"origin/{integration}"],
            cwd=main_repo,
        )
        if result.returncode != 0:
            logger(f"FATAL: git worktree add failed: {result.stderr.strip()}")
            return False
    return True


def remote_branch_exists(
    *,
    main_repo: Path,
    branch: str,
    command_runner=run,
    logger: Callable[[str], None] = log,
) -> bool:
    result = command_runner(["git", "ls-remote", "--exit-code", "--heads", "origin", branch], cwd=main_repo)
    if result.returncode != 0 or not result.stdout.strip():
        logger(f"ALERT: missing remote integration branch origin/{branch}")
        return False
    return True


def codex_resolve_in_flight(
    *,
    main_repo: Path,
    worktree: Path,
    command_runner=run,
) -> bool:
    """Return whether this repo already has a dev-sync resolver supervisor."""
    repo = str(main_repo)
    wt = str(worktree)
    for line in command_runner(["ps", "-eo", "command="]).stdout.splitlines():
        if "consensus-rnd-cli" not in line or "spawn-codex" not in line:
            continue
        if "dev-sync-codex-" not in line:
            continue
        if repo not in line and wt not in line:
            continue
        if " -c " in line:
            continue
        return True
    return False


def dispatch_codex_resolve(
    *,
    worktree: Path,
    main_repo: Path,
    integration: str,
    review_base: str,
    spawn_codex: Path,
    logger: Callable[[str], None] = log,
) -> None:
    """Spawn a codex to resolve the in-progress merge conflicts in worktree.

    """
    ctx = LoopContext.load(repo_root=main_repo)
    ts = int(time.time())
    prompt_file = main_repo / ".refactor-loop" / "prompts" / f"dev-sync-conflict-{ts}.md"
    log_file = main_repo / ".refactor-loop" / "logs" / f"dev-sync-codex-{ts}.log"
    worktree_display = ctx.durable_artifact_path(worktree)
    main_repo_display = "."
    prompt_file_display = ctx.durable_artifact_path(prompt_file)
    log_file_display = ctx.durable_artifact_path(log_file)
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_body = f"""# Task: resolve merge conflict on {integration} from origin/{review_base} sync

## Context

dev_sync_daemon (Python) detected a conflict in the dedicated worktree
`{worktree_display}`. The worker is launched with --cd already set to that
dedicated worktree. Resolve conflicts and stage files in that worktree, then emit the
marker. The daemon owns the later narrow #53 integration-branch apply after it
detects a resolved merge state.

Durable artifact paths in this prompt are repo-relative text:
- main repo: `{main_repo_display}`
- prompt artifact: `{prompt_file_display}`
- resolver log artifact: `{log_file_display}`

## Task

1. Confirm `pwd` is the dedicated worktree.
2. Run `git status` to find conflicted files.
3. Read each conflicted file and understand `origin/{review_base}` changes
   versus `{integration}` changes.
4. Merge both substantive sides: tests, new files, docs, and production code.
5. Run `git add <files>` for resolved files.
6. Run `bash -lc "$BUILD_CMD"` and fix build failures caused by the resolution.
7. Finish with marker `DEV_SYNC_RESOLVED:<files-resolved>` or
   `DEV_SYNC_BLOCKED:<reason>`.

## Hard Constraints

- No branch lifecycle apply, abort, or destructive checkout/reset. The daemon
  owns only the later typed #53 continuation when the merge is fully resolved.
- Do not delete substantive changes from either side: tests, production code,
  docs, or proto changes.
- Do not commit. Only resolve, stage, and emit the marker.
- Do not write new production code except what is needed for conflict
  resolution and build verification.
- Proto field conflicts with the same field number and different semantics
  must emit `DEV_SYNC_BLOCKED:proto-schema-conflict`.
- Do all work in the worktree and do not modify main repo `{main_repo_display}`.

Write the marker to stdout when done so the daemon can read it from the log:
- Success: `DEV_SYNC_RESOLVED:<file1>,<file2>,...`
- Blocked: `DEV_SYNC_BLOCKED:<reason>:<short>`

⟦AI:AUTO-LOOP⟧
"""
    prompt_file.write_text(prompt_body)
    logger(f"dispatching codex: prompt={prompt_file_display} log={log_file_display}")
    subprocess.Popen(
        [
            "nohup",
            str(spawn_codex),
            "spawn-codex",
            "--cd",
            str(worktree),
            "--add-dir",
            str(main_repo),
            "--prompt",
            str(prompt_file),
            "--log",
            str(log_file),
            "--stall",
            "5400",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def working_tree_dirty(cwd: Path, command_runner=run) -> bool:
    unstaged = command_runner(["git", "diff", "--quiet"], cwd=cwd)
    staged = command_runner(["git", "diff", "--cached", "--quiet"], cwd=cwd)
    return unstaged.returncode != 0 or staged.returncode != 0


def merge_in_progress(cwd: Path, command_runner=run) -> bool:
    """Detect an in-progress merge in normal checkouts and linked worktrees."""
    result = command_runner(["git", "-C", str(cwd), "rev-parse", "--git-path", "MERGE_HEAD"])
    return result.returncode == 0 and Path(result.stdout.strip()).exists()


@dataclass(frozen=True)
class RollupAdoption:
    pr_number: int | None
    old_head: str
    expected_remote_sha: str


@dataclass(frozen=True)
class RollupDetection:
    status: str
    adoption: RollupAdoption | None = None


class IntegrationSyncDaemon:
    """Narrow detector and executor for integration-branch sync transitions.

    """

    def __init__(
        self,
        *,
        context: LoopContext | None = None,
        worktree: Path,
        main_repo: Path,
        integration: str,
        review_base: str,
        pending_events_file: Path | None = None,
        spawn_codex: Path | None = None,
        command_runner=run,
        logger=log,
        ensure_worktree_fn: Callable[[], bool] | None = None,
        merge_detector: Callable[[Path], bool] | None = None,
        dirty_detector: Callable[[Path], bool] | None = None,
        resolver_in_flight: Callable[[], bool] | None = None,
        resolver_dispatcher: Callable[[], None] | None = None,
        release_rollup_min_commits: int = DEFAULT_RELEASE_ROLLUP_MIN_COMMITS,
        release_rollup_cooldown_seconds: int = DEFAULT_RELEASE_ROLLUP_COOLDOWN_SECONDS,
        now_provider: Callable[[], datetime] | None = None,
        executor: IntegrationSyncExecutor | None = None,
    ) -> None:
        self.context = context
        self.worktree = worktree
        self.main_repo = main_repo
        self.integration = integration
        self.review_base = review_base
        self.spawn_codex = spawn_codex
        self.run = command_runner
        self.log = logger
        self.ensure_worktree = ensure_worktree_fn or (lambda: ensure_worktree(
            worktree=self.worktree,
            main_repo=self.main_repo,
            integration=self.integration,
            command_runner=self.run,
            logger=self.log,
        ))
        self.merge_in_progress = merge_detector or (lambda cwd: merge_in_progress(cwd, self.run))
        self.working_tree_dirty = dirty_detector or (lambda cwd: working_tree_dirty(cwd, self.run))
        self.codex_resolve_in_flight = resolver_in_flight or (lambda: codex_resolve_in_flight(
            main_repo=self.main_repo,
            worktree=self.worktree,
            command_runner=self.run,
        ))
        self.dispatch_codex_resolve = resolver_dispatcher or (lambda: dispatch_codex_resolve(
            worktree=self.worktree,
            main_repo=self.main_repo,
            integration=self.integration,
            review_base=self.review_base,
            spawn_codex=self.spawn_codex or self.main_repo / "skills" / "codex-refactor-loop" / "scripts" / "consensus-rnd-cli",
            logger=self.log,
        ))
        self.pending_events_file = pending_events_file or main_repo / ".refactor-loop" / ".controller-pending-events.log"
        self.release_rollup_min_commits = release_rollup_min_commits
        self.release_rollup_cooldown_seconds = release_rollup_cooldown_seconds
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.executor = executor or IntegrationSyncExecutor()

    @classmethod
    def from_config(cls, config: DevSyncConfig, *, command_runner=run, logger=log) -> "IntegrationSyncDaemon":
        return cls(
            context=config.context,
            worktree=config.worktree,
            main_repo=config.main_repo,
            integration=config.integration,
            review_base=config.review_base,
            pending_events_file=config.pending_events_file,
            spawn_codex=config.spawn_codex,
            command_runner=command_runner,
            logger=logger,
            ensure_worktree_fn=lambda: ensure_worktree(
                worktree=config.worktree,
                main_repo=config.main_repo,
                integration=config.integration,
                command_runner=command_runner,
                logger=logger,
            ),
            resolver_in_flight=lambda: codex_resolve_in_flight(
                main_repo=config.main_repo,
                worktree=config.worktree,
                command_runner=command_runner,
            ),
            resolver_dispatcher=lambda: dispatch_codex_resolve(
                worktree=config.worktree,
                main_repo=config.main_repo,
                integration=config.integration,
                review_base=config.review_base,
                spawn_codex=config.spawn_codex,
                logger=logger,
            ),
            release_rollup_min_commits=config.release_rollup_min_commits,
            release_rollup_cooldown_seconds=config.release_rollup_cooldown_seconds,
        )

    def append_pending_event(self, reason: str, detail: str) -> None:
        self.pending_events_file.parent.mkdir(parents=True, exist_ok=True)
        with self.pending_events_file.open("a", encoding="utf-8") as fh:
            fh.write(f"DEV_SYNC_PENDING:{reason}:{detail}\n")

    def execute_sync_operation(self, operation: IntegrationSyncOperation) -> Path:
        path = write_operation_artifact(self.main_repo, operation)
        rel = path.relative_to(self.main_repo)
        self.log(f"executing IntegrationSyncOperation {rel.as_posix()}")
        executor = self.executor if self.executor.record_stem else IntegrationSyncExecutor(record_stem=path.stem)
        result = executor.execute(
            operation,
            repo=self.main_repo,
            worktree=self.worktree,
            env={
                "INTEGRATION_BRANCH": self.integration,
                "REVIEW_BASE_BRANCH": self.review_base,
            },
            command_runner=self.run,
        )
        self.log(f"integration sync {operation.kind}: {result.status}:{result.reason}")
        return path

    def local_ahead_count(self, cwd: Path) -> int:
        result = self.run(["git", "rev-list", "--count", f"origin/{self.integration}..HEAD"], cwd=cwd)
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
        result = self.run(["git", "rev-list", "--count", f"origin/{self.review_base}..origin/{self.integration}"], cwd=cwd)
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
                "--base",
                self.review_base,
                "--limit",
                "100",
                "--json",
                "number,headRefName,baseRefName,headRefOid",
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
        integration_sha = self.remote_branch_sha(self.worktree, self.integration)
        if not integration_sha:
            return True
        for row in rows:
            if not isinstance(row, dict):
                continue
            head = str(row.get("headRefName") or "")
            head_sha = str(row.get("headRefOid") or "")
            if head == self.integration or (head.startswith("rollup/") and head_sha == integration_sha):
                return True
        return False

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

    def execute_clean_local_ahead(self, cwd: Path) -> bool:
        ahead_n = self.local_ahead_count(cwd)
        if ahead_n <= 0:
            return False
        head = self.run(["git", "rev-parse", "HEAD"], cwd=cwd)
        expected = self.remote_integration_sha(cwd)
        if head.returncode != 0 or not expected:
            self.append_pending_event("local-ahead-operation-ambiguous", "missing-head-or-remote")
            return True
        self.execute_sync_operation(
            IntegrationSyncOperation(
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
                "--base",
                self.review_base,
                "--limit",
                "100",
                "--json",
                "number,headRefName,headRefOid,mergedAt",
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
        row = None
        for candidate in rows:
            if not isinstance(candidate, dict):
                continue
            head_name = str(candidate.get("headRefName") or "")
            head_oid = str(candidate.get("headRefOid") or "").strip()
            if head_name == self.integration or head_name.startswith("rollup/") or head_oid == expected_remote_sha:
                row = candidate
                break
        if row is None:
            return None
        old_head = (row.get("headRefOid") or "").strip()
        if not old_head:
            self.append_pending_event("rollup-adoption-ambiguous", "missing-headRefOid")
            return RollupDetection("ambiguous")
        ancestor = self.run(["git", "merge-base", "--is-ancestor", old_head, f"origin/{self.integration}"], cwd=cwd)
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

    def execute_merged_rollup_adoption(self, cwd: Path, adoption: RollupAdoption) -> bool:
        if not adoption.expected_remote_sha:
            self.append_pending_event("rollup-adoption-ambiguous", "missing-expected-remote-sha")
            return False

        replay_count = self.run(["git", "rev-list", "--count", f"{adoption.old_head}..origin/{self.integration}"], cwd=cwd)
        try:
            replay_n = int(replay_count.stdout.strip())
        except ValueError:
            self.append_pending_event("rollup-adoption-ambiguous", "post-rollup-count-unknown")
            return False

        head = self.run(["git", "rev-parse", "HEAD"], cwd=cwd)
        if head.returncode != 0:
            self.append_pending_event("rollup-adoption-ambiguous", "head-unknown")
            return False
        self.execute_sync_operation(
            IntegrationSyncOperation(
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

    def execute_forward_sync_review_base(self, cwd: Path) -> None:
        behind = self.run(["git", "rev-list", "--count", f"HEAD..origin/{self.review_base}"], cwd=cwd).stdout.strip()
        try:
            behind_n = int(behind)
        except ValueError:
            behind_n = 0

        if behind_n == 0:
            self.detect_release_rollup_needed(cwd)
            self.log(f"up-to-date with origin/{self.review_base}")
            return

        self.log(f"behind origin/{self.review_base} by {behind_n} commits, executing daemon operation")
        head = self.run(["git", "rev-parse", "HEAD"], cwd=cwd)
        expected = self.remote_integration_sha(cwd)
        if head.returncode != 0 or not expected:
            self.append_pending_event("forward-sync-operation-ambiguous", "missing-head-or-remote")
            return
        self.execute_sync_operation(
            IntegrationSyncOperation(
                kind="forward-sync-review-base",
                integration_branch=self.integration,
                review_base_branch=self.review_base,
                worktree_head=head.stdout.strip(),
                expected_remote_sha=expected,
                evidence={"behind_count": behind_n, "reason": "review-base-ahead-of-integration"},
            )
        )

    def execute_reset_to_remote(self, cwd: Path) -> bool:
        head = self.run(["git", "rev-parse", "HEAD"], cwd=cwd)
        expected = self.remote_integration_sha(cwd)
        if head.returncode != 0 or not expected:
            self.append_pending_event("reset-to-remote-ambiguous", "missing-head-or-remote")
            return True
        if head.stdout.strip() == expected:
            return False
        self.execute_sync_operation(
            IntegrationSyncOperation(
                kind="reset-to-remote",
                integration_branch=self.integration,
                review_base_branch=self.review_base,
                worktree_head=head.stdout.strip(),
                expected_remote_sha=expected,
                evidence={"reason": "local-head-diverged-from-integration"},
            )
        )
        return True

    def tick(self) -> None:
        if self.context is not None:
            decision = require_active_controller(self.context, "dev-sync")
            write_active_controller_status(self.context, decision)
            if not decision.allowed:
                self.log(f"active_controller=noop:not-owner action=dev-sync owner={decision.owner_device}")
                return
        cwd = self.worktree
        if not remote_branch_exists(
            main_repo=self.main_repo,
            branch=self.integration,
            command_runner=self.run,
            logger=self.log,
        ):
            self.append_pending_event("missing-integration-branch", self.integration)
            return
        if not cwd.exists():
            self.log(f"worktree {cwd} missing, attempting create")
            if not self.ensure_worktree():
                return

        self.run(["git", "fetch", "origin", "--quiet"], cwd=cwd)

        if self.merge_in_progress(cwd):
            if self.codex_resolve_in_flight():
                self.log("skip: merge in progress + codex resolving")
            else:
                unresolved = self.run(["git", "diff", "--name-only", "--diff-filter=U"], cwd=cwd)
                if unresolved.returncode == 0 and not unresolved.stdout.strip():
                    head = self.run(["git", "rev-parse", "HEAD"], cwd=cwd)
                    expected = self.remote_integration_sha(cwd)
                    if head.returncode != 0 or not expected:
                        self.append_pending_event("continue-merge-ambiguous", "missing-head-or-remote")
                    else:
                        self.execute_sync_operation(
                            IntegrationSyncOperation(
                                kind="continue-resolved-merge",
                                integration_branch=self.integration,
                                review_base_branch=self.review_base,
                                worktree_head=head.stdout.strip(),
                                expected_remote_sha=expected,
                                evidence={"reason": "resolved-merge-continuation"},
                            )
                        )
                else:
                    self.log("WARN: merge in progress but no codex running - dispatching")
                    self.dispatch_codex_resolve()
            return

        if self.working_tree_dirty(cwd):
            self.log("skip: worktree dirty (no merge in progress)")
            return

        if self.execute_clean_local_ahead(cwd):
            return

        rollup = self.detect_merged_rollup(cwd)
        if rollup and rollup.status == "adopt" and rollup.adoption:
            if self.execute_merged_rollup_adoption(cwd, rollup.adoption):
                return

        if self.execute_reset_to_remote(cwd):
            return

        self.execute_forward_sync_review_base(cwd)


def local_ahead_count(cwd: Path, config: DevSyncConfig | None = None) -> int:
    cfg = config or load_dev_sync_config()
    return IntegrationSyncDaemon.from_config(cfg).local_ahead_count(cwd)


def tick(config: DevSyncConfig | None = None) -> None:
    cfg = config or load_dev_sync_config()
    IntegrationSyncDaemon.from_config(cfg).tick()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run dev integration sync daemon")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--daemon", action="store_true", help="run persistently")
    mode.add_argument("--once", action="store_true", help="run one tick and exit")
    args = parser.parse_args(argv)
    try:
        config = load_dev_sync_config()
    except (LoopContextError, ValueError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    if args.once:
        IntegrationSyncDaemon.from_config(config).tick()
        return 0
    with singleton_lock(config.lock_file):
        log(
            "dev_sync_daemon (Python) started: "
            f"interval={config.interval}s worktree={config.worktree} "
            f"{config.review_base} -> {config.integration}"
        )
        if not ensure_worktree(
            worktree=config.worktree,
            main_repo=config.main_repo,
            integration=config.integration,
            command_runner=run,
            logger=log,
        ):
            log("FATAL: cannot ensure worktree, exiting")
            return 1
        lease = DaemonHeartbeatLease("dev_sync_daemon", config.main_repo)
        daemon = IntegrationSyncDaemon.from_config(config)
        while True:
            try:
                daemon.tick()
            except Exception as exc:
                log(f"EXCEPTION in tick: {exc!r}")
            lease.beat()
            lease.sleep_with_lease(config.interval)


__all__ = [
    "DevSyncConfig",
    "IntegrationSyncDaemon",
    "RollupAdoption",
    "RollupDetection",
    "codex_resolve_in_flight",
    "dispatch_codex_resolve",
    "ensure_worktree",
    "load_dev_sync_config",
    "local_ahead_count",
    "main",
    "merge_in_progress",
    "singleton_lock",
    "tick",
    "working_tree_dirty",
]
