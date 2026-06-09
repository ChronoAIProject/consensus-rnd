"""Python implementation of the codex spawn supervisor."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

from .gh_accounting import accounting_env
from .processes import ProcessSupervisor, prompt_file_from_text
from .task_spawn_claim import TaskSpawnClaimError, TaskSpawnClaimStore, safe_task_id_from_task


def build_codex_args(*, cd: str, model: str, add_dirs: Sequence[str]) -> list[str]:
    args = [
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "-C",
        cd,
    ]
    for directory in add_dirs:
        args.extend(["--add-dir", directory])
    if model:
        args.extend(["-m", model])
    args.append("-")
    return args


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run codex with process-exit-or-timeout supervision")
    parser.add_argument("--cd", required=True)
    prompt = parser.add_mutually_exclusive_group(required=True)
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-text")
    parser.add_argument("--log", required=True)
    parser.add_argument("--stall", "--timeout", dest="stall", required=True, type=int)
    parser.add_argument("--model", default="")
    parser.add_argument("--add-dir", action="append", default=[])
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    prompt_path = Path(args.prompt) if args.prompt else prompt_file_from_text(args.prompt_text)
    log_path = Path(args.log)
    if not prompt_path.is_file():
        sys.stderr.write(f"prompt file not found: {prompt_path}\n")
        return 2
    if args.stall <= 0:
        sys.stderr.write(f"codex timeout must be a positive integer number of seconds: {args.stall}\n")
        return 2
    command = build_codex_args(cd=args.cd, model=args.model, add_dirs=args.add_dir)
    try:
        task_id = _task_id_from_log(log_path)
    except TaskSpawnClaimError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    usage_repo = Path(os.environ.get("REPO_ROOT") or args.cd)
    repo_root = usage_repo.resolve()
    try:
        claim = TaskSpawnClaimStore(repo_root).acquire(task_id, log_path=log_path)
    except TaskSpawnClaimError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2 if "task id" in str(exc) else 1
    if not claim.acquired:
        sys.stderr.write(f"SPAWN_CLAIM_HELD:task={claim.task_id} lock={claim.lock_path}\n")
        return 0
    child_env = accounting_env(os.environ, skill_root=Path(__file__).resolve().parents[2], repo_root=usage_repo, source=f"codex:{task_id}", force_source=True)
    banner = f"SPAWN: prompt={prompt_path} log={log_path} cd={args.cd} timeout={args.stall}s"
    if args.model:
        banner += f" model={args.model}"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        sys.stderr.write(banner + "\n")
        exit_code = ProcessSupervisor().supervise(command, stdin=prompt_path, log=log_path, stall=args.stall, preamble=banner + "\n", env=child_env)
    except RuntimeError as exc:
        sys.stderr.write(f"{exc}\n")
        return 3 if "refusing to reuse unfinished log" in str(exc) else 1
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    sys.stderr.write(f"DONE: log={log_path} exit={exit_code} prompt={prompt_path}\n")
    return exit_code


def _task_id_from_log(log_path: Path) -> str:
    stem = log_path.name.rsplit(".", 1)[0]
    return safe_task_id_from_task(stem)


if __name__ == "__main__":
    raise SystemExit(main())
