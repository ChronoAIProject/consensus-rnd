"""Source-regression checks with explicit methodological limits.

These assertions can prove the presence and location of sections, fields, enums,
ordering, and stable anchors, and can expose obvious deletion or replacement.
They cannot judge the semantics of free-form English. In particular, they cannot
prevent a retained normative sentence from being weakened by a later exception.

Known bypass evidence for that limit, each retaining the original normative text
and appending a weakening:

1. append that the single most salient condition is sufficient to trigger;
2. append that a later pass resets the one-round limit;
3. add panel-local precedence or reclassify the same event because of delay;
4. treat an unavailable harness as approval;
5. append ``merely recommended and may be omitted``;
6. allow ``No Context Pollution`` to inline full reasoning as an exception;
7. call the seven-stage order illustrative and permit skipped stages;
8. allow one seat to count as a complete triplet.

These are known source-regression limits, not defects queued for another mechanism;
this module does not provide end-to-end behavior validation.

The canonical default-seat-allocation span is normative wording. Rewriting it
requires a synchronized update to ``DEFAULT_SEAT_ALLOCATION_PATTERNS``. The
positive anchors accept ``each`` or ``every`` and do not require the four carrier
allocation propositions to stay in source order, but passive voice, tables, and
cardinal mappings are intentionally not accepted. The ``not|unless|except``
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
and all worker-panel or gate sections remain carrier-free.
"""

import ast
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
DECISION_GROUNDING_PREVENTIVE_BASIS = (
    "a current consumer (an existing call site) or an explicit `GoalArtifact` demand — "
    "a `normalized_goal` clause, `constraints`, or `success_criteria` item"
)

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
)
CARDINAL_CARRIER_BINDING_PATTERN = re.compile(
    rf"\b(?:\d+|one|two|three|four|five|six)\s+(?:{'|'.join(map(re.escape, CARRIER_IDENTIFIERS))})",
    flags=re.IGNORECASE,
)

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
GapOwnerAssignment: TypeAlias = tuple[JsonValue, JsonValue]
TerminationSeatResults: TypeAlias = tuple[tuple[JsonValue, JsonValue], ...]


@dataclass(frozen=True)
class TerminationResolution:
    truth_table_exit: str
    gap_route: str | None
    shared_budget_remaining: int
    roster_evaluations_consumed: int
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


