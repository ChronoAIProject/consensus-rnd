"""Daemon-owned executor for typed IntegrationSyncOperation artifacts.

"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ..context import LoopContext
from .operations import IntegrationSyncOperation, IntegrationSyncOperationError


DEFAULT_INTEGRATION_BRANCH = "auto-refact-dev"
DEFAULT_REVIEW_BASE_BRANCH = "dev"


@dataclass(frozen=True)
class SyncExecutionResult:
    status: str
    reason: str
    record_path: Path

    @property
    def ok(self) -> bool:
        return self.status == "applied"


def repo_root(env: dict[str, str] | None = None, cwd: Path | str | None = None) -> Path:
    source_env = dict(os.environ if env is None else env)
    return LoopContext.load(env=source_env, cwd=cwd or os.getcwd()).repo_root


def run(cmd: list[str], cwd: Path, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


class IntegrationSyncExecutor:
    """Execute one typed integration sync operation with live-state rechecks."""

    def __init__(self, *, record_stem: str | None = None) -> None:
        self.record_stem = record_stem

    def _record(self, repo: Path, operation: IntegrationSyncOperation, status: str, reason: str) -> Path:
        out_dir = repo / ".refactor-loop" / "runs" / "integration-sync-executions"
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = self.record_stem or f"integration-sync-operation-{operation.kind}"
        out = out_dir / f"{stem}.{status}.json"
        out.write_text(
            json.dumps(
                {
                    "operation_kind": operation.kind,
                    "status": status,
                    "reason": reason,
                    "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return out

    def _applied_record(self, repo: Path, operation: IntegrationSyncOperation) -> Path:
        out_dir = repo / ".refactor-loop" / "runs" / "integration-sync-executions"
        stem = self.record_stem or f"integration-sync-operation-{operation.kind}"
        return out_dir / f"{stem}.applied.json"

    def _reject(self, repo: Path, operation: IntegrationSyncOperation, reason: str) -> SyncExecutionResult:
        return SyncExecutionResult("rejected", reason, self._record(repo, operation, "rejected", reason))

    def _ensure_clean_or_merge(
        self,
        worktree: Path,
        *,
        command_runner=None,
    ) -> tuple[bool, bool]:
        command_runner = command_runner or run
        merge_head = command_runner(["git", "rev-parse", "--git-path", "MERGE_HEAD"], worktree)
        merge_in_progress = merge_head.returncode == 0 and Path(merge_head.stdout.strip()).exists()
        if merge_in_progress:
            return True, True
        unstaged = command_runner(["git", "diff", "--quiet"], worktree)
        staged = command_runner(["git", "diff", "--cached", "--quiet"], worktree)
        return unstaged.returncode == 0 and staged.returncode == 0, False

    def _expected_branches(self, env: dict[str, str] | None = None) -> tuple[str, str]:
        source_env = os.environ if env is None else env
        expected_integration = source_env.get("INTEGRATION_BRANCH") or DEFAULT_INTEGRATION_BRANCH
        expected_review_base = source_env.get("REVIEW_BASE_BRANCH") or DEFAULT_REVIEW_BASE_BRANCH
        return expected_integration, expected_review_base

    def _validate_common(
        self,
        repo: Path,
        worktree: Path,
        operation: IntegrationSyncOperation,
        *,
        env: dict[str, str] | None = None,
        command_runner=None,
    ) -> None:
        command_runner = command_runner or run
        if self._applied_record(repo, operation).exists():
            raise IntegrationSyncOperationError("already-executed")
        expected_integration, expected_review_base = self._expected_branches(env)
        if operation.integration_branch != expected_integration:
            raise IntegrationSyncOperationError("branch mismatch: integration")
        if operation.review_base_branch != expected_review_base:
            raise IntegrationSyncOperationError("branch mismatch: review_base")
        command_runner(["git", "fetch", "origin", "--quiet"], worktree)
        remote = command_runner(["git", "rev-parse", f"origin/{operation.integration_branch}"], worktree)
        if remote.returncode != 0 or remote.stdout.strip() != operation.expected_remote_sha:
            raise IntegrationSyncOperationError("stale expected_remote_sha")
        head = command_runner(["git", "rev-parse", "HEAD"], worktree)
        if head.returncode != 0 or head.stdout.strip() != operation.worktree_head:
            raise IntegrationSyncOperationError("stale worktree_head")

    def _execute_push_local_ahead(
        self,
        operation: IntegrationSyncOperation,
        worktree: Path,
        *,
        command_runner=None,
    ) -> subprocess.CompletedProcess[str]:
        command_runner = command_runner or run
        count = command_runner(["git", "rev-list", "--count", f"origin/{operation.integration_branch}..HEAD"], worktree)
        if count.returncode != 0 or int((count.stdout or "0").strip() or "0") <= 0:
            raise IntegrationSyncOperationError("no local ahead commits")
        return command_runner(["git", "push", "origin", f"HEAD:{operation.integration_branch}"], worktree)

    def _execute_reset_to_remote(
        self,
        operation: IntegrationSyncOperation,
        worktree: Path,
        *,
        command_runner=None,
    ) -> subprocess.CompletedProcess[str]:
        command_runner = command_runner or run
        return command_runner(["git", "reset", "--hard", f"origin/{operation.integration_branch}"], worktree)

    def _execute_continue_resolved_merge(
        self,
        operation: IntegrationSyncOperation,
        worktree: Path,
        *,
        merge_in_progress: bool,
        command_runner=None,
    ) -> subprocess.CompletedProcess[str]:
        command_runner = command_runner or run
        if not merge_in_progress:
            raise IntegrationSyncOperationError("no merge in progress")
        unresolved = command_runner(["git", "diff", "--name-only", "--diff-filter=U"], worktree)
        if unresolved.returncode != 0 or unresolved.stdout.strip():
            raise IntegrationSyncOperationError("merge has unresolved paths")
        result = command_runner(["git", "merge", "--continue"], worktree)
        if result.returncode == 0:
            result = command_runner(["git", "push", "origin", f"HEAD:{operation.integration_branch}"], worktree)
        return result

    def _execute_forward_sync_review_base(
        self,
        operation: IntegrationSyncOperation,
        worktree: Path,
        *,
        command_runner=None,
    ) -> subprocess.CompletedProcess[str]:
        command_runner = command_runner or run
        ff = command_runner(["git", "merge", "--ff-only", f"origin/{operation.review_base_branch}"], worktree)
        if ff.returncode == 0:
            return command_runner(["git", "push", "origin", f"HEAD:{operation.integration_branch}"], worktree)
        result = command_runner(
            [
                "git",
                "merge",
                "--no-ff",
                "-m",
                f"Sync {operation.integration_branch} with {operation.review_base_branch} (daemon apply)",
                f"origin/{operation.review_base_branch}",
            ],
            worktree,
        )
        if result.returncode == 0:
            result = command_runner(["git", "push", "origin", f"HEAD:{operation.integration_branch}"], worktree)
        return result

    def _execute_adopt_merged_rollup(
        self,
        operation: IntegrationSyncOperation,
        worktree: Path,
        *,
        command_runner=None,
    ) -> subprocess.CompletedProcess[str]:
        command_runner = command_runner or run
        assert operation.old_rollup_head is not None
        ancestor = command_runner(
            ["git", "merge-base", "--is-ancestor", operation.old_rollup_head, f"origin/{operation.integration_branch}"],
            worktree,
        )
        if ancestor.returncode != 0:
            raise IntegrationSyncOperationError("invalid rollup ancestry")
        replay = command_runner(
            ["git", "rev-list", "--count", f"{operation.old_rollup_head}..origin/{operation.integration_branch}"],
            worktree,
        )
        replay_n = int((replay.stdout or "0").strip() or "0")
        if operation.old_rollup_ahead_count is not None and replay_n != operation.old_rollup_ahead_count:
            raise IntegrationSyncOperationError("stale rollup ahead count")
        if replay_n == 0:
            result = command_runner(["git", "reset", "--hard", f"origin/{operation.review_base_branch}"], worktree)
        else:
            result = command_runner(["git", "reset", "--hard", f"origin/{operation.integration_branch}"], worktree)
            if result.returncode == 0:
                result = command_runner(
                    [
                        "git",
                        "rebase",
                        "--rebase-merges",
                        "--onto",
                        f"origin/{operation.review_base_branch}",
                        operation.old_rollup_head,
                    ],
                    worktree,
                )
        if result.returncode == 0:
            result = command_runner(
                [
                    "git",
                    "push",
                    f"--force-with-lease=refs/heads/{operation.integration_branch}:{operation.expected_remote_sha}",
                    "origin",
                    f"HEAD:{operation.integration_branch}",
                ],
                worktree,
            )
        return result

    def execute(
        self,
        operation: IntegrationSyncOperation,
        *,
        repo: Path,
        worktree: Path,
        env: dict[str, str] | None = None,
        command_runner=None,
    ) -> SyncExecutionResult:
        command_runner = command_runner or run
        try:
            self._validate_common(repo, worktree, operation, env=env, command_runner=command_runner)
            clean, merge_in_progress = self._ensure_clean_or_merge(worktree, command_runner=command_runner)
            if not clean:
                raise IntegrationSyncOperationError("dirty non-merge worktree")

            if operation.kind == "push-local-ahead":
                result = self._execute_push_local_ahead(operation, worktree, command_runner=command_runner)
            elif operation.kind == "reset-to-remote":
                result = self._execute_reset_to_remote(operation, worktree, command_runner=command_runner)
            elif operation.kind == "continue-resolved-merge":
                result = self._execute_continue_resolved_merge(
                    operation,
                    worktree,
                    merge_in_progress=merge_in_progress,
                    command_runner=command_runner,
                )
            elif operation.kind == "forward-sync-review-base":
                result = self._execute_forward_sync_review_base(operation, worktree, command_runner=command_runner)
            elif operation.kind == "adopt-merged-rollup":
                result = self._execute_adopt_merged_rollup(operation, worktree, command_runner=command_runner)
            else:
                raise IntegrationSyncOperationError("invalid kind")

            if result.returncode != 0:
                raise IntegrationSyncOperationError((result.stderr or result.stdout or "execution failed").strip()[:240])
            return SyncExecutionResult("applied", operation.kind, self._record(repo, operation, "applied", operation.kind))
        except Exception as exc:
            return self._reject(repo, operation, str(exc))
