#!/usr/bin/env python3
"""Checklist tests for source-literal to behavior/projection migration."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
SCRIPT_DIR = SCRIPT_PATH.parent
SKILL_ROOT = SCRIPT_PATH.parents[1]
HELPER = SCRIPT_DIR / "test_support" / "authorization_projection.py"
TARGET_TESTS = (
    SCRIPT_DIR / "test_runtime_exception_authorization_sources.py",
    SCRIPT_DIR / "test_wakeup_plan.py",
    SCRIPT_DIR / "test_comment_monitor.py",
)
RUNTIME_ROOT = SCRIPT_DIR / "codex_refactor_loop"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def imports_for(path: Path) -> set[str]:
    tree = ast.parse(read(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")
    return imports


def string_literals_for(path: Path) -> set[str]:
    tree = ast.parse(read(path))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


class SourceLiteralBehaviorMigrationTests(unittest.TestCase):
    def test_authorization_projection_is_test_only_and_runtime_independent(self) -> None:
        self.assertTrue(HELPER.exists())
        helper_imports = imports_for(HELPER)

        self.assertFalse(any(name == "codex_refactor_loop" or name.startswith("codex_refactor_loop.") for name in helper_imports))
        self.assertIn("ast", helper_imports)

    def test_runtime_does_not_import_test_support_authorization_projection(self) -> None:
        for path in RUNTIME_ROOT.rglob("*.py"):
            with self.subTest(path=path.relative_to(SKILL_ROOT)):
                imports = imports_for(path)
                self.assertNotIn("test_support.authorization_projection", imports)
                self.assertNotIn("authorization_projection", imports)

    def test_targeted_tests_use_projection_helper_for_source_regression_migration(self) -> None:
        for path in TARGET_TESTS:
            with self.subTest(path=path.name):
                imports = imports_for(path)
                text = read(path)
                self.assertIn("test_support.authorization_projection", imports)
                self.assertIn("project_", text)

    def test_targeted_tests_no_longer_lock_known_private_source_lines(self) -> None:
        forbidden_literals = {
            "monitor.list_in_flight_codex_lines()",
            "if monitor is None:\n        return False",
            "CONSENSUS_JUDGE_LOG_RE.fullmatch(log_path.name)",
            "CONSENSUS_JUDGE_ARTIFACT_RE.fullmatch(artifact.name)",
            "serialize_conflicting_consensus_implementation_actions(actions)",
            "latest_reviewer_heads(",
            "latest_valid_reviewer_rounds(",
            "pending_or_fresh_review_evidence_exists(",
            "dead_reviewer_roles(",
            "reviewer_roles_with_evidence(",
            "valid_required_review_round_complete(",
            "by_role: dict[str, tuple[int, str]]",
            "pending_review_spawn_exists(repo_root, item.number)",
            "suppress_stale_unexecutable_actions(actions, repo_root=repo_root, gh_items=gh_items, gh_items_loaded=gh_items_loaded)",
            'controller_action == "publish_implementation_output"',
            'controller_action == "close_managed_item_from_drop_marker"',
            'action["status_only"] = True',
            "clean :ok stale-base belongs to publish recovery, not redispatch",
            'getattr(state, "reason", "") == "stale_base"',
            'replace(state, status="publish_ready")',
            "source_marker = str(closed.get(\"source_marker\") or \"\")",
            'os.environ.get("COMMENT_MONITOR_LOOKBACK", "")',
            'raw.startswith("updated:")',
            'return f"updated:>={raw}"',
            'os.environ.get("STATE_FILE"',
            'os.environ.get("INTERVAL"',
            '"post-banner": CommandSpec',
            'self._require_owner_or_raise("post-banner")',
        }
        for path in TARGET_TESTS:
            literals = string_literals_for(path)
            with self.subTest(path=path.name):
                self.assertTrue(forbidden_literals.isdisjoint(literals))


if __name__ == "__main__":
    unittest.main()
