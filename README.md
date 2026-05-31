# consensus-rnd

English canonical public identity document. 中文 companion: [README.zh-CN.md](./README.zh-CN.md).

`consensus-rnd` is a cross-platform Agent Skills publication repository. Its product identity is a **consensus engine** for repository work: biased independent solvers produce candidate plans, a meta-judge converges them into one concrete plan, implementation runs, independent reviewers apply the same consensus discipline, and only then may the work proceed to merge.

This repository is not an application runtime. Its deliverables are skills under `skills/<name>/` plus the platform manifests that expose the same skill tree to Claude Code, Codex, Cursor, and Gemini.

## What It Provides

| skill | What it is for | Runtime shape |
|---|---|---|
| `codex-refactor-loop` | Heavy autonomous Consensus R&D work-unit loop for issue/PR resolution, ongoing repository R&D, daemon supervision, Codex workers, GitHub orchestration, review gates, and automated release publication when the host opts in. Audit/refactor is a fallback issue producer when no actionable managed work is open. | Uses checked-in scripts, `.refactor-loop/` state, GitHub, git, and host-provided `host.env` facts. |
| `sshx` | Lightweight worker-delegated inline consensus methodology (轻量 worker-delegated inline 共识方法论) for high-risk decisions or implementation plans that need isolated perspectives but do not need daemon, GitHub, or git orchestration. | WorkerMode dispatches isolated thinking and review workers; no daemon, no lifecycle authority, no runtime control plane, and not a duplicate alias for `codex-refactor-loop`. |

## Core

The engine is not "run the same prompt several times and vote." It is biased, independent, multi-angle convergence:

- **Biased independent perspectives**: solver and reviewer roles start from different priors, such as minimal change, structural integrity, deletion pressure, architecture, quality, and tests. Same-round peers must not read one another's output before sealing their own verdicts.
- **Meta-judge convergence**: disagreement is reduced into fixed exits: reached consensus, close enough to converge into one concrete plan, or truly stalled and escalated to a meta layer.
- **Concrete plans pass the gate**: even when a direction looks obvious, one agent does not skip directly to implementation. The gate turns an obvious direction into an evidenced plan.
- **Symmetric verification**: the finished work goes through independent reviewers and a fixed review truth table before merge.
- **Pure orchestration controller**: the controller routes, posts, labels, spawns, commits, pushes, merges, and publishes only through narrow allowed surfaces; design, implementation, verification, and repair are delegated to workers or deterministic scripts.

## Risks

`codex-refactor-loop` is an experimental autonomous R&D system. Enable it only after the host repository explicitly opts in and the maintainers understand these risks:

- **Autonomous writes**: when enabled, the loop can run unattended. Controller-owned paths may commit, push, open PRs, merge PRs, and publish releases after their respective gates and allowlists pass, without per-action human confirmation. Agent workers only produce implementation diffs in isolated worktrees; they do not commit or push.
- **API and compute cost**: continuous Codex worker dispatch plus six GitHub-polling daemons can continuously consume API quota, model tokens, and local compute.
- **Automatic releases**: with `RELEASE_AUTO_ENABLE=true`, the release gate may automatically bump manifests, commit, push, tag, and publish a GitHub release after required checks pass. Bad published tags are abandoned and superseded by the next version; they are not moved or rolled back.
- **Host boundary**: skills have no authority to edit host configuration outside the surfaces exposed through `host.env`. The active-controller lease still creates a single writer that can mutate GitHub and already-pushed git surfaces within its narrow authorization.
- **Experimental scope**: this is dogfood-stage infrastructure, not a production stability guarantee. Downstream hosts must opt in before autonomous surfaces do anything.

## Quick Start

### Claude Code

```bash
/plugin marketplace add ChronoAIProject/consensus-rnd
/plugin install consensus-rnd@consensus-rnd
```

### Codex / Cursor

Point the platform plugin mechanism at this repository. `.codex-plugin/plugin.json` and `.cursor-plugin/plugin.json` expose skills through `"skills": "./skills/"`.

### Gemini CLI

Install as an extension. `gemini-extension.json` uses `GEMINI.md` as the context entrypoint, lists available skills, and instructs the agent to read them on demand.

### Direct Copy

Copy `skills/<name>/` into the agent's personal skills directory, such as Claude Code's `~/.claude/skills/`.

### Downstream Host Setup

The host installation sequence for `codex-refactor-loop` is centralized in the [`Downstream install walkthrough`](./skills/codex-refactor-loop/SKILL.md#downstream-install-walkthrough). Use that walkthrough to install the skill, copy and fill the host-owned `host.env`, configure user-level cron or launchd, and connect the Claude Code `statusLine`; this README does not duplicate the command matrix.

## Architecture

`skills/` is the shared product surface. Each skill owns its contract in `SKILL.md`; heavy references may live beside it, and mechanical behavior lives under that skill's `scripts/` tree.

Platform manifests point different agents at the same skills:

```text
.
├── .claude-plugin/        # Claude Code: plugin.json + marketplace.json
├── .codex-plugin/         # Codex: plugin.json
├── .cursor-plugin/        # Cursor: plugin.json
├── gemini-extension.json + GEMINI.md   # Gemini: extension manifest + context entry
├── package.json           # npm-style metadata / version anchor
├── AGENTS.md -> CLAUDE.md # Cross-agent rules, symlinked
├── CLAUDE.md              # Agent guide for work inside this repository
├── README.md              # English canonical public identity document
├── README.zh-CN.md        # Chinese companion public identity document
├── LICENSE                # MIT
├── skills/<name>/         # Each skill; SKILL.md is required
└── .version-bump.json     # Manifest version synchronization map
```

Host projects inject runtime facts through `host.env`: repository root, GitHub slug, review and integration branches, project rules, build/test commands, release opt-in, and related surfaces. The skill repository must not hardcode host facts or modify host configuration directly.

## Roadmap

The public product identity is Consensus R&D. Refactoring, issue-solving, and repository R&D are different entry surfaces for the same work-unit loop. `codex-refactor-loop` remains the stable heavy autonomous loop entrypoint, while `sshx` carries the same consensus philosophy as a lightweight worker-delegated inline method, not as a duplicate alias for the heavy loop. Future work should continue generalizing host/project assumptions and producer inputs while keeping runtime authority narrow enough to verify mechanically.

## License

[MIT](./LICENSE) © ChronoAIProject
