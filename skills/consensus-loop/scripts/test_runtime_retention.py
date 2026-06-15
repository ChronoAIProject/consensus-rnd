#!/usr/bin/env python3
"""Behavior and source-regression tests for RuntimeRetention."""

from __future__ import annotations

import json
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Callable, Sequence
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
CLI = SCRIPT_DIR / "consensus-rnd-cli"

sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.active_controller import LeaseDecision
from codex_refactor_loop import runtime_retention
from codex_refactor_loop.runtime_retention import main as runtime_retention_main, retain_runtime


class RuntimeRetentionBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="runtime-retention-test-"))
        self.repo = self.tmp_root / "repo"
        self.refactor_loop = self.repo / ".refactor-loop"
        for rel in ("logs", "prompts", "runs", "state"):
            (self.refactor_loop / rel).mkdir(parents=True)
        (self.repo / ".worktrees").mkdir(parents=True)
        self.host_env_rel = Path(".config/consensus-rnd/host.env")
        self.host_env = self.repo / self.host_env_rel
        self.host_env.parent.mkdir(parents=True)
        self.host_env.write_text(
            f'export REPO_ROOT="{self.repo}"\nexport RUNTIME_RETENTION_ENABLE="true"\n',
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def write_file(self, rel: str, text: str, age_hours: float) -> Path:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        ts = time.time() - age_hours * 60 * 60
        os.utime(path, (ts, ts))
        return path

    def run_cli(self, command: str = "runtime-retention") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), command],
            cwd=self.repo,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
                "CONSENSUS_RND_HOST_ENV": self.host_env_rel.as_posix(),
            },
            capture_output=True,
            text=True,
            check=False,
        )

    def write_stale_worktree_plan(self, name: str = "iter1-issue-1") -> Path:
        stale = self.repo / ".worktrees" / name
        stale.mkdir(parents=True, exist_ok=True)
        plan = {
            "kind": "RuntimeRetentionPlan",
            "stale_worktrees": [
                {
                    "path": f".worktrees/{name}",
                    "eligible": True,
                    "proof": {
                        "no_in_flight": True,
                        "no_open_issue_or_pr": True,
                        "no_dirty": True,
                        "no_local_ahead": True,
                        "merged_or_missing_safe": True,
                    },
                },
            ],
        }
        (self.refactor_loop / "state" / "runtime-retention-plan.json").write_text(json.dumps(plan), encoding="utf-8")
        return stale

    def write_plan(self, plan: object) -> None:
        (self.refactor_loop / "state" / "runtime-retention-plan.json").write_text(json.dumps(plan), encoding="utf-8")

    def legacy_generated_file_plan_item(
        self,
        rel: str,
        *,
        eligible: bool = True,
        proof_overrides: dict[str, object] | None = None,
    ) -> dict[str, object]:
        proof: dict[str, object] = {}
        if proof_overrides:
            proof.update(proof_overrides)
        return {"path": rel, "eligible": eligible, "proof": proof}

    def write_generated_files_plan(self, *items: object) -> None:
        self.write_plan({"kind": "RuntimeRetentionPlan", "generated_files": list(items)})

    def write_spawn_task_lock(
        self,
        task_id: str,
        *,
        log_path: Path | None = None,
        age_hours: float = 25,
        payload_overrides: dict[str, object] | None = None,
        basename: str | None = None,
    ) -> Path:
        lock_dir = self.refactor_loop / "locks" / "spawn-tasks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        safe_name = basename or f"{task_id}.lock"
        path = lock_dir / safe_name
        payload: dict[str, object] = {
            "task_id": task_id,
            "log_path": str((log_path or (self.refactor_loop / "logs" / f"{task_id}.log")).resolve()),
            "pid": 123456,
            "acquired_at": "2026-06-06T00:00:00Z",
        }
        if payload_overrides:
            payload.update(payload_overrides)
        path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        ts = time.time() - age_hours * 60 * 60
        os.utime(path, (ts, ts))
        return path

    def git_recheck_runner(
        self,
        commands: list[tuple[str, ...]],
        *,
        status_stdout: str = "",
        status_returncode: int = 0,
        status_stderr: str = "",
        ahead_stdout: str = "0\n",
        ahead_returncode: int = 0,
        ahead_stderr: str = "",
        remove_returncode: int = 0,
        remove_stderr: str = "",
        prune_returncode: int = 0,
        prune_stderr: str = "",
    ) -> Callable[[Sequence[str]], subprocess.CompletedProcess[str]]:
        def runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            commands.append(tuple(command))
            if tuple(command[3:5]) == ("status", "--porcelain"):
                return subprocess.CompletedProcess(command, status_returncode, status_stdout, status_stderr)
            if tuple(command[3:6]) == ("rev-list", "--count", "@{upstream}..HEAD"):
                return subprocess.CompletedProcess(command, ahead_returncode, ahead_stdout, ahead_stderr)
            if tuple(command[3:5]) == ("worktree", "remove"):
                return subprocess.CompletedProcess(command, remove_returncode, "", remove_stderr)
            if tuple(command[3:5]) == ("worktree", "prune"):
                return subprocess.CompletedProcess(command, prune_returncode, "", prune_stderr)
            return subprocess.CompletedProcess(command, 0, "", "")

        return runner

    def test_default_disabled_noops_even_when_old_files_exist(self) -> None:
        self.host_env.write_text(
            f'export REPO_ROOT="{self.repo}"\nexport RUNTIME_RETENTION_ENABLE="false"\n',
            encoding="utf-8",
        )
        old_log = self.write_file(".refactor-loop/logs/old.log", "done\nEXIT=0\n", 25)

        result = self.run_cli()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("runtime_retention: enabled=false", result.stdout)
        self.assertTrue(old_log.exists())

    def test_non_owner_noops_without_file_delete_or_worktree_remove(self) -> None:
        old_log = self.write_file(".refactor-loop/logs/old.log", "done\nEXIT=0\n", 25)
        stale = self.write_stale_worktree_plan()
        decision = LeaseDecision(False, "not-owner", "runtime-retention", "other-device")
        output = io.StringIO()

        with (
            mock.patch("codex_refactor_loop.runtime_retention.os.getcwd", return_value=str(self.repo)),
            mock.patch.dict(
                os.environ,
                {"CONSENSUS_RND_HOST_ENV": self.host_env_rel.as_posix(), "REPO_ROOT": ""},
                clear=False,
            ),
            mock.patch("codex_refactor_loop.runtime_retention.require_active_controller", return_value=decision),
            mock.patch(
                "codex_refactor_loop.runtime_retention._run_git",
                side_effect=AssertionError("non-owner must not run git worktree commands"),
            ),
            redirect_stdout(output),
        ):
            returncode = runtime_retention_main([])

        self.assertEqual(0, returncode)
        self.assertIn("runtime_retention: enabled=false active_controller=noop:not-owner owner=other-device", output.getvalue())
        self.assertTrue(old_log.exists())
        self.assertTrue(stale.exists())

    def test_old_logs_prompts_and_run_artifacts_survive_retention(self) -> None:
        old_log = self.write_file(".refactor-loop/logs/old.log", "complete\nDONE_OK:real\nEXIT=0\n", 25)
        old_prompt = self.write_file(".refactor-loop/prompts/old.md", "prompt\n", 25)
        old_run = self.write_file(".refactor-loop/runs/old.json", "{}\n", 25)
        young_log = self.write_file(".refactor-loop/logs/young.log", "done\n", 1)
        state_artifact = self.write_file(".refactor-loop/state/old.json", "keep\n", 25)

        result = retain_runtime(self.repo, enabled=True)

        self.assertEqual((result.deleted, result.kept, result.missing), (0, 0, False))
        self.assertEqual(result.target.resolve(), self.refactor_loop.resolve())
        for path in (old_log, old_prompt, old_run, young_log, state_artifact):
            with self.subTest(path=path):
                self.assertTrue(path.exists())
        self.assertFalse((self.refactor_loop / "state" / "runtime-retention-plan.json").exists())

    def test_retention_keeps_worker_artifacts_while_consuming_stale_worktree_plan(self) -> None:
        old_log = self.write_file(".refactor-loop/logs/old.log", "done\n", 25)
        stale = self.write_stale_worktree_plan()
        commands: list[tuple[str, ...]] = []

        result = retain_runtime(self.repo, enabled=True, command_runner=self.git_recheck_runner(commands))

        self.assertEqual((result.deleted, result.kept), (0, 0))
        self.assertEqual(1, result.removed_worktrees)
        self.assertTrue(result.pruned_worktrees)
        self.assertTrue(old_log.exists())
        self.assertIn(("git", "-C", str(self.repo.resolve()), "worktree", "remove", str(stale.resolve())), commands)

    def test_legacy_generated_files_plan_entries_are_inert(self) -> None:
        pending_prompt = self.write_file(".refactor-loop/prompts/pending-ref.md", "prompt\n", 25)
        markerless_log = self.write_file(".refactor-loop/logs/implement-issue-8.log", "done without marker\nEXIT=0\n", 25)
        run_json = self.write_file(".refactor-loop/runs/old.json", "{}\n", 25)
        self.write_generated_files_plan(
            self.legacy_generated_file_plan_item(".refactor-loop/prompts/pending-ref.md"),
            self.legacy_generated_file_plan_item(".refactor-loop/logs/implement-issue-8.log"),
            self.legacy_generated_file_plan_item(".refactor-loop/runs/old.json"),
        )

        result = retain_runtime(self.repo, enabled=True)

        self.assertEqual((result.deleted, result.kept), (0, 3))
        for path in (pending_prompt, markerless_log, run_json):
            with self.subTest(path=path):
                self.assertTrue(path.exists())
        self.assertTrue(any("reason=legacy_generated_files_ignored" in diagnostic for diagnostic in result.diagnostics), result.diagnostics)

    def test_completed_spawn_task_lock_with_repo_log_exit_marker_is_removed(self) -> None:
        log = self.write_file(".refactor-loop/logs/implement-issue879.log", "worker output\nEXIT=0\n", 25)
        lock = self.write_spawn_task_lock("implement-issue879", log_path=log)

        result = retain_runtime(self.repo, enabled=True)

        self.assertEqual(1, result.removed_spawn_task_locks)
        self.assertEqual(1, result.deleted)
        self.assertFalse(lock.exists())
        self.assertTrue(log.exists())
        self.assertFalse(any("spawn_task_lock_" in diagnostic for diagnostic in result.diagnostics), result.diagnostics)

    def test_spawn_task_lock_cleanup_does_not_use_plan_or_delete_worker_artifacts(self) -> None:
        log = self.write_file(".refactor-loop/logs/implement-issue879.log", "worker output\nEXIT=1\n", 25)
        lock = self.write_spawn_task_lock("implement-issue879", log_path=log)
        prompt = self.write_file(".refactor-loop/prompts/implement-issue879.md", "prompt\n", 25)
        run = self.write_file(".refactor-loop/runs/implement-issue879.md", "summary\n", 25)

        result = retain_runtime(self.repo, enabled=True)

        self.assertEqual(1, result.removed_spawn_task_locks)
        self.assertFalse(lock.exists())
        self.assertTrue(log.exists())
        self.assertTrue(prompt.exists())
        self.assertTrue(run.exists())

    def test_spawn_task_lock_cleanup_keeps_missing_log_even_with_companion_artifact(self) -> None:
        runs = self.refactor_loop / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        (runs / "implement-issue879.md").write_text("IMPLEMENT_DONE:issue-879:ok\n", encoding="utf-8")
        log = self.refactor_loop / "logs" / "implement-issue879.log"
        lock = self.write_spawn_task_lock("implement-issue879", log_path=log)

        result = retain_runtime(self.repo, enabled=True)

        self.assertEqual(0, result.removed_spawn_task_locks)
        self.assertTrue(lock.exists())
        self.assertTrue(
            any("reason=spawn_task_lock_log_missing" in diagnostic for diagnostic in result.diagnostics),
            result.diagnostics,
        )

    def test_spawn_task_lock_cleanup_keeps_unsafe_cases_with_diagnostics(self) -> None:
        inside_log = self.write_file(".refactor-loop/logs/inside.log", "done\nEXIT=0\n", 25)
        outside_log = self.write_file("outside.log", "done\nEXIT=0\n", 25)
        non_terminal_log = self.write_file(".refactor-loop/logs/non-terminal.log", "still running\n", 25)
        cases: list[tuple[str, str, dict[str, object]]] = [
            (
                "unsafe-basename",
                "spawn_task_lock_unsafe_basename",
                {"task_id": "bad", "basename": "bad name.lock", "log_path": inside_log},
            ),
            (
                "young",
                "spawn_task_lock_young",
                {"task_id": "young", "age_hours": 1, "log_path": inside_log},
            ),
            (
                "malformed",
                "spawn_task_lock_malformed",
                {"task_id": "malformed", "payload_overrides": {"pid": "not-int"}, "log_path": inside_log},
            ),
            (
                "basename-mismatch",
                "spawn_task_lock_basename_mismatch",
                {"task_id": "claimed", "basename": "other.lock", "log_path": inside_log},
            ),
            (
                "relative-log",
                "spawn_task_lock_log_path_relative",
                {"task_id": "relative-log", "payload_overrides": {"log_path": ".refactor-loop/logs/inside.log"}},
            ),
            (
                "escaped-log",
                "spawn_task_lock_log_path_escaped",
                {"task_id": "escaped-log", "log_path": outside_log},
            ),
            (
                "missing-log",
                "spawn_task_lock_log_missing",
                {"task_id": "missing-log", "log_path": self.refactor_loop / "logs" / "missing.log"},
            ),
            (
                "not-terminal",
                "spawn_task_lock_log_not_terminal",
                {"task_id": "not-terminal", "log_path": non_terminal_log},
            ),
        ]
        locks = []
        for name, _reason, kwargs in cases:
            with self.subTest(create=name):
                locks.append(self.write_spawn_task_lock(**kwargs))
        symlink = self.refactor_loop / "locks" / "spawn-tasks" / "symlink.lock"
        symlink.symlink_to(inside_log)

        result = retain_runtime(self.repo, enabled=True)

        self.assertEqual(0, result.removed_spawn_task_locks)
        for lock in locks + [symlink]:
            with self.subTest(lock=lock):
                self.assertTrue(lock.exists())
        for _name, reason, _kwargs in cases:
            with self.subTest(reason=reason):
                self.assertTrue(any(f"reason={reason}" in diagnostic for diagnostic in result.diagnostics), result.diagnostics)
        self.assertTrue(any("reason=spawn_task_lock_non_regular" in diagnostic for diagnostic in result.diagnostics), result.diagnostics)

    def test_spawn_task_lock_unlink_failure_keeps_diagnostic(self) -> None:
        log = self.write_file(".refactor-loop/logs/implement-issue879.log", "done\nEXIT=0\n", 25)
        lock = self.write_spawn_task_lock("implement-issue879", log_path=log)

        with mock.patch("codex_refactor_loop.runtime_retention._unlink_spawn_task_lock", side_effect=OSError("denied")):
            result = retain_runtime(self.repo, enabled=True)

        self.assertEqual(0, result.removed_spawn_task_locks)
        self.assertTrue(lock.exists())
        self.assertTrue(
            any("reason=spawn_task_lock_unlink_failed" in diagnostic and "denied" in diagnostic for diagnostic in result.diagnostics),
            result.diagnostics,
        )

    def test_pending_events_compaction_preserves_same_inode_tail(self) -> None:
        pending = self.refactor_loop / ".controller-pending-events.log"
        pending.write_text("".join(f"event-{index}\n" for index in range(2500)), encoding="utf-8")
        inode_before = pending.stat().st_ino

        result = retain_runtime(self.repo, enabled=True)

        self.assertTrue(result.compacted_events)
        self.assertEqual(inode_before, pending.stat().st_ino)
        lines = pending.read_text(encoding="utf-8").splitlines()
        self.assertEqual(2000, len(lines))
        self.assertEqual("event-500", lines[0])
        self.assertEqual("event-2499", lines[-1])

    def test_concurrency_alert_compaction_preserves_same_inode_tail(self) -> None:
        pending = self.refactor_loop / ".controller-pending-events.log"
        alert = self.refactor_loop / ".concurrency-alert.log"
        pending.write_text("".join(f"event-{index}\n" for index in range(2500)), encoding="utf-8")
        alert.write_text("".join(f"alert-{index}\n" for index in range(2500)), encoding="utf-8")
        pending_inode_before = pending.stat().st_ino
        alert_inode_before = alert.stat().st_ino

        result = retain_runtime(self.repo, enabled=True)

        self.assertTrue(result.compacted_events)
        self.assertEqual(pending_inode_before, pending.stat().st_ino)
        self.assertEqual(alert_inode_before, alert.stat().st_ino)
        pending_lines = pending.read_text(encoding="utf-8").splitlines()
        alert_lines = alert.read_text(encoding="utf-8").splitlines()
        self.assertEqual(2000, len(pending_lines))
        self.assertEqual(2000, len(alert_lines))
        self.assertEqual("event-500", pending_lines[0])
        self.assertEqual("event-2499", pending_lines[-1])
        self.assertEqual("alert-500", alert_lines[0])
        self.assertEqual("alert-2499", alert_lines[-1])

    def test_stale_worktree_requires_planner_proof_and_git_recheck(self) -> None:
        stale = self.repo / ".worktrees" / "iter1-issue-1"
        stale.mkdir(parents=True)
        plan = {
            "kind": "RuntimeRetentionPlan",
            "stale_worktrees": [
                {
                    "path": ".worktrees/iter1-issue-1",
                    "eligible": True,
                    "proof": {
                        "no_in_flight": True,
                        "no_open_issue_or_pr": True,
                        "no_dirty": True,
                        "no_local_ahead": True,
                        "merged_or_missing_safe": True,
                    },
                },
                {
                    "path": ".worktrees/iter2-issue-2",
                    "eligible": True,
                    "proof": {
                        "no_in_flight": False,
                        "no_open_issue_or_pr": True,
                        "no_dirty": True,
                        "no_local_ahead": True,
                        "merged_or_missing_safe": True,
                    },
                },
            ],
        }
        (self.refactor_loop / "state" / "runtime-retention-plan.json").write_text(json.dumps(plan), encoding="utf-8")
        commands: list[tuple[str, ...]] = []

        result = retain_runtime(self.repo, enabled=True, command_runner=self.git_recheck_runner(commands))

        self.assertEqual(1, result.removed_worktrees)
        self.assertTrue(result.pruned_worktrees)
        self.assertIn(("git", "-C", str(self.repo.resolve()), "worktree", "remove", str(stale.resolve())), commands)
        self.assertIn(("git", "-C", str(self.repo.resolve()), "worktree", "prune"), commands)
        self.assertFalse(any(".worktrees/iter2-issue-2" in " ".join(command) for command in commands))

    def test_stale_worktree_dirty_git_recheck_refuses_remove_and_prune(self) -> None:
        self.write_stale_worktree_plan()
        commands: list[tuple[str, ...]] = []

        result = retain_runtime(self.repo, enabled=True, command_runner=self.git_recheck_runner(commands, status_stdout=" M changed.txt\n"))

        self.assertEqual(0, result.removed_worktrees)
        self.assertFalse(result.pruned_worktrees)
        self.assertFalse(any(command[3:5] == ("worktree", "remove") for command in commands))
        self.assertFalse(any(command[3:5] == ("worktree", "prune") for command in commands))

    def test_stale_worktree_local_ahead_git_recheck_refuses_remove_and_prune(self) -> None:
        self.write_stale_worktree_plan()
        commands: list[tuple[str, ...]] = []

        result = retain_runtime(self.repo, enabled=True, command_runner=self.git_recheck_runner(commands, ahead_stdout="2\n"))

        self.assertEqual(0, result.removed_worktrees)
        self.assertFalse(result.pruned_worktrees)
        self.assertFalse(any(command[3:5] == ("worktree", "remove") for command in commands))
        self.assertFalse(any(command[3:5] == ("worktree", "prune") for command in commands))

    def test_stale_worktree_failed_git_recheck_refuses_remove_and_prune(self) -> None:
        for failing_command in ("status", "rev-list"):
            with self.subTest(failing_command=failing_command):
                self.write_stale_worktree_plan()
                commands: list[tuple[str, ...]] = []
                result = retain_runtime(
                    self.repo,
                    enabled=True,
                    command_runner=self.git_recheck_runner(
                        commands,
                        status_returncode=1 if failing_command == "status" else 0,
                        status_stderr="fatal\n",
                        ahead_returncode=1 if failing_command == "rev-list" else 0,
                        ahead_stderr="fatal\n",
                    ),
                )

                self.assertEqual(0, result.removed_worktrees)
                self.assertFalse(result.pruned_worktrees)
                self.assertFalse(any(command[3:5] == ("worktree", "remove") for command in commands))
                self.assertFalse(any(command[3:5] == ("worktree", "prune") for command in commands))

    def test_cli_uses_host_env_and_reports_summary(self) -> None:
        self.write_file(".refactor-loop/logs/old.log", "done\nEXIT=0\n", 25)
        result = self.run_cli()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("runtime_retention: enabled=true ttl_hours=24 deleted=0 kept=0", result.stdout)
        self.assertIn("removed_worktrees=0", result.stdout)
        self.assertIn("diagnostics=none", result.stdout)

    def test_malformed_plan_shapes_emit_diagnostic_reasons(self) -> None:
        cases: list[tuple[str, str, str]] = [
            ("not-json", "{", "plan_json_invalid"),
            ("wrong-top-level", "[]", "plan_shape_invalid"),
            ("wrong-kind", json.dumps({"kind": "Other", "stale_worktrees": []}), "plan_kind_invalid"),
            ("wrong-worktrees", json.dumps({"kind": "RuntimeRetentionPlan", "stale_worktrees": {}}), "stale_worktrees_invalid"),
            ("wrong-generated-files", json.dumps({"kind": "RuntimeRetentionPlan", "generated_files": {}}), "generated_files_invalid"),
            ("invalid-item", json.dumps({"kind": "RuntimeRetentionPlan", "stale_worktrees": ["bad"]}), "invalid_item"),
        ]
        for name, content, reason in cases:
            with self.subTest(name=name):
                (self.refactor_loop / "state" / "runtime-retention-plan.json").write_text(content, encoding="utf-8")

                result = retain_runtime(self.repo, enabled=True)

                self.assertEqual(0, result.removed_worktrees)
                self.assertTrue(any(f"reason={reason}" in diagnostic for diagnostic in result.diagnostics), result.diagnostics)
                self.assertTrue(any("target=" in diagnostic for diagnostic in result.diagnostics), result.diagnostics)

        plan_path = self.refactor_loop / "state" / "runtime-retention-plan.json"
        plan_path.unlink()
        plan_path.mkdir()

        result = retain_runtime(self.repo, enabled=True)

        self.assertEqual(0, result.removed_worktrees)
        self.assertTrue(any(f"target={plan_path.resolve()}" in diagnostic and "reason=plan_read_failed" in diagnostic for diagnostic in result.diagnostics), result.diagnostics)

    def test_planner_item_skip_paths_emit_target_and_reason(self) -> None:
        full_proof = {
            "no_in_flight": True,
            "no_open_issue_or_pr": True,
            "no_dirty": True,
            "no_local_ahead": True,
            "merged_or_missing_safe": True,
        }
        cases: list[tuple[str, dict[str, object], str, str]] = [
            (
                "not-eligible",
                {"path": ".worktrees/iter1-issue-1", "eligible": False, "proof": full_proof},
                "planner_not_eligible",
                ".worktrees/iter1-issue-1",
            ),
            (
                "invalid-proof",
                {"path": ".worktrees/iter1-issue-1", "eligible": True, "proof": []},
                "invalid_proof",
                ".worktrees/iter1-issue-1",
            ),
            (
                "false-proof",
                {
                    "path": ".worktrees/iter1-issue-1",
                    "eligible": True,
                    "proof": {**full_proof, "no_dirty": False},
                },
                "proof_no_dirty_not_true",
                ".worktrees/iter1-issue-1",
            ),
            ("missing-path", {"eligible": True, "proof": full_proof}, "invalid_path", "entry:0"),
            (
                "escaped-path",
                {"path": ".worktrees/../x", "eligible": True, "proof": full_proof},
                "invalid_path",
                ".worktrees/../x",
            ),
        ]
        for name, item, reason, target in cases:
            with self.subTest(name=name):
                self.write_plan({"kind": "RuntimeRetentionPlan", "stale_worktrees": [item]})

                result = retain_runtime(self.repo, enabled=True)

                self.assertEqual(0, result.removed_worktrees)
                self.assertTrue(
                    any(f"target={target}" in diagnostic and f"reason={reason}" in diagnostic for diagnostic in result.diagnostics),
                    result.diagnostics,
                )

    def test_missing_worktree_and_git_recheck_failures_emit_diagnostics(self) -> None:
        cases: list[tuple[str, dict[str, object], str]] = [
            ("missing", {}, "worktree_missing"),
            ("dirty", {"status_stdout": " M changed.txt\n"}, "dirty_status"),
            ("status-failed", {"status_returncode": 1, "status_stderr": "fatal status\n"}, "git_status_failed"),
            ("ahead", {"ahead_stdout": "2\n"}, "local_ahead"),
            ("ahead-failed", {"ahead_returncode": 1, "ahead_stderr": "fatal ahead\n"}, "git_ahead_failed"),
        ]
        for name, runner_kwargs, reason in cases:
            with self.subTest(name=name):
                stale = self.write_stale_worktree_plan()
                if name == "missing":
                    shutil.rmtree(stale)
                commands: list[tuple[str, ...]] = []

                result = retain_runtime(self.repo, enabled=True, command_runner=self.git_recheck_runner(commands, **runner_kwargs))

                self.assertEqual(0, result.removed_worktrees)
                self.assertFalse(result.pruned_worktrees)
                self.assertTrue(
                    any(f"target={stale.resolve()}" in diagnostic and f"reason={reason}" in diagnostic for diagnostic in result.diagnostics),
                    result.diagnostics,
                )
                self.assertFalse(any(command[3:5] == ("worktree", "remove") for command in commands))

    def test_worktree_remove_and_prune_failures_emit_diagnostics(self) -> None:
        stale = self.write_stale_worktree_plan()
        commands: list[tuple[str, ...]] = []

        remove_failed = retain_runtime(
            self.repo,
            enabled=True,
            command_runner=self.git_recheck_runner(commands, remove_returncode=1, remove_stderr="fatal remove\n"),
        )

        self.assertEqual(0, remove_failed.removed_worktrees)
        self.assertFalse(remove_failed.pruned_worktrees)
        self.assertTrue(
            any(f"target={stale.resolve()}" in diagnostic and "reason=worktree_remove_failed" in diagnostic for diagnostic in remove_failed.diagnostics),
            remove_failed.diagnostics,
        )

        stale = self.write_stale_worktree_plan()
        commands = []
        prune_failed = retain_runtime(
            self.repo,
            enabled=True,
            command_runner=self.git_recheck_runner(commands, prune_returncode=1, prune_stderr="fatal prune\n"),
        )

        self.assertEqual(1, prune_failed.removed_worktrees)
        self.assertFalse(prune_failed.pruned_worktrees)
        self.assertTrue(
            any(f"target={self.repo.resolve()}" in diagnostic and "reason=worktree_prune_failed" in diagnostic for diagnostic in prune_failed.diagnostics),
            prune_failed.diagnostics,
        )

    def test_refuses_without_repo_root_or_host_env(self) -> None:
        isolated = self.tmp_root / "isolated"
        isolated.mkdir()
        result = subprocess.run(
            [sys.executable, str(CLI), "runtime-retention"],
            cwd=isolated,
            env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": os.environ.get("PYTHONPATH", "")},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("REPO_ROOT is unset", result.stderr)


class RuntimeRetentionSourceRegressionTests(unittest.TestCase):
    def test_runtime_retention_contract_is_narrow(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "runtime_retention.py").read_text(encoding="utf-8")
        for token in (
            "RUNTIME_RETENTION_ENABLE",
            "RuntimeRetentionPlan",
            "PENDING_EVENTS_MAX_LINES",
            ".controller-pending-events.log",
            ".concurrency-alert.log",
            "RETENTION_COMPACT_LOG_PATHS",
            "same-inode",
            "generated_files",
            "legacy_generated_files_ignored",
            "SPAWN_TASK_LOCKS_PATH",
            "read_spawn_task_lock_metadata",
            "spawn_task_log_has_exit_marker",
            "removed_spawn_task_locks",
            "spawn_task_lock_log_missing",
            '"worktree", "remove"',
            '"worktree", "prune"',
            "no_in_flight",
            "no_open_issue_or_pr",
            "no_dirty",
            "no_local_ahead",
            "merged_or_missing_safe",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)
        for forbidden in ("gh ", "gh-", '"fetch"', '"push"', '"merge"', '"reset"', '"rebase"', '"commit"', '"label"', '"release"', "archive", "index"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)
        for forbidden in ("GENERATED_FILE_PROOF_TRUTHS", "RuntimeRetentionPlan.spawn_task_locks", "path.unlink(", "os.replace", "NamedTemporaryFile"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_restart_helper_runs_runtime_retention_before_daemon_start(self) -> None:
        restart = (SCRIPT_DIR / "codex_refactor_loop" / "restart.py").read_text(encoding="utf-8")
        self.assertIn("_run_runtime_retention", restart)
        self.assertLess(
            restart.index("self._run_runtime_retention()"),
            restart.index("for name, command in restart_daemon_commands_for_context(self.ctx)"),
        )
        self.assertIn("runtime_retention warning: helper failed; continuing daemon restart", restart)


if __name__ == "__main__":
    unittest.main()
