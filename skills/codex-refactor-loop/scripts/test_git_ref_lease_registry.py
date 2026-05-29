#!/usr/bin/env python3
"""Behavior tests for git-ref CAS lease coordination."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.coordination.leases import (
    GitRefLeaseRegistry,
    LeaseGate,
    LeaseToken,
    lease_projection_comment,
)


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False)


class GitRefLeaseRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lease-registry-test-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        run(["git", "init"], self.repo)
        run(["git", "config", "user.name", "test"], self.repo)
        run(["git", "config", "user.email", "test@example.com"], self.repo)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ctx(self, device: str, *, enabled: bool = True) -> LoopContext:
        env = {
            "MULTI_DEVICE_COORDINATION": "true" if enabled else "false",
            "AUTO_LOOP_DEVICE_ID": device,
            "AUTO_LOOP_LEASE_TTL_SECONDS": "900",
            "AUTO_LOOP_LEASE_RENEW_SECONDS": "300",
        }
        return LoopContext.load(repo_root=self.repo, env=env)

    def test_fresh_acquire_creates_authoritative_ref_and_projection_is_not_read(self) -> None:
        registry = GitRefLeaseRegistry(self.ctx("desk-a"))

        decision = registry.acquire("work-claim", "issue:193", reason="dispatch", target="#193")

        self.assertTrue(decision.acquired)
        record = registry.current("work-claim", "issue:193")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual("desk-a", record.owner_device_id)
        self.assertTrue(record.ref.startswith("refs/heads/auto-loop/leases/work-claim/"))
        projection = lease_projection_comment(record)
        self.assertIn("consensus-rnd:lease-projection", projection)
        self.assertIn("⟦AI:AUTO-LOOP⟧", projection)

    def test_concurrent_claim_one_winner_with_git_update_ref_old_value_cas(self) -> None:
        barrier = threading.Barrier(2)
        decisions = []

        def claimant(device: str):
            def before_cas(_ref: str, _old_sha: str) -> None:
                barrier.wait(timeout=5)

            registry = GitRefLeaseRegistry(self.ctx(device), before_cas_hook=before_cas)
            decisions.append(registry.acquire("work-claim", "same-work", reason="race", target="same"))

        threads = [threading.Thread(target=claimant, args=(device,)) for device in ("desk-a", "desk-b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(2, len(decisions))
        self.assertEqual(1, sum(1 for decision in decisions if decision.acquired))
        self.assertEqual(1, sum(1 for decision in decisions if decision.reason == "cas-lost"))

    def test_active_foreign_owner_rejects_and_expired_owner_can_be_taken_over(self) -> None:
        now = datetime(2026, 5, 29, 0, 0, 0, tzinfo=timezone.utc)
        first = GitRefLeaseRegistry(self.ctx("desk-a"), now_provider=lambda: now)
        self.assertTrue(first.acquire("singleton", "integration-sync:auto-refact-dev", ttl_seconds=60).acquired)

        second = GitRefLeaseRegistry(self.ctx("desk-b"), now_provider=lambda: now + timedelta(seconds=30))
        self.assertFalse(second.acquire("singleton", "integration-sync:auto-refact-dev", ttl_seconds=60).acquired)

        later = GitRefLeaseRegistry(self.ctx("desk-b"), now_provider=lambda: now + timedelta(seconds=200))
        takeover = later.acquire("singleton", "integration-sync:auto-refact-dev", ttl_seconds=60)

        self.assertTrue(takeover.acquired)
        self.assertEqual("expired-takeover", takeover.reason)
        current = later.current("singleton", "integration-sync:auto-refact-dev")
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual("desk-b", current.owner_device_id)

    def test_renew_and_release_require_matching_owner_token(self) -> None:
        owner = GitRefLeaseRegistry(self.ctx("desk-a"))
        claimed = owner.acquire("work-claim", "task", reason="test", target="task")
        self.assertTrue(claimed.acquired)
        assert claimed.token is not None

        other = GitRefLeaseRegistry(self.ctx("desk-b"))
        forged = LeaseToken("work-claim", "task", claimed.token.token, claimed.token.ref)
        self.assertFalse(other.renew(forged).acquired)
        self.assertFalse(other.release(forged).acquired)

        renewed = owner.renew(claimed.token)
        self.assertTrue(renewed.acquired)
        assert renewed.token is not None
        self.assertTrue(owner.release(renewed.token).acquired)
        self.assertIsNone(owner.current("work-claim", "task"))

    def test_invalid_scope_and_key_are_rejected_before_ref_write(self) -> None:
        registry = GitRefLeaseRegistry(self.ctx("desk-a"))
        with self.assertRaisesRegex(ValueError, "unsupported lease scope"):
            registry.acquire("source-work", "main")
        with self.assertRaisesRegex(ValueError, "single-line"):
            registry.acquire("work-claim", "bad\nkey")

    def test_remote_mode_uses_force_with_lease_only_for_lease_namespace(self) -> None:
        calls: list[list[str]] = []

        def runner(cmd, input_value):
            del input_value
            calls.append(list(cmd))
            if "rev-parse" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "missing")
            if "hash-object" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "blob-sha\n", "")
            if "mktree" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "tree-sha\n", "")
            if "commit-tree" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "commit-sha\n", "")
            if "push" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        registry = GitRefLeaseRegistry(self.ctx("desk-a"), command_runner=runner, use_remote=True)
        decision = registry.acquire("work-claim", "remote-task", reason="test")

        self.assertTrue(decision.acquired)
        push = [cmd for cmd in calls if "push" in cmd][0]
        self.assertIn("--force-with-lease=", " ".join(push))
        self.assertIn("refs/heads/auto-loop/leases/work-claim/", " ".join(push))

    def test_single_device_mode_acquires_without_git_ref_write(self) -> None:
        registry = GitRefLeaseRegistry(self.ctx("desk-a", enabled=False))
        decision = registry.acquire("work-claim", "task")
        self.assertTrue(decision.acquired)
        self.assertEqual("multi-device-disabled", decision.reason)
        refs = run(["git", "for-each-ref", "refs/heads/auto-loop/leases"], self.repo)
        self.assertEqual("", refs.stdout.strip())


if __name__ == "__main__":
    unittest.main()
