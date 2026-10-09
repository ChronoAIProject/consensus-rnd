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
    capabilityChecked := [.codexCli, .isolatedTokenSubagent] }

private def twoParts : ImplementationPlan := ⟨["contract", "verification"]⟩
private def begin := step readyState (.beginImplementation twoParts)
private def opened := step begin (.openFlight .implementation .implementation .codexCli "target" 0)
private def successful : Observation := ⟨true, true, true, true, true⟩
private def finish (s : ProtocolState) (id : Nat) (o : Observation) : ProtocolState :=
  step (step (step s (.launchViaRunner id)) (.hostNotified id)) (.collect id o)
private def returned := finish opened 0 successful
private def partDone := step returned (.recordImplementation 0 ["contract"] true true)
private def second := step partDone (.openFlight .implementation .implementation .codexCli "target" 0)
private def complete := step (finish second 1 successful)
  (.recordImplementation 1 ["verification"] true true)

-- The first worker is terminal and its tests passed; remaining scope still blocks review.
example : (partDone.flight 0).map (·.status) = some .terminal := by decide
example : partDone.reviewReady = false := by decide
example : ¬ allowed partDone .advanceStage := by
  simp [allowed, guardAdvanceStage, partDone, returned, opened, begin, readyState,
    finish, step, updateFlight, collectEffect, done, successful, twoParts, ImplementationPlan.start,
    ProtocolState.initial, ProtocolState.reviewReady, ProtocolState.batchSettled,
    ProtocolState.batchFlights, newFlight, ProtocolState.freshId, FlightRec.active]

-- The next approved assignment remains in implementation, with no review flight in between.
example : allowed partDone (.openFlight .implementation .implementation .codexCli "target" 0) := by
  simp [allowed, guardOpenFlight, guardImplementationFlight, ProtocolState.modeResolved,
    ProtocolState.abstained, partDone, returned, opened, begin, readyState, finish, step, updateFlight,
    collectEffect, done, successful, twoParts, ImplementationPlan.start, ProtocolState.initial,
    ProtocolState.reviewStarted, ProtocolState.reviewReady, ProtocolState.batchSettled, ProtocolState.batchFlights,
    newFlight, ProtocolState.freshId]
example : complete.reviewReady = true := by decide
example : complete.reviewStarted = false := by decide
private def review := step complete .advanceStage
example : review.stage = .reviewTriplet := by decide
example : allowed complete .advanceStage := by
  simp [allowed, guardAdvanceStage, complete, second, partDone, returned, opened, begin,
    readyState, finish, step, updateFlight, collectEffect, done, successful, twoParts,
    ImplementationPlan.start, ProtocolState.initial, ProtocolState.reviewReady,
    ProtocolState.batchSettled, ProtocolState.batchFlights, newFlight, ProtocolState.freshId,
    FlightRec.active, Stage.next, ProtocolState.modeResolved, ProtocolState.abstained]

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
    true, true, 2, false, .uniformInvariant true, false, false, false, false, none, true⟩

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
example : ¬ allowed defaultRepeated (.recordPassBudget 1
    ⟨.user, "late user quota", "post-review", true, true, 1⟩) := by
  simp [allowed, guardRecordPassBudget, defaultRepeated, step]
example : defaultRepeated.passBudget = none := by decide

private def ownerLimit (units : Nat) : PassLimitEvidence :=
  ⟨.boundaryOwner, "explicit owner constraint", "post-initial-review passes", true, true, units⟩

private def funded := step fix (.recordPassBudget 1 (ownerLimit 1))
example : ¬ allowed fix (.recordPassBudget 1
    { ownerLimit 1 with authority := .caller }) := by
  apply caller_cap_never_admitted
  rfl
example : ¬ allowed fix (.recordPassBudget 1
    { ownerLimit 1 with sourceRef := "" }) := by
  simp [allowed, guardRecordPassBudget, PassLimitEvidence.valid]
example : ¬ allowed fix (.recordPassBudget 1
    { ownerLimit 1 with scopeRef := "" }) := by
  simp [allowed, guardRecordPassBudget, PassLimitEvidence.valid]
example : ¬ allowed fix (.recordPassBudget 1
    { ownerLimit 1 with sourceSupported := false }) := by
  simp [allowed, guardRecordPassBudget, PassLimitEvidence.valid]
