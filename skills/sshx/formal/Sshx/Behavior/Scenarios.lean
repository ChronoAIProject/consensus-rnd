import Sshx.Behavior.Invariant
import Sshx.Reasoning.Authority

/-!
Observable route regressions. These compose the production `step`, `allowed`, downgrade
and review table; there is no parallel test decision model. Worker evidence is an input,
not a proof of English semantics. Baselines are recorded in test_sshx_contract.py.
`admitted` below checks every guard of the composed traces; `run` applies their effects.
Approved-plan fixtures supply the prior thinking settlement as an input.
-/
namespace Sshx.Behavior.Scenarios
open Sshx Sshx.Reasoning

private def goal : GoalArtifact :=
  ⟨"repair", "correct ordinary use", [], ["verified"], "remaining gap?",
    ⟨["worker execution"], "non-malicious ordinary mistakes", "engineering owner"⟩, []⟩
private def intake (continuation : ContinuationEntry) : IntakeEvidence :=
  ⟨goal.harness, ["task", "repository rules"], true, true, true, false, false, continuation⟩

private def readyState : ProtocolState :=
  { ProtocolState.initial with
    stage := .implementation, goal := some goal, mode := some (.carrier .codexCli),
    capabilityChecked := [.codexCli, .isolatedTokenSubagent], hecaCheckRecorded := true }

private def twoParts : ImplementationPlan := ⟨["contract", "verification"], 2⟩
private def begin := step readyState (.beginImplementation twoParts)
private def opened := step begin (.openFlight .implementation .implementation .codexCli "target" 0)
private def successful : Observation := ⟨true, true, true, true, true⟩
private def finish (s : ProtocolState) (id : Nat) (o : Observation) : ProtocolState :=
  step (step (step s (.launchViaRunner id)) (.hostNotified id)) (.collect id o)
private def returned := finish opened 0 successful
private def partDone := step returned (.recordImplementation 0 ["contract"] true)
private def second := step partDone (.openFlight .implementation .implementation .codexCli "target" 0)
private def complete := step (finish second 1 successful)
  (.recordImplementation 1 ["verification"] true)

-- The first worker is terminal and its tests passed; remaining scope still blocks review.
example : (partDone.flight 0).map (·.status) = some .terminal := by decide
example : partDone.reviewReady = false := by decide
example : ¬ allowed partDone .advanceStage := by
  simp [allowed, guardAdvanceStage, partDone, returned, opened, begin, readyState,
    finish, step, updateFlight, collectEffect, done, successful, twoParts, ImplementationPlan.start,
    ProtocolState.initial, ProtocolState.reviewReady, ProtocolState.batchSettled,
    ProtocolState.batchFlights, newFlight, ProtocolState.freshId, FlightRec.active,
    ProtocolState.flight, ProtocolState.attemptSettled, ProtocolState.housekeepingFor,
    ProtocolState.allHecaHousekeepingSettled]

-- The next approved assignment remains in implementation, with no review flight in between.
example : allowed partDone (.openFlight .implementation .implementation .codexCli "target" 0) := by
  simp [allowed, guardOpenFlight, guardImplementationFlight, ProtocolState.modeResolved,
    ProtocolState.abstained, partDone, returned, opened, begin, readyState, finish, step, updateFlight,
    collectEffect, done, successful, twoParts, ImplementationPlan.start, ProtocolState.initial,
    ProtocolState.reviewStarted, ProtocolState.reviewReady, ProtocolState.batchSettled, ProtocolState.batchFlights,
    newFlight, ProtocolState.freshId, ProtocolState.flight, ProtocolState.attemptSettled,
    ProtocolState.housekeepingFor]
example : complete.reviewReady = true := by decide
example : complete.reviewStarted = false := by decide
private def review := step complete .advanceStage
example : review.stage = .reviewTriplet := by decide
example : allowed complete .advanceStage := by
  simp [allowed, guardAdvanceStage, complete, second, partDone, returned, opened, begin,
    readyState, finish, step, updateFlight, collectEffect, done, successful, twoParts,
    ImplementationPlan.start, ProtocolState.initial, ProtocolState.reviewReady,
    ProtocolState.batchSettled, ProtocolState.batchFlights, newFlight, ProtocolState.freshId,
    FlightRec.active, Stage.next, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.flight, ProtocolState.attemptSettled, ProtocolState.housekeepingFor,
    ProtocolState.allHecaHousekeepingSettled]

-- Triplet seats can all dispatch independently; a second flight for the same seat cannot.
private def architecture := step review (.openFlight .review .architecture .codexCli "target" 0)
private def quality := step architecture (.openFlight .review .quality .codexCli "target" 0)
private def tests := step quality (.openFlight .review .tests .codexCli "target" 0)
example : architecture.reviewReady = true := by decide
example : allowed architecture (.openFlight .review .quality .codexCli "target" 0) := by
  simp only [allowed, guardOpenFlight, guardReviewFlight, seatEligible, ProtocolState.modeResolved,
    ProtocolState.abstained]
  decide
example : allowed quality (.openFlight .review .tests .codexCli "target" 0) := by
  simp only [allowed, guardOpenFlight, guardReviewFlight, seatEligible, canRunRepositoryCommands, ProtocolState.modeResolved,
    ProtocolState.abstained]
  decide
example : (tests.batchFlights.filter (fun f => f.stage == .review)).length = 3 := by decide
example : ¬ guardReviewFlight tests .architecture .codexCli := by
  have : (tests.batchFlights.any fun f => f.stage == .review && f.role == .architecture) = true := by decide
  simp [guardReviewFlight, this]
private def reviewed := finish (finish (finish tests 2 successful) 3 successful) 4 successful
example : reviewed.reviewComplete = true := by decide
private def fix := step reviewed .advanceStage
example : fix.stage = .fixOrDone := by decide

private def family : FamilyEvidence :=
  ⟨"goal", "correct output", "ordinary inputs", "shared mechanism", "worker", "behavior check",
    true, true, 2, false, .uniformInvariant true, false, false, false, false, none⟩

-- The default fix state carries no cap, so a direction-gated pass remains admissible
-- and leaves the absent cap unchanged.
example : fix.passBudget = none := by decide
example : allowed fix (.pass .repeatedReviewPass family none) := by
  have hs : fix.stage = .fixOrDone := by decide
  have hr : fix.reviewComplete = true := by decide
  have hb : fix.passBudget = none := by decide
  simp [allowed, guardPass, hs, hr, hb, family, familyPassAllowed, familyRoute,
    ownerAuthorizedDomainChange, classGateActive, FamilyEvidence.recordComplete,
    coverageBasisVerified, Transition.counted]
private def defaultRepeated := step fix (.pass .repeatedReviewPass family none)
example : defaultRepeated.passBudget = none := by decide

private def funded := step fix (.recordPassBudget 1)
private def repair := step funded (.pass .repairWithRerunReview family (some twoParts))
example : allowed funded (.pass .repairWithRerunReview family (some twoParts)) := by
  have hs : funded.stage = .fixOrDone := by decide
  have hr : funded.reviewComplete = true := by decide
  have hb : funded.passBudget = some 1 := by decide
  simp [allowed, guardPass, hs, hr, hb, family, familyPassAllowed, familyRoute,
    ownerAuthorizedDomainChange, classGateActive, FamilyEvidence.recordComplete,
    coverageBasisVerified, twoParts, ImplementationPlan.valid, Transition.counted]
example : repair.passBudget = some 0 := by decide
example : repair.stage = .fixOrDone := by decide
private def repairedFirst := step (finish (step repair
  (.openFlight .implementation .implementation .codexCli "target" 0)) 5 successful)
  (.recordImplementation 5 ["contract"] true)
example : repairedFirst.reviewReady = false := by decide
private def repaired := step (finish (step repairedFirst
  (.openFlight .implementation .implementation .codexCli "target" 0)) 6 successful)
  (.recordImplementation 6 ["verification"] true)
example : repaired.reviewReady = true := by decide
example : repaired.passBudget = some 0 := by decide
-- The last paid unit includes this final review, with no new unit or pass needed.
example : guardReviewFlight repaired .architecture .codexCli := by
  have hs : repaired.stage = .fixOrDone := by decide
  have hr : repaired.reviewReady = true := by decide
  have hn : (repaired.batchFlights.any fun f => f.stage == .review && f.role == .architecture) = false := by decide
  simp [guardReviewFlight, hs, hr, hn, reviewRoles, seatEligible]
example : ¬ allowed repaired (.pass .repairWithRerunReview family (some twoParts)) := by
  have hb : repaired.passBudget = some 0 := by decide
  simp [allowed, guardPass, Transition.counted, hb]

private def repairReviews := step (step (step repaired
  (.openFlight .review .architecture .codexCli "target" 0))
  (.openFlight .review .quality .codexCli "target" 0))
  (.openFlight .review .tests .codexCli "target" 0)
private def repairReviewed := finish (finish (finish repairReviews 7 successful) 8 successful) 9 successful
-- Kernel reduction traverses both full batches and their flight handshakes.
set_option maxRecDepth 4096 in
example : repairReviewed.reviewComplete = true := by decide
example : repairReviewed.passBudget = some 0 := by decide
example : ¬ allowed repairReviewed (.pass .repairWithRerunReview family (some twoParts)) := by
  have hb : repairReviewed.passBudget = some 0 := by decide
  simp [allowed, guardPass, Transition.counted, hb]
example : ¬ allowed repairReviewed (.pass .repeatedReviewPass family none) := by
  have hb : repairReviewed.passBudget = some 0 := by decide
  simp [allowed, guardPass, Transition.counted, hb]
