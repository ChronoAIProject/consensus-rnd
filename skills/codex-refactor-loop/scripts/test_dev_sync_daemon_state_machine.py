#!/usr/bin/env python3
"""Behavior and source-regression tests for IntegrationSyncDaemon."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
os.environ.setdefault("REPO_ROOT", str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codex_refactor_loop.sync.dev import IntegrationSyncDaemon
from codex_refactor_loop.sync.requests import validate_request_dict

SKILL_ROOT = REPO_ROOT / "skills" / "codex-refactor-loop"
DEV_SYNC = SKILL_ROOT / "scripts" / "codex_refactor_loop" / "sync" / "dev.py"
SYNC_APPLY = SKILL_ROOT / "scripts" / "codex_refactor_loop" / "sync" / "apply.py"
SYNC_REQUESTS = SKILL_ROOT / "scripts" / "codex_refactor_loop" / "sync" / "requests.py"
SKILL_MD = SKILL_ROOT / "SKILL.md"


class FakeGit:
    def __init__(
        self,
        *,
        ahead: int = 0,
        release_ahead: int = 0,
        behind: int = 0,
        remote_sha: str = "remote-sha",
        head_sha: str = "head-sha",
        review_base_sha: str = "review-base-sha",
        gh_rows: list[dict] | None = None,
        open_gh_rows: list[dict] | None = None,
        merge_base_adopted: bool = False,
        old_head_is_ancestor: bool = True,
        replay_count: int = 0,
        integration_ref_exists: bool = True,
    ) -> None:
        self.ahead = ahead
        self.release_ahead = release_ahead
        self.behind = behind
        self.remote_sha = remote_sha
        self.head_sha = head_sha
        self.review_base_sha = review_base_sha
        self.gh_rows = gh_rows or []
        self.open_gh_rows = open_gh_rows or []
        self.merge_base_adopted = merge_base_adopted
        self.old_head_is_ancestor = old_head_is_ancestor
        self.replay_count = replay_count
        self.integration_ref_exists = integration_ref_exists
        self.commands: list[list[str]] = []

    def __call__(self, cmd: list[str], cwd: Path | None = None, check: bool = False) -> subprocess.CompletedProcess:
        self.commands.append(cmd)
        stdout = ""
        returncode = 0
        if cmd[:5] == ["git", "ls-remote", "--exit-code", "--heads", "origin"]:
            returncode = 0 if self.integration_ref_exists else 2
            stdout = f"{self.remote_sha}\trefs/heads/auto-refact-dev\n" if self.integration_ref_exists else ""
        elif cmd[:2] == ["git", "fetch"]:
            pass
        elif cmd[:3] == ["git", "rev-list", "--count"]:
            spec = cmd[3]
            if spec.startswith("origin/auto-refact-dev..HEAD"):
                stdout = f"{self.ahead}\n"
            elif spec.startswith("origin/dev..origin/auto-refact-dev"):
                stdout = f"{self.release_ahead}\n"
            elif spec.startswith("HEAD..origin/dev"):
                stdout = f"{self.behind}\n"
            elif spec.startswith("old-head..origin/auto-refact-dev"):
                stdout = f"{self.replay_count}\n"
            else:
                stdout = "0\n"
        elif cmd[:3] == ["git", "rev-parse", "HEAD"]:
            stdout = f"{self.head_sha}\n"
        elif cmd[:3] == ["git", "rev-parse", "origin/auto-refact-dev"]:
            stdout = f"{self.remote_sha}\n"
        elif cmd[:3] == ["git", "rev-parse", "origin/dev"]:
            stdout = f"{self.review_base_sha}\n"
        elif cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            returncode = 0 if (self.merge_base_adopted if cmd[3] == "origin/dev" else self.old_head_is_ancestor) else 1
        elif cmd[:3] == ["gh", "pr", "list"]:
            rows = self.open_gh_rows if "--state" in cmd and cmd[cmd.index("--state") + 1] == "open" else self.gh_rows
            stdout = json.dumps(rows)
        return subprocess.CompletedProcess(cmd, returncode, stdout, "")


class IntegrationSyncDaemonBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.worktree = self.repo / "wt"
        self.worktree.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def daemon(self, fake: FakeGit, **overrides) -> IntegrationSyncDaemon:
        return IntegrationSyncDaemon(
            worktree=self.worktree,
            main_repo=self.repo,
            integration="auto-refact-dev",
            review_base="dev",
            command_runner=fake,
            logger=lambda _msg: None,
            ensure_worktree_fn=lambda: True,
            merge_detector=overrides.get("merge_detector", lambda _cwd: False),
            dirty_detector=overrides.get("dirty_detector", lambda _cwd: False),
            resolver_in_flight=overrides.get("resolver_in_flight", lambda: False),
            resolver_dispatcher=overrides.get("resolver_dispatcher", lambda: None),
            release_rollup_min_commits=overrides.get("release_rollup_min_commits", 0),
            now_provider=lambda: datetime(2026, 5, 27, 0, 0, 0, tzinfo=timezone.utc),
        )

    def pending_events(self) -> list[str]:
        path = self.repo / ".refactor-loop" / ".controller-pending-events.log"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def request_jsons(self) -> list[dict]:
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted((self.repo / ".refactor-loop" / "runs").glob("integration-sync-request-*.json"))]

    def test_clean_local_ahead_emits_request_without_push_or_reset(self) -> None:
        fake = FakeGit(ahead=2)
        self.daemon(fake).tick()

        requests = self.request_jsons()
        self.assertEqual(requests[0]["kind"], "push-local-ahead")
        self.assertEqual(requests[0]["expected_remote_sha"], "remote-sha")
        self.assertIn("DEV_SYNC_REQUEST:.refactor-loop/runs/integration-sync-request-push_local_ahead", self.pending_events()[0])
        self.assertFalse(any(command[:2] == ["git", "push"] for command in fake.commands))
        self.assertFalse(any(command[:2] == ["git", "reset"] for command in fake.commands))

    def test_merged_rollup_adoption_emits_request_with_ancestry_facts(self) -> None:
        fake = FakeGit(
            gh_rows=[{"number": 45, "headRefName": "auto-refact-dev", "headRefOid": "old-head", "mergedAt": "2026-05-25T00:00:00Z"}],
            replay_count=2,
        )
        self.daemon(fake).tick()

        request = self.request_jsons()[0]
        self.assertEqual(request["kind"], "adopt-merged-rollup")
        self.assertEqual(request["old_rollup_head"], "old-head")
        self.assertEqual(request["old_rollup_ahead_count"], 2)
        self.assertEqual(request["lifecycle_owner"], "controller")
        self.assertFalse(request["lifecycle_authority"])

    def test_merged_throwaway_rollup_head_emits_adoption_request(self) -> None:
        fake = FakeGit(
            gh_rows=[{"number": 46, "headRefName": "rollup/old-head", "headRefOid": "old-head", "mergedAt": "2026-05-25T00:00:00Z"}],
            replay_count=0,
        )
        self.daemon(fake).tick()

        request = self.request_jsons()[0]
        self.assertEqual(request["kind"], "adopt-merged-rollup")
        self.assertEqual(request["pr_number"], 46)
        self.assertEqual(request["old_rollup_head"], "old-head")

    def test_forward_sync_review_base_emits_request_without_merge_or_push(self) -> None:
        fake = FakeGit(behind=3, merge_base_adopted=True)
        self.daemon(fake).tick()

        request = self.request_jsons()[0]
        self.assertEqual(request["kind"], "forward-sync-review-base")
        self.assertEqual(request["evidence"]["behind_count"], 3)
        self.assertFalse(any(command[:2] in (["git", "merge"], ["git", "push"]) for command in fake.commands))

    def test_merge_in_progress_dispatches_resolver_without_request_apply(self) -> None:
        fake = FakeGit()
        dispatched: list[bool] = []
        self.daemon(
            fake,
            merge_detector=lambda _cwd: True,
            resolver_in_flight=lambda: False,
            resolver_dispatcher=lambda: dispatched.append(True),
        ).tick()

        self.assertEqual(dispatched, [True])
        self.assertEqual(self.request_jsons(), [])

    def test_release_rollup_needed_remains_pending_event_only(self) -> None:
        fake = FakeGit(merge_base_adopted=True, release_ahead=3, remote_sha="integration-sha", review_base_sha="base-sha")
        self.daemon(fake, release_rollup_min_commits=1).tick()

        prefix = "DEV_SYNC_PENDING:release-rollup-needed:"
        self.assertTrue(self.pending_events()[0].startswith(prefix))
        event = json.loads(self.pending_events()[0][len(prefix):])
        self.assertEqual(event["integration_branch"], "auto-refact-dev")
        self.assertEqual(event["review_base_branch"], "dev")
        self.assertEqual(event["integration_sha"], "integration-sha")
        self.assertEqual(event["review_base_sha"], "base-sha")
        self.assertEqual(event["ahead_count"], 3)
        self.assertEqual(event["reason"], "integration-ahead-review-base-without-open-rollup-pr")
        self.assertEqual(self.request_jsons(), [])

    def test_missing_integration_branch_alerts_and_skips_sync_work(self) -> None:
        fake = FakeGit(integration_ref_exists=False)
        self.daemon(fake).tick()

        self.assertEqual(["DEV_SYNC_PENDING:missing-integration-branch:auto-refact-dev"], self.pending_events())
        self.assertEqual(["git", "ls-remote", "--exit-code", "--heads", "origin", "auto-refact-dev"], fake.commands[0])
        self.assertFalse(any(command[:2] == ["git", "fetch"] for command in fake.commands))


class IntegrationSyncRequestSchemaTests(unittest.TestCase):
    def valid_request(self) -> dict:
        return {
            "schema": "IntegrationSyncRequest",
            "kind": "push-local-ahead",
            "integration_branch": "auto-refact-dev",
            "review_base_branch": "dev",
            "worktree_head": "abcdef0",
            "expected_remote_sha": "1234567",
            "evidence": {"reason": "test"},
            "created_at": "2026-05-27T00:00:00Z",
            "lifecycle_owner": "controller",
            "lifecycle_authority": False,
        }

    def test_schema_rejects_command_envelope_fields(self) -> None:
        for field in ("argv", "shell", "command", "target_ref", "git_verb"):
            data = self.valid_request()
            data[field] = "git push"
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    validate_request_dict(data)

    def test_schema_requires_controller_no_lifecycle_authority(self) -> None:
        data = self.valid_request()
        data["lifecycle_authority"] = True
        with self.assertRaises(ValueError):
            validate_request_dict(data)
        data = self.valid_request()
        data["lifecycle_owner"] = "daemon"
        with self.assertRaises(ValueError):
            validate_request_dict(data)


class IntegrationSyncDaemonSourceRegressionTests(unittest.TestCase):
    def test_skill_phase6_names_detector_and_controller_apply_boundary(self) -> None:
        text = SKILL_MD.read_text(encoding="utf-8")
        self.assertIn("## Named runtime exception — integration sync daemon integration-sync controller boundary", text)
        self.assertIn("daemon-owned detect-and-emit plus controller-owned git apply", text)
        self.assertIn("integration sync request artifacts", text)

    def test_skill_has_no_active_controller_sync_procedure(self) -> None:
        text = SKILL_MD.read_text(encoding="utf-8")
        self.assertNotIn("### Sync procedure", text)
        self.assertNotIn("### Sync cadence", text)
        self.assertIn("integration sync request artifact", text)

    def test_dev_sync_daemon_has_no_git_lifecycle_mutation_tokens(self) -> None:
        src = DEV_SYNC.read_text(encoding="utf-8")
        for token in ("git push", "git merge", "git rebase", "git reset --hard", "--force-with-lease"):
            with self.subTest(token=token):
                self.assertNotIn(token, src)
        for token in ('["git", "push"', '["git", "merge"', '["git", "rebase"', '["git", "reset"'):
            with self.subTest(token=token):
                self.assertNotIn(token, src)

    def test_no_generic_controller_lifecycle_intent_or_command_envelope(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                DEV_SYNC,
                SYNC_REQUESTS,
                SYNC_APPLY,
            )
        )
        for forbidden in ("ControllerLifecycleIntent", "ControllerCommand", "ControllerOrchestrator", "generic event bus"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)

    def test_missing_ref_guard_is_literal_and_ls_remote_based(self) -> None:
        src = DEV_SYNC.read_text(encoding="utf-8")
        self.assertIn("remote_branch_exists", src)
        self.assertIn('"ls-remote", "--exit-code", "--heads", "origin"', src)
        self.assertIn('append_pending_event("missing-integration-branch", self.integration)', src)
        self.assertIn('head_name.startswith("rollup/")', src)


if __name__ == "__main__":
    unittest.main()
