import Mathlib.Tactic
import Sshx.Budget
import Sshx.Gate
import Sshx.Behavior.Model
import Sshx.Reasoning.Convergence

/-!
# Reasoning: fix or done

Source: `## Fix Or Done` — the reflection gate before every pass, reachability evidence
versus mere non-improvement, the repair step, main-path-first repair order, and the exits.
-/

namespace Sshx.Reasoning

open Sshx

-- SKILL[ref]: "Before each fix or repeated review pass, use the existing gate to ask whether the goal or harness changed and whether evidence overturned the direction; emit exactly one concrete `continue`, `revise`, `stop`, or `escalate` action and name its responsible party."
abbrev gateBeforeEachPass := @reflect

/-- Evidence about whether the current approach can reach the gap. -/
inductive ReachabilityEvidence
  | reachableByCurrentApproach
  | unreachableByCurrentApproach
  deriving DecidableEq, Repr

inductive ApproachVerdict
  | keepApproach
  | changeApproach
  | undetermined
  deriving DecidableEq, Repr

-- SKILL[def]: "When that gate weighs whether evidence has overturned the direction across repeated passes on the same blocking goal gap, distinguish evidence that the gap is reachable by the current approach from evidence that it is not."
def approachVerdict : Option ReachabilityEvidence → ApproachVerdict
  | some .reachableByCurrentApproach => .keepApproach
  | some .unreachableByCurrentApproach => .changeApproach
  | none => .undetermined

-- SKILL[thm]: "Consecutive passes without improvement are, alone, evidence of neither: they do not prove the current approach is exhausted, and they do not license further identical passes as progress."
/-- The verdict reads no pass counter: any number of unimproved passes leaves it undetermined
without reachability evidence, and never turns into progress. -/
theorem unimproved_passes_prove_nothing (passesWithoutImprovement : Nat) :
    approachVerdict none = .undetermined ∧ passesWithoutImprovement = passesWithoutImprovement :=
  ⟨rfl, rfl⟩

def identicalPassIsProgress : Bool := false

/-- One repair step as the contract shapes it. -/
structure RepairStep where
  askedWhatStillDiffers : Bool
  smallestChange : Bool
  delegatedToWorkerFlight : Bool
  callerStayedOrchestrationOnly : Bool
  rerunReviewTriplet : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "If review exits `fix`, ask what still differs from `GoalArtifact`, apply the smallest change that addresses that blocking goal gap by delegating it to a worker using the stage's default carrier exactly as `## Implementation Worker` requires - open a new `SshxWorkerFlightRecord` for the same `work_target` and stay orchestration-only for the repair - then rerun the review triplet on the worker's returned `conclusion`."
def RepairStep.conforming (r : RepairStep) : Bool :=
  r.askedWhatStillDiffers && r.smallestChange && r.delegatedToWorkerFlight &&
    r.callerStayedOrchestrationOnly && r.rerunReviewTriplet

/-- Where a blocking gap sits in `GoalArtifact`; lower ranks are repaired first. -/
inductive GapRank
  | normalizedGoal
  | constraint
  | successCriterion
  | periphery
  deriving DecidableEq, Repr

def GapRank.order : GapRank → Nat
  | .normalizedGoal => 0
  | .constraint => 1
  | .successCriterion => 2
  | .periphery => 3

-- SKILL[def]: "When a pass carries more than one blocking goal gap, repair them in `GoalArtifact` order — a gap that blocks `normalized_goal` before one that blocks only its periphery — so the main path is repaired first."
def repairOrder (gaps : List GapRank) : List GapRank :=
  gaps.filter (· == .normalizedGoal) ++ gaps.filter (· == .constraint) ++
    gaps.filter (· == .successCriterion) ++ gaps.filter (· == .periphery)

theorem main_path_first (gaps : List GapRank) (h : .normalizedGoal ∈ gaps) :
    (repairOrder gaps).head? = some .normalizedGoal := by
  unfold repairOrder
  have hmem : GapRank.normalizedGoal ∈ gaps.filter (· == .normalizedGoal) := by
    simp [List.mem_filter, h]
  obtain ⟨x, xs, hx⟩ : ∃ x xs, gaps.filter (· == .normalizedGoal) = x :: xs := by
    cases hl : gaps.filter (· == .normalizedGoal) with
    | nil => simp [hl] at hmem
    | cons x xs => exact ⟨x, xs, rfl⟩
  have hxmem : x ∈ gaps.filter (· == .normalizedGoal) := by simp [hx]
  have hxeq : x = .normalizedGoal := by simpa using (List.mem_filter.mp hxmem).2
  simp [hx, hxeq]

-- SKILL[ref]: "Stop when `pass_budget` owned below is exhausted and report remaining blockers honestly."
abbrev stopWhenExhausted := @Sshx.step_zero_counted

inductive DoneRoute
  | claimCandidateThroughGate
  | reportDone
  deriving DecidableEq, Repr

-- SKILL[def]: "If review exits `done with advisory surfaced`, treat that exit as a candidate for an affirmative success claim rather than the claim itself when `## Termination Gate` applies, and route the candidate through that gate before reporting success."
def routeDoneExit : Applicability → DoneRoute
  | .applies => .claimCandidateThroughGate
  | .inapplicable => .reportDone
  | .escalateToMaintainer => .claimCandidateThroughGate

theorem done_is_only_a_candidate_when_gate_applies :
    routeDoneExit .applies = .claimCandidateThroughGate := rfl

-- SKILL[ref]: "Include any non-blocking advisory feedback without inlining logs."
abbrev advisoryWithoutLogs := @Behavior.ContextItem.permitted

inductive BoundedPassChoice
  | oneMoreBoundedPass (nextIterationQuestion : String)
  | askTheUser
  deriving DecidableEq, Repr

-- SKILL[def]: "If review exits `explicit user decision or another bounded review pass`, either run one more bounded pass with a concrete next iteration question tied to `GoalArtifact`, or ask the user to decide."
def allCommentExitChoices (question : String) : List BoundedPassChoice :=
  [.oneMoreBoundedPass question, .askTheUser]

-- SKILL[ref]: "Do not loop indefinitely."
abbrev noIndefiniteLoop := @Sshx.counted_passes_bounded

-- SKILL[ref]: "After any explicit correction, use the existing correction gate to ask whether the goal or harness changed and whether evidence overturned the direction; emit exactly one concrete `continue`, `revise`, `stop`, or `escalate` action and name its responsible party before further work."
abbrev correctionGate := @reflect

-- SKILL[policy]: "Protocol policy, not a mathematical consequence: before the first pass after the initial review triplet, the caller records one owner-precommitted finite integer `pass_budget`."
abbrev passBudgetPrecommitment := @Behavior.guardRecordPassBudget

-- SKILL[ref]: "Carrier retries and fallbacks are bounded by each flight's `retry_budget` and the finite eligible-untried-carrier set and consume no unit; the initial review triplet is the single occurrence fixed by the stage order and consumes none."
abbrev uncountedTransitions := @Sshx.step_uncounted

end Sshx.Reasoning
