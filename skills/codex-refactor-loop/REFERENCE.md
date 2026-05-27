# codex-refactor-loop — Reference

Detailed specifications, heavy templates, schemas, command bodies, and recovery playbooks for [SKILL.md](SKILL.md). The entrypoint keeps the controller contract and phase index; this file carries the material that should be loaded lazily by anchor. Keep full command bodies, long path examples, schemas, and recovery matrices here; keep only short invariants and anchor links in `SKILL.md`.

<a id="controller-contract-details"></a>
## Controller contract details

The following excerpts preserve the detailed controller runbook that was moved out of SKILL.md during the skill-md-controller-split. Keep host-specific facts injected by `host.env`; do not add absolute local paths or platform-specific installation facts here.

<a id="release-decision-schema"></a>
### Release decision schema

`auto_release_gate.py` is a one-shot controller helper, not a daemon. It reads `$REPO_ROOT/host.env` or `$REPO_ROOT/.refactor-loop/host.env`; only `RELEASE_AUTO_ENABLE=true` enables decision writes or `--dispatch` candidate writes. `--score-only` prints the same stability calculation without requiring opt-in and without writing state. The decider is decision-artifact-only and does not run `git`; controller or `release.yml` owns any manifest bump, commit, push, tag, publish, merge, or close action.

Stability score is the percentage of the eight boolean signals that pass. `ready=true` requires score 100 plus the release interval and at least one commit since the last release. Live signal inputs are intentionally narrow:

| Signal key | Pass condition |
|---|---|
| `required_checks_recent_green` | `contract-tests` and `manifest-version-sync` completed successfully on both `$REVIEW_BASE_BRANCH` and `$INTEGRATION_BRANCH` (host.env) within two hours. |
| `no_open_blocked_pr` | No open PR has `⏸️ phase:blocked`. |
| `no_human_decision_label` | No open issue or PR has `👤 human:需-maintainer-决策`. |
| `no_phase8_reject_churn` | `.refactor-loop/state/phase8-review-state.json` reports fewer than three consecutive reject rounds. |
| `p0_alert_streak_ok` | `.refactor-loop/.concurrency-monitor-state.json` zero streak and recent P0 alert lines are both at most 3 in the last 30 minutes. |
| `recent_pr_merges_min` | `.refactor-loop/state/recent-pr-merges.json` reports at least `RELEASE_AUTO_MIN_MERGES` commits in the last two hours(default 1). |
| `fresh_heartbeats` | At least five entries in `.refactor-loop/state/daemon-heartbeats.json` are fresh within 90 seconds. |
| `no_unresolved_human_escalation` | `.refactor-loop/state/meta-resolutions.json` has zero `unresolved_escalate_human` entries. |

Tests or controller-side aggregators may write `.refactor-loop/state/auto-release-signals.json` with either booleans or `{ "passed": bool, ... }` objects for those same keys. When that file exists, it is the deterministic source for the eight gate signals.

Semver bump is computed from `.refactor-loop/state/release-commits.json` entries since the latest release: `feat!:` or `BREAKING CHANGE:` yields `major`; otherwise any `feat:` yields `minor`; otherwise `fix:`, `perf:`, `refactor:`, or any other commit yields `patch`. If stability is not ready, no commits exist, or the minimum release interval has not elapsed, `bump_type` is null and `to_version == from_version`.

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
| `release-candidate.json` | Separate artifact written by `--dispatch`; contains the decision path, target version, host opt-in hint, and lifecycle owner for controller/workflow consumption. |

<a id="host-runtime-details"></a>
## Host 运行编排(daemon 启动 + 运行节奏适配)(强制)

dogfood 运行中固化的操作经验。host 注入的配置集中放 `$REPO_ROOT/.refactor-loop/host.env`(`export REPO_ROOT/GH_REPO_SLUG/INTEGRATION_BRANCH/REVIEW_BASE_BRANCH/BUILD_CMD/TEST_CMD/CI_GUARDS/SOURCE_GLOBS/MAINTAINER_WHITELIST` 等)。

<a id="skill-degradation-watch-details"></a>
### Skill degradation watch details
`SkillDegradationWatchV1(per #66)` is authorized by `.refactor-loop/runs/phase9-issue66-r8-judge.md` and is intentionally delete-framed: no standalone watchdog, no seventh daemon, no `DegradationCheck` protocol, no plugin registry, no new event envelope, no auto-clean, no auto-fix, no GitHub lifecycle mutation, and no codex dispatch path.
Static checker: `python3 skills/codex-refactor-loop/scripts/check_skill_degradation.py --static`; CI job `.github/workflows/consensus-rnd-ci.yml` `skill-degradation`; release gate `auto_release_gate.py:required_checks_recent_green` requires `skill-degradation` beside `contract-tests` and `manifest-version-sync`, mirrored by `release.yml`. The checker is read-only and returns nonzero on missing named exception text, CI/release wiring, forbidden runtime files, forbidden expansion surfaces, or missing runtime hook markers.
Runtime hook: existing `concurrency_monitor.py`, no standalone daemon. `$DEGRADATION_WATCH_INTERVAL_SECONDS` controls throttle; unset or `0` disables and `host.env.example` opts in with `1800`. `$DEGRADATION_WATCH_TIMEOUT_SECONDS` defaults to `30`. Passing writes nothing; failing writes `.refactor-loop/.degradation-alert.log` and appends an existing-format pending event to `.refactor-loop/.controller-pending-events.log`.
Alert formats: pending event `<UTC> skill-degradation-alert returncode=N log=.refactor-loop/.degradation-alert.log` or `<UTC> skill-degradation-alert checker-error log=.refactor-loop/.degradation-alert.log`; alert log `[UTC] skill-degradation-alert <summary> | detail=<json>` with `returncode`, `stdout_tail`, `stderr_tail`, or `error`. `DEGRADATION_ALERT_TAIL_LINES` controls `peek.sh` display count for `.refactor-loop/.degradation-alert.log`, default `10`.
Narrow allowlist: run `check_skill_degradation.py`, write `.refactor-loop/.degradation-alert.log`, append existing-format pending events, and expose read-only `peek.sh` status. Forbidden: no source mutation, git operations, GitHub issue/PR/body/label lifecycle mutation, codex dispatch, standalone daemon creation, WorkUnit/schema/envelope changes, protocol/plugin registry, auto-clean root garbage, and auto-fix API.
### Worktree 位置约定(强制)

所有 daemon/codex/implement worktree 都在 `$REPO_ROOT/.worktrees/` 内,路径形如 `$REPO_ROOT/.worktrees/<name>/`。仓库根 `.gitignore` 必须包含 `/.worktrees/`,因此这些运行时 worktree 不进入发布产物。旧 sibling pattern `<repo>-wt-<name>/` 只作为历史兼容/清理线索出现,不得作为新 worktree 创建位置。

### Daemon 启动(强制 pattern — 必须注入 host.env)

**禁止** 裸 `nohup python3 <daemon> &`(拿不到 host 配置)与 `nohup env $(grep ... host.env) <daemon> &`(`BUILD_CMD="cargo build --workspace"` 含空格 → `env` 把 `build` 当命令崩)。**唯一正确**:用 `bash -c 'source host.env && exec'` 注入后再 exec:
```bash
nohup bash -c 'source .refactor-loop/host.env && exec python3 <skill-root>/scripts/<daemon>.py' \
  >> .refactor-loop/logs/<daemon>.log 2>&1 & disown
```
**6 个长跑 daemon 全部要起**(监控面 = 这 6 个):`concurrency_monitor.py`(60s codex 并发)、`codex-progress-reporter.sh`(600s 进度回贴)、`comment-monitor.sh`(30s maintainer 评论 eyes-react)、`dev_sync_daemon.py`(600s integration ← review_base 同步)、`triage-monitor.sh`(60s 外部 `auto-loop-triage` issue)、`phase9_router_daemon.py`(30s narrow Phase 9 deterministic routing)。
<!-- Refactor (iter215/cluster-215-controller-process-selftest):
  Old pattern: Controller runbook(REFERENCE.md)still instructs ps|grep/pgrep liveness checks,与 SKILL.md canonical CLI 与 CLAUDE.md daemon-counts-authority 子句矛盾。
  New principle: Controller-facing 检查必须读 daemon-maintained state / heartbeat / canonical script CLI(restart-daemons.sh / peek.sh / concurrency_monitor.py);process probes 留在 daemon / helper 实现内部,不在 controller runbook 段。
-->
**单例强制**(尤其 `dev_sync_daemon` 多实例会 race):controller 不做 process probe。每 wakeup 读 `.refactor-loop/heartbeats/*.ts` / `.refactor-loop/state/statusline-snapshot.json`;任 heartbeat missing/malformed/stale `>90s` 时调用 `bash <skill-root>/scripts/restart-daemons.sh` 让 helper 内部维护 singleton + restart。`phase9_router_daemon.py` 的 singleton 由自身 lock/ledger/fallback event contract 维护;controller 只读其 log/ledger/pending event surface,未知状态走 fallback sweep。

### Controller 主链路 wake 源不变量(强制,精化 detached 规则)

controller 主链路**优先禁止**用 `( … ) & disown` / `nohup … &` / Bash 内 `spawn-codex.sh ... &` 派 **codex**(即使为省 tool 调用想批量)。detached 进程丢的是 harness 即时 `<task-notification>`,**不是检测能力**:controller 每次 wakeup 的 `EXIT=0` / marker log sweep 仍能扫到 detached codex 的完成,只是延到下次 ScheduleWakeup,变慢。

**真正致命**的是 `detached + 无 active daemon-event Monitor bridge / 已注册的 ScheduleWakeup / task-notification`。单独 detached 只是慢;detached 后又没有任何 wake 源,loop 才会过夜停摆。

**铁律**:
- controller 主链路 codex **优先**用 **一个 `Bash run_in_background:true`** 跑一个 `spawn-codex.sh`(N 个 codex 就 N 个调用),拿即时 task-notification。
- 若 codex 意外被 detached,**不要 panic-kill 后重派**。这会浪费已跑工作;sweep 会在下次 wakeup 接住。正确动作是确认 log 路径可扫,并在本 turn 结束前确认已有 wake 源。
- 每个 turn 结束**必须**有已确认的下次唤醒源:active daemon-event Monitor bridge, 在飞 task-notification, 或 ScheduleWakeup 返回 `scheduled`。这是比"禁 detached"更本质的不变量。
- daemon 自主动作可以 detached,但必须同时满足:prompt 落 `.refactor-loop/prompts/`,log 落 `.refactor-loop/logs/`,状态写 GitHub 或 pending event 可恢复,daemon 单例,并受 `peek.sh` / liveness 检查。
- daemon alone is not a wake source; daemon event files become a wake source only through a mounted Monitor bridge.
**反面(❌ 严禁)**:
- ❌ detached codex 后发现没有 ScheduleWakeup,却 end turn。
- ❌ 误以为 `concurrency_monitor.py` / progress reporter / comment monitor 单独会唤醒 controller。daemon **只写 alert 文件 / GitHub 评论 / pending event**;只有 mounted Monitor bridge 把 daemon event file 转成 controller wakeup 时,daemon events 才是 wake 源。
- ❌ detached codex 已在跑,controller 为了"恢复追踪"直接 kill 并重派同任务。应让现有任务跑完,靠下次 wakeup sweep 接住。
### ScheduleWakeup 必须确认注册(强制)
ScheduleWakeup 是 daemon-event Monitor bridge / task-notification 丢失时**兜底**,不是 daemon-event immediate lane。每次调用后**确认返回 `scheduled`**;若 malformed(如 `<invoke>` 漏 `antml:` 前缀)或未注册 → **立即重试**,绝不带着"以为排了但没排"的假设 end turn。turn 结束前心里要有一个**已确认的下次唤醒源**(daemon-event Monitor bridge active, task-notification 在飞, 或 ScheduleWakeup 已注册),否则 loop 就死了。
### 并发 floor 的 `ps codex` 在多系统 host 上会过计(强制修正)

`concurrency_monitor` 与 floor 判定如果用全局 `ps codex exec | wc -l`,当 **host 仓库自身另有 codex-spawning 系统**(如 fkst supervisor 跑自己的 evolve/review 部门并 spawn codex)时,会把两套都算进去 → floor 永远"满"、永不补,而本 loop 实际可能 0 codex。

**更隐蔽的同机多 loop 过计(dogfood 实测)**:即使只数「含 `spawn-codex.sh`」的 codex,如果再用**相对子串** `.refactor-loop/logs/` / `.refactor-loop/prompts/` 做 scope,**同一台机器跑两个不同仓库的本 skill 时会互相过计**——两个 loop 的相对路径子串完全相同。实测另一 host 的 loop 在跑时,本仓库 `concurrency_monitor` 报 `actual=8` 而本仓库实际只有 1 个 codex,floor 被骗成"满",本仓库 codex 永远补不上去、可能长期单线程。

**修正(强制)**:floor 只数**本仓库(本 loop)的 codex** —— 命令行含 `spawn-codex.sh` **且含本仓库绝对路径 `$REPO_ROOT`**。
- spawn 时 caller **必须传绝对 `--cd`**(audit 用 `--cd $REPO_ROOT`;implement/verify 用绝对 worktree 路径),使 `$REPO_ROOT` 进入进程 cmdline;inside worktree `<repo>/.worktrees/*` 以 `$REPO_ROOT` 为前缀,亦匹配。
- ❌ **不要**只用相对子串 `.refactor-loop/logs/` 做 scope(同机多 loop 互相过计)。
- **去重(强制)**:每个 codex 会派生**两个**含 `spawn-codex.sh` 的进程 —— 真 supervisor(`bash <path>/spawn-codex.sh --cd ...`)+ 一个 shell `-c` wrapper(harness 后台任务回显整条命令)。两个都数 = 每个真 codex 被算成 2,`CODEX_FLOOR=2` 会被**单个**真 codex 满足 → 永远凑不到真正 2 并行。**排除含 ` -c ` 的行**,只数真 supervisor(spawn-codex.sh 自身不带 ` -c ` flag)。
- 不数 host 其它系统 / 其它仓库的 codex。
- 低于 `$CODEX_FLOOR` 个**本仓库** codex 才补(`CODEX_FLOOR` 由 host.env 注入,默认 5,**硬下限 2** —— 详见下「Concurrency floor」节)。

`concurrency_monitor.py` 的 `count_in_flight_codex()` 与 `peek.sh` 的 `list_loop_codex()` 均已按 `$REPO_ROOT` 绝对路径 scope。

<a id="dispatch-queue-protocol"></a>
### Dispatch queue protocol

`concurrency_monitor.py` consumes queued dispatch files when this loop is below its local floor. The queue is host-local and durable:

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

Auto-dispatch semantics:
- On each tick, if `actual < CODEX_FLOOR` and the queue is non-empty, the monitor launches at most `CODEX_FLOOR - actual` tasks via `<skill-root>/scripts/spawn-codex.sh --cd <cd> --prompt <prompt> --log <log> --stall <stall>`.
- After each launch, the monitor archives the consumed file to `.refactor-loop/dispatch-dispatched/<task-id>.json`, adding `dispatch_at`, `priority`, and `source_dispatch_file` for audit trail.
- The monitor writes `DISPATCH_FIRED:<task-id>:<priority>:<reason>` to `.refactor-loop/.controller-pending-events.log`.
- If `actual < CODEX_FLOOR` and the queue is empty, the monitor writes `CONCURRENCY_LOW:actual=N expected=M queue=0` so the controller can enqueue real work when one of the floor refill routes below is valid.
- This daemon path is a narrow exception for mechanical controller-runtime dispatch; it does not add lifecycle authority or change marker routing.

### 完成判据必须用 `EXIT=0`,marker 只作 verdict(排 prompt 回显)(强制)

判 `judge-ready` / `merge-ready` / `solver-done` / `reviewer-done` **必须先看 log 末尾 `^EXIT=0`**。`EXIT=0` 表示 codex 进程干净结束,输出文件与 marker 才可被视为完整。**不要**用 `SOLVER_DONE:` / `REVIEW_DONE:` / `META_JUDGE_DONE:` / `FIX_DONE:` marker 的存在判断"已完成"。

