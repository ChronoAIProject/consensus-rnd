#!/usr/bin/env python3
"""Source contract tests for the codex-refactor-loop entrypoint split."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__)
SKILL_ROOT = SCRIPT_PATH.parents[1]
SKILL_MD = SKILL_ROOT / "SKILL.md"
REFERENCE_MD = SKILL_ROOT / "REFERENCE.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def markdown_headings(text: str, level: int = 2) -> list[str]:
    prefix = "#" * level
    return [line for line in text.splitlines() if line.startswith(f"{prefix} ")]


def section_between(text: str, start_heading_re: str, end_heading_re: str) -> str:
    start = re.search(start_heading_re, text, flags=re.MULTILINE)
    end = re.search(end_heading_re, text[start.end() :] if start else "", flags=re.MULTILINE)
    if not start or not end:
        return ""
    return text[start.end() : start.end() + end.start()]


class SkillEntrypointContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill = read(SKILL_MD)
        self.reference = read(REFERENCE_MD)

    def test_frontmatter_contract_is_minimal_and_trigger_only(self) -> None:
        match = re.match(r"\A---\n(?P<body>.*?)\n---\n", self.skill, flags=re.DOTALL)
        self.assertIsNotNone(match)
        body = match.group("body")
        lines = body.splitlines()

        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "name: codex-refactor-loop")
        self.assertTrue(lines[1].startswith("description: Use when "))
        self.assertLessEqual(len(body), 1024)

    def test_entrypoint_line_budget_and_controller_contract_headings(self) -> None:
        line_count = len(self.skill.splitlines())
        headings = set(markdown_headings(self.skill))

        self.assertGreaterEqual(line_count, 600)
        self.assertLessEqual(line_count, 850)
        for pattern in (
            r"^## Controller Contract Index$",
            r"^## Host .+$",
            r"^## Phase Index$",
            r"^## Phase 0 .+Bootstrap .+$",
            r"^## Loop control$",
            r"^## Label .+$",
            r"^## Hard rules .+$",
            r"^## .+language.+$|^## .+语言.+$",
            r"^## Files$",
        ):
            with self.subTest(pattern=pattern):
                self.assertTrue(any(re.match(pattern, heading) for heading in headings))

    def test_mandatory_local_invariants_remain_in_entrypoint(self) -> None:
        required = (
            "⟦AI:AUTO-LOOP⟧",
            "REFERENCE.md#status-and-escalation-templates",
            "Controller = pure orchestration",
            "REFERENCE.md#phase-0-details",
            "Phase 0",
            "phase routing",
            "3/3",
            "CODEX_FLOOR",
            "floor",
            "label",
            "spawn",
            "Hard rules",
            "REFERENCE.md#language-policy-details",
            "REFERENCE.md#historical-bilingual-notes",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, self.skill)

    def test_first_wakeup_bootstrap_obligations_are_ordered_in_skill_alone(self) -> None:
        phase0 = section_between(
            self.skill,
            r"^## Phase 0 .+Bootstrap .+$",
            r"^## Phase Routing$",
        )
        self.assertTrue(phase0)
        obligations = (
            "source .refactor-loop/host.env",
            "fail closed",
            "ProjectRulesFixedPointEnsurer",
            "initialize state",
            "integration branch",
            "ensure labels",
            "restart-helper-managed daemons",
            "arm persistent daemon-event Monitor",
            "dispatch producer",
            "confirm a wake source",
        )
        cursor = -1
        for obligation in obligations:
            index = phase0.find(obligation)
            with self.subTest(obligation=obligation):
                self.assertNotEqual(index, -1)
                self.assertGreater(index, cursor)
            cursor = index
        for daemon in (
            "concurrency_monitor.py",
            "codex-progress-reporter.sh",
            "comment-monitor.sh",
            "dev_sync_daemon.py",
            "phase9_router_daemon.py",
        ):
            with self.subTest(daemon=daemon):
                self.assertIn(daemon, phase0)

    def test_wake_source_contract_names_three_lanes(self) -> None:
        wake_row = next(line for line in self.skill.splitlines() if line.startswith("| Wake source |"))
        for token in ("daemon-event Monitor bridge", "codex task-notification", "ScheduleWakeup"):
            with self.subTest(token=token):
                self.assertIn(token, wake_row)
        self.assertIn("REFERENCE.md#wake-source-rules", wake_row)

    def test_heavy_reference_material_is_not_in_entrypoint(self) -> None:
        reference_only_anchors = (
            "work-unit-contract",
            "batching-heuristics",
            "recovery-playbook",
            "label-bootstrap-loops",
            "historical-bilingual-notes",
        )
        for anchor in reference_only_anchors:
            with self.subTest(anchor=anchor):
                self.assertIn(f"(REFERENCE.md#{anchor})", self.skill)
                self.assertIn(anchor, self.reference)
        self.assertNotIn('"schema_version": 1', self.skill)
        self.assertNotIn('"schema_version": 1', self.reference)
        self.assertNotIn('"work_unit_schema_version": 1', self.reference)
        for emoji_heading in ("📊", "🆘"):
            with self.subTest(emoji_heading=emoji_heading):
                self.assertNotRegex(self.skill, rf"(?m)^## {emoji_heading} ")
                self.assertRegex(self.reference, rf"(?m)^## {emoji_heading} ")

    def test_entrypoint_uses_lazy_reference_links_only(self) -> None:
        self.assertNotIn("@REFERENCE.md", self.skill)
        self.assertNotRegex(self.skill, r"\]\(/Users/[^)]+REFERENCE\.md")
        self.assertRegex(self.skill, r"\(REFERENCE\.md#[^)]+\)")

    def test_phase9_router_daemon_boundary_is_narrow(self) -> None:
        self.assertIn("phase9_router_daemon.py", self.skill)
        self.assertIn("narrow Phase 9 allowlist", self.skill)
        self.assertIn("SOLVER_DONE", self.skill)
        self.assertIn("META_JUDGE_DONE:converge", self.skill)
        self.assertIn("META_JUDGE_DONE:escalate:stalled", self.skill)
        self.assertIn("do not introduce migrated work-unit schema, public marker aliases, ControllerOrchestrator, ControllerEvent, ControllerCommand, or lifecycle authority", self.skill)


if __name__ == "__main__":
    unittest.main()
