"""Controller-side release commit producer."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Sequence

from ..state import read_json, write_json


DEFAULT_REVIEW_BASE_BRANCH = "dev"
RELEASE_COMMITS_RELATIVE_PATH = Path(".refactor-loop/state/release-commits.json")


def run_git(repo_root: Path, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def collect_release_commits(
    repo_root: Path,
    review_base_branch: str | None = None,
    since_ref: str | None = None,
) -> list[dict[str, str]]:
    """Collect commits on the review base since the latest release ref."""

    repo_root = Path(repo_root).expanduser().resolve()
    target_ref = resolve_review_ref(
        repo_root,
        review_base_branch or os.environ.get("REVIEW_BASE_BRANCH") or DEFAULT_REVIEW_BASE_BRANCH,
    )
    release_ref = since_ref or latest_release_ref(repo_root)
    rev_range = f"{release_ref}..{target_ref}" if release_ref else target_ref
    result = run_git(repo_root, ["log", "--reverse", "--format=%H%x00%s%x00%b%x1e", rev_range])
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"git log {rev_range} failed"
        raise RuntimeError(detail)
    return parse_git_log(result.stdout)


def write_release_commits(
    repo_root: Path,
    review_base_branch: str | None = None,
    since_ref: str | None = None,
) -> Path:
    # Refactor (impl/issue232-release-commits-producer): Old pattern: release-gate consumed release-commits.json but no controller-side producer owned the artifact. New principle: a one-shot pre-gate producer reads git, then atomically writes the state artifact while the decider stays git-free.
    repo_root = Path(repo_root).expanduser().resolve()
    commits = collect_release_commits(repo_root, review_base_branch=review_base_branch, since_ref=since_ref)
    output_path = repo_root / RELEASE_COMMITS_RELATIVE_PATH
    write_json(output_path, {"commits": commits})
    return output_path


def latest_release_ref(repo_root: Path) -> str | None:
    described = run_git(repo_root, ["describe", "--tags", "--abbrev=0"])
    if described.returncode == 0 and described.stdout.strip():
        return described.stdout.strip()
    version_tag = manifest_version_tag(repo_root)
    if version_tag and git_ref_exists(repo_root, version_tag):
        return version_tag
    return None


def resolve_review_ref(repo_root: Path, review_base_branch: str) -> str:
    candidates = [review_base_branch]
    if not review_base_branch.startswith("origin/"):
        candidates.append(f"origin/{review_base_branch}")
    for candidate in candidates:
        if git_ref_exists(repo_root, candidate):
            return candidate
    return "HEAD"


def manifest_version_tag(repo_root: Path) -> str | None:
    mapping = read_json(repo_root / ".version-bump.json", {})
    files = mapping.get("files") if isinstance(mapping, dict) else None
    if not isinstance(files, list) or not files:
        return None
    first = files[0]
    if not isinstance(first, dict):
        return None
    relative = first.get("path")
    field = first.get("field")
    if not isinstance(relative, str) or not isinstance(field, str):
        return None
    data = read_json(repo_root / relative, None)
    version = resolve_field(data, field)
    return f"v{version}" if isinstance(version, str) and version else None


def git_ref_exists(repo_root: Path, ref: str) -> bool:
    return run_git(repo_root, ["rev-parse", "--verify", "--quiet", ref]).returncode == 0


def resolve_field(data: Any, field: str) -> Any:
    current = data
    for part in field.split("."):
        if isinstance(current, list):
            if not part.isdigit():
                return None
            index = int(part)
            if index >= len(current):
                return None
            current = current[index]
            continue
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def parse_git_log(raw: str) -> list[dict[str, str]]:
    commits: list[dict[str, str]] = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split("\x00", 2)
        if len(parts) != 3:
            continue
        sha, subject, body = parts
        commits.append({"sha": sha, "subject": subject, "body": body.rstrip("\n")})
    return commits


__all__ = [
    "RELEASE_COMMITS_RELATIVE_PATH",
    "collect_release_commits",
    "latest_release_ref",
    "resolve_review_ref",
    "write_release_commits",
]
