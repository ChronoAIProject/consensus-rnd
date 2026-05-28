#!/usr/bin/env python3
"""ManualIssueTriageDecision schema for controller-owned apply.

Refactor (iter5/cluster-issue70-controller-owned-apply):
Old: triage worker 直 gh issue edit + TriageLifecycleRequestV1 Markdown artifact parsed inline by bash.
New: triage worker emits ManualIssueTriageDecision JSON artifact + TRIAGE_DECISION_DONE marker; controller-owned apply_triage_decision.py re-reads live labels before lifecycle apply.

Refactor (iter5/issue107-python-identifier-rename): Old pattern: version
suffix embedded in schema/type names (ManualIssueTriageDecisionV1). New
principle: naked responsibility names express stable artifact intent;
compatibility/version policy lives in contracts/tests, not identifier suffixes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ACCEPT_LABELS = [
    "auto-loop",
    "phase9-auto-solve",
    "🔍 phase:design-solving",
    "🤖 human:auto-推进",
    "refactor-design-needed",
]
REMOVE_LABELS = ["auto-loop-triage"]
ALLOWED_VERDICTS = {"accept", "reject"}
COMMAND_LIKE_FIELDS = {"argv", "args", "shell", "command", "commands", "cmd", "close", "assignee", "milestone"}


class ManualIssueTriageDecisionError(ValueError):
    """Raised when a ManualIssueTriageDecision artifact is malformed."""


@dataclass(frozen=True)
class ManualIssueTriageDecision:
    issue_number: int
    verdict: str
    body_artifact_path: str
    comment_artifact_path: str
    add_labels: list[str]
    remove_labels: list[str]
    sentinel_present: bool
    lifecycle_owner: str = "controller"
    lifecycle_authority: bool = False
    schema: str = "ManualIssueTriageDecision"


def _reject_command_like_fields(data: dict[str, Any]) -> None:
    present = sorted(COMMAND_LIKE_FIELDS.intersection(data))
    if present:
        raise ManualIssueTriageDecisionError(f"command-like fields forbidden: {','.join(present)}")


def _repo_artifact_path(value: Any, *, allow_empty: bool = False) -> str:
    if allow_empty and value in ("", None):
        return ""
    if not isinstance(value, str) or not value:
        raise ManualIssueTriageDecisionError("artifact path required")
    if value.startswith("/") or ".." in Path(value).parts:
        raise ManualIssueTriageDecisionError("artifact path outside repo")
    if not value.startswith(".refactor-loop/runs/"):
        raise ManualIssueTriageDecisionError("artifact path must be under .refactor-loop/runs")
    return value


def validate_decision_dict(data: dict[str, Any], *, expected_issue: int | None = None) -> ManualIssueTriageDecision:
    if not isinstance(data, dict):
        raise ManualIssueTriageDecisionError("decision must be an object")
    _reject_command_like_fields(data)
    if data.get("schema") != "ManualIssueTriageDecision":
        raise ManualIssueTriageDecisionError("schema must be ManualIssueTriageDecision")
    if data.get("lifecycle_owner") != "controller":
        raise ManualIssueTriageDecisionError("lifecycle_owner must be controller")
    if data.get("lifecycle_authority") is not False:
        raise ManualIssueTriageDecisionError("lifecycle_authority must be false")
    issue_number = data.get("issue_number")
    if not isinstance(issue_number, int) or issue_number <= 0:
        raise ManualIssueTriageDecisionError("invalid issue_number")
    if expected_issue is not None and issue_number != expected_issue:
        raise ManualIssueTriageDecisionError("issue mismatch")
    verdict = data.get("verdict")
    if verdict not in ALLOWED_VERDICTS:
        raise ManualIssueTriageDecisionError("invalid verdict")
    remove_labels = data.get("remove_labels")
    if remove_labels != REMOVE_LABELS:
        raise ManualIssueTriageDecisionError("remove_labels must be fixed auto-loop-triage")
    add_labels = data.get("add_labels")
    body_artifact_path = _repo_artifact_path(data.get("body_artifact_path"), allow_empty=(verdict == "reject"))
    comment_artifact_path = _repo_artifact_path(data.get("comment_artifact_path"))
    if verdict == "accept":
        if add_labels != ACCEPT_LABELS:
            raise ManualIssueTriageDecisionError("accept add_labels must be fixed Phase 9 labels")
        if not body_artifact_path:
            raise ManualIssueTriageDecisionError("accept requires body_artifact_path")
    else:
        if add_labels != []:
            raise ManualIssueTriageDecisionError("reject add_labels must be empty")
        if body_artifact_path:
            raise ManualIssueTriageDecisionError("reject body_artifact_path must be empty")
    if data.get("sentinel_present") is not True:
        raise ManualIssueTriageDecisionError("sentinel_present must be true")
    return ManualIssueTriageDecision(
        issue_number=issue_number,
        verdict=verdict,
        body_artifact_path=body_artifact_path,
        comment_artifact_path=comment_artifact_path,
        add_labels=add_labels,
        remove_labels=remove_labels,
        sentinel_present=True,
    )


def extract_json_object(text: str) -> dict[str, Any]:
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, flags=re.S)
    raw = match.group(1) if match else text
    try:
        data = json.loads(raw)
    except Exception as exc:
        raise ManualIssueTriageDecisionError(f"malformed json: {exc}") from exc
    return data


def load_decision(path: Path, *, expected_issue: int | None = None) -> ManualIssueTriageDecision:
    return validate_decision_dict(extract_json_object(path.read_text(encoding="utf-8")), expected_issue=expected_issue)
