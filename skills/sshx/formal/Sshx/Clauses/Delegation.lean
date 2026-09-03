import Mathlib.Tactic
import Sshx.Carrier
import Sshx.Flight
import Sshx.Tables
import Sshx.Gate
import Sshx.Isolation
import Sshx.Protocol
import Sshx.Behavior.Model
import Sshx.Behavior.Invariant
import Sshx.Reasoning.Discipline
import Sshx.Clauses.Contract
import Sshx.Clauses.Boundaries

/-!
# Clauses: worker delegation

Source: `## Worker Delegation` — the carriers, the dispatch-time composition and seat
rotation, flight records,
runner and batch mechanics as the caller sees them, the oracle carrier's rules, and fallback.
-/

namespace Sshx.Clauses

open Sshx

/-! ## Carriers and mode -/

-- SKILL[def]: "`WorkerDelegationContract` is the source-owned contract for choosing and using worker carriers."
-- SKILL[def]: "1. `codex-cli`"
-- SKILL[def]: "2. `nyxid-oracle`"
-- SKILL[def]: "3. `isolated-token-subagent`"
-- SKILL[def]: "4. `abstain`"
def workerDelegationContract : List WorkerMode :=
  [.carrier .codexCli, .carrier .nyxidOracle, .carrier .isolatedTokenSubagent, .abstain]

theorem contract_lists_every_mode (m : WorkerMode) : m ∈ workerDelegationContract := by
  cases m with
  | carrier c => cases c <;> decide
  | abstain => decide

-- SKILL[def]: "`nyxid-oracle` is an out-of-process worker carrier that routes a perspective to a browser oracle (ChatGPT Pro) through `nyxid oracle`."
-- SKILL[def]: "`isolated-token-subagent` is an in-context worker carrier."
def outOfProcess : Carrier → Bool
  | .codexCli | .nyxidOracle => true
  | .isolatedTokenSubagent => false

/-- What an oracle reply is to the caller. -/
inductive ReplyKind
  | data
  | instruction
  deriving DecidableEq, Repr

-- SKILL[def]: "Despite the CLI name, within this contract it is a fallible advisory worker exactly like `codex-cli`, with no authority of any kind; its reply is data for the caller, not an instruction."
def oracleReplyIs : ReplyKind := .data

theorem oracle_has_no_authority : carrierHasControllerAuthority .nyxidOracle = false := rfl

-- SKILL[ref]: "Its prior context is permanently sterile-context-unverified as detailed under `## No Context Pollution`."
abbrev oracleSterilityUnverified := @sterilityVerifiable

-- SKILL[def]: "Its capability check and dispatch are non-mutating; it is worker-delegation reasoning capability only, never controller authority."
def capabilityCheckMutates (_ : Carrier) : Bool := false

-- SKILL[ref]: "It must run with isolated token context so same-round workers cannot read one another's full reasoning or peer outputs before returning their own verdict."
abbrev isolatedTokenContext := @same_round_peer_invisible

-- SKILL[thm]: "`abstain` is required when none of `codex-cli`, `nyxid-oracle`, or `isolated-token-subagent` is available."
theorem abstain_when_nothing_available (tried : CarrierSet) :
    resolveSeat CarrierSet.empty tried = .abstain := by
  cases tried with
  | mk a b c => cases a <;> cases b <;> cases c <;> decide

-- SKILL[ref]: "Do not self-apply the triplet inside the caller context and present it as worker consensus."
abbrev noSelfApplication := @fake_roster_rejected

/-! ## Dispatch-time composition and seat rotation -/

-- SKILL[policy]: "Protocol policy, not a mathematical consequence: at dispatch time, every multi-seat stage assigns exactly one seat to `isolated-token-subagent`, exactly one seat to `nyxid-oracle`, and every remaining seat to `codex-cli`, and the three-seat `## Termination Gate` follows that same composition; every single-worker stage assigns its worker to `codex-cli`."
def stageComposition (seats : Nat) : List Carrier :=
  if seats ≤ 1 then List.replicate seats .codexCli
  else [.isolatedTokenSubagent, .nyxidOracle] ++ List.replicate (seats - 2) .codexCli

