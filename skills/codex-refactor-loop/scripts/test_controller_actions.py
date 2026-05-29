#!/usr/bin/env python3
"""Behavior tests for Python controller actions."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.controller_actions import ControllerActions
from codex_refactor_loop.coordination.leases import LeaseDecision


class ControllerActionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="controller-actions-test-"))
        (self.tmp / ".refactor-loop" / "state").mkdir(parents=True)
        (self.tmp / ".refactor-loop" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\n',
            encoding="utf-8",
        )
        self.actions = ControllerActions(LoopContext.load(repo_root=self.tmp))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_recent_pr_merge_writes_rolling_artifact(self) -> None:
        facts = {
            "number": 7,
            "mergedAt": "2026-05-29T00:00:00Z",
            "mergeCommit": {"oid": "abc123"},
            "baseRefName": "dev",
            "headRefName": "feature",
        }
        with mock.patch.object(self.actions, "gh", return_value=mock.Mock(returncode=0, stdout=json.dumps(facts), stderr="")):
            self.actions.record_recent_pr_merge("7")
        data = json.loads((self.tmp / ".refactor-loop" / "state" / "recent-pr-merges.json").read_text(encoding="utf-8"))
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["merges"][0]["sha"], "abc123")

    def test_apply_marker_rejects_unbounded_paths(self) -> None:
        self.assertEqual(2, self.actions.apply_dev_sync_request_marker("DEV_SYNC_REQUEST:/tmp/out.json"))
        self.assertEqual(2, self.actions.apply_triage_decision_marker("TRIAGE_DECISION_DONE:x:accept:/tmp/out.json"))

    def test_merge_pr_lease_miss_skips_gh_merge_and_label_side_effects(self) -> None:
        self.actions.lease_gate = mock.Mock()
        self.actions.lease_gate.singleton.return_value = LeaseDecision(False, "leased-by:other")
        with mock.patch.object(self.actions, "gh") as gh:
            self.assertEqual(3, self.actions.merge_pr("12"))
        gh.assert_not_called()


class ControllerActionsSourceRegressionTests(unittest.TestCase):
    def test_required_lifecycle_helpers_exist(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        for needle in ("merge_pr", "open_pr_with_label", "safe_worktree", "record_recent_pr_merge", "apply_dev_sync_request_marker", "apply_triage_decision_marker", "render_template"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
