"""Review-fix prompt render boundary helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Mapping


_POSITIVE_INT_RE = re.compile(r"^[1-9][0-9]*$")
_FIX_OUTPUT_RE = re.compile(r"^\.refactor-loop/runs/fix-pr[1-9][0-9]*-round-[1-9][0-9]*-report\.md$")


@dataclass(frozen=True)
class ReviewFixDispatchSpec:
    """Canonical review-fix prompt, log, and output artifact paths."""

    pr_number: str
    round_number: str
    prompt_path: str
    log_path: str
    fix_output_path: str

    @classmethod
    def for_round(cls, pr_number: int, round_number: int) -> "ReviewFixDispatchSpec":
        pr = _validate_positive_int(pr_number, "pr_number")
        round_no = _validate_positive_int(round_number, "round_number")
        fix_output_path = f".refactor-loop/runs/fix-pr{pr}-round-{round_no}-report.md"
        return cls(
            pr_number=pr,
            round_number=round_no,
            prompt_path=f".refactor-loop/prompts/fixes/fix-pr{pr}-round-{round_no}.md",
            log_path=f".refactor-loop/logs/fix-pr{pr}-round-{round_no}.log",
            fix_output_path=cls.validate_fix_output_path(fix_output_path),
        )

    def as_render_env(self) -> Mapping[str, str]:
        return {
            "PR_NUMBER": self.pr_number,
            "FIX_ROUND": self.round_number,
            "MAX_FIX_ROUNDS": "3",
            "FIX_OUTPUT_PATH": self.fix_output_path,
        }

    @staticmethod
    def validate_fix_output_path(path: str) -> str:
        value = str(path or "").strip()
        pure = PurePosixPath(value)
        if not value:
            raise ValueError("FIX_OUTPUT_PATH is required")
        if pure.is_absolute():
            raise ValueError("FIX_OUTPUT_PATH must be repo-relative")
        if "\\" in value or ".." in pure.parts:
            raise ValueError("FIX_OUTPUT_PATH must not escape the repo")
        if not _FIX_OUTPUT_RE.fullmatch(value):
            raise ValueError(
                "FIX_OUTPUT_PATH must match .refactor-loop/runs/fix-pr<N>-round-<R>-report.md"
            )
        return value


def _validate_positive_int(value: int, label: str) -> str:
    text = str(value)
    if not _POSITIVE_INT_RE.fullmatch(text):
        raise ValueError(f"{label} must be a positive decimal integer")
    return text
