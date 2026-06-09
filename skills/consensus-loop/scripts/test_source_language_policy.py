#!/usr/bin/env python3
"""Test-only source language policy guard."""

from __future__ import annotations

import ast
import os
import re
import tokenize
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]
REF_HISTORY_TOKENS = ("Refactor (", "Old pattern", "New principle")
REF_HISTORY_ITER_CLUSTER_RE = re.compile(r"\biter(?:\d+|N)/cluster[A-Za-z0-9_-]*\b")
HAN_START = "\u4e00"
HAN_END = "\u9fff"
LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical"}
ERROR_TYPES = {"Exception", "RuntimeError", "ValueError", "KeyError", "TypeError", "SystemExit"}
COMMIT_TEMPLATE_NAMES = {"commit_body", "commit_message", "body_lines"}
REFACTOR_POLICY_NONE = "none"
REFACTOR_POLICY_SELF_DOC = "self-doc-comment"


@dataclass(frozen=True)
class Finding:
    relative_path: str
    owner: str
    line: int
    reason: str
    text: str
    category: str = "runtime-python"


@dataclass(frozen=True)
class SemanticAllowance:
    relative_path: str
    owner: str
    category: str
    reason: str


SEMANTIC_ALLOWANCES: tuple[SemanticAllowance, ...] = (
    SemanticAllowance("skills/consensus-loop/scripts/codex_refactor_loop/github_body.py", "AUTHORITY_PATH_RE", "named-read-compat-regex", "validator regex recognizes legacy zh authority labels in existing GitHub bodies"),
    SemanticAllowance("skills/consensus-loop/scripts/codex_refactor_loop/github_body.py", "INLINE_ARTIFACT_DETAILS_RE", "named-read-compat-regex", "validator regex recognizes explicit zh inline artifact summaries"),
    SemanticAllowance("skills/consensus-loop/scripts/codex_refactor_loop/runtime_copy.py", "COPY_CATALOG", "explicit-zh-runtime-rendering", "explicit zh runtime copy catalog for host work language"),
    SemanticAllowance("skills/consensus-loop/scripts/codex_refactor_loop/project_rules.py", "CANONICAL_BODY", "project-rules-contract-anchor", "managed CLAUDE.md fixed-point contract text"),
    SemanticAllowance("skills/consensus-loop/scripts/codex_refactor_loop/project_rules.py", "OLD_CANONICAL_BODY", "project-rules-contract-anchor", "legacy managed fixed-point text used for read compatibility"),
    SemanticAllowance("skills/consensus-loop/scripts/codex_refactor_loop/closed_label_reconciler.py", "comment", "historical-self-doc-comment", "existing #238 self-documentation comment retained as historical source"),
    SemanticAllowance("skills/consensus-loop/scripts/codex_refactor_loop/closed_phase_labels.py", "comment", "historical-self-doc-comment", "existing #238 self-documentation comment retained as historical source"),
    SemanticAllowance("skills/consensus-loop/scripts/codex_refactor_loop/checks/degradation.py", "DOC_FORBIDDEN_CONTEXT", "documentation-static-checker", "static checker recognizes forbidden-context terms in docs"),
)


def has_han(text: str) -> bool:
    return any(HAN_START <= char <= HAN_END for char in text)


def has_refactor_history(text: str) -> bool:
    return any(token in text for token in REF_HISTORY_TOKENS) or REF_HISTORY_ITER_CLUSTER_RE.search(text) is not None


def source_files(repo_root: Path = REPO_ROOT) -> list[Path]:
    skill_root = repo_root / "skills" / "consensus-loop"
    python_source_roots = (
        repo_root / ".github" / "scripts",
        skill_root / "scripts" / "codex_refactor_loop",
    )
    files: list[Path] = []
    for root in python_source_roots:
        if not root.exists():
            continue
        files.extend(root.rglob("*.py"))
    return sorted(files)


def prompt_files(repo_root: Path = REPO_ROOT) -> list[Path]:
    prompt_root = repo_root / "skills" / "consensus-loop" / "prompts"
    if not prompt_root.exists():
        return []
    return sorted(prompt_root.glob("*.md"))


def relative(path: Path, repo_root: Path = REPO_ROOT) -> str:
    return path.relative_to(repo_root).as_posix()


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


