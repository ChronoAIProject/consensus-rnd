import Sshx.Behavior.Invariant
import Sshx.Reasoning.Authority
import Sshx.Reasoning.Review
import Sshx.Reasoning.Termination
import Sshx.Clauses.Delegation

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
  ⟨goal.harness, ["task", "repository rules"], true, true, true, false, false, continuation, .hostMandate, false, false⟩

private def readyState : ProtocolState :=
  { ProtocolState.initial with
    stage := .implementation, goal := some goal, mode := some (.carrier .codexCli),
    capabilityChecked := [.codexCli, .isolatedTokenSubagent], hecaCheckRecorded := true }

private def ordinary : Finding :=
  ⟨.reject, ⟨true, true, false, false, .confirmed, "goal S1 / existing contract source"⟩, false,
    ⟨"ordinary typo", "user", .ordinaryOperation, "parsing", none, true,
      ⟨none, "current parser input path", true⟩, .unrestricted,
      ⟨"correct parsing", "ordinary parser typo", "new cause at initial review"⟩⟩, false⟩

private def twoParts : ImplementationPlan := ⟨["contract", "verification"], 2, [ordinary]⟩
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
    ProtocolState.batchFlights, newFlight, ProtocolState.freshId, ProtocolState.flightProtected, FlightRec.active,
    ProtocolState.flight, ProtocolState.attemptSettled, ProtocolState.housekeepingFor,
    ProtocolState.allHecaHousekeepingSettled]

-- The next approved assignment remains in implementation, with no review flight in between.
example : allowed partDone (.openFlight .implementation .implementation .codexCli "target" 0) := by
  simp [allowed, guardOpenFlight, ProtocolState.carrierEnabled, guardImplementationFlight, ProtocolState.modeResolved,
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
    ProtocolState.flightProtected, FlightRec.active, Stage.next, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.flight, ProtocolState.attemptSettled, ProtocolState.housekeepingFor,
    ProtocolState.allHecaHousekeepingSettled]

-- Triplet seats can all dispatch independently; a second flight for the same seat cannot.
private def architecture := step review (.openFlight .review .architecture .codexCli "target" 0)
private def quality := step architecture (.openFlight .review .quality .codexCli "target" 0)
private def tests := step quality (.openFlight .review .tests .codexCli "target" 0)
example : architecture.reviewReady = true := by decide
example : allowed architecture (.openFlight .review .quality .codexCli "target" 0) := by
  simp only [allowed, guardOpenFlight, ProtocolState.carrierEnabled, guardReviewFlight, seatEligible, ProtocolState.modeResolved,
    ProtocolState.abstained]
  decide
example : allowed quality (.openFlight .review .tests .codexCli "target" 0) := by
  simp only [allowed, guardOpenFlight, ProtocolState.carrierEnabled, guardReviewFlight, seatEligible, canRunRepositoryCommands, ProtocolState.modeResolved,
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

-- Initial-review evidence is shared by the review route and first-family repair.

private def approval : Finding := { ordinary with verdict := .approve }

private def family : FamilyEvidence :=
  ⟨"goal", "correct output", "ordinary inputs", "shared mechanism", "worker", "behavior check",
    true, ⟨"current paraphrase", "same property/cause evidence", true,
      .priorComparison "prior label" true true true⟩,
    2, false, .uniformInvariant true, false, false, false, false, none, true, true⟩

-- The ordinary initial-review repair needs neither a predecessor nor class coverage.
private def firstFamily : FamilyEvidence :=
  { family with
    property := ordinary.trigger.familyBasis.failedProperty
    mechanismFamily := ordinary.trigger.mechanismFamily
    sameGoalTerm := false
    correspondence := ⟨ordinary.trigger.mechanismFamily,
      ordinary.trigger.familyBasis.causalMechanism, true,
      .newFamily ordinary.trigger.familyBasis.correspondenceEvidence⟩
    consecutivePasses := 1
    coverageBasis := .unknown }

-- The default fix state carries no cap, so a direction-gated pass remains admissible
-- and leaves the absent cap unchanged.
example : fix.passBudget = none := by decide
example : allowed fix (.pass .repeatedReviewPass family none) := by
  have hs : fix.stage = .fixOrDone := by decide
  have hr : fix.reviewComplete = true := by decide
  have hb : fix.passBudget = none := by decide
  simp [allowed, guardPass, hs, hr, hb, family, familyPassAllowed, familyRoute,
    ownerAuthorizedDomainChange, classGateActive, FamilyEvidence.recordComplete, FamilyCorrespondence.verified, FamilyCorrespondence.sameFamily,
    coverageBasisVerified, Transition.counted]
private def defaultRepeated := step fix (.pass .repeatedReviewPass family none)
example : defaultRepeated.passBudget = none := by decide

private def funded := step fix (.recordPassBudget 1)
private def repair := step funded (.pass .repairWithRerunReview firstFamily (some twoParts))
private theorem firstRepairPassAdmitted :
    allowed funded (.pass .repairWithRerunReview firstFamily (some twoParts)) := by
  have hs : funded.stage = .fixOrDone := by decide
  have hr : funded.reviewComplete = true := by decide
  have hb : funded.passBudget = some 1 := by decide
  simp [allowed, guardPass, hs, hr, hb, firstFamily, ordinary, family, familyPassAllowed, familyRoute,
    ownerAuthorizedDomainChange, classGateActive, FamilyEvidence.recordComplete, FamilyCorrespondence.verified, FamilyCorrespondence.sameFamily,
    coverageBasisVerified, twoParts, ImplementationPlan.valid, ImplementationPlan.repairsAdmitted, ordinary, downgrade, completeAdmission, eligible, triggerCheck, force,
    WorkflowInputEvidence.ordinary, WorkflowInputEvidence.observed, occurrenceIsIndependent,
    OwnerAdmissionPolicy.admits, Transition.counted]
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
    ownerAuthorizedDomainChange, classGateActive, FamilyEvidence.recordComplete, FamilyCorrespondence.verified, FamilyCorrespondence.sameFamily,
    coverageBasisVerified, Transition.counted]
example : repeated.reviewReady = true := by decide
example : repeated.reviewComplete = false := by decide
example : ¬ guardImplementationFlight repeated := by
  have hr : repeated.reviewReady = true := by decide
  simp [guardImplementationFlight, hr]
example : ¬ allowed funded (.pass .repairWithRerunReview
    { family with coverageBasis := .unsupportedMemberEnumeration } (some twoParts)) := by
  exact unsupported_class_cannot_start_batch funded
    { family with coverageBasis := .unsupportedMemberEnumeration } (some twoParts) rfl (by decide)

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
  (.beginImplementation ⟨["all work"], 1, []⟩))
  (.openFlight .implementation .implementation .codexCli "target" 0)) 0 successful)
  (.recordImplementation 0 ["all work"] true)
example : single.reviewReady = true := by decide

/-- A trace checks each production guard at the state produced by its preceding actions. -/
private def admitted (s : ProtocolState) : List Action → Prop
  | [] => True
  | a :: rest => allowed s a ∧ admitted (step s a) rest

private def run (s : ProtocolState) (actions : List Action) : ProtocolState :=
  actions.foldl step s

private theorem run_append (s : ProtocolState) (xs ys : List Action) :
    run s (xs ++ ys) = run (run s xs) ys := by
  simp only [run, List.foldl_append]

