#!/usr/bin/env python3
"""Behavior tests for ConsensusGateProof validation."""

from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__)
SKILL_ROOT = SCRIPT_PATH.parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from codex_refactor_loop.consensus_gate import (  # noqa: E402
    FORBIDDEN_CONSENSUS_PROOF_FIELDS,
    ConsensusGateProofError,
    consensus_gate_digest,
    validate_consensus_gate_proof,
)


def valid_proof() -> dict[str, object]:
    target_digest = consensus_gate_digest({"target": "implementation-plan", "scope": ["a.py"]})
    return {
        "target_kind": "implementation-plan",
        "target_ref": "gh-issue-579#consensus",
        "target_digest": target_digest,
        "decision_producer_id": "judge-issue-579-r2",
        "evidence": [
            {
                "producer_id": "solver-minimal-issue-579-r2",
                "role": "minimal",
                "artifact": ".refactor-loop/runs/phase9-issue579-r2-minimal.md",
                "artifact_digest": consensus_gate_digest({"role": "minimal", "verdict": "propose"}),
                "verdict": "propose",
            },
            {
                "producer_id": "solver-structural-issue-579-r2",
                "role": "structural",
                "artifact": ".refactor-loop/runs/phase9-issue579-r2-structural.md",
                "artifact_digest": consensus_gate_digest({"role": "structural", "verdict": "approve"}),
                "verdict": "approve",
            },
        ],
        "required_roles": ["minimal", "structural"],
        "verdict_rule": "all_required_approve",
        "scope_paths": ["skills/consensus-loop/scripts/codex_refactor_loop/consensus_gate.py"],
    }


def replace_evidence(raw: dict[str, object], index: int, **updates: object) -> None:
    evidence = list(raw["evidence"])
    item = dict(evidence[index])
    item.update(updates)
    evidence[index] = item
    raw["evidence"] = evidence


