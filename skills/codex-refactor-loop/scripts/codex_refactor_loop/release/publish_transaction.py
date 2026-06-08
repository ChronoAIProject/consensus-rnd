"""Detached first-run release commit transaction."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


Runner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class ReleaseCommitTransactionResult:
    target_ref: str
    base_ref: str
    attempts: int


class ReleaseCommitTransaction:
    """Materialize and push a release commit from the fresh integration tip."""

    def __init__(
        self,
        repo_root: Path,
        *,
        runner: Runner,
        integration_branch: str,
        max_attempts: int = 3,
    ) -> None:
        self.repo_root = repo_root
        self.runner = runner
        self.integration_branch = integration_branch
        self.max_attempts = max_attempts

    def publish(self, *, version: str, manifest_paths: Sequence[str], subject: str) -> ReleaseCommitTransactionResult:
        if not self.integration_branch:
            raise RuntimeError("INTEGRATION_BRANCH is required for release publish transaction")
        if self.max_attempts < 1:
            raise RuntimeError("release publish transaction requires at least one attempt")

        last_base = ""
        for attempt in range(1, self.max_attempts + 1):
            base_sha = self._resolve_remote_base()
            last_base = base_sha
            worktree = self._worktree_path(version, attempt)
            self._clear_worktree_path(worktree)
            try:
                self._ensure_success(
                    self.runner(["git", "worktree", "add", "--detach", str(worktree), base_sha], self.repo_root),
                    "git worktree add --detach",
                )
                self._materialize_release_commit(worktree, version, manifest_paths, subject)
                release_sha = self._rev_parse(worktree, "HEAD")
                current_remote = self._resolve_remote_base()
                if current_remote != base_sha:
                    continue
                self._ensure_success(
                    self.runner(["git", "push", "origin", f"HEAD:refs/heads/{self.integration_branch}"], worktree),
                    "git push origin HEAD:refs/heads/$INTEGRATION_BRANCH",
                )
                return ReleaseCommitTransactionResult(target_ref=release_sha, base_ref=base_sha, attempts=attempt)
            finally:
                self._remove_worktree(worktree)

        raise RuntimeError(
            "release publish transaction refused: remote integration branch moved "
            f"after {self.max_attempts} attempts from last base {last_base}"
        )

    def _materialize_release_commit(
        self,
        worktree: Path,
        version: str,
        manifest_paths: Sequence[str],
        subject: str,
    ) -> None:
        self._ensure_success(
            self.runner(["python3", ".github/scripts/bump_version.py", "--version", version], worktree),
            "bump_version",
        )
        self._ensure_success(self.runner(["git", "add", ".version-bump.json", *manifest_paths], worktree), "git add")
        self._ensure_success(self.runner(["git", "commit", "-m", subject], worktree), "git commit")

    def _resolve_remote_base(self) -> str:
        self._ensure_success(
            self.runner(["git", "fetch", "origin", self.integration_branch], self.repo_root),
            "git fetch origin $INTEGRATION_BRANCH",
        )
        return self._rev_parse(self.repo_root, f"origin/{self.integration_branch}")

    def _rev_parse(self, cwd: Path, ref: str) -> str:
        result = self.runner(["git", "rev-parse", ref], cwd)
        self._ensure_success(result, f"git rev-parse {ref}")
        sha = result.stdout.strip()
        if not sha:
            raise RuntimeError(f"git rev-parse {ref} returned an empty release ref")
        return sha

    def _worktree_path(self, version: str, attempt: int) -> Path:
        safe_version = "".join(ch if ch.isalnum() or ch in ".-_" else "-" for ch in version)
        return self.repo_root / ".worktrees" / "release-publish" / f"{safe_version}-{attempt}"

    def _clear_worktree_path(self, worktree: Path) -> None:
        if worktree.exists():
            shutil.rmtree(worktree)

    def _remove_worktree(self, worktree: Path) -> None:
        if not worktree.exists():
            return
        result = self.runner(["git", "worktree", "remove", "--force", str(worktree)], self.repo_root)
        if result.returncode != 0:
            shutil.rmtree(worktree, ignore_errors=True)

    def _ensure_success(self, result: subprocess.CompletedProcess[str], label: str) -> None:
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"{label} failed")


__all__ = ["ReleaseCommitTransaction", "ReleaseCommitTransactionResult"]
