"""Host-opt-in patrol inspector daemon."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import labels as label_catalog
from .active_controller import require_active_controller, write_active_controller_status
from .context import LoopContext, LoopContextError
from .heartbeat import DaemonHeartbeatLease
from .patrol_issue_publisher import PatrolIssuePublisher
from .wakeup_plan import GhItem, load_github_items_with_status


DEFAULT_INTERVAL_SECONDS = 7200
DEFAULT_MAX_FINDINGS = 25
STATE_FILE_NAME = "patrol-inspector.json"
PATROL_DAEMON_NAME = "patrol_inspector_daemon"


@dataclass(frozen=True)
class PatrolFinding:
    kind: str
    source: str
    summary: str
    severity: str
    evidence: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        payload = {
            "kind": self.kind,
            "source": self.source,
            "summary": self.summary,
            "severity": self.severity,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]

    def to_json(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "source": self.source,
            "summary": self.summary,
            "severity": self.severity,
            "fingerprint": self.fingerprint,
            "evidence": list(self.evidence),
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
        github_items: Iterable[GhItem | Mapping[str, object]] | None = None,
    ) -> None:
        self.ctx = ctx
        self.config = config or PatrolInspectorConfig.from_context(ctx)
        self.publisher = publisher or PatrolIssuePublisher(ctx)
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
        findings.extend(_find_log_exceptions(self.ctx.paths.logs))
        findings.extend(_find_runtime_artifact_gaps(self.ctx.paths.runs))
        findings.extend(_find_projection_gaps(self.ctx.paths.state))
        findings.extend(_find_managed_snapshot_gaps(self._load_github_items_or_fail()))
        deduped: dict[str, PatrolFinding] = {}
        for finding in findings:
            deduped.setdefault(finding.fingerprint, finding)
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
    evidence = "\n".join(f"- `{line}`" for line in finding.evidence[:10]) or "- no local evidence lines"
    return (
        "## Patrol Finding\n"
        f"- Kind: `{finding.kind}`\n"
        f"- Source: `{finding.source}`\n"
        f"- Severity: `{finding.severity}`\n"
        f"- Summary: {finding.summary}\n"
        "\n"
        "## Evidence\n"
        f"{evidence}\n"
        "\n"
        "## Boundary\n"
        "This issue is patrol-owned intake. The patrol inspector only reported local runtime evidence; normal design consensus remains the implementation gate.\n"
    )


def _find_log_exceptions(log_dir: Path) -> tuple[PatrolFinding, ...]:
    findings = []
    for path in sorted(log_dir.glob("*.log"))[-200:]:
        lines = _read_tail_or_fail(path, 80)
        matched = _exception_signal_lines(lines)
        if not matched:
            continue
        findings.append(
            PatrolFinding(
                kind="exception-log",
                source=_repo_local_source(path),
                summary=f"runtime log reports exception signals in {path.name}",
                severity="high",
                evidence=tuple(matched[-10:]),
            )
        )
    return tuple(findings)


def _find_runtime_artifact_gaps(runs_dir: Path) -> tuple[PatrolFinding, ...]:
    findings = []
    for path in sorted(runs_dir.glob("*.md"))[-200:]:
        text = _read_text_or_fail(path)
        if not text:
            continue
        if "IMPLEMENT_DONE:" in text and "⟦AI:AUTO-LOOP⟧" not in text:
            findings.append(
                PatrolFinding(
                    kind="runtime-artifact",
                    source=_repo_local_source(path),
                    summary=f"implementation artifact is missing AI sentinel in {path.name}",
                    severity="medium",
                    evidence=("IMPLEMENT_DONE artifact without sentinel",),
                )
            )
    return tuple(findings)


def _find_projection_gaps(state_dir: Path) -> tuple[PatrolFinding, ...]:
    findings = []
    for name in ("wakeup-plan.json", "peek.json"):
        path = state_dir / name
        if not path.exists():
            continue
        data = _json_or_fail(path)
        if isinstance(data, dict) and data.get("status") in {"error", "failed", "blocked"}:
            findings.append(
                PatrolFinding(
                    kind="projection",
                    source=_repo_local_source(path),
                    summary=f"{name} reports {data.get('status')}",
                    severity="medium",
                    evidence=(json.dumps(data, sort_keys=True)[:500],),
                )
            )
    return tuple(findings)


def _find_managed_snapshot_gaps(items: Iterable[GhItem | Mapping[str, object]]) -> tuple[PatrolFinding, ...]:
    missing_phase: list[str] = []
    for item in items:
        labels = _item_labels(item)
        if label_catalog.MANAGED in labels and not any(label in label_catalog.labels_for_group("phase") for label in labels):
            missing_phase.append(_item_ref(item))
    if not missing_phase:
        return ()
    return (
        PatrolFinding(
            kind="managed-snapshot",
            source="github-managed-item-snapshot",
            summary="open managed items are missing phase labels",
            severity="medium",
            evidence=tuple(missing_phase[:10]),
        ),
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


def _line_has_exception_signal(line: str) -> bool:
    lowered = line.lower()
    return any(token in lowered for token in ("traceback", "exception", "runtimeerror", "fatal:", "failed"))


def _exception_signal_lines(lines: Sequence[str]) -> tuple[str, ...]:
    if _tail_has_clean_exit(lines):
        return tuple(line for line in lines if _line_is_worker_self_post_failure(line))
    return tuple(line for line in lines if _line_has_exception_signal(line))


def _tail_has_clean_exit(lines: Sequence[str]) -> bool:
    return any(line.strip() == "EXIT=0" for line in lines)


def _line_is_worker_self_post_failure(line: str) -> bool:
    return line.strip().startswith("POST_FAILED:")


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


def _patrol_daemon_heartbeat_lease(ctx: LoopContext) -> DaemonHeartbeatLease:
    return DaemonHeartbeatLease(PATROL_DAEMON_NAME, ctx.repo_root)


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
        inspector.run_once(beat=lease.beat)
        lease.sleep_with_lease(config.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
