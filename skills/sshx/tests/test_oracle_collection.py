"""Frozen prompt-behavior corpus and a checker for independently collected artifacts.

This module does not parse or repair oracle prose. A fresh agent reads SKILL.md and
fixtures/oracle_responses.json without oracle_expectations.json. Its actual outputs
are checked by verify_collection; unit tests exercise the checker and unchanged
completion boundary, not a pretend implementation of the English instruction.
Required-fact presence checks do not prove arbitrary semantic fidelity or absence
of invented substantive claims; those remain independent caller/review judgments.
"""

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from test_sshx_contract import ContractFailure, completed_worker_verdict, resolve_failed_flight


FIXTURES = Path(__file__).with_name("fixtures")
REVIEW_VERDICTS = {"approve", "reject", "comment"}


@dataclass(frozen=True)
class CarrierFacts:
    terminal: bool
    exit_code: int | None
    completion_ref: str
    flight_matches: bool
    attempt_matches: bool


@dataclass(frozen=True)
class CollectionCase:
    case_id: str
    raw_response: str
    raw_capture_ref: str | None
    dispatch: dict[str, Any]
    carrier: CarrierFacts
    attempt: int
    pass_budget: int


@dataclass(frozen=True)
class CollectionExpectation:
    case_id: str
    outcome: str
    verdict: str | None
    preserved_facts: tuple[str, ...]
    diagnostic_source: str | None
    stage_metadata: dict[str, Any]
    worker_log_ref: str | None


def read_cases(path: Path) -> list[CollectionCase]:
    """Adapt source fixtures or a blind run's host-bound input JSON."""
    data = json.loads(path.read_text())
    defaults = data.get("defaults", {})
    rows = [{**defaults, **row,
        "dispatch": {**defaults.get("dispatch", {}), **row.get("dispatch", {})},
        "carrier": {**defaults.get("carrier", {}), **row.get("carrier", {})},
    } for row in data["cases"]]
    return [CollectionCase(
        case_id=row["case_id"], raw_response=row["raw_response"],
        raw_capture_ref=row.get("raw_capture_ref"), dispatch=row["dispatch"],
        carrier=CarrierFacts(**row["carrier"]), attempt=row["attempt"],
        pass_budget=row["pass_budget"],
    ) for row in rows]


def read_expectations() -> list[CollectionExpectation]:
    rows = json.loads((FIXTURES / "oracle_expectations.json").read_text())["expectations"]
    return [CollectionExpectation(**{**row, "preserved_facts": tuple(row["preserved_facts"])}) for row in rows]


def require(condition: bool, case_id: str, reason: str) -> None:
    if not condition:
        raise ContractFailure(f"{case_id}: {reason}")


