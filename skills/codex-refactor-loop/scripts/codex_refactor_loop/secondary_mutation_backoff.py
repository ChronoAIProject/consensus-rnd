"""Secondary GitHub content-creation backoff state."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from .context import LoopContext


STATE_RELATIVE_PATH = Path(".refactor-loop/state/secondary-mutation-backoff.json")
CONTENT_CREATION_KEY = "contentCreation"
DEFAULT_BACKOFF_SECONDS = 900
SECONDARY_LIMIT_NEEDLES = (
    "you have exceeded a secondary rate limit",
    "temporarily blocked from content creation",
)


def is_secondary_content_creation_failure(result: subprocess.CompletedProcess[str]) -> bool:
    if result.returncode == 0:
        return False
    text = f"{result.stderr}\n{result.stdout}".lower()
    return any(needle in text for needle in SECONDARY_LIMIT_NEEDLES)


def record_content_creation_backoff(
    ctx: LoopContext,
    operation: str,
    result: subprocess.CompletedProcess[str],
    *,
    now: float | None = None,
    backoff_seconds: int = DEFAULT_BACKOFF_SECONDS,
) -> bool:
    if not is_secondary_content_creation_failure(result):
        return False
    state_path = ctx.repo_root / STATE_RELATIVE_PATH
    state = _read_state(state_path)
    current = time.time() if now is None else float(now)
    until = int(current + max(1, int(backoff_seconds)))
    state[CONTENT_CREATION_KEY] = {
        "until_epoch": until,
        "operation": operation,
        "reason": "secondary-content-creation-limit",
        "not_live_state_fact_source": True,
        "not_host_production_ssot": True,
        "no_lifecycle_authority": True,
    }
    _write_state(state_path, state)
    return True


def _read_state(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
