"""Owner-local target extraction for HARNESS_SPAWN_INTENT records."""

from __future__ import annotations

import re
from typing import Any, Mapping


HARNESS_SPAWN_TARGET_TEXT_PATTERNS = (
    (re.compile(r"(?i)\bPR\s*#([1-9][0-9]*)\b"), "PR"),
    (re.compile(r"(?i)\bissue\s*#([1-9][0-9]*)\b"), "issue"),
    (re.compile(r"\bphase9-" + r"issue([1-9][0-9]*)-r[1-9][0-9]*-[A-Za-z][A-Za-z0-9_-]*\b"), "issue"),
    (re.compile(r"\b" + "review-" + r"pr([1-9][0-9]*)-[A-Za-z][A-Za-z0-9_-]*-r[1-9][0-9]*\b"), "PR"),
    (re.compile(r"\b" + "fix-" + r"pr([1-9][0-9]*)(?:-r|-round-)"), "PR"),
)


def _harness_spawn_intent_target(intent: Mapping[str, Any]) -> tuple[str, int] | None:
    text_parts = []
    for field in ("task_id", "intent_id", "source", "route", "reason"):
        value = intent.get(field)
        if isinstance(value, str) and value:
            text_parts.append(value)
    text = " ".join(text_parts)
    for pattern, kind in HARNESS_SPAWN_TARGET_TEXT_PATTERNS:
        match = pattern.search(text)
        if match:
            return kind, int(match.group(1))
    return None
