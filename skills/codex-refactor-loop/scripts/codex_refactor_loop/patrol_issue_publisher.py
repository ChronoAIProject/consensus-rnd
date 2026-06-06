"""Patrol-owned GitHub issue publisher."""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from . import labels as label_catalog
from .context import LoopContext
from .gh_invoke import build_gh_argv


FINGERPRINT_PREFIX = f"{label_catalog.CANONICAL_PREFIX}:patrol:fingerprint:"
PATROL_LABEL_BUNDLE = (
    label_catalog.MANAGED,
    label_catalog.PHASE_DESIGN_SOLVING,
    label_catalog.HUMAN_AUTO,
    label_catalog.canonical_name("triage", "pending"),
)


@dataclass(frozen=True)
class PatrolIssue:
    number: int
    title: str
    body: str
    labels: tuple[str, ...]
    state: str = "open"

    @classmethod
    def from_json(cls, data: object) -> "PatrolIssue | None":
        if not isinstance(data, dict):
            return None
        number = data.get("number")
        title = data.get("title")
        body = data.get("body")
        state = data.get("state", "open")
        raw_labels = data.get("labels", [])
        labels: list[str] = []
        if isinstance(raw_labels, list):
            for item in raw_labels:
                if isinstance(item, str):
                    labels.append(item)
                elif isinstance(item, dict) and isinstance(item.get("name"), str):
                    labels.append(str(item["name"]))
        if not isinstance(number, int) or not isinstance(title, str) or not isinstance(body, str) or not isinstance(state, str):
            return None
        return cls(number=number, title=title, body=body, labels=tuple(labels), state=state)


class PatrolIssuePublisher:
    def __init__(
        self,
        ctx: LoopContext,
        *,
        command_runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.ctx = ctx
        self._run = command_runner or self._default_runner

    def publish(self, *, fingerprint: str, title: str, body: str) -> PatrolIssue:
        if not self.ctx.gh_repo_slug:
            raise RuntimeError("patrol issue publisher requires GH_REPO_SLUG")
        existing = self.find_by_fingerprint(fingerprint)
        body_with_fingerprint = ensure_fingerprint_line(body, fingerprint)
        if existing is None:
            number = self.create_issue(title=title, body=body_with_fingerprint)
            return PatrolIssue(number=number, title=title, body=body_with_fingerprint, labels=PATROL_LABEL_BUNDLE)
        self.update_issue_body(existing.number, body_with_fingerprint)
        return PatrolIssue(
            number=existing.number,
            title=existing.title,
            body=body_with_fingerprint,
            labels=existing.labels,
            state=existing.state,
        )

    def find_by_fingerprint(self, fingerprint: str) -> PatrolIssue | None:
        query = f'repo:{self.ctx.gh_repo_slug} is:issue label:"{label_catalog.MANAGED}" "{fingerprint_marker(fingerprint)}"'
        argv = [
            "gh",
            "issue",
            "list",
            "--state",
            "open",
            "--search",
            query,
            "--json",
            "number,title,body,labels,state",
            "--limit",
            "20",
        ]
        result = self._run(build_gh_argv(self.ctx.gh_repo_slug, argv))
        if result.returncode != 0:
            raise RuntimeError(f"patrol issue search failed: {result.stderr.strip() or result.returncode}")
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise RuntimeError("patrol issue search returned malformed JSON") from exc
        if not isinstance(payload, list):
            raise RuntimeError("patrol issue search returned non-list JSON")
        matches = [
            issue
            for issue in (PatrolIssue.from_json(item) for item in payload)
            if issue is not None and fingerprint_marker(fingerprint) in f"{issue.title}\n{issue.body}"
        ]
        if len(matches) > 1:
            raise RuntimeError(f"patrol fingerprint is ambiguous: {fingerprint}")
        return matches[0] if matches else None

    def create_issue(self, *, title: str, body: str) -> int:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(body)
            body_path = Path(handle.name)
        try:
            argv = [
                "gh",
                "issue",
                "create",
                "--title",
                title,
                "--body-file",
                str(body_path),
            ]
            for label in PATROL_LABEL_BUNDLE:
                argv.extend(["--label", label])
            result = self._run(build_gh_argv(self.ctx.gh_repo_slug, argv))
        finally:
            body_path.unlink(missing_ok=True)
        if result.returncode != 0:
            raise RuntimeError(f"patrol issue create failed: {result.stderr.strip() or result.returncode}")
        number = _issue_number_from_create_output(result.stdout)
        if number is None:
            raise RuntimeError("patrol issue create did not return an issue number")
        return number

    def update_issue_body(self, issue_number: int, body: str) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write(body)
            body_path = Path(handle.name)
        try:
            argv = ["gh", "issue", "edit", str(issue_number), "--body-file", str(body_path)]
            result = self._run(build_gh_argv(self.ctx.gh_repo_slug, argv))
        finally:
            body_path.unlink(missing_ok=True)
        if result.returncode != 0:
            raise RuntimeError(f"patrol issue body update failed: {result.stderr.strip() or result.returncode}")

    def _default_runner(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(argv),
            cwd=str(self.ctx.repo_root),
            env=self.ctx.env_for_subprocess(),
            capture_output=True,
            text=True,
            check=False,
        )


def fingerprint_marker(fingerprint: str) -> str:
    return f"{FINGERPRINT_PREFIX}{fingerprint}"


def ensure_fingerprint_line(body: str, fingerprint: str) -> str:
    marker = fingerprint_marker(fingerprint)
    lines = [line for line in body.rstrip().splitlines() if not line.startswith(FINGERPRINT_PREFIX)]
    lines.append(marker)
    return "\n".join(lines) + "\n"


def _issue_number_from_create_output(output: str) -> int | None:
    for token in reversed(output.replace("/", " ").split()):
        if token.startswith("#") and token[1:].isdigit():
            return int(token[1:])
        if token.isdigit():
            return int(token)
    return None
