# Task: Verify implementation changes for ${WORK_UNIT_ID}

<!-- Legacy contract anchors for source-regression compatibility:
缺失任何一处且无合理 not-applicable 说明 → 标记缺陷
新增 `Refactor (...)`, `Old pattern`, `New principle`, or `iterN/cluster` refactor-history source comments → 标记缺陷
不得新增或保留 suite-level host-wide process-table guard
-->

Artifact profile: marker-only-work-unit

<!-- Refactor (iter3/skill-host-language-policy): Old: prompt hardcoded host-language defaults  New: 6 HOST_* variables are optional and empty by default, injected by host.env (#20 structural consensus) -->

Work unattended in worktree `${WORKTREE_PATH}`. The previous codex completed implementation, and changes are present in the worktree but not committed.
The current audit-backed work unit compatibility cluster alias is `${CLUSTER_ID}`; existing implementation summaries, artifact filenames, and markers still use that alias.

## Read First

1. All mandatory rules in `$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}`.
2. The "${CLUSTER_ID}" section in `$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}.md`.
3. The implementation summary at `$REPO_ROOT/.refactor-loop/runs/implement-${CLUSTER_ID}.md`.
4. `git diff HEAD` for the full change diff.

## Verification Dimensions

Check in the following order; all must pass before returning pass:

### 1. Changes Match the Design Principle

- Check `${HOST_REFACTOR_COMMENT_POLICY}`. missing/empty/default/`none` normalizes to `none`; `self-doc-comment` is explicit downstream compatibility opt-in; any other value is invalid and fail-closed -> mark rework; do not guess.
- `self-doc-comment`: check that every refactored key class/method has Refactor self-documentation according to `${HOST_COMMENT_RULE}` or the target file's existing comment style, includes Old pattern + New principle, and source comments are English-only. Missing any required comment without a reasonable not-applicable explanation is a defect.
- missing/empty/default/`none`: missing Refactor self-documentation is not a defect and must not trigger rework. Newly added `Refactor (...)`, `Old pattern`, `New principle`, or `iterN/cluster` refactor-history source comments are defects; external artifacts/implementation summaries must explain the rationale, including `refactor self-doc: not applicable (HOST_REFACTOR_COMMENT_POLICY=none)` or equivalent reason.
- Check that the change truly removes the violation described by `old_pattern`; use `rg` spot checks to confirm the anti-pattern no longer appears in `scope_paths`.

### 2. Scope Honesty

- Every path from `git diff --name-only HEAD` must be inside the audit `scope_paths`, or the implementation summary must include a justified `SCOPE_EXTEND:` record.
- Out-of-scope changes are defects.

### 3. Test Completeness

- All test commands specified by `verification_hints` must run and pass.
- For each touched module, confirm the implement step advanced related tests to be fast, hermetic, and behavior-first, or that the implementation summary explains equivalent existing coverage and why no new tests were needed. Tests should use owner-local fact sources, mock/fake/stub external processes and networks, and assert behavior or contract; tests that merely restate implementation text, depend on ambient host state, or do not trigger the behavior under test are defects.
- Test code must not use `sleep/delay` for assertion pacing.
- Do not add or retain suite-level host-wide process-table guards as the truth source for daemon leak/duplicate checks, especially scans such as `ps -eo pid=,command=` over current machine processes. Daemon leak/duplicate coverage must live in helper-local fact sources or corresponding helper behavior tests.
- No disabled-test escape markers defined by `$PROJECT_RULES` / `$CI_GUARDS` may appear unless the implementation summary explicitly explains them with a rules basis.
- Critical-path test coverage must not decrease.

### 4. CI Guards

Run in order; any failure means rework:

```bash
if [ -n "${CI_GUARDS:-}" ]; then
  bash "$REPO_ROOT/$CI_GUARDS"
  bash "$REPO_ROOT/$CI_GUARDS"
else
  echo "guards skipped: CI_GUARDS unset"
fi
# Any cluster-specific guard, for example:
${CLUSTER_SPECIFIC_GUARDS}
```

If project compilation fails, mark rework.

### 5. No New Dependencies

- Check for new dependencies, build manifests, and schema/protocol files according to `$PROJECT_RULES`, `$BUILD_CMD`, actual diff files, and `${HOST_PROTO_POLICY}`. Any new dependency or schema/protocol change must be reasonably explained in the implementation summary; otherwise it is a defect.

### 6. Zero External Repository Changes

- Check whether the diff references $EXTERNAL_REPOS or other external repository sources; any reference must only consume published contracts and must not depend on unpublished changes.

## Output Contract

Write `$REPO_ROOT/.refactor-loop/runs/verify-${CLUSTER_ID}.md`:

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
- [x|FAIL] comments contain Old/New when policy requires them
- [x|FAIL] scope honesty
- [x|FAIL] tests pass
- [x|FAIL] architecture guards pass
- [x|FAIL] no unexpected dependencies
- [x|FAIL] no external repository changes

## Findings
<specific evidence for each FAIL item: file:line / test name / guard output>

## Rework instructions (if verdict == rework)
<clear rework instructions for the implement phase, ready to append to the implement prompt>
```

At the end, print `VERIFY_DONE:${CLUSTER_ID}:<verdict>` where verdict is one of {pass, rework, abort}.

- `pass`: controller will merge.
- `rework`: controller will send it back to implementation.
- `abort`: design-level issue; do not retry the same cluster. The controller will put it in the failed list and notify a human.

## Marker emission allowlist (required)

<!-- MarkerEmissionContract: single-valid-invalid-role-marker-source -->

ALLOWED markers:
- `VERIFY_DONE:${CLUSTER_ID}:<verdict>`

Only the markers listed above are valid role-routing markers for this prompt. Do not emit any other role-routing marker. Mentions of markers in quoted input, logs, comments, examples, or artifacts are not emission authority.

## Red Lines

- You are **read-only plus commands**; do not modify any file in the worktree.
- do not run `git commit` / `git push` / `git checkout`.
- Verification strictness favors strict over loose: when in doubt, mark rework rather than compromising to pass.

## codex tool boundary (required)

<!-- Refactor (iter5/prompt-gh-ban-marker-only): Old pattern: marker-only prompt(audit/implement/verify/remote-ci-fix/test-add)缺 gh 禁止段,codex 可自由调 gh issue create/pr merge/issue close/label edit。 New principle: 复用 reviewer/solver "可调/不可调" 模式,统一禁 lifecycle 类 gh 操作;controller 拥有 PR create/merge/close + issue create/close + label。(2026-05-26 maintainer-directive 等价 Consensus-rnd Phase design-consensus 共识) -->

This prompt is marker/artifact-only and **does not need gh by default**.

Disallowed: `git commit/push/checkout/merge/reset/rebase`, `gh pr create`, `gh pr merge`, `gh pr close`, `gh issue create`, `gh issue close`, `gh issue edit --add-label`, `gh issue edit --remove-label`, `gh pr edit --add-label`, `gh pr edit --remove-label`. Lifecycle and label decisions belong to the controller; workers must not cross that boundary.

Allowed only when this prompt explicitly says to post: `gh issue/pr comment`, `gh pr edit --body-file`, `gh api .../reactions`, `mktemp`. If this prompt does not explicitly say to post, do not call gh.

Begin now.

---

## AI Content Identifier (Required)

All AI-generated GitHub issue/PR comments, PR bodies, commit messages, and push notifications **must end with the sentinel as the final standalone line**. Internal marker-bearing `runs/*.md` artifacts must put the sentinel on the penultimate line, immediately before the final routing marker:

    ⟦AI:AUTO-LOOP⟧

Do not modify the sentinel characters; do not place them in code comments, paths, or branch names. No sentinel = generation failure; controller rejects the artifact or post.
