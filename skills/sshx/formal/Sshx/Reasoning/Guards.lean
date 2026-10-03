import Sshx.Blocking

/-!
# Reasoning boundary and planning guards

These small predicates keep trigger provenance, pre-run occurrence, review-focus scope,
repeated-family routing, and planning evidence explicit. They are prompt-level evidence, not a
runtime schema.
-/

namespace Sshx.Reasoning

open Sshx

-- SKILL[def]: "During `intake`, the caller must ask the boundary owner"
structure IntakeScopeAnswers where
  outputConsumerConfirmed : Bool
  localStateScopeConfirmed : Bool
  deriving DecidableEq, Repr

def intakeScopeComplete (answers : IntakeScopeAnswers) : Bool :=
  answers.outputConsumerConfirmed && answers.localStateScopeConfirmed

-- SKILL[def]: "Planning evidence must be gathered before a thinking seat settles a candidate plan: search relevant authoritative best-practice sources and inspect the current `work_target`, repository artifacts, history, and visible prior decisions for work that already covers the named `GoalArtifact` terms."
structure PlanningEvidence where
  sourcesSearched : Bool
  sourcesVerified : Bool
  existingWorkInspected : Bool
  overlapReused : Bool
  uncoveredDeltaRecorded : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "Record the searched sources, applicable practice, inspected existing work, and overlap or uncovered delta in the existing dispatch brief or `SshxResultEnvelope.conclusion`."
def planningEvidenceComplete (evidence : PlanningEvidence) : Bool :=
  evidence.sourcesSearched && evidence.sourcesVerified && evidence.existingWorkInspected &&
    evidence.overlapReused && evidence.uncoveredDeltaRecorded

-- SKILL[prose]: "This is prompt-level evidence and adds no `GoalArtifact` field or envelope field."
-- why: keeps planning evidence in the existing brief and conclusion channels instead of adding schema.

-- SKILL[def]: "Reuse work that already covers a named `GoalArtifact` term and plan only the uncovered delta."
inductive PlanningRoute
  | planUncoveredDelta
  | advisoryUnverified
  | rejectDuplicate
  deriving DecidableEq, Repr

-- SKILL[def]: "A duplicate mechanism is not a concrete plan."
def planningRoute (evidence : PlanningEvidence) (overlapExists : Bool) : PlanningRoute :=
  if !evidence.sourcesSearched || !evidence.sourcesVerified || !evidence.existingWorkInspected then
    .advisoryUnverified
  else if (overlapExists && !evidence.overlapReused) || !evidence.uncoveredDeltaRecorded then
    .rejectDuplicate
  else
    .planUncoveredDelta

-- SKILL[def]: "Search scope is bounded by `GoalArtifact` and the assigned review focus; stop once the applicable practice and overlap or delta are settled instead of opening a generic improvement search."
def planningScopeBound (goalBound focusBound settled : Bool) : Bool :=
  goalBound && focusBound && settled

-- SKILL[def]: "A source that cannot be reached or verified is `ASSUMED-UNVERIFIED` and may guide a candidate only as advisory."
def unverifiedPlanningSourceIsAdvisory (sourceVerified : Bool) : Bool := !sourceVerified

-- SKILL[def]: "It cannot be called a best practice, block routing, or create a plan element by itself."
def unverifiedSourceCanRoute (sourceVerified : Bool) : Bool := sourceVerified

-- SKILL[def]: "Search results never create a `GoalArtifact` term or current consumer."
def searchResultsCreateGrounding (sourceVerified : Bool) : Bool := false

-- SKILL[def]: "When `conclusion.blocking_findings` is present"
structure BlockingFindingMetadata where
  trigger : String
  triggerActor : String
  mechanismFamily : String
  deriving DecidableEq, Repr

-- SKILL[def]: "Trusted-party failure, omission, and uncertainty remain eligible"
def ordinaryOperationKeepsTrustedFailureEligible (ordinaryPath : Bool) (trustedFailure : Bool) : Bool :=
  ordinaryPath && trustedFailure

-- SKILL[def]: "An omission that only matters after a trusted operator deliberately selects"
def nonstandardOmissionIsNotOrdinary (nonstandardPath : Bool) (omission : Bool) : Bool :=
  nonstandardPath && omission

-- SKILL[def]: "A blocking finding must also pass the structured trigger check"
inductive TriggerPath
  | ordinaryOperation
  | nonstandardDeliberate
  | malicious
  deriving DecidableEq, Repr

structure TriggerRecord where
  trigger : String
  triggerActor : String
  triggerPath : TriggerPath
  mechanismFamily : String
  recordedOccurrence : Option String
  deriving DecidableEq, Repr

