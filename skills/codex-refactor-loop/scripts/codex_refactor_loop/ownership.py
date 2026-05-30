"""GitHub-native work ownership projection for side-effect gates."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal, Sequence


AI_SENTINEL = "⟦AI:AUTO-LOOP⟧"
STALE_AFTER = timedelta(hours=3)


@dataclass(frozen=True)
class WorkTarget:
    # Refactor (iter/issue-193):
    #   Old pattern: multi-node work ownership could drift toward device ids,
    #   local claims, or lease refs as new authority.
    #   New principle: WorkTarget names only an existing GitHub issue/PR fact.
    kind: Literal["issue", "pr"]
    number: int

    @classmethod
    def from_mapping(cls, raw: object) -> "WorkTarget | None":
        if not isinstance(raw, dict):
            return None
        kind = str(raw.get("kind") or "").lower()
        if kind in {"pull", "pull_request", "pr"}:
            kind = "pr"
        if kind not in {"issue", "pr"}:
            return None
        try:
            number = int(raw.get("number") or raw.get("pr_number") or raw.get("issue_number"))
        except (TypeError, ValueError):
            return None
        if number <= 0:
            return None
        return cls(kind=kind, number=number)  # type: ignore[arg-type]

    @property
    def display(self) -> str:
        return f"{'PR' if self.kind == 'pr' else 'issue'} #{self.number}"


@dataclass(frozen=True)
class GitHubItemOwnership:
    target: WorkTarget
    author_login: str
    updated_at: datetime
    current_login: str
    age_hours: float


@dataclass(frozen=True)
class OwnershipDecision:
    allowed: bool
    reason: Literal["owned", "foreign-fresh", "stale-takeover", "unknown-current-login", "unknown-target"]
    target: WorkTarget
    author_login: str = ""
    current_login: str = ""
    age_hours: float = 0.0

    @property
    def fresh_foreign(self) -> bool:
        return self.reason == "foreign-fresh"


class GitHubWorkOwnership:
    # Refactor (iter/issue-193):
    #   Old pattern: comments, labels, device ids, local files, or git-ref-CAS
    #   could become competing ownership authorities.
    #   New principle: decide from read-only GitHub author.login + updatedAt.
    def __init__(
        self,
        repo_slug: str | None,
        *,
        cwd: Path,
        command_runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None,
        stale_after: timedelta = STALE_AFTER,
    ) -> None:
        self.repo_slug = repo_slug
        self.cwd = cwd
        self.command_runner = command_runner or self._run
        self.stale_after = stale_after
        self._current_login: str | None = None

    def _repo_args(self) -> list[str]:
        return ["--repo", self.repo_slug] if self.repo_slug else []

    def _run(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(list(command), cwd=str(self.cwd), capture_output=True, text=True, check=False)

    def _gh_json(self, args: Sequence[str]) -> object | None:
        result = self.command_runner(["gh", *args, *self._repo_args()])
        if result.returncode != 0:
            return None
        try:
            return json.loads(result.stdout or "null")
        except json.JSONDecodeError:
            return None

    def current_login(self) -> str | None:
        if self._current_login is not None:
            return self._current_login
        data = self._gh_json(["api", "user"])
        login = data.get("login") if isinstance(data, dict) else None
        self._current_login = str(login) if login else None
        return self._current_login

    def load(self, target: WorkTarget, *, now: datetime | None = None) -> GitHubItemOwnership | None:
        login = self.current_login()
        if not login:
            return None
        gh_kind = "pr" if target.kind == "pr" else "issue"
        data = self._gh_json([gh_kind, "view", str(target.number), "--json", "author,updatedAt"])
        if not isinstance(data, dict):
            return None
        author = data.get("author")
        author_login = author.get("login") if isinstance(author, dict) else None
        updated = _parse_github_time(data.get("updatedAt"))
        if not author_login or updated is None:
            return None
        now = _ensure_utc(now or datetime.now(timezone.utc))
        age_hours = max(0.0, (now - updated).total_seconds() / 3600)
        return GitHubItemOwnership(
            target=target,
            author_login=str(author_login),
            updated_at=updated,
            current_login=login,
            age_hours=age_hours,
        )

    def decide(self, target: WorkTarget, *, now: datetime | None = None) -> OwnershipDecision:
        item = self.load(target, now=now)
        if item is None:
            login = self.current_login() or ""
            reason: Literal["unknown-current-login", "unknown-target"] = "unknown-current-login" if not login else "unknown-target"
            return OwnershipDecision(False, reason, target, current_login=login)
        if item.author_login == item.current_login:
            return OwnershipDecision(True, "owned", target, item.author_login, item.current_login, item.age_hours)
        if _ensure_utc(now or datetime.now(timezone.utc)) - item.updated_at >= self.stale_after:
            return OwnershipDecision(True, "stale-takeover", target, item.author_login, item.current_login, item.age_hours)
        return OwnershipDecision(False, "foreign-fresh", target, item.author_login, item.current_login, item.age_hours)

    def takeover_comment(self, decision: OwnershipDecision) -> str:
        return (
            "## 🔄 Stale takeover notice\n\n"
            f"Target: {decision.target.display}\n\n"
            f"Previous author.login: `{decision.author_login}`\n\n"
            f"Current login: `{decision.current_login}`\n\n"
            f"stale_hours={int(decision.age_hours)}; ownership authority is GitHub `author.login` plus `updatedAt`, "
            "and comments or labels are visibility only.\n\n"
            f"{AI_SENTINEL}\n"
        )

    def post_takeover_notice(self, decision: OwnershipDecision) -> bool:
        # Refactor (iter/issue-193):
        #   Old pattern: stale takeover could be decided without the visible
        #   GitHub explanation required before controller side effects.
        #   New principle: stale takeover side effects require a posted
        #   comment; the comment remains visibility only, not authority.
        if decision.reason != "stale-takeover":
            return True
        result = self.command_runner(
            [
                "gh",
                "pr" if decision.target.kind == "pr" else "issue",
                "comment",
                str(decision.target.number),
                "--body",
                self.takeover_comment(decision),
                *self._repo_args(),
            ]
        )
        return result.returncode == 0


class WorkTargetResolver:
    # Refactor (iter/issue-193):
    #   Old pattern: side-effecting callers inferred ownership from local
    #   state or task strings differently at each entry point.
    #   New principle: resolve only existing issue/PR targets, then ask the
    #   read-only ownership helper before side effects.
    ISSUE_RE = __import__("re").compile(r"(?:^|[-_ ])issue[-_# ]?(\d+)|#(\d+)")
    PR_RE = __import__("re").compile(r"(?:^|[-_ ])pr[-_# ]?(\d+)|PR #(\d+)", __import__("re").IGNORECASE)

    @classmethod
    def from_payload(cls, payload: dict, *, fallback_text: str = "") -> WorkTarget | None:
        for key in ("github_target", "target"):
            target = WorkTarget.from_mapping(payload.get(key))
            if target:
                return target
        pr_number = payload.get("pr_number")
        if isinstance(pr_number, int) and pr_number > 0:
            return WorkTarget("pr", pr_number)
        issue_number = payload.get("issue_number")
        if isinstance(issue_number, int) and issue_number > 0:
            return WorkTarget("issue", issue_number)
        return cls.from_text(" ".join(str(payload.get(key) or "") for key in ("task_id", "reason", "prompt", "log")) + " " + fallback_text)

    @classmethod
    def from_text(cls, text: str) -> WorkTarget | None:
        pr = cls.PR_RE.search(text)
        if pr:
            return WorkTarget("pr", int(next(group for group in pr.groups() if group)))
        issue = cls.ISSUE_RE.search(text)
        if issue:
            return WorkTarget("issue", int(next(group for group in issue.groups() if group)))
        return None


def _parse_github_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
