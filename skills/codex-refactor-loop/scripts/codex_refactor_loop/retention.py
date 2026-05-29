"""Daemonless 24 hour log retention for .refactor-loop/logs."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Sequence

from .context import LoopContext, LoopContextError


RETENTION_TTL_HOURS = 24


def retain_logs(repo_root: Path, *, now: float | None = None) -> tuple[int, int, Path, bool]:
    repo_real = repo_root.resolve()
    log_dir = repo_real / ".refactor-loop" / "logs"
    if log_dir != repo_real / ".refactor-loop" / "logs":
        raise RuntimeError(f"log retention target escaped .refactor-loop/logs: {log_dir}")
    if not log_dir.is_dir():
        return 0, 0, log_dir, True
    cutoff = int(now if now is not None else time.time()) - RETENTION_TTL_HOURS * 60 * 60
    deleted = 0
    kept = 0
    for path in log_dir.glob("*.log"):
        try:
            if path.is_symlink() or not path.is_file():
                kept += 1
                continue
            if int(path.stat().st_mtime) < cutoff:
                path.unlink(missing_ok=True)
                deleted += 1
            else:
                kept += 1
        except OSError:
            kept += 1
    return deleted, kept, log_dir, False


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    try:
        ctx = LoopContext.load(cwd=os.getcwd())
    except LoopContextError as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 2
    try:
        deleted, kept, target, missing = retain_logs(ctx.repo_root)
    except RuntimeError as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 2
    suffix = " missing=true" if missing else ""
    print(f"log_retention: ttl_hours={RETENTION_TTL_HOURS} deleted={deleted} kept={kept} target={target}{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
