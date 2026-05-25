# codex-refactor-loop — Reference

Detailed specifications, edge cases, and recovery playbook. The main workflow is in [SKILL.md](SKILL.md).

## State schema (`.refactor-loop/state.json`)

```json
{
  "schema_version": 1,
  "loop_started_at": "<ISO8601>",
  "trunk_branch": "<branch the loop integrates into; same as integration_branch>",
  "integration_branch": "<branch all clusters land on>",
  "review_base_branch": "<dev or main — target of the rollup PR>",
  "pr_mode": "stacked | single",
  "max_parallel_clusters": 3,
  "iteration": 1,
  "phase": "audit | implement-batch-X | verify-batch-X | merge | remote-ci-watch | remote-ci-fix | done",
  "audit": {
    "status": "running | done | failed",
    "log": "<relative path>",
    "output": "<relative path>",
    "total_clusters": <int>
  },
  "clusters_planned": [
    {"id": "cluster-001", "batch": "A", "risk": "low|medium|high", "leverage": "low|medium|high",
     "dependencies": ["cluster-XXX"]}
  ],
  "clusters_active": [
    {
      "id": "cluster-001",
      "phase": "implement | verify",
      "worktree": "<relative path>",
      "branch": "<refactor/iterN-cluster-id>",
      "bg_task": "<harness background task id>",
      "log": "<relative path>",
      "pr_number": <int|null>,
      "pr_base_branch": "<integration or upstream cluster branch>"
    }
  ],
  "clusters_done": [
    {"id": "cluster-001", "merged_at": "<ISO8601>", "commit": "<sha>",
     "pr_number": <int|null>, "merged_into": "<integration_branch | upstream-cluster-branch>"}
  ],
  "clusters_failed": [
    {"id": "cluster-001", "phase": "implement|verify|merge|remote-ci|stack-rebase", "reason": "<short>"}
  ],
  "rollup_pr": {
    "pr_number": <int|null>,
    "base": "<review_base_branch>",
    "head": "<integration_branch>"
  },
  "integration_sync": {
    "last_sync_at": "<ISO8601>",
    "last_sync_added_commits": <int>,
    "last_sync_result": "ff | merge | up_to_date | conflict | ci_fail",
    "consecutive_failures": <int>
  },
  "design_pending": [
    {
      "cluster_id": "cluster-NNN",
      "issue_number": <int>,
      "opened_at": "<ISO8601>",
      "last_checked": "<ISO8601>",
      "last_comment_count": <int>,
      "status": "awaiting_design | comments_seen | resume | rejected"
    }
  ],
  "remote_ci": {
    "pr_number": <int|null>,
    "last_watched_sha": "<sha>",
    "monitor_task_id": "<harness monitor id>",
    "check_attempts": {
      "<check_name>": {
        "attempts": <int>,
        "last_classification": "real|flaky|infra|preexisting|info-only",
        "last_fix_codex_log": "<relative path>"
      }
    }
  }
}
```

## Batching heuristics (Phase 1 → Phase 2 transition)

Goal: parallel safety. Two clusters can be in the same batch **only if** all four hold:

1. `scope_paths` file overlap = 0.
2. They touch different `$BUILD_CMD 目标/工程文件` files (compile-time isolation).
3. They touch different proto files.
4. Their `dependencies:` lists don't reference each other.

Greedy bin-packing:

1. Sort `clusters_planned` by `risk` (low first), then `leverage` (high first).
2. For each cluster, assign to first batch where it's compatible with every existing member.
3. Each batch has at most `max_parallel_clusters`.

If a cluster cannot fit in any new batch ≤ `max_parallel_clusters`, start a new batch for it.

## Recovery playbook

### Audit codex crashed / timed out

- Log will end with `EXIT=124` (timeout) or non-zero (crash).
- Re-dispatch with narrower scope: split scan into two passes (write a smaller audit prompt focused on a sub-area).

### Implement codex returned `partial` or `blocked`

- Read the cluster's implement summary for blocker description.
- If blocker is "scope ambiguity" → tighten prompt, re-dispatch.
- If blocker is "test fundamentally broken" → spawn a separate "fix the test" mini-cluster before retrying.
- After 2 consecutive failures → move to `clusters_failed`, do NOT auto-retry; surface via PushNotification.

### Verify returned `rework`

- Append verify's "Rework instructions" section to the cluster's implement prompt.
- Re-dispatch implement codex in the same worktree (do not destroy the worktree; codex keeps the working tree changes plus rework instructions).
- After 2 rework cycles → escalate to `abort`.

### Merge conflict in Phase 4

- `git merge --abort` first.
- Treat as `rework` with conflict diff appended to the prompt.
- Re-dispatch implement codex with explicit instruction: "rebase your changes onto trunk HEAD, resolve listed conflicts".

