"""Controller-side release commit producer."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ..state import read_json, write_json
from .gate import inject_host_env, repo_root_from_env
from .versions import parse_semver_full, sort_semver


RELEASE_COMMITS_RELATIVE_PATH = Path(".refactor-loop/state/release-commits.json")


@dataclass(frozen=True)
class ManifestTarget:
    relative: str
    field: str


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
    release_ref = since_ref or latest_release_ref(repo_root, target_ref=resolved_target_ref)
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
    latest_tag = latest_release_tag(repo_root)
    latest_version = latest_tag.removeprefix("v") if latest_tag else None
    write_json(output_path, {"commits": commits, "latest_release_version": latest_version})
    return output_path


def latest_release_ref(repo_root: Path, target_ref: str | None = None) -> str | None:
    release_tag = latest_release_tag(repo_root)
    if not release_tag:
        return latest_release_subject_ref(repo_root, target_ref)
    if target_ref and git_ref_is_ancestor(repo_root, release_tag, target_ref):
        return release_tag
    if target_ref:
        return manifest_version_transition_ref(
            repo_root,
            target_ref=target_ref,
            version=release_tag.removeprefix("v"),
        )
    if git_ref_exists(repo_root, release_tag):
        return release_tag
    return None


def latest_release_tag(repo_root: Path) -> str | None:
    result = run_git(repo_root, ["tag", "--list", "v*"])
    if result.returncode != 0:
        return None
    versions_by_tag: dict[str, list[str]] = {}
    for raw_tag in result.stdout.splitlines():
        tag = raw_tag.strip()
        if not tag.startswith("v"):
            continue
        version = tag.removeprefix("v")
        try:
            parse_semver_full(version)
        except ValueError:
            continue
        versions_by_tag.setdefault(version, []).append(tag)
    if not versions_by_tag:
        return None
    latest_version = sort_semver(list(versions_by_tag))[-1]
    return sorted(versions_by_tag[latest_version])[-1]


def latest_release_subject_ref(repo_root: Path, target_ref: str | None) -> str | None:
    version = current_mapped_manifest_version(repo_root)
    if not version or not target_ref:
        return None
    subject_re = re.compile(rf"^Release v{re.escape(version)}(?: \(#[1-9][0-9]*\))?$")
    result = run_git(repo_root, ["log", "--format=%H%x00%s", target_ref])
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        sha, separator, subject = line.partition("\x00")
        if not separator:
            continue
        if subject_re.fullmatch(subject):
            return sha
    return None


def manifest_version_transition_ref(repo_root: Path, *, target_ref: str, version: str) -> str:
    targets = load_manifest_targets(repo_root)
    target_version = mapped_manifest_version_at_ref(repo_root, targets, target_ref, strict=True)
    if target_version != version:
        raise RuntimeError(
            f"target ref mapped manifest version {target_version} does not match latest release tag version {version}"
        )
    result = run_git(repo_root, ["rev-list", "--reverse", target_ref])
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"git rev-list {target_ref} failed"
        raise RuntimeError(detail)
    for line in result.stdout.splitlines():
        commit = line.strip()
        if not commit:
            continue
        commit_version = mapped_manifest_version_at_ref(repo_root, targets, commit, strict=False)
        if commit_version != version:
            continue
        parent_versions = [
            mapped_manifest_version_at_ref(repo_root, targets, parent, strict=False)
            for parent in commit_parent_refs(repo_root, commit)
        ]
        if not parent_versions or any(parent_version != version for parent_version in parent_versions):
            return commit
    raise RuntimeError(f"no branch-reachable mapped manifest transition found for release version {version}")


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


def load_manifest_targets(repo_root: Path) -> list[ManifestTarget]:
    mapping = read_json(repo_root / ".version-bump.json", {})
    files = mapping.get("files") if isinstance(mapping, dict) else None
    if not isinstance(files, list) or not files:
        raise RuntimeError(".version-bump.json: expected non-empty top-level files list")
    targets: list[ManifestTarget] = []
    for item in files:
        if not isinstance(item, dict):
            raise RuntimeError(".version-bump.json: files entries must be objects")
        relative = item.get("path")
        field = item.get("field")
        if not isinstance(relative, str) or not isinstance(field, str):
            raise RuntimeError(".version-bump.json: files entries require path and field")
        targets.append(ManifestTarget(relative=relative, field=field))
    return targets


def current_mapped_manifest_version(repo_root: Path) -> str | None:
    mapping = read_json(repo_root / ".version-bump.json", {})
    files = mapping.get("files") if isinstance(mapping, dict) else None
    if not isinstance(files, list) or not files:
        return None
    targets = load_manifest_targets(repo_root)
    versions: list[str] = []
    for target in targets:
        data = read_json(repo_root / target.relative, None)
        version = resolve_field(data, target.field)
        if not isinstance(version, str) or not version:
            return None
        versions.append(version)
    unique = set(versions)
    if len(unique) != 1:
        raise RuntimeError("mapped manifest versions are not synchronized")
    return versions[0]


def mapped_manifest_version_at_ref(
    repo_root: Path,
    targets: Sequence[ManifestTarget],
    ref: str,
    *,
    strict: bool,
) -> str | None:
    versions: list[str] = []
    for target in targets:
        data = read_json_from_git(repo_root, ref, target.relative, strict=strict)
        if data is None:
            return None
        version = resolve_field(data, target.field)
        if not isinstance(version, str) or not version:
            if strict:
                raise RuntimeError(f"{ref}:{target.relative}: missing mapped manifest field {target.field}")
            return None
        versions.append(version)
    unique = set(versions)
    if len(unique) != 1:
        raise RuntimeError(f"mapped manifest versions are not synchronized at {ref}")
    return versions[0]


def read_json_from_git(repo_root: Path, ref: str, relative: str, *, strict: bool) -> Any:
    result = run_git(repo_root, ["show", f"{ref}:{relative}"])
    if result.returncode != 0:
        if strict:
            detail = result.stderr.strip() or result.stdout.strip() or f"missing mapped manifest at {ref}:{relative}"
            raise RuntimeError(detail)
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        if strict:
            raise RuntimeError(f"{ref}:{relative}: invalid JSON") from exc
        return None


def commit_parent_refs(repo_root: Path, commit: str) -> list[str]:
    result = run_git(repo_root, ["rev-list", "--parents", "-n", "1", commit])
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"git rev-list --parents {commit} failed"
        raise RuntimeError(detail)
    parts = result.stdout.strip().split()
    return parts[1:]


def git_ref_exists(repo_root: Path, ref: str) -> bool:
    return run_git(repo_root, ["rev-parse", "--verify", "--quiet", ref]).returncode == 0


def git_ref_is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    return run_git(repo_root, ["merge-base", "--is-ancestor", ancestor, descendant]).returncode == 0


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
