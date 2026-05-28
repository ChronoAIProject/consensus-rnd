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
from typing import Callable, Iterable, Literal, cast


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
# Refactor (iter4/skill-router-fallback-flood-fix): Old pattern: 接受任意 marker payload,
# 把含 `|` / `\"` / `*` / `r+1` / 反斜杠的 regex 片段当真 marker -> 每个旧 log 灌噪声进
# pending-events。 New principle: 真实 marker 的 prefix + suffix 段只含 [A-Za-z0-9_./\-]
# 及有限标点;含其他特殊字符即视为 prompt/regex 回显,拒绝(per 2026-05-26 maintainer-directive)。
VALID_MARKER_PAYLOAD = re.compile(r"^[A-Z][A-Z0-9_]*:[A-Za-z0-9_./\-]+(?::[A-Za-z0-9_./\-]+)*$")


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
        # Refactor (iter4/skill-router-fallback-flood-fix): Old pattern: 内存 only -> daemon
        # 重启即丢失,re-emit 历史 fallback 灌爆 Monitor。 New principle: __init__ 扫已存在
        # pending-events 中的 phase9-router-fallback 行 seed dedup set,跨重启幂等。
        self._fallback_seen: set[str] = self._load_persisted_fallback_seen()

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

    # Refactor (iter5/skill-marker-tail-only-scope):
    #   Old pattern: scan entire log body for markers; codex worker logs that
    #   happen to echo prompt-body / test-fixture / grep-output marker text
    #   (e.g. `META_JUDGE_DONE:converge:round-3:echoed-from-prompt-body` from
    #   test_phase9_router_daemon.py source listing) were classified as real
    #   verdicts and triggered cascading dispatches.
    #   New principle: real worker verdict markers always appear in the tail
    #   alongside `EXIT=0`. Scan only the last MARKER_TAIL_LINES of each log.
    #   Body-position prompt-body echoes never reach the marker parser.
    MARKER_TAIL_LINES = 30
    TAIL_READ_BYTES = 8192  # Refactor (iter5/issue122-phase9-tail-perf): bound tail read to ~8KB so per-tick scan stays O(num_logs), not O(total log bytes).

    @staticmethod
    def _read_tail_lines(path: Path, num_lines: int) -> list[str]:
        # Refactor (iter5/issue122-phase9-tail-perf): Old: read full log via
        # read_text() then splitlines()[-N:]. New: seek to file end and read
        # only the last TAIL_READ_BYTES, decode, return tail num_lines. Keeps
        # per-tick CPU bounded as logs/ grows.
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
            tail = self._read_tail_lines(log_path, self.MARKER_TAIL_LINES)
            for line in tail:
                marker = self._extract_marker(line)
                if marker is None:
                    continue
                role: str | None = identity.actor
                if marker.startswith("SOLVER_DONE:"):
                    role = marker.split(":", 2)[1]
                    if role not in ROLES:
                        continue
                markers.append(Marker(marker, log_path, identity.issue, identity.round, role))
        return markers

    def _extract_marker(self, line: str) -> str | None:
        stripped = line.strip().strip("`")
        if self._is_placeholder_or_echo(stripped):
            return None
        candidate: str | None = None
        for prefix in KNOWN_PREFIXES:
            index = stripped.find(prefix)
            if index == -1:
                continue
            candidate = stripped[index:].split()[0].rstrip("`.,);:|\"\\")
            break
        if candidate is None:
            match = MARKER_RE.search(stripped)
            if match:
                candidate = match.group(0).rstrip("`.,);:|\"\\")
        if candidate is None:
            return None
        if not VALID_MARKER_PAYLOAD.match(candidate):
            return None
        return candidate

    def _is_placeholder_or_echo(self, text: str) -> bool:
        if "<" in text and ">" in text:
            return True
        lowered = text.lower()
        if "template" in lowered or "example" in lowered or "emits `" in lowered:
            return True
        if "round-n" in lowered:
            return True
        # Refactor (iter4/skill-router-fallback-flood-fix): regex/grep alternation 或
        # template 占位的常见特征:`|` 选择、`\"` 转义引号、`r+1` 占位、`*` 通配。这些行
        # 几乎一定是 prompt 模板或 grep 命令的 marker 引用,不是真 codex 输出。
        if "|" in text and any(prefix in text for prefix in KNOWN_PREFIXES):
            return True
        if "\\\"" in text or '\\"' in text:
            return True
        if "r+1" in text or "round-k+" in lowered or "round-n+" in lowered:
            return True
        if any(f"{prefix}*" in text or f"{prefix}:*" in text for prefix in KNOWN_PREFIXES):
            return True
        return False

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
            if idx == -1:
                continue
            payload = line[idx + len("phase9-router-fallback"):].strip()
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            log_path = event.get("log_path")
            if isinstance(log_path, str):
                seen.add(f"fallback:{log_path}")
        return seen

    def _identity_from_path(self, path: Path) -> Phase9LogIdentity | None:
        # Refactor (issue-100/router-filename-identity): Old pattern: one loose regex
        # accepted non-owned Phase 9-ish names. New principle: router-private filename
        # identity allowlist accepts only phase9-issue, solver-issue, and meta-judge-issue
        # dialects; public markers remain role-local.
        return parse_phase9_log_identity(path.name)

    def _is_clean_exit(self, path: Path) -> bool:
        # Refactor (iter5/issue122-phase9-tail-perf): tail-only read via
        # _read_tail_lines bounds CPU as logs/ grows.
        tail = self._read_tail_lines(path, 5)
        if not tail:
            return False
        return any(re.match(r"^EXIT=0$", line) for line in tail)

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
            if key in ledger or self._in_flight(log_path) or self._equivalent_actor_log_exists(issue, round_no, "judge"):
                continue
            prompt = self._write_prompt(issue, round_no, "judge", self._meta_judge_prompt(issue, round_no, role_markers.values()))
            if self._spawn(prompt, log_path):
                self._append_ledger(key, "SOLVER_DONE:triplet", log_path)
                ledger.add(key)

    def _dispatch_meta_judge_routes(self, markers: list[Marker], ledger: set[str]) -> None:
        # Refactor (iter5/skill-converge-source-and-monotonic-guard):
        #   Old pattern: any log with `META_JUDGE_DONE:converge:round-N` marker
        #   could trigger solver dispatch, and `target_round` was accepted even
        #   if it equaled or preceded the source log's round. Result: solver
        #   logs echoing prompt-body marker examples plus judge-self-referential
        #   verdicts spawned cascading r3..r8 solver rounds with judge gaps.
        #   New principle: only judge-role source logs may authorize a converge
        #   dispatch (JUDGE markers come from JUDGE logs), and `target_round`
        #   must be strictly greater than the source round (monotonic).
        for marker in markers:
            if marker.marker.startswith("META_JUDGE_DONE:converge:round-"):
                if marker.role != "judge":
                    continue
                target_round = self._round_from_converge(marker.marker)
                if target_round is None:
                    continue
                if target_round <= marker.round:
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
                if marker.role != "judge":
                    continue
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
        # Refactor (iter4/skill-router-fallback-flood-fix): Old pattern: dedup 用
        # (log_path, marker) 二元组,但 marker 文本随 extract 规则微调而变 -> 跨版本/
        # 跨重启不稳。 New principle: dedup 仅按 log_path —— 一旦某 log 的任意 marker
        # 已 surface 给 controller,该 log 后续 marker 不再重 emit;controller 若需细节,
        # 可直接读 log 自取。这一改让 dedup 跨版本稳健。
        for marker in markers:
            if self._directly_handled(marker, ledger):
                continue
            if marker.marker.startswith("SOLVER_DONE:"):
                continue
            event_key = f"fallback:{marker.log_path}"
            if event_key in self._fallback_seen:
                continue
            self._fallback_seen.add(event_key)
            self._append_pending_event(marker)

    def _directly_handled(self, marker: Marker, ledger: set[str]) -> bool:
        if marker.marker.startswith("META_JUDGE_DONE:converge:round-"):
            if marker.role != "judge":
                return False
            target_round = self._round_from_converge(marker.marker)
            if target_round is None or target_round <= marker.round:
                return False
            return all(self._key(marker.issue, target_round, role) in ledger for role in ROLES)
        if marker.marker.startswith("META_JUDGE_DONE:escalate:stalled:"):
            if marker.role != "judge":
                return False
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
                role_verdicts: set[str] = set()
                for path in self._solver_history_log_paths(issue, r, role):
                    if not self._is_clean_exit(path):
                        continue
                    for marker in self._collect_markers_from_path(path):
                        if marker.startswith(f"SOLVER_DONE:{role}:"):
                            role_verdicts.add(self._solver_verdict_text(marker))
                if not role_verdicts:
                    return False
                verdicts.update(role_verdicts)
            if not verdicts:
                return False
            recent.append(verdicts)
        return recent[0] == recent[1] == recent[2]

    def _collect_markers_from_path(self, path: Path) -> list[str]:
        # Refactor (iter5/skill-marker-tail-only-scope): same tail-only invariant
        # as _collect_markers — _stalled_predicate_holds must not trust body-
        # position SOLVER_DONE echoes when classifying convergence verdicts.
        # Refactor (iter5/issue122-phase9-tail-perf): tail-only read via
        # _read_tail_lines bounds CPU as logs/ grows.
        if not path.exists():
            return []
        tail = self._read_tail_lines(path, self.MARKER_TAIL_LINES)
        markers: list[str] = []
        for line in tail:
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
        try:
            ps = subprocess.run(["ps", "-eo", "command="], capture_output=True, text=True, check=False)
        except OSError:
            return False
        target = str(log_path)
        for line in ps.stdout.splitlines():
            if "spawn-codex.sh" in line and target in line and " -c " not in line:
                return True
        return False

    def _equivalent_actor_log_exists(self, issue: str, round_no: int, actor: str) -> bool:
        paths = [self._log_path(issue, round_no, actor)]
        if actor in ROLES:
            paths.append(self.logs_dir / f"solver-issue{issue}-r{round_no}-{actor}.log")
        if actor == "judge":
            paths.append(self.logs_dir / f"meta-judge-issue{issue}-r{round_no}.log")
        return any(path.exists() for path in paths)

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

    # Refactor (iter5/issue-85-stalled-reflector-template):
    #   Old pattern: generic 3-line fallback reflector prompt(无 template body / 无 solver evidence)
    #   New principle: 嵌入完整 meta-reflector-stalled.md template + 9 个 solver log path evidence;
    #                  template 缺失 fail closed(显式 missing-template prompt 含 META_RESOLVED:escalate-human)
    def _reflector_prompt(self, marker: Marker) -> str:
        template = self._stalled_reflector_template()
        evidence_lines = "\n".join(self._stalled_evidence_lines(marker.issue, marker.round))
        return (
            f"# Phase 9 stalled reflector\n\nIssue: #{marker.issue}\nRound: {marker.round}\n"
            f"Stalled marker: {marker.marker}\n\n"
            f"## Solver log evidence\n\n{evidence_lines}\n\n"
            f"## Stalled reflector template\n\n{template}\n"
        )

    def _stalled_reflector_template(self) -> str:
        template_path = Path(__file__).resolve().parents[1] / "prompts" / "meta-reflector-stalled.md"
        try:
            return template_path.read_text(encoding="utf-8")
        except OSError as exc:
            return (
                f"FATAL: missing stalled reflector template: {template_path}\n"
                f"Reason: {exc}\n"
                "Do not infer a fallback route. Emit META_RESOLVED:escalate-human:missing-stalled-reflector-template\n"
            )

    def _stalled_evidence_lines(self, issue: str, round_no: int) -> list[str]:
        lines = []
        for r in range(round_no - 2, round_no + 1):
            for role in ROLES:
                paths = " or ".join(str(path) for path in self._solver_history_log_paths(issue, r, role))
                lines.append(f"- r{r} {role}: {paths}")
        return lines

    def _log_path(self, issue: str, round_no: int, actor: str) -> Path:
        return self.logs_dir / f"phase9-issue{issue}-r{round_no}-{actor}.log"

    def _solver_history_log_paths(self, issue: str, round_no: int, role: str) -> tuple[Path, Path]:
        return (
            self._log_path(issue, round_no, role),
            self.logs_dir / f"solver-issue{issue}-r{round_no}-{role}.log",
        )

    def _key(self, issue: str, round_no: int, actor: str) -> str:
        return f"{issue}-{round_no}-{actor}"

    def _now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
            router.tick()
            return 0
        while True:
            router.tick()
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
