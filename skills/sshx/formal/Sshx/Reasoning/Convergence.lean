import Mathlib.Tactic
import Sshx.Tables
import Sshx.Reasoning.Panel
import Sshx.Semantics.Stop

/-!
# Reasoning: meta-judge convergence

Source: `## Design Truth Table` — the implement preconditions, the worth challenge and
accepted debt, harness mismatch, the reflection gate, the focused round and its reopening
rule, and the convergence question and orientation.
-/

namespace Sshx.Reasoning

open Sshx

-- SKILL[ref]: "The meta-judge applies this fixed thinking truth table:"
abbrev designTable := @designRoute

/-! ## Convergence output -/

-- SKILL[def]: "`meta-layer convergence` must produce one concrete plan before implementation."
inductive ConvergenceResult
  | concretePlan (plan : Plan)
  | stopWithOptions (options : List String)
  deriving DecidableEq, Repr

-- SKILL[def]: "If the bounded pass still cannot produce a concrete plan, stop with options instead of inventing agreement."
def converge (candidate : Option Plan) (options : List String) : ConvergenceResult :=
  match candidate with
  | some p => .concretePlan p
  | none => .stopWithOptions options

theorem no_plan_means_stop_with_options (options : List String) :
    converge none options = .stopWithOptions options := rfl

/-! ## The worth challenge -/

-- SKILL[def]: "A concrete plan that fails the 值不值 (worth) judgment is an unclosed `GoalArtifact` goal gap: before `implement` the meta-judge must rebut the `worth` seat's factual premises, choose a cheaper sufficient alternative, show the omitted benefit clears the goal threshold, or record an explicit owner-level acceptance of the cost — otherwise the exit stays `meta-layer convergence` or `abstain/escalate with options`, never `implement`."
inductive WorthRemedy
  | rebutFactualPremises
  | cheaperSufficientAlternative
  | omittedBenefitClearsThreshold
  | ownerLevelAcceptance
  deriving DecidableEq, Repr

def worthResolved (worthPassed : Bool) (remedy : Option WorthRemedy) : Bool :=
  worthPassed || remedy.isSome

theorem worth_failure_without_remedy_never_implements (d : DesignInputs)
    (h : d.worth = false) : designRoute d ≠ .implement :=
  no_implement_without_worth d h

/-- An expiry condition; there is no constructor for `temporary` without one. -/
inductive ExpiryCondition
  | removalCondition (condition : String)
  | expiryDate (date : String)
  deriving DecidableEq, Repr

-- SKILL[def]: "Beauty and worth are a conditional challenge, not a forced clash: when the beautiful form carries a material elegance premium over the cheapest sufficient form, `worth` must justify or reject that premium; when `worth` prefers a cheaper, uglier form, the meta-judge records the accepted debt with its owner, containment boundary, and removal or expiry condition, since `temporary` without an expiry condition is not acceptable."
structure AcceptedDebt where
  owner : String
  containmentBoundary : String
  expiry : ExpiryCondition
  deriving DecidableEq, Repr

/-- Whether the challenge is even raised: only when a material elegance premium exists. -/
def elegancePremiumChallenged (beautifulCost cheapestCost : Nat) : Bool :=
  cheapestCost < beautifulCost

theorem no_premium_no_clash (c : Nat) : elegancePremiumChallenged c c = false := by
  simp [elegancePremiumChallenged]

-- SKILL[thm]: "An `implement` exit also requires no unresolved harness overlap or authority gap."
theorem harness_gap_blocks_implement (d : DesignInputs)
    (h : d.harnessOverlapOrAuthorityGap = true) : designRoute d ≠ .implement :=
  no_implement_with_harness_gap d h

/-- What a goal presupposes of the harness. -/
structure GoalHarnessFit where
  presupposesMissingHostCapability : Bool
  repeatsDeclaredCapability : Bool
  usesOwnJudgmentResponsibilities : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "A goal and its harness mismatch when it presupposes missing host/controller execution capability or asks the skill to repeat a capability already declared by the harness; the skill's own judgment responsibilities are not a mismatch."
def harnessMismatch (g : GoalHarnessFit) : Bool :=
  g.presupposesMissingHostCapability || g.repeatsDeclaredCapability

theorem judgment_is_not_mismatch (g : GoalHarnessFit)
    (h1 : g.presupposesMissingHostCapability = false) (h2 : g.repeatsDeclaredCapability = false) :
    harnessMismatch g = false := by
  simp [harnessMismatch, h1, h2]

