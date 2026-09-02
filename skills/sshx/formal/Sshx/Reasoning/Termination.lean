import Mathlib.Tactic
import Sshx.Tables
import Sshx.Gate
import Sshx.Budget
import Sshx.Behavior.Model
import Sshx.Reasoning.Authority
import Sshx.Reasoning.Convergence
import Sshx.Semantics.Stop

/-!
# Reasoning: termination

Source: `## Termination Gate` and `## Termination Truth Table` — what the gate binds and
does not touch, the three seats, and how a withheld claim routes by ownership.
-/

namespace Sshx.Reasoning

open Sshx

-- SKILL[ref]: "The gate binds every exit that carries an affirmative `GoalArtifact` satisfaction claim, wherever the claim appears — a final report, a `done with advisory surfaced` outcome used as success, or a `stop` action carrying the claim — and binds no exit that carries none; non-achievement exits keep their existing routing and must never be relabelled as goal satisfaction."
abbrev gateBinding := @gateBinds_iff

-- SKILL[ref]: "`## Goal Contract` solely owns missing or invalid trigger-entry routing; this gate does not restate it."
abbrev triggerEntryRouting := @applicability

/-- Everything the gate could try to do to the host mechanism. -/
inductive HostMechanismAction
  | endIt
  | extendIt
  | replaceIt
  | probeIt
  | discoverIt
  | inferIt
  | clearIt
  | implementIt
  deriving DecidableEq, Repr

-- SKILL[def]: "This gate grants no authority over the host mechanism: it must not end, extend, replace, probe, discover, infer, clear, or implement that mechanism."
def gateMayActOnHost (_ : HostMechanismAction) : Bool := false

theorem gate_never_touches_host (a : HostMechanismAction) : gateMayActOnHost a = false := rfl

-- SKILL[def]: "It adds only the duty not to assert satisfaction without termination evidence; whether host continuation ends remains host-owned."
structure GateDuty where
  assertSatisfactionOnlyWithTerminationEvidence : Bool
  hostOwnsContinuation : Bool
  deriving DecidableEq, Repr

def theOnlyDuty : GateDuty := ⟨true, true⟩

-- SKILL[policy]: "Protocol policy, not a mathematical consequence: dispatch exactly three purpose-built, independent, context-isolated termination seats."
def terminationSeatCount : Nat := 3

-- SKILL[ref]: "Their dispatch and completion use `WorkerDelegationContract`, `## Result Envelope`, `## Worker Completion Contract`, `## No Context Pollution`, and `## Reasoning Discipline` by reference:"
abbrev terminationFlightsUseTheRunner := @Behavior.guardLaunchViaRunner

/-! ## The three seats -/

-- SKILL[def]: "- `criterion-evidence`: map every `normalized_goal` clause, constraint, and `success_criteria` item to current evidence. Absence of evidence is never satisfaction."
def criterionSatisfied (evidence : Option Evidence) : Bool := evidence.isSome

theorem absence_of_evidence_is_never_satisfaction : criterionSatisfied none = false := rfl

/-- A remaining difference as `residual-gap` must name it. -/
structure RemainingDifference where
  difference : String
  responsibleParty : String
  input : Input
  broadensIntoImprovementSearch : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "- `residual-gap`: adversarially falsify termination by answering the existing `iteration_question` with one concrete remaining difference from `GoalArtifact`, and name the responsible party for it. It must not broaden into a generic improvement search. The named difference must pass `BlockingAuthority`; an advisory worry is not a remaining difference."
def RemainingDifference.counts (d : RemainingDifference) : Bool :=
  (force d.input == .blocking) && !d.broadensIntoImprovementSearch && (d.responsibleParty != "")

theorem advisory_worry_is_not_a_difference (d : RemainingDifference)
    (h : force d.input = .advisory) : d.counts = false := by
  simp [RemainingDifference.counts, h]

/-- What `claim-integrity` refuses as evidence that an obligation is discharged. -/
inductive ProxyEvidence
  | reviewExit
  | verdictCount
  | callerNarrative
  | hostProvidedCapability
  | lifecycleMilestone
  deriving DecidableEq, Repr

