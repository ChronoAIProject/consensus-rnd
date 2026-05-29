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
    # Refactor (iter205/issue-205):
    #   Old pattern: dogfood 实测的运维经验(audit 并行撞 iteration 号、新 role prompt 漏注册 marker contract、review verdict grep log tail 误判、daemon 恢复手 kill)只靠 agent 记忆,没落进 skill 合同与机械验证。
    #   New principle: 把四条经验写回局部合同:SKILL.md 增 #205 反面规则段(audit 同一时刻单 active iteration、新 role prompt 必同步 marker inventory、review verdict 权威源优先 review artifact frontmatter、daemon 恢复只走 restart-daemons);audit.md 渲染后 ITERATION 空则 fail-closed;peek.py 局部优先读 review artifact frontmatter verdict;配套 source-regression + behavior test。不新增跨模块抽象层。
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

    def test_issue205_daemon_recovery_uses_restart_helper_only(self) -> None:
        section_start = self.skill.find("## Dogfood anti-rules(per #205)")
        section_end = self.skill.find("## Wakeup Skeleton", section_start)
        section = self.skill[section_start:section_end]

        for needle in (
            "consensus-rnd-cli restart-daemons",
            "must not hand-kill daemon processes",
            "probe process lists as liveness authority",
            "bypass the restart helper",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)


if __name__ == "__main__":
    unittest.main()
