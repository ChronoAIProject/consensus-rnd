#!/usr/bin/env python3
"""Tests for shared worker terminal marker parsing."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

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

    def test_duplicate_or_conflicting_artifact_evidence_fails_closed(self) -> None:
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

            self.assertFalse(read_worker_terminal_marker(duplicate).found)
            self.assertFalse(read_worker_terminal_marker(conflict).found)

    def test_duplicate_or_conflicting_log_evidence_fails_closed(self) -> None:
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

            self.assertEqual(read_worker_terminal_marker(duplicate).reason, "duplicate_or_conflicting_log_marker")
            self.assertEqual(read_worker_terminal_marker(conflict).reason, "duplicate_or_conflicting_log_marker")

    def test_source_regression_consumers_import_shared_reader(self) -> None:
        for relative in (
            "codex_refactor_loop/wakeup_plan.py",
            "codex_refactor_loop/wakeup_runner.py",
            "codex_refactor_loop/implement_lifecycle.py",
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
            "len(markers) == 1",
            "len(unique) == 1",
        ):
            with self.subTest(required=required):
                self.assertIn(required, reader)


if __name__ == "__main__":
    unittest.main()
