#!/usr/bin/env python3
"""Behavior tests for the phase9-router source-OPEN GitHub state gate."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop import labels as label_catalog
from codex_refactor_loop.managed_work_snapshot import ManagedWorkSnapshotItem, ManagedWorkSnapshotResult
from codex_refactor_loop.phase9.router import Phase9Router


class Phase9RouterOpenStateGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / ".refactor-loop" / "logs").mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def router(self, *, gh_repo_slug: str | None = None) -> Phase9Router:
        env = {"GH_REPO_SLUG": gh_repo_slug} if gh_repo_slug else {}
        return Phase9Router(ctx=LoopContext.load(repo_root=self.repo, env=env))

    def assert_state_only_read(self, command: list[str], *, issue: str, repo_slug: str | None = None) -> None:
        expected = ["gh", "api", f"repos/{repo_slug}/issues/{issue}"]
        self.assertEqual(expected, command)
        forbidden = {
            "--add-label",
            "--assignee",
            "--body",
            "--label",
            "--remove-label",
            "--state",
            "--title",
            "close",
            "comment",
            "create",
            "delete",
            "edit",
            "label",
            "merge",
            "pr",
            "release",
            "reopen",
            "view",
        }
        self.assertFalse(set(command) & forbidden)

    def test_open_state_allows_dispatch_using_exact_state_only_issue_read(self) -> None:
        result = mock.Mock(returncode=0, stdout=json.dumps({"state": "open", "title": "issue", "body": ""}), stderr="")
        with mock.patch("codex_refactor_loop.phase9.router.subprocess.run", return_value=result) as run:
            decision = self.router(gh_repo_slug="owner/repo")._read_source_issue_decision("37")

        self.assertTrue(decision.allowed)
        self.assertEqual("OPEN", decision.state)
        self.assertEqual("phase9-source-open", decision.reason)
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assert_state_only_read(command, issue="37", repo_slug="owner/repo")
        self.assertEqual(str(self.repo.resolve()), run.call_args.kwargs["cwd"])
        self.assertEqual(15, run.call_args.kwargs["timeout"])
        self.assertFalse(run.call_args.kwargs["check"])

    def test_closed_state_fails_closed_using_repo_scoped_state_only_issue_read(self) -> None:
        result = mock.Mock(returncode=0, stdout=json.dumps({"state": "CLOSED"}), stderr="")
        with mock.patch("codex_refactor_loop.phase9.router.subprocess.run", return_value=result) as run:
            decision = self.router(gh_repo_slug="owner/repo")._read_source_issue_decision("245")

        self.assertFalse(decision.allowed)
        self.assertEqual("CLOSED", decision.state)
        self.assertEqual("phase9-source-not-open", decision.reason)
        run.assert_called_once()
        self.assert_state_only_read(run.call_args.args[0], issue="245", repo_slug="owner/repo")

    def test_design_issue_intake_uses_open_managed_list_without_lifecycle_commands(self) -> None:
        rows = [
            {
                "number": 416,
                "title": "design issue",
                "labels": [
                    {"name": label_catalog.MANAGED},
                    {"name": label_catalog.PHASE_DESIGN_SOLVING},
                    {"name": label_catalog.HUMAN_AUTO},
                ],
            }
        ]
        snapshot = ManagedWorkSnapshotResult(
            (
                ManagedWorkSnapshotItem(
                    kind="issue",
                    number=416,
                    title="design issue",
                    labels=(label_catalog.MANAGED, label_catalog.PHASE_DESIGN_SOLVING, label_catalog.HUMAN_AUTO),
                ),
            ),
            True,
            "cache:fresh",
        )
        with mock.patch("codex_refactor_loop.phase9.router.load_open_managed_work_snapshot", return_value=snapshot) as load_snapshot:
            issues = self.router(gh_repo_slug="owner/repo")._open_design_consensus_issues()

        self.assertEqual([issue.number for issue in issues], ["416"])
        load_snapshot.assert_called_once()


if __name__ == "__main__":
    unittest.main()