example : ¬ allowed fix (.recordPassBudget 2 (ownerLimit 1)) := by
  simp [allowed, guardRecordPassBudget, PassLimitEvidence.valid, ownerLimit]
example : allowed fix (.recordPassBudget 1
    { ownerLimit 1 with authority := .user }) := by
  simp only [allowed, guardRecordPassBudget, PassLimitEvidence.valid, ownerLimit]
  decide
example : allowed fix (.recordPassBudget 1
    { ownerLimit 1 with authority := .hardExternal }) := by
  simp only [allowed, guardRecordPassBudget, PassLimitEvidence.valid, ownerLimit]
  decide
example : ¬ allowed fix (.recordPassBudget 1
    { ownerLimit 1 with applicable := false }) := by
  simp [allowed, guardRecordPassBudget, PassLimitEvidence.valid]
example : ¬ allowed fix (.pass .repeatedReviewPass
    { family with continuationJustified := false } none) := by
  apply unchanged_pass_never_admitted
  rfl

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
  (.recordImplementation 5 ["contract"] true true)
example : repairedFirst.reviewReady = false := by decide
private def repaired := step (finish (step repairedFirst
  (.openFlight .implementation .implementation .codexCli "target" 0)) 6 successful)
  (.recordImplementation 6 ["verification"] true true)
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

-- Active, terminal-with-failed-checks, and incomplete assignments never admit review.
example : opened.reviewReady = false := by decide
private def unchecked := step (finish second 1 successful)
  (.recordImplementation 1 ["verification"] false true)
example : unchecked.reviewReady = false := by decide
private def failed := finish second 1 { successful with exitZero := false }
example : failed.batchSettled = false := by decide
example : failed.reviewReady = false := by decide
private def pending := step (finish second 1 successful)
  (.recordImplementation 1 [] true true)
example : pending.reviewReady = false := by decide
-- Assignment count does not exhaust authorized pending work with new evidence.
example : guardImplementationFlight pending := by
  simp only [guardImplementationFlight]
  decide
private def unchanged := step (finish second 1 successful)
  (.recordImplementation 1 [] true false)
example : ¬ guardImplementationFlight unchanged := by
  simp only [guardImplementationFlight]
  decide
-- A single flight still goes directly to its one initial triplet after all checks.
private def single := step (finish (step (step readyState
  (.beginImplementation ⟨["all work"]⟩))
  (.openFlight .implementation .implementation .codexCli "target" 0)) 0 successful)
  (.recordImplementation 0 ["all work"] true true)
example : single.reviewReady = true := by decide

/-- A trace checks each production guard at the state produced by its preceding actions. -/
private def admitted (s : ProtocolState) : List Action → Prop
  | [] => True
  | a :: rest => allowed s a ∧ admitted (step s a) rest

private def run (s : ProtocolState) (actions : List Action) : ProtocolState :=
  actions.foldl step s

-- Every finite number of host wait yields uses the actual production guard/effect.
private def waitActions (id n : Nat) : List Action := List.replicate n (.waitBoundary id)
private theorem wait_prefix (s : ProtocolState) (id n : Nat)
    (h : allowed s (.waitBoundary id)) :
    admitted s (waitActions id n) ∧ run s (waitActions id n) = s := by
  induction n with
  | zero => simp [waitActions, admitted, run]
  | succ n ih =>
    constructor
    · simpa [waitActions, List.replicate_succ, admitted, step] using And.intro h ih.1
    · simpa [waitActions, List.replicate_succ, run, step] using ih.2

private def waiting := step opened (.launchViaRunner 0)
example (n : Nat) : admitted waiting (waitActions 0 n) ∧
    run waiting (waitActions 0 n) = waiting := by
  apply wait_prefix
  simp only [allowed, guardWaitBoundary]
  decide
example : (waiting.flight 0).map (·.retryBudget) = some 0 := by decide
example : ¬ allowed waiting (.collect 0 successful) := by
  simp only [allowed, guardCollect]
  decide
example : ¬ allowed waiting (.fallbackFlight 0 .isolatedTokenSubagent) := by
  simp only [allowed, guardFallback]
  decide

/-- A history of evidence-backed terminal handoffs with an approved check still pending.
This is a fixture of production ProtocolState, not another decision model. -/
private def completedFlight (id : Nat) : FlightRec :=
  { newFlight id .implementation .implementation .codexCli "target" 0 with
    status := .terminal
    launched := true
    notified := true
    envelopeRef := some "result"
    sentinelRef := some "sentinel" }
