#!/usr/bin/env python3
"""Behavior tests for controller-private release notes."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
sys.path.insert(0, str(SCRIPT_PATH.parent))

from codex_refactor_loop.release.notes import (
    ReleaseNoteCommit,
    generate_release_notes_file,
    is_mechanical_release_artifact,
    render_release_notes,
)


LOCALIZED_ROLLUP_SUBJECT = "".join(chr(codepoint) for codepoint in (0x53D1, 0x5E03)) + " rollup: integration ahead 2 commits (256e4b827b26) (#595)"


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ReleaseNotesTests(unittest.TestCase):
    def test_render_release_notes_filters_mechanical_artifacts_and_surfaces_referenced_work(self) -> None:
        notes = render_release_notes(
            [
                ReleaseNoteCommit(
                    sha="a" * 40,
                    subject="Release v2.0.0-beta.10",
                    body="",
                ),
                ReleaseNoteCommit(
                    sha="b" * 40,
                    subject=LOCALIZED_ROLLUP_SUBJECT,
                    body="",
                ),
                ReleaseNoteCommit(
                    sha="c" * 40,
                    subject="fix: enforce release publisher notes file (#704)",
                    body="Closes #704\nRefs #322",
                ),
                ReleaseNoteCommit(
                    sha="d" * 40,
                    subject="feat: surface release notes for real work",
                    body="Related #705",
                ),
            ],
            version="2.0.0-beta.11",
            target_ref="release-sha",
        )

        self.assertIn("## v2.0.0-beta.11", notes)
        self.assertIn("Target: `release-sha`", notes)
        self.assertIn("fix: enforce release publisher notes file (#704)", notes)
        self.assertIn("feat: surface release notes for real work (#705)", notes)
        self.assertIn("- #322", notes)
        self.assertIn("- #704", notes)
        self.assertIn("- #705", notes)
        self.assertNotIn("Release v2.0.0-beta.10", notes)
        self.assertNotIn(LOCALIZED_ROLLUP_SUBJECT, notes)
        self.assertNotIn("integration ahead", notes)

    def test_generate_release_notes_file_consumes_release_commits_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            write_json(
                repo / ".refactor-loop/state/release-commits.json",
                {
                    "commits": [
                        {"sha": "a" * 40, "subject": "Release v2.0.0-beta.4", "body": ""},
                        {"sha": "b" * 40, "subject": "fix: ship selected work (#704)", "body": "Refs #322"},
                    ],
                    "latest_release_version": "2.0.0-beta.3",
                },
            )

            path = generate_release_notes_file(repo, version="2.0.0-beta.5", target_ref="target123")

            self.assertEqual(path, repo / ".refactor-loop/state/release-notes/v2.0.0-beta.5.md")
            body = path.read_text(encoding="utf-8")
            self.assertIn("fix: ship selected work (#704)", body)
            self.assertIn("- #322", body)
            self.assertIn("- #704", body)
            self.assertNotIn("Release v2.0.0-beta.4", body)

    def test_mechanical_artifact_classifier_covers_release_and_rollup_titles(self) -> None:
        mechanical = (
            "Release v1.2.3",
            "Release v1.2.3-beta.4 (#595)",
            "Release rollup",
            "release-rollup: sync integration",
            LOCALIZED_ROLLUP_SUBJECT,
            "reserve release candidate",
            "release commit for v1.2.3",
        )
        for subject in mechanical:
            with self.subTest(subject=subject):
                self.assertTrue(is_mechanical_release_artifact(subject))

        self.assertFalse(is_mechanical_release_artifact("fix: release notes include referenced work (#704)"))


if __name__ == "__main__":
    unittest.main()
