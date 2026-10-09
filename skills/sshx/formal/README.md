# sshx formal model

A Lean 4 model of the whole `sshx` prompt contract in [`../SKILL.md`](../SKILL.md). Every
atomic clause of the contract — every sentence, bullet item, and table row outside fenced
blocks — is quoted verbatim by a `-- SKILL[kind]: "..."` trace line that sits next to the
Lean object modeling it. `tests/test_sshx_formal_model.py` splits the contract into those
clauses and holds coverage at 100%: an edit to any clause fails the suite until the model is
revisited. `SKILL.md` stays the contract; the model is evidence about it.

No module contains `sorry`, `axiom`, `native_decide`, or a proposition defined as bare `True`.

## Layers

| Layer | Modules | What it formalizes |
|---|---|---|
| Mechanics | `Sshx/*.lean` | verdict alphabets, carriers and fallback, flight accounting, the completion predicate, `BlockingAuthority` and downgrade, the three truth tables (exhaustive, order-sensitive), `pass_budget`, gate applicability and binding, isolation, records, stage order |
| Behavior | `Sshx/Behavior/*.lean` | the caller as an operational model: `ProtocolState`, one `Action` per caller act, one guard per "must" clause, `step`, `Reachable`, scoped implementation batches and evidence-backed handoffs, shared whole-candidate review readiness, and safety invariants over every reachable state |
| Reasoning | `Sshx/Reasoning/*.lean` | the reasoning logic every seat applies: reference frame, aesthetic verdict, seek truth from facts, mathematical applicability, prospective evidence, depth discipline, goal primacy, boundary checks, blocking authority in full, the six seats and the locus dyad, meta-judge convergence and the focused round, review downgrade, repair passes, termination seats and ownership routing |
| Semantics | `Sshx/Semantics/*.lean` | the contract's concepts as instances of the kernel-frozen theorems of [trureturing](https://github.com/the-omega-institute/trureturing) (`D5`, pinned by commit in `lakefile.toml`); each instance discharges the theorem's premises with `sshx` structures |
| Clauses | `Sshx/Clauses/*.lean` | the remaining definitional clauses: identity and trigger, goal contract records, protocol records, envelope, completion, context pollution, worker delegation mechanics, boundaries, baseline failure modes, verification |

## Trace kinds

`def` a definition or closed list; `guard` a precondition on a caller action; `inv` a
safety property over reachable states; `thm` a derived statement; `policy` a constant fixed
by protocol policy, not derived; `ref` a clause that restates or cross-references an object
defined elsewhere; `prose` an explanatory sentence with no norm of its own, which must carry a
`-- why:` line. The kind set is closed and checked.

## Semantic instances

| `sshx` concept | trureturing object | instance |
|---|---|---|
| register of advisory shapes; adversarial seat | qualified finite listing `g : A → A → Force`, twist `flip` (no fixed point), with verified theorem hypotheses and an admissible-input correspondence | `every_register_escaped`, `register_diagonal_unlisted`, `every_register_counted` via `escape_all_of_fixfree`, `escaped_card_of_fixfree`; the abstract theorem does not establish a real counterexample without that correspondence |
| written `GoalArtifact` vs user intent; goal gap | current concept `q`, target `T`, `defectRelation q T` | `goalGap` |
| blocking findings joined over passes; optional `pass_budget` | countable check language `Γ`, unit cost, `finiteBudgetEnvelope` for an explicit finite cap | `goal_gap_budget_envelope` via `budget_envelope_infimum_and_limit`: antitone in an explicit cap, never below the all-finite infimum of the same language; the default no-cap route remains gate-controlled |
| independent adjudication evidence; dependency closure | `AdmissionContext`, `AdmissibleJudge`, `AdaptiveUse` | `enlarging_closure_only_removes_admission` via `dependency_closure_admission_antitone`; `adaptive_use_is_inadmissible` |
| `revisions` append-only; earlier settlements untouched | `RoundRecord` ledger, `AppendOnly`, `settleAt` | `revision_keeps_old_settlement` via `append_only_old_settlement_unchanged`; `correction_is_append_only` |
| material comparison coordinates | `GainVector`, `ParetoWeak` | `candidate_dominance_is_preorder` via `pareto_weak_reflexive_transitive` |
| path-summed gain on additive coordinates | `gainDifference` | `additive_gain_is_path_independent` via `gain_difference_self_zero_and_cocycle` |
| retrospective fit is not prospective evidence | `CopyComparison`, `tableCopy`, `NonAnticipating` | `replayed_facts_carry_no_prospective_weight` via `lookup_copy_zero_loss_and_nonanticipating_failure` |
| sealed stop inputs; fail-closed cases; logs not inputs | `DecisionSet`, `OrientationSpec`, `AdjudicationStopTargetOnDecisionSet`, `stopCheck` | `terminationClaim`, `no_claim_without_candidate`, `no_claim_with_empty_feasible`, `no_claim_outside_feasible`, `claim_reads_only_sealed_inputs` |
| Pareto frontier and its linear extensions are not stop rules | two-action sourced models | `frontier_is_not_a_stop_rule` via `pareto_frontier_requires_sourced_orientation`; `linear_extension_is_not_a_stop_rule` via `op5_pareto_stop_linear_extension_equivalences_refuted` |

