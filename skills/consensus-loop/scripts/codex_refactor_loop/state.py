"""Shared path and JSON helpers for loop state artifacts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .context import LoopContext


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def dispatch_queue(ctx: LoopContext, priority: str = "p1") -> Path:
    return ctx.paths.dispatch_queue / priority


def pending_events(ctx: LoopContext) -> Path:
    return ctx.paths.pending_events


def heartbeats(ctx: LoopContext) -> Path:
    return ctx.paths.heartbeats


def statusline_snapshot(ctx: LoopContext) -> Path:
    return ctx.paths.statusline_snapshot


def recent_pr_merges(ctx: LoopContext) -> Path:
    return ctx.paths.recent_pr_merges

