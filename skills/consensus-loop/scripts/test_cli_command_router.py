#!/usr/bin/env python3
"""Behavior tests for the consensus-rnd-cli command router."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.cli import COMMANDS, RuntimeCommandRouter


CLI = SCRIPT_DIR / "consensus-rnd-cli"

ALL_AUTHORITY_TOKENS = {
    "delete-log",
    "delete-runtime",
    "gh-close",
    "gh-close-linked",
    "gh-comment",
    "gh-edit",
    "gh-label-closed-reconcile",
    "gh-label",
    "gh-label-owned",
    "gh-merge",
    "gh-open",
    "gh-reaction",
    "git-fetch",
    "git-commit-worker-output",
    "git-merge",
    "git-push",
    "git-rebase",
    "git-reset",
    "git-worktree",
    "read-artifact",
    "read-gh",
    "read-git",
    "read-log",
    "read-process",
    "read-source",
    "read-stdin",
    "read-state",
    "spawn",
    "spawn-daemon",
    "write-artifact",
    "write-event",
    "write-log",
    "write-source",
    "write-state",
    "controller-lifecycle-runner",
}

MUTATION_TOKENS = {
    token
    for token in ALL_AUTHORITY_TOKENS
    if not token.startswith("read-")
}

DAEMON_COMMANDS = {
    "closed-label-reconciler",
    "comment-monitor",
    "concurrency",
    "dev-sync",
    "phase9-router",
    "patrol-inspector",
    "progress-reporter",
    "release-gate",
    "restart-daemons",
    "runtime-retention",
    "wakeup-runner",
}

DAEMON_FORBIDDEN_LIFECYCLE_TOKENS = {
    "gh-close",
    "gh-edit",
    "gh-label",
    "gh-merge",
    "gh-open",
    "git-merge",
    "git-push",
    "git-rebase",
    "git-reset",
    "git-worktree",
}

DAEMON_LIFECYCLE_CARVEOUTS = {
    "dev-sync": {"git-fetch", "git-worktree", "git-merge", "git-push", "git-rebase", "git-reset"},
    "runtime-retention": {"git-worktree"},
    "wakeup-runner": {"git-commit-worker-output", "git-push", "gh-open", "gh-merge", "gh-close-linked", "gh-label-owned"},
    "patrol-inspector": {"gh-open", "gh-edit"},
}

LIFECYCLE_TOKENS = {
    "gh-close",
    "gh-edit",
    "gh-label",
    "gh-merge",
    "gh-open",
    "git-merge",
    "git-push",
    "git-rebase",
    "git-reset",
    "git-worktree",
}


class RuntimeCommandRouterTests(unittest.TestCase):
    # Refactor (iter218/issue-218):
    #   Old pattern: ensure-project-rules 是 public CLI 默认写 host policy 文件($PROJECT_RULES),违反 skill 无 host 改动权边界
    #   New principle: 改为 read-only check-project-rules probe + patch artifact:probe 只读判 sentinel block,非 current 写 .refactor-loop/runs/ patch 并 fail-closed 不派 actor;删 ensure-project-rules/_atomic_write,不引入 PROJECT_RULES_WRITE_ENABLE。严格按 plan 逐条改。
    def test_each_public_operation_is_registered(self) -> None:
        self.assertEqual(
            {
                "check-degradation",
                "check-manifest",
                "check-project-rules",
                "closed-label-reconciler",
                "daemon-status",
                "gh-stats",
                "holistic-status",
                "labels",
                "monitor-bridge-filter",
                "patrol-inspector",
                "concurrency",
                "dev-sync",
                "runtime-retention",
                "spawn-codex",
                "peek",
                "pr-checks",
                "wakeup-plan",
                "wakeup-runner",
                "restart-daemons",
                "statusline",
                "comment-monitor",
                "progress-reporter",
                "phase9-router",
                "release-commits",
                "release-gate",
                "release-required-checks",
                "render-github-body",
                "revive-implements",
                "update-check",
            },
            set(COMMANDS),
        )

    def test_help_imports_lightly_and_does_not_run_legacy_scripts(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CLI), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("consensus-loop controller command router", result.stdout)

    def test_phase9_router_is_compatibility_alias_for_design_consensus(self) -> None:
        self.assertIn("phase9-router", COMMANDS)
        self.assertIn("compatibility alias", COMMANDS["phase9-router"].description)
        self.assertIn("design-consensus router", COMMANDS["phase9-router"].description)

    def test_phase9_router_declares_state_only_read_gh_authority_without_lifecycle_tokens(self) -> None:
        # Refactor (fix/pr245-router-authority-anchor): Old: the CLI authority source did not expose phase9-router's source-OPEN GitHub state read. New: lock the read-gh token while preserving the no-lifecycle daemon boundary.
        self.assertEqual(
            ("read-log", "read-gh", "write-event", "write-artifact"),
            COMMANDS["phase9-router"].authority,
        )
        self.assertFalse(set(COMMANDS["phase9-router"].authority) & LIFECYCLE_TOKENS)

    def test_harness_spawn_intent_writers_do_not_declare_spawn_authority(self) -> None:
        self.assertNotIn("spawn", COMMANDS["concurrency"].authority)
        self.assertNotIn("spawn", COMMANDS["phase9-router"].authority)
        self.assertIn("write-event", COMMANDS["concurrency"].authority)
        self.assertIn("write-event", COMMANDS["phase9-router"].authority)

    def test_closed_label_reconciler_declares_only_closed_reconcile_label_authority(self) -> None:
        self.assertEqual(
            ("read-gh", "gh-label-closed-reconcile", "write-state"),
            COMMANDS["closed-label-reconciler"].authority,
        )
        self.assertNotIn("reconcile-labels", COMMANDS)
        for name, spec in COMMANDS.items():
            with self.subTest(command=name):
                if name in {"closed-label-reconciler", "patrol-inspector"}:
                    continue
                self.assertNotIn("gh-label-closed-reconcile", spec.authority)
                self.assertNotIn("gh-label", spec.authority)
                self.assertNotIn("gh-edit", spec.authority)

    def test_patrol_inspector_declares_only_patrol_issue_intake_authority(self) -> None:
        self.assertEqual(
            ("read-state", "read-log", "read-gh", "gh-open", "gh-edit", "write-state"),
            COMMANDS["patrol-inspector"].authority,
        )
        self.assertFalse({"gh-close", "gh-label", "gh-merge", "git-push"} & set(COMMANDS["patrol-inspector"].authority))

    def test_unknown_command_exits_2(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CLI), "does-not-exist"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("unknown command: does-not-exist", result.stderr)

    def test_registered_commands_are_python_handlers_not_shell_scripts(self) -> None:
        for name, spec in COMMANDS.items():
            with self.subTest(command=name):
                self.assertTrue(callable(spec.handler))
                self.assertTrue(spec.handler.__module__.startswith("codex_refactor_loop"))
                self.assertIsInstance(spec.authority, tuple)

    def test_every_command_declares_closed_authority_tokens(self) -> None:
        for name, spec in COMMANDS.items():
            with self.subTest(command=name):
                self.assertGreater(len(spec.authority), 0)
                self.assertEqual(len(spec.authority), len(set(spec.authority)))
                self.assertTrue(set(spec.authority).issubset(ALL_AUTHORITY_TOKENS), spec.authority)

    def test_read_only_field_was_deleted_from_command_spec(self) -> None:
        field_names = {field.name for field in fields(next(iter(COMMANDS.values())))}
        self.assertNotIn("read_only", field_names)
        for name, spec in COMMANDS.items():
            with self.subTest(command=name):
                self.assertFalse(hasattr(spec, "read_only"))

    def test_read_only_commands_have_only_read_authority(self) -> None:
        for name in {"check-degradation", "check-manifest", "daemon-status", "gh-stats", "holistic-status", "peek", "pr-checks", "release-required-checks", "render-github-body", "statusline", "wakeup-plan"}:
            with self.subTest(command=name):
                self.assertFalse(set(COMMANDS[name].authority) & MUTATION_TOKENS)

    def test_check_degradation_remains_existing_private_read_surface(self) -> None:
        self.assertEqual(("read-source", "read-state"), COMMANDS["check-degradation"].authority)
        forbidden_authority = {
            "read-gh",
            "write-artifact",
            "write-state",
            "spawn",
            "git-push",
            "git-merge",
            "git-reset",
            "gh-open",
            "gh-merge",
            "gh-close",
            "gh-label",
        }
        self.assertFalse(set(COMMANDS["check-degradation"].authority) & forbidden_authority)
        for command in ("check-clean-room", "clean-room-smoke", "host-fixture-smoke"):
            with self.subTest(command=command):
                self.assertNotIn(command, COMMANDS)

    def test_activity_is_not_a_public_command_and_peek_remains_status_lens(self) -> None:
        self.assertNotIn("activity", COMMANDS)
        self.assertIn("peek", COMMANDS)
        self.assertEqual(("read-state", "read-gh"), COMMANDS["peek"].authority)
        self.assertIn("read-only state sweep", COMMANDS["peek"].description)

    def test_holistic_status_is_read_only_shared_projection_command(self) -> None:
        self.assertEqual(("read-state", "read-process", "read-gh"), COMMANDS["holistic-status"].authority)
        self.assertFalse(set(COMMANDS["holistic-status"].authority) & MUTATION_TOKENS)
        source = (SCRIPT_DIR / "codex_refactor_loop" / "cli.py").read_text(encoding="utf-8")
        holistic = (SCRIPT_DIR / "codex_refactor_loop" / "holistic_status.py").read_text(encoding="utf-8")
        self.assertIn('"holistic-status": CommandSpec(', source)
        self.assertIn("render the shared read-only holistic status card", source)
        self.assertIn("class HolisticStatusProjection", holistic)
        for forbidden in ("dashboard-writer", "status-card-writer", "global-status-card", "write-holistic-status"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, COMMANDS)

    def test_daemon_status_is_read_only_status_projection(self) -> None:
        self.assertEqual(("read-state", "read-process"), COMMANDS["daemon-status"].authority)
        self.assertFalse(set(COMMANDS["daemon-status"].authority) & MUTATION_TOKENS)
        cli = (SCRIPT_DIR / "codex_refactor_loop" / "cli.py").read_text(encoding="utf-8")
        daemon_status = (SCRIPT_DIR / "codex_refactor_loop" / "daemon_status.py").read_text(encoding="utf-8")
        for token in (
            '"daemon-status": CommandSpec(',
            "daemon_status_main",
            '("read-state", "read-process")',
            "def collect(",
            "DaemonStatusProjection",
            "DaemonStatusReport",
            "read_daemon_pid",
            "read_heartbeat_age_seconds",
            "duplicate_canonical_wrappers",
        ):
            with self.subTest(token=token):
                self.assertIn(token, cli + daemon_status)
        for forbidden in ("def start(", "def stop(", "def restart(", "def reload(", "spawn-daemon", "write-state"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, daemon_status)

    def test_wakeup_runner_is_only_396_lifecycle_daemon_command(self) -> None:
        self.assertIn("wakeup-runner", COMMANDS)
        self.assertIn("#396", COMMANDS["wakeup-runner"].description)
        self.assertEqual(
            (
                "read-state",
                "read-log",
                "read-gh",
                "read-git",
                "write-artifact",
                "write-event",
                "spawn",
                "git-commit-worker-output",
                "git-push",
                "gh-open",
                "gh-merge",
                "gh-close-linked",
                "gh-label-owned",
                "controller-lifecycle-runner",
            ),
            COMMANDS["wakeup-runner"].authority,
        )
        for forbidden in ("merge-pr", "open-pr", "safe-push", "release-publish", "ControllerCommand", "ControllerOrchestrator", "ControllerTurnDecision"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, COMMANDS)

    def test_daemon_status_cli_json_and_unknown_target_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="daemon-status-cli-") as raw_tmp:
            repo = Path(raw_tmp) / "repo"
            for rel in (".refactor-loop/locks", ".refactor-loop/heartbeats", ".refactor-loop/state"):
                (repo / rel).mkdir(parents=True, exist_ok=True)
            host_env = repo / ".config" / "consensus-rnd" / "host.env"
            host_env.parent.mkdir(parents=True, exist_ok=True)
            host_env.write_text(
                f'export REPO_ROOT="{repo}"\nexport GH_REPO_SLUG="example/repo"\n',
                encoding="utf-8",
            )
            (repo / ".refactor-loop" / "state" / "active-controller-status.json").write_text(
                json.dumps({"active_controller": "owner"}) + "\n",
                encoding="utf-8",
            )
            child_env = os.environ.copy()
            child_env.pop("REPO_ROOT", None)

            result = subprocess.run(
                [sys.executable, str(CLI), "daemon-status", "--json"],
                cwd=repo,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                    "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(str(repo.resolve()), payload["repo_root"])
            self.assertEqual("owner", payload["active_controller"])
            self.assertIn("generated_at", payload)
            self.assertEqual(
                "concurrency_monitor",
                payload["daemons"][0]["name"],
            )
            self.assertEqual("dead", payload["daemons"][0]["status"])
            self.assertIn("duplicate_canonical_wrappers", payload["daemons"][0])

            unknown = subprocess.run(
                [sys.executable, str(CLI), "daemon-status", "not-allowlisted"],
                cwd=repo,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                    "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(2, unknown.returncode)
            self.assertIn("unknown daemon target: not-allowlisted", unknown.stderr)

    def test_daemon_commands_do_not_gain_lifecycle_authority(self) -> None:
        for name in DAEMON_COMMANDS:
            with self.subTest(command=name):
                allowed = DAEMON_LIFECYCLE_CARVEOUTS.get(name, set())
                forbidden = set(COMMANDS[name].authority) & DAEMON_FORBIDDEN_LIFECYCLE_TOKENS
                self.assertFalse(forbidden - allowed)

    def test_dev_sync_declares_integration_worktree_git_carveout(self) -> None:
        self.assertEqual(
            {"git-fetch", "git-worktree", "git-merge", "git-push", "git-rebase", "git-reset"},
            set(COMMANDS["dev-sync"].authority)
            & {"git-fetch", "git-worktree", "git-merge", "git-push", "git-rebase", "git-reset"},
        )

    def test_public_lifecycle_cli_commands_are_removed(self) -> None:
        # Refactor (iter1/issue-322):
        #   Old pattern: ReleasePublisher had commit/push/gh-release authority only in SKILL prose.
        #   New principle: release-publication-322 mirrors exact commands and forbidden lifecycle surfaces.
        for command in {
            "apply-human-label",
            "apply-sync",
            "apply-triage",
            "ensure-project-rules",
            "merge-pr",
            "open-pr",
            "open-release-rollup-pr",
            "safe-push",
            "safe-sync-main",
            "sync-request",
            "release-publish",
            "publish-release",
            "dashboard-writer",
            "global-status-card",
            "write-holistic-status",
            "apply-update",
            "check-update",
            "install-update",
            "update-apply",
        }:
            with self.subTest(command=command):
                self.assertNotIn(command, COMMANDS)

    def test_post_banner_is_not_public_cli_surface(self) -> None:
        self.assertNotIn("post-banner", COMMANDS)
        for name, spec in COMMANDS.items():
            with self.subTest(command=name):
                if "banner" in name:
                    self.assertNotIn("gh-comment", spec.authority)

    def test_update_check_declares_exact_notify_only_authority(self) -> None:
        self.assertEqual(("read-source", "read-gh", "write-state"), COMMANDS["update-check"].authority)
        self.assertFalse(set(COMMANDS["update-check"].authority) & LIFECYCLE_TOKENS)

    def test_monitor_bridge_filter_declares_read_stdin_only_authority(self) -> None:
        self.assertEqual(("read-stdin",), COMMANDS["monitor-bridge-filter"].authority)
        self.assertFalse(set(COMMANDS["monitor-bridge-filter"].authority) & MUTATION_TOKENS)
        self.assertIn("filter daemon-event Monitor bridge stdin", COMMANDS["monitor-bridge-filter"].description)

    def test_runtime_retention_is_canonical_without_log_retention_alias(self) -> None:
        self.assertEqual(("delete-runtime", "git-worktree"), COMMANDS["runtime-retention"].authority)
        self.assertIn("canonical RuntimeRetention", COMMANDS["runtime-retention"].description)
        removed_legacy_command = "log" + "-retention"
        self.assertNotIn(removed_legacy_command, COMMANDS)
        for forbidden in ("read-gh", "gh-close", "gh-edit", "gh-label", "gh-merge", "gh-open", "git-fetch", "git-push", "git-merge", "git-reset", "git-rebase"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, COMMANDS["runtime-retention"].authority)

        result = subprocess.run(
            [sys.executable, str(CLI), removed_legacy_command],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn(f"unknown command: {removed_legacy_command}", result.stderr)

    def test_public_commands_expose_no_generic_lifecycle_authority_tokens(self) -> None:
        for name, spec in COMMANDS.items():
            with self.subTest(command=name):
                lifecycle_tokens = set(spec.authority) & LIFECYCLE_TOKENS
                allowed = DAEMON_LIFECYCLE_CARVEOUTS.get(name, set())
                self.assertFalse(lifecycle_tokens - allowed)

    def test_check_project_rules_cli_declares_read_source_write_artifact_only(self) -> None:
        self.assertIn("check-project-rules", COMMANDS)
        self.assertNotIn("ensure-project-rules", COMMANDS)
        self.assertEqual(("read-source", "write-artifact"), COMMANDS["check-project-rules"].authority)
        for name, spec in COMMANDS.items():
            if "project-rules" in name:
                with self.subTest(command=name):
                    self.assertNotIn("write-source", spec.authority)

    def test_release_commits_command_is_read_git_write_artifact_only(self) -> None:
        self.assertEqual(("read-git", "write-artifact"), COMMANDS["release-commits"].authority)
        forbidden = {
            "read-gh",
            "git-push",
            "git-merge",
            "git-reset",
            "git-rebase",
            "git-worktree",
            "gh-close",
            "gh-edit",
            "gh-label",
            "gh-merge",
            "gh-open",
        }
        self.assertFalse(set(COMMANDS["release-commits"].authority) & forbidden)
        self.assertNotIn("read-git", COMMANDS["release-gate"].authority)

    def test_pr_checks_command_declares_read_gh_only_and_no_lifecycle_authority(self) -> None:
        self.assertEqual(("read-gh",), COMMANDS["pr-checks"].authority)
        self.assertFalse(set(COMMANDS["pr-checks"].authority) & LIFECYCLE_TOKENS)

    def test_authority_refactor_self_doc_source_regression(self) -> None:
        cli = (SCRIPT_DIR / "codex_refactor_loop" / "cli.py").read_text(encoding="utf-8")
        for token in (
            "COMMANDS: dict[str, CommandSpec]",
            "authority: tuple[str, ...]",
            '"dev-sync": CommandSpec(',
            '"release-gate": CommandSpec(',
            '"check-project-rules": CommandSpec(',
            '("read-git", "write-artifact")',
            '"phase9-router": CommandSpec(',
        ):
            with self.subTest(token=token):
                self.assertIn(token, cli)

    def test_single_entrypoint_name_is_checked_in(self) -> None:
        self.assertTrue(CLI.is_file())
        self.assertFalse((SCRIPT_DIR / "codex_loop.py").exists())

    def test_peek_command_uses_registered_python_handler(self) -> None:
        router = RuntimeCommandRouter(script_dir=SCRIPT_DIR)
        handler = mock.Mock(return_value=0)
        with mock.patch.dict("codex_refactor_loop.cli.COMMANDS", {"peek": COMMANDS["peek"].__class__(handler, "peek", ("read-state",)), **{k: v for k, v in COMMANDS.items() if k != "peek"}}):
            self.assertEqual(0, router.run("peek", ["--flag"]))
        handler.assert_called_once_with(["--flag"])

    def test_wakeup_plan_command_uses_registered_python_handler(self) -> None:
        router = RuntimeCommandRouter(script_dir=SCRIPT_DIR)
        handler = mock.Mock(return_value=0)
        with mock.patch.dict("codex_refactor_loop.cli.COMMANDS", {"wakeup-plan": COMMANDS["wakeup-plan"].__class__(handler, "wakeup", ("read-state",)), **{k: v for k, v in COMMANDS.items() if k != "wakeup-plan"}}):
            self.assertEqual(0, router.run("wakeup-plan", ["--repo-root", "/tmp/repo"]))
        handler.assert_called_once_with(["--repo-root", "/tmp/repo"])

    def test_labels_command_forwards_argv_to_read_only_catalog_handler(self) -> None:
        router = RuntimeCommandRouter(script_dir=SCRIPT_DIR)
        handler = mock.Mock(return_value=0)
        with mock.patch.dict("codex_refactor_loop.cli.COMMANDS", {"labels": COMMANDS["labels"].__class__(handler, "labels", ("read-source", "read-gh")), **{k: v for k, v in COMMANDS.items() if k != "labels"}}):
            self.assertEqual(0, router.run("labels", ["check-github", "--plan"]))
        handler.assert_called_once_with(["check-github", "--plan"])
        self.assertEqual(set(COMMANDS["labels"].authority), {"read-source", "read-gh"})

    def test_removed_lifecycle_commands_fail_closed_without_handler_dispatch(self) -> None:
        router = RuntimeCommandRouter(script_dir=SCRIPT_DIR)
        handler = mock.Mock(return_value=0)
        with mock.patch.dict("codex_refactor_loop.cli.COMMANDS", dict(COMMANDS), clear=True):
            for command in ("merge-pr", "apply-sync", "apply-triage", "safe-push"):
                with self.subTest(command=command):
                    self.assertEqual(2, router.run(command, ["123"]))
        handler.assert_not_called()


if __name__ == "__main__":
    unittest.main()
