"""Git subprocess primitives that preserve existing controller semantics."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

SAFE_WORKTREE_ITERATION_RE = re.compile(r"^[0-9]+$")
SAFE_WORKTREE_CLUSTER_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class Git:
    repo_root: Path

    def run(self, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", "-C", str(self.repo_root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed with exit {result.returncode}")
        return result

    def safe_worktree(self, iteration: str | int, cluster: str, base_ref: str) -> tuple[Path, str]:
        _validate_safe_worktree_fields(str(iteration), cluster)
        wt_path = self.repo_root / ".worktrees" / f"iter{iteration}-{cluster}"
        branch = f"refactor/iter{iteration}-{cluster}"
        if wt_path.is_dir():
            return wt_path, branch
        (self.repo_root / ".worktrees").mkdir(parents=True, exist_ok=True)
        if self.run(["show-ref", "--quiet", f"refs/heads/{branch}"], check=False).returncode == 0:
            self.run(["worktree", "add", str(wt_path), branch])
        else:
            self.run(["worktree", "add", "-b", branch, str(wt_path), base_ref])
        return wt_path, branch

    def fresh_safe_worktree(self, iteration: str | int, cluster: str, base_ref: str) -> tuple[Path, str]:
        _validate_safe_worktree_fields(str(iteration), cluster)
        wt_path = self.repo_root / ".worktrees" / f"iter{iteration}-{cluster}"
        branch = f"refactor/iter{iteration}-{cluster}"
        self.run(["fetch", "origin", base_ref])
        self.run(["worktree", "remove", str(wt_path), "--force"], check=False)
        self.run(["branch", "-D", branch], check=False)
        (self.repo_root / ".worktrees").mkdir(parents=True, exist_ok=True)
        self.run(["worktree", "add", "-b", branch, str(wt_path), f"origin/{base_ref}"])
        return wt_path, branch

    def merge_ff_only(self, ref: str) -> subprocess.CompletedProcess[str]:
        return self.run(["merge", "--ff-only", ref])

    def push(self, remote: str, refspec: str, *, force_with_lease: bool = False) -> subprocess.CompletedProcess[str]:
        args = ["push"]
        if force_with_lease:
            args.append("--force-with-lease")
        args.extend([remote, refspec])
        return self.run(args)


def _validate_safe_worktree_fields(iteration: str, cluster: str) -> None:
    """refactor helper, no behavior change except rejecting unsafe path fields."""
    if not SAFE_WORKTREE_ITERATION_RE.fullmatch(iteration):
        raise ValueError(f"safe_worktree iteration must be digits only: {iteration!r}")
    if not SAFE_WORKTREE_CLUSTER_RE.fullmatch(cluster):
        raise ValueError(f"safe_worktree cluster must match [A-Za-z0-9._-]+: {cluster!r}")
