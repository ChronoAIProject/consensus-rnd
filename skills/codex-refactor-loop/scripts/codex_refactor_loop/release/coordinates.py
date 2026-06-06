"""Release coordinate policy shared by the gate and publish preflight."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .versions import bump_semver, compare_semver, next_release_version, parse_semver_full


@dataclass(frozen=True)
class ReleaseCoordinatePlan:
    to_version: str
    policy: dict[str, Any]


def plan_release_coordinate(from_version: str, bump_type: str) -> ReleaseCoordinatePlan:
    current = parse_semver_full(from_version)
    if not current.prerelease:
        to_version = bump_semver(from_version, bump_type)
        return ReleaseCoordinatePlan(
            to_version=to_version,
            policy=_policy(
                transition="ga_core_bump",
                from_version=from_version,
                to_version=to_version,
                bump_type=bump_type,
                from_stage=None,
                to_stage=None,
                from_index=None,
                to_index=None,
            ),
        )

    _validate_supported_prerelease(from_version)
    stage, index = current.prerelease
    if stage == "beta" and bump_type in {"minor", "major"}:
        bumped_core = bump_semver(from_version, bump_type)
        to_version = f"{bumped_core}-beta.1"
        return ReleaseCoordinatePlan(
            to_version=to_version,
            policy=_policy(
                transition="beta_core_promotion",
                from_version=from_version,
                to_version=to_version,
                bump_type=bump_type,
                from_stage=stage,
                to_stage="beta",
                from_index=int(index),
                to_index=1,
            ),
        )

    to_version = next_release_version(from_version, bump_type)
    return ReleaseCoordinatePlan(
        to_version=to_version,
        policy=_policy(
            transition="same_stage_prerelease",
            from_version=from_version,
            to_version=to_version,
            bump_type=bump_type,
            from_stage=stage,
            to_stage=stage,
            from_index=int(index),
            to_index=int(index) + 1,
        ),
    )


_UNSET = object()


def validate_coordinate_policy(
    from_version: str,
    to_version: str,
    bump_type: Any,
    candidate_policy: Any,
    decision_policy: Any = _UNSET,
) -> str | None:
    if not isinstance(bump_type, str) or not bump_type:
        return "release_coordinate_off_ladder"
    try:
        plan = plan_release_coordinate(from_version, bump_type)
    except ValueError:
        return "release_coordinate_off_ladder"
    if candidate_policy is None:
        try:
            if compare_semver(next_release_version(from_version, bump_type), to_version) == 0:
                return None
        except ValueError:
            pass
    if compare_semver(plan.to_version, to_version) != 0:
        return "release_coordinate_off_ladder"

    if not _policy_required(plan.policy) and candidate_policy is None and decision_policy is None:
        return None
    if not isinstance(candidate_policy, Mapping):
        return "coordinate_policy_missing"
    if dict(candidate_policy) != plan.policy:
        return "coordinate_policy_mismatch"
    if decision_policy is not _UNSET:
        if decision_policy is None and _policy_required(plan.policy):
            return "decision_coordinate_policy_invalid"
        if not isinstance(decision_policy, Mapping):
            return "decision_coordinate_policy_invalid"
        if dict(decision_policy) != plan.policy:
            return "decision_coordinate_policy_mismatch"
        if dict(decision_policy) != dict(candidate_policy):
            return "candidate_decision_coordinate_policy_mismatch"
    return None


def _policy_required(policy: Mapping[str, Any]) -> bool:
    return policy.get("transition") == "beta_core_promotion"


def _policy(
    *,
    transition: str,
    from_version: str,
    to_version: str,
    bump_type: str,
    from_stage: str | None,
    to_stage: str | None,
    from_index: int | None,
    to_index: int | None,
) -> dict[str, Any]:
    from_core = _core(from_version)
    to_core = _core(to_version)
    return {
        "transition": transition,
        "from_version": from_version,
        "to_version": to_version,
        "bump_type": bump_type,
        "from_core": from_core,
        "to_core": to_core,
        "from_stage": from_stage,
        "to_stage": to_stage,
        "from_prerelease_index": from_index,
        "to_prerelease_index": to_index,
    }


def _core(version: str) -> str:
    parsed = parse_semver_full(version)
    return f"{parsed.major}.{parsed.minor}.{parsed.patch}"


def _validate_supported_prerelease(version: str) -> None:
    current = parse_semver_full(version)
    if (
        len(current.prerelease) != 2
        or current.prerelease[0] not in {"beta", "rc"}
        or not current.prerelease[1].isdigit()
    ):
        raise ValueError(f"unsupported prerelease ladder: {version}")


__all__ = [
    "ReleaseCoordinatePlan",
    "plan_release_coordinate",
    "validate_coordinate_policy",
]
