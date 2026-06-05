# PR #522 Merge Resolution

Resolved the in-progress merge from `origin/auto-refact-dev` into `refactor/iter511-issue-511`.

## Per-file notes

- `skills/codex-refactor-loop/SKILL.md`: combined the #511 worker-authored implementation PR title/body artifact contract with the incoming early managed PR reservation/publish contract. `IMPLEMENT_DONE:ok` now documents validation of worker-authored artifacts and publishing to exactly one matching early managed PR.
- `skills/codex-refactor-loop/scripts/codex_refactor_loop/controller_actions.py`: preserved incoming exact early PR matching and stale-base delegated fallback behavior; layered #511 artifact path/title/body validation before PR lookup/publish side effects. Removed the obsolete publish-time PR creation path.
- `skills/codex-refactor-loop/scripts/codex_refactor_loop/wakeup_plan.py`: kept incoming managed-work snapshot loading, spawn-intent suppression, review-thread completion gate, and exact early PR stale suppression; retained #511 title/body path projection and artifact validation.
- `skills/codex-refactor-loop/scripts/codex_refactor_loop/wakeup_runner.py`: combined runner preconditions so publish requires both `worker_authored_pr_artifacts` and `exactly_one_matching_open_pr`, then revalidates artifacts, worktree, and matching early PR.
- `skills/codex-refactor-loop/scripts/test_controller_actions.py`: kept both sides' publish tests and updated expectations for the combined contract: invalid worker artifacts fail before PR lookup, early PR tests provide valid artifacts, and stale-base conflicts delegate to the incoming fallback helper.
- `skills/codex-refactor-loop/scripts/test_skill_reference_anchors.py`: merged anchor expectations for worker-authored PR artifacts and early managed PR reservation/publish behavior.
- `skills/codex-refactor-loop/scripts/test_source_language_policy.py`: removed a stale allowlist entry that no longer corresponds to a Chinese literal in `controller_actions.py`.
- `skills/codex-refactor-loop/scripts/test_wakeup_plan.py`: merged publish precondition expectations for worker-authored artifacts and exact early PR matching, including target PR projection.
- `skills/codex-refactor-loop/scripts/test_wakeup_runner.py`: merged publish precondition fixtures to require worker-authored artifacts plus exact early PR matching.

## Irreconcilable choices

The incoming base branch's daemon-safety-critical early managed PR model was preferred over #511's publish-time PR creation path. #511's rich worker-authored title/body artifacts were re-applied as required validation and projected paths for the early PR flow.

## Verification

- `python3 -m compileall skills/codex-refactor-loop/scripts -q`: passed.
- `python3 -m pytest skills/codex-refactor-loop/scripts -q -p no:cacheprovider -o addopts= --ignore=skills/codex-refactor-loop/scripts/test_restart_daemons.py --ignore=skills/codex-refactor-loop/scripts/test_restart_supervisor.py --ignore=skills/codex-refactor-loop/scripts/test_daemon_heartbeat.py --ignore=skills/codex-refactor-loop/scripts/test_cli_daemon_help_smoke.py --ignore=skills/codex-refactor-loop/scripts/test_peek_status_lens.py --ignore=skills/codex-refactor-loop/scripts/test_zz_daemon_leak_guard.py --ignore=skills/codex-refactor-loop/scripts/test_anti_stop_restart_helper_contract.py`: 1496 passed, 1 skipped, 4709 subtests passed in 358.64s.
