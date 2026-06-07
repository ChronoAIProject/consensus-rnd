"""Default issue intake claim helper for open GitHub issues."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import labels
from .context import LoopContext
from .gh_invoke import build_gh_argv
from .implementation_pr_artifacts import FINAL_SENTINEL
from .managed_work_snapshot import invalidate_open_managed_work_snapshot
from .secondary_mutation_backoff import record_content_creation_backoff


CLAIM_MARKER = "crnd:default-issue-intake-claim"
STOP_MARKER = "crnd:default-issue-intake-stop"
FALSE_LIKE_VALUES = {"false", "0", "no", "off"}
CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class DefaultIssueIntakeResult:
    issue_number: int
    status: str
    reason: str
    first_claimer: str = ""
    first_claimed_at: str = ""


@dataclass(frozen=True)
class DefaultIssueIntakeClaimComment:
    author_login: str
    created_at: str


def default_issue_intake_enabled(values: Mapping[str, str] | None = None) -> bool:
    raw = (values or os.environ).get("DEFAULT_ISSUE_INTAKE_ENABLE")
    if raw is None:
        return True
    normalized = str(raw).strip().lower()
    if not normalized:
        return True
    return normalized not in FALSE_LIKE_VALUES


class DefaultIssueIntakeClaim:
    def __init__(
        self,
        ctx: LoopContext,
        *,
        actor_login: str,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.ctx = ctx
        self.actor_login = actor_login.strip()
        self.command_runner = command_runner or self._run

    def apply(self, issue_number: int) -> DefaultIssueIntakeResult:
        if not default_issue_intake_enabled(self.ctx.host_env):
            return DefaultIssueIntakeResult(issue_number, "noop", "disabled")
        if issue_number <= 0:
            raise RuntimeError("default_issue_intake: issue_number must be positive")
        if not self.ctx.gh_repo_slug:
            raise RuntimeError("default_issue_intake: GH_REPO_SLUG is required")
        if not self.actor_login:
            raise RuntimeError("default_issue_intake: authenticated actor login is required")

        issue = self._issue(issue_number)
        state = str(issue.get("state") or "").strip().lower()
        if state != "open":
            return DefaultIssueIntakeResult(issue_number, "noop", f"target_not_open:{state or 'unknown'}")
        if isinstance(issue.get("pull_request"), Mapping):
            return DefaultIssueIntakeResult(issue_number, "noop", "target_is_pr")
        if self._has_managed_label(issue):
            return DefaultIssueIntakeResult(issue_number, "noop", "target_already_managed")

        first_claim = self._first_claim(issue_number)
        if first_claim is None:
            self._write_claim_comment(issue_number)
            self._apply_design_labels(issue_number)
            invalidate_open_managed_work_snapshot(self.ctx)
            return DefaultIssueIntakeResult(issue_number, "applied", "claim_created", self.actor_login)

        if first_claim.author_login == self.actor_login:
            self._apply_design_labels(issue_number)
            invalidate_open_managed_work_snapshot(self.ctx)
            return DefaultIssueIntakeResult(
                issue_number,
                "applied",
                "existing_self_claim",
                first_claim.author_login,
                first_claim.created_at,
            )

        self._write_stop_comment(issue_number, first_claim)
        return DefaultIssueIntakeResult(
            issue_number,
            "stopped",
            "earlier_other_claim",
            first_claim.author_login,
            first_claim.created_at,
        )

    def _issue(self, issue_number: int) -> dict[str, Any]:
        result = self.command_runner(["gh", "api", f"repos/{self.ctx.gh_repo_slug}/issues/{issue_number}"])
        if result.returncode != 0:
            raise RuntimeError(_failure("issue", result))
        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError("default_issue_intake: issue JSON invalid") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("default_issue_intake: issue JSON invalid")
        return payload

    def _comments(self, issue_number: int) -> list[dict[str, Any]]:
        result = self.command_runner(
            ["gh", "api", f"repos/{self.ctx.gh_repo_slug}/issues/{issue_number}/comments?per_page=100"]
        )
        if result.returncode != 0:
            raise RuntimeError(_failure("comments", result))
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise RuntimeError("default_issue_intake: comments JSON invalid") from exc
        if not isinstance(payload, list):
            raise RuntimeError("default_issue_intake: comments JSON invalid")
        return [comment for comment in payload if isinstance(comment, dict)]

    def _first_claim(self, issue_number: int) -> DefaultIssueIntakeClaimComment | None:
        claims: list[DefaultIssueIntakeClaimComment] = []
        for comment in self._comments(issue_number):
            body = comment.get("body")
            created_at = str(comment.get("created_at") or "").strip()
            user = comment.get("user")
            author = str(user.get("login") or "").strip() if isinstance(user, dict) else ""
            if not isinstance(body, str) or CLAIM_MARKER not in body or not created_at or not author:
                continue
            claims.append(DefaultIssueIntakeClaimComment(author_login=author, created_at=created_at))
        if not claims:
            return None
        return min(claims, key=lambda claim: (_claim_time_key(claim.created_at), claim.created_at, claim.author_login))

    def _write_claim_comment(self, issue_number: int) -> None:
        body = (
            f"{CLAIM_MARKER}\n\n"
            f"First claimer: {self.actor_login}\n\n"
            f"{FINAL_SENTINEL}\n"
        )
        self._comment(issue_number, body, "claim comment")

    def _write_stop_comment(self, issue_number: int, first_claim: DefaultIssueIntakeClaimComment) -> None:
        body = (
            f"{STOP_MARKER}\n\n"
            f"First claim already exists: {first_claim.author_login} at {first_claim.created_at}\n\n"
            f"{FINAL_SENTINEL}\n"
        )
        self._comment(issue_number, body, "stop comment")

    def _comment(self, issue_number: int, body: str, label: str) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
            handle.write(body)
            path = handle.name
        try:
            result = self.command_runner(["gh", "issue", "comment", str(issue_number), "--body-file", path])
        finally:
            Path(path).unlink(missing_ok=True)
        if result.returncode != 0:
            record_content_creation_backoff(self.ctx, f"default-issue-intake-{label}", result)
            raise RuntimeError(_failure(label, result))

    def _apply_design_labels(self, issue_number: int) -> None:
        result = self.command_runner(
            [
                "gh",
                "issue",
                "edit",
                str(issue_number),
                "--add-label",
                ",".join(labels.design_issue_label_bundle()),
            ]
        )
        if result.returncode != 0:
            raise RuntimeError(_failure("label application", result))

    def _has_managed_label(self, issue: Mapping[str, Any]) -> bool:
        raw_labels = issue.get("labels")
        if not isinstance(raw_labels, list):
            return False
        names = [item.get("name") for item in raw_labels if isinstance(item, dict)]
        return labels.MANAGED in labels.normalize_label_set(names).canonical

    def _run(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            build_gh_argv(self.ctx.gh_repo_slug, list(command)),
            cwd=str(self.ctx.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )


def _claim_time_key(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.max.replace(tzinfo=timezone.utc)


def _failure(label: str, result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout).strip()
    if detail:
        return f"default_issue_intake: {label} failed: {detail}"
    return f"default_issue_intake: {label} failed with exit {result.returncode}"
