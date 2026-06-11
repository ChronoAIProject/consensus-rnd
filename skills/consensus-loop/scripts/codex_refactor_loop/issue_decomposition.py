"""Controller-private IssueDecompositionPlan validation."""

from __future__ import annotations

import json
import re
import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .context import LoopContext, LoopContextError
from .github_body import GitHubBodyError, validate_self_contained_github_body


SCHEMA = "IssueDecompositionPlan"
MINIMUM_FORBIDDEN_PLAN_FIELDS = frozenset(
    {
        "lifecycle_owner",
        "lifecycle_authority",
        "cmd",
        "argv",
        "shell",
        "command_line",
        "commands",
        "env",
        "gh",
        "git",
        "executor",
    }
)
COMPATIBILITY_FORBIDDEN_PLAN_FIELDS = frozenset(
    {
        "args",
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
FORBIDDEN_PLAN_FIELDS = MINIMUM_FORBIDDEN_PLAN_FIELDS | COMPATIBILITY_FORBIDDEN_PLAN_FIELDS
CHILD_FIELDS = frozenset({"slug", "title", "scope", "non_goals", "body_artifact_path"})
PLAN_FIELDS = frozenset({"schema", "parent_issue", "source_consensus_artifact", "children", "parent_update"})
PARENT_UPDATE_FIELDS = frozenset({"comment_artifact_path"})
SLUG_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
ISSUE_RE = re.compile(r"^[1-9][0-9]*$")
PLAN_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
TRACKING_BEGIN = "<!-- crnd:issue-decomposition-tracking -->"
TRACKING_END = "<!-- /crnd:issue-decomposition-tracking -->"
TRACKING_PARENT_RE = re.compile(r"^Parent issue: #([1-9][0-9]*)$")
TRACKING_DIGEST_RE = re.compile(r"^IssueDecompositionPlan digest: ([0-9a-f]{64})$")
TRACKING_CHILD_RE = re.compile(
    r"^- ([a-z][a-z0-9]*(?:-[a-z0-9]+)*): #([1-9][0-9]*) (https://github\.com/[^ ]+/[^ ]+/issues/[1-9][0-9]*) fingerprint=([0-9a-f]{64})$"
)
CHILD_FINGERPRINT_RE = re.compile(r"(?m)^IssueDecompositionChild fingerprint: ([0-9a-f]{64})$")


class IssueDecompositionError(ValueError):
    """Raised when an IssueDecompositionPlan crosses the controller boundary."""


class IssueDecompositionBackoff(RuntimeError):
    """Raised when decomposition apply should retry without consuming the action."""

    def __init__(self, diagnostic: str) -> None:
        super().__init__(diagnostic)
        self.diagnostic = diagnostic


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


@dataclass(frozen=True)
class IssueDecompositionTrackingChild:
    slug: str
    issue_number: int
    url: str
    fingerprint: str


@dataclass(frozen=True)
class IssueDecompositionTrackingComment:
    parent_issue: int
    digest: str
    children: tuple[IssueDecompositionTrackingChild, ...]
    source: str


@dataclass(frozen=True)
class IssueDecompositionTrackingProjection:
    comments: tuple[IssueDecompositionTrackingComment, ...]
    conflicts: tuple[str, ...]


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


def issue_decomposition_apply_proof_matches(
    proof: str,
    *,
    consensus_artifact: str,
    plan_path: str,
    digest: str,
    parent_issue: int,
) -> bool:
    fields = _issue_decomposition_apply_proof_fields(proof)
    if fields:
        return fields == {
            "plan_level_design_consensus_judge_artifact": consensus_artifact,
            "issue_decomposition_plan_path": plan_path,
            "issue_decomposition_plan_digest": digest,
            "parent_issue": str(parent_issue),
        }
    legacy = f"plan-level judge {consensus_artifact} validated plan {plan_path} digest {digest} reached consensus for parent issue #{parent_issue}"
    return proof.strip() == legacy


def issue_decomposition_child_fingerprint(parent_issue: int, digest: str, slug: str) -> str:
    if not PLAN_DIGEST_RE.fullmatch(digest):
        raise IssueDecompositionError("IssueDecompositionPlan digest must be a sha256 hex string")
    if not SLUG_RE.fullmatch(slug):
        raise IssueDecompositionError("child slug must be kebab-case")
    encoded = f"IssueDecompositionChild\nparent_issue={parent_issue}\nplan_digest={digest}\nslug={slug}\n".encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def issue_decomposition_child_fingerprint_line(parent_issue: int, digest: str, slug: str) -> str:
    return f"IssueDecompositionChild fingerprint: {issue_decomposition_child_fingerprint(parent_issue, digest, slug)}"


def issue_decomposition_expected_child_fingerprints(plan: IssueDecompositionPlan, digest: str) -> dict[str, str]:
    return {child.slug: issue_decomposition_child_fingerprint(plan.parent_issue, digest, child.slug) for child in plan.children}


def extract_issue_decomposition_child_fingerprint(body: str) -> str:
    matches = CHILD_FINGERPRINT_RE.findall(body)
    return matches[0] if len(matches) == 1 else ""


def build_issue_decomposition_tracking_block(
    parent_issue: int,
    digest: str,
    children: Sequence[IssueDecompositionTrackingChild],
) -> str:
    if not PLAN_DIGEST_RE.fullmatch(digest):
        raise IssueDecompositionError("IssueDecompositionPlan digest must be a sha256 hex string")
    lines = [
        TRACKING_BEGIN,
        f"Parent issue: #{parent_issue}",
        f"IssueDecompositionPlan digest: {digest}",
        "Children:",
    ]
    for child in sorted(children, key=lambda item: item.slug):
        if not SLUG_RE.fullmatch(child.slug):
            raise IssueDecompositionError("tracking child slug must be kebab-case")
        if not PLAN_DIGEST_RE.fullmatch(child.fingerprint):
            raise IssueDecompositionError("tracking child fingerprint must be a sha256 hex string")
        lines.append(f"- {child.slug}: #{child.issue_number} {child.url} fingerprint={child.fingerprint}")
    lines.append(TRACKING_END)
    return "\n".join(lines)


def append_issue_decomposition_tracking_block(
    parent_comment: str,
    parent_issue: int,
    digest: str,
    children: Sequence[IssueDecompositionTrackingChild],
    final_sentinel: str,
) -> str:
    if not parent_comment.endswith(final_sentinel):
        raise IssueDecompositionError("parent comment missing final sentinel")
    block = build_issue_decomposition_tracking_block(parent_issue, digest, children)
    return parent_comment[: -len(final_sentinel)].rstrip() + f"\n\n{block}{final_sentinel}"


def parse_issue_decomposition_tracking_comments(
    comments: Sequence[Mapping[str, Any]],
    *,
    expected_parent_issue: int,
    expected_digest: str,
) -> IssueDecompositionTrackingProjection:
    parsed: list[IssueDecompositionTrackingComment] = []
    conflicts: list[str] = []
    for index, comment in enumerate(comments):
        body = comment.get("body") if isinstance(comment, Mapping) else None
        if not isinstance(body, str):
            continue
        if TRACKING_BEGIN not in body and TRACKING_END not in body:
            continue
        blocks, block_errors = _tracking_blocks(body, f"comments[{index}]")
        conflicts.extend(block_errors)
        for block_index, block in enumerate(blocks):
            source = f"comments[{index}].tracking[{block_index}]"
            try:
                tracking = _parse_tracking_block(block, source)
            except IssueDecompositionError as exc:
                conflicts.append(str(exc))
                continue
            if tracking.parent_issue != expected_parent_issue:
                conflicts.append(f"{source} parent mismatch: {tracking.parent_issue} != {expected_parent_issue}")
                continue
            if tracking.digest != expected_digest:
                conflicts.append(f"{source} digest mismatch: {tracking.digest} != {expected_digest}")
                continue
            parsed.append(tracking)
    return IssueDecompositionTrackingProjection(tuple(parsed), tuple(conflicts))


def reconcile_issue_decomposition_tracking_children(
    plan: IssueDecompositionPlan,
    digest: str,
    projection: IssueDecompositionTrackingProjection,
) -> dict[str, IssueDecompositionTrackingChild]:
    if projection.conflicts:
        raise IssueDecompositionError("; ".join(projection.conflicts))
    expected = issue_decomposition_expected_child_fingerprints(plan, digest)
    by_slug: dict[str, IssueDecompositionTrackingChild] = {}
    for comment in projection.comments:
        for child in comment.children:
            expected_fingerprint = expected.get(child.slug)
            if expected_fingerprint is None:
                raise IssueDecompositionError(f"tracking child has unknown slug: {child.slug}")
            if child.fingerprint != expected_fingerprint:
                raise IssueDecompositionError(f"tracking child fingerprint mismatch: {child.slug}")
            existing = by_slug.get(child.slug)
            if existing is not None and existing != child:
                raise IssueDecompositionError(f"conflicting tracking child for slug: {child.slug}")
            by_slug[child.slug] = child
    return by_slug


def applied_issue_decomposition_parent_suppresses_expected_worker(
    ctx: LoopContext,
    parent_issue: int,
    *,
    command_runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None,
) -> bool:
    """Return true when a design-solving issue is an already-applied decomposition parent."""

    for plan_path in _candidate_issue_decomposition_plan_paths(ctx, parent_issue):
        try:
            plan = load_issue_decomposition_plan(ctx, plan_path)
            digest = issue_decomposition_plan_file_digest(ctx, plan_path)
        except IssueDecompositionError:
            continue
        if plan.parent_issue != parent_issue:
            continue
        if not _parent_comment_has_plan_digest_sentinel(ctx, parent_issue, digest, command_runner=command_runner):
            continue
        if not _wakeup_runner_has_applied_issue_decomposition_action(ctx, plan):
            continue
        return True
    return False


def _candidate_issue_decomposition_plan_paths(ctx: LoopContext, parent_issue: int) -> tuple[str, ...]:
    canonical = ctx.paths.runs / f"issue-{parent_issue}-decomposition" / "plan.json"
    candidates: list[str] = []
    if canonical.is_file():
        candidates.append(ctx.durable_artifact_path(canonical))
    for path in sorted(ctx.paths.runs.glob("issue-*-decomposition/plan.json")):
        rel = ctx.durable_artifact_path(path)
        if rel not in candidates:
            candidates.append(rel)
    return tuple(candidates)


def _parent_comment_has_plan_digest_sentinel(
    ctx: LoopContext,
    parent_issue: int,
    digest: str,
    *,
    command_runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None,
) -> bool:
    runner = command_runner or (
        lambda command: subprocess.run(
            list(command),
            cwd=str(ctx.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
    )
    result = runner(["gh", "issue", "view", str(parent_issue), "--json", "comments"])
    if result.returncode != 0:
        return False
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return False
    comments = payload.get("comments") if isinstance(payload, dict) else None
    if not isinstance(comments, list):
        return False
    projection = parse_issue_decomposition_tracking_comments(comments, expected_parent_issue=parent_issue, expected_digest=digest)
    return bool(projection.comments) and not projection.conflicts


def _tracking_blocks(body: str, source: str) -> tuple[list[list[str]], list[str]]:
    lines = body.splitlines()
    blocks: list[list[str]] = []
    conflicts: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index] != TRACKING_BEGIN:
            if lines[index] == TRACKING_END:
                conflicts.append(f"{source} unmatched tracking end")
            index += 1
            continue
        start = index
        index += 1
        block: list[str] = []
        while index < len(lines) and lines[index] != TRACKING_END:
            if lines[index] == TRACKING_BEGIN:
                conflicts.append(f"{source} nested tracking block at line {index + 1}")
            block.append(lines[index])
            index += 1
        if index >= len(lines):
            conflicts.append(f"{source} missing tracking end after line {start + 1}")
            break
        blocks.append(block)
        index += 1
    return blocks, conflicts


def _parse_tracking_block(lines: Sequence[str], source: str) -> IssueDecompositionTrackingComment:
    if len(lines) < 3:
        raise IssueDecompositionError(f"{source} tracking block too short")
    parent_match = TRACKING_PARENT_RE.fullmatch(lines[0])
    if parent_match is None:
        raise IssueDecompositionError(f"{source} invalid parent line")
    digest_match = TRACKING_DIGEST_RE.fullmatch(lines[1])
    if digest_match is None:
        raise IssueDecompositionError(f"{source} invalid digest line")
    if lines[2] != "Children:":
        raise IssueDecompositionError(f"{source} missing Children line")
    children: list[IssueDecompositionTrackingChild] = []
    seen_slugs: set[str] = set()
    for offset, line in enumerate(lines[3:], start=4):
        match = TRACKING_CHILD_RE.fullmatch(line)
        if match is None:
            raise IssueDecompositionError(f"{source} invalid child line {offset}")
        slug = match.group(1)
        if slug in seen_slugs:
            raise IssueDecompositionError(f"{source} duplicate child slug: {slug}")
        seen_slugs.add(slug)
        children.append(
            IssueDecompositionTrackingChild(
                slug=slug,
                issue_number=int(match.group(2)),
                url=match.group(3),
                fingerprint=match.group(4),
            )
        )
    return IssueDecompositionTrackingComment(
        parent_issue=int(parent_match.group(1)),
        digest=digest_match.group(1),
        children=tuple(children),
        source=source,
    )


def _wakeup_runner_has_applied_issue_decomposition_action(ctx: LoopContext, plan: IssueDecompositionPlan) -> bool:
    ledger = ctx.paths.state / "wakeup-runner-ledger.jsonl"
    if not ledger.is_file():
        return False
    source_log = Path(plan.source_consensus_artifact).with_suffix(".log").name
    action_prefix = f"completed-marker:{source_log}:"
    action_statuses: dict[str, tuple[bool, str, str]] = {}
    try:
        lines = ledger.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        action_id = str(row.get("action_id") or "")
        if row.get("kind") not in (None, "completed-marker"):
            continue
        if not action_id.startswith(action_prefix):
            continue
        if not _completed_marker_action_id_is_decomposition_consensus(action_id, action_prefix):
            continue
        if row.get("status") == "applied":
            action_statuses[action_id] = (True, "applied", "")
        elif row.get("status") == "skipped" and row.get("reason") == "duplicate":
            was_applied = action_statuses.get(action_id, (False, "", ""))[0]
            action_statuses[action_id] = (was_applied, "skipped", "duplicate")
    return any(
        was_applied and (status == "applied" or (status == "skipped" and reason == "duplicate"))
        for was_applied, status, reason in action_statuses.values()
    )


def _completed_marker_action_id_is_decomposition_consensus(action_id: str, action_prefix: str) -> bool:
    marker = action_id.removeprefix(action_prefix)
    return marker.startswith("META_JUDGE_DONE:consensus:")


def _issue_decomposition_apply_proof_fields(proof: str) -> dict[str, str]:
    allowed = {
        "plan_level_design_consensus_judge_artifact",
        "issue_decomposition_plan_path",
        "issue_decomposition_plan_digest",
        "parent_issue",
    }
    fields: dict[str, str] = {}
    for raw_line in proof.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        separator = "=" if "=" in line else ":"
        key, sep, value = line.partition(separator)
        if not sep:
            return {}
        key = key.strip()
        if key not in allowed or key in fields:
            return {}
        text = value.strip()
        if key == "parent_issue":
            text = text.removeprefix("#")
        if not text:
            return {}
        fields[key] = text
    return fields


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
