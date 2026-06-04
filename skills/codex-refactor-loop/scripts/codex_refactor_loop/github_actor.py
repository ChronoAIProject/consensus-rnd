"""Read-only GitHub authenticated-actor admission checks."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .context import LoopContext


Runner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class GitHubAuthenticatedActor:
    ctx: LoopContext
    runner: Runner | None = None

    def require_admission(self, action: str) -> None:
        if not self.ctx.gh_repo_slug:
            raise RuntimeError(f"github-authenticated-actor:{action}: GH_REPO_SLUG is required")
        self._require_auth_status(action)
        login = self._authenticated_login(action)
        permission = self._repo_permission(action)
        if permission.lower() not in {"admin", "maintain", "write"}:
            raise RuntimeError(
                f"github-authenticated-actor:{action}: authenticated actor {login} lacks write permission "
                f"for {self.ctx.gh_repo_slug}"
            )

    def _require_auth_status(self, action: str) -> None:
        result = self._run(["gh", "auth", "status"])
        if result.returncode != 0:
            raise RuntimeError(_failure(action, "gh auth status", result))

    def _authenticated_login(self, action: str) -> str:
        result = self._run(["gh", "api", "user"])
        if result.returncode != 0:
            raise RuntimeError(_failure(action, "gh api user", result))
        try:
            data = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"github-authenticated-actor:{action}: invalid gh api user JSON") from exc
        login = str(data.get("login") or "").strip()
        if not login:
            raise RuntimeError(f"github-authenticated-actor:{action}: authenticated login missing")
        return login

    def _repo_permission(self, action: str) -> str:
        result = self._run(["gh", "api", f"repos/{self.ctx.gh_repo_slug}"])
        if result.returncode != 0:
            raise RuntimeError(_failure(action, f"gh api repos/{self.ctx.gh_repo_slug}", result))
        try:
            data = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"github-authenticated-actor:{action}: invalid repo permission JSON") from exc
        permission = data.get("viewer_permission")
        if not isinstance(permission, str) or not permission.strip():
            permissions = data.get("permissions")
            if isinstance(permissions, dict):
                if permissions.get("admin"):
                    permission = "admin"
                elif permissions.get("maintain"):
                    permission = "maintain"
                elif permissions.get("push"):
                    permission = "write"
                elif permissions.get("triage"):
                    permission = "triage"
                elif permissions.get("pull"):
                    permission = "read"
        if not isinstance(permission, str) or not permission.strip():
            raise RuntimeError(f"github-authenticated-actor:{action}: repo permission missing")
        return permission.strip()

    def _run(self, cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if self.runner is not None:
            return self.runner(cmd, self.ctx.repo_root)
        return subprocess.run(list(cmd), cwd=str(self.ctx.repo_root), capture_output=True, text=True, check=False)


def _failure(action: str, label: str, result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout).strip()
    if detail:
        return f"github-authenticated-actor:{action}: {label} failed: {detail}"
    return f"github-authenticated-actor:{action}: {label} failed with exit {result.returncode}"
