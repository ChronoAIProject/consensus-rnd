#!/usr/bin/env python3
"""Prompt language-policy inventory tests."""

from __future__ import annotations

import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
PROMPTS_DIR = SKILL_ROOT / "prompts"

PUBLIC_LANGUAGE_PROMPTS = {
    "triage-external-issue.md": "writes GitHub-facing triage comments and reshaped issue body artifacts",
    "patrol-analysis.md": "produces public fields for patrol-owned issue create/update bodies",
}
INTERNAL_OR_MARKER_ONLY_EXEMPTIONS = {
    "meta-reflector-stalled.md": "controller-private stalled reflection artifact consumed by router/controller",
    "publish-implementation-fallback.md": "marker-only repair artifact consumed by controller",
    "rebase-resolve.md": "marker-only rebase resolution artifact consumed by controller",
    "remote-ci-fix.md": "marker-only CI fix artifact consumed by controller/review flow",
    "test-add.md": "marker-only test augmentation artifact consumed by controller",
    "verify.md": "marker-only verification artifact consumed by controller",
}


class PromptLanguageInventoryTests(unittest.TestCase):
    def test_public_language_prompts_declare_host_work_language(self) -> None:
        for prompt, reason in PUBLIC_LANGUAGE_PROMPTS.items():
            with self.subTest(prompt=prompt, reason=reason):
                body = (PROMPTS_DIR / prompt).read_text(encoding="utf-8")
                self.assertIn("${HOST_WORK_LANGUAGE}", body)
                self.assertIn("Public-facing natural-language", body)
                self.assertIn("do not add a mandatory parallel English section", body)

    def test_internal_marker_only_exemptions_are_explicit_and_current(self) -> None:
        for prompt, reason in INTERNAL_OR_MARKER_ONLY_EXEMPTIONS.items():
            with self.subTest(prompt=prompt, reason=reason):
                self.assertTrue((PROMPTS_DIR / prompt).exists())
                self.assertGreater(len(reason), 20)

        self.assertEqual(
            set(INTERNAL_OR_MARKER_ONLY_EXEMPTIONS),
            {
                "meta-reflector-stalled.md",
                "publish-implementation-fallback.md",
                "rebase-resolve.md",
                "remote-ci-fix.md",
                "test-add.md",
                "verify.md",
            },
        )


if __name__ == "__main__":
    unittest.main()
