---
name: sshx
description: Use when a high-risk or multi-angle decision needs inline consensus with isolated perspectives, fixed truth tables, and no daemon, GitHub, git, label, or release orchestration.
---

# sshx

`sshx` is a prompt-only inline consensus skill. It applies the consensus engine philosophy to a single decision or implementation task without using `codex-refactor-loop` runtime surfaces.

<!--
Refactor (iter342/issue-342):
  Old pattern: 共识引擎哲学只存在于 codex-refactor-loop 的 daemon/controller/GitHub 重编排里,无脱离脚本的轻量通用复刻
  New principle: 新增独立纯 prompt skill skills/sshx/SKILL.md:InlineConsensusProtocol(intake→choose_isolation_mode→thinking_triplet→meta_judge→implement→review_triplet→fix_or_done)+ IsolationMode 固定 actor-isolated|sealed-transcript|abstain。严格按 DESIGN_DECISION_PATH 的 verbatim Concrete plan 逐条改;TDD baseline 只保留为 source-owned contract/test evidence,不把 .refactor-loop runtime artifact 纳入发布源
-->

## Trigger

Use this skill when:

- a decision has meaningful product, architecture, correctness, safety, or cost risk;
- the user asks for multi-angle thinking, consensus, or review without starting a long-running work-unit loop;
- a concrete plan should be tested against independent perspectives before implementation;
- a finished change should pass a same-shape review gate before declaring done.

Do not use this skill for routine one-step answers where no separate perspectives would change the outcome.

## InlineConsensusProtocol

`InlineConsensusProtocol` is a prompt-level protocol, not a runtime API.

Run the stages in this exact order:

1. `intake`
2. `choose_isolation_mode`
3. `thinking_triplet`
4. `meta_judge`
5. `implement`
6. `review_triplet`
7. `fix_or_done`

Each thinking or review record must include these fields:

- `role`
- `bias`
- `visible_inputs`
- `isolation_mode`
- `sealed_before_peer_read`
- `verdict`

## IsolationMode

`IsolationMode` has exactly these values, in priority order:

1. `actor-isolated`
2. `sealed-transcript`
3. `abstain`

`actor-isolated` uses isolated subagent, Task, or equivalent peer actors. Same-round peers must not see one another's outputs before they seal their own verdicts.

`sealed-transcript` is a degraded fallback. The agent writes and seals each angle before reading same-round peer outputs. It is auditable weak isolation, not strict peer invisibility.

`abstain` is required when strict peer invisibility is mandatory and `actor-isolated` is unavailable. Do not pretend that `sealed-transcript` is equivalent to strict actor isolation.

## Thinking Triplet

Run three biased perspectives before choosing a plan:

- `minimal`: smallest coherent change that satisfies the user goal.
- `structural`: architecture and contract integrity under future growth.
- `delete`: whether the feature, abstraction, or work should be removed, collapsed, or avoided.

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

## Implement

Implement only the concrete plan approved by the thinking gate. Keep the implementation boundary narrow and state any deviation before making it.

`sshx` does not grant permission to commit, push, merge, close issues, edit labels, publish releases, or mutate external lifecycle state.

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

If review exits `fix`, apply the smallest change that addresses the blocking finding and rerun the review triplet. Stop after a bounded number of fix passes and report remaining blockers honestly.

If review exits `done with advisory surfaced`, summarize the final outcome and include any non-blocking advisory feedback.

If review exits `explicit user decision or another bounded review pass`, either run one more bounded pass or ask the user to decide. Do not loop indefinitely.

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

Use external actors only as isolation capability, not as controller authority.

## Baseline Failure Mode

Without this skill, lightweight high-risk decisions tend to regress to:

- single-threaded advice presented as enough for consensus;
- no required isolation declaration for peer perspectives;
- no fixed thinking truth table;
- no same-shape review gate before done;
- pressure to use daemon, GitHub, or git orchestration for cases that only need inline consensus.

## Transcript Template

Use this compact transcript shape when the decision is non-trivial:

```text
intake:
  goal:
  constraints:
  strict_peer_invisibility_required:
isolation:
  mode:
  reason:
thinking_triplet:
  - role: minimal
    bias:
    visible_inputs:
    isolation_mode:
    sealed_before_peer_read:
    verdict:
  - role: structural
    bias:
    visible_inputs:
    isolation_mode:
    sealed_before_peer_read:
    verdict:
  - role: delete
    bias:
    visible_inputs:
    isolation_mode:
    sealed_before_peer_read:
    verdict:
meta_judge:
  exit:
  concrete_plan:
review_triplet:
  - role: architecture
    verdict:
  - role: quality
    verdict:
  - role: tests
    verdict:
fix_or_done:
  exit:
```

## Verification

The contract for this skill is verified by `skills/sshx/tests/test_sshx_contract.py`.

Before adding or changing this skill, record the no-skill failure mode as source-owned contract or test evidence. Do not track `.refactor-loop/` runtime artifacts as published skill source.