theorem composition_one_subagent_one_oracle (seats : Nat) (h : 2 ≤ seats) :
    (stageComposition seats).count .isolatedTokenSubagent = 1 ∧
      (stageComposition seats).count .nyxidOracle = 1 ∧
      (stageComposition seats).count .codexCli = seats - 2 := by
  have hgt : ¬ seats ≤ 1 := by omega
  simp [stageComposition, hgt, List.count_cons, List.count_replicate]

theorem single_worker_is_codex : stageComposition 1 = [.codexCli] := rfl

/-- A stage's recorded pairing. -/
structure StageDispatch where
  pairing : List (Behavior.Role × Carrier)
  recordedBeforeAnyReturn : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "The carrier-role pairing must be chosen and recorded before any worker in that stage returns."
def StageDispatch.valid (d : StageDispatch) : Bool := d.recordedBeforeAnyReturn

/-- When each rule governs. -/
inductive DispatchPhase
  | dispatchTime
  | afterCarrierFailure
  deriving DecidableEq, Repr

inductive GoverningRule
  | rotationOverComposition
  | priorityList
  deriving DecidableEq, Repr

-- SKILL[def]: "This is the dispatch-time rotation rule; the numbered `WorkerMode` list governs only fallback after a carrier failure."
def governs : DispatchPhase → GoverningRule
  | .dispatchTime => .rotationOverComposition
  | .afterCarrierFailure => .priorityList

-- SKILL[thm]: "The recorded initial pairing must not be rebalanced in response to completion outcomes; a retry or fallback may replace only the failed flight for the same seat and role, and neither is a mechanism for redrawing or restoring a stage's recorded assignment."
def rebalanceAllowed : Bool := false

theorem fallback_keeps_seat_and_role (id : Nat) (c : Carrier) (f : Behavior.FlightRec) :
    (Behavior.reopenFlight id c f).role = f.role ∧ (Behavior.reopenFlight id c f).stage = f.stage ∧
      (Behavior.reopenFlight id c f).target = f.target :=
  ⟨rfl, rfl, rfl⟩

-- SKILL[def]: "A `tests` review seat must be assigned to a carrier capable of executing repository verification commands in the `work_target`, which is the per-seat constraint that keeps `nyxid-oracle` out of that seat's feasible draws."
def canRunRepositoryCommands : Carrier → Bool
  | .codexCli | .isolatedTokenSubagent => true
  | .nyxidOracle => false

def testsSeatEligible (c : Carrier) : Bool := canRunRepositoryCommands c

theorem oracle_cannot_hold_tests_seat : testsSeatEligible .nyxidOracle = false := rfl

/-! ### Seat rotation -/

/-- One stage dispatch's drawn seat assignment. -/
abbrev SeatAssignment := List (Behavior.Role × Carrier)

def SeatAssignment.seats (a : SeatAssignment) : List Behavior.Role := a.map Prod.fst

def SeatAssignment.carriers (a : SeatAssignment) : List Carrier := a.map Prod.snd

/-- The per-seat carrier constraint a draw must respect; only `tests` is restricted. -/
def seatEligible : Behavior.Role → Carrier → Bool
  | .tests, c => canRunRepositoryCommands c
  | _, _ => true

-- SKILL[def]: "Which named seat holds which carrier rotates: at each stage dispatch the caller draws one assignment uniformly at random from every assignment that satisfies that composition and this stage's per-seat carrier constraints, so a named role holds a carrier only for the stage dispatch it was drawn for."
def drawFeasible (seats : List Behavior.Role) (a : SeatAssignment) : Prop :=
  a.seats = seats ∧ a.carriers.Perm (stageComposition seats.length) ∧
    ∀ p ∈ a, seatEligible p.1 p.2 = true

