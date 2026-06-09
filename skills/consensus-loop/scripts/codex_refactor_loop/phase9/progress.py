"""Read-only design-consensus progress projection."""

from __future__ import annotations

import re
from pathlib import Path

from ..worker_markers import read_worker_terminal_marker


CONSENSUS_JUDGE_LOG_RE = re.compile(r"^phase9-issue([1-9][0-9]*)-r([1-9][0-9]*)-judge\.log$")


def issue_has_terminal_consensus_judge(repo_root: Path, issue: int) -> bool:
    """Return true when phase9 has a clean terminal consensus judge for an issue."""
    logs_dir = repo_root / ".refactor-loop" / "logs"
    if not logs_dir.is_dir():
        return False
    for log_path in logs_dir.glob(f"phase9-issue{issue}-r*-judge.log"):
        match = CONSENSUS_JUDGE_LOG_RE.fullmatch(log_path.name)
        if match is None or int(match.group(1)) != issue:
            continue
        marker = read_worker_terminal_marker(log_path).marker
        if marker.startswith("META_JUDGE_DONE:consensus:"):
            return True
    return False
