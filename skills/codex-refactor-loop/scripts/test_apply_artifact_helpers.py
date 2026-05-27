#!/usr/bin/env python3
"""Behavior tests for controller-owned artifact apply helpers."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import apply_integration_sync_request
import apply_triage_decision


class IntegrationSyncApplyHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.worktree = self.repo / "wt"
        self.worktree.mkdir()
        self.request_path = self.repo / ".refactor-loop" / "runs" / "integration-sync-request-test.json"
        self.request_path.parent.mkdir(parents=True)
        self.request_path.write_text(json.dumps(self.valid_request()) + "\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def valid_request(self) -> dict:
        return {
            "schema": "IntegrationSyncRequestV1",
            "kind": "push-local-ahead",
            "integration_branch": "auto-refact-dev",
            "review_base_branch": "dev",
            "worktree_head": "head-sha",
            "expected_remote_sha": "remote-sha",
            "evidence": {"reason": "test"},
            "created_at": "2026-05-27T00:00:00Z",
            "lifecycle_owner": "controller",
            "lifecycle_authority": False,
        }

    def write_request(self, updates: dict) -> None:
        data = self.valid_request()
        data.update(updates)
        self.request_path.write_text(json.dumps(data) + "\n", encoding="utf-8")

    def fake_run(self, cmd: list[str], cwd: Path, check: bool = False) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["git", "fetch"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["git", "rev-parse", "origin/auto-refact-dev"]:
            return subprocess.CompletedProcess(cmd, 0, "remote-sha\n", "")
        if cmd[:3] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(cmd, 0, "head-sha\n", "")
        if cmd[:3] == ["git", "rev-parse", "--git-path"]:
            return subprocess.CompletedProcess(cmd, 0, str(self.worktree / ".git" / cmd[3]) + "\n", "")
        if cmd[:3] in (["git", "diff", "--quiet"], ["git", "diff", "--cached"]):
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["git", "rev-list", "--count"]:
            return subprocess.CompletedProcess(cmd, 0, "1\n", "")
        if cmd[:2] == ["git", "push"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def apply(self) -> int:
        with patch.dict(os.environ, {"INTEGRATION_BRANCH": "auto-refact-dev", "REVIEW_BASE_BRANCH": "dev"}, clear=False):
            with patch.object(apply_integration_sync_request, "run", self.fake_run):
                return apply_integration_sync_request.apply_request(self.request_path, repo=self.repo, worktree=self.worktree)

    def self_assert_rejected(self, reason: str) -> None:
        self.assertEqual(self.apply(), 2)
        rejected = self.repo / ".refactor-loop" / "runs" / "integration-sync-applied" / f"{self.request_path.stem}.rejected.json"
        self.assertIn(reason, rejected.read_text(encoding="utf-8"))

    def test_rejects_stale_expected_remote_sha(self) -> None:
        self.write_request({"expected_remote_sha": "old-sha"})
        self.self_assert_rejected("stale expected_remote_sha")

    def test_rejects_branch_mismatch(self) -> None:
        self.write_request({"integration_branch": "other"})
        self.self_assert_rejected("branch mismatch")

    def test_rejects_dirty_non_merge_worktree(self) -> None:
        def dirty_run(cmd: list[str], cwd: Path, check: bool = False):
            if cmd[:3] == ["git", "diff", "--quiet"]:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return self.fake_run(cmd, cwd, check)

        with patch.dict(os.environ, {"INTEGRATION_BRANCH": "auto-refact-dev", "REVIEW_BASE_BRANCH": "dev"}, clear=False):
            with patch.object(apply_integration_sync_request, "run", dirty_run):
                self.assertEqual(apply_integration_sync_request.apply_request(self.request_path, repo=self.repo, worktree=self.worktree), 2)

    def test_rejects_invalid_rollup_ancestry(self) -> None:
        self.write_request({"kind": "adopt-merged-rollup", "old_rollup_head": "old-head", "old_rollup_ahead_count": 1})

        def bad_ancestor(cmd: list[str], cwd: Path, check: bool = False):
            if cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return self.fake_run(cmd, cwd, check)

        with patch.dict(os.environ, {"INTEGRATION_BRANCH": "auto-refact-dev", "REVIEW_BASE_BRANCH": "dev"}, clear=False):
            with patch.object(apply_integration_sync_request, "run", bad_ancestor):
                self.assertEqual(apply_integration_sync_request.apply_request(self.request_path, repo=self.repo, worktree=self.worktree), 2)

    def test_rejects_malformed_and_already_applied(self) -> None:
        self.request_path.write_text("{not json", encoding="utf-8")
        self.self_assert_rejected("malformed")
        self.request_path.write_text(json.dumps(self.valid_request()) + "\n", encoding="utf-8")
        marker = self.repo / ".refactor-loop" / "runs" / "integration-sync-applied" / f"{self.request_path.stem}.applied.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{}\n", encoding="utf-8")
        self.self_assert_rejected("already-applied")


class TriageDecisionApplyHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        runs = self.repo / ".refactor-loop" / "runs"
        runs.mkdir(parents=True)
        (runs / "comment.md").write_text("comment\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        (runs / "body.md").write_text("body\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        self.decision_path = runs / "triage-issue-53.json"
        self.write_decision()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_decision(self, **updates) -> None:
        data = {
            "schema": "ManualIssueTriageDecisionV1",
            "issue_number": 53,
            "verdict": "reject",
            "body_artifact_path": "",
            "comment_artifact_path": ".refactor-loop/runs/comment.md",
            "add_labels": [],
            "remove_labels": ["auto-loop-triage"],
            "sentinel_present": True,
            "lifecycle_owner": "controller",
            "lifecycle_authority": False,
        }
        data.update(updates)
        self.decision_path.write_text(json.dumps(data) + "\n", encoding="utf-8")

    def test_reject_apply_requires_current_triage_label_and_same_issue(self) -> None:
        with patch.object(apply_triage_decision, "current_labels", lambda _repo, _issue: []):
            self.assertEqual(apply_triage_decision.apply_decision(self.decision_path, repo=self.repo, issue_number=53, verdict="reject"), 2)
        self.write_decision(issue_number=54)
        with patch.object(apply_triage_decision, "current_labels", lambda _repo, _issue: ["auto-loop-triage"]):
            self.assertEqual(apply_triage_decision.apply_decision(self.decision_path, repo=self.repo, issue_number=53, verdict="reject"), 2)

    def test_accept_requires_fixed_labels_and_reject_only_removes_triage_label(self) -> None:
        self.write_decision(verdict="accept", body_artifact_path=".refactor-loop/runs/body.md", add_labels=["auto-loop"])
        with patch.object(apply_triage_decision, "current_labels", lambda _repo, _issue: ["auto-loop-triage"]):
            self.assertEqual(apply_triage_decision.apply_decision(self.decision_path, repo=self.repo, issue_number=53, verdict="accept"), 2)

    def test_schema_rejects_close_and_command_like_fields(self) -> None:
        self.write_decision(close=True)
        with patch.object(apply_triage_decision, "current_labels", lambda _repo, _issue: ["auto-loop-triage"]):
            self.assertEqual(apply_triage_decision.apply_decision(self.decision_path, repo=self.repo, issue_number=53, verdict="reject"), 2)


if __name__ == "__main__":
    unittest.main()
