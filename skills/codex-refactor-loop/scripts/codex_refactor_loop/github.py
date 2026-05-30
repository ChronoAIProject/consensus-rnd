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

    def issue_comment(self, number: int | str, body: str) -> str:
        return self.run_text(["issue", "comment", str(number), "--body", body])

    def reaction(self, comment_id: int | str, content: str = "eyes") -> str:
        return self.run_text(["api", f"repos/{self.repo_slug}/issues/comments/{comment_id}/reactions", "-X", "POST", "-f", f"content={content}"])
