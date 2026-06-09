#!/usr/bin/env python3
"""Read-only probe for consensus-rnd fixed points in host project rules."""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path


CANONICAL_BODY = """## 共识研发不动点（由 consensus-rnd 管理）

- FI-001 AI 产物默认不可信；进入主线前必须经过独立检查，至少包含共识、review 或自动验证中的适用组合。
- FI-002 Host 事实必须由 host 配置或 host 规则注入；通用 skill / engine 不硬编码具体项目、组织、路径、分支或人员事实；skill-private runtime directories such as `.refactor-loop/` must not become host production configuration or ledger SSOT.
- FI-003 稳定核心保持小而可审计；高频变化留在 host 规则、prompt、脚本或扩展层，不下沉为核心不变量。
- FI-004 跨进程、跨 turn 或跨节点的事实必须有权威记录；进程内记忆、cache、临时变量不能冒充事实源。
- FI-005 边界优先于便利；职责、层级、协议和状态所有权必须清楚，禁止用中间层快捷方式绕过主链路。
- FI-006 变更必须可验证且基于 evidence；失败、缺口和越界承诺要显式暴露，禁止用静默假设或禁用测试换取通过。
- FI-007 删除优先；废弃路径直接移除，除非 host 规则明确要求迁移期兼容。
"""

OLD_CANONICAL_BODY = """## 共识研发不动点（由 consensus-rnd 管理）

- FI-001 AI 产物默认不可信；进入主线前必须经过独立检查。
- FI-002 Host 事实必须由 host 配置或 host 规则注入。
- FI-003 稳定核心保持小而可审计。
"""

START_RE = re.compile(
    r"<!-- consensus-rnd:foundational-invariants:start version=1 "
    r"sha256=([0-9a-f]{64}) -->"
)
END_MARKER = "<!-- consensus-rnd:foundational-invariants:end -->"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


CANONICAL_HASH = sha256_text(CANONICAL_BODY)
KNOWN_CANONICAL_HASHES = frozenset({sha256_text(OLD_CANONICAL_BODY)})


class FixedPointError(Exception):
    """Raised when the managed block cannot be safely inspected."""


@dataclass(frozen=True)
class ProjectRulesFixedPointReport:
    """refactor helper, no behavior change: immutable probe result payload."""

    status: str
    reason: str
    target: Path
    target_relative: Path
    original_text: str
    proposed_text: str | None = None
    detail: str = ""
    artifact: Path | None = None

    @property
    def is_current(self) -> bool:
        return self.status == "current"

    @property
    def needs_patch(self) -> bool:
        return self.status == "patch-required" and self.proposed_text is not None


class ProjectRulesPatchArtifact:
    def __init__(self, repo_root: Path, filename: str = "project-rules-fixed-point.patch") -> None:
        self.repo_root = repo_root
        self.path = repo_root / ".refactor-loop" / "runs" / filename

    def write(self, report: ProjectRulesFixedPointReport) -> Path:
        if report.proposed_text is None:
            raise FixedPointError("patch artifact requires proposed project rules text")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        original_name = str(report.target_relative)
        updated_name = str(report.target_relative)
        proposed_text = _ensure_trailing_newline(report.proposed_text)
        patch = "".join(
            difflib.unified_diff(
                report.original_text.splitlines(keepends=True),
                proposed_text.splitlines(keepends=True),
                fromfile=f"a/{original_name}",
                tofile=f"b/{updated_name}",
                lineterm="\n",
            )
        )
        patch = _ensure_trailing_newline(patch)
        self.path.write_text(patch, encoding="utf-8")
        return self.path


