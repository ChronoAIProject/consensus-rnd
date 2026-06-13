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
from codex_refactor_loop import restart
from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.daemon_progress import begin_tick, complete_tick, fail_tick
from codex_refactor_loop.daemon_singleton import DaemonSingletonProjection


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
        self.assertIn("progress_status", payload["daemons"][0])
        self.assertIn("progress_age_seconds", payload["daemons"][0])
        self.assertIn("progress_reason", payload["daemons"][0])
        self.assertIn("singleton_lock_path", payload["daemons"][0])
        self.assertIn("singleton_lock_state", payload["daemons"][0])
        self.assertIn("singleton_lock_holder_pid", payload["daemons"][0])
        self.assertIn("singleton_lock_metadata_valid", payload["daemons"][0])
        self.assertIn("singleton_lock_reason", payload["daemons"][0])

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

    def test_overdue_progress_is_stale_even_when_heartbeat_is_fresh(self) -> None:
        env = {"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}
        ctx = LoopContext.load(
            repo_root=self.tmp,
            skill_root=SCRIPT_DIR.parent,
            read_only=True,
            env=env,
        )
        target = restart.daemon_target(
            ctx,
            "concurrency_monitor",
            ("python3", "{skill_root}/scripts/consensus-rnd-cli", "concurrency", "--daemon"),
        )
        target.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
        target.heartbeat_file.write_text("1000\n", encoding="utf-8")
        target.pid_file.parent.mkdir(parents=True, exist_ok=True)
        target.pid_file.write_text("848\n", encoding="utf-8")
        restart.DaemonLaunchFingerprint.current(ctx, "concurrency_monitor", target.command).write(target.fingerprint_file)
        progress = begin_tick(self.tmp, "concurrency_monitor", now=300, pid=857)
        complete_tick(self.tmp, progress, now=300)
        wrapper_command = " ".join(
            (
                sys.executable,
                "-c",
                restart.WRAPPER_CODE,
                "concurrency_monitor",
                str(ctx.repo_root),
                str(target.pid_file),
                str(target.died_file),
                *target.command,
            )
        )
        inventory = restart.DaemonProcessInventory(
            (
                restart.DaemonProcess(848, wrapper_command, 1),
                restart.DaemonProcess(857, "python3 /tmp/old/consensus-rnd-cli concurrency --daemon", 848),
            )
        )
        singleton = DaemonSingletonProjection(target.singleton_lock_file, "held", 857, True, "lock-held")
        (self.tmp / ".refactor-loop" / "state" / "active-controller-status.json").write_text(
            json.dumps({"active_controller": "owner"}) + "\n",
            encoding="utf-8",
        )

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("codex_refactor_loop.daemon_status.DaemonProcessInventory.collect", return_value=inventory):
                with mock.patch("codex_refactor_loop.daemon_status.probe_daemon_singleton", return_value=singleton):
                    with mock.patch("codex_refactor_loop.restart.time.time", return_value=1001):
                        with mock.patch("codex_refactor_loop.daemon_status.pid_alive", side_effect=lambda candidate: candidate in {848, 857}):
                            report = daemon_status.collect(repo_root=self.tmp, skill_root=SCRIPT_DIR.parent)

        daemon = next(item for item in report.to_json()["daemons"] if item["name"] == "concurrency_monitor")
        self.assertEqual("stale", daemon["status"])
        self.assertEqual("fresh", daemon["heartbeat_status"])
        self.assertEqual("overdue", daemon["progress_status"])
        self.assertEqual(701, daemon["progress_age_seconds"])
        self.assertEqual("progress-overdue:701s", daemon["stale_reason"])

    def test_failed_progress_is_stale_even_when_heartbeat_is_fresh(self) -> None:
        env = {"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}
        heartbeat = self.tmp / ".refactor-loop" / "heartbeats" / "comment-monitor.ts"
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        heartbeat.write_text("1000\n", encoding="utf-8")
        pid = self.tmp / ".refactor-loop" / "locks" / "comment-monitor.pid"
        pid.parent.mkdir(parents=True, exist_ok=True)
        pid.write_text("456\n", encoding="utf-8")
        progress = begin_tick(self.tmp, "comment-monitor", now=1000, pid=789)
        fail_tick(self.tmp, progress, now=1000, message="RuntimeError:boom")
        (self.tmp / ".refactor-loop" / "state" / "active-controller-status.json").write_text(
            json.dumps({"active_controller": "owner"}) + "\n",
            encoding="utf-8",
        )

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("codex_refactor_loop.daemon_status.DaemonProcessInventory.collect") as collect_inventory:
                collect_inventory.return_value = daemon_status.DaemonProcessInventory(())
                with mock.patch("codex_refactor_loop.restart.time.time", return_value=1001):
                    with mock.patch("codex_refactor_loop.daemon_status.pid_alive", return_value=True):
                        report = daemon_status.collect(repo_root=self.tmp, skill_root=SCRIPT_DIR.parent)

        daemon = next(item for item in report.to_json()["daemons"] if item["name"] == "comment-monitor")
        self.assertEqual("stale", daemon["status"])
        self.assertEqual("fresh", daemon["heartbeat_status"])
        self.assertEqual("failed", daemon["progress_status"])
        self.assertIn("progress-failed:RuntimeError:boom", daemon["stale_reason"])

    def test_orphan_child_lock_holder_is_stale_read_only_projection(self) -> None:
        env = {"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}
        ctx = LoopContext.load(
            repo_root=self.tmp,
            skill_root=SCRIPT_DIR.parent,
            read_only=True,
            env=env,
        )
        target = restart.daemon_target(
            ctx,
            "phase9_router_daemon",
            ("python3", "{skill_root}/scripts/consensus-rnd-cli", "phase9-router", "--daemon", "--interval", "120"),
        )
        heartbeat = self.tmp / ".refactor-loop" / "heartbeats" / "phase9_router_daemon.ts"
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        heartbeat.write_text("1000\n", encoding="utf-8")
        pid = self.tmp / ".refactor-loop" / "locks" / "phase9_router_daemon.pid"
        pid.parent.mkdir(parents=True, exist_ok=True)
        pid.write_text("999\n", encoding="utf-8")
        fingerprint = restart.DaemonLaunchFingerprint.current(
            ctx,
            "phase9_router_daemon",
            target.command,
        )
        fingerprint.write(self.tmp / ".refactor-loop" / "locks" / "phase9_router_daemon.fingerprint.json")
        lock_file = self.tmp / ".refactor-loop" / "phase9-router.lock"
        lock_file.write_text("pid=789\n", encoding="utf-8")
        (self.tmp / ".refactor-loop" / "state" / "active-controller-status.json").write_text(
            json.dumps({"active_controller": "owner"}) + "\n",
            encoding="utf-8",
        )
        inventory = restart.DaemonProcessInventory((restart.DaemonProcess(789, " ".join(target.command), 1),))

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("codex_refactor_loop.daemon_status.DaemonProcessInventory.collect", return_value=inventory):
                with mock.patch("codex_refactor_loop.restart.time.time", return_value=1001):
                    with mock.patch("codex_refactor_loop.daemon_status.pid_alive", side_effect=lambda candidate: candidate == 789):
                        report = daemon_status.collect(repo_root=self.tmp, skill_root=SCRIPT_DIR.parent)

        daemon = next(item for item in report.to_json()["daemons"] if item["name"] == "phase9_router_daemon")
        self.assertEqual("stale", daemon["status"])
        self.assertEqual("orphan-lock-holders:1", daemon["stale_reason"])
        self.assertEqual([789], daemon["managed_child_pids"])
        self.assertEqual([], daemon["canonical_child_pids"])
        self.assertEqual([789], daemon["orphan_child_pids"])
        self.assertEqual([789], daemon["bounded_lock_holder_pids"])
        self.assertNotIn("orphan_managed_child_pids", daemon)
        self.assertEqual("999\n", pid.read_text(encoding="utf-8"))
        self.assertEqual("pid=789\n", lock_file.read_text(encoding="utf-8"))

    def test_macos_wrapper_child_is_running_canonical_child_projection(self) -> None:
        env = {"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}
        ctx = LoopContext.load(
            repo_root=self.tmp,
            skill_root=SCRIPT_DIR.parent,
            read_only=True,
            env=env,
        )
        target = restart.daemon_target(
            ctx,
            "phase9_router_daemon",
            ("python3", "{skill_root}/scripts/consensus-rnd-cli", "phase9-router", "--daemon", "--interval", "120"),
        )
        heartbeat = self.tmp / ".refactor-loop" / "heartbeats" / "phase9_router_daemon.ts"
        heartbeat.parent.mkdir(parents=True, exist_ok=True)
        heartbeat.write_text("1000\n", encoding="utf-8")
        pid = self.tmp / ".refactor-loop" / "locks" / "phase9_router_daemon.pid"
        pid.parent.mkdir(parents=True, exist_ok=True)
        pid.write_text("848\n", encoding="utf-8")
        fingerprint = restart.DaemonLaunchFingerprint.current(
            ctx,
            "phase9_router_daemon",
            target.command,
        )
        fingerprint.write(self.tmp / ".refactor-loop" / "locks" / "phase9_router_daemon.fingerprint.json")
        progress = begin_tick(self.tmp, "phase9_router_daemon", now=1000, pid=857)
        complete_tick(self.tmp, progress, now=1000)
        wrapper_command = " ".join(
            (
                sys.executable,
                "-c",
                restart.WRAPPER_CODE,
                "phase9_router_daemon",
                str(ctx.repo_root),
                str(target.pid_file),
                str(target.died_file),
                *target.command,
            )
        )
        inventory = restart.DaemonProcessInventory(
            (
                restart.DaemonProcess(848, wrapper_command, 1),
                restart.DaemonProcess(857, "python3 /tmp/old/consensus-rnd-cli phase9-router --daemon", 848),
            )
        )
        singleton = DaemonSingletonProjection(target.singleton_lock_file, "held", 857, True, "lock-held")
        (self.tmp / ".refactor-loop" / "state" / "active-controller-status.json").write_text(
            json.dumps({"active_controller": "owner"}) + "\n",
            encoding="utf-8",
        )

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("codex_refactor_loop.daemon_status.DaemonProcessInventory.collect", return_value=inventory):
                with mock.patch("codex_refactor_loop.daemon_status.probe_daemon_singleton", return_value=singleton):
                    with mock.patch("codex_refactor_loop.restart.time.time", return_value=1001):
                        with mock.patch("codex_refactor_loop.daemon_status.pid_alive", side_effect=lambda candidate: candidate in {848, 857}):
                            report = daemon_status.collect(repo_root=self.tmp, skill_root=SCRIPT_DIR.parent)

        daemon = next(item for item in report.to_json()["daemons"] if item["name"] == "phase9_router_daemon")
        self.assertEqual("running", daemon["status"])
        self.assertEqual([857], daemon["managed_child_pids"])
        self.assertEqual([857], daemon["canonical_child_pids"])
        self.assertEqual([], daemon["orphan_child_pids"])
        self.assertEqual([], daemon["bounded_lock_holder_pids"])
        self.assertEqual("$REPO_ROOT/.refactor-loop/locks/phase9_router_daemon.singleton.lock", daemon["singleton_lock_path"])
        self.assertNotIn(str(self.tmp), json.dumps(daemon, sort_keys=True))
        self.assertEqual("held", daemon["singleton_lock_state"])
        self.assertEqual(857, daemon["singleton_lock_holder_pid"])
        self.assertTrue(daemon["singleton_lock_metadata_valid"])
        self.assertEqual("available", daemon["process_inventory_status"])
        self.assertEqual("", daemon["process_inventory_error"])
        self.assertNotIn("orphan_managed_child_pids", daemon)

    def test_inventory_collection_failure_is_stale_with_diagnostic_fields(self) -> None:
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
        inventory = restart.DaemonProcessInventory((), status="unavailable", error="ps denied")

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("codex_refactor_loop.daemon_status.DaemonProcessInventory.collect", return_value=inventory):
                with mock.patch("codex_refactor_loop.restart.time.time", return_value=1001):
                    with mock.patch("codex_refactor_loop.daemon_status.pid_alive", return_value=True):
                        report = daemon_status.collect(repo_root=self.tmp, skill_root=SCRIPT_DIR.parent)

        daemon = next(item for item in report.to_json()["daemons"] if item["name"] == "concurrency_monitor")
        self.assertEqual("stale", daemon["status"])
        self.assertEqual("process-inventory-unavailable:ps denied", daemon["stale_reason"])
        self.assertEqual("unavailable", daemon["process_inventory_status"])
        self.assertEqual("ps denied", daemon["process_inventory_error"])

    def test_held_malformed_singleton_lock_is_stale_fail_closed(self) -> None:
        env = {"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}
        ctx = LoopContext.load(
            repo_root=self.tmp,
            skill_root=SCRIPT_DIR.parent,
            read_only=True,
            env=env,
        )
        target = restart.daemon_target(
            ctx,
            "concurrency_monitor",
            ("python3", "{skill_root}/scripts/consensus-rnd-cli", "concurrency", "--daemon"),
        )
        target.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
        target.heartbeat_file.write_text("1000\n", encoding="utf-8")
        target.pid_file.parent.mkdir(parents=True, exist_ok=True)
        target.pid_file.write_text("848\n", encoding="utf-8")
        restart.DaemonLaunchFingerprint.current(ctx, "concurrency_monitor", target.command).write(target.fingerprint_file)
        progress = begin_tick(self.tmp, "concurrency_monitor", now=1000, pid=857)
        complete_tick(self.tmp, progress, now=1000)
        wrapper_command = " ".join(
            (
                sys.executable,
                "-c",
                restart.WRAPPER_CODE,
                "concurrency_monitor",
                str(ctx.repo_root),
                str(target.pid_file),
                str(target.died_file),
                *target.command,
            )
        )
        inventory = restart.DaemonProcessInventory(
            (
                restart.DaemonProcess(848, wrapper_command, 1),
                restart.DaemonProcess(857, "python3 /tmp/old/consensus-rnd-cli concurrency --daemon", 848),
            )
        )
        singleton = DaemonSingletonProjection(target.singleton_lock_file, "held-malformed", None, False, "lock-held-metadata-malformed")
        (self.tmp / ".refactor-loop" / "state" / "active-controller-status.json").write_text(
            json.dumps({"active_controller": "owner"}) + "\n",
            encoding="utf-8",
        )

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("codex_refactor_loop.daemon_status.DaemonProcessInventory.collect", return_value=inventory):
                with mock.patch("codex_refactor_loop.daemon_status.probe_daemon_singleton", return_value=singleton):
                    with mock.patch("codex_refactor_loop.restart.time.time", return_value=1001):
                        with mock.patch("codex_refactor_loop.daemon_status.pid_alive", side_effect=lambda candidate: candidate in {848, 857}):
                            report = daemon_status.collect(repo_root=self.tmp, skill_root=SCRIPT_DIR.parent)

        daemon = next(item for item in report.to_json()["daemons"] if item["name"] == "concurrency_monitor")
        self.assertEqual("stale", daemon["status"])
        self.assertEqual("lock-held-metadata-malformed", daemon["stale_reason"])
        self.assertEqual("held-malformed", daemon["singleton_lock_state"])
        self.assertIsNone(daemon["singleton_lock_holder_pid"])
        self.assertFalse(daemon["singleton_lock_metadata_valid"])

    def test_free_singleton_lock_metadata_is_diagnostic_status_only(self) -> None:
        env = {"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}
        ctx = LoopContext.load(
            repo_root=self.tmp,
            skill_root=SCRIPT_DIR.parent,
            read_only=True,
            env=env,
        )
        target = restart.daemon_target(
            ctx,
            "concurrency_monitor",
            ("python3", "{skill_root}/scripts/consensus-rnd-cli", "concurrency", "--daemon"),
        )
        target.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
        target.heartbeat_file.write_text("1000\n", encoding="utf-8")
        target.pid_file.parent.mkdir(parents=True, exist_ok=True)
        target.pid_file.write_text("848\n", encoding="utf-8")
        restart.DaemonLaunchFingerprint.current(ctx, "concurrency_monitor", target.command).write(target.fingerprint_file)
        progress = begin_tick(self.tmp, "concurrency_monitor", now=1000, pid=857)
        complete_tick(self.tmp, progress, now=1000)
        wrapper_command = " ".join(
            (
                sys.executable,
                "-c",
                restart.WRAPPER_CODE,
                "concurrency_monitor",
                str(ctx.repo_root),
                str(target.pid_file),
                str(target.died_file),
                *target.command,
            )
        )
        inventory = restart.DaemonProcessInventory((restart.DaemonProcess(848, wrapper_command, 1),))
        singleton = DaemonSingletonProjection(target.singleton_lock_file, "free", 857, True, "lock-free")
        (self.tmp / ".refactor-loop" / "state" / "active-controller-status.json").write_text(
            json.dumps({"active_controller": "owner"}) + "\n",
            encoding="utf-8",
        )

        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("codex_refactor_loop.daemon_status.DaemonProcessInventory.collect", return_value=inventory):
                with mock.patch("codex_refactor_loop.daemon_status.probe_daemon_singleton", return_value=singleton):
                    with mock.patch("codex_refactor_loop.restart.time.time", return_value=1001):
                        with mock.patch("codex_refactor_loop.daemon_status.pid_alive", side_effect=lambda candidate: candidate == 848):
                            report = daemon_status.collect(repo_root=self.tmp, skill_root=SCRIPT_DIR.parent)

        daemon = next(item for item in report.to_json()["daemons"] if item["name"] == "concurrency_monitor")
        self.assertEqual("stale", daemon["status"])
        self.assertEqual("stale", daemon["stale_reason"])
        self.assertEqual("free", daemon["singleton_lock_state"])
        self.assertEqual(857, daemon["singleton_lock_holder_pid"])
        self.assertTrue(daemon["singleton_lock_metadata_valid"])
        self.assertEqual("lock-free", daemon["singleton_lock_reason"])


if __name__ == "__main__":
    unittest.main()