private def pendingState (n : Nat) : ProtocolState :=
  { readyState with
    flights := (List.range n).map completedFlight
    batch := some ⟨["verification"], false, true, 0⟩ }
private def handoffActions (n : Nat) : List Action :=
  [.openFlight .implementation .implementation .codexCli "target" 0,
   .launchViaRunner n, .hostNotified n, .collect n successful,
   .recordImplementation n [] false true]
private def handoffPrefix : Nat → List Action
  | 0 => []
  | n + 1 => handoffPrefix n ++ handoffActions n

private theorem old_flight_ids (n : Nat) (f : FlightRec)
    (hf : f ∈ (pendingState n).flights) : f.id < n := by
  simp only [pendingState, List.mem_map] at hf
  obtain ⟨i, hi, rfl⟩ := hf
  simpa [completedFlight, newFlight] using List.mem_range.mp hi

private theorem old_lookup_none (n : Nat) : (pendingState n).flight n = none := by
  apply List.find?_eq_none.mpr
  intro f hf
  have hlt := old_flight_ids n f hf
  simp only [beq_iff_eq]
  omega

/-- A current flight appended by production dispatch, before recording its handoff. -/
private def currentState (n : Nat) (f : FlightRec) : ProtocolState :=
  { pendingState n with
    flights := (pendingState n).flights ++ [f]
    batch := some ⟨["verification"], false, false, 0⟩ }
private def startedFlight (n : Nat) := newFlight n .implementation .implementation .codexCli "target" 0
private def launchedFlight (n : Nat) := { startedFlight n with launched := true }
private def notifiedFlight (n : Nat) := { launchedFlight n with notified := true }

private theorem open_pending_allowed (n : Nat) : allowed (pendingState n)
    (.openFlight .implementation .implementation .codexCli "target" 0) := by
  simp [allowed, guardOpenFlight, guardImplementationFlight, pendingState, readyState,
    ProtocolState.initial, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.reviewStarted, ProtocolState.reviewReady, ProtocolState.batchSettled,
    ProtocolState.batchFlights, completedFlight, newFlight, List.any_map, List.all_map]

private theorem open_pending_effect (n : Nat) :
    step (pendingState n) (.openFlight .implementation .implementation .codexCli "target" 0) =
      currentState n (startedFlight n) := by
  simp [step, pendingState, ProtocolState.freshId, currentState, startedFlight]

private theorem update_current (n : Nat) (f : FlightRec) (g : FlightRec → FlightRec)
    (hid : f.id = n) :
    updateFlight (currentState n f) n g = currentState n (g f) := by
  have hold : ((pendingState n).flights.map fun x =>
      if x.id == n then g x else x) = (pendingState n).flights := by
    calc
      _ = (pendingState n).flights.map id := by
        apply List.map_congr_left
        intro x hx
        have hne : x.id ≠ n := Nat.ne_of_lt (old_flight_ids n x hx)
        simp [hne]
      _ = _ := List.map_id _
  simp only [beq_iff_eq] at hold
  simp [updateFlight, currentState, List.map_append, hid, hold]

private theorem lookup_current (n : Nat) (f : FlightRec) (hid : f.id = n) :
    (currentState n f).flight n = some f := by
  have hn : (pendingState n).flights.find? (fun f => f.id == n) = none := old_lookup_none n
  simp [ProtocolState.flight, currentState, List.find?_append, hn, hid]

private theorem launch_current_effect (n : Nat) :
    step (currentState n (startedFlight n)) (.launchViaRunner n) =
      currentState n (launchedFlight n) :=
  update_current n (startedFlight n) (fun f => { f with launched := true }) rfl
private theorem notify_current_effect (n : Nat) :
    step (currentState n (launchedFlight n)) (.hostNotified n) =
      currentState n (notifiedFlight n) :=
  update_current n (launchedFlight n) (fun f => { f with notified := true }) rfl
private theorem collect_current_effect (n : Nat) :
    step (currentState n (notifiedFlight n)) (.collect n successful) =
      currentState n (completedFlight n) := by
  have h := update_current n (notifiedFlight n) (collectEffect successful) rfl
  simpa [step, collectEffect, done, successful, notifiedFlight, launchedFlight,
    startedFlight, completedFlight, newFlight] using h

