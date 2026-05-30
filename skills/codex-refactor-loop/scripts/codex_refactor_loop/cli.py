"""Controller-facing command router for codex-refactor-loop."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from . import banners, github_body, project_rules, spawn, statusline
from .closed_label_reconciler import main as closed_label_reconciler_main
from .checks.degradation import main as degradation_main
from .checks.manifest import main as manifest_main
from .labels import main as labels_main
from .monitors.comment import main as comment_monitor_main
from .monitors.concurrency import main as concurrency_main
from .monitors.progress import main as progress_reporter_main
from .peek import main as peek_main
from .release.gate import main as release_gate_main
from .release.required_checks import main as release_required_checks_main
from .restart import main as restart_main
from .retention import main as retention_main
from .sync.dev import main as dev_sync_main
from .phase9.router import main as phase9_router_main
from .wakeup_plan import main as wakeup_plan_main


SCRIPT_DIR = Path(__file__).resolve().parents[1]


def release_commits_command(argv: Sequence[str] | None) -> int:
    # Refactor (fix/pr236-split-release-commits-command): Old pattern: release-gate inlined the git-reading release commit producer and gained read-git authority. New principle: release commits are produced by a separate narrow CLI surface whose only powers are read-git and write-artifact, keeping release-gate decider-only.
    from .release.commits import main as release_commits_main

    return release_commits_main(argv)


@dataclass(frozen=True)
class CommandSpec:
    handler: Callable[[Sequence[str] | None], int]
    description: str
    authority: tuple[str, ...]


# Refactor (iter201/issue-201): Old pattern: public consensus-rnd-cli exposed
# lifecycle commands and wakeup_plan/peek rendered copyable suggested_command,
# forming a generic lifecycle authority surface. New principle: COMMANDS keeps
# only public non-lifecycle CLI primitives; controller lifecycle actions stay
# controller-internal, with dev-sync's narrow integration-worktree carveout.
# Refactor (iter218/issue-218): Old pattern: ensure-project-rules 是 public CLI 默认写 host policy 文件($PROJECT_RULES),违反 skill 无 host 改动权边界
# New principle: 改为 read-only check-project-rules probe + patch artifact:probe 只读判 sentinel block,非 current 写 .refactor-loop/runs/ patch 并 fail-closed 不派 actor;删 ensure-project-rules/_atomic_write,不引入 PROJECT_RULES_WRITE_ENABLE。严格按 plan 逐条改。
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
    "closed-label-reconciler": CommandSpec(
        closed_label_reconciler_main,
        "run the closed managed item phase-label reconciler",
        ("read-gh", "gh-label-closed-reconcile", "write-state"),
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
        # Refactor (fix/pr245-router-authority-anchor): Old: phase9-router's public CommandSpec omitted the state-only GitHub read used by the source-OPEN gate. New: include read-gh in the closed-token authority tuple while keeping lifecycle mutation tokens absent.
        ("read-log", "read-gh", "write-event", "write-artifact", "spawn"),
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
    "post-banner": CommandSpec(banners.main, "post a controller status banner", ("gh-comment",)),
    "check-degradation": CommandSpec(
        degradation_main,
        "run the static skill degradation check",
        ("read-source", "read-state"),
    ),
    "check-manifest": CommandSpec(manifest_main, "run manifest version sync check", ("read-source",)),
    "log-retention": CommandSpec(retention_main, "run daemonless log retention", ("delete-log",)),
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
        spec = COMMANDS.get(command)
        if spec is None:
            sys.stderr.write(f"unknown command: {command}\n")
            return 2
        return spec.handler(args)


def main(argv: Sequence[str] | None = None) -> int:
    return RuntimeCommandRouter().main(argv)