/-- Rotation permutes seats over a fixed composition: every feasible draw carries exactly the
same carrier counts as the composition, so randomizing seats never trades heterogeneity away. -/
theorem draw_keeps_composition {seats : List Behavior.Role} {a : SeatAssignment}
    (h : drawFeasible seats a) (hlen : 2 ≤ seats.length) :
    a.carriers.count .isolatedTokenSubagent = 1 ∧
      a.carriers.count .nyxidOracle = 1 ∧
      a.carriers.count .codexCli = seats.length - 2 := by
  obtain ⟨-, hperm, -⟩ := h
  obtain ⟨h1, h2, h3⟩ := composition_one_subagent_one_oracle seats.length hlen
  exact ⟨by rw [hperm.count_eq]; exact h1, by rw [hperm.count_eq]; exact h2,
    by rw [hperm.count_eq]; exact h3⟩

/-- The constraint filter is what keeps the oracle off the `tests` seat in every draw. -/
theorem oracle_never_drawn_for_tests {seats : List Behavior.Role} {a : SeatAssignment}
    (h : drawFeasible seats a) : (Behavior.Role.tests, Carrier.nyxidOracle) ∉ a := by
  intro hmem
  have := h.2.2 _ hmem
  simp [seatEligible, canRunRepositoryCommands] at this

/-- The review stage's named seats. -/
def reviewSeats : List Behavior.Role := [.architecture, .quality, .tests]

def reviewDrawA : SeatAssignment :=
  [(.architecture, .isolatedTokenSubagent), (.quality, .nyxidOracle), (.tests, .codexCli)]

def reviewDrawB : SeatAssignment :=
  [(.architecture, .nyxidOracle), (.quality, .isolatedTokenSubagent), (.tests, .codexCli)]

theorem reviewDrawA_feasible : drawFeasible reviewSeats reviewDrawA := by
  refine ⟨rfl, ?_, by decide⟩
  show (List.map Prod.snd reviewDrawA).Perm (stageComposition 3)
  simp [reviewDrawA, stageComposition]

theorem reviewDrawB_feasible : drawFeasible reviewSeats reviewDrawB := by
  refine ⟨rfl, ?_, by decide⟩
  show (List.map Prod.snd reviewDrawB).Perm (stageComposition 3)
  simp [reviewDrawB, stageComposition]
  exact List.Perm.swap _ _ _

/-- No role keeps a standing carrier: the same seat holds different carriers in two feasible
draws of the same stage. -/
theorem no_standing_carrier_for_a_role :
    (Behavior.Role.architecture, Carrier.isolatedTokenSubagent) ∈ reviewDrawA ∧
      (Behavior.Role.architecture, Carrier.isolatedTokenSubagent) ∉ reviewDrawB := by decide

/-- Where a draw may come from. -/
inductive RandomnessSource
  | mechanical
  | callerPreference
  deriving DecidableEq, Repr

/-- The stage's recorded `worker_delegation.seat_rotation` entry. -/
structure SeatRotation where
  drawn : SeatAssignment
  source : RandomnessSource
  recordedBeforeFirstLaunch : Bool
  deriving DecidableEq, Repr

-- SKILL[guard]: "The draw must come from a mechanical randomness source outside the caller's own preference, and its result is recorded in `worker_delegation.seat_rotation` before the first worker of that stage is launched."
def SeatRotation.valid (r : SeatRotation) : Bool :=
  match r.source with
  | .mechanical => r.recordedBeforeFirstLaunch
  | .callerPreference => false

theorem caller_preference_is_never_a_draw (a : SeatAssignment) (b : Bool) :
    SeatRotation.valid ⟨a, .callerPreference, b⟩ = false := rfl

theorem draw_recorded_late_is_invalid (a : SeatAssignment) (s : RandomnessSource) :
    SeatRotation.valid ⟨a, s, false⟩ = false := by cases s <;> rfl

/-- What may follow a recorded draw. -/
inductive DrawResponse
  | keepRecordedDraw
  | redraw
  deriving DecidableEq, Repr

-- SKILL[thm]: "A recorded draw is final: redrawing it is forbidden, whatever the caller thinks of the seats it produced, and an unavailable carrier is handled by the fallback rule below rather than by a new draw."
def responseAfterRecord (_dislikedSeats : Bool) (_carrierUnavailable : Bool) : DrawResponse :=
  .keepRecordedDraw

theorem no_input_reopens_a_recorded_draw (disliked unavailable : Bool) :
    responseAfterRecord disliked unavailable ≠ .redraw := by
  simp [responseAfterRecord]

