#!/usr/bin/env python3
"""Test-only source language policy guard."""

from __future__ import annotations

import ast
import io
import tokenize
import unittest
from dataclasses import dataclass
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]
SKILL_ROOT = SCRIPT_PATH.parents[1]
PYTHON_SOURCE_ROOTS = (
    REPO_ROOT / ".github" / "scripts",
    SKILL_ROOT / "scripts" / "codex_refactor_loop",
)
REF_HISTORY_TOKENS = ("Refactor (", "Old pattern", "New principle")
HAN_START = "\u4e00"
HAN_END = "\u9fff"
LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical"}
ERROR_TYPES = {"Exception", "RuntimeError", "ValueError", "KeyError", "TypeError", "SystemExit"}
COMMIT_TEMPLATE_NAMES = {"commit_body", "commit_message", "body_lines"}


@dataclass(frozen=True)
class Finding:
    relative_path: str
    owner: str
    line: int
    reason: str
    text: str


@dataclass(frozen=True)
class AllowlistEntry:
    relative_path: str
    owner: str
    reason: str


ALLOWLIST: tuple[AllowlistEntry, ...] = (
    AllowlistEntry("skills/codex-refactor-loop/scripts/codex_refactor_loop/banners.py", "ROLE_NEXT_STEPS", "GitHub banner body is intentionally Chinese user-facing output"),
    AllowlistEntry("skills/codex-refactor-loop/scripts/codex_refactor_loop/banners.py", "build_status_banner", "GitHub banner body is intentionally Chinese user-facing output"),
    AllowlistEntry("skills/codex-refactor-loop/scripts/codex_refactor_loop/github_body.py", "DEBUG_SUMMARY", "debug details summary is intentional Chinese GitHub output"),
    AllowlistEntry("skills/codex-refactor-loop/scripts/codex_refactor_loop/github_body.py", "AUTHORITY_PATH_RE", "validator regex recognizes Chinese authority labels in GitHub bodies"),
    AllowlistEntry("skills/codex-refactor-loop/scripts/codex_refactor_loop/github_body.py", "INLINE_ARTIFACT_DETAILS_RE", "validator regex recognizes Chinese inline artifact details"),
    AllowlistEntry("skills/codex-refactor-loop/scripts/codex_refactor_loop/github_body.py", "render_github_body", "self-contained GitHub body text is intentionally Chinese user-facing output"),
    AllowlistEntry("skills/codex-refactor-loop/scripts/codex_refactor_loop/github_body.py", "validate_self_contained_github_body", "validator error references intentional Chinese debug heading"),
    AllowlistEntry("skills/codex-refactor-loop/scripts/codex_refactor_loop/github_body.py", "_kind_label", "GitHub body kind labels are intentionally Chinese user-facing output"),
    AllowlistEntry("skills/codex-refactor-loop/scripts/codex_refactor_loop/labels.py", "CLEANUP_ONLY_ALIASES", "GitHub label names include legacy Chinese catalog entries"),
    AllowlistEntry("skills/codex-refactor-loop/scripts/codex_refactor_loop/labels.py", "LABEL_SPECS", "GitHub label names include legacy Chinese catalog entries"),
    AllowlistEntry("skills/codex-refactor-loop/scripts/codex_refactor_loop/monitors/comment.py", "CommentMonitor.post_banner.banner_body", "maintainer-facing GitHub notification text is intentionally Chinese"),
    AllowlistEntry("skills/codex-refactor-loop/scripts/codex_refactor_loop/monitors/progress.py", "ProgressReporter.build_body", "progress comments are intentionally Chinese user-facing output"),
    AllowlistEntry("skills/codex-refactor-loop/scripts/codex_refactor_loop/peek.py", "PeekStatusLens.render", "status lens renders existing Chinese labels and user-facing state"),
    AllowlistEntry("skills/codex-refactor-loop/scripts/codex_refactor_loop/project_rules.py", "CANONICAL_BODY", "project-rules fixed point text is intentionally Chinese host-facing policy"),
    AllowlistEntry("skills/codex-refactor-loop/scripts/codex_refactor_loop/project_rules.py", "OLD_CANONICAL_BODY", "legacy project-rules fixed point text is intentionally Chinese host-facing policy"),
)


def has_han(text: str) -> bool:
    return any(HAN_START <= char <= HAN_END for char in text)


def has_refactor_history(text: str) -> bool:
    return any(token in text for token in REF_HISTORY_TOKENS)


def source_files() -> list[Path]:
    files: list[Path] = []
    for root in PYTHON_SOURCE_ROOTS:
        files.extend(path for path in root.rglob("*.py") if path.name != "degradation.py")
    return sorted(files)


def relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parent_map: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_map[child] = parent
    return parent_map


