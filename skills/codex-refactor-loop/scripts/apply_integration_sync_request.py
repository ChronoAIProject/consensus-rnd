#!/usr/bin/env python3
"""Controller-owned apply helper for IntegrationSyncRequest artifacts."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from integration_sync_requests import IntegrationSyncRequestError, load_request


def repo_root() -> Path:
    env_root = os.environ.get("REPO_ROOT")
    if not env_root:
        raise RuntimeError("REPO_ROOT is required")
    return Path(env_root)


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


def ensure_clean_or_merge(worktree: Path) -> tuple[bool, bool]:
    merge_head = run(["git", "rev-parse", "--git-path", "MERGE_HEAD"], worktree)
    merge_in_progress = merge_head.returncode == 0 and Path(merge_head.stdout.strip()).exists()
    if merge_in_progress:
        return True, True
    unstaged = run(["git", "diff", "--quiet"], worktree)
    staged = run(["git", "diff", "--cached", "--quiet"], worktree)
    return unstaged.returncode == 0 and staged.returncode == 0, False


def validate_common(repo: Path, worktree: Path, request_path: Path):
    request = load_request(request_path)
    if applied_marker(repo, request_path).exists() or request.applied:
        raise IntegrationSyncRequestError("already-applied")
    expected_integration = os.environ.get("INTEGRATION_BRANCH") or os.environ.get("INTEGRATION") or "auto-refact-dev"
    expected_review_base = os.environ.get("REVIEW_BASE_BRANCH") or os.environ.get("REVIEW_BASE") or "dev"
    if request.integration_branch != expected_integration:
        raise IntegrationSyncRequestError("branch mismatch: integration")
    if request.review_base_branch != expected_review_base:
        raise IntegrationSyncRequestError("branch mismatch: review_base")
    run(["git", "fetch", "origin", "--quiet"], worktree)
    remote = run(["git", "rev-parse", f"origin/{request.integration_branch}"], worktree)
    if remote.returncode != 0 or remote.stdout.strip() != request.expected_remote_sha:
        raise IntegrationSyncRequestError("stale expected_remote_sha")
    head = run(["git", "rev-parse", "HEAD"], worktree)
    if head.returncode != 0 or head.stdout.strip() != request.worktree_head:
        raise IntegrationSyncRequestError("stale worktree_head")
    return request


def apply_request(request_path: Path, *, repo: Path, worktree: Path) -> int:
    try:
        request = validate_common(repo, worktree, request_path)
        clean, merge_in_progress = ensure_clean_or_merge(worktree)
        if not clean:
            raise IntegrationSyncRequestError("dirty non-merge worktree")

        if request.kind == "push-local-ahead":
            count = run(["git", "rev-list", "--count", f"origin/{request.integration_branch}..HEAD"], worktree)
            if count.returncode != 0 or int((count.stdout or "0").strip() or "0") <= 0:
                raise IntegrationSyncRequestError("no local ahead commits")
            result = run(["git", "push", "origin", f"HEAD:{request.integration_branch}"], worktree)
        elif request.kind == "continue-resolved-merge":
            if not merge_in_progress:
                raise IntegrationSyncRequestError("no merge in progress")
            result = run(["git", "merge", "--continue"], worktree)
            if result.returncode == 0:
                result = run(["git", "push", "origin", f"HEAD:{request.integration_branch}"], worktree)
        elif request.kind == "forward-sync-review-base":
            ff = run(["git", "merge", "--ff-only", f"origin/{request.review_base_branch}"], worktree)
            if ff.returncode == 0:
                result = run(["git", "push", "origin", f"HEAD:{request.integration_branch}"], worktree)
            else:
                result = run(
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
                    result = run(["git", "push", "origin", f"HEAD:{request.integration_branch}"], worktree)
        elif request.kind == "adopt-merged-rollup":
            assert request.old_rollup_head is not None
            ancestor = run(
                ["git", "merge-base", "--is-ancestor", request.old_rollup_head, f"origin/{request.integration_branch}"],
                worktree,
            )
            if ancestor.returncode != 0:
                raise IntegrationSyncRequestError("invalid rollup ancestry")
            replay = run(["git", "rev-list", "--count", f"{request.old_rollup_head}..origin/{request.integration_branch}"], worktree)
            replay_n = int((replay.stdout or "0").strip() or "0")
            if request.old_rollup_ahead_count is not None and replay_n != request.old_rollup_ahead_count:
                raise IntegrationSyncRequestError("stale rollup ahead count")
            if replay_n == 0:
                result = run(["git", "reset", "--hard", f"origin/{request.review_base_branch}"], worktree)
            else:
                result = run(["git", "reset", "--hard", f"origin/{request.integration_branch}"], worktree)
                if result.returncode == 0:
                    result = run(
                        ["git", "rebase", "--rebase-merges", "--onto", f"origin/{request.review_base_branch}", request.old_rollup_head],
                        worktree,
                    )
            if result.returncode == 0:
                result = run(
                    [
                        "git",
                        "push",
                        f"--force-with-lease=refs/heads/{request.integration_branch}:{request.expected_remote_sha}",
                        "origin",
                        f"HEAD:{request.integration_branch}",
                    ],
                    worktree,
                )
        else:
            raise IntegrationSyncRequestError("invalid kind")

        if result.returncode != 0:
            raise IntegrationSyncRequestError((result.stderr or result.stdout or "apply failed").strip()[:240])
        record(repo, request_path, "applied", request.kind)
        print(f"INTEGRATION_SYNC_APPLIED:{request_path}:{request.kind}")
        return 0
    except Exception as exc:
        return reject(repo, request_path, str(exc))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request_path")
    parser.add_argument("--worktree", default=os.environ.get("WORKTREE"))
    args = parser.parse_args(argv)
    repo = repo_root()
    worktree = Path(args.worktree) if args.worktree else repo / ".worktrees" / "dev-sync"
    return apply_request(Path(args.request_path), repo=repo, worktree=worktree)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
