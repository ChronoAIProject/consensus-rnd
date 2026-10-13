# consensus-rnd

中文公开文档。英文 canonical：[README.md](./README.md)。

`consensus-rnd` 为 Claude Code、Codex、Cursor 和 Gemini 发布 Agent Skills。`sshx` 用隔离的 worker 视角处理高风险决策或实现任务，收敛出具体方案，再由独立 review 检查产物。交付物是 prompt 合同及其配套文件，由各平台清单共享。

当产品、架构、正确性、安全或成本风险值得投入多个视角时使用。若常规一步式回答不会因增加视角而改变结果，就不值得承担协议成本。详见[触发条件与排除项](./skills/sshx/SKILL.md#trigger)。

## 提供什么

| skill | 用途 | 运行形态 |
|---|---|---|
| `sshx` | 面向决策、方案或已完成变更的 worker-delegated inline consensus。 | Prompt 合同与 worker runner；无 skill 自有 daemon、GitHub、git、label、release 或生命周期编排权限；可用已有 host heca daemon 承载 worker。 |

## 核心

这不是“多跑几遍取多数”，而是偏置独立的多角度逼近：

- **偏置独立多角度**：thinking 和 review worker 带不同先验，同轮封存 verdict 前不得读取彼此输出。
- **meta-judge 收敛**：分歧只收敛到固定出口：达成共识、兼容的 concrete plan，或诚实暴露的有界停滞。
- **concrete plan 必过共识闸**：即使方向明显，实现前仍需独立验证。
- **验证侧同构**：产物经独立 reviewer 和固定 review truth table 后才能宣告完成。
- **纯编排**：caller 只派发 worker、汇总有界结论；实现、验证、修复和设计求解交给 worker。

## 协议速览

[协议](./skills/sshx/SKILL.md#inlineconsensusprotocol) 按顺序运行七个阶段。caller 负责 intake、派发、收敛和报告；worker 负责思考、实现和 review。

| 阶段 | 职责 |
|---|---|
| `intake` | 记录用户目标、约束、成功标准和 host 能力。 |
| `choose_worker_mode` | 派发前检查可用的 worker carrier。 |
| `thinking_panel_workers` | 用六个隔离视角检验方案。 |
| `meta_judge` | 按[思考真值表](./skills/sshx/SKILL.md#design-truth-table)收敛结论。 |
| `implementation_worker` | 委派已批准的结果与范围。 |
| `review_triplet_workers` | 独立评审完成后的候选产物。 |
| `fix_or_done` | 通过既有闸门路由修复、后续 review 或完成。 |

[思考面板](./skills/sshx/SKILL.md#thinking-panel) 为每个席位分配不同目标：

| 思考席位 | 视角 |
|---|---|
| `teleology` | 目的及其要求的形式。 |
| `parsimony` | 必需的机制数量。 |
| `fidelity` | 有来源验证的前提与有代表性的证据。 |
| `natural-ownership` | 自然承担职责的层级。 |
| `proportional-containment` | 有依据的范围、权限与持续时间。 |
| `worth` | 收益是否值得成本。 |

[评审三席](./skills/sshx/SKILL.md#review-triplet) 应用[评审真值表](./skills/sshx/SKILL.md#review-truth-table)：

| 评审席位 | 关注点 |
|---|---|
| `architecture` | 边界、合同、耦合与可维护性。 |
| `quality` | 行为、边界情况、失败与用户影响。 |
| `tests` | 覆盖、确定性与验证强度。 |

只有 host 在[目标合同](./skills/sshx/SKILL.md#goal-contract)中明确声明了目标驱动的继续执行机制，且 caller 正准备肯定目标已满足时，最后阶段内部才适用[终止闸门](./skills/sshx/SKILL.md#termination-gate)。它不是第八阶段，也不授予控制 host 机制的权限。

## 风险与限制

- **宽执行权限：** Codex runner 默认使用 `danger-full-access`。提供工作目标前，请阅读[调用合同](./skills/sshx/CODEX_WORKER_SPEC.md#invocation)和[威胁模型](./skills/sshx/CODEX_WORKER_SPEC.md#threat-model)。
- **成本与时长：** 完整的初始运行至少派发 10 次模型调用：六个思考席、一个实现 worker、三个 reviewer。修复、聚焦轮、重复 review 及适用的终止检查会增加工作。**默认不设置 `pass_budget` 上限**；详见[协议](./skills/sshx/SKILL.md#inlineconsensusprotocol)、[思考面板](./skills/sshx/SKILL.md#thinking-panel)、[评审](./skills/sshx/SKILL.md#review-triplet)和[轮次计费规则](./skills/sshx/SKILL.md#fix-or-done)。
- **内容外发：** oracle 席将内联 brief 经 NyxID 发送到外部 ChatGPT Pro 账号。派发前应考虑 brief 中的内容；详见[worker 委派](./skills/sshx/SKILL.md#worker-delegation)和 oracle 的[请求合同](./skills/sshx/ORACLE_WORKER_SPEC.md#request)。
- **产物留存：** brief、日志和结果会积累在 `${SSHX_HOME:-$HOME/.sshx}/<flight-id>/attempt-<n>`，直到清理或裁剪。详见[运行目录](./skills/sshx/CODEX_WORKER_SPEC.md#run-directory)与[不活跃 flight 清理](./skills/sshx/CODEX_WORKER_SPEC.md#inactive-flight-sweep)；过短的保留窗口可能删除仍在静默运行的 flight。
- **Host 责任：** 超时与整个任务进程树的终止由 host harness 负责。[终止前提](./skills/sshx/CODEX_WORKER_SPEC.md#teardown-prerequisite)尚未验证 Codex、Cursor 和 Gemini host 的 teardown 行为；另见[委派合同](./skills/sshx/SKILL.md#worker-delegation)。
- **合同变化：** [包元数据](./package.json)表明当前处于预发布阶段。合同可能变化，更新时请检查 [skill](./skills/sshx/SKILL.md) 与[贡献指南](./CONTRIBUTING.md)。
- **产物不可信：** 共识仍是需要独立验证的 AI 产物（[仓库不动点 FI-001](./CLAUDE.md#共识研发不动点)）。上下文隔离不能证明先验独立或结果更好（[委派限制](./skills/sshx/SKILL.md#worker-delegation)）。[Lean 模型](./skills/sshx/formal/README.md#what-the-model-cannot-verify)检查形式对象，不能证明证据真实或实际运行合规；模型与文字是否对应仍需人工判断。

## 快速开始

### 前提与 carrier

Host 必须为 runner 派发提供带完成通知的后台执行、超时和整个任务的 teardown。[Worker 委派](./skills/sshx/SKILL.md#worker-delegation)负责能力检查、席位分配、有界重试与 fallback。

| Carrier | 前提与职责 |
|---|---|
| `codex-cli` | Bash、`jq`、可执行且可用的 Codex CLI，以及可写的运行根目录和提供的工作目标。每次启动都经过 [Codex runner](./skills/sshx/CODEX_WORKER_SPEC.md#carrier)。 |
| `heca` | Python 3、路径投影所需的 Bash 与 `jq`，以及连接到已运行 daemon 的 Heca CLI；工作目标须匹配已有 heca-owned placement，并显式提供经验证的无人值守绑定。详见[使用 Heca Worker](#使用-heca-worker)与 [Heca runner](./skills/sshx/HECA_WORKER_SPEC.md#scope-and-invocation)。 |
| `nyxid-oracle` | Python 3、路径投影所需的 Bash 与 `jq`，以及可用的 NyxID CLI；broker 须有包含在线 worker 的 active pool。[Oracle runner](./skills/sshx/ORACLE_WORKER_SPEC.md#invocation)负责派发；另见 [pool 选择](./skills/sshx/ORACLE_WORKER_SPEC.md#pool-choice)与[登录诊断](./skills/sshx/ORACLE_WORKER_SPEC.md#broker-diagnostics)。 |
| `isolated-token-subagent` | Host 提供隔离 token 上下文的 subagent 能力；同轮席位返回各自 verdict 前不得读取彼此输出。 |
| `abstain` | 无 carrier 可用，或必需 flight 耗尽重试与 fallback 时诚实停止；它是结果，不是执行 carrier。 |

每个多席阶段初始分配一个隔离 subagent 席、一个 oracle 席，其余为 Codex CLI；检测到 heca 时，从 Codex 席中分一席给它，并受席位约束与 fallback 规则限制。tests reviewer 必须能在工作目标中执行验证。仅凭 carrier 分配和隔离不能证明模型多样性，详见[所属规则](./skills/sshx/SKILL.md#worker-delegation)。

#### 使用 Heca Worker

`sshx` 支持与 `codex-cli`、`nyxid-oracle` 并列的 `heca` worker 载体。非变更能力检查检测到 heca 时，每个多席阶段从 Codex 余量中分一席给它；heca 缺席时，既有载体组成与回退不变。单 worker 阶段仍从 Codex 开始，只经回退使用 heca。

在已有的 heca-owned placement 中照常运行 `sshx`，例如在 heca 中打开的主 checkout，或 heca 创建的 worktree。heca CLI 必须能连接到已运行的 daemon，host 必须显式提供经验证的无人值守绑定：`claude` / `bypassPermissions` 或 `codex` / `full-access`。目标没有匹配的已有 placement 时，不能使用该载体。机械细则与已记录的验证边界见 [`HECA_WORKER_SPEC.md`](./skills/sshx/HECA_WORKER_SPEC.md)。

### Claude Code

安装 marketplace 和插件后，带上任务调用 skill：

```text
/plugin marketplace add ChronoAIProject/consensus-rnd
/plugin install consensus-rnd@consensus-rnd
/consensus-rnd:sshx <task>
```

插件身份由 [marketplace](./.claude-plugin/marketplace.json) 和[插件清单](./.claude-plugin/plugin.json)声明。

### Codex / Cursor

[Codex](./.codex-plugin/plugin.json) 与 [Cursor](./.cursor-plugin/plugin.json) 清单都声明 `"skills": "./skills/"`。通过平台插件支持加载仓库并阅读 [skill 合同](./skills/sshx/SKILL.md)；安装与调用细节取决于 host。

### Gemini CLI

[扩展清单](./gemini-extension.json)选择 `GEMINI.md` 作为上下文文件。[GEMINI.md](./GEMINI.md) 列出可用 skill，并指示 agent 按需读取合同。

## 架构

| 层面 | 事实源 |
|---|---|
| 公开身份与入门 | 本文与[英文 canonical](./README.md)。 |
| 仓库规则与结构 | [CLAUDE.md](./CLAUDE.md)，同时由受版本控制的 `AGENTS.md` 符号链接提供。 |
| 协议与行为合同 | [SKILL.md](./skills/sshx/SKILL.md)。 |
| Codex 执行与产物维护 | [CODEX_WORKER_SPEC.md](./skills/sshx/CODEX_WORKER_SPEC.md) 与 [scripts](./skills/sshx/scripts/)。 |
| Heca worker 传输 | [HECA_WORKER_SPEC.md](./skills/sshx/HECA_WORKER_SPEC.md)。 |
| NyxID broker 派发 | [ORACLE_WORKER_SPEC.md](./skills/sshx/ORACLE_WORKER_SPEC.md)。 |
| 形式证据与局限 | [形式模型 README](./skills/sshx/formal/README.md)。 |
| 平台入口 | [Claude marketplace](./.claude-plugin/marketplace.json)、[Claude 插件](./.claude-plugin/plugin.json)、[Codex](./.codex-plugin/plugin.json)、[Cursor](./.cursor-plugin/plugin.json) 和 [Gemini](./gemini-extension.json)。 |
| 清单版本映射 | [.version-bump.json](./.version-bump.json) 与 [bump_version.py](./.github/scripts/bump_version.py)。 |
| 仓库验证 | [CI workflow](./.github/workflows/consensus-rnd-ci.yml)、[仓库测试](./.github/tests/test_repository_docs.py) 和 [skill 测试](./skills/sshx/tests/)。 |

## 参与贡献

开发前提、本地检查和 maintainer 负责的发布步骤见 [CONTRIBUTING.md](./CONTRIBUTING.md)。仓库维护指南使用中文。

## License

[MIT](./LICENSE) © ChronoAIProject
