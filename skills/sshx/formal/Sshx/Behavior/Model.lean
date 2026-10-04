import Sshx.Carrier
import Sshx.Flight
import Sshx.Budget
import Sshx.Gate
import Sshx.Tables
import Sshx.Records
import Sshx.Protocol
import Sshx.Reasoning.Repair

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
  /-- Ghost link to the original flight of this assignment; fallback preserves it.
  Derived from dispatch history, not an added worker record field. -/
  assignment : Nat
  stage : FlightStage
  role : Role
  carrier : Carrier
  target : String
  status : FlightStatus
  retryBudget : Nat
  /-- Consumed same-carrier retry allowance, starting at zero. This is a ghost
  projection of transcript history, not the external runner's `attempt` field. -/
  attempt : Nat
  envelopeRef : Option String
  sentinelRef : Option String
  launched : Bool
  notified : Bool
  deriving DecidableEq, Repr

/-- Semantic projection of the source/assumption record inside the existing harness.
These facts are review inputs, not inferred from nonempty text or a runtime schema. -/
structure IntakeEvidence where
  recordedHarness : Harness
  sources : List String
  assumptionsLabeled : Bool
  minimalTaskRelevantScope : Bool
  explicitBoundariesPreserved : Bool
  inventedAuthority : Bool
  startupQuestionAsked : Bool
  continuation : ContinuationEntry
  deriving DecidableEq, Repr

def IntakeEvidence.complete (e : IntakeEvidence) : Prop :=
  e.sources ≠ [] ∧ e.assumptionsLabeled = true ∧
    e.minimalTaskRelevantScope = true ∧ e.explicitBoundariesPreserved = true ∧
    e.inventedAuthority = false ∧ e.startupQuestionAsked = false

/-- Current semantic projection of `harness.provided_capabilities`, supplied as evidence.
The model does not parse English to establish correspondence or owner authority. -/
structure ContinuationSource where
  providedCapabilities : List String
  entry : ContinuationEntry
  deriving DecidableEq, Repr

/-- The source's position in the existing append-only revision ledger, not a runtime field. -/
structure ContinuationAuthority where
  source : ContinuationSource
  revision : Nat
  deriving DecidableEq, Repr

/-- The roster's evaluated continuation source, supplied with the sealed evidence.
This projects the existing versioned, scoped inputs; it is not a runtime schema or
an authenticity check. Evidence truth and its association are premises. Evaluation
must preserve this association, never manufacture it from the evaluation-time state. -/
structure TerminationEvidence where
  authority : ContinuationAuthority
  roster : Roster
  deriving DecidableEq, Repr

/-- Interpretation of an existing revision and its authorization. `none` means the
revision does not change continuation authority. Support and ownership are independent
evidence inputs, never inferred from a nonempty authorization string. -/
structure RevisionEvidence where
  recordedRevision : Revision
  ownerAuthorized : Bool
  sourceSupported : Bool
  continuation : Option ContinuationSource
  deriving DecidableEq, Repr

def RevisionEvidence.valid (e : RevisionEvidence) (r : Revision) : Prop :=
  e.recordedRevision = r ∧ e.ownerAuthorized = true ∧ e.sourceSupported = true

def FlightRec.active (f : FlightRec) : Bool :=
  f.status == .inFlight || f.status == .retrying

/-- Finite decomposition in the existing approved plan/batch conclusion, not a runtime schema. -/
structure ImplementationPlan where
  obligations : List String
  flightAllowance : Nat
  deriving DecidableEq, Repr

-- SKILL[def]: "Predeclare finite assignments and allowance in the approved plan's conclusion."
def ImplementationPlan.valid (p : ImplementationPlan) : Prop :=
  p.obligations ≠ [] ∧ 0 < p.flightAllowance

/-- Ghost projection of accumulated worker conclusions. `firstFlight` scopes the current
candidate; it is also reset for an explicitly budgeted repeated review. -/
structure ImplementationBatch where
  remaining : List String
  checksPassed : Bool
  flightsLeft : Nat
  firstFlight : Nat
  deriving DecidableEq, Repr

