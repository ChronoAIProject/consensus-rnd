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
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote

from . import labels as label_catalog
from .context import LoopContext
from .github_budget import graphql_headroom_ok


DEFAULT_TTL_SECONDS = 300
DEFAULT_STALE_MAX_SECONDS = 900
STATE_RELATIVE_PATH = Path(".refactor-loop/state/managed-work-snapshot.json")
LOCK_RELATIVE_PATH = Path(".refactor-loop/locks/managed-work-snapshot.lock")
_SNAPSHOT_SEARCH_QUERY = """
query($searchQuery: String!, $perPage: Int!) {
  search(query: $searchQuery, type: ISSUE, first: $perPage) {
    nodes {
      __typename
      ... on Issue {
        number
        title
        updatedAt
        body
        labels(first: 30) {
          nodes {
            name
          }
        }
      }
      ... on PullRequest {
        number
        title
        updatedAt
        body
        headRefName
        headRefOid
        isDraft
        labels(first: 30) {
          nodes {
            name
          }
        }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class ManagedWorkSnapshotItem:
    kind: str
    number: int
    title: str = ""
    labels: tuple[str, ...] = ()
    head_ref: str | None = None
    head_sha: str = ""
    body: str = ""
    is_draft: bool = False
    state: str = "open"
    updated_at: str = ""
    snapshot_source: str = ""

    @classmethod
    def from_json(cls, row: Mapping[str, Any]) -> "ManagedWorkSnapshotItem | None":
        try:
            number = int(row["number"])
        except (KeyError, TypeError, ValueError):
            return None
        raw_kind = str(row.get("kind") or "issue")
        kind = "PR" if raw_kind == "PR" else "issue"
        raw_labels = row.get("labels")
        labels = tuple(str(label) for label in raw_labels if str(label)) if isinstance(raw_labels, list) else ()
        return cls(
            kind=kind,
            number=number,
            title=str(row.get("title") or ""),
            labels=labels,
            head_ref=(str(row.get("head_ref") or "") or None) if kind == "PR" else None,
            head_sha=str(row.get("head_sha") or "") if kind == "PR" else "",
            body=str(row.get("body") or ""),
            is_draft=bool(row.get("is_draft") is True) if kind == "PR" else False,
            state=str(row.get("state") or "open"),
            updated_at=str(row.get("updated_at") or ""),
            snapshot_source=str(row.get("snapshot_source") or ""),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "number": self.number,
            "title": self.title,
            "labels": list(self.labels),
            "head_ref": self.head_ref,
            "head_sha": self.head_sha,
            "body": self.body,
            "is_draft": self.is_draft,
            "state": self.state,
            "updated_at": self.updated_at,
            "snapshot_source": self.snapshot_source,
        }


@dataclass(frozen=True)
class ManagedWorkSnapshotResult:
    items: tuple[ManagedWorkSnapshotItem, ...]
    loaded_ok: bool
    source: str
    reason: str | None = None
    age_seconds: float | None = None

    def unavailable_diagnostic(self, caller: str, *, target_context: str) -> str:
        reason = self.reason or "unknown"
        age = _format_age_seconds(self.age_seconds)
        return (
            "managed-work-snapshot-unavailable "
            f"caller={caller} reason={reason} source={self.source} "
            f"age_seconds={age} items={len(self.items)} target={target_context}"
        )


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
        self.ttl_seconds = _positive_int(
            ttl_seconds,
            _ctx_host_env_int(ctx, "MANAGED_WORK_SNAPSHOT_TTL_SECONDS", DEFAULT_TTL_SECONDS),
        )
        self.stale_max_seconds = _positive_int(
            stale_max_seconds,
            _ctx_host_env_int(ctx, "MANAGED_WORK_SNAPSHOT_STALE_MAX_SECONDS", DEFAULT_STALE_MAX_SECONDS),
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
            return ManagedWorkSnapshotResult(
                (),
                False,
                "unavailable",
                "graphql-headroom-low",
                self._cache_age_seconds(cached),
            )
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
                return ManagedWorkSnapshotResult((), False, "unavailable", "fetch-failed", self._cache_age_seconds(cached))
            self._write_cache(items)
            return ManagedWorkSnapshotResult(tuple(items), True, "live", None, 0.0)

    def _fetch_open_managed_items(self) -> list[ManagedWorkSnapshotItem] | None:
        if not self.ctx.gh_repo_slug:
            return None
        rows = self._open_managed_rows()
        if rows is None:
            return None
        items: list[ManagedWorkSnapshotItem] = []
        seen: set[tuple[str, int]] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                number = int(row["number"])
            except (KeyError, TypeError, ValueError):
                continue
            typename = str(row.get("__typename") or "")
            kind = "PR" if typename == "PullRequest" else "issue"
            key = (kind, number)
            if key in seen:
                continue
            seen.add(key)
            labels = tuple(
                str(label.get("name") or "")
                for label in ((row.get("labels") or {}).get("nodes") or [])
                if isinstance(label, dict) and label.get("name")
            )
            normalized = label_catalog.normalize_label_set(labels)
            if label_catalog.MANAGED not in normalized.canonical:
                continue
            items.append(
                ManagedWorkSnapshotItem(
                    kind=kind,
                    number=number,
                    title=str(row.get("title") or ""),
                    labels=labels,
                    head_ref=(str(row.get("headRefName") or "") or None) if kind == "PR" else None,
                    head_sha=str(row.get("headRefOid") or "") if kind == "PR" else "",
                    body=str(row.get("body") or ""),
                    is_draft=bool(row.get("isDraft") is True) if kind == "PR" else False,
                    state="open",
                    updated_at=str(row.get("updatedAt") or ""),
                    snapshot_source="github-open-managed-items",
                )
            )
        return sorted(items, key=lambda item: (0 if item.kind == "issue" else 1, item.number))

    def _open_managed_rows(self) -> list[dict[str, Any]] | None:
        rows_by_key: dict[tuple[str, int], dict[str, Any]] = {}
        for query_label in label_catalog.query_labels_for(label_catalog.MANAGED):
            rows = self._graphql_search_rows(f'repo:{self.ctx.gh_repo_slug} is:open label:"{_escape_search_label(query_label)}"')
            if rows is None:
                return self._open_managed_rows_from_rest()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    number = int(row["number"])
                except (KeyError, TypeError, ValueError):
                    continue
                typename = str(row.get("__typename") or "")
                if typename not in {"Issue", "PullRequest"}:
                    continue
                current = rows_by_key.get((typename, number))
                if current is None or str(row.get("updatedAt") or "") > str(current.get("updatedAt") or ""):
                    rows_by_key[(typename, number)] = row
        return list(rows_by_key.values())

    def _open_managed_rows_from_rest(self) -> list[dict[str, Any]] | None:
        rows_by_key: dict[tuple[str, int], dict[str, Any]] = {}
        for query_label in label_catalog.query_labels_for(label_catalog.MANAGED):
            endpoint = (
                f"repos/{self.ctx.gh_repo_slug}/issues?state=open"
                f"&labels={quote(query_label, safe='')}&per_page=100"
            )
            result = self._run(["gh", "api", endpoint])
            if result.returncode != 0 or not result.stdout.strip():
                return None
            try:
                rows = json.loads(result.stdout)
            except json.JSONDecodeError:
                return None
            if not isinstance(rows, list):
                return None
            for row in rows:
                if not isinstance(row, dict):
                    continue
                node = _legacy_rest_row_to_graphql_node(row)
                try:
                    number = int(node["number"])
                except (KeyError, TypeError, ValueError):
                    continue
                typename = str(node.get("__typename") or "")
                if typename not in {"Issue", "PullRequest"}:
                    continue
                if typename == "PullRequest":
                    details = self._pr_details(number)
                    if details is None:
                        return None
                    node.update(
                        {
                            "body": str(details.get("body") or ""),
                            "headRefName": str(details.get("headRefName") or ""),
                            "headRefOid": str(details.get("headRefOid") or ""),
                            "isDraft": bool(details.get("isDraft") is True),
                        }
                    )
                current = rows_by_key.get((typename, number))
                if current is None or str(node.get("updatedAt") or "") > str(current.get("updatedAt") or ""):
                    rows_by_key[(typename, number)] = node
        return list(rows_by_key.values())

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
                "body,headRefName,headRefOid,isDraft",
            ]
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def _graphql_search_rows(self, search_query: str) -> list[dict[str, Any]] | None:
        result = self._run(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={_SNAPSHOT_SEARCH_QUERY}",
                "-f",
                f"searchQuery={search_query}",
                "-F",
                "perPage=100",
            ]
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        if isinstance(data, list):
            return [_legacy_rest_row_to_graphql_node(row) for row in data if isinstance(row, dict)]
        nodes = (((data.get("data") or {}).get("search") or {}).get("nodes") if isinstance(data, dict) else None)
        return nodes if isinstance(nodes, list) else None

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
        age = self._cache_age_seconds(data)
        if data is None or age is None:
            return None
        if age < 0 or age > max_age:
            return None
        items = data.get("items")
        if not isinstance(items, list):
            return None
        normalized = tuple(
            item
            for item in (ManagedWorkSnapshotItem.from_json(row) for row in items if isinstance(row, dict))
            if item is not None
        )
        return ManagedWorkSnapshotResult(normalized, True, source, None, age)

    def _cache_age_seconds(self, data: dict[str, Any] | None) -> float | None:
        if not data:
            return None
        fetched_at = data.get("fetched_at_epoch")
        try:
            return self.now() - float(fetched_at)
        except (TypeError, ValueError):
            return None

    def _write_cache(self, items: Sequence[ManagedWorkSnapshotItem]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "managed-work-snapshot",
            "fetched_at_epoch": self.now(),
            "items": [item.to_json() for item in items],
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


def invalidate_open_managed_work_snapshot(ctx: LoopContext) -> None:
    """Drop the local read-only managed work cache after controller-owned GitHub writes."""
    state_path = ctx.repo_root / STATE_RELATIVE_PATH
    tmp_path = state_path.with_name(f".{state_path.name}.tmp.{os.getpid()}")
    with contextlib.suppress(FileNotFoundError):
        tmp_path.unlink()
    with contextlib.suppress(FileNotFoundError):
        state_path.unlink()


def _ctx_host_env_int(ctx: LoopContext, name: str, default: int) -> int:
    try:
        return int(ctx.host_env.get(name, str(default)))
    except ValueError:
        return default


def _positive_int(value: int | None, default: int) -> int:
    try:
        parsed = int(default if value is None else value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _format_age_seconds(value: float | None) -> str:
    if value is None:
        return "unknown"
    return str(max(0, int(value)))


def _escape_search_label(label: str) -> str:
    return label.replace("\\", "\\\\").replace('"', '\\"')


def _legacy_rest_row_to_graphql_node(row: dict[str, Any]) -> dict[str, Any]:
    node = dict(row)
    node["__typename"] = "PullRequest" if row.get("pull_request") else "Issue"
    if "updatedAt" not in node and "updated_at" in row:
        node["updatedAt"] = row.get("updated_at")
    labels = row.get("labels")
    if isinstance(labels, list):
        node["labels"] = {"nodes": labels}
    return node
