"""Read-only PR-head Checks API projection."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

try:
    from .gh_invoke import build_gh_argv
except ImportError:  # pragma: no cover - direct script execution
    from gh_invoke import build_gh_argv


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


def _not_found(result: subprocess.CompletedProcess[str]) -> bool:
    text = f"{result.stdout}\n{result.stderr}".lower()
    return "404" in text or "not found" in text


def _required_names_from_value(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, str) and value.strip():
        names.add(value.strip())
    elif isinstance(value, list):
        for item in value:
            names.update(_required_names_from_value(item))
    elif isinstance(value, dict):
        for key in ("context", "name"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                names.add(item.strip())
        for key in ("contexts", "checks", "required_status_checks", "requiredStatusChecks"):
            names.update(_required_names_from_value(value.get(key)))
        parameters = value.get("parameters")
        if isinstance(parameters, dict):
            names.update(_required_names_from_value(parameters.get("required_status_checks")))
            names.update(_required_names_from_value(parameters.get("requiredStatusChecks")))
    return names


def _required_names_from_rules(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, list):
        for item in value:
            names.update(_required_names_from_rules(item))
    elif isinstance(value, dict):
        if value.get("type") == "required_status_checks":
            names.update(_required_names_from_value(value))
        for key, item in value.items():
            if key in {"required_status_checks", "requiredStatusChecks"}:
                names.update(_required_names_from_value(item))
            elif isinstance(item, (dict, list)):
                names.update(_required_names_from_rules(item))
    return names


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


@dataclass(frozen=True)
class PrMergeReadinessStatus:
    ok: bool
    repo: str
    pr: int
    base_ref: str
    head_sha: str
    merge_state_status: str
    required_passed: tuple[PrCheckRun, ...]
    required_failed: tuple[PrCheckRun, ...]
    required_pending: tuple[PrCheckRun, ...]
    missing_required: tuple[str, ...]
    advisory_failed: tuple[PrCheckRun, ...]
    advisory_pending: tuple[PrCheckRun, ...]
    required_check_names: tuple[str, ...]
    runs: tuple[PrCheckRun, ...]
    reason: str | None = None
    source: str = "pr-merge-readiness-projection"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "repo": self.repo,
            "pr": self.pr,
            "base_ref": self.base_ref,
            "head_sha": self.head_sha,
            "merge_state_status": self.merge_state_status,
            "reason": self.reason,
            "source": self.source,
            "required_check_names": list(self.required_check_names),
            "required_passed": [run.as_dict() for run in self.required_passed],
            "required_failed": [run.as_dict() for run in self.required_failed],
            "required_pending": [run.as_dict() for run in self.required_pending],
            "missing_required": list(self.missing_required),
            "advisory_failed": [run.as_dict() for run in self.advisory_failed],
            "advisory_pending": [run.as_dict() for run in self.advisory_pending],
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

        pull = self._run_api_read(build_gh_argv(repo_slug, ["gh", "api", f"repos/{repo_slug}/pulls/{pr}"]))
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

        checks = self._run_api_read(
            build_gh_argv(repo_slug, ["gh", "api", f"repos/{repo_slug}/commits/{head_sha}/check-runs", "--paginate", "--slurp"])
        )
        if checks.returncode != 0:
            return self._failed(repo_slug, pr, head_sha, "checks_api_failure")
        try:
            checks_payload = json.loads(checks.stdout)
        except json.JSONDecodeError:
            return self._failed(repo_slug, pr, head_sha, "invalid_checks_json")
        check_runs = _load_check_runs(checks_payload)
        if check_runs is None:
            return self._failed(repo_slug, pr, head_sha, "invalid_checks_json")

        return PrChecksStatus(ok=True, repo=repo_slug, pr=pr, head_sha=head_sha, runs=tuple(_runs_from_check_payload(check_runs)))

    def _failed(self, repo_slug: str, pr: int, head_sha: str, reason: str) -> PrChecksStatus:
        return PrChecksStatus(ok=False, repo=repo_slug, pr=pr, head_sha=head_sha, runs=(), reason=reason)


class PrMergeReadinessProjection:
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

    def check_pr(self, repo_slug: str, pr_number: int | str) -> PrMergeReadinessStatus:
        try:
            pr = int(pr_number)
        except (TypeError, ValueError):
            return self._failed(repo_slug, 0, "", "", "", "invalid_pr")
        if not repo_slug or "/" not in repo_slug:
            return self._failed(repo_slug, pr, "", "", "", "invalid_repo")

        pr_view = self._run_api_read(
            build_gh_argv(
                repo_slug,
                ["gh", "pr", "view", str(pr), "--json", "baseRefName,headRefOid,mergeStateStatus"],
            )
        )
        if pr_view.returncode != 0:
            return self._failed(repo_slug, pr, "", "", "", "pull_api_failure")
        try:
            pr_payload = json.loads(pr_view.stdout)
        except json.JSONDecodeError:
            return self._failed(repo_slug, pr, "", "", "", "invalid_pull_json")
        if not isinstance(pr_payload, dict):
            return self._failed(repo_slug, pr, "", "", "", "invalid_pull_json")
        base_ref = str(pr_payload.get("baseRefName") or "")
        head_sha = str(pr_payload.get("headRefOid") or "")
        merge_state_status = str(pr_payload.get("mergeStateStatus") or "")
        if not base_ref.strip():
            return self._failed(repo_slug, pr, "", "", merge_state_status, "missing_base_ref")
        if not head_sha.strip():
            return self._failed(repo_slug, pr, base_ref, "", merge_state_status, "missing_head_sha")

        required_check_names, required_reason = self._required_check_names(repo_slug, base_ref, merge_state_status)
        if required_reason is not None:
            return self._failed(repo_slug, pr, base_ref, head_sha, merge_state_status, required_reason)

        checks = self._run_api_read(
            build_gh_argv(repo_slug, ["gh", "api", f"repos/{repo_slug}/commits/{head_sha}/check-runs", "--paginate", "--slurp"])
        )
        if checks.returncode != 0:
            return self._failed(repo_slug, pr, base_ref, head_sha, merge_state_status, "checks_api_failure")
        try:
            checks_payload = json.loads(checks.stdout)
        except json.JSONDecodeError:
            return self._failed(repo_slug, pr, base_ref, head_sha, merge_state_status, "invalid_checks_json")
        check_runs = _load_check_runs(checks_payload)
        if check_runs is None:
            return self._failed(repo_slug, pr, base_ref, head_sha, merge_state_status, "invalid_checks_json")

        runs = tuple(_runs_from_check_payload(check_runs))
        required_set = set(required_check_names)
        seen_required: set[str] = set()
        required_passed: list[PrCheckRun] = []
        required_failed: list[PrCheckRun] = []
        required_pending: list[PrCheckRun] = []
        advisory_failed: list[PrCheckRun] = []
        advisory_pending: list[PrCheckRun] = []
        for run in runs:
            if run.name in required_set:
                seen_required.add(run.name)
                if run.bucket == "fail":
                    required_failed.append(run)
                elif run.bucket == "pending":
                    required_pending.append(run)
                else:
                    required_passed.append(run)
            elif run.bucket == "fail":
                advisory_failed.append(run)
            elif run.bucket == "pending":
                advisory_pending.append(run)
        missing_required = tuple(name for name in required_check_names if name not in seen_required)
        return PrMergeReadinessStatus(
            ok=True,
            repo=repo_slug,
            pr=pr,
            base_ref=base_ref,
            head_sha=head_sha,
            merge_state_status=merge_state_status,
            required_passed=tuple(required_passed),
            required_failed=tuple(required_failed),
            required_pending=tuple(required_pending),
            missing_required=missing_required,
            advisory_failed=tuple(advisory_failed),
            advisory_pending=tuple(advisory_pending),
            required_check_names=required_check_names,
            runs=runs,
        )

    def _required_check_names(self, repo_slug: str, base_ref: str, merge_state_status: str) -> tuple[tuple[str, ...], str | None]:
        classic_names: set[str] = set()
        classic = self._run_api_read(
            build_gh_argv(repo_slug, ["gh", "api", f"repos/{repo_slug}/branches/{base_ref}/protection/required_status_checks"])
        )
        if classic.returncode == 0:
            try:
                classic_payload = json.loads(classic.stdout)
            except json.JSONDecodeError:
                return (), "invalid_required_checks_json"
            classic_names = _required_names_from_value(classic_payload)
        elif not _not_found(classic):
            return (), "required_checks_api_failure"

        if classic_names:
            return tuple(sorted(classic_names)), None

        rules = self._run_api_read(build_gh_argv(repo_slug, ["gh", "api", f"repos/{repo_slug}/rules/branches/{base_ref}"]))
        if rules.returncode == 0:
            try:
                rules_payload = json.loads(rules.stdout)
            except json.JSONDecodeError:
                return (), "invalid_required_rules_json"
            return tuple(sorted(_required_names_from_rules(rules_payload))), None
        if merge_state_status == "BLOCKED":
            return (), "required_rules_unavailable"
        if _not_found(classic) and _not_found(rules):
            return (), None
        if rules.returncode != 0 and not _not_found(rules):
            return (), "required_rules_api_failure"
        return (), None

    def _failed(
        self,
        repo_slug: str,
        pr: int,
        base_ref: str,
        head_sha: str,
        merge_state_status: str,
        reason: str,
    ) -> PrMergeReadinessStatus:
        return PrMergeReadinessStatus(
            ok=False,
            repo=repo_slug,
            pr=pr,
            base_ref=base_ref,
            head_sha=head_sha,
            merge_state_status=merge_state_status,
            required_passed=(),
            required_failed=(),
            required_pending=(),
            missing_required=(),
            advisory_failed=(),
            advisory_pending=(),
            required_check_names=(),
            runs=(),
            reason=reason,
        )


def _runs_from_check_payload(check_runs: Sequence[Any]) -> list[PrCheckRun]:
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
    return runs


def check_pr(
    repo_slug: str,
    pr_number: int | str,
    *,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None,
    cwd: Path | None = None,
) -> PrChecksStatus:
    return PrChecksProjection(runner=runner, cwd=cwd).check_pr(repo_slug, pr_number)


def check_pr_merge_readiness(
    repo_slug: str,
    pr_number: int | str,
    *,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None,
    cwd: Path | None = None,
) -> PrMergeReadinessStatus:
    return PrMergeReadinessProjection(runner=runner, cwd=cwd).check_pr(repo_slug, pr_number)


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
    "PrMergeReadinessProjection",
    "PrMergeReadinessStatus",
    "check_pr",
    "check_pr_merge_readiness",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
