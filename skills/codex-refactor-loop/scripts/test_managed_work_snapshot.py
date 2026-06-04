#!/usr/bin/env python3
"""Behavior tests for the managed work snapshot cache."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import labels as label_catalog
from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.managed_work_snapshot import (
    LOCK_RELATIVE_PATH,
    STATE_RELATIVE_PATH,
    ManagedWorkSnapshot,
)


class ManagedWorkSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="managed-work-snapshot-"))
        (self.tmp / ".config" / "consensus-rnd").mkdir(parents=True)
        (self.tmp / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_live_fetch_writes_fixed_state_and_lock_paths(self) -> None:
        calls: list[list[str]] = []

        def runner(command):
            calls.append(list(command))
            text = " ".join(command)
            if command[:2] == ["gh", "api"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps(
                        [
                            {
                                "number": 516,
                                "title": "snapshot",
                                "updated_at": "2026-06-05T00:00:00Z",
                                "labels": [
                                    {"name": label_catalog.MANAGED},
                                    {"name": label_catalog.PHASE_DESIGN_SOLVING},
                                    {"name": label_catalog.HUMAN_AUTO},
                                ],
                            },
                            {
                                "number": 12,
                                "title": "pr",
                                "updated_at": "2026-06-05T00:01:00Z",
                                "pull_request": {"url": "https://api.github.test/pr/12"},
                                "labels": [
                                    {"name": label_catalog.MANAGED},
                                    {"name": label_catalog.PHASE_REVIEWING},
                                    {"name": label_catalog.HUMAN_AUTO},
                                ],
                            },
                        ]
                    ),
                    "",
                )
            if "pr view 12" in text:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps({"body": "Closes #516", "headRefName": "refactor/iter516-issue-516", "headRefOid": "abc1234"}),
                    "",
                )
            return subprocess.CompletedProcess(command, 1, "", "unexpected")

        snapshot = ManagedWorkSnapshot(self.ctx, runner=runner, now=lambda: 1000)
        with mock.patch("codex_refactor_loop.managed_work_snapshot.graphql_headroom_ok", return_value=True):
            result = snapshot.load()

        self.assertTrue(result.loaded_ok)
        self.assertEqual("live", result.source)
        self.assertEqual((self.tmp / STATE_RELATIVE_PATH).resolve(), snapshot.state_path.resolve())
        self.assertEqual((self.tmp / LOCK_RELATIVE_PATH).resolve(), snapshot.lock_path.resolve())
        self.assertEqual([("issue", 516), ("PR", 12)], [(item["kind"], item["number"]) for item in result.items])
        pr = next(item for item in result.items if item["kind"] == "PR")
        self.assertEqual("refactor/iter516-issue-516", pr["head_ref"])
        self.assertEqual("Closes #516", pr["body"])
        written = json.loads(snapshot.state_path.read_text(encoding="utf-8"))
        self.assertTrue(written["not_live_state_fact_source"])
        self.assertTrue(written["not_host_production_ssot"])
        self.assertTrue(written["no_lifecycle_authority"])

    def test_fresh_cache_avoids_github_reads(self) -> None:
        path = self.tmp / STATE_RELATIVE_PATH
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "fetched_at_epoch": 1000,
                    "items": [{"kind": "issue", "number": 1, "labels": [label_catalog.MANAGED]}],
                }
            ),
            encoding="utf-8",
        )

        snapshot = ManagedWorkSnapshot(
            self.ctx,
            ttl_seconds=300,
            runner=lambda command: self.fail(f"unexpected GitHub read: {command}"),
            now=lambda: 1100,
        )
        result = snapshot.load()

        self.assertTrue(result.loaded_ok)
        self.assertEqual("cache:fresh", result.source)
        self.assertEqual(100, result.age_seconds)

    def test_low_headroom_uses_stale_cache_before_unavailable(self) -> None:
        path = self.tmp / STATE_RELATIVE_PATH
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "fetched_at_epoch": 1000,
                    "items": [{"kind": "issue", "number": 2, "labels": [label_catalog.MANAGED]}],
                }
            ),
            encoding="utf-8",
        )

        snapshot = ManagedWorkSnapshot(self.ctx, ttl_seconds=300, stale_max_seconds=900, now=lambda: 1600)
        with mock.patch("codex_refactor_loop.managed_work_snapshot.graphql_headroom_ok", return_value=False):
            result = snapshot.load()

        self.assertTrue(result.loaded_ok)
        self.assertEqual("cache:stale", result.source)

        too_stale = ManagedWorkSnapshot(self.ctx, ttl_seconds=300, stale_max_seconds=900, now=lambda: 2001)
        with mock.patch("codex_refactor_loop.managed_work_snapshot.graphql_headroom_ok", return_value=False):
            unavailable = too_stale.load()

        self.assertFalse(unavailable.loaded_ok)
        self.assertEqual("graphql-headroom-low", unavailable.reason)

    def test_source_does_not_create_forbidden_budget_or_open_work_env_names(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "managed_work_snapshot.py").read_text(encoding="utf-8")
        for forbidden in ("MANAGED_WORK_GRAPHQL_", "GITHUB_OPEN_ITEMS_", "OPEN_MANAGED_WORK_"):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("github-graphql-budget-backoff.json", source)


if __name__ == "__main__":
    unittest.main()
