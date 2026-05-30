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
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.sync.dev import IntegrationSyncDaemon, codex_resolve_in_flight, dispatch_codex_resolve, merge_in_progress


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
        dirty: bool = False,
        unresolved: bool = False,
        ff_fails: bool = False,
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
        self.dirty = dirty
        self.unresolved = unresolved
        self.ff_fails = ff_fails
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
        elif cmd[:3] == ["git", "rev-parse", "--git-path"]:
            stdout = f"{(cwd or Path('/tmp')) / '.git' / cmd[3]}\n"
        elif cmd[:3] == ["git", "diff", "--quiet"]:
            returncode = 1 if self.dirty else 0
        elif cmd[:3] == ["git", "diff", "--cached"]:
            returncode = 0
        elif cmd[:4] == ["git", "diff", "--name-only", "--diff-filter=U"]:
            stdout = "conflict.txt\n" if self.unresolved else ""
        elif cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            returncode = 0 if (self.merge_base_adopted if cmd[3] == "origin/dev" else self.old_head_is_ancestor) else 1
        elif cmd[:3] == ["git", "merge", "--ff-only"] and self.ff_fails:
            returncode = 1
            stdout = ""
        elif cmd[:3] == ["gh", "pr", "list"]:
            rows = self.open_gh_rows if "--state" in cmd and cmd[cmd.index("--state") + 1] == "open" else self.gh_rows
            stdout = json.dumps(rows)
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
            context=overrides.get("context"),
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

    def operation_jsons(self) -> list[dict]:
        paths = sorted((self.repo / ".refactor-loop" / "runs").glob("integration-sync-operation-*.json"))
        return [json.loads(path.read_text(encoding="utf-8")) for path in paths]

    def execution_jsons(self, status: str = "applied") -> list[dict]:
        paths = sorted((self.repo / ".refactor-loop" / "runs" / "integration-sync-executions").glob(f"*.{status}.json"))
        return [json.loads(path.read_text(encoding="utf-8")) for path in paths]

    def test_clean_local_ahead_executes_push_without_dev_sync_request(self) -> None:
        fake = FakeGit(ahead=2)
        self.daemon(fake).tick()

        operation = self.operation_jsons()[0]
        self.assertEqual("IntegrationSyncOperation", operation["schema"])
        self.assertEqual("push-local-ahead", operation["kind"])
        self.assertEqual("auto-refact-dev", operation["integration_branch"])
        self.assertEqual("dev", operation["review_base_branch"])
        self.assertEqual("head-sha", operation["worktree_head"])
        self.assertEqual("remote-sha", operation["expected_remote_sha"])
        self.assertEqual("dev_sync_daemon", operation["executor"])
        self.assertEqual("integration-branch-git-allowlist", operation["authority"])
        self.assertNotIn("lifecycle_owner", operation)
        self.assertEqual([], [line for line in self.pending_events() if line.startswith("DEV_SYNC_REQUEST:")])
        self.assertIn(["git", "push", "origin", "HEAD:auto-refact-dev"], fake.commands)
        self.assertFalse(any(command[:2] == ["git", "reset"] for command in fake.commands))
        self.assertEqual("applied", self.execution_jsons()[0]["status"])

    def test_merged_rollup_adoption_executes_operation(self) -> None:
        fake = FakeGit(
            gh_rows=[{"number": 45, "headRefName": "auto-refact-dev", "headRefOid": "old-head", "mergedAt": "2026-05-25T00:00:00Z"}],
            replay_count=2,
        )
        self.daemon(fake).tick()

        operation = self.operation_jsons()[0]
        self.assertEqual("adopt-merged-rollup", operation["kind"])
        self.assertEqual("old-head", operation["old_rollup_head"])
        self.assertEqual(2, operation["old_rollup_ahead_count"])
        self.assertEqual(45, operation["pr_number"])
        self.assertEqual({"reason": "merged-rollup-adoption", "replay_count": 2}, operation["evidence"])
        self.assertIn(["git", "push", "--force-with-lease=refs/heads/auto-refact-dev:remote-sha", "origin", "HEAD:auto-refact-dev"], fake.commands)

    def test_merged_throwaway_rollup_head_emits_adoption_request(self) -> None:
        fake = FakeGit(
            gh_rows=[{"number": 46, "headRefName": "rollup/old-head", "headRefOid": "old-head", "mergedAt": "2026-05-25T00:00:00Z"}],
            replay_count=0,
        )
        self.daemon(fake).tick()

        operation = self.operation_jsons()[0]
        self.assertEqual("adopt-merged-rollup", operation["kind"])
        self.assertEqual(46, operation["pr_number"])
        self.assertEqual("old-head", operation["old_rollup_head"])

    def test_forward_sync_review_base_executes_merge_or_push(self) -> None:
        fake = FakeGit(behind=3, merge_base_adopted=True, remote_sha="head-sha")
        self.daemon(fake).tick()

        operation = self.operation_jsons()[0]
        self.assertEqual("forward-sync-review-base", operation["kind"])
        self.assertEqual({"behind_count": 3, "reason": "review-base-ahead-of-integration"}, operation["evidence"])
        self.assertIn(["git", "merge", "--ff-only", "origin/dev"], fake.commands)
        self.assertIn(["git", "push", "origin", "HEAD:auto-refact-dev"], fake.commands)

    def test_daemon_reset_to_remote_rejects_dirty_non_merge(self) -> None:
        fake = FakeGit(remote_sha="remote-sha", head_sha="old-local", dirty=True, merge_base_adopted=True)
        self.daemon(fake, dirty_detector=lambda _cwd: False).tick()

        operation = self.operation_jsons()[0]
        self.assertEqual("reset-to-remote", operation["kind"])
        self.assertEqual("rejected", self.execution_jsons("rejected")[0]["status"])
        self.assertFalse(any(command[:3] == ["git", "reset", "--hard"] for command in fake.commands))

    def test_merge_in_progress_dispatches_resolver_without_request_apply(self) -> None:
        fake = FakeGit(unresolved=True)
        dispatched: list[bool] = []
        self.daemon(
            fake,
            merge_detector=lambda _cwd: True,
            resolver_in_flight=lambda: False,
            resolver_dispatcher=lambda: dispatched.append(True),
        ).tick()

        self.assertEqual([True], dispatched)
        self.assertEqual([], self.operation_jsons())

    def test_daemon_continue_resolved_merge_pushes(self) -> None:
        fake = FakeGit()
        merge_head = self.worktree / ".git" / "MERGE_HEAD"
        merge_head.parent.mkdir(parents=True, exist_ok=True)
        merge_head.write_text("merge\n", encoding="utf-8")
        self.daemon(fake, merge_detector=lambda _cwd: True, resolver_in_flight=lambda: False).tick()

        operation = self.operation_jsons()[0]
        self.assertEqual("continue-resolved-merge", operation["kind"])
        self.assertIn(["git", "merge", "--continue"], fake.commands)
        self.assertIn(["git", "push", "origin", "HEAD:auto-refact-dev"], fake.commands)

    def test_release_rollup_needed_appends_existing_pending_event_format(self) -> None:
        fake = FakeGit(merge_base_adopted=True, release_ahead=3, remote_sha="head-sha", review_base_sha="base-sha")
        self.daemon(fake, release_rollup_min_commits=1).tick()

        prefix = "DEV_SYNC_PENDING:release-rollup-needed:"
        self.assertTrue(self.pending_events()[0].startswith(prefix))
        event = json.loads(self.pending_events()[0][len(prefix):])
        self.assertEqual("auto-refact-dev", event["integration_branch"])
        self.assertEqual("dev", event["review_base_branch"])
        self.assertEqual("head-sha", event["integration_sha"])
        self.assertEqual("base-sha", event["review_base_sha"])
        self.assertEqual(3, event["ahead_count"])
        self.assertEqual("2026-05-27T00:00:00Z", event["detected_at"])
        self.assertEqual("integration-ahead-review-base-without-open-rollup-pr", event["reason"])
        self.assertEqual([], self.operation_jsons())

    def test_release_rollup_open_same_sha_throwaway_head_suppresses_duplicate_event(self) -> None:
        fake = FakeGit(
            merge_base_adopted=True,
            release_ahead=3,
            remote_sha="head-sha",
            review_base_sha="base-sha",
            open_gh_rows=[{"number": 77, "headRefName": "rollup/head-sha", "headRefOid": "head-sha"}],
        )
        self.daemon(fake, release_rollup_min_commits=1).tick()

        self.assertEqual([], self.pending_events())
        self.assertEqual([], self.operation_jsons())

    def test_release_rollup_open_stale_throwaway_head_does_not_suppress_event(self) -> None:
        fake = FakeGit(
            merge_base_adopted=True,
            release_ahead=3,
            remote_sha="head-sha",
            review_base_sha="base-sha",
            open_gh_rows=[{"number": 77, "headRefName": "rollup/old-sha", "headRefOid": "old-sha"}],
        )
        self.daemon(fake, release_rollup_min_commits=1).tick()

        self.assertTrue(self.pending_events()[0].startswith("DEV_SYNC_PENDING:release-rollup-needed:"))
        self.assertEqual([], self.operation_jsons())

    def test_missing_integration_branch_appends_alert_event_and_stops(self) -> None:
        fake = FakeGit(integration_ref_exists=False)
        self.daemon(fake).tick()

        self.assertEqual(["DEV_SYNC_PENDING:missing-integration-branch:auto-refact-dev"], self.pending_events())
        self.assertEqual(["git", "ls-remote", "--exit-code", "--heads", "origin", "auto-refact-dev"], fake.commands[0])
        self.assertFalse(any(command[:2] == ["git", "fetch"] for command in fake.commands))

    # Refactor (impl/issue191-single-active-controller): Old pattern: a
    # non-owner dev-sync daemon could touch the integration worktree and run
    # #53 git actions. New principle: non-owner exits before any worktree/git
    # or pending-event mutation.
    def test_non_owner_dev_sync_does_not_touch_worktree_git_or_pending_events(self) -> None:
        fake = FakeGit(ahead=2)
        ctx = LoopContext.load(repo_root=self.repo)
        decision = mock.Mock(allowed=False, owner_device="device-a", status="not-owner", action="dev-sync", lease_id="", expires_at="")

        with mock.patch("codex_refactor_loop.sync.dev.require_active_controller", return_value=decision):
            self.daemon(fake, context=ctx).tick()

        self.assertEqual([], fake.commands)
        self.assertEqual([], self.pending_events())
        self.assertEqual([], self.operation_jsons())

    def test_owner_dev_sync_keeps_existing_git_allowlist_path(self) -> None:
        fake = FakeGit(ahead=1)
        ctx = LoopContext.load(repo_root=self.repo)
        decision = mock.Mock(allowed=True, owner_device="device-b", status="owner", action="dev-sync", lease_id="lease", expires_at="")

        with mock.patch("codex_refactor_loop.sync.dev.require_active_controller", return_value=decision):
            self.daemon(fake, context=ctx).tick()

        self.assertIn(["git", "ls-remote", "--exit-code", "--heads", "origin", "auto-refact-dev"], fake.commands)
        self.assertEqual("push-local-ahead", self.operation_jsons()[0]["kind"])

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

    def test_conflict_resolver_prompt_stores_relative_paths_but_spawn_argv_absolute(self) -> None:
        worktree = self.repo / ".worktrees" / "dev-sync"
        worktree.mkdir(parents=True)
        popen_calls: list[list[str]] = []
        logger_lines: list[str] = []

        with mock.patch("codex_refactor_loop.sync.dev.time.time", return_value=1234), mock.patch(
            "codex_refactor_loop.sync.dev.subprocess.Popen",
            side_effect=lambda argv, **_kwargs: popen_calls.append(argv),
        ):
            dispatch_codex_resolve(
                worktree=worktree,
                main_repo=self.repo,
                integration="auto-refact-dev",
                review_base="dev",
                spawn_codex=self.repo / "skills" / "codex-refactor-loop" / "scripts" / "consensus-rnd-cli",
                logger=logger_lines.append,
            )

        prompt_file = self.repo / ".refactor-loop" / "prompts" / "dev-sync-conflict-1234.md"
        log_file = self.repo / ".refactor-loop" / "logs" / "dev-sync-codex-1234.log"
        prompt = prompt_file.read_text(encoding="utf-8")
        self.assertIn("`.worktrees/dev-sync`", prompt)
        self.assertIn("prompt artifact: `.refactor-loop/prompts/dev-sync-conflict-1234.md`", prompt)
        self.assertIn("resolver log artifact: `.refactor-loop/logs/dev-sync-codex-1234.log`", prompt)
        self.assertIn("main repo: `.`", prompt)
        self.assertNotIn(str(self.repo), prompt)
        self.assertEqual(["dispatching codex: prompt=.refactor-loop/prompts/dev-sync-conflict-1234.md log=.refactor-loop/logs/dev-sync-codex-1234.log"], logger_lines)

        argv = popen_calls[0]
        self.assertEqual(str(worktree), argv[argv.index("--cd") + 1])
        self.assertEqual(str(self.repo), argv[argv.index("--add-dir") + 1])
        self.assertEqual(str(prompt_file), argv[argv.index("--prompt") + 1])
        self.assertEqual(str(log_file), argv[argv.index("--log") + 1])
        self.assertTrue(Path(argv[argv.index("--cd") + 1]).is_absolute())
        self.assertTrue(Path(argv[argv.index("--prompt") + 1]).is_absolute())


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

    def test_dev_sync_module_has_only_named_git_lifecycle_mutation_tokens(self) -> None:
        src = SYNC_DEV.read_text(encoding="utf-8")
        for token in ("git commit", "gh pr create", "gh pr merge", "gh issue close", "git tag", "release publish"):
            with self.subTest(token=token):
                self.assertNotIn(token, src)

    def test_narrow_allowlist_contract_is_visible_in_module_source(self) -> None:
        src = SYNC_DEV.read_text(encoding="utf-8")
        self.assertIn("daemon writes IntegrationSyncOperation", src)
        self.assertIn("executes the #53 integration-branch git allowlist itself", src)
        self.assertIn("DEV_SYNC_PENDING:release-rollup-needed:", src)
        self.assertIn('["git", "ls-remote", "--exit-code", "--heads", "origin", branch]', src)
        self.assertIn('append_pending_event("missing-integration-branch", self.integration)', src)
        self.assertIn('head_name.startswith("rollup/")', src)
        self.assertNotIn("DEV_SYNC_REQUEST:", src)

    def test_sync_source_regression_uses_durable_display_paths(self) -> None:
        src = SYNC_DEV.read_text(encoding="utf-8")
        self.assertIn("ctx.durable_artifact_path(worktree)", src)
        self.assertIn("ctx.durable_artifact_path(prompt_file)", src)
        self.assertIn("ctx.durable_artifact_path(log_file)", src)
        self.assertIn("spawn-codex --cd/--add-dir/--prompt/--log", src)
        for forbidden in (
            "`{worktree}`. Resolve conflicts",
            "`cd {worktree}`",
            "main repo `{main_repo}`",
            "prompt={prompt_file} log={log_file}",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, src)


if __name__ == "__main__":
    unittest.main()
