#!/usr/bin/env python3
"""Regression tests for progress comment orphan delete retry behavior."""

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


class ProgressReporterOrphanRetryTests(unittest.TestCase):
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

    def seed_exit0_with_cid(self, cid: int = 12345, finished: str = "false") -> None:
        self.log_file.write_text(
            "some output line\nSOLVER_DONE:fake:propose:nothing\nEXIT=0\nDONE_AT=2026-01-01T00:00:00Z\n",
            encoding="utf-8",
        )
        self.state_file.write_text(
            json.dumps({"foo-test": {"target": "42", "kind": "issue", "comment_id": cid, "last_md5": "abc", "finished": finished}}) + "\n",
            encoding="utf-8",
        )

    def state(self) -> dict[str, object]:
        return json.loads(self.state_file.read_text(encoding="utf-8"))["foo-test"]

    def run_post_or_update(self, *, delete_exit: int, get_exit: int = 0) -> None:
        def fake_run(command, cwd, *, check):
            del cwd, check
            text = " ".join(command)
            if "-X DELETE" in text:
                return mock.Mock(returncode=delete_exit, stdout="", stderr="")
            if "issues/comments/12345" in text:
                return mock.Mock(returncode=get_exit, stdout="{}", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        reporter = ProgressReporter(self.ctx)
        with mock.patch("codex_refactor_loop.monitors.progress._run", side_effect=fake_run):
            reporter.post_or_update("foo-test", self.log_file)

    def test_delete_success_first_attempt(self) -> None:
        self.seed_exit0_with_cid(12345, "false")
        self.run_post_or_update(delete_exit=0)
        self.assertEqual("true", self.state()["finished"])
        self.assertEqual(0, self.state()["comment_id"])

    def test_delete_fail_keeps_state_for_retry(self) -> None:
        self.seed_exit0_with_cid(12345, "false")
        self.run_post_or_update(delete_exit=1, get_exit=0)
        self.assertEqual("false", self.state()["finished"])
        self.assertEqual(12345, self.state()["comment_id"])

    def test_delete_fail_but_404_marks_gone(self) -> None:
        self.seed_exit0_with_cid(12345, "false")
        self.run_post_or_update(delete_exit=1, get_exit=1)
        self.assertEqual("true", self.state()["finished"])
        self.assertEqual(0, self.state()["comment_id"])

    def test_orphan_state_retried_on_next_tick(self) -> None:
        self.seed_exit0_with_cid(12345, "true")
        self.run_post_or_update(delete_exit=0)
        self.assertEqual("true", self.state()["finished"])
        self.assertEqual(0, self.state()["comment_id"])


if __name__ == "__main__":
    unittest.main()
