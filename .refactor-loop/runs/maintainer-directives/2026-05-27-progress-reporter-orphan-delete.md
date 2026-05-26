# Maintainer-directive: codex-progress-reporter orphan delete

**Authorization**: maintainer @loning, 2026-05-27 wakeup, "这个 bug 直接改吧"(referring to issue #69 评论中 30 条 progress comment 累积)。

## 实证

- `codex-progress-reporter.sh` 已是 edit-in-place 设计:每 codex 一条 tracking comment,定期 edit。
- 但 issue #69 累积 30 条 `## 📊 codex 进展` 来自 30 个**不同**的 codex(r1-r7 × {minimal, structural, delete, judge, reflector}),不是单 codex 重发。
- 每条 codex EXIT=0 时,daemon 应 `gh api DELETE` 删 comment。**实际:30 个 state entry 全部 `finished=true` 但 comment 仍在 GitHub 上** — delete 当时失败(疑似 rate limit / 网络),daemon 把 `finished=true` 写死后再也不会重试,comment 永远 orphan。

## 改动 scope(narrow allowlist,maintainer-directive 等价 Phase 9 共识)

1. `skills/codex-refactor-loop/scripts/codex-progress-reporter.sh`
   - 改动 1:`post_or_update` 的 delete 分支 — 只有 `gh api DELETE` 返回 success 或 404 才 `state_set finished=true`;其他失败保留 state 不变,下 tick 重试。
   - 改动 2:tick 顶部增加 **orphan sweep** — 扫 state.json,对 `finished=true && cid != "0" && cid != "deleted" && cid != "gone"` 的 entry 再次 attempt delete(GitHub 上 comment 还在 → 删;404 → 标记 gone)。
2. `skills/codex-refactor-loop/scripts/test_codex_progress_reporter_orphan.sh`(新增)
   - 模拟 delete 失败 → 下 tick 重试 → success 后 finished=true。
   - 模拟 GitHub 上 comment 已 404 → 标记 gone 而非死循环。
3. `skills/codex-refactor-loop/scripts/test_ensure_project_rules_fixed_points.py`(增 source-regression)
   - 字面断言本 directive artifact 路径 + 关键不变量字串。

## 不允许扩 scope

- 不改 daemon 整体架构(不引入 `gh comment edit` 设计 — 那是 issue #70 的 design 范畴)。
- 不改 INTERVAL 默认值(那需要 host policy 共识)。
- 不删 GitHub 上既有 30 条 spam —— 由独立 cleanup 脚本 `cleanup_orphan_progress_comments.py` 一次性 run 处理(state 一致后 daemon 下次 tick 也会顺手处理,但 cleanup 脚本是确定性入口)。

## 后续 routing

- 该 directive 提交后会与现有 issue #70 r1 solver 并行,不阻塞 #70 design(它评估的是 daemon 整体架构而非 orphan bug)。

