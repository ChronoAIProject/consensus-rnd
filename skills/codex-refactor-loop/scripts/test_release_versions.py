#!/usr/bin/env python3
"""Behavior tests for shared semver helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.release.versions import bump_semver, compare_semver, parse_semver


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


if __name__ == "__main__":
    unittest.main()
