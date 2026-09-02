/-!
# Records

Source: `## Goal Contract` (`GoalArtifact`, `harness`, `revisions`), `## Result Envelope`,
and the stage record mirror rule.
-/

namespace Sshx

-- SKILL[def]: "`harness` is a prompt-level record containing exactly these three sub-items:"
-- SKILL[def]: "- `provided_capabilities`: capabilities already supplied by the execution environment that the skill must not implement again;"
-- SKILL[def]: "- `trust_boundary`: which roles are trusted and which are untrusted. The trusted declaration is **non-adversarial, not infallible**: failures, omissions, and uncertainty by a trusted party remain fully in review scope;"
-- SKILL[def]: "- `decision_ownership`: product, governance, and boundary decisions; engineering judgments; and orchestration judgments, each assigned to its owner."
/-- `harness` has exactly three sub-items. -/
structure Harness where
  providedCapabilities : List String
  trustBoundary : String
  decisionOwnership : String
  deriving DecidableEq, Repr

-- SKILL[def]: "- `change`: what was corrected;"
-- SKILL[def]: "- `authorization_source`: where authorization came from;"
-- SKILL[def]: "- `invalidated_completed_work`: completed work invalidated by the correction, or `none`."
-- SKILL[def]: "A revision item missing any one of these sub-items is invalid and fails closed."
/-- A revision item has exactly three sub-items; a missing one is invalid and fails closed,
which the type makes impossible to construct. -/
structure Revision where
  change : String
  authorizationSource : String
  invalidatedCompletedWork : String
  deriving DecidableEq, Repr

/-! `GoalArtifact` has exactly these seven fields. -/
-- SKILL[def]: "`GoalArtifact` has exactly these fields:"
-- SKILL[def]: "- `raw_user_input`"
-- SKILL[def]: "- `normalized_goal`"
-- SKILL[def]: "- `constraints`"
-- SKILL[def]: "- `success_criteria`"
-- SKILL[def]: "- `iteration_question`"
-- SKILL[def]: "- `harness`"
-- SKILL[def]: "- `revisions`"
structure GoalArtifact where
  rawUserInput : String
  normalizedGoal : String
  constraints : List String
  successCriteria : List String
  iterationQuestion : String
  harness : Harness
  revisions : List Revision
  deriving DecidableEq, Repr

-- SKILL[def]: "`revisions` is an append-only list"
/-- The only way to correct a `GoalArtifact`: append one revision. -/
def GoalArtifact.correct (g : GoalArtifact) (r : Revision) : GoalArtifact :=
  { g with revisions := g.revisions ++ [r] }

/-- Revisions are append-only: every earlier revision list is a prefix of every later one. -/
theorem GoalArtifact.correct_prefix (g : GoalArtifact) (r : Revision) :
    g.revisions <+: (g.correct r).revisions :=
  ⟨[r], rfl⟩

-- SKILL[thm]: "A correction is appended after the settlement it supersedes, and only a later settlement may consume the corrected target."
/-- The correction lands strictly after every earlier revision. -/
theorem GoalArtifact.correct_appends_after (g : GoalArtifact) (r : Revision) :
    (g.correct r).revisions.length = g.revisions.length + 1 := by
  simp [GoalArtifact.correct]

/-- A correction never rewrites an earlier target. -/
theorem GoalArtifact.correct_keeps_goal (g : GoalArtifact) (r : Revision) :
    (g.correct r).normalizedGoal = g.normalizedGoal ∧
      (g.correct r).successCriteria = g.successCriteria := ⟨rfl, rfl⟩

-- SKILL[def]: "Every `SshxResultEnvelope` returned by `thinking_panel_workers`, `meta_judge`, `implementation_worker`, `review_triplet_workers`, and `fix_or_done` uses exactly these top-level fields:"
-- SKILL[def]: "The envelope payload itself stays exactly `conclusion` and `log_ref`."
/-- `SshxResultEnvelope` has exactly `conclusion` and `log_ref`; the verdict lives inside
`conclusion`. -/
structure Envelope (Verdict : Type) where
  conclusionVerdict : Verdict
  logRef : String
  deriving DecidableEq, Repr

/-- A caller-carried stage record wraps an envelope. Its `verdict` is a projection of
`conclusion.verdict`, so a mismatch cannot be written. -/
structure StageRecord (Verdict : Type) where
  envelope : Envelope Verdict
  role : String
  deriving DecidableEq, Repr

-- SKILL[def]: "A stage record's `verdict` field, when present, is a read-only mirror of its envelope's `conclusion.verdict`"
def StageRecord.verdict {V : Type} (r : StageRecord V) : V := r.envelope.conclusionVerdict

theorem StageRecord.verdict_mirrors {V : Type} (r : StageRecord V) :
    r.verdict = r.envelope.conclusionVerdict := rfl

end Sshx