-- SKILL[def]: "Route a mismatch as a goal gap to the maintainer rather than implementing it."
inductive MismatchRoute
  | goalGapToMaintainer
  deriving DecidableEq, Repr

def routeMismatch (g : GoalHarnessFit) (h : harnessMismatch g = true) : MismatchRoute :=
  .goalGapToMaintainer

/-! ## The reflection gate -/

/-- The three questions the gate asks. -/
structure ReflectionInputs where
  goalChanged : Bool
  harnessChanged : Bool
  evidenceOverturnedDirection : Bool
  deriving DecidableEq, Repr

inductive ReflectionAction
  | continue
  | revise
  | stop
  | escalate
  deriving DecidableEq, Repr

-- SKILL[def]: "At this existing `meta_judge` implement-exit gate, reflect on whether the goal or harness changed and whether current evidence has overturned the direction."
structure ReflectionOutcome where
  action : ReflectionAction
  responsibleParty : String
  deriving DecidableEq, Repr

-- SKILL[def]: "Emit exactly one concrete action with its responsible party: `continue`, `revise`, `stop`, or `escalate`."
/-- The gate returns exactly one outcome; the type has room for no second action. -/
def reflect (r : ReflectionInputs) (party : String) : ReflectionOutcome :=
  if r.goalChanged || r.harnessChanged then ⟨.revise, party⟩
  else if r.evidenceOverturnedDirection then ⟨.stop, party⟩
  else ⟨.continue, party⟩

/-! ## The focused round -/

/-- A seat's dedicated-domain objection with its falsifiable causal prediction. -/
structure Objection where
  seat : Lens
  inSeatExclusiveDomain : Bool
  causalPredictionFalsifiable : Bool
  convergenceAnsweredChain : Bool
  input : Input
  ownWords : String
  deriving DecidableEq, Repr

-- SKILL[def]: "The three conditions must all hold simultaneously: the objection recorded in that conclusion is in that seat's exclusive domain (for example, mechanism necessity for `parsimony`, purpose-forced form for `teleology`, or cost worth for `worth`); the causal prediction recorded in that conclusion is falsifiable rather than a preference; and the meta-judge's proposed convergence has not answered that causal chain, including when it answers only a secondary point."
def threeConditions (o : Objection) : Bool :=
  o.inSeatExclusiveDomain && o.causalPredictionFalsifiable && !o.convergenceAnsweredChain

-- SKILL[def]: "When a seat's `SshxResultEnvelope.conclusion` records both a dedicated-domain objection and its falsifiable causal prediction, and the meta-judge's proposed convergence has not refuted that causal chain, the meta-judge must run a `FocusedRound` before converging, provided the objection passes `BlockingAuthority`."
def focusedRoundRequired (o : Objection) : Bool :=
  threeConditions o && (force o.input == .blocking)

-- SKILL[thm]: "An advisory objection does not trigger a `FocusedRound`; for this prerequisite, the meta-judge checks only whether the seat named both conjuncts and must not assess their persuasiveness."
theorem advisory_never_triggers (o : Objection) (h : force o.input = .advisory) :
    focusedRoundRequired o = false := by
  simp [focusedRoundRequired, h]

-- SKILL[thm]: "An objection that named a basis whose correctness is disputed still triggers the round because disputed is not absent."
theorem disputed_still_triggers (o : Objection) (b : Bool) :
    focusedRoundRequired { o with input := { o.input with basisDisputed := b } } =
      focusedRoundRequired o := by
  simp [focusedRoundRequired, threeConditions, force]

-- SKILL[def]: "When the meta-judge declines a round on this ground, it records that decline in the existing `finding_downgrades` record under the same own-words requirement that governs downgrades."
def declineRecord (o : Objection) : Option DowngradeRecord :=
  if focusedRoundRequired o then none else downgradeRecord o.input o.ownWords

-- SKILL[def]: "In the focused round, all seats independently answer one question: "Does this causal chain hold, and if it does, how should the plan change?" The round preserves `## No Context Pollution`."
def focusedRoundQuestion : String :=
  "Does this causal chain hold, and if it does, how should the plan change?"

/-- Rounds already run for one causal chain, and whether sealed inputs changed independently. -/
structure ChainState where
  roundsRun : Nat
  independentlyChangedSealedInputs : Bool
  differentGroundedObligation : Bool
  deriving DecidableEq, Repr

inductive ChainRoute
  | runFocusedRound
  | escalateToMaintainer
  | differentCausalChain
  deriving DecidableEq, Repr

