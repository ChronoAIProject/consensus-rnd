---
name: codex-refactor-loop
description: Use when the user wants an unattended Consensus R&D issue/PR resolution and work-unit loop driven by codex CLI in isolated git worktrees, with audit/refactor as a fallback compatibility issue producer, dynamic /loop wakeups, GitHub status, and per-work-unit merges.
---
> Refactor (iter319/issue-319): Old pattern: 物理拆 REFERENCE.md 后跨平台加载/维护退化.
> New principle: 单文件 SKILL.md 用 Controller Contract Index + Detailed reference anchors 分层,禁止重新拆 REFERENCE.md 作为默认修复.
> Maintainer directive recognizes this single SKILL.md as the canonical controller contract and detailed reference; use intra-file anchor links.
> Refactor (iter1/issue-141): Old pattern: 下游没有 installer 时,装机步骤散落在 README、SKILL statusline 段和 restart helper 段,缺乏从安装 skill 到配置 host.env、调度守护进程、接入 statusLine 的单步 walkthrough。
> New principle: Downstream install walkthrough 是唯一装机主段;README 链到 SKILL 锚点,SKILL 内部段落互链;source-regression 锁住单文件链接与必备 surface,bounded scheduler behavior test 锁住 consensus-rnd-cli restart-daemons 不无限阻塞。
> Refactor (iter218/issue-218): Old pattern: ensure-project-rules 是 public CLI 默认写 host policy 文件($PROJECT_RULES),违反 skill 无 host 改动权边界
> New principle: 改为 read-only check-project-rules probe + patch artifact:probe 只读判 sentinel block,非 current 写 .refactor-loop/runs/ patch 并 fail-closed 不派 actor;删 ensure-project-rules/_atomic_write,不引入 PROJECT_RULES_WRITE_ENABLE。严格按 plan 逐条改。
# Consensus R&D Work-Unit Loop — Controller Contract
This SKILL.md is the single controller contract and detailed reference by maintainer directive. It must be enough to run the loop safely on first load while keeping heavy schemas, full templates, command bodies, and recovery runbooks reachable by intra-file anchors.

Use intra-file anchors when a phase needs the detailed body, such as [host runtime details](#host-runtime-details); do not force-load unrelated sections.

## Controller Contract Index
| Contract | Keep-local invariant | Controller action | Reference anchor | Prompt/script surface |
|---|---|---|---|---|
| Host config | Loop runtime facts come only from host-owned `host.env`; skill text remains host-agnostic. | Require non-empty `CONSENSUS_RND_HOST_ENV`, then `source "$CONSENSUS_RND_HOST_ENV"` before running actors; fail closed if the locator or required vars are absent. `.refactor-loop/` is skill-private runtime/cache/log state, not host production SSOT. | [host runtime details](#host-runtime-details) | `host.env.example`, controller-internal `ControllerActions` |
| GitHub state | GitHub 是系统状态唯一显示面. Maintainer must see current state without local logs. | Post status banners and labels in the same turn as every spawn, completion, consensus, merge, block, or escalation. | [status and escalation templates](#status-and-escalation-templates) | `ControllerActions.post_status_banner`, GitHub labels |
| Active controller | 跨设备时 GitHub/已 push git 面只承载一个 `ActiveControllerLease`; local `.refactor-loop` is owner-machine cache/log only. | Owner may run controller write paths and seven write daemons; non-owner may peek/statusline or restart-daemons noop only. | [active controller lease](#named-runtime-exception--active-controller-leaseper-191) | `active_controller.py`, `ACTIVE_CONTROLLER_*` |
| Pure orchestration | Controller execution may be interactive or #396 `wakeup-runner`; all reasoning remains in codex workers / consensus gates. `phase9-router` handles only deterministic design-consensus routes; `wakeup-runner` consumes only `wakeup-plan` evidence-bound closed action projection and existing controller helpers. | Never implement product/refactor code in the controller conversation. Dispatch a codex for implementation, verification, fixing, review, and design solving; let `consensus-rnd-cli phase9-router` handle only its allowlisted deterministic routes and `consensus-rnd-cli wakeup-runner` mechanically apply only closed projection actions. | [controller contract details](#controller-contract-details) | `consensus-rnd-cli spawn-codex`, prompt files |
| Sentinel | Every AI-authored GitHub body ends with a final independent `⟦AI:AUTO-LOOP⟧` line. | Filter AI comments by sentinel and AI banner prefixes; never react to own comments as maintainer input. | [sentinel and comment filters](#sentinel-and-comment-filters) | prompts, `consensus-rnd-cli comment-monitor` |
<!-- Refactor (iter1/issue-139): Old pattern: Wake-source 契约措辞自相矛盾:SKILL.md/REFERENCE.md 多处写三选一(Monitor / task-notification / ScheduleWakeup 任一即可),与 checklist step15 / ownership 的必维持 Monitor 冲突,新会话据此漏挂 Monitor bridge。
  New principle: 统一语义:每个 controller 会话必须 arm/confirm persistent daemon-event Monitor bridge;task-notification / ScheduleWakeup 仅作 turn 级 completion/fallback,非 Monitor 替代。Durable anchors are the Wake source contract row, wake-source-rules anchor, test_skill_entrypoint_contract.py, and test_skill_reference_anchors.py; no SessionWakeSourceContract/helper/schema/daemon/Tier lifecycle expansion.
-->
| Wake source | Every controller session must maintain a persistent daemon-event Monitor bridge. | Arm or confirm the daemon-event Monitor bridge; before ending a turn, also confirm any in-flight codex task-notification or registered ScheduleWakeup fallback used as the next wake. | [wake source rules](#wake-source-rules) | Monitor bridge, harness Bash background tasks, ScheduleWakeup fallback |
| First wakeup | Consensus-rnd Phase bootstrap is ordered and mandatory before any normal phase. | Run the Consensus-rnd Phase bootstrap checklist in this file, in order. | [daemon command bodies](#daemon-command-bodies) | scripts, `host.env` |
| Work unit state | The work-unit contract is stable; do not rename, migrate, or wrap it. Root `.refactor-loop/state.json` is not a contract surface. | Use GitHub labels/comments, clean `EXIT=0` logs, prompt artifacts, git topology, and named specialized state artifacts; export `WORK_UNIT_ID=$CLUSTER_ID` for audit-backed units. | [work-unit contract](#work-unit-contract), [specialized state artifacts](#specialized-state-artifacts) | `.refactor-loop/state/*.json`, daemon-owned state files |
| Structured consumption | Steady-state controller/daemon paths consume only clean-exit status plus final allowlisted standalone marker/verdict lines, artifact frontmatter, CLI JSON/action fields, and artifact paths. Raw log prose is diagnostic-only. | Do not read, understand, quote, relay, or transcribe worker log prose, review reject prose, judge reasoning, or progress raw tails during normal routing. Raw logs are allowed only for `EXIT!=0`, stream disconnect/503, stuck/crash, missing/invalid structured artifact, router fallback, or worker self-post failure diagnostics. | [structured-consumption boundary](#structured-consumption-boundary), [phase routing details](#phase-routing-details) | `wakeup_plan.py`, `phase9/router.py`, `monitors/progress.py` |
| Phase routing | Markers route immediately to the next actor in the same wakeup. | Sweep `EXIT=0` logs, parse verdict markers only after clean exit, then spawn next work if actionable. | [phase routing details](#phase-routing-details) | logs, prompts |
| Operational names | Parsed or cross-agent names are operational interfaces with owner-local fact sources. | Keep each parser/generator in its owner surface; do not add a production registry or whole-repo naming lint. | [operational names](#operational-names) | router/progress/concurrency/git/controller actions/labels/cli/stages |
| Design consensus | Concrete plans require Consensus-rnd Phase design-consensus multi-solver consensus and meta-judge consensus. | Dispatch minimal, structural, delete solvers; meta-judge returns consensus/converge only; router-owned stalled predicate may route qualifying converge to reflector, with legacy stalled markers read-only compatible. | [design-consensus details](#design-consensus-details) | `solver-*.md`, `meta-judge.md` |
| Large issue decomposition | Large/epic issues split only through a validated `IssueDecompositionPlan` consensus artifact; parent issue remains open/tracking. | Active-controller owner may run the checked-in internal apply helper to create managed child design issues and comment on the parent; workers, daemons, public CLI, and `wakeup-plan` do not create decomposition issues. | [large issue decomposition](#large-issue-decomposition) | `issue_decomposition.py`, `ControllerActions.apply_issue_decomposition_plan` |
<!-- Refactor (issue-304): Old: meta-judge owned a fresh stalled output. New: stalled is a router predicate continuation; legacy stalled markers are compatibility input only. -->
| Floor | Keep `$CODEX_FLOOR` host-scoped codexes, default 5, hard lower bound 2. | Count only this loop's `consensus-rnd-cli spawn-codex` processes containing absolute `$REPO_ROOT`; top up before ScheduleWakeup. | [concurrency floor details](#concurrency-floor-details) | `consensus-rnd-cli concurrency`, `consensus-rnd-cli peek` |
| Labels | Every issue/PR has exactly one phase label and one human label. | Sync labels and banner together; `crnd:human:maintainer-decision` only after allowed meta-layer routes. | [label bootstrap loops](#label-bootstrap-loops) | controller-internal `ControllerActions`, GitHub labels |
| Spawn | Mainline codex spawn uses harness background tasks, not detached nohup. | Use one background task per codex; if detached already happened, preserve work and rely on log sweep plus wake source. | [codex invocation details](#codex-invocation-details) | `consensus-rnd-cli spawn-codex` |
| Hard rules | All worker prompts inherit controller-level hard rules. | Include scope, git, test, language, and no-scope-creep constraints in every spawned prompt. | [hard rules details](#hard-rules-details) | prompt templates |
| Language | Source files are English-only; external user-facing artifacts are 中文 by default. README.md + README.zh-CN.md is the only English-canonical public-doc carve-out. No mandatory parallel English section. | Enforce on prompts, GitHub posts, commits, docs, source comments/logs. | [language policy details](#language-policy-details), [historical bilingual notes](#historical-bilingual-notes) | prompts, docs, commit text |

<a id="two-entry-modes"></a>
## Main path and fallback producer

The default main path is open actionable catalog-managed GitHub issue/PR resolution. The controller dispatches the next-step actor for managed issues and PRs before starting any producer for new work.

`issue-driven / Path A` is the main-path issue entry surface: create or reuse a concrete GitHub issue, apply the catalog-derived design issue label bundle (`crnd:lifecycle:managed`, `crnd:phase:design-solving`, and `crnd:human:auto`), then let the controller sweep dispatch Consensus-rnd Phase design-consensus directly. Legacy issue-entry labels are migration aliases only and must not be written as the active bundle.

`audit` remains a stable compatibility producer value and fallback issue producer. It runs only after no open actionable managed issue/PR, queued dispatch, clean marker route, CI/no-gap route, maintainer-comment route, or higher-priority wakeup route exists. Audit produces or updates issues that feed back into the main path; it is not a co-equal entry mode or a parallel R&D lane. Issue-driven work uses the router-injected GitHub issue source snapshot as the work-unit source when no local audit artifact is provided; `gh issue view <N>` is fallback-only when that snapshot is unavailable. Concrete plans still require Consensus-rnd Phase design-consensus solver consensus and meta-judge consensus before implementation.

Workflow stage display names are sourced from `scripts/codex_refactor_loop/workflow_stages.py`. The built-in registry remains the default compatibility vocabulary; public built-in stage display must use `Consensus-rnd Phase <stage>`. Legacy `phase9-router` and `phase9-issue...` strings are compatibility command and artifact dialects only.

<a id="operational-names"></a>
## Operational names

Operational names are names parsed, routed, spawned, inspected, consumed by GitHub/git state, or passed across agents. They are protocol surfaces, not style preferences. Each surface is owner-local: its owner declares the field order, allowed charset, canonical write policy, legacy read/migration policy, behavior tests, and source-regression anchors. Do not add `codex_refactor_loop/names.py`, `check_naming.py`, or `naming_policy.py` as a production fact source; do not copy existing label, command, stage, or route catalogs into a generic registry.

Owner map:

| Owner | Operational names owned | Policy |
|---|---|---|
| `scripts/codex_refactor_loop/phase9/router.py` | `phase9-issue<N>-r<R>-<role>`, `solver-issue<N>-r<R>-<role>`, `meta-judge-issue<N>-r<R>`, and design-consensus artifact references | Canonical writer/parser for design-consensus filename identity and artifact references; legacy input is local to the router. |
| `scripts/codex_refactor_loop/review_fix_dispatch.py` | `fix-pr<N>-round-<R>` review-fix dispatch filename identity | Canonical writer of review-fix prompt, log, and report artifact filename identity. |
| `scripts/codex_refactor_loop/controller_actions.py` | `review-pr<N>-<role>-r<R>` reviewer prompt/log filename identity | Canonical writer for #396 reviewer dispatch prompt and log artifact names; review evidence parsing remains owned by `wakeup_runner.py`. |
| `scripts/codex_refactor_loop/monitors/progress.py` | progress-comment target extraction for `review-pr<N>-<role>-r<R>`, `fix-pr<N>-<round>`, and `phase9-issue<N>-r<R>-<role>` | Read-only extraction owner only; malformed near-misses return empty and prompt fallback is allowed only when a prompt file exists. |
| `scripts/codex_refactor_loop/wakeup_plan.py` | read-only stale-target extraction for `HARNESS_SPAWN_INTENT` fields `task_id`, `intent_id`, `source`, `route`, and `reason` matching `PR #<N>`, `issue #<N>`, `phase9-issue<N>-r<R>-<role>`, `review-pr<N>-<role>-r<R>`, and `fix-pr<N>-(r|round-)<R>` | Extraction owner only for suppressing stale closed/merged target spawn intents. Field order is `task_id`, `intent_id`, `source`, `route`, then `reason`; numbers are positive decimals and role charset `[A-Za-z][A-Za-z0-9_-]*`; no canonical write authority; legacy free-text read only for stale closed/merged target suppression; unresolved targets fail open; behavior and source-regression are locked in `test_wakeup_plan.py`. |
| `scripts/codex_refactor_loop/monitors/concurrency.py` | mutable/read-only dispatch `task_id` prefix classification | Classification owner only; main-readonly prefixes must match exact owner-local forms before `$REPO_ROOT` `cd` is allowed. |
| `scripts/codex_refactor_loop/controller_actions.py` and `scripts/codex_refactor_loop/git.py` | `refactor/iter<I>-<cluster>` branch/worktree generation | Validate iteration digits and cluster `[A-Za-z0-9._-]+` locally; this is duplicated owner-local safety until one implementation is removed or delegated. |
| `scripts/codex_refactor_loop/controller_actions.py` and `scripts/codex_refactor_loop/sync/dev.py` | `rollup/<integration_sha>` release rollup heads | Controller-owned throwaway head only; no generic branch-name registry. |
| `scripts/codex_refactor_loop/labels.py` | `crnd:<group>:<slug>` labels | Sole canonical label catalog; consumers import/use catalog helpers instead of copying regex truth tables. |
| `scripts/codex_refactor_loop/cli.py::COMMANDS` | public command names and authority tokens | Public command catalog and authority fact source; controller lifecycle primitives stay outside `COMMANDS`. |
| `scripts/codex_refactor_loop/workflow_stages.py` | workflow stage display names and slugs | Sole built-in stage catalog; HostWorkflowSpec may project `host:` data without overwriting built-ins. |

`HOST_WORKFLOW_SPEC` may point at one repo-relative JSON HostWorkflowSpec. Empty or unset keeps built-in behavior. The file is data-only route vocabulary and a seven-surface data-only projection for events, host stages, work-unit kinds, roles, prompt bindings, consensus policies, and issue-intake mappings (`events`, `stages`, `work_unit_kinds`, `roles`, `prompt_bindings`, `consensus_policies`, and `issue_intake_mappings`). All host-added names must use the reserved `host:` namespace, and `WorkflowInvariantValidator` rejects attempts to overwrite built-ins, public compatibility aliases, marker families, producers, or cluster aliases. HostWorkflowSpec grants no lifecycle authority: no command, shell, argv, git, commit, push, merge, close, label mutation, assignee, milestone, import, or executor fields are allowed. It also cannot downgrade consensus: design-consensus-shaped host policy still requires at least three independent solvers, exactly one independent judge, peer-output isolation, and fixed marker families. First-version scope is bounded to status/prompt/intake projection; it is not a DAG executor and does not create public marker aliases. Consensus-rnd Phase design-consensus router direct-spawn-intent ignores host `roles`, `dispatch`, and `consensus_policies` completely; its allowlist is always the built-in `minimal`/`structural`/`delete` solver triplet plus built-in `judge`.

## Host 配置(通用化注入点)
<!-- Refactor (iter1/issue-170):
  Old pattern: host.env contract facts were split across prose tables and
  template comments, so categories, defaults, consumers, and test ownership
  could drift.
  New principle: SKILL.md owns one host.env surface matrix; host.env.example is
  a copyable template view; tests mechanically derive exported keys,
  categories, defaults, prompt placeholders, and runtime literal anchors from
  that matrix.
-->
These variables are injected by the host project. The skill must not hardcode project facts.
`CONSENSUS_RND_HOST_ENV` locates the host-owned `host.env` loop runtime injection file; it is the only runtime locator for host facts and is not a host production config schema.
### Host env surface matrix
This matrix is the only manually maintained host.env contract. `host.env.example` is a copyable template view; tests derive its expected exports, categories, defaults, and prompt placeholders from this table.

| Variable | Category | Owner | Default/example | Missing/empty behavior | Consumer | Test owner |
|---|---|---|---|---|---|---|
| `$CONSENSUS_RND_HOST_ENV` | required | HostEnvLocator | repo-relative host-owned path, e.g. `.config/consensus-rnd/host.env` | required for host fact loading; when set it must be repo-relative or repo-contained absolute; missing, empty, unreadable, or invalid locators fail closed; no `.refactor-loop/host.env` fallback is read; it is not host production config schema | LoopContext locator | `test_loop_context.py`, `test_host_env_surface_matrix.py` |
| `$REPO_ROOT` | required | LoopContext | host absolute repo path | fail closed; do not infer from cwd unless an explicit read-only fallback test allows it | LoopContext | `test_loop_context.py` |
| `$GH_REPO_SLUG` | required | LoopContext | `OWNER/REPO` | fail closed for GitHub operations when absent or not `OWNER/REPO`; preferred slug | LoopContext, release-gate | `test_loop_context.py`, `test_auto_release_gate.py` |
| `$GH_OWNER` | compatibility | LoopContext | optional owner fallback | noop when `$GH_REPO_SLUG` is present; used only with `$GH_REPO_NAME` compatibility construction | LoopContext | `test_loop_context.py` |
| `$GH_REPO_NAME` | compatibility | LoopContext | optional repo-name fallback | noop when `$GH_REPO_SLUG` is present; used only with `$GH_OWNER` compatibility construction | LoopContext | `test_loop_context.py` |
| `$BUILD_CMD` | required | LoopContext | host shell command string | fail closed for build-required work; callers must execute with `bash -lc "$BUILD_CMD"` after sourcing host.env | prompt templates | `test_skill_entrypoint_contract.py` |
| `$TEST_CMD` | required | LoopContext | host shell command string | fail closed for test-required work; callers must execute with `bash -lc "$TEST_CMD"` after sourcing host.env | prompt templates | `test_skill_entrypoint_contract.py` |
| `$INTEGRATION_BRANCH` | required | sync helpers | host integration branch name | fail closed when missing or empty; never infer host branch topology or default to a product branch | sync helpers, release-gate | `test_sync_dev.py`, `test_auto_release_gate.py` |
| `$REVIEW_BASE_BRANCH` | required | sync helpers | host review-base branch name | fail closed when missing or empty; never infer host branch topology or default to a product branch | sync helpers, release-gate | `test_sync_dev.py`, `test_auto_release_gate.py` |
| `$PROJECT_RULES` | defaulted | LoopContext | `CLAUDE.md` | default to `CLAUDE.md` as host-owned read-only prompt/bootstrap evidence; non-current fixed points produce a patch artifact and fail closed | LoopContext, prompt templates | `test_ensure_project_rules_fixed_points.py` |
| `$RELEASE_AUTO_ENABLE` | defaulted | release-gate | `false` | false or empty exits 0 with noop reason and writes no release decision artifact | release-gate | `test_auto_release_gate.py`, `test_release_gate_module.py` |
| `$HOST_GITHUB_RELEASE_REQUIRED_CHECKS` | defaulted | release required-check projection | host-owned comma-separated exact check-run names, e.g. `ci,lint,typecheck` | comma-separated exact GitHub check-run names; empty has no effect for non-release hosts, but `RELEASE_AUTO_ENABLE=true` with empty/missing fails closed with `missing_host_required_release_checks` | release-gate, ReleasePublishPreflight, ReleasePublisher | `test_required_release_checks.py`, `test_auto_release_gate.py`, `test_release_publish_preflight.py` |
| `$UPDATE_CHECK_ENABLE` | optional-noop | update-check probe | `false` | false or empty exits 0 with noop reason and writes disabled update-check state | update-check, restart-daemons | `test_update_check.py`, `test_restart_daemons.py` |
| `$UPDATE_CHECK_INTERVAL_SECONDS` | defaulted | update-check probe | `21600` | default to `21600` seconds; fresh local update-check state is reused for manual probes | update-check, concurrency snapshot projection | `test_update_check.py`, `test_concurrency_monitor_snapshot.py` |
| `$UPDATE_CHECK_TIMEOUT_SECONDS` | defaulted | update-check probe | `5` | default to `5` seconds for GitHub release/tag reads; failures write unknown state and never block restart | update-check | `test_update_check.py` |
| `$RELEASE_AUTO_MIN_MERGES` | defaulted | release-gate | `1` | default to `1` recent merge for stability scoring | release-gate | `test_auto_release_gate.py` |
| `$RELEASE_AUTO_MIN_INTERVAL_HOURS` | defaulted | release-gate | `2` | default to `2` hours since last release decision | release-gate | `test_auto_release_gate.py` |
| `$RELEASE_ROLLUP_MIN_COMMITS` | defaulted | sync helpers | `1` | default to `1` integration-ahead commit before release-rollup pending event | sync helpers | `test_sync_dev.py` |
| `$RELEASE_ROLLUP_COOLDOWN_SECONDS` | defaulted | sync helpers | `21600` | default to `21600` seconds before duplicate release-rollup event for the same integration SHA | sync helpers | `test_sync_dev.py` |
| `$CI_GUARDS` | optional-noop | prompt templates | empty or host guard script | empty skips with reported noop reason `guards skipped: CI_GUARDS unset` | prompt templates | `test_skill_entrypoint_contract.py` |
| `$CODEX_FLOOR` | defaulted | concurrency floor | `5` | missing or invalid defaults to `5`; values below hard min `2` clamp to `2` | concurrency monitor, wakeup plan | `test_concurrency_monitor.py`, `test_wakeup_plan.py` |
| `$STALE_REVIVAL_HOURS` | defaulted | stale revival | `3` | missing, invalid, or non-positive defaults to `3` hours; converts to seconds and gates re-triggering stuck managed work whose blocking local evidence (e.g. a redispatchable implement log) is older than the threshold | wakeup plan | `test_wakeup_plan.py` |
| `$ACTIVE_CONTROLLER_DEVICE_ID` | optional-noop | active-controller lease | empty | empty means default single-device local-owner noop; multi-device opt-in requires a stable per-device id on every upgraded device | active_controller, restart/concurrency/router/comment/progress/dev-sync/controller actions | `test_active_controller_lease.py`, `test_host_env_surface_matrix.py` |
| `$ACTIVE_CONTROLLER_TTL_SECONDS` | defaulted | active-controller lease | `1800` | missing or invalid defaults to `1800`; owner renews before expiry; expired lease may be acquired by another device | active_controller | `test_active_controller_lease.py`, `test_host_env_surface_matrix.py` |
| `$COMMENT_MONITOR_INTERVAL` | defaulted | comment-monitor | `30` | default to `30` seconds; higher values lower fixed search cost while unchanged `updatedAt` items skip comments queries | comment-monitor | `test_comment_monitor.py` |
| `$COMMENT_MONITOR_LOOKBACK` | optional-noop | comment-monitor | empty or `YYYY-MM-DD` / `updated:>=YYYY-MM-DD` search fragment | empty adds no lookback filter; non-empty is limited to a GitHub `updated:` search qualifier and must not change labels, ownership, or write behavior | comment-monitor | `test_comment_monitor.py`, `test_host_env_surface_matrix.py` |
| `$HOST_REFACTOR_COMMENT_POLICY` | defaulted | prompt templates | `none` | missing/empty/default normalizes to `none`; rationale belongs in external artifacts; explicit `self-doc-comment` is downstream compatibility opt-in and must obey source English-only; any other value is invalid and fail-closed | prompt templates | `test_host_env_surface_matrix.py`, `test_refactor_comment_policy_prompt_contract.py`, `test_source_language_policy.py` |
| `$SOURCE_GLOBS` | optional-noop | review prompts | host source glob hints | empty means review from actual diff and project evidence; do not invent host source layout | review prompts | `test_host_env_surface_matrix.py` |
| `$MAINTAINER_WHITELIST` | conditional-fail-closed | comment-monitor | host GitHub handles | optional for hosts without comment-monitor/direct-mention intake; when that surface runs, empty fails closed | comment-monitor | `test_comment_monitor.py` |
| `$HOST_TEST_FILE_GLOBS` | prompt-empty-infer | prompt templates | empty | infer from existing tests; fail closed if unsafe to locate writable tests | prompt templates | `test_host_env_surface_matrix.py` |
| `$HOST_TEST_NAMING_RULE` | prompt-empty-infer | prompt templates | empty | mirror existing tests; do not assume suffix, extension, or framework | prompt templates | `test_host_env_surface_matrix.py` |
| `$HOST_COMMENT_RULE` | prompt-empty-infer | prompt templates | empty | match surrounding file style or mark not applicable; do not invent a host comment syntax | prompt templates | `test_host_env_surface_matrix.py` |
| `$HOST_CODE_FENCE_LANG` | prompt-empty-infer | prompt templates | empty | omit language tag; do not invent a host language default | prompt templates | `test_host_env_surface_matrix.py` |
| `$HOST_PROTO_POLICY` | prompt-empty-infer | prompt templates | empty | treat schema/protocol checks as diff/project-rule driven only; do not invent protobuf or schema defaults | prompt templates | `test_host_env_surface_matrix.py` |
| `$HOST_ARCHITECTURE_GREP_CHECKS` | prompt-empty-infer | prompt templates | empty | use `$PROJECT_RULES`, `$SOURCE_GLOBS`, `$CI_GUARDS`, and diff evidence only | prompt templates | `test_host_env_surface_matrix.py` |
| `$HOST_WORKFLOW_SPEC` | optional-noop | WorkflowSpecLoader | empty or repo-relative JSON | empty/unset keeps built-in behavior; non-empty validates exactly seven data-only projection surfaces: events, stages, work_unit_kinds, roles, prompt_bindings, consensus_policies, and issue_intake_mappings; phase9 direct-spawn-intent ignores host roles/dispatch/policies and keeps the built-in allowlist | workflow_spec, triage, wakeup plan, controller templates | `test_host_workflow_spec.py`, `test_skill_reference_anchors.py`, `test_phase9_router_package.py` |

Prompt templates reference these fields as `${HOST_*}` placeholders so normal `host.env` sourcing plus `render_template`/`envsubst` injects them at prompt construction time. Do not add aliases for the rejected Set B names.

`$HOST_REFACTOR_COMMENT_POLICY` controls only refactor-history self-documentation source-comment semantics. Missing, empty, or default policy is `none`, which rejects Old/New refactor-history source comments and keeps rationale in external artifacts. Explicit `self-doc-comment` is a downstream compatibility opt-in; `${HOST_COMMENT_RULE}` only supplies comment syntax in that mode and does not override source English-only.

Host config rules:
1. `host.env` is the only loop runtime fact injection point. It is not host production configuration schema.
2. `GH_REPO` must not be exported as a bare repo name; use `GH_REPO_SLUG`.
3. `CI_GUARDS` is optional. Use `[ -n "${CI_GUARDS:-}" ]` before invoking it and report `guards skipped: CI_GUARDS unset` when absent.
4. Source host-owned `$CONSENSUS_RND_HOST_ENV` before daemon or codex supervision commands; if unset, fail closed and ask the host to set the explicit locator.
5. `$BUILD_CMD` and `$TEST_CMD` are shell command strings. They may contain `cd`, `&&`, pipes, and host script invocations; callers must run `bash -lc "$BUILD_CMD"` / `bash -lc "$TEST_CMD"` or an equivalent sourced shell invocation, never split them into argv.
6. Detailed daemon start examples live in [daemon command bodies](#daemon-command-bodies), including the `bash -c 'test -n "${CONSENSUS_RND_HOST_ENV:-}" && source "$CONSENSUS_RND_HOST_ENV" && exec'` pattern and why `env $(grep ...)` is unsafe.
7. Runtime scripts must consume host.env through LoopContext or the shared parser in context.py; root host.env and unlisted aliases such as INTEGRATION, REVIEW_BASE, and WORKTREE are not compatibility inputs.
8. `$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}` is host-owned read-only evidence. `consensus-rnd-cli check-project-rules` may inspect the sentinel block and write `.refactor-loop/runs/project-rules-fixed-point.patch`; it must never apply host policy edits.
9. Multi-device mode is safe only after all devices run a version that honors the #191 active-controller lease. Mixed old/new versions are not safe for multi-device mode.
10. The active-controller lease ref is a code-owned singleton constant, not a host scope. Host env must not define alternate active-controller refs.
11. `.refactor-loop/` is the skill-private runtime home for cache/log/state/prompt/run artifacts. Host production facts, branch topology, durable ledger authority, and host-owned config SSOT must live in host-owned config/rules/artifacts, not in `.refactor-loop/`.

## Skill Root Contract
`<skill-root>` means the installed `skills/codex-refactor-loop` directory containing this `SKILL.md`, `scripts/consensus-rnd-cli spawn-codex`, and `prompts/`. Runtime scripts self-locate from their own file path; `CODEX_REFACTOR_LOOP_SKILL_ROOT` is optional and only for wrappers or nonstandard packaging. If that override is set but invalid, scripts fail closed instead of falling back to `.claude/skills`.

Detailed path examples and host installation variants stay in the detailed reference section of this `SKILL.md`; the controller-contract section keeps only self-location invariants.

<a id="downstream-install-walkthrough"></a>
## Downstream install walkthrough

<!--
Refactor (iter1/issue-141):
  Old pattern: 下游没有 installer 时,装机步骤散落在 README、SKILL statusline 段和 restart helper 段,缺乏从安装 skill 到配置 host.env、调度守护进程、接入 statusLine 的单步 walkthrough。
  New principle: Downstream install walkthrough 是唯一装机主段;README 链到 SKILL 锚点,SKILL 内部段落互链;source-regression 锁住单文件链接与必备 surface,bounded scheduler behavior test 锁住 consensus-rnd-cli restart-daemons 不无限阻塞。
-->

This walkthrough is the only downstream install runbook for `codex-refactor-loop`. It documents existing checked-in surfaces only: plugin or copy install, loop runtime fact injection through host-owned `host.env` located by `CONSENSUS_RND_HOST_ENV`, user-level cron or launchd calling `consensus-rnd-cli restart-daemons`, Claude Code `statusLine` pointing at the read-only `consensus-rnd-cli statusline`, and uninstall/rollback.
<!-- Refactor (issue-298): Old: downstream daemon status guidance reused restart-daemons or heartbeat/process checks for reads. New: daemon-status --json is the read-only status surface; restart-daemons remains the only repair/reload command. -->

The skill must not modify a host repository's `.git` config, CI config, or policy files. It must not add installer scripts, host runtime installers, statusline installers, or a root `INSTALL.md`. Loop runtime facts come only from host-owned `host.env`; `CONSENSUS_RND_HOST_ENV` locates that file and is not a second host production config schema.

### Install the skill

Use the platform plugin mechanism when available. For direct copy install, copy the checked-in skill directory into the agent's skills directory:

```bash
mkdir -p ~/.claude/skills
cp -R skills/codex-refactor-loop ~/.claude/skills/codex-refactor-loop
```

When the skill is installed by a plugin, use that installed `<skill-root>` path. When it is copied, `<skill-root>` is the copied `codex-refactor-loop` directory.

### Inject host facts

From the host repository root:

```bash
mkdir -p .config/consensus-rnd
cp <skill-root>/host.env.example .config/consensus-rnd/host.env
$EDITOR .config/consensus-rnd/host.env
export CONSENSUS_RND_HOST_ENV=.config/consensus-rnd/host.env
```

Fill the host-owned `host.env` according to the Host env surface matrix: required values must be set, defaulted values may keep their template defaults, optional/noop values may stay empty, and conditional fail-closed surfaces such as `MAINTAINER_WHITELIST` are required only when their surface is enabled. The optional `HOST_*` language-policy variables are empty by default and may stay empty unless the host has explicit policy text to inject. `CONSENSUS_RND_HOST_ENV` must point at this host-owned file before loop runtime commands run.

<a id="github-workflow-portability-checklist"></a>
### GitHub workflow portability checklist

#104 setup is folded into this skill's existing owner surface. It may only generate or fill host-owned `.config/consensus-rnd/host.env`, a repo-relative JSON file named by `HOST_WORKFLOW_SPEC`, and optional repo-relative prompt or body binding files referenced from that JSON. It must not create a standalone setup skill or a second protocol owner.

Allowed host artifacts:

- `.config/consensus-rnd/host.env` with `CONSENSUS_RND_HOST_ENV=.config/consensus-rnd/host.env`.
- A repo-relative HostWorkflowSpec JSON with exactly seven data-only surfaces: `events`, `stages`, `work_unit_kinds`, `roles`, `prompt_bindings`, `consensus_policies`, and `issue_intake_mappings`.
- Optional repo-relative prompt/body binding files referenced by HostWorkflowSpec.

Forbidden setup actions: no host `.github` edits, no label mutation, no issue/PR mutation, no branch-protection probing or edits, no git mutation, no branch creation, and no merge/close side effects. Future #357 interactive configuration may guide a maintainer through the same contract, but it must output these same host-owned artifacts rather than owning a new setup protocol.

#### Guided GitHub consensus workflow setup

When a host user asks for guided setup, do not add a renderer, CLI command, setup skill, installer, template directory, or root install document. Follow this walkthrough and write advisory artifacts by hand under `.refactor-loop/runs/github-workflow-setup/<timestamp>/`:

- `host-env.patch.md`: derive a suggested host-owned `.config/consensus-rnd/host.env` patch from the Host env surface matrix and `host.env.example`; include the explicit `CONSENSUS_RND_HOST_ENV=.config/consensus-rnd/host.env` locator setup.
- `labels-plan.json`: derive the label plan from `scripts/codex_refactor_loop/labels.py`; do not run `gh label create`, `gh label edit`, or `gh label delete`.
- `scheduler.md`: point at the existing cron/launchd `consensus-rnd-cli restart-daemons` examples below; do not install a scheduler.
- `statusline.json`: point at the existing read-only `consensus-rnd-cli statusline`; do not write Claude Code settings.
- `host-workflow-spec.json`: optional data-only `HOST_WORKFLOW_SPEC` draft when the host needs workflow invariants; use the existing `workflow_spec.py` / `WorkflowInvariantValidator` contract, `host:` namespace entries, repo-relative paths, and no command, git, label, merge, or lifecycle fields.
- `walkthrough.md`: summarize what the host still needs to review and apply.

Do not produce `summary.json` or `host-workflow-spec.example.json`. These artifacts are advisory only: they must not modify host `.git`, `.github`, CI, policy, branch protection, GitHub labels, issues, PRs, commits, pushes, merges, closes, tags, releases, installers, settings, or any lifecycle surface.

### Keep existing daemons alive

Install exactly one user-level scheduler. The command must require non-empty `CONSENSUS_RND_HOST_ENV` and `source "$CONSENSUS_RND_HOST_ENV"` before it execs the checked-in helper; this preserves values with spaces and keeps all loop runtime facts in the host env file.
Existing scheduler entries must be updated to set `CONSENSUS_RND_HOST_ENV` to the host-owned file; `.refactor-loop/host.env` is not a runtime fallback.

Cron example:

```bash
*/5 * * * * cd /abs/path/to/host-repo && bash -lc 'export CONSENSUS_RND_HOST_ENV=.config/consensus-rnd/host.env; source "$CONSENSUS_RND_HOST_ENV" && exec python3 <skill-root>/scripts/consensus-rnd-cli restart-daemons' >> .refactor-loop/logs/restart-cron.log 2>&1
```

launchd `ProgramArguments` example:

```xml
<key>ProgramArguments</key>
<array>
  <string>/bin/bash</string>
  <string>-lc</string>
  <string>cd /abs/path/to/host-repo && export CONSENSUS_RND_HOST_ENV=.config/consensus-rnd/host.env && source "$CONSENSUS_RND_HOST_ENV" && exec python3 &lt;skill-root&gt;/scripts/consensus-rnd-cli restart-daemons >> .refactor-loop/logs/restart-cron.log 2>&1</string>
</array>
<key>StartInterval</key><integer>300</integer>
```

The helper remains the existing cron/launchd-only anti-stop surface. It has no lifecycle authority: it must not commit, push, merge, label, create, close, or edit issues/PRs.

For operator inspection, read daemon state with `python3 <skill-root>/scripts/consensus-rnd-cli daemon-status --json`. If the projection reports stale/dead owner daemons, run the existing scheduler command above or `python3 <skill-root>/scripts/consensus-rnd-cli restart-daemons` to repair/reload; do not use a separate start/stop/restart/reload verb.

### Add the Claude Code status line

Set Claude Code `statusLine` manually to the checked-in read-only script:

```json
{
  "statusLine": "python3 /abs/path/to/skills/codex-refactor-loop/scripts/consensus-rnd-cli statusline"
}
```

Use the installed `python3 <skill-root>/scripts/consensus-rnd-cli statusline` path. This is not an installer; it does not edit settings for the user.

### Uninstall or rollback

Remove the user-level cron or launchd entry, remove the Claude Code `statusLine` setting, stop any running helper-managed daemon wrappers if needed, and remove the copied skill directory only if it was installed by direct copy. Keep or delete the host repository's host-owned `host.env` according to the host's own rollback policy.

<a id="release-pipeline-integrationpost-61"></a>
## Named runtime exception — autonomous release gate(per #56)
Authorization mirror: `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#autonomous-release-gate-56`. It records `META_JUDGE_DONE:consensus:A-with-host-opt-in-as-gate`: autonomous release decision after one host opt-in gate. `$RELEASE_AUTO_ENABLE=true` in `host.env` is that opt-in; when it is absent or not `true`, `consensus-rnd-cli release-gate` exits 0 with a noop reason and writes no release decision.

`consensus-rnd-cli release-gate` is decision-artifact-only. **禁止** decider 直接 bump/commit/push: it must not run `git`, bump mapped manifests, commit, push, tag, publish, merge, close, or otherwise exercise lifecycle authority. It only computes stability from GitHub/state artifacts and writes durable release decision/candidate artifacts for the controller-owned release publisher to consume.

Before making the release decision, the controller runs `consensus-rnd-cli release-commits --target-ref origin/$REVIEW_BASE_BRANCH`, then runs `consensus-rnd-cli release-gate`. `release-commits` is the independent narrow producer for `.refactor-loop/state/release-commits.json`. Authorization mirror: `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#release-commits-producer-232`. Allowed: read git by fetching tags, describing the latest release tag, resolving the target ref, and logging the release range; atomically write `.refactor-loop/state/release-commits.json`; authority tokens are `read-git` and `write-artifact` only. Forbidden: no gh, push, merge, reset, tag, release, lifecycle mutation, or inline execution inside release-gate. Fact source: local git tags and refs. Verification: behavior and source-regression coverage in test_release_commits.py and test_cli_command_router.py. Release-gate only reads `.refactor-loop/state/release-commits.json`; it does not run git and must remain a consumer-only decider.

Command contract:
| Command | Behavior |
|---|---|
| `python3 <skill-root>/scripts/consensus-rnd-cli release-commits --target-ref origin/$REVIEW_BASE_BRANCH` | Pre-gate projection producer. Read local git tags/refs, derive commits since the latest release tag for the target ref, and atomically write `.refactor-loop/state/release-commits.json`; no GitHub or lifecycle authority. |
| `python3 <skill-root>/scripts/consensus-rnd-cli release-gate` | Dry-run. Compute stability, decide release type when ready, write `.refactor-loop/state/release-decision.json`, and print a summary. |
| `python3 <skill-root>/scripts/consensus-rnd-cli release-gate --dispatch` | Compute a ready decision and write `.refactor-loop/state/release-decision.json` plus `.refactor-loop/state/release-candidate.json`; print a hint that the controller-owned publisher owns bump/commit/push/tag/release after preflight. |
| `python3 <skill-root>/scripts/consensus-rnd-cli release-gate --score-only` | Compute and print stability only; it does not require release opt-in and does not write the decision file. |

Stability requires all signals green and fail-closed handling on missing or red evidence: the shared Checks API projection must see exact check-run name success for every name in `$HOST_GITHUB_RELEASE_REQUIRED_CHECKS` on both `$REVIEW_BASE_BRANCH` and `$INTEGRATION_BRANCH` (host.env); zero open `crnd:phase:blocked` PRs; zero `crnd:human:maintainer-decision` labels; zero Consensus-rnd Phase review-gate reject churn at three or more consecutive rounds; last 30 minutes P0 alert streak at most 3; at least `RELEASE_AUTO_MIN_MERGES` recent merge commits in `.refactor-loop/state/recent-pr-merges.json` for the last two hours(default 1); every restart-managed daemon heartbeat fresh within 90 seconds; and zero unresolved `META_RESOLVED:escalate-human` records. `RELEASE_AUTO_ENABLE=true` with missing or empty `$HOST_GITHUB_RELEASE_REQUIRED_CHECKS` fails closed with `missing_host_required_release_checks`. `recent-pr-merges.json` is a controller-owned post-merge projection produced by `merge_pr` after successful non-admin `gh pr merge`; the release decider only reads it and never discovers merge facts from `git` or GitHub. Release cadence also requires more than `RELEASE_AUTO_MIN_INTERVAL_HOURS` hours since last release(default 2). Detailed scoring and the release decision schema live in [release decision schema](#release-decision-schema).

`release-decision.json` records `from_version`, `to_version`, `bump_type`, `commits`, `decided_at`, `stability_score`, `signals`, `ready`, `blocked_reasons`, and `release_interval`. `release-candidate.json` records the artifact-only handoff metadata, including the decision artifact path, target version, target ref, expiry, decision digest, required signal projection, host opt-in name, publish preflight name, and controller lifecycle owner.

## Named runtime exception — release-publication(per #322)
<!-- Refactor (iter217/issue-217): Old pattern: release.yml 保留 tag/release mutation,无法可靠读本地 runtime fact,绕过 release-gate decider-only 边界. New principle: controller-only publication via ReleasePublishPreflight+ReleasePublisher; release.yml is read-only preview(contents:read) and forbidden to create tags/releases. -->
<!-- Refactor (iter1/issue-322): Old pattern: ReleasePublisher controller writes lived only in SKILL prose. New principle: name release-publication-322, mirror its exact allowlist, and lock forbidden lifecycle surfaces with tests. -->
<!-- Refactor (iter334/issue-334): Old pattern: ReleasePublisher could create a release tag at a fresh manifest-bump SHA before exact-SHA checks were green. New principle: after safe push, gate that exact fresh SHA with ReleaseRequiredChecksProjection before release creation. -->
<!-- Refactor (iter341/issue-341): Old pattern: ReleasePublisher.publish() 线性 bump/add/commit→push→green-gate;push 后 CI pending 即陷入不可恢复授权态(manifests 已 bump,re-run git commit nothing-to-commit 失败)——beta.5 靠 controller hand-complete 绕过. New principle: 单一 publish() 主链路加 already-bumped reentry:仅当唯一 preflight mismatch 是 mapped manifests 已==to_version 且 git show -s --format=%s HEAD 证明 HEAD subject 精确为 'Release v<to_version>' 时跳过 bump/add/commit 三步,随后仍必须 _safe_push + exact-SHA required-checks green gate + gh release create + result artifact。严格按 DESIGN_DECISION_PATH verbatim Concrete plan;不新增 resume ticket/public CLI/workflow 发版权/host.env 事实源. -->
Authorization source: `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#release-publication-322`. Exact-SHA evidence mirror: `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#controller-release-publisher-334`; it records source_issue `#334`, source_round `r5`, source_marker `META_JUDGE_DONE:converge:round-4:decide`, and skill_anchor `#release-pipeline-integrationpost-61`. The release lifecycle surface consumes decision-artifact-only output through the controller only. A scheduled/on-demand active-controller owner may run `ReleasePublisher` only after `ReleasePublishPreflight` validates `RELEASE_AUTO_ENABLE=true`, fresh `.refactor-loop/state/release-candidate.json`, fresh `.refactor-loop/state/release-decision.json`, matching `decision_digest`, matching `target_ref`, mapped manifest `from_version`, and required checks green. `ReleasePublisher` may run the first-run publication allowlist: `python3 .github/scripts/bump_version.py --version <to_version>`, `git add .version-bump.json <mapped manifests>`, `git commit -m "Release v<to_version>"`, `git rev-parse HEAD`, `git fetch origin HEAD`, `git rev-list --count HEAD..origin/HEAD`, `git push origin HEAD`, read `gh api repos/<slug>/commits/<fresh release commit sha>/check-runs --paginate --slurp` through `ReleaseRequiredChecksProjection`, and only then `gh release create v<to_version> --target <fresh release commit sha> --generate-notes [--prerelease]`. `ReleasePublisher` may also use already-bumped reentry when the only preflight mismatch is mapped manifests already equal `to_version` and `git show -s --format=%s HEAD` proves the HEAD subject is exactly `Release v<to_version>`; reentry may skip only `python3 .github/scripts/bump_version.py --version <to_version>`, `git add .version-bump.json <mapped manifests>`, and `git commit -m "Release v<to_version>"`, then must run `git rev-parse HEAD`, `git fetch origin HEAD`, `git rev-list --count HEAD..origin/HEAD`, `git push origin HEAD`, read `gh api repos/<slug>/commits/<exact release/reentry commit sha>/check-runs --paginate --slurp`, and only then `gh release create v<to_version> --target <exact release/reentry commit sha> --generate-notes [--prerelease]`. Mirror summary: commit/push the release manifest commit or prove the already-bumped reentry commit, read exact-SHA Checks API, and create tag/release only after that exact fresh SHA is green. Missing `GH_REPO_SLUG`, pending/red/missing/stale exact-SHA required checks, invalid Checks API JSON, Checks API failure, or pending/red/missing/API-fail fail closed before release creation and before `.refactor-loop/state/release-publish-result.json` is written. It writes `.refactor-loop/state/release-publish-result.json` only after release creation. Fact source: release candidate/decision artifacts + mapped manifests + exact fresh SHA + Checks API projection. `consensus-rnd-cli release-gate` remains decider only; `release.yml` is read-only manual preview/verification with `contents: read` and no tag or GitHub Release authority. Forbidden: no public `consensus-rnd-cli release-publish`, no public `consensus-rnd-cli publish-release`, no workflow tag/release creation, no tag target without exact-SHA green checks, no proof-ticket/resume system, no `git tag`, no force-push, no `git merge`, no `git rebase`, no `git reset`, no arbitrary branch push, no worker diff commit, no GitHub Release edit/delete/upload, no approval-ticket/emoji gate, no issue/PR lifecycle, no issue lifecycle, PR lifecycle, label mutation, label lifecycle, merge/close, or generic lifecycle actor. Verification: `test_release_publisher.py`, `test_release_publish_preflight.py`, `test_cli_command_router.py`, `test_runtime_exception_authorization_sources.py`, `test_release_pipeline_contract.py`, `test_skill_reference_anchors.py`, and `test_controller_actions.py`. no_new_runtime_authority: this names and mirrors the existing controller-owned ReleasePublisher path; mirror only, not a runtime API/loader/schema/proof ticket/authorization source; it adds no public CLI, no workflow publication authority, and no production runtime behavior beyond the checked-in preflight plus publisher allowlist.

## Notify-only update check(per #231)
Authorization source: `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#update-check-231`. `skills/codex-refactor-loop/VERSION.json` is the checked-in `VersionSourceManifest`: data-only fields are `schema`, `version`, `repository`, `release_source`, and `install_hint`; only `version` is listed in `.version-bump.json`.

`consensus-rnd-cli update-check` is notify-only. It reads local `VERSION.json`, reads the GitHub latest release and then tags for `ChronoAIProject/consensus-rnd`, and atomically writes `.refactor-loop/state/update-check.json`. `$UPDATE_CHECK_ENABLE` missing, empty, or not true exits 0 and writes disabled state with a reason. GitHub/network/manifest errors write unknown state and do not block `restart-daemons`.

Downstream apply remains host-owned: the probe must not copy, overwrite, reinstall, edit host config, run installers, mutate `.git`, commit, push, tag, release, open/close/edit issues or PRs, mutate labels, or create a daemon. `restart-daemons` may call `maybe_run_update_check(startup=True)` only after the fixed daemon start/skip pass, and failures are warnings. `concurrency` may project fresh, positive update state into `statusline-snapshot.json`; `statusline` remains snapshot-only and never reads `update-check.json` directly or touches the network. When `update_available=true`, statusline displays `up:v<latest>`.

Verification is locked by `test_update_check.py`, `test_cli_command_router.py`, `test_restart_daemons.py`, `test_concurrency_monitor_snapshot.py`, `test_statusline.py`, `test_runtime_exception_authorization_sources.py`, and `test_skill_reference_anchors.py`.

<!-- Refactor (iter1/issue-166): Old pattern: CLI command authority was represented by coarse read_only metadata and prose-only runtime exception text, so the matrix could under-report command surfaces. New principle: `cli.py::COMMANDS[*].authority` is the inline closed-token mechanical fact source, including named narrow carveouts such as dev-sync's integration-worktree git surface, and SKILL prose cannot grant missing runtime authority. -->
<!-- Refactor (iter201/issue-201): Old pattern: public consensus-rnd-cli exposed merge-pr/open-pr/safe-push/apply-sync/apply-triage lifecycle commands and wakeup_plan/peek rendered copyable suggested_command, forming generic lifecycle authority. New principle: delete public lifecycle CLI surface; COMMANDS covers public non-lifecycle commands only, controller lifecycle primitives stay internal, wakeup-plan emits fixed controller_action facts with no_lifecycle_authority:true, and dev-sync keeps only the #53 carveout. -->
## CLI runtime authority fact source(per #166)
`skills/codex-refactor-loop/scripts/codex_refactor_loop/cli.py::COMMANDS[*].authority` is the unique mechanical fact source for CLI runtime command authority for worker-visible, non-lifecycle public commands and for the single #396 lifecycle daemon command `wakeup-runner`. Each `CommandSpec.authority` is an inline closed-token tuple that states a public command's maximum git/gh/spawn/write-artifact/label/merge capability. Controller lifecycle primitives such as `merge_pr`, `open_pr_with_label`, `open_release_rollup_pr_from_pending_event`, `apply_human_label_or_skip`, `safe_push`, `safe_sync_main`, and triage apply stay outside `COMMANDS`; their authorization comes from controller-owned decisions/action facts and direct internal call boundaries. `wakeup-plan` may emit only fixed facts such as `controller_action: "safe_push"` with `no_lifecycle_authority: true`; #396 `wakeup-runner` may consume only `wakeup-plan` closed action projection with `apply_authority: "wakeup-runner-396-only"` and `runner_authority: "wakeup-runner-396"`. `peek` may display those facts but must not render executable lifecycle commands. SKILL prose explains the human contract, narrow allowlist, durable authorization source, and no lifecycle authority by default; it must not grant a CLI capability missing from `CommandSpec.authority`. Worker prompt authority remains in prompt contracts and prompt tests, not as pseudo-commands in `COMMANDS`. New or expanded public CLI runtime authority requires matching behavior test and source-regression anchor coverage.

## Named runtime exception - wakeup-runner(per #396)
Authorization source: `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#wakeup-runner-396`. `wakeup-runner` is active-controller owner only and consumes only `wakeup-plan` evidence-bound closed action projection. It revalidates #191 owner, clean `EXIT=0` source marker when required, review truth table `reject==0 && approve>=1 && all required reviewers present && all required reviewer heads equal live PR head`, OPEN/live GitHub state, missing/stale per-reviewer head SHA, release #322 preflight, and helper-specific preconditions before mechanically calling existing controller helpers or the named #396 helpers. `wakeup-plan` action `head_sha` is not reviewer-head authority. Consensus→implement projection durable fact source is the consensus judge artifact frontmatter, `## If consensus`, `Implementation owner`, and Implement plan structured fields `scope_paths`, `old_pattern`, `new_principle`, and optional `verification_hints`; parser failure emits no implementation action. Consensus implementation readiness is a helper-specific precondition: dispatchable projection requires `consensus_implementation_ready` and suppresses to status-only with `suppressed_reason` such as `open_closing_pr`, `remote_iter_branch`, `in_flight_implement`, or `scope_conflict_waiting` when the OPEN managed issue is already claimed by a closing PR, canonical implementation branch/worktree, implement log, pending intent, in-flight worker, or an earlier executable consensus implementation action with overlapping normalized `scope_paths`; runner and controller helper revalidate before dispatch. `dispatch_consensus_implementation` creates the canonical worktree/head, writes an empty reservation commit, pushes the head, and opens the managed draft PR before spawning the implement worker; missing reservation for an already clean implementation is treated as `early_pr_missing` fresh redispatch. `publish_implementation_output` may only commit/push the implementation diff to exactly one matching open managed PR with the canonical head, integration base, and single linked managed issue; zero, multiple, head/base/link mismatch, or stale-base clean output fails closed/status-only, and stale-base clean `IMPLEMENT_DONE:ok` is `implementation_refresh_needed:stale_base` without clearing the log. Before runner application, `wakeup-plan` prunes stale, terminal, or superseded local evidence from its projection; release-rollup freshness may use read-only local `refs/remotes/origin/<review_base>..refs/remotes/origin/<integration>` evidence to suppress stale pending events, and local ref probe failure fails open. This narrows runner input only and does not weaken #396 revalidation or create standalone authorization. Allowed actions are spawn codex, named helper `dispatch_consensus_implementation`, named helper `publish_implementation_output`, named helper `open_release_rollup_pr_from_action`, publish worker output, dispatch reviewers/fix/remote-ci worker, apply triage decision, merge PR under review truth table, close managed item from drop marker, and publish release through #322. Forbidden: no arbitrary git/gh command, workflow tag/release, prompt-body decision, standalone authorization from `wakeup-plan`, argv/shell/cmd/command_line/commands/env/git/gh/executor/lifecycle_authority/lifecycle_owner/generic command fields, `ControllerTurnDecision`, controller-turn worker, private schema, active-active scheduler, `.refactor-loop/host.env` as host production SSOT, generic lifecycle actor, and arbitrary label/merge/close outside existing helper or named #396 helper. Verification: `test_wakeup_runner.py`, `test_wakeup_runner_review_gate.py`, `test_wakeup_runner_release.py`, `test_wakeup_plan.py`, `test_cli_command_router.py`, `test_runtime_exception_authorization_sources.py`, `test_restart_daemons.py`, and `test_skill_reference_anchors.py`.

<a id="large-issue-decomposition"></a>
## Large issue decomposition(per #403)
Authorization source: `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#issue-decomposition-403`. Large or epic issue decomposition is allowed only after design-consensus/meta-reflector consensus that the source is scope-too-broad or explicitly `decompose`. The only controller-private handoff is an `IssueDecompositionPlan` artifact with exactly `{schema, parent_issue, source_consensus_artifact, children:[{slug,title,scope,non_goals,body_artifact_path}], parent_update:{comment_artifact_path}}`. The plan must not contain lifecycle owner/authority, command, argv, shell, gh, git, close, assignee, milestone, absolute path, or path traversal fields; child bodies must be self-contained GitHub bodies with parent issue, source consensus artifact, scope, non-goals, and final sentinel.

Only the active-controller owner may run the checked-in internal `ControllerActions.apply_issue_decomposition_plan()` helper after `issue_decomposition.py` validates the plan. The helper creates child design issues through the catalog design issue label bundle (`crnd:lifecycle:managed`, `crnd:phase:design-solving`, `crnd:human:auto`) and posts exactly one tracking comment on the parent issue. This is not a public issue factory. The parent epic remains open/tracking: no parent issue close, reopen, body edit, title edit, assignee, milestone, or label lifecycle mutation is part of #403.

Discoverability stays on existing surfaces. `META_JUDGE_DONE:consensus:decompose` is not a phase9 direct route; the router appends an existing-format `phase9-router-fallback` pending event. A clean judge log is visible through `completed_marker_actions()` as generic `kind: completed-marker`, `phase: design-consensus`, `actor: design-consensus-router-or-controller`; `peek` may show the pending-events tail read-only. `wakeup_plan.py` is not the #403 owner and must not project a decompose action/status, `IssueDecompositionPlan`, `issue-decomposition`, `decomposition-plan`, or `apply_issue_decomposition_plan`.

## Named runtime exception — autonomous-release-gate lifecycle boundary(per #56)
Host-agnostic, no lifecycle authority: only read repo/GitHub evidence and write durable decision/candidate artifacts; do not run `git`, bump mapped manifests, commit, push, tag, publish, open, close, label, approve, merge, or otherwise lifecycle-manage issues or PRs.

## Named runtime exception - update-check(per #231)
Authorization source: `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#update-check-231`. Narrow allowlist: read checked-in `skills/codex-refactor-loop/VERSION.json`, read GitHub release/tag metadata, write `.refactor-loop/state/update-check.json`, and let `concurrency` project fresh positive update fields into `statusline-snapshot.json`. Forbidden: no copy/overwrite/reinstall, host config edit, git or GitHub lifecycle mutation, installer, new daemon, commit, push, merge, tag, release, issue/PR lifecycle, label lifecycle, or apply/update command surface.

<a id="named-runtime-exception--active-controller-leaseper-191"></a>
## Named runtime exception - active controller lease(per #191)
Authorization source: `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#active-controller-lease-191`. This is the single-active-controller cross-device carveout. Durable source is one JSON blob at `active-controller.json` on `refs/heads/crnd/active-controller`. Lease-only git allowlist: `git fetch origin <lease-ref>`, `git ls-remote --exit-code --heads origin <lease-ref>`, `git rev-parse`, `git show <commit>:active-controller.json`, `git hash-object -w --stdin`, `git mktree`, `git commit-tree`, and `git push --force-with-lease=<old>:<lease-ref>`. These commands may only read/build/publish the singleton lease blob CAS and expose owner/expiry. JSON fields are `owner_device`, `lease_id`, `acquired_at`, `expires_at`, `renewed_at`, `repo`, `reason`, and `source_issue`.

Allowed: read/acquire/renew the singleton lease, expose owner and expiry, and gate controller write paths plus the seven restart-helper-managed write daemons. Non-owner devices may run read-only peek/statusline surfaces and `restart-daemons` must write `active_controller=noop:not-owner` without starting write daemons. Worker throughput remains owner-local via `$CODEX_FLOOR`; there is no cross-device floor aggregation.

Forbidden: no worker diff commit, issue/PR create/merge/close/edit, label mutation, tag/release, per-work claim, host-defined lease scope, daemon ownership matrix, active-active scheduler, generic distributed lock library, or generic lifecycle actor. Missing `ACTIVE_CONTROLLER_DEVICE_ID` is the default single-device local-owner noop, not a multi-device claim.

#193 metadata-only invariant: issue/PR `author.login` and `updatedAt` may only be planning/routing/stale read-only metadata. They must not become side-effect authorization, per-work owner authority, claim/lease scope, stale takeover permit, or any replacement for the #191 `ActiveControllerLease` / `require_active_controller(...)` gate on issue/PR target writes.

## Named runtime exception — closed-label-reconciler(per #238)
Authorization source: `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#closed-label-reconciler-238`. This is the only closed managed item phase-label reconciliation carveout. Consensus marker: `META_JUDGE_DONE:consensus:closed-label-reconciler:option B named restart-managed closed-label-reconciler with crnd:phase:closed and gh-label-closed-reconcile authority`.

Allowed: active-controller owner only; checked-in `closed-label-reconciler` command (`consensus-rnd-cli closed-label-reconciler`) may read CLOSED `crnd:lifecycle:managed` issue/PR labels and apply terminal phase-label reconciliation: remove phase labels, cleanup-only aliases, and `crnd:lifecycle:stuck`, then add exactly one terminal phase label, either `crnd:phase:merged` when merged evidence is present or `crnd:phase:closed` when evidence is insufficient. `crnd:phase:closed` is a protocol terminal state, not a product verdict.

Forbidden: no open item mutation, issue create/close/reopen/body/title edit, PR create/merge/close/body/title edit, human label mutation, triage label mutation, milestone label mutation, lifecycle label mutation beyond removing `crnd:lifecycle:stuck`, tag/release, generic `gh-label`, generic `gh-edit`, generic lifecycle actor, or controller close-path inline reconcile. `peek` may display the read-only projection but must not render remediation text or copyable edit commands.

Fact source and verification: projection logic lives in `closed_phase_labels.py`, the daemon in `closed_label_reconciler.py`, and the only public command authority is `cli.py::COMMANDS["closed-label-reconciler"].authority == ("read-gh", "gh-label-closed-reconcile", "write-state")`; it does not grant generic `gh-label` or `gh-edit`. Behavior/source-regression coverage: `test_closed_label_reconciler.py`, `test_peek_status_lens.py`, `test_cli_command_router.py`, `test_restart_daemons.py`, `test_label_taxonomy.py`, `test_label_contract_source.py`, and `test_runtime_exception_authorization_sources.py`.

## Named runtime exception — integration sync daemon(per #53)
Authorization source: `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#integration-sync-daemon-53`. **Narrow allowlist**: daemon-owned autonomous integration-branch git apply in the dedicated integration worktree. The daemon may fetch refs, run `git ls-remote --exit-code --heads origin $INTEGRATION_BRANCH`, compare ref counts and ancestry, detect conflicts, dispatch resolver workers, append pending events, write integration sync operation artifacts, and execute only the #53 git allowlist: `git fetch`, `git ls-remote --exit-code --heads origin $INTEGRATION_BRANCH`, `rev-list`, `rev-parse`, `merge-base`, `reset --hard`, `rebase --rebase-merges`, `merge --ff-only|--no-ff`, `git push HEAD:$INTEGRATION_BRANCH`, and force-with-lease rollup adoption. **Forbidden**: no worker-diff commit, no PR create/merge/close/edit, no issue/PR/label lifecycle, no tag/release, no generic lifecycle actor, and no git commands outside that allowlist. Implement/fix workers still never commit, push, or open PRs.

## Named runtime exception — observability-comment-writers(per #53)
Authorization source: `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#observability-comment-writers-53`. **Narrow allowlist**: GitHub issue/PR comments, PR body edit, reactions, and deleting/updating own progress comments only. Progress target/kind facts are owned locally by `monitors/progress.py`: exact log basenames are the canonical target source, and prompt fallback applies only when the matching prompt file exists. Comment-monitor controller-post identity is owned locally by `monitors/comment.py`: a final `⟦AI:AUTO-LOOP⟧` sentinel is canonical, while `CONTROLLER_PREFIXES` is only a legacy compatibility skip list. Observability runtime paths are private `.refactor-loop` paths derived from `LoopContext`, not host env surfaces. Issue/PR target writes still require the #191 `ActiveControllerLease` / `require_active_controller(...)` gate; #53 is not a cross-device write permit. **Forbidden**: label mutation, issue/PR close/create/merge, release/tag, and git lifecycle. Triage accept/reject writes manual issue triage decision artifacts for controller apply instead of mutating labels/body directly.

## Named runtime exception — gh usage accounting(per #455)
Authorization source: `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#gh-usage-accounting-455`. The checked-in `scripts/ghwrap/gh` shim is observability-only accounting for existing GitHub CLI calls. Controller CLI dispatch prepends the shim to PATH with `CRND_GH_SOURCE=controller`; `restart-daemons` starts each daemon with `CRND_GH_SOURCE=daemon:<name>`; `spawn-codex` starts workers with `CRND_GH_SOURCE=codex:<task_id>`. Owner-local runtime surfaces are bounded: `CRND_GH_USAGE_PATH` may only select a repo-relative or repo-contained JSONL path and otherwise falls back to `$REPO_ROOT/.refactor-loop/state/gh-usage.jsonl`; `CRND_GH_USAGE_MAX_LINES` may only lower the default retention bound and invalid, non-positive, or larger values fall back to the default. The shim removes its own directory from PATH, delegates to the real `gh`, preserves argv/stdin/stdout/stderr/exit code semantics, and appends bounded JSONL records to `.refactor-loop/state/gh-usage.jsonl` with `schema`, `ts`, `source`, `subcommand`, `pool`, `exit_code`, and `count`. `consensus-rnd-cli gh-stats` is read-state only and aggregates per source, pool, and subcommand. Forbidden: no issue/PR/label lifecycle, no merge/close, no tag/release, no dispatch, no controller lifecycle authority, no host config edits, no measurement-only GitHub requests, no accounting artifact outside `$REPO_ROOT`, and no blocking real `gh` when accounting fails. Verification: `test_gh_accounting.py`, `test_cli_command_router.py`, and `test_runtime_exception_authorization_sources.py`.

## Named runtime exception — integration sync daemon(per #65)
Authorization mirror: `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#integration-sync-release-rollup-65`. It records the existing-review-base pending-event boundary. **Narrow allowlist**: release-rollup detection and existing-format pending-event emission only; event facts include `integration_branch`, `review_base_branch`, `integration_sha`, `review_base_sha`, `ahead_count`, `detected_at`, and `reason`.
**No lifecycle authority**: the daemon must not run `gh pr create`; it must not create PRs, edit PRs, label PRs, close PRs, approve PRs, merge PRs, or push directly to `$REVIEW_BASE_BRANCH`. Controller pending-event sweep re-checks open head/base PRs, writes the Chinese PR body, and calls `open_release_rollup_pr_from_pending_event`, which first pushes a throwaway `rollup/<integration_sha>` head and then delegates to `open_pr_with_label`; the rollup PR head must not be `$INTEGRATION_BRANCH` itself. Behavior/source-regression tests cover event emission, suppression, cooldown, missing integration ref alerts, throwaway rollup heads, and forbidden daemon lifecycle tokens.

## Skill degradation source-repo validation
<!-- Refactor (iter259/issue-259): Old pattern: check-degradation --static 把 downstream/plugin host root 当 source tree 扫描,吐 skills/codex-refactor-loop/... required-file false-positive(每 tick rc=1). New principle: degradation.py 内加私有 not-source-repo guard:无 source sentinels 时 rc=0 + reason not-source-repo;source repo candidate 仍 fail-closed;不新增 SourceRepoValidationContext,不改 manifest.py. -->
`skill-degradation` is source-repo CI/release validation, not downstream host runtime authority. Source validation runs through `consensus-rnd-cli check-degradation --static`; CI required `skill-degradation` runs `<skill-root>/scripts/consensus-rnd-cli check-degradation --static`; release required checks are not hardcoded by source-repo CI job names. `consensus-rnd-cli release-gate` consumes `required_release_checks()` from `$HOST_GITHUB_RELEASE_REQUIRED_CHECKS` and checks whichever host-owned exact GitHub check-run names are listed there. Static degradation checking against a non consensus-rnd source repo root must return rc=0 with `not-source-repo`, without emitting source-repo required-file findings, writing host artifacts, or creating runtime alerts/pending events; any source repo candidate remains fail-closed. A downstream host has no runtime watch, no alert log, no pending event, no peek lens, and no host.env knobs for skill-degradation. **Forbidden actions**: no source mutation; no git reset/rebase/merge/push; no GitHub issue/PR/body/label lifecycle mutation; no codex dispatch; no standalone daemon creation; no WorkUnit/schema/envelope changes; no protocol/plugin registry; no auto-clean root garbage; no auto-fix API. Details: [skill degradation source-repo validation details](#skill-degradation-source-repo-validation-details).

## Claude Code statusline(per #51 consensus)
`skills/codex-refactor-loop/scripts/consensus-rnd-cli statusline` 是 fast (<200ms) read-only Claude Code statusline reader,显示本仓库 loop 实时状态(codex 计数、PR/issue 数、daemon 健康、P0 streak、freeze 指示、optional update notice)。

**Producer**:`consensus-rnd-cli concurrency` 每 tick 末尾原子写 `.refactor-loop/state/statusline-snapshot.json`(reuse 现有 daemon,**无新 daemon**)。Snapshot 包含 `daemons` map(扫 `.refactor-loop/heartbeats/*.ts` 动态发现,每条记 `age_seconds` + `stale`,stale 阈值 90s)+ 汇总 `daemons_healthy` / `daemons_total`。
**Consumer**:`consensus-rnd-cli statusline` 读 snapshot,bash + jq < 200ms。任一 daemon stale → ⚠ 红色。显示形如 `⚙ 5/10 PR:1 issue:9 d:5/5`;当 snapshot 中 `update_available=true` 时追加 `up:v<latest>`。Statusline 不直读 `update-check.json`,不触网。

**Install one-liner**(host project,**手动一行,无 installer script**):

```json
// ~/.claude/settings.json
"statusLine": "python3 /abs/path/to/skills/codex-refactor-loop/scripts/consensus-rnd-cli statusline"
```
(host 用安装后的 `python3 <skill-root>/scripts/consensus-rnd-cli statusline` 或拷过去的对应路径。)
完整下游装机顺序见 [Downstream install walkthrough](#downstream-install-walkthrough);本段只保留 statusline invariant。

**Uninstall one-liner**:删 `statusLine` 字段即可。

**Named runtime exception(per #51 consensus)**:
- **Narrow allowlist**:concurrency_monitor 写 snapshot + 顺手 stat `heartbeats/*.ts` 汇总 daemon 健康;不引入新 daemon、不持 lifecycle authority、不读 prompt body。
- **Host-agnostic**:snapshot schema 不含 host fact;daemon 发现按 heartbeat 文件 glob,无 hard-coded daemon 列表;consensus-rnd-cli statusline 不假设 host repo 结构(只依赖 $REPO_ROOT)。
- **No lifecycle authority**:statusline 只 read,不写 GitHub / git / file lifecycle。
- **Behavior tests**:`test_statusline` < 200ms + 各 state icon + freeze 指示 + daemon health 显示;`test_concurrency_monitor::SnapshotDaemonHealthFieldTests` 覆盖 fresh / stale / malformed / missing-dir / 动态发现 / snapshot 字段。
- **Source-regression**:本段 + Named exception 子段 + install one-liner。

授权来源:`skills/codex-refactor-loop/authorizations/runtime-exceptions.md#statusline-51`(Consensus-rnd Phase design-consensus r3 3/3 unanimous consensus on C framing)。

## Anti-stop restart helper cron/launchd install(per #49)

<!-- Refactor (iter1/issue-143): Old pattern: consensus-rnd-cli restart-daemons wrapper sidecar wrote heartbeat while daemon loop hangs.
New principle: singleton wrapper + actor-owned heartbeat lease; stale means actor loop stopped renewing.
Same heartbeat path/epoch/90s consumers; no new daemon, lifecycle authority, CLAUDE.md, or Tier change. -->
<!-- Refactor (issue-264): Old: restart skip trusted one fresh pidfile wrapper and missed duplicate canonical instances.
New: skip additionally requires zero duplicate canonical live wrapper for the same static allowlist command; process inventory is helper-private daemon-maintained state, not controller probing. -->
<!-- Refactor (issue-298): Old: status reads and repair were both described through restart-daemons. New: daemon-status --json reads the same helper-private pid/heartbeat/fingerprint/inventory facts without lifecycle authority; restart-daemons is still the only write-side repair/reload path. -->
`skills/codex-refactor-loop/scripts/consensus-rnd-cli restart-daemons` 是 checked-in,host-agnostic restart helper。它维护 static daemon allowlist 的 singleton wrapper + actor-owned heartbeat lease + helper-private launch fingerprint(`concurrency_monitor`, `comment-monitor`, `codex-progress-reporter`, `dev_sync_daemon`, `phase9_router_daemon`, `closed_label_reconciler`)。事实源是 `.refactor-loop/locks/<daemon>.pid`、`.refactor-loop/heartbeats/<daemon>.ts`、`.refactor-loop/locks/<daemon>.fingerprint.json`,以及 helper-private `DaemonProcessInventory`;只有 pid alive、actor-loop heartbeat fresh(`<90s`)、fingerprint current、且同一 static allowlist command 零 duplicate canonical live wrapper 时才 skip,missing/malformed/mismatch fail-closed 并重启对应 wrapper。每次 helper tick 先调用 `consensus-rnd-cli log-retention`,直接删除超过 24h 的 `.refactor-loop/logs/*.log`;不 archive、不索引、不新增非 allowlist daemon。
Before starting or repairing any of the seven write daemons, `restart-daemons` acquires or renews the #191 active-controller lease. A non-owner restart writes local status `active_controller=noop:not-owner` and exits 0 without starting, killing, or repairing those daemons.
`consensus-rnd-cli daemon-status --json` is the paired read-only daemon-status projection. It reports `running`, `stale`, `dead`, or `not-owner` from the existing static allowlist, helper-private launch fingerprint, pid/heartbeat readers, cached active-controller status, and `DaemonProcessInventory`; it has no public start/stop/restart/reload lifecycle verb. Repair/reload remains restart-daemons.

完整下游装机顺序见 [Downstream install walkthrough](#downstream-install-walkthrough);本段保留 cron/launchd-only helper invariant。

Uninstall note: remove the cron line or unload/delete the launchd plist; do not replace it with a new watchdog daemon, installer script, or lifecycle actor.

## Named runtime exception — anti-stop restart helper(per #49)

`skills/codex-refactor-loop/scripts/consensus-rnd-cli restart-daemons` = Consensus-rnd Phase design-consensus r3 授权的 cron/launchd-only anti-stop helper,不新增 watchdog daemon。

- **Narrow allowlist**: helper 只 maintain singleton wrapper + actor-owned heartbeat lease + helper-private launch fingerprint for `concurrency_monitor`, `comment-monitor`, `codex-progress-reporter`, `dev_sync_daemon`, `phase9_router_daemon`, `closed_label_reconciler` in the existing static daemon allowlist;heartbeat 是 actor-loop progress lease,不是 wrapper sidecar liveness;daemon actor after tick / caught exception / lease sleep renews it;fingerprint artifact `.refactor-loop/locks/<daemon>.fingerprint.json` 只记录 daemon name、resolved command、CLI entrypoint hash、Python package tree hash 和文件计数,只用于 restart skip eligibility;`DaemonProcessInventory` 只在 helper 内部枚举 same resolved static allowlist command 的 canonical live wrapper,发现 duplicate canonical live wrapper 时 duplicate canonical wrappers fail closed:先 terminate 多余实例并等待下一 tick,绝不在 duplicate 存在时 spawn;并顺手运行 `consensus-rnd-cli log-retention` 对 24h+ `.refactor-loop/logs/*.log` direct rm;不 spawn codex / commit / push / merge / label / archive。
- **Read-only status projection**: `consensus-rnd-cli daemon-status --json` mirrors the same static allowlist and helper-private pid/heartbeat/fingerprint/inventory facts plus cached active-controller status. It is read-only status only, has no public start/stop/restart/reload lifecycle verb, and repair/reload remains restart-daemons.
- **Host-agnostic**: 只使用 `$REPO_ROOT` 相对路径和 `<skill-root>` self-location;无 host fact hardcode。
- **No lifecycle authority**: 不开关 issue/PR,不打 label,不 commit/push/merge/tag/release;controller wakeup `STALE_CONTROLLER` 事件仅 alert。
- **Behavior tests**: `test_restart_daemons.py` 覆盖 fresh heartbeat + matching fingerprint skip / stale/missing/malformed heartbeat repair / dead pid repair / missing/malformed/mismatched fingerprint restart / CLI entrypoint and package tree fingerprint restart / duplicate cleanup / concurrent helper no double-spawn / deterministic hung actor restart;`test_daemon_heartbeat.py` 覆盖 deterministic lease sleep renewal;`test_log_retention.py` 覆盖 24h direct rm / idempotency / restart hook。
- **Source-regression**: `AntiStopRestartHelperContractTests` + `RestartDaemonsBehaviorTests.test_restart_helper_source_mentions_launch_fingerprint_contract` + `LogRetentionSourceRegressionTests` 字面断言本段标题、narrow allowlist、no lifecycle authority、cron/launchd install、#49 mirror authorization path、helper singleton check + actor-owned heartbeat freshness check + helper-private launch fingerprint fact source、controller wakeup ordering、anti-regression forbidden tokens、no wrapper sidecar heartbeat writer、direct rm、no archive/index/new daemon。

授权来源:`skills/codex-refactor-loop/authorizations/runtime-exceptions.md#anti-stop-restart-helper-49`(Consensus-rnd Phase design-consensus r3 `META_JUDGE_DONE:consensus:A-cron-only-with-pending-event-alert`)。

## Dogfood anti-rules(per #205)

<!-- Refactor (iter205/issue-205):
  Old pattern: dogfood 实测的运维经验(audit 并行撞 iteration 号、新 role prompt 漏注册 marker contract、review verdict grep log tail 误判、daemon 恢复手 kill)只靠 agent 记忆,没落进 skill 合同与机械验证。
  New principle: 把四条经验写回局部合同:SKILL.md 增 #205 反面规则段(audit 同一时刻单 active iteration、新 role prompt 必同步 marker inventory、review verdict 权威源优先 review artifact frontmatter、daemon 恢复只走 restart-daemons);audit.md 渲染后 ITERATION 空则 fail-closed;peek.py 局部优先读 review artifact frontmatter verdict;配套 source-regression + behavior test。不新增跨模块抽象层。
-->

These are local controller contract rules learned from dogfood incidents:

1. Audit fallback may have only one active `audit-iter-N` for the same `N` at a time; never start parallel audit runs that reuse `audit-iter-N.md`, `audit-iter-N-candidates.ndjson`, or `audit-iter-N.log`.
2. Audit prompt rendering fails closed when `ITERATION` is empty; do not write `audit-iter-.md`, `audit-iter--candidates.ndjson`, or similarly empty-identity artifacts.
3. Any new role prompt under `skills/codex-refactor-loop/prompts/*.md` must be registered in `test_marker_emission_contract.py` prompt inventory, including both `PROMPT_ALLOWLISTS` and `PROMPT_ARTIFACT_PROFILES`.
4. Review verdict authority for merge-readiness starts from `.refactor-loop/runs/review-pr<N>-<role>-r<R>.md` frontmatter `verdict: approve|comment|reject`; only missing or invalid review artifacts fall back to clean log-tail `REVIEW_DONE` markers.
5. To read daemon state, run `python3 <skill-root>/scripts/consensus-rnd-cli daemon-status --json`; daemon repair/reload goes through `python3 <skill-root>/scripts/consensus-rnd-cli restart-daemons`; controller must not hand-kill daemon processes, probe process lists as liveness authority, or bypass the restart helper.

## Wakeup Skeleton

Every `/loop`, task notification, ScheduleWakeup resume, or daemon pending-event wakeup follows this skeleton. Each controller session must arm or confirm the mounted persistent Monitor bridge before pending-event sweep, marker parsing, concurrency-floor handling, or dispatch/spawn. Daemon pending-event wakeups are valid only through that Monitor or equivalent harness bridge; daemon alone is not a wake source. The Consensus-rnd Phase design-consensus router daemon may replace controller dispatch for SOLVER_DONE triplets, converge, and router-derived stalled continuation; legacy stalled judge markers are read-only compatibility input under the same gates; controller fallback sweep remains authoritative for every other marker.

<!-- Refactor (issue-277): Old: 并发 floor 把 audit fallback 当成无限可重复派发,会和 #205 单 active audit 规则冲突。New: floor 无通用豁免,`AUDIT_DONE:none:0` 仍不豁免;但同一 iteration ordinary audit fallback 只有一个 active slot,slot 占用且无其他合法 work 时输出 WAIT + blocked_deficit,不重复 audit。 -->

`consensus-rnd-cli wakeup-plan` is the prioritized-next-action reader and `codex_refactor_loop wakeup-plan` is the package CLI subcommand; the script remains a compatibility entrypoint. Contract: 每次唤醒 / every wakeup must mechanically call `python3 <skill-root>/scripts/consensus-rnd-cli wakeup-plan --repo-root "$REPO_ROOT"` first and execute from its structured output; execution may be the interactive controller or #396 `wakeup-runner`. The `wakeup-plan` named surface is an evidence-bound closed action projection with top-level `mode: "closed-action-projection"`, `no_lifecycle_authority: true`, and `apply_authority: "wakeup-runner-396-only"`, but it is not standalone authorization source. Each executable action must carry `runner_authority: "wakeup-runner-396"`, `preconditions`, `source_marker` or `source_artifact`, `target_kind`, `target_number`, `target`, `controller_action`, and `no_generic_command: true`; status actions remain `status_only: true` and cannot apply. Authorization: `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#wakeup-runner-396`, `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#maintainer-directive-wakeup-plan-script`, and `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#maintainer-directive-floor-no-exemption`. `consensus-rnd-cli wakeup-plan` 直接算并发并产出 deficit hard-gate; controller 不得用通用 low-floor exemption 结束唤醒. `AUDIT_DONE:none:0` no longer exempts the floor: if no existing actionable work is open and no same-iteration audit is active, the plan still emits `RECOMMEND:audit` plus `HARD_GATE:dispatch_required=N`; if the single active audit slot is already occupied, it emits `WAIT:single-active-audit` with `dispatch_required=0`, `reason=single_active_audit_in_flight`, and `blocked_deficit=N`; no duplicate same-iteration audit.

`consensus-rnd-cli peek` is a status lens, not routing authority; it remains useful for human-readable ambient state after the plan. `consensus-rnd-cli wakeup-plan` outputs prioritized routing recommendations from local evidence plus GitHub labels; `consensus-rnd-cli peek` displays status and does not decide next action.

1. Run `python3 <skill-root>/scripts/consensus-rnd-cli wakeup-plan --repo-root "$REPO_ROOT"` first and follow its prioritized `actions` / `recommendation` output.
2. Run `python3 <skill-root>/scripts/consensus-rnd-cli peek | tail -80` as the status lens.
3. Load host config with `test -n "${CONSENSUS_RND_HOST_ENV:-}" && source "$CONSENSUS_RND_HOST_ENV"`; if the locator is missing or malformed, fail closed and post a status explaining the blocked bootstrap.
4. Arm or confirm the persistent daemon-event Monitor bridge for `.refactor-loop/.controller-pending-events.log` and `.refactor-loop/.concurrency-alert.log`; then read daemon status with `python3 <skill-root>/scripts/consensus-rnd-cli daemon-status --json`;任 owner daemon `stale` / `dead` → 调 `python3 <skill-root>/scripts/consensus-rnd-cli restart-daemons` repair/reload;无 progress >10 min(检 `.refactor-loop/runs/` + `.refactor-loop/logs/` mtime)→ 写 `STALE_CONTROLLER:freeze_minutes=N` 到 `.refactor-loop/.controller-pending-events.log`(no lifecycle authority,仅 alert).
5. Sweep GitHub comments and pending events, excluding sentinel comments, AI banner prefixes, and bot authors.
6. Sweep all recent logs. A worker is complete only when `tail -5 <log>` contains `^EXIT=0`.
7. Parse verdict markers only after `EXIT=0`; marker text in prompt echoes is not a completed verdict.
8. Apply phase routing in the same turn; do not leave an actionable marker for the next wakeup.
9. Post GitHub banner and sync labels for each state transition.
10. Run controller wakeup step 1.5 for the concurrency floor before any `ScheduleWakeup`.
11. Spawn the next codexes with harness background tasks if actionable work exists.
12. Confirm the daemon-event Monitor bridge is still maintained; then confirm any in-flight background task notification or successfully registered ScheduleWakeup fallback that is being used for turn-level completion/fallback.
13. Run `python3 <skill-root>/scripts/consensus-rnd-cli peek | tail -80` again after spawn, merge, banner, or close actions.

`consensus-rnd-cli wakeup-plan` named read-only surface:

- **Allowed**: read `.refactor-loop` files, scan `.refactor-loop/heartbeats/*.ts`, read clean-exit log tails, run read-only GitHub list/check/view commands, and print JSON recommendations.
- **Allowed git topology observation(issue #190 only)**: `git fetch origin --quiet`, `git -C <repo-root> worktree list --porcelain`, `git -C <worktree> rev-parse --verify HEAD`, `git -C <worktree> rev-parse --verify refs/remotes/origin/<head>`, and `git -C <worktree> rev-list --count refs/remotes/origin/<head>..HEAD`, solely for committed-but-unpushed worker output detection on open auto-loop PR heads. Committed `FIX_DONE` / `IMPLEMENT_DONE` output is not reviewer/CI visible until `origin/<head>` contains it; ahead local output emits actionable `UNPUSHED_WORKER_OUTPUT:<pr>:<n>`.
- **Forbidden / no lifecycle authority**: no restart, no spawn, no git lifecycle or mutation commands, no checkout/switch, no branch create/delete/update, no worktree add/remove/prune, no commit, no push, no reset, no rebase, no merge, no label mutation, no issue/PR create-close-edit, no tag/release, and no GitHub lifecycle mutation.
- **Hard-gate**: computes canonical `actual`, `target=max(CODEX_FLOOR, expected_from_active_tasks)`, `deficit=max(0,target-actual)`, and when `deficit>0` emits `HARD_GATE:dispatch_required=N` plus structured `hard_gate`; this is not advisory and requires dispatching enough ordered actionable tasks or legal audit fallback before ending the wakeup. There is no general low-floor exemption, and `AUDIT_DONE:none:0` still does not exempt the floor. The only single active audit boundary is when no actionable open work and no queue candidate exists, expected is 0, and the same-iteration `audit-iter-N` is already active; then the plan emits `WAIT:single-active-audit`, `dispatch_required=0`, `reason=single_active_audit_in_flight`, and `blocked_deficit=N` instead of duplicating the same audit; no duplicate same-iteration audit.
- **Release countdown**: explicit `crnd:milestone:release-target` wins and may add a `release-countdown` status action; when no explicit release-target exists, wakeup-plan may still emit a default goal countdown from open GitHub milestones plus release-gate scoring. Release-countdown status is status-only and not dispatchable.
- Output priority order mirrors the controller checklist: bootstrap or missing wake source, maintainer comment, unpushed worker output, completed `EXIT=0` marker, CI red, no-gap violation, `crnd:milestone:current` open issue/PR, ordinary open existing issue/PR, then producer or audit fixed-point recommendation.
- If no actionable open work exists, it emits `RECOMMEND:audit`; ordinary audit is the floor fallback only when no same-iteration audit is already active.

## Workflow Stage Index

The workflow stage index is the local routing map. It intentionally links to heavy details instead of inlining them.

| Phase | Local controller contract | Detail anchor |
|---|---|---|
| Consensus-rnd Phase bootstrap | Session bootstrap. Must complete before normal routing. | [Consensus-rnd Phase bootstrap details](#bootstrap-details) |
| Consensus-rnd Phase work-intake | Fallback issue production when no actionable managed issue/PR exists; audit is the built-in compatibility producer. | [work-unit contract](#work-unit-contract), [batching heuristics](#batching-heuristics) |
| Consensus-rnd Phase implementation | Implement one codex per active work unit in the batch. Controller owns branch/worktree topology and prompt construction. | [phase routing details](#phase-routing-details) |
| Consensus-rnd Phase verification | Verify with a separate codex from the implementer. Verification may return ok, rework, partial, or blocked. | [recovery playbook](#recovery-playbook) |
| Consensus-rnd Phase publish | Controller commits, merges, pushes, and opens PRs. Workers never commit/push/checkout. | [merge and push details](#merge-and-push-details) |
| Consensus-rnd Phase ci-watch | Watch remote CI after push; classify failures and route fix/test-add work immediately. | [remote CI details](#remote-ci-details) |
| Consensus-rnd Phase integration-sync | Integration sync is daemon-owned autonomous integration-branch git apply through the #53 allowlist. | [daemon command bodies](#daemon-command-bodies) |
| Consensus-rnd Phase design-intake | Sweep design issues and maintainer comments every wakeup. External issues enter through explicit labels or triage. | [design issue details](#design-issue-details) |
| Consensus-rnd Phase review-gate | Three independent PR reviewers; fixes loop until reviewer consensus or meta-layer reflection. | [review-gate details](#review-gate-details) |
| Consensus-rnd Phase design-consensus | Three solvers plus meta-judge. Sole authorization gate for concrete plans. | [design-consensus details](#design-consensus-details) |

## Consensus-rnd Phase bootstrap — Bootstrap (session bootstrap)

Consensus-rnd Phase bootstrap is mandatory and ordered for each controller session bootstrap. Do not spawn normal actors before it completes.

1. `test -n "${CONSENSUS_RND_HOST_ENV:-}" && source "$CONSENSUS_RND_HOST_ENV"` from `$REPO_ROOT`; if the locator is absent, unreadable, or lacks required values, fail closed.
2. Validate `REPO_ROOT`, `GH_REPO_SLUG`, `INTEGRATION_BRANCH`, `REVIEW_BASE_BRANCH`, `BUILD_CMD`, `TEST_CMD`, and `SOURCE_GLOBS` according to host policy.
3. Run `ProjectRulesFixedPointProbe(强制,先于任何 actor 派发)` through `consensus-rnd-cli check-project-rules` against `$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}`.
4. If the probe exits non-zero, including `patch-required` with `.refactor-loop/runs/project-rules-fixed-point.patch`, bootstrap fail closed; post the failure and stop before actors, 不得派 audit / solver / reviewer / implement actor.
5. Create `.refactor-loop/{logs,runs,clusters,prompts,worktrees,state}` if missing; do not create or maintain root `.refactor-loop/state.json`.
6. Ensure the integration branch exists locally and remotely; create it from `$REVIEW_BASE_BRANCH` only when missing.
7. ensure labels for the exact phase/human taxonomy; bootstrap command loops live in [label bootstrap loops](#label-bootstrap-loops).
8. ensure all 7 restart-helper-managed daemons are alive as singletons: `consensus-rnd-cli concurrency`, `consensus-rnd-cli progress-reporter`, `consensus-rnd-cli comment-monitor`, `consensus-rnd-cli dev-sync`, `consensus-rnd-cli phase9-router`, `consensus-rnd-cli closed-label-reconciler`, and `consensus-rnd-cli wakeup-runner`. The persistent daemon-event Monitor bridge is armed separately in step 9.
9. arm persistent daemon-event Monitor bridge for `.refactor-loop/.controller-pending-events.log` and `.refactor-loop/.concurrency-alert.log`.
10. dispatch producer: audit by default, or manual issue intake only when explicit GitHub labels select it.
11. Post a GitHub status card for Consensus-rnd Phase bootstrap completion or blocked state.
12. confirm the daemon-event Monitor bridge is still active before ending; task-notification and ScheduleWakeup are only turn-level completion/fallback signals, not Monitor substitutes.

Consensus-rnd Phase bootstrap anti-patterns stay local because they are safety gates:

- Do not continue with missing `host.env` under guessed defaults.
- Do not skip `ProjectRulesFixedPointProbe` because `$PROJECT_RULES` already exists.
- Do not start fewer than the six required restart-helper-managed daemons.
- Do not initialize an alternate state model, alternate queue, wrapper envelope, root state file, or renamed work-unit schema.
- Do not post local-only bootstrap status; GitHub must show the state.

## Phase Routing

Routing is marker-driven, but markers are trusted only after `EXIT=0` at the tail of the log.

| Finished marker | Same-wakeup controller action |
|---|---|
| `AUDIT_DONE` | Create design issues for `requires_design` units; dispatch direct implement work where allowed. |
| `SOLVER_DONE` from minimal, structural, and delete for same issue/round | Spawn same issue/round meta-judge; this triplet route may be executed directly by `consensus-rnd-cli phase9-router`. |
| `META_JUDGE_DONE:consensus:<framing>` | Post consensus card, move labels, dispatch implement codex. |
| `META_JUDGE_DONE:converge:round-N` | Canonical clean rS judge payload is source round `round-S`; legacy adjacent `round-(S+1)` is accepted temporarily; before dispatching r(S+1), the router-owned stalled predicate may route qualifying r3+ no-progress converge to the stalled reflector and suppress next solvers; non-adjacent payload mismatch falls back; no hard round cap; this route may be executed directly by `consensus-rnd-cli phase9-router`. |
| legacy `META_JUDGE_DONE:escalate:stalled` | Read-only compatibility input only: dispatch meta-reflector only when the same judge-role/source-OPEN/stalled-predicate gates hold; no-framing evidence must be evaluated through the stalled reflector template and preferentially dropped; do not label human directly; this route may be executed directly by `consensus-rnd-cli phase9-router`. |
| `META_RESOLVED:retry-fix` | Dispatch fix with reflector constraints and bounded retry window. |
| `META_RESOLVED:re-design` | `consensus-rnd-cli phase9-router` queues a fresh source-adjacent `marker.round + 1` minimal/structural/delete solver triplet with new framing; no wakeup-runner redispatch. |
| `META_RESOLVED:re-cluster` | Close current PR/issue path and queue re-split. |
| `META_RESOLVED:drop` | Close as no-op/wontfix with explanation. |
| `META_RESOLVED:escalate-human:<reason>` | Only then call `apply_human_label_or_skip` for `crnd:human:maintainer-decision`, passing the full marker source; the helper fails closed without that marker and does not treat local maintainer-directive captures as skip authority. |
| `IMPLEMENT_DONE:ok` | Controller commits/pushes to exactly one matching early managed PR, then dispatches Consensus-rnd Phase review-gate reviewers. |
| `IMPLEMENT_DONE:blocked` | Route to recovery or Consensus-rnd Phase design-consensus depending on reason. |
| Latest complete Consensus-rnd Phase review-gate reviewer round resolves to `MERGE` or `MERGE_WITH_COMMENTS` | Merge path; surface comments for `MERGE_WITH_COMMENTS`. |
| Latest complete Consensus-rnd Phase review-gate reviewer round resolves to `WAIT_EXPLICIT_APPROVAL` | Surface comments and wait; do not merge or dispatch fix. |
| Latest complete Consensus-rnd Phase review-gate reviewer round resolves to `FIX` | Dispatch fix codex for next round using reject evidence as blocking input. |
| Consensus-rnd Phase review-gate gate incomplete or invalid (`WAIT_OR_REDISPATCH`) | Wait or re-dispatch the missing/invalid reviewer; never merge. |
| `FIX_DONE` | Dispatch reviewers again. |
| `TEST_ADD_DONE` | Commit/push and resume CI watch. |

No-gap policy:

1. If an active phase issue/PR exists, at least one this-loop codex should be running unless every active item is truly waiting for maintainer.
2. `0 codex + active task = bug` and must be treated as `no-gap-violation`.
3. Controller 每 wakeup 必派下一步 when actionable; no `wakeup → sweep → 0 spawn → next wakeup` pattern.
4. Controller 严禁自升 escalate. Only the marker routes above can reach a human-needed label.

## GitHub State Contract

GitHub 是系统状态唯一显示面. Local logs and state are implementation details.

Required visible updates:

| State change | GitHub reflection |
|---|---|
| Codex spawned | Status banner on the linked issue/PR and phase/human label sync. |
| Codex completed | Status update describing result and next action. |
| Consensus reached | Consensus card with framing, implementation owner, tests, and scope. |
| Maintainer comment recognized | Daemon or controller status showing it was seen. |
| Reflector decision | `meta-reflector decision` post and label transition. |
| Human escalation | Reason banner with concrete decision options. |
| Phase transition | Exactly one phase label plus one human label. |
| Stuck timeout | Status explaining timeout and reflector dispatch. |
| Iteration complete | Rollup PR banner and next audit dispatch. |
| Skill bug fix | Commit visible on integration branch. |

Status card templates and escalation ASCII diagrams are in [status and escalation templates](#status-and-escalation-templates).

## Sentinel and Comment Sweep

The sentinel is mandatory:

```text
⟦AI:AUTO-LOOP⟧
```

Rules:

1. It must be the final independent line of every AI-authored GitHub comment/body.
2. Controller and daemons must skip comments containing the sentinel.
3. Controller and daemons must also skip AI banner prefixes: `## 🤖`, `## 📊`, `## ✅`, and `## 🆘`.
4. Sweep filters must exclude bot authors and Codecov-style bot bodies.
5. Prompts must require spawned codexes to add the sentinel to any GitHub-facing output.
6. Do not use body prefix alone as the identity filter; sentinel plus author/bot filtering is required.

## Controller = Pure Orchestration

Controller duties:

- Inspect GitHub, logs, state, labels, branches, and worktrees.
- Decide phase route from durable state and completed markers.
- Post banners and sync labels.
- Spawn codex workers.
- Own git topology: commit, merge, push, PR create/merge/close.
- Maintain wake source and concurrency floor.
- Named exception: `consensus-rnd-cli phase9-router` owns only the narrow Consensus-rnd Phase design-consensus allowlist (`SOLVER_DONE` triplet, `META_JUDGE_DONE:converge` including router-derived stalled continuation, legacy read-only `META_JUDGE_DONE:escalate:stalled`, and reflector `META_RESOLVED:re-design` to source-adjacent `marker.round + 1` solver triplet) and appends fallback pending events for everything else.

Controller non-duties:

- Do not edit product/refactor code as the controller.
- Do not run reviewer, solver, implementer, or verifier reasoning inline when a codex role exists.
- Do not fabricate consensus without Consensus-rnd Phase design-consensus.
- Do not hide status in local files only.
- Do not create new runtime abstractions, event envelopes, state versions, or producer registries for this split, except the Consensus-rnd Phase design-consensus-authorized consensus-rnd-cli phase9-router private ledger plus existing-format pending-event append for narrow deterministic Consensus-rnd Phase design-consensus dispatch; do not introduce migrated work-unit schema, public marker aliases, ControllerOrchestrator, ControllerEvent, ControllerCommand, or lifecycle authority.

## Concurrency Floor

The floor is local because it prevents loop stalls.

<!-- Refactor (issue-277): Old: "floor 不足时 audit fallback is mandatory" 未区分合法 audit 与重复 same-iteration audit,和 #205 单 active audit anti-rule 冲突。New: no general low-floor exemption;`AUDIT_DONE:none:0` still does not exempt;ordinary audit fallback 同一时刻一个 same-iteration active slot;slot 占用且无其他 legal work 时 expose blocked_deficit as WAIT,不 duplicate audit。 -->

- `$CODEX_FLOOR` defaults to 5 and has a hard minimum of 2.
- Use `FLOOR=$(( ${CODEX_FLOOR:-5} < 2 ? 2 : ${CODEX_FLOOR:-5} ))`.
- Count only this repository's loop codexes: command line contains `consensus-rnd-cli spawn-codex` and the absolute `$REPO_ROOT`.
- Exclude shell ` -c ` wrapper rows so each real codex counts once.
<!-- Refactor (iter4/skill-count-cli-canonical): Old pattern: controller 手 ps | grep consensus-rnd-cli spawn-codex 重新实现 count_in_flight_codex 逻辑,容易跟 daemon 算法漂移。 New principle: 直接调 `python3 <skill-root>/scripts/consensus-rnd-cli concurrency --count-only` 拿 canonical 整数,或 `--list-codex` 拿每条 supervisor cmdline。禁止 controller 临时 ps/awk pipeline。(2026-05-26 maintainer-directive 等价 Consensus-rnd Phase design-consensus 共识) -->
- **Canonical CLI**(controller 强制使用,**禁止**手 `ps | grep`):
  - `python3 <skill-root>/scripts/consensus-rnd-cli concurrency --count-only` → 打印 canonical 计数(int)并退出
  - `python3 <skill-root>/scripts/consensus-rnd-cli concurrency --list-codex` → 每行一个 supervisor cmdline,scope `$REPO_ROOT` + 排除 ` -c ` wrapper
  - `python3 <skill-root>/scripts/consensus-rnd-cli concurrency --once` → 跑一 tick 退出(替代曾 missing 的 one-shot 入口)
  - 直接读 daemon 日志 `tail -1 .refactor-loop/logs/concurrency_monitor.log` 也是 canonical 来源;两条路径都比 controller 自重算 ps grep 安全。
- 自 PR #<本>: `consensus-rnd-cli concurrency` 不仅 alert; actual < floor 且 dispatch-queue 非空时自动派发(per host 实证 "低于预期数就继续派发"). controller 写 queue 即可,无需自己 ps grep + spawn.
- controller 每次 wakeup 的 step 1.5 checks the count and 必须在任何 `ScheduleWakeup` 之前执行.
- If below floor, consume real work first: existing dispatch queue, then higher-priority actionable marker, then maintainer comment, CI red, no-gap violation, or Consensus-rnd Phase design-intake / Consensus-rnd Phase design-consensus actionable route. "Actionable marker" 限定为:log tail `EXIT=0` 后的完成 verdict (FIX_DONE / REVIEW_DONE / IMPLEMENT_DONE / SOLVER_DONE / META_JUDGE_DONE / TEST_ADD_DONE / AUDIT_DONE / VERIFY_DONE),或新 maintainer comment、CI red、no-gap violation。in-flight codex (没 EXIT=0) 不是 actionable marker——以"等 cascade / fix 完会派 reviewers"为由 defer floor top-up 是绕规则。
- If `deficit>0`, there is no general exemption: dispatch existing/open actionable managed issue/PR work first, then legal fallback issue production through audit only when no higher-priority route exists. The audit fallback remains: envsubst 下一 iteration `prompts/audit.md` 到 `.refactor-loop/prompts/audit-iter-N.md` → `consensus-rnd-cli spawn-codex` 用 harness background task 启动。
- `AUDIT_DONE:none:0` still does not exempt the concurrency floor; when no real queued/actionable open work exists and no same-iteration audit is active, emit `RECOMMEND:audit` and the hard gate line `HARD_GATE:dispatch_required=N`.
- Ordinary audit fallback has one same-iteration active slot. If that slot is occupied and no other legal work exists, expose the remaining capacity as `WAIT:single-active-audit` with `dispatch_required=0`, `reason=single_active_audit_in_flight`, and `blocked_deficit=N`; do not duplicate same-iteration audit; no duplicate same-iteration audit.
- "派 audit 重 / daemon target stale / 等 cascade / 和已有工作冲突" 都不接受作为 defer 理由; the correct visible state for a positive deficit is hard-gate dispatch or the single active audit boundary WAIT, not low-floor exemption.
- **Existing-issue priority(strict)**: Before ordinary audit fallback, dispatch the next-step actor for every open catalog-managed issue/PR (`crnd:lifecycle:managed`, dual-read through catalog aliases during migration) lacking in-flight codex coverage of its canonical phase label; when any such open item carries `crnd:milestone:current`, milestone-labeled next steps come before non-milestone existing-issue work and audit fallback. Concurrent audit against this rule must be killed (`pkill -f audit-iter-N`). Full route table per phase label + audit-fallback gate live in [concurrency floor details](#concurrency-floor-details). Authorization: `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#maintainer-directive-existing-issue-priority-over-audit`.
- **Stale-issue revival(3h)**: Open catalog-managed issue/PR (`crnd:lifecycle:managed`, dual-read through catalog aliases during migration) with `updatedAt` older than 3h UTC MUST be re-dispatched on next wakeup; each re-dispatch posts a banner with `stale_hours=N`. Unlabeled-default route + 3h cutoff details live in [concurrency floor details](#concurrency-floor-details). Authorization: `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#maintainer-directive-stale-issue-3h-revival`.

More detail is in [concurrency floor details](#concurrency-floor-details).

## Milestone priority(强制)

Authorization: `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#maintainer-directive-milestone-priority`.

GitHub label `crnd:milestone:current` marks issue/PRs related to the current period's main task. It is orthogonal third axis beside phase labels and human labels: it changes dispatch priority, not phase semantics, human escalation semantics, marker semantics, or label exclusivity for those axes. Legacy milestone labels are migration aliases only and must be normalized through `codex_refactor_loop.labels`.

GitHub label `crnd:milestone:release-target` marks open catalog-managed issue/PRs whose existence should surface release countdown status and has explicit-target precedence. It is a non-exclusive milestone fact and may coexist with `crnd:milestone:current`; `crnd:milestone:current` remains dispatch priority only and must not trigger explicit release-target mode by itself.

Release countdown is wakeup-plan-only and read-only. `consensus-rnd-cli wakeup-plan` may append a status-only, non-dispatchable `release-countdown` action when an open actionable managed issue/PR has `crnd:milestone:release-target`; this explicit-target path wins and does not query the GitHub milestones API. When no explicit release-target exists, the same wakeup-plan-only action may emit a default goal countdown: it reads GitHub open milestones, chooses the nearest open milestone by `due_on` ascending with no `due_on` sorted after dated milestones and then by milestone `number`, and falls back to `goal.milestone: null` when no open milestone exists. In both activation modes it uses the release-gate scoring source, `.version-bump.json`, and the existing release commits projection for the release goal. Its fields include `activation: "explicit-target" | "default-goal"`, `goal.milestone`, `goal.release`, `goal.release.passed_signals`, `goal.release.total_signals`, `goal.release.countdown_to_version`, `targets`, `from_version`, `to_version`, `stability_score`, `ready`, `red_signals`, `blocked_reasons`, `no_lifecycle_authority`, and `source: "release-gate"`. `host.env`, statusline snapshots, and local state are not a goal SSOT. It must not create a daemon, write state, update statusline, update peek, create a top-level duplicate object, write a release decision, mutate labels, tag, publish a release, or add lifecycle authority.

Milestone active means at least one open catalog-managed issue/PR carries `crnd:milestone:current`. Before any non-milestone existing-issue work or ordinary audit fallback, controller MUST first dispatch the next-step actor for milestone-labeled issue/PRs that lack in-flight codex coverage for their current phase label. Non-milestone design/audit work is downgraded while milestone is active; do not kill already-running non-milestone codexes solely because milestone became active.

Only these actions stay above milestone priority: bootstrap failure / missing wake source, maintainer comment, completed marker same-wakeup route, CI red, and no-gap violation. Correctness still wins: no-gap violation means an active item has no required worker and must be repaired before discretionary prioritization.

When no milestone is active, behavior is unchanged: existing-issue priority runs before audit fallback, then ordinary audit fallback may run only under the existing fixed-point rules.

Fact source is unique: milestone members = GitHub `crnd:milestone:current` as declared by `codex_refactor_loop.labels`. Do not add a parallel state file, queue, marker, local cache, or work-unit field to track milestone membership.

## Named runtime exception — concurrency_monitor auto-topup(per #57)

`skills/codex-refactor-loop/scripts/consensus-rnd-cli concurrency` 的 `top_up_from_dispatch_queue` + tick() deficit 分支 = Consensus-rnd Phase design-consensus / maintainer-directive 等价授权的 controller-runtime intent dispatch 路径(narrow allowlist):

- **Narrow allowlist**: 只在 `actual < max(expected, CODEX_FLOOR)` 且 `.refactor-loop/dispatch-queue/{p0,p1,p2}/*.dispatch.json` 非空时追加 `HARNESS_SPAWN_INTENT`;不读 prompt body、不决定 cd / log path,只消费 controller / actor 入队 spec。
- **Host-agnostic**: dispatch JSON schema host-agnostic;cd/prompt/log path 由入队方决定,不含 host fact。
- **No lifecycle authority**: 不开 / 关 issue / PR,不打 label,不 commit / push;只写 controller-visible intent + 归档 JSON + 写 event log。
- **Behavior tests**: `test_concurrency_monitor` 覆盖 priority order / overshoot prevention / dispatch_one / tick() 整链 / floor 边界 / archive collision / filename-derived task_id。
- **Source-regression**: `test_ensure_project_rules_fixed_points` 字面断言本段标题 + "top_up_from_dispatch_queue" + "DISPATCH_INTENT" + "HARD_GATE:dispatch_required=N" + "narrow allowlist" 等关键字面。

<!-- Refactor (iterissue-330/issue-330):
  Old pattern: daemon nohup spawn bypassed the harness-visible contract; command could mean argv/shell.
  New principle: HARNESS_SPAWN_INTENT.command is closed enum Literal['spawn-codex']; argv is built by controller/harness.
-->
`HARNESS_SPAWN_INTENT.command` is exactly `"spawn-codex"` as a closed semantic enum, not argv and not shell. Valid intents carry `controller_action: "spawn_codex_harness_background"`, repo-relative `cd` / `prompt` / `log`, positive integer `stall`, `run_in_background_required: true`, and `no_lifecycle_authority: true`. Writers and host specs must not add `argv`, `args`, `shell`, `cmd`, `commands`, `env`, `git`, `gh`, `executor`, `target_ref`, `ControllerCommand`, `ControllerEvent`, `SpawnIntentInbox`, `spawn-intents`, or any generic command bus. `wakeup-plan` projects valid intents into controller background-spawn actions and rejects invalid variants; the controller/harness consumption layer constructs the actual `consensus-rnd-cli spawn-codex --cd ... --prompt ... --log ... --stall ...` argv.

授权来源:`skills/codex-refactor-loop/authorizations/runtime-exceptions.md#maintainer-directive-concurrency-auto-topup`(per CLAUDE.md maintainer-directive equivalence 子句,PR #48 merged)。

## Named runtime surface — codex-progress-reporter TEST_NO_LOOP(per #69)

`skills/codex-refactor-loop/scripts/consensus-rnd-cli progress-reporter` supports `TEST_NO_LOOP=1` only as a source-time test seam for `scripts/test_progress_reporter.py`.

- **Allowed**: behavior tests may set `TEST_NO_LOOP=1`, run the packaged reporter inside an isolated tmp repo with stubbed `gh`, and call methods such as `post_or_update` directly.
- **Forbidden**: production daemon startup, controller prompts, cron/launchd helpers, host wrappers, and manual operator runbooks must not set `TEST_NO_LOOP`; it must not be used to skip the daemon loop in a live host.
- **Fact source**: runtime truth remains `.refactor-loop/codex-progress-state.json`, `.refactor-loop/logs/*.log`, and GitHub comment existence via `gh api`. The test seam does not create a new state file, queue, lifecycle authority, or host fact source.
- **Verification**: `python3 -m unittest skills/codex-refactor-loop/scripts/test_progress_reporter.py` covers failed-state and orphan delete retry behavior; `python3 -m unittest discover -s skills/codex-refactor-loop/scripts -p 'test_*.py'` includes source-regression assertions for this narrow surface.

授权来源:`skills/codex-refactor-loop/authorizations/runtime-exceptions.md#maintainer-directive-progress-reporter-orphan-delete`(maintainer-directive for issue #69 orphan progress comments)。

## Spawn Contract

Mainline spawn contract:

1. Use one harness background task per codex. Do not batch multiple codexes inside one detached shell.
2. Invoke `<skill-root>/scripts/consensus-rnd-cli spawn-codex --cd <absolute-dir> --prompt <prompt> --log <log> --stall <seconds>`.
3. `--cd` must be absolute so process counting can scope to `$REPO_ROOT`.
4. Prompt files live under `.refactor-loop/prompts/`; logs live under `.refactor-loop/logs/`.
5. Completion detection primary path is harness task notification; fallback is log tail `EXIT=0` sweep.
6. If a codex was accidentally detached, do not kill and re-dispatch solely to regain tracking. Confirm the log is sweepable and confirm a wake source.
7. Never place a `spawn-codex` background task in the same parallel tool-call batch as any other fallible call (status probe, `grep`, `ls`, marker parse). The harness cancels every sibling call in a parallel batch when one of them exits non-zero, so a failing probe silently cancels the queued spawns and leaves the floor unfilled with no real dispatch. Dispatch each `spawn-codex` in its own tool-call message, isolated from fallible reads.
8. Detailed invocation examples live in [codex invocation details](#codex-invocation-details).

## Label 系统 — 强制

Loop-owned GitHub labels are protocol state. Canonical labels use
`crnd:<group>:<slug>`, where group is exactly
`phase|human|lifecycle|triage|milestone`. The sole fact source is
`codex_refactor_loop.labels` (`scripts/codex_refactor_loop/labels.py`);
do not maintain parallel label truth tables in SKILL.md or prompts.

Managed issues/PRs must have exactly one canonical `crnd:phase:*` label and
exactly one canonical `crnd:human:*` label after migration. GitHub
`external_defaults` may remain for editorial use, but they never satisfy
phase/human/lifecycle/triage/milestone routing semantics. Migration is
controller-owned: add canonical labels first, re-read live labels, validate
exactly-one phase/human, then remove aliases. Workers and daemons must not
mutate labels except the #238 `closed-label-reconciler`, which may mutate only
CLOSED `crnd:lifecycle:managed` item phase/cleanup/stuck labels into exactly
one terminal phase, `crnd:phase:merged` or `crnd:phase:closed`.

Label exclusivity is per `LabelSpec.exclusive_axis`, not per group. Phase and
human labels remain exactly-one axes; non-exclusive catalog labels such as
`crnd:milestone:current` and `crnd:milestone:release-target` may coexist when
`LabelSpec.exclusive_axis is None`.

## `crnd:human:maintainer-decision` 严格语义(强制) <a id="human-label-strict-semantics"></a>

# Refactor (iter4/human-label-semantics-guard): Old pattern: label 当 architect reject workaround. New principle: 严语义 + reflector self-check + controller helper guard + source-regression test.

**Apply only when** maintainer must physically perform an action:
- product/strategy decision that cannot be derived from code/repo
- explicit governance approval(Tier I/II,non-codable)
- manual merge a script cannot execute(rare)

**DO NOT apply when**:
- architect/quality reviewer 因 "needs Consensus-rnd Phase design-consensus artifact" reject → 开真 Consensus-rnd Phase design-consensus(reflector option A)
- reviewer 与 maintainer prior session directive 冲突 → cite checked-in mirror anchor or self-contained GitHub maintainer evidence; local `.refactor-loop/runs/maintainer-directives/<date>-<topic>.md` is raw capture awaiting mirror
- controller uncertain → reflector,不 label
- reflector 自己 emit `META_RESOLVED:escalate-human` 但 controller 复审发现 maintainer 已授权 → 撤 label,以 checked-in maintainer-directive mirror anchor or self-contained GitHub maintainer evidence 替代

**禁止**:把 `👤` label 作 architect/quality reject 的绕路工具。

Hard label rules:

1. Label transition and banner post happen together.
2. Same group only allows one active label.
3. `crnd:human:maintainer-decision` is not a shortcut for controller uncertainty.
4. Legacy human escalation aliases may be removed as cleanup targets only.
5. PRs must carry `crnd:lifecycle:managed` or comment monitoring will miss them after migration.
6. Label protocol details live in [label bootstrap loops](#label-bootstrap-loops).

## Consensus-rnd Phase review-gate — Multi-Codex PR Review

Consensus-rnd Phase review-gate keeps the consensus merge gate local enough for routing:

<!-- Refactor (iter3/skill-merge-policy): Old pattern: unanimous-approve merge gate + Consensus-rnd Phase review-gate 文案矛盾  New principle: 固定真值表 reject=0 && approve>=1 → MERGE;comment 是 advisory(#26 minimal option B 共识) -->

1. Dispatch three reviewers in parallel: architect, tests, quality.
2. Each reviewer posts or emits a `REVIEW_DONE` verdict.
3. Controller computes one fixed action vocabulary after the latest complete required round: `MERGE`, `MERGE_WITH_COMMENTS`, `WAIT_EXPLICIT_APPROVAL`, `FIX`, or `WAIT_OR_REDISPATCH`.
4. Truth table: `reject=0`, `approve=R`, `comment=0` → `MERGE`; `reject=0`, `approve>=1`, `comment>=1`, `approve+comment=R` → `MERGE_WITH_COMMENTS`; `reject=0`, `approve=0`, `comment=R` → `WAIT_EXPLICIT_APPROVAL`; `reject>=1` → `FIX`; missing role, duplicate/unknown verdict, no `EXIT=0`, missing/stale per-reviewer head SHA, CI pending/fail, or non-mergeable PR → `WAIT_OR_REDISPATCH`.
5. `comment` is terminal advisory evidence: surface it, but do not count it as approval and do not dispatch fix for comments alone.
6. `FIX` dispatches fix codex; fix completion dispatches reviewers again.
   Fix prompt rendering binds `REVIEW_ARCHITECT_PATH`, `REVIEW_TESTS_PATH`, `REVIEW_QUALITY_PATH`, and `FIX_OUTPUT_PATH`; the controller passes artifact paths and structured counts/status, not hand-copied reject prose from logs.
7. After repeated fix failure, dispatch meta-layer reflector before any human label.
8. Every Consensus-rnd Phase review-gate action posts to the PR for traceability.
9. Detailed reviewer prompts, retry rules, and anti-spiral safeguards are in [review-gate details](#review-gate-details).

## Consensus-rnd Phase design-consensus — Multi-Solver Design Consensus

Consensus-rnd Phase design-consensus is the sole authorization gate for concrete plans.

1. Dispatch exactly three solver framings by default: minimal, structural, delete.
2. A meta-judge reads all three solver outputs.
3. Concrete implementation authorization requires Step 3 truth-table consensus plus meta-judge `consensus`.
4. `converge:round-N` uses canonical source-round payload from the judge log; source round S and legacy adjacent S+1 both route to r(S+1), while non-adjacent mismatch falls back; no hard round cap.
5. Qualifying r3+ no-progress `converge` routes to reflector via router-owned stalled predicate, not directly to human; legacy `escalate:stalled` markers are compatibility input only.
6. Maintainer replies reset the round when they materially change framing.
7. Any concrete plan bypassing Consensus-rnd Phase design-consensus is invalid.
8. Full consensus artifact posting is worker-owned; controller records completion and next-step structured fields only. Self-post failure is an exceptional diagnostic fallback.
9. Full consensus card template and solver rules are in [design-consensus details](#design-consensus-details).

## Status Banners

Local contract:

- Post status on every spawn, completion, phase transition, consensus, merge, CI failure, block, and human-needed state.
- Use specific phase, current actor, next action, and whether maintainer action is required.
- Never say only vague text such as “processing”.
- Include the final sentinel line.
- Full banner and escalation templates live in [status and escalation templates](#status-and-escalation-templates).

## Loop control

This is an infinite refactor/research loop; do not idle after one iteration completes.

Loop rules:

1. Last cluster merged means roll up state and dispatch next audit/producer pass.
2. Stop only on explicit maintainer stop, unrecoverable bootstrap failure, or approved human-needed escalation.
3. Sync to remote promptly after controller-owned commits.
4. Each wakeup checks CI status for open catalog-managed PRs before sleeping.
5. Each wakeup checks pending daemon events.
6. Each wakeup verifies daemon singleton health.
7. Each wakeup enforces the concurrency floor.
8. Transient stream disconnects route to log sweep and wake-source confirmation, not panic re-dispatch.
9. Recovery cases are in [recovery playbook](#recovery-playbook).

Policy:the loop continues until an explicit stop condition or a visible `crnd:human:maintainer-decision` reason surface is reached.

## Hard rules (controller-level, propagated into every codex prompt)

1. No new features; only clean the authorized violation or implement the consensus plan.
2. No external repo changes; `$EXTERNAL_REPOS` are out of scope unless the user explicitly expands scope.
3. Code refactor rationale follows `$HOST_REFACTOR_COMMENT_POLICY`: missing, empty, or default policy is `none`, which forbids refactor-history source comments and keeps rationale in external artifacts; explicit `self-doc-comment` is a downstream compatibility opt-in and must still obey source English-only.
4. No `commit`, `push`, `checkout`, PR create/merge, or issue close inside worker prompts; controller owns git topology.
5. No sleep/delay-based test pacing; use deterministic awaiters.
6. No `[Skip]`, disabled tests, ignored tests, or manual category escapes to make CI green.
7. No scope creep; workers must print `SCOPE_EXTEND: <file> <reason>` before touching outside authorized scope.
8. Source files are English-only; external user-facing artifacts are 中文 by default. The root README pair is the only English-canonical public-doc carve-out: `README.md` is English canonical, `README.zh-CN.md` is the 中文 companion, and GitHub issue/PR/commit/design artifacts remain 中文 by default. No mandatory parallel English section.
10. Do not hardcode host facts into this cross-platform skill.

Details are in [hard rules details](#hard-rules-details).

## 工作语言规则(源码内英文,源码外中文)

<!--
Refactor (iter343/issue-343):
  Old pattern: README 单一(非英文默认),CLAUDE.md 文档分层称 README 为权威源;无英文 canonical + 中文 companion 双文件,语言策略未给 README pair carve-out
  New principle: README.md 英文 canonical 公开身份文档 + README.zh-CN.md 中文 companion(双向交叉链接,大段顺序对齐不要求逐句对等);CLAUDE.md 文档分层/根.md收口/语言 carve-out 与 SKILL.md 语言策略窄改:README pair 是唯一英文-canonical 公开文档 carve-out,GitHub issue/PR/commit/design artifact 等工作态仍中文默认。严格按 DESIGN_DECISION_PATH verbatim Concrete plan;不碰 .version-bump.json/额外根文档/runtime/host.env/marker/daemon/workflow
-->
Policy: Source files are English-only; external user-facing artifacts are 中文 by default. The root README pair is the only English-canonical public-doc carve-out. No mandatory parallel English section.
Operational details live in [language policy details](#language-policy-details); historical bilingual notes live in [historical bilingual notes](#historical-bilingual-notes).

## Files

- [prompts/audit.md](prompts/audit.md) — audit producer prompt.
- [prompts/implement.md](prompts/implement.md) — implement worker prompt.
- [prompts/verify.md](prompts/verify.md) — verify worker prompt.
- [prompts/remote-ci-fix.md](prompts/remote-ci-fix.md) — remote CI fix prompt.
- [prompts/test-add.md](prompts/test-add.md) — codecov/test-add prompt.
- [prompts/meta-reflector-stalled.md](prompts/meta-reflector-stalled.md) — meta-reflector self-check prompt for stalled routes.
- [prompts/design-issue-body.md](prompts/design-issue-body.md) — design issue body template.
- [prompts/design-issue-reply.md](prompts/design-issue-reply.md) — maintainer-comment analyst prompt.
- [prompts/reviewer-architect.md](prompts/reviewer-architect.md) — Consensus-rnd Phase review-gate architecture reviewer.
- [prompts/reviewer-tests.md](prompts/reviewer-tests.md) — Consensus-rnd Phase review-gate tests reviewer.
- [prompts/reviewer-quality.md](prompts/reviewer-quality.md) — Consensus-rnd Phase review-gate quality reviewer.
- [prompts/review-fix.md](prompts/review-fix.md) — Consensus-rnd Phase review-gate fix worker.
- [prompts/solver-minimal.md](prompts/solver-minimal.md) — Consensus-rnd Phase design-consensus minimal solver.
- [prompts/solver-structural.md](prompts/solver-structural.md) — Consensus-rnd Phase design-consensus structural solver.
- [prompts/solver-delete.md](prompts/solver-delete.md) — Consensus-rnd Phase design-consensus delete/collapse/abstain solver.
- [prompts/meta-judge.md](prompts/meta-judge.md) — Consensus-rnd Phase design-consensus meta-judge.
- [scripts/consensus-rnd-cli spawn-codex](scripts/consensus-rnd-cli spawn-codex) — codex supervisor.
- [scripts/consensus-rnd-cli peek](scripts/consensus-rnd-cli peek) — controller wakeup summary.
- `scripts/codex_refactor_loop/controller_actions.py` — controller-internal lifecycle primitives; not a public CLI command surface.
- `ControllerActions.post_status_banner` — controller-internal GitHub banner posting helper.
- [scripts/consensus-rnd-cli check-project-rules](scripts/consensus-rnd-cli check-project-rules) — read-only Consensus-rnd Phase bootstrap fixed-point probe; writes patch artifact only.
- [scripts/consensus-rnd-cli concurrency](scripts/consensus-rnd-cli concurrency) — no-gap sentinel daemon.
- [scripts/consensus-rnd-cli restart-daemons](scripts/consensus-rnd-cli restart-daemons) — cron/launchd anti-stop helper for existing daemon wrappers.
- [scripts/consensus-rnd-cli log-retention](scripts/consensus-rnd-cli log-retention) — daemonless 24h direct-rm helper for `.refactor-loop/logs/*.log`.
- [scripts/consensus-rnd-cli progress-reporter](scripts/consensus-rnd-cli progress-reporter) — progress comment daemon.
- [scripts/consensus-rnd-cli comment-monitor](scripts/consensus-rnd-cli comment-monitor) — maintainer comment monitor.
- [scripts/consensus-rnd-cli dev-sync](scripts/consensus-rnd-cli dev-sync) — integration sync daemon.
- [scripts/consensus-rnd-cli phase9-router](scripts/consensus-rnd-cli phase9-router) — narrow Consensus-rnd Phase design-consensus direct-dispatch daemon.
- [Detailed reference](#detailed-reference) — heavy runbooks, templates, schemas, and recovery details in this file.

## Controller Wakeup Checklist

Use this checklist literally on each wakeup:

1. Mechanically call `python3 <skill-root>/scripts/consensus-rnd-cli wakeup-plan --repo-root "$REPO_ROOT"` and execute from its prioritized output.
2. Peek for human-readable status.
3. Load host config.
4. Check pending daemon events.
5. Sweep GitHub comments with sentinel/bot filters.
6. Sweep log tails for `EXIT=0`.
7. Parse markers only after clean exit.
8. Route all actionable completions.
9. Post GitHub status before or with spawned work.
10. Sync phase and human labels.
11. Check open PR CI failures.
12. Verify daemon singleton health.
13. Enforce floor before sleep.
14. Spawn next codexes with harness tracking.
15. Commit/push only when controller-owned lifecycle requires it.
16. Confirm wake source, including maintaining the daemon-event Monitor bridge.
17. Peek again after visible actions.
18. End only when GitHub reflects the current state.

Priority order when multiple actions are possible:

1. Bootstrap failure or missing wake source.
2. Maintainer comment that changes design framing.
3. Completed worker marker ready for same-wakeup route.
4. CI red on open catalog-managed PR.
5. No-gap violation.
6. Milestone-labeled open catalog-managed issue/PR next-step dispatch.
7. Non-milestone existing-issue next-step dispatch.
8. Floor deficit.
9. Producer dispatch for next work unit.
10. Ordinary audit fallback.
11. Routine ScheduleWakeup.

When uncertain:

- Prefer a reversible status post plus a correctly scoped codex dispatch.
- Prefer Consensus-rnd Phase design-consensus for design uncertainty.
- Prefer reflector for repeated AI-loop disagreement.
- Prefer recovery playbook for operational failures.
- Never invent a human gate just because the controller is tired.

## Durable State Contract

The local state file is a recovery aid, not the maintainer-facing state surface.

Authoritative surfaces:

1. GitHub comments and labels tell humans what is happening.
2. `.refactor-loop/logs/*` tells the controller which actors exited cleanly; verdict markers are trusted only after `EXIT=0`; `.refactor-loop/logs/*.log` is a 24h short-lived surface, not history.
3. `.refactor-loop/prompts/*` tells future maintainers what was dispatched.
4. Branches, worktrees, and PRs tell git topology.
5. Named specialized artifacts such as `.refactor-loop/state/statusline-snapshot.json`, `.refactor-loop/state/phase8-review-state.json`, `.refactor-loop/state/recent-pr-merges.json`, `.refactor-loop/codex-progress-state.json`, `.refactor-loop/comment-monitor-state.json`, and `.refactor-loop/.concurrency-monitor-state.json` are owned by their producers.

State rules:

1. Do not create or maintain root `.refactor-loop/state.json`; it owns no phase decisions, queues, resumability index, or debug ledger.
2. Do not add queue aliases, envelope wrappers, normalizer helpers, or a migration for a root state file.
3. For audit-backed work, keep `work_unit_id == id == cluster_id` during compatibility.
4. For manual issue work, do not fabricate `cluster_id`; use `work_unit_id: issue-<N>`.
5. Prompt dispatch may keep `WORK_UNIT_ID=$CLUSTER_ID` for current audit-backed units.
6. Public operational names remain stable: `cluster`, `refactor`, `*_DONE`, branch prefixes, marker names, and catalog-owned canonical label names. Legacy label names remain migration aliases only.
7. Specialized artifact examples live in [specialized state artifacts](#specialized-state-artifacts).

State write timing:

1. Before spawn, write the prompt artifact and post/sync the GitHub-visible phase state.
2. After spawn, rely on harness task notification plus log path and GitHub banner/labels.
3. After completion, route the clean-exit marker in the same wakeup and update GitHub plus any named producer-owned artifact.
4. After PR creation, record PR number and base/head.
5. After merge, record merged commit and close/label state.
6. After a recovery decision, record the reason string and next route.

## Producer Contract

The controller recognizes two producers:

| Producer | Intake | Controller behavior |
|---|---|---|
| `audit` | Compatibility audit/refactor fallback issue producer. | Run audit prompt only when no actionable managed issue/PR or higher-priority route exists; project accepted clusters into managed issues or work-unit items, then feed the main path. |
| `manual-issue` | Explicit managed GitHub issue intake for main-path resolution. | Normalize problem and verification hints into a design issue, then use Consensus-rnd Phase design-consensus. |

Producer rules:

1. Issue/PR resolution is the main-path state, not a producer that creates new work.
2. Audit runs only as a compatibility fallback issue producer when no actionable managed issue/PR, queued dispatch, clean marker route, CI/no-gap route, maintainer-comment route, or higher-priority wakeup route exists.
3. Manual issues enter only through explicit maintainer label or triage monitor routing.
4. `requires_design` audit clusters open GitHub issues and do not auto-implement until Consensus-rnd Phase design-consensus consensus.
5. Direct implementation is allowed only for clusters already authorized by policy and not requiring design.
6. Batching should prefer independent, low-risk work and preserve dependency ordering.
7. Detailed producer fields and batching heuristics live in [work-unit contract](#work-unit-contract) and [batching heuristics](#batching-heuristics).

## Phase Guardrails

Consensus-rnd Phase work-intake guardrails:

This stage covers fallback issue production through the audit compatibility producer. The main issue/PR resolution path does not start here; it starts from open actionable managed GitHub issues and PRs already visible to the controller.

1. Run the producer with host-injected `$SOURCE_GLOBS`.
2. Write audit output to `.refactor-loop/runs/audit-iter-N.md`; `N` must be nonempty and unique among currently active audit fallback runs.
3. Convert accepted units into work-unit items before dispatch.
4. Clean stale worktrees before audit pollution can affect decisions.
5. For `requires_design`, open or update GitHub design issues and label them `crnd:phase:design-solving` plus `crnd:human:auto`.

Consensus-rnd Phase implementation guardrails:

1. One implement codex per work unit in the current batch.
2. Each work unit gets an isolated worktree and branch.
3. Prompt includes scope paths, old pattern, new principle, verification hints, hard rules, language rule, and sentinel rule.
4. Implement codex must not commit, push, create PRs, merge, or close issues.
5. `IMPLEMENT_DONE:ok` means the controller may inspect diff, run host checks, commit, push to the pre-opened managed PR, and advance.

Consensus-rnd Phase verification guardrails:

1. Verifier is independent from implementer.
2. Verification uses `$BUILD_CMD`, `$TEST_CMD`, and optional `$CI_GUARDS`.
3. Optional guards require `[ -n "${CI_GUARDS:-}" ]`; otherwise report `guards skipped: CI_GUARDS unset`.
4. Rework returns to implement/fix routing, not human by default.
5. Verification logs still require tail `EXIT=0`.

Consensus-rnd Phase publish guardrails:

1. Controller owns commit, merge, push, PR create, PR close, and PR merge.
2. Workers never run git lifecycle commands unless a prompt explicitly says a command is forbidden context.
3. Re-read current branch and worktree before merge to avoid cwd leaks.
4. After merge/push/open PR, post status and run `consensus-rnd-cli peek`.
5. Stacked PR mode and single PR mode details live in [merge and push details](#merge-and-push-details).

Consensus-rnd Phase ci-watch guardrails:

1. Every wakeup checks all open catalog-managed PR CI before sleeping.
2. Red CI routes immediately to classification and fix/test-add dispatch.
3. Pre-existing failures are reported, not blindly fixed in the PR.
4. Codecov patch failures route to test-add work.
5. Repeated same-check failure routes through meta-layer policy before human escalation.

Consensus-rnd Phase integration-sync guardrails: integration sync is daemon-owned autonomous integration-branch git apply through the #53 allowlist. The daemon writes integration sync operation artifacts, re-checks live state before git mutation, and records execution results under `.refactor-loop/runs/integration-sync-executions/`. Daemon command details live in [daemon command bodies](#daemon-command-bodies).

## Named runtime exception — integration sync daemon integration-sync controller boundary
The integration sync daemon owns detection, conflict detection, resolver dispatch, heartbeat, pending-event append, integration sync operation artifact emission, and autonomous #53 git apply in its dedicated integration worktree. The executor must reject stale SHA, branch mismatch, dirty non-merge worktrees, invalid rollup ancestry, unresolved merges, malformed operations, or already-executed operations. Resolver codexes resolve and stage conflicts only; they never push, reset, continue, or abort.

Consensus-rnd Phase design-intake guardrails:

1. Sweep design issues every wakeup.
2. Maintainer replies that materially change framing reset the Consensus-rnd Phase design-consensus round.
3. Bot comments and AI sentinel comments do not count as maintainer input.
4. External issues require explicit opt-in labels; controller wakeup sweep dispatches triage and applies manual issue triage decision artifacts.
5. Do not auto-implement from a free-form issue without Consensus-rnd Phase design-consensus consensus.

Consensus-rnd Phase review-gate guardrails:

1. Dispatch architect, tests, and quality reviewers in parallel.
2. Reviews are tied to a PR head SHA.
3. Any reject produces a fix round unless meta-layer reflection is triggered.
4. Reviewer consensus must be visible on the PR.
5. Re-review after push; do not reuse stale approval across materially changed heads.

Consensus-rnd Phase design-consensus guardrails:

1. Minimal, structural, and delete solvers run for each design round.
2. Meta-judge consumes all three outputs.
3. Only Step 3 truth-table consensus plus meta-judge `consensus` authorizes implementation.
4. `converge` means more solver work, not human escalation.
5. `stalled` means reflector, not human escalation.
6. Maintainer input can reframe the next round, but the controller does not synthesize a concrete plan alone.

## Recovery Triage

Use this local triage before opening the heavy recovery playbook:

| Symptom | First controller action | Reference |
|---|---|---|
| Codex log has no tail `EXIT=0` | Treat as still running or crashed; do not parse markers. | [recovery playbook](#recovery-playbook) |
| Prompt marker appears in log body | Ignore until clean exit and filtered real marker. | [phase routing details](#phase-routing-details) |
| Worktree merge says already up to date unexpectedly | Check cwd and branch before retrying. | [recovery playbook](#recovery-playbook) |
| Remote CI monitor appears stuck | Check PR checks directly and dispatch fix if red. | [remote CI details](#remote-ci-details) |
| No codex running with active work | Treat as no-gap violation and spawn next route. | [concurrency floor details](#concurrency-floor-details) |
| Repeated reviewer/fix loop | Dispatch reflector before human label. | [review-gate details](#review-gate-details) |
| Design consensus not converging | Continue rounds or reflector according to judge marker. | [design-consensus details](#design-consensus-details) |
| Maintainer says stop | Stop visibly, leave state/logs intact, and do not schedule wakeup. | [recovery playbook](#recovery-playbook) |

Recovery rules:

1. Preserve useful in-flight work whenever possible.
2. Prefer idempotent re-checks over destructive cleanup.
3. Do not delete worktrees or branches unless their PR/branch state proves they are stale.
4. Do not rewrite history on shared branches.
5. Post what happened and what the controller will do next.

## GitHub Posting Contract

<!--
Refactor (iter6/issue-118):
  Old pattern: skill docs maintained a posting-mode prompt filename roster,会漂移
  New principle: prompt-self-declaration posting mode is owned by the GitHub Posting Contract, prompts/_github-post-rules.md, prompt body self-declaration, test_marker_only_prompts_gh_ban.py, and test_marker_emission_contract.py; no SKILL-maintained prompt filename roster.
-->

Posting rules:

1. Controller posts lifecycle banners directly.
2. A worker prompt is direct-post only when its own body contains `## GitHub post` and the fixed token `{{GITHUB_POST_RULES_CONTRACT}}`; `_github-post-rules.md` is the template-time source, and the rendered worker prompt inlines its shared rules body. The rules file is not a worker runtime path.
3. Every GitHub body uses the sentinel final line.
4. Avoid plain-text unverified human names or handles.
5. `SKILL.md` must not maintain a posting-mode prompt filename roster; inventory tests derive posting mode from prompt bodies.
6. Direct-post permission is limited to GitHub comments, PR body edits, reactions, and temp files; lifecycle, labels, create/close/merge, push, and release stay controller-owned.
7. Whitelisted mentions come from `$MAINTAINER_WHITELIST`.
8. Label changes require a banner explaining the reason.

## Anchor Read Policy

This single file is intentionally enough for controller routing. Jump to detailed anchors only when the current phase needs the heavy body.

When to read an anchor:

1. Before writing a full status or escalation banner, read [status and escalation templates](#status-and-escalation-templates).
2. Before editing producer normalization or named state artifacts, read [work-unit contract](#work-unit-contract) and [specialized state artifacts](#specialized-state-artifacts).
3. Before starting or repairing daemons, read [daemon command bodies](#daemon-command-bodies).
4. Before changing label bootstrap or transition helpers, read [label bootstrap loops](#label-bootstrap-loops).
5. Before handling repeated failure, stuck CI, merge conflicts, or stale worktrees, read [recovery playbook](#recovery-playbook).
6. Before changing language policy in prompts, read [language policy details](#language-policy-details).

When not to read an anchor:

1. Do not load every detailed reference section at startup.
2. Do not require agents to use forced-load reference syntax.
3. Do not link to absolute local paths.
4. Do not copy heavy templates into short controller sections to solve a one-off routing question.

## Controller Ownership Boundaries

Controller-owned operations:

1. Create runtime directories and honor named producer-owned state artifacts; do not create a root state queue.
2. Create worktrees and branches for worker tasks.
3. Render worker prompts from stable prompt files, GitHub state, and source artifacts.
4. Spawn codex workers and track prompt/log paths.
5. Commit worker diffs after verification.
6. Push branches and open/update PRs.
7. Merge PRs after review/CI criteria are satisfied.
8. Close issues/PRs only through explicit route outcomes.
9. Post status, consensus, progress, and escalation banners.
10. Add/remove phase and human labels.
11. Maintain daemon singleton health.
12. Maintain the daemon-event Monitor bridge.
13. Schedule or confirm the next wake source.

Worker-owned operations:

1. Analyze assigned source/design material.
2. Edit files inside assigned scope.
3. Run assigned local checks.
4. Produce marker verdicts and artifacts.
5. Explain blockers with enough evidence for controller routing.
6. Avoid git lifecycle operations.
7. Avoid changing prompt, daemon, or manifest contracts unless specifically assigned.

Maintainer-owned operations:

1. Change the host policy or project rules, including applying any fixed-point patch artifact.
2. Approve a real human-needed decision after `META_RESOLVED:escalate-human`.
3. Stop the loop.
4. Expand scope beyond the current work unit or repo.
5. Change product/philosophy boundaries that Consensus-rnd Phase design-consensus cannot decide.

## Detailed reference

Detailed specifications, heavy templates, schemas, command bodies, and recovery playbooks formerly kept in a separate reference file. Keep host-specific facts injected by `host.env`; do not add absolute local paths or platform-specific installation facts here.

<a id="controller-contract-details"></a>
## Controller contract details

The following excerpts preserve the detailed controller runbook in the single SKILL.md file. Keep host-specific facts injected by `host.env`; do not add absolute local paths or platform-specific installation facts here.

<a id="release-decision-schema"></a>
### Release decision schema

`consensus-rnd-cli release-gate` is a one-shot controller helper, not a daemon. It reads host.env through `LoopContext` or the shared parser in `context.py`: `CONSENSUS_RND_HOST_ENV` is the only runtime locator for host facts, and root `host.env` or `.refactor-loop/host.env` are not migration reads. Only `RELEASE_AUTO_ENABLE=true` enables decision writes or `--dispatch` candidate writes. `--score-only` prints the same stability calculation without requiring opt-in and without writing state. A controller-side pre-gate producer writes `.refactor-loop/state/release-commits.json` before the decider runs; the decider is decision-artifact-only and does not run `git`, and the controller-owned publisher owns any manifest bump, commit, push, tag, or release action after publish preflight. Controller scheduling order is fixed: first run `consensus-rnd-cli release-commits --target-ref origin/$REVIEW_BASE_BRANCH`, then run `consensus-rnd-cli release-gate`.

Stability score is the percentage of the eight boolean signals that pass. `ready=true` requires score 100 plus the release interval and at least one commit since the last release. Live signal inputs are intentionally narrow:

| Signal key | Pass condition |
|---|---|
| `required_checks_recent_green` | Shared Checks API projection sees exact check-run name success for every name in `$HOST_GITHUB_RELEASE_REQUIRED_CHECKS` on both `$REVIEW_BASE_BRANCH` and `$INTEGRATION_BRANCH` (host.env) within two hours; auto-release with an empty list fails closed. |
| `no_open_blocked_pr` | No open PR has `crnd:phase:blocked`. |
| `no_human_decision_label` | No open issue or PR has `crnd:human:maintainer-decision`. |
| `no_phase8_reject_churn` | `.refactor-loop/state/phase8-review-state.json` reports fewer than three consecutive reject rounds. |
| `p0_alert_streak_ok` | `.refactor-loop/.concurrency-monitor-state.json` zero streak and recent P0 alert lines are both at most 3 in the last 30 minutes. |
| `recent_pr_merges_min` | `.refactor-loop/state/recent-pr-merges.json` reports at least `RELEASE_AUTO_MIN_MERGES` commits in the last two hours(default 1). `merge_pr` produces the controller-owned projection after successful `gh pr merge`; schema fields are `count/window_hours/updated_at/merges[]`; the decider only reads this artifact. |
| `fresh_heartbeats` | Every restart-managed daemon has a `.refactor-loop/heartbeats/*.ts` file fresh within 90 seconds. |
| `no_unresolved_human_escalation` | `.refactor-loop/state/meta-resolutions.json` has zero `unresolved_escalate_human` entries. |

Tests or controller-side aggregators may write `.refactor-loop/state/auto-release-signals.json` with either booleans or `{ "passed": bool, ... }` objects for those same keys. When that file exists, it is the deterministic source for the eight gate signals.

Semver bump is computed from `.refactor-loop/state/release-commits.json` entries since the latest release: `feat!:` or `BREAKING CHANGE:` yields `major`; otherwise any `feat:` yields `minor`; otherwise `fix:`, `perf:`, `refactor:`, or any other commit yields `patch`. `next_release_version()` computes `to_version` from that `bump_type`; on a prerelease base such as `X.Y.Z-beta.N` or `X.Y.Z-rc.N`, the default target is same-stage `N+1`. `bump_type` is commit-impact metadata, not promotion authority: it does not authorize beta-to-rc, beta-to-GA, rc-to-GA, or core promotion while on the prerelease ladder. If stability is not ready, no commits exist, or the minimum release interval has not elapsed, `bump_type` is null and `to_version == from_version`.

`.refactor-loop/state/release-decision.json` fields:

| Field | Meaning |
|---|---|
| `from_version` | Current synchronized manifest version from `.version-bump.json`. |
| `to_version` | Next version when ready, otherwise `from_version`. |
| `bump_type` | `major`, `minor`, `patch`, or null when no release should be applied. |
| `commits` | Commit SHA and subject list since the latest release tag. |
| `decided_at` | UTC timestamp for the decision. |
| `stability_score` | Integer 0-100 score from the eight signals. |
| `signals` | Per-signal pass/fail evidence. |
| `ready` | True only when all stability, interval, and commit gates pass. |
| `blocked_reasons` | Failed signal keys plus `min_interval` or `no_commits_since_last_release` when applicable. |
| `release_interval` | Last release timestamp, minimum hours, elapsed seconds, and pass/fail status. |
| `release-candidate.json` | Separate artifact written by `--dispatch`; contains the decision path, target version, target ref, expiry, decision digest, required signal projection, host opt-in hint, publish preflight name, and controller lifecycle owner for controller publisher consumption. |

<a id="host-runtime-details"></a>
## Host 运行编排(daemon 启动 + 运行节奏适配)(强制)

dogfood 运行中固化的操作经验。host 注入的 loop runtime 配置集中放 host-owned `CONSENSUS_RND_HOST_ENV`(`export REPO_ROOT/GH_REPO_SLUG/INTEGRATION_BRANCH/REVIEW_BASE_BRANCH/BUILD_CMD/TEST_CMD/CI_GUARDS/SOURCE_GLOBS/MAINTAINER_WHITELIST` 等)。

<a id="skill-degradation-source-repo-validation-details"></a>
### Skill degradation source-repo validation details
The skill-degradation checker is intentionally source-repo scoped: no standalone watchdog, no seventh daemon, no `DegradationCheck` protocol, no plugin registry, no new event envelope, no auto-clean, no auto-fix, no GitHub lifecycle mutation, and no codex dispatch path.
Static checker: `python3 skills/codex-refactor-loop/scripts/consensus-rnd-cli check-degradation --static`; CI job `.github/workflows/consensus-rnd-ci.yml` `skill-degradation`; release gate `consensus-rnd-cli release-gate:required_checks_recent_green` does not name source-repo CI jobs directly. It consumes `required_release_checks()` from `$HOST_GITHUB_RELEASE_REQUIRED_CHECKS`, so each host configures the exact required GitHub check-run names in `host.env`; this source repo may list its own CI names in its host-owned `host.env`, but that example is not downstream runtime authority. The checker is read-only and returns nonzero on missing source-repo validation text, CI/release wiring, forbidden runtime files, forbidden expansion surfaces, or downstream runtime watch markers.
Downstream plugin-installed hosts have no skill-degradation runtime watch, no degradation alert log, no degradation pending event, no degradation peek lens, and no degradation host.env knobs. `consensus-rnd-cli concurrency` must not invoke `check-degradation` as a runtime watch against a host repo root.
Forbidden: no source mutation, git operations, GitHub issue/PR/body/label lifecycle mutation, codex dispatch, standalone daemon creation, WorkUnit/schema/envelope changes, protocol/plugin registry, auto-clean root garbage, and auto-fix API.
### Worktree 位置约定(强制)

所有 daemon/codex/implement worktree 都在 `$REPO_ROOT/.worktrees/` 内,路径形如 `$REPO_ROOT/.worktrees/<name>/`。仓库根 `.gitignore` 必须包含 `/.worktrees/`,因此这些运行时 worktree 不进入发布产物。旧 sibling pattern `<repo>-wt-<name>/` 只作为历史兼容/清理线索出现,不得作为新 worktree 创建位置。

### Daemon 启动(强制 pattern — 必须注入 host.env)

**禁止** 裸 `nohup python3 <daemon> &`(拿不到 host 配置)与 `nohup env $(grep ... host.env) <daemon> &`(`BUILD_CMD` 含空格时 `env` 会把后续 token 当命令崩)。**唯一正确**:用 `bash -c 'test -n "${CONSENSUS_RND_HOST_ENV:-}" && source "$CONSENSUS_RND_HOST_ENV" && exec'` 注入后再 exec:
```bash
nohup bash -c 'test -n "${CONSENSUS_RND_HOST_ENV:-}" && source "$CONSENSUS_RND_HOST_ENV" && exec python3 <skill-root>/scripts/consensus-rnd-cli <operation> --daemon' \
  >> .refactor-loop/logs/<daemon>.log 2>&1 & disown
```
Integration sync uses integration sync operation artifacts; the daemon writes `.refactor-loop/runs/integration-sync-operation-<kind>-<ts>.json` and records `.refactor-loop/runs/integration-sync-executions/<operation-stem>.(applied|rejected).json` after live-state validation.
**7 个长跑 daemon 全部要起**(监控面 = 这 7 个):`consensus-rnd-cli concurrency`(60s codex 并发)、`consensus-rnd-cli progress-reporter`(600s 进度回贴)、`consensus-rnd-cli comment-monitor`(30s maintainer 评论 eyes-react)、`consensus-rnd-cli dev-sync`(600s integration sync operation executor)、`consensus-rnd-cli phase9-router`(30s narrow Consensus-rnd Phase design-consensus deterministic routing)、`consensus-rnd-cli closed-label-reconciler`(1800s closed managed item terminal phase-label reconciliation)、`consensus-rnd-cli wakeup-runner`(60s #396 closed action projection apply)。

`consensus-rnd-cli restart-daemons` also runs daemonless log retention before daemon freshness checks. `consensus-rnd-cli log-retention` has no lifecycle authority: it reads the host-owned `host.env`, targets only `$REPO_ROOT/.refactor-loop/logs/*.log`, and directly removes regular log files older than 24h. It must not create archive/index state, scan or delete `.refactor-loop/runs/` or prompts, call GitHub, run git, spawn codex, or become a daemon. Verification lives in `test_log_retention.py`.
<!-- Refactor (iter215/cluster-215-controller-process-selftest):
  Old pattern: Controller runbook still instructs ps|grep/pgrep liveness checks,与 SKILL.md canonical CLI 与 CLAUDE.md daemon-counts-authority 子句矛盾。
  New principle: Controller-facing 检查必须读 daemon-maintained state / heartbeat / canonical script CLI(consensus-rnd-cli restart-daemons / consensus-rnd-cli peek / consensus-rnd-cli concurrency);process probes 留在 daemon / helper 实现内部,不在 controller runbook 段。
-->
**单例强制**(尤其 `dev_sync_daemon` 多实例会 race):controller 不做 process probe。每 wakeup 读 `python3 <skill-root>/scripts/consensus-rnd-cli daemon-status --json` / `.refactor-loop/state/statusline-snapshot.json`;任 owner daemon `stale` / `dead` 时调用 `python3 <skill-root>/scripts/consensus-rnd-cli restart-daemons` 让 helper 内部维护 singleton + restart/reload。`consensus-rnd-cli phase9-router` 的 singleton 由自身 lock/ledger/fallback event contract 维护;controller 只读其 log/ledger/pending event surface,未知状态走 fallback sweep。

### Controller 主链路 wake 源不变量(强制,精化 detached 规则)

controller 主链路**优先禁止**用 `( … ) & disown` / `nohup … &` / Bash 内 `consensus-rnd-cli spawn-codex ... &` 派 **codex**(即使为省 tool 调用想批量)。detached 进程丢的是 harness 即时 `<task-notification>`,**不是检测能力**:controller 每次 wakeup 的 `EXIT=0` / marker log sweep 仍能扫到 detached codex 的完成,只是延到下次 ScheduleWakeup,变慢。

**真正致命**的是 `detached + 无 active daemon-event Monitor bridge`。单独 detached 只是慢;detached 后又没有 session-level Monitor,loop 才会漏掉 daemon events。task-notification / ScheduleWakeup 只能覆盖 turn-level completion/fallback,不能替代每个 controller session 必须维护的 Monitor bridge。

**铁律**:
- controller 主链路 codex **优先**用 **一个 `Bash run_in_background:true`** 跑一个 `consensus-rnd-cli spawn-codex`(N 个 codex 就 N 个调用),拿即时 task-notification。
- 若 codex 意外被 detached,**不要 panic-kill 后重派**。这会浪费已跑工作;sweep 会在下次 wakeup 接住。正确动作是确认 log 路径可扫,并确认 session-level Monitor bridge 仍 active。
- 每个 controller session **必须先维护** active daemon-event Monitor bridge;每个 turn 结束还要确认 task-notification 在飞或 ScheduleWakeup 返回 `scheduled` 作为 completion/fallback。后两者不是 Monitor substitute。
- daemon 自主动作可以 detached,但必须同时满足:prompt 落 `.refactor-loop/prompts/`,log 落 `.refactor-loop/logs/`,状态写 GitHub 或 pending event 可恢复,daemon 单例,并受 `consensus-rnd-cli peek` / liveness 检查。
- daemon alone is not a wake source; daemon event files become a wake source only through a mounted Monitor bridge.
**反面(❌ 严禁)**:
- ❌ controller session 没有 active Monitor bridge,只靠 task-notification 或 ScheduleWakeup end turn。
- ❌ 误以为 `consensus-rnd-cli concurrency` / progress reporter / comment monitor 单独会唤醒 controller。daemon **只写 alert 文件 / GitHub 评论 / pending event**;只有 mounted Monitor bridge 把 daemon event file 转成 controller wakeup 时,daemon events 才是 wake 源。
- ❌ detached codex 已在跑,controller 为了"恢复追踪"直接 kill 并重派同任务。应让现有任务跑完,靠下次 wakeup sweep 接住。
### ScheduleWakeup 必须确认注册(强制)
ScheduleWakeup 是 task-notification 丢失或长时间无完成通知时的 turn-level **fallback**,不是 daemon-event immediate lane,也不是 session-level Monitor substitute。每次调用后**确认返回 `scheduled`**;若 malformed(如 `<invoke>` 漏 `antml:` 前缀)或未注册 → **立即重试**,绝不带着"以为排了但没排"的假设 end turn。turn 结束前必须先确认 daemon-event Monitor bridge active;若本 turn 还依赖 fallback,再确认 task-notification 在飞或 ScheduleWakeup 已注册,否则 loop 就死了。
### 并发 floor 的 `ps codex` 在多系统 host 上会过计(强制修正)

`concurrency_monitor` 与 floor 判定如果用全局 `ps codex exec | wc -l`,当 **host 仓库自身另有 codex-spawning 系统**(如 fkst supervisor 跑自己的 evolve/review 部门并 spawn codex)时,会把两套都算进去 → floor 永远"满"、永不补,而本 loop 实际可能 0 codex。

**更隐蔽的同机多 loop 过计(dogfood 实测)**:即使只数「含 `consensus-rnd-cli spawn-codex`」的 codex,如果再用**相对子串** `.refactor-loop/logs/` / `.refactor-loop/prompts/` 做 scope,**同一台机器跑两个不同仓库的本 skill 时会互相过计**——两个 loop 的相对路径子串完全相同。实测另一 host 的 loop 在跑时,本仓库 `concurrency_monitor` 报 `actual=8` 而本仓库实际只有 1 个 codex,floor 被骗成"满",本仓库 codex 永远补不上去、可能长期单线程。

**修正(强制)**:floor 只数**本仓库(本 loop)的 codex** —— 命令行含 `consensus-rnd-cli spawn-codex` **且含本仓库绝对路径 `$REPO_ROOT`**。
- spawn 时 caller **必须传绝对 `--cd`**(audit 用 `--cd $REPO_ROOT`;implement/verify 用绝对 worktree 路径),使 `$REPO_ROOT` 进入进程 cmdline;inside worktree `<repo>/.worktrees/*` 以 `$REPO_ROOT` 为前缀,亦匹配。
- ❌ **不要**只用相对子串 `.refactor-loop/logs/` 做 scope(同机多 loop 互相过计)。
- **去重(强制)**:每个 codex 会派生**两个**含 `consensus-rnd-cli spawn-codex` 的进程 —— 真 supervisor(`bash <path>/consensus-rnd-cli spawn-codex --cd ...`)+ 一个 shell `-c` wrapper(harness 后台任务回显整条命令)。两个都数 = 每个真 codex 被算成 2,`CODEX_FLOOR=2` 会被**单个**真 codex 满足 → 永远凑不到真正 2 并行。**排除含 ` -c ` 的行**,只数真 supervisor(consensus-rnd-cli spawn-codex 自身不带 ` -c ` flag)。
- 不数 host 其它系统 / 其它仓库的 codex。
- 低于 `$CODEX_FLOOR` 个**本仓库** codex 才补(`CODEX_FLOOR` 由 host.env 注入,默认 5,**硬下限 2** —— 详见下「Concurrency floor」节)。

`consensus-rnd-cli concurrency` 的 `count_in_flight_codex()` 与 `consensus-rnd-cli peek` 的 `list_loop_codex()` 均已按 `$REPO_ROOT` 绝对路径 scope。

<a id="dispatch-queue-protocol"></a>
### Dispatch queue protocol

`consensus-rnd-cli concurrency` consumes queued dispatch files when this loop is below its local floor. The queue is host-local and durable:

```text
$REPO_ROOT/.refactor-loop/dispatch-queue/<priority>/<task-id>.dispatch.json
```

Allowed priority directories are `p0/`, `p1/`, and `p2/`; the monitor always checks `p0` first, then `p1`, then `p2`, and uses lexicographic file order within a priority. Each JSON file uses this schema:

```json
{
  "task_id": "fix-pr44-round-3",
  "cd": "/abs/worktree",
  "prompt": "/abs/prompt.md",
  "log": "/abs/log.log",
  "stall": 5400,
  "queued_at": "2026-05-26T07:25:00Z",
  "reason": "PR #44 r3 fix needed"
}
```

Required fields are `cd`, `prompt`, and `log`; `task_id` defaults to the `.dispatch.json` filename stem if omitted, and `stall` defaults to `5400` if omitted. Paths must be absolute so floor counting can still scope by `$REPO_ROOT`.

Dispatch cwd guard:
- `MUTABLE_DISPATCH_PREFIXES`: `implement-`, `fix-pr`, `remote-ci-fix`, `test-add-`, `verify-`, and `hotfix-`.
- `MAIN_READONLY_DISPATCH_PREFIXES`: `audit-`, `phase9-issue`, `solver-`, `meta-judge-`, `review-pr`, and `reviewer-pr`.
- Queued mutable task prefixes must use `cd` under `$REPO_ROOT/.worktrees/<name>/`; `$REPO_ROOT`, relative paths, paths outside `$REPO_ROOT`, and `$REPO_ROOT/.worktrees/` itself fail closed.
- Main-readonly prefixes are the explicit allowlist that may use `$REPO_ROOT` as `cd`, but each task id must still match the exact owner-local grammar for that prefix; near-miss names fail closed.
- This is a backward-compatible tightening of the existing dispatch queue protocol: no shared workspace policy, no `workspace_policy.py`, no `WorkUnitWorkspace`, and no required `actor/work_unit_id` migration.

Auto-dispatch semantics:
- On each tick, if `actual < CODEX_FLOOR` and the queue is non-empty, the monitor queues at most `CODEX_FLOOR - actual` `HARNESS_SPAWN_INTENT` events for controller/harness background spawn.
- After each intent append, the monitor archives the consumed file to `.refactor-loop/dispatch-dispatched/<task-id>.json`, adding `dispatch_at`, `priority`, `intent_id`, `intent_queued_at`, `dispatch_state="harness-intent"`, and `source_dispatch_file` for audit trail.
- The monitor writes `HARNESS_SPAWN_INTENT` plus `DISPATCH_INTENT:<task-id>:<priority>:<reason>` to `.refactor-loop/.controller-pending-events.log`; archive means controller-visible intent queued, not worker process launched.
- If a queued mutable task violates the cwd guard, the monitor moves it to `.refactor-loop/dispatch-rejected/<task-id>.json`, adding `rejected_at`, `reject_reason`, `priority`, and `source_dispatch_file`, writes `DISPATCH_REJECTED:<task-id>:<priority>:main-worktree-cd:<reason>`, and continues scanning for the next legal queued item.
- If `actual < CODEX_FLOOR` and the queue is empty, the monitor writes `HARD_GATE:dispatch_required=N:actual=A expected=E queue=0` so the controller must enqueue or dispatch real work; audit is the fallback when no open actionable work exists.
- This daemon path is a narrow exception for mechanical controller-runtime dispatch; it does not add lifecycle authority or change marker routing.

### 完成判据必须用 `EXIT=0`,marker 只作 verdict(排 prompt 回显)(强制)

判 `judge-ready` / `merge-ready` / `solver-done` / `reviewer-done` **必须先看 log 末尾 `^EXIT=0`**。`EXIT=0` 表示 codex 进程干净结束,输出文件与 marker 才可被视为完整。**不要**用 `SOLVER_DONE:` / `REVIEW_DONE:` / `META_JUDGE_DONE:` / `FIX_DONE:` marker 的存在判断"已完成"。

codex 常把 prompt 里的 marker 模板原样回显到 log(如 prompt 写 `SOLVER_DONE:<role>:<verdict>` 或 `REVIEW_DONE:<PR>:<role>:<approve|comment|reject>`)。`grep "SOLVER_DONE:"` 会命中 prompt 回显,误把失败 / 未完成 / 部分写入的 codex 判成 done,过早派 judge 或 merge 读到不完整输入。**修正**:
- readiness / done 判据:只看 `tail -5 <log> | grep -q '^EXIT=0'`。
- verdict 判据:只有 `EXIT=0` 后才解析 marker。
- marker verdict 判据:只接受 `line.strip()` 本身是 allowlisted standalone final marker/verdict token 的行;禁止从前缀文本、引用、代码块示例、prompt echo、embedded prose、grep output 或占位符中截取 marker。
- diff 兼容仅限 marker 行带单个 `+` 前缀且去掉 `+` 后整行仍是 standalone marker;除此之外不得用宽松 `find(prefix)` 或 body regex 从任意行中搜索 marker。

<a id="structured-consumption-boundary"></a>
### Controller structured-consumption boundary

Steady-state controller/daemon paths consume only these structured surfaces: clean `EXIT=0` completion state, final allowlisted standalone marker/verdict lines, artifact frontmatter, CLI JSON/action fields, and artifact paths. Artifact paths may be passed through directly to the next worker or comment surface; the controller must not summarize or transcribe the raw prose behind those paths as routing evidence.

Raw log prose, review reject prose, judge reasoning bodies, progress raw tails, and prompt echoes are not normal controller inputs. They may be read or quoted only for exceptional diagnostics: `EXIT!=0`, stream disconnect/503, stuck/crash, missing/invalid structured artifact, router fallback, or worker self-post failure. Worker self-posts own full solver, judge, reviewer, or fix artifacts; the controller records completion, route, counts, verdict token, artifact path, and next step.

**反面(❌ 严禁)**:
- ❌ 三个 solver log 里都出现 `SOLVER_DONE:` 字面就派 meta-judge。
- ❌ reviewer log 里出现 `REVIEW_DONE:` 字面就进入 merge / fix。
- ❌ `grep "^EXIT="` 全 log 判 finished → codex 中途 echo / cat 含 `EXIT=` 文件会误判。必须 `tail -5`。

### gh CLI 保留 env 冲突(强制 — host.env 的 GH_REPO 坑)

`gh` CLI 把 **`GH_REPO`** 当 `--repo` 默认值且要求 `OWNER/REPO` 格式。host.env 禁止导出 `GH_REPO=<repo-name>`。统一用 `GH_REPO_SLUG=OWNER/REPO`,脚本兼容 `GH_OWNER/GH_REPO_NAME`,所有脚本内 `gh issue/pr ...` 必带 `--repo "$GH_REPO_SLUG"`,所有 `gh api repos/...` 必用 `repos/$GH_REPO_SLUG/...`。旧 host 若只有 `GH_OWNER/GH_REPO` 且 `GH_REPO` 是 repo 名,脚本会拼成 slug,但新配置不再使用裸 `GH_REPO`。

### Host 测试节奏(host 适配)

测试命令由 host.env 的 `$TEST_CMD` 注入。若 host 测试共享 permit 池、cwd 或其他 filesystem 状态,必须在 host 项目的 `$PROJECT_RULES` / `$CI_GUARDS` 中声明串行化或稳定性约束;skill prompt 不推断具体测试框架或语言默认值。

<a id="sentinel-and-comment-filters"></a>
## ⭐ 核心原则:GitHub 是系统状态唯一显示面(强制)

**Maintainer 打开 GitHub 必须一眼看到完整状态**,不用读本地 log / root state file / ps process / chat history。任何状态变化在 GitHub **立即可见**。

### 必须 reflect 到 GitHub 的状态变化

| 状态变化 | 触发位置 | GitHub 反映方式 |
|---|---|---|
| 派 codex(任何角色) | spawn 同 turn | `## 📊 状态卡片` post 到关联 issue/PR + label transition |
| Codex 完成(任何角色) | task-notification 处理 | update 卡片(或 post 新卡片说"X 已完成,下一步 Y") |
| 共识达成 | meta-judge consensus | `## ✅ 共识卡片` post(详见 Consensus-rnd Phase design-consensus Consensus action) |
| Maintainer 评论被识别 | daemon eyes react 后 | `## 📊 状态 — 已收到 maintainer 评论(daemon 识别)` daemon banner |
| Reflector 决议 | META_RESOLVED:<kind> | `## 🤖 meta-reflector decision: <kind>` post + label 转 |
| Escalate human | label 加 🆘 | banner 说"✅ 需要 maintainer 决策:具体什么决策" |
| Phase transition | controller route | label sync(`🔍`→`✅`→`🛠️`→`🚀`→`👀`→`🔧`→`⚙️`→`🎉`) |
| Stuck 4h timeout | controller sweep | banner 说"等了 4h 自动派 reflector 重新评估" |
| iter 完成 | last cluster merged | rollup PR banner + 派 next iter audit |
| Bug 修复 | skill commit | commit 内容 push 到 auto-refact-dev,maintainer 可看 commit diff |

### 反面(❌ 严禁)

- ❌ Codex 在本地跑但 GitHub 上对应 issue/PR 无任何状态卡片(maintainer 不知道 controller 在干什么)
- ❌ Codex 完成后只更新本地 log,不 post GitHub banner
- ❌ Label 在 GitHub 转了但没配 banner 解释(label list 不解释 why)
- ❌ Banner 用模糊语言("处理中""稍等"),应该具体说当前 phase + 下一步 + ETA / 何时介入
- ❌ 多个 daemon 同时跑但 maintainer 看 GitHub 只看到 eyes,不知道还有 codex 在工作

### Controller comment sweep:必排除 bot author(强制)

Controller 之前 sentinel-aware sweep filter 用 body prefix(`## 🤖` 等),但 **codecov[bot] / dependabot[bot]** 等 GitHub bot 评论以 `## [Codecov](` 起首,filter 漏。误判为"真人新评论"派 fresh codex round → 浪费 + 可能再误 ping。

**修法**:sweep query 必加 `author.login | endswith("[bot]") | not` filter,**同时** body prefix `## [Codecov](` 排除(codecov user login 不带 `[bot]` suffix,需 body 兜底):

```bash
gh issue view <N> --json comments --jq '
  [.comments[] | select(
    (.body | contains("⟦AI:AUTO-LOOP⟧") | not)
    and (.body | startswith("## 🤖") | not)
    and (.body | startswith("## 📊") | not)
    and (.body | startswith("## ✅") | not)
    and (.body | startswith("## 🆘") | not)
    and (.author.login | endswith("[bot]") | not)
  )][-1]
'
```

剔除:codecov[bot] / dependabot[bot] / github-actions[bot] / etc。

### 严禁把人名当 plain text 导致误 @-ping(强制)

**根因**:prompts / banner 文本里的真人姓名或 handle 容易被 GitHub auto-link 成 `@-mention`,导致误 ping 不相关用户或 maintainer。

**铁律**:
- **所有 codex prompts**(`solver-*.md` / `meta-judge.md` / `reviewer-*.md` / `review-fix.md` / `audit.md` / `design-issue-*.md` 等)严禁写未经过 host 配置确认的人名或 `@-mention`。
- **Controller 自己 post banner** 使用 `maintainer` 或 host 配置的安全称谓,不写裸人名。
- **@-mention whitelist** 来自 `$MAINTAINER_WHITELIST`,并且必须经 git blame / host 配置验证。

### Wakeup 第一动作:`python3 <skill-root>/scripts/consensus-rnd-cli peek | tail -80`(强制)

减少人工 grep / parse 错误。一眼看全:
- 活跃 codex 数(只数本 loop:命令行含 `.refactor-loop/logs/` 或 `.refactor-loop/prompts/`)
- Open auto-loop PR 的 CI + state
- Merge readiness / daemon health / monitor zero_streak 摘要
- Consensus-rnd Phase design-consensus router ledger + pending events 近 10 行
- Open auto-loop issue + phase label

`consensus-rnd-cli peek` 是 observability-only status lens,只读展示 ledger / pending / readiness / health,不显示 generic marker-to-route recommendation。Route authority remains SKILL Phase Routing + controller clean-exit log-tail sweep + `consensus-rnd-cli phase9-router`。

<a id="wake-source-rules"></a>
## Wake source rules and no-gap details

### 0 codex + active task = bug(强制)
<!--
# Refactor (iter3/skill-human-label-taxonomy):
#   Old: 四个 Human label(含两个 🆘),no-gap/escalation 判定散落
#   New principle: 恰好两个 active Human label;causes 移到 reason surface(#15 structural 共识)
-->

**铁律**:任何 active phase issue/PR(`🔍 design-solving` / `🔧 fixing` / `👀 reviewing` / `🛠️ implementing`)存在时,**应至少有 1 个本 loop codex 在跑**。本 loop codex = `consensus-rnd-cli spawn-codex` 命令行含 `.refactor-loop/logs/` 或 `.refactor-loop/prompts/`。实际为 0 且 GitHub 有 active phase → **P0 bug**(no-gap-violation)。

**Controller wakeup 第一动作**:`python3 <skill-root>/scripts/consensus-rnd-cli peek | tail -80`。如果活跃 codex == 0:
1. **不允许** `ScheduleWakeup` 后 end-turn — 必须派下一步 codex 才允许 ScheduleWakeup
2. **不允许**只看 marker 不 sweep:必须扫所有刚 finished marker(implement/judge/reviewer/fix/reflector)并按 marker→spawn-next 表派至少 1 codex
3. 如果所有 active issue/PR 都真在等 maintainer(全是 `crnd:human:maintainer-decision` / `crnd:phase:blocked`),那 0 codex 才 OK — 但仍要在 status 报告中说明 "0 codex by design:N issue 全等人"

**consensus-rnd-cli concurrency** P0 alert:`expected > 0 AND actual == 0` → IMMEDIATE(streak=1 即写 alert + pending event,不等 2 tick)。controller 看到 alert → 立即 wake 自查。

**无观察模式豁免(强制,airtight)**:controller 任何时刻——无论 wakeup、task-notification、还是 Monitor 事件——一旦观察到本 loop codex `actual == 0` 且存在 active phase issue/PR(非全 `crnd:human:maintainer-decision` / `crnd:phase:blocked`),**必须在同一 turn 内立即派出真实下一步 codex**,把并发拉回 floor。**禁止**以下任何理由 defer、end-turn、或转入被动等待:"观察 daemon 自驱 / 验证 headless"、"等 daemon 1/tick 自己爬"、"floor 会自己回升"、"router/wakeup_runner 会接管"、"等 cascade / fix 完会派"、"backoff 是已知症状"。daemon 自驱达稳态(批量派 + worker 解耦落地)**之前**,维持 floor 是 controller 不可让渡、不可暂缓的职责;`actual == 0 + active work` 永远是必须当 turn 修复的 P0,不是可观察的状态。唯一例外是上面 point 3(所有 active item 真在等 maintainer),且必须在 status 显式说明。

### Controller 每 wakeup 必派"下一步"(no gap policy)

Controller wakeup 处理 markers 后,**必须在同 turn 内派出下一步 codex**(if any actionable),不留 gap 等下次 wakeup:

| Marker 完成 | 立即派 |
|---|---|
| SOLVER_DONE × 3(同 issue 同 round)| 同 issue 同 round meta-judge |
| META_JUDGE_DONE:consensus | implement codex |
| META_JUDGE_DONE:converge:rS | r(S+1) 三 solver unless router-owned stalled predicate dispatches round-S reflector; legacy r(S+1) payload remains compatible |
| legacy META_JUDGE_DONE:escalate:stalled | reflector only as read-only compatibility and only if the stalled predicate holds |
| META_RESOLVED:re-design | phase9-router queues marker.round + 1 三 solver with new framing |
| IMPLEMENT_DONE:ok | controller commit/push to early managed PR + Consensus-rnd Phase review-gate reviewer × 3 |
| REVIEW_DONE × 3 + any reject | fix codex r+1 |
| FIX_DONE | reviewer r+1 |
| TEST_ADD_DONE | controller commit/push 等 CI |
| AUDIT_DONE | bootstrap design issues + cluster-003 类直接 implement |

派出后 ScheduleWakeup;**不允许** "wakeup → sweep → 0 派出 → 下 wakeup" pattern(空 wakeup)。

### Controller 严禁自升 escalate(强制 — 防偷懒标人)
<!--
# Refactor (iter3/skill-human-label-taxonomy):
#   Old: 四个 Human label(含两个 🆘),no-gap/escalation 判定散落
#   New principle: 恰好两个 active Human label;causes 移到 reason surface(#15 structural 共识)
-->

controller 严格按 judge marker 判 escalate,**不允许**自己以"累了/round 多 / 触及 Tier 或哲学"等理由直接 label `crnd:human:maintainer-decision`。

**判定铁律**:

| Judge marker | Controller 动作 | 不允许 |
|---|---|---|
| `converge:round-N` | clean rS judge 的 canonical payload 是 round-S; legacy round-(S+1) 也派 r(S+1);若 router-owned stalled predicate 成立则改派 round-S reflector;非相邻 payload mismatch fallback | ❌ "round 多了"自升 escalate |
| legacy `escalate:stalled` | read-only compatibility: predicate/source gates 成立才派 reflector codex | ❌ 直接 label `crnd:human:maintainer-decision` |
| `escalate:philosophy:<reason>` / `escalate:gpg-ratification:<reason>` / `escalate:<其他>` | 视为 legacy judge 输出:重派 judge 或派 reflector,要求回到 consensus / converge;stalled 只能由 router predicate 派生 | ❌ 因 CLAUDE.md / Tier I/II / GPG / reinstall 直接 label 人 |
| `consensus` | 派 implement | — |
| 无 judge marker / judge crash | 重派 judge | ❌ 自判 escalate |

**正确"label 人"的唯一路径**:`reflector` 输出 `META_RESOLVED:escalate-human:<reason>` → controller 才允许 label `crnd:human:maintainer-decision` + ASCII A/B/C reason banner。该路径只表示**共识机制本身无法收敛**,不是因为触及 Tier I/II、CLAUDE.md、核心抽象、GPG 或 reinstall。

结构性教训:controller 曾把多数 issue 误升为人工等待,根因是没有严格区分 `converge`、router-derived `stalled`、`philosophy` 三类路由。只有 reflector 输出 `META_RESOLVED:escalate-human:<reason>` 后才允许 label 人;`converge` 继续派 solver或由 router predicate 改派 reflector,可由 reflector 处理的 philosophy 分歧不得直接升人。

### Spawn / merge / banner 后必须 peek(强制 — 防 maintainer 漏读)

任何 controller turn 派 codex / merge PR / post banner / close issue 之后,**turn 结束前必须 `python3 <skill-root>/scripts/consensus-rnd-cli peek | tail -80` 一次扫 maintainer 评论 + 0-codex 漏洞**。

理由:`task-notification` 触发的 turn 容易陷入"处理 marker → spawn 下一步 → end turn"线性思维,会跳过 peek 而错过 maintainer 与此 task 并行的新评论。曾出现 controller 派出下一步 judge 期间漏读新的架构反馈,直到 maintainer 报错才发现;peek 是防漏读的强制尾部检查。

例外:turn 唯一动作是 ScheduleWakeup(纯休眠)可省 peek。

### Concurrency monitor:`<skill-root>/scripts/consensus-rnd-cli concurrency`(强制)

**60s** 周期 daemon,只监控 no-gap sentinel:
- expected = active issue/PR 数(per phase 表)
- actual = `consensus-rnd-cli spawn-codex` 且命令行含 `.refactor-loop/logs/` 或 `.refactor-loop/prompts/`
- **P0 规则**:`expected > 0 AND actual == 0` → **IMMEDIATE** alert(streak=1 即触发,不等 2 tick)。这是 no-gap-violation。
- 写 `.refactor-loop/.concurrency-alert.log` + `.controller-pending-events.log`(controller 下次 wakeup 必读)
- 不读取 `CODEX_FLOOR`,不判断 refill 候选,不写非零 floor-deficit 事件,不自动 spawn codex。floor 补给只属于 controller wakeup step 1.5。

**Controller 每 wakeup 必读** `tail -20 .refactor-loop/.concurrency-alert.log`:
- 看到 `P0 no-gap-violation: ...zero_streak=N` → 至少 N×60s 没 codex,**必须**先派 codex 才允许 ScheduleWakeup
- zero_streak >= 5(>= 5 分钟 0 codex)= 严重失保 — 同时把 PushNotification 给 user "controller 失保 N min"

启动:
```bash
nohup bash -c 'test -n "${CONSENSUS_RND_HOST_ENV:-}" && source "$CONSENSUS_RND_HOST_ENV" && exec python3 <skill-root>/scripts/consensus-rnd-cli concurrency' \
  >> .refactor-loop/logs/concurrency-monitor.log 2>&1 &
disown
```

### 反面(❌ 严禁)

- ❌ wakeup sweep 看到 SOLVER_DONE × 3 但**不派 judge**(留 gap)
- ❌ codex 完成后只删 progress comment,不派下一步
- ❌ wakeup ScheduleWakeup 但本 turn 0 codex spawn(等 wakeup 才动 = lazy / 死循环)
- ❌ 看到 concurrency-alert.log 有 entry 但 controller 不读
- ❌ active issue 0 codex 跑 >= 1 wakeup 周期(说明 controller 漏派)

### Controller-internal lifecycle primitives(强制)

<!-- Refactor (iter201/issue-201): Old pattern: lifecycle helpers were
documented as public consensus-rnd-cli/controller-actions shell commands.
New principle: lifecycle operations are controller-internal ControllerActions
methods/direct package calls, never worker-visible public CLI verbs. -->

<!-- Refactor (issue-276): Old pattern: controller lifecycle PR/issue targets
could be empty, inferred, or non-canonical GitHub identifiers before gh/git
side effects. New principle: lifecycle targets are normalized through a private
canonical positive-decimal boundary before any lifecycle side effect. -->

<!-- Refactor (issue-300): Old pattern: controller-opened PRs could be mergeable
before the review-gate consensus decision. New principle: open PRs as draft by
default and mark ready only inside post-decision merge_pr after MERGE or
MERGE_WITH_COMMENTS has already been decided. -->

7 个曾发生的 bug 都来自 controller boilerplate 重复 + shell 变量传值 bug。统一用 controller-internal `ControllerActions` primitives, not public CLI commands:

```python
actions = ControllerActions(LoopContext.load(cwd=os.getcwd()))
actions.safe_worktree(iteration, cluster, base)
actions.open_pr_with_label(title, body_file, base=base, head=head)
actions.merge_pr(pr)
actions.render_template(input_path, output_path)
actions.apply_human_label_or_skip(pr_number, source_marker, reason)
```

**强制**:
- 派 codex 前必须 validate rendered prompt output — 防 codex blocked on unresolved placeholder
- Controller-opened PRs must use internal `open_pr_with_label(...)`; it creates open PRs as draft by default (`gh pr create --draft`) before labels are applied.
- merge PR 必须用 internal `merge_pr(pr)` — post-decision ready+merge + auto-close + label cleanup,不留尾巴。`merge_pr` first checks draft state and, only when the controller has already decided `MERGE` or `MERGE_WITH_COMMENTS`, marks the PR ready before `gh pr merge`; it never computes Consensus-rnd Phase review-gate reviewer policy.
- worktree 创建必须用 internal `safe_worktree(iteration, cluster, base)` — 处理 "already exists" race
- PR 号捕获必须用 internal `open_pr_with_label(...)` returned tuple — **禁止** shell `pr_num=$(...grep -oE...)` 这种 subshell 变量传值模式
- Lifecycle PR/issue targets entering `apply_human_label_or_skip`, `merge_pr`, `open_pr_with_label`, or `record_recent_pr_merge` must pass `_normalize_lifecycle_target` and become canonical positive decimals before any `gh` or `git` side effect; empty, blank, zero, negative, non-digit, leading-zero, URL, branch, and current-PR inference inputs fail closed and write `CONTROLLER_ACTION_BLOCKED:invalid-github-target:<action>:<kind>:<source>` to the controller pending-event log. PR creation target capture stays limited to `open_pr_with_label(...)` URL extraction followed by the same normalization.
- `safe_push`, `safe_sync_main`, triage apply, and human-label apply are internal primitives/direct package calls only; `consensus-rnd-cli merge-pr/open-pr/open-release-rollup-pr/apply-human-label/safe-push/safe-sync-main/apply-sync/apply-triage` must fail closed as unknown public commands.

**Label 生命周期(强制状态机)**:
<!--
# Refactor (iter3/skill-human-label-taxonomy):
#   Old: 四个 Human label(含两个 🆘),no-gap/escalation 判定散落
#   New principle: 恰好两个 active Human label;causes 移到 reason surface(#15 structural 共识)
-->

The lifecycle state machine is implemented through `codex_refactor_loop.labels`
and controller helpers. Prose may name individual canonical labels when
explaining a single guard, but active label bundles and transition tables must
come from catalog-backed helpers instead of SKILL.md examples. The invariant is
still exactly one canonical phase label and exactly one canonical human label
for managed open items after migration. For loop-managed issue/PR routing, that
means exactly one loop-owned `crnd:phase:*` label and exactly one loop-owned
`crnd:human:*` label. Host business labels may coexist, but they are not
routing authority and must not replace the loop-owned phase/human axes.

### Spawn pattern — Bash `run_in_background: true`(强制)

**关键架构铁律**:codex spawn 必须用 **Bash tool with `run_in_background: true`** 跑 `consensus-rnd-cli spawn-codex`。这样 harness 会跟踪 Bash → codex 进程链,**codex exit 时 harness 立即 fire `<task-notification>` 唤醒 controller**,不用等 ScheduleWakeup。

**两步流程**(per spawn):

1. **先 post banner**:controller 在同一 turn 调用 `ControllerActions.post_status_banner(BannerRequest(...))`;该 internal action 先过 #191 active-controller owner gate,再校验 issue/PR target,写 body tempfile,并通过 `self.gh(...)` 发布。

2. **再 spawn codex**(Bash `run_in_background: true`):
   ```bash
   <skill-root>/scripts/consensus-rnd-cli spawn-codex \
     --cd <worktree> --add-dir $REPO_ROOT \
     --prompt <prompt-file> --log <log-file> --stall 5400
   ```

**反模式(❌ controller 主链路禁用)自定义 `Popen` detach spawn**:
- 用 `Popen + start_new_session` 把 codex 脱离 python parent → harness 看不见 codex
- 结果:codex done 1-13 分钟后 controller 才在下次 ScheduleWakeup 时才发现(0 codex 期间监控告警但 controller 在睡)
- detached spawn 会让 harness 失去追踪,曾导致 codex 完成后 controller 长时间未醒,monitor 连续告警但无人处理。

**正确语义**:controller 主链路 codex = harness-tracked Bash task = automatic task-notification on exit。daemon 不直接 detached 启动 codex;允许的 deterministic daemon 只写 prompt/log/ledger 或 `HARNESS_SPAWN_INTENT`,再由 controller/harness 用 background task 启动。

**禁止**:
- ❌ controller 主链路用 `nohup ... &` 或 `Popen + start_new_session` detach codex
- ❌ 用 blocking Bash 跑 codex(同步等 60 分钟 → conversation 卡死)
- ❌ 漏 post banner → GitHub 看不到运行状态(per `ControllerActions.post_status_banner` 强制)

### Controller 自检(每次 wakeup)

per-wakeup sweep step 1.5 之后,**对每个 in-flight codex 验证关联 issue/PR 是否有最新状态卡片**(创建时间 ≥ codex spawn 时间):

```bash
# 对每个 in-flight codex 任务
for log in $(ls -t .refactor-loop/logs/*-r*.log .refactor-loop/logs/implement-*.log .refactor-loop/logs/meta-reflect-*.log 2>/dev/null); do
  # 找到关联 issue/PR
  # 找到 spawn 时间(log mtime / SPAWN 行)
  # gh 查 issue/PR 最新 AI banner 时间
  # 如 banner 早于 spawn 时间 → controller MUST post 新 banner 反映 "<codex> 在跑"
done
```

如发现 in-flight codex 但关联 issue 无对应 banner → **本 turn 必须 post 补**,然后才能 schedule wakeup。

---

You are the **Controller**. You never edit production code yourself. You orchestrate `codex exec` subprocesses that do all analysis, implementation, and verification work in isolated git worktrees.

**默认 worker = codex CLI(`codex exec`),不是 Claude `Agent` / `Task` subagent(强制)**。所有需要「思考」的工作——分析、诊断、设计、实现、验证、review、solve,乃至对本 skill 自身的 baseline / 验证测试——一律 delegate 给 `codex exec`(经 `consensus-rnd-cli spawn-codex`)。❌ 严禁用 Claude `Agent` / `Task` subagent 替代 codex 做这些工作。理由:codex 进程是 harness-tracked、可跨 session 存活、log 落 `.refactor-loop/logs/` 可 sweep、完成发 task-notification、被 concurrency floor 计数——这套无人值守编排的**全部不变量都建立在「worker 是 codex 进程」之上**;Claude subagent 不落盘 marker、不被 floor 计数、不留可 sweep 的状态,会让监控面、恢复逻辑、并发兜底全部失效。`refactor-team` skill 才是 Agent-subagent based 的那套;**本 skill 的 worker 恒为 codex CLI**。

<a id="bootstrap-details"></a>
## Consensus-rnd Phase bootstrap — Bootstrap (first wakeup only)

### 首次唤醒强制序列(MANDATORY — 按序跑完才能 end turn)

> 这是 first wakeup 唯一合法路径。baseline 测试证明:不把以下步骤钉成强制有序首步,controller 会只 bootstrap state + 派 audit,**漏起全部 7 daemon、漏建 labels**(把 daemon / label 误当成「别处已起好」的 steady-state 检查)。下面把它们钉成不可跳过的有序步骤。

0. **host.env 自检(缺失即停,绝不臆造)**:`test -n "${CONSENSUS_RND_HOST_ENV:-}" && source "$CONSENSUS_RND_HOST_ENV"` 取 `$REPO_ROOT/$GH_REPO_SLUG/$BUILD_CMD/$TEST_CMD/...`。
   - 不存在 → 从 `skills/codex-refactor-loop/host.env.example` 复制到 host-owned `.config/consensus-rnd/host.env` 并设置 `CONSENSUS_RND_HOST_ENV` 并填必填项;无法确定必填值(REPO_ROOT/GH_REPO_SLUG/BUILD_CMD/TEST_CMD)→ **PushNotification 请 maintainer 填,end turn,不 spawn 任何东西**。
   - ❌ 严禁用 `git rev-parse` / `gh repo view` 猜值后带空 BUILD_CMD/TEST_CMD 硬跑。
0b. **ProjectRulesFixedPointProbe(强制,先于任何 actor 派发)**:在 `host.env` 注入后立即运行:
   ```bash
   python3 skills/codex-refactor-loop/scripts/consensus-rnd-cli check-project-rules
   ```
   - 该 probe 只读解析后的 `$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}` 内 `consensus-rnd:foundational-invariants` sentinel block;`$PROJECT_RULES` 是 host-owned read-only evidence,不是 skill 可写 surface。
   - 非 current 时只写 `.refactor-loop/runs/project-rules-fixed-point.patch`,并以非 0 退出;controller 把该 patch artifact 交给 maintainer / host policy 流程处理。
   - probe 退出非 0 → bootstrap fail closed:不得初始化 state、不得建 labels、不得起 daemon、不得派 audit / solver / reviewer / implement actor。
   - # Refactor (iter218/issue-218):
     #   Old pattern: ensure-project-rules 是 public CLI 默认写 host policy 文件($PROJECT_RULES),违反 skill 无 host 改动权边界
     #   New principle: 改为 read-only check-project-rules probe + patch artifact:probe 只读判 sentinel block,非 current 写 .refactor-loop/runs/ patch 并 fail-closed 不派 actor;删 ensure-project-rules/_atomic_write,不引入 PROJECT_RULES_WRITE_ENABLE。严格按 plan 逐条改。
1. **runtime dirs + integration 分支**:`mkdir -p .refactor-loop/{logs,runs,clusters,prompts,worktrees,state}` + idempotent 建/推 `$INTEGRATION_BRANCH`(下方细节)。Do not create or maintain root `.refactor-loop/state.json`.
2. **建全套 labels**:跑「Label 系统」节的 catalog validation / GitHub drift plan, then controller-owned apply if authorized. **漏建 = 后续 phase transition 无 canonical label 可挂、comment-monitor 查 catalog-managed items 漏掉 PR**。
3. **起并挂载全部 7 个 daemon**:按「Host 运行编排 → Daemon 启动」节的 `bash -c 'source host.env && exec'` pattern 起齐 `consensus-rnd-cli concurrency` / `consensus-rnd-cli progress-reporter` / `consensus-rnd-cli comment-monitor` / `consensus-rnd-cli dev-sync` / `consensus-rnd-cli phase9-router` / `consensus-rnd-cli closed-label-reconciler` / `consensus-rnd-cli wakeup-runner`。随后运行 `python3 <skill-root>/scripts/consensus-rnd-cli restart-daemons` 规范化 heartbeat-managed daemon,再读 `python3 <skill-root>/scripts/consensus-rnd-cli daemon-status --json` / `.refactor-loop/state/statusline-snapshot.json` / `python3 <skill-root>/scripts/consensus-rnd-cli peek | tail -80` 确认健康面可见;Consensus-rnd Phase design-consensus router 读其 lock/ledger/log/fallback event surface。**首轮就必须把 7 个全起起来——它不是「以后某次 wakeup 才做的 liveness 检查」**。
4. **派主路径或 fallback producer**:先扫 open actionable managed issue/PR 并派 next-step actor;只有没有 open actionable work / queued dispatch / clean marker route / CI-no-gap route / maintainer-comment route / higher-priority wakeup route 时,才派 Consensus-rnd Phase work-intake audit fallback(`consensus-rnd-cli spawn-codex` + Bash `run_in_background:true`)+ ScheduleWakeup 兜底 + end turn。

每步做完才进下一步。3 漏起任一 daemon、2 漏建 labels = bootstrap 失败,下次 wakeup 第一件事补齐。

#### ❌ 严禁(首次唤醒反模式 — 均来自 baseline 失败)
- ❌ 只建本地目录 + 直接派 audit fallback,不起 6 daemon(baseline 默认失败模式)
- ❌ 不建 labels 就派 codex(phase transition 时无 label 可挂)
- ❌ 把整个 skill 降级成「本地读代码 + 出 markdown 报告 + 本地 commit」而不碰 GitHub、不起 daemon、不派 audit
- ❌ host.env 缺失时猜值硬跑

---

Create the runtime directories if missing:

```bash
mkdir -p .refactor-loop/{logs,runs,clusters,prompts,worktrees,state}
```

There is no root `.refactor-loop/state.json` bootstrap schema. Work-unit recovery comes from
GitHub labels/comments, clean `EXIT=0` log tails, prompt artifacts, git topology, and named
producer-owned state artifacts.

**Integration branch setup**: `$INTEGRATION_BRANCH` and `$REVIEW_BASE_BRANCH` are required host.env facts. The integration branch is the long-lived branch where all auto-refactor cluster PRs land before rolling up to the review base. On a fresh loop:

```bash
test -n "${INTEGRATION_BRANCH:-}" && test -n "${REVIEW_BASE_BRANCH:-}"
git fetch origin
git checkout -B "$INTEGRATION_BRANCH" "origin/$INTEGRATION_BRANCH" 2>/dev/null \
  || git checkout -b "$INTEGRATION_BRANCH" "origin/$REVIEW_BASE_BRANCH"
git push -u origin "$INTEGRATION_BRANCH" 2>/dev/null || true
```

Do not infer branch names from this skill. Existing loops keep their host.env branch names; missing or empty branch variables fail closed before bootstrap, sync, PR creation, release-commit projection, or release-gate branch checks.

**`pr_mode` choice (set in Consensus-rnd Phase bootstrap; do not change mid-loop)**:

- `"stacked"` (**default**): each cluster opens its own PR. Hard-dep clusters stack (PR B's base = PR A's branch); soft-dep / independent clusters PR against `integration_branch`. Integration branch eventually opens one rollup PR to `review_base_branch`. Reviewer sees small per-cluster PRs and can ack independently; cost is rebase-on-reject when an upstream cluster is changed. This is the right shape for typical refactor loops (3+ clusters, reviewable independently).
- `"single"`: all clusters merge to `integration_branch` and a single PR targets `review_base_branch`. Simple; reviewer sees one big PR. Use only when the loop is expected to produce ≤ 2 clusters or the user explicitly asks for a single PR.

If the user doesn't specify, default `"stacked"` and surface in bootstrap PushNotification: "Using stacked-PR mode; pass `pr_mode: single` to override."

Create top-level TaskCreate items: audit / dispatch / merge.

---

<a id="phase-routing-details"></a>
## Consensus-rnd Phase work-intake — Fallback issue production

The default main path is open actionable managed issue/PR resolution; the controller must dispatch those
next-step actors before starting any new-work producer. Producer normalization is documented in
[work-unit contract](#work-unit-contract): accepts only `producer: audit` and `producer: manual-issue`.
`audit` is the fallback raw artifact issue producer for this phase and runs only when no actionable managed
issue/PR or higher-priority route exists. `manual-issue` enters through Consensus-rnd Phase design-intake
triage and must already be reshaped before Consensus-rnd Phase design-consensus.

1. Copy `prompts/audit.md` (this skill's template) to `.refactor-loop/prompts/audit-iter-N.md`.
2. Replace the literal `${ITERATION}` placeholders with N using a targeted replacement; leave runtime placeholders such as `$REPO_ROOT`, `${PROJECT_RULES:-CLAUDE.md}`, and `$SOURCE_GLOBS` intact for the audit codex runtime.
3. Dispatch:

   ```bash
   <skill-root>/scripts/consensus-rnd-cli spawn-codex \
     --cd "$REPO_ROOT" \
     --prompt .refactor-loop/prompts/audit-iter-N.md \
     --log .refactor-loop/logs/audit-iter-N.log \
     --stall 3600
   ```

   Use Bash with `run_in_background: true`. `--stall` is a no-output stall window, not a total runtime ceiling; codex may run longer while it keeps producing output.

4. Schedule wakeup 1500–1800s as safety net (task notification is primary wake).
5. **End turn.**

When task notification fires → **controller validation** before accepting the audit:

- a. Check log tail for the terminal marker: `AUDIT_DONE:...:<N>` or `AUDIT_INCOMPLETE:<reason>`.
- b. If `AUDIT_INCOMPLETE` → log reason, re-dispatch audit with the missing pieces called out in the prompt header (e.g., "previous audit returned INCOMPLETE because <reason>; deliver the missing artifact this run"). Do NOT proceed to Consensus-rnd Phase implementation with an incomplete audit.
- c. Verify the two output files exist: `audit-iter-N.md` AND `audit-iter-N-candidates.ndjson`. Missing either → treat as INCOMPLETE.
- d. Verify the candidate file has `>= 25` entries unless the audit body explicitly explains why every analyzer pack command returned 0 hits.
- e. Verify the audit body contains the 6 fixed-analyzer-pack commands by name with hit counts.
- f. Verify reject reasons cite a CLAUDE clause + per-candidate evidence (not blanket "covered by guard"). Sample 3 random rejects; if any lack evidence → INCOMPLETE.
- g. Verify `coverage_manifest.total_opened_files >= 60` with the documented sub-distribution.

Anti-anchoring: **do not** include phrases like "prefer 0", "loop saturated", "healthy signal" in the audit prompt body. These bias codex toward terminating instead of digging. Use the mechanical thresholds in `prompts/audit.md` as the only stop criteria.

After validation: read `audit-iter-N.md`; the controller projects each accepted audit cluster into
the work-unit fields documented in [work-unit contract](#work-unit-contract) (`work_unit_id`, `kind`, `producer`,
`source_ref`, and audit compatibility aliases), derive the next dispatch batch from the audit artifact and GitHub state
(max `max_parallel_clusters` per batch) by
**file/project disjointness**:

- Current audit-backed units set `work_unit_id == id == cluster_id == legacy_cluster_id`,
  `kind="audit-cluster"`, `producer="audit"`, and `source_ref="audit-iter-N.md#<cluster-id>"`.
- Preserve existing cluster fields for audit section lookup, markers, artifact filenames, branch
  names, and GitHub issue routing during compatibility.
- Stable operational tokens are public routing tokens, not names to migrate in this contract:
  `[refactor-design]`, `refactor/iterN-<cluster-id>`, `.refactor-loop/.../<cluster-id>`,
  `IMPLEMENT_DONE:${CLUSTER_ID}`, `VERIFY_DONE:${CLUSTER_ID}`, `SOLVER_DONE`, and
  `META_JUDGE_DONE`.
- Do not rename, dual-write, alias, or replace those tokens with `work-unit-*` forms; do not add
  a named operational-policy abstraction for compatibility.
- Consensus-rnd Phase design-intake `manual-issue` intake may dispatch work-unit contract items only after the
  accepted GitHub issue has been reshaped with `kind="manual-work-unit"`,
  `producer="manual-issue"`, `source_ref="gh-issue-<N>"`, `scope_paths`, problem/invariant text,
  and `verification_hints`. It must not fabricate `cluster_id` or `legacy_cluster_id`.

- Two clusters that touch the same `$BUILD_CMD 目标/工程文件` or share a file path go in different batches.
- Two clusters that touch the same schema/protocol file → different batches.

### requires_design clusters → open GitHub issue, do NOT auto-implement

For every cluster with `requires_design: true`:

1. Open a GitHub design issue through the active-controller-gated
   `ControllerActions.open_design_issue_with_labels(title, body_file)` internal
   primitive. It validates the self-contained body file and applies the
   catalog-derived design issue label bundle from `labels.design_issue_label_bundle()`.
   <!-- Refactor (issue-297): Old: controller runbook exposed copyable issue
   create and label commands. New: design issue creation is routed through a
   narrow active-controller-gated ControllerActions contract. -->
   Do not inline the active label bundle in SKILL.md or prompts.
   The body template at `prompts/design-issue-body.md` includes: the cluster's YAML block from audit, full evidence section, the audit's `Fix boundary` paragraph, and an explicit "decision needed" checklist (schema/protocol change? new contract? backward-compat strategy? whether to split into multiple PRs?).
2. Record design-pending status on GitHub: the issue body/comment links the source work unit,
   `work_unit_id`, opened timestamp, and current status. Future routing reads GitHub labels,
   comments, and Consensus-rnd Phase design-consensus artifacts, not a root local queue.
3. Skip the cluster in Consensus-rnd Phase implementation (do NOT batch it).
4. PushNotification: "iter<N> opened design issue #<issue> for cluster-<id>. Auto-loop paused on this cluster pending human design decision."

Update GitHub-visible state, advance to Consensus-rnd Phase implementation (with requires_design clusters excluded).

### Stale-worktree audit pollution(强制 pre-audit cleanup)

**Bug 来源**:audit codex 默认在 `--cd $REPO_ROOT` 下扫描,但 `find` / `rg` 会无视 git boundary 扫到 inside worktrees(`$REPO_ROOT/.worktrees/iterN-cluster-*` 等)。已 merge 但未清理的 worktree 里仍保留 pre-refactor src 文件,audit 把那些当成"现状"出 evidence,导致 cluster 描述指向 main 中**已删除**的文件路径(file:line 在 main 不存在)。

结构性教训:曾出现 audit 从已合并但未清理的 worktree 读取过期 evidence,导致 cluster 指向 main 中已删除的 file:line。pre-audit 必须清理 stale worktree,并抽查 evidence file:line 在当前 main 真实存在。

**强制 pre-audit 步骤**(每次派 audit codex 前 controller 执行):

Use the owner-local worktree primitives instead of copyable git cleanup
recipes: read the worktree projection, classify stale worktrees against open PR
state, and perform cleanup only from the active controller's checked-in
maintenance path. The desired postcondition is main + dev-sync + current
in-flight cluster worktrees only.

**反面禁止**:
- ❌ 派 audit codex 前不 clean worktrees → bogus evidence + 浪费 5400s codex 时间
- ❌ 见 audit-iter-N 的 cluster 直接 trust → 必须 controller 抽查 3 个 evidence file:line 真存在(且不在 stale wt)
- ❌ "可能下次还要用" → worktree 是 disposable;branch 在 git history,需要时由 owner-local worktree primitive 重建

如果发现 audit 输出含 stale-worktree evidence(典型征兆:file path 在 main `git ls-files` 中找不到):
1. archive 该 audit md/ndjson 加 `.STALE-WORKTREES.md` 后缀
2. clean worktrees(per 上)
3. 重派 audit(同 prompt)

---

## Consensus-rnd Phase implementation — Implement (parallel codexes, one per cluster in current batch)

For each cluster in the current batch:

1. Create or reuse the implementation worktree through
   `ControllerActions.safe_worktree(iteration, cluster, base)`, which owns the
   branch/path naming contract and keeps controller worktrees under
   `$REPO_ROOT/.worktrees/`.

2. Materialize prompt: copy `prompts/implement.md`, replace placeholders (`{{work_unit_id}}`,
   `{{cluster_id}}`, `{{worktree_path}}`, `{{branch}}`, `{{old_pattern}}`, `{{new_principle}}`,
   `{{scope_paths}}`, `{{verification_hints}}`). For current audit-backed units, export
   `WORK_UNIT_ID=$CLUSTER_ID` before `envsubst` / placeholder replacement. Save to
   `.refactor-loop/prompts/implement-<cluster-id>.md`.

3. Dispatch via `consensus-rnd-cli spawn-codex --cd <worktree>` with `--stall 5400` (5400s no-output stall window).

4. Post/sync the GitHub status with the work-unit identity/provenance fields, prompt path, log path, and harness task id if exposed.

After all parallel dispatches, schedule wakeup 1800s safety net. **End turn.**

When each task notification fires → check log tail for `IMPLEMENT_DONE:<cluster-id>:<status>`:
- `ok` → advance that cluster to Consensus-rnd Phase verification (verify).
- `partial` / `blocked` → post the failed/blocked reason, log it in the run artifact, optionally re-dispatch with corrected prompt.

Do **not** advance the whole batch in lockstep; verify each cluster independently as soon as its implement finishes.

---

## Consensus-rnd Phase verification — Verify (one codex per cluster, independent of implement codex)

For each cluster whose implement finished `ok`:

1. Materialize `prompts/verify.md` → `.refactor-loop/prompts/verify-<cluster-id>.md`. For current
   audit-backed units, export `WORK_UNIT_ID=$CLUSTER_ID`; `WORK_UNIT_ID` is the canonical prompt
   identity, while `CLUSTER_ID` remains the compatibility alias for markers and artifacts.
2. Dispatch in the same worktree (verify reads `git diff HEAD`, runs full test/guard suite, gates merge):

   ```bash
   <skill-root>/scripts/consensus-rnd-cli spawn-codex \
     --cd <worktree> \
     --prompt .refactor-loop/prompts/verify-<cluster-id>.md \
     --log .refactor-loop/logs/verify-<cluster-id>.log \
     --stall 3600
   ```

3. End turn after dispatching all ready verifies. Wait for task notifications.

Verify output marker: `VERIFY_DONE:<cluster-id>:<verdict>` where verdict ∈ `{pass, rework, abort}`.

- `pass` → advance to Consensus-rnd Phase publish (merge).
- `rework` → re-dispatch implement codex with verifier's findings appended.
- `abort` → post the failure/blocking reason and surface in PushNotification.

---

## Consensus-rnd Phase publish — Merge & Push (controller, not codex)

<a id="merge-and-push-details"></a>
### Post-merge trunk build verify(强制)

两个 PR 单独 merge OK,**顺序 merge 后 trunk 可能 build 挂**(API 重命名 + 第二 PR 引用旧名)。merge 后必须:

```bash
cd $REPO_ROOT
git pull --ff-only origin auto-refact-dev
bash -lc "$BUILD_CMD"
```

若 trunk build 错 → 立即派 **hotfix codex**(直接 push 到 `$INTEGRATION_BRANCH`,不开 PR):
- 在 `$REPO_ROOT/.worktrees/hotfix-trunk` worktree 跑 codex 修
- 用 `.refactor-loop/prompts/hotfix-trunk-*.md` 模板(参考 iterN hotfix 模板)
- IMPLEMENT_DONE marker + controller commit/push 到 `$INTEGRATION_BRANCH` 直接

结构性教训:两个独立 PR 各自 CI 绿仍可能在顺序 merge 后引入 trunk build break,典型原因是一个 PR 重命名 API、另一个 PR 仍引用旧名。每次 merge 后必须在 trunk 重新跑 `$BUILD_CMD`,失败则立即派 hotfix codex。

**cwd discipline (critical)**: trunk-side git and GitHub mutations are
active-controller-owned operations. Use the checked-in `ControllerActions`
primitives from `$REPO_ROOT`, never from a worktree directory. Cwd persists
across Bash invocations in the harness, so chained commands that include
`cd "$REPO_ROOT/.worktrees/<id>"` leak cwd into the next call.

For each `pass` cluster, serially:

1. **Commit in worktree**: `cd <worktree> && git add -A && git commit -m "<msg>"`.

2. **Local CI on the cluster branch** (still in worktree):
   ```bash
   if [ -n "${CI_GUARDS:-}" ]; then
     bash "$CI_GUARDS"
     bash "$CI_GUARDS"
   else
     echo "guards skipped: CI_GUARDS unset"
   fi
   # plus any cluster-specific guards from audit.verification_hints
   ```
   On fail → `git reset --soft HEAD~1` (undo the commit), mark cluster `rework`, re-dispatch implement codex with the failure log.

3. **Push cluster branch** through `ControllerActions.safe_push(remote, branch)`;
   it owns the active-controller lease check and remote-behind handling.

4. **Branch off** by `pr_mode`:

### Consensus-rnd Phase publish single — `pr_mode: "single"`

5a. Merge cluster branch into `integration_branch`:
    ```bash
    cd "$REPO_ROOT" && git merge --no-ff refactor/iterN-<cluster-id> \
      -m "Merge cluster-<id>: <short title>"
    ```
6a. Re-run local CI on integration_branch (catches inter-cluster interaction).
7a. `git push origin <integration_branch>`.
8a. Goto Consensus-rnd Phase ci-watch (remote CI watch).

### Consensus-rnd Phase publish stacked — `pr_mode: "stacked"`

5b. **Choose PR base** per the cluster's `dependencies` field from the audit:
    - `dependencies: []` (independent, soft-dep, or batch-disjoint) → base = `integration_branch`.
    - `dependencies: ["cluster-XXX", ...]` (hard-dep — won't compile without the prerequisite) → base = the prerequisite cluster's branch (use the **first**, primary one; document others in PR description).

    **All cluster PRs target the integration branch by default. Never PR directly to `review_base_branch` (dev).** The rollup PR (Consensus-rnd Phase publish stacked step 10b, one per iteration) is the only PR that targets `review_base_branch`. Rationale: cluster PRs stay small and reviewer-friendly; the integration branch holds the cumulative refactor state with merge-conflict resolution done once; the rollup PR is the human gate where iter-level rationale (scorecard, cluster ledger, CI guard adds) lives.

    Edge case — if a maintainer accidentally retargets a cluster PR to `review_base_branch`, the next Consensus-rnd Phase integration-sync sweep detects the mismatch and posts a comment requesting retarget (does NOT auto-edit, to respect maintainer intent).

6b. **Open PR** (**body follows current language policy: 中文 by default; no mandatory parallel English section**):

    Structure the body as:

    ```markdown
    ## Summary / 摘要

    iter<N> <cluster-id>（<严重度>，<rule_ids>）。

    - **Old**：<old_pattern 完整中文一句，来自 human_brief.problem_statement；老 cluster 只有英文时由 controller 翻成中文>
    - **New**：<new_pattern 完整中文一句>

    违反：<对应 CLAUDE.md/AGENTS.md 条款中文摘录>。

    ## Scope / 范围 (language-neutral file list)

    <N files changed (+X/-Y). Targeted test pass counts. Architecture guards green.>

    Inline the implement summary and audit excerpt with `consensus-rnd-cli render-github-body`.
    Local `.refactor-loop/runs/*.md` paths may appear only under `<details><summary>本机调试线索</summary>` and never as the authority source.

    ## Stacked-PR

    Part of iter<N> batch <X>. Base = `<base_branch>`. Rollup target = `<review_base_branch>`.

    🤖 Auto-loop / codex-refactor-loop iter<N>
    ```

    Open the PR through
    `ControllerActions.open_pr_with_label(title, body_file, base, head)`. The
    helper validates the self-contained body, opens the PR, and applies the
    catalog-managed PR label bundle, including `crnd:lifecycle:managed`, in
    the same active-controller-gated path.

    Controller must reject a generated body that reintroduces a parallel `## English` section as a required peer to 中文.

7b. The PR open helper must add the catalog-managed label bundle immediately.
**漏加 → comment-monitor 不监控该 PR 评论 → maintainer 评论无 react 无回复**。漏加是 P0 bug,等同失保。Consensus-rnd Phase publish stacked cannot defer this label sync to the next turn.

7b. Record the PR number in the GitHub banner/comment and run artifact for the active work unit.
8b. **Stack rebase on upstream merge**: when an upstream (dependency) cluster's PR merges into `integration_branch`, immediately:
    - For each downstream cluster whose `dependencies` contained it:
      - Rebase the downstream worktree through the controller's checked-in stack-rebase path (or record a maintainer retarget request if stacked-on-stacked is no longer needed).
      - Re-run local CI in worktree; on conflict, mark cluster `rework` and re-dispatch implement codex with conflict diff.
      - Force-push only through the stack-rebase path's guarded remote update.
9b. Goto Consensus-rnd Phase ci-watch (remote CI watch on the cluster's PR).
10b. After **all** iteration clusters have merged into `integration_branch`, Consensus-rnd Phase integration-sync may emit `DEV_SYNC_PENDING:release-rollup-needed:<json>`. Controller re-checks for an open rollup PR covering the same integration SHA to `$REVIEW_BASE_BRANCH` and, only when none exists, creates it through `open_release_rollup_pr_from_pending_event <event-json> <body-file>`. That helper pushes a one-time `rollup/<integration_sha>` head and opens `rollup/<integration_sha> -> $REVIEW_BASE_BRANCH`; merge auto-delete may delete only the throwaway head, never `$INTEGRATION_BRANCH`. Daemon only detects/writes the event; PR create, labels, review gate, CI, and merge policy stay controller-owned.
After merge of the cluster branch into its target → request cleanup through the
owner-local worktree cleanup primitive. **Do NOT** delete the cluster branch yet
under `stacked` mode — downstream PRs may still reference it as base; let
GitHub auto-delete on merge.

If no clusters left in current batch → start next batch (Consensus-rnd Phase implementation again). If no batches left → start next iteration (Consensus-rnd Phase work-intake again) or **start Consensus-rnd Phase ci-watch if there is an open PR for the trunk/cluster branches**.

### Consensus-rnd Phase publish stack-depth cap

Hard cap: any single dependency stack ≥ 5 PRs deep triggers a controller halt. Reason: rebase blast-radius compounds — reviewer changes to the bottom PR force-rebase the entire stack, and reviewers stop landing PRs that get rebased twice. On cap:
- send PushNotification with the stack contents,
- merge all completed lower PRs into `integration_branch` immediately (collapse stack to a single base),
- continue remaining clusters from the collapsed base.

---

## Consensus-rnd Phase ci-watch — Remote CI watch (controller, after push)

<a id="remote-ci-details"></a>
## Remote CI details

Local CI passing is necessary but not sufficient. Remote CI runs additional jobs that don't fit on the controller machine (kafka integration, projection provider e2e, host composition smoke, codecov, etc.). Consensus-rnd Phase ci-watch watches them and treats remote failures the same way Consensus-rnd Phase verification treats verify failures: dispatch a focused fix codex, loop back through verify/merge.

### When Consensus-rnd Phase ci-watch fires

After every push to `<trunk_branch>` that is the head of an open PR. Detect open PR with:

```bash
PR_NUMBER=$(gh pr list --head "<trunk_branch>" --json number --jq '.[0].number')
```

If no open PR → skip Consensus-rnd Phase ci-watch (local CI is sufficient).

### Read the watch

<!-- Refactor (issue-275): Old pattern: SKILL.md fenced shell 探针含 raw positional $0/$1/$2,skill 带参加载被 clobber。 New principle: 删可执行探针改指 canonical CLI(wakeup-plan ci-red + concurrency --count-only),不在文档放可被位置参数 clobber 的 inline shell。 -->
<!-- Refactor (issue-297): Old: controller runbook copied PR checks CLI
recipes. New: PR-head check facts route through the named read-only
`pr-checks` projection and wakeup-plan consumes that same projection. -->
Do not run a controller-authored shell poller for remote CI. Every controller
wakeup first reads `python3 <skill-root>/scripts/consensus-rnd-cli wakeup-plan
--repo-root "$REPO_ROOT"` and handles any structured action with `kind: "ci-red"`.
For each red PR, the controller reads failed check details through
`python3 <skill-root>/scripts/consensus-rnd-cli pr-checks --repo "$GH_REPO_SLUG"
--pr "$PR_NUMBER" --json`, selects `bucket: fail`, and uses the check `name`
plus `link` for the focused remote-CI fix route.

The persistent daemon-event Monitor bridge remains the wake source for pending controller events; remote CI triage is driven by `consensus-rnd-cli wakeup-plan` output, not by an executable fenced shell watch in SKILL.md.

### Triage on failure

For each `bucket: fail` check:

1. Fetch the failure logs:
   ```bash
   RUN_URL=<link from consensus-rnd-cli pr-checks JSON for the failing check>
   RUN_ID=$(basename "$(dirname "$RUN_URL")")  # parse from link
   gh run view "$RUN_ID" --log-failed > .refactor-loop/logs/remote-ci-<check>-<sha>.log 2>&1 || \
     gh run view "$RUN_ID" --log | tail -200 > .refactor-loop/logs/remote-ci-<check>-<sha>.log
   ```

2. Classify:
   - **Flaky / infra-only** (network timeout, registry unreachable, runner OOM that doesn't recur): retry by `gh workflow run` or pushing an empty whitespace commit; document the `flaky` reason in GitHub and the run artifact.
   - **Real failure tied to merged work**: dispatch a `prompts/remote-ci-fix.md` codex (see template) with the failure log + last 10 cluster commits as input. Treat the resulting fix as a mini-cluster: implement → controller verify (re-run local guards + the specific failing test) → commit → push → Consensus-rnd Phase ci-watch again.
   - **Pre-existing failure unrelated to merged work** (failure exists on `dev` base too): document, do not fix in this PR; surface via PushNotification.

3. `codecov/patch` specifically: this measures coverage on **lines added by this PR**, i.e. the refactor's own new/modified production lines. A refactor-induced patch-coverage drop is the loop's own responsibility — the loop just shipped new code without tests, that is exactly what the loop must close before merge. Treat as a **real failure**:
   - Pull the codecov patch detail via API (`https://api.codecov.io/api/v2/github/<owner>/repos/<repo>/pulls/<pr>`) to identify `patch.misses` + `patch.partials` line ranges per file.
   - Cross-reference with the cluster ledger: each uncovered patch line belongs to a known cluster.
   - Dispatch `prompts/test-add.md` codex per cluster with the uncovered file:line list, target threshold (default 80% patch coverage), and "tests must exercise behavior the cluster introduced (e.g., host external client factory typed-client path, head-index cursor compaction trigger, compiled-delegate exception path, projection session lease lifecycle)".
   - Test-add codex output joins the cluster's branch and re-pushes; codecov re-evaluates.
   - **Exception** (info-only ack): if `head_totals.coverage - base_totals.coverage > -0.5%` (i.e. project coverage barely moved) AND the cluster summary explicitly declared deletion-heavy refactor, you may ack the codecov failure with a PushNotification explaining the math; do not silently dismiss.

### Loop control under Consensus-rnd Phase ci-watch

- Cap remote-ci fix attempts per check at **2**. After 2 attempts on the same check → post reason `remote-ci-stuck`, send PushNotification, stop the loop.
- Consensus-rnd Phase ci-watch may overlap with Consensus-rnd Phase implementation of the next iteration. If a new cluster's local CI passes but remote CI is still failing on a prior commit → push anyway (CI re-runs on each push); the watch picks up the latest checks.

---

## Consensus-rnd Phase integration-sync — Integration branch auto-sync with `review_base_branch` (heartbeat)

<a id="daemon-command-bodies"></a>
## Daemon command bodies

All seven daemon command bodies below are active-controller-owner-only in multi-device mode. The owner may start/maintain `concurrency_monitor`, `comment-monitor`, `codex-progress-reporter`, `dev_sync_daemon`, `phase9_router_daemon`, `closed_label_reconciler`, and `wakeup_runner_daemon`; a non-owner `restart-daemons` is a noop with `active_controller=noop:not-owner`. Read-only `peek` and `statusline` remain allowed on non-owner devices.

Consensus-rnd Phase integration-sync is owned by the singleton daemon, not by controller wakeup shell commands. The goal is to keep `integration_branch` continuously up-to-date with `review_base_branch` so cluster PRs base on fresh code and the eventual rollup PR has minimal merge conflicts.

### Consensus-rnd Phase integration-sync 现在由独立 daemon 自主完成

**`<skill-root>/scripts/consensus-rnd-cli dev-sync`** 是独立 daemon,**600s 周期**自主跑 sync,不依赖 controller wakeup:

```bash
nohup bash -c 'test -n "${CONSENSUS_RND_HOST_ENV:-}" && source "$CONSENSUS_RND_HOST_ENV" && exec python3 <skill-root>/scripts/consensus-rnd-cli dev-sync' \
  >> .refactor-loop/logs/dev-sync-daemon.log 2>&1 &
disown
```

Daemon 工作流由 `integration sync daemon` 命名状态机表达:
1. `FETCH`: fetch origin in the daemon worktree.
2. `CHECK_MERGE`: if a merge is in progress with unresolved paths, observe or dispatch exactly one resolver codex. Resolver codexes resolve and stage files only; they never continue, push, reset, or abort. If all paths are resolved, the daemon executes a `continue-resolved-merge` operation and pushes through the #53 allowlist.
3. `CHECK_DIRTY`: dirty non-merge worktrees skip without reset.
4. `PRESERVE_LOCAL_AHEAD`: compute `local_ahead_count` with `git rev-list --count origin/$INTEGRATION_BRANCH..HEAD`; if the daemon worktree is clean and ahead, write and execute a `push-local-ahead` integration sync operation. This preserves resolver continuation commits without controller side-channel latency.
5. `ADOPT_MERGED_ROLLUP`: if a merged rollup PR from `$INTEGRATION_BRANCH` to `$REVIEW_BASE_BRANCH` is provable, capture the old rollup head and current expected remote SHA, then write and execute an `adopt-merged-rollup` operation. The executor re-checks ancestry and live SHAs before force-with-lease adoption.
6. `RESET_TO_REMOTE`: after local-ahead preservation and rollup adoption checks, write and execute a `reset-to-remote` operation for remote alignment when local HEAD differs from `origin/$INTEGRATION_BRANCH`.
7. `FORWARD_SYNC`: when review base needs to be incorporated into integration, write and execute a `forward-sync-review-base` operation. The executor re-checks live state before merge and push.
8. `DETECT_RELEASE_ROLLUP_NEEDED`: if `origin/$INTEGRATION_BRANCH` is ahead of `origin/$REVIEW_BASE_BRANCH` by at least `RELEASE_ROLLUP_MIN_COMMITS` and no open rollup PR already covers that integration SHA, append `DEV_SYNC_PENDING:release-rollup-needed:<json>` with branch names, SHAs, ahead count, timestamp, and reason. Cooldown only suppresses duplicate same-SHA events; it grants no lifecycle authority.
9. `MISSING_INTEGRATION_BRANCH_ALERT`: daemon startup/tick verifies `origin/$INTEGRATION_BRANCH` exists via `git ls-remote --exit-code --heads origin "$INTEGRATION_BRANCH"`. If missing, append `DEV_SYNC_PENDING:missing-integration-branch:<branch>` and stop the tick without guessing or recreating the branch.
Ambiguous adoption metadata, failed adoption operation construction, or adoption conflicts append `rollup-adoption-ambiguous` to `.refactor-loop/.controller-pending-events.log`; that ambiguity blocks only force-with-lease adoption. The same tick continues to `RESET_TO_REMOTE`, `FORWARD_SYNC`, and `DETECT_RELEASE_ROLLUP_NEEDED`, and those later phases keep their own live-state, expected-SHA, and dirty-worktree fail-closed checks. Controller reads pending events and posts the visible GitHub card when action is needed.

### Consensus-rnd Phase design-consensus router daemon command body
Authorization source: `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#phase9-router-open-state-gate-229`.
`consensus-rnd-cli phase9-router` 是单例 daemon,只读 open managed issue list、clean-exit logs、私有 ledger,以及每条 direct route 在 prompt/intent/ledger side-effect 前的 source-OPEN gate GitHub issue read:`gh api repos/<slug>/issues/<N>`。That REST issue read is allowed to consume issue state/title/body only for the source-OPEN gate and router-injected issue source snapshot; comments may be read with `gh api repos/<slug>/issues/<N>/comments?per_page=20` only for bounded recent comments in the same router-local prompt-source projection. The state-only mirror token for the same source-OPEN gate is `gh api repos/<slug>/issues/<N> --jq .state`. The router-injected issue source snapshots are router-local prompt context, not durable schema, host production SSOT, or lifecycle authority; this does not grant daemon process-spawn, durable schema, host production SSOT, or lifecycle authority. DesignConsensusIssueIntake 只可用 `gh issue list --repo <owner/repo> --state open --label crnd:lifecycle:managed --json number,title,labels` 发现 open managed `crnd:phase:design-solving` issue;该 read-gh 只读取 issue list/issue state;非 OPEN 或 state 不可证明时 fail closed,不写 spawn intent、不写 dispatch ledger,只追加 existing-format `phase9-router-fallback` pending event,reason ∈ `phase9-source-not-open` / `phase9-source-state-unavailable`。Before DesignConsensusIssueIntake or converge-to-next-solvers queues solver intents, the terminal design-consensus gate suppresses solver dispatch when a clean consensus judge log exists (`META_JUDGE_DONE:consensus:*` from `phase9-issue<N>-r*-judge.log` or `meta-judge-issue<N>-r*.log`) or when the already-loaded/open issue labels or labels-only live read `gh api repos/<slug>/issues/<N> --jq '[.labels[].name]'` show terminal design-consensus phase labels `crnd:phase:consensus-reached`, `crnd:phase:implementing`, `crnd:phase:merged`, or `crnd:phase:closed`; it appends existing-format `phase9-router-fallback` pending events with key prefix `phase9-terminal-eligibility:` and reason `phase9-already-consensus`, without writing spawn intent or dispatch ledger. solver-triplet-to-judge route 必须渲染完整 `prompts/meta-judge.md` template,绑定 issue/work-unit/producer/source-ref/round、三个 scoped solver paths 和 judge output path;manual issue provenance uses `WORK_UNIT_PRODUCER=manual-issue (prompt-only provenance)` and `WORK_UNIT_SOURCE_REF=gh-issue-<N>`. missing template 或 scope 校验失败 fail closed,不写 spawn intent、不写 dispatch ledger,只追加 `phase9-router-fallback`,reason ∈ `phase9-meta-judge-template-unavailable` / `phase9-meta-judge-scope-invalid`。启动:`nohup bash -c 'test -n "${CONSENSUS_RND_HOST_ENV:-}" && source "$CONSENSUS_RND_HOST_ENV" && exec python3 <skill-root>/scripts/consensus-rnd-cli phase9-router --daemon --repo-root "$REPO_ROOT"' >> .refactor-loop/logs/phase9-router-daemon.log 2>&1 & disown`
Verification: `test_phase9_router_open_state_gate.py`, `test_phase9_router_daemon.py`, `test_cli_command_router.py`, `test_runtime_exception_authorization_sources.py`, and `test_skill_reference_anchors.py`.
One-shot:`python3 <skill-root>/scripts/consensus-rnd-cli phase9-router --once --repo-root "$REPO_ROOT"`; dry-run:`python3 <skill-root>/scripts/consensus-rnd-cli phase9-router --once --dry-run --repo-root "$REPO_ROOT"`; monitor:`tail -50 .refactor-loop/logs/phase9-router-daemon.log`。
Allowlist(唯一 direct spawn-intent authority):
There are five built-in phase9 direct routes.
- DesignConsensusIssueIntake: open `crnd:lifecycle:managed` + `crnd:phase:design-solving` issue, source-OPEN gate passed → queues each r1 solver role (`minimal`, `structural`, `delete`) whose role-specific ledger key, r1 evidence/log, and in-flight target are absent as that role's r1 `HARNESS_SPAWN_INTENT` with router-rendered prompt/log/output paths and manual issue provenance; existing evidence/log/in-flight for one solver role suppresses only that role.
- `SOLVER_DONE:<minimal|structural|delete>:*` x3, same issue/round, clean `^EXIT=0`, non-placeholder, not ledgered, not in-flight → render full `prompts/meta-judge.md` with router-scoped inputs and queue same-round meta-judge `HARNESS_SPAWN_INTENT`.
- `META_JUDGE_DONE:converge:round-<N>:*`, clean exit, not ledgered/in-flight → clean rS judge canonical payload is `round-S`; legacy `round-(S+1)` is temporarily accepted; before queueing r(S+1) minimal/structural/delete solver intents, run the router-owned stalled predicate(`round >= 3` and solver verdict text unchanged across 3 rounds); if it holds, queue round-S reflector intent with the full `prompts/meta-reflector-stalled.md` template and suppress next solvers; non-adjacent payload mismatch falls back.
- legacy `META_JUDGE_DONE:escalate:stalled:*`, clean exit + stalled predicate + judge-role/source-OPEN gates → read-only compatibility replay that queues reflector intent with the full `prompts/meta-reflector-stalled.md` template plus the 3 recent rounds x 3 solver log path evidence; template read failure must fail closed in the spawned prompt, not fall back to a generic route.
- `META_RESOLVED:re-design` from reflector to source-adjacent `marker.round + 1` solver triplet: clean exit, source-OPEN gate passed, not ledgered/in-flight, terminal gate open → queue minimal/structural/delete solver `HARNESS_SPAWN_INTENT`; collisions are suppressed by ledger/log/pending/in-flight/terminal/source-open gates, not by skipping to a later round.
HostWorkflowSpec is not a phase9 direct-spawn-intent authority: host `roles`, `dispatch`, and `consensus_policies` are validation/display/data-only projection surfaces and must not alter this allowlist or block the built-in router routes.
Input filename dialect allowlist:`phase9-issue<N>-r<R>-<minimal|structural|delete|judge|reflector>.log`,`solver-issue<N>-r<R>-<minimal|structural|delete>.log`,`meta-judge-issue<N>-r<R>.log`。issue/round 来自 filename identity,public marker payload remains role-local(`SOLVER_DONE:<role>:...`); must not introduce public marker aliases, migrated work-unit schema, ControllerOrchestrator, ControllerEvent, ControllerCommand, or lifecycle authority. daemon-owned output logs remain `phase9-issue...`;legacy input logs 只作为读取兼容面。daemon startup / first wakeup 文本必须与 `consensus-rnd-cli restart-daemons` 的 6-daemon restart-helper-managed 面一致,包含 Consensus-rnd Phase design-consensus router; persistent daemon-event Monitor bridge 单独由 controller arm。
Fallback/ledger/recovery: lifecycle/unknown markers append `.refactor-loop/.controller-pending-events.log`; no direct codex process spawn, no direct resolution, no git, no GitHub lifecycle mutation, no label, no lifecycle authority(no close/merge/release). Direct routes append `HARNESS_SPAWN_INTENT`; its `command` field is exactly `"spawn-codex"` as a closed semantic enum, not argv and not shell. The mirror token is `command: "spawn-codex"`; forbidden boundary includes daemon direct `nohup spawn-codex`. `argv`, `args`, `shell`, `cmd`, `commands`, `env`, `git`, `gh`, `executor`, `target_ref`, argv array, shell command, and generic command bus fields are forbidden; actual CLI binary and argv construction live only in the controller/harness consumption layer. Append-only `.refactor-loop/phase9-router-ledger.jsonl` records base dispatch fields `{key, marker, log_path, dispatched_at, dispatch_state}` where `dispatch_state="harness-intent"` means intent queued, not process launched; successful solver-triplet-to-judge rows may add row-level router-private provenance fields `{route, issue, round, target_actor, clean_exit_solver_logs, solver_input_prompts, judge_input_solver_logs, judge_prompt_path, judge_prompt_template_path, judge_prompt_scope, independence_check}`. Router recovery/idempotency reads only `key`, and meta-judge decisions read solver logs, not ledger evidence. If router-visible solver prompts explicitly reference same-round peer solver logs/prompts/run artifacts, the router fails closed before judge dispatch and appends an existing-format pending event with reason `phase9-triplet-evidence-invalid`; if full `prompts/meta-judge.md` rendering is unavailable or any solver/judge path falls outside same issue/round/role scope, the router fails closed before judge dispatch and appends an existing-format `phase9-router-fallback` event. Fallback events use prefix `phase9-router-fallback`. A solver-triplet-to-judge duplicate with `key` already in the ledger is silent; when the triplet is not ledgered but target log / equivalent legacy judge log / in-flight target suppresses dispatch, append one existing-format `phase9-router-fallback` event with key prefix `phase9-triplet-suppression:` and reason exactly one of `phase9-triplet-target-log-exists`, `phase9-triplet-equivalent-log-exists`, or `phase9-triplet-in-flight`. Terminal design-consensus suppression appends one existing-format `phase9-router-fallback` event with key prefix `phase9-terminal-eligibility:` and reason exactly `phase9-already-consensus`; restart recovery seeds `phase9-terminal-eligibility:` keys from pending events to keep duplicate fallback emission suppressed. The source-OPEN gate and issue source snapshot reads, state-only source-OPEN gate mirror token, and labels-only terminal gate must not use gh issue close, gh issue edit, gh label, gh pr merge, gh release, or any label/close/merge/release lifecycle flag. In-flight target logs or live `consensus-rnd-cli spawn-codex --log <target>` suppress re-dispatch, `.refactor-loop/phase9-router.lock` enforces singleton, and duplicate ledger rows never delete logs. Staged expansion requires route-ledger evidence and must not introduce ControllerEvent, ControllerCommand, SpawnIntentInbox, spawn-intents, ControllerOrchestrator, migrated work-unit schema, public marker aliases, or lifecycle authority.
### Daemon vs controller 分工
dev sync stays with daemon; Consensus-rnd Phase design-consensus issue intake, triplet/converge/router-derived stalled continuation, legacy stalled compatibility, and reflector `META_RESOLVED:re-design` continuation may use **consensus-rnd-cli phase9-router** narrow allowlist with controller fallback sweep retained; wakeup-plan design-consensus completed-marker evidence is status-only and suppresses design-consensus solver `HARNESS_SPAWN_INTENT` actions for terminal phase labels only, and runner never applies `dispatch_next_step_worker`; consensus/implement/review/fix/liveness/escalation stay with controller wakeups or named #396 helpers.
### Controller 每 wakeup 责任(只 verify daemon)
```bash
# Consensus-rnd Phase integration-sync 现在 controller 只读 daemon-maintained health/log surface
python3 <skill-root>/scripts/consensus-rnd-cli daemon-status --json
python3 <skill-root>/scripts/consensus-rnd-cli concurrency --count-only >/dev/null
python3 <skill-root>/scripts/consensus-rnd-cli peek | tail -80
tail -10 .refactor-loop/logs/dev_sync_daemon.log | grep -E "(DEV_SYNC_BLOCKED|FAIL|FATAL)" | tail -3
```
若 daemon-status 报 owner daemon `stale` / `dead` → 由 `consensus-rnd-cli restart-daemons` 按 canonical wrapper repair/reload。
若发现 `DEV_SYNC_BLOCKED` → controller post 卡片到 rollup PR / 通知 maintainer。若发现 `DEV_SYNC_PENDING:release-rollup-needed:<json>` → controller 重新查是否已有覆盖同一 integration SHA 的 open rollup PR;已存在则 ledger/suppress,否则生成中文 body 并调用 `open_release_rollup_pr_from_pending_event <event-json> <body-file>`,由 helper 创建 `rollup/<integration_sha> -> $REVIEW_BASE_BRANCH`。该 PR 进入既有 Consensus-rnd Phase review-gate 与 CI/merge policy。
### 反面(❌ 禁止)

- ❌ controller 自己跑 `git merge dev` 同步(daemon 已做,会 race / 冲突)
- ❌ daemon push 后 controller 不 fetch 就 commit(stale base bug)
- ❌ Daemon 派 codex 自己 push(daemon 决定 push 时机,codex 只 resolve + merge --continue)
- ❌ controller 用 process probe 判断 daemon 单例;controller 只读 `consensus-rnd-cli daemon-status --json`,单例与 pid/kill 细节只属于 `consensus-rnd-cli restart-daemons` / daemon 自身 helper 实现。

### Manual recovery

If a maintainer must repair the daemon worktree manually, stop the singleton `consensus-rnd-cli dev-sync` first, verify there is no resolver codex in flight, then repair the dedicated worktree. Restart the daemon only after the branch topology is clean and `git status` is clean.

### Post-rollup adoption invariant

After a rollup PR has merged into `review_base_branch`, `integration sync daemon` must make `integration_branch` contain that merged review-base head before new forward sync work. Any post-rollup integration commits are replayed only after the proven old rollup head; if the old head or expected remote SHA cannot be proven, the daemon writes a pending event and does not force-push.

### Why this matters

- Without auto-sync, the integration branch drifts from dev and the eventual rollup PR becomes one giant conflict resolution.
- Cluster PR diffs viewed by reviewers should be just the cluster's changes; if integration is stale, the PR shows a noisy diff that mixes cluster work with "what dev added since" which is reviewer-hostile.
- Sync conflicts are rare but real (e.g., a dev PR refactored the same area). Surfacing them as halts is better than silently posting a busted integration.

---

## Consensus-rnd Phase design-intake — Design-issue watch (sweep on every wakeup)

<a id="design-issue-details"></a>
## Design issue details

Runs **after Consensus-rnd Phase integration-sync sync** and **before** any new Consensus-rnd Phase implementation / 3 / 4 / 5 cluster work on every controller wakeup (whether triggered by user `/loop`, ScheduleWakeup, or task-notification). Goal: detect when a paused-for-design cluster has a maintainer response and resume it.

### 外部 issue 接入(强制)

**问题**:audit codex 自动产生的 design issue 走完 Consensus-rnd Phase design-consensus 链路;但 maintainer 或其他人手动开的 issue(无 `crnd:lifecycle:managed` label)不接入,controller 看不见。

**两条 onboarding path**:

#### Path A — 手动 label opt-in(已现成支持)

maintainer applies the catalog-derived design-issue label bundle from
`consensus-rnd-cli labels design-issue-labels`.

Controller 下次 wakeup sweep reads `crnd:lifecycle:managed` and normalizes via `codex_refactor_loop.labels`,把它当 Consensus-rnd Phase design-consensus candidate,直接派 r1 三 solver + meta-judge。Solver prompt 自包含,会读 issue body 全文 + grep 相关代码自找 evidence。

**前提**:issue body 至少要描述 "what's broken + relevant file paths"。Body 越结构化(evidence / fix boundary / decision questions)solver 越准。

#### Path B — Triage codex / `manual-issue` producer(推荐,更安全)

maintainer applies the catalog-defined triage-pending label.

This path is the `manual-issue` producer. The triage codex accepts only concrete repository
work units suitable for consensus, reshapes the issue into a work-unit-backed design issue, and
then label-routes it to Consensus-rnd Phase design-consensus. Accepted manual issues must contain `work_unit_id: issue-<N>`,
`kind: manual-work-unit`, `producer: manual-issue`, `source_ref: gh-issue-<N>`, `scope_paths`,
problem/invariant text, and `verification_hints`; they must not include fabricated `cluster_id` or
`legacy_cluster_id`.

**Daemon 自包含**:

Controller wakeup sweep handles external `crnd:triage:pending` issue intake; `triage-monitor.sh` is deleted. Triage workers emit a manual issue triage decision artifact plus `TRIAGE_DECISION_DONE:<issue>:<accept|reject>:<path>`, and controller apply helpers re-read live labels before body/label lifecycle.


<a id="review-gate-details"></a>
## Consensus-rnd Phase review-gate details

## Consensus-rnd Phase review-gate — Multi-codex PR review with consensus merge

<!-- Refactor (iter3/skill-merge-policy): Old pattern: unanimous-approve merge gate + Consensus-rnd Phase review-gate 文案矛盾  New principle: 固定真值表 reject=0 && approve>=1 → MERGE;comment 是 advisory(#26 minimal option B 共识) -->

Runs when a cluster PR's remote CI is green (Consensus-rnd Phase ci-watch settled with pass) and the PR is mergeable. Goal: 3 (or more) independent codex reviewers from **different angles** verify the PR at the current head SHA; the controller then chooses exactly one action from `MERGE`, `MERGE_WITH_COMMENTS`, `WAIT_EXPLICIT_APPROVAL`, `FIX`, or `WAIT_OR_REDISPATCH`.

### Default reviewer roles

- **Architect** (`prompts/reviewer-architect.md`): CLAUDE.md / AGENTS.md clause compliance.
- **Tests** (`prompts/reviewer-tests.md`): test coverage on net-new logic, no `[Skip]` / `sleep/delay` sneaking in, no loosened assertions.
- **Quality** (`prompts/reviewer-quality.md`): naming / dead code / over-engineering / readability / refactor self-doc clarity.

Optional (add when cluster touches the relevant area, audit's `rule_ids` decides): Perf (future), Security (future).

### Dispatch (parallel)

For each cluster PR with `CI green AND mergeable AND not yet auto-reviewed`:

```bash
for role in architect tests quality; do
  envsubst < <skill-root>/prompts/reviewer-${role}.md \
    > .refactor-loop/prompts/review-pr${PR_NUMBER}-${role}.md
  <skill-root>/scripts/consensus-rnd-cli spawn-codex \
    --cd "$REPO_ROOT" \
    --prompt .refactor-loop/prompts/review-pr${PR_NUMBER}-${role}.md \
    --log .refactor-loop/logs/review-pr${PR_NUMBER}-${role}.log \
    --stall 3600 &
done
```

All reviewers in parallel background; one task-notification per reviewer when done.

### Consensus rules

Each reviewer outputs `REVIEW_DONE:${PR}:${role}:<approve|comment|reject>` marker.

`comment` is terminal advisory evidence. It is not approval and is not a fix trigger; comments are surfaced to the PR and to fix codex as context only when a `FIX` round happens for rejects.

| Preconditions | Latest complete required round | Controller action |
|---|---|
| CI green, PR mergeable, every required reviewer head SHA equals the live PR head, every required role has exactly one valid marker after `EXIT=0` | `reject=0`, `approve=R`, `comment=0` | `MERGE`: post 中文 merge comment, then call `merge_pr <pr>` for ready+merge. |
| Same preconditions | `reject=0`, `approve>=1`, `comment>=1`, `approve+comment=R` | `MERGE_WITH_COMMENTS`: surface comment evidence, post 中文 merge comment, then call `merge_pr <pr>` for ready+merge. |
| Same preconditions | `reject=0`, `approve=0`, `comment=R` | `WAIT_EXPLICIT_APPROVAL`: surface comments, do not ready, do not merge, do not dispatch fix. |
| Same preconditions | `reject>=1` | `FIX`: enter fix-retry loop; do not ready, do not merge; fix codex consumes reject evidence as blocking input and comments as context. Do NOT escalate to human on first reject. |
| Any gate incomplete or invalid | missing role, duplicate/unknown verdict, no `EXIT=0`, missing/stale per-reviewer head SHA, CI pending/fail, or non-mergeable PR | `WAIT_OR_REDISPATCH`: wait or re-dispatch invalid/missing reviewer once; do not ready, never merge. |

### Fix-retry loop (AI iterates until consensus)

Policy: AI keeps iterating until the fixed Consensus-rnd Phase review-gate truth table resolves to `MERGE` or `MERGE_WITH_COMMENTS`, OR until escalation criteria are hit. Default `max_fix_rounds = 3` per PR。

Loop:

1. **Round entry** — derive the next fix round from review/fix artifacts. If `fix_round > max_fix_rounds`, escalate (see below).
2. **Render and dispatch fix codex** in PR's own worktree. Before spawn, call `ControllerActions.render_review_fix_prompt(PR, N, env)` or an equivalent controller-internal render action; this binds `FIX_OUTPUT_PATH=.refactor-loop/runs/fix-pr${PR}-round-${N}-report.md` into the rendered prompt artifact:
   ```bash
   <skill-root>/scripts/consensus-rnd-cli spawn-codex \
     --cd "$PR_WORKTREE" --add-dir "$REPO_ROOT" \
     --prompt .refactor-loop/prompts/fixes/fix-pr${PR}-round-${N}.md \
     --log .refactor-loop/logs/fix-pr${PR}-round-${N}.log \
     --stall 3600
   ```
   <!-- Refactor (issue-267): Old: fix worker wrote root FIX_REPORT.md by convention. New: controller-rendered FIX_OUTPUT_PATH points to .refactor-loop/runs/. -->
   Fix codex reads all 3 reviewer outputs, applies in-scope fixes, validates locally, writes `${FIX_OUTPUT_PATH}` under `.refactor-loop/runs/`, emits `FIX_DONE:${PR}:round-${N}:applied-<N>:rejected-<M>:blocked-<K>` OR `FIX_BLOCKED:${PR}:round-${N}:<reason>:<short>`.
3. **Controller commits + pushes** the fix codex's changes to the PR's HEAD branch (codex itself doesn't push, per hard rule 4). Commit message includes round number and applied/blocked counts.
4. **Re-dispatch all 3 reviewers** against the new HEAD SHA (drop prior consensus).
5. **Re-evaluate**:
   - Unanimous approve → auto-merge (per table above).
   - Same reject reasons as previous round (no progress) → escalate.
   - New reject reasons but still <unanimous → go to step 1.

### Escalation criteria ("十分难搞" — truly stuck)

Escalate to human ONLY when the meta-layer cannot make progress:

- `fix_round > max_fix_rounds` (default 3) and still not unanimous → **不要直接升 human,先升 meta-layer**(see "## Meta-layer escalation" 下文)。只有 meta-layer 也无法解才升 human;architecture/philosophy/Tier 触发不是人工 gate。
- Fix codex emits `FIX_BLOCKED:<PR>:round-<N>:human-decision:<...>` (e.g. reviewer demands deleting a feature, splitting into 3 PRs, renaming a cross-cluster type).
- Fix codex emits `FIX_BLOCKED:<PR>:round-<N>:conflict:<...>` (reviewers' demands contradict each other and codex cannot resolve).
- Two consecutive rounds produce IDENTICAL reject text for the same reviewer (the fix didn't address the demand and codex isn't making progress).
- A reviewer's demand requires touching another in-flight cluster's PR (would create cross-PR dependency).

Escalation action:
- Add `crnd:human:maintainer-decision` label on PR.
- Post 中文 PR comment with: round history (N rounds tried), reject evidence per round, what fix codex tried, why it's stuck.
- `PushNotification`: "PR #N stuck at round N — human decision needed: <one-line reason>".
- State: `pr_reviews[PR].consensus = "stuck-human-review"`.

### Anti-spiral safeguards

- Round-N reviewer outputs MUST be diffed against round-(N-1). If reviewer text didn't change but verdict didn't change either → that reviewer is stuck on a non-addressable demand → escalate.
- Each fix round must reduce total reject count OR change which reviewer rejects. If neither → escalate.
- Cumulative PR diff size grows by ≤ +30% per round; if a fix round adds more code than the original PR → controller flags scope-runaway and escalates.

### GitHub traceability (mandatory — every Consensus-rnd Phase review-gate action posts to the PR)

All review/fix/consensus/escalation behavior MUST be observable on GitHub so the whole loop is traceable without reading local `.refactor-loop/` artifacts. Authority-bearing GitHub bodies(PR description, design issue body, consensus/authorization comment, escalation comment, triage body-comment) must inline the cited artifact text; local `.refactor-loop/runs/*.md` paths are allowed only under `<details><summary>本机调试线索</summary>` and never as the only source. Natural-language GitHub posts follow the skill language rule.

**Hard rule**: all natural-language GitHub posts go through the codex role that produced the artifact, NOT directly composed by controller.

The controller's only inline composition allowed for GitHub:
- Status one-liners (≤ 80 chars, e.g. "labels updated").
- Mechanical link / SHA / cluster id mentions.
- Programmatic label edits + merge actions.

EVERYTHING ELSE(reviewer verdict、fix-done body、consensus 公告、escalation rationale、design issue body、cross-post 通知、PR description 包括 rollup PR)由**正在跑的那个 codex 自己 post**,**不需要专门的 writer-codex 中介**:

<!--
Refactor (iter6/issue-118):
  Old pattern: skill docs maintained a posting-mode prompt filename roster,会漂移
  New principle: prompt-self-declaration posting mode is owned by the GitHub Posting Contract, prompts/_github-post-rules.md, prompt body self-declaration, test_marker_only_prompts_gh_ban.py, and test_marker_emission_contract.py; no SKILL-maintained prompt filename roster.
-->

- A prompt is direct-post only when its own body contains a `## GitHub post` section with fixed token `{{GITHUB_POST_RULES_CONTRACT}}`; prompts without that self-declaration are marker/artifact-only. `_github-post-rules.md` is the template-time source only, and rendered worker prompts inline the shared rules body instead of relying on a worker runtime path.
- `SKILL.md` 不维护 posting-mode prompt filename roster,也不引入 JSON manifest 或 helper contract; inventory coverage 由 prompt body + tests 承担。
- Direct-post prompts keep to GitHub comments, PR body edits, reactions, and temp files. Lifecycle/label/create/close/merge/push/release authority remains controller-owned.
- body 必须 `## 🤖 <headline>` 开头(consensus-rnd-cli comment-monitor 据此识别 controller-post 跳 react)
- 中文 only / TL;DR ≤ 6 行 / raw artifact 折叠 `<details>` / 若 situation 给 `original_authors:` 加 `📢 cc`
- codex 自己抓 gh 输出的 URL,打 `POSTED:<role>:<N>:<URL>:<headline>` 或 `POST_FAILED:...`
- controller 只读 log 末尾 marker,**不读 body**

Rationale: 减少一跳 + 减少 controller 上下文负担 + 写 post 的 codex 本身就是最了解 artifact 的人,质量比 "翻译者" 更高。controller 边界仍是 git topology(commit/push/checkout)+ PR/issue 创建/merge/close lifecycle 决策,这些 codex 不动(per `_github-post-rules.md` "你不能调的" 列表)。

**@-mention rule:**

Every design issue body AND every escalation comment MUST include an "📢 cc 原作者 / cc original authors" section with `@<github-handle>` of the top 1-3 commit authors per evidence file (via `git blame --line-porcelain | uniq -c`). Handle mapping (current team):

| git author | GitHub handle |
|---|---|
| maintainer | @<maintainer-handle> |
| maintainer | @<maintainer-handle> |
| maintainer / maintainer | @<maintainer-handle> |
| jason | @<maintainer-handle> |
| maintainer | @<maintainer-handle> |
| potter / maintainer | @<maintainer-handle> |

The audit codex captures `original_authors` per cluster (top blame authors across evidence files); the writer-codex emits the @-mention block from that input. If git blame extraction fails or returns unknown handle, fall back to "@<maintainer-handle>" alone with a note that auto-mention was incomplete.

Required PR comments (controller posts via `gh pr comment <PR> --body-file <file>`):

| Consensus-rnd Phase review-gate event | PR comment content |
|---|---|
| Reviewer round N complete | 中文 table of 3 verdicts + reject demands per role + "next action" (fix-retry dispatched OR auto-merge OR escalation). Link to commit SHA reviewed. |
| Fix codex round N complete (FIX_DONE) | 中文 fix artifact excerpt from `${FIX_OUTPUT_PATH}`: applied / rejected-as-false-positive / blocked counts, build+test status, files changed. Link to fix commit SHA. |
| Fix codex blocked (FIX_BLOCKED) | 中文: which reason category (conflict / human-decision / build-broken), reviewer demand text, controller's escalation decision. |
| Consensus reached (`MERGE` / `MERGE_WITH_COMMENTS`) | 中文: round count, final reviewer outputs, surfaced comment evidence when present, "auto-merging now". Then merge + a second "merged at <commit>" comment. |
| Escalation triggered | Add `crnd:human:maintainer-decision` label. Comment includes: full round history, latest verdicts, why escalation criteria hit, what controller tried. PushNotification mirrors the headline. |
| Reviewer crash | 中文: which reviewer, log path, re-dispatch attempt. Second crash → escalate per above. |

Required GitHub labels (controller applies/removes):
- `phase8-reviewing`: a reviewer round is in flight
- `phase8-fixing`: a fix codex round is in flight
- `phase8-consensus-pending`: consensus computation in progress
- `crnd:human:maintainer-decision`: escalated
- `phase8-merged`: auto-merged after consensus (removed by merge action)

Local-only files (logs, raw codex output, internal state) stay in `.refactor-loop/` and are NOT posted (would spam the PR). The PR comment must summarize enough that a reader can decide whether to read the local artifact, and link the exact local path.

Forbidden:
- Posting the same content twice in the same round.
- Posting reviewer/fix output with deprecated mandatory bilingual sections.
- Auto-merging without first posting the "consensus reached" comment.
- Escalating without first posting the escalation rationale comment.

### State tracking

```json
"pr_reviews": {
  "<PR_NUMBER>": {
    "head_sha": "<sha at review dispatch>",
    "dispatched_at": "<ISO8601>",
    "reviewers": {
      "architect": {"verdict": "approve|comment|reject", "rationale_path": "...", "log": "..."},
      "tests": {...},
      "quality": {...}
    },
    "consensus": "MERGE | MERGE_WITH_COMMENTS | WAIT_EXPLICIT_APPROVAL | FIX | WAIT_OR_REDISPATCH",
    "merged_at": "<ISO8601|null>",
    "auto_merge_commit": "<sha|null>"
  }
}
```

### Re-review on push

If PR is pushed after consensus (rebase, requested change), head SHA changes. Next Consensus-rnd Phase review-gate sweep requires every required reviewer artifact head SHA to match the current head SHA; any missing or stale per-reviewer head drops prior consensus and re-dispatches all reviewers. Review artifact verdict authority does not bypass current-head binding; merge readiness requires every required reviewer artifact to bind to the live PR head. Never auto-merge stale consensus.

### Idempotency

Skip a PR in Consensus-rnd Phase review-gate if any of:
- already merged / closed
- `crnd:human:maintainer-decision` label present (operator handling)
- consensus recorded for current head SHA AND not stale

### Why three angles, not one

A single reviewer codex would weigh all dimensions and might trade tests for architecture or vice versa. Three independent codexes with bounded scopes are harder to convince than one — a real defect tends to hit one role hard rather than all three lightly. Consensus across orthogonal angles is the actual signal.

---

<a id="design-consensus-details"></a>
## Consensus-rnd Phase design-consensus details

## Consensus-rnd Phase design-consensus — Multi-solver design consensus (sole authorization gate)

Runs when a GitHub design issue / Consensus-rnd Phase design-consensus artifact needs a concrete implementation decision.
Current audit-backed items expose `WORK_UNIT_ID=$CLUSTER_ID` so Consensus-rnd Phase design-consensus can frame the decision as
work-unit design while preserving `cluster_id` as legacy routing metadata. Goal: 3 independent
solver codexes produce mandatory outputs from different biases; a 4th meta-judge codex arbitrates;
**all implementation-bearing proposals agree + meta-judge consensus → auto-dispatch implement**.
Deep consensus is the only sufficient authorization gate for every change, including Tier I,
Tier II, `CLAUDE.md`, `SPEC.md`, conformance, and core abstractions. There is no post-consensus
maintainer approval, physical GPG ratification, reinstall ratification, or philosophy escalation gate.

Policy: all three solver outputs are mandatory, and all implementation-bearing proposals must agree. The only compatible-neutral mixed verdict is built-in Path A issue-driven greenfield: router-rendered `WORK_UNIT_PRODUCER=manual-issue (prompt-only provenance)` plus `WORK_UNIT_SOURCE_REF=gh-issue-<N>`, issue body/comments plus delete reverse-evidence proving greenfield/no current deletion target, two matching implementation-bearing proposals, and delete `abstain` that does not contradict the plan. This is not a generic 2/3 gate and has no host override; missing/unknown/audit-backed/non-greenfield provenance, implementation-bearing disagreement, delete `false-positive:nothing-to-delete`, or delete `escalate:no-plan` goes through convergence until consensus or true stall.

### Solver source contract

<!--
Refactor (iter364/issue364):
  Old pattern: Path-A solvers dispatched with --cd $REPO_ROOT (integration checkout) can't see work-unit source when the issue references files on a divergent non-integration branch, emitting spurious no-plan and wasting rounds.
  New principle: Contract-only source locator: SKILL solver source contract + 3 solver prompts document a read-only source-locator recipe (git show <ref>:<path> / raw URL / gh api / host.env), classify missing/invalid locator as source-location-missing-or-invalid; NO new projection/parser/header/module.
-->

Solver scope comes from the prompt header `WORK_UNIT_SOURCE_REF`, the work-unit `source_ref`, or a local source artifact explicitly pointed to by either field. For issue-driven / Path A work, `WORK_UNIT_PRODUCER=manual-issue (prompt-only provenance)` and `WORK_UNIT_SOURCE_REF=gh-issue-<N>` mean the router-injected issue source snapshot is the preferred scope source when no local audit artifact is provided. The snapshot contains bounded issue title/body and recent comments read by the router for prompt projection only; it is not durable schema, host production SSOT, lifecycle authority, or a replacement for GitHub as the visible state surface. `gh issue view <N>` issue body/comments are fallback-only when the router-injected snapshot is unavailable. If an issue body, snapshot body, or source_ref points to an existing audit artifact, solvers may read and verify that artifact; otherwise missing audit artifacts are valid for issue-driven work and must not be fabricated. For Path A greenfield identity, absence of existing local code to delete is neutral evidence for the delete solver and is compatible with `SOLVER_DONE:delete:abstain:<reason>` when deletion/collapse is not justified.

For Path A issue body/comments that cite files absent from the current checkout, solvers must not directly emit a generic `no-plan`. The issue body/comments may carry a read-only source locator: `git show <ref>:<path>` for a named ref/path, a raw URL, `gh api` for a GitHub object, or a host-provided path from the explicit host-owned file named by `CONSENSUS_RND_HOST_ENV`. Use the locator only to read the cited source; do not fetch, checkout, switch, merge, rebase, reset, create a source worktree, or add a source directory. If the locator is missing or invalid and the current checkout cannot verify the cited source, classify the no-plan reason precisely as `source-location-missing-or-invalid`.

Audit-backed sources require verification of the audit `evidence:` file:line. Issue-driven sources require verification of the cited files, symbols, problem statement, or repo rules present in the issue body/comments. A missing audit `evidence:` block is not by itself a defect for manual issues. The router renders the same producer/source-ref provenance into meta-judge prompts so the judge can recognize Path A greenfield framing instead of treating delete-solver abstain as a failed deletion proof.

### Default solver roles

| Solver | Bias | Prompt |
|---|---|---|
| **minimal** | smallest viable change; documented rule exception OK if scope is genuinely narrow | `prompts/solver-minimal.md` |
| **structural** | CLAUDE-philosophy-aligned; new abstraction allowed if justified; never proposes rule exception | `prompts/solver-structural.md` |
| **delete** | question necessity; propose delete / collapse-and-redirect; abstain if feature genuinely needed or Path A greenfield has no current deletion target | `prompts/solver-delete.md` |

A 4th **meta-judge** codex arbitrates (`prompts/meta-judge.md`).

### Dispatch (parallel)

For each cluster needing Consensus-rnd Phase design-consensus:

```bash
for role in minimal structural delete; do
  envsubst < <skill-root>/prompts/solver-${role}.md \
    > .refactor-loop/prompts/phase9/solve-issue${ISSUE_NUMBER}-r${ROUND}-${role}.md
  <skill-root>/scripts/consensus-rnd-cli spawn-codex \
    --cd "$REPO_ROOT" \
    --prompt .refactor-loop/prompts/phase9/solve-issue${ISSUE_NUMBER}-r${ROUND}-${role}.md \
    --log .refactor-loop/logs/phase9-issue${ISSUE_NUMBER}-r${ROUND}-${role}.log \
    --stall 3600 &
done
```

All 3 solvers in parallel; each emits `SOLVER_DONE:<role>:<verdict>:<summary>`. When all 3 done, dispatch meta-judge:

```bash
envsubst < <skill-root>/prompts/meta-judge.md \
  > .refactor-loop/prompts/phase9/judge-issue${ISSUE_NUMBER}-r${ROUND}.md
<skill-root>/scripts/consensus-rnd-cli spawn-codex \
  --cd "$REPO_ROOT" \
  --prompt .refactor-loop/prompts/phase9/judge-issue${ISSUE_NUMBER}-r${ROUND}.md \
  --log .refactor-loop/logs/phase9-issue${ISSUE_NUMBER}-r${ROUND}-judge.log \
  --stall 3600
```

This triplet dispatch is now the first Consensus-rnd Phase design-consensus daemon-first route: `consensus-rnd-cli phase9-router` may do it directly after clean-exit gating, placeholder exclusion, ledger de-dupe, and in-flight checks. Controller fallback sweep remains required.
The daemon route renders the full `prompts/meta-judge.md` template with the same scoped solver triplet paths the controller would bind; missing template or cross-issue/cross-round scope evidence fails closed through `phase9-router-fallback`, not a stub prompt.

Meta-judge emits `META_JUDGE_DONE:<decision>:<...>`,**controller 路由表(强制)**:

| Decision | Category | Controller 动作 |
|---|---|---|
| `consensus:<framing>:<summary>` | — | auto-applies(派 implement,见 "Consensus action";implement 可改 Tier I/II/CLAUDE.md/SPEC/核心抽象) |
| `converge:round-N:<question>` | — | clean rS judge canonical payload is `round-S`; legacy `round-(S+1)` also派 r(S+1) 三 solver unless router-owned stalled predicate first派 round-S reflector; non-adjacent payload mismatch falls back; `consensus-rnd-cli phase9-router` may direct-dispatch this route |
| legacy `escalate:stalled:<...>` | compatibility only | read-only replay path: predicate/source gates 成立才派 reflector codex(走完整 stalled reflector template + 9 个 solver log path evidence);no-framing evidence 优先 drop,`re-design` 仅用于 concrete new framing/directive artifact;**禁止**直接 label 人 |
| `escalate:<其他 category>` | legacy / judge 异常 | 重派 judge 或派 reflector,要求归一到 `consensus` / `converge`;**禁止**直接 label 人 |

结构性教训:曾出现多个 `escalate:stalled` 被直接 label 人,**没派 reflector**。现在 fresh judge 不再授权 stalled 输出;router predicate 从 clean converge 历史派生 stalled,legacy stalled marker 只读兼容且仍必须 reflector 优先。

**stalled 判据铁律**:`stalled` 只能由 router 在 `CONVERGENCE_ROUND >= 3` 且 solver verdict 文本连续无变化时从 clean `converge` 派生。round 1 / round 2 不可能 stalled;此时 solver 分歧应判 `converge` 并继续派下一轮,不能接受 meta-judge 在 r1/r2 输出的 `escalate:stalled` 作为事实。若 r1/r2 judge 输出 `escalate:stalled`,controller 必须按 legacy judge 异常处理:重派 judge(同输入,提示 fresh stalled 禁止),而不是派 reflector 或 label 人。

**反面(❌ 严禁)**:
- ❌ r1 三 solver 分歧,meta-judge 输出 `escalate:stalled`,controller 直接派 reflector。
- ❌ r2 verdict 变化但未 unanimous,controller 以"看起来卡了"自判 stalled。

reflector spawn 模板见 "Meta-layer escalation" 节。reflector 输出 `META_RESOLVED:<kind>:<reason>` 后 controller 再按 retry-fix / re-design / re-cluster / drop / escalate-human 路由。**只有** reflector 显式输出 `META_RESOLVED:escalate-human:<reason>` 时,controller 才允许 label `crnd:human:maintainer-decision` 并写 reason banner;这只用于"共识机制本身无法收敛",非"触及 Tier/哲学/签名"。

### Maintainer-directive artifact precedence

When reviewer evidence conflicts with maintainer prior session directive, a current-session `.refactor-loop/runs/maintainer-directives/<date>-<topic>.md` file is raw capture only. Durable route authority must be a checked-in mirror anchor in `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#maintainer-directive-*` or self-contained GitHub maintainer evidence. A mirrored maintainer-directive authorization has precedence over reviewer uncertainty about authorization.

If architect or quality rejects because the PR "needs Consensus-rnd Phase design-consensus artifact", do not apply `crnd:human:maintainer-decision`. Open a real Consensus-rnd Phase design-consensus path. If maintainer already authorized that topic in-session, cite the checked-in maintainer-directive mirror anchor or self-contained GitHub maintainer evidence and reframe Consensus-rnd Phase design-consensus with that evidence. Local directive captures awaiting mirror are not route authority. This is the Consensus-rnd Phase design-consensus-artifact replacement path; the label is not an interchange format for architect/quality reject.

Controller label application must use internal `ControllerActions.apply_human_label_or_skip(pr_number, source_marker, reason)` with the full `META_RESOLVED:escalate-human:<reason>` marker as `source_marker`. `META_JUDGE_DONE:*` and `FIX_BLOCKED:*` must route through reflector/meta-layer and must not call the primitive. The helper is a strict active-controller plus marker-gated label primitive; it does not scan local maintainer-directive captures and does not treat raw local files as durable authorization.

### Historical anti-pattern:`crnd:human:maintainer-decision` 误用 (2026-05-26)

PR #47/#48/#50/#52 因 architect codex 严格读 CLAUDE.md reject,reflector 选 option C 误以 label 绕路。实际 maintainer 已多次 session 内 verbal 授权。Fix:开真 Consensus-rnd Phase design-consensus(issue #54),encode maintainer-directive artifact 作 Consensus-rnd Phase design-consensus-等价。从此 label 严语义 + helper 守护。

### Reflector 完成 → 立即回到共识阶段(强制)
<!--
# Refactor (iter3/skill-human-label-taxonomy):
#   Old: 四个 Human label(含两个 🆘),no-gap/escalation 判定散落
#   New principle: 恰好两个 active Human label;causes 移到 reason surface(#15 structural 共识)
-->

**关键 bug**:之前 `escalate:stalled` 触发后挂 `crnd:lifecycle:stuck` + `crnd:human:maintainer-decision` label,**reflector 完成后没清掉**,导致 issue 视觉上仍卡在"等人"状态;controller sweep 时也会看到 stuck label 误以为不需处理。

**修复**:reflector 完成(任何 `META_RESOLVED:<kind>` 除 `escalate-human` 外)后,controller **必须立即**执行 label transition:

```bash
gh issue edit <N> \
  --remove-label "crnd:lifecycle:stuck" \
  --remove-label "crnd:human:maintainer-decision" \
  --add-label "crnd:phase:design-solving" \
  --add-label "crnd:human:auto"
```

然后按 `META_RESOLVED:<kind>` 路由立刻做下一步(派 fresh 3 solver 轮 / 关 issue / re-cluster);**不允许**停在"reflector done but stuck label still on"暧昧态。整个系统核心是多角色多角度共识——reflector 是中介调和角色,完成后必须把控制权交回 solver 共识循环。

唯一例外:`META_RESOLVED:escalate-human` → 保留 / 加 `crnd:human:maintainer-decision` label 并写明 reason/banner,这才是真正 human 介入态;它必须说明为什么 3 solver + meta-judge + reflector 的共识机制无法继续收敛。

### Daemon → controller event channel + 自适应 wakeup(强制, 关于 daemon detect → controller 25 min gap 问题)

**问题**:`consensus-rnd-cli comment-monitor` 30s 周期 detect maintainer 评论 ✓ + eyes react ✓ + daemon banner ✓,但若没有 mounted Monitor bridge,controller wakeup 默认可能只靠 1500s(25 min) ScheduleWakeup,maintainer 评论到 controller 派 fresh round 最长 25 min gap — maintainer 看 daemon banner 后等 controller。
**修法**:daemon writes durable event file → persistent Monitor wakes controller immediately → existing per-wakeup offset sweep processes events idempotently. ScheduleWakeup remains a fallback lane, not the immediate daemon-event lane. <!-- Refactor (iter3/skill-monitor-wake-source): Old pattern: 2-lane wake source(harness task-notification + ScheduleWakeup). New principle: 3-lane wake source adds daemon-event Monitor lane(daemon writes event file -> mounted persistent Monitor bridge -> controller wakes immediately; daemon alone is not a wake source). -->

Mount a persistent Monitor or equivalent harness bridge that forwards every non-empty line from both durable daemon event files, filtering only `tail -F` file headers:
```bash
tail -n 0 -F .refactor-loop/.controller-pending-events.log .refactor-loop/.concurrency-alert.log 2>/dev/null \
  | grep --line-buffered -v '^==> ' \
  | grep --line-buffered .
```

新 maintainer 评论 detect 后,**额外 append** 一行到 `.refactor-loop/.controller-pending-events.log`:
```
<ISO8601> new-team-comment <issue_number> <author> <comment_id>
```
(daemon 仍正常 react eyes + post daemon banner + write comment-monitor.log。新增的 pending-events 文件只用于 controller 自适应 wakeup。)

#### Controller 侧 — per-wakeup step 1.6:check pending events

每次 wakeup 在 sync(step 0)+ GitHub state derive(step 1)之后:
```bash
PENDING=".refactor-loop/.controller-pending-events.log"
LAST_PROCESSED=".refactor-loop/.controller-last-processed-event-offset"
prev_offset=$(cat "$LAST_PROCESSED" 2>/dev/null || echo 0)
cur_offset=$(wc -l < "$PENDING" 2>/dev/null || echo 0)
new_events=$(( cur_offset - prev_offset ))
if (( new_events > 0 )); then
  # 有 daemon detect 但未 controller-process 的 events
  sed -n "$((prev_offset+1)),$((cur_offset))p" "$PENDING" | while read -r line; do
    if [[ "$line" == DEV_SYNC_PENDING:release-rollup-needed:* ]]; then
      event_json="${line#DEV_SYNC_PENDING:release-rollup-needed:}"
      # Re-check open head/base PR; suppress if present, else open through helper.
      process_release_rollup_needed "$event_json"
      continue
    fi
    # 解析 issue / author / comment_id,触发 maintainer-reply-resets-the-round
    process_maintainer_reply "$line"
  done
  echo "$cur_offset" > "$LAST_PROCESSED"
  # ScheduleWakeup fallback can be shorter after daemon events, but the
  # immediate lane is the persistent Monitor bridge above.
  NEXT_WAKEUP_SECONDS=600
else
  NEXT_WAKEUP_SECONDS=1500  # 默认
fi
ScheduleWakeup(delaySeconds=$NEXT_WAKEUP_SECONDS, ...)
```

#### 自适应 wakeup 策略

| 触发 | 下次 wakeup 周期 |
|---|---|
| daemon-event Monitor bridge active | immediate wakeup on non-empty daemon event file append |
| pending events file 有新 entry | **600s** ScheduleWakeup fallback |
| in-flight codex(busy 状态) | 1500s(默认,等 task-notification) |
| 完全 idle 无 pending | 1800s(30 min idle heartbeat) |

#### 防回(❌ 禁止)

- ❌ daemon 写 events log 但 controller 不读 → maintainer 评论 → 25 min gap
- ❌ controller 处理完 events 但没有维护 persistent Monitor bridge 或 fallback wakeup → 下次再来评论 → 又 25 min gap
- ❌ controller 不更新 LAST_PROCESSED offset → 每 wakeup 重复处理同 events

### Stuck label 4h 超时自动新一轮 meta-reflect(强制)

每次 controller wakeup 第一动作之后(per-wakeup sweep step 1 完成后),对每个带 `crnd:lifecycle:stuck` OR `crnd:human:maintainer-decision` label 的 issue:

```bash
last_human_at=$(gh issue view <N> --json comments --jq '[.comments[] | select(.body | contains("⟦AI:AUTO-LOOP⟧") | not) | .createdAt][-1] // .createdAt' | tr -d '"')
now_epoch=$(date -u +%s)
last_epoch=$(date -j -u -f "%Y-%m-%dT%H:%M:%SZ" "$last_human_at" +%s 2>/dev/null \
  || date -u -d "$last_human_at" +%s)
delta_h=$(( (now_epoch - last_epoch) / 3600 ))

# 防重复:有 in-flight reflector(meta-reflect-issue<N>*.log mtime < 30min)→ 跳过
if (( delta_h >= 4 )) && [ -z "$(find .refactor-loop/logs/meta-reflect-issue<N>*.log -mmin -30 2>/dev/null)" ]; then
  # 派 fresh reflector,suffix -rN+1 防 overwrite 历史 reflector log
  spawn-reflector <N>
fi
```

意图:防 escalated issue 在"等 maintainer"无限堆积。4h 后**自动**派 fresh reflector,让 AI 反思能否重新框架到共识路径(narrow scope / drop / re-cluster),不积攒。

**反面禁止**:
- ❌ 见 stuck label 就跳过,不计算 delta
- ❌ 用 `author=maintainer` 判真人评论时间(deprecated,见 sentinel 节)
- ❌ 4h 内重复派 reflector 浪费 codex
- ❌ reflector 完成但忘清 stuck label → 下次 sweep 仍误判为 stuck

### 任何 concrete-plan 都必须走 multi-solver consensus

**铁律**:任何"具体怎么改代码"级别的 plan(file:line / 新 type 列表 / 删除清单 / migration 步骤)只能由 **3 solver + meta-judge consensus** 产出,**不能**由单 codex(包括 writer-codex / investigator codex / analyst codex)直接给出。

具体禁止:
- ❌ writer-codex 把 maintainer 文字指令 "translate" 成 concrete impl 计划(即使指令很明确)→ 必须走 r(N+1) solver round
- ❌ analyst codex 在 design issue 评论里给具体方案(它只能澄清/反推/列选项,不能落地)
- ❌ controller 自己 inline 写 impl plan(controller 只能写 status / 链接 / label,不写计划)

允许:
- ✅ writer-codex 翻译已经达成共识的 solver/judge 输出 → 中文 GitHub post(consensus 已在前)
- ✅ investigator codex 收集证据(grep / dep chain / git log)→ 数据回答事实问题(不给 plan)
- ✅ writer-codex 起草 PR body / consensus 公告(基于已 consensus 的 plan)

当 maintainer 给出方向性指令:
1. controller records the directive in the Consensus-rnd Phase design-consensus run artifact and GitHub issue thread.
2. 立刻派一轮 fresh 3 solver(把指令 verbatim 作为 narrowing constraint)
3. solver 们各自把指令具体化成 impl 计划(可能 minimal 给一套、structural 给另一套、delete 给第三套)
4. meta-judge 仲裁 → Step 3 truth-table consensus → 才能进 implement
5. 不允许跳过 3 solver round 直接 implement(哪怕 maintainer 觉得方向很明显)

理由:maintainer 直觉常常对,但 concrete 落地的细节(新 actor 边界 / schema 字段 / 命名 / 迁移路径)需要 3 个独立角度验证,避免单 codex 把 "明显方向" 误读成 "明显方案"。consensus 这步就是 catch 误读用的。

### Maintainer-reply-resets-the-round (mandatory)

Policy:任何 maintainer 新回复都让本轮推理失效,必须重新派 3 solver 并重新达成共识。

When the auto-discover Monitor fires `design-issue-event:<N>` and the new comment is from a verified team member (per Consensus-rnd Phase design-intake security gate) AND is substantive (>30 chars / contains technical content / not a controller self-reply):

1. **TaskStop any in-flight Consensus-rnd Phase design-consensus codex for that issue** (solvers OR meta-judge) — old reasoning is stale once new constraint lands.
2. **Treat the new comment as fresh constraint material** — prepend its verbatim text to a NEW round's solver prompt header under "Maintainer comment (must incorporate)".
3. **Dispatch FRESH 3 solver codex** (not "continue convergence"; truly fresh, with all prior rounds as context but no inherited stance).
4. **No round counter penalty** — maintainer input is the loop's continuation signal, not a stop signal. The round counter increments but does NOT trip the escalation cap.
5. **Only all implementation-bearing proposals agree + meta-judge consensus** moves the cluster to implement, with the single Path A greenfield delete-abstain compatible-neutral exception defined in the design-consensus contract. Maintainer can override at any time by adding `crnd:triage:resume-requested` label with their explicit framing in a comment.

This means: even if a previous round escalated with `crnd:lifecycle:stuck`, a new maintainer comment re-opens Consensus-rnd Phase design-consensus. The `crnd:lifecycle:stuck` label is removed automatically on reset; the canonical design-solving phase is re-applied.

### Consensus action (all implementation-bearing proposals agree + meta-judge consensus)

1. Read the winning solver's "Concrete plan" section from the meta-judge output.
2. Materialize `prompts/implement-<cluster-id>.md` prepending:
   ```markdown
   ## Design decision (from Consensus-rnd Phase design-consensus consensus, issue #${ISSUE_NUMBER})
   <winning solver's framing verbatim>
   <winning solver's concrete plan verbatim>
   ```
3. Add `crnd:triage:resume-requested` label to the issue (mirrors maintainer-decision flow).
4. Move the design issue through GitHub labels/comments into implementing state.
5. Dispatch implement codex per Consensus-rnd Phase implementation (worktree + 5400s no-output stall window).
6. **Post 共识卡片**(强制)— 不再用普通 status banner,改用 distinct **consensus card** 格式:

```markdown
## ✅ 共识卡片 — Consensus-rnd Phase design-consensus r${ROUND} consensus reached

| 维度 | 值 |
|---|---|
| Issue | #${ISSUE_NUMBER} ${TITLE} |
| Cluster | ${CLUSTER_ID} |
| Round | r${ROUND}(共识达成,Step 3 truth-table consensus) |
| 选定 framing | **${FRAMING}**(minimal / structural / delete 中的一个) |
| Solver 投票 | minimal: <verdict>:<summary> · structural: <verdict>:<summary> · delete: <verdict>:<summary> |
| Meta-judge 仲裁 | ${JUDGE_VERBATIM_REASON} |
| Concrete plan 摘要 | <3-5 bullet,来自 winning solver "Concrete plan" 头几条> |
| 下一步自动会做 | 1. 创 worktree + branch  2. 派 implement codex(5400s no-output stall window)  3. implement done 后 open PR + Consensus-rnd Phase review-gate reviewer  4. PR merge 后 close 本 issue |
| **是否需要人介入** | **❌ 否**(自动推进;maintainer 仍可在本 issue 评论 override) |

📦 implement worktree:\`$REPO_ROOT/.worktrees/iter${ITER}-${CLUSTER_ID}\`
📦 implement branch:\`refactor/iter${ITER}-${CLUSTER_ID}-${SLUG}\`

🤖 controller consensus card

⟦AI:AUTO-LOOP⟧
```

**约束**:
- 共识卡片第一行**必须** `## ✅ 共识卡片 — Consensus-rnd Phase design-consensus r${ROUND} consensus reached`(✅ 而非 📊,与普通 status banner 区分)
- 末尾 `🤖 controller consensus card` 标识 + sentinel
- 不在普通 status banner / 进度评论用 ✅ 开头(只共识达成时用)
- 共识卡片是 **一次性 event** post,implement 派出同 turn 内发,不重复

### Consensus scope (no hardcoded human escalation)

These are first-class consensus scope, not escalation triggers. Meta-judge MUST require solvers to include exact file/clause changes when any item appears; once Step 3 truth-table consensus is reached, implement proceeds without human ratification:

1. **Top-level CLAUDE.md clause change** — solver proposes editing CLAUDE.md "## 顶级架构约束" / "## 架构哲学" / Phase rules
2. **Tier I/II change** — solver proposes changing supervisor, SPEC, conformance, trusted base lock, GPG policy text, or swap/reinstall policy text
3. **New core abstraction** — solver proposes new actor type, new envelope kind, new pipeline phase, new Layer
4. **`$REPO_ROOT 的架构/词汇文档(若有)` change** — repo architecture vocabulary change
5. **Rule exception that escapes scope** — proposed exception is broader than "this one transient sink"; the exception would apply to multiple code paths
6. **Cross-cluster coupling** — solver's plan requires touching another in-flight cluster's PR
7. **Performance constraint unverifiable** — solver claims latency/memory bound but only prod can verify
8. **Issue body's `human_brief.why_needs_design`** contains: `rule-boundary` / `architecture-change` / `philosophy` / `CLAUDE.md` / `canon-vocabulary`

If the above makes the current framing underspecified, route `converge` with the missing exact text or evidence question. If solvers repeat unchanged text for ≥3 rounds, the router-owned stalled predicate may route that `converge` to reflector. Do not create fresh `escalate:stalled`, `escalate:gpg-ratification`, or `escalate:philosophy`.

### GitHub traceability (mandatory per SKILL.md "GitHub traceability" — same standard as Consensus-rnd Phase review-gate)

Every Consensus-rnd Phase design-consensus action posts a 中文 comment to the issue. **The issue must be a complete audit trail** — solver outputs follow the current language policy; the controller posts each one as a SEPARATE issue comment so reviewers can inspect the 3 perspectives side-by-side. Comments are traceability, not a human approval gate.

| Consensus-rnd Phase design-consensus event | Issue comment content |
|---|---|
| Round N solvers dispatched | 中文: "Consensus-rnd Phase design-consensus round N — minimal/structural/delete codex in flight. all implementation-bearing proposals must agree; only built-in Path A greenfield delete-abstain can be compatible-neutral; otherwise iterate." |
| Maintainer reply detected mid-Phase-9 | 中文: "Halted in-flight round; resetting with maintainer comment as new constraint. New round dispatched. Old round outputs preserved for solver context." |
| **Each individual solver completes** | Worker self-posts its full artifact/comment for traceability. Controller records clean completion, marker verdict, artifact path, and next-step fields only; if self-post fails, raw output may be referenced as diagnostic fallback. |
| **Meta-judge completes** | Worker self-posts its full artifact/comment for traceability. Controller records clean completion, marker verdict, artifact path, and next-step fields only; if self-post fails, raw output may be referenced as diagnostic fallback. |
| Meta-judge → consensus | Same as above + then a follow-up controller comment: "`crnd:triage:resume-requested` label added; implement codex dispatched" |
| Meta-judge → converge | Same as above + the round-(N+1) "solvers dispatched" comment that includes the convergence question for transparency |
| Router-derived stalled converge | Same as above + `## 🤖 Controller next-step` comment saying reflector is being dispatched for a no-progress stall |
| Legacy escalation category emitted | Post meta-judge output + summary "legacy escalation category normalized back into consensus loop"; re-dispatch judge or reflector; do not label human directly |

**Forbidden**: controller relay/transcription of solver or judge raw prose as normal routing evidence. Full raw artifacts remain the audit record through worker self-posts or artifact paths; controller comments use structured completion fields, counts, verdicts, and paths.

Required labels (additions to Consensus-rnd Phase review-gate set):
- `phase9-solving`: 3 solver codexes in flight
- `phase9-judging`: meta-judge in flight
- `phase9-converging`: convergence round in progress
- (re-used) `crnd:triage:resume-requested` on consensus dispatch
- (re-used) `crnd:lifecycle:stuck` on escalation

### Consensus-rnd Phase design-consensus tracking

Consensus-rnd Phase design-consensus tracking lives in GitHub issue labels/comments plus `.refactor-loop/runs/phase9-issue<N>-r<M>-*.md`
solver/judge artifacts. Do not mirror it into a root local state queue.

### Anti-spiral safeguards (no hard round cap — different safeguards instead)

Policy:the loop continues until Step 3 truth-table consensus, true stall reaches reflector, maintainer provides new evidence, or maintainer closes the issue.

- **No `MAX_CONVERGENCE_ROUNDS` cap**. The loop iterates until Step 3 truth-table consensus OR true stall reaches reflector OR maintainer adds new constraints OR maintainer closes issue.
- **Stall detection**: if 3 consecutive rounds with NO maintainer input AND NO change in any solver's verdict text → **trigger meta-layer reflector** (not human escalate;)。Reflector 同样回 4 framing question + 输出 `META_RESOLVED:<kind>` marker;路由:
  - `retry-fix` → 派 r+1 solver,加 "reflector 提示: 你们三 round 没收敛,本轮必须 propose 新 framing 不重复之前"
  - `re-design` → reset Consensus-rnd Phase design-consensus round counter,prompt 重写带 reflector 总结的新 framing 角度
  - `re-cluster` → close design issue + audit re-split(下 iter 拆 cluster)
  - `drop` → close design issue with `wontfix`
  - `escalate-human` → `apply_human_label_or_skip` with the full `META_RESOLVED:escalate-human:<reason>` marker for `crnd:human:maintainer-decision` + reason banner + PushNotification(仅 reflector 也无解;checked-in maintainer-directive mirror anchor or self-contained GitHub maintainer evidence 才能替代 human label route)
- **Maintainer reply RESETS stall counter** — fresh round dispatched with their comment as constraint; stall counter goes back to 0.
- Solver may not repeat a framing that prior rounds showed to be underspecified without adding new exact text/evidence; doing so counts toward stall detection.
- Cumulative solver runtime across all rounds capped at 12h per issue (raised from 6h to account for maintainer-reset iterations); over → escalate as `stalled:budget-exhausted`.
- Architecture/philosophy/Tier triggers never escalate immediately. They require stricter concrete text in solver plans, then either consensus or stall.

### When to trigger Consensus-rnd Phase design-consensus (operator policy)

- **Default ON for design decisions that need a concrete plan**. Operator labels may prioritize work, but Tier/CLAUDE/philosophy scope is not a reason to bypass Consensus-rnd Phase design-consensus.
- Rationale: Consensus-rnd Phase design-consensus is the authorization mechanism. Hard architectural calls require better solver evidence and exact rule text, not a maintainer dialog gate.
- The cluster spec's `requires_design: true` + `human_brief.why_needs_design` content informs solver prompts; philosophy keywords must be incorporated into the consensus question instead of silently no-oping Consensus-rnd Phase design-consensus.

---

## Loop control

<a id="concurrency-floor-details"></a>
## Concurrency floor details

### This is an INFINITE refactor loop — never idle on "iter done"

Policy. An iteration completing is NEVER a stop signal. The loop's only legitimate stops are:
1. Audit returns 0 candidates (codebase has no flagged violations under current rules) — extremely rare.
2. Every cluster in the current batch failed verify twice — escalate operator.
3. Operator explicitly tells the loop to stop.

**Iteration boundary is automatic**: as soon as iter N's last cluster PR merges into `integration_branch` (NOT after rollup PR human review — rollup runs independently in parallel as a human gate), controller IMMEDIATELY dispatches `Consensus-rnd Phase work-intake audit` for iter N+1. The rollup PR (auto-refact-dev → review_base_branch) is a parallel human-review track, not a serial gate.

Concretely, this means:
- After PR #<pr> (a cluster PR in iterN) merged, controller does NOT wait for PR #<pr> (rollup) review — it immediately dispatches the iterN audit codex.
- iterN implement / verify / Consensus-rnd Phase review-gate review runs in parallel with iterN rollup PR being reviewed.
- If iterN rollup PR gets rejected by human, iterN work stays on auto-refact-dev (which now contains iterN + iterN deltas); we re-do iterN rework on top and ship combined.

### Existing-issue priority route table(per 2026-05-28 maintainer-directive)

Authorization: `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#maintainer-directive-existing-issue-priority-over-audit`. Before ordinary audit fallback, controller MUST first dispatch the next-step actor for every open catalog-managed issue/PR (`crnd:lifecycle:managed`, dual-read through catalog aliases during migration) that lacks an in-flight codex covering its current canonical phase label. If any such open item carries `crnd:milestone:current`, milestone-labeled issue/PRs dispatch before non-milestone existing-issue work:

Managed work identity is projected through `codex_refactor_loop.work_items.ManagedWorkProjection`: an open managed PR body with exactly one durable `Closes #N` link represents that parent issue. The represented parent issue is visible as `crnd:phase:pr-open` and has expected workers 0; worker expectation and review/fix routing belong to the child PR. Missing, duplicate, or ambiguous `Closes #N` links are diagnostics, not guessed lifecycle authority.

- `crnd:phase:design-solving` with 0 codex → phase9-router DesignConsensusIssueIntake dispatches Consensus-rnd Phase design-consensus r1 solver triplet for that issue; wakeup-plan only projects status evidence until the router emits HARNESS_SPAWN_INTENT
- `crnd:phase:reviewing` with 0 codex → dispatch the missing reviewer(s) for the latest head SHA
- `crnd:phase:fixing` with 0 codex → dispatch fix codex for next round
- `crnd:phase:implementing` with 0 codex + IMPLEMENT_DONE absent → re-dispatch implementer (or block reason banner)
- PR review work is represented by the child PR; parent issue `crnd:phase:pr-open` is non-action, expected workers 0
- `crnd:phase:consensus-reached` with 0 codex → dispatch implement codex

Audit fallback (`audit-iter-N+1`) is valid **only after** every open catalog-managed issue/PR already has an in-flight codex matching its canonical phase label or has documented blocked-on-maintainer reason. Spawning fresh audit while existing design-solving / fixing work sits 0-codex is a no-gap violation, not a floor refill.

### Stale-issue revival(3h) details(per 2026-05-28 maintainer-directive)

Authorization: `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#maintainer-directive-stale-issue-3h-revival`. Open catalog-managed issue/PR whose latest `updatedAt` (from `gh issue view --json updatedAt` / `gh pr view --json updatedAt`) is older than **3 hours UTC** MUST be re-dispatched to its next-step actor on the next wakeup, even if (a) the phase label looks current, or (b) an earlier round already produced markers, or (c) prior comments suggest "awaiting" something. The 3h cutoff is wall-clock; do not weaken it because in-flight tasks elsewhere are busy.

Stale revival selects the actor by current phase label using the Existing-issue priority routes above; for unlabeled `crnd:lifecycle:managed` items the default revival is Consensus-rnd Phase design-consensus r1 solver triplet. Each re-dispatch posts a banner noting `stale_hours=N` from `updatedAt`.

Stale `updatedAt` is routing metadata only: it may trigger re-dispatch visibility, but it does not authorize GitHub comments, label edits, PR merges, issue closes, takeover, or any other issue/PR target write. Those writes remain gated solely by #191 `ActiveControllerLease` ownership through `require_active_controller(...)`.

### Concurrency floor = `$CODEX_FLOOR` 本仓库 codex(host 可配,默认 5,硬下限 2)(强制)

<!-- Refactor (iter3/skill-concurrency-floor-enforcement):
  Old pattern: concurrency_monitor 有误导性 low-threshold 路径,CODEX_FLOOR 强制职责不清
  New principle: monitor 保持 no-gap-only;删 stale low-threshold 路径;CODEX_FLOOR 补给仅 controller wakeup step 1.5;SKILL 澄清职责(#14 delete 共识)
-->

**问题**:之前 "iteration boundary" 是 merge-driven:等 iter N 最后 cluster PR merge 才派 iter N+1 audit。但 iter N 走到 fix r2/r3 阶段时常常只有 1 codex 在跑(fix codex 单点),其他 phase 都在等。codex 总并发数掉到 1-2,远低于本地资源能撑的并行度。

**floor 取值**:`CODEX_FLOOR` 由 host.env 注入(未设则默认 **5**)。**无论 host 设多少,硬下限 = 2** —— controller 必须**确保始终有 ≥2 个本仓库 codex 并行**(防单线程死等);`CODEX_FLOOR < 2` 一律按 2 处理。小型 host(纯文档 / skills 仓,可派的独立工作少)宜设 `CODEX_FLOOR=2`,大型代码仓可设 5+。**floor 计数只算本仓库 codex**(按 `$REPO_ROOT` 绝对路径 scope,见上「并发 floor … 过计」节;❌ 不要用相对子串,同机多 loop 会过计致本仓库永远补不上)。

<!-- Refactor (issue-277): Old: Concurrency floor details used absolute audit fallback wording, allowing duplicate same-iteration audit as fake dispatch authority. New: keep no general exemption and `AUDIT_DONE:none:0` still not exempt, but represent occupied single audit slot as WAIT with blocked_deficit when no other legal work exists. -->

**规则**:**活跃(本仓库)codex < `$CODEX_FLOOR` 时主动派额外真实工作填满 floor**,不等当前 phase 完成。floor 是保底,不是 burst 目标;单次派发按真实工作量伸缩,默认补到 `$CODEX_FLOOR`,不要为了"并行更猛"一次性齐发十几个。controller 每次 wakeup 的 step 1.5 仍必须在任何 `ScheduleWakeup` 之前执行;同时 `consensus-rnd-cli concurrency` 现在会消费 [dispatch queue protocol](#dispatch-queue-protocol) 中的 queued work 自动补 floor,避免 controller 卡住时只 alert 不派。若没有 open actionable work, audit 仍是可用兜底;`AUDIT_DONE:none:0` still does not exempt a positive deficit; ordinary audit fallback has one same-iteration active slot, so an occupied slot with no other legal work is `WAIT:single-active-audit` plus `blocked_deficit`, not duplicate audit。

| 活跃本仓库 codex 数 | 动作 |
|---|---|
| `>= $CODEX_FLOOR` | 不抢资源,保持现状 |
| `< $CODEX_FLOOR`(floor 至少为 2) | 立即派 `$CODEX_FLOOR - 当前数` 个新 codex 填满 floor;优先级如下 |

**填 floor 优先级**(从高到低):

1. **Existing dispatch queue** — `.refactor-loop/dispatch-queue/{p0,p1,p2}/*.dispatch.json` remains first; queue schema is unchanged.
2. **Clean actionable marker / maintainer comment / CI red / no-gap** — only log-tail markers after `EXIT=0` count; in-flight codexes are not actionable.
3. **Consensus-rnd Phase design-intake / Consensus-rnd Phase design-consensus actionable routes** — manual-issue intake and consensus routes that already have durable issue/comment/marker evidence.
4. **Audit fallback** — envsubst next iteration `prompts/audit.md`; `AUDIT_DONE:none:0` still does not exempt a positive deficit, and there is no duplicate same-iteration audit.
5. **Visible hard gate / blocked boundary** — when no real open work exists and no same-iteration audit is active, emit `RECOMMEND:audit` and `HARD_GATE:dispatch_required=N`; when the same-iteration audit slot is occupied, emit `WAIT:single-active-audit`, `dispatch_required=0`, and `blocked_deficit=N`; do not stop with a low-floor exemption.

**反面禁止**:
- ❌ 看到 1 codex 跑就 ScheduleWakeup 等(消极等待)→ 必须先填到 `$CODEX_FLOOR`(至少 2)才允许 ScheduleWakeup
- ❌ "iter N 还没完"作为不派 pre-fixed-point audit 的理由 → audit 与 cluster impl 完全独立,无依赖
- ❌ 重复派同 iter audit(已有 active `audit-iter-N` 还派)→ expose `WAIT:single-active-audit` + `blocked_deficit`,不要 duplicate same-iteration audit
- ❌ latest controller-validated audit 已是 `AUDIT_DONE:none:0` 就不补 floor → 仍不豁免;无 active audit 且无 open actionable work 时继续 hard-gate dispatch audit
- ❌ 一次性派 `>= 15` 个 codex 凑吞吐。大 burst 会压 API,更容易触发 transient stream-disconnect,并让 prompt 回显误判与追踪问题一起放大。

### Transient stream-disconnect 处理(强制)

codex 偶发 `ERROR: stream disconnected before completion` 且 exit 1,尤其同时派 `>=~15` 个 codex 压 API 时。这通常是 transient,不是 prompt 问题。

**铁律**:
- 读 log 只确认失败类型:若含 `ERROR: stream disconnected before completion` 且 `EXIT=1`,**重派同 prompt**(spawn 形态一致、同 worktree / prompt / stall 语义,新 log path 或清晰 retry 后缀)。
- 不要修改 prompt 来"修" stream-disconnect;prompt 未被完整执行,没有 evidence 证明语义有问题。
- 控制单次派发批量:floor 5 是合理起点,按真实工作量伸缩;不要为凑并发大批齐发。

**反面禁止**:
- ❌ stream-disconnect 后把任务标 blocked / reject。
- ❌ stream-disconnect 后派 reviewer / judge 读取半截输出。
- ❌ 为了补 backlog 一口气派 15+ codex,再靠人工清理一堆 transient 失败。

**判定脚本**(controller wakeup step 1.5):

<!-- Refactor (issue-275): Old pattern: SKILL.md fenced shell 探针含 raw positional $0/$1/$2,skill 带参加载被 clobber。 New principle: 删可执行探针改指 canonical CLI(wakeup-plan ci-red + concurrency --count-only),不在文档放可被位置参数 clobber 的 inline shell。 -->
```bash
test -n "${CONSENSUS_RND_HOST_ENV:-}" && source "$CONSENSUS_RND_HOST_ENV"                # 取 REPO_ROOT / CODEX_FLOOR
FLOOR=$(( ${CODEX_FLOOR:-5} < 2 ? 2 : ${CODEX_FLOOR:-5} ))   # 硬下限 2
# 只数本仓库 codex:使用 canonical CLI 计数;diagnostic 明细可用 --list-codex
ACTIVE=$(python3 <skill-root>/scripts/consensus-rnd-cli concurrency --count-only)
NEEDED=$(( FLOOR - ACTIVE ))
[ "$NEEDED" -le 0 ] && return  # floor 已满(本仓库 codex 已 >= FLOOR)

# 按优先级派 NEEDED 个 codex:
# queue -> actionable marker / maintainer comment / CI red / no-gap / Consensus-rnd Phase design-intake/9 route
# -> audit fallback, including after AUDIT_DONE:none:0 when no same-iteration audit is active
# -> HARD_GATE:dispatch_required=N or WAIT:single-active-audit blocked_deficit=N until deficit is gone or no legal dispatch remains
```

**反面禁止**:
- ❌ 看到 1 codex 跑就 ScheduleWakeup 等(消极等待)→ 应主动派真实 work 提升并发
- ❌ 多个 same-iteration audit 同时跑(`audit-iter-N` active 还派同 N)→ 资源浪费,重复 evidence;必须 expose `blocked_deficit`
- ❌ "iter N 还没完"作为不派 pre-fixed-point audit 的理由 → audit 与 cluster impl 完全独立,无依赖
- ❌ 重复派同 iter audit(已有 active `audit-iter-N` 还派)→ no duplicate same-iteration audit
- ❌ 在 latest controller-validated `AUDIT_DONE:none:0` 后把 floor deficit 当成已豁免 → no general exemption;无 active audit 且无 open actionable work 时继续 `HARD_GATE:dispatch_required=N`

结构性教训:曾出现 fix 期间并发只剩 1 个 codex,说明单靠 merge-driven iteration boundary 不足以维持无限循环吞吐。concurrency-driven trigger 是并行优化的必要规则:并发过低时应主动开启真实 work;maintainer directive `skills/codex-refactor-loop/authorizations/runtime-exceptions.md#maintainer-directive-floor-no-exemption` removes the former audit-none floor exemption.

### Sync to remote in time (强制)

Policy:controller must sync with remote promptly before deriving GitHub and branch state.

- After EVERY skill edit that affects controller behavior, `git commit && git push origin "$INTEGRATION_BRANCH"` IMMEDIATELY — do not batch multiple skill changes for a single push, do not defer to "end of turn".
- After EVERY cluster PR commit (fix codex round output): `git push origin <branch>` IMMEDIATELY — the reviewer / CI / maintainer all need to see latest state, not yesterday's local state.
- Consensus-rnd Phase integration-sync sync (`$INTEGRATION_BRANCH` ← `origin/$REVIEW_BASE_BRANCH`) runs FIRST on every controller wakeup; never assume "I just synced" — verify with `git fetch && git rev-list --count`.
- Consensus-rnd Phase ci-watch CI watch reads `consensus-rnd-cli pr-checks`
  (PR-head Checks API projection, always remote), never a local cached value.
- Consensus-rnd Phase design-intake/8/9 reviewer/judge outputs MUST be posted to GitHub as PR/issue comments within the same controller turn they complete; do not let them sit local-only across multiple turns.

If a push fails (network, conflict, branch protection): controller MUST surface the failure inline and either fix-and-retry or escalate within the same turn — never silently leave local changes uncommitted/unpushed.

### Stop conditions / stop action

- **Stop conditions**: audit returns 0 candidates twice in a row OR every cluster in current batch failed verify twice OR operator says stop.
- **Stop action**: omit ScheduleWakeup, TaskStop any monitor, send one-line PushNotification with summary.

### Wakeup cadence

- Immediate daemon lane: daemon-event Monitor bridge over `.controller-pending-events.log` and `.concurrency-alert.log`.
- Worker completion lane: codex task-notification (auto on codex exit).
- Fallback lane: 1200–1800s ScheduleWakeup (matches /loop dynamic mode guidance).

---

<a id="status-and-escalation-templates"></a>
## Status and escalation templates

## 状态横幅(status banner)— 强制

**问题**:design issue / PR 一旦进入 multi-codex loop,会堆积几十条 audit / solver / judge / reviewer / fix 评论。人类站维护者角度打开 issue 一眼看不出"现在到了哪步、是否需要我介入"。100 条 AI 评论自治不等于 transparent。

**规则**:**controller** 在每次 phase transition 时**必发** status banner 评论。Codex 不发 banner(它们各发各的角色 artifact 评论)。Banner 是 controller-owned 集中状态指示器。

### Banner 触发时刻(每个均强制 post)

| 触发 | banner 内容要点 |
|---|---|
| 共识达成(Consensus-rnd Phase design-consensus meta-judge `consensus`) | "✅ 共识达成,implement 派出" + chosen framing |
| implement 完成(任何 cluster) | "实施完成,即将开 PR" + LOC delta + 文件清单 |
| PR open | "PR open + reviewer 派出" + PR # + base branch |
| Consensus-rnd Phase review-gate r1 reviewer 完成 | "评审 r1: <N approve / N comment / N reject>" + next step |
| Consensus-rnd Phase review-gate fix 派出 | "fix r<N> 派出,目标修 reject" |
| Consensus-rnd Phase review-gate consensus 达成 | "Consensus-rnd Phase review-gate 共识达成,等 CI 绿后 merge" |
| CI 全绿 | "CI 全绿,合并中" |
| CI red | "CI 红,fix codex 派出" |
| merge 完成 | "🎉 已合并到 <branch>" |
| escalation | "🚨 需要人介入: <reason>" + label `crnd:lifecycle:stuck` |
| blocked-on(被其他 issue 拖) | "blocked-on #<issue>: 待其完成自动推进" |

### Banner 模板(controller 直接 gh issue/pr comment,不走 codex)

第一行**必须** `## 📊 当前状态 — <短 phase 名>(<介入与否>)`。然后表格 + 下一步 + 何时介入。

```markdown
## 📊 当前状态 — <phase>(<不需要人介入 | ✅ 需要人介入>)

| 维度 | 值 |
|---|---|
| 阶段 | **<phase 名>** |
| 共识 | ✅/❌ <link 到 meta-judge 评论> |
| 关联 issue | #N #M |
| 关联 PR | #K(若有) |
| codex 任务 | <task-id>(<已跑 min> / <stall window min>) |
| **是否需要人介入** | **❌ 否** / **✅ 是: <原因>** |

**下一步自动会做**:<具体动作>

**何时需要人介入**:
- <具体条件 1>
- <具体条件 2>

🤖 controller status banner
```

### 硬约束

- **第一行必须是 `## 📊 当前状态 — ...`**(comment-monitor 据此识别 controller-post 跳过自 react)。
- 每条 banner 末尾必须 `🤖 controller status banner`(双重防护)。
- **不写过程**(谁讨论了什么)。只写"当前 phase + 下一步 + 何时介入"。讨论详情在前面 codex 评论里,banner 是 *index*,不是 *recap*。
- 不要发废 banner(同 phase 连续两次没变化 → 不要重发)。
- escalation banner 只允许在 reflector / meta-layer 输出 `META_RESOLVED:escalate-human` 后使用;必须**显式**说"✅ 共识机制无法继续收敛"并列出卡住的具体问题(不是"看一下"这种 vague 描述)。

### Escalation banner — 必须含问题 ASCII 图 + 详细问题描述(强制)

普通 status banner 是 *index*(简短);**escalation banner 是共识机制停滞的审计依据**,必须**问题导向**:

1. **问题 ASCII 图**:当前架构里**问题模式**长什么样(数据流 / 调用链 / 状态归属违反点),不是 reflector 路径
2. **问题描述**:具体 `file:line` + 当前行为 + 违反的 CLAUDE/AGENTS 条款 + 影响范围
3. **卡住选项**:每个选项的 Plan / 影响 / Tradeoff(说明为什么 meta-layer 无法选)
4. **可能输入入口**:choose A/B/C / narrowing constraint / close wontfix(这是恢复共识的外部输入,不是授权 gate)
5. **历史轮次**降为表格内**一行**(`r1+reflector+r2 仍 escalate` 一句话),不画路径图——只保留审计所需最小信息

#### 模板(必须严格遵循)

```markdown
## 🆘 状态卡片 — 共识机制无法继续收敛

| 维度 | 值 |
|---|---|
| Issue | #<N> <title> |
| Cluster | <cluster-id> |
| 历史 | r1+reflector r1+r2+reflector r2 全 escalate(详情见上面评论) |
| **核心问题** | **<一句话,具体到 file/类/方法,说清楚现在系统在哪里做什么导致违反什么>** |
| **卡住问题** | **<一句话,说明 3 solver + meta-judge + reflector 无法收敛在哪里>** |

### 问题图(ASCII)

\`\`\`
当前架构(违反点):

  ┌──────────────────────┐         ┌─────────────────────┐
  │  <调用方 file:line>  │ ──────▶ │  <被调对象 file:line>│
  │  e.g. endpoint       │         │  e.g. ExternalLink   │
  │                      │         │      Manager         │
  └──────────────────────┘         └─────────────────────┘
                                            │
                                            ▼ ← problem: 这里持有 process-local
                                   ┌─────────────────────┐
                                   │ process-local collection│
                                   │ <state-violating-X> │
                                   └─────────────────────┘

  违反:<CLAUDE.md 哪条 + 一句话>
\`\`\`

(根据问题类型画对应图——状态归属:框 + 数据流箭头;生命周期:时间线 + actor 栏;调用链:source→sink 链;依赖反转:层 + 反向箭头标 ❌)

### 问题描述

**当前行为**(具体到代码):
- `<file:line>`:<这里在干什么,1-3 行>
- `<file:line>`:<另一个 evidence>

**违反规则**:
- CLAUDE.md「<引用条款>」
- 或 AGENTS.md「<引用条款>」

**影响范围**:
- <谁会被 affected,具体到 callers / data flow>
- <如不修是否阻塞 production / cause silent fail / 仅风格>

**为什么共识机制无法继续推进**:
- <如:涉及 public surface 删除策略 / cross-cluster 耦合 / $REPO_ROOT 的架构/词汇文档(若有) 改动 / 性能 vs 简洁取舍>
- <一句话说明 solver verdict 文本如何连续无变化,或 reflector 为什么判定无可收敛 framing>

### 决策选项

#### 选项 A — <选项名,动词起始>
- **Plan**:<具体 file:line 改动,1-3 行>
- **影响**:<改动范围 + 谁会被 break>
- **Tradeoff**:<这条路的代价>

#### 选项 B — <选项名>
- **Plan**:...
- **影响**:...
- **Tradeoff**:...

#### 选项 C — <选项名>(可选,2-4 个之间)
- ...

### Maintainer 行动入口

- **选定**:评论 `choose: A` / `choose: B` / `choose: C` 或给具体 narrowing constraint
- **重派**:加 `crnd:triage:resume-requested` label,controller 用你评论作 narrowing 派 fresh round
- **不做**:close issue + 加 `wontfix` label

🤖 controller status banner

⟦AI:AUTO-LOOP⟧
```

**约束**:
- 问题 ASCII 图**画当前架构的违反点**——数据流 / 状态归属 / 调用链 / 生命周期等;**不画**reflector / round 路径(那是过程,不是问题)
- 用 box-drawing(`─│┌┐└┘▶▼◀▲`)+ 空格对齐;**禁用 mermaid**(per this skill's GitHub banner rendering rules)
- 历史 round 信息**降级为表格一行**(`r1+reflector+r2 仍 escalate`),不占主视觉
- 决策选项 2-4 个,每个 Plan / 影响 / Tradeoff 三栏(file:line 级别)
- "为什么不是机械重构能解"段是**根因**而非 *recap*(maintainer 看一眼知道为什么 AI 不接手)
- 末尾标准 `🤖 controller status banner` + sentinel
- 背景段不要 *recap* 评论历史,是**为什么 AI 解不了**的根因分析
- maintainer 行动入口三选一(选项 / 重派 / 关闭)
- 末尾标准 `🤖 controller status banner` + sentinel

### 反面(❌ 禁止)

- ❌ 一堆 codex artifact 评论之后无 status banner → 人类不知道当前 phase
- ❌ banner 把过程 recap 一遍 → 噪音叠加噪音
- ❌ banner 用 `## 🤖 controller` 第一行(comment-monitor 已经把 `## 🤖` 当 codex post 跳过,但 banner 应该是 controller 自己,用 `## 📊` 区分)
- ❌ "需要人介入"用模糊措辞 → 人类还是不知道要不要看

<a id="meta-layer-escalation"></a>
## Meta-layer escalation

## Meta-layer escalation — 强制
<!--
# Refactor (iter3/skill-human-label-taxonomy):
#   Old: 四个 Human label(含两个 🆘),no-gap/escalation 判定散落
#   New principle: 恰好两个 active Human label;causes 移到 reason surface(#15 structural 共识)
-->

**问题**:Consensus-rnd Phase review-gate fix r6 仍 reject,或 CI same-check 6 次仍 fail,**第一反应不是喊 human**,而是**反思上一层是否本身错了**。喊 human 是最后的手段。

**层级**(由小到大):
1. **fix(r1..r6)**:针对 reviewer evidence 直接补丁
2. **Meta-layer reflect**:反思 design / cluster / audit 框定是否本身错位
3. **Consensus-rnd Phase design-consensus re-design**:重派 3 solver + meta-judge,prompt 带 "previous design caused 6 round non-converge"
4. **Cluster re-split**:audit 阶段 re-evaluate,把当前 cluster 拆 / 合 / 撤回
5. **Drop / wontfix**:确认任务本身价值不足,关 PR + close issue with wontfix
6. **Human escalation**:`crnd:human:maintainer-decision` + reason banner + PushNotification(只在 meta-layer 也无法解时)

### 触发 meta-layer 反思

- Consensus-rnd Phase review-gate `fix_round > 3` 仍 reject(所有 reviewer 同一组 / 同一 reviewer 反复 reject)
- CI same-check 失败 6 次(同 test 6 次 fix 仍红)
- Cumulative PR diff size > 原 PR 200%(scope-runaway 信号)
- Reviewer 同一类 evidence(test coverage / dead surface / self-doc)在 3 round 内反复出现 → meta-reflect "为什么 evidence 总是同类"
- **Consensus-rnd Phase design-consensus design issue stall**:3 consecutive round 无 maintainer input AND solver verdict text 无变化 → 也走 meta-layer

### 派出 reflector codex

```bash
# 内容(prompt 摘要)
你是 reflector codex,不写代码,只反思。Input:
- 当前 PR diff
- 所有 review round 的 reject evidence(verbatim)
- 当前 Consensus-rnd Phase design-consensus 共识 / audit cluster 框定
你的任务:回答 4 问 + 给 1 决议:
1. Reviewer 反复 reject 的根本原因是 design 错位 / cluster scope 错位 / audit framing 错位 / 还是仅"reviewer 在做完整审查正常 surfacing 小 gap"?
2. 当前 PR scope 是否爆炸(原 cluster 范围 vs 现 diff)?
3. 当前 design 共识(Consensus-rnd Phase design-consensus)是否本身有漏洞(reviewer 抓到 design 没考虑的角落)?
4. Audit cluster 框定是否过大 / 过小 / 错混?

决议(选一):
- `META_RESOLVED:retry-fix`: 是 reviewer 正常审查,继续 fix r4+ 仍可收敛(给 reviewer 一个 "approve if r4 仍 narrow valid" 的窗口)
- `META_RESOLVED:re-design`: design 错位,关 PR / 撤回当前 implement,re-Consensus-rnd Phase design-consensus with reflector prompt
- `META_RESOLVED:re-cluster`: cluster scope 错位,关 PR + audit 阶段 re-split(拆为 2-3 个小 cluster)
- `META_RESOLVED:drop`: 任务价值不足或代价 > 收益,关 PR + close issue wontfix
- `META_RESOLVED:escalate-human`: meta-layer 也无法解,真的需要 maintainer 决策(reason 必须说明 rework / deadlock / ci-stuck 等原因)

```bash
<skill-root>/scripts/consensus-rnd-cli spawn-codex \
  --cd $REPO_ROOT \
  --prompt .refactor-loop/prompts/meta-reflect-pr<N>.md \
  --log .refactor-loop/logs/meta-reflect-pr<N>.log \
  --stall 3600
```

Controller 读 marker 后路由:
- `retry-fix` → 派 fix r4 + 提高 max_fix_rounds 临时到 5(只本 PR)+ 同时 narrow reviewer 关注新 evidence only(不再 surface 旧 evidence)
- `re-design` → 关 PR / 撤回 commits / re-Consensus-rnd Phase design-consensus with constraint = reject evidence pattern
- `re-cluster` → 关 PR / audit re-split(产新 cluster 在 next iter)
- `drop` → close PR + close issue with `wontfix` label + 转 phase merged-no-op
- `escalate-human` → `apply_human_label_or_skip` with the full `META_RESOLVED:escalate-human:<reason>` marker for `crnd:human:maintainer-decision` + reason banner + PushNotification(只 meta-layer 也无路时;checked-in maintainer-directive mirror anchor or self-contained GitHub maintainer evidence 才能替代 human label route)

### 反面(❌ 禁止)

- ❌ fix r4 直接派出而不 reflect → 可能在错的层级死循环
- ❌ 3 轮卡死直接升 human → 没把 AI 自身的反思能力用足
- ❌ reflector 也写代码 → 它的职责是 question framing,不是 propose fix
- ❌ reflector 决议 `re-design` 但 controller 继续派 fix → 框架失效
- ❌ 临时 `max_fix_rounds = 5` 滥用 → 仅 reflector 明确 `retry-fix` 时允许,且不超过 5

<a id="ci-progress-and-reporting"></a>
## CI progress and reporting

CI sweep contract: every controller wakeup checks open catalog-managed PR checks, immediately classifies red checks, dispatches fix/test-add for real or codecov failures, reports pre-existing failures, and routes repeated same-check failures through the meta-layer before human escalation.

## Codex 进展实时上报 — 强制

`consensus-rnd-cli progress-reporter` is one of the six required daemons. It edits one progress comment per in-flight codex, includes elapsed time plus log tail, skips old finished logs, deletes the progress comment only when the codex exits cleanly, and uses only log-tail `^EXIT=0` for successful completion detection. Nonzero `EXIT=<n>` is a failed terminal state that remains visible instead of being silently cleaned up.

<a id="label-bootstrap-loops"></a>
## Label bootstrap loops

This section is intentionally not a bootstrap loop. The catalog in
`codex_refactor_loop.labels` is the single label fact source; run
`consensus-rnd-cli labels validate-catalog` for local validation and
`consensus-rnd-cli labels check-github --plan` for a read-only GitHub drift
plan. The plan preserves GitHub `external_defaults`, reports unknown
`crnd:*` labels fail-closed, and lists legacy alias migrations.

Controller apply, when used, must add canonical labels first, re-read live
labels, validate exactly one canonical phase and human label for managed
items, and only then remove aliases. Do not add bootstrap shell loops, copied
catalog tables, or alternate label grammars here.

<a id="codex-invocation-details"></a>
## Codex invocation details

## Codex 调用方式 — 强制

**问题**:codex 进程要让 maintainer 在 Claude Code UI 的 background tasks / shells panel 一眼可见。

**规则**:**controller 主链路所有 codex spawn 优先用 Bash tool `run_in_background: true`**。Claude Code harness 跟踪该 background task,显示在 UI shells/tasks 面板 → maintainer 看到 "8 shells" 等计数。`nohup ... & disown` 会 detach 出 harness,maintainer 看不到即时 shells/task-notification;若意外发生,不要杀掉重派,必须确认 log 可 sweep 且本 turn 结束前有已注册 ScheduleWakeup 或其它在飞 task-notification。

### 推荐调用 pattern

```python
Bash(
  command="<skill-root>/scripts/consensus-rnd-cli spawn-codex "
          "--cd <dir> --prompt <prompt-file> --log <log-file> --stall 5400",
  run_in_background=True,    # 必须 true → 进 Claude Code shells panel
  description="cluster-XXX implement"
)
```

返回 task-id(e.g. `bjat04xwl`),codex 完成时 harness 自动发 task-notification 唤醒 controller。

### 完成检测

- Primary: task-notification(harness 自动发,codex exit 时即触发)
- Fallback: controller wakeup 时仍 sweep log tail 找 `^EXIT=0` 防 notification 漏(zombie 30min mtime 无 EXIT → 告警)

### 反面(❌ 禁止)

- ❌ 主链路主动用 `nohup consensus-rnd-cli spawn-codex ... & disown` 图省事 → 脱离 Claude harness,UI 看不到 shells,失去即时 task-notification
- ❌ 已 detached 的 codex 仍在跑,controller kill 后重派 → 浪费工作;应靠已确认 wake 源 + `EXIT=0` sweep 接住
- ❌ Bash `run_in_background: false` 同步等 codex(可能跑 1-2h)→ Bash tool 阻塞,turn 卡死
- ❌ codex 跑在 controller 自己的 conversation Bash 里 → 同步阻塞 OR 中断 UI

<a id="hard-rules-details"></a>
## Hard rules details

## Hard rules (controller-level, propagated into every codex prompt)

1. **No new features** — only clean violations of CLAUDE.md philosophy.
2. **No external repo changes** — $EXTERNAL_REPOS are out of scope.
3. **Code refactor rationale follows policy** — `$HOST_REFACTOR_COMMENT_POLICY` missing/empty/default is `none`: source refactor-history comments are forbidden and rationale belongs in external artifacts. Explicit `self-doc-comment` is a downstream compatibility opt-in for a 3-5 line host-style source comment with `Refactor (iterN/cluster-XXX)`, `Old pattern`, and `New principle`; it must still obey source English-only.
4. **No `commit`/`push`/`checkout` inside codex prompts** — the controller owns git topology.
5. **No `sleep/delay`-based test pacing** — tests must use deterministic awaiters.
6. **No `[Skip]` / disabled tests** as a way to make CI green.
7. **No scope creep** — codex must print `SCOPE_EXTEND: <file> <reason>` before touching anything outside `scope_paths`.
8. **Source files are English-only; external user-facing artifacts are 中文 by default**. Inside `.rs` / `.lua` / `.sh` / `.py` / `.ts`, comments, docstrings, `log.{info,warn,error}` strings, error/panic text, identifiers, and code-built commit-body templates are English. Outside source files, GitHub issue bodies, PR descriptions, design notifications, git commit messages written by the controller/codex, docs, TODO markers, and natural-language artifacts use 中文. `README.md` + `README.zh-CN.md` is the only English-canonical public-doc carve-out: `README.md` is English canonical, `README.zh-CN.md` is the 中文 companion, and large-section order alignment is enough. English may appear inline when quoting (a) a CLAUDE.md / AGENTS.md clause, (b) source error messages, (c) test names — quote verbatim, do not translate. No mandatory parallel English section.

## 工作语言规则(源码内英文,源码外中文)

Policy: **源文件内部 English-only;源文件之外的 user-facing artifact 默认 中文**。

中文适用对象:GitHub issue body、PR description、PR comments、design issue auto-loop 评论、scorecard docs (`docs/audit-scorecard/`)、escalation 文案、cross-post 通知、controller / codex 写出的 git commit message、`docs/*.md`、TODO 标记。`README.md` + `README.zh-CN.md` 是唯一英文 canonical 公开文档 carve-out:`README.md` 英文 canonical,`README.zh-CN.md` 中文 companion,双向交叉链接且大段顺序对齐即可,不要求逐句对等。Internal artifact(`.refactor-loop/runs/*.md` and named daemon state artifacts)仍是英文(只要 grep / 调试用)。

英文适用对象:所有源文件(`.rs` / `.lua` / `.sh` / `.py` / `.ts`)内部自然语言与代码元素,包括注释、docstring、`log.{info,warn,error}` 字符串、error / panic 文本、代码 identifier、代码内构造的 commit-body 模板字符串。fkst 是 substrate,无 end-user UI;人读 `git log` / `journalctl` / source,英文 log 与注释强制英文同理,保持 LLM 语料一致、跨工程 reuse、无 encoding / 字体问题。

<a id="language-policy-details"></a>
## Language policy details

### 规则

| 内容类型 | 语言 |
|---|---|
| GitHub issue title / body / 评论 | **中文** |
| GitHub PR title / body / 评论 | **中文** |
| Git commit message | **中文**(包括 controller 写的 fix/merge/squash 等) |
| Push notification | **中文** |
| Public identity README pair | `README.md` is **English canonical**; `README.zh-CN.md` is the **中文 companion**. This is the only English-canonical public-doc carve-out. |
| Skill 文档 / $REPO_ROOT 的架构/词汇文档(若有) /audit 报告 | 维持现状(中英混排已存在) |
| **代码内 `// Refactor (iterN/cluster-XXX):` 注释** | **英文**(production code 跨团队读) |
| **代码内 doc comment / xmldoc / 其他注释** | **英文** |
| **代码内 log / error / panic 字符串** | **英文** |
| **代码内构造的 commit-body 模板字符串** | **英文** |
| 代码 identifier / 类名 / 方法名 / 字段 | 英文(遵循 host / 项目惯例) |
| schema / data 结构 | 英文 |
| CLI 命令 / 文件路径 / SHA / URL | 英文 |
| CLAUDE/AGENTS 条款 verbatim 引用 / error message / test name / 第三方英文 quote | 引用原文,不翻译 |

具体红线:
1. 不再生成平行 `## English` section。
2. 不再要求 `_en` + `_zh` 对。`prompts/audit.md` `human_brief` 块只保留中文字段(去掉 `_zh` 后缀)。
3. TL;DR 也是中文。
4. Controller 自己写的 `git commit -m "..."` 用中文。fix codex / writer codex prompt 里要求写中文 commit message。
5. PR title 中文(但分支名仍 `refactor/iterN-cluster-XXX-...` 英文以维持 ID 惯例)。
6. 源文件内部不写中文自然语言。❌ `log.info("开始派发事件")` → ✅ `log.info("dispatching event")`;❌ `panic!("配置缺失")` → ✅ `panic!("missing configuration")`。
7. **已发布的 EN+ZH 历史 artifact 保留原样**:不回头删 / 重译。新发的按本规则走。

<a id="historical-bilingual-notes"></a>
## Historical bilingual notes

### 历史 bilingual 规则的位置

本节之前的"Bilingual rule (双语规则)"硬要求双语 + equivalence test 已废止。所有 codex prompt 在引用本 skill 时,把历史 "bilingual EN+ZH" 一律读作"中文(允许英文引用)"。Active prompt 文件不得重新要求平行 `## English` / `## 中文` 双段；历史迁移记录只保留在本节。

### 例外

`$REPO_ROOT 的架构/词汇文档(若有)` 与 `docs/adr/*.md` 在仓库内的文档仍按 [$REPO_ROOT 的架构/词汇文档(若有)architecture-vocabulary.md]($REPO_ROOT 的架构/词汇文档(若有)architecture-vocabulary.md) 既有惯例(混排,不归本规则管辖)。CLAUDE.md / AGENTS.md 仍是中英混排,不动。

<a id="work-unit-contract"></a>

## Work-unit contract

The work-unit contract describes the fields carried through audit artifacts, GitHub design issues,
prompt artifacts, and implementation/review run artifacts. Do not add migrated queue containers,
normalizer helpers, root state migrations, producer abstractions, registry helpers, or envelope
wrappers, except the optional read-only `transition_assessment` sidecar documented here.
<!-- Refactor (issue-262): Old/New
Old: transition ranking and prompt context had no narrow checked-in sidecar boundary.
New: optional read-only transition_assessment is the only sidecar exception for ranking/prompt projection.
-->

The optional read-only `transition_assessment` sidecar is a ranking/prompt projection fact keyed
by `work_unit_id` and `source_ref`. It is not stable candidate NDJSON, not a work-unit envelope wrapper,
and not a WorkUnit producer. Missing/malformed/untrusted -> unknown with confidence 0.
The only canonical path is `.refactor-loop/runs/transition-assessments/<safe-work-unit-id>.json`,
where `<safe-work-unit-id>` matches `[A-Za-z0-9._-]+`; no explicit path, directory scan, writer,
controller alias, host command, host lens, `bedc_ci.py` call, marker change, branch change, or
work-unit token change is part of this sidecar. Transition bucket order is
`positive-discovery > classifier-shift > formal-hardening > ledger-repair > record-growth > unknown`.
`positive-discovery` requires both classifier-surface delta and `net_positive_signal=true`.

Naming policy: this engine's public product identity is Consensus R&D, and `codex-refactor-loop`
remains the stable installed skill entrypoint. `refactor` is a valid development/work-unit
metaphor and compatibility intake, not a requirement to rename the skill, add an alias, or create a
new identity contract.

Required identity/provenance fields:

- `work_unit_id`: canonical work-unit identity.
- `kind`: work-unit type, for example `audit-cluster`; future producers may use non-audit kinds.
- `producer`: source producer, for example `audit`; future producers must not pretend to be audit.
- `source_ref`: stable pointer to the source material, for example `audit-iter-N.md#cluster-001`.

Required audit-work fields when present in planned units:

- `scope_paths`
- `old_pattern`
- `new_principle`
- `verification_hints`
- `dependencies`
- `risk`
- `leverage`

Audit compatibility:

- For current audit-backed units, `work_unit_id == id == cluster_id == legacy_cluster_id`.
- For non-audit units, `work_unit_id` is not required to start with `cluster-`; omit
  `legacy_cluster_id` and do not fabricate `cluster_id`.
- Prompt dispatch for current audit-backed units exports `WORK_UNIT_ID=$CLUSTER_ID`. Existing
  markers, artifact names, branch names, and audit section lookups may continue to use
  `CLUSTER_ID` during compatibility.

Stable operational tokens:

- Current markers, GitHub labels, issue title prefixes, branch prefixes, artifact paths, prompt
  markers, log markers, and audit section lookups are stable operational names.
- `cluster-009-marker-label-compat-migration` does not rename, dual-write, or add aliases for
  these names. Keep existing `refactor`, `cluster`, `auto-loop`, and `*_DONE` spellings as the
  public routing surface while `WORK_UNIT_ID=$CLUSTER_ID` is the compatibility bridge.

## Producers

The work-unit contract separates the queue item contract from the source that produced the item.
The controller recognizes exactly these producer values:

- `audit`
- `manual-issue`

This is a documented normalization boundary, not a new producer framework. Do not add new
producer abstractions, registry helpers, envelope wrappers, or migrated work-unit state containers
for this contract.

The sidecar `producer` field is assessment provenance only and does not extend the WorkUnit
producer enum. The only allowed sidecar provenance values are `audit` and `manual-issue`;
`host:<slug>` is not allowed in the first version.

### `audit` producer

`audit` remains the stable compatibility producer value and fallback issue producer, not the default
main path. It runs only after no open actionable managed issue/PR, queued dispatch, clean marker route,
CI/no-gap route, maintainer-comment route, or higher-priority wakeup route exists. It reads the raw
artifact contract from `prompts/audit.md` and the resulting `.refactor-loop/runs/audit-iter-N.md`
cluster sections. The controller leaves `prompts/audit.md` unchanged and projects each accepted audit
cluster into the work-unit contract before dispatching or opening a design issue:

- `work_unit_id: <cluster-id>`
- `id: <cluster-id>`
- `cluster_id: <cluster-id>`
- `kind: audit-cluster`
- `producer: audit`
- `source_ref: .refactor-loop/runs/audit-iter-N.md#<cluster-id>`
- `legacy_cluster_id: <cluster-id>` optional but recommended during compatibility window

Audit-backed units may keep using `<cluster-id>` for branch names, worktree paths, artifact
filenames, markers, and audit section lookup while `WORK_UNIT_ID=$CLUSTER_ID` remains the compatibility alias.

### `manual-issue` producer

`manual-issue` is the Consensus-rnd Phase design-intake `crnd:triage:pending` intake path for maintainer-selected GitHub
issues. Accepted issues must be reshaped into a work-unit-backed design issue before Consensus-rnd Phase design-consensus
solver dispatch:

- `work_unit_id: issue-<N>`
- `kind: manual-work-unit`
- `producer: manual-issue`
- `source_ref: gh-issue-<N>`
- `scope_paths`
- problem / invariant text
- `verification_hints`

Manual issues must not fabricate `cluster_id` or `legacy_cluster_id`; those fields are audit
compatibility aliases only.

<a id="specialized-state-artifacts"></a>
## Specialized state artifacts

There is no root `.refactor-loop/state.json` contract. The controller must not create or maintain
a root local state queue, schema, resumability index, or debug ledger. Local machine-readable state
is owned by named producers, while controller decisions come from GitHub labels/comments, clean
`EXIT=0` log tails, prompt artifacts, git topology, and Consensus-rnd Phase design-consensus run artifacts.

### Statusline snapshot schema

`consensus-rnd-cli concurrency` writes `.refactor-loop/state/statusline-snapshot.json` once per tick
for the Claude Code statusline. The write is atomic (`tmp` file plus rename), and the consumer is
read-only.

```json
{
  "ts": "2026-05-26T08:45:00Z",
  "actual": 7,
  "expected": 5,
  "floor": 4,
  "p0_streak": 0,
  "last_p0_at": null,
  "freeze_minutes": 0,
  "open_pr_count": 5,
  "open_issue_count": 4
}
```

Fields:

- `ts`: UTC snapshot generation time.
- `actual`: current this-loop `consensus-rnd-cli spawn-codex` process count.
- `expected`: current no-gap expected worker count from active catalog-managed issues/PRs.
- `floor`: host `CODEX_FLOOR`, with the existing hard lower bound of 2.
- `p0_streak`: consecutive no-gap violation tick count.
- `last_p0_at`: UTC timestamp of the latest P0 no-gap violation, or `null`.
- `freeze_minutes`: whole minutes since the newest local PHASE/REVIEW/FIX/META marker file mtime; 0 when no marker exists.
- `open_pr_count`: open catalog-managed PR count from the same GitHub scan used by the monitor.
- `open_issue_count`: open catalog-managed issue count from the same GitHub scan used by the monitor.

Other named surfaces include `.refactor-loop/state/phase8-review-state.json`,
`.refactor-loop/state/recent-pr-merges.json`, `.refactor-loop/codex-progress-state.json`,
`.refactor-loop/comment-monitor-state.json`, and `.refactor-loop/.concurrency-monitor-state.json`.
Each producer owns its own schema and lifecycle.

<a id="batching-heuristics"></a>
## Batching heuristics

Goal: parallel safety. Two clusters can be in the same batch **only if** all four hold:

1. `scope_paths` file overlap = 0.
   For executable consensus→implement wakeup actions, `wakeup-plan` normalizes `scope_paths` to repo-relative file/directory keys and emits only the first action in an overlapping group as executable; later overlapping actions are `status_only` with `suppressed_reason=scope_conflict_waiting`, while disjoint groups remain parallel.
2. They touch different `$BUILD_CMD 目标/工程文件` files (compile-time isolation).
3. They touch different schema/protocol files.
4. Their `dependencies:` lists don't reference each other.

Greedy bin-packing:

1. For checked-in callers that read the optional `transition_assessment` sidecar, sort by
   transition bucket before `risk` and `leverage`; missing/malformed/untrusted sidecars are
   `unknown, confidence=0`, preserving existing ordering among units with no sidecar.
2. Sort candidate work units by `risk` (low first), then `leverage` (high first).
3. For each cluster, assign to first batch where it's compatible with every existing member.
4. Each batch has at most `max_parallel_clusters`.

If a cluster cannot fit in any new batch ≤ `max_parallel_clusters`, start a new batch for it.

<a id="recovery-playbook"></a>
## Recovery playbook

### Audit codex crashed / timed out

- Log will end with `EXIT=124` (timeout) or non-zero (crash).
- Re-dispatch with narrower scope: split scan into two passes (write a smaller audit prompt focused on a sub-area).

### Implement codex returned `partial` or `blocked`

- Read the cluster's implement summary for blocker description.
- If blocker is "scope ambiguity" → tighten prompt, re-dispatch.
- If blocker is "test fundamentally broken" → spawn a separate "fix the test" mini-cluster before retrying.
- After 2 consecutive failures → post the repeated-failure reason, do NOT auto-retry; surface via PushNotification.

### Verify returned `rework`

- Append verify's "Rework instructions" section to the cluster's implement prompt.
- Re-dispatch implement codex in the same worktree (do not destroy the worktree; codex keeps the working tree changes plus rework instructions).
- After 2 rework cycles → escalate to `abort`.

### Merge conflict in Consensus-rnd Phase publish

- `git merge --abort` first.
- Treat as `rework` with conflict diff appended to the prompt.
- Re-dispatch implement codex with explicit instruction: "rebase your changes onto trunk HEAD, resolve listed conflicts".

### cwd leak in Consensus-rnd Phase publish ("Already up to date.")

Symptom: `git merge` after a `cd "$REPO_ROOT/.worktrees/<id>"` chain prints `Already up to date.` instead of merging the branch into trunk.

Cause: the harness persists Bash cwd across invocations, so an earlier `cd` into the worktree leaks into the merge call. The merge then runs from inside the worktree (which is already at the branch's tip), so git correctly reports no-op.

Fix:
- Always prefix the merge call with `cd "$REPO_ROOT" &&` when chained, OR
- Run the worktree-scoped commit in one Bash call, then run `cd $REPO_ROOT && git merge ...` in a separate call.

Detection: after every merge, verify `git log --oneline -1` shows the new merge commit (not the prior trunk head). If not, redo from `$REPO_ROOT`.

### Consensus-rnd Phase ci-watch remote-ci check stuck

- Cap fix attempts per check at 2.
- After cap: post reason `remote-ci-stuck:<check>`, push PushNotification with run url, stop the loop.
- Common stuck causes: real environmental gap (docker service missing on runner), test contract change needing human design call, flake masking a real issue. Each is a stop-and-escalate signal, not auto-retry.

### Consensus-rnd Phase publish stacked-PR rebase storm

When PR A (bottom of a stack) gets reviewer changes:

1. A's branch updates with new commits.
2. Every downstream PR (B, C, … stacked on A) needs `git rebase --onto A's-new-head A's-old-head <downstream-branch>`.
3. Force-push each rebased branch with `--force-with-lease` (refuse if remote moved unexpectedly).
4. Re-run local CI per cluster (rebase may have semantic conflict beyond textual).
5. If rebase fails on conflict, mark that cluster `rework`, dispatch implement codex with conflict diff + "rebase onto integration head, preserve cluster intent" instruction.

Mitigations encoded in skill defaults:
- Stack depth cap = 5 (see SKILL.md Consensus-rnd Phase publish stack-depth cap).
- Soft-dep clusters always base on `integration_branch`, never on another cluster — even if conceptually related — unless hard-dep is explicit in `audit.dependencies[]`.
- Bundle related rework: if reviewer touches A and C, rebase B then C in one batch, single CI run, single force-push round.

### Consensus-rnd Phase publish PR creation idempotency

`ControllerActions.open_pr_with_label(title, body_file, base, head)` is the
controller-owned PR open primitive. Before calling it, read the open head/base PR projection from the controller's named GitHub read surface and
reuse the existing PR number when one is already open.

Re-running the loop after partial failure must NOT create duplicate PRs.

### Consensus-rnd Phase ci-watch long-running bash

The Consensus-rnd Phase ci-watch Monitor polls the `pr-checks` projection for
up to ~30 minutes. If the harness backgrounds the merge+CI+push chain command
and it hangs at architecture_guards.sh (observed in practice — appears stuck
after the merge section), `TaskStop` it and run the remaining steps in separate
foreground Bash calls. Do not assume the chain completed.

### Trunk branch moved while batch was in flight

- Detect via live `git rev-parse HEAD` and merge-base checks before each merge.
- If moved → for each `pass` cluster: rebase its branch onto new trunk HEAD inside its worktree, re-run verify, then merge.
