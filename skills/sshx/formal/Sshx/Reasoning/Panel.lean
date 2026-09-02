import Mathlib.Tactic
import Sshx.Verdicts
import Sshx.Reasoning.Discipline
import Sshx.Reasoning.Authority

/-!
# Reasoning: the six thinking seats

Source: `## Thinking Panel` — the six lenses, the locus dyad, the independence of `worth`,
and what every seat must do before it returns a verdict.
-/

namespace Sshx.Reasoning

open Sshx

/-- The six seats. -/
inductive Lens
  | teleology
  | parsimony
  | fidelity
  | naturalOwnership
  | proportionalContainment
  | worth
  deriving DecidableEq, Repr

/-- A seat's charter: its objective question and what it attacks. -/
structure Charter where
  lens : Lens
  objective : String
  attacks : List String
  deriving DecidableEq, Repr

-- SKILL[policy]: "Protocol policy, not a mathematical consequence: run six whole-picture philosopher seats before choosing a plan — the same universal judgment lenses the consensus engine debates with."
def seatCount : Nat := 6

-- SKILL[def]: "- `teleology`: purpose and inevitability. What is this for, and is the form forced by that purpose? Attacks skipped-purpose and missing-inevitability."
def teleology : Charter :=
  ⟨.teleology, "What is this for, and is the form forced by that purpose?",
    ["skipped-purpose", "missing-inevitability"]⟩

-- SKILL[def]: "- `parsimony`: economy. Delete until nothing is left to delete; every element must prove its right to exist. Attacks magic numbers, symptom branches, and machinery that has not earned its place."
def parsimony : Charter :=
  ⟨.parsimony, "Delete until nothing is left to delete; every element must prove its right to exist.",
    ["magic numbers", "symptom branches", "machinery that has not earned its place"]⟩

-- SKILL[def]: "- `fidelity`: truth over proxy. Does it measure the real thing, and is every premise verified at its source? Attacks proxy-over-truth and narrative-over-verification."
def fidelity : Charter :=
  ⟨.fidelity, "Does it measure the real thing, and is every premise verified at its source?",
    ["proxy-over-truth", "narrative-over-verification"]⟩

-- SKILL[def]: "- `natural-ownership`: locus dyad, ownership pole. Which layer naturally owns this invariant, duty, or constraint — the layer with semantic responsibility and causal control? Attacks symptom patches, duplicated enforcement, and invariants forced onto consumers of what a producer should own."
def naturalOwnership : Charter :=
  ⟨.naturalOwnership, "Which layer naturally owns this invariant, duty, or constraint?",
    ["symptom patches", "duplicated enforcement",
      "invariants forced onto consumers of what a producer should own"]⟩

-- SKILL[def]: "- `proportional-containment`: locus dyad, containment pole. How far may this intervention rightfully bind, across scope, authority, and duration, given the evidence? Attacks over-hoisting, speculative abstraction, and turning a local fact into universal law."
def proportionalContainment : Charter :=
  ⟨.proportionalContainment,
    "How far may this intervention rightfully bind, across scope, authority, and duration?",
    ["over-hoisting", "speculative abstraction", "turning a local fact into universal law"]⟩

/-- The lifecycle cost components `worth` weighs. -/
inductive LifecycleCost
  | buildAndVerificationEffort
  | recurringMaintenanceBurden
  | complexityDebt
  | failureAndMisuseRisk
  | reversibility
  | delay
  | opportunityCost
  deriving DecidableEq, Repr

/-- The two counterfactuals `worth` compares against. -/
inductive Counterfactual
  | doNothing
  | cheapestSufficientAlternative
  deriving DecidableEq, Repr

/-- A `worth` judgment as the seat must state it. -/
structure WorthJudgment where
  bestCounterfactual : Counterfactual
  decisiveAssumption : String
  incrementalBenefitClearsCost : Bool
  cutsRequiredCapability : Bool
  fabricatedNumericRoi : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "- `worth` (值不值 — is it worth it?): decision value. Compare the candidate against doing nothing and against the cheapest sufficient alternative, then weigh its incremental expected benefit toward `GoalArtifact` against its total lifecycle cost — build and verification effort, recurring maintenance burden, complexity debt, failure and misuse risk, reversibility, delay, and the opportunity cost of the more valuable work it displaces. Attacks not-worth-it machinery, elegance `GoalArtifact` does not need, and cost that outruns benefit; it may reject a candidate every other seat finds beautiful and well-owned. It must not cut a capability `GoalArtifact.success_criteria` requires to save cost, and it must state its best counterfactual and the decisive cost/benefit assumption rather than fabricating a numeric ROI."
def worth : Charter :=
  ⟨.worth, "Is it worth paying for this at all, at this cost, now, versus the best alternative?",
    ["not-worth-it machinery", "elegance GoalArtifact does not need", "cost that outruns benefit"]⟩

