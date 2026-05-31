#!/usr/bin/env python3
"""Validate intra-file reference links in SKILL.md."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__)
SKILL_ROOT = SCRIPT_PATH.parents[1]
REPO_ROOT = SCRIPT_PATH.parents[3]
README_MD = REPO_ROOT / "README.md"
SKILL_MD = SKILL_ROOT / "SKILL.md"
REFERENCE_MD = SKILL_ROOT / "REFERENCE.md"

sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from codex_refactor_loop.workflow_spec import FORBIDDEN_FIELD_NAMES  # noqa: E402
from codex_refactor_loop.workflow_stages import WORKFLOW_STAGES, format_stage  # noqa: E402


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def slugify_heading(heading: str) -> str:
    heading = re.sub(r"^\s*#+\s*", "", heading).strip().lower()
    heading = re.sub(r"<[^>]+>", "", heading)
    heading = re.sub(r"[^\w\u4e00-\u9fff -]", "", heading)
    heading = re.sub(r"\s+", "-", heading)
    return heading


def reference_anchors(reference: str) -> set[str]:
    anchors = set(re.findall(r'<a\s+id="([^"]+)"></a>', reference))
    for line in reference.splitlines():
        if line.startswith("#"):
            anchors.add(slugify_heading(line))
    return anchors


def section_after_anchor(markdown: str, anchor: str) -> str:
    marker = f'<a id="{anchor}"></a>'
    _, found, after_anchor = markdown.partition(marker)
    if not found:
        raise AssertionError(f"missing markdown anchor: {anchor}")
    return _section_after_first_heading(after_anchor)


def section_after_heading(markdown: str, heading: str) -> str:
    pattern = re.compile(rf"(?m)^## {re.escape(heading)}$")
    match = pattern.search(markdown)
    if not match:
        raise AssertionError(f"missing markdown heading: {heading}")
    return _section_after_first_heading(markdown[match.start() :])


def _section_after_first_heading(markdown: str) -> str:
    lines = markdown.splitlines(keepends=True)
    if not lines:
        return ""
    start = 0
    for index, line in enumerate(lines):
        if re.match(r"^##\s+", line):
            start = index
            break
    for index in range(start + 1, len(lines)):
        if re.match(r"^##\s+", lines[index]):
            return "".join(lines[start:index])
    return "".join(lines[start:])


class SkillReferenceAnchorTests(unittest.TestCase):
    # Refactor (iter1/issue-139):
    #   Old pattern: Wake-source 契约措辞自相矛盾:SKILL.md/REFERENCE.md 多处写三选一(Monitor / task-notification / ScheduleWakeup 任一即可),与 checklist step15 / ownership 的必维持 Monitor 冲突,新会话据此漏挂 Monitor bridge。
    #   New principle: 统一语义:每个 controller 会话必须 arm/confirm persistent daemon-event Monitor bridge;task-notification / ScheduleWakeup 仅作 turn 级 completion/fallback,非 Monitor 替代。删除所有三选一/or-ScheduleWakeup 弱化措辞,替换 test_skill_entrypoint_contract.py 与 test_skill_reference_anchors.py 两个 source-regression 入口,不引入 SessionWakeSourceContract 等新命名,不新增 helper/schema/daemon,不改 CLAUDE.md/Tier/lifecycle。严格按 .refactor-loop/runs/phase9-issue139-r2-judge.md 的 Implement plan 逐条改。
    def setUp(self) -> None:
        self.skill = read(SKILL_MD)
        self.readme = read(README_MD)

    def test_reference_file_was_merged_into_skill(self) -> None:
        self.assertFalse(REFERENCE_MD.exists())
        self.assertIn("## Detailed reference", self.skill)
        self.assertEqual(1, self.skill.count('id="downstream-install-walkthrough"'))
        self.assertIn("## Downstream install walkthrough", self.skill)

    def test_all_skill_intra_file_reference_links_resolve(self) -> None:
        links = re.findall(r"\(#([^)#\s]+)\)", self.skill)
        self.assertGreaterEqual(len(links), 12)
        available = reference_anchors(self.skill)

        for anchor in links:
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, available)

    def test_skill_contains_required_detailed_sections(self) -> None:
        required_anchors = (
            "controller-contract-details",
            "host-runtime-details",
            "status-and-escalation-templates",
            "work-unit-contract",
            "specialized-state-artifacts",
            "batching-heuristics",
            "recovery-playbook",
            "daemon-command-bodies",
            "label-bootstrap-loops",
            "historical-bilingual-notes",
        )
        available = reference_anchors(self.skill)
        for anchor in required_anchors:
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, available)

    def test_workflow_stage_index_uses_registry_display_and_anchors(self) -> None:
        available = reference_anchors(self.skill)
        index = section_after_heading(self.skill, "Workflow Stage Index")
        self.assertIn("scripts/codex_refactor_loop/workflow_stages.py", self.skill)
        for stage in WORKFLOW_STAGES:
            with self.subTest(slug=stage.slug):
                self.assertIn(format_stage(stage), index)
                self.assertIn(stage.slug, index)
                self.assertIn(stage.detail_anchor, available)

    def test_skill_is_the_single_heavy_manual_after_merge(self) -> None:
        skill_lines = len(self.skill.splitlines())

        self.assertGreaterEqual(skill_lines, 3000)
        self.assertIn("Detailed specifications, heavy templates, schemas, command bodies, and recovery playbooks", self.skill)

    def test_no_absolute_reference_links_in_entrypoint(self) -> None:
        self.assertNotRegex(self.skill, r"/Users/[^)\s]+")
        self.assertNotRegex(self.skill, r"REFERENCE\.md#/[^\s)]+")

    def test_skill_documents_two_entry_modes_near_top(self) -> None:
        top = "\n".join(self.skill.splitlines()[:200])
        for needle in (
            "## Two entry modes",
            "audit-driven",
            "issue-driven / Path A",
            "catalog-derived design issue label bundle",
            "crnd:lifecycle:managed",
            "crnd:phase:design-solving",
            "crnd:human:auto",
            "Legacy issue-entry labels are migration aliases only",
            "Audit is a seed producer, not the only entry",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, top)

    def test_skill_documents_phase9_solver_source_contract(self) -> None:
        phase9 = section_after_heading(
            self.skill,
            "Consensus-rnd Phase design-consensus — Multi-solver design consensus (sole authorization gate)",
        )
        for needle in (
            "### Solver source contract",
            "WORK_UNIT_SOURCE_REF",
            "source_ref",
            "gh-issue-<N>",
            "gh issue view <N>",
            "issue body/comments are the scope source",
            "must not be fabricated",
            "A missing audit `evidence:` block is not by itself a defect for manual issues",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, phase9)

    def test_downstream_install_walkthrough_contract(self) -> None:
        # Refactor (iter1/issue-141):
        #   Old pattern: downstream install steps without an installer were split across README, SKILL statusline text, and restart helper text, with no one-step walkthrough.
        #   New principle: Downstream install walkthrough centralizes setup, README/SKILL links point at it, and source-regression locks required host surfaces.
        walkthrough = section_after_anchor(self.skill, "downstream-install-walkthrough")
        combined_links = "\n".join((self.readme, self.skill))
        self.assertNotIn("REFERENCE.md#downstream-install-walkthrough", combined_links)
        self.assertIn("SKILL.md#downstream-install-walkthrough", combined_links)
        self.assertIn("(#downstream-install-walkthrough)", self.skill)
        self.assertIn("Downstream install walkthrough", combined_links)

        required = (
            "host.env.example",
            ".refactor-loop/host.env",
            "consensus-rnd-cli restart-daemons",
            "consensus-rnd-cli statusline",
            "source .refactor-loop/host.env && exec",
            ".git",
            "CI",
            "policy",
            "HOST_*",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, walkthrough)

        forbidden_paths = (
            "scripts/install.sh",
            "scripts/installer.sh",
            "scripts/install-host-runtime.py",
            "scripts/install-consensus-rnd-cli statusline",
            "INSTALL.md",
        )
        for forbidden in forbidden_paths:
            with self.subTest(forbidden=forbidden):
                self.assertFalse((REPO_ROOT / forbidden).exists(), forbidden)

        self.assertEqual(1, walkthrough.count("HOST_*"))
        self.assertNotIn("HOST_TEST_FILE_GLOBS |", walkthrough)
        self.assertIn("according to the Host env surface matrix", walkthrough)
        self.assertIn("conditional fail-closed surfaces such as `MAINTAINER_WHITELIST`", walkthrough)
        self.assertNotIn(
            "including `REPO_ROOT`, `GH_REPO_SLUG`, `BUILD_CMD`, `TEST_CMD`, `SOURCE_GLOBS`, and `MAINTAINER_WHITELIST`",
            walkthrough,
        )

    def test_skill_documents_update_check_notify_only_contract(self) -> None:
        section = section_after_heading(self.skill, "Notify-only update check(per #231)")
        for needle in (
            "VERSION.json",
            "VersionSourceManifest",
            ".version-bump.json",
            "consensus-rnd-cli update-check",
            "notify-only",
            "$UPDATE_CHECK_ENABLE",
            ".refactor-loop/state/update-check.json",
            "host-owned",
            "create a daemon",
            "statusline-snapshot.json",
            "test_statusline.py",
            "skills/codex-refactor-loop/authorizations/runtime-exceptions.md#update-check-231",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)
        for forbidden in (
            "copy, overwrite, reinstall",
            "run installers",
            "mutate `.git`",
            "touches the network",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, section)

    def test_skill_documents_daemon_event_monitor_command(self) -> None:
        self.assertIn(
            "tail -n 0 -F .refactor-loop/.controller-pending-events.log .refactor-loop/.concurrency-alert.log 2>/dev/null \\",
            self.skill,
        )
        self.assertIn("grep --line-buffered -v '^==> ' \\", self.skill)
        self.assertIn("grep --line-buffered .", self.skill)
        self.assertIn("forwards every non-empty line", self.skill)
        self.assertIn("filtering only `tail -F` file headers", self.skill)

    def test_skill_rejects_unconditional_daemon_not_wake_source(self) -> None:
        self.assertIn(
            "daemon alone is not a wake source; daemon event files become a wake source only through a mounted Monitor bridge",
            self.skill,
        )
        self.assertNotIn(
            "不产生 harness task-notification,不是 wake 源",
            self.skill,
        )

    def test_reference_requires_session_monitor_and_fallback_only_wakes(self) -> None:
        required = (
            "每个 controller session **必须先维护** active daemon-event Monitor bridge",
            "后两者不是 Monitor substitute",
            "ScheduleWakeup 是 task-notification 丢失或长时间无完成通知时的 turn-level **fallback**",
            "不是 daemon-event immediate lane,也不是 session-level Monitor substitute",
            "turn 结束前必须先确认 daemon-event Monitor bridge active",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, self.skill)

        forbidden = (
            "每个 turn 结束**必须**有已确认的下次唤醒源:active daemon-event Monitor bridge, 在飞 task-notification, 或 ScheduleWakeup 返回 `scheduled`",
            "turn 结束前心里要有一个**已确认的下次唤醒源**(daemon-event Monitor bridge active, task-notification 在飞, 或 ScheduleWakeup 已注册)",
        )
        for needle in forbidden:
            with self.subTest(needle=needle):
                self.assertNotIn(needle, self.skill)
        self.assertNotRegex(
            self.skill,
            r"active daemon-event Monitor bridge, 在飞 task-notification, 或 ScheduleWakeup",
        )

    def test_no_checked_in_daemon_event_monitor_helper(self) -> None:
        scripts_dir = SKILL_ROOT / "scripts"
        self.assertFalse((scripts_dir / "daemon_event_monitor.sh").exists())
        self.assertFalse((scripts_dir / "daemon-event-monitor-bridge.sh").exists())

    def test_skill_documents_phase9_router_daemon_boundary(self) -> None:
        self.assertIn("consensus-rnd-cli phase9-router --daemon --repo-root", self.skill)
        self.assertIn("Allowlist(唯一 direct spawn authority)", self.skill)
        self.assertIn("phase9-issue<N>-r<R>-<minimal|structural|delete|judge|reflector>.log", self.skill)
        self.assertIn("solver-issue<N>-r<R>-<minimal|structural|delete>.log", self.skill)
        self.assertIn("meta-judge-issue<N>-r<R>.log", self.skill)
        self.assertIn("issue/round 来自 filename identity", self.skill)
        self.assertIn("public marker payload remains role-local", self.skill)
        self.assertIn("daemon-owned output logs remain `phase9-issue...`", self.skill)
        self.assertIn("clean `^EXIT=0`", self.skill)
        self.assertIn(".refactor-loop/phase9-router-ledger.jsonl", self.skill)
        for token in (
            "route",
            "target_actor",
            "clean_exit_solver_logs",
            "solver_input_prompts",
            "judge_input_solver_logs",
            "judge_prompt_path",
            "judge_prompt_template_path",
            "judge_prompt_scope",
            "independence_check",
            "phase9-triplet-evidence-invalid",
            "Router recovery/idempotency reads only `key`",
            "meta-judge decisions read solver logs, not ledger evidence",
            "render full `prompts/meta-judge.md`",
            "missing template",
            "phase9-meta-judge-template-unavailable",
            "phase9-meta-judge-scope-invalid",
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.skill)
        self.assertIn(".controller-pending-events.log", self.skill)
        self.assertIn("no lifecycle authority", self.skill)
        self.assertIn("`gh issue view <N> --json state`", self.skill)
        self.assertIn("state-only", self.skill)
        self.assertIn("phase9-source-not-open", self.skill)
        self.assertIn("phase9-source-state-unavailable", self.skill)
        self.assertIn("skills/codex-refactor-loop/authorizations/runtime-exceptions.md#phase9-router-open-state-gate-229", self.skill)
        self.assertIn("must not introduce ControllerEvent, ControllerCommand, ControllerOrchestrator", self.skill)

    def test_meta_judge_prompt_documents_router_scoped_input_boundary(self) -> None:
        meta_judge = (SKILL_ROOT / "prompts" / "meta-judge.md").read_text(encoding="utf-8")
        for needle in (
            "## Router-scoped input boundary",
            "`${SOLVER_MINIMAL_PATH}`",
            "`${SOLVER_STRUCTURAL_PATH}`",
            "`${SOLVER_DELETE_PATH}`",
            "gh issue view ${ISSUE_NUMBER}",
            "Do not search for, infer from, or copy sibling judge artifacts",
            "solver frontmatter `issue` is not `${ISSUE_NUMBER}`",
            "`${META_JUDGE_OUTPUT_PATH}` is not the judge output path",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, meta_judge)

    def test_skill_documents_single_active_controller_lease_boundary(self) -> None:
        # Refactor (impl/issue191-single-active-controller): Old pattern:
        # multi-device controller writes were described as local daemon facts.
        # New principle: SKILL.md locks one cross-device active-controller
        # lease and forbids per-work/distributed scheduler expansion.
        for needle in (
            "| Active controller |",
            "## Named runtime exception - active controller lease(per #191)",
            "GitHub/已 push git 面只承载一个 `ActiveControllerLease`",
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
            "active_controller=noop:not-owner",
            "Worker throughput remains owner-local via `$CODEX_FLOOR`",
            "no cross-device floor aggregation",
            "per-work claim",
            "host-defined lease scope",
            "daemon ownership matrix",
            "active-active scheduler",
            "generic distributed lock library",
            "#193 metadata-only invariant",
            "issue/PR `author.login` and `updatedAt` may only be planning/routing/stale read-only metadata",
            "must not become side-effect authorization",
            "per-work owner authority",
            "claim/lease scope",
            "stale takeover permit",
            "`require_active_controller(...)` gate on issue/PR target writes",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.skill)

    def test_stale_revival_documents_updated_at_as_metadata_only(self) -> None:
        # Refactor (iter193/issue-193):
        #   Old pattern: stale updatedAt could be read as takeover permission.
        #   New principle: stale routing is visibility metadata, while all
        #   issue/PR write side effects stay behind #191 ActiveControllerLease.
        for needle in (
            "Stale `updatedAt` is routing metadata only",
            "it does not authorize GitHub comments",
            "label edits",
            "PR merges",
            "issue closes",
            "takeover",
            "Those writes remain gated solely by #191 `ActiveControllerLease` ownership through `require_active_controller(...)`",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.skill)

    def test_phase9_router_issue167_refactor_self_doc_source_regression(self) -> None:
        router = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "phase9" / "router.py").read_text(encoding="utf-8")
        for token in (
            "Refactor (iter1/issue-167)",
            "Old pattern: solver triplet handoff recorded only the base dispatch row",
            "durable triplet provenance",
            "visible same-round peer artifact reference failure",
            "New principle: keep row-level router-private ledger provenance",
            "narrow fail-closed peer artifact token check",
            "do not add a",
            "standalone evidence file",
            "hash",
            "lifecycle authority",
        ):
            with self.subTest(token=token):
                self.assertIn(token, router)

    def test_skill_documents_cli_runtime_authority_fact_source(self) -> None:
        required = (
            "Refactor (iter1/issue-166)",
            "Old pattern: CLI command authority was represented by coarse read_only metadata",
            "New principle: `cli.py::COMMANDS[*].authority` is the inline closed-token mechanical fact source",
            "dev-sync's integration-worktree git surface",
            "## CLI runtime authority fact source(per #166)",
            "cli.py::COMMANDS[*].authority",
            "unique mechanical fact source for CLI runtime command authority",
            "CommandSpec.authority",
            "inline closed-token tuple",
            "git/gh/spawn/write-artifact/label/merge",
            "narrow allowlist",
            "durable authorization source",
            "no lifecycle authority by default",
            "Worker prompt authority remains in prompt contracts and prompt tests",
            "not as pseudo-commands in `COMMANDS`",
            "behavior test and source-regression anchor",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, self.skill)

    def test_skill_project_rules_surface_has_no_apply_opt_in(self) -> None:
        # Refactor (iter218/issue-218):
        #   Old pattern: ensure-project-rules 是 public CLI 默认写 host policy 文件($PROJECT_RULES),违反 skill 无 host 改动权边界
        #   New principle: 改为 read-only check-project-rules probe + patch artifact:probe 只读判 sentinel block,非 current 写 .refactor-loop/runs/ patch 并 fail-closed 不派 actor;删 ensure-project-rules/_atomic_write,不引入 PROJECT_RULES_WRITE_ENABLE。严格按 plan 逐条改。
        for needle in (
            "host-owned read-only prompt/bootstrap evidence",
            "patch artifact",
            "fail closed",
            "不得派 audit / solver / reviewer / implement actor",
            ".refactor-loop/runs/project-rules-fixed-point.patch",
            "consensus-rnd-cli check-project-rules",
            "must never apply host policy edits",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.skill)
        contract_text = "\n".join(
            line
            for line in self.skill.splitlines()
            if "Refactor (iter218/issue-218)" not in line and "Old pattern:" not in line and "New principle:" not in line
        )
        for forbidden in (
            "幂等向 $PROJECT_RULES 写入",
            "ensure-project-rules",
            "PROJECT_RULES_WRITE_ENABLE",
            "--apply",
            "ProjectRulesFixedPointEnsurer",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, contract_text)

    def test_phase9_router_filename_identity_source_regression(self) -> None:
        router = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "phase9" / "router.py").read_text(encoding="utf-8")
        helper = router + "\n" + (SKILL_ROOT / "scripts" / "consensus-rnd-cli").read_text(encoding="utf-8")
        combined = "\n".join((self.skill, router, helper))
        for token in ("phase9-issue", "solver-issue", "meta-judge-issue"):
            with self.subTest(token=token):
                self.assertIn(token, router)
                self.assertIn(token, self.skill)
        self.assertIn("parse_phase9_log_identity", router)

    def test_operational_name_contract_owner_map_and_registry_ban(self) -> None:
        claude = read(REPO_ROOT / "CLAUDE.md")
        for needle in (
            "operational interface",
            "owner-local 事实源",
            "canonical write policy",
            "legacy read / migration policy",
            "behavior test + source-regression",
            "不得被第二套通用命名 registry 或全仓审美 lint 复制",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, claude)

        section = section_after_heading(self.skill, "Operational names")
        for needle in (
            "scripts/codex_refactor_loop/phase9/router.py",
            "phase9-issue<N>-r<R>-<role>",
            "solver-issue<N>-r<R>-<role>",
            "meta-judge-issue<N>-r<R>",
            "scripts/codex_refactor_loop/monitors/progress.py",
            "progress-comment target extraction",
            "scripts/codex_refactor_loop/monitors/concurrency.py",
            "mutable/read-only dispatch `task_id` prefix classification",
            "scripts/codex_refactor_loop/controller_actions.py",
            "scripts/codex_refactor_loop/git.py",
            "refactor/iter<I>-<cluster>",
            "rollup/<integration_sha>",
            "scripts/codex_refactor_loop/labels.py",
            "crnd:<group>:<slug>",
            "scripts/codex_refactor_loop/cli.py::COMMANDS",
            "scripts/codex_refactor_loop/workflow_stages.py",
            "codex_refactor_loop/names.py",
            "check_naming.py",
            "naming_policy.py",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)

        package_root = SKILL_ROOT / "scripts" / "codex_refactor_loop"
        for forbidden in ("names.py", "check_naming.py", "naming_policy.py"):
            with self.subTest(forbidden=forbidden):
                self.assertFalse((package_root / forbidden).exists())
                self.assertFalse((SKILL_ROOT / "scripts" / forbidden).exists())

    def test_operational_name_production_literal_owner_allowlist(self) -> None:
        production_files = [path for path in (SKILL_ROOT / "scripts" / "codex_refactor_loop").rglob("*.py")]
        allowed: dict[str, set[str]] = {
            "phase9-issue": {
                "controller_actions.py",
                "git.py",
                "monitors/progress.py",
                "monitors/concurrency.py",
                "peek.py",
                "phase9/router.py",
                "wakeup_plan.py",
            },
            "solver-issue": {"monitors/concurrency.py", "phase9/router.py"},
            "meta-judge-issue": {"monitors/concurrency.py", "phase9/router.py"},
            "review-pr": {"monitors/progress.py", "monitors/concurrency.py", "peek.py"},
            "fix-pr": {"monitors/progress.py", "monitors/concurrency.py"},
            "crnd:": {"labels.py", "triage.py"},
            "refactor/iter": {"controller_actions.py", "git.py"},
            "rollup/": {"controller_actions.py", "sync/dev.py"},
            "COMMANDS": {"cli.py", "restart.py"},
            "WorkflowStage": {"workflow_stages.py", "workflow_spec.py"},
        }
        for token, allowed_paths in allowed.items():
            actual = {
                str(path.relative_to(SKILL_ROOT / "scripts" / "codex_refactor_loop"))
                for path in production_files
                if token in path.read_text(encoding="utf-8")
            }
            with self.subTest(token=token):
                self.assertLessEqual(actual, allowed_paths, actual - allowed_paths)

        progress = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "monitors" / "progress.py").read_text(encoding="utf-8")
        concurrency = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "monitors" / "concurrency.py").read_text(encoding="utf-8")
        controller_actions = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        git = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "git.py").read_text(encoding="utf-8")
        self.assertIn("PROGRESS_PHASE9_TARGET_RE", progress)
        self.assertIn("MAIN_READONLY_DISPATCH_PATTERNS", concurrency)
        self.assertIn("SAFE_WORKTREE_CLUSTER_RE", controller_actions)
        self.assertIn("SAFE_WORKTREE_CLUSTER_RE", git)
        progress_executable = "\n".join(line for line in progress.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn(r"^phase9-issue([0-9]+).*", progress_executable)

    def test_host_workflow_spec_contract_locks_consensus_and_lifecycle_invariants(self) -> None:
        for needle in (
            "HOST_WORKFLOW_SPEC",
            "HostWorkflowSpec",
            "repo-relative JSON",
            "Empty or unset keeps built-in behavior",
            "data-only route vocabulary",
            "events, host stages, work-unit kinds, roles, prompt bindings, consensus policies, and issue-intake mappings",
            "reserved `host:` namespace",
            "WorkflowInvariantValidator",
            "rejects attempts to overwrite built-ins",
            "public compatibility aliases",
            "marker families",
            "producers",
            "cluster aliases",
            "grants no lifecycle authority",
            "command, shell, argv, git, commit, push, merge, close, label mutation, assignee, milestone, import",
            "cannot downgrade consensus",
            "at least three independent solvers",
            "exactly one independent judge",
            "peer-output isolation",
            "fixed marker families",
            "First-version scope is bounded",
            "not a DAG executor",
            "does not create public marker aliases",
            "router direct-spawn ignores host `roles`, `dispatch`, and `consensus_policies` completely",
            "always the built-in `minimal`/`structural`/`delete` solver triplet plus built-in `judge`",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.skill)

        workflow_spec = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "workflow_spec.py").read_text(encoding="utf-8")
        for token in (
            "class HostWorkflowSpec",
            "class WorkflowInvariantValidator",
            "HOST_WORKFLOW_SPEC",
            "FORBIDDEN_FIELD_NAMES",
            "FIXED_MARKER_FAMILIES",
            "peer_output_isolation",
            "at least three independent solvers",
        ):
            with self.subTest(token=token):
                self.assertIn(token, workflow_spec)

        contract = re.search(
            r"HostWorkflowSpec grants no lifecycle authority: no (?P<fields>.*?) fields are allowed\.",
            self.skill,
        )
        self.assertIsNotNone(contract)
        documented_fields = {
            item.strip().removeprefix("or ")
            for item in contract.group("fields").split(",")
        }
        documented_fields.remove("label mutation")
        documented_fields.add("label")
        for field in documented_fields:
            with self.subTest(field=field):
                self.assertIn(field, FORBIDDEN_FIELD_NAMES)

    def test_phase9_direct_spawn_allowlist_ignores_host_workflow_spec_sources(self) -> None:
        router = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "phase9" / "router.py").read_text(encoding="utf-8")
        router_section = router[router.index("class Phase9Router") :]
        heading = "### Consensus-rnd Phase design-consensus router daemon command body"
        start = self.skill.index(heading)
        end = self.skill.index("### Daemon vs controller", start)
        contract_section = self.skill[start:end]

        for token in (
            "HostWorkflowSpec is not a phase9 direct-spawn authority",
            "host `roles`, `dispatch`, and `consensus_policies` are validation/display/data-only projection surfaces",
            "must not alter this allowlist or block the built-in router routes",
            "SOLVER_DONE:<minimal|structural|delete>:*",
            "both spawn r(S+1) minimal/structural/delete solvers",
        ):
            with self.subTest(token=token):
                self.assertIn(token, contract_section)

        for required in (
            "ROLES = (\"minimal\", \"structural\", \"delete\")",
            "JUDGE_ROLE = \"judge\"",
            "return ROLES",
            "return JUDGE_ROLE",
            "Phase9 direct-spawn ignores HostWorkflowSpec role/dispatch/policy data entirely",
        ):
            with self.subTest(required=required):
                self.assertIn(required, router)

        for forbidden in (
            "load_validated_workflow_spec",
            "WorkflowSpecError",
            "workflow_spec",
            "consensus_policies",
            "host_workflow_spec_path",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, router_section)

    def test_phase9_router_filename_identity_source_regression_keeps_role_markers(self) -> None:
        router = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "phase9" / "router.py").read_text(encoding="utf-8")
        helper = router + "\n" + (SKILL_ROOT / "scripts" / "consensus-rnd-cli").read_text(encoding="utf-8")
        combined = "\n".join((self.skill, router, helper))
        self.assertIn("Refactor (issue-100/router-filename-identity)", router)
        self.assertIn("SOLVER_DONE:<role>:", combined)
        self.assertNotIn("SOLVER_DONE:<issue>:<round>:", combined)
        self.assertIn("consensus-rnd-cli", helper)

    def test_phase9_converge_adjacent_round_helper_source_regression(self) -> None:
        # Refactor (iter6/issue-244): Old pattern: router/docs treated
        # converge payloads as target-round-only. New principle: one local
        # adjacent helper maps source-round and legacy next-round payloads to
        # r(S+1), and non-adjacent payloads fall back without a projection module.
        router_path = SKILL_ROOT / "scripts" / "codex_refactor_loop" / "phase9" / "router.py"
        router = router_path.read_text(encoding="utf-8")
        meta_judge = (SKILL_ROOT / "prompts" / "meta-judge.md").read_text(encoding="utf-8")
        combined = "\n".join((self.skill, meta_judge, router))

        for token in (
            "_converge_target_round",
            "canonical payload is the judge log source round",
            "source-round and legacy",
            "non-adjacent payloads fall back",
            "clean rS judge canonical payload is `round-S`",
            "legacy `round-(S+1)`",
            "non-adjacent payload mismatch falls back",
        ):
            with self.subTest(token=token):
                self.assertIn(token, combined)

        self.assertEqual(router.count("_converge_target_round("), 3)
        self.assertNotIn("target_round <= marker.round", router)
        self.assertFalse((router_path.parent / "decision.py").exists())
        self.assertNotIn("MetaJudgeRouteProjection", combined)


class AutoLoopStatuslineContractTests(unittest.TestCase):
    # Refactor (iter1/issue-140):
    #   Old pattern: statusline install wording was not pinned to the local
    #   manual one-liner surface.
    #   New principle: keep install/uninstall anchors local to the statusline
    #   section and reject installer-script drift.
    def setUp(self) -> None:
        self.skill = read(SKILL_MD)

    def test_skill_contains_statusline_consensus_section(self) -> None:
        self.assertRegex(
            self.skill,
            r"(?m)^## Claude Code statusline\(per #51 consensus\)$",
            "statusline consensus section must be an independent markdown heading",
        )
        section = section_after_heading(self.skill, "Claude Code statusline(per #51 consensus)")
        required = (
            "statusLine",
            "consensus-rnd-cli statusline",
            "install",
            "installer script",
            "daemon",
            "Named runtime exception",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, section)

    def test_statusline_contract_does_not_add_daemon_or_installer(self) -> None:
        scripts_dir = SKILL_ROOT / "scripts"
        self.assertFalse((scripts_dir / "installer.sh").exists())
        self.assertFalse((scripts_dir / "install-consensus-rnd-cli statusline").exists())
        combined = self.skill
        forbidden = (
            "StatuslineDaemon",
            "StatusLineDaemon",
            "StatuslineController",
            "new daemon class",
            "自动修改 settings.json",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, combined)

    def test_statusline_install_uninstall_anchor_is_local_and_manual(self) -> None:
        section = section_after_heading(self.skill, "Claude Code statusline(per #51 consensus)")
        required = (
            "statusLine",
            "consensus-rnd-cli statusline",
            "installer script",
            "Uninstall",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, section)
        scripts_dir = SKILL_ROOT / "scripts"
        self.assertFalse((scripts_dir / "installer.sh").exists())
        self.assertFalse((scripts_dir / "install-consensus-rnd-cli statusline").exists())

    def test_skill_documents_statusline_snapshot_schema(self) -> None:
        self.assertIn("### Statusline snapshot schema", self.skill)
        for field in (
            '"actual"',
            '"expected"',
            '"floor"',
            '"p0_streak"',
            '"freeze_minutes"',
            '"open_pr_count"',
            '"open_issue_count"',
        ):
            with self.subTest(field=field):
                self.assertIn(field, self.skill)


if __name__ == "__main__":
    unittest.main()
