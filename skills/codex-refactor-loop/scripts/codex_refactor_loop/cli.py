"""Controller-facing command router for codex-refactor-loop."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from . import github_body, holistic_status, patrol, project_rules, spawn, statusline
from .closed_label_reconciler import main as closed_label_reconciler_main
from .checks.degradation import main as degradation_main
from .checks.manifest import main as manifest_main
from .daemon_status import main as daemon_status_main
from .gh_accounting import activate_controller_accounting
from .gh_accounting import main as gh_stats_main
from .labels import main as labels_main
from .monitors.comment import main as comment_monitor_main
from .monitors.concurrency import main as concurrency_main
from .monitors.progress import main as progress_reporter_main
from .peek import main as peek_main
from .pr_checks import main as pr_checks_main
from .release.gate import main as release_gate_main
from .release.required_checks import main as release_required_checks_main
from .restart import main as restart_main
from .runtime_retention import main as runtime_retention_main
from .sync.dev import main as dev_sync_main
from .phase9.router import main as phase9_router_main
from .update_check import main as update_check_main
from .wakeup_plan import main as wakeup_plan_main
from .wakeup_plan import revive_implements_main
from .wakeup_runner import main as wakeup_runner_main


SCRIPT_DIR = Path(__file__).resolve().parents[1]


def release_commits_command(argv: Sequence[str] | None) -> int:
    from .release.commits import main as release_commits_main

    return release_commits_main(argv)


@dataclass(frozen=True)
class CommandSpec:
    handler: Callable[[Sequence[str] | None], int]
    description: str
    authority: tuple[str, ...]


COMMANDS: dict[str, CommandSpec] = {
    "spawn-codex": CommandSpec(spawn.main, "run the Python codex spawn supervisor", ("spawn", "write-log")),
    "peek": CommandSpec(peek_main, "run the Python read-only state sweep", ("read-state", "read-gh")),
    "holistic-status": CommandSpec(
        holistic_status.main,
        "render the shared read-only holistic status card",
        ("read-state", "read-process", "read-gh"),
    ),
    "gh-stats": CommandSpec(gh_stats_main, "read local gh usage accounting", ("read-state",)),
    "wakeup-plan": CommandSpec(wakeup_plan_main, "emit the read-only prioritized wakeup plan", ("read-state", "read-gh")),
    "revive-implements": CommandSpec(revive_implements_main, "manual trigger: re-trigger stuck implement workers now (no age wait)", ("delete-log",)),
    "wakeup-runner": CommandSpec(
        wakeup_runner_main,
        "apply the #396 wakeup-plan closed action projection",
        (
            "read-state",
            "read-log",
            "read-gh",
            "read-git",
            "write-artifact",
            "write-event",
            "spawn",
            "git-commit-worker-output",
            "git-push",
            "gh-open",
            "gh-merge",
            "gh-close-linked",
            "gh-label-owned",
            "controller-lifecycle-runner",
        ),
    ),
    "pr-checks": CommandSpec(
        pr_checks_main,
        "read PR-head check-runs through the narrow Checks API projection",
        ("read-gh",),
    ),
    "restart-daemons": CommandSpec(
        restart_main,
        "run the Python daemon restart helper",
        ("spawn-daemon", "write-state", "delete-runtime"),
    ),
    "daemon-status": CommandSpec(
        daemon_status_main,
        "read restart-helper-managed daemon status",
        ("read-state", "read-process"),
    ),
    "statusline": CommandSpec(statusline.main, "read the Python statusline snapshot", ("read-state", "read-git")),
    "comment-monitor": CommandSpec(
        comment_monitor_main,
        "run the Python comment monitor daemon",
        ("read-gh", "gh-reaction", "gh-comment", "write-state", "write-event"),
    ),
    "closed-label-reconciler": CommandSpec(
        closed_label_reconciler_main,
        "run the closed managed item phase-label reconciler",
        ("read-gh", "gh-label-closed-reconcile", "write-state"),
    ),
    "patrol-inspector": CommandSpec(
        patrol.main,
        "run the #541 patrol-inspector issue intake daemon",
        ("read-state", "read-log", "read-gh", "gh-open", "gh-edit", "write-state"),
    ),
    "concurrency": CommandSpec(
        concurrency_main,
        "run the Python concurrency monitor or read-only counter",
        ("read-process", "read-gh", "write-state", "write-event", "write-artifact"),
    ),
    "progress-reporter": CommandSpec(
        progress_reporter_main,
        "run the Python progress reporter daemon",
        ("read-gh", "gh-comment", "write-state"),
    ),
    "dev-sync": CommandSpec(
        dev_sync_main,
        "run the Python integration sync daemon",
        (
            "read-git",
            "read-gh",
            "git-fetch",
            "git-worktree",
            "git-merge",
            "git-push",
            "git-rebase",
            "git-reset",
            "write-event",
            "write-artifact",
            "spawn",
        ),
    ),
    "phase9-router": CommandSpec(
        phase9_router_main,
        "compatibility alias for the Python design-consensus router",
        ("read-log", "read-gh", "write-event", "write-artifact"),
    ),
    "release-gate": CommandSpec(
        release_gate_main,
        "run the Python auto release gate",
        ("read-state", "read-gh", "write-artifact"),
    ),
    "release-commits": CommandSpec(
        release_commits_command,
        "write the git-derived release commits projection",
        ("read-git", "write-artifact"),
    ),
    "release-required-checks": CommandSpec(
        release_required_checks_main,
        "check exact release required check-runs",
        ("read-gh",),
    ),
    "update-check": CommandSpec(
        update_check_main,
        "run the notify-only version update check probe",
        ("read-source", "read-gh", "write-state"),
    ),
    "render-github-body": CommandSpec(
        github_body.main,
        "render a self-contained GitHub body from local artifacts",
        ("read-artifact",),
    ),
    "labels": CommandSpec(
        labels_main,
        "validate or plan loop-owned GitHub labels",
        ("read-source", "read-gh"),
    ),
    "check-degradation": CommandSpec(
        degradation_main,
        "run the static skill degradation check",
        ("read-source", "read-state"),
    ),
    "check-manifest": CommandSpec(manifest_main, "run manifest version sync check", ("read-source",)),
    "runtime-retention": CommandSpec(
        runtime_retention_main,
        "run canonical RuntimeRetention for skill-private generated artifacts",
        ("delete-runtime", "git-worktree"),
    ),
    "check-project-rules": CommandSpec(
        project_rules.main,
        "check host project rules fixed points and write patch artifact when needed",
        ("read-source", "write-artifact"),
    ),
}


class RuntimeCommandRouter:
    """Stable command-name router for Python runtime modules."""

    def __init__(self, script_dir: Path = SCRIPT_DIR) -> None:
        self.script_dir = script_dir

    def main(self, argv: Sequence[str] | None = None) -> int:
        parser = argparse.ArgumentParser(
            prog="consensus-rnd-cli",
            description="codex-refactor-loop controller command router",
        )
        parser.add_argument("command", nargs="?")
        parser.add_argument("args", nargs=argparse.REMAINDER)
        args = parser.parse_args(argv)
        if not args.command:
            parser.print_help()
            return 0
        return self.run(args.command, list(args.args))

    def run(self, command: str, args: Sequence[str]) -> int:
        activate_controller_accounting(skill_root=self.script_dir.parent)
        spec = COMMANDS.get(command)
        if spec is None:
            sys.stderr.write(f"unknown command: {command}\n")
            return 2
        return spec.handler(args)


def main(argv: Sequence[str] | None = None) -> int:
    return RuntimeCommandRouter().main(argv)