-- A separately funded repeated review remains available and resets only the review window.
private def repeated := step funded (.pass .repeatedReviewPass family none)
example : allowed funded (.pass .repeatedReviewPass family none) := by
  have hs : funded.stage = .fixOrDone := by decide
  have hr : funded.reviewComplete = true := by decide
  have hb : funded.passBudget = some 1 := by decide
  simp [allowed, guardPass, hs, hr, hb, family, familyPassAllowed, familyRoute,
    ownerAuthorizedDomainChange, classGateActive, FamilyEvidence.recordComplete,
    coverageBasisVerified, Transition.counted]
example : repeated.reviewReady = true := by decide
example : repeated.reviewComplete = false := by decide
example : ¬ guardImplementationFlight repeated := by
  have hr : repeated.reviewReady = true := by decide
  simp [guardImplementationFlight, hr]
example : ¬ allowed funded (.pass .repairWithRerunReview
    { family with coverageBasis := .unsupportedMemberEnumeration } (some twoParts)) := by
  simp [allowed, guardPass, familyPassAllowed, familyRoute, family,
    classGateActive, ownerAuthorizedDomainChange, coverageBasisVerified]

-- Active, terminal-with-failed-checks, and exhausted incomplete assignments never admit review.
example : opened.reviewReady = false := by decide
private def unchecked := step (finish second 1 successful)
  (.recordImplementation 1 ["verification"] false)
example : unchecked.reviewReady = false := by decide
private def failed := finish second 1 { successful with exitZero := false }
example : failed.batchSettled = false := by decide
example : failed.reviewReady = false := by decide
private def exhausted := step (finish second 1 successful)
  (.recordImplementation 1 [] true)
example : exhausted.reviewReady = false := by decide
example : ¬ guardImplementationFlight exhausted := by
  have hb : exhausted.batch = some ⟨["verification"], true, 0, 0⟩ := by decide
  simp [guardImplementationFlight, hb]
-- A single flight still goes directly to its one initial triplet after all checks.
private def single := step (finish (step (step readyState
  (.beginImplementation ⟨["all work"], 1⟩))
  (.openFlight .implementation .implementation .codexCli "target" 0)) 0 successful)
  (.recordImplementation 0 ["all work"] true)
example : single.reviewReady = true := by decide

/-- A trace checks each production guard at the state produced by its preceding actions. -/
private def admitted (s : ProtocolState) : List Action → Prop
  | [] => True
  | a :: rest => allowed s a ∧ admitted (step s a) rest

private def run (s : ProtocolState) (actions : List Action) : ProtocolState :=
  actions.foldl step s

private def implementationActions (firstId : Nat) : List Action :=
  [.openFlight .implementation .implementation .codexCli "target" 0,
   .launchViaRunner firstId, .hostNotified firstId, .collect firstId successful,
   .recordImplementation firstId ["contract"] true,
   .openFlight .implementation .implementation .codexCli "target" 0,
   .launchViaRunner (firstId + 1), .hostNotified (firstId + 1), .collect (firstId + 1) successful,
   .recordImplementation (firstId + 1) ["verification"] true]
private def reviewActions (firstId : Nat) : List Action :=
  [.openFlight .review .architecture .codexCli "target" 0,
   .openFlight .review .quality .codexCli "target" 0,
   .openFlight .review .tests .codexCli "target" 0,
   .launchViaRunner firstId, .hostNotified firstId, .collect firstId successful,
   .launchViaRunner (firstId + 1), .hostNotified (firstId + 1), .collect (firstId + 1) successful,
   .launchViaRunner (firstId + 2), .hostNotified (firstId + 2), .collect (firstId + 2) successful]

-- The earlier effect projections also have complete guard admission, including handshakes.
example : admitted readyState [.beginImplementation twoParts] := by
  simp [admitted, allowed, guardBeginImplementation, readyState, ProtocolState.initial,
    twoParts, ImplementationPlan.valid]
example : admitted begin (implementationActions 0) := by
  simp [admitted, implementationActions, allowed, guardOpenFlight, guardImplementationFlight,
    guardLaunchViaRunner, guardHostNotified, guardCollect, guardRecordImplementation,
    begin, readyState, twoParts, ProtocolState.initial, ProtocolState.modeResolved,
    ProtocolState.abstained, ProtocolState.reviewStarted, ProtocolState.reviewReady,
    ProtocolState.batchSettled, ProtocolState.batchFlights, ProtocolState.flight,
    ProtocolState.freshId, step, ImplementationPlan.start, newFlight, updateFlight,
    collectEffect, done, successful, FlightRec.active]
example : run begin (implementationActions 0) = complete := rfl
example : admitted review (reviewActions 2) := by
  simp [admitted, reviewActions, allowed, guardOpenFlight, guardReviewFlight,
    guardLaunchViaRunner, guardHostNotified, guardCollect, seatEligible, canRunRepositoryCommands,
    review, complete, second, partDone, returned, opened, begin, readyState, finish, twoParts,
    ProtocolState.initial, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.reviewReady, ProtocolState.batchSettled, ProtocolState.batchFlights,
    ProtocolState.flight, ProtocolState.freshId, step, ImplementationPlan.start, newFlight,
    updateFlight, collectEffect, done, successful, FlightRec.active, reviewRoles, Stage.next]
example : run review (reviewActions 2) = reviewed := rfl
example : allowed reviewed .advanceStage := by
  simp only [allowed, guardAdvanceStage, ProtocolState.goalWritten, ProtocolState.modeResolved,
    ProtocolState.abstained]
  decide
example : allowed fix (.recordPassBudget 1) := by
  simp only [allowed, guardRecordPassBudget]
  decide

-- A paid repair uses identical whole-plan admission and still includes its review at zero.
set_option maxRecDepth 4096 in
example : admitted repair (implementationActions 5) := by
  simp [admitted, implementationActions, allowed, guardOpenFlight, guardImplementationFlight,
    guardLaunchViaRunner, guardHostNotified, guardCollect, guardRecordImplementation,
    repair, funded, fix, reviewed, tests, quality, architecture, review, complete, second,
    partDone, returned, opened, begin, readyState, finish, twoParts, ProtocolState.initial,
    ProtocolState.modeResolved, ProtocolState.abstained, ProtocolState.reviewStarted,
    ProtocolState.reviewReady, ProtocolState.batchSettled, ProtocolState.batchFlights,
    ProtocolState.flight, ProtocolState.freshId, step, ImplementationPlan.start, newFlight,
    updateFlight, collectEffect, done, successful, FlightRec.active, Stage.next]
example : run repair (implementationActions 5) = repaired := rfl
set_option maxRecDepth 4096 in
example : admitted repaired (reviewActions 7) := by
  simp [admitted, reviewActions, allowed, guardOpenFlight, guardReviewFlight,
    guardLaunchViaRunner, guardHostNotified, guardCollect, seatEligible, canRunRepositoryCommands,
    repaired, repairedFirst, repair, funded, fix, reviewed, tests, quality, architecture,
    review, complete, second, partDone, returned, opened, begin, readyState, finish, twoParts,
    ProtocolState.initial, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.reviewReady, ProtocolState.batchSettled, ProtocolState.batchFlights,
    ProtocolState.flight, ProtocolState.freshId, step, ImplementationPlan.start, newFlight,
    updateFlight, collectEffect, done, successful, FlightRec.active, reviewRoles, Stage.next]
example : run repaired (reviewActions 7) = repairReviewed := rfl

private def recoveryActions (observation : Observation) : List Action :=
  [.beginImplementation ⟨["all work"], 1⟩,
   .openFlight .implementation .implementation .codexCli "target" 0,
   .launchViaRunner 0, .hostNotified 0, .collect 0 { successful with exitZero := false },
   .fallbackFlight 0 .isolatedTokenSubagent,
   .launchDelegated 1, .hostNotified 1, .collect 1 observation]

-- Both success and exhaustion traverse launch, notification and collection on each carrier.
private theorem recovery_admitted (observation : Observation) :
    admitted readyState (recoveryActions observation) := by
  simp [admitted, recoveryActions, allowed, guardBeginImplementation, ImplementationPlan.valid,
    guardOpenFlight, guardImplementationFlight, guardLaunchViaRunner, guardLaunchDelegated,
    guardHostNotified, guardCollect, guardFallback, readyState, ProtocolState.initial,
    ProtocolState.modeResolved, ProtocolState.abstained, ProtocolState.reviewStarted,
    ProtocolState.reviewReady, ProtocolState.batchSettled, ProtocolState.batchFlights,
    ProtocolState.flight, ProtocolState.freshId, ProtocolState.triedCarriers,
    ProtocolState.eligibleCarriers, step, ImplementationPlan.start, newFlight, reopenFlight,
    updateFlight, collectEffect, done, successful, FlightRec.active, nextCarrier,
    Carrier.univ, CarrierSet.get]

private def recoveryReturned := run readyState (recoveryActions successful)
example : allowed recoveryReturned (.recordImplementation 1 ["all work"] true) := by
  simp [allowed, guardRecordImplementation, recoveryReturned, run, recoveryActions,
    readyState, ProtocolState.initial, step, ProtocolState.reviewStarted,
    ProtocolState.batchFlights, ProtocolState.flight, ProtocolState.freshId,
    newFlight, reopenFlight, ImplementationPlan.start, updateFlight, collectEffect, done, successful]
private def recovered := step recoveryReturned (.recordImplementation 1 ["all work"] true)
example : recovered.reviewReady = true := by decide
example : allowed recovered .advanceStage := by
  simp only [allowed, guardAdvanceStage, ProtocolState.goalWritten, ProtocolState.modeResolved, ProtocolState.abstained]
  decide
