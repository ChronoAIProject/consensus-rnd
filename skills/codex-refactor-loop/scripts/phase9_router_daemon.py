#!/usr/bin/env python3
# Refactor (iter3/skill-daemon-first-refactor): Old pattern: 所有 Phase 9 route 由 LLM controller 手动派(易漏 marker). New principle: narrow allowlist daemon 直接派 SOLVER_DONE triplet/converge/stalled,其余 marker append fallback event(#37 structural B 共识)。
"""Narrow Phase 9 deterministic router daemon.

This daemon owns only three Phase 9 direct-dispatch routes:
solver triplet -> meta-judge, converge -> next solver triplet, and valid
stalled -> reflector. Every other marker is forwarded to the existing
controller pending-event file without spawning.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, TextIO


ROLES = ("minimal", "structural", "delete")
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
KNOWN_PREFIXES = (
    "SOLVER_DONE:",
    "META_JUDGE_DONE:",
    *LIFECYCLE_PREFIXES,
)
MARKER_RE = re.compile(r"\b(?:[A-Z][A-Z0-9_]*_(?:DONE|RESOLVED|BLOCKED)|META_JUDGE_DONE):[^\s`]+")


@dataclass(frozen=True)
class Marker:
    marker: str
    log_path: Path
    issue: str
    round: int
    role: str | None = None


class Phase9Router:
    def __init__(
        self,
        repo_root: Path,
        *,
        dry_run: bool = False,
        command_runner: Callable[[list[str]], None] | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.dry_run = dry_run
        self.loop_dir = self.repo_root / ".refactor-loop"
        self.logs_dir = self.loop_dir / "logs"
        self.prompts_dir = self.loop_dir / "prompts" / "phase9"
        self.ledger_path = self.loop_dir / "phase9-router-ledger.jsonl"
        self.pending_events_path = self.loop_dir / ".controller-pending-events.log"
        self.lock_path = self.loop_dir / "phase9-router.lock"
        self.spawn_codex = Path(__file__).resolve().parent / "spawn-codex.sh"
        self.command_runner = command_runner or self._default_runner
        self._fallback_seen: set[str] = set()

    def tick(self) -> None:
        self.loop_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        ledger = self._read_ledger()
        markers = self._collect_markers()
        self._dispatch_solver_triplets(markers, ledger)
        self._dispatch_meta_judge_routes(markers, ledger)
        self._append_fallbacks(markers, ledger)

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

    def _collect_markers(self) -> list[Marker]:
        markers: list[Marker] = []
        for log_path in sorted(self.logs_dir.glob("*.log")):
            if not self._is_clean_exit(log_path):
                continue
            issue, round_no, role_hint = self._identity_from_path(log_path)
            if issue is None or round_no is None:
                continue
            for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
                marker = self._extract_marker(line)
                if marker is None:
                    continue
                role = role_hint
                if marker.startswith("SOLVER_DONE:"):
                    role = marker.split(":", 2)[1]
                    if role not in ROLES:
                        continue
                markers.append(Marker(marker, log_path, issue, round_no, role))
        return markers

    def _extract_marker(self, line: str) -> str | None:
        stripped = line.strip().strip("`")
        if self._is_placeholder_or_echo(stripped):
            return None
        for prefix in KNOWN_PREFIXES:
            index = stripped.find(prefix)
            if index == -1:
                continue
            marker = stripped[index:].split()[0]
            return marker.rstrip("`.,)")
        match = MARKER_RE.search(stripped)
        if match:
            return match.group(0).rstrip("`.,)")
        return None

    def _is_placeholder_or_echo(self, text: str) -> bool:
        if "<" in text and ">" in text:
            return True
        lowered = text.lower()
        if "template" in lowered or "example" in lowered or "emits `" in lowered:
            return True
        if "round-n" in lowered:
            return True
        return False

    def _identity_from_path(self, path: Path) -> tuple[str | None, int | None, str | None]:
        match = re.search(
            r"phase9[-_]?issue(?P<issue>\d+)[-_]r(?:ound-)?(?P<round>\d+)(?:[-_](?P<role>minimal|structural|delete|judge|reflector))?",
            path.name,
        )
        if not match:
            return None, None, None
        role = match.group("role")
        return match.group("issue"), int(match.group("round")), role

    def _is_clean_exit(self, path: Path) -> bool:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return False
        return any(re.match(r"^EXIT=0$", line) for line in lines[-5:])

    def _dispatch_solver_triplets(self, markers: list[Marker], ledger: set[str]) -> None:
        by_issue_round: dict[tuple[str, int], dict[str, Marker]] = {}
        for marker in markers:
            if not marker.marker.startswith("SOLVER_DONE:") or marker.role not in ROLES:
                continue
            by_issue_round.setdefault((marker.issue, marker.round), {})[marker.role] = marker

        for (issue, round_no), role_markers in by_issue_round.items():
            if set(role_markers) != set(ROLES):
                continue
            key = self._key(issue, round_no, "judge")
            log_path = self._log_path(issue, round_no, "judge")
            if key in ledger or self._in_flight(log_path):
                continue
            prompt = self._write_prompt(issue, round_no, "judge", self._meta_judge_prompt(issue, round_no, role_markers.values()))
            if self._spawn(prompt, log_path):
                self._append_ledger(key, "SOLVER_DONE:triplet", log_path)
                ledger.add(key)

    def _dispatch_meta_judge_routes(self, markers: list[Marker], ledger: set[str]) -> None:
        for marker in markers:
            if marker.marker.startswith("META_JUDGE_DONE:converge:round-"):
                target_round = self._round_from_converge(marker.marker)
                if target_round is None:
                    continue
                for role in ROLES:
                    key = self._key(marker.issue, target_round, role)
                    log_path = self._log_path(marker.issue, target_round, role)
                    if key in ledger or self._in_flight(log_path):
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
                key = self._key(marker.issue, marker.round, "reflector")
                log_path = self._log_path(marker.issue, marker.round, "reflector")
                if key in ledger or self._in_flight(log_path):
                    continue
                if not self._stalled_predicate_holds(marker.issue, marker.round):
                    continue
                prompt = self._write_prompt(marker.issue, marker.round, "reflector", self._reflector_prompt(marker))
                if self._spawn(prompt, log_path):
                    self._append_ledger(key, marker.marker, log_path)
                    ledger.add(key)

    def _append_fallbacks(self, markers: list[Marker], ledger: set[str]) -> None:
        for marker in markers:
            if self._directly_handled(marker, ledger):
                continue
            if marker.marker.startswith("SOLVER_DONE:"):
                continue
            event_key = f"fallback:{marker.log_path}:{marker.marker}"
            if event_key in self._fallback_seen:
                continue
            self._fallback_seen.add(event_key)
            self._append_pending_event(marker)

    def _directly_handled(self, marker: Marker, ledger: set[str]) -> bool:
        if marker.marker.startswith("META_JUDGE_DONE:converge:round-"):
            target_round = self._round_from_converge(marker.marker)
            return target_round is not None and all(self._key(marker.issue, target_round, role) in ledger for role in ROLES)
        if marker.marker.startswith("META_JUDGE_DONE:escalate:stalled:"):
            return self._key(marker.issue, marker.round, "reflector") in ledger
        return False

    def _round_from_converge(self, marker: str) -> int | None:
        match = re.match(r"META_JUDGE_DONE:converge:round-(\d+):", marker)
        return int(match.group(1)) if match else None

    def _stalled_predicate_holds(self, issue: str, round_no: int) -> bool:
        if round_no < 3:
            return False
        recent: list[set[str]] = []
        for r in range(round_no - 2, round_no + 1):
            verdicts: set[str] = set()
            for role in ROLES:
                path = self._log_path(issue, r, role)
                if not self._is_clean_exit(path):
                    return False
                for marker in self._collect_markers_from_path(path):
                    if marker.startswith(f"SOLVER_DONE:{role}:"):
                        verdicts.add(self._solver_verdict_text(marker))
            if not verdicts:
                return False
            recent.append(verdicts)
        return recent[0] == recent[1] == recent[2]

    def _collect_markers_from_path(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        markers: list[str] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            marker = self._extract_marker(line)
            if marker:
                markers.append(marker)
        return markers

    def _solver_verdict_text(self, marker: str) -> str:
        parts = marker.split(":", 3)
        return parts[2] if len(parts) >= 3 else marker

    def _in_flight(self, log_path: Path) -> bool:
        if log_path.exists():
            return True
        # Refactor (iter4/spawn-codex-pid-registry):
        #   Old pattern: ps -eo command | grep spawn-codex.sh --log <target>.
        #   New principle: spawned/<task-id>.pid is the per-repo source of truth for in-flight targets.
        reg_file = self.repo_root / ".refactor-loop" / "spawned" / f"{log_path.stem}.pid"
        return reg_file.exists()

    def _read_ledger(self) -> set[str]:
        keys: set[str] = set()
        if not self.ledger_path.exists():
            return keys
        for line in self.ledger_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = entry.get("key")
            if isinstance(key, str):
                keys.add(key)
        return keys

    def _append_ledger(self, key: str, marker: str, log_path: Path) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "key": key,
            "marker": marker,
            "log_path": str(log_path),
            "dispatched_at": self._now(),
        }
        with self.ledger_path.open("a", encoding="utf-8") as ledger:
            ledger.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")

    def _append_pending_event(self, marker: Marker) -> None:
        self.pending_events_path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "key": f"fallback:{marker.issue}-{marker.round}",
            "marker": marker.marker,
            "log_path": str(marker.log_path),
            "dispatched_at": self._now(),
        }
        with self.pending_events_path.open("a", encoding="utf-8") as pending:
            pending.write(f"{self._now()} phase9-router-fallback {json.dumps(event, ensure_ascii=False, sort_keys=True)}\n")

    def _spawn(self, prompt: Path, log_path: Path) -> bool:
        command = [
            str(self.spawn_codex),
            "--cd",
            str(self.repo_root),
            "--prompt",
            str(prompt),
            "--log",
            str(log_path),
            "--stall",
            "3600",
        ]
        if self.dry_run:
            return False
        self.command_runner(command)
        return True

    def _default_runner(self, command: list[str]) -> None:
        subprocess.Popen(
            ["nohup", *command],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def _write_prompt(self, issue: str, round_no: int, actor: str, body: str) -> Path:
        prompt = self.prompts_dir / f"phase9-issue{issue}-r{round_no}-{actor}.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text(body, encoding="utf-8")
        return prompt

    def _meta_judge_prompt(self, issue: str, round_no: int, markers: Iterable[Marker]) -> str:
        marker_lines = "\n".join(f"- {m.role}: {m.log_path}" for m in sorted(markers, key=lambda m: m.role or ""))
        return (
            f"# Phase 9 meta-judge\n\nIssue: #{issue}\nRound: {round_no}\n\n"
            f"Read the three completed solver logs and emit META_JUDGE_DONE.\n\n{marker_lines}\n"
        )

    def _solver_prompt(self, issue: str, round_no: int, role: str, marker: str) -> str:
        return (
            f"# Phase 9 {role} solver\n\nIssue: #{issue}\nRound: {round_no}\n"
            f"Convergence marker: {marker}\n\nUse prompts/solver-{role}.md contract and emit SOLVER_DONE:{role}:...\n"
        )

    def _reflector_prompt(self, marker: Marker) -> str:
        return (
            f"# Phase 9 stalled reflector\n\nIssue: #{marker.issue}\nRound: {marker.round}\n"
            f"Stalled marker: {marker.marker}\n\nReflect on the convergence failure and emit META_RESOLVED.\n"
        )

    def _log_path(self, issue: str, round_no: int, actor: str) -> Path:
        return self.logs_dir / f"phase9-issue{issue}-r{round_no}-{actor}.log"

    def _key(self, issue: str, round_no: int, actor: str) -> str:
        return f"{issue}-{round_no}-{actor}"

    def _now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_heartbeat(repo_root: Path) -> None:
    # Refactor (iter4/spawn-codex-pid-registry):
    #   Old pattern: controller health checked daemon names via process-table grep.
    #   New principle: daemon writes repo-local heartbeat timestamp; controller uses heartbeat-mtime <90s.
    heartbeat = repo_root / ".refactor-loop" / "heartbeats" / "phase9_router_daemon.py.ts"
    heartbeat.parent.mkdir(parents=True, exist_ok=True)
    heartbeat.write_text(f"{int(time.time())}\n", encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Narrow Phase 9 router daemon")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--daemon", action="store_true", help="run persistently")
    mode.add_argument("--once", action="store_true", help="run one scan and exit")
    parser.add_argument("--dry-run", action="store_true", help="do not spawn codex workers")
    parser.add_argument("--repo-root", required=True, help="absolute host repository root")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("INTERVAL", "30")))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, command_runner: Callable[[list[str]], None] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    repo_root = Path(args.repo_root)
    if not repo_root.is_absolute():
        raise SystemExit("--repo-root must be absolute")
    router = Phase9Router(repo_root, dry_run=args.dry_run, command_runner=command_runner)
    with router.singleton():
        if args.once:
            write_heartbeat(repo_root)
            router.tick()
            return 0
        while True:
            write_heartbeat(repo_root)
            router.tick()
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