def string_values(value: object) -> list[str]:
    """Inspect already collected JSON; never extract facts from raw oracle prose."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in string_values(item)]
    if isinstance(value, dict):
        return [text for item in value.values() for text in string_values(item)]
    return []


def verify_complete(case: CollectionCase, expected: CollectionExpectation, result: dict[str, Any]) -> None:
    envelope = result["envelope"]
    require(isinstance(envelope, dict), case.case_id, "missing canonical artifact")
    require(case.carrier.flight_matches and case.carrier.attempt_matches, case.case_id, "mismatched attempt")
    verdict = completed_worker_verdict(
        process_exited=case.carrier.terminal, exit_code=case.carrier.exit_code,
        result_artifact=envelope, completion_sentinel_present=bool(case.carrier.completion_ref),
        allowed_verdicts=REVIEW_VERDICTS,
    )
    require(verdict == expected.verdict, case.case_id, "changed explicit verdict")
    require(isinstance(envelope["log_ref"], str) and bool(envelope["log_ref"].strip()), case.case_id, "invalid diagnostic reference")
    require(envelope["log_ref"] != "n/a", case.case_id, "fabricated diagnostic placeholder")
    conclusion_values = string_values(envelope["conclusion"])
    for fact in expected.preserved_facts:
        require(any(fact.casefold() in value.casefold() for value in conclusion_values), case.case_id, f"lost compact fact: {fact}")
    metadata = result["stage_metadata"]
    for key, value in expected.stage_metadata.items():
        require(metadata.get(key) == value, case.case_id, f"lost stage metadata: {key}")
    dispatch_metadata = {key: case.dispatch[key] for key in ("role", "bias", "visible_inputs")}
    dispatch_metadata.update({"worker_mode": "nyxid-oracle",
        "worker_carrier": "nyxid-oracle", "worker_flight_ref": case.dispatch["flight_id"]})
    for key, value in metadata.items():
        require(key == "verdict" and value == verdict or key in dispatch_metadata and value == dispatch_metadata[key], case.case_id, "invented or conflicting stage metadata")
    require(result["diagnostic_source"] == expected.diagnostic_source, case.case_id, "wrong diagnostic provenance")
    if expected.diagnostic_source == "caller":
        require(envelope["log_ref"] == case.raw_capture_ref, case.case_id, "unbound saved response pointer")
        require(bool(result["reason"].strip()), case.case_id, "missing caller diagnostic collection note")
    else:
        require(envelope["log_ref"] == expected.worker_log_ref, case.case_id, "changed worker reference")
    artifact = Path(result["result_envelope_ref"])
    require(artifact.is_file(), case.case_id, "canonical result was not saved")
    require(json.loads(artifact.read_text()) == envelope, case.case_id, "saved result differs from collected envelope")
    if case.raw_capture_ref:
        capture = Path(case.raw_capture_ref)
        require(capture.is_file() and capture.read_text() == case.raw_response, case.case_id, "raw response missing or overwritten")
        require(artifact.resolve() != capture.resolve(), case.case_id, "raw and canonical artifact alias")
    require(result["completion_sentinel_ref"] == case.carrier.completion_ref, case.case_id, "invented completion reference")
    require(result["action"] == "route", case.case_id, "wrong successful route")


def verify_result(case: CollectionCase, expected: CollectionExpectation, result: dict[str, Any]) -> None:
    require(result["outcome"] == expected.outcome, case.case_id, "unexpected collection outcome")
    require(result["authority"] == "advisory-only", case.case_id, "reply instruction gained authority")
    require(result["attempt"] == case.attempt and result["pass_budget"] == case.pass_budget, case.case_id, "projection consumed or reset budget")
    if result["outcome"] == "complete":
        verify_complete(case, expected, result)
        return
    require(result["envelope"] is None, case.case_id, "incomplete result fabricated a vote")
    require(not result["result_envelope_ref"] and not result["completion_sentinel_ref"], case.case_id, "incomplete result recorded success references")
    require(bool(result["reason"].strip()), case.case_id, "failure has no diagnostic")
    require(result["action"] == "retry/fallback", case.case_id, "invented clarification or repair route")
    for budget, fallback, route in [(2, True, "retry-same-carrier"), (1, True, "fallback-highest-priority-untried-carrier"), (1, False, "abstain")]:
        actual = resolve_failed_flight({"status": "retrying", "attempt": case.attempt, "retry_budget": budget}, fallback)
        require(actual == route, case.case_id, "ordinary finite failure route changed")


def verify_collection(inputs: Path, outputs: Path) -> int:
    """Compare fresh caller output with source-owned expectations; raise on any gap."""
    cases = read_cases(inputs)
    expectations = {row.case_id: row for row in read_expectations()}
    results = json.loads(outputs.read_text())["results"]
    identifiers = [row["case_id"] for row in results]
    require(len(identifiers) == len(set(identifiers)), "corpus", "duplicate result")
    require(set(identifiers) == {case.case_id for case in cases} == set(expectations), "corpus", "missing or unexpected result")
    by_id = {row["case_id"]: row for row in results}
    for case in cases:
        verify_result(case, expectations[case.case_id], by_id[case.case_id])
    return len(cases)


class OracleCollectionTests(unittest.TestCase):
    def test_frozen_corpus_and_baseline_evidence(self) -> None:
        cases = read_cases(FIXTURES / "oracle_responses.json")
        expectations = read_expectations()
        self.assertEqual({row.case_id for row in cases}, {row.case_id for row in expectations})
        self.assertEqual(len(cases), len({row.case_id for row in cases}))
        baseline = json.loads((FIXTURES / "oracle_expectations.json").read_text())["baseline"]
        by_id = {case.case_id: case for case in cases}
        for case_id in baseline["exact_raw_envelope_rejected"]:
            with self.subTest(case_id=case_id):
                with self.assertRaises((json.JSONDecodeError, ContractFailure)):
                    envelope = json.loads(by_id[case_id].raw_response)
                    completed_worker_verdict(process_exited=True, exit_code=0, result_artifact=envelope,
                        completion_sentinel_present=True, allowed_verdicts=REVIEW_VERDICTS)
        self.assertEqual(set(baseline["no_skill_failures"]), {"missing-verdict", "conflicting-verdicts"})

    def test_checker_detects_lost_facts_provenance_and_completion(self) -> None:
        case = read_cases(FIXTURES / "oracle_responses.json")[0]
        expected = read_expectations()[0]
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "canonical.json"
            envelope = json.loads(case.raw_response)
            artifact.write_text(json.dumps(envelope))
            result = {"outcome": "complete", "envelope": envelope, "stage_metadata": {},
                "diagnostic_source": "worker", "reason": "Canonical source result.", "action": "route",
                "result_envelope_ref": str(artifact), "completion_sentinel_ref": "n/a",
                "authority": "advisory-only", "attempt": 1, "pass_budget": 2}
            verify_result(case, expected, result)
            capitalized = {**envelope, "conclusion": {
                key: value if key == "verdict" else value.upper()
                for key, value in envelope["conclusion"].items()
            }}
            artifact.write_text(json.dumps(capitalized))
            verify_result(case, expected, {**result, "envelope": capitalized})
            mutations = [
                {"envelope": {**envelope, "conclusion": {"verdict": "approve"}}},
                {"envelope": {**envelope, "conclusion": {
                    key: value for key, value in envelope["conclusion"].items() if key != "uncertainty"
                }}},
                {"envelope": {**envelope, "conclusion": {**envelope["conclusion"], "verdict": "APPROVE"}}},
                {"envelope": {**envelope, "log_ref": envelope["log_ref"].upper()}},
                {"envelope": {**envelope, "log_ref": "invented://pointer"}},
                {"envelope": {**envelope, "log_ref": "oracle://"}},
                {"completion_sentinel_ref": "invented.done"},
                {"authority": "commit-and-push"}, {"attempt": 2},
                {"stage_metadata": {"flight_id": case.dispatch["flight_id"]}},
            ]
            for change in mutations:
                candidate = {**result, **change}
                # Keep saved and returned artifacts identical: provenance/content
                # assertions must reject the mutation, not a serialization mismatch.
                artifact.write_text(json.dumps(candidate["envelope"]))
                with self.subTest(change=change), self.assertRaises(ContractFailure):
                    verify_result(case, expected, candidate)

    def test_diagnostic_pointer_does_not_complete_unfinished_carrier(self) -> None:
        envelope = json.loads(read_cases(FIXTURES / "oracle_responses.json")[0].raw_response)
        for terminal, exit_code, sentinel in [(False, None, True), (True, 1, True), (True, 0, False)]:
            with self.subTest(terminal=terminal, exit_code=exit_code, sentinel=sentinel), self.assertRaises(ContractFailure):
                completed_worker_verdict(process_exited=terminal, exit_code=exit_code, result_artifact=envelope,
                    completion_sentinel_present=sentinel, allowed_verdicts=REVIEW_VERDICTS)


if __name__ == "__main__":
    unittest.main()
