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

    def test_reads_verbose_solver_marker_from_full_clean_log_before_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self.repo(tmp)
            log = logs / "phase9-issue997-r1-minimal.log"
            marker_text = "SOLVER_DONE:minimal:propose:accept rollup/<current integration sha> only"
            verbose_lines = [f"verbose diagnostic line {index}" for index in range(40)]
            log.write_text(
                "\n".join(
                    [
                        "worker summary begins",
                        marker_text,
                        *verbose_lines,
                        f"+{marker_text}",
                        f"prose repeats `{marker_text}` as context",
                        "worker output footer",
                        "EXIT=0",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (runs / "phase9-issue997-r1-minimal.md").write_text(
                "\n".join(
                    [
                        "## result",
                        marker_text,
                        "⟦AI:AUTO-LOOP⟧",
                        marker_text,
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            marker = read_worker_terminal_marker(log)

            self.assertTrue(marker.found)
            self.assertEqual(marker.marker, marker_text)
            self.assertEqual(marker.source, "log")
            self.assertEqual(marker.reason, "")

    def test_solver_marker_allows_angle_brackets_in_summary_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, _runs = self.repo(tmp)
            log = logs / "phase9-issue997-r1-minimal.log"
            marker_text = "SOLVER_DONE:minimal:propose:accept rollup/<current integration sha> only"
            log.write_text(f"{marker_text}\nEXIT=0\n", encoding="utf-8")

            marker = read_worker_terminal_marker(log)

            self.assertTrue(marker.found)
            self.assertEqual(marker.marker, marker_text)
            self.assertEqual(marker.reason, "")

    def test_solver_marker_tail_requires_unique_expected_role_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, _runs = self.repo(tmp)
            other_role_only = logs / "phase9-issue659-r2-minimal.log"
            other_role_only.write_text(
                "SOLVER_DONE:structural:propose:other-role-noise\n"
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
                read_worker_terminal_marker(other_role_only).reason,
                "marker_missing",
            )
            self.assertEqual(
                read_worker_terminal_marker(conflict).reason,
                "duplicate_or_conflicting_log_marker",
            )

    def test_solver_log_ignores_stray_other_role_and_returns_expected_log_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, _runs = self.repo(tmp)
            log = logs / "phase9-issue1004-r2-minimal.log"
            expected_marker = "SOLVER_DONE:minimal:propose:sync-claude-skill-mirror"
            log.write_text(
                "worker quotes peer output\n"
                "SOLVER_DONE:structural:propose:example-peer-marker\n"
                "worker own verdict\n"
                f"{expected_marker}\n"
                "EXIT=0\n",
                encoding="utf-8",
            )

            marker = read_worker_terminal_marker(log)

            self.assertTrue(marker.found)
            self.assertEqual(marker.marker, expected_marker)
            self.assertEqual(marker.source, "log")
            self.assertEqual(marker.reason, "")

    def test_solver_log_ignores_diff_added_replay_and_uses_same_stem_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self.repo(tmp)
            log = logs / "phase9-issue952-r2-minimal.log"
            log.write_text(
                "+SOLVER_DONE:minimal:propose:diff-echo\n"
                "+ `SOLVER_DONE:minimal:reject:quoted-diff-echo`\n"
                "worker prose embeds SOLVER_DONE:minimal:embedded:ignored\n"
                "EXIT=0\n",
                encoding="utf-8",
            )
            (runs / "phase9-issue952-r2-minimal.md").write_text(
                "SOLVER_DONE:minimal:propose:artifact\n",
                encoding="utf-8",
            )

            marker = read_worker_terminal_marker(log)

            self.assertTrue(marker.found)
            self.assertEqual(marker.marker, "SOLVER_DONE:minimal:propose:artifact")
            self.assertEqual(marker.source, "artifact")
            self.assertEqual(marker.reason, "")

    def test_solver_log_other_role_only_uses_expected_role_artifact_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self.repo(tmp)
            log = logs / "phase9-issue952-r2-minimal.log"
            expected_marker = "SOLVER_DONE:minimal:propose:artifact"
            log.write_text(
                "SOLVER_DONE:structural:propose:other-role-noise\n"
                "EXIT=0\n",
                encoding="utf-8",
            )
            (runs / "phase9-issue952-r2-minimal.md").write_text(
                f"{expected_marker}\n",
                encoding="utf-8",
            )

            marker = read_worker_terminal_marker(log)

            self.assertTrue(marker.found)
            self.assertEqual(marker.marker, expected_marker)
            self.assertEqual(marker.source, "artifact")
            self.assertEqual(marker.reason, "")

    def test_solver_log_raw_malformed_marker_blocks_artifact_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self.repo(tmp)
            log = logs / "phase9-issue952-r2-minimal.log"
            log.write_text(
                "SOLVER_DONE:minimal:<verdict>:<summary>\n"
                "EXIT=0\n",
                encoding="utf-8",
            )
            (runs / "phase9-issue952-r2-minimal.md").write_text(
                "SOLVER_DONE:minimal:propose:artifact\n",
                encoding="utf-8",
            )

            marker = read_worker_terminal_marker(log)

            self.assertFalse(marker.found)
            self.assertEqual(marker.reason, "malformed_log_marker")

    def test_verbose_solver_conflict_uses_companion_when_it_matches_last_same_role_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self.repo(tmp)
            log = logs / "phase9-issue1005-r1-delete.log"
            early_marker = "SOLVER_DONE:delete:propose:delete-draft"
            final_marker = "SOLVER_DONE:delete:abstain:no-deletion-strict-marker-guard-needs-narrow-companion-disambiguation"
            log.write_text(
                "\n".join(
                    [
                        "worker reasoning",
                        early_marker,
                        "more reasoning",
                        f"+{final_marker}",
                        "artifact diff replay is not a raw solver marker",
                        final_marker,
                        "tokens used",
                        "EXIT=0",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (runs / "phase9-issue1005-r1-delete.md").write_text(
                "---\nverdict: abstain\n---\n"
                "⟦AI:AUTO-LOOP⟧\n"
                f"{final_marker}\n",
                encoding="utf-8",
            )

            marker = read_worker_terminal_marker(log)

            self.assertTrue(marker.found)
            self.assertEqual(marker.marker, final_marker)
            self.assertEqual(marker.source, "artifact")
            self.assertEqual(marker.reason, "")

    def test_verbose_solver_conflict_accepts_identical_duplicate_expected_role_companion_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self.repo(tmp)
            log = logs / "phase9-issue1004-r2-minimal.log"
            early_marker = "SOLVER_DONE:minimal:propose:early-release-proof"
            final_marker = "SOLVER_DONE:minimal:propose:sync-claude-skill-mirror"
            log.write_text(
                "worker quotes peer output\n"
                "SOLVER_DONE:structural:propose:quoted-peer-marker\n"
                f"{early_marker}\n"
                "more reasoning\n"
                f"{final_marker}\n"
                "EXIT=0\n",
                encoding="utf-8",
            )
            (runs / "phase9-issue1004-r2-minimal.md").write_text(
                "---\nverdict: propose\n---\n"
                f"{final_marker}\n"
                "⟦AI:AUTO-LOOP⟧\n"
                f"{final_marker}\n",
                encoding="utf-8",
            )

            marker = read_worker_terminal_marker(log)

            self.assertTrue(marker.found)
            self.assertEqual(marker.marker, final_marker)
            self.assertEqual(marker.source, "artifact")
            self.assertEqual(marker.reason, "")

    def test_verbose_solver_conflict_ignores_other_role_and_uses_expected_role_companion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self.repo(tmp)
            log = logs / "phase9-issue1004-r2-minimal.log"
            early_marker = "SOLVER_DONE:minimal:propose:early-release-proof"
            final_marker = "SOLVER_DONE:minimal:propose:sync-claude-skill-mirror"
            log.write_text(
                "worker quotes peer output\n"
                "SOLVER_DONE:structural:propose:quoted-peer-marker\n"
                f"{early_marker}\n"
                "more reasoning\n"
                f"{final_marker}\n"
                "EXIT=0\n",
                encoding="utf-8",
            )
            (runs / "phase9-issue1004-r2-minimal.md").write_text(
                "---\nverdict: propose\n---\n"
                f"{final_marker}\n",
                encoding="utf-8",
            )

            marker = read_worker_terminal_marker(log)

            self.assertTrue(marker.found)
            self.assertEqual(marker.marker, final_marker)
            self.assertEqual(marker.source, "artifact")
            self.assertEqual(marker.reason, "")

    def test_verbose_solver_conflict_fails_closed_when_companion_has_earlier_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self.repo(tmp)
            log = logs / "phase9-issue1004-r2-minimal.log"
            early_marker = "SOLVER_DONE:minimal:propose:early-release-proof"
            final_marker = "SOLVER_DONE:minimal:propose:sync-claude-skill-mirror"
            log.write_text(
                f"{early_marker}\n"
                "reasoning\n"
                f"{final_marker}\n"
                "EXIT=0\n",
                encoding="utf-8",
            )
            (runs / "phase9-issue1004-r2-minimal.md").write_text(
                f"{early_marker}\n",
                encoding="utf-8",
            )

            marker = read_worker_terminal_marker(log)

            self.assertFalse(marker.found)
            self.assertEqual(marker.reason, "duplicate_or_conflicting_log_marker")

    def test_verbose_solver_conflict_fails_closed_when_companion_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, _runs = self.repo(tmp)
            log = logs / "phase9-issue1005-r1-delete.log"
            log.write_text(
                "SOLVER_DONE:delete:propose:draft\n"
                "SOLVER_DONE:delete:abstain:final\n"
                "EXIT=0\n",
                encoding="utf-8",
            )

            marker = read_worker_terminal_marker(log)

            self.assertFalse(marker.found)
            self.assertEqual(marker.reason, "duplicate_or_conflicting_log_marker")

    def test_verbose_solver_conflict_fails_closed_when_companion_has_distinct_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self.repo(tmp)
            log = logs / "phase9-issue1005-r1-delete.log"
            final_marker = "SOLVER_DONE:delete:abstain:final"
            log.write_text(
                "SOLVER_DONE:delete:propose:draft\n"
                f"{final_marker}\n"
                "EXIT=0\n",
                encoding="utf-8",
            )
            (runs / "phase9-issue1005-r1-delete.md").write_text(
                f"{final_marker}\n"
                "SOLVER_DONE:delete:false-positive:other\n",
                encoding="utf-8",
            )

            marker = read_worker_terminal_marker(log)

            self.assertFalse(marker.found)
            self.assertEqual(marker.reason, "duplicate_or_conflicting_log_marker")
            self.assertNotEqual(marker.source, "artifact")

    def test_solver_expected_role_malformed_or_conflicting_without_companion_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, _runs = self.repo(tmp)
            conflict = logs / "phase9-issue1005-r1-delete.log"
            conflict.write_text(
                "SOLVER_DONE:minimal:propose:other-role-noise\n"
                "SOLVER_DONE:delete:propose:draft\n"
                "SOLVER_DONE:delete:abstain:final\n"
                "EXIT=0\n",
                encoding="utf-8",
            )
            malformed = logs / "phase9-issue1004-r2-minimal.log"
            malformed.write_text(
                "SOLVER_DONE:minimal:propose:draft\n"
                "SOLVER_DONE:minimal:<verdict>:<summary>\n"
                "EXIT=0\n",
                encoding="utf-8",
            )

            conflict_marker = read_worker_terminal_marker(conflict)
            malformed_marker = read_worker_terminal_marker(malformed)

            self.assertFalse(conflict_marker.found)
            self.assertEqual(conflict_marker.reason, "duplicate_or_conflicting_log_marker")
            self.assertFalse(malformed_marker.found)
            self.assertEqual(malformed_marker.reason, "malformed_log_marker")

    def test_non_solver_log_conflict_does_not_use_companion_disambiguation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self.repo(tmp)
            log = logs / "implement-issue-1005.log"
            log.write_text(
                "IMPLEMENT_DONE:issue-1005:partial\n"
                "IMPLEMENT_DONE:issue-1005:ok\n"
                "EXIT=0\n",
                encoding="utf-8",
            )
            (runs / "implement-issue-1005.md").write_text(
                "IMPLEMENT_DONE:issue-1005:ok\n",
                encoding="utf-8",
            )

            marker = read_worker_terminal_marker(log)

            self.assertFalse(marker.found)
            self.assertEqual(marker.reason, "duplicate_or_conflicting_log_marker")

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
            solver_marker = read_worker_terminal_marker(solver)
            self.assertTrue(solver_marker.found)
            self.assertEqual(solver_marker.marker, "SOLVER_DONE:delete:abstain:no-current-deletion")
            self.assertEqual(solver_marker.source, "log")

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
