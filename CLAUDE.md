# consensus-rnd — Agent 工作指南

本文件给**在本仓库内工作的 agent**(增改 skill、维护清单、发版)看;不是给 host 项目运行时用的。host 运行时事实由 `host.env` 注入,见各 skill 的 SKILL.md。

仓库定位与共识引擎设计哲学见 [`README.md`](./README.md);本文件是 agent 工作宪法,与 README 不重复。术语定义与项目当前状态归各 skill 的 SKILL.md / REFERENCE.md,不在本文件维护。

## 仓库性质

这是一个**跨平台 Agent Skills 发布仓库**,不是应用代码仓库。唯一产物是 `skills/<name>/` 下的 `SKILL.md` 及其配套文件。同一份 `skills/` 被 Claude Code / Codex / Cursor / Gemini 共享,各平台只靠根目录的清单文件指向它。

## 目录约定

```
.
├── .claude-plugin/        # Claude Code:plugin.json + marketplace.json
├── .codex-plugin/         # Codex:plugin.json
├── .cursor-plugin/        # Cursor:plugin.json
├── gemini-extension.json + GEMINI.md   # Gemini:扩展清单 + 上下文入口
├── package.json           # npm 风格元数据 / 版本锚点
├── AGENTS.md → CLAUDE.md  # 跨 agent 约定(符号链接)
├── LICENSE                # MIT
├── skills/<name>/         # 各 skill(SKILL.md 必备)
└── .version-bump.json     # 版本号同步映射
```

## 设计哲学(跨 skill 不动点)

通用于本仓库内所有 skill 设计与变更:

- **单一主干,插件扩展**:每个 skill 一条权威主链路;新能力以子模块/脚本挂载,禁止平行第二系统。
- **内核最小化**:SKILL.md 只承载稳定不变量与触发条件;可机械化、可参考化的部分下沉到 `scripts/` / `prompts/` / `REFERENCE.md`。
- **边界清晰,职责分层**:本文件承载**跨 skill 边界**与**仓库级宪法约束**;单个 skill 的工作流细则、术语定义、当前状态归该 skill 自维护,不复制回本文件。
- **事实源唯一**:同一约束禁止在多处平行声明。版本号 → `.version-bump.json`;host 运行时事实 → `host.env`;skill 行为 → 该 skill 的 SKILL.md 与 `scripts/test_*.py`。
- **抽象优先,行为契约**:skill 间通过 `host.env` + 文件 artifact + GitHub API 等稳定边界协作,不耦合彼此内部脚本;命名跟随职责,不泄露 runtime / 内部实现细节。
- **强类型边界,窄扩展点**:任何 controller-runtime 例外必须 narrow allowlist + no lifecycle authority by default;授权来源必须 durable artifact + 仓库级文档双重锚定。#53 唯一 carveout 是 `integration sync daemon` 在专用 integration worktree 内的 integration-branch git allowlist(`git fetch` / `rev-list` / `rev-parse` / `merge-base` / `reset --hard` / `rebase --rebase-merges` / `merge --ff-only|--no-ff` / `push HEAD:$INTEGRATION_BRANCH` / force-with-lease adoption),不得 commit worker diff、create/merge/close PR、开关 issue/PR/label、tag/release,不得作为 generic lifecycle actor;通用授权、escape hatch、宽口径修宪一律视为设计未完成。
- **抽象一旦能被滥用即设计未完成**:允许绕过审查边界、merge gate、CLAUDE.md 修宪门槛的通用机制必须继续收窄。
- **删除优先**:废弃 skill、deprecated wrapper、`*.bak/*.old/*.deprecated` 直接删除,不保留兼容空壳;历史由 git 与 CHANGELOG 保留。
- **变更必须可验证**:行为约束必须落到机械验证手段(behavior test / source-regression test / 段落 lint);仅靠"agent 应该记得"承载的约束视为未落地。
- **治理前置**:架构性 / 流程性规则与对应机械验证手段同时进仓库,缺一不补口径。
- **正确架构优先**:架构在 skill 增长(更多 work-unit / 更多并发 worker / 更多 reviewer 角色)时若自然变松,说明架构本身不正确,要重设计而非加 escape hatch。
- **命名跟随职责**:文件、目录、脚本、marker、artifact 的名字表达职责边界,不泄露偶然 runtime / 临时实现 / 当前 issue 编号。
- **哲学文档不写版本后缀**:`V1`/`V2`/`schema` 版本字段后缀 之类的 schema/identifier 版本号属于代码内部演化坐标,**不**进哲学文档(`CLAUDE.md`/`README.md`/`SKILL.md` 哲学段)。哲学描述不变量与边界,不绑定当前版本号;一旦哲学文本要随 schema 改版而改,说明版本号泄露到了不该泄露的层。Release semver(`.version-bump.json` 映射的 package/manifest 版本)是另一回事——那是发布坐标,不是设计 identifier。

