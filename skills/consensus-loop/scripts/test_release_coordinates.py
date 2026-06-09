#!/usr/bin/env python3
"""Behavior tests for release coordinate policy artifacts."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.release.coordinates import plan_release_coordinate, validate_coordinate_policy


class ReleaseCoordinatePolicyTests(unittest.TestCase):
    def test_beta_minor_and_major_promote_core_to_beta_one(self) -> None:
        cases = (
            ("minor", "1.1.0-beta.1"),
            ("major", "2.0.0-beta.1"),
        )
        for bump_type, expected in cases:
            with self.subTest(bump_type=bump_type):
                plan = plan_release_coordinate("1.0.0-beta.3", bump_type)

                self.assertEqual(expected, plan.to_version)
                self.assertEqual("beta_core_promotion", plan.policy["transition"])
                self.assertEqual("1.0.0-beta.3", plan.policy["from_version"])
                self.assertEqual(expected, plan.policy["to_version"])
                self.assertEqual(bump_type, plan.policy["bump_type"])
                self.assertEqual("1.0.0", plan.policy["from_core"])
                self.assertEqual(expected.removesuffix("-beta.1"), plan.policy["to_core"])
                self.assertEqual("beta", plan.policy["from_stage"])
                self.assertEqual("beta", plan.policy["to_stage"])
                self.assertEqual(3, plan.policy["from_prerelease_index"])
                self.assertEqual(1, plan.policy["to_prerelease_index"])

    def test_patch_beta_and_rc_stay_same_stage(self) -> None:
        beta = plan_release_coordinate("1.0.0-beta.3", "patch")
        rc = plan_release_coordinate("1.0.0-rc.3", "minor")

        self.assertEqual("1.0.0-beta.4", beta.to_version)
        self.assertEqual("same_stage_prerelease", beta.policy["transition"])
        self.assertEqual("1.0.0-rc.4", rc.to_version)
        self.assertEqual("same_stage_prerelease", rc.policy["transition"])

    def test_ga_keeps_core_bump_policy(self) -> None:
        plan = plan_release_coordinate("1.0.0", "minor")

        self.assertEqual("1.1.0", plan.to_version)
        self.assertEqual("ga_core_bump", plan.policy["transition"])

    def test_promotion_requires_matching_policy(self) -> None:
        plan = plan_release_coordinate("1.0.0-beta.3", "minor")

        self.assertIsNone(
            validate_coordinate_policy("1.0.0-beta.3", "1.1.0-beta.1", "minor", plan.policy, plan.policy)
        )
        self.assertEqual(
            "coordinate_policy_missing",
            validate_coordinate_policy("1.0.0-beta.3", "1.1.0-beta.1", "minor", None, None),
        )
        stale = {**plan.policy, "from_version": "1.0.0-beta.2"}
        self.assertEqual(
            "coordinate_policy_mismatch",
            validate_coordinate_policy("1.0.0-beta.3", "1.1.0-beta.1", "minor", stale, plan.policy),
        )

    def test_old_same_stage_and_ga_candidates_may_lack_policy(self) -> None:
        self.assertIsNone(validate_coordinate_policy("1.0.0-beta.3", "1.0.0-beta.4", "minor", None, None))
        self.assertIsNone(validate_coordinate_policy("1.0.0", "1.1.0", "minor", None, None))

    def test_beta_to_rc_ga_and_rc_core_promotion_stay_off_ladder(self) -> None:
        cases = (
            ("1.0.0-beta.3", "1.0.0-rc.1", "minor"),
            ("1.0.0-beta.3", "1.0.0", "minor"),
            ("1.0.0-rc.3", "1.1.0-rc.1", "minor"),
        )
        for from_version, to_version, bump_type in cases:
            with self.subTest(to_version=to_version):
                self.assertEqual(
                    "release_coordinate_off_ladder",
                    validate_coordinate_policy(from_version, to_version, bump_type, None, None),
                )


if __name__ == "__main__":
    unittest.main()
