"""Narrow GitHub CLI wrapper for controller-owned operations."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class GhCli:
    """Small subprocess wrapper around the `gh` CLI."""

    repo_slug: str | None = None
    cwd: Path | None = None

    def _repo_args(self) -> list[str]:
        return ["--repo", self.repo_slug] if self.repo_slug else []

    def run_text(self, args: Sequence[str], *, input_text: str | None = None) -> str:
        result = subprocess.run(
            ["gh", *args, *self._repo_args()],
            cwd=str(self.cwd) if self.cwd else None,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"gh {' '.join(args)} failed with exit {result.returncode}")
        return result.stdout

    def run_json(self, args: Sequence[str]) -> Any:
        text = self.run_text(args)
        try:
            return json.loads(text or "null")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid gh JSON for {' '.join(args)}") from exc

    # Refactor (iter/issue-193):
    #   Old pattern: callers could invent ownership from labels/comments or
    #   local state when deciding whether to run side effects.
    #   New principle: expose only read helpers for GitHub author.login and
    #   updatedAt; mutation remains in existing controller-owned methods.
    def current_login(self) -> str | None:
        data = self.run_json(["api", "user"])
        return str(data.get("login")) if isinstance(data, dict) and data.get("login") else None

    def issue_ownership_fields(self, number: int | str) -> dict[str, Any]:
        data = self.run_json(["issue", "view", str(number), "--json", "author,updatedAt"])
        return data if isinstance(data, dict) else {}

    def pr_ownership_fields(self, number: int | str) -> dict[str, Any]:
        data = self.run_json(["pr", "view", str(number), "--json", "author,updatedAt"])
        return data if isinstance(data, dict) else {}

    def issue_comment(self, number: int | str, body: str) -> str:
        return self.run_text(["issue", "comment", str(number), "--body", body])

    def reaction(self, comment_id: int | str, content: str = "eyes") -> str:
        return self.run_text(["api", f"repos/{self.repo_slug}/issues/comments/{comment_id}/reactions", "-X", "POST", "-f", f"content={content}"])
