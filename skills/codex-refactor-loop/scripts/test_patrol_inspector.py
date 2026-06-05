#!/usr/bin/env python3
"""Behavior tests for the patrol inspector."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.patrol import PatrolInspector, PatrolInspectorConfig


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, str]] = []

    def publish(self, *, fingerprint: str, title: str, body: str):
        self.published.append((fingerprint, title, body))
        return type("Issue", (), {"number": 100 + len(self.published)})()


class PatrolInspectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="patrol-inspector-test-"))
        for rel in (".config/consensus-rnd", ".refactor-loop/logs", ".refactor-loop/runs", ".refactor-loop/state"):
            (self.tmp / rel).mkdir(parents=True, exist_ok=True)
        (self.tmp / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\n'
            'export GH_REPO_SLUG="owner/repo"\n'
            'export PATROL_INSPECTOR_ENABLE="true"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_collects_local_exception_runtime_and_snapshot_findings(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "router.log").write_text("ok\nRuntimeError: broken\n", encoding="utf-8")
        (self.tmp / ".refactor-loop" / "runs" / "implement-issue-1.md").write_text("IMPLEMENT_DONE:issue-1:ok\n", encoding="utf-8")
        (self.tmp / ".refactor-loop" / "state" / "wakeup-plan.json").write_text(
            json.dumps({"status": "error", "reason": "bad"}) + "\n",
            encoding="utf-8",
        )
        items = [{"kind": "issue", "number": 5, "labels": ("crnd:lifecycle:managed",), "title": "missing phase"}]

        findings = PatrolInspector(self.ctx, github_items=items).collect_findings()

        self.assertEqual(
            {"exception-log", "runtime-artifact", "projection", "managed-snapshot"},
            {finding.kind for finding in findings},
        )

    def test_run_once_publishes_findings_and_writes_dashboard_state(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "router.log").write_text("FATAL: failed\n", encoding="utf-8")
        publisher = FakePublisher()
        inspector = PatrolInspector(
            self.ctx,
            config=PatrolInspectorConfig(enabled=True, interval_seconds=7200, max_findings=25),
            publisher=publisher,
            github_items=(),
        )

        self.assertEqual(0, inspector.run_once())

        self.assertEqual(1, len(publisher.published))
        state = json.loads((self.tmp / ".refactor-loop" / "state" / "patrol-inspector.json").read_text(encoding="utf-8"))
        self.assertEqual("ok", state["status"])
        self.assertEqual(1, len(state["findings"]))
        self.assertEqual(1, len(state["published"]))

    def test_snapshot_load_failure_is_visible_and_blocks_publication(self) -> None:
        (self.tmp / ".refactor-loop" / "logs" / "router.log").write_text("FATAL: failed\n", encoding="utf-8")
        publisher = FakePublisher()
        inspector = PatrolInspector(
            self.ctx,
            config=PatrolInspectorConfig(enabled=True, interval_seconds=7200, max_findings=25),
            publisher=publisher,
        )

        with patch("codex_refactor_loop.patrol.load_github_items_with_status", side_effect=ValueError("bad snapshot")):
            with self.assertRaisesRegex(RuntimeError, "patrol managed snapshot load failed"):
                inspector.run_once()

        self.assertEqual([], publisher.published)
        state = json.loads((self.tmp / ".refactor-loop" / "state" / "patrol-inspector.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", state["status"])
        self.assertIn("bad snapshot", state["reason"])
        self.assertEqual([], state["published"])

    def test_unavailable_snapshot_status_is_visible_and_blocks_publication(self) -> None:
        publisher = FakePublisher()
        inspector = PatrolInspector(
            self.ctx,
            config=PatrolInspectorConfig(enabled=True, interval_seconds=7200, max_findings=25),
            publisher=publisher,
        )

        with patch("codex_refactor_loop.patrol.load_github_items_with_status", return_value=([], False)):
            with self.assertRaisesRegex(RuntimeError, "loaded_ok_false"):
                inspector.run_once()

        self.assertEqual([], publisher.published)
        state = json.loads((self.tmp / ".refactor-loop" / "state" / "patrol-inspector.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", state["status"])
        self.assertIn("loaded_ok_false", state["reason"])


if __name__ == "__main__":
    unittest.main()
