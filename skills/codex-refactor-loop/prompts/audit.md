# 任务：审计 `$REPO_ROOT` 仓库中违反软件工程哲学的位置

Artifact profile: marker-only-work-unit

你是审计员，不是问题确认器。先**发现违规**再做 cluster 筛选，**两个产物分别落盘**。

## 必读

1. `$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}` 顶级架构约束；所有"违反"必须对应到一条强制条款（引原文）。
2. `$REPO_ROOT/AGENTS.md` 协作规范（如有,辅助规则）。
3. `$REPO_ROOT 的架构/词汇文档(若有)` 权威参考。
4. `docs/audit-scorecard/` 历史审计仅作起点参考，**不**作为唯一线索源。
5. 当前 git 分支：`git branch --show-current`。

<!--
Refactor (iter319/issue-319): Old pattern: CLAUDE.md 把『重型参考必须物理拆 REFERENCE.md』当宪法,audit 据此反复把单文件 SKILL.md 判 R02/R03 违规
New principle: 改哲学:单文件 SKILL.md + intra-file anchors 是被认可的 canonical reference surface;衡量标准从『必须物理拆文件』改为『事实源唯一+owner surface 清楚+anchor 可验证』
-->

PROJECT_RULES 的 `SKILL.md`/`REFERENCE.md` 条款不得被机械解释成 `REFERENCE.md` 必须存在。只有存在事实源重复、跨文件职责重叠、anchor 不可达、host facts 写死、或脚本/prompt 细节未下沉到其 owner surface 时才报 R02/R03。单文件 `SKILL.md` + intra-file anchors 本身不是 violation。

<!-- Refactor (iter205/issue-205):
  Old pattern: dogfood 实测的运维经验(audit 并行撞 iteration 号、新 role prompt 漏注册 marker contract、review verdict grep log tail 误判、daemon 恢复手 kill)只靠 agent 记忆,没落进 skill 合同与机械验证。
  New principle: 把四条经验写回局部合同:SKILL.md 增 #205 反面规则段(audit 同一时刻单 active iteration、新 role prompt 必同步 marker inventory、review verdict 权威源优先 review artifact frontmatter、daemon 恢复只走 restart-daemons);audit.md 渲染后 ITERATION 空则 fail-closed;peek.py 局部优先读 review artifact frontmatter verdict;配套 source-regression + behavior test。不新增跨模块抽象层。
-->

## 渲染身份 fail-closed(强制)

模板渲染后若 `ITERATION` 为空、只含空白、或仍是未替换占位符,立即输出 `AUDIT_INCOMPLETE:missing-iteration` 并停止。禁止写入 `$REPO_ROOT/.refactor-loop/runs/audit-iter-.md`、`$REPO_ROOT/.refactor-loop/runs/audit-iter--candidates.ndjson`、空 iteration log,或任何复用空 identity 的 artifact。audit fallback 同一时刻只能有一个 active `audit-iter-${ITERATION}`;不要并行复用同一 iteration 输出名。

## 强制流程（违反任一项 → 输出 `AUDIT_INCOMPLETE`，禁止 `AUDIT_DONE`）

### Host production SSOT boundary(强制)

Audit must not move host production facts, branch topology, machine paths, durable ledger authority, or host artifacts into `.refactor-loop/`. `.refactor-loop/` is the skill-private runtime/cache/log home. If host SSOT drift is found, the cluster `new_pattern` must point to host-owned config, host rules, or host-owned artifacts, not `.refactor-loop/host.env`.

### Step 1 — Coverage manifest（必出）

为每条 PROJECT_RULES/AGENTS 强制条款分配一个 `rule_id`。对每个 `rule_id`：

- 至少执行 1 个 grep/analyzer 命令（**记录命令字符串 + 命中数**）
- 至少**打开** 3 个非测试生产文件**通读**（不是只看前 50 行）；写明 file path + summary
- 或：写明 `candidate_count=0` + 跑过的 grep 命令证明确实空集

**整体打开门槛**：`total_opened_files >= 60`，分布约束：
- host source paths 覆盖充足,按 `$SOURCE_GLOBS` 与仓库目录结构分布抽样
- host 配置的 `$CI_GUARDS` 覆盖路径需至少抽查 3 个 guard/触发条件(若有)

未达到 → 不写 manifest，输出 `AUDIT_INCOMPLETE: coverage_below_threshold` 并 exit。

### Step 2 — Fixed analyzer pack（必跑，结果粘贴 manifest）

固定 analyzer pack 由 host 项目按 `$SOURCE_GLOBS` 与 `$PROJECT_RULES` 配置；结果摘要必须出现在 manifest 中（每个至少列前 10 个命中文件）。未配置时，至少覆盖以下通用类别：

