#!/usr/bin/env python3
"""Source-regression tests for cross-instance deny-only admission boundaries."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))


class CrossInstanceAuthorityBoundaryTests(unittest.TestCase):
    def test_stand_down_and_provenance_remain_deny_only_not_lifecycle_authority(self) -> None:
        controller = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        projection = (SCRIPT_DIR / "codex_refactor_loop" / "cross_instance_stand_down.py").read_text(encoding="utf-8")

        self.assertIn("check_cross_instance_admission", projection)
        self.assertIn("CrossInstanceAdmission", projection)
        self.assertIn("return None", controller[controller.index("def _require_item_write_admission_or_return") : controller.index("def _cross_instance_runner")])
        self.assertIn("PUSH_OWNERSHIP_BLOCKED", controller)
        self.assertIn("local_admission_evidence_only_not_durable_claim", controller)
        self.assertIn("branch_pr_author_mismatch", controller)
        for forbidden in (
            "takeover_permit",
            "per_work_owner",
            "per-work owner",
            "durable claim",
            "lifecycle_authority",
            "lease_scope",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, projection)

    def test_release_publication_surface_is_not_cross_instance_gated(self) -> None:
        controller = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        publish_release = controller[
            controller.index("    def publish_release_candidate") : controller.index("    def post_status_banner")
        ]
        self.assertNotIn("_require_item_write_admission_or_return", publish_release)
        self.assertNotIn("_require_branch_push_admission_or_return", publish_release)


if __name__ == "__main__":
    unittest.main()
