#!/usr/bin/env python3
"""Behavior tests for spawn-codex claim enforcement."""

from __future__ import annotations

import os
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import spawn
from codex_refactor_loop.task_spawn_claim import TaskSpawnClaimStore


class SpawnClaimIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(tempfile.mkdtemp(prefix="spawn-claim-integration-"))
        self.prompt = self.repo / "prompt.md"
        self.prompt.write_text("prompt\n", encoding="utf-8")
        self.log = self.repo / ".refactor-loop" / "logs" / "implement-issue490.log"
        self.env = {"REPO_ROOT": str(self.repo), "PATH": os.environ.get("PATH", "")}

    def tearDown(self) -> None:
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_spawn_returns_zero_and_does_not_call_supervisor_when_claim_is_held(self) -> None:
        TaskSpawnClaimStore(self.repo).acquire("implement-issue490", log_path=self.log)

        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch(
            "codex_refactor_loop.spawn.ProcessSupervisor"
        ) as supervisor, mock.patch("sys.stderr") as stderr:
            exit_code = spawn.main(
                [
                    "--cd",
                    str(self.repo),
                    "--prompt",
                    str(self.prompt),
                    "--log",
                    str(self.log),
                    "--stall",
                    "5",
                ]
            )

        self.assertEqual(0, exit_code)
        supervisor.assert_not_called()
        stderr_text = "".join(call.args[0] for call in stderr.write.call_args_list)
        diagnostic = json.loads(stderr_text)
        self.assertEqual(
            {
                "lock",
                "log",
                "no_lifecycle_authority",
                "source",
                "task",
                "time",
            },
            set(diagnostic),
        )
        self.assertEqual("SPAWN_CLAIM_HELD", diagnostic["source"])
        self.assertEqual("implement-issue490", diagnostic["task"])
        self.assertEqual(str(self.log), diagnostic["log"])
        self.assertIn(".refactor-loop/locks/spawn-tasks/implement-issue490.lock", diagnostic["lock"])
        self.assertIs(True, diagnostic["no_lifecycle_authority"])
        self.assertNotIn("SPAWN_CLAIM_HELD:task=", stderr_text)

    def test_spawn_acquires_claim_before_supervisor(self) -> None:
        supervisor_instance = mock.Mock()
        supervisor_instance.supervise.return_value = 0

        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch(
            "codex_refactor_loop.spawn.ProcessSupervisor", return_value=supervisor_instance
        ):
            exit_code = spawn.main(
                [
                    "--cd",
                    str(self.repo),
                    "--prompt",
                    str(self.prompt),
                    "--log",
                    str(self.log),
                    "--stall",
                    "5",
                ]
            )

        self.assertEqual(0, exit_code)
        supervisor_instance.supervise.assert_called_once()
        self.assertTrue((self.repo / ".refactor-loop" / "locks" / "spawn-tasks" / "implement-issue490.lock").is_file())

    def test_invalid_task_id_fails_before_supervisor(self) -> None:
        supervisor_instance = mock.Mock()
        unsafe_log = self.repo / ".refactor-loop" / "logs" / "---.log"

        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch(
            "codex_refactor_loop.spawn.ProcessSupervisor", return_value=supervisor_instance
        ):
            exit_code = spawn.main(
                [
                    "--cd",
                    str(self.repo),
                    "--prompt",
                    str(self.prompt),
                    "--log",
                    str(unsafe_log),
                    "--stall",
                    "5",
                ]
            )

        self.assertEqual(2, exit_code)
        supervisor_instance.supervise.assert_not_called()

    def test_log_derived_task_id_keeps_existing_safe_task_names(self) -> None:
        self.assertEqual("phase9-issue490-r4-judge", spawn._task_id_from_log(Path("phase9-issue490-r4-judge.log")))
        self.assertEqual("review-pr490-fix", spawn._task_id_from_log(Path("review-pr490 fix!.log")))
        self.assertEqual("review-pr490-tests-r1", spawn._task_id_from_log(Path("review-pr490-tests-r1.log")))

    def test_claim_held_diagnostic_is_not_wakeup_authorization_or_retry_source(self) -> None:
        scripts = SCRIPT_DIR / "codex_refactor_loop"
        wakeup_plan = (scripts / "wakeup_plan.py").read_text(encoding="utf-8")
        wakeup_runner = (scripts / "wakeup_runner.py").read_text(encoding="utf-8")

        self.assertNotIn("SPAWN_CLAIM_HELD", wakeup_plan)
        self.assertNotIn("SPAWN_CLAIM_HELD", wakeup_runner)


if __name__ == "__main__":
    unittest.main()
