"""Controller-owned GitHub status banner rendering and posting helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .runtime_copy import copy_for, current_work_language


AUTO_LOOP_SENTINEL = "⟦AI:AUTO-LOOP⟧"
AUTHORIZATION_ARTIFACT = "skills/consensus-loop/authorizations/runtime-exceptions.md#observability-comment-writers-53"
OBSERVABILITY_COMMENT_WRITER = "observability-comment-writers"
ROLE_NEXT_STEPS = copy_for("banner_role_next_steps", language="en")


@dataclass(frozen=True)
class BannerRequest:
    """Inputs required to render and post a controller status banner."""

    target: str
    kind: str
    role: str
    detail: str
    log: str
    stall: int


def build_status_banner(request: BannerRequest, *, env: Mapping[str, str] | None = None) -> str:
    """Render the exact status-card body used by the legacy banner script."""

    if request.kind not in {"issue", "pr"}:
        raise ValueError(f"unsupported banner kind: {request.kind}")
    language = current_work_language(env=env)
    role_next_steps = copy_for("banner_role_next_steps", language=language)
    if request.role not in role_next_steps:
        raise ValueError(f"unsupported banner role: {request.role}")
    role = request.role
    copy = copy_for("banner", language=language)
    next_step = role_next_steps[role]
    log_name = Path(request.log).name
    return f"""{copy['heading'].format(role=role)}

{copy['table_head']}
|---|---|
| {copy['stage_label']} | {copy['stage_value'].format(role=role)} |
| codex log | `{log_name}` |
| total wall-clock timeout | {request.stall}s(~{request.stall // 60} min) |
| {copy['context_label']} | {request.detail or "(none)"} |
| {copy['next_label']} | {next_step} |
| {copy['human_label']} | {copy['human_value']} |

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
