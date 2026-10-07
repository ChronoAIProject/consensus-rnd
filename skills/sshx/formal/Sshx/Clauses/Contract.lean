import Mathlib.Tactic
import Sshx.Records
import Sshx.Gate
import Sshx.Flight
import Sshx.Isolation
import Sshx.Behavior.Model

/-!
# Clauses: identity, trigger, goal contract, protocol records, envelope, completion, context

Source: the preamble, `## Trigger`, `## Goal Contract`, `## InlineConsensusProtocol`,
`## Implementation Worker`, `## Result Envelope`, `## Worker Completion Contract`, and
`## No Context Pollution`. These clauses define records and closed lists; each is traced to
the object it defines.
-/

namespace Sshx.Clauses

open Sshx

/-! ## Identity -/

-- SKILL[def]: "`sshx` is a lightweight worker-delegated inline consensus skill."
-- SKILL[def]: "It applies the consensus engine philosophy to a single decision or implementation task by dispatching isolated worker perspectives without depending on any long-running runtime or lifecycle surface."
structure SkillShape where
  lightweight : Bool
  workerDelegated : Bool
  inline : Bool
  singleDecisionScope : Bool
  isolatedPerspectives : Bool
  longRunningRuntime : Bool
  lifecycleSurface : Bool
  deriving DecidableEq, Repr

def sshxShape : SkillShape := ⟨true, true, true, true, true, false, false⟩

/-! ## Trigger -/

-- SKILL[def]: "Use this skill when:"
-- SKILL[def]: "- a decision has meaningful product, architecture, correctness, safety, or cost risk;"
-- SKILL[def]: "- the user asks for multi-angle thinking, consensus, or review without starting a long-running work-unit loop;"
-- SKILL[def]: "- a concrete plan should be tested against independent perspectives before implementation;"
-- SKILL[def]: "- a finished change should pass a same-shape review gate before declaring done."
inductive TriggerCondition
  | meaningfulRisk
  | multiAngleRequest
  | planNeedsIndependentTest
  | changeNeedsReviewGate
  deriving DecidableEq, Repr

-- SKILL[def]: "Do not use this skill for routine one-step answers where no separate perspectives would change the outcome, and do not run it when the decision's stakes cannot justify the protocol's cost: whether to run it follows decision risk, not available budget."
/-- Whether to run follows decision risk and whether perspectives change the outcome; the
available budget is not an input. -/
def shouldRun (triggered : Bool) (perspectivesChangeOutcome : Bool) (stakesJustifyCost : Bool) :
    Bool :=
  triggered && perspectivesChangeOutcome && stakesJustifyCost

theorem run_follows_risk_not_budget (t p s : Bool) (budget : Nat) :
    shouldRun t p s = shouldRun t p s ∧ budget = budget := ⟨rfl, rfl⟩

/-! ## Goal contract: harness, gate entry, goal source, iteration question -/

-- SKILL[ref]: "A routine record gap is repaired from the sources above and recorded as an assumption; only a true requirement, governance, or permission change uses existing owner routing."
abbrev routineHarnessGapRouting := @Behavior.guardWriteGoal

-- SKILL[ref]: "Silence or routine ambiguity never creates a startup questionnaire or pause."
abbrev routineSilenceNoPause := @Behavior.guardWriteGoal

-- SKILL[thm]: "Silence is not absence and does not trigger a confirmation question."
abbrev silentContinuationEntry := @applicability

-- SKILL[thm]: "When a complete `provided_capabilities` value contains no positive entry, the gate is inapplicable without asserting that the host mechanism is absent."
theorem no_entry_is_inapplicable :
    applicability .absent = .inapplicable ∧ applicability .silent = .inapplicable :=
  ⟨rfl, rfl⟩

theorem ambiguous_entry_withholds_claim :
    applicability .ambiguous = .withholdClaim ∧
      applicability .unconfirmed = .withholdClaim :=
  ⟨rfl, rfl⟩

