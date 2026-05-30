"""One-shot autonomous release stability gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from .. import labels as label_catalog
from ..state import read_json, write_json
from .required_checks import REQUIRED_RELEASE_CHECKS, ReleaseRequiredChecksProjection


# Refactor (issue160-p3-auto-release-gate):
#   Old pattern: scripts/auto_release_gate.py owned release-decision behavior as
#   a top-level script, which made later package CLI routing import-unsafe.
#   New principle: keep every gate signal and artifact byte-for-byte compatible,
#   but expose package functions/classes from codex_refactor_loop.release.gate.
#   This module remains decision-artifact-only per
#   skills/codex-refactor-loop/authorizations/runtime-exceptions.md#autonomous-release-gate-56;
#   it has no lifecycle authority and does not bump, commit, push, tag,
#   publish, merge, close, or mutate issue/PR labels.
#
# Refactor (iter217/issue-217):
#   Old pattern: release.yml 保留 tag/release mutation,无法可靠读本地 runtime fact,绕过 release-gate decider-only 边界
#   New principle: controller-only publication:新增 ReleasePublishPreflight+ReleasePublisher 替代 workflow 发布权;release.yml 降为 read-only preview(contents:read,禁 gh release create)。严格按 plan 'Concrete plan' 逐条改。

SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
SIGNAL_NAMES = (
    "required_checks_recent_green",
    "no_open_blocked_pr",
    "no_human_decision_label",
    "no_phase8_reject_churn",
    "p0_alert_streak_ok",
    "recent_pr_merges_min",
    "fresh_heartbeats",
    "no_unresolved_human_escalation",
)
DAEMON_NAMES = (
    "concurrency_monitor",
    "codex-progress-reporter",
    "comment-monitor",
    "dev_sync_daemon",
    "phase9_router_daemon",
)
REQUIRED_CHECKS = REQUIRED_RELEASE_CHECKS
HEARTBEAT_FRESH_SECONDS = 90


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    subject: str
    body: str


@dataclass(frozen=True)
class StabilityResult:
    ready: bool
    score: int
    signals: dict[str, dict[str, Any]]


def run_command(cmd: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, check=False)


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


def parse_host_env_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def load_host_env(repo_root: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for path in (repo_root / "host.env", repo_root / ".refactor-loop" / "host.env"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            if stripped.startswith("export "):
                stripped = stripped[len("export "):].strip()
            key, raw_value = stripped.split("=", 1)
            key = key.strip()
            if key:
                values[key] = parse_host_env_value(raw_value)
    return values


def inject_host_env(repo_root: Path) -> dict[str, str]:
    values = load_host_env(repo_root)
    for key, value in values.items():
        os.environ[key] = value
    return values


def repo_root_from_env() -> Path:
    env_root = os.environ.get("REPO_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    raise RuntimeError("REPO_ROOT is unset; consensus-rnd-cli release-gate does not infer it with git")


class AutoReleaseGate:
    def __init__(
        self,
        repo_root: Path,
        *,
        now: Callable[[], datetime] = utc_now,
        runner: Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]] = run_command,
    ) -> None:
        self.repo_root = repo_root
        self.now = now
        self.runner = runner

    @property
    def state_dir(self) -> Path:
        return self.repo_root / ".refactor-loop" / "state"

    @property
    def decision_path(self) -> Path:
        return self.state_dir / "release-decision.json"

    @property
    def candidate_path(self) -> Path:
        return self.state_dir / "release-candidate.json"

    @property
    def signal_path(self) -> Path:
        return self.state_dir / "auto-release-signals.json"

    @property
    def release_commits_path(self) -> Path:
        return self.state_dir / "release-commits.json"

    @property
    def recent_merges_path(self) -> Path:
        return self.state_dir / "recent-pr-merges.json"

    @property
    def heartbeat_dir(self) -> Path:
        return self.repo_root / ".refactor-loop" / "heartbeats"

    def current_version(self) -> str:
        targets = self.load_manifest_targets()
        versions = {target["version"] for target in targets}
        if len(versions) != 1:
            raise RuntimeError("mapped manifest versions are not synchronized")
        return next(iter(versions))

    def load_manifest_targets(self) -> list[dict[str, Any]]:
        mapping = read_json(self.repo_root / ".version-bump.json", {})
        files = mapping.get("files") if isinstance(mapping, dict) else None
        if not isinstance(files, list):
            raise RuntimeError(".version-bump.json: expected top-level files list")
        targets: list[dict[str, Any]] = []
        for item in files:
            if not isinstance(item, dict):
                raise RuntimeError(".version-bump.json: files entries must be objects")
            relative = item.get("path")
            field = item.get("field")
            if not isinstance(relative, str) or not isinstance(field, str):
                raise RuntimeError(".version-bump.json: files entries require path and field")
            path = self.repo_root / relative
            data = read_json(path, None)
            version = resolve_field(data, field)
            parse_semver(version)
            targets.append({"path": path, "relative": relative, "field": field, "data": data, "version": version})
        return targets

    def compute_stability(self, min_recent_merges: int = 1) -> StabilityResult:
        raw = read_json(self.signal_path, {})
        signals: dict[str, dict[str, Any]] = {}
        for name in SIGNAL_NAMES:
            signals[name] = self._signal_from_fixture(name, raw)
        if raw:
            self._fill_signal_defaults(signals, min_recent_merges)
        else:
            self._fill_live_signals(signals, min_recent_merges)
        self._annotate_signal_reasons(signals)
        passed = sum(1 for value in signals.values() if value["passed"])
        score = int(round((passed / len(SIGNAL_NAMES)) * 100))
        return StabilityResult(ready=passed == len(SIGNAL_NAMES), score=score, signals=signals)

    def _signal_from_fixture(self, name: str, raw: Any) -> dict[str, Any]:
        raw_signals = raw.get("signals") if isinstance(raw, dict) and isinstance(raw.get("signals"), dict) else raw
        value = raw_signals.get(name) if isinstance(raw_signals, dict) else None
        if isinstance(value, dict):
            return {"passed": bool(value.get("passed")), **value}
        if isinstance(value, bool):
            return {"passed": value, "source": "auto-release-signals.json"}
        return {"passed": False, "source": "unset"}

    def _fill_signal_defaults(self, signals: dict[str, dict[str, Any]], min_recent_merges: int) -> None:
        raw = read_json(self.signal_path, {})
        if "recent_pr_merges_min" in signals and signals["recent_pr_merges_min"].get("source") == "unset":
            count = raw.get("recent_pr_merges") if isinstance(raw, dict) else None
            signals["recent_pr_merges_min"] = {
                "passed": isinstance(count, int) and count >= min_recent_merges,
                "count": count,
                "minimum": min_recent_merges,
                "source": "auto-release-signals.json",
            }

    def _fill_live_signals(self, signals: dict[str, dict[str, Any]], min_recent_merges: int) -> None:
        since_2h = self.now() - timedelta(hours=2)
        since_30m = self.now() - timedelta(minutes=30)
        signals["required_checks_recent_green"] = self.required_checks_recent_green(since_2h)
        signals["no_open_blocked_pr"] = self.no_label_on_open_prs(label_catalog.PHASE_BLOCKED)
        signals["no_human_decision_label"] = self.no_label_on_open_items(label_catalog.HUMAN_MAINTAINER_DECISION)
        signals["no_phase8_reject_churn"] = self.no_phase8_reject_churn()
        signals["p0_alert_streak_ok"] = self.p0_alert_streak_ok(since_30m)
        signals["recent_pr_merges_min"] = self.recent_pr_merges_min(since_2h, min_recent_merges)
        signals["fresh_heartbeats"] = self.fresh_heartbeats()
        signals["no_unresolved_human_escalation"] = self.no_unresolved_human_escalation()

    def _annotate_signal_reasons(self, signals: dict[str, dict[str, Any]]) -> None:
        for name, signal in signals.items():
            if signal.get("passed"):
                continue
            reason = signal.get("reason")
            detail = reason if isinstance(reason, str) and reason else "failed"
            if name not in detail:
                signal["reason"] = f"{name}:{detail}"

    def required_checks_recent_green(self, since: datetime) -> dict[str, Any]:
        try:
            repo_slug = os.environ["GH_REPO_SLUG"].strip()
            review_base = os.environ["REVIEW_BASE_BRANCH"].strip()
            integration = os.environ["INTEGRATION_BRANCH"].strip()
        except KeyError as exc:
            missing = exc.args[0]
            reason = f"missing {missing} from host.env/environment; unsafe to infer release check branch"
            print(f"auto-release unsafe abort: {reason}", file=sys.stderr)
            return {"passed": False, "reason": reason, "source": "env"}
        if not repo_slug or not review_base or not integration:
            reason = "empty GH_REPO_SLUG, REVIEW_BASE_BRANCH, or INTEGRATION_BRANCH; unsafe to infer release check branch"
            print(f"auto-release unsafe abort: {reason}", file=sys.stderr)
            return {"passed": False, "reason": reason, "source": "env"}
        branches = (review_base, integration)
        print(f"check branches: {review_base}, {integration}")
        # Refactor (issue157 release gate):
        #   Old pattern: gh run list matched workflow run names, so a workflow
        #   called consensus-rnd-ci could not prove exact required check-runs.
        #   New principle: read the shared Checks API projection by exact
        #   check-run name for both release branches, fail-closed on drift.
        projection = ReleaseRequiredChecksProjection(
            runner=lambda cmd: self.runner(cmd, self.repo_root),
            now=self.now,
        )
        evidence: dict[str, Any] = {}
        red_checks: list[dict[str, str]] = []
        pending_checks: list[dict[str, str]] = []
        stale_checks: list[dict[str, str]] = []
        api_failures: list[dict[str, str]] = []
        for branch in branches:
            status = projection.check_ref(repo_slug, branch, since=since)
            evidence[branch] = status.checks
            red_checks.extend({"branch": branch, **check} for check in status.red_checks)
            pending_checks.extend({"branch": branch, "name": check} for check in status.pending_checks)
            stale_checks.extend({"branch": branch, "name": check} for check in status.stale_checks)
            if status.reason in {"api_failure", "invalid_json"}:
                api_failures.append({"branch": branch, "reason": status.reason})
        if api_failures:
            return {
                "passed": False,
                "reason": api_failures[0]["reason"],
                "api_failures": api_failures,
                "branches": evidence,
                "source": "checks-api-projection",
            }
        if red_checks:
            return {"passed": False, "reason": "ci_red", "red_checks": red_checks, "branches": evidence, "source": "checks-api-projection"}
        if pending_checks:
            return {
                "passed": False,
                "reason": "pending_required_checks",
                "pending_checks": pending_checks,
                "branches": evidence,
                "source": "checks-api-projection",
            }
        if stale_checks:
            return {
                "passed": False,
                "reason": "stale_required_checks",
                "stale_checks": stale_checks,
                "branches": evidence,
                "source": "checks-api-projection",
            }
        passed = all(all(checks.values()) for checks in evidence.values())
        reason = None if passed else "missing_required_checks_recent_green"
        return {"passed": passed, "reason": reason, "branches": evidence, "source": "checks-api-projection"}

    def no_label_on_open_prs(self, label: str) -> dict[str, Any]:
        return self.no_any_label("pr", label_catalog.query_labels_for(label))

    def no_label_on_open_items(self, label: str) -> dict[str, Any]:
        query_labels = label_catalog.query_labels_for(label)
        issue = self.no_any_label("issue", query_labels)
        pr = self.no_any_label("pr", query_labels)
        return {"passed": issue["passed"] and pr["passed"], "issue": issue, "pr": pr, "source": "gh"}

    def no_any_label(self, kind: str, query_labels: Sequence[str]) -> dict[str, Any]:
        checks = [self.no_label(kind, label) for label in query_labels]
        failed = [check for check in checks if not check["passed"]]
        passed = not failed
        reason = None if passed else failed[0]["reason"]
        return {"passed": passed, "reason": reason, "checks": checks, "labels": list(query_labels), "source": "gh"}

    def no_label(self, kind: str, label: str) -> dict[str, Any]:
        result = self.runner(
            ["gh", kind, "list", "--state", "open", "--label", label, "--json", "number", "--limit", "100"],
            self.repo_root,
        )
        if result.returncode != 0:
            return {"passed": False, "reason": f"gh {kind} list failed", "source": "gh"}
        try:
            items = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"passed": False, "reason": f"invalid gh JSON for {kind}", "source": "gh"}
        passed = len(items) == 0
        reason = None if passed else f"label_present:{label}"
        return {"passed": passed, "reason": reason, "count": len(items), "label": label, "source": "gh"}

    def no_phase8_reject_churn(self) -> dict[str, Any]:
        state = read_json(self.state_dir / "phase8-review-state.json", {})
        max_rounds = int(state.get("max_consecutive_reject_rounds", 0)) if isinstance(state, dict) else 0
        return {"passed": max_rounds < 3, "max_consecutive_reject_rounds": max_rounds, "source": "state"}

    def p0_alert_streak_ok(self, since: datetime) -> dict[str, Any]:
        state = read_json(self.repo_root / ".refactor-loop" / ".concurrency-monitor-state.json", {})
        streak = int(state.get("zero_streak", 0)) if isinstance(state, dict) else 0
        alert_log = self.repo_root / ".refactor-loop" / ".concurrency-alert.log"
        recent_lines = 0
        if alert_log.exists():
            for line in alert_log.read_text(encoding="utf-8", errors="replace").splitlines():
                timestamp = parse_time(line[1:21]) if line.startswith("[") else None
                if timestamp and timestamp >= since and "P0" in line:
                    recent_lines += 1
        return {"passed": streak <= 3 and recent_lines <= 3, "zero_streak": streak, "recent_p0_alerts": recent_lines, "source": "state"}

    def recent_pr_merges_min(self, since: datetime, minimum: int) -> dict[str, Any]:
        # Refactor (iter1/issue-145):
        #   Old pattern: merge_pr success did not write recent-pr-merges.json,
        #   so recent_pr_merges_min stayed red and blocked release.
        #   New principle: read the controller-owned post-merge projection only;
        #   the release gate does not discover merge facts from git or GitHub.
        raw = read_json(self.recent_merges_path, {})
        count = raw.get("count") if isinstance(raw, dict) else None
        if count is None and minimum <= 0:
            count = 0
        if not isinstance(count, int):
            return {
                "passed": False,
                "reason": "missing_recent_pr_merges_artifact",
                "minimum": minimum,
                "since": isoformat(since),
                "source": "state",
            }
        merges = raw.get("merges") if isinstance(raw, dict) else None
        signal: dict[str, Any] = {"passed": count >= minimum, "count": count, "minimum": minimum, "since": isoformat(since), "source": "state"}
        if isinstance(merges, list):
            signal["window_hours"] = raw.get("window_hours")
            signal["updated_at"] = raw.get("updated_at")
        return signal

    def fresh_heartbeats(self) -> dict[str, Any]:
        # Refactor (iter1/issue-154):
        #   Old pattern: release gate read phantom daemon-heartbeats.json.
        #   New principle: read real .refactor-loop/heartbeats/*.ts while
        #   preserving >=5 fresh @ HEARTBEAT_FRESH_SECONDS=90 semantics.
        now = self.now()
        fresh: dict[str, bool] = {}
        if self.heartbeat_dir.is_dir():
            for heartbeat_file in sorted(self.heartbeat_dir.glob("*.ts")):
                try:
                    raw = heartbeat_file.read_text(encoding="utf-8").strip()
                    heartbeat = datetime.fromtimestamp(int(raw), tz=timezone.utc)
                except (OSError, ValueError, OverflowError):
                    heartbeat = None
                fresh[heartbeat_file.stem] = bool(
                    heartbeat
                    and timedelta(0) <= now - heartbeat <= timedelta(seconds=HEARTBEAT_FRESH_SECONDS)
                )
        passed = sum(1 for ok in fresh.values() if ok) >= 5
        reason = None if passed else "heartbeat_stale"
        return {"passed": passed, "reason": reason, "heartbeats": fresh, "source": "heartbeats/*.ts"}

    def no_unresolved_human_escalation(self) -> dict[str, Any]:
        raw = read_json(self.state_dir / "meta-resolutions.json", {})
        unresolved = raw.get("unresolved_escalate_human", []) if isinstance(raw, dict) else []
        return {"passed": len(unresolved) == 0, "count": len(unresolved), "source": "state"}

    def latest_release_time(self) -> datetime | None:
        history = read_json(self.state_dir / "release-history.json", {})
        return parse_time(history.get("latest_release_at")) if isinstance(history, dict) else None

    def commits_since_latest_release(self) -> list[CommitInfo]:
        raw = read_json(self.release_commits_path, {})
        raw_commits = raw.get("commits") if isinstance(raw, dict) else None
        if not isinstance(raw_commits, list):
            return []
        commits: list[CommitInfo] = []
        for item in raw_commits:
            if not isinstance(item, dict):
                continue
            sha = item.get("sha")
            subject = item.get("subject")
            body = item.get("body", "")
            if not isinstance(sha, str) or not isinstance(subject, str) or not isinstance(body, str):
                continue
            commits.append(CommitInfo(sha=sha, subject=subject, body=body))
        return commits

    def decide_release(self, stability: StabilityResult, min_interval_hours: int) -> dict[str, Any]:
        now = self.now()
        from_version = self.current_version()
        interval = self.release_interval_status(now, min_interval_hours)
        commits = self.commits_since_latest_release()
        candidate_bump = classify_bump(commits) if commits else None
        release_ready = stability.ready and interval["passed"] and bool(commits)
        bump_type = candidate_bump if release_ready else None
        to_version = bump_semver(from_version, bump_type) if bump_type else from_version
        return {
            "from_version": from_version,
            "to_version": to_version,
            "bump_type": bump_type,
            "commits": [{"sha": commit.sha, "subject": commit.subject} for commit in commits],
            "decided_at": isoformat(now),
            "stability_score": stability.score,
            "signals": stability.signals,
            "ready": release_ready,
            "blocked_reasons": blocked_reasons(stability, interval, bool(commits)),
            "release_interval": interval,
        }

    def release_interval_status(self, now: datetime, minimum_hours: int) -> dict[str, Any]:
        latest = self.latest_release_time()
        if latest is None:
            return {"passed": True, "minimum_hours": minimum_hours, "last_release_at": None}
        elapsed = now - latest
        return {
            "passed": elapsed > timedelta(hours=minimum_hours),
            "minimum_hours": minimum_hours,
            "last_release_at": isoformat(latest),
            "elapsed_seconds": int(elapsed.total_seconds()),
        }

    def dispatch_release(self, decision: dict[str, Any]) -> None:
        if not decision.get("ready"):
            raise RuntimeError("release decision is not ready")
        write_json(self.decision_path, decision)
        write_json(self.candidate_path, self.release_candidate(decision))

    def release_candidate(self, decision: dict[str, Any]) -> dict[str, Any]:
        # Refactor (iter217/issue-217):
        #   Old pattern: release.yml 保留 tag/release mutation,无法可靠读本地 runtime fact,绕过 release-gate decider-only 边界
        #   New principle: controller-only publication:新增 ReleasePublishPreflight+ReleasePublisher 替代 workflow 发布权;release.yml 降为 read-only preview(contents:read,禁 gh release create)。严格按 plan 'Concrete plan' 逐条改。
        now = self.now()
        required_signals = {
            name: {
                "passed": bool(signal.get("passed")) if isinstance(signal, dict) else False,
                "reason": signal.get("reason") if isinstance(signal, dict) else "invalid_signal",
                **({"branches": signal["branches"]} if isinstance(signal, dict) and "branches" in signal else {}),
                **({"checks": signal["checks"]} if isinstance(signal, dict) and "checks" in signal else {}),
            }
            for name, signal in (decision.get("signals") if isinstance(decision.get("signals"), dict) else {}).items()
        }
        return {
            "schema": "decision-artifact-only/v2",
            "generated_at": isoformat(now),
            "expires_at": isoformat(now + timedelta(minutes=120)),
            "decision_artifact": str(self.decision_path.relative_to(self.repo_root)),
            "from_version": decision.get("from_version"),
            "to_version": decision.get("to_version"),
            "bump_type": decision.get("bump_type"),
            "ready": decision.get("ready"),
            "target_ref": os.environ.get("RELEASE_TARGET_REF", ""),
            "required_signals": required_signals,
            "decision_digest": canonical_digest(decision),
            "publish_preflight": "controller-release-publish-preflight",
            "host_opt_in": "RELEASE_AUTO_ENABLE=true",
            "lifecycle_owner": "controller",
            "next_step_hint": (
                "Controller may consume this artifact only through ReleasePublishPreflight, re-check "
                "host opt-in, target ref, manifests, decision digest, and required checks, then run "
                "controller-owned release publication. consensus-rnd-cli release-gate does not bump, "
                "commit, push, tag, publish, merge, or close."
            ),
        }


def resolve_field(data: Any, field: str) -> Any:
    current = data
    for part in field.split("."):
        if isinstance(current, list):
            if not part.isdigit():
                raise KeyError(f"expected list index at {part!r} in {field}")
            current = current[int(part)]
            continue
        if not isinstance(current, dict):
            raise KeyError(f"cannot resolve {part!r} in {field}")
        current = current[part]
    return current


def parse_semver(version: Any) -> tuple[int, int, int]:
    if not isinstance(version, str):
        raise ValueError(f"invalid semver: {version}")
    match = SEMVER_RE.match(version)
    if not match:
        raise ValueError(f"invalid semver: {version}")
    return tuple(int(part) for part in match.group(1, 2, 3))


def bump_semver(version: str, bump_type: str) -> str:
    # Bumps apply to the core version and intentionally drop pre-release/build metadata.
    major, minor, patch = parse_semver(version)
    if bump_type == "major":
        return f"{major + 1}.0.0"
    if bump_type == "minor":
        return f"{major}.{minor + 1}.0"
    if bump_type == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"invalid bump type: {bump_type}")


def classify_bump(commits: list[CommitInfo]) -> str:
    highest = "patch"
    for commit in commits:
        subject = commit.subject
        body = commit.body
        if subject.startswith("feat!:") or "BREAKING CHANGE:" in body:
            return "major"
        if subject.startswith("feat:") and highest != "major":
            highest = "minor"
        elif subject.startswith(("fix:", "perf:", "refactor:")) and highest not in ("major", "minor"):
            highest = "patch"
        elif highest not in ("major", "minor"):
            highest = "patch"
    return highest


def blocked_reasons(stability: StabilityResult, interval: dict[str, Any], has_commits: bool) -> list[str]:
    reasons = [name for name, value in stability.signals.items() if not value["passed"]]
    if not interval["passed"]:
        reasons.append("min_interval")
    if not has_commits:
        reasons.append("no_commits_since_last_release")
    return reasons


def canonical_digest(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def print_summary(decision: dict[str, Any]) -> None:
    status = "ready" if decision.get("ready") else "blocked"
    print(
        f"auto-release decision: {status} "
        f"score={decision['stability_score']} "
        f"bump={decision['bump_type']} "
        f"{decision['from_version']}->{decision['to_version']}"
    )
    if decision.get("blocked_reasons"):
        print("blocked_reasons=" + ",".join(decision["blocked_reasons"]))


def decide_release_artifact(repo_root: Path, *, min_recent_merges: int = 1, min_interval_hours: int = 2) -> dict[str, Any]:
    gate = AutoReleaseGate(repo_root)
    stability = gate.compute_stability(min_recent_merges=min_recent_merges)
    return gate.decide_release(stability, min_interval_hours)


def score_release_gate(repo_root: Path, *, min_recent_merges: int = 1) -> StabilityResult:
    return AutoReleaseGate(repo_root).compute_stability(min_recent_merges=min_recent_merges)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dispatch", action="store_true", help="write decision and release-candidate artifacts only")
    parser.add_argument("--score-only", action="store_true", help="compute stability score only")
    parser.add_argument("--min-recent-merges", type=int, default=int(os.environ.get("RELEASE_AUTO_MIN_MERGES", "1")))
    parser.add_argument("--min-interval-hours", type=int, default=int(os.environ.get("RELEASE_AUTO_MIN_INTERVAL_HOURS", "2")))
    args = parser.parse_args(argv)

    try:
        repo_root = repo_root_from_env()
        gate = AutoReleaseGate(repo_root)
        host_env = inject_host_env(repo_root)
        if not args.score_only and host_env.get("RELEASE_AUTO_ENABLE") != "true":
            print("auto-release noop: RELEASE_AUTO_ENABLE is not true in host.env")
            return 0

        stability = gate.compute_stability(min_recent_merges=args.min_recent_merges)
        if args.score_only:
            print(json.dumps({"stability": {"ready": stability.ready, "score": stability.score, "signals": stability.signals}}, ensure_ascii=False, indent=2))
            return 0

        decision = gate.decide_release(stability, args.min_interval_hours)
        print_summary(decision)
        if not decision.get("ready"):
            return 0
        if args.dispatch:
            gate.dispatch_release(decision)
            print(
                "auto-release dispatch artifact written: "
                f"{gate.candidate_path.relative_to(repo_root)}; "
                "controller-owned release publisher owns bump/commit/push/tag/release after preflight"
            )
        else:
            write_json(gate.decision_path, decision)
    except Exception as exc:
        print(f"release-gate: {exc}", file=sys.stderr)
        return 1
    return 0


__all__ = [
    "AutoReleaseGate",
    "CommitInfo",
    "DAEMON_NAMES",
    "HEARTBEAT_FRESH_SECONDS",
    "REQUIRED_CHECKS",
    "SIGNAL_NAMES",
    "StabilityResult",
    "blocked_reasons",
    "bump_semver",
    "canonical_digest",
    "classify_bump",
    "decide_release_artifact",
    "inject_host_env",
    "isoformat",
    "load_host_env",
    "main",
    "parse_semver",
    "parse_time",
    "repo_root_from_env",
    "resolve_field",
    "score_release_gate",
]
