#!/usr/bin/env python3
"""Source contract tests for the codex-refactor-loop single-file contract/reference skill."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__)
SKILL_ROOT = SCRIPT_PATH.parents[1]
SKILL_MD = SKILL_ROOT / "SKILL.md"
HOST_ENV_EXAMPLE = SKILL_ROOT / "host.env.example"
WAKEUP_PLAN = SKILL_ROOT / "scripts" / "consensus-rnd-cli"
PACKAGE_WAKEUP_PLAN = SKILL_ROOT / "scripts" / "codex_refactor_loop" / "consensus-rnd-cli wakeup-plan"
PACKAGE_WAKEUP_PLAN = SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py"
AUTH_MIRROR = "skills/codex-refactor-loop/authorizations/runtime-exceptions.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def markdown_headings(text: str, level: int = 2) -> list[str]:
    prefix = "#" * level
    return [line for line in text.splitlines() if line.startswith(f"{prefix} ")]


def section_between(text: str, start_heading_re: str, end_heading_re: str) -> str:
    start = re.search(start_heading_re, text, flags=re.MULTILINE)
    end = re.search(end_heading_re, text[start.end() :] if start else "", flags=re.MULTILINE)
    if not start or not end:
        return ""
    return text[start.end() : start.end() + end.start()]


class SkillEntrypointContractTests(unittest.TestCase):
    # Refactor (iter1/issue-139):
    #   Old pattern: Wake-source 契约措辞自相矛盾:SKILL.md/REFERENCE.md 多处写三选一(Monitor / task-notification / ScheduleWakeup 任一即可),与 checklist step15 / ownership 的必维持 Monitor 冲突,新会话据此漏挂 Monitor bridge。
    #   New principle: 统一语义:每个 controller 会话必须 arm/confirm persistent daemon-event Monitor bridge;task-notification / ScheduleWakeup 仅作 turn 级 completion/fallback,非 Monitor 替代。删除所有三选一/or-ScheduleWakeup 弱化措辞,替换 test_skill_entrypoint_contract.py 与 test_skill_reference_anchors.py 两个 source-regression 入口,不引入 SessionWakeSourceContract 等新命名,不新增 helper/schema/daemon,不改 CLAUDE.md/Tier/lifecycle。严格按 .refactor-loop/runs/phase9-issue139-r2-judge.md 的 Implement plan 逐条改。
    def setUp(self) -> None:
        self.skill = read(SKILL_MD)

    def test_frontmatter_contract_is_minimal_and_trigger_only(self) -> None:
        match = re.match(r"\A---\n(?P<body>.*?)\n---\n", self.skill, flags=re.DOTALL)
        self.assertIsNotNone(match)
        body = match.group("body")
        lines = body.splitlines()

        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "name: codex-refactor-loop")
        self.assertTrue(lines[1].startswith("description: Use when "))
        self.assertLessEqual(len(body), 1024)

    def test_audit_is_not_documented_as_default_main_path(self) -> None:
        match = re.match(r"\A---\n(?P<body>.*?)\n---\n", self.skill, flags=re.DOTALL)
        self.assertIsNotNone(match)
        body = match.group("body")
        top = "\n".join(self.skill.splitlines()[:120])

        self.assertIn("name: codex-refactor-loop", body)
        self.assertIn("issue/PR resolution and work-unit loop", body)
        self.assertIn("audit/refactor as a fallback compatibility issue producer", body)
        self.assertIn("# Consensus R&D Work-Unit Loop", top)
        self.assertIn("## Main path and fallback producer", top)
        self.assertIn("open actionable catalog-managed GitHub issue/PR resolution", top)
        self.assertNotIn("audit/refactor as the default compatibility intake", body)
        self.assertNotIn("Audit is the default", top)
        self.assertNotIn("The loop has two supported entry modes", top)

    def test_entrypoint_line_budget_and_controller_contract_headings(self) -> None:
        headings = set(markdown_headings(self.skill))

        for pattern in (
            r"^## Controller Contract Index$",
            r"^## Host .+$",
            r"^## Workflow Stage Index$",
            r"^## Consensus-rnd Phase bootstrap .+Bootstrap .+$",
            r"^## Loop control$",
            r"^## Label .+$",
            r"^## Hard rules .+$",
            r"^## .+language.+$|^## .+语言.+$",
            r"^## Files$",
        ):
            with self.subTest(pattern=pattern):
                self.assertTrue(any(re.match(pattern, heading) for heading in headings))

    def test_mandatory_local_invariants_remain_in_entrypoint(self) -> None:
        required = (
            "⟦AI:AUTO-LOOP⟧",
            "#status-and-escalation-templates",
            "Controller execution may be interactive or #396 `wakeup-runner`",
            "#bootstrap-details",
            "Consensus-rnd Phase bootstrap",
            "phase routing",
            "3/3",
            "CODEX_FLOOR",
            "floor",
            "label",
            "spawn",
            "Hard rules",
            "#language-policy-details",
            "#historical-bilingual-notes",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, self.skill)

    def test_milestone_priority_contract_is_in_skill_entrypoint(self) -> None:
        required = (
            "## Milestone priority",
            "crnd:milestone:current",
            "orthogonal third axis",
            "Legacy milestone labels are migration aliases only",
            "Before any non-milestone existing-issue work or ordinary audit fallback",
            "bootstrap failure / missing wake source, maintainer comment, completed marker same-wakeup route, CI red, and no-gap violation",
            "milestone members = GitHub `crnd:milestone:current` as declared by `codex_refactor_loop.labels`",
            f"{AUTH_MIRROR}#maintainer-directive-milestone-priority",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, self.skill)

    def test_first_wakeup_bootstrap_obligations_are_ordered_in_skill_alone(self) -> None:
        phase0 = section_between(
            self.skill,
            r"^## Consensus-rnd Phase bootstrap .+Bootstrap .+$",
            r"^## Phase Routing$",
        )
        self.assertTrue(phase0)
        obligations = (
            'test -n "${CONSENSUS_RND_HOST_ENV:-}" && source "$CONSENSUS_RND_HOST_ENV"',
            "fail closed",
            "ProjectRulesFixedPointProbe",
            "consensus-rnd-cli check-project-rules",
            "Create `.refactor-loop/{logs,runs,clusters,prompts,worktrees,state}`",
            "integration branch",
            "ensure labels",
            "restart-helper-managed daemons",
            "arm persistent daemon-event Monitor",
            "dispatch producer",
            "confirm the daemon-event Monitor bridge",
        )
        cursor = -1
        for obligation in obligations:
            index = phase0.find(obligation)
            with self.subTest(obligation=obligation):
                self.assertNotEqual(index, -1)
                self.assertGreater(index, cursor)
            cursor = index
        for daemon in (
            "consensus-rnd-cli concurrency",
            "consensus-rnd-cli progress-reporter",
            "consensus-rnd-cli comment-monitor",
            "consensus-rnd-cli dev-sync",
            "consensus-rnd-cli phase9-router",
        ):
            with self.subTest(daemon=daemon):
                self.assertIn(daemon, phase0)

    def test_skill_bootstrap_requires_probe_before_actor_dispatch(self) -> None:
        # Refactor (iter218/issue-218):
        #   Old pattern: ensure-project-rules 是 public CLI 默认写 host policy 文件($PROJECT_RULES),违反 skill 无 host 改动权边界
        #   New principle: 改为 read-only check-project-rules probe + patch artifact:probe 只读判 sentinel block,非 current 写 .refactor-loop/runs/ patch 并 fail-closed 不派 actor;删 ensure-project-rules/_atomic_write,不引入 PROJECT_RULES_WRITE_ENABLE。严格按 plan 逐条改。
        phase0 = section_between(
            self.skill,
            r"^## Consensus-rnd Phase bootstrap .+Bootstrap .+$",
            r"^## Phase Routing$",
        )
        required_order = (
            "ProjectRulesFixedPointProbe",
            "consensus-rnd-cli check-project-rules",
            ".refactor-loop/runs/project-rules-fixed-point.patch",
            "不得派 audit / solver / reviewer / implement actor",
            "Create `.refactor-loop/{logs,runs,clusters,prompts,worktrees,state}`",
        )
        cursor = -1
        for token in required_order:
            index = phase0.find(token)
            with self.subTest(token=token):
                self.assertNotEqual(index, -1)
                self.assertGreater(index, cursor)
            cursor = index

    def test_wake_source_contract_requires_session_monitor_with_fallback_only(self) -> None:
        wake_row = next(line for line in self.skill.splitlines() if line.startswith("| Wake source |"))
        for token in (
            "Every controller session",
            "Arm or confirm the daemon-event Monitor bridge",
            "task-notification",
            "ScheduleWakeup fallback",
        ):
            with self.subTest(token=token):
                self.assertIn(token, wake_row)
        self.assertNotIn("one of three lanes", wake_row)
        self.assertNotRegex(
            wake_row,
            r"Monitor bridge,.*\bor\b.*ScheduleWakeup",
        )
        self.assertIn("#wake-source-rules", wake_row)

    def test_wakeup_skeleton_orders_monitor_before_sweep_and_spawn(self) -> None:
        skeleton = section_between(
            self.skill,
            r"^## Wakeup Skeleton$",
            r"^## Workflow Stage Index$",
        )
        self.assertTrue(skeleton)
        self.assertIn("consensus-rnd-cli wakeup-plan", skeleton)
        self.assertIn("every wakeup must mechanically call", skeleton)
        required_order = (
            "must arm or confirm the mounted persistent Monitor bridge before pending-event sweep",
            "Run `python3 <skill-root>/scripts/consensus-rnd-cli wakeup-plan --repo-root \"$REPO_ROOT\"` first",
            "Arm or confirm the persistent daemon-event Monitor bridge",
            "Sweep GitHub comments and pending events",
            "Spawn the next codexes",
            "Confirm the daemon-event Monitor bridge is still maintained",
            "ScheduleWakeup fallback",
        )
        cursor = -1
        for token in required_order:
            index = skeleton.find(token)
            with self.subTest(token=token):
                self.assertNotEqual(index, -1)
                self.assertGreater(index, cursor)
            cursor = index
        self.assertNotRegex(
            skeleton,
            r"Confirm a wake source: an active daemon-event Monitor bridge,.*or.*ScheduleWakeup",
        )

    def test_controller_cli_invocation_contract_forbids_bash_interpreter(self) -> None:
        # Refactor (issue-303):
        #   Old pattern: controller-actionable runbooks invoked the Python CLI
        #   with `bash <skill-root>/scripts/consensus-rnd-cli peek`, so wakeup
        #   docs looked executable but selected the wrong interpreter.
        #   New principle: controller-actionable CLI examples spell the Python
        #   interpreter explicitly; shell wrappers may only source env and exec
        #   python3.
        canonical = "python3 <skill-root>/scripts/consensus-rnd-cli peek | tail -80"
        self.assertNotIn("bash <skill-root>/scripts/consensus-rnd-cli", self.skill)
        self.assertIn(canonical, self.skill)

    def test_wakeup_peek_uses_python_cli_invocation(self) -> None:
        # Refactor (issue-303): lock the controller-actionable peek callsites to
        # the Python CLI contract without rewriting conceptual command mentions.
        canonical = "python3 <skill-root>/scripts/consensus-rnd-cli peek | tail -80"
        skeleton = section_between(
            self.skill,
            r"^## Wakeup Skeleton$",
            r"^## Workflow Stage Index$",
        )
        first_action = section_between(
            self.skill,
            r"^### Wakeup 第一动作:",
            r"^<a id=\"wake-source-rules\"></a>$",
        )
        wake_source = section_between(
            self.skill,
            r"^## Wake source rules and no-gap details$",
            r"^### Controller 每 wakeup 必派",
        )
        dev_sync = section_between(
            self.skill,
            r"^### Controller 每 wakeup 责任",
            r"^### 反面",
        )
        self.assertTrue(skeleton)
        self.assertTrue(first_action)
        self.assertTrue(wake_source)
        self.assertTrue(dev_sync)
        self.assertIn(f"### Wakeup 第一动作:`{canonical}`(强制)", self.skill)
        for label, section, needle in (
            ("wakeup skeleton final peek", skeleton, f"Run `{canonical}` again after spawn, merge, banner, or close actions."),
            ("wakeup first action body", first_action, canonical),
            ("zero-codex first action", wake_source, f"**Controller wakeup 第一动作**:`{canonical}`。如果活跃 codex == 0:"),
            ("dev-sync controller responsibility", dev_sync, canonical),
        ):
            with self.subTest(label=label):
                self.assertIn(needle, section)

    def test_wakeup_plan_entrypoint_contract_is_read_only_and_authorized(self) -> None:
        skeleton = section_between(
            self.skill,
            r"^## Wakeup Skeleton$",
            r"^## Workflow Stage Index$",
        )
        checklist = section_between(
            self.skill,
            r"^## Controller Wakeup Checklist$",
            r"^## ",
        )
        combined = f"{skeleton}\n{checklist}"
        for needle in (
            "consensus-rnd-cli wakeup-plan",
            "每次唤醒",
            "Mechanically call `python3 <skill-root>/scripts/consensus-rnd-cli wakeup-plan --repo-root \"$REPO_ROOT\"`",
            f"{AUTH_MIRROR}#maintainer-directive-wakeup-plan-script",
            f"{AUTH_MIRROR}#maintainer-directive-floor-no-exemption",
            "**Allowed**",
            "**Allowed git topology observation(issue #190 only)**",
            "**Forbidden / no lifecycle authority**",
            "no restart",
            "no spawn",
            "git fetch origin --quiet",
            "worktree list --porcelain",
            "rev-list --count refs/remotes/origin/<head>..HEAD",
            "UNPUSHED_WORKER_OUTPUT:<pr>:<n>",
            "no git lifecycle or mutation commands",
            "no checkout/switch",
            "no branch create/delete/update",
            "no worktree add/remove/prune",
            "no GitHub lifecycle mutation",
            "`RECOMMEND:audit`",
            "`AUDIT_DONE:none:0` no longer exempts",
            "`AUDIT_DONE:none:0` still does not exempt",
            "deficit hard-gate",
            "no general low-floor exemption",
            "`HARD_GATE:dispatch_required=N`",
            "`WAIT:single-active-audit`",
            "`reason=single_active_audit_in_flight`",
            "`blocked_deficit=N`",
            "no duplicate same-iteration audit",
            "structured `hard_gate`",
            "not advisory",
            "`consensus-rnd-cli peek` is a status lens",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, combined)
        for forbidden in ("AuditLaneIdentity", "AUDIT_LANE_ID", "audit-iter-N-laneK"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)

    def test_wakeup_plan_script_declares_allowed_forbidden_boundary(self) -> None:
        script = read(PACKAGE_WAKEUP_PLAN)
        for needle in (
            "def unpushed_worker_output_actions",
            '["git", "-C", str(repo_root), "fetch", "origin", "--quiet"]',
            '["git", "-C", str(repo_root), "worktree", "list", "--porcelain"]',
            '["git", "-C", str(worktree), "rev-parse", "--verify", "HEAD"]',
            '["git", "-C", str(worktree), "rev-parse", "--verify", remote_ref]',
            '["git", "-C", str(worktree), "rev-list", "--count", f"{remote_ref}..HEAD"]',
            "no_lifecycle_authority",
            "count_in_flight_codex",
            "HARD_GATE:dispatch_required",
            "hard_gate",
            f"{AUTH_MIRROR}#maintainer-directive-wakeup-plan-script",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, script)
        for forbidden in ("Refactor (", "Old pattern", "New principle", '"git", "push"', '"git", "commit"', '"git", "checkout"'):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, script)
        self.assertNotIn("WorkerOutputProjection", script)
        self.assertNotIn("codex_refactor_loop.projections", script)

    def test_phase0_bootstrap_uses_session_monitor_not_first_wakeup_substitute(self) -> None:
        phase0 = section_between(
            self.skill,
            r"^## Consensus-rnd Phase bootstrap .+Bootstrap .+$",
            r"^## Phase Routing$",
        )
        self.assertTrue(phase0)
        self.assertIn("session bootstrap", phase0)
        self.assertIn("arm persistent daemon-event Monitor bridge", phase0)
        self.assertIn("confirm the daemon-event Monitor bridge is still active before ending", phase0)
        self.assertIn("not Monitor substitutes", phase0)
        self.assertNotIn("first wakeup only", phase0)
        self.assertNotIn("or ScheduleWakeup returned scheduled", phase0)

    def test_single_file_reference_architecture_is_documented(self) -> None:
        detailed_anchors = (
            "controller-contract-details",
            "host-runtime-details",
            "daemon-command-bodies",
            "work-unit-contract",
            "batching-heuristics",
            "recovery-playbook",
            "label-bootstrap-loops",
            "historical-bilingual-notes",
            "specialized-state-artifacts",
        )
        self.assertIn("## Detailed reference", self.skill)
        self.assertIn("single controller contract and detailed reference by maintainer directive", self.skill)
        self.assertIn("use intra-file anchor links", self.skill)
        self.assertIn("Controller Contract Index", self.skill)
        self.assertIn("物理拆 REFERENCE.md 后跨平台加载/维护退化", self.skill)
        for anchor in detailed_anchors:
            with self.subTest(anchor=anchor):
                self.assertIn(f"(#{anchor})", self.skill)
                self.assertIn(anchor, self.skill)
        schema_field = "schema" + "_version"
        work_unit_schema_field = "work_unit_" + schema_field
        self.assertNotIn(f'"{schema_field}": 1', self.skill)
        self.assertNotIn(f'"{work_unit_schema_field}": 1', self.skill)
        for emoji_heading in ("📊", "🆘"):
            with self.subTest(emoji_heading=emoji_heading):
                self.assertRegex(self.skill, rf"(?m)^## {emoji_heading} ")

    def test_philosophy_and_prompt_prose_do_not_leak_schema_identifier_suffixes(self) -> None:
        docs = {
            "SKILL.md": self.skill,
            "prompts/implement.md": read(SKILL_ROOT / "prompts" / "implement.md"),
            "prompts/verify.md": read(SKILL_ROOT / "prompts" / "verify.md"),
            "prompts/meta-judge.md": read(SKILL_ROOT / "prompts" / "meta-judge.md"),
        }
        schema_field = "schema" + "_version"
        work_unit_schema_field = "work_unit_" + schema_field
        forbidden_fragments = (
            "state-v2",
            "v1 audit-backed work unit",
            "v1 audit cluster alias",
            f'"{schema_field}": 1',
            f'"{work_unit_schema_field}": 1',
        )
        for path, text in docs.items():
            with self.subTest(path=path):
                for fragment in forbidden_fragments:
                    self.assertNotIn(fragment, text)
        self.assertIn("schema: refactor-verify-v1", docs["prompts/verify.md"])

    def test_skill_uses_intra_file_reference_links_only(self) -> None:
        # Refactor (iter1/issue-141):
        #   Old pattern: downstream install steps without an installer were split across README, SKILL statusline text, and restart helper text, with no one-step walkthrough.
        #   New principle: Downstream install walkthrough centralizes setup, README/SKILL links point at it, and source-regression locks single-file anchors.
        self.assertNotIn("@REFERENCE.md", self.skill)
        self.assertNotIn("REFERENCE.md#", self.skill)
        self.assertNotRegex(self.skill, r"\]\(/Users/[^)]+REFERENCE\.md")
        self.assertNotRegex(self.skill, r"\(REFERENCE\.md#[^)]+\)")
        self.assertIn("## Detailed reference", self.skill)
        self.assertIn("use intra-file anchor links", self.skill)
        self.assertEqual(2, self.skill.count("(#downstream-install-walkthrough)"))
        self.assertRegex(self.skill, r"\(#[^)]+\)")

    def test_phase9_router_daemon_boundary_is_narrow(self) -> None:
        self.assertIn("consensus-rnd-cli phase9-router", self.skill)
        self.assertIn("narrow Consensus-rnd Phase design-consensus allowlist", self.skill)
        self.assertIn("SOLVER_DONE", self.skill)
        self.assertIn("META_JUDGE_DONE:converge", self.skill)
        self.assertIn("META_JUDGE_DONE:escalate:stalled", self.skill)
        self.assertIn("router-derived stalled continuation", self.skill)
        self.assertIn("legacy read-only `META_JUDGE_DONE:escalate:stalled`", self.skill)
        self.assertIn("meta-judge returns consensus/converge only", self.skill)
        self.assertIn("do not introduce migrated work-unit schema, public marker aliases, ControllerOrchestrator, ControllerEvent, ControllerCommand, or lifecycle authority", self.skill)

    def test_issue276_lifecycle_target_normalization_contract_is_documented(self) -> None:
        section = section_between(
            self.skill,
            r"^### Controller-internal lifecycle primitives.+$",
            r"^### ",
        )
        self.assertTrue(section)
        for needle in (
            "<!-- Refactor (issue-276): Old pattern:",
            "`apply_human_label_or_skip`, `merge_pr`, `open_pr_with_label`, or `record_recent_pr_merge`",
            "`_normalize_lifecycle_target`",
            "canonical positive decimals",
            "before any `gh` or `git` side effect",
            "empty, blank, zero, negative, non-digit, leading-zero, URL, branch, and current-PR inference inputs fail closed",
            "`CONTROLLER_ACTION_BLOCKED:invalid-github-target:<action>:<kind>:<source>`",
            "`open_pr_with_label(...)` URL extraction followed by the same normalization",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)

    def test_runtime_surface_boundary_keeps_peek_human_and_wakeup_plan_machine(self) -> None:
        self.assertIn("`consensus-rnd-cli wakeup-plan` is the prioritized-next-action reader", self.skill)
        self.assertIn("`consensus-rnd-cli peek` is a status lens, not routing authority", self.skill)
        self.assertIn("structured output", self.skill)
        self.assertNotIn("peek --json", self.skill)
        self.assertNotIn("`--json`", read(SKILL_ROOT / "scripts" / "codex_refactor_loop" / "peek.py"))

    def test_executable_shell_blocks_do_not_embed_raw_positional_parameters(self) -> None:
        fenced_bash_blocks = re.findall(r"```(?:bash|sh|shell)\n(.*?)```", self.skill, flags=re.DOTALL)
        self.assertTrue(fenced_bash_blocks)
        for index, block in enumerate(fenced_bash_blocks):
            with self.subTest(block=index):
                self.assertNotRegex(block, r"(?<!\\)\$[0-9]")

    # Refactor (issue-275): Old pattern: SKILL.md executable probes could carry raw positional shell parameters and clobber skill loading. New principle: source-regression locks the removal and the canonical CLI replacement wording.
    def test_issue275_shell_clobber_patterns_are_removed_from_skill(self) -> None:
        forbidden = (
            "comm -13",
            "awk -F'\\t' '$2==\"fail\"",
            "ps -eo command= | awk",
            "index($0,r)",
            "REMOTE_CI_DONE",
        )
        for needle in forbidden:
            with self.subTest(needle=needle):
                self.assertNotIn(needle, self.skill)

        required = (
            "kind: \"ci-red\"",
            "consensus-rnd-cli pr-checks --repo \"$GH_REPO_SLUG\"",
            "concurrency --count-only",
            "--list-codex",
            "ACTIVE=$(python3 <skill-root>/scripts/consensus-rnd-cli concurrency --count-only)",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, self.skill)

    def test_issue297_controller_runbook_uses_named_projections_and_actions(self) -> None:
        # Refactor (issue-297): Old: controller-facing SKILL runbook exposed
        # copyable gh/git lifecycle recipes. New: source-regression locks named
        # read projections and active-controller-gated ControllerActions.
        forbidden = (
            "gh issue create",
            "gh pr edit <PR> --add-label",
            "gh pr list --json",
            "gh pr checks",
            "git push origin refactor",
            "git worktree add",
            "git worktree remove",
            "git worktree prune",
            "git branch -D",
        )
        allowed_history = "must not run `gh pr create`"
        skill_without_forbidden_history = self.skill.replace(allowed_history, "")
        for needle in forbidden:
            with self.subTest(needle=needle):
                self.assertNotIn(needle, skill_without_forbidden_history)
        for required in (
            "ControllerActions.open_design_issue_with_labels(title, body_file)",
            "ControllerActions.safe_worktree(iteration, cluster, base)",
            "ControllerActions.safe_push(remote, branch)",
            "ControllerActions.open_pr_with_label(title, body_file, base, head)",
            "consensus-rnd-cli pr-checks",
            "open head/base PR projection",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.skill)

    def test_root_state_json_contract_deleted_but_specialized_artifacts_remain(self) -> None:
        forbidden = (
            "Write initial state.json",
            "authoritative queue containers",
            "state.json.trunk_head",
            "clusters_planned",
            "clusters_active",
            "clusters_done",
            "clusters_failed",
            "design_pending",
            "remote_ci",
            "State schema",
            "state schema",
            "state-schema",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, self.skill)

        required = (
            "Root `.refactor-loop/state.json` is not a contract surface",
            "do not create or maintain root `.refactor-loop/state.json`",
            ".refactor-loop/state/statusline-snapshot.json",
            ".refactor-loop/state/phase8-review-state.json",
            ".refactor-loop/state/recent-pr-merges.json",
            ".refactor-loop/codex-progress-state.json",
            ".refactor-loop/comment-monitor-state.json",
            ".refactor-loop/.concurrency-monitor-state.json",
            "specialized-state-artifacts",
        )
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, self.skill)

    def test_host_commands_are_shell_strings_executed_via_bash_lc(self) -> None:
        docs = {
            "SKILL.md": self.skill,
            "host.env.example": read(HOST_ENV_EXAMPLE),
            "prompts/implement.md": read(SKILL_ROOT / "prompts" / "implement.md"),
            "prompts/review-fix.md": read(SKILL_ROOT / "prompts" / "review-fix.md"),
            "prompts/test-add.md": read(SKILL_ROOT / "prompts" / "test-add.md"),
            "sync/dev.py": read(SKILL_ROOT / "scripts" / "codex_refactor_loop" / "sync" / "dev.py"),
        }
        for path, text in docs.items():
            with self.subTest(path=path):
                if path in {"SKILL.md", "host.env.example", "prompts/implement.md"}:
                    self.assertIn("shell command string", text)
                if "BUILD_CMD" in text:
                    self.assertIn('bash -lc "$BUILD_CMD"', text)
                if "TEST_CMD" in text:
                    self.assertIn('bash -lc "$TEST_CMD"', text)

        bare_host_command_lines: list[str] = []
        bare_host_command_re = re.compile(r"^\s*\$(BUILD_CMD|TEST_CMD)\s*$")
        for path, text in docs.items():
            for lineno, line in enumerate(text.splitlines(), start=1):
                if bare_host_command_re.match(line):
                    bare_host_command_lines.append(f"{path}:{lineno}:{line.strip()}")

        self.assertEqual(
            bare_host_command_lines,
            [],
            "Host command strings must be executed via bash -lc, not as bare lines",
        )

    def test_host_env_surface_matrix_entrypoint_contract(self) -> None:
        host_config = section_between(
            self.skill,
            r"^## Host .+$",
            r"^## Skill Root Contract$",
        )
        self.assertIn("### Host env surface matrix", host_config)
        self.assertIn("`host.env.example` is a copyable template view", host_config)
        self.assertIn("the only manually maintained host.env contract", host_config)
        self.assertIn(
            "| Variable | Category | Owner | Default/example | Missing/empty behavior | Consumer | Test owner |",
            host_config,
        )
        self.assertIn("`host.env` is the only loop runtime fact injection point", host_config)
        self.assertNotIn("| Variable | Meaning | Default / example |", host_config)
        self.assertNotIn("| Variable | Prompt meaning | Empty behavior |", host_config)

    def test_refactor_loop_is_skill_private_not_host_production_ssot(self) -> None:
        host_config = section_between(
            self.skill,
            r"^## Host .+$",
            r"^## Skill Root Contract$",
        )
        for needle in (
            "$CONSENSUS_RND_HOST_ENV",
            "locates the host-owned `host.env` loop runtime injection file",
            "not host production configuration schema",
            "no `.refactor-loop/host.env` fallback is read",
            "`.refactor-loop/` is the skill-private runtime home",
            "Host production facts, branch topology, durable ledger authority, and host-owned config SSOT must live in host-owned config/rules/artifacts, not in `.refactor-loop/`.",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, host_config)

    def test_audit_and_solver_prompts_forbid_host_production_facts_in_refactor_loop(self) -> None:
        prompt_names = (
            "audit.md",
            "solver-minimal.md",
            "solver-structural.md",
            "solver-delete.md",
            "meta-judge.md",
            "implement.md",
            "reviewer-architect.md",
        )
        for name in prompt_names:
            text = read(SKILL_ROOT / "prompts" / name)
            with self.subTest(prompt=name):
                self.assertIn(".refactor-loop/`", text)
                self.assertRegex(text, r"skill-private runtime(?:/cache/log)?")
                self.assertIn("host-owned config", text)
                self.assertIn("durable ledger authority", text)

    def test_issue205_dogfood_anti_rules_are_local_contract(self) -> None:
        # Refactor (iter205/issue-205):
        #   Old pattern: dogfood 实测的运维经验(audit 并行撞 iteration 号、新 role prompt 漏注册 marker contract、review verdict grep log tail 误判、daemon 恢复手 kill)只靠 agent 记忆,没落进 skill 合同与机械验证。
        #   New principle: 把四条经验写回局部合同:SKILL.md 增 #205 反面规则段(audit 同一时刻单 active iteration、新 role prompt 必同步 marker inventory、review verdict 权威源优先 review artifact frontmatter、daemon 恢复只走 restart-daemons);audit.md 渲染后 ITERATION 空则 fail-closed;peek.py 局部优先读 review artifact frontmatter verdict;配套 source-regression + behavior test。不新增跨模块抽象层。
        section = section_between(
            self.skill,
            r"^## Dogfood anti-rules\(per #205\)$",
            r"^## Wakeup Skeleton$",
        )
        self.assertTrue(section)
        for needle in (
            "only one active `audit-iter-N`",
            "fails closed when `ITERATION` is empty",
            "do not write `audit-iter-.md`",
            "must be registered in `test_marker_emission_contract.py` prompt inventory",
            "PROMPT_ALLOWLISTS",
            "PROMPT_ARTIFACT_PROFILES",
            "review-pr<N>-<role>-r<R>.md` frontmatter `verdict: approve|comment|reject`",
            "fall back to clean log-tail `REVIEW_DONE` markers",
            "consensus-rnd-cli restart-daemons",
            "must not hand-kill daemon processes",
            "probe process lists as liveness authority",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)
        forbidden = ("WorkerCompletionEvidence", "AuditRunLease", "restart-daemons --force")
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, self.skill)

    def test_spawn_contract_isolates_background_spawns_from_fallible_calls(self) -> None:
        # Old pattern: a spawn-codex background task batched with a fallible probe
        # was silently cancelled when the probe exited non-zero (harness cancels
        # every sibling in a parallel batch on one non-zero exit), leaving the
        # floor unfilled with no real dispatch.
        # New principle: each spawn-codex goes in its own tool-call message,
        # isolated from fallible reads.
        section = section_between(
            self.skill,
            r"^## Spawn Contract$",
            r"^## ",
        )
        self.assertTrue(section)
        for needle in (
            "same parallel tool-call batch",
            "cancels every sibling call in a parallel batch when one of them exits non-zero",
            "leaves the floor unfilled with no real dispatch",
            "Dispatch each `spawn-codex` in its own tool-call message",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)

    def test_issue205_audit_prompt_fails_closed_on_empty_iteration(self) -> None:
        # Refactor (iter205/issue-205):
        #   Old pattern: dogfood 实测的运维经验(audit 并行撞 iteration 号、新 role prompt 漏注册 marker contract、review verdict grep log tail 误判、daemon 恢复手 kill)只靠 agent 记忆,没落进 skill 合同与机械验证。
        #   New principle: 把四条经验写回局部合同:SKILL.md 增 #205 反面规则段(audit 同一时刻单 active iteration、新 role prompt 必同步 marker inventory、review verdict 权威源优先 review artifact frontmatter、daemon 恢复只走 restart-daemons);audit.md 渲染后 ITERATION 空则 fail-closed;peek.py 局部优先读 review artifact frontmatter verdict;配套 source-regression + behavior test。不新增跨模块抽象层。
        audit_prompt = read(SKILL_ROOT / "prompts" / "audit.md")
        section = section_between(
            audit_prompt,
            r"^## 渲染身份 fail-closed\(强制\)$",
            r"^## 强制流程",
        )
        self.assertTrue(section)
        for needle in (
            "`ITERATION` 为空",
            "立即输出 `AUDIT_INCOMPLETE:missing-iteration`",
            "禁止写入 `$REPO_ROOT/.refactor-loop/runs/audit-iter-.md`",
            "`$REPO_ROOT/.refactor-loop/runs/audit-iter--candidates.ndjson`",
            "audit fallback 同一时刻只能有一个 active `audit-iter-${ITERATION}`",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)

    def test_audit_prompt_does_not_treat_single_file_skill_reference_as_r02_r03(self) -> None:
        audit_prompt = read(SKILL_ROOT / "prompts" / "audit.md")
        for needle in (
            "不得被机械解释成 `REFERENCE.md` 必须存在",
            "anchor 不可达",
            "单文件 `SKILL.md` + intra-file anchors 本身不是 violation",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, audit_prompt)

    def test_audit_work_intake_iteration_placeholder_matches_audit_prompt(self) -> None:
        # Refactor (issue-307): keep work-intake rendering contract aligned with
        # prompts/audit.md while preserving runtime shell placeholders.
        section = section_between(
            self.skill,
            r"^## Consensus-rnd Phase work-intake .+$",
            r"^## ",
        )
        self.assertTrue(section)
        self.assertNotIn("{{iteration}}", section)
        for needle in (
            "Replace the literal `${ITERATION}` placeholders with N using a targeted replacement",
            "leave runtime placeholders such as `$REPO_ROOT`, `${PROJECT_RULES:-CLAUDE.md}`, and `$SOURCE_GLOBS` intact",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, section)

        audit_prompt = read(SKILL_ROOT / "prompts" / "audit.md")
        rendered = audit_prompt.replace("${ITERATION}", "307")
        self.assertIn("audit-iter-307", rendered)
        for runtime_placeholder in (
            "$REPO_ROOT",
            "${PROJECT_RULES:-CLAUDE.md}",
            "$SOURCE_GLOBS",
        ):
            with self.subTest(runtime_placeholder=runtime_placeholder):
                self.assertIn(runtime_placeholder, rendered)


if __name__ == "__main__":
    unittest.main()
