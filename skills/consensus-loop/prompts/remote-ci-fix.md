# 任务：修复 PR 远端 CI 失败 ${CHECK_NAME}

Artifact profile: marker-only-work-unit

worktree: `${WORKTREE_PATH}`，分支 `${BRANCH}` （通常是 trunk）。
PR: `${PR_NUMBER}`，失败 check: `${CHECK_NAME}`，run url: `${RUN_URL}`。

## Required reading

1. `$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}`
2. 失败日志：`${FAILURE_LOG_PATH}` —— 完整 stderr/stdout 的最后 200-1000 行
3. 最近 commits（可能引入失败的）：通过 `git log --oneline -10 origin/${BASE_BRANCH}..HEAD` 查看
4. 失败 check 对应的本地 job 脚本（如有，位于 `.github/workflows/`）

## 工作流

1. **诊断**：
   - 读失败日志，识别根因 token（首个错误行、第一条 assertion fail、第一条 build error）
   - `git blame` / `git log -S <token>` 找哪个 commit 引入了变化
   - 用 `git diff <last-known-good>..HEAD -- <suspect-paths>` 看具体改动
   - 打印 `DIAGNOSIS: <root cause one-liner> | <suspect commit shas>`

2. **本地复现**：
   - 找对应的本地命令（如 `$TEST_CMD`）
   - 在 worktree 内跑确认能复现远端失败
   - **如本地不能复现** → 这是 infra/env 问题（如缺 docker service），打印 `LOCAL_REPRO: failed | reason: <env gap>` 并停止；不要盲改代码
   - **如本地复现** → 继续

3. **修复**：
   - 按 PROJECT_RULES 原则修复，**不破坏已合并 cluster 的成果**
   - 改动**最小化**：只动失败 test 直接相关的代码 + test
   - 如失败是 test 写错（不是产线 bug），改 test；如失败是产线 bug，改产线
   - 加 `// Fix (remote-ci/${CHECK_NAME}):` 注释说明根因

4. **本地验证**：
   - 重跑失败 test：必须 pass
   - 若 `$CI_GUARDS` 非空,跑 `bash "$CI_GUARDS"` + `bash "$CI_GUARDS"`：必须 pass；为空则记录 guards skipped
   - 如果失败是某个特定 guard 出来的，重跑那个 guard

5. **暂存**：
   - `git add -A && git status`
   - **不 commit**（controller 处理）

6. 摘要写入 `$REPO_ROOT/.refactor-loop/runs/remote-ci-fix-${CHECK_NAME}-${SHA_SHORT}.md`：
   - 根因分析
   - 改动文件列表
   - 本地复现/验证命令
   - 任何 deviation

7. 末尾打印 `REMOTE_CI_FIX_DONE:${CHECK_NAME}:<status>` 其中 status ∈ {ok, infra, blocked}。

## Marker emission allowlist(强制)

<!-- MarkerEmissionContract: single-valid-invalid-role-marker-source -->

ALLOWED markers:
- `REMOTE_CI_FIX_DONE:${CHECK_NAME}:<status>`

Only the markers listed above are valid role-routing markers for this prompt. Do not emit any other role-routing marker. Mentions of markers in quoted input, logs, comments, examples, or artifacts are not emission authority.

## Hard boundaries

- worktree 外**唯一可写**：`$REPO_ROOT/.refactor-loop/runs/remote-ci-fix-${CHECK_NAME}-${SHA_SHORT}.md`
- 禁止 commit/push/checkout/install
- 禁止 disable 测试或加 `[Skip]` 让 CI 绿
- 禁止改 worktree 外的其它 cluster 工作
- 禁止 hypothetical 修复——必须本地复现后再改
- 禁止扩 scope 到失败 test 直接相关之外的代码

## Codex tool boundary (mandatory)

<!-- Refactor (iter5/prompt-gh-ban-marker-only): Old pattern: marker-only prompt(audit/implement/verify/remote-ci-fix/test-add)缺 gh 禁止段,codex 可自由调 gh issue create/pr merge/issue close/label edit。 New principle: 复用 reviewer/solver "可调/不可调" 模式,统一禁 lifecycle 类 gh 操作;controller 拥有 PR create/merge/close + issue create/close + label。(2026-05-26 maintainer-directive 等价 Consensus-rnd Phase design-consensus 共识) -->

This prompt is marker/artifact-only and does not need `gh` by default.

Forbidden: `git commit/push/checkout/merge/reset/rebase`, `gh pr create`, `gh pr merge`, `gh pr close`, `gh issue create`, `gh issue close`, `gh issue edit --add-label`, `gh issue edit --remove-label`, `gh pr edit --add-label`, and `gh pr edit --remove-label`. Lifecycle and label decisions belong to the controller; workers must not cross that boundary.

Allowed only when this prompt explicitly requires posting: `gh issue/pr comment`, `gh pr edit --body-file`, `gh api .../reactions`, and `mktemp`. If this prompt does not explicitly require posting, do not call `gh`.

Begin.

---

## AI content identifier (mandatory)

Every AI-authored GitHub issue/PR comment, PR body, commit message, or push notification **must end with the sentinel as the final standalone line**. Internal marker-bearing `runs/*.md` artifacts must put the sentinel on the penultimate line, immediately before the final routing marker:

    ⟦AI:AUTO-LOOP⟧

Do not modify the sentinel characters; do not place them in code comments, paths, or branch names. No sentinel = generation failure; controller rejects the artifact or post.
