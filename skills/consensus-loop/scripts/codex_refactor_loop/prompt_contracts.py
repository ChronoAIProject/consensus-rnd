"""Render-time prompt contract expansion for checked-in worker prompts."""

from __future__ import annotations

import re
from pathlib import Path


class PromptContractError(RuntimeError):
    """Raised when a prompt contract token cannot be expanded safely."""


GITHUB_POST_RULES_CONTRACT_TOKEN = "{{GITHUB_POST_RULES_CONTRACT}}"
REASONING_DISCIPLINE_CONTRACT_TOKEN = "{{REASONING_DISCIPLINE_CONTRACT}}"
_CONTRACT_TOKEN_RE = re.compile(r"{{[A-Z0-9_]+_CONTRACT}}")
_CONTRACTS = {
    GITHUB_POST_RULES_CONTRACT_TOKEN: ("_github-post-rules.md", "GitHub post rules contract"),
    REASONING_DISCIPLINE_CONTRACT_TOKEN: ("_reasoning-discipline.md", "reasoning discipline contract"),
}


def inline_prompt_contracts(text: str, *, skill_root: Path) -> str:
    tokens = set(_CONTRACT_TOKEN_RE.findall(text))
    unknown = sorted(tokens - set(_CONTRACTS))
    if unknown:
        raise PromptContractError(f"unknown prompt contract token(s): {', '.join(unknown)}")
    rendered = text
    for token in tokens:
        filename, description = _CONTRACTS[token]
        path = skill_root / "prompts" / filename
        try:
            contract = path.read_text(encoding="utf-8").rstrip()
        except OSError as exc:
            raise PromptContractError(f"missing {description}: {path}") from exc
        rendered = rendered.replace(token, contract)
    return rendered
