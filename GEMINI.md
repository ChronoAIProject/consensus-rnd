# consensus-rnd skills（Gemini 上下文）

本扩展提供「共识式研发」skills。每个 skill 位于 `skills/<name>/SKILL.md`，**按需读取**，不要一次性全部载入（部分 SKILL.md 很长）。

当前 skills：

- `consensus-loop` — Consensus R&D work-unit 循环的稳定 skill 入口，codex CLI 驱动，GitHub 为状态唯一显示面；用于无人值守解决 managed GitHub issue/PR 并持续推进 repository work；audit/refactor 仅在没有 actionable managed work 时作为 fallback intake。
- `sshx` — 高风险决策、设计取舍或需要 worker-delegated inline consensus，但不需要 daemon/GitHub/git 编排时使用。

当任务匹配某 skill 的触发条件时，先读对应 `skills/<name>/SKILL.md` 再行动。仓库结构与维护约定见 `CLAUDE.md`。
