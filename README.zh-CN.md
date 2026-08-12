# consensus-rnd

中文 companion public identity document. English canonical: [README.md](./README.md).

`consensus-rnd` 是一个跨平台 Agent Skills 发布仓库。当前发布的 skill 是 `sshx`：面向高风险决策和实现方案的 worker-delegated inline consensus 合同。

本仓库不是应用运行时代码。唯一产物是 `skills/<name>/` 下的 skill，以及让 Claude Code / Codex / Cursor / Gemini 指向同一份 skills 的平台清单。

## 提供什么

| skill | 用途 | 运行形态 |
|---|---|---|
| `sshx` | 通过隔离且互不可见的多角度 worker、固定思考真值表和同构 review gate 完成 inline consensus。 | Prompt-level contract；无 daemon、GitHub、git、label、release 或 lifecycle authority。 |

## 核心

这不是“多跑几遍取多数”，而是偏置独立的多角度逼近：

- **偏置独立多角度**：thinking 和 review worker 带不同先验，同轮封存 verdict 前不得读取彼此输出。
- **meta-judge 收敛**：分歧只收敛到固定出口：达成共识、兼容的 concrete plan，或诚实暴露的有界停滞。
- **concrete plan 必过共识闸**：即使方向明显，实现前仍需独立验证。
- **验证侧同构**：产物经独立 reviewer 和固定 review truth table 后才能宣告完成。
- **纯编排**：caller 只派发 worker、汇总有界结论；实现、验证、修复和设计求解交给 worker。

## 快速开始

### Claude Code

```bash
/plugin marketplace add ChronoAIProject/consensus-rnd
/plugin install consensus-rnd@consensus-rnd
```

### Codex / Cursor

按各自插件机制指向本仓库；`.codex-plugin/plugin.json` 与 `.cursor-plugin/plugin.json` 通过 `"skills": "./skills/"` 暴露 skill。

### Gemini CLI

作为扩展安装。`gemini-extension.json` 以 `GEMINI.md` 为上下文入口，并列出可用 skill。

### 直接拷贝

把 `skills/<name>/` 拷进 agent 的个人 skills 目录，如 Claude Code 的 `~/.claude/skills/`。

## 架构

`skills/` 是共享产品 surface。每个 skill 在自己的 `SKILL.md` 维护契约；重型参考可放在同目录，机械行为放在该 skill 的 `scripts/` 树下。

各平台清单把不同 agent 指向同一份 skills：

```text
.
├── .claude-plugin/        # Claude Code: plugin.json + marketplace.json
├── .codex-plugin/         # Codex: plugin.json
├── .cursor-plugin/        # Cursor: plugin.json
├── gemini-extension.json + GEMINI.md   # Gemini: extension manifest + context entry
├── package.json           # npm 风格元数据 / 版本锚点
├── AGENTS.md -> CLAUDE.md # 跨 agent 约定，符号链接
├── CLAUDE.md              # 本仓库内工作的 agent 指南
├── README.md              # 英文 canonical public identity document
├── README.zh-CN.md        # 中文 companion public identity document
├── LICENSE                # MIT
├── skills/<name>/         # 各 skill；SKILL.md 必备
└── .version-bump.json     # 各清单版本号同步映射
```

## License

[MIT](./LICENSE) © ChronoAIProject
