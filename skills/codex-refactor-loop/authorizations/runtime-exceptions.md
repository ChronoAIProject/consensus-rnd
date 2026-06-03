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
- allowed_directive: only `skills/codex-refactor-loop/scripts/concurrency_monitor.py` / packaged concurrency monitor deficit handling may add `_try_topup` plus the tick deficit branch to consume `.refactor-loop/dispatch-queue/<p0|p1|p2>/*.dispatch.json` and append `HARNESS_SPAWN_INTENT` with `command: "spawn-codex"` as a closed semantic enum; behavior/source tests and the SKILL narrow exception may document that existing controller-enqueued work is intent-dispatched when actual workers are below expectation.
- forbidden_boundary: no daemon direct `nohup spawn-codex`, no daemon executable command surface, no argv array, no shell command, no generic command bus, no issue/PR lifecycle, label lifecycle, commit, push, merge, tag, release, prompt-body decision, host fact invention, generic lifecycle actor, or authority outside the controller/actor-enqueued dispatch queue.
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
<!-- Refactor (issue-277): Old: floor-no-exemption mirror implied audit fallback was unlimited dispatch authority. New: no general exemption remains,`AUDIT_DONE:none:0` still does not exempt, but same-iteration active audit exposes blocked_deficit as WAIT and forbids duplicate audit/lane protocol/fabricated work. -->
- allowed_directive: when `deficit>0`, emit/obey hard gate for legal dispatchable real work first; dispatch ordinary audit fallback when no actionable existing work exists and no same-iteration audit is active; when same-iteration audit is already active, expose blocked deficit with `dispatch_required=0`, `reason=single_active_audit_in_flight`, and `blocked_deficit=N`, and do not duplicate audit. `AUDIT_DONE:none:0` still does not exempt the floor.
- forbidden_boundary: no general low-floor exemption, no duplicate same-iteration audit, no fabricated work, no AuditLaneIdentity, no `AUDIT_LANE_ID`, no `audit-iter-N-laneK`, no lane/shard protocol in this issue, no ending a wakeup with positive deficit except the visible `WAIT:single-active-audit` blocked boundary, no issue/PR lifecycle, label lifecycle, commit, push, merge, tag, release, or generic lifecycle actor.
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

<a id="release-publication-322"></a>
## release-publication-322

