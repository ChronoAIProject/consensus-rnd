"""Source-regression checks with explicit methodological limits.

These assertions can prove the presence and location of sections, fields, enums,
ordering, and stable anchors, and can expose obvious deletion or replacement.
They cannot judge whether arbitrary free-form English semantically entails a weakening of a
normative proposition. That semantic-entailment question is the residual blind kernel, and
its declared absorber is the Review Triplet duty to check exception clauses, contradictions,
semantic weakening, and external identifier or source coupling that lexical shapes miss. The
formal-identifier check recognizes backticked alphanumeric tokens with optional dot, underscore,
or hyphen-separated segments, unformatted dot- or underscore-separated identifiers, and
unformatted CamelCase identifiers. A bare single lowercase token is digest-only: it is not
recognized as an identifier by the lexical check and remains a Review Triplet duty.

Known bypass evidence for that limit, each retaining the original normative text
and appending a weakening:

1. append that the single most salient condition is sufficient to trigger;
2. append that a reformatted objection may reopen the same causal chain with unchanged sealed inputs;
3. add panel-local precedence or reclassify the same event because of delay;
4. treat an unavailable harness as approval;
5. append ``merely recommended and may be omitted``;
6. allow ``No Context Pollution`` to inline full reasoning as an exception;
7. call the seven-stage order illustrative and permit skipped stages;
8. allow one seat to count as a complete triplet;
9. allow `pass_budget` units to be added after a repair result is seen;
10. append ``A repair flight whose rerun review finds no new blocker consumes no `pass_budget` unit.``;
11. append ``A termination truth-table row-1 evaluation that involves no roster consumes no `pass_budget` unit.``;
12. append ``A procedural `unsatisfied` that names no `GoalArtifact` term withholds the claim until the record is corrected.``;
13. append ``When `pass_budget` reaches zero, the caller may record a fresh budget for the remaining blockers.``;
14. append ``it is never a meta-judge downgrade path here`` so a procedural `unsatisfied` regains blocking force.

Items 2, 9, and 14 each also have one narrow lexical assertion for the demonstrated wording. Every
byte of ``skills/sshx/SKILL.md``, from the opening frontmatter delimiter through its terminal
newline, is one pinned canonical normative-document span. No byte of that file is positionally
unpinned. The stored digest is a change detector, not a semantic judge or self-authorization: a
legitimate edit requires both a synchronized digest update and Review Triplet judgment.

``pass_budget`` is the one counter the contract keeps; ``pass_budget_after`` and
``resolve_termination_claim`` model its decrement. No repair-rank or repair-sequence model exists
because the contract keeps none; English semantics stay with the Review Triplet absorber.
The behavior helpers below cover fixed truth tables and other load-bearing mechanical contracts,
but do not infer English semantics.
Files outside ``skills/sshx/SKILL.md`` are outside this positional boundary and remain governed
by their own behavior checks. Whether changed English preserves the contract is likewise not
decided by the digest and is routed to the named Review Triplet absorber.

The canonical default-seat-allocation span is normative wording. It fixes both
the stage's carrier composition and the seat rotation drawn over it. Rewriting it
requires a synchronized update to ``DEFAULT_SEAT_ALLOCATION_PATTERNS``. The
positive anchors accept ``each`` or ``every`` and do not require the composition
and rotation propositions to stay in source order, but passive voice, tables, and
cardinal mappings are intentionally not accepted. The rotation propositions --
uniform draw over the feasible assignments, a mechanical randomness source, the
draw recorded before the stage's first launch, no redraw, and a different draw on
a repeated pass -- are pinned verbatim by ``SEAT_ROTATION_SPAN_TAIL``, which the
suite also requires to occur once in the contract. The ``not|unless|except``
blocker catches appended weakening such as ``unless the caller prefers
otherwise``; by design, it also rejects reinforcing negation such as ``This
layout is not optional``.

The residual quantity check recognizes only a digit or an English cardinal from
``one`` through ``six`` directly adjacent to a backticked carrier identifier
within ``## Worker Delegation`` but outside the canonical span. It is a limited
heuristic, not the single-source guarantee; non-adjacent quantities and synonymous
contradictory prose elsewhere are not mechanically detected. It also rejects
same-shaped non-seat prose such as ``Only one `nyxid-oracle` conversation may be
open per flight``; rewrite that as ``one conversation per `nyxid-oracle` flight``.
The structural single-source checks are that the exact canonical span occurs once
and all worker-panel or gate sections remain carrier-free. The whole-file digest above is the
single positional closure owner for the canonical normative document.
"""

import ast
import hashlib
import re
import subprocess
import unittest
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import TypeAlias


ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / "skills" / "sshx" / "SKILL.md"
SPEC = ROOT / "skills" / "sshx" / "CODEX_WORKER_SPEC.md"
README = ROOT / "README.md"
GEMINI = ROOT / "GEMINI.md"
CI = ROOT / ".github" / "workflows" / "consensus-rnd-ci.yml"
BASELINE_ARTIFACT_PATHSPEC = "*baseline-issue342-sshx.md"
PASS_CLASSES = frozenset(
    {
        "meta-layer convergence",
        "focused round",
        "repair with rerun review",
        "repeated review pass",
        "termination-gate evaluation",
    }
)
UNCOUNTED_TRANSITIONS = frozenset({"initial review triplet", "carrier retry", "carrier fallback"})

THINKING_VERDICTS = {"propose", "revise", "reject", "abstain"}
TERMINATION_VERDICTS = {"satisfied", "unsatisfied", "abstain"}
TERMINATION_ROLES = ("criterion-evidence", "residual-gap", "claim-integrity")
CARRIER_NAMES = ("codex-cli", "nyxid-oracle", "isolated-token-subagent")
CARRIER_IDENTIFIERS = tuple(f"`{carrier}`" for carrier in CARRIER_NAMES)
DEFAULT_SEAT_ALLOCATION_PATTERNS = (
    ("dispatch-time assertion", r"\b(?:at dispatch time|when dispatch begins)\b"),
    ("multi-seat stage scope", r"\b(?:every|each) multi-seat stage assigns\b"),
    ("one isolated-token-subagent seat", r"\bexactly one seat to `isolated-token-subagent`"),
    ("one nyxid-oracle seat", r"\bexactly one seat to `nyxid-oracle`"),
    (
        "remaining codex-cli seats",
        r"\b(?:every|each) remaining seat(?: in that stage)?(?: goes)? to `codex-cli`",
    ),
    (
        "single-worker codex-cli assignment",
        r"\b(?:every|each) single-worker stage assigns its worker to `codex-cli`",
    ),
    ("uniform random seat draw", r"\bdraws? one assignment uniformly at random\b"),
    (
        "feasible-draw scope",
        r"\bfrom (?:every|each) assignment that satisfies that composition and (?:this|the) stage's per-seat carrier constraints",
    ),
    ("mechanical randomness source", r"\bmechanical randomness source\b"),
    (
        "draw recorded before first launch",
        r"recorded in `worker_delegation\.seat_rotation` before the first worker of that stage is launched",
    ),
    ("redraw forbidden", r"\bredrawing it is forbidden\b"),
    (
        "rotation across repeated passes",
        r"\bnext draw must differ from that stage's previously recorded assignment\b",
    ),
)
SEAT_ROTATION_SPAN_TAIL = (
    "Which named seat holds which carrier rotates: at each stage dispatch the caller draws one "
    "assignment uniformly at random from every assignment that satisfies that composition and this "
    "stage's per-seat carrier constraints, so a named role holds a carrier only for the stage "
    "dispatch it was drawn for. "
    "The draw must come from a mechanical randomness source outside the caller's own preference, "
    "and its result is recorded in `worker_delegation.seat_rotation` before the first worker of that "
    "stage is launched. "
    "A recorded draw is final: redrawing it is forbidden, whatever the caller thinks of the seats it "
    "produced, and an unavailable carrier is handled by the fallback rule below rather than by a new "
    "draw. "
    "When a stage runs again on the same `work_target`, its next draw must differ from that stage's "
    "previously recorded assignment whenever two or more assignments satisfy the constraints."
)
CARDINAL_CARRIER_BINDING_PATTERN = re.compile(
    rf"\b(?:\d+|one|two|three|four|five|six)\s+(?:{'|'.join(map(re.escape, CARRIER_IDENTIFIERS))})",
    flags=re.IGNORECASE,
)
PERMISSIVE_SNAPSHOT_COMPLETION_PATTERN = re.compile(
    r"(?is)(?:^|[.!?]\s+)(?=[^.!?]*\bprocess snapshot\b)"
    r"(?=[^.!?]*\b(?:may|can|allowed)\b)"
    r"(?=[^.!?]*\b(?:terminal completion|terminally complete|treated as terminal)\b)[^.!?]*[.!?]"
)
EXTERNAL_SOURCE_PATH_PATTERN = re.compile(
    r"(?i)(?<![\w./-])(?:[a-z0-9_.-]+/)+[a-z0-9_.-]+\.(?:lean|agda|thy|v)\b"
)
BACKTICKED_FORMAL_IDENTIFIER_PATTERN = re.compile(r"`([A-Za-z][A-Za-z0-9]*(?:[._-][A-Za-z0-9]+)*)`")
FORMAL_IDENTIFIER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:"
    r"[A-Za-z][A-Za-z0-9]*(?:[._][A-Za-z0-9]+)+"
    r"|[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+"
    r")(?![A-Za-z0-9_-])"
)
EXTERNAL_REPOSITORY_IDENTIFIERS = (
    "trureturing",
    "the-omega-institute",
    "D5/S3",
    "ConceptDynamics",
)
SSHX_CONTRACT_FORMAL_IDENTIFIERS = frozenset(
    {
        "AGENTS.md",
        "ASSUMED-UNVERIFIED",
        "BlockingAuthority",
        "CLAUDE.md",
        "CODEX_WORKER_SPEC.md",
        "CapabilityOverlap",
        "ChatGPT",
        "FocusedRound",
        "GitHub",
        "GoalArtifact",
        "GoalArtifact.success_criteria",
        "HEAD",
        "InlineConsensusProtocol",
        "MEMORY.md",
        "SshxResultEnvelope",
        "SshxResultEnvelope.conclusion",
        "SshxResultEnvelope.log_ref",
        "SshxWorkerFlightRecord",
        "SshxWorkerFlightRecord.stage",
        "ThreatEligibility",
        "WorkerDelegationContract",
        "WorkerMode",
        "WorkerModeGate",
        "abstain",
        "abstained",
        "approve",
        "architecture",
        "attempt",
        "authorization_source",
        "bias",
        "caller-prior-exposed",
        "change",
        "choose_worker_mode",
        "claim-integrity",
        "codex-cli",
        "comment",
        "completion_sentinel_ref",
        "conclusion",
        "conclusion.verdict",
        "concrete_plan",
        "constraints",
        "continue",
        "continuation_declaration_ref",
        "criterion-evidence",
        "danger-full-access",
        "decision_ownership",
        "disown",
        "escalate",
        "exit",
        "external-prior-exposed",
        "fidelity",
        "finding_downgrades",
        "fix",
        "fix_or_done",
        "flight_id",
        "goal_gap",
        "harness",
        "harness.decision_ownership",
        "harness.provided_capabilities",
        "harness.trust_boundary",
        "implement",
        "implementation_worker",
        "in-flight",
        "intake",
        "invalidated_completed_work",
        "isolated-token-subagent",
        "iteration_question",
        "log_ref",
        "meta_judge",
        "natural-ownership",
        "next_iteration_question",
        "nohup",
        "none",
        "normalized_goal",
        "nyxid-oracle",
        "parsimony",
        "pass_budget",
        "proportional-containment",
        "propose",
        "provided_capabilities",
        "quality",
        "raw_user_input",
        "resolved_before_any_worker_dispatch",
        "reject",
        "repo-prior-exposed",
        "residual-gap",
        "result_envelope_ref",
        "retry_budget",
        "retrying",
        "review_triplet_workers",
        "revise",
        "revisions",
        "role",
        "satisfied",
        "seat_rotation",
        "setsid",
        "sshx",
        "stage",
        "status",
        "stop",
        "strict_peer_invisibility_required",
        "success_criteria",
        "teleology",
        "temporary",
        "terminal",
        "termination",
        "termination_gate",
        "tests",
        "test_sshx_contract.py",
        "thinking_panel_workers",
        "trust_boundary",
        "unsatisfied",
        "verdict",
        "visible_inputs",
        "wait",
        "work_target",
        "worker_carrier",
        "worker_delegation",
        "worker_delegation.reason",
        "worker_delegation.seat_rotation",
        "worker_flight_ref",
        "worker_flights",
        "worker_mode",
        "worker_mode_gate",
        "worth",
    }
)
DEMONSTRATED_FOCUSED_ROUND_REOPENING_EXCEPTION = (
    "For audit reruns, a reformatted objection may be treated as a different grounded obligation "
    "and reopen the same causal chain even when the sealed inputs are unchanged."
)
DEMONSTRATED_POST_RESULT_BUDGET_TOP_UP_EXCEPTION = (
    "When a repair consumes the reserved capacity, the caller may add evaluation units after seeing "
    "the repair result so the mandatory rerun review and termination roster remain reachable."
)
CANONICAL_NORMATIVE_DOCUMENT_SHA256 = "8e56f6b4c5978e61f42cd3fd2e897174b45b8e69923c4e96c784a7e9e54e636b"

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
GapOwnerAssignment: TypeAlias = tuple[JsonValue, JsonValue]
TerminationSeatResults: TypeAlias = tuple[tuple[JsonValue, JsonValue], ...]


@dataclass(frozen=True)
class TerminationResolution:
    truth_table_exit: str
    gap_route: str | None
    pass_budget_remaining: int
    termination_evaluations_consumed: int
    fake_consensus_correction_allowed: bool


class ContractFailure(ValueError):
    pass


class EqualityRaises:
    def __eq__(self, other: object) -> bool:
        raise RuntimeError("comparison must not be invoked")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def without_refactor_comments(text: str) -> str:
    return re.sub(r"<!--\nRefactor .*?\n-->\n", "", text, flags=re.DOTALL)


def heading_index(text: str, anchor: str) -> int:
    match = re.search(rf"^{re.escape(anchor)}$", text, flags=re.MULTILINE)
    if not match:
        raise AssertionError(f"missing heading anchor {anchor}")
    return match.start()


def section(text: str, start: str, end: str) -> str:
    return text[heading_index(text, start) : heading_index(text, end)]


def has_permissive_snapshot_completion_exception(text: str) -> bool:
    return PERMISSIVE_SNAPSHOT_COMPLETION_PATTERN.search(text) is not None


def backticked_formal_identifiers(text: str) -> tuple[str, ...]:
    return tuple(match.group(1) for match in BACKTICKED_FORMAL_IDENTIFIER_PATTERN.finditer(text))


def formal_identifiers(text: str) -> tuple[str, ...]:
    formatted = backticked_formal_identifiers(text)
    shaped = tuple(match.group(0) for match in FORMAL_IDENTIFIER_PATTERN.finditer(text))
    return tuple(dict.fromkeys(formatted + shaped))


def scientific_source_couplings(text: str) -> tuple[str, ...]:
    normalized = text.replace("\\", "/")
    folded = normalized.casefold()
    repository_hits = tuple(
        identifier for identifier in EXTERNAL_REPOSITORY_IDENTIFIERS if identifier.casefold() in folded
    )
    formal_identifier_hits = tuple(
        identifier
        for identifier in formal_identifiers(text)
        if identifier not in SSHX_CONTRACT_FORMAL_IDENTIFIERS
        and not re.search(r"\.(?:lean|agda|thy|v)$", identifier, flags=re.IGNORECASE)
    )
    path_hits = tuple(match.group(0) for match in EXTERNAL_SOURCE_PATH_PATTERN.finditer(normalized))
    return repository_hits + formal_identifier_hits + path_hits


def default_seat_allocation_span(text: str) -> tuple[int, int]:
    section_start = heading_index(text, "## Worker Delegation")
    worker_delegation = section(text, "## Worker Delegation", "## Result Envelope")
    start_anchor = "Do not self-apply the triplet inside the caller context and present it as worker consensus.\n\n"
    end_anchor = "The carrier-role pairing must be chosen and recorded before any worker"
    if worker_delegation.count(start_anchor) != 1 or worker_delegation.count(end_anchor) != 1:
        raise AssertionError("default seat allocation boundaries must be unique")
    policy_prefix = "Protocol policy, not a mathematical consequence: "
    relative_start = worker_delegation.index(start_anchor) + len(start_anchor)
    if not worker_delegation.startswith(policy_prefix, relative_start):
        raise AssertionError("default seat allocation must be labelled protocol policy")
    relative_start += len(policy_prefix)
    allocation = worker_delegation[relative_start : worker_delegation.index(end_anchor, relative_start)].rstrip()
    normalized = re.sub(r"\s+", " ", allocation)
    blocked_token = re.search(r"\b(?:not|unless|except)\b", normalized, flags=re.IGNORECASE)
    if blocked_token:
        raise AssertionError(
            f"default seat allocation contains blocked weakening token: {blocked_token.group(0).lower()}"
        )
    for proposition, pattern in DEFAULT_SEAT_ALLOCATION_PATTERNS:
        if not re.search(pattern, normalized, flags=re.IGNORECASE):
            raise AssertionError(f"default seat allocation is missing: {proposition}")
    return section_start + relative_start, section_start + relative_start + len(allocation)


