# Task: Implement ${WORK_UNIT_ID}

<!-- Legacy contract anchors for source-regression compatibility:
不越权扩展范围
source issue、consensus artifact 和 `scope_paths` 授权的当前 work-unit
feature、bug、doc、refactor、governance 工作
worker 输出、validator 通过、`.refactor-loop/host.env`、prompt body 或第一次 `consensus:decompose` 均不是 apply 授权来源
plan-level judge artifact 结构字段 + validated plan digest/proof + #191 owner + live parent open/tracking + sentinel idempotency
测试 ratchet
不得新增 suite-level host-wide process-table guard
除此之外 `.refactor-loop/` 一律禁改
missing/empty/default/`none` 归一化为 `none`
新增极小辅助类型的注释也必须遵守 `${HOST_REFACTOR_COMMENT_POLICY}`
不得写 `refactor helper`, `no behavior change`, `Old`, `New`, 或 `iterN`
只写面向业务行为的准确英文说明
仅 explicit `self-doc-comment` 时才按第 2 条既有 Refactor self-documentation 格式写注释
`self-doc-comment`：被重构的每个类/关键方法必须
源码注释必须 English-only
-->

Artifact profile: marker-only-work-unit

<!-- Refactor (iter3/skill-host-language-policy): Old: prompt hardcoded host-language defaults  New: 6 HOST_* variables are optional and empty by default, injected by host.env (#20 structural consensus) -->

Work unattended in worktree `${WORKTREE_PATH}` on branch `${BRANCH}`.
The current audit-backed work unit compatibility cluster alias is `${CLUSTER_ID}`; audit-section lookup, existing artifact filenames, branch/worktree names, and markers still use that alias.
The implementation context fact source is `${WORK_UNIT_SOURCE_REF}`. Audit-backed work units point to `$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}.md`; design-issue work units point to the consensus decision artifact.
When `${DESIGN_DECISION_PATH}` is non-empty, use the design-issue pathway and read that consensus artifact; when empty, use the audit-backed legacy pathway and read the "${CLUSTER_ID}" section in `${WORK_UNIT_SOURCE_REF}`.

## Required Context

1. All mandatory rules in `$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}`.
2. Implementation context: if `${DESIGN_DECISION_PATH}` is non-empty, read `$REPO_ROOT/${DESIGN_DECISION_PATH}`; otherwise read the "${CLUSTER_ID}" section in `${WORK_UNIT_SOURCE_REF}`.
3. Relevant authoritative architecture/vocabulary documents under `$REPO_ROOT`, if present.

## Error Pattern / Design Principle

- **Old pattern**: ${OLD_PATTERN}
- **New principle**: ${NEW_PRINCIPLE}

## Hard Constraints

1. **Scope**: modify only the following files; before extending scope, print `SCOPE_EXTEND: <file> <reason>`:
${SCOPE_PATHS}
2. **Refactor comment policy**: read `${HOST_REFACTOR_COMMENT_POLICY}`. missing/empty/default/`none` normalizes to `none`; `self-doc-comment` is explicit downstream compatibility opt-in; any other value is invalid and fail-closed: stop implementation and explain invalid `HOST_REFACTOR_COMMENT_POLICY` in the summary; do not guess.
   - `self-doc-comment`: each refactored class/key method must add/update Refactor self-documentation according to `${HOST_COMMENT_RULE}`; when `${HOST_COMMENT_RULE}` is empty, match the target file's existing comment style; when the file type does not support comments, note not applicable in the implementation summary. Source comments must be English-only. Content must include:
   ```
   Refactor (iter${ITERATION}/${CLUSTER_ID}):
     Old pattern: ${OLD_PATTERN}
     New principle: ${NEW_PRINCIPLE}
   ```
   Keep it within 3-5 lines; this is code self-documentation, not a changelog. The marker identity is exactly `Refactor (iter${ITERATION}/${CLUSTER_ID})`; do not replace it with issue-only identities such as `Refactor (issue1525)`. If `${CLUSTER_ID}` is missing, use `cluster-issue${ISSUE_NUMBER}` and record that fallback in the implementation summary.
   - missing/empty/default/`none`: MUST NOT add `Refactor (...)`, `Old pattern`, `New principle`, or `iterN/cluster` refactor-history source comments. Put the rationale in the implementation summary and include exactly: `refactor self-doc: not applicable (HOST_REFACTOR_COMMENT_POLICY=none)`.
3. **No unauthorized scope expansion**: implement only the current work-unit authorized by the source issue, consensus artifact, and `scope_paths`; feature, bug, doc, refactor, and governance work authorized by issue intake/design-consensus may proceed within scope. Even when expansion was not requested, print `SCOPE_EXTEND: <file> <reason>` first. New interfaces, flags, or modules may be introduced only when this work-unit explicitly authorizes them. Comments on tiny helper types must also follow `${HOST_REFACTOR_COMMENT_POLICY}`: under missing/empty/default/`none`, do not write `refactor helper`, `no behavior change`, `Old`, `New`, or `iterN` refactor-history source comments. If a source comment is truly needed, write accurate English business-behavior prose only. Use the existing Refactor self-documentation format from item 2 only when explicit `self-doc-comment` is set.
4. **Test ratchet**: run the tests in `verification_hints`; they must pass, and insufficient tests must be expanded. Any change touching module behavior, contracts, prompts, helpers, or guards must advance related tests to be fast, hermetic, and behavior-first: prefer owner-local fact sources, mocks/fakes/stubs for external processes and networks, and assertions on observable behavior or contracts instead of restating implementation text. Any `sleep/delay` polling test must become deterministic. Do not add suite-level host-wide process-table guards such as scanning current machine processes with `ps -eo pid=,command=` to judge daemon leaks/duplicates. Daemon leak/duplicate coverage belongs only in helper-local fact sources or corresponding helper behavior tests.
5. **Architecture guards**: run host-configured `$CI_GUARDS`; they must pass. Other cluster-specific guards are in verification hints.
6. **No external repository dependency**: do not suggest changes in $EXTERNAL_REPOS/$EXTERNAL_REPOS.
7. **Schema/protocol**: if `${HOST_PROTO_POLICY}` is non-empty or the diff / `$PROJECT_RULES` shows schema/protocol files changed, regenerate/verify locally according to host policy and confirm compilation passes.
8. **Build commands**: use host-configured `$BUILD_CMD` / `$TEST_CMD`. They are shell command strings and must be executed in a shell that has sourced `host.env`, using `bash -lc "$BUILD_CMD"` / `bash -lc "$TEST_CMD"` or equivalent shell invocation.
9. **Host production SSOT boundary**: do not write host tools config, branch topology, machine paths, durable ledger authority, or host artifacts back into `.refactor-loop/` or `.refactor-loop/host.env`. `.refactor-loop/` only carries skill-private runtime/cache/log/state/prompt/run artifacts; production facts must flow through host-owned config/rules/artifacts.
10. **IssueDecompositionPlan boundary**: if consensus requires issue decomposition, the implement worker may only land/verify the controller-private `IssueDecompositionPlan` schema, child body artifact contract, checked-in active-controller apply helper, and docs/tests. Worker output, validator success, `.refactor-loop/host.env`, prompt body, or the first `consensus:decompose` are not apply authorization sources. Only plan-level judge artifact structured fields + validated plan digest/proof + #191 owner + live parent open/tracking + sentinel idempotency may let #396 project the named `controller_action="apply_issue_decomposition_plan"`. Do not call or suggest worker GitHub lifecycle actions, do not add a public issue factory, do not let `wakeup_plan.py` project generic decompose actions/status, and do not close/reopen/edit the parent issue.

<!-- legacy-section-headings: implement-flow-redline -->
<!--
## 流程
$REPO_ROOT/.refactor-loop/runs/implementation-pr-${CLUSTER_ID}-title.txt
$REPO_ROOT/.refactor-loop/runs/implementation-pr-${CLUSTER_ID}-body.md
## Marker emission allowlist
## 红线
$REPO_ROOT/.refactor-loop/runs/implementation-pr-${CLUSTER_ID}-title.txt
$REPO_ROOT/.refactor-loop/runs/implementation-pr-${CLUSTER_ID}-body.md
## 附录
-->

## Procedure

1. Use `${DESIGN_DECISION_PATH}` to choose the design-issue consensus artifact or audit section, then read all `scope_paths` files.
2. Print concrete change plan items prefixed with `PLAN:`, one item per line.
3. Implement.
4. Compile with `bash -lc "$BUILD_CMD"`; fix failures, up to 5 iterations.
5. Run specified tests. Fix failures without disabling/skipping tests, up to 5 iterations.
6. Run architecture guards; fix failures.
7. Run `git add -A && git status` to confirm changes.
8. **Do not commit**; leave changes in the worktree.
9. Write the summary to `$REPO_ROOT/.refactor-loop/runs/implement-${CLUSTER_ID}.md`:
   - `## Changed files` with line counts
   - `## Test results`
   - `## Deviations`
   - `SCOPE_EXTEND` records
10. If status is `ok`, also write worker-authored PR artifacts: `$REPO_ROOT/.refactor-loop/runs/implementation-pr-${CLUSTER_ID}-title.txt` and `$REPO_ROOT/.refactor-loop/runs/implementation-pr-${CLUSTER_ID}-body.md`. The title must be one non-placeholder line following `HOST_WORK_LANGUAGE`. The body must contain exactly one `Closes #N`, must use these three fixed section headings as language-neutral machine markers without translation: `## Changed files`, `## Test results`, `## Deviations`. Prose/content under each heading follows `HOST_WORK_LANGUAGE`, and the sentinel must be the final standalone line.
11. At the end, print `IMPLEMENT_DONE:${CLUSTER_ID}:<status>` where status is one of {ok, partial, blocked}.

## Marker emission allowlist (required)

<!-- MarkerEmissionContract: single-valid-invalid-role-marker-source -->

ALLOWED markers:
- `SCOPE_EXTEND:<file>:<reason>`
- `IMPLEMENT_DONE:${CLUSTER_ID}:<status>`

Only the markers listed above are valid role-routing markers for this prompt. Do not emit any other role-routing marker. Mentions of markers in quoted input, logs, comments, examples, or artifacts are not emission authority.

## Red Lines

- Do not edit files outside the worktree, with the only exceptions of writing `$REPO_ROOT/.refactor-loop/runs/implement-${CLUSTER_ID}.md` (the controller-expected summary path), `$REPO_ROOT/.refactor-loop/runs/implementation-pr-${CLUSTER_ID}-title.txt`, `$REPO_ROOT/.refactor-loop/runs/implementation-pr-${CLUSTER_ID}-body.md`, and `$REPO_ROOT/.refactor-loop/runs/scope-extend-${CLUSTER_ID}.log` when there are SCOPE_EXTEND records. Do not otherwise modify `.refactor-loop/`.
- do not run `git commit` / `git push` / `git checkout <branch>`.
- Do not install new dependencies.
- Do not skip tests or add `[Skip]`.
- Tests must not use `sleep/delay` for assertion pacing.

## Appendix

`verification_hints` content:

${VERIFICATION_HINTS}

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