codex 常把 prompt 里的 marker 模板原样回显到 log(如 prompt 写 `SOLVER_DONE:<role>:<verdict>` 或 `REVIEW_DONE:<PR>:<role>:<approve|comment|reject>`)。`grep "SOLVER_DONE:"` 会命中 prompt 回显,误把失败 / 未完成 / 部分写入的 codex 判成 done,过早派 judge 或 merge 读到不完整输入。**修正**:
- readiness / done 判据:只看 `tail -5 <log> | grep -q '^EXIT=0'`。
- verdict 判据:只有 `EXIT=0` 后才解析 marker。
- marker grep 排除占位回显——`grep -E "<MARKER>:" | grep -vE "<reason>|<id>|<status>|<category>|<framing>|<role>|<verdict>|round-N"`,取**真实值**那条;真实终止 marker 在 codex 最后输出段(controller 追加的 `EXIT=` 行之前),非 prompt 引用段。
- codex 输出有时被包进 diff 风格段,marker 行带 `+` 前缀 → **不要用 `^MARKER` 锚定 verdict**(会漏),用不带行首锚的 `grep "MARKER:"` + 排占位;但完成判据仍必须锚定 `^EXIT=0` 且只看 log tail。

**反面(❌ 严禁)**:
- ❌ 三个 solver log 里都出现 `SOLVER_DONE:` 字面就派 meta-judge。
- ❌ reviewer log 里出现 `REVIEW_DONE:` 字面就进入 merge / fix。
- ❌ `grep "^EXIT="` 全 log 判 finished → codex 中途 echo / cat 含 `EXIT=` 文件会误判。必须 `tail -5`。

### gh CLI 保留 env 冲突(强制 — host.env 的 GH_REPO 坑)

`gh` CLI 把 **`GH_REPO`** 当 `--repo` 默认值且要求 `OWNER/REPO` 格式。host.env 禁止导出 `GH_REPO=<repo-name>`。统一用 `GH_REPO_SLUG=OWNER/REPO`,脚本兼容 `GH_OWNER/GH_REPO_NAME`,所有脚本内 `gh issue/pr ...` 必带 `--repo "$GH_REPO_SLUG"`,所有 `gh api repos/...` 必用 `repos/$GH_REPO_SLUG/...`。旧 host 若只有 `GH_OWNER/GH_REPO` 且 `GH_REPO` 是 repo 名,脚本会拼成 slug,但新配置不再使用裸 `GH_REPO`。

### Rust 测试节奏(host 适配)

`cargo test` 必带 `--test-threads=1`(permit 池 / cwd 等共享 fs 状态,并行会 race 出假失败);测试读源码文件用 `CARGO_MANIFEST_DIR` 锚定路径,勿用相对路径(被其它测试 chdir 污染)。

<a id="sentinel-and-comment-filters"></a>
## ⭐ 核心原则:GitHub 是系统状态唯一显示面(强制)

**Maintainer 打开 GitHub 必须一眼看到完整状态**,不用读本地 log / state.json / ps process / chat history。任何状态变化在 GitHub **立即可见**。

### 必须 reflect 到 GitHub 的状态变化

| 状态变化 | 触发位置 | GitHub 反映方式 |
|---|---|---|
| 派 codex(任何角色) | spawn 同 turn | `## 📊 状态卡片` post 到关联 issue/PR + label transition |
| Codex 完成(任何角色) | task-notification 处理 | update 卡片(或 post 新卡片说"X 已完成,下一步 Y") |
| 共识达成 | meta-judge consensus | `## ✅ 共识卡片` post(详见 Phase 9 Consensus action) |
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

### Wakeup 第一动作:`bash <skill-root>/scripts/peek.sh`(强制)

减少人工 grep / parse 错误。一眼看全:
- 活跃 codex 数(只数本 loop:命令行含 `.refactor-loop/logs/` 或 `.refactor-loop/prompts/`)
- Open auto-loop PR 的 CI + state
- Merge readiness / daemon health / monitor zero_streak 摘要
- Phase 9 router ledger + pending events 近 10 行
- Open auto-loop issue + phase label

`peek.sh` 是 observability-only status lens,只读展示 ledger / pending / readiness / health,不显示 generic marker-to-route recommendation。Route authority remains SKILL Phase Routing + controller clean-exit log-tail sweep + `phase9_router_daemon.py`。

<a id="wake-source-rules"></a>
## Wake source rules and no-gap details

### 0 codex + active task = bug(强制)
<!--
# Refactor (iter3/skill-human-label-taxonomy):
#   Old: 四个 Human label(含两个 🆘),no-gap/escalation 判定散落
#   New principle: 恰好两个 active Human label;causes 移到 reason surface(#15 structural 共识)
-->

**铁律**:任何 active phase issue/PR(`🔍 design-solving` / `🔧 fixing` / `👀 reviewing` / `🛠️ implementing`)存在时,**应至少有 1 个本 loop codex 在跑**。本 loop codex = `spawn-codex.sh` 命令行含 `.refactor-loop/logs/` 或 `.refactor-loop/prompts/`。实际为 0 且 GitHub 有 active phase → **P0 bug**(no-gap-violation)。

**Controller wakeup 第一动作**:`bash <skill-root>/scripts/peek.sh`。如果活跃 codex == 0:
1. **不允许** `ScheduleWakeup` 后 end-turn — 必须派下一步 codex 才允许 ScheduleWakeup
2. **不允许**只看 marker 不 sweep:必须扫所有刚 finished marker(implement/judge/reviewer/fix/reflector)并按 marker→spawn-next 表派至少 1 codex
3. 如果所有 active issue/PR 都真在等 maintainer(全是 `👤 human:需-maintainer-决策` / `⏸️ phase:blocked`),那 0 codex 才 OK — 但仍要在 status 报告中说明 "0 codex by design:N issue 全等人"

**concurrency_monitor.py** P0 alert:`expected > 0 AND actual == 0` → IMMEDIATE(streak=1 即写 alert + pending event,不等 2 tick)。controller 看到 alert → 立即 wake 自查。

### Controller 每 wakeup 必派"下一步"(no gap policy)

Controller wakeup 处理 markers 后,**必须在同 turn 内派出下一步 codex**(if any actionable),不留 gap 等下次 wakeup:

| Marker 完成 | 立即派 |
|---|---|
| SOLVER_DONE × 3(同 issue 同 round)| 同 issue 同 round meta-judge |
| META_JUDGE_DONE:consensus | implement codex |
| META_JUDGE_DONE:converge:r+1 | r+1 三 solver |
| META_JUDGE_DONE:escalate:stalled | reflector(per Phase 9 路由表) |
| META_RESOLVED:re-design | fresh round 三 solver with new framing |
| IMPLEMENT_DONE:ok | controller commit/push/open PR + Phase 8 reviewer × 3 |
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

controller 严格按 judge marker 判 escalate,**不允许**自己以"累了/round 多 / 触及 Tier 或哲学"等理由直接 label `👤 human:需-maintainer-决策`。

**判定铁律**:

| Judge marker | Controller 动作 | 不允许 |
|---|---|---|
| `converge:round-N` | 派 r-N 三 solver(不管 N 多大) | ❌ "round 多了"自升 escalate |
| `escalate:stalled` | 派 reflector codex | ❌ 直接 label `👤 human` |
| `escalate:philosophy:<reason>` / `escalate:gpg-ratification:<reason>` / `escalate:<其他>` | 视为 legacy judge 输出:重派 judge 或派 reflector,要求回到 consensus / converge / stalled 三出口 | ❌ 因 CLAUDE.md / Tier I/II / GPG / reinstall 直接 label 人 |
| `consensus` | 派 implement | — |
| 无 judge marker / judge crash | 重派 judge | ❌ 自判 escalate |

**正确"label 人"的唯一路径**:`reflector` 输出 `META_RESOLVED:escalate-human:<reason>` → controller 才允许 label `👤 human:需-maintainer-决策` + ASCII A/B/C reason banner。该路径只表示**共识机制本身无法收敛**,不是因为触及 Tier I/II、CLAUDE.md、核心抽象、GPG 或 reinstall。

结构性教训:controller 曾把多数 issue 误升为人工等待,根因是没有严格区分 `converge`、`stalled`、`philosophy` 三类 judge marker。只有 reflector 输出 `META_RESOLVED:escalate-human:<reason>` 后才允许 label 人;`converge` 继续派 solver,`stalled` 先派 reflector,可由 reflector 处理的 philosophy 分歧不得直接升人。

### Spawn / merge / banner 后必须 peek(强制 — 防 maintainer 漏读)

任何 controller turn 派 codex / merge PR / post banner / close issue 之后,**turn 结束前必须 `bash <skill-root>/scripts/peek.sh | tail -80` 一次扫 maintainer 评论 + 0-codex 漏洞**。

理由:`task-notification` 触发的 turn 容易陷入"处理 marker → spawn 下一步 → end turn"线性思维,会跳过 peek 而错过 maintainer 与此 task 并行的新评论。曾出现 controller 派出下一步 judge 期间漏读新的架构反馈,直到 maintainer 报错才发现;peek 是防漏读的强制尾部检查。

例外:turn 唯一动作是 ScheduleWakeup(纯休眠)可省 peek。

### Concurrency monitor:`<skill-root>/scripts/concurrency_monitor.py`(强制)

**60s** 周期 daemon,只监控 no-gap sentinel:
- expected = active issue/PR 数(per phase 表)
- actual = `spawn-codex.sh` 且命令行含 `.refactor-loop/logs/` 或 `.refactor-loop/prompts/`
- **P0 规则**:`expected > 0 AND actual == 0` → **IMMEDIATE** alert(streak=1 即触发,不等 2 tick)。这是 no-gap-violation。
- 写 `.refactor-loop/.concurrency-alert.log` + `.controller-pending-events.log`(controller 下次 wakeup 必读)
- 不读取 `CODEX_FLOOR`,不判断 refill 候选,不写非零 floor-deficit 事件,不自动 spawn codex。floor 补给只属于 controller wakeup step 1.5。

**Controller 每 wakeup 必读** `tail -20 .refactor-loop/.concurrency-alert.log`:
- 看到 `P0 no-gap-violation: ...zero_streak=N` → 至少 N×60s 没 codex,**必须**先派 codex 才允许 ScheduleWakeup
- zero_streak >= 5(>= 5 分钟 0 codex)= 严重失保 — 同时把 PushNotification 给 user "controller 失保 N min"

启动:
```bash
nohup bash -c 'source .refactor-loop/host.env && exec python3 <skill-root>/scripts/concurrency_monitor.py' \
  >> .refactor-loop/logs/concurrency-monitor.log 2>&1 &
disown
```

### 反面(❌ 严禁)

- ❌ wakeup sweep 看到 SOLVER_DONE × 3 但**不派 judge**(留 gap)
- ❌ codex 完成后只删 progress comment,不派下一步
- ❌ wakeup ScheduleWakeup 但本 turn 0 codex spawn(等 wakeup 才动 = lazy / 死循环)
- ❌ 看到 concurrency-alert.log 有 entry 但 controller 不读
- ❌ active issue 0 codex 跑 >= 1 wakeup 周期(说明 controller 漏派)

### Controller helper 库:`<skill-root>/scripts/controller_lib.sh`(强制)

7 个曾发生的 bug 都来自 controller boilerplate 重复 + bash 变量传值 bug。统一抽 helper:

```bash
source <skill-root>/scripts/controller_lib.sh

safe_worktree iterN cluster-026 origin/auto-refact-dev   # → exports WT_PATH + BRANCH
open_pr_with_label "iterN cluster-XXX: title" body.md    # → exports PR_NUM(原地传值,无 grep subshell bug)
merge_pr <pr>                                             # auto-close linked issue + cleanup labels
render_template implement.md out.md                       # 处理 {{var}} 和 $VAR 两种语法
sweep_stale_labels                                        # 清 closed but 仍挂 in-flight label
validate_prompt out.md                                    # check 0 unresolved {{var}}
```

**强制**:
- 派 codex 前必须 `validate_prompt` — 防 codex blocked on unresolved placeholder
- merge PR 必须用 `merge_pr <pr>` — auto-close + label cleanup,不留尾巴。`merge_pr` is a post-decision lifecycle primitive: call it only after the controller has already decided `MERGE` or `MERGE_WITH_COMMENTS`; it never computes Phase 8 reviewer policy.
- worktree 创建必须用 `safe_worktree` — 处理 "already exists" race
- PR 号捕获必须用 `open_pr_with_label`(直接 export PR_NUM)— **禁止** `pr_num=$(...grep -oE...)` 这种 subshell 变量传值模式

**Label 生命周期(强制状态机)**:
<!--
# Refactor (iter3/skill-human-label-taxonomy):
#   Old: 四个 Human label(含两个 🆘),no-gap/escalation 判定散落
#   New principle: 恰好两个 active Human label;causes 移到 reason surface(#15 structural 共识)
-->

```
issue/PR 状态 → 期望 label

design issue:
  open + 🤖 ai → 🔍 design-solving       (solver/judge 跑)
  open + 🤖 ai → 🛠 implementing         (implement 派出)
  open + 👤 human:需-maintainer-决策     (rework/deadlock reason in banner/comment)
  closed       → 🎉 phase:merged          (via PR merge)
  closed       → wontfix                  (per maintainer drop directive)

cluster PR:
  open + 🤖 ai → 🚀 phase:pr-open + 👀 reviewing  (reviewer 派出)
  open + 🤖 ai → 🚀 phase:pr-open + 🔧 fixing     (fix codex)
  open + 👤 human:需-maintainer-决策              (reflector escalate-human with reason banner)
  closed merged → 🎉 phase:merged                  (via merge_pr)
  closed       → (no phase, branch deleted)

rollup PR:
  open → 🚀 phase:pr-open + 🤖 human:auto-推进     (passive integration)
  注:rollup 即使 BLOCKED 也是 🤖 auto-推进,不是 maintainer 决策点
```

### Spawn pattern — Bash `run_in_background: true`(强制)

**关键架构铁律**:codex spawn 必须用 **Bash tool with `run_in_background: true`** 跑 `spawn-codex.sh`。这样 harness 会跟踪 Bash → codex 进程链,**codex exit 时 harness 立即 fire `<task-notification>` 唤醒 controller**,不用等 ScheduleWakeup。

**两步流程**(per spawn):

1. **先 post banner**(blocking Bash,几秒):
   ```bash
   python3 <skill-root>/scripts/post_banner.py \
     --banner-target <issue-or-pr> --banner-kind <issue|pr> \
     --banner-role <role> --banner-detail "..." \
     --log <log-path> --cd <worktree> --stall <s>
   ```

2. **再 spawn codex**(Bash `run_in_background: true`):
   ```bash
   <skill-root>/scripts/spawn-codex.sh \
     --cd <worktree> --add-dir $REPO_ROOT \
     --prompt <prompt-file> --log <log-file> --stall 5400
   ```

**反模式(❌ controller 主链路禁用)自定义 `Popen` detach spawn**:
- 用 `Popen + start_new_session` 把 codex 脱离 python parent → harness 看不见 codex
- 结果:codex done 1-13 分钟后 controller 才在下次 ScheduleWakeup 时才发现(0 codex 期间监控告警但 controller 在睡)
- detached spawn 会让 harness 失去追踪,曾导致 codex 完成后 controller 长时间未醒,monitor 连续告警但无人处理。

**正确语义**:controller 主链路 codex = harness-tracked Bash task = automatic task-notification on exit。daemon 自主 codex 可以 detached,但必须满足 daemon 例外规则:log/prompt 落盘、GitHub 或 pending event 可恢复、单例、liveness 可查。

**禁止**:
- ❌ controller 主链路用 `nohup ... &` 或 `Popen + start_new_session` detach codex
- ❌ 用 blocking Bash 跑 codex(同步等 60 分钟 → conversation 卡死)
- ❌ 漏 post banner → GitHub 看不到运行状态(per `post_banner.py` 强制)

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

**默认 worker = codex CLI(`codex exec`),不是 Claude `Agent` / `Task` subagent(强制)**。所有需要「思考」的工作——分析、诊断、设计、实现、验证、review、solve,乃至对本 skill 自身的 baseline / 验证测试——一律 delegate 给 `codex exec`(经 `spawn-codex.sh`)。❌ 严禁用 Claude `Agent` / `Task` subagent 替代 codex 做这些工作。理由:codex 进程是 harness-tracked、可跨 session 存活、log 落 `.refactor-loop/logs/` 可 sweep、完成发 task-notification、被 concurrency floor 计数——这套无人值守编排的**全部不变量都建立在「worker 是 codex 进程」之上**;Claude subagent 不落盘 marker、不被 floor 计数、不留可 sweep 的状态,会让监控面、恢复逻辑、并发兜底全部失效。`refactor-team` skill 才是 Agent-subagent based 的那套;**本 skill 的 worker 恒为 codex CLI**。

<a id="phase-0-details"></a>
## Phase 0 — Bootstrap (first wakeup only)

### 首次唤醒强制序列(MANDATORY — 按序跑完才能 end turn)