-- SKILL[guard]: "When a stage runs again on the same `work_target`, its next draw must differ from that stage's previously recorded assignment whenever two or more assignments satisfy the constraints."
def repeatDrawValid (feasibleCount : Nat) (prev next : SeatAssignment) : Bool :=
  if 2 ≤ feasibleCount then decide (prev ≠ next) else true

theorem repeat_pass_must_rotate {n : Nat} {prev next : SeatAssignment} (h : 2 ≤ n)
    (hv : repeatDrawValid n prev next = true) : prev ≠ next := by
  simp [repeatDrawValid, h] at hv
  exact hv

/-- The rotation duty never fails closed where it binds: a repeated review pass always has a
feasible draw that differs from the previous one. -/
theorem review_rotation_is_satisfiable :
    drawFeasible reviewSeats reviewDrawB ∧ repeatDrawValid 2 reviewDrawA reviewDrawB = true :=
  ⟨reviewDrawB_feasible, by decide⟩

-- SKILL[policy]: "Carrier heterogeneity is this protocol's policy, not a theorem premise or consequence."
def carrierHeterogeneityIsPolicy : Bool := true

-- SKILL[def]: "Any claim that it or the seat rotation above improves consensus quality or yields statistically independent priors is `ASSUMED-UNVERIFIED` under `seek truth from facts`; whether `codex-cli` and `isolated-token-subagent` use different model families is also `ASSUMED-UNVERIFIED`, and a model identifier reported by a `nyxid-oracle` response is evidence only for that invocation."
def diversityBenefitStatus : Reasoning.PremiseStatus := .assumedUnverified

-- SKILL[ref]: "A stage may be presented as model-diverse only when every initially paired seat reached terminal completion on its initial carrier with no fallback, unavailability, or exhausted retry, and at least two distinct model families are recorded evidence for those completions; otherwise record that the stronger diversity claim was not achieved."
abbrev modelDiverseClaim := @diversityClaimAllowed

-- SKILL[def]: "When a thinking, implementation, review, or termination flight instead exhausts its bounded retries and fallback without terminal completion, that stage returns `abstain` rather than a synthesized worker conclusion or an incomplete triplet, the caller skips the remaining dependent stages, and the blocker is reported honestly."
def stageAfterExhaustion : StageOutcome := .abstain

-- SKILL[thm]: "A shared model family, inherited repository prior, or disclosed prior alone does not prove contamination; only a recorded dependency path does."
def contaminationProven (sharedFamily inheritedPrior disclosedPrior recordedDependencyPath : Bool) :
    Bool :=
  recordedDependencyPath

theorem only_recorded_path_proves_contamination (a b c : Bool) :
    contaminationProven a b c false = false := rfl

/-! ## Flight records -/

-- SKILL[ref]: "Every worker dispatch must create a prompt-level `SshxWorkerFlightRecord` before the worker is launched."
abbrev flightBeforeLaunch := @Behavior.guardLaunchViaRunner

-- SKILL[ref]: "The caller-carried transcript must keep these records under `worker_flights`, and each worker result record must reference the matching `flight_id` through `worker_flight_ref`."
abbrev workerFlightRef := @SeatRecord.workerFlightRef

-- SKILL[def]: "`SshxWorkerFlightRecord` has exactly these fields:"
-- SKILL[def]: "- `flight_id`"
-- SKILL[def]: "- `stage`"
abbrev SshxWorkerFlightRecord := Behavior.FlightRec

-- SKILL[def]: "- `work_target`"
-- SKILL[def]: "- `status`"
-- SKILL[def]: "- `retry_budget`"
-- SKILL[def]: "- `attempt`"
-- SKILL[def]: "- `result_envelope_ref`"
-- SKILL[def]: "- `completion_sentinel_ref`"
def flightRecordFieldCount : Nat := 11

-- SKILL[ref]: "The caller is non-mutating for that target and its external resources."
abbrev callerNonMutating := @Behavior.guardMutateTarget

/-! ## Runner mechanics as the caller sees them -/

inductive PathOwner
  | runner
  | caller
  deriving DecidableEq, Repr