example : recovered.passBudget = none := by decide

-- Detected initial implementation is heca-first; failure recovers through the unchanged
-- priority selector to Codex. Neither retry nor fallback spends a pass-budget unit.
private def hecaReady (budget : Option Nat) : ProtocolState :=
  { readyState with
    capabilityChecked := Carrier.univ, passBudget := budget, mode := some (.carrier .heca) }

private def ownStop : HecaStopEvidence := ⟨true, true, true⟩
private def hecaInitialActions (retries : Nat) : List Action :=
  [.beginImplementation ⟨["all work"], 1⟩,
   .openFlight .implementation .implementation .heca "target" retries,
   .launchDelegated 0]
private def hecaInFlight (budget : Option Nat) (retries : Nat) :=
  run (hecaReady budget) (hecaInitialActions retries)

private def archiveFlight : FlightRec :=
  { newFlight 0 .implementation .implementation .heca "target" 0 with
    launched := true, notified := true }
private def archiveNotifiedState : ProtocolState :=
  { ProtocolState.initial with
    stage := .implementation, goal := some goal, mode := some (.carrier .heca),
    flights := [archiveFlight] }
private def archiveCollectedFlight : FlightRec :=
  { archiveFlight with status := .terminal }
private def archiveCollectedState : ProtocolState :=
  { archiveNotifiedState with
    flights := [archiveCollectedFlight],
    housekeeping := [⟨0, 0, true, none⟩] }
private def archiveCollectedBatchState : ProtocolState :=
  { archiveCollectedState with
    batch := some ⟨["all work"], false, 1, 0⟩ }

private def failedArchiveFlight : FlightRec :=
  { newFlight 0 .implementation .implementation .heca "target" 1 with
    status := .retrying, attempt := 1 }
private def failedCollectedState : ProtocolState :=
  { archiveNotifiedState with
    flights := [failedArchiveFlight], housekeeping := [⟨0, 0, true, none⟩] }

-- Production archive admission is after collection and own-agent scoped; the same
-- production guards refuse the pre-collection notice window and another agent.
example : allowed archiveCollectedState (.archiveHecaAttempt 0 true .archived) := by
  simp [allowed, guardArchiveHeca, archiveCollectedState, archiveNotifiedState,
    archiveFlight, archiveCollectedFlight, ProtocolState.initial, newFlight,
    ProtocolState.attemptCollected,
    ProtocolState.attemptSettled,
    ProtocolState.housekeepingFor, ProtocolState.flight]
example : ¬ allowed archiveCollectedState (.archiveHecaAttempt 0 false .archived) := by
  exact archive_other_agent_refused archiveCollectedState 0 false .archived rfl
example : (failedCollectedState.flight 0).map (fun f => (f.status, f.attempt)) =
    some (.retrying, 1) := by decide
example : allowed failedCollectedState (.archiveHecaAttempt 0 true .failedReported) := by
  simp [allowed, guardArchiveHeca, failedCollectedState, failedArchiveFlight,
    archiveNotifiedState, archiveFlight, ProtocolState.initial, newFlight,
    ProtocolState.pendingHousekeeping, ProtocolState.flight]
example : ¬ allowed failedCollectedState (.launchDelegated 0) := by
  simp [allowed, guardLaunchDelegated, failedCollectedState, failedArchiveFlight,
    archiveNotifiedState, archiveFlight, ProtocolState.initial, newFlight,
    ProtocolState.attemptSettled, ProtocolState.pendingHousekeeping,
    ProtocolState.flight]
example : allowed (step failedCollectedState (.archiveHecaAttempt 0 true .archived))
    (.launchDelegated 0) := by
  simp [allowed, guardLaunchDelegated, failedCollectedState, failedArchiveFlight,
    archiveNotifiedState, archiveFlight, ProtocolState.initial, newFlight, step,
    ProtocolState.attemptSettled, ProtocolState.pendingHousekeeping,
    ProtocolState.flight, recordPendingArchive]
example : ¬ allowed archiveCollectedBatchState
    (.recordImplementation 0 ["all work"] true) := by
  simp [allowed, guardRecordImplementation, archiveCollectedBatchState,
    archiveCollectedState, archiveNotifiedState, archiveFlight, archiveCollectedFlight,
    ProtocolState.initial, newFlight,
    ProtocolState.batchFlights, ProtocolState.reviewStarted,
    ProtocolState.attemptSettled, ProtocolState.housekeepingFor, ProtocolState.flight]
example : ¬ allowed
    (step archiveCollectedBatchState (.recordImplementation 0 ["all work"] true)) .advanceStage := by
  simp [allowed, guardAdvanceStage, archiveCollectedBatchState,
    archiveCollectedState, archiveNotifiedState, archiveFlight, archiveCollectedFlight,
    ProtocolState.initial, newFlight, step,
    ProtocolState.batchFlights, ProtocolState.reviewStarted,
    ProtocolState.attemptSettled, ProtocolState.housekeepingFor,
    ProtocolState.allHecaHousekeepingSettled, ProtocolState.modeResolved,
    ProtocolState.abstained, ProtocolState.flight, FlightRec.active, Stage.next]
theorem heca_first_implementation_admitted (budget : Option Nat) (retries : Nat) :
    admitted (hecaReady budget) (hecaInitialActions retries) := by
  simp [admitted, hecaInitialActions, hecaReady, allowed, guardBeginImplementation,
    ImplementationPlan.valid, guardOpenFlight, guardImplementationFlight,
    guardLaunchDelegated, readyState, ProtocolState.initial, ProtocolState.modeResolved,
    ProtocolState.abstained, ProtocolState.reviewStarted, ProtocolState.reviewReady,
    ProtocolState.batchSettled, ProtocolState.batchFlights, ProtocolState.flight,
    ProtocolState.freshId, step, ImplementationPlan.start, newFlight, FlightRec.active,
    Carrier.univ]

theorem heca_stopped_no_notice_failure_admitted (budget : Option Nat) (retries : Nat) :
    ((hecaInFlight budget retries).flight 0).map (·.notified) = some false ∧
    allowed (hecaInFlight budget retries) (.recordStoppedHecaFailure 0 ownStop) := by
  simp [allowed, guardStoppedHecaFailure, hecaInFlight, run, hecaInitialActions,
    hecaReady, readyState, ProtocolState.initial, ProtocolState.flight,
    ProtocolState.freshId, step, ImplementationPlan.start, newFlight, updateFlight,
    FlightRec.active, ownStop, HecaStopEvidence.confirmed]

theorem heca_unnotified_success_refused (budget : Option Nat) (retries : Nat) :
    ¬ allowed (hecaInFlight budget retries) (.collect 0 successful) := by
  simp [allowed, guardCollect, hecaInFlight, run, hecaInitialActions, hecaReady,
    readyState, ProtocolState.initial, ProtocolState.flight, ProtocolState.freshId,
    step, ImplementationPlan.start, newFlight, updateFlight]

theorem heca_running_unnotified_failure_refused (budget : Option Nat) (retries : Nat) :
    ¬ allowed (hecaInFlight budget retries)
      (.recordStoppedHecaFailure 0 ⟨true, true, false⟩) ∧
    ¬ allowed (hecaInFlight budget retries) (.collect 0 stoppedHecaFailure) ∧
    ¬ allowed (hecaInFlight budget retries) (.mutateTarget "target") := by
  simp [allowed, guardStoppedHecaFailure, guardCollect, guardMutateTarget,
    hecaInFlight, run, hecaInitialActions, hecaReady, readyState, ProtocolState.initial,
    ProtocolState.flight, ProtocolState.freshId, ProtocolState.activeOn, step,
    ImplementationPlan.start, newFlight, updateFlight, FlightRec.active,
    HecaStopEvidence.confirmed]

private def hecaRetried (budget : Option Nat) :=
  step (hecaInFlight budget 1) (.recordStoppedHecaFailure 0 ownStop)

theorem heca_stopped_no_notice_retry (budget : Option Nat) :
    ((hecaRetried budget).flight 0).map (fun f =>
      (f.status, f.attempt, f.envelopeRef, f.sentinelRef, f.notified)) =
      some (.retrying, 1, none, none, false) ∧
    ¬ allowed (hecaRetried budget) (.recordStoppedHecaFailure 0 ownStop) ∧
    ¬ allowed (hecaRetried budget) (.mutateTarget "target") := by
  simp [hecaRetried, hecaInFlight, run, hecaInitialActions, hecaReady, readyState,
    ProtocolState.initial, ProtocolState.flight, ProtocolState.freshId,
    ProtocolState.activeOn, step, ImplementationPlan.start, newFlight, updateFlight,
    collectEffect, stoppedHecaFailure, done, FlightRec.active, allowed,
    guardStoppedHecaFailure, guardMutateTarget]

private def hecaRecoveryActions : List Action :=
  hecaInitialActions 1 ++
    [.recordStoppedHecaFailure 0 ownStop,
     .launchDelegated 0, .recordStoppedHecaFailure 0 ownStop,
     .fallbackFlight 0 .codexCli,
     .launchViaRunner 1, .hostNotified 1, .collect 1 successful,
     .recordImplementation 1 ["all work"] true, .advanceStage]