private theorem admitted_append (s : ProtocolState) (xs ys : List Action) :
    admitted s (xs ++ ys) ↔ admitted s xs ∧ admitted (run s xs) ys := by
  induction xs generalizing s with
  | nil => simp [admitted, run]
  | cons a xs ih =>
    change (allowed s a ∧ admitted (step s a) (xs ++ ys)) ↔
      (allowed s a ∧ admitted (step s a) xs) ∧ admitted (run (step s a) xs) ys
    simp only [ih, and_assoc]

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
  simp [admitted, implementationActions, allowed, guardOpenFlight, ProtocolState.carrierEnabled, guardImplementationFlight,
    guardLaunchViaRunner, guardHostNotified, guardCollect, guardRecordImplementation,
    begin, readyState, twoParts, ProtocolState.initial, ProtocolState.modeResolved,
    ProtocolState.abstained, ProtocolState.reviewStarted, ProtocolState.reviewReady,
    ProtocolState.batchSettled, ProtocolState.batchFlights, ProtocolState.flight,
    ProtocolState.freshId, step, ImplementationPlan.start, newFlight, updateFlight,
    collectEffect, done, successful, FlightRec.active]
example : run begin (implementationActions 0) = complete := rfl
private theorem initialReviewAdmitted : admitted review (reviewActions 2) := by
  simp [admitted, reviewActions, allowed, guardOpenFlight, ProtocolState.carrierEnabled, guardReviewFlight,
    guardLaunchViaRunner, guardHostNotified, guardCollect, seatEligible, canRunRepositoryCommands,
    review, complete, second, partDone, returned, opened, begin, readyState, finish, twoParts,
    ProtocolState.initial, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.reviewReady, ProtocolState.batchSettled, ProtocolState.batchFlights,
    ProtocolState.flight, ProtocolState.freshId, step, ImplementationPlan.start, newFlight,
    updateFlight, collectEffect, done, successful, ProtocolState.flightProtected, FlightRec.active, reviewRoles, Stage.next]
private theorem initialReviewRun : run review (reviewActions 2) = reviewed := rfl
private theorem initialReviewAdvanceAdmitted : allowed reviewed .advanceStage := by
  simp only [allowed, guardAdvanceStage, ProtocolState.goalWritten, ProtocolState.modeResolved,
    ProtocolState.abstained]
  decide
private theorem firstRepairBudgetAdmitted : allowed fix (.recordPassBudget 1) := by
  simp only [allowed, guardRecordPassBudget]
  decide

-- A paid repair uses identical whole-plan admission and still includes its review at zero.
set_option maxRecDepth 4096 in
private theorem firstRepairImplementationAdmitted : admitted repair (implementationActions 5) := by
  simp [admitted, implementationActions, allowed, guardOpenFlight, ProtocolState.carrierEnabled, guardImplementationFlight,
    guardLaunchViaRunner, guardHostNotified, guardCollect, guardRecordImplementation,
    repair, funded, fix, reviewed, tests, quality, architecture, review, complete, second,
    partDone, returned, opened, begin, readyState, finish, twoParts, ProtocolState.initial,
    ProtocolState.modeResolved, ProtocolState.abstained, ProtocolState.reviewStarted,
    ProtocolState.reviewReady, ProtocolState.batchSettled, ProtocolState.batchFlights,
    ProtocolState.flight, ProtocolState.freshId, step, ImplementationPlan.start, newFlight,
    updateFlight, collectEffect, done, successful, ProtocolState.flightProtected, FlightRec.active, Stage.next]
private theorem firstRepairImplementationRun : run repair (implementationActions 5) = repaired := rfl
set_option maxRecDepth 4096 in
private theorem firstRepairReviewAdmitted : admitted repaired (reviewActions 7) := by
  simp [admitted, reviewActions, allowed, guardOpenFlight, ProtocolState.carrierEnabled, guardReviewFlight,
    guardLaunchViaRunner, guardHostNotified, guardCollect, seatEligible, canRunRepositoryCommands,
    repaired, repairedFirst, repair, funded, fix, reviewed, tests, quality, architecture,
    review, complete, second, partDone, returned, opened, begin, readyState, finish, twoParts,
    ProtocolState.initial, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.reviewReady, ProtocolState.batchSettled, ProtocolState.batchFlights,
    ProtocolState.flight, ProtocolState.freshId, step, ImplementationPlan.start, newFlight,
    updateFlight, collectEffect, done, successful, ProtocolState.flightProtected, FlightRec.active, reviewRoles, Stage.next]
private theorem firstRepairReviewRun : run repaired (reviewActions 7) = repairReviewed := rfl

private def firstRepairActions : List Action :=
  reviewActions 2 ++ ([.advanceStage, .recordPassBudget 1,
    .pass .repairWithRerunReview firstFamily (some twoParts)] ++
    (implementationActions 5 ++ reviewActions 7))

-- Every production guard is checked from initial review through the first finite repair
-- and its included final triplet. The review route and family projection share evidence.
private theorem initial_review_new_family_repair :
    routeFindings ordinary approval approval = .fix ∧
    classGateActive firstFamily = false ∧ admitted review firstRepairActions := by
  refine ⟨by decide, by decide, ?_⟩
  rw [firstRepairActions, admitted_append]
  refine ⟨initialReviewAdmitted, ?_⟩
  rw [initialReviewRun]
  change allowed reviewed .advanceStage ∧ allowed fix (.recordPassBudget 1) ∧
    allowed funded (.pass .repairWithRerunReview firstFamily (some twoParts)) ∧
    admitted repair (implementationActions 5 ++ reviewActions 7)
  refine ⟨initialReviewAdvanceAdmitted, firstRepairBudgetAdmitted, firstRepairPassAdmitted, ?_⟩
  rw [admitted_append, firstRepairImplementationRun]
  exact ⟨firstRepairImplementationAdmitted, firstRepairReviewAdmitted⟩

private theorem firstRepairEntryRun :
    run reviewed [.advanceStage, .recordPassBudget 1,
      .pass .repairWithRerunReview firstFamily (some twoParts)] = repair := by
  simp only [run, List.foldl_cons, List.foldl_nil, repair, funded, fix]

private theorem firstRepairRun : run review firstRepairActions = repairReviewed := by
  rw [firstRepairActions, run_append, initialReviewRun, run_append, firstRepairEntryRun,
    run_append, firstRepairImplementationRun, firstRepairReviewRun]

set_option maxRecDepth 4096 in
example : (run review firstRepairActions).reviewComplete = true ∧
    (run review firstRepairActions).passBudget = some 0 := by
  rw [firstRepairRun]
  decide

private def recoveryActions (observation : Observation) : List Action :=
  [.beginImplementation ⟨["all work"], 1, []⟩,
   .openFlight .implementation .implementation .codexCli "target" 0,
   .launchViaRunner 0, .hostNotified 0, .collect 0 { successful with exitZero := false },
   .fallbackFlight 0 .isolatedTokenSubagent,
   .launchDelegated 1, .hostNotified 1, .collect 1 observation]

