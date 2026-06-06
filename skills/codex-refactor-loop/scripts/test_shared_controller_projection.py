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

        payload = projection.to_json()
        self.assertEqual(projection.repo_root, payload["repo_root"])
        self.assertTrue(payload["request"]["include_managed_work"])
        self.assertTrue(payload["request"]["include_daemon_status"])
        self.assertTrue(payload["request"]["include_statusline"])
        self.assertTrue(payload["request"]["include_workqueue_keys"])
        self.assertEqual("all", payload["request"]["daemon_target"])
        self.assertEqual(1, payload["managed_work"]["open_issue_count"])
        self.assertEqual(1, payload["managed_work"]["open_pr_count"])
        self.assertEqual("cache:fresh", payload["managed_work"]["source"])
        self.assertEqual(2, payload["daemon_fleet"]["total"])
        self.assertEqual(1, payload["daemon_fleet"]["running"])
        self.assertEqual(1, payload["daemon_fleet"]["stale"])
        self.assertEqual({"actual": 2, "floor": 5}, payload["statusline"])
        self.assertEqual(["phase9-router:issue/553"], payload["workqueue_keys"])
        freshness = payload["freshness"]
        self.assertEqual(projection.generated_at, freshness["generated_at"])
        self.assertTrue(freshness["overall_loaded_ok"])
        self.assertEqual(0, freshness["failed_source_count"])
        self.assertEqual(1, freshness["stale_source_count"])
        freshness_by_source = {row["source"]: row for row in freshness["sources"]}
        self.assertEqual(
            {
                "managed_work_snapshot",
                "daemon_status",
                "statusline_snapshot",
                "key_only_workqueue",
            },
            set(freshness_by_source),
        )
        self.assertEqual(
            {
                "source",
                "loaded_ok",
                "reason",
                "age_seconds",
                "next_retry_after_seconds",
                "stale",
            },
            set(freshness_by_source["managed_work_snapshot"]),
        )
        self.assertTrue(freshness_by_source["managed_work_snapshot"]["loaded_ok"])
        self.assertEqual("cache:fresh", freshness_by_source["managed_work_snapshot"]["reason"])
        self.assertEqual(12.0, freshness_by_source["managed_work_snapshot"]["age_seconds"])
        self.assertEqual(288.0, freshness_by_source["managed_work_snapshot"]["next_retry_after_seconds"])
        self.assertTrue(freshness_by_source["daemon_status"]["stale"])
        self.assertEqual("daemon-unhealthy:1", freshness_by_source["daemon_status"]["reason"])
        self.assertTrue(payload["no_lifecycle_authority"])
        self.assertTrue(payload["not_host_production_ssot"])

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
        freshness_by_source = {row["source"]: row for row in projection.to_json()["freshness"]["sources"]}
        self.assertEqual("disabled-by-request", freshness_by_source["managed_work_snapshot"]["reason"])
        self.assertEqual("disabled-by-request", freshness_by_source["daemon_status"]["reason"])
        self.assertEqual("disabled-by-request", freshness_by_source["statusline_snapshot"]["reason"])
        self.assertTrue(freshness_by_source["key_only_workqueue"]["loaded_ok"])

    def test_request_can_omit_workqueue_keys_from_projection_and_json(self) -> None:
        projection = collect_shared_controller_projection(
            self.ctx,
            ProjectionRequest(
                include_managed_work=False,
                include_daemon_status=False,
                include_statusline=False,
                include_workqueue_keys=False,
            ),
            workqueue_keys=("comment-monitor:issue/553",),
        )

        self.assertEqual((), projection.workqueue_keys)
        self.assertEqual([], projection.to_json()["workqueue_keys"])
        self.assertFalse(projection.to_json()["request"]["include_workqueue_keys"])
        freshness_by_source = {row["source"]: row for row in projection.to_json()["freshness"]["sources"]}
        self.assertEqual("disabled-by-request", freshness_by_source["key_only_workqueue"]["reason"])

    def test_malformed_statusline_snapshot_emits_diagnostic_before_empty_projection(self) -> None:
        self.ctx.paths.statusline_snapshot.parent.mkdir(parents=True, exist_ok=True)
        self.ctx.paths.statusline_snapshot.write_text("{bad json", encoding="utf-8")

        with mock.patch("sys.stderr") as stderr:
            projection = collect_shared_controller_projection(
                self.ctx,
                ProjectionRequest(include_managed_work=False, include_daemon_status=False),
            )

        self.assertEqual({}, dict(projection.statusline))
        self.assertFalse(projection.to_json()["freshness"]["overall_loaded_ok"])
        self.assertEqual(1, projection.to_json()["freshness"]["failed_source_count"])
        freshness_by_source = {row["source"]: row for row in projection.to_json()["freshness"]["sources"]}
        self.assertFalse(freshness_by_source["statusline_snapshot"]["loaded_ok"])
        self.assertEqual("json-error:line=1:column=2", freshness_by_source["statusline_snapshot"]["reason"])
        self.assertEqual(0.0, freshness_by_source["statusline_snapshot"]["next_retry_after_seconds"])
        stderr.write.assert_called_once()
        self.assertIn("SHARED_PROJECTION_STATUSLINE_READ_FAILED", stderr.write.call_args.args[0])
        self.assertIn("json-error:line=1:column=2", stderr.write.call_args.args[0])

    def test_stale_managed_work_snapshot_reports_immediate_retry(self) -> None:
        def managed_loader(_ctx: LoopContext) -> ManagedWorkSnapshotResult:
            return ManagedWorkSnapshotResult(
                (ManagedWorkSnapshotItem(kind="issue", number=578),),
                True,
                "cache:stale",
                None,
                640.0,
            )

        projection = collect_shared_controller_projection(
            self.ctx,
            ProjectionRequest(include_daemon_status=False, include_statusline=False, include_workqueue_keys=False),
            managed_work_loader=managed_loader,
        )

        freshness = projection.to_json()["freshness"]
        self.assertTrue(freshness["overall_loaded_ok"])
        self.assertEqual(1, freshness["stale_source_count"])
        managed_freshness = {row["source"]: row for row in freshness["sources"]}["managed_work_snapshot"]
        self.assertEqual("cache:stale", managed_freshness["reason"])
        self.assertEqual(640.0, managed_freshness["age_seconds"])
        self.assertEqual(0.0, managed_freshness["next_retry_after_seconds"])


if __name__ == "__main__":
    unittest.main()
