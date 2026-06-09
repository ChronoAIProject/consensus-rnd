"""Declaration-only phase-label contract for source-regression tests."""

from __future__ import annotations


PHASE_LABEL_CONTRACT = {
    "contract_owner": "codex_refactor_loop.labels and codex_refactor_loop.closed_phase_labels",
    "open_managed_reader": "codex_refactor_loop.managed_work_snapshot.ManagedWorkSnapshot",
    "closed_reconciler_planner": "codex_refactor_loop.closed_phase_labels.plan_closed_reconcile_candidate",
    "execution_import_policy": "label mutation execution modules must not import codex_refactor_loop.phase_label_contract",
}


__all__ = ("PHASE_LABEL_CONTRACT",)
