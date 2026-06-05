#!/usr/bin/env python3
"""Source-regression tests for checked-in runtime exception authorization mirrors."""

from __future__ import annotations

import ast
import re
import subprocess
import unittest
from pathlib import Path

from test_support.authorization_projection import project_markdown, project_python


SCRIPT_PATH = Path(__file__).resolve()
SKILL_ROOT = SCRIPT_PATH.parents[1]
REPO_ROOT = SCRIPT_PATH.parents[3]
SKILL_MD = SKILL_ROOT / "SKILL.md"
META_JUDGE_PROMPT = SKILL_ROOT / "prompts" / "meta-judge.md"
MIRROR_RELATIVE = "skills/codex-refactor-loop/authorizations/runtime-exceptions.md"
MIRROR = REPO_ROOT / MIRROR_RELATIVE
REPO_RULES = REPO_ROOT / "CLAUDE.md"
ACTIVE_CONTROLLER = SKILL_ROOT / "scripts" / "codex_refactor_loop" / "active_controller.py"

TARGET_ANCHORS = {
    "autonomous-release-gate-56": "## Named runtime exception — autonomous release gate(per #56)",
    "active-controller-lease-191": "## Named runtime exception - active controller lease(per #191)",
    "release-commits-producer-232": "release-commits` is the independent narrow producer",
    "release-publication-322": "## Named runtime exception — release-publication(per #322)",
    "closed-label-reconciler-238": "## Named runtime exception — closed-label-reconciler(per #238)",
    "wakeup-runner-396": "## Named runtime exception - wakeup-runner(per #396)",
    "task-spawn-claim-490": "## Task spawn claim(per #490)",
    "issue-decomposition-403": "## Large issue decomposition(per #403)",
    "update-check-231": "## Notify-only update check(per #231)",
    "integration-sync-daemon-53": "## Named runtime exception — integration sync daemon(per #53)",
    "observability-comment-writers-53": "## Named runtime exception — observability-comment-writers(per #53)",
    "integration-sync-release-rollup-65": "## Named runtime exception — integration sync daemon(per #65)",
    "statusline-51": "## Claude Code statusline(per #51 consensus)",
    "anti-stop-restart-helper-49": "## Named runtime exception — anti-stop restart helper(per #49)",
    "phase9-router-open-state-gate-229": "### Consensus-rnd Phase design-consensus router daemon command body",
    "controller-release-publisher-334": "## Named runtime exception — release-publication(per #322)",
    "gh-usage-accounting-455": "## Named runtime exception — gh usage accounting(per #455)",
    "global-dashboard-status-card-504": "## Named runtime exception - global-dashboard-status-card(per #504)",
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


def python_projection(path: Path):
    return project_python(read(path))


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
        mirror_projection = project_markdown(self.mirror)
        for anchor in TARGET_ANCHORS:
            entry = mirror_entry(self.mirror, anchor)
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, mirror_projection.anchors)
                for field in REQUIRED_FIELDS:
                    self.assertIn(field, project_markdown(entry).bullet_fields)

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

    def test_issue_504_global_dashboard_card_is_fixed_issue_comment_patch_only(self) -> None:
        entry = mirror_entry(self.mirror, "global-dashboard-status-card-504")
        skill_section = self.skill[self.skill.index("## Named runtime exception - global-dashboard-status-card(per #504)") :]
        claude = self.repo_rules
        cli_source = read(SKILL_ROOT / "scripts" / "codex_refactor_loop" / "cli.py")
        progress_source = read(SKILL_ROOT / "scripts" / "codex_refactor_loop" / "monitors" / "progress.py")
        holistic_source = read(SKILL_ROOT / "scripts" / "codex_refactor_loop" / "holistic_status.py")

        for needle in (
            "active-controller owner only",
            "HolisticStatusProjection",
            "consensus-rnd-cli holistic-status",
            "peek` reuse only the summary renderer",
            "$HOST_HOLISTIC_STATUS_ENABLE=true",
            "$HOST_HOLISTIC_STATUS_ISSUE_NUMBER",
            "$HOST_HOLISTIC_STATUS_COMMENT_ID",
            "GraphQL headroom",
            "#191 owner",
            "interval",
            "same-hash",
            "PATCH exactly one host-configured issue comment id",
            "no new daemon",
            "no public writer CLI",
            "no create comment",
            "no issue body edit",
            "no PR body/title edit",
            "no Discussions",
            "no label mutation",
            "no create/close/reopen/merge",
            "no tag/release",
            "no git",
            "no generic GitHub writer",
            "no prompt-body/prose decision reads",
            "no standalone dashboard truth source",
            "no standalone dependency truth source",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, entry)
                self.assertIn(needle, skill_section)
        for needle in (
            "#504 是唯一 global dashboard status-card writer carveout",
            "PATCH exactly one host-configured issue comment",
            "禁止 create comments",
            "new daemon",
            "public writer CLI",
            "generic GitHub writer",
            "standalone dashboard/dependency truth source",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, claude)
        self.assertIn('"holistic-status": CommandSpec(', cli_source)
        for forbidden in ("dashboard-writer", "global-status-card", "write-holistic-status"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(f'"{forbidden}"', cli_source)
        self.assertIn('"global-dashboard-status-card"', progress_source)
        self.assertIn('"HOST_HOLISTIC_STATUS_COMMENT_ID"', progress_source)
        self.assertIn("issues/comments/{config[", progress_source)
        self.assertIn('"PATCH"', progress_source)
        self.assertIn("class HolisticStatusProjection", holistic_source)
        for forbidden in ("prompt.read_text", "worker prose", "discussion"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, holistic_source)

    def test_maintainer_directive_entries_have_required_fields(self) -> None:
        self.assertEqual(len(MAINTAINER_DIRECTIVE_ANCHORS), 7)
        mirror_projection = project_markdown(self.mirror)
        for anchor in MAINTAINER_DIRECTIVE_ANCHORS:
            entry = mirror_entry(self.mirror, anchor)
            entry_projection = project_markdown(entry)
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, mirror_projection.anchors)
                self.assertIn(f"{MIRROR_RELATIVE}#{anchor}", self.skill)
                self.assertIn("source_kind: maintainer_directive", entry)
                self.assertIn("no_new_runtime_authority", entry)
                for field in MAINTAINER_DIRECTIVE_REQUIRED_FIELDS:
                    self.assertIn(field, entry_projection.bullet_fields)

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
            "bounded GitHub label/state driven dirty candidate projection",
            "whose every GitHub list query uses a managed-label predicate before any dirty-label search predicate",
            "managed-intersecting at query construction",
            "terminal-complete closed managed items are excluded from steady-state scans",
            "unmanaged CLOSED search noise must not be returned to the reconciler or `peek` lens",
            "Human-label exactness neither authorizes human-label mutation nor blocks phase/cleanup/stuck reconciliation",
            "human labels are preserved as-is",
            "test_closed_label_reconciler.py",
            "test_peek_status_lens.py",
            "test_gh_accounting.py",
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
            "clean `EXIT=0` source marker",
            "review truth table `reject==0 && approve>=1 && all required reviewers present && all required reviewer heads equal live PR head`",
            "missing/stale per-reviewer head SHA",
            "`wakeup-plan` action `head_sha` is not reviewer-head authority",
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

        self.assertIn("action `head_sha` cannot substitute for reviewer-head authority", entry)
        self.assertIn("all required reviewer heads equal live PR head", entry)
        self.assertIn("all required reviewer heads equal live PR head", self.skill)
        self.assertIn(
            "Consensus→implement projection durable fact source is the consensus judge artifact frontmatter, `## If consensus`, `Implementation owner`, and Implement plan structured fields `scope_paths`, `old_pattern`, `new_principle`, and optional `verification_hints`; parser failure emits no implementation action.",
            self.skill,
        )
        meta_judge = read(META_JUDGE_PROMPT)
        self.assertIn(
            "structured fields read by wakeup-plan from this judge artifact only, not from solver artifacts or prompt-body free text",
            meta_judge,
        )

        for forbidden in (
            "no arbitrary git/gh command",
            "workflow tag/release",
            "prompt-body decision",
            "standalone authorization from `wakeup-plan`",
            "argv/shell/cmd/command_line/commands/env/git/gh/executor/lifecycle_authority/lifecycle_owner/generic command fields",
            "`ControllerTurnDecision`",
            "controller-turn worker",
            "active-active scheduler",
            "`.refactor-loop/host.env` as host production SSOT",
            "generic lifecycle actor",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, entry)
                self.assertIn(forbidden, self.skill)

    def test_task_spawn_claim_490_preserves_local_spawn_claim_boundary(self) -> None:
        entry = mirror_entry(self.mirror, "task-spawn-claim-490")
        spawn_source = read(SKILL_ROOT / "scripts" / "codex_refactor_loop" / "spawn.py")
        claim_source = read(SKILL_ROOT / "scripts" / "codex_refactor_loop" / "task_spawn_claim.py")

        for required in (
            "#490",
            "consensus-rnd-cli spawn-codex",
            "spawn.py",
            "same-device per-codex-task mutual exclusion only",
            "TaskSpawnClaimStore.acquire(...)",
            ".refactor-loop/locks/spawn-tasks/<safe-task-id>.lock",
            "O_CREAT|O_EXCL",
            "ProcessSupervisor.supervise(...)",
            "SPAWN_CLAIM_HELD:task=<task_id> lock=<lock_path>",
            "returns 0 skip/noop",
            "metadata matches the task/log path",
            "`EXIT=` marker",
            "test_task_spawn_claim.py",
            "test_spawn_claim.py",
            "test_spawn_supervisor.py",
            "test_runtime_exception_authorization_sources.py",
            "test_skill_reference_anchors.py",
            "no_new_runtime_authority",
        ):
            with self.subTest(required=required):
                self.assertIn(required, entry)
                self.assertIn(required, self.skill)

        for forbidden in (
            "no upstream read-lock preflight",
            "no standalone authorization from the lock artifact",
            "no cross-device per-work claim",
            "no lifecycle authority",
            "no host-defined lease scope",
            "no generic distributed lock",
            "no `ActiveControllerLease` replacement",
            "no host production SSOT",
            "no issue/PR lifecycle",
            "no label mutation",
            "no commit",
            "push",
            "merge",
            "tag",
            "release",
            "generic lifecycle actor",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, entry)

        self.assertIn("TaskSpawnClaimStore(repo_root).acquire(task_id, log_path=log_path)", spawn_source)
        self.assertLess(spawn_source.index("TaskSpawnClaimStore(repo_root).acquire"), spawn_source.index("ProcessSupervisor().supervise"))
        self.assertIn("os.O_CREAT | os.O_EXCL", claim_source)
        self.assertIn('return any(line.startswith("EXIT=") for line in tail)', claim_source)

        self.assertIn("#396 是唯一 unattended wakeup-runner carveout", self.repo_rules)
        self.assertIn("`wakeup-plan` 是唯一 action projection fact source但不是 standalone authorization source", self.repo_rules)
        self.assertNotIn("named helper `dispatch_design_consensus` through phase9-router deterministic routes", self.repo_rules)
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

    def test_phase9_router_open_state_gate_authorizes_only_prompt_source_reads(self) -> None:
        entry = mirror_entry(self.mirror, "phase9-router-open-state-gate-229")

        for token in (
            "`gh issue list --repo <owner/repo> --state open --label crnd:lifecycle:managed --json number,title,labels`",
            "`gh api repos/<slug>/issues/<N>`",
            "`gh api repos/<slug>/issues/<N>/comments?per_page=20`",
            "issue state/title/body",
            "bounded recent comments",
            "router-injected issue source snapshots",
            "router-local prompt-source projection",
            "not grant daemon process-spawn, durable schema, host production SSOT, or lifecycle authority",
            "`gh api repos/<slug>/issues/<N> --jq .state`",
            "`gh api repos/<slug>/issues/<N> --jq '[.labels[].name]'`",
            "DesignConsensusIssueIntake",
            "five built-in phase9 direct routes",
            "queues each r1 solver role (`minimal`, `structural`, `delete`) whose role-specific ledger key, r1 evidence/log, and in-flight target are absent as that role's r1 `HARNESS_SPAWN_INTENT`",
            "existing evidence/log/in-flight for one solver role suppresses only that role",
            "`META_RESOLVED:re-design` from reflector to source-adjacent `marker.round + 1` solver triplet",
            "source-OPEN gate",
            "labels-only live read",
            "clean consensus judge log",
            "terminal design-consensus phase labels",
            "crnd:phase:consensus-reached",
            "crnd:phase:implementing",
            "crnd:phase:merged",
            "crnd:phase:closed",
            "phase9-source-not-open",
            "phase9-source-state-unavailable",
            "phase9-terminal-eligibility:",
            "phase9-already-consensus",
            "design-consensus solver `HARNESS_SPAWN_INTENT` actions for terminal phase labels only",
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
        self.assertNotIn("with no r1 solver evidence", self.skill)
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
        self.assertIn("terminal design-consensus suppression must not write spawn intent or dispatch ledger", entry)
        self.assertIn("without writing spawn intent or dispatch ledger", self.skill)

    def test_phase9_router_terminal_design_gate_matches_implementation(self) -> None:
        entry = mirror_entry(self.mirror, "phase9-router-open-state-gate-229")
        router_projection = python_projection(SKILL_ROOT / "scripts" / "codex_refactor_loop" / "phase9" / "router.py")
        wakeup_projection = python_projection(SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py")
        combined_authority = "\n".join((entry, self.skill))

        for token in (
            "Phase9TerminalDecision",
            "_solver_dispatch_terminal_decision",
            "_terminal_consensus_judge_source",
            "_live_terminal_issue_source",
            "_append_terminal_fallback_event",
        ):
            with self.subTest(token=token):
                self.assertIn(token, router_projection.class_names | router_projection.function_names)
        for token in (
            "phase9-terminal-eligibility:",
            "phase9-already-consensus",
            "META_JUDGE_DONE:consensus:",
        ):
            with self.subTest(token=token):
                self.assertIn(token, router_projection.string_literals)
        for token in (
            "PHASE_CONSENSUS_REACHED",
            "PHASE_IMPLEMENTING",
            "PHASE_MERGED",
            "PHASE_CLOSED",
        ):
            with self.subTest(token=token):
                self.assertIn(token, router_projection.attribute_names)
        self.assertIn("[.labels[].name]", router_projection.string_literals)
        self.assertNotIn("{state:.state,labels:[.labels[].name]}", router_projection.string_literals)
        self.assertIn("DESIGN_CONSENSUS_TERMINAL_PHASES", wakeup_projection.assigned_names)
        self.assertIn("_design_consensus_marker_is_router_owned", wakeup_projection.function_names)
        self.assertIn("_is_design_consensus_solver_dispatch_intent", wakeup_projection.function_names)
        for token in (
            "phase9-terminal-eligibility:",
            "phase9-already-consensus",
            "`gh api repos/<slug>/issues/<N> --jq '[.labels[].name]'`",
            "crnd:phase:consensus-reached",
            "crnd:phase:implementing",
            "crnd:phase:merged",
            "crnd:phase:closed",
            "clean consensus judge log",
            "terminal design-consensus phase labels",
            "design-consensus solver `HARNESS_SPAWN_INTENT` actions for terminal phase labels only",
        ):
            with self.subTest(authority_token=token):
                self.assertIn(token, combined_authority)

    def test_phase9_router_actor_health_recovery_stays_router_private(self) -> None:
        entry = mirror_entry(self.mirror, "phase9-router-open-state-gate-229")
        router = read(SKILL_ROOT / "scripts" / "codex_refactor_loop" / "phase9" / "router.py")
        combined_authority = "\n".join((entry, self.skill))

        for token in (
            "Phase9ActorHealth",
            "_recover_actor_health",
            "_quarantine_markerless_solver_logs",
            "_recover_stale_ledgered_actors",
            "_actor_recovery_allowed",
            "_read_pending_spawn_intent_logs",
            "phase9-actor-markerless-quarantine",
            "actor_health_recovery",
            "STALE_REVIVAL_HOURS",
        ):
            with self.subTest(router_token=token):
                self.assertIn(token, router)
        for token in (
            "router-private `Phase9ActorHealth`",
            "markerless clean solver logs",
            "quarantine",
            "phase9-actor-markerless-quarantine",
            "actor_health_recovery",
            "STALE_REVIVAL_HOURS",
            "source issue is OPEN",
            "terminal gate is open",
            "no valid actor marker",
            "no target log",
            "no equivalent legacy log",
            "no pending `HARNESS_SPAWN_INTENT`",
            "no live in-flight `spawn-codex --log <target>`",
            "append-only ledger row",
            "no public revive command",
            "no new runtime exception",
        ):
            with self.subTest(authority_token=token):
                self.assertIn(token, combined_authority)
        self.assertNotIn("revive-design-consensus", self.skill)
        self.assertNotIn("revive-design-consensus", entry)

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
            "metadata_only_193",
            "issue/PR `author.login` and `updatedAt` are planning/routing/stale metadata only",
            "must not authorize side effects",
            "per-work owner authority",
            "claim/lease scope",
            "stale takeover permit",
            "#191 `ActiveControllerLease` / `require_active_controller(...)` gate",
            "`GitHubAuthenticatedActor` may read the current authenticated GitHub API caller/token login",
            "repo permission",
            "branch protection/ruleset/CODEOWNERS/required-review results",
            "only after the #191 owner gate and before the first GitHub API mutation",
            "fail-closed admission checks",
            "not per-work owner",
            "daemon owner",
            "takeover permit",
            "action-specific lifecycle authorization",
            "generic lifecycle actor",
            "bypass for #191/#238/#322/#396/#403",
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
        cli_projection = python_projection(SKILL_ROOT / "scripts" / "codex_refactor_loop" / "cli.py")
        banners_projection = python_projection(SKILL_ROOT / "scripts" / "codex_refactor_loop" / "banners.py")
        actions_projection = python_projection(SKILL_ROOT / "scripts" / "codex_refactor_loop" / "controller_actions.py")
        observability_entry = mirror_entry(self.mirror, "observability-comment-writers-53")

        self.assertNotIn("post-banner", cli_projection.dict_keys)
        self.assertNotIn("banners.main", cli_projection.string_literals)
        for forbidden in ("main", "load_optional_context", "post_status_banner"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, banners_projection.function_names | banners_projection.imported_names)
        self.assertIn("post_status_banner", actions_projection.function_names)
        self.assertIn("_normalize_lifecycle_target_or_raise", actions_projection.function_names)
        self.assertIn("post-banner", actions_projection.string_literals)
        self.assertIn("gh_comment_command", actions_projection.imported_names)
        for required in (
            "#191 `ActiveControllerLease` / `require_active_controller(...)` gate",
            "not a cross-device write permit",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.skill)
                self.assertIn(required, observability_entry)

    def test_wakeup_runner_batch_budget_is_spawn_only_and_per_action_validated(self) -> None:
        entry = mirror_entry(self.mirror, "wakeup-runner-396")
        combined_authority = "\n".join((entry, self.skill, self.repo_rules))

        for required in (
            "对每个 action 重新验证",
            "each executable action",
            "spawn codex",
            "dispatch reviewers/fix/remote-ci worker",
            "merge PR under review truth table",
            "close managed item from drop marker",
            "publish release through #322",
            "禁止任意 git/gh 命令",
            "label/merge/close outside existing helper or named #396 helper",
            "generic lifecycle actor",
            "test_wakeup_runner.py",
        ):
            with self.subTest(required=required):
                self.assertIn(required, combined_authority)

        forbidden_action_fields = {
            "argv",
            "args",
            "shell",
            "cmd",
            "command_line",
            "commands",
            "env",
            "git",
            "gh",
            "executor",
            "lifecycle_authority",
            "lifecycle_owner",
        }
        for forbidden in sorted(forbidden_action_fields):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, combined_authority)
        self.assertIn("test_forbidden_fields_fail_closed", read(SKILL_ROOT / "scripts" / "test_wakeup_runner.py"))

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