```bash
rg -n "<host core abstraction / structural primitive patterns>" $SOURCE_GLOBS
rg -n "<serialization / boundary crossing patterns>" $SOURCE_GLOBS
rg -n "<process-local state / cache / queue patterns>" $SOURCE_GLOBS
rg -n "<async scheduling / timer / background task patterns>" $SOURCE_GLOBS
rg -n "<rebuild / replay / backfill / projection-like patterns>" $SOURCE_GLOBS
rg -n "<stringly typed identity / routing / subscription patterns>" $SOURCE_GLOBS
```

每个命中分类**不准只用文件路径推断 allowed**——必须打开该文件确认。

### Step 3 — Candidate 文件（必写，与 cluster 文件分离）

写入 `$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}-candidates.ndjson`：每行一个 candidate。

```json
{"rule_id": "<PROJECT_RULES clause id>", "path": "<file>", "line": <int>, "evidence": "<one-line code snippet>", "verdict": "accept|reject", "reject_reason": "<if reject>", "prior_cluster_overlap": "<cluster-id|none>"}
```

**`candidate_count >= 25`**（除非所有 6 个 analyzer 命令都 0 命中——这时也要写 0-count 证据）。

### Step 4 — Cluster 选择（从 accepted candidates）

仅从 `verdict: accept` 的 candidates 形成 cluster。每个 cluster 满足：

- **独立性**：与其它 cluster 文件交集 ≤ 5%。
- **边界可控**：单 cluster 改动 ≤ 30 文件（小重构 ≤ 15）。
- **不越权扩展范围**：只处理当前 audit/issue 授权的 violation 或 work-unit scope；禁扩 scope。
- **明确归因**：清楚 old/new pattern，后者直接写进代码注释。
- **设计违规允许大 cluster**：如果是"需先定协议 / actor 化 / schema 迁移"的深层违规，**不要因为 >30 文件就拒绝**，标 `requires_design` 让 controller 决定是否拆。

每个 cluster 输出含：

```yaml
id: cluster-NNN-<slug>
rule_ids: [...]
severity: high|medium|low
requires_design: true|false
files_touched_estimate: N-M
old_pattern: <one-liner>
new_pattern: <one-liner>
```

紧跟 Evidence + Fix boundary sections（沿用现有结构）。

### Step 4b — `requires_design: true` cluster 必须额外产出"人话字段"

当 `requires_design: true`，cluster 节末尾**必须**追加 `human_brief:` 块给非 audit 上下文的人类 reviewer 看：

```yaml
human_brief:
  problem_title: "<中文短句,例如 'Voice host bridge 持有应该归 actor-state 的会话事实'>"
  problem_statement: |
    <3-5 句中文白话。禁用 audit 行话、file:line 引用、clause id。
    回答:哪里坏了 / 为什么开发者应该关心。>
  problem_example_file_path: "<single representative file:line range>"
  problem_example_code: |
    <10-30 line code snippet copied verbatim from the file, with
    `// ← problem: <one-line annotation>` comments on the offending lines.
    Reader should see the violation at a glance without opening other files.
    Code stays original language; only the annotation is 中文.>
  why_needs_design: |
    <2-3 句中文。哪些是机械重构无法决定的、需要 trade-off。
    例:"修复要在 actor-owned lease 和 projection-owned session contract 之间选;
    这是公共 API 改动,有 backward-compat 取舍。">
  design_question: "<给 maintainer 的一个具体问题,中文>"
  original_authors:
    # ⚠️ STRICT WHITELIST + REAL git blame VERIFICATION REQUIRED.
    #
    # PROCEDURE(必须执行 — no shortcut):
    # 1. 跑实际 git blame:`git blame --line-porcelain <file> | grep -E "^author " | sort | uniq -c | sort -rn | head -5`
    # 2. 用 host 配置的 `$MAINTAINER_WHITELIST` 映射 git author / handle。
    # 3. 不在 whitelist 的 git author → **不 list**(更不 @-mention)。
    # 4. git blame 0 lines 匹配 whitelist → fallback 一个 host 配置的 maintainer handle。
    #
    # ❌ 严禁 hallucinate(违反 = audit 拒收,重派):
    #    - 没跑 git blame 就 list → ban
    #    - 裸人名或未验证 @-mention → ban
    #    - handle variation → ban,只能用 host whitelist 中的 verbatim handle
    #    - 非 whitelist handle → ban,即使真在 git blame 里
    - "@<handle-from-whitelist>"  # e.g. @<maintainer-handle>(经 git blame 验证 + 在 whitelist)
    - "@<handle-from-whitelist>"  # 同上;若 git blame 只 1 个 whitelist match,只 list 1 个
