#!/usr/bin/env python3
"""Behavior and source-regression tests for #193 lease gates."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
REPO_ROOT = SKILL_ROOT.parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.coordination.leases import LeaseDecision
from codex_refactor_loop.monitors.comment import CommentMonitor
from codex_refactor_loop.monitors.concurrency import ConcurrencyMonitor
from codex_refactor_loop.phase9.router import Phase9Router
from codex_refactor_loop.sync.dev import IntegrationSyncDaemon


class LosingGate:
    def work_claim(self, key: str, *, reason: str = "", target: str = "") -> LeaseDecision:
        return LeaseDecision(False, "leased-by:other")

    def singleton(self, key: str, *, reason: str = "", target: str = "") -> LeaseDecision:
        return LeaseDecision(False, "leased-by:other")

    def renew(self, token):
        return LeaseDecision(False, "leased-by:other")

    def release(self, token):
        return LeaseDecision(False, "leased-by:other")


class WinningGate:
    def work_claim(self, key: str, *, reason: str = "", target: str = "") -> LeaseDecision:
        return LeaseDecision(True, "claimed")

    def singleton(self, key: str, *, reason: str = "", target: str = "") -> LeaseDecision:
        return LeaseDecision(True, "claimed")

    def renew(self, token):
        return LeaseDecision(True, "renewed")

    def release(self, token):
        return LeaseDecision(True, "released")


class MultiDeviceCoordinationGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="multi-device-test-"))
        (self.tmp / ".refactor-loop" / "logs").mkdir(parents=True)
        (self.tmp / ".refactor-loop" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\n'
            'export GH_REPO_SLUG="owner/repo"\n'
            'export MAINTAINER_WHITELIST="maintainer"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dispatch_queue_lease_miss_spawns_zero_and_leaves_queue_pending(self) -> None:
        monitor = ConcurrencyMonitor(self.ctx)
        monitor.lease_gate = LosingGate()
        q = self.tmp / ".refactor-loop" / "dispatch-queue" / "p0"
        q.mkdir(parents=True)
        prompt = self.tmp / ".refactor-loop" / "prompts" / "task.md"
        log = self.tmp / ".refactor-loop" / "logs" / "task.log"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("prompt\n", encoding="utf-8")
        payload = {
            "task_id": "fix-pr44-round-3",
            "cd": str(self.tmp / ".worktrees" / "fix-pr44-round-3"),
            "prompt": str(prompt),
            "log": str(log),
        }
        dispatch = q / "fix-pr44-round-3.dispatch.json"
        dispatch.write_text(json.dumps(payload), encoding="utf-8")

        with mock.patch.object(monitor, "launch_dispatch") as launch:
            fired = monitor.dispatch_one_from_queue()

        self.assertIsNone(fired)
        launch.assert_not_called()
        self.assertTrue(dispatch.exists())
        events = (self.tmp / ".refactor-loop" / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("DISPATCH_LEASE_LOST:fix-pr44-round-3:p0", events)

    def test_phase9_router_lease_miss_spawns_zero(self) -> None:
        router = Phase9Router(ctx=self.ctx, command_runner=lambda _cmd: None)
        router.lease_gate = LosingGate()
        for role in ("minimal", "structural", "delete"):
            (self.tmp / ".refactor-loop" / "logs" / f"phase9-issue193-r2-{role}.log").write_text(
                f"SOLVER_DONE:{role}:same\nEXIT=0\n",
                encoding="utf-8",
            )

        router.tick()

        self.assertEqual(set(), router._read_ledger())
        events = (self.tmp / ".refactor-loop" / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("phase9-router-lease-lost", events)

    def test_comment_monitor_lease_miss_performs_no_reaction_or_banner(self) -> None:
        monitor = CommentMonitor(self.ctx, interval=1)
        monitor.lease_gate = LosingGate()
        calls: list[list[str]] = []

        def fake_run(command, cwd, *, check):
            del cwd, check
            calls.append(list(command))
            text = " ".join(command)
            if "issue list" in text:
                return mock.Mock(returncode=0, stdout="42\n", stderr="")
            if "pr list" in text:
                return mock.Mock(returncode=0, stdout="", stderr="")
            if "issues/42/comments" in text:
                return mock.Mock(returncode=0, stdout=json.dumps({"id": 99, "author": "maintainer", "body": "please", "created_at": "2026-05-29T00:00:00Z"}) + "\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("codex_refactor_loop.monitors.comment._run", side_effect=fake_run):
            monitor.tick()

        self.assertFalse(any("reactions" in " ".join(call) for call in calls))
        self.assertFalse(any("issue comment" in " ".join(call) for call in calls))
        state = json.loads((self.tmp / ".refactor-loop" / "comment-monitor-state.json").read_text(encoding="utf-8"))
        self.assertEqual("lease-lost:unknown", state["99"])

    def test_dev_sync_lease_miss_performs_no_fetch_emit_or_dispatch(self) -> None:
        commands: list[list[str]] = []
        dispatched: list[bool] = []
        daemon = IntegrationSyncDaemon(
            worktree=self.tmp / "wt",
            main_repo=self.tmp,
            integration="auto-refact-dev",
            review_base="dev",
            command_runner=lambda cmd, cwd=None, check=False: commands.append(cmd) or subprocess.CompletedProcess(cmd, 0, "0\n", ""),
            logger=lambda _msg: None,
            ensure_worktree_fn=lambda: True,
            resolver_dispatcher=lambda: dispatched.append(True),
            lease_gate=LosingGate(),
        )

        daemon.tick()

        self.assertEqual([], commands)
        self.assertEqual([], dispatched)
        self.assertFalse((self.tmp / ".refactor-loop" / ".controller-pending-events.log").exists())

    def test_source_regression_forbids_rejected_design_surfaces(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in [
                SKILL_ROOT / "SKILL.md",
                SKILL_ROOT / "authorizations" / "runtime-exceptions.md",
                SCRIPT_DIR / "codex_refactor_loop" / "coordination" / "leases.py",
            ]
        )
        for required in (
            "refs/heads/auto-loop/leases/*",
            "work-claim",
            "singleton",
            "Authoritative lease truth is the git ref payload only",
            "comments and labels remain projection only",
            "git update-ref",
            "git push --force-with-lease",
            "no generic lifecycle actor",
        ):
            with self.subTest(required=required):
                self.assertIn(required, combined)
        for forbidden in (
            "DeviceLeaseDaemon",
            ".refactor-loop/device-claims",
            "WorkUnitClaim",
            "label/comment overlay is authoritative",
            "labels are authoritative",
            "comments are authoritative",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
