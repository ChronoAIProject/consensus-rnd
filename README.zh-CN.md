# consensus-rnd

中文 companion public identity document. English canonical: [README.md](./README.md).

`consensus-rnd` 是一个跨平台 Agent Skills 发布仓库。它的产品身份是面向 repo-owned GitHub issue/PR work 的**共识引擎**:偏置独立的多角度 solver 先给出候选方案,meta-judge 收敛成一个 concrete plan,随后实现,再由多 reviewer 过同构共识 gate,最后才进入 merge。

本仓库不是应用运行时代码。唯一产物是 `skills/<name>/` 下的 skill 及根目录中让 Claude Code / Codex / Cursor / Gemini 指向同一份 `skills/` 的平台清单。

## 提供什么

| skill | 用途 | 运行形态 |
|---|---|---|
| `consensus-loop` | 重型自治 Consensus R&D work-unit loop:解决 repo-owned GitHub issue/PR、持续推进 repository R&D、daemon 监督、Codex worker、GitHub 编排、review gate,以及 host opt-in 后的自动发版。audit/refactor 仅在没有 actionable managed work 时作为 fallback issue producer。 | 使用 checked-in scripts、`.refactor-loop/` state、GitHub、git 和 host 通过 `host.env` 注入的事实。 |
| `sshx` | 轻量纯 prompt 共识方法论:高风险决策或实现方案需要隔离多角度判断,但不需要 daemon/GitHub/git 编排时使用。 | 纯 prompt contract:无 daemon、无 lifecycle authority、无 runtime control plane。 |

## 核心

这不是"多跑几遍取多数",而是**偏置独立的多角度逼近**:

- **偏置独立多角度**:solver / reviewer 带不同先验,如 minimal 最小改动、structural 架构完整、delete 质疑必要性、architecture、quality、tests;同一轮 peer 在 sealed verdict 前不得读取彼此输出。
- **meta-judge 收敛**:分歧只收敛到固定出口:达成共识、足够接近并收敛成一个 concrete plan、或真停滞后升级 meta-layer。
- **concrete plan 必过共识闸**:哪怕方向明显,也不允许单个 agent 跳过共识直接实现;gate 把"明显方向"证成"明确方案"。
- **验证侧同构**:实现产物再经独立 reviewer 和固定 review truth table 才允许 merge。
- **controller 纯编排**:controller 只通过窄授权 surface route / post / label / spawn / commit / push / merge / publish;设计、实现、验证、修复交给 worker 或确定性脚本。

## ⚠️ 风险提示

`consensus-loop` 是实验性自治研发系统。下游仓库必须显式 opt in,且 maintainer 理解以下风险后再启用:

- **自治写操作**:启用后 loop 可无人值守运行。controller-owned 路径在相应 gate 与 allowlist 通过后,可执行 commit、push、open PR、merge PR、release publish,且没有逐动作人工确认。agent worker 只在隔离 worktree 产出实现 diff,不 commit/push。
- **API/算力成本**:持续派发 Codex worker 加上 6 个 daemon 轮询 GitHub,会持续消耗 API quota、model token 和本机算力。
- **自动发版**:`RELEASE_AUTO_ENABLE=true` 时,release gate 可在 required checks 全绿后自动 bump manifest、commit、push、tag 并发布 GitHub release。坏版即弃,用下一个版本取代;已发布 tag 不移动、不回滚。
- **host 边界**:skill 无权修改 host 暴露面之外的配置,只在 `host.env` 暴露的 surface 工作。但 active-controller lease 会形成单写者,在窄授权内写 GitHub/已 push git 面。
- **适用范围**:这是 dogfood 阶段的实验性研发基础设施,不是生产稳定保证;下游 host 未 opt in 时自治 surface 应保持 noop。

## 快速开始

### Claude Code

```bash
/plugin marketplace add ChronoAIProject/consensus-rnd
/plugin install consensus-rnd@consensus-rnd
```

### Codex / Cursor

按各自插件机制指向本仓库;`.codex-plugin/plugin.json` 与 `.cursor-plugin/plugin.json` 已通过 `"skills": "./skills/"` 暴露 skills。

### Gemini CLI

作为扩展安装。`gemini-extension.json` 以 `GEMINI.md` 为上下文入口,列出可用 skills 并指引按需读取。

### 直接拷贝

把 `skills/<name>/` 拷进 agent 的个人 skills 目录,如 Claude Code 的 `~/.claude/skills/`。

### 下游 host 设置

`consensus-loop` 的 host 安装顺序集中在英文 canonical README 的 "Downstream Host Setup" 和 skill 内的 walkthrough。按该 walkthrough 安装 skill、复制并填写 host-owned `host.env`、配置用户级 cron/launchd 和 Claude Code `statusLine`;本 companion 不复制命令矩阵。

## 架构

`skills/` 是共享产品 surface。每个 skill 在自己的 `SKILL.md` 维护契约;重型参考可放在同目录,机械行为放在该 skill 的 `scripts/` 树下。

各平台清单把不同 agent 指向同一份 skills:

```text
.
├── .claude-plugin/        # Claude Code: plugin.json + marketplace.json
├── .codex-plugin/         # Codex: plugin.json
├── .cursor-plugin/        # Cursor: plugin.json
├── gemini-extension.json + GEMINI.md   # Gemini: extension manifest + context entry
├── package.json           # npm 风格元数据 / 版本锚点
├── AGENTS.md -> CLAUDE.md # 跨 agent 约定,符号链接
├── CLAUDE.md              # 本仓库内工作的 agent 指南
├── README.md              # 英文 canonical public identity document
├── README.zh-CN.md        # 中文 companion public identity document
├── LICENSE                # MIT
├── skills/<name>/         # 各 skill;SKILL.md 必备
└── .version-bump.json     # 各清单版本号同步映射
```

host 项目通过 `host.env` 注入运行时事实:仓库根、GitHub slug、review/integration 分支、项目规则、构建/测试命令、release opt-in 等。skill 仓库不得硬编码 host 事实,也不得直接修改 host 配置。

能力边界:`consensus-loop` 支持可表达为 managed GitHub issue/PR 的 bounded repo-owned work,包括 feature、bug、documentation、governance、refactor/重构。不支持任意 GitHub admin,例如 Projects、milestones、assignee 管理、Discussions、catalog 外 label-taxonomy mutation、named helper 外 issue/PR body edit、release allowlist 外 tag/release、或任意 custom lifecycle authority。

## 路线

公开产品身份是 Consensus R&D。managed repo-owned GitHub issue/PR work 是主路径;audit/refactor 只在没有 actionable managed work 时作为 fallback producer,创建或更新进入同一 loop 的 work。`consensus-loop` 保留为重型自治 loop 入口,`sshx` 则把同一套共识哲学压缩成轻量纯 prompt 方法。后续工作应继续 generalize host/project assumptions 和 producer inputs,并把 runtime authority 收窄到可机械验证的边界。

## License

[MIT](./LICENSE) © ChronoAIProject
