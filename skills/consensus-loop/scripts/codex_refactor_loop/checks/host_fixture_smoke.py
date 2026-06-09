"""Private bounded host-fixture smoke for the source-repo degradation check."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HostFixtureSmokeFinding:
    check: str
    path: str
    message: str
    severity: str = "error"


def run_no_manifest_open_milestone_smoke(
    source_repo_root: Path,
    *,
    timeout_seconds: int = 20,
) -> list[HostFixtureSmokeFinding]:
    with tempfile.TemporaryDirectory(prefix="crnd-host-fixture-smoke-") as tmp:
        host = Path(tmp) / "host"
        fakebin = host / "bin"
        config_dir = host / ".config" / "consensus-rnd"
        logs_dir = host / ".refactor-loop" / "logs"
        state_dir = host / ".refactor-loop" / "state"
        heartbeat_dir = host / ".refactor-loop" / "heartbeats"
        fakebin.mkdir(parents=True)
        config_dir.mkdir(parents=True)
        logs_dir.mkdir(parents=True)
        state_dir.mkdir(parents=True)
        heartbeat_dir.mkdir(parents=True)
        (host / ".refactor-loop" / ".controller-pending-events.log").write_text("", encoding="utf-8")
        (config_dir / "host.env").write_text(
            "\n".join(
                (
                    f"REPO_ROOT={host}",
                    "GH_REPO_SLUG=owner/repo",
                    "CODEX_FLOOR=5",
                    "RELEASE_AUTO_ENABLE=false",
                    "",
                )
            ),
            encoding="utf-8",
        )
        _write_fake_gh(fakebin / "gh")
        _write_fake_git(fakebin / "git")
        result = _run_wakeup_plan(source_repo_root.resolve(), host, fakebin, timeout_seconds)
        findings = _findings_from_result(result, host)
        if (host / ".version-bump.json").exists():
            findings.append(
                HostFixtureSmokeFinding(
                    "host-fixture-smoke",
                    ".version-bump.json",
                    "host fixture smoke must not create a version manifest",
                )
            )
        for relative in (
            ".refactor-loop/state/release-decision.json",
            ".refactor-loop/state/release-candidate.json",
        ):
            if (host / relative).exists():
                findings.append(
                    HostFixtureSmokeFinding(
                        "host-fixture-smoke",
                        relative,
                        "disabled release fixture must not write release artifacts",
                    )
                )
        if not (host / ".config" / "consensus-rnd" / "host.env").exists():
            findings.append(
                HostFixtureSmokeFinding(
                    "host-fixture-smoke",
                    ".config/consensus-rnd/host.env",
                    "host fixture must use host-owned host.env",
                )
            )
        if (host / ".refactor-loop" / "host.env").exists():
            findings.append(
                HostFixtureSmokeFinding(
                    "host-fixture-smoke",
                    ".refactor-loop/host.env",
                    "host fixture must not use .refactor-loop/host.env as production SSOT",
                )
            )
        return findings


def _run_wakeup_plan(
    source_repo_root: Path,
    host: Path,
    fakebin: Path,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str] | TimeoutError:
    cli = source_repo_root / "skills" / "consensus-loop" / "scripts" / "consensus-rnd-cli"
    env = {
        "PATH": f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}",
        "PYTHONPATH": str(source_repo_root / "skills" / "consensus-loop" / "scripts"),
        "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env",
        "REPO_ROOT": str(host),
    }
    try:
        return subprocess.run(
            [sys.executable, str(cli), "wakeup-plan", "--repo-root", str(host)],
            cwd=host,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return TimeoutError(f"wakeup-plan timed out after {exc.timeout} seconds")


def _findings_from_result(
    result: subprocess.CompletedProcess[str] | TimeoutError,
    host: Path,
) -> list[HostFixtureSmokeFinding]:
    if isinstance(result, TimeoutError):
        return [HostFixtureSmokeFinding("host-fixture-smoke", ".", str(result))]
    findings: list[HostFixtureSmokeFinding] = []
    combined = "\n".join((result.stdout, result.stderr))
    if result.returncode != 0:
        findings.append(
            HostFixtureSmokeFinding(
                "host-fixture-smoke",
                ".",
                f"wakeup-plan exited {result.returncode}: {_compact(combined)}",
            )
        )
    if "Traceback" in combined:
        findings.append(
            HostFixtureSmokeFinding(
                "host-fixture-smoke",
                ".",
                f"wakeup-plan emitted traceback: {_compact(combined)}",
            )
        )
    try:
        payload, _ = json.JSONDecoder().raw_decode(result.stdout.lstrip())
    except json.JSONDecodeError as exc:
        findings.append(
            HostFixtureSmokeFinding(
                "host-fixture-smoke",
                ".",
                f"wakeup-plan stdout was not JSON: {exc}",
            )
        )
        return findings
    if payload.get("schema") != "wakeup-plan":
        findings.append(
            HostFixtureSmokeFinding(
                "host-fixture-smoke",
                ".",
                "wakeup-plan did not return the wakeup-plan schema",
            )
        )
    actions = payload.get("actions")
    if not isinstance(actions, list):
        findings.append(HostFixtureSmokeFinding("host-fixture-smoke", ".", "wakeup-plan actions are invalid"))
        return findings
    release_actions = [action for action in actions if isinstance(action, dict) and action.get("kind") == "release-countdown"]
    if not release_actions:
        findings.append(
            HostFixtureSmokeFinding(
                "host-fixture-smoke",
                ".",
                "wakeup-plan did not project the open milestone release countdown",
            )
        )
        return findings
    release = release_actions[0]
    goal = release.get("goal") if isinstance(release.get("goal"), dict) else {}
    if goal.get("release") is not None:
        findings.append(
            HostFixtureSmokeFinding(
                "host-fixture-smoke",
                ".",
                "missing .version-bump.json profile must keep release goal fail-soft",
            )
        )
    milestone = goal.get("milestone") if isinstance(goal.get("milestone"), dict) else {}
    if milestone.get("number") != 1:
        findings.append(
            HostFixtureSmokeFinding(
                "host-fixture-smoke",
                ".",
                "fake open milestone was not consumed read-only",
            )
        )
    if release.get("status_only") is not True or release.get("no_lifecycle_authority") is not True:
        findings.append(
            HostFixtureSmokeFinding(
                "host-fixture-smoke",
                ".",
                "release countdown projection must remain status-only with no lifecycle authority",
            )
        )
    return findings


def _write_fake_gh(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            r"""#!/usr/bin/env bash
            set -euo pipefail
            if [[ "$1" == "issue" && "$2" == "list" ]]; then
              printf '[]\n'
              exit 0
            fi
            if [[ "$1" == "pr" && "$2" == "list" ]]; then
              printf '[]\n'
              exit 0
            fi
            if [[ "$1" == "api" && "$*" == *"milestones?state=open"* ]]; then
              printf '[{"number":1,"title":"Fixture release","due_on":"2026-06-15T00:00:00Z"}]\n'
              exit 0
            fi
            printf 'unexpected gh command: %s\n' "$*" >&2
            exit 64
            """
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def _write_fake_git(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            r"""#!/usr/bin/env bash
            set -euo pipefail
            if [[ "$1" == "status" ]]; then
              printf '## fixture\n'
              exit 0
            fi
            printf 'unexpected git command: %s\n' "$*" >&2
            exit 64
            """
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def _compact(text: str, *, limit: int = 500) -> str:
    compacted = " ".join(text.split())
    return compacted[:limit]
