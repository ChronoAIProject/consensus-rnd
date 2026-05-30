#!/usr/bin/env python3
"""Source-regression tests for checked-in runtime exception authorization mirrors."""

from __future__ import annotations

import ast
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
ACTIVE_CONTROLLER = SKILL_ROOT / "scripts" / "codex_refactor_loop" / "active_controller.py"

TARGET_ANCHORS = {
    "autonomous-release-gate-56": "## Named runtime exception — autonomous release gate(per #56)",
    # Refactor (fix/pr236-mirror-source-regression): Old pattern: a new runtime mirror entry could be added without joining the targeted source-regression set. New principle: every named runtime exception mirror added for controller authority must be linked from SKILL.md and locked by focused source tests.
    "active-controller-lease-191": "## Named runtime exception - active controller lease(per #191)",
    "release-commits-producer-232": "release-commits` is the independent narrow producer",
    "integration-sync-daemon-53": "## Named runtime exception — integration sync daemon(per #53)",
    "observability-comment-writers-53": "## Named runtime exception — observability-comment-writers(per #53)",
    "integration-sync-release-rollup-65": "## Named runtime exception — integration sync daemon(per #65)",
    "skill-degradation-watch-66": "## Named runtime exception — skill degradation watch(per #66)",
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


def active_controller_git_subcommands() -> set[str]:
    tree = ast.parse(read(ACTIVE_CONTROLLER))
    commands: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "_git":
            continue
        if not node.args or not isinstance(node.args[0], ast.List):
            continue
        values = []
        for elt in node.args[0].elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                values.append(elt.value)
        index = 0
        while index + 1 < len(values) and values[index] == "-c":
            index += 2
        if index < len(values):
            commands.add(values[index])
    return commands


def documented_git_subcommands(text: str) -> set[str]:
    commands: set[str] = set()
    for command in re.findall(r"`git ([^`]+)`", text):
        subcommand = command.split()[0]
        commands.add(subcommand)
    return commands


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
        targeted_old_paths = re.compile(r"\.refactor-loop/runs/phase9-issue(?:49|51|53|56|65|66|191)-r\d+-judge\.md")
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
            "source mutation",
            "codex dispatch",
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

    def test_active_controller_lease_mirror_preserves_singleton_boundary(self) -> None:
        entry = mirror_entry(self.mirror, "active-controller-lease-191")

        for required in (
            "single active controller lease",
            "refs/heads/crnd/active-controller",
            "active-controller.json",
            "owner_device",
            "lease_id",
            "expires_at",
            "git fetch origin <lease-ref>",
            "git ls-remote --exit-code --heads origin <lease-ref>",
            "git rev-parse",
            "git show <commit>:active-controller.json",
            "git hash-object -w --stdin",
            "git mktree",
            "git commit-tree",
            "git push --force-with-lease=<old>:<lease-ref>",
            "These commands may only read/build/publish the singleton lease blob CAS",
            "restart-daemons",
            "concurrency dispatch",
            "phase9 router",
            "comment/progress writes",
            "dev-sync",
            "controller lifecycle helpers",
        ):
            with self.subTest(required=required):
                self.assertIn(required, entry)

        for forbidden in (
            "worker diff commit",
            "issue create/edit/close",
            "PR create/edit/merge/close",
            "label mutation",
            "tag",
            "release",
            "per-work claim",
            "host-defined lease scope",
            "cross-device floor aggregation",
            "daemon ownership matrix",
            "active-active scheduler",
            "generic distributed lock library",
            "generic lifecycle actor",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, entry)

    def test_active_controller_git_allowlist_matches_implementation(self) -> None:
        # Refactor (fix/pr242-narrow-allowlist-and-nonowner-test): Old:
        # authorization anchors named only part of the lease CAS git surface.
        # New: source-regression compares both anchors to active_controller.py.
        entry = mirror_entry(self.mirror, "active-controller-lease-191")
        skill_section = re.search(
            r"(?ms)^## Named runtime exception - active controller lease\(per #191\).*?(?=^## )",
            self.skill,
        )
        self.assertIsNotNone(skill_section)
        assert skill_section is not None

        expected = active_controller_git_subcommands()
        self.assertEqual(
            expected,
            {"fetch", "ls-remote", "rev-parse", "show", "hash-object", "mktree", "commit-tree", "push"},
        )
        self.assertEqual(expected, documented_git_subcommands(entry))
        self.assertEqual(expected, documented_git_subcommands(skill_section.group(0)))
        mirror_allowlist = re.search(r"Lease-only git allowlist: .*?\.", entry)
        skill_allowlist = re.search(r"Lease-only git allowlist: .*?\.", skill_section.group(0))
        self.assertIsNotNone(mirror_allowlist)
        self.assertIsNotNone(skill_allowlist)
        assert mirror_allowlist is not None
        assert skill_allowlist is not None
        self.assertEqual(mirror_allowlist.group(0), skill_allowlist.group(0))


if __name__ == "__main__":
    unittest.main()
