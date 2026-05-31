# consensus-rnd

中文 companion public identity document. English canonical: [README.md](./README.md).

`consensus-rnd` 是一个跨平台 Agent Skills 仓库,提供**共识式研发**引擎:任意 host 仓库都可注入的多角度决策与验证循环。

## 定位

这里的"研发"取最广义:任何持续向仓库提交状态的活动都是研发。写代码、写文档、做 marketing、整理研究资料、维护配置、发版,只要产物落进 git、需要被审查、需要质量保证,就适用同一套引擎。

本库不绑定任何具体 host 项目。host 通过 `host.env` 注入 loop runtime 事实:仓库根、集成分支、项目规则、构建/测试命令、GitHub slug 等。引擎本身不得写死 host 项目事实。`.refactor-loop/` 是 skill runtime state;host-owned config 可通过 `CONSENSUS_RND_HOST_ENV` 定位。

## 核心:共识构建引擎

这不是"多跑几遍取多数",而是**偏置独立的多角度逼近**:

- **多 solver,先验对立**:每个 solver 带不同立场,如 minimal 最小改动、structural 架构洁净、delete 质疑必要性,且在形成结论前互相看不到对方输出。
- **meta-judge 仲裁**:把分歧收敛成 `consensus` / `converge`;产品层仍有 `stalled` 出口,但它由 router 在 qualifying `converge` 后按 deterministic predicate 派生,不是 judge 新增 verdict。
- **任何 concrete plan 都必须过闸**:哪怕方向已明确,也不允许单个 agent 直接落地;用多角度验证把"明显方向"证成"明显方案"。
- **验证侧同构**:实现产物再经多 reviewer 共识 gate 才允许合并。
- **controller 纯编排**:所有分析、设计、实现、验证都 delegate 给 worker;确定性脚本可按 allowlist 读 marker 并派发下一 actor;LLM controller 保留语义 fallback、未知状态、git 与状态面。

## skills

| skill | 说明 | 状态 |
|---|---|---|
| `codex-refactor-loop` | Consensus R&D 循环的稳定 skill 入口;默认以 audit/refactor 作为兼容 intake / producer,由 Codex CLI worker 执行,GitHub 是可见状态面。 | 自原 host 项目移植;保留 refactor 作为合法 work-unit 隐喻,因为 maintainer 接受 "refactor = development" 的通用解释。 |

## 仓库结构

本库是一个**跨平台 Agent Skills 仓库**。同一份 `skills/` 被 Claude Code / Codex / Cursor / Gemini 共享,各平台靠根目录的清单文件指向它:

```text
.
├── .claude-plugin/        # Claude Code: plugin.json + marketplace.json
├── .codex-plugin/         # Codex: plugin.json
├── .cursor-plugin/        # Cursor: plugin.json
├── gemini-extension.json + GEMINI.md   # Gemini: extension manifest + context entry
├── package.json           # npm 风格元数据 / 版本锚点
├── AGENTS.md -> CLAUDE.md # 跨 agent 约定(符号链接)
├── CLAUDE.md              # 本仓库内工作的 agent 指南
├── README.md              # 英文 canonical public identity document
├── README.zh-CN.md        # 中文 companion public identity document
├── LICENSE                # MIT
├── skills/<name>/         # 各 skill,SKILL.md 必备
└── .version-bump.json     # 各清单版本号同步映射
```

新增 / 修改 skill 的约定与版本同步规则见 [CLAUDE.md](./CLAUDE.md)。

## 安装

### Claude Code

```bash
/plugin marketplace add ChronoAIProject/consensus-rnd
/plugin install consensus-rnd@consensus-rnd
```

### Codex / Cursor

按各自插件机制指向本仓库;`.codex-plugin/plugin.json` 与 `.cursor-plugin/plugin.json` 已通过 `"skills": "./skills/"` 暴露 skills。

### Gemini CLI

作为扩展安装。`gemini-extension.json` 以 `GEMINI.md` 为上下文入口,列出可用 skills 并指引按需读取。

### 直接拷贝(任意 agent)

把 `skills/<name>/` 拷进 agent 的个人 skills 目录,如 Claude Code 的 `~/.claude/skills/`。

### 下游 host quickstart

`codex-refactor-loop` 的 host 安装顺序集中在英文 canonical README 的 "Downstream Host Quickstart" 和 skill 内的 walkthrough。按该 walkthrough 安装 skill、复制并填写 host-owned `host.env`、配置用户级 cron/launchd 和 Claude Code `statusLine`;本 companion 不复制命令矩阵。

## 泛化路线

第一块 skill 是直接移植,仍带"重构"外壳与少量 host 主张。后续迭代方向:

1. **抽出引擎脊柱**:让 `solve -> consensus -> implement -> verify` 可复用,把 seed producer 从 audit 输出替换为任意 work-unit 来源,如设计提案、文档任务、marketing 产出或 spec 变更。
2. **参数化漏入的 host 主张**:如工作语言策略应在合适边界由 host 注入,而非写死。
3. **对外以 "Consensus R&D" 为主产品身份**:保留 `codex-refactor-loop` 作为稳定 skill 入口,直到未来出现真实平台发现或安装问题再考虑新增 alias。

泛化引擎本身宜走引擎自己的共识 gate:用这个引擎来通用化这个引擎。

## License

[MIT](./LICENSE) © ChronoAIProject