theorem heca_stop_without_notice_recovery_admitted (budget : Option Nat) :
    admitted (hecaReady budget) hecaRecoveryActions := by
  simp [admitted, hecaRecoveryActions, hecaInitialActions, hecaReady, allowed,
    guardBeginImplementation, ImplementationPlan.valid, guardOpenFlight,
    guardImplementationFlight, guardLaunchViaRunner, guardLaunchDelegated,
    guardStoppedHecaFailure, ownStop, HecaStopEvidence.confirmed, guardHostNotified,
    guardCollect, guardFallback, guardRecordImplementation, guardAdvanceStage,
    readyState, ProtocolState.initial, ProtocolState.modeResolved,
    ProtocolState.abstained, ProtocolState.reviewStarted, ProtocolState.reviewReady,
    ProtocolState.batchSettled, ProtocolState.batchFlights, ProtocolState.flight,
    ProtocolState.freshId, ProtocolState.triedCarriers, ProtocolState.eligibleCarriers,
    step, ImplementationPlan.start, newFlight, reopenFlight, updateFlight,
    collectEffect, stoppedHecaFailure, done, successful, FlightRec.active,
    nextCarrier, Carrier.univ, CarrierSet.get, Stage.next]

private def hecaRecovered (budget : Option Nat) := run (hecaReady budget) hecaRecoveryActions

theorem heca_stop_without_notice_recovery_settles (budget : Option Nat) :
    ((hecaRecovered budget).flight 0).map (fun f =>
      (f.status, f.attempt, f.envelopeRef, f.sentinelRef, f.notified)) =
      some (.abstained, 1, none, none, false) ∧
    ((hecaRecovered budget).flight 1).map (·.carrier) = some .codexCli ∧
    ((hecaRecovered budget).flight 1).map (·.status) = some .terminal ∧
    (hecaRecovered budget).reviewReady = true ∧
    (hecaRecovered budget).stage = .reviewTriplet ∧
    (hecaRecovered budget).passBudget = budget := by
  simp [hecaRecovered, run, hecaRecoveryActions, hecaInitialActions, hecaReady,
    readyState, ProtocolState.initial, step, ImplementationPlan.start, newFlight,
    reopenFlight, updateFlight, collectEffect, stoppedHecaFailure, done, successful,
    ProtocolState.reviewReady, ProtocolState.batchSettled, ProtocolState.batchFlights,
    ProtocolState.flight, ProtocolState.freshId, FlightRec.active, Stage.next]

example : admitted (hecaReady none) hecaRecoveryActions :=
  heca_stop_without_notice_recovery_admitted none
example : admitted (hecaReady (some 0)) hecaRecoveryActions :=
  heca_stop_without_notice_recovery_admitted (some 0)

-- Notice/read/archive/status are distinct production acts. These states are composed
-- from begin/open/launch, rather than hand-built flight or housekeeping records.
private def unreadable : Observation := ⟨false, false, false, false, false⟩
private def hecaNotified (budget : Option Nat) (retries : Nat) :=
  step (hecaInFlight budget retries) (.hostNotified 0)
private def hecaUnreadableCollected (budget : Option Nat) (retries : Nat) :=
  step (hecaNotified budget retries) (.collect 0 unreadable)
private def hecaUnreadableArchived (budget : Option Nat) (retries : Nat)
    (outcome : ArchiveOutcome := .archived) :=
  step (hecaUnreadableCollected budget retries) (.archiveHecaAttempt 0 true outcome)
private def hecaUnreadableRecoveryActions (outcome : ArchiveOutcome := .archived) : List Action :=
  hecaInitialActions 1 ++ [.hostNotified 0, .collect 0 unreadable,
    .archiveHecaAttempt 0 true outcome, .recordStoppedHecaFailure 0 ownStop,
    .launchDelegated 0]

theorem heca_notified_unreadable_recovery_admitted (budget : Option Nat)
    (outcome : ArchiveOutcome := .archived) :
    admitted (hecaReady budget) (hecaUnreadableRecoveryActions outcome) := by
  cases outcome <;> simp [admitted, hecaUnreadableRecoveryActions, hecaInitialActions, hecaReady, allowed,
    guardBeginImplementation, ImplementationPlan.valid, guardOpenFlight,
    guardImplementationFlight, guardLaunchDelegated, guardHostNotified, guardCollect,
    guardArchiveHeca, guardStoppedHecaFailure, ownStop, HecaStopEvidence.confirmed,
    readyState, ProtocolState.initial, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.reviewStarted, ProtocolState.reviewReady, ProtocolState.batchSettled,
    ProtocolState.batchFlights, ProtocolState.flight, ProtocolState.freshId,
    step, ImplementationPlan.start, newFlight, updateFlight, recordPendingArchive,
    collectEffect, stoppedHecaFailure, unreadable, done, FlightRec.active, Carrier.univ]

theorem heca_notice_window_archive_and_stop_refused (budget : Option Nat) (retries : Nat) :
    ¬ allowed (hecaNotified budget retries) (.archiveHecaAttempt 0 true .archived) ∧
    ¬ allowed (hecaNotified budget retries) (.recordStoppedHecaFailure 0 ownStop) := by
  simp [allowed, guardArchiveHeca, guardStoppedHecaFailure,
    hecaNotified, hecaInFlight, run, hecaInitialActions, hecaReady,
    readyState, ProtocolState.initial, step, ProtocolState.flight, ProtocolState.freshId,
    ImplementationPlan.start, newFlight, updateFlight, ownStop, HecaStopEvidence.confirmed]

theorem heca_unreadable_collection_records_failure (budget : Option Nat) (retries : Nat) :
    ((hecaUnreadableCollected budget retries).flight 0).map (fun f =>
      (f.status, f.attempt, f.envelopeRef, f.sentinelRef, f.launched, f.notified)) =
      some (.inFlight, 0, none, none, true, true) ∧
    ((hecaUnreadableCollected budget retries).pendingHousekeeping 0).map
      (fun h => (h.attempt, h.collected, h.archive)) = some (0, true, none) := by
  simp [hecaUnreadableCollected, hecaNotified, hecaInFlight, run, hecaInitialActions, hecaReady,
    readyState, ProtocolState.initial, step, ProtocolState.flight, ProtocolState.freshId,
    ImplementationPlan.start, newFlight, updateFlight, unreadable]

theorem heca_unreadable_without_confirmation_refused (budget : Option Nat) (retries : Nat)
    (carrier : Carrier) (outcome : ArchiveOutcome := .archived) :
    ¬ allowed (hecaUnreadableArchived budget retries outcome) (.launchDelegated 0) ∧
    ¬ allowed (hecaUnreadableArchived budget retries outcome) (.fallbackFlight 0 carrier) ∧
    ¬ allowed (hecaUnreadableArchived budget retries outcome)
      (.recordStoppedHecaFailure 0 ⟨true, true, false⟩) ∧
    ¬ allowed (hecaUnreadableArchived budget retries outcome) (.mutateTarget "target") ∧
    ¬ allowed (hecaUnreadableArchived budget retries outcome) (.collect 0 successful) := by
  cases outcome <;> simp [allowed, guardLaunchDelegated, guardFallback, guardStoppedHecaFailure,
    guardMutateTarget, guardCollect, hecaUnreadableArchived, hecaUnreadableCollected,
    hecaNotified, hecaInFlight, run, hecaInitialActions, hecaReady,
    readyState, ProtocolState.initial, step, ProtocolState.flight, ProtocolState.freshId,
    ImplementationPlan.start, newFlight, updateFlight, unreadable, recordPendingArchive,
    ProtocolState.activeOn, FlightRec.active, HecaStopEvidence.confirmed]

theorem heca_unreadable_requires_archive_before_confirmation (budget : Option Nat)
    (retries : Nat) :
    ¬ allowed (hecaUnreadableCollected budget retries) (.recordStoppedHecaFailure 0 ownStop) := by
  simp [allowed, guardStoppedHecaFailure, hecaUnreadableCollected,
    hecaNotified, hecaInFlight, run, hecaInitialActions, hecaReady,
    readyState, ProtocolState.initial, step, ProtocolState.flight, ProtocolState.freshId,
    ImplementationPlan.start, newFlight, updateFlight, unreadable, recordPendingArchive,
    ownStop, HecaStopEvidence.confirmed, FlightRec.active]

theorem heca_unreadable_confirmed_retry_settles (budget : Option Nat)
    (outcome : ArchiveOutcome := .archived) :
    let settled := step (hecaUnreadableArchived budget 1 outcome) (.recordStoppedHecaFailure 0 ownStop)
    (settled.flight 0).map (fun f =>
      (f.status, f.attempt, f.envelopeRef, f.sentinelRef, f.launched, f.notified)) =
      some (.retrying, 1, none, none, false, false) ∧
    settled.pendingHousekeeping 0 = none ∧ settled.passBudget = budget ∧
    settled.housekeeping.head?.map (fun h => (h.attempt, h.archive)) =
      some (0, some outcome) := by
  cases outcome <;> simp [hecaUnreadableArchived, hecaUnreadableCollected, hecaNotified, hecaInFlight, run, hecaInitialActions, hecaReady,
    readyState, ProtocolState.initial, step, ProtocolState.flight, ProtocolState.freshId,
    ImplementationPlan.start, newFlight, updateFlight,
    unreadable, collectEffect, stoppedHecaFailure, done, recordPendingArchive]

