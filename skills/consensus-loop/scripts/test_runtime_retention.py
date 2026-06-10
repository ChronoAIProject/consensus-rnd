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

    def generated_file_plan_item(
        self,
        rel: str,
        *,
        eligible: bool = True,
        proof_overrides: dict[str, object] | None = None,
    ) -> dict[str, object]:
        proof = {
            "generated_file": True,
            "ttl_expired": True,
            "no_in_flight": True,
            "no_open_actionable": True,
            "no_pending_intent": True,
            "no_unconsumed_marker": True,
            "no_recovery_surface": True,
        }
        if proof_overrides:
            proof.update(proof_overrides)
        return {"path": rel, "eligible": eligible, "proof": proof}

    def write_generated_files_plan(self, *items: object) -> None:
        self.write_plan({"kind": "RuntimeRetentionPlan", "generated_files": list(items)})

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
                {"CONSENSUS_RND_HOST_ENV": self.host_env_rel.as_posix()},
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

    def test_generated_file_gc_fails_closed_without_file_level_planner_proof(self) -> None:
        old_log = self.write_file(".refactor-loop/logs/old.log", "done\nEXIT=0\n", 25)
        old_prompt = self.write_file(".refactor-loop/prompts/old.md", "prompt\n", 25)
        old_run = self.write_file(".refactor-loop/runs/old.json", "{}\n", 25)
        young_log = self.write_file(".refactor-loop/logs/young.log", "done\n", 1)
        old_non_generated = self.write_file(".refactor-loop/logs/old.bin", "keep\n", 25)
        state_artifact = self.write_file(".refactor-loop/state/old.json", "keep\n", 25)

        result = retain_runtime(self.repo, enabled=True)

        self.assertEqual((result.deleted, result.kept, result.missing), (0, 0, False))
        self.assertEqual(result.target.resolve(), self.refactor_loop.resolve())
        self.assertTrue(old_log.exists())
        self.assertTrue(old_prompt.exists())
        self.assertTrue(old_run.exists())
        self.assertTrue(young_log.exists())
        self.assertTrue(old_non_generated.exists())
        self.assertTrue(state_artifact.exists())

    def test_generated_file_gc_deletes_old_planner_proven_regular_files(self) -> None:
        old_log = self.write_file(".refactor-loop/logs/old.log", "complete\nDONE_OK:real\nEXIT=0\n", 25)
        old_prompt = self.write_file(".refactor-loop/prompts/old.md", "prompt\n", 25)
        old_run = self.write_file(".refactor-loop/runs/old.json", "{}\n", 25)
        self.write_generated_files_plan(
            self.generated_file_plan_item(".refactor-loop/logs/old.log"),
            self.generated_file_plan_item(".refactor-loop/prompts/old.md"),
            self.generated_file_plan_item(".refactor-loop/runs/old.json"),
        )

        result = retain_runtime(self.repo, enabled=True)

        self.assertEqual((result.deleted, result.kept, result.missing), (3, 0, False))
        self.assertFalse(old_log.exists())
        self.assertFalse(old_prompt.exists())
        self.assertFalse(old_run.exists())

    def test_generated_file_gc_legacy_stale_worktree_only_plan_keeps_compatibility(self) -> None:
        old_log = self.write_file(".refactor-loop/logs/old.log", "done\n", 25)
        stale = self.write_stale_worktree_plan()
        commands: list[tuple[str, ...]] = []

        result = retain_runtime(self.repo, enabled=True, command_runner=self.git_recheck_runner(commands))

        self.assertEqual((result.deleted, result.kept), (0, 0))
        self.assertEqual(1, result.removed_worktrees)
        self.assertTrue(result.pruned_worktrees)
        self.assertTrue(old_log.exists())
        self.assertIn(("git", "-C", str(self.repo.resolve()), "worktree", "remove", str(stale.resolve())), commands)

    def test_generated_file_gc_keeps_young_open_inflight_pending_unconsumed_recovery_and_unproven_files(self) -> None:
        cases: list[tuple[str, str, float, dict[str, object], str]] = [
            ("young", ".refactor-loop/logs/young.log", 1, {}, "generated_file_ttl_not_expired"),
            ("open-target", ".refactor-loop/logs/open.log", 25, {"no_open_actionable": False}, "generated_file_proof_no_open_actionable_not_true"),
            ("in-flight", ".refactor-loop/logs/inflight.log", 25, {"no_in_flight": False}, "generated_file_proof_no_in_flight_not_true"),
            ("pending-intent", ".refactor-loop/prompts/pending.md", 25, {"no_pending_intent": False}, "generated_file_proof_no_pending_intent_not_true"),
            (
                "unconsumed-marker",
                ".refactor-loop/runs/unconsumed.md",
                25,
                {"no_unconsumed_marker": False},
                "generated_file_proof_no_unconsumed_marker_not_true",
            ),
            ("recovery-proof", ".refactor-loop/logs/recovery-proof.log", 25, {"no_recovery_surface": False}, "generated_file_proof_no_recovery_surface_not_true"),
            ("unproven", ".refactor-loop/logs/unproven.log", 25, {"generated_file": False}, "generated_file_proof_generated_file_not_true"),
        ]
        for name, rel, age_hours, proof_overrides, reason in cases:
            with self.subTest(name=name):
                path = self.write_file(rel, "done\n", age_hours)
                self.write_generated_files_plan(self.generated_file_plan_item(rel, proof_overrides=proof_overrides))

                result = retain_runtime(self.repo, enabled=True)

                self.assertEqual((result.deleted, result.kept), (0, 1))
                self.assertTrue(path.exists())
                self.assertTrue(any(f"reason={reason}" in diagnostic for diagnostic in result.diagnostics), result.diagnostics)

    def test_generated_file_gc_keeps_pending_referenced_and_markerless_recovery_files(self) -> None:
        pending_prompt = self.write_file(".refactor-loop/prompts/pending-ref.md", "prompt\n", 25)
        markerless_log = self.write_file(".refactor-loop/logs/implement-issue-8.log", "done without marker\nEXIT=0\n", 25)
        pending = self.refactor_loop / ".controller-pending-events.log"
        pending.write_text("HARNESS_SPAWN_INTENT prompt=.refactor-loop/prompts/pending-ref.md\n", encoding="utf-8")
        self.write_generated_files_plan(
            self.generated_file_plan_item(".refactor-loop/prompts/pending-ref.md"),
            self.generated_file_plan_item(".refactor-loop/logs/implement-issue-8.log"),
        )

        result = retain_runtime(self.repo, enabled=True)

        self.assertEqual((result.deleted, result.kept), (0, 2))
        self.assertTrue(pending_prompt.exists())
        self.assertTrue(markerless_log.exists())
        for reason in ("generated_file_pending_reference", "generated_file_recovery_markerless_log"):
            with self.subTest(reason=reason):
                self.assertTrue(any(f"reason={reason}" in diagnostic for diagnostic in result.diagnostics), result.diagnostics)

    def test_generated_file_gc_keeps_dead_and_failed_worker_recovery_logs(self) -> None:
        dead_log = self.write_file(".refactor-loop/logs/implement-issue-8.log", "started\n", 25)
        failed_log = self.write_file(".refactor-loop/logs/review-pr8-tests-r1.log", "started\nREVIEW_DONE:8:tests:reject\nEXIT=1\n", 25)
        self.write_generated_files_plan(
            self.generated_file_plan_item(".refactor-loop/logs/implement-issue-8.log"),
            self.generated_file_plan_item(".refactor-loop/logs/review-pr8-tests-r1.log"),
        )

        result = retain_runtime(self.repo, enabled=True)

        self.assertEqual((result.deleted, result.kept), (0, 2))
        self.assertTrue(dead_log.exists())
        self.assertTrue(failed_log.exists())
        for reason in ("generated_file_recovery_dead_worker_log", "generated_file_recovery_failed_worker_log"):
            with self.subTest(reason=reason):
                self.assertTrue(any(f"reason={reason}" in diagnostic for diagnostic in result.diagnostics), result.diagnostics)

    def test_generated_file_gc_keeps_malformed_path_escape_symlink_fifo_and_missing_entries(self) -> None:
        old_target = self.write_file(".refactor-loop/logs/target.log", "target\n", 25)
        symlink_log = self.refactor_loop / "logs" / "linked.log"
        symlink_log.symlink_to(old_target)
        fifo_log = self.refactor_loop / "logs" / "pipe.log"
        os.mkfifo(fifo_log)
        malformed = self.write_file(".refactor-loop/logs/malformed.log", "done\n", 25)
        self.write_generated_files_plan(
            "not-a-dict",
            self.generated_file_plan_item(".refactor-loop/../escape.log"),
            self.generated_file_plan_item(".refactor-loop/state/old.json"),
            self.generated_file_plan_item(".refactor-loop/logs/linked.log"),
            self.generated_file_plan_item(".refactor-loop/logs/pipe.log"),
            {"path": ".refactor-loop/logs/malformed.log", "eligible": True, "proof": []},
            self.generated_file_plan_item(".refactor-loop/logs/missing.log"),
        )

        result = retain_runtime(self.repo, enabled=True)

        self.assertEqual((result.deleted, result.kept), (0, 7))
        self.assertTrue(old_target.exists())
        self.assertTrue(symlink_log.is_symlink())
        self.assertTrue(fifo_log.exists())
        self.assertTrue(malformed.exists())
        for reason in (
            "generated_file_item_invalid",
            "generated_file_invalid_path",
            "generated_file_disallowed_path",
            "generated_file_symlink",
            "generated_file_not_regular",
            "generated_file_invalid_proof",
            "generated_file_missing",
        ):
            with self.subTest(reason=reason):
                self.assertTrue(any(f"reason={reason}" in diagnostic for diagnostic in result.diagnostics), result.diagnostics)

    def test_generated_path_symlink_and_non_regular_files_are_retained_without_plan_entries(self) -> None:
        old_target = self.write_file(".refactor-loop/logs/target.log", "target\n", 25)
        symlink_log = self.refactor_loop / "logs" / "linked.log"
        symlink_log.symlink_to(old_target)
        fifo_log = self.refactor_loop / "logs" / "pipe.log"
        os.mkfifo(fifo_log)

        result = retain_runtime(self.repo, enabled=True)

        self.assertEqual((result.deleted, result.kept), (0, 0))
        self.assertTrue(symlink_log.is_symlink())
        self.assertTrue(fifo_log.exists())
        self.assertTrue(old_target.exists())

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
            "same-inode",
            "generated_files",
            "GENERATED_FILE_PROOF_TRUTHS",
            "no_open_actionable",
            "no_pending_intent",
            "no_unconsumed_marker",
            "no_recovery_surface",
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

    def test_restart_helper_runs_runtime_retention_before_daemon_start(self) -> None:
        restart = (SCRIPT_DIR / "codex_refactor_loop" / "restart.py").read_text(encoding="utf-8")
        self.assertIn("_run_runtime_retention", restart)
        self.assertLess(restart.index("self._run_runtime_retention()"), restart.index("for name, command in DAEMON_COMMANDS"))
        self.assertIn("runtime_retention warning: helper failed; continuing daemon restart", restart)


if __name__ == "__main__":
    unittest.main()