def literal_owner(node: ast.Constant, parent_map: dict[ast.AST, ast.AST]) -> str:
    parts: list[str] = []
    target: ast.AST | None = node
    while target is not None:
        if isinstance(target, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            parts.append(target.name)
        assign_parent = parent_map.get(target)
        if isinstance(assign_parent, ast.Assign):
            for assigned in assign_parent.targets:
                if isinstance(assigned, ast.Name):
                    parts.append(assigned.id)
        elif isinstance(assign_parent, ast.AnnAssign) and isinstance(assign_parent.target, ast.Name):
            parts.append(assign_parent.target.id)
        target = assign_parent
    return ".".join(reversed(parts)) or "module-string"


def call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def nearest_call(node: ast.AST, parent_map: dict[ast.AST, ast.AST]) -> ast.Call | None:
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, ast.Call):
            return current
        current = parent_map.get(current)
    return None


def assigned_names(node: ast.AST, parent_map: dict[ast.AST, ast.AST]) -> set[str]:
    parent = parent_map.get(node)
    names: set[str] = set()
    if isinstance(parent, ast.Assign):
        for target in parent.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    elif isinstance(parent, ast.AnnAssign) and isinstance(parent.target, ast.Name):
        names.add(parent.target.id)
    return names


def is_docstring_node(node: ast.Constant, parent_map: dict[ast.AST, ast.AST]) -> bool:
    parent = parent_map.get(node)
    grandparent = parent_map.get(parent) if parent is not None else None
    if not isinstance(parent, ast.Expr):
        return False
    if not isinstance(grandparent, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    return bool(grandparent.body and grandparent.body[0] is parent)


def is_selected_string(node: ast.Constant, parent_map: dict[ast.AST, ast.AST]) -> bool:
    if is_docstring_node(node, parent_map):
        return True
    call = nearest_call(node, parent_map)
    if call is not None and call_name(call.func) in LOG_METHODS | ERROR_TYPES:
        return True
    if assigned_names(node, parent_map) & COMMIT_TEMPLATE_NAMES:
        return True
    return False


def comment_findings(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    with tokenize.open(path) as fh:
        tokens = tokenize.generate_tokens(fh.readline)
        for token in tokens:
            if token.type != tokenize.COMMENT:
                continue
            text = token.string
            if has_han(text):
                findings.append(Finding(relative(path), "comment", token.start[0], "han-comment", text.strip()))
            if has_refactor_history(text):
                findings.append(Finding(relative(path), "comment", token.start[0], "refactor-history-comment", text.strip()))
    return findings


def string_findings(path: Path) -> list[Finding]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    parent_map = build_parent_map(tree)
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        text = node.value
        owner = literal_owner(node, parent_map)
        if has_han(text):
            reason = "han-docstring" if is_docstring_node(node, parent_map) else "han-string"
            if is_selected_string(node, parent_map) and reason != "han-docstring":
                reason = "han-selected-string"
            findings.append(Finding(relative(path), owner, node.lineno, reason, text[:160]))
        if has_refactor_history(text):
            findings.append(Finding(relative(path), owner, node.lineno, "refactor-history-string", text[:160]))
    return findings


def scan_python_source_language(repo_root: Path = REPO_ROOT) -> list[Finding]:
    del repo_root
    findings: list[Finding] = []
    for path in source_files():
        findings.extend(comment_findings(path))
        findings.extend(string_findings(path))
    return [finding for finding in findings if not is_allowlisted(finding)]


def is_allowlisted(finding: Finding) -> bool:
    return any(
        entry.relative_path == finding.relative_path and finding.owner.startswith(entry.owner)
        for entry in ALLOWLIST
    )


class SourceLanguagePolicyTests(unittest.TestCase):
    def test_scan_python_source_language_is_clean(self) -> None:
        findings = scan_python_source_language(REPO_ROOT)
        details = "\n".join(f"{f.relative_path}:{f.line}:{f.owner}:{f.reason}:{f.text}" for f in findings[:50])
        self.assertEqual([], findings, details)

    def test_scanner_rejects_han_comments_docstrings_and_refactor_history(self) -> None:
        sample = io.StringIO(
            '"""中文 docstring"""\n'
            "# Refactor (iter1/example): Old pattern: broken\n"
            "def f():\n"
            "    raise ValueError('中文 error')\n"
        )
        tokens = list(tokenize.generate_tokens(sample.readline))
        self.assertTrue(any(token.type == tokenize.COMMENT and has_refactor_history(token.string) for token in tokens))
        self.assertTrue(has_han("中文 docstring"))
        self.assertTrue(has_han("中文 error"))

    def test_allowlist_entries_match_current_literals(self) -> None:
        raw_findings: list[Finding] = []
        for path in source_files():
            raw_findings.extend(comment_findings(path))
            raw_findings.extend(string_findings(path))
        for entry in ALLOWLIST:
            with self.subTest(entry=entry):
                self.assertTrue(
                    any(f.relative_path == entry.relative_path and f.owner.startswith(entry.owner) for f in raw_findings),
                    entry,
                )


if __name__ == "__main__":
    unittest.main()