theorem heca_unreadable_confirmed_exhaustion_admitted (budget : Option Nat)
    (outcome : ArchiveOutcome := .archived) :
    allowed (hecaUnreadableArchived budget 0 outcome) (.recordStoppedHecaFailure 0 ownStop) ∧
    allowed (step (hecaUnreadableArchived budget 0 outcome) (.recordStoppedHecaFailure 0 ownStop))
      (.fallbackFlight 0 .codexCli) := by
  cases outcome <;> simp [allowed, guardStoppedHecaFailure, guardFallback,
    hecaUnreadableArchived, hecaUnreadableCollected, hecaNotified, hecaInFlight, run, hecaInitialActions, hecaReady,
    readyState, ProtocolState.initial, step, ProtocolState.flight, ProtocolState.freshId,
    ImplementationPlan.start, newFlight, updateFlight,
    unreadable, collectEffect, stoppedHecaFailure, done, recordPendingArchive,
    ownStop, HecaStopEvidence.confirmed, FlightRec.active, ProtocolState.eligibleCarriers,
    ProtocolState.triedCarriers, nextCarrier, Carrier.univ, CarrierSet.get]

-- Both archive outcomes admit the complete retry trace; failed cleanup remains reported.
theorem heca_reported_archive_retry_admitted (budget : Option Nat) :
    admitted (hecaReady budget) (hecaUnreadableRecoveryActions .failedReported) :=
  heca_notified_unreadable_recovery_admitted budget .failedReported

private def hecaUnreadableExhaustionActions : List Action :=
  hecaInitialActions 0 ++ [.hostNotified 0, .collect 0 unreadable,
    .archiveHecaAttempt 0 true .failedReported,
    .recordStoppedHecaFailure 0 ⟨true, false, true⟩, .fallbackFlight 0 .codexCli]

theorem heca_reported_archive_exhausted_fallback_admitted (budget : Option Nat) :
    admitted (hecaReady budget) hecaUnreadableExhaustionActions := by
  simp [admitted, hecaUnreadableExhaustionActions, hecaInitialActions, hecaReady, allowed,
    guardBeginImplementation, ImplementationPlan.valid, guardOpenFlight,
    guardImplementationFlight, guardLaunchDelegated, guardHostNotified, guardCollect,
    guardArchiveHeca, guardStoppedHecaFailure, guardFallback,
    readyState, ProtocolState.initial, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.reviewStarted, ProtocolState.reviewReady, ProtocolState.batchSettled,
    ProtocolState.batchFlights, ProtocolState.flight, ProtocolState.freshId,
    ProtocolState.eligibleCarriers, ProtocolState.triedCarriers,
    step, ImplementationPlan.start, newFlight, updateFlight, recordPendingArchive,
    collectEffect, stoppedHecaFailure, unreadable, done, FlightRec.active,
    nextCarrier, Carrier.univ, CarrierSet.get]

theorem heca_reported_archive_exhaustion_preserves_report (budget : Option Nat) :
    let recovered := run (hecaReady budget) hecaUnreadableExhaustionActions
    recovered.housekeeping.head?.map (fun h => (h.flightId, h.attempt, h.archive)) =
      some (0, 0, some .failedReported) ∧
    (recovered.flight 0).map (fun f => (f.status, f.envelopeRef, f.sentinelRef)) =
      some (.abstained, none, none) ∧
    (recovered.flight 1).map (·.carrier) = some .codexCli ∧
    recovered.passBudget = budget := by
  simp [hecaUnreadableExhaustionActions, hecaInitialActions, hecaReady, readyState,
    ProtocolState.initial, run, step, ProtocolState.flight, ProtocolState.freshId,
    ImplementationPlan.start, newFlight, reopenFlight, updateFlight, recordPendingArchive,
    unreadable, collectEffect, stoppedHecaFailure, done]

-- Ordinary successful collection must create a pending duty through the actual step.
private def hecaCollectionActions (o : Observation) : List Action :=
  hecaInitialActions 0 ++ [.hostNotified 0, .collect 0 o]
private def hecaCollected (budget : Option Nat) (o : Observation) :=
  run (hecaReady budget) (hecaCollectionActions o)
private def hecaSuccessActions (outcome : ArchiveOutcome) : List Action :=
  hecaCollectionActions successful ++ [.archiveHecaAttempt 0 true outcome,
    .recordImplementation 0 ["all work"] true, .advanceStage]

theorem heca_collection_admitted (budget : Option Nat) (o : Observation) :
    admitted (hecaReady budget) (hecaCollectionActions o) := by
  simp [admitted, hecaCollectionActions, hecaInitialActions, hecaReady, allowed,
    guardBeginImplementation, ImplementationPlan.valid, guardOpenFlight,
    guardImplementationFlight, guardLaunchDelegated, guardHostNotified, guardCollect,
    readyState, ProtocolState.initial, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.reviewStarted, ProtocolState.reviewReady, ProtocolState.batchSettled,
    ProtocolState.batchFlights, ProtocolState.flight, ProtocolState.freshId,
    step, ImplementationPlan.start, newFlight, updateFlight, FlightRec.active, Carrier.univ]

theorem heca_success_collect_record_refused (budget : Option Nat) :
    ¬ allowed (hecaCollected budget successful) (.recordImplementation 0 ["all work"] true) := by
  simp [allowed, guardRecordImplementation, hecaCollected, run, hecaCollectionActions,
    hecaInitialActions, hecaReady, readyState, ProtocolState.initial, step,
    ProtocolState.reviewStarted, ProtocolState.batchFlights, ProtocolState.flight,
    ProtocolState.freshId, ImplementationPlan.start, newFlight, updateFlight,
    collectEffect, successful, done]

-- The second refusal isolates the advance gate even if recording were bypassed.
theorem heca_success_collect_advance_refused (budget : Option Nat) :
    ¬ allowed (hecaCollected budget successful) .advanceStage ∧
    ¬ allowed (step (hecaCollected budget successful)
      (.recordImplementation 0 ["all work"] true)) .advanceStage := by
  simp [allowed, guardAdvanceStage, hecaCollected, run, hecaCollectionActions,
    hecaInitialActions, hecaReady, readyState, ProtocolState.initial, step,
    ProtocolState.flight, ProtocolState.freshId, ImplementationPlan.start,
    newFlight, updateFlight, collectEffect, successful, done]

theorem heca_success_archive_record_advance_admitted (budget : Option Nat)
    (outcome : ArchiveOutcome) :
    admitted (hecaReady budget) (hecaSuccessActions outcome) := by
  cases outcome <;> simp [admitted, hecaSuccessActions, hecaCollectionActions,
    hecaInitialActions, hecaReady, allowed, guardBeginImplementation, ImplementationPlan.valid,
    guardOpenFlight, guardImplementationFlight, guardLaunchDelegated, guardHostNotified,
    guardCollect, guardArchiveHeca, guardRecordImplementation, guardAdvanceStage,
    readyState, ProtocolState.initial, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.reviewStarted, ProtocolState.reviewReady, ProtocolState.batchSettled,
    ProtocolState.batchFlights, ProtocolState.flight, ProtocolState.freshId,
    step, ImplementationPlan.start, newFlight, updateFlight, recordPendingArchive,
    collectEffect, successful, done, FlightRec.active, Carrier.univ, Stage.next]

theorem heca_success_second_archive_refused (budget : Option Nat) :
    ¬ allowed (step (hecaCollected budget successful) (.archiveHecaAttempt 0 true .archived))
      (.archiveHecaAttempt 0 true .archived) := by
  simp [allowed, guardArchiveHeca, hecaCollected, run, hecaCollectionActions,
    hecaInitialActions, hecaReady, readyState, ProtocolState.initial, step,
    ProtocolState.flight, ProtocolState.freshId, ImplementationPlan.start,
    newFlight, updateFlight, collectEffect, successful, done, recordPendingArchive]

theorem heca_no_notice_stop_second_archive_refused (budget : Option Nat) :
    ¬ allowed (step (hecaInFlight budget 0) (.recordStoppedHecaFailure 0 ownStop))
      (.archiveHecaAttempt 0 true .archived) := by
  simp [allowed, guardArchiveHeca, hecaInFlight, run, hecaInitialActions, hecaReady,
    readyState, ProtocolState.initial, step, ProtocolState.flight, ProtocolState.freshId,
    ImplementationPlan.start, newFlight, updateFlight, collectEffect, stoppedHecaFailure, done]

theorem heca_failed_collect_fallback_requires_archive (budget : Option Nat) :
    let collected := hecaCollected budget { successful with exitZero := false }
    ¬ allowed collected (.fallbackFlight 0 .codexCli) ∧
    allowed (step collected (.archiveHecaAttempt 0 true .failedReported))
      (.fallbackFlight 0 .codexCli) := by
  simp [allowed, guardFallback, hecaCollected, run, hecaCollectionActions,
    hecaInitialActions, hecaReady, readyState, ProtocolState.initial, step,
    ProtocolState.flight, ProtocolState.freshId, ImplementationPlan.start,
    ProtocolState.eligibleCarriers, ProtocolState.triedCarriers,
    newFlight, updateFlight, collectEffect, successful, done, recordPendingArchive,
    nextCarrier, Carrier.univ, CarrierSet.get]

private def carrierExhausted := run readyState
  (recoveryActions { successful with exitZero := false })
example : carrierExhausted.reviewReady = false := by decide
example : carrierExhausted.batchSettled = false := by decide
-- Reusing either failure id cannot duplicate or cycle a carrier; the actual selector abstains.
example (id : Nat) (c : Carrier) : ¬ allowed carrierExhausted (.fallbackFlight id c) := by
  intro ha
  obtain ⟨f, hf, _, _, hn⟩ := ha
  simp [carrierExhausted, run, recoveryActions, readyState, ProtocolState.initial, step,
    ProtocolState.flight, ProtocolState.freshId, newFlight, reopenFlight, updateFlight,
    collectEffect, done, successful] at hf
  rcases hf with ⟨_, rfl⟩ | ⟨_, _, rfl⟩ <;>
    simp [ProtocolState.eligibleCarriers, ProtocolState.triedCarriers,
    carrierExhausted, run, recoveryActions, readyState, ProtocolState.initial, step,
    ProtocolState.flight, ProtocolState.freshId, newFlight, reopenFlight, updateFlight,
    collectEffect, done, successful, nextCarrier, Carrier.univ, CarrierSet.get] at hn
