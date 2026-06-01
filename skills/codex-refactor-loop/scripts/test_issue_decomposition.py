#!/usr/bin/env python3
"""Behavior tests for IssueDecompositionPlan validation."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.issue_decomposition import IssueDecompositionError, load_issue_decomposition_plan


class IssueDecompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="issue-decomposition-test-"))
        (self.tmp / ".refactor-loop" / "runs").mkdir(parents=True)
        (self.tmp / ".refactor-loop" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.tmp)
        self.consensus = ".refactor-loop/runs/phase9-issue403-r6-judge.md"
        (self.tmp / self.consensus).write_text("consensus artifact\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_body(self, name: str, *, parent: int = 403, scope: str, non_goals: str) -> str:
        path = f".refactor-loop/runs/{name}.md"
        (self.tmp / path).write_text(
            "\n".join(
                [
                    "## child issue",
                    "",
                    f"Parent issue: #{parent}",
                    f"Source consensus artifact: {Path(self.consensus).name}",
                    f"Scope: {scope}",
                    f"Non-goals: {non_goals}",
                    "",
                    "<details>",
                    "<summary>内联 artifact 1: decision.md</summary>",
                    "",
                    "```markdown",
                    "raw decision",
                    "```",
                    "",
                    "</details>",
                    "",
                    "⟦AI:AUTO-LOOP⟧",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return path

    def write_parent_comment(self, parent: int = 403) -> str:
        path = ".refactor-loop/runs/parent-comment.md"
        (self.tmp / path).write_text(
            f"Parent issue: #{parent}\n\nTracking child design issues.\n\n⟦AI:AUTO-LOOP⟧\n",
            encoding="utf-8",
        )
        return path

    def write_plan(self, payload: dict) -> Path:
        path = self.tmp / ".refactor-loop" / "runs" / "decomposition-plan.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def valid_payload(self) -> dict:
        return {
            "schema": "IssueDecompositionPlan",
            "parent_issue": 403,
            "source_consensus_artifact": self.consensus,
            "children": [
                {
                    "slug": "first-child",
                    "title": "First child",
                    "scope": "First bounded scope",
                    "non_goals": "No parent lifecycle mutation",
                    "body_artifact_path": self.write_body(
                        "child-one",
                        scope="First bounded scope",
                        non_goals="No parent lifecycle mutation",
                    ),
                },
                {
                    "slug": "second-child",
                    "title": "Second child",
                    "scope": "Second bounded scope",
                    "non_goals": "No public issue factory",
                    "body_artifact_path": self.write_body(
                        "child-two",
                        scope="Second bounded scope",
                        non_goals="No public issue factory",
                    ),
                },
            ],
            "parent_update": {"comment_artifact_path": self.write_parent_comment()},
        }

    def test_valid_plan_requires_parent_at_least_two_children_body_artifacts_parent_link_and_final_sentinel(self) -> None:
        plan = load_issue_decomposition_plan(self.ctx, self.write_plan(self.valid_payload()))

        self.assertEqual(plan.schema, "IssueDecompositionPlan")
        self.assertEqual(plan.parent_issue, 403)
        self.assertEqual(len(plan.children), 2)
        self.assertEqual(plan.children[0].slug, "first-child")
        self.assertEqual(plan.parent_comment_artifact_path, ".refactor-loop/runs/parent-comment.md")

    def test_rejects_command_like_lifecycle_fields_absolute_paths_and_lifecycle_authority(self) -> None:
        forbidden_fields = (
            "cmd",
            "argv",
            "shell",
            "gh",
            "git",
            "close",
            "assignee",
            "milestone",
            "lifecycle_owner",
            "lifecycle_authority",
        )
        for field in forbidden_fields:
            with self.subTest(field=field):
                payload = self.valid_payload()
                payload[field] = "forbidden"
                with self.assertRaisesRegex(IssueDecompositionError, "forbidden lifecycle/command fields"):
                    load_issue_decomposition_plan(self.ctx, self.write_plan(payload))

        payload = self.valid_payload()
        payload["children"][0]["body_artifact_path"] = str((self.tmp / ".refactor-loop/runs/child-one.md").resolve())
        with self.assertRaisesRegex(IssueDecompositionError, "repo-relative"):
            load_issue_decomposition_plan(self.ctx, self.write_plan(payload))

        payload = self.valid_payload()
        payload["children"][0]["body_artifact_path"] = "../outside.md"
        with self.assertRaisesRegex(IssueDecompositionError, "repo-relative"):
            load_issue_decomposition_plan(self.ctx, self.write_plan(payload))

        payload = self.valid_payload()
        payload["children"] = payload["children"][:1]
        with self.assertRaisesRegex(IssueDecompositionError, "at least two children"):
            load_issue_decomposition_plan(self.ctx, self.write_plan(payload))


if __name__ == "__main__":
    unittest.main()
