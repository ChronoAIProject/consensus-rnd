#!/usr/bin/env python3
"""Behavior tests for controller_lib.sh human-label helper."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]
CONTROLLER_LIB = REPO_ROOT / "skills" / "codex-refactor-loop" / "scripts" / "controller_lib.sh"
REPO_SLUG_LIB = REPO_ROOT / "skills" / "codex-refactor-loop" / "scripts" / "repo_slug.sh"
HUMAN_LABEL = "👤 human:需-maintainer-决策"
VALID_MARKER = "META_RESOLVED:escalate-human:human-label-semantics-guard"


class ControllerLibHumanLabelPrHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.controller = self.root / "controller_lib.sh"
        self.gh_log = self.root / "gh.log"
        self.directive_dir = self.root / ".refactor-loop" / "runs" / "maintainer-directives"
        self.directive_dir.mkdir(parents=True)
        shutil.copy2(CONTROLLER_LIB, self.controller)
        shutil.copy2(REPO_SLUG_LIB, self.root / "repo_slug.sh")

        fake_gh = self.root / "gh"
        fake_gh.write_text(
            '#!/bin/bash\n'
            'echo "$@" >> "$FAKE_GH_LOG"\n',
            encoding="utf-8",
        )
        fake_gh.chmod(0o755)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_helper(self, *args: str, marker_env: str = "") -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.root}{os.pathsep}{env['PATH']}",
                "REPO_ROOT": str(self.root),
                "FAKE_GH_LOG": str(self.gh_log),
                "GH_REPO_SLUG": "test-owner/test-repo",
                "GH_OWNER": "",
                "GH_REPO_NAME": "",
                "GH_REPO": "",
                "HUMAN_LABEL_SOURCE_MARKER": marker_env,
            }
        )
        return subprocess.run(
            ["bash", "-c", 'source "$CONTROLLER_LIB"; apply_human_label_or_skip "$@"', "bash", *args],
            env={**env, "CONTROLLER_LIB": str(self.controller)},
            text=True,
            capture_output=True,
            check=False,
        )

    def write_directive(self, name: str, body: str) -> None:
        (self.directive_dir / name).write_text(body, encoding="utf-8")

    def gh_calls(self) -> list[str]:
        if not self.gh_log.exists():
            return []
        return self.gh_log.read_text(encoding="utf-8").splitlines()

    def assert_gh_not_called(self) -> None:
        self.assertEqual(self.gh_calls(), [])

    def assert_human_label_applied_once(self) -> None:
        self.assertEqual(self.gh_calls(), [f"pr edit 55 --repo test-owner/test-repo --add-label {HUMAN_LABEL}"])

    def test_apply_human_label_skips_when_directive_matches_pr(self) -> None:
        self.write_directive(
            "2026-05-26-pr.md",
            "Maintainer already authorized this path for PR #55.\n",
        )

        result = self.run_helper("55", VALID_MARKER, "human-label-semantics-guard")

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("skip-label", result.stdout)
        self.assert_gh_not_called()

    def test_apply_human_label_accepts_meta_resolved_marker_for_pr(self) -> None:
        result = self.run_helper("55", VALID_MARKER, "human-label-semantics-guard")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_human_label_applied_once()

    def test_apply_human_label_when_directives_dir_absent(self) -> None:
        """Helper handles missing maintainer-directives directory gracefully."""
        self.directive_dir.rmdir()

        result = self.run_helper("55", "META_RESOLVED:escalate-human:reason", "reason")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_human_label_applied_once()

    def test_apply_human_label_applies_when_directive_unrelated(self) -> None:
        self.write_directive(
            "2026-05-26-other.md",
            "Maintainer directive for a different PR and unrelated topic.\n",
        )

        result = self.run_helper("55", VALID_MARKER, "human-label-semantics-guard")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_human_label_applied_once()

    def test_apply_human_label_no_substring_false_match(self) -> None:
        """Directive containing PR #555 must NOT match helper run for PR 55."""
        self.write_directive(
            "2026-05-26-pr-555.md",
            "Maintainer directive for PR #555 covers a different path.\n",
        )

        result = self.run_helper("55", VALID_MARKER, "human-label-semantics-guard")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_human_label_applied_once()

    def test_apply_human_label_skips_when_topic_in_directive_body(self) -> None:
        self.write_directive(
            "2026-05-26-topic.md",
            "The human-label-semantics-guard route is covered by maintainer directive.\n",
        )

        result = self.run_helper("55", VALID_MARKER, "human-label-semantics-guard")

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("skip-label", result.stdout)
        self.assert_gh_not_called()

    def test_apply_human_label_partial_topic_not_authorized(self) -> None:
        """Directive containing a topic fragment must not authorize the full topic."""
        self.write_directive(
            "2026-05-26-topic-fragment.md",
            "Maintainer directive mentions only the fragment concur.\n",
        )

        result = self.run_helper("55", "META_RESOLVED:escalate-human:concurrency", "concurrency")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_human_label_applied_once()

    def test_apply_human_label_missing_arg_returns_2(self) -> None:
        result = self.run_helper("")

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("apply_human_label_or_skip: missing pr_number", result.stderr)
        self.assert_gh_not_called()

    def test_apply_human_label_rejects_missing_marker(self) -> None:
        result = self.run_helper("55")

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("ERROR: apply_human_label_or_skip requires META_RESOLVED:escalate-human marker source", result.stderr)
        self.assert_gh_not_called()

    def test_apply_human_label_rejects_meta_judge_marker(self) -> None:
        result = self.run_helper("55", "META_JUDGE_DONE:escalate:philosophy:tier-boundary", "tier-boundary")

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("ERROR: apply_human_label_or_skip requires META_RESOLVED:escalate-human marker source", result.stderr)
        self.assert_gh_not_called()

    def test_apply_human_label_rejects_fix_blocked_marker(self) -> None:
        result = self.run_helper("55", "FIX_BLOCKED:55:round-2:human-decision:rename-api", "rename-api")

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("ERROR: apply_human_label_or_skip requires META_RESOLVED:escalate-human marker source", result.stderr)
        self.assert_gh_not_called()

    def test_apply_human_label_accepts_meta_resolved_marker_from_env(self) -> None:
        result = self.run_helper("55", "human-label-semantics-guard", marker_env=VALID_MARKER)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_human_label_applied_once()


if __name__ == "__main__":
    unittest.main()