def default_seat_allocation_span(text: str) -> tuple[int, int]:
    section_start = heading_index(text, "## Worker Delegation")
    worker_delegation = section(text, "## Worker Delegation", "## Result Envelope")
    start_anchor = "Do not self-apply the triplet inside the caller context and present it as worker consensus.\n\n"
    end_anchor = "The carrier-role pairing must be chosen and recorded before any worker"
    if worker_delegation.count(start_anchor) != 1 or worker_delegation.count(end_anchor) != 1:
        raise AssertionError("default seat allocation boundaries must be unique")
    relative_start = worker_delegation.index(start_anchor) + len(start_anchor)
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
    shared_budget_remaining: JsonValue = 1,
) -> TerminationResolution:
    if type(shared_budget_remaining) is not int or shared_budget_remaining <= 0:
        return TerminationResolution(
            truth_table_exit="withhold claim; shared bounded-pass ceiling reached",
            gap_route=None,
            shared_budget_remaining=0,
            roster_evaluations_consumed=0,
            fake_consensus_correction_allowed=False,
        )

    remaining = shared_budget_remaining - 1
    if type(consensus_source) is not str:
        return TerminationResolution(
            truth_table_exit="reject fake termination consensus",
            gap_route=None,
            shared_budget_remaining=remaining,
            roster_evaluations_consumed=1,
            fake_consensus_correction_allowed=remaining > 0,
        )
    roles: list[str] = []
    verdict_classes: list[str] = []
    for role, verdict in seat_results:
        if type(role) is not str:
            return TerminationResolution(
                truth_table_exit="reject fake termination consensus",
                gap_route=None,
                shared_budget_remaining=remaining,
                roster_evaluations_consumed=1,
                fake_consensus_correction_allowed=remaining > 0,
            )
        roles.append(role)
        verdict_classes.append(classify_termination_verdict(verdict))

    exact_roster = len(roles) == len(TERMINATION_ROLES) and set(roles) == set(TERMINATION_ROLES)
    if consensus_source != "termination-seats" or not exact_roster:
        return TerminationResolution(
            truth_table_exit="reject fake termination consensus",
            gap_route=None,
            shared_budget_remaining=remaining,
            roster_evaluations_consumed=1,
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
        shared_budget_remaining=remaining,
        roster_evaluations_consumed=1,
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

    def test_sshx_goal_contract_source_regression(self) -> None:
        text = read(SKILL)
        heading_index(text, "## Goal Contract")
        self.assertIn("`GoalArtifact` is a prompt-level record, not a runtime API", text)
        self.assertIn("It is written during `intake` before worker mode selection or any worker dispatch", text)
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
        self.assertIn("next_iteration_question:", text)

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
            "in a final report",
            "`done with advisory surfaced` outcome used as success",
            "`stop` gate action carrying the claim",
        ]:
            self.assertIn(claim_surface, termination)
        for non_application in [
            "`abstain` exit",
            "`escalate`",
            "`stop` action that reports a blocker rather than achievement",
            "`## Goal Contract` makes the gate inapplicable",
        ]:
            self.assertIn(non_application, termination)
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

    def test_sshx_termination_resolution_consumes_one_shared_budget_unit(self) -> None:
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
                    shared_budget_remaining=2,
                )
                self.assertEqual(resolution.truth_table_exit, expected_exit)
                self.assertEqual(resolution.shared_budget_remaining, 1)
                self.assertEqual(resolution.roster_evaluations_consumed, 1)
                self.assertEqual(resolution.fake_consensus_correction_allowed, name == "fake-consensus")

        last_unit = resolve_termination_claim(
            satisfied_results,
            consensus_source="caller",
            shared_budget_remaining=1,
        )
        self.assertEqual(last_unit.shared_budget_remaining, 0)
        self.assertFalse(last_unit.fake_consensus_correction_allowed)
        at_ceiling = resolve_termination_claim(
            satisfied_results,
            shared_budget_remaining=last_unit.shared_budget_remaining,
        )
        self.assertEqual(at_ceiling.truth_table_exit, "withhold claim; shared bounded-pass ceiling reached")
        self.assertEqual(at_ceiling.shared_budget_remaining, 0)
        self.assertEqual(at_ceiling.roster_evaluations_consumed, 0)

        for invalid_budget in (None, True, 1.5, [], {"remaining": 1}, EqualityRaises()):
            with self.subTest(invalid_budget=invalid_budget):
                resolution = resolve_termination_claim(
                    satisfied_results,
                    shared_budget_remaining=invalid_budget,
                )
                self.assertEqual(
                    resolution.truth_table_exit,
                    "withhold claim; shared bounded-pass ceiling reached",
                )
                self.assertEqual(resolution.roster_evaluations_consumed, 0)

        first = resolve_termination_claim(satisfied_results, consensus_source="caller", shared_budget_remaining=3)
        second = resolve_termination_claim(
            satisfied_results,
            consensus_source="caller",
            shared_budget_remaining=first.shared_budget_remaining,
        )
        self.assertEqual((first.shared_budget_remaining, second.shared_budget_remaining), (2, 1))

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
            "Flight exhaustion is not an additional table input",
            "fallback-recovered result is treated like any other valid result",
            "Roster means the dispatch-time recorded named role identities",
            "a named role absent from the roster reaches the first row",
            "a named role present without a valid result remains in the roster and reaches the fourth row",
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
            "Every roster evaluation, including one that exits `reject fake termination consensus`",
            "consumes exactly one unit of the shared bounded-pass budget in `## Fix Or Done`",
            "creates no nested budget",
            "never gates its own exit",
            "presentation rejected as fake termination consensus is not a completed gate run and may be corrected only while that shared budget remains",
            "later candidate is permitted only after new evidence or an authorized correction",
            "At the ceiling, report the unresolved blocker and do not certify satisfaction",
        ]:
            self.assertIn(anchor, truth_table)
        self.assertIn("This gate grants no authority over the host mechanism", text)

    def test_sshx_boundary_predicates_have_single_definitions(self) -> None:
        # Source-regression only: this checks unique definitions and references, not runtime enforcement.
        text = read(SKILL)
        reasoning = text[heading_index(text, "## Reasoning Discipline") : heading_index(text, "## Thinking Panel")]
        capability_definition = "`CapabilityOverlap` is the candidate-solution boundary check"
        threat_definition = "`ThreatEligibility` is the review-finding boundary check"
        grounding_definition = "`DecisionGrounding` is the decision-input admissibility check"
        self.assertEqual(text.count(capability_definition), 1)
        self.assertEqual(text.count(threat_definition), 1)
        self.assertEqual(text.count(grounding_definition), 1)
        self.assertIn(capability_definition, reasoning)
        self.assertIn(threat_definition, reasoning)
        self.assertIn(grounding_definition, reasoning)
        self.assertIn("These are independent checks that share the `harness` fact source", reasoning)

    def test_sshx_decision_grounding_contract(self) -> None:
        text = read(SKILL)
        reasoning = section(text, "## Reasoning Discipline", "## Thinking Panel")
        for anchor in [
            "no inadmissible input receives implementation work or blocking authority",
            "For predicted harm, name a current path through which the predicted harm is reachable",
            "a current call site or input path, an observed failure that demonstrates reachability, or a `GoalArtifact` term that makes the harm reachable",
            "`DecisionGrounding` judges only admissibility, never evidence strength",
            "how well an admissible premise is evidenced stays with `seek truth from facts` and its existing dispositions, which this check neither repeats nor overrides",
            "For preventive work — a defense, validation, abstraction, or compatibility path",
            f"name {DECISION_GROUNDING_PREVENTIVE_BASIS}",
            "a test introduced together with the defense under judgment may corroborate grounding but never creates it",
            "the defense would ground itself",
            "For blocking detail, this is the rabbit-holing limb, not an aesthetic matter",
            "exact `GoalArtifact` term it prevents satisfying",
            "pass the deletion counterfactual",
            "if omitting the detail changes no named `GoalArtifact` decision, it does not block",
            "depth past that point is not thoroughness",
            "Failure is objective, not semantic",
            f"only when it names none of the bases its applicable limb requires — a current path through which the predicted harm is reachable, {DECISION_GROUNDING_PREVENTIVE_BASIS}, or the exact `GoalArtifact` term the blocking detail prevents satisfying together with the deletion counterfactual",
            "A named basis that evidence shows to be false no longer counts as a named basis, so the input is inadmissible on that basis",
            "disputed grounding, not absent grounding",
            "keeps its full blocking force until the dispute is settled against evidence",
            "no one may declare an input ungrounded merely because its named basis is unpersuasive",
            "removes no actual defect",
            "a reachable failure, a trusted-party mistake, an omission, and a stated uncertainty stay grounded regardless of how expensive, inconvenient, or late the repair is",
            "sole basis of a `revise`, `reject`, `abstain`, blocking finding, `unsatisfied`, or any element of a concrete plan",
        ]:
            self.assertIn(anchor, reasoning)
        for forbidden in [
            "an unverified path has no blocking force",
            "an unverified named basis loses blocking force",
        ]:
            self.assertNotIn(forbidden, reasoning.lower())
        axis_separation = (
            "`DecisionGrounding` asks only whether a decision input is admissible; "
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
        self.assertNotIn("Concern" + "Grounding", text)

    def test_sshx_decision_grounding_preventive_basis_is_consistent(self) -> None:
        text = read(SKILL)
        reasoning_start = heading_index(text, "## Reasoning Discipline")
        reasoning_end = heading_index(text, "## Thinking Panel")
        reasoning = text[reasoning_start:reasoning_end]
        outside_reasoning = text[:reasoning_start] + text[reasoning_end:]
        self.assertEqual(reasoning.count(DECISION_GROUNDING_PREVENTIVE_BASIS), 2)
        self.assertEqual(outside_reasoning.count(DECISION_GROUNDING_PREVENTIVE_BASIS), 0)
        self.assertNotIn("an explicit `constraints` or `success_criteria` demand", text)
        self.assertNotIn("current consumer or explicit `constraints` or `success_criteria` demand", text)

    def test_sshx_decision_grounding_stage_references_and_downgrade_guards(self) -> None:
        text = read(SKILL)
        thinking = section(text, "## Thinking Panel", "## Design Truth Table")
        design = section(text, "## Design Truth Table", "## Implementation Worker")
        review = section(text, "## Review Truth Table", "## Fix Or Done")
        termination_gate = section(text, "## Termination Gate", "## Termination Truth Table")
        termination_table = section(text, "## Termination Truth Table", "## Boundaries")

        for contract_section in [thinking, design, review, termination_gate, termination_table]:
            self.assertIn("`DecisionGrounding`", contract_section)
        for anchor in [
            "every proposed plan element and every `propose`, `revise`, `reject`, or `abstain` basis",
            "named current path, current consumer, or `GoalArtifact` term",
            "An ungrounded basis is not a goal gap",
            "machinery that only defends against one must not enter a proposed plan",
        ]:
            self.assertIn(anchor, thinking)

        focused_round = design.split(
            "When a seat's `SshxResultEnvelope.conclusion` records", 1
        )[1].split("\n\n", 1)[0]
        self.assertIn(
            "the causal prediction recorded in that conclusion is falsifiable rather than a preference;",
            focused_round,
        )
        for anchor in [
            "provided the objection passes the `DecisionGrounding` prerequisite below",
            "objectively fails `DecisionGrounding` does not trigger a `FocusedRound`",
            "checks only whether the seat named any admissible basis at all and must not assess its persuasiveness",
            "named a basis whose correctness is disputed still triggers the round because disputed is not absent",
            "records that decline in the existing `finding_downgrades` record under the same own-words requirement that governs downgrades",
        ]:
            self.assertIn(anchor, focused_round)
        for anchor in [
            "fails `DecisionGrounding` under its objective-failure rule",
            "not an unclosed `GoalArtifact` goal gap and does not by itself hold the exit out of `implement`: the meta-judge records it as advisory",
            "existing `finding_downgrades` record",
            "the objecting seat itself named, or that it named none",
            "using the objecting seat's own words and never a paraphrase",
            "Disputed grounding stays blocking",
            "not permission to set aside a reachable defect",
        ]:
            self.assertIn(anchor, design)
        for anchor in [
            "must state its applicable `DecisionGrounding` basis under `## Reasoning Discipline`",
            "fails `ThreatEligibility` or `DecisionGrounding`",
            "A `DecisionGrounding` downgrade obeys its objective-failure rule",
            "the finder itself named, or that it named none",
            "using the finder's own words and never a paraphrase",
            "disputed grounding stays blocking",
            "only for threat-model ineligibility",
            "never because a finding is inconvenient",
            "never sets aside a reachable defect",
            "missing, ambiguous, or stale harness declaration",
            "never a downgrade shield",
        ]:
            self.assertIn(anchor, review)
        self.assertIn(
            "A blocking finding that fails `ThreatEligibility` or `DecisionGrounding` is downgraded by the meta-judge to an advisory with its reason recorded, then the remaining verdicts are routed again.",
            review,
        )
        self.assertIn(
            "Downgrade is allowed only for threat-model ineligibility or an ungrounded input, never because a finding is inconvenient, expensive, or late, and never sets aside a reachable defect.",
            review,
        )
        self.assertIn("named difference must pass `DecisionGrounding`", termination_gate)
        self.assertIn("an ungrounded worry is not a remaining difference", termination_gate)
        for anchor in [
            "Each termination seat applies `DecisionGrounding` itself before returning",
            "never a meta-judge downgrade path here",
            "no valid returned `unsatisfied` that passed the seat's check may be converted into permission by calling it ungrounded",
        ]:
            self.assertIn(anchor, termination_table)

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
            "for each candidate approach weighed",
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
        for boundary in [
            "not a runtime API",
            "not a daemon",
            "not a CLI",
            "not a parsed schema field",
            "not marker data",
            "not lifecycle authority",
            "not a second transcript channel",
            "not mandatory citation work",
            "not a literature search",
            "not a blocker for valid",
        ]:
            self.assertIn(boundary, reasoning_section)
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
            "Run six whole-picture philosopher seats before choosing a plan",
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
        one_round_bound_present = any(
            anchor in design_section for anchor in ["at most one focused round", "only one focused round"]
        )
        self.assertTrue(
            one_round_bound_present
            and all(
                anchor in design_section
                for anchor in [
                    "causal chain",
                    "disagreement remains afterward",
                    "escalate to the maintainer",
                ]
            ),
            "missing FocusedRound same-chain one-round bound or maintainer escalation",
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
        self.assertIn("Before each fix or repeated review pass", review_section)
        self.assertIn("After any explicit correction", fix_section)
        self.assertIn("`ThreatEligibility`", review_section)
        for section in [design_section, review_section, fix_section]:
            for action in ["`continue`", "`revise`", "`stop`", "`escalate`"]:
                self.assertIn(action, section)
            self.assertIn("responsible party", section)

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
        self.assertIn("fallible advisory worker exactly like `codex-cli`, never a privileged oracle", text)
        self.assertIn("carrier heterogeneity improves consensus quality", text)
        self.assertIn("statistically independent priors is `ASSUMED-UNVERIFIED`", text)
        self.assertIn("carrier-role pairing must be chosen and recorded before any worker", text)
        self.assertIn("three-seat `## Termination Gate` follows that same layout", text)
        self.assertIn("`tests` review seat must be assigned to a carrier capable of executing", text)
        self.assertIn("repository verification commands in the `work_target`", text)
        self.assertIn("if every completed seat ran on one model family, do not present the result as model-diverse", text)
        self.assertIn("must not be rebalanced in response to completion outcomes", text)
        self.assertIn("a retry or fallback may replace only the failed flight for the same seat and role", text)
        self.assertIn("If any fallback occurs or any initially paired carrier is unavailable", text)
        self.assertIn("do not claim that stage achieved model-diverse consensus", text)
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
        rewrites = (
            "At dispatch time, every multi-seat stage assigns exactly one seat to `isolated-token-subagent` and exactly one seat to `nyxid-oracle`. Every remaining seat in that stage goes to `codex-cli`, and every single-worker stage assigns its worker to `codex-cli`.",
            "At dispatch time, every multi-seat stage assigns exactly one seat to `isolated-token-subagent`, exactly one seat to `nyxid-oracle`, and every remaining seat to `codex-cli`. Every single-worker stage assigns its worker to `codex-cli`.",
            "At dispatch time:\n- Every multi-seat stage assigns exactly one seat to `isolated-token-subagent` and exactly one seat to `nyxid-oracle`.\n- Every remaining seat in that stage goes to `codex-cli`.\n- Every single-worker stage assigns its worker to `codex-cli`.",
            "At dispatch time, each multi-seat stage assigns exactly one seat to `isolated-token-subagent`, exactly one seat to `nyxid-oracle`, and each remaining seat to `codex-cli`; each single-worker stage assigns its worker to `codex-cli`.",
            "At dispatch time, every multi-seat stage assigns exactly one seat to `nyxid-oracle`, exactly one seat to `isolated-token-subagent`, and every remaining seat to `codex-cli`; every single-worker stage assigns its worker to `codex-cli`.",
        )
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
        self.assertIn("`WorkerModeGate` is a prompt-level dispatch gate, not a runtime API", text)
        self.assertIn(
            "During `intake`, the caller may use its own read-only tools to inspect the user's input and write `GoalArtifact`; this caller-owned read-only intake is not worker dispatch",
            text,
        )
        self.assertIn(
            "Before any worker dispatch, including delegated intake context-gathering by subagent, Agent, Task, or codex, the caller must complete the non-mutating `codex-cli` capability check and resolve `WorkerMode`",
            text,
        )
        self.assertIn("codex_cli_capability_check:", text)
        self.assertIn("worker_mode_gate:", text)
        self.assertIn("resolved_before_any_worker_dispatch:", text)
        self.assertIn("delegated_intake_context_gathering_allowed:", text)
        self.assertIn("fallback_reason:", text)
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
        text = read(SKILL)
        self.assertIn("For `isolated-token-subagent` workers, terminal completion is recognized only when", text)
        self.assertIn("its `completion_sentinel_ref` is recorded as `n/a`", text)
        self.assertIn("the flight becomes `abstained` and follows the origin-agnostic fallback rule", text)

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
            "governed solely by `## Worker Completion Contract` and are not restated here",
            worker_delegation,
        )
        self.assertNotIn("finite recovery read sequence", worker_delegation.lower())
        self.assertIn("when an attempt's dispatch invocation reports a structured terminal status", completion_contract)
        self.assertIn("that attempt does not enter recovery", completion_contract)
        self.assertIn("one bounded `nyxid oracle result` read", completion_contract)
        self.assertIn("that read is the attempt's single collection read", completion_contract)
        normal_unparseable_route = (
            "If that collection read's output is missing or unparseable, including empty or truncated output "
            "or output without a parseable structured status/result wrapper, it is not terminal completion: "
            "the matching flight becomes `abstained` and follows the origin-agnostic fallback rule"
        )
        self.assertIn(normal_unparseable_route, completion_contract)
        self.assertIn("only when both of these conditions hold", completion_contract)
        self.assertIn("structured terminal `status=completed`", completion_contract)
        self.assertIn("the `response` payload parses as a valid `SshxResultEnvelope`", completion_contract)
        self.assertIn("Intermediate task statuses (`queued`, `dispatched`", completion_contract)
        self.assertIn("no worker-owned independent completion sentinel", completion_contract)
        self.assertIn("records `result_envelope_ref` on the matching flight", completion_contract)
        self.assertIn("records `completion_sentinel_ref` there as `n/a`", completion_contract)
        self.assertIn("stage record's `log_ref`", completion_contract)
        self.assertNotIn("the flight's `log_ref`", completion_contract.lower())
        self.assertIn(
            "response prose, stdout, echoes, and log tails are never completion or verdict evidence",
            completion_contract,
        )

    def test_sshx_nyxid_oracle_recovery_trigger_and_bounds(self) -> None:
        completion_contract = section(read(SKILL), "## Worker Completion Contract", "## No Context Pollution")
        trigger = "enters dispatch-exit recovery only when both of these conditions hold"
        submitted = "dispatch call successfully submitted the oracle task"
        recorded = "caller recorded that task's oracle task reference"
        abnormal_exit = "dispatch call then exited unexpectedly before reporting a structured terminal status"
        self.assertIn(trigger, completion_contract)
        self.assertIn(submitted, completion_contract)
        self.assertIn(recorded, completion_contract)
        self.assertIn(abnormal_exit, completion_contract)
        self.assertIn("If the oracle task reference is unavailable, the attempt does not enter recovery", completion_contract)
        self.assertIn("the matching flight becomes `abstained` and follows the origin-agnostic fallback rule", completion_contract)
        self.assertIn("finite recovery read sequence against that same oracle task", completion_contract)
        self.assertIn("read-count upper bound and delay schedule must be chosen and recorded before the first recovery read", completion_contract)
        self.assertIn("Every scheduled delay must be positive and non-decreasing", completion_contract)
        self.assertIn("subject to the caller harness's task deadline", completion_contract)

    def test_sshx_nyxid_oracle_recovery_read_semantics(self) -> None:
        completion_contract = section(read(SKILL), "## Worker Completion Contract", "## No Context Pollution")
        non_terminal = "A recovery read that returns a structured non-terminal status is only a waiting recovery observation"
        first_terminal = "The first recovery read that returns a structured terminal status becomes that attempt's one and only collection read"
        self.assertIn(non_terminal, completion_contract)
        self.assertIn("it is not that attempt's collection read and provides no completion or verdict evidence", completion_contract)
        missing_or_unparseable = "A recovery read whose output is missing or unparseable is likewise only a waiting recovery observation"
        self.assertIn(missing_or_unparseable, completion_contract)
        self.assertIn("it consumes one predeclared recovery read slot", completion_contract)
        self.assertIn("is not that attempt's collection read", completion_contract)
        self.assertIn("provides no completion or verdict evidence", completion_contract)
        self.assertIn("if a slot remains, the recovery sequence may continue", completion_contract)
        self.assertIn(first_terminal, completion_contract)
        self.assertIn("after that terminal read, the caller must perform no additional reads", completion_contract)
        self.assertLess(completion_contract.index(non_terminal), completion_contract.index(first_terminal))
        self.assertLess(completion_contract.index(missing_or_unparseable), completion_contract.index(first_terminal))
        self.assertIn("Terminal completion is recognized only when both of these conditions hold", completion_contract)
        self.assertIn("a unique collection read that returns any status other than the structured terminal `status=completed`", completion_contract)
        self.assertIn("a `completed` collection read whose response envelope or required verdict is missing or invalid", completion_contract)
        self.assertIn("exhaustion of the recovery sequence without any structured terminal status", completion_contract)
        self.assertIn("makes the matching flight `abstained` under the origin-agnostic fallback rule", completion_contract)
        self.assertIn("not a polling authorization", completion_contract)
        self.assertIn("ordinary polling and busy-polling prohibition remains unchanged", completion_contract)
        self.assertIn("server-side lifecycle is independent of the waiting client invocation", completion_contract)
        self.assertIn("`result` reads are non-destructive state reads", completion_contract)

    def test_sshx_nyxid_oracle_blind_redispatch_is_idempotent_and_bounded(self) -> None:
        worker_delegation = section(read(SKILL), "## Worker Delegation", "## Result Envelope")
        self.assertIn("When blindly redispatching the same `nyxid-oracle` attempt", worker_delegation)
        self.assertIn("SHOULD reuse one stable submitter-scoped idempotency reference", worker_delegation)
        self.assertIn("repeated submissions converge on the same oracle task", worker_delegation)
        self.assertIn("carrier-supported client reference mechanism; this example is non-normative", worker_delegation)
        self.assertIn("A new attempt must use a new idempotency reference", worker_delegation)
        self.assertIn("This sentence does not itself authorize any redispatch", worker_delegation)
        self.assertIn("any blind redispatch must already be authorized by the existing bounded-attempt rules", worker_delegation)
        self.assertIn("it never replaces same-task recovery", worker_delegation)
        self.assertNotIn("does not authorize unlimited redispatch", worker_delegation.lower())
        self.assertNotIn("--client-ref", worker_delegation)

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
            "the runner launches exactly one direct non-interactive worker carrier",
            "neither layer may introduce a daemon or wrap the carrier in a repository-owned CLI",
            "owned by `CODEX_WORKER_SPEC.md`",
            "the required dispatch shape is the runner's default `danger-full-access` sandbox",
            "the caller passes no sandbox selection unless the maintainer explicitly directs a narrower one",
            "The caller must not poll worker artifact paths while the runner is active",
            "the runner performs one collection read of the derived `result_ref` and `completion_sentinel`",
            "Completion and verdict recognition stay governed by the `## Worker Completion Contract`",
        ]:
            self.assertIn(contract_string, worker_delegation)
        self.assertLess(
            worker_delegation.index("one collection read of the derived"),
            worker_delegation.index("`codex-cli` completion is recognized only when"),
        )

    def test_sshx_packaging_only_retry_contract(self) -> None:
        text = read(SKILL)
        worker_delegation = section(text, "## Worker Delegation", "## Result Envelope")
        no_context_pollution = section(text, "## No Context Pollution", "## Reasoning Discipline")
        packaging_retry = next(
            paragraph
            for paragraph in worker_delegation.split("\n\n")
            if "This packaging-only retry" in paragraph
        )
        for contract_string in [
            "When a `codex-cli` attempt",
            "terminates with runner `reason_code` `ENVELOPE_INVALID`",
            "by the runner's decision order, mechanically means that the carrier exited `0` and `result.json` exists but is invalid",
            "same carrier's predeclared `retry_budget` still has capacity",
            "include opaque path references",
            "that seat's own immediately preceding attempt artifacts",
            "next incremented attempt with the same `stage`, `role`, and `work_target`",
            "The caller must not open, summarize, or repair those artifacts",
            "read only its own immediately preceding attempt artifacts, never same-round peer output",
            "confirm for itself that the predecessor artifacts contain complete analysis reusable for the task",
            "must not package incomplete content into a terminal envelope",
            "that attempt follows the existing ordinary retry and fallback path",
            "That attempt consumes the predeclared `retry_budget`",
            "This packaging-only retry covers only envelope assembly failure",
            "`VERDICT_INVALID` and every other `reason_code` follow the existing ordinary retry and fallback path",
        ]:
            self.assertIn(contract_string, packaging_retry)
        self.assertNotIn("`nyxid-oracle`", packaging_retry)
        self.assertNotIn("`isolated-token-subagent`", packaging_retry)
        self.assertIn(
            "A seat's own immediately preceding attempt artifacts are not same-round peer output",
            no_context_pollution,
        )
        self.assertIn("does not constitute context pollution", no_context_pollution)

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

    def test_sshx_batch_signal_handling_joins_before_interrupted_publication(self) -> None:
        worker_delegation = section(read(SKILL), "## Worker Delegation", "## Result Envelope")
        for required in [
            "catches `INT` and `TERM`",
            "joins every recorded child before publishing a report",
            "records the first interruption",
            "ignores later `INT` and `TERM`",
            "repeats a wait only when that wait returned the recorded signal status",
            "marks the report interrupted",
            "does not forward the signal to runners or carriers",
            "whole-job-tree teardown remains the host's responsibility",
        ]:
            self.assertIn(required, worker_delegation)

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
        self.assertIn("must not mutate files, Git state, GitHub state", text)
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
        self.assertIn("recognized only when the caller has both a terminal `SshxResultEnvelope`", text)
        self.assertIn("worker-owned `completion_sentinel_ref` recorded on the matching flight", text)
        for forbidden_evidence in [
            "`pgrep`",
            "process-table snapshots",
            "log marker strings",
            "empty `git status` output",
        ]:
            self.assertIn(forbidden_evidence, text)
        self.assertIn("are never completion evidence", text)

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
        self.assertIn("`SshxResultEnvelope` is a prompt-level record, not a runtime API", text)
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
        self.assertIn("worker carrier process has exited with status `0`", text)
        self.assertIn("runner-derived, runner-owned `result_ref` artifact exists", text)
        self.assertIn("parses as a valid `SshxResultEnvelope`", text)
        self.assertIn(
            "the runner-derived, runner-owned `completion_sentinel` artifact exists and is recorded as `completion_sentinel_ref` on the matching `SshxWorkerFlightRecord`",
            text,
        )
        self.assertIn("`conclusion.verdict`", text)
        self.assertIn("A worker is not done while its carrier process is still running", text)
        for diagnostic_surface in [
            "stdout",
            "stderr",
            "raw transcripts",
            "final text",
            "prompt echoes",
            "`log_ref` content",
            "log tails",
        ]:
            self.assertIn(diagnostic_surface, text)
        self.assertIn("diagnostic only", text)
        self.assertIn("must not participate in done detection or verdict routing", text)
        self.assertIn("placeholder verdicts", text)
        self.assertIn("fail closed", text)
        contract = text.split("For `codex-cli` workers, caller-side completion", 1)[1].split("\n\nA worker", 1)[0]
        self.assertLess(contract.index("carrier process has exited"), contract.index("`result_ref` artifact exists"))
        self.assertLess(contract.index("`result_ref` artifact exists"), contract.index("parses as a valid"))
        self.assertLess(contract.index("parses as a valid"), contract.index("`conclusion.verdict`"))
        self.assertLess(contract.index("`conclusion.verdict`"), contract.index("`completion_sentinel` artifact exists"))

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
        for transcript_line in [
            "worker_flights:",
            "  - flight_id:",
            "    stage:",
            "    role:",
            "    worker_mode:",
            "    worker_carrier:",
            "    work_target:",
            "    status:",
            "    retry_budget:",
            "    attempt:",
            "    result_envelope_ref:",
            "    completion_sentinel_ref:",
            "    worker_flight_ref:",
        ]:
            self.assertIn(transcript_line, text)

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
        review_start = text.index("review_triplet_workers:")
        fix_start = text.index("fix_or_done:", review_start)
        review_block = text[review_start:fix_start]
        self.assertGreaterEqual(review_block.count("    bias:"), 3)
        self.assertGreaterEqual(review_block.count("    visible_inputs:"), 3)
        self.assertGreaterEqual(review_block.count("    worker_carrier:"), 3)

    def test_sshx_termination_transcript_is_nested_and_complete(self) -> None:
        text = read(SKILL)
        fix_start = text.index("fix_or_done:")
        template_end = text.index("```", fix_start)
        fix_block = text[fix_start:template_end]
        self.assertIn("  termination_gate:", fix_block)
        self.assertIn("    continuation_declaration_ref: # `GoalArtifact.harness.provided_capabilities`", fix_block)
        self.assertEqual(
            re.findall(r"^      - role: (criterion-evidence|residual-gap|claim-integrity)$", fix_block, re.MULTILINE),
            ["criterion-evidence", "residual-gap", "claim-integrity"],
        )
        for role in ("criterion-evidence", "residual-gap", "claim-integrity"):
            role_block = fix_block.split(f"      - role: {role}\n", 1)[1]
            role_block = role_block.split("      - role:", 1)[0].split("    meta_judge:", 1)[0]
            for field in [
                "bias",
                "visible_inputs",
                "worker_mode",
                "worker_carrier",
                "worker_flight_ref",
                "verdict",
                "conclusion",
                "log_ref",
            ]:
                self.assertIn(f"        {field}:", role_block)
        meta_judge = fix_block.split("    meta_judge:\n", 1)[1]
        for field in [
            "exit",
            "goal_gap",
            "next_iteration_question",
            "responsible_party",
            "conclusion",
            "log_ref",
        ]:
            self.assertIn(f"      {field}:", meta_judge)

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
        # the relationship diagram now enumerates six seats, including worth.
        self.assertIn("six philosopher-seat stances", design_truth_section)
        self.assertIn("`proportional-containment`, `worth`", design_truth_section)

    def test_sshx_meta_judge_requires_relationship_diagram(self) -> None:
        text = read(SKILL)
        design_truth_section = text[text.index("## Design Truth Table") : text.index("## Implementation Worker")]
        for required in [
            "compact free-form ASCII relationship diagram",
            "edges are labeled `agree`, `conflict`, `depends-on`, `resolved-by`, or `converges-to`",
            "any unresolved `conflict` edge is an unclosed `GoalArtifact` goal gap",
            "never `implement`",
            "not a parsed schema field, marker data, lifecycle authority, or a blocker for valid `abstain` or `reject fake consensus` exits",
        ]:
            self.assertIn(required, design_truth_section)
        self.assertIn("includes the free-form ASCII relationship diagram (no separate diagram field)", text)
        self.assertNotIn("relationship_diagram:", text)

    def test_sshx_review_truth_table(self) -> None:
        text = read(SKILL)
        for row in [
            "| any explicit reject | `fix` |",
            "| no reject and at least one approve | `done with advisory surfaced` |",
            "| all comment and no approve | `explicit user decision or another bounded review pass` |",
        ]:
            self.assertIn(row, text)
        self.assertIn("Advisory comments do not count as approval", text)
        self.assertIn("A reject blocks done", text)

    def test_sshx_bounded_passes_have_default_bound(self) -> None:
        text = read(SKILL)
        self.assertIn("defaults to at most five passes unless the user explicitly authorizes more", text)

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
            "prompt-only self-application where worker reasoning lives in the caller context",
            "transcript-based pseudo-isolation presented as enough for independent workers",
            "single-threaded advice presented as enough for consensus",
            "no required worker mode declaration for peer perspectives",
            "no fixed thinking truth table",
            "no same-shape review gate before done",
            "caller self-certification that a goal is satisfied inside a declared continuation context",
            "asserting current-system facts without verifying actual evidence",
            "silently relying on assumed factual premises",
            "judging only whether a plan is beautiful while never asking whether it is worth its cost",
            "over-building a beautiful form the goal does not need",
            "treating an imagined adversary, consumer, caller, or input path as an established premise and defending against it",
            "building defenses, validation, abstraction, or compatibility paths for a consumer no current call site or `GoalArtifact` term requires",
            "rabbit-holing into local detail that no `GoalArtifact` term reaches and letting it block done",
            "overstating carrier or model-family differences as evidence of independent priors or improved consensus quality",
            "only need inline consensus",
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

    def test_sshx_docs_and_ci_discovery(self) -> None:
        self.assertRegex(read(README), r"\| `sshx` \|")
        self.assertIn("轻量 worker-delegated inline 共识方法论", read(README))
        self.assertIn("`sshx`", read(GEMINI))
        self.assertIn("worker-delegated inline consensus", read(GEMINI))
        self.assertIn("python3 -m unittest discover -s skills/sshx/tests -p 'test_*.py'", read(CI))


if __name__ == "__main__":
    unittest.main()