class ConsensusGateProofTests(unittest.TestCase):
    def test_valid_proof_returns_typed_contract_without_authority_fields(self) -> None:
        raw = valid_proof()
        proof = validate_consensus_gate_proof(raw, target_digest=str(raw["target_digest"]))

        self.assertEqual("implementation-plan", proof.target_kind)
        self.assertEqual(("minimal", "structural"), proof.required_roles)
        self.assertEqual(("skills/consensus-loop/scripts/codex_refactor_loop/consensus_gate.py",), proof.scope_paths)
        serialized = dataclasses.asdict(proof)
        for forbidden in FORBIDDEN_CONSENSUS_PROOF_FIELDS:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)

    def test_target_digest_mismatch_fails_closed(self) -> None:
        raw = valid_proof()
        wrong_digest = consensus_gate_digest({"target": "different"})

        with self.assertRaisesRegex(ConsensusGateProofError, "target_digest_mismatch"):
            validate_consensus_gate_proof(raw, target_digest=wrong_digest)

    def test_target_kind_and_ref_bind_to_expected_substantive_target(self) -> None:
        raw = valid_proof()

        validate_consensus_gate_proof(raw, target_kind="implementation-plan", target_ref="gh-issue-579#consensus")

        with self.assertRaisesRegex(ConsensusGateProofError, "target_kind_mismatch"):
            validate_consensus_gate_proof(raw, target_kind="issue-decomposition-plan")
        with self.assertRaisesRegex(ConsensusGateProofError, "target_ref_mismatch"):
            validate_consensus_gate_proof(raw, target_ref=".refactor-loop/runs/other.json")

    def test_mechanical_target_kind_is_outside_proof_scope(self) -> None:
        for target_kind in ("route", "post", "label", "spawn", "merge", "apply-validated-proof"):
            raw = valid_proof()
            raw["target_kind"] = target_kind
            with self.subTest(target_kind=target_kind):
                with self.assertRaisesRegex(ConsensusGateProofError, "mechanical target_kind"):
                    validate_consensus_gate_proof(raw)

    def test_decision_producer_cannot_self_certify(self) -> None:
        raw = valid_proof()
        replace_evidence(raw, 0, producer_id=raw["decision_producer_id"])

        with self.assertRaisesRegex(ConsensusGateProofError, "producer_overlap"):
            validate_consensus_gate_proof(raw)

    def test_duplicate_evidence_producer_fails_closed(self) -> None:
        raw = valid_proof()
        replace_evidence(raw, 1, producer_id="solver-minimal-issue-579-r2")

        with self.assertRaisesRegex(ConsensusGateProofError, "duplicate evidence producer"):
            validate_consensus_gate_proof(raw)

    def test_duplicate_evidence_role_fails_closed(self) -> None:
        raw = valid_proof()
        replace_evidence(raw, 1, role="minimal")

        with self.assertRaisesRegex(ConsensusGateProofError, "duplicate evidence role: minimal"):
            validate_consensus_gate_proof(raw)

    def test_artifact_digest_bindings_fail_closed_on_tampered_evidence(self) -> None:
        raw = valid_proof()
        evidence = list(raw["evidence"])
        first = dict(evidence[0])
        artifact = str(first["artifact"])

        validate_consensus_gate_proof(raw, artifact_digests={artifact: str(first["artifact_digest"])})

        with self.assertRaisesRegex(ConsensusGateProofError, "artifact_digest_mismatch"):
            validate_consensus_gate_proof(raw, artifact_digests={artifact: "0" * 64})

    def test_missing_required_role_fails_closed(self) -> None:
        raw = valid_proof()
        raw["required_roles"] = ["minimal", "delete"]

        with self.assertRaisesRegex(ConsensusGateProofError, "missing_required_roles: delete"):
            validate_consensus_gate_proof(raw)

    def test_unsupported_verdict_rule_fails_closed(self) -> None:
        raw = valid_proof()
        raw["verdict_rule"] = "majority"

        with self.assertRaisesRegex(ConsensusGateProofError, "unsupported verdict_rule: majority"):
            validate_consensus_gate_proof(raw)

    def test_verdict_rule_rejects_non_positive_required_role(self) -> None:
        raw = valid_proof()
        replace_evidence(raw, 1, verdict="abstain")

        with self.assertRaisesRegex(ConsensusGateProofError, "verdict_rule_failed"):
            validate_consensus_gate_proof(raw)

    def test_review_style_rule_allows_comments_only_with_positive_and_no_reject(self) -> None:
        raw = valid_proof()
        replace_evidence(raw, 1, verdict="comment")
        raw["verdict_rule"] = "reject_zero_approve_one"

        proof = validate_consensus_gate_proof(raw)

        self.assertEqual("reject_zero_approve_one", proof.verdict_rule)

    def test_review_style_rule_rejects_all_comment_required_verdicts(self) -> None:
        raw = valid_proof()
        replace_evidence(raw, 0, verdict="comment")
        replace_evidence(raw, 1, verdict="comment")
        raw["verdict_rule"] = "reject_zero_approve_one"

        with self.assertRaisesRegex(ConsensusGateProofError, "verdict_rule_failed: reject_zero_approve_one"):
            validate_consensus_gate_proof(raw)

    def test_review_style_rule_rejects_explicit_reject(self) -> None:
        raw = valid_proof()
        replace_evidence(raw, 1, verdict="reject")
        raw["verdict_rule"] = "reject_zero_approve_one"

        with self.assertRaisesRegex(ConsensusGateProofError, "verdict_rule_failed"):
            validate_consensus_gate_proof(raw)

    def test_forbidden_command_or_lifecycle_fields_are_rejected_recursively(self) -> None:
        for forbidden in (
            "cmd",
            "argv",
            "shell",
            "command_line",
            "commands",
            "env",
            "git",
            "gh",
            "executor",
            "lifecycle_authority",
            "lifecycle_owner",
            "args",
            "controller_action",
            "proof_ticket",
            "resume_ticket",
        ):
            raw = valid_proof()
            raw["metadata"] = {"nested": {forbidden: "run-this"}}
            with self.subTest(forbidden=forbidden):
                with self.assertRaisesRegex(ConsensusGateProofError, "forbidden lifecycle/command fields"):
                    validate_consensus_gate_proof(raw)

    def test_scope_paths_must_be_repo_relative_when_present(self) -> None:
        for invalid in ("/tmp/outside.py", "../outside.py", "a/../../outside.py"):
            raw = valid_proof()
            raw["scope_paths"] = [invalid]
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ConsensusGateProofError, "repo-relative"):
                    validate_consensus_gate_proof(raw)

    def test_required_scope_paths_must_be_bound_in_proof(self) -> None:
        raw = valid_proof()

        validate_consensus_gate_proof(
            raw,
            required_scope_paths=["skills/consensus-loop/scripts/codex_refactor_loop/consensus_gate.py"],
        )

        with self.assertRaisesRegex(ConsensusGateProofError, "missing_scope_paths"):
            validate_consensus_gate_proof(raw, required_scope_paths=["skills/consensus-loop/scripts/codex_refactor_loop/wakeup_plan.py"])

    def test_proof_shape_is_exact_except_optional_scope_paths(self) -> None:
        raw = valid_proof()
        del raw["scope_paths"]
        validate_consensus_gate_proof(raw)

        raw = valid_proof()
        raw["unexpected"] = "value"
        with self.assertRaisesRegex(ConsensusGateProofError, "unsupported fields"):
            validate_consensus_gate_proof(raw)


if __name__ == "__main__":
    unittest.main()
