#!/usr/bin/env python3
"""Behavior tests for ProcessSupervisor spawn parity primitives."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.processes import ProcessSupervisor, launch_spawn_codex_supervisor, prompt_file_from_text


class FakeProcess:
    def __init__(self, *, polls_before_exit: int = 1, exit_code: int = 0, pid: int = 12345) -> None:
        self.pid = pid
        self.polls_before_exit = polls_before_exit
        self.exit_code = exit_code
        self.poll_count = 0
        self.wait_count = 0
        self.waited = False

    def poll(self) -> int | None:
        if self.waited:
            return self.exit_code
        self.poll_count += 1
        if self.poll_count > self.polls_before_exit:
            return self.exit_code
        return None

    def wait(self) -> int:
        self.wait_count += 1
        self.waited = True
        return self.exit_code


class SpawnSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="spawn-supervisor-test-"))
        self.prompt = self.tmp_root / "prompt.md"
        self.prompt.write_text("hello\n", encoding="utf-8")
        self.log = self.tmp_root / "codex.log"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_prompt_file_is_passed_to_command_stdin(self) -> None:
        code = "import sys; data=sys.stdin.read(); print('PROMPT=' + data.strip())"
        exit_code = ProcessSupervisor(poll_interval=0.01).supervise(
            [sys.executable, "-c", code],
            stdin=self.prompt,
            log=self.log,
            stall=5,
        )

        text = self.log.read_text(encoding="utf-8")
        self.assertEqual(0, exit_code)
        self.assertIn("PROMPT=hello", text)
        self.assertIn("EXIT=0", text)

    def test_prompt_text_materializes_debuggable_file(self) -> None:
        prompt = prompt_file_from_text("inline prompt")
        try:
            self.assertTrue(prompt.is_file())
            self.assertEqual("inline prompt\n", prompt.read_text(encoding="utf-8"))
        finally:
            prompt.unlink(missing_ok=True)

    def test_unfinished_log_is_rotated_before_respawn(self) -> None:
        self.log.write_text("SPAWN: still running\n", encoding="utf-8")

        exit_code = ProcessSupervisor(poll_interval=0.01).supervise(
            [sys.executable, "-c", "print('respawned')"],
            stdin=self.prompt,
            log=self.log,
            stall=5,
        )

        self.assertEqual(0, exit_code)
        self.assertIn("respawned", self.log.read_text(encoding="utf-8"))
        rotated = list(self.tmp_root.glob("codex.log.unfinished.*"))
        self.assertEqual(1, len(rotated))
        self.assertIn("SPAWN: still running", rotated[0].read_text(encoding="utf-8"))

    def test_quiet_child_exits_without_log_growth_before_total_timeout(self) -> None:
        fake_proc = FakeProcess(polls_before_exit=2, exit_code=0)
        ticks = iter([0.0, 1.0, 2.0])

        with mock.patch("codex_refactor_loop.processes.subprocess.Popen", return_value=fake_proc):
            with mock.patch("codex_refactor_loop.processes.kill_process_group") as kill:
                exit_code = ProcessSupervisor(
                    poll_interval=0.01,
                    clock=lambda: next(ticks),
                    sleeper=lambda _: None,
                ).supervise(
                    [sys.executable, "-c", "unused"],
                    stdin=self.prompt,
                    log=self.log,
                    stall=5,
                )

        self.assertEqual(0, exit_code)
        kill.assert_not_called()
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("EXIT=0", text)
        self.assertNotIn("STALL_KILL_", text)
        self.assertNotIn("TIMEOUT_KILL_", text)

    def test_total_timeout_writes_exit_137_and_kills_process_group(self) -> None:
        fake_proc = FakeProcess(polls_before_exit=20, exit_code=-9)
        ticks = iter([0.0, 0.5, 1.0])

        with mock.patch("codex_refactor_loop.processes.subprocess.Popen", return_value=fake_proc):
            with mock.patch("codex_refactor_loop.processes.kill_process_group") as kill:
                exit_code = ProcessSupervisor(
                    poll_interval=0.01,
                    clock=lambda: next(ticks),
                    sleeper=lambda _: None,
                ).supervise(
                    [sys.executable, "-c", "unused"],
                    stdin=self.prompt,
                    log=self.log,
                    stall=1,
                )

        self.assertEqual(137, exit_code)
        kill.assert_called_once_with(fake_proc.pid)
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("TIMEOUT_KILL_AFTER=1s", text)
        self.assertIn("TIMEOUT_KILL_AT=", text)
        self.assertIn("EXIT=137", text)
        self.assertNotIn("STALL_KILL_", text)

    def test_launch_spawn_codex_supervisor_detaches_without_wait_or_poll(self) -> None:
        repo = self.tmp_root
        skill_root = self.tmp_root / "installed-skill"
        cli = skill_root / "scripts" / "consensus-rnd-cli"
        cli.parent.mkdir(parents=True, exist_ok=True)
        cli.write_text("#!/bin/sh\n", encoding="utf-8")
        fake_proc = mock.Mock()

        with mock.patch("codex_refactor_loop.processes.subprocess.Popen", return_value=fake_proc) as popen:
            exit_code = launch_spawn_codex_supervisor(
                repo_root=repo,
                skill_root=skill_root,
                cd=repo / ".worktrees" / "task",
                prompt=self.prompt,
                log=self.log,
                stall=30,
                env={"REPO_ROOT": str(repo.resolve()), "GH_REPO_SLUG": "owner/repo"},
            )

        self.assertEqual(exit_code, 0)
        popen.assert_called_once()
        args, kwargs = popen.call_args
        command = args[0]
        self.assertIn("spawn-codex", command)
        self.assertEqual(str(cli.resolve()), command[0])
        self.assertNotIn(str(repo / "skills"), command[0])
        self.assertEqual(kwargs["cwd"], str(repo.resolve()))
        self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["env"]["REPO_ROOT"], str(repo.resolve()))
        self.assertEqual(kwargs["env"]["GH_REPO_SLUG"], "owner/repo")
        fake_proc.wait.assert_not_called()
        fake_proc.poll.assert_not_called()

    def test_launch_spawn_codex_supervisor_fails_closed_when_skill_cli_missing(self) -> None:
        repo = self.tmp_root / "host-repo"
        repo.mkdir()
        skill_root = self.tmp_root / "installed-skill"
        skill_root.mkdir()

        with mock.patch("codex_refactor_loop.processes.subprocess.Popen") as popen:
            exit_code = launch_spawn_codex_supervisor(
                repo_root=repo,
                skill_root=skill_root,
                cd=repo,
                prompt=self.prompt,
                log=self.log,
                stall=30,
            )

        self.assertEqual(127, exit_code)
        popen.assert_not_called()
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("SPAWN_SUPERVISOR_CLI_MISSING:", text)
        self.assertIn(str((skill_root / "scripts" / "consensus-rnd-cli").resolve()), text)

    def test_spawn_supervisor_source_preserves_claim_before_process_supervision(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "spawn.py").read_text(encoding="utf-8")
        self.assertIn("TaskSpawnClaimStore(repo_root).acquire(task_id, log_path=log_path)", source)
        self.assertIn("SPAWN_CLAIM_HELD:task=", source)
        self.assertLess(source.index("TaskSpawnClaimStore(repo_root).acquire"), source.index("ProcessSupervisor().supervise"))


if __name__ == "__main__":
    unittest.main()
