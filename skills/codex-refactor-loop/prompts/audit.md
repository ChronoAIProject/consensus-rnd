# 任务：审计 `$REPO_ROOT` 仓库中违反软件工程哲学的位置

你是审计员，不是问题确认器。先**发现违规**再做 cluster 筛选，**两个产物分别落盘**。

## 必读

1. `CLAUDE.md` 顶级架构约束；所有"违反"必须对应到一条强制条款（引原文）。
2. `AGENTS.md` 协作规范（如有）。
3. `$REPO_ROOT 的架构/词汇文档(若有)` 权威参考。
4. `docs/audit-scorecard/` 历史审计仅作起点参考，**不**作为唯一线索源。
5. 当前 git 分支：`git branch --show-current`。

## 强制流程（违反任一项 → 输出 `AUDIT_INCOMPLETE`，禁止 `AUDIT_DONE`）

### Step 1 — Coverage manifest（必出）

为每条 CLAUDE/AGENTS 强制条款分配一个 `rule_id`。对每个 `rule_id`：

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
{"rule_id": "<CLAUDE clause id>", "path": "<file>", "line": <int>, "evidence": "<one-line code snippet>", "verdict": "accept|reject", "reject_reason": "<if reject>", "prior_cluster_overlap": "<cluster-id|none>"}
```

**`candidate_count >= 25`**（除非所有 6 个 analyzer 命令都 0 命中——这时也要写 0-count 证据）。

### Step 4 — Cluster 选择（从 accepted candidates）

仅从 `verdict: accept` 的 candidates 形成 cluster。每个 cluster 满足：

- **独立性**：与其它 cluster 文件交集 ≤ 5%。
- **边界可控**：单 cluster 改动 ≤ 30 文件（小重构 ≤ 15）。
- **不新增功能**：只清理违反位置；禁扩 scope。
- **明确归因**：清楚 old/new pattern，后者直接写进代码注释。
- **设计违规允许大 cluster**：如果是"需先定协议 / actor 化 / proto 迁移"的深层违规，**不要因为 >30 文件就拒绝**，标 `requires_design` 让 controller 决定是否拆。

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

**工作语言**: human_brief 不再要求 `_en` + `_zh` 双字段。`problem_title` / `problem_statement` / `why_needs_design` / `design_question` 直接用中文。Code snippet + file path + GitHub handle 等技术内容保留原英文。

**红线**：

1. `problem_statement_*` 不能是 audit YAML 复述；必须是面向"刚来的人"的解释。
2. `problem_example_code` 必须是真实 verbatim copy + annotation comments；禁止伪造或省略。
3. **每个 `_en` 字段必须有对等 `_zh` 字段，且内容完全等价**：信息密度、段落数、决策点列举数必须一致。禁止 `_zh` 写"见英文部分"或更短的 TL;DR 版本——这违反 SKILL.md "Bilingual rule (双语规则)"。`_zh` 自身必须是非中文母语 reviewer 看不到 `_en` 也能行动的完整解释。缺 `_zh` 或 `_zh` 显著短于 `_en` → controller 验收为 `AUDIT_INCOMPLETE: human_brief_missing_or_unbalanced_zh`。
4. `design_question_pattern_en/zh` 是 cluster 专属问题，不是通用模板套话；要让 reviewer 看到就能直接回答。

输出格式（每 cluster 一节，frontmatter + cluster sections，沿用现有 YAML 结构）。

### Step 5 — Reject 必须证据齐全

`verdict: reject` 的 candidate 必须有：

- CLAUDE clause 引用 + 该 clause 对该候选**不适用**的具体理由（不是泛泛 "covered by guard"）
- 如 reject reason 是 "covered by existing CI guard"：必须给 guard 文件路径 + 行号 + 证明候选路径在 guard scan include set + 临时 probe 描述确认 reintroduction 会 fail
- 如 reject reason 是 "same family as prior cluster"：必须证明候选 anti-pattern 100% 等同已修过的，不是字面相似但语义不同

### Step 6 — 终止 marker

- 有 cluster → 末尾打印 `AUDIT_DONE:$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}.md:<N>`
- **0 cluster** → 必须满足：manifest 完整 + candidates ndjson 存在 + 每个 reject 都有 evidence + 至少跑了 1 次 "second-pass" 命令对最高风险类别复扫。否则输出 `AUDIT_INCOMPLETE:<reason>` 而不是 `AUDIT_DONE:none:0`

## 红线 — 反 anchoring

- **禁止**在输出里写"prefer 0"、"healthy signal"、"loop saturated"等措辞 —— 你是 auditor 不是 closer
- **禁止**用 "current endpoint doesn't call it" 来 reject 公开 API 设计违规
- **禁止**用 "guard passed" 直接 reject 不在 guard 语义边界内的候选
- **禁止**因为 "需先定协议" 而 reject 真违规 —— 标 `requires_design`，让 controller 决定
- 禁止改任何代码
- 禁止把"想加的功能"伪装成 cluster

开始执行。

---

## AI 内容标识符(强制)

所有 AI 生成的对外内容(GitHub issue/PR comment、PR body、commit message、`runs/*.md` artifact、push notification)**必须末尾独立一行**加 sentinel:

    ⟦AI:AUTO-LOOP⟧

不可修改字符 / 不放代码注释 / 不放路径分支名。无 sentinel = 产生失败,controller 拒绝 post。
