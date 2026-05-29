"""Fast read-only statusline snapshot renderer."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence


def snapshot_path(env: Mapping[str, str] | None = None, cwd: str | Path | None = None) -> Path | None:
    source_env = os.environ if env is None else env
    raw_root = source_env.get("REPO_ROOT")
    if raw_root:
        root = Path(raw_root)
    else:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        root = Path(result.stdout.strip())
    return root / ".refactor-loop" / "state" / "statusline-snapshot.json"


def render_snapshot(data: Mapping[str, Any]) -> str:
    actual = int(data.get("actual") or 0)
    floor = int(data.get("floor") or 0)
    p0 = int(data.get("p0_streak") or 0)
    prs = int(data.get("open_pr_count") or 0)
    issues = int(data.get("open_issue_count") or 0)
    freeze = int(data.get("freeze_minutes") or 0)
    d_healthy = int(data.get("daemons_healthy") or 0)
    d_total = int(data.get("daemons_total") or 0)
    leases = data.get("leases")
    lease_count = len(leases) if isinstance(leases, list) else 0
    icon = "⚙"
    color = ""
    if actual < floor:
        icon = "⚠"
        color = "\033[31m"
    if d_total > 0 and d_healthy < d_total:
        icon = "⚠"
        color = "\033[31m"
    if freeze > 10:
        icon = "🔴"
        color = "\033[31m"
    freeze_seg = f" [STUCK {freeze}m]" if freeze > 5 else ""
    p0_seg = f" P0×{p0}" if p0 > 2 else ""
    d_seg = f" d:{d_healthy}/{d_total}" if d_total > 0 else ""
    lease_seg = f" lease:{lease_count}" if lease_count else ""
    return f"{color}{icon} {actual}/{floor} PR:{prs} issue:{issues}{d_seg}{lease_seg}{p0_seg}{freeze_seg}\033[0m"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="consensus-rnd-cli statusline",
        description="render the latest read-only statusline snapshot",
    )
    parser.parse_args(argv)
    path = snapshot_path()
    if path is None or not path.is_file():
        print("⏸ no-snapshot")
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        print("⏸ no-snapshot")
        return 0
    print(render_snapshot(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
