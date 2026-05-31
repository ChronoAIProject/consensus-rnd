"""Shared read-only release required-check projection."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Sequence


REQUIRED_RELEASE_CHECKS = ("contract-tests", "manifest-version-sync", "skill-degradation")


@dataclass(frozen=True)
class RequiredCheckStatus:
    passed: bool
    reason: str | None
    checks: dict[str, bool]
    red_checks: list[dict[str, str]]
    pending_checks: list[str]
    stale_checks: list[str]
    ref: str
    source: str = "checks-api-projection"

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "checks": self.checks,
            "red_checks": self.red_checks,
            "pending_checks": self.pending_checks,
            "stale_checks": self.stale_checks,
            "ref": self.ref,
            "source": self.source,
        }


def run_command(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=False)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _check_sort_time(check: dict[str, Any]) -> datetime:
    for field in ("completed_at", "started_at", "created_at"):
        parsed = parse_time(check.get(field))
        if parsed is not None:
            return parsed
    return datetime.min.replace(tzinfo=timezone.utc)


def _load_check_runs(payload: Any) -> list[dict[str, Any]] | None:
    if isinstance(payload, dict):
        check_runs = payload.get("check_runs")
        return check_runs if isinstance(check_runs, list) else None
    if isinstance(payload, list):
        merged: list[dict[str, Any]] = []
        for page in payload:
            if isinstance(page, dict) and isinstance(page.get("check_runs"), list):
                merged.extend(page["check_runs"])
            elif isinstance(page, list):
                merged.extend(item for item in page if isinstance(item, dict))
        return merged
    return None


class ReleaseRequiredChecksProjection:
    def __init__(
        self,
        *,
        runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = run_command,
        now: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.runner = runner
        self.now = now
        self.sleep = sleep

    def check_ref(
        self,
        repo_slug: str,
        ref: str,
        *,
        since: datetime | None = None,
        wait_seconds: int = 0,
        poll_seconds: int = 10,
    ) -> RequiredCheckStatus:
        deadline = self.now() + timedelta(seconds=max(wait_seconds, 0))
        last_status: RequiredCheckStatus | None = None
        while True:
            status = self._check_ref_once(repo_slug, ref, since=since)
            if status.passed or wait_seconds <= 0:
                return status
            last_status = status
            if self.now() >= deadline:
                return last_status
            self.sleep(max(poll_seconds, 1))

    def check_refs(
        self,
        repo_slug: str,
        refs: Iterable[str],
        *,
        since: datetime,
    ) -> dict[str, RequiredCheckStatus]:
        return {ref: self.check_ref(repo_slug, ref, since=since) for ref in refs}

    def _check_ref_once(self, repo_slug: str, ref: str, *, since: datetime | None) -> RequiredCheckStatus:
        result = self.runner(
            [
                "gh",
                "api",
                f"repos/{repo_slug}/commits/{ref}/check-runs",
                "--paginate",
                "--slurp",
            ]
        )
        if result.returncode != 0:
            return self._failed(ref, "api_failure", {}, [], [], [])
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return self._failed(ref, "invalid_json", {}, [], [], [])
        check_runs = _load_check_runs(payload)
        if check_runs is None:
            return self._failed(ref, "invalid_json", {}, [], [], [])

        latest_by_name: dict[str, dict[str, Any]] = {}
        for check in check_runs:
            if not isinstance(check, dict):
                continue
            name = check.get("name")
            if name not in REQUIRED_RELEASE_CHECKS:
                continue
            previous = latest_by_name.get(name)
            if previous is None or _check_sort_time(check) >= _check_sort_time(previous):
                latest_by_name[name] = check

        checks: dict[str, bool] = {}
        red_checks: list[dict[str, str]] = []
        pending_checks: list[str] = []
        stale_checks: list[str] = []
        missing_checks: list[str] = []
        for name in REQUIRED_RELEASE_CHECKS:
            check = latest_by_name.get(name)
            if check is None:
                checks[name] = False
                missing_checks.append(name)
                continue
            if since is not None and _check_sort_time(check) < since:
                checks[name] = False
                stale_checks.append(name)
                continue
            status = str(check.get("status") or "")
            conclusion = str(check.get("conclusion") or "")
            if status != "completed":
                checks[name] = False
                pending_checks.append(name)
                continue
            if conclusion != "success":
                checks[name] = False
                red_checks.append({"name": name, "conclusion": conclusion or "missing"})
                continue
            checks[name] = True

        if red_checks:
            reason = "ci_red"
        elif pending_checks:
            reason = "pending_required_checks"
        elif stale_checks:
            reason = "stale_required_checks"
        elif missing_checks:
            reason = "missing_required_checks_recent_green"
        else:
            reason = None
        return RequiredCheckStatus(
            passed=reason is None,
            reason=reason,
            checks=checks,
            red_checks=red_checks,
            pending_checks=pending_checks,
            stale_checks=stale_checks,
            ref=ref,
        )

    def _failed(
        self,
        ref: str,
        reason: str,
        checks: dict[str, bool],
        red_checks: list[dict[str, str]],
        pending_checks: list[str],
        stale_checks: list[str],
    ) -> RequiredCheckStatus:
        return RequiredCheckStatus(
            passed=False,
            reason=reason,
            checks={name: checks.get(name, False) for name in REQUIRED_RELEASE_CHECKS},
            red_checks=red_checks,
            pending_checks=pending_checks,
            stale_checks=stale_checks,
            ref=ref,
        )


def check_ref(
    repo_slug: str,
    ref: str,
    *,
    since: datetime | None = None,
    wait_seconds: int = 0,
    poll_seconds: int = 10,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = run_command,
    now: Callable[[], datetime] = utc_now,
) -> RequiredCheckStatus:
    return ReleaseRequiredChecksProjection(runner=runner, now=now).check_ref(
        repo_slug,
        ref,
        since=since,
        wait_seconds=wait_seconds,
        poll_seconds=poll_seconds,
    )


def check_refs(
    repo_slug: str,
    refs: Iterable[str],
    *,
    since: datetime,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = run_command,
    now: Callable[[], datetime] = utc_now,
) -> dict[str, RequiredCheckStatus]:
    return ReleaseRequiredChecksProjection(runner=runner, now=now).check_refs(repo_slug, refs, since=since)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="GitHub OWNER/REPO slug")
    parser.add_argument("--ref", required=True, help="Commit SHA or branch ref to inspect")
    parser.add_argument("--since-minutes", type=int, default=120)
    parser.add_argument("--wait-seconds", type=int, default=0)
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="print machine-readable status")
    args = parser.parse_args(argv)

    since = utc_now() - timedelta(minutes=args.since_minutes)
    status = check_ref(
        args.repo,
        args.ref,
        since=since,
        wait_seconds=args.wait_seconds,
        poll_seconds=args.poll_seconds,
    )
    if args.json:
        print(json.dumps(status.as_dict(), ensure_ascii=False, indent=2))
    elif status.passed:
        print(f"release required checks passed for {args.ref}")
    else:
        print(f"release required checks failed for {args.ref}: {status.reason}", file=sys.stderr)
    return 0 if status.passed else 1


__all__ = [
    "REQUIRED_RELEASE_CHECKS",
    "ReleaseRequiredChecksProjection",
    "RequiredCheckStatus",
    "check_ref",
    "check_refs",
    "isoformat",
    "main",
    "parse_time",
]
