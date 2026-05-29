#!/usr/bin/env python3
"""Source contract tests for the #49 anti-stop restart helper."""

from __future__ import annotations

import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__)
SKILL_ROOT = SCRIPT_PATH.parents[1]
SKILL_MD = SKILL_ROOT / "SKILL.md"
RESTART_MODULE = SKILL_ROOT / "scripts" / "codex_refactor_loop" / "restart.py"


class AntiStopRestartHelperContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill = SKILL_MD.read_text(encoding="utf-8")
        self.restart = RESTART_MODULE.read_text(encoding="utf-8")

    def test_skill_contains_named_exception_contract(self) -> None:
        for needle in (
            "## Named runtime exception — anti-stop restart helper(per #49)",
            "Narrow allowlist",
            "singleton wrapper + actor-owned heartbeat lease",
            "actor-loop progress lease",
            "No lifecycle authority",
            "STALE_CONTROLLER",
            "$REPO_ROOT",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.skill)

    def test_scheduler_docs_use_single_cli_entrypoint(self) -> None:
        self.assertIn("consensus-rnd-cli restart-daemons", self.skill)
        self.assertIn("source .refactor-loop/host.env", self.skill)
        self.assertIn("cron/launchd-only", self.skill)

    def test_restart_module_contains_singleton_and_heartbeat_checks(self) -> None:
        for needle in (
            "def _singleton_check_fresh(",
            "def _heartbeat_is_fresh(",
            "RESTART_DAEMON_HEARTBEAT_FILE",
            "RESTART_DAEMON_HEARTBEAT_INTERVAL",
            "pid_alive",
            "restart-daemons.lock",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.restart)

    def test_restart_daemon_allowlist_uses_cli_daemon_commands(self) -> None:
        for name, op in (
            ("concurrency_monitor", "concurrency"),
            ("comment-monitor", "comment-monitor"),
            ("codex-progress-reporter", "progress-reporter"),
            ("dev_sync_daemon", "dev-sync"),
            ("phase9_router_daemon", "phase9-router"),
        ):
            with self.subTest(name=name):
                self.assertIn(name, self.restart)
                self.assertIn('"consensus-rnd-cli"', self.restart)
                self.assertIn(f'"{op}"', self.restart)
        self.assertEqual(5, self.restart.count('"--daemon"'))

    def test_restart_module_has_no_controller_lifecycle_authority(self) -> None:
        for token in ("gh ", "git ", "pr merge", "issue close", "git tag", "gh release"):
            with self.subTest(token=token):
                self.assertNotIn(token, self.restart)


if __name__ == "__main__":
    unittest.main()
