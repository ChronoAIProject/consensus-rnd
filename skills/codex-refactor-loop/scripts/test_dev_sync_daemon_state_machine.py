#!/usr/bin/env python3
"""Behavior and source-regression tests for IntegrationSyncDaemonV1."""

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

import dev_sync_daemon
from dev_sync_daemon import IntegrationSyncDaemonV1, RollupAdoption

SKILL_ROOT = REPO_ROOT / "skills" / "codex-refactor-loop"
DEV_SYNC = SKILL_ROOT / "scripts" / "dev_sync_daemon.py"
SKILL_MD = SKILL_ROOT / "SKILL.md"
REFERENCE_MD = SKILL_ROOT / "REFERENCE.md"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
AGENTS_MD = REPO_ROOT / "AGENTS.md"


class FakeGit:
    def __init__(
        self,
        *,
        ahead: int = 0,
        release_ahead: int = 0,
        behind: int = 0,
        remote_sha: str = "remote-sha",
        review_base_sha: str = "review-base-sha",
        ahead_stdout: str | None = None,
        release_ahead_stdout: str | None = None,
        behind_stdout: str | None = None,
        replay_count: int = 0,
        replay_count_stdout: str | None = None,
        remote_sha_returncode: int = 0,
        remote_sha_stderr: str = "",
        gh_rows: list[dict] | None = None,
        open_gh_rows: list[dict] | None = None,
        gh_stdout: str | None = None,
        open_gh_stdout: str | None = None,
        gh_returncode: int = 0,
        gh_stderr: str = "",
        merge_base_adopted: bool = False,
        old_head_is_ancestor: bool = True,
        ff_returncode: int | None = None,
        ff_stdout: str | None = None,
        ff_stderr: str = "",
        no_ff_returncode: int = 0,
        no_ff_stdout: str = "",
        no_ff_stderr: str = "",
        reset_fail_targets: set[str] | None = None,
        reset_stderr: str = "",
        rebase_returncode: int = 0,
        rebase_stderr: str = "",
        push_returncode: int = 0,
        force_push_returncode: int | None = None,
        push_stderr: str = "",
    ) -> None:
        self.ahead = ahead
        self.release_ahead = release_ahead
        self.behind = behind
        self.remote_sha = remote_sha
        self.review_base_sha = review_base_sha
        self.ahead_stdout = ahead_stdout
        self.release_ahead_stdout = release_ahead_stdout
        self.behind_stdout = behind_stdout
        self.replay_count = replay_count
        self.replay_count_stdout = replay_count_stdout
        self.remote_sha_returncode = remote_sha_returncode
        self.remote_sha_stderr = remote_sha_stderr
        self.gh_stdout = gh_stdout
        self.open_gh_stdout = open_gh_stdout
        self.gh_returncode = gh_returncode
        self.gh_stderr = gh_stderr
        self.merge_base_adopted = merge_base_adopted
        self.old_head_is_ancestor = old_head_is_ancestor
        self.ff_returncode = ff_returncode
        self.ff_stdout = ff_stdout
        self.ff_stderr = ff_stderr
        self.no_ff_returncode = no_ff_returncode
        self.no_ff_stdout = no_ff_stdout
        self.no_ff_stderr = no_ff_stderr
        self.reset_fail_targets = reset_fail_targets or set()
        self.reset_stderr = reset_stderr
        self.rebase_returncode = rebase_returncode
        self.rebase_stderr = rebase_stderr
        self.push_returncode = push_returncode
        self.force_push_returncode = force_push_returncode
        self.push_stderr = push_stderr
        self.commands: list[list[str]] = []
        self.gh_rows: list[dict] = gh_rows or []
        self.open_gh_rows: list[dict] = open_gh_rows or []

    def __call__(self, cmd: list[str], cwd: Path | None = None, check: bool = False) -> subprocess.CompletedProcess:
        self.commands.append(cmd)
        stdout = ""
        returncode = 0
        stderr = ""
        if cmd[:2] == ["git", "fetch"]:
            pass
        elif cmd[:3] == ["git", "rev-list", "--count"]:
            spec = cmd[3]
            if spec.startswith("origin/auto-refact-dev..HEAD"):
                stdout = self.ahead_stdout if self.ahead_stdout is not None else f"{self.ahead}\n"
            elif spec.startswith("origin/dev..origin/auto-refact-dev"):
                stdout = self.release_ahead_stdout if self.release_ahead_stdout is not None else f"{self.release_ahead}\n"
            elif spec.startswith("HEAD..origin/dev"):
                stdout = self.behind_stdout if self.behind_stdout is not None else f"{self.behind}\n"
            elif spec.startswith("old-head..origin/auto-refact-dev"):
                stdout = self.replay_count_stdout if self.replay_count_stdout is not None else f"{self.replay_count}\n"
            else:
                stdout = "0\n"
        elif cmd[:3] == ["git", "rev-parse", "origin/auto-refact-dev"]:
            returncode = self.remote_sha_returncode
            stdout = f"{self.remote_sha}\n" if returncode == 0 else ""
            stderr = self.remote_sha_stderr
        elif cmd[:3] == ["git", "rev-parse", "origin/dev"]:
            stdout = f"{self.review_base_sha}\n"
        elif cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            if cmd[3] == "origin/dev":
                returncode = 0 if self.merge_base_adopted else 1
            else:
                returncode = 0 if self.old_head_is_ancestor else 1
        elif cmd[:3] == ["gh", "pr", "list"]:
            returncode = self.gh_returncode
            if "--state" in cmd and cmd[cmd.index("--state") + 1] == "open":
                stdout = self.open_gh_stdout if self.open_gh_stdout is not None else json.dumps(self.open_gh_rows)
            else:
                stdout = self.gh_stdout if self.gh_stdout is not None else json.dumps(self.gh_rows)
            stderr = self.gh_stderr
        elif cmd[:3] == ["git", "merge", "--ff-only"]:
            returncode = self.ff_returncode if self.ff_returncode is not None else 0
            stdout = self.ff_stdout if self.ff_stdout is not None else ("Already up to date\n" if self.behind == 0 else "Fast-forward\n")
            stderr = self.ff_stderr
        elif cmd[:2] == ["git", "push"]:
            is_force_push = any(part.startswith("--force-with-lease=") for part in cmd)
            if is_force_push and self.force_push_returncode is not None:
                returncode = self.force_push_returncode
            else:
                returncode = self.push_returncode
            stderr = self.push_stderr
        elif cmd[:3] == ["git", "reset", "--hard"]:
            if cmd[3] in self.reset_fail_targets:
                returncode = 1
                stderr = self.reset_stderr
        elif cmd[:3] == ["git", "rebase", "--rebase-merges"]:
            returncode = self.rebase_returncode
            stderr = self.rebase_stderr
        elif cmd[:3] == ["git", "merge", "--no-ff"]:
            returncode = self.no_ff_returncode
            stdout = self.no_ff_stdout
            stderr = self.no_ff_stderr
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)


