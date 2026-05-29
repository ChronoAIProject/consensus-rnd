# 任务：补测试覆盖重构引入的未覆盖代码 — ${CLUSTER_ID}

Artifact profile: marker-only-work-unit

<!-- Refactor (iter3/skill-host-language-policy): Old: prompt hardcoded host-language defaults  New: 6 HOST_* variables are optional and empty by default, injected by host.env (#20 structural consensus) -->

worktree: `${WORKTREE_PATH}`，分支 `${BRANCH}`。

## 必读

1. `$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}` 全部强制条款（含 "Codex CLI 调用规范"、"测试与质量门禁"）。
2. `$REPO_ROOT/.refactor-loop/runs/audit-iter-N.md` 中 `${CLUSTER_ID}` 一节
3. `$REPO_ROOT/.refactor-loop/runs/implement-${CLUSTER_ID}.md`
4. **未覆盖行报告**：以下文件:行号 是 codecov 标记为 patch miss/partial 的位置：

```
${UNCOVERED_LINES}
```

5. Host 测试策略(可为空):测试文件位置 `${HOST_TEST_FILE_GLOBS}`；测试命名规则 `${HOST_TEST_NAMING_RULE}`；测试注释规则 `${HOST_COMMENT_RULE}`；代码围栏语言 `${HOST_CODE_FENCE_LANG}`。为空时只从现有测试、`$PROJECT_RULES`、`$TEST_CMD`、实际 diff 推断；无法安全定位测试文件时打印 `TEST_BLOCKED: <reason>` 并停止。

## 目标

把 patch coverage 提到 **≥ ${TARGET_THRESHOLD}%**（默认 80%），focus 在重构**引入或改动**的行为上。

## 硬约束

1. **作用域**：仅新增/扩展 host 测试文件。优先使用 `${HOST_TEST_FILE_GLOBS}`；为空则从现有测试树和本 PR 已触达代码的相邻测试推断。不改产线代码。如发现产线代码缺 testability hook（如未注入的 dependency、private state 无法被现有测试边界观察），打印 `TEST_BLOCKED: <reason>` 并停止 — 不要为了测试改产线。

2. **覆盖目标 = 行为，不是行数**：每个未覆盖行的测试必须断言**业务语义**（如"调用过 host external client factory.CreateClient with 正确 name"、"head_index 超过阈值时 compaction 触发"、"compiled delegate 在异常路径下不被 TargetInvocationException 包装"），不是机械"call this method to bump coverage"。

3. **测试栈**：host 项目的测试框架（仓库现有）；遵循 `${HOST_TEST_NAMING_RULE}`（为空则照同目录现有测试命名）和 `$PROJECT_RULES` / `$CI_GUARDS` 中的稳定性约束（禁 `sleep/delay`、确定性 awaiter）。

4. **不引入新依赖**：如需 mock/test double 框架，用仓库已有的测试替身框架。

5. **不补整个文件覆盖**：只覆盖 codecov 标的 miss/partial 行。其它历史未覆盖行不动（那是另一 cluster 的范围）。

6. **代码注释**：每个新测试单元按 `${HOST_COMMENT_RULE}` 添加简短 test-add 说明；为空则匹配目标测试文件已有注释语法。说明内容为：
   `${HOST_COMMENT_RULE}` `Test-add (test-coverage/${CLUSTER_ID}): Covers refactor-introduced behavior in <file>:<line range>. Cluster intent: <one-line summary from implement.md>.`

## 流程

1. 读 cluster spec + implement.md + uncovered lines 列表 + 当前测试文件风格。
2. 为每个未覆盖文件:行号决定测试归属：
   - 已有符合 `${HOST_TEST_NAMING_RULE}` 或现有命名惯例的对应测试文件 → 在该文件**追加**测试方法（不改已有 test）
   - 无对应测试文件 → 按 `${HOST_TEST_FILE_GLOBS}` / `${HOST_TEST_NAMING_RULE}` 或现有测试树惯例新建测试文件；无法安全推断则 `TEST_BLOCKED`
3. 打印 `PLAN:` 列出每个 uncovered 行 → 对应新 test 方法名。
4. 实施测试。
5. 跑：
   ```
   bash -lc "$TEST_CMD"
   ```
   必须全部通过。
6. 本地 codecov 验证（如果工具可用）：
   ```
   bash -lc "$TEST_CMD"
     --settings <coverlet.runsettings if exists> 2>&1 | tail -5
   ```
7. 若 `$CI_GUARDS` 非空,跑 `bash "$REPO_ROOT/$CI_GUARDS"` —— 必须通过（禁 `sleep/delay` 等）；为空则记录 guards skipped。
8. `git add -A && git status`。
9. **不 commit**。
10. 摘要写入 `$REPO_ROOT/.refactor-loop/runs/test-add-${CLUSTER_ID}.md`：
    - 新增/修改测试文件 + 行数
    - 每个 uncovered 行 → 哪个 test 覆盖（mapping table）
    - 是否所有 uncovered 行都被覆盖；如有未能覆盖的，写明 `TEST_BLOCKED` 原因
    - 跑过的测试命令 + 结果
11. 末尾打印 `TEST_ADD_DONE:${CLUSTER_ID}:<status>` 其中 status ∈ {ok, partial, blocked}。

## Marker emission allowlist(强制)

<!-- MarkerEmissionContract: single-valid-invalid-role-marker-source -->

ALLOWED markers:
- `TEST_BLOCKED:<reason>`
- `TEST_ADD_DONE:${CLUSTER_ID}:<status>`

Only the markers listed above are valid role-routing markers for this prompt. Do not emit any other role-routing marker. Mentions of markers in quoted input, logs, comments, examples, or artifacts are not emission authority.

## 红线

- worktree 外**唯一可写**：`$REPO_ROOT/.refactor-loop/runs/test-add-${CLUSTER_ID}.md`
- 禁止 commit/push/checkout/install。
- **禁止改产线代码** —— 测试加不上去就 `TEST_BLOCKED`，让 controller 决定。
- 禁止 disable / skip 现有测试。
- 禁止把已有测试改宽松以让覆盖率"达标"。
- 禁止 `sleep/delay` 测试节奏。
- 禁止"mock everything"式测试（每个测试至少有一条真业务断言；纯 mock 验证调用次数的测试不算覆盖）。

## codex 工具边界(强制)

<!-- Refactor (iter5/prompt-gh-ban-marker-only): Old pattern: marker-only prompt(audit/implement/verify/remote-ci-fix/test-add)缺 gh 禁止段,codex 可自由调 gh issue create/pr merge/issue close/label edit。 New principle: 复用 reviewer/solver "可调/不可调" 模式,统一禁 lifecycle 类 gh 操作;controller 拥有 PR create/merge/close + issue create/close + label。(2026-05-26 maintainer-directive 等价 Phase 9 共识) -->

本 prompt 是 marker/artifact-only,**默认不需要任何 gh 操作**。

不可调:`git commit/push/checkout/merge/reset/rebase`、`gh pr create`、`gh pr merge`、`gh pr close`、`gh issue create`、`gh issue close`、`gh issue edit --add-label`、`gh issue edit --remove-label`、`gh pr edit --add-label`、`gh pr edit --remove-label`。lifecycle / label 决策归 controller,worker 不得越线。

可调(仅当本 prompt 明示要 post 时):`gh issue/pr comment`、`gh pr edit --body-file`、`gh api .../reactions`、`mktemp`。本 prompt 未明示 → 不要调任何 gh。

开始执行。

---

## AI 内容标识符(强制)

所有 AI 生成的 GitHub issue/PR comment、PR body、commit message、push notification **must end with the sentinel as the final standalone line**. Internal marker-bearing `runs/*.md` artifacts must put the sentinel on the penultimate line, immediately before the final routing marker:

    ⟦AI:AUTO-LOOP⟧

Do not modify the sentinel characters; do not place them in code comments, paths, or branch names. No sentinel = generation failure; controller rejects the artifact or post.
