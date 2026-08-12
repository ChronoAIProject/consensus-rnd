# consensus-rnd

English canonical public identity document. 中文 companion: [README.zh-CN.md](./README.zh-CN.md).

`consensus-rnd` is a cross-platform Agent Skills publication repository. Its published skill is `sshx`, a worker-delegated inline consensus contract for high-risk decisions and implementation plans.

This repository is not an application runtime. Its deliverables are skills under `skills/<name>/` plus platform manifests that expose the same skill tree to Claude Code, Codex, Cursor, and Gemini.

## What It Provides

| skill | What it is for | Runtime shape |
|---|---|---|
| `sshx` | Worker-delegated inline consensus (轻量 worker-delegated inline 共识方法论) with isolated, independent perspectives, a fixed thinking truth table, and a same-shape review gate. | Prompt-level contract; no daemon, GitHub, git, label, release, or lifecycle authority. |

## Core

The engine is not "run the same prompt several times and vote." It is biased, independent, multi-angle convergence:

- **Biased independent perspectives**: thinking and review workers start from different priors and must not read same-round peer output before sealing their own verdicts.
- **Meta-judge convergence**: disagreement is reduced into fixed exits: consensus, a compatible concrete plan, or a bounded stall that is surfaced honestly.
- **Concrete plans pass the gate**: an obvious direction still needs independent validation before implementation.
- **Symmetric verification**: finished work passes independent reviewers and the fixed review truth table before it is declared done.
- **Pure orchestration**: the caller dispatches workers and aggregates bounded conclusions; implementation, verification, repair, and design solving remain delegated.

## Quick Start

### Claude Code

```bash
/plugin marketplace add ChronoAIProject/consensus-rnd
/plugin install consensus-rnd@consensus-rnd
```

For local repository development, you may manually create `.claude/skills -> ../skills`; it is ignored and is not a published Claude entrypoint.

### Codex / Cursor

Point the platform plugin mechanism at this repository. `.codex-plugin/plugin.json` and `.cursor-plugin/plugin.json` expose skills through `"skills": "./skills/"`.

### Gemini CLI

Install as an extension. `gemini-extension.json` uses `GEMINI.md` as the context entrypoint and lists the available skill.

### Direct Copy

Copy `skills/<name>/` into the agent's personal skills directory, such as Claude Code's `~/.claude/skills/`.

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

## License

[MIT](./LICENSE) © ChronoAIProject
