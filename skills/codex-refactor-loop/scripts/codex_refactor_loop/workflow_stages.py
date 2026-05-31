"""Closed workflow stage registry for codex-refactor-loop.

"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowStage:
    slug: str
    title: str
    contract: str
    detail_anchor: str
    legacy_number: int


WORKFLOW_STAGES: tuple[WorkflowStage, ...] = (
    WorkflowStage(
        "bootstrap",
        "Bootstrap",
        "Session bootstrap is ordered and mandatory before normal routing.",
        "bootstrap-details",
        0,
    ),
    WorkflowStage(
        "work-intake",
        "Work Intake",
        "Fallback issue production when no actionable managed issue/PR exists; audit is the built-in compatibility producer.",
        "work-unit-contract",
        1,
    ),
    WorkflowStage(
        "implementation",
        "Implementation",
        "Implement one codex per active work unit in the batch.",
        "phase-routing-details",
        2,
    ),
    WorkflowStage(
        "verification",
        "Verification",
        "Verify with a separate codex from the implementer.",
        "recovery-playbook",
        3,
    ),
    WorkflowStage(
        "publish",
        "Publish",
        "Controller commits, merges, pushes, and opens PRs.",
        "merge-and-push-details",
        4,
    ),
    WorkflowStage(
        "ci-watch",
        "CI Watch",
        "Watch remote CI after push; classify failures and route fix/test-add work immediately.",
        "remote-ci-details",
        5,
    ),
    WorkflowStage(
        "integration-sync",
        "Integration Sync",
        "Integration sync is daemon-owned detect-and-emit plus controller-owned git apply.",
        "daemon-command-bodies",
        6,
    ),
    WorkflowStage(
        "design-intake",
        "Design Intake",
        "Sweep design issues and maintainer comments every wakeup.",
        "design-issue-details",
        7,
    ),
    WorkflowStage(
        "review-gate",
        "Review Gate",
        "Three independent PR reviewers; fixes loop until reviewer consensus or meta-layer reflection.",
        "review-gate-details",
        8,
    ),
    WorkflowStage(
        "design-consensus",
        "Design Consensus",
        "Three solvers plus meta-judge. Sole authorization gate for concrete plans.",
        "design-consensus-details",
        9,
    ),
)

_STAGES_BY_SLUG = {stage.slug: stage for stage in WORKFLOW_STAGES}


def stage_by_slug(slug: str, extra_stages: tuple[WorkflowStage, ...] = ()) -> WorkflowStage:
    stages = {**_STAGES_BY_SLUG, **{stage.slug: stage for stage in extra_stages}}
    try:
        return stages[slug]
    except KeyError as exc:
        raise ValueError(f"unknown workflow stage slug: {slug}") from exc


def assert_stage_slug(slug: str, extra_stages: tuple[WorkflowStage, ...] = ()) -> None:
    stage_by_slug(slug, extra_stages)


def format_stage(stage: WorkflowStage | str, extra_stages: tuple[WorkflowStage, ...] = ()) -> str:
    if isinstance(stage, WorkflowStage):
        slug = stage.slug
    else:
        slug = stage_by_slug(stage, extra_stages).slug
    return f"Consensus-rnd Phase {slug}"
