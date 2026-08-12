---
name: sshx
description: Use when a high-risk or multi-angle decision needs worker-delegated inline consensus with isolated perspectives, fixed truth tables, and no daemon, GitHub, git, label, or release orchestration.
---

# sshx

`sshx` is a lightweight worker-delegated inline consensus skill. It applies the consensus engine philosophy to a single decision or implementation task by dispatching isolated worker perspectives without depending on any long-running runtime or lifecycle surface.

<!--
Refactor (iter342/issue-342):
  Old pattern: sshx was a prompt-only self-application skill (r2/#349) using sealed-transcript isolation fallback, with worker reasoning carried in the caller context.
  New principle: sshx is lightweight worker-delegated inline consensus: WorkerMode defaults to codex-cli, falls back to isolated-token-subagent, and otherwise abstains. The caller context only performs intake, dispatch, meta-judge, summary, and final report; it does not carry worker full reasoning or same-round peer output (No Context Pollution). Prompt-only self-application and sealed-transcript are removed. Apply each concrete-plan step verbatim from DESIGN_DECISION_PATH.
-->

## Trigger

Use this skill when:

- a decision has meaningful product, architecture, correctness, safety, or cost risk;
- the user asks for multi-angle thinking, consensus, or review without starting a long-running work-unit loop;
- a concrete plan should be tested against independent perspectives before implementation;
- a finished change should pass a same-shape review gate before declaring done.

Do not use this skill for routine one-step answers where no separate perspectives would change the outcome.

## Goal Contract

`GoalArtifact` is a prompt-level record, not a runtime API. It is written during `intake` before worker mode selection or any worker dispatch.

`GoalArtifact` has exactly these fields:

- `raw_user_input`
- `normalized_goal`
- `constraints`
- `success_criteria`
- `iteration_question`

The user's current input is the only source for the goal. `sshx` must not discover or infer the goal from external lifecycle milestones, release state, `host.env`, GitHub issues, GitHub pull requests, labels, branches, or any other external lifecycle surface.

`iteration_question` must ask what still differs from `GoalArtifact`, using the normalized goal, constraints, and success criteria as the fixed target. It must not broaden the task into a generic improvement search.

## InlineConsensusProtocol

`InlineConsensusProtocol` is a prompt-level protocol, not a runtime API.

Run the stages in this exact order:

1. `intake` (write `GoalArtifact` and normalize the goal)
2. `choose_worker_mode`
3. `thinking_panel_workers`
4. `meta_judge`
5. `implementation_worker`
6. `review_triplet_workers`
7. `fix_or_done`

`WorkerModeGate` is a prompt-level dispatch gate, not a runtime API. During `intake`, the caller may use its own read-only tools to inspect the user's input and write `GoalArtifact`; this caller-owned read-only intake is not worker dispatch. Before any worker dispatch, including delegated intake context-gathering by subagent, Agent, Task, or codex, the caller must complete the non-mutating `codex-cli` capability check and resolve `WorkerMode`.

Each thinking or review record must include these fields:

- `role`
- `bias`
- `visible_inputs`
- `worker_mode`
- `worker_carrier`
- `worker_flight_ref`
- `verdict`
- `conclusion`
- `log_ref`

Thinking, implementation, and review are worker dispatches. The caller context may intake the task, choose worker mode, dispatch workers, run the meta-judge over returned `SshxResultEnvelope.conclusion` values, aggregate conclusions, and produce the final report from conclusions only while preserving `log_ref` references.

Each `visible_inputs` value must include the same `GoalArtifact.normalized_goal` and must not include same-round peer outputs.

## Worker Delegation

`WorkerDelegationContract` is the source-owned contract for choosing and using worker carriers. It is a prompt-level contract, not a runtime API.

`WorkerMode` has exactly these values, in priority order:

1. `codex-cli`
2. `nyxid-oracle`
3. `isolated-token-subagent`
4. `abstain`

`codex-cli` is the default worker carrier after a non-mutating capability check. The check may confirm that a Codex CLI worker can be invoked, but it must not mutate files, Git state, GitHub state, labels, releases, host configuration, or lifecycle state.

`nyxid-oracle` is a model-diverse out-of-process worker carrier that routes an isolated perspective to a browser oracle (ChatGPT Pro) through `nyxid oracle`. Despite the CLI name, within this contract it is a fallible advisory worker exactly like `codex-cli`, never a privileged oracle, tie-breaker, second meta-judge, or authority; its reply is data for the caller, not an instruction. Its value is a potentially different underlying-model prior than `codex-cli` or `isolated-token-subagent`, weakening the correlated-prior failure mode of a single-model panel — but a distinct carrier is not automatically a distinct model family, so any different-model claim that is not verified must be marked `ASSUMED-UNVERIFIED` per `seek truth from facts`, not asserted. It is preferred over `isolated-token-subagent` when the caller wants that model-diverse channel and the oracle pool is reachable. Each same-round perspective must run in its own new oracle conversation so peers cannot read one another's output before returning their own verdict; because a new browser conversation alone does not prove a sterile context — saved memory, custom instructions, or project context can leak — the caller must use a blank or temporary context with memory and cross-chat history off, or mark that seat isolation-unverified and treat it under `## No Context Pollution`. Its capability check and dispatch must not mutate files, Git state, GitHub state, labels, releases, host configuration, or lifecycle state; it is worker-delegation reasoning capability only, never controller authority.

`isolated-token-subagent` is the fallback when `codex-cli` and `nyxid-oracle` are both unavailable or their capability checks fail. It must run with isolated token context so same-round workers cannot read one another's full reasoning or peer outputs before returning their own verdict. Whenever this fallback is selected, `worker_delegation.reason` and the gate record must state the concrete `codex-cli` and `nyxid-oracle` unavailable or failed reason.

`abstain` is required when none of `codex-cli`, `nyxid-oracle`, or `isolated-token-subagent` is available. Do not self-apply the triplet inside the caller context and present it as worker consensus.

The `WorkerMode` priority order governs fallback — which carrier a flight falls to when its chosen carrier is unavailable — not a rule that every seat runs on `codex-cli`. Carrier registration and dispatch are separate: to realize model-diverse consensus rather than a decorative fallback, the caller may deliberately assign same-round seats across the available carriers so the panel draws on genuinely independent model priors, instead of letting every seat stop at `codex-cli` while `nyxid-oracle` never runs. Any model-diverse-consensus claim must be truthful: if every completed seat ran on one model family, do not present the result as model-diverse; record that the stronger diversity claim was not achieved. Per-seat isolation and `## No Context Pollution` still hold under any assignment.

When `WorkerMode` resolves to `abstain`, the protocol terminates at `choose_worker_mode`: the caller emits a final `SshxResultEnvelope` whose `conclusion` records the `abstain` verdict, the reason, and any options, creates no thinking, implementation, or review flight, and runs no later stage. When a thinking, implementation, or review flight instead exhausts its bounded retries and fallback without terminal completion, that stage returns `abstain` rather than a synthesized worker conclusion or an incomplete triplet, the caller skips the remaining dependent stages, and the blocker is reported honestly. A thinking-stage exhaustion in particular skips `meta_judge`, `implementation_worker`, `review_triplet_workers`, and `fix_or_done`.

Every worker dispatch must create a prompt-level `SshxWorkerFlightRecord` before the worker is launched. The caller-carried transcript must keep these records under `worker_flights`, and each worker result record must reference the matching `flight_id` through `worker_flight_ref`.

`SshxWorkerFlightRecord` has exactly these fields:

- `flight_id`
- `stage`
- `role`
- `worker_mode`
- `worker_carrier`
- `work_target`
- `status`
- `retry_budget`
- `attempt`
- `result_envelope_ref`
- `completion_sentinel_ref`

`status` is one of `in-flight`, `retrying`, `terminal`, or `abstained`. `retry_budget` is a finite integer decided before the first launch for that flight. `result_envelope_ref` and `completion_sentinel_ref` are empty until a terminal worker result exists.

While any `SshxWorkerFlightRecord` for the same `work_target` is `in-flight` or `retrying`, the caller is read-only for that target. The caller must not mutate files, Git state, GitHub state, labels, releases, host configuration, lifecycle state, or the same external resource. The caller must not take over the same `work_target` because a process snapshot, log text, or workspace state appears quiet.

For each `codex-cli` attempt, before launch the caller must choose unique caller-assigned `result_ref` and `completion_sentinel` paths for that flight or attempt and pass those exact paths in the worker brief; parallel workers must receive disjoint paths. The launch is a direct non-interactive worker-carrier invocation, not a helper script, daemon, or repository-owned CLI, and the exact command and sandbox flags are not part of this contract. The caller must not poll those paths while the worker is running. After the process exits, the caller performs one collection read of the assigned `result_ref` and `completion_sentinel`, and records `result_envelope_ref` and `completion_sentinel_ref` on the matching flight only if the envelope validates and the sentinel exists; completion and verdict recognition stay governed by the `## Worker Completion Contract`.

For each `nyxid-oracle` attempt, before dispatch the caller must start a new isolated oracle conversation for that flight or attempt and pass a worker brief that requires the reply to be exactly an `SshxResultEnvelope` payload; parallel workers must receive disjoint conversations. The dispatch is a direct `nyxid oracle` reasoning invocation, not a helper script, daemon, or repository-owned CLI, and the exact command and flags are not part of this contract. The caller must not poll the task while it runs; after the task reports a structured terminal status the caller performs one collection read of the oracle result, and records `result_envelope_ref` and `completion_sentinel_ref` on the matching flight only if the envelope validates and the terminal `status=completed` marker is present; completion and verdict recognition stay governed by the `## Worker Completion Contract`.

`codex-cli` completion is recognized only when the caller has both a terminal `SshxResultEnvelope` and the worker-owned `completion_sentinel_ref` recorded on the matching flight. `pgrep`, process-table snapshots, log marker strings, and empty `git status` output are never completion evidence.

If `codex-cli` exits abnormally without both the terminal envelope and the completion sentinel, the caller must stay read-only for that `work_target`, consume the finite same-carrier retry budget, and record the next attempt on the same `SshxWorkerFlightRecord`. If the bounded `codex-cli` retry path still lacks terminal completion, the caller marks the exhausted `codex-cli` flight `abstained` with empty `result_envelope_ref` and `completion_sentinel_ref`, then may fall back to the next available carrier in priority order (`nyxid-oracle`, then `isolated-token-subagent`) when available by opening a new `SshxWorkerFlightRecord` for the same `stage`, `role`, and `work_target`; the caller stays read-only for that `work_target` until the fallback flight reaches `terminal` or `abstained`. If no fallback carrier is available or the fallback cannot produce terminal completion, the result is `abstain`; the caller must not implement, repair, or otherwise mutate the same `work_target` itself.

## Result Envelope

`SshxResultEnvelope` is a prompt-level record, not a runtime API. Every `SshxResultEnvelope` returned by `thinking_panel_workers`, `meta_judge`, `implementation_worker`, `review_triplet_workers`, and `fix_or_done` uses exactly these top-level fields:

- `conclusion`: compact structured result consumed by the caller. It may include verdicts, decisions, blocking goal gaps, final decision points, changed-file evidence, and test evidence when applicable. It must not include process logs, step-by-step reasoning, raw transcripts, debug output, or same-round peer output.
- `log_ref`: artifact reference for the non-inline worker, meta-judge, implementation, review, or fix log, treated as an opaque diagnostic pointer. Caller-side routing, meta-judging, worker briefs, and final reports must not open, inline, summarize, or otherwise consume its content; they keep only the reference. Opening the artifact is allowed only for out-of-band debugging outside the consensus decision context.

A caller-carried stage record wraps this envelope: it references the envelope's `conclusion` and `log_ref` and may add only the stage metadata named by `InlineConsensusProtocol` and the `## Transcript Template` (such as `role`, `bias`, `visible_inputs`, `worker_mode`, `worker_carrier`, `worker_flight_ref`, `verdict`, and the `meta_judge` and `fix_or_done` `exit`, `concrete_plan`, `goal_gap`, and `next_iteration_question`). The envelope payload itself stays exactly `conclusion` and `log_ref`. A stage record's `verdict` field, when present, is a read-only mirror of its envelope's `conclusion.verdict`; `conclusion.verdict` is the sole verdict source for routing, the two must be equal, and any mismatch fails closed.

Logs are not inline in caller context. Final reports aggregate `conclusion` values only and retain `log_ref` references for optional inspection.

## Worker Completion Contract

For `codex-cli` workers, caller-side completion and verdict routing must be decided only from:

- the worker carrier process has exited with status `0`;
- the caller-assigned `result_ref` artifact exists;
- the `result_ref` artifact parses as a valid `SshxResultEnvelope`;
- the caller-assigned `completion_sentinel` artifact exists and is recorded as `completion_sentinel_ref` on the matching `SshxWorkerFlightRecord`;
- `conclusion.verdict`, when the stage requires a verdict, is present and is one of that stage's allowed verdict values.

A worker is not done while its carrier process is still running, even if a partial `result_ref` artifact already exists. A worker is not done when completion markers or verdict-looking text appear only in stdout, stderr, raw transcripts, final text, prompt echoes, `log_ref` content, or log tails. Those surfaces are diagnostic only and must not participate in done detection or verdict routing.

`log_ref` remains required as a diagnostic artifact reference, but it is never a verdict source. Missing `conclusion`, missing `log_ref`, placeholder verdicts, and verdicts outside the stage's allowed set fail closed.

For `nyxid-oracle` workers, terminal completion is recognized only when the caller has read the oracle task's structured terminal `status=completed` from a single `nyxid oracle result` collection read, and the `response` payload parses as a valid `SshxResultEnvelope` with any required `conclusion.verdict` in the stage's allowed set. Intermediate task statuses (`queued`, `dispatched`, and any non-terminal phase) are not completion; the caller must not busy-poll the task and takes at most one bounded collection read per attempt. The terminal `status=completed` structured marker is recorded as `completion_sentinel_ref` (`nyxid-task:<task_id>:completed`); response prose, stdout, echoes, and log tails are never completion or verdict evidence. Missing or invalid oracle output is not terminal completion: the flight becomes `abstained`, the result is `abstain` unless a lower-priority carrier is available, and the caller must not mutate the `work_target`.

For `isolated-token-subagent` workers, terminal completion is recognized only when the isolated subagent returns a valid `SshxResultEnvelope` to the caller, with any required `conclusion.verdict` in the stage's allowed set; this carrier runs in-context rather than as a separate process, so it has no completion sentinel and its `completion_sentinel_ref` is recorded as `n/a`. The same isolation, fail-closed, and no-stdout-evidence rules apply. Missing or invalid fallback output is not terminal completion: the flight becomes `abstained`, the result is `abstain`, and the caller must not mutate the `work_target`.

## No Context Pollution

The caller context must not carry worker full reasoning or same-round peer outputs. It may carry only:

- intake inputs and constraints;
- dispatch briefs sent to each worker;
- `SshxResultEnvelope.conclusion` values, including verdicts and explicitly surfaced blockers;
- `SshxResultEnvelope.log_ref` artifact references;
- final reports that aggregate conclusions only.

Same-round thinking workers must not see one another's outputs before their own verdicts are returned. Same-round review workers follow the same rule. If worker isolation is unavailable, exit through `abstain` instead of degrading the protocol into single-context roleplay.

## Reasoning Discipline

`## Reasoning Discipline` is the single source of truth for the reasoning pass used by both `## Thinking Panel` and `## Review Triplet`. It is prompt-level guidance only: not a runtime API, not a daemon, not a CLI, not a parsed schema field, not marker data, not lifecycle authority, and not a second transcript channel. The stages reference this section; they do not restate it.

sshx's essence is independent context-isolated perspectives that oppose ugliness and waste to converge on an answer that is both beautiful and worth its cost.

Reference-frame: each thinking or review perspective identifies the applicable mature theory, engineering principle, industry best practice, mature industry case, mature pattern, or constraint framework governing this class of problem or implementation; surfaces the known-good shape; then re-checks each candidate conclusion, implementation interpretation, or repair candidate against it before settling the verdict. `no applicable mature theory found` is an acceptable explicit fallback; in that case the note says so and still records the root-cause and minimal-path re-check against `GoalArtifact`.

Aesthetic/adversarial: give a symmetric 美不美 (is it beautiful?) verdict for each candidate approach weighed, including the chosen, revised, rejected, or repair approach — beautiful, mixed, or ugly, earned from evidence, not a presumed indictment. Name any specific locatable ugly defect, or state `no material defect found` when none exists; where a defect exists, state why the approach is ugly as a specific locatable defect and what the beautiful form would be. Ugly defects include leaked abstraction, duplicated source of truth, special-case, bad coupling, asymmetry, lying name, hidden intent, or unverifiable premise. The beautiful form is the smaller, symmetric, single-responsibility, single-source-of-truth, intent-revealing form that satisfies `GoalArtifact` — smaller, not maximally complete; gold-plating past `GoalArtifact` is itself an ugly defect, not beauty. Beauty judges the coherence and integrity of the form that remains; whether any element is unnecessary is `parsimony`'s question, and whether the whole intervention is worth its cost is the `worth` seat's — beauty must not become a second parsimony or worth vote.

seek truth from facts: verify every factual premise against actual evidence before relying on it. Evidence examples include source artifact or line, current file contents, command result, test assertion, visible input, implementation-worker conclusion, or declared `GoalArtifact` constraint. Any assumed-not-verified premise must be explicitly marked `ASSUMED-UNVERIFIED` in `SshxResultEnvelope.conclusion` and either verified before routing, treated as a `GoalArtifact` goal gap, or used as an abstain trigger. A perspective must never silently rely on an assumed premise.

Each thinking or review worker must surface one compact free-form reasoning-discipline note in `SshxResultEnvelope.conclusion` naming the reference frame, stating the known-good shape and alignment, deviation, or revision status; stating the aesthetic verdict (美不美) with the specific ugly defect and beautiful form, or `no material defect found`, for each candidate weighed; and stating the verified-premise or `ASSUMED-UNVERIFIED` status needed for the verdict. This does not override `GoalArtifact`, assigned bias or review focus, truth tables, or allowed verdict sets, and it is not mandatory citation work, not a literature search, not a parsed schema field, not marker data, not lifecycle authority, and not a blocker for valid `abstain`, `reject`, or `comment` outcomes.

## Thinking Panel

Run six whole-picture philosopher seats before choosing a plan — the same universal judgment lenses the consensus engine debates with. Each seat is one independent, context-isolated perspective that attacks from its own objective; the seats can and do disagree, and the meta-judge converges them:

- `teleology`: purpose and inevitability. What is this for, and is the form forced by that purpose? Attacks skipped-purpose and missing-inevitability.
- `parsimony`: economy. Delete until nothing is left to delete; every element must prove its right to exist. Attacks magic numbers, symptom branches, and machinery that has not earned its place.
- `fidelity`: truth over proxy. Does it measure the real thing, and is every premise verified at its source? Attacks proxy-over-truth and narrative-over-verification.
- `natural-ownership`: locus dyad, ownership pole. Which layer naturally owns this invariant, duty, or constraint — the layer with semantic responsibility and causal control? Attacks symptom patches, duplicated enforcement, and invariants forced onto consumers of what a producer should own.
- `proportional-containment`: locus dyad, containment pole. How far may this intervention rightfully bind, across scope, authority, and duration, given the evidence? Attacks over-hoisting, speculative abstraction, and turning a local fact into universal law.
- `worth` (值不值 — is it worth it?): decision value. Compare the candidate against doing nothing and against the cheapest sufficient alternative, then weigh its incremental expected benefit toward `GoalArtifact` against its total lifecycle cost — build and verification effort, recurring maintenance burden, complexity debt, failure and misuse risk, reversibility, delay, and the opportunity cost of the more valuable work it displaces. Attacks not-worth-it machinery, elegance `GoalArtifact` does not need, and cost that outruns benefit; it may reject a candidate every other seat finds beautiful and well-owned. It must not cut a capability `GoalArtifact.success_criteria` requires to save cost, and it must state its best counterfactual and the decisive cost/benefit assumption rather than fabricating a numeric ROI.

`natural-ownership` and `proportional-containment` are a coupled **must-clash locus dyad**: they run together, each must answer the other pole's claim, and they converge on the natural owner layer — not the highest layer imaginable. Ownership pulls the fix toward the layer that owns the invariant; containment resists over-reaching past it. This is the "go upstream to the root, but not past the natural owner" balance expressed as two adversarial seats the meta-judge converges, rather than a single balanced checklist.

`worth` (值不值) is an independent objective, not the aesthetic lens repeated: `parsimony` asks how much mechanism, `proportional-containment` asks where and how far it binds, and `worth` asks whether to pay for this at all, at this cost, now, versus the best alternative. A candidate can be minimal, beautiful, and properly contained yet still not worth doing, and a less minimal candidate can still be worth doing when the avoided downside justifies it. Because it is a seat rather than a cross-cutting lens, `worth` is judged once by its own perspective, so the panel is not homogenized into every seat re-deriving the same value verdict.

Before proposing, revising, rejecting, or abstaining, each seat must apply `## Reasoning Discipline` to every candidate conclusion it weighs and surface the compact reasoning-discipline note in `SshxResultEnvelope.conclusion` before returning a verdict.

Every seat must first identify the problem essence or root cause implied by `GoalArtifact`, then frame `propose`, `revise`, `reject`, or `abstain` as an answer to it: what satisfies it, what still differs from it, or why it cannot be satisfied. A plan that only patches a surface symptom while leaving that root cause in place does not satisfy the thinking gate. `revise` must name the goal gap and a next iteration question; it must not open an unrelated design search.

Each seat returns one of:

- `propose`
- `revise`
- `reject`
- `abstain`

## Design Truth Table

The meta-judge applies this fixed thinking truth table:

| Inputs | Exit |
|---|---|
| unanimous actionable plan | `implement` |
| close disagreement with compatible plans | `meta-layer convergence` |
| bounded true stall | `abstain/escalate with options` |
| any attempt to use one perspective as consensus | `reject fake consensus` |

`meta-layer convergence` must produce one concrete plan before implementation. If the bounded pass still cannot produce a concrete plan, stop with options instead of inventing agreement.

An `implement` exit requires the concrete plan to be both beautiful (per the `## Reasoning Discipline` aesthetic verdict) and worth its cost (per the `worth` seat). A concrete plan that fails the 值不值 (worth) judgment is an unclosed `GoalArtifact` goal gap: before `implement` the meta-judge must rebut the `worth` seat's factual premises, choose a cheaper sufficient alternative, show the omitted benefit clears the goal threshold, or record an explicit owner-level acceptance of the cost — otherwise the exit stays `meta-layer convergence` or `abstain/escalate with options`, never `implement`. Beauty and worth are a conditional challenge, not a forced clash: when the beautiful form carries a material elegance premium over the cheapest sufficient form, `worth` must justify or reject that premium; when `worth` prefers a cheaper, uglier form, the meta-judge records the accepted debt with its owner, containment boundary, and removal or expiry condition, since `temporary` without an expiry condition is not acceptable.

The convergence question must be "what still differs from `GoalArtifact`?" expressed against the fixed normalized goal, constraints, and success criteria. Do not generalize the convergence pass beyond that goal gap.

Before any `implement` exit, the meta-judge must include in its `meta_judge.conclusion` a compact free-form ASCII relationship diagram built only from `GoalArtifact` and the returned `SshxResultEnvelope.conclusion` values: nodes are the goal subquestions or goal-gap items, the six philosopher-seat stances (`teleology`, `parsimony`, `fidelity`, `natural-ownership`, `proportional-containment`, `worth`), and the concrete plan; edges are labeled `agree`, `conflict`, `depends-on`, `resolved-by`, or `converges-to`. The diagram rigidly constrains convergence: every surfaced subquestion or goal-gap node must appear, the concrete plan must resolve every `conflict` edge — including the `natural-ownership` vs `proportional-containment` locus clash and any `worth` not-worth-it edge — and any unresolved `conflict` edge is an unclosed `GoalArtifact` goal gap, so the exit stays `meta-layer convergence` or `abstain/escalate with options`, never `implement`. The diagram is free-form prompt-level content synthesized from conclusions only; it must not inline worker full reasoning or same-round peer output, and it is not a parsed schema field, marker data, lifecycle authority, or a blocker for valid `abstain` or `reject fake consensus` exits.

## Implementation Worker

Implement only the concrete plan approved by the thinking gate. Keep the implementation boundary narrow and state any deviation before making it.

`sshx` does not grant permission to commit, push, merge, close issues, edit labels, publish releases, or mutate external lifecycle state.

Implementation must be delegated to a worker using the selected `WorkerMode`. The caller context may pass the approved concrete plan and constraints, then receive `conclusion` and `log_ref`; changed-file and test evidence belong in `conclusion`, and process logs stay behind `log_ref`.

## Review Triplet

After implementation, run three review perspectives:

- `architecture`: boundaries, contracts, coupling, and maintainability.
- `quality`: behavior, edge cases, failure modes, and user impact.
- `tests`: coverage, determinism, and verification strength.

Before approving, commenting, or rejecting, each reviewer must apply `## Reasoning Discipline` to every implementation interpretation, repair candidate, or approval path it weighs and surface the compact reasoning-discipline note in `SshxResultEnvelope.conclusion` before returning a verdict.

Each reviewer returns one of:

- `approve`
- `comment`
- `reject`

## Review Truth Table

The meta-judge applies this fixed review truth table:

| Inputs | Exit |
|---|---|
| any explicit reject | `fix` |
| no reject and at least one approve | `done with advisory surfaced` |
| all comment and no approve | `explicit user decision or another bounded review pass` |

Advisory comments do not count as approval. A reject blocks done until the issue is fixed or explicitly converted into a non-blocking advisory by a bounded review pass.

## Fix Or Done

If review exits `fix`, ask what still differs from `GoalArtifact`, apply the smallest change that addresses that blocking goal gap by delegating it to a worker using the selected `WorkerMode` exactly as `## Implementation Worker` requires - open a new `SshxWorkerFlightRecord` for the same `work_target` and stay orchestration-only for the repair - then rerun the review triplet on the worker's returned `conclusion`. Stop after a bounded number of fix passes and report remaining blockers honestly.

If review exits `done with advisory surfaced`, report the final outcome by aggregating `conclusion` values and include any non-blocking advisory feedback without inlining logs.

If review exits `explicit user decision or another bounded review pass`, either run one more bounded pass with a concrete next iteration question tied to `GoalArtifact`, or ask the user to decide. Do not loop indefinitely.

Every bounded pass in this skill - `meta-layer convergence`, a repeated review pass, and fix passes - defaults to at most five passes unless the user explicitly authorizes more, and the chosen bound is recorded before the first such pass.

## Boundaries

This skill is only a prompt contract. It must not add or depend on:

- helper scripts;
- daemons;
- repository-owned CLI;
- GitHub lifecycle operations;
- git lifecycle operations;
- labels;
- release authority;
- a public marker family;
- runtime host configuration as a production source of truth;
- other skills' or repository-owned internal prompts, scripts, or runtimes as an implementation dependency.

Allowed worker carriers are limited to `codex-cli`, `nyxid-oracle`, and `isolated-token-subagent`. Use them only as worker delegation capability, not as controller authority. `nyxid oracle` is used only as the `nyxid-oracle` worker carrier — a reasoning channel in the same category as `codex-cli` — never as a helper script the skill owns, a daemon, or a lifecycle actor.

## Baseline Failure Mode

Without this skill, lightweight high-risk decisions tend to regress to:

- prompt-only self-application where worker reasoning lives in the caller context;
- transcript-based pseudo-isolation presented as enough for independent workers;
- single-threaded advice presented as enough for consensus;
- no required worker mode declaration for peer perspectives;
- no fixed thinking truth table;
- no same-shape review gate before done;
- asserting current-system facts without verifying actual evidence;
- silently relying on assumed factual premises instead of marking, verifying, gap-routing, or abstaining;
- judging only whether a plan is beautiful while never asking whether it is worth its cost, or over-building a beautiful form the goal does not need;
- running every perspective on one model family so the priors are correlated rather than independent;
- pressure to use daemon, GitHub, or git orchestration for cases that only need inline consensus.

## Transcript Template

Use this compact transcript shape when the decision is non-trivial:

```text
intake:
  goal:
    raw_user_input:
    normalized_goal:
    constraints:
    success_criteria:
    iteration_question:
  strict_peer_invisibility_required:
worker_delegation:
  worker_mode_gate:
    codex_cli_capability_check:
    resolved_before_any_worker_dispatch:
    delegated_intake_context_gathering_allowed:
    fallback_reason:
  worker_mode:
  worker_carrier:
  reason:
worker_flights:
  - flight_id:
    stage:
    role:
    worker_mode:
    worker_carrier:
    work_target:
    status:
    retry_budget:
    attempt:
    result_envelope_ref:
    completion_sentinel_ref:
thinking_panel_workers:
  - role: teleology
    bias:
    visible_inputs:
    worker_mode:
    worker_carrier:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
  - role: parsimony
    bias:
    visible_inputs:
    worker_mode:
    worker_carrier:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
  - role: fidelity
    bias:
    visible_inputs:
    worker_mode:
    worker_carrier:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
  - role: natural-ownership
    bias:
    visible_inputs:
    worker_mode:
    worker_carrier:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
  - role: proportional-containment
    bias:
    visible_inputs:
    worker_mode:
    worker_carrier:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
  - role: worth
    bias:
    visible_inputs:
    worker_mode:
    worker_carrier:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
meta_judge:
  exit:
  concrete_plan:
  goal_gap:
  next_iteration_question:
  conclusion: # for an implement exit, includes the free-form ASCII relationship diagram (no separate diagram field)
  log_ref:
implementation_worker:
  worker_mode:
  worker_flight_ref:
  conclusion:
  log_ref:
review_triplet_workers:
  - role: architecture
    bias:
    visible_inputs:
    worker_mode:
    worker_carrier:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
  - role: quality
    bias:
    visible_inputs:
    worker_mode:
    worker_carrier:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
  - role: tests
    bias:
    visible_inputs:
    worker_mode:
    worker_carrier:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
fix_or_done:
  exit:
  conclusion:
  log_ref:
```

## Verification

The contract for this skill is verified by `skills/sshx/tests/test_sshx_contract.py`.

Before adding or changing this skill, record the no-skill failure mode as source-owned contract or test evidence. Do not track runtime artifacts as published skill source.
