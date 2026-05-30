#!/usr/bin/env python3
"""Behavior tests for HOST_WORKFLOW_SPEC validation."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext  # noqa: E402
from codex_refactor_loop.workflow_spec import (  # noqa: E402
    WorkflowSpecError,
    load_validated_workflow_spec,
)


class HostWorkflowSpecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name).resolve()
        (self.repo / ".refactor-loop").mkdir()
        (self.repo / "prompts").mkdir()
        (self.repo / "prompts" / "host-solver.md").write_text("host solver\n", encoding="utf-8")
        (self.repo / "prompts" / "host-judge.md").write_text("host judge\n", encoding="utf-8")
        (self.repo / "workflow.json").write_text(json.dumps(self.valid_spec()), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def ctx(self, value: str = "") -> LoopContext:
        env = {"REPO_ROOT": str(self.repo), "HOST_WORKFLOW_SPEC": value}
        return LoopContext.load(repo_root=self.repo, env=env)

    def valid_spec(self) -> dict:
        return {
            "stages": [
                {
                    "slug": "host:discovery",
                    "title": "Discovery",
                    "contract": "Host status projection only.",
                    "detail_anchor": "host-discovery",
                }
            ],
            "events": [{"name": "host:template-ready", "stage": "host:discovery", "status": "host:queued"}],
            "work_unit_kinds": [{"name": "host:design-work"}],
            "prompt_bindings": {
                "host:solver": "prompts/host-solver.md",
                "host:judge": "prompts/host-judge.md",
            },
            "roles": [
                {"name": "host:solver-a", "prompt_binding": "host:solver"},
                {"name": "host:solver-b", "prompt_binding": "host:solver"},
                {"name": "host:solver-c", "prompt_binding": "host:solver"},
                {"name": "host:judge", "prompt_binding": "host:judge"},
            ],
            "consensus_policies": [
                {
                    "name": "host:policy",
                    "solver_roles": ["host:solver-a", "host:solver-b", "host:solver-c"],
                    "judge_role": "host:judge",
                    "peer_output_isolation": True,
                    "marker_families": ["SOLVER_DONE", "META_JUDGE_DONE", "META_RESOLVED"],
                    "stage": "host:discovery",
                }
            ],
            "issue_intake_mappings": [
                {
                    "name": "host:template",
                    "work_unit_kind": "host:design-work",
                    "producer": "host:github-template",
                    "stage": "host:discovery",
                    "prompt_binding": "host:solver",
                }
            ],
        }

    def write_spec(self, data: dict, name: str = "workflow.json") -> str:
        (self.repo / name).write_text(json.dumps(data), encoding="utf-8")
        return name

    def test_empty_host_workflow_spec_uses_builtin_refactor_parity(self) -> None:
        spec = load_validated_workflow_spec(self.ctx(""))

        self.assertTrue(spec.builtin)
        self.assertEqual(spec.work_unit_kinds, ("audit-cluster", "manual-work-unit"))
        self.assertEqual(spec.consensus_policies[0].solver_roles, ("minimal", "structural", "delete"))
        self.assertEqual(spec.consensus_policies[0].judge_role, "judge")
        self.assertEqual(spec.prompt_bindings["solver-minimal"], "prompts/solver-minimal.md")

    def test_repo_relative_spec_declares_host_event_stage_kind_and_prompt_binding(self) -> None:
        spec = load_validated_workflow_spec(self.ctx("workflow.json"))

        self.assertEqual(spec.stage_for_event("host:template-ready"), "host:discovery")
        self.assertIn("host:design-work", spec.work_unit_kinds)
        self.assertEqual(spec.prompt_binding_path("host:solver"), "prompts/host-solver.md")
        self.assertTrue(any(mapping.name == "host:template" for mapping in spec.issue_intake_mappings))

    def test_spec_rejects_absolute_parent_or_symlink_escape_paths(self) -> None:
        data = self.valid_spec()
        data["prompt_bindings"]["host:solver"] = "/tmp/outside.md"
        with self.assertRaisesRegex(WorkflowSpecError, "repo-relative"):
            load_validated_workflow_spec(self.ctx(self.write_spec(data)))

        data = self.valid_spec()
        data["prompt_bindings"]["host:solver"] = "../outside.md"
        with self.assertRaisesRegex(WorkflowSpecError, "repo-relative"):
            load_validated_workflow_spec(self.ctx(self.write_spec(data)))

        outside = Path(self.tmp.name).parent / "outside-host-prompt.md"
        outside.write_text("outside\n", encoding="utf-8")
        link = self.repo / "prompts" / "outside-link.md"
        link.symlink_to(outside)
        data = self.valid_spec()
        data["prompt_bindings"]["host:solver"] = "prompts/outside-link.md"
        with self.assertRaisesRegex(WorkflowSpecError, "symlink escapes REPO_ROOT"):
            load_validated_workflow_spec(self.ctx(self.write_spec(data)))

        outside_spec = Path(self.tmp.name).parent / "outside-workflow.json"
        outside_spec.write_text(json.dumps(self.valid_spec()), encoding="utf-8")
        spec_link = self.repo / "workflow-link.json"
        spec_link.symlink_to(outside_spec)
        with self.assertRaisesRegex(WorkflowSpecError, "symlink escapes REPO_ROOT"):
            load_validated_workflow_spec(self.ctx("workflow-link.json"))

    def test_spec_rejects_reserved_producers_stages_markers_and_cluster_alias(self) -> None:
        cases = []
        data = self.valid_spec()
        data["stages"][0]["slug"] = "design-consensus"
        cases.append(data)
        data = self.valid_spec()
        data["work_unit_kinds"][0]["name"] = "issue-219"
        cases.append(data)
        data = self.valid_spec()
        data["issue_intake_mappings"][0]["producer"] = "manual-issue"
        cases.append(data)
        data = self.valid_spec()
        data["consensus_policies"][0]["marker_families"] = ["SOLVER_DONE"]
        cases.append(data)

        for index, data in enumerate(cases):
            with self.subTest(index=index):
                with self.assertRaises(WorkflowSpecError):
                    load_validated_workflow_spec(self.ctx(self.write_spec(data, f"workflow-{index}.json")))

    def test_spec_rejects_lifecycle_or_command_fields(self) -> None:
        data = self.valid_spec()
        data["events"][0]["command"] = "gh issue close 1"

        with self.assertRaisesRegex(WorkflowSpecError, "forbidden lifecycle or command field"):
            load_validated_workflow_spec(self.ctx(self.write_spec(data)))

    def test_spec_rejects_consensus_policy_below_three_solvers_or_without_peer_isolation(self) -> None:
        data = self.valid_spec()
        data["consensus_policies"][0]["solver_roles"] = ["host:solver-a", "host:solver-b"]
        with self.assertRaisesRegex(WorkflowSpecError, "at least three independent solvers"):
            load_validated_workflow_spec(self.ctx(self.write_spec(data)))

        data = self.valid_spec()
        data["consensus_policies"][0]["peer_output_isolation"] = False
        with self.assertRaisesRegex(WorkflowSpecError, "peer_output_isolation"):
            load_validated_workflow_spec(self.ctx(self.write_spec(data)))


if __name__ == "__main__":
    unittest.main()