def normalize_refactor_comment_policy(raw: str | None = None) -> str:
    value = (raw if raw is not None else os.environ.get("HOST_REFACTOR_COMMENT_POLICY", "")).strip()
    if not value or value == "default":
        return REFACTOR_POLICY_NONE
    if value in {REFACTOR_POLICY_NONE, REFACTOR_POLICY_SELF_DOC}:
        return value
    raise ValueError(f"invalid HOST_REFACTOR_COMMENT_POLICY: {value}")


def comment_findings(path: Path, repo_root: Path = REPO_ROOT, *, forbid_refactor_history: bool = True) -> list[Finding]:
    findings: list[Finding] = []
    with tokenize.open(path) as fh:
        tokens = tokenize.generate_tokens(fh.readline)
        for token in tokens:
            if token.type != tokenize.COMMENT:
                continue
            text = token.string
            category = python_comment_category(path, repo_root)
            if has_han(text):
                findings.append(Finding(relative(path, repo_root), "comment", token.start[0], "han-comment", text.strip(), category))
            if forbid_refactor_history and has_refactor_history(text):
                findings.append(Finding(relative(path, repo_root), "comment", token.start[0], "refactor-history-comment", text.strip(), category))
    return findings


def string_findings(path: Path, repo_root: Path = REPO_ROOT, *, forbid_refactor_history: bool = True) -> list[Finding]:
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
            findings.append(Finding(relative(path, repo_root), owner, node.lineno, reason, text[:160], python_string_category(path, owner, repo_root)))
        if forbid_refactor_history and has_refactor_history(text):
            findings.append(Finding(relative(path, repo_root), owner, node.lineno, "refactor-history-string", text[:160], "runtime-python-string"))
    return findings


def python_comment_category(path: Path, repo_root: Path = REPO_ROOT) -> str:
    rel = relative(path, repo_root)
    for entry in SEMANTIC_ALLOWANCES:
        if entry.relative_path == rel and entry.owner == "comment":
            return entry.category
    return "runtime-python-comment"


def python_string_category(path: Path, owner: str, repo_root: Path = REPO_ROOT) -> str:
    rel = relative(path, repo_root)
    for entry in SEMANTIC_ALLOWANCES:
        if entry.relative_path == rel and owner.startswith(entry.owner):
            return entry.category
    return "runtime-python-string"


def prompt_line_findings(path: Path, repo_root: Path = REPO_ROOT) -> list[Finding]:
    findings: list[Finding] = []
    in_fence = False
    in_html_comment = False
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if "<!--" in stripped:
            in_html_comment = True
        category = prompt_line_category(path, line, in_fence=in_fence, in_html_comment=in_html_comment)
        if has_han(line) and category == "active-prompt-instruction":
            findings.append(
                Finding(
                    relative(path, repo_root),
                    "prompt-line",
                    line_number,
                    "han-active-prompt-instruction",
                    line[:160],
                    category,
                )
            )
        if "-->" in stripped:
            in_html_comment = False
    return findings


def prompt_line_category(path: Path, line: str, *, in_fence: bool, in_html_comment: bool) -> str:
    del path
    stripped = line.strip()
    if in_fence:
        return "prompt-fenced-example"
    if in_html_comment:
        return "prompt-historical-comment"
    if not stripped:
        return "blank"
    if stripped == "⟦AI:AUTO-LOOP⟧":
        return "sentinel-literal"
    if "MarkerEmissionContract" in stripped or re.search(r"\b[A-Z_]+_DONE:", stripped):
        return "protocol-literal"
    if "${HOST_WORK_LANGUAGE}" in stripped or "$HOST_WORK_LANGUAGE" in stripped:
        return "host-language-contract-anchor"
    if "`" in stripped and any(token in stripped for token in ("CLAUDE.md", "AGENTS.md", "PROJECT_RULES", "HOST_", "gh ", ".refactor-loop", "SCOPE_EXTEND", "IMPLEMENT_DONE", "SOLVER_DONE", "META_JUDGE_DONE", "VERIFY_DONE", "TEST_ADD_DONE", "FIX_DONE")):
        return "protocol-literal"
    return "active-prompt-instruction"


