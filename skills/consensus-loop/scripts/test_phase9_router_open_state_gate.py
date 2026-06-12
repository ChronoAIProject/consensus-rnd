#!/usr/bin/env python3
"""Behavior tests for the phase9-router source-OPEN GitHub state gate."""

from __future__ import annotations

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

    def test_open_state_allows_dispatch_using_open_managed_snapshot_without_live_issue_read(self) -> None:
        snapshot = ManagedWorkSnapshotResult(
            (
                ManagedWorkSnapshotItem(
                    kind="issue",
                    number=37,
                    title="issue",
                    labels=(label_catalog.MANAGED,),
                    state="open",
                ),
            ),
            True,
            "cache:fresh",
        )
        with (
            mock.patch("codex_refactor_loop.phase9.router.load_open_managed_work_snapshot", return_value=snapshot),
            mock.patch("codex_refactor_loop.phase9.router.subprocess.run") as run,
        ):
            decision = self.router(gh_repo_slug="owner/repo")._read_source_issue_decision("37")

        self.assertTrue(decision.allowed)
        self.assertEqual("OPEN", decision.state)
        self.assertEqual("phase9-source-open", decision.reason)
        run.assert_not_called()

    def test_absent_snapshot_target_fails_closed_without_live_issue_read(self) -> None:
        snapshot = ManagedWorkSnapshotResult((), True, "cache:fresh")
        with (
            mock.patch("codex_refactor_loop.phase9.router.load_open_managed_work_snapshot", return_value=snapshot),
            mock.patch("codex_refactor_loop.phase9.router.subprocess.run") as run,
        ):
            decision = self.router(gh_repo_slug="owner/repo")._read_source_issue_decision("245")

        self.assertFalse(decision.allowed)
        self.assertEqual("ABSENT_FROM_OPEN_MANAGED_SNAPSHOT", decision.state)
        self.assertEqual("phase9-source-not-open", decision.reason)
        run.assert_not_called()

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

    def test_terminal_gate_reads_open_managed_closing_pr_from_snapshot(self) -> None:
        snapshot = ManagedWorkSnapshotResult(
            (
                ManagedWorkSnapshotItem(
                    kind="issue",
                    number=858,
                    title="design issue",
                    labels=(label_catalog.MANAGED, label_catalog.PHASE_DESIGN_SOLVING, label_catalog.HUMAN_AUTO),
                    state="open",
                ),
                ManagedWorkSnapshotItem(
                    kind="PR",
                    number=900,
                    title="implementation",
                    labels=(label_catalog.MANAGED, label_catalog.PHASE_REVIEWING, label_catalog.HUMAN_AUTO),
                    body="Closes #858\n",
                    state="open",
                ),
            ),
            True,
            "cache:fresh",
        )
        with (
            mock.patch("codex_refactor_loop.phase9.router.load_open_managed_work_snapshot", return_value=snapshot),
            mock.patch("codex_refactor_loop.phase9.router.subprocess.run") as run,
        ):
            decision = self.router(gh_repo_slug="owner/repo")._solver_dispatch_terminal_decision("858")

        self.assertFalse(decision.allowed)
        self.assertEqual("phase9-already-consensus", decision.reason)
        self.assertEqual("open-managed-closing-pr:900", decision.terminal_source)
        run.assert_not_called()

    def test_terminal_gate_does_not_guess_duplicate_open_managed_closing_prs(self) -> None:
        snapshot = ManagedWorkSnapshotResult(
            (
                ManagedWorkSnapshotItem(
                    kind="issue",
                    number=858,
                    title="design issue",
                    labels=(label_catalog.MANAGED, label_catalog.PHASE_DESIGN_SOLVING, label_catalog.HUMAN_AUTO),
                    state="open",
                ),
                ManagedWorkSnapshotItem(
                    kind="PR",
                    number=900,
                    title="implementation A",
                    labels=(label_catalog.MANAGED, label_catalog.PHASE_REVIEWING, label_catalog.HUMAN_AUTO),
                    body="Closes #858\n",
                    state="open",
                ),
                ManagedWorkSnapshotItem(
                    kind="PR",
                    number=901,
                    title="implementation B",
                    labels=(label_catalog.MANAGED, label_catalog.PHASE_REVIEWING, label_catalog.HUMAN_AUTO),
                    body="Closes #858\n",
                    state="open",
                ),
            ),
            True,
            "cache:fresh",
        )
        with (
            mock.patch("codex_refactor_loop.phase9.router.load_open_managed_work_snapshot", return_value=snapshot),
            mock.patch("codex_refactor_loop.phase9.router.subprocess.run", return_value=mock.Mock(returncode=1)),
        ):
            decision = self.router(gh_repo_slug="owner/repo")._solver_dispatch_terminal_decision("858")

        self.assertTrue(decision.allowed)
        self.assertEqual("phase9-terminal-open", decision.reason)
        self.assertIsNone(decision.terminal_source)


if __name__ == "__main__":
    unittest.main()
