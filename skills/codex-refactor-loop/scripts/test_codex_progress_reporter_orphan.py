#!/usr/bin/env python3
"""Regression tests for deleting obsolete progress comment orphan handling."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.monitors.progress import ProgressReporter


class ProgressReporterOrphanRemovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="codex-progress-orphan-test-"))
        (self.tmp / ".refactor-loop" / "logs").mkdir(parents=True)
        (self.tmp / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="fake/repo"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})
        self.state_file = self.tmp / ".refactor-loop" / "codex-progress-state.json"
        self.log_file = self.tmp / ".refactor-loop" / "logs" / "foo-test.log"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_finished_worker_log_does_not_delete_or_read_legacy_progress_comment(self) -> None:
        self.log_file.write_text(
            "some output line\nSOLVER_DONE:fake:propose:nothing\nEXIT=0\nDONE_AT=2026-01-01T00:00:00Z\n",
            encoding="utf-8",
        )
        self.state_file.write_text(
            json.dumps({"foo-test": {"target": "42", "kind": "issue", "comment_id": 12345, "last_md5": "abc", "finished": "false"}}) + "\n",
            encoding="utf-8",
        )

        reporter = ProgressReporter(self.ctx)
        with mock.patch("codex_refactor_loop.monitors.progress._run", side_effect=AssertionError("per-worker progress comment API is deleted")):
            reporter.post_or_update("foo-test", self.log_file)

        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(12345, state["foo-test"]["comment_id"])

    def test_source_has_no_orphan_delete_retry_api_path(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "monitors" / "progress.py").read_text(encoding="utf-8")
        for forbidden in ('"-X", "DELETE"', "issues/comments/{cid}", "already 404", "orphan"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
