#!/usr/bin/env python3
"""Behavior tests for actor-owned daemon heartbeat lease helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.heartbeat import DaemonHeartbeatLease
from codex_refactor_loop.daemon_progress import classify_progress, read_progress
from codex_refactor_loop.daemon_singleton import probe as probe_singleton


class DaemonHeartbeatLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="daemon-heartbeat-test-"))
        self.repo = self.tmp_root / "repo"
        (self.repo / ".refactor-loop" / "heartbeats").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_python_lease_atomic_beat_and_chunked_sleep(self) -> None:
        times = iter([1000, 1001, 1002, 1003])
        slept: list[float] = []
        lease = DaemonHeartbeatLease(
            "python-daemon",
            self.repo,
            heartbeat_interval=2,
            clock=lambda: next(times),
            sleeper=slept.append,
        )

        lease.beat()
        self.assertEqual("1000", (self.repo / ".refactor-loop" / "heartbeats" / "python-daemon.ts").read_text().strip())

        lease.sleep_with_lease(5)

        self.assertEqual([2.0, 2.0, 1.0], slept)
        self.assertEqual("1003", (self.repo / ".refactor-loop" / "heartbeats" / "python-daemon.ts").read_text().strip())
        self.assertEqual([], list((self.repo / ".refactor-loop" / "heartbeats").glob("*.tmp.*")))

    def test_run_tick_writes_progress_and_beats_only_after_success(self) -> None:
        times = iter([2000, 2001, 2002, 2003])
        lease = DaemonHeartbeatLease(
            "python-daemon",
            self.repo,
            heartbeat_interval=7,
            clock=lambda: next(times),
        )
        expected = [object()]

        def callback():
            self.assertFalse((self.repo / ".refactor-loop" / "heartbeats" / "python-daemon.ts").exists())
            progress = read_progress(self.repo, "python-daemon")
            self.assertIsNotNone(progress)
            assert progress is not None
            self.assertEqual("begin", progress.status)
            return expected

        result = lease.run_tick(callback)

        self.assertIs(result, expected)
        progress = read_progress(self.repo, "python-daemon")
        self.assertIsNotNone(progress)
        assert progress is not None
        self.assertEqual("complete", progress.status)
        self.assertTrue(progress.tick_id.endswith(f"-{os.getpid()}"))
        self.assertEqual("2000", (self.repo / ".refactor-loop" / "heartbeats" / "python-daemon.ts").read_text().strip())
        self.assertEqual("complete", classify_progress(self.repo, "python-daemon", max_age_seconds=30).state)
        self.assertEqual([], list((self.repo / ".refactor-loop" / "heartbeats").glob("*.tmp.*")))

    def test_run_tick_records_failure_without_heartbeat_success(self) -> None:
        lease = DaemonHeartbeatLease("python-daemon", self.repo, heartbeat_interval=7, clock=lambda: 2000)

        def callback() -> None:
            raise RuntimeError("boom")

        with self.assertRaisesRegex(RuntimeError, "boom"):
            lease.run_tick(callback)

        progress = read_progress(self.repo, "python-daemon")
        self.assertIsNotNone(progress)
        assert progress is not None
        self.assertEqual("fail", progress.status)
        self.assertIn("RuntimeError:boom", progress.message)
        self.assertFalse((self.repo / ".refactor-loop" / "heartbeats" / "python-daemon.ts").exists())

    def test_run_tick_keeps_heartbeat_fresh_during_long_callback(self) -> None:
        # Regression guard: a slow tick callback (e.g. a reconcile making
        # several gh/network calls) must NOT let the liveness heartbeat go
        # stale and get the live actor reaped. The renewer thread must beat
        # the heartbeat WHILE the callback is still running.
        lease = DaemonHeartbeatLease(
            "python-daemon",
            self.repo,
            heartbeat_interval=1,
            clock=lambda: int(time.time()),
        )
        hb = self.repo / ".refactor-loop" / "heartbeats" / "python-daemon.ts"
        beaten_during = {"seen": False}

        def callback() -> str:
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                if hb.exists():
                    beaten_during["seen"] = True
                    return "ok"
                time.sleep(0.05)
            return "timeout"

        result = lease.run_tick(callback)

        self.assertEqual("ok", result)
        self.assertTrue(
            beaten_during["seen"],
            "heartbeat must be renewed DURING a long callback, not only after it completes",
        )
        progress = read_progress(self.repo, "python-daemon")
        self.assertIsNotNone(progress)
        assert progress is not None
        self.assertEqual("complete", progress.status)
        self.assertEqual([], list((self.repo / ".refactor-loop" / "heartbeats").glob("*.tmp.*")))

    def test_lease_requires_repo_root_context_or_explicit_heartbeat_file(self) -> None:
        old_env = os.environ.copy()
        try:
            os.environ.clear()
            with self.assertRaisesRegex(Exception, "REPO_ROOT is unset"):
                DaemonHeartbeatLease("python-daemon")

            explicit = self.tmp_root / "explicit.ts"
            lease = DaemonHeartbeatLease("python-daemon", heartbeat_file=explicit, clock=lambda: 1000)
            lease.beat()
            self.assertEqual("1000", explicit.read_text(encoding="utf-8").strip())

            (self.repo / ".refactor-loop" / "host.env").write_text(f'export REPO_ROOT="{self.repo}"\n', encoding="utf-8")
            os.environ["REPO_ROOT"] = str(self.repo)
            lease = DaemonHeartbeatLease("python-daemon", clock=lambda: 1001)
            lease.beat()
            self.assertEqual("1001", (self.repo / ".refactor-loop" / "heartbeats" / "python-daemon.ts").read_text().strip())
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_singleton_lock_blocks_second_actor_until_first_exits(self) -> None:
        first = DaemonHeartbeatLease("python-daemon", self.repo, singleton=True, clock=lambda: 3000)
        second = DaemonHeartbeatLease("python-daemon", self.repo, singleton=True, clock=lambda: 3001)

        with first.daemon_lifetime():
            first.beat()
            projection = probe_singleton(self.repo, "python-daemon")
            self.assertEqual("held", projection.state)
            self.assertEqual(os.getpid(), projection.holder_pid)
            with self.assertRaisesRegex(RuntimeError, "daemon singleton lock held"):
                with second.daemon_lifetime():
                    pass

        with second.daemon_lifetime():
            second.beat()

        self.assertEqual("3001", (self.repo / ".refactor-loop" / "heartbeats" / "python-daemon.ts").read_text().strip())

    def test_singleton_mode_requires_lock_before_heartbeat_write(self) -> None:
        lease = DaemonHeartbeatLease("python-daemon", self.repo, singleton=True, clock=lambda: 3100)

        with self.assertRaisesRegex(RuntimeError, "requires held daemon singleton lock"):
            lease.beat()

        self.assertFalse((self.repo / ".refactor-loop" / "heartbeats" / "python-daemon.ts").exists())

    def test_held_malformed_singleton_metadata_is_fail_closed(self) -> None:
        lease = DaemonHeartbeatLease("python-daemon", self.repo, singleton=True, clock=lambda: 3200)

        with lease.daemon_lifetime():
            lock = self.repo / ".refactor-loop" / "locks" / "python-daemon.singleton.lock"
            lock.write_text("not-json\n", encoding="utf-8")
            projection = probe_singleton(self.repo, "python-daemon")

        self.assertEqual("held-malformed", projection.state)
        self.assertIsNone(projection.holder_pid)
        self.assertFalse(projection.metadata_valid)

    def test_probe_reports_missing_singleton_lock_file(self) -> None:
        projection = probe_singleton(self.repo, "python-daemon")

        self.assertEqual("missing", projection.state)
        self.assertEqual("lock-missing", projection.reason)
        self.assertIsNone(projection.holder_pid)
        self.assertFalse(projection.metadata_valid)

    def test_probe_reports_free_singleton_lock_metadata(self) -> None:
        lease = DaemonHeartbeatLease("python-daemon", self.repo, singleton=True, clock=lambda: 3300)

        with lease.daemon_lifetime():
            pass
        projection = probe_singleton(self.repo, "python-daemon")

        self.assertEqual("free", projection.state)
        self.assertEqual("lock-free", projection.reason)
        self.assertEqual(os.getpid(), projection.holder_pid)
        self.assertTrue(projection.metadata_valid)

    def test_probe_reports_filesystem_error(self) -> None:
        lock = self.repo / ".refactor-loop" / "locks" / "python-daemon.singleton.lock"
        lock.mkdir(parents=True)

        projection = probe_singleton(self.repo, "python-daemon")

        self.assertEqual("probe-error", projection.state)
        self.assertEqual("probe-error:IsADirectoryError", projection.reason)
        self.assertIsNone(projection.holder_pid)
        self.assertFalse(projection.metadata_valid)

    def test_heartbeat_source_has_no_cwd_repo_root_default(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "heartbeat.py").read_text(encoding="utf-8")
        self.assertIn("LoopContext.load", source)
        self.assertNotIn('os.environ.get("REPO_ROOT", ".")', source)

if __name__ == "__main__":
    unittest.main()
