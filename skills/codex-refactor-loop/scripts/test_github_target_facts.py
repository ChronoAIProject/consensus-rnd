#!/usr/bin/env python3
"""Source-regression tests for issue/PR target metadata boundaries."""

from __future__ import annotations

import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
REPO_ROOT = SCRIPT_DIR.parents[2]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def runtime_text_without_this_test() -> str:
    parts: list[str] = []
    for root in (
        REPO_ROOT / "CLAUDE.md",
        SKILL_ROOT / "SKILL.md",
        SKILL_ROOT / "authorizations",
        SCRIPT_DIR / "codex_refactor_loop",
    ):
        if root.is_file():
            paths = [root]
        else:
            paths = [path for path in root.rglob("*") if path.is_file() and path.suffix in {".md", ".py"}]
        parts.extend(read(path) for path in paths)
    return "\n".join(parts)


class GitHubTargetFactsMetadataOnlyTests(unittest.TestCase):
    # Refactor (iter193/issue-193):
    #   Old pattern: PR#200 introduced GitHubWorkOwnership/author.login
    #   per-work ownership as a second authority for issue/PR writes.
    #   New principle: author.login+updatedAt are metadata only; issue/PR
    #   write permits come only from #191 ActiveControllerLease.
    def test_metadata_fields_are_documented_without_per_work_authority(self) -> None:
        text = runtime_text_without_this_test()
        for required in (
            "author.login",
            "updatedAt",
            "planning/routing/stale metadata",
            "side-effect authorization",
            "per-work owner authority",
            "claim/lease scope",
            "takeover permit",
            "ActiveControllerLease",
            "require_active_controller",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)

    def test_no_github_target_facts_authorization_module_exists(self) -> None:
        self.assertFalse((SCRIPT_DIR / "codex_refactor_loop" / "ownership.py").exists())
        self.assertFalse((SCRIPT_DIR / "codex_refactor_loop" / "github_target_facts.py").exists())

    def test_pr200_per_work_ownership_tokens_do_not_reappear_in_runtime(self) -> None:
        text = runtime_text_without_this_test()
        for forbidden in (
            "GitHubWorkOwnership",
            "OwnershipDecision",
            "WorkTargetResolver",
            "require_ownership_permit",
            "post_takeover_notice",
            "allowed_ownership",
            "refs/heads/auto-loop/leases",
            "claim label",
            "owner marker",
            "WorkUnitClaim",
            ".refactor-loop/device-claims",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
