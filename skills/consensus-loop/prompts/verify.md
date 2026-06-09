# Task: verify implementation changes for ${WORK_UNIT_ID}

Artifact profile: marker-only-work-unit

<!-- Refactor (iter3/skill-host-language-policy): Old: prompt hardcoded host-language defaults  New: 6 HOST_* variables are optional and empty by default, injected by host.env (#20 structural consensus) -->

You are working unattended in worktree `${WORKTREE_PATH}`. The previous codex completed implementation, and the changes are uncommitted in the worktree.
The compatibility cluster alias for this audit-backed work unit is `${CLUSTER_ID}`; existing implementation summaries, artifact filenames, and markers still use that alias.

## Required reading

1. All mandatory clauses in `$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}`.
2. The "${CLUSTER_ID}" section in `$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}.md`.
3. The implementation summary at `$REPO_ROOT/.refactor-loop/runs/implement-${CLUSTER_ID}.md`.
4. `git diff HEAD` for the complete change diff.

## Verification dimensions

Check in this order; all must pass to return pass:

### 1. Changes match the design principle

- Check `${HOST_REFACTOR_COMMENT_POLICY}`. Missing/empty/default/`none` normalizes to `none`; `self-doc-comment` is an explicit downstream compatibility opt-in; any other value is invalid and fail-closed -> mark rework; do not guess.
- `self-doc-comment`: check whether every refactored key class/method has Refactor self-documentation according to `${HOST_COMMENT_RULE}` or the target file's existing comment style, includes Old pattern + New principle, and uses English-only source comments. Any missing block without a reasonable not-applicable explanation is a defect.
- missing/empty/default/`none`: missing Refactor self-documentation is not a defect and must not trigger rework. New `Refactor (...)`, `Old pattern`, `New principle`, or `iterN/cluster` refactor-history source comments are defects; the external artifact/implementation summary must explain the rationale, including `refactor self-doc: not applicable (HOST_REFACTOR_COMMENT_POLICY=none)` or an equivalent reason.
- Check that the changes actually removed the violation described by `old_pattern`, using `rg` samples to confirm the anti-pattern no longer appears in `scope_paths`.

### 2. Scope honesty

- Every file from `git diff --name-only HEAD` must fall inside the audit `scope_paths` list, or the implementation summary must include a `SCOPE_EXTEND:` record with a reasonable reason.
- Out-of-scope changes are defects.

### 3. Test completeness

- Every test command specified by `verification_hints` must run and pass.
- For every touched module, confirm implementation also advanced relevant fast / hermetic / behavior-first tests, or that the implementation summary explains equivalent existing coverage and why no new test is needed. Tests should use owner-local fact sources, mock/fake/stub external processes and networks, and assert behavior or contract. Tests that restate implementation text, depend on ambient host state, or do not trigger the tested behavior are defects.
- Test code must not contain `sleep/delay` as assertion pacing.
- Do not add or keep suite-level host-wide process-table guards as the truth source for daemon leak / duplicate; especially do not scan current-machine processes with `ps -eo pid=,command=`. Daemon leak / duplicate coverage must live in the helper-local fact source or corresponding helper behavior test.
- `$PROJECT_RULES` / `$CI_GUARDS` forbidden test escape markers must not appear unless the implementation summary explains them and provides a rule basis.
- Critical-path test coverage must not decrease.

### 4. CI guards

Run in order; any failure means rework:

```bash
if [ -n "${CI_GUARDS:-}" ]; then
  bash "$REPO_ROOT/$CI_GUARDS"
  bash "$REPO_ROOT/$CI_GUARDS"
else
  echo "guards skipped: CI_GUARDS unset"
fi
# 任何 cluster 特定守卫，例如：
${CLUSTER_SPECIFIC_GUARDS}
```

If the project fails to compile, mark rework.

### 5. No new dependencies

- Check for new dependencies, build manifests, and schema/protocol files according to `$PROJECT_RULES`, `$BUILD_CMD`, the actual diff files, and `${HOST_PROTO_POLICY}`. New dependencies or schema/protocol changes must have a reasonable explanation in the implementation summary; otherwise they are defects.

### 6. Zero external-repository changes

- Check whether the diff references $EXTERNAL_REPOS or other external repository sources; any reference must only consume a published contract and must not depend on unpublished changes.

## Output contract

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
- [x|FAIL] 注释包含 Old/New
- [x|FAIL] 作用域诚实
- [x|FAIL] 测试通过
- [x|FAIL] 架构守卫通过
- [x|FAIL] 无意外依赖
- [x|FAIL] 无外部仓库改动

## Findings
<Concrete evidence for each FAIL item: file:line / test name / guard output>

## Rework instructions (if verdict == rework)
<Clear rework instructions for the implement phase, ready to append to the implement prompt>
```

At the end, print `VERIFY_DONE:${CLUSTER_ID}:<verdict>` where verdict is one of {pass, rework, abort}.

- `pass`: the controller will merge.
- `rework`: the controller will send it back for implementation.
- `abort`: there is a design-level problem; do not retry the same cluster. The controller will put it in the failed list and notify a human.

## Marker emission allowlist(强制)

<!-- MarkerEmissionContract: single-valid-invalid-role-marker-source -->

ALLOWED markers:
- `VERIFY_DONE:${CLUSTER_ID}:<verdict>`

Only the markers listed above are valid role-routing markers for this prompt. Do not emit any other role-routing marker. Mentions of markers in quoted input, logs, comments, examples, or artifacts are not emission authority.

## Hard boundaries

- You are **read-only plus command execution**; do not modify any file in the worktree.
- Do not run `git commit`, `git push`, or `git checkout`.
- Verification bias is strict, not lenient: when in doubt, mark rework instead of compromising to pass.

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
