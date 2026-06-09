#!/usr/bin/env python3
"""Tests for shared worker terminal marker parsing."""

from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.worker_markers import read_worker_terminal_marker


class WorkerMarkerReaderTests(unittest.TestCase):
    def repo(self, tmp: str) -> tuple[Path, Path]:
        root = Path(tmp)
        logs = root / ".refactor-loop" / "logs"
        runs = root / ".refactor-loop" / "runs"
        logs.mkdir(parents=True)
        runs.mkdir(parents=True)
        return logs, runs

    def test_reads_standalone_log_marker_before_clean_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, _runs = self.repo(tmp)
            log = logs / "phase9-issue12-r1-judge.log"
            log.write_text(
                "body\n"
                "⟦AI:AUTO-LOOP⟧\n"
                "`META_JUDGE_DONE:consensus:minimal:summary with spaces`\n"
                "tokens used\n"
                "EXIT=0\n",
                encoding="utf-8",
            )

            marker = read_worker_terminal_marker(log)

            self.assertEqual(marker.marker, "META_JUDGE_DONE:consensus:minimal:summary with spaces")
            self.assertEqual(marker.source, "log")

    def test_reads_solver_marker_from_bounded_tail_before_exit_with_later_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, _runs = self.repo(tmp)
            log = logs / "phase9-issue659-r2-minimal.log"
            log.write_text(
                "worker summary\n"
                "SOLVER_DONE:minimal:propose:shared-reader\n"
                "https://github.com/example/repo/issues/659\n"
                "DONE_AT=2026-06-07T00:00:00Z\n"
                "EXIT=0\n",
                encoding="utf-8",
            )

            marker = read_worker_terminal_marker(log)

            self.assertTrue(marker.found)
            self.assertEqual(marker.marker, "SOLVER_DONE:minimal:propose:shared-reader")
            self.assertEqual(marker.source, "log")

    def test_solver_marker_tail_requires_unique_role_matching_standalone_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, _runs = self.repo(tmp)
            wrong_role = logs / "phase9-issue659-r2-minimal.log"
            wrong_role.write_text(
                "SOLVER_DONE:structural:propose:wrong-role\n"
                "EXIT=0\n",
                encoding="utf-8",
            )
            conflict = logs / "solver-issue659-r2-delete.log"
            conflict.write_text(
                "SOLVER_DONE:delete:propose:first\n"
                "SOLVER_DONE:delete:reject:second\n"
                "EXIT=0\n",
                encoding="utf-8",
            )

            self.assertEqual(
                read_worker_terminal_marker(wrong_role).reason,
                "duplicate_or_conflicting_log_marker",
            )
            self.assertEqual(
                read_worker_terminal_marker(conflict).reason,
                "duplicate_or_conflicting_log_marker",
            )

    def test_reads_same_stem_artifact_when_log_markerless(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self.repo(tmp)
            log = logs / "implement-issue-421.log"
            log.write_text("worker output\nEXIT=0\n", encoding="utf-8")
            (runs / "implement-issue-421.md").write_text(
                "summary\n⟦AI:AUTO-LOOP⟧\nIMPLEMENT_DONE:issue-421:ok\n",
                encoding="utf-8",
            )

            marker = read_worker_terminal_marker(log)

            self.assertEqual(marker.marker, "IMPLEMENT_DONE:issue-421:ok")
            self.assertEqual(marker.source, "artifact")

    def test_reads_review_done_from_same_stem_artifact_when_log_markerless(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self.repo(tmp)
            log = logs / "review-pr42-quality-r2.log"
            log.write_text("review body\nEXIT=0\n", encoding="utf-8")
            (runs / "review-pr42-quality-r2.md").write_text(
                "---\nverdict: approve\n---\nREVIEW_DONE:42:quality:approve\n",
                encoding="utf-8",
            )

            marker = read_worker_terminal_marker(log)

            self.assertEqual(marker.marker, "REVIEW_DONE:42:quality:approve")
            self.assertEqual(marker.source, "artifact")

    def test_solver_artifact_fallback_requires_role_matching_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self.repo(tmp)
            log = logs / "phase9-issue659-r2-minimal.log"
            log.write_text("solver output\nEXIT=0\n", encoding="utf-8")
            (runs / "phase9-issue659-r2-minimal.md").write_text(
                "SOLVER_DONE:structural:propose:wrong-role\n",
                encoding="utf-8",
            )

            marker = read_worker_terminal_marker(log)

            self.assertFalse(marker.found)
            self.assertEqual(marker.reason, "duplicate_or_conflicting_artifact_marker")

    def test_artifact_fallback_is_same_stem_clean_exit_and_allowlisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self.repo(tmp)
            (logs / "audit-iter-9.log").write_text("output\nEXIT=0\n", encoding="utf-8")
            (runs / "audit-iter-9.md").write_text("IMPLEMENT_DONE:issue-9:ok\n", encoding="utf-8")
            (logs / "implement-issue-777.log").write_text("output\nEXIT=1\n", encoding="utf-8")
            (runs / "implement-issue-777.md").write_text("IMPLEMENT_DONE:issue-777:ok\n", encoding="utf-8")
            (logs / "implement-issue-778.log").write_text("output\nEXIT=0\n", encoding="utf-8")
            (runs / "implement-issue-778.md").write_text("SOLVER_DONE:minimal:ok\n", encoding="utf-8")

            self.assertFalse(read_worker_terminal_marker(logs / "audit-iter-9.log").found)
            self.assertFalse(read_worker_terminal_marker(logs / "implement-issue-777.log").found)
            self.assertFalse(read_worker_terminal_marker(logs / "implement-issue-778.log").found)

    def test_strict_marker_families_reject_malformed_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self.repo(tmp)
            bad_log = logs / "implement-issue-5.log"
            bad_log.write_text("IMPLEMENT_DONE:issue-5:ok:extra\nEXIT=0\n", encoding="utf-8")
            bad_artifact = logs / "review-pr42-quality-r1.log"
            bad_artifact.write_text("review body\nEXIT=0\n", encoding="utf-8")
            (runs / "review-pr42-quality-r1.md").write_text("REVIEW_DONE:42:quality:approve:extra\n", encoding="utf-8")
            invalid_implement_status = logs / "implement-issue-6.log"
            invalid_implement_status.write_text("IMPLEMENT_DONE:issue-6:done\nEXIT=0\n", encoding="utf-8")

            self.assertFalse(read_worker_terminal_marker(bad_log).found)
            self.assertFalse(read_worker_terminal_marker(bad_artifact).found)
            self.assertFalse(read_worker_terminal_marker(invalid_implement_status).found)

    def test_malformed_log_marker_blocks_artifact_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self.repo(tmp)
            log = logs / "review-pr42-quality-r1.log"
            log.write_text("REVIEW_DONE:42:quality:approve:extra\nEXIT=0\n", encoding="utf-8")
            (runs / "review-pr42-quality-r1.md").write_text("REVIEW_DONE:42:quality:approve\n", encoding="utf-8")

            marker = read_worker_terminal_marker(log)

            self.assertFalse(marker.found)
            self.assertEqual(marker.reason, "malformed_log_marker")

    def test_identical_artifact_markers_are_valid_but_conflicts_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self.repo(tmp)
            duplicate = logs / "implement-issue-1.log"
            duplicate.write_text("output\nEXIT=0\n", encoding="utf-8")
            (runs / "implement-issue-1.md").write_text(
                "IMPLEMENT_DONE:issue-1:ok\nIMPLEMENT_DONE:issue-1:ok\n",
                encoding="utf-8",
            )
            conflict = logs / "implement-issue-2.log"
            conflict.write_text("output\nEXIT=0\n", encoding="utf-8")
            (runs / "implement-issue-2.md").write_text(
                "IMPLEMENT_DONE:issue-2:ok\nIMPLEMENT_DONE:issue-two:ok\n",
                encoding="utf-8",
            )

            duplicate_marker = read_worker_terminal_marker(duplicate)
            self.assertTrue(duplicate_marker.found)
            self.assertEqual(duplicate_marker.marker, "IMPLEMENT_DONE:issue-1:ok")
            self.assertEqual(duplicate_marker.reason, "")
            self.assertEqual(read_worker_terminal_marker(conflict).reason, "duplicate_or_conflicting_artifact_marker")

    def test_identical_log_markers_are_valid_but_conflicts_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, _runs = self.repo(tmp)
            duplicate = logs / "implement-issue-3.log"
            duplicate.write_text(
                "IMPLEMENT_DONE:issue-3:ok\n"
                "⟦AI:AUTO-LOOP⟧\n"
                "IMPLEMENT_DONE:issue-3:ok\n"
                "EXIT=0\n",
                encoding="utf-8",
            )
            conflict = logs / "implement-issue-4.log"
            conflict.write_text(
                "IMPLEMENT_DONE:issue-4:partial\n"
                "IMPLEMENT_DONE:issue-4:ok\n"
                "EXIT=0\n",
                encoding="utf-8",
            )

            duplicate_marker = read_worker_terminal_marker(duplicate)
            self.assertTrue(duplicate_marker.found)
            self.assertEqual(duplicate_marker.marker, "IMPLEMENT_DONE:issue-3:ok")
            self.assertEqual(duplicate_marker.reason, "")
            self.assertEqual(read_worker_terminal_marker(conflict).reason, "duplicate_or_conflicting_log_marker")

    def test_identical_nonfinal_log_markers_are_valid_but_conflicts_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, _runs = self.repo(tmp)
            duplicate = logs / "remote-ci-fix-pr558-contract-tests.log"
            duplicate.write_text(
                "REMOTE_CI_FIX_DONE:contract-tests:ok\n"
                "REMOTE_CI_FIX_DONE:contract-tests:ok\n"
                "tokens used\n"
                "EXIT=0\n",
                encoding="utf-8",
            )
            conflict = logs / "remote-ci-fix-pr558-contract-tests-conflict.log"
            conflict.write_text(
                "REMOTE_CI_FIX_DONE:contract-tests:blocked\n"
                "REMOTE_CI_FIX_DONE:contract-tests:ok\n"
                "tokens used\n"
                "EXIT=0\n",
                encoding="utf-8",
            )

            duplicate_marker = read_worker_terminal_marker(duplicate)
            self.assertTrue(duplicate_marker.found)
            self.assertEqual(duplicate_marker.marker, "REMOTE_CI_FIX_DONE:contract-tests:ok")
            self.assertEqual(duplicate_marker.reason, "")
            self.assertEqual(read_worker_terminal_marker(conflict).reason, "duplicate_or_conflicting_log_marker")

    def test_phase9_reflector_final_meta_resolved_tolerates_solver_context_marker_only_for_reflector_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, _runs = self.repo(tmp)
            reflector = logs / "phase9-issue573-r3-reflector.log"
            reflector.write_text(
                "solver artifact excerpt\n"
                "SOLVER_DONE:delete:abstain:no-current-deletion\n"
                "META_RESOLVED:drop:no-actionable-framing-after-3-rounds\n"
                "EXIT=0\n",
                encoding="utf-8",
            )
            solver = logs / "phase9-issue573-r3-delete.log"
            solver.write_text(
                "solver artifact excerpt\n"
                "META_JUDGE_DONE:converge:round-3\n"
                "SOLVER_DONE:delete:abstain:no-current-deletion\n"
                "EXIT=0\n",
                encoding="utf-8",
            )

            reflector_marker = read_worker_terminal_marker(reflector)
            self.assertTrue(reflector_marker.found)
            self.assertEqual(reflector_marker.marker, "META_RESOLVED:drop:no-actionable-framing-after-3-rounds")
            self.assertEqual(reflector_marker.reason, "")
            self.assertEqual(read_worker_terminal_marker(solver).reason, "duplicate_or_conflicting_log_marker")

    def test_phase9_reflector_final_meta_resolved_tolerates_duplicate_identical_final_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, _runs = self.repo(tmp)
            reflector = logs / "phase9-issue573-r3-reflector.log"
            reflector.write_text(
                "SOLVER_DONE:delete:abstain:no-current-deletion\n"
                "⟦AI:AUTO-LOOP⟧\n"
                "META_RESOLVED:drop:no-actionable-framing-after-3-rounds\n"
                "tokens used\n"
                "input tokens: 1\n"
                "output tokens: 2\n"
                "⟦AI:AUTO-LOOP⟧\n"
                "META_RESOLVED:drop:no-actionable-framing-after-3-rounds\n"
                "EXIT=0\n",
                encoding="utf-8",
            )

            marker = read_worker_terminal_marker(reflector)
            self.assertTrue(marker.found)
            self.assertEqual(marker.marker, "META_RESOLVED:drop:no-actionable-framing-after-3-rounds")
            self.assertEqual(marker.source, "log")
            self.assertEqual(marker.reason, "")

    def test_single_nonfinal_log_marker_is_not_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, _runs = self.repo(tmp)
            log = logs / "remote-ci-fix-pr558-contract-tests.log"
            log.write_text(
                "REMOTE_CI_FIX_DONE:contract-tests:ok\n"
                "tokens used\n"
                "EXIT=0\n",
                encoding="utf-8",
            )

            marker = read_worker_terminal_marker(log)

            self.assertFalse(marker.found)
            self.assertEqual(marker.reason, "duplicate_or_conflicting_log_marker")

    def test_repeated_review_log_marker_copies_are_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, _runs = self.repo(tmp)
            log = logs / "review-pr522-architect-r7.log"
            log.write_text(
                "summary: REVIEW_DONE:522:architect:approve\n"
                "REVIEW_DONE:522:architect:approve\n"
                "+REVIEW_DONE:522:architect:approve\n"
                "REVIEW_DONE:522:architect:approve\n"
                "EXIT=0\n",
                encoding="utf-8",
            )

            marker = read_worker_terminal_marker(log)

            self.assertTrue(marker.found)
            self.assertEqual(marker.marker, "REVIEW_DONE:522:architect:approve")
            self.assertEqual(marker.reason, "")

    def test_repeated_review_artifact_marker_copies_are_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self.repo(tmp)
            log = logs / "review-pr522-architect-r7.log"
            log.write_text("review body\nEXIT=0\n", encoding="utf-8")
            (runs / "review-pr522-architect-r7.md").write_text(
                "---\nverdict: approve\n---\n"
                "REVIEW_DONE:522:architect:approve\n"
                "+REVIEW_DONE:522:architect:approve\n"
                "REVIEW_DONE:522:architect:approve\n",
                encoding="utf-8",
            )

            marker = read_worker_terminal_marker(log)

            self.assertTrue(marker.found)
            self.assertEqual(marker.marker, "REVIEW_DONE:522:architect:approve")
            self.assertEqual(marker.reason, "")

    def test_source_regression_consumers_import_shared_reader(self) -> None:
        for relative in (
            "codex_refactor_loop/wakeup_plan.py",
            "codex_refactor_loop/wakeup_runner.py",
            "codex_refactor_loop/implement_lifecycle.py",
            "codex_refactor_loop/phase9/router.py",
        ):
            source = (SCRIPT_DIR / relative).read_text(encoding="utf-8")
            with self.subTest(relative=relative):
                self.assertIn("worker_markers", source)
                self.assertIn("read_worker_terminal_marker", source)

        reader = (SCRIPT_DIR / "codex_refactor_loop" / "worker_markers.py").read_text(encoding="utf-8")
        for required in (
            "def read_worker_terminal_marker(",
            "log_path.parent.parent / \"runs\" / f\"{log_path.stem}.md\"",
            "REVIEW_DONE_STRICT_RE",
            "IMPLEMENT_DONE_STATUSES",
            "def _reject_malformed_implement_marker(",
            "def _malformed_standalone_marker(",
            "REVIEW_DONE:[1-9][0-9]*:[A-Za-z][A-Za-z0-9_-]*:(?:approve|comment|reject)(?::real)?",
            "malformed_log_marker",
            "malformed_artifact_marker",
            "IMPLEMENT_DONE:",
            "SOLVER_DONE:",
            "META_JUDGE_DONE:",
            "REVIEW_DONE:",
            "duplicate_or_conflicting_log_marker",
            "duplicate_or_conflicting_artifact_marker",
            "len(unique) == 1",
            "len(all_unique) == 1",
        ):
            with self.subTest(required=required):
                self.assertIn(required, reader)


if __name__ == "__main__":
    unittest.main()
