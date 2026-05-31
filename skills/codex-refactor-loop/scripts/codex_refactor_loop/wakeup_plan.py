#!/usr/bin/env python3
"""Read-only wakeup planner for codex-refactor-loop controllers.

Refactor (iter1/wakeup-plan-script):
  Old pattern: controller wakeups assembled priority from peek.sh, log greps,
  GitHub checks, and floor rules by hand, making milestone and audit-none
  ordering easy to drift.
  New principle: one read-only prioritized-next-action script emits structured
  JSON from local evidence plus GitHub labels while leaving every lifecycle
  action to the controller.

Allowed: read `.refactor-loop` files, read daemon heartbeats, run read-only
GitHub list/check/view commands, observe git topology with the issue-190
allowlist (`git fetch origin --quiet`, `git worktree list --porcelain`,
`git rev-parse --verify HEAD`, `git rev-parse --verify refs/remotes/origin/<head>`,
and `git rev-list --count refs/remotes/origin/<head>..HEAD`), and print JSON
recommendations. Forbidden: no restart/spawn, no git lifecycle or mutation
commands, no GitHub lifecycle mutation, no commit, push, checkout/switch,
branch create/delete/update, worktree add/remove/prune, reset, rebase, merge,
label, issue/PR create/close/edit, tag, release, or worker dispatch.
Authorization source:
`skills/codex-refactor-loop/authorizations/runtime-exceptions.md#maintainer-directive-wakeup-plan-script`.
Issue-190 consensus source:
`.refactor-loop/runs/phase9-issue190-r3-judge.md`.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codex_refactor_loop import labels as label_catalog
from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.pr_checks import PrChecksProjection
from codex_refactor_loop.restart import restart_managed_daemon_names
from codex_refactor_loop.transition_assessment import TransitionAssessmentReader, transition_rank_key
from codex_refactor_loop.work_items import ManagedWorkProjection, open_actionable_managed_items
from codex_refactor_loop.workflow_spec import WorkflowSpecError, load_validated_workflow_spec
from codex_refactor_loop.workflow_stages import assert_stage_slug


STALE_SECONDS = 90
DONE_PREFIXES = (
    "AUDIT_DONE",
    "SOLVER_DONE",
    "META_JUDGE_DONE",
    "META_RESOLVED",
    "IMPLEMENT_DONE",
    "VERIFY_DONE",
    "REVIEW_DONE",
    "FIX_DONE",
    "TEST_ADD_DONE",
    "TRIAGE_DECISION_DONE",
)
PHASE_TO_STAGE = {
    label_catalog.PHASE_DESIGN_SOLVING: "design-consensus",
    label_catalog.PHASE_IMPLEMENTING: "implementation",
    label_catalog.PHASE_FIXING: "review-gate",
    label_catalog.PHASE_REVIEWING: "review-gate",
    label_catalog.PHASE_CI_RUNNING: "ci-watch",
    label_catalog.PHASE_PR_OPEN: "review-gate",
    label_catalog.PHASE_CONSENSUS_REACHED: "implementation",
    label_catalog.PHASE_BLOCKED: "bootstrap",
    label_catalog.PHASE_MERGED: "publish",
}
NON_ACTION_PHASE_LABELS = {
    label_catalog.PHASE_PR_OPEN: "pr-open",
    label_catalog.PHASE_CI_RUNNING: "ci-running",
    label_catalog.PHASE_BLOCKED: "blocked",
    label_catalog.PHASE_MERGED: "merged",
}


@dataclass(frozen=True)
class GhItem:
    kind: str
    number: int
    title: str
    labels: tuple[str, ...]
    head_ref: str | None = None
    body: str = ""

    @property
    def item(self) -> str:
        return f"{self.kind} #{self.number}"

    @property
    def milestone(self) -> bool:
        return label_catalog.MILESTONE_CURRENT in label_catalog.normalize_label_set(self.labels).canonical


def run_json(cmd: list[str], *, cwd: Path) -> Any:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    text = result.stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def load_host_workflow_projection(repo_root: Path) -> tuple[list[dict[str, Any]], str | None]:
    # Refactor (iter219/issue-219):
    #   Old pattern: host 无法按 GitHub 模板自定义事件流/工作流/issue/prompt;workflow vocabulary 是闭集硬编码
    #   New principle: 引入 data-only HostWorkflowSpec(HOST_WORKFLOW_SPEC,repo-relative JSON)+ WorkflowInvariantValidator;空/未设=built-in 行为;host 只能在 host: 命名空间加 data,不能覆盖 built-in/降共识闸/夺 lifecycle authority。严格按 plan 'Concrete plan' 逐条改,首版 scope 受限。
    try:
        ctx = LoopContext.load(repo_root=repo_root, env=os.environ, cwd=repo_root, read_only=True)
        spec = load_validated_workflow_spec(ctx)
    except WorkflowSpecError as exc:
        return [], str(exc)
    except Exception as exc:
        return [], f"host workflow spec unavailable: {exc}"
    actions = [
        {
            "priority": 7,
            "kind": "host-workflow-event",
            "item": event.name,
            "phase": event.stage,
            "actor": event.actor,
            "status": event.status,
            "route": "host-workflow-status-projection",
            "no_lifecycle_authority": True,
        }
        for event in spec.events
    ]
    return actions, None


def configured_floor() -> int:
    try:
        floor = int(os.environ.get("CODEX_FLOOR", "5"))
    except ValueError:
        floor = 5
    return max(2, floor)


def resolve_repo_root(arg_root: str | None) -> Path:
    # Refactor (iter316/issue-316):
    #   Old pattern: wakeup_plan guessed repo root from cwd and parsed host.env itself.
    #   New principle: LoopContext owns repo-root/host.env loading; no private cwd default or parser.
    ctx = LoopContext.load(repo_root=arg_root, env=os.environ, cwd=Path.cwd(), read_only=True)
    return ctx.repo_root


def import_concurrency_monitor(repo_root: Path) -> Any | None:
    # Refactor (iter2/wakeup-plan-hardgate):
    #   Old pattern: wakeup routing could finish with a hidden concurrency gap
    #   because only the daemon knew the canonical count.
    #   New principle: wakeup_plan imports the daemon's read-only count/expected
    #   helpers after pinning REPO_ROOT, so the controller sees a hard gate before
    #   it can end the wakeup.
    os.environ["REPO_ROOT"] = str(repo_root)
    try:
        module_name = "codex_refactor_loop.monitors.concurrency"
        if module_name in sys.modules:
            return importlib.reload(sys.modules[module_name])
        return importlib.import_module(module_name)
    except Exception:
        return None


def build_concurrency_monitor(repo_root: Path, module: Any | None) -> Any | None:
    if module is None:
        return None
    try:
        return module.ConcurrencyMonitor(LoopContext.load(repo_root=repo_root))
    except Exception:
        return None


def canonical_actual_count(repo_root: Path, monitor: Any | None) -> int:
    if monitor is not None:
        try:
            return int(monitor.count_in_flight_codex())
        except Exception:
            pass
    env = os.environ.copy()
    env["REPO_ROOT"] = str(repo_root)
    script_path = Path(__file__).resolve().parents[1] / "consensus-rnd-cli"
    result = subprocess.run(
        [sys.executable, str(script_path), "concurrency", "--count-only"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        return 0


def canonical_expected_from_active_tasks(monitor: Any | None) -> tuple[int, list[dict[str, Any]]]:
    if monitor is None:
        return 0, []
    try:
        items = monitor.list_auto_loop_issues()
        expected, breakdown = monitor.compute_expected(items)
        return int(expected), list(breakdown)
    except Exception:
        return 0, []


def expected_from_open_items(items: list[GhItem]) -> tuple[int, list[dict[str, Any]]]:
    breakdown: list[dict[str, Any]] = []
    total = 0
    # Refactor (impl/issue239-linkage):
    #   Old pattern: wakeup hard-gate counted parent issue and child PR as
    #   separate active work. New principle: share ManagedWorkProjection with
    #   the daemon so represented parents are non-action expected_workers=0.
    for item in ManagedWorkProjection(_projection_items(items)).effective_worker_items():
        labels = set(item.labels)
        if label_catalog.HUMAN_MAINTAINER_DECISION in label_catalog.normalize_label_set(labels).canonical:
            continue
        phase_label = item.phase or label_catalog.normalize_label_set(item.labels).phase or ""
        expected = label_catalog.phase_expected_workers(phase_label)
        if expected <= 0:
            continue
        breakdown.append({"id": f"#{item.number}", "kind": item.kind, "phase": phase_label, "expected": expected})
        total += expected
    return total, breakdown


def concurrency_plan(repo_root: Path, *, fixed_point: bool, gh_items: list[GhItem] | None = None) -> dict[str, Any]:
    # Refactor (issue-277):
    #   Old pattern: AUDIT_DONE:none:0 converted a positive deficit into an
    #   exemption, then floor-no-exemption made audit repeatable without a slot.
    #   New principle: positive deficits stay visible; duplicate same-iteration
    #   audit is not legal dispatch when that audit is already active.
    concurrency_module = import_concurrency_monitor(repo_root)
    monitor = build_concurrency_monitor(repo_root, concurrency_module)
    actual = canonical_actual_count(repo_root, monitor)
    expected, breakdown = expected_from_open_items(gh_items or [])
    if expected == 0:
        expected, breakdown = canonical_expected_from_active_tasks(monitor)
    floor = configured_floor()
    target = max(floor, expected)
    deficit = max(0, target - actual)
    hard_gate_active = deficit > 0
    hard_gate_line = f"HARD_GATE:dispatch_required={deficit}" if hard_gate_active else None
    boundary = None
    if deficit > 0 and expected == 0 and concurrency_module is not None:
        try:
            boundary = concurrency_module.single_active_audit_boundary(repo_root, monitor, gh_items or [], None)
        except Exception:
            boundary = None
    if boundary is not None:
        hard_gate_active = False
        hard_gate_line = None
    return {
        "actual": actual,
        "expected_from_active_tasks": expected,
        "expected_breakdown": breakdown,
        "floor": floor,
        "target": target,
        "deficit": deficit,
        "fixed_point": fixed_point,
        "hard_gate": {
            "active": hard_gate_active,
            "dispatch_required": deficit if hard_gate_active else 0,
            "line": hard_gate_line,
            "semantics": (
                "controller must dispatch this many actionable tasks or audit fallback before ending the wakeup"
                if hard_gate_active
                else None
            ),
            "reason": "single_active_audit_in_flight" if boundary is not None else None,
            "blocked_deficit": deficit if boundary is not None else 0,
            "boundary_task_id": boundary.task_id if boundary is not None else None,
        },
    }


def daemon_health(repo_root: Path, now: float | None = None) -> dict[str, Any]:
    # Refactor (iterissue-331/issue-331):
    #   Old pattern: release gate and wakeup_plan each kept local daemon-name
    #   literals, drifting from restart.py DAEMON_COMMANDS and duplicating the
    #   source of truth.
    #   New principle: restart.py::restart_managed_daemon_names() is the
    #   canonical daemon-name projection; release keeps DAEMON_NAMES only as a
    #   derived alias, wakeup deletes EXPECTED_DAEMONS, and health requires
    #   every restart-managed heartbeat to be fresh.
    if now is None:
        now = time.time()
    heartbeat_dir = repo_root / ".refactor-loop" / "heartbeats"
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    if heartbeat_dir.exists():
        for path in sorted(heartbeat_dir.glob("*.ts")):
            name = path.stem
            seen.add(name)
            try:
                raw = path.read_text(encoding="utf-8").strip()
                timestamp = int(raw)
                age = max(0, int(now - timestamp))
                status = "stale" if age > STALE_SECONDS else "fresh"
                items.append({"name": name, "status": status, "age_seconds": age})
            except (OSError, ValueError):
                items.append({"name": name, "status": "stale", "age_seconds": None})
    for name in restart_managed_daemon_names():
        if name not in seen:
            items.append({"name": name, "status": "missing", "age_seconds": None})
    needs_restart = any(item["status"] in {"stale", "missing"} for item in items)
    return {
        "stale_seconds": STALE_SECONDS,
        "items": sorted(items, key=lambda item: item["name"]),
        "ok": not needs_restart,
        "recommendation": "consensus-rnd-cli restart-daemons" if needs_restart else None,
    }


def is_clean_exit(log_path: Path) -> bool:
    tail = tail_lines(log_path, 5)
    return any(line == "EXIT=0" for line in tail)


def tail_lines(path: Path, count: int) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").splitlines()[-count:]
    except OSError:
        return []


def marker_from_completed_log(log_path: Path) -> str | None:
    if not is_clean_exit(log_path):
        return None
    for line in reversed(tail_lines(log_path, 40)):
        if line == "EXIT=0" or not line.strip():
            continue
        for prefix in DONE_PREFIXES:
            if prefix in line:
                marker = line[line.find(prefix) :].strip()
                if "<" in marker and ">" in marker:
                    continue
                return marker
    return None


def completed_marker_actions(repo_root: Path) -> list[dict[str, Any]]:
    logs_dir = repo_root / ".refactor-loop" / "logs"
    if not logs_dir.exists():
        return []
    actions: list[dict[str, Any]] = []
    for log_path in sorted(logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True):
        marker = marker_from_completed_log(log_path)
        if not marker:
            continue
        if marker.startswith("AUDIT_DONE:none:0"):
            continue
        action = {
            "priority": 3,
            "kind": "completed-marker",
            "item": infer_item_from_text(f"{log_path.name} {marker}"),
            "phase": phase_from_marker(marker),
            "actor": actor_from_marker(marker),
            "marker": marker,
            "evidence": str(log_path.relative_to(repo_root)),
        }
        route = route_from_marker(marker)
        if route:
            action["route"] = route
        actions.append(action)
    return actions


def infer_item_from_text(text: str) -> str | None:
    pr = re.search(r"\bpr[-_#]?(\d+)\b|PR #(\d+)", text, flags=re.IGNORECASE)
    if pr:
        return f"PR #{next(group for group in pr.groups() if group)}"
    issue = re.search(r"\bissue[-_#]?(\d+)\b|#(\d+)", text, flags=re.IGNORECASE)
    if issue:
        return f"issue #{next(group for group in issue.groups() if group)}"
    return None


def phase_from_marker(marker: str) -> str:
    if marker.startswith("IMPLEMENT_DONE"):
        return "publish"
    if marker.startswith("REVIEW_DONE"):
        return "review-gate"
    if marker.startswith("FIX_DONE"):
        return "review-gate"
    if marker.startswith("TEST_ADD_DONE"):
        return "ci-watch"
    if marker.startswith("AUDIT_DONE"):
        return "work-intake"
    if marker.startswith(("SOLVER_DONE", "META_JUDGE_DONE", "META_RESOLVED")):
        return "design-consensus"
    if marker.startswith("VERIFY_DONE"):
        return "publish"
    return "work-intake"


def route_from_marker(marker: str) -> str | None:
    if marker.startswith("IMPLEMENT_DONE"):
        return "publish-or-review-gate"
    if not marker.startswith(
        (
            "AUDIT_DONE",
            "SOLVER_DONE",
            "META_JUDGE_DONE",
            "META_RESOLVED",
            "IMPLEMENT_DONE",
            "VERIFY_DONE",
            "REVIEW_DONE",
            "FIX_DONE",
            "TEST_ADD_DONE",
        )
    ):
        return "marker-route"
    return None


def actor_from_marker(marker: str) -> str:
    if marker.startswith("IMPLEMENT_DONE"):
        return "controller"
    if marker.startswith("REVIEW_DONE"):
        return "controller-or-fix-codex"
    if marker.startswith("FIX_DONE"):
        return "reviewer-codex"
    if marker.startswith("TEST_ADD_DONE"):
        return "controller"
    if marker.startswith("AUDIT_DONE"):
        return "controller"
    if marker.startswith(("SOLVER_DONE", "META_JUDGE_DONE", "META_RESOLVED")):
        return "design-consensus-router-or-controller"
    if marker.startswith("VERIFY_DONE"):
        return "controller"
    return "controller"


def pending_bootstrap_actions(repo_root: Path, health: dict[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    host_env = repo_root / ".refactor-loop" / "host.env"
    if not host_env.exists():
        actions.append(
            {
                "priority": 1,
                "kind": "bootstrap",
                "item": None,
                "phase": "bootstrap",
                "actor": "controller",
                "reason": "missing .refactor-loop/host.env",
            }
        )
    if health["recommendation"]:
        stale_names = [item["name"] for item in health["items"] if item["status"] in {"stale", "missing"}]
        actions.append(
            {
                "priority": 1,
                "kind": "bootstrap",
                "item": None,
                "phase": "bootstrap",
                "actor": "controller",
                "reason": "daemon heartbeat stale-or-missing",
                "route": "daemon-health",
                "suggested_command": "python3 <skill-root>/scripts/consensus-rnd-cli restart-daemons",
                "daemons": stale_names,
            }
        )
    pending_events = repo_root / ".refactor-loop" / ".controller-pending-events.log"
    concurrency_alert = repo_root / ".refactor-loop" / ".concurrency-alert.log"
    if not pending_events.exists() and not concurrency_alert.exists():
        actions.append(
            {
                "priority": 1,
                "kind": "wake-source",
                "item": None,
                "phase": "bootstrap",
                "actor": "controller",
                "route": "wake-source",
                "reason": "missing daemon-event surfaces; confirm Monitor bridge",
            }
        )
    return actions


def maintainer_comment_actions(repo_root: Path, gh_items: list[GhItem]) -> list[dict[str, Any]]:
    pending_path = repo_root / ".refactor-loop" / ".controller-pending-events.log"
    actions: list[dict[str, Any]] = []
    if pending_path.exists():
        try:
            lines = pending_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            lines = []
        for line in lines[-20:]:
            lowered = line.lower()
            if "maintainer" in lowered and "comment" in lowered:
                actions.append(
                    {
                        "priority": 2,
                        "kind": "maintainer-comment",
                        "item": infer_item_from_text(line),
                        "phase": "design-intake",
                        "actor": "controller",
                        "evidence": line,
                    }
                )
    for item in gh_items:
        labels = set(item.labels)
        if label_catalog.HUMAN_MAINTAINER_DECISION in label_catalog.normalize_label_set(labels).canonical:
            actions.append(
                {
                    "priority": 2,
                    "kind": "maintainer-comment",
                    "item": item.item,
                    "phase": phase_from_labels(item.labels),
                    "actor": "controller",
                    "evidence": "human label present; sweep latest non-AI comments",
                }
            )
    return actions


def no_gap_actions(repo_root: Path) -> list[dict[str, Any]]:
    alert_path = repo_root / ".refactor-loop" / ".concurrency-alert.log"
    if not alert_path.exists():
        return []
    try:
        lines = alert_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    actions: list[dict[str, Any]] = []
    for line in lines[-20:]:
        if "no-gap-violation" not in line:
            continue
        actions.append(
            {
                "priority": 5,
                "kind": "no-gap-violation",
                "item": infer_item_from_text(line),
                "phase": "work-intake",
                "actor": "controller",
                "route": "no-gap-repair",
                "evidence": line,
            }
        )
    return actions


def gh_args(slug: str | None) -> list[str]:
    return ["--repo", slug] if slug else []


def github_repo_slug() -> str | None:
    slug = os.environ.get("GH_REPO_SLUG")
    if slug:
        return slug
    repo = os.environ.get("GH_REPO")
    if repo and "/" in repo:
        return repo
    owner = os.environ.get("GH_OWNER")
    name = os.environ.get("GH_REPO_NAME") or repo
    if owner and name:
        return f"{owner}/{name}"
    return None


def load_github_items(repo_root: Path) -> list[GhItem]:
    # Refactor (issue-162/wakeup-open-only):
    #   Old pattern: action planning risked mixing closed or merged auto-loop
    #   records into dispatch candidates.
    #   New principle: actions are derived only from open auto-loop issues/PRs.
    slug = github_repo_slug()
    items: list[GhItem] = []
    for kind, gh_kind in (("issue", "issue"), ("PR", "pr")):
        rows: list[dict[str, Any]] = []
        json_fields = "number,title,labels,headRefName,body" if kind == "PR" else "number,title,labels"
        for query_label in label_catalog.query_labels_for(label_catalog.MANAGED):
            command = ["gh", gh_kind, "list", *gh_args(slug), "--label", query_label, "--state", "open", "--json", json_fields]
            data = run_json(command, cwd=repo_root)
            if isinstance(data, list):
                rows.extend(item for item in data if isinstance(item, dict))
        seen: set[int] = set()
        for raw in rows:
            try:
                number = int(raw["number"])
            except (KeyError, TypeError, ValueError):
                continue
            if number in seen:
                continue
            seen.add(number)
            labels = tuple(
                label.get("name", "")
                for label in raw.get("labels", [])
                if isinstance(label, dict) and label.get("name")
            )
            items.append(
                GhItem(
                    kind=kind,
                    number=number,
                    title=str(raw.get("title") or ""),
                    labels=labels,
                    head_ref=(str(raw.get("headRefName") or "") or None) if kind == "PR" else None,
                    body=str(raw.get("body") or "") if kind == "PR" else "",
                )
            )
    return items


def git_text(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def parse_worktree_branches(porcelain: str) -> dict[str, Path]:
    worktrees: dict[str, Path] = {}
    current: Path | None = None
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            current = Path(line.removeprefix("worktree "))
            continue
        if not line.startswith("branch ") or current is None:
            continue
        branch = line.removeprefix("branch refs/heads/")
        if branch and not branch.startswith("refs/"):
            worktrees[branch] = current
    return worktrees


def safe_head_ref(value: str | None) -> str | None:
    if not value or value.startswith("-"):
        return None
    if any(ch.isspace() or ord(ch) < 32 for ch in value):
        return None
    return value


def unpushed_worker_output_actions(repo_root: Path, gh_items: list[GhItem]) -> list[dict[str, Any]]:
    # Refactor (iter201/issue-201): Old pattern: wakeup_plan rendered a copyable
    # consensus-rnd-cli safe-push suggested_command, exposing public lifecycle
    # reachability. New principle: emit only a fixed controller_action fact with
    # no_lifecycle_authority; controller maps it to an internal primitive.
    prs = [item for item in gh_items if item.kind == "PR" and safe_head_ref(item.head_ref)]
    if not prs:
        return []
    fetch = git_text(["git", "-C", str(repo_root), "fetch", "origin", "--quiet"], cwd=repo_root)
    if fetch.returncode != 0:
        return []
    listed = git_text(["git", "-C", str(repo_root), "worktree", "list", "--porcelain"], cwd=repo_root)
    if listed.returncode != 0:
        return []
    worktrees = parse_worktree_branches(listed.stdout)
    actions: list[dict[str, Any]] = []
    for item in prs:
        head_ref = safe_head_ref(item.head_ref)
        if not head_ref:
            continue
        worktree = worktrees.get(head_ref)
        if worktree is None:
            continue
        local = git_text(["git", "-C", str(worktree), "rev-parse", "--verify", "HEAD"], cwd=repo_root)
        remote_ref = f"refs/remotes/origin/{head_ref}"
        remote = git_text(["git", "-C", str(worktree), "rev-parse", "--verify", remote_ref], cwd=repo_root)
        count = git_text(["git", "-C", str(worktree), "rev-list", "--count", f"{remote_ref}..HEAD"], cwd=repo_root)
        if local.returncode != 0 or remote.returncode != 0 or count.returncode != 0:
            continue
        try:
            ahead_count = int(count.stdout.strip())
        except ValueError:
            continue
        if ahead_count <= 0:
            continue
        actions.append(
            {
                "priority": 3,
                "kind": "unpushed-worker-output",
                "item": item.item,
                "phase": "publish",
                "route": "controller-push-required",
                "actor": "controller",
                "head_ref": head_ref,
                "worktree": str(worktree),
                "ahead_count": ahead_count,
                "local_head": local.stdout.strip(),
                "remote_head": remote.stdout.strip(),
                "line": f"UNPUSHED_WORKER_OUTPUT:{item.number}:{ahead_count}",
                "controller_action": "safe_push",
                "no_lifecycle_authority": True,
            }
        )
    return actions


def ci_red_actions(repo_root: Path, items: list[GhItem]) -> list[dict[str, Any]]:
    slug = github_repo_slug()
    if not slug:
        return []
    projection = PrChecksProjection(cwd=repo_root)
    actions: list[dict[str, Any]] = []
    for item in items:
        if item.kind != "PR":
            continue
        # Refactor (issue-297): Old: ci-red routed through a naked PR checks CLI.
        # New: wakeup-plan consumes the named PR-head Checks API projection.
        status = projection.check_pr(slug, item.number)
        if not status.ok:
            continue
        fail_count = sum(1 for check in status.runs if check.bucket == "fail")
        if fail_count <= 0:
            continue
        actions.append(
            {
                "priority": 4,
                "kind": "ci-red",
                "item": item.item,
                "phase": "ci-watch",
                "actor": "remote-ci-fix-codex",
                "fail_count": fail_count,
                "head_sha": status.head_sha,
                "check_names": [check.name for check in status.runs if check.bucket == "fail"],
            }
        )
    return actions


def existing_issue_actions(items: list[GhItem], repo_root: Path | None = None) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    raw_by_key = {(item.kind.lower(), item.number): item for item in items}
    actionable = open_actionable_managed_items(_projection_items(items))
    # Refactor (issue-262): Old: existing issue ranking only used milestone,
    # kind, and number. New: a checked-in caller may use the validated
    # transition_assessment sidecar bucket before the existing tie-breakers.
    ordered = sorted(actionable, key=lambda item: _existing_issue_sort_key(item, repo_root))
    for item in ordered:
        raw = raw_by_key.get((item.kind, item.number))
        title = raw.title if raw is not None else item.title
        milestone = label_catalog.MILESTONE_CURRENT in label_catalog.normalize_label_set(item.labels).canonical
        priority = 6 if milestone else 7
        item_name = f"{'PR' if item.kind == 'pr' else item.kind} #{item.number}"
        actions.append(
            {
                "priority": priority,
                "kind": "existing-issue",
                "item": item_name,
                "phase": phase_from_labels(item.labels),
                "actor": actor_from_labels(item.labels, item.kind),
                "milestone": milestone,
                "title": title,
            }
        )
    return actions


def has_dispatchable_action(actions: list[dict[str, Any]]) -> bool:
    dispatchable = {
        "maintainer-comment",
        "completed-marker",
        "ci-red",
        "no-gap-violation",
        "existing-issue",
    }
    return any(action.get("kind") in dispatchable for action in actions)


def restore_hard_gate_for_dispatchable_actions(concurrency: dict[str, Any], actions: list[dict[str, Any]]) -> None:
    hard_gate = concurrency.get("hard_gate", {})
    if hard_gate.get("reason") != "single_active_audit_in_flight":
        return
    if not has_dispatchable_action(actions):
        return
    deficit = int(concurrency.get("deficit", 0))
    hard_gate.update(
        {
            "active": deficit > 0,
            "dispatch_required": deficit if deficit > 0 else 0,
            "line": f"HARD_GATE:dispatch_required={deficit}" if deficit > 0 else None,
            "semantics": (
                "controller must dispatch this many actionable tasks or audit fallback before ending the wakeup"
                if deficit > 0
                else None
            ),
            "reason": None,
            "blocked_deficit": 0,
            "boundary_task_id": None,
        }
    )


def _existing_issue_sort_key(item: Any, repo_root: Path | None) -> tuple[bool, int, float, int, int]:
    if repo_root is None:
        transition_key = (0, -0.0)
    else:
        transition_key = transition_rank_key(
            TransitionAssessmentReader.load_for_work_unit(
                repo_root,
                work_unit_id=f"issue-{item.number}",
                source_ref=f"gh-issue-{item.number}",
            )
        )
    milestone = label_catalog.MILESTONE_CURRENT in label_catalog.normalize_label_set(item.labels).canonical
    return (not milestone, transition_key[0], transition_key[1], 0 if item.kind == "issue" else 1, item.number)


def phase_from_labels(labels: tuple[str, ...]) -> str:
    phase = label_catalog.normalize_label_set(labels).phase
    if phase:
        stage = PHASE_TO_STAGE.get(phase, "work-intake")
        assert_stage_slug(stage)
        return stage
    return "work-intake"


def status_from_labels(labels: tuple[str, ...]) -> str | None:
    projection = label_catalog.normalize_label_set(labels)
    if projection.phase in NON_ACTION_PHASE_LABELS:
        return NON_ACTION_PHASE_LABELS[projection.phase]
    if not projection.phase:
        return "unlabeled-existing-issue"
    return None


def actor_from_labels(labels: tuple[str, ...], kind: str) -> str:
    projection = label_catalog.normalize_label_set(labels)
    if label_catalog.HUMAN_MAINTAINER_DECISION in projection.canonical or label_catalog.PHASE_BLOCKED in projection.canonical:
        return "controller"
    if projection.phase:
        actor = label_catalog.actor_for_phase(projection.phase)
        if actor:
            return actor
    return "controller-triage" if kind == "issue" else "reviewer-or-controller"


def _projection_items(items: list[GhItem]) -> list[dict[str, Any]]:
    return [
        {
            "kind": item.kind,
            "number": item.number,
            "labels": item.labels,
            "body": item.body,
            "state": "open",
            "title": item.title,
        }
        for item in items
    ]


def latest_controller_validated_audit_none(repo_root: Path) -> bool:
    logs_dir = repo_root / ".refactor-loop" / "logs"
    if not logs_dir.exists():
        return False
    audit_logs = sorted(logs_dir.glob("audit*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for log_path in audit_logs:
        marker = marker_from_completed_log(log_path)
        if not marker or not marker.startswith("AUDIT_DONE"):
            continue
        return marker.startswith("AUDIT_DONE:none:0")
    return False


def build_plan(repo_root: Path) -> dict[str, Any]:
    ctx = LoopContext.load(repo_root=repo_root, env=os.environ, cwd=repo_root, read_only=True)
    os.environ.update(ctx.env_for_subprocess())

    health = daemon_health(repo_root)
    gh_items = load_github_items(repo_root)
    audit_none_fixed_point = latest_controller_validated_audit_none(repo_root)
    concurrency = concurrency_plan(repo_root, fixed_point=audit_none_fixed_point, gh_items=gh_items)

    actions: list[dict[str, Any]] = []
    actions.extend(pending_bootstrap_actions(repo_root, health))
    actions.extend(maintainer_comment_actions(repo_root, gh_items))
    actions.extend(unpushed_worker_output_actions(repo_root, gh_items))
    actions.extend(completed_marker_actions(repo_root))
    actions.extend(ci_red_actions(repo_root, gh_items))
    actions.extend(no_gap_actions(repo_root))
    host_actions, host_spec_error = load_host_workflow_projection(repo_root)
    if host_spec_error:
        actions.append(
            {
                "priority": 2,
                "kind": "host-workflow-spec-invalid",
                "item": None,
                "phase": "bootstrap",
                "actor": "controller",
                "route": "host-workflow-spec",
                "reason": host_spec_error,
                "no_lifecycle_authority": True,
            }
        )
    else:
        actions.extend(host_actions)
    actions.extend(existing_issue_actions(gh_items, repo_root))
    actions.sort(key=lambda action: action["priority"])
    restore_hard_gate_for_dispatchable_actions(concurrency, actions)

    recommendation: str | None = None
    if not actions:
        if concurrency["hard_gate"].get("reason") == "single_active_audit_in_flight":
            recommendation = "WAIT:single-active-audit"
        else:
            recommendation = "RECOMMEND:audit"

    return {
        "schema": "wakeup-plan",
        "repo_root": str(repo_root),
        "authorization": "skills/codex-refactor-loop/authorizations/runtime-exceptions.md#maintainer-directive-wakeup-plan-script",
        "mode": "read-only-recommendation",
        "no_lifecycle_authority": True,
        "daemon_health": health,
        "concurrency": concurrency,
        "hard_gate": concurrency["hard_gate"],
        "actions": actions,
        "recommendation": recommendation,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit a read-only prioritized wakeup plan as JSON.")
    parser.add_argument("--repo-root", help="Host repository root. Defaults to REPO_ROOT or cwd.")
    args = parser.parse_args(argv)
    repo_root = resolve_repo_root(args.repo_root)
    plan = build_plan(repo_root)
    print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
    hard_gate_line = plan["hard_gate"].get("line")
    if hard_gate_line:
        print(hard_gate_line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
