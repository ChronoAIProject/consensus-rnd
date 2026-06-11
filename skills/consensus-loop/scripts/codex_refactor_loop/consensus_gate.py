"""Controller-private consensus proof validation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


FORBIDDEN_CONSENSUS_PROOF_FIELDS = frozenset(
    {
        "args",
        "argv",
        "cmd",
        "command",
        "command_line",
        "commands",
        "controller_action",
        "env",
        "executor",
        "gh",
        "git",
        "lifecycle_authority",
        "lifecycle_owner",
        "proof_ticket",
        "resume_ticket",
        "shell",
    }
)
PROOF_FIELDS = frozenset(
    {
        "target_kind",
        "target_ref",
        "target_digest",
        "decision_producer_id",
        "evidence",
        "required_roles",
        "verdict_rule",
        "scope_paths",
    }
)
EVIDENCE_FIELDS = frozenset({"producer_id", "role", "artifact", "artifact_digest", "verdict"})
SUPPORTED_VERDICT_RULES = frozenset({"all_required_approve", "reject_zero_approve_one"})
POSITIVE_VERDICTS = frozenset({"approve", "approved", "propose", "consensus"})
REJECT_VERDICTS = frozenset({"reject", "rejected"})
KNOWN_VERDICTS = POSITIVE_VERDICTS | REJECT_VERDICTS | frozenset({"comment", "abstain"})
MECHANICAL_TARGET_KINDS = frozenset(
    {
        "route",
        "post",
        "label",
        "spawn",
        "merge",
        "apply",
        "apply-validated-proof",
        "wakeup-action",
        "controller-action",
    }
)
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class ConsensusGateProofError(ValueError):
    """Raised when a ConsensusGateProof cannot be trusted."""


@dataclass(frozen=True)
class ConsensusGateEvidence:
    producer_id: str
    role: str
    artifact: str
    artifact_digest: str
    verdict: str


@dataclass(frozen=True)
class ConsensusGateProof:
    target_kind: str
    target_ref: str
    target_digest: str
    decision_producer_id: str
    evidence: tuple[ConsensusGateEvidence, ...]
    required_roles: tuple[str, ...]
    verdict_rule: str
    scope_paths: tuple[str, ...] = ()


def consensus_gate_digest(data: Any) -> str:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def consensus_gate_file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_consensus_gate_proof(
    raw: Any,
    *,
    target_digest: str | None = None,
    target_kind: str | None = None,
    target_ref: str | None = None,
    artifact_digests: Mapping[str, str] | None = None,
    required_scope_paths: Sequence[str] | None = None,
) -> ConsensusGateProof:
    if not isinstance(raw, dict):
        raise ConsensusGateProofError("ConsensusGateProof must be a JSON object")
    _reject_forbidden_fields_recursive(raw, "proof")
    _require_allowed_fields(raw, PROOF_FIELDS, "proof")

    proof_target_digest = _required_digest(raw.get("target_digest"), "target_digest")
    if target_digest is not None and proof_target_digest != _required_digest(target_digest, "expected target_digest"):
        raise ConsensusGateProofError("target_digest_mismatch")

    proof_target_kind = _required_identity(raw.get("target_kind"), "target_kind")
    if proof_target_kind in MECHANICAL_TARGET_KINDS:
        raise ConsensusGateProofError(f"mechanical target_kind is outside ConsensusGateProof scope: {proof_target_kind}")
    if target_kind is not None and proof_target_kind != _required_identity(target_kind, "expected target_kind"):
        raise ConsensusGateProofError("target_kind_mismatch")
    proof_target_ref = _required_text(raw.get("target_ref"), "target_ref")
    if target_ref is not None and proof_target_ref != _required_text(target_ref, "expected target_ref"):
        raise ConsensusGateProofError("target_ref_mismatch")
    decision_producer_id = _required_identity(raw.get("decision_producer_id"), "decision_producer_id")
    required_roles = _parse_required_roles(raw.get("required_roles"))
    verdict_rule = _required_text(raw.get("verdict_rule"), "verdict_rule")
    if verdict_rule not in SUPPORTED_VERDICT_RULES:
        raise ConsensusGateProofError(f"unsupported verdict_rule: {verdict_rule}")

    evidence = _parse_evidence(raw.get("evidence"))
    _validate_producer_independence(decision_producer_id, evidence)
    _validate_role_coverage(required_roles, verdict_rule, evidence)
    _validate_artifact_digest_bindings(evidence, artifact_digests or {})
    scope_paths = _parse_scope_paths(raw.get("scope_paths", []))
    _validate_required_scope_paths(scope_paths, required_scope_paths or ())

    return ConsensusGateProof(
        target_kind=proof_target_kind,
        target_ref=proof_target_ref,
        target_digest=proof_target_digest,
        decision_producer_id=decision_producer_id,
        evidence=evidence,
        required_roles=required_roles,
        verdict_rule=verdict_rule,
        scope_paths=scope_paths,
    )


def _reject_forbidden_fields_recursive(value: Any, context: str) -> None:
    if isinstance(value, dict):
        forbidden = sorted(FORBIDDEN_CONSENSUS_PROOF_FIELDS.intersection(value))
        if forbidden:
            raise ConsensusGateProofError(f"{context} contains forbidden lifecycle/command fields: {', '.join(forbidden)}")
        for key, child in value.items():
            _reject_forbidden_fields_recursive(child, f"{context}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_fields_recursive(child, f"{context}[{index}]")


def _require_allowed_fields(value: dict[str, Any], allowed: frozenset[str], context: str) -> None:
    missing = sorted((allowed - {"scope_paths"}) - set(value))
    extra = sorted(set(value) - allowed)
    if missing:
        raise ConsensusGateProofError(f"{context} missing required fields: {', '.join(missing)}")
    if extra:
        raise ConsensusGateProofError(f"{context} contains unsupported fields: {', '.join(extra)}")


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConsensusGateProofError(f"{field} must be non-empty text")
    return value.strip()


def _required_identity(value: Any, field: str) -> str:
    text = _required_text(value, field)
    if not IDENTITY_RE.fullmatch(text):
        raise ConsensusGateProofError(f"{field} must be a stable identifier")
    return text


def _required_digest(value: Any, field: str) -> str:
    text = _required_text(value, field)
    if not DIGEST_RE.fullmatch(text):
        raise ConsensusGateProofError(f"{field} must be a sha256 hex digest")
    return text


def _parse_required_roles(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConsensusGateProofError("required_roles must be a non-empty list")
    roles: list[str] = []
    seen: set[str] = set()
    for index, raw_role in enumerate(value):
        role = _required_identity(raw_role, f"required_roles[{index}]")
        if role in seen:
            raise ConsensusGateProofError(f"duplicate required role: {role}")
        seen.add(role)
        roles.append(role)
    return tuple(roles)


def _parse_evidence(value: Any) -> tuple[ConsensusGateEvidence, ...]:
    if not isinstance(value, list) or not value:
        raise ConsensusGateProofError("evidence must be a non-empty list")
    evidence: list[ConsensusGateEvidence] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ConsensusGateProofError(f"evidence[{index}] must be an object")
        _require_allowed_fields(item, EVIDENCE_FIELDS, f"evidence[{index}]")
        verdict = _required_text(item.get("verdict"), f"evidence[{index}].verdict")
        if verdict not in KNOWN_VERDICTS:
            raise ConsensusGateProofError(f"evidence[{index}].verdict is unsupported: {verdict}")
        evidence.append(
            ConsensusGateEvidence(
                producer_id=_required_identity(item.get("producer_id"), f"evidence[{index}].producer_id"),
                role=_required_identity(item.get("role"), f"evidence[{index}].role"),
                artifact=_required_text(item.get("artifact"), f"evidence[{index}].artifact"),
                artifact_digest=_required_digest(item.get("artifact_digest"), f"evidence[{index}].artifact_digest"),
                verdict=verdict,
            )
        )
    return tuple(evidence)


def _validate_producer_independence(decision_producer_id: str, evidence: tuple[ConsensusGateEvidence, ...]) -> None:
    producers: set[str] = set()
    for item in evidence:
        if item.producer_id == decision_producer_id:
            raise ConsensusGateProofError("producer_overlap: decision producer also supplied evidence")
        if item.producer_id in producers:
            raise ConsensusGateProofError(f"duplicate evidence producer: {item.producer_id}")
        producers.add(item.producer_id)


def _validate_role_coverage(
    required_roles: tuple[str, ...],
    verdict_rule: str,
    evidence: tuple[ConsensusGateEvidence, ...],
) -> None:
    by_role: dict[str, ConsensusGateEvidence] = {}
    for item in evidence:
        if item.role in by_role:
            raise ConsensusGateProofError(f"duplicate evidence role: {item.role}")
        by_role[item.role] = item
    missing = [role for role in required_roles if role not in by_role]
    if missing:
        raise ConsensusGateProofError(f"missing_required_roles: {', '.join(missing)}")
    required_evidence = [by_role[role] for role in required_roles]
    if verdict_rule == "all_required_approve":
        non_positive = [item.role for item in required_evidence if item.verdict not in POSITIVE_VERDICTS]
        if non_positive:
            raise ConsensusGateProofError(f"verdict_rule_failed: non_positive_required_roles={','.join(non_positive)}")
        return
    rejected = [item.role for item in required_evidence if item.verdict in REJECT_VERDICTS]
    positives = [item.role for item in required_evidence if item.verdict in POSITIVE_VERDICTS]
    if rejected or not positives:
        raise ConsensusGateProofError("verdict_rule_failed: reject_zero_approve_one")


def _validate_artifact_digest_bindings(
    evidence: tuple[ConsensusGateEvidence, ...],
    artifact_digests: Mapping[str, str],
) -> None:
    for item in evidence:
        if item.artifact not in artifact_digests:
            continue
        actual = _required_digest(artifact_digests[item.artifact], f"artifact_digests[{item.artifact}]")
        if item.artifact_digest != actual:
            raise ConsensusGateProofError(f"artifact_digest_mismatch: {item.artifact}")


def _parse_scope_paths(value: Any) -> tuple[str, ...]:
    if value in (None, []):
        return ()
    if not isinstance(value, list):
        raise ConsensusGateProofError("scope_paths must be a list when present")
    paths: list[str] = []
    for index, raw_path in enumerate(value):
        text = _required_text(raw_path, f"scope_paths[{index}]")
        posix = PurePosixPath(text)
        if posix.is_absolute() or ".." in posix.parts:
            raise ConsensusGateProofError(f"scope_paths[{index}] must be repo-relative")
        paths.append(text)
    return tuple(paths)


def _validate_required_scope_paths(scope_paths: tuple[str, ...], required_scope_paths: Sequence[str]) -> None:
    if not required_scope_paths:
        return
    required = set(_parse_scope_paths(list(required_scope_paths)))
    actual = set(scope_paths)
    missing = sorted(required - actual)
    if missing:
        raise ConsensusGateProofError(f"missing_scope_paths: {', '.join(missing)}")


__all__ = [
    "ConsensusGateEvidence",
    "ConsensusGateProof",
    "ConsensusGateProofError",
    "FORBIDDEN_CONSENSUS_PROOF_FIELDS",
    "MECHANICAL_TARGET_KINDS",
    "consensus_gate_digest",
    "consensus_gate_file_digest",
    "validate_consensus_gate_proof",
]
