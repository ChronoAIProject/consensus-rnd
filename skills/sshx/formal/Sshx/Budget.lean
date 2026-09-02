/-!
# `pass_budget`

Source: `## Fix Or Done` (sole owner) and the charging sentences of `## Termination Gate`.
One precommitted natural number; every counted pass decrements it; nothing refunds it.
-/

namespace Sshx

/-- Transitions after the initial review triplet. The first five are the declared pass
classes; the last three are bounded elsewhere and consume no unit. -/
inductive Transition
  | metaLayerConvergence
  | focusedRound
  | repairWithRerunReview
  | repeatedReviewPass
  | terminationGateEvaluation
  | carrierRetry
  | carrierFallback
  | initialReviewTriplet
  deriving DecidableEq, Repr

def Transition.counted : Transition → Bool
  | .metaLayerConvergence | .focusedRound | .repairWithRerunReview
  | .repeatedReviewPass | .terminationGateEvaluation => true
  | .carrierRetry | .carrierFallback | .initialReviewTriplet => false

-- SKILL: "consumes exactly one unit when it is dispatched"
/-- One step: a counted pass needs a unit and consumes it at dispatch; `none` is
"no pass authority". -/
def step (budget : Nat) (t : Transition) : Option Nat :=
  if t.counted then (if budget = 0 then none else some (budget - 1)) else some budget

-- SKILL: "A run with no recorded `pass_budget` has no pass authority"
/-- A run consumes transitions in order; the first refused pass stops the run. -/
def run : Nat → List Transition → Option Nat
  | b, [] => some b
  | b, t :: ts =>
    match step b t with
    | none => none
    | some b' => run b' ts

/-- Units never increase: no result, repair, or correction can add, replenish, reset, or
replace units. -/
theorem step_le (b : Nat) (t : Transition) (b' : Nat) (h : step b t = some b') : b' ≤ b := by
  unfold step at h
  split at h
  · split at h
    · exact absurd h (by simp)
    · simp at h; omega
  · simp at h; omega

/-- A counted pass with no remaining unit has no pass authority. -/
theorem step_zero_counted (t : Transition) (h : t.counted = true) : step 0 t = none := by
  simp [step, h]

/-- Uncounted transitions never touch the budget. -/
theorem step_uncounted (b : Nat) (t : Transition) (h : t.counted = false) : step b t = some b := by
  simp [step, h]

/-- Exactly one unit per counted pass. -/
theorem step_counted (b : Nat) (t : Transition) (h : t.counted = true) (hb : 0 < b) :
    step b t = some (b - 1) := by
  simp [step, h]
  omega

/-- Termination: a run that keeps pass authority performed at most `b` counted passes.
The budget is a strictly decreasing natural number along counted passes. -/
theorem counted_le_budget (ts : List Transition) :
    ∀ b b', run b ts = some b' → (ts.filter Transition.counted).length + b' ≤ b := by
  induction ts with
  | nil => intro b b' h; simp [run] at h; simp [h]
  | cons t ts ih =>
    intro b b' h
    simp only [run] at h
    cases hs : step b t with
    | none => simp [hs] at h
    | some b1 =>
      simp [hs] at h
      have hle := ih b1 b' h
      unfold step at hs
      by_cases hc : t.counted = true
      · simp [hc] at hs
        by_cases hb : b = 0
        · simp [hb] at hs
        · simp [hb] at hs
          simp [List.filter, hc]
          omega
      · have hc' : t.counted = false := by simpa using hc
        simp [hc'] at hs
        simp [List.filter, hc']
        omega

/-- With budget `b`, no run performs more than `b` counted passes. -/
theorem counted_passes_bounded (b : Nat) (ts : List Transition) (b' : Nat)
    (h : run b ts = some b') : (ts.filter Transition.counted).length ≤ b := by
  have := counted_le_budget ts b b' h
  omega

end Sshx
