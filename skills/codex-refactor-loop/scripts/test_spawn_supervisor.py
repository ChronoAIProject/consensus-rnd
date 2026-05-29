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

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.processes import ProcessSupervisor, prompt_file_from_text


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

    def test_unfinished_log_refusal(self) -> None:
        self.log.write_text("SPAWN: still running\n", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "refusing to reuse unfinished log"):
            ProcessSupervisor().supervise(
                [sys.executable, "-c", "print('nope')"],
                stdin=self.prompt,
                log=self.log,
                stall=5,
            )

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
