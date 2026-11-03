import Sshx.Verdicts

/-!
# `BlockingAuthority`, `ThreatEligibility`, and the review downgrade

Source: `## Reasoning Discipline` (`BlockingAuthority`, `ThreatEligibility`) and the
downgrade sentence of `## Review Truth Table`.
-/

namespace Sshx

-- SKILL[def]: "Admission additionally requires `evidence_status=confirmed` and a sourced `user_goal_basis`"
inductive EvidenceStatus
  | confirmed
  | advisory
  | conditional
  | unverified
  deriving DecidableEq, Repr

/-- What a decision input names, as `BlockingAuthority` reads it. -/
structure Input where
  /-- Conjunct 1: a `normalized_goal`, `constraints`, or `success_criteria` term the work fails. -/
  namesGoalTerm : Bool
  /-- Conjunct 2: evidence in the work as built, or the absence of evidence a term demands. -/
  namesWorkEvidence : Bool
  /-- A named basis that evidence shows to be false no longer counts as named. -/
  basisShownFalse : Bool
  /-- A disputed basis is not an absent basis. -/
  basisDisputed : Bool
  evidenceStatus : EvidenceStatus := .unverified
  userGoalBasis : String := ""
  deriving DecidableEq, Repr

inductive Force
  | blocking
  | advisory
  deriving DecidableEq, Repr

inductive TriggerPath
  | ordinaryOperation
  | nonstandardDeliberate
  | malicious
  deriving DecidableEq, Repr

/-- Projection of a read-only measurement of workflow inputs, not a review fixture.
Truth of provenance, scope and the workflow correspondence remains a supplied premise. -/
structure EnvironmentObservation where
  command : String
  scope : String
  count : Nat
  actualWorkflowInput : Bool
  deriving DecidableEq, Repr

def EnvironmentObservation.establishesInput (o : EnvironmentObservation) : Bool :=
  o.command != "" && o.scope != "" && decide (0 < o.count) && o.actualWorkflowInput

/-- A manual's capability statement is not a workflow derivation. The semantic
classification and owner source are interpreted evidence, not inferred from English. -/
-- SKILL[def]: "A tool manual or review fixture alone proves capability/reachability, never ordinary workflow input or occurrence."
-- SKILL[ref]: "Missing/disputed provenance is an evidence gap, never an invented restriction."
structure WorkflowInputEvidence where
  observation : Option EnvironmentObservation
  workflowSource : String
  workflowDerived : Bool
  deriving DecidableEq, Repr

def WorkflowInputEvidence.observed (e : WorkflowInputEvidence) : Bool :=
  e.observation.any (·.establishesInput)

def WorkflowInputEvidence.ordinary (e : WorkflowInputEvidence) : Bool :=
  e.observed || (e.workflowSource != "" && e.workflowDerived)

