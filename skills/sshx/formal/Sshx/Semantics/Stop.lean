import D5.S3.ConceptDynamics.DefinitionEscapeAdjudication.AdjudicationStopTargetCorrectness
import D5.S3.ConceptDynamics.DefinitionEscapeAdjudication.ParetoFrontierStopDivergence
import D5.S3.ConceptDynamics.DefinitionEscapeAdjudication.ParetoLinearExtensionStopRefutation
import D5.S3.ConceptDynamics.DefinitionEscapeAdjudication.SettleStopInputConservation

/-!
# Semantics: the termination claim is an oriented stop on a sealed decision set

Source: `## Termination Gate` (sealed stop inputs) and `## Design Truth Table`
(Pareto frontier and linear extension are not stop rules).
Instances of the adjudication stop theorems in
`D5.S3.ConceptDynamics.DefinitionEscapeAdjudication`.

The caller's sealed inputs are a `DecisionSet` (current affirmative candidate, feasible set)
and an `OrientationSpec` (owner-sourced, versioned, scoped preorder on the admissible target).
The affirmative claim is `AdjudicationStopTargetOnDecisionSet`, decided by `stopCheck`, which
reads nothing else: late narrative and logs are not among its inputs by type.
-/

namespace Sshx.Semantics

open D5.S3.ConceptDynamics.DefinitionEscape.Adjudication

universe u

variable {Goal Action Source Version Scope : Type u} [DecidableEq Action]
  (AdmTarget : Goal → Set Action) (InScope : Scope → Action → Prop)

-- SKILL: "seal the current affirmative candidate, the feasible termination decision set, and an owner-sourced, versioned, scoped orientation"
/-- The termination claim over sealed inputs. -/
def terminationClaim
    (O : OrientationSpec Goal Action Source Version Scope AdmTarget InScope)
    (D : DecisionSet Action) : Prop :=
  AdjudicationStopTargetOnDecisionSet AdmTarget InScope O D

-- SKILL: "A missing current candidate, an empty feasible set, or a current candidate outside the feasible set fails closed"
theorem no_claim_without_candidate
    (O : OrientationSpec Goal Action Source Version Scope AdmTarget InScope)
    (D : DecisionSet Action) (h : D.current = none) :
    ¬ terminationClaim AdmTarget InScope O D := by
  rintro ⟨current, hcur, -⟩
  simp [h] at hcur

theorem no_claim_with_empty_feasible
    (O : OrientationSpec Goal Action Source Version Scope AdmTarget InScope)
    (D : DecisionSet Action) (h : D.feasible = ∅) :
    ¬ terminationClaim AdmTarget InScope O D := by
  rintro ⟨current, -, hmem, -⟩
  simp [h] at hmem

theorem no_claim_outside_feasible
    (O : OrientationSpec Goal Action Source Version Scope AdmTarget InScope)
    (D : DecisionSet Action) (a : Action) (hcur : D.current = some a)
    (hout : a ∉ D.feasible) : ¬ terminationClaim AdmTarget InScope O D := by
  rintro ⟨current, hcurrent, hmem, -⟩
  rw [hcur] at hcurrent
  cases hcurrent
  exact hout hmem

-- SKILL: "late narrative and logs are not stop inputs"
/-- `stopCheck` decides the claim from the orientation and the decision set alone
(`adjudication_stop_target_correctness`, third conjunct); equal sealed inputs give equal
verdicts (`settle_stop_depends_only_on_decision_and_orientation` at the decision-set level). -/
theorem claim_reads_only_sealed_inputs
    (O O' : OrientationSpec Goal Action Source Version Scope AdmTarget InScope)
    (D D' : DecisionSet Action)
    [∀ a, Decidable (a ∈ AdmTarget O.goal)] [∀ a, Decidable (InScope O.scope a)]
    [∀ a b, Decidable (O.relation a b)]
    [∀ a, Decidable (a ∈ AdmTarget O'.goal)] [∀ a, Decidable (InScope O'.scope a)]
    [∀ a b, Decidable (O'.relation a b)]
    (hO : O = O') (hD : D = D') :
    stopCheck AdmTarget InScope O D = stopCheck AdmTarget InScope O' D' := by
  subst hO
  subst hD
  congr

-- SKILL: "neither a Pareto frontier nor a linear extension is itself a stop rule"
/-- One Pareto frontier yields opposite stops under two sourced orientations
(`pareto_frontier_requires_sourced_orientation`): the frontier alone decides nothing. -/
theorem frontier_is_not_a_stop_rule :
    (NoDominatingCandidate valueTwo decisionTwo ∧
      AdjudicationStopTargetOnDecisionSet
        admissibleTargetTwo inScopeTwo stayOrientation decisionTwo ∧
      ¬ AdjudicationStopTargetOnDecisionSet
        admissibleTargetTwo inScopeTwo advanceOrientation decisionTwo) ∧
    ¬ (NoDominatingCandidate valueTwo decisionTwo →
      AdjudicationStopTargetOnDecisionSet
        admissibleTargetTwo inScopeTwo advanceOrientation decisionTwo) :=
  pareto_frontier_requires_sourced_orientation

/-- A sourced two-action Pareto model refutes both linear-extension stop equivalences
(`op5_pareto_stop_linear_extension_equivalences_refuted`): no linear extension of the
frontier is a stop rule either. -/
alias linear_extension_is_not_a_stop_rule := op5_pareto_stop_linear_extension_equivalences_refuted

end Sshx.Semantics