<!-- Refactor (iter1/issue-322): Old pattern: ReleasePublisher controller writes lived only in SKILL prose. New principle: name release-publication-322, mirror its exact allowlist, and lock forbidden lifecycle surfaces with tests. -->
<!-- Refactor (iter341/issue-341): Old pattern: ReleasePublisher.publish() 线性 bump/add/commit→push→green-gate;push 后 CI pending 即陷入不可恢复授权态(manifests 已 bump,re-run git commit nothing-to-commit 失败)——beta.5 靠 controller hand-complete 绕过. New principle: 单一 publish() 主链路加 already-bumped reentry:仅当唯一 preflight mismatch 是 mapped manifests 已==to_version 且 git show -s --format=%s HEAD 证明 HEAD subject 精确为 'Release v<to_version>' 时跳过 bump/add/commit 三步,随后仍必须 _safe_push + exact-SHA required-checks green gate + gh release create + result artifact。严格按 DESIGN_DECISION_PATH verbatim Concrete plan;不新增 resume ticket/public CLI/workflow 发版权/host.env 事实源. -->
- surface: `controller-owned ReleasePublisher release publication`
- source_issue: `#322`
- source_round: `r2 structural`
- source_marker: `META_JUDGE_DONE:consensus:structural:ReleasePublisher release-publication-322 mirror with exact allowlist tests`
- skill_anchor: `#named-runtime-exception--release-publicationper-322`
- allowed: active-controller owner only after `ReleasePublishPreflight` validates `RELEASE_AUTO_ENABLE=true`, fresh `.refactor-loop/state/release-candidate.json`, fresh `.refactor-loop/state/release-decision.json`, matching `decision_digest`, matching `target_ref`, mapped manifest `from_version`, and required checks green; first-run exact command allowlist is `python3 .github/scripts/bump_version.py --version <to_version>`, `git add .version-bump.json <mapped manifests>`, `git commit -m "Release v<to_version>"`, `git rev-parse HEAD`, `git fetch origin HEAD`, `git rev-list --count HEAD..origin/HEAD`, `git push origin HEAD`, `gh api repos/<slug>/commits/<fresh release commit sha>/check-runs --paginate --slurp`, and `gh release create v<to_version> --target <fresh release commit sha> --generate-notes [--prerelease]`; already-bumped reentry is allowed only when the only preflight mismatch is mapped manifests already equal `to_version` and `git show -s --format=%s HEAD` proves the HEAD subject is exactly `Release v<to_version>`; reentry may skip only `python3 .github/scripts/bump_version.py --version <to_version>`, `git add .version-bump.json <mapped manifests>`, and `git commit -m "Release v<to_version>"`, then must run `git rev-parse HEAD`, `git fetch origin HEAD`, `git rev-list --count HEAD..origin/HEAD`, `git push origin HEAD`, `gh api repos/<slug>/commits/<exact release/reentry commit sha>/check-runs --paginate --slurp`, and `gh release create v<to_version> --target <exact release/reentry commit sha> --generate-notes [--prerelease]`; pending/red/missing/API-fail fail closed before release creation; write `.refactor-loop/state/release-publish-result.json`.
- forbidden: no public `consensus-rnd-cli release-publish`, no public `consensus-rnd-cli publish-release`, no workflow tag/release creation, no `git tag`, no force-push, no `git merge`, no `git rebase`, no `git reset`, no GitHub Release edit/delete/upload, no approval-ticket/emoji gate, no proof-ticket/resume system, no issue lifecycle, PR lifecycle, label lifecycle, merge/close, or generic lifecycle actor.
- fact_source: `ReleasePublishPreflight` consumes `.refactor-loop/state/release-candidate.json`, `.refactor-loop/state/release-decision.json`, `.refactor-loop/host.env`, `.version-bump.json`, mapped manifest files, and required check projections; `ReleasePublisher` consumes the approved preflight result and the fresh release commit SHA from `git rev-parse HEAD`.
- verification: `test_release_publisher.py`, `test_release_publish_preflight.py`, `test_cli_command_router.py`, `test_runtime_exception_authorization_sources.py`, `test_release_pipeline_contract.py`, `test_controller_actions.py`
- no_new_runtime_authority: This entry names and mirrors the existing controller-owned ReleasePublisher path; it adds no public CLI, no workflow publication authority, and no production runtime behavior beyond the checked-in preflight plus publisher allowlist.

<a id="controller-release-publisher-334"></a>
## controller-release-publisher-334

<!-- Refactor (iter334/issue-334): Old pattern: ReleasePublisher could create a release tag at a fresh manifest-bump SHA before exact-SHA checks were green. New principle: mirror the direct exact-SHA check gate after safe push and before release creation. -->
- surface: `controller-owned release publisher`
- source_issue: `#334`
- source_round: `r5`
- source_marker: `META_JUDGE_DONE:converge:round-4:decide`
- skill_anchor: `#release-pipeline-integrationpost-61`
- allowed: active-controller owner only; read release candidate/decision artifacts, run `ReleasePublishPreflight`, bump mapped manifests and commit/push the release manifest commit, or use already-bumped reentry only after `git show -s --format=%s HEAD` proves the exact release subject; read exact-SHA Checks API for the exact release/reentry commit sha, create tag/release only after that exact fresh SHA is green, write `.refactor-loop/state/release-publish-result.json`.
- forbidden: no worker diff commit, arbitrary branch push, issue/PR lifecycle, label mutation, workflow tag/release, public CLI release-publish, tag target without exact-SHA green checks, proof-ticket/resume system, release edit/delete/upload, merge/close, or generic lifecycle actor.
- fact_source: release candidate/decision artifacts + mapped manifests + exact fresh SHA + Checks API projection.
- verification: `test_release_publisher.py`, `test_release_pipeline_contract.py`, `test_runtime_exception_authorization_sources.py`, `test_skill_reference_anchors.py`
- no_new_runtime_authority: mirror only, not a runtime API/loader/schema/proof ticket/authorization source beyond the existing controller-owned publisher boundary.

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