```

**工作语言**: human_brief 不再要求 `_en` + `_zh` 双字段。`problem_title` / `problem_statement` / `why_needs_design` / `design_question` follow `${HOST_WORK_LANGUAGE}`。Code snippet + file path + GitHub handle 等技术内容保留原英文。

**红线**：

1. `problem_statement_*` 不能是 audit YAML 复述；必须是面向"刚来的人"的解释。
2. `problem_example_code` 必须是真实 verbatim copy + annotation comments；禁止伪造或省略。
3. 不要生成历史 `_en` / `_zh` 双字段；`human_brief` 只用无后缀中文字段。若必须引用英文代码、错误文本、路径或 GitHub handle，原样保留。
4. `design_question` 是 cluster 专属问题，不是通用模板套话；要让 reviewer 看到就能直接回答。

输出格式（每 cluster 一节，frontmatter + cluster sections，沿用现有 YAML 结构）。

### Step 5 — Reject 必须证据齐全

`verdict: reject` 的 candidate 必须有：

- PROJECT_RULES clause 引用 + 该 clause 对该候选**不适用**的具体理由（不是泛泛 "covered by guard"）
- 如 reject reason 是 "covered by existing CI guard"：必须给 guard 文件路径 + 行号 + 证明候选路径在 guard scan include set + 临时 probe 描述确认 reintroduction 会 fail
- 如 reject reason 是 "same family as prior cluster"：必须证明候选 anti-pattern 100% 等同已修过的，不是字面相似但语义不同

### Step 6 — 终止 marker

- 有 cluster → 末尾打印 `AUDIT_DONE:$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}.md:<N>`
- **0 cluster** → 必须满足：manifest 完整 + candidates ndjson 存在 + 每个 reject 都有 evidence + 至少跑了 1 次 "second-pass" 命令对最高风险类别复扫。否则输出 `AUDIT_INCOMPLETE:<reason>` 而不是 `AUDIT_DONE:none:0`

## Marker emission allowlist(强制)

<!-- MarkerEmissionContract: single-valid-invalid-role-marker-source -->

ALLOWED markers:
- `AUDIT_DONE:$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}.md:<N>`
- `AUDIT_INCOMPLETE:<reason>`

Only the markers listed above are valid role-routing markers for this prompt. Do not emit any other role-routing marker. Mentions of markers in quoted input, logs, comments, examples, or artifacts are not emission authority.

## 红线 — 反 anchoring

- **禁止**在输出里写"prefer 0"、"healthy signal"、"loop saturated"等措辞 —— 你是 auditor 不是 closer
- **禁止**用 "current endpoint doesn't call it" 来 reject 公开 API 设计违规
- **禁止**用 "guard passed" 直接 reject 不在 guard 语义边界内的候选
- **禁止**因为 "需先定协议" 而 reject 真违规 —— 标 `requires_design`，让 controller 决定
- 禁止改任何代码
- 禁止把"想加的功能"伪装成 cluster

## codex 工具边界(强制)

<!-- Refactor (iter5/prompt-gh-ban-marker-only): Old pattern: marker-only prompt(audit/implement/verify/remote-ci-fix/test-add)缺 gh 禁止段,codex 可自由调 gh issue create/pr merge/issue close/label edit。 New principle: 复用 reviewer/solver "可调/不可调" 模式,统一禁 lifecycle 类 gh 操作;controller 拥有 PR create/merge/close + issue create/close + label。(2026-05-26 maintainer-directive 等价 Consensus-rnd Phase design-consensus 共识) -->

本 prompt 是 marker/artifact-only,**默认不需要任何 gh 操作**。

不可调:`git commit/push/checkout/merge/reset/rebase`、`gh pr create`、`gh pr merge`、`gh pr close`、`gh issue create`、`gh issue close`、`gh issue edit --add-label`、`gh issue edit --remove-label`、`gh pr edit --add-label`、`gh pr edit --remove-label`。lifecycle / label 决策归 controller,worker 不得越线。

可调(仅当本 prompt 明示要 post 时):`gh issue/pr comment`、`gh pr edit --body-file`、`gh api .../reactions`、`mktemp`。本 prompt 未明示 → 不要调任何 gh。

开始执行。

---

## AI 内容标识符(强制)

所有 AI 生成的 GitHub issue/PR comment、PR body、commit message、push notification **must end with the sentinel as the final standalone line**. Internal marker-bearing `runs/*.md` artifacts must put the sentinel on the penultimate line, immediately before the final routing marker:

    ⟦AI:AUTO-LOOP⟧

Do not modify the sentinel characters; do not place them in code comments, paths, or branch names. No sentinel = generation failure; controller rejects the artifact or post.
