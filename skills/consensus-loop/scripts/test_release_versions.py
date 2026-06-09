#!/usr/bin/env python3
"""Behavior tests for shared semver helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.release.versions import (
    bump_semver,
    compare_semver,
    next_release_version,
    parse_semver,
    validate_release_version_coordinate,
)


class ReleaseVersionsTests(unittest.TestCase):
    def test_parse_and_bump_compatibility(self) -> None:
        self.assertEqual((1, 2, 3), parse_semver("1.2.3-beta.4+build.5"))
        self.assertEqual("1.3.0", bump_semver("1.2.3-rc.1", "minor"))

    def test_compare_prerelease_ordering(self) -> None:
        self.assertGreater(compare_semver("1.0.0-beta.5", "1.0.0-beta.4"), 0)
        self.assertGreater(compare_semver("1.0.0-rc.1", "1.0.0-beta.99"), 0)
        self.assertGreater(compare_semver("1.0.0", "1.0.0-rc.1"), 0)
        self.assertLess(compare_semver("1.0.0-alpha.1", "1.0.0-alpha.beta"), 0)

    def test_build_metadata_does_not_affect_precedence(self) -> None:
        self.assertEqual(0, compare_semver("1.0.0+abc", "1.0.0+def"))

    def test_invalid_semver_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            compare_semver("v1.0", "1.0.0")

    def test_next_release_version_beta_stays_same_stage_for_patch_minor_major(self) -> None:
        for bump_type in ("patch", "minor", "major"):
            with self.subTest(bump_type=bump_type):
                self.assertEqual("1.0.0-beta.4", next_release_version("1.0.0-beta.3", bump_type))

    def test_next_release_version_rc_stays_same_stage(self) -> None:
        self.assertEqual("1.0.0-rc.3", next_release_version("1.0.0-rc.2", "patch"))

    def test_next_release_version_ga_preserves_core_bump_semantics(self) -> None:
        self.assertEqual("1.0.1", next_release_version("1.0.0", "patch"))
        self.assertEqual("1.1.0", next_release_version("1.0.0", "minor"))
        self.assertEqual("2.0.0", next_release_version("1.0.0", "major"))

    def test_next_release_version_malformed_prerelease_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            next_release_version("1.0.0-beta.x", "patch")

    def test_validate_release_version_coordinate_rejects_off_ladder(self) -> None:
        self.assertEqual(
            "release_coordinate_off_ladder",
            validate_release_version_coordinate("1.0.0-beta.3", "1.0.1", "patch"),
        )
        self.assertIsNone(validate_release_version_coordinate("1.0.0-beta.3", "1.0.0-beta.4", "patch"))


if __name__ == "__main__":
    unittest.main()