example : resolveSeat (carrierExhausted.eligibleCarriers (newFlight 0 .implementation
    .implementation .codexCli "target" 0)) (carrierExhausted.triedCarriers 0) = .abstain := by decide
example : ¬ allowed carrierExhausted .advanceStage := by
  have h : carrierExhausted.reviewReady = false := by decide
  have hs : carrierExhausted.stage = .implementation := by decide
  simp [allowed, guardAdvanceStage, h, hs]

-- A distinct approved assignment on the same target retains its own carrier history.
example : (second.flight 0).map (·.assignment) = some 0 := by decide
example : (second.flight 1).map (·.assignment) = some 1 := by decide
example : allowed failed (.fallbackFlight 1 .isolatedTokenSubagent) := by
  simp [allowed, guardFallback, failed, finish, second, partDone, returned, opened, begin,
    readyState, step, updateFlight, collectEffect, done, successful, twoParts,
    ImplementationPlan.start, ProtocolState.initial, ProtocolState.flight,
    ProtocolState.freshId, ProtocolState.triedCarriers, ProtocolState.eligibleCarriers,
    newFlight, nextCarrier, Carrier.univ, CarrierSet.get]

-- Test-seat restriction applies to initial, repeated, and included repair review alike.
example : ¬ allowed { review with capabilityChecked := Carrier.univ }
    (.openFlight .review .tests .nyxidOracle "target" 0) :=
  tests_dispatch_excludes_oracle _ _ _
example : allowed review (.openFlight .review .tests .isolatedTokenSubagent "target" 0) := by
  simp only [allowed, guardOpenFlight, guardReviewFlight, ProtocolState.modeResolved,
    ProtocolState.abstained]
  decide
example : ¬ allowed repeated (.openFlight .review .tests .nyxidOracle "target" 0) :=
  tests_dispatch_excludes_oracle _ _ _
example : ¬ allowed repaired (.openFlight .review .tests .nyxidOracle "target" 0) :=
  tests_dispatch_excludes_oracle _ _ _

private def fallbackOpened := run readyState ((recoveryActions successful).take 6)
-- Direct collection cannot stand in for the carrier's launch and completion notification.
example : ¬ allowed fallbackOpened (.collect 1 successful) := by
  simp [allowed, guardCollect, fallbackOpened, run, recoveryActions, readyState,
    ProtocolState.initial, step, ProtocolState.flight, ProtocolState.freshId,
    newFlight, reopenFlight, updateFlight, collectEffect, done, successful]
example : ¬ allowed fallbackOpened (.launchViaRunner 1) := by
  simp [allowed, guardLaunchViaRunner, fallbackOpened, run, recoveryActions, readyState,
    ProtocolState.initial, step, ProtocolState.flight, ProtocolState.freshId,
    newFlight, reopenFlight, updateFlight, collectEffect, done, successful]
example : ¬ allowed fallbackOpened (.fallbackFlight 0 .isolatedTokenSubagent) := by
  simp [allowed, guardFallback, fallbackOpened, run, recoveryActions, readyState,
    ProtocolState.initial, step, ProtocolState.flight, ProtocolState.freshId,
    newFlight, reopenFlight, updateFlight, collectEffect, done, successful]
example : ¬ allowed recoveryReturned (.fallbackFlight 0 .isolatedTokenSubagent) := by
  simp [allowed, guardFallback, recoveryReturned, run, recoveryActions, readyState,
    ProtocolState.initial, step, ProtocolState.flight, ProtocolState.freshId,
    newFlight, reopenFlight, updateFlight, collectEffect, done, successful]

example : admitted readyState
    [.beginImplementation ⟨["all work"], 1⟩,
     .openFlight .implementation .implementation .codexCli "target" 0,
     .launchViaRunner 0, .hostNotified 0, .collect 0 successful,
     .recordImplementation 0 ["all work"] true, .advanceStage] := by
  simp [admitted, allowed, guardBeginImplementation, ImplementationPlan.valid,
    guardOpenFlight, guardImplementationFlight, guardLaunchViaRunner, guardHostNotified,
    guardCollect, guardRecordImplementation, guardAdvanceStage, readyState,
    ProtocolState.initial, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.reviewStarted, ProtocolState.reviewReady, ProtocolState.batchSettled,
    ProtocolState.batchFlights, ProtocolState.flight, ProtocolState.freshId,
    step, ImplementationPlan.start, newFlight, updateFlight, collectEffect, done,
    successful, FlightRec.active, Stage.next]

private def testsReviewState := { review with capabilityChecked := [.codexCli, .nyxidOracle, .isolatedTokenSubagent] }
private def testsRecoveryActions (observation : Observation) : List Action :=
  [.openFlight .review .tests .codexCli "target" 0,
   .launchViaRunner 2, .hostNotified 2, .collect 2 { successful with exitZero := false },
   .fallbackFlight 2 .isolatedTokenSubagent,
   .launchDelegated 3, .hostNotified 3, .collect 3 observation]
-- Oracle is capability-checked and higher priority than the subagent, but ineligible here.
example (observation : Observation) : admitted testsReviewState (testsRecoveryActions observation) := by
  simp [admitted, testsRecoveryActions, testsReviewState, review, complete, second,
    partDone, returned, opened, begin, readyState, finish, twoParts, successful,
    allowed, guardOpenFlight, guardReviewFlight, seatEligible, canRunRepositoryCommands,
    guardLaunchViaRunner, guardLaunchDelegated, guardHostNotified, guardCollect, guardFallback,
    ProtocolState.initial, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.reviewReady, ProtocolState.batchSettled, ProtocolState.batchFlights,
    ProtocolState.flight, ProtocolState.freshId, ProtocolState.triedCarriers,
    ProtocolState.eligibleCarriers, step, ImplementationPlan.start, newFlight, reopenFlight,
    updateFlight, collectEffect, done, FlightRec.active, nextCarrier, Carrier.univ,
    CarrierSet.get, reviewRoles, Stage.next]
private def testsRecovered := run testsReviewState (testsRecoveryActions successful)
example : (testsRecovered.flight 3).map (·.status) = some .terminal := by decide
example : (testsRecovered.flight 3).map (·.carrier) = some .isolatedTokenSubagent := by decide
example : testsRecovered.reviewComplete = false := by decide

private def testsExhausted := run testsReviewState
  (testsRecoveryActions { successful with exitZero := false })
example : resolveSeat (testsExhausted.eligibleCarriers
    (newFlight 2 .review .tests .codexCli "target" 0))
    (testsExhausted.triedCarriers 2) = .abstain := by decide
example : testsExhausted.reviewComplete = false := by decide
example : ¬ allowed testsExhausted .advanceStage := by
  have hs : testsExhausted.stage = .reviewTriplet := by decide
  have hr : testsExhausted.reviewComplete = false := by decide
  simp [allowed, guardAdvanceStage, hs, hr]

-- An oracle review flight launches through its runner; the delegated launch refuses it.
private def oracleReview := step testsReviewState (.openFlight .review .quality .nyxidOracle "target" 0)
example : allowed oracleReview (.launchViaRunner 2) := by
  simp [allowed, guardLaunchViaRunner, oracleReview, testsReviewState, review, complete, second,
    partDone, returned, opened, begin, readyState, finish, twoParts, successful,
    ProtocolState.initial, ProtocolState.flight, ProtocolState.freshId, step,
    ImplementationPlan.start, newFlight, updateFlight, collectEffect, done]
example : ¬ allowed oracleReview (.launchDelegated 2) := by
  simp [allowed, guardLaunchDelegated, oracleReview, testsReviewState, review, complete, second,
    partDone, returned, opened, begin, readyState, finish, twoParts, successful,
    ProtocolState.initial, ProtocolState.flight, ProtocolState.freshId, step,
    ImplementationPlan.start, newFlight, updateFlight, collectEffect, done]

private def scopeNote : Revision :=
  ⟨"engineering scope note", "existing user authorization", "none"⟩
private def authorizedRevision (r : Revision) (source : Option ContinuationSource) : RevisionEvidence :=
  ⟨r, true, true, source⟩

private def startupActions (c : ContinuationEntry) : List Action :=
  [.writeGoal goal (intake c),
   .appendRevision scopeNote (authorizedRevision scopeNote none),
   .advanceStage, .capabilityCheck .codexCli, .recordHecaCheck false, .resolveMode (.carrier .codexCli), .advanceStage]
-- Heca records even an absent check once; it cannot redraw availability later.
example (s : ProtocolState) (first second : Bool) :
    ¬ allowed (step s (.recordHecaCheck first)) (.recordHecaCheck second) := by
  simp [allowed, guardHecaCheck, step]

example (s : ProtocolState) :
    (step s (.recordHecaCheck false)).capabilityChecked = s.capabilityChecked := rfl

example (s : ProtocolState) :
    Carrier.heca ∈ (step s (.recordHecaCheck true)).capabilityChecked := by
  simp [step]

example : allowed { review with capabilityChecked := Carrier.univ }
    (.openFlight .review .tests .heca "target" 0) := by
  simp only [allowed, guardOpenFlight, guardReviewFlight, ProtocolState.modeResolved,
    ProtocolState.abstained]
  decide

