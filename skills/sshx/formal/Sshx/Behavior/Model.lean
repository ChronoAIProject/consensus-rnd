import Sshx.Carrier
import Sshx.Flight
import Sshx.Budget
import Sshx.Gate
import Sshx.Tables
import Sshx.Records
import Sshx.Protocol

/-!
# Behavior: the caller's operational model

The contract's "must" and "must not" sentences are guards on caller actions; its state
vocabulary is `ProtocolState`; `step` is the effect of an allowed action. Safety
properties are proven over every reachable state in `Sshx.Behavior.Invariant`.

Each guard is its own definition, traced to the clause it models, and `allowed` is their
conjunction per action. An action whose guard is `False` is never taken by a conforming
caller; that is the model of "must not".
-/

namespace Sshx.Behavior

open Sshx

/-- Seat and worker roles named by the contract. -/
inductive Role
  | teleology
  | parsimony
  | fidelity
  | naturalOwnership
  | proportionalContainment
  | worth
  | implementation
  | architecture
  | quality
  | tests
  | criterionEvidence
  | residualGap
  | claimIntegrity
  deriving DecidableEq, Repr

/-- The runner's stage enumeration for a flight. -/
inductive FlightStage
  | thinking
  | implementation
  | review
  | termination
  deriving DecidableEq, Repr

/-- What the caller context could carry. Only the first five are permitted. -/
inductive ContextItem
  | intakeInput
  | brief (flight : Nat)
  | conclusion (flight : Nat)
  | logRef (flight : Nat)
  | finalReport
  | fullLog (flight : Nat)
  | workerReasoning (flight : Nat)
  | peerOutput (flight : Nat)
  deriving DecidableEq, Repr

-- SKILL[def]: "The caller context must not carry worker full reasoning or same-round peer outputs."
-- SKILL[def]: "- intake inputs and constraints;"
-- SKILL[def]: "- dispatch briefs sent to each worker;"
-- SKILL[def]: "- `SshxResultEnvelope.conclusion` values, including verdicts and explicitly surfaced blockers;"
-- SKILL[def]: "- `SshxResultEnvelope.log_ref` artifact references;"
-- SKILL[def]: "- final reports that aggregate conclusions only."
/-- `## No Context Pollution`: the closed list of what the caller context may carry. -/
def ContextItem.permitted : ContextItem → Bool
  | .intakeInput | .brief _ | .conclusion _ | .logRef _ | .finalReport => true
  | .fullLog _ | .workerReasoning _ | .peerOutput _ => false

/-- Lifecycle operations the skill never grants. -/
inductive LifecycleOp
  | commit
  | push
  | merge
  | closeIssue
  | editLabel
  | publishRelease
  | mutateExternalState
  deriving DecidableEq, Repr

/-- One flight as the caller records it (`SshxWorkerFlightRecord` plus launch bookkeeping). -/
structure FlightRec where
  id : Nat
  stage : FlightStage
  role : Role
  carrier : Carrier
  target : String
  status : FlightStatus
  retryBudget : Nat
  attempt : Nat
  envelopeRef : Option String
  sentinelRef : Option String
  launched : Bool
  notified : Bool
  deriving DecidableEq, Repr

def FlightRec.active (f : FlightRec) : Bool :=
  f.status == .inFlight || f.status == .retrying

/-- The caller-side protocol state. -/
structure ProtocolState where
  stage : Stage
  goal : Option GoalArtifact
  mode : Option WorkerMode
  capabilityChecked : List Carrier
  flights : List FlightRec
  context : List ContextItem
  passBudget : Option Nat
  gate : Applicability
  terminationExit : Option TerminationExit
  claimed : Bool
  /-- Every target mutation with whether a flight on that target was active at that moment. -/
  mutationLog : List (String × Bool)
  deriving Repr

def ProtocolState.initial : ProtocolState :=
  { stage := .intake, goal := none, mode := none, capabilityChecked := [], flights := [],
    context := [], passBudget := none, gate := .inapplicable, terminationExit := none,
    claimed := false, mutationLog := [] }

