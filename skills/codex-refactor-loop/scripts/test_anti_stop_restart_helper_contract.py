#!/usr/bin/env python3
"""Source contract tests for the #49 anti-stop restart helper."""

from __future__ import annotations

import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__)
SKILL_ROOT = SCRIPT_PATH.parents[1]
SKILL_MD = SKILL_ROOT / "SKILL.md"
RESTART_HELPER = SKILL_ROOT / "scripts" / "restart-daemons.sh"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class AntiStopRestartHelperContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill = read(SKILL_MD)
        self.helper = read(RESTART_HELPER)

    def test_skill_contains_named_exception_contract(self) -> None:
        required = (
            "## Named runtime exception — anti-stop restart helper(per #49)",
            "Narrow allowlist",
            "singleton+heartbeat wrapper lifecycle",
            "No lifecycle authority",
            "STALE_CONTROLLER",
            "Host-agnostic",
            "$REPO_ROOT",
            "Behavior tests",
            "Source-regression",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, self.skill)

    def test_skill_documents_cron_launchd_install(self) -> None:
        required = (
            "cron/launchd install",
            "*/5 * * * * cd $REPO_ROOT && bash skills/codex-refactor-loop/scripts/restart-daemons.sh >> .refactor-loop/logs/restart-cron.log 2>&1",
            "launchd",
            "StartInterval",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, self.skill)

    def test_skill_references_issue49_r3_judge_artifact(self) -> None:
        self.assertIn(
            ".refactor-loop/runs/phase9-issue49-r3-judge.md",
            self.skill,
        )
        self.assertIn(
            "META_JUDGE_DONE:consensus:A-cron-only-with-pending-event-alert",
            self.skill,
        )

    def test_wakeup_skeleton_has_anti_stop_step_12(self) -> None:
        wakeup = self.skill.split("## Wakeup Skeleton", 1)[1].split("## Phase Index", 1)[0]
        self.assertIn("3.", wakeup)
        self.assertLess(
            wakeup.index("restart-daemons.sh"),
            wakeup.index("Sweep GitHub comments and pending events"),
        )
        self.assertLess(
            wakeup.index("restart-daemons.sh"),
            wakeup.index("Parse verdict markers"),
        )
        self.assertLess(
            wakeup.index("restart-daemons.sh"),
            wakeup.index("concurrency floor"),
        )
        self.assertLess(
            wakeup.index("restart-daemons.sh"),
            wakeup.index("Spawn the next codexes"),
        )
        self.assertIn(".refactor-loop/heartbeats/*.ts", wakeup)
        self.assertIn(">90s", wakeup)
        self.assertIn("stale/missing/malformed", wakeup)
        self.assertIn("restart-daemons.sh", wakeup)
        self.assertIn("无 progress >10 min", wakeup)
        self.assertIn(".refactor-loop/runs/", wakeup)
        self.assertIn(".refactor-loop/logs/", wakeup)
        self.assertIn("STALE_CONTROLLER:freeze_minutes=N", wakeup)
        self.assertIn(".controller-pending-events.log", wakeup)
        self.assertIn("no lifecycle authority", wakeup)

    def test_restart_helper_contains_singleton_and_heartbeat_checks(self) -> None:
        required = (
            "singleton_check_fresh",
            "heartbeat_is_fresh",
            "HEARTBEAT_FRESH_SECONDS",
            ".refactor-loop/locks/${name}.pid",
            ".refactor-loop/heartbeats/${name}.ts",
            "kill -0",
            "mkdir \"$RESTART_LOCK_DIR\"",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, self.helper)

    def test_restart_helper_is_host_agnostic_and_no_lifecycle_authority(self) -> None:
        forbidden = (
            "/Users/",
            "gh ",
            "git ",
            "spawn-codex",
            "commit",
            "push",
            "merge",
            "label",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, self.helper)
        self.assertIn('cd "$REPO_ROOT"', self.helper)

    def test_restart_helper_avoids_issue49_forbidden_runtime_sources(self) -> None:
        forbidden = (
            "ps ",
            "ps\t",
            "pgrep",
            "AntiStopWatchdogV1",
            "controller_watchdog.py",
            "spawned registry",
            "codex count",
            "concurrency-monitor",
            "dev-sync-daemon",
            "triage_monitor",
        )
        anti_stop = self.skill.split(
            "## Anti-stop restart helper cron/launchd install(per #49)",
            1,
        )[1].split("## Wakeup Skeleton", 1)[0]
        combined = f"{anti_stop}\n{self.helper}"
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, combined)
        required = (
            "concurrency_monitor",
            "comment-monitor",
            "codex-progress-reporter",
            "dev_sync_daemon",
            "phase9_router_daemon",
        )
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, self.helper)


if __name__ == "__main__":
    unittest.main()
