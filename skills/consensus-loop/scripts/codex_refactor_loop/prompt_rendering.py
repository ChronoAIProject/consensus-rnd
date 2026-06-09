"""Shared render-time prompt binding surface."""

from __future__ import annotations

import os
from pathlib import Path
from string import Template
from typing import Mapping

from .context import normalize_host_work_language
from .prompt_contracts import inline_prompt_contracts


CONTROLLER_ALIAS_NAMES = (
    "work_unit_id",
    "cluster_id",
    "iteration",
    "worktree_path",
    "branch",
    "old_pattern",
    "new_principle",
    "scope_paths",
    "verification_hints",
)


def render_prompt_text(
    template: str,
    *,
    skill_root: Path,
    values: Mapping[str, str] | None = None,
    host_env: Mapping[str, str] | None = None,
    base_env: Mapping[str, str] | None = None,
) -> str:
    bindings = prompt_render_bindings(values=values, host_env=host_env, base_env=base_env)
    rendered = _replace_controller_aliases(template, bindings)
    rendered = Template(rendered).safe_substitute(bindings)
    rendered = inline_prompt_contracts(rendered, skill_root=skill_root)
    return _replace_final_host_work_language(rendered, bindings["HOST_WORK_LANGUAGE"])


def prompt_render_bindings(
    *,
    values: Mapping[str, str] | None = None,
    host_env: Mapping[str, str] | None = None,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    bindings = {str(key): str(value) for key, value in dict(os.environ if base_env is None else base_env).items()}
    host_values = {str(key): str(value) for key, value in (host_env or {}).items()}
    caller_values = {str(key): str(value) for key, value in (values or {}).items()}
    bindings.update(host_values)
    bindings.update(caller_values)
    bindings["HOST_WORK_LANGUAGE"] = normalize_host_work_language(
        raw=caller_values.get("HOST_WORK_LANGUAGE") or host_values.get("HOST_WORK_LANGUAGE") or bindings.get("HOST_WORK_LANGUAGE"),
        env=bindings,
    )
    bindings.update(_controller_alias_values(bindings))
    return bindings


def _controller_alias_values(bindings: Mapping[str, str]) -> dict[str, str]:
    return {
        "work_unit_id": bindings.get("WORK_UNIT_ID") or bindings.get("CLUSTER_ID") or "",
        "cluster_id": bindings.get("CLUSTER_ID", ""),
        "iteration": bindings.get("ITERATION", ""),
        "worktree_path": bindings.get("WORKTREE_PATH", ""),
        "branch": bindings.get("BRANCH", ""),
        "old_pattern": bindings.get("OLD_PATTERN", ""),
        "new_principle": bindings.get("NEW_PRINCIPLE", ""),
        "scope_paths": bindings.get("SCOPE_PATHS", ""),
        "verification_hints": bindings.get("VERIFICATION_HINTS", ""),
    }


def _replace_controller_aliases(template: str, bindings: Mapping[str, str]) -> str:
    rendered = template
    for key in CONTROLLER_ALIAS_NAMES:
        rendered = rendered.replace("{{" + key + "}}", bindings.get(key, ""))
    return rendered


def _replace_final_host_work_language(text: str, language: str) -> str:
    return text.replace("${HOST_WORK_LANGUAGE}", language)
