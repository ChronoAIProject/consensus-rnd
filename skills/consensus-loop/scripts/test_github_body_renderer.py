#!/usr/bin/env python3
"""Behavior and source-regression tests for self-contained GitHub bodies."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from codex_refactor_loop.github_body import GitHubBodyError, render_github_body, validate_self_contained_github_body


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
CLI = SCRIPT_DIR / "consensus-rnd-cli"


class GitHubBodyRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="github-body-test-"))
        self.artifact = self.tmp / "phase9-issue192-r2-judge.md"
        self.artifact.write_text(
            "---\nissue: 192\n---\n\n## Decision\n完整共识正文\n\n⟦AI:AUTO-LOOP⟧\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_renderer_embeds_full_artifact_and_debug_path_only_under_details(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            body = render_github_body(
                kind="consensus",
                title="issue #192 consensus",
                artifact_paths=[self.artifact],
                debug_paths=[".refactor-loop/runs/phase9-issue192-r2-judge.md"],
            )

        self.assertTrue(body.startswith("## 🤖 issue #192 consensus\n"))
        self.assertIn("完整共识正文", body)
        self.assertIn("<summary>Inline artifact 1: phase9-issue192-r2-judge.md</summary>", body)
        self.assertIn("<summary>Local debug clues</summary>", body)
        self.assertIn("`.refactor-loop/runs/phase9-issue192-r2-judge.md`", body)
        self.assertTrue(body.splitlines()[-1] == "⟦AI:AUTO-LOOP⟧")
        validate_self_contained_github_body(body, authority_required=True)

    def test_renderer_preserves_chinese_when_host_language_is_zh(self) -> None:
        with mock.patch.dict("os.environ", {"HOST_WORK_LANGUAGE": "zh"}, clear=True):
            body = render_github_body(
                kind="consensus",
                title="issue #192 共识",
                artifact_paths=[self.artifact],
                debug_paths=[".refactor-loop/runs/phase9-issue192-r2-judge.md"],
            )

        self.assertTrue(body.startswith("## 🤖 issue #192 共识\n"))
        self.assertIn("<summary>内联 artifact 1: phase9-issue192-r2-judge.md</summary>", body)
        self.assertIn("<summary>本机调试线索</summary>", body)

    def test_validator_rejects_path_only_authority(self) -> None:
        body = "## 🤖 bad body\n\n授权:.refactor-loop/runs/phase9-issue192-r1-judge.md\n\n⟦AI:AUTO-LOOP⟧\n"
        with self.assertRaisesRegex(GitHubBodyError, "local .refactor-loop artifact path"):
            validate_self_contained_github_body(body)

    def test_validator_allows_plain_body_without_authority_requirement(self) -> None:
        validate_self_contained_github_body("## 🤖 status\n\nPlain comment.\n\n⟦AI:AUTO-LOOP⟧\n")

    def test_validator_requires_inline_details_for_authority_required_body(self) -> None:
        with self.assertRaisesRegex(GitHubBodyError, "must inline raw artifact text"):
            validate_self_contained_github_body("## 🤖 accepted\n\nConclusion accepted.\n\n⟦AI:AUTO-LOOP⟧\n", authority_required=True)

    def test_validator_rejects_generic_markdown_details_as_authority(self) -> None:
        body = (
            "## 🤖 accepted\n\n"
            "<details>\n"
            "<summary>普通说明</summary>\n\n"
            "```markdown\n完整共识正文\n```\n"
            "</details>\n\n"
            "⟦AI:AUTO-LOOP⟧\n"
        )
        with self.assertRaisesRegex(GitHubBodyError, "must inline raw artifact text"):
            validate_self_contained_github_body(body, authority_required=True)

    def test_validator_rejects_run_path_inside_non_debug_details(self) -> None:
        body = (
            "## 🤖 accepted\n\n"
            "<details>\n"
            "<summary>普通说明</summary>\n\n"
            "授权:.refactor-loop/runs/phase9-issue191-r2-judge.md\n"
            "</details>\n\n"
            "⟦AI:AUTO-LOOP⟧\n"
        )
        with self.assertRaisesRegex(GitHubBodyError, "local .refactor-loop artifact path"):
            validate_self_contained_github_body(body)

    def test_cli_renders_to_stdout_without_mutation_authority(self) -> None:
        host_env = self.tmp / ".config" / "consensus-rnd" / "host.env"
        host_env.parent.mkdir(parents=True)
        host_env.write_text('export HOST_WORK_LANGUAGE="en"\n', encoding="utf-8")
        env = {
            **os.environ,
            "REPO_ROOT": str(self.tmp),
            "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env",
        }
        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "render-github-body",
                "--kind",
                "authorization",
                "--title",
                "authorization card",
                "--artifact",
                str(self.artifact),
                "--debug-path",
                ".refactor-loop/runs/phase9-issue192-r2-judge.md",
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("完整共识正文", result.stdout)
        self.assertIn("<summary>Inline artifact 1: phase9-issue192-r2-judge.md</summary>", result.stdout)
        self.assertTrue(result.stdout.splitlines()[-1] == "⟦AI:AUTO-LOOP⟧")

    def test_cli_rejects_output_path_to_remain_read_only(self) -> None:
        output = self.tmp / "body.md"
        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "render-github-body",
                "--kind",
                "authorization",
                "--title",
                "授权卡片",
                "--artifact",
                str(self.artifact),
                "--output",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertFalse(output.exists())


class GitHubBodySourceRegressionTests(unittest.TestCase):
    def test_cli_registers_renderer_as_read_artifact_only(self) -> None:
        cli = (SCRIPT_DIR / "codex_refactor_loop" / "cli.py").read_text(encoding="utf-8")
        self.assertIn('"render-github-body": CommandSpec(', cli)
        self.assertIn("github_body.main", cli)
        self.assertIn('("read-artifact",)', cli)

    def test_no_new_daemon_or_lifecycle_authority_for_renderer(self) -> None:
        src = (SCRIPT_DIR / "codex_refactor_loop" / "github_body.py").read_text(encoding="utf-8")
        for forbidden in ("subprocess", "run_gh", "Path(args.output).write_text", "add_argument(\"--output\"", "gh ", "git ", "daemon", "write-state", "gh-open", "gh-label"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, src)
        self.assertIn("read-only helper", src)
        self.assertIn("must not write files", src)

    def test_validator_contract_uses_self_contained_authority_literals(self) -> None:
        src = (SCRIPT_DIR / "codex_refactor_loop" / "github_body.py").read_text(encoding="utf-8")
        self.assertNotIn("Refactor (iter191/issue-191):", src)
        self.assertNotIn("Old pattern", src)
        self.assertNotIn("New principle", src)
        self.assertIn("INLINE_ARTIFACT_DETAILS_RE", src)
        self.assertIn("authority body must inline raw artifact text in inline artifact details", src)

    def test_controller_and_triage_call_same_validator(self) -> None:
        controller = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        triage = (SCRIPT_DIR / "codex_refactor_loop" / "triage.py").read_text(encoding="utf-8")
        self.assertIn("from .github_body import", controller)
        self.assertIn("validate_self_contained_github_body", controller)
        self.assertIn("from .github_body import validate_self_contained_github_body", triage)
        self.assertIn("validate_self_contained_github_body", triage)

    def test_prompt_and_skill_document_self_contained_contract(self) -> None:
        rules = (SKILL_DIR / "prompts" / "_github-post-rules.md").read_text(encoding="utf-8")
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for text in (rules, skill):
            with self.subTest(source=text[:20]):
                self.assertIn("GitHub bodies must be self-contained", text) if text is rules else self.assertIn("must inline the cited artifact text", text)
                self.assertTrue(
                    "<details><summary>Local debug clues</summary>" in text
                    or "<details><summary>本机调试线索</summary>" in text
                )
                self.assertTrue("never the only authority source" in text or "never as the only source" in text)
        self.assertNotIn("See [implement summary](./.refactor-loop/runs/implement-<cluster-id>.md)", skill)


if __name__ == "__main__":
    unittest.main()
