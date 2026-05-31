"""Integration sync operation schema for daemon-owned execution.

"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ALLOWED_KINDS = {
    "push-local-ahead",
    "reset-to-remote",
    "adopt-merged-rollup",
    "forward-sync-review-base",
    "continue-resolved-merge",
}
COMMAND_LIKE_FIELDS = {
    "argv",
    "args",
    "shell",
    "command",
    "commands",
    "cmd",
    "git",
    "git_verb",
    "ref",
    "refs",
    "target_ref",
    "target",
    "remote_ref",
}
LEGACY_LIFECYCLE_FIELDS = {"lifecycle" + "_owner", "lifecycle" + "_authority"}
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
SHA_RE = re.compile(r"^[0-9a-f]{7,64}$|^[A-Za-z0-9._/-]+$")


class IntegrationSyncOperationError(ValueError):
    """Raised when an IntegrationSyncOperation artifact is malformed."""


@dataclass(frozen=True)
class IntegrationSyncOperation:
    kind: str
    integration_branch: str
    review_base_branch: str
    worktree_head: str
    expected_remote_sha: str
    evidence: dict[str, Any]
    executor: str = "dev_sync_daemon"
    authority: str = "integration-branch-git-allowlist"
    schema: str = "IntegrationSyncOperation"
    old_rollup_head: str | None = None
    old_rollup_ahead_count: int | None = None
    pr_number: int | None = None
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def _reject_forbidden_fields(data: dict[str, Any]) -> None:
    command_like = sorted(COMMAND_LIKE_FIELDS.intersection(data))
    if command_like:
        raise IntegrationSyncOperationError(f"command-like fields forbidden: {','.join(command_like)}")
    lifecycle = sorted(LEGACY_LIFECYCLE_FIELDS.intersection(data))
    if lifecycle:
        raise IntegrationSyncOperationError(f"legacy lifecycle fields forbidden: {','.join(lifecycle)}")


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise IntegrationSyncOperationError(f"missing or invalid {key}")
    return value.strip()


def _validate_branch(name: str, key: str) -> None:
    if not BRANCH_RE.fullmatch(name) or name.startswith("-") or ".." in name or name.endswith(".lock"):
        raise IntegrationSyncOperationError(f"invalid {key}")


def _validate_shaish(value: str, key: str) -> None:
    if not SHA_RE.fullmatch(value) or value.startswith("-"):
        raise IntegrationSyncOperationError(f"invalid {key}")


def validate_operation_dict(data: dict[str, Any]) -> IntegrationSyncOperation:
    if not isinstance(data, dict):
        raise IntegrationSyncOperationError("operation must be an object")
    _reject_forbidden_fields(data)
    if data.get("schema") != "IntegrationSyncOperation":
        raise IntegrationSyncOperationError("schema must be IntegrationSyncOperation")
    if data.get("executor") != "dev_sync_daemon":
        raise IntegrationSyncOperationError("executor must be dev_sync_daemon")
    if data.get("authority") != "integration-branch-git-allowlist":
        raise IntegrationSyncOperationError("authority must be integration-branch-git-allowlist")

    kind = _require_str(data, "kind")
    if kind not in ALLOWED_KINDS:
        raise IntegrationSyncOperationError(f"invalid kind: {kind}")
    integration_branch = _require_str(data, "integration_branch")
    review_base_branch = _require_str(data, "review_base_branch")
    _validate_branch(integration_branch, "integration_branch")
    _validate_branch(review_base_branch, "review_base_branch")
    if integration_branch == review_base_branch:
        raise IntegrationSyncOperationError("integration_branch and review_base_branch must differ")

    worktree_head = _require_str(data, "worktree_head")
    expected_remote_sha = _require_str(data, "expected_remote_sha")
    _validate_shaish(worktree_head, "worktree_head")
    _validate_shaish(expected_remote_sha, "expected_remote_sha")
    evidence = data.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        raise IntegrationSyncOperationError("evidence must be a non-empty object")

    old_rollup_head = data.get("old_rollup_head")
    if old_rollup_head is not None:
        if not isinstance(old_rollup_head, str) or not old_rollup_head.strip():
            raise IntegrationSyncOperationError("invalid old_rollup_head")
        _validate_shaish(old_rollup_head.strip(), "old_rollup_head")
        old_rollup_head = old_rollup_head.strip()

    old_rollup_ahead_count = data.get("old_rollup_ahead_count")
    if old_rollup_ahead_count is not None:
        if not isinstance(old_rollup_ahead_count, int) or old_rollup_ahead_count < 0:
            raise IntegrationSyncOperationError("invalid old_rollup_ahead_count")

    if kind == "adopt-merged-rollup" and old_rollup_head is None:
        raise IntegrationSyncOperationError("adopt-merged-rollup requires old_rollup_head")

    return IntegrationSyncOperation(
        kind=kind,
        integration_branch=integration_branch,
        review_base_branch=review_base_branch,
        worktree_head=worktree_head,
        expected_remote_sha=expected_remote_sha,
        evidence=evidence,
        old_rollup_head=old_rollup_head,
        old_rollup_ahead_count=old_rollup_ahead_count,
        pr_number=data.get("pr_number") if isinstance(data.get("pr_number"), int) else None,
        created_at=_require_str(data, "created_at"),
    )


def load_operation(path: Path) -> IntegrationSyncOperation:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise IntegrationSyncOperationError(f"malformed json: {exc}") from exc
    return validate_operation_dict(data)


def write_operation_artifact(repo_root: Path, operation: IntegrationSyncOperation) -> Path:
    runs = repo_root / ".refactor-loop" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    path = runs / f"integration-sync-operation-{operation.kind}-{int(time.time() * 1000)}.json"
    path.write_text(json.dumps(operation.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
