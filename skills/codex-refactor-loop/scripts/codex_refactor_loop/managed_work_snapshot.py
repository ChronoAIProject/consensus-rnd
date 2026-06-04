"""Read-only open managed work snapshot cache."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import quote

from . import labels as label_catalog
from .context import LoopContext
from .github_budget import graphql_headroom_ok


DEFAULT_TTL_SECONDS = 300
DEFAULT_STALE_MAX_SECONDS = 900
STATE_RELATIVE_PATH = Path(".refactor-loop/state/managed-work-snapshot.json")
LOCK_RELATIVE_PATH = Path(".refactor-loop/locks/managed-work-snapshot.lock")


@dataclass(frozen=True)
class ManagedWorkSnapshotResult:
    items: tuple[dict[str, Any], ...]
    loaded_ok: bool
    source: str
    reason: str | None = None
    age_seconds: float | None = None


class ManagedWorkSnapshot:
    def __init__(
        self,
        ctx: LoopContext,
        *,
        ttl_seconds: int | None = None,
        stale_max_seconds: int | None = None,
        runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.ctx = ctx
        self.repo_root = ctx.repo_root
        self.state_path = self.repo_root / STATE_RELATIVE_PATH
        self.lock_path = self.repo_root / LOCK_RELATIVE_PATH
        self.ttl_seconds = _positive_int(ttl_seconds, _env_int("MANAGED_WORK_SNAPSHOT_TTL_SECONDS", DEFAULT_TTL_SECONDS))
        self.stale_max_seconds = _positive_int(
            stale_max_seconds,
            _env_int("MANAGED_WORK_SNAPSHOT_STALE_MAX_SECONDS", DEFAULT_STALE_MAX_SECONDS),
        )
        self.runner = runner
        self.now = now or time.time

    def load(self) -> ManagedWorkSnapshotResult:
        cached = self._read_cache()
        fresh = self._cached_result_if_usable(cached, max_age=self.ttl_seconds, source="cache:fresh")
        if fresh is not None:
            return fresh
        if not graphql_headroom_ok(cwd=self.repo_root, env=self.ctx.env_for_subprocess()):
            stale = self._cached_result_if_usable(cached, max_age=self.stale_max_seconds, source="cache:stale")
            if stale is not None:
                return stale
            return ManagedWorkSnapshotResult((), False, "unavailable", "graphql-headroom-low")
        with self._lock():
            cached = self._read_cache()
            fresh = self._cached_result_if_usable(cached, max_age=self.ttl_seconds, source="cache:fresh")
            if fresh is not None:
                return fresh
            items = self._fetch_open_managed_items()
            if items is None:
                stale = self._cached_result_if_usable(cached, max_age=self.stale_max_seconds, source="cache:stale")
                if stale is not None:
                    return stale
                return ManagedWorkSnapshotResult((), False, "unavailable", "fetch-failed")
            self._write_cache(items)
            return ManagedWorkSnapshotResult(tuple(items), True, "live", None, 0.0)

    def _fetch_open_managed_items(self) -> list[dict[str, Any]] | None:
        if not self.ctx.gh_repo_slug:
            return None
        rows = self._open_managed_issue_rows()
        if rows is None:
            return None
        items: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                number = int(row["number"])
            except (KeyError, TypeError, ValueError):
                continue
            kind = "PR" if row.get("pull_request") else "issue"
            key = (kind, number)
            if key in seen:
                continue
            seen.add(key)
            labels = tuple(
                str(label.get("name") or "")
                for label in row.get("labels", [])
                if isinstance(label, dict) and label.get("name")
            )
            normalized = label_catalog.normalize_label_set(labels)
            if label_catalog.MANAGED not in normalized.canonical:
                continue
            body = ""
            head_ref = None
            head_sha = ""
            if kind == "PR":
                details = self._pr_details(number)
                if details is None:
                    return None
                body = str(details.get("body") or "")
                head_ref = str(details.get("headRefName") or "") or None
                head_sha = str(details.get("headRefOid") or "")
            items.append(
                {
                    "kind": kind,
                    "number": number,
                    "title": str(row.get("title") or ""),
                    "labels": list(labels),
                    "head_ref": head_ref,
                    "head_sha": head_sha,
                    "body": body,
                    "state": "open",
                    "updated_at": str(row.get("updated_at") or ""),
                    "snapshot_source": "github-open-managed-items",
                }
            )
        return sorted(items, key=lambda item: (0 if item["kind"] == "issue" else 1, int(item["number"])))

    def _open_managed_issue_rows(self) -> list[dict[str, Any]] | None:
        rows_by_key: dict[tuple[bool, int], dict[str, Any]] = {}
        for query_label in label_catalog.query_labels_for(label_catalog.MANAGED):
            endpoint = (
                f"repos/{self.ctx.gh_repo_slug}/issues?state=open"
                f"&labels={quote(query_label, safe='')}&per_page=100"
            )
            rows = self._gh_api_json(endpoint)
            if rows is None:
                return None
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    number = int(row["number"])
                except (KeyError, TypeError, ValueError):
                    continue
                is_pr = bool(row.get("pull_request"))
                current = rows_by_key.get((is_pr, number))
                if current is None or str(row.get("updated_at") or "") > str(current.get("updated_at") or ""):
                    rows_by_key[(is_pr, number)] = row
        return list(rows_by_key.values())

    def _gh_api_json(self, endpoint: str) -> list[dict[str, Any]] | None:
        result = self._run(["gh", "api", endpoint])
        if result.returncode != 0 or not result.stdout.strip():
            return None
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, list) else None

    def _pr_details(self, number: int) -> dict[str, Any] | None:
        result = self._run(
            [
                "gh",
                "pr",
                "view",
                str(number),
                "--repo",
                str(self.ctx.gh_repo_slug),
                "--json",
                "body,headRefName,headRefOid",
            ]
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def _run(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if self.runner is not None:
            return self.runner(command)
        return subprocess.run(
            list(command),
            cwd=self.repo_root,
            env=self.ctx.env_for_subprocess(),
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

    def _read_cache(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _cached_result_if_usable(self, data: dict[str, Any] | None, *, max_age: int, source: str) -> ManagedWorkSnapshotResult | None:
        if not data:
            return None
        fetched_at = data.get("fetched_at_epoch")
        try:
            age = self.now() - float(fetched_at)
        except (TypeError, ValueError):
            return None
        if age < 0 or age > max_age:
            return None
        items = data.get("items")
        if not isinstance(items, list):
            return None
        normalized = tuple(item for item in items if isinstance(item, dict))
        return ManagedWorkSnapshotResult(normalized, True, source, None, age)

    def _write_cache(self, items: list[dict[str, Any]]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "managed-work-snapshot",
            "fetched_at_epoch": self.now(),
            "items": items,
            "not_live_state_fact_source": True,
            "not_host_production_ssot": True,
            "no_lifecycle_authority": True,
        }
        tmp = self.state_path.with_name(f".{self.state_path.name}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_path)

    @contextlib.contextmanager
    def _lock(self) -> Any:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("w", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield


def load_open_managed_work_snapshot(ctx: LoopContext) -> ManagedWorkSnapshotResult:
    return ManagedWorkSnapshot(ctx).load()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _positive_int(value: int | None, default: int) -> int:
    try:
        parsed = int(default if value is None else value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
