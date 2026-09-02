import Sshx.Verdicts

/-!
# `BlockingAuthority`, `ThreatEligibility`, and the review downgrade

Source: `## Reasoning Discipline` (`BlockingAuthority`, `ThreatEligibility`) and the
downgrade sentence of `## Review Truth Table`.
-/

namespace Sshx

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
  deriving DecidableEq, Repr

inductive Force
  | blocking
  | advisory
  deriving DecidableEq, Repr

-- SKILL[def]: "Advisory is the default; blocking is the exception, and the exception has exactly two conjuncts that the input itself must name"
/-- `BlockingAuthority`: advisory is the default; blocking needs both named conjuncts. -/
def force (i : Input) : Force :=
  if i.namesGoalTerm && i.namesWorkEvidence && !i.basisShownFalse then .blocking else .advisory

theorem force_blocking_iff (i : Input) :
    force i = .blocking ↔
      i.namesGoalTerm = true ∧ i.namesWorkEvidence = true ∧ i.basisShownFalse = false := by
  cases i with
  | mk a b c d => cases a <;> cases b <;> cases c <;> cases d <;> simp [force]

/-- Disputed grounding is not absent grounding: the dispute flag never changes the force. -/
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
  deriving DecidableEq, Repr

-- SKILL[def]: "ask whether a finding would exist only if a role declared trusted by `harness.trust_boundary` deliberately acted maliciously; if so, the finding is ineligible"
/-- `ThreatEligibility`. -/
def eligible (f : Finding) : Bool := !f.requiresTrustedMalice

-- SKILL[def]: "A blocking finding that fails `ThreatEligibility` or `BlockingAuthority` is downgraded by the meta-judge to an advisory with its reason recorded, then the remaining verdicts are routed again."
/-- `## Review Truth Table`: a blocking finding that fails either check is an advisory. -/
def downgrade (f : Finding) : ReviewVerdict :=
  match f.verdict with
  | .reject => if eligible f && (force f.input == .blocking) then .reject else .comment
  | .approve => .approve
  | .comment => .comment

/-- Downgrading never creates a reject or an approve. -/
theorem downgrade_reject_iff (f : Finding) :
    downgrade f = .reject ↔
      f.verdict = .reject ∧ eligible f = true ∧ force f.input = .blocking := by
  cases f with
  | mk v i m =>
    cases v <;> cases m <;> cases hf : force i <;> simp [downgrade, eligible, hf]

theorem downgrade_approve_iff (f : Finding) : downgrade f = .approve ↔ f.verdict = .approve := by
  cases f with
  | mk v i m => cases v <;> cases m <;> cases hf : force i <;> simp [downgrade, eligible, hf]

/-- Downgrade is idempotent: routing "again" after a downgrade changes nothing further. -/
theorem downgrade_idempotent (f : Finding) :
    downgrade { f with verdict := downgrade f } = downgrade f := by
  cases f with
  | mk v i m => cases v <;> cases m <;> cases hf : force i <;> simp [downgrade, eligible, hf]

end Sshx
