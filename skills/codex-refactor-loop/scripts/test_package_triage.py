#!/usr/bin/env python3
"""Behavior tests for the packaged manual issue triage module."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from codex_refactor_loop import labels
from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.github_body import render_github_body
from codex_refactor_loop.triage import ACCEPT_LABELS, TriageApplyConfig, apply_decision, host_issue_intake_projection


SCRIPT_DIR = Path(__file__).resolve().parent
PACKAGE_TRIAGE = SCRIPT_DIR / "codex_refactor_loop" / "triage.py"


class PackageTriageDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name).resolve()
        runs = self.repo / ".refactor-loop" / "runs"
        runs.mkdir(parents=True)
        (self.repo / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.repo / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.repo}"\nexport GH_REPO_SLUG="owner/repo"\n',
            encoding="utf-8",
        )
        (runs / "comment.md").write_text("comment\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        (runs / "body.md").write_text("body\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        (runs / "authority.md").write_text("完整授权内容\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        self.self_contained_triage_body = render_github_body(
            kind="triage",
            title="triage accepted",
            artifact_paths=[runs / "authority.md"],
            debug_paths=[".refactor-loop/runs/authority.md"],
        )
        self.decision_path = runs / "triage-issue-53.json"
        self.config = TriageApplyConfig(LoopContext.load(repo_root=self.repo, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}))
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
            "remove_labels": [labels.TRIAGE_PENDING],
            "sentinel_present": True,
            "lifecycle_owner": "controller",
            "lifecycle_authority": False,
        }
        data.update(updates)
        self.decision_path.write_text(json.dumps(data) + "\n", encoding="utf-8")

    def applied_record(self) -> Path:
        return self.repo / ".refactor-loop" / "runs" / "triage-decisions-applied" / f"{self.decision_path.stem}.applied.json"

    def rejected_record(self) -> Path:
        return self.repo / ".refactor-loop" / "runs" / "triage-decisions-applied" / f"{self.decision_path.stem}.rejected.json"

    def test_reject_happy_path_comments_removes_triage_label_and_records_applied(self) -> None:
        calls: list[list[str]] = []

        def fake_gh(args: list[str], *, repo: Path, repo_slug: str | None = None) -> subprocess.CompletedProcess[str]:
            self.assertEqual(repo, self.repo)
            self.assertEqual(repo_slug, "owner/repo")
            calls.append(args)
            return subprocess.CompletedProcess(["gh", *args], 0, "", "")

        with patch("codex_refactor_loop.triage.current_labels", lambda _config, _issue: [labels.TRIAGE_PENDING]):
            with patch("codex_refactor_loop.triage.run_gh", fake_gh):
                self.assertEqual(apply_decision(self.config, self.decision_path, issue_number=53, verdict="reject"), 0)

        self.assertEqual(
            calls,
            [
                ["issue", "comment", "53", "--body-file", str((self.repo / ".refactor-loop" / "runs" / "comment.md").resolve())],
                ["issue", "edit", "53", "--remove-label", labels.TRIAGE_PENDING],
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

        def fake_gh(args: list[str], *, repo: Path, repo_slug: str | None = None) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            return subprocess.CompletedProcess(["gh", *args], 0, "", "")

        with patch("codex_refactor_loop.triage.current_labels", lambda _config, _issue: [labels.TRIAGE_PENDING]):
            with patch("codex_refactor_loop.triage.run_gh", fake_gh):
                self.assertEqual(apply_decision(self.config, self.decision_path, issue_number=53, verdict="accept"), 0)

        expected_edit = [
            "issue",
            "edit",
            "53",
            "--body-file",
            str((self.repo / ".refactor-loop" / "runs" / "body.md").resolve()),
            "--remove-label",
            labels.TRIAGE_PENDING,
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

    def test_accept_dual_reads_and_removes_legacy_triage_label(self) -> None:
        self.write_decision(verdict="accept", body_artifact_path=".refactor-loop/runs/body.md", add_labels=ACCEPT_LABELS)
        (self.repo / ".refactor-loop" / "runs" / "comment.md").write_text(self.self_contained_triage_body, encoding="utf-8")
        (self.repo / ".refactor-loop" / "runs" / "body.md").write_text(self.self_contained_triage_body, encoding="utf-8")
        calls: list[list[str]] = []

        def fake_gh(args: list[str], *, repo: Path, repo_slug: str | None = None) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            return subprocess.CompletedProcess(["gh", *args], 0, "", "")

        with patch("codex_refactor_loop.triage.current_labels", lambda _config, _issue: ["auto-loop-triage"]):
            with patch("codex_refactor_loop.triage.run_gh", fake_gh):
                self.assertEqual(apply_decision(self.config, self.decision_path, issue_number=53, verdict="accept"), 0)

        edit = calls[1]
        self.assertIn("--remove-label", edit)
        self.assertIn("auto-loop-triage", edit)
        self.assertNotIn(labels.TRIAGE_PENDING, edit[: edit.index("--add-label")])

    def test_rejects_missing_current_label_issue_mismatch_and_fixed_label_drift(self) -> None:
        with patch("codex_refactor_loop.triage.current_labels", lambda _config, _issue: []):
            self.assertEqual(apply_decision(self.config, self.decision_path, issue_number=53, verdict="reject"), 2)
        self.assertIn("crnd:triage:pending label missing", self.rejected_record().read_text(encoding="utf-8"))

        self.rejected_record().unlink()
        self.write_decision(issue_number=54)
        with patch("codex_refactor_loop.triage.current_labels", lambda _config, _issue: [labels.TRIAGE_PENDING]):
            self.assertEqual(apply_decision(self.config, self.decision_path, issue_number=53, verdict="reject"), 2)
        self.assertIn("issue mismatch", self.rejected_record().read_text(encoding="utf-8"))

        self.rejected_record().unlink()
        self.write_decision(verdict="accept", body_artifact_path=".refactor-loop/runs/body.md", add_labels=[labels.MANAGED])
        with patch("codex_refactor_loop.triage.current_labels", lambda _config, _issue: [labels.TRIAGE_PENDING]):
            self.assertEqual(apply_decision(self.config, self.decision_path, issue_number=53, verdict="accept"), 2)
        self.assertIn("accept add_labels must be fixed design-consensus labels", self.rejected_record().read_text(encoding="utf-8"))

    def test_rejects_path_traversal_and_missing_final_sentinel_before_github_mutation(self) -> None:
        self.write_decision(comment_artifact_path="../../../etc/passwd")
        mock_gh = Mock(return_value=subprocess.CompletedProcess(["gh"], 0, "", ""))

        with patch("codex_refactor_loop.triage.run_gh", mock_gh):
            self.assertEqual(apply_decision(self.config, self.decision_path, issue_number=53, verdict="reject"), 2)

        self.assertIn("artifact path outside repo", self.rejected_record().read_text(encoding="utf-8"))
        self.assertEqual(mock_gh.call_count, 0)

        self.rejected_record().unlink()
        self.write_decision()
        (self.repo / ".refactor-loop" / "runs" / "comment.md").write_text("comment without sentinel\n", encoding="utf-8")
        mock_gh = Mock(return_value=subprocess.CompletedProcess(["gh"], 0, "", ""))

        with patch("codex_refactor_loop.triage.current_labels", lambda _config, _issue: [labels.TRIAGE_PENDING]):
            with patch("codex_refactor_loop.triage.run_gh", mock_gh):
                self.assertEqual(apply_decision(self.config, self.decision_path, issue_number=53, verdict="reject"), 2)

        self.assertIn("comment artifact missing final sentinel", self.rejected_record().read_text(encoding="utf-8"))
        self.assertEqual(mock_gh.call_count, 0)

    def test_accept_rejects_path_only_authority_before_github_mutation(self) -> None:
        self.write_decision(verdict="accept", body_artifact_path=".refactor-loop/runs/body.md", add_labels=ACCEPT_LABELS)
        path_only = "## 🤖 triage\n\n授权:.refactor-loop/runs/phase9-issue192-r1-judge.md\n\n⟦AI:AUTO-LOOP⟧\n"
        (self.repo / ".refactor-loop" / "runs" / "comment.md").write_text(path_only, encoding="utf-8")
        (self.repo / ".refactor-loop" / "runs" / "body.md").write_text(path_only, encoding="utf-8")
        mock_gh = Mock(return_value=subprocess.CompletedProcess(["gh"], 0, "", ""))

        with patch("codex_refactor_loop.triage.current_labels", lambda _config, _issue: ["auto-loop-triage"]):
            with patch("codex_refactor_loop.triage.run_gh", mock_gh):
                self.assertEqual(apply_decision(self.config, self.decision_path, issue_number=53, verdict="accept"), 2)

        self.assertIn("local .refactor-loop artifact path", self.rejected_record().read_text(encoding="utf-8"))
        self.assertEqual(mock_gh.call_count, 0)

    def test_schema_rejects_command_like_lifecycle_fields(self) -> None:
        self.write_decision(close=True)
        with patch("codex_refactor_loop.triage.current_labels", lambda _config, _issue: [labels.TRIAGE_PENDING]):
            self.assertEqual(apply_decision(self.config, self.decision_path, issue_number=53, verdict="reject"), 2)
        self.assertIn("command-like fields forbidden: close", self.rejected_record().read_text(encoding="utf-8"))

    def test_builtin_manual_issue_decision_contract_unchanged(self) -> None:
        self.write_decision(verdict="accept", body_artifact_path=".refactor-loop/runs/body.md", add_labels=ACCEPT_LABELS)
        data = json.loads(self.decision_path.read_text(encoding="utf-8"))

        self.assertEqual(data["schema"], "ManualIssueTriageDecision")
        self.assertEqual(data["lifecycle_owner"], "controller")
        self.assertFalse(data["lifecycle_authority"])
        self.assertEqual(data["remove_labels"], [labels.TRIAGE_PENDING])
        self.assertEqual(data["add_labels"], ACCEPT_LABELS)

    def test_host_issue_intake_requires_host_namespace_and_no_lifecycle_authority(self) -> None:
        (self.repo / "prompts").mkdir(exist_ok=True)
        (self.repo / "prompts" / "host.md").write_text("host prompt\n", encoding="utf-8")
        spec = {
            "stages": [{"slug": "host:intake", "title": "Intake", "contract": "projection", "detail_anchor": "host-intake"}],
            "work_unit_kinds": [{"name": "host:kind"}],
            "prompt_bindings": {"host:prompt": "prompts/host.md"},
            "issue_intake_mappings": [
                {
                    "name": "host:template",
                    "work_unit_kind": "host:kind",
                    "producer": "host:github-template",
                    "stage": "host:intake",
                    "prompt_binding": "host:prompt",
                }
            ],
        }
        (self.repo / "workflow.json").write_text(json.dumps(spec), encoding="utf-8")
        ctx = LoopContext.load(repo_root=self.repo, env={"REPO_ROOT": str(self.repo), "HOST_WORKFLOW_SPEC": "workflow.json"})

        projection = host_issue_intake_projection(ctx, "host:template")

        self.assertEqual(projection["producer"], "host:github-template")
        self.assertEqual(projection["lifecycle_authority"], "false")
        for forbidden in ("labels", "assignees", "milestones", "spawn", "merge"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, projection)

        spec["issue_intake_mappings"][0]["producer"] = "manual-issue"
        (self.repo / "workflow.json").write_text(json.dumps(spec), encoding="utf-8")
        ctx = LoopContext.load(repo_root=self.repo, env={"REPO_ROOT": str(self.repo), "HOST_WORKFLOW_SPEC": "workflow.json"})
        with self.assertRaisesRegex(Exception, "overwrite built-in|host: namespace"):
            host_issue_intake_projection(ctx, "host:template")


class PackageTriageSourceRegressionTests(unittest.TestCase):
    def test_source_preserves_contract_literals_and_narrow_allowlist(self) -> None:
        src = PACKAGE_TRIAGE.read_text(encoding="utf-8")
        for required in (
            "ManualIssueTriageDecision",
            "TRIAGE_DECISION_DONE",
            "TRIAGE_DECISION_APPLIED",
            "TRIAGE_DECISION_REJECTED",
            ".refactor-loop/runs/",
            "triage-decisions-applied",
            "crnd:triage:pending",
            "label_catalog.design_issue_label_bundle()",
            "lifecycle_owner",
            "lifecycle_authority",
            "controller",
            "host.env",
            "⟦AI:AUTO-LOOP⟧",
            "validate_self_contained_github_body",
            "--body-file",
            "--remove-label",
            "--add-label",
        ):
            with self.subTest(required=required):
                self.assertIn(required, src)

        for forbidden in (
            "gh issue close",
            "gh issue reopen",
            "gh pr create",
            "gh pr merge",
            "git commit",
            "git push",
            "subprocess.call",
            "shell=True",
            "--add-assignee",
            "--milestone",
            "--title",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, src)


if __name__ == "__main__":
    unittest.main()
