# sshx formal model

A Lean 4 model of the `sshx` prompt contract in [`../SKILL.md`](../SKILL.md). It is
core Lean only (no Mathlib), builds in seconds, and contains no `sorry`, `axiom`, or
`native_decide`.

The model exists so that every normative clause of the contract must map to a
definition or a theorem. A clause that maps to nothing, that is provable from the
others, or that enumerates the complement of a closed definition is an optimization
candidate for the contract, not for the model.

## Build

```text
cd skills/sshx/formal && lake build
```

`lean-toolchain` pins the toolchain; `elan` installs it on first use.

## Trace

Each module carries `-- SKILL: "..."` lines quoting the contract verbatim next to the
definition that models the quote. `tests/test_sshx_formal_model.py` asserts every quote
is still a substring of `SKILL.md`, so an edit to the contract that touches a modeled
clause fails the suite until the model is revisited. The model is not a second source
of truth: `SKILL.md` stays the contract, and the model is evidence about it.

| Module | Contract section |
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
