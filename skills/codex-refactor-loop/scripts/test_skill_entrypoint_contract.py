#!/usr/bin/env python3
"""Source contract tests for the codex-refactor-loop single-file skill."""

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

    def test_entrypoint_line_budget_and_controller_contract_headings(self) -> None:
        headings = set(markdown_headings(self.skill))

        for pattern in (
            r"^## Controller Contract Index$",
            r"^## Host .+$",
            r"^## Phase Index$",
            r"^## Phase 0 .+Bootstrap .+$",
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
            "Controller = pure orchestration",
            "#phase-0-details",
            "Phase 0",
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
            "🎯 milestone",
            "orthogonal third axis",
            "Before any non-milestone existing-issue work or ordinary audit fallback",
            "bootstrap failure / missing wake source, maintainer comment, completed marker same-wakeup route, CI red, and no-gap violation",
            "milestone members = GitHub `🎯 milestone` label",
            ".refactor-loop/runs/maintainer-directives/2026-05-29-milestone-priority.md",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, self.skill)

    def test_first_wakeup_bootstrap_obligations_are_ordered_in_skill_alone(self) -> None:
        phase0 = section_between(
            self.skill,
            r"^## Phase 0 .+Bootstrap .+$",
            r"^## Phase Routing$",
        )
        self.assertTrue(phase0)
        obligations = (
            "source .refactor-loop/host.env",
            "fail closed",
            "ProjectRulesFixedPointEnsurer",
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
            r"^## Phase Index$",
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

    def test_wakeup_plan_entrypoint_contract_is_read_only_and_authorized(self) -> None:
        skeleton = section_between(
            self.skill,
            r"^## Wakeup Skeleton$",
            r"^## Phase Index$",
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
            ".refactor-loop/runs/maintainer-directives/2026-05-29-wakeup-plan-script.md",
            "**Allowed**",
            "**Forbidden / no lifecycle authority**",
            "no restart",
            "no spawn",
            "no git",
            "no GitHub lifecycle mutation",
            "`RECOMMEND:audit`",
            "`AUDIT_DONE:none:0` no longer exempts",
            "deficit hard-gate",
            "controller 不得带 `deficit>0` 结束唤醒",
            "`HARD_GATE:dispatch_required=N`",
            "structured `hard_gate`",
            "not advisory",
            "`consensus-rnd-cli peek` is a status lens",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, combined)

    def test_wakeup_plan_script_declares_allowed_forbidden_boundary(self) -> None:
        script = read(PACKAGE_WAKEUP_PLAN)
        for needle in (
            "Allowed: read `.refactor-loop` files",
            "Forbidden: no restart/spawn, no git",
            "no GitHub lifecycle mutation",
            "no_lifecycle_authority",
            "count_in_flight_codex",
            "HARD_GATE:dispatch_required",
            "hard_gate",
            ".refactor-loop/runs/maintainer-directives/2026-05-29-wakeup-plan-script.md",
        ):
            with self.subTest(needle=needle):
                self.assertIn(needle, script)

    def test_phase0_bootstrap_uses_session_monitor_not_first_wakeup_substitute(self) -> None:
        phase0 = section_between(
            self.skill,
            r"^## Phase 0 .+Bootstrap .+$",
            r"^## Phase Routing$",
        )
        self.assertTrue(phase0)
        self.assertIn("session bootstrap", phase0)
        self.assertIn("arm persistent daemon-event Monitor bridge", phase0)
        self.assertIn("confirm the daemon-event Monitor bridge is still active before ending", phase0)
        self.assertIn("not Monitor substitutes", phase0)
        self.assertNotIn("first wakeup only", phase0)
        self.assertNotIn("or ScheduleWakeup returned scheduled", phase0)

    def test_detailed_reference_material_is_in_single_skill_file(self) -> None:
        detailed_anchors = (
            "work-unit-contract",
            "batching-heuristics",
            "recovery-playbook",
            "label-bootstrap-loops",
            "historical-bilingual-notes",
            "specialized-state-artifacts",
        )
        self.assertIn("## Detailed reference", self.skill)
        for anchor in detailed_anchors:
            with self.subTest(anchor=anchor):
                self.assertIn(f"(#{anchor})", self.skill)
                self.assertIn(anchor, self.skill)
        self.assertNotIn('"schema_version": 1', self.skill)
        self.assertNotIn('"work_unit_schema_version": 1', self.skill)
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
        forbidden_fragments = (
            "state-v2",
            "v1 audit-backed work unit",
            "v1 audit cluster alias",
            '"schema_version": 1',
            '"work_unit_schema_version": 1',
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
        self.assertNotRegex(self.skill, r"\]\(/Users/[^)]+REFERENCE\.md")
        self.assertNotRegex(self.skill, r"\(REFERENCE\.md#[^)]+\)")
        self.assertEqual(2, self.skill.count("(#downstream-install-walkthrough)"))
        self.assertRegex(self.skill, r"\(#[^)]+\)")

    def test_phase9_router_daemon_boundary_is_narrow(self) -> None:
        self.assertIn("consensus-rnd-cli phase9-router", self.skill)
        self.assertIn("narrow Phase 9 allowlist", self.skill)
        self.assertIn("SOLVER_DONE", self.skill)
        self.assertIn("META_JUDGE_DONE:converge", self.skill)
        self.assertIn("META_JUDGE_DONE:escalate:stalled", self.skill)
        self.assertIn("do not introduce migrated work-unit schema, public marker aliases, ControllerOrchestrator, ControllerEvent, ControllerCommand, or lifecycle authority", self.skill)

    def test_runtime_surface_boundary_keeps_peek_human_and_wakeup_plan_machine(self) -> None:
        self.assertIn("`consensus-rnd-cli wakeup-plan` is the prioritized-next-action reader", self.skill)
        self.assertIn("`consensus-rnd-cli peek` is a status lens, not routing authority", self.skill)
        self.assertIn("structured output", self.skill)
        self.assertNotIn("peek --json", self.skill)
        self.assertNotIn("`--json`", read(SKILL_ROOT / "scripts" / "codex_refactor_loop" / "peek.py"))

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


if __name__ == "__main__":
    unittest.main()
