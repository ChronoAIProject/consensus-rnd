import Mathlib.Tactic
import Sshx.Carrier
import Sshx.Semantics.Register

/-!
# Clauses: boundaries, baseline failure modes, transcript template, verification

Source: `## Boundaries`, `## Baseline Failure Mode`, `## Transcript Template`, `## Verification`.
-/

namespace Sshx.Clauses

open Sshx

/-! ## Boundaries -/

-- SKILL[def]: "This skill is a prompt contract with a closed set of exactly four named mechanical script exceptions, governed only by `skills/sshx/CODEX_WORKER_SPEC.md` and their behavior tests:"
-- SKILL[def]: "- `skills/sshx/scripts/run-codex-worker.sh`;"
-- SKILL[def]: "- `skills/sshx/scripts/run-codex-worker-batch.sh`;"
-- SKILL[def]: "- `skills/sshx/scripts/read-codex-worker-status.sh`;"
-- SKILL[def]: "- `skills/sshx/scripts/clean-codex-worker-runs.sh`."
def mechanicalScripts : List String :=
  ["skills/sshx/scripts/run-codex-worker.sh", "skills/sshx/scripts/run-codex-worker-batch.sh",
    "skills/sshx/scripts/read-codex-worker-status.sh",
    "skills/sshx/scripts/clean-codex-worker-runs.sh"]

theorem exactly_four_scripts : mechanicalScripts.length = 4 := rfl

/-- Runtime surfaces none of the contract's named objects are. -/
inductive RuntimeSurface
  | runtimeApi
  | daemon
  | cli
  | parsedSchema
  | markerFamily
  | lifecycleAuthority
  | secondTranscriptChannel
  deriving DecidableEq, Repr

-- SKILL[def]: "All records, contracts, gates, templates, and reasoning guidance named here are prompt-level only: none is a runtime API, daemon, CLI, parsed schema, marker family, lifecycle authority, or second transcript channel."
def namedObjectIs (_ : RuntimeSurface) : Bool := false

/-- Dependencies the skill must not add or depend on. -/
inductive ForbiddenDependency
  | anyOtherHelperScript
  | daemons
  | repositoryOwnedCli
  | githubLifecycleOperations
  | gitLifecycleOperations
  | labels
  | releaseAuthority
  | publicMarkerFamily
  | runtimeHostConfigurationAsSourceOfTruth
  | otherSkillsInternalsAsDependency
  deriving DecidableEq, Repr

-- SKILL[def]: "It must not add or depend on:"
-- SKILL[def]: "- any other helper script;"
-- SKILL[def]: "- daemons;"
-- SKILL[def]: "- repository-owned CLI;"
-- SKILL[def]: "- GitHub lifecycle operations;"
-- SKILL[def]: "- git lifecycle operations;"
def mayDependOn (_ : ForbiddenDependency) : Bool := false

-- SKILL[def]: "- labels;"
-- SKILL[def]: "- release authority;"
-- SKILL[def]: "- a public marker family;"
-- SKILL[def]: "- runtime host configuration as a production source of truth;"
-- SKILL[def]: "- other skills' or repository-owned internal prompts, scripts, or runtimes as an implementation dependency."
theorem nothing_forbidden_is_depended_on (d : ForbiddenDependency) : mayDependOn d = false := rfl

-- SKILL[ref]: "Allowed worker carriers are limited to `codex-cli`, `nyxid-oracle`, and `isolated-token-subagent`."
abbrev allowedCarriers := Carrier.univ

-- SKILL[def]: "Use them only as worker delegation capability, not as controller authority."
def carrierHasControllerAuthority (_ : Carrier) : Bool := false

/-- What `nyxid oracle` is used as. -/
inductive OracleUse
  | workerCarrier
  | helperScriptTheSkillOwns
  | daemon
  | lifecycleActor
  deriving DecidableEq, Repr

-- SKILL[def]: "`nyxid oracle` is used only as the `nyxid-oracle` worker carrier — a reasoning channel in the same category as `codex-cli` — never as a helper script the skill owns, a daemon, or a lifecycle actor."
def oracleUsedAs : OracleUse → Bool
  | .workerCarrier => true
  | .helperScriptTheSkillOwns | .daemon | .lifecycleActor => false

/-! ## Baseline failure modes -/

