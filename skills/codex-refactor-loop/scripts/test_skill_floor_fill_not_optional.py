#!/usr/bin/env python3
"""Source-regression tests for the SKILL.md Concurrency Floor contract."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__)
SKILL_ROOT = SCRIPT_PATH.parents[1]
SKILL_MD = SKILL_ROOT / "SKILL.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class FloorFillNotOptionalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill = read(SKILL_MD)
        match = re.search(
            r"## Concurrency Floor\s*\n(?P<body>.*?)\n## ",
            self.skill,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match, "Concurrency Floor section missing")
        self.section = match.group("body")

    def test_refactor_self_doc_block_present(self) -> None:
        self.assertIn(
            "Refactor (issue-277)",
            self.section,
            "Refactor self-doc block must remain present",
        )

    def test_actionable_marker_bounded_by_exit_zero(self) -> None:
        self.assertRegex(
            self.section,
            r"Actionable marker.*EXIT=0",
            "Concurrency Floor must bind actionable markers to EXIT=0",
        )

    def test_in_flight_codex_explicitly_not_actionable(self) -> None:
        self.assertIn("in-flight codex", self.section)
        self.assertRegex(self.section, r"in-flight codex.*actionable marker")

    def test_audit_fallback_has_no_fixed_point_exemption(self) -> None:
        for required in ("envsubst", "audit-iter-N", "harness background task"):
            with self.subTest(required=required):
                self.assertIn(required, self.section)
        self.assertIn("deficit>0", self.section)
        self.assertIn("no general exemption", self.section)
        self.assertIn("audit fallback", self.section)
        self.assertIn("one same-iteration active slot", self.section)
        self.assertIn("WAIT:single-active-audit", self.section)
        self.assertIn("blocked_deficit", self.section)

    def test_none_zero_no_longer_stops_floor_refill(self) -> None:
        for required in (
            "AUDIT_DONE:none:0",
            "still does not exempt",
            "RECOMMEND:audit",
            "HARD_GATE:dispatch_required=N",
            "no duplicate same-iteration audit",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.section)
        self.assertNotIn("CONCURRENCY_LOW:no-work-after-audit-none", self.section)

    def test_issue277_does_not_add_lane_protocol(self) -> None:
        for forbidden in ("AuditLaneIdentity", "AUDIT_LANE_ID", "audit-iter-N-laneK"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.skill)

    def test_rationalization_wording_blocked(self) -> None:
        # Typical defer wording must stay explicitly blocked before the fixed point.
        blocked_phrases = (
            "\u6d3e audit \u91cd",
            "target stale",
            "\u7b49 cascade",
            "\u548c\u5df2\u6709\u5de5\u4f5c\u51b2\u7a81",
        )
        for phrase in blocked_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.section, f"Missing blocked wording: {phrase}")


if __name__ == "__main__":
    unittest.main()
