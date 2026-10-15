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
| Behavior | `Sshx/Behavior/*.lean` | the caller as an operational model: `ProtocolState`, one `Action` per caller act, one guard per "must" clause, `step`, `Reachable`, finite implementation batches, shared whole-candidate review readiness, and safety invariants over every reachable state |
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
check partial/active/failed/exhausted work. Approved-plan fixtures supply the prior thinking
settlement; these traces do not prove the truth of worker evidence or reviewer approval.

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

The carrier universe has four entries and the fallback bound follows its length.
`nextCarrier_heca_absent` and `fallback_heca_absent` compare the new selector/chain
with the retained pre-heca functions for every eligibility/tried table. Composition
is parameterized by the recorded heca detection: `composition_heca_absent` proves
exact equality with the old layout for every seat count; detected protocol rosters
give every seat in the Codex remainder to heca, including the executable tests
seat, and single-worker stages also use heca. Feasible draws are proved for thinking,
review, termination, and implementation/repair; detected initial batches are empty. The once-per-run heca check records even a
negative tool-exposure result before mode resolution and cannot be repeated. Detection
reads only the caller's exposed heca MCP agent toolset, including tools listed for lazy
loading; it invokes nothing and has no CLI/daemon/provider probe or outside-heca route.
For heca, `Observation.carrierExited` means terminal/not-running evidence from
the collection read. A finish notice authorizes one read even when it lacks the
turn-end transition: `step` records that failed collection, while `collectReadEffect`
clears result/completion references and keeps the attempt active, launched, and at
its current retry count. After recording either archive outcome, `recordStoppedHecaFailure` consumes
same-agent status confirmation that the agent is not running, preserves the recorded
archive outcome (including a reported failure), then applies the existing
`collectEffect` with a failed observation. The separate no-notice stop route is
unchanged. Neither route supplies completion evidence.
The same finite retry budget then permits a fresh launch or exhaustion to abstention
and the unchanged fallback selector. Unconfirmed/running or other-agent stop evidence
cannot release the target; retrying flights remain protected. These interpreted
evidence premises do not authenticate status or prove host tool behavior.
The MCP dispatch fixes the whole task through one spawn prompt: an inline whole
brief or one caller-kept, readable, unchanged, peer-output-free whole-brief file,
with the reply contract always in the prompt. Provider/model overrides pass through;
the default provider is heca’s codex when reported enabled, otherwise the caller’s
provider, and an omitted model uses the provider default. Placement mismatch in
either cwd or worktree fails closed. The newest-two collection projection refuses
a missing matching notice or a caller still in the dispatch turn, including a
blocking wait, sleep or poll loop; the caller must end/yield and collect on a later
turn. The flight remains active and its existing target guard remains in force.
The ownership projection checks only own-agent scope for stop/archive operations;
it admits dispatch/collection and forbids every other heca lifecycle or orchestration
operation. Production `Behavior.Model` refines that projection with collection and
heca-carrier requirements, through `Action`, `allowed`, `step`, and `Reachable`. Its
archive guard refuses the matching-notice-to-collection window, another agent, and
non-heca flights. The model implements the archive duty as a settlement gate before
relaunch, implementation recording, stage advance, termination evaluation, and claim —
a stricter ordering than the `SKILL.md` sentence. A failed archive remains explicitly
reported as unresolved housekeeping; recording that outcome satisfies the reporting
gate without proving cleanup success. This is a safety/duty property, not a liveness
or eventual-success guarantee for an interrupted caller. The confirmed no-notice stop
archive already discharges the duty and cannot be repeated. Both successful and failed
collections admit the subsequent archive. `archive_preserves_protocol` preserves
flights (including completion, references, retry allowance and target protection),
batch, pass budget, mutation log, and claim state; housekeeping changes and gates
later actions. `archive_operation_refines_production_guard` in
`Clauses/Delegation.lean` proves production-allowed implies ownership-projection-allowed.
No model claims a notification is completion or that activity process detail is a
conclusion. Composed heca-first implementation and stop-without-notice recovery traces
check dispatch, fresh retry launch, failure record, fallback, collection and readiness
for absent or finite pass caps, including zero. The notified unreadable-read trace
checks failed collection, either archive outcome, confirmed stop, and retry or exhausted
fallback, and refuses archive in the notice window or retry without confirmation.
The success traces refuse recording or advance before archive/reporting, admit both
archive outcomes before recording and advance, and refuse a repeated archive after
success or a no-notice stop. Failed terminal collection pins the fallback settlement
gate. A newly collected termination flight pins evaluation and claim settlement after
stage advance. Batch readiness tracks implementation completion; housekeeping gates
remain at relaunch, recording, advance, fallback, evaluation, and claim. Runner launch
has no heca settlement check because its carriers cannot be heca. Recovery preserves
the pass cap.
Unconfirmed/running or other-agent evidence cannot release the target.

Real-tool probes, the no-skill baseline, dated invocation evidence, and the
ASSUMED-UNVERIFIED register have one home at the top of
[`test_sshx_contract.py`](../tests/test_sshx_contract.py). That record owns its evidence
scope and cutoffs. There is no mechanical heca runner or replacement behavior harness.

Fallback uses the existing
`nextCarrier` selector against tried carriers projected from one assignment's flight history.
The original flight id is a ghost assignment link preserved by replacements, so distinct
approved assignments on the same target remain distinct. Every admitted fallback strictly
reduces that assignment's untried-carrier remainder. Draws, review dispatch and fallback
share the executable-carrier restriction for the tests seat.

The model's `launchDelegated` action projects direct heca MCP or isolated-subagent
invocation, with no runner receipt, minted identity or attempt path. Codex and oracle
require `launchViaRunner` through their closed-set runner (`runnerFor`); runner launches
of heca and delegated oracle launches are refused. Both paths require launch and a host
notification before one-shot result collection; a confirmed own-agent stop can instead
record failure without collecting a result. A notification grants collection authority,
not completion. This adds no runtime interface, host capability, worker-record field or budget.
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
