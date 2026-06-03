"""Read-only PR-head Checks API projection."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence


API_READ_ATTEMPTS = 3


def run_command(cmd: Sequence[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=False)


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


def _bucket_for_check(status: str, conclusion: str) -> str:
    if status != "completed":
        return "pending"
    if conclusion == "success":
        return "pass"
    if conclusion in {"skipped", "neutral"}:
        return "skipping"
    return "fail"


@dataclass(frozen=True)
class PrCheckRun:
    name: str
    bucket: str
    state: str
    link: str
    conclusion: str
    status: str
    started_at: str
    completed_at: str

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "bucket": self.bucket,
            "state": self.state,
            "link": self.link,
            "conclusion": self.conclusion,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True)
class PrChecksStatus:
    ok: bool
    repo: str
    pr: int
    head_sha: str
    runs: tuple[PrCheckRun, ...]
    reason: str | None = None
    source: str = "pr-head-checks-api-projection"

    def as_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for run in self.runs:
            counts[run.bucket] = counts.get(run.bucket, 0) + 1
        return {
            "ok": self.ok,
            "repo": self.repo,
            "pr": self.pr,
            "head_sha": self.head_sha,
            "reason": self.reason,
            "source": self.source,
            "bucket_counts": counts,
            "checks": [run.as_dict() for run in self.runs],
        }


class PrChecksProjection:
    def __init__(
        self,
        *,
        runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None,
        cwd: Path | None = None,
    ) -> None:
        self._runner = runner
        self.cwd = cwd

    def _run(self, cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if self._runner is not None:
            return self._runner(cmd)
        return run_command(cmd, cwd=self.cwd)

    def _run_api_read(self, cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        result = self._run(cmd)
        for _attempt in range(1, API_READ_ATTEMPTS):
            if result.returncode == 0:
                break
            result = self._run(cmd)
        return result

    def check_pr(self, repo_slug: str, pr_number: int | str) -> PrChecksStatus:
        try:
            pr = int(pr_number)
        except (TypeError, ValueError):
            return self._failed(repo_slug, 0, "", "invalid_pr")
        if not repo_slug or "/" not in repo_slug:
            return self._failed(repo_slug, pr, "", "invalid_repo")

        pull = self._run_api_read(["gh", "api", f"repos/{repo_slug}/pulls/{pr}"])
        if pull.returncode != 0:
            return self._failed(repo_slug, pr, "", "pull_api_failure")
        try:
            pull_payload = json.loads(pull.stdout)
        except json.JSONDecodeError:
            return self._failed(repo_slug, pr, "", "invalid_pull_json")
        head = pull_payload.get("head") if isinstance(pull_payload, dict) else None
        head_sha = head.get("sha") if isinstance(head, dict) else None
        if not isinstance(head_sha, str) or not head_sha.strip():
            return self._failed(repo_slug, pr, "", "missing_head_sha")

        checks = self._run_api_read(["gh", "api", f"repos/{repo_slug}/commits/{head_sha}/check-runs", "--paginate", "--slurp"])
        if checks.returncode != 0:
            return self._failed(repo_slug, pr, head_sha, "checks_api_failure")
        try:
            checks_payload = json.loads(checks.stdout)
        except json.JSONDecodeError:
            return self._failed(repo_slug, pr, head_sha, "invalid_checks_json")
        check_runs = _load_check_runs(checks_payload)
        if check_runs is None:
            return self._failed(repo_slug, pr, head_sha, "invalid_checks_json")

        runs: list[PrCheckRun] = []
        for check in check_runs:
            if not isinstance(check, dict):
                continue
            name = str(check.get("name") or "")
            if not name:
                continue
            status = str(check.get("status") or "")
            conclusion = str(check.get("conclusion") or "")
            bucket = _bucket_for_check(status, conclusion)
            runs.append(
                PrCheckRun(
                    name=name,
                    bucket=bucket,
                    state=status,
                    link=str(check.get("html_url") or check.get("details_url") or ""),
                    conclusion=conclusion,
                    status=status,
                    started_at=str(check.get("started_at") or ""),
                    completed_at=str(check.get("completed_at") or ""),
                )
            )
        return PrChecksStatus(ok=True, repo=repo_slug, pr=pr, head_sha=head_sha, runs=tuple(runs))

    def _failed(self, repo_slug: str, pr: int, head_sha: str, reason: str) -> PrChecksStatus:
        return PrChecksStatus(ok=False, repo=repo_slug, pr=pr, head_sha=head_sha, runs=(), reason=reason)


def check_pr(
    repo_slug: str,
    pr_number: int | str,
    *,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None,
    cwd: Path | None = None,
) -> PrChecksStatus:
    return PrChecksProjection(runner=runner, cwd=cwd).check_pr(repo_slug, pr_number)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="GitHub OWNER/REPO slug")
    parser.add_argument("--pr", required=True, type=int, help="Pull request number")
    parser.add_argument("--json", action="store_true", help="print machine-readable status")
    args = parser.parse_args(argv)

    status = check_pr(args.repo, args.pr, cwd=Path.cwd())
    if args.json:
        print(json.dumps(status.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    elif status.ok:
        for run in status.runs:
            print(f"{run.name}\t{run.bucket}\t{run.state}\t{run.conclusion}\t{run.link}")
    else:
        print(f"pr checks projection failed for PR #{args.pr}: {status.reason}", file=sys.stderr)
    return 0 if status.ok else 1


__all__ = [
    "PrCheckRun",
    "PrChecksProjection",
    "PrChecksStatus",
    "check_pr",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
