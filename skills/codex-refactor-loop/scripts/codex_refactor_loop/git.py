"""Git subprocess primitives that preserve existing controller semantics."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


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

    def merge_ff_only(self, ref: str) -> subprocess.CompletedProcess[str]:
        return self.run(["merge", "--ff-only", ref])

    def push(self, remote: str, refspec: str, *, force_with_lease: bool = False) -> subprocess.CompletedProcess[str]:
        args = ["push"]
        if force_with_lease:
            args.append("--force-with-lease")
        args.extend([remote, refspec])
        return self.run(args)

