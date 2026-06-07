---
name: sshx
description: Use when a high-risk or multi-angle decision needs worker-delegated inline consensus with isolated perspectives, fixed truth tables, and no daemon, GitHub, git, label, or release orchestration.
---

# sshx

`sshx` is a lightweight worker-delegated inline consensus skill. It applies the consensus engine philosophy to a single decision or implementation task by dispatching isolated worker perspectives without using `codex-refactor-loop` runtime surfaces.

<!--
Refactor (iter342/issue-342):
  Old pattern: sshx 是 prompt-only 自应用 skill(r2/#349),用 sealed-transcript 作 isolation fallback,worker 推理在 caller 主上下文内
  New principle: sshx = 轻量 worker-delegated inline consensus:WorkerMode 默认 codex-cli / 无则 isolated-token-subagent fallback / 两者无则 abstain;主上下文只 intake/派发/meta-judge/摘要/最终报告,不承载 worker 完整推理或同轮 peer 输出(No Context Pollution);删 prompt-only 自应用 + sealed-transcript。严格按 DESIGN_DECISION_PATH verbatim Concrete plan 逐条改
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

The user's current input is the only source for the goal. `sshx` must not discover or infer the goal from `codex-refactor-loop` milestones, release state, `host.env`, GitHub issues, GitHub pull requests, labels, branches, or any other external lifecycle surface.

`iteration_question` must ask what still differs from `GoalArtifact`, using the normalized goal, constraints, and success criteria as the fixed target. It must not broaden the task into a generic improvement search.

## InlineConsensusProtocol

`InlineConsensusProtocol` is a prompt-level protocol, not a runtime API.

Run the stages in this exact order:

1. `intake` (write `GoalArtifact` and normalize the goal)
2. `choose_worker_mode`
3. `thinking_triplet_workers`
4. `meta_judge`
5. `implementation_worker`
6. `review_triplet_workers`
7. `fix_or_done`

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
2. `isolated-token-subagent`
3. `abstain`

`codex-cli` is the default worker carrier after a non-mutating capability check. The check may confirm that a Codex CLI worker can be invoked, but it must not mutate files, Git state, GitHub state, labels, releases, host configuration, or lifecycle state.

`isolated-token-subagent` is the fallback when `codex-cli` is unavailable. It must run with isolated token context so same-round workers cannot read one another's full reasoning or peer outputs before returning their own verdict.

`abstain` is required when neither `codex-cli` nor `isolated-token-subagent` is available. Do not self-apply the triplet inside the caller context and present it as worker consensus.

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

`codex-cli` completion is recognized only when the caller has both a terminal `SshxResultEnvelope` and the worker-owned `completion_sentinel_ref` recorded on the matching flight. `pgrep`, process-table snapshots, log marker strings, and empty `git status` output are never completion evidence.

If `codex-cli` exits abnormally without both the terminal envelope and the completion sentinel, the caller must stay read-only for that `work_target`, consume the finite same-carrier retry budget, and record the next attempt on the same `SshxWorkerFlightRecord`. If the bounded `codex-cli` retry path still lacks terminal completion, the caller may fall back to `isolated-token-subagent` when available. If no fallback carrier is available or the fallback cannot produce terminal completion, the result is `abstain`; the caller must not implement, repair, or otherwise mutate the same `work_target` itself.

## Result Envelope

`SshxResultEnvelope` is a prompt-level record, not a runtime API. Every caller-carried result from `thinking_triplet_workers`, `meta_judge`, `implementation_worker`, `review_triplet_workers`, and `fix_or_done` must use exactly these top-level fields:

- `conclusion`: compact structured result consumed by the caller. It may include verdicts, decisions, blocking goal gaps, final decision points, changed-file evidence, and test evidence when applicable. It must not include process logs, step-by-step reasoning, raw transcripts, debug output, or same-round peer output.
- `log_ref`: artifact reference for the non-inline worker, meta-judge, implementation, review, or fix log. The caller may open the referenced artifact on demand, but caller-carried transcripts and final reports must keep only the reference and must not inline the log body.

Logs are not inline in caller context. Final reports aggregate `conclusion` values only and retain `log_ref` references for optional inspection.

## No Context Pollution

The caller context must not carry worker full reasoning or same-round peer outputs. It may carry only:

- intake inputs and constraints;
- dispatch briefs sent to each worker;
- `SshxResultEnvelope.conclusion` values, including verdicts and explicitly surfaced blockers;
- `SshxResultEnvelope.log_ref` artifact references;
- final reports that aggregate conclusions only.

Same-round thinking workers must not see one another's outputs before their own verdicts are returned. Same-round review workers follow the same rule. If worker isolation is unavailable, exit through `abstain` instead of degrading the protocol into single-context roleplay.

## Thinking Triplet

Run three biased perspectives before choosing a plan:

- `minimal`: smallest coherent change that satisfies the user goal.
- `structural`: architecture and contract integrity under future growth.
- `delete`: whether the feature, abstraction, or work should be removed, collapsed, or avoided.

Every perspective must frame `propose`, `revise`, `reject`, or `abstain` as an answer to the current `GoalArtifact`: what satisfies it, what still differs from it, or why it cannot be satisfied. `revise` must name the goal gap and a next iteration question; it must not open an unrelated design search.

Each perspective returns one of:

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

The convergence question must be "what still differs from `GoalArtifact`?" expressed against the fixed normalized goal, constraints, and success criteria. Do not generalize the convergence pass beyond that goal gap.

## Implementation Worker

Implement only the concrete plan approved by the thinking gate. Keep the implementation boundary narrow and state any deviation before making it.

`sshx` does not grant permission to commit, push, merge, close issues, edit labels, publish releases, or mutate external lifecycle state.

Implementation must be delegated to a worker using the selected `WorkerMode`. The caller context may pass the approved concrete plan and constraints, then receive `conclusion` and `log_ref`; changed-file and test evidence belong in `conclusion`, and process logs stay behind `log_ref`.

## Review Triplet

After implementation, run three review perspectives:

- `architecture`: boundaries, contracts, coupling, and maintainability.
- `quality`: behavior, edge cases, failure modes, and user impact.
- `tests`: coverage, determinism, and verification strength.

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

If review exits `fix`, ask what still differs from `GoalArtifact`, apply the smallest change that addresses that blocking goal gap, and rerun the review triplet. Stop after a bounded number of fix passes and report remaining blockers honestly.

If review exits `done with advisory surfaced`, report the final outcome by aggregating `conclusion` values and include any non-blocking advisory feedback without inlining logs.

If review exits `explicit user decision or another bounded review pass`, either run one more bounded pass with a concrete next iteration question tied to `GoalArtifact`, or ask the user to decide. Do not loop indefinitely.

## Boundaries

This skill is only a prompt contract. It must not add or depend on:

- helper scripts;
- daemons;
- `consensus-rnd-cli`;
- GitHub lifecycle operations;
- git lifecycle operations;
- labels;
- release authority;
- a public marker family;
- `.refactor-loop/host.env` as a production source of truth;
- `codex-refactor-loop` internal prompts or scripts as an implementation dependency.

Allowed worker carriers are limited to `codex-cli` and `isolated-token-subagent`. Use them only as worker delegation capability, not as controller authority.

## Baseline Failure Mode

Without this skill, lightweight high-risk decisions tend to regress to:

- prompt-only self-application where worker reasoning lives in the caller context;
- transcript-based pseudo-isolation presented as enough for independent workers;
- single-threaded advice presented as enough for consensus;
- no required worker mode declaration for peer perspectives;
- no fixed thinking truth table;
- no same-shape review gate before done;
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
thinking_triplet_workers:
  - role: minimal
    bias:
    visible_inputs:
    worker_mode:
    worker_carrier:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
  - role: structural
    bias:
    visible_inputs:
    worker_mode:
    worker_carrier:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
  - role: delete
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
  conclusion:
  log_ref:
implementation_worker:
  worker_mode:
  worker_flight_ref:
  conclusion:
  log_ref:
review_triplet_workers:
  - role: architecture
    worker_mode:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
  - role: quality
    worker_mode:
    worker_flight_ref:
    verdict:
    conclusion:
    log_ref:
  - role: tests
    worker_mode:
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

Before adding or changing this skill, record the no-skill failure mode as source-owned contract or test evidence. Do not track `.refactor-loop/` runtime artifacts as published skill source.
