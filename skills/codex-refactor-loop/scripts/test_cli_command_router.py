#!/usr/bin/env python3
"""Behavior tests for the consensus-rnd-cli command router."""

from __future__ import annotations

import subprocess
import sys
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
    "gh-close",
    "gh-comment",
    "gh-edit",
    "gh-label",
    "gh-merge",
    "gh-open",
    "gh-reaction",
    "git-fetch",
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
    "read-state",
    "spawn",
    "spawn-daemon",
    "write-artifact",
    "write-event",
    "write-log",
    "write-source",
    "write-state",
}

MUTATION_TOKENS = {
    token
    for token in ALL_AUTHORITY_TOKENS
    if not token.startswith("read-")
}

DAEMON_COMMANDS = {
    "comment-monitor",
    "concurrency",
    "dev-sync",
    "log-retention",
    "phase9-router",
    "progress-reporter",
    "release-gate",
    "restart-daemons",
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
    "dev-sync": {"git-fetch", "git-worktree"},
}

CONTROLLER_LIFECYCLE_COMMANDS = {
    "apply-human-label",
    "apply-sync",
    "apply-triage",
    "merge-pr",
    "open-pr",
    "open-release-rollup-pr",
    "post-banner",
    "safe-push",
    "safe-sync-main",
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
    def test_each_public_operation_is_registered(self) -> None:
        self.assertEqual(
            {
                "apply-sync",
                "apply-triage",
                "check-degradation",
                "check-manifest",
                "concurrency",
                "dev-sync",
                "ensure-project-rules",
                "log-retention",
                "spawn-codex",
                "peek",
                "wakeup-plan",
                "restart-daemons",
                "statusline",
                "comment-monitor",
                "progress-reporter",
                "phase9-router",
                "post-banner",
                "release-gate",
                "release-required-checks",
                "render-github-body",
                "merge-pr",
                "open-pr",
                "open-release-rollup-pr",
                "apply-human-label",
                "safe-push",
                "safe-sync-main",
                "sync-request",
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
        self.assertIn("codex-refactor-loop controller command router", result.stdout)

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
        for name in {"check-degradation", "check-manifest", "peek", "release-required-checks", "render-github-body", "statusline", "sync-request", "wakeup-plan"}:
            with self.subTest(command=name):
                self.assertFalse(set(COMMANDS[name].authority) & MUTATION_TOKENS)

    def test_daemon_commands_do_not_gain_lifecycle_authority(self) -> None:
        for name in DAEMON_COMMANDS:
            with self.subTest(command=name):
                allowed = DAEMON_LIFECYCLE_CARVEOUTS.get(name, set())
                forbidden = set(COMMANDS[name].authority) & DAEMON_FORBIDDEN_LIFECYCLE_TOKENS
                self.assertFalse(forbidden - allowed)

    def test_dev_sync_declares_integration_worktree_git_carveout(self) -> None:
        self.assertEqual(
            {"git-fetch", "git-worktree"},
            set(COMMANDS["dev-sync"].authority) & {"git-fetch", "git-worktree"},
        )

    def test_handler_live_surfaces_are_declared_in_command_authority(self) -> None:
        handler_surface_requirements = {
            "merge-pr": {
                "source": SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py",
                "needles": ("record_recent_pr_merge", "recent-pr-merges.json"),
                "authority": {"write-state"},
            },
            "apply-triage": {
                "source": SCRIPT_DIR / "codex_refactor_loop" / "triage.py",
                "needles": ('["issue", "view", str(issue_number), "--json", "labels"]',),
                "authority": {"read-gh"},
            },
        }
        for command, requirement in handler_surface_requirements.items():
            with self.subTest(command=command):
                source = requirement["source"].read_text(encoding="utf-8")
                for needle in requirement["needles"]:
                    self.assertIn(needle, source)
                self.assertTrue(requirement["authority"].issubset(COMMANDS[command].authority))

    def test_lifecycle_tokens_stay_on_controller_apply_surfaces(self) -> None:
        for name, spec in COMMANDS.items():
            with self.subTest(command=name):
                lifecycle_tokens = set(spec.authority) & LIFECYCLE_TOKENS
                allowed = DAEMON_LIFECYCLE_CARVEOUTS.get(name, set())
                if lifecycle_tokens - allowed:
                    self.assertIn(name, CONTROLLER_LIFECYCLE_COMMANDS)

    def test_authority_refactor_self_doc_source_regression(self) -> None:
        cli = (SCRIPT_DIR / "codex_refactor_loop" / "cli.py").read_text(encoding="utf-8")
        for token in (
            "Refactor (iter1/issue-166)",
            "Old pattern: CommandSpec exposed a coarse",
            "read_only boolean",
            "New principle:",
            "inline closed-token authority tuple",
            "mechanical CLI authority fact source",
            "dev-sync's integration-worktree carveout",
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

    def test_controller_actions_dispatch_to_python_action_handler(self) -> None:
        router = RuntimeCommandRouter(script_dir=SCRIPT_DIR)
        handler = mock.Mock(return_value=0)
        with mock.patch.dict("codex_refactor_loop.cli.COMMANDS", {"merge-pr": COMMANDS["merge-pr"].__class__(handler, "merge", ("gh-merge",))}):
            self.assertEqual(0, router.run("merge-pr", ["123", "45"]))
        handler.assert_called_once_with(["merge-pr", "123", "45"])


if __name__ == "__main__":
    unittest.main()
