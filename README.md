# consensus-rnd

English canonical public documentation. [Chinese companion](./README.zh-CN.md).

`consensus-rnd` publishes Agent Skills for Claude Code, Codex, Cursor, and Gemini. Its `sshx` skill brings isolated worker perspectives to a high-risk decision or implementation task, converges on a concrete plan, and checks the result through independent review. The deliverable is a prompt contract and its supporting files, shared across platform manifests.

Use it when product, architecture, correctness, safety, or cost risks justify separate perspectives. Routine one-step answers whose outcome would not change do not justify the protocol's cost. See the [trigger and exclusions](./skills/sshx/SKILL.md#trigger).

## What It Provides

| skill | What it is for | Runtime shape |
|---|---|---|
| `sshx` | Worker-delegated inline consensus for a decision, plan, or finished change. | Prompt contract with worker runners; no skill-owned daemon, GitHub, git, label, release, or lifecycle orchestration authority; optional heca MCP delegation inside heca. |

## Core

The engine is not "run the same prompt several times and vote." It is biased, independent, multi-angle convergence:

- **Biased independent perspectives**: thinking and review workers start from different priors and must not read same-round peer output before sealing their own verdicts.
- **Meta-judge convergence**: disagreement is reduced into fixed exits: consensus, a compatible concrete plan, or a bounded stall that is surfaced honestly.
- **Concrete plans pass the gate**: an obvious direction still needs independent validation before implementation.
- **Symmetric verification**: finished work passes independent reviewers and the fixed review truth table before it is declared done.
- **Pure orchestration**: the caller dispatches workers and aggregates bounded conclusions; implementation, verification, repair, and design solving remain delegated.

## Protocol at a Glance

