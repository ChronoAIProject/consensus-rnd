#!/usr/bin/env python3
"""Source-regression tests for checked-in runtime exception authorization mirrors."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
SKILL_ROOT = SCRIPT_PATH.parents[1]
REPO_ROOT = SCRIPT_PATH.parents[3]
SKILL_MD = SKILL_ROOT / "SKILL.md"
MIRROR_RELATIVE = "skills/codex-refactor-loop/authorizations/runtime-exceptions.md"
MIRROR = REPO_ROOT / MIRROR_RELATIVE
REPO_RULES = REPO_ROOT / "CLAUDE.md"

TARGET_ANCHORS = {
    "autonomous-release-gate-56": "## Named runtime exception — autonomous release gate(per #56)",
    # Refactor (fix/pr236-mirror-source-regression): Old pattern: a new runtime mirror entry could be added without joining the targeted source-regression set. New principle: every named runtime exception mirror added for controller authority must be linked from SKILL.md and locked by focused source tests.
    "release-commits-producer-232": "release-commits` is the independent narrow producer",
    "integration-sync-daemon-53": "## Named runtime exception — integration sync daemon(per #53)",
    "observability-comment-writers-53": "## Named runtime exception — observability-comment-writers(per #53)",
    "integration-sync-release-rollup-65": "## Named runtime exception — integration sync daemon(per #65)",
    "statusline-51": "## Claude Code statusline(per #51 consensus)",
    "anti-stop-restart-helper-49": "## Named runtime exception — anti-stop restart helper(per #49)",
}

REQUIRED_FIELDS = (
    "surface",
    "source_issue",
    "source_round",
    "source_marker",
    "skill_anchor",
    "allowed",
    "forbidden",
    "verification",
    "no_new_runtime_authority",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def mirror_entry(mirror: str, anchor: str) -> str:
    marker = f'<a id="{anchor}"></a>'
    start = mirror.index(marker)
    rest = mirror[start:]
    match = re.search(r"\n<a id=\"[^\"]+\"></a>\n## ", rest[len(marker):])
    if match is None:
        return rest
    return rest[: len(marker) + match.start()]


class RuntimeExceptionAuthorizationSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill = read(SKILL_MD)
        self.mirror = read(MIRROR)
        self.repo_rules = read(REPO_RULES)

    def test_mirror_file_exists_and_is_versionable(self) -> None:
        self.assertTrue(MIRROR.exists())
        self.assertFalse(MIRROR_RELATIVE.startswith(".refactor-loop/"))
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", MIRROR_RELATIVE],
            cwd=REPO_ROOT,
            check=False,
        )
        self.assertNotEqual(ignored.returncode, 0, f"{MIRROR_RELATIVE} must not be gitignored")
        versionable = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", MIRROR_RELATIVE],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertIn(MIRROR_RELATIVE, versionable.stdout.splitlines())

    def test_each_targeted_named_exception_points_to_mirror_anchor(self) -> None:
        for anchor, heading in TARGET_ANCHORS.items():
            with self.subTest(anchor=anchor):
                self.assertIn(heading, self.skill)
                self.assertIn(f"{MIRROR_RELATIVE}#{anchor}", self.skill)
                self.assertIn(f'<a id="{anchor}"></a>', self.mirror)

    def test_skill_degradation_runtime_exception_mirror_is_removed(self) -> None:
        self.assertNotIn("skill-degradation-watch-66", self.skill)
        self.assertNotIn("skill-degradation-watch-66", self.mirror)
        self.assertIn("## Skill degradation source-repo validation", self.skill)
        self.assertIn("source-repo CI/release validation", self.skill)
        self.assertIn("downstream host has no runtime watch", self.skill)

    def test_mirror_entries_have_required_fields(self) -> None:
        for anchor in TARGET_ANCHORS:
            entry = mirror_entry(self.mirror, anchor)
            with self.subTest(anchor=anchor):
                for field in REQUIRED_FIELDS:
                    self.assertRegex(entry, rf"(?m)^- {field}:")

    def test_release_commits_producer_mirror_preserves_narrow_boundary(self) -> None:
        entry = mirror_entry(self.mirror, "release-commits-producer-232")

        self.assertIn("`read-git` and `write-artifact` only", entry)
        self.assertIn("read local git only", entry)
        self.assertIn("atomically write `.refactor-loop/state/release-commits.json`", entry)
        self.assertIn("fact_source: local git tags and refs", entry)
        for verification in (
            "test_release_commits.py",
            "test_cli_command_router.py",
            "test_release_gate_module.py",
        ):
            with self.subTest(verification=verification):
                self.assertIn(verification, entry)
        for forbidden in (
            "GitHub API",
            "push",
            "merge",
            "reset",
            "rebase",
            "worktree mutation",
            "tag",
            "release",
            "commit",
            "issue lifecycle",
            "PR lifecycle",
            "label lifecycle",
            "generic lifecycle authority",
            "inline execution from `release-gate`",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, entry)

    def test_no_targeted_phase9_judge_run_is_authorization_source(self) -> None:
        targeted_old_paths = re.compile(r"\.refactor-loop/runs/phase9-issue(?:49|51|53|56|65|66)-r\d+-judge\.md")
        checked_paths = (
            SKILL_MD,
            SKILL_ROOT / "scripts" / "codex_refactor_loop" / "release" / "gate.py",
            SKILL_ROOT / "scripts" / "codex_refactor_loop" / "banners.py",
            SKILL_ROOT / "scripts" / "codex_refactor_loop" / "checks" / "degradation.py",
        )
        for path in checked_paths:
            with self.subTest(path=path):
                self.assertIsNone(targeted_old_paths.search(read(path)))

    def test_mirror_forbidden_fields_preserve_lifecycle_denials(self) -> None:
        required_denials = (
            "commit",
            "push",
            "merge",
            "close",
            "label",
            "tag",
            "release",
            "new daemon",
        )
        for token in required_denials:
            with self.subTest(token=token):
                self.assertIn(token, self.mirror)
        for grant in (
            "may commit",
            "may push",
            "may merge",
            "may tag",
            "may release",
            "may create PR",
            "may mutate labels",
        ):
            with self.subTest(grant=grant):
                self.assertNotIn(grant, self.mirror)

    def test_integration_sync_ls_remote_is_authorized_only_as_readonly_branch_probe(self) -> None:
        expected_command = "git ls-remote --exit-code --heads origin $INTEGRATION_BRANCH"
        integration_entry = mirror_entry(self.mirror, "integration-sync-daemon-53")

        self.assertIn(expected_command, self.repo_rules)
        self.assertIn(expected_command, self.skill)
        self.assertIn(expected_command, integration_entry)
        self.assertIn("daemon-owned execution", integration_entry)
        self.assertIn("integration-branch git allowlist", integration_entry)
        self.assertIn("no worker-diff commit", integration_entry)
        self.assertIn("no PR create, merge, close, or edit", integration_entry)
        for token in (
            "reset --hard",
            "rebase --rebase-merges",
            "merge --ff-only|--no-ff",
            "git push HEAD:$INTEGRATION_BRANCH",
            "force-with-lease",
        ):
            with self.subTest(token=token):
                self.assertIn(token, integration_entry)

        other_mirror_entries = self.mirror.replace(integration_entry, "")
        self.assertNotIn(expected_command, other_mirror_entries)


if __name__ == "__main__":
    unittest.main()
