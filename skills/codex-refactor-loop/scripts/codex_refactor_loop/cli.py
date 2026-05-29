"""Controller-facing command router for codex-refactor-loop."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


SCRIPT_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class CommandSpec:
    script: str
    description: str
    read_only: bool = False


COMMANDS: dict[str, CommandSpec] = {
    "spawn-codex": CommandSpec("spawn-codex.sh", "run the existing codex spawn supervisor"),
    "peek": CommandSpec("peek.sh", "run the existing read-only state sweep", read_only=True),
    "restart-daemons": CommandSpec("restart-daemons.sh", "run the existing daemon restart helper"),
    "statusline": CommandSpec("statusline.sh", "read the existing statusline snapshot", read_only=True),
    "comment-monitor": CommandSpec("comment-monitor.sh", "run the existing comment monitor daemon"),
    "progress-reporter": CommandSpec("codex-progress-reporter.sh", "run the existing progress reporter daemon"),
    "merge-pr": CommandSpec("controller_lib.sh", "invoke controller_lib.sh merge_pr"),
    "open-pr": CommandSpec("controller_lib.sh", "invoke controller_lib.sh open_pr_with_label"),
}


class RuntimeCommandRouter:
    """Stable command-name router that delegates to current scripts."""

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
            return self._run_controller_lib("merge_pr", args)
        if command == "open-pr":
            return self._run_controller_lib("open_pr_with_label", args)
        return self._exec_script(spec.script, args)

    def _exec_script(self, script_name: str, args: Sequence[str]) -> int:
        script = self.script_dir / script_name
        cmd = ["bash", str(script), *args] if script.suffix == ".sh" else [sys.executable, str(script), *args]
        return subprocess.call(cmd)

    def _run_controller_lib(self, function_name: str, args: Sequence[str]) -> int:
        quoted = " ".join(_shell_quote(arg) for arg in args)
        call = f"{function_name} {quoted}".rstrip()
        body = f"source {_shell_quote(str(self.script_dir / 'controller_lib.sh'))}; {call}"
        return subprocess.call(["bash", "-c", body], env=os.environ.copy())


def main(argv: Sequence[str] | None = None) -> int:
    return RuntimeCommandRouter().main(argv)


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