-- SKILL[def]: "Without this skill, lightweight high-risk decisions tend to regress to these source-owned classes:"
-- SKILL[def]: "- fake consensus: self-application, pseudo-isolation, missing worker-mode declaration, or caller self-certification in place of the fixed thinking, review, and termination rosters;"
-- SKILL[def]: "- false grounding: unverified premises, retrospective fit, imagined relevance, or a mathematical name whose hypotheses the recorded mechanism state does not instantiate;"
-- SKILL[def]: "- rabbit-holing: blocking by default, peripheral detail, repeated unchanged work, finite case registers, procedural findings against the run's own records, or per-case diagnosis after one declared absorber already determines the goal-visible route;"
-- SKILL[def]: "- wrong convergence: beauty without worth, scalarized incomparable candidates, path-dependent gain on non-additive coordinates, or budget and lifecycle milestones presented as completion;"
-- SKILL[def]: "- contaminated adjudication: same-round peer evidence, an out-of-prefix ledger event, or dependency-reaching evidence presented as independent;"
-- SKILL[def]: "- boundary drift: carrier diversity over-claims, improvised worker mechanics, or daemon, GitHub, git, label, and release orchestration for an inline decision."
inductive BaselineFailure
  | fakeConsensus
  | falseGrounding
  | rabbitHoling
  | wrongConvergence
  | contaminatedAdjudication
  | boundaryDrift
  deriving DecidableEq, Repr

/-- The model object that guards each baseline class. -/
def guardedBy : BaselineFailure → String
  | .fakeConsensus => "Sshx.caller_never_consensus, Sshx.fake_roster_rejected"
  | .falseGrounding => "Sshx.Reasoning.analogy_has_no_force, Sshx.Reasoning.explicit_excludes_silent_reliance"
  | .rabbitHoling => "Sshx.force_blocking_iff, Sshx.Reasoning.shallowest_is_the_standard"
  | .wrongConvergence => "Sshx.no_implement_without_worth, Sshx.Semantics.candidate_dominance_is_preorder"
  | .contaminatedAdjudication => "Sshx.same_round_peer_invisible, Sshx.Semantics.enlarging_closure_only_removes_admission"
  | .boundaryDrift => "Sshx.fallback_forbids, Sshx.Behavior.never_lifecycle"

/-! ## Transcript template -/

-- SKILL[def]: "Use this compact nesting shape when the decision is non-trivial; every referenced record keeps the fields already defined by its owning section:"
structure TranscriptShape where
  intake : Bool
  workerDelegation : Bool
  workerFlights : Bool
  thinkingPanelWorkers : Bool
  metaJudge : Bool
  implementationWorker : Bool
  reviewTripletWorkers : Bool
  fixOrDone : Bool
  deriving DecidableEq, Repr

def transcriptTemplate : TranscriptShape := ⟨true, true, true, true, true, true, true, true⟩

/-! ## Verification -/

-- SKILL[def]: "The contract for this skill is verified by `skills/sshx/tests/test_sshx_contract.py`."
def contractTestFile : String := "skills/sshx/tests/test_sshx_contract.py"

-- SKILL[ref]: "Before adding or changing this skill, record the no-skill failure mode as source-owned contract or test evidence."
abbrev noSkillFailureModes := BaselineFailure

-- SKILL[ref]: "When a new failure case appears, prefer widening or verifying the absorber that already covers its class to adding another case entry: when the same verified construction hypothesis applies, the register cannot be completed, and every entry added must be held true by every later change."
abbrev registerCannotBeCompleted := @Semantics.every_register_escaped

/-- What may be tracked as published skill source. -/
inductive SourceKind
  | contract
  | script
  | test
  | formalModel
  | runtimeArtifact
  deriving DecidableEq, Repr

-- SKILL[def]: "Do not track runtime artifacts as published skill source."
def trackedAsSource : SourceKind → Bool
  | .contract | .script | .test | .formalModel => true
  | .runtimeArtifact => false

/-- Evidence for a claim about an external carrier or tool capability. -/
inductive CapabilityClaimEvidence
  | realToolEndToEnd
  | fakeCarrierOnly
  | none
  deriving DecidableEq, Repr

-- SKILL[def]: "Before publishing or changing a claim about an external carrier or tool capability, verify the exact composed workflow end to end with the real tool."
def supportedOption : CapabilityClaimEvidence → Bool
  | .realToolEndToEnd => true
  | .fakeCarrierOnly | .none => false

-- SKILL[thm]: "Fake carriers may supplement deterministic contract tests but must not be the sole evidence for a supported capability; when real verification is unavailable, mark the claim ASSUMED-UNVERIFIED and do not expose it as a supported option."
theorem fake_carrier_is_not_sole_evidence : supportedOption .fakeCarrierOnly = false := rfl

end Sshx.Clauses
