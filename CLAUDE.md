# consensus-rnd — Agent 工作指南

本文件给**在本仓库内工作的 agent**(增改 skill、维护清单、发版)看;不是给 host 项目运行时用的。host 运行时事实由 `host.env` 注入,见各 skill 的 SKILL.md。

仓库定位与共识引擎设计哲学见英文 canonical [`README.md`](./README.md);中文 companion 见 [`README.zh-CN.md`](./README.zh-CN.md)。本文件是 agent 工作宪法,与 README pair 不重复。术语定义与项目当前状态归各 skill 的 SKILL.md / REFERENCE.md,不在本文件维护。

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
├── README.md + README.zh-CN.md  # 英文 canonical 公开身份文档 + 中文 companion
├── LICENSE                # MIT
├── skills/<name>/         # 各 skill(SKILL.md 必备)
└── .version-bump.json     # 版本号同步映射
```

## 设计哲学(跨 skill 不动点)

通用于本仓库内所有 skill 设计与变更:

- **单一主干,插件扩展**:每个 skill 一条权威主链路;新能力以子模块/脚本挂载,禁止平行第二系统。
<!--
Refactor (iter319/issue-319): Old pattern: CLAUDE.md 把『重型参考必须物理拆 REFERENCE.md』当宪法,audit 据此反复把单文件 SKILL.md 判 R02/R03 违规
New principle: 改哲学:单文件 SKILL.md + intra-file anchors 是被认可的 canonical reference surface;衡量标准从『必须物理拆文件』改为『事实源唯一+owner surface 清楚+anchor 可验证』
-->
- **内核最小化**:SKILL.md 必须承载触发条件、稳定不变量、controller 合同与该 skill 的行为事实源索引;可机械化部分下沉到 `scripts/`, prompt 模板下沉到 `prompts/`。重型参考默认可下沉到 `REFERENCE.md`,但若跨平台 agent 加载或维护实证显示物理拆分退化,允许单文件 SKILL.md 同时承载 controller 合同与详细参考,用 intra-file anchor 分层并由 source-regression 锁住事实源唯一性。
- **边界清晰,职责分层**:本文件承载**跨 skill 边界**与**仓库级宪法约束**;单个 skill 的工作流细则、术语定义、当前状态归该 skill 自维护,不复制回本文件。
- **事实源唯一**:同一约束禁止在多处平行声明。版本号 → `.version-bump.json`;host 运行时事实 → `host.env`;skill 行为 → 该 skill 的 SKILL.md 与 `scripts/test_*.py`。
- **抽象优先,行为契约**:skill 间通过 `host.env` + 文件 artifact + GitHub API 等稳定边界协作,不耦合彼此内部脚本;命名跟随职责,不泄露 runtime / 内部实现细节。
- **强类型边界,窄扩展点**:任何 controller-runtime 例外必须 narrow allowlist + no lifecycle authority by default;授权来源必须 durable artifact + 仓库级文档双重锚定。#53 是唯一 integration-branch git carveout:`integration sync daemon` 在专用 integration worktree 内的 integration-branch git allowlist(`git fetch` / `git ls-remote --exit-code --heads origin $INTEGRATION_BRANCH` / `rev-list` / `rev-parse` / `merge-base` / `reset --hard` / `rebase --rebase-merges` / `merge --ff-only|--no-ff` / `push HEAD:$INTEGRATION_BRANCH` / force-with-lease adoption),不得 commit worker diff、create/merge/close PR、开关 issue/PR/label、tag/release,不得作为 generic lifecycle actor。#191 是唯一跨设备 active-controller lease carveout:GitHub/已 push git 面只承载一个全局 `ActiveControllerLease`,允许 read/acquire/renew 专用 lease artifact 并暴露 owner/expiry;禁止 worker diff commit、issue/PR create/merge/close/edit、label mutation、tag/release、per-work claim、host-defined lease scope、跨设备 floor 聚合、daemon ownership matrix、active-active scheduler、generic lifecycle actor。#193 中 issue/PR author.login 与 updatedAt 仅可作为 planning/routing/stale metadata,不得作为 side-effect authorization、per-work owner authority、claim/lease scope 或 takeover permit;issue/PR target 写副作用的跨设备 permit 只来自 #191 ActiveControllerLease。authenticated GitHub API caller/token、repo permission、branch protection/ruleset/CODEOWNERS/required-review results 只能作为 #191 owner-gated GitHub API mutation 的必过 preflight facts;不得成为 per-work owner、claim/lease scope、daemon owner、takeover permit、action-specific lifecycle authorization,也不得绕过 #191/#238/#322/#396/#403/#437/#541/#623。#238 是唯一 closed managed item phase-label reconciliation carveout:active-controller owner 的 checked-in `closed-label-reconciler` 只可对 CLOSED `crnd:lifecycle:managed` issue/PR 做 phase-label reconciliation,移除 phase/cleanup/stuck label 并加 exactly one terminal phase `crnd:phase:merged` 或 `crnd:phase:closed`;禁止 open item mutation、issue/PR create/close/reopen/body/title edit、PR merge、human/triage/milestone/lifecycle label mutation、tag/release、generic lifecycle actor。#322 是唯一 controller-owned release publication carveout:active-controller owner 的 `ReleasePublisher` 只可在 `ReleasePublishPreflight` 验证 `RELEASE_AUTO_ENABLE=true`、fresh release-candidate/release-decision、decision_digest、target_ref、mapped manifest from_version、required checks 全绿后,走同一 publish 主链路:首次发布运行 `python3 .github/scripts/bump_version.py --version <to_version>`、`git add .version-bump.json <mapped manifests>`、`git commit -m "Release v<to_version>"`;already-bumped reentry 仅当 only preflight mismatch 是 mapped manifests 已==`to_version` 且 `git show -s --format=%s HEAD` 证明 HEAD subject 精确为 `Release v<to_version>` 时跳过这三步。remote already-bumped reentry 仅可只读运行 `git fetch origin <INTEGRATION_BRANCH>`、`git rev-parse origin/<INTEGRATION_BRANCH>`、`git show -s --format=%s origin/<INTEGRATION_BRANCH>`、`git show origin/<INTEGRATION_BRANCH>:<mapped manifest>` 证明远端 subject 精确为 `Release v<to_version>` 且远端 mapped manifests 已==`to_version`;若 integration branch head 不是精确 release commit,可额外只读 `git for-each-ref --format=%(refname:short) refs/remotes/origin/rollup`、`git rev-parse origin/rollup/<40hex>`、`git show -s --format=%s origin/rollup/<40hex>`、`git show origin/rollup/<40hex>:<mapped manifest>` 证明 suffix 等于 resolved commit sha、subject 精确为 `Release v<to_version>` 且 mapped manifests 已==`to_version`,随后跳过本地 bump/add/commit/push,只对该 remote integration/rollup release sha 走 exact-SHA checks gate + release create。本地两条路径随后都必须运行 `git rev-parse HEAD`、`git fetch origin HEAD`、`git rev-list --count HEAD..origin/HEAD`、`git push origin HEAD`、通过 `ReleaseRequiredChecksProjection` 读取 `gh api repos/<slug>/commits/<fresh release commit sha>/check-runs --paginate --slurp` 或 reentry 的 `gh api repos/<slug>/commits/<exact release/reentry commit sha>/check-runs --paginate --slurp` 并确认该 exact fresh SHA required checks 全绿后才生成 controller-private release notes file(消费 `.refactor-loop/state/release-commits.json`,过滤 mechanical integration artifacts 并 surface referenced work)并运行(或 reentry 时确认该 exact fresh/reentry SHA required checks 全绿后才生成该 notes file 并运行) `gh release create v<to_version> --target <fresh release commit sha> --notes-file <controller-generated release notes file> [--prerelease]` 或 reentry 的 `gh release create v<to_version> --target <exact release/reentry commit sha> --notes-file <controller-generated release notes file> [--prerelease]`,并写 `.refactor-loop/state/release-publish-result.json`;该 notes file 不是 public CLI、workflow release authority、tag authority、release edit/delete/upload authority、issue/PR/label lifecycle authority 或 host production SSOT;禁止 public release-publish CLI、workflow tag/release creation、tag target without exact-SHA green checks、`git tag`、force-push、release edit/delete/upload、approval-ticket/emoji gate、issue/PR/label lifecycle、merge/close、generic lifecycle actor。#396 是唯一 unattended wakeup-runner carveout:active-controller owner 的 checked-in `wakeup-runner` 只可消费 `wakeup-plan` 产出的 evidence-bound closed action projection,并对每个 action 重新验证 clean `EXIT=0` source marker、review truth table、OPEN/live GitHub state、#191 owner、release #322 preflight 或 helper-specific precondition 后,机械调用既有 controller helper 或 #396 narrow helper。`wakeup-plan` 是唯一 action projection fact source但不是 standalone authorization source;daemon 不得读 prompt body 决策,不得新增 `ControllerTurnDecision`/controller-turn worker/schema,不得接受 argv/shell/cmd/command_line/commands/env/git/gh/executor/lifecycle_authority/lifecycle_owner/generic command fields,不得把 `.refactor-loop/host.env` 当 host production SSOT。允许动作仅限 spawn codex, including allowlisted release-rollup body generation that only writes `.refactor-loop/runs/release-rollup-pr-body.md`, named helper `dispatch_consensus_implementation`、named helper `publish_implementation_output`、named helper `apply_issue_decomposition_plan`、named helper `apply_default_issue_intake_claim`、then named helper `open_release_rollup_pr_from_action` after the body exists、publish worker output、dispatch reviewers/fix/remote-ci worker、apply triage decision、merge PR under review truth table、close managed item from drop marker、publish release through #322;`apply_issue_decomposition_plan` named action 只来自 plan-level judge artifact 的结构字段 + validated plan digest/proof + #191 owner + live parent open/tracking + sentinel idempotency,第一次 `consensus:decompose`、solver artifact、prompt body、validator、`.refactor-loop/host.env` 均不是授权来源;禁止任意 git/gh 命令、workflow tag/release、router guard adjudication、generic codex fallback、prompt-body decision、command fields、new lifecycle authority、label/merge/close outside existing helper or named #396 helper、active-active scheduler、generic lifecycle actor。#403 是唯一大 issue 分解 carveout:active-controller owner 的 checked-in apply helper 只可消费已验证的 `IssueDecompositionPlan`,创建 `crnd:lifecycle:managed` child design issues 并评论父 issue;父 epic 保持 open/tracking,禁止 close/reopen/body-title edit,禁止 daemon/worker 建 issue、public issue factory、除 #396 evidence-bound named `controller_action="apply_issue_decomposition_plan"` 外的 wakeup-plan decompose/status/generic 投影或 generic lifecycle actor。#437 是唯一 skill-private runtime-retention local-GC carveout:active-controller owner 的 checked-in `RuntimeRetention` helper 只可在 host opt-in 后 same-inode compact `$REPO_ROOT/.refactor-loop/.controller-pending-events.log` and `$REPO_ROOT/.refactor-loop/.concurrency-alert.log`、对 `$REPO_ROOT/.worktrees/<name>` 中 no in-flight/no OPEN issue-or-PR/no dirty/no local-ahead/merged-or-missing-safe 的 stale worktree 运行 `git worktree remove <path>` 与 `git worktree prune`,并可 unlink regular `$REPO_ROOT/.refactor-loop/locks/spawn-tasks/<safe-task-id>.lock` only when valid metadata matches basename, `log_path` resolves under `$REPO_ROOT/.refactor-loop/logs/`, TTL elapsed, and matching log has terminal `EXIT=` marker;missing-log/companion/plan proof、dead-holder/no-in-flight、GitHub terminal-state、path-escape、unreadable、malformed、non-regular、unsafe-basename、young、unlink-failure cases only keep with diagnostics;`$REPO_ROOT/.refactor-loop/{logs,prompts,runs}` worker artifacts 不再是删除 surface,legacy `generated_files` plan entries 只作 inert compatibility input 且无 delete authority;允许 GitHub only read OPEN issue/PR/head state;允许 git only read projection plus `git worktree remove`/`git worktree prune`;禁止 `git fetch`、branch deletion、commit/push/reset/rebase/merge/tag、issue/PR create/edit/close/merge、label mutation、tag/release、archive/index durable fact source、worker artifact deletion、generated_files deletion authority、`RuntimeRetentionPlan.spawn_task_locks`、missing-log companion/plan proof、generic lifecycle actor。#504 是唯一 global dashboard status-card writer carveout:active-controller owner 的 `global-dashboard-status-card` 只可读取既有 status producers,由共享只读 `HolisticStatusProjection` 渲染,并 PATCH exactly one host-configured issue comment;禁止 create comments、edit issue/PR bodies/titles、Discussions、label mutation、create/close/reopen/merge、tag/release、git、new daemon、public writer CLI、generic GitHub writer、standalone dashboard/dependency truth source 或 lifecycle authority。#541 是唯一 patrol-inspector issue-intake carveout:active-controller owner 的 checked-in `patrol-inspector` 只可在 host opt-in 后只读异常日志、runs artifact、wakeup-plan/peek 投影和 GitHub managed item snapshot,生成 patrol-private `PatrolCandidateSignal`,raw log prose 仅可作为 codex prompt diagnostic context,经 `codex exec` analysis gate 写入结构化 `PatrolAnalysisDecision`,且只有 `is_real_issue=true` 才可生成 publishable `PatrolFinding`;analysis spawn 只可读取固定 prompt `prompts/patrol-analysis.md`、写 `.refactor-loop/prompts/patrol-analysis/<signal>.md`、`.refactor-loop/logs/patrol-analysis-<signal>.log`、`.refactor-loop/runs/patrol-analysis/<signal>.json`,非 generic codex fallback,无 git/GitHub/lifecycle authority;随后只可按 fingerprint create/update patrol-owned issue;create 时只可附带固定 patrol/design-intake label bundle,update 仅改该 patrol issue body,public issue body 只可使用 analysis fields 与 fingerprint metadata;禁止修改非 patrol issue/PR、close/reopen/merge、PR edit、label mutation outside create-time fixed bundle、commit/push/tag/release、generic issue factory 或 lifecycle actor。#623 是唯一 default issue intake claim carveout:active-controller owner 的 checked-in `DefaultIssueIntakeAdmission` + `DefaultIssueIntakeClaim` surface:DEFAULT_ISSUE_INTAKE_ENABLE 未关闭时,`DefaultIssueIntakeAdmission` 只读 live GitHub issue state、open managed snapshot、daemon progress/pending spawn intent、skill-private claim cooldown state 与 host knobs,证明 active cap/cooldown/upstream-idle/concrete-work-unit admission 后,wakeup-plan 才可投射 #396 named `controller_action="apply_default_issue_intake_claim"`,且 wakeup-runner 在写 comment/label 前必须重算 admission;`DefaultIssueIntakeClaim` 只可对 admitted live OPEN、non-PR、缺 crnd:lifecycle:managed 的 issue 读取 GitHub issue comments 中的 crnd:default-issue-intake-claim marker,用最早有效 marker 的 GitHub comment createdAt + author.login 作为 first-claimer admission/accounting fact;无更早 claim 时当前 authenticated actor 可写 claim comment 并应用既有 labels.design_issue_label_bundle(),已有更早其他 actor 时只写 crnd:default-issue-intake-stop stop comment;GitHub username/authenticated actor/comment author 在此 surface 内只作 admission/accounting fact,不得成为 #191 lease owner、daemon owner、takeover permit、通用 per-work claim、lifecycle owner 或 lifecycle authority。禁止 issue/PR close/reopen/body/title edit、assignee、milestone、PR merge、tag/release、通用 label mutation、新 daemon、public lifecycle CLI、任何绕过 #191/#396 的 GitHub side effect。通用授权、escape hatch、宽口径修宪一律视为设计未完成。
- **#53 main checkout follow 边界**:integration publish/write authority remains only in the dedicated integration worktree; active-controller owner integration sync daemon may only reuse the narrowed checked-in `safe_sync_main` for main checkout branch==`$INTEGRATION_BRANCH`, tracked-clean, no in-progress git operation, remote-only-ahead `git merge --ff-only origin/$INTEGRATION_BRANCH` follow. It forbids main checkout local-ahead push, rebase, reset, no-ff merge, force-push, branch create/delete, worker diff commit, issue/PR/label/tag/release lifecycle, and any use of main checkout HEAD as publish authorization.
- **#53 adoption rebase continuation 边界**:integration publish/write authority remains only in the dedicated integration worktree; active-controller owner integration sync daemon may run resolved `git rebase --continue` only for typed operation `continue-resolved-rollup-adoption-rebase`, only with adoption artifact evidence from the original `adopt-merged-rollup` operation, matching expected remote SHA, no unresolved paths, replay-integrity verification, and the same `git push HEAD:$INTEGRATION_BRANCH` / force-with-lease push boundary. Forbidden: no generic rebase, rebase abort/cleanup, worker diff commit, issue/PR/label/tag/release lifecycle, and any use of main checkout HEAD as publish authorization.
- **#322 release coordinate policy 边界**:beta 阶位内 core promotion 只能由 fresh release decision/candidate 中 release-gate 机械产出的 `coordinate_policy.transition=beta_core_promotion` 授权;`ReleasePublishPreflight` 必须重算并验证 `from_version`、`to_version`、`bump_type`、core、stage、prerelease index 与 decision/candidate policy 一致。旧 same-stage/GA candidate 可读兼容缺 policy;promotion 缺失、篡改、mismatch 或 stale policy 一律 fail closed,且不新增 git/tag/release/issue/PR/label authority、public release CLI、approval ticket 或 workflow release authority。
- **同仓库多 GitHub username HOLD**:GitHub username、authenticated actor login、comment author、issue author 只能是 read-only display/admission/accounting/routing/status metadata;#623 是唯一 default issue intake claim carveout:active-controller owner 的 checked-in `DefaultIssueIntakeAdmission` + `DefaultIssueIntakeClaim` surface:DEFAULT_ISSUE_INTAKE_ENABLE 未关闭时,`DefaultIssueIntakeAdmission` 只读 live GitHub issue state、open managed snapshot、daemon progress/pending spawn intent、skill-private claim cooldown state 与 host knobs,证明 active cap/cooldown/upstream-idle/concrete-work-unit admission 后,wakeup-plan 才可投射 #396 named `controller_action="apply_default_issue_intake_claim"`,且 wakeup-runner 在写 comment/label 前必须重算 admission;`DefaultIssueIntakeClaim` 只可对 admitted live OPEN、non-PR、缺 crnd:lifecycle:managed 的 issue 读取 GitHub issue comments 中的 crnd:default-issue-intake-claim marker,用最早有效 marker 的 GitHub comment createdAt + author.login 作为 first-claimer admission/accounting fact;无更早 claim 时当前 authenticated actor 可写 claim comment 并应用既有 labels.design_issue_label_bundle(),已有更早其他 actor 时只写 crnd:default-issue-intake-stop stop comment;GitHub username/authenticated actor/comment author 在此 surface 内只作 admission/accounting fact,不得成为 #191 lease owner、daemon owner、takeover permit、通用 per-work claim、lifecycle owner 或 lifecycle authority。禁止 issue/PR close/reopen/body/title edit、assignee、milestone、PR merge、tag/release、通用 label mutation、新 daemon、public lifecycle CLI、任何绕过 #191/#396 的 GitHub side effect。除此窄口径外禁止作为 partition key、per-work claim、lease scope、daemon owner、takeover permit、lifecycle owner 或 lifecycle authority。`owner_device` 继续是唯一 durable active-controller identity;username diagnostics 只可由 `github_actor.py` diagnostics-only helper 读取并写入本地 rebuildable status/snapshot,不得进入 durable lease 或 executable action authority。
- **抽象一旦能被滥用即设计未完成**:允许绕过审查边界、merge gate、CLAUDE.md 修宪门槛的通用机制必须继续收窄。
- **删除优先**:废弃 skill、deprecated wrapper、`*.bak/*.old/*.deprecated` 直接删除,不保留兼容空壳;历史由 git 与 CHANGELOG 保留。
- **变更必须可验证**:行为约束默认由 behavior test 或端到端可观察输入 / 输出 / 副作用验证;source-regression / 段落 lint 仅用于跨 artifact 一致性、授权边界、事实源唯一性、owner-local public / parsed / authorization interface、必备 anchor / path 存在;禁止对纯实现细节做同义反复精确字面断言。若断言只因实现重构而必须同步修改且不验证行为或授权边界,应替换为 behavior test 或语义化的跨 artifact 一致性断言;仅靠"agent 应该记得"承载的约束视为未落地。
- **治理前置**:架构性 / 流程性规则与对应机械验证手段同时进仓库,缺一不补口径。
- **正确架构优先**:架构在 skill 增长(更多 work-unit / 更多并发 worker / 更多 reviewer 角色)时若自然变松,说明架构本身不正确,要重设计而非加 escape hatch。
- **命名跟随职责**:文件、目录、脚本、prompt、marker、label、branch、artifact 的名字表达职责边界,不泄露偶然 runtime / 临时实现 / 当前 issue 编号。任何被脚本解析、路由、daemon 启动、进程探测、GitHub/git 状态面消费、或跨 agent 传递的名字都是 operational interface,必须由所属 owner-local 事实源声明字段顺序、允许字符集、canonical write policy、legacy read / migration policy,并用 behavior test + source-regression 锁住。已存在事实源(如 label catalog、command registry、workflow stage registry、route-local parser)不得被第二套通用命名 registry 或全仓审美 lint 复制。未被脚本消费的一次性历史 artifact 不因风格不统一强制重命名;已发布 public command、marker、label、branch 或 artifact basename 改名必须先有 named migration artifact。
- **哲学文档不写版本后缀**:`V1`/`V2`/`schema` 版本字段后缀 之类的 schema/identifier 版本号属于代码内部演化坐标,**不**进哲学文档(`CLAUDE.md`/`README.md`/`SKILL.md` 哲学段)。哲学描述不变量与边界,不绑定当前版本号;一旦哲学文本要随 schema 改版而改,说明版本号泄露到了不该泄露的层。Release semver(`.version-bump.json` 映射的 package/manifest 版本)是另一回事——那是发布坐标,不是设计 identifier。

## 共识引擎哲学(本仓库唯一产品身份)

权威表述见英文 canonical [`README.md`](./README.md)「Core」段;[`README.zh-CN.md`](./README.zh-CN.md) 是中文 companion。此处只述跨 skill 不动点:

- **偏置独立多角度**:同一决策点的多 solver / 多 reviewer **互相看不到对方输出**,各自带先验立场独立得出结论;禁止串行"先看 A 再写 B"或单源冒充多源。
- **meta-judge 收敛**:分歧只收敛到固定数量的出口语义(达成 / 接近 / 真停滞);真停滞才升级到 meta-layer 调和,不直接升级到人。
- **concrete plan 必过共识闸**:哪怕方向已明确,也不允许单个 agent 直接落地;用多角度验证把"明显方向"证成"明显方案"。
- **验证侧同构**:产物必须再经多 reviewer 共识 gate 才允许 merge;固定真值表语义(明确 reject 即拦,否则按既定规则放行),advisory 反馈不计入 approval。
- **controller 纯编排**:controller role 只 route / post / label / spawn / commit / push / merge;实现 / 验证 / 修复 / review / design solve 全部 delegate 给 agent worker。controller 的执行体可以是交互 session,也可以是 #396 `wakeup-runner`;后者只执行 `wakeup-plan` evidence-bound closed action projection,不得产生新的 substantive judgment。跨设备 side-effect permit 仍只来自 #191 ActiveControllerLease。
- **不伪造共识**:不得用单 agent / 单 reviewer 输出代替 multi-perspective gate。
- **人工介入要诚实**:只有确实需要人做产品、战略、治理或权限决策时,才把状态升级给 maintainer;不把人当作 reviewer 反复 reject 的兜底出口。

## 角色与边界

- **maintainer(人)**:产品/战略决策、治理级非可编码变更的授权、罕见手工 merge。
- **agent(controller LLM)**:纯编排;长跑中读 daemon 维护的 counts / state / artifact,不重新自测。
- **agent worker**(被派发的 codex / 其他 CLI 实例):承担所有思考密集工作 —— 实现、验证、修复、review、design solving。每个 worker 在隔离 worktree 内运行。
- **daemon(`scripts/` 后台)**:可机械、状态确定的 controller 工作的实现载体。Daemon 是经共识授权的 narrow allowlist 例外,默认**不**持 lifecycle authority(不开关 issue/PR/label、不 commit/push/merge/tag/release publish);仅 #53 授权 `integration sync daemon` 在专用 integration worktree 内执行 integration-branch git allowlist;仅 #191 授权 active-controller lease 在专用 pushed ref 上做 singleton owner CAS;仅 #238 授权 active-controller owner 的 checked-in `closed-label-reconciler` 对 CLOSED `crnd:lifecycle:managed` issue/PR 做 terminal phase-label reconciliation;仅 #322 授权 active-controller owner 的 controller-owned `ReleasePublisher` 做 release publication exact allowlist;仅 #396 授权 active-controller owner 的 checked-in `wakeup-runner` 对 `wakeup-plan` closed action projection 做机械 apply;仅 #403 授权 active-controller owner 的 checked-in apply helper 从 validated `IssueDecompositionPlan` 创建 managed child design issues并评论父 issue,`wakeup-plan` 只可经 #396 evidence-bound named `controller_action="apply_issue_decomposition_plan"` 投射 apply action且不是 #403 read-model/status/authorization owner;仅 #437 授权 active-controller owner 的 checked-in `RuntimeRetention` helper 在 host opt-in 后做 pending-events/concurrency-alert same-inode compaction、planner-proven stale worktree remove/prune、log-`EXIT=` proven spawn-task lock unlink,并 inert-ignore legacy generated_files entries,无 worker artifact deletion 或 GitHub/git lifecycle authority;仅 #504 授权 active-controller owner 的 `global-dashboard-status-card` 读取既有 status producers 并 PATCH 一个 host-configured issue comment,不得创建 comment 或拥有 lifecycle authority;仅 #541 授权 active-controller owner 的 checked-in `patrol-inspector` 在 host opt-in 后生成 `PatrolCandidateSignal`,经结构化 `PatrolAnalysisDecision.is_real_issue=true` 分析闸生成 `PatrolFinding` 并按 fingerprint create/update patrol-owned issue,无通用 issue/PR/label/git/release lifecycle authority;仅 #623 授权 active-controller owner 的 checked-in `DefaultIssueIntakeAdmission` + `DefaultIssueIntakeClaim` helper 在默认开启、`DefaultIssueIntakeAdmission` 证明 active cap/cooldown/upstream-idle/concrete-work-unit 且 #396 named action gate 下对 admitted OPEN non-PR not-managed issue 写 claim/stop comment 和既有 design label bundle;判断仍来自已有 worker prompts、meta-judge/review truth table、release preflight、validated plan artifact、runtime-retention plan artifact、patrol analysis decision/patrol finding fingerprint、default issue intake admission proof/comment protocol、共享只读 projection 和 live GitHub state;daemon 只验证和执行 allowlist。Implement/fix worker 仍不得 commit、push、open PR、merge、close issue/PR。
- **host 项目**:消费 skill 的下游项目。skill **无 host 项目改动权**:不修改 host 的 `.git` 配置 / CI 配置 / policy 文档;只在 `host.env` 暴露的 surface 上工作。host opt-in 缺失或为假时,所有相关 surface 静默 noop(`exit 0` + reason)。
- **#53 daemon role mirror**:integration publish/write authority remains only in the dedicated integration worktree; active-controller owner integration sync daemon may only reuse `ControllerActions.safe_sync_main()` for main checkout branch==`$INTEGRATION_BRANCH`, tracked-clean, no in-progress git operation, remote-only-ahead `git merge --ff-only origin/$INTEGRATION_BRANCH` follow. Main checkout local-ahead/diverged states only write pending events and must go through managed adoption PR/review recovery.

## 新增 / 修改 skill

- 每个 skill 一个目录:`skills/<kebab-name>/SKILL.md`。
- frontmatter 仅需 `name` + `description`(全文 ≤1024 字符)。`description` 以 "Use when..." 描述**触发条件**,不要复述工作流。
- 重型参考默认拆 `REFERENCE.md`;若该 skill 记录了单文件更稳定的维护事实,可留在 SKILL.md 的详细参考区并用 intra-file anchor 暴露;脚本放 `scripts/`;prompt 模板放 `prompts/`。
- 写 / 改 skill **必须**遵循 `superpowers:writing-skills` 的 TDD 纪律:先用子 agent 跑 baseline(看它在没有 skill 时怎么失败),再写 / 改 skill。
- 行为变更必须优先配套 **behavior test** 或端到端可观察输入 / 输出 / 副作用断言,直接验证行为本身。**source-regression test** / 段落 lint 仅用于跨 artifact 一致性、narrow allowlist、授权来源 path、事实源唯一性、owner-local public / parsed / authorization interface、必备 anchor / path 等语义边界;禁止精确锁私有常量名、私有函数 / 类位置、局部变量、局部控制流、单行源码原文等纯实现细节。若断言只因实现重构而必须同步修改且不验证行为或授权边界,应替换为 behavior test 或语义化的跨 artifact 一致性断言。
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
<!--
Refactor (iter343/issue-343):
  Old pattern: README 单一(非英文默认),CLAUDE.md 文档分层称 README 为权威源;无英文 canonical + 中文 companion 双文件,语言策略未给 README pair carve-out
  New principle: README.md 英文 canonical 公开身份文档 + README.zh-CN.md 中文 companion(双向交叉链接,大段顺序对齐不要求逐句对等);CLAUDE.md 文档分层/根.md收口/语言 carve-out 与 SKILL.md 语言策略窄改:README pair 是唯一英文-canonical 公开文档 carve-out,GitHub issue/PR/commit/design artifact 等工作态仍中文默认。严格按 DESIGN_DECISION_PATH verbatim Concrete plan;不碰 .version-bump.json/额外根文档/runtime/host.env/marker/daemon/workflow
-->
- **公开身份文档语言例外与工作语言**:README pair 是唯一英文 canonical 公开文档 carve-out(only English-canonical public-doc carve-out):`README.md` 用英文作为 canonical public identity document,`README.zh-CN.md` 是中文 companion,二者双向交叉链接且大段顺序对齐即可,不要求逐句对等。External user-facing artifact language comes from host.env `HOST_WORK_LANGUAGE`:default `en`,explicit `zh` for Chinese; source files remain English-only.

## Python 代码规范

本节只约束本仓库内 Python skill scripts 和测试代码;不是 host 项目规范,不得外推为下游 host 的 Python 规则。

- **类型边界清楚**:公共函数和方法必须有类型注解。跨内部层传递结构化事实时优先使用 `dataclass`、`TypedDict` 或明确投影类型;`Mapping[str, Any]`、`dict[str, Any]` 一类宽边界只用于外部 JSON adapter 层,不得蔓延到核心决策逻辑。
- **职责分层**:I/O、GitHub/git 副作用、环境读取、文件系统写入与决策逻辑分层;纯函数优先,可机械验证的判断应从副作用执行体中拆出。
- **复杂度不继续膨胀**:过长函数/文件和高复杂度分支不得在新增或触碰时继续膨胀;必须拆分到职责清晰的 helper / projection,或在同次变更说明中写明具体后续重构计划。
- **fail-closed 可诊断**:fail-closed 路径必须抛出具体、可诊断的异常或返回明确错误原因;禁止裸 `except`、吞错、静默 fallback,也不得用宽泛 fallback 掩盖缺失 host 事实或无授权副作用。
- **命名跟随职责**:稳定 API、类型、helper、artifact parser 的命名表达职责边界,不把 runtime、issue 编号或临时实现泄露进稳定接口;哲学文档仍不写 schema/identifier 版本后缀。

## 版本同步(强制)

改版本号时,`.version-bump.json` 列出的所有文件必须同步为同一版本:`package.json`、`.claude-plugin/plugin.json`、`.claude-plugin/marketplace.json`、`.codex-plugin/plugin.json`、`.cursor-plugin/plugin.json`、`gemini-extension.json`、`skills/consensus-loop/VERSION.json`。漏改任一份会让某个平台装到旧版。

## 版本迭代(规范)

发版坐标走 semver,迭代有固定阶梯与触发,**不临时拍版本、不在红信号上发**:

- **预发布阶梯**:`X.Y.Z-beta.N` → `X.Y.Z-rc.N` → `X.Y.Z`(GA)。默认同阶位只递增 `N`;唯一 beta 阶位内 core promotion 例外是 fresh release decision/candidate 携带 release-gate 机械产出的 `coordinate_policy.transition=beta_core_promotion`,且只允许 beta minor/major core promotion 到 bumped-core `beta.1`。beta→rc、beta→GA、rc→GA、rc core promotion 仍需另行治理。
- **gate 驱动,不手动选号**:发版由 release gate 全绿(信号全过)+ host opt-in(`RELEASE_AUTO_ENABLE=true`)触发;版本号由上一发布坐标机械递推,禁止临时手选或跳号。
- **不在红信号上发版**:双分支(`$REVIEW_BASE_BRANCH` + `$INTEGRATION_BRANCH`)required checks 全绿、稳定性信号(含 P0 streak)已清才发;gate 红即不发,先修再发。
- **坏版即弃,不动已发 tag**:已发布 tag 不可移动 / 复用 / 回填;发版提交若有缺陷,修复后以**下一个预发布号取代**(坏 `beta.N` → `beta.N+1`),旧 tag 保留为历史。
- **tag 必须指向绿提交**:tag 只能打在 required checks 已绿的提交上,且该提交的 manifest 版本与 tag 号一致(承「版本同步」)。

## 工程约定(精简)

- **文档分层**:`README.md` 是英文 canonical 仓库定位与共识引擎设计哲学权威源;`README.zh-CN.md` 是中文 companion,用于公开中文阅读入口,大段顺序对齐即可;`CLAUDE.md` 是 agent 工作宪法;`skills/<name>/SKILL.md` 是该 skill 的契约;`skills/<name>/REFERENCE.md` 是可选重型参考层;未使用 `REFERENCE.md` 时,SKILL.md 的详细参考区是该 skill 的权威参考层。职责不重叠:README pair 写产品身份,CLAUDE.md 写仓库宪法,skill 自己维护行为合同/参考。
- **根目录 `.md` 收口**:仅保留 `CLAUDE.md`、`README.md`、`README.zh-CN.md`、`AGENTS.md`(符号链接,内容同 `CLAUDE.md`)、`LICENSE`、`GEMINI.md`、`CHANGELOG.md`(若有)。
- **不保留历史副本**:废弃文件直接删除,不留 `.bak/.old/.deprecated`;历史由 git 保留。
- **Git**:分支名描述意图;提交信息祈使句聚焦单一目的;PR 写明动机、影响范围、验证命令与结果。
- **CI / 守卫**:任何 controller-runtime 例外(narrow allowlist daemon、observability、decision-artifact 等)必须配套机械验证手段(behavior test + source-regression test)。
- **跨 platform 清单 lint**:发版前比对 `.version-bump.json` 列出的所有文件版本一致;比对各 plugin manifest 列出的 skill 列表与 `skills/` 子目录一致。
- **测试按风险扩展**:窄文档 / anchor / 授权边界改动可用 source-regression 覆盖;共享脚本、跨 skill 流程或可观察行为改动必须补 behavior test。source-regression 不得替代行为验证,仅用于跨 artifact 一致性、授权边界、事实源唯一性、owner-local public / parsed / authorization interface、必备 anchor / path 存在,也不得把纯实现细节变成事实源。
- **生成物不当事实源**:临时日志、一次性报告、agent 草稿、运行输出不得成为长期规范来源;权威源永远在 SKILL.md / 脚本 / 测试里。
- **历史由 git 保存**:需要追溯旧行为时查 git,不在工作树保留影子副本或归档目录。
- **异常必抛出 + 必记日志,严禁吞掉/忽略**:任何错误、失败、fail-closed、skip、WAIT、budget 耗尽、分支不可达都必须抛出异常或写一条**可诊断、可 grep 的单行日志**(含目标 id、原因、关键计数),让问题在外部状态面立即可见。**禁止**空 `except`/`except: pass`、裸吞错、静默 `continue`/`return`、不记原因的跳过。诊断信息缺失即视为 bug —— 静默失败会让一个本可 5 分钟定位的问题拖成几小时。
- **测试必须断言真实行为,禁无意义测试**:测试只为验证行为而存在,**禁止**无意义/同义反复测试 —— 断言常量、复述源码字面、纯为覆盖率而写、不实际触发被测行为的测试一律删除或重写为 behavior test。慢的真实-进程/真实-时序测试要 mock 成确定性快测试,不得让无价值测试拖慢验证回路。

## 通用工程基本规则(面向对象,跨语言)

落到代码层的通用面向对象设计准则,**跨语言适用** —— C# / Java / Python / TS 等具体语言机制只作举例,约束的是**设计意图**而非某语言语法。本节先列**面向对象核心原则**(为什么这么设计),再给**实现层细则**(具体怎么做);细则是原则的落地,与已有「异常必抛出 + 必记日志」「事实源唯一」「命名跟随职责」等不动点同向,按事实源唯一不在多处重复声明。

**核心原则(OO principles)**

- **单一职责(SRP)**:一个类 / 模块只有一个变化原因,只承担一组内聚职责;职责一多就拆。
- **开闭(OCP)**:对扩展开放、对修改关闭;新增行为靠新增类型 / 扩展点,不改既有稳定代码。
- **里氏替换(LSP)**:子类型必须能无差别替换父类型而不破坏其契约 —— 前置条件不加强、后置条件不减弱、不抛父类型未声明的异常。
- **接口隔离(ISP)**:接口窄而专,调用方不被迫依赖用不到的方法;宁可多个小接口,不要一个胖接口。
- **依赖倒置(DIP)/ 面向抽象**:高层与低层都依赖抽象,而非高层依赖低层;依赖、参数、返回值优先用接口 / 抽象类型,面向接口编程不面向具体实现,便于替换、mock 与测试。
- **组合优于继承**:优先用组合 / 委托复用行为;继承只用于真正稳定的 is-a 契约,不为复用代码而继承。
- **迪米特法则(最少知识)**:只与直接协作者交互,不链式穿透 `a.b().c().d()` 抓别人内部结构;需要什么就让对方直接给到手。
- **高内聚低耦合**:相关的聚在一起、无关的分开;跨边界只经稳定契约,不依赖对方内部实现。
- **封装**:状态私有,只经方法 / 契约暴露;不泄露内部表示,不让外部依赖可变内部字段。
- **DRY(一处权威)**:同一知识只有一处权威表达,重复即抽象(承「事实源唯一」不动点)。
- **KISS / YAGNI**:用能解决当前问题的最简设计;不为臆想的未来需求预埋抽象与扩展点。

**实现层细则(上述原则的落地)**

- **一方法一职**:一个方法只做一件事,只做方法名所表达的事,不包揽整条流程。
- **参数不搬运**:实现类只关心自己的参数和字段,不做参数转换搬运;需要某类型就直接传该类型(如需要字节串就传字节串),不传通用容器再在方法内转换,转换交给扩展 / 工具函数。
- **依赖过多即拆分**:一个类依赖太多 service 时,把部分能力改为参数传入或拆成更小职责,不让单个类成为万能入口。
- **取值依赖一大堆就建 manager/service**:若取某个东西要先备齐一堆前置依赖,把它收敛成有明确状态边界的 manager / service(例如「需要 chain id 才能算」的能力不要做成无上下文静态 helper)。
- **service 无状态**:service 必须无状态,命名以 `Service` 结尾。
- **改接口先走评审**:改动任何对外 interface / 契约前,必须先开 issue / PR、至少 2 人 review 并组织讨论,想改的人负责发起;本仓库即落到共识闸 —— concrete plan 必过多角度共识(见「共识引擎哲学」)。
- **接口保持简单**:加方法前先尝试扩展方法 / 组合实现,能不进接口就不进接口。
- **delegate 先想 interface**:想用 delegate / 函数指针前,先考虑能否用 interface 表达职责。
- **元编程需顶层评审**:attribute / 反射 / delegate / 语言级 event 等元编程机制需顶层开发评审讨论后才用,不默认使用。
- **禁全局可变静态**:不用静态全局属性 / 全局可变状态。
- **运行期不改静态值**:运行期绝不设置或改写静态值。
- **只读不可降级**:字段在编码时已是 readonly / final / 只读时,永远不为图方便移除只读修饰。
- **event 只在同命名空间内 raise**:只在同一命名空间 / 模块内发出 event,不跨模块乱发。
- **符号引用不用裸字符串**:引用符号名用语言的符号引用机制(如 `nameof`),不写裸字符串字面量 `"MethodName"`。
- **跟随既有风格**:加代码遵循文件既有风格;不得不破坏风格时,就地标 `TODO: review required`。
- **基础设施层引用方向**:基础设施层只能被同一命名空间中 Domain 以上的层引用,不被反向依赖。
- **子命名空间不反向引用父**:子命名空间不反向引用父命名空间(如 `X.Kernel.Node` 不应引用 `X.Kernel`)。
- **不乱注入跨命名空间基础设施**:不理解含义时,不注入 / 引入其它命名空间(模块 / 层)的基础设施。
- **复制即抽象**:需要复制代码时,抽成方法 / 扩展方法 / delegate,不就地复制。
- **通用化优先**:重复出现的逻辑、被多处依赖的能力,提炼为不绑定单一调用点的通用方法 / manager / service,不写死一次性专用实现。
- **非用户输入不防御性校验**:参数若非用户输入,不必写防御性校验,让异常自然抛出(承「异常必抛出」不动点,失败须可诊断)。
- **不吞基类异常**:不 catch 基类异常(等于一次吞掉一切),除非确知在做什么(承「严禁吞掉 / 静默」不动点)。

**常用设计模式(Design Patterns)**

成熟解法目录,服务于上面的原则、按需取用;**忌为模式而模式**(承 KISS / YAGNI),简单问题别套重模式。

创建型:

- **工厂 / 抽象工厂(Factory)**:把"创建哪个具体类型"从调用方抽走,调用方只依赖抽象;按上下文(配置 / chain id 等)决定实例时用,是 DIP 的落地。
- **建造者(Builder)**:分步构造复杂对象,消除长参数列表 / 可选参数爆炸。
- **单例(Singleton)**:慎用 —— 它常是全局可变状态的伪装;优先用 DI 容器管理生命周期,不自建运行期静态单例(承「禁全局可变静态」「运行期不改静态值」)。

结构型:

- **适配器(Adapter)**:把不兼容接口包成目标接口,隔离第三方 / 旧实现。
- **装饰器(Decorator)**:用组合叠加横切行为(日志 / 缓存 / 重试)而不改原类,体现开闭 + 组合优于继承。
- **代理(Proxy)**:在真实对象前加同接口的间接层(延迟加载 / 访问控制 / 远程调用)。
- **外观(Facade)**:给一组子系统一个简化入口,降低调用方耦合(呼应高内聚低耦合)。

行为型:

- **策略(Strategy)**:把可替换算法封到同一接口、运行期注入,是「delegate 先想 interface」的正解。
- **观察者 / 事件(Observer)**:发布-订阅解耦;本仓库落到「event 只在同命名空间内 raise」。
- **模板方法(Template Method)**:父类定骨架、子类填可变步骤,骨架稳定 + 步骤可变时用。
- **命令(Command)**:把请求封成对象,便于排队 / 撤销 / 审计。
- **责任链(Chain of Responsibility)**:请求沿处理器链传递直到被处理,适合管线 / 中间件。
- **状态(State)**:把状态相关行为分到状态对象,消灭巨型 `switch`。

<!-- consensus-rnd:foundational-invariants:start version=1 sha256=f5c24b0c3515993a7b86c4ed78ce7386add665f8c8b84cc7275aedebd6c3e6af -->
## 共识研发不动点（由 consensus-rnd 管理）

- FI-001 AI 产物默认不可信；进入主线前必须经过独立检查，至少包含共识、review 或自动验证中的适用组合。
- FI-002 Host 事实必须由 host 配置或 host 规则注入；通用 skill / engine 不硬编码具体项目、组织、路径、分支或人员事实；skill-private runtime directories such as `.refactor-loop/` must not become host production configuration or ledger SSOT.
- FI-003 稳定核心保持小而可审计；高频变化留在 host 规则、prompt、脚本或扩展层，不下沉为核心不变量。
- FI-004 跨进程、跨 turn 或跨节点的事实必须有权威记录；进程内记忆、cache、临时变量不能冒充事实源。
- FI-005 边界优先于便利；职责、层级、协议和状态所有权必须清楚，禁止用中间层快捷方式绕过主链路。
- FI-006 变更必须可验证且基于 evidence；失败、缺口和越界承诺要显式暴露，禁止用静默假设或禁用测试换取通过。
- FI-007 删除优先；废弃路径直接移除，除非 host 规则明确要求迁移期兼容。
<!-- consensus-rnd:foundational-invariants:end -->
