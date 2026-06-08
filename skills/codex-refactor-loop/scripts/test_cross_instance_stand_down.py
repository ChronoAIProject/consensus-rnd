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

    def test_missing_current_login_or_bad_json_is_unavailable(self) -> None:
        missing = check_cross_instance_admission(self.ctx, "issue", 77, "", self.now, runner=self.runner())
        self.assertEqual("unavailable", missing.status)

        bad = check_cross_instance_admission(self.ctx, "issue", 77, "current-user", self.now, runner=self.runner(comments_json="not-json"))
        self.assertEqual("unavailable", bad.status)

    def runner(self, *, comments: list[dict] | None = None, timeline: list[dict] | None = None, comments_json: str | None = None):
        def _run(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            if command[:3] in (["gh", "issue", "view"], ["gh", "pr", "view"]) and "comments" in command:
                stdout = comments_json if comments_json is not None else json.dumps({"comments": comments or []})
                return subprocess.CompletedProcess(command, 0, stdout, "")
            if command[:2] == ["gh", "api"]:
                return subprocess.CompletedProcess(command, 0, json.dumps(timeline or []), "")
            return subprocess.CompletedProcess(command, 0, json.dumps({"labels": [{"name": labels.MANAGED}], "body": ""}), "")

        return _run

    def iso(self, *, minutes: int = 0, hours: int = 0) -> str:
        return (self.now + timedelta(minutes=minutes, hours=hours)).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    unittest.main()
