import Mathlib.Tactic
import Sshx.Tables
import Sshx.Reasoning.Authority
import Sshx.Reasoning.Discipline

/-!
# Reasoning: review

Source: `## Review Triplet` and `## Review Truth Table` — the three perspectives, the
reviewer's duty over protocol text, and the downgrade logic.
-/

namespace Sshx.Reasoning

open Sshx

-- SKILL[policy]: "Protocol policy, not a mathematical consequence: after implementation, run three review perspectives:"
def reviewCount : Nat := 3

inductive ReviewFocus
  | architecture
  | quality
  | tests
  deriving DecidableEq, Repr

structure ReviewCharter where
  focus : ReviewFocus
  concerns : List String
  deriving DecidableEq, Repr

-- SKILL[def]: "- `architecture`: boundaries, contracts, coupling, and maintainability."
def architecture : ReviewCharter :=
  ⟨.architecture, ["boundaries", "contracts", "coupling", "maintainability"]⟩

-- SKILL[def]: "- `quality`: behavior, edge cases, failure modes, and user impact."
def quality : ReviewCharter :=
  ⟨.quality, ["behavior", "edge cases", "failure modes", "user impact"]⟩

-- SKILL[def]: "- `tests`: coverage, determinism, and verification strength."
def tests : ReviewCharter :=
  ⟨.tests, ["coverage", "determinism", "verification strength"]⟩

def reviewTriplet : List ReviewCharter := [architecture, quality, tests]

theorem triplet_has_three : reviewTriplet.length = reviewCount := rfl

/-! ## The reviewer's duty over protocol text -/

-- SKILL[def]: "Reviewers must check protocol text for newly added exception clauses, statements that contradict existing clauses, semantic weakening of existing propositions, and external identifier or source coupling that lexical token shapes cannot recognize."
inductive ProtocolTextCheck
  | newlyAddedExceptionClause
  | contradictionOfExistingClause
  | semanticWeakening
  | unrecognizedExternalCoupling
  deriving DecidableEq, Repr

def reviewerDuty : List ProtocolTextCheck :=
  [.newlyAddedExceptionClause, .contradictionOfExistingClause, .semanticWeakening,
    .unrecognizedExternalCoupling]

/-- The residual classes no positional or lexical check decides. -/
inductive ResidualClass
  | arbitraryEnglishEntailsWeakening
  | unrecognizedTokenCouplesExternally
  deriving DecidableEq, Repr

-- SKILL[def]: "This reviewer duty is the declared absorber for the residual classes that positional and lexical checks cannot decide: whether arbitrary English semantically entails such a weakening, and whether an unrecognized token or phrase couples the contract to an external identifier or source."
def absorber : ResidualClass → ProtocolTextCheck
  | .arbitraryEnglishEntailsWeakening => .semanticWeakening
  | .unrecognizedTokenCouplesExternally => .unrecognizedExternalCoupling

theorem every_residual_class_absorbed (r : ResidualClass) : absorber r ∈ reviewerDuty := by
  cases r <;> decide

/-- A reviewer's conclusion. -/
structure ReviewConclusion where
  focus : ReviewFocus
  verdict : ReviewVerdict
  note : DisciplineNote
  weighed : List Candidate

-- SKILL[def]: "Before approving, commenting, or rejecting, each reviewer must apply `## Reasoning Discipline` to every implementation interpretation, repair candidate, or approval path it weighs and surface the compact reasoning-discipline note in `SshxResultEnvelope.conclusion` before returning a verdict."
def ReviewConclusion.disciplined (c : ReviewConclusion) : Prop := c.note.valid c.weighed

-- SKILL[ref]: "- `approve`"
-- SKILL[ref]: "- `comment`"
abbrev reviewVerdicts : List ReviewVerdict := ReviewVerdict.univ

/-! ## The review truth table's surrounding rules -/

-- SKILL[ref]: "The meta-judge applies this fixed review truth table:"
abbrev reviewTable := @reviewRoute

-- SKILL[ref]: "Advisory comments do not count as approval."
abbrev advisoryCommentsAreNotApproval := @comments_are_not_approval

-- SKILL[thm]: "A reject blocks done until the issue is fixed or explicitly converted into a non-blocking advisory by a bounded review pass."
theorem reject_blocks_done (a b c : ReviewVerdict) (h : a = .reject ∨ b = .reject ∨ c = .reject) :
    reviewRoute a b c = .fix := by
  simp [reviewRoute, h]

/-- The only conversion of a reject into an advisory is the downgrade. -/
theorem conversion_is_downgrade (f : Finding) (h : f.verdict = .reject)
    (hc : downgrade f = .comment) : eligible f = false ∨ force f.input = .advisory := by
  by_contra hcontra
  push_neg at hcontra
  have hb : force f.input = .blocking := by
    cases hf : force f.input
    · rfl
    · exact absurd hf hcontra.2
  have : downgrade f = .reject :=
    (downgrade_reject_iff f).mpr ⟨h, by simpa using hcontra.1, hb⟩
  simp [this] at hc

/-- A blocking finding as the contract requires it to be stated. -/
structure BlockingFinding where
  input : Input
  goalTerm : String
  conduct : TrustedPartyConduct
  deriving DecidableEq, Repr

-- SKILL[def]: "Every blocking finding must name both `BlockingAuthority` conjuncts under `## Reasoning Discipline` — the `GoalArtifact` term the work as built fails and the evidence in the work that shows it — and which class of failure, omission, or uncertainty within the declared trust boundary it addresses."
def BlockingFinding.wellFormed (b : BlockingFinding) : Prop :=
  force b.input = .blocking ∧ b.goalTerm ≠ "" ∧ threatEligible b.conduct = true

-- SKILL[ref]: "A `BlockingAuthority` downgrade is objective: it is recorded as `BlockingAuthority` requires and never assesses persuasiveness; disputed grounding stays blocking."
abbrev objectiveDowngrade := @downgradeRecord

-- SKILL[thm]: "Downgrade is allowed only for threat-model ineligibility or an advisory input, never because a finding is inconvenient, expensive, or late, and never sets aside a reachable defect."
theorem downgrade_only_for_ineligible_or_advisory (f : Finding) (h : f.verdict = .reject)
    (hc : downgrade f = .comment) : eligible f = false ∨ force f.input = .advisory :=
  conversion_is_downgrade f h hc

/-- The state of the harness declaration at review time. -/
inductive HarnessState
  | declared
  | missing
  | ambiguous
  | stale
  deriving DecidableEq, Repr

inductive HarnessRoute
  | routeNormally
  | pauseAndEscalateToMaintainer
  deriving DecidableEq, Repr

-- SKILL[def]: "A missing, ambiguous, or stale harness declaration is never a downgrade shield: pause routing and escalate to the maintainer instead of declaring done."
def routeWithHarness : HarnessState → HarnessRoute
  | .declared => .routeNormally
  | .missing | .ambiguous | .stale => .pauseAndEscalateToMaintainer

theorem defective_harness_never_shields (h : HarnessState) (hne : h ≠ .declared) :
    routeWithHarness h = .pauseAndEscalateToMaintainer := by
  cases h <;> simp_all [routeWithHarness]

end Sshx.Reasoning