Declared non-instances, with the reason each premise does not match `sshx`:

- `spectrum_commitment_local_settlement`: a `Fin 5` roster with threshold `3`; the `sshx` rosters
  are `6`, `3`, and `3` with `unanimous`, `any reject`, and `unanimous` rules.
- Governance Fixed-Point Theory obligations G-A–G-H and Operational Tuition Theory T-A–T-H are
  registered as `open` in their source volumes, not kernel-frozen; the corresponding `sshx`
  clauses (a gate never gates its own exit; a stage record's verdict is a projection of
  `conclusion.verdict`; widen the absorber instead of adding a case) are modeled in the
  mechanics and reasoning layers only.

## Composed routes

`Sshx/Behavior/Scenarios.lean` proves each action's actual `allowed` guard along composed
initial multi-flight, repair, included-last-unit-review, carrier recovery/exhaustion,
tests-seat fallback, autonomous-intake, and single-flight traces. Effect assertions also
check partial/active/failed work and explicit-cap exhaustion. Approved-plan fixtures supply the prior thinking
settlement; these traces do not prove the truth of worker evidence or reviewer approval.

The no-cap route removes mandatory implementation assignment counts. Approved obligations
remain finite scope; each handoff carries new work/check evidence for the remaining gap.
The batch's `continuationJustified` and the direction gate's corresponding evidence projection
are interpreted worker/gate conclusions, not new runtime fields. Dispatch clears the handoff
projection; a terminal worker return supplies the next one. Unchanged handoffs and passes
are refused. Candidate readiness still requires all obligations/checks and settled flights.
`no_cap_handoff_prefix` composes actual `allowed` and `step` for every natural-number prefix
of justified handoffs with a check still pending; no large finite allowance replaces the
removed quota. It proves continuation admission, not that evidence is true, work eventually
finishes or a carrier can run forever.

`PassLimitEvidence` interprets a precommitted limit's authority, source, scope, applicability
and units from the existing GoalArtifact/harness. Cap recording refuses caller-only,
unsupported, out-of-scope or mismatched-unit evidence. Source truth and applicability remain
premises; the model does not authenticate provenance or parse English. Existing immutable
caps, pass debit and already-paid final review remain effective. An absent cap stays absent.
The formal `waitBoundary` action projects a host observation yield, not artifact polling or
a new scheduler/API. Every finite prefix leaves the active flight unchanged even with zero
retry capacity. Collection still requires launch and actual host completion notification;
ended malformed/failed attempts retain finite retry/fallback and honest abstention. Held-open
Codex and broker behavior tests verify that the existing synchronous runners publish no
terminal status during observations, then distinguish actual terminal success/failure.
Their watchdogs bound tests only, not production work.

