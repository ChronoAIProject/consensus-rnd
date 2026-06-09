"""Shared read-only GitHub graphql budget guard."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


DEFAULT_GRAPHQL_MIN_REMAINING = 500
DEFAULT_GRAPHQL_BUDGET_CACHE_TTL_SECONDS = 25.0


@dataclass
class _BudgetCache:
    checked_at: float = 0.0
    remaining: int | None = None
    cache_key: str = ""


_CACHE = _BudgetCache()


def reset_graphql_budget_cache() -> None:
    """Clear the process-local graphql budget cache for tests."""
    _CACHE.checked_at = 0.0
    _CACHE.remaining = None
    _CACHE.cache_key = ""


def graphql_headroom_ok(
    min_remaining: int | None = None,
    *,
    cache_ttl_seconds: float | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None,
    now: float | None = None,
) -> bool:
    """Return whether the shared GitHub API graphql pool has enough remaining calls.

    The guard is read-only and uses `gh api rate_limit`, whose REST endpoint does
    not consume graphql quota. Failures fail open so a transient GitHub CLI,
    network, or JSON error does not halt controller progress.
    """
    threshold = _int_env("GRAPHQL_BUDGET_MIN_REMAINING", DEFAULT_GRAPHQL_MIN_REMAINING) if min_remaining is None else int(min_remaining)
    ttl = (
        _float_env("GRAPHQL_BUDGET_CACHE_TTL_SECONDS", DEFAULT_GRAPHQL_BUDGET_CACHE_TTL_SECONDS)
        if cache_ttl_seconds is None
        else float(cache_ttl_seconds)
    )
    current = time.time() if now is None else float(now)
    if runner is None and cwd is not None and not _looks_like_git_checkout(cwd):
        return True
    cache_key = _cache_key(cwd=cwd, env=env, threshold=threshold)
    if _CACHE.remaining is not None and _CACHE.cache_key == cache_key and current - _CACHE.checked_at < max(0.0, ttl):
        return _CACHE.remaining >= threshold

    remaining = _read_graphql_remaining(cwd=cwd, env=env, runner=runner)
    if remaining is None:
        return True
    _CACHE.checked_at = current
    _CACHE.remaining = remaining
    _CACHE.cache_key = cache_key
    return remaining >= threshold


def _read_graphql_remaining(
    *,
    cwd: Path | None,
    env: dict[str, str] | None,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None,
) -> int | None:
    command = ["gh", "api", "rate_limit"]
    try:
        if runner is None:
            result = subprocess.run(
                command,
                cwd=str(cwd) if cwd is not None else None,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        else:
            result = runner(command)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    remaining = (((payload.get("resources") or {}).get("graphql") or {}).get("remaining") if isinstance(payload, dict) else None)
    try:
        return int(remaining)
    except (TypeError, ValueError):
        return None


def _looks_like_git_checkout(cwd: Path) -> bool:
    return (cwd / ".git").exists()


def _cache_key(*, cwd: Path | None, env: dict[str, str] | None, threshold: int) -> str:
    repo_hint = ""
    if env:
        repo_hint = env.get("GH_REPO_SLUG") or env.get("GH_REPO") or ""
    cwd_hint = str(cwd.resolve()) if cwd is not None else ""
    return f"{cwd_hint}|{repo_hint}|{threshold}"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default
