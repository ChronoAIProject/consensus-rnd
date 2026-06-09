#!/usr/bin/env python3
"""Behavior tests for host-owned external artifact language selection."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContextError, normalize_host_work_language
from codex_refactor_loop.github_body import render_github_body
from codex_refactor_loop.runtime_copy import copy_for


def has_han(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


class WorkLanguagePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="work-language-test-"))
        self.artifact = self.tmp / "authority.md"
        self.artifact.write_text("authority body\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_missing_empty_and_default_normalize_to_english(self) -> None:
        self.assertEqual("en", normalize_host_work_language(raw=None, env={}))
        self.assertEqual("en", normalize_host_work_language(raw="", env={}))
        self.assertEqual("en", normalize_host_work_language(raw="default", env={}))

    def test_explicit_zh_preserves_chinese_external_artifact_body(self) -> None:
        with mock.patch.dict(os.environ, {"HOST_WORK_LANGUAGE": "zh"}, clear=True):
            body = render_github_body(
                kind="authorization",
                title="授权卡片",
                artifact_paths=[self.artifact],
                debug_paths=[".refactor-loop/runs/authority.md"],
            )

        self.assertIn("### 详细说明", body)
        self.assertIn("结论:授权/共识 artifact 全文已内联", body)
        self.assertIn("<summary>内联 artifact 1: authority.md</summary>", body)
        self.assertIn("<summary>本机调试线索</summary>", body)
        self.assertTrue(body.splitlines()[-1] == "⟦AI:AUTO-LOOP⟧")

    def test_renderer_reads_host_owned_env_file_locator(self) -> None:
        host_env = self.tmp / ".config" / "consensus-rnd" / "host.env"
        host_env.parent.mkdir(parents=True)
        host_env.write_text('export HOST_WORK_LANGUAGE="zh"\n', encoding="utf-8")

        with mock.patch.dict(os.environ, {"CONSENSUS_RND_HOST_ENV": str(host_env)}, clear=True):
            with mock.patch("pathlib.Path.cwd", return_value=self.tmp):
                body = render_github_body(
                    kind="authorization",
                    title="授权卡片",
                    artifact_paths=[self.artifact],
                )

        self.assertIn("### 详细说明", body)
        self.assertIn("<summary>内联 artifact 1: authority.md</summary>", body)

    def test_default_renderer_uses_english_external_artifact_body(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            body = render_github_body(
                kind="authorization",
                title="authorization card",
                artifact_paths=[self.artifact],
                debug_paths=[".refactor-loop/runs/authority.md"],
            )

        self.assertIn("### Details", body)
        self.assertIn("Conclusion: the full authorization/consensus artifact is inline", body)
        self.assertIn("<summary>Inline artifact 1: authority.md</summary>", body)
        self.assertIn("<summary>Local debug clues</summary>", body)
        self.assertNotIn("### 详细说明", body)
        self.assertFalse(has_han(copy_for("github_body", language="en")["conclusion"]))
        self.assertTrue(body.splitlines()[-1] == "⟦AI:AUTO-LOOP⟧")

    def test_invalid_value_fails_closed(self) -> None:
        with self.assertRaisesRegex(LoopContextError, "invalid HOST_WORK_LANGUAGE"):
            normalize_host_work_language(raw="fr", env={})

        with mock.patch.dict(os.environ, {"HOST_WORK_LANGUAGE": "fr"}, clear=True):
            with self.assertRaisesRegex(LoopContextError, "invalid HOST_WORK_LANGUAGE"):
                render_github_body(kind="authorization", title="bad", artifact_paths=[self.artifact])


if __name__ == "__main__":
    unittest.main()
