"""Host-opt-in patrol inspector daemon."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import labels as label_catalog
from .active_controller import require_active_controller, write_active_controller_status
from .context import LoopContext, LoopContextError
from .heartbeat import DaemonHeartbeatLease
from .patrol_analysis import CodexPatrolAnalysisProvider, PatrolAnalysisDecision, PatrolCandidateSignal
from .patrol_issue_publisher import PatrolIssuePublisher
from .wakeup_plan import GhItem, load_github_items_with_status


DEFAULT_INTERVAL_SECONDS = 7200
DEFAULT_MAX_FINDINGS = 25
STATE_FILE_NAME = "patrol-inspector.json"
PATROL_DAEMON_NAME = "patrol_inspector_daemon"
EXIT_STATUS_LINE_RE = re.compile(r"^EXIT=(\d+)$")
EXITED_STATUS_RE = re.compile(r"\bexited\s+(\d+)\b", re.IGNORECASE)


@dataclass(frozen=True)
class PatrolFinding:
    kind: str
    source: str
    summary: str
    severity: str
    root_cause: str
    recommendation: str
    rationale: str

    @property
    def fingerprint(self) -> str:
        payload = {
            "kind": self.kind,
            "source": self.source,
            "summary": self.summary,
            "severity": self.severity,
            "root_cause": self.root_cause,
            "recommendation": self.recommendation,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]

    def to_json(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "source": self.source,
            "summary": self.summary,
            "severity": self.severity,
            "fingerprint": self.fingerprint,
            "root_cause": self.root_cause,
            "recommendation": self.recommendation,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class PatrolInspectorConfig:
    enabled: bool
    interval_seconds: int
    max_findings: int

    @classmethod
    def from_context(cls, ctx: LoopContext) -> "PatrolInspectorConfig":
        env = ctx.env_for_subprocess()
        return cls(
            enabled=str(env.get("PATROL_INSPECTOR_ENABLE", "")).lower() == "true",
            interval_seconds=_positive_int(env.get("PATROL_INSPECTOR_INTERVAL_SECONDS"), DEFAULT_INTERVAL_SECONDS),
            max_findings=_positive_int(env.get("PATROL_INSPECTOR_MAX_FINDINGS"), DEFAULT_MAX_FINDINGS),
        )


class PatrolInspector:
    def __init__(
        self,
        ctx: LoopContext,
        *,
        config: PatrolInspectorConfig | None = None,
        publisher: PatrolIssuePublisher | None = None,
        analysis_provider: CodexPatrolAnalysisProvider | None = None,
        github_items: Iterable[GhItem | Mapping[str, object]] | None = None,
    ) -> None:
        self.ctx = ctx
        self.config = config or PatrolInspectorConfig.from_context(ctx)
        self.publisher = publisher or PatrolIssuePublisher(ctx)
        self.analysis_provider = analysis_provider or CodexPatrolAnalysisProvider(ctx)
        self.github_items = tuple(github_items) if github_items is not None else None

    def run_once(self, beat=None) -> int:
        self.ctx.paths.state.mkdir(parents=True, exist_ok=True)
        if not self.config.enabled:
            self._write_state(status="disabled", findings=(), published=(), reason="PATROL_INSPECTOR_ENABLE is not true")
            print("patrol-inspector noop: PATROL_INSPECTOR_ENABLE is not true")
            return 0
        decision = require_active_controller(self.ctx, "patrol-inspector")
        write_active_controller_status(self.ctx, decision)
        if not decision.allowed:
            self._write_state(status="noop:not-owner", findings=(), published=(), reason=decision.status)
            print(f"patrol-inspector noop: active-controller {decision.status} owner={decision.owner_device}")
            return 0
        try:
            findings = self.collect_findings()
        except RuntimeError as exc:
            reason = str(exc)
            self._write_state(status="failed", findings=(), published=(), reason=reason)
            print(f"patrol-inspector failed: {reason}", file=sys.stderr)
            if beat is not None:
                beat()
                return 0
            raise
        if beat is not None:
            beat()
        published = []
        for finding in findings[: self.config.max_findings]:
            body = render_issue_body(finding)
            issue = self.publisher.publish(
                fingerprint=finding.fingerprint,
                title=render_issue_title(finding),
                body=body,
            )
            published.append({"fingerprint": finding.fingerprint, "issue": issue.number})
            if beat is not None:
                beat()
        self._write_state(status="ok", findings=findings, published=tuple(published), reason="")
        print(f"patrol-inspector ok: findings={len(findings)} published={len(published)}")
        return 0

    def collect_findings(self) -> tuple[PatrolFinding, ...]:
        findings: list[PatrolFinding] = []
        for signal in self.collect_candidate_signals():
            try:
                decision = self.analysis_provider.analyze(signal)
            except RuntimeError as exc:
                print(
                    f"patrol-inspector analysis skipped: source={_single_line(signal.source)} reason={_single_line(str(exc))}",
                    file=sys.stderr,
                )
                continue
            if not decision.is_real_issue:
                continue
            findings.append(_finding_from_decision(signal, decision))
        deduped: dict[str, PatrolFinding] = {}
        for finding in findings:
            deduped.setdefault(finding.fingerprint, finding)
        return tuple(deduped.values())[: self.config.max_findings]

    def collect_candidate_signals(self) -> tuple[PatrolCandidateSignal, ...]:
        signals: list[PatrolCandidateSignal] = []
        signals.extend(_find_log_exceptions(self.ctx.paths.logs))
        signals.extend(_find_runtime_artifact_gaps(self.ctx.paths.runs))
        signals.extend(_find_projection_gaps(self.ctx.paths.state))
        signals.extend(_find_managed_snapshot_gaps(self._load_github_items_or_fail()))
        deduped: dict[str, PatrolCandidateSignal] = {}
        for signal in signals:
            key = json.dumps(
                {
                    "kind": signal.kind,
                    "source": signal.source,
                    "summary": signal.summary,
                    "severity": signal.severity,
                    "evidence": list(signal.evidence),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            deduped.setdefault(hashlib.sha256(key.encode("utf-8")).hexdigest(), signal)
        return tuple(deduped.values())[: self.config.max_findings]

    def _load_github_items_or_fail(self) -> tuple[GhItem | Mapping[str, object], ...]:
        if self.github_items is not None:
            return self.github_items
        try:
            items, loaded_ok = load_github_items_with_status(self.ctx.repo_root)
        except Exception as exc:
            raise RuntimeError(f"patrol managed snapshot load failed: repo={self.ctx.repo_root} reason={exc}") from exc
        if not loaded_ok:
            raise RuntimeError(f"patrol managed snapshot load failed: repo={self.ctx.repo_root} reason=loaded_ok_false")
        return tuple(items)

    def _write_state(
        self,
        *,
        status: str,
        findings: Sequence[PatrolFinding],
        published: Sequence[Mapping[str, object]],
        reason: str,
    ) -> None:
        payload = {
            "status": status,
            "reason": reason,
            "generated_at": _utc_now(),
            "findings": [finding.to_json() for finding in findings],
            "published": list(published),
        }
        path = self.ctx.paths.state / STATE_FILE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render_issue_title(finding: PatrolFinding) -> str:
    return f"Patrol finding: {finding.summary} [{finding.fingerprint}]"


def render_issue_body(finding: PatrolFinding) -> str:
    return (
        "## Patrol Finding\n"
        f"- Kind: `{finding.kind}`\n"
        f"- Source: `{finding.source}`\n"
        f"- Severity: `{finding.severity}`\n"
        f"- Summary: {finding.summary}\n"
        "\n"
        "## Analysis\n"
        f"- Root cause: {finding.root_cause}\n"
        f"- Recommendation: {finding.recommendation}\n"
        f"- Rationale: {finding.rationale}\n"
        "\n"
        "## Boundary\n"
        "This issue is patrol-owned intake. The patrol inspector publishes only structured analysis; raw local logs remain diagnostic context for the analysis gate.\n"
    )


def _find_log_exceptions(log_dir: Path) -> tuple[PatrolCandidateSignal, ...]:
    signals = []
    for path in sorted(log_dir.glob("*.log"))[-200:]:
        lines = _read_tail_or_fail(path, 80)
        signal = _log_failure_signal(lines)
        if signal is None:
            continue
        evidence = signal.evidence
        if not evidence:
            continue
        signals.append(
            PatrolCandidateSignal(
                kind="exception-log",
                source=_repo_local_source(path),
                summary=f"{signal.summary} in {path.name}",
                severity="high",
                evidence=evidence[-10:],
            )
        )
    return tuple(signals)


def _find_runtime_artifact_gaps(runs_dir: Path) -> tuple[PatrolCandidateSignal, ...]:
    signals = []
    for path in sorted(runs_dir.glob("*.md"))[-200:]:
        text = _read_text_or_fail(path)
        if not text:
            continue
        if "IMPLEMENT_DONE:" in text and "⟦AI:AUTO-LOOP⟧" not in text:
            signals.append(
                PatrolCandidateSignal(
                    kind="runtime-artifact",
                    source=_repo_local_source(path),
                    summary=f"implementation artifact is missing AI sentinel in {path.name}",
                    severity="medium",
                    evidence=("IMPLEMENT_DONE artifact without sentinel",),
                )
            )
    return tuple(signals)


def _find_projection_gaps(state_dir: Path) -> tuple[PatrolCandidateSignal, ...]:
    signals = []
    for name in ("wakeup-plan.json", "peek.json"):
        path = state_dir / name
        if not path.exists():
            continue
        data = _json_or_fail(path)
        if isinstance(data, dict) and data.get("status") in {"error", "failed", "blocked"}:
            signals.append(
                PatrolCandidateSignal(
                    kind="projection",
                    source=_repo_local_source(path),
                    summary=f"{name} reports {data.get('status')}",
                    severity="medium",
                    evidence=(json.dumps(data, sort_keys=True)[:500],),
                )
            )
    return tuple(signals)


def _find_managed_snapshot_gaps(items: Iterable[GhItem | Mapping[str, object]]) -> tuple[PatrolCandidateSignal, ...]:
    missing_phase: list[str] = []
    for item in items:
        labels = _item_labels(item)
        if label_catalog.MANAGED in labels and not any(label in label_catalog.labels_for_group("phase") for label in labels):
            missing_phase.append(_item_ref(item))
    if not missing_phase:
        return ()
    return (
        PatrolCandidateSignal(
            kind="managed-snapshot",
            source="github-managed-item-snapshot",
            summary="open managed items are missing phase labels",
            severity="medium",
            evidence=tuple(missing_phase[:10]),
        ),
    )


def _finding_from_decision(signal: PatrolCandidateSignal, decision: PatrolAnalysisDecision) -> PatrolFinding:
    return PatrolFinding(
        kind=signal.kind,
        source=signal.source,
        summary=decision.summary,
        severity=decision.severity,
        root_cause=decision.root_cause,
        recommendation=decision.recommendation,
        rationale=decision.rationale,
    )


def _item_labels(item: GhItem | Mapping[str, object]) -> tuple[str, ...]:
    labels = getattr(item, "labels", None) if not isinstance(item, Mapping) else item.get("labels")
    if not isinstance(labels, (list, tuple)):
        return ()
    return tuple(str(label) for label in labels)


def _item_ref(item: GhItem | Mapping[str, object]) -> str:
    kind = getattr(item, "kind", None) if not isinstance(item, Mapping) else item.get("kind")
    number = getattr(item, "number", None) if not isinstance(item, Mapping) else item.get("number")
    return f"{kind or 'item'} #{number or '?'}"


@dataclass(frozen=True)
class TerminalFailureSignal:
    summary: str
    evidence: tuple[str, ...]


def _log_failure_signal(lines: Sequence[str]) -> TerminalFailureSignal | None:
    exit_signal = _terminal_failure_signal(lines)
    if exit_signal is not None:
        return exit_signal
    if _has_exit_marker(lines):
        return None
    evidence = _extract_log_diagnostic_evidence(lines)
    if not evidence:
        return None
    return TerminalFailureSignal(summary="runtime log reports exception signals", evidence=evidence)


def _terminal_failure_signal(lines: Sequence[str]) -> TerminalFailureSignal | None:
    for index in range(len(lines) - 1, -1, -1):
        exit_code = _parse_exit_line(lines[index])
        if exit_code is None:
            continue
        if exit_code == 0:
            evidence = _clean_exit_failure_evidence(lines)
            if not evidence:
                return None
            summary = "worker log self-post failure after clean EXIT=0"
            if any(not _line_is_worker_self_post_failure(line) for line in evidence):
                summary = "worker log structured failure signal after clean EXIT=0"
            return TerminalFailureSignal(summary=summary, evidence=evidence)
        evidence = _terminal_failure_evidence(lines, index)
        return TerminalFailureSignal(summary=f"worker log terminal failure EXIT={exit_code}", evidence=evidence)
    return None


def _has_exit_marker(lines: Sequence[str]) -> bool:
    return any(_parse_exit_line(line) is not None for line in lines)


def _parse_exit_line(line: str) -> int | None:
    stripped = line.strip()
    if not stripped.startswith("EXIT="):
        return None
    raw_code = stripped.removeprefix("EXIT=").strip()
    if not raw_code.isdigit():
        return None
    return int(raw_code)


def _terminal_failure_evidence(lines: Sequence[str], exit_index: int) -> tuple[str, ...]:
    start = max(0, exit_index - 9)
    window = [line for line in lines[start : exit_index + 1] if line.strip()]
    structured_failure = [
        line
        for line in window
        if line.strip().startswith(("SPAWN_FAILED=", "TIMEOUT_KILL_AFTER=", "STALL_KILL_AFTER="))
    ]
    if structured_failure:
        return tuple(structured_failure + [lines[exit_index]])
    return tuple(window)


def _clean_exit_failure_evidence(lines: Sequence[str]) -> tuple[str, ...]:
    evidence: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if _line_is_worker_self_post_failure(line):
            evidence.append(line)
        elif line == "Traceback (most recent call last):":
            block, next_index = _extract_traceback_block(lines, index)
            if block:
                evidence.extend(block)
            index = next_index
            continue
        elif _is_python_exception_line(line):
            evidence.append(line)
        index += 1
    return tuple(evidence)


def _extract_log_diagnostic_evidence(lines: Sequence[str]) -> tuple[str, ...]:
    evidence: list[str] = []
    final_exit_status = _tail_final_exit_status(lines)
    has_clean_final_exit = final_exit_status == 0
    index = 0
    while index < len(lines):
        line = lines[index]
        if line == "Traceback (most recent call last):":
            block, next_index = _extract_traceback_block(lines, index)
            if block:
                evidence.extend(block)
            index = next_index
            continue
        if _is_exit_failure_line(line) and not evidence:
            evidence.append(line)
        elif _is_single_line_diagnostic(line) or _is_python_exception_line(line):
            evidence.append(line)
        elif not has_clean_final_exit and _is_command_failure_summary(line):
            evidence.append(line)
        index += 1
    return tuple(evidence)


def _extract_traceback_block(lines: Sequence[str], start: int) -> tuple[tuple[str, ...], int]:
    block = [lines[start]]
    index = start + 1
    while index < len(lines):
        line = lines[index]
        block.append(line)
        index += 1
        if _is_python_exception_line(line):
            return tuple(block), index
    return (), index


def _is_single_line_diagnostic(line: str) -> bool:
    return line.startswith(("FATAL:", "POST_FAILED:", "RuntimeError:"))


def _is_command_failure_summary(line: str) -> bool:
    lowered = line.lower()
    return lowered.startswith(("command failed:", "command failure:", "cmd failed:"))


def _is_exit_failure_line(line: str) -> bool:
    status = _line_exit_status(line)
    return status is not None and status != 0


def _tail_final_exit_status(lines: Sequence[str]) -> int | None:
    for line in reversed(lines):
        status = _line_exit_status(line)
        if status is not None:
            return status
    return None


def _line_exit_status(line: str) -> int | None:
    stripped = line.strip()
    exit_match = EXIT_STATUS_LINE_RE.fullmatch(stripped)
    if exit_match is not None:
        return int(exit_match.group(1))
    exited_match = EXITED_STATUS_RE.search(stripped)
    if exited_match is not None:
        return int(exited_match.group(1))
    return None


def _is_python_exception_line(line: str) -> bool:
    if line.startswith((" ", "\t")):
        return False
    exception_type = line.split(":", 1)[0].strip()
    return exception_type.endswith(("Error", "Exception")) or exception_type in {"KeyboardInterrupt", "SystemExit"}


def _line_is_worker_self_post_failure(line: str) -> bool:
    return line.lstrip() == line and line.startswith("POST_FAILED:")


def _read_tail_or_fail(path: Path, max_lines: int) -> tuple[str, ...]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise RuntimeError(f"patrol input read failed: source={_repo_local_source(path)} reason={exc}") from exc
    return tuple(lines[-max_lines:])


def _read_text_or_fail(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"patrol input read failed: source={_repo_local_source(path)} reason={exc}") from exc


def _json_or_fail(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"patrol input read failed: source={_repo_local_source(path)} reason={exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"patrol input JSON malformed: source={_repo_local_source(path)} reason={exc}") from exc


def _repo_local_source(path: Path) -> str:
    parts = path.parts
    if ".refactor-loop" in parts:
        return Path(*parts[parts.index(".refactor-loop") :]).as_posix()
    return path.name


def _positive_int(raw: str | None, default: int) -> int:
    try:
        value = int(str(raw or ""))
    except ValueError:
        return default
    return value if value > 0 else default


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _single_line(value: str) -> str:
    return " ".join(str(value).split())[:240]


def _patrol_daemon_heartbeat_lease(ctx: LoopContext) -> DaemonHeartbeatLease:
    return DaemonHeartbeatLease(PATROL_DAEMON_NAME, ctx.repo_root)


def _run_daemon_tick(inspector: PatrolInspector, lease: DaemonHeartbeatLease, interval_seconds: int) -> None:
    try:
        inspector.run_once(beat=lease.beat)
    except Exception as exc:
        print(f"patrol-inspector daemon tick failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        lease.beat()
    lease.sleep_with_lease(interval_seconds)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="run the patrol inspector")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--daemon", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=None)
    args = parser.parse_args(argv)
    try:
        ctx = LoopContext.load(cwd=os.getcwd())
    except LoopContextError as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 2
    config = PatrolInspectorConfig.from_context(ctx)
    if args.interval_seconds is not None:
        config = PatrolInspectorConfig(config.enabled, max(1, args.interval_seconds), config.max_findings)
    inspector = PatrolInspector(ctx, config=config)
    if not args.daemon:
        return inspector.run_once()
    lease = _patrol_daemon_heartbeat_lease(ctx)
    while True:
        _run_daemon_tick(inspector, lease, config.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