private theorem record_current_allowed (n : Nat) :
    allowed (currentState n (completedFlight n)) (.recordImplementation n [] false true) := by
  refine ⟨?_, ?_, completedFlight n, ?_, rfl, rfl, rfl, ?_⟩
  · simp [ProtocolState.reviewStarted, ProtocolState.batchFlights, currentState,
      pendingState, completedFlight, newFlight, List.any_map]
  · rfl
  · simp [ProtocolState.batchFlights, currentState, pendingState, completedFlight, newFlight]
  · intro f hf _
    have hm : f ∈ (pendingState n).flights ++ [completedFlight n] := by
      simpa [ProtocolState.batchFlights, currentState] using hf
    rcases List.mem_append.mp hm with hold | hnew
    · exact Nat.le_of_lt (old_flight_ids n f hold)
    · rw [List.mem_singleton.mp hnew]
      exact Nat.le_refl n

private theorem record_current_effect (n : Nat) :
    step (currentState n (completedFlight n)) (.recordImplementation n [] false true) =
      pendingState (n + 1) := by
  simp [step, currentState, pendingState, List.range_succ]

private theorem handoff_admitted (n : Nat) : admitted (pendingState n) (handoffActions n) := by
  simp only [admitted, handoffActions]
  refine ⟨open_pending_allowed n, ?_⟩
  rw [open_pending_effect]
  refine ⟨?_, ?_⟩
  · exact ⟨startedFlight n, lookup_current n _ rfl, rfl, rfl⟩
  rw [launch_current_effect]
  refine ⟨?_, ?_⟩
  · exact ⟨launchedFlight n, lookup_current n _ rfl, rfl⟩
  rw [notify_current_effect]
  refine ⟨?_, ?_⟩
  · exact ⟨notifiedFlight n, lookup_current n _ rfl, rfl, rfl⟩
  rw [collect_current_effect]
  exact ⟨record_current_allowed n, trivial⟩

private theorem handoff_effect (n : Nat) :
    run (pendingState n) (handoffActions n) = pendingState (n + 1) := by
  simp only [run, handoffActions, List.foldl_cons, List.foldl_nil,
    open_pending_effect, launch_current_effect, notify_current_effect,
    collect_current_effect, record_current_effect]

private theorem admitted_append (s : ProtocolState) (xs ys : List Action) :
    admitted s (xs ++ ys) ↔ admitted s xs ∧ admitted (run s xs) ys := by
  induction xs generalizing s with
  | nil => simp [admitted, run]
  | cons a xs ih => simp [admitted, run, ih, and_assoc]

-- SKILL[thm]: "Healthy authorized work continues without caller-invented runtime, work/assignment or round ceilings."
/-- For arbitrary n, every actual allowed/step handoff is admitted and leaves pending work
eligible for another justified handoff. New evidence is an interpreted premise at each return;
this proves finite-prefix continuation, not eventual completion or infinite carrier service. -/
private theorem no_cap_handoff_prefix (n : Nat) :
    admitted (pendingState 0) (handoffPrefix n) ∧
    run (pendingState 0) (handoffPrefix n) = pendingState n := by
  induction n with
  | zero => simp [handoffPrefix, admitted, run]
  | succ n ih =>
    constructor
    · rw [handoffPrefix, admitted_append, ih.2]
      exact ⟨ih.1, handoff_admitted n⟩
    · simp only [handoffPrefix, run, List.foldl_append]
      change run (run (pendingState 0) (handoffPrefix n)) (handoffActions n) = _
      rw [ih.2, handoff_effect]

example (n : Nat) : allowed (pendingState n)
    (.openFlight .implementation .implementation .codexCli "target" 0) := open_pending_allowed n
-- The arbitrary prefix begins with an actual admitted approved-plan start.
example (n : Nat) : admitted readyState
    (.beginImplementation ⟨["verification"]⟩ :: handoffPrefix n) := by
  refine ⟨?_, ?_⟩
  · simp [allowed, guardBeginImplementation, readyState, ProtocolState.initial,
      ImplementationPlan.valid]
  · change admitted (pendingState 0) (handoffPrefix n)
    exact (no_cap_handoff_prefix n).1

example (n : Nat) : ¬ allowed (pendingState n) .advanceStage := by
  simp [allowed, guardAdvanceStage, pendingState, readyState, ProtocolState.reviewReady]
