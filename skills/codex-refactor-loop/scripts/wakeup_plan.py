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
GitHub list/check/view commands, and print JSON recommendations.
Forbidden: no restart/spawn, no git, no GitHub lifecycle mutation, no commit,
push, merge, label, issue/PR create/close/edit, tag, release, or worker
dispatch. Authorization source:
`.refactor-loop/runs/maintainer-directives/2026-05-29-wakeup-plan-script.md`.
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

from repo_config import github_repo_slug


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
    ("🔍 phase:design-solving", "phase9-solver-or-judge"),
    ("🛠️ phase:implementing", "implement-codex"),
    ("🔧 phase:fixing", "fix-codex"),
    ("👀 phase:reviewing", "reviewer-codex"),
    ("⚙️ phase:ci-running", "controller-ci-watch"),
    ("🚀 phase:pr-open", "reviewer-codex"),
    ("✅ phase:consensus-reached", "implement-codex"),
)


@dataclass(frozen=True)
class GhItem:
    kind: str
    number: int
    title: str
    labels: tuple[str, ...]

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
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    try:
        if "concurrency_monitor" in sys.modules:
            return importlib.reload(sys.modules["concurrency_monitor"])
        return importlib.import_module("concurrency_monitor")
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
    script_path = Path(__file__).resolve().parent / "concurrency_monitor.py"
    result = subprocess.run(
        [sys.executable, str(script_path), "--count-only"],
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


def concurrency_plan(repo_root: Path, *, fixed_point: bool) -> dict[str, Any]:
    monitor = import_concurrency_monitor(repo_root)
    actual = canonical_actual_count(repo_root, monitor)
    expected, breakdown = canonical_expected_from_active_tasks(monitor)
    floor = configured_floor()
    target = max(floor, expected)
    deficit = max(0, target - actual)
    fixed_point_low = fixed_point and deficit > 0
    hard_gate_active = deficit > 0 and not fixed_point_low
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
                "controller must dispatch this many actionable tasks before ending the wakeup"
                if hard_gate_active
                else None
            ),
            "reason": "CONCURRENCY_LOW:no-work-after-audit-none" if fixed_point_low else None,
        },
    }


def daemon_health(repo_root: Path, now: float | None = None) -> dict[str, Any]:
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
        "recommendation": "restart-daemons.sh" if needs_restart else None,
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
        actions.append(
            {
                "priority": 3,
                "kind": "completed-marker",
                "item": infer_item_from_text(f"{log_path.name} {marker}"),
                "phase": phase_from_marker(marker),
                "actor": actor_from_marker(marker),
                "marker": marker,
                "evidence": str(log_path.relative_to(repo_root)),
            }
        )
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
        return "phase-4-open-pr-or-phase-8-review"
    if marker.startswith("REVIEW_DONE"):
        return "phase-8-review-gate"
    if marker.startswith("FIX_DONE"):
        return "phase-8-rereview"
    if marker.startswith("TEST_ADD_DONE"):
        return "phase-5-ci-watch"
    if marker.startswith("AUDIT_DONE"):
        return "phase-1-audit-result"
    if marker.startswith(("SOLVER_DONE", "META_JUDGE_DONE", "META_RESOLVED")):
        return "phase-9-consensus-route"
    if marker.startswith("VERIFY_DONE"):
        return "phase-4-controller-lifecycle"
    return "marker-route"


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
        return "phase9-router-or-controller"
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
                "phase": "phase-0-bootstrap",
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
                "phase": "daemon-health",
                "actor": "controller",
                "reason": "daemon heartbeat stale-or-missing",
                "suggested_command": "bash <skill-root>/scripts/restart-daemons.sh",
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
                "phase": "wake-source",
                "actor": "controller",
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
                        "phase": "phase-7-comment-intake",
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
                "phase": "no-gap-repair",
                "actor": "controller",
                "evidence": line,
            }
        )
    return actions


def gh_args(slug: str | None) -> list[str]:
    return ["--repo", slug] if slug else []


def load_github_items(repo_root: Path) -> list[GhItem]:
    slug = github_repo_slug()
    items: list[GhItem] = []
    for kind, command in (
        ("issue", ["gh", "issue", "list", *gh_args(slug), "--label", "auto-loop", "--state", "open", "--json", "number,title,labels"]),
        ("PR", ["gh", "pr", "list", *gh_args(slug), "--label", "auto-loop", "--state", "open", "--json", "number,title,labels"]),
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
            items.append(GhItem(kind=kind, number=number, title=str(raw.get("title") or ""), labels=labels))
    return items


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
                "phase": "phase-5-ci-red",
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
        if phase in {"blocked", "merged", "ci-running"}:
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
        ("🔍 phase:design-solving", "phase-9-design-solving"),
        ("🛠️ phase:implementing", "phase-2-implementing"),
        ("🔧 phase:fixing", "phase-8-fixing"),
        ("👀 phase:reviewing", "phase-8-reviewing"),
        ("⚙️ phase:ci-running", "ci-running"),
        ("🚀 phase:pr-open", "phase-8-reviewing"),
        ("✅ phase:consensus-reached", "phase-2-implementing"),
        ("⏸️ phase:blocked", "blocked"),
        ("🎉 phase:merged", "merged"),
    ):
        if label in labels:
            return phase
    return "unlabeled-existing-issue"


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
    concurrency = concurrency_plan(repo_root, fixed_point=audit_none_fixed_point)

    actions: list[dict[str, Any]] = []
    actions.extend(pending_bootstrap_actions(repo_root, health))
    actions.extend(maintainer_comment_actions(repo_root, gh_items))
    actions.extend(completed_marker_actions(repo_root))
    actions.extend(ci_red_actions(repo_root, gh_items))
    actions.extend(no_gap_actions(repo_root))
    actions.extend(existing_issue_actions(gh_items))
    actions.sort(key=lambda action: action["priority"])

    recommendation: str | None = None
    if not actions:
        if audit_none_fixed_point:
            recommendation = "CONCURRENCY_LOW:no-work-after-audit-none"
        else:
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
