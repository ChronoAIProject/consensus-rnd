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

-- SKILL[def]: "The closed set of exactly six named mechanical script exceptions is governed only by `skills/sshx/CODEX_WORKER_SPEC.md`, `skills/sshx/ORACLE_WORKER_SPEC.md`, and their behavior tests:"
-- SKILL[def]: "- `skills/sshx/scripts/run-codex-worker.sh`;"
-- SKILL[def]: "- `skills/sshx/scripts/run-oracle-worker.py`;"
-- SKILL[def]: "- `skills/sshx/scripts/run-codex-worker-batch.sh`;"
-- SKILL[def]: "- `skills/sshx/scripts/read-codex-worker-status.sh`;"
-- SKILL[def]: "- `skills/sshx/scripts/clean-codex-worker-runs.sh`;"
-- SKILL[def]: "- `skills/sshx/scripts/prune-inactive-codex-worker-runs.sh`."
def mechanicalScripts : List String :=
  ["skills/sshx/scripts/run-codex-worker.sh", "skills/sshx/scripts/run-oracle-worker.py",
    "skills/sshx/scripts/run-codex-worker-batch.sh",
    "skills/sshx/scripts/read-codex-worker-status.sh",
    "skills/sshx/scripts/clean-codex-worker-runs.sh",
    "skills/sshx/scripts/prune-inactive-codex-worker-runs.sh"]

theorem exactly_six_scripts : mechanicalScripts.length = 6 := rfl

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
  | skillOwnedDaemons
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
-- SKILL[def]: "- skill-owned daemons;"
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

-- SKILL[ref]: "Allowed worker carriers are limited to `codex-cli`, `heca`, `nyxid-oracle`, and `isolated-token-subagent`."
abbrev allowedCarriers := Carrier.univ

-- SKILL[def]: "Use them only as worker delegation capability, not as controller authority."
def carrierHasControllerAuthority (_ : Carrier) : Bool := false

/-! ## Baseline failure modes -/

-- SKILL[def]: "Source-owned no-skill failure classes:"
-- SKILL[def]: "- fake consensus: self-application, pseudo-isolation, undeclared worker mode, self-certification;"
-- SKILL[def]: "- false grounding: unverified premises, retrospective fit, imagined relevance;"
-- SKILL[def]: "- rabbit-holing: default blocking, peripheral/repeated work, case registers, procedural findings;"
-- SKILL[def]: "- goal loss: 忘记目标 drift to an unranked target, 因小失大 a part served at the whole's cost, or 买椟还珠 form held above substance;"
-- SKILL[def]: "- wrong convergence: beauty without worth, scalarized incomparability, non-additive path gain, false completion proxies;"
-- SKILL[def]: "- contaminated adjudication: peer/out-of-prefix/dependency-reaching evidence presented as independent;"
-- SKILL[def]: "- boundary drift: diversity overclaims, improvised mechanics, unauthorized lifecycle;"
inductive BaselineFailure
  | fakeConsensus
  | falseGrounding
  | rabbitHoling
  | goalLoss
  | wrongConvergence
  | contaminatedAdjudication
  | boundaryDrift
  | forcedIntakeConfirmation
  | scopeExpansion
  deriving DecidableEq, Repr

-- SKILL[def]: "- forced intake confirmation: routine questions replacing sourced assumptions;"
-- SKILL[def]: "- scope expansion: risk activation, uncertain repairs, stale obligations after correction."
/-- The model object that guards each baseline class. -/
def guardedBy : BaselineFailure → String
  | .scopeExpansion => "Sshx.Behavior.guardWriteGoal, Sshx.force, Sshx.Behavior.guardAppendRevision"
  | .fakeConsensus => "Sshx.caller_never_consensus, Sshx.fake_roster_rejected"
  | .falseGrounding => "Sshx.Reasoning.analogy_has_no_force, Sshx.Reasoning.explicit_excludes_silent_reliance"
  | .rabbitHoling => "Sshx.force_blocking_iff, Sshx.Reasoning.shallowest_is_the_standard"
  | .goalLoss => "Sshx.Reasoning.unranked_is_not_served, Sshx.Reasoning.substance_never_spent_for_form"
  | .wrongConvergence => "Sshx.no_implement_without_worth, Sshx.Semantics.candidate_dominance_is_preorder"
  | .contaminatedAdjudication => "Sshx.same_round_peer_invisible, Sshx.Semantics.enlarging_closure_only_removes_admission"
  | .boundaryDrift => "Sshx.fallback_forbids, Sshx.Behavior.never_lifecycle"
  | .forcedIntakeConfirmation => "Sshx.Behavior.guardWriteGoal"

/-! ## Transcript template -/

-- SKILL[def]: "Non-trivial decisions use this nesting; records keep their owning section's fields:"
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

-- SKILL[ref]: "For new failures prefer verifying/widening the class absorber over case entries: verified finite-construction hypotheses defeat register closure; open/infinite domains still need class coverage, and every added entry binds later changes."
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
