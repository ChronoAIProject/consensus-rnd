# Runtime Exception Authorization Mirror

This checked-in mirror preserves the durable authorization evidence for named
runtime exceptions whose original Phase 9 judge logs or maintainer directive
captures live under ignored `.refactor-loop/runs/` runtime output paths. It is
the only checked-in runtime authorization evidence mirror. It is not a runtime
API, loader, schema, or source of new authority. The executable contract remains
in `SKILL.md` and the tests.

<a id="consensus-gate-proof-579"></a>
## ConsensusGateProof(per #579)

- surface: `controller-private ConsensusGateProof`
- source_issue: `#579`
- source_round: `r2`
- source_marker: `META_JUDGE_DONE:consensus:minimal:ConsensusGateProof pure validator/anchor first, no same-round wakeup/effect runtime migration`
- skill_anchor: `#consensus-gate-proof`
- allowed: validate proof validity for substantive decision targets only with non-mechanical `target_kind`, stable `target_ref`, `target_digest`, `decision_producer_id`, `evidence[]` entries containing `producer_id`, `role`, `artifact`, `artifact_digest`, and `verdict`, `required_roles`, `verdict_rule`, and optional repo-relative `scope_paths`; reject single-worker self-certification, target digest mismatch, target kind/ref mismatch, artifact digest mismatch, missing required roles, duplicate roles, duplicate or overlapping producers, unsupported verdict rules, mechanical target kinds, missing required `scope_paths`, and recursive lifecycle or command fields; consuming helpers may use valid proof only as an admission fact and must still revalidate durable target digest, artifact digests, #191 owner, live state, and helper-specific preconditions.
- forbidden: no GitHub/git/file lifecycle authority, no route/post/label/spawn/merge/apply side effects, no public CLI, no wakeup-plan action projection, no wakeup-runner action, no IssueDecompositionPlan apply migration in this issue, no standalone wakeup-plan action projection, no standalone wakeup-runner action, no recursive gate for mechanical route/post/label/spawn/merge/apply-validated-proof actions, no proof-ticket/resume system, no command bus, and no `cmd`, `argv`, `shell`, `command_line`, `commands`, `env`, `git`, `gh`, `executor`, `lifecycle_authority`, `lifecycle_owner`, `args`, `controller_action`, `proof_ticket`, or `resume_ticket` fields.
- fact_source: proof JSON supplied by a helper integration plus the target artifact digest and artifact digests passed by that helper; the validator does not read GitHub, git, host.env, `.refactor-loop/` runtime ledgers, prompt bodies, or local process state.
- verification: `test_consensus_gate.py`, `test_runtime_exception_authorization_sources.py`, `test_skill_reference_anchors.py`
- no_new_runtime_authority: This mirror documents a controller-private pure validator only. Proof validity never grants lifecycle authority; later route/post/label/spawn/merge/apply-validated-proof integrations must add their own consensus, #191 active-controller owner gate, live state, and helper-specific preconditions.

<a id="maintainer-directive-concurrency-auto-topup"></a>
## maintainer-directive-concurrency-auto-topup

