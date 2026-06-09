"""Check that all published plugin manifests share one version."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..context import LoopContext


class ManifestVersionSyncError(Exception):
    """Raised when manifest version mappings cannot be resolved or synced."""


@dataclass(frozen=True)
class VersionRecord:
    path: str
    field: str
    value: str


def load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise ManifestVersionSyncError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestVersionSyncError(f"invalid JSON in {path}: {exc}") from exc


def resolve_field(document: Any, field: str, *, path: str) -> Any:
    current = document
    for segment in field.split("."):
        if isinstance(current, dict):
            if segment not in current:
                raise ManifestVersionSyncError(f"{path}:{field}: missing field segment {segment!r}")
            current = current[segment]
            continue

        if isinstance(current, list):
            try:
                index = int(segment)
            except ValueError as exc:
                raise ManifestVersionSyncError(f"{path}:{field}: expected numeric list index, got {segment!r}") from exc
            if index < 0 or index >= len(current):
                raise ManifestVersionSyncError(f"{path}:{field}: list index {index} out of range")
            current = current[index]
            continue

        raise ManifestVersionSyncError(f"{path}:{field}: cannot resolve segment {segment!r} through {type(current).__name__}")

    return current


def repo_root_from_context(repo_root: Path | None = None, *, ctx: LoopContext | None = None) -> Path:
    if ctx is not None:
        return ctx.repo_root.resolve()
    if repo_root is None:
        raise ValueError("repo_root or ctx is required")
    return repo_root.resolve()


def load_manifest_records(repo_root: Path | None = None, *, ctx: LoopContext | None = None) -> list[VersionRecord]:
    root = repo_root_from_context(repo_root, ctx=ctx)
    version_bump_path = root / ".version-bump.json"
    version_bump = load_json(version_bump_path)
    files = version_bump.get("files") if isinstance(version_bump, dict) else None
    if not isinstance(files, list):
        raise ManifestVersionSyncError(".version-bump.json: expected top-level files list")

    records: list[VersionRecord] = []
    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            raise ManifestVersionSyncError(f".version-bump.json: files.{index} must be an object")
        manifest_path = entry.get("path")
        field = entry.get("field")
        if not isinstance(manifest_path, str) or not manifest_path:
            raise ManifestVersionSyncError(f".version-bump.json: files.{index}.path must be a non-empty string")
        if not isinstance(field, str) or not field:
            raise ManifestVersionSyncError(f".version-bump.json: files.{index}.field must be a non-empty string")

        manifest = load_json(root / manifest_path)
        value = resolve_field(manifest, field, path=manifest_path)
        if not isinstance(value, str) or not value:
            raise ManifestVersionSyncError(f"{manifest_path}:{field}: version value must be a non-empty string")
        records.append(VersionRecord(path=manifest_path, field=field, value=value))

    return records


def check_manifest_version_sync(repo_root: Path | None = None, *, ctx: LoopContext | None = None) -> list[VersionRecord]:
    records = load_manifest_records(repo_root, ctx=ctx)
    values = {record.value for record in records}
    if len(values) > 1:
        lines = ["manifest versions are not synced:"]
        lines.extend(f"- {record.path}:{record.field}={record.value}" for record in records)
        raise ManifestVersionSyncError("\n".join(lines))
    return records


def format_success(records: list[VersionRecord]) -> str:
    lines = [f"{record.path}:{record.field}={record.value}" for record in records]
    if records:
        lines.append(f"MANIFEST_VERSION_SYNC_OK:{records[0].value}")
    else:
        lines.append("MANIFEST_VERSION_SYNC_OK:no-records")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path)
    args = parser.parse_args(argv)
    try:
        root = args.repo_root or Path.cwd()
        records = check_manifest_version_sync(root)
    except ManifestVersionSyncError as exc:
        sys.stderr.write(f"MANIFEST_VERSION_SYNC_ERROR: {exc}\n")
        return 1
    except Exception as exc:
        sys.stderr.write(f"manifest-version-sync: {exc}\n")
        return 2
    print(format_success(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
