#!/usr/bin/env python3
"""Behavior tests for packaged integration sync operation/executor modules."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.sync.executor import IntegrationSyncExecutor
from codex_refactor_loop.sync.operations import (
    IntegrationSyncOperation,
    IntegrationSyncOperationError,
    validate_operation_dict,
    write_operation_artifact,
)


SYNC_EXECUTOR = SCRIPT_DIR / "codex_refactor_loop" / "sync" / "executor.py"
SYNC_OPERATIONS = SCRIPT_DIR / "codex_refactor_loop" / "sync" / "operations.py"


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
        unresolved: bool = False,
        stale_merge_msg: bool = False,
    ) -> None:
        self.remote_sha = remote_sha
        self.head_sha = head_sha
        self.ahead_count = ahead_count
        self.replay_count = replay_count
        self.merge_in_progress = merge_in_progress
        self.dirty = dirty
        self.ff_fails = ff_fails
        self.ancestor_ok = ancestor_ok
        self.unresolved = unresolved
        self.stale_merge_msg = stale_merge_msg
        self.commands: list[list[str]] = []
        self.merge_head = Path("/tmp/no-merge-head")
        self.merge_msg = Path("/tmp/stale-merge-msg")

    def __call__(self, cmd: list[str], cwd: Path, check: bool = False) -> subprocess.CompletedProcess[str]:
        self.commands.append(cmd)
        if cmd[:2] == ["git", "fetch"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["git", "rev-parse", "origin/auto-refact-dev"]:
            return subprocess.CompletedProcess(cmd, 0, f"{self.remote_sha}\n", "")
        if cmd[:3] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(cmd, 0, f"{self.head_sha}\n", "")
        if cmd[:3] == ["git", "rev-parse", "--git-path"]:
            return subprocess.CompletedProcess(cmd, 0, f"{self.merge_head if cmd[3] == 'MERGE_HEAD' else self.merge_msg}\n", "")
        if cmd[:3] == ["git", "diff", "--quiet"]:
            return subprocess.CompletedProcess(cmd, 1 if self.dirty else 0, "", "")
        if cmd[:3] == ["git", "diff", "--cached"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:4] == ["git", "diff", "--name-only", "--diff-filter=U"]:
            return subprocess.CompletedProcess(cmd, 0, "conflict.txt\n" if self.unresolved else "", "")
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


class PackagedIntegrationSyncOperationTests(unittest.TestCase):
    def valid_operation(self) -> dict:
        return {
            "schema": "IntegrationSyncOperation",
            "kind": "push-local-ahead",
            "integration_branch": "auto-refact-dev",
            "review_base_branch": "dev",
            "worktree_head": "head-sha",
            "expected_remote_sha": "remote-sha",
            "evidence": {"reason": "test"},
            "created_at": "2026-05-27T00:00:00Z",
            "executor": "dev_sync_daemon",
            "authority": "integration-branch-git-allowlist",
        }

    def test_operation_schema_has_daemon_executor_not_controller_owner(self) -> None:
        operation = validate_operation_dict(self.valid_operation())
        self.assertEqual("IntegrationSyncOperation", operation.schema)
        self.assertEqual("dev_sync_daemon", operation.executor)
        self.assertEqual("integration-branch-git-allowlist", operation.authority)

        for field in ("lifecycle_owner", "lifecycle_authority"):
            data = self.valid_operation()
            data[field] = "controller"
            with self.subTest(field=field):
                with self.assertRaises(IntegrationSyncOperationError):
                    validate_operation_dict(data)

    def test_schema_rejects_command_envelope_fields(self) -> None:
        for field in ("argv", "args", "shell", "command", "commands", "cmd", "git", "git_verb", "target_ref"):
            data = self.valid_operation()
            data[field] = "git push"
            with self.subTest(field=field):
                with self.assertRaises(IntegrationSyncOperationError):
                    validate_operation_dict(data)

    def test_write_operation_artifact_uses_operation_path_and_json_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            path = write_operation_artifact(
                repo,
                IntegrationSyncOperation(
                    kind="forward-sync-review-base",
                    integration_branch="auto-refact-dev",
                    review_base_branch="dev",
                    worktree_head="head-sha",
                    expected_remote_sha="remote-sha",
                    evidence={"reason": "review-base-ahead-of-integration"},
                    created_at="2026-05-27T00:00:00Z",
                ),
            )

            self.assertRegex(path.as_posix(), r"\.refactor-loop/runs/integration-sync-operation-forward-sync-review-base-[0-9]+\.json$")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("IntegrationSyncOperation", payload["schema"])
            self.assertEqual("dev_sync_daemon", payload["executor"])
            self.assertEqual("integration-branch-git-allowlist", payload["authority"])
            self.assertNotIn("lifecycle_owner", payload)


class PackagedIntegrationSyncExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.worktree = self.repo / "wt"
        self.worktree.mkdir()
        self.executor = IntegrationSyncExecutor(record_stem="integration-sync-operation-test")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def operation(self, **updates) -> IntegrationSyncOperation:
        data = {
            "kind": "push-local-ahead",
            "integration_branch": "auto-refact-dev",
            "review_base_branch": "dev",
            "worktree_head": "head-sha",
            "expected_remote_sha": "remote-sha",
            "evidence": {"reason": "test"},
            "created_at": "2026-05-27T00:00:00Z",
        }
        data.update(updates)
        return IntegrationSyncOperation(**data)

    def record(self, status: str) -> Path:
        return self.repo / ".refactor-loop" / "runs" / "integration-sync-executions" / f"integration-sync-operation-test.{status}.json"

    def execute(self, operation: IntegrationSyncOperation, fake: FakeGit):
        if fake.merge_in_progress:
            fake.merge_head = self.worktree / ".git" / "MERGE_HEAD"
            fake.merge_head.parent.mkdir(parents=True, exist_ok=True)
            fake.merge_head.write_text("merge\n", encoding="utf-8")
        if fake.stale_merge_msg:
            fake.merge_msg = self.worktree / ".git" / "MERGE_MSG"
            fake.merge_msg.parent.mkdir(parents=True, exist_ok=True)
            fake.merge_msg.write_text("stale message\n", encoding="utf-8")
        return self.executor.execute(
            operation,
            repo=self.repo,
            worktree=self.worktree,
            env={"INTEGRATION_BRANCH": "auto-refact-dev", "REVIEW_BASE_BRANCH": "dev"},
            command_runner=fake,
        )

    def test_execute_kinds_preserve_daemon_git_allowlist_and_records(self) -> None:
        cases = [
            (
                self.operation(kind="push-local-ahead"),
                FakeGit(),
                [
                    ["git", "rev-list", "--count", "origin/auto-refact-dev..HEAD"],
                    ["git", "push", "origin", "HEAD:auto-refact-dev"],
                ],
            ),
            (
                self.operation(kind="reset-to-remote"),
                FakeGit(),
                [["git", "reset", "--hard", "origin/auto-refact-dev"]],
            ),
            (
                self.operation(kind="continue-resolved-merge"),
                FakeGit(merge_in_progress=True),
                [
                    ["git", "diff", "--name-only", "--diff-filter=U"],
                    ["git", "merge", "--continue"],
                    ["git", "push", "origin", "HEAD:auto-refact-dev"],
                ],
            ),
            (
                self.operation(kind="forward-sync-review-base"),
                FakeGit(),
                [
                    ["git", "merge", "--ff-only", "origin/dev"],
                    ["git", "push", "origin", "HEAD:auto-refact-dev"],
                ],
            ),
            (
                self.operation(kind="adopt-merged-rollup", old_rollup_head="old-head", old_rollup_ahead_count=1),
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

        for operation, fake, expected in cases:
            with self.subTest(kind=operation.kind):
                marker = self.record("applied")
                if marker.exists():
                    marker.unlink()

                result = self.execute(operation, fake)
                self.assertTrue(result.ok)
                applied = json.loads(marker.read_text(encoding="utf-8"))
                self.assertEqual("applied", applied["status"])
                self.assertEqual(operation.kind, applied["reason"])
                for command in expected:
                    self.assertIn(command, fake.commands)
                self.assertFalse((self.repo / ".refactor-loop" / ".controller-pending-events.log").exists())

    def test_forward_sync_review_base_falls_back_to_no_ff_merge_then_push(self) -> None:
        fake = FakeGit(ff_fails=True)

        result = self.execute(self.operation(kind="forward-sync-review-base"), fake)

        self.assertTrue(result.ok)
        self.assertIn(["git", "merge", "--ff-only", "origin/dev"], fake.commands)
        self.assertIn(
            [
                "git",
                "merge",
                "--no-ff",
                "-m",
                "Sync auto-refact-dev with dev (daemon apply)",
                "origin/dev",
            ],
            fake.commands,
        )
        self.assertIn(["git", "push", "origin", "HEAD:auto-refact-dev"], fake.commands)

    def test_adopt_merged_rollup_zero_replay_resets_to_review_base_and_force_with_lease_pushes(self) -> None:
        fake = FakeGit(replay_count=0)

        result = self.execute(self.operation(kind="adopt-merged-rollup", old_rollup_head="old-head", old_rollup_ahead_count=0), fake)

        self.assertTrue(result.ok)
        self.assertIn(["git", "reset", "--hard", "origin/dev"], fake.commands)
        self.assertNotIn(["git", "reset", "--hard", "origin/auto-refact-dev"], fake.commands)
        self.assertNotIn(["git", "rebase", "--rebase-merges", "--onto", "origin/dev", "old-head"], fake.commands)
        self.assertIn(
            ["git", "push", "--force-with-lease=refs/heads/auto-refact-dev:remote-sha", "origin", "HEAD:auto-refact-dev"],
            fake.commands,
        )

    def test_rechecks_live_state_and_rejects_stale_dirty_unresolved_or_invalid_operations(self) -> None:
        result = self.execute(self.operation(), FakeGit(remote_sha="new-remote"))
        self.assertFalse(result.ok)
        self.assertIn("stale expected_remote_sha", self.record("rejected").read_text(encoding="utf-8"))

        result = self.execute(self.operation(), FakeGit(dirty=True))
        self.assertFalse(result.ok)
        self.assertIn("dirty non-merge worktree", self.record("rejected").read_text(encoding="utf-8"))

        result = self.execute(self.operation(kind="continue-resolved-merge"), FakeGit(merge_in_progress=True, unresolved=True))
        self.assertFalse(result.ok)
        self.assertIn("merge has unresolved paths", self.record("rejected").read_text(encoding="utf-8"))

        result = self.execute(
            self.operation(kind="adopt-merged-rollup", old_rollup_head="old-head", old_rollup_ahead_count=1),
            FakeGit(ancestor_ok=False),
        )
        self.assertFalse(result.ok)
        self.assertIn("invalid rollup ancestry", self.record("rejected").read_text(encoding="utf-8"))

    def test_stale_merge_msg_without_merge_head_rejects_continue_resolved_merge(self) -> None:
        # Refactor (issue-264): Old: stale MERGE_MSG could be confused with a merge.
        # New: executor continue-resolved-merge remains guarded by MERGE_HEAD only.
        result = self.execute(self.operation(kind="continue-resolved-merge"), FakeGit(stale_merge_msg=True))

        self.assertFalse(result.ok)
        self.assertIn("no merge in progress", self.record("rejected").read_text(encoding="utf-8"))

    def test_rejects_already_executed_record(self) -> None:
        marker = self.record("applied")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("{}\n", encoding="utf-8")

        result = self.execute(self.operation(), FakeGit())
        self.assertFalse(result.ok)
        self.assertIn("already-executed", self.record("rejected").read_text(encoding="utf-8"))

    def test_expected_branches_ignore_legacy_aliases(self) -> None:
        self.assertEqual(
            ("auto-refact-dev", "dev"),
            self.executor._expected_branches({"INTEGRATION": "legacy-integration", "REVIEW_BASE": "legacy-review"}),
        )
        self.assertEqual(
            ("canonical-integration", "canonical-review"),
            self.executor._expected_branches(
                {
                    "INTEGRATION_BRANCH": "canonical-integration",
                    "REVIEW_BASE_BRANCH": "canonical-review",
                    "INTEGRATION": "legacy-integration",
                    "REVIEW_BASE": "legacy-review",
                }
            ),
        )


class PackagedIntegrationSyncSourceRegressionTests(unittest.TestCase):
    def test_modules_are_import_safe_without_repo_root(self) -> None:
        for module in ("codex_refactor_loop.sync.operations", "codex_refactor_loop.sync.executor"):
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

    def test_source_preserves_artifact_host_env_and_daemon_boundary(self) -> None:
        combined = SYNC_OPERATIONS.read_text(encoding="utf-8") + "\n" + SYNC_EXECUTOR.read_text(encoding="utf-8")
        for required in (
            "IntegrationSyncOperation",
            "dev_sync_daemon",
            "integration-branch-git-allowlist",
            ".refactor-loop",
            "integration-sync-operation-",
            "integration-sync-executions",
            "INTEGRATION_BRANCH",
            "REVIEW_BASE_BRANCH",
            "reset-to-remote",
            "--force-with-lease=refs/heads/",
        ):
            with self.subTest(required=required):
                self.assertIn(required, combined)

        for forbidden in (
            "IntegrationSyncRequest",
            "DEV_SYNC_REQUEST:",
            "lifecycle_owner",
            "lifecycle_authority",
            "apply-sync",
            "sync-request",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)

    def test_source_keeps_narrow_allowlist_and_forbids_generic_lifecycle_tokens(self) -> None:
        combined = SYNC_OPERATIONS.read_text(encoding="utf-8") + "\n" + SYNC_EXECUTOR.read_text(encoding="utf-8")
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
            "reset-to-remote",
        ):
            with self.subTest(required=required):
                self.assertIn(required, combined)

        for forbidden in (
            "ControllerLifecycleIntent",
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
            'get("INTEGRATION")',
            'get("REVIEW_BASE")',
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
