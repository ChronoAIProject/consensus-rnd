#!/usr/bin/env python3
"""Behavior tests for actor-owned daemon heartbeat lease helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading as real_threading
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.heartbeat import DaemonHeartbeatLease
from codex_refactor_loop.daemon_singleton import probe as probe_singleton

REAL_CONDITION = real_threading.Condition
REAL_EVENT = real_threading.Event
REAL_THREAD = real_threading.Thread


class HeartbeatCoordinator:
    def __init__(self) -> None:
        self.condition = REAL_CONDITION()
        self.callback_entered = False
        self.beat_during_callback = False
        self.stop_requested = False

    def enter_callback(self) -> None:
        with self.condition:
            self.callback_entered = True
            self.condition.notify_all()

    def record_beat(self) -> None:
        with self.condition:
            if self.callback_entered and not self.stop_requested:
                self.beat_during_callback = True
            self.condition.notify_all()

    def wait_for_heartbeat_while_callback_is_pending(self) -> bool:
        with self.condition:
            return self.condition.wait_for(lambda: self.beat_during_callback, timeout=1.0)

    def request_stop(self) -> None:
        with self.condition:
            self.stop_requested = True
            self.condition.notify_all()


class ScriptedEvent:
    instances: list["ScriptedEvent"] = []
    coordinator: HeartbeatCoordinator | None = None

    def __init__(self) -> None:
        self.wait_timeouts: list[float] = []
        self.set_called = False
        ScriptedEvent.instances.append(self)

    def wait(self, timeout: float | None = None) -> bool:
        self.wait_timeouts.append(float(timeout or 0))
        if ScriptedEvent.coordinator is None:
            return True
        coordinator = ScriptedEvent.coordinator
        with coordinator.condition:
            if not coordinator.callback_entered:
                coordinator.condition.wait_for(lambda: coordinator.callback_entered or coordinator.stop_requested, timeout=1.0)
            if coordinator.stop_requested:
                return True
            if not coordinator.beat_during_callback:
                return False
            coordinator.condition.wait_for(lambda: coordinator.stop_requested, timeout=1.0)
            return True

    def set(self) -> None:
        self.set_called = True
        if ScriptedEvent.coordinator is not None:
            ScriptedEvent.coordinator.request_stop()


class InlineThread:
    instances: list["InlineThread"] = []

    def __init__(self, *, target, name: str, daemon: bool) -> None:
        self.target = target
        self.name = name
        self.daemon = daemon
        self.started = False
        with mock.patch("threading.Event", REAL_EVENT):
            self.thread = REAL_THREAD(target=self.target, name=name, daemon=daemon)
        self.join_timeouts: list[float] = []
        InlineThread.instances.append(self)

    def start(self) -> None:
        self.started = True
        self.thread.start()

    def join(self, timeout: float | None = None) -> None:
        self.join_timeouts.append(float(timeout or 0))
        self.thread.join(timeout=timeout)


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

    def test_run_with_lease_periodically_renews_heartbeat_during_callback(self) -> None:
        ScriptedEvent.instances = []
        ScriptedEvent.coordinator = HeartbeatCoordinator()
        InlineThread.instances = []
        times = iter([2000, 2001])
        lease = DaemonHeartbeatLease(
            "python-daemon",
            self.repo,
            heartbeat_interval=7,
            clock=lambda: next(times),
        )
        expected = [object()]
        original_beat = lease.beat
        original_replace = os.replace
        replace_sources: list[Path] = []

        def beat() -> None:
            original_beat()
            if ScriptedEvent.coordinator is not None:
                ScriptedEvent.coordinator.record_beat()

        lease.beat = beat

        def record_replace(src: Path | str, dst: Path | str) -> None:
            replace_sources.append(Path(src))
            original_replace(src, dst)

        with mock.patch("codex_refactor_loop.heartbeat.threading.Event", ScriptedEvent), mock.patch(
            "codex_refactor_loop.heartbeat.threading.Thread",
            InlineThread,
        ), mock.patch("codex_refactor_loop.heartbeat.os.replace", side_effect=record_replace):

            def callback():
                assert ScriptedEvent.coordinator is not None
                ScriptedEvent.coordinator.enter_callback()
                self.assertTrue(ScriptedEvent.coordinator.wait_for_heartbeat_while_callback_is_pending())
                lease.beat()
                return expected

            result = lease.run_with_lease(callback)

        self.assertIs(result, expected)
        self.assertTrue(ScriptedEvent.coordinator.beat_during_callback)
        self.assertGreaterEqual(ScriptedEvent.instances[0].wait_timeouts.count(7.0), 1)
        self.assertTrue(ScriptedEvent.instances[0].set_called)
        self.assertTrue(InlineThread.instances[0].started)
        self.assertTrue(InlineThread.instances[0].daemon)
        self.assertEqual(InlineThread.instances[0].name, "python-daemon-heartbeat-renewer")
        self.assertEqual(InlineThread.instances[0].join_timeouts, [1.0])
        self.assertEqual(2, len(replace_sources))
        self.assertEqual(2, len(set(replace_sources)))
        self.assertTrue(all(path.name.startswith(".python-daemon.ts.tmp.") for path in replace_sources))
        self.assertIn((self.repo / ".refactor-loop" / "heartbeats" / "python-daemon.ts").read_text().strip(), {"2000", "2001"})
        self.assertEqual([], list((self.repo / ".refactor-loop" / "heartbeats").glob("*.tmp.*")))
        ScriptedEvent.coordinator = None

    def test_run_with_lease_propagates_exception_and_stops_renewer(self) -> None:
        ScriptedEvent.instances = []
        ScriptedEvent.coordinator = HeartbeatCoordinator()
        InlineThread.instances = []
        lease = DaemonHeartbeatLease("python-daemon", self.repo, heartbeat_interval=7, clock=lambda: 2000)
        original_beat = lease.beat

        def beat() -> None:
            original_beat()
            if ScriptedEvent.coordinator is not None:
                ScriptedEvent.coordinator.record_beat()

        lease.beat = beat

        with mock.patch("codex_refactor_loop.heartbeat.threading.Event", ScriptedEvent), mock.patch(
            "codex_refactor_loop.heartbeat.threading.Thread",
            InlineThread,
        ):

            def callback() -> None:
                assert ScriptedEvent.coordinator is not None
                ScriptedEvent.coordinator.enter_callback()
                self.assertTrue(ScriptedEvent.coordinator.wait_for_heartbeat_while_callback_is_pending())
                raise RuntimeError("boom")

            with self.assertRaisesRegex(RuntimeError, "boom"):
                lease.run_with_lease(callback)

        self.assertTrue(ScriptedEvent.coordinator.beat_during_callback)
        self.assertTrue(ScriptedEvent.instances[0].set_called)
        self.assertTrue(InlineThread.instances[0].started)
        self.assertEqual(InlineThread.instances[0].join_timeouts, [1.0])
        ScriptedEvent.coordinator = None

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