class ProjectRulesFixedPointProbe:
    def __init__(self, repo_root: str, project_rules: str | None = None) -> None:
        self.repo_root = self._resolve_repo_root(repo_root)
        self.target = self._resolve_target(project_rules if project_rules else "CLAUDE.md")
        self.target_relative = self.target.relative_to(self.repo_root)

    @classmethod
    def from_env(cls) -> "ProjectRulesFixedPointProbe":
        return cls(os.environ.get("REPO_ROOT", ""), os.environ.get("PROJECT_RULES"))

    def inspect(self) -> ProjectRulesFixedPointReport:
        original = self._read_target()
        return self._inspect_text(original)

    def _resolve_repo_root(self, repo_root: str) -> Path:
        if not repo_root:
            raise FixedPointError("REPO_ROOT is required")
        root = Path(repo_root).expanduser().resolve()
        if not root.is_dir():
            raise FixedPointError(f"REPO_ROOT is not a directory: {root}")
        return root

    def _resolve_target(self, project_rules: str) -> Path:
        raw = Path(project_rules)
        if any(part == ".." for part in raw.parts):
            raise FixedPointError(f"PROJECT_RULES must not contain '..': {project_rules}")
        target = (raw if raw.is_absolute() else self.repo_root / raw).expanduser().resolve()
        if not target.is_relative_to(self.repo_root):
            raise FixedPointError(f"PROJECT_RULES escapes REPO_ROOT: {project_rules}")
        return target

    def _read_target(self) -> str:
        try:
            text = self.target.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise FixedPointError(f"PROJECT_RULES file does not exist: {self.target}") from exc
        except OSError as exc:
            raise FixedPointError(f"PROJECT_RULES file is unreadable: {self.target}: {exc}") from exc
        if text == "":
            raise FixedPointError(f"PROJECT_RULES file is empty: {self.target}")
        return text

    def _inspect_text(self, text: str) -> ProjectRulesFixedPointReport:
        starts = list(START_RE.finditer(text))
        end_count = text.count(END_MARKER)
        if len(starts) != end_count:
            return self._blocked_report(text, "invalid-marker", "managed marker pair is missing or unbalanced")
        if len(starts) > 1:
            return self._blocked_report(text, "invalid-marker", "duplicate managed marker blocks are not allowed")
        if not starts:
            return self._patch_report(text, text + "\n\n" + self._managed_block(), "missing")

        start = starts[0]
        end_index = text.find(END_MARKER, start.end())
        if end_index < 0:
            return self._blocked_report(text, "invalid-marker", "managed end marker is missing")

        enclosed = text[start.end() + 1:end_index]
        enclosed_hash = sha256_text(enclosed)
        marker_hash = start.group(1)
        if marker_hash != enclosed_hash:
            return self._blocked_report(text, "tampered", "managed block hash mismatch; refusing to apply repair")
        if enclosed_hash == CANONICAL_HASH:
            return ProjectRulesFixedPointReport(
                status="current",
                reason="current",
                target=self.target,
                target_relative=self.target_relative,
                original_text=text,
            )
        if enclosed_hash not in KNOWN_CANONICAL_HASHES:
            return self._blocked_report(text, "unknown-old", "unknown managed block version; refusing to apply repair")

        proposed = text[: start.start()] + self._managed_block() + text[end_index + len(END_MARKER):]
        return self._patch_report(text, proposed, "known-old")

    def _patch_report(self, original: str, proposed: str, reason: str) -> ProjectRulesFixedPointReport:
        return ProjectRulesFixedPointReport(
            status="patch-required",
            reason=reason,
            target=self.target,
            target_relative=self.target_relative,
            original_text=original,
            proposed_text=proposed,
        )

    def _blocked_report(self, original: str, reason: str, detail: str) -> ProjectRulesFixedPointReport:
        return ProjectRulesFixedPointReport(
            status="blocked",
            reason=reason,
            target=self.target,
            target_relative=self.target_relative,
            original_text=original,
            detail=detail,
        )

    def _managed_block(self) -> str:
        return (
            f"<!-- consensus-rnd:foundational-invariants:start version=1 sha256={CANONICAL_HASH} -->\n"
            f"{CANONICAL_BODY}"
            f"{END_MARKER}"
        )


def _ensure_trailing_newline(text: str) -> str:
    if text.endswith("\n"):
        return text
    return f"{text}\n"


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        probe = ProjectRulesFixedPointProbe.from_env()
        report = probe.inspect()
    except FixedPointError as exc:
        sys.stderr.write(f"PROJECT_RULES_FIXED_POINT_ERROR: {exc}\n")
        return 1
    if report.is_current:
        sys.stdout.write("PROJECT_RULES_FIXED_POINT:current\n")
        return 0
    if report.needs_patch:
        artifact = ProjectRulesPatchArtifact(probe.repo_root).write(report)
        relative_artifact = artifact.relative_to(probe.repo_root)
        sys.stdout.write(
            f"PROJECT_RULES_FIXED_POINT:patch-required artifact={relative_artifact} reason={report.reason}\n"
        )
        return 1
    sys.stderr.write(f"PROJECT_RULES_FIXED_POINT_ERROR: reason={report.reason} {report.detail}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
