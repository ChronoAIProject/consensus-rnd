"""Read-only GitHub body renderer and self-contained authority validator.

Runtime boundary: this read-only helper may read local artifact files and print rendered
Markdown to stdout. It must not write files, call Git/GitHub, spawn background processes, or
change controller state; behavior and source-regression tests verify that
boundary.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path
from typing import Sequence

from .context import HostWorkLanguage, LoopContextError
from .runtime_copy import all_copy_values, copy_for, current_work_language


FINAL_SENTINEL = "⟦AI:AUTO-LOOP⟧"
MAX_BODY_BYTES = 60000
LOCAL_RUN_ARTIFACT_RE = re.compile(r"\.refactor-loop/runs/[^\s)>\]`'\"]+\.md")
AUTHORITY_PATH_RE = re.compile(
    r"(授权|共识|consensus|authorization|judge|solver|artifact|decision|依据|来源)\s*[:：]?\s*`?"
    r"\.refactor-loop/runs/[^\s)>\]`'\"]+\.md",
    re.I,
)
INLINE_ARTIFACT_DETAILS_RE = re.compile(
    r"<details>\s*<summary>(?:Inline|内联) artifact [0-9]+: [^<]+</summary>\s*"
    r"```markdown\n(?P<artifact>.*?)\n```\s*</details>",
    re.S,
)
ALLOWED_KINDS = {"pr", "design-issue", "consensus", "authorization", "escalation", "triage"}


class GitHubBodyError(ValueError):
    """Raised when a GitHub-facing body is not self-contained."""


def render_github_body(
    *,
    kind: str,
    title: str,
    artifact_paths: Sequence[str | Path],
    debug_paths: Sequence[str | Path] = (),
    max_bytes: int = MAX_BODY_BYTES,
) -> str:
    """Render a self-contained GitHub body from local artifacts."""

    _validate_kind(kind)
    language = current_work_language()
    if not title.strip():
        raise GitHubBodyError("title required")
    artifacts = [(Path(path), _read_artifact(Path(path))) for path in artifact_paths]
    if not artifacts:
        raise GitHubBodyError("at least one artifact required")

    copy = _body_copy(language)
    lines = [
        f"## 🤖 {title.strip()}",
        "",
        "### TL;DR",
        f"- {copy['what_prefix']}{_kind_label(kind, language)} GitHub body.",
        f"- {copy['conclusion']}",
        f"- {copy['next_step']}",
        "",
        "---",
        "",
        copy["details_heading"],
        "",
        copy["intro"],
        "",
    ]
    for index, (path, text) in enumerate(artifacts, start=1):
        lines.extend(
            [
                "<details>",
                f"<summary>{copy['artifact_summary']} {index}: {html.escape(path.name)}</summary>",
                "",
                "```markdown",
                text.rstrip(),
                "```",
                "",
                "</details>",
                "",
            ]
        )

    if debug_paths:
        lines.extend(
            [
                "<details>",
                copy["debug_summary"],
                "",
                copy["debug_intro"],
                "",
            ]
        )
        for path in debug_paths:
            lines.append(f"- `{Path(path).as_posix()}`")
        lines.extend(["", "</details>", ""])

    lines.append(FINAL_SENTINEL)
    body = "\n".join(lines) + "\n"
    validate_self_contained_github_body(body, authority_required=True, max_bytes=max_bytes)
    return body


def validate_self_contained_github_body(
    text: str,
    *,
    authority_required: bool = False,
    max_bytes: int = MAX_BODY_BYTES,
) -> None:
    """Fail closed when a GitHub body uses local run paths as sole authority."""

    if not isinstance(text, str) or not text.strip():
        raise GitHubBodyError("empty GitHub body")
    if len(text.encode("utf-8")) > max_bytes:
        raise GitHubBodyError("BODY_TOO_LARGE")
    if not text.splitlines()[-1:] == [FINAL_SENTINEL]:
        raise GitHubBodyError("missing final sentinel")
    public_text = _mask_allowed_run_path_sections(text)
    if AUTHORITY_PATH_RE.search(public_text):
        raise GitHubBodyError("local .refactor-loop artifact path cannot be the only authority source")
    if LOCAL_RUN_ARTIFACT_RE.search(public_text) is not None:
        raise GitHubBodyError("local .refactor-loop artifact path is only allowed under local debug details")
    if authority_required and not _has_raw_inline_artifact_details(text):
        raise GitHubBodyError("authority body must inline raw artifact text in inline artifact details")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a self-contained GitHub body from local artifacts")
    parser.add_argument("--kind", required=True, choices=sorted(ALLOWED_KINDS))
    parser.add_argument("--title", required=True)
    parser.add_argument("--artifact", action="append", required=True, default=[])
    parser.add_argument("--debug-path", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        body = render_github_body(
            kind=args.kind,
            title=args.title,
            artifact_paths=args.artifact,
            debug_paths=args.debug_path,
        )
        sys.stdout.write(body)
        return 0
    except (OSError, GitHubBodyError, LoopContextError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2


def _read_artifact(path: Path) -> str:
    if not path.is_file():
        raise GitHubBodyError(f"artifact not found: {path}")
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise GitHubBodyError(f"artifact empty: {path}")
    return text


def _validate_kind(kind: str) -> None:
    if kind not in ALLOWED_KINDS:
        raise GitHubBodyError(f"invalid GitHub body kind: {kind}")


def _body_copy(language: HostWorkLanguage) -> dict[str, str]:
    return dict(copy_for("github_body", language=language))


def _kind_label(kind: str, language: HostWorkLanguage) -> str:
    return copy_for("github_body_kind", language=language)[kind]


def _has_raw_inline_artifact_details(text: str) -> bool:
    return any(match.group("artifact").strip() for match in INLINE_ARTIFACT_DETAILS_RE.finditer(text))


def _mask_allowed_run_path_sections(text: str) -> str:
    debug_pattern = "|".join(re.escape(summary) for summary in all_copy_values("github_body", "debug_summary"))
    masked = re.sub(r"<details>\s*(?:" + debug_pattern + r").*?</details>", "", text, flags=re.S)
    masked = INLINE_ARTIFACT_DETAILS_RE.sub("", masked)
    return masked


if __name__ == "__main__":
    raise SystemExit(main())