-- Both success and exhaustion traverse launch, notification and collection on each carrier.
private theorem recovery_admitted (observation : Observation) :
    admitted readyState (recoveryActions observation) := by
  simp [admitted, recoveryActions, allowed, guardBeginImplementation, ImplementationPlan.valid,
    guardOpenFlight, ProtocolState.carrierEnabled, guardImplementationFlight, guardLaunchViaRunner, guardLaunchDelegated, ProtocolState.carrierEnabled,
    guardHostNotified, guardCollect, guardFallback, readyState, ProtocolState.initial,
    ProtocolState.modeResolved, ProtocolState.abstained, ProtocolState.reviewStarted,
    ProtocolState.reviewReady, ProtocolState.batchSettled, ProtocolState.batchFlights,
    ProtocolState.flight, ProtocolState.freshId, ProtocolState.triedCarriers,
    ProtocolState.eligibleCarriers, ProtocolState.carrierEnabled, step, ImplementationPlan.start, newFlight, reopenFlight,
    updateFlight, collectEffect, done, successful, ProtocolState.flightProtected, FlightRec.active, nextCarrier,
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
  [.beginImplementation ⟨["all work"], 1, []⟩,
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
    capabilityChecked := [.heca], hecaCheckRecorded := true, flights := [archiveFlight] }
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
  simp [allowed, guardLaunchDelegated, ProtocolState.carrierEnabled, failedCollectedState, failedArchiveFlight,
    archiveNotifiedState, archiveFlight, ProtocolState.initial, newFlight,
    ProtocolState.attemptSettled, ProtocolState.pendingHousekeeping,
    ProtocolState.flight]
example : allowed (step failedCollectedState (.archiveHecaAttempt 0 true .archived))
    (.launchDelegated 0) := by
  simp [allowed, guardLaunchDelegated, ProtocolState.carrierEnabled, failedCollectedState, failedArchiveFlight,
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
    ProtocolState.abstained, ProtocolState.flight, ProtocolState.flightProtected, FlightRec.active, Stage.next]
theorem heca_first_implementation_admitted (budget : Option Nat) (retries : Nat) :
    admitted (hecaReady budget) (hecaInitialActions retries) := by
  simp [admitted, hecaInitialActions, hecaReady, allowed, guardBeginImplementation,
    ImplementationPlan.valid, guardOpenFlight, ProtocolState.carrierEnabled, guardImplementationFlight,
    guardLaunchDelegated, ProtocolState.carrierEnabled, readyState, ProtocolState.initial, ProtocolState.modeResolved,
    ProtocolState.abstained, ProtocolState.reviewStarted, ProtocolState.reviewReady,
    ProtocolState.batchSettled, ProtocolState.batchFlights, ProtocolState.flight,
    ProtocolState.freshId, step, ImplementationPlan.start, newFlight, ProtocolState.flightProtected, FlightRec.active,
    Carrier.univ]

theorem heca_stopped_no_notice_failure_admitted (budget : Option Nat) (retries : Nat) :
    ((hecaInFlight budget retries).flight 0).map (·.notified) = some false ∧
    allowed (hecaInFlight budget retries) (.recordStoppedHecaFailure 0 ownStop) := by
  simp [allowed, guardStoppedHecaFailure, hecaInFlight, run, hecaInitialActions,
    hecaReady, readyState, ProtocolState.initial, ProtocolState.flight,
    ProtocolState.freshId, step, ImplementationPlan.start, newFlight, updateFlight,
    ProtocolState.flightProtected, FlightRec.active, ownStop, HecaStopEvidence.confirmed]

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
    ImplementationPlan.start, newFlight, updateFlight, ProtocolState.flightProtected, FlightRec.active,
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
    collectEffect, stoppedHecaFailure, done, ProtocolState.flightProtected, FlightRec.active, allowed,
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
    guardBeginImplementation, ImplementationPlan.valid, guardOpenFlight, ProtocolState.carrierEnabled,
    guardImplementationFlight, guardLaunchViaRunner, guardLaunchDelegated, ProtocolState.carrierEnabled,
    guardStoppedHecaFailure, ownStop, HecaStopEvidence.confirmed, guardHostNotified,
    guardCollect, guardFallback, guardRecordImplementation, guardAdvanceStage,
    readyState, ProtocolState.initial, ProtocolState.modeResolved,
    ProtocolState.abstained, ProtocolState.reviewStarted, ProtocolState.reviewReady,
    ProtocolState.batchSettled, ProtocolState.batchFlights, ProtocolState.flight,
    ProtocolState.freshId, ProtocolState.triedCarriers, ProtocolState.eligibleCarriers, ProtocolState.carrierEnabled,
    step, ImplementationPlan.start, newFlight, reopenFlight, updateFlight,
    collectEffect, stoppedHecaFailure, done, successful, ProtocolState.flightProtected, FlightRec.active,
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
    ProtocolState.flight, ProtocolState.freshId, ProtocolState.flightProtected, FlightRec.active, Stage.next]

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
    guardBeginImplementation, ImplementationPlan.valid, guardOpenFlight, ProtocolState.carrierEnabled,
    guardImplementationFlight, guardLaunchDelegated, ProtocolState.carrierEnabled, guardHostNotified, guardCollect,
    guardArchiveHeca, guardStoppedHecaFailure, ownStop, HecaStopEvidence.confirmed,
    readyState, ProtocolState.initial, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.reviewStarted, ProtocolState.reviewReady, ProtocolState.batchSettled,
    ProtocolState.batchFlights, ProtocolState.flight, ProtocolState.freshId,
    step, ImplementationPlan.start, newFlight, updateFlight, recordPendingArchive,
    collectEffect, stoppedHecaFailure, unreadable, done, ProtocolState.flightProtected, FlightRec.active, Carrier.univ]

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
  cases outcome <;> simp [allowed, guardLaunchDelegated, ProtocolState.carrierEnabled, guardFallback, guardStoppedHecaFailure,
    guardMutateTarget, guardCollect, hecaUnreadableArchived, hecaUnreadableCollected,
    hecaNotified, hecaInFlight, run, hecaInitialActions, hecaReady,
    readyState, ProtocolState.initial, step, ProtocolState.flight, ProtocolState.freshId,
    ImplementationPlan.start, newFlight, updateFlight, unreadable, recordPendingArchive,
    ProtocolState.activeOn, ProtocolState.flightProtected, FlightRec.active, HecaStopEvidence.confirmed]

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
    ownStop, HecaStopEvidence.confirmed, ProtocolState.flightProtected, FlightRec.active, ProtocolState.eligibleCarriers, ProtocolState.carrierEnabled,
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
    guardBeginImplementation, ImplementationPlan.valid, guardOpenFlight, ProtocolState.carrierEnabled,
    guardImplementationFlight, guardLaunchDelegated, ProtocolState.carrierEnabled, guardHostNotified, guardCollect,
    guardArchiveHeca, guardStoppedHecaFailure, guardFallback,
    readyState, ProtocolState.initial, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.reviewStarted, ProtocolState.reviewReady, ProtocolState.batchSettled,
    ProtocolState.batchFlights, ProtocolState.flight, ProtocolState.freshId,
    ProtocolState.eligibleCarriers, ProtocolState.carrierEnabled, ProtocolState.triedCarriers,
    step, ImplementationPlan.start, newFlight, updateFlight, recordPendingArchive,
    collectEffect, stoppedHecaFailure, unreadable, done, ProtocolState.flightProtected, FlightRec.active,
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
    guardBeginImplementation, ImplementationPlan.valid, guardOpenFlight, ProtocolState.carrierEnabled,
    guardImplementationFlight, guardLaunchDelegated, ProtocolState.carrierEnabled, guardHostNotified, guardCollect,
    readyState, ProtocolState.initial, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.reviewStarted, ProtocolState.reviewReady, ProtocolState.batchSettled,
    ProtocolState.batchFlights, ProtocolState.flight, ProtocolState.freshId,
    step, ImplementationPlan.start, newFlight, updateFlight, ProtocolState.flightProtected, FlightRec.active, Carrier.univ]

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
    guardOpenFlight, ProtocolState.carrierEnabled, guardImplementationFlight, guardLaunchDelegated, ProtocolState.carrierEnabled, guardHostNotified,
    guardCollect, guardArchiveHeca, guardRecordImplementation, guardAdvanceStage,
    readyState, ProtocolState.initial, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.reviewStarted, ProtocolState.reviewReady, ProtocolState.batchSettled,
    ProtocolState.batchFlights, ProtocolState.flight, ProtocolState.freshId,
    step, ImplementationPlan.start, newFlight, updateFlight, recordPendingArchive,
    collectEffect, successful, done, ProtocolState.flightProtected, FlightRec.active, Carrier.univ, Stage.next]

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
    ProtocolState.eligibleCarriers, ProtocolState.carrierEnabled, ProtocolState.triedCarriers,
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
    simp [ProtocolState.eligibleCarriers, ProtocolState.carrierEnabled, ProtocolState.triedCarriers,
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
    ProtocolState.freshId, ProtocolState.triedCarriers, ProtocolState.eligibleCarriers, ProtocolState.carrierEnabled,
    newFlight, nextCarrier, Carrier.univ, CarrierSet.get]

