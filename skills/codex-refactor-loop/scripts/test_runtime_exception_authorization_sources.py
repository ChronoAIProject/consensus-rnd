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
    "closed-label-reconciler-238": "## Named runtime exception — closed-label-reconciler(per #238)",
    "update-check-231": "## Notify-only update check(per #231)",
    "integration-sync-daemon-53": "## Named runtime exception — integration sync daemon(per #53)",
    "observability-comment-writers-53": "## Named runtime exception — observability-comment-writers(per #53)",
    "integration-sync-release-rollup-65": "## Named runtime exception — integration sync daemon(per #65)",
    "statusline-51": "## Claude Code statusline(per #51 consensus)",
    "anti-stop-restart-helper-49": "## Named runtime exception — anti-stop restart helper(per #49)",
    "phase9-router-open-state-gate-229": "### Consensus-rnd Phase design-consensus router daemon command body",
}

MAINTAINER_DIRECTIVE_ANCHORS = {
    "maintainer-directive-concurrency-auto-topup",
    "maintainer-directive-progress-reporter-orphan-delete",
    "maintainer-directive-existing-issue-priority-over-audit",
    "maintainer-directive-stale-issue-3h-revival",
    "maintainer-directive-floor-no-exemption",
    "maintainer-directive-milestone-priority",
    "maintainer-directive-wakeup-plan-script",
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

MAINTAINER_DIRECTIVE_REQUIRED_FIELDS = (
    "source_kind",
    "surface",
    "source_date",
    "source_evidence",
    "local_original_pointer",
    "affected_contracts",
    "allowed_directive",
    "forbidden_boundary",
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

    def test_maintainer_directive_entries_have_required_fields(self) -> None:
        self.assertEqual(len(MAINTAINER_DIRECTIVE_ANCHORS), 7)
        for anchor in MAINTAINER_DIRECTIVE_ANCHORS:
            entry = mirror_entry(self.mirror, anchor)
            with self.subTest(anchor=anchor):
                self.assertIn(f'<a id="{anchor}"></a>', self.mirror)
                self.assertIn(f"{MIRROR_RELATIVE}#{anchor}", self.skill)
                self.assertIn("source_kind: maintainer_directive", entry)
                self.assertIn("no_new_runtime_authority", entry)
                for field in MAINTAINER_DIRECTIVE_REQUIRED_FIELDS:
                    self.assertRegex(entry, rf"(?m)^- {field}:")

    def test_maintainer_directive_mirror_is_single_checked_in_authorization_surface(self) -> None:
        forbidden_mirror = "skills/codex-refactor-loop/authorizations/maintainer-directives.md"
        self.assertFalse((REPO_ROOT / forbidden_mirror).exists())
        for path in (
            SKILL_MD,
            MIRROR,
            SKILL_ROOT / "prompts" / "meta-reflector-stalled.md",
            SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py",
            SKILL_ROOT / "scripts" / "codex_refactor_loop" / "controller_actions.py",
        ):
            with self.subTest(path=path):
                text = read(path)
                self.assertNotIn(forbidden_mirror, text)
                self.assertNotRegex(text, r"Authorization(?: source)?: `\.refactor-loop/runs/maintainer-directives/")
                self.assertNotIn("skip-label: maintainer-directive", text)

    def test_local_maintainer_directives_are_not_durable_authorization(self) -> None:
        for path in (
            SKILL_MD,
            SKILL_ROOT / "prompts" / "meta-reflector-stalled.md",
            SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py",
            SKILL_ROOT / "scripts" / "codex_refactor_loop" / "controller_actions.py",
        ):
            text = read(path)
            with self.subTest(path=path):
                self.assertNotIn(".refactor-loop/runs/maintainer-directives/2026-05-29-wakeup-plan-script.md", text)
                self.assertNotIn(".refactor-loop/runs/maintainer-directives/2026-05-29-floor-no-exemption.md", text)
                self.assertNotIn(".refactor-loop/runs/maintainer-directives/2026-05-29-milestone-priority.md", text)
                self.assertNotIn(".refactor-loop/runs/maintainer-directives/2026-05-28-existing-issue-priority-over-audit.md", text)
                self.assertNotIn(".refactor-loop/runs/maintainer-directives/2026-05-28-stale-issue-3h-revival.md", text)
                self.assertNotIn(".refactor-loop/runs/maintainer-directives/2026-05-26-concurrency-auto-topup.md", text)
                self.assertNotIn(".refactor-loop/runs/maintainer-directives/2026-05-27-progress-reporter-orphan-delete.md", text)
        self.assertIn("Local `.refactor-loop/runs/maintainer-directives/<date>-<topic>.md` files are raw evidence awaiting mirror", read(SKILL_ROOT / "prompts" / "meta-reflector-stalled.md"))

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

    def test_closed_label_reconciler_238_preserves_closed_only_terminal_boundary(self) -> None:
        entry = mirror_entry(self.mirror, "closed-label-reconciler-238")

        for required in (
            "#238",
            "closed-label-reconciler",
            "active-controller owner only",
            "CLOSED `crnd:lifecycle:managed`",
            "terminal phase-label reconciliation",
            "crnd:phase:merged",
            "crnd:phase:closed",
            "protocol terminal state",
            "gh-label-closed-reconcile",
            "closed_phase_labels.py",
            "test_closed_label_reconciler.py",
            "test_peek_status_lens.py",
        ):
            with self.subTest(required=required):
                self.assertIn(required, entry)
                self.assertIn(required, self.skill)

        for forbidden in (
            "no open item mutation",
            "issue create/close/reopen/body/title edit",
            "PR create/merge/close/body/title edit",
            "human label mutation",
            "triage label mutation",
            "milestone label mutation",
            "lifecycle label mutation beyond removing `crnd:lifecycle:stuck`",
            "generic `gh-label`",
            "generic `gh-edit`",
            "controller close-path inline reconcile",
            "generic lifecycle actor",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, entry)
                self.assertIn(forbidden, self.skill)

        self.assertIn("#238 是唯一 closed managed item phase-label reconciliation carveout", self.repo_rules)
        self.assertIn("checked-in `closed-label-reconciler`", self.repo_rules)
        self.assertIn("exactly one terminal phase `crnd:phase:merged` 或 `crnd:phase:closed`", self.repo_rules)

    def test_update_check_mirror_preserves_notify_only_boundary(self) -> None:
        entry = mirror_entry(self.mirror, "update-check-231")

        for token in (
            "notify-only",
            "VERSION.json",
            ".refactor-loop/state/update-check.json",
            "restart-daemons",
            "statusline-snapshot.json",
            "test_update_check.py",
            "test_statusline.py",
        ):
            with self.subTest(token=token):
                self.assertIn(token, entry)
                self.assertIn(token, self.skill)
        for forbidden in (
            "copy/overwrite/reinstall",
            "host config edit",
            "GitHub lifecycle",
            "installer",
            "new daemon",
            "apply/update command surface",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, entry)

    def test_anti_stop_restart_helper_mirror_preserves_duplicate_canonical_boundary(self) -> None:
        # Refactor (issue-264): Old: #49 mirror did not lock duplicate canonical skip narrowing.
        # New: source-regression requires helper-private inventory, static allowlist, and no lifecycle authority.
        entry = mirror_entry(self.mirror, "anti-stop-restart-helper-49")

        for required in (
            "DaemonProcessInventory",
            "existing static daemon allowlist",
            "zero duplicate canonical live wrapper",
            "same resolved static allowlist command",
            "duplicate canonical wrappers fail closed",
            "test_restart_daemons.py",
        ):
            with self.subTest(required=required):
                self.assertIn(required, entry)
                self.assertIn(required, self.skill)

        for forbidden in (
            "no host-defined daemon registry",
            "generic process supervisor",
            "GitHub/git lifecycle authority",
            "generic lifecycle authority",
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

    def test_phase9_router_open_state_gate_authorizes_only_state_read(self) -> None:
        # Refactor (fix/pr245-router-authority-anchor): Old: phase9-router's new source issue state read was absent from the mechanical runtime-exception mirror. New: source-regression locks the exact state-only read and lifecycle denials in both mirror and SKILL.
        entry = mirror_entry(self.mirror, "phase9-router-open-state-gate-229")

        for token in (
            "`gh issue view <N> --json state`",
            "state-only",
            "`read-gh`",
            "source-OPEN gate",
            "phase9-source-not-open",
            "phase9-source-state-unavailable",
            "test_phase9_router_open_state_gate.py",
            "test_cli_command_router.py",
            "test_skill_reference_anchors.py",
        ):
            with self.subTest(token=token):
                self.assertIn(token, entry)
                self.assertIn(token, self.skill)
        for forbidden in (
            "gh issue close",
            "gh issue edit",
            "gh label",
            "gh pr merge",
            "gh release",
            "label lifecycle",
            "issue close",
            "PR merge",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, entry)
                self.assertIn(forbidden, self.skill)

    def test_active_controller_lease_mirror_preserves_singleton_boundary(self) -> None:
        # Refactor (iter193/issue-193):
        #   Old pattern: PR#200 introduced GitHubWorkOwnership/author.login
        #   per-work ownership as a second authority for issue/PR writes.
        #   New principle: author.login+updatedAt are metadata only; issue/PR
        #   write permits come only from #191 ActiveControllerLease.
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
            "metadata_only_193",
            "issue/PR `author.login` and `updatedAt` are planning/routing/stale metadata only",
            "must not authorize side effects",
            "per-work owner authority",
            "claim/lease scope",
            "stale takeover permit",
            "#191 `ActiveControllerLease` / `require_active_controller(...)` gate",
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
