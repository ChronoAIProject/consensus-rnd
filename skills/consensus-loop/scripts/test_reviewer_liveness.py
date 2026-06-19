#!/usr/bin/env python3
"""Behavior tests for reviewer liveness projection."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.reviewer_liveness import (  # noqa: E402
    ReviewerLivenessProjection,
    reviewer_liveness_projection,
)


class ReviewerLivenessProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="reviewer-liveness-test-"))
        (self.tmp / ".refactor-loop" / "prompts").mkdir(parents=True)
        (self.tmp / ".refactor-loop" / "logs").mkdir(parents=True)
        (self.tmp / ".refactor-loop" / "locks" / "spawn-tasks").mkdir(parents=True)
        self.live_head = "a" * 40
        self.other_head = "b" * 40

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_same_head_stale_log_with_live_holder_is_pending_not_redispatchable(self) -> None:
        self.write_review_prompt("architect", 1, self.live_head)
        log = self.write_review_log("architect", 1, self.live_head, "review still running\n")
        self.make_stale(log)
        self.write_spawn_lock("review-pr77-architect-r1", log, pid=1234)

        projection = reviewer_liveness_projection(
            self.tmp,
            pr_number=77,
            head_sha=self.live_head,
            role="architect",
            holder_alive=lambda pid: pid == 1234,
        )

        self.assertTrue(projection.pending)
        self.assertFalse(projection.redispatchable)
        self.assertEqual("spawn_holder_alive", projection.reason)
        self.assertEqual(1, projection.round_number)

    def test_same_head_stale_log_with_dead_holder_is_redispatchable(self) -> None:
        self.write_review_prompt("architect", 1, self.live_head)
        log = self.write_review_log("architect", 1, self.live_head, "review stopped before exit\n")
        self.make_stale(log)
        self.write_spawn_lock("review-pr77-architect-r1", log, pid=1234)

        projection = reviewer_liveness_projection(
            self.tmp,
            pr_number=77,
            head_sha=self.live_head,
            role="architect",
            holder_alive=lambda _pid: False,
        )

        self.assertFalse(projection.pending)
        self.assertTrue(projection.redispatchable)
        self.assertEqual("stale_without_live_holder", projection.reason)

    def test_terminal_failed_reviewer_is_redispatchable(self) -> None:
        self.write_review_prompt("tests", 1, self.live_head)
        self.write_review_log("tests", 1, self.live_head, "REVIEW_DONE:77:tests:reject\nEXIT=1\n")

        projection = reviewer_liveness_projection(
            self.tmp,
            pr_number=77,
            head_sha=self.live_head,
            role="tests",
            holder_alive=lambda _pid: False,
        )

        self.assertFalse(projection.pending)
        self.assertTrue(projection.redispatchable)
        self.assertTrue(projection.terminal)
        self.assertEqual("terminal_failed", projection.reason)

    def test_different_head_log_does_not_suppress_live_head_dispatch(self) -> None:
        self.write_review_prompt("quality", 1, self.other_head)
        log = self.write_review_log("quality", 1, self.other_head, "old-head review still running\n")
        self.write_spawn_lock("review-pr77-quality-r1", log, pid=1234)

        projection = reviewer_liveness_projection(
            self.tmp,
            pr_number=77,
            head_sha=self.live_head,
            role="quality",
            holder_alive=lambda pid: pid == 1234,
        )

        self.assertFalse(projection.pending)
        self.assertFalse(projection.redispatchable)
        self.assertEqual("no_same_head_attempt", projection.reason)

    def test_r1_same_role_live_reviewer_blocks_r2_intent(self) -> None:
        self.write_review_prompt("architect", 1, self.live_head)
        log = self.write_review_log("architect", 1, self.live_head, "silent but alive\n")
        self.make_stale(log)
        self.write_spawn_lock("review-pr77-architect-r1", log, pid=4321)

        projection = ReviewerLivenessProjection.for_next_dispatch(
            self.tmp,
            pr_number=77,
            head_sha=self.live_head,
            role="architect",
            next_round=2,
            holder_alive=lambda pid: pid == 4321,
        )

        self.assertTrue(projection.pending)
        self.assertFalse(projection.redispatchable)
        self.assertEqual(1, projection.round_number)
        self.assertEqual("spawn_holder_alive", projection.reason)

    def write_review_prompt(self, role: str, round_number: int, head_sha: str) -> Path:
        path = self.tmp / ".refactor-loop" / "prompts" / f"review-pr77-{role}-r{round_number}.md"
        path.write_text(f"head_sha: {head_sha}\n", encoding="utf-8")
        return path

    def write_review_log(self, role: str, round_number: int, head_sha: str, body: str) -> Path:
        path = self.tmp / ".refactor-loop" / "logs" / f"review-pr77-{role}-r{round_number}.log"
        path.write_text(f"head_sha: {head_sha}\n{body}", encoding="utf-8")
        return path

    def write_spawn_lock(self, task_id: str, log_path: Path, *, pid: int) -> Path:
        path = self.tmp / ".refactor-loop" / "locks" / "spawn-tasks" / f"{task_id}.lock"
        path.write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "log_path": str(log_path.resolve()),
                    "pid": pid,
                    "acquired_at": "2026-06-01T00:00:00Z",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    @staticmethod
    def make_stale(path: Path) -> None:
        old = time.time() - 600
        os.utime(path, (old, old))


if __name__ == "__main__":
    unittest.main()
