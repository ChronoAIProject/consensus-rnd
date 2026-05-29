#!/usr/bin/env python3
"""Behavior tests for the packaged Phase 9 router module."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.phase9.router import Phase9Router, main, parse_phase9_log_identity


REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROUTER = REPO_ROOT / "skills" / "codex-refactor-loop" / "scripts" / "codex_refactor_loop" / "phase9" / "router.py"


class Phase9RouterPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / ".refactor-loop" / "logs").mkdir(parents=True)
        self.commands: list[list[str]] = []
        self.ctx = LoopContext.load(repo_root=self.repo)
        self.router = Phase9Router(ctx=self.ctx, command_runner=self.commands.append)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_log(self, name: str, *lines: str, exit_zero: bool = True) -> Path:
        path = self.repo / ".refactor-loop" / "logs" / name
        tail = ["EXIT=0"] if exit_zero else ["EXIT=1"]
        path.write_text("\n".join([*lines, *tail, ""]), encoding="utf-8")
        return path

    def ledger_entries(self) -> list[dict]:
        path = self.repo / ".refactor-loop" / "phase9-router-ledger.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def pending_events(self) -> str:
        path = self.repo / ".refactor-loop" / ".controller-pending-events.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def test_package_router_uses_loop_context_paths_and_legacy_spawn_script(self) -> None:
        self.assertEqual(self.router.loop_dir, self.ctx.paths.refactor_loop)
        self.assertEqual(self.router.logs_dir, self.ctx.paths.logs)
        self.assertEqual(self.router.prompts_dir, self.ctx.paths.prompts / "phase9")
        self.assertEqual(self.router.pending_events_path, self.ctx.paths.pending_events)
        self.assertEqual(self.router.spawn_codex, self.ctx.skill_root / "scripts" / "spawn-codex.sh")

    def test_package_router_solver_triplet_dispatches_meta_judge_once(self) -> None:
        for role in ("minimal", "structural", "delete"):
            self.write_log(f"phase9-issue160-r3-{role}.log", f"SOLVER_DONE:{role}:same:summary")

        self.router.tick()
        self.router.tick()

        self.assertEqual(len(self.commands), 1)
        joined = " ".join(self.commands[0])
        self.assertIn("phase9-issue160-r3-judge.log", joined)
        self.assertIn(str(self.ctx.skill_root / "scripts" / "spawn-codex.sh"), joined)
        self.assertEqual([entry["key"] for entry in self.ledger_entries()], ["160-3-judge"])

    def test_package_router_converge_accepts_chinese_body_and_dispatches_solver_triplet(self) -> None:
        self.write_log(
            "phase9-issue149-r2-judge.log",
            "META_JUDGE_DONE:converge:round-3:中文收敛问题-继续三路判断",
        )

        self.router.tick()

        self.assertEqual(len(self.commands), 3)
        logs = " ".join(" ".join(command) for command in self.commands)
        self.assertIn("phase9-issue149-r3-minimal.log", logs)
        self.assertIn("phase9-issue149-r3-structural.log", logs)
        self.assertIn("phase9-issue149-r3-delete.log", logs)
        self.assertEqual(
            sorted(entry["key"] for entry in self.ledger_entries()),
            ["149-3-delete", "149-3-minimal", "149-3-structural"],
        )

    def test_package_router_unknown_marker_appends_existing_format_fallback_event_only_once(self) -> None:
        self.write_log("phase9-issue160-r1-judge.log", "SOMETHING_DONE:surprise:payload")

        self.router.tick()
        fresh_router = Phase9Router(ctx=self.ctx, command_runner=self.commands.append)
        fresh_router.tick()

        self.assertEqual(self.commands, [])
        events = self.pending_events()
        self.assertEqual(events.count("phase9-router-fallback"), 1)
        self.assertIn("SOMETHING_DONE:surprise:payload", events)
        event_json = events.split("phase9-router-fallback ", 1)[1].strip()
        event = json.loads(event_json)
        self.assertEqual(event["key"], "fallback:160-1")
        self.assertEqual(event["marker"], "SOMETHING_DONE:surprise:payload")
        self.assertEqual(self.ledger_entries(), [])

    def test_package_router_stalled_dispatches_reflector_when_predicate_holds(self) -> None:
        for round_no in (1, 2, 3):
            for role in ("minimal", "structural", "delete"):
                self.write_log(f"phase9-issue160-r{round_no}-{role}.log", f"SOLVER_DONE:{role}:same:summary")
        self.write_log("phase9-issue160-r3-judge.log", "META_JUDGE_DONE:escalate:stalled:no-change")

        self.router.tick()

        reflector_commands = [
            command for command in self.commands if "phase9-issue160-r3-reflector.log" in " ".join(command)
        ]
        self.assertEqual(len(reflector_commands), 1)
        self.assertIn("160-3-reflector", [entry["key"] for entry in self.ledger_entries()])

    def test_package_router_singleton_lock_conflict_fails_before_dispatch(self) -> None:
        for role in ("minimal", "structural", "delete"):
            self.write_log(f"phase9-issue160-r4-{role}.log", f"SOLVER_DONE:{role}:same:summary")

        with mock.patch("codex_refactor_loop.phase9.router.fcntl.flock", side_effect=BlockingIOError):
            with self.assertRaises(SystemExit):
                with self.router.singleton():
                    self.router.tick()

        self.assertEqual(self.commands, [])
        self.assertEqual(self.ledger_entries(), [])

    def test_package_router_parse_phase9_log_identity_parity(self) -> None:
        accepted = {
            "phase9-issue100-r3-minimal.log": ("100", 3, "minimal", "phase9"),
            "phase9-issue100-r3-judge.log": ("100", 3, "judge", "phase9"),
            "solver-issue100-r3-delete.log": ("100", 3, "delete", "solver"),
            "meta-judge-issue100-r3.log": ("100", 3, "judge", "meta-judge"),
        }
        for name, expected in accepted.items():
            with self.subTest(name=name):
                identity = parse_phase9_log_identity(name)
                self.assertIsNotNone(identity)
                assert identity is not None
                self.assertEqual((identity.issue, identity.round, identity.actor, identity.dialect), expected)

        for name in (
            "solver-issue100-r3-judge.log",
            "meta-judge-issue100-r3-minimal.log",
            "issue100-r3-minimal.log",
            "phase9-issue100-r3-architect.log",
            "phase9_issue100_r3_minimal.log",
        ):
            with self.subTest(name=name):
                self.assertIsNone(parse_phase9_log_identity(name))

    def test_package_router_source_preserves_narrow_allowlist_and_forbidden_tokens(self) -> None:
        src = PACKAGE_ROUTER.read_text(encoding="utf-8")
        for required in (
            "SOLVER_DONE",
            "META_JUDGE_DONE:converge",
            "META_JUDGE_DONE:escalate:stalled",
            "phase9-router-ledger.jsonl",
            ".controller-pending-events.log",
            "phase9-router-fallback",
            "phase9-router.lock",
            "phase9_router_daemon",
            "meta-reflector-stalled.md",
        ):
            with self.subTest(required=required):
                self.assertIn(required, src)

        for forbidden in (
            "WorkUnitV2",
            "ControllerOrchestrator",
            "ControllerEvent",
            "ControllerCommand",
            "gh pr create",
            "gh pr merge",
            "git commit",
            "git push",
            "merge_pr(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(
                    forbidden,
                    src,
                    f"codex_refactor_loop.phase9.router must not introduce forbidden boundary token: {forbidden}",
                )

    def test_package_main_once_dispatches_via_absolute_repo_root(self) -> None:
        for role in ("minimal", "structural", "delete"):
            self.write_log(f"phase9-issue160-r5-{role}.log", f"SOLVER_DONE:{role}:same:summary")
        commands: list[list[str]] = []

        exit_code = main(["--once", "--repo-root", str(self.repo)], command_runner=commands.append)

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(commands), 1)
        self.assertEqual([entry["key"] for entry in self.ledger_entries()], ["160-5-judge"])

    def test_package_main_rejects_relative_repo_root(self) -> None:
        with self.assertRaisesRegex(SystemExit, "must be absolute"):
            main(["--once", "--repo-root", "relative/path"], command_runner=self.commands.append)

        self.assertEqual(self.commands, [])


if __name__ == "__main__":
    unittest.main()
