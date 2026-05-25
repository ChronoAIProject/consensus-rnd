# consensus-rnd 改进 backlog(无人值守 /loop 自审记录)

> 本文件是 `/loop /codex-refactor-loop`（主动发现问题 / 自我对话 / 持续无人值守）的持久产物。
> 每轮 loop 在此追加发现与动作,使发现跨上下文压缩存活,并给维护者一个单一审计面。
> 这是 dev 工作文档,非发布产物(发布产物只有 `skills/`)。

## 自审判定线(loop 自主执行的边界)

- **可在循环内直接修(卫生类)**:移植残留的脏数据 / 损坏占位 / 悬空文件引用 / 泄漏的真人名与 org 名 / 版本漂移 / 纯增量模板。这些不是「正文逻辑」。
- **需共识 gate 或维护者放行(正文逻辑类)**:phase / 路由表 / marker 语义 / 共识规则的改写,以及脱壳(去 "refactor" 语义)、抽引擎脊柱。CLAUDE.md 明确「脱壳/重命名前不改正文逻辑」;README 明确「用这个引擎来通用化这个引擎」——大改应走引擎自身的多 solver 共识闸,而非单 agent 直接落地。

---

## 发现清单（iter-1 自审，2026-05-25）

| ID | 严重度 | 类别 | 位置 | 问题 | 建议动作 | 状态 |
|---|---|---|---|---|---|---|
| F1 | P1 | 泛化 blocker | (无) | 无 `host.env` 模板,新 host 须从 SKILL.md「Host 配置」表逆向出所有变量 | 新增 `host.env.example`(含 BUILD_CMD 空格坑、GH_REPO 冲突坑) | ✅ iter-1 已修 |
| F2 | P1 | redaction 残留 | SKILL.md:1760, 2065, 2396, 2421 | 损坏占位 `$REPO_ROOT 的架构/词汇文档(若有)`;2421 是双重损坏 markdown 链接 `[占位](占位)` | 脱壳时参数化为可选 `$ARCH_VOCAB_DOC`(host.env)或删除该约束;2421 的坏链接可作卫生类先修 | ⬜ 待办 |
| F3 | P1 | redaction 残留 + 运行时必失败 | SKILL.md:1178 | `gh api orgs/该项目AI/members/<author>` —— 中文占位 org 名,运行时直接报错 | 参数化为 `$GH_ORG` 或从 `GH_REPO_SLUG` 的 owner 推导;卫生类,可循环内修 | ⬜ 待办 |
| F4 | P1 | 泄漏真人名(违反自身规则) | SKILL.md:1438-1445 | @-mention 白名单表混入未脱敏真人名 `jason` / `potter`,与本 skill「严禁把人名当 plain text 致误 @-ping」直接冲突 | 全部脱敏 / 参数化为 `$MAINTAINER_WHITELIST` 驱动;卫生类,可循环内修 | ⬜ 待办 |
| F5 | P2 | 悬空引用 | SKILL.md:1428, 2417 | 称 `github-post-writer.md` 保留为 `*.deprecated`,但 prompts/ 下无 `.deprecated` 文件;2417 又把它列进 bilingual 适用 prompt 列表 | 要么真放一个 `.deprecated` 占位,要么把这两处引用删掉;卫生类 | ⬜ 待办 |
| F6 | P2 | host 主张残留 | SKILL.md:23, 66, 2386 | `fkst` 三处具体 host 示例 | 脱壳时改中性示例(`your-repo` / 通用描述) | ⬜ 待办 |
| F7 | P3 | 泛化路线(README 已列) | 全 skill | 引擎脊柱与 audit(work-unit 种子来源)耦合;「工作语言规则」是写死的 host policy;名字带 refactor 语义 | 走引擎自身共识 gate;需维护者放行 + 可能需 host 引导 | ⬜ 待办 |

## 观察(非缺陷)

- 版本号 6 份清单全为 `0.1.0`,一致 ✓
- SKILL.md frontmatter 306 字符,远低于 1024 上限 ✓
- scripts/(10)+ prompts/(18) 齐全,无缺文件 ✓
- dogfood 软链 `.claude/skills → ../skills` 与 `AGENTS.md → CLAUDE.md` 正常 ✓
- 运行时:`codex` 0.131.0 + `gh` 2.88.1(登录 loning)+ remote `ChronoAIProject/consensus-rnd` 均就绪 → 真 dogfood 闭环技术可行
- ⚠️ 但本仓库无应用代码(纯 markdown skills),audit 阶段几乎无可重构候选;且全闭环会创建真实 GitHub issue/PR(对外动作)。**需维护者确认**是否在本仓库跑真闭环,或换一个有代码的 host 来 dogfood 引擎。

---

## Loop 迭代日志

### iter-1 — 2026-05-25
- 摸清仓库结构;确认 skill 完整(28 文件)、版本一致、工具就绪。
- 自我对话挖出 F1–F7。
- **动作**:新增 `skills/codex-refactor-loop/host.env.example`(修 F1);建本 backlog。
- 本地 commit 为 checkpoint,**未 push**(对外动作,留待维护者放行)。
- 下一轮候选:应用 F3/F4/F5 + F2 的坏链接(卫生类,按判定线可自主修)。
