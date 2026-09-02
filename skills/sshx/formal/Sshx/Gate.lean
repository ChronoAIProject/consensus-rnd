/-!
# Termination gate: applicability, binding, sealed inputs, and independent predicates

Source: `## Goal Contract` (trigger entry), `## Termination Gate`, and the model-diverse
claim sentences of `## Worker Delegation`.
-/

namespace Sshx

/-! ## Applicability (`## Goal Contract`) -/

/-- The `harness.provided_capabilities` continuation entry as the boundary owner left it. -/
inductive ContinuationEntry
  | present
  | absent
  | silent
  | ambiguous
  | unconfirmed
  deriving DecidableEq, Repr

inductive Applicability
  | applies
  | inapplicable
  | escalateToMaintainer
  deriving DecidableEq, Repr

-- SKILL[def]: "The termination gate is triggered only by a positive, boundary-owner-confirmed entry declaring such a mechanism."
/-- The gate is triggered only by a positive, boundary-owner-confirmed entry; a silent or
explicitly negative complete harness makes the gate inapplicable without asserting the
mechanism is absent; anything else is the harness rule: stop and escalate. -/
def applicability (harnessComplete : Bool) (e : ContinuationEntry) : Applicability :=
  if !harnessComplete then .escalateToMaintainer
  else match e with
    | .present => .applies
    | .absent | .silent => .inapplicable
    | .ambiguous | .unconfirmed => .escalateToMaintainer

theorem applies_iff (h : Bool) (e : ContinuationEntry) :
    applicability h e = .applies ↔ h = true ∧ e = .present := by
  cases h <;> cases e <;> simp [applicability]

/-! ## Binding (`## Termination Gate`) -/

/-- Exits a `fix_or_done` run can end in. `affirmative` is the one attribute the gate
reads: the contract's "binds ..." list and "does not bind ..." list are exactly the two
halves of this predicate. -/
inductive Exit
  | finalReportClaim
  | doneWithAdvisoryAsSuccess
  | stopWithClaim
  | abstain
  | escalate
  | stopWithBlocker
  deriving DecidableEq, Repr

def Exit.affirmative : Exit → Bool
  | .finalReportClaim | .doneWithAdvisoryAsSuccess | .stopWithClaim => true
  | .abstain | .escalate | .stopWithBlocker => false

def Exit.univ : List Exit :=
  [.finalReportClaim, .doneWithAdvisoryAsSuccess, .stopWithClaim, .abstain, .escalate,
    .stopWithBlocker]

theorem Exit.mem_univ (e : Exit) : e ∈ Exit.univ := by cases e <;> decide

/-- The gate binds an exit iff it applies and the exit is affirmative. -/
def gateBinds (a : Applicability) (e : Exit) : Bool := (a == .applies) && e.affirmative

theorem gateBinds_iff (a : Applicability) (e : Exit) :
    gateBinds a e = true ↔ a = .applies ∧ e.affirmative = true := by
  cases a <;> cases e <;> simp [gateBinds, Exit.affirmative]

/-- Non-affirmative exits keep their routing under every applicability. -/
theorem nonaffirmative_never_bound (a : Applicability) (e : Exit) (h : e.affirmative = false) :
    gateBinds a e = false := by
  simp [gateBinds, h]

/-! ## Sealed stop inputs -/

/-- Before evaluation the caller seals the current affirmative candidate, the feasible
decision set, and an owner-sourced, versioned, scoped orientation; the claim is computed
only from those. -/
structure Sealed where
  candidate : Option Nat
  feasible : List Nat
  orientationSourced : Bool
  orientationVersioned : Bool
  orientationScoped : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "seal the current affirmative candidate, the feasible termination decision set, and an owner-sourced, versioned, scoped orientation"
def sealedAdmits (s : Sealed) : Bool :=
  match s.candidate with
  | none => false
  | some c => s.feasible.contains c && s.orientationSourced && s.orientationVersioned &&
      s.orientationScoped

theorem sealed_no_candidate (s : Sealed) (h : s.candidate = none) : sealedAdmits s = false := by
  simp [sealedAdmits, h]

theorem sealed_empty_feasible (s : Sealed) (h : s.feasible = []) : sealedAdmits s = false := by
  cases hc : s.candidate <;> simp [sealedAdmits, hc, h]

theorem sealed_outside_feasible (s : Sealed) (c : Nat) (hc : s.candidate = some c)
    (h : s.feasible.contains c = false) : sealedAdmits s = false := by
  simp only [sealedAdmits, hc, h, Bool.false_and]

/-! ## Three independent predicates -/

-- SKILL[def]: "Method stop, a protocol or review exit, and `GoalArtifact` completion are separate predicates."
/-- Method stop, a protocol or review exit, and `GoalArtifact` completion are separate
coordinates: every combination is a legal state, so none implies another. -/
structure StopState where
  methodStop : Bool
  reviewExitReached : Bool
  goalComplete : Bool
  deriving DecidableEq, Repr

theorem review_exit_not_completion :
    ∃ s : StopState, s.reviewExitReached = true ∧ s.goalComplete = false :=
  ⟨⟨false, true, false⟩, rfl, rfl⟩

theorem budget_exhaustion_not_completion :
    ∃ s : StopState, s.methodStop = true ∧ s.goalComplete = false :=
  ⟨⟨true, false, false⟩, rfl, rfl⟩

theorem completion_not_method_stop :
    ∃ s : StopState, s.goalComplete = true ∧ s.methodStop = false :=
  ⟨⟨false, false, true⟩, rfl, rfl⟩

/-! ## Model-diverse claim (`## Worker Delegation`) -/

/-- What a stage may truthfully say about carrier diversity. -/
structure StageDiversity where
  everyInitialSeatCompleted : Bool
  anyFallbackOrUnavailable : Bool
  distinctRecordedFamilies : Nat
  deriving DecidableEq, Repr

/-- The claim is permitted only when every initially paired seat completed, no fallback or
unavailability occurred, and at least two distinct model families are recorded evidence.
Each forbidding case the contract enumerates is an instance of the negation. -/
def diversityClaimAllowed (s : StageDiversity) : Bool :=
  s.everyInitialSeatCompleted && !s.anyFallbackOrUnavailable &&
    decide (2 ≤ s.distinctRecordedFamilies)

theorem one_family_forbids (s : StageDiversity) (h : s.distinctRecordedFamilies ≤ 1) :
    diversityClaimAllowed s = false := by
  simp [diversityClaimAllowed]
  omega

theorem fallback_forbids (s : StageDiversity) (h : s.anyFallbackOrUnavailable = true) :
    diversityClaimAllowed s = false := by
  simp [diversityClaimAllowed, h]

theorem incomplete_seat_forbids (s : StageDiversity) (h : s.everyInitialSeatCompleted = false) :
    diversityClaimAllowed s = false := by
  simp [diversityClaimAllowed, h]

end Sshx
