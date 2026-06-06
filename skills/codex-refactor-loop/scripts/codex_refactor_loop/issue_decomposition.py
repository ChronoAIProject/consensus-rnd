"""Controller-private IssueDecompositionPlan validation."""

from __future__ import annotations

import json
import re
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .context import LoopContext, LoopContextError
from .github_body import GitHubBodyError, validate_self_contained_github_body


SCHEMA = "IssueDecompositionPlan"
FORBIDDEN_PLAN_FIELDS = frozenset(
    {
        "lifecycle_owner",
        "lifecycle_authority",
        "cmd",
        "argv",
        "shell",
        "gh",
        "git",
        "close",
        "assignee",
        "milestone",
        "proof",
        "digest",
        "plan_digest",
        "controller_action",
        "kind",
        "executor",
        "env",
        "commands",
        "command_line",
    }
)
CHILD_FIELDS = frozenset({"slug", "title", "scope", "non_goals", "body_artifact_path"})
PLAN_FIELDS = frozenset({"schema", "parent_issue", "source_consensus_artifact", "children", "parent_update"})
PARENT_UPDATE_FIELDS = frozenset({"comment_artifact_path"})
SLUG_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
ISSUE_RE = re.compile(r"^[1-9][0-9]*$")


class IssueDecompositionError(ValueError):
    """Raised when an IssueDecompositionPlan crosses the controller boundary."""


@dataclass(frozen=True)
class IssueDecompositionChild:
    slug: str
    title: str
    scope: str
    non_goals: str
    body_artifact_path: str


@dataclass(frozen=True)
class IssueDecompositionPlan:
    schema: str
    parent_issue: int
    source_consensus_artifact: str
    children: tuple[IssueDecompositionChild, ...]
    parent_comment_artifact_path: str


