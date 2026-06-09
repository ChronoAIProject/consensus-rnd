# ${PROBLEM_TITLE}

<!-- Refactor (iter3/skill-host-language-policy): Old: prompt hardcoded host-language defaults  New: 6 HOST_* variables are optional and empty by default, injected by host.env (#20 structural consensus) -->

> Please reply according to `${HOST_WORK_LANGUAGE}`. Code identifiers, file paths, error messages, and rule quotes may remain verbatim.

---

## 1. One-Paragraph Explanation

${PROBLEM_STATEMENT}

---

## 2. Concrete Example

Below is the real problem pattern in the current code. Lines marked `← problem` trigger the violation.

```${HOST_CODE_FENCE_LANG}
${PROBLEM_EXAMPLE_CODE}
```

**File**: `${PROBLEM_EXAMPLE_FILE_PATH}`

---

## 3. Why Human Design Is Needed

${WHY_NEEDS_DESIGN}

---

## 4. Needed Answer

Please answer the following before adding the `crnd:triage:resume-requested` label. Implement codex will read your latest comment **verbatim** as design input, so be specific.

- [ ] **Pattern choice**: ${DESIGN_QUESTION}
- [ ] **Schema impact**: if `${HOST_PROTO_POLICY}` is non-empty, answer according to that host schema/protocol policy. If new typed fields or schema/protocol changes are needed, list them according to host convention; if none, say so explicitly.
- [ ] **Backward compatibility**: how should existing durable state be handled? reserved identifier / compatibility alias / schema migration / acceptable reset.
- [ ] **Scope split**: keep one cluster or split into N PRs? If split, propose cluster ids.
- [ ] **Test surface**: beyond `verification_hints` in the cluster spec below, what behavior **must** be tested?
- [ ] **No-touch areas**: what should implement codex **not** touch?

---

## 5. Auto-Loop Behavior (mechanism note, **does not affect your answer**)

- The controller polls about once per hour when this issue is the only remaining work.
- The **first** new comment after issue creation triggers a PushNotification to the operator; later comments are not repeatedly pushed.
- Adding `crnd:triage:resume-requested` makes the controller prepend your latest comment as `## Design decision (from issue #${ISSUE_NUMBER})` to a new implement codex prompt and dispatch it. Implement runs in an isolated worktree, opens a PR back to `auto-refact-dev`, and the issue closes automatically when the PR opens.
- Closing without adding `crnd:triage:resume-requested` means the design was rejected and the cluster is permanently shelved; the controller records `design-rejected:closed` in GitHub / run artifacts.

---

## 6. Technical Reference (Folded)

<details>
<summary>Expand full cluster YAML / evidence / audit fix boundary</summary>

### Cluster spec (from `.refactor-loop/runs/audit-iter-${ITERATION}.md`)

${CLUSTER_YAML}

### Evidence

${CLUSTER_EVIDENCE}

### Initial Audit Proposal

${CLUSTER_FIX_BOUNDARY}

</details>

cc: @<maintainer-handle-from-$MAINTAINER_WHITELIST> (auto-loop operator)

---

## AI Content Identifier (Required)

All AI-generated external content (GitHub issue/PR comments, PR bodies, commit messages, `runs/*.md` artifacts, push notifications) **must end with the sentinel as a standalone line**:

    ⟦AI:AUTO-LOOP⟧

Do not modify the characters; do not place them in code comments, paths, or branch names. Missing sentinel = generation failure; the controller rejects the post.
