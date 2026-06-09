"""Loop-owned GitHub label catalog for consensus-loop."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable, Sequence

from .context import LoopContext, LoopContextError


CANONICAL_PREFIX = "crnd"
LABEL_GROUPS = ("phase", "human", "lifecycle", "triage", "milestone")
SLUG_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
CANONICAL_RE = re.compile(r"^crnd:(phase|human|lifecycle|triage|milestone):[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")

EXTERNAL_DEFAULT_LABELS = frozenset(
    {
        "bug",
        "documentation",
        "duplicate",
        "enhancement",
        "good first issue",
        "help wanted",
        "invalid",
        "question",
        "wontfix",
    }
)


@dataclass(frozen=True)
class LabelSpec:
    name: str
    group: str
    slug: str
    description: str
    color: str
    exclusive_axis: str | None = None
    expected_workers: int = 0
    route_actor: str | None = None


@dataclass(frozen=True)
class LabelProjection:
    canonical: frozenset[str]
    unknown_crnd: frozenset[str]

    def labels_for_group(self, group: str) -> tuple[str, ...]:
        return tuple(sorted(label for label in self.canonical if _spec_by_name[label].group == group))

    @property
    def phase(self) -> str | None:
        labels = self.labels_for_group("phase")
        return labels[0] if labels else None

    @property
    def human(self) -> str | None:
        labels = self.labels_for_group("human")
        return labels[0] if labels else None


@dataclass(frozen=True)
class MigrationPlan:
    create: tuple[LabelSpec, ...]
    update: tuple[LabelSpec, ...]
    unknown_crnd: tuple[str, ...]
    external_defaults: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "create": [_spec_dict(spec) for spec in self.create],
            "update": [_spec_dict(spec) for spec in self.update],
            "unknown_crnd": list(self.unknown_crnd),
            "external_defaults": list(self.external_defaults),
        }


def canonical_name(group: str, slug: str) -> str:
    if group not in LABEL_GROUPS:
        raise ValueError(f"unknown label group: {group}")
    if not SLUG_RE.fullmatch(slug):
        raise ValueError(f"invalid label slug: {slug}")
    return f"{CANONICAL_PREFIX}:{group}:{slug}"


def _spec(
    group: str,
    slug: str,
    description: str,
    color: str,
    *,
    exclusive_axis: str | None = None,
    expected_workers: int = 0,
    route_actor: str | None = None,
) -> LabelSpec:
    return LabelSpec(
        name=canonical_name(group, slug),
        group=group,
        slug=slug,
        description=description,
        color=color,
        exclusive_axis=exclusive_axis,
        expected_workers=expected_workers,
        route_actor=route_actor,
    )


LABEL_SPECS: tuple[LabelSpec, ...] = (
    _spec("phase", "design-solving", "Consensus design solving is active.", "0e8a16", exclusive_axis="phase", expected_workers=1, route_actor="phase9-solver-or-judge"),
    _spec("phase", "consensus-reached", "Consensus is reached and implementation is ready.", "1d76db", exclusive_axis="phase", route_actor="implement-codex"),
    _spec("phase", "implementing", "Implementation worker is active.", "c5def5", exclusive_axis="phase", expected_workers=1, route_actor="implement-codex"),
    _spec("phase", "pr-open", "Pull request is open and awaiting review or CI routing.", "5319e7", exclusive_axis="phase", route_actor="reviewer-codex"),
    _spec("phase", "reviewing", "Review workers are active.", "5319e7", exclusive_axis="phase", expected_workers=1, route_actor="reviewer-codex"),
    _spec("phase", "fixing", "Fix worker is active.", "d93f0b", exclusive_axis="phase", expected_workers=1, route_actor="fix-codex"),
    _spec("phase", "ci-running", "CI watch is active.", "fbca04", exclusive_axis="phase", route_actor="controller-ci-watch"),
    _spec("phase", "blocked", "Work is blocked or explicitly waiting.", "b60205", exclusive_axis="phase", route_actor="controller"),
    _spec("phase", "merged", "Work has landed.", "0e8a16", exclusive_axis="phase", route_actor="controller"),
    _spec("phase", "closed", "Closed terminal protocol state without merged evidence.", "ededed", exclusive_axis="phase", route_actor="closed-label-reconciler"),
    _spec("human", "auto", "Controller may continue without maintainer intervention.", "bfd4f2", exclusive_axis="human"),
    _spec("human", "maintainer-decision", "Maintainer decision is required.", "b60205", exclusive_axis="human"),
    _spec("lifecycle", "managed", "Item is managed by consensus-loop.", "ededed"),
    _spec("lifecycle", "stuck", "Item is stalled and waiting for loop recovery.", "b60205"),
    _spec("lifecycle", "no-framing", "Item has no actionable framing.", "d4c5f9"),
    _spec("triage", "pending", "External issue is pending manual intake triage.", "fbca04"),
    _spec("triage", "resume-requested", "Maintainer requested resumed implementation.", "1d76db"),
    _spec("milestone", "current", "Milestone-priority item.", "f9d0c4"),
    _spec("milestone", "release-target", "Release countdown target issue/PR.", "f9d0c4"),
)

_spec_by_name = {spec.name: spec for spec in LABEL_SPECS}

MANAGED = canonical_name("lifecycle", "managed")
STUCK = canonical_name("lifecycle", "stuck")
NO_FRAMING = canonical_name("lifecycle", "no-framing")
TRIAGE_PENDING = canonical_name("triage", "pending")
TRIAGE_RESUME_REQUESTED = canonical_name("triage", "resume-requested")
MILESTONE_CURRENT = canonical_name("milestone", "current")
MILESTONE_RELEASE_TARGET = canonical_name("milestone", "release-target")
HUMAN_AUTO = canonical_name("human", "auto")
HUMAN_MAINTAINER_DECISION = canonical_name("human", "maintainer-decision")
PHASE_DESIGN_SOLVING = canonical_name("phase", "design-solving")
PHASE_CONSENSUS_REACHED = canonical_name("phase", "consensus-reached")
PHASE_IMPLEMENTING = canonical_name("phase", "implementing")
PHASE_PR_OPEN = canonical_name("phase", "pr-open")
PHASE_REVIEWING = canonical_name("phase", "reviewing")
PHASE_FIXING = canonical_name("phase", "fixing")
PHASE_CI_RUNNING = canonical_name("phase", "ci-running")
PHASE_BLOCKED = canonical_name("phase", "blocked")
PHASE_MERGED = canonical_name("phase", "merged")
PHASE_CLOSED = canonical_name("phase", "closed")


def canonical_labels() -> tuple[str, ...]:
    return tuple(spec.name for spec in LABEL_SPECS)


def labels_for_group(group: str) -> tuple[str, ...]:
    if group not in LABEL_GROUPS:
        raise ValueError(f"unknown label group: {group}")
    return tuple(spec.name for spec in LABEL_SPECS if spec.group == group)


def phase_expected_workers(label: str) -> int:
    projection = normalize_label_set([label])
    phase = projection.phase
    return _spec_by_name[phase].expected_workers if phase else 0


def actor_for_phase(label: str) -> str | None:
    projection = normalize_label_set([label])
    phase = projection.phase
    return _spec_by_name[phase].route_actor if phase else None


def is_loop_owned(label: str) -> bool:
    return bool(label.startswith(f"{CANONICAL_PREFIX}:"))


def is_allowed_external(label: str) -> bool:
    return label in EXTERNAL_DEFAULT_LABELS


def normalize_label_set(labels: Iterable[str]) -> LabelProjection:
    canonical: set[str] = set()
    unknown: set[str] = set()
    for raw in labels:
        label = str(raw)
        if label in _spec_by_name:
            canonical.add(label)
        elif is_loop_owned(label):
            unknown.add(label)
    return LabelProjection(
        canonical=frozenset(canonical),
        unknown_crnd=frozenset(unknown),
    )


def validate_exactly_one_phase_human(labels: Iterable[str]) -> tuple[bool, list[str]]:
    projection = normalize_label_set(labels)
    errors: list[str] = []
    phases = projection.labels_for_group("phase")
    humans = projection.labels_for_group("human")
    if len(phases) != 1:
        errors.append(f"expected exactly one canonical phase label, got {len(phases)}")
    if len(humans) != 1:
        errors.append(f"expected exactly one canonical human label, got {len(humans)}")
    if projection.unknown_crnd:
        errors.append("unknown crnd labels: " + ",".join(sorted(projection.unknown_crnd)))
    return not errors, errors


def query_labels_for(label: str) -> tuple[str, ...]:
    return (label,)


def design_issue_label_bundle() -> tuple[str, ...]:
    return (MANAGED, PHASE_DESIGN_SOLVING, HUMAN_AUTO)


def migration_plan(live_labels: Iterable[dict[str, str] | str]) -> MigrationPlan:
    live_by_name: dict[str, dict[str, str]] = {}
    for item in live_labels:
        if isinstance(item, str):
            live_by_name[item] = {"name": item}
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            live_by_name[str(item["name"])] = item
    create: list[LabelSpec] = []
    update: list[LabelSpec] = []
    for spec in LABEL_SPECS:
        live = live_by_name.get(spec.name)
        if live is None:
            create.append(spec)
            continue
        if str(live.get("description", "")) != spec.description or str(live.get("color", "")).lower().lstrip("#") != spec.color:
            update.append(spec)
    projection = normalize_label_set(live_by_name)
    return MigrationPlan(
        create=tuple(create),
        update=tuple(update),
        unknown_crnd=tuple(sorted(projection.unknown_crnd)),
        external_defaults=tuple(sorted(label for label in live_by_name if is_allowed_external(label))),
    )


def assert_catalog_valid() -> None:
    names = canonical_labels()
    if len(names) != len(set(names)):
        raise AssertionError("duplicate canonical label names")
    for spec in LABEL_SPECS:
        if spec.group not in LABEL_GROUPS:
            raise AssertionError(f"unknown group for {spec.name}")
        if spec.name != canonical_name(spec.group, spec.slug):
            raise AssertionError(f"canonical name mismatch for {spec.name}")
        spec.name.encode("ascii")
        if not CANONICAL_RE.fullmatch(spec.name):
            raise AssertionError(f"invalid canonical label: {spec.name}")
        if not re.fullmatch(r"[0-9a-fA-F]{6}", spec.color):
            raise AssertionError(f"invalid label color for {spec.name}")


def _spec_dict(spec: LabelSpec) -> dict[str, object]:
    return {
        "name": spec.name,
        "group": spec.group,
        "slug": spec.slug,
        "description": spec.description,
        "color": spec.color,
    }


def _load_gh_labels() -> list[dict[str, str]]:
    try:
        ctx = LoopContext.load(env=dict(os.environ), read_only=True, cwd=os.getcwd())
    except LoopContextError as exc:
        raise RuntimeError(f"cannot load host context for gh label list: {exc}") from exc
    if not ctx.gh_repo_slug:
        raise RuntimeError("GH_REPO_SLUG is unset; unsafe to infer GitHub repository")
    result = subprocess.run(
        ["gh", "label", "list", "--repo", ctx.gh_repo_slug, "--json", "name,description,color", "--limit", "1000"],
        cwd=str(ctx.repo_root),
        env=ctx.env_for_subprocess(),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "gh label list failed")
    try:
        data = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid gh label JSON: {exc}") from exc
    if not isinstance(data, list):
        raise RuntimeError("gh label list returned non-list JSON")
    return [item for item in data if isinstance(item, dict)]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="consensus-rnd-cli labels")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate-catalog")
    check = sub.add_parser("check-github")
    check.add_argument("--plan", action="store_true")
    sub.add_parser("design-issue-labels")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "validate-catalog":
        assert_catalog_valid()
        print("labels catalog valid")
        return 0
    if args.command == "check-github":
        plan = migration_plan(_load_gh_labels())
        print(json.dumps(plan.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 1 if plan.unknown_crnd or (not args.plan and (plan.create or plan.update)) else 0
    if args.command == "design-issue-labels":
        print(",".join(design_issue_label_bundle()))
        return 0
    raise AssertionError(f"unhandled labels command: {args.command}")


assert_catalog_valid()


if __name__ == "__main__":
    sys.exit(main())
