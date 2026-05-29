#!/usr/bin/env python3
"""Behavior tests for packaged GitHub status banner helpers."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from codex_refactor_loop import banners
from codex_refactor_loop.banners import BannerRequest
from codex_refactor_loop.context import LoopContext


REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_BANNERS = REPO_ROOT / "skills" / "codex-refactor-loop" / "scripts" / "codex_refactor_loop" / "banners.py"


class BannerPackageTests(unittest.TestCase):
    def request(self, **overrides: object) -> BannerRequest:
        values = {
            "target": "160",
            "kind": "pr",
            "role": "implement",
            "detail": "phase9 issue160 parity",
            "log": "/tmp/refactor-loop/implement-160.log",
            "cd": "/repo/.worktrees/issue160",
            "stall": 180,
        }
        values.update(overrides)
        return BannerRequest(**values)

    def test_build_status_banner_preserves_legacy_body_tokens(self) -> None:
        body = banners.build_status_banner(self.request())

        self.assertTrue(body.startswith("## 📊 状态卡片 — implement 派出\n"))
        for required in (
            "| 阶段 | **派出 codex(role=`implement`)** |",
            "| codex log | `implement-160.log` |",
            "| 工作目录 | `/repo/.worktrees/issue160` |",
            "| no-output stall window | 180s(~3 min 无输出窗口) |",
            "| 上下文 | phase9 issue160 parity |",
            "IMPLEMENT_DONE:<cluster>:<status>",
            "| **是否需要人介入** | **❌ 否**(自动推进) |",
            "🤖 controller status banner",
            "⟦AI:AUTO-LOOP⟧",
        ):
            with self.subTest(required=required):
                self.assertIn(required, body)

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

    def test_post_status_banner_writes_body_file_posts_and_removes_tempfile(self) -> None:
        captured: dict[str, object] = {}

        def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            body_file = Path(command[-1])
            captured["command"] = command
            captured["body_file"] = body_file
            captured["body"] = body_file.read_text(encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "https://github.com/owner/repo/pull/160#issuecomment-1\n", "")

        url = banners.post_status_banner(self.request(), repo_slug="owner/repo", command_runner=fake_runner)

        self.assertEqual(url, "https://github.com/owner/repo/pull/160#issuecomment-1")
        self.assertEqual(captured["command"][:4], ["gh", "pr", "comment", "160"])
        self.assertIn("⟦AI:AUTO-LOOP⟧", str(captured["body"]))
        self.assertFalse(Path(captured["body_file"]).exists())

    def test_main_preserves_legacy_success_and_failure_stderr_contract(self) -> None:
        success = subprocess.CompletedProcess(["gh"], 0, "https://example.test/comment\n", "")
        stderr = io.StringIO()
        with mock.patch.object(sys, "stderr", stderr):
            code = banners.main(
                [
                    "--banner-target",
                    "160",
                    "--banner-kind",
                    "pr",
                    "--banner-role",
                    "fix",
                    "--banner-detail",
                    "r2",
                    "--log",
                    "/tmp/fix.log",
                    "--cd",
                    "/repo/wt",
                    "--stall",
                    "60",
                ],
                command_runner=lambda command: success,
            )
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "BANNER_POSTED: pr #160 https://example.test/comment\n")

        failure = subprocess.CompletedProcess(["gh"], 1, "", "denied\n")
        stderr = io.StringIO()
        with mock.patch.object(sys, "stderr", stderr):
            code = banners.main(
                [
                    "--banner-target",
                    "160",
                    "--banner-kind",
                    "pr",
                    "--banner-role",
                    "fix",
                    "--log",
                    "/tmp/fix.log",
                    "--cd",
                    "/repo/wt",
                    "--stall",
                    "60",
                ],
                command_runner=lambda command: failure,
            )
        self.assertEqual(code, 1)
        self.assertEqual(stderr.getvalue(), "FAIL banner post: denied\n")

    def test_host_env_repo_slug_resolution_reuses_loop_context_primitive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            host_env = repo / ".refactor-loop" / "host.env"
            host_env.parent.mkdir(parents=True)
            host_env.write_text(f"REPO_ROOT={repo}\nGH_REPO_SLUG=host/repo\n", encoding="utf-8")

            ctx = LoopContext.load(repo_root=repo)

        self.assertEqual(banners.repo_slug_from_context(ctx), "host/repo")

    def test_source_preserves_authorization_and_forbidden_lifecycle_tokens(self) -> None:
        source = PACKAGE_BANNERS.read_text(encoding="utf-8")
        for required in (
            "observability-comment-writers",
            ".refactor-loop/runs/phase9-issue53-r7-judge.md",
            "ROLE_NEXT_STEPS",
            "BANNER_POSTED",
            "FAIL banner post",
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

    def test_module_import_has_no_repo_root_side_effect(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(banners.repo_slug_from_context(env={}), None)


if __name__ == "__main__":
    unittest.main()
