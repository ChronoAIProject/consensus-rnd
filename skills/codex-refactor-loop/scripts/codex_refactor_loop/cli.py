"""Controller-facing command router for codex-refactor-loop."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from . import spawn, statusline
from .controller_actions import main as controller_actions_main
from .monitors.comment import main as comment_monitor_main
from .monitors.progress import main as progress_reporter_main
from .peek import main as peek_main
from .restart import main as restart_main
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
    "progress-reporter": CommandSpec(progress_reporter_main, "run the Python progress reporter daemon"),
    "merge-pr": CommandSpec(controller_actions_main, "invoke Python controller action merge_pr"),
    "open-pr": CommandSpec(controller_actions_main, "invoke Python controller action open_pr_with_label"),
}


class RuntimeCommandRouter:
    """Stable command-name router for Python runtime modules."""

    def __init__(self, script_dir: Path = SCRIPT_DIR) -> None:
        self.script_dir = script_dir

    def main(self, argv: Sequence[str] | None = None) -> int:
        parser = argparse.ArgumentParser(
            prog="codex_loop.py",
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
        if command == "merge-pr":
            return spec.handler(["merge-pr", *args])
        if command == "open-pr":
            return spec.handler(["open-pr", *args])
        return spec.handler(args)


def main(argv: Sequence[str] | None = None) -> int:
    return RuntimeCommandRouter().main(argv)
