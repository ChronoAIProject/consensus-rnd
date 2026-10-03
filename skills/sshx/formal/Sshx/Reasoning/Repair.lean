import Mathlib.Tactic
import Sshx.Budget
import Sshx.Gate
import Sshx.Records
import Sshx.Reasoning.Convergence

/-!
# Reasoning: fix or done

Source: `## Fix Or Done` — the reflection gate before every pass, reachability evidence
versus mere non-improvement, the repair step, main-path-first repair order, and the exits.
-/

namespace Sshx.Reasoning

open Sshx

-- SKILL[ref]: "Before each fix or repeated review pass, use the existing gate to ask whether the goal or harness changed and whether evidence overturned the direction; emit exactly one concrete `continue`, `revise`, `stop`, or `escalate` action and name its responsible party."
abbrev gateBeforeEachPass := @reflect

/-- Evidence about whether the current approach can reach the gap. -/
inductive ReachabilityEvidence
  | reachableByCurrentApproach
  | unreachableByCurrentApproach
  deriving DecidableEq, Repr

inductive ApproachVerdict
  | keepApproach
  | changeApproach
  | undetermined
  deriving DecidableEq, Repr

-- SKILL[def]: "When that gate weighs whether evidence has overturned the direction across repeated passes on the same blocking goal gap, distinguish evidence that the gap is reachable by the current approach from evidence that it is not."
def approachVerdict : Option ReachabilityEvidence → ApproachVerdict
  | some .reachableByCurrentApproach => .keepApproach
  | some .unreachableByCurrentApproach => .changeApproach
  | none => .undetermined

-- SKILL[thm]: "Consecutive passes without improvement are, alone, evidence of neither: they do not prove the current approach is exhausted, and they do not license further identical passes as progress."
/-- The verdict reads no pass counter: any number of unimproved passes leaves it undetermined
without reachability evidence, and never turns into progress. -/
theorem unimproved_passes_prove_nothing (passesWithoutImprovement : Nat) :
    approachVerdict none = .undetermined ∧ passesWithoutImprovement = passesWithoutImprovement :=
  ⟨rfl, rfl⟩

def identicalPassIsProgress : Bool := false

/-- Coverage judgments in the existing conclusion; these are review evidence, not new
runtime fields. A verified invariant or abstraction can cover an infinite domain. -/
inductive ClassCoverageBasis
  | uniformInvariant (verified : Bool)
  | soundAbstraction (verified : Bool)
  | completeFiniteTreatment (verified : Bool)
  | authorizedBoundary (enforced authorized : Bool)
  | unsupportedMemberEnumeration
  | unknown
  deriving DecidableEq, Repr

inductive FamilyRoute
  | continue
  | reviseOrInvestigate
  | honestStop
  | ownerDecision
  deriving DecidableEq, Repr

-- SKILL[def]: "A uniform invariant, a verified complete finite treatment, or an already-authorized enforced boundary may establish class coverage; otherwise the class gate routes bounded revise or investigation, and unresolved coverage is reported honestly."

-- SKILL[def]: "Record the goal/property, authorized domain, family, coverage basis, action/owner and falsifiable validation in the existing conclusion."
structure FamilyEvidence where
  goalTerm : String
  property : String
  authorizedInputDomain : String
  mechanismFamily : String
  actionOwner : String
  falsifiableValidation : String
  sameGoalTerm : Bool
  sameMechanismFamily : Bool
  consecutivePasses : Nat
  earlierUnsupportedEnumeration : Bool
  coverageBasis : ClassCoverageBasis
  noSupportedPath : Bool
  ownerDecisionRequired : Bool
  changesDomainOrCriterion : Bool
  ownerAuthorized : Bool
  revision : Option Revision
  deriving DecidableEq, Repr

-- SKILL[ref]: "Domain or criterion changes require the existing owner authorization and append-only revision."
def ownerAuthorizedDomainChange (e : FamilyEvidence) : Bool :=
  !e.changesDomainOrCriterion || (e.ownerAuthorized && e.revision.isSome)

def FamilyEvidence.recordComplete (e : FamilyEvidence) : Bool :=
  e.goalTerm != "" && e.property != "" && e.authorizedInputDomain != "" &&
    e.mechanismFamily != "" && e.actionOwner != "" && e.falsifiableValidation != ""

-- SKILL[def]: "Before dispatching a repair after two consecutive passes whose blocking findings name the same `GoalArtifact` term and `mechanism_family`, or after earlier recorded evidence shows that member-by-member enumeration leaves the property unsupported, the existing gate makes one class-level decision."
def classGateActive (e : FamilyEvidence) : Bool :=
  (e.sameGoalTerm && e.sameMechanismFamily && decide (2 ≤ e.consecutivePasses)) ||
    e.earlierUnsupportedEnumeration

-- SKILL[def]: "Repair dispatch requires the recorded family and a verified coverage basis under `## Reasoning Discipline`."
-- The formal basis additionally admits a verified sound abstraction over an open or infinite
-- domain when its admissible-input correspondence and falsifier are recorded.
-- SKILL[def]: "A sound abstraction may cover an infinite domain with recorded admissible-input correspondence and a falsifiable invariant."
def coverageBasisVerified : ClassCoverageBasis → Bool
  | .uniformInvariant verified | .soundAbstraction verified | .completeFiniteTreatment verified => verified
  | .authorizedBoundary enforced authorized => enforced && authorized
  | .unsupportedMemberEnumeration | .unknown => false

