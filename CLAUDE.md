# consensus-rnd — Agent 工作指南

本文件给**在本仓库内工作的 agent**（增改 skill、维护清单、发版）看；不是给 host 项目运行时用的。host 运行时的事实由 `host.env` 注入,见各 skill 的 SKILL.md。

## 仓库性质

这是一个**跨平台 Agent Skills 发布仓库**,不是应用代码仓库。唯一产物是 `skills/<name>/` 下的 `SKILL.md` 及其配套文件。同一份 `skills/` 被 Claude Code / Codex / Cursor / Gemini 共享,各平台只靠根目录的清单文件指向它。

定位与共识构建引擎的设计哲学见 `README.md`。

## 目录约定

```
.
├── .claude-plugin/      # Claude Code:plugin.json(插件清单)+ marketplace.json(可被 marketplace add)
├── .codex-plugin/       # Codex:plugin.json("skills": "./skills/" + interface 元数据)
├── .cursor-plugin/      # Cursor:plugin.json("skills": "./skills/")
├── gemini-extension.json + GEMINI.md   # Gemini:扩展清单 + 上下文入口
├── package.json         # npm 风格元数据 / 版本锚点
├── AGENTS.md → CLAUDE.md  # 跨 agent 约定(符号链接,内容即本文件)
├── LICENSE              # MIT
├── skills/<name>/       # 各 skill(SKILL.md 必备)
└── .version-bump.json   # 版本号同步映射
```

## 新增 / 修改 skill

- 每个 skill 一个目录:`skills/<kebab-name>/SKILL.md`。
- frontmatter 仅需 `name` + `description`(全文 ≤1024 字符)。`description` 以 "Use when..." 描述**触发条件**,不要复述工作流。
- 重型参考拆 `REFERENCE.md`;脚本放 `scripts/`;prompt 模板放 `prompts/`。
- 写 / 改 skill **必须**遵循 `superpowers:writing-skills` 的 TDD 纪律:先用子 agent 跑 baseline(看它在没有 skill 时怎么失败),再写 / 改 skill。

## 版本同步(强制)

改版本号时,`.version-bump.json` 列出的所有文件必须同步为同一版本:`package.json`、`.claude-plugin/plugin.json`、`.claude-plugin/marketplace.json`、`.codex-plugin/plugin.json`、`.cursor-plugin/plugin.json`、`gemini-extension.json`。漏改任一份会让某个平台装到旧版。

## 当前状态

- `skills/codex-refactor-loop/` 为 host 项目移植,**verbatim**,仍带"重构"外壳与少量 host 主张。泛化路线见 `README.md` 的「泛化路线」。脱壳 / 重命名前不要改它的正文逻辑；例外：经 Phase 9 deep consensus 明确授权的 host-agnostic bootstrap / policy 注入修正可先落地，但不得引入具体 host 事实，必须 narrow allowlist、no lifecycle authority，且必须配套行为测试与 source-regression test。

**该例外同时覆盖** Phase 9 deep consensus 共识授权的**任何 named runtime surface**(narrow allowlist deterministic controller-runtime daemon、observability surface、release/lifecycle decision-artifact surface,等):必须 **host-agnostic** + **narrow allowlist 不引入 lifecycle authority** + 必须配套**行为测试/behavior tests**与 **source-regression test** + 必须在 SKILL.md 加 **named exception 段**(标题含 `## Named runtime exception — <surface-name>(per #<issue>)`)+ 必须有 Phase 9 r<N> consensus 的 judge artifact 引用。每个 named surface 都必须:
- **host-agnostic**:目录路径相对 `$REPO_ROOT`,无 host fact 注入
- **no lifecycle authority**:不开关 issue/PR/label,不 commit/push/merge,不 tag,不 release publish。**release/lifecycle surface 仅产出 durable decision/candidate artifact**,真 commit/push/tag/release/merge/close 仍由既有 controller 或 release pipeline 在 host opt-in + 有效 artifact 同时成立后执行
- **narrow allowlist**:每段必须明确列举该 surface 允许做的事;**不**给 surface 通用授权或 escape hatch
**不放宽**:merge gate、CI/release policy、语言 policy、Tier I/II 边界、版本同步规则、任意 CLAUDE.md 修宪授权、独立 PR 自我放行权。
这是为了把可机械的 controller 工作从长跑 LLM 搬到脚本(维护者明示 "LLM 长时间运行会退化,但是脚本不会",见 issue #37 共识),同时让后续 Phase 9 授权的 named surface(如 observability per #51 + release decision-artifact per #56)能 mechanical 落地,不需每次都修宪。

**该例外也可由 maintainer-directive artifact 等价证明**:仅限 audit-derived、`requires_design=false` 的机械型 hygiene 批次;当维护者在 `.refactor-loop/runs/maintainer-directives/<date>-<topic>.md` 明示授权该批次时,该 artifact 可视为 Phase 9 deep consensus 等价证明。该等价证明不新增独立 PR 自我放行权,也不放宽 merge gate、CI/release policy、语言 policy、lifecycle authority 或 Tier I/II 边界,不得作为任意 `CLAUDE.md` 修宪授权;覆盖项仍必须 host-agnostic、不得引入具体 host 事实、必须配套行为测试与源回归测试/source-regression test、必须保留 `Refactor (iterN/cluster): Old pattern: ... New principle: ...` 自文档块,并在 FIX_REPORT 逐项标 fixed / already addressed / blocked。
