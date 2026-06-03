#!/usr/bin/env python3
"""Behavior tests for ProcessSupervisor spawn parity primitives."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.processes import ProcessSupervisor, launch_spawn_codex_supervisor, prompt_file_from_text


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

    def test_stall_writes_exit_137_and_kills_process_group(self) -> None:
        marker = self.tmp_root / "child.pid"
        code = (
            "import os, subprocess, sys, time\n"
            f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"open({str(marker)!r}, 'w').write(str(p.pid))\n"
            "sys.stdout.write('started\\n'); sys.stdout.flush()\n"
            "time.sleep(60)\n"
        )

        exit_code = ProcessSupervisor(poll_interval=0.05).supervise(
            [sys.executable, "-c", code],
            stdin=self.prompt,
            log=self.log,
            stall=1,
        )

        self.assertEqual(137, exit_code)
        text = self.log.read_text(encoding="utf-8")
        self.assertIn("STALL_KILL_AFTER=1s", text)
        self.assertIn("EXIT=137", text)
        child_pid = int(marker.read_text(encoding="utf-8"))
        self.assert_process_dead(child_pid)

    def test_launch_spawn_codex_supervisor_detaches_without_wait_or_poll(self) -> None:
        repo = self.tmp_root
        cli = repo / "skills" / "codex-refactor-loop" / "scripts" / "consensus-rnd-cli"
        cli.parent.mkdir(parents=True, exist_ok=True)
        cli.write_text("#!/bin/sh\n", encoding="utf-8")
        fake_proc = mock.Mock()

        with mock.patch("codex_refactor_loop.processes.subprocess.Popen", return_value=fake_proc) as popen:
            exit_code = launch_spawn_codex_supervisor(
                repo_root=repo,
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
        self.assertIn(str(repo.resolve()), command[0])
        self.assertEqual(kwargs["cwd"], str(repo.resolve()))
        self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["env"]["REPO_ROOT"], str(repo.resolve()))
        self.assertEqual(kwargs["env"]["GH_REPO_SLUG"], "owner/repo")
        fake_proc.wait.assert_not_called()
        fake_proc.poll.assert_not_called()

    def assert_process_dead(self, pid: int) -> None:
        deadline = time.time() + 3
        while time.time() < deadline:
            if not self.pid_alive(pid):
                return
            time.sleep(0.05)
        self.fail(f"process still alive after process-group kill: {pid}")

    @staticmethod
    def pid_alive(pid: int) -> bool:
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False)
        if state.returncode != 0:
            return False
        if state.stdout.strip().startswith("Z"):
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


if __name__ == "__main__":
    unittest.main()
