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
- allowed: read/acquire/renew only the global active-controller lease. Lease-only git allowlist: `git fetch origin <lease-ref>`, `git ls-remote --exit-code --heads origin <lease-ref>`, `git rev-parse`, `git show <commit>:active-controller.json`, `git hash-object -w --stdin`, `git mktree`, `git commit-tree`, and `git push --force-with-lease=<old>:<lease-ref>`. These commands may only read/build/publish the singleton lease blob CAS and expose owner/expiry; gate restart-daemons, concurrency dispatch, phase9 router, comment/progress writes, dev-sync, and controller lifecycle helpers.
- forbidden: no worker diff commit, issue create/edit/close, PR create/edit/merge/close, label mutation, tag, release, per-work claim, host-defined lease scope, cross-device floor aggregation, daemon ownership matrix, active-active scheduler, generic distributed lock library, or generic lifecycle actor.
- metadata_only_193: issue/PR `author.login` and `updatedAt` are planning/routing/stale metadata only; they must not authorize side effects, per-work owner authority, claim/lease scope, stale takeover permit, or any issue/PR target write outside the #191 `ActiveControllerLease` / `require_active_controller(...)` gate.
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

<a id="closed-label-reconciler-238"></a>
## closed-label-reconciler-238

- surface: `consensus-rnd-cli closed-label-reconciler`
- source_issue: `#238`
- source_round: `r3`
- source_marker: `META_JUDGE_DONE:consensus:closed-label-reconciler:option B named restart-managed closed-label-reconciler with crnd:phase:closed and gh-label-closed-reconcile authority`
- skill_anchor: `#named-runtime-exception--closed-label-reconcilerper-238`
- allowed: active-controller owner only; read CLOSED `crnd:lifecycle:managed` issue/PR labels and apply terminal phase-label reconciliation by removing phase labels, cleanup-only aliases, and `crnd:lifecycle:stuck`, then adding exactly one terminal phase label, either `crnd:phase:merged` when merged evidence is present or `crnd:phase:closed` when evidence is insufficient.
- forbidden: no open item mutation, issue create/close/reopen/body/title edit, PR create/merge/close/body/title edit, human label mutation, triage label mutation, milestone label mutation, lifecycle label mutation beyond removing `crnd:lifecycle:stuck`, tag, release, generic `gh-label`, generic `gh-edit`, controller close-path inline reconcile, or generic lifecycle actor.
- fact_source: GitHub CLOSED item state plus live labels, normalized by `closed_phase_labels.py`; `crnd:phase:closed` is a protocol terminal state, not a product verdict.
- verification: `test_closed_label_reconciler.py`, `test_peek_status_lens.py`, `test_cli_command_router.py`, `test_restart_daemons.py`, `test_label_taxonomy.py`, `test_label_contract_source.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This mirror documents only the #238 closed managed item terminal phase-label reconciliation carveout and does not grant generic GitHub label/edit or lifecycle authority.

<a id="update-check-231"></a>
## update-check-231

- surface: `consensus-rnd-cli update-check`
- source_issue: `#231`
- source_round: `r4 structural`
- source_marker: `META_JUDGE_DONE:consensus:B-structural-profile:notify-only-update-check-with-version-manifest-snapshot-projection-shared-semver`
- skill_anchor: `#notify-only-update-checkper-231`
- allowed: read checked-in `skills/codex-refactor-loop/VERSION.json`; read GitHub latest release and tags for the repository named in that manifest; atomically write `.refactor-loop/state/update-check.json`; let `restart-daemons` call the probe after the fixed daemon start/skip pass; let `concurrency` project fresh positive update fields into `statusline-snapshot.json`.
- forbidden: no copy/overwrite/reinstall, host config edit, git lifecycle, GitHub lifecycle, installer, new daemon, commit, push, merge, rebase, reset, tag, release, issue lifecycle, PR lifecycle, label lifecycle, or apply/update command surface.
- verification: `test_update_check.py`, `test_cli_command_router.py`, `test_restart_daemons.py`, `test_concurrency_monitor_snapshot.py`, `test_statusline.py`, `test_runtime_exception_authorization_sources.py`, `test_skill_reference_anchors.py`
- no_new_runtime_authority: This is notify-only state projection; downstream installation or update application is host-owned and outside the skill runtime.

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

<a id="phase9-router-open-state-gate-229"></a>
## phase9-router-open-state-gate-229

- surface: `consensus-rnd-cli phase9-router`
- source_issue: `#229`
- source_round: `phase9-router source-OPEN gate`
- source_marker: `phase9-router source issue state gate`
- skill_anchor: `#consensus-rnd-phase-design-consensus-router-daemon-command-body`
- allowed: read clean-exit logs and the private router ledger; run the state-only source-OPEN gate read `gh issue view <N> --json state` with optional `--repo <owner/repo>` from host GitHub context; append existing-format phase9-router-fallback pending events with reasons `phase9-source-not-open` or `phase9-source-state-unavailable`; write router prompts, append the private dispatch ledger, and spawn only the built-in phase9 direct routes.
- forbidden: no gh issue close, gh issue edit, gh label, gh pr merge, gh release, GitHub lifecycle mutation, issue close, PR merge, label lifecycle, git, commit, push, tag, release, or generic lifecycle authority.
- verification: `test_phase9_router_open_state_gate.py`, `test_phase9_router_daemon.py`, `test_cli_command_router.py`, `test_runtime_exception_authorization_sources.py`, `test_skill_reference_anchors.py`
- no_new_runtime_authority: This mirror records only the #229 state-only `read-gh` source-OPEN gate and does not widen phase9-router beyond the named direct-spawn allowlist or grant lifecycle authority.
