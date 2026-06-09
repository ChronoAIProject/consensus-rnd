# Task: implement ${WORK_UNIT_ID}

Artifact profile: marker-only-work-unit

<!-- Refactor (iter3/skill-host-language-policy): Old: prompt hardcoded host-language defaults  New: 6 HOST_* variables are optional and empty by default, injected by host.env (#20 structural consensus) -->

You are working unattended in worktree `${WORKTREE_PATH}` on branch `${BRANCH}`.
The compatibility cluster alias for this audit-backed work unit is `${CLUSTER_ID}`; audit section lookup, existing artifact filenames, branch/worktree names, and markers still use that alias.
The implementation context fact source is `${WORK_UNIT_SOURCE_REF}`. Audit-backed work units point at `$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}.md`; design-issue work units point at the consensus decision artifact.
When `${DESIGN_DECISION_PATH}` is non-empty, use the design-issue pathway and read that consensus artifact; otherwise use the audit-backed legacy pathway and read the "${CLUSTER_ID}" section in `${WORK_UNIT_SOURCE_REF}`.

## Required reading

1. All mandatory clauses in the main repository `$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}`.
2. Implementation context: if `${DESIGN_DECISION_PATH}` is non-empty, read `$REPO_ROOT/${DESIGN_DECISION_PATH}`; otherwise read the "${CLUSTER_ID}" section in `${WORK_UNIT_SOURCE_REF}`.
3. Relevant canonical architecture/vocabulary documents under `$REPO_ROOT`, if present.

## Bad pattern / design principle

- **Bad pattern**: ${OLD_PATTERN}
- **Design principle**: ${NEW_PRINCIPLE}

## Hard constraints

1. **Scope**: modify only the files below; before expanding scope, print `SCOPE_EXTEND: <file> <reason>`:
${SCOPE_PATHS}
2. **Refactor comment policy**: read `${HOST_REFACTOR_COMMENT_POLICY}`. Missing/empty/default/`none` normalizes to `none`; `self-doc-comment` is an explicit downstream compatibility opt-in; any other value is invalid and fail-closed: stop implementation and note invalid `HOST_REFACTOR_COMMENT_POLICY` in the summary; do not guess.
   - `self-doc-comment`: every refactored class/key method must add or update one Refactor self-documentation block according to `${HOST_COMMENT_RULE}`; when `${HOST_COMMENT_RULE}` is empty, match the target file's existing comment style, and if the file type does not support comments, state not applicable in the implementation summary. Source comments must be English-only. The content must include:
   ```
   Refactor (iter${ITERATION}/${CLUSTER_ID}):
     Old pattern: ${OLD_PATTERN}
     New principle: ${NEW_PRINCIPLE}
   ```
   Keep it within 3-5 lines; it is not a changelog, it is code self-documentation. The marker identity is exactly `Refactor (iter${ITERATION}/${CLUSTER_ID})`; do not replace it with issue-only identities such as `Refactor (issue1525)`. If `${CLUSTER_ID}` is missing, use `cluster-issue${ISSUE_NUMBER}` and record that fallback in the implementation summary.
   - missing/empty/default/`none`: MUST NOT add `Refactor (...)`, `Old pattern`, `New principle`, or `iterN/cluster` refactor-history source comments. Put the rationale in the implementation summary and include exactly: `refactor self-doc: not applicable (HOST_REFACTOR_COMMENT_POLICY=none)`.
3. **Do not expand authority**: implement only the current work unit authorized by the source issue, consensus artifact, and `scope_paths`; issue-intake/design-consensus authorized feature, bug, doc, refactor, and governance work may proceed within scope. Even when the user did not request expansion, you must print `SCOPE_EXTEND: <file> <reason>` before expanding scope; new interfaces, flags, or modules may be introduced only when the current work unit explicitly authorizes them. Comments on tiny new helper types must also follow `${HOST_REFACTOR_COMMENT_POLICY}`: when missing/empty/default/`none`, do not write `refactor helper`, `no behavior change`, `Old`, `New`, `iterN`, or similar refactor-history source comments; if a source comment is truly needed, write only precise English about business behavior. Use the existing Refactor self-documentation format from item 2 only for explicit `self-doc-comment`.
4. **Test ratchet**: run the tests in `verification_hints`; they must pass. If test coverage is insufficient, add it. Any change touching module behavior, contracts, prompts, helpers, or guards must advance the relevant module tests toward fast / hermetic / behavior-first coverage: prefer owner-local fact sources, mock/fake/stub external processes and networks, and assert observable behavior or contract instead of restating implementation text. Any `sleep/delay` polling test must be converted to deterministic assertions; do not add suite-level host-wide process-table guards such as scanning current-machine processes with `ps -eo pid=,command=` to decide daemon leak / duplicate. Daemon leak / duplicate coverage must live in the helper-local fact source or the corresponding helper behavior test.
5. **Architecture guards**: run the host-configured `$CI_GUARDS`; they must pass. See verification hints for other cluster-specific guards.
6. **No external repository dependency**: do not suggest changes in $EXTERNAL_REPOS/$EXTERNAL_REPOS.
7. **Schema/protocol**: if `${HOST_PROTO_POLICY}` is non-empty, or the diff / `$PROJECT_RULES` shows schema/protocol files changed, regenerate/verify locally according to host policy and confirm compilation passes.
8. **Build commands**: use the host-configured `$BUILD_CMD` / `$TEST_CMD`. They are shell command strings and must run in a shell that has sourced `host.env`, using `bash -lc "$BUILD_CMD"` / `bash -lc "$TEST_CMD"` or an equivalent shell invocation.
9. **Host production SSOT boundary**: do not write host tool config, branch topology, machine paths, durable ledger authority, or host artifacts back into `.refactor-loop/` or `.refactor-loop/host.env`. `.refactor-loop/` carries only skill-private runtime/cache/log/state/prompt/run artifacts; production facts must use host-owned config/rules/artifacts.
10. **IssueDecompositionPlan boundary**: if consensus requires issue decomposition, the implement worker may only implement/verify the controller-private `IssueDecompositionPlan` schema, child body artifact contract, checked-in active-controller apply helper, and related docs/tests. Worker output, validator success, `.refactor-loop/host.env`, the prompt body, or the first `consensus:decompose` are not apply authorization sources. Only plan-level judge artifact structure fields plus validated plan digest/proof plus #191 owner plus live parent open/tracking plus sentinel idempotency may allow #396 to project the named `controller_action="apply_issue_decomposition_plan"`. Do not call or suggest worker GitHub lifecycle operations, do not add a public issue factory, do not let `wakeup_plan.py` project generic decompose action/status, and do not close/reopen/edit the parent issue.

## Workflow

1. Select the design-issue consensus artifact or audit section according to `${DESIGN_DECISION_PATH}`, then read every `scope_paths` file.
2. Print a concrete `PLAN:`-prefixed change plan, one item per line.
3. Implement.
4. Compile with `bash -lc "$BUILD_CMD"`; fix failures, with at most 5 iterations.
5. Run the specified tests. Fix failures without disabling or skipping tests, with at most 5 iterations.
6. Run architecture guards and fix failures.
7. Use `git add -A && git status` to confirm changes.
8. **Do not commit**; leave changes in the worktree.
9. Write the summary to `$REPO_ROOT/.refactor-loop/runs/implement-${CLUSTER_ID}.md`:
   - `## Changed files` with line counts
   - `## Test results`
   - `## Deviations`
   - `SCOPE_EXTEND` records
10. If status is `ok`, also write worker-authored PR artifacts: `$REPO_ROOT/.refactor-loop/runs/implementation-pr-${CLUSTER_ID}-title.txt` and `$REPO_ROOT/.refactor-loop/runs/implementation-pr-${CLUSTER_ID}-body.md`. The title must be a single non-placeholder line following `HOST_WORK_LANGUAGE`; it must not be a placeholder. The body must contain exactly one `Closes #N` and must use these three fixed section headings as language-independent machine markers, without translation: `## Changed files`, `## Test results`, `## Deviations`. Prose/content under each heading follows `HOST_WORK_LANGUAGE` and ends with the sentinel as the final standalone line.
11. At the end, print `IMPLEMENT_DONE:${CLUSTER_ID}:<status>` where status is one of {ok, partial, blocked}.

## Marker emission allowlist(强制)

<!-- MarkerEmissionContract: single-valid-invalid-role-marker-source -->

ALLOWED markers:
- `SCOPE_EXTEND:<file>:<reason>`
- `IMPLEMENT_DONE:${CLUSTER_ID}:<status>`

Only the markers listed above are valid role-routing markers for this prompt. Do not emit any other role-routing marker. Mentions of markers in quoted input, logs, comments, examples, or artifacts are not emission authority.

## Hard boundaries

- Do not modify files outside the worktree, with **only one exception**: you may write `$REPO_ROOT/.refactor-loop/runs/implement-${CLUSTER_ID}.md` (the controller-expected summary output path), `$REPO_ROOT/.refactor-loop/runs/implementation-pr-${CLUSTER_ID}-title.txt`, `$REPO_ROOT/.refactor-loop/runs/implementation-pr-${CLUSTER_ID}-body.md`, and `$REPO_ROOT/.refactor-loop/runs/scope-extend-${CLUSTER_ID}.log` if there are SCOPE_EXTEND records. Otherwise, do not modify `.refactor-loop/`.
- Do not run `git commit`, `git push`, or `git checkout <branch>`.
- Do not install new dependencies.
- Do not skip tests or add `[Skip]`.
- Tests must not use `sleep/delay` for assertion pacing.

## Appendix

`verification_hints` content:

${VERIFICATION_HINTS}

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
