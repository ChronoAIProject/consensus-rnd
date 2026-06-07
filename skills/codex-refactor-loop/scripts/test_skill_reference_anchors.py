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
from codex_refactor_loop.restart import restart_managed_daemon_names  # noqa: E402


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


def section_after_anchor_until_heading(markdown: str, anchor: str, level: int) -> str:
    marker = f'<a id="{anchor}"></a>'
    _, found, after_anchor = markdown.partition(marker)
    if not found:
        raise AssertionError(f"missing markdown anchor: {anchor}")
    pattern = re.compile(rf"(?m)^#{{1,{level}}}\s+")
    first_heading = pattern.search(after_anchor)
    start = first_heading.end() if first_heading else 0
    match = pattern.search(after_anchor, start)
    return after_anchor[: match.start()] if match else after_anchor


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

    def test_reference_file_was_merged_into_skill_as_documented_architecture(self) -> None:
        self.assertFalse(REFERENCE_MD.exists())
        self.assertIn("## Detailed reference", self.skill)
        self.assertIn("single controller contract and detailed reference by maintainer directive", self.skill)
        self.assertIn("use intra-file anchor links", self.skill)
        self.assertIn("Controller Contract Index", self.skill)
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
            "detailed-reference",
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

    def test_github_posting_contract_documents_render_time_inline_rules(self) -> None:
        contract = section_after_heading(self.skill, "GitHub Posting Contract")
        for needle in (
            "contains `## GitHub post` and the fixed token `{{GITHUB_POST_RULES_CONTRACT}}`",
            "`_github-post-rules.md` is the template-time source",
            "rendered worker prompt inlines",
            "not a worker runtime path",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, contract)
        self.assertNotIn("references `prompts/_github-post-rules.md`", contract)

    def test_skill_is_the_single_heavy_manual_after_merge(self) -> None:
        available = reference_anchors(self.skill)
        for anchor in (
            "detailed-reference",
            "controller-contract-details",
            "host-runtime-details",
            "daemon-command-bodies",
        ):
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, available)
                self.assertIn(f"(#{anchor})", self.skill)
        self.assertIn("single controller contract and detailed reference by maintainer directive", self.skill)
        self.assertIn("物理拆 REFERENCE.md 后跨平台加载/维护退化", self.skill)
        self.assertIn("Detailed specifications, heavy templates, schemas, command bodies, and recovery playbooks", self.skill)

    def test_project_rules_document_optional_reference_contract(self) -> None:
        claude = read(REPO_ROOT / "CLAUDE.md")
        for needle in (
            "重型参考默认可下沉到 `REFERENCE.md`",
            "跨平台 agent 加载或维护实证显示物理拆分退化",
            "允许单文件 SKILL.md 同时承载 controller 合同与详细参考",
            "用 intra-file anchor 分层并由 source-regression 锁住" + "事实源" + "唯一性",
            "重型参考默认拆 `REFERENCE.md`",
            "可留在 SKILL.md 的详细参考区并用 intra-file anchor 暴露",
            "`skills/<name>/REFERENCE.md` 是可选重型参考层",
            "未使用 `REFERENCE.md` 时,SKILL.md 的详细参考区是该 skill 的权威参考层",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, claude)

    def test_no_absolute_reference_links_in_entrypoint(self) -> None:
        self.assertNotRegex(self.skill, r"/Users/[^)\s]+")
        self.assertNotRegex(self.skill, r"REFERENCE\.md#/[^\s)]+")

    def test_skill_documents_main_path_and_fallback_producer_near_top(self) -> None:
        top = "\n".join(self.skill.splitlines()[:200])
        for needle in (
            "## Main path and fallback producer",
            "open actionable catalog-managed GitHub issue/PR resolution",
            "issue-driven / Path A",
            "catalog-derived design issue label bundle",
            "crnd:lifecycle:managed",
            "crnd:phase:design-solving",
            "crnd:human:auto",
            "Historical non-`crnd:*` issue-entry labels are unmanaged residue",
            "`audit` remains a stable compatibility producer value and fallback issue producer",
            "no open actionable managed issue/PR",
            "Audit produces or updates issues that feed back into the main path",
            "not a co-equal entry mode",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, top)
        self.assertIn('<a id="two-entry-modes"></a>', top)
        self.assertNotIn("The loop has two supported entry modes", top)
        self.assertNotIn("audit-driven", top)

    def test_detailed_reference_uses_issue_pr_main_path_and_audit_fallback(self) -> None:
        producers = section_after_heading(self.skill, "Producers")
        work_intake = section_after_heading(self.skill, "Consensus-rnd Phase work-intake — Fallback issue production")
        bootstrap = section_after_heading(
            self.skill,
            "Consensus-rnd Phase bootstrap — Bootstrap (first wakeup only)",
        )
        detailed = "\n".join((producers, work_intake, bootstrap))

        for needle in (
            "The default main path is open actionable managed issue/PR resolution",
            "`audit` is the fallback raw artifact issue producer",
            "`audit` remains the stable compatibility producer value and fallback issue producer, not the default\nmain path",
            "先扫 open actionable managed issue/PR 并派 next-step actor",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, detailed)
        for forbidden in (
            "`audit` remains the default producer",
            "The default work-unit producer is `audit`",
            "派默认 work-unit producer",
            "默认 audit",
            "默认 producer",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, detailed)

    def test_project_rules_do_not_duplicate_skill_local_main_path_contract(self) -> None:
        claude = read(REPO_ROOT / "CLAUDE.md")
        for forbidden in (
            "issue resolution 是主路径",
            "audit 是 fallback producer",
            "open actionable managed GitHub issue/PR",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, claude)
        self.assertIn("Refactoring, issue-solving, and repository R&D are different entry surfaces", self.readme)
        self.assertIn("## Main path and fallback producer", self.skill)

    def test_project_rules_document_repo_python_code_policy(self) -> None:
        claude = read(REPO_ROOT / "CLAUDE.md")
        python_policy = section_after_heading(claude, "Python 代码规范")

        for needle in (
            "只约束本仓库内 Python skill scripts 和测试代码",
            "不是 host 项目规范",
            "公共函数和方法必须有类型注解",
            "`dataclass`、`TypedDict` 或明确投影类型",
            "`Mapping[str, Any]`、`dict[str, Any]` 一类宽边界只用于外部 JSON adapter 层",
            "I/O、GitHub/git 副作用、环境读取、文件系统写入与决策逻辑分层",
            "纯函数优先",
            "过长函数/文件和高复杂度分支不得在新增或触碰时继续膨胀",
            "具体后续重构计划",
            "fail-closed 路径必须抛出具体、可诊断的异常或返回明确错误原因",
            "禁止裸 `except`、吞错、静默 fallback",
            "命名表达职责边界",
            "不把 runtime、issue 编号或临时实现泄露进稳定接口",
            "哲学文档仍不写 schema/identifier 版本后缀",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, python_policy)

    def test_default_issue_intake_claim_anchor_and_contract_are_documented(self) -> None:
        available = reference_anchors(self.skill)
        anchor = "named-runtime-exception---default-issue-intake-claimper-623"
        self.assertIn(anchor, available)
        section = section_after_anchor(self.skill, anchor)

        for required in (
            "DefaultIssueIntakeClaim",
            "DEFAULT_ISSUE_INTAKE_ENABLE",
            "apply_default_issue_intake_claim",
            "crnd:default-issue-intake-claim",
            "crnd:default-issue-intake-stop",
            "test_default_issue_intake.py",
            "test_wakeup_runner.py",
        ):
            with self.subTest(required=required):
                self.assertIn(required, section)
        self.assertIn("default-issue-intake-claim-623", read(REPO_ROOT / "skills/codex-refactor-loop/authorizations/runtime-exceptions.md"))

    def test_issue_decomposition_discoverability_requires_plan_level_judge_fields(self) -> None:
        section = section_after_anchor(self.skill, "large-issue-decomposition")
        for needle in (
            "IssueDecompositionPlan",
            "parent_issue",
            "source_consensus_artifact",
            "body_artifact_path",
            "parent_update",
            "crnd:lifecycle:managed",
            "crnd:phase:design-solving",
            "crnd:human:auto",
            "parent epic remains open/tracking",
            "META_JUDGE_DONE:consensus:decompose",
            "phase9-router-fallback",
            "completed_marker_actions()",
            "kind: completed-marker",
            "first `consensus:decompose`, solver artifacts, prompt body, validator result, worker output, and `.refactor-loop/host.env` are not apply authorization",
            "exact named `controller_action=\"apply_issue_decomposition_plan\"`",
            "plan_level_design_consensus_judge_artifact",
            "plan path, digest, and proof",
            "wakeup_runner.py` then revalidates clean plan-level judge source marker",
            "live parent open/tracking",
            "sentinel idempotency",
            "wakeup_plan.py` is not the #403 read-model/status/authorization owner",
            "must not project any other decompose action/status",
            "`kind=\"issue-decomposition-apply\"`",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)
        for forbidden in (
            "public issue factory",
            "parent issue close",
            "reopen",
            "body edit",
            "title edit",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, section)

    def test_runtime_retention_anchor_documents_canonical_owner_and_alias(self) -> None:
        section = section_after_anchor(self.skill, "named-runtime-exception--runtime-retentionper-437")
        for needle in (
            "RuntimeRetention(per #437)",
            "runtime-retention-437",
            "`consensus-rnd-cli runtime-retention` is the canonical command",
            "$RUNTIME_RETENTION_ENABLE=true",
            "$REPO_ROOT/.refactor-loop/{logs,prompts,runs}",
            "fails closed until a file-level planner proof surface and producer exist",
            "deleted=0 kept=0",
            "same inode",
            ".controller-pending-events.log",
            ".refactor-loop/state/runtime-retention-plan.json",
            "git worktree remove <path>",
            "git worktree prune",
            "no `git fetch`",
            "no GitHub write or lifecycle authority",
            "test_runtime_retention.py",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)

    def test_task_spawn_claim_documents_spawn_boundary_not_distributed_authority(self) -> None:
        section = section_after_anchor(self.skill, "task-spawn-claim-490")
        spawn_pattern = self.skill

        for needle in (
            "consensus-rnd-cli spawn-codex",
            "spawn.py",
            "same-device per-codex-task atomic spawn-claim enforcement point",
            "TaskSpawnClaimStore.acquire(...)",
            ".refactor-loop/locks/spawn-tasks/<safe-task-id>.lock",
            "O_CREAT|O_EXCL",
            "ProcessSupervisor.supervise(...)",
            "SPAWN_CLAIM_HELD:task=<task_id> lock=<lock_path>",
            "exits 0 as skip/noop",
            "fail closed nonzero before supervisor launch",
            "log has an `EXIT=` marker",
            "not #191 `ActiveControllerLease`",
            "not a cross-device per-work claim",
            "not lifecycle authority",
            "not host production SSOT",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)
        self.assertIn("[Task spawn claim](#task-spawn-claim-490)", spawn_pattern)
        self.assertIn("Callers may use logs, readiness, pending intents, or process counts for planning", spawn_pattern)
        self.assertIn("not the enforcement point", spawn_pattern)

    def test_issue_504_global_dashboard_status_card_anchor_and_boundaries(self) -> None:
        section = section_after_heading(self.skill, "Named runtime exception - global-dashboard-status-card(per #504)")

        for needle in (
            "HolisticStatusProjection",
            "single shared read-only algorithm",
            "`consensus-rnd-cli holistic-status` renders the full local card",
            "`peek` may only reuse `render_peek_summary(...)`",
            "progress-reporter",
            "$HOST_HOLISTIC_STATUS_ENABLE=true",
            "$HOST_HOLISTIC_STATUS_ISSUE_NUMBER",
            "$HOST_HOLISTIC_STATUS_COMMENT_ID",
            "PATCH exactly one host-configured issue comment id",
            "no new daemon",
            "no public writer CLI",
            "no create comment",
            "no issue body edit",
            "no PR body/title edit",
            "no Discussions",
            "no labels",
            "no create/close/reopen/merge",
            "no tag/release",
            "no git",
            "no generic GitHub writer",
            "no prompt-body/prose decision reads",
            "no multi-carrier grammar",
            "no standalone dashboard truth source",
            "no standalone dependency truth source",
            "test_holistic_status.py",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)

    def test_issue_541_patrol_inspector_anchor_and_boundaries(self) -> None:
        section = section_after_heading(self.skill, "Named runtime exception - patrol-inspector issue-intake(per #541)")

        for needle in (
            "PatrolFinding",
            "PatrolIssuePublisher",
            "$PATROL_INSPECTOR_ENABLE=true",
            "#191 active-controller owner gate",
            "exception logs",
            "runs artifacts",
            "wakeup-plan/peek projections",
            "GitHub managed item snapshot",
            "durable fingerprint",
            "fixed patrol/design-intake label bundle",
            "crnd:lifecycle:managed",
            "crnd:phase:design-solving",
            "crnd:human:auto",
            "crnd:triage:pending",
            "update may edit only the patrol issue body",
            "no modification of non-patrol issues or PRs",
            "no close/reopen/merge",
            "no PR edit",
            "no label mutation outside the create-time fixed bundle",
            "no commit/push/tag/release",
            "no public inspector CLI",
            "no second dashboard/comment writer",
            "no #396 `wakeup-plan` issue-create action",
            "no #506 issue factory",
            "no generic GitHub writer",
            "no generic issue factory",
            "test_patrol_inspector.py",
            "test_patrol_issue_publisher.py",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)

    def test_issue_579_consensus_gate_proof_anchor_and_boundaries(self) -> None:
        section = section_after_anchor(self.skill, "consensus-gate-proof")

        for needle in (
            "ConsensusGateProof",
            "scripts/codex_refactor_loop/consensus_gate.py",
            "skills/codex-refactor-loop/authorizations/runtime-exceptions.md#consensus-gate-proof-579",
            "controller-private pure proof-validity contract",
            "not a public CLI",
            "not a public CLI, runtime exception, wakeup action, proof-ticket/resume system, command bus, or lifecycle authority",
            "target_kind",
            "target_ref",
            "target_digest",
            "decision_producer_id",
            "producer_id",
            "role",
            "artifact",
            "artifact_digest",
            "verdict",
            "required_roles",
            "verdict_rule",
            "scope_paths",
            "single-worker self-certification",
            "target digest mismatch",
            "missing required roles",
            "duplicate or overlapping producers",
            "cmd",
            "argv",
            "shell",
            "command_line",
            "commands",
            "env",
            "git",
            "gh",
            "executor",
            "lifecycle_authority",
            "lifecycle_owner",
            "args",
            "controller_action",
            "proof_ticket",
            "resume_ticket",
            "does not authorize GitHub, git, file lifecycle",
            "#191 active-controller owner gate",
            "helper-specific preconditions",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)

    # Refactor (iter364/issue364):
    #   Old pattern: Path-A solvers dispatched with --cd $REPO_ROOT (integration checkout) can't see work-unit source when the issue references files on a divergent non-integration branch, emitting spurious no-plan and wasting rounds.
    #   New principle: Contract-only source locator: SKILL solver source contract + 3 solver prompts document a read-only source-locator recipe (git show <ref>:<path> / raw URL / gh api / host.env), classify missing/invalid locator as source-location-missing-or-invalid; NO new projection/parser/header/module.
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
            "router-injected issue source snapshot is the preferred scope source",
            "bounded issue title/body and recent comments",
            "prompt projection only",
            "not durable schema, host production SSOT, lifecycle authority",
            "`gh issue view <N>` issue body/comments are fallback-only",
            "must not be fabricated",
            "A missing audit `evidence:` block is not by itself a defect for manual issues",
            "Path A issue body/comments that cite files absent from the current checkout",
            "read-only source locator",
            "git show <ref>:<path>",
            "raw URL",
            "gh api",
            "explicit host-owned file named by `CONSENSUS_RND_HOST_ENV`",
            "must not directly emit a generic `no-plan`",
            "source-location-missing-or-invalid",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, phase9)

    def test_router_injected_issue_source_snapshot_contract_matches_router_surface(self) -> None:
        phase9 = section_after_heading(
            self.skill,
            "Consensus-rnd Phase design-consensus — Multi-solver design consensus (sole authorization gate)",
        )
        router = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "phase9" / "router.py").read_text(encoding="utf-8")
        combined = "\n".join((phase9, router))

        for needle in (
            "IssueSourceSnapshot",
            "def _issue_source_snapshot_markdown",
            "def _read_issue_source_snapshot",
            "def _issue_snapshot_preferred_text",
            "router-injected issue source snapshot is the preferred scope source",
            "`gh issue view <N>` issue body/comments are fallback-only",
            "Snapshot unavailable.",
            "Fallback only: run `gh issue view",
            "/comments?per_page=20",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, combined)

    def test_release_countdown_contract_is_wakeup_plan_only_status_projection(self) -> None:
        milestone = section_after_heading(self.skill, "Milestone priority(强制)")
        wakeup = section_after_heading(self.skill, "Wakeup Skeleton")

        for needle in (
            "crnd:milestone:release-target",
            "release countdown status",
            "explicit-target precedence",
            "non-exclusive milestone fact",
            "crnd:milestone:current` remains dispatch priority only and must not trigger explicit release-target mode by itself",
            "wakeup-plan-only and read-only",
            "status-only, non-dispatchable `release-countdown` action",
            "does not query the GitHub milestones API",
            "default goal countdown",
            "GitHub open milestones",
            "`due_on` ascending",
            "no `due_on` sorted after dated milestones",
            "goal.milestone: null",
            "release-gate scoring source",
            ".version-bump.json",
            "existing release commits projection",
            'activation: "explicit-target" | "default-goal"',
            "goal.milestone",
            "goal.release",
            "goal.release.passed_signals",
            "goal.release.total_signals",
            "goal.release.countdown_to_version",
            "no_lifecycle_authority",
            "targets",
            "from_version",
            "to_version",
            "stability_score",
            "ready",
            "red_signals",
            "blocked_reasons",
            'source: "release-gate"',
            "host.env",
            "statusline snapshots",
            "local state are not a goal SSOT",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, milestone)
        for forbidden in (
            "create a daemon",
            "write state",
            "update statusline",
            "update peek",
            "create a top-level duplicate object",
            "write a release decision",
            "mutate labels",
            "tag",
            "publish a release",
            "add lifecycle authority",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, milestone)
        self.assertIn("Release-countdown status is status-only", wakeup)
        self.assertIn("not dispatchable", wakeup)

    def test_skill_documents_transition_assessment_sidecar_boundary(self) -> None:
        work_unit = section_after_anchor(self.skill, "work-unit-contract")
        producers = section_after_heading(self.skill, "Producers")
        batching = section_after_anchor(self.skill, "batching-heuristics")
        prompts = "\n".join(
            (
                read(SKILL_ROOT / "prompts" / "solver-minimal.md"),
                read(SKILL_ROOT / "prompts" / "solver-structural.md"),
                read(SKILL_ROOT / "prompts" / "solver-delete.md"),
                read(SKILL_ROOT / "prompts" / "meta-judge.md"),
            )
        )

        for needle in (
            "optional read-only `transition_assessment` sidecar",
            "not stable candidate NDJSON",
            "not a work-unit envelope wrapper",
            "not a WorkUnit producer",
            "Missing/malformed/untrusted -> unknown",
            ".refactor-loop/runs/transition-assessments/<safe-work-unit-id>.json",
            "[A-Za-z0-9._-]+",
            "positive-discovery > classifier-shift > formal-hardening > ledger-repair > record-growth > unknown",
            "classifier-surface delta and `net_positive_signal=true`",
            "marker change, branch change, or\nwork-unit token change",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, work_unit)
        self.assertIn("does not extend the WorkUnit\nproducer enum", producers)
        self.assertIn("transition bucket before `risk` and `leverage`", batching)
        self.assertIn("Use only the router-injected validated transition projection", prompts)
        self.assertIn("cannot override the meta-judge truth table", prompts)
        self.assertNotIn("host:<slug>` is allowed", self.skill)

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
            "CONSENSUS_RND_HOST_ENV",
            "consensus-rnd-cli restart-daemons",
            "consensus-rnd-cli statusline",
            'source "$CONSENSUS_RND_HOST_ENV"',
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

    def test_github_workflow_portability_checklist_is_folded_into_skill(self) -> None:
        walkthrough = section_after_anchor(self.skill, "downstream-install-walkthrough")
        checklist = section_after_anchor_until_heading(self.skill, "github-workflow-portability-checklist", 3)
        self.assertIn("SKILL.md#github-workflow-portability-checklist", self.readme)
        self.assertIn("Host GitHub workflow portability", self.readme)
        for needle in (
            "#104 setup is folded into this skill's existing owner surface",
            ".config/consensus-rnd/host.env",
            "HOST_WORKFLOW_SPEC",
            "exactly seven data-only surfaces",
            "`events`, `stages`, `work_unit_kinds`, `roles`, `prompt_bindings`, `consensus_policies`, and `issue_intake_mappings`",
            "no host `.github` edits",
            "no branch-protection probing or edits",
            "Future #357 interactive configuration",
            "must output these same host-owned artifacts",
            "#### Guided GitHub consensus workflow setup",
            ".refactor-loop/runs/github-workflow-setup/<timestamp>/",
            "host-env.patch.md",
            "labels-plan.json",
            "scheduler.md",
            "statusline.json",
            "host-workflow-spec.json",
            "walkthrough.md",
            "Host env surface matrix",
            "host.env.example",
            "scripts/codex_refactor_loop/labels.py",
            "consensus-rnd-cli restart-daemons",
            "consensus-rnd-cli statusline",
            "workflow_spec.py",
            "WorkflowInvariantValidator",
            "`host:` namespace",
            "repo-relative paths",
            "advisory only",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, checklist)
        self.assertIn("GitHub workflow portability checklist", walkthrough)
        self.assertFalse((REPO_ROOT / "skills" / "consensus-github-workflow-setup").exists())
        for forbidden in (
            "HostWorkflowPortabilityProjection",
            "GitHubHostPolicy",
            "HOST_GITHUB_LABEL_MAP",
            "branch-protection probe",
            "Projects adapter",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.skill)
                self.assertNotIn(forbidden, self.readme)

        for forbidden in (
            "summary.json",
            "host-workflow-spec.example.json",
            "renderer",
            "CLI command",
            "setup skill",
            "installer",
            "template directory",
            "root install document",
            "host `.git`",
            "`.github`",
            "CI",
            "policy",
            "branch protection",
            "GitHub labels",
            "issues",
            "PRs",
            "commits",
            "pushes",
            "merges",
            "closes",
            "tags",
            "releases",
            "settings",
            "lifecycle surface",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertIn(forbidden, checklist)

        self.assertNotIn("GuidedWorkflowSetupBundle", self.skill)
        self.assertFalse((REPO_ROOT / "skills" / "github-workflow-setup").exists())
        self.assertFalse((SKILL_ROOT / "scripts" / "codex_refactor_loop" / "setup.py").exists())
        self.assertEqual(0, len(list((SKILL_ROOT / "scripts" / "codex_refactor_loop").glob("*setup*"))))

    def test_release_required_checks_contract_is_host_configurable(self) -> None:
        source_repo_validation = section_after_heading(self.skill, "Skill degradation source-repo validation")
        details = section_after_anchor_until_heading(self.skill, "skill-degradation-source-repo-validation-details", 3)
        release_schema = section_after_anchor_until_heading(self.skill, "release-decision-schema", 3)
        combined = "\n".join((source_repo_validation, details, release_schema))

        for needle in (
            "$HOST_GITHUB_RELEASE_REQUIRED_CHECKS",
            "required_release_checks()",
            "host.env",
            "host configures the exact required GitHub check-run names",
            "release required checks are not hardcoded by source-repo CI job names",
            "Shared Checks API projection sees exact check-run name success for every name in `$HOST_GITHUB_RELEASE_REQUIRED_CHECKS`",
            "auto-release with an empty list fails closed",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, combined)

        stale_contracts = (
            "requires `skill-degradation` beside `contract-tests` and `manifest-version-sync`",
            "release gate `consensus-rnd-cli release-gate:required_checks_recent_green` requires `skill-degradation` beside `contract-tests` and `manifest-version-sync`",
        )
        for stale in stale_contracts:
            with self.subTest(stale=stale):
                self.assertNotIn(stale, combined)

    def test_skill_degradation_documents_private_419_host_fixture_smoke_boundary(self) -> None:
        source_repo_validation = section_after_heading(self.skill, "Skill degradation source-repo validation")
        details = section_after_anchor_until_heading(self.skill, "skill-degradation-source-repo-validation-details", 3)
        combined = "\n".join((source_repo_validation, details))
        for needle in (
            "source-repo CI/release validation covering static contract checks plus one bounded temporary host-fixture smoke for the #419 profile",
            "no `.version-bump.json`",
            "fake/read-only open milestone",
            "RELEASE_AUTO_ENABLE=false",
            "runs only through existing `consensus-rnd-cli check-degradation --static`",
            "writes only a temporary host fixture with host-owned `.config/consensus-rnd/host.env`",
            "reports failures as `host-fixture-smoke` findings in the existing `skill-degradation` check-run",
            "no public clean-room command",
            "no clean-room artifact",
            "no ninth internal release signal",
            "no workflow job",
            "no real GitHub repo lifecycle",
            "no downstream runtime watch",
            "no `.refactor-loop/host.env` production SSOT",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, combined)

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

    def test_skill_documents_controller_release_publisher_exact_sha_gate(self) -> None:
        section = section_after_heading(self.skill, "Named runtime exception — release-publication(per #322)")

        for needle in (
            "skills/codex-refactor-loop/authorizations/runtime-exceptions.md#controller-release-publisher-334",
            "release-pipeline-integrationpost-61",
            "ReleaseRequiredChecksProjection",
            "gh api repos/<slug>/commits/<fresh release commit sha>/check-runs --paginate --slurp",
            "only then `gh release create v<to_version> --target <fresh release commit sha>",
            "Missing `GH_REPO_SLUG`",
            "pending/red/missing/stale exact-SHA required checks",
            "invalid Checks API JSON",
            "Checks API failure",
            "fail closed before release creation",
            "before `.refactor-loop/state/release-publish-result.json` is written",
            "no tag target without exact-SHA green checks",
            "no proof-ticket/resume system",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)

    def test_release_publication_anchor_names_already_bumped_reentry(self) -> None:
        section = section_after_heading(self.skill, "Named runtime exception — release-publication(per #322)")
        for needle in (
            "already-bumped reentry",
            "only preflight mismatch is mapped manifests already equal `to_version`",
            "git show -s --format=%s HEAD",
            "HEAD subject is exactly `Release v<to_version>`",
            "skip only `python3 .github/scripts/bump_version.py --version <to_version>`, `git add .version-bump.json <mapped manifests>`, and `git commit -m \"Release v<to_version>\"`",
            "exact release/reentry commit sha",
            "pending/red/missing/API-fail fail closed",
            "no proof-ticket/resume system",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)

    def test_skill_documents_issue_300_draft_then_ready_merge_contract(self) -> None:
        for needle in (
            "Refactor (issue-300)",
            "gh pr create --draft",
            "open PRs as draft by default",
            "post-decision ready+merge",
            "only when the controller has already decided `MERGE` or `MERGE_WITH_COMMENTS`",
            "marks the PR ready before `gh pr merge`",
            "it never computes Consensus-rnd Phase review-gate reviewer policy",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.skill)
        self.assertIn("`MERGE`: post 中文 merge comment, then call `merge_pr <pr>` for ready+merge.", self.skill)
        self.assertIn(
            "`MERGE_WITH_COMMENTS`: surface comment evidence, post 中文 merge comment, then call `merge_pr <pr>` for ready+merge.",
            self.skill,
        )
        for needle in (
            "`WAIT_EXPLICIT_APPROVAL`: surface comments, do not ready, do not merge",
            "`FIX`: enter fix-retry loop; do not ready, do not merge",
            "`WAIT_OR_REDISPATCH`: wait or re-dispatch invalid/missing reviewer once; do not ready, never merge",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.skill)

    def test_skill_documents_daemon_event_monitor_command(self) -> None:
        self.assertIn(
            "tail -n 0 -F .refactor-loop/.controller-pending-events.log .refactor-loop/.concurrency-alert.log 2>/dev/null \\",
            self.skill,
        )
        self.assertIn("grep --line-buffered -v '^==> ' \\", self.skill)
        self.assertIn("grep --line-buffered .", self.skill)
        self.assertIn("forwards every non-empty line", self.skill)
        self.assertIn("filtering only `tail -F` file headers", self.skill)

    def test_status_banner_surface_is_controller_owned_action(self) -> None:
        self.assertIn("`ControllerActions.post_status_banner`, GitHub labels", self.skill)
        self.assertIn("`ControllerActions.post_status_banner` — controller-internal GitHub banner posting helper.", self.skill)
        self.assertIn("ControllerActions.post_status_banner(BannerRequest(...))", self.skill)
        self.assertIn("#191 active-controller owner gate", self.skill)
        self.assertNotIn("consensus-rnd-cli post-banner", self.skill)

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
        self.assertIn("Allowlist(唯一 direct spawn-intent authority)", self.skill)
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
            "HARNESS_SPAWN_INTENT",
            '`command` field is exactly `"spawn-codex"` as a closed semantic enum',
            "not argv and not shell",
            "actual CLI binary and argv construction live only in the controller/harness consumption layer",
            'dispatch_state="harness-intent"',
        ):
            with self.subTest(token=token):
                self.assertIn(token, self.skill)
        self.assertIn(".controller-pending-events.log", self.skill)
        self.assertIn("no lifecycle authority", self.skill)
        self.assertIn("DesignConsensusIssueIntake", self.skill)
        self.assertIn("queues each r1 solver role (`minimal`, `structural`, `delete`) whose role-specific ledger key, r1 evidence/log, and in-flight target are absent as that role's r1 `HARNESS_SPAWN_INTENT`", self.skill)
        self.assertIn("existing evidence/log/in-flight for one solver role suppresses only that role", self.skill)
        self.assertNotIn("with no r1 solver evidence", self.skill)
        self.assertIn("`ManagedWorkSnapshot` 发现 open managed `crnd:phase:design-solving` issue", self.skill)
        self.assertIn("`.refactor-loop/state/managed-work-snapshot.json`", self.skill)
        self.assertIn("`.refactor-loop/locks/managed-work-snapshot.lock`", self.skill)
        self.assertIn("`MANAGED_WORK_SNAPSHOT_TTL_SECONDS=300`", self.skill)
        self.assertIn("`MANAGED_WORK_SNAPSHOT_STALE_MAX_SECONDS=900`", self.skill)
        self.assertIn("not GitHub live state fact source, not host production SSOT", self.skill)
        self.assertIn("`gh api repos/<slug>/issues/<N>`", self.skill)
        self.assertIn("`gh api repos/<slug>/issues/<N>/comments?per_page=20`", self.skill)
        self.assertIn("The router-injected issue source snapshots are router-local prompt context, not durable schema, host production SSOT, or lifecycle authority", self.skill)
        self.assertIn("`gh api repos/<slug>/issues/<N> --jq .state`", self.skill)
        self.assertIn("`gh api repos/<slug>/issues/<N> --jq '[.labels[].name]'`", self.skill)
        self.assertIn("issue intake, triplet/converge/router-derived stalled continuation", self.skill)
        self.assertIn("wakeup-plan design-consensus completed-marker evidence is status-only", self.skill)
        self.assertIn("design-consensus solver `HARNESS_SPAWN_INTENT` actions for terminal phase labels only", self.skill)
        self.assertIn("state-only", self.skill)
        self.assertIn("labels-only terminal gate", self.skill)
        self.assertIn("phase9-source-not-open", self.skill)
        self.assertIn("phase9-source-state-unavailable", self.skill)
        self.assertIn("phase9-terminal-eligibility:", self.skill)
        self.assertIn("phase9-already-consensus", self.skill)
        self.assertIn("clean consensus judge log", self.skill)
        self.assertIn("terminal design-consensus phase labels", self.skill)
        for label in (
            "crnd:phase:consensus-reached",
            "crnd:phase:implementing",
            "crnd:phase:merged",
            "crnd:phase:closed",
        ):
            with self.subTest(label=label):
                self.assertIn(label, self.skill)
        self.assertIn("skills/codex-refactor-loop/authorizations/runtime-exceptions.md#phase9-router-open-state-gate-229", self.skill)
        self.assertIn("must not introduce ControllerEvent, ControllerCommand, SpawnIntentInbox, spawn-intents, ControllerOrchestrator", self.skill)

    def test_meta_judge_prompt_documents_router_scoped_input_boundary(self) -> None:
        meta_judge = (SKILL_ROOT / "prompts" / "meta-judge.md").read_text(encoding="utf-8")
        for needle in (
            "## Router-scoped input boundary",
            "`${SOLVER_MINIMAL_PATH}`",
            "`${SOLVER_STRUCTURAL_PATH}`",
            "`${SOLVER_DELETE_PATH}`",
            "gh issue view ${ISSUE_NUMBER}",
            "`${WORK_UNIT_PRODUCER}`",
            "`${WORK_UNIT_SOURCE_REF}`",
            "Do not search for, infer from, or copy sibling judge artifacts",
            "solver frontmatter `issue` is not `${ISSUE_NUMBER}`",
            "`${META_JUDGE_OUTPUT_PATH}` is not the judge output path",
            "absence of an existing local audit artifact or existing code-to-delete is neutral",
            "abstain-compatible Path A greenfield frame",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, meta_judge)

    def test_path_a_greenfield_delete_abstain_provenance_is_documented(self) -> None:
        solver_delete = (SKILL_ROOT / "prompts" / "solver-delete.md").read_text(encoding="utf-8")
        meta_judge = (SKILL_ROOT / "prompts" / "meta-judge.md").read_text(encoding="utf-8")
        router = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "phase9" / "router.py").read_text(encoding="utf-8")
        combined = "\n".join((self.skill, solver_delete, meta_judge, router))
        for needle in (
            "WORK_UNIT_PRODUCER=manual-issue (prompt-only provenance)",
            "WORK_UNIT_SOURCE_REF=gh-issue-<N>",
            "`${WORK_UNIT_SOURCE_REF}` is `gh-issue-${ISSUE_NUMBER}`",
            "Path A greenfield",
            "absence of existing local code to delete is neutral evidence",
            "absence of an existing local audit artifact or existing code-to-delete is neutral",
            "compatible with `SOLVER_DONE:delete:abstain:<reason>`",
            "classify as genuinely needed/no current deletion dependency and abstain",
            '"WORK_UNIT_PRODUCER"',
            '"WORK_UNIT_SOURCE_REF"',
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, combined)

    def test_path_a_greenfield_delete_abstain_truth_table_is_authoritative(self) -> None:
        meta_judge = (SKILL_ROOT / "prompts" / "meta-judge.md").read_text(encoding="utf-8")
        combined = "\n".join((self.skill, meta_judge))
        for needle in (
            "all implementation-bearing proposals agree + meta-judge consensus",
            "all three solver outputs are mandatory",
            "Path A greenfield compatible-neutral exception",
            "exactly 2 implementation-bearing `propose` verdicts plus delete `abstain`",
            "${WORK_UNIT_PRODUCER}` is `manual-issue (prompt-only provenance)`",
            "${WORK_UNIT_SOURCE_REF}` is `gh-issue-${ISSUE_NUMBER}`",
            "issue body/comments plus delete reverse-evidence prove greenfield/no current deletion target",
            "delete abstain does not contradict that plan",
            "This is not a generic 2/3 gate and has no host override",
            "Missing/unknown/audit-backed/non-greenfield provenance",
            "delete `false-positive:nothing-to-delete`",
            "delete `escalate:no-plan`",
            "implementation-bearing disagreement",
            "fail closed to convergence",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, combined)

    def test_closed_label_reconciler_documents_bounded_candidate_projection(self) -> None:
        section = section_after_heading(self.skill, "Named runtime exception — closed-label-reconciler(per #238)")
        for token in (
            "bounded GitHub label/state driven dirty candidate projection",
            "whose every GitHub list query uses a managed-label predicate before any dirty-label search predicate",
            "missing terminal phase",
            "residual nonterminal phase",
            "`crnd:lifecycle:stuck`",
            "managed-intersecting at query construction",
            "small recent closed read-only managed window",
            "terminal-complete closed managed items are excluded from steady-state scans",
            "must not receive steady-state per-item view or linked-merge probes",
            "unmanaged CLOSED search noise must not be returned to the reconciler or `peek` lens",
            "Human-label exactness neither authorizes human-label mutation nor blocks phase/cleanup/stuck reconciliation",
            "human labels are preserved as-is",
            "test_gh_accounting.py",
        ):
            with self.subTest(token=token):
                self.assertIn(token, section)

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
            "Same-repo multi-GitHub-user handling is HOLD-collapse",
            "display/admission/accounting/routing/status metadata only",
            "forbidden as partition key",
            "lifecycle owner",
            "lifecycle authority",
            "diagnostics-only helper",
            "`current_github_login`",
            '`identity_authority="display-only"`',
            "must not enter durable lease state or executable action authority",
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
            "_solver_triplet_ledger_fields",
            "_peer_solver_reference_violation",
            "_peer_solver_reference_tokens",
            "clean_exit_solver_logs",
            "solver_input_prompts",
            "judge_input_solver_logs",
            "judge_prompt_scope",
            "phase9-triplet-evidence-invalid",
            "Dispatch ledger evidence:",
            "phase9-router-fallback",
        ):
            with self.subTest(token=token):
                self.assertIn(token, router)

    def test_skill_documents_cli_runtime_authority_fact_source(self) -> None:
        required = (
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
                "wakeup_runner.py",
                "worker_markers.py",
            },
            "solver-issue": {"monitors/concurrency.py", "phase9/router.py"},
            "meta-judge-issue": {"monitors/concurrency.py", "phase9/router.py"},
            "review-pr": {"controller_actions.py", "monitors/progress.py", "monitors/concurrency.py", "peek.py", "wakeup_plan.py", "wakeup_runner.py"},
            "fix-pr": {"monitors/progress.py", "monitors/concurrency.py", "review_fix_dispatch.py", "wakeup_runner.py"},
            "crnd:": {"default_issue_intake.py", "labels.py", "triage.py"},
            "refactor/iter": {"controller_actions.py", "git.py", "implement_lifecycle.py", "wakeup_runner.py"},
            "rollup/": {"controller_actions.py", "sync/dev.py", "wakeup_plan.py", "wakeup_runner.py", "work_items.py"},
            "COMMANDS": {"cli.py", "restart.py", "gh_accounting.py", "gh_invoke.py"},
            "WorkflowStage": {"workflow_stages.py", "workflow_spec.py"},
        }
        for token, allowed_paths in allowed.items():
            actual = {
                str(path.relative_to(SKILL_ROOT / "scripts" / "codex_refactor_loop"))
                for path in production_files
                if token
                in "\n".join(
                    line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
                )
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
            "router direct-spawn-intent ignores host `roles`, `dispatch`, and `consensus_policies` completely",
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

    def test_phase9_direct_spawn_intent_allowlist_ignores_host_workflow_spec_sources(self) -> None:
        router = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "phase9" / "router.py").read_text(encoding="utf-8")
        router_section = router[router.index("class Phase9Router") :]
        heading = "### Consensus-rnd Phase design-consensus router daemon command body"
        start = self.skill.index(heading)
        end = self.skill.index("### Daemon vs controller", start)
        contract_section = self.skill[start:end]

        for token in (
            "HostWorkflowSpec is not a phase9 direct-spawn-intent authority",
            "host `roles`, `dispatch`, and `consensus_policies` are validation/display/data-only projection surfaces",
            "must not alter this allowlist or block the built-in router routes",
            "DesignConsensusIssueIntake",
            "queues each r1 solver role (`minimal`, `structural`, `delete`) whose role-specific ledger key, r1 evidence/log, and in-flight target are absent as that role's r1 `HARNESS_SPAWN_INTENT`",
            "existing evidence/log/in-flight for one solver role suppresses only that role",
            "SOLVER_DONE:<minimal|structural|delete>:*",
            "before queueing r(S+1) minimal/structural/delete solver intents",
            "router-owned stalled predicate",
            "suppress next solvers",
            "`META_RESOLVED:re-design` from reflector to source-adjacent `marker.round + 1` solver triplet",
            "source-adjacent `marker.round + 1`",
        ):
            with self.subTest(token=token):
                self.assertIn(token, contract_section)

        for required in (
            "ROLES = (\"minimal\", \"structural\", \"delete\")",
            "JUDGE_ROLE = \"judge\"",
            "def _solver_roles",
            "return ROLES",
            "def _judge_role",
            "return JUDGE_ROLE",
            "class Phase9Router",
            "Phase9 direct-spawn-intent ignores HostWorkflowSpec role/dispatch/policy data entirely",
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
        self.assertIn("parse_phase9_log_identity", router)
        self.assertIn("PHASE9_LOG_RE", router)
        self.assertIn("SOLVER_LOG_RE", router)
        self.assertIn("META_JUDGE_LOG_RE", router)
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
            "payload_round in {source_round, source_round + 1}",
            "return source_round + 1",
            "return None",
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

    def test_phase9_stalled_is_router_owned_predicate_source_regression(self) -> None:
        # Refactor (issue-304): Old: fresh phase9 meta-judge artifacts could
        # authorize `META_JUDGE_DONE:escalate:stalled`. New: prompt/profile
        # allow only consensus/converge; router checks stalled predicate before
        # r(S+1) solver dispatch, while legacy stalled markers are read-only.
        router = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "phase9" / "router.py").read_text(encoding="utf-8")
        meta_judge = (SKILL_ROOT / "prompts" / "meta-judge.md").read_text(encoding="utf-8")
        marker_contract = (SKILL_ROOT / "scripts" / "test_marker_emission_contract.py").read_text(encoding="utf-8")
        profile_contract = (SKILL_ROOT / "scripts" / "test_role_artifact_profiles.py").read_text(encoding="utf-8")
        combined = "\n".join((self.skill, meta_judge, router, marker_contract, profile_contract))

        for token in (
            "meta-judge emits only consensus/converge",
            "router-owned stalled predicate",
            "_dispatch_stalled_reflector",
            "legacy read-only `META_JUDGE_DONE:escalate:stalled`",
            "r(S+1)",
        ):
            with self.subTest(token=token):
                self.assertIn(token, combined)

        allowlist = re.search(
            r'    "meta-judge\.md": \(\n(?P<body>.*?)\n    \),',
            marker_contract,
            flags=re.S,
        )
        self.assertIsNotNone(allowlist)
        assert allowlist is not None
        self.assertNotIn("META_JUDGE_DONE:escalate:stalled:<short>", allowlist.group("body"))
        self.assertIn(r"^META_JUDGE_DONE:(consensus|converge):.+$", profile_contract)
        self.assertNotIn(r"^META_JUDGE_DONE:(consensus|converge|escalate):.+$", profile_contract)

    def test_phase9_solver_triplet_suppression_fallback_contract_source_regression(self) -> None:
        router = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "phase9" / "router.py").read_text(encoding="utf-8")
        combined = "\n".join((self.skill, router))

        for token in (
            "_solver_triplet_suppression_reason",
            "_append_solver_triplet_suppression_event",
            "phase9-triplet-suppression:",
            "phase9-triplet-target-log-exists",
            "phase9-triplet-equivalent-log-exists",
            "phase9-triplet-in-flight",
            "A solver-triplet-to-judge duplicate with `key` already in the ledger is silent",
            "when the triplet is not ledgered but target log / equivalent legacy judge log / in-flight target suppresses dispatch",
        ):
            with self.subTest(token=token):
                self.assertIn(token, combined)

        self.assertIn("if key in ledger:\n                continue", router)
        self.assertIn("prefix `phase9-triplet-suppression:`", self.skill)
        self.assertIn("reason exactly one of", self.skill)

    def test_structured_consumption_boundary_contract_is_locked(self) -> None:
        wakeup_plan = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py").read_text(encoding="utf-8")
        router = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "phase9" / "router.py").read_text(encoding="utf-8")
        progress = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "monitors" / "progress.py").read_text(
            encoding="utf-8"
        )
        for needle in (
            '<a id="structured-consumption-boundary"></a>',
            "final allowlisted standalone marker/verdict lines",
            "artifact frontmatter",
            "CLI JSON/action fields",
            "artifact paths",
            "EXIT!=0",
            "stream disconnect/503",
            "stuck/crash",
            "missing/invalid structured artifact",
            "router fallback",
            "worker self-post failure",
            "controller must not summarize or transcribe",
            "Worker self-posts own full solver, judge, reviewer, or fix artifacts",
            "REVIEW_ARCHITECT_PATH",
            "REVIEW_TESTS_PATH",
            "REVIEW_QUALITY_PATH",
            "FIX_OUTPUT_PATH",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.skill)
        self.assertIn("DONE_PREFIX_RE.fullmatch", wakeup_plan)
        self.assertNotIn("' '.join(tail_lines(log_path, 40))", wakeup_plan)
        self.assertIn("MARKER_RE.fullmatch", router)
        self.assertNotIn("stripped.find(prefix)", router)
        self.assertIn("Raw log tail is intentionally omitted", progress)
        self.assertIn("异常诊断 tail (non-zero EXIT only)", progress)


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


class WakeupRunnerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill = read(SKILL_MD)
        self.claude = read(REPO_ROOT / "CLAUDE.md")

    def test_wakeup_runner_396_anchor_and_claude_carveout_are_locked(self) -> None:
        available = reference_anchors(self.skill)
        self.assertIn("named-runtime-exception---wakeup-runnerper-396", available)
        for needle in (
            "#396 是唯一 unattended wakeup-runner carveout",
            "evidence-bound closed action projection",
            "`wakeup-plan` 是唯一 action projection fact source但不是 standalone authorization source",
            "不得新增 `ControllerTurnDecision`/controller-turn worker/schema",
            "不得接受 argv/shell/cmd/command_line/commands/env/git/gh/executor/lifecycle_authority/lifecycle_owner/generic command fields",
            "不得把 `.refactor-loop/host.env` 当 host production SSOT",
            "允许动作仅限 spawn codex",
            "allowlisted release-rollup body generation that only writes `.refactor-loop/runs/release-rollup-pr-body.md`",
            "named helper `dispatch_consensus_implementation`",
            "named helper `publish_implementation_output`",
            "named helper `apply_issue_decomposition_plan`",
            "then named helper `open_release_rollup_pr_from_action` after the body exists",
            "named helper `open_release_rollup_pr_from_action`",
            "router guard adjudication",
            "generic codex fallback",
            "prompt-body decision",
            "command fields",
            "new lifecycle authority",
            "publish release through #322",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.claude)

    def test_consensus_implementation_projection_fact_source_is_judge_only(self) -> None:
        wakeup_runner = section_after_heading(self.skill, "Named runtime exception - wakeup-runner(per #396)")
        meta_judge = read(SKILL_ROOT / "prompts" / "meta-judge.md")
        for needle in (
            "Consensus→implement projection durable fact source is the consensus judge artifact frontmatter, `## If consensus`, `Implementation owner`, and Implement plan structured fields `scope_paths`, `old_pattern`, `new_principle`, and optional `verification_hints`; parser failure emits no implementation action.",
            "Consensus implementation readiness is a helper-specific precondition",
            "`consensus_implementation_ready`",
            "`suppressed_reason`",
            "`open_closing_pr`",
            "`remote_iter_branch`",
            "`in_flight_implement`",
            "`scope_conflict_waiting`",
            "overlapping normalized `scope_paths`",
            "PR title/body are worker-authored GitHub-facing artifacts",
            "`.refactor-loop/runs/implementation-pr-${CLUSTER_ID}-title.txt`",
            "`.refactor-loop/runs/implementation-pr-${CLUSTER_ID}-body.md`",
            "exactly one matching `Closes #N`",
            "non-placeholder title/body",
            "empty reservation commit",
            "`early_pr_missing`",
            "exactly one matching open managed PR",
            "`implementation_refresh_needed:stale_base`",
            "named helper `dispatch_consensus_implementation`",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, wakeup_runner)
        for needle in (
            "structured fields read by wakeup-plan from this judge artifact only, not from solver artifacts or prompt-body free text",
            "scope_paths",
            "old_pattern",
            "new_principle",
            "verification_hints",
            "Implementation owner: dispatch implement codex with cluster_id=${CLUSTER_ID}, design_decision_path=<this file>",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, meta_judge)

    def test_batching_heuristics_lock_consensus_implementation_scope_serialization(self) -> None:
        batching = section_after_anchor(self.skill, "batching-heuristics")
        for needle in (
            "For executable consensus→implement wakeup actions",
            "normalizes `scope_paths` to repo-relative file/directory keys",
            "`status_only` with `suppressed_reason=scope_conflict_waiting`",
            "disjoint groups remain parallel",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, batching)

    def test_touched_module_test_ratchet_is_skill_and_prompt_contract(self) -> None:
        hard_rules = section_after_heading(self.skill, "Hard rules (controller-level, propagated into every codex prompt)")
        implement = read(SKILL_ROOT / "prompts" / "implement.md")
        verify = read(SKILL_ROOT / "prompts" / "verify.md")
        guard = SKILL_ROOT / "scripts" / "test_zz_daemon_leak_guard.py"
        combined = "\n".join((hard_rules, implement, verify))

        for needle in (
            "Touched-module test ratchet",
            "fast / hermetic / behavior-first",
            "owner-local fact source",
            "observable behavior or contracts",
            "No suite-level host-wide process-table daemon guard",
            "daemon leak / duplicate coverage belongs in the responsible helper's local fact source",
            "must not scan the current machine with `ps -eo pid=,command=`",
            "不得新增 suite-level host-wide process-table guard",
            "不得新增或保留 suite-level host-wide process-table guard",
            "helper-local fact source",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, combined)
        self.assertFalse(guard.exists())

    def test_implement_prompt_pr_artifact_writes_are_allowed_by_red_line(self) -> None:
        implement = read(SKILL_ROOT / "prompts" / "implement.md")
        flow = implement[implement.index("## 流程") : implement.index("## Marker emission allowlist")]
        red_line = implement[implement.index("## 红线") : implement.index("## 附录")]
        for artifact in (
            "$REPO_ROOT/.refactor-loop/runs/implementation-pr-${CLUSTER_ID}-title.txt",
            "$REPO_ROOT/.refactor-loop/runs/implementation-pr-${CLUSTER_ID}-body.md",
        ):
            with self.subTest(artifact=artifact):
                self.assertIn(artifact, flow)
                self.assertIn(artifact, red_line)

    def test_headless_dogfood_e2e_anchors_router_plan_runner_without_real_external_dependencies(self) -> None:
        source = read(SKILL_ROOT / "scripts" / "test_headless_dogfood_e2e.py")
        for needle in (
            "class HeadlessDogfoodFixture",
            "Phase9Router",
            "build_plan",
            "WakeupRunner",
            "FakeControllerActions",
            "dispatch_consensus_implementation",
            "merge_pr",
            "mock.patch(\"codex_refactor_loop.phase9.router.subprocess.run\"",
            "mock.patch(\"codex_refactor_loop.wakeup_plan.subprocess.run\"",
            "mock.patch(\"codex_refactor_loop.wakeup_runner.PrChecksProjection\"",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, source)
        self.assertNotIn("time.sleep", source)
        self.assertNotIn("gh issue close", source)
        self.assertNotIn("gh pr merge", source)

    def test_wakeup_plan_release_rollup_freshness_prunes_superseded_local_evidence(self) -> None:
        wakeup_runner = section_after_heading(self.skill, "Named runtime exception - wakeup-runner(per #396)")
        wakeup_plan = read(SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py")
        for needle in (
            "prunes stale, terminal, or superseded local evidence",
            "release-rollup freshness may use read-only local `refs/remotes/origin/<review_base>..refs/remotes/origin/<integration>` evidence",
            "local ref probe failure fails open",
            "does not weaken #396 revalidation or create standalone authorization",
            "allowlisted `release-rollup-body` generation that only writes `.refactor-loop/runs/release-rollup-pr-body.md`",
            "router guard adjudication",
            "generic codex fallback",
            "new lifecycle authority",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, wakeup_runner)
        for token in (
            "RELEASE_ROLLUP_BODY_FILE",
            "release-rollup-body",
            "latest_by_integration_sha",
            "_release_rollup_event_is_fresh",
            "refs/remotes/origin/{review_base_branch}",
            "refs/remotes/origin/{integration_branch}",
            "rev-parse",
            "rev-list",
            "return True",
        ):
            with self.subTest(token=token):
                self.assertIn(token, wakeup_plan)

    def test_wakeup_plan_closed_projection_is_not_standalone_authorization(self) -> None:
        section = section_after_heading(self.skill, "Wakeup Skeleton")
        for needle in (
            'mode: "closed-action-projection"',
            'no_lifecycle_authority: true',
            'apply_authority: "wakeup-runner-396-only"',
            'runner_authority: "wakeup-runner-396"',
            "status actions remain `status_only: true` and cannot apply",
            "not standalone authorization source",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, section + self.skill)
        self.assertNotIn("ControllerTurnDecision", self.skill.replace("`ControllerTurnDecision`", ""))

    def test_review_gate_requires_per_reviewer_live_head_binding(self) -> None:
        review_gate = section_after_heading(self.skill, "Consensus-rnd Phase review-gate — Multi-codex PR review with consensus merge")
        wakeup_runner = section_after_heading(self.skill, "Named runtime exception - wakeup-runner(per #396)")
        combined = "\n".join((review_gate, wakeup_runner))
        for needle in (
            "missing/stale per-reviewer head SHA",
            "every required reviewer head SHA equals the live PR head",
            "Review artifact verdict authority does not bypass current-head binding; merge readiness requires every required reviewer artifact to bind to the live PR head.",
            "review truth table `reject==0 && approve>=1 && all required reviewers present && all required reviewer heads equal live PR head`",
            "`wakeup-plan` action `head_sha` is not reviewer-head authority",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, combined)

    def test_restart_managed_daemon_list_projects_owner_local_command_surface(self) -> None:
        self.assertIn("restart.py::DAEMON_COMMANDS", self.skill)
        for daemon_name in restart_managed_daemon_names():
            with self.subTest(daemon_name=daemon_name):
                self.assertIn(daemon_name, self.skill)
        self.assertIn("`consensus-rnd-cli wakeup-runner`", self.skill)
        daemon_bodies = section_after_heading(self.skill, "Daemon command bodies")
        self.assertNotIn("patrol_inspector_daemon", daemon_bodies)

    def test_restart_daemon_contract_uses_restart_owner_local_command_surface(self) -> None:
        self.assertIn("restart.py::DAEMON_COMMANDS", self.skill)
        for stale in (
            "all 7 restart-helper-managed daemons",
            "7 restart-helper-managed daemons",
            "eight restart-helper-managed write daemons",
            "eight write daemons",
            "All eight daemon command bodies",
            "8 个长跑 daemon",
            "全部 8 个 daemon",
            "fewer than the six required restart-helper-managed daemons",
            "one of the six required daemons",
            "6-daemon restart-helper-managed",
        ):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, self.skill)

    def test_no_shared_controller_runtime_registry_or_tick_envelope_scaffold(self) -> None:
        inventory = section_after_anchor_until_heading(self.skill, "tier0-scaffold-inventory", level=3)
        for needle in (
            "prose-only and non-authoritative",
            "not a machine-readable catalog, registry, runtime scaffold, or package API",
            "not imported by runtime code",
            "no dispatch, write, state-transition, or authorization authority",
            "restart.py::DAEMON_COMMANDS",
            "restart_managed_daemon_names()",
            "cli.py::COMMANDS[*].authority",
            "SKILL.md#work-unit-contract",
            "ctx.paths.pending_events",
            ".refactor-loop/.controller-pending-events.log",
            "no pending-events authority",
            "owner-local files",
            "shared `TickOutcome`, `tick_helpers.py`, or controller-runtime catalog",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, inventory)

        package_root = SKILL_ROOT / "scripts" / "codex_refactor_loop"
        forbidden_path_names = {
            "controller_runtime_scaffold.py",
            "controller_runtime_capabilities.py",
            "controller_runtime_catalog.py",
            "tick_helpers.py",
            "tick_outcome.py",
            "tick_outcomes.py",
            "daemon_registry.json",
            "daemon_registry.yaml",
            "daemon_registry.yml",
            "controller_runtime_registry.json",
            "controller_runtime_registry.yaml",
            "controller_runtime_registry.yml",
        }
        forbidden_paths = tuple(path for path in package_root.rglob("*") if path.name in forbidden_path_names)
        for path in forbidden_paths:
            with self.subTest(path=path.relative_to(SKILL_ROOT)):
                self.assertFalse(path.exists())

        package_sources = "\n".join(path.read_text(encoding="utf-8") for path in package_root.rglob("*.py"))
        for token in (
            "class TickOutcome",
            "TickOutcome(",
            "ControllerRuntimeScaffold",
            "ControllerRuntimeCatalog",
            "ControllerRuntimeCapabilities",
            "ControllerRuntimeRegistry",
            "DaemonRegistry",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, package_sources)
        for token in ('".refactor-loop/state.json"', "'/.refactor-loop/state.json'", '"/.refactor-loop/state.json"'):
            with self.subTest(token=token):
                self.assertNotIn(token, package_sources)

    def test_controller_tick_supervisor_anchor_documents_key_only_no_lifecycle_contract(self) -> None:
        section = section_after_anchor(self.skill, "controller-tick-supervisor-553")
        for needle in (
            "SharedControllerProjection",
            "ProjectionRequest",
            "collect_shared_controller_projection()",
            "only shared informer read entrypoint",
            "SharedControllerProjection.to_json()",
            "top-level `freshness` object",
            "generated_at",
            "sources",
            "overall_loaded_ok",
            "failed_source_count",
            "stale_source_count",
            "next_retry_after_seconds",
            "not a new public or parsed read-model authority",
            "Do not add `ControllerProjectionInformer`",
            "ManagedWorkSnapshot",
            "key-only workqueue keys",
            "TickWorkItem(handler,key)",
            "KeyOnlyWorkQueue",
            "backoff",
            "noop",
            "diagnostics only",
            "LegacyDaemonModeGuard",
            "$CONTROLLER_TICK_SUPERVISOR_ENABLE=true",
            "canonical legacy daemon list unchanged",
            "canonical eight legacy daemon names",
            "no generic executor",
            "no pending-events authority",
            "no host production SSOT",
            "no first-pass dev-sync migration",
            "test_shared_controller_projection.py",
            "test_controller_tick_supervisor.py",
            "test_workqueue.py",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)


if __name__ == "__main__":
    unittest.main()
