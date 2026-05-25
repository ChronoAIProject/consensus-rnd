# 任务：验证 ${CLUSTER_ID} 的实施改动

你以无人值守模式在 worktree `${WORKTREE_PATH}` 中工作。前一个 codex 已完成实施，改动在工作树未提交。

## 必读

1. `$REPO_ROOT/CLAUDE.md` 全部强制条款。
2. `$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}.md` 的 "${CLUSTER_ID}" 一节。
3. `$REPO_ROOT/.refactor-loop/runs/implement-${CLUSTER_ID}.md` 实施摘要。
4. `git diff HEAD` —— 完整改动 diff。

## 验证维度

按以下顺序，全部通过才能给 pass：

### 1. 改动与设计原则一致

- 检查每个被重构的关键类/方法是否带有 `// Refactor (...):` 注释，包含 Old pattern + New principle。缺失任何一处 → 标记缺陷。
- 检查改动是否真正消除了 `old_pattern` 描述的违反（用 `rg` 抽样确认 anti-pattern 不再出现在 scope_paths 内）。

### 2. 作用域诚实

- `git diff --name-only HEAD` 必须全部落在 audit 的 `scope_paths` 列表内，或在实施摘要中有 `SCOPE_EXTEND:` 记录并给出合理理由。
- 越界改动 → 缺陷。

### 3. 测试完备

- `verification_hints` 指定的所有测试命令必须能跑且通过。
- 测试代码不得包含 `sleep/delay` 作为断言节奏。
- 不得出现 `[Skip]` / `[Trait("Category","Manual")]` 之类的禁用标记，除非实施摘要明确说明且有 CLAUDE.md 依据。
- 关键路径测试覆盖率不得下降。

### 4. CI 守卫

按顺序运行（任意失败 → rework）：

```bash
bash $REPO_ROOT/$CI_GUARDS
bash $REPO_ROOT/$CI_GUARDS
# 任何 cluster 特定守卫，例如：
${CLUSTER_SPECIFIC_GUARDS}
```

如果项目编译失败 → rework。

### 5. 没有新增依赖

- `git diff -- '*$BUILD_CMD 目标/工程文件' 'Directory.Packages.props' '*.proto'` 若有新增依赖、新增 NuGet 包、新增 proto 文件，必须在实施摘要中有合理说明；否则缺陷。

### 6. 外部仓库零改动

- 检查 diff 是否引用 $EXTERNAL_REPOS / 其它外部仓库源；若引用必须仅是消费已发布契约，不得依赖未发布改动。

## 输出契约

写入 `$REPO_ROOT/.refactor-loop/runs/verify-${CLUSTER_ID}.md`：

```markdown
---
schema: refactor-verify-v1
cluster_id: ${CLUSTER_ID}
verdict: pass | rework | abort
verified_at: <ISO8601>
---

## Diff summary
<files changed, lines added/removed>

## Checks
- [x|FAIL] 注释包含 Old/New
- [x|FAIL] 作用域诚实
- [x|FAIL] 测试通过
- [x|FAIL] 架构守卫通过
- [x|FAIL] 无意外依赖
- [x|FAIL] 无外部仓库改动

## Findings
<每个 FAIL 项的具体证据：文件:行号 / 测试名 / 守卫输出>

## Rework instructions (if verdict == rework)
<给 implement 阶段的明确返工指令，可直接拼接到 implement prompt>
```

末尾打印 `VERIFY_DONE:${CLUSTER_ID}:<verdict>` 其中 verdict ∈ {pass, rework, abort}。

- `pass` —— controller 会合并。
- `rework` —— controller 会回炉实施。
- `abort` —— 设计层面问题，不要再尝试同一 cluster；controller 会丢到 failed 列表并通知人类。

## 红线

- 你**只读 + 跑命令**；禁止修改 worktree 内任何文件。
- 禁止 `git commit` / `git push` / `git checkout`。
- 验证宽松度倾向严格而非宽松：怀疑 → 标 rework，不要妥协给 pass。

开始执行。

---

## AI 内容标识符(强制)

所有 AI 生成的对外内容(GitHub issue/PR comment、PR body、commit message、`runs/*.md` artifact、push notification)**必须末尾独立一行**加 sentinel:

    ⟦AI:AUTO-LOOP⟧

不可修改字符 / 不放代码注释 / 不放路径分支名。无 sentinel = 产生失败,controller 拒绝 post。
