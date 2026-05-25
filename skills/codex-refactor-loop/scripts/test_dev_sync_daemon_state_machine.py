#!/usr/bin/env python3
"""Behavior and source-regression tests for IntegrationSyncDaemonV1."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
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
    def __init__(self, *, ahead: int = 0, behind: int = 0, remote_sha: str = "remote-sha") -> None:
        self.ahead = ahead
        self.behind = behind
        self.remote_sha = remote_sha
        self.commands: list[list[str]] = []
        self.gh_rows: list[dict] = []
        self.merge_base_adopted = False
        self.old_head_is_ancestor = True

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
                stdout = f"{self.ahead}\n"
            elif spec.startswith("HEAD..origin/dev"):
                stdout = f"{self.behind}\n"
            else:
                stdout = "0\n"
        elif cmd[:3] == ["git", "rev-parse", "origin/auto-refact-dev"]:
            stdout = f"{self.remote_sha}\n"
        elif cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            if cmd[3] == "origin/dev":
                returncode = 0 if self.merge_base_adopted else 1
            else:
                returncode = 0 if self.old_head_is_ancestor else 1
        elif cmd[:3] == ["gh", "pr", "list"]:
            stdout = json.dumps(self.gh_rows)
        elif cmd[:3] == ["git", "merge", "--ff-only"]:
            stdout = "Already up to date\n" if self.behind == 0 else "Fast-forward\n"
        elif cmd[:2] == ["git", "push"]:
            pass
        elif cmd[:3] == ["git", "reset", "--hard"]:
            pass
        elif cmd[:3] == ["git", "rebase", "--rebase-merges"]:
            pass
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
        )

    def command_index(self, fake: FakeGit, needle: list[str]) -> int:
        for index, command in enumerate(fake.commands):
            if command[: len(needle)] == needle:
                return index
        self.fail(f"missing command prefix {needle!r} in {fake.commands!r}")

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


if __name__ == "__main__":
    unittest.main()
