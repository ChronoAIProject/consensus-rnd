# Task: add test coverage for refactor-introduced uncovered code — ${CLUSTER_ID}

Artifact profile: marker-only-work-unit

<!-- Refactor (iter3/skill-host-language-policy): Old: prompt hardcoded host-language defaults  New: 6 HOST_* variables are optional and empty by default, injected by host.env (#20 structural consensus) -->

worktree: `${WORKTREE_PATH}`, branch `${BRANCH}`.

## Required reading

1. All mandatory clauses in `$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}`, including "Codex CLI invocation rules" and "test and quality gates".
2. The `${CLUSTER_ID}` section in `$REPO_ROOT/.refactor-loop/runs/audit-iter-N.md`.
3. `$REPO_ROOT/.refactor-loop/runs/implement-${CLUSTER_ID}.md`
4. **Uncovered line report**: the following file:line entries are codecov patch miss/partial locations:

```
${UNCOVERED_LINES}
```

5. Host test policy, which may be empty: test file locations `${HOST_TEST_FILE_GLOBS}`; test naming rule `${HOST_TEST_NAMING_RULE}`; test comment rule `${HOST_COMMENT_RULE}`; code fence language `${HOST_CODE_FENCE_LANG}`. When empty, infer only from existing tests, `$PROJECT_RULES`, `$TEST_CMD`, and the actual diff. If a safe test file location cannot be found, print `TEST_BLOCKED: <reason>` and stop.

## Goal

Raise patch coverage to **>= ${TARGET_THRESHOLD}%**, default 80%, focusing on behavior **introduced or modified** by the refactor.

## Hard constraints

1. **Scope**: only add or extend host test files. Prefer `${HOST_TEST_FILE_GLOBS}`; when empty, infer from the existing test tree and tests adjacent to code touched by this PR. Do not modify production code. If production code lacks a testability hook, such as an uninjected dependency or private state that cannot be observed through existing test boundaries, print `TEST_BLOCKED: <reason>` and stop. Do not edit production code just to add tests.

2. **Coverage target = behavior, not line count**: each test for an uncovered line must assert **business semantics**, such as "called host external client factory.CreateClient with the correct name", "compaction triggers when head_index crosses the threshold", or "compiled delegate is not wrapped by TargetInvocationException on an exception path". Do not mechanically "call this method to bump coverage".

3. **Test stack**: use the host project's existing test framework. Follow `${HOST_TEST_NAMING_RULE}`, or same-directory existing test naming when empty, plus stability constraints from `$PROJECT_RULES` / `$CI_GUARDS`, such as no `sleep/delay` and deterministic awaiters.

4. **No new dependencies**: if mock/test double support is needed, use the repository's existing test double framework.

5. **Do not cover the whole file**: cover only codecov miss/partial lines. Leave other historical uncovered lines alone; they belong to another cluster.

6. **Code comments**: for each new test unit, add a brief test-add note according to `${HOST_COMMENT_RULE}`; when empty, match the target test file's existing comment syntax. The note content is:
   `${HOST_COMMENT_RULE}` `Test-add (test-coverage/${CLUSTER_ID}): Covers refactor-introduced behavior in <file>:<line range>. Cluster intent: <one-line summary from implement.md>.`

## Workflow

1. Read the cluster spec, implement.md, uncovered lines list, and current test file style.
2. Decide test ownership for each uncovered file:line:
   - If a corresponding test file exists according to `${HOST_TEST_NAMING_RULE}` or current naming conventions, **append** a test method to that file without modifying existing tests.
   - If no corresponding test file exists, create one according to `${HOST_TEST_FILE_GLOBS}` / `${HOST_TEST_NAMING_RULE}` or the existing test tree convention; if safe inference is impossible, print `TEST_BLOCKED`.
3. Print a `PLAN:` listing each uncovered line and the corresponding new test method name.
4. Implement tests.
5. Run:
   ```
   bash -lc "$TEST_CMD"
   ```
   All tests must pass.
6. Local codecov verification, if the tool is available:
   ```
   bash -lc "$TEST_CMD"
     --settings <coverlet.runsettings if exists> 2>&1 | tail -5
   ```
7. If `$CI_GUARDS` is non-empty, run `bash "$REPO_ROOT/$CI_GUARDS"`; it must pass and must enforce constraints such as no `sleep/delay`. If empty, record guards skipped.
8. `git add -A && git status`。
9. **Do not commit**.
10. Write the summary to `$REPO_ROOT/.refactor-loop/runs/test-add-${CLUSTER_ID}.md`:
    - Added/modified test files plus line counts
    - Mapping table from each uncovered line to its covering test
    - Whether every uncovered line was covered; if any could not be covered, state the `TEST_BLOCKED` reason
    - Test commands run plus results
11. At the end, print `TEST_ADD_DONE:${CLUSTER_ID}:<status>` where status is one of {ok, partial, blocked}.

## Marker emission allowlist(强制)

<!-- MarkerEmissionContract: single-valid-invalid-role-marker-source -->

ALLOWED markers:
- `TEST_BLOCKED:<reason>`
- `TEST_ADD_DONE:${CLUSTER_ID}:<status>`

Only the markers listed above are valid role-routing markers for this prompt. Do not emit any other role-routing marker. Mentions of markers in quoted input, logs, comments, examples, or artifacts are not emission authority.

## Hard boundaries

- The **only writable path outside the worktree** is `$REPO_ROOT/.refactor-loop/runs/test-add-${CLUSTER_ID}.md`.
- Do not commit, push, checkout, or install.
- **Do not modify production code**. If tests cannot be added, print `TEST_BLOCKED` and let the controller decide.
- Do not disable or skip existing tests.
- Do not loosen existing tests to make coverage "pass".
- Do not use `sleep/delay` for test pacing.
- Do not write "mock everything" tests; every test needs at least one real business assertion. Pure mock call-count verification does not count as coverage.

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