/-- Every caller-side act the contract speaks about. `hostNotified` is an environment event. -/
inductive Action
  | inspectReadOnly
  | writeGoal (g : GoalArtifact)
  | appendRevision (r : Revision)
  | capabilityCheck (c : Carrier)
  | resolveMode (m : WorkerMode)
  | openFlight (stage : FlightStage) (role : Role) (carrier : Carrier) (target : String) (retryBudget : Nat)
  | launchViaRunner (flight : Nat)
  | launchViaShellBackground (flight : Nat)
  | pollArtifacts (flight : Nat)
  | hostNotified (flight : Nat)
  | collect (flight : Nat) (o : Observation)
  | fallbackFlight (flight : Nat) (carrier : Carrier)
  | mutateTarget (target : String)
  | carry (item : ContextItem)
  | recordPassBudget (units : Nat)
  | pass (t : Transition)
  | advanceStage
  | declareGate (a : Applicability)
  | evaluateTermination (source : ClaimSource) (roster : Roster)
  | claimSatisfied
  | lifecycle (op : LifecycleOp)
  | oracleReference (url : String) (isPublic : Bool) (pinned : Bool)
  | publishToMakeLinkable
  deriving DecidableEq, Repr

/-! ## State projections -/

def ProtocolState.goalWritten (s : ProtocolState) : Prop := s.goal.isSome = true
def ProtocolState.modeResolved (s : ProtocolState) : Prop := s.mode.isSome = true
def ProtocolState.abstained (s : ProtocolState) : Prop := s.mode = some .abstain

def ProtocolState.activeOn (s : ProtocolState) (target : String) : Bool :=
  s.flights.any fun f => f.target == target && f.active

def ProtocolState.flight (s : ProtocolState) (id : Nat) : Option FlightRec :=
  s.flights.find? fun f => f.id == id

def ProtocolState.freshId (s : ProtocolState) : Nat :=
  s.flights.length

/-- `harness` is complete when every sub-item is non-empty. -/
def Harness.complete (h : Harness) : Prop :=
  h.providedCapabilities ≠ [] ∧ h.trustBoundary ≠ "" ∧ h.decisionOwnership ≠ ""

/-- The stage a flight stage belongs to. -/
def FlightStage.protocolStage : FlightStage → Stage
  | .thinking => .thinkingPanel
  | .implementation => .implementation
  | .review => .reviewTriplet
  | .termination => .fixOrDone

/-! ## Guards, one per clause -/

-- SKILL[guard]: "During `intake`, the caller may use its own read-only tools to inspect the user's input and write `GoalArtifact`; this caller-owned read-only intake is not worker dispatch."
def guardInspect (s : ProtocolState) : Prop := s.stage = .intake

-- SKILL[guard]: "`GoalArtifact` is written during `intake` before worker mode selection or any worker dispatch."
def guardWriteGoal (s : ProtocolState) (g : GoalArtifact) : Prop :=
  s.stage = .intake ∧ s.goal = none ∧ s.mode = none ∧ s.flights = [] ∧ Harness.complete g.harness

-- SKILL[guard]: "Any explicit correction to `GoalArtifact` or `harness` must append one such revision item before routing continues."
def guardAppendRevision (s : ProtocolState) : Prop := s.goalWritten

-- SKILL[guard]: "Its capability check may confirm that a Codex CLI worker can be invoked, but it is non-mutating: everywhere in this contract, non-mutating means it changes no file, Git state, GitHub state, label, release, host configuration, lifecycle state, or other external resource."
def guardCapabilityCheck (s : ProtocolState) : Prop :=
  s.goalWritten ∧ s.stage = .chooseWorkerMode

-- SKILL[guard]: "Before any worker dispatch, including delegated intake context-gathering by subagent, Agent, Task, or codex, the caller must complete the non-mutating `codex-cli` capability check and resolve `WorkerMode`."
def guardResolveMode (s : ProtocolState) (m : WorkerMode) : Prop :=
  s.goalWritten ∧ s.stage = .chooseWorkerMode ∧ s.mode = none ∧
    Carrier.codexCli ∈ s.capabilityChecked ∧
    (m = .abstain ∨ ∃ c, m = .carrier c ∧ c ∈ s.capabilityChecked)

-- SKILL[guard]: "`WorkerModeGate` requires resolution before dispatch."
def guardOpenFlight (s : ProtocolState) (stage : FlightStage) (carrier : Carrier) : Prop :=
  s.modeResolved ∧ ¬ s.abstained ∧ s.stage = stage.protocolStage ∧
    carrier ∈ s.capabilityChecked

-- SKILL[guard]: "Every formal `codex-cli` flight must use this runner rather than a parallel direct-launch path."
def guardLaunchViaRunner (s : ProtocolState) (id : Nat) : Prop :=
  ∃ f, s.flight id = some f ∧ f.launched = false ∧ f.carrier = .codexCli

-- SKILL[guard]: "It must not use shell `&` to background the runner, because that detaches the process from host tracking and can leave an init-adopted carrier running without ever notifying the caller of completion."
def guardNoShellBackground : Prop := False

