# consensus-rnd

English canonical public identity document. 中文 companion: [README.zh-CN.md](./README.zh-CN.md).

`consensus-rnd` is a cross-platform Agent Skills repository for **consensus-driven R&D**: a reusable multi-perspective decision and verification engine that any host repository can inject into its own development loop.

## Positioning

Here, "R&D" has the broadest meaning: any sustained activity that commits state back to a repository. Code changes, documentation, marketing assets, research notes, configuration maintenance, and release work all fit the same shape when the output lands in git, needs review, and needs quality control.

This repository is not bound to one host project. A host injects loop runtime facts through `host.env`: repository root, integration branch, project rules, build and test commands, GitHub slug, and related surfaces. The engine must not hardcode host project facts. `.refactor-loop/` is skill runtime state; host-owned config may be located through `CONSENSUS_RND_HOST_ENV`.

## Core: Consensus-Building Engine

This is not "run the same prompt several times and take a majority vote." The core is **biased, independent, multi-angle convergence**:

- **Multiple solvers with opposing priors**: each solver carries a different stance, such as minimal change, structural cleanliness, or deletion pressure, and cannot see the other solvers' outputs before forming its own conclusion.
- **Meta-judge arbitration**: disagreement is reduced to `consensus` or `converge`; product-level `stalled` remains a router-derived continuation after qualifying `converge`, not a fresh judge-owned verdict.
<!--
Refactor (iter343/issue-343):
  Old pattern: README 单一(非英文默认),CLAUDE.md 文档分层称 README 为权威源;无英文 canonical + 中文 companion 双文件,语言策略未给 README pair carve-out
  New principle: README.md 英文 canonical 公开身份文档 + README.zh-CN.md 中文 companion(双向交叉链接,大段顺序对齐不要求逐句对等);CLAUDE.md 文档分层/根.md收口/语言 carve-out 与 SKILL.md 语言策略窄改:README pair 是唯一英文-canonical 公开文档 carve-out,GitHub issue/PR/commit/design artifact 等工作态仍中文默认。严格按 DESIGN_DECISION_PATH verbatim Concrete plan;不碰 .version-bump.json/额外根文档/runtime/host.env/marker/daemon/workflow
-->
- **Every concrete plan passes the gate**: even when the direction is obvious, a single agent does not implement directly. The gate turns an obvious direction into an evidenced plan.
- **Symmetric verification**: implementation output goes through a multi-reviewer consensus gate before merge.
- **Pure orchestration controller**: analysis, design, implementation, and verification are delegated to workers; deterministic scripts may read markers and dispatch allowlisted next actors; the LLM controller keeps semantic fallback, unknown states, git, and state surfaces.

## Skills

| skill | Description | Status |
|---|---|---|
| `codex-refactor-loop` | Stable Consensus R&D loop entrypoint; keeps audit/refactor as the compatibility intake and producer, uses Codex CLI workers, and treats GitHub as the visible state surface. | Ported from the original host project; `refactor` remains the accepted work-unit metaphor because the maintainer treats refactor as general development. |

## Repository Structure

This is a **cross-platform Agent Skills repository**. The same `skills/` tree is shared by Claude Code, Codex, Cursor, and Gemini; each platform points at it through root manifests:

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

Rules for adding or changing skills, plus version synchronization requirements, live in [CLAUDE.md](./CLAUDE.md).

## Install

### Claude Code

```bash
/plugin marketplace add ChronoAIProject/consensus-rnd
/plugin install consensus-rnd@consensus-rnd
```

### Codex / Cursor

Point the platform plugin mechanism at this repository. `.codex-plugin/plugin.json` and `.cursor-plugin/plugin.json` expose skills through `"skills": "./skills/"`.

### Gemini CLI

Install as an extension. `gemini-extension.json` uses `GEMINI.md` as the context entrypoint, lists available skills, and instructs the agent to read them on demand.

### Direct Copy, Any Agent

Copy `skills/<name>/` into the agent's personal skills directory, such as Claude Code's `~/.claude/skills/`.

### Downstream Host Quickstart

The host installation sequence for `codex-refactor-loop` is centralized in the [`Downstream install walkthrough`](./skills/codex-refactor-loop/SKILL.md#downstream-install-walkthrough). Use that walkthrough to install the skill, copy and fill the host-owned `host.env`, configure user-level cron or launchd, and connect the Claude Code `statusLine`; this README does not duplicate the command matrix.

## Generalization Roadmap

The first shipped skill is a direct port and still carries the "refactor" shell plus a few host-shaped assumptions. The intended evolution is:

1. **Extract the engine spine**: make `solve -> consensus -> implement -> verify` reusable while allowing the seed producer to change from audit output to any work-unit source, such as a design proposal, documentation task, marketing asset, or spec change.
2. **Parameterize leaked host assumptions**: policies such as work language should be host-injected where appropriate, not hardcoded.
3. **Keep "Consensus R&D" as the public product identity**: retain `codex-refactor-loop` as the stable skill entrypoint until there is a real discovery or installation reason to add another alias.

The engine should generalize through its own consensus gate: use the engine to generalize the engine.

## License

[MIT](./LICENSE) © ChronoAIProject
