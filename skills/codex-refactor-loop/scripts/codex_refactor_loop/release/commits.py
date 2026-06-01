"""Controller-side release commit producer."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from ..state import read_json, write_json
from .gate import inject_host_env, repo_root_from_env


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
    target_ref: str | None = None,
    fetch_tags: bool = False,
) -> list[dict[str, str]]:
    """Collect commits on the review base since the latest release ref."""

    repo_root = Path(repo_root).expanduser().resolve()
    if fetch_tags:
        refresh_origin_tags(repo_root)
    resolved_target_ref = resolve_target_ref(
        repo_root,
        target_ref,
        review_base_branch=review_base_branch,
    )
    release_ref = since_ref or latest_release_ref(repo_root)
    if not release_ref:
        raise RuntimeError("no latest release tag found")
    rev_range = f"{release_ref}..{resolved_target_ref}"
    result = run_git(repo_root, ["log", "--reverse", "--format=%H%x00%s%x00%b%x1e", rev_range])
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"git log {rev_range} failed"
        raise RuntimeError(detail)
    return parse_git_log(result.stdout)


def write_release_commits(
    repo_root: Path,
    review_base_branch: str | None = None,
    since_ref: str | None = None,
    target_ref: str | None = None,
    fetch_tags: bool = False,
) -> Path:
    repo_root = Path(repo_root).expanduser().resolve()
    commits = collect_release_commits(
        repo_root,
        review_base_branch=review_base_branch,
        since_ref=since_ref,
        target_ref=target_ref,
        fetch_tags=fetch_tags,
    )
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


def refresh_origin_tags(repo_root: Path) -> None:
    result = run_git(repo_root, ["fetch", "--tags", "origin"])
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git fetch --tags origin failed"
        raise RuntimeError(detail)


def resolve_target_ref(repo_root: Path, target_ref: str | None, review_base_branch: str | None = None) -> str:
    if target_ref:
        if git_ref_exists(repo_root, target_ref):
            return target_ref
        raise RuntimeError(f"target ref does not exist: {target_ref}")
    branch = str(review_base_branch or os.environ.get("REVIEW_BASE_BRANCH", "")).strip()
    if not branch:
        raise RuntimeError("missing required host branch env: REVIEW_BASE_BRANCH")
    return resolve_review_ref(repo_root, branch)


def resolve_review_ref(repo_root: Path, review_base_branch: str) -> str:
    candidates = [review_base_branch]
    if not review_base_branch.startswith("origin/"):
        candidates.append(f"origin/{review_base_branch}")
    for candidate in candidates:
        if git_ref_exists(repo_root, candidate):
            return candidate
    raise RuntimeError(f"review base ref does not exist: {review_base_branch}")


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-ref")
    parser.add_argument("--since-ref")
    parser.add_argument("--no-fetch-tags", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:
        repo_root = repo_root_from_env()
        host_env = inject_host_env(repo_root)
        review_base_branch = str(host_env.get("REVIEW_BASE_BRANCH", "")).strip()
        if not review_base_branch:
            raise RuntimeError("missing required host branch env: REVIEW_BASE_BRANCH")
        output = write_release_commits(
            repo_root,
            review_base_branch=review_base_branch,
            since_ref=args.since_ref,
            target_ref=args.target_ref,
            fetch_tags=not args.no_fetch_tags,
        )
        print(f"release commits artifact written: {output.relative_to(repo_root)}")
    except Exception as exc:
        print(f"release-commits: {exc}", file=sys.stderr)
        return 1
    return 0


__all__ = [
    "RELEASE_COMMITS_RELATIVE_PATH",
    "collect_release_commits",
    "latest_release_ref",
    "main",
    "refresh_origin_tags",
    "resolve_review_ref",
    "resolve_target_ref",
    "write_release_commits",
]
