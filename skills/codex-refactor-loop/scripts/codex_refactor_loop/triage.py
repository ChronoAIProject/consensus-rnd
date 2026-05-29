"""Manual issue triage decision artifacts and controller apply.

Refactor (issue160/p3-triage):
Old pattern: `triage_decisions.py` and `apply_triage_decision.py` split the
ManualIssueTriageDecision schema from controller-owned GitHub apply behavior.
New principle: expose the same schema validation and bounded apply behavior
from an import-safe package module; legacy script callers remain unchanged
until the caller switch. Host facts still resolve through host.env via
LoopContext.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .context import LoopContext


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
FINAL_SENTINEL = "⟦AI:AUTO-LOOP⟧"
SCHEMA_NAME = "ManualIssueTriageDecision"
TRIAGE_DECISION_DONE_MARKER = "TRIAGE_DECISION_DONE"


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
    schema: str = SCHEMA_NAME


@dataclass(frozen=True)
class TriageApplyConfig:
    context: LoopContext

    @property
    def repo(self) -> Path:
        return self.context.repo_root

    @property
    def gh_repo_slug(self) -> str | None:
        return self.context.gh_repo_slug

    @property
    def applied_dir(self) -> Path:
        return self.context.paths.runs / "triage-decisions-applied"


def load_triage_apply_config(
    *,
    repo_root: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
) -> TriageApplyConfig:
    return TriageApplyConfig(context=LoopContext.load(repo_root=repo_root, env=env, cwd=cwd or os.getcwd()))


def gh_repo_args(repo_slug: str | None) -> list[str]:
    return ["--repo", repo_slug] if repo_slug else []


def run_gh(args: list[str], *, repo: Path, repo_slug: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", *args, *gh_repo_args(repo_slug)], cwd=repo, capture_output=True, text=True)


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
    if data.get("schema") != SCHEMA_NAME:
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


def record_decision_apply(config: TriageApplyConfig, decision_path: Path, status: str, reason: str) -> Path:
    out_dir = config.applied_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{decision_path.stem}.{status}.json"
    out.write_text(
        json.dumps(
            {
                "decision_path": str(decision_path),
                "status": status,
                "reason": reason,
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return out


def reject_decision_apply(config: TriageApplyConfig, decision_path: Path, reason: str) -> int:
    record_decision_apply(config, decision_path, "rejected", reason)
    print(f"TRIAGE_DECISION_REJECTED:{decision_path}:{reason}")
    return 2


def applied_marker(config: TriageApplyConfig, decision_path: Path) -> Path:
    return config.applied_dir / f"{decision_path.stem}.applied.json"


def path_under_repo(repo: Path, rel: str) -> Path:
    path = (repo / rel).resolve()
    repo_resolved = repo.resolve()
    if not str(path).startswith(str(repo_resolved) + os.sep):
        raise ManualIssueTriageDecisionError("artifact path outside repo")
    return path


def current_labels(config: TriageApplyConfig, issue_number: int) -> list[str]:
    result = run_gh(["issue", "view", str(issue_number), "--json", "labels"], repo=config.repo, repo_slug=config.gh_repo_slug)
    if result.returncode != 0:
        raise ManualIssueTriageDecisionError("issue view failed")
    data = json.loads(result.stdout or "{}")
    return [label.get("name", "") for label in data.get("labels", [])]


def _artifact_has_final_sentinel(path: Path) -> bool:
    return path.exists() and path.read_text(encoding="utf-8").splitlines()[-1:] == [FINAL_SENTINEL]


def apply_decision(config: TriageApplyConfig, decision_path: Path, *, issue_number: int, verdict: str) -> int:
    try:
        if applied_marker(config, decision_path).exists():
            raise ManualIssueTriageDecisionError("already-applied")
        decision = load_decision(decision_path, expected_issue=issue_number)
        if decision.verdict != verdict:
            raise ManualIssueTriageDecisionError("verdict mismatch")
        labels = current_labels(config, issue_number)
        if "auto-loop-triage" not in labels:
            raise ManualIssueTriageDecisionError("auto-loop-triage label missing")

        comment_file = path_under_repo(config.repo, decision.comment_artifact_path)
        if not _artifact_has_final_sentinel(comment_file):
            raise ManualIssueTriageDecisionError("comment artifact missing final sentinel")
        comment = run_gh(
            ["issue", "comment", str(issue_number), "--body-file", str(comment_file)],
            repo=config.repo,
            repo_slug=config.gh_repo_slug,
        )
        if comment.returncode != 0:
            raise ManualIssueTriageDecisionError("comment post failed")

        if decision.verdict == "accept":
            body_file = path_under_repo(config.repo, decision.body_artifact_path)
            if not _artifact_has_final_sentinel(body_file):
                raise ManualIssueTriageDecisionError("body artifact missing final sentinel")
            args = ["issue", "edit", str(issue_number), "--body-file", str(body_file)]
            args += ["--remove-label", "auto-loop-triage"]
            for label in ACCEPT_LABELS:
                args += ["--add-label", label]
        else:
            args = ["issue", "edit", str(issue_number), "--remove-label", "auto-loop-triage"]

        edit = run_gh(args, repo=config.repo, repo_slug=config.gh_repo_slug)
        if edit.returncode != 0:
            raise ManualIssueTriageDecisionError("issue edit failed")
        record_decision_apply(config, decision_path, "applied", decision.verdict)
        print(f"TRIAGE_DECISION_APPLIED:{issue_number}:{decision.verdict}:{decision_path}")
        return 0
    except Exception as exc:
        return reject_decision_apply(config, decision_path, str(exc))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("issue_number", type=int)
    parser.add_argument("verdict", choices=("accept", "reject"))
    parser.add_argument("decision_path")
    parser.add_argument("--repo-root")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    config = load_triage_apply_config(repo_root=args.repo_root)
    return apply_decision(config, Path(args.decision_path), issue_number=args.issue_number, verdict=args.verdict)