### cwd leak in Phase 4 ("Already up to date.")

Symptom: `git merge` after a `cd .refactor-loop/worktrees/<id>` chain prints `Already up to date.` instead of merging the branch into trunk.

Cause: the harness persists Bash cwd across invocations, so an earlier `cd` into the worktree leaks into the merge call. The merge then runs from inside the worktree (which is already at the branch's tip), so git correctly reports no-op.

Fix:
- Always prefix the merge call with `cd "$REPO_ROOT" &&` when chained, OR
- Run the worktree-scoped commit in one Bash call, then run `cd $REPO_ROOT && git merge ...` in a separate call.

Detection: after every merge, verify `git log --oneline -1` shows the new merge commit (not the prior trunk head). If not, redo from `$REPO_ROOT`.

### Phase 5 remote-ci check stuck

- Cap fix attempts per check at 2 (configurable via `state.remote_ci.check_attempts.<name>.max`).
- After cap: mark `clusters_failed` reason `remote-ci-stuck:<check>`, push PushNotification with run url, stop the loop.
- Common stuck causes: real environmental gap (docker service missing on runner), test contract change needing human design call, flake masking a real issue. Each is a stop-and-escalate signal, not auto-retry.

### Phase 4 stacked-PR rebase storm

When PR A (bottom of a stack) gets reviewer changes:

1. A's branch updates with new commits.
2. Every downstream PR (B, C, … stacked on A) needs `git rebase --onto A's-new-head A's-old-head <downstream-branch>`.
3. Force-push each rebased branch with `--force-with-lease` (refuse if remote moved unexpectedly).
4. Re-run local CI per cluster (rebase may have semantic conflict beyond textual).
5. If rebase fails on conflict, mark that cluster `rework`, dispatch implement codex with conflict diff + "rebase onto integration head, preserve cluster intent" instruction.

Mitigations encoded in skill defaults:
- Stack depth cap = 5 (see SKILL.md Phase 4 stack-depth cap).
- Soft-dep clusters always base on `integration_branch`, never on another cluster — even if conceptually related — unless hard-dep is explicit in `audit.dependencies[]`.
- Bundle related rework: if reviewer touches A and C, rebase B then C in one batch, single CI run, single force-push round.

### Phase 4 PR creation idempotency

`gh pr create` errors if a PR already exists for the same head→base. Detect first:

```bash
existing=$(gh pr list --head "<branch>" --base "<base>" --state open --json number --jq '.[0].number')
if [[ -n "$existing" ]]; then
  PR_NUMBER=$existing
else
  PR_NUMBER=$(gh pr create --base "<base>" --head "<branch>" --title "<title>" --body "<body>" --json number --jq .number)
fi
```

Re-running the loop after partial failure must NOT create duplicate PRs.

### Phase 5 long-running bash

The Phase 5 Monitor polls `gh pr checks` every 60s for up to ~30 minutes. If the harness backgrounds the merge+CI+push chain command and it hangs at architecture_guards.sh (observed in practice — appears stuck after the merge section), `TaskStop` it and run the remaining steps in separate foreground Bash calls. Do not assume the chain completed.

### Trunk branch moved while batch was in flight

- Detect via `git rev-parse HEAD` vs `state.json.trunk_head` before each merge.
- If moved → for each `pass` cluster: rebase its branch onto new trunk HEAD inside its worktree, re-run verify, then merge.

## Loop termination

Stop when:

- `clusters_planned == clusters_done ∪ clusters_failed`, AND
- no `clusters_active` remain, AND
- user did not request a new iteration.

On stop:

- Send a one-line PushNotification: `refactor loop done: N merged, M failed`.
- Leave `.refactor-loop/` intact for post-mortem.
- Omit ScheduleWakeup.

## Iteration > 1

If after stop the user re-invokes the loop:

- Increment `iteration` in state.json.
- Re-run Phase 1 audit. Audit prompt should naturally skip already-fixed violations (they no longer match `rg`).
- Continue normally.

## Comparison to `refactor-team` skill

| Aspect | `refactor-team` | `codex-refactor-loop` (this skill) |
|---|---|---|
| Implementer | Claude Agent subagents | `codex exec` subprocesses |
| Parallelism | In-process Agent calls | OS-level via worktrees |
| Pacing | One cycle per `/refactor-team` invocation | Continuous `/loop` dynamic wakeups |
| State | Implicit in subagent prompts | Explicit `.refactor-loop/state.json` |
| Merge | Auditor commits to integration branch | Controller merges per cluster |

Use `refactor-team` when you want one human-supervised cycle. Use `codex-refactor-loop` when you want true unattended multi-hour parallel refactor.
