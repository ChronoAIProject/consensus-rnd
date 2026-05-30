#!/usr/bin/env python3
"""Behavior tests for the narrow Consensus-rnd Phase design-consensus router daemon."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.ownership import OwnershipDecision, WorkTarget
from codex_refactor_loop.phase9.router import (
    Phase9Router,
    Phase9SourceIssueDecision,
    main,
    parse_phase9_log_identity,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
PHASE9_ROUTER = REPO_ROOT / "skills" / "codex-refactor-loop" / "scripts" / "codex_refactor_loop" / "phase9" / "router.py"


class Phase9RouterDaemonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / ".refactor-loop" / "logs").mkdir(parents=True)
        self.env = mock.patch.dict(
            os.environ,
            {"GH_REPO_SLUG": "", "GH_OWNER": "", "GH_REPO_NAME": "", "GH_REPO": ""},
            clear=False,
        )
        self.env.start()
        self.commands: list[list[str]] = []
        self.source_issue_states: dict[str, str] = {}
        self.original_source_issue_reader = Phase9Router._read_source_issue_decision

        def fake_source_issue_decision(issue: str) -> Phase9SourceIssueDecision:
            state = self.source_issue_states.get(str(issue), "OPEN")
            if state == "UNAVAILABLE":
                return Phase9SourceIssueDecision(False, None, "phase9-source-state-unavailable")
            normalized = state.upper()
            if normalized == "OPEN":
                return Phase9SourceIssueDecision(True, normalized, "phase9-source-open")
            return Phase9SourceIssueDecision(False, normalized, "phase9-source-not-open")

        self.router = Phase9Router(ctx=LoopContext.load(repo_root=self.repo), command_runner=self.commands.append)
        self.router._read_source_issue_decision = fake_source_issue_decision  # type: ignore[method-assign]

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def write_log(self, name: str, *lines: str, exit_zero: bool = True) -> Path:
        path = self.repo / ".refactor-loop" / "logs" / name
        tail = ["EXIT=0"] if exit_zero else ["EXIT=1"]
        path.write_text("\n".join([*lines, *tail, ""]), encoding="utf-8")
        return path

    def write_solver_prompt(self, issue: int, round_no: int, role: str, body: str = "solver input\n") -> Path:
        path = self.repo / ".refactor-loop" / "prompts" / "phase9" / f"phase9-issue{issue}-r{round_no}-{role}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def ledger_entries(self) -> list[dict]:
        path = self.repo / ".refactor-loop" / "phase9-router-ledger.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def write_ledger_key(self, key: str) -> None:
        path = self.repo / ".refactor-loop" / "phase9-router-ledger.jsonl"
        with path.open("a", encoding="utf-8") as ledger:
            ledger.write(json.dumps({"key": key}, sort_keys=True) + "\n")

    def pending_events(self) -> str:
        path = self.repo / ".refactor-loop" / ".controller-pending-events.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def pending_event_payloads(self, prefix: str = "phase9-router-fallback") -> list[dict]:
        payloads = []
        for line in self.pending_events().splitlines():
            if f" {prefix} " not in line:
                continue
            payloads.append(json.loads(line.split(f"{prefix} ", 1)[1]))
        return payloads

    def solver_triplet(self, issue: int = 37, round_no: int = 4, verdict: str = "same") -> None:
        for role in ("minimal", "structural", "delete"):
            self.write_log(
                f"phase9-issue{issue}-r{round_no}-{role}.log",
                f"SOLVER_DONE:{role}:{verdict}:summary",
            )

    def enable_github_repo(self) -> None:
        (self.repo / ".refactor-loop" / "host.env").write_text("GH_REPO_SLUG=owner/repo\n", encoding="utf-8")
        self.router = Phase9Router(ctx=LoopContext.load(repo_root=self.repo), command_runner=self.commands.append)
        self.router._read_source_issue_decision = lambda issue: Phase9SourceIssueDecision(True, "OPEN", "phase9-source-open")  # type: ignore[method-assign]

    def write_stalled_ready_logs(self, issue: int, round_no: int = 3) -> None:
        for current_round in range(round_no - 2, round_no + 1):
            self.solver_triplet(issue=issue, round_no=current_round, verdict="same")
        for current_round in range(round_no - 2, round_no):
            self.write_ledger_key(f"{issue}-{current_round}-judge")
        self.write_log(f"phase9-issue{issue}-r{round_no}-judge.log", "META_JUDGE_DONE:escalate:stalled:no-change")

    def test_phase9_router_tail_read_bounds_io_to_last_kb(self) -> None:
        # Refactor (iter5/issue122-phase9-tail-perf): direct behavior test for
        # _read_tail_lines. Build a log far larger than TAIL_READ_BYTES, write
        # the verdict marker only in the last few lines, and verify the helper
        # returns those tail lines without paging the entire body.
        path = self.repo / ".refactor-loop" / "logs" / "big.log"
        body_size = Phase9Router.TAIL_READ_BYTES * 8
        filler = "noise-line-padding-text\n" * (body_size // len("noise-line-padding-text\n"))
        tail_lines = ["MARK_PENULTIMATE", "SOLVER_DONE:minimal:approve:summary", "EXIT=0", ""]
        path.write_text(filler + "\n".join(tail_lines), encoding="utf-8")

        result = self.router._read_tail_lines(path, 4)

        self.assertEqual(result[-3:], ["MARK_PENULTIMATE", "SOLVER_DONE:minimal:approve:summary", "EXIT=0"])
        self.assertTrue(self.router._is_clean_exit(path))

    def test_phase9_router_tail_read_handles_short_file(self) -> None:
        path = self.repo / ".refactor-loop" / "logs" / "short.log"
        path.write_text("only-one-line\nEXIT=0\n", encoding="utf-8")
        result = self.router._read_tail_lines(path, 5)
        self.assertEqual(result, ["only-one-line", "EXIT=0"])

    def test_phase9_router_clean_exit_gating_requires_tail_exit_zero(self) -> None:
        self.write_log("phase9-issue37-r4-minimal.log", "SOLVER_DONE:minimal:ok:x")
        self.write_log("phase9-issue37-r4-structural.log", "SOLVER_DONE:structural:ok:x")
        self.write_log("phase9-issue37-r4-delete.log", "SOLVER_DONE:delete:ok:x", exit_zero=False)

        self.router.tick()

        self.assertEqual(self.commands, [])
        self.assertEqual(self.ledger_entries(), [])

    def test_phase9_router_unknown_marker_fallback_appends_event_only(self) -> None:
        self.write_log("phase9-issue37-r4-judge.log", "SOMETHING_DONE:surprise:payload")

        self.router.tick()

        self.assertEqual(self.commands, [])
        self.assertIn("SOMETHING_DONE:surprise:payload", self.pending_events())
        self.assertEqual(self.ledger_entries(), [])

    def test_phase9_router_idempotency_spawns_once_per_dedupe_key(self) -> None:
        self.solver_triplet()

        self.router.tick()
        self.router.tick()

        self.assertEqual(len(self.commands), 1)
        self.assertEqual([entry["key"] for entry in self.ledger_entries()], ["37-4-judge"])

    def test_phase9_router_singleton_lock_conflict_fail_closed_before_dispatch(self) -> None:
        self.solver_triplet()

        with mock.patch("codex_refactor_loop.phase9.router.fcntl.flock", side_effect=BlockingIOError):
            with self.assertRaises(SystemExit):
                with self.router.singleton():
                    self.router.tick()

        self.assertEqual(self.commands, [])
        self.assertEqual(self.ledger_entries(), [])

    def test_phase9_router_in_flight_suppresses_duplicate_dispatch_before_ledger(self) -> None:
        with self.subTest("existing target log"):
            self.solver_triplet(issue=37, round_no=4)
            self.router._log_path("37", 4, "judge").write_text("reserved by existing worker\n", encoding="utf-8")

            self.router.tick()

            self.assertEqual(self.commands, [])
            self.assertEqual(self.ledger_entries(), [])

        with self.subTest("ps command line"):
            self.tmp.cleanup()
            self.setUp()
            self.solver_triplet(issue=38, round_no=5)
            target_log = self.router._log_path("38", 5, "judge")
            ps_output = f"/bin/sh /tmp/consensus-rnd-cli spawn-codex --cd {self.repo.resolve()} --log {target_log} --stall 3600\n"

            with mock.patch("codex_refactor_loop.phase9.router.subprocess.run", return_value=mock.Mock(stdout=ps_output)):
                self.router.tick()

            self.assertEqual(self.commands, [])
            self.assertEqual(self.ledger_entries(), [])

    def test_phase9_router_placeholder_exclusion_ignores_prompt_echo(self) -> None:
        for role in ("minimal", "structural", "delete"):
            self.write_log(
                f"phase9-issue37-r4-{role}.log",
                f"prompt template says SOLVER_DONE:<{role}>:<verdict>:<summary>",
            )

        self.router.tick()

        self.assertEqual(self.commands, [])
        self.assertEqual(self.ledger_entries(), [])

    def test_phase9_router_solver_triplet_dispatches_meta_judge_once(self) -> None:
        self.solver_triplet(issue=37, round_no=4)

        self.router.tick()

        self.assertEqual(len(self.commands), 1)
        command = self.commands[0]
        self.assertIn(str(self.repo.resolve()), command)
        self.assertIn(str((self.repo / ".refactor-loop" / "logs" / "phase9-issue37-r4-judge.log").resolve()), command)
        self.assertEqual(self.ledger_entries()[0]["key"], "37-4-judge")

    def test_phase9_router_unknown_ownership_ledgers_skip_before_direct_dispatch(self) -> None:
        (self.repo / ".refactor-loop" / "host.env").write_text("GH_REPO_SLUG=owner/repo\n", encoding="utf-8")
        self.router = Phase9Router(ctx=LoopContext.load(repo_root=self.repo), command_runner=self.commands.append)
        self.solver_triplet(issue=37, round_no=4)
        unknown = OwnershipDecision(False, "unknown-current-login", WorkTarget("issue", 37))

        with mock.patch("codex_refactor_loop.phase9.router.GitHubWorkOwnership") as ownership_cls:
            ownership_cls.return_value.decide.return_value = unknown
            self.router.tick()

        self.assertEqual(self.commands, [])
        entries = self.ledger_entries()
        self.assertEqual([entry["key"] for entry in entries], ["37-4-judge-ownership-skip"])
        self.assertEqual(entries[0]["ownership"], "unknown-current-login")

    def test_phase9_router_stale_ownership_posts_notice_before_direct_dispatch(self) -> None:
        (self.repo / ".refactor-loop" / "host.env").write_text("GH_REPO_SLUG=owner/repo\n", encoding="utf-8")
        self.router = Phase9Router(ctx=LoopContext.load(repo_root=self.repo), command_runner=self.commands.append)
        self.solver_triplet(issue=38, round_no=4)
        stale = OwnershipDecision(True, "stale-takeover", WorkTarget("issue", 38), "other", "alice", 4.0)

        with mock.patch("codex_refactor_loop.phase9.router.GitHubWorkOwnership") as ownership_cls:
            ownership_cls.return_value.decide.return_value = stale
            ownership_cls.return_value.post_takeover_notice.return_value = True
            self.router._read_source_issue_decision = lambda issue: Phase9SourceIssueDecision(True, "OPEN", "phase9-source-open")  # type: ignore[method-assign]
            self.router.tick()

        self.assertEqual(len(self.commands), 1)
        ownership_cls.return_value.post_takeover_notice.assert_called_once_with(stale)
        self.assertEqual(self.ledger_entries()[0]["key"], "38-4-judge")

    def test_phase9_router_stale_ownership_notice_failure_ledgers_skip_before_dispatch(self) -> None:
        (self.repo / ".refactor-loop" / "host.env").write_text("GH_REPO_SLUG=owner/repo\n", encoding="utf-8")
        self.router = Phase9Router(ctx=LoopContext.load(repo_root=self.repo), command_runner=self.commands.append)
        self.solver_triplet(issue=38, round_no=4)
        stale = OwnershipDecision(True, "stale-takeover", WorkTarget("issue", 38), "other", "alice", 4.0)

        with mock.patch("codex_refactor_loop.phase9.router.GitHubWorkOwnership") as ownership_cls:
            ownership_cls.return_value.decide.return_value = stale
            ownership_cls.return_value.post_takeover_notice.return_value = False
            self.router.tick()

        self.assertEqual(self.commands, [])
        entries = self.ledger_entries()
        self.assertEqual([entry["key"] for entry in entries], ["38-4-judge-ownership-notice-failed"])
        self.assertEqual(entries[0]["ownership"], "stale-takeover")

    def test_phase9_router_converge_unknown_ownership_ledgers_route_specific_skips(self) -> None:
        self.enable_github_repo()
        self.write_log("phase9-issue40-r4-judge.log", "META_JUDGE_DONE:converge:round-5:need-more")
        unknown = OwnershipDecision(False, "unknown-current-login", WorkTarget("issue", 40))

        with mock.patch("codex_refactor_loop.phase9.router.GitHubWorkOwnership") as ownership_cls:
            ownership_cls.return_value.decide.return_value = unknown
            self.router.tick()

        self.assertEqual(self.commands, [])
        entries = self.ledger_entries()
        self.assertEqual(
            sorted(entry["key"] for entry in entries),
            [
                "40-5-delete-ownership-skip",
                "40-5-minimal-ownership-skip",
                "40-5-structural-ownership-skip",
            ],
        )
        self.assertEqual({entry["route"] for entry in entries}, {"5-minimal", "5-structural", "5-delete"})
        self.assertEqual({entry["ownership"] for entry in entries}, {"unknown-current-login"})

    def test_phase9_router_converge_stale_ownership_posts_notice_before_solver_dispatch(self) -> None:
        self.enable_github_repo()
        self.write_log("phase9-issue41-r4-judge.log", "META_JUDGE_DONE:converge:round-5:need-more")
        stale = OwnershipDecision(True, "stale-takeover", WorkTarget("issue", 41), "other", "alice", 4.0)

        with mock.patch("codex_refactor_loop.phase9.router.GitHubWorkOwnership") as ownership_cls:
            ownership_cls.return_value.decide.return_value = stale
            ownership_cls.return_value.post_takeover_notice.side_effect = lambda decision: self.commands.append(["post_takeover_notice"]) or True
            self.router.tick()

        ownership_cls.return_value.post_takeover_notice.assert_any_call(stale)
        notice_indexes = [index for index, command in enumerate(self.commands) if command == ["post_takeover_notice"]]
        spawn_indexes = [index for index, command in enumerate(self.commands) if "spawn-codex" in command]
        self.assertEqual(len(notice_indexes), 3)
        self.assertEqual(len(spawn_indexes), 3)
        self.assertTrue(all(notice < spawn for notice, spawn in zip(notice_indexes, spawn_indexes)))
        self.assertEqual(
            sorted(entry["key"] for entry in self.ledger_entries()),
            ["41-5-delete", "41-5-minimal", "41-5-structural"],
        )

    def test_phase9_router_converge_stale_notice_failure_blocks_solver_dispatch(self) -> None:
        self.enable_github_repo()
        self.write_log("phase9-issue42-r4-judge.log", "META_JUDGE_DONE:converge:round-5:need-more")
        stale = OwnershipDecision(True, "stale-takeover", WorkTarget("issue", 42), "other", "alice", 4.0)

        with mock.patch("codex_refactor_loop.phase9.router.GitHubWorkOwnership") as ownership_cls:
            ownership_cls.return_value.decide.return_value = stale
            ownership_cls.return_value.post_takeover_notice.return_value = False
            self.router.tick()

        self.assertEqual(self.commands, [])
        entries = self.ledger_entries()
        self.assertEqual(
            sorted(entry["key"] for entry in entries),
            [
                "42-5-delete-ownership-notice-failed",
                "42-5-minimal-ownership-notice-failed",
                "42-5-structural-ownership-notice-failed",
            ],
        )
        self.assertEqual({entry["ownership"] for entry in entries}, {"stale-takeover"})

    def test_phase9_router_stalled_unknown_ownership_ledgers_reflector_skip(self) -> None:
        self.enable_github_repo()
        self.write_stalled_ready_logs(issue=43)
        unknown = OwnershipDecision(False, "unknown-current-login", WorkTarget("issue", 43))

        with mock.patch("codex_refactor_loop.phase9.router.GitHubWorkOwnership") as ownership_cls:
            ownership_cls.return_value.decide.return_value = unknown
            self.router.tick()

        self.assertEqual(self.commands, [])
        entries = self.ledger_entries()
        self.assertIn("43-3-reflector-ownership-skip", [entry["key"] for entry in entries])
        skip = next(entry for entry in entries if entry["key"] == "43-3-reflector-ownership-skip")
        self.assertEqual(skip["route"], "3-reflector")
        self.assertEqual(skip["ownership"], "unknown-current-login")

    def test_phase9_router_stalled_stale_ownership_posts_notice_before_reflector_dispatch(self) -> None:
        self.enable_github_repo()
        self.write_stalled_ready_logs(issue=44)
        stale = OwnershipDecision(True, "stale-takeover", WorkTarget("issue", 44), "other", "alice", 4.0)

        with mock.patch("codex_refactor_loop.phase9.router.GitHubWorkOwnership") as ownership_cls:
            ownership_cls.return_value.decide.return_value = stale
            ownership_cls.return_value.post_takeover_notice.side_effect = lambda decision: self.commands.append(["post_takeover_notice"]) or True
            self.router.tick()

        ownership_cls.return_value.post_takeover_notice.assert_called_once_with(stale)
        notice_index = self.commands.index(["post_takeover_notice"])
        reflector_index = next(index for index, command in enumerate(self.commands) if "phase9-issue44-r3-reflector.log" in " ".join(command))
        self.assertLess(notice_index, reflector_index)
        self.assertIn("44-3-reflector", [entry["key"] for entry in self.ledger_entries()])

    def test_phase9_router_stalled_stale_notice_failure_blocks_reflector_dispatch(self) -> None:
        self.enable_github_repo()
        self.write_stalled_ready_logs(issue=45)
        stale = OwnershipDecision(True, "stale-takeover", WorkTarget("issue", 45), "other", "alice", 4.0)

        with mock.patch("codex_refactor_loop.phase9.router.GitHubWorkOwnership") as ownership_cls:
            ownership_cls.return_value.decide.return_value = stale
            ownership_cls.return_value.post_takeover_notice.return_value = False
            self.router.tick()

        self.assertEqual(self.commands, [])
        entries = self.ledger_entries()
        self.assertIn("45-3-reflector-ownership-notice-failed", [entry["key"] for entry in entries])
        failure = next(entry for entry in entries if entry["key"] == "45-3-reflector-ownership-notice-failed")
        self.assertEqual(failure["route"], "3-reflector")
        self.assertEqual(failure["ownership"], "stale-takeover")

    def test_phase9_router_closed_issue_suppresses_triplet_to_judge_and_appends_skip_event(self) -> None:
        self.source_issue_states["37"] = "CLOSED"
        self.solver_triplet(issue=37, round_no=4)

        self.router.tick()
        self.router.tick()

        self.assertEqual(self.commands, [])
        self.assertEqual(self.ledger_entries(), [])
        self.assertFalse((self.repo / ".refactor-loop" / "prompts" / "phase9" / "phase9-issue37-r4-judge.md").exists())
        events = self.pending_event_payloads()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["key"], "phase9-source-eligibility:37-4-solver_triplet_to_judge")
        self.assertEqual(event["reason"], "phase9-source-not-open")
        self.assertEqual(event["state"], "CLOSED")
        self.assertEqual(event["issue"], "37")
        self.assertEqual(event["round"], 4)
        self.assertEqual(event["route"], "solver_triplet_to_judge")
        self.assertEqual(event["marker"], "SOLVER_DONE:triplet")
        self.assertIn(event["log_path"], {
            ".refactor-loop/logs/phase9-issue37-r4-delete.log",
            ".refactor-loop/logs/phase9-issue37-r4-minimal.log",
            ".refactor-loop/logs/phase9-issue37-r4-structural.log",
        })

    def test_phase9_router_source_eligibility_fallback_restart_dedupe(self) -> None:
        # Refactor (fix/pr245-source-open-restart-dedupe-test): Old: source-OPEN gate tests covered in-memory fallback dedupe only. New: recreate the router after a persisted source-eligibility fallback and prove restart seeding suppresses duplicate events.
        cases = (
            ("CLOSED", "phase9-source-not-open"),
            ("UNAVAILABLE", "phase9-source-state-unavailable"),
        )
        for state, reason in cases:
            with self.subTest(reason=reason):
                self.tmp.cleanup()
                self.setUp()
                self.source_issue_states["37"] = state
                self.solver_triplet(issue=37, round_no=4)

                self.router.tick()
                fresh_router = Phase9Router(ctx=LoopContext.load(repo_root=self.repo), command_runner=self.commands.append)
                fresh_router._read_source_issue_decision = self.router._read_source_issue_decision  # type: ignore[method-assign]
                fresh_router.tick()

                key = "phase9-source-eligibility:37-4-solver_triplet_to_judge"
                events = self.pending_event_payloads()
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["key"], key)
                self.assertEqual(events[0]["reason"], reason)
                self.assertIn(key, fresh_router._fallback_seen)
                self.assertIn(key, fresh_router._load_persisted_fallback_seen())
                self.assertEqual(self.pending_events().count(key), 1)
                self.assertEqual(self.commands, [])
                self.assertEqual(self.ledger_entries(), [])

    def test_phase9_router_issue_state_unavailable_fails_closed_without_ledger(self) -> None:
        self.source_issue_states["37"] = "UNAVAILABLE"
        self.solver_triplet(issue=37, round_no=4)

        self.router.tick()

        self.assertEqual(self.commands, [])
        self.assertEqual(self.ledger_entries(), [])
        events = self.pending_event_payloads()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reason"], "phase9-source-state-unavailable")
        self.assertIsNone(events[0]["state"])

    def test_phase9_router_issue_state_reader_fails_closed_on_bad_gh_results(self) -> None:
        cases = (
            mock.Mock(returncode=1, stdout="", stderr="missing"),
            mock.Mock(returncode=0, stdout="{not-json", stderr=""),
            mock.Mock(returncode=0, stdout=json.dumps({}), stderr=""),
            mock.Mock(returncode=0, stdout=json.dumps({"state": ""}), stderr=""),
        )
        for result in cases:
            with self.subTest(stdout=result.stdout, returncode=result.returncode):
                with mock.patch("codex_refactor_loop.phase9.router.subprocess.run", return_value=result):
                    decision = self.original_source_issue_reader(self.router, "37")
                self.assertFalse(decision.allowed)
                self.assertIsNone(decision.state)
                self.assertEqual(decision.reason, "phase9-source-state-unavailable")

    def test_phase9_router_triplet_dispatch_writes_row_level_ledger_provenance(self) -> None:
        self.solver_triplet(issue=167, round_no=6)
        for role in ("minimal", "structural", "delete"):
            self.write_solver_prompt(167, 6, role)

        self.router.tick()

        self.assertEqual(len(self.commands), 1)
        entries = self.ledger_entries()
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        for key in (
            "key",
            "marker",
            "log_path",
            "dispatched_at",
            "route",
            "issue",
            "round",
            "target_actor",
            "clean_exit_solver_logs",
            "solver_input_prompts",
            "judge_input_solver_logs",
            "judge_prompt_path",
            "independence_check",
        ):
            with self.subTest(key=key):
                self.assertIn(key, entry)
        self.assertEqual(entry["key"], "167-6-judge")
        self.assertEqual(entry["marker"], "SOLVER_DONE:triplet")
        self.assertEqual(entry["log_path"], ".refactor-loop/logs/phase9-issue167-r6-judge.log")
        self.assertEqual(entry["route"], "solver_triplet_to_judge")
        self.assertEqual(entry["issue"], "167")
        self.assertEqual(entry["round"], 6)
        self.assertEqual(entry["target_actor"], "judge")
        self.assertEqual(entry["independence_check"], "pass")
        self.assertEqual(entry["judge_prompt_path"], ".refactor-loop/prompts/phase9/phase9-issue167-r6-judge.md")
        self.assertEqual(
            entry["judge_input_solver_logs"],
            [
                ".refactor-loop/logs/phase9-issue167-r6-delete.log",
                ".refactor-loop/logs/phase9-issue167-r6-minimal.log",
                ".refactor-loop/logs/phase9-issue167-r6-structural.log",
            ],
        )
        self.assertEqual([record["role"] for record in entry["clean_exit_solver_logs"]], ["delete", "minimal", "structural"])
        self.assertEqual([record["dialect"] for record in entry["clean_exit_solver_logs"]], ["phase9", "phase9", "phase9"])
        self.assertEqual([record["status"] for record in entry["solver_input_prompts"]], ["present", "present", "present"])
        self.assertNotIn("independence_checks", entry)

    def test_phase9_router_triplet_ledger_records_missing_solver_prompts_without_blocking(self) -> None:
        self.solver_triplet(issue=168, round_no=2)
        self.write_solver_prompt(168, 2, "minimal")

        self.router.tick()

        self.assertEqual(len(self.commands), 1)
        entry = self.ledger_entries()[0]
        self.assertEqual(
            entry["solver_input_prompts"],
            [
                {
                    "role": "delete",
                    "prompt_path": ".refactor-loop/prompts/phase9/phase9-issue168-r2-delete.md",
                    "status": "missing",
                },
                {
                    "role": "minimal",
                    "prompt_path": ".refactor-loop/prompts/phase9/phase9-issue168-r2-minimal.md",
                    "status": "present",
                },
                {
                    "role": "structural",
                    "prompt_path": ".refactor-loop/prompts/phase9/phase9-issue168-r2-structural.md",
                    "status": "missing",
                },
            ],
        )

    def test_phase9_router_read_ledger_ignores_provenance_fields_for_recovery(self) -> None:
        path = self.repo / ".refactor-loop" / "phase9-router-ledger.jsonl"
        path.write_text(
            "\n".join(
                [
                    json.dumps({"key": "167-6-judge", "clean_exit_solver_logs": "malformed"}),
                    json.dumps({"key": "167-7-judge", "independence_check": {"unexpected": "shape"}}),
                    json.dumps({"route": "solver_triplet_to_judge"}),
                    "{not-json",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        self.assertEqual(self.router._read_ledger(), {"167-6-judge", "167-7-judge"})
        self.solver_triplet(issue=167, round_no=6)

        self.router.tick()

        self.assertEqual(self.commands, [])
        self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 4)

    def test_phase9_router_judge_prompt_references_dispatch_ledger_evidence(self) -> None:
        self.solver_triplet(issue=169, round_no=8)

        self.router.tick()

        prompt_path = self.repo / ".refactor-loop" / "prompts" / "phase9" / "phase9-issue169-r8-judge.md"
        prompt = prompt_path.read_text(encoding="utf-8")
        for token in (
            ".refactor-loop/logs/phase9-issue169-r8-delete.log",
            ".refactor-loop/logs/phase9-issue169-r8-minimal.log",
            ".refactor-loop/logs/phase9-issue169-r8-structural.log",
            "Dispatch ledger evidence: .refactor-loop/phase9-router-ledger.jsonl key=169-8-judge",
        ):
            with self.subTest(token=token):
                self.assertIn(token, prompt)
        self.assertIn("Consensus-rnd Phase design-consensus meta-judge", prompt)
        self.assertNotRegex(prompt, re.compile(r"\bPhase\s+[0-9]\b"))
        self.assertNotIn("phase9-evidence", prompt)
        self.assertNotIn("Dispatch ledger:", prompt)
        self.assertNotIn(str(self.repo), prompt)

    def test_phase9_router_durable_artifacts_store_relative_paths_but_spawn_argv_absolute(self) -> None:
        self.solver_triplet(issue=202, round_no=1)

        self.router.tick()

        ledger = self.ledger_entries()[0]
        self.assertEqual(".refactor-loop/logs/phase9-issue202-r1-judge.log", ledger["log_path"])
        self.assertNotIn(str(self.repo), json.dumps(ledger, ensure_ascii=False))
        command = self.commands[0]
        self.assertIn(str((self.repo / ".refactor-loop" / "prompts" / "phase9" / "phase9-issue202-r1-judge.md").resolve()), command)
        self.assertIn(str((self.repo / ".refactor-loop" / "logs" / "phase9-issue202-r1-judge.log").resolve()), command)

        self.write_log("phase9-issue202-r2-judge.log", "META_RESOLVED:re-design:scope")
        self.router.tick()

        events = self.pending_events()
        self.assertIn('"log_path": ".refactor-loop/logs/phase9-issue202-r2-judge.log"', events)
        self.assertNotIn(str(self.repo), events)

    def test_phase9_router_fallback_restart_dedupe_reads_legacy_absolute_and_writes_relative(self) -> None:
        legacy_log = self.repo / ".refactor-loop" / "logs" / "phase9-issue203-r1-judge.log"
        pending = self.repo / ".refactor-loop" / ".controller-pending-events.log"
        pending.write_text(
            "2026-05-29T00:00:00Z phase9-router-fallback "
            + json.dumps({"log_path": str(legacy_log), "marker": "META_RESOLVED:old"}, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        self.write_log("phase9-issue203-r1-judge.log", "META_RESOLVED:old")

        fresh_router = Phase9Router(self.repo, command_runner=self.commands.append)
        fresh_router.tick()

        self.assertEqual(self.pending_events().count("META_RESOLVED:old"), 1)

    def test_phase9_router_peer_solver_prompt_reference_fails_closed(self) -> None:
        self.solver_triplet(issue=170, round_no=3)
        self.write_solver_prompt(
            170,
            3,
            "minimal",
            "I already read .refactor-loop/logs/phase9-issue170-r3-delete.log\n",
        )

        self.router.tick()
        self.router.tick()

        self.assertEqual(self.commands, [])
        self.assertEqual(self.ledger_entries(), [])
        self.assertFalse((self.repo / ".refactor-loop" / "prompts" / "phase9" / "phase9-issue170-r3-judge.md").exists())
        events = self.pending_events()
        self.assertEqual(events.count("phase9-triplet-evidence-invalid"), 3)
        event = json.loads(events.split("phase9-triplet-evidence-invalid ", 1)[1].splitlines()[0])
        self.assertEqual(event["reason"], "phase9-triplet-evidence-invalid")
        self.assertEqual(event["issue"], "170")
        self.assertEqual(event["round"], 3)
        self.assertEqual(event["role"], "minimal")
        self.assertEqual(event["peer_role"], "delete")
        self.assertEqual(event["prompt_path"], ".refactor-loop/prompts/phase9/phase9-issue170-r3-minimal.md")
        self.assertEqual(event["matched_token"], ".refactor-loop/logs/phase9-issue170-r3-delete.log")

    def test_phase9_router_triplet_evidence_requires_exact_clean_exit_triplet(self) -> None:
        self.write_log("phase9-issue171-r1-minimal.log", "SOLVER_DONE:minimal:ok:x")
        self.write_log("phase9-issue171-r1-structural.log", "SOLVER_DONE:structural:ok:x")
        self.router.tick()
        self.assertEqual(self.commands, [])
        self.assertEqual(self.ledger_entries(), [])

        self.write_log("phase9-issue172-r1-minimal.log", "SOLVER_DONE:minimal:ok:x")
        self.write_log("phase9-issue172-r1-structural.log", "SOLVER_DONE:structural:ok:x")
        self.write_log("phase9-issue172-r1-delete.log", "SOLVER_DONE:delete:ok:x", exit_zero=False)
        self.router.tick()
        self.assertEqual(self.commands, [])
        self.assertEqual(self.ledger_entries(), [])

    def test_phase9_router_solver_triplet_accepts_non_ascii_summary(self) -> None:
        # Refactor (iter1/issue-149):
        #   Old pattern: phase9_router marker parser 不能可靠识别含中文收敛问题/route 后缀的 judge marker → 漏派 triplet judge 与 converge round,controller 被迫 fallback 全部 dispatch(本会话持续 no-gap churn 根因)。
        #   New principle: 按 .refactor-loop/runs/phase9-issue149-r2-judge.md consensus(structural):route-specific marker-grammar parser fix,正确解析所有 route marker(含中文 body),不引入 Phase9RoundProjection 抽象。使 router 对所有 glob 可见的 3/3 SOLVER_DONE triplet 与 converge 可靠 dispatch。硬约束:不重建 REFERENCE.md;refactor 注释自含 Old/New;不超范围。
        for role in ("minimal", "structural", "delete"):
            self.write_log(
                f"phase9-issue149-r1-{role}.log",
                f"SOLVER_DONE:{role}:propose:中文摘要-继续收敛",
            )

        self.router.tick()

        self.assertEqual(len(self.commands), 1)
        self.assertIn("phase9-issue149-r1-judge.log", " ".join(self.commands[0]))
        self.assertEqual([entry["key"] for entry in self.ledger_entries()], ["149-1-judge"])

    def test_phase9_router_controller_dispatched_triplet_across_ticks(self) -> None:
        for role in ("minimal", "structural"):
            self.write_log(
                f"phase9-issue149-r2-{role}.log",
                f"SOLVER_DONE:{role}:propose:中文摘要-等待第三路",
            )

        self.router.tick()
        self.assertEqual(self.commands, [])
        self.assertEqual(self.ledger_entries(), [])

        self.write_log(
            "phase9-issue149-r2-delete.log",
            "SOLVER_DONE:delete:propose:中文摘要-第三路完成",
        )
        self.router.tick()
        self.router.tick()

        self.assertEqual(len(self.commands), 1)
        self.assertIn("phase9-issue149-r2-judge.log", " ".join(self.commands[0]))
        self.assertEqual([entry["key"] for entry in self.ledger_entries()], ["149-2-judge"])

    def test_phase9_router_accepts_solver_issue_logs_for_triplet(self) -> None:
        for role in ("minimal", "structural", "delete"):
            self.write_log(
                f"solver-issue100-r4-{role}.log",
                f"SOLVER_DONE:{role}:propose:summary",
            )

        self.router.tick()

        self.assertEqual(len(self.commands), 1)
        joined = " ".join(self.commands[0])
        self.assertIn("phase9-issue100-r4-judge.log", joined)
        self.assertNotIn("solver-issue100-r4-judge.log", joined)
        self.assertEqual([entry["key"] for entry in self.ledger_entries()], ["100-4-judge"])

    def test_phase9_router_suppresses_judge_dispatch_when_legacy_meta_judge_log_exists(self) -> None:
        """Legacy meta-judge log presence suppresses same issue/round judge dispatch."""
        for role in ("minimal", "structural", "delete"):
            self.write_log(
                f"solver-issue100-r4-{role}.log",
                f"SOLVER_DONE:{role}:propose:summary",
            )
        self.write_log(
            "meta-judge-issue100-r4.log",
            "META_JUDGE_DONE:converge:round-5:already-dispatched-by-legacy-worker",
        )

        self.router.tick()

        joined_commands = "\n".join(" ".join(command) for command in self.commands)
        self.assertNotIn("phase9-issue100-r4-judge.log", joined_commands)
        self.assertNotIn("100-4-judge", [entry["key"] for entry in self.ledger_entries()])

    def test_phase9_log_identity_rejects_unowned_filename_dialects(self) -> None:
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

        rejected = (
            "solver-issue100-r3-judge.log",
            "meta-judge-issue100-r3-minimal.log",
            "issue100-r3-minimal.log",
            "phase9-issue100-r3-architect.log",
            "phase9_issue100_r3_minimal.log",
        )
        for name in rejected:
            with self.subTest(name=name):
                self.assertIsNone(parse_phase9_log_identity(name))

    def test_phase9_router_converge_dispatches_next_round_solvers(self) -> None:
        self.write_log("phase9-issue37-r4-judge.log", "META_JUDGE_DONE:converge:round-5:need-more")

        self.router.tick()

        self.assertEqual(len(self.commands), 3)
        logs = " ".join(" ".join(command) for command in self.commands)
        self.assertIn("phase9-issue37-r5-minimal.log", logs)
        self.assertIn("phase9-issue37-r5-structural.log", logs)
        self.assertIn("phase9-issue37-r5-delete.log", logs)
        self.assertEqual(
            sorted(entry["key"] for entry in self.ledger_entries()),
            ["37-5-delete", "37-5-minimal", "37-5-structural"],
        )

    def test_phase9_router_closed_issue_suppresses_converge_without_lifecycle_mutation(self) -> None:
        self.source_issue_states["37"] = "CLOSED"
        self.write_log("phase9-issue37-r4-judge.log", "META_JUDGE_DONE:converge:round-5:need-more")

        self.router.tick()
        self.router.tick()

        self.assertEqual(self.commands, [])
        self.assertEqual(self.ledger_entries(), [])
        events = self.pending_event_payloads()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["key"], "phase9-source-eligibility:37-5-converge_to_next_solvers")
        self.assertEqual(events[0]["reason"], "phase9-source-not-open")
        self.assertEqual(events[0]["route"], "converge_to_next_solvers")
        self.assertEqual(events[0]["marker"], "META_JUDGE_DONE:converge:round-5:need-more")

    def test_solver_prompt_for_issue_driven_converge_has_source_header(self) -> None:
        self.write_log("phase9-issue114-r1-judge.log", "META_JUDGE_DONE:converge:round-2:need-more")

        self.router.tick()

        prompt = (
            self.repo
            / ".refactor-loop"
            / "prompts"
            / "phase9"
            / "phase9-issue114-r2-structural.md"
        ).read_text(encoding="utf-8")
        required = (
            "WORK_UNIT_ID=issue-114",
            "CLUSTER_ID=issue-114 (compatibility alias only; not an audit cluster_id)",
            "WORK_UNIT_KIND=manual-work-unit",
            "WORK_UNIT_PRODUCER=manual-issue (prompt-only provenance)",
            "WORK_UNIT_SOURCE_REF=gh-issue-114",
            "SOLVER_OUTPUT_PATH=.refactor-loop/runs/phase9-issue114-r2-structural.md",
            "gh issue view 114",
            "issue body/comments are the scope spec when no local audit artifact is provided",
            "do not fabricate audit artifacts",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, prompt)
        self.assertNotIn("$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}.md", prompt)
        self.assertNotIn("cluster spec", prompt)

    def test_phase9_router_converge_accepts_non_ascii_reason(self) -> None:
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

    def test_phase9_router_accepts_meta_judge_issue_log_for_converge(self) -> None:
        self.write_log("meta-judge-issue100-r2.log", "META_JUDGE_DONE:converge:round-3:need-more")

        self.router.tick()

        self.assertEqual(len(self.commands), 3)
        logs = " ".join(" ".join(command) for command in self.commands)
        self.assertIn("phase9-issue100-r3-minimal.log", logs)
        self.assertIn("phase9-issue100-r3-structural.log", logs)
        self.assertIn("phase9-issue100-r3-delete.log", logs)
        self.assertEqual(
            sorted(entry["key"] for entry in self.ledger_entries()),
            ["100-3-delete", "100-3-minimal", "100-3-structural"],
        )

    def test_phase9_router_marker_tail_only_ignores_body_echo(self) -> None:
        # Refactor (iter5/skill-marker-tail-only-scope):
        # codex worker logs that echo prompt-body / grep-output / test-fixture
        # marker text in the body (not tail) must NOT trigger dispatch.
        # Negative path: body echo targets round-9 (distinct from tail's
        # round-3). On the old broken implementation this would dispatch
        # 90-9-* solvers; on the fixed implementation only 90-3-* appear.
        path = self.repo / ".refactor-loop" / "logs" / "phase9-issue90-r2-judge.log"
        body_pad = "\n".join(f"discussion line {i}" for i in range(60))
        body_echo = (
            "skills/codex-refactor-loop/scripts/test_consensus-rnd-cli phase9-router:171: "
            '"META_JUDGE_DONE:converge:round-9:body-echo-from-test-fixture",'
        )
        actual_tail = "META_JUDGE_DONE:converge:round-3:real-tail-verdict"
        path.write_text(
            "\n".join([body_pad, body_echo, body_pad, actual_tail, "EXIT=0", ""]),
            encoding="utf-8",
        )

        self.router.tick()

        ledger_keys = [entry["key"] for entry in self.ledger_entries()]
        for forbidden_key in ("90-9-minimal", "90-9-structural", "90-9-delete"):
            self.assertNotIn(forbidden_key, ledger_keys,
                             "body-position round-9 echo must not dispatch round-9 solvers")
        for command in self.commands:
            joined = " ".join(command)
            self.assertNotIn("phase9-issue90-r9-", joined,
                             "no command may target round-9 (body echo)")
            self.assertNotIn("body-echo-from-test-fixture", joined)
        for key in ("90-3-minimal", "90-3-structural", "90-3-delete"):
            self.assertIn(key, ledger_keys,
                          "real tail verdict (round-3) must still spawn next round")

    def test_phase9_router_stalled_predicate_uses_tail_only(self) -> None:
        # Refactor (iter5/skill-marker-tail-only-scope):
        # _stalled_predicate_holds must also scope to log tail so body-only
        # SOLVER_DONE echoes cannot satisfy the stalled predicate.
        body_pad_lines = [f"discussion line {i}" for i in range(60)]
        for round_no in (1, 2, 3):
            for role in ("minimal", "structural", "delete"):
                body_echo = (
                    "skills/codex-refactor-loop/scripts/grep_output_quote.txt: "
                    f"SOLVER_DONE:{role}:same:body-echo-not-real-verdict"
                )
                self.write_log(
                    f"phase9-issue91-r{round_no}-{role}.log",
                    *body_pad_lines,
                    body_echo,
                    *body_pad_lines,
                    "non-verdict tail line",
                )
        self.write_ledger_key("91-1-judge")
        self.write_ledger_key("91-2-judge")
        self.write_log(
            "phase9-issue91-r3-judge.log",
            "META_JUDGE_DONE:escalate:stalled:no-change",
        )

        self.router.tick()

        reflector_commands = [
            command for command in self.commands if "phase9-issue91-r3-reflector.log" in " ".join(command)
        ]
        self.assertEqual(reflector_commands, [],
                         "body-only SOLVER_DONE echoes must not satisfy stalled predicate")
        ledger_keys = [entry["key"] for entry in self.ledger_entries()]
        self.assertNotIn("91-3-reflector", ledger_keys)

    def test_phase9_router_converge_ignores_non_judge_source_logs(self) -> None:
        # Refactor (iter5/skill-converge-source-and-monotonic-guard):
        # solver logs echoing a converge marker (e.g. from prompt body or codex
        # brainstorming) must not authorize daemon to dispatch next-round
        # solvers. Only judge-role logs may carry an authoritative converge
        # verdict.
        for role in ("minimal", "structural", "delete"):
            self.write_log(
                f"phase9-issue79-r2-{role}.log",
                f"SOLVER_DONE:{role}:propose:settle",
                "META_JUDGE_DONE:converge:round-3:echoed-from-prompt-body",
            )

        self.router.tick()

        for command in self.commands:
            joined = " ".join(command)
            self.assertNotIn("phase9-issue79-r3-", joined,
                             "solver-log converge marker must not spawn next round")
        ledger_keys = [entry["key"] for entry in self.ledger_entries()]
        for forbidden in ("79-3-minimal", "79-3-structural", "79-3-delete"):
            self.assertNotIn(forbidden, ledger_keys)

    def test_phase9_router_converge_requires_strictly_greater_target_round(self) -> None:
        # Refactor (iter5/skill-converge-source-and-monotonic-guard):
        # judge emitting converge:round-N where N <= source round (e.g. r2 judge
        # emitting converge:round-2 self-reference, or a smaller round number)
        # must be treated as noop — otherwise daemon enters spawn loops over
        # past rounds.
        self.write_log(
            "phase9-issue79-r2-judge.log",
            "META_JUDGE_DONE:converge:round-2:self-reference-bug",
        )
        self.write_log(
            "phase9-issue79-r5-judge.log",
            "META_JUDGE_DONE:converge:round-4:backward-reference-bug",
        )

        self.router.tick()

        for command in self.commands:
            joined = " ".join(command)
            self.assertNotIn("phase9-issue79-r2-minimal.log", joined)
            self.assertNotIn("phase9-issue79-r2-structural.log", joined)
            self.assertNotIn("phase9-issue79-r2-delete.log", joined)
            self.assertNotIn("phase9-issue79-r4-minimal.log", joined)
        ledger_keys = [entry["key"] for entry in self.ledger_entries()]
        for forbidden in ("79-2-minimal", "79-2-structural", "79-2-delete",
                          "79-4-minimal", "79-4-structural", "79-4-delete"):
            self.assertNotIn(forbidden, ledger_keys)

    def test_phase9_router_converge_valid_judge_marker_still_spawns_next_round(self) -> None:
        # Confirm the source/monotonic guards do not break the happy path:
        # r4 judge emitting converge:round-5 must still spawn r5 solver triplet.
        self.write_log(
            "phase9-issue80-r4-judge.log",
            "META_JUDGE_DONE:converge:round-5:legitimate-next-round",
        )

        self.router.tick()

        logs = " ".join(" ".join(command) for command in self.commands)
        self.assertIn("phase9-issue80-r5-minimal.log", logs)
        self.assertIn("phase9-issue80-r5-structural.log", logs)
        self.assertIn("phase9-issue80-r5-delete.log", logs)

    def test_phase9_router_stalled_ignores_non_judge_source_logs(self) -> None:
        # Solver log emitting an escalate:stalled marker (echo/brainstorm)
        # must not spawn reflector.
        self.write_log(
            "phase9-issue81-r3-minimal.log",
            "SOLVER_DONE:minimal:ok:summary",
            "META_JUDGE_DONE:escalate:stalled:echoed-from-prompt",
        )

        self.router.tick()

        self.assertFalse(any("reflector" in " ".join(command) for command in self.commands))
        self.assertNotIn("81-3-reflector", [entry["key"] for entry in self.ledger_entries()])

    def test_phase9_router_stalled_requires_valid_predicate(self) -> None:
        self.write_log("phase9-issue37-r2-judge.log", "META_JUDGE_DONE:escalate:stalled:no-change")
        self.router.tick()
        self.assertEqual(self.commands, [])

        self.commands.clear()
        for round_no in (1, 2, 3):
            self.solver_triplet(issue=38, round_no=round_no, verdict="same")
        self.write_ledger_key("38-1-judge")
        self.write_ledger_key("38-2-judge")
        self.write_log("phase9-issue38-r3-judge.log", "META_JUDGE_DONE:escalate:stalled:no-change")
        self.router.tick()

        reflector_commands = [
            command for command in self.commands if "phase9-issue38-r3-reflector.log" in " ".join(command)
        ]
        self.assertEqual(len(reflector_commands), 1)
        self.assertEqual(len(self.commands), 1)
        self.assertIn("38-3-reflector", [entry["key"] for entry in self.ledger_entries()])

    def test_phase9_router_closed_issue_suppresses_stalled_reflector_dispatch(self) -> None:
        self.source_issue_states["38"] = "CLOSED"
        for round_no in (1, 2, 3):
            self.solver_triplet(issue=38, round_no=round_no, verdict="same")
        self.write_ledger_key("38-1-judge")
        self.write_ledger_key("38-2-judge")
        self.write_log("phase9-issue38-r3-judge.log", "META_JUDGE_DONE:escalate:stalled:no-change")

        self.router.tick()

        self.assertEqual(self.commands, [])
        self.assertNotIn("38-3-reflector", [entry["key"] for entry in self.ledger_entries()])
        events = self.pending_event_payloads()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["key"], "phase9-source-eligibility:38-3-stalled_to_reflector")
        self.assertEqual(events[0]["reason"], "phase9-source-not-open")
        self.assertEqual(events[0]["route"], "stalled_to_reflector")

    def test_phase9_router_accepts_meta_judge_issue_log_for_stalled(self) -> None:
        for round_no in (1, 2, 3):
            for role in ("minimal", "structural", "delete"):
                self.write_log(
                    f"solver-issue100-r{round_no}-{role}.log",
                    f"SOLVER_DONE:{role}:unchanged:summary",
                )
        self.write_ledger_key("100-1-judge")
        self.write_ledger_key("100-2-judge")
        self.write_log("meta-judge-issue100-r3.log", "META_JUDGE_DONE:escalate:stalled:no-change")

        self.router.tick()

        reflector_commands = [
            command for command in self.commands if "phase9-issue100-r3-reflector.log" in " ".join(command)
        ]
        self.assertEqual(len(reflector_commands), 1)
        self.assertEqual(len(self.commands), 1)
        self.assertIn("100-3-reflector", [entry["key"] for entry in self.ledger_entries()])

    def test_stalled_reflector_prompt_uses_full_template_and_evidence(self) -> None:
        for round_no in (1, 2, 3):
            self.solver_triplet(issue=85, round_no=round_no, verdict="same")
        self.write_ledger_key("85-1-judge")
        self.write_ledger_key("85-2-judge")
        stalled_marker = "META_JUDGE_DONE:escalate:stalled:no-actionable-framing"
        self.write_log("phase9-issue85-r3-judge.log", stalled_marker)

        self.router.tick()

        prompt_path = self.repo / ".refactor-loop" / "prompts" / "phase9" / "phase9-issue85-r3-reflector.md"
        prompt = prompt_path.read_text(encoding="utf-8")
        for token in (
            "# Role: Meta-reflector - stalled route resolver",
            "## Priority 0: mandatory no-framing drop",
            "META_RESOLVED:drop:no-actionable-framing-after-N-rounds",
            "Do not route to re-design unless you can cite",
            "## Marker emission allowlist",
            "⟦AI:AUTO-LOOP⟧",
            stalled_marker,
        ):
            with self.subTest(token=token):
                self.assertIn(token, prompt)

        for round_no in (1, 2, 3):
            for role in ("minimal", "structural", "delete"):
                expected = f".refactor-loop/logs/phase9-issue85-r{round_no}-{role}.log"
                with self.subTest(expected=expected):
                    self.assertIn(expected, prompt)
        self.assertNotIn(str(self.repo), prompt)

        self.assertNotEqual(
            prompt.strip(),
            (
                "# Consensus-rnd Phase design-consensus stalled reflector\n\nIssue: #85\nRound: 3\n"
                f"Stalled marker: {stalled_marker}\n\n"
                "Reflect on the convergence failure and emit META_RESOLVED."
            ),
        )
        self.assertIn("Consensus-rnd Phase design-consensus stalled reflector", prompt)
        self.assertNotRegex(prompt, re.compile(r"\bPhase\s+[0-9]\b"))

    def test_stalled_reflector_prompt_fails_closed_on_template_oserror(self) -> None:
        for round_no in (1, 2, 3):
            self.solver_triplet(issue=86, round_no=round_no, verdict="same")
        self.write_ledger_key("86-1-judge")
        self.write_ledger_key("86-2-judge")
        stalled_marker = "META_JUDGE_DONE:escalate:stalled:no-actionable-framing"
        self.write_log("phase9-issue86-r3-judge.log", stalled_marker)

        original_read_text = Path.read_text

        def read_text_or_fail_template(path: Path, *args: object, **kwargs: object) -> str:
            if path.name == "meta-reflector-stalled.md":
                raise OSError("template unavailable ⟦AI:AUTO-LOOP⟧")
            return original_read_text(path, *args, **kwargs)

        with mock.patch("codex_refactor_loop.phase9.router.Path.read_text", autospec=True, side_effect=read_text_or_fail_template):
            self.router.tick()

        prompt_path = self.repo / ".refactor-loop" / "prompts" / "phase9" / "phase9-issue86-r3-reflector.md"
        prompt = prompt_path.read_text(encoding="utf-8")
        for token in (
            "FATAL: missing stalled reflector template",
            "Do not infer a fallback route",
            "META_RESOLVED:escalate-human:missing-stalled-reflector-template",
            "template unavailable",
            stalled_marker,
            ".refactor-loop/logs/phase9-issue86-r3-minimal.log",
            "⟦AI:AUTO-LOOP⟧",
        ):
            with self.subTest(token=token):
                self.assertIn(token, prompt)
        self.assertNotIn(str(self.repo), prompt)

    def test_phase9_router_stalled_rejects_changed_recent_verdict_text(self) -> None:
        for round_no, verdict in ((1, "same"), (2, "changed"), (3, "same")):
            self.solver_triplet(issue=39, round_no=round_no, verdict=verdict)
        self.write_ledger_key("39-1-judge")
        self.write_ledger_key("39-2-judge")
        self.write_log("phase9-issue39-r3-judge.log", "META_JUDGE_DONE:escalate:stalled:no-change")

        self.router.tick()

        self.assertFalse(any("reflector" in " ".join(command) for command in self.commands))
        self.assertNotIn("39-3-reflector", [entry["key"] for entry in self.ledger_entries()])
        self.assertIn("META_JUDGE_DONE:escalate:stalled:no-change", self.pending_events())

    def test_phase9_router_lifecycle_markers_never_spawn(self) -> None:
        markers = (
            "META_JUDGE_DONE:consensus:structural:summary",
            "IMPLEMENT_DONE:cluster:ok",
            "VERIFY_DONE:cluster:ok",
            "REVIEW_DONE:pr:quality:approve",
            "FIX_DONE:pr:ok",
            "FIX_BLOCKED:pr:reason",
            "TEST_ADD_DONE:pr:ok",
            "META_RESOLVED:retry-fix:reason",
        )
        for index, marker in enumerate(markers, start=1):
            self.write_log(f"phase9-issue{index}-r1-judge.log", marker)

        self.router.tick()

        self.assertEqual(self.commands, [])
        events = self.pending_events()
        for marker in markers:
            self.assertIn(marker, events)

    def test_phase9_router_persists_fallback_dedup_across_restart(self) -> None:
        """Restart must not re-emit fallback events already in pending-events log."""
        self.write_log("phase9-issue42-r1-judge.log", "META_RESOLVED:re-design:scope-too-broad")

        self.router.tick()
        first_events = self.pending_events()
        self.assertEqual(first_events.count("META_RESOLVED:re-design:scope-too-broad"), 1)

        fresh_router = Phase9Router(self.repo, command_runner=self.commands.append)
        fresh_router.tick()

        second_events = self.pending_events()
        self.assertEqual(
            second_events.count("META_RESOLVED:re-design:scope-too-broad"),
            1,
            "fallback event must not re-emit after daemon restart",
        )

    def test_phase9_router_rejects_junk_markers_with_regex_special_chars(self) -> None:
        """Markers containing pipe/quote/backslash/template chars are prompt/regex echoes, not real markers."""
        self.write_log("phase9-issue41-r1-judge.log", "META_JUDGE_DONE:converge:round-2:中文收敛问题-合法")
        junk_lines = [
            'grep "META_JUDGE_DONE:converge:r+1" log',
            'pattern META_JUDGE_DONE:converge:round-2:With|round-3|Choose|minimal\\""',
            "echo META_RESOLVED:re-design`:`",
            'grep -E "META_JUDGE_DONE:consensus:false-positive-already-landed:no-op;" log',
            "marker META_JUDGE_DONE:escalate:stalled:* placeholder",
            'pattern META_RESOLVED:escalate-human|CANONICAL_HUMAN_LABELS\\""',
            "regex IMPLEMENT_DONE|VERIFY_DONE|SOLVER_DONE)",
        ]
        self.write_log("phase9-issue42-r1-judge.log", *junk_lines)

        self.router.tick()

        events = self.pending_events()
        self.assertEqual(len(self.commands), 3)
        self.assertTrue(all("phase9-issue41-r2-" in " ".join(command) for command in self.commands))
        for forbidden_token in ("r+1", "round-3|Choose", "no-op;", "stalled:*", "CANONICAL_HUMAN_LABELS"):
            with self.subTest(forbidden_token=forbidden_token):
                self.assertNotIn(
                    forbidden_token,
                    events,
                    f"junk marker fragment must not leak into fallback events: {forbidden_token}",
                )

    def test_phase9_router_source_does_not_introduce_forbidden_abstractions(self) -> None:
        """#37 consensus exception allows only private ledger plus fallback event."""
        src = PHASE9_ROUTER.read_text(encoding="utf-8")
        for forbidden in (
            "WorkUnitReplacement",
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
                    f"consensus-rnd-cli phase9-router must not introduce forbidden boundary token: {forbidden}",
                )

    def test_phase9_router_source_issue_gate_is_state_only_read_without_lifecycle_authority(self) -> None:
        src = PHASE9_ROUTER.read_text(encoding="utf-8")
        for required in (
            "gh",
            "issue",
            "view",
            "--json",
            "state",
            "OPEN",
            "phase9-source-not-open",
            "phase9-source-state-unavailable",
            "phase9-router-fallback",
        ):
            with self.subTest(required=required):
                self.assertIn(required, src)
        self.assertNotIn('"state,labels"', src)
        self.assertNotIn('"labels"', src)
        for forbidden in (
            "gh issue close",
            "gh issue edit",
            "gh pr merge",
            "gh pr create",
            "gh release",
            "label set",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, src)

    def test_phase9_router_marker_grammar_is_route_specific_not_ascii_payload_gate(self) -> None:
        src = PHASE9_ROUTER.read_text(encoding="utf-8")

        self.assertIn("class Phase9MarkerGrammar", src)
        self.assertIn("def parse_marker_candidate", src)
        self.assertIn("def parse_converge_round", src)
        self.assertIn("def is_stalled_marker", src)
        self.assertNotIn("class Phase9RoundProjection", src)
        self.assertNotIn("Phase9RoundProjection(", src)
        self.assertNotIn("VALID_MARKER_PAYLOAD.match(candidate)", src)

    def test_phase9_router_source_regression_uses_loop_context_artifact_path_boundary(self) -> None:
        src = PHASE9_ROUTER.read_text(encoding="utf-8")
        self.assertIn("self.ctx.durable_artifact_path(path)", src)
        self.assertIn("self.ctx.artifact_execution_path(text)", src)
        for forbidden in (
            '"log_path": self._artifact_path(log_path) if extra else str(log_path)',
            '"log_path": str(marker.log_path)',
            "f\"- {m.role}: {m.log_path}\"",
            "str(path) for path in self._solver_history_log_paths",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, src)

    def test_main_once_dispatches_via_temp_repo_root(self) -> None:
        self.solver_triplet(issue=37, round_no=4)
        commands: list[list[str]] = []

        with mock.patch.object(
            Phase9Router,
            "_read_source_issue_decision",
            return_value=Phase9SourceIssueDecision(True, "OPEN", "phase9-source-open"),
        ):
            exit_code = main(["--once", "--repo-root", str(self.repo)], command_runner=commands.append)

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(commands), 1)
        self.assertIn(str(self.repo.resolve()), commands[0])
        self.assertEqual([entry["key"] for entry in self.ledger_entries()], ["37-4-judge"])

    def test_main_rejects_relative_repo_root(self) -> None:
        commands: list[list[str]] = []

        with self.assertRaisesRegex(SystemExit, "must be absolute"):
            main(["--once", "--repo-root", "relative/path"], command_runner=commands.append)

        self.assertEqual(commands, [])

    def test_main_dry_run_records_no_dispatch(self) -> None:
        self.solver_triplet(issue=37, round_no=4)
        commands: list[list[str]] = []

        exit_code = main(["--once", "--repo-root", str(self.repo), "--dry-run"], command_runner=commands.append)

        self.assertEqual(exit_code, 0)
        self.assertEqual(commands, [])
        self.assertEqual(self.ledger_entries(), [])


if __name__ == "__main__":
    unittest.main()
