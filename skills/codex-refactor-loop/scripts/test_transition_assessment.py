#!/usr/bin/env python3
"""Behavior tests for read-only transition assessment sidecar projection."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from codex_refactor_loop.transition_assessment import (  # noqa: E402
    TRANSITION_BUCKET_ORDER,
    TransitionAssessmentReader,
    projection_lines,
    transition_rank_key,
)


class TransitionAssessmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.assessment_dir = self.repo / ".refactor-loop" / "runs" / "transition-assessments"
        self.assessment_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_assessment(self, work_unit_id: str = "issue-262", **overrides: object) -> None:
        payload = {
            "transition_type": "positive-discovery",
            "confidence": 0.8,
            "evidence_refs": [".refactor-loop/runs/example.md"],
            "classifier_surface_delta": ["classifier added a new signal"],
            "ledger_delta": [],
            "formal_delta": [],
            "record_growth_delta": [],
            "net_positive_signal": True,
            "notes": "validated",
            "producer": "manual-issue",
            "source_ref": "gh-issue-262",
            "work_unit_id": work_unit_id,
        }
        payload.update(overrides)
        (self.assessment_dir / f"{work_unit_id}.json").write_text(json.dumps(payload), encoding="utf-8")

    def load(self, work_unit_id: str = "issue-262", source_ref: str = "gh-issue-262"):
        return TransitionAssessmentReader.load_for_work_unit(self.repo, work_unit_id, source_ref)

    def test_loads_valid_canonical_sidecar_projection(self) -> None:
        self.write_assessment()

        assessment = self.load()

        self.assertEqual(assessment.transition_type, "positive-discovery")
        self.assertEqual(assessment.confidence, 0.8)
        self.assertEqual(assessment.evidence_refs, (".refactor-loop/runs/example.md",))
        self.assertEqual(projection_lines(assessment), (
            "TRANSITION_TYPE=positive-discovery",
            "TRANSITION_CONFIDENCE=0.8",
            "TRANSITION_EVIDENCE_REFS=.refactor-loop/runs/example.md",
        ))

    def test_missing_malformed_or_untrusted_sidecars_are_unknown(self) -> None:
        cases = [
            ("missing", None),
            ("malformed", "{not-json"),
            ("producer", {"producer": "host:newmath"}),
            ("source", {"source_ref": "gh-issue-999"}),
            ("work-unit", {"work_unit_id": "issue-999"}),
            ("confidence", {"confidence": 1.5}),
        ]
        for name, override in cases:
            with self.subTest(name=name):
                self.tmp.cleanup()
                self.setUp()
                if isinstance(override, str):
                    (self.assessment_dir / "issue-262.json").write_text(override, encoding="utf-8")
                elif isinstance(override, dict):
                    self.write_assessment(**override)

                assessment = self.load()

                self.assertEqual(assessment.transition_type, "unknown")
                self.assertEqual(assessment.confidence, 0)

    def test_unsafe_work_unit_id_never_escapes_canonical_directory(self) -> None:
        outside = self.repo / ".refactor-loop" / "runs" / "transition-assessments" / ".." / "evil.json"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text("{}", encoding="utf-8")

        assessment = TransitionAssessmentReader.load_for_work_unit(self.repo, "../evil", "gh-issue-262")

        self.assertEqual(assessment.transition_type, "unknown")
        self.assertIsNone(TransitionAssessmentReader.canonical_path(self.repo, "../evil"))

    def test_bucket_order_and_rank_key(self) -> None:
        self.assertEqual(
            list(TRANSITION_BUCKET_ORDER),
            ["positive-discovery", "classifier-shift", "formal-hardening", "ledger-repair", "record-growth", "unknown"],
        )
        for transition_type, expected_bucket in TRANSITION_BUCKET_ORDER.items():
            with self.subTest(transition_type=transition_type):
                self.write_assessment(transition_type=transition_type, confidence=0.25)
                assessment = self.load()
                self.assertEqual(transition_rank_key(assessment), (expected_bucket, -0.25 if transition_type != "unknown" else -0.0))

    def test_positive_discovery_requires_classifier_delta_and_net_positive_signal(self) -> None:
        cases = [
            ({"classifier_surface_delta": [], "net_positive_signal": True}, "unknown", 0),
            ({"classifier_surface_delta": ["delta"], "net_positive_signal": False}, "classifier-shift", 0.8),
        ]
        for override, expected_type, expected_confidence in cases:
            with self.subTest(override=override):
                self.tmp.cleanup()
                self.setUp()
                self.write_assessment(**override)

                assessment = self.load()

                self.assertEqual(assessment.transition_type, expected_type)
                self.assertEqual(assessment.confidence, expected_confidence)


if __name__ == "__main__":
    unittest.main()
