# 任务：补测试覆盖重构引入的未覆盖代码 — ${CLUSTER_ID}

worktree: `${WORKTREE_PATH}`，分支 `${BRANCH}`。

## 必读

1. `$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}` 全部强制条款（含 "Codex CLI 调用规范"、"测试与质量门禁"）。
2. `$REPO_ROOT/.refactor-loop/runs/audit-iter-N.md` 中 `${CLUSTER_ID}` 一节
3. `$REPO_ROOT/.refactor-loop/runs/implement-${CLUSTER_ID}.md`
4. **未覆盖行报告**：以下文件:行号 是 codecov 标记为 patch miss/partial 的位置：

```
${UNCOVERED_LINES}
```

5. 现有测试项目结构（`test/该项目.*.Tests/`），参考同模式现有测试风格。

## 目标

把 patch coverage 提到 **≥ ${TARGET_THRESHOLD}%**（默认 80%），focus 在重构**引入或改动**的行为上。

## 硬约束

1. **作用域**：仅新增/扩展测试文件（`test/**/*.cs`），不改产线代码。如发现产线代码缺 testability hook（如未注入的 dependency、private state 无 internals visibility），打印 `TEST_BLOCKED: <reason>` 并停止 — 不要为了测试改产线。

2. **覆盖目标 = 行为，不是行数**：每个未覆盖行的测试必须断言**业务语义**（如"调用过 host external client factory.CreateClient with 正确 name"、"head_index 超过阈值时 compaction 触发"、"compiled delegate 在异常路径下不被 TargetInvocationException 包装"），不是机械"call this method to bump coverage"。

3. **测试栈**：host 项目的测试框架（仓库现有）；遵循 `*Tests.cs` 命名 + `test_stability_guards.sh` 约束（禁 `sleep/delay`、确定性 awaiter）。

4. **不引入新依赖**：如需 mock 框架，用仓库已有的（host 项目的测试替身框架 / host 项目的测试替身框架 中的一个）。

5. **不补整个文件覆盖**：只覆盖 codecov 标的 miss/partial 行。其它历史未覆盖行不动（那是另一 cluster 的范围）。

6. **代码注释**：每个新测试 class 加：
   ```csharp
   // Test-add (test-coverage/${CLUSTER_ID}):
   //   Covers refactor-introduced behavior in <file>:<line range>.
   //   Cluster intent: <one-line summary from implement.md>.
   ```

## 流程

1. 读 cluster spec + implement.md + uncovered lines 列表 + 当前测试文件风格。
2. 为每个未覆盖文件:行号决定测试归属：
   - 已有对应 `*Tests.cs` → 在该文件**追加**测试方法（不改已有 test）
   - 无对应测试文件 → 新建 `<TypeName>Tests.cs` 在合适的 `test/该项目.<Project>.Tests/` 下
3. 打印 `PLAN:` 列出每个 uncovered 行 → 对应新 test 方法名。
4. 实施测试。
5. 跑：
   ```
   $TEST_CMD
   ```
   必须全部通过。
6. 本地 codecov 验证（如果工具可用）：
   ```
   $TEST_CMD
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

## 红线

- worktree 外**唯一可写**：`$REPO_ROOT/.refactor-loop/runs/test-add-${CLUSTER_ID}.md`
- 禁止 commit/push/checkout/install。
- **禁止改产线代码** —— 测试加不上去就 `TEST_BLOCKED`，让 controller 决定。
- 禁止 disable / skip 现有测试。
- 禁止把已有测试改宽松以让覆盖率"达标"。
- 禁止 `sleep/delay` 测试节奏。
- 禁止"mock everything"式测试（每个测试至少有一条真业务断言；纯 mock 验证调用次数的测试不算覆盖）。

开始执行。

---

## AI 内容标识符(强制)

所有 AI 生成的对外内容(GitHub issue/PR comment、PR body、commit message、`runs/*.md` artifact、push notification)**必须末尾独立一行**加 sentinel:

    ⟦AI:AUTO-LOOP⟧

不可修改字符 / 不放代码注释 / 不放路径分支名。无 sentinel = 产生失败,controller 拒绝 post。