-- SKILL[def]: "For each `codex-cli` attempt, before launch the caller must choose a unique `flight_id` and `attempt` and pass them to `skills/sshx/scripts/run-codex-worker.sh`; the runner derives and owns every artifact path, parallel attempts receive disjoint derived paths, and the caller must not supply arbitrary result, sentinel, log, or state paths."
def artifactPathOwner : PathOwner := .runner

-- SKILL[def]: "The command, sandbox, path, direct-process, and collection mechanics are owned by `CODEX_WORKER_SPEC.md`; the required dispatch shape is the runner's default `danger-full-access` sandbox, so the caller passes no sandbox selection unless the maintainer explicitly directs a narrower one."
def defaultSandbox : String := "danger-full-access"

inductive TeardownOwner
  | callerHarness
  | runner
  deriving DecidableEq, Repr

-- SKILL[def]: "Time limits and final teardown of the whole job tree are the caller AI harness's responsibility."
def teardownOwner : TeardownOwner := .callerHarness

-- SKILL[ref]: "The caller records `result_envelope_ref` and `completion_sentinel_ref` on the matching flight only if the runner reports completion and the envelope and sentinel validate."
abbrev refsOnlyOnCompletion := @Behavior.collectEffect

-- SKILL[ref]: "Completion and verdict recognition stay governed by the `## Worker Completion Contract`."
abbrev completionGovernedByPredicate := @done

/-! ## Batch dispatch -/

-- SKILL[def]: "`skills/sshx/scripts/run-codex-worker-batch.sh` is the permitted one-call fan-out alternative for the `codex-cli` subset of a multi-seat stage."
def batchSeats (layout : List Carrier) : List Carrier := layout.filter (· == .codexCli)

-- SKILL[thm]: "It never covers a whole stage because the `nyxid-oracle` and `isolated-token-subagent` seats reserved by the dispatch-time composition above remain outside the batch."
theorem batch_never_covers_whole_stage (seats : Nat) (h : 2 ≤ seats) :
    (batchSeats (stageComposition seats)).length < (stageComposition seats).length := by
  have hgt : ¬ seats ≤ 1 := by omega
  simp [batchSeats, stageComposition, hgt, List.filter_replicate]

-- SKILL[ref]: "The dispatcher obtains every worker artifact path from the runner's pure path projection; worker artifact paths remain runner-derived and are never caller-supplied."
abbrev batchPathsFromRunner := artifactPathOwner

-- SKILL[def]: "Internal shell `&` followed by `wait` is permitted inside that one named batch script because it remains the foreground process of one host-tracked job, records every child, and joins every recorded child before publishing a report; its signal handling, interruption reporting, and inherited-disposition limits are owned by `CODEX_WORKER_SPEC.md` and the script's behavior tests, and whole-job-tree teardown remains the host's responsibility."
def batchInternalWaitPermitted : Bool := true

inductive NotificationGranularity
  | perCarrier
  | perBatch
  deriving DecidableEq, Repr

-- SKILL[def]: "Batching degrades host completion notification from per-carrier to per-batch."
def notificationGranularity (batched : Bool) : NotificationGranularity :=
  if batched then .perBatch else .perCarrier

-- SKILL[def]: "Launching one host job per seat remains permitted and is the form on which per-seat retry and fallback latency depends; batching is an alternative, not a mandate."
def oneJobPerSeatPermitted : Bool := true

def batchingMandated : Bool := false

-- SKILL[ref]: "Status reading is a one-shot, after-terminal collection convenience and is not authorization to poll while any runner is active."
abbrev statusReadAfterNotification := @Behavior.guardCollect

/-- Kinds of artifact around a flight. -/
inductive ArtifactKind
  | workerArtifact
  | dispatcherEvidence
  | statusProjection
  deriving DecidableEq, Repr

-- SKILL[def]: "The batch report is dispatcher-owned orchestration evidence, not a worker artifact, and neither it nor the status projection changes completion or verdict routing."
def changesRouting : ArtifactKind → Bool
  | .workerArtifact => true
  | .dispatcherEvidence | .statusProjection => false

/-! ## The oracle carrier -/