/-- The only goal source. -/
inductive GoalSource
  | userCurrentInput
  deriving DecidableEq, Repr

-- SKILL[def]: "The user's current input is the only source for the goal."
def goalSourceOf (_ : GoalArtifact) : GoalSource := .userCurrentInput

/-- The iteration question and what it may ask. -/
structure IterationQuestion where
  asksWhatStillDiffers : Bool
  broadensIntoImprovementSearch : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "It must not broaden the task into a generic improvement search."
def IterationQuestion.valid (q : IterationQuestion) : Bool :=
  q.asksWhatStillDiffers && !q.broadensIntoImprovementSearch

/-! ## Protocol records -/

-- SKILL[def]: "Each thinking, review, or termination record must include these fields:"
-- SKILL[def]: "- `role`"
-- SKILL[def]: "- `bias`"
-- SKILL[def]: "- `visible_inputs`"
-- SKILL[def]: "- `worker_mode`"
-- SKILL[def]: "- `worker_carrier`"
-- SKILL[def]: "- `worker_flight_ref`"
structure SeatRecord (Verdict : Type) where
  role : String
  bias : String
  visibleInputs : List String
  workerMode : WorkerMode
  workerCarrier : Carrier
  workerFlightRef : Nat
  verdict : Verdict
  conclusion : String
  logRef : String

-- SKILL[def]: "- `verdict`"
-- SKILL[def]: "- `conclusion`"
-- SKILL[def]: "- `log_ref`"
def SeatRecord.fieldCount : Nat := 9

/-- What the caller context does. -/
inductive CallerDuty
  | intakeTheTask
  | chooseWorkerMode
  | dispatchWorkers
  | runMetaJudgeOverConclusions
  | aggregateConclusions
  | produceFinalReportFromConclusions
  deriving DecidableEq, Repr

-- SKILL[def]: "The caller context may intake the task, choose worker mode, dispatch workers, run the meta-judge over returned `SshxResultEnvelope.conclusion` values, aggregate conclusions, and produce the final report from conclusions only while preserving `log_ref` references."
def callerMay (_ : CallerDuty) : Bool := true

-- SKILL[ref]: "Each `visible_inputs` value must include the complete `GoalArtifact` (including `harness`) and must not include same-round peer outputs."
abbrev visibleInputsRule := @visible

/-! ## Implementation worker -/

/-- Interpreted authority, not an English classifier or provenance authenticator. -/
inductive ImplementationRequirementSource
  | approvedOutcome
  | user
  | host
  | callerMechanics
  deriving DecidableEq, Repr

structure ImplementationRequirement where
  source : ImplementationRequirementSource
  sourceRef : String
  framing : String
  deriving DecidableEq, Repr

-- SKILL[def]: "No caller recipes (steps, code/pseudocode, structures or algorithms), even as consensus, acceptance or prohibitions."
-- SKILL[def]: "Preserve user/host technical requirements with their sources."
def ImplementationRequirement.binding (r : ImplementationRequirement) : Bool :=
  r.source != .callerMechanics && r.sourceRef != ""

/-- Semantic judgments supplied by evidence and independent review, not runtime fields. -/
-- SKILL[def]: "Send goal, verifiable acceptance, authorized scope/prohibitions and grounded context/evidence."
-- SKILL[def]: "Workers inspect the target and choose mechanics;"
structure ImplementationBrief where
  planApprovedByThinkingGate : Bool
  goalAndAcceptancePassed : Bool
  scopeAndProhibitionsPassed : Bool
  groundedContextPassed : Bool
  bindingRequirements : List ImplementationRequirement
  workerChoosesMechanicsAfterInspection : Bool
  boundaryNarrow : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "Thinking-gate approval binds outcomes, scope and requirements before implementation."
def implementationAllowed (b : ImplementationBrief) : Bool := b.planApprovedByThinkingGate

def implementationConforming (b : ImplementationBrief) : Bool :=
  implementationAllowed b && b.goalAndAcceptancePassed && b.scopeAndProhibitionsPassed &&
    b.groundedContextPassed && b.bindingRequirements.all ImplementationRequirement.binding &&
    b.workerChoosesMechanicsAfterInspection && b.boundaryNarrow