<a id="wakeup-runner-396"></a>
## wakeup-runner-396

- surface: `consensus-rnd-cli wakeup-runner`
- source_issue: `#396`
- source_round: `r3 structural`
- source_marker: `META_JUDGE_DONE:consensus:structural:wakeup-plan-closed-action-projection-plus-wakeup-runner-396`
- skill_anchor: `#named-runtime-exception---wakeup-runnerper-396`
- allowed: active-controller owner only; consume only `wakeup-plan` evidence-bound closed action projection with top-level `mode: "closed-action-projection"`, `no_lifecycle_authority: true`, and `apply_authority: "wakeup-runner-396-only"`; each executable action must carry `runner_authority: "wakeup-runner-396"`, `preconditions`, `source_marker` or `source_artifact`, `target_kind`, `target_number`, `target`, `controller_action`, and `no_generic_command: true`; revalidate clean `EXIT=0` source marker, review truth table `reject==0 && approve>=1 && all required reviewers present && all required reviewer heads equal live PR head`, OPEN/live GitHub state, missing/stale per-reviewer head SHA, #191 owner, release #322 preflight, or helper-specific precondition; `wakeup-plan` action `head_sha` is not reviewer-head authority; mechanically call existing controller helpers or #396 narrow helpers for spawn codex, named helper `dispatch_design_consensus` through phase9-router deterministic routes, named helper `dispatch_consensus_implementation`, named helper `publish_implementation_output`, named helper `open_release_rollup_pr_from_action`, publish worker output, dispatch reviewers/fix/remote-ci worker, apply triage decision, merge PR under review truth table, close managed item from drop marker, and publish release through #322.
- forbidden: no arbitrary git/gh command, workflow tag/release, label/merge/close outside existing helper or named #396 helper, prompt-body decision, standalone authorization from `wakeup-plan`, argv/shell/cmd/command_line/commands/env/git/gh/executor/lifecycle_authority/lifecycle_owner/generic command fields, `ControllerTurnDecision`, controller-turn worker, private schema, active-active scheduler, `.refactor-loop/host.env` as host production SSOT, or generic lifecycle actor.
- fact_source: `wakeup-plan` closed action projection is the only action projection fact source but not a standalone authorization source; action `head_sha` cannot substitute for reviewer-head authority; final side-effect permit comes from #191 active-controller owner plus source marker/artifact, review truth table, all required reviewer heads equal live PR head, live GitHub state, release #322 preflight, or helper-specific validator.
- verification: `test_wakeup_runner.py`, `test_wakeup_runner_review_gate.py`, `test_wakeup_runner_release.py`, `test_wakeup_plan.py`, `test_cli_command_router.py`, `test_runtime_exception_authorization_sources.py`, `test_restart_daemons.py`, `test_skill_reference_anchors.py`
- no_new_runtime_authority: This names only the #396 unattended runner carveout; it adds no public generic lifecycle CLI, no command bus, no controller-turn worker, and no authorization beyond checked-in closed projection validation plus existing helpers.

<a id="issue-decomposition-403"></a>
## issue-decomposition-403

