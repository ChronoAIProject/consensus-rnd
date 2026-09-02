import Sshx.Verdicts
import Sshx.Blocking

/-!
# The three fixed truth tables

Source: `## Design Truth Table`, `## Review Truth Table`, `## Termination Truth Table`.
Each table is a total function; the English rows are also stated as predicates and
proven exhaustive, and (where the contract claims it) unambiguous.
-/

namespace Sshx

/-! ## Review -/

inductive ReviewExit
  | fix
  | doneWithAdvisory
  | userDecisionOrBoundedPass
  deriving DecidableEq, Repr

-- SKILL: "| any explicit reject | `fix` |"
def reviewRoute (a b c : ReviewVerdict) : ReviewExit :=
  if a = .reject ∨ b = .reject ∨ c = .reject then .fix
  else if a = .approve ∨ b = .approve ∨ c = .approve then .doneWithAdvisory
  else .userDecisionOrBoundedPass

/-- Row 1: any explicit reject. -/
def reviewRow1 (a b c : ReviewVerdict) : Prop := a = .reject ∨ b = .reject ∨ c = .reject
-- SKILL: "| no reject and at least one approve | `done with advisory surfaced` |"
/-- Row 2: no reject and at least one approve. -/
def reviewRow2 (a b c : ReviewVerdict) : Prop :=
  ¬ reviewRow1 a b c ∧ (a = .approve ∨ b = .approve ∨ c = .approve)
-- SKILL: "| all comment | `explicit user decision or another bounded review pass` |"
/-- Row 3: all comment. The contract adds "and no approve", which all-comment already implies. -/
def reviewRow3 (a b c : ReviewVerdict) : Prop := a = .comment ∧ b = .comment ∧ c = .comment

instance (a b c : ReviewVerdict) : Decidable (reviewRow1 a b c) := by unfold reviewRow1; infer_instance
instance (a b c : ReviewVerdict) : Decidable (reviewRow2 a b c) := by unfold reviewRow2; infer_instance
instance (a b c : ReviewVerdict) : Decidable (reviewRow3 a b c) := by unfold reviewRow3; infer_instance

theorem review_rows_exhaustive :
    ∀ a ∈ ReviewVerdict.univ, ∀ b ∈ ReviewVerdict.univ, ∀ c ∈ ReviewVerdict.univ,
      reviewRow1 a b c ∨ reviewRow2 a b c ∨ reviewRow3 a b c := by
  decide

theorem review_rows_exclusive :
    ∀ a ∈ ReviewVerdict.univ, ∀ b ∈ ReviewVerdict.univ, ∀ c ∈ ReviewVerdict.univ,
      ¬ (reviewRow1 a b c ∧ reviewRow2 a b c) ∧ ¬ (reviewRow1 a b c ∧ reviewRow3 a b c) ∧
        ¬ (reviewRow2 a b c ∧ reviewRow3 a b c) := by
  decide

theorem reviewRoute_rows :
    ∀ a ∈ ReviewVerdict.univ, ∀ b ∈ ReviewVerdict.univ, ∀ c ∈ ReviewVerdict.univ,
      (reviewRoute a b c = .fix ↔ reviewRow1 a b c) ∧
        (reviewRoute a b c = .doneWithAdvisory ↔ reviewRow2 a b c) ∧
        (reviewRoute a b c = .userDecisionOrBoundedPass ↔ reviewRow3 a b c) := by
  decide

theorem reviewRoute_fix_iff (a b c : ReviewVerdict) :
    reviewRoute a b c = .fix ↔ (a = .reject ∨ b = .reject ∨ c = .reject) := by
  cases a <;> cases b <;> cases c <;> decide

/-- "all comment and no approve" is the same row as "all comment". -/
theorem review_row3_no_approve_redundant :
    ∀ a ∈ ReviewVerdict.univ, ∀ b ∈ ReviewVerdict.univ, ∀ c ∈ ReviewVerdict.univ,
      reviewRow3 a b c ↔
        (reviewRow3 a b c ∧ ¬ (a = .approve ∨ b = .approve ∨ c = .approve)) := by
  decide

/-- Advisory comments do not count as approval. -/
theorem comments_are_not_approval :
    reviewRoute .comment .comment .comment = .userDecisionOrBoundedPass := by
  decide

