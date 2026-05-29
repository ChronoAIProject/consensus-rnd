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
`.refactor-loop/runs/maintainer-directives/2026-05-29-wakeup-plan-script.md`.
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

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.workflow_stages import assert_stage_slug


STALE_SECONDS = 90
EXPECTED_DAEMONS = (
    "concurrency_monitor",
    "comment-monitor",
    "codex-progress-reporter",
    "dev_sync_daemon",
    "phase9_router_daemon",
)
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
PHASE_ACTORS = (
    ("🔍 phase:design-solving", "design-consensus-solver-or-judge"),
    ("🛠️ phase:implementing", "implement-codex"),
    ("🔧 phase:fixing", "fix-codex"),
    ("👀 phase:reviewing", "reviewer-codex"),
    ("⚙️ phase:ci-running", "controller-ci-watch"),
    ("🚀 phase:pr-open", "reviewer-codex"),
    ("✅ phase:consensus-reached", "implement-codex"),
)
PHASE_EXPECTED = {
    "🔍 phase:design-solving": 1,
    "🔧 phase:fixing": 1,
    "👀 phase:reviewing": 1,
    "🛠️ phase:implementing": 1,
    "⚙️ phase:ci-running": 0,
    "🚀 phase:pr-open": 0,
    "✅ phase:consensus-reached": 0,
    "🎉 phase:merged": 0,
    "⏸️ phase:blocked": 0,
}
NON_ACTION_PHASE_LABELS = {
    "⚙️ phase:ci-running": "ci-running",
    "⏸️ phase:blocked": "blocked",
    "🎉 phase:merged": "merged",
}


@dataclass(frozen=True)
class GhItem:
    kind: str
    number: int
    title: str
    labels: tuple[str, ...]
    head_ref: str | None = None

    @property
    def item(self) -> str:
        return f"{self.kind} #{self.number}"

    @property
    def milestone(self) -> bool:
        return "🎯 milestone" in self.labels


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


def read_host_env(repo_root: Path) -> dict[str, str]:
    env_path = repo_root / ".refactor-loop" / "host.env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def configured_floor() -> int:
    try:
        floor = int(os.environ.get("CODEX_FLOOR", "5"))
    except ValueError:
        floor = 5
    return max(2, floor)


def resolve_repo_root(arg_root: str | None) -> Path:
    if arg_root:
        return Path(arg_root).resolve()
    env_root = os.environ.get("REPO_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path.cwd().resolve()


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
    for item in items:
        labels = set(item.labels)
        if "👤 human:需-maintainer-决策" in labels:
            continue
        phase_label = next((label for label in item.labels if label in PHASE_EXPECTED), "")
        expected = PHASE_EXPECTED.get(phase_label, 0)
        if expected <= 0:
            continue
        breakdown.append({"id": f"#{item.number}", "kind": item.kind.lower(), "phase": phase_label, "expected": expected})
        total += expected
    return total, breakdown


def concurrency_plan(repo_root: Path, *, fixed_point: bool, gh_items: list[GhItem] | None = None) -> dict[str, Any]:
    # Refactor (floor-no-exemption):
    #   Old pattern: AUDIT_DONE:none:0 converted a positive deficit into
    #   a low-floor stop and suppressed the hard gate.
    #   New principle: deficit>0 has no exemption; audit is the fallback work
    #   when no open existing action is available.
    monitor = build_concurrency_monitor(repo_root, import_concurrency_monitor(repo_root))
    actual = canonical_actual_count(repo_root, monitor)
    expected, breakdown = expected_from_open_items(gh_items or [])
    if expected == 0:
        expected, breakdown = canonical_expected_from_active_tasks(monitor)
    floor = configured_floor()
    target = max(floor, expected)
    deficit = max(0, target - actual)
    hard_gate_active = deficit > 0
    hard_gate_line = f"HARD_GATE:dispatch_required={deficit}" if hard_gate_active else None
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
            "reason": None,
        },
    }


