"""Controller-facing command router for codex-refactor-loop."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from . import banners, github_body, project_rules, spawn, statusline
from .controller_actions import main as controller_actions_main
from .checks.degradation import main as degradation_main
from .checks.manifest import main as manifest_main
from .monitors.comment import main as comment_monitor_main
from .monitors.concurrency import main as concurrency_main
from .monitors.progress import main as progress_reporter_main
from .peek import main as peek_main
from .release.gate import main as release_gate_main
from .release.required_checks import main as release_required_checks_main
from .restart import main as restart_main
from .retention import main as retention_main
from .sync.apply import main as sync_apply_main
from .sync.dev import main as dev_sync_main
from .sync.requests import main as sync_requests_main
from .phase9.router import main as phase9_router_main
from .triage import main as triage_main
from .wakeup_plan import main as wakeup_plan_main


SCRIPT_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class CommandSpec:
    handler: Callable[[Sequence[str] | None], int]
    description: str
    authority: tuple[str, ...]


# Refactor (iter1/issue-166): Old pattern: CommandSpec exposed a coarse
# read_only boolean, so daemon/controller git, GitHub, spawn, and artifact
# surfaces were inferred from prose and could drift from runtime. New principle:
# COMMANDS keeps one inline closed-token authority tuple per command as the
# mechanical CLI authority fact source; named runtime exceptions, such as
# dev-sync's integration-worktree carveout, must be explicit here and in tests.
COMMANDS: dict[str, CommandSpec] = {
    "spawn-codex": CommandSpec(spawn.main, "run the Python codex spawn supervisor", ("spawn", "write-log")),
    "peek": CommandSpec(peek_main, "run the Python read-only state sweep", ("read-state", "read-gh")),
    "wakeup-plan": CommandSpec(wakeup_plan_main, "emit the read-only prioritized wakeup plan", ("read-state", "read-gh")),
    "restart-daemons": CommandSpec(
        restart_main,
        "run the Python daemon restart helper",
        ("spawn-daemon", "write-state", "delete-log"),
    ),
    "statusline": CommandSpec(statusline.main, "read the Python statusline snapshot", ("read-state", "read-git")),
    "comment-monitor": CommandSpec(
        comment_monitor_main,
        "run the Python comment monitor daemon",
        ("read-gh", "gh-reaction", "gh-comment", "write-state", "write-event"),
    ),
    "concurrency": CommandSpec(
        concurrency_main,
        "run the Python concurrency monitor or read-only counter",
        ("read-process", "read-gh", "write-state", "write-event", "spawn", "write-artifact"),
    ),
    "progress-reporter": CommandSpec(
        progress_reporter_main,
        "run the Python progress reporter daemon",
        ("read-gh", "gh-comment", "write-state"),
    ),
    "dev-sync": CommandSpec(
        dev_sync_main,
        "run the Python integration sync daemon",
        ("read-git", "read-gh", "git-fetch", "git-worktree", "write-event", "write-artifact", "spawn"),
    ),
    "phase9-router": CommandSpec(
        phase9_router_main,
        "run the Python Phase 9 router",
        ("read-log", "write-event", "write-artifact", "spawn"),
    ),
    "release-gate": CommandSpec(
        release_gate_main,
        "run the Python auto release gate",
        ("read-state", "read-gh", "write-artifact"),
    ),
    "release-required-checks": CommandSpec(
        release_required_checks_main,
        "check exact release required check-runs",
        ("read-gh",),
    ),
    "render-github-body": CommandSpec(
        github_body.main,
        "render a self-contained GitHub body from local artifacts",
        ("read-artifact",),
    ),
    "merge-pr": CommandSpec(
        controller_actions_main,
        "invoke Python controller action merge_pr",
        ("read-gh", "gh-merge", "gh-label", "gh-close", "git-worktree", "write-state"),
    ),
    "open-pr": CommandSpec(
        controller_actions_main,
        "invoke Python controller action open_pr_with_label",
        ("gh-open", "gh-label"),
    ),
    "open-release-rollup-pr": CommandSpec(
        controller_actions_main,
        "open release rollup PR from throwaway head branch",
        ("read-git", "git-push", "gh-open", "gh-label"),
    ),
    "apply-human-label": CommandSpec(
        controller_actions_main,
        "apply maintainer-decision label after guard checks",
        ("read-state", "gh-label"),
    ),
    "safe-push": CommandSpec(
        controller_actions_main,
        "push after bounded fetch/rebase catch-up",
        ("git-fetch", "git-rebase", "git-push"),
    ),
    "safe-sync-main": CommandSpec(
        controller_actions_main,
        "catch current branch up to the remote tip",
        ("git-fetch", "git-rebase"),
    ),
    "post-banner": CommandSpec(banners.main, "post a controller status banner", ("gh-comment",)),
    "check-degradation": CommandSpec(
        degradation_main,
        "run the static skill degradation check",
        ("read-source", "read-state"),
    ),
    "check-manifest": CommandSpec(manifest_main, "run manifest version sync check", ("read-source",)),
    "apply-sync": CommandSpec(
        sync_apply_main,
        "apply an IntegrationSyncRequest artifact",
        ("read-artifact", "write-artifact", "git-fetch", "git-merge", "git-rebase", "git-reset", "git-push"),
    ),
    "apply-triage": CommandSpec(
        triage_main,
        "apply a ManualIssueTriageDecision artifact",
        ("read-artifact", "write-artifact", "read-gh", "gh-comment", "gh-label", "gh-edit"),
    ),
    "log-retention": CommandSpec(retention_main, "run daemonless log retention", ("delete-log",)),
    "ensure-project-rules": CommandSpec(project_rules.main, "ensure host project rules fixed points", ("write-source",)),
    "sync-request": CommandSpec(sync_requests_main, "validate IntegrationSyncRequest artifacts", ("read-artifact",)),
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
        spec = COMMANDS.get(command)
        if spec is None:
            sys.stderr.write(f"unknown command: {command}\n")
            return 2
        if command in {"merge-pr", "open-pr", "open-release-rollup-pr", "apply-human-label", "safe-push", "safe-sync-main"}:
            return spec.handler([command, *args])
        return spec.handler(args)


def main(argv: Sequence[str] | None = None) -> int:
    return RuntimeCommandRouter().main(argv)
