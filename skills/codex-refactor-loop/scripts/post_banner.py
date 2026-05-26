#!/usr/bin/env python3
"""
post_banner.py — 只 post GitHub status banner,不 spawn codex

spawn_with_banner.py 的 detached Popen 模式让 harness 看不见 codex,导致
codex done 后 controller 不知道,monitor 60s 报警但 controller 没有即时处理。

新架构:**两步**
1. 此脚本(blocking,几秒)post banner 到 issue/PR
2. 调用方用 Bash `run_in_background: true` 跑 `spawn-codex.sh` — harness 跟踪 →
   codex exit 时 harness fire <task-notification> → controller 立即唤醒

Usage:
  python3 .claude/skills/codex-refactor-loop/scripts/post_banner.py \\
    --banner-target <issue-or-pr> --banner-kind <issue|pr> \\
    --banner-role <role> --banner-detail "..." \\
    --log <log-path> --cd <worktree> --stall <s>

返回:
- exit 0 if banner posted,stderr 打印 BANNER_POSTED: <kind> #<issue-or-pr> <url>
- exit 1 if banner failed

⟦AI:AUTO-LOOP⟧
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from repo_config import github_repo_slug

ROLE_NEXT_STEPS = {
    "test-add": "1. test-add 完成 marker `TEST_ADD_DONE:...`  2. controller 自动 commit + push  3. codecov 重测",
    "fix": "1. fix r<N> 完成 marker `FIX_DONE:...`  2. controller commit + push  3. 派 reviewer r<N+1>",
    # Refactor (iter3/skill-merge-policy): Old pattern: unanimous-approve merge gate + Phase 8 文案矛盾  New principle: 固定真值表 reject=0 && approve>=1 → MERGE;comment 是 advisory(#26 minimal option B 共识)
    "reviewer": "1. 三 reviewer 完成 verdict marker  2. controller 计算 consensus  3. reject=0 + approve>=1 -> merge; all-comment -> wait explicit approval; reject -> fix",
    "implement": "1. implement 完成 marker `IMPLEMENT_DONE:<cluster>:<status>`  2. controller commit + push  3. open PR + 派 reviewer r1",
    "solver": "1. 三 solver `SOLVER_DONE:...`  2. controller 派 meta-judge r<N>  3. consensus → implement / converge → fresh round",
    "judge": "1. judge `META_JUDGE_DONE:...`  2. consensus → implement / converge → fresh round / escalate → reflector or 人介入",
    "reflector": "1. reflector `META_RESOLVED:<kind>`  2. controller 按 kind 路由(retry-fix / re-design / re-cluster / drop / escalate-human)",
    "audit": "1. audit 完成 marker `AUDIT_DONE:...:<N>`  2. controller 验证 + 开 design issues + 派 implement",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--banner-target", required=True)
    p.add_argument("--banner-kind", choices=["issue", "pr"], required=True)
    p.add_argument("--banner-role", required=True, choices=list(ROLE_NEXT_STEPS.keys()))
    p.add_argument("--banner-detail", default="")
    p.add_argument("--log", required=True, help="codex log path (for banner display)")
    p.add_argument("--cd", required=True, help="codex cwd (for banner display)")
    p.add_argument("--stall", "--timeout", dest="stall", type=int, required=True,
                   help="codex no-output stall window seconds (for banner display)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    role = args.banner_role
    next_step = ROLE_NEXT_STEPS[role]
    log_name = Path(args.log).name
    body = f"""## 📊 状态卡片 — {role} 派出

| 维度 | 值 |
|---|---|
| 阶段 | **派出 codex(role=`{role}`)** |
| codex log | `{log_name}` |
| 工作目录 | `{args.cd}` |
| no-output stall window | {args.stall}s(~{args.stall // 60} min 无输出窗口) |
| 上下文 | {args.banner_detail or "(none)"} |
| 下一步自动会做 | {next_step} |
| **是否需要人介入** | **❌ 否**(自动推进) |

🤖 controller status banner

⟦AI:AUTO-LOOP⟧
"""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
    tmp.write(body)
    tmp.close()
    cmd = ["gh", args.banner_kind, "comment", str(args.banner_target)]
    repo_slug = github_repo_slug()
    if repo_slug:
        cmd.extend(["--repo", repo_slug])
    cmd.extend(["--body-file", tmp.name])
    r = subprocess.run(cmd, capture_output=True, text=True)
    os.unlink(tmp.name)
    if r.returncode != 0:
        sys.stderr.write(f"FAIL banner post: {r.stderr.strip()}\n")
        return 1
    url = r.stdout.strip()
    sys.stderr.write(f"BANNER_POSTED: {args.banner_kind} #{args.banner_target} {url}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
