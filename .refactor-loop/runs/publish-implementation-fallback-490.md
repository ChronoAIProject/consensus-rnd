## Publish implementation fallback - issue 490

Source marker: `IMPLEMENT_DONE:issue-490:ok`

State inspected:
- Branch `refactor/iter490-issue-490` tracks `origin/auto-refact-dev`.
- Initial state was ahead 2 / behind 1 with no merge in progress and no unmerged index entries.
- `git diff --check origin/auto-refact-dev...HEAD` exposed leftover conflict markers in three files from the stale-base publish path.

Changed files:
- `skills/codex-refactor-loop/SKILL.md`: removed stale conflict markers and preserved both the #490 task-spawn-claim section and the fresh #504 global-dashboard-status-card section.
- `skills/codex-refactor-loop/authorizations/runtime-exceptions.md`: removed stale conflict markers and preserved both runtime exception records.
- `skills/codex-refactor-loop/scripts/test_skill_reference_anchors.py`: removed stale conflict markers and preserved both source-regression tests.
- `skills/codex-refactor-loop/scripts/codex_refactor_loop/task_spawn_claim.py`: retained fail-closed error reporting for metadata cleanup failure and unreadable recycle logs, aligning the issue 490 implementation with the fresh project rule forbidding silent failure.

Verification:
- `git diff --check`
- `python3 -m unittest skills/codex-refactor-loop/scripts/test_task_spawn_claim.py skills/codex-refactor-loop/scripts/test_spawn_claim.py skills/codex-refactor-loop/scripts/test_runtime_exception_authorization_sources.py skills/codex-refactor-loop/scripts/test_skill_reference_anchors.py`
- Result: 99 tests passed.

Unresolved risk:
- The branch remains topologically ahead 2 / behind 2 relative to `origin/auto-refact-dev`; this fallback resolved the stale conflict markers and staged the local resolution, but did not commit, push, rebase, merge, or create/update PRs per role boundary.

⟦AI:AUTO-LOOP⟧
PUBLISH_FALLBACK_DONE:490:resolved-stale-base-conflict