def daemon_health(repo_root: Path, now: float | None = None) -> dict[str, Any]:
    # Refactor (issue-162/wakeup-heartbeats-only):
    #   Old pattern: daemon health could drift toward matching solver or log text.
    #   New principle: health reads only actor-owned .refactor-loop/heartbeats/*.ts.
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
    for name in EXPECTED_DAEMONS:
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
        if "👤 human:需-maintainer-决策" in labels:
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
    for kind, command in (
        ("issue", ["gh", "issue", "list", *gh_args(slug), "--label", "auto-loop", "--state", "open", "--json", "number,title,labels"]),
        ("PR", ["gh", "pr", "list", *gh_args(slug), "--label", "auto-loop", "--state", "open", "--json", "number,title,labels,headRefName"]),
    ):
        data = run_json(command, cwd=repo_root)
        if not isinstance(data, list):
            continue
        for raw in data:
            if not isinstance(raw, dict):
                continue
            try:
                number = int(raw["number"])
            except (KeyError, TypeError, ValueError):
                continue
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
    actions: list[dict[str, Any]] = []
    for item in items:
        if item.kind != "PR":
            continue
        checks = run_json(["gh", "pr", "checks", str(item.number), *gh_args(slug), "--json", "bucket"], cwd=repo_root)
        if not isinstance(checks, list):
            continue
        fail_count = sum(1 for check in checks if isinstance(check, dict) and check.get("bucket") == "fail")
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
            }
        )
    return actions


def existing_issue_actions(items: list[GhItem]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    ordered = sorted(items, key=lambda item: (not item.milestone, 0 if item.kind == "issue" else 1, item.number))
    for item in ordered:
        phase = phase_from_labels(item.labels)
        status = status_from_labels(item.labels)
        if status in {"blocked", "merged", "ci-running"}:
            continue
        priority = 6 if item.milestone else 7
        actions.append(
            {
                "priority": priority,
                "kind": "existing-issue",
                "item": item.item,
                "phase": phase,
                "actor": actor_from_labels(item.labels, item.kind),
                "milestone": item.milestone,
                "title": item.title,
            }
        )
    return actions


def phase_from_labels(labels: tuple[str, ...]) -> str:
    for label, phase in (
        ("🔍 phase:design-solving", "design-consensus"),
        ("🛠️ phase:implementing", "implementation"),
        ("🔧 phase:fixing", "review-gate"),
        ("👀 phase:reviewing", "review-gate"),
        ("⚙️ phase:ci-running", "ci-watch"),
        ("🚀 phase:pr-open", "review-gate"),
        ("✅ phase:consensus-reached", "implementation"),
        ("⏸️ phase:blocked", "bootstrap"),
        ("🎉 phase:merged", "publish"),
    ):
        if label in labels:
            assert_stage_slug(phase)
            return phase
    return "work-intake"


def status_from_labels(labels: tuple[str, ...]) -> str | None:
    for label, status in NON_ACTION_PHASE_LABELS.items():
        if label in labels:
            return status
    if not any(label.startswith("phase:") or " phase:" in label for label in labels):
        return "unlabeled-existing-issue"
    return None


def actor_from_labels(labels: tuple[str, ...], kind: str) -> str:
    if "👤 human:需-maintainer-决策" in labels or "⏸️ phase:blocked" in labels:
        return "controller"
    for label, actor in PHASE_ACTORS:
        if label in labels:
            return actor
    return "controller-triage" if kind == "issue" else "reviewer-or-controller"


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
    host_values = read_host_env(repo_root)
    for key, value in host_values.items():
        os.environ.setdefault(key, value)
    os.environ["REPO_ROOT"] = str(repo_root)

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
    actions.extend(existing_issue_actions(gh_items))
    actions.sort(key=lambda action: action["priority"])

    recommendation: str | None = None
    if not actions:
        recommendation = "RECOMMEND:audit"

    return {
        "schema": "wakeup-plan",
        "repo_root": str(repo_root),
        "authorization": ".refactor-loop/runs/maintainer-directives/2026-05-29-wakeup-plan-script.md",
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
