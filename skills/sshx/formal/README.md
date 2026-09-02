# sshx formal model

A Lean 4 model of the `sshx` prompt contract in [`../SKILL.md`](../SKILL.md), in two layers:

- **Mechanics** (`Sshx/*.lean`): verdict alphabets, carriers and fallback, flight accounting,
  `BlockingAuthority`, the three truth tables, `pass_budget`, gate applicability and binding,
  isolation, records, stage order. Core Lean, finite, decided by cases.
- **Semantics** (`Sshx/Semantics/*.lean`): the contract's concepts as instances of the
  kernel-frozen theorems of [trureturing](https://github.com/the-omega-institute/trureturing)
  (`D5`, pinned by commit in `lakefile.toml`). Each instance discharges the theorem's
  premises with `sshx` structures; nothing is used by analogy.

No module contains `sorry`, `axiom`, or `native_decide`.

## Semantic instances

| `sshx` concept | trureturing object | instance |
|---|---|---|
| register of advisory shapes; adversarial seat | listing `g : A → A → Force`, twist `flip` (no fixed point) | `every_register_escaped`, `register_diagonal_unlisted`, `every_register_counted` via `escape_all_of_fixfree`, `escaped_card_of_fixfree` |
| written `GoalArtifact` vs user intent; goal gap | current concept `q`, target `T`, `defectRelation q T` | `goalGap` |
| blocking findings joined over passes; `pass_budget` | countable check language `Γ`, unit cost, `finiteBudgetEnvelope` | `goal_gap_budget_envelope` via `budget_envelope_infimum_and_limit`: antitone in budget, never below the all-finite infimum of the same language |
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
  mechanics layer only.

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

## Trace

Each module carries `-- SKILL: "..."` lines quoting the contract verbatim next to the
definition or theorem that models the quote. `tests/test_sshx_formal_model.py` asserts every
quote is still a substring of `SKILL.md`, that every truth-table row is traced in
`Sshx/Tables.lean`, and that the package builds when `lake` is installed. An edit to a modeled
clause therefore fails the suite until the model is revisited. `SKILL.md` stays the contract;
the model is evidence about it.

| Mechanics module | Contract section |
|---|---|
| `Verdicts` | verdict alphabets of the thinking, review, and termination seats |
| `Carrier` | `WorkerMode` priority, fallback selection, bounded fallback chain |
| `Flight` | flight status, same-carrier retry accounting, the completion predicate |
| `Blocking` | `BlockingAuthority`, `ThreatEligibility`, review downgrade |
| `Tables` | design, review, and termination truth tables; exhaustiveness and order |
| `Budget` | `pass_budget` decrement and termination |
| `Gate` | termination-gate applicability, binding, sealed inputs, diversity claim |
| `Isolation` | same-round invisibility and the append-only role ledger |
| `Records` | `GoalArtifact`, `harness`, `revisions`, envelope, stage-record mirror |
| `Protocol` | stage order and the `abstain` exit at `choose_worker_mode` |
