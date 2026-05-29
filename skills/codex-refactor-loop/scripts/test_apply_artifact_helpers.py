#!/usr/bin/env python3
"""Behavior tests for remaining controller-owned artifact apply helpers."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import triage as apply_triage_decision
from codex_refactor_loop.github_body import render_github_body
from codex_refactor_loop.triage import ACCEPT_LABELS


class TriageDecisionApplyHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        runs = self.repo / ".refactor-loop" / "runs"
        runs.mkdir(parents=True)
        (runs / "comment.md").write_text("comment\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        (runs / "body.md").write_text("body\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        (runs / "authority.md").write_text("完整 triage 授权内容\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        self.self_contained_triage_body = render_github_body(
            kind="triage",
            title="triage accepted",
            artifact_paths=[runs / "authority.md"],
            debug_paths=[".refactor-loop/runs/authority.md"],
        )
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
        (self.repo / ".refactor-loop" / "runs" / "comment.md").write_text(self.self_contained_triage_body, encoding="utf-8")
        (self.repo / ".refactor-loop" / "runs" / "body.md").write_text(self.self_contained_triage_body, encoding="utf-8")
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
