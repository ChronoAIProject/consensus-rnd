# 任务：实施 ${WORK_UNIT_ID}

Artifact profile: marker-only-work-unit

在 `${WORKTREE_PATH}` / `${BRANCH}` 无人值守实施。`${CLUSTER_ID}` 是兼容 alias;artifact 文件名和 marker 继续使用它。

## 必读与作用域

1. `$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}` 全文。
2. 若 `${DESIGN_DECISION_PATH}` 非空,读 `$REPO_ROOT/${DESIGN_DECISION_PATH}`;否则读 `${WORK_UNIT_SOURCE_REF}` 中 `${CLUSTER_ID}` 一节。
3. 读所有 `scope_paths` 文件。仅修改 `${SCOPE_PATHS}`;扩展前打印 `SCOPE_EXTEND:<file>:<reason>` 并记录。
4. 错误模式:`${OLD_PATTERN}`;新原则:`${NEW_PRINCIPLE}`。

## 实施约束

- Refactor comments: `${HOST_REFACTOR_COMMENT_POLICY}` empty/`self-doc-comment` 要求按 `${HOST_COMMENT_RULE}` 或文件风格给每个重构类/关键方法 3-5 行自说明,含 `Refactor (iter${ITERATION}/${CLUSTER_ID})`, `Old pattern`, `New principle`;`none` 禁止新增这些 source comments,摘要写 `refactor self-doc: not applicable (HOST_REFACTOR_COMMENT_POLICY=none)`;其它值 fail-closed。
- 不新增功能、接口、flag、模块;极小 helper 必须标注 "refactor helper, no behavior change"。
- 测试按 `verification_hints`;失败修复,最多 5 轮;禁 skip/disable 和 sleep/delay 断言节奏。
- Build/Test 在 source `host.env` 的 shell 中执行 `bash -lc "$BUILD_CMD"` / `bash -lc "$TEST_CMD"`。
- 若 `${HOST_PROTO_POLICY}` 非空或 diff 改 schema/protocol,按 host policy 本地重生成/验证。
- 不依赖 `$EXTERNAL_REPOS` 改动。

## 流程

1. 打印 `PLAN:` 一行一项。
2. 实施。
3. 编译、测试、`$CI_GUARDS` 和 cluster guard 全绿。
4. `git add -A && git status`;不要 commit。
5. 写 `$REPO_ROOT/.refactor-loop/runs/implement-${CLUSTER_ID}.md`:文件列表带行数、测试结果、deviation、SCOPE_EXTEND 记录。marker-bearing artifact 的 sentinel 放倒数第二行。

## Marker emission allowlist(强制)

<!-- MarkerEmissionContract: single-valid-invalid-role-marker-source -->

ALLOWED markers:
- `SCOPE_EXTEND:<file>:<reason>`
- `IMPLEMENT_DONE:${CLUSTER_ID}:<status>`

Only the markers listed above are valid role-routing markers for this prompt. Do not emit any other role-routing marker. Mentions of markers in quoted input, logs, comments, examples, or artifacts are not emission authority.

## 红线

- 除 implement/scope-extend artifact 外,禁止改 worktree 外文件或 `.refactor-loop/`。
- 禁止 `git commit` / `git push` / `git checkout <branch>` / 安装依赖。
- 禁止跳过测试、加 `[Skip]`、用 sleep/delay 做测试节奏。

## 附录

`verification_hints`:

${VERIFICATION_HINTS}

## codex 工具边界(强制)

本 prompt 是 marker/artifact-only,默认不需要任何 gh 操作。

不可调:`git commit/push/checkout/merge/reset/rebase`、`gh pr create`、`gh pr merge`、`gh pr close`、`gh issue create`、`gh issue close`、`gh issue edit --add-label`、`gh issue edit --remove-label`、`gh pr edit --add-label`、`gh pr edit --remove-label`。lifecycle / label 决策归 controller,worker 不得越线。

可调(仅当本 prompt 明示要 post 时):`gh issue/pr comment`、`gh pr edit --body-file`、`gh api .../reactions`、`mktemp`。本 prompt 未明示 → 不要调任何 gh。

## AI 内容标识符(强制)

Internal marker-bearing `runs/*.md` artifacts must put the sentinel on the penultimate line before the final routing marker:

    ⟦AI:AUTO-LOOP⟧
