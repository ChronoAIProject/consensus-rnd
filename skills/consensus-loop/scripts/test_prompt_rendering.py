#!/usr/bin/env python3
"""Behavior tests for shared prompt render-time binding."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContextError
from codex_refactor_loop.prompt_rendering import render_prompt_text

SKILL_ROOT = SCRIPT_DIR.parent


class PromptRenderingTests(unittest.TestCase):
    def test_controller_aliases_and_prompt_contract_language_resolve_together(self) -> None:
        template = (
            "{{work_unit_id}}\n"
            "{{cluster_id}}\n"
            "{{iteration}}\n"
            "{{worktree_path}}\n"
            "{{branch}}\n"
            "{{old_pattern}}\n"
            "{{new_principle}}\n"
            "{{scope_paths}}\n"
            "{{verification_hints}}\n"
            "{{GITHUB_POST_RULES_CONTRACT}}\n"
        )

        rendered = render_prompt_text(
            template,
            skill_root=SKILL_ROOT,
            base_env={},
            host_env={"HOST_WORK_LANGUAGE": "zh"},
            values={
                "WORK_UNIT_ID": "issue-740",
                "CLUSTER_ID": "issue-740",
                "ITERATION": "740",
                "WORKTREE_PATH": "/tmp/worktree",
                "BRANCH": "refactor/iter740-issue-740",
                "OLD_PATTERN": "private phase9 rendering",
                "NEW_PRINCIPLE": "shared prompt rendering",
                "SCOPE_PATHS": "prompt_rendering.py",
                "VERIFICATION_HINTS": "pytest prompt rendering",
            },
        )

        for expected in (
            "issue-740",
            "740",
            "/tmp/worktree",
            "refactor/iter740-issue-740",
            "private phase9 rendering",
            "shared prompt rendering",
            "prompt_rendering.py",
            "pytest prompt rendering",
            "follow `zh`",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, rendered)
        self.assertNotIn("${HOST_WORK_LANGUAGE}", rendered)
        self.assertNotIn("$HOST_WORK_LANGUAGE", rendered)
        self.assertNotIn("{{GITHUB_POST_RULES_CONTRACT}}", rendered)

    def test_invalid_host_work_language_fails_closed(self) -> None:
        with self.assertRaisesRegex(LoopContextError, "invalid HOST_WORK_LANGUAGE"):
            render_prompt_text(
                "${HOST_WORK_LANGUAGE}",
                skill_root=SKILL_ROOT,
                base_env={},
                host_env={"HOST_WORK_LANGUAGE": "fr"},
            )


if __name__ == "__main__":
    unittest.main()
