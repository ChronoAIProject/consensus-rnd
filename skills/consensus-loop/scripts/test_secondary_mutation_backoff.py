#!/usr/bin/env python3
"""Behavior tests for secondary mutation backoff state."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.secondary_mutation_backoff import (
    STATE_RELATIVE_PATH,
    is_secondary_content_creation_failure,
    record_content_creation_backoff,
)


class SecondaryMutationBackoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="secondary-mutation-backoff-"))
        (self.tmp / ".config" / "consensus-rnd").mkdir(parents=True)
        (self.tmp / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_classifier_matches_known_secondary_content_creation_failures(self) -> None:
        cases = (
            "You have exceeded a secondary rate limit",
            "You have been temporarily blocked from content creation",
        )
        for message in cases:
            with self.subTest(message=message):
                result = subprocess.CompletedProcess(["gh"], 1, stdout="", stderr=message)
                self.assertTrue(is_secondary_content_creation_failure(result))

    def test_non_secondary_or_success_does_not_write_state(self) -> None:
        self.assertFalse(
            record_content_creation_backoff(
                self.ctx,
                "unit",
                subprocess.CompletedProcess(["gh"], 0, stdout="ok", stderr="You have exceeded a secondary rate limit"),
                now=100,
            )
        )
        self.assertFalse(
            record_content_creation_backoff(
                self.ctx,
                "unit",
                subprocess.CompletedProcess(["gh"], 1, stdout="", stderr="not found"),
                now=100,
            )
        )
        self.assertFalse((self.tmp / STATE_RELATIVE_PATH).exists())

    def test_secondary_failure_writes_content_creation_backoff_state(self) -> None:
        result = subprocess.CompletedProcess(["gh"], 1, stdout="", stderr="temporarily blocked from content creation")

        self.assertTrue(record_content_creation_backoff(self.ctx, "unit-operation", result, now=100, backoff_seconds=30))

        state = json.loads((self.tmp / STATE_RELATIVE_PATH).read_text(encoding="utf-8"))
        entry = state["contentCreation"]
        self.assertEqual("unit-operation", entry["operation"])
        self.assertEqual("secondary-content-creation-limit", entry["reason"])
        self.assertEqual(130, entry["until_epoch"])
        self.assertTrue(entry["not_live_state_fact_source"])
        self.assertTrue(entry["not_host_production_ssot"])
        self.assertTrue(entry["no_lifecycle_authority"])


if __name__ == "__main__":
    unittest.main()
