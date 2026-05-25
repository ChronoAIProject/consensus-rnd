#!/usr/bin/env python3
"""Ensure the host project rules file carries consensus-rnd fixed points."""

from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
from pathlib import Path


CANONICAL_BODY = """## 共识研发不动点（由 consensus-rnd 管理）

- FI-001 AI 产物默认不可信；进入主线前必须经过独立检查，至少包含共识、review 或自动验证中的适用组合。
- FI-002 Host 事实必须由 host 配置或 host 规则注入；通用 skill / engine 不硬编码具体项目、组织、路径、分支或人员事实。
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
    """Raised when the managed block cannot be safely ensured."""


class ProjectRulesFixedPointEnsurer:
    # Refactor (iter1/host-claude-md-fixed-points):
    #   Old pattern: host 的 PROJECT_RULES/CLAUDE.md 不保证基础不动点(泛化理论)在场,跑 loop 时基础理论未被可靠加载
    #   New principle: Phase 0 ProjectRulesFixedPointEnsurer 幂等向 $PROJECT_RULES 写入带 sentinel 的 managed 不动点区块(consensus:minimal,不覆盖 host 已有内容)
    def __init__(self, repo_root: str, project_rules: str | None = None) -> None:
        self.repo_root = self._resolve_repo_root(repo_root)
        self.project_rules = project_rules if project_rules else "CLAUDE.md"
        self.target = self._resolve_target(self.project_rules)

    @classmethod
    def from_env(cls) -> "ProjectRulesFixedPointEnsurer":
        return cls(os.environ.get("REPO_ROOT", ""), os.environ.get("PROJECT_RULES"))

    def ensure(self) -> str:
        # Refactor (iter1/host-claude-md-fixed-points):
        #   Old pattern: host 的 PROJECT_RULES/CLAUDE.md 不保证基础不动点(泛化理论)在场,跑 loop 时基础理论未被可靠加载
        #   New principle: Phase 0 ProjectRulesFixedPointEnsurer 幂等向 $PROJECT_RULES 写入带 sentinel 的 managed 不动点区块(consensus:minimal,不覆盖 host 已有内容)
        original = self._read_target()
        updated = self._updated_text(original)
        if updated == original:
            return "already-current"
        self._atomic_write(updated)
        return "updated"

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

    def _updated_text(self, text: str) -> str:
        # Refactor (iter1/host-claude-md-fixed-points):
        #   Old pattern: host 的 PROJECT_RULES/CLAUDE.md 不保证基础不动点(泛化理论)在场,跑 loop 时基础理论未被可靠加载
        #   New principle: Phase 0 ProjectRulesFixedPointEnsurer 幂等向 $PROJECT_RULES 写入带 sentinel 的 managed 不动点区块(consensus:minimal,不覆盖 host 已有内容)
        starts = list(START_RE.finditer(text))
        end_count = text.count(END_MARKER)
        if len(starts) != end_count:
            raise FixedPointError("managed marker pair is missing or unbalanced")
        if len(starts) > 1:
            raise FixedPointError("duplicate managed marker blocks are not allowed")
        if not starts:
            return text + "\n\n" + self._managed_block()

        start = starts[0]
        end_index = text.find(END_MARKER, start.end())
        if end_index < 0:
            raise FixedPointError("managed end marker is missing")

        enclosed = text[start.end() + 1:end_index]
        enclosed_hash = sha256_text(enclosed)
        marker_hash = start.group(1)
        if marker_hash != enclosed_hash:
            raise FixedPointError("managed block hash mismatch; refusing to overwrite manual edits")
        if enclosed_hash == CANONICAL_HASH:
            return text
        if enclosed_hash not in KNOWN_CANONICAL_HASHES:
            raise FixedPointError("unknown managed block version; refusing to overwrite")

        return text[: start.start()] + self._managed_block() + text[end_index + len(END_MARKER):]

    def _managed_block(self, body: str = CANONICAL_BODY) -> str:
        body_hash = sha256_text(body)
        return (
            f"<!-- consensus-rnd:foundational-invariants:start version=1 sha256={body_hash} -->\n"
            f"{body}"
            f"{END_MARKER}"
        )

    def _atomic_write(self, text: str) -> None:
        self.target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{self.target.name}.", dir=self.target.parent)
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(tmp_path, self.target)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


def main() -> int:
    try:
        status = ProjectRulesFixedPointEnsurer.from_env().ensure()
    except FixedPointError as exc:
        sys.stderr.write(f"PROJECT_RULES_FIXED_POINT_ERROR: {exc}\n")
        return 1
    sys.stdout.write(f"PROJECT_RULES_FIXED_POINT:{status}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
