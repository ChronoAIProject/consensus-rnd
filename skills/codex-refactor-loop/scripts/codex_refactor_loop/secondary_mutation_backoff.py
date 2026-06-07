"""Shared local cooldown for GitHub GraphQL secondary mutation throttles."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


STATE_FILE_NAME = "secondary-mutation-backoff.json"
DEFAULT_COOLDOWN_SECONDS = 600
SECONDARY_MUTATION_RE = re.compile(r"GraphQL:\s*was submitted too quickly\s*\(([A-Za-z][A-Za-z0-9_]*)\)")


@dataclass(frozen=True)
class SecondaryMutationBackoff:
    """Current secondary mutation cooldown projection."""

    active: bool
    mutation: str
    until_epoch: float
    reason: str


def extract_secondary_mutation(text: str) -> str:
    """Return the throttled GraphQL mutation name from gh output, if present."""

    match = SECONDARY_MUTATION_RE.search(text)
    return match.group(1) if match else ""


def currently_backing_off(state_dir: Path, *, now: float | None = None) -> SecondaryMutationBackoff:
    """Read the shared cooldown file and report whether it is still active."""

    payload = _read_state(_state_path(state_dir))
    until = _float(payload.get("until_epoch"))
    now_value = time.time() if now is None else now
    active = until is not None and until > now_value
    return SecondaryMutationBackoff(
        active=active,
        mutation=str(payload.get("mutation") or ""),
        until_epoch=until or 0.0,
        reason=str(payload.get("reason") or ""),
    )


def record_secondary_mutation_backoff(
    state_dir: Path,
    mutation: str,
    *,
    output: str = "",
    now: float | None = None,
    env: Mapping[str, str] | None = None,
) -> SecondaryMutationBackoff:
    """Persist a local cooldown after a GitHub secondary mutation throttle."""

    now_value = time.time() if now is None else now
    cooldown = _cooldown_seconds(os.environ if env is None else env)
    until = now_value + cooldown
    payload = {
        "mutation": mutation,
        "until_epoch": until,
        "recorded_at_epoch": now_value,
        "cooldown_seconds": cooldown,
        "reason": _one_line(output),
    }
    path = _state_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return SecondaryMutationBackoff(active=True, mutation=mutation, until_epoch=until, reason=payload["reason"])


def record_backoff_from_gh_output(
    state_dir: Path,
    stdout: str,
    stderr: str,
    *,
    now: float | None = None,
    env: Mapping[str, str] | None = None,
) -> SecondaryMutationBackoff | None:
    """Detect secondary throttling in gh output and persist a cooldown."""

    output = f"{stdout}\n{stderr}"
    mutation = extract_secondary_mutation(output)
    if not mutation:
        return None
    return record_secondary_mutation_backoff(state_dir, mutation, output=output, now=now, env=env)


def _state_path(state_dir: Path) -> Path:
    return state_dir / STATE_FILE_NAME


def _read_state(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _cooldown_seconds(env: Mapping[str, str]) -> int:
    raw = str(env.get("SECONDARY_MUTATION_BACKOFF_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_COOLDOWN_SECONDS
    try:
        parsed = int(raw)
    except ValueError:
        return DEFAULT_COOLDOWN_SECONDS
    return parsed if parsed > 0 else DEFAULT_COOLDOWN_SECONDS


def _float(value: object) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _one_line(text: str) -> str:
    return " ".join(text.strip().split())[:300]