> 这是 first wakeup 唯一合法路径。baseline 测试证明:不把以下步骤钉成强制有序首步,controller 会只 bootstrap state + 派 audit,**漏起全部 6 daemon、漏建 labels**(把 daemon / label 误当成「别处已起好」的 steady-state 检查)。下面把它们钉成不可跳过的有序步骤。

0. **host.env 自检(缺失即停,绝不臆造)**:`source .refactor-loop/host.env` 取 `$REPO_ROOT/$GH_REPO_SLUG/$BUILD_CMD/$TEST_CMD/...`。
   - 不存在 → 从 `skills/codex-refactor-loop/host.env.example` 复制到 `.refactor-loop/host.env` 并填必填项;无法确定必填值(REPO_ROOT/GH_REPO_SLUG/BUILD_CMD/TEST_CMD)→ **PushNotification 请 maintainer 填,end turn,不 spawn 任何东西**。
   - ❌ 严禁用 `git rev-parse` / `gh repo view` 猜值后带空 BUILD_CMD/TEST_CMD 硬跑。
0b. **ProjectRulesFixedPointEnsurer(强制,先于任何 actor 派发)**:在 `host.env` 注入后立即运行:
   ```bash
   python3 skills/codex-refactor-loop/scripts/ensure_project_rules_fixed_points.py
   ```
   - 该 helper 只维护解析后的 `$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}` 内一个 `consensus-rnd:foundational-invariants` sentinel managed 区块;区块外 host 内容保持不变。
   - helper 退出非 0 → bootstrap fail closed:不得初始化 state、不得建 labels、不得起 daemon、不得派 audit / solver / reviewer / implement actor。
   - # Refactor (iter1/host-claude-md-fixed-points):
     #   Old pattern: host 的 PROJECT_RULES/CLAUDE.md 不保证基础不动点(泛化理论)在场,跑 loop 时基础理论未被可靠加载
     #   New principle: Phase 0 ProjectRulesFixedPointEnsurer 幂等向 $PROJECT_RULES 写入带 sentinel 的 managed 不动点区块(consensus:minimal,不覆盖 host 已有内容)
1. **state + integration 分支**:`mkdir -p .refactor-loop/{...}` + 写 `state.json` + idempotent 建/推 `$INTEGRATION_BRANCH`(下方细节)。
2. **建全套 labels**:跑「Label 系统」节的 Bootstrap —— 9 个 phase label + 2 个 human label 创建循环。**漏建 = 后续 phase transition 无 label 可挂、comment-monitor 查 `--label auto-loop` 漏掉 PR**。
3. **起并挂载全部 6 个 daemon**:按「Host 运行编排 → Daemon 启动」节的 `bash -c 'source host.env && exec'` pattern 起齐 `concurrency_monitor.py` / `codex-progress-reporter.sh` / `comment-monitor.sh` / `dev_sync_daemon.py` / `triage-monitor.sh` / `phase9_router_daemon.py`。随后运行 `bash <skill-root>/scripts/restart-daemons.sh` 规范化 heartbeat-managed daemon,再读 `.refactor-loop/heartbeats/*.ts` / `.refactor-loop/state/statusline-snapshot.json` / `bash <skill-root>/scripts/peek.sh | tail -80` 确认健康面可见;Phase 9 router 读其 lock/ledger/log/fallback event surface。**首轮就必须把 6 个全起起来——它不是「以后某次 wakeup 才做的 liveness 检查」**。
4. **派默认 v1 work-unit producer**(Phase 1,默认 audit,`spawn-codex.sh` + Bash `run_in_background:true`)+ ScheduleWakeup 兜底 + end turn。

每步做完才进下一步。3 漏起任一 daemon、2 漏建 labels = bootstrap 失败,下次 wakeup 第一件事补齐。

#### ❌ 严禁(首次唤醒反模式 — 均来自 baseline 失败)
- ❌ 只 bootstrap state + 派默认 producer,不起 6 daemon(baseline 默认失败模式)
- ❌ 不建 labels 就派 codex(phase transition 时无 label 可挂)
- ❌ 把整个 skill 降级成「本地读代码 + 出 markdown 报告 + 本地 commit」而不碰 GitHub、不起 daemon、不派 audit
- ❌ host.env 缺失时猜值硬跑

---

If `.refactor-loop/state.json` does not exist:

```bash
mkdir -p .refactor-loop/{logs,runs,clusters,prompts,worktrees,state}
```

Write initial `state.json`:

```json
{
  "schema_version": 1,
  "work_unit_schema_version": 1,
  "trunk_branch": "auto-refact-dev",
  "integration_branch": "auto-refact-dev",
  "review_base_branch": "dev",
  "pr_mode": "stacked",
  "max_parallel_clusters": 3,
  "iteration": 1,
  "phase": "audit",
  "clusters_planned": [],
  "clusters_active": [],
  "clusters_done": [],
  "clusters_failed": []
}
```

`work_unit_schema_version: 1` means `clusters_planned`, `clusters_active`, `clusters_done`, and
`clusters_failed` are the authoritative v1 queue containers, but each item is a WorkUnitV1 record
as specified in [REFERENCE.md](REFERENCE.md). If an existing state file lacks
`work_unit_schema_version`, read it as v1 legacy state: derive `work_unit_id` from each item's
`id`, treat audit clusters as `kind="audit-cluster"` and `producer="audit"`, and continue without
migration.

**Default integration branch**: `auto-refact-dev`. This is the long-lived branch where all auto-refactor cluster PRs land before rolling up to `dev`. On a fresh loop:

```bash
# Idempotent setup — safe to re-run
git fetch origin
git checkout -B auto-refact-dev origin/auto-refact-dev 2>/dev/null \
  || git checkout -b auto-refact-dev origin/dev
git push -u origin auto-refact-dev 2>/dev/null || true
```

Override only when the user explicitly names a different integration branch (e.g., to test a new audit prompt without polluting the canonical one). Existing loops on a different branch can keep their name; the default applies to **new** Phase 0 bootstraps only.

**`pr_mode` choice (set in Phase 0; do not change mid-loop)**:

- `"stacked"` (**default**): each cluster opens its own PR. Hard-dep clusters stack (PR B's base = PR A's branch); soft-dep / independent clusters PR against `integration_branch`. Integration branch eventually opens one rollup PR to `review_base_branch`. Reviewer sees small per-cluster PRs and can ack independently; cost is rebase-on-reject when an upstream cluster is changed. This is the right shape for typical refactor loops (3+ clusters, reviewable independently).
- `"single"`: all clusters merge to `integration_branch` and a single PR targets `review_base_branch`. Simple; reviewer sees one big PR. Use only when the loop is expected to produce ≤ 2 clusters or the user explicitly asks for a single PR.

If the user doesn't specify, default `"stacked"` and surface in bootstrap PushNotification: "Using stacked-PR mode; pass `pr_mode: single` to override."

Create top-level TaskCreate items: audit / dispatch / merge.

---

<a id="phase-routing-details"></a>
## Phase 1 — Work-unit production (audit default)

The default v1 work-unit producer is `audit`. Producer normalization is documented in
[REFERENCE.md](REFERENCE.md): v1 accepts only `producer: audit` and `producer: manual-issue`.
`audit` is the raw artifact producer for this phase; `manual-issue` enters through Phase 7
triage and must already be reshaped before Phase 9.

