/-!
# Flight records and the fail-closed completion predicate

Source: `SshxWorkerFlightRecord` in `## Worker Delegation` and the one predicate of
`## Worker Completion Contract`.
-/

namespace Sshx

/-- `SshxWorkerFlightRecord.status`. -/
inductive FlightStatus
  | inFlight
  | retrying
  | terminal
  | abstained
  deriving DecidableEq, Repr

/-- The five observables the completion predicate admits. The predicate has no other
inputs, so stdout text, log tails, status files, batch reports, and process snapshots
are not completion evidence by construction, not by enumeration. -/
structure Observation where
  carrierExited : Bool
  exitZero : Bool
  envelopeValid : Bool
  verdictAllowed : Bool
  sentinelPresent : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "completion and verdict routing use one fail-closed predicate"
/-- `## Worker Completion Contract`: the single fail-closed completion predicate. -/
def done (o : Observation) : Bool :=
  o.carrierExited && o.exitZero && o.envelopeValid && o.verdictAllowed && o.sentinelPresent

theorem done_iff (o : Observation) :
    done o = true ↔
      o.carrierExited = true ∧ o.exitZero = true ∧ o.envelopeValid = true ∧
        o.verdictAllowed = true ∧ o.sentinelPresent = true := by
  cases o with
  | mk a b c d e => cases a <;> cases b <;> cases c <;> cases d <;> cases e <;> simp [done]

/-- Fail-closed: any one observable missing means not done. -/
theorem not_done_of_running (o : Observation) (h : o.carrierExited = false) : done o = false := by
  simp [done, h]

theorem not_done_of_nonzero_exit (o : Observation) (h : o.exitZero = false) : done o = false := by
  simp [done, h]

theorem not_done_of_invalid_envelope (o : Observation) (h : o.envelopeValid = false) :
    done o = false := by
  simp [done, h]

theorem not_done_of_disallowed_verdict (o : Observation) (h : o.verdictAllowed = false) :
    done o = false := by
  simp [done, h]

theorem not_done_of_missing_sentinel (o : Observation) (h : o.sentinelPresent = false) :
    done o = false := by
  simp [done, h]

-- SKILL[def]: "`retry_budget` is a finite integer decided before the first launch for that flight"
/-- Same-carrier retry accounting for one flight. `retryBudget` is fixed before the first
launch; `attempt` counts launches already made. -/
structure Flight where
  retryBudget : Nat
  attempt : Nat
  status : FlightStatus
  deriving DecidableEq, Repr

/-- What the caller learns when a carrier attempt ends. -/
inductive AttemptOutcome
  | complete
  | failed
  deriving DecidableEq, Repr

/-- After an attempt ends: complete is terminal; a failure retries while the same-carrier
budget has capacity and otherwise exhausts the flight (`abstained`), after which only the
fallback rule in `Carrier` may open a new flight. -/
def Flight.step (f : Flight) : AttemptOutcome → Flight
  | .complete => { f with status := .terminal }
  | .failed =>
    if f.attempt < f.retryBudget then
      { f with attempt := f.attempt + 1, status := .retrying }
    else
      { f with status := .abstained }

theorem Flight.attempt_le_budget (f : Flight) (h : f.attempt ≤ f.retryBudget)
    (o : AttemptOutcome) : (f.step o).attempt ≤ (f.step o).retryBudget := by
  cases o with
  | complete => simpa [Flight.step] using h
  | failed =>
    simp only [Flight.step]
    split <;> simp <;> omega

/-- A failed attempt with an exhausted budget is `abstained`, never terminal. -/
theorem Flight.exhausted_abstains (f : Flight) (h : ¬ f.attempt < f.retryBudget) :
    (f.step .failed).status = .abstained := by
  simp [Flight.step, h]

/-- Terminal status arises only from a complete attempt. -/
theorem Flight.terminal_only_from_complete (f : Flight) (o : AttemptOutcome)
    (h : (f.step o).status = .terminal) : o = .complete := by
  cases o with
  | complete => rfl
  | failed =>
    simp only [Flight.step] at h
    split at h <;> simp_all

end Sshx
