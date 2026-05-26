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

- `skills/codex-refactor-loop/` 为 host 项目移植,**verbatim**,仍带"重构"外壳与少量 host 主张。泛化路线见 `README.md` 的「泛化路线」。脱壳 / 重命名前不要改它的正文逻辑；例外：经 Phase 9 deep consensus 明确授权的 host-agnostic bootstrap / policy 注入修正可先落地，但不得引入具体 host 事实，且必须配套行为测试。

**该例外同时覆盖** Phase 9 共识授权的**确定性 controller-runtime 路由 daemon**(例如 narrow allowlist phase router):必须 host-agnostic、必须 narrow allowlist 不引入 lifecycle authority、必须配套行为测试与 source-regression test、必须在 SKILL.md 加 named exception 段。这是为了把可机械的 controller 工作从长跑 LLM 搬到脚本(维护者明示 "LLM 长时间运行会退化,但是脚本不会"，见 issue #37 共识)。

**该例外同时覆盖**审计产生的**机械型 hygiene 批次**(audit-derived,`requires_design=false`):每条已在 audit run artifact 内明确(例如 `.refactor-loop/runs/self-audit-skill.md` / `skill-improvement-*.md`),且必须 host-agnostic、必须配套源回归测试、必须每个改动加 `Refactor (iterN/cluster): Old pattern: ... New principle: ...` 自文档块。维护者通过 audit `requires_design=false` 标记隐式授权,无需逐项 Phase 9 round;controller 必须在 FIX_REPORT 逐项标 ✓ fixed / ⏭ already addressed / ❌ blocked。
