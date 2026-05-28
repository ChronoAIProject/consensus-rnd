# consensus-rnd

通用**共识式研发** skills 库 —— 可被任意 host 仓库注入的多角度共识构建引擎。

## 定位

这里的"研发"取最广义:**任何持续向仓库提交状态的活动都是研发** —— 写代码是研发,写文档是研发,做 marketing、整理资料、维护配置同样是研发。只要产物落进 git、需要被审查、需要质量保证,就适用同一套引擎。

本库不绑定任何具体项目。host 通过 `host.env` 注入自己的事实(仓库根、集成分支、规则文档、构建/测试命令、GitHub slug 等),引擎本身不写死任一项目。

## 核心:共识构建引擎

不是"多跑几遍取多数",而是**偏置独立的多角度逼近**:

- **多 solver,先验对立**:每个 solver 带不同立场(如 minimal 最小改动 / structural 架构洁净 / delete 质疑必要性),且**互相看不到对方输出**,各自独立得出结论。
- **meta-judge 仲裁**:把分歧收敛成 `consensus` / `converge` / `stalled` 三个出口,不达成一致就继续收敛,直到真停滞才升 reflector 调和。
- **任何 concrete plan 都必须过这道闸**:哪怕方向已明确,也不允许单个 agent 直接落地 —— 用多角度验证把"明显方向"证成"明显方案"。
- **验证侧同构**:产物再经多 reviewer(架构 / 质量 / 测试)共识 gate 才允许合并。
- **controller 纯编排**:所有思考、分析、诊断、验证都 delegate 给 agent;确定性脚本可按 allowlist 读 marker 并派发下一 actor;LLM controller 保留语义 fallback、未知状态、git 与状态面。

## skills

| skill | 说明 | 状态 |
|---|---|---|
| `codex-refactor-loop` | Consensus R&D 循环的稳定 skill 入口;默认以 audit/refactor 作为兼容 intake / producer,codex CLI 驱动,GitHub 为状态面 | 自 host 项目移植,verbatim;保留 refactor 作为合法 work-unit 隐喻 |

## 仓库结构

本库是一个**跨平台 Agent Skills 仓库**,同一份 `skills/` 被多个 coding agent 共享,各平台靠根目录的清单文件指向它:

```
.
├── .claude-plugin/        # Claude Code:plugin.json + marketplace.json
├── .codex-plugin/         # Codex:plugin.json
├── .cursor-plugin/        # Cursor:plugin.json
├── gemini-extension.json  # Gemini:扩展清单(配 GEMINI.md 上下文入口)
├── package.json           # npm 风格元数据 / 版本锚点
├── AGENTS.md → CLAUDE.md   # 跨 agent 约定(符号链接)
├── CLAUDE.md              # 本仓库内工作的 agent 指南
├── LICENSE                # MIT
├── skills/<name>/         # 各 skill(SKILL.md 必备)
└── .version-bump.json     # 各清单版本号同步映射
```

新增 / 修改 skill 的约定与版本同步规则见 [`CLAUDE.md`](./CLAUDE.md)。

## 安装

### Claude Code

```bash
/plugin marketplace add ChronoAIProject/consensus-rnd
/plugin install consensus-rnd@consensus-rnd
```

### Codex / Cursor

按各自插件机制指向本仓库;`.codex-plugin/plugin.json` 与 `.cursor-plugin/plugin.json` 已通过 `"skills": "./skills/"` 暴露 skills。

### Gemini CLI

作为扩展安装,`gemini-extension.json` 以 `GEMINI.md` 为上下文入口,列出可用 skills 并指引按需读取。

### 直接拷贝(任意 agent)

把 `skills/<name>/` 拷进 agent 的个人 skills 目录(如 Claude Code 的 `~/.claude/skills/`)即可。

### 下游 host quickstart

<!--
Refactor (iter1/issue-141):
  Old pattern: 下游没有 installer 时,装机步骤散落在 README、SKILL statusline 段和 restart helper 段,缺乏从安装 skill 到配置 host.env、调度守护进程、接入 statusLine 的单步 walkthrough。
  New principle: Downstream install walkthrough 是唯一装机主段;README 链到 SKILL 锚点,SKILL 内部段落互链;source-regression 锁住单文件链接与必备 surface,bounded scheduler behavior test 锁住 restart-daemons.sh 不无限阻塞。
-->

`codex-refactor-loop` 的 host 安装顺序集中在
[`Downstream install walkthrough`](./skills/codex-refactor-loop/SKILL.md#downstream-install-walkthrough)。
按该 walkthrough 安装 skill、复制并填写 `.refactor-loop/host.env`、配置用户级 cron/launchd 和 Claude Code `statusLine`;README 不复制命令矩阵。

## 泛化路线(待迭代)

第一块是直接移植,仍带"重构"外壳与少量 host 主张。后续迭代方向:

1. **抽出引擎脊柱**(`solve → consensus → implement → verify`),让 audit 这一步(产生工作单元的种子)可替换 —— 换成任意 work-unit 来源(设计提案 / 文档任务 / 营销产出 / spec 变更),其余整套共识机制原样复用。
2. **参数化漏入的 host 主张**:如"工作语言规则"应成为可注入的 host policy,而非写死。
3. 对外以 "Consensus R&D" 为主产品身份; 保留 codex-refactor-loop 作为稳定 skill 入口,因为 maintainer 已接受 "refactor = development" 通用隐喻; 不新增重复 alias skill,除非未来出现真实平台发现/安装问题。

> 泛化引擎本身宜走引擎自己那套共识 gate —— 用这个引擎来通用化这个引擎。

## License

[MIT](./LICENSE) © ChronoAIProject
