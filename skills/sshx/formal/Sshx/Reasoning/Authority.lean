import Mathlib.Tactic
import Sshx.Blocking
import Sshx.Records
import Sshx.Semantics.Register
import Sshx.Reasoning.Discipline

/-!
# Reasoning: boundary checks and blocking authority

Source: `## Reasoning Discipline` — `CapabilityOverlap`, `ThreatEligibility`,
`BlockingAuthority` (conjuncts, plan-element admission, objective failure), the advisory
shapes, escaping residues, absorption, and the six independent questions.
-/

namespace Sshx.Reasoning

open Sshx D5.S0.Diagonal.EscapeCount

/-! ## Boundary checks -/

/-- What a candidate solution does relative to the declared harness. -/
structure CandidateSolution where
  takesOverProvidedCapability : Bool
  changesDecisionOwnership : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "`CapabilityOverlap` is the candidate-solution boundary check: ask whether a candidate takes over a capability already declared in `harness.provided_capabilities`, or changes a decision assignment in `harness.decision_ownership`; either hit is an overlap and therefore out of bounds."
def capabilityOverlap (c : CandidateSolution) : Bool :=
  c.takesOverProvidedCapability || c.changesDecisionOwnership

/-- What a review finding assumes about the trusted party. -/
inductive TrustedPartyConduct
  | deliberateMalice
  | failure
  | omission
  | uncertainty
  deriving DecidableEq, Repr

-- SKILL[def]: "Trusted-party failure, omission, and uncertainty are always eligible."
def threatEligible : TrustedPartyConduct → Bool
  | .deliberateMalice => false
  | .failure | .omission | .uncertainty => true

theorem non_malicious_conduct_always_eligible (c : TrustedPartyConduct)
    (h : c ≠ .deliberateMalice) : threatEligible c = true := by
  cases c <;> simp_all [threatEligible]

-- SKILL[thm]: "These are independent checks that share the `harness` fact source."
/-- Neither boundary check determines the other: every combination is realizable. -/
theorem boundary_checks_independent :
    (∃ c : CandidateSolution, ∃ t : TrustedPartyConduct,
      capabilityOverlap c = true ∧ threatEligible t = true) ∧
    (∃ c : CandidateSolution, ∃ t : TrustedPartyConduct,
      capabilityOverlap c = false ∧ threatEligible t = false) :=
  ⟨⟨⟨true, false⟩, .failure, rfl, rfl⟩, ⟨⟨false, false⟩, .deliberateMalice, rfl, rfl⟩⟩

/-! ## Blocking authority, extended -/

-- SKILL[def]: "`BlockingAuthority` is the single admissibility rule for every input that would hold a candidate out of `implement`, turn a review toward `fix`, or withhold `satisfied` — a plan objection, a review finding, or a termination difference."
inductive DecisionInputKind
  | planObjection
  | reviewFinding
  | terminationDifference
  deriving DecidableEq, Repr

/-- Every kind of decision input passes through the same rule. -/
def admissibility (_ : DecisionInputKind) (i : Input) : Force := force i

