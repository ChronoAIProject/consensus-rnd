# ${PROBLEM_TITLE}
<!-- Refactor (iter5/prompts-compression): Old pattern: English-heavy design issue template. New principle: Chinese GitHub-facing scaffold with host placeholders and evidence details. -->

> GitHub-facing comments / PR bodies are 中文 by default; identifiers / paths / quoted rule text remain verbatim inline; no mandatory parallel English section.

Facts are injected from `source .refactor-loop/host.env`; preserve `${HOST_*}` placeholders.

## 摘要

${PROBLEM_STATEMENT}

## 具体现象

以下标记行展示当前违反点。

```${HOST_CODE_FENCE_LANG}
${PROBLEM_EXAMPLE_CODE}
```

文件: `${PROBLEM_EXAMPLE_FILE_PATH}`

## 为什么需要设计决策

${WHY_NEEDS_DESIGN}

## 需要维护者确认

添加 `auto-loop-resume` 前,请确认:

- 模式选择: ${DESIGN_QUESTION}
- Schema 影响: 若 `${HOST_PROTO_POLICY}` 非空,按该 host schema/protocol policy 回答;否则说明无 schema 变更。
- 兼容性: persistent state、reserved field numbers、aliases、migration,或可接受 reset。
- 范围拆分: 一个 cluster 或 N 个 PR;若拆分,请给 draft cluster ids。
- 测试面: 除 `verification_hints` 外必须覆盖的行为。
- 禁区: implement codex 不得触碰的文件或区域。

## Auto-Loop 机制

- 当这是剩余工作时,controller 约每小时轮询一次。
- issue 创建后的第一条新评论会触发一次 operator 通知;后续评论不会重复通知。
- 添加 `auto-loop-resume` 后,controller 会把最新 maintainer 评论作为 design input 并在隔离 worktree 派发 implement。
- 未添加 `auto-loop-resume` 就关闭 issue,表示设计被拒绝,该 cluster 标记为 failed。

## 技术参考

<details>
<summary>Cluster YAML、证据与 audit 边界</summary>

### Cluster spec

${CLUSTER_YAML}

### Evidence

${CLUSTER_EVIDENCE}

### Audit fix boundary

${CLUSTER_FIX_BOUNDARY}

</details>

cc: @<maintainer-handle-from-$MAINTAINER_WHITELIST>

AI 内容标识符 `⟦AI:AUTO-LOOP⟧` must be the 末尾独立一行.
