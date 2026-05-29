#!/usr/bin/env python3
"""Synchronize release manifest versions."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MAP_PATH = ROOT / ".version-bump.json"
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


@dataclass(frozen=True)
class ManifestTarget:
    path: Path
    field: str
    version: str
    data: Any


def parse_version(version: str) -> tuple[int, int, int]:
    match = SEMVER_RE.match(version)
    if not match:
        raise ValueError(f"invalid semver: {version}")
    return tuple(int(part) for part in match.group(1, 2, 3))


def bump_semver(version: str, level: str) -> str:
    # Bumps apply to the core version and intentionally drop pre-release/build metadata.
    major, minor, patch = parse_version(version)
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    if level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"invalid bump level: {level}")


def resolve_field(data: Any, field: str) -> Any:
    current = data
    for part in field.split("."):
        if isinstance(current, list):
            if not part.isdigit():
                raise KeyError(f"expected list index at {part!r} in {field}")
            index = int(part)
            current = current[index]
            continue
        if not isinstance(current, dict):
            raise KeyError(f"cannot resolve {part!r} in {field}")
        current = current[part]
    return current


def set_field(data: Any, field: str, value: str) -> None:
    current = data
    parts = field.split(".")
    for part in parts[:-1]:
        if isinstance(current, list):
            if not part.isdigit():
                raise KeyError(f"expected list index at {part!r} in {field}")
            current = current[int(part)]
            continue
        if not isinstance(current, dict):
            raise KeyError(f"cannot resolve {part!r} in {field}")
        current = current[part]

    last = parts[-1]
    if isinstance(current, list):
        if not last.isdigit():
            raise KeyError(f"expected list index at {last!r} in {field}")
        current[int(last)] = value
    elif isinstance(current, dict):
        current[last] = value
    else:
        raise KeyError(f"cannot set {last!r} in {field}")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def load_targets(root: Path = ROOT) -> list[ManifestTarget]:
    mapping = read_json(root / ".version-bump.json")
    targets: list[ManifestTarget] = []
    for item in mapping["files"]:
        path = root / item["path"]
        field = item["field"]
        data = read_json(path)
        version = resolve_field(data, field)
        if not isinstance(version, str):
            raise ValueError(f"{item['path']}:{field} is not a string")
        parse_version(version)
        targets.append(ManifestTarget(path=path, field=field, version=version, data=data))
    return targets


# Refactor (iter3/skill-release-pipeline): Old: 无机械 release 管道  New: bump_version + release.yml minimal option A(#32 minimal 共识)
def assert_versions_sync(targets: list[ManifestTarget]) -> str:
    versions = {target.version for target in targets}
    if len(versions) != 1:
        lines = ["manifest versions are not synchronized:"]
        lines.extend(f"- {target.path.relative_to(ROOT)}:{target.field} = {target.version}" for target in targets)
        raise ValueError("\n".join(lines))
    return targets[0].version


# Refactor (iter3/skill-release-pipeline): Old: 无机械 release 管道  New: bump_version + release.yml minimal option A(#32 minimal 共识)
def write_version(targets: list[ManifestTarget], version: str, dry_run: bool) -> None:
    parse_version(version)
    for target in targets:
        set_field(target.data, target.field, version)
    if dry_run:
        return
    for target in targets:
        write_json(target.path, target.data)


# Refactor (iter3/skill-release-pipeline): Old: 无机械 release 管道  New: bump_version + release.yml minimal option A(#32 minimal 共识)
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate mapped manifest versions are synchronized")
    parser.add_argument("--read-version", action="store_true", help="print the synchronized manifest version")
    parser.add_argument("--dry-run", action="store_true", help="compute changes without writing files")
    parser.add_argument("--level", choices=("patch", "minor", "major"), help="semver bump level")
    parser.add_argument("--version", help="exact semver version to write")
    args = parser.parse_args(argv)

    try:
        if args.level and args.version:
            raise ValueError("--level and --version are mutually exclusive")
        targets = load_targets()
        current = assert_versions_sync(targets)
        next_version = args.version or (bump_semver(current, args.level) if args.level else None)
        if next_version is not None:
            write_version(targets, next_version, args.dry_run)
            current = next_version
        if args.read_version:
            print(current)
    except Exception as exc:
        print(f"bump_version.py: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
