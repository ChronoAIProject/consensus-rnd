"""Read-only closed-item phase label projection for #238."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from . import labels as label_catalog


@dataclass(frozen=True)
class ClosedPhaseLabelPlan:
    """Read-only terminal phase-label plan for closed managed items."""

    kind: str
    number: int
    terminal_phase: str
    add_labels: tuple[str, ...]
    remove_labels: tuple[str, ...]
    reason: str

    @property
    def needs_edit(self) -> bool:
        return bool(self.add_labels or self.remove_labels)


def plan_closed_phase_labels(
    *,
    kind: str,
    number: int,
    state: str,
    labels: Sequence[str],
    merged: bool = False,
    managed: bool | None = None,
    linked_merged: bool = False,
) -> ClosedPhaseLabelPlan | None:
    projection = label_catalog.normalize_label_set(labels)
    is_closed = state.upper() in {"CLOSED", "MERGED"}
    is_managed = label_catalog.MANAGED in projection.canonical if managed is None else managed
    if not is_closed or not is_managed:
        return None

    terminal = _terminal_phase(kind=kind, merged=merged, linked_merged=linked_merged, projection=projection)
    terminal_present = False
    remove = set()
    for label in labels:
        label_projection = label_catalog.normalize_label_set([label])
        phase_labels = set(label_projection.labels_for_group("phase"))
        if terminal in phase_labels:
            terminal_present = True
        if label_projection.cleanup_only or label_catalog.STUCK in label_projection.canonical:
            remove.add(label)
        elif phase_labels and terminal not in phase_labels:
            remove.add(label)
    add = () if terminal_present else (terminal,)
    return ClosedPhaseLabelPlan(
        kind=kind,
        number=number,
        terminal_phase=terminal,
        add_labels=tuple(sorted(add)),
        remove_labels=tuple(sorted(remove)),
        reason=_reason(kind=kind, merged=merged, linked_merged=linked_merged, terminal=terminal),
    )


def labels_after_plan(labels: Sequence[str], plan: ClosedPhaseLabelPlan) -> tuple[str, ...]:
    live = set(labels)
    live.difference_update(plan.remove_labels)
    live.update(plan.add_labels)
    return tuple(sorted(live))


# Refactor (iter370/issue-370): expose the closed-item postcondition as terminal-phase-only.
# Old pattern: the helper name and validation bundled phase cleanup with human-label presence.
# New principle: #238 helpers assert one terminal phase while preserving human-label ownership elsewhere.
def has_exactly_one_terminal_phase(labels: Sequence[str], terminal_phase: str) -> bool:
    projection = label_catalog.normalize_label_set(labels)
    return projection.labels_for_group("phase") == (terminal_phase,)


def _terminal_phase(
    *,
    kind: str,
    merged: bool,
    linked_merged: bool,
    projection: label_catalog.LabelProjection,
) -> str:
    if kind == "pr" and merged:
        return label_catalog.PHASE_MERGED
    if linked_merged:
        return label_catalog.PHASE_MERGED
    if label_catalog.PHASE_MERGED in projection.canonical:
        return label_catalog.PHASE_MERGED
    return label_catalog.PHASE_CLOSED


def _reason(*, kind: str, merged: bool, linked_merged: bool, terminal: str) -> str:
    if terminal == label_catalog.PHASE_MERGED and kind == "pr" and merged:
        return "merged-pr"
    if terminal == label_catalog.PHASE_MERGED and linked_merged:
        return "closed-by-merged-pr"
    if terminal == label_catalog.PHASE_MERGED:
        return "existing-merged-evidence"
    return "closed-no-merged-evidence"


def plan_from_gh_item(kind: str, item: Mapping[str, object], *, linked_merged: bool = False) -> ClosedPhaseLabelPlan | None:
    labels = [
        str(label.get("name", ""))
        for label in item.get("labels", [])
        if isinstance(label, Mapping)
    ]
    raw_state = str(item.get("state") or "")
    merged = bool(item.get("mergedAt")) or raw_state.upper() == "MERGED"
    try:
        number = int(item.get("number"))
    except (TypeError, ValueError):
        return None
    state = "MERGED" if merged else raw_state
    return plan_closed_phase_labels(
        kind=kind,
        number=number,
        state=state,
        labels=labels,
        merged=merged,
        linked_merged=linked_merged,
    )