-- Test-seat restriction applies to initial, repeated, and included repair review alike.
example : ¬ allowed { review with capabilityChecked := Carrier.univ }
    (.openFlight .review .tests .nyxidOracle "target" 0) :=
  tests_dispatch_excludes_oracle _ _ _
example : allowed review (.openFlight .review .tests .isolatedTokenSubagent "target" 0) := by
  simp only [allowed, guardOpenFlight, ProtocolState.carrierEnabled, guardReviewFlight, ProtocolState.modeResolved,
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
    [.beginImplementation ⟨["all work"], 1, []⟩,
     .openFlight .implementation .implementation .codexCli "target" 0,
     .launchViaRunner 0, .hostNotified 0, .collect 0 successful,
     .recordImplementation 0 ["all work"] true, .advanceStage] := by
  simp [admitted, allowed, guardBeginImplementation, ImplementationPlan.valid,
    guardOpenFlight, ProtocolState.carrierEnabled, guardImplementationFlight, guardLaunchViaRunner, guardHostNotified,
    guardCollect, guardRecordImplementation, guardAdvanceStage, readyState,
    ProtocolState.initial, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.reviewStarted, ProtocolState.reviewReady, ProtocolState.batchSettled,
    ProtocolState.batchFlights, ProtocolState.flight, ProtocolState.freshId,
    step, ImplementationPlan.start, newFlight, updateFlight, collectEffect, done,
    successful, ProtocolState.flightProtected, FlightRec.active, Stage.next]

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
    allowed, guardOpenFlight, ProtocolState.carrierEnabled, guardReviewFlight, seatEligible, canRunRepositoryCommands,
    guardLaunchViaRunner, guardLaunchDelegated, ProtocolState.carrierEnabled, guardHostNotified, guardCollect, guardFallback,
    ProtocolState.initial, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.reviewReady, ProtocolState.batchSettled, ProtocolState.batchFlights,
    ProtocolState.flight, ProtocolState.freshId, ProtocolState.triedCarriers,
    ProtocolState.eligibleCarriers, ProtocolState.carrierEnabled, step, ImplementationPlan.start, newFlight, reopenFlight,
    updateFlight, collectEffect, done, ProtocolState.flightProtected, FlightRec.active, nextCarrier, Carrier.univ,
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
  simp [allowed, guardLaunchDelegated, ProtocolState.carrierEnabled, oracleReview, testsReviewState, review, complete, second,
    partDone, returned, opened, begin, readyState, finish, twoParts, successful,
    ProtocolState.initial, ProtocolState.flight, ProtocolState.freshId, step,
    ImplementationPlan.start, newFlight, updateFlight, collectEffect, done]

private def scopeNote : Revision :=
  ⟨"engineering scope note", "existing user authorization", "none"⟩
private def authorizedRevision (r : Revision) (source : Option ContinuationSource) : RevisionEvidence :=
  ⟨r, true, true, source, false, false⟩

private def startupActions (c : ContinuationEntry) : List Action :=
  [.writeGoal goal (intake c),
   .appendRevision scopeNote (authorizedRevision scopeNote none),
   .advanceStage, .capabilityCheck .codexCli, .recordHecaCheck ⟨false, .disabled, false⟩, .resolveMode (.carrier .codexCli), .advanceStage]
-- Every opt-in/exposure combination passes through the production check and consumers.
-- Opt-in source is supplied evidence; these cases do not detect authority from English.
private def optInCheckState : ProtocolState :=
  { readyState with
    stage := .chooseWorkerMode, mode := none,
    hecaCheckRecorded := false, capabilityChecked := [.codexCli, .isolatedTokenSubagent] }

private def optInState (source : HecaOptIn) (exposed : Bool) : ProtocolState :=
  { step optInCheckState (.recordHecaCheck ⟨exposed, source, false⟩) with
    stage := .implementation, mode := some (.carrier .codexCli) }

example (facts : HecaCapability) : allowed optInCheckState (.recordHecaCheck facts) := by
  simp [allowed, guardHecaCheck, guardCapabilityCheck, optInCheckState, readyState,
    ProtocolState.initial, ProtocolState.goalWritten]


private def optInFailed (source : HecaOptIn) (exposed : Bool) : ProtocolState :=
  finish (step (step (optInState source exposed)
    (.beginImplementation ⟨["all work"], 1, []⟩))
    (.openFlight .implementation .implementation .codexCli "target" 0)) 0
    { successful with exitZero := false }

-- All six source/exposure cases include the four requested enablement/exposure cases.
-- Initial composition, single-worker admission and the actual fallback guard agree.
theorem heca_opt_in_consumer_matrix :
    ∀ source ∈ [HecaOptIn.disabled, .user, .host], ∀ exposed ∈ [false, true],
      let s := optInState source exposed
      let available := s.capabilityChecked.contains .heca
      available = (source != .disabled && exposed) ∧
      (Clauses.stageComposition 3 available).contains .heca = available ∧
      (Clauses.stageComposition 1 available).contains .heca = available ∧
      (allowed (step s (.beginImplementation ⟨["all work"], 1, []⟩))
        (.openFlight .implementation .implementation .heca "target" 0) ↔ available = true) ∧
      (allowed (optInFailed source exposed) (.fallbackFlight 0 .heca) ↔ available = true) := by
  intro source _ exposed _
  cases source <;> cases exposed <;>
    simp [optInState, optInCheckState, optInFailed, readyState, ProtocolState.initial,
      finish, allowed, guardOpenFlight, ProtocolState.carrierEnabled, guardImplementationFlight, guardFallback,
      guardBeginImplementation, ImplementationPlan.valid, admitted,
      guardLaunchViaRunner, guardHostNotified, guardCollect,
      ProtocolState.modeResolved, ProtocolState.abstained, step, ImplementationPlan.start,
      newFlight, ProtocolState.freshId, ProtocolState.flight, updateFlight, collectEffect,
      done, successful, ProtocolState.flightProtected, FlightRec.active, ProtocolState.eligibleCarriers, ProtocolState.carrierEnabled,
      ProtocolState.triedCarriers, nextCarrier, Carrier.univ, CarrierSet.get,
      HecaCapability.detected, Clauses.stageComposition,
      ProtocolState.reviewStarted, ProtocolState.reviewReady, ProtocolState.batchSettled,
      ProtocolState.batchFlights, ProtocolState.allHecaHousekeepingSettled,
      ProtocolState.attemptSettled, ProtocolState.housekeepingFor]

-- Failure really is reached by an admitted Codex dispatch/notification/collection trace.
theorem opt_in_codex_failure_admitted :
    ∀ source ∈ [HecaOptIn.disabled, .user, .host], ∀ exposed ∈ [false, true],
      admitted (optInState source exposed)
        [.beginImplementation ⟨["all work"], 1, []⟩,
         .openFlight .implementation .implementation .codexCli "target" 0,
         .launchViaRunner 0, .hostNotified 0, .collect 0 { successful with exitZero := false }] := by
  intro source _ exposed _
  cases source <;> cases exposed <;>
    simp [optInState, optInCheckState, optInFailed, readyState, ProtocolState.initial,
      finish, allowed, guardOpenFlight, ProtocolState.carrierEnabled, guardImplementationFlight, guardFallback,
      guardBeginImplementation, ImplementationPlan.valid, admitted,
      guardLaunchViaRunner, guardHostNotified, guardCollect,
      ProtocolState.modeResolved, ProtocolState.abstained, step, ImplementationPlan.start,
      newFlight, ProtocolState.freshId, ProtocolState.flight, updateFlight, collectEffect,
      done, successful, ProtocolState.flightProtected, FlightRec.active, ProtocolState.eligibleCarriers, ProtocolState.carrierEnabled,
      ProtocolState.triedCarriers, nextCarrier, Carrier.univ, CarrierSet.get,
      HecaCapability.detected, Clauses.stageComposition,
      ProtocolState.reviewStarted, ProtocolState.reviewReady, ProtocolState.batchSettled,
      ProtocolState.batchFlights, ProtocolState.allHecaHousekeepingSettled,
      ProtocolState.attemptSettled, ProtocolState.housekeepingFor]

-- With no enablement or no tools, fallback actually continues on the existing subagent route.
theorem opt_in_unavailable_fallback :
    ∀ source ∈ [HecaOptIn.disabled, .user, .host], ∀ exposed ∈ [false, true],
      (source = .disabled ∨ exposed = false) →
      allowed (optInFailed source exposed) (.fallbackFlight 0 .isolatedTokenSubagent) := by
  intro source _ exposed _
  cases source <;> cases exposed <;>
    simp [optInState, optInCheckState, optInFailed, readyState, ProtocolState.initial,
      finish, allowed, guardOpenFlight, ProtocolState.carrierEnabled, guardImplementationFlight, guardFallback,
      guardBeginImplementation, ImplementationPlan.valid, admitted,
      guardLaunchViaRunner, guardHostNotified, guardCollect,
      ProtocolState.modeResolved, ProtocolState.abstained, step, ImplementationPlan.start,
      newFlight, ProtocolState.freshId, ProtocolState.flight, updateFlight, collectEffect,
      done, successful, ProtocolState.flightProtected, FlightRec.active, ProtocolState.eligibleCarriers, ProtocolState.carrierEnabled,
      ProtocolState.triedCarriers, nextCarrier, Carrier.univ, CarrierSet.get,
      HecaCapability.detected, Clauses.stageComposition,
      ProtocolState.reviewStarted, ProtocolState.reviewReady, ProtocolState.batchSettled,
      ProtocolState.batchFlights, ProtocolState.allHecaHousekeepingSettled,
      ProtocolState.attemptSettled, ProtocolState.housekeepingFor]

-- Opted-in lifecycle fixtures have the same available carrier membership as the check.
theorem opt_in_preserves_heca_lifecycle_fixture :
    ∀ source ∈ [HecaOptIn.user, .host], ∀ c ∈ Carrier.univ,
      c ∈ (step { readyState with capabilityChecked := [.codexCli, .nyxidOracle, .isolatedTokenSubagent] }
        (.recordHecaCheck ⟨true, source, false⟩)).capabilityChecked ↔
      c ∈ (hecaReady none).capabilityChecked := by
  decide

-- Explicit disable overrides even sourced opt-in and exposed tools.
example (source : HecaOptIn) (exposed : Bool) :
    HecaCapability.detected ⟨exposed, source, true⟩ = false := by
  simp [HecaCapability.detected]

-- A source-supported correction reaches every dispatch consumer through existing enablement.
private def disableEvidence : RevisionEvidence :=
  { authorizedRevision scopeNote none with hecaDisableCorrection := true }
private def disabledAfterOptIn := step (optInState .host true) (.appendRevision scopeNote disableEvidence)
example : allowed (optInState .host true) (.appendRevision scopeNote disableEvidence) := by
  simp [allowed, guardAppendRevision, RevisionEvidence.valid, disableEvidence, authorizedRevision,
    ProtocolState.goalWritten, optInState, optInCheckState, readyState, ProtocolState.initial, step]
example : disabledAfterOptIn.carrierEnabled .heca = false := by decide
example : (Clauses.stageComposition 6 (disabledAfterOptIn.carrierEnabled .heca)).contains .heca = false := by decide
example : ¬ allowed (step disabledAfterOptIn (.beginImplementation ⟨["work"], 1, []⟩))
    (.openFlight .implementation .implementation .heca "target" 0) := by
  simp [allowed, guardOpenFlight, disabledAfterOptIn, ProtocolState.carrierEnabled, step,
    disableEvidence]
example : ¬ allowed (step (optInFailed .host true) (.appendRevision scopeNote disableEvidence))
    (.fallbackFlight 0 .heca) := by
  exact disabled_heca_cannot_fallback _ _ (by simp [step, disableEvidence])
-- Retry/resume cannot launch an existing Heca flight using stale capability records.
example (s : ProtocolState) (id : Nat) (f : FlightRec)
    (hf : s.flight id = some f) (hc : f.carrier = .heca) (hd : s.hecaDisabled = true) :
    ¬ allowed s (.launchDelegated id) := by
  simp [allowed, guardLaunchDelegated, hf, hc, ProtocolState.carrierEnabled, hd]

-- Heca records even an absent check once; it cannot redraw availability later.
example (s : ProtocolState) (first second : HecaCapability) :
    ¬ allowed (step s (.recordHecaCheck first)) (.recordHecaCheck second) := by
  simp [allowed, guardHecaCheck, step]

example (s : ProtocolState) :
    (step s (.recordHecaCheck ⟨false, .disabled, false⟩)).capabilityChecked = s.capabilityChecked := rfl

example (s : ProtocolState) :
    Carrier.heca ∈ (step s (.recordHecaCheck ⟨true, .user, false⟩)).capabilityChecked := by
  simp [step, HecaCapability.detected]

example : allowed { review with capabilityChecked := Carrier.univ }
    (.openFlight .review .tests .heca "target" 0) := by
  simp only [allowed, guardOpenFlight, ProtocolState.carrierEnabled, guardReviewFlight, ProtocolState.modeResolved,
    ProtocolState.abstained]
  decide

-- All authority states permit routine startup without a question, preserving revision support.
example (c : ContinuationEntry) : admitted ProtocolState.initial (startupActions c) := by
  simp [admitted, startupActions, allowed, guardWriteGoal, guardAppendRevision,
    guardAdvanceStage, guardCapabilityCheck, guardHecaCheck, guardResolveMode, HecaCapability.detected, Harness.complete,
    authorizedRevision, RevisionEvidence.valid,
    IntakeEvidence.complete, ActivationAuthority.authorized, goal, intake, ProtocolState.initial, ProtocolState.goalWritten,
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
    ImplementationPlan.valid, guardOpenFlight, ProtocolState.carrierEnabled, guardImplementationFlight, guardReviewFlight,
    guardLaunchViaRunner, guardLaunchDelegated, ProtocolState.carrierEnabled, guardHostNotified, guardCollect,
    guardArchiveHeca, guardRecordImplementation, guardAdvanceStage, guardAppendRevision,
    guardRecordPassBudget, correctionAction, correctionEvidence, authorizedRevision,
    continuationRevision, RevisionEvidence.valid, ProtocolState.goalWritten,
    readyState, ProtocolState.initial, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.reviewStarted, ProtocolState.reviewReady, ProtocolState.reviewComplete,
    ProtocolState.batchSettled, ProtocolState.batchFlights, ProtocolState.flight,
    ProtocolState.freshId, reviewRoles, seatEligible, canRunRepositoryCommands,
    step, ImplementationPlan.start, newFlight, updateFlight, recordPendingArchive,
    collectEffect, successful, done, ProtocolState.flightProtected, FlightRec.active, Carrier.univ, Stage.next,
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

-- Workflow provenance is consumed by the actual review and plan-admission routes.
private def manualOnly := { ordinary with trigger := { ordinary.trigger with
  inputEvidence := ⟨none, "tool manual describes an optional operation", false⟩ } }
example : routeFindings manualOnly approval approval = .doneWithAdvisory := by decide
example : planElementAdmitted ⟨"manual-only defense", manualOnly, false, true⟩ = false := by decide
private def fixtureOnly := { manualOnly with trigger := { manualOnly.trigger with
  inputEvidence := ⟨some ⟨"fixture command", "outside target fixture", 1, false⟩, "", false⟩ } }
example : routeFindings fixtureOnly approval approval = .doneWithAdvisory := by decide
-- A census of zero is not a new restriction on future ordinary workflow states.
private def futureOrdinary := { ordinary with trigger := { ordinary.trigger with
  inputEvidence := ⟨some ⟨"read-only census", "target", 0, true⟩, "workflow update path", true⟩ } }
example : routeFindings futureOrdinary approval approval = .fix := by decide
private def ownerRestricted := { futureOrdinary with trigger := { futureOrdinary.trigger with
  ownerPolicy := .actualInputOrOccurrence ⟨"owner rule preserved in goal/harness", by decide⟩ } }
example : routeFindings ownerRestricted approval approval = .doneWithAdvisory := by decide
example : routeFindings { ownerRestricted with trigger := { ownerRestricted.trigger with
    inputEvidence := ⟨some ⟨"read-only census", "target workflow inputs", 1, true⟩, "", false⟩ } }
    approval approval = .fix := by decide
example : routeFindings { ownerRestricted with trigger := { ownerRestricted.trigger with
    recordedOccurrence := some "independent incident", createdDuringRun := false } }
    approval approval = .fix := by decide
-- A missing source cannot be interpreted as an owner restriction and downgrade a defect.
example : ¬ ∃ source : { s : String // s ≠ "" }, source.val = "" := by
  rintro ⟨source, h⟩
  exact source.property h

-- Every generic blocking route carries the same trigger-aware admission basis.
private def ordinaryBasis : DecisionBasis := ⟨ordinary.input, ordinary.trigger⟩
private def manualBasis : DecisionBasis := ⟨manualOnly.input, manualOnly.trigger⟩
example : completeAdmission ordinaryBasis = true := by decide
example : completeAdmission manualBasis = false := by decide
example : downgradeRecord manualBasis "manual-only trigger" =
    some (.namedInOwnWords "manual-only trigger" false) := by decide
example : goalGapOf [manualBasis] = [] := by decide
example : goalGapOf [ordinaryBasis] = [ordinaryBasis] := by decide

private def ordinaryObjection : Objection :=
  ⟨.parsimony, true, true, false, ordinary.input, ordinary.trigger, "ordinary objection"⟩
private def manualObjection : Objection :=
  { ordinaryObjection with trigger := manualOnly.trigger }
example : focusedRoundRequired ordinaryObjection = true := by decide
example : focusedRoundRequired manualObjection = false := by decide

private def ordinaryDifference : RemainingDifference :=
  ⟨"ordinary residual", "engineering", ordinary.input, ordinary.trigger, false⟩
private def manualDifference : RemainingDifference :=
  ⟨"manual residual", "engineering", manualOnly.input, manualOnly.trigger, false⟩
example : ordinaryDifference.counts = true := by decide
example : manualDifference.counts = false := by decide

/-- Exercise existing consumers, not a second admission implementation. -/
private def blockingConsumerDecisions (f : Finding) : List Bool :=
  let b : DecisionBasis := ⟨f.input, f.trigger⟩
  let objection : Objection :=
    ⟨.parsimony, true, true, false, f.input, f.trigger, "basis in its own words"⟩
  let difference : RemainingDifference :=
    ⟨"remaining goal gap", "engineering", f.input, f.trigger, false⟩
  ([.planObjection, .reviewFinding, .terminationDifference] : List DecisionInputKind).map
      (fun k => admissibility k b) ++
    ([.revise, .reject, .abstain, .blockingFinding, .unsatisfied, .concretePlanElement] :
      List SoleBasisDecision).map (fun d => mayBeSoleBasis b d) ++
    [routeFindings { f with verdict := .reject } approval approval == .fix,
     planElementAdmitted ⟨"goal-required element", f, false, true⟩,
     asksMayBlock b, focusedRoundRequired objection,
     !(goalGapOf [b]).isEmpty, !(convergencePassScope [b]).isEmpty,
     (downgradeRecord b "basis in its own words").isNone,
     (declineRecord objection).isNone,
     @decide (BlockingFinding.wellFormed ⟨f.input, f.trigger, "S3", .omission⟩)
       (by unfold BlockingFinding.wellFormed; infer_instance),
     difference.counts, seatAppliesAuthority .unsatisfied b true == .unsatisfied]

-- A parametric statement covers every failed trigger, independent of label, input
-- conjuncts, owner policy, provenance and incident spelling supplied to the model.
theorem failed_trigger_rejected_by_every_consumer (f : Finding)
    (h : triggerCheck f.trigger = false) :
    blockingConsumerDecisions f = List.replicate 20 false := by
  cases hg : f.input.namesGoalTerm <;> cases he : f.input.namesWorkEvidence <;>
    simp [blockingConsumerDecisions, admissibility, mayBeSoleBasis, asksMayBlock,
    routeFindings, reviewRoute, downgrade, completeAdmission, h, planElementAdmitted,
    focusedRoundRequired, threeConditions, goalGapOf, convergencePassScope,
    downgradeRecord, declineRecord, BlockingFinding.wellFormed, RemainingDifference.counts,
    seatAppliesAuthority, settleSeat, approval, hg, he]

theorem admitted_basis_retained_by_every_consumer (f : Finding)
    (ha : completeAdmission ⟨f.input, f.trigger⟩ = true)
    (hm : f.requiresTrustedMalice = false) (ho : f.requiresOwnerDecision = false) :
    blockingConsumerDecisions f = List.replicate 20 true := by
  have hf := completeAdmission_requires_force ⟨f.input, f.trigger⟩ ha
  have ht : triggerCheck f.trigger = true := by
    have hparts := ha
    simp [completeAdmission] at hparts
    exact hparts.2
  simp [blockingConsumerDecisions, admissibility, mayBeSoleBasis, asksMayBlock,
    routeFindings, reviewRoute, downgrade, completeAdmission, hf, ht, hm, ho,
    planElementAdmitted, focusedRoundRequired, threeConditions, goalGapOf,
    convergencePassScope, downgradeRecord, declineRecord, BlockingFinding.wellFormed,
    RemainingDifference.counts, seatAppliesAuthority, settleSeat, approval, threatEligible]

private def observedRestricted : Finding :=
  { ownerRestricted with trigger := { ownerRestricted.trigger with
    inputEvidence := ⟨some ⟨"read-only census", "target workflow inputs", 1, true⟩, "", false⟩ } }
private def independentRestricted : Finding :=
  { ownerRestricted with trigger := { ownerRestricted.trigger with
    recordedOccurrence := some "independent incident", createdDuringRun := false } }

-- Nine constructed evidence classes, each checked at all twenty decisions. These
-- fixtures establish model behavior, never observed host occurrence or provenance.
example : ([manualOnly, fixtureOnly, invented, ownerRestricted,
    { invented with trigger := { invented.trigger with
      recordedOccurrence := some "fixture made during this run" } }] : List Finding).all
    (fun f => blockingConsumerDecisions f == List.replicate 20 false) = true := by decide
example : ([ordinary, futureOrdinary, observedRestricted, independentRestricted] : List Finding).all
    (fun f => blockingConsumerDecisions f == List.replicate 20 true) = true := by decide

-- Paraphrase reaches the production paid-pass guard, not a separate classifier.
private theorem fundedRepairAdmission (e : FamilyEvidence) :
    allowed funded (.pass .repairWithRerunReview e (some twoParts)) ↔
      familyPassAllowed e .repairWithRerunReview = true := by
  have hs : funded.stage = .fixOrDone := by decide
  have hr : funded.reviewComplete = true := by decide
  have hb : funded.passBudget = some 1 := by decide
  simp [allowed, guardPass, hs, hr, hb, twoParts, ImplementationPlan.valid, ImplementationPlan.repairsAdmitted, ordinary, downgrade, completeAdmission, eligible, triggerCheck, force,
    WorkflowInputEvidence.ordinary, WorkflowInputEvidence.observed, occurrenceIsIndependent,
    OwnerAdmissionPolicy.admits, Transition.counted]

-- Inactive recurrence alone cannot make an unresolved comparison a new family.
private def unresolvedComparison := { firstFamily with correspondence :=
  { firstFamily.correspondence with history := .priorComparison "candidate prior label" true true false } }
example : classGateActive unresolvedComparison = false ∧
    familyRoute unresolvedComparison = .reviseOrInvestigate := by decide
example : ¬ allowed funded (.pass .repairWithRerunReview unresolvedComparison (some twoParts)) := by
  rw [fundedRepairAdmission]
  decide
example : allowed funded (.pass .metaLayerConvergence unresolvedComparison none) := by
  have hs : funded.stage = .fixOrDone := by decide
  have hr : funded.reviewComplete = true := by decide
  have hb : funded.passBudget = some 1 := by decide
  simp [allowed, guardPass, hs, hr, hb, familyPassAllowed, familyRoute,
    unresolvedComparison, firstFamily, ordinary, family, FamilyCorrespondence.verified,
    ownerAuthorizedDomainChange, Transition.counted]

-- Evidence for a genuinely new family is still required at the same repair consumer.
example : ¬ allowed funded (.pass .repairWithRerunReview
    { firstFamily with correspondence := { firstFamily.correspondence with history := .newFamily "" } }
    (some twoParts)) := by
  rw [fundedRepairAdmission]
  decide
example : ¬ allowed funded (.pass .repairWithRerunReview
    { firstFamily with correspondence := { firstFamily.correspondence with evidence := "" } }
    (some twoParts)) := by
  rw [fundedRepairAdmission]
  decide
example : ¬ allowed funded (.pass .repairWithRerunReview
    { firstFamily with correspondence := { firstFamily.correspondence with peerFree := false } }
    (some twoParts)) := by
  rw [fundedRepairAdmission]
  decide

-- Earlier unsupported enumeration activates the existing gate even for a new family.
private def firstFamilyUnsupported := { firstFamily with earlierUnsupportedEnumeration := true }
example : classGateActive firstFamilyUnsupported = true := by decide
example : ¬ allowed funded (.pass .repairWithRerunReview firstFamilyUnsupported (some twoParts)) := by
  rw [fundedRepairAdmission]
  decide
example : allowed funded (.pass .repairWithRerunReview
    { firstFamilyUnsupported with coverageBasis := .soundAbstraction true } (some twoParts)) := by
  rw [fundedRepairAdmission]
  decide
example : ¬ allowed funded (.pass .repairWithRerunReview
    { firstFamily with changesDomainOrCriterion := true } (some twoParts)) := by
  rw [fundedRepairAdmission]
  decide

private def paraphrasedFamily := { family with coverageBasis := .unsupportedMemberEnumeration }
example : paraphrasedFamily.correspondence.history = .priorComparison "prior label" true true true ∧
    paraphrasedFamily.correspondence.currentLabel = "current paraphrase" := by decide
example : classGateActive paraphrasedFamily = true := by decide
example : allowed funded (.pass .repairWithRerunReview family (some twoParts)) := by
  rw [fundedRepairAdmission]
  decide
example : ¬ allowed funded (.pass .repairWithRerunReview paraphrasedFamily (some twoParts)) := by
  exact unsupported_class_cannot_start_batch funded paraphrasedFamily (some twoParts) rfl (by decide)
-- Supplied incident labels are evidence inputs, not independently replayed host events.
private def incidentLabels : List String :=
  ["lake_manifest_exemption_discards_dependency_local_state",
   "nested-git-boundary bypass leaves dependency-owned submodule/worktree metadata outside local-history coverage",
   "lake_dependency_recovery_roots_omit_reflogs", "unversioned_git_metadata",
   "lake_dependency_metadata_content_outside_git_object_graph"]
example : ∀ label ∈ incidentLabels,
    familyPassAllowed { paraphrasedFamily with correspondence :=
      { paraphrasedFamily.correspondence with currentLabel := label } }
      .repairWithRerunReview = false := by decide

-- Same location is immaterial; a different cause remains distinct.
private def distinctCause := { paraphrasedFamily with correspondence :=
  { paraphrasedFamily.correspondence with history := .priorComparison "prior label" true false true } }
example : classGateActive distinctCause = false := by decide
example : allowed funded (.pass .repairWithRerunReview distinctCause (some twoParts)) := by
  have hs : funded.stage = .fixOrDone := by decide
  have hr : funded.reviewComplete = true := by decide
  have hb : funded.passBudget = some 1 := by decide
  simp [allowed, guardPass, hs, hr, hb, distinctCause, paraphrasedFamily, family,
    familyPassAllowed, familyRoute, ownerAuthorizedDomainChange, classGateActive,
    FamilyCorrespondence.verified, FamilyCorrespondence.sameFamily, twoParts,
    ImplementationPlan.valid, ImplementationPlan.repairsAdmitted, ordinary, downgrade, completeAdmission, eligible, triggerCheck, force,
    WorkflowInputEvidence.ordinary, WorkflowInputEvidence.observed, occurrenceIsIndependent,
    OwnerAdmissionPolicy.admits, Transition.counted]
example : familyPassAllowed { family with correspondence :=
    { family.correspondence with history := .priorComparison "prior label" true true false } }
    .repairWithRerunReview = false := by decide
example : familyPassAllowed { family with correspondence :=
    { family.correspondence with peerFree := false } } .repairWithRerunReview = false := by decide

example : familyPassAllowed { family with inputCorrespondenceVerified := false }
    .repairWithRerunReview = false := by decide
example : familyPassAllowed { family with measurementLimitsReported := false }
    .repairWithRerunReview = false := by decide
example : ¬ allowed funded (.pass .repairWithRerunReview
    { family with inputCorrespondenceVerified := false } (some twoParts)) := by
  rw [fundedRepairAdmission]
  decide
example : ¬ allowed funded (.pass .repairWithRerunReview
    { family with measurementLimitsReported := false } (some twoParts)) := by
  rw [fundedRepairAdmission]
  decide

-- Autonomous intake proceeds from honest sources even when continuation is silent/ambiguous.
example : allowed ProtocolState.initial (.writeGoal goal (intake .silent)) := by
  simp [allowed, guardWriteGoal, IntakeEvidence.complete, ActivationAuthority.authorized, Harness.complete, ProtocolState.initial, intake, goal]
example : allowed ProtocolState.initial (.writeGoal goal (intake .ambiguous)) := by
  simp [allowed, guardWriteGoal, IntakeEvidence.complete, ActivationAuthority.authorized, Harness.complete, ProtocolState.initial, intake, goal]
example : ¬ allowed ProtocolState.initial (.writeGoal goal
    { intake .silent with startupQuestionAsked := true }) := by
  simp [allowed, guardWriteGoal, IntakeEvidence.complete, intake]
example : ¬ allowed ProtocolState.initial (.writeGoal goal
    { intake .silent with explicitBoundariesPreserved := false }) := by
  simp [allowed, guardWriteGoal, IntakeEvidence.complete, intake]
example : (step ProtocolState.initial (.writeGoal goal (intake .silent))).gate = .inapplicable := by decide
example : (step ProtocolState.initial (.writeGoal goal (intake .ambiguous))).gate = .withholdClaim := by decide
example : (step ProtocolState.initial (.writeGoal goal (intake .present))).gate = .applies := by decide

-- #1075: authority is consumed by actual intake; risk/importance alone supplies none.
example : ¬ allowed ProtocolState.initial (.writeGoal goal
    { intake .silent with activation := .absent }) := by
  simp [allowed, guardWriteGoal, IntakeEvidence.complete, ActivationAuthority.authorized]
example : allowed ProtocolState.initial (.writeGoal goal
    { intake .silent with activation := .explicitRequest }) := by
  simp [allowed, guardWriteGoal, IntakeEvidence.complete, ActivationAuthority.authorized,
    Harness.complete, ProtocolState.initial, intake, goal]
-- A material owner question is permitted; dependent intake waits for its answer.
example : ¬ allowed ProtocolState.initial (.writeGoal goal
    { intake .silent with startupQuestionAsked := true, materialOwnerQuestion := true }) := by
  simp [allowed, guardWriteGoal, IntakeEvidence.complete, intake]
example : allowed ProtocolState.initial (.writeGoal goal
    { intake .silent with
      startupQuestionAsked := true
      materialOwnerQuestion := true
      ownerClarified := true }) := by
  simp [allowed, guardWriteGoal, IntakeEvidence.complete, ActivationAuthority.authorized,
    Harness.complete, ProtocolState.initial, intake, goal]

private def uncertain (status : EvidenceStatus) : Finding :=
  { ordinary with input := { ordinary.input with evidenceStatus := status } }
example : ([.advisory, .conditional, .unverified] : List EvidenceStatus).all (fun status =>
    routeFindings (uncertain status) approval approval == .doneWithAdvisory &&
    !planElementAdmitted ⟨"new approval layer", uncertain status, true, true⟩) = true := by decide
example (status : EvidenceStatus) (h : status ≠ .confirmed) :
    ¬ allowed funded (.pass .repairWithRerunReview firstFamily
      (some { twoParts with repairFindings := [uncertain status] })) := by
  apply unadmitted_repairs_cannot_pass
  simp [ImplementationPlan.repairsAdmitted, uncertain, downgrade, completeAdmission, force, h, ordinary]
example : routeFindings { ordinary with input := { ordinary.input with userGoalBasis := "" } }
    approval approval = .doneWithAdvisory := by decide
example : ¬ allowed funded (.pass .repairWithRerunReview firstFamily
    (some { twoParts with repairFindings := [{ ordinary with requiresOwnerDecision := true }] })) := by
  apply unadmitted_repairs_cannot_pass
  decide
-- Confirmed ordinary defects, confirmed missing required evidence and confirmed disputed
-- interpretations retain the real review/repair path. A fixture alone still does not.
example : routeFindings { ordinary with input := { ordinary.input with basisDisputed := true } }
    approval approval = .fix := by decide
example : allowed funded (.pass .repairWithRerunReview firstFamily (some twoParts)) :=
  (fundedRepairAdmission firstFamily).mpr (by decide)

private def scopeCorrection : Revision :=
  ⟨"compose existing grants only; cancel dependent approval-system work", "current user", "dependent plan/reviews/repairs"⟩
private def scopeCorrectionEvidence : RevisionEvidence :=
  { authorizedRevision scopeCorrection none with invalidatesWork := true }
private def scopeCorrected (s : ProtocolState) :=
  step s (.appendRevision scopeCorrection scopeCorrectionEvidence)
example : allowed funded (.appendRevision scopeCorrection scopeCorrectionEvidence) := by
  refine ⟨?_, rfl, rfl, rfl⟩
  change funded.goal.isSome = true
  decide
example (s : ProtocolState) : (scopeCorrected s).flights = s.flights ∧
    (scopeCorrected s).passBudget = s.passBudget ∧ (scopeCorrected s).batch = none := ⟨rfl, rfl, rfl⟩
example : (scopeCorrected funded).reviewComplete = false := by decide
example : ¬ allowed (scopeCorrected funded) .claimSatisfied := by
  simp [allowed, guardClaimSatisfied, scopeCorrected, step, scopeCorrectionEvidence]
example : ¬ allowed (scopeCorrected funded) (.pass .repairWithRerunReview firstFamily (some twoParts)) := by
  simp [allowed, guardPass, scopeCorrected, step, scopeCorrectionEvidence]
example : ¬ allowed (scopeCorrected complete) (.openFlight .review .architecture .codexCli "target" 0) := by
  simp [allowed, guardOpenFlight, guardReviewFlight, scopeCorrected, step, scopeCorrectionEvidence]
-- A pending old assignment cannot launch; an already launched job remains protected.
example : ¬ allowed (scopeCorrected opened) (.launchViaRunner 0) := by
  simp [allowed, guardLaunchViaRunner, scopeCorrected, step, scopeCorrectionEvidence,
    opened, begin, readyState, ProtocolState.initial, ProtocolState.freshId,
    ProtocolState.flight, newFlight]
example : (scopeCorrected opened).activeOn "target" = false := by decide
private def activeOld := step opened (.launchViaRunner 0)
example : (scopeCorrected activeOld).activeOn "target" = true := by decide
example : ¬ allowed (scopeCorrected activeOld) (.mutateTarget "target") := by
  simp only [allowed, guardMutateTarget]
  decide
example : (scopeCorrected activeOld).stage = .thinkingPanel := rfl
example : ¬ allowed (scopeCorrected activeOld) .advanceStage := by
  have protectionObserved : ((scopeCorrected activeOld).flights.all fun f =>
    !(scopeCorrected activeOld).flightProtected f) = false := by decide
  simp [allowed, guardAdvanceStage, protectionObserved]
example : (scopeCorrected (finish opened 0 successful)).activeOn "target" = false := by decide
-- An unrelated sourced revision preserves valid candidate evidence and readiness.
example : (step complete (.appendRevision scopeNote (authorizedRevision scopeNote none))).reviewReady = true := by decide

-- Goal corrections invalidate claim evidence even without changing the host declaration.
example : (scopeCorrected settled).gate = settled.gate := by decide
example : (scopeCorrected settled).terminationExit = settled.terminationExit ∧
    (scopeCorrected settled).terminationAuthority = settled.terminationAuthority := by
  simpa only [scopeCorrected] using
    correction_keeps_settlement settled scopeCorrection scopeCorrectionEvidence
example : (scopeCorrected settled).continuationAuthority ≠ settled.continuationAuthority := by decide
example (source : ClaimSource) (e : TerminationEvidence)
    (old : e.authority = settled.continuationAuthority) :
    ¬ allowed (scopeCorrected settled) (.evaluateTermination source e) := by
  apply stale_evidence_cannot_evaluate
  rw [old]
  decide
example : ¬ allowed (scopeCorrected settled) (.recordImplementation 0 ["contract"] true) := by
  simp [allowed, guardRecordImplementation, scopeCorrected, step, scopeCorrectionEvidence]

end Sshx.Behavior.Scenarios
