"""Runtime retention for skill-private runtime state."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .active_controller import require_active_controller
from .context import LoopContext, LoopContextError
from .task_spawn_claim import (
    SAFE_TASK_ID_RE,
    TaskSpawnClaimMetadataError,
    read_spawn_task_lock_metadata,
    spawn_task_log_has_exit_marker,
)


RETENTION_TTL_HOURS = 24
PENDING_EVENTS_MAX_LINES = 2000
RETENTION_PLAN_PATH = Path(".refactor-loop") / "state" / "runtime-retention-plan.json"
SPAWN_TASK_LOCKS_PATH = Path(".refactor-loop") / "locks" / "spawn-tasks"
RETENTION_COMPACT_LOG_PATHS = (
    ".controller-pending-events.log",
    ".concurrency-alert.log",
)


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
    diagnostics: tuple[str, ...] = ()
    removed_spawn_task_locks: int = 0


@dataclass(frozen=True)
class RuntimeRetentionPlan:
    stale_worktrees: tuple[dict[str, Any], ...] = ()
    generated_files: tuple[Any, ...] = ()


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

    diagnostics: list[str] = []
    compacted = _compact_runtime_logs(refactor_loop)
    plan = _read_retention_plan(repo_real / RETENTION_PLAN_PATH, diagnostics=diagnostics)
    removed_spawn_locks = _remove_completed_spawn_task_locks(repo_real, now=now, diagnostics=diagnostics)
    deleted = removed_spawn_locks
    kept = _ignore_legacy_generated_files(plan.generated_files, diagnostics=diagnostics)
    runner = command_runner or _run_git
    removed = _remove_planner_stale_worktrees(repo_real, plan.stale_worktrees, command_runner=runner, diagnostics=diagnostics)
    pruned = False
    if removed:
        prune = runner(["git", "-C", str(repo_real), "worktree", "prune"])
        pruned = prune.returncode == 0
        if not pruned:
            diagnostics.append(_diagnostic(repo_real, "worktree_prune_failed", code=prune.returncode, stderr=prune.stderr))
    return RuntimeRetentionResult(
        True,
        deleted,
        kept,
        compacted,
        removed,
        pruned,
        refactor_loop,
        False,
        tuple(diagnostics),
        removed_spawn_task_locks=removed_spawn_locks,
    )


def _compact_runtime_logs(refactor_loop: Path) -> bool:
    compacted = False
    for name in RETENTION_COMPACT_LOG_PATHS:
        compacted = _compact_runtime_log(refactor_loop / name) or compacted
    return compacted


def _compact_runtime_log(path: Path) -> bool:
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
    plan: Sequence[dict[str, Any]],
    *,
    command_runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]],
    diagnostics: list[str],
) -> int:
    if not plan:
        return 0
    removed = 0
    for entry_no, item in enumerate(plan):
        path = _eligible_worktree_path(repo_root, item, entry_no=entry_no, diagnostics=diagnostics)
        if path is None:
            continue
        if not _git_verification_passes(path, command_runner=command_runner, diagnostics=diagnostics):
            continue
        result = command_runner(["git", "-C", str(repo_root), "worktree", "remove", str(path)])
        if result.returncode == 0:
            removed += 1
        else:
            diagnostics.append(_diagnostic(path, "worktree_remove_failed", code=result.returncode, stderr=result.stderr))
    return removed


def _ignore_legacy_generated_files(generated_files: Sequence[Any], *, diagnostics: list[str]) -> int:
    if generated_files:
        diagnostics.append(_diagnostic("generated_files", "legacy_generated_files_ignored", count=len(generated_files)))
    return len(generated_files)


def _remove_completed_spawn_task_locks(repo_root: Path, *, now: float | None, diagnostics: list[str]) -> int:
    lock_dir = repo_root / SPAWN_TASK_LOCKS_PATH
    try:
        entries = list(lock_dir.iterdir())
    except FileNotFoundError:
        return 0
    except OSError as exc:
        diagnostics.append(_diagnostic(lock_dir, "spawn_task_lock_dir_unreadable", error=exc))
        return 0
    removed = 0
    for lock_path in sorted(entries):
        if _unlinkable_completed_spawn_task_lock(repo_root, lock_path, now=now, diagnostics=diagnostics):
            try:
                _unlink_spawn_task_lock(lock_path)
            except OSError as exc:
                diagnostics.append(_diagnostic(lock_path, "spawn_task_lock_unlink_failed", error=exc))
            else:
                removed += 1
    return removed


def _unlinkable_completed_spawn_task_lock(repo_root: Path, lock_path: Path, *, now: float | None, diagnostics: list[str]) -> bool:
    target = _relative_diagnostic_target(repo_root, lock_path)
    try:
        stat_result = lock_path.lstat()
    except OSError as exc:
        diagnostics.append(_diagnostic(target, "spawn_task_lock_unreadable", error=exc))
        return False
    if not stat.S_ISREG(stat_result.st_mode):
        diagnostics.append(_diagnostic(target, "spawn_task_lock_non_regular"))
        return False
    task_id = lock_path.name.removesuffix(".lock") if lock_path.name.endswith(".lock") else ""
    if not task_id or not SAFE_TASK_ID_RE.fullmatch(task_id) or lock_path.name != f"{task_id}.lock":
        diagnostics.append(_diagnostic(target, "spawn_task_lock_unsafe_basename"))
        return False
    age_seconds = (now if now is not None else _current_time()) - stat_result.st_mtime
    if age_seconds < RETENTION_TTL_HOURS * 60 * 60:
        diagnostics.append(_diagnostic(target, "spawn_task_lock_young"))
        return False
    try:
        metadata = read_spawn_task_lock_metadata(lock_path)
    except TaskSpawnClaimMetadataError as exc:
        diagnostics.append(_diagnostic(target, f"spawn_task_lock_{exc.reason}"))
        return False
    except FileNotFoundError:
        diagnostics.append(_diagnostic(target, "spawn_task_lock_missing"))
        return False
    if metadata.task_id != task_id:
        diagnostics.append(_diagnostic(target, "spawn_task_lock_basename_mismatch", task_id=metadata.task_id))
        return False
    log_path = metadata.log_path
    if not log_path.is_absolute():
        diagnostics.append(_diagnostic(target, "spawn_task_lock_log_path_relative", log_path=log_path))
        return False
    try:
        resolved_log = log_path.resolve(strict=False)
        resolved_log.relative_to((repo_root / ".refactor-loop" / "logs").resolve())
    except (OSError, ValueError):
        diagnostics.append(_diagnostic(target, "spawn_task_lock_log_path_escaped", log_path=log_path))
        return False
    if not resolved_log.is_file():
        diagnostics.append(_diagnostic(target, "spawn_task_lock_log_missing", log_path=resolved_log))
        return False
    try:
        has_exit = spawn_task_log_has_exit_marker(resolved_log)
    except RuntimeError as exc:
        diagnostics.append(_diagnostic(target, "spawn_task_lock_log_unreadable", log_path=resolved_log, error=exc))
        return False
    if not has_exit:
        diagnostics.append(_diagnostic(target, "spawn_task_lock_log_not_terminal", log_path=resolved_log))
        return False
    return True


def _unlink_spawn_task_lock(lock_path: Path) -> None:
    os.unlink(lock_path)


def _read_retention_plan(path: Path, *, diagnostics: list[str]) -> RuntimeRetentionPlan:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return RuntimeRetentionPlan()
    except json.JSONDecodeError as exc:
        diagnostics.append(_diagnostic(path, "plan_json_invalid", line=exc.lineno, column=exc.colno))
        return RuntimeRetentionPlan()
    except OSError as exc:
        diagnostics.append(_diagnostic(path, "plan_read_failed", error=exc))
        return RuntimeRetentionPlan()
    if not isinstance(raw, dict):
        diagnostics.append(_diagnostic(path, "plan_shape_invalid", got=type(raw).__name__))
        return RuntimeRetentionPlan()
    if raw.get("kind") != "RuntimeRetentionPlan":
        diagnostics.append(_diagnostic(path, "plan_kind_invalid", got=raw.get("kind")))
        return RuntimeRetentionPlan()
    worktrees = raw.get("stale_worktrees")
    if worktrees is None:
        worktrees = []
    if not isinstance(worktrees, list):
        diagnostics.append(_diagnostic(path, "stale_worktrees_invalid", got=type(worktrees).__name__))
        worktrees = []
    generated_files = raw.get("generated_files")
    if generated_files is None:
        generated_files = []
    if not isinstance(generated_files, list):
        diagnostics.append(_diagnostic(path, "generated_files_invalid", got=type(generated_files).__name__))
        generated_files = []
    stale_worktrees: list[dict[str, Any]] = []
    for entry_no, item in enumerate(worktrees):
        if isinstance(item, dict):
            stale_worktrees.append(item)
        else:
            diagnostics.append(_diagnostic(path, "invalid_item", entry=entry_no, got=type(item).__name__))
    return RuntimeRetentionPlan(tuple(stale_worktrees), tuple(generated_files))


def _eligible_worktree_path(
    repo_root: Path,
    item: dict[str, Any],
    *,
    entry_no: int,
    diagnostics: list[str],
) -> Path | None:
    target = item.get("path") if isinstance(item.get("path"), str) else f"entry:{entry_no}"
    if item.get("eligible") is not True:
        diagnostics.append(_diagnostic(target, "planner_not_eligible", entry=entry_no))
        return None
    proof = item.get("proof")
    if not isinstance(proof, dict):
        diagnostics.append(_diagnostic(target, "invalid_proof", entry=entry_no, got=type(proof).__name__))
        return None
    required_truths = (
        "no_in_flight",
        "no_open_issue_or_pr",
        "no_dirty",
        "no_local_ahead",
        "merged_or_missing_safe",
    )
    for key in required_truths:
        if proof.get(key) is not True:
            diagnostics.append(_diagnostic(target, f"proof_{key}_not_true", entry=entry_no))
            return None
    raw_path = item.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        diagnostics.append(_diagnostic(target, "invalid_path", entry=entry_no, got=type(raw_path).__name__))
        return None
    rel = Path(raw_path)
    if rel.is_absolute() or ".." in rel.parts or len(rel.parts) != 2 or rel.parts[0] != ".worktrees":
        diagnostics.append(_diagnostic(raw_path, "invalid_path", entry=entry_no))
        return None
    path = (repo_root / rel).resolve()
    try:
        path.relative_to((repo_root / ".worktrees").resolve())
    except ValueError:
        diagnostics.append(_diagnostic(raw_path, "path_escaped", entry=entry_no))
        return None
    return path


def _git_verification_passes(
    path: Path,
    *,
    command_runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]],
    diagnostics: list[str],
) -> bool:
    if not path.is_dir():
        diagnostics.append(_diagnostic(path, "worktree_missing"))
        return False
    dirty = command_runner(["git", "-C", str(path), "status", "--porcelain"])
    if dirty.returncode != 0:
        diagnostics.append(_diagnostic(path, "git_status_failed", code=dirty.returncode, stderr=dirty.stderr))
        return False
    if dirty.stdout.strip():
        diagnostics.append(_diagnostic(path, "dirty_status", stdout=dirty.stdout))
        return False
    ahead = command_runner(["git", "-C", str(path), "rev-list", "--count", "@{upstream}..HEAD"])
    if ahead.returncode != 0:
        diagnostics.append(_diagnostic(path, "git_ahead_failed", code=ahead.returncode, stderr=ahead.stderr))
        return False
    if ahead.stdout.strip() not in ("", "0"):
        diagnostics.append(_diagnostic(path, "local_ahead", count=ahead.stdout.strip()))
        return False
    return True


def _run_git(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), capture_output=True, text=True, check=False)


def _current_time() -> float:
    return time.time()


def _relative_diagnostic_target(repo_root: Path, path: Path) -> Path | str:
    try:
        return path.resolve(strict=False).relative_to(repo_root)
    except ValueError:
        return path


def _diagnostic(target: Path | str, reason: str, **facts: object) -> str:
    parts = [f"target={_one_line(target)}", f"reason={reason}"]
    for key, value in facts.items():
        parts.append(f"{key}={_one_line(value)}")
    return " ".join(parts)


def _one_line(value: object) -> str:
    text = str(value).replace("\n", "\\n").replace("\r", "\\r")
    if len(text) > 160:
        return text[:157] + "..."
    return text


def _summary(result: RuntimeRetentionResult) -> str:
    suffix = " missing=true" if result.missing else ""
    diagnostics = "none" if not result.diagnostics else " | ".join(result.diagnostics)
    return (
        f"runtime_retention: enabled={str(result.enabled).lower()} ttl_hours={RETENTION_TTL_HOURS} "
        f"deleted={result.deleted} kept={result.kept} compacted_events={str(result.compacted_events).lower()} "
        f"removed_worktrees={result.removed_worktrees} pruned_worktrees={str(result.pruned_worktrees).lower()} "
        f"removed_spawn_task_locks={result.removed_spawn_task_locks} "
        f"target={result.target}{suffix} diagnostics={diagnostics}"
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
