#!/usr/bin/env python3
"""Behavior tests for the narrow Phase 9 router daemon."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from phase9_router_daemon import Phase9Router, main, parse_phase9_log_identity


REPO_ROOT = Path(__file__).resolve().parents[3]
PHASE9_ROUTER = REPO_ROOT / "skills" / "codex-refactor-loop" / "scripts" / "phase9_router_daemon.py"


class Phase9RouterDaemonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / ".refactor-loop" / "logs").mkdir(parents=True)
        self.commands: list[list[str]] = []
        self.router = Phase9Router(self.repo, command_runner=self.commands.append)

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

    def write_ledger_key(self, key: str) -> None:
        path = self.repo / ".refactor-loop" / "phase9-router-ledger.jsonl"
        with path.open("a", encoding="utf-8") as ledger:
            ledger.write(json.dumps({"key": key}, sort_keys=True) + "\n")

    def pending_events(self) -> str:
        path = self.repo / ".refactor-loop" / ".controller-pending-events.log"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def solver_triplet(self, issue: int = 37, round_no: int = 4, verdict: str = "same") -> None:
        for role in ("minimal", "structural", "delete"):
            self.write_log(
                f"phase9-issue{issue}-r{round_no}-{role}.log",
                f"SOLVER_DONE:{role}:{verdict}:summary",
            )

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

        with mock.patch("phase9_router_daemon.fcntl.flock", side_effect=BlockingIOError):
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
            ps_output = f"/bin/sh /tmp/spawn-codex.sh --cd {self.repo.resolve()} --log {target_log} --stall 3600\n"

            with mock.patch("phase9_router_daemon.subprocess.run", return_value=mock.Mock(stdout=ps_output)):
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
            "skills/codex-refactor-loop/scripts/test_phase9_router_daemon.py:171: "
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
                expected = str(self.repo / ".refactor-loop" / "logs" / f"phase9-issue85-r{round_no}-{role}.log")
                with self.subTest(expected=expected):
                    self.assertIn(expected, prompt)

        self.assertNotEqual(
            prompt.strip(),
            (
                "# Phase 9 stalled reflector\n\nIssue: #85\nRound: 3\n"
                f"Stalled marker: {stalled_marker}\n\n"
                "Reflect on the convergence failure and emit META_RESOLVED."
            ),
        )

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

        with mock.patch("phase9_router_daemon.Path.read_text", autospec=True, side_effect=read_text_or_fail_template):
            self.router.tick()

        prompt_path = self.repo / ".refactor-loop" / "prompts" / "phase9" / "phase9-issue86-r3-reflector.md"
        prompt = prompt_path.read_text(encoding="utf-8")
        for token in (
            "FATAL: missing stalled reflector template",
            "Do not infer a fallback route",
            "META_RESOLVED:escalate-human:missing-stalled-reflector-template",
            "template unavailable",
            stalled_marker,
            str(self.repo / ".refactor-loop" / "logs" / "phase9-issue86-r3-minimal.log"),
            "⟦AI:AUTO-LOOP⟧",
        ):
            with self.subTest(token=token):
                self.assertIn(token, prompt)

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
        self.assertEqual(self.commands, [])
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
                    f"phase9_router_daemon.py must not introduce forbidden boundary token: {forbidden}",
                )

    def test_main_once_dispatches_via_temp_repo_root(self) -> None:
        self.solver_triplet(issue=37, round_no=4)
        commands: list[list[str]] = []

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