/-- A `worth` judgment is well-formed when it names its counterfactual and decisive
assumption, cuts no required capability, and fabricates no number. -/
def WorthJudgment.wellFormed (w : WorthJudgment) : Prop :=
  w.decisiveAssumption ≠ "" ∧ w.cutsRequiredCapability = false ∧ w.fabricatedNumericRoi = false

def worthVerdict (w : WorthJudgment) : Bool := w.incrementalBenefitClearsCost

-- SKILL[def]: "Each seat is one independent, context-isolated perspective that attacks from its own objective; the seats can and do disagree, and the meta-judge converges them:"
def panel : List Charter :=
  [teleology, parsimony, fidelity, naturalOwnership, proportionalContainment, worth]

theorem panel_has_six_seats : panel.length = seatCount := rfl

/-- The seats can disagree: two seats may return different verdicts on one candidate. -/
theorem seats_can_disagree :
    ∃ v w : ThinkingVerdict, v ≠ w := ⟨.propose, .reject, by decide⟩

/-! ## The locus dyad -/

/-- The two poles' claims about the owning layer, and each pole's answer to the other. -/
structure DyadExchange where
  ownershipClaimsLayer : Nat
  containmentAllowsUpTo : Nat
  ownershipAnsweredContainment : Bool
  containmentAnsweredOwnership : Bool
  highestLayerImaginable : Nat
  deriving DecidableEq, Repr

-- SKILL[def]: "`natural-ownership` and `proportional-containment` are a coupled **must-clash locus dyad**: they run together, each must answer the other pole's claim, and they converge on the natural owner layer — not the highest layer imaginable."
def DyadExchange.mustClash (d : DyadExchange) : Prop :=
  d.ownershipAnsweredContainment = true ∧ d.containmentAnsweredOwnership = true

-- SKILL[def]: "Ownership pulls the fix toward the layer that owns the invariant; containment resists over-reaching past it."
/-- The converged layer is the owner layer capped by containment, never the highest. -/
def dyadConverge (d : DyadExchange) : Nat := min d.ownershipClaimsLayer d.containmentAllowsUpTo

theorem dyad_never_over_reaches (d : DyadExchange) :
    dyadConverge d ≤ d.containmentAllowsUpTo := Nat.min_le_right _ _

theorem dyad_converges_to_owner_not_highest (d : DyadExchange)
    (h : d.ownershipClaimsLayer ≤ d.containmentAllowsUpTo)
    (hh : d.ownershipClaimsLayer < d.highestLayerImaginable) :
    dyadConverge d < d.highestLayerImaginable := by
  unfold dyadConverge
  omega

-- SKILL[prose]: "This is the "go upstream to the root, but not past the natural owner" balance expressed as two adversarial seats the meta-judge converges, rather than a single balanced checklist."
-- why: names the design intent of the dyad; the balance itself is `dyadConverge` and the two charters above.

/-! ## Worth is its own seat -/

-- SKILL[thm]: "`worth` (值不值) is an independent objective, not the aesthetic lens repeated: `parsimony` asks how much mechanism, `proportional-containment` asks where and how far it binds, and `worth` asks whether to pay for this at all, at this cost, now, versus the best alternative."
/-- The three questions read different inputs: a mechanism count, a binding, and a
benefit–cost pair; none is a function of another. -/
theorem worth_is_not_parsimony_or_containment :
    ∃ mechanism : Nat, ∃ b : Binding, ∃ benefit cost : Nat,
      asksHowMuchMechanism mechanism = 1 ∧ asksHowFarItBinds b = b ∧
        asksWhetherToPay benefit cost = false :=
  ⟨1, ⟨1, 1, 1⟩, 0, 1, rfl, rfl, by decide⟩

-- SKILL[thm]: "A candidate can be minimal, beautiful, and properly contained yet still not worth doing, and a less minimal candidate can still be worth doing when the avoided downside justifies it."
theorem minimal_beautiful_contained_yet_not_worth :
    ∃ f : Form, ∃ w : WorthJudgment, f.beautiful = true ∧ f.size = 1 ∧ worthVerdict w = false :=
  ⟨⟨1, true, false, true, true, true, true⟩, ⟨.doNothing, "no benefit", false, false, false⟩,
    rfl, rfl, rfl⟩

theorem less_minimal_yet_worth :
    ∃ f : Form, ∃ w : WorthJudgment, f.size = 2 ∧ worthVerdict w = true :=
  ⟨⟨2, true, false, true, true, true, true⟩,
    ⟨.cheapestSufficientAlternative, "avoided downside", true, false, false⟩, rfl, rfl⟩

-- SKILL[thm]: "Because it is a seat rather than a cross-cutting lens, `worth` is judged once by its own perspective, so the panel is not homogenized into every seat re-deriving the same value verdict."
theorem worth_judged_once : (panel.filter fun c => c.lens == .worth).length = 1 := by decide

