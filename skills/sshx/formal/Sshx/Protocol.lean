import Sshx.Carrier

/-!
# Stage order

Source: `## InlineConsensusProtocol` and the abstain rule of `## Worker Delegation`.
-/

namespace Sshx

/-- The seven stages in their fixed order. -/
inductive Stage
  | intake
  | chooseWorkerMode
  | thinkingPanel
  | metaJudge
  | implementation
  | reviewTriplet
  | fixOrDone
  deriving DecidableEq, Repr

-- SKILL[def]: "Run the stages in this exact order:"
def Stage.next : Stage → Option Stage
  | .intake => some .chooseWorkerMode
  | .chooseWorkerMode => some .thinkingPanel
  | .thinkingPanel => some .metaJudge
  | .metaJudge => some .implementation
  | .implementation => some .reviewTriplet
  | .reviewTriplet => some .fixOrDone
  | .fixOrDone => none

def Stage.order : List Stage :=
  [.intake, .chooseWorkerMode, .thinkingPanel, .metaJudge, .implementation, .reviewTriplet,
    .fixOrDone]

theorem Stage.order_is_next :
    ∀ i, i < 6 → Stage.order[i]?.bind Stage.next = Stage.order[i + 1]? := by
  decide

/-- What ends a stage: proceed to the next, or terminate the protocol. -/
inductive StageOutcome
  | proceed
  | abstain
  deriving DecidableEq, Repr

-- SKILL[def]: "When `WorkerMode` resolves to `abstain`, the protocol terminates at `choose_worker_mode`"
/-- `WorkerModeGate`: when the resolved mode is `abstain`, the protocol terminates at
`choose_worker_mode` and no later stage runs. -/
def afterModeSelection : WorkerMode → Option Stage
  | .carrier _ => some .thinkingPanel
  | .abstain => none

theorem abstain_runs_no_later_stage : afterModeSelection .abstain = none := rfl

/-- The termination gate is a conditional subgate of `fix_or_done`, never a stage. -/
theorem termination_gate_not_a_stage : Stage.order.length = 7 := rfl

end Sshx