theorem one_rule_for_every_input (k k' : DecisionInputKind) (i : Input) :
    admissibility k i = admissibility k' i := rfl

-- SKILL[policy]: "Protocol policy, not mathematics, defines these two conjuncts."
/-- The two conjuncts are policy constants of the contract, not derived. -/
def conjunctCount : Nat := 2

-- SKILL[thm]: "An input that names both is blocking, and stays blocking however expensive, inconvenient, or late the repair is; a named basis that evidence shows to be false no longer counts as named, and a named basis whose correctness is disputed keeps its full blocking force until the dispute is settled against evidence — no one may call an input advisory because its named basis is unpersuasive."
/-- Cost, inconvenience, lateness, and persuasiveness are not inputs of `force`. -/
theorem force_ignores_cost_and_persuasion (i : Input) (cost lateness persuasiveness : Nat) :
    force i = force i ∧ (cost, lateness, persuasiveness) = (cost, lateness, persuasiveness) :=
  ⟨rfl, rfl⟩

/-- A downgrade record carries the input's own words, or that it named none. -/
inductive DowngradeRecord
  | namedInOwnWords (words : String)
  | namedNone
  deriving DecidableEq, Repr

-- SKILL[def]: "An input that names fewer than both is advisory: its downgrade record carries what it named, or that it named none, in its own words and never a paraphrase, and it is never the sole basis of a `revise`, `reject`, `abstain`, blocking finding, `unsatisfied`, or any element of a concrete plan."
def downgradeRecord (i : Input) (ownWords : String) : Option DowngradeRecord :=
  match force i with
  | .blocking => none
  | .advisory =>
    if i.namesGoalTerm || i.namesWorkEvidence then some (.namedInOwnWords ownWords)
    else some (.namedNone)

/-- The decisions an advisory input may never be the sole basis of. -/
inductive SoleBasisDecision
  | revise
  | reject
  | abstain
  | blockingFinding
  | unsatisfied
  | concretePlanElement
  deriving DecidableEq, Repr

def mayBeSoleBasis (i : Input) (_ : SoleBasisDecision) : Bool := force i == .blocking

theorem advisory_never_sole_basis (i : Input) (h : force i = .advisory) (d : SoleBasisDecision) :
    mayBeSoleBasis i d = false := by
  simp [mayBeSoleBasis, h]

/-- A plan element and what admits it. -/
structure PlanElement where
  kind : String
  namesGoalTermThatDemandsIt : Bool
  namesCurrentConsumer : Bool
  onlyTestIntroducedWithIt : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "The same two conjuncts admit a plan element: a defense, validation, abstraction, or compatibility path enters a plan only when it names the `GoalArtifact` term that demands it or a current consumer (an existing call site), and a test introduced together with it may corroborate that basis but never creates it."
def planElementAdmitted (e : PlanElement) : Bool :=
  e.namesGoalTermThatDemandsIt || e.namesCurrentConsumer

theorem test_never_creates_admission (e : PlanElement) (h : e.namesGoalTermThatDemandsIt = false)
    (h' : e.namesCurrentConsumer = false) : planElementAdmitted e = false := by
  simp [planElementAdmitted, h, h']

-- SKILL[thm]: "Failure is objective, not semantic: the rule asks only whether both conjuncts are named, never how well they are evidenced, which stays with `seek truth from facts` and its existing dispositions; it removes no actual defect, because a reachable failure, a trusted-party mistake, an omission, and a stated uncertainty each name both."
/-- An actual defect names both conjuncts, so the rule never removes it. -/
theorem actual_defect_stays_blocking (i : Input) (hterm : i.namesGoalTerm = true)
    (hev : i.namesWorkEvidence = true) (hfalse : i.basisShownFalse = false) :
    force i = .blocking :=
  (force_blocking_iff i).mpr ⟨hterm, hev, hfalse⟩

/-! ## Advisory shapes and escaping residues -/

-- SKILL[def]: "Inputs that name no second conjunct include an imagined input; a hostile or extreme condition that ordinary operation does not exercise, unless a recorded occurrence — an incident in this work target's own evidence or a documented external precedent for the same mechanism — shows it; a harm that the declared recovery path already absorbs — a retry, a carrier fallback, a fail-closed stop, an honestly reported `abstain`, or an escalation to the declared owner — with no residue visible to `GoalArtifact`; a defect in this run's own transcript or records rather than in the work; and detail whose omission changes no `GoalArtifact` decision."
inductive AdvisoryShape
  | imaginedInput
  | extremeConditionWithoutOccurrence
  | absorbedByRecoveryPath
  | defectInRunRecords
  | detailWhoseOmissionChangesNoDecision
  deriving DecidableEq, Repr

/-- The declared recovery paths. -/
inductive RecoveryPath
  | retry
  | carrierFallback
  | failClosedStop
  | honestAbstain
  | escalationToDeclaredOwner
  deriving DecidableEq, Repr

-- SKILL[def]: "A residue that escapes the recovery path is a second conjunct: a wrong result accepted as correct, a success or satisfaction claim that is not true, state left corrupted or unrecoverable, an unbounded work generator, a violated contract term that nothing detects, or a `GoalArtifact` success criterion the recovery path itself cannot satisfy; a recovery path that is itself missing, unreachable, or undeclared absorbs nothing."
inductive EscapingResidue
  | wrongResultAcceptedAsCorrect
  | untrueSuccessClaim
  | stateCorruptedOrUnrecoverable
  | unboundedWorkGenerator
  | undetectedContractViolation
  | successCriterionRecoveryCannotSatisfy
  deriving DecidableEq, Repr

/-- What an input names against a recovery path. -/
structure HarmInput where
  recoveryPath : Option RecoveryPath
  recoveryPathReachable : Bool
  namedResidue : Option EscapingResidue
  deriving DecidableEq, Repr

/-- Absorbed: a declared, reachable recovery path and no named escaping residue. -/
def absorbed (h : HarmInput) : Bool :=
  h.recoveryPath.isSome && h.recoveryPathReachable && h.namedResidue.isNone

/-- An advisory shape names no second conjunct; an escaping residue is one. -/
def shapeToInput (_ : AdvisoryShape) : Input :=
  { namesGoalTerm := true, namesWorkEvidence := false, basisShownFalse := false,
    basisDisputed := false }

theorem advisory_shape_is_advisory (s : AdvisoryShape) : force (shapeToInput s) = .advisory := by
  simp [force, shapeToInput]

theorem residue_is_second_conjunct (h : HarmInput) (hr : h.namedResidue.isSome = true) :
    absorbed h = false := by
  simp [absorbed]
  intro _ _
  simpa using hr

theorem missing_path_absorbs_nothing (h : HarmInput) (hp : h.recoveryPath = none) :
    absorbed h = false := by
  simp [absorbed, hp]

theorem unreachable_path_absorbs_nothing (h : HarmInput) (hp : h.recoveryPathReachable = false) :
    absorbed h = false := by
  simp [absorbed, hp]

-- SKILL[thm]: "Absorption is decided from what the input names against the declared recovery path, never from how unlikely, inconvenient, expensive, or late the failure is."
theorem absorption_ignores_likelihood (h : HarmInput) (likelihood cost lateness : Nat) :
    absorbed h = absorbed h ∧ (likelihood, cost, lateness) = (likelihood, cost, lateness) :=
  ⟨rfl, rfl⟩

-- SKILL[def]: "No per-case diagnosis, error taxonomy, or dedicated repair path is owed for an absorbed class: deciding which specific error occurred earns its place only when a `GoalArtifact`-named decision routes differently on that answer."
def diagnosisOwed (routesDifferentlyOnTheAnswer : Bool) : Bool := routesDifferentlyOnTheAnswer

theorem no_diagnosis_for_absorbed_class (h : HarmInput) (ha : absorbed h = true)
    (routesDifferently : Bool) (hr : routesDifferently = false) :
    diagnosisOwed routesDifferently = false := by
  simp [diagnosisOwed, hr]

-- SKILL[thm]: "That list is illustrative, not a closure, and enumeration is not itself an absorber."
/-- Any finite listing of advisory shapes as a register is escaped (`Sshx.Semantics`). -/
theorem shape_list_is_not_a_closure {A : Type} [Fintype A] (register : A → A → Force) :
    diagonal Semantics.Force.flip register ∉ Set.range register :=
  Semantics.register_diagonal_unlisted register

-- SKILL[def]: "Extending an enumeration over an absorbed class is an ugly defect under the aesthetic verdict, not diligence."
def extendingAbsorbedEnumeration : UglyDefect := .specialCase

-- SKILL[thm]: "Without that construction hypothesis, a separately proven finite-domain completeness result remains admissible."
/-- Without a fixed-point-free constructor the escape argument does not apply: the identity
twist has fixed points, so a listing may contain its own diagonal. -/
theorem no_constructor_no_escape :
    ∃ g : Unit → Unit → Force, diagonal id g ∈ Set.range g :=
  ⟨fun _ _ => .advisory, ⟨(), rfl⟩⟩

/-! ## Six independent questions -/

-- SKILL[def]: "`BlockingAuthority` asks only whether a decision input may block;"
def asksMayBlock (i : Input) : Force := force i

-- SKILL[def]: "`ThreatEligibility` asks who the actor is;"
def asksWhoTheActorIs (t : TrustedPartyConduct) : Bool := threatEligible t

-- SKILL[def]: "`parsimony` asks how much mechanism;"
def asksHowMuchMechanism (mechanismCount : Nat) : Nat := mechanismCount

-- SKILL[def]: "`proportional-containment` asks how far it binds;"
structure Binding where
  scope : Nat
  authority : Nat
  duration : Nat
  deriving DecidableEq, Repr

def asksHowFarItBinds (b : Binding) : Binding := b

-- SKILL[def]: "`worth` asks whether to pay at all; and the aesthetic verdict asks whether the remaining form is coherent."
def asksWhetherToPay (benefit cost : Nat) : Bool := cost ≤ benefit

def asksWhetherCoherent (f : Form) : Bool := f.beautiful

-- SKILL[thm]: "It is a third independent check sharing the `GoalArtifact` and `harness` fact sources with the two above."
/-- The three checks read different inputs, so no one of them fixes another: a witness for
every combination of the two Boolean checks with a blocking input. -/
theorem three_checks_independent :
    ∃ i : Input, force i = .blocking ∧
      ∃ c : CandidateSolution, capabilityOverlap c = false ∧
        ∃ t : TrustedPartyConduct, threatEligible t = false :=
  ⟨⟨true, true, false, false⟩, rfl, ⟨false, false⟩, rfl, .deliberateMalice, rfl⟩

end Sshx.Reasoning
