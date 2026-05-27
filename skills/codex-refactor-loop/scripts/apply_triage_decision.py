#!/usr/bin/env python3
"""Controller-owned apply helper for ManualIssueTriageDecisionV1."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from triage_decisions import ACCEPT_LABELS, ManualIssueTriageDecisionError, load_decision


def repo_root() -> Path:
    env_root = os.environ.get("REPO_ROOT")
    if not env_root:
        raise RuntimeError("REPO_ROOT is required")
    return Path(env_root)


def gh_repo_args() -> list[str]:
    slug = os.environ.get("GH_REPO_SLUG")
    return ["--repo", slug] if slug else []


def run_gh(args: list[str], repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", *args, *gh_repo_args()], cwd=repo, capture_output=True, text=True)


def record(repo: Path, decision_path: Path, status: str, reason: str) -> Path:
    out_dir = repo / ".refactor-loop" / "runs" / "triage-decisions-applied"
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


def reject(repo: Path, decision_path: Path, reason: str) -> int:
    record(repo, decision_path, "rejected", reason)
    print(f"TRIAGE_DECISION_REJECTED:{decision_path}:{reason}")
    return 2


def applied_marker(repo: Path, decision_path: Path) -> Path:
    return repo / ".refactor-loop" / "runs" / "triage-decisions-applied" / f"{decision_path.stem}.applied.json"


def path_under_repo(repo: Path, rel: str) -> Path:
    path = (repo / rel).resolve()
    repo_resolved = repo.resolve()
    if not str(path).startswith(str(repo_resolved) + os.sep):
        raise ManualIssueTriageDecisionError("artifact path outside repo")
    return path


def current_labels(repo: Path, issue_number: int) -> list[str]:
    result = run_gh(["issue", "view", str(issue_number), "--json", "labels"], repo)
    if result.returncode != 0:
        raise ManualIssueTriageDecisionError("issue view failed")
    data = json.loads(result.stdout or "{}")
    return [label.get("name", "") for label in data.get("labels", [])]


def apply_decision(decision_path: Path, *, repo: Path, issue_number: int, verdict: str) -> int:
    try:
        if applied_marker(repo, decision_path).exists():
            raise ManualIssueTriageDecisionError("already-applied")
        decision = load_decision(decision_path, expected_issue=issue_number)
        if decision.verdict != verdict:
            raise ManualIssueTriageDecisionError("verdict mismatch")
        labels = current_labels(repo, issue_number)
        if "auto-loop-triage" not in labels:
            raise ManualIssueTriageDecisionError("auto-loop-triage label missing")

        comment_file = path_under_repo(repo, decision.comment_artifact_path)
        if not comment_file.exists() or comment_file.read_text(encoding="utf-8").splitlines()[-1:] != ["⟦AI:AUTO-LOOP⟧"]:
            raise ManualIssueTriageDecisionError("comment artifact missing final sentinel")
        comment = run_gh(["issue", "comment", str(issue_number), "--body-file", str(comment_file)], repo)
        if comment.returncode != 0:
            raise ManualIssueTriageDecisionError("comment post failed")

        if decision.verdict == "accept":
            body_file = path_under_repo(repo, decision.body_artifact_path)
            if not body_file.exists() or body_file.read_text(encoding="utf-8").splitlines()[-1:] != ["⟦AI:AUTO-LOOP⟧"]:
                raise ManualIssueTriageDecisionError("body artifact missing final sentinel")
            args = ["issue", "edit", str(issue_number), "--body-file", str(body_file)]
            args += ["--remove-label", "auto-loop-triage"]
            for label in ACCEPT_LABELS:
                args += ["--add-label", label]
        else:
            args = ["issue", "edit", str(issue_number), "--remove-label", "auto-loop-triage"]

        edit = run_gh(args, repo)
        if edit.returncode != 0:
            raise ManualIssueTriageDecisionError("issue edit failed")
        record(repo, decision_path, "applied", decision.verdict)
        print(f"TRIAGE_DECISION_APPLIED:{issue_number}:{decision.verdict}:{decision_path}")
        return 0
    except Exception as exc:
        return reject(repo, decision_path, str(exc))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("issue_number", type=int)
    parser.add_argument("verdict", choices=("accept", "reject"))
    parser.add_argument("decision_path")
    args = parser.parse_args(argv)
    return apply_decision(Path(args.decision_path), repo=repo_root(), issue_number=args.issue_number, verdict=args.verdict)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
