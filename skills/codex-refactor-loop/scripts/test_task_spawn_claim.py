#!/usr/bin/env python3
"""Behavior tests for local codex task spawn claims."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.task_spawn_claim import TaskSpawnClaimError, TaskSpawnClaimStore, safe_task_id_from_task


class TaskSpawnClaimStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(tempfile.mkdtemp(prefix="task-spawn-claim-test-"))
        self.log = self.repo / ".refactor-loop" / "logs" / "implement-issue490.log"
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.store = TaskSpawnClaimStore(self.repo)

    def tearDown(self) -> None:
        shutil.rmtree(self.repo, ignore_errors=True)

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

    def test_completed_log_recycles_existing_claim_deterministically(self) -> None:
        first = self.store.acquire("fix-pr490-round-1", log_path=self.log)
        self.log.write_text("DONE\nEXIT=0\n", encoding="utf-8")

        second = self.store.acquire("fix-pr490-round-1", log_path=self.log)

        self.assertTrue(first.acquired)
        self.assertTrue(second.acquired)
        self.assertTrue(first.lock_path.is_file())
        metadata = json.loads(first.lock_path.read_text(encoding="utf-8"))
        self.assertEqual("fix-pr490-round-1", metadata["task_id"])

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

    def test_safe_task_id_rejects_empty_or_unsafe_values(self) -> None:
        self.assertEqual("review-pr490-tests-r1", safe_task_id_from_task("review-pr490 tests r1"))
        self.assertEqual("review-pr490-tests-r1", safe_task_id_from_task("review-pr490-tests-r1"))
        for task_id in ("", "../../../x", "---", "x" * 161):
            with self.subTest(task_id=task_id):
                with self.assertRaises(TaskSpawnClaimError):
                    safe_task_id_from_task(task_id)


if __name__ == "__main__":
    unittest.main()
