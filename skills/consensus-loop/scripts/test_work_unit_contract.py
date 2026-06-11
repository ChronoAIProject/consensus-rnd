#!/usr/bin/env python3
"""Contract tests for existing work-unit identity/provenance fields."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
SKILL_MD = SKILL_ROOT / "SKILL.md"
PACKAGE_ROOT = SCRIPT_DIR / "codex_refactor_loop"
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.phase9.router import MetaJudgePromptContext  # noqa: E402
from codex_refactor_loop.transition_assessment import ALLOWED_PRODUCERS, TransitionAssessment  # noqa: E402


def read_skill() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


def section_after_heading(markdown: str, heading: str) -> str:
    pattern = re.compile(rf"(?m)^## {re.escape(heading)}$")
    match = pattern.search(markdown)
    if not match:
        raise AssertionError(f"missing markdown heading: {heading}")
    tail = markdown[match.start() :]
    next_match = re.search(r"(?m)^##\s+", tail[1:])
    return tail[: next_match.start() + 1] if next_match else tail


def work_unit_contract_surface(markdown: str) -> str:
    start_pattern = re.compile(r"(?m)^## Work-unit contract$")
    end_pattern = re.compile(r"(?m)^<a id=\"specialized-state-artifacts\"></a>$")
    start = start_pattern.search(markdown)
    end = end_pattern.search(markdown)
    if not start or not end or end.start() <= start.start():
        raise AssertionError("missing work-unit contract surface")
    return markdown[start.start() : end.start()]


class WorkUnitContractTests(unittest.TestCase):
    def test_skill_contract_keeps_existing_work_unit_fields_and_producers(self) -> None:
        contract = work_unit_contract_surface(read_skill())

        for needle in (
            "`work_unit_id`: canonical work-unit identity",
            "`kind`: work-unit type",
            "`producer`: source producer",
            "`source_ref`: stable pointer to the source material",
            "`scope_paths`",
            "`old_pattern`",
            "`new_principle`",
            "`verification_hints`",
            "`dependencies`",
            "`risk`",
            "`leverage`",
            "`work_unit_id == id == cluster_id == legacy_cluster_id`",
            "`WORK_UNIT_ID=$CLUSTER_ID`",
            "`audit`",
            "`manual-issue`",
            "`work_unit_id: issue-<N>`",
            "`producer: manual-issue`",
            "`source_ref: gh-issue-<N>`",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, contract)

    def test_work_unit_contract_does_not_authorize_second_runtime_surface(self) -> None:
        contract = work_unit_contract_surface(read_skill())

        for forbidden in (
            "migrated queue containers",
            "normalizer helpers",
            "root state migrations",
            "producer abstractions",
            "registry helpers",
            "envelope wrappers",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, contract)

        package_sources = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE_ROOT.rglob("*.py"))
        for token in (
            "WorkUnitRegistry",
            "WorkUnitEnvelope",
            "WorkUnitNormalizer",
            "work_unit_registry",
            "work_unit_envelope",
            "work_unit_normalizer",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, package_sources)

    def test_manual_issue_prompt_context_projects_existing_provenance_fields(self) -> None:
        context = MetaJudgePromptContext(
            issue="838",
            round=1,
            solver_paths={
                "minimal": ".refactor-loop/runs/phase9-issue838-r1-minimal.md",
                "structural": ".refactor-loop/runs/phase9-issue838-r1-structural.md",
                "delete": ".refactor-loop/runs/phase9-issue838-r1-delete.md",
            },
            output_path=".refactor-loop/runs/phase9-issue838-r1-judge.md",
            transition_assessment=TransitionAssessment("unknown", 0.0),
        )

        self.assertEqual("issue-838", context.work_unit_id)
        self.assertEqual("manual-issue (prompt-only provenance)", context.work_unit_producer)
        self.assertEqual("gh-issue-838", context.work_unit_source_ref)
        self.assertEqual(2, context.convergence_round_plus_one)

    def test_transition_sidecar_producer_values_do_not_expand_work_unit_producers(self) -> None:
        self.assertEqual(frozenset({"audit", "manual-issue"}), ALLOWED_PRODUCERS)


if __name__ == "__main__":
    unittest.main()
