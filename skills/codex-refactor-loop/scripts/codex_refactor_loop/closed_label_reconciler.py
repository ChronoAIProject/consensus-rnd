"""Restart-managed closed item phase label reconciler for #238."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Mapping, Sequence

from . import labels as label_catalog
from .active_controller import require_active_controller, write_active_controller_status
from .closed_phase_labels import ClosedPhaseLabelPlan, plan_from_gh_item
from .context import LoopContext, LoopContextError
from .heartbeat import DaemonHeartbeatLease


DEFAULT_INTERVAL_SECONDS = 1800


class ClosedLabelReconciler:
    """Refactor (issue238/closed-label-reconciler):
      Old pattern: closed managed items relied on controller close-path cleanup
      or peek hints. New principle: a restart-managed closed-only daemon owns
      the narrow gh-label-closed-reconcile authority.
    """

    def __init__(self, ctx: LoopContext, *, dry_run: bool = False) -> None:
        self.ctx = ctx
        self.dry_run = dry_run

    def run_once(self) -> int:
        decision = require_active_controller(self.ctx, "closed-label-reconciler")
        write_active_controller_status(self.ctx, decision)
        if not decision.allowed:
            print(f"closed-label-reconciler noop: active-controller {decision.status} owner={decision.owner_device}")
            return 0
        if not self.ctx.gh_repo_slug:
            print("closed-label-reconciler noop: GH_REPO_SLUG unset")
            return 0
        plans = self.collect_plans()
        changed = 0
        for plan in plans:
            if not plan.needs_edit:
                continue
            changed += 1
            if self.dry_run:
                print(_format_plan(plan, dry_run=True))
                continue
            live_plan = self.apply_plan(plan)
            if live_plan is None:
                print(f"closed-label-reconciler skip: {plan.kind} #{plan.number} no longer closed managed")
                continue
            self.verify_plan(live_plan)
            print(_format_plan(live_plan, dry_run=False))
        if changed == 0:
            print("closed-label-reconciler noop: no closed managed phase-label drift")
        return 0

    def collect_plans(self) -> tuple[ClosedPhaseLabelPlan, ...]:
        plans: list[ClosedPhaseLabelPlan] = []
        seen: set[tuple[str, int]] = set()
        for kind, state in (("issue", "closed"), ("pr", "closed"), ("pr", "merged")):
            for item in self._list_managed(kind, state):
                plan = plan_from_gh_item(kind, item, linked_merged=self._issue_has_merged_pr(item) if kind == "issue" else False)
                if plan is None:
                    continue
                key = (plan.kind, plan.number)
                if key in seen:
                    continue
                seen.add(key)
                plans.append(plan)
        return tuple(plans)

    def apply_plan(self, plan: ClosedPhaseLabelPlan) -> ClosedPhaseLabelPlan | None:
        live_plan = self._live_plan(plan)
        if live_plan is None:
            return None
        if not live_plan.needs_edit:
            return live_plan
        command = [live_plan.kind, "edit", str(live_plan.number)]
        for label in live_plan.add_labels:
            command.extend(["--add-label", label])
        for label in live_plan.remove_labels:
            command.extend(["--remove-label", label])
        self._gh(command)
        return live_plan

    def verify_plan(self, plan: ClosedPhaseLabelPlan) -> None:
        item = self._view_item(plan.kind, plan.number)
        live_labels = _label_names(item)
        ok, errors = label_catalog.validate_exactly_one_phase_human(live_labels)
        if not ok or plan.terminal_phase not in label_catalog.normalize_label_set(live_labels).canonical:
            detail = "; ".join(errors) if errors else "terminal phase missing"
            raise RuntimeError(f"post-edit label invariant failed for {plan.kind} #{plan.number}: {detail}")

    def _live_plan(self, plan: ClosedPhaseLabelPlan) -> ClosedPhaseLabelPlan | None:
        item = self._view_item(plan.kind, plan.number)
        return plan_from_gh_item(plan.kind, item, linked_merged=self._issue_has_merged_pr(item) if plan.kind == "issue" else False)

    def _list_managed(self, kind: str, state: str) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        seen: set[int] = set()
        fields = "number,state,labels,title"
        if kind == "pr":
            fields += ",mergedAt"
        for query_label in label_catalog.query_labels_for(label_catalog.MANAGED):
            data = self._gh_json([kind, "list", "--label", query_label, "--state", state, "--limit", "100", "--json", fields], [])
            if not isinstance(data, list):
                continue
            for item in data:
                if not isinstance(item, dict):
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
            reconciler.run_once()
            lease.sleep_with_lease(interval)
    return reconciler.run_once()


if __name__ == "__main__":
    raise SystemExit(main())