- surface: `IssueDecompositionPlan active-controller apply helper`
- source_issue: `#403`
- source_round: `r6 structural`
- source_marker: `META_JUDGE_DONE:consensus:structural:wakeup_plan.py零行为改动-IssueDecompositionPlan发现链路用pending-event-completed-marker-peek`
- skill_anchor: `#large-issue-decomposition`
- allowed: active-controller owner only; consume a validated controller-private `IssueDecompositionPlan` with exactly `{schema, parent_issue, source_consensus_artifact, children:[{slug,title,scope,non_goals,body_artifact_path}], parent_update:{comment_artifact_path}}`; create `crnd:lifecycle:managed` child design issues with the catalog design issue label bundle; post one tracking comment to the parent issue.
- forbidden: no daemon/worker issue creation, no public issue factory, no public CLI command, no wakeup-plan decompose projection, no prompt-body decision, no arbitrary git/gh command, no parent issue close/reopen/body-title edit, no assignee, no milestone, no label lifecycle beyond child design issue catalog labels, no lifecycle_owner/lifecycle_authority/cmd/argv/shell/gh/git/close fields, no absolute or escaping artifact paths, and no generic lifecycle actor.
- fact_source: design-consensus/meta-reflector consensus artifact plus checked-in `IssueDecompositionPlan` JSON/Markdown body artifacts validated by `issue_decomposition.py`; discovery uses phase9-router fallback pending events, generic completed-marker projection, and read-only `peek` pending-events tail.
- verification: `test_issue_decomposition.py`, `test_controller_actions.py`, `test_phase9_router_daemon.py`, `test_wakeup_plan.py`, `test_skill_reference_anchors.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This grants only the #403 active-controller checked-in apply helper; it adds no daemon owner, public CLI, wakeup-plan action projection, issue factory, or parent lifecycle mutation.

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
- allowed: GitHub issue or PR comments, PR body edit, reactions, and deleting or updating own progress comments only; issue/PR target writes still require the #191 `ActiveControllerLease` / `require_active_controller(...)` gate, and this mirror is not a cross-device write permit.
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
<!-- Refactor (issue-298): Old: mirror covered only write-side restart helper health semantics. New: mirror adds read-only daemon-status projection over the same facts while write repair/reload remains restart-daemons. -->
- allowed: cron or launchd helper maintains singleton wrappers, actor-owned heartbeat leases, helper-private launch fingerprints at `.refactor-loop/locks/<daemon>.fingerprint.json`, and helper-private `DaemonProcessInventory` for the existing static daemon allowlist; pid alive plus fresh heartbeat plus current fingerprint plus zero duplicate canonical live wrapper for the same resolved static allowlist command is the only skip condition, missing, malformed, or mismatched fingerprint data fails closed to restart, and duplicate canonical wrappers fail closed to repair/reconcile before restart; runs 24h log retention. `consensus-rnd-cli daemon-status --json` is a read-only daemon-status projection over the same static allowlist, pid/heartbeat/fingerprint readers, cached active-controller status, and `DaemonProcessInventory`; repair/reload remains restart-daemons.
- forbidden: no host-defined daemon registry, generic process supervisor, GitHub/git lifecycle authority, codex spawn, commit, push, merge, label, archive, index, new daemon, issue lifecycle, PR lifecycle, tag, release, wrapper sidecar heartbeat writer, public start/stop/restart/reload lifecycle verb, or generic lifecycle authority.
- verification: `test_restart_daemons.py`, `test_anti_stop_restart_helper_contract.py`, `test_cli_command_router.py`, `test_log_retention.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This mirror only replaces the missing ignored judge-log authorization path.

<a id="phase9-router-open-state-gate-229"></a>
## phase9-router-open-state-gate-229