- source_kind: maintainer_directive
- surface: `concurrency_monitor auto-topup`
- source_date: `2026-05-26`
- source_evidence: maintainer loning said "改一下监控,低于预期数就继续派发", "关于并发问题直接自己改", and "你直接先把并发检测的问题解决掉, 现在总是空闲"; commit evidence `91cb381 feat(skill): concurrency_monitor 改 alert+auto-topup(maintainer 实证 #56 路径) (#57)` and `f2854db chore(skill): 扩 CLAUDE.md 例外子句覆盖 maintainer-directive equivalence 路径(#54 r2 共识) (#48)`.
- local_original_pointer: `.refactor-loop/runs/maintainer-directives/2026-05-26-concurrency-auto-topup.md`
- affected_contracts: `SKILL.md` named runtime exception for `concurrency_monitor auto-topup`; `concurrency` dispatch-queue top-up behavior.
- allowed_directive: only `skills/consensus-loop/scripts/concurrency_monitor.py` / packaged concurrency monitor deficit handling may add `_try_topup` plus the tick deficit branch to consume `.refactor-loop/dispatch-queue/<p0|p1|p2>/*.dispatch.json` and append `HARNESS_SPAWN_INTENT` with `command: "spawn-codex"` as a closed semantic enum; behavior/source tests and the SKILL narrow exception may document that existing controller-enqueued work is intent-dispatched when actual workers are below expectation.
- forbidden_boundary: no daemon direct `nohup spawn-codex`, no daemon executable command surface, no argv array, no shell command, no generic command bus, no issue/PR lifecycle, label lifecycle, commit, push, merge, tag, release, prompt-body decision, host fact invention, generic lifecycle actor, or authority outside the controller/actor-enqueued dispatch queue.
- verification: `test_concurrency_monitor.py`, `test_ensure_project_rules_fixed_points.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This entry mirrors the existing maintainer-directive equivalence evidence; it does not grant new dispatch authority beyond the checked-in narrow queue consumer contract.

<a id="maintainer-directive-progress-reporter-orphan-delete"></a>
## maintainer-directive-progress-reporter-orphan-delete

- source_kind: maintainer_directive
- surface: `codex-progress-reporter obsolete orphan delete retry`
- source_date: `2026-05-27`
- source_evidence: maintainer loning, 2026-05-27 wakeup, said "这个 bug 直接改吧" for issue #69 accumulated progress comments; commit evidence `897a670 fix(skill): progress-reporter orphan delete retry (issue #69 实证) (#73)`.
- local_original_pointer: `.refactor-loop/runs/maintainer-directives/2026-05-27-progress-reporter-orphan-delete.md`
- affected_contracts: #626 deletes recurring per-worker progress comments; `SKILL.md` keeps only the source-time `TEST_NO_LOOP` test seam and #504 fixed global status-card PATCH.
- allowed_directive: obsolete historical evidence only; it grants no recurring daemon delete path, no per-worker progress comment create/edit/delete/get/read path, and no own-comment maintenance authority.
- forbidden_boundary: no GitHub progress comment delete retry, no GitHub cleanup of existing spam through this runtime path, no per-worker issue/PR comment create/edit/read/delete, no production use of `TEST_NO_LOOP`, no new queue, lifecycle authority, host fact source, issue/PR lifecycle, label lifecycle, commit, push, merge, tag, or release authority.
- verification: `test_progress_reporter.py`, `test_codex_progress_reporter_orphan.py`, `test_ensure_project_rules_fixed_points.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This entry records that the old #69 carveout is deleted by #626 and grants no runtime GitHub write authority.

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
- durable_source: singleton JSON blob `active-controller.json` on `refs/heads/crnd/active-controller`; fields `owner_device`, `lease_id`, `acquired_at`, `expires_at`, `renewed_at`, `repo`, `reason`, `source_issue`; durable lease JSON must not contain `owner_login` or any GitHub username/login field.
- allowed: read/acquire/renew only the global active-controller lease. Lease-only git allowlist: `git fetch origin <lease-ref>`, `git ls-remote --exit-code --heads origin <lease-ref>`, `git rev-parse`, `git show <commit>:active-controller.json`, `git hash-object -w --stdin`, `git mktree`, `git commit-tree`, and `git push --force-with-lease=<old>:<lease-ref>`. These commands may only read/build/publish the singleton lease blob CAS and expose owner/expiry; gate restart-daemons, concurrency dispatch, phase9 router, patrol-inspector, comment/progress writes, dev-sync, and controller lifecycle helpers.
- forbidden: no worker diff commit, issue create/edit/close, PR create/edit/merge/close, label mutation, tag, release, per-work claim, host-defined lease scope, cross-device floor aggregation, daemon ownership matrix, active-active scheduler, generic distributed lock library, or generic lifecycle actor.
- metadata_only_193: issue/PR `author.login` and `updatedAt` are planning/routing/stale metadata only; they must not authorize side effects, per-work owner authority, claim/lease scope, stale takeover permit, or any issue/PR target write outside the #191 `ActiveControllerLease` / `require_active_controller(...)` gate. Same-repo multi-GitHub-user handling is HOLD-collapse: GitHub username, authenticated actor login, comment author, and issue author are display/admission/accounting/routing/status metadata only; they are forbidden as partition key, per-work claim, lease scope, daemon owner, takeover permit, lifecycle owner, or lifecycle authority. `GitHubAuthenticatedActor` may read the current authenticated GitHub API caller/token login, repo permission, and necessary GitHub server-side preflight facts such as branch protection/ruleset/CODEOWNERS/required-review results only after the #191 owner gate and before the first GitHub API mutation; those facts are fail-closed admission checks, not per-work owner, claim/lease scope, daemon owner, takeover permit, action-specific lifecycle authorization, generic lifecycle actor, or a bypass for #191/#238/#322/#396/#403. Its diagnostics-only helper may read `gh api user` and write rebuildable local status/snapshot fields such as `current_github_login` and `identity_authority="display-only"`; those fields must not enter durable lease state or executable action authority.
- verification: `test_active_controller_lease.py`, `test_restart_daemons.py`, `test_concurrency_monitor.py`, `test_phase9_router_package.py`, `test_comment_progress_active_controller.py`, `test_sync_dev.py`, `test_controller_actions.py`, `test_runtime_exception_authorization_sources.py`, `test_skill_reference_anchors.py`, `test_host_env_surface_matrix.py`
- no_new_runtime_authority: This is a singleton coordination gate only; it does not grant lifecycle authority and does not create per-work leases.

<a id="release-commits-producer-232"></a>
## release-commits-producer-232

- surface: `consensus-rnd-cli release-commits`
- source_issue: `#232`
- source_round: `r2-minimal`
- source_marker: `SOLVER_DONE:minimal:propose:lock minimal boundary to read-git pre-gate release-commits producer; keep release-gate consumer-only`
- skill_anchor: `#named-runtime-exception--autonomous-release-gateper-56`
- allowed: `read-git` and `write-artifact` only: read local git only to fetch tags, resolve the latest release tag, resolve the target ref, select a branch-reachable release since-anchor from a tag ancestor or mapped manifest version transition, and log that release range; atomically write `.refactor-loop/state/release-commits.json`.
- forbidden: no GitHub API, push, merge, reset, rebase, worktree mutation, tag, release, commit, issue lifecycle, PR lifecycle, label lifecycle, generic lifecycle authority, or inline execution from `release-gate`.
- fact_source: local git tags, target branch refs, `.version-bump.json`, and mapped manifest version fields.
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
- allowed: active-controller owner only after `ReleasePublishPreflight` validates `RELEASE_AUTO_ENABLE=true`, fresh `.refactor-loop/state/release-candidate.json`, fresh `.refactor-loop/state/release-decision.json`, matching `decision_digest`, matching `target_ref`, mapped manifest `from_version`, matching coordinate policy when present, mandatory `coordinate_policy.transition=beta_core_promotion` evidence for beta core promotion, and required checks green; first-run exact command allowlist is the controller-private detached release-publish transaction: `git fetch origin <INTEGRATION_BRANCH>`, `git rev-parse origin/<INTEGRATION_BRANCH>`, `git worktree add --detach <repo>/.worktrees/release-publish/<version>-<attempt> <fresh origin integration sha>`, then in that detached worktree `python3 .github/scripts/bump_version.py --version <to_version>`, `git add .version-bump.json <mapped manifests>`, `git commit -m "Release v<to_version>"`, `git rev-parse HEAD`, re-fetch and re-read `origin/<INTEGRATION_BRANCH>`, and only if the remote base still matches run `git push origin HEAD:refs/heads/<INTEGRATION_BRANCH>`, `gh api repos/<slug>/commits/<fresh release commit sha>/check-runs --paginate --slurp`, generate a controller-private release notes file from `.refactor-loop/state/release-commits.json`, and `gh release create v<to_version> --target <fresh release commit sha> --notes-file <controller-generated release notes file> [--prerelease]`; already-bumped reentry is allowed only when the only preflight mismatch is mapped manifests already equal `to_version` and `git show -s --format=%s HEAD` proves the HEAD subject is exactly `Release v<to_version>`; reentry may skip only `python3 .github/scripts/bump_version.py --version <to_version>`, `git add .version-bump.json <mapped manifests>`, and `git commit -m "Release v<to_version>"`, then must run `git rev-parse HEAD`, `gh api repos/<slug>/commits/<exact release/reentry commit sha>/check-runs --paginate --slurp`, generate a controller-private release notes file from `.refactor-loop/state/release-commits.json`, and `gh release create v<to_version> --target <exact release/reentry commit sha> --notes-file <controller-generated release notes file> [--prerelease]`; remote already-bumped reentry may read only remote integration/rollup release evidence, including `git for-each-ref --format=%(refname:short) refs/remotes/origin/rollup`, `git rev-parse origin/rollup/<40hex>`, `git show -s --format=%s origin/rollup/<40hex>`, and `git show origin/rollup/<40hex>:<mapped manifest>`, requires the rollup ref suffix equals the resolved commit sha, then uses `gh api repos/<slug>/commits/<exact release/reentry commit sha>/check-runs --paginate --slurp`, generates a controller-private release notes file from `.refactor-loop/state/release-commits.json`, and `gh release create v<to_version> --target <exact release/reentry commit sha> --notes-file <controller-generated release notes file> [--prerelease]`; pending/red/missing/API-fail fail closed before release notes generation and release creation; write `.refactor-loop/state/release-publish-result.json`.
- forbidden: no public `consensus-rnd-cli release-publish`, no public `consensus-rnd-cli publish-release`, no workflow tag/release creation, no current-checkout first-run release commit/push, no `git fetch origin HEAD`, no `git rev-list --count HEAD..origin/HEAD`, no `git push origin HEAD`, no `git tag`, no force-push, no `git merge`, no `git rebase`, no `git reset`, no GitHub Release edit/delete/upload, no approval-ticket/emoji gate, no proof-ticket/resume system, no issue lifecycle, PR lifecycle, label lifecycle, merge/close, or generic lifecycle actor.
- fact_source: `ReleasePublishPreflight` consumes `.refactor-loop/state/release-candidate.json`, `.refactor-loop/state/release-decision.json`, `.refactor-loop/host.env`, `.version-bump.json`, mapped manifest files, coordinate policy, and required check projections; `ReleasePublisher` consumes the approved preflight result plus either the detached transaction exact pushed release SHA, a local already-bumped SHA, or a read-only proven remote integration/rollup release SHA, and consumes `.refactor-loop/state/release-commits.json` only to generate the controller-private release notes file.
- verification: `test_release_publisher.py`, `test_release_notes.py`, `test_release_publish_preflight.py`, `test_cli_command_router.py`, `test_runtime_exception_authorization_sources.py`, `test_release_pipeline_contract.py`, `test_controller_actions.py`
- no_new_runtime_authority: This entry names and mirrors the existing controller-owned ReleasePublisher path; the notes file is a controller-private artifact generated after exact-SHA checks and before release creation, not a new public CLI, workflow release authority, tag authority, release edit/delete/upload authority, issue/PR/label lifecycle authority, or host production SSOT.

<a id="controller-release-publisher-334"></a>
## controller-release-publisher-334

<!-- Refactor (iter334/issue-334): Old pattern: ReleasePublisher could create a release tag at a fresh manifest-bump SHA before exact-SHA checks were green. New principle: mirror the direct exact-SHA check gate after safe push and before release creation. -->
- surface: `controller-owned release publisher`
- source_issue: `#334`
- source_round: `r5`
- source_marker: `META_JUDGE_DONE:converge:round-4:decide`
- skill_anchor: `#release-pipeline-integrationpost-61`
- allowed: active-controller owner only; read release candidate/decision artifacts, run `ReleasePublishPreflight`, bump mapped manifests and commit/push the release manifest commit through the detached origin-tip transaction, or use already-bumped reentry only after `git show -s --format=%s HEAD` proves the exact release subject, or use read-only proven remote integration/rollup release SHA including `origin/rollup/<40hex>` only when the suffix equals the resolved commit sha; read exact-SHA Checks API for the exact release/reentry commit sha, create tag/release only after that exact fresh SHA is green, write `.refactor-loop/state/release-publish-result.json`.
- forbidden: no worker diff commit, arbitrary branch push, issue/PR lifecycle, label mutation, workflow tag/release, public CLI release-publish, tag target without exact-SHA green checks, proof-ticket/resume system, release edit/delete/upload, merge/close, or generic lifecycle actor.
- fact_source: release candidate/decision artifacts + mapped manifests + exact detached transaction SHA or local already-bumped SHA or read-only proven remote integration/rollup release SHA + Checks API projection, with coordinate policy validated by #322 preflight when applicable.
- verification: `test_release_publisher.py`, `test_release_pipeline_contract.py`, `test_runtime_exception_authorization_sources.py`, `test_skill_reference_anchors.py`
- no_new_runtime_authority: mirror only, not a runtime API/loader/schema/proof ticket/authorization source beyond the existing controller-owned publisher boundary.

<a id="closed-label-reconciler-238"></a>
## closed-label-reconciler-238

- surface: `consensus-rnd-cli closed-label-reconciler`
- source_issue: `#238`
- source_round: `r3`
- source_marker: `META_JUDGE_DONE:consensus:closed-label-reconciler:option B named restart-managed closed-label-reconciler with crnd:phase:closed and gh-label-closed-reconcile authority`
- skill_anchor: `#named-runtime-exception--closed-label-reconcilerper-238`
- allowed: active-controller owner only; read CLOSED `crnd:lifecycle:managed` issue/PR labels through a bounded GitHub label/state driven dirty candidate projection whose every GitHub list query uses a managed-label predicate before any dirty-label search predicate, and apply terminal phase-label reconciliation by removing phase labels, cleanup-only aliases, and `crnd:lifecycle:stuck`, then adding exactly one terminal phase label, either `crnd:phase:merged` when merged evidence is present or `crnd:phase:closed` when evidence is insufficient.
- forbidden: no open item mutation, issue create/close/reopen/body/title edit, PR create/merge/close/body/title edit, human label mutation, triage label mutation, milestone label mutation, lifecycle label mutation beyond removing `crnd:lifecycle:stuck`, tag, release, generic `gh-label`, generic `gh-edit`, controller close-path inline reconcile, or generic lifecycle actor. Human-label exactness neither authorizes human-label mutation nor blocks phase/cleanup/stuck reconciliation; human labels are preserved as-is.
- fact_source: GitHub CLOSED item state plus live labels, normalized by `closed_phase_labels.py`; candidate collection is GitHub label/state driven and managed-intersecting at query construction for missing terminal phase, residual nonterminal phase, cleanup-only alias, `crnd:lifecycle:stuck`, and a small recent closed read-only managed window; terminal-complete closed managed items are excluded from steady-state scans and must not receive steady-state per-item view or linked-merge probes; unmanaged CLOSED search noise must not be returned to the reconciler or `peek` lens. `crnd:phase:closed` is a protocol terminal state, not a product verdict.
- verification: `test_closed_label_reconciler.py`, `test_peek_status_lens.py`, `test_gh_accounting.py`, `test_cli_command_router.py`, `test_restart_daemons.py`, `test_label_taxonomy.py`, `test_label_contract_source.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This mirror documents only the #238 closed managed item terminal phase-label reconciliation carveout and does not grant generic GitHub label/edit or lifecycle authority.

<a id="default-issue-intake-claim-623"></a>
## default-issue-intake-claim-623

- surface: `DefaultIssueIntakeClaim`
- source_issue: `#623`
- source_round: `r5`
- source_marker: `META_JUDGE_DONE:consensus:hybrid-default-issue-intake`
- skill_anchor: `#named-runtime-exception---default-issue-intake-claimper-623`
- allowed: active-controller owner only; only when `DEFAULT_ISSUE_INTAKE_ENABLE` is not false-like; consume #396 `wakeup-plan` closed projection with `controller_action="apply_default_issue_intake_claim"`; read live OPEN non-PR issue state and issue comments; parse `crnd:default-issue-intake-claim`; use earliest valid comment `createdAt` plus `author.login` only as first-claimer admission/accounting fact; when no earlier claim exists, current authenticated actor may write the claim comment and apply `labels.design_issue_label_bundle()`; when an earlier other actor claim exists, write only `crnd:default-issue-intake-stop`.
- forbidden: no `UNMANAGED_ISSUE_INTAKE_ENABLE`, `UnmanagedIssueIntakeClaim`, `intake_unmanaged_issue_claim`, `crnd:unmanaged-issue-intake-*`, public lifecycle CLI, new daemon, generic issue factory, issue/PR close/reopen/body/title edit, PR edit/merge, assignee, milestone, tag/release, commit/push, generic label mutation, takeover permit, per-work claim, lifecycle owner, lifecycle authority, daemon owner, #191 lease owner, prompt-body authorization, GitHub username authority outside this admission/accounting protocol, or any GitHub side effect that bypasses #191 and #396.
- fact_source: live GitHub issue state, live issue comments carrying `crnd:default-issue-intake-claim`, current authenticated actor admission after #191 owner gate, `DEFAULT_ISSUE_INTAKE_ENABLE`, and the checked-in label catalog `labels.design_issue_label_bundle()`.
- verification: `test_default_issue_intake.py`, `test_wakeup_plan.py`, `test_wakeup_runner.py`, `test_host_env_surface_matrix.py`, `test_runtime_exception_authorization_sources.py`, `test_skill_reference_anchors.py`
- no_new_runtime_authority: This mirror anchors only the #623 default issue intake claim helper and named #396 action. It adds no public CLI, daemon, generic issue factory, unmanaged alias protocol, lifecycle owner, or authority beyond the claim/stop comment protocol plus existing design label bundle.

<a id="wakeup-runner-396"></a>
## wakeup-runner-396

- surface: `consensus-rnd-cli wakeup-runner`
- source_issue: `#396`
- source_round: `r3 structural`
- source_marker: `META_JUDGE_DONE:consensus:structural:wakeup-plan-closed-action-projection-plus-wakeup-runner-396`
- skill_anchor: `#named-runtime-exception---wakeup-runnerper-396`
- allowed: active-controller owner only; consume only `wakeup-plan` evidence-bound closed action projection with top-level `mode: "closed-action-projection"`, `no_lifecycle_authority: true`, and `apply_authority: "wakeup-runner-396-only"`; `safe_progress_scheduler.py` is the sole risk admission owner between `wakeup-plan` / dispatch queue metadata and executable `wakeup-runner` actions; the effect-adapter boundary is only the owner-local admission contract before existing #396 controller actions/named helpers and the #403 `ControllerActions.apply_issue_decomposition_plan()` helper, not a generic effect-adapter runtime abstraction; each executable action must carry `runner_authority: "wakeup-runner-396"`, `preconditions`, `source_marker` or `source_artifact`, `target_kind`, `target_number`, `target`, concrete `controller_action`, `no_generic_command: true`, and safe-progress admission metadata; medium risk actions must carry `risk_tier: "medium"` plus `execution_policy: "cautious"` and are bounded per tick, while high/unsafe actions are excluded from executable actions and written only to `.refactor-loop/state/safe-progress-blocked-queue.json`; effects are allowed only by concrete `controller_action` or helper name; revalidate durable artifact evidence, clean `EXIT=0` source marker, ConsensusGate/meta-judge or review truth table `reject==0 && approve>=1 && all required reviewers present && all required reviewer heads equal live PR head`, target-required PR merge-readiness checks, OPEN/live GitHub state, missing/stale per-reviewer head SHA, #191 owner, release #322 preflight, safe-progress admission metadata, or helper-specific precondition; ordinary rejection uses grep-able one-line runner diagnostics such as `WAKEUP_RUNNER_BLOCKED:<action_id>:<reason>` plus ledger/pending-event reason; multi-step external side effects require helper-owned durable result/diagnostic artifacts when that helper owns such a surface or partial external state can exist; `.refactor-loop/host.env` may be skill-private runtime/cache/log read state only, not branch topology, machine paths, durable ledger authority, host artifact, or host production SSOT; `dispatch_consensus_implementation` only moves the issue to implementing phase, creates the canonical implementation worktree/head, renders the implement prompt, and spawns the implement worker; it does not commit, push, or open a PR; after validating a real scoped diff, committing it, restoring the integration base, running build/test checks, and safe-pushing the canonical head, `publish_implementation_output` updates an existing matching open managed PR when exactly one exists, or opens exactly one managed implementation PR and verifies it when zero matching PRs exist; duplicate/multiple PRs, head/base/link mismatch, unmanaged PRs, invalid worker-authored PR artifacts, or stale-base clean output fails closed/status-only; `wakeup-plan` action `head_sha` is not reviewer-head authority; raw PR-head check buckets or advisory check buckets are display-only diagnostics, not merge/fix lifecycle authority; mechanically call existing controller helpers or #396 narrow helpers for spawn codex, including allowlisted `release-rollup-body` generation that only writes `.refactor-loop/runs/release-rollup-pr-body.md`, named helper `dispatch_consensus_implementation`, named helper `publish_implementation_output`, named helper `archive_invalid_harness_spawn_intent` that only appends a local invalid-intent digest archive marker, named helper `apply_issue_decomposition_plan`, named helper `apply_default_issue_intake_claim`, then named helper `open_release_rollup_pr_from_action` after the body exists, publish worker output, dispatch reviewers/fix/remote-ci worker only for target-required failed checks with `target_required_checks_red`, apply triage decision, merge PR under review truth table plus target-required readiness, close managed item from drop marker, and publish release through #322.
- forbidden: no arbitrary git/gh command, workflow tag/release, router guard adjudication, generic codex fallback, label/merge/close outside existing helper or named #396 helper, prompt-body decision, standalone authorization from `wakeup-plan`, no generic effect-adapter runtime abstraction, no public command bus, no executor layer; the fixed forbidden field set is at least `cmd`, `argv`, `shell`, `command_line`, `commands`, `env`, `git`, `gh`, `executor`, `lifecycle_authority`, and `lifecycle_owner`, with existing extra `args` rejection retained; no generic command fields, new lifecycle authority, `ControllerTurnDecision`, controller-turn worker, private schema, active-active scheduler, `.refactor-loop/host.env` as host production SSOT, or generic lifecycle actor.
- fact_source: `wakeup-plan` closed action projection is the only action projection fact source but not a standalone authorization source; safe-progress classification is the owner-local risk admission fact source only for low/medium/high routing, not final side-effect authorization; action `head_sha` cannot substitute for reviewer-head authority; final side-effect permit comes from #191 active-controller owner plus source marker/artifact, review truth table, all required reviewer heads equal live PR head, target branch required-check facts plus live PR head check-runs classified into target-required and advisory buckets, live GitHub state, release #322 preflight, or helper-specific validator.
- verification: `test_wakeup_runner.py`, `test_wakeup_runner_review_gate.py`, `test_wakeup_runner_release.py`, `test_wakeup_plan.py`, `test_safe_progress_scheduler.py`, `test_cli_command_router.py`, `test_runtime_exception_authorization_sources.py`, `test_restart_daemons.py`, `test_skill_reference_anchors.py`
- no_new_runtime_authority: This names only the #396 unattended runner carveout; it adds no public generic lifecycle CLI, no command bus, no controller-turn worker, and no authorization beyond checked-in closed projection validation plus existing helpers.

<a id="repository-stalled-meta-reflector-506"></a>
## repository-stalled-meta-reflector-506

- surface: `wakeup-plan spawn-only repository stalled reflector`
- source_issue: `#506`
- source_round: `r4`
- source_marker: `META_JUDGE_DONE:consensus:minimal-structural-converged:v1仓库级只读reflector+wakeup-plan spawn-only;无meta_escalation.py无根CLAUDE carveout`
- skill_anchor: `#wakeup-skeleton`
- allowed: active-controller owner only; after `wakeup-plan` reads live open managed issue/PR metadata and `updatedAt` ages exceed effective `META_ESCALATION_STUCK_HOURS=max(meta, STALE_REVIVAL_HOURS)`, project at most one `spawn_codex_harness_background` action for the checked-in `meta-reflector-repository-stalled.md` prompt, with `no_lifecycle_authority: true`, `no_generic_command: true`, prompt/log paths, open-target summary, and recommendation-only preconditions; the evaluator may write `.refactor-loop/runs/meta-escalation/` recommendation artifacts.
- forbidden: no standalone lifecycle or escalation system, no direct decompose, no direct `IssueDecompositionPlan` apply outside the #396 named action, no private `kind="issue-decomposition-apply"` dialect, no close, merge, label, issue/PR create/edit/reopen, commit, push, tag, release, git, gh, cmd, argv, shell, commands, env, executor, lifecycle_authority, lifecycle_owner, prompt-body apply decision, command bus, public issue factory, or generic lifecycle actor.
- fact_source: `wakeup-plan` consumes GitHub open managed issue/PR metadata plus `META_ESCALATION_STUCK_HOURS` / `STALE_REVIVAL_HOURS`; recommendation artifacts are advisory only and are not side-effect authorization. Handoffs must go through existing design-consensus, #403 validated `IssueDecompositionPlan`, normal narrow-fix/review gate, or #396 clean `META_RESOLVED:drop` close path.
- verification: `test_wakeup_plan.py`, `test_marker_emission_contract.py`, `test_host_env_surface_matrix.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This mirror anchors #506 v1 as read-only, spawn-only, and recommendation-only; it adds no root CLAUDE lifecycle carveout, no public CLI, no validator module, and no lifecycle authority beyond existing #396 spawn projection plus existing downstream gates.

<a id="task-spawn-claim-490"></a>
## task-spawn-claim-490

- surface: `consensus-rnd-cli spawn-codex` / `spawn.py` local task-spawn claim
- source_issue: `#490`
- source_round: `r4 structural`
- source_marker: `META_JUDGE_DONE:consensus:structural:TaskSpawnClaimStore at spawn-codex with SPAWN_CLAIM_HELD and skill-local governance`
- skill_anchor: `#task-spawn-claim-490`
- allowed: same-device per-codex-task mutual exclusion only; before `ProcessSupervisor.supervise(...)`, `TaskSpawnClaimStore.acquire(...)` creates `.refactor-loop/locks/spawn-tasks/<safe-task-id>.lock` using `O_CREAT|O_EXCL`; only successful acquisition may call the supervisor; a live conflict prints `SPAWN_CLAIM_HELD:task=<task_id> lock=<lock_path>` and returns 0 skip/noop; matching completed-log locks may be recycled only when metadata matches the task/log path and the log has an `EXIT=` marker.
- forbidden: no upstream read-lock preflight, no standalone authorization from the lock artifact, no cross-device per-work claim, no lifecycle authority, no host-defined lease scope, no generic distributed lock, no `ActiveControllerLease` replacement, no host production SSOT, no issue/PR lifecycle, no label mutation, no commit, push, merge, tag, release, or generic lifecycle actor.
- fact_source: `skills/consensus-loop/scripts/codex_refactor_loop/task_spawn_claim.py` owns safe task-id validation, lock path, atomic create, metadata validation, and recycle policy; `spawn.py` is the only long-term enforcement caller before `ProcessSupervisor.supervise(...)`.
- verification: `test_task_spawn_claim.py`, `test_spawn_claim.py`, `test_spawn_supervisor.py`, `test_runtime_exception_authorization_sources.py`, `test_skill_reference_anchors.py`
- no_new_runtime_authority: This is local spawn mutual exclusion only and does not grant lifecycle authority or distributed ownership.

<a id="global-dashboard-status-card-504"></a>
## global-dashboard-status-card-504

- surface: `global-dashboard-status-card`
- source_issue: `#504`
- source_round: `r2 structural`
- source_marker: `META_JUDGE_DONE:consensus:structural`
- skill_anchor: `#named-runtime-exception---global-dashboard-status-cardper-504`
- allowed: active-controller owner only; collect the shared read-only `HolisticStatusProjection` from existing status producers; let local `consensus-rnd-cli holistic-status` render the full card and `peek` reuse only the summary renderer; when `$HOST_HOLISTIC_STATUS_ENABLE=true`, `$HOST_HOLISTIC_STATUS_ISSUE_NUMBER`, and `$HOST_HOLISTIC_STATUS_COMMENT_ID` are valid, the existing `progress-reporter` tick may, after GraphQL headroom, #191 owner, interval, and same-hash gates, PATCH exactly one host-configured issue comment id with the rendered status card.
- forbidden: no new daemon, no public writer CLI, no create comment, no issue body edit, no PR body/title edit, no Discussions, no label mutation, no create/close/reopen/merge, no tag/release, no git, no generic GitHub writer, no prompt-body/prose decision reads, no multi-carrier grammar, no standalone dashboard truth source, no standalone dependency truth source, and no lifecycle authority.
- fact_source: `HolisticStatusProjection` in `holistic_status.py` is the single status-card algorithm; it reads existing statusline snapshot, daemon-status projection, concurrency count/list surfaces, dispatch queue depth, managed issue/PR label/body closing-ref projection through `ManagedWorkProjection`, recent merge state, GitHub headroom, and progress-reporter state. Host opt-in and fixed target fields come only from the host env surface matrix.
- verification: `test_holistic_status.py`, `test_work_item_projection.py`, `test_progress_reporter.py`, `test_cli_command_router.py`, `test_peek_status_lens.py`, `test_host_env_surface_matrix.py`, `test_runtime_exception_authorization_sources.py`, `test_skill_reference_anchors.py`
- no_new_runtime_authority: This is a private progress-reporter issue-comment PATCH subpath plus a read-only local command; it grants no new daemon, public writer command, lifecycle authority, or dashboard/dependency ledger authority.

<a id="patrol-inspector-issue-intake-541"></a>
## patrol-inspector-issue-intake-541

- surface: `patrol-inspector issue-intake`
- source_issue: `#541`
- source_round: `r2 structural`
- source_marker: `META_JUDGE_DONE:consensus:structural`
- skill_anchor: `#named-runtime-exception---patrol-inspector-issue-intakeper-541`
- allowed: active-controller owner only; checked-in `patrol-inspector` runs only after `$PATROL_INSPECTOR_ENABLE=true`; read worker terminal failure envelopes from local logs, runs artifacts, wakeup-plan/peek projections, and GitHub managed item snapshot; generate patrol-private `PatrolCandidateSignal`; raw log prose is diagnostic text, not an issue-intake fact source, and may be used only as codex prompt context; require structured codex `PatrolAnalysisDecision` with `is_real_issue=true` before generating publishable `PatrolFinding`; public issue bodies may use only analysis fields such as summary, root cause, recommendation, rationale, severity, source, kind, and fingerprint metadata; create or update patrol-owned managed design issues by durable fingerprint; create may attach only the fixed patrol/design-intake label bundle (`crnd:lifecycle:managed`, `crnd:phase:design-solving`, `crnd:human:auto`, `crnd:triage:pending`); update may edit only the patrol issue body; write `.refactor-loop/state/patrol-inspector.json` as cache and #504 dashboard input only.
- forbidden: no modification of non-patrol issues or PRs, no close/reopen/merge, no PR edit, no label mutation outside the create-time fixed bundle, no commit, push, tag, release, no public inspector CLI, no second dashboard/comment writer, #396 `wakeup-plan` issue-create action, #506 issue factory, generic GitHub writer, no generic issue factory, or no lifecycle actor.
- fact_source: `PatrolAnalysisDecision.is_real_issue=true` is the publishability gate; `PatrolFinding.fingerprint` and the patrol fingerprint line in GitHub issue title/body are the idempotency fact source; raw logs, run artifacts, prompt context, and `.refactor-loop/state/patrol-inspector.json` are diagnostic/cache/dashboard inputs only and not authorization.
- verification: `test_patrol_inspector.py`, `test_patrol_fingerprint.py`, `test_patrol_issue_publisher.py`, `test_patrol_authority.py`, `test_restart_daemons.py`, `test_host_env_surface_matrix.py`, `test_holistic_status.py`, `test_progress_reporter.py`, `test_runtime_exception_authorization_sources.py`, `test_skill_reference_anchors.py`, `test_cli_command_router.py`
- no_new_runtime_authority: This is the only patrol issue-intake carveout and does not create a generic issue factory, dashboard writer, lifecycle actor, or new source of host production truth.

<a id="issue-decomposition-403"></a>
## issue-decomposition-403

- surface: `IssueDecompositionPlan active-controller apply helper`
- source_issue: `#403`
- source_round: `r6 structural`
- source_marker: `META_JUDGE_DONE:consensus:structural:wakeup_plan.py零行为改动-IssueDecompositionPlan发现链路用pending-event-completed-marker-peek`
- skill_anchor: `#large-issue-decomposition`
- allowed: active-controller owner only; the #403 admission boundary is the existing `ControllerActions.apply_issue_decomposition_plan()` helper's private validation gate, not a second apply schema, public command bus, executor layer, or generic effect-adapter runtime abstraction; consume a validated controller-private `IssueDecompositionPlan` with exactly `{schema, parent_issue, source_consensus_artifact, children:[{slug,title,scope,non_goals,body_artifact_path}], parent_update:{comment_artifact_path}}`; when invoked through #396, `wakeup-runner` must revalidate clean plan-level judge source marker, `plan_level_design_consensus_judge_artifact`, plan digest/proof, live parent open/tracking, forbidden fields, and exact helper-owned parent tracking grammar; create only missing `crnd:lifecycle:managed` child design issues with the catalog design issue label bundle, using helper-owned child fingerprints derived from parent issue, plan digest, and child slug; post a consolidated tracking comment to the parent issue.
- forbidden: no daemon/worker issue creation, no public issue factory, no public CLI command, no wakeup-plan decompose projection except the #396 evidence-bound named `controller_action="apply_issue_decomposition_plan"`, no prompt-body decision, no second #403 apply schema, no generic effect-adapter runtime abstraction, no public command bus, no executor layer, no arbitrary git/gh command, no parent issue close/reopen/body-title edit, no assignee, no milestone, no label lifecycle beyond child design issue catalog labels, no lifecycle_owner/lifecycle_authority/cmd/argv/args/shell/command_line/commands/env/gh/git/executor/close fields, no absolute or escaping artifact paths, and no generic lifecycle actor.
- fact_source: plan-level design-consensus judge artifact structured fields plus checked-in `IssueDecompositionPlan` JSON/Markdown body artifacts validated by `issue_decomposition.py`; #396 closed action projection fields are derived only from that clean plan-level judge artifact's `controller_action="apply_issue_decomposition_plan"`, `plan_level_design_consensus_judge_artifact`, plan path, digest, and proof fields, not the first `META_JUDGE_DONE:consensus:decompose`, solver artifacts, prompt-body free text, validator output, worker output, or `.refactor-loop/host.env`; helper-owned child fingerprints and exact parent tracking comments are the durable idempotency fact sources, while loose sentinel-looking prose is ignored.
- verification: `test_issue_decomposition.py`, `test_controller_actions.py`, `test_phase9_router_daemon.py`, `test_wakeup_plan.py`, `test_skill_reference_anchors.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This grants only the #403 active-controller checked-in apply helper plus the #396 named action pathway; it adds no daemon owner, public CLI, private action dialect, issue factory, or parent lifecycle mutation.

<a id="update-check-231"></a>
## update-check-231

- surface: `consensus-rnd-cli update-check`
- source_issue: `#231`
- source_round: `r4 structural`
- source_marker: `META_JUDGE_DONE:consensus:B-structural-profile:notify-only-update-check-with-version-manifest-snapshot-projection-shared-semver`
- skill_anchor: `#notify-only-update-checkper-231`
- allowed: read checked-in `skills/consensus-loop/VERSION.json`; read GitHub latest release and tags for the repository named in that manifest; atomically write `.refactor-loop/state/update-check.json`; let `restart-daemons` call the probe after the fixed daemon start/skip pass; let `concurrency` project fresh positive update fields into `statusline-snapshot.json`.
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
- allowed: integration publish/write authority remains only in the dedicated integration worktree; write integration sync operation artifacts there; run `git ls-remote --exit-code --heads origin $INTEGRATION_BRANCH`; use the existing narrow integration-branch git allowlist through daemon-owned execution in that worktree: `git fetch`, `git ls-remote --exit-code --heads origin $INTEGRATION_BRANCH`, `rev-list`, `rev-parse`, `merge-base`, `reset --hard`, daemon-owned `rebase --rebase-merges` for `adopt-merged-rollup`, resolved `git rebase --continue` only for `continue-resolved-rollup-adoption-rebase` with adoption artifact evidence, replay-integrity verification, and force-with-lease push, `merge --ff-only|--no-ff`, `git push HEAD:$INTEGRATION_BRANCH`, and force-with-lease rollup adoption; active-controller owner may reuse the narrowed checked-in `ControllerActions.safe_sync_main()` for main checkout branch==`$INTEGRATION_BRANCH`, tracked-clean, no in-progress git operation, remote-only-ahead `git merge --ff-only origin/$INTEGRATION_BRANCH` follow.
- forbidden: no generic rebase, no rebase abort/cleanup, no main checkout local-ahead push, rebase, reset, no-ff merge, force-push, branch create/delete, worker-diff commit, use of main checkout HEAD as publish authorization, PR create, merge, close, or edit, issue lifecycle, label lifecycle, tag, release, generic lifecycle actor, or git command outside the #53 allowlist.
- verification: `test_sync_dev.py`, `test_sync_operations_executor.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This mirror only replaces the missing ignored judge-log authorization path.

<a id="observability-comment-writers-53"></a>
## observability-comment-writers-53

- surface: `observability-comment-writers`
- source_issue: `#53`
- source_round: `r7`
- source_marker: `META_JUDGE_DONE:consensus`
- skill_anchor: `#named-runtime-exception--observability-comment-writersper-53`
- allowed: GitHub issue or PR comments for controller status banners, PR body edit, and reactions only; progress-reporter per-worker progress comments are deleted and #504 separately allows only the fixed global status-card PATCH. Issue/PR target writes still require the #191 `ActiveControllerLease` / `require_active_controller(...)` gate, and this mirror is not a cross-device write permit.
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
- allowed: restart helper owns singleton wrappers, actor-owned heartbeat leases, helper-private launch fingerprints at `.refactor-loop/locks/<daemon>.fingerprint.json`, and helper-private `DaemonProcessInventory` for the existing static daemon allowlist; each wrapper may self-heal only its own static-allowlist child on child exit or actor heartbeat failure by reading the actor-owned heartbeat, logging one reason line, terminating that child with SIGTERM/grace/SIGKILL when needed, and restarting the same resolved command after at least one wrapper poll interval. Malformed/future actor heartbeat fails closed immediately; missing/stale numeric heartbeat is measured against the current child generation spawn age and may terminate/restart only after one heartbeat freshness window. Wrappers do not write heartbeat and do not reload fingerprint or host.env. Cron/launchd remains mandatory for wrapper death, boot/post-wake, fingerprint/host.env reload, duplicate cleanup, and #191 owner refresh. pid alive plus fresh heartbeat plus current fingerprint plus zero duplicate canonical live wrapper for the same repo_root plus daemon name plus restart wrapper shape plus same resolved static allowlist command is the only skip condition, missing, malformed, or mismatched fingerprint data fails closed, and missing/malformed/mismatch fingerprint data or duplicate canonical wrappers fail closed to reap every same-repo same-daemon wrapper before one fresh restart; runs canonical RuntimeRetention before daemon freshness checks. `consensus-rnd-cli daemon-status --json` is a read-only daemon-status projection over the same static allowlist, pid/heartbeat/fingerprint readers, cached active-controller status, and `DaemonProcessInventory`, including stale reason/age and duplicate count from the same wrapper matcher; repair/reload remains restart-daemons.
- forbidden: no host-defined daemon registry, generic process supervisor, GitHub/git lifecycle authority, codex spawn, commit, push, merge, label, archive, index, new daemon, issue lifecycle, PR lifecycle, tag, release, wrapper sidecar heartbeat writer, public start/stop/restart/reload lifecycle verb, or generic lifecycle authority.
- verification: `test_restart_daemons.py`, `test_anti_stop_restart_helper_contract.py`, `test_cli_command_router.py`, `test_runtime_retention.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This mirror only replaces the missing ignored judge-log authorization path.

<a id="controller-tick-supervisor-553"></a>
## ControllerTickSupervisor(per #553)

- surface: `ControllerTickSupervisor`
- source_issue: `#553`
- source_round: `r1`
- source_marker: `META_JUDGE_DONE:consensus:structural:shared-read-projection-then-narrow-tick-supervisor-with-key-only-queue-and-legacy-mode-guard`
- skill_anchor: `#controller-tick-supervisor-553`
- allowed: `collect_shared_controller_projection()` is the only shared informer read entrypoint. `SharedControllerProjection` and `ProjectionRequest` are rebuildable typed read-only projections over owner-local fact sources such as `ManagedWorkSnapshot`, daemon-status, statusline snapshot, and key-only workqueue keys. `SharedControllerProjection.to_json()` additively exposes a top-level diagnostic `freshness` object with `generated_at`, `sources`, `overall_loaded_ok`, `failed_source_count`, and `stale_source_count`; every source entry carries at least `source`, `loaded_ok`, `reason`, `age_seconds`, and `next_retry_after_seconds`. `ControllerTickSupervisor` may schedule only named `TickHandler` implementations from `TickWorkItem(handler,key)` pairs after `LegacyDaemonModeGuard` proves the same target is not also owned by a legacy restart-helper-managed daemon. Each migrated handler has a supervisor-local `TickHandlerContract` naming only handler identity, required projection sources/freshness, delegated existing helper, replaced legacy daemon target, and net-deletion target. Handler `backoff`, `blocked`, and `noop` results are returned and logged as diagnostics only. `$CONTROLLER_TICK_SUPERVISOR_ENABLE=true` is a first migration opt-in that lets `restart-daemons` include the supervisor target, mechanically excludes the migrated `comment-monitor` legacy daemon target from active restart inventory, and keep the canonical legacy daemon list as the static compatibility list. The `comment-monitor` handler delegates to existing `run_comment_monitor_reconcile_tick()` / `CommentMonitor.tick()` and requires fresh `managed_work_snapshot` before executing.
- forbidden: no `ControllerProjectionInformer`, no second public shared projection read surface, no freshness public or parsed read-model authority, no generic executor, no argv/shell/cmd/command_line/commands/env/git/gh/owner/pending_events_authority/lifecycle_authority/lifecycle_owner payload, no pending-events authority movement, no host production SSOT, no issue/PR lifecycle, no label mutation, no commit, no push, no merge, no tag, no release, no public lifecycle verb, no phase9-router migration, no dev-sync migration in the first pass, and no write side-effect authorization movement; existing helpers must re-check #191/#396/#238/#322/#403/#437/#504 or #53 gates.
- verification: `test_shared_controller_projection.py`, `test_controller_tick_supervisor.py`, `test_workqueue.py`, `test_restart_daemons.py`, `test_runtime_exception_authorization_sources.py`, `test_skill_reference_anchors.py`
- no_new_runtime_authority: This mirror documents an opt-in migration building block only. It does not authorize lifecycle mutations or make `.refactor-loop/` a host production fact source.

<a id="runtime-retention-437"></a>
## runtime-retention-437

- surface: `consensus-rnd-cli runtime-retention`
- source_issue: `#437`
- source_round: `r4`
- source_marker: `META_JUDGE_DONE:consensus:structural:choose canonical runtime-retention owner and #437 narrow local-GC carveout`
- skill_anchor: `#named-runtime-exception--runtime-retentionper-437`
- allowed: active-controller owner only; host opt-in is `$RUNTIME_RETENTION_ENABLE=true`; `RuntimeRetention` is the only canonical owner and `consensus-rnd-cli runtime-retention` is the only public command for this surface; produce the owner-local `generated_files` slice by scanning only `$REPO_ROOT/.refactor-loop/{logs,prompts,runs}` for old generated regular non-symlink files, atomically merge that slice into `.refactor-loop/state/runtime-retention-plan.json`, and consume the same plan as the sole planner proof; additive `generated_files` entries may delete only TTL-expired repo-relative regular non-symlink files under `$REPO_ROOT/.refactor-loop/{logs,prompts,runs}` after executor revalidation proves planner eligibility, `generated_file`, `ttl_expired`, `no_in_flight`, `no_open_actionable`, `no_pending_intent`, `no_unconsumed_marker`, and `no_recovery_surface`; missing, malformed, ambiguous, young, open-target, in-flight, symlink/FIFO, path-escape, pending-reference, markerless recovery, or unproven files are kept fail-closed; same-inode compact `$REPO_ROOT/.refactor-loop/.controller-pending-events.log`; preserve and consume the same plan as the planner proof for stale `$REPO_ROOT/.worktrees/<name>` entries with `no_in_flight`, `no_open_issue_or_pr`, `no_dirty`, `no_local_ahead`, and `merged_or_missing_safe`, recheck local git read projections, then run only `git worktree remove <path>` and `git worktree prune`.
- forbidden: no GitHub write, no issue/PR create/edit/close/merge, no label mutation, no tag/release, no `git fetch`, no branch deletion, no commit, no push, no reset, no rebase, no merge, no tag, no archive/index durable fact source, no host config edit, no `.refactor-loop/host.env` as host production SSOT, no generated-file or worktree cleanup without planner proof, no public lifecycle command, no compatibility alias, no daemon ownership expansion, and no generic lifecycle actor.
- verification: `test_runtime_retention.py`, `test_cli_command_router.py`, `test_restart_daemons.py`, `test_runtime_exception_authorization_sources.py`, `test_skill_reference_anchors.py`, `test_anti_stop_restart_helper_contract.py`
- no_new_runtime_authority: This mirror narrows local GC to RuntimeRetention only; it does not authorize GitHub lifecycle, git branch lifecycle, release lifecycle, generic cleanup, host production config ownership, or a second retention owner.

<a id="phase9-router-open-state-gate-229"></a>
## phase9-router-open-state-gate-229

- surface: `consensus-rnd-cli phase9-router`
- source_issue: `#229`
- source_round: `phase9-router source-OPEN gate`
- source_marker: `phase9-router source issue state gate`
- skill_anchor: `#consensus-rnd-phase-design-consensus-router-daemon-command-body`
- allowed: read `ManagedWorkSnapshot` open managed projection, clean-exit logs, and the private router ledger; `ManagedWorkSnapshot` is the skill-private read-only owner for open managed work discovery and may write `.refactor-loop/state/managed-work-snapshot.json` under `.refactor-loop/locks/managed-work-snapshot.lock`, use `MANAGED_WORK_SNAPSHOT_TTL_SECONDS=300` and `MANAGED_WORK_SNAPSHOT_STALE_MAX_SECONDS=900`, reuses `github_budget.py`, query GitHub GraphQL with search shape `repo:<slug> is:open label:"crnd:lifecycle:managed"` over Issue and PullRequest nodes for number, title, updatedAt, labels(first: 30), PullRequest body, headRefName, and headRefOid, fall back to REST `gh api repos/<slug>/issues?state=open&labels=<label>&per_page=100`, and read PR details with `gh pr view <N> --repo <slug> --json body,headRefName,headRefOid`; `ManagedWorkSnapshot` returns `loaded_ok=false` when cache is too stale or absent under low GraphQL headroom, is cache-only/read-only status, not GitHub live state fact source, not host production SSOT, and not #191/#396/#238/#322 lifecycle permit; run DesignConsensusIssueIntake only from `ManagedWorkSnapshot` to discover open managed `crnd:phase:design-solving` issue, and when snapshot unavailable fail closed without writing spawn intent or dispatch ledger; run the source-OPEN gate and prompt-source projection read `gh api repos/<slug>/issues/<N>` for issue state/title/body, plus `gh api repos/<slug>/issues/<N>/comments?per_page=20` for bounded recent comments when rendering router-injected issue source snapshots; keep the state-only source-OPEN gate mirror token `gh api repos/<slug>/issues/<N> --jq .state`; append existing-format phase9-router-fallback pending events with reasons `phase9-source-not-open` or `phase9-source-state-unavailable`; before DesignConsensusIssueIntake or converge-to-next-solvers solver dispatch, suppress solver intents when a clean consensus judge log exists (`META_JUDGE_DONE:consensus:*` from `phase9-issue<N>-r*-judge.log` or `meta-judge-issue<N>-r*.log`) or when already-loaded/open issue labels or labels-only live read `gh api repos/<slug>/issues/<N> --jq '[.labels[].name]'` show terminal design-consensus phase labels `crnd:phase:consensus-reached`, `crnd:phase:implementing`, `crnd:phase:merged`, or `crnd:phase:closed`; append existing-format phase9-router-fallback pending events with key prefix `phase9-terminal-eligibility:` and reason `phase9-already-consensus`; let wakeup-plan suppress design-consensus completed-marker actions to status-only and design-consensus solver `HARNESS_SPAWN_INTENT` actions for terminal phase labels only; use router-private `Phase9ActorHealth` only as a recovery/idempotency projection over structured log/marker/path/ledger/pending/in-flight/source/terminal facts; preserve clean markerless solver target logs in place and append one existing-format fallback event with reason `phase9-solver-markerless-exhausted`; clean markerless solver evidence is terminal format failure and never authorizes `actor_health_recovery`; reuse the existing direct route prompt/intent/ledger path as route `actor_health_recovery` only when the source issue is OPEN, the terminal gate is open, there is no valid actor marker, no target log, no equivalent legacy log, no pending `HARNESS_SPAWN_INTENT`, no live in-flight `spawn-codex --log <target>`, and the latest append-only ledger row is older than `STALE_REVIVAL_HOURS` (default 3h); write router prompts, append `HARNESS_SPAWN_INTENT` with `command: "spawn-codex"` as a closed semantic enum, and append the private dispatch ledger with `dispatch_state="harness-intent"` only for the five built-in phase9 direct routes: DesignConsensusIssueIntake queues each r1 solver role (`minimal`, `structural`, `delete`) whose role-specific ledger key, r1 evidence/log, and in-flight target are absent as that role's r1 `HARNESS_SPAWN_INTENT`, and existing evidence/log/in-flight for one solver role suppresses only that role; solver triplet to judge, converge to next solver triplet, stalled to reflector, and `META_RESOLVED:re-design` from reflector to source-adjacent `marker.round + 1` solver triplet; stalled dispatch remains `round >= 3`, clean-tail, source-OPEN-gated, and reflector-only, but compares router-private normalized no-progress signatures so `source-location-missing-or-invalid`, `no-current-*target*`, and narrowly matched target/source/PR unreadable `propose:*` markers share the source-unreachable/no-actionable-source family while ordinary implementation-bearing `propose:*` blocks stalled.
- forbidden: no daemon direct `nohup spawn-codex`, no daemon executable command surface, no argv array, no shell command, no generic command bus, no `argv`, `args`, `shell`, `cmd`, `commands`, `env`, `git`, `gh`, `executor`, or `target_ref` fields in the spawn intent, no gh issue close, gh issue edit, gh label, gh pr merge, gh release, GitHub lifecycle mutation, issue close, PR merge, label lifecycle, git, commit, push, tag, release, or generic lifecycle authority; terminal design-consensus suppression must not write spawn intent or dispatch ledger.
- verification: `test_phase9_router_open_state_gate.py`, `test_phase9_router_daemon.py`, `test_wakeup_plan.py`, `test_wakeup_runner.py`, `test_cli_command_router.py`, `test_runtime_exception_authorization_sources.py`, `test_skill_reference_anchors.py`
- no_new_runtime_authority: This mirror records only the #229 read-gh source-OPEN gate plus router-local prompt-source projection, the #330 narrowed direct spawn-intent allowlist, and router-private `Phase9ActorHealth` recovery/idempotency; it does not grant daemon process-spawn, durable schema, host production SSOT, or lifecycle authority, and it adds no public revive command or new runtime exception.

<a id="gh-usage-accounting-455"></a>
## gh-usage-accounting-455

- surface: `gh usage accounting`
- source_issue: `#455`
- source_round: `maintainer-directive`
- source_marker: `direct maintainer instruction: hijack all gh calls and count them`
- skill_anchor: `#gh-usage-accountingper-455`
- allowed: prepend checked-in `skills/consensus-loop/scripts/ghwrap/gh` to controller, daemon, and codex worker `PATH`; set `CRND_GH_SOURCE` as `controller`, `daemon:<name>`, or `codex:<task_id>`; transparently delegate to the real `gh` after removing the shim directory from PATH; append bounded JSONL runtime rows to `.refactor-loop/state/gh-usage.jsonl` with schema fields `schema`, `ts`, `source`, `subcommand`, `pool`, `exit_code`, and `count`; `CRND_GH_USAGE_PATH` may only select a repo-relative or repo-contained JSONL path and otherwise falls back to `$REPO_ROOT/.refactor-loop/state/gh-usage.jsonl`; `CRND_GH_USAGE_MAX_LINES` may only lower the default retention bound and invalid, non-positive, or larger values fall back to the default; read aggregate stats through `consensus-rnd-cli gh-stats`.
- forbidden: no issue/PR/label lifecycle, no merge/close, no tag/release, no dispatch, no controller lifecycle authority, no host config edits, no GitHub request made only for measurement, no stdout/stderr/stdin capture that changes gh semantics, no accounting artifact outside `$REPO_ROOT`, and no blocking real gh when accounting fails.
- verification: `test_gh_accounting.py`, `test_cli_command_router.py`, `test_runtime_exception_authorization_sources.py`
- no_new_runtime_authority: This is observability-only accounting over existing `gh` calls; it does not authorize any new GitHub or git side effect.

<a id="rollup-autonomous-merge-2026-06-06"></a>
## rollup-autonomous-merge-2026-06-06

- surface: `release rollup singleton CI-only squash merge`
- source_issue: `maintainer-directive-2026-06-06`
- source_round: `maintainer-directive`
- source_marker: `rollup 只要ci过了就可以,不用review`
- durable_artifact: `.refactor-loop/runs/maintainer-directives/2026-06-06-rollup-autonomous-merge.md`
- skill_anchor: `#rollup-autonomous-merge-2026-06-06`
- allowed: active-controller owner only; detect and maintain exactly one open rollup PR whose head starts with `rollup/` and base is `$REVIEW_BASE_BRANCH`; when one exists, update only that rollup head with `git push --force-with-lease origin <integration_sha>:refs/heads/<existing-rollup-head>` and refresh title/body; create a new rollup PR only when no open rollup exists; exclude rollup PRs from reviewer dispatch, review-fix, and remote-ci-fix; project only `auto_merge_release_rollup_pr_from_action`; re-read live PR base/head/head SHA; verify `$ROLLUP_AUTO_MERGE` is auto/true-like; verify exact live head SHA required checks through `ReleaseRequiredChecksProjection`; then run only `gh pr merge <N> --squash --delete-branch`.
- forbidden: no cluster PR review policy change, no reviewer bypass for non-rollup PRs, no generic merge-to-review-base authority, no direct push to `$REVIEW_BASE_BRANCH`, no force-push except singleton rollup head update, no admin merge, no branch-protection bypass, no issue lifecycle, no label mutation, no tag/release, no #322 release publication change, no public lifecycle CLI, no generic lifecycle actor, no worker-owned commit/push/merge.
- fact_source: maintainer directive artifact plus host env surface matrix for `$INTEGRATION_BRANCH`, `$REVIEW_BASE_BRANCH`, `$ROLLUP_AUTO_MERGE`, and `$HOST_GITHUB_RELEASE_REQUIRED_CHECKS`; live GitHub PR view for base/head/head SHA; Checks API projection for exact-SHA required checks.
- fail_closed: missing/invalid branch config, non-rollup head, wrong base, stale action head, missing required checks, pending/red/missing/API-failed checks, invalid `$ROLLUP_AUTO_MERGE`, or branch-protection/host-policy merge failure writes a grepable pending event and leaves the PR for humans.
- verification: `test_sync_dev.py`, `test_controller_actions.py`, `test_wakeup_plan.py`, `test_wakeup_runner.py`, `test_host_env_surface_matrix.py`, `test_runtime_exception_authorization_sources.py`, `test_skill_reference_anchors.py`
- no_new_runtime_authority: This mirror grants only singleton rollup head maintenance and CI-green squash merge for `rollup/` PRs into `$REVIEW_BASE_BRANCH`; it does not authorize generic merge, release publication, review-gate bypass outside rollups, or any public lifecycle command.
