"""Narrow IntegrationSyncRequest schema for controller-owned apply.

Refactor (issue160/p3-sync-apply): Old pattern:
`integration_sync_requests.py` exposed the request artifact contract as a
top-level script module.
New principle: expose the same artifact schema from the package sync boundary;
legacy callers remain on the old script until the caller switch.
"""

from __future__ import annotations

import argparse
import json
import sys
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ALLOWED_KINDS = {
    "push-local-ahead",
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
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
SHA_RE = re.compile(r"^[0-9a-f]{7,64}$|^[A-Za-z0-9._/-]+$")


class IntegrationSyncRequestError(ValueError):
    """Raised when an IntegrationSyncRequest artifact is malformed."""


@dataclass(frozen=True)
class IntegrationSyncRequest:
    kind: str
    integration_branch: str
    review_base_branch: str
    worktree_head: str
    expected_remote_sha: str
    evidence: dict[str, Any]
    lifecycle_owner: str = "controller"
    lifecycle_authority: bool = False
    schema: str = "IntegrationSyncRequest"
    old_rollup_head: str | None = None
    old_rollup_ahead_count: int | None = None
    pr_number: int | None = None
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def _reject_command_like_fields(data: dict[str, Any]) -> None:
    present = sorted(COMMAND_LIKE_FIELDS.intersection(data))
    if present:
        raise IntegrationSyncRequestError(f"command-like fields forbidden: {','.join(present)}")


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise IntegrationSyncRequestError(f"missing or invalid {key}")
    return value.strip()


def _validate_branch(name: str, key: str) -> None:
    if not BRANCH_RE.fullmatch(name) or name.startswith("-") or ".." in name or name.endswith(".lock"):
        raise IntegrationSyncRequestError(f"invalid {key}")


def _validate_shaish(value: str, key: str) -> None:
    if not SHA_RE.fullmatch(value) or value.startswith("-"):
        raise IntegrationSyncRequestError(f"invalid {key}")


def validate_request_dict(data: dict[str, Any]) -> IntegrationSyncRequest:
    if not isinstance(data, dict):
        raise IntegrationSyncRequestError("request must be an object")
    _reject_command_like_fields(data)
    if data.get("schema") != "IntegrationSyncRequest":
        raise IntegrationSyncRequestError("schema must be IntegrationSyncRequest")
    if data.get("lifecycle_owner") != "controller":
        raise IntegrationSyncRequestError("lifecycle_owner must be controller")
    if data.get("lifecycle_authority") is not False:
        raise IntegrationSyncRequestError("lifecycle_authority must be false")

    kind = _require_str(data, "kind")
    if kind not in ALLOWED_KINDS:
        raise IntegrationSyncRequestError(f"invalid kind: {kind}")
    integration_branch = _require_str(data, "integration_branch")
    review_base_branch = _require_str(data, "review_base_branch")
    _validate_branch(integration_branch, "integration_branch")
    _validate_branch(review_base_branch, "review_base_branch")
    if integration_branch == review_base_branch:
        raise IntegrationSyncRequestError("integration_branch and review_base_branch must differ")

    worktree_head = _require_str(data, "worktree_head")
    expected_remote_sha = _require_str(data, "expected_remote_sha")
    _validate_shaish(worktree_head, "worktree_head")
    _validate_shaish(expected_remote_sha, "expected_remote_sha")
    evidence = data.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        raise IntegrationSyncRequestError("evidence must be a non-empty object")

    old_rollup_head = data.get("old_rollup_head")
    if old_rollup_head is not None:
        if not isinstance(old_rollup_head, str) or not old_rollup_head.strip():
            raise IntegrationSyncRequestError("invalid old_rollup_head")
        _validate_shaish(old_rollup_head.strip(), "old_rollup_head")
        old_rollup_head = old_rollup_head.strip()

    old_rollup_ahead_count = data.get("old_rollup_ahead_count")
    if old_rollup_ahead_count is not None:
        if not isinstance(old_rollup_ahead_count, int) or old_rollup_ahead_count < 0:
            raise IntegrationSyncRequestError("invalid old_rollup_ahead_count")

    if kind == "adopt-merged-rollup" and old_rollup_head is None:
        raise IntegrationSyncRequestError("adopt-merged-rollup requires old_rollup_head")

    return IntegrationSyncRequest(
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
        applied=bool(data.get("applied", False)),
    )


def load_request(path: Path) -> IntegrationSyncRequest:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise IntegrationSyncRequestError(f"malformed json: {exc}") from exc
    return validate_request_dict(data)


def write_request_artifact(repo_root: Path, request: IntegrationSyncRequest) -> Path:
    runs = repo_root / ".refactor-loop" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    safe_kind = request.kind.replace("-", "_")
    path = runs / f"integration-sync-request-{safe_kind}-{int(time.time() * 1000)}.json"
    path.write_text(json.dumps(request.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request_path", nargs="?", help="validate an IntegrationSyncRequest artifact")
    parser.add_argument("--json", action="store_true", help="print normalized JSON")
    args = parser.parse_args(argv)
    if not args.request_path:
        parser.print_help()
        return 0
    try:
        request = load_request(Path(args.request_path))
    except IntegrationSyncRequestError as exc:
        sys.stderr.write(f"INTEGRATION_SYNC_REQUEST_INVALID:{args.request_path}:{exc}\n")
        return 2
    if args.json:
        print(json.dumps(request.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"INTEGRATION_SYNC_REQUEST_OK:{args.request_path}:{request.kind}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