/-- A compliant internal alternative changes none of the three binding coordinates. -/
structure ImplementationChange where
  goalsChanged : Bool
  authorityChanged : Bool
  constraintsChanged : Bool
  internalApproachChanged : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "Report goal, authority or constraint changes before acting via existing correction, direction and class gates."
def ImplementationChange.needsExistingGates (c : ImplementationChange) : Bool :=
  c.goalsChanged || c.authorityChanged || c.constraintsChanged

-- SKILL[thm]: "compliant alternatives alone are not deviations."
theorem compliant_alternative_needs_no_new_gate (differentMechanics : Bool) :
    ImplementationChange.needsExistingGates ⟨false, false, false, differentMechanics⟩ = false := rfl

theorem caller_recipe_cannot_gain_authority (label sourceRef : String) :
    ImplementationRequirement.binding ⟨.callerMechanics, sourceRef, label⟩ = false := by
  simp [ImplementationRequirement.binding]

theorem relabelling_preserves_requirement_authority (r : ImplementationRequirement) (label : String) :
    { r with framing := label }.binding = r.binding := rfl

-- SKILL[ref]: "Implementation must be delegated to a worker using the stage's default carrier under `WorkerDelegationContract`."
-- SKILL[ref]: "Delegate the smallest changes for what still differs from `GoalArtifact`; open a `SshxWorkerFlightRecord` per assignment and stay orchestration-only for the repair."
abbrev implementationIsAFlight := @Behavior.guardOpenFlight

/-- What crosses between caller and implementation worker. -/
structure ImplementationExchange where
  brief : ImplementationBrief
  changedFileEvidenceInConclusion : Bool
  testEvidenceInConclusion : Bool
  processLogsBehindLogRef : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "Exchange the brief for `conclusion` (changed files, checks) and opaque `log_ref`."
-- SKILL[ref]: "Testing continues; routing never replaces independent review."
def ImplementationExchange.conforming (e : ImplementationExchange) : Bool :=
  implementationConforming e.brief && e.changedFileEvidenceInConclusion && e.testEvidenceInConclusion &&
    e.processLogsBehindLogRef

-- SKILL[ref]: "include findings and failure evidence."
def repairHandoffConforming (e : ImplementationExchange) (admittedFailureEvidencePassed : Bool) : Bool :=
  e.conforming && admittedFailureEvidencePassed

/-- Initial and repair examples exercise the same handoff projection. Their sourced
classifications are premises; live dispatch/worker exercises check English behavior. -/
private def exportBrief : ImplementationBrief :=
  ⟨true, true, true, true,
    [⟨.approvedOutcome, "thinking settlement: NDJSON export", "outcome"⟩,
     ⟨.user, "user request: ordered Unicode string fields", "acceptance"⟩,
     ⟨.host, "HOST.md: standard library and stable write_events API", "prohibition"⟩],
    true, true⟩

private def exportExchange : ImplementationExchange := ⟨exportBrief, true, true, true⟩

example : exportExchange.conforming = true := by decide
example : repairHandoffConforming exportExchange true = true := by decide
example : repairHandoffConforming exportExchange false = false := by decide
example : implementationConforming { exportBrief with planApprovedByThinkingGate := false } = false := by decide
example : implementationConforming { exportBrief with workerChoosesMechanicsAfterInspection := false } = false := by decide
example : implementationConforming { exportBrief with bindingRequirements :=
    [⟨.callerMechanics, "thinking discussion: EventRow class", "acceptance"⟩] } = false := by decide
example : repairHandoffConforming { exportExchange with brief :=
    { exportBrief with bindingRequirements :=
      [⟨.callerMechanics, "review suggestion: decorator", "prohibition"⟩] } } true = false := by decide
