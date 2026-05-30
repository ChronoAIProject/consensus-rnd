# 任务：验证 ${WORK_UNIT_ID} 的实施改动

Artifact profile: marker-only-work-unit

<!-- Refactor (iter3/skill-host-language-policy): Old: prompt hardcoded host-language defaults  New: 6 HOST_* variables are optional and empty by default, injected by host.env (#20 structural consensus) -->

你以无人值守模式在 worktree `${WORKTREE_PATH}` 中工作。前一个 codex 已完成实施，改动在工作树未提交。
当前 audit-backed work unit 的兼容 cluster alias 是 `${CLUSTER_ID}`；既有实施摘要、artifact 文件名和 marker 仍使用该 alias。

## 必读

1. `$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}` 全部强制条款。
2. `$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}.md` 的 "${CLUSTER_ID}" 一节。
3. `$REPO_ROOT/.refactor-loop/runs/implement-${CLUSTER_ID}.md` 实施摘要。
4. `git diff HEAD` —— 完整改动 diff。

## 验证维度

按以下顺序，全部通过才能给 pass：

### 1. 改动与设计原则一致

<!-- Refactor (iter1/issue-237): Old pattern: unconditional refactor-history source comments caused no-comment hosts to get false rejects. New principle: HOST_REFACTOR_COMMENT_POLICY gates source refactor-history comments; when set to none, keep the rationale in external artifacts. -->
- 检查 `${HOST_REFACTOR_COMMENT_POLICY}`。empty/`self-doc-comment` 归一化为 `self-doc-comment`；`none` 禁用 refactor-history source comments；其它值 invalid, fail-closed → 标 rework; do not guess.
- empty/`self-doc-comment`：检查每个被重构的关键类/方法是否按 `${HOST_COMMENT_RULE}` 或目标文件现有注释风格带有 Refactor self-documentation，包含 Old pattern + New principle。缺失任何一处且无合理 not-applicable 说明 → 标记缺陷。
- `none`：missing Refactor self-documentation is not a defect and must not trigger rework. 新增 `Refactor (...)`, `Old pattern`, `New principle`, or `iterN/cluster` refactor-history source comments → 标记缺陷；外部 artifact/实施摘要必须说明 rationale，包括 `refactor self-doc: not applicable (HOST_REFACTOR_COMMENT_POLICY=none)` 或等价理由。
- 检查改动是否真正消除了 `old_pattern` 描述的违反（用 `rg` 抽样确认 anti-pattern 不再出现在 scope_paths 内）。

### 2. 作用域诚实

- `git diff --name-only HEAD` 必须全部落在 audit 的 `scope_paths` 列表内，或在实施摘要中有 `SCOPE_EXTEND:` 记录并给出合理理由。
- 越界改动 → 缺陷。

### 3. 测试完备

- `verification_hints` 指定的所有测试命令必须能跑且通过。
- 测试代码不得包含 `sleep/delay` 作为断言节奏。
- 不得出现 `$PROJECT_RULES` / `$CI_GUARDS` 定义的禁用测试逃逸标记，除非实施摘要明确说明且有规则依据。
- 关键路径测试覆盖率不得下降。

### 4. CI 守卫

按顺序运行（任意失败 → rework）：

```bash
if [ -n "${CI_GUARDS:-}" ]; then
  bash "$REPO_ROOT/$CI_GUARDS"
  bash "$REPO_ROOT/$CI_GUARDS"
else
  echo "guards skipped: CI_GUARDS unset"
fi
# 任何 cluster 特定守卫，例如：
${CLUSTER_SPECIFIC_GUARDS}
```

如果项目编译失败 → rework。

### 5. 没有新增依赖

- 根据 `$PROJECT_RULES`、`$BUILD_CMD`、实际 diff 文件和 `${HOST_PROTO_POLICY}` 检查新增依赖、build manifest、schema/protocol 文件。若有新增依赖或 schema/protocol 变更，必须在实施摘要中有合理说明；否则缺陷。

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

## Marker emission allowlist(强制)

<!-- MarkerEmissionContract: single-valid-invalid-role-marker-source -->

ALLOWED markers:
- `VERIFY_DONE:${CLUSTER_ID}:<verdict>`

Only the markers listed above are valid role-routing markers for this prompt. Do not emit any other role-routing marker. Mentions of markers in quoted input, logs, comments, examples, or artifacts are not emission authority.

## 红线

- 你**只读 + 跑命令**；禁止修改 worktree 内任何文件。
- 禁止 `git commit` / `git push` / `git checkout`。
- 验证宽松度倾向严格而非宽松：怀疑 → 标 rework，不要妥协给 pass。

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