## 共识引擎哲学(本仓库唯一产品身份)

权威表述见 [`README.md`](./README.md)「核心」段;此处只述跨 skill 不动点:

- **偏置独立多角度**:同一决策点的多 solver / 多 reviewer **互相看不到对方输出**,各自带先验立场独立得出结论;禁止串行"先看 A 再写 B"或单源冒充多源。
- **meta-judge 收敛**:分歧只收敛到固定数量的出口语义(达成 / 接近 / 真停滞);真停滞才升级到 meta-layer 调和,不直接升级到人。
- **concrete plan 必过共识闸**:哪怕方向已明确,也不允许单个 agent 直接落地;用多角度验证把"明显方向"证成"明显方案"。
- **验证侧同构**:产物必须再经多 reviewer 共识 gate 才允许 merge;固定真值表语义(明确 reject 即拦,否则按既定规则放行),advisory 反馈不计入 approval。
- **controller 纯编排**:controller 只 route / post / label / spawn / commit / push / merge;实现 / 验证 / 修复 / review / design solve 全部 delegate 给 agent worker。可机械化、状态确定的部分可经多角度共识授权后下沉为 narrow allowlist daemon。
- **不伪造共识**:不得用单 agent / 单 reviewer 输出代替 multi-perspective gate。
- **人工介入要诚实**:只有确实需要人做产品、战略、治理或权限决策时,才把状态升级给 maintainer;不把人当作 reviewer 反复 reject 的兜底出口。

## 角色与边界

- **maintainer(人)**:产品/战略决策、治理级非可编码变更的授权、罕见手工 merge。
- **agent(controller LLM)**:纯编排;长跑中读 daemon 维护的 counts / state / artifact,不重新自测。
- **agent worker**(被派发的 codex / 其他 CLI 实例):承担所有思考密集工作 —— 实现、验证、修复、review、design solving。每个 worker 在隔离 worktree 内运行。
- **daemon(`scripts/` 后台)**:可机械、状态确定的 controller 工作的实现载体。Daemon 是经共识授权的 narrow allowlist 例外,默认**不**持 lifecycle authority(不开关 issue/PR/label、不 commit/push/merge/tag/release publish);仅 #53 授权 `integration sync daemon` 在专用 integration worktree 内执行 integration-branch git allowlist。Implement/fix worker 仍不得 commit、push、open PR、merge、close issue/PR。
- **host 项目**:消费 skill 的下游项目。skill **无 host 项目改动权**:不修改 host 的 `.git` 配置 / CI 配置 / policy 文档;只在 `host.env` 暴露的 surface 上工作。host opt-in 缺失或为假时,所有相关 surface 静默 noop(`exit 0` + reason)。

## 新增 / 修改 skill

- 每个 skill 一个目录:`skills/<kebab-name>/SKILL.md`。
- frontmatter 仅需 `name` + `description`(全文 ≤1024 字符)。`description` 以 "Use when..." 描述**触发条件**,不要复述工作流。
- 重型参考拆 `REFERENCE.md`;脚本放 `scripts/`;prompt 模板放 `prompts/`。
- 写 / 改 skill **必须**遵循 `superpowers:writing-skills` 的 TDD 纪律:先用子 agent 跑 baseline(看它在没有 skill 时怎么失败),再写 / 改 skill。
- 行为变更必须配套 **behavior test**(断言行为本身)+ **source-regression test**(对 SKILL.md 段落标题、narrow allowlist 字面、授权来源 path 等做字面断言),防止"改文档没改实现"或反之。
- 新增后台脚本或 runtime surface 必须显式说明:**允许做什么 / 不允许做什么 / 事实源在哪里 / 如何验证**;缺任一项视为未完成。
- skill 间通过 `host.env` + 文件 artifact + GitHub API 等稳定边界协作,**不**耦合彼此内部脚本。

## Agent 工作约定(跨 skill 不动点)