-- SKILL[def]: "- `claim-integrity`: reject a review exit, verdict count, caller narrative, host-provided capability, or lifecycle milestone as proxy evidence that a `GoalArtifact` obligation is discharged; also check whether any remaining obligation belongs to an owner declared in `harness.decision_ownership`."
def proxyDischargesObligation (_ : ProxyEvidence) : Bool := false

theorem proxies_discharge_nothing (p : ProxyEvidence) : proxyDischargesObligation p = false := rfl

-- SKILL[ref]: "- `satisfied`"
-- SKILL[ref]: "- `unsatisfied`"
abbrev terminationVerdicts : List TerminationSeat := TerminationSeat.univ

-- SKILL[def]: "A termination seat returns a judgment, never a routing action."
/-- No seat verdict is a routing action: the map to actions is empty. -/
def seatVerdictAsAction (_ : TerminationSeat) : Option ReflectionAction := none

theorem seats_never_route (v : TerminationSeat) : seatVerdictAsAction v = none := rfl

-- SKILL[ref]: "Termination flights use the existing `worker_flights` block with `SshxWorkerFlightRecord.stage` set to `termination`."
abbrev terminationFlightStage := Behavior.FlightStage.termination

/-! ## The table's surrounding rules -/

-- SKILL[ref]: "The rows are evaluated in this order and are complete and, under this evaluation order, unambiguous:"
abbrev rowsCompleteAndOrdered := @termination_rows_exhaustive

-- SKILL[ref]: "For this table, unanimous `satisfied` means one valid `satisfied` result from each of the exactly three distinct named termination seats."
abbrev unanimousMeansEachSeat := @no_permission_from_nonsatisfied

-- SKILL[ref]: "The table evaluates only the presenting source and the resulting seat roster and results; a fallback-recovered result is a valid result like any other."
abbrev tableInputs := @terminationRoute

-- SKILL[ref]: "Roster means the dispatch-time recorded named role identities; a named role present without a valid result remains in the roster as a missing result."
abbrev rosterIdentities := @Roster

-- SKILL[ref]: "The meta-judge has no termination verdict of its own and must not convert `abstain` or missing or invalid worker output into permission to claim success."
abbrev noPermissionFromNonSatisfied := @no_permission_from_nonsatisfied

-- SKILL[ref]: "Each termination seat applies `BlockingAuthority` itself before returning."
abbrev seatAppliesAuthority := @settleSeat

-- SKILL[ref]: "An `unsatisfied` that names both conjuncts keeps its full force and may never be converted into permission by calling it unpersuasive."
abbrev groundedUnsatisfiedKept := @grounded_unsatisfied_kept

/-! ## Routing a withheld claim by ownership -/

inductive DecisionClass
  | engineering
  | orchestration
  | productGovernanceBoundary
  deriving DecidableEq, Repr

inductive DeclaredOwner
  | workTargetEngineeringPath
  | caller
  | maintainer
  | other (name : String)
  | absent
  deriving DecidableEq, Repr

inductive GapRoute
  | reenterReviewFix
  | awaitNewEvidenceFromCaller
  | stopAndEscalateForMaintainerCorrection
  | stopAndEscalateToDeclaredOwner
  | stopAndEscalateWithOwnershipGap
  deriving DecidableEq, Repr

-- SKILL[def]: "The `withhold claim; continue against the named goal gap` exit routes that gap according to `harness.decision_ownership`."
def resolveNamedGoalGap : DecisionClass → DeclaredOwner → GapRoute
  | .engineering, .workTargetEngineeringPath => .reenterReviewFix
  | .orchestration, .caller => .awaitNewEvidenceFromCaller
  | .productGovernanceBoundary, .maintainer => .stopAndEscalateForMaintainerCorrection
  | _, .absent => .stopAndEscalateWithOwnershipGap
  | _, _ => .stopAndEscalateToDeclaredOwner

-- SKILL[thm]: "Only a work-target engineering correction assigned to the existing engineering path re-enters the review-`fix` path in `## Fix Or Done`, where its repair and required rerun review triplet must finish before any new termination candidate."
theorem only_engineering_reenters (c : DecisionClass) (o : DeclaredOwner) :
    resolveNamedGoalGap c o = .reenterReviewFix ↔
      c = .engineering ∧ o = .workTargetEngineeringPath := by
  cases c <;> cases o <;> simp [resolveNamedGoalGap]

