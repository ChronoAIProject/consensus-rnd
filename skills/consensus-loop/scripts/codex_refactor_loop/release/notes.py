"""Controller-private release notes generation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..state import read_json
from .commits import RELEASE_COMMITS_RELATIVE_PATH


RELEASE_NOTES_DIR = Path(".refactor-loop/state/release-notes")
REFERENCE_RE = re.compile(r"(?<![\w/#-])#([1-9][0-9]*)\b")
SEMVER_RELEASE_SUBJECT_RE = re.compile(r"^Release v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?: \(\#[1-9][0-9]*\))?$")
LOCALIZED_ROLLUP_TOKEN = "".join(chr(codepoint) for codepoint in (0x53D1, 0x5E03)) + " rollup"


@dataclass(frozen=True)
class ReleaseNoteCommit:
    sha: str
    subject: str
    body: str


def generate_release_notes_file(repo_root: Path, *, version: str, target_ref: str) -> Path:
    notes = render_release_notes(load_release_note_commits(repo_root), version=version, target_ref=target_ref)
    output_path = release_notes_path(repo_root, version)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(notes, encoding="utf-8")
    return output_path


def release_notes_path(repo_root: Path, version: str) -> Path:
    safe_version = "".join(ch if ch.isalnum() or ch in ".-_" else "-" for ch in version)
    return repo_root / RELEASE_NOTES_DIR / f"v{safe_version}.md"


def load_release_note_commits(repo_root: Path) -> list[ReleaseNoteCommit]:
    raw = read_json(repo_root / RELEASE_COMMITS_RELATIVE_PATH, {})
    raw_commits = raw.get("commits") if isinstance(raw, dict) else None
    if not isinstance(raw_commits, list):
        raise RuntimeError("release notes require .refactor-loop/state/release-commits.json commits list")
    commits: list[ReleaseNoteCommit] = []
    for item in raw_commits:
        if not isinstance(item, dict):
            continue
        sha = item.get("sha")
        subject = item.get("subject")
        body = item.get("body", "")
        if not isinstance(sha, str) or not isinstance(subject, str) or not isinstance(body, str):
            continue
        commits.append(ReleaseNoteCommit(sha=sha, subject=subject, body=body))
    return commits


def render_release_notes(commits: Iterable[ReleaseNoteCommit], *, version: str, target_ref: str) -> str:
    visible = [commit for commit in commits if not is_mechanical_release_artifact(commit.subject)]
    referenced = referenced_work_numbers(visible)
    lines = [f"## v{version}", "", f"Target: `{target_ref}`", ""]
    if visible:
        lines.append("### Changes")
        for commit in visible:
            suffix = reference_suffix(commit)
            lines.append(f"- {commit.subject}{suffix}")
    else:
        lines.extend(("### Changes", "- No user-facing changes were found in the controller release range."))
    if referenced:
        lines.extend(("", "### Referenced work"))
        for number in referenced:
            lines.append(f"- #{number}")
    lines.append("")
    return "\n".join(lines)


def is_mechanical_release_artifact(subject: str) -> bool:
    normalized = subject.strip().lower()
    if SEMVER_RELEASE_SUBJECT_RE.fullmatch(subject.strip()):
        return True
    mechanical_tokens = (
        "release rollup",
        "release-rollup",
        LOCALIZED_ROLLUP_TOKEN,
        "rollup:",
        "integration ahead",
        "reserve release",
        "release reserve",
        "reserve:",
        "release commit",
    )
    return any(token in normalized for token in mechanical_tokens)


def referenced_work_numbers(commits: Iterable[ReleaseNoteCommit]) -> list[str]:
    numbers: set[str] = set()
    for commit in commits:
        numbers.update(REFERENCE_RE.findall(f"{commit.subject}\n{commit.body}"))
    return sorted(numbers, key=int)


def reference_suffix(commit: ReleaseNoteCommit) -> str:
    numbers = referenced_work_numbers([commit])
    if not numbers:
        return ""
    return " (" + ", ".join(f"#{number}" for number in numbers) + ")"


__all__ = [
    "ReleaseNoteCommit",
    "generate_release_notes_file",
    "is_mechanical_release_artifact",
    "load_release_note_commits",
    "referenced_work_numbers",
    "release_notes_path",
    "render_release_notes",
]