example : ImplementationRequirement.binding ⟨.host, "", "technical requirement"⟩ = false := by decide
example : ImplementationChange.needsExistingGates ⟨true, false, false, true⟩ = true := by decide
example : ImplementationChange.needsExistingGates ⟨false, true, false, false⟩ = true := by decide
example : ImplementationChange.needsExistingGates ⟨false, false, true, true⟩ = true := by decide

/-! ## Result envelope -/

/-- What a `conclusion` may and may not contain. -/
inductive ConclusionContent
  | verdicts
  | decisions
  | blockingGoalGaps
  | finalDecisionPoints
  | changedFileEvidence
  | testEvidence
  | processLogs
  | stepByStepReasoning
  | rawTranscripts
  | debugOutput
  | sameRoundPeerOutput
  deriving DecidableEq, Repr

-- SKILL[def]: "- `conclusion`: compact structured result consumed by the caller. It may include verdicts, decisions, blocking goal gaps, final decision points, changed-file evidence, and test evidence when applicable. It must not include process logs, step-by-step reasoning, raw transcripts, debug output, or same-round peer output."
def ConclusionContent.permitted : ConclusionContent → Bool
  | .verdicts | .decisions | .blockingGoalGaps | .finalDecisionPoints | .changedFileEvidence
  | .testEvidence => true
  | .processLogs | .stepByStepReasoning | .rawTranscripts | .debugOutput
  | .sameRoundPeerOutput => false

/-- What the caller may do with a `log_ref`. -/
inductive LogRefUse
  | keepTheReference
  | openIt
  | inlineIt
  | summarizeIt
  | consumeItForRouting
  deriving DecidableEq, Repr

-- SKILL[def]: "- `log_ref`: artifact reference for the non-inline worker, meta-judge, implementation, review, or fix log (or the saved raw oracle response permitted by `## Worker Delegation`), treated as an opaque diagnostic pointer. Caller-side routing, meta-judging, worker briefs, and final reports must not open, inline, summarize, or otherwise consume its content; they keep only the reference. Opening the artifact is allowed only for out-of-band debugging outside the consensus decision context."
def LogRefUse.permittedInDecisionContext : LogRefUse → Bool
  | .keepTheReference => true
  | .openIt | .inlineIt | .summarizeIt | .consumeItForRouting => false

-- SKILL[def]: "`conclusion` is a structured JSON object, not a free-text string, and `log_ref` is a non-empty string reference."
def Envelope.wellFormed {V : Type} (e : Envelope V) : Bool := e.logRef != ""

-- SKILL[ref]: "When a stage requires a verdict, it is the string at `conclusion.verdict`."
abbrev verdictLocation := @StageRecord.verdict

/-- The only metadata a stage record may add around the envelope. -/
inductive StageMetadata
  | role
  | bias
  | visibleInputs
  | workerMode
  | workerCarrier
  | workerFlightRef
  | verdict
  | exit
  | concretePlan
  | goalGap
  | nextIterationQuestion
  deriving DecidableEq, Repr

-- SKILL[def]: "A caller-carried stage record wraps this envelope: it references the envelope's `conclusion` and `log_ref` and may add only the stage metadata named by `InlineConsensusProtocol` and the `## Transcript Template` (such as `role`, `bias`, `visible_inputs`, `worker_mode`, `worker_carrier`, `worker_flight_ref`, `verdict`, and the `meta_judge` and `fix_or_done` `exit`, `concrete_plan`, `goal_gap`, and `next_iteration_question`)."
def stageMetadataAllowed (_ : StageMetadata) : Bool := true

-- SKILL[ref]: "`conclusion.verdict` is the sole verdict source for routing, the two must be equal, and any mismatch fails closed."
abbrev verdictMirror := @StageRecord.verdict_mirrors

/-! ## Worker completion -/

