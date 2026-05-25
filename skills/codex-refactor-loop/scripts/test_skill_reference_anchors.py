#!/usr/bin/env python3
"""Validate lazy REFERENCE.md links from SKILL.md."""

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


def slugify_heading(heading: str) -> str:
    heading = re.sub(r"^\s*#+\s*", "", heading).strip().lower()
    heading = re.sub(r"<[^>]+>", "", heading)
    heading = re.sub(r"[^\w\u4e00-\u9fff -]", "", heading)
    heading = re.sub(r"\s+", "-", heading)
    return heading


def reference_anchors(reference: str) -> set[str]:
    anchors = set(re.findall(r'<a\s+id="([^"]+)"></a>', reference))
    for line in reference.splitlines():
        if line.startswith("#"):
            anchors.add(slugify_heading(line))
    return anchors


class SkillReferenceAnchorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill = read(SKILL_MD)
        self.reference = read(REFERENCE_MD)

    def test_all_skill_reference_links_resolve(self) -> None:
        links = re.findall(r"\(REFERENCE\.md#([^)#\s]+)\)", self.skill)
        self.assertGreaterEqual(len(links), 12)
        available = reference_anchors(self.reference)

        for anchor in links:
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, available)

    def test_reference_contains_required_split_sections(self) -> None:
        required_anchors = (
            "controller-contract-details",
            "host-runtime-details",
            "status-and-escalation-templates",
            "workunitv1-contract",
            "state-schema",
            "batching-heuristics",
            "recovery-playbook",
            "daemon-command-bodies",
            "label-bootstrap-loops",
            "historical-bilingual-notes",
        )
        available = reference_anchors(self.reference)
        for anchor in required_anchors:
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, available)

    def test_reference_is_the_heavy_manual_after_split(self) -> None:
        reference_lines = len(self.reference.splitlines())
        skill_lines = len(self.skill.splitlines())

        self.assertGreaterEqual(reference_lines, 2000)
        self.assertLessEqual(reference_lines, 2450)
        self.assertGreater(reference_lines, skill_lines * 2)

    def test_no_absolute_reference_links_in_entrypoint(self) -> None:
        self.assertNotRegex(self.skill, r"/Users/[^)\s]+")
        self.assertNotRegex(self.skill, r"REFERENCE\.md#/[^\s)]+")


if __name__ == "__main__":
    unittest.main()
