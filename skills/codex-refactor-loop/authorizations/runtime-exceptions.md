# Runtime Exception Authorization Mirror

This checked-in mirror preserves the durable authorization evidence for named
runtime exceptions whose original Phase 9 judge logs live under ignored
`.refactor-loop/runs/` runtime output paths. It is not a runtime API, loader,
schema, or source of new authority. The executable contract remains in
`SKILL.md` and the tests.

<a id="autonomous-release-gate-56"></a>
## autonomous-release-gate-56

- surface: `autonomous release gate`
- source_issue: `#56`
- source_round: `r2`
- source_marker: `META_JUDGE_DONE:consensus:A-with-host-opt-in-as-gate`
- skill_anchor: `#named-runtime-exception--autonomous-release-gateper-56`
- allowed: decide release readiness only after `RELEASE_AUTO_ENABLE=true`; write release decision and candidate artifacts.
- forbidden: no git, bump, commit, push, tag, publish, merge, close, issue lifecycle, PR lifecycle, or label lifecycle authority.
- verification: `test_release_gate_module.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This mirror only replaces the missing ignored judge-log authorization path.

<a id="active-controller-lease-191"></a>
## active-controller-lease-191

- surface: `single active controller lease`
- source_issue: `#191`
- source_round: `r2`
- source_marker: `META_JUDGE_DONE:consensus:single-active-controller`
- skill_anchor: `#named-runtime-exception--active-controller-leaseper-191`
- durable_source: singleton JSON blob `active-controller.json` on `refs/heads/crnd/active-controller`; fields `owner_device`, `lease_id`, `acquired_at`, `expires_at`, `renewed_at`, `repo`, `reason`, `source_issue`.
- allowed: read/acquire/renew only the global active-controller lease via `git fetch origin <lease-ref>`, `git rev-parse`, `git commit-tree`, and `git push --force-with-lease=<old>:<lease-ref>`; expose owner/expiry; gate restart-daemons, concurrency dispatch, phase9 router, comment/progress writes, dev-sync, and controller lifecycle helpers.
- forbidden: no worker diff commit, issue create/edit/close, PR create/edit/merge/close, label mutation, tag, release, per-work claim, host-defined lease scope, cross-device floor aggregation, daemon ownership matrix, active-active scheduler, generic distributed lock library, or generic lifecycle actor.
- verification: `test_active_controller_lease.py`, `test_restart_daemons.py`, `test_concurrency_monitor.py`, `test_phase9_router_package.py`, `test_comment_progress_active_controller.py`, `test_sync_dev.py`, `test_controller_actions.py`, `test_runtime_exception_authorization_sources.py`, `test_skill_reference_anchors.py`, `test_host_env_surface_matrix.py`
- no_new_runtime_authority: This is a singleton coordination gate only; it does not grant lifecycle authority and does not create per-work leases.

<a id="release-commits-producer-232"></a>
## release-commits-producer-232

- surface: `consensus-rnd-cli release-commits`
- source_issue: `#232`
- source_round: `r2-minimal`
- source_marker: `SOLVER_DONE:minimal:propose:lock minimal boundary to read-git pre-gate release-commits producer; keep release-gate consumer-only`
- skill_anchor: `#named-runtime-exception--autonomous-release-gateper-56`
- allowed: `read-git` and `write-artifact` only: read local git only to fetch tags, describe the latest release tag, resolve the target ref, and log the latest-tag-to-target range; atomically write `.refactor-loop/state/release-commits.json`.
- forbidden: no GitHub API, push, merge, reset, rebase, worktree mutation, tag, release, commit, issue lifecycle, PR lifecycle, label lifecycle, generic lifecycle authority, or inline execution from `release-gate`.
- fact_source: local git tags and refs.
- verification: `test_release_commits.py`, `test_cli_command_router.py`, `test_release_gate_module.py`
- no_new_runtime_authority: This mirror documents only the independent narrow producer; it does not widen `release-gate`, which remains no-git decision-artifact-only.

<a id="integration-sync-daemon-53"></a>
## integration-sync-daemon-53