The [protocol](./skills/sshx/SKILL.md#inlineconsensusprotocol) has seven ordered stages. The caller handles intake, dispatch, convergence, and reporting; workers own thinking, implementation, and review.

| Stage | Purpose |
|---|---|
| `intake` | Record the user's goal, constraints, success criteria, and host capabilities. |
| `choose_worker_mode` | Check available worker carriers before dispatch. |
| `thinking_panel_workers` | Test the plan through six isolated perspectives. |
| `meta_judge` | Converge conclusions using the [thinking truth table](./skills/sshx/SKILL.md#design-truth-table). |
| `implementation_worker` | Delegate the approved outcomes and scope. |
| `review_triplet_workers` | Review the completed candidate independently. |
| `fix_or_done` | Route repairs, further review, or completion under the existing gates. |

The [thinking panel](./skills/sshx/SKILL.md#thinking-panel) assigns a distinct objective to each seat:

| Thinking seat | Lens |
|---|---|
| `teleology` | Purpose and the form it requires. |
| `parsimony` | How much mechanism is necessary. |
| `fidelity` | Source-verified premises and representative evidence. |
| `natural-ownership` | The layer that naturally owns the duty. |
| `proportional-containment` | The justified scope, authority, and duration. |
| `worth` | Whether the benefit warrants the cost. |

The [review triplet](./skills/sshx/SKILL.md#review-triplet) applies the [review truth table](./skills/sshx/SKILL.md#review-truth-table):

| Review seat | Focus |
|---|---|
| `architecture` | Boundaries, contracts, coupling, and maintainability. |
| `quality` | Behavior, edge cases, failures, and user impact. |
| `tests` | Coverage, determinism, and verification strength. |

A [termination gate](./skills/sshx/SKILL.md#termination-gate) applies inside the final stage only when the host has positively declared a goal-driven continuation mechanism in the [goal contract](./skills/sshx/SKILL.md#goal-contract), and an affirmative satisfaction claim is being considered. It is not an eighth stage and grants no control over the host mechanism.

## Risks and Limits

- **Broad execution access:** the Codex runner defaults to `danger-full-access`. Read the [invocation contract](./skills/sshx/CODEX_WORKER_SPEC.md#invocation) and [threat model](./skills/sshx/CODEX_WORKER_SPEC.md#threat-model) before supplying a work target.
- **Heca execution:** seats run unattended with full permissions; heca owns their agents, so host job time limits and teardown do not reach them. Finished agents remain host housekeeping, and support is limited to recorded evidence. See [worker delegation](./skills/sshx/SKILL.md#worker-delegation).
- **Cost and duration:** a full initial run dispatches at least 10 worker turns, each potentially making multiple model calls: six thinking seats, one implementation worker, and three reviewers. Repair, focused, repeated review, and applicable termination passes add work. There is **no `pass_budget` cap by default**; see the [protocol](./skills/sshx/SKILL.md#inlineconsensusprotocol), [panel](./skills/sshx/SKILL.md#thinking-panel), [review](./skills/sshx/SKILL.md#review-triplet), and [pass accounting](./skills/sshx/SKILL.md#fix-or-done).
- **External content transfer:** the oracle seat sends the inlined brief through NyxID to an external ChatGPT Pro account. Consider what the brief contains before dispatch; see [worker delegation](./skills/sshx/SKILL.md#worker-delegation) and the oracle [request contract](./skills/sshx/ORACLE_WORKER_SPEC.md#request).
- **Artifact retention:** runner briefs, logs, and results accumulate under `${SSHX_HOME:-$HOME/.sshx}/<flight-id>/attempt-<n>` until cleaned or pruned. See the [run directory](./skills/sshx/CODEX_WORKER_SPEC.md#run-directory) and [inactive-flight sweep](./skills/sshx/CODEX_WORKER_SPEC.md#inactive-flight-sweep); a short retention window can remove a still-running silent flight.
- **Host responsibility:** runner time limits and teardown of the whole job tree belong to the host harness. Teardown behavior for Codex, Cursor, and Gemini hosts is unverified in the [teardown specification](./skills/sshx/CODEX_WORKER_SPEC.md#teardown-prerequisite); also see [delegation](./skills/sshx/SKILL.md#worker-delegation).
- **Changing contract:** this is a prerelease, as indicated by the [package metadata](./package.json). The contract may change; check the [skill](./skills/sshx/SKILL.md) and [contribution guidance](./CONTRIBUTING.md) when updating.
- **Untrusted output:** consensus remains AI output requiring independent verification ([repository invariant FI-001](./CLAUDE.md#共识研发不动点)). Context isolation does not prove independent priors or better results ([delegation limits](./skills/sshx/SKILL.md#worker-delegation)). The [Lean model](./skills/sshx/formal/README.md#what-the-model-cannot-verify) checks formal objects, not the truth of evidence or compliance of real runs; correspondence with the prose remains a human judgment.

## Quick Start

### Prerequisites and carriers

The host must provide background execution with completion notification, time limits, and whole-job teardown for runner dispatch. [Worker delegation](./skills/sshx/SKILL.md#worker-delegation) owns capability checks, seat assignment, bounded retries, and fallback.

| Carrier | Prerequisites and role |
|---|---|
| `codex-cli` | Bash, `jq`, and an executable, usable Codex CLI; a writable run root and supplied work target. Initial seats outside heca and fallback seats inside heca use the [Codex runner](./skills/sshx/CODEX_WORKER_SPEC.md#carrier). |
| `heca` | Run sshx inside heca with heca's MCP agent tools exposed to the session and an existing placement matching the work target. Takes all initial Codex seats when detected, including implementation and repair. See [Using Heca Workers](#using-heca-workers) and [worker delegation](./skills/sshx/SKILL.md#worker-delegation). |
| `nyxid-oracle` | Python 3, Bash and `jq` for path projection, and a usable NyxID CLI with an active broker pool containing an online worker. The [oracle runner](./skills/sshx/ORACLE_WORKER_SPEC.md#invocation) owns dispatch; see [pool selection](./skills/sshx/ORACLE_WORKER_SPEC.md#pool-choice) and [login diagnostics](./skills/sshx/ORACLE_WORKER_SPEC.md#broker-diagnostics). |
| `isolated-token-subagent` | A host subagent facility with isolated token context; same-round peers cannot read one another's outputs before returning their verdicts. |
| `abstain` | Stop honestly when no carrier is available or a required flight exhausts retries and fallback; this is an outcome, not an execution carrier. |

Every multi-seat stage initially assigns one seat to the isolated subagent, one to the oracle, and the rest to heca when detected, otherwise Codex CLI, subject to seat constraints and fallback. The tests reviewer needs a carrier that can execute verification in the work target. Carrier assignment and isolation do not by themselves establish model diversity; see the [owning policy](./skills/sshx/SKILL.md#worker-delegation).

#### Using Heca Workers

`sshx` supports `heca` alongside `codex-cli` and `nyxid-oracle` as a worker carrier. When its non-mutating capability check detects heca, it takes every seat otherwise assigned to Codex CLI: the remainder of each multi-seat stage and every single-worker stage, including implementation and repair. When heca is absent, existing carrier composition stays unchanged. The fallback priority stays unchanged, so Codex CLI remains reachable inside heca.

The heca option is offered only when sshx runs inside heca and the caller's session exposes heca's MCP agent tools. Seats use those tools directly. You or the host may select a provider and model per seat; by default, seats use heca's `codex` provider when reported enabled, otherwise the caller's own heca provider, with heca's default model. The work target must have a matching existing heca placement. [Worker delegation](./skills/sshx/SKILL.md#worker-delegation) owns the contract and its evidence limits.

### Claude Code

Install the marketplace and plugin, then invoke the skill with your task:

```text
/plugin marketplace add ChronoAIProject/consensus-rnd
/plugin install consensus-rnd@consensus-rnd
/consensus-rnd:sshx <task>
```

The plugin identity is declared in the [marketplace](./.claude-plugin/marketplace.json) and [plugin manifest](./.claude-plugin/plugin.json).

### Codex / Cursor

The [Codex](./.codex-plugin/plugin.json) and [Cursor](./.cursor-plugin/plugin.json) manifests both declare `"skills": "./skills/"`. Use the platform's plugin support to load the repository and read the [skill contract](./skills/sshx/SKILL.md); installation and invocation details depend on the host.

### Gemini CLI

The [extension manifest](./gemini-extension.json) selects `GEMINI.md` as its context file. [GEMINI.md](./GEMINI.md) lists the available skill and directs the agent to read its contract on demand.

## Architecture

| Surface | Owning source |
|---|---|
| Public identity and introduction | This README and its [Chinese companion](./README.zh-CN.md). |
| Repository rules and structure | [CLAUDE.md](./CLAUDE.md), also exposed through the tracked `AGENTS.md` symlink. |
| Protocol and behavioral contract | [SKILL.md](./skills/sshx/SKILL.md). |
| Codex execution and artifact maintenance | [CODEX_WORKER_SPEC.md](./skills/sshx/CODEX_WORKER_SPEC.md) and [scripts](./skills/sshx/scripts/). |
| Heca MCP delegation | [SKILL.md](./skills/sshx/SKILL.md#worker-delegation). |
| NyxID broker dispatch | [ORACLE_WORKER_SPEC.md](./skills/sshx/ORACLE_WORKER_SPEC.md). |
| Formal evidence and its limits | [Formal model README](./skills/sshx/formal/README.md). |
| Platform entrypoints | [Claude marketplace](./.claude-plugin/marketplace.json), [Claude plugin](./.claude-plugin/plugin.json), [Codex](./.codex-plugin/plugin.json), [Cursor](./.cursor-plugin/plugin.json), and [Gemini](./gemini-extension.json). |
| Manifest version mapping | [.version-bump.json](./.version-bump.json) and [bump_version.py](./.github/scripts/bump_version.py). |
| Repository verification | [CI workflow](./.github/workflows/consensus-rnd-ci.yml), [repository tests](./.github/tests/test_repository_docs.py), and [skill tests](./skills/sshx/tests/). |

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for development prerequisites, local checks, and maintainer-owned release steps. Repository maintenance guidance is in Chinese.

## License

[MIT](./LICENSE) © ChronoAIProject
