"""Canonical implement-attempt lifecycle classification."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .worker_markers import read_worker_terminal_marker

IMPLEMENT_DONE_OK_RE = re.compile(r"^IMPLEMENT_DONE:.+:ok$")
IMPLEMENT_DONE_TERMINAL_NON_OK_RE = re.compile(r"^IMPLEMENT_DONE:.+:(?:partial|blocked)$")
IMPLEMENT_LOG_RE = re.compile(r"^implement-(?P<cluster>[A-Za-z0-9._-]+)\.log$")


CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class ImplementAttemptState:
    status: str
    reason: str = ""
    marker: str = ""
    head_ref: str = ""
    worktree: Path | None = None

    @property
    def in_flight(self) -> bool:
        return self.status == "in_flight"

    @property
    def publish_ready(self) -> bool:
        return self.status == "publish_ready"

    @property
    def redispatch(self) -> bool:
        return self.status == "redispatch"

    @property
    def refresh_needed(self) -> bool:
        return self.status == "refresh_needed"

    @property
    def terminal_non_ok(self) -> bool:
        return self.status == "terminal_non_ok"


def classify_implement_attempt(
    *,
    repo_root: Path,
    action: Mapping[str, object] | None = None,
    log_path: Path | None = None,
    integration_branch: str = "",
    command_runner: CommandRunner | None = None,
) -> ImplementAttemptState:
    action = action or {}
    log_path = log_path or canonical_implement_log_path(repo_root, action)
    if not is_implement_log(log_path):
        if not log_path.exists():
            return ImplementAttemptState("redispatch", "log_absent")
        return ImplementAttemptState("in_flight", "non_implement_log_exists")
    if not log_path.exists():
        return ImplementAttemptState("redispatch", "log_absent")
    marker_read = read_worker_terminal_marker(log_path)
    if marker_read.reason == "log_unreadable":
        return ImplementAttemptState("in_flight", "log_unreadable")
    if marker_read.reason == "missing_exit_zero":
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ImplementAttemptState("in_flight", "log_unreadable")
        exit_line = terminal_exit_line(lines)
        if exit_line is None:
            return ImplementAttemptState("in_flight", "no_terminal_exit")
        return ImplementAttemptState("redispatch", "nonzero_exit")
    if IMPLEMENT_DONE_TERMINAL_NON_OK_RE.fullmatch(marker_read.marker):
        return ImplementAttemptState("terminal_non_ok", "terminal_non_ok", marker=marker_read.marker)
    marker = marker_read.marker if IMPLEMENT_DONE_OK_RE.fullmatch(marker_read.marker) else ""
    if not marker and marker_read.reason == "duplicate_or_conflicting_log_marker":
        marker = _implement_run_artifact_done_marker(log_path)
    if not marker:
        return ImplementAttemptState("redispatch", "markerless")
    identity = canonical_implementation_identity(repo_root, action, marker)
    if identity is None:
        return ImplementAttemptState("redispatch", "noncanonical_identity")
    head_ref, worktree = identity
    if not worktree.is_dir():
        return ImplementAttemptState("redispatch", "worktree_missing", marker=marker, head_ref=head_ref, worktree=worktree)
    runner = command_runner or _subprocess_runner
    branch = runner(["git", "-C", str(worktree), "rev-parse", "--abbrev-ref", "HEAD"])
    if branch.returncode != 0 or branch.stdout.strip() != head_ref:
        return ImplementAttemptState("redispatch", "noncanonical_branch", marker=marker, head_ref=head_ref, worktree=worktree)
    if integration_branch:
        merge_base = runner(["git", "-C", str(worktree), "merge-base", "HEAD", f"origin/{integration_branch}"])
        current = runner(["git", "-C", str(worktree), "rev-parse", "--verify", f"origin/{integration_branch}"])
        if merge_base.returncode != 0 or current.returncode != 0:
            return ImplementAttemptState("redispatch", "base_unavailable", marker=marker, head_ref=head_ref, worktree=worktree)
        if merge_base.stdout.strip() != current.stdout.strip():
            return ImplementAttemptState("refresh_needed", "stale_base", marker=marker, head_ref=head_ref, worktree=worktree)
    status = runner(["git", "-C", str(worktree), "status", "--porcelain"])
    if status.returncode != 0:
        return ImplementAttemptState("redispatch", "diff_unavailable", marker=marker, head_ref=head_ref, worktree=worktree)
    if not status.stdout.strip():
        diff = runner(["git", "-C", str(worktree), "diff", "--quiet"])
        if diff.returncode == 0:
            return ImplementAttemptState("redispatch", "empty_scoped_diff", marker=marker, head_ref=head_ref, worktree=worktree)
        if diff.returncode != 1:
            return ImplementAttemptState("redispatch", "diff_unavailable", marker=marker, head_ref=head_ref, worktree=worktree)
    return ImplementAttemptState("publish_ready", marker=marker, head_ref=head_ref, worktree=worktree)


def canonical_implement_log_path(repo_root: Path, action: Mapping[str, object]) -> Path:
    cluster_id = str(action.get("cluster_id") or "").strip()
    return repo_root / ".refactor-loop" / "logs" / f"implement-{cluster_id}.log"


def is_implement_log(path: Path) -> bool:
    return IMPLEMENT_LOG_RE.fullmatch(path.name) is not None


def _implement_run_artifact_done_marker(log_path: Path) -> str:
    if not is_implement_log(log_path):
        return ""
    artifact = log_path.parent.parent / "runs" / f"{log_path.stem}.md"
    try:
        lines = artifact.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    markers = {line.strip().strip("`") for line in lines[-30:] if IMPLEMENT_DONE_OK_RE.fullmatch(line.strip().strip("`"))}
    return markers.pop() if len(markers) == 1 else ""


def terminal_exit_line(lines: list[str]) -> str | None:
    for line in reversed(lines[-20:]):
        stripped = line.strip()
        if stripped.startswith("EXIT="):
            return stripped
    return None


def clean_implement_done_marker(lines: list[str]) -> str:
    for line in lines:
        stripped = line.strip()
        if IMPLEMENT_DONE_OK_RE.fullmatch(stripped):
            return stripped
    return ""


def canonical_implementation_identity(repo_root: Path, action: Mapping[str, object], marker: str) -> tuple[str, Path] | None:
    target = action.get("target_number") or action.get("linked_issue")
    try:
        issue = int(str(target))
    except (TypeError, ValueError):
        issue = _issue_from_marker(marker)
    if issue is None:
        return None
    marker_id = marker.removeprefix("IMPLEMENT_DONE:").removesuffix(":ok").strip(":")
    candidate = marker_id.replace("_", "-").strip("-") or f"issue-{issue}"
    if not candidate:
        return None
    head_ref = f"refactor/iter{issue}-{candidate}"
    worktree = (repo_root / ".worktrees" / f"iter{issue}-{candidate}").resolve()
    return head_ref, worktree


def _issue_from_marker(marker: str) -> int | None:
    marker_id = marker.removeprefix("IMPLEMENT_DONE:").removesuffix(":ok").strip(":")
    match = re.fullmatch(r"issue-?([1-9][0-9]*)", marker_id)
    if not match:
        return None
    return int(match.group(1))


def clear_redispatchable_implement_log(
    *,
    repo_root: Path,
    action: Mapping[str, object] | None = None,
    log_path: Path | None = None,
    integration_branch: str = "",
    command_runner: CommandRunner | None = None,
) -> bool:
    state = classify_implement_attempt(
        repo_root=repo_root,
        action=action,
        log_path=log_path,
        integration_branch=integration_branch,
        command_runner=command_runner,
    )
    target = log_path or canonical_implement_log_path(repo_root, action or {})
    if state.redispatch and target.exists():
        target.unlink(missing_ok=True)
        return True
    return False


def _subprocess_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), capture_output=True, text=True, check=False)
