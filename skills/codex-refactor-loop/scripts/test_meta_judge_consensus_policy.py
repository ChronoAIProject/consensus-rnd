#!/usr/bin/env python3
"""Behavior tests for the documented meta-judge solver consensus policy."""

from __future__ import annotations

from dataclasses import dataclass
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__)
SKILL_ROOT = SCRIPT_PATH.parents[1]


@dataclass(frozen=True)
class SolverOutput:
    role: str
    verdict: str
    framing: str = ""
    concrete_objection: bool = False


def solver_consensus(outputs: tuple[SolverOutput, SolverOutput, SolverOutput]) -> bool:
    """Test mirror of the SKILL.md/meta-judge.md consensus truth table."""
    by_role = {output.role: output for output in outputs}
    if set(by_role) != {"minimal", "structural", "delete"}:
        return False

    if all(output.verdict == "propose" for output in outputs):
        return len({output.framing for output in outputs}) == 1

    minimal = by_role["minimal"]
    structural = by_role["structural"]
    delete = by_role["delete"]
    return (
        minimal.verdict == "propose"
        and structural.verdict == "propose"
        and delete.verdict == "abstain"
        and minimal.framing == structural.framing
        and not delete.concrete_objection
    )


class MetaJudgeConsensusPolicyBehaviorTests(unittest.TestCase):
    def test_delete_abstain_without_concrete_objection_is_non_dissent_for_same_bounded_plan(self) -> None:
        self.assertTrue(
            solver_consensus(
                (
                    SolverOutput("minimal", "propose", "bounded-router-fix"),
                    SolverOutput("structural", "propose", "bounded-router-fix"),
                    SolverOutput("delete", "abstain", concrete_objection=False),
                )
            )
        )

    def test_delete_abstain_with_concrete_objection_is_not_consensus(self) -> None:
        self.assertFalse(
            solver_consensus(
                (
                    SolverOutput("minimal", "propose", "bounded-router-fix"),
                    SolverOutput("structural", "propose", "bounded-router-fix"),
                    SolverOutput("delete", "abstain", concrete_objection=True),
                )
            )
        )

    def test_non_delete_abstain_remains_not_consensus(self) -> None:
        self.assertFalse(
            solver_consensus(
                (
                    SolverOutput("minimal", "abstain"),
                    SolverOutput("structural", "propose", "bounded-router-fix"),
                    SolverOutput("delete", "propose", "bounded-router-fix"),
                )
            )
        )

    def test_delete_abstain_does_not_save_disagreeing_proposals(self) -> None:
        self.assertFalse(
            solver_consensus(
                (
                    SolverOutput("minimal", "propose", "bounded-router-fix"),
                    SolverOutput("structural", "propose", "new-router-abstraction"),
                    SolverOutput("delete", "abstain", concrete_objection=False),
                )
            )
        )


class MetaJudgeConsensusPolicySourceRegressionTests(unittest.TestCase):
    def test_skill_and_prompt_share_the_bounded_exception_contract(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        prompt = (SKILL_ROOT / "prompts" / "meta-judge.md").read_text(encoding="utf-8")
        combined = "\n".join((skill, prompt))

        for token in (
            "Solver consensus means either 3/3 same framing or the bounded delete-abstain non-dissent exception below.",
            "if minimal and structural propose the same bounded plan and delete abstains with no concrete objection",
            "Any other mixed propose/abstain/escalate:no-plan case goes through convergence",
            "Delete abstain with no concrete objection against the same bounded plan",
            "otherwise it remains not unanimous",
        ):
            with self.subTest(token=token):
                self.assertIn(token, combined)

    def test_prompt_header_no_longer_claims_absolute_three_of_three_gate(self) -> None:
        prompt = (SKILL_ROOT / "prompts" / "meta-judge.md").read_text(encoding="utf-8")

        self.assertNotIn("Policy: **3/3 unanimous + meta-judge consensus** is the sole gate", prompt)
        self.assertNotIn("The loop iterates until 3/3 unanimous consensus", prompt)


if __name__ == "__main__":
    unittest.main()
