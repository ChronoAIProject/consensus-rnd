# ${PROBLEM_TITLE_EN} / ${PROBLEM_TITLE_ZH}

> **Bilingual / 双语**. English and 中文 sections are fully equivalent. Read either. Reply in either.

---

## 1. What's broken (in plain language) / 一段话说清楚

### English

${PROBLEM_STATEMENT_EN}

### 中文

${PROBLEM_STATEMENT_ZH}

---

## 2. Concrete example / 具体示例

The code below is the actual offending pattern in the current codebase. Each line marked `← problem` is what triggers the violation.

下面是当前代码里的真实问题模式。标 `← problem` 的行就是触发违反的位置。

```csharp
${PROBLEM_EXAMPLE_CODE}
```

**File / 文件**: `${PROBLEM_EXAMPLE_FILE_PATH}`

---

## 3. Why this needs human design (not auto-refactor) / 为什么需要人来设计

### English

${WHY_NEEDS_DESIGN_EN}

### 中文

${WHY_NEEDS_DESIGN_ZH}

---

## 4. What we need from you / 需要你的回答

### English

Please answer the following before adding the `auto-loop-resume` label. The implement codex will read your latest comment verbatim, so be specific.

- [ ] **Pattern choice / 模式选择**: ${DESIGN_QUESTION_PATTERN_EN}
- [ ] **Proto schema impact / Proto 影响**: If new typed fields are needed, sketch them (message names + field numbers). If no proto change, say so.
- [ ] **Backward compatibility / 向后兼容**: How to handle existing persisted state (reserved field numbers, alias, migration, accepted reset)?
- [ ] **Scope split / 拆分**: One cluster or split into N PRs? If split, sketch the cluster ids.
- [ ] **Test surface / 测试面**: What behavior MUST be tested beyond `verification_hints` in the cluster spec below?
- [ ] **Out-of-scope guard rails / 越界禁地**: Anything the implement codex must NOT touch?

### 中文

加 `auto-loop-resume` 标签前请回答以下问题。Implement codex 会**原样**读取你的最新评论作为设计输入，所以请具体。

- [ ] **Pattern choice / 模式选择**：${DESIGN_QUESTION_PATTERN_ZH}
- [ ] **Proto schema 影响**：如需新增 typed field，列出 message 名 + field number；无 proto 改动请明确说明。
- [ ] **向后兼容**：现有持久态如何处理？（reserved 字段号 / type alias / schema migration / 可接受的重置）
- [ ] **Scope 拆分**：保留单 cluster 还是拆 N 个 PR？拆则给出 cluster id 草案。
- [ ] **测试面**：除了下方 cluster spec 里 `verification_hints` 之外，**必须**被测试的行为？
- [ ] **越界禁地**：implement codex **不应**碰的地方？

---

## 5. Auto-loop behavior / Auto-loop 行为（机制说明，**不影响你回答的内容**）

### English

- Controller polls this issue every ~1 hour when this is the only remaining work.
- First new comment after issue opens → PushNotification to operator. Subsequent comments do NOT re-notify (anti-spam).
- Adding `auto-loop-resume` label → controller prepends your latest comment as `## Design decision (from issue #${ISSUE_NUMBER})` to a fresh implement codex prompt and dispatches. Implement runs in an isolated worktree, opens a PR back to `auto-refact-dev`, and closes this issue on PR open.
- Closing the issue **without** `auto-loop-resume` label → "design rejected; cluster permanently deferred", controller marks `clusters_failed[design-rejected:closed]`.

### 中文

- Controller 在此 issue 是仅剩工作时大约每 1 小时轮询一次。
- Issue 打开后**首次**新评论触发 PushNotification 通知 operator；后续评论不重复推送（防打扰）。
- 加 `auto-loop-resume` 标签 → controller 把你的最新评论作为 `## Design decision (from issue #${ISSUE_NUMBER})` 段拼到新 implement codex prompt 前面 dispatch。Implement 在独立 worktree 跑，开 PR 回到 `auto-refact-dev`，PR 一开自动关闭本 issue。
- 不加 `auto-loop-resume` 标签直接关闭 → 判定"设计被拒绝；cluster 永久搁置"，controller 标记 `clusters_failed[design-rejected:closed]`。

---

## 6. Reference: full cluster spec / 技术参考（可折叠）

<details>
<summary>Click to expand cluster YAML + evidence + audit's fix boundary / 展开完整 cluster YAML / 证据 / audit 修复边界</summary>

### Cluster spec (from `.refactor-loop/runs/audit-iter-${ITERATION}.md`)

${CLUSTER_YAML}

### Evidence / 证据

${CLUSTER_EVIDENCE}

### Fix boundary (audit's initial proposal) / audit 初步提议

${CLUSTER_FIX_BOUNDARY}

</details>

cc: @<maintainer-handle-from-$MAINTAINER_WHITELIST> (auto-loop operator / 运维者)

---

## AI 内容标识符(强制)

所有 AI 生成的对外内容(GitHub issue/PR comment、PR body、commit message、`runs/*.md` artifact、push notification)**必须末尾独立一行**加 sentinel:

    ⟦AI:AUTO-LOOP⟧

不可修改字符 / 不放代码注释 / 不放路径分支名。无 sentinel = 产生失败,controller 拒绝 post。