inductive OwnerAdmissionPolicy
  | unrestricted
  | actualInputOrOccurrence (source : { s : String // s ≠ "" })
  deriving DecidableEq, Repr

def OwnerAdmissionPolicy.admits (p : OwnerAdmissionPolicy) (observed occurrence : Bool) : Bool :=
  match p with
  | .unrestricted => true
  | .actualInputOrOccurrence _ => observed || occurrence

/-- Structured projection of family_basis; semantic correspondence is judged from
settled summaries by the existing class gate, never by the label string. -/
structure FamilyBasis where
  failedProperty : String
  causalMechanism : String
  correspondenceEvidence : String
  deriving DecidableEq, Repr

structure TriggerRecord where
  trigger : String
  triggerActor : String
  triggerPath : TriggerPath
  mechanismFamily : String
  recordedOccurrence : Option String
  createdDuringRun : Bool
  inputEvidence : WorkflowInputEvidence
  ownerPolicy : OwnerAdmissionPolicy
  familyBasis : FamilyBasis
  deriving DecidableEq, Repr

-- SKILL[def]: "`recorded_occurrence` must have existed before this run"
def occurrenceIsIndependent (recordedOccurrence : Option String) (createdDuringRun : Bool) : Bool :=
  recordedOccurrence.isSome && !createdDuringRun

-- SKILL[def]: "Without workflow grounding, require independent `recorded_occurrence`, else advisory."
-- SKILL[ref]: "Apply sourced owner restrictions from `GoalArtifact`: actual-input-or-occurrence-only additionally requires observed actual input or independent occurrence; absent that rule, future ordinary workflow-produced inputs remain eligible."
-- SKILL[def]: "A blocking finding must also pass the structured trigger check"
def triggerCheck (record : TriggerRecord) : Bool :=
  let occurrence := occurrenceIsIndependent record.recordedOccurrence record.createdDuringRun
  let ordinary := record.triggerPath == .ordinaryOperation && record.inputEvidence.ordinary
  (ordinary || occurrence) && record.ownerPolicy.admits record.inputEvidence.observed occurrence

-- SKILL[thm]: "deliberately constructed run fixtures/reproductions/states are reachability evidence only"
theorem run_fixture_is_not_occurrence (createdDuringRun : Bool) :
    occurrenceIsIndependent none createdDuringRun = false := by
  simp [occurrenceIsIndependent]

-- SKILL[def]: "Advisory is the default; blocking is the exception, with exactly two conjuncts the input itself must name"
/-- `BlockingAuthority`: advisory is the default; blocking needs both named conjuncts. -/
def force (i : Input) : Force :=
  if i.namesGoalTerm && i.namesWorkEvidence && !i.basisShownFalse &&
      i.evidenceStatus == .confirmed && i.userGoalBasis != "" then .blocking else .advisory

/-- A decision basis keeps the trigger provenance beside the two conjuncts.  Any
blocking consumer must use this projection rather than a bare `Input`. -/
structure DecisionBasis where
  input : Input
  trigger : TriggerRecord
  deriving DecidableEq, Repr

-- SKILL[ref]: "workflow grounding and owner-policy compliance remain trigger-check premises"
def completeAdmission (b : DecisionBasis) : Bool :=
  force b.input == .blocking && triggerCheck b.trigger

theorem completeAdmission_requires_force (b : DecisionBasis)
    (h : completeAdmission b = true) : force b.input = .blocking := by
  simp [completeAdmission] at h
  exact h.1

theorem failed_trigger_is_not_admitted (b : DecisionBasis)
    (h : triggerCheck b.trigger = false) : completeAdmission b = false := by
  simp [completeAdmission, h]

theorem force_blocking_iff (i : Input) :
    force i = .blocking ↔
      i.namesGoalTerm = true ∧ i.namesWorkEvidence = true ∧ i.basisShownFalse = false ∧
        i.evidenceStatus = .confirmed ∧ i.userGoalBasis ≠ "" := by
  simp [force, Bool.and_eq_true, bne_iff_ne]

-- SKILL[ref]: "Advisory/conditional/unverified findings create no blockers or automatic repairs"
theorem unconfirmed_is_advisory (i : Input) (h : i.evidenceStatus ≠ .confirmed) :
    force i = .advisory := by
  simp [force, h]

/-- Disputed grounding is not absent grounding: the dispute flag never changes the force. -/
-- SKILL[ref]: "Evidence showing a basis false removes it; a confirmed basis with disputed correctness retains full force until evidence settles it, never downgrade for being unpersuasive."
-- SKILL[ref]: "Dispute preserves confirmed defects; unsupported premises are not confirmed."
theorem force_ignores_dispute (i : Input) (b : Bool) :
    force { i with basisDisputed := b } = force i := by
  simp [force]

/-- One missing conjunct is advisory, however the rest looks. -/
theorem advisory_of_no_goal_term (i : Input) (h : i.namesGoalTerm = false) :
    force i = .advisory := by
  simp [force, h]

theorem advisory_of_no_evidence (i : Input) (h : i.namesWorkEvidence = false) :
    force i = .advisory := by
  simp [force, h]

theorem advisory_of_false_basis (i : Input) (h : i.basisShownFalse = true) :
    force i = .advisory := by
  simp [force, h]

/-- A review finding: its verdict, what it names, and whether it exists only under a
trusted role acting maliciously (`ThreatEligibility`). -/
structure Finding where
  verdict : ReviewVerdict
  input : Input
  requiresTrustedMalice : Bool
  trigger : TriggerRecord
  requiresOwnerDecision : Bool := false
  deriving DecidableEq, Repr

-- SKILL[def]: "ask whether a finding would exist only if a role declared trusted by `harness.trust_boundary` deliberately acted maliciously; if so, the finding is ineligible"
/-- `ThreatEligibility`. -/
def eligible (f : Finding) : Bool := !f.requiresTrustedMalice && triggerCheck f.trigger

-- SKILL[def]: "A blocking finding that fails `ThreatEligibility` or `BlockingAuthority` is downgraded by the meta-judge to an advisory with its reason recorded, then the remaining verdicts are routed again."
/-- `## Review Truth Table`: a blocking finding that fails either check is an advisory. -/
def downgrade (f : Finding) : ReviewVerdict :=
  match f.verdict with
  | .reject =>
    if !f.requiresTrustedMalice && completeAdmission ⟨f.input, f.trigger⟩ then .reject else .comment
  | .approve => .approve
  | .comment => .comment

/-- Downgrading never creates a reject or an approve. -/
theorem downgrade_reject_iff (f : Finding) :
    downgrade f = .reject ↔
      f.verdict = .reject ∧ eligible f = true ∧ force f.input = .blocking := by
  cases f with
  | mk v i m tr owner =>
    cases v <;> cases m <;> cases hf : force i <;> cases ht : triggerCheck tr <;>
      simp [downgrade, completeAdmission, eligible, hf, ht]

theorem downgrade_approve_iff (f : Finding) : downgrade f = .approve ↔ f.verdict = .approve := by
  cases f with
  | mk v i m tr owner => cases v <;> cases m <;> cases hf : force i <;> cases ht : triggerCheck tr <;>
      simp [downgrade, completeAdmission, eligible, hf, ht]

/-- Downgrade is idempotent: routing "again" after a downgrade changes nothing further. -/
theorem downgrade_idempotent (f : Finding) :
    downgrade { f with verdict := downgrade f } = downgrade f := by
  cases f with
  | mk v i m tr owner => cases v <;> cases m <;> cases hf : force i <;> cases ht : triggerCheck tr <;>
      simp [downgrade, completeAdmission, eligible, hf, ht]

theorem ordinary_reachable_finding_stays_blocking (f : Finding)
    (hv : f.verdict = .reject) (hi : force f.input = .blocking)
    (hm : f.requiresTrustedMalice = false)
    (ht : triggerCheck f.trigger = true) :
    downgrade f = .reject := by
  simp [downgrade, completeAdmission, hv, hi, hm, ht]

end Sshx
