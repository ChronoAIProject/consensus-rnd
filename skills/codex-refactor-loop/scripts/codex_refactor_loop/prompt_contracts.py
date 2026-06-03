"""Render-time prompt contract expansion for checked-in worker prompts."""

from __future__ import annotations

import re
from pathlib import Path


class PromptContractError(RuntimeError):
    """Raised when a prompt contract token cannot be expanded safely."""


GITHUB_POST_RULES_CONTRACT_TOKEN = "{{GITHUB_POST_RULES_CONTRACT}}"
_CONTRACT_TOKEN_RE = re.compile(r"{{[A-Z0-9_]+_CONTRACT}}")


def inline_prompt_contracts(text: str, *, skill_root: Path) -> str:
    tokens = set(_CONTRACT_TOKEN_RE.findall(text))
    unknown = sorted(tokens - {GITHUB_POST_RULES_CONTRACT_TOKEN})
    if unknown:
        raise PromptContractError(f"unknown prompt contract token(s): {', '.join(unknown)}")
    if GITHUB_POST_RULES_CONTRACT_TOKEN not in tokens:
        return text
    rules_path = skill_root / "prompts" / "_github-post-rules.md"
    try:
        rules = rules_path.read_text(encoding="utf-8").rstrip()
    except OSError as exc:
        raise PromptContractError(f"missing GitHub post rules contract: {rules_path}") from exc
    return text.replace(GITHUB_POST_RULES_CONTRACT_TOKEN, rules)
