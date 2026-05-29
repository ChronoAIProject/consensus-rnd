"""Controller-owned GitHub status banner rendering and posting helpers."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .context import LoopContext, LoopContextError


AUTO_LOOP_SENTINEL = "⟦AI:AUTO-LOOP⟧"
AUTHORIZATION_ARTIFACT = ".refactor-loop/runs/phase9-issue53-r7-judge.md"
OBSERVABILITY_COMMENT_WRITER = "observability-comment-writers"

# Refactor (issue160/p3-banners): Old pattern: status banner rendering and
# GitHub posting lived only in the executable script. New principle: package
# code owns the reusable contract while legacy callers keep using post_banner.py
# until the caller migration phase.
ROLE_NEXT_STEPS = {
    "test-add": "1. test-add 完成 marker `TEST_ADD_DONE:...`  2. controller 自动 commit + push  3. codecov 重测",
    "fix": "1. fix r<N> 完成 marker `FIX_DONE:...`  2. controller commit + push  3. 派 reviewer r<N+1>",
    # Refactor (iter3/skill-merge-policy): Old pattern: unanimous-approve merge
    # gate + contradictory Phase 8 wording. New principle: fixed truth table
    # reject=0 && approve>=1 -> MERGE; comments are advisory (#26 minimal option B consensus).
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
    cd: str
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
| 工作目录 | `{request.cd}` |
| no-output stall window | {request.stall}s(~{request.stall // 60} min 无输出窗口) |
| 上下文 | {request.detail or "(none)"} |
| 下一步自动会做 | {next_step} |
| **是否需要人介入** | **❌ 否**(自动推进) |

🤖 controller status banner

{AUTO_LOOP_SENTINEL}
"""


def repo_slug_from_env(env: Mapping[str, str]) -> str | None:
    """Resolve the GitHub repo slug with the existing host.env-compatible order."""

    slug = env.get("GH_REPO_SLUG")
    if slug:
        return slug
    repo = env.get("GH_REPO")
    if repo and "/" in repo:
        return repo
    owner = env.get("GH_OWNER")
    name = env.get("GH_REPO_NAME") or repo
    if owner and name:
        return f"{owner}/{name}"
    return None


def repo_slug_from_context(ctx: LoopContext | None = None, env: Mapping[str, str] | None = None) -> str | None:
    """Return the repo slug from LoopContext or environment without mutating host state."""

    if ctx is not None:
        return ctx.gh_repo_slug
    return repo_slug_from_env(os.environ if env is None else env)


def gh_comment_command(request: BannerRequest, body_file: Path, repo_slug: str | None = None) -> list[str]:
    """Build the narrow GitHub comment command allowed for status banners."""

    if request.kind not in {"issue", "pr"}:
        raise ValueError(f"unsupported banner kind: {request.kind}")
    cmd = ["gh", request.kind, "comment", str(request.target)]
    if repo_slug:
        cmd.extend(["--repo", repo_slug])
    cmd.extend(["--body-file", str(body_file)])
    return cmd


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def post_status_banner(
    request: BannerRequest,
    *,
    repo_slug: str | None = None,
    ctx: LoopContext | None = None,
    command_runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = _run_command,
) -> str:
    """Post a status banner and return the GitHub URL printed by `gh`."""

    body = build_status_banner(request)
    resolved_repo_slug = repo_slug if repo_slug is not None else repo_slug_from_context(ctx)
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
    try:
        tmp.write(body)
        tmp.close()
        result = command_runner(gh_comment_command(request, Path(tmp.name), resolved_repo_slug))
    finally:
        try:
            os.unlink(tmp.name)
        except FileNotFoundError:
            pass
    if result.returncode != 0:
        raise RuntimeError(f"FAIL banner post: {result.stderr.strip()}")
    return result.stdout.strip()


def load_optional_context() -> LoopContext | None:
    """Load host.env-backed context when available; keep legacy env-only fallback."""

    try:
        return LoopContext.load()
    except LoopContextError:
        return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--banner-target", required=True)
    parser.add_argument("--banner-kind", choices=["issue", "pr"], required=True)
    parser.add_argument("--banner-role", required=True, choices=list(ROLE_NEXT_STEPS.keys()))
    parser.add_argument("--banner-detail", default="")
    parser.add_argument("--log", required=True, help="codex log path (for banner display)")
    parser.add_argument("--cd", required=True, help="codex cwd (for banner display)")
    parser.add_argument(
        "--stall",
        "--timeout",
        dest="stall",
        type=int,
        required=True,
        help="codex no-output stall window seconds (for banner display)",
    )
    return parser.parse_args(argv)


def request_from_args(args: argparse.Namespace) -> BannerRequest:
    return BannerRequest(
        target=str(args.banner_target),
        kind=str(args.banner_kind),
        role=str(args.banner_role),
        detail=str(args.banner_detail),
        log=str(args.log),
        cd=str(args.cd),
        stall=int(args.stall),
    )


def main(
    argv: list[str] | None = None,
    *,
    command_runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = _run_command,
) -> int:
    args = parse_args(argv)
    request = request_from_args(args)
    try:
        url = post_status_banner(request, ctx=load_optional_context(), command_runner=command_runner)
    except RuntimeError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stderr.write(f"BANNER_POSTED: {request.kind} #{request.target} {url}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
