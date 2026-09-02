import D5.S3.ConceptDynamics.EscapeSpectrum.BudgetEnvelopeCompletion
import Sshx.Budget

/-!
# Semantics: the goal gap is a definition-escape residual

Source: `## Goal Contract` (`iteration_question`), `## Fix Or Done` (repeated passes),
`## Reasoning Discipline` (enumeration is not an absorber).
Instance of DECT's budget envelope theorem
(`D5.S3.ConceptDynamics.EscapeSpectrum.BudgetEnvelopeCompletion`).

The written `GoalArtifact` is the current concept `q`; the user's intent is the target `T`;
the goal gap is the defect relation `E(q;T)`: outcome pairs the written goal conflates but
the intent distinguishes. A blocking finding is a candidate definition joined to `q`; a
countable language of such checks, each costing one pass unit, is the family `Γ`. Under any
monotone escape weight with a positive baseline, the best residual reachable with `pass_budget`
units is antitone in the budget and never drops below the infimum over all finite selections
from the same language: more passes on the same approach never beat that floor.
-/

namespace Sshx.Semantics

open D5.S3.ConceptDynamics.ConceptFiberDecomposition
open D5.S3.ConceptDynamics.TargetRisk.RefinementRiskCostTradeoff
open D5.S3.ConceptDynamics.EscapeSpectrum.BudgetEnvelopeCompletion
open D5.S3.AnalyticClosure.Budget.BudgetedEscapeRateAntitone

variable {Outcome Intent Written : Type*}

-- SKILL[def]: "`iteration_question` must ask what still differs from `GoalArtifact`"
/-- The goal gap: pairs of outcomes the written goal cannot tell apart but the intent does. -/
def goalGap (written : Concept Outcome Written) (intent : Concept Outcome Intent) :
    Set (Outcome × Outcome) :=
  defectRelation written intent

/-- Every check costs exactly one `pass_budget` unit. -/
def checkCost : ℕ → Real := fun _ => 1

-- SKILL[thm]: "rather than respending `pass_budget` on an unchanged approach"
/-- With a fixed language of checks, the best residual gap affordable at a budget is antitone
in the budget and bounded below by the infimum over all finite selections from that language.
Only a different language — a changed approach — can go below that floor. -/
theorem goal_gap_budget_envelope
    (written : Concept Outcome Written) (intent : Concept Outcome Intent)
    (checks : ℕ → Concept Outcome Bool)
    (weight : EscapeWeight (Outcome × Outcome))
    (baselinePositive : 0 < weight.mass (goalGap written intent))
    (massMonotone : Monotone weight.mass) :
    Antitone
        (finiteBudgetEnvelope (Set.univ : Set ℕ) (fun i => checks i) written intent
          checkCost weight) ∧
      ∀ budget : NNReal,
        allFiniteResidualInfimum (Set.univ : Set ℕ) (fun i => checks i) written intent
            weight ≤
          finiteBudgetEnvelope (Set.univ : Set ℕ) (fun i => checks i) written intent
            checkCost weight budget := by
  have h := budget_envelope_infimum_and_limit (Set.univ : Set ℕ) (fun i => checks i)
    written intent checkCost weight baselinePositive massMonotone
  obtain ⟨hanti, hbounds, hinf, _, _, _⟩ := h
  refine ⟨hanti, fun budget => ?_⟩
  rw [← hinf]
  refine csInf_le ⟨0, ?_⟩ (Set.mem_range_self budget)
  rintro _ ⟨b, rfl⟩
  exact (hbounds b).1

end Sshx.Semantics