- **daemon-first**:可机械、状态确定、长期运行的 controller 工作放进 `scripts/` 后台 daemon(LLM 长时间运行会退化,脚本不会)。
- **信任 daemon 计数 / 状态**:controller LLM 只读 daemon 维护的 counts / state / artifact;**禁止** controller 重新 `ps | grep` 自测或重实现 daemon 算法。Daemon 是测量与状态的权威。
- **不弹 popup**:仓库内 skill 矩阵相关的决策(改 skill、改清单、改本文件、改 `.version-bump.json`)由 agent 自决,**不** `AskUserQuestion`。宪法级争议走 meta-layer self-check + 小规模 rule PR,不走 popup。
- **Skill routing 优先**:能匹配仓库内现有 skill 的请求一律走对应 skill;skill 自包含的操作细则不复制回本文件。
- **artifact 路径相对 `$REPO_ROOT`**:不硬编码 host 路径,不引入具体 host 事实。
- **controller worktree 统一位置**:放在 `<repo-root>/.worktrees/<name>/`(gitignored),**不**创建 sibling `<repo>-wt-*` 目录。
- **最小权限动作**:没有明确授权时,agent 不修改 host 配置、不发布 release、不关闭外部状态面、不执行不可逆生命周期动作。

## 版本同步(强制)

改版本号时,`.version-bump.json` 列出的所有文件必须同步为同一版本:`package.json`、`.claude-plugin/plugin.json`、`.claude-plugin/marketplace.json`、`.codex-plugin/plugin.json`、`.cursor-plugin/plugin.json`、`gemini-extension.json`。漏改任一份会让某个平台装到旧版。

## 版本迭代(规范)

发版坐标走 semver,迭代有固定阶梯与触发,**不临时拍版本、不在红信号上发**:

- **预发布阶梯**:`X.Y.Z-beta.N` → `X.Y.Z-rc.N` → `X.Y.Z`(GA)。同阶位只递增 `N`;升阶位(beta→rc→GA)需 release gate 连续达标且无回归证据。
- **gate 驱动,不手动选号**:发版由 release gate 全绿(信号全过)+ host opt-in(`RELEASE_AUTO_ENABLE=true`)触发;版本号由上一发布坐标机械递推,禁止临时手选或跳号。
- **不在红信号上发版**:双分支(`$REVIEW_BASE_BRANCH` + `$INTEGRATION_BRANCH`)required checks 全绿、稳定性信号(含 P0 streak)已清才发;gate 红即不发,先修再发。
- **坏版即弃,不动已发 tag**:已发布 tag 不可移动 / 复用 / 回填;发版提交若有缺陷,修复后以**下一个预发布号取代**(坏 `beta.N` → `beta.N+1`),旧 tag 保留为历史。
- **tag 必须指向绿提交**:tag 只能打在 required checks 已绿的提交上,且该提交的 manifest 版本与 tag 号一致(承「版本同步」)。

## 工程约定(精简)

- **文档分层**:`README.md` 是仓库定位与共识引擎设计哲学权威源;`CLAUDE.md` 是 agent 工作宪法;`skills/<name>/SKILL.md` 是该 skill 的契约;`skills/<name>/REFERENCE.md` 放重型参考。三者职责不重叠,改动只更新对应一处。
- **根目录 `.md` 收口**:仅保留 `CLAUDE.md`、`README.md`、`AGENTS.md`(符号链接,内容同 `CLAUDE.md`)、`LICENSE`、`GEMINI.md`、`CHANGELOG.md`(若有)。
- **不保留历史副本**:废弃文件直接删除,不留 `.bak/.old/.deprecated`;历史由 git 保留。
- **Git**:分支名描述意图;提交信息祈使句聚焦单一目的;PR 写明动机、影响范围、验证命令与结果。
- **CI / 守卫**:任何 controller-runtime 例外(narrow allowlist daemon、observability、decision-artifact 等)必须配套机械验证手段(behavior test + source-regression test)。
- **跨 platform 清单 lint**:发版前比对 `.version-bump.json` 列出的所有文件版本一致;比对各 plugin manifest 列出的 skill 列表与 `skills/` 子目录一致。
- **测试按风险扩展**:窄文档改动可用 source-regression 覆盖;共享脚本或跨 skill 流程改动必须补 behavior test。
- **生成物不当事实源**:临时日志、一次性报告、agent 草稿、运行输出不得成为长期规范来源;权威源永远在 SKILL.md / 脚本 / 测试里。
- **历史由 git 保存**:需要追溯旧行为时查 git,不在工作树保留影子副本或归档目录。
