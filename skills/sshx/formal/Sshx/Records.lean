/-!
# Records

Source: `## Goal Contract` (`GoalArtifact`, `harness`, `revisions`), `## Result Envelope`,
and the stage record mirror rule.
-/

namespace Sshx

/-- `harness` has exactly three sub-items. -/
structure Harness where
  providedCapabilities : List String
  trustBoundary : String
  decisionOwnership : String
  deriving DecidableEq, Repr

/-- A revision item has exactly three sub-items; a missing one is invalid and fails closed,
which the type makes impossible to construct. -/
structure Revision where
  change : String
  authorizationSource : String
  invalidatedCompletedWork : String
  deriving DecidableEq, Repr

-- SKILL[def]: "`GoalArtifact` has exactly these fields:"
/-- `GoalArtifact` has exactly these seven fields. -/
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

/-- A correction never rewrites an earlier target. -/
theorem GoalArtifact.correct_keeps_goal (g : GoalArtifact) (r : Revision) :
    (g.correct r).normalizedGoal = g.normalizedGoal ∧
      (g.correct r).successCriteria = g.successCriteria := ⟨rfl, rfl⟩

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
