# Verify `${WORK_UNIT_ID}`
<!-- Refactor (iter5/prompts-compression): Old pattern: broad verification narrative. New principle: compact marker-only verify contract with pass/rework/block exits. -->

Work in `${WORKTREE_PATH}`. Implementation changes are uncommitted. Cluster alias `${CLUSTER_ID}` names the artifacts.

Facts are injected from `source .refactor-loop/host.env`; preserve `${HOST_*}` placeholders.

## Required Reading

1. `$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}`.
2. `$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}.md` section `${CLUSTER_ID}`.
3. `$REPO_ROOT/.refactor-loop/runs/implement-${CLUSTER_ID}.md`.
4. `git diff HEAD`.

## Checks

All must pass for `pass`:

1. Design principle: key refactored types/methods have `${HOST_COMMENT_RULE}` or local-style Refactor self-documentation with Old pattern + New principle; `rg` samples show old anti-pattern removed in scope.
2. Scope honesty: `git diff --name-only HEAD` is within audit `scope_paths` or implement summary has justified `SCOPE_EXTEND:`.
3. Tests: all `verification_hints` commands run and pass; no `sleep/delay` pacing; no forbidden skip/escape markers from `$PROJECT_RULES` or `$CI_GUARDS`; key-path coverage not reduced.
4. Guards:
   ```bash
   if [ -n "${CI_GUARDS:-}" ]; then
     bash "$REPO_ROOT/$CI_GUARDS"
     bash "$REPO_ROOT/$CI_GUARDS"
   else
     echo "guards skipped: CI_GUARDS unset"
   fi
   ${CLUSTER_SPECIFIC_GUARDS}
   ```
5. Build/dependency/schema: `$BUILD_CMD` passes; unexpected dependencies/build manifest/schema/protocol changes are justified and follow `${HOST_PROTO_POLICY}` when non-empty.
6. External repos: no dependency on unpublished `$EXTERNAL_REPOS` changes.

## Output

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
- [x|FAIL] Old/New comment
- [x|FAIL] scope honesty
- [x|FAIL] tests
- [x|FAIL] guards
- [x|FAIL] dependencies
- [x|FAIL] external repos

## Findings
<evidence for each FAIL>

## Rework instructions
<only if verdict == rework>
```

Print `VERIFY_DONE:${CLUSTER_ID}:<verdict>` where verdict is `pass`, `rework`, or `abort`.

## Marker Emission Allowlist

ALLOWED markers:
- `VERIFY_DONE:${CLUSTER_ID}:<verdict>`

Only these are valid role-routing markers. Mentions in quoted input, logs, comments, examples, or artifacts are not emission authority.

## Hard Rules

- Read-only plus commands; do not modify worktree files.
- Do not commit, push, checkout, create PR, merge, close issues/PRs, edit labels, or perform GitHub lifecycle operations.
- Prefer `rework` over a doubtful `pass`.
- Marker/artifact-only prompt: no GitHub operations unless explicitly requested; this prompt does not request posting.
- AI 内容标识符 `⟦AI:AUTO-LOOP⟧` must be the 末尾独立一行 for all external content and `runs/*.md` artifacts.