/-- One oracle attempt as the caller must set it up. -/
structure OracleAttempt where
  newIsolatedConversation : Bool
  disjointFromParallelWorkers : Bool
  briefRequiresEnvelopeReply : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "For each `nyxid-oracle` attempt, the caller must start a new isolated oracle conversation before that attempt's first submission and pass a worker brief that requires the reply to be exactly an `SshxResultEnvelope` payload; parallel workers must receive disjoint conversations."
def OracleAttempt.conforming (a : OracleAttempt) : Bool :=
  a.newIsolatedConversation && a.disjointFromParallelWorkers && a.briefRequiresEnvelopeReply

-- SKILL[ref]: "The dispatch is a direct `nyxid oracle` reasoning invocation, not a helper script, daemon, or repository-owned CLI, and the exact command and flags are not part of this contract."
abbrev oracleIsDirectInvocation := @oracleUsedAs

-- SKILL[ref]: "Completion and verdict recognition use only `## Worker Completion Contract`."
abbrev oracleCompletionPredicate := @done_iff

/-- What content the oracle can read. -/
inductive ContentRef
  | callerLocalPath
  | publicPinnedUrl
  | inlinedContent
  deriving DecidableEq, Repr

-- SKILL[def]: "A `nyxid-oracle` worker has no access to the caller's filesystem, so caller-local paths, including `work_target` paths, are not readable content references for it."
def oracleCanRead : ContentRef → Bool
  | .callerLocalPath => false
  | .publicPinnedUrl | .inlinedContent => true

/-- How a repository URL is pinned. -/
inductive UrlPin
  | commitSha
  | branch
  | tag
  | head
  deriving DecidableEq, Repr

-- SKILL[def]: "Its brief may instead reference repository content by public GitHub URL, pinned to an immutable commit SHA so every seat reads the same bytes; branch, tag, and `HEAD` URLs drift between reads and must not be used."
def urlPinAllowed : UrlPin → Bool
  | .commitSha => true
  | .branch | .tag | .head => false

/-- What a referenced URL is and is not. -/
structure UrlRole where
  workerContext : Bool
  goalSource : Bool
  peerOutputPointer : Bool
  callerVerifiedEvidence : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "A referenced URL is worker context only: it is never a goal source under `## Goal Contract`, never a pointer to same-round peer output or another seat's artifacts, and whatever the oracle reports from it is worker-reported data rather than caller-verified evidence."
def referencedUrlRole : UrlRole := ⟨true, false, false, false⟩

-- SKILL[def]: "If the oracle cannot retrieve a referenced URL, it must record that in `SshxResultEnvelope.conclusion` and mark every premise that depended on it `ASSUMED-UNVERIFIED` under `## Reasoning Discipline`, never reconstructing the content from memory."
def unretrievedPremiseStatus : Reasoning.PremiseStatus := .assumedUnverified

def reconstructFromMemoryAllowed : Bool := false

/-! ## Fallback -/

inductive FallbackOrigin
  | unavailableBeforeOpen
  | retryBudgetExhausted
  deriving DecidableEq, Repr

-- SKILL[def]: "If an initially paired carrier is unavailable before a flight can be opened, the caller records the unavailable origin in `worker_delegation.reason` and the gate record, then immediately applies the fallback selection rule below without claiming that a same-carrier retry budget was exhausted."
def recordedOrigin (unavailableBeforeOpen : Bool) : FallbackOrigin :=
  if unavailableBeforeOpen then .unavailableBeforeOpen else .retryBudgetExhausted

theorem unavailable_is_not_exhaustion : recordedOrigin true ≠ .retryBudgetExhausted := by decide

-- SKILL[ref]: "The caller creates a new `SshxWorkerFlightRecord` for the same `stage`, `role`, and `work_target`, and `worker_delegation.reason` and the gate record state the exhausted or unavailable origin and chosen fallback."
abbrev fallbackKeepsIdentity := @fallback_keeps_seat_and_role

-- SKILL[ref]: "The caller stays read-only for that `work_target` until the fallback flight reaches `terminal` or `abstained`."
abbrev readOnlyUntilFallbackSettles := @Behavior.ProtocolState.activeOn

end Sshx.Clauses
