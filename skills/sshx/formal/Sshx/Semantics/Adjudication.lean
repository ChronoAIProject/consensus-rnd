import D5.S3.ConceptDynamics.DefinitionEscapeAdjudication.DependencyClosureAdmissionAntitone
import D5.S3.ConceptDynamics.DefinitionEscapeAdjudication.TargetChangeSettlementConservation
import D5.S3.ConceptDynamics.DefinitionEscapeAdjudication.ParetoWeakPreorder
import D5.S3.ConceptDynamics.DefinitionEscapeAdjudication.GainDifferenceCocycle
import D5.S3.ConceptDynamics.DefinitionEscapeAdjudication.RetrospectiveLookupFailure
import Sshx.Records

/-!
# Semantics: adjudication laws

Source: `## Reasoning Discipline` (independent adjudication evidence, retrospective fit),
`## Goal Contract` (revisions), `## Design Truth Table` (comparison coordinates).
Instances of the kernel-frozen adjudication theorems in
`D5.S3.ConceptDynamics.DefinitionEscapeAdjudication`.
-/

namespace Sshx.Semantics

open D5.S3.ConceptDynamics.DefinitionEscape.Adjudication

universe u v

/-! ## Dependency closure -/

-- SKILL[thm]: "Enlarging that closure may only remove admission, never restore it."
/-- Enlarging the candidate's dependency closure can only remove admissible adjudication
evidence (`dependency_closure_admission_antitone`). -/
theorem enlarging_closure_only_removes_admission
    {Evidence : Type u} {Artifact : Type v}
    (context : AdmissionContext Evidence Artifact)
    {small large : Set Artifact} (included : small ⊆ large) (record : Evidence) :
    AdmissibleJudge context large record → AdmissibleJudge context small record :=
  dependency_closure_admission_antitone context included record

-- SKILL[thm]: "inadmissible when its recorded use to generate, tune, or select reaches the candidate's dependency closure"
/-- A record with a recorded generate, tune, or select use touching the closure is not an
admissible judge. -/
theorem adaptive_use_is_inadmissible
    {Evidence : Type u} {Artifact : Type v}
    (context : AdmissionContext Evidence Artifact) (closure : Set Artifact)
    (record : Evidence) (used : AdaptiveUse context closure record) :
    ¬ AdmissibleJudge context closure record :=
  fun admissible => admissible.2.2.2 used

/-! ## Append-only settlement -/

-- SKILL[thm]: "Revisions never rewrite an earlier target or recompute an earlier settlement."
/-- Appending revisions cannot change the settlement of an earlier round
(`append_only_old_settlement_unchanged`). -/
theorem revision_keeps_old_settlement
    {Target Commitment Evidence Verdict : Type u}
    (evaluate : Commitment → Evidence → Verdict)
    (old new : List (RoundRecord Target Commitment Evidence))
    (round : Nat) (appendOnly : AppendOnly old new) (oldRound : round < old.length) :
    settleAt evaluate new round = settleAt evaluate old round :=
  append_only_old_settlement_unchanged evaluate old new round appendOnly oldRound

/-- Every `GoalArtifact` correction is an append-only ledger step. -/
theorem correction_is_append_only
    {Commitment Evidence : Type}
    (ledger : List (RoundRecord Sshx.GoalArtifact Commitment Evidence))
    (next : RoundRecord Sshx.GoalArtifact Commitment Evidence) :
    AppendOnly ledger (ledger ++ [next]) :=
  ⟨[next], rfl⟩

/-! ## Comparison coordinates -/

-- SKILL[thm]: "Material comparison coordinates form a product preorder"
/-- Weak Pareto dominance over the declared coordinates is reflexive and transitive
(`pareto_weak_reflexive_transitive`); it is a preorder, not a stop rule. -/
theorem candidate_dominance_is_preorder
    {Action Information Residual Transfer Cost Risk : Type u}
    [Preorder Information] [Preorder Residual] [Preorder Transfer]
    [Preorder Cost] [Preorder Risk]
    (value : Action → GainVector Information Residual Transfer Cost Risk) :
    (∀ a, ParetoWeak value a a) ∧
      (∀ ⦃a b c⦄, ParetoWeak value a b → ParetoWeak value b c → ParetoWeak value a c) :=
  pareto_weak_reflexive_transitive value

-- SKILL[thm]: "Path-summed gain reconciliation applies only to coordinates with declared additive structure"
/-- On coordinates with additive structure, gain differences are path independent
(`gain_difference_self_zero_and_cocycle`). -/
theorem additive_gain_is_path_independent
    {Action Information Residual Transfer Cost Risk : Type u}
    [AddGroup Information] [AddGroup Residual] [AddGroup Transfer]
    [AddGroup Cost] [AddGroup Risk]
    (value : Action → GainVector Information Residual Transfer Cost Risk) :
    (∀ a, gainDifference value a a = 0) ∧
      (∀ a b c, gainDifference value a c =
        gainDifference value a b + gainDifference value b c) :=
  gain_difference_self_zero_and_cocycle value

/-! ## Retrospective fit -/

-- SKILL[thm]: "Retrospective fit is not prospective evidence"
/-- A rationale that only replays the facts in its visible inputs is a lookup copy: zero
retrospective loss, every record dependency-contaminated, and no prospective gain implied
(`lookup_copy_zero_loss_and_nonanticipating_failure`). -/
theorem replayed_facts_carry_no_prospective_weight
    {Z Answer : Type u} [Fintype Z] [DecidableEq Z]
    (comparison : CopyComparison Z Answer) (commitment : CopyCommitment Z)
    (usesCopy : IncorporatesTableCopy commitment) :
    retrospectiveLoss comparison (tableCopy comparison) = 0 ∧
      (∀ z, ¬ NonAnticipating commitment z) ∧
      ¬ (retrospectiveLoss comparison (tableCopy comparison) = 0 →
        ∀ prospectiveGain : (Z → Answer) → Nat,
          PositiveProspectiveGain prospectiveGain (tableCopy comparison)) :=
  lookup_copy_zero_loss_and_nonanticipating_failure comparison commitment usesCopy

end Sshx.Semantics