example (n : Nat) : ¬ allowed (pendingState n)
    (.openFlight .review .architecture .codexCli "target" 0) := by
  simp [allowed, guardOpenFlight, guardReviewFlight, pendingState, readyState,
    ProtocolState.reviewReady]
example (n : Nat) : (pendingState n).reviewReady = false := rfl
example (n : Nat) : (pendingState n).passBudget = none := rfl

private def implementationActions (firstId : Nat) : List Action :=
  [.openFlight .implementation .implementation .codexCli "target" 0,
   .launchViaRunner firstId, .hostNotified firstId, .collect firstId successful,
   .recordImplementation firstId ["contract"] true true,
   .openFlight .implementation .implementation .codexCli "target" 0,
   .launchViaRunner (firstId + 1), .hostNotified (firstId + 1), .collect (firstId + 1) successful,
   .recordImplementation (firstId + 1) ["verification"] true true]
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
example : allowed fix (.recordPassBudget 1 (ownerLimit 1)) := by
  simp only [allowed, guardRecordPassBudget, PassLimitEvidence.valid, ownerLimit]
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
  [.beginImplementation ⟨["all work"]⟩,
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
example : allowed recoveryReturned (.recordImplementation 1 ["all work"] true true) := by
  simp [allowed, guardRecordImplementation, recoveryReturned, run, recoveryActions,
    readyState, ProtocolState.initial, step, ProtocolState.reviewStarted,
    ProtocolState.batchFlights, ProtocolState.flight, ProtocolState.freshId,
    newFlight, reopenFlight, ImplementationPlan.start, updateFlight, collectEffect, done, successful]
private def recovered := step recoveryReturned (.recordImplementation 1 ["all work"] true true)
example : recovered.reviewReady = true := by decide
example : allowed recovered .advanceStage := by
  simp only [allowed, guardAdvanceStage, ProtocolState.goalWritten, ProtocolState.modeResolved, ProtocolState.abstained]
  decide
example : recovered.passBudget = none := by decide

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
    [.beginImplementation ⟨["all work"]⟩,
     .openFlight .implementation .implementation .codexCli "target" 0,
     .launchViaRunner 0, .hostNotified 0, .collect 0 successful,
     .recordImplementation 0 ["all work"] true true, .advanceStage] := by
  simp [admitted, allowed, guardBeginImplementation, ImplementationPlan.valid,
    guardOpenFlight, guardImplementationFlight, guardLaunchViaRunner, guardHostNotified,
    guardCollect, guardRecordImplementation, guardAdvanceStage, readyState,
    ProtocolState.initial, ProtocolState.modeResolved, ProtocolState.abstained,
    ProtocolState.reviewStarted, ProtocolState.reviewReady, ProtocolState.batchSettled,
    ProtocolState.batchFlights, ProtocolState.flight, ProtocolState.freshId,
    step, ImplementationPlan.start, newFlight, updateFlight, collectEffect, done,
    successful, FlightRec.active, Stage.next]

private def testsReviewState := { review with capabilityChecked := Carrier.univ }
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
   .advanceStage, .capabilityCheck .codexCli, .resolveMode (.carrier .codexCli), .advanceStage]
-- All authority states permit routine startup without a question, preserving revision support.
example (c : ContinuationEntry) : admitted ProtocolState.initial (startupActions c) := by
  simp [admitted, startupActions, allowed, guardWriteGoal, guardAppendRevision,
    guardAdvanceStage, guardCapabilityCheck, guardResolveMode, Harness.complete,
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
private def correctionFunded := step (corrected .present) (.recordPassBudget 2 (ownerLimit 2))
private def settled := step correctionFunded evaluateA1
-- Each action is guarded: evaluation consumes supplied evidence for the current source.
example : admitted fix [correctionAction .present, .recordPassBudget 2 (ownerLimit 2), evaluateA1, .claimSatisfied] := by
  simp only [admitted, correctionAction, evaluateA1, allowed, guardAppendRevision,
    RevisionEvidence.valid, guardRecordPassBudget, PassLimitEvidence.valid, ownerLimit, guardEvaluateTermination,
    guardClaimSatisfied, ProtocolState.goalWritten]
  decide
example : settled.terminationExit = some .claimPermitted := by decide
example : settled.terminationAuthority = some settled.continuationAuthority := by decide

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
