#!/usr/bin/env python3
"""Behavior tests for the packaged Consensus-rnd Phase design-consensus router module."""

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
from codex_refactor_loop.managed_work_snapshot import ManagedWorkSnapshotItem, ManagedWorkSnapshotResult
from codex_refactor_loop.monitors.concurrency import ConcurrencyMonitor
from codex_refactor_loop.phase9.router import (
    Phase9Router,
    Phase9SourceIssueDecision,
    main,
    parse_phase9_log_identity,
)
from codex_refactor_loop.prompt_contracts import GITHUB_POST_RULES_CONTRACT_TOKEN


REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROUTER = REPO_ROOT / "skills" / "codex-refactor-loop" / "scripts" / "codex_refactor_loop" / "phase9" / "router.py"


def managed_snapshot(rows: list[dict[str, object]]) -> ManagedWorkSnapshotResult:
    items = []
    for row in rows:
        labels = [
            label.get("name", "")
            for label in row.get("labels", [])  # type: ignore[union-attr]
            if isinstance(label, dict) and label.get("name")
        ]
        items.append(
            ManagedWorkSnapshotItem(
                kind="issue",
                number=int(row.get("number", 0)),
                title=str(row.get("title", "")),
                labels=tuple(labels),
            )
        )
    return ManagedWorkSnapshotResult(tuple(items), True, "cache:fresh")