-- All authority states permit routine startup without a question, preserving revision support.
example (c : ContinuationEntry) : admitted ProtocolState.initial (startupActions c) := by
  simp [admitted, startupActions, allowed, guardWriteGoal, guardAppendRevision,
    guardAdvanceStage, guardCapabilityCheck, guardHecaCheck, guardResolveMode, Harness.complete,
    authorizedRevision, RevisionEvidence.valid,
    IntakeEvidence.complete, goal, intake, ProtocolState.initial, ProtocolState.goalWritten,
    ProtocolState.modeResolved, ProtocolState.abstained, step, GoalArtifact.correct, Stage.next]
example (c : ContinuationEntry) :
    (run ProtocolState.initial (startupActions c)).gate = applicability c := by rfl
example : (run ProtocolState.initial (startupActions .silent)).stage = .thinkingPanel := by decide
example : (run ProtocolState.initial (startupActions .ambiguous)).gate = .withholdClaim := by decide
example : (run ProtocolState.initial (startupActions .absent)).gate = .inapplicable := by decide
example : (run ProtocolState.initial (startupActions .unconfirmed)).gate = .withholdClaim := by decide
example : (run ProtocolState.initial (startupActions .present)).gate = .applies := by decide

-- T-R1-CORRECTION: start after the composed, guarded implementation and review above.
-- The supplied evidence interprets an explicit owner correction; no English parsing or
-- caller-inferred continuation mechanism is claimed here.
private def continuationRevision : Revision :=
  ⟨"owner corrects harness.provided_capabilities to declare host continuation",
    "explicit boundary-owner authorization", "earlier continuation projection"⟩
private def continuationSource (entry : ContinuationEntry) : ContinuationSource :=
  ⟨["owner's corrected provided_capabilities"], entry⟩
private def correctionEvidence (entry : ContinuationEntry) : RevisionEvidence :=
  authorizedRevision continuationRevision (some (continuationSource entry))
private def correctionAction (entry : ContinuationEntry) : Action :=
  .appendRevision continuationRevision (correctionEvidence entry)
private def corrected (entry : ContinuationEntry) := step fix (correctionAction entry)

-- All supplied source states follow the same update route, including withdrawal and
-- uncertainty; authority ownership/support, rather than a state-pair whitelist, guards it.
example (entry : ContinuationEntry) : allowed fix (correctionAction entry) := by
  simp only [allowed, correctionAction, guardAppendRevision, ProtocolState.goalWritten,
    RevisionEvidence.valid, correctionEvidence, authorizedRevision]
  decide
example (entry : ContinuationEntry) : (corrected entry).gate = applicability entry := by
  exact correction_projects_current_source fix continuationRevision
    (correctionEvidence entry) (continuationSource entry) rfl
example : (corrected .present).goal = some (goal.correct continuationRevision) := by decide
example : (corrected .present).terminationExit = none := by decide
example : ¬ allowed (corrected .present) .claimSatisfied := by
  simp only [allowed, guardClaimSatisfied]
  decide
example : ¬ allowed (corrected .ambiguous) .claimSatisfied := by
  simp only [allowed, guardClaimSatisfied]
  decide
example : ¬ allowed (corrected .unconfirmed) .claimSatisfied := by
  simp only [allowed, guardClaimSatisfied]
  decide
example : allowed (corrected .absent) .claimSatisfied := by
  simp only [allowed, guardClaimSatisfied]
  decide
example : allowed (corrected .silent) .claimSatisfied := by
  simp only [allowed, guardClaimSatisfied]
  decide

-- Nonempty revision text alone supplies neither ownership nor source support.
example (s : ProtocolState) : ¬ allowed s (.appendRevision continuationRevision
    { correctionEvidence .present with ownerAuthorized := false }) := by
  simp [allowed, guardAppendRevision, RevisionEvidence.valid]
example (s : ProtocolState) : ¬ allowed s (.appendRevision continuationRevision
    { correctionEvidence .present with sourceSupported := false }) := by
  simp [allowed, guardAppendRevision, RevisionEvidence.valid]
example (s : ProtocolState) : ¬ allowed s (.appendRevision scopeNote
    (correctionEvidence .present)) := by
  simp [allowed, guardAppendRevision, RevisionEvidence.valid, correctionEvidence,
    authorizedRevision, continuationRevision, scopeNote]

private def satisfiedRoster : Roster := ⟨true, .satisfied, .satisfied, .satisfied⟩
-- These inputs represent separately obtained evidence for the named source revisions.
-- Equal seat verdicts do not establish freshness; their supplied authority association does.
private def evidenceA1 : TerminationEvidence :=
  ⟨⟨continuationSource .present, 1⟩, satisfiedRoster⟩
private def evaluateA1 : Action := .evaluateTermination .terminationSeats evidenceA1
private def correctionFunded := step (corrected .present) (.recordPassBudget 2)
private def settled := step correctionFunded evaluateA1
-- Each action is guarded: evaluation consumes supplied evidence for the current source.
example : admitted fix [correctionAction .present, .recordPassBudget 2, evaluateA1, .claimSatisfied] := by
  simp only [admitted, correctionAction, evaluateA1, allowed, guardAppendRevision,
    RevisionEvidence.valid, guardRecordPassBudget, guardEvaluateTermination,
    guardClaimSatisfied, ProtocolState.goalWritten]
  decide
example : settled.terminationExit = some .claimPermitted := by decide
example : settled.terminationAuthority = some settled.continuationAuthority := by decide

-- A fresh termination flight can create housekeeping after the stage-advance gate.
-- This is why evaluation and claim retain their own settlement checks.
private def hecaTerminationCollectionActions : List Action :=
  hecaSuccessActions .archived ++ reviewActions 1 ++
    [.advanceStage, correctionAction .present, .recordPassBudget 1,
     .openFlight .termination .criterionEvidence .heca "target" 0,
     .launchDelegated 4, .hostNotified 4, .collect 4 successful]
private def hecaTerminationCollected :=
  run (hecaReady none) hecaTerminationCollectionActions
private def hecaTerminationEvidence : TerminationEvidence :=
  ⟨hecaTerminationCollected.continuationAuthority, satisfiedRoster⟩
private def hecaTerminationEvaluation : Action :=
  .evaluateTermination .terminationSeats hecaTerminationEvidence

set_option maxRecDepth 4096 in
theorem heca_termination_collection_admitted :
    admitted (hecaReady none) hecaTerminationCollectionActions := by
  simp [admitted, hecaTerminationCollectionActions, hecaSuccessActions, hecaCollectionActions,
    reviewActions, hecaInitialActions, hecaReady, allowed, guardBeginImplementation,
    ImplementationPlan.valid, guardOpenFlight, guardImplementationFlight, guardReviewFlight,
    guardLaunchViaRunner, guardLaunchDelegated, guardHostNotified, guardCollect,
    guardArchiveHeca, guardRecordImplementation, guardAdvanceStage, guardAppendRevision,
    guardRecordPassBudget, correctionAction, correctionEvidence, authorizedRevision,
    continuationRevision, RevisionEvidence.valid, ProtocolState.goalWritten,
    readyState, ProtocolState.initial, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.reviewStarted, ProtocolState.reviewReady, ProtocolState.reviewComplete,
    ProtocolState.batchSettled, ProtocolState.batchFlights, ProtocolState.flight,
    ProtocolState.freshId, reviewRoles, seatEligible, canRunRepositoryCommands,
    step, ImplementationPlan.start, newFlight, updateFlight, recordPendingArchive,
    collectEffect, successful, done, FlightRec.active, Carrier.univ, Stage.next,
    FlightStage.protocolStage]

set_option maxRecDepth 4096 in
theorem heca_termination_evaluate_and_claim_require_archive :
    ¬ allowed hecaTerminationCollected hecaTerminationEvaluation ∧
    ¬ allowed (step hecaTerminationCollected hecaTerminationEvaluation) .claimSatisfied ∧
    admitted hecaTerminationCollected [.archiveHecaAttempt 4 true .failedReported,
      hecaTerminationEvaluation, .claimSatisfied] := by
  simp only [admitted]
  refine ⟨?_, ?_, ?_, ?_, ?_, trivial⟩
  · have pending : hecaTerminationCollected.allHecaHousekeepingSettled = false := by decide
    simp only [hecaTerminationEvaluation, allowed, guardEvaluateTermination, pending]
    simp
  · have pending : (step hecaTerminationCollected hecaTerminationEvaluation).allHecaHousekeepingSettled =
        false := by decide
    simp only [allowed, guardClaimSatisfied, pending]
    simp
  · change guardArchiveHeca hecaTerminationCollected 4 true .failedReported
    refine ⟨{ newFlight 4 .termination .criterionEvidence .heca "target" 0 with
      status := .terminal, launched := true, notified := true,
      envelopeRef := some "result", sentinelRef := some "sentinel" },
      ⟨4, 0, true, none⟩, ?_, rfl, rfl, ?_, rfl, rfl, rfl⟩ <;> decide
  · simp only [hecaTerminationEvaluation, allowed, guardEvaluateTermination]
    refine ⟨?_, ?_, ?_, ?_, ?_, Or.inr ⟨1, ?_, by decide⟩⟩ <;> decide
  · simp only [allowed, guardClaimSatisfied]
    decide

-- Even a correction that stays `present` changes the source generation. Keep the old
-- affirmative settlement, but refuse both direct claim and resubmission of the old roster.
private def sourceRevision : Revision :=
  ⟨"owner corrects the declared continuation's scope", "explicit boundary-owner authorization",
    "termination evidence for the earlier continuation scope"⟩