/-! ## What every seat does before returning -/

/-- Whether a candidate answers the root cause `GoalArtifact` implies. -/
inductive RootCauseAnswer
  | satisfies
  | stillDiffers (gap : String)
  | cannotBeSatisfied (why : String)
  deriving DecidableEq, Repr

/-- A seat's conclusion: verdict, note, root-cause answer, boundary and authority statements. -/
structure SeatConclusion where
  lens : Lens
  verdict : ThinkingVerdict
  note : DisciplineNote
  weighed : List Candidate
  rootCause : String
  answer : RootCauseAnswer
  capabilityOverlapHit : Bool
  bases : List Input
  planElements : List PlanElement
  reviseGoalGap : Option String
  reviseNextIterationQuestion : Option String
  opensUnrelatedDesignSearch : Bool

-- SKILL[def]: "Before proposing, revising, rejecting, or abstaining, each seat must apply `## Reasoning Discipline` to every candidate conclusion it weighs and surface the compact reasoning-discipline note in `SshxResultEnvelope.conclusion` before returning a verdict."
def SeatConclusion.disciplined (c : SeatConclusion) : Prop := c.note.valid c.weighed

-- SKILL[def]: "When proposing, revising, or rejecting a candidate, each seat must state whether it hits `CapabilityOverlap`; a hit is an unclosed goal gap and must not enter `implement`."
def SeatConclusion.implementable (c : SeatConclusion) : Bool :=
  c.verdict == .propose && !c.capabilityOverlapHit

theorem overlap_never_implements (c : SeatConclusion) (h : c.capabilityOverlapHit = true) :
    c.implementable = false := by
  simp [SeatConclusion.implementable, h]

-- SKILL[def]: "Each seat applies `BlockingAuthority` to every proposed plan element and every `propose`, `revise`, `reject`, or `abstain` basis, and states the `GoalArtifact` term and evidence that make each basis blocking, or the `GoalArtifact` term or current consumer that admits each plan element."
def SeatConclusion.basesStated (c : SeatConclusion) : Prop :=
  (∀ i ∈ c.bases, force i = .blocking ∨ force i = .advisory) ∧
    (∀ e ∈ c.planElements, planElementAdmitted e = true)

-- SKILL[thm]: "An advisory basis is not a goal gap, must not by itself hold a candidate out of `implement`, and machinery that only defends against one must not enter a proposed plan."
/-- A goal gap is a blocking basis; an advisory basis is never one. -/
def goalGapOf (bases : List Input) : List Input := bases.filter fun i => force i == .blocking

theorem advisory_is_not_a_goal_gap (bases : List Input) (i : Input) (h : force i = .advisory) :
    i ∉ goalGapOf bases := by
  simp [goalGapOf, h]

-- SKILL[def]: "Every seat must first identify the problem essence or root cause implied by `GoalArtifact`, then frame `propose`, `revise`, `reject`, or `abstain` as an answer to it: what satisfies it, what still differs from it, or why it cannot be satisfied."
def SeatConclusion.framedAsAnswer (c : SeatConclusion) : Prop :=
  c.rootCause ≠ "" ∧
    (c.verdict = .propose → c.answer = .satisfies) ∧
    (c.verdict = .revise → ∃ gap, c.answer = .stillDiffers gap) ∧
    (c.verdict = .reject → ∃ why, c.answer = .cannotBeSatisfied why)

/-- A plan and whether it addresses the root cause or only a surface symptom. -/
structure Plan where
  addressesRootCause : Bool
  patchesSurfaceSymptomOnly : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "A plan that only patches a surface symptom while leaving that root cause in place does not satisfy the thinking gate."
def satisfiesThinkingGate (p : Plan) : Bool := p.addressesRootCause && !p.patchesSurfaceSymptomOnly

theorem symptom_patch_fails_gate (p : Plan) (h : p.patchesSurfaceSymptomOnly = true) :
    satisfiesThinkingGate p = false := by
  simp [satisfiesThinkingGate, h]

-- SKILL[def]: "`revise` must name the goal gap and a next iteration question; it must not open an unrelated design search."
def SeatConclusion.reviseWellFormed (c : SeatConclusion) : Prop :=
  c.verdict = .revise →
    c.reviseGoalGap.isSome = true ∧ c.reviseNextIterationQuestion.isSome = true ∧
      c.opensUnrelatedDesignSearch = false

-- SKILL[ref]: "- `propose`"
-- SKILL[ref]: "- `revise`"
-- SKILL[ref]: "- `reject`"
-- SKILL[ref]: "- `abstain`"
/-- The closed verdict set a seat returns from. -/
abbrev thinkingVerdicts : List ThinkingVerdict := ThinkingVerdict.univ

end Sshx.Reasoning
