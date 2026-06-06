#!/usr/bin/env python3
"""Behavior tests for the shared holistic status projection."""

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

from codex_refactor_loop import labels
from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.holistic_status import (
    REASON_BLOCKED,
    REASON_MAINTAINER_DECISION,
    REASON_NO_WORKER_CAPACITY,
    REASON_REPRESENTED_BY_PR,
    collect,
    render_markdown,
    render_peek_summary,
)


class HolisticStatusProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="holistic-status-test-"))
        for rel in (
            ".config/consensus-rnd",
            ".refactor-loop/state",
            ".refactor-loop/dispatch-queue/p0",
            ".refactor-loop/dispatch-queue/p1",
        ):
            (self.tmp / rel).mkdir(parents=True, exist_ok=True)
        (self.tmp / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\n'
            'export GH_REPO_SLUG="owner/repo"\n'
            'export CODEX_FLOOR="5"\n',
            encoding="utf-8",
        )
        (self.tmp / ".refactor-loop" / "state" / "statusline-snapshot.json").write_text(
            json.dumps({"actual": 1, "floor": 5, "open_pr_count": 1, "open_issue_count": 3}) + "\n",
            encoding="utf-8",
        )
        (self.tmp / ".refactor-loop" / "state" / "recent-pr-merges.json").write_text(
            json.dumps({"merges": [{"pr": 1}, {"pr": 2}]}) + "\n",
            encoding="utf-8",
        )
        (self.tmp / ".refactor-loop" / "state" / "patrol-inspector.json").write_text(
            json.dumps({"status": "ok", "findings": [{"fingerprint": "a"}], "published": [{"issue": 1}]}) + "\n",
            encoding="utf-8",
        )
        (self.tmp / ".refactor-loop" / "dispatch-queue" / "p0" / "a.dispatch.json").write_text("{}\n", encoding="utf-8")
        (self.tmp / ".refactor-loop" / "dispatch-queue" / "p1" / "b.dispatch.json").write_text("{}\n", encoding="utf-8")
        self.ctx = LoopContext.load(
            repo_root=self.tmp,
            env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"},
            read_only=True,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_card_sections_reasons_dependencies_and_queue_depth(self) -> None:
        items = [
            {
                "kind": "issue",
                "number": 10,
                "title": "implemented by child PR",
                "labels": (labels.MANAGED, labels.PHASE_IMPLEMENTING, labels.HUMAN_AUTO),
            },
            {
                "kind": "pr",
                "number": 20,
                "title": "child PR",
                "labels": (labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO),
                "body": "Closes #10\n",
            },
            {
                "kind": "issue",
                "number": 11,
                "title": "blocked issue",
                "labels": (labels.MANAGED, labels.PHASE_BLOCKED, labels.HUMAN_AUTO),
            },
            {
                "kind": "issue",
                "number": 12,
                "title": "needs maintainer",
                "labels": (labels.MANAGED, labels.PHASE_DESIGN_SOLVING, labels.HUMAN_MAINTAINER_DECISION),
            },
            {
                "kind": "issue",
                "number": 13,
                "title": "needs worker capacity",
                "labels": (labels.MANAGED, labels.PHASE_DESIGN_SOLVING, labels.HUMAN_AUTO),
            },
        ]
        monitor = mock.Mock(count_in_flight_codex=mock.Mock(return_value=1))

        with mock.patch("codex_refactor_loop.holistic_status.collect_daemon_status", side_effect=RuntimeError("offline")):
            projection = collect(self.ctx, monitor=monitor, github_items=items)

        reasons = {item.number: item.reason for item in projection.open_items}
        self.assertEqual(REASON_REPRESENTED_BY_PR, reasons[10])
        self.assertEqual(REASON_BLOCKED, reasons[11])
        self.assertEqual(REASON_MAINTAINER_DECISION, reasons[12])
        self.assertEqual(REASON_NO_WORKER_CAPACITY, reasons[13])
        self.assertEqual(1, projection.throughput.actual_workers)
        self.assertEqual(5, projection.throughput.floor)
        self.assertEqual(4, projection.throughput.target_workers)
        self.assertEqual(3, projection.throughput.deficit)
        self.assertEqual(2, projection.throughput.queued_dispatches)
        self.assertEqual(("issue #10",), next(item.dependencies for item in projection.open_items if item.number == 20))
        markdown = render_markdown(projection)
        for heading in ("### Throughput", "### Daemons", "### Patrol", "### Open Managed Items", "### Dependencies"):
            self.assertIn(heading, markdown)
        self.assertIn("PR #20 -> issue #10", markdown)
        self.assertIn("reason `no-worker-capacity`", markdown)
        self.assertIn("patrol-inspector: ok findings=1 published=1", markdown)
        summary = "\n".join(render_peek_summary(projection))
        self.assertIn("workers actual=1 target=4 floor=5 deficit=3 queue=2", summary)
        self.assertIn("patrol=1", summary)
        self.assertIn("issue #13 reason=no-worker-capacity", summary)

    def test_source_does_not_read_prompt_body_or_worker_prose(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "holistic_status.py").read_text(encoding="utf-8")

        for forbidden in ("prompts_dir", "prompt.read_text", "worker prose", "SOLVER_DONE", "META_JUDGE_DONE"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        for required in (
            "class HolisticStatusProjection",
            "def collect(",
            "def render_markdown(",
            "def render_peek_summary(",
            "ManagedWorkProjection",
            "ctx.paths.statusline_snapshot",
            "ctx.paths.dispatch_queue",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)


if __name__ == "__main__":
    unittest.main()