private def sourceCorrection : Action :=
  .appendRevision sourceRevision (authorizedRevision sourceRevision
    (some ⟨["owner's corrected continuation scope"], .present⟩))
private def superseded := step settled sourceCorrection
example : allowed settled sourceCorrection := by
  simp only [sourceCorrection, allowed, guardAppendRevision, ProtocolState.goalWritten,
    RevisionEvidence.valid, authorizedRevision]
  decide
example : superseded.gate = settled.gate := by decide
example : superseded.continuationAuthority.revision = 2 := by decide
example : superseded.terminationExit = settled.terminationExit := by decide
example : superseded.terminationAuthority = settled.terminationAuthority := by decide
example : superseded.passBudget = settled.passBudget := by decide
example : superseded.goal = some ((goal.correct continuationRevision).correct sourceRevision) := by decide
example : ¬ allowed superseded .claimSatisfied := by
  apply stale_authority_cannot_claim
  · decide
  · decide
-- A-F2-ROSTER-SOURCE: the exact A1 evidence cannot be rebound by reevaluation at A2.
example : ¬ allowed superseded evaluateA1 := by
  apply stale_evidence_cannot_evaluate
  decide
example : ¬ admitted superseded [evaluateA1, .claimSatisfied] := by
  simp only [admitted, evaluateA1, allowed, guardEvaluateTermination, guardClaimSatisfied]
  decide
-- Even an unguarded effect preserves A1 rather than manufacturing A2 provenance.
example : (step superseded evaluateA1).terminationAuthority = some evidenceA1.authority := rfl
example : ¬ allowed (step superseded evaluateA1) .claimSatisfied := by
  apply stale_authority_cannot_claim
  · decide
  · decide

private def evidenceA2 : TerminationEvidence :=
  ⟨⟨⟨["owner's corrected continuation scope"], .present⟩, 2⟩, satisfiedRoster⟩
private def evaluateA2 : Action := .evaluateTermination .terminationSeats evidenceA2
example : evidenceA1.roster = evidenceA2.roster := rfl
example : evidenceA1.authority ≠ evidenceA2.authority := by decide
-- Fresh A2 evidence with the same verdicts is admitted through the actual consumer.
example : admitted superseded [evaluateA2, .claimSatisfied] := by
  simp only [admitted, evaluateA2, allowed, guardEvaluateTermination, guardClaimSatisfied]
  decide
example : (step superseded evaluateA2).terminationAuthority = some evidenceA2.authority := rfl
example : (step superseded evaluateA2).passBudget = some 0 := by decide
example : (run superseded [evaluateA2, .claimSatisfied]).claimed = true := by decide

-- A relevant repeated revision advances the ledger even with an identical source payload.
private def sourceRepeated := step settled (correctionAction .present)
private def repeatedEvidence : TerminationEvidence :=
  ⟨⟨continuationSource .present, 2⟩, satisfiedRoster⟩
example : ¬ admitted sourceRepeated [evaluateA1, .claimSatisfied] := by
  simp only [admitted, evaluateA1, allowed, guardEvaluateTermination, guardClaimSatisfied]
  decide
example : admitted sourceRepeated [.evaluateTermination .terminationSeats repeatedEvidence,
    .claimSatisfied] := by
  simp only [admitted, allowed, guardEvaluateTermination, guardClaimSatisfied]
  decide

-- Changes away from positive authority update the actual consumer too; uncertainty
-- withholds, while an owner-supported withdrawal needs no continuation evidence.
example (entry : ContinuationEntry) : admitted settled [correctionAction entry] := by
  simp only [admitted, correctionAction, allowed, guardAppendRevision,
    ProtocolState.goalWritten, RevisionEvidence.valid, correctionEvidence, authorizedRevision]
  decide
example : ¬ allowed (step settled (correctionAction .ambiguous)) .claimSatisfied := by
  simp only [allowed, guardClaimSatisfied]
  decide
example : allowed (step settled (correctionAction .absent)) .claimSatisfied := by
  simp only [allowed, guardClaimSatisfied]
  decide
-- Returning to an identical positive source cannot revive its earlier settlement or evidence.
example : admitted settled [correctionAction .absent, correctionAction .present] := by
  simp only [admitted, correctionAction, allowed, guardAppendRevision,
    ProtocolState.goalWritten, RevisionEvidence.valid, correctionEvidence, authorizedRevision]
  decide
example : ¬ allowed (step (step settled (correctionAction .absent))
    (correctionAction .present)) .claimSatisfied := by
  simp only [allowed, guardClaimSatisfied]
  decide
private def restored := step (step settled (correctionAction .absent)) (correctionAction .present)
private def restoredEvidence : TerminationEvidence :=
  ⟨⟨continuationSource .present, 3⟩, satisfiedRoster⟩
example : restored.continuationAuthority.source = evidenceA1.authority.source := by decide
example : ¬ admitted restored [evaluateA1, .claimSatisfied] := by
  simp only [admitted, evaluateA1, allowed, guardEvaluateTermination, guardClaimSatisfied]
  decide
example : admitted restored [.evaluateTermination .terminationSeats restoredEvidence,
    .claimSatisfied] := by
  simp only [admitted, allowed, guardEvaluateTermination, guardClaimSatisfied]
  decide
-- A note unrelated to continuation leaves valid evidence usable at both consumers.
example : admitted settled [.appendRevision scopeNote (authorizedRevision scopeNote none),
    .claimSatisfied] := by
  simp only [admitted, allowed, guardAppendRevision, guardClaimSatisfied,
    ProtocolState.goalWritten, RevisionEvidence.valid, authorizedRevision]
  decide

example : admitted settled [.appendRevision scopeNote (authorizedRevision scopeNote none),
    evaluateA1, .claimSatisfied] := by
  simp only [admitted, evaluateA1, allowed, guardAppendRevision, guardEvaluateTermination,
    guardClaimSatisfied, ProtocolState.goalWritten, RevisionEvidence.valid, authorizedRevision]
  decide

-- Actual review table retains ordinary defects (including deliberate test reproductions).
private def ordinary : Finding :=
  ⟨.reject, ⟨true, true, false, false⟩, false,
    ⟨"ordinary typo", "user", .ordinaryOperation, "parsing", none, true⟩⟩
private def approval : Finding := { ordinary with verdict := .approve }
example : routeFindings ordinary approval approval = .fix := by decide
private def invented := { ordinary with trigger := { ordinary.trigger with triggerPath := .nonstandardDeliberate } }
example : routeFindings invented approval approval = .doneWithAdvisory := by decide
example : routeFindings { ordinary with requiresTrustedMalice := true } approval approval = .doneWithAdvisory := by decide
example : routeFindings { invented with trigger := { invented.trigger with
    recordedOccurrence := some "independent pre-run incident", createdDuringRun := false } }
    approval approval = .fix := by decide
example : routeFindings { invented with trigger := { invented.trigger with
    recordedOccurrence := some "fixture made during this run" } } approval approval = .doneWithAdvisory := by decide
-- Absorbed harms have no second conjunct; a goal-visible residue still blocks.
example : routeFindings { ordinary with input := shapeToInput .absorbedByRecoveryPath }
    approval approval = .doneWithAdvisory := by decide
example : planElementAdmitted ⟨"defense", invented, false, true⟩ = false := by decide
example : planElementAdmitted ⟨"ordinary validation", ordinary, false, true⟩ = true := by decide
example : planElementAdmitted ⟨"integrity label", { ordinary with
    input := shapeToInput .absorbedByRecoveryPath }, true, true⟩ = false := by decide

example : familyPassAllowed { family with coverageBasis := .unsupportedMemberEnumeration }
    .repairWithRerunReview = false := by decide
example : familyPassAllowed { family with coverageBasis := .soundAbstraction true }
    .repairWithRerunReview = true := by decide
example : familyPassAllowed { family with changesDomainOrCriterion := true, ownerAuthorized := true }
    .repairWithRerunReview = false := by decide
example : familyPassAllowed { family with
    changesDomainOrCriterion := true, ownerAuthorized := true,
    revision := some ⟨"scope", "owner authorization", "none"⟩ } .repairWithRerunReview = true := by decide
example : familyRoute { family with noSupportedPath := true } = .honestStop := by decide

-- Autonomous intake proceeds from honest sources even when continuation is silent/ambiguous.
example : allowed ProtocolState.initial (.writeGoal goal (intake .silent)) := by
  simp [allowed, guardWriteGoal, IntakeEvidence.complete, Harness.complete, ProtocolState.initial, intake, goal]
example : allowed ProtocolState.initial (.writeGoal goal (intake .ambiguous)) := by
  simp [allowed, guardWriteGoal, IntakeEvidence.complete, Harness.complete, ProtocolState.initial, intake, goal]
example : ¬ allowed ProtocolState.initial (.writeGoal goal
    { intake .silent with startupQuestionAsked := true }) := by
  simp [allowed, guardWriteGoal, IntakeEvidence.complete]
example : ¬ allowed ProtocolState.initial (.writeGoal goal
    { intake .silent with explicitBoundariesPreserved := false }) := by
  simp [allowed, guardWriteGoal, IntakeEvidence.complete]
example : (step ProtocolState.initial (.writeGoal goal (intake .silent))).gate = .inapplicable := by decide
example : (step ProtocolState.initial (.writeGoal goal (intake .ambiguous))).gate = .withholdClaim := by decide
example : (step ProtocolState.initial (.writeGoal goal (intake .present))).gate = .applies := by decide

end Sshx.Behavior.Scenarios
