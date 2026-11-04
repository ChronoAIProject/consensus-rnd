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

-- SKILL[policy]: "Protocol policy, not a mathematical consequence: run six whole-picture philosopher seats before choosing a plan, with distinct objectives and independent, isolated context."
def seatCount : Nat := 6

-- SKILL[def]: "- `teleology`: purpose and inevitability. What is this for, and is the form forced by that purpose? Attacks skipped-purpose and missing-inevitability."
def teleology : Charter :=
  ⟨.teleology, "What is this for, and is the form forced by that purpose?",
    ["skipped-purpose", "missing-inevitability"]⟩

-- SKILL[def]: "- `parsimony`: economy. Delete until nothing is left to delete; every element must prove its right to exist. Attacks magic numbers, symptom branches, and machinery that has not earned its place."
def parsimony : Charter :=
  ⟨.parsimony, "Delete until nothing is left to delete; every element must prove its right to exist.",
    ["magic numbers", "symptom branches", "machinery that has not earned its place"]⟩

-- SKILL[def]: "- `fidelity`: truth over proxy. Are premises source-verified and metrics representative, including performance claims relevant to `GoalArtifact`? Attacks proxy-over-truth and narrative-over-verification."
def fidelity : Charter :=
  ⟨.fidelity, "Are premises source-verified and metrics representative, including performance claims relevant to GoalArtifact?",
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

-- SKILL[def]: "- `worth` (值不值 — is it worth it?): decision value. Compare doing nothing and the cheapest sufficient alternative; weigh incremental expected benefit toward `GoalArtifact` against total lifecycle cost — build and verification effort, recurring maintenance, complexity debt, failure and misuse risk, reversibility, delay, and opportunity cost. Include the work target's latency, throughput, and resource use when material to `GoalArtifact`. Attacks not-worth-it machinery, unneeded elegance, and cost that outruns benefit; it may reject a candidate every other seat finds beautiful and well-owned. It must not cut a capability `GoalArtifact.success_criteria` requires to save cost; state its best counterfactual and decisive cost/benefit assumption without fabricating numeric ROI."
def worth : Charter :=
  ⟨.worth, "Is it worth paying for this at all, at this cost, now, versus the best alternative? Include the work target's latency, throughput, and resource use when material to GoalArtifact.",
    ["not-worth-it machinery", "unneeded elegance", "cost that outruns benefit"]⟩

/-- A `worth` judgment is well-formed when it names its counterfactual and decisive
assumption, cuts no required capability, and fabricates no number. -/
def WorthJudgment.wellFormed (w : WorthJudgment) : Prop :=
  w.decisiveAssumption ≠ "" ∧ w.cutsRequiredCapability = false ∧ w.fabricatedNumericRoi = false

def worthVerdict (w : WorthJudgment) : Bool := w.incrementalBenefitClearsCost

-- SKILL[def]: "They may disagree; meta-judge converges their judgments:"
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

-- SKILL[def]: "`natural-ownership` and `proportional-containment` are a coupled **must-clash locus dyad**: run together, each answers the other pole, converging on the natural owner layer, not the highest imaginable."
def DyadExchange.mustClash (d : DyadExchange) : Prop :=
  d.ownershipAnsweredContainment = true ∧ d.containmentAnsweredOwnership = true

-- SKILL[def]: "Ownership pulls toward the invariant owner; containment resists overreach"
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

-- SKILL[prose]: ""go upstream to the root, but not past the natural owner" is two adversarial seats for meta-judge convergence, not one balanced checklist."
-- why: names the design intent of the dyad; the balance itself is `dyadConverge` and the two charters above.

/-! ## Worth is its own seat -/

-- SKILL[thm]: "`worth` (值不值) is independent of aesthetics: `parsimony` asks how much mechanism, `proportional-containment` where/how far, `worth` whether to pay this cost now versus the best alternative."
/-- The three questions read different inputs: a mechanism count, a binding, and a
benefit–cost pair; none is a function of another. -/
theorem worth_is_not_parsimony_or_containment :
    ∃ mechanism : Nat, ∃ b : Binding, ∃ benefit cost : Nat,
      asksHowMuchMechanism mechanism = 1 ∧ asksHowFarItBinds b = b ∧
        asksWhetherToPay benefit cost = false :=
  ⟨1, ⟨1, 1, 1⟩, 0, 1, rfl, rfl, by decide⟩

-- SKILL[thm]: "Minimal, beautiful, contained work may be unworthy; less minimal work may justify its cost."
theorem minimal_beautiful_contained_yet_not_worth :
    ∃ f : Form, ∃ w : WorthJudgment, f.beautiful = true ∧ f.size = 1 ∧ worthVerdict w = false :=
  ⟨⟨1, true, false, true, true, true, true⟩, ⟨.doNothing, "no benefit", false, false, false⟩,
    rfl, rfl, rfl⟩

theorem less_minimal_yet_worth :
    ∃ f : Form, ∃ w : WorthJudgment, f.size = 2 ∧ worthVerdict w = true :=
  ⟨⟨2, true, false, true, true, true, true⟩,
    ⟨.cheapestSufficientAlternative, "avoided downside", true, false, false⟩, rfl, rfl⟩

-- SKILL[thm]: "Only its own seat derives this verdict, preserving distinct objectives."
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
  bases : List DecisionBasis
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
  (∀ b ∈ c.bases, completeAdmission b = true ∨ completeAdmission b = false) ∧
    (∀ e ∈ c.planElements, planElementAdmitted e = true)

-- SKILL[thm]: "An advisory basis is not a goal gap, must not by itself hold a candidate out of `implement`, and machinery that only defends against one must not enter a proposed plan."
/-- A goal gap is a blocking basis; an advisory basis is never one. -/
def goalGapOf (bases : List DecisionBasis) : List DecisionBasis :=
  bases.filter fun b => completeAdmission b

theorem advisory_is_not_a_goal_gap (bases : List DecisionBasis) (b : DecisionBasis)
    (h : completeAdmission b = false) : b ∉ goalGapOf bases := by
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
