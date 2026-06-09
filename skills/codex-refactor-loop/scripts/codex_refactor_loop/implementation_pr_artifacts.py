"""Shared validation for worker-authored implementation PR artifacts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .github_body import GitHubBodyError, validate_self_contained_github_body


SAFE_IMPLEMENTATION_CLUSTER_RE = re.compile(r"^[A-Za-z0-9._-]+$")
IMPLEMENTATION_PR_REQUIRED_SECTION_HEADINGS = (
    "## Changed files",
    "## Test results",
    "## Deviations",
)
IMPLEMENTATION_PR_REQUIRED_SECTION_RULES = (
    ("changed files", ("changed", "file")),
    ("test results", ("test", "result")),
    ("deviations", ("deviation",)),
)
PLACEHOLDER_IMPLEMENT_TITLE_RE_TEMPLATE = r"(?i)^(?:implement(?:ed|ation)?\s+issue\s+#%s\b|issue\s+#%s\s+implement(?:ed|ation)?\b|\u5b9e\u73b0\s+issue\s+#%s\s*$)"
PLACEHOLDER_IMPLEMENT_HEADING_RE_TEMPLATE = r"(?im)^##\s+(?:implement(?:ed|ation)?\s+issue\s+#%s|issue\s+#%s\s+(?:implement(?:ed|ation)?|\u5b9e\u73b0)|\u5b9e\u73b0\s+issue\s+#%s)\s*$"
FINAL_SENTINEL = "\u27e6AI:AUTO-LOOP\u27e7"
CLOSING_ISSUE_RE = re.compile(r"(?im)\bCloses\s+#([1-9][0-9]*)\b")


@dataclass(frozen=True)
class ImplementationPrArtifactValidation:
    title_path: Path
    body_path: Path
    title: str = ""
    reason: str | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.reason is None


def implementation_cluster_id(action: Mapping[str, object], issue_target: int | str) -> str:
    marker = str(action.get("source_marker") or "")
    marker_id = marker.removeprefix("IMPLEMENT_DONE:").removesuffix(":ok").strip(":")
    candidate = marker_id.replace("_", "-").strip("-") or f"issue-{issue_target}"
    return candidate if SAFE_IMPLEMENTATION_CLUSTER_RE.fullmatch(candidate) else f"issue-{issue_target}"


def implementation_pr_title_path(
    repo_root: Path,
    runs_dir: Path,
    action: Mapping[str, object],
    issue_target: int | str,
) -> Path:
    raw = str(action.get("title_file") or "").strip()
    cluster_id = implementation_cluster_id(action, issue_target)
    path = Path(raw) if raw else runs_dir / f"implementation-pr-{cluster_id}-title.txt"
    return path if path.is_absolute() else repo_root / path


def implementation_pr_body_path(
    repo_root: Path,
    runs_dir: Path,
    action: Mapping[str, object],
    issue_target: int | str,
) -> Path:
    raw = str(action.get("body_file") or "").strip()
    cluster_id = implementation_cluster_id(action, issue_target)
    path = Path(raw) if raw else runs_dir / f"implementation-pr-{cluster_id}-body.md"
    return path if path.is_absolute() else repo_root / path


def validate_implementation_pr_artifacts(
    repo_root: Path,
    runs_dir: Path,
    action: Mapping[str, object],
    issue_target: int | str,
) -> ImplementationPrArtifactValidation:
    title_path = implementation_pr_title_path(repo_root, runs_dir, action, issue_target)
    body_path = implementation_pr_body_path(repo_root, runs_dir, action, issue_target)
    title_invalid = _validate_title_path(repo_root, title_path)
    if title_invalid:
        return ImplementationPrArtifactValidation(title_path, body_path, reason=title_invalid)
    body_invalid = _validate_body_path(repo_root, body_path)
    if body_invalid:
        return ImplementationPrArtifactValidation(title_path, body_path, reason=body_invalid)

    try:
        issue_number = int(issue_target)
    except (TypeError, ValueError):
        return ImplementationPrArtifactValidation(title_path, body_path, reason="implementation_pr_artifact_target_missing")

    title_result = _validate_title(title_path, issue_number)
    if title_result.reason:
        return ImplementationPrArtifactValidation(title_path, body_path, reason=title_result.reason, detail=title_result.detail)
    body_result = _validate_body(body_path, issue_number)
    if body_result.reason:
        return ImplementationPrArtifactValidation(
            title_path,
            body_path,
            title=title_result.title,
            reason=body_result.reason,
            detail=body_result.detail,
        )
    return ImplementationPrArtifactValidation(title_path, body_path, title=title_result.title)


def _validate_title_path(repo_root: Path, path: Path) -> str | None:
    if not _is_repo_runs_file(repo_root, path):
        return "implementation_pr_title_artifact_invalid_path"
    if not path.is_file():
        return "implementation_pr_title_artifact_missing"
    return None


def _validate_body_path(repo_root: Path, path: Path) -> str | None:
    if not _is_repo_runs_file(repo_root, path):
        return "implementation_pr_body_artifact_invalid_path"
    if not path.is_file():
        return "implementation_pr_body_artifact_missing"
    return None


def _is_repo_runs_file(repo_root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to((repo_root / ".refactor-loop" / "runs").resolve())
    except ValueError:
        return False
    return True


def _validate_title(path: Path, issue_target: int) -> ImplementationPrArtifactValidation:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ImplementationPrArtifactValidation(path, path, reason="implementation_pr_title_artifact_missing")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        return ImplementationPrArtifactValidation(path, path, reason="implementation_pr_title_artifact_invalid")
    title = lines[0]
    issue_text = str(issue_target)
    if re.search(PLACEHOLDER_IMPLEMENT_TITLE_RE_TEMPLATE % (issue_text, issue_text, issue_text), title):
        return ImplementationPrArtifactValidation(path, path, reason="implementation_pr_title_placeholder")
    if FINAL_SENTINEL in title or re.search(r"\bCloses\s+#", title, flags=re.IGNORECASE):
        return ImplementationPrArtifactValidation(path, path, reason="implementation_pr_title_contains_body_content")
    return ImplementationPrArtifactValidation(path, path, title=title)


def _validate_body(path: Path, issue_target: int) -> ImplementationPrArtifactValidation:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ImplementationPrArtifactValidation(path, path, reason="implementation_pr_body_artifact_missing")
    if text.rstrip("\n").splitlines()[-1:] != [FINAL_SENTINEL]:
        return ImplementationPrArtifactValidation(path, path, reason="implementation_pr_body_sentinel_missing")
    try:
        validate_self_contained_github_body(text)
    except GitHubBodyError as exc:
        return ImplementationPrArtifactValidation(path, path, reason="implementation_pr_body_github_body_invalid", detail=str(exc))
    if _closing_issue_numbers(text) != [issue_target]:
        return ImplementationPrArtifactValidation(path, path, reason="implementation_pr_body_closes_mismatch")
    for _concept, keywords in IMPLEMENTATION_PR_REQUIRED_SECTION_RULES:
        if not _has_heading_with_keywords(text, keywords):
            return ImplementationPrArtifactValidation(path, path, reason="implementation_pr_body_required_section_missing")
    issue_text = str(issue_target)
    if re.search(PLACEHOLDER_IMPLEMENT_HEADING_RE_TEMPLATE % (issue_text, issue_text, issue_text), text):
        return ImplementationPrArtifactValidation(path, path, reason="implementation_pr_body_placeholder")
    return ImplementationPrArtifactValidation(path, path)


def _closing_issue_numbers(text: str) -> list[int]:
    return [int(match) for match in CLOSING_ISSUE_RE.findall(text)]


def _has_heading_with_keywords(text: str, keywords: tuple[str, ...]) -> bool:
    for line in text.splitlines():
        if not re.match(r"^\s*##\s+", line):
            continue
        normalized = line.strip().casefold()
        if all(keyword.casefold() in normalized for keyword in keywords):
            return True
    return False
