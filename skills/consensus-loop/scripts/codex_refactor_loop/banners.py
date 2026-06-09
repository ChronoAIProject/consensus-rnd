"""Controller-owned GitHub status banner rendering and posting helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


AUTO_LOOP_SENTINEL = "⟦AI:AUTO-LOOP⟧"
AUTHORIZATION_ARTIFACT = "skills/consensus-loop/authorizations/runtime-exceptions.md#observability-comment-writers-53"
OBSERVABILITY_COMMENT_WRITER = "observability-comment-writers"

ROLE_NEXT_STEPS = {
    "test-add": "1. test-add 完成 marker `TEST_ADD_DONE:...`  2. controller 自动 commit + push  3. codecov 重测",
    "fix": "1. fix r<N> 完成 marker `FIX_DONE:...`  2. controller commit + push  3. 派 reviewer r<N+1>",
    "reviewer": "1. 三 reviewer 完成 verdict marker  2. controller 计算 consensus  3. reject=0 + approve>=1 -> merge; all-comment -> wait explicit approval; reject -> fix",
    "implement": "1. implement 完成 marker `IMPLEMENT_DONE:<cluster>:<status>`  2. controller commit + push  3. open PR + 派 reviewer r1",
    "solver": "1. 三 solver `SOLVER_DONE:...`  2. controller 派 meta-judge r<N>  3. consensus → implement / converge → fresh round",
    "judge": "1. judge `META_JUDGE_DONE:...`  2. consensus → implement / converge → fresh round / escalate → reflector or 人介入",
    "reflector": "1. reflector `META_RESOLVED:<kind>`  2. controller 按 kind 路由(retry-fix / re-design / re-cluster / drop / escalate-human)",
    "audit": "1. audit 完成 marker `AUDIT_DONE:...:<N>`  2. controller 验证 + 开 design issues + 派 implement",
}


@dataclass(frozen=True)
class BannerRequest:
    """Inputs required to render and post a controller status banner."""

    target: str
    kind: str
    role: str
    detail: str
    log: str
    stall: int


def build_status_banner(request: BannerRequest) -> str:
    """Render the exact status-card body used by the legacy banner script."""

    if request.kind not in {"issue", "pr"}:
        raise ValueError(f"unsupported banner kind: {request.kind}")
    if request.role not in ROLE_NEXT_STEPS:
        raise ValueError(f"unsupported banner role: {request.role}")
    role = request.role
    next_step = ROLE_NEXT_STEPS[role]
    log_name = Path(request.log).name
    return f"""## 📊 状态卡片 — {role} 派出

| 维度 | 值 |
|---|---|
| 阶段 | **派出 codex(role=`{role}`)** |
| codex log | `{log_name}` |
| total wall-clock timeout | {request.stall}s(~{request.stall // 60} min) |
| 上下文 | {request.detail or "(none)"} |
| 下一步自动会做 | {next_step} |
| **是否需要人介入** | **❌ 否**(自动推进) |

🤖 controller status banner

{AUTO_LOOP_SENTINEL}
"""


def gh_comment_command(request: BannerRequest, body_file: Path, repo_slug: str | None = None) -> list[str]:
    """Build the narrow GitHub comment command allowed for status banners."""

    if request.kind not in {"issue", "pr"}:
        raise ValueError(f"unsupported banner kind: {request.kind}")
    cmd = ["gh", request.kind, "comment", str(request.target)]
    if repo_slug:
        cmd.extend(["--repo", repo_slug])
    cmd.extend(["--body-file", str(body_file)])
    return cmd