/-- Downgrade then route: the routed exit after downgrade is `fix` iff some finding is a
reject that passes both `ThreatEligibility` and `BlockingAuthority`. -/
def routeFindings (f g h : Finding) : ReviewExit :=
  reviewRoute (downgrade f) (downgrade g) (downgrade h)

theorem routeFindings_fix_iff (f g h : Finding) :
    routeFindings f g h = .fix ↔
      (f.verdict = .reject ∧ eligible f = true ∧ force f.input = .blocking) ∨
        (g.verdict = .reject ∧ eligible g = true ∧ force g.input = .blocking) ∨
        (h.verdict = .reject ∧ eligible h = true ∧ force h.input = .blocking) := by
  rw [routeFindings, reviewRoute_fix_iff, downgrade_reject_iff, downgrade_reject_iff,
    downgrade_reject_iff]

/-! ## Design -/

/-- The meta-judge's inputs at the design gate. `unanimousPropose` is row 1's input;
`compatiblePlans` is row 2's; the remaining flags are the `implement` preconditions the
contract spreads over three paragraphs. -/
structure DesignInputs where
  rosterIsSixWorkerSeats : Bool
  unanimousPropose : Bool
  compatiblePlans : Bool
  beautiful : Bool
  worth : Bool
  harnessOverlapOrAuthorityGap : Bool
  unresolvedGroundedConflict : Bool
  deriving DecidableEq, Repr

-- SKILL: "| close disagreement with compatible plans | `meta-layer convergence` |"
-- SKILL: "| bounded true stall | `abstain/escalate with options` |"
-- SKILL: "| any attempt to use one perspective as consensus | `reject fake consensus` |"
inductive DesignExit
  | implement
  | metaLayerConvergence
  | abstainEscalate
  | rejectFakeConsensus
  deriving DecidableEq, Repr

-- SKILL: "An `implement` exit requires the concrete plan to be both beautiful"
/-- When a unanimous plan fails a precondition the contract permits either
`meta-layer convergence` or `abstain/escalate`, "never `implement`"; the model takes
convergence first. -/
def implementAllowed (d : DesignInputs) : Bool :=
  d.unanimousPropose && d.beautiful && d.worth && !d.harnessOverlapOrAuthorityGap &&
    !d.unresolvedGroundedConflict

-- SKILL: "| unanimous actionable plan | `implement` |"
def designRoute (d : DesignInputs) : DesignExit :=
  if !d.rosterIsSixWorkerSeats then .rejectFakeConsensus
  else if implementAllowed d then .implement
  else if d.compatiblePlans || d.unanimousPropose then .metaLayerConvergence
  else .abstainEscalate

theorem designRoute_implement_iff (d : DesignInputs) :
    designRoute d = .implement ↔ d.rosterIsSixWorkerSeats = true ∧ implementAllowed d = true := by
  cases d with
  | mk r u c b w h g =>
    cases r <;> cases u <;> cases c <;> cases b <;> cases w <;> cases h <;> cases g <;>
      simp [designRoute, implementAllowed]

theorem no_implement_without_worth (d : DesignInputs) (h : d.worth = false) :
    designRoute d ≠ .implement := by
  intro hc
  have := (designRoute_implement_iff d).mp hc
  simp [implementAllowed, h] at this

theorem no_implement_with_unresolved_conflict (d : DesignInputs)
    (h : d.unresolvedGroundedConflict = true) : designRoute d ≠ .implement := by
  intro hc
  have := (designRoute_implement_iff d).mp hc
  simp [implementAllowed, h] at this

theorem no_implement_with_harness_gap (d : DesignInputs)
    (h : d.harnessOverlapOrAuthorityGap = true) : designRoute d ≠ .implement := by
  intro hc
  have := (designRoute_implement_iff d).mp hc
  simp [implementAllowed, h] at this

theorem fake_roster_rejected (d : DesignInputs) (h : d.rosterIsSixWorkerSeats = false) :
    designRoute d = .rejectFakeConsensus := by
  simp [designRoute, h]

/-! ## Termination -/

