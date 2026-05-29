#!/usr/bin/env python3
"""Behavior tests for codex_refactor_loop.sync.dev."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.sync.dev import IntegrationSyncDaemon, codex_resolve_in_flight, merge_in_progress


SYNC_DEV = SCRIPT_DIR / "codex_refactor_loop" / "sync" / "dev.py"


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

    def __call__(self, cmd: list[str], cwd: Path | None = None, check: bool = False) -> subprocess.CompletedProcess[str]:
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
        elif cmd[:3] == ["git", "-C", "wt"]:
            stdout = "/tmp/no-merge-state\n"
        elif cmd[:3] == ["ps", "-eo", "command="]:
            stdout = ""
        return subprocess.CompletedProcess(cmd, returncode, stdout, "")


class SyncDevBehaviorTests(unittest.TestCase):
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
            release_rollup_cooldown_seconds=overrides.get("release_rollup_cooldown_seconds", 21600),
            now_provider=lambda: datetime(2026, 5, 27, 0, 0, 0, tzinfo=timezone.utc),
        )

    def pending_events(self) -> list[str]:
        path = self.repo / ".refactor-loop" / ".controller-pending-events.log"
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def request_jsons(self) -> list[dict]:
        paths = sorted((self.repo / ".refactor-loop" / "runs").glob("integration-sync-request-*.json"))
        return [json.loads(path.read_text(encoding="utf-8")) for path in paths]

    def test_clean_local_ahead_emits_same_request_and_pending_marker(self) -> None:
        fake = FakeGit(ahead=2)
        self.daemon(fake).tick()

        request = self.request_jsons()[0]
        self.assertEqual("IntegrationSyncRequest", request["schema"])
        self.assertEqual("push-local-ahead", request["kind"])
        self.assertEqual("auto-refact-dev", request["integration_branch"])
        self.assertEqual("dev", request["review_base_branch"])
        self.assertEqual("head-sha", request["worktree_head"])
        self.assertEqual("remote-sha", request["expected_remote_sha"])
        self.assertEqual("controller", request["lifecycle_owner"])
        self.assertFalse(request["lifecycle_authority"])
        self.assertIn("DEV_SYNC_REQUEST:.refactor-loop/runs/integration-sync-request-push_local_ahead", self.pending_events()[0])
        self.assertFalse(any(command[:2] == ["git", "push"] for command in fake.commands))
        self.assertFalse(any(command[:2] == ["git", "reset"] for command in fake.commands))

    def test_merged_rollup_adoption_emits_same_artifact_shape(self) -> None:
        fake = FakeGit(
            gh_rows=[{"number": 45, "headRefName": "auto-refact-dev", "headRefOid": "old-head", "mergedAt": "2026-05-25T00:00:00Z"}],
            replay_count=2,
        )
        self.daemon(fake).tick()

        request = self.request_jsons()[0]
        self.assertEqual("adopt-merged-rollup", request["kind"])
        self.assertEqual("old-head", request["old_rollup_head"])
        self.assertEqual(2, request["old_rollup_ahead_count"])
        self.assertEqual(45, request["pr_number"])
        self.assertEqual({"reason": "merged-rollup-adoption", "replay_count": 2}, request["evidence"])

    def test_merged_throwaway_rollup_head_emits_adoption_request(self) -> None:
        fake = FakeGit(
            gh_rows=[{"number": 46, "headRefName": "rollup/old-head", "headRefOid": "old-head", "mergedAt": "2026-05-25T00:00:00Z"}],
            replay_count=0,
        )
        self.daemon(fake).tick()

        request = self.request_jsons()[0]
        self.assertEqual("adopt-merged-rollup", request["kind"])
        self.assertEqual(46, request["pr_number"])
        self.assertEqual("old-head", request["old_rollup_head"])

    def test_forward_sync_review_base_emits_same_artifact_shape(self) -> None:
        fake = FakeGit(behind=3, merge_base_adopted=True)
        self.daemon(fake).tick()

        request = self.request_jsons()[0]
        self.assertEqual("forward-sync-review-base", request["kind"])
        self.assertEqual({"behind_count": 3, "reason": "review-base-ahead-of-integration"}, request["evidence"])
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

        self.assertEqual([True], dispatched)
        self.assertEqual([], self.request_jsons())

    def test_release_rollup_needed_appends_existing_pending_event_format(self) -> None:
        fake = FakeGit(merge_base_adopted=True, release_ahead=3, remote_sha="integration-sha", review_base_sha="base-sha")
        self.daemon(fake, release_rollup_min_commits=1).tick()

        prefix = "DEV_SYNC_PENDING:release-rollup-needed:"
        self.assertTrue(self.pending_events()[0].startswith(prefix))
        event = json.loads(self.pending_events()[0][len(prefix):])
        self.assertEqual("auto-refact-dev", event["integration_branch"])
        self.assertEqual("dev", event["review_base_branch"])
        self.assertEqual("integration-sha", event["integration_sha"])
        self.assertEqual("base-sha", event["review_base_sha"])
        self.assertEqual(3, event["ahead_count"])
        self.assertEqual("2026-05-27T00:00:00Z", event["detected_at"])
        self.assertEqual("integration-ahead-review-base-without-open-rollup-pr", event["reason"])
        self.assertEqual([], self.request_jsons())

    def test_release_rollup_open_same_sha_throwaway_head_suppresses_duplicate_event(self) -> None:
        fake = FakeGit(
            merge_base_adopted=True,
            release_ahead=3,
            remote_sha="integration-sha",
            review_base_sha="base-sha",
            open_gh_rows=[{"number": 77, "headRefName": "rollup/integration-sha", "headRefOid": "integration-sha"}],
        )
        self.daemon(fake, release_rollup_min_commits=1).tick()

        self.assertEqual([], self.pending_events())
        self.assertEqual([], self.request_jsons())

    def test_release_rollup_open_stale_throwaway_head_does_not_suppress_event(self) -> None:
        fake = FakeGit(
            merge_base_adopted=True,
            release_ahead=3,
            remote_sha="integration-sha",
            review_base_sha="base-sha",
            open_gh_rows=[{"number": 77, "headRefName": "rollup/old-sha", "headRefOid": "old-sha"}],
        )
        self.daemon(fake, release_rollup_min_commits=1).tick()

        self.assertTrue(self.pending_events()[0].startswith("DEV_SYNC_PENDING:release-rollup-needed:"))
        self.assertEqual([], self.request_jsons())

    def test_missing_integration_branch_appends_alert_event_and_stops(self) -> None:
        fake = FakeGit(integration_ref_exists=False)
        self.daemon(fake).tick()

        self.assertEqual(["DEV_SYNC_PENDING:missing-integration-branch:auto-refact-dev"], self.pending_events())
        self.assertFalse(any(command[:2] == ["git", "fetch"] for command in fake.commands))

    def test_resolver_in_flight_scopes_to_repo_or_worktree_and_skips_shell_wrappers(self) -> None:
        repo = Path("/tmp/repo")
        worktree = repo / ".worktrees" / "dev-sync"

        def command_runner(line: str):
            return lambda _cmd: subprocess.CompletedProcess(_cmd, 0, line + "\n", "")

        in_scope = f"bash {repo}/skill/scripts/consensus-rnd-cli spawn-codex --cd {worktree} --log {repo}/.refactor-loop/logs/dev-sync-codex-1.log"
        sibling = "bash /tmp/other/skill/scripts/consensus-rnd-cli spawn-codex --log /tmp/other/.refactor-loop/logs/dev-sync-codex-1.log"
        wrapped = f"bash -c {repo}/skill/scripts/consensus-rnd-cli spawn-codex --log {repo}/.refactor-loop/logs/dev-sync-codex-1.log"

        self.assertTrue(codex_resolve_in_flight(main_repo=repo, worktree=worktree, command_runner=command_runner(in_scope)))
        self.assertFalse(codex_resolve_in_flight(main_repo=repo, worktree=worktree, command_runner=command_runner(sibling)))
        self.assertFalse(codex_resolve_in_flight(main_repo=repo, worktree=worktree, command_runner=command_runner(wrapped)))

    def test_merge_in_progress_uses_git_path_for_linked_worktree_state(self) -> None:
        merge_head = self.repo / ".git" / "worktrees" / "dev-sync" / "MERGE_HEAD"
        merge_head.parent.mkdir(parents=True)
        merge_head.write_text("abc\n", encoding="utf-8")

        def command_runner(cmd: list[str], cwd: Path | None = None, check: bool = False) -> subprocess.CompletedProcess[str]:
            target = merge_head if cmd[-1] == "MERGE_HEAD" else self.repo / ".git" / "worktrees" / "dev-sync" / "MERGE_MSG"
            return subprocess.CompletedProcess(cmd, 0, f"{target}\n", "")

        self.assertTrue(merge_in_progress(self.worktree, command_runner))


class SyncDevSourceRegressionTests(unittest.TestCase):
    def test_module_is_import_safe_without_repo_root(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", "import codex_refactor_loop.sync.dev; print('ok')"],
            cwd=SCRIPT_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("ok", result.stdout.strip())

    def test_dev_sync_module_has_no_git_lifecycle_mutation_tokens(self) -> None:
        src = SYNC_DEV.read_text(encoding="utf-8")
        for token in ("git push", "git merge", "git rebase", "git reset --hard", "--force-with-lease"):
            with self.subTest(token=token):
                self.assertNotIn(token, src)
        for token in ('["git", "push"', '["git", "merge"', '["git", "rebase"', '["git", "reset"'):
            with self.subTest(token=token):
                self.assertNotIn(token, src)

    def test_narrow_allowlist_contract_is_visible_in_module_source(self) -> None:
        src = SYNC_DEV.read_text(encoding="utf-8")
        self.assertIn("daemon detects and emits", src)
        self.assertIn("IntegrationSyncRequest; controller owns apply", src)
        self.assertIn("DEV_SYNC_PENDING:release-rollup-needed:", src)
        self.assertIn('append_pending_event("missing-integration-branch", self.integration)', src)
        self.assertIn('head_name.startswith("rollup/")', src)
        self.assertIn("DEV_SYNC_REQUEST:", src)


if __name__ == "__main__":
    unittest.main()