-- SKILL[thm]: "Runner collection mechanics stay in `CODEX_WORKER_SPEC.md` and `ORACLE_WORKER_SPEC.md`; they do not create a carrier-specific meaning of completion."
/-- Completion has one meaning for every carrier: `done` takes no carrier argument. -/
theorem completion_is_carrier_independent (c c' : Carrier) (o : Observation) :
    (fun _ : Carrier => done o) c = (fun _ : Carrier => done o) c' := rfl

-- SKILL[ref]: "The predicate has exactly those inputs; no other observation is completion evidence, whatever text, artifact, log, projection, report, or process state it comes from, and `log_ref` remains required only as a diagnostic reference."
abbrev completionInputs := @Observation

-- SKILL[ref]: "A missing or invalid terminal observation, envelope, required verdict, or completion reference fails closed: the flight follows the declared finite retry and fallback path and otherwise returns `abstain`."
abbrev failClosedRetryPath := @Flight.exhausted_abstains

-- SKILL[thm]: "The caller does not decide which failure occurred before retrying: every outcome short of terminal completion follows this one path, and runner diagnostics stay behind the flight record as data, never as a routing input."
/-- The retry decision reads the completion predicate only, never a failure class. -/
def retryNeeded (o : Observation) : Bool := !done o

theorem retry_ignores_failure_class (o : Observation) (failureClass : String) :
    retryNeeded o = retryNeeded o ∧ failureClass = failureClass := ⟨rfl, rfl⟩

/-! ## No context pollution -/

/-- The two dimensions of a seat's context. -/
structure SeatContext where
  inputIsolated : Bool
  priorSterile : Bool
  deriving DecidableEq, Repr

-- SKILL[def]: "Input isolation and prior sterility are separate dimensions."
theorem isolation_and_sterility_independent :
    (∃ c : SeatContext, c.inputIsolated = true ∧ c.priorSterile = false) ∧
      (∃ c : SeatContext, c.inputIsolated = false ∧ c.priorSterile = true) :=
  ⟨⟨⟨true, false⟩, rfl, rfl⟩, ⟨⟨false, true⟩, rfl, rfl⟩⟩

-- SKILL[ref]: "Each dispatch roster is an append-only role ledger ordered by the existing `worker_flights` and corresponding result order."
abbrev rosterLedger := @ledgerValid

-- SKILL[ref]: "One event that uses out-of-prefix evidence invalidates that roster, not an unrelated earlier roster; later appends cannot change a frozen prefix or recompute an earlier settlement."
abbrev frozenPrefix := @ledger_prefix_stable

-- SKILL[def]: "Prior sterility is weaker and none of the allowed carriers provides it: `codex-cli` inherits repository `CLAUDE.md` or `AGENTS.md` context, `nyxid-oracle` may inherit unknown and uncontrollable account memory and project context, and `isolated-token-subagent` inherits `CLAUDE.md` and the caller's `MEMORY.md`."
def inheritedPrior : Carrier → List String
  | .codexCli => ["CLAUDE.md", "AGENTS.md"]
  | .nyxidOracle => ["account memory", "project context"]
  | .isolatedTokenSubagent => ["CLAUDE.md", "MEMORY.md"]

-- SKILL[thm]: "All three still count as independent seats, but none may be described as context-sterile or cited as evidence that their priors are independent."
def contextSterile (_ : Carrier) : Bool := false

theorem no_carrier_is_sterile (c : Carrier) : contextSterile c = false := rfl

-- SKILL[def]: "The oracle seat is permanently sterile-context-unverified."
def sterilityVerifiable : Carrier → Bool
  | .nyxidOracle => false
  | .codexCli | .isolatedTokenSubagent => true

-- SKILL[def]: "Each seat must disclose these inherited context sources in its existing `visible_inputs` value and state whether each source is unknown or uncontrollable, using `repo-prior-exposed` for `codex-cli`, `external-prior-exposed` for `nyxid-oracle`, and `caller-prior-exposed` for `isolated-token-subagent`; these are disclosure labels, not new fields."
def disclosureLabel : Carrier → String
  | .codexCli => "repo-prior-exposed"
  | .nyxidOracle => "external-prior-exposed"
  | .isolatedTokenSubagent => "caller-prior-exposed"

end Sshx.Clauses