-- SKILL[guard]: "The caller must not poll worker artifact paths while the runner is active."
def guardNoPolling : Prop := False

-- SKILL[guard]: "The caller must launch the runner through a host-provided background job mechanism that notifies the caller when the carrier process exits."
def guardHostNotified (s : ProtocolState) (id : Nat) : Prop :=
  ∃ f, s.flight id = some f ∧ f.launched = true

-- SKILL[guard]: "The caller may invoke `skills/sshx/scripts/read-codex-worker-status.sh` only after host completion notification."
def guardCollect (s : ProtocolState) (id : Nat) : Prop :=
  ∃ f, s.flight id = some f ∧ f.notified = true ∧ f.active = true

-- SKILL[guard]: "If any flight lacks terminal completion after its finite same-carrier retry budget is exhausted, the caller marks that flight `abstained` with empty `result_envelope_ref` and `completion_sentinel_ref`."
def guardFallback (s : ProtocolState) (id : Nat) (carrier : Carrier) : Prop :=
  ∃ f, s.flight id = some f ∧ f.status = .abstained ∧ carrier ≠ f.carrier ∧
    carrier ∈ s.capabilityChecked

-- SKILL[guard]: "While any `SshxWorkerFlightRecord` for the same `work_target` is `in-flight` or `retrying`, the caller is read-only for that target."
def guardMutateTarget (s : ProtocolState) (target : String) : Prop :=
  s.activeOn target = false

-- SKILL[guard]: "It may carry only:"
def guardCarry (item : ContextItem) : Prop := item.permitted = true

-- SKILL[guard]: "This section is the sole owner of `pass_budget`."
def guardRecordPassBudget (s : ProtocolState) : Prop :=
  s.stage = .fixOrDone ∧ s.passBudget = none

-- SKILL[guard]: "The budget is immutable for this run: no result, repair, or correction may add, replenish, reset, or replace units, and a unit is never refunded."
def guardPass (s : ProtocolState) (t : Transition) : Prop :=
  s.stage = .fixOrDone ∧ (t.counted = true → ∃ b, s.passBudget = some b ∧ 0 < b)

def guardAdvanceStage (s : ProtocolState) : Prop :=
  s.stage.next.isSome = true ∧ s.modeResolved ∧ ¬ s.abstained ∧
    s.flights.all (fun f => !f.active) = true

-- SKILL[guard]: "The boundary owner may declare a host-provided goal-driven continuation mechanism only in `harness.provided_capabilities`; the skill must not discover or infer whether one exists."
def guardDeclareGate (s : ProtocolState) : Prop := s.goalWritten

-- SKILL[guard]: "`## Termination Gate` is a conditional subgate reached inside `fix_or_done`, never an additional `InlineConsensusProtocol` stage."
def guardEvaluateTermination (s : ProtocolState) : Prop :=
  s.stage = .fixOrDone ∧ s.gate = .applies ∧ ∃ b, s.passBudget = some b ∧ 0 < b

-- SKILL[guard]: "It applies only when `## Goal Contract` supplies its positive, boundary-owner-confirmed `harness.provided_capabilities` entry and the caller is about to assert that `GoalArtifact` is satisfied."
def guardClaimSatisfied (s : ProtocolState) : Prop :=
  s.stage = .fixOrDone ∧ s.gate ≠ .escalateToMaintainer ∧
    (s.gate = .applies → s.terminationExit = some .claimPermitted)

-- SKILL[guard]: "`sshx` does not grant permission to commit, push, merge, close issues, edit labels, publish releases, or mutate external lifecycle state."
def guardNoLifecycle : Prop := False

-- SKILL[guard]: "Such a URL is permitted only when the referenced content is already anonymously readable on the remote, which the caller confirms before the first submission; the caller must never push, publish, change repository visibility, or otherwise mutate remote state to make content linkable."
def guardOracleReference (isPublic pinned : Bool) : Prop := isPublic = true ∧ pinned = true

def guardNoPublishToLink : Prop := False

