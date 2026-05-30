# ${PROBLEM_TITLE}

> 请用中文回复。Code identifier、file path、错误消息和条款引用可以保留原文。

## 1. 一段话说清楚

${PROBLEM_STATEMENT}

## 2. 具体示例

```${HOST_CODE_FENCE_LANG}
${PROBLEM_EXAMPLE_CODE}
```

文件: `${PROBLEM_EXAMPLE_FILE_PATH}`

## 3. 为什么需要人来设计

${WHY_NEEDS_DESIGN}

## 4. 需要你的回答

加 `crnd:triage:resume-requested` 标签前请具体回答:

- 模式选择:${DESIGN_QUESTION}
- Schema 影响:若 `${HOST_PROTO_POLICY}` 非空,按 host schema/protocol 策略回答;无变更请明确。
- 向后兼容:持久态如何处理。
- Scope 拆分:单 cluster 还是拆 N 个 PR。
- 测试面:除 `verification_hints` 外必须覆盖的行为。
- 越界禁地:implement codex 不应碰哪里。

## 5. Auto-loop 行为

- controller 轮询;首次新评论触发通知。
- 加 `crnd:triage:resume-requested` 后,controller 把最新评论作为 design decision dispatch implement。
- 不加 label 直接关闭表示设计拒绝。

## 6. 技术参考

<details>
<summary>完整 cluster YAML / 证据 / audit 修复边界</summary>

### Cluster spec

${CLUSTER_YAML}

### 证据

${CLUSTER_EVIDENCE}

### audit 初步提议

${CLUSTER_FIX_BOUNDARY}

</details>

cc: @<maintainer-handle-from-$MAINTAINER_WHITELIST>

## AI 内容标识符(强制)

所有 AI 生成的对外内容必须末尾独立一行加 sentinel:

    ⟦AI:AUTO-LOOP⟧