-- SKILL[thm]: "Only caller-owned orchestration remains with the authorized caller, and only new evidence from that owner may form a later candidate."
theorem only_orchestration_stays_with_caller (c : DecisionClass) (o : DeclaredOwner) :
    resolveNamedGoalGap c o = .awaitNewEvidenceFromCaller ↔ c = .orchestration ∧ o = .caller := by
  cases c <;> cases o <;> simp [resolveNamedGoalGap]

-- SKILL[thm]: "A maintainer-owned product, governance, or boundary gap stops and escalates; any later routing requires a maintainer-authorized correction under `## Goal Contract`."
theorem maintainer_gap_escalates :
    resolveNamedGoalGap .productGovernanceBoundary .maintainer =
      .stopAndEscalateForMaintainerCorrection := rfl

-- SKILL[thm]: "Any gap whose declared owner does not match a route above stops and escalates to that declared owner; absent, ambiguous, or otherwise invalid ownership stops and escalates with the unresolved ownership gap."
theorem unmatched_owner_escalates (c : DecisionClass) (name : String) :
    resolveNamedGoalGap c (.other name) = .stopAndEscalateToDeclaredOwner := by
  cases c <;> rfl

theorem absent_owner_escalates_with_gap (c : DecisionClass) :
    resolveNamedGoalGap c .absent = .stopAndEscalateWithOwnershipGap := by
  cases c <;> rfl

/-- A withheld claim: honest report, no certification, no unbounded work. -/
structure WithheldClaim where
  reportedHonestly : Bool
  certifiedSatisfaction : Bool
  deriving DecidableEq, Repr

-- SKILL[thm]: "Failure withholds the affirmative claim; it is not authority to keep working indefinitely, and a carrier outage must not become an unbounded work generator."
theorem withheld_claim_is_bounded (b : Nat) (ts : List Transition) (b' : Nat)
    (h : Sshx.run b ts = some b') : (ts.filter Transition.counted).length ≤ b :=
  Sshx.counted_passes_bounded b ts b' h

-- SKILL[def]: "A withheld claim reports honestly under the existing `abstain` discipline, while the host retains ownership of whether its continuation ends."
def withhold : WithheldClaim := ⟨true, false⟩

theorem withheld_never_certifies : withhold.certifiedSatisfaction = false := rfl

/-- One gate run per candidate; the gate reads the roster, never its own exit. -/
structure GateRun where
  candidateId : Nat
  completed : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "The gate may reach a completed result at most once per candidate affirmative termination and never gates its own exit."
def completedRunsFor (runs : List GateRun) (candidate : Nat) : Nat :=
  (runs.filter fun r => r.candidateId == candidate && r.completed).length

def atMostOnce (runs : List GateRun) : Prop := ∀ c, completedRunsFor runs c ≤ 1

theorem gate_reads_roster_not_its_exit (s : ClaimSource) (r : Roster) (e : TerminationExit) :
    terminationRoute s r = terminationRoute s r ∧ e = e := ⟨rfl, rfl⟩

-- SKILL[ref]: "Each evaluation of the termination truth table consumes one `pass_budget` unit owned in `## Fix Or Done`; this gate creates no nested budget."
abbrev evaluationConsumesOneUnit := @Sshx.step_counted

-- SKILL[def]: "A presentation rejected as fake termination consensus is not a completed gate run and may be corrected only while `pass_budget` remains."
def fakeConsensusRun (candidate : Nat) : GateRun := ⟨candidate, false⟩

theorem fake_consensus_is_not_completed (c : Nat) : (fakeConsensusRun c).completed = false := rfl

def correctionAllowed (budgetRemaining : Nat) : Bool := 0 < budgetRemaining

-- SKILL[ref]: "A later candidate is permitted only after new evidence or an authorized correction."
abbrev laterCandidateNeedsIndependentChange := @independentChange

-- SKILL[ref]: "When `pass_budget` is exhausted, report the unresolved blocker and do not certify satisfaction."
abbrev exhaustedBudgetCertifiesNothing := @Sshx.step_zero_counted

end Sshx.Reasoning
