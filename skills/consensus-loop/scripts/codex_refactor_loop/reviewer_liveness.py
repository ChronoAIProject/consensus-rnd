"""Owner-local reviewer liveness projection."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .task_spawn_claim import (
    TaskSpawnClaimMetadataError,
    read_spawn_task_lock_metadata,
    safe_task_id_from_task,
    spawn_task_holder_alive,
)


REVIEW_HEAD_RE = re.compile(r"(?im)^(?:reviewed[-_ ]?head[-_ ]?sha|head[-_ ]?sha|headRefOid|REVIEW_HEAD_SHA)\s*[:=]\s*([0-9a-f]{7,64})\s*$")
REVIEW_TASK_PREFIX = "review" + "-pr"
REVIEW_ATTEMPT_RE = re.compile(r"^" + REVIEW_TASK_PREFIX + r"([1-9][0-9]*)-([A-Za-z][A-Za-z0-9_-]*)-r([1-9][0-9]*)\.(?:md|log)$")
REVIEW_PENDING_SECONDS = 90


@dataclass(frozen=True)
class ReviewerLivenessProjection:
    pr_number: int
    head_sha: str
    role: str
    round_number: int
    pending: bool
    terminal: bool
    terminal_success: bool
    terminal_failed: bool
    redispatchable: bool
    reason: str
    prompt_path: Path | None = None
    log_path: Path | None = None
    holder_pid: int | None = None

    @classmethod
    def for_next_dispatch(
        cls,
        repo_root: Path,
        *,
        pr_number: int,
        head_sha: str,
        role: str,
        next_round: int,
        now: float | None = None,
        pending_seconds: int = REVIEW_PENDING_SECONDS,
        holder_alive: Callable[[int], bool] | None = None,
    ) -> "ReviewerLivenessProjection":
        latest = reviewer_liveness_projection(
            repo_root,
            pr_number=pr_number,
            head_sha=head_sha,
            role=role,
            now=now,
            pending_seconds=pending_seconds,
            holder_alive=holder_alive,
        )
        if latest.round_number >= next_round:
            return latest
        return latest


@dataclass(frozen=True)
class _ReviewAttempt:
    pr_number: int
    role: str
    round_number: int
    prompt_path: Path
    log_path: Path
    prompt_head: str
    log_head: str

    @property
    def head_sha(self) -> str:
        return self.prompt_head or self.log_head


def reviewer_liveness_projection(
    repo_root: Path,
    *,
    pr_number: int,
    head_sha: str,
    role: str,
    now: float | None = None,
    pending_seconds: int = REVIEW_PENDING_SECONDS,
    holder_alive: Callable[[int], bool] | None = None,
) -> ReviewerLivenessProjection:
    attempts = [
        attempt
        for attempt in _review_attempts(repo_root, pr_number=pr_number, role=role)
        if attempt.head_sha == head_sha
    ]
    if not attempts:
        return ReviewerLivenessProjection(
            pr_number=pr_number,
            head_sha=head_sha,
            role=role,
            round_number=0,
            pending=False,
            terminal=False,
            terminal_success=False,
            terminal_failed=False,
            redispatchable=False,
            reason="no_same_head_attempt",
        )
    latest = max(attempts, key=lambda item: item.round_number)
    return _classify_attempt(
        repo_root,
        latest,
        head_sha=head_sha,
        now=now,
        pending_seconds=pending_seconds,
        holder_alive=holder_alive,
    )


def pending_reviewer_roles(
    repo_root: Path,
    *,
    pr_number: int,
    head_sha: str,
    roles: tuple[str, ...],
    now: float | None = None,
    pending_seconds: int = REVIEW_PENDING_SECONDS,
) -> set[str]:
    return {
        role
        for role in roles
        if reviewer_liveness_projection(
            repo_root,
            pr_number=pr_number,
            head_sha=head_sha,
            role=role,
            now=now,
            pending_seconds=pending_seconds,
        ).pending
    }


def _classify_attempt(
    repo_root: Path,
    attempt: _ReviewAttempt,
    *,
    head_sha: str,
    now: float | None,
    pending_seconds: int,
    holder_alive: Callable[[int], bool] | None,
) -> ReviewerLivenessProjection:
    log_path = attempt.log_path
    exit_code = _exit_code(log_path)
    if exit_code is not None:
        terminal_failed = exit_code != 0
        return ReviewerLivenessProjection(
            pr_number=attempt.pr_number,
            head_sha=head_sha,
            role=attempt.role,
            round_number=attempt.round_number,
            pending=False,
            terminal=True,
            terminal_success=exit_code == 0,
            terminal_failed=terminal_failed,
            redispatchable=terminal_failed,
            reason="terminal_failed" if terminal_failed else "terminal_success",
            prompt_path=attempt.prompt_path if attempt.prompt_path.exists() else None,
            log_path=log_path if log_path.exists() else None,
        )
    holder_pid = _live_spawn_holder_pid(repo_root, attempt, holder_alive=holder_alive)
    if holder_pid is not None:
        return ReviewerLivenessProjection(
            pr_number=attempt.pr_number,
            head_sha=head_sha,
            role=attempt.role,
            round_number=attempt.round_number,
            pending=True,
            terminal=False,
            terminal_success=False,
            terminal_failed=False,
            redispatchable=False,
            reason="spawn_holder_alive",
            prompt_path=attempt.prompt_path if attempt.prompt_path.exists() else None,
            log_path=log_path if log_path.exists() else None,
            holder_pid=holder_pid,
        )
    if _log_is_fresh(log_path, now=now, pending_seconds=pending_seconds):
        return ReviewerLivenessProjection(
            pr_number=attempt.pr_number,
            head_sha=head_sha,
            role=attempt.role,
            round_number=attempt.round_number,
            pending=True,
            terminal=False,
            terminal_success=False,
            terminal_failed=False,
            redispatchable=False,
            reason="fresh_log",
            prompt_path=attempt.prompt_path if attempt.prompt_path.exists() else None,
            log_path=log_path if log_path.exists() else None,
        )
    return ReviewerLivenessProjection(
        pr_number=attempt.pr_number,
        head_sha=head_sha,
        role=attempt.role,
        round_number=attempt.round_number,
        pending=False,
        terminal=False,
        terminal_success=False,
        terminal_failed=False,
        redispatchable=True,
        reason="stale_without_live_holder",
        prompt_path=attempt.prompt_path if attempt.prompt_path.exists() else None,
        log_path=log_path if log_path.exists() else None,
    )


def _review_attempts(repo_root: Path, *, pr_number: int, role: str) -> list[_ReviewAttempt]:
    prompts_dir = repo_root / ".refactor-loop" / "prompts"
    logs_dir = repo_root / ".refactor-loop" / "logs"
    rounds: set[int] = set()
    for directory in (prompts_dir, logs_dir):
        if not directory.exists():
            continue
        for path in directory.glob(f"{REVIEW_TASK_PREFIX}{pr_number}-{role}-r*.*"):
            match = REVIEW_ATTEMPT_RE.fullmatch(path.name)
            if match and int(match.group(1)) == pr_number and match.group(2) == role:
                rounds.add(int(match.group(3)))
    attempts: list[_ReviewAttempt] = []
    for round_number in sorted(rounds):
        prompt_path = prompts_dir / f"{REVIEW_TASK_PREFIX}{pr_number}-{role}-r{round_number}.md"
        log_path = logs_dir / f"{REVIEW_TASK_PREFIX}{pr_number}-{role}-r{round_number}.log"
        attempts.append(
            _ReviewAttempt(
                pr_number=pr_number,
                role=role,
                round_number=round_number,
                prompt_path=prompt_path,
                log_path=log_path,
                prompt_head=reviewed_head_sha_from_file(prompt_path),
                log_head=reviewed_head_sha_from_file(log_path),
            )
        )
    return attempts


def reviewed_head_sha_from_file(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = REVIEW_HEAD_RE.search(text)
    return match.group(1) if match else ""


def _exit_code(path: Path) -> int | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        text = line.strip()
        if not text.startswith("EXIT="):
            continue
        try:
            return int(text.removeprefix("EXIT="))
        except ValueError:
            return 1
    return None


def _log_is_fresh(path: Path, *, now: float | None, pending_seconds: int) -> bool:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    now_value = now if now is not None else time.time()
    return now_value - mtime < pending_seconds


def _live_spawn_holder_pid(
    repo_root: Path,
    attempt: _ReviewAttempt,
    *,
    holder_alive: Callable[[int], bool] | None,
) -> int | None:
    task_id = safe_task_id_from_task(f"{REVIEW_TASK_PREFIX}{attempt.pr_number}-{attempt.role}-r{attempt.round_number}")
    lock_path = repo_root / ".refactor-loop" / "locks" / "spawn-tasks" / f"{task_id}.lock"
    try:
        metadata = read_spawn_task_lock_metadata(lock_path)
    except (FileNotFoundError, TaskSpawnClaimMetadataError):
        return None
    if metadata.task_id != task_id:
        return None
    try:
        lock_log = metadata.log_path.resolve()
        attempt_log = attempt.log_path.resolve()
    except OSError:
        return None
    if lock_log != attempt_log:
        return None
    alive = holder_alive(metadata.pid) if holder_alive is not None else spawn_task_holder_alive(metadata)
    return metadata.pid if alive else None