def ImplementationPlan.start (p : ImplementationPlan) (firstFlight : Nat) : ImplementationBatch :=
  ⟨p.obligations, false, p.flightAllowance, firstFlight⟩

/-- The caller-side protocol state. -/
structure ProtocolState where
  stage : Stage
  goal : Option GoalArtifact
  mode : Option WorkerMode
  capabilityChecked : List Carrier
  flights : List FlightRec
  context : List ContextItem
  passBudget : Option Nat
  batch : Option ImplementationBatch
  continuationAuthority : ContinuationAuthority
  terminationExit : Option TerminationExit
  /-- Evaluated source retained from the evidence consumed by the settlement. -/
  terminationAuthority : Option ContinuationAuthority
  claimed : Bool
  /-- Every target mutation with whether a flight on that target was active at that moment. -/
  mutationLog : List (String × Bool)
  deriving Repr

def ProtocolState.initial : ProtocolState :=
  { stage := .intake, goal := none, mode := none, capabilityChecked := [], flights := [],
    context := [], passBudget := none, batch := none,
    continuationAuthority := ⟨⟨[], .silent⟩, 0⟩,
    terminationExit := none, terminationAuthority := none,
    claimed := false, mutationLog := [] }

/-- Every caller-side act the contract speaks about. `hostNotified` is an environment event. -/
inductive Action
  | inspectReadOnly
  | writeGoal (g : GoalArtifact) (e : IntakeEvidence)
  | appendRevision (r : Revision) (e : RevisionEvidence)
  | capabilityCheck (c : Carrier)
  | resolveMode (m : WorkerMode)
  | openFlight (stage : FlightStage) (role : Role) (carrier : Carrier) (target : String) (retryBudget : Nat)
  | launchViaRunner (flight : Nat)
  | launchDelegated (flight : Nat)
  | launchViaShellBackground (flight : Nat)
  | pollArtifacts (flight : Nat)
  | hostNotified (flight : Nat)
  | collect (flight : Nat) (o : Observation)
  | fallbackFlight (flight : Nat) (carrier : Carrier)
  | mutateTarget (target : String)
  | carry (item : ContextItem)
  | recordPassBudget (units : Nat)
  | beginImplementation (plan : ImplementationPlan)
  | recordImplementation (flight : Nat) (completed : List String) (checksPassed : Bool)
  | pass (t : Transition) (e : Reasoning.FamilyEvidence) (plan : Option ImplementationPlan)
  | advanceStage
  | evaluateTermination (source : ClaimSource) (evidence : TerminationEvidence)
  | claimSatisfied
  | lifecycle (op : LifecycleOp)
  | oracleReference (url : String) (isPublic : Bool) (pinned : Bool)
  | publishToMakeLinkable
  deriving DecidableEq, Repr

/-! ## State projections -/

def ProtocolState.goalWritten (s : ProtocolState) : Prop := s.goal.isSome = true
def ProtocolState.modeResolved (s : ProtocolState) : Prop := s.mode.isSome = true
def ProtocolState.abstained (s : ProtocolState) : Prop := s.mode = some .abstain

/-- Both intake and authorized corrections feed the same claim-routing projection. -/
def ProtocolState.gate (s : ProtocolState) : Applicability :=
  applicability s.continuationAuthority.source.entry

/-- The revision ledger supplies the position; evidence cannot choose or reuse it. -/
def ProtocolState.correctedAuthority (s : ProtocolState) (e : RevisionEvidence) :
    ContinuationAuthority :=
  match e.continuation with
  | none => s.continuationAuthority
  | some source => ⟨source, (s.goal.map (·.revisions.length)).getD 0 + 1⟩

def ProtocolState.activeOn (s : ProtocolState) (target : String) : Bool :=
  s.flights.any fun f => f.target == target && f.active

def ProtocolState.flight (s : ProtocolState) (id : Nat) : Option FlightRec :=
  s.flights.find? fun f => f.id == id

def ProtocolState.freshId (s : ProtocolState) : Nat :=
  s.flights.length

