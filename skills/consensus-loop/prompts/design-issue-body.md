# ${PROBLEM_TITLE}

<!-- Refactor (iter3/skill-host-language-policy): Old: prompt hardcoded host-language defaults  New: 6 HOST_* variables are optional and empty by default, injected by host.env (#20 structural consensus) -->

> Please reply according to `${HOST_WORK_LANGUAGE}`; do not add a mandatory parallel English section. Code identifiers, file paths, error messages, and rule quotes may remain verbatim.

---

## 1. One-paragraph summary

${PROBLEM_STATEMENT}

---

## 2. Concrete example

The following snippet shows the current code pattern. The line marked `← problem` is the violating location.

```${HOST_CODE_FENCE_LANG}
${PROBLEM_EXAMPLE_CODE}
```

**File**: `${PROBLEM_EXAMPLE_FILE_PATH}`

---

## 3. Why this needs human design

${WHY_NEEDS_DESIGN}

---

## 4. Answer needed

Before adding the `crnd:triage:resume-requested` label, please answer the questions below. The implement codex will read your latest comment **verbatim** as design input, so keep it specific.

- [ ] **Pattern choice**: ${DESIGN_QUESTION}
- [ ] **Schema impact**: if `${HOST_PROTO_POLICY}` is non-empty, answer according to that host schema/protocol policy. If a typed field or schema/protocol change is needed, list it according to the host convention; otherwise state that there is no change.
- [ ] **Backward compatibility**: how should existing durable state be handled? (reserved identifier / compatibility alias / schema migration / acceptable reset)
- [ ] **Scope split**: keep this as one cluster or split it into N PRs? If split, propose cluster ids.
- [ ] **Test surface**: beyond the `verification_hints` in the cluster spec below, which behaviors **must** be tested?
- [ ] **No-go areas**: what should the implement codex **not** touch?

---

## 5. Auto-loop behavior (mechanism note, **does not affect your answer**)

- When this issue is the only remaining work, the controller polls it roughly once per hour.
- The **first** new comment after the issue opens triggers a PushNotification to the operator; later comments do not send repeated notifications.
- Adding the `crnd:triage:resume-requested` label makes the controller prepend your latest comment as `## Design decision (from issue #${ISSUE_NUMBER})` to a new implement codex prompt. Implementation runs in an isolated worktree, opens a PR back to `auto-refact-dev`, and the PR opening automatically closes this issue.
- Closing without adding `crnd:triage:resume-requested` is treated as "design rejected; cluster permanently parked"; the controller records `design-rejected:closed` in GitHub and the run artifact.

---

## 6. Technical reference (collapsed)

<details>
<summary>Expand full cluster YAML / evidence / audit fix boundary</summary>

### Cluster spec (from `.refactor-loop/runs/audit-iter-${ITERATION}.md`)

${CLUSTER_YAML}

### Evidence

${CLUSTER_EVIDENCE}

### Initial audit proposal

${CLUSTER_FIX_BOUNDARY}

</details>

cc: @<maintainer-handle-from-$MAINTAINER_WHITELIST> (auto-loop operator)

---

## AI content identifier (mandatory)

Every AI-authored external artifact (GitHub issue/PR comment, PR body, commit message, `runs/*.md` artifact, or push notification) **must end with the sentinel as the final standalone line**:

    ⟦AI:AUTO-LOOP⟧

Do not modify the sentinel characters; do not place them in code comments, paths, or branch names. No sentinel means generation failure and the controller rejects the post.
