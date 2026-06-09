#!/usr/bin/env python3
"""Behavior tests for packaged GitHub status banner helpers."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from codex_refactor_loop import banners
from codex_refactor_loop.banners import BannerRequest


REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_BANNERS = REPO_ROOT / "skills" / "consensus-loop" / "scripts" / "codex_refactor_loop" / "banners.py"


class BannerPackageTests(unittest.TestCase):
    def request(self, **overrides: object) -> BannerRequest:
        values = {
            "target": "160",
            "kind": "pr",
            "role": "implement",
            "detail": "phase9 issue160 parity",
            "log": "/tmp/refactor-loop/implement-160.log",
            "stall": 180,
        }
        values.update(overrides)
        return BannerRequest(**values)

    def test_build_status_banner_preserves_status_tokens_without_machine_workdir(self) -> None:
        body = banners.build_status_banner(self.request())

        self.assertTrue(body.startswith("## 📊 状态卡片 — implement 派出\n"))
        for required in (
            "| 阶段 | **派出 codex(role=`implement`)** |",
            "| codex log | `implement-160.log` |",
            "| total wall-clock timeout | 180s(~3 min) |",
            "| 上下文 | phase9 issue160 parity |",
            "IMPLEMENT_DONE:<cluster>:<status>",
            "| **是否需要人介入** | **❌ 否**(自动推进) |",
            "🤖 controller status banner",
            "⟦AI:AUTO-LOOP⟧",
        ):
            with self.subTest(required=required):
                self.assertIn(required, body)
        for forbidden in ("工作目录", "/repo/", "request.cd"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, body)

    def test_build_status_banner_uses_none_for_empty_detail_and_all_legacy_roles(self) -> None:
        for role, marker in {
            "test-add": "TEST_ADD_DONE:...",
            "fix": "FIX_DONE:...",
            "reviewer": "reject=0 + approve>=1 -> merge",
            "implement": "IMPLEMENT_DONE:<cluster>:<status>",
            "solver": "SOLVER_DONE:...",
            "judge": "META_JUDGE_DONE:...",
            "reflector": "META_RESOLVED:<kind>",
            "audit": "AUDIT_DONE:...:<N>",
        }.items():
            with self.subTest(role=role):
                body = banners.build_status_banner(self.request(role=role, detail=""))
                self.assertIn("| 上下文 | (none) |", body)
                self.assertIn(marker, body)

    def test_gh_comment_command_preserves_issue_pr_comment_allowlist_and_repo_slug(self) -> None:
        command = banners.gh_comment_command(self.request(kind="issue", target="161"), Path("/tmp/body.md"), "owner/repo")

        self.assertEqual(
            command,
            ["gh", "issue", "comment", "161", "--repo", "owner/repo", "--body-file", "/tmp/body.md"],
        )

    def test_source_preserves_authorization_and_forbidden_lifecycle_tokens(self) -> None:
        source = PACKAGE_BANNERS.read_text(encoding="utf-8")
        for required in (
            "observability-comment-writers",
            "skills/consensus-loop/authorizations/runtime-exceptions.md#observability-comment-writers-53",
            "ROLE_NEXT_STEPS",
            "controller status banner",
            "⟦AI:AUTO-LOOP⟧",
            "TEST_ADD_DONE",
            "FIX_DONE",
            "IMPLEMENT_DONE",
            "SOLVER_DONE",
            "META_JUDGE_DONE",
            "META_RESOLVED",
            "AUDIT_DONE",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)

        for forbidden in (
            "gh issue close",
            "gh pr close",
            "gh pr create",
            "gh pr merge",
            "gh issue edit",
            "gh pr edit",
            "gh release",
            "git commit",
            "git push",
            "git tag",
            "--add-label",
            "--remove-label",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_source_regression_status_banner_has_no_raw_cd_surface(self) -> None:
        source = PACKAGE_BANNERS.read_text(encoding="utf-8")
        for forbidden in (
            "request.cd",
            "| 工作目录 |",
            "cd: str",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_module_import_has_no_repo_root_side_effect(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIn("implement", banners.ROLE_NEXT_STEPS)

    def test_banner_module_has_no_public_posting_entrypoint_or_env_fallback(self) -> None:
        source = PACKAGE_BANNERS.read_text(encoding="utf-8")
        for required in (
            "def build_status_banner(",
            "def gh_comment_command(",
            "AUTO_LOOP_SENTINEL",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)
        for forbidden in (
            "def main(",
            "argparse",
            "load_optional_context",
            "post_status_banner(",
            "repo_slug_from_context",
            "repo_slug_from_env",
            "BANNER_POSTED",
            "FAIL banner post",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
