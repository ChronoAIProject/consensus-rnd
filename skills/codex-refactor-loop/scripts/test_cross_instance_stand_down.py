#!/usr/bin/env python3
"""Behavior tests for cross-instance stand-down projection."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import labels
from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.cross_instance_stand_down import check_cross_instance_admission


class CrossInstanceStandDownTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="cross-instance-stand-down-"))
        (self.tmp / ".config" / "consensus-rnd").mkdir(parents=True)
        (self.tmp / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})
        self.now = datetime(2026, 6, 9, 1, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fresh_other_ai_controller_comment_stands_down(self) -> None:
        runner = self.runner(
            comments=[
                {
                    "createdAt": self.iso(minutes=-5),
                    "author": {"login": "other-user"},
                    "body": "## 🤖 controller update\n\n⟦AI:AUTO-LOOP⟧\n",
                }
            ]
        )

        result = check_cross_instance_admission(self.ctx, "issue", 77, "current-user", self.now, runner=runner)

        self.assertEqual("stand_down", result.status)
        self.assertEqual("other-user", result.other_login)
        self.assertEqual("comment", result.source)
        self.assertTrue(runner.seen_comments_paginate)

    def test_same_login_and_stale_activity_are_allowed(self) -> None:
        for name, comments in {
            "same-login": [
                {
                    "createdAt": self.iso(minutes=-5),
                    "author": {"login": "current-user"},
                    "body": "## 📊 status\n\n⟦AI:AUTO-LOOP⟧\n",
                }
            ],
            "stale": [
                {
                    "createdAt": self.iso(hours=-3),
                    "author": {"login": "other-user"},
                    "body": "## ✅ done\n\n⟦AI:AUTO-LOOP⟧\n",
                }
            ],
        }.items():
            with self.subTest(name=name):
                result = check_cross_instance_admission(
                    self.ctx,
                    "issue",
                    77,
                    "current-user",
                    self.now,
                    runner=self.runner(comments=comments),
                )
                self.assertEqual("allowed", result.status)

    def test_fresh_other_loop_label_stands_down(self) -> None:
        result = check_cross_instance_admission(
            self.ctx,
            "issue",
            77,
            "current-user",
            self.now,
            runner=self.runner(
                timeline=[
                    {
                        "event": "labeled",
                        "created_at": self.iso(minutes=-3),
                        "actor": {"login": "other-user"},
                        "label": {"name": labels.PHASE_REVIEWING},
                    }
                ]
            ),
        )

        self.assertEqual("stand_down", result.status)
        self.assertEqual("label", result.source)

    def test_fresh_other_loop_label_on_second_timeline_page_stands_down(self) -> None:
        runner = self.runner(
            timeline_pages=[
                [
                    {
                        "event": "labeled",
                        "created_at": self.iso(minutes=-3),
                        "actor": {"login": "current-user"},
                        "label": {"name": labels.PHASE_REVIEWING},
                    }
                ],
                [
                    {
                        "event": "labeled",
                        "created_at": self.iso(minutes=-2),
                        "actor": {"login": "other-user"},
                        "label": {"name": labels.PHASE_IMPLEMENTING},
                    }
                ],
            ]
        )

        result = check_cross_instance_admission(self.ctx, "issue", 77, "current-user", self.now, runner=runner)

        self.assertEqual("stand_down", result.status)
        self.assertEqual("other-user", result.other_login)
        self.assertEqual("label", result.source)
        self.assertTrue(runner.seen_timeline_paginate)

    def test_fresh_other_unknown_crnd_label_stands_down_without_catalog_match(self) -> None:
        unknown = "crnd:phase:future-not-in-local-catalog"
        self.assertNotIn(unknown, labels.canonical_labels())

        result = check_cross_instance_admission(
            self.ctx,
            "issue",
            77,
            "current-user",
            self.now,
            runner=self.runner(
                timeline=[
                    {
                        "event": "labeled",
                        "created_at": self.iso(minutes=-3),
                        "actor": {"login": "other-user"},
                        "label": {"name": unknown},
                    }
                ]
            ),
        )

        self.assertEqual("stand_down", result.status)
        self.assertEqual("label", result.source)

    def test_pr_linked_managed_issue_read_failure_is_unavailable(self) -> None:
        cases = {
            "pr-body-unavailable": self.runner(pr_body_returncode=1),
            "pr-body-invalid-json": self.runner(pr_body_json="not-json"),
            "issue-labels-unavailable": self.runner(pr_body_json=json.dumps({"body": "Closes #99"}), issue_labels_returncode=1),
            "issue-labels-invalid-json": self.runner(pr_body_json=json.dumps({"body": "Closes #99"}), issue_labels_json="not-json"),
            "issue-labels-not-list": self.runner(pr_body_json=json.dumps({"body": "Closes #99"}), issue_labels_json=json.dumps({"labels": {"name": labels.MANAGED}})),
        }
        for name, runner in cases.items():
            with self.subTest(name=name):
                result = check_cross_instance_admission(self.ctx, "pr", 77, "current-user", self.now, runner=runner)
                self.assertEqual("unavailable", result.status)
                self.assertIn("linked_issue_", result.reason)

    def test_pr_linked_managed_issue_ambiguous_closing_refs_are_unavailable(self) -> None:
        cases = {
            "zero-valid": "Closes #0",
            "malformed": "Fixes:#99",
            "multiple": "Closes #99\n\nResolves #100",
        }
        for name, body in cases.items():
            with self.subTest(name=name):
                result = check_cross_instance_admission(
                    self.ctx,
                    "pr",
                    77,
                    "current-user",
                    self.now,
                    runner=self.runner(pr_body_json=json.dumps({"body": body})),
                )

                self.assertEqual("unavailable", result.status)
                self.assertIn("linked_issue_closing_ref_ambiguous", result.reason)

    def test_pr_without_closing_marker_allows_when_no_other_signal(self) -> None:
        result = check_cross_instance_admission(
            self.ctx,
            "pr",
            77,
            "current-user",
            self.now,
            runner=self.runner(pr_body_json=json.dumps({"body": "No linked managed issue here."})),
        )

        self.assertEqual("allowed", result.status)

    def test_stand_down_window_env_override_and_invalid_falls_back_to_default(self) -> None:
        short_window_ctx = self.ctx_with_host_env('export CROSS_INSTANCE_STAND_DOWN_WINDOW_SECONDS="60"\n')
        invalid_window_ctx = self.ctx_with_host_env('export CROSS_INSTANCE_STAND_DOWN_WINDOW_SECONDS="not-an-int"\n')
        timeline = [
            {
                "event": "labeled",
                "created_at": self.iso(minutes=-90),
                "actor": {"login": "other-user"},
                "label": {"name": labels.PHASE_REVIEWING},
            }
        ]

        short_window = check_cross_instance_admission(
            short_window_ctx,
            "issue",
            77,
            "current-user",
            self.now,
            runner=self.runner(timeline=timeline),
        )
        invalid_window = check_cross_instance_admission(
            invalid_window_ctx,
            "issue",
            77,
            "current-user",
            self.now,
            runner=self.runner(timeline=timeline),
        )

        self.assertEqual("allowed", short_window.status)
        self.assertEqual("stand_down", invalid_window.status)

    def test_missing_current_login_or_bad_json_is_unavailable(self) -> None:
        missing = check_cross_instance_admission(self.ctx, "issue", 77, "", self.now, runner=self.runner())
        self.assertEqual("unavailable", missing.status)

        bad = check_cross_instance_admission(self.ctx, "issue", 77, "current-user", self.now, runner=self.runner(comments_json="not-json"))
        self.assertEqual("unavailable", bad.status)

    def runner(
        self,
        *,
        comments: list[dict] | None = None,
        comments_pages: list[list[dict]] | None = None,
        timeline: list[dict] | None = None,
        timeline_pages: list[list[dict]] | None = None,
        comments_json: str | None = None,
        pr_body_json: str | None = None,
        pr_body_returncode: int = 0,
        issue_labels_json: str | None = None,
        issue_labels_returncode: int = 0,
    ):
        class FakeRunner:
            seen_timeline_paginate = False
            seen_comments_paginate = False

            def __call__(self, command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
                if command[:2] == ["gh", "api"] and "/comments" in command[2]:
                    self.seen_comments_paginate = "--paginate" in command and "--slurp" in command and "per_page=100" in command[2]
                    stdout = comments_json if comments_json is not None else json.dumps(comments_pages if comments_pages is not None else [comments or []])
                    return subprocess.CompletedProcess(command, 0, stdout, "")
                if command[:3] == ["gh", "pr", "view"] and "body" in command:
                    stdout = pr_body_json if pr_body_json is not None else json.dumps({"body": ""})
                    return subprocess.CompletedProcess(command, pr_body_returncode, stdout, "pr body failed" if pr_body_returncode else "")
                if command[:3] == ["gh", "issue", "view"] and "labels" in command:
                    stdout = issue_labels_json if issue_labels_json is not None else json.dumps({"labels": [{"name": labels.MANAGED}]})
                    return subprocess.CompletedProcess(command, issue_labels_returncode, stdout, "issue labels failed" if issue_labels_returncode else "")
                if command[:2] == ["gh", "api"]:
                    self.seen_timeline_paginate = "--paginate" in command and "--slurp" in command and "per_page=100" in command[2]
                    stdout = json.dumps(timeline_pages if timeline_pages is not None else [timeline or []])
                    return subprocess.CompletedProcess(command, 0, stdout, "")
                return subprocess.CompletedProcess(command, 0, json.dumps({"labels": [{"name": labels.MANAGED}], "body": ""}), "")

        return FakeRunner()

    def ctx_with_host_env(self, extra: str) -> LoopContext:
        (self.tmp / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\n{extra}',
            encoding="utf-8",
        )
        return LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})

    def iso(self, *, minutes: int = 0, hours: int = 0) -> str:
        return (self.now + timedelta(minutes=minutes, hours=hours)).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    unittest.main()