/-- `harness` is complete when every sub-item is non-empty; intake may fill routine gaps from
the task, repository rules, existing authorizations, and execution context, recording assumptions
without asking a startup boundary question. -/
def Harness.complete (h : Harness) : Prop :=
  h.providedCapabilities ≠ [] ∧ h.trustBoundary ≠ "" ∧ h.decisionOwnership ≠ ""

/-- The stage a flight stage belongs to. -/
def FlightStage.protocolStage : FlightStage → Stage
  | .thinking => .thinkingPanel
  | .implementation => .implementation
  | .review => .reviewTriplet
  | .termination => .fixOrDone

/-- Flights belonging to this candidate, including eligible carrier fallback attempts. -/
def ProtocolState.batchFlights (s : ProtocolState) : List FlightRec :=
  match s.batch with
  | none => []
  | some b => s.flights.filter fun f => b.firstFlight ≤ f.id

/-- A failed carrier may be absorbed by a later terminal replacement of the same assignment.
The scope evidence still has to cover the obligations; a bare failure supplies none. -/
def ProtocolState.batchSettled (s : ProtocolState) : Bool :=
  (s.batchFlights.filter (fun f => f.stage == .implementation)).all fun f => f.status == .terminal ||
    (f.status == .abstained && s.batchFlights.any fun g =>
      f.id < g.id && f.assignment == g.assignment &&
        g.status == .terminal)

-- SKILL[guard]: "Initial review needs worker evidence for all approved work/checks and no active/unrecovered failed flight."
/-- The sole candidate-readiness projection, shared by stage advance and review dispatch.
It is independent of the remaining pass budget: a repair has already paid for its review. -/
def ProtocolState.reviewReady (s : ProtocolState) : Bool :=
  s.batch.any fun b => b.remaining.isEmpty && b.checksPassed && s.batchSettled

def ProtocolState.reviewStarted (s : ProtocolState) : Bool :=
  s.batchFlights.any fun f => f.stage == .review

def reviewRoles : List Role := [.architecture, .quality, .tests]

def ProtocolState.reviewComplete (s : ProtocolState) : Bool :=
  s.reviewReady && reviewRoles.all fun role =>
    s.batchFlights.any fun f => f.stage == .review && f.role == role && f.status == .terminal

-- SKILL[guard]: "Pending work/checks stay in implementation within the allowance; exhaustion/failure reports unresolved work."
def guardImplementationFlight (s : ProtocolState) : Prop :=
  (s.stage = .implementation ∨ s.stage = .fixOrDone) ∧
    s.reviewStarted = false ∧ s.reviewReady = false ∧ s.batchSettled = true ∧
    ∃ b, s.batch = some b ∧ 0 < b.flightsLeft

/-- Shared per-seat constraint for the recorded draw, dispatch, and fallback. -/
def seatEligible : Role → Carrier → Bool
  | .tests, c => canRunRepositoryCommands c
  | _, _ => true

/-- One normal triplet per candidate; another review needs an explicit counted pass. -/
def guardReviewFlight (s : ProtocolState) (role : Role) (carrier : Carrier) : Prop :=
  (s.stage = .reviewTriplet ∨ s.stage = .fixOrDone) ∧ s.reviewReady = true ∧
    role ∈ reviewRoles ∧
    (s.batchFlights.any fun f => f.stage == .review && f.role == role) = false ∧
    seatEligible role carrier = true

/-! ## Guards, one per clause -/

-- SKILL[guard]: "During `intake`, the caller may use its own read-only tools to inspect the user's input and write `GoalArtifact`; this caller-owned read-only intake is not worker dispatch."
def guardInspect (s : ProtocolState) : Prop := s.stage = .intake

-- SKILL[guard]: "During `intake`, the caller resolves these sub-items from the user's current input, repository rules, existing authorizations, and available execution context; it records those sources and labels any minimal engineering assumptions explicitly."
-- SKILL[guard]: "It must not ask a startup boundary or harness confirmation question."
-- SKILL[guard]: "Explicit user boundaries and permission decisions remain authoritative; routine missing detail is resolved with the smallest task-relevant assumption."
-- SKILL[guard]: "The boundary owner may declare a host-provided goal-driven continuation mechanism only in `harness.provided_capabilities`; the skill must not discover or infer an external mechanism."
-- SKILL[guard]: "`GoalArtifact` is written during `intake` before worker mode selection or any worker dispatch."
def guardWriteGoal (s : ProtocolState) (g : GoalArtifact) (e : IntakeEvidence) : Prop :=
  s.stage = .intake ∧ s.goal = none ∧ s.mode = none ∧ s.flights = [] ∧
    Harness.complete g.harness ∧ e.recordedHarness = g.harness ∧ e.complete

