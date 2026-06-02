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
    "release-publication-322": "## Named runtime exception — release-publication(per #322)",
    "closed-label-reconciler-238": "## Named runtime exception — closed-label-reconciler(per #238)",
    "wakeup-runner-396": "## Named runtime exception - wakeup-runner(per #396)",
    "issue-decomposition-403": "## Large issue decomposition(per #403)",
    "update-check-231": "## Notify-only update check(per #231)",
    "integration-sync-daemon-53": "## Named runtime exception — integration sync daemon(per #53)",
    "observability-comment-writers-53": "## Named runtime exception — observability-comment-writers(per #53)",
    "integration-sync-release-rollup-65": "## Named runtime exception — integration sync daemon(per #65)",
    "statusline-51": "## Claude Code statusline(per #51 consensus)",
    "anti-stop-restart-helper-49": "## Named runtime exception — anti-stop restart helper(per #49)",
    "phase9-router-open-state-gate-229": "### Consensus-rnd Phase design-consensus router daemon command body",
    "controller-release-publisher-334": "## Named runtime exception — release-publication(per #322)",
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

    def test_issue_403_decomposition_allowlist_excludes_wakeup_plan_public_projection(self) -> None:
        entry = mirror_entry(self.mirror, "issue-decomposition-403")
        skill_section = self.skill[self.skill.index("## Large issue decomposition(per #403)") :]
        claude = self.repo_rules
        wakeup_source = read(SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py")
        cli_source = read(SKILL_ROOT / "scripts" / "codex_refactor_loop" / "cli.py")
        controller_source = read(SKILL_ROOT / "scripts" / "codex_refactor_loop" / "controller_actions.py")

        for needle in (
            "active-controller owner only",
            "IssueDecompositionPlan",
            "children:[{slug,title,scope,non_goals,body_artifact_path}]",
            "parent_update:{comment_artifact_path}",
            "catalog design issue label bundle",
            "phase9-router fallback pending events",
            "generic completed-marker projection",
            "read-only `peek` pending-events tail",
            "no daemon/worker issue creation",
            "no public issue factory",
            "no public CLI command",
            "no wakeup-plan decompose projection",
            "no parent issue close/reopen/body-title edit",
            "no lifecycle_owner/lifecycle_authority/cmd/argv/shell/gh/git/close fields",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, entry)
        for needle in (
            "#403 是唯一大 issue 分解 carveout",
            "checked-in apply helper",
            "`wakeup-plan` 不投射 issue-decomposition apply/status action",
            "父 epic 保持 open/tracking",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, claude)
        self.assertIn("apply_issue_decomposition_plan", controller_source)
        for forbidden in (
            "apply-decomposition",
            "open-child-issue",
            "issue-decomposition",
            "decomposition-plan",
            "apply_issue_decomposition_plan",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(f'"{forbidden}"', cli_source)
        for forbidden in (
            "IssueDecompositionPlan",
            "issue-decomposition",
            "decomposition-plan",
            "apply_issue_decomposition_plan",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, wakeup_source)
        self.assertIn("wakeup_plan.py` is not the #403 owner", skill_section)

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

    def test_floor_no_exemption_mirror_preserves_single_active_audit_boundary(self) -> None:
        entry = mirror_entry(self.mirror, "maintainer-directive-floor-no-exemption")

        for required in (
            "legal dispatchable real work",
            "ordinary audit fallback",
            "no same-iteration audit is active",
            "same-iteration audit is already active",
            "dispatch_required=0",
            "reason=single_active_audit_in_flight",
            "blocked_deficit=N",
            "AUDIT_DONE:none:0` still does not exempt",
            "no general low-floor exemption",
            "no duplicate same-iteration audit",
            "no fabricated work",
            "no AuditLaneIdentity",
            "no `AUDIT_LANE_ID`",
            "no `audit-iter-N-laneK`",
            "no lane/shard protocol in this issue",
            "no issue/PR lifecycle",
            "label lifecycle",
            "commit",
            "push",
            "merge",
            "tag",
            "release",
            "generic lifecycle actor",
        ):
            with self.subTest(required=required):
                self.assertIn(required, entry)

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

    def test_release_publication_322_preserves_controller_only_boundary(self) -> None:
        # Refactor (iter1/issue-322):
        #   Old pattern: ReleasePublisher had commit/push/gh-release authority only in SKILL prose.
        #   New principle: release-publication-322 mirrors exact commands and forbidden lifecycle surfaces.
        entry = mirror_entry(self.mirror, "release-publication-322")

        for required in (
            "#322",
            "ReleasePublisher",
            "active-controller owner",
            "ReleasePublishPreflight",
            "RELEASE_AUTO_ENABLE=true",
            "fresh `.refactor-loop/state/release-candidate.json`",
            "fresh `.refactor-loop/state/release-decision.json`",
            "matching `decision_digest`",
            "matching `target_ref`",
            "mapped manifest `from_version`",
            "required checks green",
            "python3 .github/scripts/bump_version.py --version <to_version>",
            "git add .version-bump.json <mapped manifests>",
            'git commit -m "Release v<to_version>"',
            "git rev-parse HEAD",
            "git fetch origin HEAD",
            "git rev-list --count HEAD..origin/HEAD",
            "git push origin HEAD",
            "gh release create v<to_version> --target <fresh release commit sha> --generate-notes [--prerelease]",
            ".refactor-loop/state/release-publish-result.json",
            "test_release_publisher.py",
            "test_release_publish_preflight.py",
            "test_cli_command_router.py",
            "test_runtime_exception_authorization_sources.py",
            "test_release_pipeline_contract.py",
            "test_controller_actions.py",
            "no_new_runtime_authority",
        ):
            with self.subTest(required=required):
                self.assertIn(required, entry)
                self.assertIn(required, self.skill)

        for forbidden in (
            "no public `consensus-rnd-cli release-publish`",
            "no public `consensus-rnd-cli publish-release`",
            "no workflow tag/release creation",
            "no `git tag`",
            "no force-push",
            "no `git merge`",
            "no `git rebase`",
            "no `git reset`",
            "no GitHub Release edit/delete/upload",
            "no approval-ticket/emoji gate",
            "no issue lifecycle",
            "PR lifecycle",
            "label lifecycle",
            "generic lifecycle actor",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, entry)
                self.assertIn(forbidden, self.skill)

        self.assertIn("#322 是唯一 controller-owned release publication carveout", self.repo_rules)
        self.assertIn("active-controller owner 的 `ReleasePublisher`", self.repo_rules)
        self.assertIn("`ReleasePublishPreflight` 验证 `RELEASE_AUTO_ENABLE=true`", self.repo_rules)
        self.assertIn("`git push origin HEAD`、通过 `ReleaseRequiredChecksProjection` 读取", self.repo_rules)
        self.assertIn("`gh api repos/<slug>/commits/<fresh release commit sha>/check-runs --paginate --slurp`", self.repo_rules)
        self.assertIn("确认该 exact fresh SHA required checks 全绿后才运行", self.repo_rules)
        self.assertIn("`gh release create v<to_version> --target <fresh release commit sha> --generate-notes [--prerelease]`", self.repo_rules)
        self.assertIn("禁止 public release-publish CLI", self.repo_rules)
        self.assertIn("tag target without exact-SHA green checks", self.repo_rules)
        self.assertIn("release edit/delete/upload", self.repo_rules)

    def test_release_publication_322_allows_only_first_bump_or_already_bumped_reentry(self) -> None:
        entry = mirror_entry(self.mirror, "release-publication-322")
        for required in (
            "already-bumped reentry",
            "only preflight mismatch is mapped manifests already equal `to_version`",
            "git show -s --format=%s HEAD",
            "HEAD subject is exactly `Release v<to_version>`",
            "skip only `python3 .github/scripts/bump_version.py --version <to_version>`, `git add .version-bump.json <mapped manifests>`, and `git commit -m \"Release v<to_version>\"`",
            "git rev-parse HEAD",
            "git fetch origin HEAD",
            "git rev-list --count HEAD..origin/HEAD",
            "git push origin HEAD",
            "gh api repos/<slug>/commits/<exact release/reentry commit sha>/check-runs --paginate --slurp",
            "gh release create v<to_version> --target <exact release/reentry commit sha> --generate-notes [--prerelease]",
            "pending/red/missing/API-fail fail closed",
            "no proof-ticket/resume system",
            "no public `consensus-rnd-cli release-publish`",
            "no workflow tag/release creation",
        ):
            with self.subTest(required=required):
                self.assertIn(required, entry)
                self.assertIn(required, self.skill)
        for repo_rules_required in (
            "only preflight mismatch 是 mapped manifests 已==`to_version`",
            "`git show -s --format=%s HEAD`",
            "HEAD subject 精确为 `Release v<to_version>`",
            "`gh api repos/<slug>/commits/<exact release/reentry commit sha>/check-runs --paginate --slurp`",
            "`gh release create v<to_version> --target <exact release/reentry commit sha> --generate-notes [--prerelease]`",
        ):
            with self.subTest(repo_rules_required=repo_rules_required):
                self.assertIn(repo_rules_required, self.repo_rules)

    def test_controller_release_publisher_334_mirror_preserves_exact_sha_green_gate(self) -> None:
        entry = mirror_entry(self.mirror, "controller-release-publisher-334")

        for required in (
            "controller-owned release publisher",
            "#334",
            "r5",
            "META_JUDGE_DONE:converge:round-4:decide",
            "#release-pipeline-integrationpost-61",
            "active-controller owner only",
            "release candidate/decision artifacts",
            "ReleasePublishPreflight",
            "bump mapped manifests",
            "commit/push the release manifest commit",
            "read exact-SHA Checks API",
            "only after that exact fresh SHA is green",
            ".refactor-loop/state/release-publish-result.json",
            "release candidate/decision artifacts + mapped manifests + exact fresh SHA + Checks API projection",
            "test_release_publisher.py",
            "test_release_pipeline_contract.py",
            "test_runtime_exception_authorization_sources.py",
            "test_skill_reference_anchors.py",
            "mirror only, not a runtime API/loader/schema/proof ticket/authorization source",
        ):
            with self.subTest(required=required):
                self.assertIn(required, entry)
                self.assertIn(required, self.skill)

        for forbidden in (
            "workflow tag/release",
            "public CLI release-publish",
            "proof-ticket/resume system",
            "tag target without exact-SHA green checks",
            "arbitrary branch push",
            "issue/PR lifecycle",
            "label mutation",
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

    def test_wakeup_runner_396_preserves_closed_projection_boundary(self) -> None:
        entry = mirror_entry(self.mirror, "wakeup-runner-396")

        for required in (
            "#396",
            "wakeup-runner",
            "active-controller owner",
            "`wakeup-plan` evidence-bound closed action projection",
            'mode: "closed-action-projection"',
            'apply_authority: "wakeup-runner-396-only"',
            'runner_authority: "wakeup-runner-396"',
            "clean `EXIT=0` source marker",
            "review truth table `reject==0 && approve>=1 && all required reviewers present`",
            "OPEN/live GitHub state",
            "release #322 preflight",
            "helper-specific precondition",
            "spawn codex",
            "named helper `dispatch_consensus_implementation`",
            "named helper `publish_implementation_output`",
            "named helper `open_release_rollup_pr_from_action`",
            "publish worker output",
            "dispatch reviewers/fix/remote-ci worker",
            "apply triage decision",
            "merge PR under review truth table",
            "close managed item from drop marker",
            "publish release through #322",
            "test_wakeup_runner.py",
            "test_wakeup_runner_review_gate.py",
            "test_wakeup_runner_release.py",
            "test_wakeup_plan.py",
        ):
            with self.subTest(required=required):
                self.assertIn(required, entry)
                self.assertIn(required, self.skill)

        for forbidden in (
            "no arbitrary git/gh command",
            "workflow tag/release",
            "prompt-body decision",
            "standalone authorization from `wakeup-plan`",
            "argv/shell/cmd/command_line/commands/env/git/gh/executor/lifecycle_authority/lifecycle_owner/generic command fields",
            "`ControllerTurnDecision`",
            "controller-turn worker",
            "private schema",
            "active-active scheduler",
            "`.refactor-loop/host.env` as host production SSOT",
            "generic lifecycle actor",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, entry)
                self.assertIn(forbidden, self.skill)

        self.assertIn("#396 是唯一 unattended wakeup-runner carveout", self.repo_rules)
        self.assertIn("`wakeup-plan` 是唯一 action projection fact source但不是 standalone authorization source", self.repo_rules)
        self.assertIn("不得新增 `ControllerTurnDecision`/controller-turn worker/schema", self.repo_rules)

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
            "read-only daemon-status projection",
            "repair/reload remains restart-daemons",
            "cached active-controller status",
            "public start/stop/restart/reload lifecycle verb",
            "test_cli_command_router.py",
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
            "`gh issue list --repo <owner/repo> --state open --label crnd:lifecycle:managed --json number,title,labels`",
            "`gh api repos/<slug>/issues/<N> --jq .state`",
            "DesignConsensusIssueIntake",
            "four built-in phase9 direct routes",
            "r1 solver triplet",
            "state-only",
            "source-OPEN gate",
            "phase9-source-not-open",
            "phase9-source-state-unavailable",
            "HARNESS_SPAWN_INTENT",
            '`command: "spawn-codex"`',
            'dispatch_state="harness-intent"',
            "test_phase9_router_open_state_gate.py",
            "test_wakeup_plan.py",
            "test_wakeup_runner.py",
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
            "daemon direct `nohup spawn-codex`",
            "argv array",
            "shell command",
            "generic command bus",
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

    def test_banner_public_cli_removed_and_controller_action_owner_gated(self) -> None:
        cli = read(SKILL_ROOT / "scripts" / "codex_refactor_loop" / "cli.py")
        banners = read(SKILL_ROOT / "scripts" / "codex_refactor_loop" / "banners.py")
        actions = read(SKILL_ROOT / "scripts" / "codex_refactor_loop" / "controller_actions.py")
        observability_entry = mirror_entry(self.mirror, "observability-comment-writers-53")

        self.assertNotIn('"post-banner": CommandSpec', cli)
        self.assertNotIn("banners.main", cli)
        for forbidden in ("def main(", "argparse", "load_optional_context", "post_status_banner("):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, banners)
        for required in (
            "def post_status_banner(self, request: BannerRequest) -> str:",
            'self._require_owner_or_raise("post-banner")',
            "_normalize_lifecycle_target_or_raise",
            "gh_comment_command",
        ):
            with self.subTest(required=required):
                self.assertIn(required, actions)
        for required in (
            "#191 `ActiveControllerLease` / `require_active_controller(...)` gate",
            "not a cross-device write permit",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.skill)
                self.assertIn(required, observability_entry)

    def test_observability_comment_writers_owner_local_contract_is_locked(self) -> None:
        heading = "## Named runtime exception — observability-comment-writers(per #53)"
        start = self.skill.index(heading)
        rest = self.skill[start:]
        next_heading = rest.find("\n## ", len(heading))
        section = rest if next_heading == -1 else rest[:next_heading]

        for required in (
            "Progress target/kind facts are owned locally by `monitors/progress.py`",
            "exact log basenames are the canonical target source",
            "prompt fallback applies only when the matching prompt file exists",
            "Comment-monitor controller-post identity is owned locally by `monitors/comment.py`",
            "final `⟦AI:AUTO-LOOP⟧` sentinel is canonical",
            "`CONTROLLER_PREFIXES` is only a legacy compatibility skip list",
            "private `.refactor-loop` paths derived from `LoopContext`, not host env surfaces",
            "#191 `ActiveControllerLease` / `require_active_controller(...)` gate",
            "label mutation",
            "issue/PR close/create/merge",
            "release/tag",
            "git lifecycle",
        ):
            with self.subTest(required=required):
                self.assertIn(required, section)

        for forbidden in (
            "observability_comments.py",
            "progress-comment-targets",
            "PROGRESS_REPORTER_INTERVAL",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, section)
                self.assertNotIn(forbidden, self.skill)
                self.assertNotIn(forbidden, self.mirror)


if __name__ == "__main__":
    unittest.main()
