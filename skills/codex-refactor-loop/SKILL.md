---
name: codex-refactor-loop
description: Use when the user wants an unattended Consensus R&D work-unit loop driven by codex CLI in isolated git worktrees, with audit/refactor as the default compatibility intake, dynamic /loop wakeups, GitHub status, and per-work-unit merges.
---

> Refactor (iter3/skill-md-controller-split): Old pattern: 单文件 2537 行 entrypoint 混 contract + 重型参考.
> New principle: SKILL.md 仅留 controller 契约 + phase index + 硬不变量.
> Heavy content moves to REFERENCE.md via anchor links, following #12 structural consensus.

# Codex Refactor Loop — Controller Contract

This SKILL.md is the controller entrypoint. It must be enough to run the loop safely on first load: hard invariants, phase routing, and the phase index stay local. Heavy schemas, full templates, command bodies, and recovery runbooks live in lazy reference anchors.

Read `REFERENCE.md` only when a phase needs the detailed body. Use normal Markdown links such as [host runtime details](REFERENCE.md#host-runtime-details); do not force-load the reference.

## Controller Contract Index

| Contract | Keep-local invariant | Controller action | Reference anchor | Prompt/script surface |
|---|---|---|---|---|
| Host config | Host facts come only from `host.env`; skill text remains host-agnostic. | `source .refactor-loop/host.env` before running actors; fail closed if required vars are absent. | [host runtime details](REFERENCE.md#host-runtime-details) | `host.env.example`, `controller_lib.sh` |
| GitHub state | GitHub 是系统状态唯一显示面. Maintainer must see current state without local logs. | Post status banners and labels in the same turn as every spawn, completion, consensus, merge, block, or escalation. | [status and escalation templates](REFERENCE.md#status-and-escalation-templates) | `post_banner.py`, GitHub labels |
| Pure orchestration | Controller = pure orchestration. It routes, posts, labels, spawns, commits, pushes, merges; codex workers change code. Narrow Phase 9 allowlist dispatch is the named daemon exception. | Never implement product/refactor code in the controller conversation. Dispatch a codex for implementation, verification, fixing, review, and design solving; let `phase9_router_daemon.py` handle only its allowlisted deterministic routes. | [controller contract details](REFERENCE.md#controller-contract-details) | `spawn-codex.sh`, prompt files |
| Sentinel | Every AI-authored GitHub body ends with a final independent `⟦AI:AUTO-LOOP⟧` line. | Filter AI comments by sentinel and AI banner prefixes; never react to own comments as maintainer input. | [sentinel and comment filters](REFERENCE.md#sentinel-and-comment-filters) | prompts, `comment-monitor.sh` |
<!-- Refactor (iter3/skill-monitor-wake-source): Old pattern: 2-lane wake source(harness task-notification + ScheduleWakeup). New principle: 3-lane wake source adds daemon-event Monitor lane(daemon writes event file -> mounted persistent Monitor bridge -> controller wakes immediately; daemon alone is not a wake source). -->
| Wake source | Each turn must end with a confirmed future wake source. | Confirm one of three lanes before ending: active daemon-event Monitor bridge, in-flight codex task-notification, or confirmed ScheduleWakeup. | [wake source rules](REFERENCE.md#wake-source-rules) | Monitor bridge, harness Bash background tasks, ScheduleWakeup |
| First wakeup | Phase 0 bootstrap is ordered and mandatory before any normal phase. | Run the Phase 0 checklist in this file, in order. | [daemon command bodies](REFERENCE.md#daemon-command-bodies) | scripts, `host.env` |
| Work unit state | WorkUnitV1 is stable v1; do not rename, migrate, or wrap it. | Read and write existing v1 containers; export `WORK_UNIT_ID=$CLUSTER_ID` for audit-backed units. | [WorkUnitV1](REFERENCE.md#workunitv1-contract), [state schema](REFERENCE.md#state-schema) | `.refactor-loop/state.json` |
| Phase routing | Markers route immediately to the next actor in the same wakeup. | Sweep `EXIT=0` logs, parse verdict markers only after clean exit, then spawn next work if actionable. | [phase routing details](REFERENCE.md#phase-routing-details) | logs, prompts |
| 3/3 consensus | Concrete plans require Phase 9 multi-solver consensus and meta-judge consensus. | Dispatch minimal, structural, delete solvers; meta-judge may return only consensus, converge, or stalled-style escalation path. | [phase 9 details](REFERENCE.md#phase-9-details) | `solver-*.md`, `meta-judge.md` |
| Floor | Keep `$CODEX_FLOOR` host-scoped codexes, default 5, hard lower bound 2. | Count only this loop's `spawn-codex.sh` processes containing absolute `$REPO_ROOT`; top up before ScheduleWakeup. | [concurrency floor details](REFERENCE.md#concurrency-floor-details) | `concurrency_monitor.py`, `peek.sh` |
| Labels | Every issue/PR has exactly one phase label and one human label. | Sync labels and banner together; `👤 human:需-maintainer-决策` only after allowed meta-layer routes. | [label bootstrap loops](REFERENCE.md#label-bootstrap-loops) | `controller_lib.sh`, GitHub labels |
| Spawn | Mainline codex spawn uses harness background tasks, not detached nohup. | Use one background task per codex; if detached already happened, preserve work and rely on log sweep plus wake source. | [codex invocation details](REFERENCE.md#codex-invocation-details) | `spawn-codex.sh` |
| Hard rules | All worker prompts inherit controller-level hard rules. | Include scope, git, test, language, and no-scope-creep constraints in every spawned prompt. | [hard rules details](REFERENCE.md#hard-rules-details) | prompt templates |
| Language | Source files are English-only; external user-facing artifacts are 中文 by default. No mandatory parallel English section. | Enforce on prompts, GitHub posts, commits, docs, source comments/logs. | [language policy details](REFERENCE.md#language-policy-details), [historical bilingual notes](REFERENCE.md#historical-bilingual-notes) | prompts, docs, commit text |

## Host 配置(通用化注入点)

These variables are injected by the host project. The skill must not hardcode project facts.

| Variable | Meaning | Default / example |
|---|---|---|
| `$REPO_ROOT` | host repository root | required in `host.env` |
| `$INTEGRATION_BRANCH` | integration branch | `auto-refact-dev` |
| `$REVIEW_BASE_BRANCH` | review base branch | `dev` |
| `$PROJECT_RULES` | project rules file and Phase 0 fixed-point target | `CLAUDE.md` |
| `$BUILD_CMD` | build command | host-specific |
| `$TEST_CMD` | test command | host-specific |
| `$CI_GUARDS` | optional CI guard script | host-specific; guard only when non-empty |
| `$SOURCE_GLOBS` | source globs for review diffs | host-specific |
| `$MAINTAINER_WHITELIST` | handles allowed for explicit maintainer decisions | host-specific |
| `$GH_REPO_SLUG` | GitHub `OWNER/REPO` slug | required for `gh --repo` |
| `$GH_OWNER` / `$GH_REPO_NAME` | compatibility fields for slug construction | optional |

### Host language policy

These optional fields carry host language, test-layout, comment, schema, and architecture-review policy into prompt text. Their default is empty. Empty means the prompt must infer from existing repository evidence plus `$PROJECT_RULES`, `$SOURCE_GLOBS`, `$TEST_CMD`, `$BUILD_CMD`, and the actual diff; it must not invent C#, .NET, protobuf, or any other host-specific default.

| Variable | Prompt meaning | Empty behavior |
|---|---|---|
| `$HOST_TEST_FILE_GLOBS` | writable test file glob or location hints for test-writing/review prompts | infer from existing tests; fail closed if unsafe |
| `$HOST_TEST_NAMING_RULE` | host test file and test method naming rule | mirror existing tests; do not assume a suffix or extension |
| `$HOST_COMMENT_RULE` | refactor/self-documentation comment syntax and applicability | match surrounding file style or mark not applicable |
| `$HOST_CODE_FENCE_LANG` | language tag for illustrative code fences in generated prompt text | omit the language tag |
| `$HOST_PROTO_POLICY` | schema/protocol review and regeneration policy when applicable | treat schema checks as diff/project-rule driven only |
| `$HOST_ARCHITECTURE_GREP_CHECKS` | host-specific architecture anti-pattern grep hints for reviewers | use `$PROJECT_RULES`, `$SOURCE_GLOBS`, `$CI_GUARDS`, and diff evidence only |

Prompt templates reference these fields as `${HOST_*}` placeholders so normal `host.env` sourcing plus `render_template`/`envsubst` injects them at prompt construction time. Do not add aliases for the rejected Set B names.

Host config rules:

1. `host.env` is the only runtime fact injection point.
2. `GH_REPO` must not be exported as a bare repo name; use `GH_REPO_SLUG`.
3. `CI_GUARDS` is optional. Use `[ -n "${CI_GUARDS:-}" ]` before invoking it and report `guards skipped: CI_GUARDS unset` when absent.
4. Source `$REPO_ROOT/.refactor-loop/host.env` before daemon or codex supervision commands.
5. Detailed daemon start examples live in [daemon command bodies](REFERENCE.md#daemon-command-bodies), including the `bash -c 'source .refactor-loop/host.env && exec` pattern and why `env $(grep ...)` is unsafe.
6. The ProjectRules fixed-point target is `$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}`.

## Skill Root Contract

`<skill-root>` means the installed `skills/codex-refactor-loop` directory containing this `SKILL.md`, `scripts/spawn-codex.sh`, and `prompts/`. Runtime scripts self-locate from their own file path; `CODEX_REFACTOR_LOOP_SKILL_ROOT` is optional and only for wrappers or nonstandard packaging. If that override is set but invalid, scripts fail closed instead of falling back to `.claude/skills`.

Detailed path examples and host installation variants stay in `REFERENCE.md`; `SKILL.md` keeps only controller-contract self-location invariants.

## Named runtime exception — autonomous release gate(per #56)

The r2 judge artifact `.refactor-loop/runs/phase9-issue56-r2-judge.md` authorizes `META_JUDGE_DONE:consensus:A-with-host-opt-in-as-gate`: autonomous release decision after one host opt-in gate. `$RELEASE_AUTO_ENABLE=true` in `host.env` is that opt-in; when it is absent or not `true`, `auto_release_gate.py` exits 0 with a noop reason and writes no release decision.

`auto_release_gate.py` is decision-artifact-only. **禁止** decider 直接 bump/commit/push: it must not run `git`, bump mapped manifests, commit, push, tag, publish, merge, close, or otherwise exercise lifecycle authority. It only computes stability from GitHub/state artifacts and writes durable release decision/candidate artifacts for the controller or release pipeline to consume.

Command contract:

| Command | Behavior |
|---|---|
| `<skill-root>/scripts/auto_release_gate.py` | Dry-run. Compute stability, decide release type when ready, write `.refactor-loop/state/release-decision.json`, and print a summary. |
| `<skill-root>/scripts/auto_release_gate.py --dispatch` | Compute a ready decision and write `.refactor-loop/state/release-decision.json` plus `.refactor-loop/state/release-candidate.json`; print a hint that the controller or `release.yml` owns bump/commit/push. |
| `<skill-root>/scripts/auto_release_gate.py --score-only` | Compute and print stability only; it does not require release opt-in and does not write the decision file. |

Stability requires all signals green and fail-closed handling on missing or red evidence: recent `contract-tests` and `manifest-version-sync` success on `$REVIEW_BASE_BRANCH` and `$INTEGRATION_BRANCH` (host.env); zero open `⏸️ phase:blocked` PRs; zero `👤 human:需-maintainer-决策` labels; zero Phase 8 reject churn at three or more consecutive rounds; last 30 minutes P0 alert streak at most 3; at least `RELEASE_AUTO_MIN_MERGES` recent merge commits in `.refactor-loop/state/recent-pr-merges.json` for the last two hours(default 1); at least five fresh daemon heartbeats; and zero unresolved `META_RESOLVED:escalate-human` records. Release cadence also requires more than `RELEASE_AUTO_MIN_INTERVAL_HOURS` hours since last release(default 2). Detailed scoring and the release decision schema live in [release decision schema](REFERENCE.md#release-decision-schema).

`release-decision.json` records `from_version`, `to_version`, `bump_type`, `commits`, `decided_at`, `stability_score`, `signals`, `ready`, `blocked_reasons`, and `release_interval`. `release-candidate.json` records the artifact-only handoff metadata, including the decision artifact path, target version, host opt-in name, and lifecycle owner.

## Release pipeline integration(post-#61)

The release lifecycle surface consumes the decision-artifact-only output. A scheduled/on-demand controller may read `.refactor-loop/state/release-candidate.json`, re-check `$RELEASE_AUTO_ENABLE=true`, then call the existing version bump command, commit/push the mapped manifest changes, and let `release.yml` publish. Alternatively, `release.yml` may read `.refactor-loop/state/release-decision.json` directly, re-check the same host opt-in, bump/publish through its own guarded jobs, and record the result.

In both integrations, `auto_release_gate.py` remains the decider only. The controller or workflow owns lifecycle operations and must re-validate the host opt-in before mutating git state or publishing. Forbidden: do not add per-release maintainer emoji ratification, approval-ticket gating, or release-candidate JSON authorization. The host opt-in is durable until removed from `host.env`.

Named exception: this autonomous release gate is host-agnostic and has no lifecycle authority. It only reads repo state/GitHub evidence, computes stability, and writes durable decision/candidate artifacts; it does not run `git`, bump mapped release manifests, commit, push, tag, publish, open, close, label, approve, merge, or otherwise lifecycle-manage issues or PRs.

## Named runtime exception — IntegrationSyncDaemonV1(per #65)
The r7 judge artifact `.refactor-loop/runs/phase9-issue65-r7-judge.md` authorizes the existing-review-base pending-event boundary. **Narrow allowlist**: release-rollup detection and existing-format pending-event emission only; event facts include `integration_branch`, `review_base_branch`, `integration_sha`, `review_base_sha`, `ahead_count`, `detected_at`, and `reason`.
**No lifecycle authority**: the daemon must not run `gh pr create`; it must not create PRs, edit PRs, label PRs, close PRs, approve PRs, merge PRs, or push directly to `$REVIEW_BASE_BRANCH`. Controller pending-event sweep re-checks open head/base PRs, writes the Chinese PR body, and calls `open_release_rollup_pr_from_pending_event`, which delegates to `open_pr_with_label`. Behavior/source-regression tests cover event emission, suppression, cooldown, and forbidden daemon lifecycle tokens.

## Named runtime exception — SkillDegradationWatchV1(per #66) — Authorization source: `.refactor-loop/runs/phase9-issue66-r8-judge.md`. Single-file checker/gates: `check_skill_degradation.py`; CI required `skill-degradation` runs `<skill-root>/scripts/check_skill_degradation.py --static`; `auto_release_gate.py` requires it beside `contract-tests` and `manifest-version-sync`. **Narrow allowlist**: run `check_skill_degradation.py`; write `.refactor-loop/.degradation-alert.log`; append existing-format pending events to `.refactor-loop/.controller-pending-events.log`; expose read-only `peek.sh` status. **Forbidden actions**: no source mutation; no git reset/rebase/merge/push; no GitHub issue/PR/body/label lifecycle mutation; no codex dispatch; no standalone daemon creation; no WorkUnit/schema/envelope changes; no protocol/plugin registry; no auto-clean root garbage; no auto-fix API. Runtime hook: `concurrency_monitor.py` may run the checker on `$DEGRADATION_WATCH_INTERVAL_SECONDS`; failures alert-only, passing writes nothing, and the hook must not mutate source, run git, call GitHub lifecycle APIs, spawn codex, create a daemon, or change WorkUnit/event schema; details: [skill degradation watch details](REFERENCE.md#skill-degradation-watch-details).

## Claude Code statusline(per #51 consensus)
`skills/codex-refactor-loop/scripts/statusline.sh` 是 fast (<200ms) read-only Claude Code statusline reader,显示本仓库 loop 实时状态(codex 计数、PR/issue 数、daemon 健康、P0 streak、freeze 指示)。

**Producer**:`concurrency_monitor.py` 每 tick 末尾原子写 `.refactor-loop/state/statusline-snapshot.json`(reuse 现有 daemon,**无新 daemon**)。Snapshot 包含 `daemons` map(扫 `.refactor-loop/heartbeats/*.ts` 动态发现,每条记 `age_seconds` + `stale`,stale 阈值 90s)+ 汇总 `daemons_healthy` / `daemons_total`。
**Consumer**:`statusline.sh` 读 snapshot,bash + jq < 200ms。任一 daemon stale → ⚠ 红色。显示形如 `⚙ 5/10 PR:1 issue:9 d:5/5`。

**Install**(host project,**手动一行,无 installer script**):

```json
// ~/.claude/settings.json
"statusLine": "/abs/path/to/skills/codex-refactor-loop/scripts/statusline.sh"
```
(host 用安装后的 `<skill-root>/scripts/statusline.sh` 或拷过去的对应路径。)

**Uninstall**:删 `statusLine` 字段即可。

**Named runtime exception(per #51 consensus)**:
- **Narrow allowlist**:concurrency_monitor 写 snapshot + 顺手 stat `heartbeats/*.ts` 汇总 daemon 健康;不引入新 daemon、不持 lifecycle authority、不读 prompt body。
- **Host-agnostic**:snapshot schema 不含 host fact;daemon 发现按 heartbeat 文件 glob,无 hard-coded daemon 列表;statusline.sh 不假设 host repo 结构(只依赖 $REPO_ROOT)。
- **No lifecycle authority**:statusline 只 read,不写 GitHub / git / file lifecycle。
- **Behavior tests**:`test_statusline.sh` < 200ms + 各 state icon + freeze 指示 + daemon health 显示;`test_concurrency_monitor.py::SnapshotDaemonHealthFieldTests` 覆盖 fresh / stale / malformed / missing-dir / 动态发现 / snapshot 字段。
- **Source-regression**:本段 + Named exception 子段 + install one-liner。

授权来源:`.refactor-loop/runs/phase9-issue51-r3-judge.md`(Phase 9 r3 3/3 unanimous consensus on C framing)。

## Anti-stop restart helper cron/launchd install(per #49)

`skills/codex-refactor-loop/scripts/restart-daemons.sh` 是 checked-in,host-agnostic restart helper。它维护 5 个既有 daemon 的 singleton+heartbeat wrapper,若 wrapper alive 且 heartbeat fresh(`<90s`)则 skip;否则重启对应 wrapper。

Host project cron install one-liner(每 5 min):

```bash
*/5 * * * * cd $REPO_ROOT && bash skills/codex-refactor-loop/scripts/restart-daemons.sh >> .refactor-loop/logs/restart-cron.log 2>&1
```

launchd host template:

```xml
<key>ProgramArguments</key>
<array>
  <string>/bin/bash</string>
  <string>-lc</string>
  <string>cd $REPO_ROOT && bash skills/codex-refactor-loop/scripts/restart-daemons.sh >> .refactor-loop/logs/restart-cron.log 2>&1</string>
</array>
<key>StartInterval</key><integer>300</integer>
```

## Named runtime exception — anti-stop restart helper(per #49)

`skills/codex-refactor-loop/scripts/restart-daemons.sh` = Phase 9 r3 授权的 cron/launchd-only anti-stop helper,不新增 watchdog daemon。

- **Narrow allowlist**: helper 只 maintain singleton+heartbeat wrapper lifecycle for `concurrency_monitor`, `comment-monitor`, `codex-progress-reporter`, `dev_sync_daemon`, `triage-monitor`;不 spawn codex / commit / push / merge / label。
- **Host-agnostic**: 只使用 `$REPO_ROOT` 相对路径和 `<skill-root>` self-location;无 host fact hardcode。
- **No lifecycle authority**: 不开关 issue/PR,不打 label,不 commit/push/merge/tag/release;controller wakeup `STALE_CONTROLLER` 事件仅 alert。
- **Behavior tests**: `test_restart_daemons.py` 覆盖 fresh heartbeat skip / stale/missing/malformed heartbeat repair / dead pid repair / duplicate cleanup / concurrent helper no double-spawn。
- **Source-regression**: `AntiStopRestartHelperContractTests` 字面断言本段标题、narrow allowlist、no lifecycle authority、cron/launchd install、#49 r3 judge artifact path、helper singleton check + heartbeat freshness check、controller wakeup ordering、anti-regression forbidden tokens。

授权来源:`.refactor-loop/runs/phase9-issue49-r3-judge.md`(Phase 9 r3 `META_JUDGE_DONE:consensus:A-cron-only-with-pending-event-alert`)。

## Wakeup Skeleton

Every `/loop`, task notification, ScheduleWakeup resume, or daemon pending-event wakeup follows this skeleton. Daemon pending-event wakeups are valid only through a mounted persistent Monitor or equivalent harness bridge; daemon alone is not a wake source. The Phase 9 router daemon may replace controller dispatch for SOLVER_DONE triplets, converge, and valid stalled continuation; controller fallback sweep remains authoritative for every other marker.

`peek.sh` is a status lens, not routing authority; route actions still come from Phase Routing, clean-exit sweep, and the Phase 9 router daemon.

1. Run `bash <skill-root>/scripts/peek.sh | tail -80` first.
2. Load host config with `source .refactor-loop/host.env`; if missing or malformed, fail closed and post a status explaining the blocked bootstrap.
3. Before pending-event sweep, marker parsing, concurrency-floor handling, or dispatch/spawn, read daemon heartbeats(`.refactor-loop/heartbeats/*.ts`);任 stale/missing/malformed `>90s` → 调 `bash <skill-root>/scripts/restart-daemons.sh`;无 progress >10 min(检 `.refactor-loop/runs/` + `.refactor-loop/logs/` mtime)→ 写 `STALE_CONTROLLER:freeze_minutes=N` 到 `.refactor-loop/.controller-pending-events.log`(no lifecycle authority,仅 alert).
4. Sweep GitHub comments and pending events, excluding sentinel comments, AI banner prefixes, and bot authors.
5. Sweep all recent logs. A worker is complete only when `tail -5 <log>` contains `^EXIT=0`.
6. Parse verdict markers only after `EXIT=0`; marker text in prompt echoes is not a completed verdict.
7. Apply phase routing in the same turn; do not leave an actionable marker for the next wakeup.
8. Post GitHub banner and sync labels for each state transition.
9. Run controller wakeup step 1.5 for the concurrency floor before any `ScheduleWakeup`.
10. Spawn the next codexes with harness background tasks if actionable work exists.
11. Confirm a wake source: an active daemon-event Monitor bridge, an in-flight background task notification, or a successfully registered ScheduleWakeup.
12. Run `peek.sh | tail -80` again after spawn, merge, banner, or close actions.

## Phase Index

The phase index is the local routing map. It intentionally links to heavy details instead of inlining them.

| Phase | Local controller contract | Detail anchor |
|---|---|---|
| Phase 0 | First wakeup bootstrap. Must complete before normal routing. | [Phase 0 details](REFERENCE.md#phase-0-details) |
| Phase 1 | Produce WorkUnitV1 items. Audit remains the default compatibility producer; manual issue intake is separate. | [WorkUnitV1](REFERENCE.md#workunitv1-contract), [batching heuristics](REFERENCE.md#batching-heuristics) |
| Phase 2 | Implement one codex per active work unit in the batch. Controller owns branch/worktree topology and prompt construction. | [phase routing details](REFERENCE.md#phase-routing-details) |
| Phase 3 | Verify with a separate codex from the implementer. Verification may return ok, rework, partial, or blocked. | [recovery playbook](REFERENCE.md#recovery-playbook) |
| Phase 4 | Controller commits, merges, pushes, and opens PRs. Workers never commit/push/checkout. | [merge and push details](REFERENCE.md#merge-and-push-details) |
| Phase 5 | Watch remote CI after push; classify failures and route fix/test-add work immediately. | [remote CI details](REFERENCE.md#remote-ci-details) |
| Phase 6 | Integration branch auto-sync is daemon-owned; controller verifies health and reacts to events. | [daemon command bodies](REFERENCE.md#daemon-command-bodies) |
| Phase 7 | Sweep design issues and maintainer comments every wakeup. External issues enter through explicit labels or triage. | [design issue details](REFERENCE.md#design-issue-details) |
| Phase 8 | Three independent PR reviewers; fixes loop until reviewer consensus or meta-layer reflection. | [phase 8 details](REFERENCE.md#phase-8-details) |
| Phase 9 | Three solvers plus meta-judge. Sole authorization gate for concrete plans. | [phase 9 details](REFERENCE.md#phase-9-details) |

## Phase 0 — Bootstrap (first wakeup only)

Phase 0 is mandatory and ordered. Do not spawn normal actors before it completes.

1. `source .refactor-loop/host.env` from `$REPO_ROOT`; if it is absent, unreadable, or lacks required values, fail closed.
2. Validate `REPO_ROOT`, `GH_REPO_SLUG`, `INTEGRATION_BRANCH`, `REVIEW_BASE_BRANCH`, `BUILD_CMD`, `TEST_CMD`, and `SOURCE_GLOBS` according to host policy.
3. Run `ProjectRulesFixedPointEnsurer(强制,先于任何 actor 派发)` against `$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}`.
4. If the helper exits non-zero, helper 退出非 0 → bootstrap fail closed; post the failure and stop before actors.
5. initialize state in `.refactor-loop/state.json` if missing, using WorkUnitV1 v1 containers only.
6. Ensure the integration branch exists locally and remotely; create it from `$REVIEW_BASE_BRANCH` only when missing.
7. ensure labels for the exact phase/human taxonomy; bootstrap command loops live in [label bootstrap loops](REFERENCE.md#label-bootstrap-loops).
8. ensure all 6 daemons are alive as singletons: `concurrency_monitor.py`, `codex-progress-reporter.sh`, `comment-monitor.sh`, `dev_sync_daemon.py`, `triage-monitor.sh`, and `phase9_router_daemon.py`.
9. arm persistent daemon-event Monitor bridge for `.refactor-loop/.controller-pending-events.log` and `.refactor-loop/.concurrency-alert.log`.
10. dispatch producer: audit by default, or manual issue intake only when explicit GitHub labels select it.
11. Post a GitHub status card for Phase 0 completion or blocked state.
12. confirm a wake source before ending: daemon-event Monitor bridge active, background task notification in flight, or ScheduleWakeup returned scheduled.

Phase 0 anti-patterns stay local because they are safety gates:

- Do not continue with missing `host.env` under guessed defaults.
- Do not skip `ProjectRulesFixedPointEnsurer` because `$PROJECT_RULES` already exists.
- Do not start fewer than the six required daemons.
- Do not initialize a state-v2, alternate queue, wrapper envelope, or renamed work-unit schema.
- Do not post local-only bootstrap status; GitHub must show the state.

## Phase Routing

Routing is marker-driven, but markers are trusted only after `EXIT=0` at the tail of the log.

| Finished marker | Same-wakeup controller action |
|---|---|
| `AUDIT_DONE` | Create design issues for `requires_design` units; dispatch direct implement work where allowed. |
| `SOLVER_DONE` from minimal, structural, and delete for same issue/round | Spawn same issue/round meta-judge; this triplet route may be executed directly by `phase9_router_daemon.py`. |
| `META_JUDGE_DONE:consensus:<framing>` | Post consensus card, move labels, dispatch implement codex. |
| `META_JUDGE_DONE:converge:round-N` | Dispatch round N solvers; no hard round cap; this route may be executed directly by `phase9_router_daemon.py`. |
| `META_JUDGE_DONE:escalate:stalled` | Dispatch meta-reflector only when the stalled predicate holds; no-framing evidence must be evaluated through the stalled reflector template and preferentially dropped; do not label human directly; this route may be executed directly by `phase9_router_daemon.py`. |
| `META_RESOLVED:retry-fix` | Dispatch fix with reflector constraints and bounded retry window. |
| `META_RESOLVED:re-design` | Close/withdraw current path and restart Phase 9 only for concrete new framing or a cited current maintainer directive/current authorization artifact. |
| `META_RESOLVED:re-cluster` | Close current PR/issue path and queue re-split. |
| `META_RESOLVED:drop` | Close as no-op/wontfix with explanation. |
| `META_RESOLVED:escalate-human:<reason>` | Only then call `apply_human_label_or_skip` for `👤 human:需-maintainer-决策` and post reason banner if not skipped by maintainer-directive. |
| `IMPLEMENT_DONE:ok` | Controller commits/pushes/opens PR, then dispatches Phase 8 reviewers. |
| `IMPLEMENT_DONE:blocked` | Route to recovery or Phase 9 depending on reason. |
| Latest complete Phase 8 reviewer round resolves to `MERGE` or `MERGE_WITH_COMMENTS` | Merge path; surface comments for `MERGE_WITH_COMMENTS`. |
| Latest complete Phase 8 reviewer round resolves to `WAIT_EXPLICIT_APPROVAL` | Surface comments and wait; do not merge or dispatch fix. |
| Latest complete Phase 8 reviewer round resolves to `FIX` | Dispatch fix codex for next round using reject evidence as blocking input. |
| Phase 8 gate incomplete or invalid (`WAIT_OR_REDISPATCH`) | Wait or re-dispatch the missing/invalid reviewer; never merge. |
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

Status card templates and escalation ASCII diagrams are in [status and escalation templates](REFERENCE.md#status-and-escalation-templates).

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
- Named exception: `phase9_router_daemon.py` owns only the narrow Phase 9 allowlist (`SOLVER_DONE` triplet, `META_JUDGE_DONE:converge`, valid `META_JUDGE_DONE:escalate:stalled`) and appends fallback pending events for everything else.

Controller non-duties:

- Do not edit product/refactor code as the controller.
- Do not run reviewer, solver, implementer, or verifier reasoning inline when a codex role exists.
- Do not fabricate consensus without Phase 9.
- Do not hide status in local files only.
- Do not create new runtime abstractions, event envelopes, state versions, or producer registries for this split, except the Phase 9-authorized phase9_router_daemon.py private ledger plus existing-format pending-event append for narrow deterministic Phase 9 dispatch; do not introduce WorkUnitV2, public marker aliases, ControllerOrchestrator, ControllerEvent, ControllerCommand, or lifecycle authority.

## Concurrency Floor

The floor is local because it prevents loop stalls.

<!-- Refactor (iter4/skill-floor-fill-not-optional): Old pattern: "If below floor and no higher-priority actionable marker exists, dispatch audit" left "actionable marker" undefined,导致 controller 拿 in-flight codex 当 actionable marker rationalize defer top-up。New principle: actionable marker 必须 EXIT=0 / maintainer comment / CI red / no-gap;in-flight codex (没 EXIT=0) 不算;floor 不足时 ordinary audit fallback is guarded by the latest controller-validated audit result, and a validated AUDIT_DONE:none:0 stops fabricated refill work.(2026-05-26 maintainer-directive + issue-86 Phase 9 consensus) -->

- `$CODEX_FLOOR` defaults to 5 and has a hard minimum of 2.
- Use `FLOOR=$(( ${CODEX_FLOOR:-5} < 2 ? 2 : ${CODEX_FLOOR:-5} ))`.
- Count only this repository's loop codexes: command line contains `spawn-codex.sh` and the absolute `$REPO_ROOT`.
- Exclude shell ` -c ` wrapper rows so each real codex counts once.
<!-- Refactor (iter4/skill-count-cli-canonical): Old pattern: controller 手 ps | grep spawn-codex.sh 重新实现 count_in_flight_codex 逻辑,容易跟 daemon 算法漂移。 New principle: 直接调 `python3 <skill-root>/scripts/concurrency_monitor.py --count-only` 拿 canonical 整数,或 `--list-codex` 拿每条 supervisor cmdline。禁止 controller 临时 ps/awk pipeline。(2026-05-26 maintainer-directive 等价 Phase 9 共识) -->
- **Canonical CLI**(controller 强制使用,**禁止**手 `ps | grep`):
  - `python3 <skill-root>/scripts/concurrency_monitor.py --count-only` → 打印 canonical 计数(int)并退出
  - `python3 <skill-root>/scripts/concurrency_monitor.py --list-codex` → 每行一个 supervisor cmdline,scope `$REPO_ROOT` + 排除 ` -c ` wrapper
  - `python3 <skill-root>/scripts/concurrency_monitor.py --once` → 跑一 tick 退出(替代曾 missing 的 one-shot 入口)
  - 直接读 daemon 日志 `tail -1 .refactor-loop/logs/concurrency_monitor.log` 也是 canonical 来源;两条路径都比 controller 自重算 ps grep 安全。
- 自 PR #<本>: `concurrency_monitor.py` 不仅 alert; actual < floor 且 dispatch-queue 非空时自动派发(per host 实证 "低于预期数就继续派发"). controller 写 queue 即可,无需自己 ps grep + spawn.
- controller 每次 wakeup 的 step 1.5 checks the count and 必须在任何 `ScheduleWakeup` 之前执行.
- If below floor, consume real work first: existing dispatch queue, then higher-priority actionable marker, then maintainer comment, CI red, no-gap violation, or Phase 7 / Phase 9 actionable route. "Actionable marker" 限定为:log tail `EXIT=0` 后的完成 verdict (FIX_DONE / REVIEW_DONE / IMPLEMENT_DONE / SOLVER_DONE / META_JUDGE_DONE / TEST_ADD_DONE / AUDIT_DONE / VERIFY_DONE),或新 maintainer comment、CI red、no-gap violation。in-flight codex (没 EXIT=0) 不是 actionable marker——以"等 cascade / fix 完会派 reviewers"为由 defer floor top-up 是绕规则。
- Ordinary audit fallback is valid only before the latest controller-validated audit reaches `AUDIT_DONE:none:0`. Before that fixed point, the guarded fallback remains: envsubst 下一 iteration `prompts/audit.md` 到 `.refactor-loop/prompts/audit-iter-N.md` → `spawn-codex.sh` 用 harness background task 启动。
- After the latest controller-validated audit is `AUDIT_DONE:none:0` and no real queued/actionable work exists, emit `CONCURRENCY_LOW:no-work-after-audit-none` and do not fabricate ordinary audit, profile, planner, or synthetic producer work just to satisfy `$CODEX_FLOOR`.
- "派 audit 重 / daemon target stale / 等 cascade / 和已有工作冲突" 都不接受作为 defer 理由 before the validated `AUDIT_DONE:none:0` fixed point; after that fixed point, the correct visible state is `CONCURRENCY_LOW:no-work-after-audit-none`, not fake work.

More detail is in [concurrency floor details](REFERENCE.md#concurrency-floor-details).

## Named runtime exception — concurrency_monitor auto-topup(per #57)

`skills/codex-refactor-loop/scripts/concurrency_monitor.py` 的 `top_up_from_dispatch_queue` + tick() deficit 分支 = **第二个** Phase 9 / maintainer-directive 等价授权的 controller-runtime 直接 dispatch 路径(narrow allowlist):

- **Narrow allowlist**: 只在 `actual < max(expected, CODEX_FLOOR)` 且 `.refactor-loop/dispatch-queue/{p0,p1,p2}/*.dispatch.json` 非空时 fork `spawn-codex.sh` detached;不读 prompt body、不决定 cd / log path,只消费 controller / actor 入队 spec。
- **Host-agnostic**: dispatch JSON schema host-agnostic;cd/prompt/log path 由入队方决定,不含 host fact。
- **No lifecycle authority**: 不开 / 关 issue / PR,不打 label,不 commit / push;只 fork 进程 + 归档 JSON + 写 event log。
- **Behavior tests**: `test_concurrency_monitor.py` 覆盖 priority order / overshoot prevention / dispatch_one / tick() 整链 / floor 边界 / archive collision / filename-derived task_id。
- **Source-regression**: `test_ensure_project_rules_fixed_points.py` 字面断言本段标题 + "top_up_from_dispatch_queue" + "DISPATCH_FIRED" + "CONCURRENCY_LOW" + "narrow allowlist" 等关键字面。

授权来源:`.refactor-loop/runs/maintainer-directives/2026-05-26-concurrency-auto-topup.md`(per CLAUDE.md maintainer-directive equivalence 子句,PR #48 merged)。

## Named runtime surface — codex-progress-reporter TEST_NO_LOOP(per #69)

`skills/codex-refactor-loop/scripts/codex-progress-reporter.sh` supports `TEST_NO_LOOP=1` only as a source-time test seam for `scripts/test_codex_progress_reporter_orphan.sh`.

- **Allowed**: behavior tests may set `TEST_NO_LOOP=1`, source the reporter inside an isolated tmp repo with stubbed `gh` and `repo_slug.sh`, and call functions such as `post_or_update` directly.
- **Forbidden**: production daemon startup, controller prompts, cron/launchd helpers, host wrappers, and manual operator runbooks must not set `TEST_NO_LOOP`; it must not be used to skip the daemon loop in a live host.
- **Fact source**: runtime truth remains `.refactor-loop/codex-progress-state.json`, `.refactor-loop/logs/*.log`, and GitHub comment existence via `gh api`. The test seam does not create a new state file, queue, lifecycle authority, or host fact source.
- **Verification**: `bash skills/codex-refactor-loop/scripts/test_codex_progress_reporter_orphan.sh` covers delete success, transient delete failure retry, 404 gone handling, and prior orphan retry; `python3 -m unittest discover -s skills/codex-refactor-loop/scripts -p 'test_*.py'` includes source-regression assertions for this narrow surface.

授权来源:`.refactor-loop/runs/maintainer-directives/2026-05-27-progress-reporter-orphan-delete.md`(maintainer-directive for issue #69 orphan progress comments)。

## Spawn Contract

Mainline spawn contract:

1. Use one harness background task per codex. Do not batch multiple codexes inside one detached shell.
2. Invoke `<skill-root>/scripts/spawn-codex.sh --cd <absolute-dir> --prompt <prompt> --log <log> --stall <seconds>`.
3. `--cd` must be absolute so process counting can scope to `$REPO_ROOT`.
4. Prompt files live under `.refactor-loop/prompts/`; logs live under `.refactor-loop/logs/`.
5. Completion detection primary path is harness task notification; fallback is log tail `EXIT=0` sweep.
6. If a codex was accidentally detached, do not kill and re-dispatch solely to regain tracking. Confirm the log is sweepable and confirm a wake source.
7. Detailed invocation examples live in [codex invocation details](REFERENCE.md#codex-invocation-details).

## Label 系统 — 强制

Every issue/PR has exactly one phase label and exactly one human label.

Phase labels:

| Label | Meaning |
|---|---|
| `🔍 phase:design-solving` | Phase 9 solvers/judge active. |
| `✅ phase:consensus-reached` | Consensus accepted and ready for implementation. |
| `🛠️ phase:implementing` | Implement codex active. |
| `🚀 phase:pr-open` | PR exists and is waiting for review/CI route. |
| `👀 phase:reviewing` | Phase 8 reviewers active. |
| `🔧 phase:fixing` | Fix codex active. |
| `⚙️ phase:ci-running` | Remote CI watch/fix active. |
| `🎉 phase:merged` | Work landed. |
| `⏸️ phase:blocked` | Dependency or explicit wait. |

Human labels:

| Label | Meaning |
|---|---|
| `🤖 human:auto-推进` | Fully automatic; no maintainer action needed. |
| `👤 human:需-maintainer-决策` | Meta-layer exhausted or explicit maintainer decision needed. |

## `👤 human:需-maintainer-决策` 严格语义(强制)

# Refactor (iter4/human-label-semantics-guard): Old pattern: label 当 architect reject workaround. New principle: 严语义 + reflector self-check + controller helper guard + source-regression test.

**Apply only when** maintainer must physically perform an action:
- product/strategy decision that cannot be derived from code/repo
- explicit governance approval(Tier I/II,non-codable)
- manual merge a script cannot execute(rare)

**DO NOT apply when**:
- architect/quality reviewer 因 "needs Phase 9 artifact" reject → 开真 Phase 9(reflector option A)
- reviewer 与 maintainer prior session directive 冲突 → 把 directive 编码为 `.refactor-loop/runs/maintainer-directives/<date>-<topic>.md` artifact + 更新 reviewer prompts 含 "maintainer-directive precedence" 段
- controller uncertain → reflector,不 label
- reflector 自己 emit `META_RESOLVED:escalate-human` 但 controller 复审发现 maintainer 已授权 → 撤 label,以 maintainer-directive artifact 替代

**禁止**:把 `👤` label 作 architect/quality reject 的绕路工具。

Hard label rules:

1. Label transition and banner post happen together.
2. Same group only allows one active label.
3. `👤 human:需-maintainer-决策` is not a shortcut for controller uncertainty.
4. Legacy `🆘 human:` labels may be removed as cleanup targets only.
5. PRs must carry `auto-loop` or comment monitoring will miss them.
6. Bootstrap command loops live in [label bootstrap loops](REFERENCE.md#label-bootstrap-loops).

## Phase 8 — Multi-Codex PR Review

Phase 8 keeps the consensus merge gate local enough for routing:

<!-- Refactor (iter3/skill-merge-policy): Old pattern: unanimous-approve merge gate + Phase 8 文案矛盾  New principle: 固定真值表 reject=0 && approve>=1 → MERGE;comment 是 advisory(#26 minimal option B 共识) -->

1. Dispatch three reviewers in parallel: architect, tests, quality.
2. Each reviewer posts or emits a `REVIEW_DONE` verdict.
3. Controller computes one fixed action vocabulary after the latest complete required round: `MERGE`, `MERGE_WITH_COMMENTS`, `WAIT_EXPLICIT_APPROVAL`, `FIX`, or `WAIT_OR_REDISPATCH`.
4. Truth table: `reject=0`, `approve=R`, `comment=0` → `MERGE`; `reject=0`, `approve>=1`, `comment>=1`, `approve+comment=R` → `MERGE_WITH_COMMENTS`; `reject=0`, `approve=0`, `comment=R` → `WAIT_EXPLICIT_APPROVAL`; `reject>=1` → `FIX`; missing role, duplicate/unknown verdict, no `EXIT=0`, stale head SHA, CI pending/fail, or non-mergeable PR → `WAIT_OR_REDISPATCH`.
5. `comment` is terminal advisory evidence: surface it, but do not count it as approval and do not dispatch fix for comments alone.
6. `FIX` dispatches fix codex; fix completion dispatches reviewers again.
7. After repeated fix failure, dispatch meta-layer reflector before any human label.
8. Every Phase 8 action posts to the PR for traceability.
9. Detailed reviewer prompts, retry rules, and anti-spiral safeguards are in [phase 8 details](REFERENCE.md#phase-8-details).

## Phase 9 — Multi-Solver Design Consensus

Phase 9 is the sole authorization gate for concrete plans.

1. Dispatch exactly three solver framings by default: minimal, structural, delete/defer.
2. A meta-judge reads all three solver outputs.
3. Concrete implementation authorization requires 3/3 solver convergence plus meta-judge `consensus`.
4. `converge:round-N` always routes to another solver round; no hard round cap.
5. `escalate:stalled` routes to reflector, not directly to human.
6. Maintainer replies reset the round when they materially change framing.
7. Any concrete plan bypassing Phase 9 is invalid.
8. Full consensus card template and solver rules are in [phase 9 details](REFERENCE.md#phase-9-details).

## Status Banners

Local contract:

- Post status on every spawn, completion, phase transition, consensus, merge, CI failure, block, and human-needed state.
- Use specific phase, current actor, next action, and whether maintainer action is required.
- Never say only vague text such as “processing”.
- Include the final sentinel line.
- Full banner and escalation templates live in [status and escalation templates](REFERENCE.md#status-and-escalation-templates).

## Loop control

This is an infinite refactor/research loop; do not idle after one iteration completes.

Loop rules:

1. Last cluster merged means roll up state and dispatch next audit/producer pass.
2. Stop only on explicit maintainer stop, unrecoverable bootstrap failure, or approved human-needed escalation.
3. Sync to remote promptly after controller-owned commits.
4. Each wakeup checks CI status for open auto-loop PRs before sleeping.
5. Each wakeup checks pending daemon events.
6. Each wakeup verifies daemon singleton health.
7. Each wakeup enforces the concurrency floor.
8. Transient stream disconnects route to log sweep and wake-source confirmation, not panic re-dispatch.
9. Recovery cases are in [recovery playbook](REFERENCE.md#recovery-playbook).

Policy:the loop continues until an explicit stop condition or a visible `👤 human:需-maintainer-决策` reason surface is reached.

## Hard rules (controller-level, propagated into every codex prompt)

1. No new features; only clean the authorized violation or implement the consensus plan.
2. No external repo changes; `$EXTERNAL_REPOS` are out of scope unless the user explicitly expands scope.
3. Code self-documents refactors with the host-required refactor comment format when touching source.
4. No `commit`, `push`, `checkout`, PR create/merge, or issue close inside worker prompts; controller owns git topology.
5. No sleep/delay-based test pacing; use deterministic awaiters.
6. No `[Skip]`, disabled tests, ignored tests, or manual category escapes to make CI green.
7. No scope creep; workers must print `SCOPE_EXTEND: <file> <reason>` before touching outside authorized scope.
8. Source files are English-only; external user-facing artifacts are 中文 by default. No mandatory parallel English section.
9. Do not rename the skill, manifests, WorkUnitV1, public markers, branch prefixes, or labels during this split.
10. Do not hardcode host facts into this cross-platform skill.

Details are in [hard rules details](REFERENCE.md#hard-rules-details).

## 工作语言规则(源码内英文,源码外中文)

Policy: Source files are English-only; external user-facing artifacts are 中文 by default. No mandatory parallel English section.

Chinese by default:

- GitHub issue titles, bodies, and comments.
- GitHub PR titles, bodies, and comments.
- Git commit messages written by controller/codex.
- Push notifications.
- Escalation/status wording.
- Docs and TODO markers unless the host document has a stronger local convention.

English-only inside source:

- Comments and docstrings.
- Log, error, and panic strings.
- Identifiers, type names, fields, proto/yaml structural keys.
- Code-built commit-body templates.

Allowed inline English in Chinese artifacts:

- Verbatim quotes from AGENTS/CLAUDE rules.
- Source error messages.
- Test names.
- CLI commands, paths, SHAs, URLs, and labels.

Historical bilingual notes are moved to [historical bilingual notes](REFERENCE.md#historical-bilingual-notes).

## Files

- [prompts/audit.md](prompts/audit.md) — audit producer prompt.
- [prompts/implement.md](prompts/implement.md) — implement worker prompt.
- [prompts/verify.md](prompts/verify.md) — verify worker prompt.
- [prompts/remote-ci-fix.md](prompts/remote-ci-fix.md) — remote CI fix prompt.
- [prompts/test-add.md](prompts/test-add.md) — codecov/test-add prompt.
- [prompts/meta-reflector-stalled.md](prompts/meta-reflector-stalled.md) — meta-reflector self-check prompt for stalled routes.
- [prompts/design-issue-body.md](prompts/design-issue-body.md) — design issue body template.
- [prompts/design-issue-reply.md](prompts/design-issue-reply.md) — maintainer-comment analyst prompt.
- [prompts/reviewer-architect.md](prompts/reviewer-architect.md) — Phase 8 architecture reviewer.
- [prompts/reviewer-tests.md](prompts/reviewer-tests.md) — Phase 8 tests reviewer.
- [prompts/reviewer-quality.md](prompts/reviewer-quality.md) — Phase 8 quality reviewer.
- [prompts/review-fix.md](prompts/review-fix.md) — Phase 8 fix worker.
- [prompts/solver-minimal.md](prompts/solver-minimal.md) — Phase 9 minimal solver.
- [prompts/solver-structural.md](prompts/solver-structural.md) — Phase 9 structural solver.
- [prompts/solver-delete.md](prompts/solver-delete.md) — Phase 9 delete/defer solver.
- [prompts/meta-judge.md](prompts/meta-judge.md) — Phase 9 meta-judge.
- [scripts/spawn-codex.sh](scripts/spawn-codex.sh) — codex supervisor.
- [scripts/peek.sh](scripts/peek.sh) — controller wakeup summary.
- [scripts/controller_lib.sh](scripts/controller_lib.sh) — shared controller helpers.
- [scripts/post_banner.py](scripts/post_banner.py) — GitHub banner posting helper.
- [scripts/ensure_project_rules_fixed_points.py](scripts/ensure_project_rules_fixed_points.py) — Phase 0 fixed-point helper.
- [scripts/concurrency_monitor.py](scripts/concurrency_monitor.py) — no-gap sentinel daemon.
- [scripts/restart-daemons.sh](scripts/restart-daemons.sh) — cron/launchd anti-stop helper for existing daemon wrappers.
- [scripts/codex-progress-reporter.sh](scripts/codex-progress-reporter.sh) — progress comment daemon.
- [scripts/comment-monitor.sh](scripts/comment-monitor.sh) — maintainer comment monitor.
- [scripts/dev_sync_daemon.py](scripts/dev_sync_daemon.py) — integration sync daemon.
- [scripts/triage-monitor.sh](scripts/triage-monitor.sh) — external issue triage daemon.
- [scripts/phase9_router_daemon.py](scripts/phase9_router_daemon.py) — narrow Phase 9 direct-dispatch daemon.
- [REFERENCE.md](REFERENCE.md) — heavy runbooks, templates, schemas, and recovery details.

## Controller Wakeup Checklist

Use this checklist literally on each wakeup:

1. Peek first.
2. Load host config.
3. Check pending daemon events.
4. Sweep GitHub comments with sentinel/bot filters.
5. Sweep log tails for `EXIT=0`.
6. Parse markers only after clean exit.
7. Route all actionable completions.
8. Post GitHub status before or with spawned work.
9. Sync phase and human labels.
10. Check open PR CI failures.
11. Verify daemon singleton health.
12. Enforce floor before sleep.
13. Spawn next codexes with harness tracking.
14. Commit/push only when controller-owned lifecycle requires it.
15. Confirm wake source, including maintaining the daemon-event Monitor bridge.
16. Peek again after visible actions.
17. End only when GitHub reflects the current state.

Priority order when multiple actions are possible:

1. Bootstrap failure or missing wake source.
2. Maintainer comment that changes design framing.
3. Completed worker marker ready for same-wakeup route.
4. CI red on open auto-loop PR.
5. No-gap violation.
6. Floor deficit.
7. Producer dispatch for next work unit.
8. Routine ScheduleWakeup.

When uncertain:

- Prefer a reversible status post plus a correctly scoped codex dispatch.
- Prefer Phase 9 for design uncertainty.
- Prefer reflector for repeated AI-loop disagreement.
- Prefer recovery playbook for operational failures.
- Never invent a human gate just because the controller is tired.

## Durable State Contract

The local state file is a recovery aid, not the maintainer-facing state surface.

Authoritative surfaces:

1. GitHub comments and labels tell humans what is happening.
2. `.refactor-loop/logs/*` tells the controller which actors exited cleanly; verdict markers are trusted only after `EXIT=0`.
3. `.refactor-loop/state.json` is a resumability index and debug ledger, not a phase decision source of truth.
4. `.refactor-loop/prompts/*` tells future maintainers what was dispatched.
5. Branches, worktrees, and PRs tell git topology.

State rules:

1. Keep `schema_version: 1` and `work_unit_schema_version: 1`.
2. Use existing containers: `clusters_planned`, `clusters_active`, `clusters_done`, `clusters_failed`, `design_pending`, `remote_ci`.
3. Do not add a state-v2 migration for this split.
4. Do not add queue aliases, envelope wrappers, or normalizer helpers.
5. For audit-backed work, keep `work_unit_id == id == cluster_id` during v1 compatibility.
6. For manual issue work, do not fabricate `cluster_id`; use `work_unit_id: issue-<N>`.
7. Prompt dispatch may keep `WORK_UNIT_ID=$CLUSTER_ID` for current audit-backed units.
8. Public v1 operational names remain stable: `cluster`, `refactor`, `auto-loop`, `*_DONE`, branch prefixes, marker names, and label names.
9. Full schema and examples live in [state schema](REFERENCE.md#state-schema).

State write timing:

1. Before spawn, record intended actor, prompt, log, target issue/PR, and phase.
2. After spawn, record background task id if the harness exposes one.
3. After completion, move active record to done or failed in the same wakeup that routes the marker.
4. After PR creation, record PR number and base/head.
5. After merge, record merged commit and close/label state.
6. After a recovery decision, record the reason string and next route.

## Producer Contract

The controller recognizes two v1 producers:

| Producer | Intake | Controller behavior |
|---|---|---|
| `audit` | Default compatibility audit/refactor intake. | Run audit prompt, project accepted clusters into WorkUnitV1, batch by dependencies/risk. |
| `manual-issue` | Explicit GitHub issue intake via labels/triage. | Normalize problem and verification hints into a design issue, then use Phase 9. |

Producer rules:

1. Audit is the default when the user asks for the unattended loop without a narrower producer.
2. Manual issues enter only through explicit maintainer label or triage monitor routing.
3. `requires_design` audit clusters open GitHub issues and do not auto-implement until Phase 9 consensus.
4. Direct implementation is allowed only for clusters already authorized by policy and not requiring design.
5. Batching should prefer independent, low-risk work and preserve dependency ordering.
6. Detailed producer fields and batching heuristics live in [WorkUnitV1](REFERENCE.md#workunitv1-contract) and [batching heuristics](REFERENCE.md#batching-heuristics).

## Phase Guardrails

Phase 1 guardrails:

1. Run the producer with host-injected `$SOURCE_GLOBS`.
2. Write audit output to `.refactor-loop/runs/audit-iter-N.md`.
3. Convert accepted units into WorkUnitV1 before dispatch.
4. Clean stale worktrees before audit pollution can affect decisions.
5. For `requires_design`, open or update GitHub design issues and label them `🔍 phase:design-solving` plus `🤖 human:auto-推进`.

Phase 2 guardrails:

1. One implement codex per work unit in the current batch.
2. Each work unit gets an isolated worktree and branch.
3. Prompt includes scope paths, old pattern, new principle, verification hints, hard rules, language rule, and sentinel rule.
4. Implement codex must not commit, push, create PRs, merge, or close issues.
5. `IMPLEMENT_DONE:ok` means the controller may inspect diff, run host checks, commit, and advance.

Phase 3 guardrails:

1. Verifier is independent from implementer.
2. Verification uses `$BUILD_CMD`, `$TEST_CMD`, and optional `$CI_GUARDS`.
3. Optional guards require `[ -n "${CI_GUARDS:-}" ]`; otherwise report `guards skipped: CI_GUARDS unset`.
4. Rework returns to implement/fix routing, not human by default.
5. Verification logs still require tail `EXIT=0`.

Phase 4 guardrails:

1. Controller owns commit, merge, push, PR create, PR close, and PR merge.
2. Workers never run git lifecycle commands unless a prompt explicitly says a command is forbidden context.
3. Re-read current branch and worktree before merge to avoid cwd leaks.
4. After merge/push/open PR, post status and run `peek.sh`.
5. Stacked PR mode and single PR mode details live in [merge and push details](REFERENCE.md#merge-and-push-details).

Phase 5 guardrails:

1. Every wakeup checks all open auto-loop PR CI before sleeping.
2. Red CI routes immediately to classification and fix/test-add dispatch.
3. Pre-existing failures are reported, not blindly fixed in the PR.
4. Codecov patch failures route to test-add work.
5. Repeated same-check failure routes through meta-layer policy before human escalation.

Phase 6 guardrails:

1. Integration sync is daemon-owned.
2. Controller verifies daemon singleton health and reacts to pending events.
3. Controller does not run an ad hoc sync loop when the daemon is healthy.
4. Sync failures must be visible on GitHub.
5. Daemon command details live in [daemon command bodies](REFERENCE.md#daemon-command-bodies).

Named exception: `IntegrationSyncDaemonV1` owns integration branch auto-sync, resolver continuation push, merged-rollup adoption, and release-rollup pending-event detection. The controller verifies singleton health, reads pending events, fetches after daemon pushes, and does not run checkout/merge/push sync while the daemon is healthy. Resolver codexes resolve conflicts only; they never push, reset, or abort. Release-rollup pending events do not grant daemon PR lifecycle authority.

Phase 7 guardrails:

1. Sweep design issues every wakeup.
2. Maintainer replies that materially change framing reset the Phase 9 round.
3. Bot comments and AI sentinel comments do not count as maintainer input.
4. External issues require explicit opt-in labels or triage monitor normalization.
5. Do not auto-implement from a free-form issue without Phase 9 consensus.

Phase 8 guardrails:

1. Dispatch architect, tests, and quality reviewers in parallel.
2. Reviews are tied to a PR head SHA.
3. Any reject produces a fix round unless meta-layer reflection is triggered.
4. Reviewer consensus must be visible on the PR.
5. Re-review after push; do not reuse stale approval across materially changed heads.

Phase 9 guardrails:

1. Minimal, structural, and delete/defer solvers run for each design round.
2. Meta-judge consumes all three outputs.
3. Only 3/3 consensus plus meta-judge `consensus` authorizes implementation.
4. `converge` means more solver work, not human escalation.
5. `stalled` means reflector, not human escalation.
6. Maintainer input can reframe the next round, but the controller does not synthesize a concrete plan alone.

## Recovery Triage

Use this local triage before opening the heavy recovery playbook:

| Symptom | First controller action | Reference |
|---|---|---|
| Codex log has no tail `EXIT=0` | Treat as still running or crashed; do not parse markers. | [recovery playbook](REFERENCE.md#recovery-playbook) |
| Prompt marker appears in log body | Ignore until clean exit and filtered real marker. | [phase routing details](REFERENCE.md#phase-routing-details) |
| Worktree merge says already up to date unexpectedly | Check cwd and branch before retrying. | [recovery playbook](REFERENCE.md#recovery-playbook) |
| Remote CI monitor appears stuck | Check PR checks directly and dispatch fix if red. | [remote CI details](REFERENCE.md#remote-ci-details) |
| No codex running with active work | Treat as no-gap violation and spawn next route. | [concurrency floor details](REFERENCE.md#concurrency-floor-details) |
| Repeated reviewer/fix loop | Dispatch reflector before human label. | [phase 8 details](REFERENCE.md#phase-8-details) |
| Design consensus not converging | Continue rounds or reflector according to judge marker. | [phase 9 details](REFERENCE.md#phase-9-details) |
| Maintainer says stop | Stop visibly, leave state/logs intact, and do not schedule wakeup. | [recovery playbook](REFERENCE.md#recovery-playbook) |

Recovery rules:

1. Preserve useful in-flight work whenever possible.
2. Prefer idempotent re-checks over destructive cleanup.
3. Do not delete worktrees or branches unless their PR/branch state proves they are stale.
4. Do not rewrite history on shared branches.
5. Post what happened and what the controller will do next.

## GitHub Posting Contract

Direct-post prompts:

- `design-issue-body.md`
- `design-issue-reply.md`
- `_github-post-rules.md` inclusions where the prompt explicitly posts to GitHub

Marker/artifact-only prompts:

- `audit.md`
- `implement.md`
- `verify.md`
- `remote-ci-fix.md`
- `review-fix.md`
- `reviewer-architect.md`
- `reviewer-tests.md`
- `reviewer-quality.md`
- `solver-minimal.md`
- `solver-structural.md`
- `solver-delete.md`
- `meta-judge.md`
- `test-add.md`
- `triage-external-issue.md`

Posting rules:

1. Controller posts lifecycle banners directly.
2. Worker prompts post only when their prompt explicitly owns a GitHub reply/body.
3. Every GitHub body uses the sentinel final line.
4. Avoid plain-text unverified human names or handles.
5. Whitelisted mentions come from `$MAINTAINER_WHITELIST`.
6. Label changes require a banner explaining the reason.

## Anchor Read Policy

The entrypoint is intentionally enough for controller routing. Open reference anchors only when the current phase needs the heavy body.

When to read an anchor:

1. Before writing a full status or escalation banner, read [status and escalation templates](REFERENCE.md#status-and-escalation-templates).
2. Before editing state shape or producer normalization, read [WorkUnitV1](REFERENCE.md#workunitv1-contract) and [state schema](REFERENCE.md#state-schema).
3. Before starting or repairing daemons, read [daemon command bodies](REFERENCE.md#daemon-command-bodies).
4. Before changing label bootstrap or transition helpers, read [label bootstrap loops](REFERENCE.md#label-bootstrap-loops).
5. Before handling repeated failure, stuck CI, merge conflicts, or stale worktrees, read [recovery playbook](REFERENCE.md#recovery-playbook).
6. Before changing language policy in prompts, read [language policy details](REFERENCE.md#language-policy-details).

When not to read an anchor:

1. Do not load all of `REFERENCE.md` at startup.
2. Do not require agents to use forced-load reference syntax.
3. Do not link to absolute local paths.
4. Do not copy heavy templates back into this entrypoint to solve a one-off routing question.

## Controller Ownership Boundaries

Controller-owned operations:

1. Create, update, and validate `.refactor-loop/state.json`.
2. Create worktrees and branches for worker tasks.
3. Render worker prompts from stable prompt files and current state.
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

1. Change the host policy or project rules outside the managed fixed-point block.
2. Approve a real human-needed decision after `META_RESOLVED:escalate-human`.
3. Stop the loop.
4. Expand scope beyond the current work unit or repo.
5. Change product/philosophy boundaries that Phase 9 cannot decide.
