import tempfile
import unittest
from pathlib import Path

import subprocess

from codex_refactor_loop.implement_lifecycle import (
    _implement_run_artifact_done_marker,
    classify_implement_attempt,
    implement_attempt_is_terminal_or_noop_completion,
)


class ImplementArtifactMarkerFallbackTests(unittest.TestCase):
    """The success-aware implement-lifecycle predicate must recover an
    IMPLEMENT_DONE:ok marker from the run artifact when a clean-exit implement
    worker emitted it only there (not the log tail), so readiness does not
    re-dispatch and overwrite an already-complete implement. The fallback is
    scoped: only :ok is accepted, so partial/failed/missing attempts still
    re-dispatch for recovery — preserving the r17/r18 markerless-redispatch
    contract for the true-failure case."""

    def _repo(self, tmp: str) -> tuple[Path, Path]:
        repo = Path(tmp)
        logs = repo / ".refactor-loop" / "logs"
        runs = repo / ".refactor-loop" / "runs"
        logs.mkdir(parents=True)
        runs.mkdir(parents=True)
        return logs, runs

    def test_recovers_ok_marker_from_run_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self._repo(tmp)
            (runs / "implement-issue-421.md").write_text(
                "body\n⟦AI:AUTO-LOOP⟧\nIMPLEMENT_DONE:issue-421:ok\n", encoding="utf-8"
            )
            (logs / "implement-issue-421.log").write_text("worker output\nEXIT=0\n", encoding="utf-8")
            self.assertEqual(
                _implement_run_artifact_done_marker(logs / "implement-issue-421.log"),
                "IMPLEMENT_DONE:issue-421:ok",
            )

    def test_artifact_partial_is_not_recovered_for_publish_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self._repo(tmp)
            (runs / "implement-issue-777.md").write_text("IMPLEMENT_DONE:issue-777:partial\n", encoding="utf-8")
            (logs / "implement-issue-777.log").write_text("worker output\nEXIT=0\n", encoding="utf-8")
            self.assertEqual(_implement_run_artifact_done_marker(logs / "implement-issue-777.log"), "")

    def test_missing_and_non_implement_stay_redispatchable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, _runs = self._repo(tmp)
            # missing artifact -> empty (true-failure markerless still re-dispatches)
            (logs / "implement-issue-888.log").write_text("worker output\nEXIT=0\n", encoding="utf-8")
            self.assertEqual(_implement_run_artifact_done_marker(logs / "implement-issue-888.log"), "")
            # non-implement log -> empty (scope guard)
            (logs / "audit-iter-9.log").write_text("worker output\nEXIT=0\n", encoding="utf-8")
            self.assertEqual(_implement_run_artifact_done_marker(logs / "audit-iter-9.log"), "")

    def test_clean_blocked_or_partial_marker_is_terminal_not_redispatch(self) -> None:
        for status in ("blocked", "partial"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                logs, _runs = self._repo(tmp)
                repo = Path(tmp)
                log = logs / "implement-issue-537.log"
                log.write_text(f"IMPLEMENT_DONE:issue-537:{status}\nEXIT=0\n", encoding="utf-8")

                state = classify_implement_attempt(repo_root=repo, log_path=log)

                self.assertTrue(state.terminal_non_ok)
                self.assertFalse(state.redispatch)
                self.assertFalse(state.publish_ready)
                self.assertEqual(state.marker, f"IMPLEMENT_DONE:issue-537:{status}")

    def test_clean_ok_stale_base_is_refresh_needed_not_redispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, _runs = self._repo(tmp)
            repo = Path(tmp)
            worktree = repo / ".worktrees" / "iter421-issue-421"
            worktree.mkdir(parents=True)
            log = logs / "implement-issue-421.log"
            log.write_text("IMPLEMENT_DONE:issue-421:ok\nEXIT=0\n", encoding="utf-8")

            def runner(command):
                if command[-2:] == ["--abbrev-ref", "HEAD"]:
                    return subprocess.CompletedProcess(command, 0, "refactor/iter421-issue-421\n", "")
                if command[-3:] == ["merge-base", "HEAD", "origin/integration"]:
                    return subprocess.CompletedProcess(command, 0, "old-base\n", "")
                if command[-2:] == ["--verify", "origin/integration"]:
                    return subprocess.CompletedProcess(command, 0, "new-base\n", "")
                if command[-2:] == ["diff", "--quiet"]:
                    return subprocess.CompletedProcess(command, 1, "", "")
                return subprocess.CompletedProcess(command, 0, "", "")

            state = classify_implement_attempt(
                repo_root=repo,
                action={"target_number": 421},
                log_path=log,
                integration_branch="integration",
                command_runner=runner,
            )

            self.assertTrue(state.refresh_needed)
            self.assertFalse(state.redispatch)
            self.assertEqual(state.reason, "stale_base")

    def test_clean_ok_stale_base_noop_is_terminal_noop_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, _runs = self._repo(tmp)
            repo = Path(tmp)
            worktree = repo / ".worktrees" / "iter581-issue-581"
            worktree.mkdir(parents=True)
            log = logs / "implement-issue-581.log"
            log.write_text("0 LOC 收口，没有修改任何仓库源码\nIMPLEMENT_DONE:issue-581:ok\nEXIT=0\n", encoding="utf-8")

            def runner(command):
                if command[-2:] == ["--abbrev-ref", "HEAD"]:
                    return subprocess.CompletedProcess(command, 0, "refactor/iter581-issue-581\n", "")
                if command[-3:] == ["merge-base", "HEAD", "origin/integration"]:
                    return subprocess.CompletedProcess(command, 0, "old-base\n", "")
                if command[-2:] == ["--verify", "origin/integration"]:
                    return subprocess.CompletedProcess(command, 0, "new-base\n", "")
                if command[-2:] == ["status", "--porcelain"]:
                    return subprocess.CompletedProcess(command, 0, "", "")
                if command[-2:] == ["diff", "--quiet"]:
                    return subprocess.CompletedProcess(command, 0, "", "")
                return subprocess.CompletedProcess(command, 0, "", "")

            state = classify_implement_attempt(
                repo_root=repo,
                action={"target_number": 581},
                log_path=log,
                integration_branch="integration",
                command_runner=runner,
            )

            self.assertTrue(state.redispatch)
            self.assertEqual(state.reason, "empty_scoped_diff")
            self.assertTrue(implement_attempt_is_terminal_or_noop_completion(state))

    def test_staged_only_worker_diff_is_publish_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, _runs = self._repo(tmp)
            repo = Path(tmp)
            worktree = repo / ".worktrees" / "iter553-issue-553"
            worktree.mkdir(parents=True)
            log = logs / "implement-issue-553.log"
            log.write_text("IMPLEMENT_DONE:issue-553:ok\nEXIT=0\n", encoding="utf-8")

            def runner(command):
                if command[-2:] == ["--abbrev-ref", "HEAD"]:
                    return subprocess.CompletedProcess(command, 0, "refactor/iter553-issue-553\n", "")
                if command[-3:] == ["merge-base", "HEAD", "origin/integration"]:
                    return subprocess.CompletedProcess(command, 0, "base\n", "")
                if command[-2:] == ["--verify", "origin/integration"]:
                    return subprocess.CompletedProcess(command, 0, "base\n", "")
                if command[-2:] == ["status", "--porcelain"]:
                    return subprocess.CompletedProcess(command, 0, "M  staged.py\n", "")
                if command[-2:] == ["diff", "--quiet"]:
                    raise AssertionError("staged publish-ready output must not be classified from unstaged diff")
                return subprocess.CompletedProcess(command, 0, "", "")

            state = classify_implement_attempt(
                repo_root=repo,
                action={"target_number": 553},
                log_path=log,
                integration_branch="integration",
                command_runner=runner,
            )

            self.assertTrue(state.publish_ready)
            self.assertFalse(state.redispatch)

    def test_duplicate_log_marker_uses_clean_artifact_marker_for_publish_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self._repo(tmp)
            repo = Path(tmp)
            worktree = repo / ".worktrees" / "iter553-issue-553"
            worktree.mkdir(parents=True)
            (runs / "implement-issue-553.md").write_text(
                "summary\n⟦AI:AUTO-LOOP⟧\nIMPLEMENT_DONE:issue-553:ok\n", encoding="utf-8"
            )
            log = logs / "implement-issue-553.log"
            log.write_text(
                "body\n⟦AI:AUTO-LOOP⟧\nIMPLEMENT_DONE:issue-553:ok\n"
                "later echo\nIMPLEMENT_DONE:issue-553:ok\nEXIT=0\n",
                encoding="utf-8",
            )

            def runner(command):
                if command[-2:] == ["--abbrev-ref", "HEAD"]:
                    return subprocess.CompletedProcess(command, 0, "refactor/iter553-issue-553\n", "")
                if command[-3:] == ["merge-base", "HEAD", "origin/integration"]:
                    return subprocess.CompletedProcess(command, 0, "base\n", "")
                if command[-2:] == ["--verify", "origin/integration"]:
                    return subprocess.CompletedProcess(command, 0, "base\n", "")
                if command[-2:] == ["status", "--porcelain"]:
                    return subprocess.CompletedProcess(command, 0, "M  staged.py\n", "")
                return subprocess.CompletedProcess(command, 0, "", "")

            state = classify_implement_attempt(
                repo_root=repo,
                action={"target_number": 553},
                log_path=log,
                integration_branch="integration",
                command_runner=runner,
            )

            self.assertTrue(state.publish_ready)
            self.assertEqual(state.marker, "IMPLEMENT_DONE:issue-553:ok")
            self.assertEqual(state.head_ref, "refactor/iter553-issue-553")


if __name__ == "__main__":
    unittest.main()
