import tempfile
import unittest
from pathlib import Path

from codex_refactor_loop.implement_lifecycle import _implement_run_artifact_done_marker


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
            self.assertEqual(
                _implement_run_artifact_done_marker(logs / "implement-issue-421.log"),
                "IMPLEMENT_DONE:issue-421:ok",
            )

    def test_partial_and_missing_and_non_implement_stay_redispatchable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs, runs = self._repo(tmp)
            # partial is NOT recovered (only :ok), so partial attempts still re-dispatch
            (runs / "implement-issue-777.md").write_text("IMPLEMENT_DONE:issue-777:partial\n", encoding="utf-8")
            self.assertEqual(_implement_run_artifact_done_marker(logs / "implement-issue-777.log"), "")
            # missing artifact -> empty (true-failure markerless still re-dispatches)
            self.assertEqual(_implement_run_artifact_done_marker(logs / "implement-issue-888.log"), "")
            # non-implement log -> empty (scope guard)
            self.assertEqual(_implement_run_artifact_done_marker(logs / "audit-iter-9.log"), "")


if __name__ == "__main__":
    unittest.main()