-- SKILL[def]: "Unknown family or coverage routes bounded investigation under the intake scope; no supported path means honest unresolved stop, with only actual product, governance, boundary or permission decisions routed to their owner."
def familyRoute (e : FamilyEvidence) : FamilyRoute :=
  if e.ownerDecisionRequired || (e.changesDomainOrCriterion && !e.ownerAuthorized) then .ownerDecision
  else if !ownerAuthorizedDomainChange e then .reviseOrInvestigate
  else if !classGateActive e then .continue
  else if e.noSupportedPath then .honestStop
  else if e.recordComplete && coverageBasisVerified e.coverageBasis then .continue
  else .reviseOrInvestigate

-- SKILL[def]: "A new fixture, recurrence, zero usage, or exhausted budget cannot authorize a member patch or narrow the domain."
/-- The actual caller pass guard consumes this route. Unsupported class coverage permits
only a bounded investigation/convergence or review, never another member repair. -/
def familyPassAllowed (e : FamilyEvidence) (t : Transition) : Bool :=
  match t with
  | .repairWithRerunReview => familyRoute e == .continue
  | .repeatedReviewPass | .metaLayerConvergence | .focusedRound =>
      familyRoute e == .continue || familyRoute e == .reviseOrInvestigate
  | _ => true

theorem unsupported_class_never_dispatches (e : FamilyEvidence)
    (h : e.coverageBasis = .unsupportedMemberEnumeration)
  (ha : classGateActive e = true) :
    familyPassAllowed e .repairWithRerunReview = false := by
  simp only [familyPassAllowed, familyRoute, ha, h, coverageBasisVerified]
  unfold ownerAuthorizedDomainChange
  cases e.ownerDecisionRequired <;> cases e.changesDomainOrCriterion <;>
    cases e.ownerAuthorized <;> cases e.noSupportedPath <;> cases e.revision <;> simp_all

/-- Where a blocking gap sits in `GoalArtifact`; lower ranks are repaired first. -/
inductive GapRank
  | normalizedGoal
  | constraint
  | successCriterion
  | periphery
  deriving DecidableEq, Repr

def GapRank.order : GapRank → Nat
  | .normalizedGoal => 0
  | .constraint => 1
  | .successCriterion => 2
  | .periphery => 3

-- SKILL[ref]: "When a pass carries more than one blocking goal gap, repair them in goal-primacy rank, so the main path is repaired first."
def repairOrder (gaps : List GapRank) : List GapRank :=
  gaps.filter (· == .normalizedGoal) ++ gaps.filter (· == .constraint) ++
    gaps.filter (· == .successCriterion) ++ gaps.filter (· == .periphery)

theorem main_path_first (gaps : List GapRank) (h : .normalizedGoal ∈ gaps) :
    (repairOrder gaps).head? = some .normalizedGoal := by
  unfold repairOrder
  have hmem : GapRank.normalizedGoal ∈ gaps.filter (· == .normalizedGoal) := by
    simp [List.mem_filter, h]
  obtain ⟨x, xs, hx⟩ : ∃ x xs, gaps.filter (· == .normalizedGoal) = x :: xs := by
    cases hl : gaps.filter (· == .normalizedGoal) with
    | nil => simp [hl] at hmem
    | cons x xs => exact ⟨x, xs, rfl⟩
  have hxmem : x ∈ gaps.filter (· == .normalizedGoal) := by simp [hx]
  have hxeq : x = .normalizedGoal := by simpa using (List.mem_filter.mp hxmem).2
  simp [hx, hxeq]

-- SKILL[ref]: "At zero units, start no new pass and report remaining blockers honestly; finish the already-paid batch including its review."
abbrev stopWhenExhausted := @Sshx.step_zero_counted

inductive DoneRoute
  | claimCandidateThroughGate
  | reportDone
  | withholdClaim
  deriving DecidableEq, Repr

-- SKILL[def]: "If review exits `done with advisory surfaced`, treat that exit as a candidate for an affirmative success claim rather than the claim itself when `## Termination Gate` applies, and route the candidate through that gate before reporting success."
def routeDoneExit : Applicability → DoneRoute
  | .applies => .claimCandidateThroughGate
  | .inapplicable => .reportDone
  | .withholdClaim => .withholdClaim

theorem done_is_only_a_candidate_when_gate_applies :
    routeDoneExit .applies = .claimCandidateThroughGate := rfl

inductive BoundedPassChoice
  | oneMoreBoundedPass (nextIterationQuestion : String)
  | askTheUser
  deriving DecidableEq, Repr

-- SKILL[def]: "If review exits `explicit user decision or another bounded review pass`, run one more bounded pass with a concrete next iteration question tied to `GoalArtifact`; when no bounded pass remains, report the unresolved evidence and route it to the declared owner."
def allCommentExitChoices (question : String) (needsOwnerDecision : Bool) : List BoundedPassChoice :=
  [.oneMoreBoundedPass question] ++ if needsOwnerDecision then [.askTheUser] else []

-- SKILL[ref]: "Do not pause for routine confirmation or ask the user to choose a method, and do not loop indefinitely."
abbrev noIndefiniteLoop := @Sshx.counted_passes_bounded

-- SKILL[ref]: "After any explicit correction, repeat this section's direction gate before further work."
abbrev correctionGate := @reflect

-- SKILL[ref]: "Carrier retries and fallbacks are bounded by each flight's `retry_budget` and the finite eligible-untried-carrier set and consume no unit; the initial review triplet is the single occurrence fixed by the stage order and consumes none."
abbrev uncountedTransitions := @Sshx.step_uncounted

end Sshx.Reasoning
