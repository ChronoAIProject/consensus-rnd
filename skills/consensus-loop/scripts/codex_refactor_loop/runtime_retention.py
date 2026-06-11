"""Runtime retention for skill-private generated artifacts."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .active_controller import require_active_controller
from .context import LoopContext, LoopContextError
from .worker_markers import extract_standalone_marker


RETENTION_TTL_HOURS = 24
PENDING_EVENTS_MAX_LINES = 2000
RETENTION_PLAN_PATH = Path(".refactor-loop") / "state" / "runtime-retention-plan.json"
GENERATED_FILE_ROOTS = ("logs", "prompts", "runs")
GENERATED_FILE_SUFFIXES = (".json", ".jsonl", ".log", ".md", ".txt")
GENERATED_FILE_PROOF_TRUTHS = (
    "generated_file",
    "ttl_expired",
    "no_in_flight",
    "no_open_actionable",
    "no_pending_intent",
    "no_unconsumed_marker",
    "no_recovery_surface",
)
WORKER_LOG_PREFIXES = (
    "audit-iter-",
    "fix-",
    "implement-",
    "meta-judge-",
    "phase9-",
    "remote-ci-fix-",
    "review-",
    "solver-",
    "test-add-",
    "triage-",
    "verify-",
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

    now_value = time.time() if now is None else now
    diagnostics: list[str] = []
    compacted = _compact_pending_events(refactor_loop / ".controller-pending-events.log")
    _write_generated_file_plan(repo_real, now=now_value, diagnostics=diagnostics)
    plan = _read_retention_plan(repo_real / RETENTION_PLAN_PATH, diagnostics=diagnostics)
    deleted, kept = _delete_planner_generated_files(repo_real, plan.generated_files, now=now_value, diagnostics=diagnostics)
    runner = command_runner or _run_git
    removed = _remove_planner_stale_worktrees(repo_real, plan.stale_worktrees, command_runner=runner, diagnostics=diagnostics)
    pruned = False
    if removed:
        prune = runner(["git", "-C", str(repo_real), "worktree", "prune"])
        pruned = prune.returncode == 0
        if not pruned:
            diagnostics.append(_diagnostic(repo_real, "worktree_prune_failed", code=prune.returncode, stderr=prune.stderr))
    return RuntimeRetentionResult(True, deleted, kept, compacted, removed, pruned, refactor_loop, False, tuple(diagnostics))


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


def _delete_planner_generated_files(
    repo_root: Path,
    generated_files: Sequence[Any],
    *,
    now: float,
    diagnostics: list[str],
) -> tuple[int, int]:
    deleted = 0
    kept = 0
    for entry_no, item in enumerate(generated_files):
        path = _eligible_generated_file_path(repo_root, item, entry_no=entry_no, now=now, diagnostics=diagnostics)
        if path is None:
            kept += 1
            continue
        try:
            path.unlink()
        except OSError as exc:
            diagnostics.append(_diagnostic(path, "generated_file_delete_failed", entry=entry_no, error=exc))
            kept += 1
            continue
        deleted += 1
    return deleted, kept


def _write_generated_file_plan(repo_root: Path, *, now: float, diagnostics: list[str]) -> None:
    plan_path = repo_root / RETENTION_PLAN_PATH
    plan = _read_retention_plan_payload(plan_path, diagnostics=diagnostics)
    generated_files = _produce_generated_file_plan(repo_root, now=now, diagnostics=diagnostics)
    plan["kind"] = "RuntimeRetentionPlan"
    plan["generated_files"] = generated_files
    try:
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=plan_path.parent,
            prefix=f".{plan_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(plan, handle, sort_keys=True)
            handle.write("\n")
        os.replace(temp_path, plan_path)
    except OSError as exc:
        diagnostics.append(_diagnostic(plan_path, "generated_file_plan_write_failed", error=exc))
        try:
            temp_path.unlink()
        except (NameError, OSError):
            pass


def _read_retention_plan_payload(path: Path, *, diagnostics: list[str]) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"kind": "RuntimeRetentionPlan"}
    except json.JSONDecodeError as exc:
        diagnostics.append(_diagnostic(path, "plan_json_invalid", line=exc.lineno, column=exc.colno))
        return {"kind": "RuntimeRetentionPlan"}
    except OSError as exc:
        diagnostics.append(_diagnostic(path, "plan_read_failed", error=exc))
        return {"kind": "RuntimeRetentionPlan"}
    if not isinstance(raw, dict):
        diagnostics.append(_diagnostic(path, "plan_shape_invalid", got=type(raw).__name__))
        return {"kind": "RuntimeRetentionPlan"}
    if raw.get("kind") != "RuntimeRetentionPlan":
        diagnostics.append(_diagnostic(path, "plan_kind_invalid", got=raw.get("kind")))
        return {"kind": "RuntimeRetentionPlan"}
    generated_files = raw.get("generated_files")
    if generated_files is not None and not isinstance(generated_files, list):
        diagnostics.append(_diagnostic(path, "generated_files_invalid", got=type(generated_files).__name__))
    return raw


def _produce_generated_file_plan(repo_root: Path, *, now: float, diagnostics: list[str]) -> list[dict[str, Any]]:
    planned: list[dict[str, Any]] = []
    refactor_loop = repo_root / ".refactor-loop"
    cutoff = now - (RETENTION_TTL_HOURS * 60 * 60)
    for root_name in GENERATED_FILE_ROOTS:
        root = refactor_loop / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            item = _generated_file_plan_item(repo_root, path, cutoff=cutoff, diagnostics=diagnostics)
            if item is not None:
                planned.append(item)
    return planned


def _generated_file_plan_item(
    repo_root: Path,
    path: Path,
    *,
    cutoff: float,
    diagnostics: list[str],
) -> dict[str, Any] | None:
    if path.is_symlink():
        return None
    try:
        file_stat = path.stat(follow_symlinks=False)
    except OSError as exc:
        diagnostics.append(_diagnostic(path, "generated_file_plan_stat_failed", error=exc))
        return None
    if not stat.S_ISREG(file_stat.st_mode):
        return None
    if path.suffix not in GENERATED_FILE_SUFFIXES:
        return None
    if file_stat.st_mtime > cutoff:
        return None
    rel = path.relative_to(repo_root)
    if _pending_reference_reason(repo_root, rel):
        return None
    if _recovery_surface_reason(path, rel):
        return None
    return {
        "path": rel.as_posix(),
        "eligible": True,
        "proof": {key: True for key in GENERATED_FILE_PROOF_TRUTHS},
    }


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


def _eligible_generated_file_path(
    repo_root: Path,
    item: Any,
    *,
    entry_no: int,
    now: float,
    diagnostics: list[str],
) -> Path | None:
    target = item.get("path") if isinstance(item, dict) and isinstance(item.get("path"), str) else f"entry:{entry_no}"
    if not isinstance(item, dict):
        diagnostics.append(_diagnostic(target, "generated_file_item_invalid", entry=entry_no, got=type(item).__name__))
        return None
    if item.get("eligible") is not True:
        diagnostics.append(_diagnostic(target, "generated_file_planner_not_eligible", entry=entry_no))
        return None
    proof = item.get("proof")
    if not isinstance(proof, dict):
        diagnostics.append(_diagnostic(target, "generated_file_invalid_proof", entry=entry_no, got=type(proof).__name__))
        return None
    for key in GENERATED_FILE_PROOF_TRUTHS:
        if proof.get(key) is not True:
            diagnostics.append(_diagnostic(target, f"generated_file_proof_{key}_not_true", entry=entry_no))
            return None
    path = _generated_file_path_from_item(repo_root, item, entry_no=entry_no, diagnostics=diagnostics)
    if path is None:
        return None
    if path.is_symlink():
        diagnostics.append(_diagnostic(path, "generated_file_symlink", entry=entry_no))
        return None
    try:
        file_stat = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        diagnostics.append(_diagnostic(path, "generated_file_missing", entry=entry_no))
        return None
    except OSError as exc:
        diagnostics.append(_diagnostic(path, "generated_file_stat_failed", entry=entry_no, error=exc))
        return None
    if not stat.S_ISREG(file_stat.st_mode):
        diagnostics.append(_diagnostic(path, "generated_file_not_regular", entry=entry_no))
        return None
    cutoff = now - (RETENTION_TTL_HOURS * 60 * 60)
    if file_stat.st_mtime > cutoff:
        diagnostics.append(_diagnostic(path, "generated_file_ttl_not_expired", entry=entry_no))
        return None
    rel = path.relative_to(repo_root)
    pending_reason = _pending_reference_reason(repo_root, rel)
    if pending_reason:
        diagnostics.append(_diagnostic(path, pending_reason, entry=entry_no))
        return None
    recovery_reason = _recovery_surface_reason(path, rel)
    if recovery_reason:
        diagnostics.append(_diagnostic(path, recovery_reason, entry=entry_no))
        return None
    return path


def _generated_file_path_from_item(
    repo_root: Path,
    item: dict[str, Any],
    *,
    entry_no: int,
    diagnostics: list[str],
) -> Path | None:
    raw_path = item.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        diagnostics.append(_diagnostic(f"entry:{entry_no}", "generated_file_invalid_path", entry=entry_no, got=type(raw_path).__name__))
        return None
    rel = Path(raw_path)
    if rel.is_absolute() or ".." in rel.parts or len(rel.parts) < 3:
        diagnostics.append(_diagnostic(raw_path, "generated_file_invalid_path", entry=entry_no))
        return None
    if rel.parts[0] != ".refactor-loop" or rel.parts[1] not in GENERATED_FILE_ROOTS:
        diagnostics.append(_diagnostic(raw_path, "generated_file_disallowed_path", entry=entry_no))
        return None
    path = repo_root / rel
    if path.is_symlink():
        return path
    allowed_root = (repo_root / ".refactor-loop" / rel.parts[1]).resolve()
    try:
        path.resolve(strict=False).relative_to(allowed_root)
    except ValueError:
        diagnostics.append(_diagnostic(raw_path, "generated_file_path_escaped", entry=entry_no))
        return None
    return path


def _pending_reference_reason(repo_root: Path, rel: Path) -> str:
    pending = repo_root / ".refactor-loop" / ".controller-pending-events.log"
    try:
        text = pending.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    except OSError:
        return "generated_file_pending_events_unreadable"
    return "generated_file_pending_reference" if rel.as_posix() in text else ""


def _recovery_surface_reason(path: Path, rel: Path) -> str:
    if rel.parts[1] != "logs" or path.suffix != ".log" or not path.name.startswith(WORKER_LOG_PREFIXES):
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "generated_file_recovery_log_unreadable"
    exit_lines = [line.strip() for line in lines if line.strip().startswith("EXIT=")]
    if not exit_lines:
        return "generated_file_recovery_dead_worker_log"
    if exit_lines[-1] != "EXIT=0":
        return "generated_file_recovery_failed_worker_log"
    try:
        exit_no = max(entry_no for entry_no, line in enumerate(lines) if line.strip() == "EXIT=0")
    except ValueError:
        return "generated_file_recovery_failed_worker_log"
    if not any(extract_standalone_marker(line) for line in lines[:exit_no]):
        return "generated_file_recovery_markerless_log"
    return ""


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
