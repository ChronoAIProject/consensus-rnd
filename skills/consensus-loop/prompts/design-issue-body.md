# ${PROBLEM_TITLE}

<!-- Refactor (iter3/skill-host-language-policy): Old: prompt hardcoded host-language defaults  New: 6 HOST_* variables are optional and empty by default, injected by host.env (#20 structural consensus) -->

> Please reply according to `${HOST_WORK_LANGUAGE}`; do not add a mandatory parallel English section. Code identifiers, file paths, error messages, and rule quotes may remain verbatim.

---

## 1. 一段话说清楚

${PROBLEM_STATEMENT}

---

## 2. 具体示例

下面是当前代码里的真实问题模式。标 `← problem` 的行就是触发违反的位置。

```${HOST_CODE_FENCE_LANG}
${PROBLEM_EXAMPLE_CODE}
```

**文件**: `${PROBLEM_EXAMPLE_FILE_PATH}`

---

## 3. 为什么需要人来设计

${WHY_NEEDS_DESIGN}

---

## 4. 需要你的回答

加 `crnd:triage:resume-requested` 标签前请回答以下问题。Implement codex 会**原样**读取你的最新评论作为设计输入，所以请具体。

- [ ] **模式选择**：${DESIGN_QUESTION}
- [ ] **Schema 影响**：若 `${HOST_PROTO_POLICY}` 非空,按该 host schema/protocol 策略回答。如需新增 typed field 或 schema/protocol 变更,按 host 约定列出；无变更请明确说明。
- [ ] **向后兼容**：现有持久态如何处理？（reserved identifier / compatibility alias / schema migration / 可接受的重置）
- [ ] **Scope 拆分**：保留单 cluster 还是拆 N 个 PR？拆则给出 cluster id 草案。
- [ ] **测试面**：除了下方 cluster spec 里 `verification_hints` 之外，**必须**被测试的行为？
- [ ] **越界禁地**：implement codex **不应**碰的地方？

---

## 5. Auto-loop 行为（机制说明，**不影响你回答的内容**）

- Controller 在此 issue 是仅剩工作时大约每 1 小时轮询一次。
- Issue 打开后**首次**新评论触发 PushNotification 通知 operator；后续评论不重复推送（防打扰）。
- 加 `crnd:triage:resume-requested` 标签 → controller 把你的最新评论作为 `## Design decision (from issue #${ISSUE_NUMBER})` 段拼到新 implement codex prompt 前面 dispatch。Implement 在独立 worktree 跑，开 PR 回到 `auto-refact-dev`，PR 一开自动关闭本 issue。
- 不加 `crnd:triage:resume-requested` 标签直接关闭 → 判定"设计被拒绝；cluster 永久搁置"，controller 在 GitHub / run artifact 记录 `design-rejected:closed`。

---

## 6. 技术参考（可折叠）

<details>
<summary>展开完整 cluster YAML / 证据 / audit 修复边界</summary>

### Cluster spec (from `.refactor-loop/runs/audit-iter-${ITERATION}.md`)

${CLUSTER_YAML}

### 证据

${CLUSTER_EVIDENCE}

### audit 初步提议

${CLUSTER_FIX_BOUNDARY}

</details>

cc: @<maintainer-handle-from-$MAINTAINER_WHITELIST>（auto-loop 运维者）

---

## AI content identifier (mandatory)

Every AI-authored external artifact (GitHub issue/PR comment, PR body, commit message, `runs/*.md` artifact, or push notification) **must end with the sentinel as the final standalone line**:

    ⟦AI:AUTO-LOOP⟧

Do not modify the sentinel characters; do not place them in code comments, paths, or branch names. No sentinel means generation failure and the controller rejects the post.
