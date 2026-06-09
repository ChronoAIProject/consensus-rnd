"""Read-only GitHub authenticated-actor admission and diagnostics checks."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Sequence

from .context import LoopContext


Runner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]
Permission = Literal["read", "triage", "write", "maintain", "admin"]
Source = Literal["gh-api"]


PERMISSION_RANK: dict[str, int] = {
    "read": 0,
    "triage": 1,
    "write": 2,
    "maintain": 3,
    "admin": 4,
}


@dataclass(frozen=True)
class GitHubActorAdmission:
    login: str
    repo_slug: str
    permission: Permission
    source: Source = "gh-api"


@dataclass(frozen=True)
class GitHubActorDiagnostics:
    current_github_login: str
    identity_authority: str
    status: str
    error: str = ""

    def to_status_fields(self) -> dict[str, str]:
        fields = {
            "current_github_login": self.current_github_login,
            "identity_authority": self.identity_authority,
            "github_login_status": self.status,
        }
        if self.error:
            fields["github_login_error"] = self.error
        return fields


@dataclass(frozen=True)
class GitHubAuthenticatedActor:
    ctx: LoopContext
    runner: Runner | None = None

    def require_admission(self, action: str) -> GitHubActorAdmission:
        if not self.ctx.gh_repo_slug:
            raise RuntimeError(f"github-authenticated-actor:{action}: GH_REPO_SLUG is required")
        login = self._authenticated_login(action)
        permission = self._repo_permission(action, login)
        if PERMISSION_RANK[permission] < PERMISSION_RANK["write"]:
            raise RuntimeError(
                f"github-authenticated-actor:{action}: authenticated actor {login} lacks write permission "
                f"for {self.ctx.gh_repo_slug}"
            )
        return GitHubActorAdmission(login=login, repo_slug=self.ctx.gh_repo_slug, permission=permission)

    def diagnostics(self) -> GitHubActorDiagnostics:
        try:
            result = self._run(["gh", "api", "user"])
        except (OSError, subprocess.SubprocessError, TypeError, ValueError) as exc:
            return GitHubActorDiagnostics(
                current_github_login="",
                identity_authority="display-only",
                status="unavailable",
                error=f"github-authenticated-actor:diagnostics: gh api user failed: {exc}",
            )
        if result.returncode != 0:
            return GitHubActorDiagnostics(
                current_github_login="",
                identity_authority="display-only",
                status="unavailable",
                error=_failure("diagnostics", "gh api user", result),
            )
        try:
            data = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return GitHubActorDiagnostics(
                current_github_login="",
                identity_authority="display-only",
                status="invalid",
                error="github-authenticated-actor:diagnostics: invalid gh api user JSON",
            )
        login = str(data.get("login") or "").strip()
        if not login:
            return GitHubActorDiagnostics(
                current_github_login="",
                identity_authority="display-only",
                status="missing",
                error="github-authenticated-actor:diagnostics: authenticated login missing",
            )
        return GitHubActorDiagnostics(
            current_github_login=login,
            identity_authority="display-only",
            status="ok",
        )

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

    def _repo_permission(self, action: str, login: str) -> Permission:
        endpoint = f"repos/{self.ctx.gh_repo_slug}/collaborators/{login}/permission"
        result = self._run(["gh", "api", endpoint])
        if result.returncode != 0:
            raise RuntimeError(_failure(action, f"gh api {endpoint}", result))
        try:
            data = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"github-authenticated-actor:{action}: invalid repo permission JSON") from exc
        permission = data.get("permission")
        if not isinstance(permission, str):
            raise RuntimeError(f"github-authenticated-actor:{action}: repo permission missing")
        normalized = permission.strip().lower()
        if normalized not in PERMISSION_RANK:
            raise RuntimeError(f"github-authenticated-actor:{action}: unsupported repo permission {permission!r}")
        return normalized  # type: ignore[return-value]

    def _run(self, cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if self.runner is not None:
            return self.runner(cmd, self.ctx.repo_root)
        return subprocess.run(list(cmd), cwd=str(self.ctx.repo_root), capture_output=True, text=True, check=False)


def write_github_actor_diagnostics_status(
    ctx: LoopContext,
    *,
    runner: Runner | None = None,
) -> GitHubActorDiagnostics:
    diagnostics = GitHubAuthenticatedActor(ctx, runner=runner).diagnostics()
    path = ctx.paths.state / "active-controller-status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.update(diagnostics.to_status_fields())
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return diagnostics


def _failure(action: str, label: str, result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout).strip()
    if detail:
        return f"github-authenticated-actor:{action}: {label} failed: {detail}"
    return f"github-authenticated-actor:{action}: {label} failed with exit {result.returncode}"