- surface: `integration sync daemon`
- source_issue: `#53`
- source_round: `r7`
- source_marker: `META_JUDGE_DONE:consensus`
- skill_anchor: `#named-runtime-exception--integration-sync-daemonper-53`
- allowed: write integration sync operation artifacts in the dedicated integration worktree; run `git ls-remote --exit-code --heads origin $INTEGRATION_BRANCH`; use the existing narrow integration-branch git allowlist through daemon-owned execution: `git fetch`, `git ls-remote --exit-code --heads origin $INTEGRATION_BRANCH`, `rev-list`, `rev-parse`, `merge-base`, `reset --hard`, `rebase --rebase-merges`, `merge --ff-only|--no-ff`, `git push HEAD:$INTEGRATION_BRANCH`, and force-with-lease rollup adoption.
- forbidden: no worker-diff commit, no PR create, merge, close, or edit, no issue lifecycle, no label lifecycle, no tag, release, generic lifecycle actor, or git command outside the #53 allowlist.
- verification: `test_sync_dev.py`, `test_sync_operations_executor.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This mirror only replaces the missing ignored judge-log authorization path.

<a id="observability-comment-writers-53"></a>
## observability-comment-writers-53

- surface: `observability-comment-writers`
- source_issue: `#53`
- source_round: `r7`
- source_marker: `META_JUDGE_DONE:consensus`
- skill_anchor: `#named-runtime-exception--observability-comment-writersper-53`
- allowed: GitHub issue or PR comments, PR body edit, reactions, and deleting or updating own progress comments only.
- forbidden: no label mutation, issue close, PR close, issue create, PR create, PR merge, release, tag, or git lifecycle authority.
- verification: `test_banner_package.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This mirror only replaces the missing ignored judge-log authorization path.

<a id="integration-sync-release-rollup-65"></a>
## integration-sync-release-rollup-65

- surface: `integration sync release rollup`
- source_issue: `#65`
- source_round: `r7`
- source_marker: `META_JUDGE_DONE:consensus`
- skill_anchor: `#named-runtime-exception--integration-sync-daemonper-65`
- allowed: release-rollup detection and existing-format pending-event emission only.
- forbidden: no PR create, PR edit, PR label, PR close, PR approve, PR merge, issue lifecycle, direct push to review base, tag, release, or generic lifecycle authority.
- verification: `test_sync_dev.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This mirror only replaces the missing ignored judge-log authorization path.

<a id="skill-degradation-watch-66"></a>
## skill-degradation-watch-66

- surface: `skill degradation watch`
- source_issue: `#66`
- source_round: `r8`
- source_marker: `META_JUDGE_DONE:consensus`
- skill_anchor: `#named-runtime-exception--skill-degradation-watchper-66`
- allowed: run `consensus-rnd-cli check-degradation`; write `.refactor-loop/.degradation-alert.log`; append existing-format pending events; expose read-only `consensus-rnd-cli peek` status.
- forbidden: no source mutation, git reset, rebase, merge, push, GitHub issue lifecycle, PR lifecycle, body lifecycle, label lifecycle, codex dispatch, standalone daemon creation, WorkUnit schema changes, event envelope changes, protocol registry, plugin registry, auto-clean, or auto-fix API.
- verification: `test_check_skill_degradation.py`, `test_package_checks.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This mirror only replaces the missing ignored judge-log authorization path.

<a id="statusline-51"></a>
## statusline-51

- surface: `Claude Code statusline`
- source_issue: `#51`
- source_round: `r3`
- source_marker: `META_JUDGE_DONE:consensus:C`
- skill_anchor: `#claude-code-statuslineper-51-consensus`
- allowed: `concurrency_monitor` writes the statusline snapshot and stats heartbeat files; `consensus-rnd-cli statusline` reads the snapshot.
- forbidden: no new daemon, installer script, GitHub lifecycle, git lifecycle, file lifecycle beyond snapshot writes, issue lifecycle, PR lifecycle, label lifecycle, tag, release, commit, push, merge, or close authority.
- verification: `test_statusline.py`, `test_skill_reference_anchors.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This mirror only replaces the missing ignored judge-log authorization path.

<a id="anti-stop-restart-helper-49"></a>
## anti-stop-restart-helper-49

- surface: `anti-stop restart helper`
- source_issue: `#49`
- source_round: `r3`
- source_marker: `META_JUDGE_DONE:consensus:A-cron-only-with-pending-event-alert`
- skill_anchor: `#named-runtime-exception--anti-stop-restart-helperper-49`
- allowed: cron or launchd helper maintains singleton wrappers, actor-owned heartbeat leases, and helper-private launch fingerprints at `.refactor-loop/locks/<daemon>.fingerprint.json` for the existing daemon allowlist; pid alive plus fresh heartbeat plus current fingerprint is the only skip condition, and missing, malformed, or mismatched fingerprint data fails closed to restart; runs 24h log retention.
- forbidden: no codex spawn, commit, push, merge, label, archive, index, new daemon, issue lifecycle, PR lifecycle, tag, release, wrapper sidecar heartbeat writer, or generic lifecycle authority.
- verification: `test_restart_daemons.py`, `test_anti_stop_restart_helper_contract.py`, `test_log_retention.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This mirror only replaces the missing ignored judge-log authorization path.
