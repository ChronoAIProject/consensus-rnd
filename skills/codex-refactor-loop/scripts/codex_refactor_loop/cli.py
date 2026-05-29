"""Controller-facing command router for codex-refactor-loop."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from . import banners, project_rules, spawn, statusline
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
    read_only: bool = False


COMMANDS: dict[str, CommandSpec] = {
    "spawn-codex": CommandSpec(spawn.main, "run the Python codex spawn supervisor"),
    "peek": CommandSpec(peek_main, "run the Python read-only state sweep", read_only=True),
    "wakeup-plan": CommandSpec(wakeup_plan_main, "emit the read-only prioritized wakeup plan", read_only=True),
    "restart-daemons": CommandSpec(restart_main, "run the Python daemon restart helper"),
    "statusline": CommandSpec(statusline.main, "read the Python statusline snapshot", read_only=True),
    "comment-monitor": CommandSpec(comment_monitor_main, "run the Python comment monitor daemon"),
    "concurrency": CommandSpec(concurrency_main, "run the Python concurrency monitor or read-only counter"),
    "progress-reporter": CommandSpec(progress_reporter_main, "run the Python progress reporter daemon"),
    "dev-sync": CommandSpec(dev_sync_main, "run the Python integration sync daemon"),
    "phase9-router": CommandSpec(phase9_router_main, "run the Python Phase 9 router"),
    "release-gate": CommandSpec(release_gate_main, "run the Python auto release gate"),
    "release-required-checks": CommandSpec(release_required_checks_main, "check exact release required check-runs", read_only=True),
    "merge-pr": CommandSpec(controller_actions_main, "invoke Python controller action merge_pr"),
    "open-pr": CommandSpec(controller_actions_main, "invoke Python controller action open_pr_with_label"),
    "apply-human-label": CommandSpec(controller_actions_main, "apply maintainer-decision label after guard checks"),
    "safe-push": CommandSpec(controller_actions_main, "push after bounded fetch/rebase catch-up"),
    "safe-sync-main": CommandSpec(controller_actions_main, "catch current branch up to the remote tip"),
    "post-banner": CommandSpec(banners.main, "post a controller status banner"),
    "check-degradation": CommandSpec(degradation_main, "run the static skill degradation check", read_only=True),
    "check-manifest": CommandSpec(manifest_main, "run manifest version sync check", read_only=True),
    "apply-sync": CommandSpec(sync_apply_main, "apply an IntegrationSyncRequest artifact"),
    "apply-triage": CommandSpec(triage_main, "apply a ManualIssueTriageDecision artifact"),
    "log-retention": CommandSpec(retention_main, "run daemonless log retention"),
    "ensure-project-rules": CommandSpec(project_rules.main, "ensure host project rules fixed points"),
    "sync-request": CommandSpec(sync_requests_main, "validate or emit IntegrationSyncRequest artifacts"),
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
        if command in {"merge-pr", "open-pr", "apply-human-label", "safe-push", "safe-sync-main"}:
            return spec.handler([command, *args])
        return spec.handler(args)


def main(argv: Sequence[str] | None = None) -> int:
    return RuntimeCommandRouter().main(argv)
