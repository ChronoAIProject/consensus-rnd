#!/usr/bin/env python3
"""Behavior tests for packaged integration sync request/apply modules."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.sync import apply as sync_apply
from codex_refactor_loop.sync.requests import (
    IntegrationSyncRequest,
    IntegrationSyncRequestError,
    validate_request_dict,
    write_request_artifact,
)


SYNC_APPLY = SCRIPT_DIR / "codex_refactor_loop" / "sync" / "apply.py"
SYNC_REQUESTS = SCRIPT_DIR / "codex_refactor_loop" / "sync" / "requests.py"


class FakeGit:
    def __init__(
        self,
        *,
        remote_sha: str = "remote-sha",
        head_sha: str = "head-sha",
        ahead_count: int = 1,
        replay_count: int = 1,
        merge_in_progress: bool = False,
        dirty: bool = False,
        ff_fails: bool = False,
        ancestor_ok: bool = True,
    ) -> None:
        self.remote_sha = remote_sha
        self.head_sha = head_sha
        self.ahead_count = ahead_count
        self.replay_count = replay_count
        self.merge_in_progress = merge_in_progress
        self.dirty = dirty
        self.ff_fails = ff_fails
        self.ancestor_ok = ancestor_ok
        self.commands: list[list[str]] = []
        self.merge_head = Path("/tmp/no-merge-head")

    def __call__(self, cmd: list[str], cwd: Path, check: bool = False) -> subprocess.CompletedProcess[str]:
        self.commands.append(cmd)
        if cmd[:2] == ["git", "fetch"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["git", "rev-parse", "origin/auto-refact-dev"]:
            return subprocess.CompletedProcess(cmd, 0, f"{self.remote_sha}\n", "")
        if cmd[:3] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(cmd, 0, f"{self.head_sha}\n", "")
        if cmd[:3] == ["git", "rev-parse", "--git-path"]:
            return subprocess.CompletedProcess(cmd, 0, f"{self.merge_head}\n", "")
        if cmd[:3] == ["git", "diff", "--quiet"]:
            return subprocess.CompletedProcess(cmd, 1 if self.dirty else 0, "", "")
        if cmd[:3] == ["git", "diff", "--cached"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["git", "rev-list", "--count"]:
            if cmd[3] == "origin/auto-refact-dev..HEAD":
                return subprocess.CompletedProcess(cmd, 0, f"{self.ahead_count}\n", "")
            if cmd[3] == "old-head..origin/auto-refact-dev":
                return subprocess.CompletedProcess(cmd, 0, f"{self.replay_count}\n", "")
        if cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            return subprocess.CompletedProcess(cmd, 0 if self.ancestor_ok else 1, "", "")
        if cmd[:3] == ["git", "merge", "--ff-only"] and self.ff_fails:
            return subprocess.CompletedProcess(cmd, 1, "", "not possible to fast-forward\n")
        return subprocess.CompletedProcess(cmd, 0, "", "")


class PackagedIntegrationSyncRequestTests(unittest.TestCase):
    def valid_request(self) -> dict:
        return {
            "schema": "IntegrationSyncRequest",
            "kind": "push-local-ahead",
            "integration_branch": "auto-refact-dev",
            "review_base_branch": "dev",
            "worktree_head": "head-sha",
            "expected_remote_sha": "remote-sha",
            "evidence": {"reason": "test"},
            "created_at": "2026-05-27T00:00:00Z",
            "lifecycle_owner": "controller",
            "lifecycle_authority": False,
        }

    def test_schema_accepts_existing_artifact_shape_and_rejects_command_envelope_fields(self) -> None:
        request = validate_request_dict(self.valid_request())
        self.assertEqual("IntegrationSyncRequest", request.schema)
        self.assertEqual("controller", request.lifecycle_owner)
        self.assertFalse(request.lifecycle_authority)

        for field in ("argv", "args", "shell", "command", "commands", "cmd", "git", "git_verb", "target_ref"):
            data = self.valid_request()
            data[field] = "git push"
            with self.subTest(field=field):
                with self.assertRaises(IntegrationSyncRequestError):
                    validate_request_dict(data)

    def test_schema_requires_controller_owner_and_no_lifecycle_authority(self) -> None:
        data = self.valid_request()
        data["lifecycle_owner"] = "daemon"
        with self.assertRaisesRegex(IntegrationSyncRequestError, "lifecycle_owner must be controller"):
            validate_request_dict(data)

        data = self.valid_request()
        data["lifecycle_authority"] = True
        with self.assertRaisesRegex(IntegrationSyncRequestError, "lifecycle_authority must be false"):
            validate_request_dict(data)

    def test_write_request_artifact_preserves_existing_path_and_json_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            path = write_request_artifact(
                repo,
                IntegrationSyncRequest(
                    kind="forward-sync-review-base",
                    integration_branch="auto-refact-dev",
                    review_base_branch="dev",
                    worktree_head="head-sha",
                    expected_remote_sha="remote-sha",
                    evidence={"reason": "review-base-ahead-of-integration"},
                    created_at="2026-05-27T00:00:00Z",
                ),
            )

            self.assertRegex(path.as_posix(), r"\.refactor-loop/runs/integration-sync-request-forward_sync_review_base-[0-9]+\.json$")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("IntegrationSyncRequest", payload["schema"])
            self.assertEqual("controller", payload["lifecycle_owner"])
            self.assertFalse(payload["lifecycle_authority"])


class PackagedIntegrationSyncApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.worktree = self.repo / "wt"
        self.worktree.mkdir()
        self.request_path = self.repo / ".refactor-loop" / "runs" / "integration-sync-request-test.json"
        self.request_path.parent.mkdir(parents=True)
        self.write_request({})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def valid_request(self) -> dict:
        return {
            "schema": "IntegrationSyncRequest",
            "kind": "push-local-ahead",
            "integration_branch": "auto-refact-dev",
            "review_base_branch": "dev",
            "worktree_head": "head-sha",
            "expected_remote_sha": "remote-sha",
            "evidence": {"reason": "test"},
            "created_at": "2026-05-27T00:00:00Z",
            "lifecycle_owner": "controller",
            "lifecycle_authority": False,
        }

    def write_request(self, updates: dict) -> None:
        data = self.valid_request()
        data.update(updates)
        self.request_path.write_text(json.dumps(data) + "\n", encoding="utf-8")

    def applied_record(self) -> Path:
        return self.repo / ".refactor-loop" / "runs" / "integration-sync-applied" / f"{self.request_path.stem}.applied.json"

    def rejected_record(self) -> Path:
        return self.repo / ".refactor-loop" / "runs" / "integration-sync-applied" / f"{self.request_path.stem}.rejected.json"

    def apply(self, fake: FakeGit) -> int:
        if fake.merge_in_progress:
            fake.merge_head = self.worktree / ".git" / "MERGE_HEAD"
            fake.merge_head.parent.mkdir(parents=True, exist_ok=True)
            fake.merge_head.write_text("merge\n", encoding="utf-8")
        return sync_apply.apply_request(
            self.request_path,
            repo=self.repo,
            worktree=self.worktree,
            env={"INTEGRATION_BRANCH": "auto-refact-dev", "REVIEW_BASE_BRANCH": "dev"},
            command_runner=fake,
        )

    def test_apply_kinds_preserve_controller_owned_git_allowlist_and_records(self) -> None:
        cases = [
            (
                "push-local-ahead",
                {},
                FakeGit(),
                [
                    ["git", "rev-list", "--count", "origin/auto-refact-dev..HEAD"],
                    ["git", "push", "origin", "HEAD:auto-refact-dev"],
                ],
            ),
            (
                "continue-resolved-merge",
                {},
                FakeGit(merge_in_progress=True),
                [
                    ["git", "merge", "--continue"],
                    ["git", "push", "origin", "HEAD:auto-refact-dev"],
                ],
            ),
            (
                "forward-sync-review-base",
                {},
                FakeGit(),
                [
                    ["git", "merge", "--ff-only", "origin/dev"],
                    ["git", "push", "origin", "HEAD:auto-refact-dev"],
                ],
            ),
            (
                "adopt-merged-rollup",
                {"old_rollup_head": "old-head", "old_rollup_ahead_count": 1},
                FakeGit(replay_count=1),
                [
                    ["git", "merge-base", "--is-ancestor", "old-head", "origin/auto-refact-dev"],
                    ["git", "rev-list", "--count", "old-head..origin/auto-refact-dev"],
                    ["git", "reset", "--hard", "origin/auto-refact-dev"],
                    ["git", "rebase", "--rebase-merges", "--onto", "origin/dev", "old-head"],
                    ["git", "push", "--force-with-lease=refs/heads/auto-refact-dev:remote-sha", "origin", "HEAD:auto-refact-dev"],
                ],
            ),
        ]

        for kind, updates, fake, expected in cases:
            with self.subTest(kind=kind):
                self.write_request({"kind": kind, **updates})
                marker = self.applied_record()
                if marker.exists():
                    marker.unlink()

                self.assertEqual(0, self.apply(fake))
                applied = json.loads(marker.read_text(encoding="utf-8"))
                self.assertEqual("applied", applied["status"])
                self.assertEqual(kind, applied["reason"])
                for command in expected:
                    self.assertIn(command, fake.commands)
                self.assertFalse((self.repo / ".refactor-loop" / ".controller-pending-events.log").exists())

    def test_forward_sync_review_base_falls_back_to_no_ff_merge_then_push(self) -> None:
        self.write_request({"kind": "forward-sync-review-base"})
        fake = FakeGit(ff_fails=True)

        self.assertEqual(0, self.apply(fake))

        self.assertIn(["git", "merge", "--ff-only", "origin/dev"], fake.commands)
        self.assertIn(
            [
                "git",
                "merge",
                "--no-ff",
                "-m",
                "Sync auto-refact-dev with dev (controller apply)",
                "origin/dev",
            ],
            fake.commands,
        )
        self.assertIn(["git", "push", "origin", "HEAD:auto-refact-dev"], fake.commands)

    def test_adopt_merged_rollup_zero_replay_resets_to_review_base_and_force_with_lease_pushes(self) -> None:
        self.write_request({"kind": "adopt-merged-rollup", "old_rollup_head": "old-head", "old_rollup_ahead_count": 0})
        fake = FakeGit(replay_count=0)

        self.assertEqual(0, self.apply(fake))

        self.assertIn(["git", "reset", "--hard", "origin/dev"], fake.commands)
        self.assertNotIn(["git", "reset", "--hard", "origin/auto-refact-dev"], fake.commands)
        self.assertNotIn(["git", "rebase", "--rebase-merges", "--onto", "origin/dev", "old-head"], fake.commands)
        self.assertIn(
            ["git", "push", "--force-with-lease=refs/heads/auto-refact-dev:remote-sha", "origin", "HEAD:auto-refact-dev"],
            fake.commands,
        )

    def test_apply_rechecks_live_state_and_rejects_stale_dirty_or_invalid_requests(self) -> None:
        stale_remote = FakeGit(remote_sha="new-remote")
        self.assertEqual(2, self.apply(stale_remote))
        self.assertIn("stale expected_remote_sha", self.rejected_record().read_text(encoding="utf-8"))

        self.write_request({})
        dirty = FakeGit(dirty=True)
        self.assertEqual(2, self.apply(dirty))
        self.assertIn("dirty non-merge worktree", self.rejected_record().read_text(encoding="utf-8"))

        self.write_request({"kind": "adopt-merged-rollup", "old_rollup_head": "old-head", "old_rollup_ahead_count": 1})
        bad_ancestor = FakeGit(ancestor_ok=False)
        self.assertEqual(2, self.apply(bad_ancestor))
        self.assertIn("invalid rollup ancestry", self.rejected_record().read_text(encoding="utf-8"))

    def test_apply_rejects_already_applied_marker_and_request_flag(self) -> None:
        marker = self.applied_record()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{}\n", encoding="utf-8")

        self.assertEqual(2, self.apply(FakeGit()))
        self.assertIn("already-applied", self.rejected_record().read_text(encoding="utf-8"))

        marker.unlink()
        self.write_request({"applied": True})
        self.assertEqual(2, self.apply(FakeGit()))
        self.assertIn("already-applied", self.rejected_record().read_text(encoding="utf-8"))


class PackagedIntegrationSyncSourceRegressionTests(unittest.TestCase):
    def test_modules_are_import_safe_without_repo_root(self) -> None:
        for module in ("codex_refactor_loop.sync.requests", "codex_refactor_loop.sync.apply"):
            with self.subTest(module=module):
                result = subprocess.run(
                    [sys.executable, "-c", f"import {module}; print('ok')"],
                    cwd=SCRIPT_DIR,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual("ok", result.stdout.strip())

    def test_source_preserves_artifact_markers_host_env_and_controller_boundary(self) -> None:
        combined = SYNC_REQUESTS.read_text(encoding="utf-8") + "\n" + SYNC_APPLY.read_text(encoding="utf-8")
        for required in (
            "IntegrationSyncRequest",
            "DEV_SYNC_REQUEST:",
            "INTEGRATION_SYNC_APPLIED:",
            "INTEGRATION_SYNC_REJECTED:",
            ".refactor-loop",
            "integration-sync-request-",
            "integration-sync-applied",
            "REPO_ROOT",
            "WORKTREE",
            "INTEGRATION_BRANCH",
            "INTEGRATION",
            "REVIEW_BASE_BRANCH",
            "REVIEW_BASE",
            "lifecycle_owner",
            "lifecycle_authority",
            "controller",
            "false",
            "--force-with-lease=refs/heads/",
        ):
            with self.subTest(required=required):
                self.assertIn(required, combined)

    def test_source_keeps_narrow_allowlist_and_forbids_generic_lifecycle_tokens(self) -> None:
        combined = SYNC_REQUESTS.read_text(encoding="utf-8") + "\n" + SYNC_APPLY.read_text(encoding="utf-8")
        for required in (
            '["git", "fetch", "origin", "--quiet"]',
            '["git", "rev-list", "--count"',
            '["git", "merge-base", "--is-ancestor"',
            '["git", "reset", "--hard"',
            '"--rebase-merges"',
            '["git", "push"',
            "continue-resolved-merge",
            "forward-sync-review-base",
            "adopt-merged-rollup",
            "push-local-ahead",
        ):
            with self.subTest(required=required):
                self.assertIn(required, combined)

        for forbidden in (
            "ControllerLifecycleIntentV1",
            "ControllerCommand",
            "ControllerOrchestrator",
            "generic event bus",
            "gh pr create",
            "gh pr merge",
            "gh issue close",
            "gh label",
            "git commit",
            "git tag",
            "release publish",
            "REQUIRED_CHECKS",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