def assert_canonical_contract_closure(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if digest != CANONICAL_NORMATIVE_DOCUMENT_SHA256:
        raise AssertionError("canonical normative document changed")
    return digest


def has_cardinal_carrier_binding_outside_default(text: str) -> bool:
    allocation_start, allocation_end = default_seat_allocation_span(text)
    remainder = section(text[:allocation_start] + text[allocation_end:], "## Worker Delegation", "## Result Envelope")
    return CARDINAL_CARRIER_BINDING_PATTERN.search(remainder) is not None


def worker_dispatch_sections_are_carrier_free(text: str) -> bool:
    worker_dispatch_sections = (
        section(text, "## Thinking Panel", "## Design Truth Table"),
        section(text, "## Review Triplet", "## Review Truth Table"),
        section(text, "## Termination Gate", "## Termination Truth Table"),
    )
    return all(carrier not in stage for stage in worker_dispatch_sections for carrier in CARRIER_NAMES)


def frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"---\n(?P<body>.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise AssertionError("missing frontmatter")
    result: dict[str, str] = {}
    for line in match.group("body").splitlines():
        key, value = line.split(": ", 1)
        result[key] = value
    return result


def completed_worker_verdict(
    *,
    process_exited: bool,
    exit_code: int | None,
    result_artifact: dict[str, object] | None,
    completion_sentinel_present: bool,
    allowed_verdicts: set[str],
) -> str:
    if not process_exited:
        raise ContractFailure("worker carrier still running")
    if exit_code != 0:
        raise ContractFailure("worker carrier did not exit 0")
    if result_artifact is None:
        raise ContractFailure("missing result_ref artifact")
    if set(result_artifact) != {"conclusion", "log_ref"}:
        raise ContractFailure("result_ref is not an SshxResultEnvelope")
    if not result_artifact["log_ref"]:
        raise ContractFailure("missing log_ref")
    conclusion = result_artifact["conclusion"]
    if not isinstance(conclusion, dict):
        raise ContractFailure("missing conclusion")
    verdict = conclusion.get("verdict")
    if verdict not in allowed_verdicts:
        raise ContractFailure("invalid conclusion.verdict")
    if not completion_sentinel_present:
        raise ContractFailure("missing completion sentinel")
    return str(verdict)


def flight_blocks_mutation(flight: dict[str, object], work_target: str) -> bool:
    return flight.get("work_target") == work_target and flight.get("status") in {"in-flight", "retrying"}


def has_terminal_completion(flight: dict[str, object]) -> bool:
    return (
        flight.get("status") == "terminal"
        and bool(flight.get("result_envelope_ref"))
        and bool(flight.get("completion_sentinel_ref"))
    )


def resolve_failed_flight(flight: dict[str, object], fallback_available: bool) -> str:
    if has_terminal_completion(flight):
        return "complete"
    retry_budget = int(flight.get("retry_budget", 0))
    attempt = int(flight.get("attempt", 0))
    if attempt < retry_budget:
        return "retry-same-carrier"
    if fallback_available:
        return "fallback-highest-priority-untried-carrier"
    return "abstain"


def classify_termination_verdict(verdict: JsonValue) -> str:
    if type(verdict) is not str:
        return "invalid"
    if verdict == "satisfied":
        return "satisfied"
    if verdict == "unsatisfied":
        return "unsatisfied"
    if verdict == "abstain":
        return "abstain"
    return "invalid"


def resolve_named_goal_gap(owner_assignment: GapOwnerAssignment) -> str:
    decision_class, declared_owner = owner_assignment
    if type(decision_class) is not str or type(declared_owner) is not str:
        return "stop and escalate with the unresolved ownership gap"
    if decision_class == "engineering" and declared_owner == "work-target-engineering-path":
        return "re-enter review-fix through the work-target engineering path"
    if decision_class == "orchestration" and declared_owner == "caller":
        return "await new evidence from the authorized caller"
    if decision_class == "product-governance-boundary" and declared_owner == "maintainer":
        return "stop and escalate for a maintainer-authorized correction"
    if declared_owner == "":
        return "stop and escalate with the unresolved ownership gap"
    return "stop and escalate to the declared owner"


def resolve_termination_claim(
    seat_results: TerminationSeatResults,
    *,
    consensus_source: JsonValue = "termination-seats",
    owner_assignment: GapOwnerAssignment = (None, None),
    pass_budget_remaining: JsonValue = 1,
) -> TerminationResolution:
    if type(pass_budget_remaining) is not int or pass_budget_remaining <= 0:
        return TerminationResolution(
            truth_table_exit="withhold claim; pass budget exhausted",
            gap_route=None,
            pass_budget_remaining=0,
            termination_evaluations_consumed=0,
            fake_consensus_correction_allowed=False,
        )

    remaining = pass_budget_remaining - 1
    if type(consensus_source) is not str:
        return TerminationResolution(
            truth_table_exit="reject fake termination consensus",
            gap_route=None,
            pass_budget_remaining=remaining,
            termination_evaluations_consumed=1,
            fake_consensus_correction_allowed=remaining > 0,
        )
    roles: list[str] = []
    verdict_classes: list[str] = []
    for role, verdict in seat_results:
        if type(role) is not str:
            return TerminationResolution(
                truth_table_exit="reject fake termination consensus",
                gap_route=None,
                pass_budget_remaining=remaining,
                termination_evaluations_consumed=1,
                fake_consensus_correction_allowed=remaining > 0,
            )
        roles.append(role)
        verdict_classes.append(classify_termination_verdict(verdict))

    exact_roster = len(roles) == len(TERMINATION_ROLES) and set(roles) == set(TERMINATION_ROLES)
    if consensus_source != "termination-seats" or not exact_roster:
        return TerminationResolution(
            truth_table_exit="reject fake termination consensus",
            gap_route=None,
            pass_budget_remaining=remaining,
            termination_evaluations_consumed=1,
            fake_consensus_correction_allowed=remaining > 0,
        )
    if all(verdict_class == "satisfied" for verdict_class in verdict_classes):
        exit_name = "termination claim permitted"
        gap_route = None
    elif "unsatisfied" in verdict_classes:
        exit_name = "withhold claim; continue against the named goal gap"
        gap_route = resolve_named_goal_gap(owner_assignment)
    else:
        exit_name = "withhold claim; escalate with the unresolved evidence gap"
        gap_route = None
    return TerminationResolution(
        truth_table_exit=exit_name,
        gap_route=gap_route,
        pass_budget_remaining=remaining,
        termination_evaluations_consumed=1,
        fake_consensus_correction_allowed=False,
    )


def resolve_termination_gate_applicability(
    *,
    harness_complete: bool,
    capability_source_confirmed: bool,
    continuation_entry: str | None,
) -> str:
    if not harness_complete or not capability_source_confirmed:
        return "stop and escalate to the maintainer"
    if continuation_entry == "present":
        return "termination gate applies"
    if continuation_entry in {None, "absent"}:
        return "termination gate inapplicable"
    return "stop and escalate to the maintainer"


def blocking_force(
    *,
    names_goal_term: bool,
    names_work_evidence: bool,
    basis_shown_false: bool,
    basis_disputed: bool,
) -> str:
    del basis_disputed
    if basis_shown_false:
        return "advisory"
    if names_goal_term and names_work_evidence:
        return "blocking"
    return "advisory"


def termination_unsatisfied_route(*, names_goal_term: bool, already_redispatched: bool) -> str:
    if names_goal_term:
        return "keep-full-force"
    if not already_redispatched:
        return "record-advisory-and-redispatch-once"
    return "treat-as-abstain"


def pass_budget_after(remaining: int, transition: str) -> int | str:
    if transition in UNCOUNTED_TRANSITIONS:
        return remaining
    if transition not in PASS_CLASSES:
        raise ContractFailure(f"unknown pass transition: {transition}")
    if remaining <= 0:
        return "no pass authority"
    return remaining - 1


def mathematical_conclusion_force(hypothesis_states: tuple[str, ...]) -> str:
    if "false" in hypothesis_states:
        return "inapplicable"
    if any(state != "verified" for state in hypothesis_states):
        return "ASSUMED-UNVERIFIED"
    return "binding"


def finite_listing_closure_force(
    *,
    fixed_point_free_constructor_verified: bool,
    finite_domain_completeness_proven: bool,
) -> str:
    if fixed_point_free_constructor_verified:
        return "not-closure-evidence"
    if finite_domain_completeness_proven:
        return "closure-evidence-admissible"
    return "ASSUMED-UNVERIFIED"


def sealed_stop_permits_claim(
    *,
    current_candidate: str | None,
    feasible_set: frozenset[str],
    decisions: tuple[str, ...],
    sourced_orientation: tuple[bool, bool, bool],
    late_narrative: bool = False,
) -> bool:
    del late_narrative
    return (
        current_candidate is not None
        and bool(feasible_set)
        and current_candidate in feasible_set
        and all(sourced_orientation)
        and bool(decisions)
        and all(decision == "satisfied" for decision in decisions)
    )


def weak_product_dominates(left: tuple[int, ...], right: tuple[int, ...]) -> bool:
    return len(left) == len(right) and all(left_value <= right_value for left_value, right_value in zip(left, right))


def reconcile_gain(
    *,
    additive: bool,
    absolute_endpoints: tuple[int, int],
    path_segments: tuple[int, ...],
) -> int:
    if additive:
        return sum(path_segments)
    start, end = absolute_endpoints
    return end - start


def focused_round_route(
    *,
    completed_rounds_for_chain: int,
    independently_changed_inputs: bool,
    different_grounded_obligation: bool,
) -> str:
    if completed_rounds_for_chain == 0:
        return "run-focused-round"
    if independently_changed_inputs and different_grounded_obligation:
        return "different-causal-chain"
    return "escalate-to-maintainer"


def independent_evidence_admitted(
    *,
    recorded_dependencies: frozenset[str],
    candidate_dependency_closure: frozenset[str],
) -> bool:
    return recorded_dependencies.isdisjoint(candidate_dependency_closure)


def role_ledger_valid(events: tuple[tuple[int, frozenset[int]], ...]) -> bool:
    return all(used_evidence.issubset(range(event_index)) for event_index, used_evidence in events)


def frozen_role_prefix(events: tuple[str, ...], decision_index: int) -> tuple[str, ...]:
    return events[:decision_index]


def finite_listing_has_diagonal_escape(values: tuple[int, ...], twist: dict[int, int]) -> bool:
    diagonal = tuple(twist[value] for value in values)
    return all(escaped != listed for escaped, listed in zip(diagonal, values))


class SshxContractTests(unittest.TestCase):
    def test_sshx_lowered_haystack_assert_not_in_needles_are_lowercase(self) -> None:
        source_path = Path(__file__)
        tree = ast.parse(source_path.read_text())
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        offending_literals: list[str] = []

        for call in ast.walk(tree):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "assertNotIn"
                and len(call.args) >= 2
            ):
                continue
            haystack = call.args[1]
            if not (
                isinstance(haystack, ast.Call)
                and isinstance(haystack.func, ast.Attribute)
                and haystack.func.attr == "lower"
            ):
                continue

            needle = call.args[0]
            literals: list[str] = []
            if isinstance(needle, ast.Constant) and isinstance(needle.value, str):
                literals.append(needle.value)
            elif isinstance(needle, ast.Name):
                ancestor: ast.AST = call
                while ancestor in parents:
                    ancestor = parents[ancestor]
                    if (
                        isinstance(ancestor, ast.For)
                        and isinstance(ancestor.target, ast.Name)
                        and ancestor.target.id == needle.id
                        and isinstance(ancestor.iter, (ast.List, ast.Tuple, ast.Set))
                    ):
                        literals.extend(
                            element.value
                            for element in ancestor.iter.elts
                            if isinstance(element, ast.Constant) and isinstance(element.value, str)
                        )
                        break

            offending_literals.extend(literal for literal in literals if literal != literal.lower())

        self.assertFalse(
            offending_literals,
            "assertNotIn needles used with a lowercased haystack must be lowercase: "
            + ", ".join(repr(literal) for literal in sorted(offending_literals)),
        )

    def test_sshx_frontmatter_contract(self) -> None:
        meta = frontmatter(read(SKILL))
        self.assertEqual(set(meta), {"name", "description"})
        self.assertEqual(meta["name"], "sshx")
        self.assertTrue(meta["description"].startswith("Use when"))
        self.assertLessEqual(len(meta["description"]), 1024)

    def test_sshx_required_anchors(self) -> None:
        text = read(SKILL)
        for anchor in [
            "## Trigger",
            "## Goal Contract",
            "## InlineConsensusProtocol",
            "## Worker Delegation",
            "## Result Envelope",
            "## Worker Completion Contract",
            "## No Context Pollution",
            "## Reasoning Discipline",
            "## Thinking Panel",
            "## Design Truth Table",
            "## Implementation Worker",
            "## Review Triplet",
            "## Review Truth Table",
            "## Fix Or Done",
            "## Termination Gate",
            "## Termination Truth Table",
            "## Boundaries",
            "## Baseline Failure Mode",
            "## Transcript Template",
            "## Verification",
        ]:
            heading_index(text, anchor)
        self.assertLess(heading_index(text, "## No Context Pollution"), heading_index(text, "## Reasoning Discipline"))
        self.assertLess(heading_index(text, "## Reasoning Discipline"), heading_index(text, "## Thinking Panel"))
        self.assertLess(heading_index(text, "## Thinking Panel"), heading_index(text, "## Design Truth Table"))
        self.assertLess(heading_index(text, "## Design Truth Table"), heading_index(text, "## Implementation Worker"))
        self.assertLess(heading_index(text, "## Implementation Worker"), heading_index(text, "## Review Triplet"))
        self.assertLess(heading_index(text, "## Review Triplet"), heading_index(text, "## Review Truth Table"))
        self.assertLess(heading_index(text, "## Fix Or Done"), heading_index(text, "## Termination Gate"))
        self.assertLess(heading_index(text, "## Termination Gate"), heading_index(text, "## Termination Truth Table"))
        self.assertLess(heading_index(text, "## Termination Truth Table"), heading_index(text, "## Boundaries"))
        self.assertIn(
            "`intake` (write `GoalArtifact` and normalize the goal)\n2. `choose_worker_mode`\n3. `thinking_panel_workers`\n4. `meta_judge`\n5. `implementation_worker`\n6. `review_triplet_workers`\n7. `fix_or_done`",
            text,
        )

    def test_sshx_contract_stays_within_size_ratchet(self) -> None:
        text = read(SKILL)
        self.assertLessEqual(len(text.splitlines()), 451)
        self.assertLessEqual(len(text.encode("utf-8")), 65_536)

    def test_sshx_goal_contract_source_regression(self) -> None:
        text = read(SKILL)
        heading_index(text, "## Goal Contract")
        self.assertIn("`GoalArtifact` is written during `intake` before worker mode selection or any worker dispatch", text)
        goal_section = text[heading_index(text, "## Goal Contract") : heading_index(text, "## InlineConsensusProtocol")]
        field_block = goal_section.split("`GoalArtifact` has exactly these fields:\n\n", 1)[1].split("\n\n", 1)[0]
        self.assertEqual(field_block.splitlines(), [
            "- `raw_user_input`",
            "- `normalized_goal`",
            "- `constraints`",
            "- `success_criteria`",
            "- `iteration_question`",
            "- `harness`",
            "- `revisions`",
        ])
        self.assertIn("The user's current input is the only source for the goal", text)
        self.assertIn(
            "must not discover or infer the goal from external lifecycle milestones, release state, runtime host configuration, GitHub issues, GitHub pull requests, labels, branches, or any other external lifecycle surface",
            text,
        )

    def test_sshx_goal_iteration_behavior_contract(self) -> None:
        text = read(SKILL)
        self.assertIn("`intake` (write `GoalArtifact` and normalize the goal)", text)
        protocol_section = text[heading_index(text, "## InlineConsensusProtocol") : heading_index(text, "## Worker Delegation")]
        for anchor in ["`visible_inputs`", "`GoalArtifact`", "`harness`", "same-round peer outputs"]:
            self.assertIn(anchor, protocol_section)
        self.assertIn(
            "`revise` must name the goal gap and a next iteration question; it must not open an unrelated design search",
            text,
        )
        self.assertIn(
            "The convergence question must be \"what still differs from `GoalArtifact`?\"",
            text,
        )
        self.assertIn("Do not generalize the convergence pass beyond that goal gap", text)
        self.assertIn(
            "ask what still differs from `GoalArtifact`, apply the smallest change that addresses that blocking goal gap",
            text,
        )
        self.assertIn(
            "by delegating it to a worker using the stage's default carrier exactly as `## Implementation Worker` requires",
            text,
        )
        self.assertIn("stay orchestration-only for the repair", text)
        self.assertIn("next iteration question", text)

    def test_sshx_harness_and_revisions_contract(self) -> None:
        # Source-regression only: this checks fields and stable anchors, not prose semantics.
        text = read(SKILL)
        goal_section = text[text.index("## Goal Contract") : text.index("## InlineConsensusProtocol")]
        for item in [
            "`provided_capabilities`",
            "`trust_boundary`",
            "`decision_ownership`",
            "`change`",
            "`authorization_source`",
            "`invalidated_completed_work`",
        ]:
            self.assertIn(item, goal_section)
        self.assertIn("exactly these three sub-items", goal_section)
        self.assertIn("append-only list", goal_section)
        self.assertIn("missing any one of these sub-items is invalid and fails closed", goal_section)
        self.assertIn("before any worker dispatch", goal_section)
        self.assertIn("stop and escalate to the maintainer", goal_section)
        self.assertIn("non-adversarial, not infallible", goal_section)
        self.assertEqual(text.count("`harness` is a prompt-level record containing exactly these three sub-items"), 1)
        self.assertEqual(text.count("`revisions` is an append-only list whose each item contains exactly these three sub-items"), 1)

    def test_sshx_role_ledger_prefix_and_settlement_behavior(self) -> None:
        text = read(SKILL)
        goal = section(text, "## Goal Contract", "## InlineConsensusProtocol")
        isolation = section(text, "## No Context Pollution", "## Reasoning Discipline")
        valid_roster = ((0, frozenset()), (1, frozenset({0})))
        invalid_roster = ((0, frozenset()), (1, frozenset({2})))
        old_events = ("dispatch", "decision")
        appended_events = old_events + ("late-correction",)
        self.assertEqual(
            (
                all(
                    anchor in isolation
                    for anchor in [
                        "append-only role ledger",
                        "only evidence in its recorded prefix",
                        "out-of-prefix evidence invalidates that roster, not an unrelated earlier roster",
                        "later appends cannot change a frozen prefix or recompute an earlier settlement",
                    ]
                ),
                "never rewrite an earlier target or recompute an earlier settlement" in goal,
                role_ledger_valid(valid_roster),
                role_ledger_valid(invalid_roster),
                frozen_role_prefix(old_events, 2),
                frozen_role_prefix(appended_events, 2),
            ),
            (True, True, True, False, old_events, old_events),
        )

    def test_sshx_termination_trigger_and_claim_scope(self) -> None:
        text = read(SKILL)
        goal_section = section(text, "## Goal Contract", "## InlineConsensusProtocol")
        termination = section(text, "## Termination Gate", "## Termination Truth Table")
        harness_items = goal_section.split(
            "`harness` is a prompt-level record containing exactly these three sub-items:\n\n",
            1,
        )[1].split("\n\n", 1)[0]
        self.assertEqual(
            [line.split("`", 2)[1] for line in harness_items.splitlines()],
            ["provided_capabilities", "trust_boundary", "decision_ownership"],
        )
        self.assertIn(
            "declare a host-provided goal-driven continuation mechanism only in `harness.provided_capabilities`",
            goal_section,
        )
        self.assertIn("must not discover or infer whether one exists", goal_section)
        for trigger_rule in [
            "triggered only by a positive, boundary-owner-confirmed entry",
            "whether silent or explicitly negative, the gate is inapplicable",
            "without asserting that the host mechanism is absent",
            "purported continuation entry that is ambiguous or unconfirmed",
        ]:
            self.assertIn(trigger_rule, goal_section)
        self.assertIn("boundary-owner-confirmed `harness.provided_capabilities`", termination)
        self.assertIn("permits only that `GoalArtifact`-scoped claim", termination)
        self.assertIn("does not certify any broader host goal condition", termination)
        for claim_surface in [
            "a final report",
            "`done with advisory surfaced` outcome used as success",
            "`stop` action carrying the claim",
        ]:
            self.assertIn(claim_surface, termination)
        self.assertIn(
            "The gate binds every exit that carries an affirmative `GoalArtifact` satisfaction claim, wherever the claim appears",
            termination,
        )
        self.assertIn("and binds no exit that carries none", termination)
        self.assertIn(
            "non-achievement exits keep their existing routing and must never be relabelled as goal satisfaction",
            termination,
        )
        self.assertIn(
            "`## Goal Contract` solely owns missing or invalid trigger-entry routing",
            termination,
        )
        self.assertNotIn("stop and escalate to the maintainer", termination.lower())

        protocol = section(text, "## InlineConsensusProtocol", "## Worker Delegation")
        stage_lines = re.findall(r"^\d+\. `([^`]+)`(?: .*)?$", protocol, flags=re.MULTILINE)
        self.assertEqual(stage_lines, [
            "intake",
            "choose_worker_mode",
            "thinking_panel_workers",
            "meta_judge",
            "implementation_worker",
            "review_triplet_workers",
            "fix_or_done",
        ])
        self.assertIn("termination-gate work are worker dispatches", protocol)

    def test_sshx_termination_trigger_routing_is_total(self) -> None:
        cases = {
            "affirmative-presence": (True, True, "present", "termination gate applies"),
            "explicit-absence": (True, True, "absent", "termination gate inapplicable"),
            "silence": (True, True, None, "termination gate inapplicable"),
            "ambiguous-entry": (True, True, "ambiguous", "stop and escalate to the maintainer"),
            "unconfirmed-source": (True, False, "present", "stop and escalate to the maintainer"),
            "incomplete-harness": (False, True, "present", "stop and escalate to the maintainer"),
        }
        for name, (harness_complete, source_confirmed, entry, expected) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(
                    resolve_termination_gate_applicability(
                        harness_complete=harness_complete,
                        capability_source_confirmed=source_confirmed,
                        continuation_entry=entry,
                    ),
                    expected,
                )

    def test_sshx_termination_gate_has_no_host_specific_coupling(self) -> None:
        text = read(SKILL)
        for host_specific_token in [
            "/loop",
            "create_goal",
            "get_goal",
            "update_goal",
            "Codex Goals",
            "OpenAI Goals",
            "ChatGPT Goals",
        ]:
            with self.subTest(token=host_specific_token):
                self.assertNotIn(host_specific_token, text)

    def test_sshx_termination_gate_seats_and_existing_contract_references(self) -> None:
        text = read(SKILL)
        termination = section(text, "## Termination Gate", "## Termination Truth Table")
        roles = re.findall(r"^- `(criterion-evidence|residual-gap|claim-integrity)`: .+$", termination, re.MULTILINE)
        self.assertEqual(tuple(roles), TERMINATION_ROLES)
        for contract in [
            "`WorkerDelegationContract`",
            "`## Result Envelope`",
            "`## Worker Completion Contract`",
            "`## No Context Pollution`",
            "`## Reasoning Discipline`",
        ]:
            self.assertIn(contract, termination)
        self.assertIn("every `normalized_goal` clause, constraint, and `success_criteria` item", termination)
        self.assertIn("Absence of evidence is never satisfaction", termination)
        self.assertIn("answering the existing `iteration_question` with one concrete remaining difference", termination)
        self.assertIn("name the responsible party", termination)
        self.assertIn("must not broaden into a generic improvement search", termination)
        for proxy in [
            "review exit",
            "verdict count",
            "caller narrative",
            "host-provided capability",
            "lifecycle milestone",
        ]:
            self.assertIn(proxy, termination)
        verdict_block = termination.split("Each termination seat returns one of:\n\n", 1)[1]
        self.assertEqual(
            set(re.findall(r"^- `([^`]+)`$", verdict_block, re.MULTILINE)),
            TERMINATION_VERDICTS,
        )
        self.assertIn("returns a judgment, never a routing action", termination)
        self.assertIn("Termination flights use the existing `worker_flights` block", termination)
        self.assertIn("`SshxWorkerFlightRecord.stage` set to `termination`", termination)
        self.assertNotIn("surfaces its compact note", termination.lower())

    def test_sshx_termination_dispatch_policy_requires_exactly_three_seats(self) -> None:
        termination = section(read(SKILL), "## Termination Gate", "## Termination Truth Table")
        self.assertIn(
            "Protocol policy, not a mathematical consequence: dispatch exactly three purpose-built, independent, context-isolated termination seats",
            termination,
        )

    def test_sshx_termination_routing_is_exhaustive_and_fail_closed(self) -> None:
        possible_results: tuple[JsonValue, ...] = ("satisfied", "unsatisfied", "abstain", None, "invalid")
        permitted = 0
        for verdicts in product(possible_results, repeat=3):
            seat_results = tuple(zip(TERMINATION_ROLES, verdicts, strict=True))
            for consensus_source in ("termination-seats", "caller", "review-exit"):
                with self.subTest(verdicts=verdicts, consensus_source=consensus_source):
                    resolution = resolve_termination_claim(
                        seat_results,
                        consensus_source=consensus_source,
                        owner_assignment=("engineering", "work-target-engineering-path"),
                    )
                    if consensus_source != "termination-seats":
                        self.assertEqual(resolution.truth_table_exit, "reject fake termination consensus")
                    elif "unsatisfied" in verdicts:
                        self.assertEqual(
                            resolution.truth_table_exit,
                            "withhold claim; continue against the named goal gap",
                        )
                    elif verdicts == ("satisfied",) * 3:
                        self.assertEqual(resolution.truth_table_exit, "termination claim permitted")
                        permitted += 1
                    else:
                        self.assertEqual(
                            resolution.truth_table_exit,
                            "withhold claim; escalate with the unresolved evidence gap",
                        )
        self.assertEqual(permitted, 1)

    def test_sshx_termination_routing_classifies_invalid_values_without_comparing_them(self) -> None:
        cases = {
            "nested-object-valued": (
                {"unexpected": {"nested": [True, None, 3.5]}},
                "satisfied",
                "satisfied",
            ),
            "nested-array-valued": (["unexpected", {"nested": [False, 7]}], "satisfied", "satisfied"),
            "object-and-array-valued": ({"unexpected": "result"}, ["unexpected", "result"], "satisfied"),
            "equality-raising-valued": (EqualityRaises(), "satisfied", "satisfied"),
        }
        for name, verdicts in cases.items():
            with self.subTest(case=name):
                seat_results = tuple(zip(TERMINATION_ROLES, verdicts, strict=True))
                self.assertEqual(
                    resolve_termination_claim(seat_results).truth_table_exit,
                    "withhold claim; escalate with the unresolved evidence gap",
                )

        hostile = EqualityRaises()
        invalid_role_results = ((hostile, "satisfied"),) + tuple(
            (role, "satisfied") for role in TERMINATION_ROLES[1:]
        )
        self.assertEqual(
            resolve_termination_claim(invalid_role_results).truth_table_exit,
            "reject fake termination consensus",
        )
        satisfied_results = tuple((role, "satisfied") for role in TERMINATION_ROLES)
        self.assertEqual(
            resolve_termination_claim(satisfied_results, consensus_source=hostile).truth_table_exit,
            "reject fake termination consensus",
        )

    def test_sshx_termination_owner_routing_is_exhaustive_by_behavior(self) -> None:
        unsatisfied_results = tuple(
            (role, "unsatisfied" if role == TERMINATION_ROLES[0] else "satisfied")
            for role in TERMINATION_ROLES
        )
        decision_classes = ("engineering", "orchestration", "product-governance-boundary")
        declared_owners = ("work-target-engineering-path", "caller", "maintainer", "another-owner")
        expected_routes = {
            ("engineering", "work-target-engineering-path"): (
                "re-enter review-fix through the work-target engineering path"
            ),
            ("orchestration", "caller"): "await new evidence from the authorized caller",
            ("product-governance-boundary", "maintainer"): (
                "stop and escalate for a maintainer-authorized correction"
            ),
        }
        review_fix_routes = 0
        for decision_class, declared_owner in product(decision_classes, declared_owners):
            with self.subTest(decision_class=decision_class, declared_owner=declared_owner):
                resolution = resolve_termination_claim(
                    unsatisfied_results,
                    owner_assignment=(decision_class, declared_owner),
                )
                expected_route = expected_routes.get(
                    (decision_class, declared_owner),
                    "stop and escalate to the declared owner",
                )
                self.assertEqual(
                    resolution.truth_table_exit,
                    "withhold claim; continue against the named goal gap",
                )
                self.assertEqual(resolution.gap_route, expected_route)
                if resolution.gap_route == "re-enter review-fix through the work-target engineering path":
                    review_fix_routes += 1
        self.assertEqual(review_fix_routes, 1)

        for owner_assignment in [
            ("engineering", None),
            ("orchestration", ""),
            ({"ambiguous": ["owner"]}, "caller"),
        ]:
            with self.subTest(owner_assignment=owner_assignment):
                resolution = resolve_termination_claim(
                    unsatisfied_results,
                    owner_assignment=owner_assignment,
                )
                self.assertEqual(
                    resolution.gap_route,
                    "stop and escalate with the unresolved ownership gap",
                )

    def test_sshx_termination_resolution_consumes_one_pass_budget_unit(self) -> None:
        satisfied_results = tuple((role, "satisfied") for role in TERMINATION_ROLES)
        unsatisfied_results = ((TERMINATION_ROLES[0], "unsatisfied"),) + satisfied_results[1:]
        abstained_results = ((TERMINATION_ROLES[0], "abstain"),) + satisfied_results[1:]
        row_cases = {
            "fake-consensus": (satisfied_results, "caller", "reject fake termination consensus"),
            "permitted": (satisfied_results, "termination-seats", "termination claim permitted"),
            "unsatisfied": (
                unsatisfied_results,
                "termination-seats",
                "withhold claim; continue against the named goal gap",
            ),
            "unresolved": (
                abstained_results,
                "termination-seats",
                "withhold claim; escalate with the unresolved evidence gap",
            ),
        }
        for name, (seat_results, source, expected_exit) in row_cases.items():
            with self.subTest(case=name):
                resolution = resolve_termination_claim(
                    seat_results,
                    consensus_source=source,
                    owner_assignment=("engineering", "work-target-engineering-path"),
                    pass_budget_remaining=2,
                )
                self.assertEqual(resolution.truth_table_exit, expected_exit)
                self.assertEqual(resolution.pass_budget_remaining, 1)
                self.assertEqual(resolution.termination_evaluations_consumed, 1)
                self.assertEqual(resolution.fake_consensus_correction_allowed, name == "fake-consensus")

        last_unit = resolve_termination_claim(
            satisfied_results,
            consensus_source="caller",
            pass_budget_remaining=1,
        )
        self.assertEqual(last_unit.pass_budget_remaining, 0)
        self.assertFalse(last_unit.fake_consensus_correction_allowed)
        at_ceiling = resolve_termination_claim(
            satisfied_results,
            pass_budget_remaining=last_unit.pass_budget_remaining,
        )
        self.assertEqual(at_ceiling.truth_table_exit, "withhold claim; pass budget exhausted")
        self.assertEqual(at_ceiling.pass_budget_remaining, 0)
        self.assertEqual(at_ceiling.termination_evaluations_consumed, 0)

        for invalid_budget in (None, True, 1.5, [], {"remaining": 1}, EqualityRaises()):
            with self.subTest(invalid_budget=invalid_budget):
                resolution = resolve_termination_claim(
                    satisfied_results,
                    pass_budget_remaining=invalid_budget,
                )
                self.assertEqual(
                    resolution.truth_table_exit,
                    "withhold claim; pass budget exhausted",
                )
                self.assertEqual(resolution.termination_evaluations_consumed, 0)

        first = resolve_termination_claim(satisfied_results, consensus_source="caller", pass_budget_remaining=3)
        second = resolve_termination_claim(
            satisfied_results,
            consensus_source="caller",
            pass_budget_remaining=first.pass_budget_remaining,
        )
        self.assertEqual((first.pass_budget_remaining, second.pass_budget_remaining), (2, 1))

    def test_sshx_termination_routing_requires_exact_named_roster(self) -> None:
        satisfied = "satisfied"
        exact = tuple((role, satisfied) for role in TERMINATION_ROLES)
        cases = {
            "count-0": (),
            "count-1": exact[:1],
            "count-2-missing-role": exact[:2],
            "count-3-exact": exact,
            "count-4-extra-role": exact + (("extra-role", satisfied),),
            "duplicate-role": ((TERMINATION_ROLES[0], satisfied),) * 2 + exact[1:2],
            "unknown-role": exact[:2] + (("unknown-role", satisfied),),
        }
        permitted = []
        for name, roster in cases.items():
            with self.subTest(case=name):
                exit_name = resolve_termination_claim(roster).truth_table_exit
                if name == "count-3-exact":
                    self.assertEqual(exit_name, "termination claim permitted")
                    permitted.append(name)
                else:
                    self.assertEqual(exit_name, "reject fake termination consensus")
        self.assertEqual(permitted, ["count-3-exact"])

    def test_sshx_termination_routing_accepts_a_fallback_recovered_result(self) -> None:
        exhausted_origin = {
            "status": "abstained",
            "retry_budget": 1,
            "attempt": 1,
            "result_envelope_ref": "",
            "completion_sentinel_ref": "",
        }
        self.assertEqual(
            resolve_failed_flight(exhausted_origin, fallback_available=True),
            "fallback-highest-priority-untried-carrier",
        )

        recovered_fallback = {
            "status": "terminal",
            "retry_budget": 1,
            "attempt": 1,
            "result_envelope_ref": "result.json",
            "completion_sentinel_ref": "completion.sentinel",
        }
        self.assertEqual(resolve_failed_flight(recovered_fallback, fallback_available=False), "complete")
        recovered_results = tuple((role, "satisfied") for role in TERMINATION_ROLES)
        self.assertEqual(
            resolve_termination_claim(recovered_results).truth_table_exit,
            "termination claim permitted",
        )

    def test_sshx_termination_truth_table_and_boundedness_contract(self) -> None:
        text = read(SKILL)
        truth_table = section(text, "## Termination Truth Table", "## Boundaries")
        for row in [
            "| caller judgment, a review exit, or any roster other than exactly the three distinct named isolated termination seats presented as termination consensus | `reject fake termination consensus` |",
            "| unanimous `satisfied` | `termination claim permitted` |",
            "| any `unsatisfied` | `withhold claim; continue against the named goal gap` |",
            "| no `unsatisfied` and any `abstain`, invalid or missing seat result | `withhold claim; escalate with the unresolved evidence gap` |",
        ]:
            self.assertIn(row, truth_table)
        for anchor in [
            "rows are evaluated in this order and are complete and, under this evaluation order, unambiguous",
            "unanimous `satisfied` means one valid `satisfied` result from each of the exactly three distinct named termination seats",
            "The table evaluates only the presenting source and the resulting seat roster and results",
            "a fallback-recovered result is a valid result like any other",
            "Roster means the dispatch-time recorded named role identities",
            "a named role present without a valid result remains in the roster as a missing result",
            "meta-judge has no termination verdict of its own",
            "routes that gap according to `harness.decision_ownership`",
            "work-target engineering correction assigned to the existing engineering path re-enters the review-`fix` path in `## Fix Or Done`",
            "required rerun review triplet must finish before any new termination candidate",
            "caller-owned orchestration remains with the authorized caller",
            "only new evidence from that owner may form a later candidate",
            "maintainer-owned product, governance, or boundary gap stops and escalates",
            "later routing requires a maintainer-authorized correction under `## Goal Contract`",
            "Any gap whose declared owner does not match a route above stops and escalates to that declared owner",
            "invalid ownership stops and escalates with the unresolved ownership gap",
            "Failure withholds the affirmative claim",
            "not authority to keep working indefinitely",
            "carrier outage must not become an unbounded work generator",
            "existing `abstain` discipline",
            "gate may reach a completed result at most once per candidate affirmative termination",
            "Each evaluation of the termination truth table consumes one `pass_budget` unit owned in `## Fix Or Done`",
            "creates no nested budget",
            "never gates its own exit",
            "presentation rejected as fake termination consensus is not a completed gate run and may be corrected only while `pass_budget` remains",
            "later candidate is permitted only after new evidence or an authorized correction",
            "When `pass_budget` is exhausted, report the unresolved blocker and do not certify satisfaction",
        ]:
            self.assertIn(anchor, truth_table)
        self.assertIn("This gate grants no authority over the host mechanism", text)

    def test_sshx_nonachievement_routes_report_honestly(self) -> None:
        text = read(SKILL)
        delegation = section(text, "## Worker Delegation", "## Result Envelope")
        fix_or_done = section(text, "## Fix Or Done", "## Termination Gate")
        termination = section(text, "## Termination Gate", "## Boundaries")
        self.assertIn(
            "Only when no eligible untried carrier remains or every fallback fails to produce terminal completion is the result `abstain`",
            delegation,
        )
        self.assertIn(
            "Stop when `pass_budget` owned below is exhausted and report remaining blockers honestly",
            fix_or_done,
        )
        self.assertIn("reaching zero reports every unresolved blocker honestly", fix_or_done)
        self.assertIn(
            "A withheld claim reports honestly under the existing `abstain` discipline",
            termination,
        )
        self.assertIn(
            "When `pass_budget` is exhausted, report the unresolved blocker and do not certify satisfaction",
            termination,
        )

    def test_sshx_termination_uses_only_sealed_stop_inputs(self) -> None:
        termination = section(read(SKILL), "## Termination Gate", "## Termination Truth Table")
        orientation = (True, True, True)
        self.assertEqual(
            (
                all(
                    anchor in termination
                    for anchor in [
                        "Method stop, a protocol or review exit, and `GoalArtifact` completion are separate predicates",
                        "seal the current affirmative candidate, the feasible termination decision set",
                        "affirmative claim is computed only from that sealed set and orientation",
                        "late narrative and logs are not stop inputs",
                    ]
                ),
                sealed_stop_permits_claim(
                    current_candidate="candidate",
                    feasible_set=frozenset({"candidate"}),
                    decisions=("satisfied", "satisfied", "satisfied"),
                    sourced_orientation=orientation,
                ),
                sealed_stop_permits_claim(
                    current_candidate=None,
                    feasible_set=frozenset({"candidate"}),
                    decisions=("satisfied",) * 3,
                    sourced_orientation=orientation,
                ),
                sealed_stop_permits_claim(
                    current_candidate="candidate",
                    feasible_set=frozenset(),
                    decisions=("satisfied",) * 3,
                    sourced_orientation=orientation,
                ),
                sealed_stop_permits_claim(
                    current_candidate="candidate",
                    feasible_set=frozenset({"other"}),
                    decisions=("satisfied",) * 3,
                    sourced_orientation=orientation,
                ),
                sealed_stop_permits_claim(
                    current_candidate="candidate",
                    feasible_set=frozenset({"candidate"}),
                    decisions=("satisfied",) * 3,
                    sourced_orientation=(True, False, True),
                    late_narrative=True,
                ),
            ),
            (True, True, False, False, False, False),
        )

    def test_sshx_mathematical_applicability_behavior(self) -> None:
        reasoning = section(read(SKILL), "## Reasoning Discipline", "## Thinking Panel")
        self.assertEqual(
            (
                all(
                    anchor in reasoning
                    for anchor in [
                        "recorded state instantiates every hypothesis",
                        "name applied by analogy carries no blocking, convergence, or completion force",
                        "missing or disputed instantiation is `ASSUMED-UNVERIFIED`",
                        "false instantiation makes the conclusion inapplicable",
                    ]
                ),
                mathematical_conclusion_force(("verified", "verified")),
                mathematical_conclusion_force(("verified", "missing")),
                mathematical_conclusion_force(("verified", "disputed")),
                mathematical_conclusion_force(("verified", "false")),
            ),
            (True, "binding", "ASSUMED-UNVERIFIED", "ASSUMED-UNVERIFIED", "inapplicable"),
        )

    def test_sshx_boundary_predicates_have_single_definitions(self) -> None:
        # Source-regression only: this checks unique definitions and references, not runtime enforcement.
        text = read(SKILL)
        reasoning = text[heading_index(text, "## Reasoning Discipline") : heading_index(text, "## Thinking Panel")]
        capability_definition = "`CapabilityOverlap` is the candidate-solution boundary check"
        threat_definition = "`ThreatEligibility` is the review-finding boundary check"
        grounding_definition = "`BlockingAuthority` is the single admissibility rule"
        self.assertEqual(text.count(capability_definition), 1)
        self.assertEqual(text.count(threat_definition), 1)
        self.assertEqual(text.count(grounding_definition), 1)
        self.assertIn(capability_definition, reasoning)
        self.assertIn(threat_definition, reasoning)
        self.assertIn(grounding_definition, reasoning)
        self.assertIn("These are independent checks that share the `harness` fact source", reasoning)

    def test_sshx_blocking_authority_contract(self) -> None:
        text = read(SKILL)
        reasoning = section(text, "## Reasoning Discipline", "## Thinking Panel")
        definition = (
            "`BlockingAuthority` is the single admissibility rule for every input that would hold a candidate "
            "out of `implement`, turn a review toward `fix`, or withhold `satisfied`"
        )
        self.assertEqual(text.count(definition), 1)
        self.assertIn(definition, reasoning)
        for anchor in [
            "Advisory is the default; blocking is the exception",
            "exactly two conjuncts that the input itself must name",
            "first, the `normalized_goal` clause, `constraints` item, or `success_criteria` item that the work as built fails",
            "second, evidence in the work as built that shows the failure",
            "a current call site or input path, an observed failure, a failing verification command, a wrong result",
            "against a satisfaction claim, the absence of the evidence the named term demands",
            "An input that names both is blocking, and stays blocking however expensive, inconvenient, or late the repair is",
            "a named basis that evidence shows to be false no longer counts as named",
            "keeps its full blocking force until the dispute is settled against evidence",
            "no one may call an input advisory because its named basis is unpersuasive",
            "An input that names fewer than both is advisory",
            "its downgrade record carries what it named, or that it named none, in its own words and never a paraphrase",
            "never the sole basis of a `revise`, `reject`, `abstain`, blocking finding, `unsatisfied`, or any element of a concrete plan",
            "The same two conjuncts admit a plan element",
            "names the `GoalArtifact` term that demands it or a current consumer (an existing call site)",
            "a test introduced together with it may corroborate that basis but never creates it",
            "Failure is objective, not semantic",
            "asks only whether both conjuncts are named, never how well they are evidenced",
            "it removes no actual defect",
            "a reachable failure, a trusted-party mistake, an omission, and a stated uncertainty each name both",
        ]:
            self.assertIn(anchor, reasoning)
        self.assertEqual(text.count("The same two conjuncts admit a plan element"), 1)
        for forbidden in [
            "an unverified path has no blocking force",
            "an unverified named basis loses blocking force",
            "decisiongrounding",
            "absorbedfailure",
            "specification style for protocol edge-case completeness",
        ]:
            self.assertNotIn(forbidden, text.lower())
        axis_separation = (
            "`BlockingAuthority` asks only whether a decision input may block; "
            "`ThreatEligibility` asks who the actor is; `parsimony` asks how much mechanism; "
            "`proportional-containment` asks how far it binds; `worth` asks whether to pay at all; "
            "and the aesthetic verdict asks whether the remaining form is coherent."
        )
        self.assertEqual(text.count(axis_separation), 1)
        self.assertIn(axis_separation, reasoning)
        self.assertIn(
            "a third independent check sharing the `GoalArtifact` and `harness` fact sources with the two above",
            reasoning,
        )
        self.assertEqual(
            tuple(
                blocking_force(
                    names_goal_term=goal_term,
                    names_work_evidence=evidence,
                    basis_shown_false=shown_false,
                    basis_disputed=disputed,
                )
                for goal_term, evidence, shown_false, disputed in [
                    (True, True, False, False),
                    (True, True, False, True),
                    (True, True, True, False),
                    (True, False, False, False),
                    (False, True, False, False),
                    (False, False, False, True),
                ]
            ),
            ("blocking", "blocking", "advisory", "advisory", "advisory", "advisory"),
        )

    def test_sshx_dependency_closure_admission_is_antitone(self) -> None:
        reasoning = section(read(SKILL), "## Reasoning Discipline", "## Thinking Panel")
        direct_dependency = frozenset({"candidate-output"})
        small_closure = frozenset({"candidate-input"})
        enlarged_closure = small_closure | direct_dependency
        self.assertEqual(
            (
                all(
                    anchor in reasoning
                    for anchor in [
                        "recorded use to generate, tune, or select reaches the candidate's dependency closure",
                        "Enlarging that closure may only remove admission, never restore it",
                        "shared model family, inherited repository prior, or disclosed prior alone does not prove contamination",
                        "only a recorded dependency path does",
                    ]
                ),
                independent_evidence_admitted(
                    recorded_dependencies=direct_dependency,
                    candidate_dependency_closure=small_closure,
                ),
                independent_evidence_admitted(
                    recorded_dependencies=direct_dependency,
                    candidate_dependency_closure=enlarged_closure,
                ),
                independent_evidence_admitted(
                    recorded_dependencies=frozenset(),
                    candidate_dependency_closure=enlarged_closure,
                ),
            ),
            (True, True, False, True),
        )

    def test_sshx_absorbed_failure_is_advisory(self) -> None:
        text = read(SKILL)
        reasoning = section(text, "## Reasoning Discipline", "## Thinking Panel")
        for anchor in [
            "Inputs that name no second conjunct include an imagined input",
            "a harm that the declared recovery path already absorbs — a retry, a carrier fallback, a fail-closed stop, an honestly reported `abstain`, or an escalation to the declared owner — with no residue visible to `GoalArtifact`",
            "a defect in this run's own transcript or records rather than in the work",
            "detail whose omission changes no `GoalArtifact` decision",
            "A residue that escapes the recovery path is a second conjunct: a wrong result accepted as correct, a success or satisfaction claim that is not true, state left corrupted or unrecoverable, an unbounded work generator, a violated contract term that nothing detects, or a `GoalArtifact` success criterion the recovery path itself cannot satisfy",
            "a recovery path that is itself missing, unreachable, or undeclared absorbs nothing",
            "never from how unlikely, inconvenient, expensive, or late the failure is",
            "No per-case diagnosis, error taxonomy, or dedicated repair path is owed for an absorbed class",
            "deciding which specific error occurred earns its place only when a `GoalArtifact`-named decision routes differently on that answer",
        ]:
            self.assertEqual(text.count(anchor), 1)
            self.assertIn(anchor, reasoning)

    def test_sshx_enumeration_escape_requires_verified_construction(self) -> None:
        text = read(SKILL)
        reasoning = section(text, "## Reasoning Discipline", "## Thinking Panel")
        verification = text[heading_index(text, "## Verification") :]
        self.assertEqual(
            (
                all(
                    fragment in reasoning
                    for fragment in [
                        "That list is illustrative, not a closure, and enumeration is not itself an absorber",
                        "every finite listing of cases is escaped by a fixed-point-free self-application",
                        "an adversarial seat's charter is such a constructor",
                        "no extension of this or any register can complete it",
                        "the defense against an unlisted case is the two-conjunct test together with the declared recovery path, never another entry",
                        "Extending an enumeration over an absorbed class is an ugly defect under the aesthetic verdict, not diligence",
                        "Without that construction hypothesis, a separately proven finite-domain completeness result remains admissible",
                    ]
                ),
                "no finite listing of cases is escape-free" not in reasoning,
                "the longer the list grows the likelier" not in reasoning,
                "when the same verified construction hypothesis applies, the register cannot be completed" in verification,
                finite_listing_closure_force(
                    fixed_point_free_constructor_verified=True,
                    finite_domain_completeness_proven=False,
                ),
                finite_listing_closure_force(
                    fixed_point_free_constructor_verified=False,
                    finite_domain_completeness_proven=True,
                ),
                finite_listing_closure_force(
                    fixed_point_free_constructor_verified=False,
                    finite_domain_completeness_proven=False,
                ),
                finite_listing_has_diagonal_escape((0, 1, 0), {0: 1, 1: 0}),
                finite_listing_has_diagonal_escape((0, 1, 0), {0: 0, 1: 1}),
            ),
            (
                True,
                True,
                True,
                True,
                "not-closure-evidence",
                "closure-evidence-admissible",
                "ASSUMED-UNVERIFIED",
                True,
                False,
            ),
        )

    def test_sshx_preventive_defense_requires_named_occurrence(self) -> None:
        text = read(SKILL)
        reasoning = section(text, "## Reasoning Discipline", "## Thinking Panel")
        anchor = (
            "a hostile or extreme condition that ordinary operation does not exercise, unless a recorded "
            "occurrence — an incident in this work target's own evidence or a documented external precedent "
            "for the same mechanism — shows it"
        )
        self.assertEqual(text.count(anchor), 1)
        self.assertIn(anchor, reasoning)
        self.assertIn("never from how unlikely, inconvenient, expensive, or late the failure is", reasoning)

    def test_sshx_blocking_gap_repair_order_is_main_path_first(self) -> None:
        text = read(SKILL)
        fix_or_done = section(text, "## Fix Or Done", "## Termination Gate")
        anchor = (
            "When a pass carries more than one blocking goal gap, repair them in "
            "`GoalArtifact` order — a gap that blocks `normalized_goal` before one that "
            "blocks only its periphery — so the main path is repaired first."
        )
        self.assertEqual(text.count(anchor), 1)
        self.assertIn(anchor, fix_or_done)
        self.assertIn("Stop when `pass_budget` owned below is exhausted", fix_or_done)

    def test_sshx_depth_discipline_contract(self) -> None:
        text = read(SKILL)
        reasoning = section(text, "## Reasoning Discipline", "## Thinking Panel")
        self.assertEqual(text.count("Depth discipline:"), 1)
        self.assertIn("Depth discipline:", reasoning)
        for anchor in [
            "钻牛角尖 (rabbit-holing) is the failure this discipline prevents, never the standard of care it demands",
            "at the shallowest depth that still changes a `GoalArtifact`-named decision, a verdict, or a routing exit",
            "ask one bounded question: would the additional detail change any of those?",
            "stop and name the stop in the reasoning-discipline note",
            "exhaustive enumeration past verdict-settling evidence is itself an ugly defect",
            "Chase a premise only as far as the verdict depends on it",
            "a premise the verdict does not depend on needs no verification and no mark",
            "The bound caps elaboration and advisory volume, never a seat's assigned coverage, and never what `BlockingAuthority` admits",
        ]:
            self.assertIn(anchor, reasoning)
        self.assertIn(
            "for each candidate materially weighed; stating the verified-premise or "
            "`ASSUMED-UNVERIFIED` status needed for the verdict; and naming any "
            "depth-bound stop that settled a judgment",
            reasoning,
        )
        trigger = section(text, "## Trigger", "## Goal Contract")
        self.assertIn(
            "whether to run it follows decision risk, not available budget",
            trigger,
        )

    def test_sshx_prospective_evidence_is_reasoning_owned(self) -> None:
        text = read(SKILL)
        reasoning = section(text, "## Reasoning Discipline", "## Thinking Panel")
        outside_reasoning = text.replace(reasoning, "", 1)
        for anchor in [
            "Retrospective fit is not prospective evidence",
            "only replays facts already present in its visible inputs",
            "supports no causal, transfer, benefit, or future-performance claim",
            "An explanation compatible with every possible outcome carries zero prospective weight",
            "used to settle a `GoalArtifact`-named decision",
            "the check or observation that could falsify it before consulting its outcome",
            "forward commitment must not be replaced by post-hoc fitting",
            "A prospective claim with no stated falsifier is `ASSUMED-UNVERIFIED`",
            "follows the existing `ASSUMED-UNVERIFIED` dispositions",
        ]:
            self.assertIn(anchor, reasoning)
            self.assertNotIn(anchor, outside_reasoning)

    def test_sshx_blocking_authority_stage_references_and_downgrade_guards(self) -> None:
        text = read(SKILL)
        thinking = section(text, "## Thinking Panel", "## Design Truth Table")
        design = section(text, "## Design Truth Table", "## Implementation Worker")
        review = section(text, "## Review Truth Table", "## Fix Or Done")
        termination_gate = section(text, "## Termination Gate", "## Termination Truth Table")
        termination_table = section(text, "## Termination Truth Table", "## Boundaries")

        for contract_section in [thinking, design, review, termination_gate, termination_table]:
            self.assertIn("`BlockingAuthority`", contract_section)
        for anchor in [
            "every proposed plan element and every `propose`, `revise`, `reject`, or `abstain` basis",
            "states the `GoalArtifact` term and evidence that make each basis blocking, or the `GoalArtifact` term or current consumer that admits each plan element",
            "An advisory basis is not a goal gap",
            "machinery that only defends against one must not enter a proposed plan",
        ]:
            self.assertIn(anchor, thinking)

        focused_round = design.split(
            "When a seat's `SshxResultEnvelope.conclusion` records", 1
        )[1].split("\n\n", 1)[0]
        for anchor in [
            "the causal prediction recorded in that conclusion is falsifiable rather than a preference;",
            "provided the objection passes `BlockingAuthority`",
            "An advisory objection does not trigger a `FocusedRound`",
            "checks only whether the seat named both conjuncts and must not assess their persuasiveness",
            "named a basis whose correctness is disputed still triggers the round because disputed is not absent",
            "records that decline in the existing `finding_downgrades` record under the same own-words requirement that governs downgrades",
        ]:
            self.assertIn(anchor, focused_round)
        for anchor in [
            "An objection that fails `BlockingAuthority` is not an unclosed `GoalArtifact` goal gap and does not by itself hold the exit out of `implement`: the meta-judge records it as advisory in the existing `finding_downgrades` record as `BlockingAuthority` requires.",
            "Disputed grounding stays blocking",
            "not permission to set aside a reachable defect",
        ]:
            self.assertIn(anchor, design)
        for anchor in [
            "Every blocking finding must name both `BlockingAuthority` conjuncts under `## Reasoning Discipline`",
            "the `GoalArtifact` term the work as built fails and the evidence in the work that shows it",
            "fails `ThreatEligibility` or `BlockingAuthority`",
            "A `BlockingAuthority` downgrade is objective",
            "it is recorded as `BlockingAuthority` requires and never assesses persuasiveness; disputed grounding stays blocking",
            "only for threat-model ineligibility or an advisory input",
            "never because a finding is inconvenient",
            "never sets aside a reachable defect",
            "missing, ambiguous, or stale harness declaration",
            "never a downgrade shield",
        ]:
            self.assertIn(anchor, review)
        self.assertIn(
            "A blocking finding that fails `ThreatEligibility` or `BlockingAuthority` is downgraded by the meta-judge to an advisory with its reason recorded, then the remaining verdicts are routed again.",
            review,
        )
        self.assertIn("named difference must pass `BlockingAuthority`", termination_gate)
        self.assertIn("an advisory worry is not a remaining difference", termination_gate)
        for anchor in [
            "Each termination seat applies `BlockingAuthority` itself before returning",
            "An `unsatisfied` that names both conjuncts keeps its full force and may never be converted into permission by calling it unpersuasive",
            "An `unsatisfied` that names no `GoalArtifact` term is advisory exactly as under `## Review Truth Table`",
            "the meta-judge records it in `finding_downgrades` as `BlockingAuthority` requires",
            "re-dispatches that seat once on the same sealed candidate with that record in its brief as part of the same evaluation",
            "treating a repeated `unsatisfied` that again names no `GoalArtifact` term as `abstain`",
        ]:
            self.assertIn(anchor, termination_table)
        self.assertNotIn("never a meta-judge downgrade path", text)
        self.assertEqual(
            (
                termination_unsatisfied_route(names_goal_term=True, already_redispatched=False),
                termination_unsatisfied_route(names_goal_term=False, already_redispatched=False),
                termination_unsatisfied_route(names_goal_term=False, already_redispatched=True),
            ),
            ("keep-full-force", "record-advisory-and-redispatch-once", "treat-as-abstain"),
        )

    def test_sshx_triplets_require_reasoning_discipline_in_conclusion(self) -> None:
        text = read(SKILL)
        reasoning_section = text[heading_index(text, "## Reasoning Discipline") : heading_index(text, "## Thinking Panel")]
        thinking_section = text[heading_index(text, "## Thinking Panel") : heading_index(text, "## Design Truth Table")]
        review_section = text[heading_index(text, "## Review Triplet") : heading_index(text, "## Review Truth Table")]

        for required in [
            "Reference-frame",
            "applicable mature theory, engineering principle, industry best practice",
            "mature industry case, mature pattern",
            "constraint framework",
            "known-good shape",
            "`no applicable mature theory found`",
            "root-cause and minimal-path re-check against `GoalArtifact`",
        ]:
            self.assertIn(required, reasoning_section)
        self.assertEqual(
            text.count(
                "sshx's essence is independent context-isolated perspectives that oppose ugliness and waste to converge on an answer that is both beautiful and worth its cost"
            ),
            1,
        )
        self.assertIn(
            "sshx's essence is independent context-isolated perspectives that oppose ugliness and waste to converge on an answer that is both beautiful and worth its cost",
            reasoning_section,
        )
        for required in [
            "for each candidate approach materially weighed",
            "each rejected alternative whose rejection changed the conclusion",
            "a micro-variation that changed no decision needs no separate verdict",
            "why the approach is ugly as a specific locatable defect",
            "what the beautiful form would be",
            "leaked abstraction",
            "duplicated source of truth",
            "bad coupling",
            "asymmetry",
            "lying name",
            "hidden intent",
            "single-source-of-truth",
            "intent-revealing",
        ]:
            self.assertIn(required, reasoning_section)
        # 美不美 stays the single strengthened aesthetic form judgment: a symmetric verdict, not a presumed indictment.
        for required in [
            "美不美",
            "is it beautiful?",
            "no material defect found",
            "gold-plating past `GoalArtifact` is itself an ugly defect",
        ]:
            self.assertIn(required, reasoning_section)
        # 值不值 (worth) is an independent SEAT, not a cross-cutting lens: it must not live in Reasoning Discipline,
        # or every seat would be homogenized into re-deriving the same value verdict.
        self.assertNotIn("值不值", reasoning_section)
        self.assertNotIn("worth (值不值", reasoning_section.lower())
        self.assertNotIn("## Worth", text)
        self.assertNotIn("worth_marker", text)
        self.assertNotIn("worth_daemon", text)
        for required in [
            "seek truth from facts",
            "verify every factual premise against actual evidence",
            "source artifact or line",
            "command result",
            "test assertion",
            "ASSUMED-UNVERIFIED",
            "verified before routing",
            "`GoalArtifact` goal gap",
            "abstain trigger",
            "never silently rely",
        ]:
            self.assertIn(required, reasoning_section)
        boundaries = section(text, "## Boundaries", "## Baseline Failure Mode")
        for boundary in [
            "prompt-level only",
            "runtime API",
            "daemon",
            "CLI",
            "parsed schema",
            "marker family",
            "lifecycle authority",
            "second transcript channel",
        ]:
            self.assertIn(boundary, boundaries)
        self.assertIn(
            "This does not override `GoalArtifact`, assigned bias or review focus, truth tables, or allowed verdict sets",
            reasoning_section,
        )
        self.assertEqual(len(re.findall(r"^## Reasoning Discipline$", text, flags=re.MULTILINE)), 1)
        for forbidden in [
            "## Aesthetic Frame",
            "## Aesthetic Discipline",
            "## Seek Truth From Facts",
        ]:
            self.assertNotIn(forbidden, text)
        for forbidden in [
            "why_ugly:",
            "beautiful_form:",
            "verified_premises:",
            "assumed_unverified:",
            "reasoning_discipline:",
            "aesthetic_marker",
            "truth_marker",
            "aesthetic_daemon",
            "truth_daemon",
        ]:
            self.assertNotIn(forbidden, text.lower())

        self.assertIn(
            "Before proposing, revising, rejecting, or abstaining, each seat must apply `## Reasoning Discipline`",
            thinking_section,
        )
        self.assertIn(
            "surface the compact reasoning-discipline note in `SshxResultEnvelope.conclusion` before returning a verdict",
            thinking_section,
        )
        self.assertLess(
            thinking_section.index("Before proposing, revising, rejecting, or abstaining"),
            thinking_section.index("Each seat returns one of:"),
        )
        self.assertIn(
            "Before approving, commenting, or rejecting, each reviewer must apply `## Reasoning Discipline`",
            review_section,
        )
        self.assertIn(
            "surface the compact reasoning-discipline note in `SshxResultEnvelope.conclusion` before returning a verdict",
            review_section,
        )
        self.assertLess(
            review_section.index("Before approving, commenting, or rejecting"),
            review_section.index("Each reviewer returns one of:"),
        )
        self.assertIn(
            "applicable mature theory, engineering principle, industry best practice",
            reasoning_section,
        )
        self.assertNotIn(
            "applicable mature theory, engineering principle, industry best practice",
            thinking_section.lower(),
        )
        self.assertNotIn(
            "applicable mature theory, engineering principle, industry best practice",
            review_section.lower(),
        )

    def test_sshx_thinking_panel_seats_and_anchors_root_cause(self) -> None:
        text = read(SKILL)
        thinking_start = text.index("## Thinking Panel")
        design_truth_start = text.index("## Design Truth Table")
        thinking_section = text[thinking_start:design_truth_start]
        self.assertIn(
            "run six whole-picture philosopher seats before choosing a plan",
            thinking_section,
        )
        for seat in [
            "`teleology`",
            "`parsimony`",
            "`fidelity`",
            "`natural-ownership`",
            "`proportional-containment`",
            "`worth`",
        ]:
            self.assertIn(seat, thinking_section)
        self.assertIn(
            "coupled **must-clash locus dyad**",
            thinking_section,
        )
        # 值不值 is the sixth seat: an independent objective distinct from parsimony / containment / aesthetic.
        self.assertIn("`worth` (值不值 — is it worth it?)", thinking_section)
        self.assertIn("opportunity cost", thinking_section)
        self.assertIn(
            "it may reject a candidate every other seat finds beautiful and well-owned",
            thinking_section,
        )
        self.assertIn(
            "`worth` (值不值) is an independent objective, not the aesthetic lens repeated",
            thinking_section,
        )
        self.assertIn(
            "the panel is not homogenized into every seat re-deriving the same value verdict",
            thinking_section,
        )
        self.assertIn("must state whether it hits `CapabilityOverlap`", thinking_section)
        self.assertIn("a hit is an unclosed goal gap and must not enter `implement`", thinking_section)
        self.assertIn(
            "Every seat must first identify the problem essence or root cause implied by `GoalArtifact`",
            thinking_section,
        )
        self.assertIn(
            "A plan that only patches a surface symptom while leaving that root cause in place does not satisfy the thinking gate",
            thinking_section,
        )

    def test_sshx_cardinalities_relevance_and_pass_budget_are_protocol_policy(self) -> None:
        text = read(SKILL)
        self.assertEqual(
            (
                text.count("Protocol policy, not a mathematical consequence:"),
                "Carrier heterogeneity is this protocol's policy, not a theorem premise or consequence" in text,
                "Protocol policy, not mathematics, defines these two conjuncts" in text,
                "before the first pass after the initial review triplet, the caller records one owner-precommitted finite integer `pass_budget`" in text,
            ),
            (5, True, True, True),
        )

    def test_sshx_fixed_routing_units_and_reflection_actions(self) -> None:
        # Source-regression only: these are fixed routing anchors and action enums.
        text = read(SKILL)
        design_section = text[
            heading_index(text, "## Design Truth Table") : heading_index(text, "## Implementation Worker")
        ]
        review_section = text[
            heading_index(text, "## Review Truth Table") : heading_index(text, "## Fix Or Done")
        ]
        fix_section = text[heading_index(text, "## Fix Or Done") : heading_index(text, "## Boundaries")]
        design_section_lower = design_section.lower()

        conjunctive_trigger_present = (
            "all three conditions" in design_section_lower
            or all(anchor in design_section_lower for anchor in ["three conditions", "all hold", "simultaneously"])
        )
        self.assertTrue(
            all(
                anchor in design_section
                for anchor in [
                    "An `implement` exit also requires",
                    "unresolved harness overlap",
                    "authority gap",
                    "missing host/controller execution capability",
                    "repeat a capability already declared by the harness",
                    "goal gap to the maintainer",
                ]
            ),
            "missing Design Truth Table harness/authority implement route",
        )
        self.assertTrue(
            conjunctive_trigger_present
            and all(
                anchor in design_section
                for anchor in [
                    "`FocusedRound`",
                    "exclusive domain",
                    "falsifiable rather than a preference",
                    "has not answered that causal chain",
                    "Does this causal chain hold",
                    "how should the plan change?",
                ]
            ),
            "missing FocusedRound three-condition trigger or fixed question",
        )
        self.assertTrue(
            all(
                anchor in review_section
                for anchor in [
                    "fails `ThreatEligibility`",
                    "downgraded",
                    "only for threat-model ineligibility",
                    "never because a finding is inconvenient",
                ]
            ),
            "missing threat-ineligibility-only review downgrade guard",
        )
        self.assertTrue(
            all(
                anchor in review_section
                for anchor in [
                    "missing, ambiguous, or stale harness declaration",
                    "never a downgrade shield",
                    "pause routing",
                    "escalate to the maintainer",
                ]
            ),
            "missing unavailable-harness pause-and-escalate guard",
        )
        self.assertIn("`meta_judge` implement-exit gate", design_section)
        pre_fix_gate_start = fix_section.index("Before each fix or repeated review pass")
        pre_fix_gate_end = fix_section.index(". ", pre_fix_gate_start) + 1
        pre_fix_gate = fix_section[pre_fix_gate_start:pre_fix_gate_end]
        correction_gate_start = fix_section.index("After any explicit correction")
        correction_gate_end = fix_section.index("\n\n", correction_gate_start)
        correction_gate = fix_section[correction_gate_start:correction_gate_end]
        self.assertIn("Before each fix or repeated review pass", pre_fix_gate)
        self.assertIn("After any explicit correction", correction_gate)
        self.assertIn("`ThreatEligibility`", review_section)
        for contract_section in [design_section, pre_fix_gate, correction_gate]:
            for action in ["`continue`", "`revise`", "`stop`", "`escalate`"]:
                self.assertIn(action, contract_section)
            self.assertIn("responsible party", contract_section)

    def test_sshx_focused_round_has_one_round_per_causal_chain(self) -> None:
        text = read(SKILL)
        design = section(text, "## Design Truth Table", "## Implementation Worker")
        self.assertNotIn(DEMONSTRATED_FOCUSED_ROUND_REOPENING_EXCEPTION, text)
        self.assertEqual(
            (
                all(
                    anchor in design
                    for anchor in [
                        "causal chain triggers at most one focused round",
                        "disagreement on that chain remains afterward, escalate to the maintainer",
                        "independently changed sealed inputs",
                        "never conclusions generated by the completed round itself",
                        "create a different grounded obligation",
                        "Replaying the same causal chain cannot reopen",
                        "records every grounded conflict and its resolution",
                        "presentation format is non-normative",
                        "only an unresolved grounded conflict blocks `implement`",
                    ]
                ),
                focused_round_route(
                    completed_rounds_for_chain=0,
                    independently_changed_inputs=False,
                    different_grounded_obligation=False,
                ),
                focused_round_route(
                    completed_rounds_for_chain=1,
                    independently_changed_inputs=False,
                    different_grounded_obligation=True,
                ),
                focused_round_route(
                    completed_rounds_for_chain=1,
                    independently_changed_inputs=True,
                    different_grounded_obligation=True,
                ),
            ),
            (True, "run-focused-round", "escalate-to-maintainer", "different-causal-chain"),
        )

    def test_sshx_repeated_pass_evidence_is_fix_or_done_owned(self) -> None:
        text = read(SKILL)
        fix_or_done = section(text, "## Fix Or Done", "## Termination Gate")
        outside_fix_or_done = text.replace(fix_or_done, "", 1)
        for anchor in [
            "across repeated passes on the same blocking goal gap",
            "distinguish evidence that the gap is reachable by the current approach from evidence that it is not",
            "Consecutive passes without improvement are, alone, evidence of neither",
            "do not prove the current approach is exhausted",
            "do not license further identical passes as progress",
            "Evidenced unreachability by the current approach routes through the gate's existing `revise`, `stop`, or `escalate` actions",
            "rather than respending `pass_budget` on an unchanged approach",
        ]:
            self.assertIn(anchor, fix_or_done)
            self.assertNotIn(anchor, outside_fix_or_done)

    def test_sshx_review_triplet_runs_all_three_perspectives(self) -> None:
        # Source-regression only: this checks triplet routing text, not reviewer quality or host execution.
        text = read(SKILL)
        triplet = text[text.index("## Review Triplet") : text.index("## Review Truth Table")]
        role_lines = re.findall(r"^- `(architecture|quality|tests)`: .+$", triplet, flags=re.MULTILINE)
        self.assertEqual(role_lines, ["architecture", "quality", "tests"])
        self.assertIn("no worker may see a same-round peer output", text)

    def test_sshx_stable_core_remains_present(self) -> None:
        # Source-regression only: presence of stable anchors is not end-to-end protocol validation.
        text = read(SKILL)
        for anchor in [
            "`intake`",
            "`choose_worker_mode`",
            "`thinking_panel_workers`",
            "`meta_judge`",
            "`implementation_worker`",
            "`review_triplet_workers`",
            "`fix_or_done`",
            "`WorkerDelegationContract`",
            "`SshxResultEnvelope`",
            "`FocusedRound`",
        ]:
            self.assertIn(anchor, text)
        for heading in [
            "## Worker Completion Contract",
            "## No Context Pollution",
            "## Reasoning Discipline",
            "## Thinking Panel",
            "## Review Triplet",
            "## Design Truth Table",
            "## Review Truth Table",
            "## Termination Gate",
            "## Termination Truth Table",
        ]:
            heading_index(text, heading)

    def test_sshx_worker_modes(self) -> None:
        text = read(SKILL)
        contract_text = without_refactor_comments(text)
        mode_lines = re.findall(r"^\d+\. `(codex-cli|nyxid-oracle|isolated-token-subagent|abstain)`$", text, re.MULTILINE)
        self.assertEqual(mode_lines, ["codex-cli", "nyxid-oracle", "isolated-token-subagent", "abstain"])
        self.assertIn("`WorkerDelegationContract`", text)
        self.assertIn(
            "`abstain` is required when none of `codex-cli`, `nyxid-oracle`, or `isolated-token-subagent` is available",
            text,
        )
        self.assertIn("Do not self-apply the triplet inside the caller context", text)
        self.assertIn("fallible advisory worker exactly like `codex-cli`, with no authority of any kind", text)
        self.assertIn(
            "everywhere in this contract, non-mutating means it changes no file, Git state, GitHub state, label, release, host configuration, lifecycle state, or other external resource",
            text,
        )
        self.assertIn("Its capability check and dispatch are non-mutating", text)
        self.assertIn("Carrier heterogeneity is this protocol's policy, not a theorem premise or consequence", text)
        self.assertIn("improves consensus quality or yields statistically independent priors is `ASSUMED-UNVERIFIED`", text)
        self.assertIn("carrier-role pairing must be chosen and recorded before any worker", text)
        self.assertIn("three-seat `## Termination Gate` follows that same composition", text)
        self.assertIn(SEAT_ROTATION_SPAN_TAIL, text)
        self.assertEqual(text.count(SEAT_ROTATION_SPAN_TAIL), 1)
        self.assertIn("`tests` review seat must be assigned to a carrier capable of executing", text)
        self.assertIn(
            "repository verification commands in the `work_target`, which is the per-seat constraint that keeps `nyxid-oracle` out of that seat's feasible draws",
            text,
        )
        self.assertIn(
            "A stage may be presented as model-diverse only when every initially paired seat reached terminal completion on its initial carrier with no fallback, unavailability, or exhausted retry, and at least two distinct model families are recorded evidence for those completions; otherwise record that the stronger diversity claim was not achieved.",
            text,
        )
        self.assertIn("must not be rebalanced in response to completion outcomes", text)
        self.assertIn("a retry or fallback may replace only the failed flight for the same seat and role", text)
        self.assertIn("neither is a mechanism for redrawing or restoring a stage's recorded assignment", text)
        self.assertIn("This is the dispatch-time rotation rule", text)
        self.assertNotIn("sealed-transcript", contract_text.lower())
        self.assertNotIn("actor-isolated", contract_text.lower())

    def test_sshx_default_seat_allocation_positive_anchors_with_mutations(self) -> None:
        text = read(SKILL)
        start, end = default_seat_allocation_span(text)
        allocation = text[start:end]
        self.assertEqual(text.count(allocation), 1)
        with self.assertRaises(AssertionError):
            default_seat_allocation_span(text.replace(allocation, "", 1))
        for proposition, pattern in DEFAULT_SEAT_ALLOCATION_PATTERNS:
            self.assertEqual(len(re.findall(pattern, allocation, flags=re.IGNORECASE)), 1)
            weakened = re.sub(pattern, "allocation omitted", allocation, count=1, flags=re.IGNORECASE)
            with self.assertRaisesRegex(
                AssertionError,
                re.escape(f"default seat allocation is missing: {proposition}"),
            ):
                default_seat_allocation_span(text[:start] + weakened + text[end:])
        with self.assertRaisesRegex(AssertionError, r"blocked weakening token: unless"):
            default_seat_allocation_span(
                text[:start] + f"{allocation} Unless the caller prefers otherwise." + text[end:]
            )
        composition_rewrites = (
            "At dispatch time, every multi-seat stage assigns exactly one seat to `isolated-token-subagent` and exactly one seat to `nyxid-oracle`. Every remaining seat in that stage goes to `codex-cli`, and every single-worker stage assigns its worker to `codex-cli`.",
            "At dispatch time, every multi-seat stage assigns exactly one seat to `isolated-token-subagent`, exactly one seat to `nyxid-oracle`, and every remaining seat to `codex-cli`. Every single-worker stage assigns its worker to `codex-cli`.",
            "At dispatch time:\n- Every multi-seat stage assigns exactly one seat to `isolated-token-subagent` and exactly one seat to `nyxid-oracle`.\n- Every remaining seat in that stage goes to `codex-cli`.\n- Every single-worker stage assigns its worker to `codex-cli`.",
            "At dispatch time, each multi-seat stage assigns exactly one seat to `isolated-token-subagent`, exactly one seat to `nyxid-oracle`, and each remaining seat to `codex-cli`; each single-worker stage assigns its worker to `codex-cli`.",
            "At dispatch time, every multi-seat stage assigns exactly one seat to `nyxid-oracle`, exactly one seat to `isolated-token-subagent`, and every remaining seat to `codex-cli`; every single-worker stage assigns its worker to `codex-cli`.",
        )
        rewrites = tuple(f"{rewrite} {SEAT_ROTATION_SPAN_TAIL}" for rewrite in composition_rewrites)
        for rewrite in rewrites:
            mutated = text[:start] + rewrite + text[end:]
            rewritten_start, rewritten_end = default_seat_allocation_span(mutated)
            self.assertEqual(mutated[rewritten_start:rewritten_end], rewrite)
            self.assertFalse(has_cardinal_carrier_binding_outside_default(mutated), f"rewrite triggered quantity heuristic: {rewrite}")

        role_swapped = allocation.replace(
            "exactly one seat to `isolated-token-subagent`",
            "exactly one seat to `codex-cli`",
            1,
        ).replace(
            "every remaining seat to `codex-cli`",
            "every remaining seat to `isolated-token-subagent`",
            1,
        )
        with self.assertRaises(AssertionError):
            default_seat_allocation_span(text[:start] + role_swapped + text[end:])

        insertion_anchor = "\n## Worker Delegation\n\n"
        self.assertEqual(text.count(insertion_anchor), 1)
        for noun in ("workers", "slots", "seats"):
            for carrier in CARRIER_NAMES:
                hardcoded = f"The Thinking Panel uses four `{carrier}` {noun}."
                mutated = text.replace(insertion_anchor, f"{insertion_anchor}{hardcoded}\n\n", 1)
                self.assertTrue(has_cardinal_carrier_binding_outside_default(mutated), f"hardcoded binding was not detected: {hardcoded}")

        legal_prose = (
            "The three carrier modes include `codex-cli`, whose capability is checked before dispatch.",
            "Attempt 2 for `codex-cli` consumes the same predeclared retry budget.",
        )
        for prose in legal_prose:
            mutated = text.replace(insertion_anchor, f"{insertion_anchor}{prose}\n\n", 1)
            self.assertFalse(has_cardinal_carrier_binding_outside_default(mutated), f"quantity heuristic matched legal prose: {prose}")

    def test_sshx_seat_rotation_is_drawn_once_and_recorded(self) -> None:
        text = read(SKILL)
        start, end = default_seat_allocation_span(text)
        allocation = text[start:end]
        self.assertIn(SEAT_ROTATION_SPAN_TAIL, allocation)

        transcript = section(text, "## Transcript Template", "## Verification")
        self.assertIn("  seat_rotation: # per multi-seat stage: the drawn seat-to-carrier assignment\n", transcript)
        self.assertLess(transcript.index("seat_rotation:"), transcript.index("worker_flights:"))

        weakenings = (
            (
                "uniform random seat draw",
                "draws one assignment uniformly at random",
                "picks one assignment it prefers",
            ),
            ("redraw forbidden", "redrawing it is forbidden", "redrawing it is allowed"),
            (
                "rotation across repeated passes",
                "next draw must differ from that stage's previously recorded assignment",
                "next draw may repeat that stage's previously recorded assignment",
            ),
            ("mechanical randomness source", "mechanical randomness source", "caller judgment"),
            (
                "draw recorded before first launch",
                "recorded in `worker_delegation.seat_rotation` before the first worker of that stage is launched",
                "recorded once the stage has returned",
            ),
        )
        for proposition, original, weakened in weakenings:
            self.assertIn(original, allocation)
            mutated = text[:start] + allocation.replace(original, weakened, 1) + text[end:]
            with self.assertRaisesRegex(
                AssertionError, re.escape(f"default seat allocation is missing: {proposition}")
            ):
                default_seat_allocation_span(mutated)

    def test_sshx_worker_dispatch_sections_have_no_carrier_owners_with_mutations(self) -> None:
        text = read(SKILL)
        self.assertTrue(
            worker_dispatch_sections_are_carrier_free(text),
            "canonical worker-dispatch sections unexpectedly name a carrier",
        )
        for heading in ("## Thinking Panel", "## Review Triplet", "## Termination Gate"):
            insertion = heading_index(text, heading) + len(heading)
            for carrier in CARRIER_NAMES:
                mutated = f"{text[:insertion]}\n\nThe {carrier} carrier owns this stage.{text[insertion:]}"
                self.assertFalse(
                    worker_dispatch_sections_are_carrier_free(mutated),
                    f"carrier owner was not detected: {carrier} in {heading}",
                )

    def test_sshx_worker_mode_gate_blocks_delegated_dispatch_before_mode_resolution(self) -> None:
        text = read(SKILL)
        self.assertIn("`WorkerModeGate` requires resolution before dispatch", text)
        self.assertIn(
            "During `intake`, the caller may use its own read-only tools to inspect the user's input and write `GoalArtifact`; this caller-owned read-only intake is not worker dispatch",
            text,
        )
        self.assertIn(
            "Before any worker dispatch, including delegated intake context-gathering by subagent, Agent, Task, or codex, the caller must complete the non-mutating `codex-cli` capability check and resolve `WorkerMode`",
            text,
        )
        self.assertIn("worker_mode_gate:", text)
        self.assertIn("resolved_before_any_worker_dispatch:", text)
        self.assertIn("  reason:", text)
        self.assertLess(
            text.index("Before any worker dispatch"),
            text.index("Thinking, implementation, review, and termination-gate work are worker dispatches"),
        )
        self.assertLess(
            text.index("`WorkerModeGate`"),
            text.index("Implementation must be delegated to a worker using the stage's default carrier"),
        )

    def test_sshx_isolated_subagent_completion_rule(self) -> None:
        completion = section(read(SKILL), "## Worker Completion Contract", "## No Context Pollution")
        self.assertIn("using `n/a` only when the carrier has no independent sentinel", completion)
        self.assertIn("otherwise returns `abstain`", completion)

    def test_sshx_nyxid_oracle_completion_rule(self) -> None:
        text = read(SKILL)
        worker_delegation = section(text, "## Worker Delegation", "## Result Envelope")
        completion_contract = section(text, "## Worker Completion Contract", "## No Context Pollution")
        self.assertIn(
            "must start a new isolated oracle conversation before that attempt's first submission",
            worker_delegation,
        )
        self.assertNotIn(
            "must start a new isolated oracle conversation for that flight or attempt",
            worker_delegation.lower(),
        )
        self.assertIn(
            "Completion and verdict recognition use only `## Worker Completion Contract`",
            worker_delegation,
        )
        self.assertIn("For every worker carrier, completion and verdict routing use one fail-closed predicate", completion_contract)
        self.assertNotIn("For `nyxid-oracle` workers", completion_contract)

    def test_sshx_nyxid_oracle_recovery_trigger_and_bounds(self) -> None:
        text = read(SKILL)
        completion_contract = section(text, "## Worker Completion Contract", "## No Context Pollution")
        self.assertNotIn("finite recovery read sequence", completion_contract.lower())
        self.assertIn("declared finite retry and fallback path", completion_contract)
        self.assertIn("highest-priority eligible untried carrier", section(text, "## Worker Delegation", "## Result Envelope"))

    def test_sshx_nyxid_oracle_recovery_read_semantics(self) -> None:
        completion_contract = section(read(SKILL), "## Worker Completion Contract", "## No Context Pollution")
        for required in [
            "carrier has successfully exited or returned terminally",
            "matching flight records a valid `SshxResultEnvelope`",
            "required `conclusion.verdict` is in that stage's allowed set",
            "required completion evidence is recorded in `completion_sentinel_ref`",
            "do not create a carrier-specific meaning of completion",
        ]:
            self.assertIn(required, completion_contract)

    def test_sshx_nyxid_oracle_blind_redispatch_is_idempotent_and_bounded(self) -> None:
        text = read(SKILL)
        completion = section(text, "## Worker Completion Contract", "## No Context Pollution")
        self.assertNotIn("blindly redispatching", text.lower())
        self.assertNotIn("dispatch-exit recovery", text.lower())
        self.assertNotIn("same-task recovery", text.lower())
        self.assertIn("one fail-closed predicate", completion)

    def test_sshx_nyxid_oracle_public_github_reference_rule(self) -> None:
        skill = read(SKILL)
        worker_delegation = section(skill, "## Worker Delegation", "## Result Envelope")
        reference_rule = next(
            paragraph
            for paragraph in worker_delegation.split("\n\n")
            if "public GitHub URL" in paragraph
        )
        for required in [
            "A `nyxid-oracle` worker has no access to the caller's filesystem",
            "caller-local paths, including `work_target` paths, are not readable content references for it",
            "may instead reference repository content by public GitHub URL",
            "pinned to an immutable commit SHA so every seat reads the same bytes",
            "branch, tag, and `HEAD` URLs drift between reads and must not be used",
            "permitted only when the referenced content is already anonymously readable on the remote",
            "which the caller confirms before the first submission",
            "must never push, publish, change repository visibility, or otherwise mutate remote state to make content linkable",
            "When the needed content is not already public, the brief inlines it instead",
            "never a goal source under `## Goal Contract`",
            "never a pointer to same-round peer output or another seat's artifacts",
            "worker-reported data rather than caller-verified evidence",
            "If the oracle cannot retrieve a referenced URL",
            "mark every premise that depended on it `ASSUMED-UNVERIFIED`",
            "never reconstructing the content from memory",
        ]:
            self.assertIn(required, reference_rule)
        self.assertIn("- GitHub lifecycle operations;", section(skill, "## Boundaries", "## Baseline Failure Mode"))

    def test_sshx_external_carrier_claims_require_real_verification(self) -> None:
        text = read(SKILL)
        self.assertIn("verify the exact composed workflow end to end with the real tool", text)
        self.assertIn("Fake carriers may supplement deterministic contract tests but must not be the sole evidence for a supported capability", text)
        self.assertIn("when real verification is unavailable, mark the claim ASSUMED-UNVERIFIED and do not expose it as a supported option", text)

    def test_sshx_abstain_is_terminal_transition(self) -> None:
        text = read(SKILL)
        self.assertIn("When `WorkerMode` resolves to `abstain`, the protocol terminates at `choose_worker_mode`", text)
        self.assertIn("creates no thinking, implementation, or review flight", text)
        self.assertIn(
            "When a thinking, implementation, review, or termination flight instead exhausts its bounded retries and fallback without terminal completion",
            text,
        )
        self.assertIn(
            "A thinking-stage exhaustion in particular skips `meta_judge`, `implementation_worker`, `review_triplet_workers`, and `fix_or_done`",
            text,
        )

    def test_sshx_worker_flight_record_contract(self) -> None:
        text = read(SKILL)
        self.assertIn("`SshxWorkerFlightRecord`", text)
        self.assertIn("Every worker dispatch must create a prompt-level `SshxWorkerFlightRecord`", text)
        self.assertIn("worker result record must reference the matching `flight_id` through `worker_flight_ref`", text)
        for field in [
            "`flight_id`",
            "`stage`",
            "`role`",
            "`worker_mode`",
            "`worker_carrier`",
            "`work_target`",
            "`status`",
            "`retry_budget`",
            "`attempt`",
            "`result_envelope_ref`",
            "`completion_sentinel_ref`",
        ]:
            self.assertIn(field, text)
        self.assertIn("`status` is one of `in-flight`, `retrying`, `terminal`, or `abstained`", text)
        self.assertIn("`retry_budget` is a finite integer decided before the first launch", text)

    def test_sshx_worker_invocation_collection_contract(self) -> None:
        text = read(SKILL)
        wd_start = text.index("## Worker Delegation")
        wd_end = text.index("## Result Envelope")
        worker_delegation = text[wd_start:wd_end]
        for contract_string in [
            "the caller must choose a unique `flight_id` and `attempt`",
            "pass them to `skills/sshx/scripts/run-codex-worker.sh`",
            "the runner derives and owns every artifact path",
            "the caller must not supply arbitrary result, sentinel, log, or state paths",
            "Every formal `codex-cli` flight must use this runner",
            "owned by `CODEX_WORKER_SPEC.md`",
            "the required dispatch shape is the runner's default `danger-full-access` sandbox",
            "the caller passes no sandbox selection unless the maintainer explicitly directs a narrower one",
            "The caller must not poll worker artifact paths while the runner is active",
            "The caller records `result_envelope_ref` and `completion_sentinel_ref` on the matching flight only if the runner reports completion and the envelope and sentinel validate.",
            "Completion and verdict recognition stay governed by the `## Worker Completion Contract`",
        ]:
            self.assertIn(contract_string, worker_delegation)
        for deleted_mechanic in [
            "The caller invokes the runner, and the runner launches exactly one direct non-interactive worker carrier; neither layer may introduce a daemon or wrap the carrier in a repository-owned CLI.",
            "The runner does not propagate signals or manage carrier PIDs.",
            "After the carrier exits, the runner performs one collection read of the derived `result_ref` and `completion_sentinel`",
        ]:
            self.assertNotIn(deleted_mechanic, worker_delegation)
        self.assertLess(
            text.index("The caller records `result_envelope_ref` and `completion_sentinel_ref`"),
            text.index("## Worker Completion Contract"),
        )

    def test_sshx_carrier_failure_follows_one_retry_path(self) -> None:
        text = read(SKILL)
        completion = section(text, "## Worker Completion Contract", "## No Context Pollution")
        for anchor in [
            "The caller does not decide which failure occurred before retrying",
            "every outcome short of terminal completion follows this one path",
            "runner diagnostics stay behind the flight record as data, never as a routing input",
        ]:
            self.assertIn(anchor, completion)
        for stale in ["packaging-only", "envelope_invalid", "verdict_invalid", "immediately preceding attempt"]:
            self.assertNotIn(stale, text.lower())
        self.assertIn("`ENVELOPE_INVALID`", read(SPEC))

    def test_sshx_result_envelope_types_are_explicit(self) -> None:
        result_envelope = section(read(SKILL), "## Result Envelope", "## Worker Completion Contract")
        self.assertIn("`conclusion` is a structured JSON object, not a free-text string", result_envelope)
        self.assertIn("`log_ref` is a non-empty string reference", result_envelope)
        self.assertIn("it is the string at `conclusion.verdict`", result_envelope)

    def test_sshx_codex_cli_background_job_dispatch_source_regression(self) -> None:
        text = read(SKILL)
        worker_delegation = text[
            heading_index(text, "## Worker Delegation") : heading_index(text, "## Result Envelope")
        ]
        paragraphs = [re.sub(r"\s+", " ", paragraph).lower() for paragraph in worker_delegation.split("\n\n")]
        dispatch = next((paragraph for paragraph in paragraphs if "background" in paragraph and "notif" in paragraph), "")
        self.assertTrue(dispatch, "missing background execution plus completion notification rule")
        sentences = re.split(r"(?<=[.!?])\s+", dispatch)
        shell_rule = next((sentence for sentence in sentences if "shell" in sentence and ("background" in sentence or "ampersand" in sentence or "`&`" in sentence)), "")
        self.assertRegex(shell_rule, r"(?:must not|forbid\w*|prohibit\w*|never)")
        self.assertRegex(shell_rule, r"(?:detach\w*|disconnect\w*).{0,100}host(?: lifecycle)? tracking")
        polling_rule = next((sentence for sentence in sentences if re.search(r"\bfiles?\b", sentence) and re.search(r"\blogs?\b", sentence) and "poll" in sentence), "")
        self.assertRegex(polling_rule, r"(?:must not|forbid\w*|prohibit\w*|never)")

    def test_sshx_batch_signal_semantics_are_spec_owned(self) -> None:
        text = read(SKILL)
        worker_delegation = section(text, "## Worker Delegation", "## Result Envelope")
        for required in [
            "records every child, and joins every recorded child before publishing a report",
            "its signal handling, interruption reporting, and inherited-disposition limits are owned by `CODEX_WORKER_SPEC.md` and the script's behavior tests",
            "whole-job-tree teardown remains the host's responsibility",
            "Caller-authored `&`, `nohup`, `disown`, and `setsid` remain forbidden",
        ]:
            self.assertIn(required, worker_delegation)
        for duplicated in ["catches `INT` and `TERM`", "`SIGINT` ignored", "repeats a wait only when"]:
            self.assertNotIn(duplicated, text)
        spec = read(SPEC)
        self.assertIn("join-then-publish interruption handling", spec)
        self.assertIn("`INT` and `TERM` traps record only the first signal", spec)

    def test_worker_tool_contract_keeps_presence_fact_without_lifecycle_inference(self) -> None:
        specification = read(SPEC)
        for required in [
            "The present/absent file fact is required",
            "derived completion or lifecycle boolean",
            "lifecycle inference",
            "verdict checking",
            "`reason_code` remapping",
            "semantic JSON equality",
            "byte-for-byte whitespace and object-key formatting are not preserved",
        ]:
            self.assertIn(required, specification)

    def test_worker_tool_spec_states_preflight_and_cleanup_limits(self) -> None:
        specification = re.sub(r"\s+", " ", read(SPEC))
        for required in [
            "exclusively creates one unique same-directory report temporary file before launching any worker",
            "report target must be absent",
            "An internal pre-launch failure",
            "no worker launches and no complete JSON report exists",
            "the supported Bash must retain an exited child's status",
            "Preflight makes no all-or-nothing claim that survives such replacement",
            "Cleanup accepts the batch manifest document shape",
            "invalid manifest document are `USAGE_ERROR` (exit 64)",
            "This narrows the authorization window but does not eliminate TOCTOU",
            "`state` equal to `removed`, `partially-removed`, or `untouched`",
            "pure-Bash JSON fallback that does not invoke `jq`",
        ]:
            self.assertIn(required, specification)

    def test_sshx_in_flight_worker_blocks_caller_mutation(self) -> None:
        text = read(SKILL)
        self.assertIn("While any `SshxWorkerFlightRecord` for the same `work_target` is `in-flight` or `retrying`", text)
        self.assertIn("the caller is read-only for that target", text)
        self.assertIn("The caller is non-mutating for that target and its external resources.", text)
        self.assertIn("must not take over the same `work_target`", text)

        in_flight = {"work_target": "skills/sshx/SKILL.md", "status": "in-flight"}
        retrying = {"work_target": "skills/sshx/SKILL.md", "status": "retrying"}
        terminal = {"work_target": "skills/sshx/SKILL.md", "status": "terminal"}
        other_target = {"work_target": "README.md", "status": "in-flight"}

        self.assertTrue(flight_blocks_mutation(in_flight, "skills/sshx/SKILL.md"))
        self.assertTrue(flight_blocks_mutation(retrying, "skills/sshx/SKILL.md"))
        self.assertFalse(flight_blocks_mutation(terminal, "skills/sshx/SKILL.md"))
        self.assertFalse(flight_blocks_mutation(other_target, "skills/sshx/SKILL.md"))

    def test_sshx_completion_requires_envelope_and_worker_sentinel(self) -> None:
        text = read(SKILL)
        completion = section(text, "## Worker Completion Contract", "## No Context Pollution")
        self.assertIn("matching flight records a valid `SshxResultEnvelope`", completion)
        self.assertIn("required completion evidence is recorded in `completion_sentinel_ref`", completion)
        self.assertIn(
            "The predicate has exactly those inputs; no other observation is completion evidence, whatever text, artifact, log, projection, report, or process state it comes from, and `log_ref` remains required only as a diagnostic reference.",
            completion,
        )

        self.assertTrue(
            has_terminal_completion(
                {
                    "status": "terminal",
                    "result_envelope_ref": "artifacts/sshx/worker-result.json",
                    "completion_sentinel_ref": "artifacts/sshx/worker.done",
                }
            )
        )
        self.assertFalse(
            has_terminal_completion(
                {
                    "status": "terminal",
                    "result_envelope_ref": "artifacts/sshx/worker-result.json",
                    "completion_sentinel_ref": "",
                }
            )
        )
        self.assertFalse(
            has_terminal_completion(
                {
                    "status": "terminal",
                    "result_envelope_ref": "",
                    "completion_sentinel_ref": "artifacts/sshx/worker.done",
                }
            )
        )

    def test_sshx_exhausted_flight_fallback_is_origin_agnostic(self) -> None:
        text = read(SKILL)
        self.assertIn("If an initially paired carrier is unavailable before a flight can be opened", text)
        self.assertIn("without claiming that a same-carrier retry budget was exhausted", text)
        self.assertIn("If any flight lacks terminal completion after its finite same-carrier retry budget is exhausted", text)
        self.assertIn(
            "marks that flight `abstained` with empty `result_envelope_ref` and `completion_sentinel_ref`",
            text,
        )
        self.assertIn("highest-priority eligible untried carrier from the full `WorkerMode` list", text)
        self.assertIn("rather than continuing strictly downward from the failed carrier", text)
        self.assertIn("must satisfy this stage and role's carrier constraints", text)
        self.assertIn("creates a new `SshxWorkerFlightRecord` for the same `stage`, `role`, and `work_target`", text)
        self.assertIn("until the fallback flight reaches `terminal` or `abstained`", text)
        self.assertIn("Only when no eligible untried carrier remains or every fallback fails", text)
        self.assertIn("is the result `abstain`", text)
        self.assertIn("must not implement, repair, or otherwise mutate the same `work_target` itself", text)
        self.assertIn("`worker_delegation.reason` and the gate record state", text)

        flight = {"status": "retrying", "retry_budget": 2, "attempt": 1}
        self.assertEqual(resolve_failed_flight(flight, fallback_available=False), "retry-same-carrier")
        for worker_mode in ("codex-cli", "nyxid-oracle", "isolated-token-subagent"):
            exhausted = {**flight, "worker_mode": worker_mode, "attempt": 2}
            self.assertEqual(
                resolve_failed_flight(exhausted, fallback_available=True),
                "fallback-highest-priority-untried-carrier",
            )
            self.assertEqual(resolve_failed_flight(exhausted, fallback_available=False), "abstain")

    def test_sshx_no_context_pollution_contract(self) -> None:
        text = read(SKILL)
        self.assertIn("The caller context must not carry worker full reasoning or same-round peer outputs", text)
        for allowed_context in [
            "intake inputs and constraints",
            "dispatch briefs sent to each worker",
            "`SshxResultEnvelope.conclusion` values, including verdicts and explicitly surfaced blockers",
            "`SshxResultEnvelope.log_ref` artifact references",
            "final reports that aggregate conclusions only",
        ]:
            self.assertIn(allowed_context, text)
        for removed_escape_hatch in [
            "worker summaries, verdicts, and explicitly surfaced blockers",
            "meta-judge synthesis",
            "final summary and report",
        ]:
            self.assertNotIn(removed_escape_hatch, text.lower())
        self.assertIn("Input isolation and prior sterility are separate dimensions", text)
        self.assertIn("no worker may see a same-round peer output or caller-conversation transcript content", text)
        self.assertIn("none of the allowed carriers provides it", text)
        self.assertIn("unknown and uncontrollable account memory and project context", text)
        self.assertIn("none may be described as context-sterile or cited as evidence that their priors are independent", text)
        self.assertIn("permanently sterile-context-unverified", text)
        for label in ("repo-prior-exposed", "external-prior-exposed", "caller-prior-exposed"):
            self.assertIn(label, text)
        self.assertIn("existing `visible_inputs` value", text)

    def test_sshx_result_envelope_contract(self) -> None:
        text = read(SKILL)
        heading_index(text, "## Result Envelope")
        self.assertIn("All records, contracts, gates, templates, and reasoning guidance named here are prompt-level only", text)
        self.assertIn(
            "Every `SshxResultEnvelope` returned by `thinking_panel_workers`, `meta_judge`, `implementation_worker`, `review_triplet_workers`, and `fix_or_done` uses exactly these top-level fields",
            text,
        )
        self.assertIn("A caller-carried stage record wraps this envelope", text)
        self.assertIn("The envelope payload itself stays exactly `conclusion` and `log_ref`", text)
        self.assertIn("is a read-only mirror of its envelope's `conclusion.verdict`", text)
        self.assertIn(
            "`conclusion.verdict` is the sole verdict source for routing, the two must be equal, and any mismatch fails closed",
            text,
        )
        self.assertIn("`conclusion`: compact structured result consumed by the caller", text)
        self.assertIn("`log_ref`: artifact reference for the non-inline worker", text)
        self.assertIn("treated as an opaque diagnostic pointer", text)
        self.assertIn("must not open, inline, summarize, or otherwise consume its content", text)
        self.assertIn("out-of-band debugging outside the consensus decision context", text)
        self.assertNotIn("The caller may open the referenced artifact on demand", text)
        for forbidden_inline in [
            "process logs",
            "step-by-step reasoning",
            "raw transcripts",
            "debug output",
            "same-round peer output",
        ]:
            self.assertIn(forbidden_inline, text)
        self.assertIn("Logs are not inline in caller context", text)
        self.assertIn("Final reports aggregate `conclusion` values only", text)
        self.assertIn("produce the final report from conclusions only while preserving `log_ref` references", text)
        self.assertIn("process logs stay behind `log_ref`", text)
        self.assertIn("without inlining logs", text)

    def test_sshx_worker_completion_contract(self) -> None:
        text = read(SKILL)
        heading_index(text, "## Worker Completion Contract")
        completion = section(text, "## Worker Completion Contract", "## No Context Pollution")
        self.assertIn("one fail-closed predicate", completion)
        self.assertIn("carrier has successfully exited or returned terminally", completion)
        self.assertIn("matching flight records a valid `SshxResultEnvelope`", completion)
        self.assertIn("required `conclusion.verdict` is in that stage's allowed set", completion)
        self.assertIn("required completion evidence is recorded in `completion_sentinel_ref`", completion)
        self.assertIn(
            "The predicate has exactly those inputs; no other observation is completion evidence, whatever text, artifact, log, projection, report, or process state it comes from, and `log_ref` remains required only as a diagnostic reference.",
            completion,
        )
        self.assertIn("missing or invalid terminal observation, envelope, required verdict, or completion reference fails closed", completion)
        self.assertIn("otherwise returns `abstain`", completion)
        self.assertNotIn("For `codex-cli` workers, caller-side completion", completion)
        self.assertFalse(has_permissive_snapshot_completion_exception(completion))

    def test_sshx_completion_model_validates_verdict_before_sentinel(self) -> None:
        invalid_artifact = {
            "conclusion": {"verdict": "invalid"},
            "log_ref": "artifacts/sshx/worker.log",
        }
        with self.assertRaisesRegex(ContractFailure, "invalid conclusion.verdict"):
            completed_worker_verdict(
                process_exited=True,
                exit_code=0,
                result_artifact=invalid_artifact,
                completion_sentinel_present=False,
                allowed_verdicts=THINKING_VERDICTS,
            )

    def test_worker_spec_references_skill_without_exact_verdict_sets(self) -> None:
        text = re.sub(r"\s+", " ", read(SPEC))
        self.assertIn("`SKILL.md` is the sole source of the exact stage verdict sets", text)
        verdict_tokens = {"propose", "revise", "reject", "abstain", "approve", "comment"}
        self.assertEqual(verdict_tokens.intersection(re.findall(r"\b[a-z]+\b", text)), set())

    def test_worker_spec_source_regression_has_executable_rollback_and_threat_boundaries(self) -> None:
        text = re.sub(r"\s+", " ", read(SPEC))
        for required in [
            "No other skill may depend on these mechanisms",
            "Delete `scripts/run-codex-worker.sh`, `scripts/run-codex-worker-batch.sh`, `scripts/read-codex-worker-status.sh`, and `scripts/clean-codex-worker-runs.sh`",
            "Delete this specification, `tests/test_run_codex_worker.py`, and `tests/test_codex_worker_tools.py`",
            "Restore the narrow `SKILL.md` clauses",
            "A new design review is required",
            "daemon behavior",
            "lifecycle authority",
            "a second consuming skill",
            "completion semantics cease to be isomorphic",
            "trusted is non-adversarial, not infallible",
            "TOCTOU races",
            "an active `setsid` escape",
            "forged runner artifact paths",
            "A default `TERM` sent only to the runner PID may be deferred",
            "An uncatchable `SIGKILL` sent only to the runner PID",
            "Before either runner-owned projection is written",
            "temporary and final target must be absent or a non-symbolic-link regular file",
            "After each rename, the fixed `carrier.exit` or `status.json` path must be a non-symbolic-link regular file",
        ]:
            self.assertIn(required, text)

    def test_sshx_runner_ownership_wording_has_no_legacy_assignment(self) -> None:
        text = read(SKILL)
        self.assertIn("the runner derives and owns every artifact path", text)
        self.assertNotIn("caller-assigned `result_ref`", text.lower())
        self.assertNotIn("caller-assigned `completion_sentinel`", text.lower())

    def test_sshx_worker_completion_ignores_log_only_fake_markers(self) -> None:
        log_only_fake_marker = {
            "stdout": "IMPL_DONE VERDICT:propose",
            "stderr": "VERDICT:approve",
            "raw_transcript": "final answer says done",
            "log_tail": "REVIEW_DONE:approve",
        }
        with self.assertRaisesRegex(ContractFailure, "missing result_ref artifact"):
            completed_worker_verdict(
                process_exited=True,
                exit_code=0,
                result_artifact=None,
                completion_sentinel_present=True,
                allowed_verdicts=THINKING_VERDICTS,
            )
        self.assertIn("VERDICT:propose", repr(log_only_fake_marker))

    def test_sshx_running_process_is_not_done_even_with_valid_artifact(self) -> None:
        valid_artifact = {
            "conclusion": {"verdict": "propose", "decision": "use artifact authority"},
            "log_ref": "artifacts/sshx/minimal.log",
        }
        with self.assertRaisesRegex(ContractFailure, "still running"):
            completed_worker_verdict(
                process_exited=False,
                exit_code=None,
                result_artifact=valid_artifact,
                completion_sentinel_present=True,
                allowed_verdicts=THINKING_VERDICTS,
            )

    def test_sshx_exit_zero_and_valid_artifact_is_done(self) -> None:
        valid_artifact = {
            "conclusion": {"verdict": "propose", "decision": "use artifact authority"},
            "log_ref": "artifacts/sshx/minimal.log",
        }
        verdict = completed_worker_verdict(
            process_exited=True,
            exit_code=0,
            result_artifact=valid_artifact,
            completion_sentinel_present=True,
            allowed_verdicts=THINKING_VERDICTS,
        )
        self.assertEqual(verdict, "propose")

    def test_sshx_verdict_only_comes_from_conclusion_verdict(self) -> None:
        valid_artifact = {
            "conclusion": {"verdict": "reject", "decision": "artifact wins"},
            "log_ref": "artifacts/sshx/worker.log#VERDICT:propose",
        }
        verdict = completed_worker_verdict(
            process_exited=True,
            exit_code=0,
            result_artifact=valid_artifact,
            completion_sentinel_present=True,
            allowed_verdicts=THINKING_VERDICTS,
        )
        self.assertEqual(verdict, "reject")

    def test_sshx_invalid_result_envelopes_fail_closed(self) -> None:
        invalid_artifacts = [
            ({"log_ref": "artifacts/sshx/worker.log"}, "SshxResultEnvelope"),
            ({"conclusion": {"verdict": "propose"}}, "SshxResultEnvelope"),
            ({"conclusion": {}, "log_ref": "artifacts/sshx/worker.log"}, "invalid"),
            ({"conclusion": {"verdict": "TODO"}, "log_ref": "artifacts/sshx/worker.log"}, "invalid"),
            ({"conclusion": {"verdict": "maybe"}, "log_ref": "artifacts/sshx/worker.log"}, "invalid"),
            ({"conclusion": {"verdict": "propose"}, "log_ref": ""}, "missing log_ref"),
        ]
        for artifact, reason in invalid_artifacts:
            with self.subTest(artifact=artifact):
                with self.assertRaisesRegex(ContractFailure, reason):
                    completed_worker_verdict(
                        process_exited=True,
                        exit_code=0,
                        result_artifact=artifact,
                        completion_sentinel_present=True,
                        allowed_verdicts=THINKING_VERDICTS,
                    )

    def test_sshx_missing_completion_sentinel_fails_closed(self) -> None:
        valid_artifact = {
            "conclusion": {"verdict": "propose", "decision": "use artifact authority"},
            "log_ref": "artifacts/sshx/minimal.log",
        }
        with self.assertRaisesRegex(ContractFailure, "missing completion sentinel"):
            completed_worker_verdict(
                process_exited=True,
                exit_code=0,
                result_artifact=valid_artifact,
                completion_sentinel_present=False,
                allowed_verdicts=THINKING_VERDICTS,
            )

    def test_sshx_result_envelope_caller_context_excludes_inline_logs(self) -> None:
        caller_carried_transcript = {
            "thinking_panel_workers": [
                {
                    "role": "teleology",
                    "worker_flight_ref": "flight-thinking-teleology",
                    "verdict": "propose",
                    "conclusion": {
                        "decision": "the form is forced by the stated purpose",
                        "goal_gap": "none",
                    },
                    "log_ref": "artifacts/sshx/teleology.log",
                }
            ],
            "meta_judge": {
                "conclusion": {
                    "exit": "implement",
                    "decision": "all worker conclusions agree",
                },
                "log_ref": "artifacts/sshx/meta-judge.log",
            },
            "implementation_worker": {
                "worker_flight_ref": "flight-implementation",
                "conclusion": {
                    "changed_files": ["skills/sshx/SKILL.md"],
                    "tests": ["python3 -m unittest discover -s skills/sshx/tests -p 'test_*.py'"],
                },
                "log_ref": "artifacts/sshx/implementation.log",
            },
            "review_triplet_workers": [
                {
                    "role": "tests",
                    "worker_flight_ref": "flight-review-tests",
                    "verdict": "approve",
                    "conclusion": {"coverage": "contract locks envelope-only output"},
                    "log_ref": "artifacts/sshx/review-tests.log",
                }
            ],
            "fix_or_done": {
                "conclusion": {"exit": "done with advisory surfaced"},
                "log_ref": "artifacts/sshx/fix-or-done.log",
            },
        }

        def assert_envelope(node: dict[str, object]) -> None:
            self.assertIn("conclusion", node)
            self.assertIn("log_ref", node)
            self.assertNotIn("summary", node)
            self.assertNotIn("report", node)
            self.assertNotIn("synthesis", node)

        for worker in caller_carried_transcript["thinking_panel_workers"]:
            assert_envelope(worker)
        assert_envelope(caller_carried_transcript["meta_judge"])
        assert_envelope(caller_carried_transcript["implementation_worker"])
        for reviewer in caller_carried_transcript["review_triplet_workers"]:
            assert_envelope(reviewer)
        assert_envelope(caller_carried_transcript["fix_or_done"])

        rendered = repr(caller_carried_transcript)
        for inline_log_phrase in [
            "worker reasoning:",
            "raw transcript:",
            "debug log body:",
            "step-by-step reasoning:",
            "same-round peer output:",
        ]:
            self.assertNotIn(inline_log_phrase, rendered.lower())

    def test_sshx_transcript_worker_flights_shape(self) -> None:
        text = read(SKILL)
        delegation_start = text.index("worker_delegation:")
        flights_start = text.index("worker_flights:", delegation_start)
        delegation_block = text[delegation_start:flights_start]
        self.assertNotRegex(delegation_block, r"(?m)^  worker_(?:mode|carrier):$")
        self.assertIn("worker_flights: # ordered SshxWorkerFlightRecord entries", text)
        field_block = text.split("`SshxWorkerFlightRecord` has exactly these fields:\n\n", 1)[1].split("\n\n", 1)[0]
        self.assertEqual(
            [line.removeprefix("- `").removesuffix("`") for line in field_block.splitlines()],
            [
                "flight_id",
                "stage",
                "role",
                "worker_mode",
                "worker_carrier",
                "work_target",
                "status",
                "retry_budget",
                "attempt",
                "result_envelope_ref",
                "completion_sentinel_ref",
            ],
        )

        transcript = {
            "worker_flights": [
                {
                    "flight_id": "flight-implementation",
                    "stage": "implementation_worker",
                    "role": "implementation",
                    "worker_mode": "codex-cli",
                    "worker_carrier": "codex-cli",
                    "work_target": "skills/sshx/SKILL.md",
                    "status": "terminal",
                    "retry_budget": 2,
                    "attempt": 1,
                    "result_envelope_ref": "artifacts/sshx/implementation-result.json",
                    "completion_sentinel_ref": "artifacts/sshx/implementation.done",
                }
            ],
            "implementation_worker": {
                "worker_flight_ref": "flight-implementation",
                "conclusion": {"changed_files": ["skills/sshx/SKILL.md"]},
                "log_ref": "artifacts/sshx/implementation.log",
            },
        }
        self.assertEqual(
            set(transcript["worker_flights"][0]),
            {
                "flight_id",
                "stage",
                "role",
                "worker_mode",
                "worker_carrier",
                "work_target",
                "status",
                "retry_budget",
                "attempt",
                "result_envelope_ref",
                "completion_sentinel_ref",
            },
        )
        self.assertEqual(
            transcript["implementation_worker"]["worker_flight_ref"],
            transcript["worker_flights"][0]["flight_id"],
        )

    def test_sshx_review_transcript_entries_have_full_fields(self) -> None:
        text = read(SKILL)
        record_contract = section(text, "## InlineConsensusProtocol", "## Worker Delegation")
        template = section(text, "## Transcript Template", "## Verification")
        self.assertIn("review_triplet_workers: # protocol-policy three named stage records", template)
        for field in ["role", "bias", "visible_inputs", "worker_carrier", "worker_flight_ref", "verdict", "conclusion", "log_ref"]:
            self.assertIn(f"- `{field}`", record_contract)
        self.assertEqual(template.count("visible_inputs:"), 0)

    def test_sshx_termination_transcript_is_nested_and_complete(self) -> None:
        text = read(SKILL)
        fix_start = text.index("fix_or_done:")
        template_end = text.index("```", fix_start)
        fix_block = text[fix_start:template_end]
        self.assertIn("  termination_gate:", fix_block)
        self.assertIn("    continuation_declaration_ref:", fix_block)
        self.assertIn("    seats: # protocol-policy exactly three named termination stage records", fix_block)
        self.assertIn("    meta_judge: # termination routing record", fix_block)
        self.assertIn("  pass_budget:", fix_block)
        termination = section(text, "## Termination Gate", "## Termination Truth Table")
        self.assertEqual(
            tuple(re.findall(r"^- `(criterion-evidence|residual-gap|claim-integrity)`: .+$", termination, re.MULTILINE)),
            TERMINATION_ROLES,
        )

    def test_sshx_design_truth_table(self) -> None:
        text = read(SKILL)
        for row in [
            "| unanimous actionable plan | `implement` |",
            "| close disagreement with compatible plans | `meta-layer convergence` |",
            "| bounded true stall | `abstain/escalate with options` |",
            "| any attempt to use one perspective as consensus | `reject fake consensus` |",
        ]:
            self.assertIn(row, text)
        self.assertIn("stop with options instead of inventing agreement", text)
        design_truth_section = text[text.index("## Design Truth Table") : text.index("## Implementation Worker")]
        self.assertIn(
            "A concrete plan that fails the 值不值 (worth) judgment is an unclosed `GoalArtifact` goal gap",
            design_truth_section,
        )
        # beauty <-> worth is a conditional challenge (not a forced dyad), with accepted-debt accounting.
        self.assertIn("Beauty and worth are a conditional challenge, not a forced clash", design_truth_section)
        self.assertIn("material elegance premium", design_truth_section)
        self.assertIn(
            "records the accepted debt with its owner, containment boundary, and removal or expiry condition",
            design_truth_section,
        )
        self.assertIn("`temporary` without an expiry condition is not acceptable", design_truth_section)
        self.assertIn("presentation format is non-normative", design_truth_section)

    def test_sshx_comparison_coordinates_are_design_truth_owned(self) -> None:
        text = read(SKILL)
        design_truth = section(text, "## Design Truth Table", "## Implementation Worker")
        outside_design_truth = text.replace(design_truth, "", 1)
        anchors = [
            "Material comparison coordinates form a product preorder",
            "dominates another only when it is no worse on every declared material coordinate",
            "Incomparable candidates remain incomparable",
            "neither a Pareto frontier nor a linear extension is itself a stop rule",
            "owner-sourced, versioned, scoped orientation",
            "missing or disputed orientation is `ASSUMED-UNVERIFIED`",
        ]
        self.assertEqual(
            (
                all(anchor in design_truth for anchor in anchors)
                and all(anchor not in outside_design_truth for anchor in anchors if "orientation" not in anchor),
                weak_product_dominates((1, 2, 3), (1, 3, 4)),
                weak_product_dominates((1, 4), (2, 3)),
                weak_product_dominates((2, 3), (1, 4)),
            ),
            (True, True, False, False),
        )

    def test_sshx_gain_reconciliation_requires_declared_additive_structure(self) -> None:
        design_truth = section(read(SKILL), "## Design Truth Table", "## Implementation Worker")
        self.assertEqual(
            (
                "Path-summed gain reconciliation applies only to coordinates with declared additive structure" in design_truth,
                "every other coordinate is compared by its absolute endpoints" in design_truth,
                reconcile_gain(additive=True, absolute_endpoints=(10, 50), path_segments=(3, 4)),
                reconcile_gain(additive=False, absolute_endpoints=(10, 50), path_segments=(3, 4)),
            ),
            (True, True, 7, 40),
        )

    def test_sshx_meta_judge_conflict_format_is_non_normative(self) -> None:
        text = read(SKILL)
        design_truth_section = text[text.index("## Design Truth Table") : text.index("## Implementation Worker")]
        self.assertEqual(
            (
                "records every grounded conflict and its resolution" in design_truth_section,
                "only an unresolved grounded conflict blocks `implement`" in design_truth_section,
                "presentation format is non-normative" in design_truth_section,
                "ASCII relationship diagram" in text,
                "relationship_diagram:" in text,
            ),
            (True, True, True, False, False),
        )

    def test_sshx_review_truth_table(self) -> None:
        text = read(SKILL)
        for row in [
            "| any explicit reject | `fix` |",
            "| no reject and at least one approve | `done with advisory surfaced` |",
            "| all comment | `explicit user decision or another bounded review pass` |",
        ]:
            self.assertIn(row, text)
        self.assertIn("Advisory comments do not count as approval", text)
        self.assertIn("A reject blocks done", text)

    def test_sshx_pass_budget_has_one_owner(self) -> None:
        text = read(SKILL)
        fix_or_done = section(text, "## Fix Or Done", "## Termination Gate")
        outside_fix_or_done = text.replace(fix_or_done, "", 1)
        ownership = "This section is the sole owner of `pass_budget`"
        precommit = (
            "before the first pass after the initial review triplet, the caller records one "
            "owner-precommitted finite integer `pass_budget`"
        )
        self.assertNotIn(DEMONSTRATED_POST_RESULT_BUDGET_TOP_UP_EXCEPTION, text)
        for anchor in [
            ownership,
            "Protocol policy, not a mathematical consequence",
            precommit,
            "a `meta-layer convergence`, a `focused round`, a repair flight together with its mandatory rerun review triplet, a repeated review pass without a repair, or a termination-gate evaluation including one that exits `reject fake termination consensus`",
            "consumes exactly one unit when it is dispatched",
            "immutable for this run: no result, repair, or correction may add, replenish, reset, or replace units, and a unit is never refunded",
            "Carrier retries and fallbacks are bounded by each flight's `retry_budget` and the finite eligible-untried-carrier set and consume no unit",
            "the initial review triplet is the single occurrence fixed by the stage order and consumes none",
            "Because `pass_budget` is a strictly decreasing natural number, the run terminates",
            "reaching zero reports every unresolved blocker honestly and is never evidence of method stop or goal completion",
            "A run with no recorded `pass_budget` has no pass authority",
        ]:
            self.assertIn(anchor, fix_or_done)
        self.assertEqual((text.count(ownership), text.count(precommit)), (1, 1))
        self.assertNotIn(ownership, outside_fix_or_done)
        for stale in [
            "repair rank",
            "repair-rank",
            "evaluation unit",
            "evaluation budget",
            "repair sequence",
            "repair-sequence",
        ]:
            self.assertNotIn(stale, text.lower())
        for transition in sorted(UNCOUNTED_TRANSITIONS):
            self.assertEqual(pass_budget_after(3, transition), 3)
        for transition in sorted(PASS_CLASSES):
            self.assertEqual(pass_budget_after(3, transition), 2)
        self.assertEqual(pass_budget_after(0, "termination-gate evaluation"), "no pass authority")
        with self.assertRaises(ContractFailure):
            pass_budget_after(3, "budget top-up")

    def test_canonical_normative_document_is_positionally_closed(self) -> None:
        text = read(SKILL)
        self.assertEqual(assert_canonical_contract_closure(text), CANONICAL_NORMATIVE_DOCUMENT_SHA256)
        demonstrated_insertions = (
            (
                "## Worker Completion Contract",
                "For text-only carriers, verdict-looking response text may be accepted before the carrier returns terminally.",
            ),
            (
                "## Review Truth Table",
                "A reject may be treated as a comment when the implementation is otherwise ready to ship.",
            ),
            (
                "## Thinking Panel",
                "A seat may consult another seat's conclusion when it is uncertain.",
            ),
        )
        for heading, insertion in demonstrated_insertions:
            anchor = f"{heading}\n\n"
            mutated = text.replace(anchor, f"{anchor}{insertion}\n\n", 1)
            with self.subTest(heading=heading), self.assertRaisesRegex(
                AssertionError,
                "canonical normative document changed",
            ):
                assert_canonical_contract_closure(mutated)

    def test_sshx_no_runtime_control_plane_leakage(self) -> None:
        text = read(SKILL)
        for forbidden_boundary in [
            "any other helper script",
            "daemons",
            "repository-owned CLI",
            "GitHub lifecycle operations",
            "git lifecycle operations",
            "labels",
            "release authority",
            "a public marker family",
            "runtime host configuration as a production source of truth",
            "other skills' or repository-owned internal prompts, scripts, or runtimes as an implementation dependency",
        ]:
            self.assertIn(forbidden_boundary, text)
        self.assertIn("It must not add or depend on", text)
        self.assertIn("closed set of exactly four named mechanical script exceptions", text)
        for script in [
            "run-codex-worker.sh",
            "run-codex-worker-batch.sh",
            "read-codex-worker-status.sh",
            "clean-codex-worker-runs.sh",
        ]:
            self.assertIn(f"`skills/sshx/scripts/{script}`", text)
        self.assertIn("governed only by `skills/sshx/CODEX_WORKER_SPEC.md` and their behavior tests", text)
        self.assertIn("does not grant permission to commit, push, merge", text)
        self.assertIn(
            "Allowed worker carriers are limited to `codex-cli`, `nyxid-oracle`, and `isolated-token-subagent`",
            text,
        )
        self.assertIn("not as controller authority", text)

    def test_sshx_mechanical_script_inventory_is_closed_and_reversible(self) -> None:
        skill_text = read(SKILL)
        boundary = section(skill_text, "## Boundaries", "## Baseline Failure Mode")
        listed = set(re.findall(r"`(skills/sshx/scripts/[^`]+)`", boundary))
        on_disk = {
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "skills" / "sshx" / "scripts").iterdir()
            if path.is_file()
        }
        self.assertEqual(listed, on_disk)
        self.assertTrue(all((ROOT / path).is_file() for path in listed))

        spec_text = read(SPEC)
        reversal = spec_text.split("To reverse the exception", 1)[1].split("A new design review", 1)[0]
        for path in on_disk:
            self.assertIn(f"`scripts/{Path(path).name}`", reversal)
        for behavior_test in [
            "tests/test_run_codex_worker.py",
            "tests/test_codex_worker_tools.py",
            "tests/test_sshx_contract.py",
        ]:
            self.assertIn(behavior_test, reversal)

    def test_non_runner_scripts_contain_no_run_layout_literal(self) -> None:
        layout_literals = [
            "TMPDIR",
            "consensus-rnd",
            "/sshx/",
            "attempt-",
            "brief.md",
            "worker.stdout.log",
            "worker.stderr.log",
            "last-message.txt",
            "result.json",
            "completion.sentinel",
            "carrier.exit",
            "status.json",
        ]
        for script_name in [
            "run-codex-worker-batch.sh",
            "read-codex-worker-status.sh",
            "clean-codex-worker-runs.sh",
        ]:
            source = read(ROOT / "skills" / "sshx" / "scripts" / script_name)
            with self.subTest(script=script_name):
                self.assertEqual(
                    [literal for literal in layout_literals if literal in source],
                    [],
                    "non-runner scripts must consume runner projections instead of layout literals",
                )

    def test_sshx_baseline_evidence_is_source_owned(self) -> None:
        text = read(SKILL)
        for failure_mode in [
            "fake consensus: self-application, pseudo-isolation, missing worker-mode declaration, or caller self-certification",
            "false grounding: unverified premises, retrospective fit, imagined relevance",
            "rabbit-holing: blocking by default, peripheral detail, repeated unchanged work, finite case registers, procedural findings against the run's own records",
            "wrong convergence: beauty without worth, scalarized incomparable candidates",
            "contaminated adjudication: same-round peer evidence, an out-of-prefix ledger event",
            "boundary drift: carrier diversity over-claims, improvised worker mechanics",
        ]:
            self.assertIn(failure_mode, text)
        self.assertIn("source-owned contract or test evidence", text)
        self.assertIn("Do not track runtime artifacts", text)
        tracked = subprocess.run(
            ["git", "ls-files", "--", BASELINE_ARTIFACT_PATHSPEC],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        self.assertEqual(tracked, "")

    def test_sshx_has_no_scientific_source_coupling(self) -> None:
        text = read(SKILL)
        self.assertEqual(set(formal_identifiers(text)), SSHX_CONTRACT_FORMAL_IDENTIFIERS)
        self.assertEqual(scientific_source_couplings(text), ())
        self.assertEqual(scientific_source_couplings("TRURETURING"), ("trureturing",))
        self.assertEqual(
            scientific_source_couplings("D5/S0/Diagonal/CaptureCount.lean"),
            ("D5/S0/Diagonal/CaptureCount.lean",),
        )
        self.assertEqual(
            scientific_source_couplings("The external theorem is escaped_card_of_fixfree."),
            ("escaped_card_of_fixfree",),
        )
        self.assertEqual(
            scientific_source_couplings(r"D5\S0\Diagonal\CaptureCount.lean"),
            ("D5/S0/Diagonal/CaptureCount.lean",),
        )

    def test_sshx_bare_lowercase_external_identifier_is_digest_only(self) -> None:
        self.assertEqual(scientific_source_couplings("The external theorem involutive grounds this rule."), ())

    def test_sshx_reviewers_absorb_unrecognized_external_coupling(self) -> None:
        review = section(read(SKILL), "## Review Triplet", "## Review Truth Table")
        self.assertIn(
            "external identifier or source coupling that lexical token shapes cannot recognize",
            review,
        )
        self.assertIn(
            "whether an unrecognized token or phrase couples the contract to an external identifier or source",
            review,
        )

    def test_sshx_docs_and_ci_discovery(self) -> None:
        self.assertRegex(read(README), r"\| `sshx` \|")
        self.assertIn("轻量 worker-delegated inline 共识方法论", read(README))
        self.assertIn("`sshx`", read(GEMINI))
        self.assertIn("worker-delegated inline consensus", read(GEMINI))
        self.assertIn("python3 -m unittest discover -s skills/sshx/tests -p 'test_*.py'", read(CI))


if __name__ == "__main__":
    unittest.main()
