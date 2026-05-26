# Maintainer directive — spawn-codex PID self-registry

## Quotes from session

loning (2026-05-26):
- "改一下skills, 是检测方法的问题,用错了脚本.优先解决一下相关issues."
- "改一下skills,改由数codex, 变成数本仓库的脚本不就好了,这样别的codex跟本仓库也没关系, 本仓库的codex量都是脚本启动的."

## Scope

- `skills/codex-refactor-loop/scripts/spawn-codex.sh`:加 PID self-registry(`.refactor-loop/spawned/<log-stem>.pid`)+ trap cleanup
- `skills/codex-refactor-loop/scripts/concurrency_monitor.py`:`count_in_flight_codex()` 改从 `spawned/` 目录读 PID 文件 + 校验 PID 活
- `skills/codex-refactor-loop/scripts/phase9_router_daemon.py`:同样改 in-flight 计数
- `skills/codex-refactor-loop/REFERENCE.md`:加 spawned PID registry contract 说明
- 配套行为测试 + source-regression

## requires_design

false。机械型修复 cross-repo `ps grep` 误检测 bug(false positive 跨 host project 计数)。属维护者实证 + audit-derived。

## Phase 9 等价证明

依 CLAUDE.md line 43 maintainer-directive equivalence 子句(PR #48 merged f2854db)。

⟦AI:AUTO-LOOP⟧
