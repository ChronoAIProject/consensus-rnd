#!/usr/bin/env python3
"""Behavior tests for shared read-only controller projection."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.daemon_status import DaemonStatusProjection, DaemonStatusReport
from codex_refactor_loop.managed_work_snapshot import ManagedWorkSnapshotItem, ManagedWorkSnapshotResult
from codex_refactor_loop.projections import ProjectionRequest, collect_shared_controller_projection


class SharedControllerProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="shared-projection-test-"))
        self.repo = self.tmp_root / "repo"
        self.skill = self.tmp_root / "skill"
        (self.repo / ".config" / "consensus-rnd").mkdir(parents=True)
        (self.skill / "scripts").mkdir(parents=True)
        self.host_env = self.repo / ".config" / "consensus-rnd" / "host.env"
        self.host_env.write_text(
            f'export REPO_ROOT="{self.repo}"\nexport GH_REPO_SLUG="example/repo"\n',
            encoding="utf-8",
        )
        self.env_patch = mock.patch.dict(os.environ, {"CONSENSUS_RND_HOST_ENV": str(self.host_env)})
        self.env_patch.start()
        self.ctx = LoopContext.load(repo_root=self.repo, skill_root=self.skill)

    def tearDown(self) -> None:
        self.env_patch.stop()
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_projection_aggregates_owner_local_read_models(self) -> None:
        self.ctx.paths.statusline_snapshot.parent.mkdir(parents=True, exist_ok=True)
        self.ctx.paths.statusline_snapshot.write_text(json.dumps({"actual": 2, "floor": 5}) + "\n", encoding="utf-8")

        def managed_loader(_ctx: LoopContext) -> ManagedWorkSnapshotResult:
            return ManagedWorkSnapshotResult(
                (
                    ManagedWorkSnapshotItem(kind="issue", number=553),
                    ManagedWorkSnapshotItem(kind="PR", number=77),
                ),
                True,
                "cache:fresh",
                None,
                12.0,
            )

        def daemon_collector(*_args, **_kwargs) -> DaemonStatusReport:
            return DaemonStatusReport(
                repo_root=str(self.repo),
                active_controller="owner",
                generated_at="2026-06-06T00:00:00Z",
                daemons=(
                    DaemonStatusProjection("comment-monitor", "running", 1, 1, True, True, 0, "owner"),
                    DaemonStatusProjection("phase9_router_daemon", "stale", 2, 100, False, True, 0, "owner"),
                ),
            )

        projection = collect_shared_controller_projection(
            self.ctx,
            ProjectionRequest(),
            managed_work_loader=managed_loader,
            daemon_status_collector=daemon_collector,
            workqueue_keys=("phase9-router:issue/553",),
        )

        self.assertTrue(projection.no_lifecycle_authority)
        self.assertTrue(projection.not_host_production_ssot)
        self.assertEqual(1, projection.managed_work.open_issue_count)
        self.assertEqual(1, projection.managed_work.open_pr_count)
        self.assertEqual(2, projection.daemon_fleet.total)
        self.assertEqual(1, projection.daemon_fleet.running)
        self.assertEqual(1, projection.daemon_fleet.stale)
        self.assertEqual({"actual": 2, "floor": 5}, dict(projection.statusline))
        self.assertEqual(("phase9-router:issue/553",), projection.workqueue_keys)

    def test_request_can_disable_expensive_sources(self) -> None:
        def failing_managed_loader(_ctx: LoopContext) -> ManagedWorkSnapshotResult:
            raise AssertionError("managed work loader should not run")

        def failing_daemon_collector(*_args, **_kwargs) -> DaemonStatusReport:
            raise AssertionError("daemon collector should not run")

        projection = collect_shared_controller_projection(
            self.ctx,
            ProjectionRequest(include_managed_work=False, include_daemon_status=False, include_statusline=False),
            managed_work_loader=failing_managed_loader,
            daemon_status_collector=failing_daemon_collector,
            workqueue_keys=("comment-monitor:issue/553",),
        )

        self.assertIsNone(projection.managed_work)
        self.assertIsNone(projection.daemon_fleet)
        self.assertEqual({}, dict(projection.statusline))
        self.assertEqual(("comment-monitor:issue/553",), projection.workqueue_keys)


if __name__ == "__main__":
    unittest.main()