def load_issue_decomposition_plan(ctx: LoopContext, plan_path: str | Path) -> IssueDecompositionPlan:
    path = _resolve_input_path(ctx, plan_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IssueDecompositionError(f"invalid IssueDecompositionPlan JSON: {exc}") from exc
    return validate_issue_decomposition_plan(ctx, raw)


def validate_issue_decomposition_plan(ctx: LoopContext, raw: Any) -> IssueDecompositionPlan:
    if not isinstance(raw, dict):
        raise IssueDecompositionError("IssueDecompositionPlan must be a JSON object")
    _reject_forbidden_fields_recursive(raw, "plan")
    _require_exact_fields(raw, PLAN_FIELDS, "plan")
    if raw.get("schema") != SCHEMA:
        raise IssueDecompositionError("IssueDecompositionPlan schema must be IssueDecompositionPlan")

    parent_issue = _parse_issue(raw.get("parent_issue"), "parent_issue")
    source_consensus_artifact = _validate_artifact_path(ctx, raw.get("source_consensus_artifact"), "source_consensus_artifact")
    children_raw = raw.get("children")
    if not isinstance(children_raw, list) or len(children_raw) < 2:
        raise IssueDecompositionError("IssueDecompositionPlan requires at least two children")

    parent_update = raw.get("parent_update")
    if not isinstance(parent_update, dict):
        raise IssueDecompositionError("parent_update must be an object")
    _reject_forbidden_fields_recursive(parent_update, "parent_update")
    _require_exact_fields(parent_update, PARENT_UPDATE_FIELDS, "parent_update")
    parent_comment_artifact_path = _validate_artifact_path(ctx, parent_update.get("comment_artifact_path"), "parent_update.comment_artifact_path")
    _validate_parent_comment(ctx, parent_comment_artifact_path, parent_issue)

    children: list[IssueDecompositionChild] = []
    slugs: set[str] = set()
    for index, child_raw in enumerate(children_raw):
        if not isinstance(child_raw, dict):
            raise IssueDecompositionError(f"children[{index}] must be an object")
        _reject_forbidden_fields_recursive(child_raw, f"children[{index}]")
        _require_exact_fields(child_raw, CHILD_FIELDS, f"children[{index}]")
        slug = _required_text(child_raw.get("slug"), f"children[{index}].slug")
        if not SLUG_RE.fullmatch(slug):
            raise IssueDecompositionError(f"children[{index}].slug must be kebab-case")
        if slug in slugs:
            raise IssueDecompositionError(f"duplicate child slug: {slug}")
        slugs.add(slug)
        title = _required_text(child_raw.get("title"), f"children[{index}].title")
        scope = _required_text(child_raw.get("scope"), f"children[{index}].scope")
        non_goals = _required_text(child_raw.get("non_goals"), f"children[{index}].non_goals")
        body_artifact_path = _validate_artifact_path(ctx, child_raw.get("body_artifact_path"), f"children[{index}].body_artifact_path")
        _validate_child_body(ctx, body_artifact_path, parent_issue, source_consensus_artifact, scope, non_goals)
        children.append(
            IssueDecompositionChild(
                slug=slug,
                title=title,
                scope=scope,
                non_goals=non_goals,
                body_artifact_path=body_artifact_path,
            )
        )

    return IssueDecompositionPlan(
        schema=SCHEMA,
        parent_issue=parent_issue,
        source_consensus_artifact=source_consensus_artifact,
        children=tuple(children),
        parent_comment_artifact_path=parent_comment_artifact_path,
    )


def issue_decomposition_plan_digest(raw: Any) -> str:
    if not isinstance(raw, dict):
        raise IssueDecompositionError("IssueDecompositionPlan must be a JSON object")
    _reject_forbidden_fields_recursive(raw, "plan")
    encoded = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def issue_decomposition_plan_file_digest(ctx: LoopContext, plan_path: str | Path) -> str:
    path = _resolve_input_path(ctx, plan_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IssueDecompositionError(f"invalid IssueDecompositionPlan JSON: {exc}") from exc
    return issue_decomposition_plan_digest(raw)


def _resolve_input_path(ctx: LoopContext, plan_path: str | Path) -> Path:
    path = Path(plan_path)
    if path.is_absolute():
        try:
            path.resolve().relative_to(ctx.repo_root.resolve())
        except ValueError as exc:
            raise IssueDecompositionError("plan path must stay under REPO_ROOT") from exc
        return path
    return ctx.artifact_execution_path(path.as_posix())


def _reject_forbidden_fields(value: dict[str, Any], context: str) -> None:
    forbidden = sorted(FORBIDDEN_PLAN_FIELDS.intersection(value))
    if forbidden:
        raise IssueDecompositionError(f"{context} contains forbidden lifecycle/command fields: {', '.join(forbidden)}")


def _reject_forbidden_fields_recursive(value: Any, context: str) -> None:
    if isinstance(value, dict):
        _reject_forbidden_fields(value, context)
        for key, child in value.items():
            _reject_forbidden_fields_recursive(child, f"{context}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_fields_recursive(child, f"{context}[{index}]")


def _require_exact_fields(value: dict[str, Any], allowed: frozenset[str], context: str) -> None:
    extra = sorted(set(value) - allowed)
    missing = sorted(allowed - set(value))
    if extra:
        raise IssueDecompositionError(f"{context} contains unsupported fields: {', '.join(extra)}")
    if missing:
        raise IssueDecompositionError(f"{context} missing required fields: {', '.join(missing)}")


def _parse_issue(value: Any, field: str) -> int:
    text = str(value) if isinstance(value, int) else value
    if not isinstance(text, str) or not ISSUE_RE.fullmatch(text):
        raise IssueDecompositionError(f"{field} must be a positive GitHub issue number")
    return int(text)


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IssueDecompositionError(f"{field} must be non-empty text")
    return value.strip()


def _validate_artifact_path(ctx: LoopContext, value: Any, field: str) -> str:
    text = _required_text(value, field)
    try:
        path = ctx.artifact_execution_path(text)
    except LoopContextError as exc:
        raise IssueDecompositionError(f"{field}: {exc}") from exc
    if not path.is_file():
        raise IssueDecompositionError(f"{field} artifact not found: {text}")
    return text


def _validate_child_body(
    ctx: LoopContext,
    body_artifact_path: str,
    parent_issue: int,
    source_consensus_artifact: str,
    scope: str,
    non_goals: str,
) -> None:
    text = _read_artifact(ctx, body_artifact_path)
    try:
        validate_self_contained_github_body(text, authority_required=True)
    except GitHubBodyError as exc:
        raise IssueDecompositionError(f"child body invalid: {body_artifact_path}: {exc}") from exc
    consensus_name = Path(source_consensus_artifact).name
    required = (f"Parent issue: #{parent_issue}", consensus_name, scope, non_goals)
    for needle in required:
        if needle not in text:
            raise IssueDecompositionError(f"child body {body_artifact_path} missing required self-contained metadata: {needle}")


def _validate_parent_comment(ctx: LoopContext, comment_artifact_path: str, parent_issue: int) -> None:
    text = _read_artifact(ctx, comment_artifact_path)
    try:
        validate_self_contained_github_body(text, authority_required=False)
    except GitHubBodyError as exc:
        raise IssueDecompositionError(f"parent comment invalid: {comment_artifact_path}: {exc}") from exc
    if f"Parent issue: #{parent_issue}" not in text:
        raise IssueDecompositionError(f"parent comment {comment_artifact_path} missing parent issue link")


def _read_artifact(ctx: LoopContext, rel_path: str) -> str:
    return ctx.artifact_execution_path(rel_path).read_text(encoding="utf-8")
