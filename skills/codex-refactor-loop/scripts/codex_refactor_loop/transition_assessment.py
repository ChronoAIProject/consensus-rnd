"""Read-only transition assessment sidecar projection."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SAFE_WORK_UNIT_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
ALLOWED_PRODUCERS = frozenset({"audit", "manual-issue"})
TRANSITION_BUCKET_ORDER = {
    "positive-discovery": 0,
    "classifier-shift": 1,
    "formal-hardening": 2,
    "ledger-repair": 3,
    "record-growth": 4,
    "unknown": 5,
}
TRANSITION_TYPES = frozenset(TRANSITION_BUCKET_ORDER)


@dataclass(frozen=True)
class TransitionAssessment:
    transition_type: str
    confidence: float
    evidence_refs: tuple[str, ...] = ()
    classifier_surface_delta: tuple[str, ...] = ()
    ledger_delta: tuple[str, ...] = ()
    formal_delta: tuple[str, ...] = ()
    record_growth_delta: tuple[str, ...] = ()
    net_positive_signal: bool = False
    notes: str = ""
    producer: str = ""
    source_ref: str = ""
    work_unit_id: str = ""

    @property
    def bucket(self) -> int:
        return TRANSITION_BUCKET_ORDER.get(self.transition_type, TRANSITION_BUCKET_ORDER["unknown"])

    @property
    def evidence_refs_text(self) -> str:
        return ",".join(self.evidence_refs) if self.evidence_refs else "none"


def unknown_assessment(*, work_unit_id: str = "", source_ref: str = "") -> TransitionAssessment:
    return TransitionAssessment(
        transition_type="unknown",
        confidence=0.0,
        work_unit_id=work_unit_id,
        source_ref=source_ref,
    )


class TransitionAssessmentReader:
    """Load validated transition_assessment sidecars from the canonical path only."""

    @staticmethod
    def canonical_path(repo_root: Path, work_unit_id: str) -> Path | None:
        if not SAFE_WORK_UNIT_ID_RE.fullmatch(work_unit_id):
            return None
        return repo_root / ".refactor-loop" / "runs" / "transition-assessments" / f"{work_unit_id}.json"

    @classmethod
    def load_for_work_unit(cls, repo_root: Path, work_unit_id: str, source_ref: str) -> TransitionAssessment:
        path = cls.canonical_path(repo_root, work_unit_id)
        if path is None:
            return unknown_assessment(work_unit_id=work_unit_id, source_ref=source_ref)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return unknown_assessment(work_unit_id=work_unit_id, source_ref=source_ref)
        return transition_assessment_from_mapping(raw, work_unit_id=work_unit_id, source_ref=source_ref)


def transition_assessment_from_mapping(
    raw: Any,
    *,
    work_unit_id: str,
    source_ref: str,
) -> TransitionAssessment:
    if not isinstance(raw, dict):
        return unknown_assessment(work_unit_id=work_unit_id, source_ref=source_ref)
    if raw.get("work_unit_id") != work_unit_id or raw.get("source_ref") != source_ref:
        return unknown_assessment(work_unit_id=work_unit_id, source_ref=source_ref)
    producer = raw.get("producer")
    if producer not in ALLOWED_PRODUCERS:
        return unknown_assessment(work_unit_id=work_unit_id, source_ref=source_ref)
    confidence = _confidence(raw.get("confidence"))
    if confidence is None:
        return unknown_assessment(work_unit_id=work_unit_id, source_ref=source_ref)

    transition_type = raw.get("transition_type")
    if transition_type not in TRANSITION_TYPES:
        return unknown_assessment(work_unit_id=work_unit_id, source_ref=source_ref)

    evidence_refs = _string_tuple(raw.get("evidence_refs"))
    classifier_surface_delta = _string_tuple(raw.get("classifier_surface_delta"))
    ledger_delta = _string_tuple(raw.get("ledger_delta"))
    formal_delta = _string_tuple(raw.get("formal_delta"))
    record_growth_delta = _string_tuple(raw.get("record_growth_delta"))
    if None in (evidence_refs, classifier_surface_delta, ledger_delta, formal_delta, record_growth_delta):
        return unknown_assessment(work_unit_id=work_unit_id, source_ref=source_ref)
    net_positive_signal = raw.get("net_positive_signal")
    if not isinstance(net_positive_signal, bool):
        return unknown_assessment(work_unit_id=work_unit_id, source_ref=source_ref)

    normalized_type = _normalize_transition_type(
        transition_type,
        classifier_surface_delta=classifier_surface_delta,
        net_positive_signal=net_positive_signal,
    )
    notes = raw.get("notes", "")
    if not isinstance(notes, str):
        return unknown_assessment(work_unit_id=work_unit_id, source_ref=source_ref)
    return TransitionAssessment(
        transition_type=normalized_type,
        confidence=confidence if normalized_type != "unknown" else 0.0,
        evidence_refs=evidence_refs,
        classifier_surface_delta=classifier_surface_delta,
        ledger_delta=ledger_delta,
        formal_delta=formal_delta,
        record_growth_delta=record_growth_delta,
        net_positive_signal=net_positive_signal,
        notes=notes,
        producer=producer,
        source_ref=source_ref,
        work_unit_id=work_unit_id,
    )


def transition_rank_key(assessment: TransitionAssessment) -> tuple[int, float]:
    return (assessment.bucket, -assessment.confidence)


def projection_lines(assessment: TransitionAssessment) -> tuple[str, str, str]:
    return (
        f"TRANSITION_TYPE={assessment.transition_type}",
        f"TRANSITION_CONFIDENCE={assessment.confidence:g}",
        f"TRANSITION_EVIDENCE_REFS={assessment.evidence_refs_text}",
    )


def _normalize_transition_type(
    transition_type: str,
    *,
    classifier_surface_delta: tuple[str, ...],
    net_positive_signal: bool,
) -> str:
    if transition_type != "positive-discovery":
        return transition_type
    if classifier_surface_delta and net_positive_signal:
        return "positive-discovery"
    if classifier_surface_delta:
        return "classifier-shift"
    return "unknown"


def _confidence(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    confidence = float(value)
    if confidence < 0 or confidence > 1:
        return None
    return confidence


def _string_tuple(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return ()
    if not isinstance(value, list):
        return None
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            return None
        result.append(item)
    return tuple(result)