-- SKILL[guard]: "Any explicit correction to `GoalArtifact` or `harness` must append one such revision item before routing continues."
def guardAppendRevision (s : ProtocolState) (r : Revision) (e : RevisionEvidence) : Prop :=
  s.goalWritten ∧ e.valid r

-- SKILL[guard]: "Its capability check may confirm that a Codex CLI worker can be invoked, but it is non-mutating: everywhere in this contract, non-mutating means it changes no file, Git state, GitHub state, label, release, host configuration, lifecycle state, or other external resource."
def guardCapabilityCheck (s : ProtocolState) : Prop :=
  s.goalWritten ∧ s.stage = .chooseWorkerMode

-- SKILL[guard]: "Before any worker dispatch, including delegated intake context-gathering by subagent, Agent, Task, or codex, the caller must complete the non-mutating `codex-cli` capability check and resolve `WorkerMode`."
def guardResolveMode (s : ProtocolState) (m : WorkerMode) : Prop :=
  s.goalWritten ∧ s.stage = .chooseWorkerMode ∧ s.mode = none ∧
    Carrier.codexCli ∈ s.capabilityChecked ∧
    (m = .abstain ∨ ∃ c, m = .carrier c ∧ c ∈ s.capabilityChecked)

-- SKILL[guard]: "`WorkerModeGate` requires resolution before dispatch."
def guardOpenFlight (s : ProtocolState) (stage : FlightStage) (role : Role) (carrier : Carrier) : Prop :=
  s.modeResolved ∧ ¬ s.abstained ∧ carrier ∈ s.capabilityChecked ∧
    match stage with
    | .implementation => role = .implementation ∧ guardImplementationFlight s
    | .review => guardReviewFlight s role carrier
    | _ => s.stage = stage.protocolStage

-- SKILL[guard]: "Use `skills/sshx/scripts/run-codex-worker.sh` for every `codex-cli` launch."
def guardLaunchViaRunner (s : ProtocolState) (id : Nat) : Prop :=
  ∃ f, s.flight id = some f ∧ f.launched = false ∧ f.carrier = .codexCli

/-- Existing direct oracle/subagent invocation, outside the Codex runner. This models
host dispatch only; carrier-specific isolation remains at its existing contract owner. -/
def guardLaunchDelegated (s : ProtocolState) (id : Nat) : Prop :=
  ∃ f, s.flight id = some f ∧ f.launched = false ∧ f.carrier ≠ .codexCli

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

/-- Tried carriers come from all attempts of this assignment, including replacements. -/
def ProtocolState.triedCarriers (s : ProtocolState) (assignment : Nat) : CarrierSet :=
  let tried := fun c => s.flights.any fun f => f.assignment == assignment && f.carrier == c
  ⟨tried .codexCli, tried .nyxidOracle, tried .isolatedTokenSubagent⟩

/-- Capability availability and the same per-seat restriction used at review admission. -/
def ProtocolState.eligibleCarriers (s : ProtocolState) (f : FlightRec) : CarrierSet :=
  let eligible := fun c => s.capabilityChecked.contains c &&
    (f.stage != .review || seatEligible f.role c)
  ⟨eligible .codexCli, eligible .nyxidOracle, eligible .isolatedTokenSubagent⟩

-- SKILL[guard]: "If any flight lacks terminal completion after its finite same-carrier retry budget is exhausted, the caller marks that flight `abstained` with empty `result_envelope_ref` and `completion_sentinel_ref`."
/-- A replacement uses the existing finite selector for the entire failed assignment.
An active or successful replacement closes fallback through any earlier flight id. -/
def guardFallback (s : ProtocolState) (id : Nat) (carrier : Carrier) : Prop :=
  ∃ f, s.flight id = some f ∧ f.status = .abstained ∧
    (s.flights.filter fun g => g.assignment == f.assignment).all
      (fun g => g.status == .abstained) = true ∧
    nextCarrier (s.eligibleCarriers f) (s.triedCarriers f.assignment) = some carrier

