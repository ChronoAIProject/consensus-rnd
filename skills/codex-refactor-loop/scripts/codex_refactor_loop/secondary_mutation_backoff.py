"""Secondary GitHub mutation/content-creation backoff state."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .context import LoopContext


STATE_FILE_NAME = "secondary-mutation-backoff.json"
STATE_RELATIVE_PATH = Path(".refactor-loop/state") / STATE_FILE_NAME
CONTENT_CREATION_KEY = "contentCreation"
MUTATION_KEY = "mutationThrottle"
DEFAULT_COOLDOWN_SECONDS = 600
DEFAULT_BACKOFF_SECONDS = 900
SECONDARY_MUTATION_RE = re.compile(r"GraphQL:\s*was submitted too quickly\s*\(([A-Za-z][A-Za-z0-9_]*)\)")
SECONDARY_LIMIT_NEEDLES = (
    "you have exceeded a secondary rate limit",
    "temporarily blocked from content creation",
)


@dataclass(frozen=True)
class SecondaryMutationBackoff:
    """Current secondary mutation cooldown projection."""

    active: bool
    mutation: str
    until_epoch: float
    reason: str


def is_secondary_content_creation_failure(result: subprocess.CompletedProcess[str]) -> bool:
    if result.returncode == 0:
        return False
    text = f"{result.stderr}\n{result.stdout}".lower()
    return any(needle in text for needle in SECONDARY_LIMIT_NEEDLES)


def extract_secondary_mutation(text: str) -> str:
    """Return the throttled GraphQL mutation name from gh output, if present."""

    match = SECONDARY_MUTATION_RE.search(text)
    return match.group(1) if match else ""


def currently_backing_off(state_dir: Path, *, now: float | None = None) -> SecondaryMutationBackoff:
    """Read the shared cooldown file and report whether any known cooldown is active."""

    payload = _read_state(_state_path(state_dir))
    now_value = time.time() if now is None else now
    entries: list[tuple[str, float, str]] = []
    flat_until = _float(payload.get("until_epoch"))
    if flat_until is not None:
        entries.append((str(payload.get("mutation") or ""), flat_until, str(payload.get("reason") or "")))
    mutation_payload = payload.get(MUTATION_KEY)
    if isinstance(mutation_payload, dict):
        mutation_until = _float(mutation_payload.get("until_epoch"))
        if mutation_until is not None:
            entries.append(
                (
                    str(mutation_payload.get("mutation") or ""),
                    mutation_until,
                    str(mutation_payload.get("reason") or ""),
                )
            )
    content_payload = payload.get(CONTENT_CREATION_KEY)
    if isinstance(content_payload, dict):
        content_until = _float(content_payload.get("until_epoch"))
        if content_until is not None:
            entries.append(
                (
                    str(content_payload.get("operation") or CONTENT_CREATION_KEY),
                    content_until,
                    str(content_payload.get("reason") or ""),
                )
            )
    active_entries = [(mutation, until, reason) for mutation, until, reason in entries if until > now_value]
    if not active_entries:
        return SecondaryMutationBackoff(active=False, mutation="", until_epoch=0.0, reason="")
    mutation, until, reason = max(active_entries, key=lambda entry: entry[1])
    return SecondaryMutationBackoff(active=True, mutation=mutation, until_epoch=until, reason=reason)


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
    path = _state_path(state_dir)
    state = _read_state(path)
    state[MUTATION_KEY] = {
        "mutation": mutation,
        "until_epoch": until,
        "recorded_at_epoch": now_value,
        "cooldown_seconds": cooldown,
        "reason": _one_line(output),
        "not_live_state_fact_source": True,
        "not_host_production_ssot": True,
        "no_lifecycle_authority": True,
    }
    state.setdefault("mutation", mutation)
    state.setdefault("until_epoch", until)
    state.setdefault("reason", _one_line(output))
    _write_state(path, state)
    return SecondaryMutationBackoff(active=True, mutation=mutation, until_epoch=until, reason=str(state[MUTATION_KEY]["reason"]))


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


def _state_path(state_dir: Path) -> Path:
    return state_dir / STATE_FILE_NAME


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