class IntegrationSyncDaemonV1BehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.worktree = self.repo / "wt"
        self.worktree.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def daemon(self, fake: FakeGit, **overrides) -> IntegrationSyncDaemonV1:
        return IntegrationSyncDaemonV1(
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
            now_provider=overrides.get("now_provider", lambda: datetime(2026, 5, 27, 0, 0, 0, tzinfo=timezone.utc)),
        )

    def command_index(self, fake: FakeGit, needle: list[str]) -> int:
        for index, command in enumerate(fake.commands):
            if command[: len(needle)] == needle:
                return index
        self.fail(f"missing command prefix {needle!r} in {fake.commands!r}")

    def pending_events(self) -> list[str]:
        path = self.repo / ".refactor-loop" / ".controller-pending-events.log"
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8").splitlines()

    def release_rollup_events(self) -> list[dict]:
        events = []
        prefix = "DEV_SYNC_PENDING:release-rollup-needed:"
        for line in self.pending_events():
            if line.startswith(prefix):
                events.append(json.loads(line[len(prefix):]))
        return events

    def test_clean_local_ahead_is_pushed_before_reset(self) -> None:
        fake = FakeGit(ahead=2)

        self.daemon(fake).tick()

        push_i = self.command_index(fake, ["git", "push", "origin", "HEAD:auto-refact-dev"])
        self.assertNotIn(["git", "reset", "--hard", "origin/auto-refact-dev"], fake.commands[:push_i])

    def test_resolver_completed_merge_commit_is_not_discarded(self) -> None:
        fake = FakeGit(ahead=1)

        self.daemon(fake).tick()

        self.assertIn(["git", "push", "origin", "HEAD:auto-refact-dev"], fake.commands)
        self.assertNotIn(["git", "reset", "--hard", "origin/auto-refact-dev"], fake.commands)

    def test_dirty_non_merge_worktree_skips_without_reset(self) -> None:
        fake = FakeGit()

        self.daemon(fake, dirty_detector=lambda _cwd: True).tick()

        self.assertNotIn(["git", "reset", "--hard", "origin/auto-refact-dev"], fake.commands)
        self.assertFalse(any(command[:2] == ["git", "push"] for command in fake.commands))

    def test_normal_forward_sync_never_uses_force_with_lease(self) -> None:
        fake = FakeGit(behind=1)

        self.daemon(fake).tick()

        push_commands = [command for command in fake.commands if command[:2] == ["git", "push"]]
        self.assertTrue(push_commands)
        self.assertFalse(any("--force-with-lease" in part for command in push_commands for part in command))

    def test_merged_rollup_adoption_uses_expected_remote_sha_force_with_lease(self) -> None:
        fake = FakeGit(remote_sha="expected-remote")
        fake.gh_rows = [{"number": 45, "headRefOid": "old-head", "mergedAt": "2026-05-25T00:00:00Z"}]

        self.daemon(fake).tick()

        self.assertIn(
            [
                "git",
                "push",
                "--force-with-lease=refs/heads/auto-refact-dev:expected-remote",
                "origin",
                "HEAD:auto-refact-dev",
            ],
            fake.commands,
        )

    def test_rollup_adoption_unknown_old_head_emits_pending_event_without_push(self) -> None:
        fake = FakeGit(remote_sha="expected-remote")
        fake.gh_rows = [{"number": 45, "headRefOid": "", "mergedAt": "2026-05-25T00:00:00Z"}]

        self.daemon(fake).tick()

        events = self.repo / ".refactor-loop" / ".controller-pending-events.log"
        self.assertIn("DEV_SYNC_PENDING:rollup-adoption-ambiguous:missing-headRefOid", events.read_text())
        self.assertFalse(any(command[:2] == ["git", "push"] for command in fake.commands))

    def test_merged_rollup_adoption_replays_post_rollup_commits_before_force_push(self) -> None:
        fake = FakeGit(
            remote_sha="expected-remote",
            gh_rows=[{"number": 45, "headRefOid": "old-head", "mergedAt": "2026-05-25T00:00:00Z"}],
            replay_count=2,
        )

        self.daemon(fake).tick()

        self.assertEqual(
            fake.commands,
            [
                ["git", "fetch", "origin", "--quiet"],
                ["git", "rev-list", "--count", "origin/auto-refact-dev..HEAD"],
                ["git", "merge-base", "--is-ancestor", "origin/dev", "origin/auto-refact-dev"],
                ["git", "rev-parse", "origin/auto-refact-dev"],
                [
                    "gh",
                    "pr",
                    "list",
                    "--state",
                    "merged",
                    "--head",
                    "auto-refact-dev",
                    "--base",
                    "dev",
                    "--limit",
                    "1",
                    "--json",
                    "number,headRefOid,mergedAt",
                ],
                ["git", "merge-base", "--is-ancestor", "old-head", "origin/auto-refact-dev"],
                ["git", "rev-list", "--count", "old-head..origin/auto-refact-dev"],
                ["git", "reset", "--hard", "origin/auto-refact-dev"],
                ["git", "rebase", "--rebase-merges", "--onto", "origin/dev", "old-head"],
                [
                    "git",
                    "push",
                    "--force-with-lease=refs/heads/auto-refact-dev:expected-remote",
                    "origin",
                    "HEAD:auto-refact-dev",
                ],
            ],
        )
        self.assertEqual(self.pending_events(), [])

    def test_rollup_post_rollup_count_unknown_emits_pending_event(self) -> None:
        fake = FakeGit(
            gh_rows=[{"number": 45, "headRefOid": "old-head", "mergedAt": "2026-05-25T00:00:00Z"}],
            replay_count_stdout="not-a-number\n",
        )

        self.daemon(fake).tick()

        self.assertEqual(
            self.pending_events(),
            ["DEV_SYNC_PENDING:rollup-adoption-ambiguous:post-rollup-count-unknown"],
        )
        self.assertEqual(
            fake.commands,
            [
                ["git", "fetch", "origin", "--quiet"],
                ["git", "rev-list", "--count", "origin/auto-refact-dev..HEAD"],
                ["git", "merge-base", "--is-ancestor", "origin/dev", "origin/auto-refact-dev"],
                ["git", "rev-parse", "origin/auto-refact-dev"],
                [
                    "gh",
                    "pr",
                    "list",
                    "--state",
                    "merged",
                    "--head",
                    "auto-refact-dev",
                    "--base",
                    "dev",
                    "--limit",
                    "1",
                    "--json",
                    "number,headRefOid,mergedAt",
                ],
                ["git", "merge-base", "--is-ancestor", "old-head", "origin/auto-refact-dev"],
                ["git", "rev-list", "--count", "old-head..origin/auto-refact-dev"],
            ],
        )

    def test_rollup_adoption_reset_failed_emits_pending_event(self) -> None:
        fake = FakeGit(
            gh_rows=[{"number": 45, "headRefOid": "old-head", "mergedAt": "2026-05-25T00:00:00Z"}],
            replay_count=1,
            reset_fail_targets={"origin/auto-refact-dev"},
            reset_stderr="reset denied",
        )

        self.daemon(fake).tick()

        self.assertEqual(
            self.pending_events(),
            ["DEV_SYNC_PENDING:rollup-adoption-reset-failed:reset denied"],
        )
        self.assertEqual(
            fake.commands,
            [
                ["git", "fetch", "origin", "--quiet"],
                ["git", "rev-list", "--count", "origin/auto-refact-dev..HEAD"],
                ["git", "merge-base", "--is-ancestor", "origin/dev", "origin/auto-refact-dev"],
                ["git", "rev-parse", "origin/auto-refact-dev"],
                [
                    "gh",
                    "pr",
                    "list",
                    "--state",
                    "merged",
                    "--head",
                    "auto-refact-dev",
                    "--base",
                    "dev",
                    "--limit",
                    "1",
                    "--json",
                    "number,headRefOid,mergedAt",
                ],
                ["git", "merge-base", "--is-ancestor", "old-head", "origin/auto-refact-dev"],
                ["git", "rev-list", "--count", "old-head..origin/auto-refact-dev"],
                ["git", "reset", "--hard", "origin/auto-refact-dev"],
            ],
        )

    def test_rollup_adoption_replay_conflict_emits_pending_event(self) -> None:
        fake = FakeGit(
            gh_rows=[{"number": 45, "headRefOid": "old-head", "mergedAt": "2026-05-25T00:00:00Z"}],
            replay_count=1,
            rebase_returncode=1,
            rebase_stderr="rebase conflict",
        )

        self.daemon(fake).tick()

        self.assertEqual(
            self.pending_events(),
            ["DEV_SYNC_PENDING:rollup-adoption-replay-conflict:rebase conflict"],
        )
        self.assertEqual(
            fake.commands,
            [
                ["git", "fetch", "origin", "--quiet"],
                ["git", "rev-list", "--count", "origin/auto-refact-dev..HEAD"],
                ["git", "merge-base", "--is-ancestor", "origin/dev", "origin/auto-refact-dev"],
                ["git", "rev-parse", "origin/auto-refact-dev"],
                [
                    "gh",
                    "pr",
                    "list",
                    "--state",
                    "merged",
                    "--head",
                    "auto-refact-dev",
                    "--base",
                    "dev",
                    "--limit",
                    "1",
                    "--json",
                    "number,headRefOid,mergedAt",
                ],
                ["git", "merge-base", "--is-ancestor", "old-head", "origin/auto-refact-dev"],
                ["git", "rev-list", "--count", "old-head..origin/auto-refact-dev"],
                ["git", "reset", "--hard", "origin/auto-refact-dev"],
                ["git", "rebase", "--rebase-merges", "--onto", "origin/dev", "old-head"],
            ],
        )

    def test_rollup_adoption_push_failed_emits_pending_event(self) -> None:
        fake = FakeGit(
            remote_sha="expected-remote",
            gh_rows=[{"number": 45, "headRefOid": "old-head", "mergedAt": "2026-05-25T00:00:00Z"}],
            force_push_returncode=1,
            push_stderr="lease rejected",
        )

        self.daemon(fake).tick()

        self.assertEqual(
            self.pending_events(),
            ["DEV_SYNC_PENDING:rollup-adoption-push-failed:lease rejected"],
        )
        self.assertEqual(
            fake.commands,
            [
                ["git", "fetch", "origin", "--quiet"],
                ["git", "rev-list", "--count", "origin/auto-refact-dev..HEAD"],
                ["git", "merge-base", "--is-ancestor", "origin/dev", "origin/auto-refact-dev"],
                ["git", "rev-parse", "origin/auto-refact-dev"],
                [
                    "gh",
                    "pr",
                    "list",
                    "--state",
                    "merged",
                    "--head",
                    "auto-refact-dev",
                    "--base",
                    "dev",
                    "--limit",
                    "1",
                    "--json",
                    "number,headRefOid,mergedAt",
                ],
                ["git", "merge-base", "--is-ancestor", "old-head", "origin/auto-refact-dev"],
                ["git", "rev-list", "--count", "old-head..origin/auto-refact-dev"],
                ["git", "reset", "--hard", "origin/dev"],
                [
                    "git",
                    "push",
                    "--force-with-lease=refs/heads/auto-refact-dev:expected-remote",
                    "origin",
                    "HEAD:auto-refact-dev",
                ],
            ],
        )

    def test_local_ahead_unknown_emits_pending_event_and_continues_to_normal_sync(self) -> None:
        fake = FakeGit(ahead_stdout="unknown\n", merge_base_adopted=True)

        self.daemon(fake).tick()

        self.assertEqual(
            self.pending_events(),
            ["DEV_SYNC_PENDING:local-ahead-unknown:"],
        )
        self.assertEqual(
            fake.commands,
            [
                ["git", "fetch", "origin", "--quiet"],
                ["git", "rev-list", "--count", "origin/auto-refact-dev..HEAD"],
                ["git", "merge-base", "--is-ancestor", "origin/dev", "origin/auto-refact-dev"],
                ["git", "reset", "--hard", "origin/auto-refact-dev"],
                ["git", "rev-list", "--count", "HEAD..origin/dev"],
            ],
        )

    def test_remote_sha_unknown_emits_pending_event_without_reset(self) -> None:
        fake = FakeGit(remote_sha_returncode=1, remote_sha_stderr="missing remote")

        self.daemon(fake).tick()

        self.assertEqual(
            self.pending_events(),
            ["DEV_SYNC_PENDING:remote-sha-unknown:missing remote"],
        )
        self.assertEqual(
            fake.commands,
            [
                ["git", "fetch", "origin", "--quiet"],
                ["git", "rev-list", "--count", "origin/auto-refact-dev..HEAD"],
                ["git", "merge-base", "--is-ancestor", "origin/dev", "origin/auto-refact-dev"],
                ["git", "rev-parse", "origin/auto-refact-dev"],
            ],
        )

    def test_local_ahead_push_failed_emits_pending_event_without_reset(self) -> None:
        fake = FakeGit(ahead=1, push_returncode=1, push_stderr="push rejected")

        self.daemon(fake).tick()

        self.assertEqual(
            self.pending_events(),
            ["DEV_SYNC_PENDING:local-ahead-push-failed:push rejected"],
        )
        self.assertEqual(
            fake.commands,
            [
                ["git", "fetch", "origin", "--quiet"],
                ["git", "rev-list", "--count", "origin/auto-refact-dev..HEAD"],
                ["git", "push", "origin", "HEAD:auto-refact-dev"],
            ],
        )

    def test_gh_json_decode_failed_emits_pending_event_without_reset(self) -> None:
        fake = FakeGit(gh_stdout="{not json")

        self.daemon(fake).tick()

        self.assertEqual(
            self.pending_events(),
            ["DEV_SYNC_PENDING:rollup-adoption-ambiguous:gh-json-decode-failed"],
        )
        self.assertEqual(
            fake.commands,
            [
                ["git", "fetch", "origin", "--quiet"],
                ["git", "rev-list", "--count", "origin/auto-refact-dev..HEAD"],
                ["git", "merge-base", "--is-ancestor", "origin/dev", "origin/auto-refact-dev"],
                ["git", "rev-parse", "origin/auto-refact-dev"],
                [
                    "gh",
                    "pr",
                    "list",
                    "--state",
                    "merged",
                    "--head",
                    "auto-refact-dev",
                    "--base",
                    "dev",
                    "--limit",
                    "1",
                    "--json",
                    "number,headRefOid,mergedAt",
                ],
            ],
        )

    def test_old_head_not_ancestor_emits_pending_event_without_reset(self) -> None:
        fake = FakeGit(
            gh_rows=[{"number": 45, "headRefOid": "old-head", "mergedAt": "2026-05-25T00:00:00Z"}],
            old_head_is_ancestor=False,
        )

        self.daemon(fake).tick()

        self.assertEqual(
            self.pending_events(),
            ["DEV_SYNC_PENDING:rollup-adoption-ambiguous:old-head-not-ancestor:old-head"],
        )
        self.assertEqual(
            fake.commands,
            [
                ["git", "fetch", "origin", "--quiet"],
                ["git", "rev-list", "--count", "origin/auto-refact-dev..HEAD"],
                ["git", "merge-base", "--is-ancestor", "origin/dev", "origin/auto-refact-dev"],
                ["git", "rev-parse", "origin/auto-refact-dev"],
                [
                    "gh",
                    "pr",
                    "list",
                    "--state",
                    "merged",
                    "--head",
                    "auto-refact-dev",
                    "--base",
                    "dev",
                    "--limit",
                    "1",
                    "--json",
                    "number,headRefOid,mergedAt",
                ],
                ["git", "merge-base", "--is-ancestor", "old-head", "origin/auto-refact-dev"],
            ],
        )

    def test_forward_sync_no_ff_merge_success_pushes_integration_branch(self) -> None:
        fake = FakeGit(
            behind=2,
            merge_base_adopted=True,
            ff_returncode=1,
            ff_stdout="Not possible\n",
            no_ff_returncode=0,
        )

        self.daemon(fake).tick()

        self.assertEqual(self.pending_events(), [])
        self.assertEqual(
            fake.commands,
            [
                ["git", "fetch", "origin", "--quiet"],
                ["git", "rev-list", "--count", "origin/auto-refact-dev..HEAD"],
                ["git", "merge-base", "--is-ancestor", "origin/dev", "origin/auto-refact-dev"],
                ["git", "reset", "--hard", "origin/auto-refact-dev"],
                ["git", "rev-list", "--count", "HEAD..origin/dev"],
                ["git", "merge", "--ff-only", "origin/dev"],
                [
                    "git",
                    "merge",
                    "--no-ff",
                    "-m",
                    "Sync auto-refact-dev with dev (auto by dev_sync_daemon)",
                    "origin/dev",
                ],
                ["git", "push", "origin", "HEAD:auto-refact-dev"],
            ],
        )

    def test_forward_sync_merge_conflict_dispatches_resolver(self) -> None:
        fake = FakeGit(
            behind=2,
            merge_base_adopted=True,
            ff_returncode=1,
            no_ff_returncode=1,
            no_ff_stderr="conflict",
        )
        merge_checks = iter([False, True])
        dispatched: list[bool] = []

        self.daemon(
            fake,
            merge_detector=lambda _cwd: next(merge_checks),
            resolver_in_flight=lambda: False,
            resolver_dispatcher=lambda: dispatched.append(True),
        ).tick()

        self.assertEqual(dispatched, [True])
        self.assertEqual(self.pending_events(), [])
        self.assertEqual(
            fake.commands,
            [
                ["git", "fetch", "origin", "--quiet"],
                ["git", "rev-list", "--count", "origin/auto-refact-dev..HEAD"],
                ["git", "merge-base", "--is-ancestor", "origin/dev", "origin/auto-refact-dev"],
                ["git", "reset", "--hard", "origin/auto-refact-dev"],
                ["git", "rev-list", "--count", "HEAD..origin/dev"],
                ["git", "merge", "--ff-only", "origin/dev"],
                [
                    "git",
                    "merge",
                    "--no-ff",
                    "-m",
                    "Sync auto-refact-dev with dev (auto by dev_sync_daemon)",
                    "origin/dev",
                ],
            ],
        )

    def test_forward_sync_merge_conflict_skips_dispatch_when_resolver_in_flight(self) -> None:
        fake = FakeGit(
            behind=2,
            merge_base_adopted=True,
            ff_returncode=1,
            no_ff_returncode=1,
            no_ff_stderr="conflict",
        )
        merge_checks = iter([False, True])
        dispatched: list[bool] = []

        self.daemon(
            fake,
            merge_detector=lambda _cwd: next(merge_checks),
            resolver_in_flight=lambda: True,
            resolver_dispatcher=lambda: dispatched.append(True),
        ).tick()

        self.assertEqual(dispatched, [])
        self.assertEqual(self.pending_events(), [])
        self.assertEqual(
            fake.commands,
            [
                ["git", "fetch", "origin", "--quiet"],
                ["git", "rev-list", "--count", "origin/auto-refact-dev..HEAD"],
                ["git", "merge-base", "--is-ancestor", "origin/dev", "origin/auto-refact-dev"],
                ["git", "reset", "--hard", "origin/auto-refact-dev"],
                ["git", "rev-list", "--count", "HEAD..origin/dev"],
                ["git", "merge", "--ff-only", "origin/dev"],
                [
                    "git",
                    "merge",
                    "--no-ff",
                    "-m",
                    "Sync auto-refact-dev with dev (auto by dev_sync_daemon)",
                    "origin/dev",
                ],
            ],
        )

    def test_release_rollup_needed_emits_pending_event_when_integration_ahead_without_open_pr(self) -> None:
        fake = FakeGit(merge_base_adopted=True, release_ahead=3, remote_sha="integration-sha", review_base_sha="base-sha")

        self.daemon(fake, release_rollup_min_commits=1).tick()

        events = self.release_rollup_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["integration_branch"], "auto-refact-dev")
        self.assertEqual(events[0]["review_base_branch"], "dev")
        self.assertEqual(events[0]["integration_sha"], "integration-sha")
        self.assertEqual(events[0]["review_base_sha"], "base-sha")
        self.assertEqual(events[0]["ahead_count"], 3)
        self.assertEqual(events[0]["reason"], "integration-ahead-review-base-without-open-rollup-pr")

    def test_release_rollup_needed_does_not_emit_when_open_rollup_pr_exists(self) -> None:
        fake = FakeGit(
            merge_base_adopted=True,
            release_ahead=3,
            open_gh_rows=[{"number": 77, "headRefName": "auto-refact-dev", "baseRefName": "dev"}],
        )

        self.daemon(fake, release_rollup_min_commits=1).tick()

        self.assertEqual(self.release_rollup_events(), [])

    def test_release_rollup_detector_skips_on_gh_pr_list_nonzero_returncode(self) -> None:
        fake = FakeGit(
            merge_base_adopted=True,
            release_ahead=3,
            gh_returncode=1,
            gh_stderr="gh unavailable",
        )

        self.daemon(fake, release_rollup_min_commits=1).tick()

        self.assertEqual(self.release_rollup_events(), [])

    def test_release_rollup_detector_skips_on_malformed_gh_pr_list_stdout(self) -> None:
        fake = FakeGit(
            merge_base_adopted=True,
            release_ahead=3,
            open_gh_stdout="{not json",
        )

        self.daemon(fake, release_rollup_min_commits=1).tick()

        self.assertEqual(self.release_rollup_events(), [])

    def test_release_rollup_needed_does_not_emit_below_threshold_or_without_ahead(self) -> None:
        for release_ahead, threshold in ((0, 1), (1, 2)):
            with self.subTest(release_ahead=release_ahead, threshold=threshold):
                self.tearDown()
                self.setUp()
                fake = FakeGit(merge_base_adopted=True, release_ahead=release_ahead)

                self.daemon(fake, release_rollup_min_commits=threshold).tick()

                self.assertEqual(self.release_rollup_events(), [])

    def test_release_rollup_needed_cooldown_emits_same_integration_sha_once(self) -> None:
        fake = FakeGit(merge_base_adopted=True, release_ahead=2, remote_sha="same-sha", review_base_sha="base-sha")
        daemon = self.daemon(fake, release_rollup_min_commits=1, release_rollup_cooldown_seconds=3600)

        daemon.tick()
        daemon.tick()

        self.assertEqual(len(self.release_rollup_events()), 1)


