#!/usr/bin/env python3
"""Authority boundary tests for patrol inspector."""

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
from codex_refactor_loop.patrol import PatrolInspector, PatrolInspectorConfig


class FailingPublisher:
    def publish(self, **_kwargs):
        raise AssertionError("publisher must not be called")


class PatrolAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="patrol-authority-test-"))
        for rel in (".config/consensus-rnd", ".refactor-loop/logs", ".refactor-loop/state"):
            (self.tmp / rel).mkdir(parents=True, exist_ok=True)
        self.host_env = self.tmp / ".config" / "consensus-rnd" / "host.env"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def context(self, enable: str) -> LoopContext:
        self.host_env.write_text(
            f'export REPO_ROOT="{self.tmp}"\n'
            'export GH_REPO_SLUG="owner/repo"\n'
            f'export PATROL_INSPECTOR_ENABLE="{enable}"\n',
            encoding="utf-8",
        )
        return LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})

    def test_disabled_host_opt_in_is_noop_before_owner_gate(self) -> None:
        ctx = self.context("false")
        with mock.patch("codex_refactor_loop.patrol.require_active_controller") as owner:
            result = PatrolInspector(
                ctx,
                config=PatrolInspectorConfig(enabled=False, interval_seconds=7200, max_findings=25),
                publisher=FailingPublisher(),
            ).run_once()

        self.assertEqual(0, result)
        owner.assert_not_called()
        state = json.loads((self.tmp / ".refactor-loop" / "state" / "patrol-inspector.json").read_text(encoding="utf-8"))
        self.assertEqual("disabled", state["status"])

    def test_non_owner_writes_noop_state_and_does_not_publish(self) -> None:
        ctx = self.context("true")
        decision = mock.Mock(allowed=False, owner_device="other", status="not-owner", action="patrol-inspector", lease_id="", expires_at="")
        with mock.patch("codex_refactor_loop.patrol.require_active_controller", return_value=decision):
            result = PatrolInspector(
                ctx,
                config=PatrolInspectorConfig(enabled=True, interval_seconds=7200, max_findings=25),
                publisher=FailingPublisher(),
            ).run_once()

        self.assertEqual(0, result)
        state = json.loads((self.tmp / ".refactor-loop" / "state" / "patrol-inspector.json").read_text(encoding="utf-8"))
        self.assertEqual("noop:not-owner", state["status"])

    def test_source_contains_no_generic_lifecycle_or_git_authority(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "patrol.py").read_text(encoding="utf-8")
        publisher = (SCRIPT_DIR / "codex_refactor_loop" / "patrol_issue_publisher.py").read_text(encoding="utf-8")
        for forbidden in ("git ", "gh pr", "label edit", "issue close", "issue reopen", "merge", "release create"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, (source + publisher).lower())
        self.assertIn("require_active_controller", source)
        self.assertIn("PATROL_INSPECTOR_ENABLE", source)
        self.assertIn('"create"', publisher)
        self.assertIn('"edit"', publisher)
        self.assertIn('"issue"', publisher)


if __name__ == "__main__":
    unittest.main()