1. Copy `prompts/audit.md` (this skill's template) to `.refactor-loop/prompts/audit-iter-N.md`.
2. Replace `{{iteration}}` placeholder.
3. Dispatch:

   ```bash
   <skill-root>/scripts/spawn-codex.sh \
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
- b. If `AUDIT_INCOMPLETE` → log reason, re-dispatch audit with the missing pieces called out in the prompt header (e.g., "previous audit returned INCOMPLETE because <reason>; deliver the missing artifact this run"). Do NOT proceed to Phase 2 with an incomplete audit.
- c. Verify the two output files exist: `audit-iter-N.md` AND `audit-iter-N-candidates.ndjson`. Missing either → treat as INCOMPLETE.
- d. Verify the candidate file has `>= 25` entries unless the audit body explicitly explains why every analyzer pack command returned 0 hits.
- e. Verify the audit body contains the 6 fixed-analyzer-pack commands by name with hit counts.
- f. Verify reject reasons cite a CLAUDE clause + per-candidate evidence (not blanket "covered by guard"). Sample 3 random rejects; if any lack evidence → INCOMPLETE.
- g. Verify `coverage_manifest.total_opened_files >= 60` with the documented sub-distribution.

Anti-anchoring: **do not** include phrases like "prefer 0", "loop saturated", "healthy signal" in the audit prompt body. These bias codex toward terminating instead of digging. Use the mechanical thresholds in `prompts/audit.md` as the only stop criteria.

After validation: read `audit-iter-N.md`; the controller projects each accepted audit cluster into
the WorkUnitV1 fields documented in `REFERENCE.md` (`work_unit_id`, `kind`, `producer`,
`source_ref`, and v1 audit compatibility aliases), populate `clusters_planned`, split into batches
(max `max_parallel_clusters` per batch) by
**file/project disjointness**:

- Current audit-backed units set `work_unit_id == id == cluster_id == legacy_cluster_id`,
  `kind="audit-cluster"`, `producer="audit"`, and `source_ref="audit-iter-N.md#<cluster-id>"`.
- Preserve existing cluster fields for audit section lookup, markers, artifact filenames, branch
  names, and GitHub issue routing during v1 compatibility.
- Stable v1 operational tokens are public routing tokens, not names to migrate in this contract:
  `[refactor-design]`, `refactor-design-needed`, `auto-loop`, `phase9-auto-solve`,
  `auto-loop-resume`, `refactor/iterN-<cluster-id>`, `.refactor-loop/.../<cluster-id>`,
  `IMPLEMENT_DONE:${CLUSTER_ID}`, `VERIFY_DONE:${CLUSTER_ID}`, `SOLVER_DONE`, and
  `META_JUDGE_DONE`.
- Do not rename, dual-write, alias, or replace those tokens with `work-unit-*` forms; do not add
  a named operational-policy abstraction for v1 compatibility.
- Phase 7 `manual-issue` intake may write WorkUnitV1 items into `clusters_planned` only after the
  accepted GitHub issue has been reshaped with `kind="manual-work-unit"`,
  `producer="manual-issue"`, `source_ref="gh-issue-<N>"`, `scope_paths`, problem/invariant text,
  and `verification_hints`. It must not fabricate `cluster_id` or `legacy_cluster_id`.

- Two clusters that touch the same `$BUILD_CMD 目标/工程文件` or share a file path go in different batches.
- Two clusters that touch the same proto file → different batches.

### requires_design clusters → open GitHub issue, do NOT auto-implement

For every cluster with `requires_design: true`:

1. Open a GitHub issue via `gh issue create`:
   ```bash
   gh issue create \
     --title "[refactor-design] <cluster-id>: <one-line problem from audit>" \
     --label "refactor-design-needed,auto-loop" \
     --body "$(envsubst < <skill-root>/prompts/design-issue-body.md)"
   ```
   The body template at `prompts/design-issue-body.md` includes: the cluster's YAML block from audit, full evidence section, the audit's `Fix boundary` paragraph, and an explicit "decision needed" checklist (proto schema? new contract? backward-compat strategy? whether to split into multiple PRs?).
2. Record in state.json:
   ```json
   "design_pending": [
     {"work_unit_id": "cluster-NNN", "cluster_id": "cluster-NNN", "issue_number": 234,
      "opened_at": "<ISO8601>", "last_checked": "<ISO8601>",
      "last_comment_count": 0, "status": "awaiting_design"}
   ]
   ```
   `design_pending.work_unit_id` is canonical. `design_pending.cluster_id` remains a legacy alias
   for current audit-backed issues and existing controller routing.
3. Skip the cluster in Phase 2 (do NOT batch it).
4. PushNotification: "iter<N> opened design issue #<issue> for cluster-<id>. Auto-loop paused on this cluster pending human design decision."

Update state, advance to Phase 2 (with requires_design clusters excluded).

### Stale-worktree audit pollution(强制 pre-audit cleanup)

**Bug 来源**:audit codex 默认在 `--cd $REPO_ROOT` 下扫描,但 `find` / `rg` 会无视 git boundary 扫到 inside worktrees(`$REPO_ROOT/.worktrees/iterN-cluster-*` 等)。已 merge 但未清理的 worktree 里仍保留 pre-refactor src 文件,audit 把那些当成"现状"出 evidence,导致 cluster 描述指向 main 中**已删除**的文件路径(file:line 在 main 不存在)。

结构性教训:曾出现 audit 从已合并但未清理的 worktree 读取过期 evidence,导致 cluster 指向 main 中已删除的 file:line。pre-audit 必须清理 stale worktree,并抽查 evidence file:line 在当前 main 真实存在。

**强制 pre-audit 步骤**(每次派 audit codex 前 controller 执行):

```bash
# 1. List worktrees,标记 main + active 之外的 stale
git worktree list

# 2. 对每个非 main / 非 active(active = in-flight cluster impl 用的)worktree:
#    - 若对应 PR 已 merged → 删
#    - 若对应 PR 已 closed(superseded / drop)→ 删
#    - 若对应 branch 已不在 origin → 删
git worktree remove <stale-wt> --force
git worktree prune
git branch -D <stale-branch>  # 同 step 一起清

# 3. 验收:`git worktree list` 只剩 main + dev-sync + 当前 in-flight cluster wt
```

**反面禁止**:
- ❌ 派 audit codex 前不 clean worktrees → bogus evidence + 浪费 5400s codex 时间
- ❌ 见 audit-iter-N 的 cluster 直接 trust → 必须 controller 抽查 3 个 evidence file:line 真存在(且不在 stale wt)
- ❌ "可能下次还要用" → worktree 是 disposable;branch 在 git history,需要时 `git worktree add -b <new-branch> <path> <commit>` 重建

如果发现 audit 输出含 stale-worktree evidence(典型征兆:file path 在 main `git ls-files` 中找不到):
1. archive 该 audit md/ndjson 加 `.STALE-WORKTREES.md` 后缀
2. clean worktrees(per 上)
3. 重派 audit(同 prompt)

---

## Phase 2 — Implement (parallel codexes, one per cluster in current batch)

For each cluster in the current batch:

1. Create worktree:

   ```bash
   mkdir -p "$REPO_ROOT/.worktrees"
   git worktree add -b refactor/iterN-<cluster-id> \
     "$REPO_ROOT/.worktrees/iterN-<cluster-id>" HEAD
   ```

2. Materialize prompt: copy `prompts/implement.md`, replace placeholders (`{{work_unit_id}}`,
   `{{cluster_id}}`, `{{worktree_path}}`, `{{branch}}`, `{{old_pattern}}`, `{{new_principle}}`,
   `{{scope_paths}}`, `{{verification_hints}}`). For current audit-backed units, export
   `WORK_UNIT_ID=$CLUSTER_ID` before `envsubst` / placeholder replacement. Save to
   `.refactor-loop/prompts/implement-<cluster-id>.md`.

3. Dispatch via `spawn-codex.sh --cd <worktree>` with `--stall 5400` (5400s no-output stall window).

4. Update `clusters_active` with the WorkUnitV1 identity/provenance fields plus `bg_task` id.

After all parallel dispatches, schedule wakeup 1800s safety net. **End turn.**

When each task notification fires → check log tail for `IMPLEMENT_DONE:<cluster-id>:<status>`:
- `ok` → advance that cluster to Phase 3 (verify).
- `partial` / `blocked` → move to `clusters_failed`, log reason, optionally re-dispatch with corrected prompt.

Do **not** advance the whole batch in lockstep; verify each cluster independently as soon as its implement finishes.

---

## Phase 3 — Verify (one codex per cluster, independent of implement codex)

For each cluster whose implement finished `ok`:

1. Materialize `prompts/verify.md` → `.refactor-loop/prompts/verify-<cluster-id>.md`. For current
   audit-backed units, export `WORK_UNIT_ID=$CLUSTER_ID`; `WORK_UNIT_ID` is the canonical prompt
   identity, while `CLUSTER_ID` remains the v1 compatibility alias for markers and artifacts.
2. Dispatch in the same worktree (verify reads `git diff HEAD`, runs full test/guard suite, gates merge):

   ```bash
   <skill-root>/scripts/spawn-codex.sh \
     --cd <worktree> \
     --prompt .refactor-loop/prompts/verify-<cluster-id>.md \
     --log .refactor-loop/logs/verify-<cluster-id>.log \
     --stall 3600
   ```

3. End turn after dispatching all ready verifies. Wait for task notifications.

Verify output marker: `VERIFY_DONE:<cluster-id>:<verdict>` where verdict ∈ `{pass, rework, abort}`.

- `pass` → advance to Phase 4 (merge).
- `rework` → re-dispatch implement codex with verifier's findings appended.
- `abort` → move to `clusters_failed`, surface in PushNotification.

---

## Phase 4 — Merge & Push (controller, not codex)

<a id="merge-and-push-details"></a>
### Post-merge trunk build verify(强制)

两个 PR 单独 merge OK,**顺序 merge 后 trunk 可能 build 挂**(API 重命名 + 第二 PR 引用旧名)。merge 后必须:

```bash
cd $REPO_ROOT
git pull --ff-only origin auto-refact-dev
$BUILD_CMD
```

若 trunk build 错 → 立即派 **hotfix codex**(直接 push 到 auto-refact-dev,不开 PR):
- 在 `该项目-wt-hotfix-trunk` worktree 跑 codex 修
- 用 `.refactor-loop/prompts/hotfix-trunk-*.md` 模板(参考 iterN hotfix 模板)
- IMPLEMENT_DONE marker + controller commit/push 到 auto-refact-dev 直接

结构性教训:两个独立 PR 各自 CI 绿仍可能在顺序 merge 后引入 trunk build break,典型原因是一个 PR 重命名 API、另一个 PR 仍引用旧名。每次 merge 后必须在 trunk 重新跑 `$BUILD_CMD`,失败则立即派 hotfix codex。

**cwd discipline (critical)**: `git merge`, `git push`, and `gh pr create` MUST run from `$REPO_ROOT`, never from a worktree directory. Cwd persists across Bash invocations in the harness, so chained commands that include `cd "$REPO_ROOT/.worktrees/<id>"` leak cwd into the next call. Always either start the trunk-side command with `cd "$REPO_ROOT" && …` or run it in a separate Bash invocation after the worktree-scoped commit. If you see `Already up to date.` after a merge, that is the signature of cwd leak — diagnose and redo from `$REPO_ROOT`.

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

3. **Push cluster branch**: `cd $REPO_ROOT && git push origin refactor/iterN-<cluster-id>`.

4. **Branch off** by `pr_mode`:

### Phase 4a — `pr_mode: "single"`

5a. Merge cluster branch into `integration_branch`:
    ```bash
    cd "$REPO_ROOT" && git merge --no-ff refactor/iterN-<cluster-id> \
      -m "Merge cluster-<id>: <short title>"
    ```
6a. Re-run local CI on integration_branch (catches inter-cluster interaction).
7a. `git push origin <integration_branch>`.
8a. Goto Phase 5 (remote CI watch).

### Phase 4b — `pr_mode: "stacked"`

5b. **Choose PR base** per the cluster's `dependencies` field from the audit:
    - `dependencies: []` (independent, soft-dep, or batch-disjoint) → base = `integration_branch`.
    - `dependencies: ["cluster-XXX", ...]` (hard-dep — won't compile without the prerequisite) → base = the prerequisite cluster's branch (use the **first**, primary one; document others in PR description).

    **All cluster PRs target the integration branch by default. Never PR directly to `review_base_branch` (dev).** The rollup PR (Phase 4b step 10b, one per iteration) is the only PR that targets `review_base_branch`. Rationale: cluster PRs stay small and reviewer-friendly; the integration branch holds the cumulative refactor state with merge-conflict resolution done once; the rollup PR is the human gate where iter-level rationale (scorecard, cluster ledger, CI guard adds) lives.

    Edge case — if a maintainer accidentally retargets a cluster PR to `review_base_branch`, the next Phase 6 sweep detects the mismatch and posts a comment requesting retarget (does NOT auto-edit, to respect maintainer intent).

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

    See [implement summary](./.refactor-loop/runs/implement-<cluster-id>.md) and [audit](./.refactor-loop/runs/audit-iter-<N>.md#<cluster-anchor>).

    ## Stacked-PR

    Part of iter<N> batch <X>. Base = `<base_branch>`. Rollup target = `<review_base_branch>`.

    🤖 Auto-loop / codex-refactor-loop iter<N>
    ```

    Run via:
    ```bash
    cd "$REPO_ROOT" && \
    gh pr create \
      --base "<base_branch>" \
      --head "refactor/iterN-<cluster-id>" \
      --title "<cluster id>: <short imperative title — same English title; PR title is not bilingual since GitHub UI truncates>" \
      --body-file <generated_body_file>
    ```

    Controller must reject a generated body that reintroduces a parallel `## English` section as a required peer to 中文.

7b. **立刻给 PR 加 `auto-loop` label**:`gh pr edit <PR> --add-label "auto-loop"`。**漏加 → comment-monitor 不监控该 PR 评论 → maintainer 评论无 react 无回复**。漏加是 P0 bug,等同失保。Phase 4b 在 `gh pr create` 成功后立刻 chain 这条 `gh pr edit`,不能延后到下一 turn。

7b. Record the PR number in `state.clusters_active[i].pr_number`.
8b. **Stack rebase on upstream merge**: when an upstream (dependency) cluster's PR merges into `integration_branch`, immediately:
    - For each downstream cluster whose `dependencies` contained it:
      - `git -C <worktree> rebase --onto integration_branch <old_upstream_branch>` (or `gh pr edit <pr> --base integration_branch` if stacked-on-stacked is no longer needed).
      - Re-run local CI in worktree; on conflict, mark cluster `rework` and re-dispatch implement codex with conflict diff.
      - Force-push the cluster branch: `git push --force-with-lease origin refactor/iterN-<cluster-id>`.
9b. Goto Phase 5 (remote CI watch on the cluster's PR).
10b. After **all** iteration clusters have merged into `integration_branch`, Phase 6 may emit `DEV_SYNC_PENDING:release-rollup-needed:<json>`. Controller re-checks exactly one open `$INTEGRATION_BRANCH -> $REVIEW_BASE_BRANCH` rollup PR and, only when none exists, creates it through `open_release_rollup_pr_from_pending_event <event-json> <body-file>`. Daemon only detects/writes the event; PR create, labels, review gate, CI, and merge policy stay controller-owned.
After merge of the cluster branch into its target → `git worktree remove "$REPO_ROOT/.worktrees/<cluster-id>"`. **Do NOT** delete the cluster branch yet under `stacked` mode — downstream PRs may still reference it as base; let GitHub auto-delete on merge.

If no clusters left in current batch → start next batch (Phase 2 again). If no batches left → start next iteration (Phase 1 again) or **start Phase 5 if there is an open PR for the trunk/cluster branches**.

### Phase 4 stack-depth cap

Hard cap: any single dependency stack ≥ 5 PRs deep triggers a controller halt. Reason: rebase blast-radius compounds — reviewer changes to the bottom PR force-rebase the entire stack, and reviewers stop landing PRs that get rebased twice. On cap:
- send PushNotification with the stack contents,
- merge all completed lower PRs into `integration_branch` immediately (collapse stack to a single base),
- continue remaining clusters from the collapsed base.

---

## Phase 5 — Remote CI watch (controller, after push)

<a id="remote-ci-details"></a>
## Remote CI details

Local CI passing is necessary but not sufficient. Remote CI runs additional jobs that don't fit on the controller machine (kafka integration, projection provider e2e, host composition smoke, codecov, etc.). Phase 5 watches them and treats remote failures the same way Phase 3 treats verify failures: dispatch a focused fix codex, loop back through verify/merge.

### When Phase 5 fires

After every push to `<trunk_branch>` that is the head of an open PR. Detect open PR with:

```bash
PR_NUMBER=$(gh pr list --head "<trunk_branch>" --json number --jq '.[0].number')
```

If no open PR → skip Phase 5 (local CI is sufficient).

### Arm the watch

```bash
# Poll every 60s; emit one event per failed check; exit when all checks settled.
prev=""
while true; do
  state=$(gh pr checks "$PR_NUMBER" --json name,bucket,state)
  cur=$(jq -r '.[] | "\(.name)\t\(.bucket)\t\(.state)"' <<<"$state" | sort)
  comm -13 <(printf '%s\n' "$prev") <(printf '%s\n' "$cur") | awk -F'\t' '$2=="fail"{print $0}'
  prev=$cur
  if jq -e 'all(.bucket != "pending")' <<<"$state" >/dev/null; then
    failed=$(jq -r '[.[] | select(.bucket=="fail") | .name] | length' <<<"$state")
    echo "REMOTE_CI_DONE:failed=$failed"
    break
  fi
  sleep 60
done
```

Arm as a Monitor with `persistent: true`. Each emitted line is a notification you wake on. Stop only on the `REMOTE_CI_DONE:` line.

### Triage on failure

For each `bucket: fail` check:

1. Fetch the failure logs:
   ```bash
   RUN_URL=$(gh pr checks "$PR_NUMBER" --json name,link --jq '.[] | select(.name=="<check>") | .link')
   RUN_ID=$(basename "$(dirname "$RUN_URL")")  # parse from link
   gh run view "$RUN_ID" --log-failed > .refactor-loop/logs/remote-ci-<check>-<sha>.log 2>&1 || \
     gh run view "$RUN_ID" --log | tail -200 > .refactor-loop/logs/remote-ci-<check>-<sha>.log
   ```

2. Classify:
   - **Flaky / infra-only** (network timeout, registry unreachable, runner OOM that doesn't recur): retry by `gh workflow run` or pushing an empty whitespace commit; document under `clusters_failed` with reason `flaky`.
   - **Real failure tied to merged work**: dispatch a `prompts/remote-ci-fix.md` codex (see template) with the failure log + last 10 cluster commits as input. Treat the resulting fix as a mini-cluster: implement → controller verify (re-run local guards + the specific failing test) → commit → push → Phase 5 again.
   - **Pre-existing failure unrelated to merged work** (failure exists on `dev` base too): document, do not fix in this PR; surface via PushNotification.

3. `codecov/patch` specifically: this measures coverage on **lines added by this PR**, i.e. the refactor's own new/modified production lines. A refactor-induced patch-coverage drop is the loop's own responsibility — the loop just shipped new code without tests, that is exactly what the loop must close before merge. Treat as a **real failure**:
   - Pull the codecov patch detail via API (`https://api.codecov.io/api/v2/github/<owner>/repos/<repo>/pulls/<pr>`) to identify `patch.misses` + `patch.partials` line ranges per file.
   - Cross-reference with the cluster ledger: each uncovered patch line belongs to a known cluster.
   - Dispatch `prompts/test-add.md` codex per cluster with the uncovered file:line list, target threshold (default 80% patch coverage), and "tests must exercise behavior the cluster introduced (e.g., host external client factory typed-client path, head-index cursor compaction trigger, compiled-delegate exception path, projection session lease lifecycle)".
   - Test-add codex output joins the cluster's branch and re-pushes; codecov re-evaluates.
   - **Exception** (info-only ack): if `head_totals.coverage - base_totals.coverage > -0.5%` (i.e. project coverage barely moved) AND the cluster summary explicitly declared deletion-heavy refactor, you may ack the codecov failure with a PushNotification explaining the math; do not silently dismiss.

### Loop control under Phase 5

- Cap remote-ci fix attempts per check at **2**. After 2 attempts on the same check → mark `clusters_failed` reason `remote-ci-stuck`, send PushNotification, stop the loop.
- Phase 5 may overlap with Phase 2 of the next iteration. If a new cluster's local CI passes but remote CI is still failing on a prior commit → push anyway (CI re-runs on each push); the watch picks up the latest checks.

---

## Phase 6 — Integration branch auto-sync with `review_base_branch` (heartbeat)

<a id="daemon-command-bodies"></a>
## Daemon command bodies

Phase 6 is owned by the singleton daemon, not by controller wakeup shell commands. The goal is to keep `integration_branch` continuously up-to-date with `review_base_branch` so cluster PRs base on fresh code and the eventual rollup PR has minimal merge conflicts.

### Phase 6 现在由独立 daemon 自主完成

**`<skill-root>/scripts/dev_sync_daemon.py`** 是独立 daemon,**600s 周期**自主跑 sync,不依赖 controller wakeup:

```bash
nohup bash -c 'source .refactor-loop/host.env && exec python3 <skill-root>/scripts/dev_sync_daemon.py' \
  >> .refactor-loop/logs/dev-sync-daemon.log 2>&1 &
disown
```

Daemon 工作流由 `IntegrationSyncDaemonV1` 命名状态机表达:
1. `FETCH`: fetch origin in the daemon worktree.
2. `CHECK_MERGE`: if a merge is in progress, observe or dispatch exactly one resolver codex. Resolver codexes resolve files and run `git merge --continue`; they never push, reset, or abort.
3. `CHECK_DIRTY`: dirty non-merge worktrees skip without reset.
4. `PRESERVE_LOCAL_AHEAD`: before any reset, compute `local_ahead_count` with `git rev-list --count origin/$INTEGRATION_BRANCH..HEAD`; if the daemon worktree is clean and ahead, push `HEAD:$INTEGRATION_BRANCH` and return. This preserves resolver continuation commits.
5. `ADOPT_MERGED_ROLLUP`: if a merged rollup PR from `$INTEGRATION_BRANCH` to `$REVIEW_BASE_BRANCH` is provable, capture the old rollup head and current expected remote SHA, reset/replay onto `origin/$REVIEW_BASE_BRANCH`, then push only with exact `--force-with-lease=refs/heads/$INTEGRATION_BRANCH:<expected_remote_sha>`.
6. `RESET_TO_REMOTE`: reset to `origin/$INTEGRATION_BRANCH` only after local-ahead preservation and rollup adoption checks.
7. `FORWARD_SYNC`: merge `origin/$REVIEW_BASE_BRANCH` into integration using ff-only first, then no-ff merge; push with ordinary `git push origin HEAD:$INTEGRATION_BRANCH`.
8. `DETECT_RELEASE_ROLLUP_NEEDED`: if `origin/$INTEGRATION_BRANCH` is ahead of `origin/$REVIEW_BASE_BRANCH` by at least `RELEASE_ROLLUP_MIN_COMMITS` and no open `$INTEGRATION_BRANCH -> $REVIEW_BASE_BRANCH` PR exists, append `DEV_SYNC_PENDING:release-rollup-needed:<json>` with branch names, SHAs, ahead count, timestamp, and reason. Cooldown only suppresses duplicate same-SHA events; it grants no lifecycle authority.
Ambiguous rollup metadata, failed local-ahead push, or adoption conflicts append `.refactor-loop/.controller-pending-events.log` and do not guess. Controller reads pending events and posts the visible GitHub card when action is needed.

### Phase 9 router daemon command body
`phase9_router_daemon.py` 是单例 daemon,只读 clean-exit logs 和私有 ledger。启动:`nohup bash -c 'source .refactor-loop/host.env && exec python3 <skill-root>/scripts/phase9_router_daemon.py --daemon --repo-root "$REPO_ROOT"' >> .refactor-loop/logs/phase9-router-daemon.log 2>&1 & disown`
One-shot:`python3 <skill-root>/scripts/phase9_router_daemon.py --once --repo-root "$REPO_ROOT"`; dry-run:`python3 <skill-root>/scripts/phase9_router_daemon.py --once --dry-run --repo-root "$REPO_ROOT"`; monitor:`tail -50 .refactor-loop/logs/phase9-router-daemon.log`。
Allowlist(唯一 direct spawn authority):
- `SOLVER_DONE:<minimal|structural|delete>:*` x3, same issue/round, clean `^EXIT=0`, non-placeholder, not ledgered, not in-flight → spawn same-round meta-judge.
- `META_JUDGE_DONE:converge:round-<N>:*`, clean exit, not ledgered/in-flight → spawn round-N minimal/structural/delete solvers.
- `META_JUDGE_DONE:escalate:stalled:*`, clean exit + stalled predicate(`round >= 3` and solver verdict text unchanged across 3 rounds) → spawn reflector with the full `prompts/meta-reflector-stalled.md` template plus the 3 recent rounds x 3 solver log path evidence; template read failure must fail closed in the spawned prompt, not fall back to a generic route.
Fallback/ledger/recovery: lifecycle/unknown markers append `.refactor-loop/.controller-pending-events.log`; no spawn beyond the allowlisted worker dispatches, no direct resolution, no git, no GitHub, no label, no lifecycle authority(no close/merge/release). Append-only `.refactor-loop/phase9-router-ledger.jsonl` records `{key, marker, log_path, dispatched_at}`; fallback events use prefix `phase9-router-fallback`. In-flight target logs or live `spawn-codex.sh --log <target>` suppress re-dispatch, `.refactor-loop/phase9-router.lock` enforces singleton, and duplicate ledger rows never delete logs. Staged expansion requires route-ledger evidence and must not introduce ControllerEvent, ControllerCommand, ControllerOrchestrator, WorkUnitV2, public marker aliases, or lifecycle authority.
### Daemon vs controller 分工
dev sync stays with daemon; Phase 9 triplet/converge/valid-stalled continuation may use **phase9_router_daemon.py** narrow allowlist with controller fallback sweep retained; design/consensus/implement/review/fix/liveness/escalation stay with controller wakeups.
### Controller 每 wakeup 责任(只 verify daemon)
```bash
# Phase 6 现在 controller 只读 daemon-maintained health/log surface
bash <skill-root>/scripts/restart-daemons.sh
python3 <skill-root>/scripts/concurrency_monitor.py --count-only >/dev/null
bash <skill-root>/scripts/peek.sh | tail -80
tail -10 .refactor-loop/logs/dev_sync_daemon.log | grep -E "(DEV_SYNC_BLOCKED|FAIL|FATAL)" | tail -3
```
若 heartbeat stale/missing/malformed → 由 `restart-daemons.sh` 按 canonical wrapper 重启。
若发现 `DEV_SYNC_BLOCKED` → controller post 卡片到 rollup PR / 通知 maintainer。若发现 `DEV_SYNC_PENDING:release-rollup-needed:<json>` → controller 重新查 open `$INTEGRATION_BRANCH -> $REVIEW_BASE_BRANCH` PR;已存在则 ledger/suppress,否则生成中文 body 并调用 `open_release_rollup_pr_from_pending_event <event-json> <body-file>`。该 PR 进入既有 Phase 8 review gate 与 CI/merge policy。
### 反面(❌ 禁止)

- ❌ controller 自己跑 `git merge dev` 同步(daemon 已做,会 race / 冲突)
- ❌ daemon push 后 controller 不 fetch 就 commit(stale base bug)
- ❌ Daemon 派 codex 自己 push(daemon 决定 push 时机,codex 只 resolve + merge --continue)
- ❌ controller 用 process probe 判断 daemon 单例;单例与 pid/kill 细节只属于 `restart-daemons.sh` / daemon 自身 helper 实现。

### Manual recovery

If a maintainer must repair the daemon worktree manually, stop the singleton `dev_sync_daemon.py` first, verify there is no resolver codex in flight, then repair the dedicated worktree. Restart the daemon only after the branch topology is clean and `git status` is clean.

### Post-rollup adoption invariant

After a rollup PR has merged into `review_base_branch`, `IntegrationSyncDaemonV1` must make `integration_branch` contain that merged review-base head before new forward sync work. Any post-rollup integration commits are replayed only after the proven old rollup head; if the old head or expected remote SHA cannot be proven, the daemon writes a pending event and does not force-push.

### Why this matters

- Without auto-sync, the integration branch drifts from dev and the eventual rollup PR becomes one giant conflict resolution.
- Cluster PR diffs viewed by reviewers should be just the cluster's changes; if integration is stale, the PR shows a noisy diff that mixes cluster work with "what dev added since" which is reviewer-hostile.
- Sync conflicts are rare but real (e.g., a dev PR refactored the same area). Surfacing them as halts is better than silently posting a busted integration.

---

## Phase 7 — Design-issue watch (sweep on every wakeup)

<a id="design-issue-details"></a>
## Design issue details

Runs **after Phase 6 sync** and **before** any new Phase 2 / 3 / 4 / 5 cluster work on every controller wakeup (whether triggered by user `/loop`, ScheduleWakeup, or task-notification). Goal: detect when a paused-for-design cluster has a maintainer response and resume it.

### 外部 issue 接入(强制)

**问题**:audit codex 自动产生的 design issue 走完 Phase 9 链路;但 maintainer 或其他人手动开的 issue(无 `auto-loop` label)不接入,controller 看不见。

**两条 onboarding path**:

#### Path A — 手动 label opt-in(已现成支持)

maintainer 在外部 issue 上加 **4 label**:`auto-loop` + `phase9-auto-solve` + `🔍 phase:design-solving` + `🤖 human:auto-推进`

Controller 下次 wakeup sweep `gh issue list --label "auto-loop,phase9-auto-solve" --state open`,把它当 Phase 9 candidate,直接派 r1 三 solver + meta-judge。Solver prompt 自包含,会读 issue body 全文 + grep 相关代码自找 evidence。

**前提**:issue body 至少要描述 "what's broken + relevant file paths"。Body 越结构化(evidence / fix boundary / decision questions)solver 越准。

#### Path B — Triage codex / `manual-issue` producer(推荐,更安全)

maintainer 只加 1 label:`auto-loop-triage`

This path is the v1 `manual-issue` producer. The triage codex accepts only concrete repository
work units suitable for consensus, reshapes the issue into a WorkUnitV1-backed design issue, and
then label-routes it to Phase 9. Accepted manual issues must contain `work_unit_id: issue-<N>`,
`kind: manual-work-unit`, `producer: manual-issue`, `source_ref: gh-issue-<N>`, `scope_paths`,
problem/invariant text, and `verification_hints`; they must not include fabricated `cluster_id` or
`legacy_cluster_id`.

**Daemon 自包含**:

`<skill-root>/scripts/triage-monitor.sh` 60s 周期:
- 扫 `gh issue list --label "auto-loop-triage" --state open`
- 新 issue → mark seen + **直接 spawn triage codex**(nohup + disown,daemon 自己派)
- triage codex 自己读 issue body + update GitHub(reshape or 评论 + label 切换)
- daemon 不依赖 controller 中转,无中间 event log
- state 存 `.refactor-loop/triage-monitor-state.json` 防重复
- 启动:`nohup bash -c 'source .refactor-loop/host.env && exec bash <skill-root>/scripts/triage-monitor.sh' >> .refactor-loop/logs/triage-monitor.log 2>&1 & disown`
- Liveness:每 wakeup 读 `.refactor-loop/heartbeats/triage-monitor.ts` / statusline snapshot;stale/missing/malformed 时调 `bash <skill-root>/scripts/restart-daemons.sh`
- Codex 完成 marker:`TRIAGE_DONE:<issue>:<accept|reject>:<reason>`(写 issue 评论 + 切 label)
- Controller 下次 wakeup 从 GitHub state derive(issue label 改了即看见)

结构性教训:triage daemon 只 emit event 等 controller 中转时,一旦 controller 未 sweep pending-events 就会漏处理外部 issue。修法是 daemon 直接 spawn triage codex,移除中转环节;daemon 自己 take action,controller 从 GitHub state 派生结果。

Controller 每 wakeup sweep `--label "auto-loop-triage"`(daemon 漏了兜底),对每个新 issue:
1. 派 **triage codex**(`prompts/triage-external-issue.md`)读 issue body + 判断:
   - 是否是 concrete repository work unit suitable for consensus?
   - 若是 → 调研代码 + 补 evidence / Fix Boundary / human_brief / decision questions + 重写 issue body 成含 `manual-issue` WorkUnitV1 字段的 standardized design issue 格式 + label 切换为 `auto-loop,phase9-auto-solve,🔍 phase:design-solving,🤖 human:auto-推进`(移除 `auto-loop-triage`)
   - 若否 → 评论"不适合作为 manual-issue work unit(原因 XXX),退出 auto-loop";移除 `auto-loop-triage` label;不再处理
2. Triage codex 完成后 issue 进 Phase 9 标准链路

**triage codex 输出 marker**:`TRIAGE_DONE:<issue>:<accept|reject>:<reason>`

**优势 vs Path A**:
- maintainer 只加 1 label(易记)
- body reshaping 由 codex 自动做(maintainer 不用学 design-issue body 模板)
- 不适合 consensus 的 issue 会被自动拒绝(防 controller 把任意 issue 当 work unit 跑)
- triage codex 调研代码补 evidence,solver 后续准

### 反面(❌ 禁止)

- ❌ controller 无 sweep `auto-loop-triage` label → 外部 issue 加 label 也无人接
- ❌ Path B triage codex 直接派 solver 而不 reshape body → solver 找不到 evidence
- ❌ triage codex 接受产品需求 / runtime bug report / duplicate / unclear / >50 files issue → Phase 9 完全错位
- ❌ 加 `auto-loop` label 但忘加 `phase9-auto-solve` → controller 当普通 design issue 等 maintainer,不自动派 solver

<a id="phase-8-details"></a>
## Phase 8 details

## Phase 8 — Multi-codex PR review with consensus merge

<!-- Refactor (iter3/skill-merge-policy): Old pattern: unanimous-approve merge gate + Phase 8 文案矛盾  New principle: 固定真值表 reject=0 && approve>=1 → MERGE;comment 是 advisory(#26 minimal option B 共识) -->

Runs when a cluster PR's remote CI is green (Phase 5 settled with pass) and the PR is mergeable. Goal: 3 (or more) independent codex reviewers from **different angles** verify the PR at the current head SHA; the controller then chooses exactly one action from `MERGE`, `MERGE_WITH_COMMENTS`, `WAIT_EXPLICIT_APPROVAL`, `FIX`, or `WAIT_OR_REDISPATCH`.

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
  <skill-root>/scripts/spawn-codex.sh \
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
| CI green, PR mergeable, reviewed head SHA current, every required role has exactly one valid marker after `EXIT=0` | `reject=0`, `approve=R`, `comment=0` | `MERGE`: post 中文 merge comment, then call `merge_pr <pr>`. |
| Same preconditions | `reject=0`, `approve>=1`, `comment>=1`, `approve+comment=R` | `MERGE_WITH_COMMENTS`: surface comment evidence, post 中文 merge comment, then call `merge_pr <pr>`. |
| Same preconditions | `reject=0`, `approve=0`, `comment=R` | `WAIT_EXPLICIT_APPROVAL`: surface comments, do not merge, do not dispatch fix. |
| Same preconditions | `reject>=1` | `FIX`: enter fix-retry loop; fix codex consumes reject evidence as blocking input and comments as context. Do NOT escalate to human on first reject. |
| Any gate incomplete or invalid | missing role, duplicate/unknown verdict, no `EXIT=0`, stale head SHA, CI pending/fail, or non-mergeable PR | `WAIT_OR_REDISPATCH`: wait or re-dispatch invalid/missing reviewer once; never merge. |

### Fix-retry loop (AI iterates until consensus)

Policy: AI keeps iterating until the fixed Phase 8 truth table resolves to `MERGE` or `MERGE_WITH_COMMENTS`, OR until escalation criteria are hit. Default `max_fix_rounds = 3` per PR。

Loop:

1. **Round entry** — `state.pr_reviews[PR].fix_round += 1`. If `fix_round > max_fix_rounds`, escalate (see below).
2. **Dispatch fix codex** in PR's own worktree:
   ```bash
   <skill-root>/scripts/spawn-codex.sh \
     --cd "$PR_WORKTREE" --add-dir "$REPO_ROOT" \
     --prompt .refactor-loop/prompts/fixes/fix-pr${PR}-round-${N}.md \
     --log .refactor-loop/logs/fix-pr${PR}-round-${N}.log \
     --stall 3600
   ```
   Fix codex reads all 3 reviewer outputs, applies in-scope fixes, validates locally, writes `FIX_REPORT.md`, emits `FIX_DONE:${PR}:round-${N}:applied-<N>:rejected-<M>:blocked-<K>` OR `FIX_BLOCKED:${PR}:round-${N}:<reason>:<short>`.
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
- Add `needs-human-review` label on PR.
- Post 中文 PR comment with: round history (N rounds tried), reject evidence per round, what fix codex tried, why it's stuck.
- `PushNotification`: "PR #N stuck at round N — human decision needed: <one-line reason>".
- State: `pr_reviews[PR].consensus = "stuck-human-review"`.

### Anti-spiral safeguards

- Round-N reviewer outputs MUST be diffed against round-(N-1). If reviewer text didn't change but verdict didn't change either → that reviewer is stuck on a non-addressable demand → escalate.
- Each fix round must reduce total reject count OR change which reviewer rejects. If neither → escalate.
- Cumulative PR diff size grows by ≤ +30% per round; if a fix round adds more code than the original PR → controller flags scope-runaway and escalates.

### GitHub traceability (mandatory — every Phase 8 action posts to the PR)

All review/fix/consensus/escalation behavior MUST be observable on GitHub so the whole loop is traceable without reading local `.refactor-loop/` artifacts. Natural-language GitHub posts follow the skill language rule.

**Hard rule**: all natural-language GitHub posts go through the codex role that produced the artifact, NOT directly composed by controller.

The controller's only inline composition allowed for GitHub:
- Status one-liners (≤ 80 chars, e.g. "labels updated").
- Mechanical link / SHA / cluster id mentions.
- Programmatic label edits + merge actions.

EVERYTHING ELSE(reviewer verdict、fix-done body、consensus 公告、escalation rationale、design issue body、cross-post 通知、PR description 包括 rollup PR)由**正在跑的那个 codex 自己 post**,**不需要专门的 writer-codex 中介**:

<!--
# Refactor (iter3/skill-github-post-contract):
#   Old: 宽泛 all-prompts direct-post 主张
#   New: 两组明确 roster + 可枚举行为测试(#13 structural 共识)
-->

- Direct-post prompts: `solver-minimal.md`, `solver-structural.md`, `solver-delete.md`, `meta-judge.md`, `reviewer-architect.md`, `reviewer-quality.md`, `reviewer-tests.md`, `review-fix.md`, `design-issue-reply.md`, `triage-external-issue.md`. Only these prompts must contain a `## GitHub post` section referencing `prompts/_github-post-rules.md`.
- Marker/artifact-only prompts: `audit.md`, `design-issue-body.md`, `implement.md`, `verify.md`, `remote-ci-fix.md`, `test-add.md`. They keep the AI sentinel plus their marker/body contract, are surfaced by controller / PR / CI status, and must not claim direct posting.
- body 必须 `## 🤖 <headline>` 开头(comment-monitor.sh 据此识别 controller-post 跳 react)
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

| Phase 8 event | PR comment content |
|---|---|
| Reviewer round N complete | 中文 table of 3 verdicts + reject demands per role + "next action" (fix-retry dispatched OR auto-merge OR escalation). Link to commit SHA reviewed. |
| Fix codex round N complete (FIX_DONE) | 中文 FIX_REPORT excerpt: applied / rejected-as-false-positive / blocked counts, build+test status, files changed. Link to fix commit SHA. |
| Fix codex blocked (FIX_BLOCKED) | 中文: which reason category (conflict / human-decision / build-broken), reviewer demand text, controller's escalation decision. |
| Consensus reached (`MERGE` / `MERGE_WITH_COMMENTS`) | 中文: round count, final reviewer outputs, surfaced comment evidence when present, "auto-merging now". Then merge + a second "merged at <commit>" comment. |
| Escalation triggered | Add `needs-human-review` label. Comment includes: full round history, latest verdicts, why escalation criteria hit, what controller tried. PushNotification mirrors the headline. |
| Reviewer crash | 中文: which reviewer, log path, re-dispatch attempt. Second crash → escalate per above. |

Required GitHub labels (controller applies/removes):
- `phase8-reviewing`: a reviewer round is in flight
- `phase8-fixing`: a fix codex round is in flight
- `phase8-consensus-pending`: consensus computation in progress
- `needs-human-review`: escalated
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

If PR is pushed after consensus (rebase, requested change), head SHA changes. Next Phase 8 sweep: if `state.pr_reviews[PR].head_sha != current head SHA` → drop prior consensus, re-dispatch all reviewers against new head. Never auto-merge stale consensus.

### Idempotency

Skip a PR in Phase 8 if any of:
- already merged / closed
- `needs-human-review` label present (operator handling)
- consensus recorded for current head SHA AND not stale

### Why three angles, not one

A single reviewer codex would weigh all dimensions and might trade tests for architecture or vice versa. Three independent codexes with bounded scopes are harder to convince than one — a real defect tends to hit one role hard rather than all three lightly. Consensus across orthogonal angles is the actual signal.

---

<a id="phase-9-details"></a>
## Phase 9 details

## Phase 9 — Multi-solver design consensus (sole authorization gate)

Runs when a `state.design_pending[i]` WorkUnitV1 item needs a concrete implementation decision.
Current audit-backed items expose `WORK_UNIT_ID=$CLUSTER_ID` so Phase 9 can frame the decision as
work-unit design while preserving `cluster_id` as legacy routing metadata. Goal: 3 independent
solver codexes propose framings from different biases; a 4th meta-judge codex arbitrates; **3/3
unanimous + meta-judge consensus → auto-dispatch implement**. Deep consensus is the only sufficient
authorization gate for every change, including Tier I, Tier II, `CLAUDE.md`, `SPEC.md`,
conformance, and core abstractions. There is no post-consensus maintainer approval, physical GPG
ratification, reinstall ratification, or philosophy escalation gate.

Policy: **3/3 unanimous required** — anything less goes through convergence until consensus or true stall.

### Default solver roles

| Solver | Bias | Prompt |
|---|---|---|
| **minimal** | smallest viable change; documented rule exception OK if scope is genuinely narrow | `prompts/solver-minimal.md` |
| **structural** | CLAUDE-philosophy-aligned; new abstraction allowed if justified; never proposes rule exception | `prompts/solver-structural.md` |
| **delete** | question necessity; propose delete / defer / collapse-and-redirect; abstain if feature genuinely needed | `prompts/solver-delete.md` |

A 4th **meta-judge** codex arbitrates (`prompts/meta-judge.md`).

### Dispatch (parallel)

For each cluster needing Phase 9:

```bash
for role in minimal structural delete; do
  envsubst < <skill-root>/prompts/solver-${role}.md \
    > .refactor-loop/prompts/phase9/solve-issue${ISSUE_NUMBER}-r${ROUND}-${role}.md
  <skill-root>/scripts/spawn-codex.sh \
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
<skill-root>/scripts/spawn-codex.sh \
  --cd "$REPO_ROOT" \
  --prompt .refactor-loop/prompts/phase9/judge-issue${ISSUE_NUMBER}-r${ROUND}.md \
  --log .refactor-loop/logs/phase9-issue${ISSUE_NUMBER}-r${ROUND}-judge.log \
  --stall 3600
```

This triplet dispatch is now the first Phase 9 daemon-first route: `phase9_router_daemon.py` may do it directly after clean-exit gating, placeholder exclusion, ledger de-dupe, and in-flight checks. Controller fallback sweep remains required.

Meta-judge emits `META_JUDGE_DONE:<decision>:<...>`,**controller 路由表(强制)**:

| Decision | Category | Controller 动作 |
|---|---|---|
| `consensus:<framing>:<summary>` | — | auto-applies(派 implement,见 "Consensus action";implement 可改 Tier I/II/CLAUDE.md/SPEC/核心抽象) |
| `converge:round-N:<question>` | — | 派 r-N+1 三 solver(把 convergence question prepend prompt); `phase9_router_daemon.py` may direct-dispatch this route |
| `escalate:stalled:<...>` | `CONVERGENCE_ROUND >= 3` 且 3+ round 无 maintainer input 且 solver verdict 文本连续无变化 | **必须先派 reflector codex**(走完整 stalled reflector template + 9 个 solver log path evidence);no-framing evidence 优先 drop,`re-design` 仅用于 concrete new framing/directive artifact;**禁止**直接 label 人; `phase9_router_daemon.py` may direct-dispatch only when the stalled predicate holds |
| `escalate:<其他 category>` | legacy / judge 异常 | 重派 judge 或派 reflector,要求归一到 `consensus` / `converge` / `escalate:stalled`;**禁止**直接 label 人 |

结构性教训:曾出现多个 `escalate:stalled` 被直接 label 人,**没派 reflector**。原因是路由只写了"escalate → label",没有明确 `stalled` 子类必须 reflector 优先。上表 `escalate:stalled` 行强制 reflector。

**stalled 判据铁律**:`stalled` 只能在 `CONVERGENCE_ROUND >= 3` 且 solver verdict 文本连续无变化时成立。round 1 / round 2 不可能 stalled;此时 solver 分歧应判 `converge` 并继续派下一轮,不能接受 meta-judge 在 r1/r2 输出的 `escalate:stalled` 作为事实。若 r1/r2 judge 输出 `escalate:stalled`,controller 必须按 judge 异常处理:重派 judge(同输入,提示 stalled 最小轮次约束),而不是派 reflector 或 label 人。

**反面(❌ 严禁)**:
- ❌ r1 三 solver 分歧,meta-judge 输出 `escalate:stalled`,controller 直接派 reflector。
- ❌ r2 verdict 变化但未 unanimous,controller 以"看起来卡了"自判 stalled。

reflector spawn 模板见 "Meta-layer escalation" 节。reflector 输出 `META_RESOLVED:<kind>:<reason>` 后 controller 再按 retry-fix / re-design / re-cluster / drop / escalate-human 路由。**只有** reflector 显式输出 `META_RESOLVED:escalate-human:<reason>` 时,controller 才允许 label `👤 human:需-maintainer-决策` 并写 reason banner;这只用于"共识机制本身无法收敛",非"触及 Tier/哲学/签名"。

### Maintainer-directive artifact precedence

When reviewer evidence conflicts with maintainer prior session directive, encode the directive as `.refactor-loop/runs/maintainer-directives/<date>-<topic>.md` before considering any human label. A maintainer-directive artifact is the durable replacement for verbal authorization and has precedence over reviewer uncertainty about authorization.

If architect or quality rejects because the PR "needs Phase 9 artifact", do not apply `👤 human:需-maintainer-决策`. Open a real Phase 9 path. If maintainer already authorized that topic in-session, encode or reuse the maintainer-directive artifact and reframe Phase 9 with that directive as evidence. This is the Phase 9-artifact replacement path; the label is not an interchange format for architect/quality reject.

Controller label application must use `apply_human_label_or_skip <pr-number> <source-marker> <reason-or-topic>` from `controller_lib.sh`, with the full `META_RESOLVED:escalate-human:<reason>` marker as `<source-marker>`. `META_JUDGE_DONE:*` and `FIX_BLOCKED:*` must route through reflector/meta-layer and must not call the helper. If the helper finds a matching `.refactor-loop/runs/maintainer-directives/<date>-<topic>.md`, it prints `skip-label: maintainer-directive 已覆盖,见 .refactor-loop/runs/maintainer-directives/` and leaves the item automatic.

### Historical anti-pattern:`👤 human:需-maintainer-决策` 误用 (2026-05-26)

PR #47/#48/#50/#52 因 architect codex 严格读 CLAUDE.md reject,reflector 选 option C 误以 label 绕路。实际 maintainer 已多次 session 内 verbal 授权。Fix:开真 Phase 9(issue #54),encode maintainer-directive artifact 作 Phase 9-等价。从此 label 严语义 + helper 守护。

### Reflector 完成 → 立即回到共识阶段(强制)
<!--
# Refactor (iter3/skill-human-label-taxonomy):
#   Old: 四个 Human label(含两个 🆘),no-gap/escalation 判定散落
#   New principle: 恰好两个 active Human label;causes 移到 reason surface(#15 structural 共识)
-->

**关键 bug**:之前 `escalate:stalled` 触发后挂 `auto-loop-stuck` + `👤 human:需-maintainer-决策` label,**reflector 完成后没清掉**,导致 issue 视觉上仍卡在"等人"状态;controller sweep 时也会看到 stuck label 误以为不需处理。

**修复**:reflector 完成(任何 `META_RESOLVED:<kind>` 除 `escalate-human` 外)后,controller **必须立即**执行 label transition:

```bash
gh issue edit <N> \
  --remove-label "auto-loop-stuck" \
  --remove-label "👤 human:需-maintainer-决策" \
  --remove-label "🆘 human:卡死" \
  --remove-label "🆘 human:卡死-需-rework" \
  --add-label "🔍 phase:design-solving" \
  --add-label "🤖 human:auto-推进"
```

然后按 `META_RESOLVED:<kind>` 路由立刻做下一步(派 fresh 3 solver 轮 / 关 issue / re-cluster);**不允许**停在"reflector done but stuck label still on"暧昧态。整个系统核心是多角色多角度共识——reflector 是中介调和角色,完成后必须把控制权交回 solver 共识循环。

唯一例外:`META_RESOLVED:escalate-human` → 保留 / 加 `👤 human:需-maintainer-决策` label 并写明 reason/banner,这才是真正 human 介入态;它必须说明为什么 3 solver + meta-judge + reflector 的共识机制无法继续收敛。

### Daemon → controller event channel + 自适应 wakeup(强制, 关于 daemon detect → controller 25 min gap 问题)

**问题**:`comment-monitor.sh` 30s 周期 detect maintainer 评论 ✓ + eyes react ✓ + daemon banner ✓,但若没有 mounted Monitor bridge,controller wakeup 默认可能只靠 1500s(25 min) ScheduleWakeup,maintainer 评论到 controller 派 fresh round 最长 25 min gap — maintainer 看 daemon banner 后等 controller。
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

每次 controller wakeup 第一动作之后(per-wakeup sweep step 1 完成后),对每个带 `auto-loop-stuck` OR `👤 human:需-maintainer-决策` label 的 issue:

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
1. controller 把指令记入 `state.design_pending[i].maintainer_directive`
2. 立刻派一轮 fresh 3 solver(把指令 verbatim 作为 narrowing constraint)
3. solver 们各自把指令具体化成 impl 计划(可能 minimal 给一套、structural 给另一套、delete 给第三套)
4. meta-judge 仲裁 → 3/3 unanimous → 才能进 implement
5. 不允许跳过 3 solver round 直接 implement(哪怕 maintainer 觉得方向很明显)

理由:maintainer 直觉常常对,但 concrete 落地的细节(新 actor 边界 / proto 字段 / 命名 / 迁移路径)需要 3 个独立角度验证,避免单 codex 把 "明显方向" 误读成 "明显方案"。consensus 这步就是 catch 误读用的。

### Maintainer-reply-resets-the-round (mandatory)

Policy:任何 maintainer 新回复都让本轮推理失效,必须重新派 3 solver 并重新达成共识。

When the auto-discover Monitor fires `design-issue-event:<N>` and the new comment is from a verified team member (per Phase 7 security gate) AND is substantive (>30 chars / contains technical content / not a controller self-reply):

1. **TaskStop any in-flight Phase 9 codex for that issue** (solvers OR meta-judge) — old reasoning is stale once new constraint lands.
2. **Treat the new comment as fresh constraint material** — prepend its verbatim text to a NEW round's solver prompt header under "Maintainer comment (must incorporate)".
3. **Dispatch FRESH 3 solver codex** (not "continue convergence"; truly fresh, with all prior rounds as context but no inherited stance).
4. **No round counter penalty** — maintainer input is the loop's continuation signal, not a stop signal. The round counter increments but does NOT trip the escalation cap.
5. **Only 3/3 unanimous + meta-judge consensus** moves the cluster to implement. Maintainer can override at any time by adding `auto-loop-resume` label with their explicit framing in a comment.

This means: even if a previous round escalated with `auto-loop-stuck`, a new maintainer comment re-opens Phase 9. The `auto-loop-stuck` label is removed automatically on reset; `phase9-converging` is re-applied.

### Consensus action (3/3 unanimous + meta-judge consensus)

1. Read the winning solver's "Concrete plan" section from the meta-judge output.
2. Materialize `prompts/implement-<cluster-id>.md` prepending:
   ```markdown
   ## Design decision (from Phase 9 consensus, issue #${ISSUE_NUMBER})
   <winning solver's framing verbatim>
   <winning solver's concrete plan verbatim>
   ```
3. Add `auto-loop-resume` label to the issue (mirrors maintainer-decision flow).
4. Move cluster from `design_pending` to `clusters_active`.
5. Dispatch implement codex per Phase 2 (worktree + 5400s no-output stall window).
6. **Post 共识卡片**(强制)— 不再用普通 status banner,改用 distinct **consensus card** 格式:

```markdown
## ✅ 共识卡片 — Phase 9 r${ROUND} consensus reached

| 维度 | 值 |
|---|---|
| Issue | #${ISSUE_NUMBER} ${TITLE} |
| Cluster | ${CLUSTER_ID} |
| Round | r${ROUND}(共识达成,3/3 unanimous) |
| 选定 framing | **${FRAMING}**(minimal / structural / delete 中的一个) |
| Solver 投票 | minimal: <verdict>:<summary> · structural: <verdict>:<summary> · delete: <verdict>:<summary> |
| Meta-judge 仲裁 | ${JUDGE_VERBATIM_REASON} |
| Concrete plan 摘要 | <3-5 bullet,来自 winning solver "Concrete plan" 头几条> |
| 下一步自动会做 | 1. 创 worktree + branch  2. 派 implement codex(5400s no-output stall window)  3. implement done 后 open PR + Phase 8 reviewer  4. PR merge 后 close 本 issue |
| **是否需要人介入** | **❌ 否**(自动推进;maintainer 仍可在本 issue 评论 override) |

📦 implement worktree:\`$REPO_ROOT/.worktrees/iter${ITER}-${CLUSTER_ID}\`
📦 implement branch:\`refactor/iter${ITER}-${CLUSTER_ID}-${SLUG}\`

🤖 controller consensus card

⟦AI:AUTO-LOOP⟧
```

**约束**:
- 共识卡片第一行**必须** `## ✅ 共识卡片 — Phase 9 r${ROUND} consensus reached`(✅ 而非 📊,与普通 status banner 区分)
- 末尾 `🤖 controller consensus card` 标识 + sentinel
- 不在普通 status banner / 进度评论用 ✅ 开头(只共识达成时用)
- 共识卡片是 **一次性 event** post,implement 派出同 turn 内发,不重复

### Consensus scope (no hardcoded human escalation)

These are first-class consensus scope, not escalation triggers. Meta-judge MUST require solvers to include exact file/clause changes when any item appears; once 3/3 consensus is reached, implement proceeds without human ratification:

1. **Top-level CLAUDE.md clause change** — solver proposes editing CLAUDE.md "## 顶级架构约束" / "## 架构哲学" / Phase rules
2. **Tier I/II change** — solver proposes changing supervisor, SPEC, conformance, trusted base lock, GPG policy text, or swap/reinstall policy text
3. **New core abstraction** — solver proposes new actor type, new envelope kind, new pipeline phase, new Layer
4. **`$REPO_ROOT 的架构/词汇文档(若有)` change** — repo architecture vocabulary change
5. **Rule exception that escapes scope** — proposed exception is broader than "this one transient sink"; the exception would apply to multiple code paths
6. **Cross-cluster coupling** — solver's plan requires touching another in-flight cluster's PR
7. **Performance constraint unverifiable** — solver claims latency/memory bound but only prod can verify
8. **Issue body's `human_brief.why_needs_design`** contains: `rule-boundary` / `architecture-change` / `philosophy` / `CLAUDE.md` / `canon-vocabulary`

If the above makes the current framing underspecified, route `converge` with the missing exact text or evidence question. If solvers repeat unchanged text for ≥3 rounds, route `escalate:stalled` to reflector. Do not create `escalate:gpg-ratification` or `escalate:philosophy`.

### GitHub traceability (mandatory per SKILL.md "GitHub traceability" — same standard as Phase 8)

Every Phase 9 action posts a 中文 comment to the issue. **The issue must be a complete audit trail** — solver outputs follow the current language policy; the controller posts each one as a SEPARATE issue comment so reviewers can inspect the 3 perspectives side-by-side. Comments are traceability, not a human approval gate.

| Phase 9 event | Issue comment content |
|---|---|
| Round N solvers dispatched | 中文: "Phase 9 round N — minimal/structural/delete codex in flight. 3/3 unanimous required to auto-implement; otherwise iterate." |
| Maintainer reply detected mid-Phase-9 | 中文: "Halted in-flight round; resetting with maintainer comment as new constraint. New round dispatched. Old round outputs preserved for solver context." |
| **Each individual solver completes** | Post FULL solver output as its own comment. Header: `## 🤖 Phase 9 Solver — \`<role>\` (round N)`. Body = verbatim solver output. One comment per solver, three comments per round. |
| **Meta-judge completes** | Post FULL meta-judge output as its own comment. Header: `## 🤖 Phase 9 Meta-judge — round N verdict: \`<consensus\|converge\|escalate>\``. Body = verbatim judge output. |
| Meta-judge → consensus | Same as above + then a follow-up controller comment: "auto-loop-resume label added; implement codex dispatched" |
| Meta-judge → converge | Same as above + the round-(N+1) "solvers dispatched" comment that includes the convergence question for transparency |
| Meta-judge → escalate:stalled | Same as above + label `auto-loop-stuck` + `## 🤖 Controller next-step` comment saying reflector is being dispatched for a no-progress stall |
| Legacy escalation category emitted | Post meta-judge output + summary "legacy escalation category normalized back into consensus loop"; re-dispatch judge or reflector; do not label human directly |

**Forbidden**: posting a "summary" of solver outputs instead of the FULL outputs. The raw reasoning, evidence, and concrete plans are the audit record; a summary loses too much fidelity. The 3+ comments per round are intentional.

Required labels (additions to Phase 8 set):
- `phase9-solving`: 3 solver codexes in flight
- `phase9-judging`: meta-judge in flight
- `phase9-converging`: convergence round in progress
- (re-used) `auto-loop-resume` on consensus dispatch
- (re-used) `auto-loop-stuck` on escalation

### State tracking

```json
"design_pending": [{
  "cluster_id": "...",
  "issue_number": 684,
  "auto_solve": true,
  "phase9": {
    "rounds": [
      {"round": 1, "solvers": {"minimal": "propose", "structural": "propose", "delete": "abstain"},
       "judge": "converge", "convergence_question": "..."},
      {"round": 2, "solvers": {...}, "judge": "consensus", "chosen_framing": "structural"}
    ],
    "final_decision": "consensus:structural" | "escalate:stalled" | null,
    "implement_dispatched": true | false
  }
}]
```

### Anti-spiral safeguards (no hard round cap — different safeguards instead)

Policy:the loop continues until 3/3 unanimous consensus, true stall reaches reflector, maintainer provides new evidence, or maintainer closes the issue.

- **No `MAX_CONVERGENCE_ROUNDS` cap**. The loop iterates until 3/3 unanimous OR true stall reaches reflector OR maintainer adds new constraints OR maintainer closes issue.
- **Stall detection**: if 3 consecutive rounds with NO maintainer input AND NO change in any solver's verdict text → **trigger meta-layer reflector** (not human escalate;)。Reflector 同样回 4 framing question + 输出 `META_RESOLVED:<kind>` marker;路由:
  - `retry-fix` → 派 r+1 solver,加 "reflector 提示: 你们三 round 没收敛,本轮必须 propose 新 framing 不重复之前"
  - `re-design` → reset Phase 9 round counter,prompt 重写带 reflector 总结的新 framing 角度
  - `re-cluster` → close design issue + audit re-split(下 iter 拆 cluster)
  - `drop` → close design issue with `wontfix`
  - `escalate-human` → `apply_human_label_or_skip` with the full `META_RESOLVED:escalate-human:<reason>` marker for `👤 human:需-maintainer-决策` + reason banner + PushNotification(仅 reflector 也无解;helper skip 时改走 maintainer-directive artifact)
- **Maintainer reply RESETS stall counter** — fresh round dispatched with their comment as constraint; stall counter goes back to 0.
- Solver may not repeat a framing that prior rounds showed to be underspecified without adding new exact text/evidence; doing so counts toward stall detection.
- Cumulative solver runtime across all rounds capped at 12h per issue (raised from 6h to account for maintainer-reset iterations); over → escalate as `stalled:budget-exhausted`.
- Architecture/philosophy/Tier triggers never escalate immediately. They require stricter concrete text in solver plans, then either consensus or stall.

### When to trigger Phase 9 (operator policy)

- **Default ON for design decisions that need a concrete plan**. Operator labels may prioritize work, but Tier/CLAUDE/philosophy scope is not a reason to bypass Phase 9.
- Rationale: Phase 9 is the authorization mechanism. Hard architectural calls require better solver evidence and exact rule text, not a maintainer dialog gate.
- The cluster spec's `requires_design: true` + `human_brief.why_needs_design` content informs solver prompts; philosophy keywords must be incorporated into the consensus question instead of silently no-oping Phase 9.

---

## Loop control

<a id="concurrency-floor-details"></a>
## Concurrency floor details

### This is an INFINITE refactor loop — never idle on "iter done"

Policy. An iteration completing is NEVER a stop signal. The loop's only legitimate stops are:
1. Audit returns 0 candidates (codebase has no flagged violations under current rules) — extremely rare.
2. Every cluster in the current batch failed verify twice — escalate operator.
3. Operator explicitly tells the loop to stop.

**Iteration boundary is automatic**: as soon as iter N's last cluster PR merges into `integration_branch` (NOT after rollup PR human review — rollup runs independently in parallel as a human gate), controller IMMEDIATELY dispatches `Phase 1 audit` for iter N+1. The rollup PR (auto-refact-dev → review_base_branch) is a parallel human-review track, not a serial gate.

Concretely, this means:
- After PR #<pr> (a cluster PR in iterN) merged, controller does NOT wait for PR #<pr> (rollup) review — it immediately dispatches the iterN audit codex.
- iterN implement / verify / Phase 8 review runs in parallel with iterN rollup PR being reviewed.
- If iterN rollup PR gets rejected by human, iterN work stays on auto-refact-dev (which now contains iterN + iterN deltas); we re-do iterN rework on top and ship combined.

### Concurrency floor = `$CODEX_FLOOR` 本仓库 codex(host 可配,默认 5,硬下限 2)(强制)

<!-- Refactor (iter3/skill-concurrency-floor-enforcement):
  Old pattern: concurrency_monitor 有误导性 low-threshold 路径,CODEX_FLOOR 强制职责不清
  New principle: monitor 保持 no-gap-only;删 stale low-threshold 路径;CODEX_FLOOR 补给仅 controller wakeup step 1.5;SKILL 澄清职责(#14 delete 共识)
-->

**问题**:之前 "iteration boundary" 是 merge-driven:等 iter N 最后 cluster PR merge 才派 iter N+1 audit。但 iter N 走到 fix r2/r3 阶段时常常只有 1 codex 在跑(fix codex 单点),其他 phase 都在等。codex 总并发数掉到 1-2,远低于本地资源能撑的并行度。

**floor 取值**:`CODEX_FLOOR` 由 host.env 注入(未设则默认 **5**)。**无论 host 设多少,硬下限 = 2** —— controller 必须**确保始终有 ≥2 个本仓库 codex 并行**(防单线程死等);`CODEX_FLOOR < 2` 一律按 2 处理。小型 host(纯文档 / skills 仓,可派的独立工作少)宜设 `CODEX_FLOOR=2`,大型代码仓可设 5+。**floor 计数只算本仓库 codex**(按 `$REPO_ROOT` 绝对路径 scope,见上「并发 floor … 过计」节;❌ 不要用相对子串,同机多 loop 会过计致本仓库永远补不上)。

**规则**:**活跃(本仓库)codex < `$CODEX_FLOOR` 时主动派额外真实工作填满 floor**,不等当前 phase 完成。floor 是保底,不是 burst 目标;单次派发按真实工作量伸缩,默认补到 `$CODEX_FLOOR`,不要为了"并行更猛"一次性齐发十几个。controller 每次 wakeup 的 step 1.5 仍必须在任何 `ScheduleWakeup` 之前执行;同时 `concurrency_monitor.py` 现在会消费 [dispatch queue protocol](#dispatch-queue-protocol) 中的 queued work 自动补 floor,避免 controller 卡住时只 alert 不派。若 latest controller-validated audit 已是 `AUDIT_DONE:none:0` 且没有真实 work,不得为了 floor 合成普通 audit 或 profile/planner work;写可见 `CONCURRENCY_LOW:no-work-after-audit-none`。

| 活跃本仓库 codex 数 | 动作 |
|---|---|
| `>= $CODEX_FLOOR` | 不抢资源,保持现状 |
| `< $CODEX_FLOOR`(floor 至少为 2) | 立即派 `$CODEX_FLOOR - 当前数` 个新 codex 填满 floor;优先级如下 |

**填 floor 优先级**(从高到低):

1. **Existing dispatch queue** — `.refactor-loop/dispatch-queue/{p0,p1,p2}/*.dispatch.json` remains first; queue schema is unchanged.
2. **Clean actionable marker / maintainer comment / CI red / no-gap** — only log-tail markers after `EXIT=0` count; in-flight codexes are not actionable.
3. **Phase 7 / Phase 9 actionable routes** — manual-issue intake and consensus routes that already have durable issue/comment/marker evidence.
4. **Ordinary audit refill before fixed point** — envsubst next iteration `prompts/audit.md` only when the latest controller-validated audit is not `AUDIT_DONE:none:0`.
5. **Visible low-floor stop** — when the latest controller-validated audit is `AUDIT_DONE:none:0` and no real work exists, write `CONCURRENCY_LOW:no-work-after-audit-none` and stop fabricating floor work.

**反面禁止**:
- ❌ 看到 1 codex 跑就 ScheduleWakeup 等(消极等待)→ 必须先填到 `$CODEX_FLOOR`(至少 2)才允许 ScheduleWakeup
- ❌ "iter N 还没完"作为不派 pre-fixed-point audit 的理由 → audit 与 cluster impl 完全独立,无依赖
- ❌ 重复派同 iter audit(已有 log 还派)→ 检查 `[ ! -f ".refactor-loop/logs/audit-iter-${N}.log" ]`
- ❌ latest controller-validated audit 已是 `AUDIT_DONE:none:0` 仍继续派普通 audit / self-audit / retrospective / profile / planner work 凑 floor → 必须写 `CONCURRENCY_LOW:no-work-after-audit-none`
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

```bash
source .refactor-loop/host.env                              # 取 REPO_ROOT / CODEX_FLOOR
FLOOR=$(( ${CODEX_FLOOR:-5} < 2 ? 2 : ${CODEX_FLOOR:-5} ))   # 硬下限 2
# 只数本仓库 codex:含 spawn-codex.sh 且含本仓库绝对路径 $REPO_ROOT(scope,防同机多 loop 过计)
ACTIVE=$(ps -eo command= | awk -v r="$REPO_ROOT" 'r!="" && /spawn-codex[.]sh/ && index($0,r) && index($0," -c ")==0 { n++ } END { print n+0 }')
NEEDED=$(( FLOOR - ACTIVE ))
[ "$NEEDED" -le 0 ] && return  # floor 已满(本仓库 codex 已 >= FLOOR)

# 按优先级派 NEEDED 个 codex:
# queue -> actionable marker / maintainer comment / CI red / no-gap / Phase 7/9 route
# -> ordinary audit only before latest controller-validated AUDIT_DONE:none:0
# -> CONCURRENCY_LOW:no-work-after-audit-none
```

**反面禁止**:
- ❌ 看到 1 codex 跑就 ScheduleWakeup 等(消极等待)→ 应主动派真实 work 提升并发
- ❌ 多个 audit 同时跑(`ls audit-iter-*.log | head -3` 全 in-flight)→ 资源浪费,重复 evidence
- ❌ "iter N 还没完"作为不派 pre-fixed-point audit 的理由 → audit 与 cluster impl 完全独立,无依赖
- ❌ 重复派同 iter audit(已有 log 还派)→ 检查 `[ ! -f "$NEXT_LOG" ]`
- ❌ 在 latest controller-validated `AUDIT_DONE:none:0` 后伪造普通 audit / producer work → 写 `CONCURRENCY_LOW:no-work-after-audit-none`

结构性教训:曾出现 fix 期间并发只剩 1 个 codex,说明单靠 merge-driven iteration boundary 不足以维持无限循环吞吐。concurrency-driven trigger 是并行优化的必要规则:并发过低时应主动开启真实 work;但 controller-validated `AUDIT_DONE:none:0` 是 ordinary audit fixed point,不能为 floor 伪造 no-op work。

### Sync to remote in time (强制)

Policy:controller must sync with remote promptly before deriving GitHub and branch state.

- After EVERY skill edit that affects controller behavior, `git commit && git push origin auto-refact-dev` IMMEDIATELY — do not batch multiple skill changes for a single push, do not defer to "end of turn".
- After EVERY cluster PR commit (fix codex round output): `git push origin <branch>` IMMEDIATELY — the reviewer / CI / maintainer all need to see latest state, not yesterday's local state.
- Phase 6 sync (auto-refact-dev ← origin/dev) runs FIRST on every controller wakeup; never assume "I just synced" — verify with `git fetch && git rev-list --count`.
- Phase 5 CI watch reads `gh pr checks <PR>` (always remote), never a local cached value.
- Phase 7/8/9 reviewer/judge outputs MUST be posted to GitHub as PR/issue comments within the same controller turn they complete; do not let them sit local-only across multiple turns.

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
| 共识达成(Phase 9 meta-judge `consensus`) | "✅ 共识达成,implement 派出" + chosen framing |
| implement 完成(任何 cluster) | "实施完成,即将开 PR" + LOC delta + 文件清单 |
| PR open | "PR open + reviewer 派出" + PR # + base branch |
| Phase 8 r1 reviewer 完成 | "评审 r1: <N approve / N comment / N reject>" + next step |
| Phase 8 fix 派出 | "fix r<N> 派出,目标修 reject" |
| Phase 8 consensus 达成 | "Phase 8 共识达成,等 CI 绿后 merge" |
| CI 全绿 | "CI 全绿,合并中" |
| CI red | "CI 红,fix codex 派出" |
| merge 完成 | "🎉 已合并到 <branch>" |
| escalation | "🚨 需要人介入: <reason>" + label `auto-loop-stuck` |
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
- **重派**:加 `auto-loop-resume` label,controller 用你评论作 narrowing 派 fresh round
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

**问题**:Phase 8 fix r6 仍 reject,或 CI same-check 6 次仍 fail,**第一反应不是喊 human**,而是**反思上一层是否本身错了**。喊 human 是最后的手段。

**层级**(由小到大):
1. **fix(r1..r6)**:针对 reviewer evidence 直接补丁
2. **Meta-layer reflect**:反思 design / cluster / audit 框定是否本身错位
3. **Phase 9 re-design**:重派 3 solver + meta-judge,prompt 带 "previous design caused 6 round non-converge"
4. **Cluster re-split**:audit 阶段 re-evaluate,把当前 cluster 拆 / 合 / 撤回
5. **Drop / wontfix**:确认任务本身价值不足,关 PR + close issue with wontfix
6. **Human escalation**:`👤 human:需-maintainer-决策` + reason banner + PushNotification(只在 meta-layer 也无法解时)

### 触发 meta-layer 反思

- Phase 8 `fix_round > 3` 仍 reject(所有 reviewer 同一组 / 同一 reviewer 反复 reject)
- CI same-check 失败 6 次(同 test 6 次 fix 仍红)
- Cumulative PR diff size > 原 PR 200%(scope-runaway 信号)
- Reviewer 同一类 evidence(test coverage / dead surface / self-doc)在 3 round 内反复出现 → meta-reflect "为什么 evidence 总是同类"
- **Phase 9 design issue stall**:3 consecutive round 无 maintainer input AND solver verdict text 无变化 → 也走 meta-layer

### 派出 reflector codex

```bash
# 内容(prompt 摘要)
你是 reflector codex,不写代码,只反思。Input:
- 当前 PR diff
- 所有 review round 的 reject evidence(verbatim)
- 当前 Phase 9 共识 / audit cluster 框定
你的任务:回答 4 问 + 给 1 决议:
1. Reviewer 反复 reject 的根本原因是 design 错位 / cluster scope 错位 / audit framing 错位 / 还是仅"reviewer 在做完整审查正常 surfacing 小 gap"?
2. 当前 PR scope 是否爆炸(原 cluster 范围 vs 现 diff)?
3. 当前 design 共识(Phase 9)是否本身有漏洞(reviewer 抓到 design 没考虑的角落)?
4. Audit cluster 框定是否过大 / 过小 / 错混?

决议(选一):
- `META_RESOLVED:retry-fix`: 是 reviewer 正常审查,继续 fix r4+ 仍可收敛(给 reviewer 一个 "approve if r4 仍 narrow valid" 的窗口)
- `META_RESOLVED:re-design`: design 错位,关 PR / 撤回当前 implement,re-Phase 9 with reflector prompt
- `META_RESOLVED:re-cluster`: cluster scope 错位,关 PR + audit 阶段 re-split(拆为 2-3 个小 cluster)
- `META_RESOLVED:drop`: 任务价值不足或代价 > 收益,关 PR + close issue wontfix
- `META_RESOLVED:escalate-human`: meta-layer 也无法解,真的需要 maintainer 决策(reason 必须说明 rework / deadlock / ci-stuck 等原因)

```bash
<skill-root>/scripts/spawn-codex.sh \
  --cd $REPO_ROOT \
  --prompt .refactor-loop/prompts/meta-reflect-pr<N>.md \
  --log .refactor-loop/logs/meta-reflect-pr<N>.log \
  --stall 3600
```

Controller 读 marker 后路由:
- `retry-fix` → 派 fix r4 + 提高 max_fix_rounds 临时到 5(只本 PR)+ 同时 narrow reviewer 关注新 evidence only(不再 surface 旧 evidence)
- `re-design` → 关 PR / 撤回 commits / re-Phase 9 with constraint = reject evidence pattern
- `re-cluster` → 关 PR / audit re-split(产新 cluster 在 next iter)
- `drop` → close PR + close issue with `wontfix` label + 转 phase merged-no-op
- `escalate-human` → `apply_human_label_or_skip` with the full `META_RESOLVED:escalate-human:<reason>` marker for `👤 human:需-maintainer-决策` + reason banner + PushNotification(只 meta-layer 也无路时;helper skip 时改走 maintainer-directive artifact)

### 反面(❌ 禁止)

- ❌ fix r4 直接派出而不 reflect → 可能在错的层级死循环
- ❌ 3 轮卡死直接升 human → 没把 AI 自身的反思能力用足
- ❌ reflector 也写代码 → 它的职责是 question framing,不是 propose fix
- ❌ reflector 决议 `re-design` 但 controller 继续派 fix → 框架失效
- ❌ 临时 `max_fix_rounds = 5` 滥用 → 仅 reflector 明确 `retry-fix` 时允许,且不超过 5

<a id="ci-progress-and-reporting"></a>
## CI progress and reporting

CI sweep contract: every controller wakeup checks open auto-loop PR checks, immediately classifies red checks, dispatches fix/test-add for real or codecov failures, reports pre-existing failures, and routes repeated same-check failures through the meta-layer before human escalation.

## Codex 进展实时上报 — 强制

`codex-progress-reporter.sh` is one of the six required daemons. It edits one progress comment per in-flight codex, includes elapsed time plus log tail, skips old finished logs, deletes the progress comment only when the codex exits cleanly, and uses only log-tail `^EXIT=0` for successful completion detection. Nonzero `EXIT=<n>` is a failed terminal state that remains visible instead of being silently cleaned up.

<a id="label-bootstrap-loops"></a>
## Label bootstrap loops

## Label 系统 — 强制

**问题**:人类在 issue 列表页只看 title + label,banner 评论再清晰也得点进去才看见。Label 是封面信息,必须一眼传达"当前 phase + 是否需要人"。

**规则**:每次 phase transition,**controller** 在 post banner 的同时**必同步** label。每个 issue / PR **恰好**带一组 label:

### Label 组 1 — Phase(任意时刻**恰好一个**)

| Label | 含义 | 触发 |
|---|---|---|
| `🔍 phase:design-solving` | Phase 9 多 solver 跑 | 派 r1/r2 三 solver 后 |
| `✅ phase:consensus-reached` | meta-judge 共识达成 | meta-judge `consensus:...` 后 |
| `🛠️ phase:implementing` | implement codex 跑 | implement dispatch 后 |
| `🚀 phase:pr-open` | PR 已开 | gh pr create 后 |
| `👀 phase:reviewing` | Phase 8 reviewer 跑 | reviewer dispatch 后 |
| `🔧 phase:fixing` | fix codex 跑(reject 后修) | fix dispatch 后 |
| `⚙️ phase:ci-running` | CI watch 中 | push 后 CI 启动 |
| `🎉 phase:merged` | 已 merge | gh pr merge 后(也 close issue) |
| `⏸️ phase:blocked` | blocked-on(等其他 issue) | dependency 链上游未完成 |

### Label 组 2 — Human(任意时刻**恰好一个**)
<!--
# Refactor (iter3/skill-human-label-taxonomy):
#   Old: 四个 Human label(含两个 🆘),no-gap/escalation 判定散落
#   New principle: 恰好两个 active Human label;causes 移到 reason surface(#15 structural 共识)
-->

| Label | 含义 | 触发 |
|---|---|---|
| `🤖 human:auto-推进` | 完全自动,**不需要人介入** | 默认 |
| `👤 human:需-maintainer-决策` | 共识机制无法继续收敛或自动修复耗尽,需要外部输入 | `META_RESOLVED:escalate-human` / rework / ci-stuck / deadlock reason |

### Bootstrap(一次性 - controller 在首次跑 loop 时确保 label 存在)

```bash
# 创建所有 phase label
for l in "🔍 phase:design-solving" "✅ phase:consensus-reached" "🛠️ phase:implementing" \
         "🚀 phase:pr-open" "👀 phase:reviewing" "🔧 phase:fixing" "⚙️ phase:ci-running" \
         "🎉 phase:merged" "⏸️ phase:blocked"; do
  gh label create "$l" --color "5319e7" 2>/dev/null || true
done
# 创建所有 human label
gh label create "🤖 human:auto-推进" --color "0e8a16" 2>/dev/null || true
gh label create "👤 human:需-maintainer-决策" --color "d93f0b" 2>/dev/null || true
```

### 转移时刻代码模板

每次 phase transition,controller 用同一 helper 改 label + post banner:

```bash
# helper(写在脚本里): 移除所有 phase:* label, 加新 phase:* label
set_phase() {
  local issue=$1 new_phase=$2
  # 先删所有 phase:* / human:* label 再加新
  current=$(gh issue view "$issue" --json labels --jq '.labels[].name' | grep -E '^(🔍|✅|🛠️|🚀|👀|🔧|⚙️|🎉|⏸️) phase:')
  for old in $current; do gh issue edit "$issue" --remove-label "$old" 2>/dev/null; done
  gh issue edit "$issue" --add-label "$new_phase"
}
set_human() {
  local issue=$1 new_human=$2
  current=$(gh issue view "$issue" --json labels --jq '.labels[].name' | grep -E '^(🤖|👤|🆘) human:')
  for old in $current; do gh issue edit "$issue" --remove-label "$old" 2>/dev/null; done
  gh issue edit "$issue" --add-label "$new_human"
}
```

PR 同理(`gh pr edit` instead of `gh issue edit`)。

### 硬约束

- **Label 与 banner 同步发**:不允许 label 转移但不发 banner,或发 banner 但 label 没改。
- **同一组只允许一个**:不能同时有 `🛠️ phase:implementing` 和 `🚀 phase:pr-open`(实施完成 → 立刻改 pr-open)。
- **`👤` 出现 = 共识机制停滞或自动修复耗尽**:其他 active human label(`🤖`) = 完全自动。`🆘 human:` 只允许作为 legacy cleanup target。
- **escalation 不等于人工授权 gate**:Phase 9 只有 `escalate:stalled` → reflector;只有 `META_RESOLVED:escalate-human` 才配 `👤`。

### 反面(❌ 禁止)

- ❌ label 不更新就发 banner → 列表页看到的还是旧 phase
- ❌ 同时挂多个 phase label → 人类困惑
- ❌ 用纯文字 label(无 emoji)→ 列表页一眼看不出 phase / human 类别
- ❌ blocked-on 不打 `⏸️ phase:blocked` → 人类以为还在主动跑
- ❌ **PR 不加 `auto-loop` label** → comment-monitor.sh 查的是 `--label auto-loop` 而非 phase:*,漏加 = monitor 完全不监控该 PR 评论 → maintainer 喊话无 react 无回复

<a id="codex-invocation-details"></a>
## Codex invocation details

## Codex 调用方式 — 强制

**问题**:codex 进程要让 maintainer 在 Claude Code UI 的 background tasks / shells panel 一眼可见。

**规则**:**controller 主链路所有 codex spawn 优先用 Bash tool `run_in_background: true`**。Claude Code harness 跟踪该 background task,显示在 UI shells/tasks 面板 → maintainer 看到 "8 shells" 等计数。`nohup ... & disown` 会 detach 出 harness,maintainer 看不到即时 shells/task-notification;若意外发生,不要杀掉重派,必须确认 log 可 sweep 且本 turn 结束前有已注册 ScheduleWakeup 或其它在飞 task-notification。

### 推荐调用 pattern

```python
Bash(
  command="<skill-root>/scripts/spawn-codex.sh "
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

- ❌ 主链路主动用 `nohup spawn-codex.sh ... & disown` 图省事 → 脱离 Claude harness,UI 看不到 shells,失去即时 task-notification
- ❌ 已 detached 的 codex 仍在跑,controller kill 后重派 → 浪费工作;应靠已确认 wake 源 + `EXIT=0` sweep 接住
- ❌ Bash `run_in_background: false` 同步等 codex(可能跑 1-2h)→ Bash tool 阻塞,turn 卡死
- ❌ codex 跑在 controller 自己的 conversation Bash 里 → 同步阻塞 OR 中断 UI

<a id="hard-rules-details"></a>
## Hard rules details

## Hard rules (controller-level, propagated into every codex prompt)

1. **No new features** — only clean violations of CLAUDE.md philosophy.
2. **No external repo changes** — $EXTERNAL_REPOS are out of scope.
3. **Code self-documents the refactor** — every refactored type/method gets a 3-5 line comment of the form `// Refactor (iterN/cluster-XXX): Old pattern: …  New principle: …`.
4. **No `commit`/`push`/`checkout` inside codex prompts** — the controller owns git topology.
5. **No `sleep/delay`-based test pacing** — tests must use deterministic awaiters.
6. **No `[Skip]` / disabled tests** as a way to make CI green.
7. **No scope creep** — codex must print `SCOPE_EXTEND: <file> <reason>` before touching anything outside `scope_paths`.
8. **Source files are English-only; external user-facing artifacts are 中文 by default**. Inside `.rs` / `.lua` / `.sh` / `.py` / `.ts`, comments, docstrings, `log.{info,warn,error}` strings, error/panic text, identifiers, and code-built commit-body templates are English. Outside source files, GitHub issue bodies, PR descriptions, design notifications, git commit messages written by the controller/codex, docs, TODO markers, and natural-language artifacts use 中文. English may appear inline when quoting (a) a CLAUDE.md / AGENTS.md clause, (b) source error messages, (c) test names — quote verbatim, do not translate. No mandatory parallel English section.

## 工作语言规则(源码内英文,源码外中文)

Policy: **源文件内部 English-only;源文件之外的 user-facing artifact 默认 中文**。

中文适用对象:GitHub issue body、PR description、PR comments、design issue auto-loop 评论、scorecard docs (`docs/audit-scorecard/`)、escalation 文案、cross-post 通知、controller / codex 写出的 git commit message、`docs/*.md`、TODO 标记。Internal artifact(`.refactor-loop/runs/*.md`、state.json)仍是英文(只要 grep / 调试用)。

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
| Skill 文档 / $REPO_ROOT 的架构/词汇文档(若有) /audit 报告 | 维持现状(中英混排已存在) |
| **代码内 `// Refactor (iterN/cluster-XXX):` 注释** | **英文**(production code 跨团队读) |
| **代码内 doc comment / xmldoc / 其他注释** | **英文** |
| **代码内 log / error / panic 字符串** | **英文** |
| **代码内构造的 commit-body 模板字符串** | **英文** |
| 代码 identifier / 类名 / 方法名 / 字段 | 英文(原 .NET / 项目惯例) |
| proto / yaml 结构 | 英文 |
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

<a id="workunitv1-contract"></a>

## WorkUnitV1 contract

`WorkUnitV1` is the v1 queue item contract stored inside the existing `clusters_planned`,
`clusters_active`, `clusters_done`, and `clusters_failed` containers. The container names are
historical but authoritative for state schema v1; do not add migrated queue containers, envelope
wrappers, a normalizer helper, or a state-v2 migration for this contract.

Naming policy: this engine's public product identity is Consensus R&D, and `codex-refactor-loop`
remains the stable installed skill entrypoint. In v1, `refactor` is a valid development/work-unit
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
- Old state without `work_unit_schema_version` is read as v1 legacy state. Derive
  `work_unit_id` from each queue item's `id`, treat items as `kind="audit-cluster"` and
  `producer="audit"`, and use the audit section as `source_ref` when known.
- Prompt dispatch for current audit-backed units exports `WORK_UNIT_ID=$CLUSTER_ID`. Existing
  markers, artifact names, branch names, and audit section lookups may continue to use
  `CLUSTER_ID` during v1 compatibility.

Stable v1 operational tokens:

- Current markers, GitHub labels, issue title prefixes, branch prefixes, artifact paths, prompt
  markers, log markers, and audit section lookups are stable v1 operational names.
- `cluster-009-marker-label-compat-migration` does not rename, dual-write, or add aliases for
  these names. Keep existing `refactor`, `cluster`, `auto-loop`, and `*_DONE` spellings as the
  v1 public routing surface while `WORK_UNIT_ID=$CLUSTER_ID` is the compatibility bridge.

## Producers in v1

`WorkUnitV1` separates the queue item contract from the source that produced the item. The v1
controller recognizes exactly these producer values:

- `audit`
- `manual-issue`

This is a documented normalization boundary, not a new producer framework. Do not add new
producer abstractions, registry helpers, envelope wrappers, or migrated work-unit state containers
for v1.

### `audit` producer

`audit` remains the default producer. It reads the raw artifact contract from
`prompts/audit.md` and the resulting `.refactor-loop/runs/audit-iter-N.md` cluster sections.
The controller leaves `prompts/audit.md` unchanged and projects each accepted audit cluster into
`WorkUnitV1` before adding it to `clusters_planned`:

- `work_unit_id: <cluster-id>`
- `id: <cluster-id>`
- `cluster_id: <cluster-id>`
- `kind: audit-cluster`
- `producer: audit`
- `source_ref: .refactor-loop/runs/audit-iter-N.md#<cluster-id>`
- `legacy_cluster_id: <cluster-id>` optional but recommended during v1 compatibility

Audit-backed units may keep using `<cluster-id>` for branch names, worktree paths, artifact
filenames, markers, and audit section lookup while `WORK_UNIT_ID=$CLUSTER_ID` remains the v1 alias.

### `manual-issue` producer

`manual-issue` is the Phase 7 `auto-loop-triage` intake path for maintainer-selected GitHub
issues. Accepted issues must be reshaped into a `WorkUnitV1`-backed design issue before Phase 9
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

<a id="state-schema"></a>
## State schema (`.refactor-loop/state.json`)

### Statusline snapshot schema

`concurrency_monitor.py` writes `.refactor-loop/state/statusline-snapshot.json` once per tick
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
- `actual`: current this-loop `spawn-codex.sh` process count.
- `expected`: current no-gap expected worker count from active auto-loop issues/PRs.
- `floor`: host `CODEX_FLOOR`, with the existing hard lower bound of 2.
- `p0_streak`: consecutive no-gap violation tick count.
- `last_p0_at`: UTC timestamp of the latest P0 no-gap violation, or `null`.
- `freeze_minutes`: whole minutes since the newest local PHASE/REVIEW/FIX/META marker file mtime; 0 when no marker exists.
- `open_pr_count`: open auto-loop PR count from the same GitHub scan used by the monitor.
- `open_issue_count`: open auto-loop issue count from the same GitHub scan used by the monitor.

```json
{
  "schema_version": 1,
  "work_unit_schema_version": 1,
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
    {
      "work_unit_id": "cluster-001",
      "id": "cluster-001",
      "cluster_id": "cluster-001",
      "legacy_cluster_id": "cluster-001",
      "kind": "audit-cluster",
      "producer": "audit",
      "source_ref": "audit-iter-1.md#cluster-001",
      "batch": "A",
      "scope_paths": ["<path>"],
      "old_pattern": "<problem>",
      "new_principle": "<target principle>",
      "verification_hints": "<checks>",
      "risk": "low|medium|high",
      "leverage": "low|medium|high",
      "dependencies": ["cluster-XXX"]
    }
  ],
  "clusters_active": [
    {
      "work_unit_id": "cluster-001",
      "id": "cluster-001",
      "cluster_id": "cluster-001",
      "legacy_cluster_id": "cluster-001",
      "kind": "audit-cluster",
      "producer": "audit",
      "source_ref": "audit-iter-1.md#cluster-001",
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
    {
      "work_unit_id": "cluster-001",
      "id": "cluster-001",
      "cluster_id": "cluster-001",
      "legacy_cluster_id": "cluster-001",
      "kind": "audit-cluster",
      "producer": "audit",
      "source_ref": "audit-iter-1.md#cluster-001",
      "merged_at": "<ISO8601>",
      "commit": "<sha>",
      "pr_number": <int|null>,
      "merged_into": "<integration_branch | upstream-cluster-branch>"
    }
  ],
  "clusters_failed": [
    {
      "work_unit_id": "cluster-001",
      "id": "cluster-001",
      "cluster_id": "cluster-001",
      "legacy_cluster_id": "cluster-001",
      "kind": "audit-cluster",
      "producer": "audit",
      "source_ref": "audit-iter-1.md#cluster-001",
      "phase": "implement|verify|merge|remote-ci|stack-rebase",
      "reason": "<short>"
    }
  ],
  "rollup_pr": {
    "pr_number": <int|null>,
    "base": "<review_base_branch>",
    "head": "<integration_branch>"
  },
  "design_pending": [
    {
      "work_unit_id": "cluster-NNN",
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

<a id="batching-heuristics"></a>
## Batching heuristics

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

<a id="recovery-playbook"></a>
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

Symptom: `git merge` after a `cd "$REPO_ROOT/.worktrees/<id>"` chain prints `Already up to date.` instead of merging the branch into trunk.

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