/-- Who presents the roster as termination consensus. -/
inductive ClaimSource
  | terminationSeats
  | callerJudgment
  | reviewExit
  deriving DecidableEq, Repr

def ClaimSource.univ : List ClaimSource := [.terminationSeats, .callerJudgment, .reviewExit]

theorem ClaimSource.mem_univ (s : ClaimSource) : s ∈ ClaimSource.univ := by
  cases s <;> decide

/-- The dispatch-time recorded roster: the three named roles and whether the roster is
exactly those three distinct named isolated seats. -/
structure Roster where
  exactThreeNamed : Bool
  criterionEvidence : TerminationSeat
  residualGap : TerminationSeat
  claimIntegrity : TerminationSeat
  deriving DecidableEq, Repr

inductive TerminationExit
  | rejectFakeConsensus
  | claimPermitted
  | withholdContinue
  | withholdEscalate
  deriving DecidableEq, Repr

def Roster.seats (r : Roster) : List TerminationSeat :=
  [r.criterionEvidence, r.residualGap, r.claimIntegrity]

-- SKILL: "| unanimous `satisfied` | `termination claim permitted` |"
def terminationRoute (s : ClaimSource) (r : Roster) : TerminationExit :=
  if s ≠ .terminationSeats ∨ r.exactThreeNamed = false then .rejectFakeConsensus
  else if r.seats.all (· = .satisfied) then .claimPermitted
  else if r.seats.any (· = .unsatisfied) then .withholdContinue
  else .withholdEscalate

-- SKILL: "| caller judgment, a review exit, or any roster other than exactly the three distinct named isolated termination seats presented as termination consensus | `reject fake termination consensus` |"
def termRow1 (s : ClaimSource) (r : Roster) : Prop :=
  s ≠ .terminationSeats ∨ r.exactThreeNamed = false
def termRow2 (_ : ClaimSource) (r : Roster) : Prop :=
  r.criterionEvidence = .satisfied ∧ r.residualGap = .satisfied ∧ r.claimIntegrity = .satisfied
-- SKILL: "| any `unsatisfied` | `withhold claim; continue against the named goal gap` |"
def termRow3 (_ : ClaimSource) (r : Roster) : Prop :=
  r.criterionEvidence = .unsatisfied ∨ r.residualGap = .unsatisfied ∨
    r.claimIntegrity = .unsatisfied
-- SKILL: "| no `unsatisfied` and any `abstain`, invalid or missing seat result | `withhold claim; escalate with the unresolved evidence gap` |"
def termRow4 (s : ClaimSource) (r : Roster) : Prop :=
  ¬ termRow3 s r ∧ r.seats.any (fun v => v = .abstain ∨ v = .invalid ∨ v = .missing)

instance (s : ClaimSource) (r : Roster) : Decidable (termRow1 s r) := by
  unfold termRow1; infer_instance
instance (s : ClaimSource) (r : Roster) : Decidable (termRow2 s r) := by
  unfold termRow2; infer_instance
instance (s : ClaimSource) (r : Roster) : Decidable (termRow3 s r) := by
  unfold termRow3; infer_instance
instance (s : ClaimSource) (r : Roster) : Decidable (termRow4 s r) := by
  unfold termRow4; infer_instance

def Roster.univ : List Roster :=
  [true, false].flatMap fun e =>
    TerminationSeat.univ.flatMap fun a =>
      TerminationSeat.univ.flatMap fun b =>
        TerminationSeat.univ.map fun c => ⟨e, a, b, c⟩

theorem Roster.mem_univ (r : Roster) : r ∈ Roster.univ := by
  cases r with
  | mk e a b c => cases e <;> cases a <;> cases b <;> cases c <;> decide

/-- The rows are complete. -/
theorem termination_rows_exhaustive (s : ClaimSource) (r : Roster) :
    termRow1 s r ∨ termRow2 s r ∨ termRow3 s r ∨ termRow4 s r := by
  cases s <;> cases r with
  | mk e a b c => cases e <;> cases a <;> cases b <;> cases c <;> decide