class IntegrationSyncDaemonV1SourceRegressionTests(unittest.TestCase):
    def test_skill_phase6_names_integration_sync_daemon_v1(self) -> None:
        text = SKILL_MD.read_text(encoding="utf-8")
        self.assertIn("Named exception: `IntegrationSyncDaemonV1`", text)
        self.assertIn("resolver continuation push", text)
        self.assertIn("merged-rollup adoption", text)

    def test_reference_has_no_active_controller_sync_procedure(self) -> None:
        text = REFERENCE_MD.read_text(encoding="utf-8")
        self.assertNotIn("### Sync procedure", text)
        self.assertNotIn("### Sync cadence", text)
        self.assertNotIn("Every controller wakeup (cheap when `behind == 0`)", text)
        self.assertIn("IntegrationSyncDaemonV1", text)

    def test_reference_integration_sync_state_is_absent_or_non_contractual(self) -> None:
        text = REFERENCE_MD.read_text(encoding="utf-8")
        self.assertNotIn('"integration_sync"', text)
        self.assertNotIn("last_sync_added_commits", text)
        self.assertNotIn("consecutive_failures", text)

    def test_dev_sync_reset_is_after_local_ahead_guard(self) -> None:
        src = DEV_SYNC.read_text(encoding="utf-8")
        self.assertLess(src.index("push_clean_local_ahead_before_reset(cwd)"), src.index("reset_to_remote(cwd)"))
        self.assertIn("def local_ahead_count", src)
        self.assertIn("rev-list\", \"--count\", f\"origin/{self.integration}..HEAD", src)

    def test_dev_sync_force_with_lease_is_adoption_only(self) -> None:
        src = DEV_SYNC.read_text(encoding="utf-8")
        self.assertEqual(src.count("--force-with-lease="), 1)
        lease_i = src.index("--force-with-lease=")
        adopt_i = src.index("def adopt_merged_rollup")
        forward_i = src.index("def forward_sync_review_base")
        self.assertGreater(lease_i, adopt_i)
        self.assertLess(lease_i, forward_i)

    def test_project_rules_unchanged_and_no_forbidden_abstractions_for_integration_sync_daemon_v1(self) -> None:
        for path in (CLAUDE_MD, AGENTS_MD):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("IntegrationSyncDaemonV1", text)
            self.assertNotIn("merged-rollup adoption", text)
        src = DEV_SYNC.read_text(encoding="utf-8")
        for forbidden in (
            "IAsyncEnumerable",
            "Channel",
            "actor inbox",
            "projection pipeline",
            "event envelope",
            "WorkUnitV2",
            "ControllerEvent",
            "ControllerCommand",
            "ControllerOrchestrator",
            "generic event bus",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, src)

    def test_dev_sync_daemon_has_no_pr_lifecycle_or_direct_review_base_push(self) -> None:
        src = DEV_SYNC.read_text(encoding="utf-8")
        forbidden_tokens = (
            "gh pr create",
            "gh pr edit",
            "gh pr merge",
            "HEAD:$REVIEW_BASE_BRANCH",
            "HEAD:{self.review_base}",
        )
        for token in forbidden_tokens:
            with self.subTest(token=token):
                self.assertNotIn(token, src)


if __name__ == "__main__":
    unittest.main()