class Phase9RouterPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / ".refactor-loop" / "logs").mkdir(parents=True)
        self.old_env = os.environ.copy()
        os.environ.pop("CONSENSUS_RND_HOST_ENV", None)
        self.commands: list[dict[str, object]] = []
        self.ctx = LoopContext.load(repo_root=self.repo, env={})
        self.router = Phase9Router(ctx=self.ctx, command_runner=self.commands.append)
        self.router._read_source_issue_decision = self.open_source_issue_decision  # type: ignore[method-assign]
        self.router._open_design_consensus_issues = lambda: []  # type: ignore[method-assign]

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)
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

    def open_source_issue_decision(self, issue: str) -> Phase9SourceIssueDecision:
        return Phase9SourceIssueDecision(True, "OPEN", "phase9-source-open")

    def intent_text(self, intent: dict[str, object]) -> str:
        return json.dumps(intent, ensure_ascii=False, sort_keys=True)

    def write_host_policy(self, *, invalid: bool = False) -> LoopContext:
        (self.repo / "prompts").mkdir(exist_ok=True)
        (self.repo / "prompts" / "host-solver.md").write_text("solver\n", encoding="utf-8")
        (self.repo / "prompts" / "host-judge.md").write_text("judge\n", encoding="utf-8")
        data = {
            "prompt_bindings": {
                "host:solver": "prompts/host-solver.md",
                "host:judge": "prompts/host-judge.md",
            },
            "roles": [
                {"name": "host:a", "prompt_binding": "host:solver"},
                {"name": "host:b", "prompt_binding": "host:solver"},
                {"name": "host:c", "prompt_binding": "host:solver"},
                {"name": "host:judge", "prompt_binding": "host:judge"},
            ],
            "consensus_policies": [
                {
                    "name": "host:policy",
                    "solver_roles": ["host:a", "host:b", "host:c"],
                    "judge_role": "host:judge",
                    "peer_output_isolation": not invalid,
                    "marker_families": ["SOLVER_DONE", "META_JUDGE_DONE", "META_RESOLVED"],
                }
            ],
            "dispatch": {
                "direct_spawn": {
                    "solver_roles": ["host:a", "host:b", "host:c"],
                    "judge_role": "host:judge",
                }
            },
        }
        if invalid:
            data["consensus_policies"][0]["peer_output_isolation"] = False
            data["roles"][0]["command"] = "gh pr merge 224"
        (self.repo / "workflow.json").write_text(json.dumps(data), encoding="utf-8")
        return LoopContext.load(repo_root=self.repo, env={"REPO_ROOT": str(self.repo), "HOST_WORKFLOW_SPEC": "workflow.json"})

    def test_package_router_uses_loop_context_paths(self) -> None:
        self.assertEqual(self.router.loop_dir, self.ctx.paths.refactor_loop)
        self.assertEqual(self.router.logs_dir, self.ctx.paths.logs)
        self.assertEqual(self.router.prompts_dir, self.ctx.paths.prompts / "phase9")
        self.assertEqual(self.router.pending_events_path, self.ctx.paths.pending_events)
        self.assertFalse(hasattr(self.router, "spawn_codex"))

    def test_package_router_solver_triplet_dispatches_meta_judge_once(self) -> None:
        for role in ("minimal", "structural", "delete"):
            self.write_log(f"phase9-issue160-r3-{role}.log", f"SOLVER_DONE:{role}:same:summary")

        self.router.tick()
        self.router.tick()

        self.assertEqual(len(self.commands), 1)
        joined = self.intent_text(self.commands[0])
        self.assertIn("phase9-issue160-r3-judge.log", joined)
        self.assertEqual(self.commands[0]["command"], "spawn-codex")
        self.assertEqual([entry["key"] for entry in self.ledger_entries()], ["160-3-judge"])

    def test_package_router_design_issue_intake_dispatches_r1_triplet(self) -> None:
        rows = [
            {
                "number": 410,
                "title": "package intake",
                "labels": [
                    {"name": "crnd:lifecycle:managed"},
                    {"name": "crnd:phase:design-solving"},
                    {"name": "crnd:human:auto"},
                ],
            }
        ]
        self.router = Phase9Router(
            ctx=LoopContext.load(repo_root=self.repo, env={"GH_REPO_SLUG": "owner/repo"}),
            command_runner=self.commands.append,
        )
        self.router._read_source_issue_decision = self.open_source_issue_decision  # type: ignore[method-assign]
        self.router._open_design_consensus_issues = self.router.__class__._open_design_consensus_issues.__get__(self.router)  # type: ignore[method-assign]

        with mock.patch("codex_refactor_loop.phase9.router.load_open_managed_work_snapshot", return_value=managed_snapshot(rows)):
            self.router.tick()

        self.assertEqual(len(self.commands), 3)
        for command in self.commands:
            self.assertEqual(command["cd"], str(self.repo.resolve()))
            self.assertNotEqual(command["cd"], ".")
            self.assertTrue(Path(str(command["cd"])).is_absolute())
        self.assertEqual(
            sorted(command["log"] for command in self.commands),
            [
                ".refactor-loop/logs/phase9-issue410-r1-delete.log",
                ".refactor-loop/logs/phase9-issue410-r1-minimal.log",
                ".refactor-loop/logs/phase9-issue410-r1-structural.log",
            ],
        )
        self.assertEqual(
            sorted(entry["key"] for entry in self.ledger_entries()),
            ["410-1-delete", "410-1-minimal", "410-1-structural"],
        )

    def test_design_issue_intake_absolute_cd_matches_concurrency_floor_scope(self) -> None:
        rows = [
            {
                "number": 430,
                "title": "intake floor scope",
                "labels": [
                    {"name": "crnd:lifecycle:managed"},
                    {"name": "crnd:phase:design-solving"},
                    {"name": "crnd:human:auto"},
                ],
            }
        ]
        self.router = Phase9Router(
            ctx=LoopContext.load(repo_root=self.repo, env={"GH_REPO_SLUG": "owner/repo"}),
            command_runner=self.commands.append,
        )
        self.router._read_source_issue_decision = self.open_source_issue_decision  # type: ignore[method-assign]
        self.router._open_design_consensus_issues = self.router.__class__._open_design_consensus_issues.__get__(self.router)  # type: ignore[method-assign]

        with mock.patch("codex_refactor_loop.phase9.router.load_open_managed_work_snapshot", return_value=managed_snapshot(rows)):
            self.router.tick()

        self.assertEqual(len(self.commands), 3)
        command = self.commands[0]
        self.assertEqual(command["cd"], str(self.repo.resolve()))
        self.assertNotEqual(command["cd"], ".")
        fake_ps = (
            f"python3 consensus-rnd-cli spawn-codex --cd {command['cd']} "
            f"--prompt {self.repo.resolve()}/{command['prompt']} "
            f"--log {self.repo.resolve()}/{command['log']}\n"
        )
        monitor = ConcurrencyMonitor(LoopContext.load(repo_root=self.repo, env={}))
        with mock.patch.object(monitor, "run", return_value=mock.Mock(stdout=fake_ps, returncode=0)):
            self.assertEqual(monitor.count_in_flight_codex(), 1)

    # Refactor (impl/issue191-single-active-controller): Old pattern: every
    # device-local phase9 router could write prompts, ledgers, and fallback
    # events. New principle: non-owner routers are read-only/noop.
    def test_non_owner_router_writes_no_prompt_ledger_spawn_or_fallback(self) -> None:
        for role in ("minimal", "structural", "delete"):
            self.write_log(f"phase9-issue191-r2-{role}.log", f"SOLVER_DONE:{role}:same:summary")
        decision = mock.Mock(allowed=False, owner_device="device-a", status="not-owner", action="phase9-router", lease_id="", expires_at="")

        with mock.patch("codex_refactor_loop.phase9.router.require_active_controller", return_value=decision):
            self.router.tick()

        self.assertEqual(self.commands, [])
        self.assertEqual(self.ledger_entries(), [])
        self.assertFalse((self.repo / ".refactor-loop" / "prompts" / "phase9").exists())
        self.assertEqual(self.pending_events(), "")

    def test_package_router_converge_accepts_chinese_body_and_dispatches_solver_triplet(self) -> None:
        self.write_log(
            "phase9-issue149-r2-judge.log",
            "META_JUDGE_DONE:converge:round-3:中文收敛问题-继续三路判断",
        )

        self.router.tick()

        self.assertEqual(len(self.commands), 3)
        logs = " ".join(self.intent_text(command) for command in self.commands)
        self.assertIn("phase9-issue149-r3-minimal.log", logs)
        self.assertIn("phase9-issue149-r3-structural.log", logs)
        self.assertIn("phase9-issue149-r3-delete.log", logs)
        self.assertEqual(
            sorted(entry["key"] for entry in self.ledger_entries()),
            ["149-3-delete", "149-3-minimal", "149-3-structural"],
        )
        prompt = (self.repo / ".refactor-loop" / "prompts" / "phase9" / "phase9-issue149-r3-minimal.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Consensus-rnd Phase design-consensus minimal solver", prompt)
        router_header = prompt.split("## Issue source snapshot", 1)[0]
        self.assertNotRegex(router_header, re.compile(r"\bPhase\s+[0-9]\b"))

    def test_package_router_converge_current_round_dispatches_adjacent_next_round(self) -> None:
        self.write_log(
            "phase9-issue244-r6-judge.log",
            "META_JUDGE_DONE:converge:round-6:canonical-source-round",
        )

        self.router.tick()

        self.assertEqual(len(self.commands), 3)
        logs = " ".join(self.intent_text(command) for command in self.commands)
        self.assertIn("phase9-issue244-r7-minimal.log", logs)
        self.assertIn("phase9-issue244-r7-structural.log", logs)
        self.assertIn("phase9-issue244-r7-delete.log", logs)
        self.assertEqual(
            sorted(entry["key"] for entry in self.ledger_entries()),
            ["244-7-delete", "244-7-minimal", "244-7-structural"],
        )
        self.assertEqual(self.pending_events(), "")

    def test_converge_solver_prompt_declares_issue_source_ref(self) -> None:
        self.write_log("phase9-issue114-r1-judge.log", "META_JUDGE_DONE:converge:round-2:need-more")

        self.router.tick()

        prompt = (
            self.repo
            / ".refactor-loop"
            / "prompts"
            / "phase9"
            / "phase9-issue114-r2-minimal.md"
        ).read_text(encoding="utf-8")
        required = (
            "WORK_UNIT_ID=issue-114",
            "WORK_UNIT_PRODUCER=manual-issue (prompt-only provenance)",
            "WORK_UNIT_SOURCE_REF=gh-issue-114",
            "SOLVER_OUTPUT_PATH=.refactor-loop/runs/phase9-issue114-r2-minimal.md",
            "Use the router-injected issue source snapshot as the scope spec",
            "## Issue source snapshot",
            "source: gh-issue-114",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, prompt)
        self.assertIn("# GitHub post rules", prompt)
        self.assertNotIn(GITHUB_POST_RULES_CONTRACT_TOKEN, prompt)
        self.assertNotIn("prompts/_github-post-rules.md", prompt)
        router_header = prompt.split("## Issue source snapshot", 1)[0]
        self.assertNotIn("$REPO_ROOT/.refactor-loop/runs/audit-iter-${ITERATION}.md", router_header)
        self.assertNotIn("cluster spec", router_header)

    def test_peer_reference_check_excludes_issue_source_snapshot(self) -> None:
        # A solver prompt whose injected issue source snapshot quotes a peer
        # solver's prior-round audit-trail log path is NOT an isolation breach:
        # the snapshot is issue-author content (a prior round's consensus record
        # echoed onto the GitHub issue), and blocking judge dispatch on it wedges
        # every design-consensus whose issue body echoes a prior round.
        issue, round_no = "777", 1
        peer_token = f".refactor-loop/logs/phase9-issue{issue}-r{round_no}-minimal.log"
        for role in sorted(self.router._solver_roles()):
            path = self.router._solver_prompt_path(issue, round_no, role)
            path.parent.mkdir(parents=True, exist_ok=True)
            if role == "delete":
                body = (
                    "# design-consensus delete solver\n\n"
                    f"Issue: #{issue}\nRole: delete\n\n"
                    "## Issue source snapshot\n\n"
                    f"source: gh-issue-{issue}\n\n"
                    "## Round audit trail (links to local artifacts)\n"
                    f"- solver-minimal: {peer_token}\n\n"
                    "## Full solver template\n\n"
                    "# Role: Solver - delete framing\nDo your own analysis.\n"
                )
            else:
                body = (
                    f"# {role} solver\n\n"
                    "## Issue source snapshot\n\n(clean)\n\n"
                    "## Full solver template\n\nclean\n"
                )
            path.write_text(body, encoding="utf-8")
        self.assertIsNone(self.router._peer_solver_reference_violation(issue, round_no))

    def test_peer_reference_check_flags_router_controlled_region(self) -> None:
        # A peer reference leaked into a router-controlled region (before the
        # snapshot) is still a real isolation violation and must block dispatch.
        issue, round_no = "778", 1
        peer_token = f".refactor-loop/logs/phase9-issue{issue}-r{round_no}-minimal.log"
        for role in sorted(self.router._solver_roles()):
            path = self.router._solver_prompt_path(issue, round_no, role)
            path.parent.mkdir(parents=True, exist_ok=True)
            if role == "delete":
                body = (
                    "# design-consensus delete solver\n"
                    f"leaked peer evidence: {peer_token}\n\n"
                    "## Issue source snapshot\n\n(clean)\n\n"
                    "## Full solver template\n\nclean\n"
                )
            else:
                body = (
                    f"# {role} solver\n\n"
                    "## Issue source snapshot\n\n(clean)\n\n"
                    "## Full solver template\n\nclean\n"
                )
            path.write_text(body, encoding="utf-8")
        violation = self.router._peer_solver_reference_violation(issue, round_no)
        self.assertIsNotNone(violation)
        self.assertEqual(violation["role"], "delete")
        self.assertEqual(violation["peer_role"], "minimal")
        self.assertEqual(violation["matched_token"], peer_token)

    def test_package_router_unknown_marker_appends_existing_format_fallback_event_only_once(self) -> None:
        self.write_log("phase9-issue160-r1-judge.log", "SOMETHING_DONE:surprise:payload")

        self.router.tick()
        fresh_router = Phase9Router(ctx=self.ctx, command_runner=self.commands.append)
        fresh_router._read_source_issue_decision = self.open_source_issue_decision  # type: ignore[method-assign]
        fresh_router._open_design_consensus_issues = lambda: []  # type: ignore[method-assign]
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

    def test_package_router_ignores_embedded_marker_without_fallback(self) -> None:
        self.write_log(
            "phase9-issue160-r1-judge.log",
            "controller saw META_JUDGE_DONE:converge:round-2:embedded prose",
            "> META_JUDGE_DONE:converge:round-2:quoted",
            "grep output: META_JUDGE_DONE:converge:round-2:grep",
        )

        self.router.tick()

        self.assertEqual(self.commands, [])
        self.assertEqual(self.ledger_entries(), [])
        self.assertEqual(self.pending_events(), "")

    def test_package_router_ignores_standalone_marker_followed_by_raw_prose(self) -> None:
        self.write_log(
            "phase9-issue160-r1-judge.log",
            "META_JUDGE_DONE:converge:round-2",
            "later raw judge prose",
        )

        self.router.tick()

        self.assertEqual(self.commands, [])
        self.assertEqual(self.ledger_entries(), [])
        self.assertEqual(self.pending_events(), "")

    def test_package_router_converge_dispatches_stalled_reflector_when_predicate_holds(self) -> None:
        # Refactor (issue-304): Old: package smoke used a fresh stalled judge
        # marker. New: r3 converge plus unchanged solver history renders the
        # stalled reflector template and suppresses r4 solver dispatch.
        for round_no in (1, 2, 3):
            for role in ("minimal", "structural", "delete"):
                self.write_log(f"phase9-issue160-r{round_no}-{role}.log", f"SOLVER_DONE:{role}:same:summary")
        self.write_log("phase9-issue160-r3-judge.log", "META_JUDGE_DONE:converge:round-3:no-change")

        self.router.tick()

        reflector_commands = [
            command for command in self.commands if "phase9-issue160-r3-reflector.log" in self.intent_text(command)
        ]
        self.assertEqual(len(reflector_commands), 1)
        self.assertIn("160-3-reflector", [entry["key"] for entry in self.ledger_entries()])
        self.assertNotIn("160-4-minimal", [entry["key"] for entry in self.ledger_entries()])
        prompt = (self.repo / ".refactor-loop" / "prompts" / "phase9" / "phase9-issue160-r3-reflector.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("# Role: Meta-reflector - stalled route resolver", prompt)

    def test_router_ignores_host_policy_roles_and_dispatch_for_active_spawn_allowlist(self) -> None:
        ctx = self.write_host_policy()
        router = Phase9Router(ctx=ctx, command_runner=self.commands.append)
        router._read_source_issue_decision = self.open_source_issue_decision  # type: ignore[method-assign]
        for role in ("host:a", "host:b", "host:c"):
            self.write_log(f"phase9-issue219-r1-{role}.log", f"SOLVER_DONE:{role}:same")
        router.tick()

        self.assertEqual(self.commands, [])
        self.assertEqual(self.ledger_entries(), [])
        self.assertEqual(self.pending_events(), "")

        for role in ("minimal", "structural", "delete"):
            self.write_log(f"phase9-issue219-r1-{role}.log", f"SOLVER_DONE:{role}:same")
        router.tick()

        self.assertEqual(len(self.commands), 1)
        self.assertIn("phase9-issue219-r1-judge.log", self.intent_text(self.commands[0]))
        self.assertEqual([entry["key"] for entry in self.ledger_entries()], ["219-1-judge"])
        self.assertEqual(self.pending_events(), "")

    def test_router_does_not_load_or_fail_closed_on_invalid_host_workflow_spec(self) -> None:
        ctx = self.write_host_policy(invalid=True)
        router = Phase9Router(ctx=ctx, command_runner=self.commands.append)
        router._read_source_issue_decision = self.open_source_issue_decision  # type: ignore[method-assign]
        for role in ("minimal", "structural", "delete"):
            self.write_log(f"phase9-issue220-r1-{role}.log", f"SOLVER_DONE:{role}:same")

        router.tick()

        self.assertEqual(len(self.commands), 1)
        self.assertIn("phase9-issue220-r1-judge.log", self.intent_text(self.commands[0]))
        self.assertEqual([entry["key"] for entry in self.ledger_entries()], ["220-1-judge"])
        self.assertEqual(self.pending_events(), "")

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
            "phase9-issue100-r3-host:a.log",
            "solver-issue100-r3-host:a.log",
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
            ".refactor-loop/phase9-router-ledger.jsonl",
            "route",
            "target_actor",
            "clean_exit_solver_logs",
            "solver_input_prompts",
            "judge_input_solver_logs",
            "judge_prompt_path",
            "judge_prompt_template_path",
            "judge_prompt_scope",
            "independence_check",
            "prompts/meta-judge.md",
            "MetaJudgePromptContext",
            "MetaJudgePromptRenderer",
            "phase9-meta-judge-template-unavailable",
            "phase9-meta-judge-scope-invalid",
            "phase9-triplet-evidence-invalid",
            "phase9-triplet-suppression:",
            "phase9-triplet-target-log-exists",
            "phase9-triplet-equivalent-log-exists",
            "phase9-triplet-in-flight",
            "_solver_triplet_ledger_fields",
            "_peer_solver_reference_violation",
            "_peer_solver_reference_tokens",
            "clean_exit_solver_logs",
            "solver_input_prompts",
            "judge_input_solver_logs",
            "judge_prompt_scope",
            "independence_check",
            "Dispatch ledger evidence:",
            ".controller-pending-events.log",
            "phase9-router-fallback",
            "phase9-router.lock",
            "phase9_router_daemon",
            "meta-reflector-stalled.md",
        ):
            with self.subTest(required=required):
                self.assertIn(required, src)

        for forbidden in (
            "WorkUnitReplacement",
            "phase9-evidence",
            "Phase9RoundEvidence",
            "evidence.py",
            "phase9_triplet_evidence",
            "prompt_sha256",
            "sha256",
            "peer_reference_status",
            "phase9-triplet-peer-reference",
            "independence_checks",
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

    def test_package_router_source_uses_meta_judge_template_not_stub_prompt(self) -> None:
        src = PACKAGE_ROUTER.read_text(encoding="utf-8")
        skill = (REPO_ROOT / "skills" / "codex-refactor-loop" / "SKILL.md").read_text(encoding="utf-8")
        combined = "\n".join((src, skill))
        for required in (
            "prompts/meta-judge.md",
            "MetaJudgePromptRenderer",
            "full `prompts/meta-judge.md`",
            "Router template bindings",
            '"WORK_UNIT_PRODUCER"',
            '"WORK_UNIT_SOURCE_REF"',
            "producer/source-ref",
            "missing template",
            "phase9-meta-judge-template-unavailable",
            "phase9-meta-judge-scope-invalid",
        ):
            with self.subTest(required=required):
                self.assertIn(required, combined)
        self.assertNotIn("Read the three completed solver logs and emit META_JUDGE_DONE", src)

    def test_router_source_uses_standalone_marker_matching(self) -> None:
        src = PACKAGE_ROUTER.read_text(encoding="utf-8")
        self.assertIn("MARKER_RE.fullmatch", src)
        self.assertIn("stripped.startswith(prefix)", src)
        self.assertNotIn("stripped.find(prefix)", src)
        self.assertNotIn("MARKER_RE.search", src)

    def test_package_main_once_dispatches_via_absolute_repo_root(self) -> None:
        for role in ("minimal", "structural", "delete"):
            self.write_log(f"phase9-issue160-r5-{role}.log", f"SOLVER_DONE:{role}:same:summary")
        commands: list[dict[str, object]] = []

        with mock.patch.object(
            Phase9Router,
            "_read_source_issue_decision",
            return_value=Phase9SourceIssueDecision(True, "OPEN", "phase9-source-open"),
        ), mock.patch.object(Phase9Router, "_open_design_consensus_issues", return_value=[]):
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