- surface: `consensus-rnd-cli phase9-router`
- source_issue: `#229`
- source_round: `phase9-router source-OPEN gate`
- source_marker: `phase9-router source issue state gate`
- skill_anchor: `#consensus-rnd-phase-design-consensus-router-daemon-command-body`
- allowed: read open managed issue list, clean-exit logs, and the private router ledger; run DesignConsensusIssueIntake with `gh issue list --repo <owner/repo> --state open --label crnd:lifecycle:managed --json number,title,labels`; run the source-OPEN gate and prompt-source projection read `gh api repos/<slug>/issues/<N>` for issue state/title/body, plus `gh api repos/<slug>/issues/<N>/comments?per_page=20` for bounded recent comments when rendering router-injected issue source snapshots; keep the state-only source-OPEN gate mirror token `gh api repos/<slug>/issues/<N> --jq .state`; append existing-format phase9-router-fallback pending events with reasons `phase9-source-not-open` or `phase9-source-state-unavailable`; before DesignConsensusIssueIntake or converge-to-next-solvers solver dispatch, suppress solver intents when a clean consensus judge log exists (`META_JUDGE_DONE:consensus:*` from `phase9-issue<N>-r*-judge.log` or `meta-judge-issue<N>-r*.log`) or when already-loaded/open issue labels or labels-only live read `gh api repos/<slug>/issues/<N> --jq '[.labels[].name]'` show terminal design-consensus phase labels `crnd:phase:consensus-reached`, `crnd:phase:implementing`, `crnd:phase:merged`, or `crnd:phase:closed`; append existing-format phase9-router-fallback pending events with key prefix `phase9-terminal-eligibility:` and reason `phase9-already-consensus`; let wakeup-plan suppress `dispatch_design_consensus` and design-consensus solver `HARNESS_SPAWN_INTENT` actions for terminal phase labels only; write router prompts, append `HARNESS_SPAWN_INTENT` with `command: "spawn-codex"` as a closed semantic enum, and append the private dispatch ledger with `dispatch_state="harness-intent"` only for the four built-in phase9 direct routes: DesignConsensusIssueIntake queues each r1 solver role (`minimal`, `structural`, `delete`) whose role-specific ledger key, r1 evidence/log, and in-flight target are absent as that role's r1 `HARNESS_SPAWN_INTENT`, and existing evidence/log/in-flight for one solver role suppresses only that role; solver triplet to judge, converge to next solver triplet, and stalled to reflector.
- forbidden: no daemon direct `nohup spawn-codex`, no daemon executable command surface, no argv array, no shell command, no generic command bus, no `argv`, `args`, `shell`, `cmd`, `commands`, `env`, `git`, `gh`, `executor`, or `target_ref` fields in the spawn intent, no gh issue close, gh issue edit, gh label, gh pr merge, gh release, GitHub lifecycle mutation, issue close, PR merge, label lifecycle, git, commit, push, tag, release, or generic lifecycle authority; terminal design-consensus suppression must not write spawn intent or dispatch ledger.
- verification: `test_phase9_router_open_state_gate.py`, `test_phase9_router_daemon.py`, `test_wakeup_plan.py`, `test_wakeup_runner.py`, `test_cli_command_router.py`, `test_runtime_exception_authorization_sources.py`, `test_skill_reference_anchors.py`
- no_new_runtime_authority: This mirror records only the #229 read-gh source-OPEN gate plus router-local prompt-source projection and the #330 narrowed direct spawn-intent allowlist; it does not grant daemon process-spawn, durable schema, host production SSOT, or lifecycle authority.

<a id="gh-usage-accounting-455"></a>
## gh-usage-accounting-455

- surface: `gh usage accounting`
- source_issue: `#455`
- source_round: `maintainer-directive`
- source_marker: `direct maintainer instruction: hijack all gh calls and count them`
- skill_anchor: `#gh-usage-accountingper-455`
- allowed: prepend checked-in `skills/codex-refactor-loop/scripts/ghwrap/gh` to controller, daemon, and codex worker `PATH`; set `CRND_GH_SOURCE` as `controller`, `daemon:<name>`, or `codex:<task_id>`; transparently delegate to the real `gh` after removing the shim directory from PATH; append bounded JSONL runtime rows to `.refactor-loop/state/gh-usage.jsonl` with schema fields `schema`, `ts`, `source`, `subcommand`, `pool`, `exit_code`, and `count`; `CRND_GH_USAGE_PATH` may only select a repo-relative or repo-contained JSONL path and otherwise falls back to `$REPO_ROOT/.refactor-loop/state/gh-usage.jsonl`; `CRND_GH_USAGE_MAX_LINES` may only lower the default retention bound and invalid, non-positive, or larger values fall back to the default; read aggregate stats through `consensus-rnd-cli gh-stats`.
- forbidden: no issue/PR/label lifecycle, no merge/close, no tag/release, no dispatch, no controller lifecycle authority, no host config edits, no GitHub request made only for measurement, no stdout/stderr/stdin capture that changes gh semantics, no accounting artifact outside `$REPO_ROOT`, and no blocking real gh when accounting fails.
- verification: `test_gh_accounting.py`, `test_cli_command_router.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This is observability-only accounting over existing `gh` calls; it does not authorize any new GitHub or git side effect.
