#!/usr/bin/env python3
"""Source contract tests for the #49 anti-stop restart helper."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__)
SKILL_ROOT = SCRIPT_PATH.parents[1]

SKILL_MD = SKILL_ROOT / "SKILL.md"
RESTART_MODULE = SKILL_ROOT / "scripts" / "codex_refactor_loop" / "restart.py"
RUNTIME_EXCEPTIONS = SKILL_ROOT / "authorizations" / "runtime-exceptions.md"


def restart_source_daemon_commands(source: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return restart_source_assignment(source, "DAEMON_COMMANDS")


def restart_source_assignment(source: str, name: str):
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} assignment missing from restart.py")


class AntiStopRestartHelperContractTests(unittest.TestCase):
    # Refactor (iter205/issue-205):
    #   Old pattern: dogfood 实测的运维经验(audit 并行撞 iteration 号、新 role prompt 漏注册 marker contract、review verdict grep log tail 误判、daemon 恢复手 kill)只靠 agent 记忆,没落进 skill 合同与机械验证。
    #   New principle: 把四条经验写回局部合同:SKILL.md 增 #205 反面规则段(audit 同一时刻单 active iteration、新 role prompt 必同步 marker inventory、review verdict 权威源优先 review artifact frontmatter、daemon 恢复只走 restart-daemons);audit.md 渲染后 ITERATION 空则 fail-closed;peek.py 局部优先读 review artifact frontmatter verdict;配套 source-regression + behavior test。不新增跨模块抽象层。
    def setUp(self) -> None:
        self.skill = SKILL_MD.read_text(encoding="utf-8")
        self.restart = RESTART_MODULE.read_text(encoding="utf-8")
        self.runtime_exceptions = RUNTIME_EXCEPTIONS.read_text(encoding="utf-8")

    def test_skill_contains_named_exception_contract(self) -> None:
        for needle in (
            "## Named runtime exception — anti-stop restart helper(per #49)",
            "Narrow allowlist",
            "singleton wrapper + actor-owned heartbeat lease",
            "self-heal only its own static-allowlist child",
            "helper-private launch fingerprint",
            "DaemonProcessInventory",
            "DaemonInstanceProjection",
            "repo_root plus daemon name plus restart wrapper shape",
            "static allowlist command",
            "managed child command instances",
            "bounded lock-holder evidence",
            "orphan-lock-holders:N",
            ".refactor-loop/locks/<daemon>.fingerprint.json",
            "host_env_path",
            "host_env_sha256",
            "never host.env plaintext",
            "pid alive",
            "fingerprint current",
            "missing/malformed/mismatch fail-closed",
            "actor-loop progress lease",
            "current child has had one heartbeat freshness window",
            "malformed/future heartbeat immediately",
            "after at least one wrapper poll interval",
            "No lifecycle authority",
            "STALE_CONTROLLER",
            "$REPO_ROOT",
            "consensus-rnd-cli runtime-retention",
            "daemon-status",
            "cron/launchd remains mandatory",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.skill)

    def test_scheduler_docs_use_single_cli_entrypoint(self) -> None:
        self.assertIn("consensus-rnd-cli restart-daemons", self.skill)
        self.assertIn("consensus-rnd-cli daemon-status --json", self.skill)
        self.assertIn('source "$CONSENSUS_RND_HOST_ENV"', self.skill)
        self.assertIn("not a runtime fallback", self.skill)
        self.assertIn("does not write, load, unload, or delete cron entries or LaunchAgent plists", self.skill)
        self.assertIn("only loop action is to call the existing checked-in `consensus-rnd-cli restart-daemons` helper", self.skill)
        self.assertIn("launchctl bootstrap gui/$(id -u)", self.skill)
        self.assertIn("launchctl bootout gui/$(id -u)", self.skill)
        self.assertIn("Do not add a second watchdog or installer", self.skill)
        self.assertIn("scheduler-backed anti-stop surface", self.skill)
        self.assertIn("mandatory cron/launchd outer repair surface", self.skill)

    def test_restart_module_contains_singleton_and_heartbeat_checks(self) -> None:
        for needle in (
            "def _singleton_check_fresh(",
            "def _heartbeat_is_fresh(",
            "DaemonLaunchFingerprint",
            "DaemonTarget",
            "DaemonProcessInventory",
            "DaemonInstanceProjection",
            "RestartWrapperShape",
            "ManagedChildCommandShape",
            "live_restart_wrappers",
            "canonical_child_pids",
            "orphan_child_pids",
            "live_managed_children",
            "bounded_lock_holder_pids",
            "daemon_lock_files",
            "daemon_targets",
            "read_daemon_pid",
            "read_heartbeat_age_seconds",
            "read_heartbeat_status",
            "DaemonHeartbeatStatus",
            "_run_restart_wrapper",
            "child exited exit=",
            "terminating child and restarting same command",
            "child_spawned_at",
            "_heartbeat_failure_has_generation_grace",
            ".fingerprint.json",
            "package_tree_sha256",
            "entrypoint_sha256",
            "host_env_path",
            "host_env_sha256",
            "RESTART_DAEMON_HEARTBEAT_FILE",
            "RESTART_DAEMON_HEARTBEAT_INTERVAL",
            "pid_alive",
            "restart-daemons.lock",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.restart)

    def test_restart_daemon_allowlist_uses_cli_daemon_commands(self) -> None:
        # Fix (remote-ci/contract-tests): parse the checked-in restart source
        # so unittest discovery cannot inherit a patched runtime allowlist.
        daemon_commands = restart_source_daemon_commands(self.restart)
        for name, command in daemon_commands:
            with self.subTest(name=name):
                self.assertIn(name, self.restart)
                self.assertIn('"consensus-rnd-cli"', self.restart)
                self.assertIn(f'"{command[2]}"', self.restart)

        # The opt-in supervisor command also uses --daemon, but it must not
        # become part of the canonical legacy daemon allowlist.
        supervisor_command = restart_source_assignment(self.restart, "SUPERVISOR_DAEMON_COMMAND")
        self.assertEqual(7, len(daemon_commands))
        self.assertNotIn("patrol_inspector_daemon", {name for name, _command in daemon_commands})
        self.assertNotIn(supervisor_command[0], {name for name, _command in daemon_commands})
        self.assertEqual(len(daemon_commands), sum(command.count("--daemon") for _name, command in daemon_commands))
        self.assertIn("--daemon", supervisor_command[1])

    def test_restart_module_has_no_controller_lifecycle_authority(self) -> None:
        for token in ("gh ", "git ", "pr merge", "issue close", "git tag", "gh release"):
            with self.subTest(token=token):
                self.assertNotIn(token, self.restart)

    def test_issue205_daemon_recovery_uses_restart_helper_only(self) -> None:
        section_start = self.skill.find("## Dogfood anti-rules(per #205)")
        section_end = self.skill.find("## Wakeup Skeleton", section_start)
        section = self.skill[section_start:section_end]

        for needle in (
            "consensus-rnd-cli daemon-status --json",
            "consensus-rnd-cli restart-daemons",
            "read daemon state",
            "repair/reload",
            "must not hand-kill daemon processes",
            "probe process lists as liveness authority",
            "bypass the restart helper",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)

    def test_runtime_exception_mirror_mentions_launch_fingerprint_contract(self) -> None:
        for needle in (
            "## anti-stop-restart-helper-49",
            "helper-private launch fingerprints",
            ".refactor-loop/locks/<daemon>.fingerprint.json",
            "pid alive plus fresh heartbeat plus current fingerprint plus zero duplicate canonical live wrapper",
            "repo_root plus daemon name plus restart wrapper shape",
            "same resolved static allowlist command",
            "read-only daemon-status projection",
            "repair/reload remains restart-daemons",
            "self-heal only its own static-allowlist child",
            "Malformed/future actor heartbeat fails closed immediately",
            "missing/stale numeric heartbeat is measured against the current child generation spawn age",
            "after at least one wrapper poll interval",
            "Cron/launchd remains mandatory",
            "runs canonical RuntimeRetention before daemon freshness checks",
            "no host-defined daemon registry",
            "generic process supervisor",
            "GitHub/git lifecycle authority",
            "missing, malformed, or mismatched fingerprint data fails closed",
            "test_restart_daemons.py",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.runtime_exceptions)


if __name__ == "__main__":
    unittest.main()
