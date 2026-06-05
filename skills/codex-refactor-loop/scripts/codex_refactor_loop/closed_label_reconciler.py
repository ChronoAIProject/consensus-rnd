"""Restart-managed closed item phase label reconciler for #238."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Callable, Mapping, Sequence

from . import labels as label_catalog
from .active_controller import require_active_controller, write_active_controller_status
from .closed_phase_labels import (
    ClosedPhaseLabelPlan,
    closed_reconcile_candidate_queries,
    item_matches_closed_reconcile_query,
    plan_closed_reconcile_candidate,
    plan_from_gh_item,
)
from .context import LoopContext, LoopContextError
from .github_budget import graphql_headroom_ok
from .heartbeat import DaemonHeartbeatLease


DEFAULT_INTERVAL_SECONDS = 1800


class ClosedLabelReconciler:
    """Restart-managed closed-only phase label reconciler.
    """

    def __init__(self, ctx: LoopContext, *, dry_run: bool = False) -> None:
        self.ctx = ctx
        self.dry_run = dry_run
        self._host_label_names: frozenset[str] | None = None
        self._warned_unresolved_host_labels: set[tuple[str, int, str]] = set()
        self._apply_skip_reasons: dict[tuple[str, int], str] = {}

    # Refactor (iter370/issue-370): keep the #238 reconciler alive across mixed closed-item drift.
    # Old pattern: one slow or failing item could stall the whole tick and let the daemon heartbeat expire.
    # New principle: renew during the tick and isolate each item so closed-only phase reconciliation continues.
    def run_once(self, beat: Callable[[], None] | None = None) -> int:
        if not graphql_headroom_ok(cwd=self.ctx.repo_root, env=self.ctx.env_for_subprocess()):
            _log_tick_status("closed-label-reconciler", "skip:graphql-backoff remaining=unknown")
            return 0
        decision = require_active_controller(self.ctx, "closed-label-reconciler")
        write_active_controller_status(self.ctx, decision)
        if not decision.allowed:
            print(f"closed-label-reconciler noop: active-controller {decision.status} owner={decision.owner_device}")
            _log_tick_status("closed-label-reconciler", f"noop:not-owner:{decision.status}")
            return 0
        if not self.ctx.gh_repo_slug:
            print("closed-label-reconciler noop: GH_REPO_SLUG unset")
            _log_tick_status("closed-label-reconciler", "noop:gh-repo-slug-unset")
            return 0
        plans = self.collect_plans()
        if beat is not None:
            beat()
        changed = 0
        for plan in plans:
            try:
                if not plan.needs_edit:
                    continue
                changed += 1
                if self.dry_run:
                    print(_format_plan(plan, dry_run=True))
                    continue
                live_plan = self.apply_plan(plan)
                if live_plan is None:
                    reason = self._apply_skip_reasons.pop((plan.kind, plan.number), None)
                    if reason is None:
                        print(f"closed-label-reconciler skip: {plan.kind} #{plan.number} no longer closed managed")
                    continue
                self.verify_plan(live_plan)
                print(_format_plan(live_plan, dry_run=False))
            except Exception as exc:
                print(f"closed-label-reconciler skip: {plan.kind} #{plan.number} {exc}")
            finally:
                if beat is not None:
                    beat()
        if changed == 0:
            print("closed-label-reconciler noop: no closed managed phase-label drift")
            _log_tick_status("closed-label-reconciler", "noop:no-closed-managed-phase-label-drift")
        else:
            _log_tick_status("closed-label-reconciler", f"dispatched {changed} reconciliation")
        return 0

    def collect_plans(self) -> tuple[ClosedPhaseLabelPlan, ...]:
        plans: list[ClosedPhaseLabelPlan] = []
        seen: set[tuple[str, int]] = set()
        for kind, state in (("issue", "closed"), ("pr", "closed"), ("pr", "merged")):
            for item in self._list_candidates(kind, state):
                plan = plan_closed_reconcile_candidate(kind, item)
                if plan is None:
                    continue
                key = (plan.kind, plan.number)
                if key in seen:
                    continue
                seen.add(key)
                if kind == "issue":
                    plan = plan_closed_reconcile_candidate(kind, item, linked_merged=self._issue_has_merged_pr(item))
                    if plan is None:
                        continue
                plans.append(plan)
        return tuple(plans)

    def apply_plan(self, plan: ClosedPhaseLabelPlan) -> ClosedPhaseLabelPlan | None:
        live_plan = self._live_plan(plan)
        if live_plan is None:
            return None
        if not live_plan.needs_edit:
            return live_plan
        edit_plan = self._plan_for_host_labels(live_plan)
        if edit_plan is None:
            self._warn_unresolved_host_label_once(live_plan)
            self._apply_skip_reasons[(live_plan.kind, live_plan.number)] = "unresolved-host-label"
            return None
        command = [live_plan.kind, "edit", str(live_plan.number)]
        for label in edit_plan.add_labels:
            command.extend(["--add-label", label])
        for label in edit_plan.remove_labels:
            command.extend(["--remove-label", label])
        self._gh(command)
        return live_plan

    def _warn_unresolved_host_label_once(self, plan: ClosedPhaseLabelPlan) -> None:
        key = (plan.kind, plan.number, plan.terminal_phase)
        if key in self._warned_unresolved_host_labels:
            return
        self._warned_unresolved_host_labels.add(key)
        print(
            f"closed-label-reconciler warn: {plan.kind} #{plan.number} "
            f"cannot resolve host label for terminal phase {plan.terminal_phase}; skipping phase reconciliation"
        )

    def _plan_for_host_labels(self, plan: ClosedPhaseLabelPlan) -> ClosedPhaseLabelPlan | None:
        add_labels = []
        for label in plan.add_labels:
            resolved = self._resolve_writable_label(label)
            if resolved is None:
                return None
            add_labels.append(resolved)
        return ClosedPhaseLabelPlan(
            kind=plan.kind,
            number=plan.number,
            terminal_phase=plan.terminal_phase,
            add_labels=tuple(add_labels),
            remove_labels=plan.remove_labels,
            reason=plan.reason,
        )

    def _resolve_writable_label(self, canonical_label: str) -> str | None:
        host_labels = self._host_labels()
        if canonical_label in host_labels:
            return canonical_label
        for alias in label_catalog.aliases_for(canonical_label):
            if alias in host_labels:
                return alias
        return None

    def _host_labels(self) -> frozenset[str]:
        if self._host_label_names is not None:
            return self._host_label_names
        data = self._gh_json(["label", "list", "--json", "name", "--limit", "1000"], [])
        if not isinstance(data, list):
            self._host_label_names = frozenset()
            return self._host_label_names
        self._host_label_names = frozenset(
            str(item.get("name", ""))
            for item in data
            if isinstance(item, Mapping)
        )
        return self._host_label_names

    # Refactor (iter370/issue-370): verify only the terminal phase label this daemon is authorized to own.
    # Old pattern: post-edit verification required a human label and coupled #238 cleanup to human authority.
    # New principle: fail closed on terminal phase drift without widening the reconciler into human labels.
    def verify_plan(self, plan: ClosedPhaseLabelPlan) -> None:
        item = self._view_item(plan.kind, plan.number)
        live_labels = _label_names(item)
        projection = label_catalog.normalize_label_set(live_labels)
        phases = projection.labels_for_group("phase")
        if phases != (plan.terminal_phase,):
            detail = f"expected terminal phase {plan.terminal_phase}, got {','.join(phases) or '-'}"
            raise RuntimeError(f"post-edit label invariant failed for {plan.kind} #{plan.number}: {detail}")

    def _live_plan(self, plan: ClosedPhaseLabelPlan) -> ClosedPhaseLabelPlan | None:
        item = self._view_item(plan.kind, plan.number)
        return plan_from_gh_item(plan.kind, item, linked_merged=self._issue_has_merged_pr(item) if plan.kind == "issue" else False)

    def _list_candidates(self, kind: str, state: str) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        seen: set[int] = set()
        fields = "number,state,labels,title"
        if kind == "pr":
            fields += ",mergedAt"
        for query in closed_reconcile_candidate_queries(kind, state):
            data = self._gh_json(query.gh_args(fields), [])
            if not isinstance(data, list):
                continue
            for item in data:
                if not isinstance(item, dict):
                    continue
                if not item_matches_closed_reconcile_query(kind, item, query):
                    continue
                if plan_closed_reconcile_candidate(kind, item) is None:
                    continue
                try:
                    number = int(item.get("number"))
                except (TypeError, ValueError):
                    rows.append(item)
                    continue
                if number in seen:
                    continue
                seen.add(number)
                rows.append(item)
        return rows

    def _view_item(self, kind: str, number: int) -> Mapping[str, object]:
        fields = "number,state,labels"
        if kind == "pr":
            fields += ",mergedAt"
        data = self._gh_json([kind, "view", str(number), "--json", fields], {})
        return data if isinstance(data, Mapping) else {}

    def _issue_has_merged_pr(self, item: Mapping[str, object]) -> bool:
        try:
            number = int(item.get("number"))
        except (TypeError, ValueError):
            return False
        data = self._gh_json(
            ["pr", "list", "--state", "merged", "--search", f"in:body Closes #{number}", "--limit", "1", "--json", "number,mergedAt"],
            [],
        )
        return bool(isinstance(data, list) and data)

    def _gh_json(self, args: Sequence[str], default: object) -> object:
        result = self._gh(args, check=False)
        if result.returncode != 0:
            return default
        try:
            return json.loads(result.stdout or "null")
        except json.JSONDecodeError:
            return default

    def _gh(self, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        command = ["gh", *args]
        if self.ctx.gh_repo_slug:
            command.extend(["--repo", self.ctx.gh_repo_slug])
        result = subprocess.run(
            command,
            cwd=str(self.ctx.repo_root),
            env=self.ctx.env_for_subprocess(),
            capture_output=True,
            text=True,
            check=False,
        )
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"gh failed: {' '.join(command)}")
        return result


def _label_names(item: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(
        str(label.get("name", ""))
        for label in item.get("labels", [])
        if isinstance(label, Mapping)
    )


def _format_plan(plan: ClosedPhaseLabelPlan, *, dry_run: bool) -> str:
    prefix = "dry-run" if dry_run else "applied"
    return (
        f"closed-label-reconciler {prefix}: {plan.kind} #{plan.number} "
        f"terminal={plan.terminal_phase} add={','.join(plan.add_labels) or '-'} "
        f"remove={','.join(plan.remove_labels) or '-'} reason={plan.reason}"
    )


def _log_tick_status(daemon: str, action: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {daemon}: tick {action}", flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="consensus-rnd-cli closed-label-reconciler")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--daemon", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=DEFAULT_INTERVAL_SECONDS)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        ctx = LoopContext.load(read_only=bool(args.dry_run), allow_git_root_fallback=bool(args.dry_run), cwd=os.getcwd())
    except LoopContextError as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 2
    reconciler = ClosedLabelReconciler(ctx, dry_run=bool(args.dry_run))
    if args.daemon:
        lease = DaemonHeartbeatLease("closed_label_reconciler", ctx.repo_root)
        lease.beat()
        interval = max(1, int(args.interval_seconds))
        while True:
            reconciler.run_once(beat=lease.beat)
            lease.sleep_with_lease(interval)
    return reconciler.run_once()


if __name__ == "__main__":
    raise SystemExit(main())
