"""Shared semver helpers for release and update-check surfaces."""

from __future__ import annotations

import re
from functools import cmp_to_key
from typing import Any


SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


class SemVer(tuple):
    """Comparable semver value; tuple shape preserves lightweight callers."""

    __slots__ = ()

    def __new__(
        cls,
        major: int,
        minor: int,
        patch: int,
        prerelease: tuple[str, ...] = (),
        build: str | None = None,
    ) -> "SemVer":
        return tuple.__new__(cls, (major, minor, patch, prerelease, build))

    @property
    def major(self) -> int:
        return self[0]

    @property
    def minor(self) -> int:
        return self[1]

    @property
    def patch(self) -> int:
        return self[2]

    @property
    def prerelease(self) -> tuple[str, ...]:
        return self[3]

    @property
    def build(self) -> str | None:
        return self[4]


def parse_semver(version: Any) -> tuple[int, int, int]:
    return parse_semver_full(version)[:3]


def parse_semver_full(version: Any) -> SemVer:
    if not isinstance(version, str):
        raise ValueError(f"invalid semver: {version}")
    match = SEMVER_RE.match(version)
    if not match:
        raise ValueError(f"invalid semver: {version}")
    major, minor, patch = (int(part) for part in match.group(1, 2, 3))
    prerelease = tuple((match.group(4) or "").split(".")) if match.group(4) else ()
    return SemVer(major, minor, patch, prerelease, match.group(5))


def bump_semver(version: str, bump_type: str) -> str:
    # Bumps apply to the core version and intentionally drop pre-release/build metadata.
    major, minor, patch = parse_semver(version)
    if bump_type == "major":
        return f"{major + 1}.0.0"
    if bump_type == "minor":
        return f"{major}.{minor + 1}.0"
    if bump_type == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"invalid bump type: {bump_type}")


def next_release_version(version: str, bump_type: str) -> str:
    """Compute the next release coordinate.

    bump_type records commit impact; for beta.N / rc.N it never authorizes
    stage/core promotion.
    """
    current = parse_semver_full(version)
    if not current.prerelease:
        return bump_semver(version, bump_type)
    _validate_bump_type(bump_type)
    if (
        len(current.prerelease) != 2
        or current.prerelease[0] not in {"beta", "rc"}
        or not current.prerelease[1].isdigit()
    ):
        raise ValueError(f"unsupported prerelease ladder: {version}")
    stage, index = current.prerelease
    return f"{current.major}.{current.minor}.{current.patch}-{stage}.{int(index) + 1}"


def validate_release_version_coordinate(
    from_version: str,
    to_version: str,
    bump_type: str | None,
    coordinate_policy: Any = None,
) -> str | None:
    if coordinate_policy is not None:
        from .coordinates import validate_coordinate_policy

        return validate_coordinate_policy(from_version, to_version, bump_type, coordinate_policy)
    if not isinstance(bump_type, str) or not bump_type:
        return "release_coordinate_off_ladder"
    try:
        expected = next_release_version(from_version, bump_type)
        if compare_semver(expected, to_version) == 0:
            return None
    except ValueError:
        pass
    return "release_coordinate_off_ladder"


def compare_semver(left: str, right: str) -> int:
    left_value = parse_semver_full(left)
    right_value = parse_semver_full(right)
    core_left = left_value[:3]
    core_right = right_value[:3]
    if core_left != core_right:
        return (core_left > core_right) - (core_left < core_right)
    return _compare_prerelease(left_value.prerelease, right_value.prerelease)


def sort_semver(versions: list[str]) -> list[str]:
    return sorted(versions, key=cmp_to_key(compare_semver))


def _validate_bump_type(bump_type: str) -> None:
    if bump_type not in {"major", "minor", "patch"}:
        raise ValueError(f"invalid bump type: {bump_type}")


def _compare_prerelease(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    if not left and not right:
        return 0
    if not left:
        return 1
    if not right:
        return -1
    for left_part, right_part in zip(left, right):
        if left_part == right_part:
            continue
        left_numeric = left_part.isdigit()
        right_numeric = right_part.isdigit()
        if left_numeric and right_numeric:
            left_int = int(left_part)
            right_int = int(right_part)
            return (left_int > right_int) - (left_int < right_int)
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return (left_part > right_part) - (left_part < right_part)
    return (len(left) > len(right)) - (len(left) < len(right))
