#!/usr/bin/env python3
"""Validate intra-file reference links in SKILL.md."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__)
SKILL_ROOT = SCRIPT_PATH.parents[1]
SKILL_MD = SKILL_ROOT / "SKILL.md"
REFERENCE_MD = SKILL_ROOT / "REFERENCE.md"


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


class SkillReferenceAnchorTests(unittest.TestCase):
    # Refactor (iter1/issue-139):
    #   Old pattern: Wake-source 契约措辞自相矛盾:SKILL.md/REFERENCE.md 多处写三选一(Monitor / task-notification / ScheduleWakeup 任一即可),与 checklist step15 / ownership 的必维持 Monitor 冲突,新会话据此漏挂 Monitor bridge。
    #   New principle: 统一语义:每个 controller 会话必须 arm/confirm persistent daemon-event Monitor bridge;task-notification / ScheduleWakeup 仅作 turn 级 completion/fallback,非 Monitor 替代。删除所有三选一/or-ScheduleWakeup 弱化措辞,替换 test_skill_entrypoint_contract.py 与 test_skill_reference_anchors.py 两个 source-regression 入口,不引入 SessionWakeSourceContract 等新命名,不新增 helper/schema/daemon,不改 CLAUDE.md/Tier/lifecycle。严格按 .refactor-loop/runs/phase9-issue139-r2-judge.md 的 Implement plan 逐条改。
    def setUp(self) -> None:
        self.skill = read(SKILL_MD)

    def test_reference_file_was_merged_into_skill(self) -> None:
        self.assertFalse(REFERENCE_MD.exists())
        self.assertIn("## Detailed reference", self.skill)

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
            "state-schema",
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

    def test_skill_is_the_single_heavy_manual_after_merge(self) -> None:
        skill_lines = len(self.skill.splitlines())

        self.assertGreaterEqual(skill_lines, 3000)
        self.assertIn("Detailed specifications, heavy templates, schemas, command bodies, and recovery playbooks", self.skill)

    def test_no_absolute_reference_links_in_entrypoint(self) -> None:
        self.assertNotRegex(self.skill, r"/Users/[^)\s]+")
        self.assertNotRegex(self.skill, r"REFERENCE\.md#/[^\s)]+")

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
        self.assertIn("phase9_router_daemon.py --daemon --repo-root", self.skill)
        self.assertIn("Allowlist(唯一 direct spawn authority)", self.skill)
        self.assertIn("phase9-issue<N>-r<R>-<minimal|structural|delete|judge|reflector>.log", self.skill)
        self.assertIn("solver-issue<N>-r<R>-<minimal|structural|delete>.log", self.skill)
        self.assertIn("meta-judge-issue<N>-r<R>.log", self.skill)
        self.assertIn("issue/round 来自 filename identity", self.skill)
        self.assertIn("public marker payload remains role-local", self.skill)
        self.assertIn("daemon-owned output logs remain `phase9-issue...`", self.skill)
        self.assertIn("clean `^EXIT=0`", self.skill)
        self.assertIn(".refactor-loop/phase9-router-ledger.jsonl", self.skill)
        self.assertIn(".controller-pending-events.log", self.skill)
        self.assertIn("no lifecycle authority", self.skill)
        self.assertIn("must not introduce ControllerEvent, ControllerCommand, ControllerOrchestrator", self.skill)

    def test_phase9_router_filename_identity_source_regression(self) -> None:
        router = (SKILL_ROOT / "scripts" / "phase9_router_daemon.py").read_text(encoding="utf-8")
        helper = (SKILL_ROOT / "scripts" / "restart-daemons.sh").read_text(encoding="utf-8")
        combined = "\n".join((self.skill, router, helper))
        for token in ("phase9-issue", "solver-issue", "meta-judge-issue"):
            with self.subTest(token=token):
                self.assertIn(token, router)
                self.assertIn(token, self.skill)
        self.assertIn("parse_phase9_log_identity", router)
        self.assertIn("Refactor (issue-100/router-filename-identity)", router)
        self.assertIn("SOLVER_DONE:<role>:", combined)
        self.assertNotIn("SOLVER_DONE:<issue>:<round>:", combined)
        self.assertIn("phase9_router_daemon.py", helper)
        self.assertIn('--daemon --repo-root \\"\\$REPO_ROOT\\"', helper)


class AutoLoopStatuslineContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill = read(SKILL_MD)

    def test_skill_contains_statusline_consensus_section(self) -> None:
        self.assertRegex(
            self.skill,
            r"(?m)^## Claude Code statusline\(per #51 consensus\)$",
            "statusline consensus section must be an independent markdown heading",
        )
        required = (
            "**Producer**",
            "**Consumer**",
            "**Install**",
            "**无新 daemon**",
            "**手动一行,无 installer script**",
            "Named runtime exception",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, self.skill)

    def test_statusline_contract_does_not_add_daemon_or_installer(self) -> None:
        scripts_dir = SKILL_ROOT / "scripts"
        self.assertFalse((scripts_dir / "installer.sh").exists())
        self.assertFalse((scripts_dir / "install-statusline.sh").exists())
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
