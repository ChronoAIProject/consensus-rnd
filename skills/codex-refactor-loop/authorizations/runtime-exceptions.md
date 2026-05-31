# Runtime Exception Authorization Mirror

This checked-in mirror preserves the durable authorization evidence for named
runtime exceptions whose original Phase 9 judge logs or maintainer directive
captures live under ignored `.refactor-loop/runs/` runtime output paths. It is
the only checked-in runtime authorization evidence mirror. It is not a runtime
API, loader, schema, or source of new authority. The executable contract remains
in `SKILL.md` and the tests.

<a id="maintainer-directive-concurrency-auto-topup"></a>
## maintainer-directive-concurrency-auto-topup

- source_kind: maintainer_directive
- surface: `concurrency_monitor auto-topup`
- source_date: `2026-05-26`
- source_evidence: maintainer loning said "改一下监控,低于预期数就继续派发", "关于并发问题直接自己改", and "你直接先把并发检测的问题解决掉, 现在总是空闲"; commit evidence `91cb381 feat(skill): concurrency_monitor 改 alert+auto-topup(maintainer 实证 #56 路径) (#57)` and `f2854db chore(skill): 扩 CLAUDE.md 例外子句覆盖 maintainer-directive equivalence 路径(#54 r2 共识) (#48)`.
- local_original_pointer: `.refactor-loop/runs/maintainer-directives/2026-05-26-concurrency-auto-topup.md`
- affected_contracts: `SKILL.md` named runtime exception for `concurrency_monitor auto-topup`; `concurrency` dispatch-queue top-up behavior.
- allowed_directive: only `skills/codex-refactor-loop/scripts/concurrency_monitor.py` / packaged concurrency monitor deficit handling may add `_try_topup` plus the tick deficit branch to consume `.refactor-loop/dispatch-queue/<p0|p1|p2>/*.dispatch.json`; behavior/source tests and the SKILL narrow exception may document that existing controller-enqueued work is dispatched when actual workers are below expectation.
- forbidden_boundary: no issue/PR lifecycle, label lifecycle, commit, push, merge, tag, release, prompt-body decision, host fact invention, generic lifecycle actor, or authority outside the controller/actor-enqueued dispatch queue.
- verification: `test_concurrency_monitor.py`, `test_ensure_project_rules_fixed_points.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This entry mirrors the existing maintainer-directive equivalence evidence; it does not grant new dispatch authority beyond the checked-in narrow queue consumer contract.

<a id="maintainer-directive-progress-reporter-orphan-delete"></a>
## maintainer-directive-progress-reporter-orphan-delete

- source_kind: maintainer_directive
- surface: `codex-progress-reporter orphan delete retry`
- source_date: `2026-05-27`
- source_evidence: maintainer loning, 2026-05-27 wakeup, said "这个 bug 直接改吧" for issue #69 accumulated progress comments; commit evidence `897a670 fix(skill): progress-reporter orphan delete retry (issue #69 实证) (#73)`.
- local_original_pointer: `.refactor-loop/runs/maintainer-directives/2026-05-27-progress-reporter-orphan-delete.md`
- affected_contracts: `SKILL.md` named runtime surface for progress reporter `TEST_NO_LOOP`; progress reporter orphan delete retry behavior.
- allowed_directive: in `codex-progress-reporter`, mark `finished=true` only after delete success or 404, retry nonterminal delete failures on later ticks, allow terminal orphan retry when the log still exists, and expose `TEST_NO_LOOP=1` only as a source-time behavior-test seam.
- forbidden_boundary: no daemon architecture rewrite, no interval default change, no GitHub cleanup of existing spam through this runtime path, no production use of `TEST_NO_LOOP`, no new state file, queue, lifecycle authority, host fact source, issue/PR lifecycle, label lifecycle, commit, push, merge, tag, or release authority.
- verification: `test_progress_reporter.py`, `test_codex_progress_reporter_orphan.py`, `test_ensure_project_rules_fixed_points.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This entry mirrors the checked-in orphan retry bugfix authorization and does not widen progress reporter beyond its existing own-comment maintenance surface.

<a id="maintainer-directive-existing-issue-priority-over-audit"></a>
## maintainer-directive-existing-issue-priority-over-audit

- source_kind: maintainer_directive
- surface: `existing issue priority over audit fallback`
- source_date: `2026-05-28`
- source_evidence: maintainer loning@aelf.io said "改一下skills, 优先处理已经存在的issues,而不是派遣新的audit"; commit evidence `e5763d5 feat(skill): existing-issue priority strictly over audit fallback`.
- local_original_pointer: `.refactor-loop/runs/maintainer-directives/2026-05-28-existing-issue-priority-over-audit.md`
- affected_contracts: `SKILL.md` Concurrency Floor and existing-issue priority route table; wakeup-plan existing work ordering.
- allowed_directive: when codex-floor deficit exists, satisfy open managed issue/PR next-step work without in-flight coverage for its current phase before dispatching fresh audit; route design-solving, reviewing, fixing, implementing, pr-open, and consensus-reached work before ordinary audit fallback.
- forbidden_boundary: no spawning fresh audit while existing design-solving/fixing/reviewing/implementing/pr-open/consensus-reached managed work has zero matching in-flight codex; no backlog-increasing audit as a substitute for actionable existing work; no issue/PR lifecycle, label lifecycle, commit, push, merge, tag, release, or generic lifecycle actor.
- verification: `test_wakeup_plan.py`, `test_skill_entrypoint_contract.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This entry only records the priority ordering evidence; actual lifecycle actions remain controller-owned and separately gated.

<a id="maintainer-directive-stale-issue-3h-revival"></a>
## maintainer-directive-stale-issue-3h-revival

- source_kind: maintainer_directive
- surface: `3-hour stale issue/PR auto-revival`
- source_date: `2026-05-28`
- source_evidence: maintainer loning@aelf.io said "改skills 超过3小时没处理的已经存在的issues/pr就应该重新处理"; commit evidence `dbd5dfd feat(skill): 3-hour stale issue/PR auto-revival`.
- local_original_pointer: `.refactor-loop/runs/maintainer-directives/2026-05-28-stale-issue-3h-revival.md`
- affected_contracts: `SKILL.md` stale-issue revival route table; wakeup-plan stale managed item routing.
- allowed_directive: on each wakeup, compute a UTC 3-hour cutoff from GitHub `updatedAt`, re-dispatch open managed issue/PR next-step actors when stale, include unlabeled/default design route handling, and post visible `stale_hours=N` revival evidence through the normal controller path.
- forbidden_boundary: no treating a current-looking phase label, prior marker, or "awaiting" comment as an exemption; stale `updatedAt` is routing metadata only and does not authorize GitHub comments, label edits, PR merges, issue closes, takeover, issue/PR lifecycle, commit, push, merge, tag, release, or generic lifecycle actor outside existing gates.
- verification: `test_wakeup_plan.py`, `test_skill_entrypoint_contract.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This entry mirrors routing evidence only; write permits remain gated by active-controller ownership and existing controller primitives.

<a id="maintainer-directive-floor-no-exemption"></a>
## maintainer-directive-floor-no-exemption

- source_kind: maintainer_directive
- surface: `concurrency floor no exemption`
- source_date: `2026-05-29`
- source_evidence: maintainer loning explicit instruction "并发数不足无豁免,直接改skills"; commit evidence `b1ec9f4 feat(skill): 并发不足无豁免(删 no-work-after-audit escape)+ wakeup_plan→cli/filter-open(#162)`.
- local_original_pointer: `.refactor-loop/runs/maintainer-directives/2026-05-29-floor-no-exemption.md`
- affected_contracts: `SKILL.md` Concurrency Floor; `wakeup_plan.py` hard gate; concurrency floor tests.
- allowed_directive: when `deficit>0`, always emit/obey hard-gate dispatch for real work first and audit fallback when no actionable existing work exists; delete the `CONCURRENCY_LOW:no-work-after-audit-none` floor exemption and remove `AUDIT_DONE:none:0` as a floor immunity.
- forbidden_boundary: no ending a wakeup with positive deficit, no low-floor exemption, no fabricating non-audit work, no issue/PR lifecycle, label lifecycle, commit, push, merge, tag, release, or generic lifecycle actor.
- verification: `test_wakeup_plan.py`, `test_skill_floor_fill_not_optional.py`, `test_skill_entrypoint_contract.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This entry preserves the existing hard-gate routing rule and does not add lifecycle authority.

<a id="maintainer-directive-milestone-priority"></a>
## maintainer-directive-milestone-priority

- source_kind: maintainer_directive
- surface: `milestone priority`
- source_date: `2026-05-29`
- source_evidence: maintainer loning explicit instruction; commit evidence `4396eb9 feat(skill): milestone 优先级机制(🎯 milestone 标签,active 时优先派该 issue)`.
- local_original_pointer: `.refactor-loop/runs/maintainer-directives/2026-05-29-milestone-priority.md`
- affected_contracts: `SKILL.md` Milestone priority; wakeup-plan ordering; label catalog normalization.
- allowed_directive: use GitHub `crnd:milestone:current` as the sole milestone membership fact source; when at least one open managed issue/PR is milestone-labeled, dispatch milestone next-step work before non-milestone existing-issue work and ordinary audit fallback, while bootstrap/wake source, maintainer comment, completed marker same-wakeup route, CI red, and no-gap violation remain higher priority.
- forbidden_boundary: no parallel milestone state file, queue, marker, local cache, or work-unit field; no killing already-running non-milestone codex solely because milestone became active; no phase/human semantics change; no issue/PR lifecycle, label lifecycle, commit, push, merge, tag, release, or generic lifecycle actor.
- verification: `test_wakeup_plan.py`, `test_skill_entrypoint_contract.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This entry mirrors dispatch priority evidence only; it does not grant new label mutation or lifecycle authority.

<a id="maintainer-directive-wakeup-plan-script"></a>
## maintainer-directive-wakeup-plan-script

- source_kind: maintainer_directive
- surface: `read-only wakeup-plan script`
- source_date: `2026-05-29`
- source_evidence: maintainer loning explicit instruction to add a mechanically called wakeup-plan script, plus the 2026-05-29 hard-gate addition; commit evidence `2c8091d feat(skill): wakeup_plan.py 机械化每次唤醒(daemon健康+按序取任务+milestone优先+并发缺口hard-gate+无任务推荐audit)`.
- local_original_pointer: `.refactor-loop/runs/maintainer-directives/2026-05-29-wakeup-plan-script.md`
- affected_contracts: `SKILL.md` Wakeup Skeleton and Controller Wakeup Checklist; `wakeup_plan.py` JSON authorization field; `test_wakeup_plan.py`.
- allowed_directive: add a read-only script called every controller wakeup to report daemon health, ordered actionable tasks, audit recommendation, canonical concurrency actual/target/deficit, and `HARD_GATE:dispatch_required=N` from local evidence plus read-only GitHub/git checks.
- forbidden_boundary: no restart, spawn, commit, push, checkout/switch, branch create/delete/update, worktree add/remove/prune, reset, rebase, merge, label mutation, issue/PR create-close-edit, tag, release, GitHub lifecycle mutation, or worker dispatch from the script.
- verification: `test_wakeup_plan.py`, `test_skill_entrypoint_contract.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This entry mirrors the read-only recommendation surface and hard-gate evidence; the controller remains the lifecycle owner.

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
<!-- Refactor (issue-264): Old: mirror allowed skip on one fresh pidfile wrapper.
New: mirror narrows skip to pid alive + fresh heartbeat + current fingerprint + zero duplicate canonical live wrapper for the same static allowlist command. -->
- allowed: cron or launchd helper maintains singleton wrappers, actor-owned heartbeat leases, helper-private launch fingerprints at `.refactor-loop/locks/<daemon>.fingerprint.json`, and helper-private `DaemonProcessInventory` for the existing static daemon allowlist; pid alive plus fresh heartbeat plus current fingerprint plus zero duplicate canonical live wrapper for the same resolved static allowlist command is the only skip condition, missing, malformed, or mismatched fingerprint data fails closed to restart, and duplicate canonical wrappers fail closed to repair/reconcile before restart; runs 24h log retention.
- forbidden: no host-defined daemon registry, generic process supervisor, GitHub/git lifecycle authority, codex spawn, commit, push, merge, label, archive, index, new daemon, issue lifecycle, PR lifecycle, tag, release, wrapper sidecar heartbeat writer, or generic lifecycle authority.
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