def triggerCheck (record : TriggerRecord) : Bool :=
  match record.triggerPath with
  | .ordinaryOperation => true
  | .nonstandardDeliberate | .malicious => record.recordedOccurrence.isSome

-- SKILL[def]: "`recorded_occurrence` must have existed before this run"
def occurrenceIsIndependent (recordedOccurrence : Option String) (createdDuringRun : Bool) : Bool :=
  recordedOccurrence.isSome && !createdDuringRun

-- SKILL[thm]: "a fixture, reproduction, or state deliberately created by a seat, caller, or repair worker during this run is reachability evidence only"
theorem run_fixture_is_not_occurrence (createdDuringRun : Bool) :
    occurrenceIsIndependent none createdDuringRun = false := by
  simp [occurrenceIsIndependent]

-- SKILL[def]: "An input that names both is blocking"
def findingForce (input : Input) (record : TriggerRecord) : Force :=
  if force input == .blocking && triggerCheck record then .blocking else .advisory

-- SKILL[thm]: "An input that names fewer than both is advisory"
theorem missing_conjunct_is_advisory (input : Input) (record : TriggerRecord)
    (h : input.namesGoalTerm = false ∨ input.namesWorkEvidence = false) :
    findingForce input record = .advisory := by
  rcases h with h | h <;> simp [findingForce, force, h]

-- SKILL[thm]: "Failure is objective, not semantic"
theorem ordinary_reachable_failure_stays_blocking (input : Input) (record : TriggerRecord)
    (hi : force input = .blocking) (hp : record.triggerPath = .ordinaryOperation) :
    findingForce input record = .blocking := by
  simp [findingForce, hi, triggerCheck, hp]

-- SKILL[def]: "Inputs that name no second conjunct include an imagined input"
def imaginedInputIsAdvisory : Bool := true

-- SKILL[def]: "Every review focus in a dispatch brief must name the `GoalArtifact` clause"
structure ReviewFocusBinding where
  goalTerm : String
  settlingEvidence : String
  deriving DecidableEq, Repr

def reviewFocusBound (focus : ReviewFocusBinding) : Bool :=
  focus.goalTerm != "" && focus.settlingEvidence != ""

-- SKILL[def]: "An unbounded request to find any mechanism that could make a result fail is invalid"
def unboundedReviewFocusIsInvalid (bounded : Bool) : Bool := !bounded

-- SKILL[def]: "A focus that cannot be tied to a `GoalArtifact` term is an orchestration gap"
def focusNeedsCallerCorrection (focus : ReviewFocusBinding) : Bool := !reviewFocusBound focus

-- SKILL[def]: "Before `propose` or `revise`, each seat must carry the planning search and existing-work evidence in its existing `visible_inputs` or `conclusion`; the meta-judge may converge only on a plan that reuses covered work and names its uncovered delta."
def planNeedsPlanningEvidence (hasEvidence reusesCoveredWork namesUncoveredDelta : Bool) : Bool :=
  hasEvidence && reusesCoveredWork && namesUncoveredDelta

-- SKILL[def]: "Missing or unverified planning evidence is `ASSUMED-UNVERIFIED` and cannot by itself justify `implement`."
def missingPlanningEvidenceBlocksImplement (evidenceVerified : Bool) : Bool := !evidenceVerified

-- SKILL[def]: "It must also carry the structured `trigger`, `trigger_actor`, `trigger_path`"
def findingMetadataShapeComplete (metadata : TriggerRecord) : Bool :=
  metadata.trigger != "" && metadata.triggerActor != "" && metadata.mechanismFamily != ""

-- SKILL[def]: "Before dispatching a repair after two consecutive passes whose blocking findings name the same"
inductive FamilyRoute
  | continue
  | escalateToBoundaryOwner
  deriving DecidableEq, Repr

def repeatedFamilyRoute (sameGoalTerm sameMechanismFamily familyDeclared : Bool) : FamilyRoute :=
  if sameGoalTerm && sameMechanismFamily && !familyDeclared then
    .escalateToBoundaryOwner
  else
    .continue

-- SKILL[def]: "A class-level decision may use only the recorded findings and confirmed harness"
def classDecisionUsesRecordedInputs (recordedFindings confirmedHarness : Bool) : Bool :=
  recordedFindings && confirmedHarness

-- SKILL[def]: "On every review pass after the initial implementation, the review triplet must list each defense"
def reviewListsNewDefenses (listed : Bool) : Bool := listed

-- SKILL[def]: "An element that names neither the `GoalArtifact` term that demands it nor an existing consumer"
def defenseAdmission (namesGoalTerm namesConsumer : Bool) : Bool :=
  namesGoalTerm || namesConsumer

end Sshx.Reasoning
