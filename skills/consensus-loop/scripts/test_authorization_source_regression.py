#!/usr/bin/env python3
"""Focused source-regression checks for runtime authorization wording."""

from __future__ import annotations

import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
SKILL_ROOT = SCRIPT_PATH.parents[1]
REPO_ROOT = SCRIPT_PATH.parents[3]
SKILL_MD = SKILL_ROOT / "SKILL.md"
RUNTIME_EXCEPTIONS = SKILL_ROOT / "authorizations" / "runtime-exceptions.md"
WAKEUP_PLAN = SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py"
CONTROLLER_ACTIONS = SKILL_ROOT / "scripts" / "codex_refactor_loop" / "controller_actions.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class AuthorizationSourceRegressionTests(unittest.TestCase):
    def test_current_pr_noop_boundary_is_documented_without_new_authority(self) -> None:
        combined = "\n".join((read(SKILL_MD), read(RUNTIME_EXCEPTIONS)))
        for needle in (
            "`suppressed_reason=pr_already_open_current`",
            "returns 0 before diff/build/test/push/PR edit/reviewer dispatch",
            "Remote ref proof is read-only and bounded",
            "unknown or not-current evidence falls through to the existing publish path and is not success authority",
            "no public command bus",
            "no generic command fields",
            "no generic lifecycle actor",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, combined)

    def test_current_pr_noop_has_planner_and_helper_behavior_surfaces(self) -> None:
        wakeup_plan = read(WAKEUP_PLAN)
        controller_actions = read(CONTROLLER_ACTIONS)
        for needle in (
            "pr_already_open_current",
            "_current_implementation_pr_proof",
            "IMPLEMENTATION_PR_HEAD_VISIBILITY_ATTEMPTS",
        ):
            with self.subTest(planner=needle):
                self.assertIn(needle, wakeup_plan)
        for needle in (
            "_matching_current_implementation_pr",
            '["rev-parse", "HEAD"]',
            '["rev-parse", "--verify", f"refs/remotes/origin/{head_ref}"]',
            "return self.dispatch_reviewers",
        ):
            with self.subTest(helper=needle):
                self.assertIn(needle, controller_actions)


if __name__ == "__main__":
    unittest.main()
