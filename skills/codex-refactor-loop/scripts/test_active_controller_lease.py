#!/usr/bin/env python3
"""Behavior tests for the singleton active-controller lease."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import os
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.active_controller import ActiveControllerLeaseStore, DEFAULT_ACTIVE_CONTROLLER_REF
from codex_refactor_loop.context import LoopContext


def git(repo: Path, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )


class ActiveControllerLeaseStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="active-controller-test-"))
        self.remote = self.tmp / "remote.git"
        self.a = self.tmp / "a"
        self.b = self.tmp / "b"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)
        subprocess.run(["git", "clone", str(self.remote), str(self.a)], check=True, capture_output=True)
        subprocess.run(["git", "clone", str(self.remote), str(self.b)], check=True, capture_output=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def store(self, repo: Path, owner: str, ttl: int = 1800) -> ActiveControllerLeaseStore:
        return ActiveControllerLeaseStore(
            repo,
            owner_device=owner,
            lease_ref=DEFAULT_ACTIVE_CONTROLLER_REF,
            ttl_seconds=ttl,
            repo_slug="owner/repo",
        )

    def test_two_devices_acquire_only_one_success(self) -> None:
        first = self.store(self.a, "device-a").try_acquire("device-a", 1800)
        second = self.store(self.b, "device-b").try_acquire("device-b", 1800)

        self.assertTrue(first.allowed)
        self.assertFalse(second.allowed)
        self.assertEqual("not-owner", second.status)
        self.assertEqual("device-a", second.owner_device)

    def test_owner_renew_succeeds_and_non_owner_renew_fails(self) -> None:
        owner_store = self.store(self.a, "device-a")
        acquired = owner_store.try_acquire("device-a", 1800)

        renewed = owner_store.renew(acquired.lease_id)
        non_owner = self.store(self.b, "device-b").renew(acquired.lease_id)

        self.assertTrue(renewed.allowed)
        self.assertFalse(non_owner.allowed)
        self.assertEqual("not-owner", non_owner.status)

    def test_renew_missing_lease_ref_fails_closed(self) -> None:
        renewed = self.store(self.a, "device-a").renew("missing-lease-id")

        self.assertFalse(renewed.allowed)
        self.assertEqual("missing-lease", renewed.status)
        self.assertEqual("renew", renewed.action)
        self.assertEqual("", renewed.owner_device)

    def test_renew_expired_current_lease_fails_closed(self) -> None:
        from codex_refactor_loop.active_controller import ActiveControllerLease, _format_time, _now
        from datetime import timedelta

        now = _now()
        expired = ActiveControllerLease(
            owner_device="device-a",
            lease_id="expired-lease",
            acquired_at=_format_time(now - timedelta(seconds=120)),
            renewed_at=_format_time(now - timedelta(seconds=120)),
            expires_at=_format_time(now - timedelta(seconds=60)),
            repo="owner/repo",
            reason="single-active-controller",
            source_issue="191",
        )
        self.assertTrue(self.store(self.a, "device-a", ttl=1)._write_remote(expired, None))

        renewed = self.store(self.a, "device-a").renew(expired.lease_id)

        self.assertFalse(renewed.allowed)
        self.assertEqual("expired", renewed.status)
        self.assertEqual("device-a", renewed.owner_device)
        self.assertEqual("expired-lease", renewed.lease_id)
        current = self.store(self.b, "device-b").read()
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(expired, current)

    def test_renew_force_with_lease_conflict_fails_closed_and_preserves_remote(self) -> None:
        from codex_refactor_loop.active_controller import ActiveControllerLease, _format_time, _now
        from datetime import timedelta

        owner_store = self.store(self.a, "device-a")
        acquired = owner_store.try_acquire("device-a", 1800)
        self.assertTrue(acquired.allowed)
        current_sha = owner_store._read_remote().commit_sha
        self.assertIsNotNone(current_sha)
        assert current_sha is not None

        now = _now()
        conflicting_lease = ActiveControllerLease(
            owner_device="device-b",
            lease_id="remote-winner",
            acquired_at=_format_time(now),
            renewed_at=_format_time(now),
            expires_at=_format_time(now + timedelta(seconds=1800)),
            repo="owner/repo",
            reason="single-active-controller",
            source_issue="191",
        )
        pushed_conflict = False

        def racing_runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            nonlocal pushed_conflict
            if command[3] == "push" and not pushed_conflict:
                pushed_conflict = True
                self.assertTrue(self.store(self.b, "device-b")._write_remote(conflicting_lease, current_sha))
            return subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, check=False)

        renewed = ActiveControllerLeaseStore(
            self.a,
            owner_device="device-a",
            lease_ref=DEFAULT_ACTIVE_CONTROLLER_REF,
            ttl_seconds=1800,
            repo_slug="owner/repo",
            command_runner=racing_runner,
        ).renew(acquired.lease_id)

        self.assertTrue(pushed_conflict)
        self.assertFalse(renewed.allowed)
        self.assertEqual("cas-conflict", renewed.status)
        self.assertEqual("device-a", renewed.owner_device)
        self.assertEqual(acquired.lease_id, renewed.lease_id)
        current = self.store(self.b, "device-b").read()
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(conflicting_lease, current)

    def test_expired_lease_can_be_taken_over_by_other_device(self) -> None:
        from codex_refactor_loop.active_controller import ActiveControllerLease, _format_time, _now
        from datetime import timedelta

        now = _now()
        expired = ActiveControllerLease(
            owner_device="device-a",
            lease_id="expired-lease",
            acquired_at=_format_time(now - timedelta(seconds=120)),
            renewed_at=_format_time(now - timedelta(seconds=120)),
            expires_at=_format_time(now - timedelta(seconds=60)),
            repo="owner/repo",
            reason="single-active-controller",
            source_issue="191",
        )
        self.assertTrue(self.store(self.a, "device-a", ttl=1)._write_remote(expired, None))

        takeover = self.store(self.b, "device-b").try_acquire("device-b", 1800)

        self.assertTrue(takeover.allowed)
        self.assertEqual("device-b", takeover.owner_device)
        lease = self.store(self.b, "device-b").read()
        self.assertIsNotNone(lease)
        assert lease is not None
        self.assertEqual("device-b", lease.owner_device)

    def test_force_with_lease_conflict_fails_closed(self) -> None:
        store_a = self.store(self.a, "device-a")
        stale = store_a._read_remote()
        self.assertEqual("missing", stale.status)
        self.assertTrue(self.store(self.b, "device-b").try_acquire("device-b", 1800).allowed)

        from codex_refactor_loop.active_controller import ActiveControllerLease, _format_time, _now
        from datetime import timedelta

        now = _now()
        lease = ActiveControllerLease(
            owner_device="device-a",
            lease_id="stale-write",
            acquired_at=_format_time(now),
            renewed_at=_format_time(now),
            expires_at=_format_time(now + timedelta(seconds=1800)),
            repo="owner/repo",
            reason="single-active-controller",
            source_issue="191",
        )
        self.assertFalse(store_a._write_remote(lease, stale.commit_sha))
        current = self.store(self.a, "device-a").read()
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual("device-b", current.owner_device)

    def test_default_missing_device_id_is_single_device_local_owner_no_remote_ref(self) -> None:
        store = ActiveControllerLeaseStore(self.a, owner_device="")

        decision = store.require_owner("restart-daemons")

        self.assertTrue(decision.allowed)
        self.assertEqual("local-owner", decision.status)
        self.assertEqual("local-single-device", decision.owner_device)
        refs = git(self.a, "ls-remote", "--heads", "origin", DEFAULT_ACTIVE_CONTROLLER_REF)
        self.assertEqual("", refs.stdout.strip())

    def test_context_ignores_host_selected_active_controller_ref(self) -> None:
        repo = self.a
        (repo / ".refactor-loop").mkdir(parents=True, exist_ok=True)
        (repo / ".refactor-loop" / "host.env").write_text(
            "\n".join(
                [
                    f'export REPO_ROOT="{repo}"',
                    'export GH_REPO_SLUG="owner/repo"',
                    'export ACTIVE_CONTROLLER_DEVICE_ID="device-a"',
                    'export ACTIVE_CONTROLLER_REF="refs/heads/crnd/per-work-split"',
                    "",
                ]
            ),
            encoding="utf-8",
        )

        previous = os.environ.get("REPO_ROOT")
        os.environ["REPO_ROOT"] = str(repo)
        try:
            store = ActiveControllerLeaseStore.from_context(LoopContext.load(repo_root=repo, skill_root=self.tmp))
        finally:
            if previous is None:
                os.environ.pop("REPO_ROOT", None)
            else:
                os.environ["REPO_ROOT"] = previous

        self.assertEqual(DEFAULT_ACTIVE_CONTROLLER_REF, store.lease_ref)
        self.assertNotEqual("refs/heads/crnd/per-work-split", store.lease_ref)

    def test_json_blob_contains_required_fields(self) -> None:
        self.assertTrue(self.store(self.a, "device-a").try_acquire("device-a", 1800).allowed)
        git(self.b, "fetch", "origin", DEFAULT_ACTIVE_CONTROLLER_REF)
        data = json.loads(git(self.b, "show", "FETCH_HEAD:active-controller.json").stdout)

        self.assertEqual(
            sorted(data),
            ["acquired_at", "expires_at", "lease_id", "owner_device", "reason", "renewed_at", "repo", "source_issue"],
        )
        self.assertEqual("device-a", data["owner_device"])
        self.assertEqual("191", data["source_issue"])


if __name__ == "__main__":
    unittest.main()
