"""Controller-owned apply helper for IntegrationSyncRequest artifacts.

Refactor (issue160/p3-sync-apply): Old pattern:
`apply_integration_sync_request.py` owned controller apply as a top-level
script with direct imports from another top-level script.
New principle: expose the same live-state-rechecked apply behavior from the
package sync boundary; legacy callers remain on the old script until the caller
switch.

Contract: controller sweep consumes `DEV_SYNC_REQUEST:<path>` markers, resolves
host state from `REPO_ROOT` / host.env, and applies only
IntegrationSyncRequest artifacts with lifecycle_owner controller and
lifecycle_authority false.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

from ..context import LoopContext
from ..ownership import GitHubWorkOwnership, WorkTarget
from .requests import IntegrationSyncRequest, IntegrationSyncRequestError, load_request


DEFAULT_INTEGRATION_BRANCH = "auto-refact-dev"
DEFAULT_REVIEW_BASE_BRANCH = "dev"


def repo_root(env: dict[str, str] | None = None, cwd: Path | str | None = None) -> Path:
    source_env = dict(os.environ if env is None else env)
    return LoopContext.load(env=source_env, cwd=cwd or os.getcwd()).repo_root


def run(cmd: list[str], cwd: Path, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check)


def record(repo: Path, request_path: Path, status: str, reason: str) -> Path:
    out_dir = repo / ".refactor-loop" / "runs" / "integration-sync-applied"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{request_path.stem}.{status}.json"
    out.write_text(
        json.dumps(
            {
                "request_path": str(request_path),
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


def reject(repo: Path, request_path: Path, reason: str) -> int:
    record(repo, request_path, "rejected", reason)
    print(f"INTEGRATION_SYNC_REJECTED:{request_path}:{reason}")
    return 2


def applied_marker(repo: Path, request_path: Path) -> Path:
    return repo / ".refactor-loop" / "runs" / "integration-sync-applied" / f"{request_path.stem}.applied.json"


def ensure_clean_or_merge(
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


def expected_branches(env: dict[str, str] | None = None) -> tuple[str, str]:
    source_env = os.environ if env is None else env
    expected_integration = (
        source_env.get("INTEGRATION_BRANCH") or source_env.get("INTEGRATION") or DEFAULT_INTEGRATION_BRANCH
    )
    expected_review_base = (
        source_env.get("REVIEW_BASE_BRANCH") or source_env.get("REVIEW_BASE") or DEFAULT_REVIEW_BASE_BRANCH
    )
    return expected_integration, expected_review_base


def validate_common(
    repo: Path,
    worktree: Path,
    request_path: Path,
    *,
    env: dict[str, str] | None = None,
    command_runner=None,
) -> IntegrationSyncRequest:
    command_runner = command_runner or run
    request = load_request(request_path)
    if applied_marker(repo, request_path).exists() or request.applied:
        raise IntegrationSyncRequestError("already-applied")
    expected_integration, expected_review_base = expected_branches(env)
    if request.integration_branch != expected_integration:
        raise IntegrationSyncRequestError("branch mismatch: integration")
    if request.review_base_branch != expected_review_base:
        raise IntegrationSyncRequestError("branch mismatch: review_base")
    command_runner(["git", "fetch", "origin", "--quiet"], worktree)
    remote = command_runner(["git", "rev-parse", f"origin/{request.integration_branch}"], worktree)
    if remote.returncode != 0 or remote.stdout.strip() != request.expected_remote_sha:
        raise IntegrationSyncRequestError("stale expected_remote_sha")
    head = command_runner(["git", "rev-parse", "HEAD"], worktree)
    if head.returncode != 0 or head.stdout.strip() != request.worktree_head:
        raise IntegrationSyncRequestError("stale worktree_head")
    return request


def apply_push_local_ahead(
    request: IntegrationSyncRequest,
    worktree: Path,
    *,
    command_runner=None,
) -> subprocess.CompletedProcess[str]:
    command_runner = command_runner or run
    count = command_runner(["git", "rev-list", "--count", f"origin/{request.integration_branch}..HEAD"], worktree)
    if count.returncode != 0 or int((count.stdout or "0").strip() or "0") <= 0:
        raise IntegrationSyncRequestError("no local ahead commits")
    return command_runner(["git", "push", "origin", f"HEAD:{request.integration_branch}"], worktree)


def apply_continue_resolved_merge(
    request: IntegrationSyncRequest,
    worktree: Path,
    *,
    merge_in_progress: bool,
    command_runner=None,
) -> subprocess.CompletedProcess[str]:
    command_runner = command_runner or run
    if not merge_in_progress:
        raise IntegrationSyncRequestError("no merge in progress")
    result = command_runner(["git", "merge", "--continue"], worktree)
    if result.returncode == 0:
        result = command_runner(["git", "push", "origin", f"HEAD:{request.integration_branch}"], worktree)
    return result


def apply_forward_sync_review_base(
    request: IntegrationSyncRequest,
    worktree: Path,
    *,
    command_runner=None,
) -> subprocess.CompletedProcess[str]:
    command_runner = command_runner or run
    ff = command_runner(["git", "merge", "--ff-only", f"origin/{request.review_base_branch}"], worktree)
    if ff.returncode == 0:
        return command_runner(["git", "push", "origin", f"HEAD:{request.integration_branch}"], worktree)
    result = command_runner(
        [
            "git",
            "merge",
            "--no-ff",
            "-m",
            f"Sync {request.integration_branch} with {request.review_base_branch} (controller apply)",
            f"origin/{request.review_base_branch}",
        ],
        worktree,
    )
    if result.returncode == 0:
        result = command_runner(["git", "push", "origin", f"HEAD:{request.integration_branch}"], worktree)
    return result


def apply_adopt_merged_rollup(
    request: IntegrationSyncRequest,
    worktree: Path,
    *,
    command_runner=None,
) -> subprocess.CompletedProcess[str]:
    command_runner = command_runner or run
    assert request.old_rollup_head is not None
    ancestor = command_runner(
        ["git", "merge-base", "--is-ancestor", request.old_rollup_head, f"origin/{request.integration_branch}"],
        worktree,
    )
    if ancestor.returncode != 0:
        raise IntegrationSyncRequestError("invalid rollup ancestry")
    replay = command_runner(
        ["git", "rev-list", "--count", f"{request.old_rollup_head}..origin/{request.integration_branch}"],
        worktree,
    )
    replay_n = int((replay.stdout or "0").strip() or "0")
    if request.old_rollup_ahead_count is not None and replay_n != request.old_rollup_ahead_count:
        raise IntegrationSyncRequestError("stale rollup ahead count")
    if replay_n == 0:
        result = command_runner(["git", "reset", "--hard", f"origin/{request.review_base_branch}"], worktree)
    else:
        result = command_runner(["git", "reset", "--hard", f"origin/{request.integration_branch}"], worktree)
        if result.returncode == 0:
            result = command_runner(
                [
                    "git",
                    "rebase",
                    "--rebase-merges",
                    "--onto",
                    f"origin/{request.review_base_branch}",
                    request.old_rollup_head,
                ],
                worktree,
            )
    if result.returncode == 0:
        result = command_runner(
            [
                "git",
                "push",
                f"--force-with-lease=refs/heads/{request.integration_branch}:{request.expected_remote_sha}",
                "origin",
                f"HEAD:{request.integration_branch}",
            ],
            worktree,
        )
    return result


def apply_request(
    request_path: Path,
    *,
    repo: Path,
    worktree: Path,
    env: dict[str, str] | None = None,
    command_runner=None,
) -> int:
    command_runner = command_runner or run
    try:
        request = validate_common(repo, worktree, request_path, env=env, command_runner=command_runner)
        # Refactor (iter/issue-193):
        #   Old pattern: PR-targeted sync apply relied only on local request
        #   state and branch SHA checks before git lifecycle side effects.
        #   New principle: if a request names a PR, its author.login ownership
        #   gate must pass before apply; non-PR integration sync stays SHA-only.
        if request.pr_number is not None:
            ctx = LoopContext.load(repo_root=repo, env=env)
            decision = GitHubWorkOwnership(ctx.gh_repo_slug, cwd=repo).decide(WorkTarget("pr", request.pr_number))
            if not decision.allowed:
                raise IntegrationSyncRequestError(f"ownership not allowed: {decision.reason}")
        clean, merge_in_progress = ensure_clean_or_merge(worktree, command_runner=command_runner)
        if not clean:
            raise IntegrationSyncRequestError("dirty non-merge worktree")

        if request.kind == "push-local-ahead":
            result = apply_push_local_ahead(request, worktree, command_runner=command_runner)
        elif request.kind == "continue-resolved-merge":
            result = apply_continue_resolved_merge(
                request,
                worktree,
                merge_in_progress=merge_in_progress,
                command_runner=command_runner,
            )
        elif request.kind == "forward-sync-review-base":
            result = apply_forward_sync_review_base(request, worktree, command_runner=command_runner)
        elif request.kind == "adopt-merged-rollup":
            result = apply_adopt_merged_rollup(request, worktree, command_runner=command_runner)
        else:
            raise IntegrationSyncRequestError("invalid kind")

        if result.returncode != 0:
            raise IntegrationSyncRequestError((result.stderr or result.stdout or "apply failed").strip()[:240])
        record(repo, request_path, "applied", request.kind)
        print(f"INTEGRATION_SYNC_APPLIED:{request_path}:{request.kind}")
        return 0
    except Exception as exc:
        return reject(repo, request_path, str(exc))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request_path")
    parser.add_argument("--worktree", default=os.environ.get("WORKTREE"))
    args = parser.parse_args(argv)
    repo = repo_root()
    worktree = Path(args.worktree) if args.worktree else repo / ".worktrees" / "dev-sync"
    return apply_request(Path(args.request_path), repo=repo, worktree=worktree)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
