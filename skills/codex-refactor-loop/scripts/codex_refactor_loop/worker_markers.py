"""Shared clean-exit worker terminal marker reader."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


MARKER_TAIL_LINES = 30
DONE_PREFIXES = (
    "AUDIT_DONE",
    "SOLVER_DONE",
    "META_JUDGE_DONE",
    "META_RESOLVED",
    "IMPLEMENT_DONE",
    "VERIFY_DONE",
    "REVIEW_DONE",
    "FIX_DONE",
    "TEST_ADD_DONE",
    "TRIAGE_DECISION_DONE",
)
DONE_PREFIX_RE = re.compile(r"^(?:" + "|".join(re.escape(prefix) for prefix in DONE_PREFIXES) + r")(?::[^\s`]+)*$")
IMPLEMENT_LOG_RE = re.compile(r"^implement-(?:issue-)?[A-Za-z0-9._-]+\.log$")
SOLVER_LOG_RE = re.compile(r"^(?:" + "phase9-" + r"|solver-)issue[1-9][0-9]*-r[1-9][0-9]*-(?:minimal|structural|delete)\.log$")
JUDGE_LOG_RE = re.compile(r"^" + "phase9-" + r"issue[1-9][0-9]*-r[1-9][0-9]*-judge\.log$")
REVIEW_LOG_RE = re.compile(r"^" + "review-" + r"pr[1-9][0-9]*-[A-Za-z][A-Za-z0-9_-]*-r[1-9][0-9]*\.log$")


@dataclass(frozen=True)
class WorkerMarkerRead:
    marker: str = ""
    source: str = ""
    reason: str = ""

    @property
    def found(self) -> bool:
        return bool(self.marker)


def read_worker_terminal_marker(log_path: Path) -> WorkerMarkerRead:
    lines = _read_lines(log_path)
    if lines is None:
        return WorkerMarkerRead(reason="log_unreadable")
    if _terminal_exit_line(lines) != "EXIT=0":
        return WorkerMarkerRead(reason="missing_exit_zero")
    log_marker = _marker_from_clean_log_lines(lines)
    if log_marker.found or log_marker.reason:
        return log_marker
    artifact_marker = _marker_from_companion_artifact(log_path)
    if artifact_marker.found or artifact_marker.reason:
        return artifact_marker
    return WorkerMarkerRead(reason="marker_missing")


def log_has_clean_exit(log_path: Path) -> bool:
    lines = _read_lines(log_path)
    return lines is not None and _terminal_exit_line(lines) == "EXIT=0"


def extract_standalone_marker(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("+") and not stripped.startswith("+++"):
        stripped = stripped[1:].strip()
    stripped = stripped.strip("`")
    if not stripped:
        return ""
    if "<" in stripped and ">" in stripped:
        return ""
    if any(stripped.startswith(f"{prefix}:") for prefix in DONE_PREFIXES):
        return stripped
    if DONE_PREFIX_RE.fullmatch(stripped):
        return stripped
    return ""


def _marker_from_clean_log_lines(lines: list[str]) -> WorkerMarkerRead:
    try:
        exit_index = max(index for index, line in enumerate(lines) if line.strip() == "EXIT=0")
    except ValueError:
        return WorkerMarkerRead()
    before_exit = lines[:exit_index]
    tail = before_exit[-MARKER_TAIL_LINES:]
    marker = _last_final_marker(tail)
    if marker.found or marker.reason:
        return marker
    return _sentinel_adjacent_marker(tail)


def _last_final_marker(lines: list[str]) -> WorkerMarkerRead:
    final_marker = ""
    for index in range(len(lines) - 1, -1, -1):
        stripped = lines[index].strip()
        if not stripped:
            continue
        final_marker = extract_standalone_marker(stripped)
        if not final_marker:
            return WorkerMarkerRead()
        earlier = [
            marker
            for marker in (extract_standalone_marker(line) for line in lines[:index])
            if marker
        ]
        if earlier:
            return WorkerMarkerRead(reason="duplicate_or_conflicting_log_marker")
        return WorkerMarkerRead(marker=final_marker, source="log")
    return WorkerMarkerRead()


def _sentinel_adjacent_marker(lines: list[str]) -> WorkerMarkerRead:
    all_markers = [marker for marker in (extract_standalone_marker(line) for line in lines) if marker]
    markers: list[str] = []
    for index, line in enumerate(lines):
        if "⟦AI:AUTO-LOOP⟧" not in line:
            continue
        for candidate in lines[index + 1 : index + 4]:
            marker = extract_standalone_marker(candidate)
            if marker:
                markers.append(marker)
    unique = set(markers)
    if len(markers) == 1 and len(unique) == 1 and len(all_markers) == 1:
        return WorkerMarkerRead(marker=markers[0], source="log")
    if markers or all_markers:
        return WorkerMarkerRead(reason="duplicate_or_conflicting_log_marker")
    return WorkerMarkerRead()


def _marker_from_companion_artifact(log_path: Path) -> WorkerMarkerRead:
    allowed_prefixes = _artifact_allowed_prefixes(log_path.name)
    if not allowed_prefixes:
        return WorkerMarkerRead()
    artifact = log_path.parent.parent / "runs" / f"{log_path.stem}.md"
    lines = _read_lines(artifact)
    if lines is None:
        return WorkerMarkerRead()
    all_markers = [marker for marker in (extract_standalone_marker(line) for line in lines[-MARKER_TAIL_LINES:]) if marker]
    markers = [marker for marker in all_markers if marker.startswith(allowed_prefixes)]
    if all_markers and len(markers) != len(all_markers):
        return WorkerMarkerRead(reason="duplicate_or_conflicting_artifact_marker")
    unique = set(markers)
    if len(markers) == 1 and len(unique) == 1:
        return WorkerMarkerRead(marker=markers[-1], source="artifact")
    if markers:
        return WorkerMarkerRead(reason="duplicate_or_conflicting_artifact_marker")
    return WorkerMarkerRead()


def _artifact_allowed_prefixes(name: str) -> tuple[str, ...]:
    if IMPLEMENT_LOG_RE.fullmatch(name):
        return ("IMPLEMENT_DONE:",)
    if SOLVER_LOG_RE.fullmatch(name):
        return ("SOLVER_DONE:",)
    if JUDGE_LOG_RE.fullmatch(name):
        return ("META_JUDGE_DONE:",)
    if REVIEW_LOG_RE.fullmatch(name):
        return ("REVIEW_DONE:",)
    return ()


def _terminal_exit_line(lines: list[str]) -> str:
    for line in reversed(lines[-20:]):
        stripped = line.strip()
        if stripped.startswith("EXIT="):
            return stripped
    return ""


def _read_lines(path: Path) -> list[str] | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
