"""Runtime retention for skill-private generated artifacts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .active_controller import require_active_controller
from .context import LoopContext, LoopContextError


RETENTION_TTL_HOURS = 24
PENDING_EVENTS_MAX_LINES = 2000
RETENTION_PLAN_PATH = Path(".refactor-loop") / "state" / "runtime-retention-plan.json"
GENERATED_DIRS = ("logs", "prompts", "runs")
GENERATED_SUFFIXES = (".json", ".log", ".md", ".txt")


@dataclass(frozen=True)
class RuntimeRetentionResult:
    enabled: bool
    deleted: int
    kept: int
    compacted_events: bool
    removed_worktrees: int
    pruned_worktrees: bool
    target: Path
    missing: bool


def runtime_retention_enabled(ctx: LoopContext) -> bool:
    value = (ctx.host_env.get("RUNTIME_RETENTION_ENABLE") or os.environ.get("RUNTIME_RETENTION_ENABLE") or "").strip().lower()
    return value == "true"


def retain_runtime(
    repo_root: Path,
    *,
    enabled: bool = False,
    now: float | None = None,
    command_runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None,
) -> RuntimeRetentionResult:
    repo_real = repo_root.resolve()
    refactor_loop = repo_real / ".refactor-loop"
    if not enabled:
        return RuntimeRetentionResult(False, 0, 0, False, 0, False, refactor_loop, not refactor_loop.is_dir())
    if not refactor_loop.is_dir():
        return RuntimeRetentionResult(True, 0, 0, False, 0, False, refactor_loop, True)

    cutoff = int(now if now is not None else time.time()) - RETENTION_TTL_HOURS * 60 * 60
    deleted, kept = _delete_generated_files(repo_real, cutoff)
    compacted = _compact_pending_events(refactor_loop / ".controller-pending-events.log")
    removed = _remove_planner_stale_worktrees(repo_real, command_runner=command_runner or _run_git)
    pruned = False
    if removed:
        prune = (command_runner or _run_git)(["git", "-C", str(repo_real), "worktree", "prune"])
        pruned = prune.returncode == 0
    return RuntimeRetentionResult(True, deleted, kept, compacted, removed, pruned, refactor_loop, False)


def _delete_generated_files(repo_root: Path, cutoff: int) -> tuple[int, int]:
    refactor_loop = repo_root / ".refactor-loop"
    deleted = 0
    kept = 0
    for dirname in GENERATED_DIRS:
        target_dir = (refactor_loop / dirname).resolve()
        try:
            target_dir.relative_to(refactor_loop.resolve())
        except ValueError as exc:
            raise RuntimeError(f"runtime retention target escaped .refactor-loop: {target_dir}") from exc
        if not target_dir.is_dir():
            continue
        for path in target_dir.iterdir():
            try:
                if path.is_symlink() or not path.is_file():
                    kept += 1
                    continue
                if path.suffix not in GENERATED_SUFFIXES:
                    kept += 1
                    continue
                if int(path.stat().st_mtime) < cutoff:
                    path.unlink(missing_ok=True)
                    deleted += 1
                else:
                    kept += 1
            except OSError:
                kept += 1
    return deleted, kept


def _compact_pending_events(path: Path) -> bool:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    if len(lines) <= PENDING_EVENTS_MAX_LINES:
        return False
    tail = lines[-PENDING_EVENTS_MAX_LINES:]
    # Keep the same-inode file watched by controller Monitor bridges.
    with path.open("r+", encoding="utf-8") as handle:
        handle.seek(0)
        handle.write("\n".join(tail) + "\n")
        handle.truncate()
    return True


def _remove_planner_stale_worktrees(
    repo_root: Path,
    *,
    command_runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]],
) -> int:
    plan = _read_retention_plan(repo_root / RETENTION_PLAN_PATH)
    if not plan:
        return 0
    removed = 0
    for item in plan:
        path = _eligible_worktree_path(repo_root, item)
        if path is None:
            continue
        if not _git_verification_passes(path, command_runner=command_runner):
            continue
        result = command_runner(["git", "-C", str(repo_root), "worktree", "remove", str(path)])
        if result.returncode == 0:
            removed += 1
    return removed


def _read_retention_plan(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, dict):
        return []
    if raw.get("kind") != "RuntimeRetentionPlan":
        return []
    worktrees = raw.get("stale_worktrees")
    if not isinstance(worktrees, list):
        return []
    return [item for item in worktrees if isinstance(item, dict)]


def _eligible_worktree_path(repo_root: Path, item: dict[str, Any]) -> Path | None:
    if item.get("eligible") is not True:
        return None
    proof = item.get("proof")
    if not isinstance(proof, dict):
        return None
    required_truths = (
        "no_in_flight",
        "no_open_issue_or_pr",
        "no_dirty",
        "no_local_ahead",
        "merged_or_missing_safe",
    )
    if any(proof.get(key) is not True for key in required_truths):
        return None
    raw_path = item.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return None
    rel = Path(raw_path)
    if rel.is_absolute() or ".." in rel.parts or len(rel.parts) != 2 or rel.parts[0] != ".worktrees":
        return None
    path = (repo_root / rel).resolve()
    try:
        path.relative_to((repo_root / ".worktrees").resolve())
    except ValueError:
        return None
    return path


def _git_verification_passes(
    path: Path,
    *,
    command_runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]],
) -> bool:
    if not path.is_dir():
        return False
    dirty = command_runner(["git", "-C", str(path), "status", "--porcelain"])
    if dirty.returncode != 0 or dirty.stdout.strip():
        return False
    ahead = command_runner(["git", "-C", str(path), "rev-list", "--count", "@{upstream}..HEAD"])
    if ahead.returncode != 0 or ahead.stdout.strip() not in ("", "0"):
        return False
    return True


def _run_git(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), capture_output=True, text=True, check=False)


def _summary(result: RuntimeRetentionResult) -> str:
    suffix = " missing=true" if result.missing else ""
    return (
        f"runtime_retention: enabled={str(result.enabled).lower()} ttl_hours={RETENTION_TTL_HOURS} "
        f"deleted={result.deleted} kept={result.kept} compacted_events={str(result.compacted_events).lower()} "
        f"removed_worktrees={result.removed_worktrees} pruned_worktrees={str(result.pruned_worktrees).lower()} "
        f"target={result.target}{suffix}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    try:
        ctx = LoopContext.load(cwd=os.getcwd())
    except LoopContextError as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 2
    decision = require_active_controller(ctx, "runtime-retention")
    if not decision.allowed:
        print(f"runtime_retention: enabled=false active_controller=noop:{decision.status} owner={decision.owner_device}")
        return 0
    try:
        result = retain_runtime(ctx.repo_root, enabled=runtime_retention_enabled(ctx))
    except RuntimeError as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 2
    print(_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
