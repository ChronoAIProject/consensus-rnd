#!/usr/bin/env python3
"""Behavior tests for local codex task spawn claims."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.task_spawn_claim import (
    TaskSpawnClaimError,
    TaskSpawnClaimMetadataError,
    TaskSpawnClaimStore,
    read_spawn_task_lock_metadata,
    safe_task_id_from_task,
)


class TaskSpawnClaimStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(tempfile.mkdtemp(prefix="task-spawn-claim-test-"))
        self.log = self.repo / ".refactor-loop" / "logs" / "implement-issue490.log"
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.store = TaskSpawnClaimStore(self.repo)

    def tearDown(self) -> None:
        shutil.rmtree(self.repo, ignore_errors=True)

    def _write_claim_lock(self, task_id: str, *, pid: int, log_path: Path | None = None) -> Path:
        safe_task_id = safe_task_id_from_task(task_id)
        lock_path = self.repo / ".refactor-loop" / "locks" / "spawn-tasks" / f"{safe_task_id}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        metadata = {
            "task_id": safe_task_id,
            "log_path": str((log_path or self.log).resolve()),
            "pid": pid,
            "acquired_at": "2026-06-06T00:00:00Z",
        }
        lock_path.write_text(json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8")
        return lock_path

    def _dead_pid(self) -> int:
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        os.waitpid(pid, 0)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        return pid

    def test_acquire_creates_exclusive_lock_metadata(self) -> None:
        claim = self.store.acquire("implement-issue490", log_path=self.log)

        self.assertTrue(claim.acquired)
        self.assertEqual("implement-issue490", claim.task_id)
        self.assertEqual(self.repo / ".refactor-loop" / "locks" / "spawn-tasks" / "implement-issue490.lock", claim.lock_path)
        metadata = json.loads(claim.lock_path.read_text(encoding="utf-8"))
        self.assertEqual("implement-issue490", metadata["task_id"])
        self.assertEqual(str(self.log.resolve()), metadata["log_path"])

    def test_live_existing_claim_returns_held_without_recycling(self) -> None:
        first = self.store.acquire("review-pr490-tests-r1", log_path=self.log)
        second = self.store.acquire("review-pr490-tests-r1", log_path=self.log)

        self.assertTrue(first.acquired)
        self.assertFalse(second.acquired)
        self.assertEqual(first.lock_path, second.lock_path)

    def test_dead_holder_with_missing_log_recycles_existing_claim(self) -> None:
        task_id = "implement-issue490"
        lock_path = self._write_claim_lock(task_id, pid=self._dead_pid())
        self.assertFalse(self.log.exists())

        claim = self.store.acquire(task_id, log_path=self.log)

        self.assertTrue(claim.acquired)
        self.assertEqual(lock_path, claim.lock_path)
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        self.assertEqual(os.getpid(), metadata["pid"])

    def test_live_holder_with_missing_log_stays_held(self) -> None:
        task_id = "implement-issue490"
        lock_path = self._write_claim_lock(task_id, pid=os.getpid())
        self.assertFalse(self.log.exists())

        claim = self.store.acquire(task_id, log_path=self.log)

        self.assertFalse(claim.acquired)
        self.assertEqual(lock_path, claim.lock_path)
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        self.assertEqual(os.getpid(), metadata["pid"])

    def test_completed_log_recycles_existing_claim_deterministically(self) -> None:
        first = self.store.acquire("fix-pr490-round-1", log_path=self.log)
        self.log.write_text("DONE\nEXIT=0\n", encoding="utf-8")

        second = self.store.acquire("fix-pr490-round-1", log_path=self.log)

        self.assertTrue(first.acquired)
        self.assertTrue(second.acquired)
        self.assertTrue(first.lock_path.is_file())
        metadata = json.loads(first.lock_path.read_text(encoding="utf-8"))
        self.assertEqual("fix-pr490-round-1", metadata["task_id"])

    def test_exit_marker_recycles_existing_claim_even_with_live_holder_pid(self) -> None:
        task_id = "fix-pr490-round-1"
        lock_path = self._write_claim_lock(task_id, pid=os.getpid())
        self.log.write_text("DONE\nEXIT=1\n", encoding="utf-8")

        claim = self.store.acquire(task_id, log_path=self.log)

        self.assertTrue(claim.acquired)
        self.assertEqual(lock_path, claim.lock_path)

    def test_deleted_completed_log_recycles_from_durable_artifact(self) -> None:
        task_id = "implement-issue490"
        first = self.store.acquire(task_id, log_path=self.log)
        runs = self.repo / ".refactor-loop" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        self.log.write_text("old worker output\nEXIT=0\n", encoding="utf-8")
        self.log.unlink()
        (runs / f"{self.log.stem}.md").write_text(
            "summary\n"
            "IMPLEMENT_DONE:issue490:ok\n",
            encoding="utf-8",
        )

        second = self.store.acquire(task_id, log_path=self.log)

        self.assertTrue(first.acquired)
        self.assertTrue(second.acquired)
        self.assertTrue(first.lock_path.is_file())

    def test_running_claim_without_terminal_log_or_artifact_marker_stays_held(self) -> None:
        task_id = "implement-issue490"
        first = self.store.acquire(task_id, log_path=self.log)
        runs = self.repo / ".refactor-loop" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        self.log.write_text("worker still running\n", encoding="utf-8")
        (runs / f"{self.log.stem}.md").write_text("summary without terminal marker\n", encoding="utf-8")

        second = self.store.acquire(task_id, log_path=self.log)

        self.assertTrue(first.acquired)
        self.assertFalse(second.acquired)
        self.assertEqual(first.lock_path, second.lock_path)

    def test_log_with_exit_zero_still_recycles_existing_claim(self) -> None:
        task_id = "implement-issue490"
        first = self.store.acquire(task_id, log_path=self.log)
        self.log.write_text("worker output\nEXIT=0\n", encoding="utf-8")

        second = self.store.acquire(task_id, log_path=self.log)

        self.assertTrue(first.acquired)
        self.assertTrue(second.acquired)
        self.assertTrue(first.lock_path.is_file())

    def test_unreadable_metadata_fails_closed(self) -> None:
        claim = self.store.acquire("phase9-issue490-r4-judge", log_path=self.log)
        claim.lock_path.write_text("{not json", encoding="utf-8")

        with self.assertRaises(TaskSpawnClaimError):
            self.store.acquire("phase9-issue490-r4-judge", log_path=self.log)

    def test_mismatched_metadata_fails_closed_before_recycle_or_acquire(self) -> None:
        claim = self.store.acquire("phase9-issue490-r4-judge", log_path=self.log)
        self.log.write_text("DONE\nEXIT=0\n", encoding="utf-8")
        mismatched_metadata = json.loads(claim.lock_path.read_text(encoding="utf-8"))
        mismatched_metadata["log_path"] = str((self.repo / ".refactor-loop" / "logs" / "other.log").resolve())
        mismatched_payload = json.dumps(mismatched_metadata, sort_keys=True)
        claim.lock_path.write_text(mismatched_payload, encoding="utf-8")

        with self.assertRaises(TaskSpawnClaimError):
            self.store.acquire("phase9-issue490-r4-judge", log_path=self.log)
        self.assertTrue(claim.lock_path.is_file())
        self.assertEqual(mismatched_payload, claim.lock_path.read_text(encoding="utf-8"))

    def test_shared_metadata_reader_requires_safe_task_id_and_matching_basename(self) -> None:
        lock_path = self._write_claim_lock("implement-issue490", pid=os.getpid())

        metadata = read_spawn_task_lock_metadata(lock_path)

        self.assertEqual("implement-issue490", metadata.task_id)
        self.assertEqual(self.log.resolve(), metadata.log_path)
        wrong_basename = lock_path.with_name("other.lock")
        wrong_basename.write_text(lock_path.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaises(TaskSpawnClaimMetadataError) as raised:
            read_spawn_task_lock_metadata(wrong_basename)
        self.assertEqual("basename_mismatch", raised.exception.reason)

    def test_safe_task_id_rejects_empty_or_unsafe_values(self) -> None:
        self.assertEqual("review-pr490-tests-r1", safe_task_id_from_task("review-pr490 tests r1"))
        self.assertEqual("review-pr490-tests-r1", safe_task_id_from_task("review-pr490-tests-r1"))
        for task_id in ("", "../../../x", "---", "x" * 161):
            with self.subTest(task_id=task_id):
                with self.assertRaises(TaskSpawnClaimError):
                    safe_task_id_from_task(task_id)


if __name__ == "__main__":
    unittest.main()
