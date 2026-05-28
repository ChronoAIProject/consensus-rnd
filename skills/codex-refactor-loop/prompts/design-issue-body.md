# ${PROBLEM_TITLE}

> Reply in Chinese. Code identifiers, file paths, errors, and quoted rule text may stay verbatim.

Facts are injected from `source .refactor-loop/host.env`; preserve `${HOST_*}` placeholders.

## Summary

${PROBLEM_STATEMENT}

## Concrete Example

The marked lines show the current violation.

```${HOST_CODE_FENCE_LANG}
${PROBLEM_EXAMPLE_CODE}
```

File: `${PROBLEM_EXAMPLE_FILE_PATH}`

## Why Design Is Needed

${WHY_NEEDS_DESIGN}

## Decision Requested

Before adding `auto-loop-resume`, answer:

- Mode choice: ${DESIGN_QUESTION}
- Schema impact: if `${HOST_PROTO_POLICY}` is non-empty, answer using that host schema/protocol policy; otherwise state no schema change.
- Compatibility: persistent state handling, reserved field numbers, aliases, migration, or acceptable reset.
- Scope split: one cluster or N PRs; if split, provide draft cluster ids.
- Test surface: behavior that must be tested beyond `verification_hints`.
- Off-limits: files or areas implement codex must not touch.

## Auto-Loop Mechanics

- Controller polls roughly hourly when this is the remaining work.
- First new comment after issue creation triggers one operator notification; later comments do not.
- Adding `auto-loop-resume` lets controller append the newest maintainer comment as design input and dispatch implement in an isolated worktree.
- Closing without `auto-loop-resume` means design rejected and the cluster is marked failed.

## Technical Reference

<details>
<summary>Cluster YAML, evidence, and audit boundary</summary>

### Cluster spec

${CLUSTER_YAML}

### Evidence

${CLUSTER_EVIDENCE}

### Audit fix boundary

${CLUSTER_FIX_BOUNDARY}

</details>

cc: @<maintainer-handle-from-$MAINTAINER_WHITELIST>

⟦AI:AUTO-LOOP⟧