-- SKILL[guard]: "While any `SshxWorkerFlightRecord` for the same `work_target` is `in-flight` or `retrying`, the caller is read-only for that target."
def guardMutateTarget (s : ProtocolState) (target : String) : Prop :=
  s.activeOn target = false

-- SKILL[guard]: "It may carry only:"
def guardCarry (item : ContextItem) : Prop := item.permitted = true

-- SKILL[guard]: "This section is the sole owner of `pass_budget`."
def guardRecordPassBudget (s : ProtocolState) : Prop :=
  s.stage = .fixOrDone ∧ s.passBudget = none

-- SKILL[guard]: "The budget is immutable for this run: no result, repair, or correction may add, replenish, reset, or replace units, and a unit is never refunded."
-- SKILL[guard]: "Accumulate completed/remaining obligations and test evidence; terminal completion permits handoff only."
def guardRecordImplementation (s : ProtocolState) (id : Nat) : Prop :=
  s.reviewStarted = false ∧ s.batch.isSome = true ∧
    ∃ f ∈ s.batchFlights, f.id = id ∧ f.stage = .implementation ∧ f.status = .terminal ∧
      (∀ g ∈ s.batchFlights, g.stage = .implementation → g.id ≤ id)

def guardBeginImplementation (s : ProtocolState) (plan : ImplementationPlan) : Prop :=
  s.stage = .implementation ∧ s.batch = none ∧ plan.valid

-- SKILL[guard]: "The batch debit includes all bounded assignments and final review even at zero remaining units, with no subsequent pass authority."
-- SKILL[guard]: "For `fix`, freeze admitted repairs as a finite batch under `## Implementation Worker` handoff, allowance and evidence rules;"
def guardPass (s : ProtocolState) (t : Transition) (e : Reasoning.FamilyEvidence)
    (plan : Option ImplementationPlan) : Prop :=
  s.stage = .fixOrDone ∧ Reasoning.familyPassAllowed e t = true ∧
    s.reviewComplete = true ∧ t ≠ .initialReviewTriplet ∧
    (t.counted = true → ∃ b, s.passBudget = some b ∧ 0 < b) ∧
    (if t = .repairWithRerunReview then ∃ p, plan = some p ∧ p.valid else plan = none)

def guardAdvanceStage (s : ProtocolState) : Prop :=
  s.stage.next.isSome = true ∧
    (if s.stage = .intake then s.goalWritten else s.modeResolved ∧ ¬ s.abstained) ∧
    s.flights.all (fun f => !f.active) = true ∧
    (s.stage = .implementation → s.reviewReady = true) ∧
    (s.stage = .reviewTriplet → s.reviewComplete = true)

-- SKILL[guard]: "`## Termination Gate` is a conditional subgate reached inside `fix_or_done`, never an additional `InlineConsensusProtocol` stage."
def guardEvaluateTermination (s : ProtocolState) (e : TerminationEvidence) : Prop :=
  e.authority = s.continuationAuthority ∧
    s.stage = .fixOrDone ∧ s.reviewComplete = true ∧ s.gate = .applies ∧
    ∃ b, s.passBudget = some b ∧ 0 < b

-- SKILL[guard]: "Ambiguous or unconfirmed claim-specific authority constrains an affirmative claim and is recorded as an assumption or limitation; it does not pause routine startup or invent a mechanism."
def guardClaimSatisfied (s : ProtocolState) : Prop :=
  s.stage = .fixOrDone ∧ s.gate ≠ .withholdClaim ∧
    (s.gate = .applies → s.terminationExit = some .claimPermitted ∧
      s.terminationAuthority = some s.continuationAuthority) ∧ s.reviewComplete = true

-- SKILL[guard]: "No lifecycle authority is granted."
def guardNoLifecycle : Prop := False