/-- Rows 2, 3, 4 are pairwise exclusive on their own. -/
theorem termination_rows_2_3_4_exclusive (s : ClaimSource) (r : Roster) :
    ¬ (termRow2 s r ∧ termRow3 s r) ∧ ¬ (termRow2 s r ∧ termRow4 s r) ∧
      ¬ (termRow3 s r ∧ termRow4 s r) := by
  cases s <;> cases r with
  | mk e a b c => cases e <;> cases a <;> cases b <;> cases c <;> decide

/-- Row 1 overlaps rows 2–4 without the evaluation order, so the contract's qualifier
"under this evaluation order, unambiguous" is load-bearing: a caller judgment over an
all-`satisfied` roster satisfies both row 1 and row 2. -/
theorem termination_row1_overlaps_row2 :
    termRow1 .callerJudgment ⟨true, .satisfied, .satisfied, .satisfied⟩ ∧
      termRow2 .callerJudgment ⟨true, .satisfied, .satisfied, .satisfied⟩ := by
  decide

/-- The ordered evaluation is the function `terminationRoute`. -/
theorem terminationRoute_rows (s : ClaimSource) (r : Roster) :
    (terminationRoute s r = .rejectFakeConsensus ↔ termRow1 s r) ∧
      (terminationRoute s r = .claimPermitted ↔ ¬ termRow1 s r ∧ termRow2 s r) ∧
      (terminationRoute s r = .withholdContinue ↔ ¬ termRow1 s r ∧ termRow3 s r) ∧
      (terminationRoute s r = .withholdEscalate ↔ ¬ termRow1 s r ∧ termRow4 s r) := by
  cases s <;> cases r with
  | mk e a b c => cases e <;> cases a <;> cases b <;> cases c <;> decide

/-- A named role present without a valid result reaches row 4 unless an earlier row
matches; this is a consequence of the table, not an extra rule. -/
theorem missing_role_reaches_row4 (s : ClaimSource) (r : Roster) :
    r.criterionEvidence = .missing → ¬ termRow1 s r → ¬ termRow3 s r →
      terminationRoute s r = .withholdEscalate := by
  cases s <;> cases r with
  | mk e a b c => cases e <;> cases a <;> cases b <;> cases c <;> decide

/-- No `abstain`, invalid, or missing result ever becomes permission. -/
theorem no_permission_from_nonsatisfied (s : ClaimSource) (r : Roster) :
    terminationRoute s r = .claimPermitted →
      r.criterionEvidence = .satisfied ∧ r.residualGap = .satisfied ∧
        r.claimIntegrity = .satisfied := by
  cases s <;> cases r with
  | mk e a b c => cases e <;> cases a <;> cases b <;> cases c <;> decide

/-- Caller judgment and review exits are never termination consensus. -/
theorem caller_never_consensus (r : Roster) :
    terminationRoute .callerJudgment r = .rejectFakeConsensus ∧
      terminationRoute .reviewExit r = .rejectFakeConsensus := by
  simp [terminationRoute]

-- SKILL: "treating a repeated `unsatisfied` that again names no `GoalArtifact` term as `abstain`"
/-- `## Termination Truth Table` (beta.39): an `unsatisfied` that names no `GoalArtifact`
term is advisory; the seat is re-dispatched once and a repeated unnamed `unsatisfied` is
read as `abstain`. The first phase is a process step; this function is the table input
after that phase. -/
def settleSeat (v : TerminationSeat) (namesGoalTerm : Bool) (redispatchedAlready : Bool) :
    TerminationSeat :=
  match v with
  | .unsatisfied => if namesGoalTerm then .unsatisfied
                    else if redispatchedAlready then .abstain else .unsatisfied
  | v => v

/-- After the single re-dispatch, a procedural `unsatisfied` can neither permit the claim
nor route `withhold; continue against the named goal gap`: it only escalates. -/
theorem procedural_unsatisfied_never_continues (r : Roster)
    (h : settleSeat r.criterionEvidence false true = r.criterionEvidence)
    (hu : r.criterionEvidence = .unsatisfied) : False := by
  rw [hu] at h
  simp [settleSeat] at h

theorem settled_procedural_is_abstain :
    settleSeat .unsatisfied false true = .abstain := by
  decide

theorem grounded_unsatisfied_kept (b : Bool) :
    settleSeat .unsatisfied true b = .unsatisfied := by
  cases b <;> decide

end Sshx
