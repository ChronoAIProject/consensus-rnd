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
from unittest.mock import Mock, patch

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.sync import apply as apply_integration_sync_request
from codex_refactor_loop import triage as apply_triage_decision
from codex_refactor_loop.triage import ACCEPT_LABELS


class IntegrationSyncApplyHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.worktree = self.repo / "wt"
        self.worktree.mkdir()
        self.request_path = self.repo / ".refactor-loop" / "runs" / "integration-sync-request-test.json"
        self.request_path.parent.mkdir(parents=True)
        self.request_path.write_text(json.dumps(self.valid_request()) + "\n", encoding="utf-8")
        self.commands: list[list[str]] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def valid_request(self) -> dict:
        return {
            "schema": "IntegrationSyncRequest",
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
        self.commands.append(cmd)
        if cmd[:2] == ["git", "fetch"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:2] == ["git", "rev-parse"] and cmd[-1] == "origin/auto-refact-dev":
            return subprocess.CompletedProcess(cmd, 0, "remote-sha\n", "")
        if cmd[:2] == ["git", "rev-parse"] and cmd[-1] == "HEAD":
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

    def applied_record(self) -> Path:
        return self.repo / ".refactor-loop" / "runs" / "integration-sync-applied" / f"{self.request_path.stem}.applied.json"

    def test_applies_valid_requests_with_bounded_git_side_effects_and_no_pending_event(self) -> None:
        cases = [
            (
                "push-local-ahead",
                {},
                [
                    ["git", "rev-list", "--count", "origin/auto-refact-dev..HEAD"],
                    ["git", "push", "origin", "HEAD:auto-refact-dev"],
                ],
            ),
            (
                "continue-resolved-merge",
                {},
                [
                    ["git", "merge", "--continue"],
                    ["git", "push", "origin", "HEAD:auto-refact-dev"],
                ],
            ),
            (
                "forward-sync-review-base",
                {},
                [
                    ["git", "merge", "--ff-only", "origin/dev"],
                    ["git", "push", "origin", "HEAD:auto-refact-dev"],
                ],
            ),
            (
                "adopt-merged-rollup",
                {"old_rollup_head": "old-head", "old_rollup_ahead_count": 1},
                [
                    ["git", "merge-base", "--is-ancestor", "old-head", "origin/auto-refact-dev"],
                    ["git", "rev-list", "--count", "old-head..origin/auto-refact-dev"],
                    ["git", "reset", "--hard", "origin/auto-refact-dev"],
                    ["git", "rebase", "--rebase-merges", "--onto", "origin/dev", "old-head"],
                    ["git", "push", "--force-with-lease=refs/heads/auto-refact-dev:remote-sha", "origin", "HEAD:auto-refact-dev"],
                ],
            ),
        ]

        for kind, updates, expected in cases:
            with self.subTest(kind=kind):
                self.commands = []
                data = {"kind": kind, **updates}
                self.write_request(data)
                marker = self.applied_record()
                if marker.exists():
                    marker.unlink()
                merge_head = self.worktree / ".git" / "MERGE_HEAD"
                merge_head.parent.mkdir(parents=True, exist_ok=True)
                if kind == "continue-resolved-merge":
                    merge_head.write_text("merge\n", encoding="utf-8")
                elif merge_head.exists():
                    merge_head.unlink()

                self.assertEqual(self.apply(), 0)
                applied = json.loads(marker.read_text(encoding="utf-8"))
                self.assertEqual(applied["status"], "applied")
                self.assertEqual(applied["reason"], kind)
                for command in expected:
                    self.assertIn(command, self.commands)
                self.assertFalse((self.repo / ".refactor-loop" / ".controller-pending-events.log").exists())

    def self_assert_rejected(self, reason: str) -> None:
        self.assertEqual(self.apply(), 2)
        rejected = self.repo / ".refactor-loop" / "runs" / "integration-sync-applied" / f"{self.request_path.stem}.rejected.json"
        self.assertIn(reason, rejected.read_text(encoding="utf-8"))

    def test_rejects_stale_expected_remote_sha(self) -> None:
        self.write_request({"expected_remote_sha": "old-sha"})
        self.self_assert_rejected("stale expected_remote_sha")

    def test_apply_integration_sync_request_falls_back_to_no_ff_when_ff_only_fails(self) -> None:
        self.write_request({"kind": "forward-sync-review-base"})

        def ff_fails_no_ff_succeeds(cmd: list[str], cwd: Path, check: bool = False):
            if cmd[:3] == ["git", "merge", "--ff-only"]:
                self.commands.append(cmd)
                return subprocess.CompletedProcess(cmd, 1, "", "not possible to fast-forward\n")
            return self.fake_run(cmd, cwd, check)

        with patch.dict(os.environ, {"INTEGRATION_BRANCH": "auto-refact-dev", "REVIEW_BASE_BRANCH": "dev"}, clear=False):
            with patch.object(apply_integration_sync_request, "run", ff_fails_no_ff_succeeds):
                self.assertEqual(apply_integration_sync_request.apply_request(self.request_path, repo=self.repo, worktree=self.worktree), 0)

        self.assertIn(["git", "merge", "--ff-only", "origin/dev"], self.commands)
        self.assertIn(
            [
                "git",
                "merge",
                "--no-ff",
                "-m",
                "Sync auto-refact-dev with dev (controller apply)",
                "origin/dev",
            ],
            self.commands,
        )
        self.assertIn(["git", "push", "origin", "HEAD:auto-refact-dev"], self.commands)

    def test_apply_integration_sync_request_skips_when_replay_n_is_zero(self) -> None:
        self.write_request({"kind": "adopt-merged-rollup", "old_rollup_head": "old-head", "old_rollup_ahead_count": 0})

        def zero_replay(cmd: list[str], cwd: Path, check: bool = False):
            self.commands.append(cmd)
            if cmd[:3] == ["git", "rev-list", "--count"]:
                return subprocess.CompletedProcess(cmd, 0, "0\n", "")
            return self.fake_run(cmd, cwd, check)

        with patch.dict(os.environ, {"INTEGRATION_BRANCH": "auto-refact-dev", "REVIEW_BASE_BRANCH": "dev"}, clear=False):
            with patch.object(apply_integration_sync_request, "run", zero_replay):
                self.assertEqual(apply_integration_sync_request.apply_request(self.request_path, repo=self.repo, worktree=self.worktree), 0)

        self.assertIn(["git", "rev-list", "--count", "old-head..origin/auto-refact-dev"], self.commands)
        self.assertIn(["git", "reset", "--hard", "origin/dev"], self.commands)
        self.assertNotIn(["git", "reset", "--hard", "origin/auto-refact-dev"], self.commands)
        self.assertNotIn(["git", "rebase", "--rebase-merges", "--onto", "origin/dev", "old-head"], self.commands)
        self.assertIn(
            ["git", "push", "--force-with-lease=refs/heads/auto-refact-dev:remote-sha", "origin", "HEAD:auto-refact-dev"],
            self.commands,
        )

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
            "schema": "ManualIssueTriageDecision",
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

    def applied_record(self) -> Path:
        return self.repo / ".refactor-loop" / "runs" / "triage-decisions-applied" / f"{self.decision_path.stem}.applied.json"

    def test_reject_happy_path_comments_removes_triage_label_and_records_applied(self) -> None:
        calls: list[list[str]] = []

        def fake_gh(args: list[str], *, repo: Path, repo_slug=None) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            return subprocess.CompletedProcess(["gh", *args], 0, "", "")

        with patch.object(apply_triage_decision, "current_labels", lambda _config, _issue: ["auto-loop-triage"]):
            with patch.object(apply_triage_decision, "run_gh", fake_gh):
                self.assertEqual(apply_triage_decision.apply_decision(apply_triage_decision.load_triage_apply_config(repo_root=self.repo), self.decision_path, issue_number=53, verdict="reject"), 0)

        self.assertEqual(
            calls,
            [
                ["issue", "comment", "53", "--body-file", str((self.repo / ".refactor-loop" / "runs" / "comment.md").resolve())],
                ["issue", "edit", "53", "--remove-label", "auto-loop-triage"],
            ],
        )
        applied = json.loads(self.applied_record().read_text(encoding="utf-8"))
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(applied["reason"], "reject")
        self.assertFalse((self.repo / ".refactor-loop" / ".controller-pending-events.log").exists())

    def test_accept_happy_path_comments_edits_body_adds_fixed_labels_and_records_applied(self) -> None:
        self.write_decision(verdict="accept", body_artifact_path=".refactor-loop/runs/body.md", add_labels=ACCEPT_LABELS)
        calls: list[list[str]] = []

        def fake_gh(args: list[str], *, repo: Path, repo_slug=None) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            return subprocess.CompletedProcess(["gh", *args], 0, "", "")

        with patch.object(apply_triage_decision, "current_labels", lambda _config, _issue: ["auto-loop-triage"]):
            with patch.object(apply_triage_decision, "run_gh", fake_gh):
                self.assertEqual(apply_triage_decision.apply_decision(apply_triage_decision.load_triage_apply_config(repo_root=self.repo), self.decision_path, issue_number=53, verdict="accept"), 0)

        expected_edit = [
            "issue",
            "edit",
            "53",
            "--body-file",
            str((self.repo / ".refactor-loop" / "runs" / "body.md").resolve()),
            "--remove-label",
            "auto-loop-triage",
        ]
        for label in ACCEPT_LABELS:
            expected_edit += ["--add-label", label]
        self.assertEqual(
            calls,
            [
                ["issue", "comment", "53", "--body-file", str((self.repo / ".refactor-loop" / "runs" / "comment.md").resolve())],
                expected_edit,
            ],
        )
        applied = json.loads(self.applied_record().read_text(encoding="utf-8"))
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(applied["reason"], "accept")
        self.assertFalse((self.repo / ".refactor-loop" / ".controller-pending-events.log").exists())

    def test_reject_apply_requires_current_triage_label_and_same_issue(self) -> None:
        with patch.object(apply_triage_decision, "current_labels", lambda _config, _issue: []):
            self.assertEqual(apply_triage_decision.apply_decision(apply_triage_decision.load_triage_apply_config(repo_root=self.repo), self.decision_path, issue_number=53, verdict="reject"), 2)
        self.write_decision(issue_number=54)
        with patch.object(apply_triage_decision, "current_labels", lambda _config, _issue: ["auto-loop-triage"]):
            self.assertEqual(apply_triage_decision.apply_decision(apply_triage_decision.load_triage_apply_config(repo_root=self.repo), self.decision_path, issue_number=53, verdict="reject"), 2)

    def test_accept_requires_fixed_labels_and_reject_only_removes_triage_label(self) -> None:
        self.write_decision(verdict="accept", body_artifact_path=".refactor-loop/runs/body.md", add_labels=["auto-loop"])
        with patch.object(apply_triage_decision, "current_labels", lambda _config, _issue: ["auto-loop-triage"]):
            self.assertEqual(apply_triage_decision.apply_decision(apply_triage_decision.load_triage_apply_config(repo_root=self.repo), self.decision_path, issue_number=53, verdict="accept"), 2)

    def test_apply_triage_decision_rejects_path_traversal_and_does_not_mutate_github(self) -> None:
        self.write_decision(comment_artifact_path="../../../etc/passwd")
        mock_gh = Mock(return_value=subprocess.CompletedProcess(["gh"], 0, "", ""))

        with patch.object(apply_triage_decision, "run_gh", mock_gh):
            self.assertEqual(apply_triage_decision.apply_decision(apply_triage_decision.load_triage_apply_config(repo_root=self.repo), self.decision_path, issue_number=53, verdict="reject"), 2)

        rejected = self.repo / ".refactor-loop" / "runs" / "triage-decisions-applied" / f"{self.decision_path.stem}.rejected.json"
        self.assertIn("artifact path outside repo", rejected.read_text(encoding="utf-8"))
        self.assertEqual(mock_gh.call_count, 0)

    def test_apply_triage_decision_rejects_missing_final_sentinel_and_does_not_mutate_github(self) -> None:
        (self.repo / ".refactor-loop" / "runs" / "comment.md").write_text("comment without sentinel\n", encoding="utf-8")
        mock_gh = Mock(return_value=subprocess.CompletedProcess(["gh"], 0, "", ""))

        with patch.object(apply_triage_decision, "current_labels", lambda _config, _issue: ["auto-loop-triage"]):
            with patch.object(apply_triage_decision, "run_gh", mock_gh):
                self.assertEqual(apply_triage_decision.apply_decision(apply_triage_decision.load_triage_apply_config(repo_root=self.repo), self.decision_path, issue_number=53, verdict="reject"), 2)

        rejected = self.repo / ".refactor-loop" / "runs" / "triage-decisions-applied" / f"{self.decision_path.stem}.rejected.json"
        self.assertIn("comment artifact missing final sentinel", rejected.read_text(encoding="utf-8"))
        self.assertEqual(mock_gh.call_count, 0)

    def test_schema_rejects_close_and_command_like_fields(self) -> None:
        self.write_decision(close=True)
        with patch.object(apply_triage_decision, "current_labels", lambda _config, _issue: ["auto-loop-triage"]):
            self.assertEqual(apply_triage_decision.apply_decision(apply_triage_decision.load_triage_apply_config(repo_root=self.repo), self.decision_path, issue_number=53, verdict="reject"), 2)


if __name__ == "__main__":
    unittest.main()