-- SKILL[guard]: "Such a URL is permitted only when the referenced content is already anonymously readable on the remote, which the caller confirms before the first submission; the caller must never push, publish, change repository visibility, or otherwise mutate remote state to make content linkable."
def guardOracleReference (isPublic pinned : Bool) : Prop := isPublic = true ∧ pinned = true

def guardNoPublishToLink : Prop := False

/-- The conjunction of the clause guards, per action. -/
def allowed (s : ProtocolState) : Action → Prop
  | .inspectReadOnly => guardInspect s
  | .writeGoal g e => guardWriteGoal s g e
  | .appendRevision r e => guardAppendRevision s r e
  | .capabilityCheck _ => guardCapabilityCheck s
  | .resolveMode m => guardResolveMode s m
  | .openFlight stage role carrier _ _ => guardOpenFlight s stage role carrier
  | .launchViaRunner id => guardLaunchViaRunner s id
  | .launchDelegated id => guardLaunchDelegated s id
  | .launchViaShellBackground _ => guardNoShellBackground
  | .pollArtifacts _ => guardNoPolling
  | .hostNotified id => guardHostNotified s id
  | .collect id _ => guardCollect s id
  | .fallbackFlight id carrier => guardFallback s id carrier
  | .mutateTarget target => guardMutateTarget s target
  | .carry item => guardCarry item
  | .recordPassBudget _ => guardRecordPassBudget s
  | .beginImplementation p => guardBeginImplementation s p
  | .recordImplementation id _ _ => guardRecordImplementation s id
  | .pass t e p => guardPass s t e p
  | .advanceStage => guardAdvanceStage s
  | .evaluateTermination _ e => guardEvaluateTermination s e
  | .claimSatisfied => guardClaimSatisfied s
  | .lifecycle _ => guardNoLifecycle
  | .oracleReference _ isPublic pinned => guardOracleReference isPublic pinned
  | .publishToMakeLinkable => guardNoPublishToLink

-- SKILL[ref]: "Include any non-blocking advisory feedback without inlining logs."
abbrev advisoryWithoutLogs := @ContextItem.permitted

-- SKILL[policy]: "Protocol policy, not a mathematical consequence: before the first pass after the initial review triplet, the caller records one owner-precommitted finite integer `pass_budget`."
abbrev passBudgetPrecommitment := @guardRecordPassBudget

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
  assignment := id
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
  | .writeGoal g e =>
    { s with
      goal := some g,
      continuationAuthority := ⟨⟨e.recordedHarness.providedCapabilities, e.continuation⟩,
        g.revisions.length⟩ }
  | .appendRevision r e =>
    { s with
      goal := s.goal.map (fun g => g.correct r),
      continuationAuthority := s.correctedAuthority e }
  | .capabilityCheck c => { s with capabilityChecked := c :: s.capabilityChecked }
  | .resolveMode m => { s with mode := some m }
  | .openFlight stage role carrier target retryBudget =>
    { s with
      flights := s.flights ++ [newFlight s.freshId stage role carrier target retryBudget],
      batch := s.batch.map fun b => if stage = .implementation then
        { b with flightsLeft := b.flightsLeft - 1, checksPassed := false } else b }
  | .launchViaRunner id | .launchDelegated id =>
    updateFlight s id fun f => { f with launched := true }
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
  | .beginImplementation p => { s with batch := some (p.start s.freshId) }
  | .recordImplementation _ completed checksPassed =>
    { s with batch := s.batch.map fun b =>
        { b with remaining := b.remaining.filter (fun x => !completed.contains x), checksPassed := checksPassed } }
  | .pass t _ plan =>
    { s with
      passBudget := s.passBudget.bind fun b => Sshx.step b t,
      batch := if t = .repairWithRerunReview then plan.map (·.start s.freshId)
        else if t = .repeatedReviewPass then s.batch.map fun b => { b with firstFlight := s.freshId }
        else s.batch }
  | .advanceStage => { s with stage := s.stage.next.getD s.stage }
  | .evaluateTermination source e =>
    { s with terminationExit := some (terminationRoute source e.roster),
             terminationAuthority := some e.authority,
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