/-- The conjunction of the clause guards, per action. -/
def allowed (s : ProtocolState) : Action → Prop
  | .inspectReadOnly => guardInspect s
  | .writeGoal g => guardWriteGoal s g
  | .appendRevision _ => guardAppendRevision s
  | .capabilityCheck _ => guardCapabilityCheck s
  | .resolveMode m => guardResolveMode s m
  | .openFlight stage _ carrier _ _ => guardOpenFlight s stage carrier
  | .launchViaRunner id => guardLaunchViaRunner s id
  | .launchViaShellBackground _ => guardNoShellBackground
  | .pollArtifacts _ => guardNoPolling
  | .hostNotified id => guardHostNotified s id
  | .collect id _ => guardCollect s id
  | .fallbackFlight id carrier => guardFallback s id carrier
  | .mutateTarget target => guardMutateTarget s target
  | .carry item => guardCarry item
  | .recordPassBudget _ => guardRecordPassBudget s
  | .pass t => guardPass s t
  | .advanceStage => guardAdvanceStage s
  | .declareGate _ => guardDeclareGate s
  | .evaluateTermination _ _ => guardEvaluateTermination s
  | .claimSatisfied => guardClaimSatisfied s
  | .lifecycle _ => guardNoLifecycle
  | .oracleReference _ isPublic pinned => guardOracleReference isPublic pinned
  | .publishToMakeLinkable => guardNoPublishToLink

/-! ## Effects -/

def updateFlight (s : ProtocolState) (id : Nat) (f : FlightRec → FlightRec) : ProtocolState :=
  { s with flights := s.flights.map fun x => if x.id == id then f x else x }

/-- Collecting an observation applies the completion predicate and the same-carrier retry rule. -/
def collectEffect (o : Observation) (f : FlightRec) : FlightRec :=
  if done o then
    { f with status := .terminal, envelopeRef := some "result", sentinelRef := some "sentinel" }
  else if f.attempt < f.retryBudget then
    { f with attempt := f.attempt + 1, status := .retrying, launched := false, notified := false,
             envelopeRef := none, sentinelRef := none }
  else
    { f with status := .abstained, envelopeRef := none, sentinelRef := none }

/-- A freshly opened flight record. -/
def newFlight (id : Nat) (stage : FlightStage) (role : Role) (carrier : Carrier)
    (target : String) (retryBudget : Nat) : FlightRec where
  id := id
  stage := stage
  role := role
  carrier := carrier
  target := target
  status := .inFlight
  retryBudget := retryBudget
  attempt := 0
  envelopeRef := none
  sentinelRef := none
  launched := false
  notified := false

/-- The fallback flight for the same stage, role, and target on another carrier. -/
def reopenFlight (id : Nat) (carrier : Carrier) (f : FlightRec) : FlightRec :=
  { f with id := id, carrier := carrier, status := .inFlight, attempt := 0,
           envelopeRef := none, sentinelRef := none, launched := false, notified := false }

def step (s : ProtocolState) : Action → ProtocolState
  | .inspectReadOnly => s
  | .writeGoal g => { s with goal := some g }
  | .appendRevision r => { s with goal := s.goal.map fun g => g.correct r }
  | .capabilityCheck c => { s with capabilityChecked := c :: s.capabilityChecked }
  | .resolveMode m => { s with mode := some m }
  | .openFlight stage role carrier target retryBudget =>
    { s with flights := s.flights ++ [newFlight s.freshId stage role carrier target retryBudget] }
  | .launchViaRunner id => updateFlight s id fun f => { f with launched := true }
  | .launchViaShellBackground _ => s
  | .pollArtifacts _ => s
  | .hostNotified id => updateFlight s id fun f => { f with notified := true }
  | .collect id o => updateFlight s id (collectEffect o)
  | .fallbackFlight id carrier =>
    match s.flight id with
    | none => s
    | some f =>
      { s with flights := s.flights ++ [reopenFlight s.freshId carrier f] }
  | .mutateTarget target => { s with mutationLog := (target, s.activeOn target) :: s.mutationLog }
  | .carry item => { s with context := item :: s.context }
  | .recordPassBudget units => { s with passBudget := some units }
  | .pass t =>
    { s with passBudget := s.passBudget.bind fun b => Sshx.step b t }
  | .advanceStage => { s with stage := s.stage.next.getD s.stage }
  | .declareGate a => { s with gate := a }
  | .evaluateTermination source roster =>
    { s with terminationExit := some (terminationRoute source roster),
             passBudget := s.passBudget.bind fun b => Sshx.step b .terminationGateEvaluation }
  | .claimSatisfied => { s with claimed := true }
  | .lifecycle _ => s
  | .oracleReference _ _ _ => s
  | .publishToMakeLinkable => s

/-- Reachable states: the initial state and every allowed step from a reachable state. -/
inductive Reachable : ProtocolState → Prop
  | initial : Reachable ProtocolState.initial
  | move {s : ProtocolState} {a : Action} : Reachable s → allowed s a → Reachable (step s a)

end Sshx.Behavior