Gate applicability is derived from one current continuation source. Intake records the
initial source; only an owner-authorized, source-supported append-only correction can
update it afterward. The correction evidence is a formal interpretation of the existing
revision, not a new runtime record or an English authority detector. The original target
and revision prefix remain intact. `TerminationEvidence` pairs the supplied roster with
its evaluated `ContinuationAuthority`, including the source's ledger position. The actual
evaluation guard requires that association to match current authority; its effect retains
the supplied association and applies the unchanged truth table to the roster. It does not
stamp old results with evaluation-time authority. A relevant correction preserves the old
verdict and snapshot, while both reevaluation and the claim guard refuse stale evidence,
even when applicability stays positive, the source payload repeats, or an earlier source
is restored. An unrelated note preserves usable evidence at both consumers.

General guard/effect and reachable-state proofs connect each affirmative claim to an
admitted evaluation whose supplied evidence still matches current authority. Guarded
regressions distinguish old A1 evidence refused after an authorized A2 correction from
separately supplied current A2 evidence accepted with the same verdicts. They also cover
repeated/restored sources, unchanged authority, and unsupported/unowned corrections.
The association and semantic truth of external evidence remain premises; this is no
provenance authentication scheme or new runtime record. There is no independent declaration
action.

Fallback uses the existing
`nextCarrier` selector against tried carriers projected from one assignment's flight history.
The original flight id is a ghost assignment link preserved by replacements, so distinct
approved assignments on the same target remain distinct. Every admitted fallback strictly
reduces that assignment's untried-carrier remainder. Draws, review dispatch and fallback
share the executable-carrier restriction for the tests seat.

The model's `launchDelegated` action projects the direct subagent invocation, the only
launch outside a runner. Codex and oracle flights require `launchViaRunner`, each through its
own closed-set runner (`runnerFor`), so a delegated oracle launch is refused. Both paths
require launch and host completion before collection. This adds no runtime interface, host capability, worker-record field or budget.
The seven stages and per-flight completion predicate are unchanged. Batch progress is a
formal projection of existing worker conclusions, not a runtime scheduler. Trigger-aware
downgrade and plan admission retain the existing contextual evidence chain.

The Codex identity adapter in `Clauses/Delegation.lean` pairs the protocol flight with
pending or bound runner identity. The protocol's zero-based `attempt` counts consumed
retries from transcript history; the adapter projects the public external `attempt`
separately. Pending retries omit identity options and remain at external attempt 1;
receipt binding leaves consumed retries and the fixed budget untouched. Bound retries
preserve identity and increment the external attempt. Both use `Behavior.collectEffect`
for accounting: each permitted retry strictly decreases remaining allowance, and
exhaustion abstains with either identity state. These are formal projections, not new
public record fields. Byte-level ID allocation, receipt
ordering/delivery, path isolation, and batch recovery are behavior-tested in the scripts,
not claimed as consequences of the abstract model.

## What the model cannot verify

The implementation/repair handoff in `Clauses/Contract.lean` projects the approved
outcome, brief completeness, sourced requirements and worker ownership. Relabelling
a caller recipe cannot make it binding; a compliant internal alternative alone does
not activate the existing change gates. Both initial and repair examples use the same
projection. Requirement provenance, compliance and brief completeness are interpreted
premises, not classifications proved by Lean. The fixture
`tests/fixtures/implementation_dispatch.md` (relative to the skill directory) exercises
actual brief generation and routing; generated behavior evidence stays outside source.
These definitions do not extend the runtime or replace the independent review gate.

The trace ties each clause to a Lean object, and the Lean kernel checks the object. Whether
the object *means* what the English says is a human correspondence judgment, kept reviewable
by placing each quote next to its object. A clause traced as `prose` carries no norm in the
model and is the first candidate for deletion from the contract.

## Build

```text
cd skills/sshx/formal && lake build
```

`lakefile.toml` pins `trureturing` by commit; Lake resolves it and Mathlib through it. A fresh
clone downloads Mathlib's build cache and compiles the imported `D5` modules. To reuse an
already built `trureturing` checkout instead, place symbolic links in the untracked
`.lake/packages/` directory before building: one named `trureturing` pointing at the checkout
whose `HEAD` is the pinned commit, and one per entry of that checkout's own
`.lake/packages/`. Lake verifies the revisions and reuses the existing build outputs without
touching the linked checkout.
