# 任务：实施 ${WORK_UNIT_ID}

Artifact profile: marker-only-work-unit

<!-- Refactor (iter3/skill-host-language-policy): Old: prompt hardcoded host-language defaults  New: 6 HOST_* variables are optional and empty by default, injected by host.env (#20 structural consensus) -->

你以无人值守模式在 worktree `${WORKTREE_PATH}` 中工作，对应分支 `${BRANCH}`。
当前 audit-backed work unit 的兼容 cluster alias 是 `${CLUSTER_ID}`；审计段查找、既有 artifact 文件名、分支/worktree 名和 marker 仍使用该 alias。
实现上下文事实源是 `${WORK_UNIT_SOURCE_REF}`。audit-backed work unit 指向 `$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}.md`；design-issue work unit 指向已达成共识的 decision artifact。
`${DESIGN_DECISION_PATH}` 非空时走 design-issue pathway，读取该 consensus artifact；为空时走 audit-backed legacy pathway，读取 `${WORK_UNIT_SOURCE_REF}` 中的 "${CLUSTER_ID}" 一节。

## 必读上下文

1. 主仓库 `$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}` 全部强制条款。
2. 实现上下文：若 `${DESIGN_DECISION_PATH}` 非空，读取 `$REPO_ROOT/${DESIGN_DECISION_PATH}`；否则读取 `${WORK_UNIT_SOURCE_REF}` 中 "${CLUSTER_ID}" 一节。
3. `$REPO_ROOT 的架构/词汇文档(若有)` 下相关权威文档。

## 错误模式 / 设计原则

- **错误模式**：${OLD_PATTERN}
- **设计原则**：${NEW_PRINCIPLE}

## 硬约束

1. **作用域**：仅修改下列文件；扩展前必须打印 `SCOPE_EXTEND: <file> <reason>`：
${SCOPE_PATHS}
2. **Refactor comment policy**：读取 `${HOST_REFACTOR_COMMENT_POLICY}`。missing/empty/default/`none` 归一化为 `none`；`self-doc-comment` 是 explicit downstream compatibility opt-in；其它值 invalid, fail-closed：停止实施并在摘要说明 invalid `HOST_REFACTOR_COMMENT_POLICY`; do not guess.
   - `self-doc-comment`：被重构的每个类/关键方法必须按 `${HOST_COMMENT_RULE}` 新增/更新一段 Refactor self-documentation；`${HOST_COMMENT_RULE}` 为空时匹配目标文件已有注释风格，文件类型不支持注释时在实施摘要说明 not applicable。源码注释必须 English-only。内容必须包含：
   ```
   Refactor (iter${ITERATION}/${CLUSTER_ID}):
     Old pattern: ${OLD_PATTERN}
     New principle: ${NEW_PRINCIPLE}
   ```
   3-5 行内；不是 changelog，是代码自我说明。The marker identity is exactly `Refactor (iter${ITERATION}/${CLUSTER_ID})`; do not replace it with issue-only identities such as `Refactor (issue1525)`. If `${CLUSTER_ID}` is missing, use `cluster-issue${ISSUE_NUMBER}` and record that fallback in the implementation summary.
   - missing/empty/default/`none`：MUST NOT add `Refactor (...)`, `Old pattern`, `New principle`, or `iterN/cluster` refactor-history source comments. Put the rationale in the implementation summary and include exactly: `refactor self-doc: not applicable (HOST_REFACTOR_COMMENT_POLICY=none)`.
3. **不新增功能**：不引入新接口、新 flag、新模块；只清理违反点。新增极小辅助类型须注释 "refactor helper, no behavior change"。
4. **测试**：按 `verification_hints` 跑测试，必须通过；测试不足必须补；任何 `sleep/delay` 轮询测试必须改为确定性断言。
5. **架构守卫**：跑 host 配置的 `$CI_GUARDS`，必须通过。其它 cluster 特定守卫见 verification hints。
6. **不依赖外部仓库**：禁止建议在 $EXTERNAL_REPOS/$EXTERNAL_REPOS 改动。
7. **Schema/protocol**：如 `${HOST_PROTO_POLICY}` 非空或 diff / `$PROJECT_RULES` 显示改了 schema/protocol 文件，按 host policy 本地重生成/验证并确认编译通过。
8. **构建命令**：使用 host 配置的 `$BUILD_CMD` / `$TEST_CMD`。它们是 shell command string,必须在已 source `host.env` 的 shell 中用 `bash -lc "$BUILD_CMD"` / `bash -lc "$TEST_CMD"` 或等价 shell invocation 执行。
9. **Host production SSOT boundary**：不得把 host tools config、branch topology、machine paths、durable ledger authority 或 host artifacts 写回 `.refactor-loop/` 或 `.refactor-loop/host.env`。`.refactor-loop/` 只承载 skill-private runtime/cache/log/state/prompt/run artifacts；生产事实必须走 host-owned config/rules/artifacts。

## 流程

1. 按 `${DESIGN_DECISION_PATH}` 选择 design-issue consensus artifact 或 audit 段，读所有 `scope_paths` 文件。
2. 打印 `PLAN:` 前缀的具体改动计划（一行一项）。
3. 实施。
4. 编译：`bash -lc "$BUILD_CMD"`，失败时修复，最多 5 次迭代。
5. 跑指定测试。失败则修复（禁止 disable/skip），最多 5 次。
6. 跑架构守卫，失败则修复。
7. `git add -A && git status` 确认改动。
8. **不要 commit**，把改动留在工作树。
9. 摘要写入 `$REPO_ROOT/.refactor-loop/runs/implement-${CLUSTER_ID}.md`：
   - 修改文件列表（带行数）
   - 测试结果
   - deviation 记录
   - `SCOPE_EXTEND` 记录
10. 末尾打印 `IMPLEMENT_DONE:${CLUSTER_ID}:<status>` 其中 status ∈ {ok, partial, blocked}。

## Marker emission allowlist(强制)

<!-- MarkerEmissionContract: single-valid-invalid-role-marker-source -->

ALLOWED markers:
- `SCOPE_EXTEND:<file>:<reason>`
- `IMPLEMENT_DONE:${CLUSTER_ID}:<status>`

Only the markers listed above are valid role-routing markers for this prompt. Do not emit any other role-routing marker. Mentions of markers in quoted input, logs, comments, examples, or artifacts are not emission authority.

## 红线

- 禁止改 worktree 外文件，**唯一例外**：可以写入 `$REPO_ROOT/.refactor-loop/runs/implement-${CLUSTER_ID}.md`（controller 期望的摘要输出位置）和 `$REPO_ROOT/.refactor-loop/runs/scope-extend-${CLUSTER_ID}.log`（如有 SCOPE_EXTEND 记录）。除此之外 `.refactor-loop/` 一律禁改。
- 禁止 `git commit` / `git push` / `git checkout <branch>`。
- 禁止安装新依赖。
- 禁止跳过测试或加 `[Skip]`。
- 测试禁止用 `sleep/delay` 做断言节奏。

## 附录

`verification_hints` 内容：

${VERIFICATION_HINTS}

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