-- SKILL[def]: "A causal chain triggers at most one focused round."
def chainRoute (c : ChainState) : ChainRoute :=
  if c.roundsRun = 0 then .runFocusedRound
  else if c.independentlyChangedSealedInputs && c.differentGroundedObligation then
    .differentCausalChain
  else .escalateToMaintainer

theorem at_most_one_round (c : ChainState) (h : 1 ≤ c.roundsRun) :
    chainRoute c ≠ .runFocusedRound := by
  unfold chainRoute
  have hne : c.roundsRun ≠ 0 := by omega
  simp only [hne, if_false]
  split <;> simp

-- SKILL[thm]: "If disagreement on that chain remains afterward, escalate to the maintainer rather than run it again."
theorem remaining_disagreement_escalates (c : ChainState) (h : c.roundsRun = 1)
    (hi : c.independentlyChangedSealedInputs = false) : chainRoute c = .escalateToMaintainer := by
  simp [chainRoute, h, hi]

-- SKILL[def]: "A later round is a genuine reopening only when independently changed sealed inputs — external new evidence, or an authorized `GoalArtifact` or harness correction, but never conclusions generated by the completed round itself — create a different grounded obligation."
inductive SealedInputChange
  | externalNewEvidence
  | authorizedGoalOrHarnessCorrection
  | conclusionsOfTheCompletedRound
  deriving DecidableEq, Repr

def independentChange : SealedInputChange → Bool
  | .externalNewEvidence | .authorizedGoalOrHarnessCorrection => true
  | .conclusionsOfTheCompletedRound => false

-- SKILL[thm]: "Replaying the same causal chain cannot reopen."
theorem replay_cannot_reopen (c : ChainState) (h : 1 ≤ c.roundsRun)
    (hi : c.independentlyChangedSealedInputs = false) : chainRoute c ≠ .runFocusedRound :=
  at_most_one_round c h

/-- A grounded conflict and its resolution. -/
structure GroundedConflict where
  chain : String
  resolved : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "The meta-judge records every grounded conflict and its resolution; presentation format is non-normative, and only an unresolved grounded conflict blocks `implement`."
def unresolvedConflict (conflicts : List GroundedConflict) : Bool :=
  conflicts.any fun c => !c.resolved

theorem only_unresolved_conflict_blocks (d : DesignInputs) (conflicts : List GroundedConflict)
    (h : unresolvedConflict conflicts = true)
    (hd : d.unresolvedGroundedConflict = unresolvedConflict conflicts) :
    designRoute d ≠ .implement :=
  no_implement_with_unresolved_conflict d (by rw [hd, h])

-- SKILL[ref]: "An objection that fails `BlockingAuthority` is not an unclosed `GoalArtifact` goal gap and does not by itself hold the exit out of `implement`: the meta-judge records it as advisory in the existing `finding_downgrades` record as `BlockingAuthority` requires."
abbrev advisoryObjectionRecord := @downgradeRecord

-- SKILL[ref]: "Disputed grounding stays blocking."
abbrev disputedStaysBlocking := @force_ignores_dispute

-- SKILL[ref]: "This is not permission to set aside a reachable defect."
abbrev reachableDefectStaysBlocking := @actual_defect_stays_blocking

/-! ## The convergence question and orientation -/

-- SKILL[def]: "The convergence question must be "what still differs from `GoalArtifact`?" expressed against the fixed normalized goal, constraints, and success criteria."
def convergenceQuestion : String := "what still differs from GoalArtifact?"

-- SKILL[def]: "Do not generalize the convergence pass beyond that goal gap."
/-- The convergence pass ranges over the goal gap only. -/
def convergencePassScope (bases : List Input) : List Input := goalGapOf bases

theorem convergence_scope_is_the_goal_gap (bases : List Input) :
    convergencePassScope bases = goalGapOf bases := rfl

/-- Whether an owner-sourced, versioned, scoped orientation is recorded. -/
inductive OrientationStatus
  | recorded
  | missing
  | disputed
  deriving DecidableEq, Repr

-- SKILL[def]: "Choosing among incomparable candidates requires an owner-sourced, versioned, scoped orientation recorded in `GoalArtifact` or assigned through `harness.decision_ownership`."
def canChooseAmongIncomparable (o : OrientationStatus) : Bool := o == .recorded

-- SKILL[thm]: "A missing or disputed orientation is `ASSUMED-UNVERIFIED` and cannot support convergence."
theorem missing_or_disputed_orientation_cannot_converge (o : OrientationStatus)
    (h : o ≠ .recorded) : canChooseAmongIncomparable o = false := by
  cases o <;> simp_all [canChooseAmongIncomparable]

end Sshx.Reasoning
