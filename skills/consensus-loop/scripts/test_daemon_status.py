#!/usr/bin/env python3
"""Behavior tests for read-only daemon status projection."""

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

from codex_refactor_loop import daemon_status


class DaemonStatusProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="daemon-status-test-"))
        (self.tmp / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\n',
            encoding="utf-8",
        )
        state = self.tmp / ".refactor-loop" / "state"
        state.mkdir(parents=True)
        (state / "active-controller-status.json").write_text(
            json.dumps(
                {
                    "active_controller": "noop:not-owner",
                    "owner_device": "device-a",
                    "current_github_login": "octocat",
                    "identity_authority": "display-only",
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_collect_projects_cached_display_only_login(self) -> None:
        env = {"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("codex_refactor_loop.daemon_status.DaemonProcessInventory.collect") as collect_inventory:
                collect_inventory.return_value = daemon_status.DaemonProcessInventory(())
                report = daemon_status.collect(repo_root=self.tmp, skill_root=SCRIPT_DIR.parent)

        payload = report.to_json()
        self.assertEqual("noop:not-owner", payload["active_controller"])
        self.assertEqual("octocat", payload["current_github_login"])
        self.assertEqual("display-only", payload["identity_authority"])
        self.assertTrue(payload["daemons"])
        self.assertEqual("not-owner", payload["daemons"][0]["status"])
        self.assertEqual("octocat", payload["daemons"][0]["current_github_login"])
        self.assertEqual("display-only", payload["daemons"][0]["identity_authority"])
        self.assertIn("heartbeat_status", payload["daemons"][0])
        self.assertIn("stale_reason", payload["daemons"][0])

    def test_stale_heartbeat_reason_and_age_are_read_only(self) -> None:
        env = {"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}
        heartbeat = self.tmp / ".refactor-loop" / "heartbeats" / "concurrency_monitor.ts"
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        heartbeat.write_text("1000\n", encoding="utf-8")
        pid = self.tmp / ".refactor-loop" / "locks" / "concurrency_monitor.pid"
        pid.parent.mkdir(parents=True, exist_ok=True)
        pid.write_text("123\n", encoding="utf-8")
        (self.tmp / ".refactor-loop" / "state" / "active-controller-status.json").write_text(
            json.dumps({"active_controller": "owner"}) + "\n",
            encoding="utf-8",
        )

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("codex_refactor_loop.daemon_status.DaemonProcessInventory.collect") as collect_inventory:
                collect_inventory.return_value = daemon_status.DaemonProcessInventory(())
                with mock.patch("codex_refactor_loop.restart.time.time", return_value=1120):
                    with mock.patch("codex_refactor_loop.daemon_status.pid_alive", return_value=True):
                        report = daemon_status.collect(repo_root=self.tmp, skill_root=SCRIPT_DIR.parent)

        daemon = next(item for item in report.to_json()["daemons"] if item["name"] == "concurrency_monitor")
        self.assertEqual("stale", daemon["status"])
        self.assertEqual("stale", daemon["heartbeat_status"])
        self.assertEqual(120, daemon["heartbeat_age_seconds"])
        self.assertEqual("heartbeat-stale:120s", daemon["stale_reason"])
        self.assertEqual("123\n", pid.read_text(encoding="utf-8"))
        self.assertEqual("1000\n", heartbeat.read_text(encoding="utf-8"))

    def test_future_heartbeat_is_malformed_stale_projection_without_writing_it(self) -> None:
        env = {"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}
        heartbeat = self.tmp / ".refactor-loop" / "heartbeats" / "comment-monitor.ts"
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        heartbeat.write_text("1200\n", encoding="utf-8")
        pid = self.tmp / ".refactor-loop" / "locks" / "comment-monitor.pid"
        pid.parent.mkdir(parents=True, exist_ok=True)
        pid.write_text("456\n", encoding="utf-8")
        (self.tmp / ".refactor-loop" / "state" / "active-controller-status.json").write_text(
            json.dumps({"active_controller": "owner"}) + "\n",
            encoding="utf-8",
        )

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("codex_refactor_loop.daemon_status.DaemonProcessInventory.collect") as collect_inventory:
                collect_inventory.return_value = daemon_status.DaemonProcessInventory(())
                with mock.patch("codex_refactor_loop.restart.time.time", return_value=1120):
                    with mock.patch("codex_refactor_loop.daemon_status.pid_alive", return_value=True):
                        report = daemon_status.collect(repo_root=self.tmp, skill_root=SCRIPT_DIR.parent)

        daemon = next(item for item in report.to_json()["daemons"] if item["name"] == "comment-monitor")
        self.assertEqual("stale", daemon["status"])
        self.assertEqual("malformed", daemon["heartbeat_status"])
        self.assertIsNone(daemon["heartbeat_age_seconds"])
        self.assertEqual("heartbeat-future", daemon["stale_reason"])
        self.assertEqual("456\n", pid.read_text(encoding="utf-8"))
        self.assertEqual("1200\n", heartbeat.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