def scan_python_source_language(repo_root: Path = REPO_ROOT, *, refactor_comment_policy: str | None = None) -> list[Finding]:
    policy = normalize_refactor_comment_policy(refactor_comment_policy)
    forbid_refactor_history = policy == REFACTOR_POLICY_NONE
    findings: list[Finding] = []
    for path in source_files(repo_root):
        findings.extend(comment_findings(path, repo_root, forbid_refactor_history=forbid_refactor_history))
        findings.extend(string_findings(path, repo_root, forbid_refactor_history=forbid_refactor_history))
    return [finding for finding in findings if not is_allowlisted(finding)]


def scan_prompt_language(repo_root: Path = REPO_ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for path in prompt_files(repo_root):
        findings.extend(prompt_line_findings(path, repo_root))
    return findings


def is_allowlisted(finding: Finding) -> bool:
    return any(
        entry.relative_path == finding.relative_path
        and finding.owner.startswith(entry.owner)
        and finding.category == entry.category
        for entry in SEMANTIC_ALLOWANCES
    )


class SourceLanguagePolicyTests(unittest.TestCase):
    def test_source_files_includes_degradation_checker(self) -> None:
        expected = (
            REPO_ROOT
            / "skills"
            / "consensus-loop"
            / "scripts"
            / "codex_refactor_loop"
            / "checks"
            / "degradation.py"
        )

        self.assertIn(expected, source_files(REPO_ROOT))

    def test_renderer_owned_chinese_copy_lives_only_in_runtime_catalog(self) -> None:
        findings = [
            finding
            for finding in comment_findings(REPO_ROOT / "skills/consensus-loop/scripts/codex_refactor_loop/github_body.py", REPO_ROOT)
            + string_findings(REPO_ROOT / "skills/consensus-loop/scripts/codex_refactor_loop/github_body.py", REPO_ROOT)
            + comment_findings(REPO_ROOT / "skills/consensus-loop/scripts/codex_refactor_loop/banners.py", REPO_ROOT)
            + string_findings(REPO_ROOT / "skills/consensus-loop/scripts/codex_refactor_loop/banners.py", REPO_ROOT)
            + comment_findings(REPO_ROOT / "skills/consensus-loop/scripts/codex_refactor_loop/monitors/comment.py", REPO_ROOT)
            + string_findings(REPO_ROOT / "skills/consensus-loop/scripts/codex_refactor_loop/monitors/comment.py", REPO_ROOT)
            + comment_findings(REPO_ROOT / "skills/consensus-loop/scripts/codex_refactor_loop/peek.py", REPO_ROOT)
            + string_findings(REPO_ROOT / "skills/consensus-loop/scripts/codex_refactor_loop/peek.py", REPO_ROOT)
            + comment_findings(REPO_ROOT / "skills/consensus-loop/scripts/codex_refactor_loop/controller_actions.py", REPO_ROOT)
            + string_findings(REPO_ROOT / "skills/consensus-loop/scripts/codex_refactor_loop/controller_actions.py", REPO_ROOT)
            if has_han(finding.text)
        ]
        unallowlisted = [finding for finding in findings if not is_allowlisted(finding)]

        self.assertEqual([], unallowlisted)
        self.assertTrue(
            all(
                finding.relative_path.endswith("runtime_copy.py")
                or finding.relative_path.endswith("github_body.py")
                for finding in findings
            )
        )

    def test_scan_python_source_language_is_clean(self) -> None:
        findings = scan_python_source_language(REPO_ROOT)
        details = "\n".join(f"{f.relative_path}:{f.line}:{f.owner}:{f.category}:{f.reason}:{f.text}" for f in findings[:50])
        self.assertEqual([], findings, details)

    def test_active_prompt_instruction_bodies_are_han_free(self) -> None:
        findings = scan_prompt_language(REPO_ROOT)
        details = "\n".join(f"{f.relative_path}:{f.line}:{f.reason}:{f.text}" for f in findings[:80])
        self.assertEqual([], findings, details)

    def test_scanner_rejects_han_comments_docstrings_and_refactor_history(self) -> None:
        with TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            source_path = repo_root / ".github" / "scripts" / "prohibited_sample.py"
            source_path.parent.mkdir(parents=True)
            source_path.write_text(
                '"""Chinese text in source: 中文 docstring"""\n'
                "# Refactor (iter1/example): Old pattern: 中文 comment history\n"
                "# iter3/cluster-016 rationale\n"
                "def run() -> None:\n"
                '    raise ValueError("Chinese text in source: 中文 error")\n',
                encoding="utf-8",
            )

            comment_results = comment_findings(source_path, repo_root)
            string_results = string_findings(source_path, repo_root)
            scan_results = scan_python_source_language(repo_root, refactor_comment_policy=REFACTOR_POLICY_NONE)

        expected_path = ".github/scripts/prohibited_sample.py"
        comment_reasons = {(finding.relative_path, finding.line, finding.reason) for finding in comment_results}
        string_reasons = {(finding.relative_path, finding.owner, finding.reason) for finding in string_results}
        scan_reasons = {(finding.relative_path, finding.line, finding.reason) for finding in scan_results}

        self.assertIn((expected_path, 2, "han-comment"), comment_reasons)
        self.assertIn((expected_path, 2, "refactor-history-comment"), comment_reasons)
        self.assertIn((expected_path, 3, "refactor-history-comment"), comment_reasons)
        self.assertIn((expected_path, "module-string", "han-docstring"), string_reasons)
        self.assertIn((expected_path, "run", "han-selected-string"), string_reasons)
        self.assertIn((expected_path, 1, "han-docstring"), scan_reasons)
        self.assertIn((expected_path, 2, "han-comment"), scan_reasons)
        self.assertIn((expected_path, 2, "refactor-history-comment"), scan_reasons)
        self.assertIn((expected_path, 3, "refactor-history-comment"), scan_reasons)
        self.assertIn((expected_path, 5, "han-selected-string"), scan_reasons)

    def test_self_doc_policy_allows_english_refactor_history_comments_only(self) -> None:
        with TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            source_path = repo_root / ".github" / "scripts" / "allowed_self_doc.py"
            source_path.parent.mkdir(parents=True)
            source_path.write_text(
                "# Refactor (iter1/example):\n"
                "#   Old pattern: Previous behavior was hard to review.\n"
                "#   New principle: Keep local rationale readable when explicitly enabled.\n"
                "def run() -> None:\n"
                "    return None\n",
                encoding="utf-8",
            )

            self.assertEqual([], scan_python_source_language(repo_root, refactor_comment_policy=REFACTOR_POLICY_SELF_DOC))
            none_findings = scan_python_source_language(repo_root, refactor_comment_policy=REFACTOR_POLICY_NONE)

        self.assertEqual(
            {(1, "refactor-history-comment"), (2, "refactor-history-comment"), (3, "refactor-history-comment")},
            {(finding.line, finding.reason) for finding in none_findings},
        )

    def test_invalid_refactor_comment_policy_fails_closed(self) -> None:
        with TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            (repo_root / ".github" / "scripts").mkdir(parents=True)

            with self.assertRaises(ValueError):
                scan_python_source_language(repo_root, refactor_comment_policy="maybe")

    def test_allowlist_entries_match_current_literals(self) -> None:
        raw_findings: list[Finding] = []
        for path in source_files():
            raw_findings.extend(comment_findings(path))
            raw_findings.extend(string_findings(path))
        for entry in SEMANTIC_ALLOWANCES:
            with self.subTest(entry=entry):
                self.assertTrue(
                    any(
                        f.relative_path == entry.relative_path
                        and f.owner.startswith(entry.owner)
                        and f.category == entry.category
                        for f in raw_findings
                    ),
                    entry,
                )

    def test_prompt_scanner_rejects_active_han_but_ignores_protocol_examples(self) -> None:
        with TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            prompt_root = repo_root / "skills" / "consensus-loop" / "prompts"
            prompt_root.mkdir(parents=True)
            (prompt_root / "sample.md").write_text(
                "# Title\n"
                "Active instruction 中文\n"
                "```\n"
                "Example 中文\n"
                "```\n"
                "<!-- historical 中文 -->\n"
                "Marker `IMPLEMENT_DONE:<cluster>:<status>` 中文\n"
                "Final sentinel:\n"
                "⟦AI:AUTO-LOOP⟧\n",
                encoding="utf-8",
            )

            findings = scan_prompt_language(repo_root)

        self.assertEqual(
            [("skills/consensus-loop/prompts/sample.md", 2, "han-active-prompt-instruction")],
            [(finding.relative_path, finding.line, finding.reason) for finding in findings],
        )


if __name__ == "__main__":
    unittest.main()
