"""Read-only GitHub body renderer and self-contained authority validator.

Runtime boundary: this module may read local artifact files and print rendered
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


FINAL_SENTINEL = "⟦AI:AUTO-LOOP⟧"
MAX_BODY_BYTES = 60000
LOCAL_RUN_ARTIFACT_RE = re.compile(r"\.refactor-loop/runs/[^\s)>\]`'\"]+\.md")
AUTHORITY_PATH_RE = re.compile(
    r"(授权|共识|consensus|authorization|judge|solver|artifact|decision|依据|来源)\s*[:：]?\s*`?"
    r"\.refactor-loop/runs/[^\s)>\]`'\"]+\.md",
    re.I,
)
DEBUG_SUMMARY = "<summary>本机调试线索</summary>"
INLINE_ARTIFACT_DETAILS_RE = re.compile(
    r"<details>\s*<summary>内联 artifact [0-9]+: [^<]+</summary>\s*"
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
    """Render a self-contained Chinese GitHub body from local artifacts.

    Refactor (iter191/issue-191):
    Old pattern: multi-device / multi-loop runtime lacks a single-active-controller guard; GitHub-facing authority/consensus bodies risk referencing local .refactor-loop paths instead of inlining artifacts
    New principle: single active controller lease (no per-work claims, no cross-device floor); strengthen the self-contained github_body.py validator so authority/consensus/plan bodies inline raw artifacts and .refactor-loop/runs/*.md appears only as debug detail

    Refactor (issue192/self-contained-github-body):
    Old pattern: GitHub bodies could cite `.refactor-loop/runs/*.md` as the
    sole authorization source, leaving reviewers with machine-local dead links.
    New principle: this read-only helper inlines the complete artifact text and
    demotes local paths to optional debug hints under a collapsed section.
    """

    _validate_kind(kind)
    if not title.strip():
        raise GitHubBodyError("title required")
    artifacts = [(Path(path), _read_artifact(Path(path))) for path in artifact_paths]
    if not artifacts:
        raise GitHubBodyError("at least one artifact required")

    lines = [
        f"## 🤖 {title.strip()}",
        "",
        "### TL;DR",
        f"- 这是什么:{_kind_label(kind)} GitHub body。",
        "- 结论:授权/共识 artifact 全文已内联,GitHub 正文本身可审计。",
        "- 下一步:按正文中的自包含信息继续处理。",
        "",
        "---",
        "",
        "### 详细说明",
        "",
        "以下内容由只读 `render-github-body` 从本地 artifact 渲染;授权/共识正文已完整内联,本地路径不作为唯一来源。",
        "",
    ]
    for index, (path, text) in enumerate(artifacts, start=1):
        lines.extend(
            [
                "<details>",
                f"<summary>内联 artifact {index}: {html.escape(path.name)}</summary>",
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
                DEBUG_SUMMARY,
                "",
                "这些路径仅供本机调试,不是授权/共识来源:",
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
    """Fail closed when a GitHub body uses local run paths as sole authority.

    Refactor (iter191/issue-191):
    Old pattern: multi-device / multi-loop runtime lacks a single-active-controller guard; GitHub-facing authority/consensus bodies risk referencing local .refactor-loop paths instead of inlining artifacts
    New principle: single active controller lease (no per-work claims, no cross-device floor); strengthen the self-contained github_body.py validator so authority/consensus/plan bodies inline raw artifacts and .refactor-loop/runs/*.md appears only as debug detail
    """

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
        raise GitHubBodyError("local .refactor-loop artifact path is only allowed under 本机调试线索 details")
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
    except (OSError, GitHubBodyError) as exc:
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


def _kind_label(kind: str) -> str:
    return {
        "pr": "PR 描述",
        "design-issue": "design issue",
        "consensus": "共识",
        "authorization": "授权",
        "escalation": "升级",
        "triage": "triage",
    }[kind]


def _has_raw_inline_artifact_details(text: str) -> bool:
    return any(match.group("artifact").strip() for match in INLINE_ARTIFACT_DETAILS_RE.finditer(text))


def _mask_allowed_run_path_sections(text: str) -> str:
    masked = re.sub(r"<details>\s*" + re.escape(DEBUG_SUMMARY) + r".*?</details>", "", text, flags=re.S)
    masked = INLINE_ARTIFACT_DETAILS_RE.sub("", masked)
    return masked


if __name__ == "__main__":
    raise SystemExit(main())
