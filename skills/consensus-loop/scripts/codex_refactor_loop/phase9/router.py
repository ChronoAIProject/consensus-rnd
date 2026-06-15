#!/usr/bin/env python3
"""Narrow design-consensus deterministic router daemon.

This daemon owns only five design-consensus direct-dispatch routes:
design-consensus issue intake -> r1 solver triplet, solver triplet ->
meta-judge, converge -> next solver triplet, valid stalled -> reflector,
and re-design reflector decisions -> next solver triplet. Every other marker
is forwarded to the existing controller pending-event file without spawning.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, cast

from ..active_controller import require_active_controller, write_active_controller_status
from ..context import LoopContext
from ..github_budget import graphql_headroom_ok
from ..heartbeat import DaemonHeartbeatLease
from ..managed_work_snapshot import ManagedWorkSnapshotItem
from ..managed_work_snapshot import load_open_managed_work_snapshot
from ..prompt_rendering import render_prompt_text
from ..transition_assessment import TransitionAssessment, TransitionAssessmentReader, projection_lines
from ..work_items import design_consensus_terminal_source
from ..worker_markers import log_has_clean_exit, read_worker_terminal_marker
from ..workflow_stages import format_stage
from .. import labels as label_catalog


ROLES = ("minimal", "structural", "delete")
JUDGE_ROLE = "judge"
PHASE9_STALLED_ROUND_CEILING = 8
LIFECYCLE_PREFIXES = (
    "META_JUDGE_DONE:consensus",
    "IMPLEMENT_DONE",
    "VERIFY_DONE",
    "REVIEW_DONE",
    "FIX_DONE",
    "FIX_BLOCKED",
    "TEST_ADD_DONE",
    "META_RESOLVED",
)


class Phase9MarkerGrammar:
    ROUTE_TOKEN = re.compile(r"^[A-Za-z0-9_./-]+$")
    VERDICT_TOKEN = re.compile(r"^[A-Za-z0-9_./-]+$")
    CONVERGE_RE = re.compile(r"^META_JUDGE_DONE:converge:round-(\d+)(?::.*)?$")

    @classmethod
    def parse_marker_candidate(cls, text: str) -> str | None:
        if cls.is_solver_done(text) or cls.parse_converge_round(text) is not None or cls.is_stalled_marker(text):
            return text
        if cls._is_lifecycle_or_unknown_marker(text):
            return text
        return None

    @classmethod
    def is_solver_done(cls, marker: str) -> bool:
        parts = marker.split(":", 3)
        return (
            len(parts) >= 3
            and parts[0] == "SOLVER_DONE"
            and bool(cls.ROUTE_TOKEN.match(parts[1]))
            and bool(cls.VERDICT_TOKEN.match(parts[2]))
        )

    @classmethod
    def parse_converge_round(cls, marker: str) -> int | None:
        match = cls.CONVERGE_RE.match(marker)
        return int(match.group(1)) if match else None

    @classmethod
    def is_stalled_marker(cls, marker: str) -> bool:
        return marker.startswith("META_JUDGE_DONE:escalate:stalled:")

    @classmethod
    def _is_lifecycle_or_unknown_marker(cls, marker: str) -> bool:
        parts = marker.split(":")
        if len(parts) < 2:
            return False
        if not re.match(r"^[A-Z][A-Z0-9_]*(?:_DONE|_RESOLVED|_BLOCKED)?$", parts[0]):
            return False
        return all(cls.ROUTE_TOKEN.match(part) for part in parts[1:])


@dataclass(frozen=True)
class Marker:
    marker: str
    log_path: Path
    issue: str
    round: int
    role: str | None = None


@dataclass(frozen=True)
class Phase9LogIdentity:
    issue: str
    round: int
    actor: Literal["minimal", "structural", "delete", "judge", "reflector"]
    dialect: Literal["phase9", "solver", "meta-judge"]


@dataclass(frozen=True)
class Phase9SourceIssueDecision:
    allowed: bool
    state: str | None
    reason: Literal["phase9-source-open", "phase9-source-not-open", "phase9-source-state-unavailable"]


@dataclass(frozen=True)
class IssueSourceSnapshot:
    number: str
    title: str
    body: str
    comments: tuple[dict[str, str], ...]
    read_at: str
    source: str
    updated_at: str
    truncated: bool
    unavailable_reason: str | None = None
    comments_loaded: bool = False


@dataclass(frozen=True)
class Phase9TerminalDecision:
    allowed: bool
    reason: str
    terminal_source: str | None = None


@dataclass(frozen=True)
class Phase9ActorHealth:
    issue: str
    round: int
    actor: str
    key: str
    log_path: Path
    ledgered: bool
    target_log_exists: bool
    equivalent_log_exists: bool
    pending_intent: bool
    source_allowed: bool
    terminal_allowed: bool
    valid_marker: str | None = None
    markerless_clean_log: Path | None = None
    dispatched_at: datetime | None = None

    def stale_for_recovery(self, now: datetime, threshold: timedelta) -> bool:
        return self.dispatched_at is not None and now - self.dispatched_at >= threshold


@dataclass(frozen=True)
class MetaJudgePromptContext:
    issue: str
    round: int
    solver_paths: dict[str, str]
    output_path: str
    transition_assessment: TransitionAssessment

    @property
    def work_unit_id(self) -> str:
        return f"issue-{self.issue}"

    @property
    def work_unit_producer(self) -> str:
        return "manual-issue (prompt-only provenance)"

    @property
    def work_unit_source_ref(self) -> str:
        return f"gh-issue-{self.issue}"

    @property
    def convergence_round_plus_one(self) -> int:
        return self.round + 1


@dataclass(frozen=True)
class DesignConsensusIssue:
    number: str
    title: str
    labels: tuple[str, ...]


class MetaJudgePromptRenderer:
    def __init__(self, template_path: Path, *, host_env: Mapping[str, str] | None = None) -> None:
        self.template_path = template_path
        self.host_env = host_env

    def render(self, context: MetaJudgePromptContext) -> str:
        template = self.template_path.read_text(encoding="utf-8")
        values = {
            "ISSUE_NUMBER": context.issue,
            "WORK_UNIT_ID": context.work_unit_id,
            "CLUSTER_ID": context.work_unit_id,
            "WORK_UNIT_PRODUCER": context.work_unit_producer,
            "WORK_UNIT_SOURCE_REF": context.work_unit_source_ref,
            "CONVERGENCE_ROUND": str(context.round),
            "CONVERGENCE_ROUND_PLUS_ONE": str(context.convergence_round_plus_one),
            "SOLVER_MINIMAL_PATH": context.solver_paths["minimal"],
            "SOLVER_STRUCTURAL_PATH": context.solver_paths["structural"],
            "SOLVER_DELETE_PATH": context.solver_paths["delete"],
            "META_JUDGE_OUTPUT_PATH": context.output_path,
            "TRANSITION_TYPE": context.transition_assessment.transition_type,
            "TRANSITION_CONFIDENCE": f"{context.transition_assessment.confidence:g}",
            "TRANSITION_EVIDENCE_REFS": context.transition_assessment.evidence_refs_text,
        }
        return render_prompt_text(
            template,
            skill_root=self.template_path.parents[1],
            values=values,
            host_env=self.host_env,
        )


PHASE9_LOG_RE = re.compile(
    r"^phase9-issue(?P<issue>\d+)-r(?P<round>\d+)-"
    r"(?P<actor>minimal|structural|delete|judge|reflector)\.log$"
)
SOLVER_LOG_RE = re.compile(
    r"^solver-issue(?P<issue>\d+)-r(?P<round>\d+)-"
    r"(?P<actor>minimal|structural|delete)\.log$"
)
META_JUDGE_LOG_RE = re.compile(r"^meta-judge-issue(?P<issue>\d+)-r(?P<round>\d+)\.log$")


def parse_phase9_log_identity(name: str) -> Phase9LogIdentity | None:
    match = PHASE9_LOG_RE.match(name)
    if match:
        return Phase9LogIdentity(
            issue=match.group("issue"),
            round=int(match.group("round")),
            actor=cast(Literal["minimal", "structural", "delete", "judge", "reflector"], match.group("actor")),
            dialect="phase9",
        )
    match = SOLVER_LOG_RE.match(name)
    if match:
        return Phase9LogIdentity(
            issue=match.group("issue"),
            round=int(match.group("round")),
            actor=cast(Literal["minimal", "structural", "delete"], match.group("actor")),
            dialect="solver",
        )
    match = META_JUDGE_LOG_RE.match(name)
    if match:
        return Phase9LogIdentity(
            issue=match.group("issue"),
            round=int(match.group("round")),
            actor="judge",
            dialect="meta-judge",
        )
    return None


class Phase9Router:
    def __init__(
        self,
        repo_root: Path | None = None,
        *,
        ctx: LoopContext | None = None,
        dry_run: bool = False,
        command_runner: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        if ctx is None:
            if repo_root is None:
                raise ValueError("repo_root or ctx is required")
            # Read the process environment (the daemon is started after sourcing
            # host.env) so GH_REPO_SLUG and other host facts resolve. Restricting
            # env to {REPO_ROOT} drops the slug -> _open_design_consensus_issues
            # returns [] and DesignConsensusIssueIntake silently never dispatches.
            ctx = LoopContext.load(repo_root=repo_root)
        self.ctx = ctx
        self.repo_root = ctx.repo_root
        self.skill_root = ctx.skill_root
        self.dry_run = dry_run
        self.loop_dir = ctx.paths.refactor_loop
        self.logs_dir = ctx.paths.logs
        self.runs_dir = ctx.paths.runs
        self.prompts_dir = ctx.paths.prompts / "phase9"
        self.ledger_path = self.loop_dir / "phase9-router-ledger.jsonl"
        # Artifact parity: ctx.paths.pending_events resolves to
        # .refactor-loop/.controller-pending-events.log.
        self.pending_events_path = ctx.paths.pending_events
        self.lock_path = self.loop_dir / "phase9-router.lock"
        self.command_runner = command_runner or self._default_runner
        self._fallback_seen: set[str] = self._load_persisted_fallback_seen()
        self._source_issue_decisions: dict[str, Phase9SourceIssueDecision] = {}
        self._issue_source_snapshots: dict[str, IssueSourceSnapshot] = {}
        self._terminal_decisions: dict[str, Phase9TerminalDecision] = {}
        self._managed_work_items: tuple[ManagedWorkSnapshotItem, ...] | None = None
        self._managed_work_items_by_number: dict[str, ManagedWorkSnapshotItem] | None = None
        self._managed_work_snapshot_loaded_ok: bool | None = None
        self._managed_work_snapshot_unavailable_reason: str | None = None
        self._pending_spawn_intent_logs: set[str] = set()
        self._ledger_entries_by_key: dict[str, list[dict[str, object]]] = {}
        self._tick_dispatch_count = 0

    def tick(self) -> None:
        if not graphql_headroom_ok(cwd=self.ctx.repo_root, env=self.ctx.env_for_subprocess()):
            self._log_tick_status("skip:graphql-backoff remaining=unknown")
            return
        decision = require_active_controller(self.ctx, "phase9-router")
        write_active_controller_status(self.ctx, decision)
        if not decision.allowed:
            self._log_tick_status(f"noop:not-owner:{decision.status}")
            return
        self.loop_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        self._tick_dispatch_count = 0
        before_fallbacks = len(self._fallback_seen)
        self._source_issue_decisions = {}
        self._issue_source_snapshots = {}
        self._terminal_decisions = {}
        self._managed_work_items = None
        self._managed_work_items_by_number = None
        self._managed_work_snapshot_loaded_ok = None
        self._managed_work_snapshot_unavailable_reason = None
        self._pending_spawn_intent_logs = self._read_pending_spawn_intent_logs()
        self._ledger_entries_by_key = self._read_ledger_entries_by_key()
        ledger = set(self._ledger_entries_by_key)
        markers = self._collect_markers()
        self._recover_actor_health(ledger)
        self._dispatch_design_issue_intake(ledger)
        self._dispatch_solver_triplets(markers, ledger)
        self._dispatch_meta_judge_routes(markers, ledger)
        self._dispatch_reflector_routes(markers, ledger)
        self._append_fallbacks(markers, ledger)
        dispatched = self._tick_dispatch_count
        fallbacks = len(self._fallback_seen) - before_fallbacks
        if dispatched > 0:
            self._log_tick_status(f"dispatched {dispatched} spawn-intent")
        elif fallbacks > 0:
            self._log_tick_status(f"dispatched {fallbacks} fallback-event")
        else:
            self._log_tick_status("noop:no-dispatchable-markers")

    @contextlib.contextmanager
    def singleton(self) -> Iterable[None]:
        self.loop_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("w", encoding="utf-8") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SystemExit(f"another phase9_router_daemon holds {self.lock_path}")
            lock.write(f"pid={os.getpid()}\n")
            lock.flush()
            yield

    MARKER_TAIL_LINES = 30
    TAIL_READ_BYTES = 8192

    @staticmethod
    def _read_tail_lines(path: Path, num_lines: int) -> list[str]:
        try:
            with path.open("rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - Phase9Router.TAIL_READ_BYTES))
                blob = fh.read()
        except OSError:
            return []
        text = blob.decode("utf-8", errors="replace")
        lines = text.splitlines()
        return lines[-num_lines:] if len(lines) > num_lines else lines

    def _collect_markers(self) -> list[Marker]:
        markers: list[Marker] = []
        for log_path in sorted(self.logs_dir.glob("*.log")):
            if not self._is_clean_exit(log_path):
                continue
            identity = self._identity_from_path(log_path)
            if identity is None:
                continue
            marker = self._final_marker_from_path(log_path)
            if marker is None:
                continue
            role: str | None = identity.actor
            if marker.startswith("SOLVER_DONE:"):
                role = self._role_from_solver_marker(marker)
                if role not in self._solver_roles():
                    continue
            markers.append(Marker(marker, log_path, identity.issue, identity.round, role))
        return markers

    def _final_marker_from_path(self, path: Path) -> str | None:
        marker_read = read_worker_terminal_marker(path)
        marker = marker_read.marker
        if marker is None:
            return None
        identity = self._identity_from_path(path)
        if identity is None:
            return None
        if identity.actor in self._solver_roles():
            return marker if marker.startswith(f"SOLVER_DONE:{identity.actor}:") else None
        if identity.actor == self._judge_role():
            if marker.startswith("META_JUDGE_DONE:"):
                return marker
            if any(marker.startswith(prefix) for prefix in LIFECYCLE_PREFIXES):
                return marker
            return Phase9MarkerGrammar.parse_marker_candidate(marker)
        if identity.actor == "reflector":
            if marker.startswith("META_RESOLVED:"):
                return marker
            if any(marker.startswith(prefix) for prefix in LIFECYCLE_PREFIXES):
                return marker
            return Phase9MarkerGrammar.parse_marker_candidate(marker)
        return None

    def _load_persisted_fallback_seen(self) -> set[str]:
        """Seed _fallback_seen from existing pending-events log so restart is idempotent."""
        seen: set[str] = set()
        if not self.pending_events_path.exists():
            return seen
        try:
            content = self.pending_events_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return seen
        for line in content.splitlines():
            idx = line.find("phase9-router-fallback")
            prefix = "phase9-router-fallback"
            if idx == -1:
                idx = line.find("phase9-triplet-evidence-invalid")
                prefix = "phase9-triplet-evidence-invalid"
            if idx == -1:
                continue
            payload = line[idx + len(prefix):].strip()
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            log_path = event.get("log_path")
            if isinstance(log_path, str):
                seen.add(f"fallback:{self._stored_artifact_path_key(log_path)}")
            key = event.get("key")
            if isinstance(key, str) and key.startswith("phase9-triplet-evidence-invalid:"):
                seen.add(key)
            if isinstance(key, str) and key.startswith("phase9-source-eligibility:"):
                seen.add(key)
            if isinstance(key, str) and key.startswith("phase9-meta-judge-prompt:"):
                seen.add(key)
            if isinstance(key, str) and key.startswith("phase9-triplet-suppression:"):
                seen.add(key)
            if isinstance(key, str) and key.startswith("phase9-terminal-eligibility:"):
                seen.add(key)
            if isinstance(key, str) and key.startswith("phase9-actor-health:"):
                seen.add(key)
        return seen

    def _identity_from_path(self, path: Path) -> Phase9LogIdentity | None:
        return parse_phase9_log_identity(path.name)

    def _is_clean_exit(self, path: Path) -> bool:
        return log_has_clean_exit(path)

    def _dispatch_solver_triplets(self, markers: list[Marker], ledger: set[str]) -> None:
        solver_roles = self._solver_roles()
        judge_role = self._judge_role()
        by_issue_round: dict[tuple[str, int], dict[str, Marker]] = {}
        for marker in markers:
            if not marker.marker.startswith("SOLVER_DONE:") or marker.role not in solver_roles:
                continue
            by_issue_round.setdefault((marker.issue, marker.round), {})[marker.role] = marker

        for (issue, round_no), role_markers in by_issue_round.items():
            if set(role_markers) != set(solver_roles):
                continue
            key = self._key(issue, round_no, judge_role)
            log_path = self._log_path(issue, round_no, judge_role)
            if key in ledger:
                continue
            suppression_reason = self._solver_triplet_suppression_reason(issue, round_no, judge_role, log_path)
            if suppression_reason is not None:
                self._append_solver_triplet_suppression_event(issue, round_no, judge_role, log_path, suppression_reason)
                continue
            if not self._require_open_source_issue(
                issue,
                round_no,
                "solver_triplet_to_judge",
                "SOLVER_DONE:triplet",
                next(iter(role_markers.values())).log_path,
            ):
                continue
            violation = self._peer_solver_reference_violation(issue, round_no)
            if violation is not None:
                self._append_invalid_triplet_event(issue, round_no, violation)
                continue
            prompt_body = self._meta_judge_prompt(issue, round_no, role_markers.values())
            if prompt_body is None:
                continue
            prompt = self._write_prompt(issue, round_no, judge_role, prompt_body)
            if self._spawn(prompt, log_path):
                self._append_ledger(
                    key,
                    "SOLVER_DONE:triplet",
                    log_path,
                    extra=self._solver_triplet_ledger_fields(issue, round_no, role_markers.values(), prompt),
                )
                ledger.add(key)

    def _dispatch_design_issue_intake(self, ledger: set[str]) -> None:
        for item in self._open_design_consensus_issues():
            issue = item.number
            if not self._require_open_source_issue(
                issue,
                1,
                "design_consensus_issue_intake",
                "DesignConsensusIssueIntake",
                self.pending_events_path,
            ):
                continue
            for role in self._solver_roles():
                key = self._key(issue, 1, role)
                log_path = self._log_path(issue, 1, role)
                if key in ledger or self._solver_intake_suppressed(issue, 1, role, log_path):
                    continue
                terminal_decision = self._solver_dispatch_terminal_decision(
                    issue,
                    item.labels,
                )
                if not terminal_decision.allowed:
                    self._append_terminal_fallback_event(
                        issue,
                        1,
                        "design_consensus_issue_intake",
                        "DesignConsensusIssueIntake",
                        self.pending_events_path,
                        terminal_decision,
                    )
                    continue
                prompt = self._write_prompt(
                    issue,
                    1,
                    role,
                    self._solver_prompt(issue, 1, role, "DesignConsensusIssueIntake"),
                )
                if self._spawn(prompt, log_path, route="design_consensus_issue_intake", reason="phase9-router design issue intake"):
                    self._append_ledger(
                        key,
                        "DesignConsensusIssueIntake",
                        log_path,
                        extra=self._design_issue_intake_ledger_fields(issue, role, item, prompt),
                    )
                    ledger.add(key)

    def _open_design_consensus_issues(self) -> list[DesignConsensusIssue]:
        snapshot = load_open_managed_work_snapshot(self.ctx)
        self._managed_work_snapshot_loaded_ok = snapshot.loaded_ok
        self._managed_work_snapshot_unavailable_reason = snapshot.reason
        self._managed_work_items = tuple(snapshot.items) if snapshot.loaded_ok else ()
        self._managed_work_items_by_number = {
            str(item.number): item for item in snapshot.items if snapshot.loaded_ok and item.kind == "issue"
        }
        if not snapshot.loaded_ok:
            print(
                snapshot.unavailable_diagnostic(
                    "phase9-router.design-consensus-issue-intake",
                    target_context="open-design-consensus-issues",
                ),
                flush=True,
            )
            return []
        issues: list[DesignConsensusIssue] = []
        for row in snapshot.items:
            number = str(row.number)
            if row.kind != "issue":
                continue
            labels = tuple(str(label) for label in row.labels if str(label))
            normalized = label_catalog.normalize_label_set(labels)
            if label_catalog.MANAGED not in normalized.canonical:
                continue
            if normalized.phase != label_catalog.PHASE_DESIGN_SOLVING:
                continue
            if label_catalog.HUMAN_MAINTAINER_DECISION in normalized.canonical:
                continue
            issues.append(DesignConsensusIssue(number=number, title=row.title, labels=labels))
        return issues

    def _solver_intake_suppressed(self, issue: str, round_no: int, role: str, log_path: Path) -> bool:
        return self._equivalent_actor_log_exists(issue, round_no, role) or self._pending_spawn_intent_exists(log_path)

    def _dispatch_meta_judge_routes(self, markers: list[Marker], ledger: set[str]) -> None:
        for marker in markers:
            if marker.marker.startswith("META_JUDGE_DONE:converge:round-"):
                if marker.role != self._judge_role():
                    continue
                target_round = self._converge_target_round(marker.marker, marker.round)
                if target_round is None:
                    continue
                stalled_reason = self._stalled_route_reason(marker.issue, marker.round, marker.marker)
                if stalled_reason is not None:
                    self._dispatch_stalled_reflector(marker, ledger, stalled_reason)
                    continue
                for role in self._solver_roles():
                    key = self._key(marker.issue, target_round, role)
                    log_path = self._log_path(marker.issue, target_round, role)
                    if key in ledger or self._actor_dispatch_suppressed(marker.issue, target_round, role, log_path):
                        continue
                    terminal_decision = self._solver_dispatch_terminal_decision(
                        marker.issue,
                    )
                    if not terminal_decision.allowed:
                        self._append_terminal_fallback_event(
                            marker.issue,
                            target_round,
                            "converge_to_next_solvers",
                            marker.marker,
                            marker.log_path,
                            terminal_decision,
                        )
                        continue
                    if not self._require_open_source_issue(
                        marker.issue,
                        target_round,
                        "converge_to_next_solvers",
                        marker.marker,
                        marker.log_path,
                    ):
                        continue
                    prompt = self._write_prompt(
                        marker.issue,
                        target_round,
                        role,
                        self._solver_prompt(marker.issue, target_round, role, marker.marker),
                    )
                    if self._spawn(prompt, log_path):
                        self._append_ledger(key, marker.marker, log_path)
                        ledger.add(key)
                continue

            if marker.marker.startswith("META_JUDGE_DONE:escalate:stalled:"):
                if marker.role != self._judge_role():
                    continue
                self._dispatch_stalled_reflector(marker, ledger, "stalled-marker")

    def _append_fallbacks(self, markers: list[Marker], ledger: set[str]) -> None:
        for marker in markers:
            if self._directly_handled(marker, ledger):
                continue
            if marker.marker.startswith("SOLVER_DONE:"):
                continue
            event_key = f"fallback:{self._artifact_path(marker.log_path)}"
            if event_key in self._fallback_seen:
                continue
            self._fallback_seen.add(event_key)
            self._append_pending_event(marker)

    def _dispatch_reflector_routes(self, markers: list[Marker], ledger: set[str]) -> None:
        for marker in markers:
            if not marker.marker.startswith("META_RESOLVED:re-design:"):
                continue
            if marker.role != "reflector":
                continue
            target_round = marker.round + 1
            for role in self._solver_roles():
                key = self._key(marker.issue, target_round, role)
                log_path = self._log_path(marker.issue, target_round, role)
                if key in ledger or self._actor_dispatch_suppressed(marker.issue, target_round, role, log_path):
                    continue
                terminal_decision = self._solver_dispatch_terminal_decision(marker.issue)
                if not terminal_decision.allowed:
                    self._append_terminal_fallback_event(
                        marker.issue,
                        target_round,
                        "redesign_to_next_solvers",
                        marker.marker,
                        marker.log_path,
                        terminal_decision,
                    )
                    continue
                if not self._require_open_source_issue(
                    marker.issue,
                    target_round,
                    "redesign_to_next_solvers",
                    marker.marker,
                    marker.log_path,
                ):
                    continue
                prompt = self._write_prompt(
                    marker.issue,
                    target_round,
                    role,
                    self._solver_prompt(marker.issue, target_round, role, marker.marker),
                )
                if self._spawn(
                    prompt,
                    log_path,
                    route="redesign_to_next_solvers",
                    reason="phase9-router re-design continuation",
                ):
                    self._append_ledger(key, marker.marker, log_path)
                    ledger.add(key)

    def _directly_handled(self, marker: Marker, ledger: set[str]) -> bool:
        if marker.marker.startswith("META_JUDGE_DONE:converge:round-"):
            if marker.role != self._judge_role():
                return False
            target_round = self._converge_target_round(marker.marker, marker.round)
            if target_round is None:
                return False
            if self._stalled_route_reason(marker.issue, marker.round, marker.marker) is not None:
                if f"phase9-terminal-eligibility:{marker.issue}-{marker.round}-stalled_to_reflector" in self._fallback_seen:
                    return True
                if f"phase9-source-eligibility:{marker.issue}-{marker.round}-stalled_to_reflector" in self._fallback_seen:
                    return True
                return self._key(marker.issue, marker.round, "reflector") in ledger
            if f"phase9-terminal-eligibility:{marker.issue}-{target_round}-converge_to_next_solvers" in self._fallback_seen:
                return True
            if f"phase9-source-eligibility:{marker.issue}-{target_round}-converge_to_next_solvers" in self._fallback_seen:
                return True
            return all(self._key(marker.issue, target_round, role) in ledger for role in self._solver_roles())
        if marker.marker.startswith("META_JUDGE_DONE:escalate:stalled:"):
            if marker.role != self._judge_role():
                return False
            if f"phase9-terminal-eligibility:{marker.issue}-{marker.round}-stalled_to_reflector" in self._fallback_seen:
                return True
            if f"phase9-source-eligibility:{marker.issue}-{marker.round}-stalled_to_reflector" in self._fallback_seen:
                return True
            return self._key(marker.issue, marker.round, "reflector") in ledger
        if marker.marker.startswith("META_RESOLVED:re-design:"):
            if marker.role != "reflector":
                return False
            target_round = marker.round + 1
            if f"phase9-terminal-eligibility:{marker.issue}-{target_round}-redesign_to_next_solvers" in self._fallback_seen:
                return True
            if f"phase9-source-eligibility:{marker.issue}-{target_round}-redesign_to_next_solvers" in self._fallback_seen:
                return True
            return all(self._key(marker.issue, target_round, role) in ledger for role in self._solver_roles())
        return False

    def _round_from_converge(self, marker: str) -> int | None:
        return Phase9MarkerGrammar.parse_converge_round(marker)

    def _converge_target_round(self, marker_text: str, source_round: int) -> int | None:
        payload_round = Phase9MarkerGrammar.parse_converge_round(marker_text)
        if payload_round in {source_round, source_round + 1}:
            return source_round + 1
        return None

    def _dispatch_stalled_reflector(self, marker: Marker, ledger: set[str], reason: str | None = None) -> None:
        key = self._key(marker.issue, marker.round, "reflector")
        log_path = self._log_path(marker.issue, marker.round, "reflector")
        if key in ledger or self._actor_dispatch_suppressed(marker.issue, marker.round, "reflector", log_path):
            return
        stalled_reason = reason or self._stalled_route_reason(marker.issue, marker.round, marker.marker)
        if stalled_reason is None:
            return
        terminal_decision = self._solver_dispatch_terminal_decision(marker.issue)
        if not terminal_decision.allowed:
            self._append_terminal_fallback_event(
                marker.issue,
                marker.round,
                "stalled_to_reflector",
                marker.marker,
                marker.log_path,
                terminal_decision,
            )
            return
        if not self._require_open_source_issue(
            marker.issue,
            marker.round,
            "stalled_to_reflector",
            marker.marker,
            marker.log_path,
        ):
            return
        prompt = self._write_prompt(marker.issue, marker.round, "reflector", self._reflector_prompt(marker))
        if self._spawn(prompt, log_path, reason=stalled_reason):
            self._append_ledger(key, marker.marker, log_path)
            ledger.add(key)

    def _stalled_route_reason(self, issue: str, round_no: int, marker: str) -> str | None:
        if self._stalled_predicate_holds(issue, round_no):
            return "no-progress-signature"
        if self._round_ceiling_holds(round_no, marker):
            return "round-ceiling"
        return None

    def _round_ceiling_holds(self, round_no: int, marker: str) -> bool:
        return round_no >= PHASE9_STALLED_ROUND_CEILING and self._converge_target_round(marker, round_no) is not None

    def _stalled_predicate_holds(self, issue: str, round_no: int) -> bool:
        if round_no < 3:
            return False
        judge_reasons = [self._judge_converge_reason_for_round(issue, r) for r in range(round_no - 2, round_no + 1)]
        if all(reason is not None for reason in judge_reasons):
            judge_signatures = [
                self._normalize_judge_converge_no_progress_reason(reason)
                for reason in judge_reasons
                if reason is not None
            ]
            return all(signature is not None for signature in judge_signatures) and len(set(judge_signatures)) == 1
        recent: list[set[str]] = []
        for r in range(round_no - 2, round_no + 1):
            signatures: set[str] = set()
            for role in self._solver_roles():
                role_signatures: set[str] = set()
                for path in self._solver_history_log_paths(issue, r, role):
                    if not self._is_clean_exit(path):
                        continue
                    for marker in self._collect_markers_from_path(path):
                        if marker.startswith(f"SOLVER_DONE:{role}:"):
                            signature = self._solver_no_progress_signature(marker)
                            if signature is None:
                                return False
                            role_signatures.add(signature)
                if not role_signatures:
                    return False
                signatures.update(role_signatures)
            if not signatures:
                return False
            recent.append(signatures)
        return recent[0] == recent[1] == recent[2]

    def _judge_converge_no_progress_signature(self, issue: str, round_no: int) -> str | None:
        reason = self._judge_converge_reason_for_round(issue, round_no)
        if reason is None:
            return None
        return self._normalize_judge_converge_no_progress_reason(reason)

    def _judge_converge_reason_for_round(self, issue: str, round_no: int) -> str | None:
        for path in self._actor_log_paths(issue, round_no, self._judge_role()):
            if not self._is_clean_exit(path):
                continue
            marker = self._final_marker_from_path(path)
            if marker is None or self._converge_target_round(marker, round_no) is None:
                continue
            reason = self._judge_converge_reason(marker)
            if reason is None:
                continue
            return reason
        return None

    def _judge_converge_reason(self, marker: str) -> str | None:
        parts = marker.split(":", 3)
        if len(parts) < 4 or parts[0] != "META_JUDGE_DONE" or parts[1] != "converge":
            return None
        return parts[3]

    def _normalize_judge_converge_no_progress_reason(self, reason: str) -> str | None:
        normalized = reason.lower()
        normalized = re.sub(r"\bround[-\s]*\d+\b", " ", normalized)
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if not normalized:
            return None
        normalized_words = f" {normalized} "
        motion_terms = (
            "moving",
            "need more",
            "different split",
            "split different",
            "changed split",
            "split changed",
        )
        if any(f" {term} " in normalized_words for term in motion_terms):
            return None
        stable_terms = (
            "split unchanged",
            "unchanged split",
            "same split",
            "implementation split unchanged",
            "proposal split unchanged",
        )
        if any(f" {term} " in normalized_words for term in stable_terms):
            return "judge-converge/split-unchanged"
        return None

    def _collect_markers_from_path(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        marker = self._final_marker_from_path(path)
        return [marker] if marker else []

    def _solver_verdict_text(self, marker: str) -> str:
        parts = marker.split(":", 3)
        return parts[2] if len(parts) >= 3 else marker

    def _solver_no_progress_signature(self, marker: str) -> str | None:
        parts = marker.split(":", 2)
        if len(parts) < 3:
            return None
        payload = parts[2].strip().lower()
        if not payload:
            return None
        if "source-location-missing-or-invalid" in payload:
            return "source-unreachable/no-actionable-source"
        if re.search(r"\bno-current-[a-z0-9._/-]*target\b", payload):
            return "source-unreachable/no-actionable-source"
        if payload.startswith("propose:") and self._propose_unreadable_source_signature(payload) is not None:
            return "source-unreachable/no-actionable-source"
        if payload.startswith("propose:"):
            return payload
        return self._solver_verdict_text(marker)

    def _propose_unreadable_source_signature(self, payload: str) -> str | None:
        if not re.search(r"\b(?:target|source|pr)[a-z0-9._/-]*-unreadable\b", payload):
            return None
        if not re.search(r"\b(?:current-checkout|checkout|source|target|pr)\b", payload):
            return None
        return "source-unreachable/no-actionable-source"

    def _actor_dispatch_suppressed(self, issue: str, round_no: int, actor: str, log_path: Path) -> bool:
        return self._equivalent_actor_log_exists(issue, round_no, actor) or self._pending_spawn_intent_exists(log_path)

    def _pending_spawn_intent_exists(self, log_path: Path) -> bool:
        return self._artifact_path(log_path) in self._pending_spawn_intent_logs

    def _read_pending_spawn_intent_logs(self) -> set[str]:
        logs: set[str] = set()
        if not self.pending_events_path.exists():
            return logs
        try:
            lines = self.pending_events_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return logs
        from ..wakeup_plan import (
            archived_invalid_harness_spawn_intent_markers,
            harness_spawn_intent_line_is_archived_invalid,
            live_valid_harness_spawn_intent,
        )

        archived_invalid_markers = archived_invalid_harness_spawn_intent_markers(lines)
        for line in lines:
            if " HARNESS_SPAWN_INTENT " not in line:
                continue
            if harness_spawn_intent_line_is_archived_invalid(line, None, archived_invalid_markers):
                continue
            try:
                event = json.loads(line.split(" HARNESS_SPAWN_INTENT ", 1)[1])
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if not live_valid_harness_spawn_intent(self.ctx, line, event, archived_invalid_markers):
                continue
            log = event.get("log")
            if isinstance(log, str):
                logs.add(self._stored_artifact_path_key(log))
        return logs

    def _actor_health(self, issue: str, round_no: int, actor: str, ledger: set[str]) -> Phase9ActorHealth:
        key = self._key(issue, round_no, actor)
        log_path = self._log_path(issue, round_no, actor)
        valid_marker = self._valid_actor_marker(issue, round_no, actor)
        markerless_clean_log = self._markerless_clean_actor_log(issue, round_no, actor)
        terminal_decision = (
            Phase9TerminalDecision(True, "phase9-terminal-open")
            if actor == self._judge_role()
            else self._solver_dispatch_terminal_decision(issue)
        )
        return Phase9ActorHealth(
            issue=issue,
            round=round_no,
            actor=actor,
            key=key,
            log_path=log_path,
            ledgered=key in ledger,
            target_log_exists=log_path.exists(),
            equivalent_log_exists=self._equivalent_actor_log_exists(issue, round_no, actor),
            pending_intent=self._artifact_path(log_path) in self._pending_spawn_intent_logs,
            source_allowed=self._source_issue_decision(issue).allowed,
            terminal_allowed=terminal_decision.allowed,
            valid_marker=valid_marker,
            markerless_clean_log=markerless_clean_log,
            dispatched_at=self._last_dispatched_at(key),
        )

    def _valid_actor_marker(self, issue: str, round_no: int, actor: str) -> str | None:
        for path in self._actor_log_paths(issue, round_no, actor):
            if not path.exists() or not self._is_clean_exit(path):
                continue
            marker = self._final_marker_from_path(path)
            if marker is not None:
                return marker
        return None

    def _markerless_clean_actor_log(self, issue: str, round_no: int, actor: str) -> Path | None:
        for path in self._actor_log_paths(issue, round_no, actor):
            if not path.exists() or not self._is_clean_exit(path):
                continue
            if self._final_marker_from_path(path) is None:
                return path
        return None

    def _actor_log_paths(self, issue: str, round_no: int, actor: str) -> tuple[Path, ...]:
        paths: list[Path] = [self._log_path(issue, round_no, actor)]
        if actor in self._solver_roles():
            paths.append(self.logs_dir / f"solver-issue{issue}-r{round_no}-{actor}.log")
        if actor == self._judge_role():
            paths.append(self.logs_dir / f"meta-judge-issue{issue}-r{round_no}.log")
        return tuple(paths)

    def _last_dispatched_at(self, key: str) -> datetime | None:
        for entry in reversed(self._ledger_entries_by_key.get(key, [])):
            raw = entry.get("dispatched_at")
            if not isinstance(raw, str):
                continue
            parsed = self._parse_utc(raw)
            if parsed is not None:
                return parsed
        return None

    def _parse_utc(self, value: str) -> datetime | None:
        try:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def _stale_revival_threshold(self) -> timedelta:
        raw = self.ctx.host_env.get("STALE_REVIVAL_HOURS") or os.environ.get("STALE_REVIVAL_HOURS") or "3"
        try:
            hours = float(raw)
        except ValueError:
            hours = 3.0
        if hours <= 0:
            hours = 3.0
        return timedelta(hours=hours)

    def _recover_actor_health(self, ledger: set[str]) -> None:
        self._append_markerless_solver_exhausted_events()
        self._recover_stale_ledgered_actors(ledger)

    def _append_markerless_solver_exhausted_events(self) -> None:
        markerless_by_round: dict[tuple[str, int], list[Phase9ActorHealth]] = {}
        for log_path in sorted(self.logs_dir.glob("*.log")):
            identity = self._identity_from_path(log_path)
            if identity is None or identity.actor not in self._solver_roles():
                continue
            health = self._actor_health(identity.issue, identity.round, identity.actor, set(self._ledger_entries_by_key))
            if health.markerless_clean_log is not None and health.valid_marker is None:
                markerless_by_round.setdefault((identity.issue, identity.round), []).append(health)
        for (issue, round_no), healths in markerless_by_round.items():
            self._append_actor_health_fallback_event(
                issue,
                round_no,
                "phase9-solver-markerless-exhausted",
                "solver_triplet_to_judge",
                {
                    "markerless_logs": [
                        self._artifact_path(health.markerless_clean_log)
                        for health in healths
                        if health.markerless_clean_log is not None
                    ],
                    "roles": sorted(health.actor for health in healths),
                },
            )

    def _recover_stale_ledgered_actors(self, ledger: set[str]) -> None:
        now = datetime.now(timezone.utc)
        threshold = self._stale_revival_threshold()
        capacity_threshold = self._capacity_recovery_threshold(now)
        for key, entries in sorted(self._ledger_entries_by_key.items()):
            parsed = self._parse_key(key)
            if parsed is None:
                continue
            issue, round_no, actor = parsed
            if actor not in (*self._solver_roles(), self._judge_role()):
                continue
            health = self._actor_health(issue, round_no, actor, ledger)
            effective_threshold = capacity_threshold if capacity_threshold is not None else threshold
            if not self._actor_recovery_allowed(health, now, effective_threshold):
                continue
            source_marker = self._ledger_source_marker(entries)
            prompt_body = self._recovery_prompt_body(issue, round_no, actor, source_marker)
            if prompt_body is None:
                continue
            prompt = self._write_prompt(issue, round_no, actor, prompt_body)
            if self._spawn(
                prompt,
                health.log_path,
                route="actor_health_recovery",
                reason="phase9-router actor health recovery",
            ):
                self._append_ledger(
                    key,
                    source_marker,
                    health.log_path,
                    extra={
                        "route": "actor_health_recovery",
                        "issue": issue,
                        "round": round_no,
                        "target_actor": actor,
                        "recovery": "Phase9ActorHealth",
                        "recovered_from_ledger_rows": len(entries),
                    },
                )

    def _actor_recovery_allowed(self, health: Phase9ActorHealth, now: datetime, threshold: timedelta) -> bool:
        return (
            health.ledgered
            and health.stale_for_recovery(now, threshold)
            and health.valid_marker is None
            and health.markerless_clean_log is None
            and not health.target_log_exists
            and not health.equivalent_log_exists
            and not health.pending_intent
            and health.source_allowed
            and health.terminal_allowed
        )

    def _capacity_recovery_threshold(self, now: datetime) -> timedelta | None:
        latest = self._latest_hard_gate_dispatch_required_at(now)
        if latest is None:
            return None
        if now - latest > timedelta(minutes=10):
            return None
        return timedelta(minutes=2)

    def _latest_hard_gate_dispatch_required_at(self, now: datetime) -> datetime | None:
        if not self.pending_events_path.exists():
            return None
        try:
            lines = self.pending_events_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None
        for line in reversed(lines[-200:]):
            if "HARD_GATE:dispatch_required=" not in line:
                continue
            if "dispatch_required=0" in line:
                continue
            ts = self._parse_pending_event_timestamp(line)
            if ts is not None:
                return ts
        return None

    def _parse_pending_event_timestamp(self, line: str) -> datetime | None:
        if len(line) < 20:
            return None
        prefix = line[:20]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", prefix):
            return None
        return self._parse_utc(prefix)

    def _ledger_source_marker(self, entries: list[dict[str, object]]) -> str:
        for entry in reversed(entries):
            marker = entry.get("marker")
            if isinstance(marker, str) and marker.strip():
                return marker
        return "Phase9ActorHealth:recovery"

    def _recovery_prompt_body(self, issue: str, round_no: int, actor: str, marker: str) -> str | None:
        if actor in self._solver_roles():
            return self._solver_prompt(issue, round_no, actor, marker)
        if actor == self._judge_role():
            role_markers = self._role_markers_for_round(issue, round_no)
            if role_markers is None:
                return None
            return self._meta_judge_prompt(issue, round_no, role_markers)
        return None

    def _role_markers_for_round(self, issue: str, round_no: int) -> list[Marker] | None:
        markers: list[Marker] = []
        for role in self._solver_roles():
            for path in self._actor_log_paths(issue, round_no, role):
                if not path.exists() or not self._is_clean_exit(path):
                    continue
                marker = self._final_marker_from_path(path)
                if marker and marker.startswith(f"SOLVER_DONE:{role}:"):
                    markers.append(Marker(marker, path, issue, round_no, role))
                    break
        if {marker.role for marker in markers} != set(self._solver_roles()):
            return None
        return markers

    def _parse_key(self, key: str) -> tuple[str, int, str] | None:
        parts = key.split("-", 2)
        if len(parts) != 3:
            return None
        issue, round_text, actor = parts
        if not issue.isdigit() or not round_text.isdigit():
            return None
        return issue, int(round_text), actor

    def _solver_triplet_suppression_reason(
        self,
        issue: str,
        round_no: int,
        actor: str,
        log_path: Path,
    ) -> Literal[
        "phase9-triplet-target-log-exists",
        "phase9-triplet-equivalent-log-exists",
        "phase9-triplet-pending-intent",
    ] | None:
        if log_path.exists():
            return "phase9-triplet-target-log-exists"
        if self._equivalent_actor_log_exists(issue, round_no, actor):
            return "phase9-triplet-equivalent-log-exists"
        if self._pending_spawn_intent_exists(log_path):
            return "phase9-triplet-pending-intent"
        return None

    def _equivalent_actor_log_exists(self, issue: str, round_no: int, actor: str) -> bool:
        paths = [self._log_path(issue, round_no, actor)]
        if actor in self._solver_roles():
            paths.append(self.logs_dir / f"solver-issue{issue}-r{round_no}-{actor}.log")
        if actor == self._judge_role():
            paths.append(self.logs_dir / f"meta-judge-issue{issue}-r{round_no}.log")
        return any(path.exists() for path in paths)

    def _read_ledger(self) -> set[str]:
        return set(self._read_ledger_entries_by_key())

    def _read_ledger_entries_by_key(self) -> dict[str, list[dict[str, object]]]:
        entries: dict[str, list[dict[str, object]]] = {}
        if not self.ledger_path.exists():
            return entries
        for line in self.ledger_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            key = entry.get("key")
            if isinstance(key, str):
                entries.setdefault(key, []).append(entry)
        return entries

    def _append_ledger(self, key: str, marker: str, log_path: Path, *, extra: dict[str, object] | None = None) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "key": key,
            "marker": marker,
            "log_path": self._artifact_path(log_path),
            "dispatched_at": self._now(),
            "dispatch_state": "harness-intent",
        }
        if extra:
            entry.update(extra)
        with self.ledger_path.open("a", encoding="utf-8") as ledger:
            ledger.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")

    def _append_pending_event(self, marker: Marker) -> None:
        self.pending_events_path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "key": f"fallback:{marker.issue}-{marker.round}",
            "marker": marker.marker,
            "log_path": self._artifact_path(marker.log_path),
            "dispatched_at": self._now(),
        }
        with self.pending_events_path.open("a", encoding="utf-8") as pending:
            pending.write(f"{self._now()} phase9-router-fallback {json.dumps(event, ensure_ascii=False, sort_keys=True)}\n")

    def _require_open_source_issue(
        self,
        issue: str,
        round_no: int,
        route: str,
        marker: str,
        log_path: Path,
    ) -> bool:
        decision = self._source_issue_decision(issue)
        if decision.allowed:
            return True
        self._append_source_issue_fallback_event(issue, round_no, route, marker, log_path, decision)
        return False

    def _source_issue_decision(self, issue: str) -> Phase9SourceIssueDecision:
        if issue not in self._source_issue_decisions:
            self._source_issue_decisions[issue] = self._read_source_issue_decision(issue)
        return self._source_issue_decisions[issue]

    def _read_source_issue_decision(self, issue: str) -> Phase9SourceIssueDecision:
        if not self._ensure_managed_work_items_loaded():
            return Phase9SourceIssueDecision(False, None, "phase9-source-state-unavailable")
        item = self._managed_work_items_by_number.get(str(issue)) if self._managed_work_items_by_number is not None else None
        if item is None:
            return Phase9SourceIssueDecision(False, "ABSENT_FROM_OPEN_MANAGED_SNAPSHOT", "phase9-source-not-open")
        state = str(item.state or "open")
        if not state.strip():
            return Phase9SourceIssueDecision(False, None, "phase9-source-state-unavailable")
        normalized = state.strip().upper()
        if normalized == "OPEN":
            return Phase9SourceIssueDecision(True, normalized, "phase9-source-open")
        return Phase9SourceIssueDecision(False, normalized, "phase9-source-not-open")

    def _issue_source_snapshot_markdown(self, issue: str) -> str:
        snapshot = self._read_issue_source_snapshot(issue)
        lines = [
            "## Issue source snapshot",
            "",
            f"source: gh-issue-{snapshot.number}",
            f"read_at: {snapshot.read_at}",
            f"state: {snapshot.source}",
            f"truncated: {str(snapshot.truncated).lower()}",
            "",
        ]
        if snapshot.unavailable_reason:
            lines.extend(
                [
                    "Snapshot unavailable.",
                    f"unavailable_reason: {snapshot.unavailable_reason}",
                    f"Fallback only: run `gh issue view {snapshot.number}` if the injected snapshot is unavailable.",
                ]
            )
            return "\n".join(lines)
        lines.extend(
            [
                f"# Issue #{snapshot.number}: {snapshot.title or '(untitled)'}",
                "",
                "## Body",
                "",
                snapshot.body or "(empty)",
                "",
                "## Recent comments",
                "",
            ]
        )
        if snapshot.comments:
            for comment in snapshot.comments:
                author = comment.get("author") or "unknown"
                created_at = comment.get("created_at") or "unknown"
                body = comment.get("body") or ""
                lines.extend([f"### {created_at} by {author}", "", body or "(empty)", ""])
        else:
            lines.append("(none)")
        return "\n".join(lines).rstrip()

    def _read_issue_source_snapshot(self, issue: str) -> IssueSourceSnapshot:
        snapshot = self._read_issue_metadata_snapshot(issue)
        if snapshot.unavailable_reason is not None or snapshot.comments_loaded:
            return snapshot
        comments_result, comments_source = self._issue_comments_projection(issue, snapshot.updated_at)
        if comments_result is None:
            snapshot = IssueSourceSnapshot(
                number=snapshot.number,
                title=snapshot.title,
                body=snapshot.body,
                comments=(),
                read_at=snapshot.read_at,
                source=snapshot.source,
                updated_at=snapshot.updated_at,
                truncated=snapshot.truncated,
                unavailable_reason="comments-read-failed",
                comments_loaded=True,
            )
            self._issue_source_snapshots[issue] = snapshot
            return snapshot
        comments: list[dict[str, str]] = []
        comments_truncated = False
        for raw_comment in comments_result[-20:]:
            if not isinstance(raw_comment, dict):
                continue
            comment_body, truncated = self._bounded_text(str(raw_comment.get("body") or ""), 4_000)
            comments_truncated = comments_truncated or truncated
            user = raw_comment.get("user") if isinstance(raw_comment.get("user"), dict) else {}
            comments.append(
                {
                    "id": str(raw_comment.get("id") or ""),
                    "author": str(user.get("login") or ""),
                    "created_at": str(raw_comment.get("created_at") or ""),
                    "body": comment_body,
                }
            )
        snapshot = IssueSourceSnapshot(
            number=snapshot.number,
            title=snapshot.title,
            body=snapshot.body,
            comments=tuple(comments),
            read_at=snapshot.read_at,
            source=f"{snapshot.source};comments={comments_source}",
            updated_at=snapshot.updated_at,
            truncated=snapshot.truncated or comments_truncated or len(comments_result) > 20,
            comments_loaded=True,
        )
        self._issue_source_snapshots[issue] = snapshot
        return snapshot

    def _read_issue_metadata_snapshot(self, issue: str) -> IssueSourceSnapshot:
        if issue not in self._issue_source_snapshots:
            self._issue_source_snapshots[issue] = self._load_issue_source_snapshot(issue)
        return self._issue_source_snapshots[issue]

    def _load_issue_source_snapshot(self, issue: str) -> IssueSourceSnapshot:
        read_at = self._now()
        if not self.ctx.gh_repo_slug:
            return self._unavailable_issue_source_snapshot(issue, read_at, "missing-gh-repo-slug")
        item = self._managed_work_item(issue)
        if item is not None:
            body, body_truncated = self._bounded_text(item.body, 20_000)
            return IssueSourceSnapshot(
                number=str(issue),
                title=item.title,
                body=body,
                comments=(),
                read_at=read_at,
                source=item.state,
                updated_at=item.updated_at,
                truncated=body_truncated,
            )
        if self._managed_work_snapshot_loaded_ok is False:
            return self._unavailable_issue_source_snapshot(
                issue,
                read_at,
                self._managed_work_snapshot_unavailable_reason or "managed-work-snapshot-unavailable",
            )
        return self._unavailable_issue_source_snapshot(issue, read_at, "not-in-open-managed-snapshot")

    def _unavailable_issue_source_snapshot(
        self,
        issue: str,
        read_at: str,
        reason: str,
        *,
        source: str = "unavailable",
    ) -> IssueSourceSnapshot:
        return IssueSourceSnapshot(
            number=str(issue),
            title="",
            body="",
            comments=(),
            read_at=read_at,
            source=source or "unavailable",
            updated_at="",
            truncated=False,
            unavailable_reason=reason,
        )

    def _managed_work_item(self, issue: str) -> ManagedWorkSnapshotItem | None:
        self._ensure_managed_work_items_loaded()
        return self._managed_work_items_by_number.get(str(issue)) if self._managed_work_items_by_number is not None else None

    def _ensure_managed_work_items_loaded(self) -> bool:
        if self._managed_work_items_by_number is None:
            snapshot = load_open_managed_work_snapshot(self.ctx)
            self._managed_work_items = tuple(snapshot.items) if snapshot.loaded_ok else ()
            self._managed_work_items_by_number = {}
            self._managed_work_snapshot_loaded_ok = snapshot.loaded_ok
            self._managed_work_snapshot_unavailable_reason = snapshot.reason
            if snapshot.loaded_ok:
                for item in snapshot.items:
                    if item.kind == "issue":
                        self._managed_work_items_by_number[str(item.number)] = item
        return bool(self._managed_work_snapshot_loaded_ok)

    def _issue_comments_projection(self, issue: str, updated_at: str) -> tuple[list[dict[str, object]] | None, str]:
        cache = self._read_comments_cache()
        row = cache.get(str(issue))
        if updated_at and isinstance(row, dict) and str(row.get("updated_at") or "") == str(updated_at):
            comments = row.get("comments")
            if isinstance(comments, list):
                return [comment for comment in comments if isinstance(comment, dict)], "cache:fresh"
        comments_result = self._gh_api_json(f"repos/{self.ctx.gh_repo_slug}/issues/{issue}/comments?per_page=20")
        if not isinstance(comments_result, list):
            return None, "unavailable"
        cache[str(issue)] = {
            "updated_at": str(updated_at or ""),
            "comments": comments_result[-20:],
            "not_live_state_fact_source": True,
            "not_host_production_ssot": True,
            "no_lifecycle_authority": True,
        }
        self._write_comments_cache(cache)
        return comments_result, "live"

    def _comments_cache_path(self) -> Path:
        return self.ctx.paths.state / "phase9-router-comments-cache.json"

    def _read_comments_cache(self) -> dict[str, object]:
        try:
            data = json.loads(self._comments_cache_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        issues = data.get("issues")
        return issues if isinstance(issues, dict) else {}

    def _write_comments_cache(self, cache: dict[str, object]) -> None:
        path = self._comments_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "issues": cache,
            "not_live_state_fact_source": True,
            "not_host_production_ssot": True,
            "no_lifecycle_authority": True,
        }
        tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)

    def _gh_api_json(self, path: str) -> Any:
        command = ["gh", "api", path]
        try:
            result = subprocess.run(
                command,
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                check=False,
                env=self.ctx.env_for_subprocess(),
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0 or not result.stdout.strip():
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return None

    def _bounded_text(self, text: str, limit: int) -> tuple[str, bool]:
        if len(text) <= limit:
            return text, False
        return text[:limit] + "\n[truncated]", True

    def _append_source_issue_fallback_event(
        self,
        issue: str,
        round_no: int,
        route: str,
        marker: str,
        log_path: Path,
        decision: Phase9SourceIssueDecision,
    ) -> None:
        event_key = f"phase9-source-eligibility:{issue}-{round_no}-{route}"
        if event_key in self._fallback_seen:
            return
        self._fallback_seen.add(event_key)
        self.pending_events_path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "key": event_key,
            "reason": decision.reason,
            "state": decision.state,
            "issue": issue,
            "round": round_no,
            "route": route,
            "marker": marker,
            "log_path": self._artifact_path(log_path),
            "dispatched_at": self._now(),
        }
        with self.pending_events_path.open("a", encoding="utf-8") as pending:
            pending.write(f"{self._now()} phase9-router-fallback {json.dumps(event, ensure_ascii=False, sort_keys=True)}\n")

    def _solver_dispatch_terminal_decision(
        self,
        issue: str,
        issue_labels: Iterable[str] | None = None,
    ) -> Phase9TerminalDecision:
        label_source = self._terminal_source_from_labels(issue_labels)
        if label_source is not None:
            return Phase9TerminalDecision(False, "phase9-already-consensus", label_source)
        if issue not in self._terminal_decisions:
            self._terminal_decisions[issue] = self._read_terminal_decision(issue)
        return self._terminal_decisions[issue]

    def _read_terminal_decision(self, issue: str) -> Phase9TerminalDecision:
        judge_source = self._terminal_consensus_judge_source(issue)
        if judge_source is not None:
            return Phase9TerminalDecision(False, "phase9-already-consensus", judge_source)
        snapshot_source = self._managed_work_terminal_source(issue)
        if snapshot_source is not None:
            return Phase9TerminalDecision(False, "phase9-already-consensus", snapshot_source)
        live_source = self._live_terminal_issue_source(issue)
        if live_source is not None:
            return Phase9TerminalDecision(False, "phase9-already-consensus", live_source)
        return Phase9TerminalDecision(True, "phase9-terminal-open", None)

    def _terminal_source_from_labels(self, issue_labels: Iterable[str] | None) -> str | None:
        if issue_labels is None:
            return None
        return design_consensus_terminal_source(0, labels=issue_labels)

    def _terminal_consensus_judge_source(self, issue: str) -> str | None:
        patterns = (f"phase9-issue{issue}-r*-judge.log", f"meta-judge-issue{issue}-r*.log")
        for pattern in patterns:
            for log_path in sorted(self.logs_dir.glob(pattern)):
                identity = self._identity_from_path(log_path)
                if identity is None or identity.issue != issue or identity.actor != self._judge_role():
                    continue
                if not self._is_clean_exit(log_path):
                    continue
                marker = self._final_marker_from_path(log_path)
                if marker and marker.startswith("META_JUDGE_DONE:consensus:"):
                    return f"consensus-judge-log:{self._artifact_path(log_path)}"
        return None

    def _live_terminal_issue_source(self, issue: str) -> str | None:
        if not self.ctx.gh_repo_slug or "GH_REPO_SLUG" not in self.ctx.host_env:
            return None
        command = [
            "gh",
            "api",
            f"repos/{self.ctx.gh_repo_slug}/issues/{issue}",
            "--jq",
            "[.labels[].name]",
        ]
        try:
            result = subprocess.run(
                command,
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                check=False,
                env=self.ctx.env_for_subprocess(),
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            if isinstance(payload, list):
                return self._terminal_source_from_labels(label for label in payload if isinstance(label, str))
            return None
        labels = payload.get("labels")
        if isinstance(labels, list):
            return self._terminal_source_from_labels(label for label in labels if isinstance(label, str))
        return None

    def _managed_work_terminal_source(self, issue: str) -> str | None:
        try:
            issue_number = int(issue)
        except ValueError:
            return None
        if not self._ensure_managed_work_items_loaded():
            return None
        return design_consensus_terminal_source(
            issue_number,
            items=(
                {
                    "kind": item.kind,
                    "number": item.number,
                    "labels": item.labels,
                    "body": item.body,
                    "state": item.state,
                }
                for item in (self._managed_work_items or ())
            ),
        )

    def _append_terminal_fallback_event(
        self,
        issue: str,
        round_no: int,
        route: str,
        marker: str,
        log_path: Path,
        decision: Phase9TerminalDecision,
    ) -> None:
        event_key = f"phase9-terminal-eligibility:{issue}-{round_no}-{route}"
        if event_key in self._fallback_seen:
            return
        self._fallback_seen.add(event_key)
        self.pending_events_path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "key": event_key,
            "reason": decision.reason,
            "terminal_source": decision.terminal_source,
            "issue": issue,
            "round": round_no,
            "route": route,
            "marker": marker,
            "log_path": self._artifact_path(log_path),
            "dispatched_at": self._now(),
        }
        with self.pending_events_path.open("a", encoding="utf-8") as pending:
            pending.write(f"{self._now()} phase9-router-fallback {json.dumps(event, ensure_ascii=False, sort_keys=True)}\n")

    def _append_invalid_triplet_event(self, issue: str, round_no: int, violation: dict[str, str]) -> None:
        event_key = f"phase9-triplet-evidence-invalid:{issue}-{round_no}"
        if event_key in self._fallback_seen:
            return
        self._fallback_seen.add(event_key)
        self.pending_events_path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "key": event_key,
            "reason": "phase9-triplet-evidence-invalid",
            "issue": issue,
            "round": round_no,
            "dispatched_at": self._now(),
            **violation,
        }
        with self.pending_events_path.open("a", encoding="utf-8") as pending:
            pending.write(f"{self._now()} phase9-triplet-evidence-invalid {json.dumps(event, ensure_ascii=False, sort_keys=True)}\n")

    def _append_meta_judge_prompt_fallback_event(
        self,
        issue: str,
        round_no: int,
        reason: Literal["phase9-meta-judge-template-unavailable", "phase9-meta-judge-scope-invalid"],
        **extra: str,
    ) -> None:
        event_key = f"phase9-meta-judge-prompt:{issue}-{round_no}"
        if event_key in self._fallback_seen:
            return
        self._fallback_seen.add(event_key)
        self.pending_events_path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "key": event_key,
            "reason": reason,
            "issue": issue,
            "round": round_no,
            "route": "solver_triplet_to_judge",
            "marker": "SOLVER_DONE:triplet",
            "dispatched_at": self._now(),
            **extra,
        }
        with self.pending_events_path.open("a", encoding="utf-8") as pending:
            pending.write(f"{self._now()} phase9-router-fallback {json.dumps(event, ensure_ascii=False, sort_keys=True)}\n")

    def _append_solver_triplet_suppression_event(
        self,
        issue: str,
        round_no: int,
        target_actor: str,
        log_path: Path,
        reason: Literal[
            "phase9-triplet-target-log-exists",
            "phase9-triplet-equivalent-log-exists",
            "phase9-triplet-pending-intent",
        ],
    ) -> None:
        event_key = f"phase9-triplet-suppression:{issue}-{round_no}-{target_actor}"
        if event_key in self._fallback_seen:
            return
        self._fallback_seen.add(event_key)
        self.pending_events_path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "key": event_key,
            "reason": reason,
            "issue": issue,
            "round": round_no,
            "route": "solver_triplet_to_judge",
            "marker": "SOLVER_DONE:triplet",
            "log_path": self._artifact_path(log_path),
            "target_actor": target_actor,
            "dispatched_at": self._now(),
        }
        with self.pending_events_path.open("a", encoding="utf-8") as pending:
            pending.write(f"{self._now()} phase9-router-fallback {json.dumps(event, ensure_ascii=False, sort_keys=True)}\n")

    def _append_actor_health_fallback_event(
        self,
        issue: str,
        round_no: int,
        reason: Literal[
            "phase9-solver-markerless-exhausted",
            "phase9-actor-recovery-prompt-unavailable",
        ],
        route: str,
        extra: dict[str, object],
    ) -> None:
        event_key = f"phase9-actor-health:{issue}-{round_no}-{reason}"
        if event_key in self._fallback_seen:
            return
        self._fallback_seen.add(event_key)
        self.pending_events_path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "key": event_key,
            "reason": reason,
            "issue": issue,
            "round": round_no,
            "route": route,
            "dispatched_at": self._now(),
            **extra,
        }
        with self.pending_events_path.open("a", encoding="utf-8") as pending:
            pending.write(f"{self._now()} phase9-router-fallback {json.dumps(event, ensure_ascii=False, sort_keys=True)}\n")

    def _spawn(
        self,
        prompt: Path,
        log_path: Path,
        *,
        route: str = "design-consensus-direct",
        reason: str = "phase9-router direct route",
    ) -> bool:
        if self.dry_run:
            return False
        intent = {
            "intent_id": self._intent_id_for_log(log_path),
            "source": "phase9-router",
            "route": route,
            "task_id": log_path.stem,
            "priority": "p1",
            "command": "spawn-codex",
            "controller_action": "spawn_codex_harness_background",
            "cd": str(self.ctx.repo_root.resolve()),
            "prompt": self._artifact_path(prompt),
            "log": self._artifact_path(log_path),
            "stall": 5400,
            "reason": reason,
            "queued_at": self._now(),
            "run_in_background_required": True,
            "no_lifecycle_authority": True,
        }
        self.command_runner(intent)
        self._tick_dispatch_count += 1
        return True

    def _default_runner(self, intent: dict[str, object]) -> None:
        self.pending_events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.pending_events_path.open("a", encoding="utf-8") as pending:
            pending.write(f"{self._now()} HARNESS_SPAWN_INTENT {json.dumps(intent, ensure_ascii=False, sort_keys=True)}\n")

    def _intent_id_for_log(self, log_path: Path) -> str:
        identity = self._identity_from_path(log_path)
        if identity is None:
            return f"phase9-router:{log_path.stem}"
        return f"phase9-router:{identity.issue}:{identity.round}:{identity.actor}"

    def _log_tick_status(self, action: str) -> None:
        print(f"[{self._now()}] phase9-router: tick {action}", flush=True)

    def _write_prompt(self, issue: str, round_no: int, actor: str, body: str) -> Path:
        prompt = self.prompts_dir / f"phase9-issue{issue}-r{round_no}-{actor}.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text(body, encoding="utf-8")
        return prompt

    def _meta_judge_prompt(self, issue: str, round_no: int, markers: Iterable[Marker]) -> str | None:
        context = self._meta_judge_prompt_context(issue, round_no, markers)
        if context is None:
            return None
        template_path = self.skill_root / "prompts" / "meta-judge.md"
        try:
            prompt = MetaJudgePromptRenderer(template_path, host_env=self.ctx.host_env).render(context)
        except OSError:
            self._append_meta_judge_prompt_fallback_event(
                issue,
                round_no,
                "phase9-meta-judge-template-unavailable",
                template_path=self._skill_artifact_path(template_path),
            )
            return None
        prompt = self._issue_snapshot_preferred_text(issue, prompt)
        bindings = "\n".join(
            (
                f"ISSUE_NUMBER={context.issue}",
                f"WORK_UNIT_ID={context.work_unit_id}",
                f"CLUSTER_ID={context.work_unit_id}",
                f"WORK_UNIT_PRODUCER={context.work_unit_producer}",
                f"WORK_UNIT_SOURCE_REF={context.work_unit_source_ref}",
                f"CONVERGENCE_ROUND={context.round}",
                f"CONVERGENCE_ROUND_PLUS_ONE={context.convergence_round_plus_one}",
                f"SOLVER_MINIMAL_PATH={context.solver_paths['minimal']}",
                f"SOLVER_STRUCTURAL_PATH={context.solver_paths['structural']}",
                f"SOLVER_DELETE_PATH={context.solver_paths['delete']}",
                f"META_JUDGE_OUTPUT_PATH={context.output_path}",
                *projection_lines(context.transition_assessment),
            )
        )
        return (
            f"# {format_stage('design-consensus')} meta-judge\n\n"
            f"## Router template bindings\n\n{bindings}\n\n"
            f"{self._issue_source_snapshot_markdown(issue)}\n\n"
            f"## Validated transition projection\n\n{self._transition_projection_summary(context.transition_assessment)}\n\n"
            f"## Full meta-judge template\n\n{prompt}"
            + "\n\n## Router dispatch evidence\n\n"
            + f"Dispatch ledger evidence: .refactor-loop/phase9-router-ledger.jsonl key={self._key(issue, round_no, self._judge_role())}\n"
        )

    def _meta_judge_prompt_context(
        self,
        issue: str,
        round_no: int,
        markers: Iterable[Marker],
    ) -> MetaJudgePromptContext | None:
        solver_paths: dict[str, str] = {}
        for marker in markers:
            if marker.role is None:
                return self._fail_meta_judge_scope(issue, round_no, "missing solver role")
            if marker.role not in self._solver_roles():
                return self._fail_meta_judge_scope(issue, round_no, f"unexpected solver role {marker.role}")
            identity = self._identity_from_path(marker.log_path)
            if identity is None:
                return self._fail_meta_judge_scope(issue, round_no, f"unowned solver path {marker.log_path.name}")
            if identity.issue != issue or identity.round != round_no or identity.actor != marker.role:
                return self._fail_meta_judge_scope(issue, round_no, f"solver path scope mismatch {marker.log_path.name}")
            solver_paths[marker.role] = self._artifact_path(marker.log_path)
        if set(solver_paths) != set(self._solver_roles()):
            return self._fail_meta_judge_scope(issue, round_no, "missing solver path")
        output_path = self._artifact_path(self.ctx.paths.runs / f"phase9-issue{issue}-r{round_no}-judge.md")
        return MetaJudgePromptContext(
            issue=issue,
            round=round_no,
            solver_paths=solver_paths,
            output_path=output_path,
            transition_assessment=self._transition_assessment(issue),
        )

    def _fail_meta_judge_scope(self, issue: str, round_no: int, detail: str) -> None:
        self._append_meta_judge_prompt_fallback_event(
            issue,
            round_no,
            "phase9-meta-judge-scope-invalid",
            detail=detail,
        )

    def _solver_prompt(self, issue: str, round_no: int, role: str, marker: str) -> str:
        prompt = self._issue_snapshot_preferred_text(issue, self._render_solver_template(issue, round_no, role))
        return (
            f"# {format_stage('design-consensus')} {role} solver\n\n"
            f"{self._solver_work_unit_header(issue, round_no, role)}\n\n"
            f"{self._issue_source_snapshot_markdown(issue)}\n\n"
            f"Convergence marker: {marker}\n\n"
            f"## Full solver template\n\n{prompt}\n"
        )

    def _render_solver_template(self, issue: str, round_no: int, role: str) -> str:
        template_path = self.skill_root / "prompts" / f"solver-{role}.md"
        template = template_path.read_text(encoding="utf-8")
        values = {
            "ISSUE_NUMBER": issue,
            "CLUSTER_ID": f"issue-{issue}",
            "CONVERGENCE_ROUND": str(round_no),
            "SOLVER_OUTPUT_PATH": f".refactor-loop/runs/phase9-issue{issue}-r{round_no}-{role}.md",
        }
        return render_prompt_text(
            template,
            skill_root=self.skill_root,
            values=values,
            host_env=self.ctx.host_env,
        )

    def _solver_work_unit_header(self, issue: str, round_no: int, role: str) -> str:
        output_path = f".refactor-loop/runs/phase9-issue{issue}-r{round_no}-{role}.md"
        transition_lines = "\n".join(projection_lines(self._transition_assessment(issue)))
        snapshot = self._read_issue_source_snapshot(issue)
        scope_spec_instruction = (
            "Use the router-injected issue source snapshot as the scope spec when no local audit artifact is provided. "
        )
        if snapshot.unavailable_reason:
            source_instruction = scope_spec_instruction + (
                "The router-injected issue source snapshot is unavailable. "
                f"Read `gh issue view {issue}` as fallback; do not fabricate audit artifacts."
            )
        else:
            source_instruction = scope_spec_instruction + (
                "Do not fabricate audit artifacts."
            )
        return (
            f"Issue: #{issue}\n"
            f"Round: {round_no}\n"
            f"Role: {role}\n"
            f"WORK_UNIT_ID=issue-{issue}\n"
            f"CLUSTER_ID=issue-{issue} (compatibility alias only; not an audit cluster_id)\n"
            "WORK_UNIT_KIND=manual-work-unit\n"
            "WORK_UNIT_PRODUCER=manual-issue (prompt-only provenance)\n"
            f"WORK_UNIT_SOURCE_REF=gh-issue-{issue}\n"
            f"{transition_lines}\n"
            f"SOLVER_OUTPUT_PATH={output_path}\n"
            f"{source_instruction}"
        )

    def _issue_snapshot_preferred_text(self, issue: str, text: str) -> str:
        if self._read_issue_source_snapshot(issue).unavailable_reason:
            return text
        replacements = (
            (f"`gh issue view {issue}` — original cluster spec + maintainer comments", "router-injected issue source snapshot — original cluster spec + maintainer comments"),
            (f"`gh issue view {issue}` issue body/comments", "router-injected issue source snapshot"),
            (f"`gh issue view {issue}`", "router-injected issue source snapshot"),
            (f"gh issue view {issue} issue body/comments", "router-injected issue source snapshot"),
            (f"gh issue view {issue}", "router-injected issue source snapshot"),
        )
        rendered = text
        for old, new in replacements:
            rendered = rendered.replace(old, new)
        return rendered

    def _transition_assessment(self, issue: str) -> TransitionAssessment:
        return TransitionAssessmentReader.load_for_work_unit(
            self.repo_root,
            work_unit_id=f"issue-{issue}",
            source_ref=f"gh-issue-{issue}",
        )

    def _transition_projection_summary(self, assessment: TransitionAssessment) -> str:
        return "\n".join(
            (
                "Use only this router-validated transition projection; missing/malformed/untrusted sidecars are unknown.",
                f"- transition_type: {assessment.transition_type}",
                f"- confidence: {assessment.confidence:g}",
                f"- evidence_refs: {assessment.evidence_refs_text}",
            )
        )

    def _reflector_prompt(self, marker: Marker) -> str:
        template = self._stalled_reflector_template()
        evidence_lines = "\n".join(self._stalled_evidence_lines(marker.issue, marker.round))
        return (
            f"# {format_stage('design-consensus')} stalled reflector\n\nIssue: #{marker.issue}\nRound: {marker.round}\n"
            f"Stalled marker: {marker.marker}\n\n"
            f"## Solver log evidence\n\n{evidence_lines}\n\n"
            f"## Stalled reflector template\n\n{template}\n"
        )

    def _stalled_reflector_template(self) -> str:
        template_path = self.skill_root / "prompts" / "meta-reflector-stalled.md"
        try:
            return template_path.read_text(encoding="utf-8")
        except OSError as exc:
            return (
                f"FATAL: missing stalled reflector template: {self._skill_artifact_path(template_path)}\n"
                f"Reason: {exc}\n"
                "Do not infer a fallback route. Emit META_RESOLVED:escalate-human:missing-stalled-reflector-template\n"
            )

    def _stalled_evidence_lines(self, issue: str, round_no: int) -> list[str]:
        lines = []
        for r in range(round_no - 2, round_no + 1):
            for role in self._solver_roles():
                paths = self._solver_history_log_paths(issue, r, role)
                artifact_paths = " or ".join(self._artifact_path(path) for path in paths)
                signatures = self._solver_no_progress_signatures_for_paths(role, paths)
                signature_text = ", ".join(sorted(signatures)) if signatures else "unavailable"
                lines.append(f"- r{r} {role}: {artifact_paths}; normalized_no_progress_signature={signature_text}")
        return lines

    def _solver_no_progress_signatures_for_paths(self, role: str, paths: tuple[Path, ...]) -> set[str]:
        signatures: set[str] = set()
        for path in paths:
            if not self._is_clean_exit(path):
                continue
            for marker in self._collect_markers_from_path(path):
                if not marker.startswith(f"SOLVER_DONE:{role}:"):
                    continue
                signature = self._solver_no_progress_signature(marker)
                if signature is not None:
                    signatures.add(signature)
        return signatures

    def _log_path(self, issue: str, round_no: int, actor: str) -> Path:
        return self.logs_dir / f"phase9-issue{issue}-r{round_no}-{actor}.log"

    def _solver_prompt_path(self, issue: str, round_no: int, role: str) -> Path:
        return self.prompts_dir / f"phase9-issue{issue}-r{round_no}-{role}.md"

    def _solver_history_log_paths(self, issue: str, round_no: int, role: str) -> tuple[Path, Path]:
        return (
            self._log_path(issue, round_no, role),
            self.logs_dir / f"solver-issue{issue}-r{round_no}-{role}.log",
        )

    def _solver_prompt_record(self, issue: str, round_no: int, role: str) -> dict[str, str]:
        prompt_path = self._solver_prompt_path(issue, round_no, role)
        return {
            "role": role,
            "prompt_path": self._artifact_path(prompt_path),
            "status": "present" if prompt_path.exists() else "missing",
        }

    def _solver_triplet_ledger_fields(
        self,
        issue: str,
        round_no: int,
        markers: Iterable[Marker],
        judge_prompt: Path,
    ) -> dict[str, object]:
        sorted_markers = sorted(markers, key=lambda marker: marker.role or "")
        return {
            "route": "solver_triplet_to_judge",
            "issue": issue,
            "round": round_no,
            "target_actor": self._judge_role(),
            "clean_exit_solver_logs": [
                {
                    "role": marker.role or "",
                    "log_path": self._artifact_path(marker.log_path),
                    "dialect": (self._identity_from_path(marker.log_path) or Phase9LogIdentity(issue, round_no, self._judge_role(), "phase9")).dialect,
                    "marker": marker.marker,
                }
                for marker in sorted_markers
            ],
            "solver_input_prompts": [self._solver_prompt_record(issue, round_no, role) for role in sorted(self._solver_roles())],
            "judge_input_solver_logs": [self._artifact_path(marker.log_path) for marker in sorted_markers],
            "judge_prompt_path": self._artifact_path(judge_prompt),
            "judge_prompt_template_path": "prompts/meta-judge.md",
            "judge_prompt_scope": {
                "issue": issue,
                "round": round_no,
                "solver_roles": sorted(self._solver_roles()),
            },
            "independence_check": "pass",
        }

    def _design_issue_intake_ledger_fields(
        self,
        issue: str,
        role: str,
        item: DesignConsensusIssue,
        prompt: Path,
    ) -> dict[str, object]:
        return {
            "route": "design_consensus_issue_intake",
            "issue": issue,
            "round": 1,
            "target_actor": role,
            "source_issue": f"gh-issue-{issue}",
            "source_labels": sorted(label_catalog.normalize_label_set(item.labels).canonical),
            "source_title": item.title,
            "solver_prompt_path": self._artifact_path(prompt),
            "solver_roles": sorted(self._solver_roles()),
            "source_open_gate": "pass",
        }

    def _peer_solver_reference_violation(self, issue: str, round_no: int) -> dict[str, str] | None:
        for role in sorted(self._solver_roles()):
            prompt_path = self._solver_prompt_path(issue, round_no, role)
            try:
                prompt = prompt_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                continue
            except OSError:
                continue
            scanned = self._peer_reference_scan_region(prompt)
            for peer_role in sorted(set(self._solver_roles()) - {role}):
                for token in self._peer_solver_reference_tokens(issue, round_no, peer_role):
                    if token in scanned:
                        return {
                            "role": role,
                            "peer_role": peer_role,
                            "prompt_path": self._artifact_path(prompt_path),
                            "matched_token": token,
                        }
        return None

    def _peer_reference_scan_region(self, prompt: str) -> str:
        """Return only the router-controlled regions of a solver prompt for
        peer-isolation scanning. The router-injected issue source snapshot is
        issue-author content and may legitimately quote prior-round peer solver
        log/run paths (e.g. a previous round's audit trail that was posted back
        onto the GitHub issue body/comments). Quoting those paths inside the
        snapshot is not an isolation breach and must not block judge dispatch;
        only a peer reference the router itself emits into the header or the
        solver template is a real violation, so the snapshot region between
        '## Issue source snapshot' and '## Full solver template' is excluded."""
        start = prompt.find("## Issue source snapshot")
        if start == -1:
            return prompt
        end = prompt.find("## Full solver template", start)
        if end == -1:
            return prompt[:start]
        return prompt[:start] + prompt[end:]

    def _peer_solver_reference_tokens(self, issue: str, round_no: int, peer_role: str) -> tuple[str, ...]:
        return (
            f".refactor-loop/logs/phase9-issue{issue}-r{round_no}-{peer_role}.log",
            f".refactor-loop/logs/solver-issue{issue}-r{round_no}-{peer_role}.log",
            f".refactor-loop/prompts/phase9/phase9-issue{issue}-r{round_no}-{peer_role}.md",
            f".refactor-loop/runs/phase9-issue{issue}-r{round_no}-{peer_role}.md",
        )

    def _artifact_path(self, path: Path) -> str:
        return self.ctx.durable_artifact_path(path)

    def _skill_artifact_path(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.skill_root.resolve()).as_posix()
        except ValueError:
            return path.name

    def _stored_artifact_path_key(self, text: str) -> str:
        if Path(text).is_absolute():
            try:
                return self._artifact_path(Path(text))
            except Exception:
                return text
        try:
            return self._artifact_path(self.ctx.artifact_execution_path(text))
        except Exception:
            return text

    def _key(self, issue: str, round_no: int, actor: str) -> str:
        return f"{issue}-{round_no}-{actor}"

    def _now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _solver_roles(self) -> tuple[str, ...]:
        # Phase9 direct-spawn-intent ignores HostWorkflowSpec role/dispatch/policy data entirely.
        return ROLES

    def _judge_role(self) -> str:
        return JUDGE_ROLE

    def _role_from_solver_marker(self, marker: str) -> str:
        return marker.split(":", 2)[1]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="phase9-router compatibility alias for the design-consensus router daemon"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--daemon", action="store_true", help="run persistently")
    mode.add_argument("--once", action="store_true", help="run one scan and exit")
    parser.add_argument("--dry-run", action="store_true", help="do not spawn codex workers")
    parser.add_argument("--repo-root", help="absolute host repository root")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("INTERVAL", "30")))
    return parser.parse_args(argv)


def run_phase9_router_reconcile_tick(router: Phase9Router) -> None:
    router.tick()


def main(argv: list[str] | None = None, command_runner: Callable[[dict[str, object]], None] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    repo_root = Path(args.repo_root) if args.repo_root else LoopContext.load(cwd=os.getcwd()).repo_root
    if not repo_root.is_absolute():
        raise SystemExit("--repo-root must be absolute")
    router = Phase9Router(repo_root, dry_run=args.dry_run, command_runner=command_runner)
    lease = DaemonHeartbeatLease("phase9_router_daemon", repo_root, singleton=True)
    with lease.daemon_lifetime():
        if args.once:
            run_phase9_router_reconcile_tick(router)
            return 0
        lease.beat()
        while True:
            try:
                lease.run_tick(lambda: run_phase9_router_reconcile_tick(router))
            except Exception as exc:
                print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] EXCEPTION in tick: {exc!r}", flush=True)
            lease.sleep_with_lease(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
