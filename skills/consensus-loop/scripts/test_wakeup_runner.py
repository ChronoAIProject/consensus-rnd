#!/usr/bin/env python3
"""Behavior tests for the #396 wakeup runner."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from string import Template
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.issue_decomposition import issue_decomposition_plan_file_digest
from codex_refactor_loop import labels
from codex_refactor_loop.cross_instance_stand_down import CrossInstanceAdmission
from codex_refactor_loop.github_budget import reset_graphql_budget_cache
from codex_refactor_loop.github_actor import GitHubActorAdmission
from codex_refactor_loop.release.gate import canonical_digest, isoformat
from codex_refactor_loop.wakeup_runner import (
    WakeupRunner,
    RunnerResult,
    SUPPORTED_CONTROLLER_ACTIONS,
    _log_tick_status,
    _wakeup_tick_action,
    main as wakeup_runner_main,
    _source_log_has_clean_marker,
    run_wakeup_runner_reconcile_tick,
)
from codex_refactor_loop.wakeup_plan import harness_spawn_intent_line_digest


def release_decision(from_version: str, to_version: str) -> dict:
    return {
        "from_version": from_version,
        "to_version": to_version,
        "bump_type": "patch",
        "coordinate_policy": None,
        "ready": True,
        "signals": {"fresh_heartbeats": {"passed": True}},
        "blocked_reasons": [],
    }


class SourceMarkerRevalidationFallbackTests(unittest.TestCase):
    def test_revalidation_falls_back_to_implement_run_artifact_for_duplicate_log_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            logs = repo / ".refactor-loop" / "logs"
            runs = repo / ".refactor-loop" / "runs"
            logs.mkdir(parents=True)
            runs.mkdir(parents=True)
            log = logs / "implement-issue-553.log"
            log.write_text(
                "IMPLEMENT_DONE:issue-553:partial\n"
                "more worker output\n"
                "IMPLEMENT_DONE:issue-553:ok\n"
                "EXIT=0\n",
                encoding="utf-8",
            )
            (runs / "implement-issue-553.md").write_text(
                "body\n⟦AI:AUTO-LOOP⟧\nIMPLEMENT_DONE:issue-553:ok\n",
                encoding="utf-8",
            )

            self.assertTrue(_source_log_has_clean_marker(log, "IMPLEMENT_DONE:issue-553:ok"))

    def test_duplicate_implement_log_revalidation_requires_single_ok_artifact_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            logs = repo / ".refactor-loop" / "logs"
            runs = repo / ".refactor-loop" / "runs"
            logs.mkdir(parents=True)
            runs.mkdir(parents=True)
            marker = "IMPLEMENT_DONE:issue-553:ok"
            log = logs / "implement-issue-553.log"
            log.write_text(f"IMPLEMENT_DONE:issue-553:partial\nworker output\n{marker}\nEXIT=0\n", encoding="utf-8")

            cases = (
                ("missing", None),
                ("multiple", f"{marker}\nIMPLEMENT_DONE:issue-554:ok\n"),
                ("blocked", "IMPLEMENT_DONE:issue-553:blocked\n"),
            )
            for name, artifact_text in cases:
                with self.subTest(name=name):
                    artifact = runs / "implement-issue-553.md"
                    if artifact.exists():
                        artifact.unlink()
                    if artifact_text is not None:
                        artifact.write_text(artifact_text, encoding="utf-8")
                    self.assertFalse(_source_log_has_clean_marker(log, marker))

    def test_duplicate_marker_fallback_is_limited_to_implement_log_duplicate_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            logs = repo / ".refactor-loop" / "logs"
            runs = repo / ".refactor-loop" / "runs"
            logs.mkdir(parents=True)
            runs.mkdir(parents=True)
            other = logs / "review-pr77-security-r1.log"
            other.write_text(
                "REVIEW_DONE:77:security:comment\n"
                "REVIEW_DONE:77:security:approve\n"
                "EXIT=0\n",
                encoding="utf-8",
            )
            (runs / "review-pr77-security-r1.md").write_text(
                "REVIEW_DONE:77:security:approve\n",
                encoding="utf-8",
            )

            self.assertFalse(_source_log_has_clean_marker(other, "REVIEW_DONE:77:security:approve"))

    def test_revalidation_falls_back_to_implement_run_artifact_for_markerless_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            logs = repo / ".refactor-loop" / "logs"
            runs = repo / ".refactor-loop" / "runs"
            logs.mkdir(parents=True)
            runs.mkdir(parents=True)
            log = logs / "implement-issue-421.log"
            log.write_text("chatter, no standalone marker\nEXIT=0\nDONE_AT=x\n", encoding="utf-8")
            (runs / "implement-issue-421.md").write_text(
                "body\n⟦AI:AUTO-LOOP⟧\nIMPLEMENT_DONE:issue-421:ok\n", encoding="utf-8"
            )
            self.assertTrue(_source_log_has_clean_marker(log, "IMPLEMENT_DONE:issue-421:ok"))
            # scope guard: a non-implement log must not fall back to an artifact
            other = logs / "audit-iter-9.log"
            other.write_text("x\nEXIT=0\n", encoding="utf-8")
            (runs / "audit-iter-9.md").write_text("IMPLEMENT_DONE:issue-9:ok\n", encoding="utf-8")
            self.assertFalse(_source_log_has_clean_marker(other, "IMPLEMENT_DONE:issue-9:ok"))
            # scope guard: an unclean implement log must not fall back
            unclean = logs / "implement-issue-777.log"
            unclean.write_text("crash\nEXIT=1\n", encoding="utf-8")
            (runs / "implement-issue-777.md").write_text("IMPLEMENT_DONE:issue-777:ok\n", encoding="utf-8")
            self.assertFalse(_source_log_has_clean_marker(unclean, "IMPLEMENT_DONE:issue-777:ok"))

    def test_revalidation_rejects_raw_embedded_marker_text_without_shared_reader_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            logs = repo / ".refactor-loop" / "logs"
            logs.mkdir(parents=True)
            log = logs / "implement-issue-421.log"
            log.write_text(
                "controller saw IMPLEMENT_DONE:issue-421:ok in prose\nEXIT=0\n",
                encoding="utf-8",
            )

            self.assertFalse(_source_log_has_clean_marker(log, "IMPLEMENT_DONE:issue-421:ok"))


class FakeSupervisor:
    def __init__(self) -> None:
        self.calls = []

    def supervise(self, command, *, stdin, log, stall, preamble=""):
        self.calls.append({"command": list(command), "stdin": stdin, "log": log, "stall": stall})
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        Path(log).write_text("EXIT=0\n", encoding="utf-8")
        return 0


class FakeActions:
    def __init__(self, *, safe_push_code: int = 0, publish_code: int = 0, close_code: int = 0) -> None:
        self.safe_push_code = safe_push_code
        self.publish_code = publish_code
        self.close_code = close_code
        self.calls: list[tuple[str, object]] = []
        self.github_actor = self

    def require_admission(self, action: str) -> GitHubActorAdmission:
        return GitHubActorAdmission("current-user", "owner/repo", "write")

    def cross_instance_admission(self, kind: str, target: str | int, current_login: str, now: datetime) -> CrossInstanceAdmission:
        return CrossInstanceAdmission("allowed", "test-fake")

    def safe_push(self, remote: str = "origin", branch: str = "", worktree: str | Path | None = None) -> int:
        self.calls.append(("safe_push", {"remote": remote, "branch": branch, "worktree": str(worktree or "")}))
        return self.safe_push_code

    def _require_branch_push_admission_or_return(self, action: str, branch: str, worktree: Path, *, current_login: str = "") -> int | None:
        return None

    def publish_worker_output_from_action(self, action: dict) -> int:
        self.calls.append(("publish_worker_output_from_action", dict(action)))
        return self.publish_code

    def publish_implementation_output(self, action: dict) -> int:
        self.calls.append(("publish_implementation_output", dict(action)))
        return self.publish_code

    def dispatch_consensus_implementation(self, action: dict) -> int:
        self.calls.append(("dispatch_consensus_implementation", dict(action)))
        return 0

    def dispatch_reviewers(self, action: dict) -> int:
        self.calls.append(("dispatch_reviewers", dict(action)))
        return 0

    def render_template(self, input_path: str, output_path: str, env: dict | None = None) -> None:
        values = dict(env or {})
        Path(output_path).write_text(Template(Path(input_path).read_text(encoding="utf-8")).safe_substitute(values), encoding="utf-8")

    def dispatch_pr_rebase_resolve(self, action: dict) -> int:
        self.calls.append(("dispatch_pr_rebase_resolve", dict(action)))
        return 0

    def commit_push_resolved_pr_rebase(self, action: dict) -> int:
        self.calls.append(("commit_push_resolved_pr_rebase", dict(action)))
        return 0

    def open_release_rollup_pr_from_action(self, action: dict) -> int:
        self.calls.append(("open_release_rollup_pr_from_action", dict(action)))
        return 0

    def auto_merge_release_rollup_pr_from_action(self, action: dict) -> int:
        self.calls.append(("auto_merge_release_rollup_pr_from_action", dict(action)))
        return 0

    def apply_issue_decomposition_plan(self, plan_path: str) -> tuple[tuple[int, str], ...]:
        self.calls.append(("apply_issue_decomposition_plan", plan_path))
        return ((501, "https://github.com/owner/repo/issues/501"),)

    def apply_default_issue_intake_claim(self, issue_number: int):
        self.calls.append(("apply_default_issue_intake_claim", issue_number))
        return None

    def render_release_rollup_body_prompt(self, action: dict) -> Path:
        self.calls.append(("render_release_rollup_body_prompt", dict(action)))
        prompt = Path(action["prompt"])
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("release rollup body prompt\n", encoding="utf-8")
        return prompt

    def render_implementation_pr_artifact_repair_prompt(self, action: dict) -> Path:
        self.calls.append(("render_implementation_pr_artifact_repair_prompt", dict(action)))
        prompt = Path(action["prompt"])
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("implementation PR artifact repair prompt\n", encoding="utf-8")
        return prompt

    def close_managed_item_from_drop_marker(self, action: dict) -> int:
        self.calls.append(("close_managed_item_from_drop_marker", dict(action)))
        return self.close_code

    def merge_pr(self, target: str) -> int:
        self.calls.append(("merge_pr", target))
        return 0

    def render_review_fix_prompt(self, pr_number: int, round_number: int):
        raise AssertionError("review fix should not be dispatched")

    def publish_release_candidate(self, *, candidate_path: str, target_ref: str):
        raise AssertionError("release publish should not be dispatched")


class FakeReviewFixActions(FakeActions):
    def __init__(self, repo: Path) -> None:
        super().__init__()
        self.repo = repo
        self.rendered: list[tuple[int, int]] = []

    def render_review_fix_prompt(self, pr_number: int, round_number: int):
        self.rendered.append((pr_number, round_number))
        prompt_path = ".refactor-loop/prompts/fixes/fix-pr77-round-1.md"
        log_path = ".refactor-loop/logs/fix-pr77-round-1.log"
        (self.repo / prompt_path).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / prompt_path).write_text("headless rendered prompt\n", encoding="utf-8")
        return type("Spec", (), {"prompt_path": prompt_path, "log_path": log_path})()


class WakeupRunnerBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_graphql_budget_cache()
        self.graphql_headroom_patch = mock.patch("codex_refactor_loop.wakeup_runner.graphql_headroom_ok", return_value=True)
        self.graphql_headroom_patch.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        for rel in (".refactor-loop/state", ".refactor-loop/logs", ".refactor-loop/prompts", ".refactor-loop/runs"):
            (self.repo / rel).mkdir(parents=True, exist_ok=True)
        (self.repo / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.repo / ".config/consensus-rnd/host.env").write_text(
            f'export REPO_ROOT="{self.repo}"\n'
            'export GH_REPO_SLUG="owner/repo"\n'
            'export INTEGRATION_BRANCH="auto-refact-dev"\n'
            'export REVIEW_BASE_BRANCH="dev"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.repo, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})
        self.supervisor = FakeSupervisor()
        self.expected_skill_root = SCRIPT_DIR.parent

    def tearDown(self) -> None:
        self.tmp.cleanup()
        self.graphql_headroom_patch.stop()
        reset_graphql_budget_cache()

    def test_run_command_injects_gh_repo_only_in_valid_subcommand_position(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            calls.append(list(command))
            return subprocess.CompletedProcess(command, 0, "", "")

        runner = WakeupRunner(self.ctx)
        with mock.patch("codex_refactor_loop.wakeup_runner.subprocess.run", side_effect=fake_run):
            runner._run_command(["gh", "api", "repos/owner/repo/pulls/77"])
            runner._run_command(["gh", "pr", "view", "77", "--json", "mergeable,isDraft,changedFiles"])
            runner._run_command(["gh", "issue", "view", "53", "--json", "state"])

        self.assertEqual(calls[0], ["gh", "api", "repos/owner/repo/pulls/77"])
        self.assertEqual(calls[1], ["gh", "pr", "view", "77", "--repo", "owner/repo", "--json", "mergeable,isDraft,changedFiles"])
        self.assertEqual(calls[2], ["gh", "issue", "view", "53", "--repo", "owner/repo", "--json", "state"])
        for command in calls:
            self.assertNotEqual(command[:2], ["gh", "--repo"])

    def test_run_command_preserves_existing_gh_repo_flag(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            calls.append(list(command))
            return subprocess.CompletedProcess(command, 0, "", "")

        runner = WakeupRunner(self.ctx)
        with mock.patch("codex_refactor_loop.wakeup_runner.subprocess.run", side_effect=fake_run):
            runner._run_command(["gh", "pr", "view", "77", "--repo", "other/repo", "--json", "state"])

        self.assertEqual(calls, [["gh", "pr", "view", "77", "--repo", "other/repo", "--json", "state"]])

    def test_apply_default_issue_intake_claim_dispatches_named_helper(self) -> None:
        action = {
            "kind": "default-issue-intake-claim",
            "action_id": "default-issue-intake-claim:issue:77",
            "runner_authority": "wakeup-runner-396",
            "preconditions": [
                "active_controller_owner",
                "default_issue_intake_enabled",
                "live_open_target",
                "non_pr_issue",
                "target_not_managed",
                "github_comment_claim_protocol",
            ],
            "source_artifact": "github-open-default-issue-intake-candidates",
            "source_marker": "default-issue-intake-candidate:issue:77",
            "target_kind": "issue",
            "target_number": 77,
            "target": {"kind": "issue", "number": 77},
            "controller_action": "apply_default_issue_intake_claim",
            "no_generic_command": True,
            "no_lifecycle_authority": True,
        }
        actions = FakeActions()

        result = self.run_result(self.base_plan(action), gh_labels=[], actions=actions)

        self.assertEqual("applied", result[0].status)
        self.assertEqual([("apply_default_issue_intake_claim", 77)], actions.calls)

    def test_apply_action_skips_managed_write_on_cross_instance_stand_down(self) -> None:
        action = {
            "kind": "review-dispatch",
            "action_id": "dispatch-reviewers:pr:77",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "live_open_target", "clean_exit_source_marker"],
            "source_artifact": ".refactor-loop/logs/review-ready.log",
            "source_marker": "REVIEW_DONE:77:architect:approve",
            "target_kind": "PR",
            "target_number": 77,
            "target": {"kind": "PR", "number": 77},
            "controller_action": "dispatch_reviewers",
            "no_generic_command": True,
            "no_lifecycle_authority": True,
        }
        (self.repo / ".refactor-loop/logs/review-ready.log").write_text("REVIEW_DONE:77:architect:approve\nEXIT=0\n", encoding="utf-8")
        actions = FakeActions()
        actions.cross_instance_admission = lambda kind, target, current_login, now: CrossInstanceAdmission(
            "stand_down",
            "fresh_other_instance_comment:other-user",
            other_login="other-user",
            source="comment",
            created_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        )

        result = self.run_result(self.base_plan(action), gh_state="OPEN", actions=actions)

        self.assertEqual("skipped", result[0].status)
        self.assertIn("cross_instance_stand_down:pr:77", result[0].reason)
        self.assertEqual([], actions.calls)
        self.assertIn("CROSS_INSTANCE_STAND_DOWN:dispatch_reviewers:pr:77", (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8"))

    def test_runner_blocks_high_risk_action_before_helper_dispatch(self) -> None:
        action = self.release_dispatch_action(
            risk_tier="high",
            execution_policy="blocked",
            argv=["gh", "release", "create"],
        )
        actions = FakeActions()

        result = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual("blocked", result[0].status)
        self.assertEqual("risk_tier_high", result[0].reason)
        self.assertEqual([], actions.calls)

    def test_runner_blocks_medium_without_cautious_policy(self) -> None:
        action = self.release_dispatch_action(risk_tier="medium", execution_policy="auto")
        actions = FakeActions()

        result = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual("blocked", result[0].status)
        self.assertEqual("medium_requires_cautious_execution_policy", result[0].reason)
        self.assertEqual([], actions.calls)

    def test_runner_archives_invalid_harness_spawn_intent_without_dispatch(self) -> None:
        raw_line = "2026-06-01T00:00:00Z HARNESS_SPAWN_INTENT " + json.dumps(
            {
                "intent_id": "bad:colon:id",
                "source": "controller-actions",
                "route": "dispatch-consensus-implementation",
                "task_id": "implement-issue-771",
                "priority": "p1",
                "command": "spawn-codex",
                "controller_action": "spawn_codex_harness_background",
                "cd": str(self.repo),
                "prompt": ".refactor-loop/prompts/implement-issue-771.md",
                "log": ".refactor-loop/logs/implement-issue-771.log",
                "stall": 5400,
                "reason": "issue #771 consensus implementation",
                "queued_at": None,
                "run_in_background_required": True,
                "no_lifecycle_authority": True,
            },
            sort_keys=True,
        )
        self.ctx.paths.pending_events.write_text(raw_line + "\n", encoding="utf-8")
        digest = harness_spawn_intent_line_digest(raw_line)
        action = {
            "kind": "harness-spawn-intent-invalid",
            "action_id": f"harness-spawn-intent-invalid:{digest}",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["source_artifact_contains_evidence"],
            "source_artifact": ".refactor-loop/.controller-pending-events.log",
            "source_marker": "HARNESS_SPAWN_INTENT",
            "evidence_digest": digest,
            "reason": "missing-queued_at",
            "no_lifecycle_authority": True,
            "no_generic_command": True,
        }
        actions = FakeActions()

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", side_effect=AssertionError("must not spawn")) as launch:
            results = self.run_result(self.base_plan(action), actions=actions)
            duplicate = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual("skipped", results[0].status)
        self.assertEqual("archived_invalid_harness_spawn_intent:missing-queued_at", results[0].reason)
        self.assertEqual("skipped", duplicate[0].status)
        self.assertEqual("duplicate", duplicate[0].reason)
        self.assertEqual([], actions.calls)
        launch.assert_not_called()
        pending = self.ctx.paths.pending_events.read_text(encoding="utf-8")
        archived_marker = f"WAKEUP_RUNNER_ARCHIVED_INVALID_HARNESS_SPAWN_INTENT:{digest}:missing-queued_at"
        self.assertIn(archived_marker, pending)
        self.assertEqual(1, pending.count(archived_marker))
        ledger_rows = [
            json.loads(line)
            for line in (self.ctx.paths.state / "wakeup-runner-ledger.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            [
                {
                    "action_id": f"harness-spawn-intent-invalid:{digest}",
                    "kind": "harness-spawn-intent-invalid",
                    "reason": "archived_invalid_harness_spawn_intent:missing-queued_at",
                    "status": "skipped",
                },
                {
                    "action_id": f"harness-spawn-intent-invalid:{digest}",
                    "kind": "harness-spawn-intent-invalid",
                    "reason": "duplicate",
                    "status": "skipped",
                },
            ],
            ledger_rows,
        )

    def test_runner_quarantines_distinct_invalid_harness_spawn_intents_with_same_id(self) -> None:
        def raw_line(reason: str) -> str:
            payload = {
                "intent_id": "dispatch-reviewers:77:architect:r1",
                "source": "controller-actions",
                "route": "dispatch-reviewers",
                "task_id": "review-pr77-architect-r1",
                "priority": "p1",
                "command": "spawn-codex",
                "controller_action": "spawn_codex_harness_background",
                "cd": str(self.repo),
                "prompt": ".refactor-loop/prompts/review-pr77-architect-r1.md",
                "log": ".refactor-loop/logs/review-pr77-architect-r1.log",
                "stall": 5400,
                "reason": reason,
                "queued_at": None,
                "run_in_background_required": True,
                "no_lifecycle_authority": True,
            }
            return "2026-06-01T00:00:00Z HARNESS_SPAWN_INTENT " + json.dumps(payload, sort_keys=True)

        lines = [raw_line("first malformed"), raw_line("second malformed")]
        actions = [
            {
                "kind": "harness-spawn-intent-invalid",
                "action_id": f"harness-spawn-intent-invalid:{harness_spawn_intent_line_digest(line)}",
                "runner_authority": "wakeup-runner-396",
                "preconditions": ["source_artifact_contains_evidence"],
                "source_artifact": ".refactor-loop/.controller-pending-events.log",
                "source_marker": "HARNESS_SPAWN_INTENT",
                "evidence_digest": harness_spawn_intent_line_digest(line),
                "reason": "missing-queued_at",
                "no_lifecycle_authority": True,
                "no_generic_command": True,
            }
            for line in lines
        ]
        self.ctx.paths.pending_events.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", side_effect=AssertionError("must not spawn")) as launch:
            results = self.run_result({**self.base_plan(actions[0]), "actions": [self.annotate_safe_progress(action) for action in actions]}, actions=FakeActions())

        self.assertEqual(["skipped", "skipped"], [result.status for result in results])
        self.assertEqual(
            ["archived_invalid_harness_spawn_intent:missing-queued_at", "archived_invalid_harness_spawn_intent:missing-queued_at"],
            [result.reason for result in results],
        )
        launch.assert_not_called()
        pending = self.ctx.paths.pending_events.read_text(encoding="utf-8")
        for line in lines:
            marker = (
                "WAKEUP_RUNNER_ARCHIVED_INVALID_HARNESS_SPAWN_INTENT:"
                + harness_spawn_intent_line_digest(line)
                + ":missing-queued_at"
            )
            self.assertEqual(1, pending.count(marker))

    def test_hard_gate_archives_invalid_harness_intent_before_spawn_top_up(self) -> None:
        raw_line = "2026-06-01T00:00:00Z HARNESS_SPAWN_INTENT " + json.dumps(
            {
                "intent_id": "dispatch-reviewers:77:quality:r6",
                "source": "dispatch-reviewers",
                "route": "dispatch-reviewers",
                "task_id": "review-pr77-quality-r6",
                "priority": "p1",
                "command": "spawn-codex",
                "controller_action": "spawn_codex_harness_background",
                "cd": str(self.repo),
                "prompt": ".refactor-loop/prompts/review-pr77-quality-r6.md",
                "log": ".refactor-loop/logs/review-pr77-quality-r6.log",
                "stall": 5400,
                "reason": "review PR #77 as quality",
                "run_in_background_required": True,
                "no_lifecycle_authority": True,
            },
            sort_keys=True,
        )
        digest = harness_spawn_intent_line_digest(raw_line)
        invalid = {
            "kind": "harness-spawn-intent-invalid",
            "action_id": f"harness-spawn-intent-invalid:{digest}",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["source_artifact_contains_evidence"],
            "source_artifact": ".refactor-loop/.controller-pending-events.log",
            "source_marker": "HARNESS_SPAWN_INTENT",
            "evidence_digest": digest,
            "reason": "missing-queued_at",
            "no_lifecycle_authority": True,
            "no_generic_command": True,
        }
        spawn = self.spawn_action(action_id="spawn:hard-gate-top-up")
        with self.ctx.paths.pending_events.open("a", encoding="utf-8") as handle:
            handle.write(raw_line + "\n")

        results = self.run_result(self.batch_plan([spawn, invalid], dispatch_required=2, deficit=2), actions=FakeActions())

        self.assertIn(f"WAKEUP_RUNNER_ARCHIVED_INVALID_HARNESS_SPAWN_INTENT:{digest}:missing-queued_at", self.ctx.paths.pending_events.read_text(encoding="utf-8"))
        self.assertEqual(["skipped", "applied"], [result.status for result in results])
        self.assertEqual("archived_invalid_harness_spawn_intent:missing-queued_at", results[0].reason)

    def test_ordinary_tick_archives_legacy_malformed_review_intent_for_merged_pr_before_successful_action(self) -> None:
        raw_line = "2026-06-10T11:48:39Z HARNESS_SPAWN_INTENT " + json.dumps(
            {
                "cd": "/Users/auric/consensus-rnd",
                "command": "spawn-codex",
                "controller_action": "spawn_codex_harness_background",
                "intent_id": "dispatch-reviewers:774:architect:r5",
                "log": ".refactor-loop/logs/review-pr774-architect-r5.log",
                "no_lifecycle_authority": True,
                "priority": "p1",
                "prompt": ".refactor-loop/prompts/review-pr774-architect-r5.md",
                "reason": "review PR #774 as architect",
                "route": "dispatch-reviewers",
                "run_in_background_required": True,
                "source": "controller-actions",
                "stall": 5400,
                "task_id": "review-pr774-architect-r5",
            },
            sort_keys=True,
        )
        self.ctx.paths.pending_events.write_text(raw_line + "\n", encoding="utf-8")
        digest = harness_spawn_intent_line_digest(raw_line)
        invalid = {
            "kind": "harness-spawn-intent-invalid",
            "action_id": f"harness-spawn-intent-invalid:{digest}",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["source_artifact_contains_evidence"],
            "source_artifact": ".refactor-loop/.controller-pending-events.log",
            "source_marker": "HARNESS_SPAWN_INTENT",
            "evidence_digest": digest,
            "reason": "missing-queued_at",
            "no_lifecycle_authority": True,
            "no_generic_command": True,
        }
        successful = self.release_rollup_action(action_id="release-rollup-needed:ordinary-success")
        with self.ctx.paths.pending_events.open("a", encoding="utf-8") as handle:
            handle.write(raw_line + "\n")
        actions = FakeActions()

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", side_effect=AssertionError("must not spawn")):
            results = self.run_result(self.batch_plan([successful, invalid], dispatch_required=0, deficit=0, active=False), actions=actions)

        self.assertEqual(["skipped", "applied"], [result.status for result in results])
        self.assertEqual("archived_invalid_harness_spawn_intent:missing-queued_at", results[0].reason)
        self.assertEqual("release-rollup-needed:ordinary-success", results[1].action_id)
        self.assertIn(f"WAKEUP_RUNNER_ARCHIVED_INVALID_HARNESS_SPAWN_INTENT:{digest}:missing-queued_at", self.ctx.paths.pending_events.read_text(encoding="utf-8"))
        self.assertEqual(["open_release_rollup_pr_from_action"], [name for name, _payload in actions.calls])

    def test_ordinary_tick_archives_capped_invalid_harness_batch_then_allows_work(self) -> None:
        invalid_actions = []
        pending_lines = []
        for index in range(7):
            raw_line = "2026-06-10T11:48:39Z HARNESS_SPAWN_INTENT " + json.dumps(
                {
                    "cd": str(self.repo),
                    "command": "spawn-codex",
                    "controller_action": "spawn_codex_harness_background",
                    "intent_id": f"invalid-review:{index}",
                    "log": f".refactor-loop/logs/invalid-review-{index}.log",
                    "no_lifecycle_authority": True,
                    "prompt": f".refactor-loop/prompts/invalid-review-{index}.md",
                    "run_in_background_required": True,
                    "source": "controller-actions",
                    "task_id": f"invalid-review-{index}",
                },
                sort_keys=True,
            )
            pending_lines.append(raw_line)
            digest = harness_spawn_intent_line_digest(raw_line)
            invalid_actions.append(
                {
                    "kind": "harness-spawn-intent-invalid",
                    "action_id": f"harness-spawn-intent-invalid:{digest}",
                    "runner_authority": "wakeup-runner-396",
                    "preconditions": ["source_artifact_contains_evidence"],
                    "source_artifact": ".refactor-loop/.controller-pending-events.log",
                    "source_marker": "HARNESS_SPAWN_INTENT",
                    "evidence_digest": digest,
                    "reason": "missing-queued_at",
                    "no_lifecycle_authority": True,
                    "no_generic_command": True,
                }
            )
        self.ctx.paths.pending_events.write_text("\n".join(pending_lines) + "\n", encoding="utf-8")
        ordinary = self.release_rollup_action(action_id="release-rollup-needed:after-invalid-batch")
        with self.ctx.paths.pending_events.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(pending_lines) + "\n")
        actions = FakeActions()

        results = self.run_result(
            self.batch_plan([ordinary, *invalid_actions], dispatch_required=0, deficit=0, active=False),
            actions=actions,
        )

        archived = [result for result in results if result.reason.startswith("archived_invalid_harness_spawn_intent:")]
        self.assertGreater(len(archived), 1)
        self.assertLessEqual(len(archived), 5)
        self.assertEqual(results[-1].action_id, "release-rollup-needed:after-invalid-batch")
        self.assertEqual(results[-1].status, "applied")
        self.assertEqual(["open_release_rollup_pr_from_action"], [name for name, _payload in actions.calls])

    def test_runner_applies_at_most_one_medium_non_spawn_per_tick(self) -> None:
        base = {
            "kind": "default-issue-intake-claim",
            "runner_authority": "wakeup-runner-396",
            "preconditions": [
                "active_controller_owner",
                "default_issue_intake_enabled",
                "live_open_target",
                "non_pr_issue",
                "target_not_managed",
                "github_comment_claim_protocol",
            ],
            "source_artifact": "github-open-default-issue-intake-candidates",
            "controller_action": "apply_default_issue_intake_claim",
            "no_generic_command": True,
            "no_lifecycle_authority": True,
            "risk_tier": "medium",
            "execution_policy": "cautious",
        }
        first = {
            **base,
            "action_id": "default-issue-intake-claim:issue:77",
            "source_marker": "default-issue-intake-candidate:issue:77",
            "target_kind": "issue",
            "target_number": 77,
            "target": {"kind": "issue", "number": 77},
        }
        second = {
            **base,
            "action_id": "default-issue-intake-claim:issue:78",
            "source_marker": "default-issue-intake-candidate:issue:78",
            "target_kind": "issue",
            "target_number": 78,
            "target": {"kind": "issue", "number": 78},
        }
        actions = FakeActions()

        results = self.run_result(self.batch_plan([first, second], dispatch_required=2, deficit=2), gh_labels=[], actions=actions)

        self.assertEqual(["default-issue-intake-claim:issue:77"], [result.action_id for result in results])

    def test_apply_default_issue_intake_claim_rejects_pr_shape(self) -> None:
        action = {
            "kind": "default-issue-intake-claim",
            "action_id": "default-issue-intake-claim:issue:77",
            "runner_authority": "wakeup-runner-396",
            "preconditions": [
                "active_controller_owner",
                "default_issue_intake_enabled",
                "live_open_target",
                "non_pr_issue",
                "target_not_managed",
                "github_comment_claim_protocol",
            ],
            "source_artifact": "github-open-default-issue-intake-candidates",
            "source_marker": "default-issue-intake-candidate:issue:77",
            "target_kind": "issue",
            "target_number": 77,
            "target": {"kind": "issue", "number": 77},
            "controller_action": "apply_default_issue_intake_claim",
            "no_generic_command": True,
            "no_lifecycle_authority": True,
        }

        def command_runner(command):
            if command[:2] == ["gh", "api"]:
                return subprocess.CompletedProcess(command, 0, json.dumps({"state": "open", "pull_request": {}}), "")
            if command[:3] == ["gh", "issue", "view"]:
                return subprocess.CompletedProcess(command, 0, json.dumps({"labels": [], "body": ""}), "")
            return subprocess.CompletedProcess(command, 0, "", "")

        runner = WakeupRunner(
            self.ctx,
            plan_loader=lambda _repo: self.base_plan(action),
            actions=FakeActions(),
            command_runner=command_runner,
        )

        result = runner.run_once()

        self.assertEqual("blocked", result[0].status)
        self.assertEqual("default_issue_intake_target_is_pr", result[0].reason)

    def run_result(
        self,
        plan: dict,
        *,
        gh_state: str | None = "OPEN",
        gh_labels: list[str] | None = None,
        gh_head_ref: str = "refactor/iter77-worker",
        git_diff_code: int = 0,
        implementation_status: str | None = None,
        implementation_issue: int = 77,
        duplicate_prs: list[dict] | None = None,
        implementation_base: tuple[str, str] = ("base-sha", "base-sha"),
        actions=None,
        issue_comments: list[dict] | None = None,
        open_rollup_prs: list[dict] | None = None,
        remote_rollup_refs: dict[str, str] | None = None,
    ) -> list:
        def command_runner(command):
            if command[:2] == ["gh", "api"]:
                endpoint = str(command[2]) if len(command) > 2 else ""
                if endpoint == "user":
                    return subprocess.CompletedProcess(command, 0, json.dumps({"login": "current-user"}), "")
                if endpoint.startswith("repos/owner/repo/collaborators/") and endpoint.endswith("/permission"):
                    return subprocess.CompletedProcess(command, 0, json.dumps({"permission": "write"}), "")
                if endpoint.endswith("/timeline"):
                    return subprocess.CompletedProcess(command, 0, "[]", "")
                if endpoint == "repos/owner/repo/pulls/77":
                    if gh_state is None:
                        return subprocess.CompletedProcess(command, 1, "", "not found")
                    payload = {"state": str(gh_state).lower(), "head": {"sha": "a" * 40}}
                    if gh_state == "MERGED":
                        payload = {"state": "closed", "merged": True, "head": {"sha": "a" * 40}}
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
                if endpoint == f"repos/owner/repo/commits/{'a' * 40}/check-runs":
                    payload = [{"check_runs": [{"name": "ci", "status": "completed", "conclusion": "success"}]}]
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
                if endpoint == "repos/owner/repo/branches/main/protection/required_status_checks":
                    return subprocess.CompletedProcess(command, 0, json.dumps({"contexts": ["ci"]}), "")
                if endpoint == "repos/owner/repo/rules/branches/main":
                    return subprocess.CompletedProcess(command, 1, "", "404 Not Found")
                if endpoint == "repos/owner/repo/issues/77":
                    if gh_state is None:
                        return subprocess.CompletedProcess(command, 1, "", "not found")
                    live_labels = gh_labels if gh_labels is not None else [labels.MANAGED]
                    payload = {
                        "state": str(gh_state).lower(),
                        "labels": [{"name": name} for name in live_labels],
                    }
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
                if "/pulls/" in endpoint or "/issues/" in endpoint:
                    if gh_state is None:
                        return subprocess.CompletedProcess(command, 1, "", "not found")
                    payload = {"state": str(gh_state).lower()}
                    if gh_state == "MERGED":
                        payload = {"state": "closed", "merged": True}
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
                return subprocess.CompletedProcess(command, 0, "{}", "")
            if command[:4] == ["gh", "pr", "list", "--state"]:
                if "--head" in command:
                    head = str(command[command.index("--head") + 1])
                    payload = open_rollup_prs or []
                    payload = [row for row in payload if row.get("headRefName") == head]
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
                payload = duplicate_prs
                if payload is None:
                    payload = [
                        {
                            "number": 99,
                            "baseRefName": "auto-refact-dev",
                            "headRefName": "refactor/iter77-issue-77",
                            "labels": [{"name": labels.MANAGED}],
                            "body": "Closes #77\n",
                        }
                    ]
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
            if command[:3] == ["gh", "issue", "view"] and "comments" in command:
                return subprocess.CompletedProcess(command, 0, json.dumps({"comments": issue_comments or []}), "")
            if command[:3] == ["gh", "pr", "view"] and "comments" in command:
                return subprocess.CompletedProcess(command, 0, json.dumps({"comments": []}), "")
            if command[:3] == ["gh", "issue", "view"] or command[:3] == ["gh", "pr", "view"]:
                if "labels,body" in command:
                    live_labels = gh_labels if gh_labels is not None else [labels.MANAGED]
                    payload = {"labels": [{"name": name} for name in live_labels], "body": ""}
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
                if "headRefName" in command:
                    if "--jq" not in command:
                        return subprocess.CompletedProcess(command, 0, json.dumps({"headRefName": gh_head_ref}), "")
                    return subprocess.CompletedProcess(command, 0, gh_head_ref + "\n", "")
                if "baseRefName,headRefOid,mergeStateStatus" in command:
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        json.dumps({"baseRefName": "main", "headRefOid": "a" * 40, "mergeStateStatus": "DIRTY"}),
                        "",
                    )
                if ".headRefOid" in command:
                    return subprocess.CompletedProcess(command, 0, "a" * 40 + "\n", "")
                if "mergeable,isDraft,changedFiles" in command:
                    return subprocess.CompletedProcess(command, 0, json.dumps({"mergeable": "MERGEABLE", "isDraft": False, "changedFiles": 1}), "")
                if gh_state is None:
                    return subprocess.CompletedProcess(command, 1, "", "not found")
                return subprocess.CompletedProcess(command, 0, gh_state + "\n", "")
            git_cwd = Path(command[2]).resolve() if len(command) >= 3 and command[:2] == ["git", "-C"] else None
            repo_root = self.ctx.repo_root
            if git_cwd == (self.repo / ".worktrees" / "pr77").resolve():
                return subprocess.CompletedProcess(command, git_diff_code, "", "")
            if git_cwd == (self.repo / ".worktrees" / f"iter{implementation_issue}-issue-{implementation_issue}").resolve():
                if command[3:] == ["status", "--porcelain"]:
                    return subprocess.CompletedProcess(command, 0, implementation_status or "", "")
                if command[3:] == ["diff", "HEAD", "--quiet"]:
                    return subprocess.CompletedProcess(command, git_diff_code, "", "")
                if command[3:] == ["rev-parse", "--abbrev-ref", "HEAD"]:
                    return subprocess.CompletedProcess(command, 0, f"refactor/iter{implementation_issue}-issue-{implementation_issue}\n", "")
                if command[3:] == ["merge-base", "HEAD", "origin/auto-refact-dev"]:
                    return subprocess.CompletedProcess(command, 0, implementation_base[0] + "\n", "")
                if command[3:] == ["rev-parse", "--verify", "origin/auto-refact-dev"]:
                    return subprocess.CompletedProcess(command, 0, implementation_base[1] + "\n", "")
            if git_cwd == repo_root.resolve() and command[3:] == ["worktree", "list", "--porcelain"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    f"worktree {repo_root}\nbranch refs/heads/auto-refact-dev\n\n"
                    f"worktree {repo_root / '.worktrees' / 'iter77-worker'}\nbranch refs/heads/{gh_head_ref}\n\n",
                    "",
                )
            if command[:5] == ["git", "ls-remote", "--exit-code", "--heads", "origin"]:
                ref = str(command[5])
                sha = (remote_rollup_refs or {}).get(ref)
                if sha:
                    return subprocess.CompletedProcess(command, 0, f"{sha}\trefs/heads/{ref}\n", "")
                return subprocess.CompletedProcess(command, 2, "", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        runner = WakeupRunner(
            self.ctx,
            plan_loader=lambda _repo: plan,
            actions=actions,
            supervisor=self.supervisor,
            command_runner=command_runner,
        )
        return runner.run_once()

    def base_plan(self, action: dict) -> dict:
        return {
            "schema": "wakeup-plan",
            "mode": "closed-action-projection",
            "apply_authority": "wakeup-runner-396-only",
            "no_lifecycle_authority": True,
            "actions": [self.annotate_safe_progress(action)],
            "blocked_queue": [],
        }

    def release_dispatch_action(self, **overrides) -> dict:
        action = {
            "kind": "release-gate-dispatch",
            "action_id": "release-gate-dispatch:1.2.3-beta.4->1.2.3-beta.5",
            "runner_authority": "wakeup-runner-396",
            "preconditions": [
                "active_controller_owner",
                "release_auto_opt_in",
                "release_gate_ready",
                "decision_artifact_only",
            ],
            "source_artifact": ".refactor-loop/state/auto-release-signals.json",
            "target_kind": None,
            "target_number": None,
            "target": None,
            "controller_action": "dispatch_release_candidate",
            "from_version": "1.2.3-beta.4",
            "to_version": "1.2.3-beta.5",
            "no_generic_command": True,
            "no_lifecycle_authority": True,
        }
        action.update(overrides)
        return action

    def write_release_dispatch_fixtures(self, *, auto_enable: bool = True) -> None:
        host_env = self.repo / ".config/consensus-rnd/host.env"
        with host_env.open("a", encoding="utf-8") as handle:
            handle.write(f'export RELEASE_AUTO_ENABLE="{"true" if auto_enable else "false"}"\n')
            handle.write('export HOST_GITHUB_RELEASE_REQUIRED_CHECKS="ci"\n')
            handle.write('export RELEASE_TARGET_REF="origin/dev"\n')
        self.ctx = LoopContext.load(repo_root=self.repo, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})
        (self.repo / ".version-bump.json").write_text(
            json.dumps({"files": [{"path": "package.json", "field": "version"}]}),
            encoding="utf-8",
        )
        (self.repo / "package.json").write_text(json.dumps({"version": "1.2.3-beta.4"}), encoding="utf-8")
        signals = {
            "signals": {
                "required_checks_recent_green": {"passed": True},
                "no_open_blocked_pr": {"passed": True},
                "no_human_decision_label": {"passed": True},
                "no_phase8_reject_churn": {"passed": True},
                "p0_alert_streak_ok": {"passed": True},
                "recent_pr_merges_min": {"passed": True},
                "fresh_heartbeats": {"passed": True},
                "no_unresolved_human_escalation": {"passed": True},
            }
        }
        (self.repo / ".refactor-loop/state/auto-release-signals.json").write_text(json.dumps(signals), encoding="utf-8")
        (self.repo / ".refactor-loop/state/release-commits.json").write_text(
            json.dumps({"commits": [{"sha": "abc123", "subject": "fix: release blocker", "body": ""}]}),
            encoding="utf-8",
        )
        self.release_dispatch_fix_sha = self.init_release_dispatch_git_history()

    def init_release_dispatch_git_history(self) -> str:
        def git_ok(*args: str) -> str:
            result = subprocess.run(
                ["git", "-C", str(self.repo), *args],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise AssertionError(result.stderr or result.stdout)
            return result.stdout.strip()

        git_ok("init", "-q")
        git_ok("add", ".")
        git_ok("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "Release v1.2.3-beta.4")
        (self.repo / "file.txt").write_text("fix\n", encoding="utf-8")
        git_ok("add", "file.txt")
        git_ok("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "fix: next release")
        fix_sha = git_ok("rev-parse", "HEAD")
        git_ok("branch", "dev")
        git_ok("update-ref", "refs/remotes/origin/dev", "HEAD")
        origin = self.repo.parent / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", str(origin)], capture_output=True, text=True, check=True)
        git_ok("remote", "add", "origin", str(origin))
        return fix_sha

    def batch_plan(self, actions: list[dict], *, dispatch_required: object, deficit: object, active: bool = True) -> dict:
        return {
            "schema": "wakeup-plan",
            "mode": "closed-action-projection",
            "apply_authority": "wakeup-runner-396-only",
            "no_lifecycle_authority": True,
            "concurrency": {"deficit": deficit},
            "hard_gate": {"active": active, "dispatch_required": dispatch_required},
            "actions": [self.annotate_safe_progress(action) for action in actions],
            "blocked_queue": [],
        }

    def annotate_safe_progress(self, action: dict) -> dict:
        annotated = dict(action)
        if "risk_tier" not in annotated:
            annotated["risk_tier"] = "low" if annotated.get("controller_action") == "spawn_codex_harness_background" else "medium"
        if "execution_policy" not in annotated:
            annotated["execution_policy"] = "cautious" if annotated["risk_tier"] == "medium" else "auto"
        return annotated

    def spawn_action(self, **overrides) -> dict:
        prompt = self.repo / ".refactor-loop/prompts/task.md"
        prompt.write_text("hello\n", encoding="utf-8")
        marker = "intent-marker"
        action = {
            "kind": "harness-spawn-intent",
            "action_id": "spawn:1",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "source_artifact_contains_evidence", "target_log_absent"],
            "source_artifact": ".refactor-loop/.controller-pending-events.log",
            "source_marker": marker,
            "target_kind": "codex",
            "target_number": None,
            "target": {"kind": "codex", "task_id": "task"},
            "controller_action": "spawn_codex_harness_background",
            "no_generic_command": True,
            "cd": str(self.repo),
            "prompt": str(prompt),
            "log": str(self.repo / ".refactor-loop/logs/task.log"),
            "stall": 30,
        }
        action.update(overrides)
        pending = self.repo / ".refactor-loop/.controller-pending-events.log"
        with pending.open("a", encoding="utf-8") as handle:
            handle.write(marker + "\n")
        return action

    def remote_ci_fix_action(self, **overrides) -> dict:
        action = {
            "kind": "ci-red",
            "action_id": "ci-red:77:" + "a" * 40 + ":contract-tests",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "live_open_target", "target_required_checks_red"],
            "source_artifact": "github-check-runs",
            "source_marker": "ci-red:77:" + "a" * 40 + ":contract-tests",
            "target_kind": "PR",
            "target_number": 77,
            "target": {"kind": "PR", "number": 77},
            "controller_action": "dispatch_remote_ci_fix",
            "no_generic_command": True,
            "head_sha": "a" * 40,
            "check_name": "contract-tests",
            "check_names": ["contract-tests"],
            "run_url": "https://checks/contract-tests",
        }
        action.update(overrides)
        return action

    def review_gate_action(self, **overrides) -> dict:
        marker = "REVIEW_GATE_READY:77"
        pending = self.repo / ".refactor-loop/.controller-pending-events.log"
        with pending.open("a", encoding="utf-8") as handle:
            handle.write(marker + "\n")
        action = {
            "kind": "review-gate",
            "action_id": "review-gate:77:sha",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "live_open_target"],
            "source_artifact": ".refactor-loop/.controller-pending-events.log",
            "source_marker": marker,
            "target_kind": "PR",
            "target_number": 77,
            "target": {"kind": "PR", "number": 77},
            "controller_action": "review_gate",
            "no_generic_command": True,
            "head_sha": "a" * 40,
        }
        action.update(overrides)
        return action

    def review_gate_snapshot(self, **overrides) -> dict:
        snapshot = {
            "invalid": [],
            "all_present": True,
            "approve": 1,
            "reject": 0,
            "comment": 0,
            "live_head_sha": "a" * 40,
        }
        snapshot.update(overrides)
        return snapshot

    def close_action(self, **overrides) -> dict:
        marker = "META_RESOLVED:drop:no-action"
        pending = self.repo / ".refactor-loop/.controller-pending-events.log"
        pending.write_text(marker + "\n", encoding="utf-8")
        action = {
            "kind": "completed-marker",
            "action_id": "close-managed-item:53",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "live_open_target", "live_managed_target"],
            "source_artifact": ".refactor-loop/.controller-pending-events.log",
            "source_marker": marker,
            "target_kind": "issue",
            "target_number": 53,
            "target": {"kind": "issue", "number": 53},
            "controller_action": "close_managed_item_from_drop_marker",
            "no_generic_command": True,
        }
        action.update(overrides)
        return action

    def write_zero_code_completion_artifacts(self, issue: int = 77, cluster: str | None = None) -> dict[str, str]:
        cluster_id = cluster or f"issue-{issue}"
        runs = self.repo / ".refactor-loop" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        (runs / f"phase9-issue{issue}-r1-judge.md").write_text(
            "---\ndecision: consensus\n---\n"
            "## If consensus\n"
            "- Chosen framing: delete\n"
            "- Implement plan (structured fields read by wakeup-plan from this judge artifact only, not from solver artifacts or prompt-body free text):\n"
            "  - scope_paths:\n"
            "    - none\n"
            "  - old_pattern: old\n"
            "  - new_principle: new\n"
            f"- Implementation owner: dispatch implement codex with cluster_id={cluster_id}, design_decision_path=.refactor-loop/runs/phase9-issue{issue}-r1-judge.md\n"
            "META_JUDGE_DONE:consensus:delete\n",
            encoding="utf-8",
        )
        (runs / f"implement-{cluster_id}.md").write_text(
            f"worker artifact: 0 LOC no source changes\nIMPLEMENT_DONE:issue-{issue}:ok\n",
            encoding="utf-8",
        )
        (runs / f"implementation-pr-{cluster_id}-body.md").write_text(
            "## Changed files\n\n- 0 LOC no source changes\n\n"
            "## Test results\n\n- no-op verification only\n\n"
            "## Deviations\n\n- none\n\n"
            f"Closes #{issue}\n\n"
            "⟦AI:AUTO-LOOP⟧\n",
            encoding="utf-8",
        )
        return {
            "classification": "empty_scoped_diff",
            "consensus_scope_paths": "- none",
            "implementation_artifact": f".refactor-loop/runs/implement-{cluster_id}.md",
            "body_file": f".refactor-loop/runs/implementation-pr-{cluster_id}-body.md",
        }

    def rebase_dispatch_action(self, **overrides) -> dict:
        action = {
            "kind": "stale-base-conflicting-pr",
            "action_id": "dispatch-pr-rebase-resolve:77:abc123",
            "runner_authority": "wakeup-runner-396",
            "preconditions": [
                "active_controller_owner",
                "live_open_target_if_present",
                "live_managed_target",
                "conflicting_or_dirty_mergeability",
                "base_ahead_pr_branch",
            ],
            "source_artifact": "github-managed-pr-mergeability",
            "source_marker": "CONFLICTING_PR_STALE_BASE:77:abc123",
            "target_kind": "PR",
            "target_number": 77,
            "target": {"kind": "PR", "number": 77},
            "head_ref": "refactor/iter77-worker",
            "controller_action": "dispatch_pr_rebase_resolve",
            "no_generic_command": True,
        }
        action.update(overrides)
        return action

    def rebase_commit_action(self, **overrides) -> dict:
        log = self.repo / ".refactor-loop/logs/rebase-resolve-pr77-r1.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("resolved\nREBASE_RESOLVE_DONE:77:ok\nEXIT=0\n", encoding="utf-8")
        worktree = self.repo / ".worktrees" / "iter77-worker"
        worktree.mkdir(parents=True, exist_ok=True)
        action = {
            "kind": "completed-marker",
            "action_id": "completed-marker:rebase-resolve-pr77-r1.log:REBASE_RESOLVE_DONE:77:ok",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "clean_exit_source_marker", "live_open_target_if_present"],
            "source_artifact": ".refactor-loop/logs/rebase-resolve-pr77-r1.log",
            "source_marker": "REBASE_RESOLVE_DONE:77:ok",
            "target_kind": "PR",
            "target_number": 77,
            "target": {"kind": "PR", "number": 77},
            "head_ref": "refactor/iter77-worker",
            "worktree": str(worktree),
            "controller_action": "commit_push_resolved_pr_rebase",
            "no_generic_command": True,
        }
        action.update(overrides)
        return action

    def assert_blocked_ledger(self, action_id: str, reason: str) -> None:
        rows = [
            json.loads(line)
            for line in (self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertIn(
            {"action_id": action_id, "status": "blocked", "reason": reason},
            [{"action_id": row["action_id"], "status": row["status"], "reason": row["reason"]} for row in rows],
        )

    def assert_blocked_event(self, action_id: str, reason: str) -> None:
        pending = (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn(f"WAKEUP_RUNNER_BLOCKED:{action_id}:{reason}", pending)

    def assert_blocked_before_dispatch(self, results: list, action_id: str, reason: str, actions: FakeActions) -> None:
        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, reason)
        self.assertEqual(actions.calls, [])
        self.assertEqual(self.supervisor.calls, [])
        self.assert_blocked_ledger(action_id, reason)
        self.assert_blocked_event(action_id, reason)

    def worker_output_action(self, **overrides) -> dict:
        worktree = self.repo / ".worktrees" / "pr77"
        worktree.mkdir(parents=True, exist_ok=True)
        marker = "UNPUSHED_WORKER_OUTPUT:77:2"
        action = {
            "kind": "unpushed-worker-output",
            "action_id": "unpushed-worker-output:77:local-sha",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "verified_pr_head", "clean_scoped_diff"],
            "source_artifact": str(worktree),
            "source_marker": marker,
            "target_kind": "PR",
            "target_number": 77,
            "target": {"kind": "PR", "number": 77},
            "controller_action": "safe_push",
            "no_generic_command": True,
            "head_ref": "refactor/iter77-worker",
            "worktree": str(worktree),
        }
        action.update(overrides)
        return action

    def implementation_output_action(self, **overrides) -> dict:
        worktree = self.repo / ".worktrees" / "iter77-issue-77"
        worktree.mkdir(parents=True, exist_ok=True)
        marker = "IMPLEMENT_DONE:issue-77:ok"
        log = self.repo / ".refactor-loop/logs/implement-issue77.log"
        log.write_text(f"{marker}\nEXIT=0\n", encoding="utf-8")
        runs = self.repo / ".refactor-loop" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        title = runs / "implementation-pr-issue-77-title.txt"
        body = runs / "implementation-pr-issue-77-body.md"
        title.write_text("完成 issue #77 的发布契约\n", encoding="utf-8")
        body.write_text(
            "## Changed files\n\n- skills/consensus-loop/scripts/codex_refactor_loop/wakeup_runner.py\n\n"
            "## Test results\n\n- python3 skills/consensus-loop/scripts/test_wakeup_runner.py\n\n"
            "## Deviations\n\n- none\n\n"
            "Closes #77\n\n"
            "⟦AI:AUTO-LOOP⟧\n",
            encoding="utf-8",
        )
        action = {
            "kind": "completed-marker",
            "action_id": "completed-marker:implement-issue77.log:IMPLEMENT_DONE:issue-77:ok",
            "runner_authority": "wakeup-runner-396",
            "preconditions": [
                "active_controller_owner",
                "clean_exit_source_marker",
                "canonical_implementation_identity",
                "fresh_integration_base",
                "clean_scoped_diff",
                "host_checks_green",
                "single_linked_managed_issue",
                "worker_authored_pr_artifacts",
                "no_conflicting_open_implementation_pr",
            ],
            "source_artifact": ".refactor-loop/logs/implement-issue77.log",
            "source_marker": marker,
            "target_kind": "issue",
            "target_number": 77,
            "target": {"kind": "issue", "number": 77},
            "controller_action": "publish_implementation_output",
            "no_generic_command": True,
            "head_ref": "refactor/iter77-issue-77",
            "worktree": str(worktree),
            "title_file": title.relative_to(self.repo).as_posix(),
            "body_file": body.relative_to(self.repo).as_posix(),
        }
        action.update(overrides)
        return action

    def release_rollup_action(self, **overrides) -> dict:
        body = self.repo / ".refactor-loop/runs/release-rollup-pr-body.md"
        body.write_text("## rollup\n\nbody\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        marker = 'DEV_SYNC_PENDING:release-rollup-needed:{"integration_sha":"abc123"}'
        pending = self.repo / ".refactor-loop/.controller-pending-events.log"
        pending.write_text(marker + "\n", encoding="utf-8")
        action = {
            "kind": "release-rollup-needed",
            "action_id": "release-rollup-needed:abc123",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "source_artifact_contains_evidence", "release_rollup_event"],
            "source_artifact": ".refactor-loop/.controller-pending-events.log",
            "source_marker": marker,
            "target_kind": "release-rollup",
            "target_number": None,
            "target": {"kind": "release-rollup", "integration_sha": "abc123"},
            "controller_action": "open_release_rollup_pr_from_action",
            "no_generic_command": True,
            "event": {"integration_sha": "abc123"},
            "body_file": ".refactor-loop/runs/release-rollup-pr-body.md",
        }
        action.update(overrides)
        return action

    def rollup_auto_merge_action(self, **overrides) -> dict:
        action = {
            "kind": "release-rollup-auto-merge",
            "action_id": "release-rollup-auto-merge:88:abc123",
            "runner_authority": "wakeup-runner-396",
            "preconditions": [
                "active_controller_owner",
                "live_open_target",
                "rollup_head_prefix",
                "review_base_target",
                "required_checks_green_exact_head",
                "rollup_auto_merge_enabled",
            ],
            "source_artifact": "github-open-managed-work-snapshot",
            "source_marker": "RELEASE_ROLLUP_AUTO_MERGE:88:abc123",
            "target_kind": "PR",
            "target_number": 88,
            "target": {"kind": "PR", "number": 88},
            "controller_action": "auto_merge_release_rollup_pr_from_action",
            "no_generic_command": True,
            "head_ref": "rollup/abc123",
            "head_sha": "abc123",
            "base_ref": "dev",
        }
        action.update(overrides)
        return action

    def release_rollup_body_action(self, **overrides) -> dict:
        marker = 'DEV_SYNC_PENDING:release-rollup-needed:{"integration_sha":"abc123"}'
        pending = self.repo / ".refactor-loop/.controller-pending-events.log"
        pending.write_text(marker + "\n", encoding="utf-8")
        action = {
            "kind": "release-rollup-needed",
            "action_id": "release-rollup-body:abc123",
            "runner_authority": "wakeup-runner-396",
            "preconditions": [
                "active_controller_owner",
                "source_artifact_contains_evidence",
                "release_rollup_event",
                "target_log_absent",
                "target_body_absent",
            ],
            "source_artifact": ".refactor-loop/.controller-pending-events.log",
            "source_marker": marker,
            "target_kind": "codex",
            "target_number": None,
            "target": {"kind": "codex", "task_id": "release-rollup-body"},
            "controller_action": "spawn_codex_harness_background",
            "capability": "release-rollup-body",
            "no_generic_command": True,
            "event": {"integration_sha": "abc123"},
            "body_file": ".refactor-loop/runs/release-rollup-pr-body.md",
            "cd": str(self.repo),
            "prompt": str(self.repo / ".refactor-loop/prompts/release-rollup-body.md"),
            "log": str(self.repo / ".refactor-loop/logs/release-rollup-body.log"),
            "stall": 5400,
        }
        action.update(overrides)
        return action

    def implementation_pr_artifact_repair_action(self, **overrides) -> dict:
        marker = "IMPLEMENT_DONE:issue-77:ok"
        log = self.repo / ".refactor-loop/logs/implement-issue77.log"
        log.write_text(f"{marker}\nEXIT=0\n", encoding="utf-8")
        runs = self.repo / ".refactor-loop/runs"
        runs.mkdir(parents=True, exist_ok=True)
        (runs / "implement-issue77.md").write_text("implementation summary\nIMPLEMENT_DONE:issue-77:ok\n", encoding="utf-8")
        action = {
            "kind": "harness-spawn-intent",
            "action_id": "implementation-pr-artifacts:issue-77:implementation_pr_title_artifact_missing",
            "runner_authority": "wakeup-runner-396",
            "preconditions": [
                "active_controller_owner",
                "clean_exit_source_marker",
                "target_log_absent",
                "implementation_pr_artifacts_missing_or_invalid",
                "publish_implementation_output_status_only",
            ],
            "source_artifact": ".refactor-loop/logs/implement-issue77.log",
            "source_marker": marker,
            "target_kind": "codex",
            "target_number": None,
            "target": {"kind": "codex", "task_id": "implementation-pr-artifacts-issue-77"},
            "controller_action": "spawn_codex_harness_background",
            "capability": "implementation-pr-artifact-repair",
            "no_generic_command": True,
            "issue_number": 77,
            "cluster_id": "issue-77",
            "implementation_log": ".refactor-loop/logs/implement-issue77.log",
            "implementation_summary": ".refactor-loop/runs/implement-issue77.md",
            "title_file": ".refactor-loop/runs/implementation-pr-issue-77-title.txt",
            "body_file": ".refactor-loop/runs/implementation-pr-issue-77-body.md",
            "cd": str(self.repo),
            "prompt": str(self.repo / ".refactor-loop/prompts/implementation-pr-artifacts-issue-77.md"),
            "log": str(self.repo / ".refactor-loop/logs/implementation-pr-artifacts-issue-77.log"),
            "stall": 5400,
        }
        action.update(overrides)
        return action

    def reviewer_dispatch_action(self, **overrides) -> dict:
        marker = "FIX_DONE:414:round-2:applied-1:rejected-0:blocked-0"
        log = self.repo / ".refactor-loop/logs/fix-pr77-r3.log"
        log.write_text(f"{marker}\nEXIT=0\n", encoding="utf-8")
        action = {
            "kind": "completed-marker",
            "action_id": "completed-marker:fix-pr77-r3.log:FIX_DONE:414:round-2:applied-1:rejected-0:blocked-0",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "clean_exit_source_marker", "live_open_target_if_present"],
            "source_artifact": ".refactor-loop/logs/fix-pr77-r3.log",
            "source_marker": marker,
            "target_kind": "PR",
            "target_number": 77,
            "target": {"kind": "PR", "number": 77},
            "controller_action": "dispatch_reviewers",
            "no_generic_command": True,
        }
        action.update(overrides)
        return action

    def consensus_action(self, **overrides) -> dict:
        artifact = self.repo / ".refactor-loop/runs/phase9-issue20-r5-judge.md"
        artifact.write_text(
            "## PROJECT_RULES clause violated\nold\n\n"
            "## Concrete plan\n- `skills/consensus-loop/scripts/codex_refactor_loop/wakeup_plan.py`: scope.\n\n"
            "META_JUDGE_DONE:consensus:structural\n",
            encoding="utf-8",
        )
        marker = "META_JUDGE_DONE:consensus:structural"
        log = self.repo / ".refactor-loop/logs/phase9-issue20-r5-judge.log"
        log.write_text(f"{marker}\nEXIT=0\n", encoding="utf-8")
        action = {
            "kind": "completed-marker",
            "action_id": "completed-marker:phase9-issue20-r5-judge.log:META_JUDGE_DONE:consensus:structural",
            "runner_authority": "wakeup-runner-396",
            "preconditions": [
                "active_controller_owner",
                "clean_exit_source_marker",
                "live_open_target_if_present",
                "durable_consensus_artifact",
                "consensus_implementation_ready",
            ],
            "source_artifact": ".refactor-loop/logs/phase9-issue20-r5-judge.log",
            "source_marker": marker,
            "target_kind": "issue",
            "target_number": 20,
            "target": {"kind": "issue", "number": 20},
            "controller_action": "dispatch_consensus_implementation",
            "no_generic_command": True,
            "consensus_artifact": ".refactor-loop/runs/phase9-issue20-r5-judge.md",
            "design_decision_path": ".refactor-loop/runs/phase9-issue20-r5-judge.md",
            "consensus_issue": 20,
            "consensus_round": 5,
            "cluster_id": "issue-20",
            "iteration": "20",
            "scope_paths": "- skills/consensus-loop/scripts/codex_refactor_loop/wakeup_plan.py",
            "old_pattern": "old",
            "new_principle": "new",
        }
        action.update(overrides)
        return action

    def issue_decomposition_action(self, **overrides) -> dict:
        consensus = ".refactor-loop/runs/phase9-issue403-r6-judge.md"
        (self.repo / consensus).write_text(
            "---\nissue: 403\nconvergence_round: 6\ndecision: consensus\n---\n\n"
            "## If consensus\n"
            "META_JUDGE_DONE:consensus:decompose\n",
            encoding="utf-8",
        )

        def write_child(name: str, scope: str, non_goals: str) -> str:
            path = f".refactor-loop/runs/{name}.md"
            (self.repo / path).write_text(
                "## child\n\n"
                "Parent issue: #403\n"
                f"Source consensus artifact: {Path(consensus).name}\n"
                f"Scope: {scope}\n"
                f"Non-goals: {non_goals}\n\n"
                "<details>\n<summary>内联 artifact 1: decision.md</summary>\n\n"
                "```markdown\nraw decision\n```\n\n</details>\n\n"
                "⟦AI:AUTO-LOOP⟧\n",
                encoding="utf-8",
            )
            return path

        parent_comment = ".refactor-loop/runs/decompose-parent-comment.md"
        (self.repo / parent_comment).write_text("Parent issue: #403\n\nChildren opened.\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        plan_path = ".refactor-loop/runs/decomposition-plan.json"
        (self.repo / plan_path).write_text(
            json.dumps(
                {
                    "schema": "IssueDecompositionPlan",
                    "parent_issue": 403,
                    "source_consensus_artifact": consensus,
                    "children": [
                        {
                            "slug": "first-child",
                            "title": "First child",
                            "scope": "First bounded scope",
                            "non_goals": "No parent lifecycle mutation",
                            "body_artifact_path": write_child("child-one", "First bounded scope", "No parent lifecycle mutation"),
                        },
                        {
                            "slug": "second-child",
                            "title": "Second child",
                            "scope": "Second bounded scope",
                            "non_goals": "No public issue factory",
                            "body_artifact_path": write_child("child-two", "Second bounded scope", "No public issue factory"),
                        },
                    ],
                    "parent_update": {"comment_artifact_path": parent_comment},
                }
            ),
            encoding="utf-8",
        )
        digest = issue_decomposition_plan_file_digest(self.ctx, plan_path)
        marker = "META_JUDGE_DONE:consensus:decompose"
        log = self.repo / ".refactor-loop/logs/phase9-issue403-r6-judge.log"
        log.write_text(f"{marker}\nEXIT=0\n", encoding="utf-8")
        action = {
            "kind": "completed-marker",
            "action_id": "completed-marker:phase9-issue403-r6-judge.log:META_JUDGE_DONE:consensus:decompose",
            "runner_authority": "wakeup-runner-396",
            "preconditions": [
                "active_controller_owner",
                "clean_exit_source_marker",
                "durable_consensus_artifact",
                "plan_level_design_consensus_judge_artifact",
                "issue_decomposition_plan_digest_match",
                "live_parent_open_tracking",
                "github_sentinel_idempotency_owner",
            ],
            "source_artifact": ".refactor-loop/logs/phase9-issue403-r6-judge.log",
            "source_marker": marker,
            "target_kind": "issue",
            "target_number": 403,
            "target": {"kind": "issue", "number": 403},
            "controller_action": "apply_issue_decomposition_plan",
            "no_generic_command": True,
            "consensus_artifact": consensus,
            "consensus_issue": 403,
            "consensus_round": 6,
            "plan_level_design_consensus_judge_artifact": consensus,
            "issue_decomposition_plan_path": plan_path,
            "issue_decomposition_plan_digest": digest,
            "issue_decomposition_proof": f"plan-level judge {consensus} validated plan {plan_path} digest {digest} reached consensus",
        }
        action.update(overrides)
        return action

    def design_consensus_spawn_action(self, **overrides) -> dict:
        prompt = self.repo / ".refactor-loop/prompts/phase9/phase9-issue104-r2-judge.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("design consensus\n", encoding="utf-8")
        pending = self.repo / ".refactor-loop/.controller-pending-events.log"
        marker = "HARNESS_SPAWN_INTENT phase9-issue104-r2-judge"
        pending.write_text(marker + "\n", encoding="utf-8")
        action = {
            "kind": "harness-spawn-intent",
            "action_id": "harness-spawn-intent:phase9-router:104:2:judge",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "source_artifact_contains_evidence", "target_log_absent"],
            "source_artifact": ".refactor-loop/.controller-pending-events.log",
            "source_marker": marker,
            "target_kind": "codex",
            "target_number": None,
            "target": {"kind": "codex", "task_id": "phase9-issue104-r2-judge"},
            "controller_action": "spawn_codex_harness_background",
            "no_generic_command": True,
            "cd": str(self.repo),
            "prompt": str(prompt),
            "log": str(self.repo / ".refactor-loop/logs/phase9-issue104-r2-judge.log"),
            "stall": 30,
        }
        action.update(overrides)
        return action

    def test_valid_harness_spawn_executes_through_checked_supervisor(self) -> None:
        with mock.patch("codex_refactor_loop.processes.subprocess.Popen") as popen:
            results = self.run_result(self.base_plan(self.spawn_action()))

        self.assertEqual(results[0].status, "applied")
        popen.assert_called_once()
        args, kwargs = popen.call_args
        command = args[0]
        self.assertIn("spawn-codex", command)
        self.assertEqual(command[0], str((self.expected_skill_root / "scripts" / "consensus-rnd-cli").resolve()))
        self.assertEqual(kwargs["cwd"], str(self.repo.resolve()))
        self.assertEqual(command[command.index("--cd") + 1], str(self.repo))
        self.assertEqual(command[command.index("--prompt") + 1], str(self.repo / ".refactor-loop/prompts/task.md"))
        self.assertEqual(command[command.index("--log") + 1], str(self.repo / ".refactor-loop/logs/task.log"))
        self.assertEqual(command[command.index("--stall") + 1], "30")
        self.assertTrue(kwargs["start_new_session"])
        popen.return_value.wait.assert_not_called()
        popen.return_value.poll.assert_not_called()

    def test_wakeup_runner_batches_spawn_actions_up_to_hard_gate_dispatch_required(self) -> None:
        actions = [self.spawn_action(action_id=f"spawn:{index}", log=str(self.repo / f".refactor-loop/logs/task-{index}.log")) for index in range(3)]

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.batch_plan(actions, dispatch_required=3, deficit=5))

        self.assertEqual([result.status for result in results], ["applied", "applied", "applied"])
        self.assertEqual([result.action_id for result in results], ["spawn:0", "spawn:1", "spawn:2"])
        self.assertEqual(launch.call_count, 3)

    def test_wakeup_runner_spawn_batch_uses_projected_dispatch_required(self) -> None:
        actions = [self.spawn_action(action_id=f"spawn:{index}", log=str(self.repo / f".refactor-loop/logs/task-{index}.log")) for index in range(4)]

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.batch_plan(actions, dispatch_required=2, deficit=4))

        self.assertEqual([result.action_id for result in results], ["spawn:0", "spawn:1"])
        self.assertEqual([result.status for result in results], ["applied", "applied"])
        self.assertEqual(launch.call_count, 2)

    def test_wakeup_runner_covered_transient_supply_keeps_single_apply_compatibility(self) -> None:
        actions = [self.spawn_action(action_id=f"spawn:{index}", log=str(self.repo / f".refactor-loop/logs/task-{index}.log")) for index in range(3)]
        plan = self.batch_plan(actions, dispatch_required=0, deficit=2, active=False)
        plan["concurrency"]["transient_supply"] = {"supply": 2}

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(plan)

        self.assertEqual([result.action_id for result in results], ["spawn:0"])
        self.assertEqual([result.status for result in results], ["applied"])
        self.assertEqual(launch.call_count, 1)

    def test_wakeup_runner_spawn_batch_uses_deficit_as_upper_bound(self) -> None:
        actions = [self.spawn_action(action_id=f"spawn:{index}", log=str(self.repo / f".refactor-loop/logs/task-{index}.log")) for index in range(3)]

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.batch_plan(actions, dispatch_required=5, deficit=2))

        self.assertEqual([result.action_id for result in results], ["spawn:0", "spawn:1"])
        self.assertEqual(launch.call_count, 2)

    def test_wakeup_runner_headless_harness_spawn_intents_launch_to_deficit(self) -> None:
        actions = [
            self.design_consensus_spawn_action(
                action_id=f"harness-spawn-intent:phase9-router:{issue}:1:minimal",
                target={"kind": "codex", "task_id": f"phase9-issue{issue}-r1-minimal"},
                prompt=str(self.repo / ".refactor-loop/prompts/phase9/phase9-issue104-r2-judge.md"),
                log=str(self.repo / f".refactor-loop/logs/phase9-issue{issue}-r1-minimal.log"),
            )
            for issue in (104, 105, 106)
        ]

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.batch_plan(actions, dispatch_required=2, deficit=2), gh_state="OPEN", actions=FakeActions())

        self.assertEqual([result.status for result in results], ["applied", "applied"])
        self.assertEqual([result.action_id for result in results], [actions[0]["action_id"], actions[1]["action_id"]])
        self.assertEqual(launch.call_count, 2)

    def test_wakeup_runner_prioritizes_reviewer_spawn_intent_over_stale_fallback_spawn(self) -> None:
        fallback = self.spawn_action(
            action_id="audit-fallback:audit-iter-9",
            route="audit-fallback",
            intent_id="audit-fallback:audit-iter-9",
            log=str(self.repo / ".refactor-loop/logs/audit-iter-9.log"),
        )
        reviewer = self.spawn_action(
            action_id="harness-spawn-intent:dispatch-reviewers:632:{architect,tests,quality}:r1",
            route="dispatch-reviewers",
            intent_id="dispatch-reviewers:632:{architect,tests,quality}:r1",
            target={"kind": "codex", "task_id": "review-pr632-architect-r1"},
            prompt=str(self.repo / ".refactor-loop/prompts/review-pr632-architect-r1.md"),
            log=str(self.repo / ".refactor-loop/logs/review-pr632-architect-r1.log"),
        )

        Path(reviewer["prompt"]).write_text("review PR 632\n", encoding="utf-8")
        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.batch_plan([fallback, reviewer], dispatch_required=1, deficit=1))

        self.assertEqual([result.action_id for result in results], [reviewer["action_id"]])
        self.assertEqual(results[0].status, "applied")
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(str(launch.call_args.kwargs["log"]), reviewer["log"])

    def test_wakeup_runner_stale_applied_spawn_ledger_retries_headless_intent(self) -> None:
        action = self.design_consensus_spawn_action(action_id="harness-spawn-intent:phase9-router:104:1:minimal-retry")
        ledger = self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl"
        ledger.write_text(
            json.dumps({"action_id": action["action_id"], "status": "applied", "reason": "", "kind": "harness-spawn-intent"})
            + "\n",
            encoding="utf-8",
        )

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.base_plan(action), gh_state="OPEN", actions=FakeActions())

        self.assertEqual(results[0].status, "applied")
        launch.assert_called_once()
        pending = (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("WAKEUP_RUNNER_STALE_SPAWN_LEDGER:harness-spawn-intent:phase9-router:104:1:minimal-retry:target-log-absent", pending)

    def test_wakeup_runner_stale_applied_spawn_ledger_does_not_retry_clean_markerless_phase9_solver(self) -> None:
        log = self.repo / ".refactor-loop/logs/phase9-issue659-r2-minimal.log"
        log.write_text("clean markerless solver output\nEXIT=0\n", encoding="utf-8")
        action = self.design_consensus_spawn_action(
            action_id="harness-spawn-intent:phase9-router:659:2:minimal",
            log=str(log),
        )
        ledger = self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl"
        ledger.write_text(
            json.dumps({"action_id": action["action_id"], "status": "applied", "reason": "", "kind": "harness-spawn-intent"})
            + "\n",
            encoding="utf-8",
        )

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.base_plan(action), gh_state="OPEN", actions=FakeActions())

        self.assertEqual(results[0].status, "skipped")
        self.assertEqual(results[0].reason, "duplicate")
        launch.assert_not_called()
        pending = self.repo / ".refactor-loop/.controller-pending-events.log"
        pending_text = pending.read_text(encoding="utf-8") if pending.exists() else ""
        self.assertNotIn("WAKEUP_RUNNER_STALE_SPAWN_LEDGER", pending_text)

    def test_wakeup_runner_stale_applied_spawn_ledger_retries_failed_implement_log(self) -> None:
        log = self.repo / ".refactor-loop/logs/implement-issue-537.log"
        log.write_text("Error: No such file or directory (os error 2)\nEXIT=1\n", encoding="utf-8")
        action = self.spawn_action(
            action_id="harness-spawn-intent:dispatch-consensus-implementation:537",
            target={"kind": "codex", "task_id": "implement-issue-537"},
            log=str(log),
        )
        ledger = self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl"
        ledger.write_text(
            json.dumps({"action_id": action["action_id"], "status": "applied", "reason": "", "kind": "harness-spawn-intent"})
            + "\n",
            encoding="utf-8",
        )

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.base_plan(action), gh_state="OPEN", actions=FakeActions())

        self.assertEqual(results[0].status, "applied")
        self.assertFalse(log.exists())
        launch.assert_called_once()
        pending = (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn(
            "WAKEUP_RUNNER_STALE_SPAWN_LEDGER:harness-spawn-intent:dispatch-consensus-implementation:537:target-log-redispatchable:nonzero_exit",
            pending,
        )

    def test_wakeup_runner_stale_applied_spawn_ledger_does_not_retry_terminal_blocked_implement(self) -> None:
        log = self.repo / ".refactor-loop/logs/implement-issue-537.log"
        log.write_text("IMPLEMENT_DONE:issue-537:blocked\nEXIT=0\n", encoding="utf-8")
        action = self.spawn_action(
            action_id="harness-spawn-intent:dispatch-consensus-implementation:537",
            target={"kind": "codex", "task_id": "implement-issue-537"},
            log=str(log),
        )
        ledger = self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl"
        ledger.write_text(
            json.dumps({"action_id": action["action_id"], "status": "applied", "reason": "", "kind": "harness-spawn-intent"})
            + "\n",
            encoding="utf-8",
        )

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.base_plan(action), gh_state="OPEN", actions=FakeActions())

        self.assertEqual(results[0].status, "skipped")
        self.assertEqual(results[0].reason, "duplicate")
        self.assertTrue(log.exists())
        launch.assert_not_called()
        pending = (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8")
        self.assertNotIn(
            "WAKEUP_RUNNER_STALE_SPAWN_LEDGER:harness-spawn-intent:dispatch-consensus-implementation:537",
            pending,
        )

    def test_wakeup_runner_stale_applied_spawn_ledger_does_not_retry_empty_scoped_diff_implement(self) -> None:
        worktree = self.repo / ".worktrees" / "iter581-issue-581"
        worktree.mkdir(parents=True)
        log = self.repo / ".refactor-loop/logs/implement-issue-581.log"
        log.write_text("no code changes required\nIMPLEMENT_DONE:issue-581:ok\nEXIT=0\n", encoding="utf-8")
        action = self.spawn_action(
            action_id="harness-spawn-intent:dispatch-consensus-implementation:581",
            target={"kind": "codex", "task_id": "implement-issue-581"},
            log=str(log),
        )
        ledger = self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl"
        ledger.write_text(
            json.dumps({"action_id": action["action_id"], "status": "applied", "reason": "", "kind": "harness-spawn-intent"})
            + "\n",
            encoding="utf-8",
        )

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(
                self.base_plan(action),
                gh_state="OPEN",
                git_diff_code=0,
                implementation_status="",
                implementation_issue=581,
                actions=FakeActions(),
            )

        self.assertEqual(results[0].status, "skipped")
        self.assertEqual(results[0].reason, "duplicate")
        self.assertTrue(log.exists())
        launch.assert_not_called()
        pending = (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8")
        self.assertNotIn(
            "WAKEUP_RUNNER_STALE_SPAWN_LEDGER:harness-spawn-intent:dispatch-consensus-implementation:581",
            pending,
        )

    def test_wakeup_runner_spawn_duplicate_does_not_block_later_spawn_batch(self) -> None:
        duplicate = self.spawn_action(action_id="spawn:duplicate", log=str(self.repo / ".refactor-loop/logs/duplicate.log"))
        later = self.spawn_action(action_id="spawn:later", log=str(self.repo / ".refactor-loop/logs/later.log"))
        Path(duplicate["log"]).write_text("SPAWN\n", encoding="utf-8")
        ledger = self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl"
        ledger.write_text(
            json.dumps({"action_id": duplicate["action_id"], "status": "applied", "reason": "", "kind": "harness-spawn-intent"})
            + "\n",
            encoding="utf-8",
        )

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.batch_plan([duplicate, later], dispatch_required=2, deficit=2))

        self.assertEqual([result.status for result in results], ["skipped", "applied"])
        self.assertEqual([result.action_id for result in results], ["spawn:duplicate", "spawn:later"])
        launch.assert_called_once()

    def test_unsupported_applied_ledger_row_does_not_suppress_retry(self) -> None:
        action = self.worker_output_action(action_id="safe-push:stale-applied-ledger")
        ledger = self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl"
        ledger.write_text(
            json.dumps({"action_id": action["action_id"], "status": "applied", "reason": "", "kind": "unpushed-worker-output"})
            + "\n",
            encoding="utf-8",
        )
        actions = FakeActions()

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual([call[0] for call in actions.calls], ["safe_push"])

    def test_consensus_implementation_applied_row_retries_without_dispatch_effect(self) -> None:
        action = self.consensus_action(action_id="consensus-implementation:stale-applied")
        (self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl").write_text(
            json.dumps({"action_id": action["action_id"], "status": "applied", "reason": "", "kind": "completed-marker"})
            + "\n",
            encoding="utf-8",
        )
        actions = FakeActions()

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual([call[0] for call in actions.calls], ["dispatch_consensus_implementation"])

    def test_consensus_implementation_success_records_dispatch_receipt_for_next_tick(self) -> None:
        action = self.consensus_action(action_id="consensus-implementation:receipt-written")
        actions = FakeActions()

        first = self.run_result(self.base_plan(action), actions=actions)
        second_actions = FakeActions()
        second = self.run_result(self.base_plan(action), actions=second_actions)

        receipt = "WAKEUP_RUNNER_CONSENSUS_IMPLEMENTATION_DISPATCHED:20:issue-20:consensus-implementation:receipt-written"
        pending = (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8")
        self.assertEqual(first[0].status, "applied")
        self.assertIn(receipt, pending.splitlines())
        self.assertEqual([call[0] for call in actions.calls], ["dispatch_consensus_implementation"])
        self.assertEqual(second[0].status, "skipped")
        self.assertEqual(second[0].reason, "duplicate")
        self.assertEqual(second_actions.calls, [])

    def test_consensus_implementation_applied_row_suppresses_after_dispatch_receipt(self) -> None:
        action = self.consensus_action(action_id="consensus-implementation:receipt")
        (self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl").write_text(
            json.dumps({"action_id": action["action_id"], "status": "applied", "reason": "", "kind": "completed-marker"})
            + "\n",
            encoding="utf-8",
        )
        (self.repo / ".refactor-loop/.controller-pending-events.log").write_text(
            "WAKEUP_RUNNER_CONSENSUS_IMPLEMENTATION_DISPATCHED:20:issue-20:consensus-implementation:receipt\n",
            encoding="utf-8",
        )
        actions = FakeActions()

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual(results[0].status, "skipped")
        self.assertEqual(results[0].reason, "duplicate")
        self.assertEqual(actions.calls, [])

    def test_wakeup_runner_missing_or_invalid_budget_keeps_single_apply_compatibility(self) -> None:
        cases = (
            ("missing", self.base_plan),
            ("inactive", lambda actions: self.batch_plan(actions, dispatch_required=2, deficit=2, active=False)),
            ("zero", lambda actions: self.batch_plan(actions, dispatch_required=0, deficit=2)),
            ("non-int", lambda actions: self.batch_plan(actions, dispatch_required="2", deficit=2)),
            ("missing-deficit", lambda actions: self.batch_plan(actions, dispatch_required=2, deficit=None)),
        )
        for name, plan_factory in cases:
            with self.subTest(name=name):
                actions = [
                    self.spawn_action(action_id=f"{name}:spawn:0", log=str(self.repo / f".refactor-loop/logs/{name}-0.log")),
                    self.spawn_action(action_id=f"{name}:spawn:1", log=str(self.repo / f".refactor-loop/logs/{name}-1.log")),
                ]
                plan = plan_factory(actions) if name != "missing" else self.base_plan(actions[0])
                if name == "missing":
                    plan["actions"] = actions
                with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
                    results = self.run_result(plan)

                self.assertEqual([result.action_id for result in results], [actions[0]["action_id"]])
                self.assertEqual(launch.call_count, 1)

    def test_wakeup_runner_non_spawn_action_does_not_consume_batch_budget(self) -> None:
        first = self.close_action(action_id="close-managed-item:53:first")
        second = self.close_action(action_id="close-managed-item:53:second")
        actions = FakeActions()

        results = self.run_result(self.batch_plan([first, second], dispatch_required=3, deficit=3), actions=actions)

        self.assertEqual([result.action_id for result in results], ["close-managed-item:53:first"])
        self.assertEqual([result.status for result in results], ["applied"])
        self.assertEqual([call[0] for call in actions.calls], ["close_managed_item_from_drop_marker"])

    def test_wakeup_runner_routes_dispatch_pr_rebase_resolve_action(self) -> None:
        action = self.rebase_dispatch_action()
        actions = FakeActions()
        results = self.run_result(self.base_plan(action), gh_state="OPEN", actions=actions)
        self.assertEqual([result.status for result in results], ["applied"])
        self.assertEqual([call[0] for call in actions.calls], ["dispatch_pr_rebase_resolve"])

    def test_wakeup_runner_routes_false_done_rebase_recovery_dispatch(self) -> None:
        log = self.repo / ".refactor-loop/logs/rebase-resolve-pr77-r1.log"
        log.write_text("resolved\nREBASE_RESOLVE_DONE:77:ok\nEXIT=0\n", encoding="utf-8")
        action = self.rebase_dispatch_action(
            action_id="dispatch-pr-rebase-resolve:false-done:77:refactor/iter77-worker:1",
            source_artifact=".refactor-loop/logs/rebase-resolve-pr77-r1.log",
            source_marker="REBASE_RESOLVE_DONE:77:ok",
            reason="rebase_resolve_done_without_resolved_merge",
            preconditions=[
                "active_controller_owner",
                "clean_exit_source_marker",
                "live_managed_target",
                "false_done_unresolved_or_not_commit_ready",
            ],
        )
        actions = FakeActions()
        results = self.run_result(self.base_plan(action), gh_state="OPEN", actions=actions)
        self.assertEqual([result.status for result in results], ["applied"])
        self.assertEqual([call[0] for call in actions.calls], ["dispatch_pr_rebase_resolve"])
        self.assertNotIn("mode", actions.calls[0][1])

    def test_hard_gate_dispatch_pr_rebase_resolve_is_not_starved_by_spawn_budget(self) -> None:
        spawns = [
            self.spawn_action(
                action_id=f"spawn:publish-implementation-fallback:{index}",
                route="publish-implementation-fallback",
                log=str(self.repo / f".refactor-loop/logs/publish-implementation-fallback-{index}.log"),
            )
            for index in range(3)
        ]
        rebase = self.rebase_dispatch_action(action_id="dispatch-pr-rebase-resolve:77:priority-current")
        actions = FakeActions()

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(
                self.batch_plan([*spawns, rebase], dispatch_required=2, deficit=2),
                gh_state="OPEN",
                actions=actions,
            )

        self.assertEqual([result.action_id for result in results[:1]], [rebase["action_id"]])
        self.assertEqual(results[0].status, "applied")
        self.assertEqual([call[0] for call in actions.calls], ["dispatch_pr_rebase_resolve"])
        self.assertEqual(launch.call_count, 2)

    def test_hard_gate_publishes_dirty_review_fix_before_same_pr_rebase_resolve(self) -> None:
        worktree = self.repo / ".worktrees" / "iter77-worker"
        worktree.mkdir(parents=True, exist_ok=True)
        seen: list[list[str]] = []

        def command_runner(command):
            cmd = [str(part) for part in command]
            seen.append(cmd)
            if cmd[:2] == ["gh", "api"]:
                endpoint = cmd[2] if len(cmd) > 2 else ""
                if "/pulls/" in endpoint or "/issues/" in endpoint:
                    return subprocess.CompletedProcess(cmd, 0, json.dumps({"state": "open"}), "")
                return subprocess.CompletedProcess(cmd, 0, "{}", "")
            if cmd[:3] == ["gh", "pr", "view"]:
                if "labels,body" in cmd:
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        json.dumps({"labels": [{"name": labels.MANAGED}], "body": ""}),
                        "",
                    )
                if "headRefName" in cmd:
                    if "--jq" in cmd:
                        return subprocess.CompletedProcess(cmd, 0, "refactor/iter77-worker\n", "")
                    return subprocess.CompletedProcess(cmd, 0, json.dumps({"headRefName": "refactor/iter77-worker"}), "")
                return subprocess.CompletedProcess(cmd, 0, "OPEN\n", "")
            if (
                len(cmd) >= 6
                and cmd[:2] == ["git", "-C"]
                and Path(cmd[2]).resolve() == self.repo.resolve()
                and cmd[3:] == ["worktree", "list", "--porcelain"]
            ):
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    f"worktree {self.repo}\nbranch refs/heads/dev\n\n"
                    f"worktree {worktree}\nbranch refs/heads/refactor/iter77-worker\n\n",
                    "",
                )
            if (
                len(cmd) >= 5
                and cmd[:2] == ["git", "-C"]
                and Path(cmd[2]).resolve() == worktree.resolve()
                and cmd[3:] == ["status", "--porcelain"]
            ):
                return subprocess.CompletedProcess(cmd, 0, " M skills/x.py\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        actions = FakeActions()
        runner = WakeupRunner(
            self.ctx,
            plan_loader=lambda _repo: self.batch_plan(
                [
                    self.rebase_dispatch_action(),
                    self.reviewer_dispatch_action(
                        action_id="publish-review-fix-output:77",
                        controller_action="publish_review_fix_output_from_action",
                    ),
                ],
                dispatch_required=2,
                deficit=2,
            ),
            actions=actions,
            supervisor=self.supervisor,
            command_runner=command_runner,
        )

        results = runner.run_once()

        self.assertEqual(
            [result.action_id for result in results],
            ["publish-review-fix-output:77"],
        )
        self.assertEqual([result.status for result in results], ["applied"])
        self.assertEqual(
            [call[0] for call in actions.calls],
            ["safe_push", "dispatch_reviewers"],
        )
        git_subcmds = [
            cmd[3]
            for cmd in seen
            if len(cmd) > 3 and cmd[:2] == ["git", "-C"] and Path(cmd[2]).resolve() == worktree.resolve()
        ]
        self.assertIn("commit", git_subcmds)

    def test_wakeup_runner_routes_commit_push_resolved_pr_rebase_action(self) -> None:
        action = self.rebase_commit_action()
        actions = FakeActions()
        results = self.run_result(self.base_plan(action), gh_state="OPEN", actions=actions)
        self.assertEqual([result.status for result in results], ["applied"])
        self.assertEqual([call[0] for call in actions.calls], ["commit_push_resolved_pr_rebase"])

    def test_wakeup_runner_blocks_stale_base_dispatch_without_base_ahead_precondition(self) -> None:
        action = self.rebase_dispatch_action(
            action_id="dispatch-pr-rebase-resolve:77:abc123",
            preconditions=["active_controller_owner", "live_managed_target"],
        )
        actions = FakeActions()
        results = self.run_result(self.base_plan(action), gh_state="OPEN", actions=actions)
        self.assertEqual([result.status for result in results], ["blocked"])
        self.assertEqual("dispatch_pr_rebase_resolve_missing_precondition:base_ahead_pr_branch", results[0].reason)
        self.assertEqual([], actions.calls)

    def test_wakeup_runner_reports_missing_false_done_recovery_guard_for_recovery_shape(self) -> None:
        log = self.repo / ".refactor-loop/logs/rebase-resolve-pr77-r1.log"
        log.write_text("resolved\nREBASE_RESOLVE_DONE:77:ok\nEXIT=0\n", encoding="utf-8")
        action = self.rebase_dispatch_action(
            action_id="dispatch-pr-rebase-resolve:false-done:77:refactor/iter77-worker:1",
            source_artifact=".refactor-loop/logs/rebase-resolve-pr77-r1.log",
            source_marker="REBASE_RESOLVE_DONE:77:ok",
            preconditions=[
                "active_controller_owner",
                "clean_exit_source_marker",
                "live_managed_target",
                "base_ahead_pr_branch",
            ],
        )
        actions = FakeActions()
        results = self.run_result(self.base_plan(action), gh_state="OPEN", actions=actions)
        self.assertEqual([result.status for result in results], ["blocked"])
        self.assertEqual(
            "dispatch_pr_rebase_resolve_missing_precondition:false_done_unresolved_or_not_commit_ready",
            results[0].reason,
        )
        self.assertEqual([], actions.calls)

    def test_wakeup_runner_blocked_lifecycle_action_does_not_dead_stop_later_spawn_batch(self) -> None:
        blocked = self.implementation_output_action(
            action_id="publish-implementation:missing-verified-head-before-spawn",
            preconditions=[
                "active_controller_owner",
                "clean_exit_source_marker",
                "clean_scoped_diff",
                "host_checks_green",
                "single_linked_managed_issue",
                "no_conflicting_open_implementation_pr",
            ],
        )
        later = self.spawn_action(action_id="spawn:after-blocked-lifecycle")
        actions = FakeActions()

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.batch_plan([blocked, later], dispatch_required=2, deficit=2), actions=actions)

        self.assertEqual(
            [(result.action_id, result.status, result.reason) for result in results],
            [
                (
                    "publish-implementation:missing-verified-head-before-spawn",
                    "blocked",
                    "publish_implementation_missing_precondition:canonical_implementation_identity",
                ),
                ("spawn:after-blocked-lifecycle", "applied", ""),
            ],
        )
        self.assertEqual(actions.calls, [])
        launch.assert_called_once()
        self.assert_blocked_event(
            "publish-implementation:missing-verified-head-before-spawn",
            "publish_implementation_missing_precondition:canonical_implementation_identity",
        )

    def test_wakeup_runner_hard_gate_scans_past_noop_prefix_to_spawn_batch(self) -> None:
        status_only_prefix = [
            {
                "kind": "completed-marker",
                "action_id": f"completed-marker:old-{index}",
                "status_only": True,
                "no_lifecycle_authority": True,
            }
            for index in range(20)
        ]
        duplicate = self.reviewer_dispatch_action(action_id="completed-marker:duplicate-review")
        applied_lifecycle = self.implementation_output_action(action_id="completed-marker:implement-issue77-before-spawn")
        spawn_batch = [
            self.design_consensus_spawn_action(
                action_id=f"harness-spawn-intent:phase9-router:{issue}:2:minimal",
                target={"kind": "codex", "task_id": f"phase9-issue{issue}-r2-minimal"},
                prompt=str(self.repo / ".refactor-loop/prompts/phase9/phase9-issue104-r2-judge.md"),
                log=str(self.repo / f".refactor-loop/logs/phase9-issue{issue}-r2-minimal.log"),
            )
            for issue in (582, 583, 584)
        ]
        actions = FakeActions()
        ledger = self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl"
        ledger.write_text(
            json.dumps({"action_id": duplicate["action_id"], "status": "applied", "reason": "", "kind": "completed-marker"})
            + "\n",
            encoding="utf-8",
        )

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(
                self.batch_plan([*status_only_prefix, duplicate, applied_lifecycle, *spawn_batch], dispatch_required=3, deficit=3),
                actions=actions,
                git_diff_code=1,
            )

        self.assertEqual(
            [(result.action_id, result.status, result.reason) for result in results],
            [
                ("completed-marker:duplicate-review", "skipped", "duplicate"),
                ("completed-marker:implement-issue77-before-spawn", "applied", ""),
                (spawn_batch[0]["action_id"], "applied", ""),
                (spawn_batch[1]["action_id"], "applied", ""),
                (spawn_batch[2]["action_id"], "applied", ""),
            ],
        )
        self.assertEqual([call[0] for call in actions.calls], ["publish_implementation_output"])
        self.assertEqual(launch.call_count, 3)

    def test_wakeup_runner_rejects_design_consensus_redispatch_actions(self) -> None:
        issue = 496
        (self.repo / f".refactor-loop/logs/phase9-issue{issue}-r1-delete.log").write_text(
            "SOLVER_DONE:delete:abstain:genuine-gap\nEXIT=0\n", encoding="utf-8"
        )
        action = {
            "kind": "completed-marker",
            "action_id": f"completed-marker:phase9-issue{issue}-r1-delete.log:SOLVER_DONE:delete:abstain:genuine-gap",
            "runner_authority": "wakeup-runner-396",
            "preconditions": ["active_controller_owner", "clean_exit_source_marker", "live_open_target"],
            "source_artifact": f".refactor-loop/logs/phase9-issue{issue}-r1-delete.log",
            "source_marker": "SOLVER_DONE:delete:abstain:genuine-gap",
            "target_kind": "issue",
            "target_number": issue,
            "target": {"kind": "issue", "number": issue},
            "controller_action": "dispatch_design_consensus",
            "no_generic_command": True,
        }
        actions = FakeActions()

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(
                self.batch_plan([action], dispatch_required=1, deficit=1),
                gh_state="OPEN",
                actions=actions,
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].action_id, action["action_id"])
        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "unsupported_controller_action:dispatch_design_consensus")
        launch.assert_not_called()

    def test_wakeup_runner_blocked_non_spawn_actions_do_not_dead_stop_spawn_batch(self) -> None:
        blocked_publish = self.implementation_output_action(
            action_id="publish-implementation:missing-verified-head-before-spawns",
            preconditions=[
                "active_controller_owner",
                "clean_exit_source_marker",
                "clean_scoped_diff",
                "host_checks_green",
                "single_linked_managed_issue",
                "no_conflicting_open_implementation_pr",
            ],
        )
        spawns = [
            self.spawn_action(
                action_id=f"spawn:after-blockers:{index}",
                log=str(self.repo / f".refactor-loop/logs/after-blockers-{index}.log"),
            )
            for index in range(3)
        ]
        blocked_close = self.close_action(
            action_id="close-managed-item:53:closed-before-spawns",
            preconditions=["active_controller_owner", "live_managed_target"],
        )
        (self.repo / ".refactor-loop/.controller-pending-events.log").write_text(
            "META_RESOLVED:drop:no-action\nintent-marker\n",
            encoding="utf-8",
        )
        actions = FakeActions()

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(
                self.batch_plan([blocked_publish, blocked_close, *spawns], dispatch_required=3, deficit=3),
                actions=actions,
            )

        self.assertEqual(
            [(result.action_id, result.status, result.reason) for result in results],
            [
                (
                    "publish-implementation:missing-verified-head-before-spawns",
                    "blocked",
                    "publish_implementation_missing_precondition:canonical_implementation_identity",
                ),
                (
                    "close-managed-item:53:closed-before-spawns",
                    "blocked",
                    "close_managed_drop_missing_precondition:live_open_target",
                ),
                ("spawn:after-blockers:0", "applied", ""),
                ("spawn:after-blockers:1", "applied", ""),
                ("spawn:after-blockers:2", "applied", ""),
            ],
        )
        self.assertEqual(actions.calls, [])
        self.assertEqual(launch.call_count, 3)
        self.assert_blocked_event(
            "publish-implementation:missing-verified-head-before-spawns",
            "publish_implementation_missing_precondition:canonical_implementation_identity",
        )
        self.assert_blocked_event(
            "close-managed-item:53:closed-before-spawns",
            "close_managed_drop_missing_precondition:live_open_target",
        )

    def test_wakeup_runner_tick_reports_later_solver_launch_after_blocked_lifecycle(self) -> None:
        results = [
            RunnerResult(
                "publish-implementation:missing-verified-head-before-spawn",
                "blocked",
                "publish_implementation_missing_precondition:canonical_implementation_identity",
            ),
            RunnerResult("harness-spawn-intent:phase9-router:493:1:minimal", "applied", ""),
            RunnerResult("harness-spawn-intent:phase9-router:493:1:structural", "applied", ""),
            RunnerResult("harness-spawn-intent:phase9-router:493:1:delete", "applied", ""),
        ]

        action = _wakeup_tick_action(results)
        # still reports the dispatched spawn as the headline
        self.assertIn("dispatched harness-spawn-intent:phase9-router:493:1:minimal+2", action)
        # and now also surfaces the blocked lifecycle action that used to be hidden
        self.assertIn("blocked:publish_implementation_missing_precondition", action)
        # and the per-status counts for the whole tick
        self.assertIn("[applied=3,blocked=1]", action)

    def test_wakeup_runner_blocked_non_spawn_can_continue_to_one_later_lifecycle_action(self) -> None:
        blocked = self.implementation_output_action(
            action_id="publish-implementation:missing-verified-head-before-close",
            preconditions=[
                "active_controller_owner",
                "clean_exit_source_marker",
                "clean_scoped_diff",
                "host_checks_green",
                "single_linked_managed_issue",
                "no_conflicting_open_implementation_pr",
            ],
        )
        close = self.close_action(action_id="close-managed-item:53:after-blocked-publish")
        actions = FakeActions()

        results = self.run_result(self.batch_plan([blocked, close], dispatch_required=2, deficit=2), actions=actions)

        self.assertEqual(
            [(result.action_id, result.status) for result in results],
            [
                ("publish-implementation:missing-verified-head-before-close", "blocked"),
                ("close-managed-item:53:after-blocked-publish", "applied"),
            ],
        )
        self.assertEqual([call[0] for call in actions.calls], ["close_managed_item_from_drop_marker"])

    def test_wakeup_runner_lifecycle_review_gate_not_starved_after_spawn_batch(self) -> None:
        for role, verdict in (("architect", "approve"), ("tests", "approve"), ("quality", "comment")):
            (self.repo / ".refactor-loop/prompts" / f"review-pr77-{role}-r1.md").write_text(
                f"head_sha: {'a' * 40}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/runs" / f"review-pr77-{role}-r1.md").write_text(
                f"---\nverdict: {verdict}\n---\nREVIEW_DONE:77:{role}:{verdict}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/logs" / f"review-pr77-{role}-r1.log").write_text(
                f"REVIEW_DONE:77:{role}:{verdict}\nEXIT=0\n",
                encoding="utf-8",
            )
        first = self.spawn_action(action_id="spawn:before-review-gate:1", log=str(self.repo / ".refactor-loop/logs/spawn-before-review-1.log"))
        second = self.spawn_action(action_id="spawn:before-review-gate:2", log=str(self.repo / ".refactor-loop/logs/spawn-before-review-2.log"))
        gate = self.review_gate_action(action_id="review-gate:77:after-spawns")
        actions = FakeActions()

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.batch_plan([first, second, gate], dispatch_required=2, deficit=2), actions=actions)

        self.assertEqual(
            [(result.action_id, result.status) for result in results],
            [
                ("spawn:before-review-gate:1", "applied"),
                ("spawn:before-review-gate:2", "applied"),
                ("review-gate:77:after-spawns", "applied"),
            ],
        )
        self.assertEqual(launch.call_count, 2)
        self.assertEqual(actions.calls, [("merge_pr", "77")])

    def test_hard_gate_release_rollup_open_pr_is_not_starved_by_spawn_budget(self) -> None:
        rollup = self.release_rollup_action(action_id="release-rollup-needed:priority-current")
        spawns = [
            self.spawn_action(
                action_id=f"spawn:publish-implementation-fallback:{index}",
                log=str(self.repo / f".refactor-loop/logs/publish-implementation-fallback-{index}.log"),
            )
            for index in range(2)
        ]
        ordinary_lifecycle = self.implementation_output_action(action_id="completed-marker:implement-before-rollup")
        actions = FakeActions()

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(
                self.batch_plan([*spawns, ordinary_lifecycle, rollup], dispatch_required=2, deficit=2),
                actions=actions,
                duplicate_prs=[],
                git_diff_code=1,
            )

        self.assertEqual([result.action_id for result in results[:1]], [rollup["action_id"]])
        self.assertEqual(results[0].status, "applied")
        self.assertEqual([call[0] for call in actions.calls], ["open_release_rollup_pr_from_action"])
        self.assertEqual(launch.call_count, 2)

    def test_review_gate_reject_routes_to_fix_even_when_ci_is_red(self) -> None:
        runner = WakeupRunner(self.ctx)
        action = self.review_gate_action()

        with (
            mock.patch.object(runner, "_review_gate", return_value=self.review_gate_snapshot(reject=1, approve=0)) as gate,
            mock.patch.object(runner, "_review_gate_ci_error", return_value="ci_failed") as ci_error,
            mock.patch.object(runner, "_review_gate_mergeability_error", return_value=None) as mergeability_error,
            mock.patch.object(runner, "_pr_head_sha", return_value="a" * 40),
        ):
            decision = runner._review_gate_decision(action)

        self.assertEqual(decision["decision"], "FIX")
        self.assertEqual(decision["reason"], "")
        gate.assert_called_once_with(77)
        ci_error.assert_not_called()
        mergeability_error.assert_called_once_with(77)

    def test_review_gate_reject_waits_when_pr_is_conflicting(self) -> None:
        runner = WakeupRunner(self.ctx)
        action = self.review_gate_action()

        with (
            mock.patch.object(runner, "_review_gate", return_value=self.review_gate_snapshot(reject=1, approve=0)),
            mock.patch.object(runner, "_review_gate_ci_error", return_value=None) as ci_error,
            mock.patch.object(runner, "_review_gate_mergeability_error", return_value="mergeability:CONFLICTING") as mergeability_error,
            mock.patch.object(runner, "_pr_head_sha", return_value="a" * 40),
        ):
            decision = runner._review_gate_decision(action)

        self.assertEqual(decision["decision"], "WAIT_OR_REDISPATCH")
        self.assertEqual(decision["reason"], "mergeability:CONFLICTING")
        mergeability_error.assert_called_once_with(77)
        ci_error.assert_not_called()

    def test_review_gate_approval_still_waits_when_ci_is_red(self) -> None:
        runner = WakeupRunner(self.ctx)
        action = self.review_gate_action()

        with (
            mock.patch.object(runner, "_review_gate", return_value=self.review_gate_snapshot(approve=1, reject=0)),
            mock.patch.object(runner, "_review_gate_ci_error", return_value="ci_failed") as ci_error,
            mock.patch.object(runner, "_review_gate_mergeability_error", return_value=None) as mergeability_error,
            mock.patch.object(runner, "_pr_head_sha", return_value="a" * 40),
        ):
            decision = runner._review_gate_decision(action)

        self.assertEqual(decision["decision"], "WAIT_OR_REDISPATCH")
        self.assertEqual(decision["reason"], "ci_failed")
        ci_error.assert_called_once_with(77, "a" * 40)
        mergeability_error.assert_called_once_with(77)

    def test_review_gate_approval_waits_when_pr_is_conflicting(self) -> None:
        runner = WakeupRunner(self.ctx)
        action = self.review_gate_action()

        with (
            mock.patch.object(runner, "_review_gate", return_value=self.review_gate_snapshot(approve=1, reject=0, comment=0)),
            mock.patch.object(runner, "_review_gate_ci_error", return_value=None) as ci_error,
            mock.patch.object(runner, "_review_gate_mergeability_error", return_value="mergeability:CONFLICTING") as mergeability_error,
            mock.patch.object(runner, "_pr_head_sha", return_value="a" * 40),
        ):
            decision = runner._review_gate_decision(action)

        self.assertEqual(decision["decision"], "WAIT_OR_REDISPATCH")
        self.assertEqual(decision["reason"], "mergeability:CONFLICTING")
        mergeability_error.assert_called_once_with(77)
        ci_error.assert_not_called()

    def test_review_gate_approval_merges_when_ci_green_and_mergeable(self) -> None:
        runner = WakeupRunner(self.ctx)
        action = self.review_gate_action()

        with (
            mock.patch.object(runner, "_review_gate", return_value=self.review_gate_snapshot(approve=1, reject=0, comment=0)),
            mock.patch.object(runner, "_review_gate_ci_error", return_value=None) as ci_error,
            mock.patch.object(runner, "_review_gate_mergeability_error", return_value=None) as mergeability_error,
            mock.patch.object(runner, "_pr_head_sha", return_value="a" * 40),
        ):
            decision = runner._review_gate_decision(action)

        self.assertEqual(decision["decision"], "MERGE")
        self.assertEqual(decision["reason"], "")
        ci_error.assert_called_once_with(77, "a" * 40)
        mergeability_error.assert_called_once_with(77)

    def test_review_gate_reject_with_mismatched_head_still_waits(self) -> None:
        runner = WakeupRunner(self.ctx)
        action = self.review_gate_action(head_sha="a" * 40)

        with (
            mock.patch.object(runner, "_review_gate", return_value=self.review_gate_snapshot(reject=1, approve=0, live_head_sha="b" * 40)),
            mock.patch.object(runner, "_review_gate_ci_error", return_value="ci_failed") as ci_error,
            mock.patch.object(runner, "_review_gate_mergeability_error", return_value=None) as mergeability_error,
            mock.patch.object(runner, "_pr_head_sha", return_value="b" * 40),
        ):
            decision = runner._review_gate_decision(action)

        self.assertEqual(decision["decision"], "WAIT_OR_REDISPATCH")
        self.assertEqual(decision["reason"], "action_head_mismatch")
        ci_error.assert_not_called()
        mergeability_error.assert_not_called()

    def test_wakeup_runner_headless_review_fix_dispatch_uses_fully_rendered_prompt(self) -> None:
        worktree = self.repo / ".worktrees" / "iter77-worker"
        worktree.mkdir(parents=True, exist_ok=True)

        for role, verdict in (("architect", "approve"), ("tests", "reject"), ("quality", "comment")):
            (self.repo / ".refactor-loop/prompts" / f"review-pr77-{role}-r1.md").write_text(
                f"head_sha: {'a' * 40}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/runs" / f"review-pr77-{role}-r1.md").write_text(
                f"---\nverdict: {verdict}\n---\nREVIEW_DONE:77:{role}:{verdict}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/logs" / f"review-pr77-{role}-r1.log").write_text(
                f"REVIEW_DONE:77:{role}:{verdict}\nEXIT=0\n",
                encoding="utf-8",
            )
        gate = self.review_gate_action(action_id="review-gate:77:headless-fix")
        actions = FakeReviewFixActions(self.repo)

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.base_plan(gate), gh_state="OPEN", actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.rendered, [(77, 1)])
        launch.assert_called_once()
        self.assertEqual(Path(launch.call_args.kwargs["cd"]).resolve(), worktree.resolve())
        self.assertNotEqual(Path(launch.call_args.kwargs["cd"]).resolve(), self.repo.resolve())
        self.assertEqual(tuple(Path(path).resolve() for path in launch.call_args.kwargs["add_dirs"]), (self.ctx.repo_root.resolve(),))
        prompt = Path(launch.call_args.kwargs["prompt"])
        self.assertEqual(prompt.resolve(), (self.repo / ".refactor-loop/prompts/fixes/fix-pr77-round-1.md").resolve())
        self.assertNotIn("${", prompt.read_text(encoding="utf-8"))

    def test_wakeup_runner_headless_review_fix_fails_closed_when_pr_worktree_missing(self) -> None:
        for role, verdict in (("architect", "approve"), ("tests", "reject"), ("quality", "comment")):
            (self.repo / ".refactor-loop/prompts" / f"review-pr77-{role}-r1.md").write_text(
                f"head_sha: {'a' * 40}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/runs" / f"review-pr77-{role}-r1.md").write_text(
                f"---\nverdict: {verdict}\n---\nREVIEW_DONE:77:{role}:{verdict}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/logs" / f"review-pr77-{role}-r1.log").write_text(
                f"REVIEW_DONE:77:{role}:{verdict}\nEXIT=0\n",
                encoding="utf-8",
            )
        gate = self.review_gate_action(action_id="review-gate:77:missing-fix-worktree")
        actions = FakeReviewFixActions(self.repo)

        def command_runner(command):
            repo_root = self.ctx.repo_root
            if command == ["gh", "api", "repos/owner/repo/pulls/77"]:
                return subprocess.CompletedProcess(command, 0, json.dumps({"state": "open", "head": {"sha": "a" * 40}}), "")
            if command[:3] == ["gh", "api", f"repos/owner/repo/commits/{'a' * 40}/check-runs"]:
                payload = [{"check_runs": [{"name": "ci", "status": "completed", "conclusion": "success"}]}]
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
            if command[:3] == ["gh", "pr", "view"]:
                if "headRefName" in command and "--jq" not in command:
                    return subprocess.CompletedProcess(command, 0, json.dumps({"headRefName": "refactor/iter77-worker"}), "")
                if ".headRefOid" in command:
                    return subprocess.CompletedProcess(command, 0, "a" * 40 + "\n", "")
                if "mergeable,isDraft,changedFiles" in command:
                    return subprocess.CompletedProcess(command, 0, json.dumps({"mergeable": "MERGEABLE", "isDraft": False, "changedFiles": 1}), "")
            if command == ["git", "-C", str(repo_root), "worktree", "list", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, f"worktree {repo_root}\nbranch refs/heads/auto-refact-dev\n\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        runner = WakeupRunner(
            self.ctx,
            plan_loader=lambda _repo: self.base_plan(gate),
            actions=actions,
            supervisor=self.supervisor,
            command_runner=command_runner,
        )

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = runner.run_once()

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "helper_exit:3")
        launch.assert_not_called()
        pending = (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("WAKEUP_RUNNER_REVIEW_FIX_WORKTREE_MISSING:77:refactor/iter77-worker", pending)

    def test_wakeup_runner_review_fix_worktree_gap_does_not_starve_later_ci_red_dispatch(self) -> None:
        for role, verdict in (("architect", "approve"), ("tests", "reject"), ("quality", "comment")):
            (self.repo / ".refactor-loop/prompts" / f"review-pr77-{role}-r1.md").write_text(
                f"head_sha: {'a' * 40}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/runs" / f"review-pr77-{role}-r1.md").write_text(
                f"---\nverdict: {verdict}\n---\nREVIEW_DONE:77:{role}:{verdict}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/logs" / f"review-pr77-{role}-r1.log").write_text(
                f"REVIEW_DONE:77:{role}:{verdict}\nEXIT=0\n",
                encoding="utf-8",
            )
        blocked_gate = self.review_gate_action(action_id="review-gate:77:missing-fix-worktree-before-ci-red")
        ci_red = self.remote_ci_fix_action(
            action_id="ci-red:78:" + "b" * 40 + ":contract-tests",
            target_number=78,
            target={"kind": "PR", "number": 78},
            head_sha="b" * 40,
            source_marker="ci-red:78:" + "b" * 40 + ":contract-tests",
        )
        actions = FakeReviewFixActions(self.repo)

        def command_runner(command):
            repo_root = self.ctx.repo_root
            if command[:2] == ["gh", "api"]:
                endpoint = str(command[2]) if len(command) > 2 else ""
                if endpoint in {"repos/owner/repo/pulls/77", "repos/owner/repo/pulls/78"}:
                    return subprocess.CompletedProcess(command, 0, json.dumps({"state": "open", "head": {"sha": "a" * 40}}), "")
                if endpoint == f"repos/owner/repo/commits/{'a' * 40}/check-runs":
                    payload = [{"check_runs": [{"name": "ci", "status": "completed", "conclusion": "success"}]}]
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
                return subprocess.CompletedProcess(command, 0, "{}", "")
            if command[:3] == ["gh", "pr", "view"]:
                target = command[3] if len(command) > 3 else ""
                if "headRefName" in command and "--jq" not in command:
                    branch = "refactor/iter77-worker" if target == "77" else "refactor/iter78-worker"
                    return subprocess.CompletedProcess(command, 0, json.dumps({"headRefName": branch}), "")
                if ".headRefOid" in command:
                    return subprocess.CompletedProcess(command, 0, "a" * 40 + "\n", "")
                if "mergeable,isDraft,changedFiles" in command:
                    return subprocess.CompletedProcess(command, 0, json.dumps({"mergeable": "MERGEABLE", "isDraft": False, "changedFiles": 1}), "")
            if command == ["git", "-C", str(repo_root), "worktree", "list", "--porcelain"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    f"worktree {repo_root}\nbranch refs/heads/auto-refact-dev\n\n"
                    f"worktree {repo_root / '.worktrees' / 'iter78-worker'}\nbranch refs/heads/refactor/iter78-worker\n\n",
                    "",
                )
            return subprocess.CompletedProcess(command, 0, "", "")

        runner = WakeupRunner(
            self.ctx,
            plan_loader=lambda _repo: self.batch_plan([blocked_gate, ci_red], dispatch_required=2, deficit=2),
            actions=actions,
            supervisor=self.supervisor,
            command_runner=command_runner,
        )

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = runner.run_once()

        self.assertEqual(
            [(result.action_id, result.status, result.reason) for result in results],
            [
                ("review-gate:77:missing-fix-worktree-before-ci-red", "blocked", "helper_exit:3"),
                ("ci-red:78:" + "b" * 40 + ":contract-tests", "applied", ""),
            ],
        )
        self.assertEqual(actions.rendered, [(77, 1)])
        launch.assert_called_once()
        self.assertEqual(Path(launch.call_args.kwargs["cd"]).resolve(), (self.repo / ".worktrees" / "iter78-worker").resolve())
        pending = (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("WAKEUP_RUNNER_REVIEW_FIX_WORKTREE_MISSING:77:refactor/iter77-worker", pending)

    def test_wakeup_runner_skips_blocked_spawn_validation_and_scans_later_actions(self) -> None:
        first = self.spawn_action(action_id="spawn:first", log=str(self.repo / ".refactor-loop/logs/first.log"))
        blocked = self.spawn_action(action_id="spawn:blocked", log=str(self.repo / ".refactor-loop/logs/blocked.log"))
        later = self.spawn_action(action_id="spawn:later", log=str(self.repo / ".refactor-loop/logs/later.log"))
        Path(blocked["log"]).write_text("already claimed\n", encoding="utf-8")

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.batch_plan([first, blocked, later], dispatch_required=3, deficit=3))

        self.assertEqual([result.action_id for result in results], ["spawn:first", "spawn:blocked", "spawn:later"])
        self.assertEqual([result.status for result in results], ["applied", "blocked", "applied"])
        self.assertEqual(results[1].reason, "target_log_exists")
        self.assertEqual(launch.call_count, 2)
        self.assert_blocked_event("spawn:blocked", "target_log_exists")

    def test_wakeup_runner_stops_on_spawn_launch_failure_without_scanning_later_actions(self) -> None:
        first = self.spawn_action(action_id="spawn:launch-fails", log=str(self.repo / ".refactor-loop/logs/launch-fails.log"))
        later = self.spawn_action(action_id="spawn:after-launch-failure", log=str(self.repo / ".refactor-loop/logs/after-launch-failure.log"))

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=3) as launch:
            results = self.run_result(self.batch_plan([first, later], dispatch_required=2, deficit=2))

        self.assertEqual([result.action_id for result in results], ["spawn:launch-fails"])
        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "helper_exit:3")
        self.assertEqual(launch.call_count, 1)

    def test_wakeup_runner_records_helper_exit_source_for_spawn_supervisor_failure(self) -> None:
        action = self.spawn_action(action_id="spawn:supervisor-exit-3")

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=3):
            results = self.run_result(self.base_plan(action))

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "helper_exit:3")
        pending = (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("WAKEUP_RUNNER_SPAWN_LAUNCH_EXIT:spawn:supervisor-exit-3:3", pending)
        self.assertIn("WAKEUP_RUNNER_HELPER_EXIT:spawn:supervisor-exit-3:spawn_codex_harness_background:3", pending)

    def test_harness_spawn_passes_context_skill_root_to_supervisor(self) -> None:
        action = self.spawn_action(action_id="spawn:skill-root")

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.base_plan(action))

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(Path(launch.call_args.kwargs["skill_root"]).resolve(), self.expected_skill_root.resolve())
        self.assertEqual(Path(launch.call_args.kwargs["repo_root"]).resolve(), self.repo.resolve())

    def test_harness_spawn_existing_target_log_blocks_before_supervisor(self) -> None:
        actions = FakeActions()
        action = self.spawn_action(action_id="spawn:target-log-exists")
        Path(action["log"]).write_text("existing worker\n", encoding="utf-8")

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assert_blocked_before_dispatch(results, "spawn:target-log-exists", "target_log_exists", actions)

    def test_harness_spawn_failed_target_log_retries_supervisor(self) -> None:
        action = self.spawn_action(action_id="spawn:failed-target-log")
        Path(action["log"]).write_text("SPAWN_FAILED=codex missing\nEXIT=127\n", encoding="utf-8")

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.base_plan(action), actions=FakeActions())

        self.assertEqual([(result.action_id, result.status, result.reason) for result in results], [("spawn:failed-target-log", "applied", "")])
        launch.assert_called_once()

    def test_effect_admission_boundary_rejects_minimum_forbidden_command_and_lifecycle_fields(self) -> None:
        minimum_forbidden_fields = (
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
        )
        for field in minimum_forbidden_fields:
            with self.subTest(field=field):
                results = self.run_result(self.base_plan(self.spawn_action(action_id=f"forbidden:{field}", **{field: "forbidden"})))

                self.assertEqual(results[0].status, "blocked")
                self.assertEqual(results[0].reason, f"forbidden_fields:{field}")
                self.assertEqual(self.supervisor.calls, [])
                pending = (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8")
                self.assertIn(f"WAKEUP_RUNNER_BLOCKED:forbidden:{field}:forbidden_fields:{field}", pending)
                self.assert_blocked_ledger(f"forbidden:{field}", f"forbidden_fields:{field}")

    def test_effect_admission_boundary_keeps_compatibility_extra_forbidden_fields(self) -> None:
        for field in (
            "args",
            "target_ref",
        ):
            with self.subTest(field=field):
                results = self.run_result(self.base_plan(self.spawn_action(action_id=f"forbidden:{field}", **{field: "forbidden"})))

                self.assertEqual(results[0].status, "blocked")
                self.assertEqual(results[0].reason, f"forbidden_fields:{field}")
                self.assertEqual(self.supervisor.calls, [])
                pending = (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8")
                self.assertIn(f"WAKEUP_RUNNER_BLOCKED:forbidden:{field}:forbidden_fields:{field}", pending)
                self.assert_blocked_ledger(f"forbidden:{field}", f"forbidden_fields:{field}")

    def test_effect_admission_boundary_is_concrete_controller_action_allowlist_only(self) -> None:
        expected_actions = {
            "spawn_codex_harness_background",
            "safe_push",
            "dispatch_consensus_implementation",
            "publish_implementation_output",
            "publish_worker_output_from_action",
            "publish_review_fix_output_from_action",
            "dispatch_reviewers",
            "dispatch_remote_ci_fix",
            "dispatch_pr_rebase_resolve",
            "commit_push_resolved_pr_rebase",
            "open_release_rollup_pr_from_action",
            "close_managed_item_from_drop_marker",
            "review_gate",
            "auto_merge_release_rollup_pr_from_action",
            "dispatch_release_candidate",
            "publish_release_candidate",
            "apply_issue_decomposition_plan",
            "apply_default_issue_intake_claim",
        }

        self.assertEqual(SUPPORTED_CONTROLLER_ACTIONS, expected_actions)
        source = (SCRIPT_DIR / "codex_refactor_loop" / "wakeup_runner.py").read_text(encoding="utf-8")
        for forbidden_runtime_abstraction in (
            "ControllerEffectAdapter",
            "WakeupActionAdmission",
            "WakeupActionResult",
        ):
            with self.subTest(forbidden_runtime_abstraction=forbidden_runtime_abstraction):
                self.assertNotIn(forbidden_runtime_abstraction, source)

    def test_release_dispatch_writes_candidate_artifact_only(self) -> None:
        self.write_release_dispatch_fixtures()
        actions = FakeActions()

        results = self.run_result(self.base_plan(self.release_dispatch_action()), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls, [])
        decision = json.loads((self.repo / ".refactor-loop/state/release-decision.json").read_text(encoding="utf-8"))
        candidate = json.loads((self.repo / ".refactor-loop/state/release-candidate.json").read_text(encoding="utf-8"))
        self.assertTrue(decision["ready"])
        self.assertEqual(candidate["schema"], "decision-artifact-only/v2")
        self.assertEqual(candidate["target_ref"], "origin/dev")
        self.assertEqual(candidate["to_version"], "1.2.3-beta.5")

    def test_release_dispatch_refreshes_release_commits_before_decision(self) -> None:
        self.write_release_dispatch_fixtures()
        (self.repo / ".refactor-loop/state/release-commits.json").write_text(
            json.dumps({"commits": [{"sha": "stale", "subject": "fix: stale", "body": ""}]}),
            encoding="utf-8",
        )

        results = self.run_result(self.base_plan(self.release_dispatch_action()), actions=FakeActions())

        self.assertEqual(results[0].status, "applied")
        commits = json.loads((self.repo / ".refactor-loop/state/release-commits.json").read_text(encoding="utf-8"))
        self.assertEqual(commits["commits"], [{"sha": self.release_dispatch_fix_sha, "subject": "fix: next release", "body": ""}])
        self.assertIn("latest_release_version", commits)
        decision = json.loads((self.repo / ".refactor-loop/state/release-decision.json").read_text(encoding="utf-8"))
        self.assertEqual(decision["commits"], [{"sha": self.release_dispatch_fix_sha, "subject": "fix: next release"}])

    def test_release_dispatch_recovers_existing_candidate_with_empty_target_ref(self) -> None:
        self.write_release_dispatch_fixtures()
        bad_candidate = self.repo / ".refactor-loop/state/release-candidate.json"
        bad_candidate.write_text(
            json.dumps({"ready": True, "target_ref": "", "to_version": "1.2.3-beta.5"}),
            encoding="utf-8",
        )
        actions = FakeActions()

        results = self.run_result(
            self.base_plan(
                self.release_dispatch_action(
                    preconditions=[
                        "active_controller_owner",
                        "release_auto_opt_in",
                        "release_gate_ready",
                        "decision_artifact_only",
                        "release_candidate_target_ref_invalid",
                    ],
                )
            ),
            actions=actions,
        )

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls, [])
        candidate = json.loads(bad_candidate.read_text(encoding="utf-8"))
        self.assertTrue(candidate["ready"])
        self.assertEqual(candidate["target_ref"], "origin/dev")
        self.assertEqual(candidate["to_version"], "1.2.3-beta.5")
        pending = (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn(
            f"WAKEUP_RUNNER_RELEASE_DISPATCH_STALE_CANDIDATE:{self.release_dispatch_action()['action_id']}:release_candidate_target_ref_invalid",
            pending,
        )

    def test_release_dispatch_recovers_existing_candidate_for_previous_decision(self) -> None:
        self.write_release_dispatch_fixtures()
        current_decision = release_decision("1.2.3-beta.4", "1.2.3-beta.5")
        old_decision = release_decision("1.2.3-beta.3", "1.2.3-beta.4")
        (self.repo / ".refactor-loop/state/release-decision.json").write_text(
            json.dumps(current_decision),
            encoding="utf-8",
        )
        existing = self.repo / ".refactor-loop/state/release-candidate.json"
        existing.write_text(
            json.dumps(
                {
                    "ready": True,
                    "target_ref": "origin/dev",
                    "from_version": "1.2.3-beta.3",
                    "to_version": "1.2.3-beta.4",
                    "bump_type": "patch",
                    "coordinate_policy": None,
                    "generated_at": isoformat(datetime.now(timezone.utc)),
                    "expires_at": isoformat(datetime.now(timezone.utc) + timedelta(minutes=120)),
                    "decision_digest": canonical_digest(old_decision),
                }
            ),
            encoding="utf-8",
        )

        results = self.run_result(
            self.base_plan(
                self.release_dispatch_action(
                    preconditions=[
                        "active_controller_owner",
                        "release_auto_opt_in",
                        "release_gate_ready",
                        "decision_artifact_only",
                        "release_candidate_decision_mismatch",
                    ],
                )
            ),
            actions=FakeActions(),
        )

        self.assertEqual(results[0].status, "applied")
        candidate = json.loads(existing.read_text(encoding="utf-8"))
        self.assertEqual(candidate["target_ref"], "origin/dev")
        self.assertEqual(candidate["from_version"], "1.2.3-beta.4")
        self.assertEqual(candidate["to_version"], "1.2.3-beta.5")

    def test_release_dispatch_recovers_consumed_candidate(self) -> None:
        self.write_release_dispatch_fixtures()
        current_decision = release_decision("1.2.3-beta.4", "1.2.3-beta.5")
        (self.repo / ".refactor-loop/state/release-decision.json").write_text(
            json.dumps(current_decision),
            encoding="utf-8",
        )
        existing = self.repo / ".refactor-loop/state/release-candidate.json"
        existing.write_text(
            json.dumps(
                {
                    "ready": True,
                    "target_ref": "origin/dev",
                    "from_version": "1.2.3-beta.4",
                    "to_version": "1.2.3-beta.5",
                    "bump_type": "patch",
                    "coordinate_policy": None,
                    "generated_at": isoformat(datetime.now(timezone.utc)),
                    "expires_at": isoformat(datetime.now(timezone.utc) + timedelta(minutes=120)),
                    "decision_digest": canonical_digest(current_decision),
                }
            ),
            encoding="utf-8",
        )
        (self.repo / ".refactor-loop/state/release-publish-result.json").write_text(
            json.dumps({"version": "1.2.3-beta.5", "tag": "v1.2.3-beta.5"}),
            encoding="utf-8",
        )

        results = self.run_result(
            self.base_plan(
                self.release_dispatch_action(
                    preconditions=[
                        "active_controller_owner",
                        "release_auto_opt_in",
                        "release_gate_ready",
                        "decision_artifact_only",
                        "release_candidate_consumed_by_publish_result",
                    ],
                )
            ),
            actions=FakeActions(),
        )

        self.assertEqual(results[0].status, "applied")
        candidate = json.loads(existing.read_text(encoding="utf-8"))
        self.assertEqual(candidate["from_version"], "1.2.3-beta.4")
        self.assertEqual(candidate["to_version"], "1.2.3-beta.5")
        pending = (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn(
            f"WAKEUP_RUNNER_RELEASE_DISPATCH_STALE_CANDIDATE:{self.release_dispatch_action()['action_id']}:release_candidate_consumed_by_publish_result",
            pending,
        )

    def test_release_dispatch_recovers_stale_applied_ledger_with_empty_target_ref(self) -> None:
        self.write_release_dispatch_fixtures()
        bad_candidate = self.repo / ".refactor-loop/state/release-candidate.json"
        bad_candidate.write_text(
            json.dumps({"ready": True, "target_ref": "", "to_version": "1.2.3-beta.5"}),
            encoding="utf-8",
        )
        action = self.release_dispatch_action(
            preconditions=[
                "active_controller_owner",
                "release_auto_opt_in",
                "release_gate_ready",
                "decision_artifact_only",
                "release_candidate_target_ref_invalid",
            ],
        )
        ledger = self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl"
        ledger.write_text(
            json.dumps({"action_id": action["action_id"], "status": "applied", "reason": ""}) + "\n",
            encoding="utf-8",
        )

        results = self.run_result(self.base_plan(action), actions=FakeActions())

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(results[0].reason, "")
        candidate = json.loads(bad_candidate.read_text(encoding="utf-8"))
        self.assertEqual(candidate["target_ref"], "origin/dev")
        pending = (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn(
            f"WAKEUP_RUNNER_STALE_RELEASE_DISPATCH_LEDGER:{action['action_id']}:release_candidate_target_ref_invalid",
            pending,
        )

    def test_release_dispatch_recovers_stale_applied_ledger_with_consumed_candidate(self) -> None:
        self.write_release_dispatch_fixtures()
        current_decision = release_decision("1.2.3-beta.4", "1.2.3-beta.5")
        (self.repo / ".refactor-loop/state/release-decision.json").write_text(json.dumps(current_decision), encoding="utf-8")
        candidate = self.repo / ".refactor-loop/state/release-candidate.json"
        candidate.write_text(
            json.dumps(
                {
                    "ready": True,
                    "target_ref": "origin/dev",
                    "from_version": "1.2.3-beta.4",
                    "to_version": "1.2.3-beta.5",
                    "bump_type": "patch",
                    "coordinate_policy": None,
                    "generated_at": isoformat(datetime.now(timezone.utc)),
                    "expires_at": isoformat(datetime.now(timezone.utc) + timedelta(minutes=120)),
                    "decision_digest": canonical_digest(current_decision),
                }
            ),
            encoding="utf-8",
        )
        (self.repo / ".refactor-loop/state/release-publish-result.json").write_text(
            json.dumps({"tag": "v1.2.3-beta.5"}),
            encoding="utf-8",
        )
        action = self.release_dispatch_action(
            preconditions=[
                "active_controller_owner",
                "release_auto_opt_in",
                "release_gate_ready",
                "decision_artifact_only",
                "release_candidate_consumed_by_publish_result",
            ],
        )
        (self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl").write_text(
            json.dumps({"action_id": action["action_id"], "status": "applied", "reason": ""}) + "\n",
            encoding="utf-8",
        )

        results = self.run_result(self.base_plan(action), actions=FakeActions())

        self.assertEqual(results[0].status, "applied")
        pending = (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn(
            f"WAKEUP_RUNNER_STALE_RELEASE_DISPATCH_LEDGER:{action['action_id']}:release_candidate_consumed_by_publish_result",
            pending,
        )

    def test_release_dispatch_valid_candidate_keeps_applied_ledger_duplicate(self) -> None:
        self.write_release_dispatch_fixtures()
        existing = self.repo / ".refactor-loop/state/release-candidate.json"
        existing.write_text(
            json.dumps({"ready": True, "target_ref": "origin/dev", "to_version": "1.2.3-beta.5"}),
            encoding="utf-8",
        )
        action = self.release_dispatch_action(
            preconditions=[
                "active_controller_owner",
                "release_auto_opt_in",
                "release_gate_ready",
                "decision_artifact_only",
                "release_candidate_target_ref_invalid",
            ],
        )
        (self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl").write_text(
            json.dumps({"action_id": action["action_id"], "status": "applied", "reason": ""}) + "\n",
            encoding="utf-8",
        )

        results = self.run_result(self.base_plan(action), actions=FakeActions())

        self.assertEqual(results[0].status, "skipped")
        self.assertEqual(results[0].reason, "duplicate")
        candidate = json.loads(existing.read_text(encoding="utf-8"))
        self.assertEqual(candidate["target_ref"], "origin/dev")

    def test_release_dispatch_without_recovery_precondition_keeps_applied_ledger_duplicate(self) -> None:
        self.write_release_dispatch_fixtures()
        bad_candidate = self.repo / ".refactor-loop/state/release-candidate.json"
        bad_candidate.write_text(
            json.dumps({"ready": True, "target_ref": "", "to_version": "1.2.3-beta.5"}),
            encoding="utf-8",
        )
        action = self.release_dispatch_action()
        (self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl").write_text(
            json.dumps({"action_id": action["action_id"], "status": "applied", "reason": ""}) + "\n",
            encoding="utf-8",
        )

        results = self.run_result(self.base_plan(action), actions=FakeActions())

        self.assertEqual(results[0].status, "skipped")
        self.assertEqual(results[0].reason, "duplicate")
        candidate = json.loads(bad_candidate.read_text(encoding="utf-8"))
        self.assertEqual(candidate["target_ref"], "")

    def test_release_dispatch_rejects_existing_candidate_with_valid_target_ref(self) -> None:
        self.write_release_dispatch_fixtures()
        existing = self.repo / ".refactor-loop/state/release-candidate.json"
        existing.write_text(
            json.dumps({"ready": True, "target_ref": "origin/dev", "to_version": "1.2.3-beta.5"}),
            encoding="utf-8",
        )

        results = self.run_result(
            self.base_plan(
                self.release_dispatch_action(
                    preconditions=[
                        "active_controller_owner",
                        "release_auto_opt_in",
                        "release_gate_ready",
                        "decision_artifact_only",
                        "release_candidate_target_ref_invalid",
                    ],
                )
            ),
            actions=FakeActions(),
        )

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "release_candidate_already_exists")

    def test_release_dispatch_rejects_forged_stale_precondition(self) -> None:
        self.write_release_dispatch_fixtures()
        current_decision = release_decision("1.2.3-beta.4", "1.2.3-beta.5")
        existing = self.repo / ".refactor-loop/state/release-candidate.json"
        existing.write_text(
            json.dumps(
                {
                    "ready": True,
                    "target_ref": "origin/dev",
                    "from_version": "1.2.3-beta.4",
                    "to_version": "1.2.3-beta.5",
                    "bump_type": "patch",
                    "coordinate_policy": None,
                    "generated_at": isoformat(datetime.now(timezone.utc)),
                    "expires_at": isoformat(datetime.now(timezone.utc) + timedelta(minutes=120)),
                    "decision_digest": canonical_digest(current_decision),
                }
            ),
            encoding="utf-8",
        )

        results = self.run_result(
            self.base_plan(
                self.release_dispatch_action(
                    preconditions=[
                        "active_controller_owner",
                        "release_auto_opt_in",
                        "release_gate_ready",
                        "decision_artifact_only",
                        "release_candidate_expired",
                    ],
                )
            ),
            actions=FakeActions(),
        )

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "release_candidate_already_exists")

    def test_release_dispatch_fails_closed_without_host_opt_in(self) -> None:
        self.write_release_dispatch_fixtures(auto_enable=False)

        results = self.run_result(self.base_plan(self.release_dispatch_action()), actions=FakeActions())

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "release_auto_opt_in_missing")
        self.assertFalse((self.repo / ".refactor-loop/state/release-candidate.json").exists())

    def test_nested_forbidden_fields_fail_closed(self) -> None:
        action = self.issue_decomposition_action(
            action_id="decompose:nested-forbidden",
            proof_payload={"executor": "shell", "nested": [{"env": {"TOKEN": "x"}}]},
        )

        results = self.run_result(self.base_plan(action), actions=FakeActions())

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "forbidden_fields:proof_payload.executor,proof_payload.nested[0].env")

    def test_nested_forbidden_fields_fail_closed(self) -> None:
        action = self.issue_decomposition_action(
            action_id="decompose:nested-forbidden",
            proof_payload={"executor": "shell", "nested": [{"env": {"TOKEN": "x"}}]},
        )

        results = self.run_result(self.base_plan(action), actions=FakeActions())

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "forbidden_fields:proof_payload.executor,proof_payload.nested[0].env")

    def test_malformed_plan_envelope_blocks_before_dispatch_and_records_ledger(self) -> None:
        actions = FakeActions()
        plan = self.base_plan(self.spawn_action())
        plan["schema"] = "wrong-schema"

        results = self.run_result(plan, actions=actions)

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "schema_mismatch")
        self.assertEqual(actions.calls, [])
        self.assertEqual(self.supervisor.calls, [])
        self.assert_blocked_ledger("", "schema_mismatch")

    def test_malformed_action_authorization_blocks_before_dispatch_and_records_event(self) -> None:
        cases = [
            ("runner-authority", self.spawn_action(action_id="auth:runner", runner_authority="controller"), "runner_authority_mismatch"),
            ("generic-command", self.spawn_action(action_id="auth:generic", no_generic_command=False), "missing_no_generic_command"),
            ("preconditions", self.spawn_action(action_id="auth:preconditions", preconditions=[]), "missing_preconditions"),
        ]

        for name, action, reason in cases:
            with self.subTest(name=name):
                actions = FakeActions()
                results = self.run_result(self.base_plan(action), actions=actions)
                self.assert_blocked_before_dispatch(results, action["action_id"], reason, actions)

    def test_non_owner_noops(self) -> None:
        with mock.patch("codex_refactor_loop.wakeup_runner.require_active_controller") as owner:
            owner.return_value = type("Decision", (), {"allowed": False, "status": "not-owner", "action": "wakeup-runner", "owner_device": "other", "lease_id": "", "expires_at": ""})()

            results = self.run_result(self.base_plan(self.spawn_action()))

        self.assertEqual(results[0].status, "noop")
        self.assertEqual(self.supervisor.calls, [])

    def test_missing_evidence_fails_closed(self) -> None:
        action = self.spawn_action(source_marker="missing")

        results = self.run_result(self.base_plan(action))

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "source_marker_missing")

    def test_missing_clean_exit_source_marker_blocks_before_dispatch_and_records_event(self) -> None:
        source = self.repo / ".refactor-loop/logs/worker.log"
        marker = "IMPLEMENT_DONE:pr-77:ok"
        source.write_text(marker + "\n", encoding="utf-8")
        action = self.spawn_action(
            action_id="clean-exit:missing",
            preconditions=["active_controller_owner", "clean_exit_source_marker"],
            source_artifact=".refactor-loop/logs/worker.log",
            source_marker=marker,
        )
        actions = FakeActions()

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assert_blocked_before_dispatch(results, "clean-exit:missing", "clean_exit_marker_missing", actions)

    def test_ci_red_dispatch_spawns_remote_ci_fix_worker_and_records_attempt(self) -> None:
        worktree = self.repo / ".worktrees" / "iter77-worker"
        worktree.mkdir(parents=True, exist_ok=True)
        actions = FakeActions()

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.base_plan(self.remote_ci_fix_action()), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(Path(launch.call_args.kwargs["skill_root"]).resolve(), self.expected_skill_root.resolve())
        self.assertEqual(Path(launch.call_args.kwargs["cd"]).resolve(), worktree.resolve())
        prompt = Path(launch.call_args.kwargs["prompt"])
        self.assertEqual(prompt.name, f"remote-ci-fix-pr77-contract-tests-{'a' * 12}-a1.md")
        self.assertIn("PR: `77`", prompt.read_text(encoding="utf-8"))
        self.assertIn("failing check: `contract-tests`", prompt.read_text(encoding="utf-8"))
        attempts = json.loads((self.repo / ".refactor-loop/state/remote-ci-fix-attempts.json").read_text(encoding="utf-8"))
        self.assertEqual(attempts[f"pr77:{'a' * 40}:contract-tests"], 1)
        self.assertEqual(actions.calls, [])

    def test_ci_red_dispatch_rejects_legacy_raw_checks_red_precondition(self) -> None:
        action = self.remote_ci_fix_action(preconditions=["active_controller_owner", "live_open_target", "checks_red"])

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor") as launch:
            results = self.run_result(self.base_plan(action), actions=FakeActions())

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(
            results[0].reason,
            "dispatch_remote_ci_fix_missing_precondition:target_required_checks_red",
        )
        launch.assert_not_called()

    def test_ci_red_dispatch_retry_cap_stops_third_attempt_per_check(self) -> None:
        worktree = self.repo / ".worktrees" / "iter77-worker"
        worktree.mkdir(parents=True, exist_ok=True)
        action = self.remote_ci_fix_action()
        attempts_path = self.repo / ".refactor-loop/state/remote-ci-fix-attempts.json"
        attempts_path.write_text(json.dumps({f"pr77:{'a' * 40}:contract-tests": 2}), encoding="utf-8")

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor") as launch:
            results = self.run_result(self.base_plan(action), actions=FakeActions())

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "helper_exit:3")
        launch.assert_not_called()
        pending = (self.repo / ".refactor-loop/.controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn(f"WAKEUP_RUNNER_REMOTE_CI_FIX_RETRY_CAP:pr77:{'a' * 40}:contract-tests:2", pending)

    def test_remote_ci_fix_done_commits_and_pushes_worker_output(self) -> None:
        worktree = self.repo / ".worktrees" / "iter77-issue-77"
        worktree.mkdir(parents=True, exist_ok=True)
        marker = "REMOTE_CI_FIX_DONE:contract-tests:ok"
        log = self.repo / ".refactor-loop/logs/remote-ci-fix-pr77-contract-tests.log"
        log.write_text(f"{marker}\nEXIT=0\n", encoding="utf-8")
        action = self.remote_ci_fix_action(
            kind="completed-marker",
            action_id=f"completed-marker:{log.name}:{marker}",
            source_artifact=".refactor-loop/logs/remote-ci-fix-pr77-contract-tests.log",
            source_marker=marker,
            preconditions=["active_controller_owner", "clean_exit_source_marker", "live_open_target_if_present"],
            head_sha=None,
            check_name=None,
        )
        seen: list[list[str]] = []

        def command_runner(command):
            cmd = [str(part) for part in command]
            seen.append(cmd)
            if cmd[:3] == ["gh", "api", "repos/owner/repo/pulls/77"]:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"state": "open"}), "")
            if cmd[:3] == ["gh", "pr", "view"] and "headRefName" in cmd:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"headRefName": "refactor/iter77-issue-77"}), "")
            if cmd[:2] == ["git", "-C"] and len(cmd) > 3 and Path(cmd[2]).resolve() == self.repo.resolve() and cmd[3] == "worktree":
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    f"worktree {self.repo}\nbranch refs/heads/auto-refact-dev\n\n"
                    f"worktree {worktree}\nbranch refs/heads/refactor/iter77-issue-77\n\n",
                    "",
                )
            if "status" in cmd and "--porcelain" in cmd:
                return subprocess.CompletedProcess(cmd, 0, " M skills/x.py\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        actions = FakeActions()
        runner = WakeupRunner(self.ctx, plan_loader=lambda _repo: self.base_plan(action), actions=actions, command_runner=command_runner)
        results = runner.run_once()

        self.assertEqual(results[0].status, "applied")
        git_subcmds = [cmd[3] for cmd in seen if cmd[0] == "git" and len(cmd) > 3]
        self.assertIn("add", git_subcmds)
        self.assertIn("commit", git_subcmds)
        self.assertEqual(actions.calls[0][0], "safe_push")
        self.assertEqual(actions.calls[0][1]["branch"], "refactor/iter77-issue-77")
        self.assertEqual(Path(actions.calls[0][1]["worktree"]).resolve(), worktree.resolve())

    def test_duplicate_remote_ci_fix_done_commits_and_pushes_worker_output(self) -> None:
        worktree = self.repo / ".worktrees" / "iter77-issue-77"
        worktree.mkdir(parents=True, exist_ok=True)
        marker = "REMOTE_CI_FIX_DONE:contract-tests:ok"
        log = self.repo / ".refactor-loop/logs/remote-ci-fix-pr77-contract-tests.log"
        log.write_text(f"{marker}\n{marker}\ntokens used\nEXIT=0\n", encoding="utf-8")
        action = self.remote_ci_fix_action(
            kind="completed-marker",
            action_id=f"completed-marker:{log.name}:{marker}",
            source_artifact=".refactor-loop/logs/remote-ci-fix-pr77-contract-tests.log",
            source_marker=marker,
            preconditions=["active_controller_owner", "clean_exit_source_marker", "live_open_target_if_present"],
            head_sha=None,
            check_name=None,
        )
        seen: list[list[str]] = []

        def command_runner(command):
            cmd = [str(part) for part in command]
            seen.append(cmd)
            if cmd[:3] == ["gh", "api", "repos/owner/repo/pulls/77"]:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"state": "open"}), "")
            if cmd[:3] == ["gh", "pr", "view"] and "headRefName" in cmd:
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"headRefName": "refactor/iter77-issue-77"}), "")
            if cmd[:2] == ["git", "-C"] and len(cmd) > 3 and Path(cmd[2]).resolve() == self.repo.resolve() and cmd[3] == "worktree":
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    f"worktree {self.repo}\nbranch refs/heads/auto-refact-dev\n\n"
                    f"worktree {worktree}\nbranch refs/heads/refactor/iter77-issue-77\n\n",
                    "",
                )
            if "status" in cmd and "--porcelain" in cmd:
                return subprocess.CompletedProcess(cmd, 0, " M skills/x.py\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        actions = FakeActions()
        runner = WakeupRunner(self.ctx, plan_loader=lambda _repo: self.base_plan(action), actions=actions, command_runner=command_runner)
        results = runner.run_once()

        self.assertEqual(results[0].status, "applied")
        git_subcmds = [cmd[3] for cmd in seen if cmd[0] == "git" and len(cmd) > 3]
        self.assertIn("add", git_subcmds)
        self.assertIn("commit", git_subcmds)
        self.assertEqual(actions.calls[0][0], "safe_push")

    def test_conflicting_remote_ci_fix_done_does_not_satisfy_source_marker(self) -> None:
        marker = "REMOTE_CI_FIX_DONE:contract-tests:ok"
        log = self.repo / ".refactor-loop/logs/remote-ci-fix-pr77-contract-tests.log"
        log.write_text(
            "REMOTE_CI_FIX_DONE:contract-tests:blocked\n"
            f"{marker}\n"
            "tokens used\n"
            "EXIT=0\n",
            encoding="utf-8",
        )
        action = self.remote_ci_fix_action(
            kind="completed-marker",
            action_id=f"completed-marker:{log.name}:{marker}",
            source_artifact=".refactor-loop/logs/remote-ci-fix-pr77-contract-tests.log",
            source_marker=marker,
            preconditions=["active_controller_owner", "clean_exit_source_marker", "live_open_target_if_present"],
            head_sha=None,
            check_name=None,
        )
        actions = FakeActions()

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "clean_exit_marker_missing")
        self.assertEqual(actions.calls, [])

    def test_safe_push_routes_to_named_helper_after_head_and_clean_diff_revalidation(self) -> None:
        actions = FakeActions()

        results = self.run_result(self.base_plan(self.worker_output_action()), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(
            actions.calls,
            [
                (
                    "safe_push",
                    {
                        "remote": "origin",
                        "branch": "refactor/iter77-worker",
                        "worktree": str(self.repo / ".worktrees" / "pr77"),
                    },
                )
            ],
        )

    def test_safe_push_helper_nonzero_exit_maps_to_blocked(self) -> None:
        actions = FakeActions(safe_push_code=7)

        results = self.run_result(self.base_plan(self.worker_output_action(action_id="unpushed-worker-output:77:other")), actions=actions)

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "helper_exit:7")

    def test_publish_implementation_output_helper_failure_is_blocked(self) -> None:
        actions = FakeActions(publish_code=75)
        action = self.implementation_output_action(action_id="publish-implementation:helper-failure")

        first = self.run_result(self.base_plan(action), git_diff_code=1, actions=actions)
        second = self.run_result(self.base_plan(action), git_diff_code=1, actions=actions)

        self.assertEqual(first[0].status, "blocked")
        self.assertEqual(first[0].reason, "helper_exit:75")
        self.assertEqual(second[0].status, "blocked")
        self.assertEqual([call[0] for call in actions.calls], ["publish_implementation_output", "publish_implementation_output"])
        ledger_rows = [
            json.loads(line)
            for line in (self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual([row["status"] for row in ledger_rows], ["blocked", "blocked"])
        pending_path = self.repo / ".refactor-loop/.controller-pending-events.log"
        pending = pending_path.read_text(encoding="utf-8") if pending_path.exists() else ""
        self.assertEqual(
            pending.count("WAKEUP_RUNNER_HELPER_EXIT:publish-implementation:helper-failure:publish_implementation_output:75"),
            2,
        )

    def test_publish_implementation_create_pull_request_rate_limit_blocked_ledger_retries(self) -> None:
        actions = FakeActions()

        def rate_limited_publish(action: dict) -> int:
            actions.calls.append(("publish_implementation_output", dict(action)))
            raise RuntimeError(
                "open_pr_with_label: failed to extract PR num from: "
                "pull request create failed: GraphQL: was submitted too quickly (createPullRequest)"
            )

        actions.publish_implementation_output = rate_limited_publish
        action = self.implementation_output_action(action_id="publish-implementation:create-pull-request-rate-limit")

        first = self.run_result(self.base_plan(action), git_diff_code=1, actions=actions)
        second = self.run_result(self.base_plan(action), git_diff_code=1, actions=actions)

        self.assertEqual(first[0].status, "blocked")
        self.assertIn("submitted too quickly", first[0].reason)
        self.assertEqual(second[0].status, "blocked")
        self.assertIn("createPullRequest", second[0].reason)
        self.assertEqual([call[0] for call in actions.calls], ["publish_implementation_output", "publish_implementation_output"])

    def test_safe_push_stale_head_blocks_before_helper(self) -> None:
        actions = FakeActions()

        results = self.run_result(self.base_plan(self.worker_output_action(action_id="unpushed-worker-output:77:stale")), gh_head_ref="other/ref", actions=actions)

        self.assertEqual(results[0].status, "blocked")
        self.assertEqual(results[0].reason, "safe_push_stale_head:other/ref")
        self.assertEqual(actions.calls, [])

    def test_closed_or_unavailable_live_target_blocks_before_lifecycle_helpers(self) -> None:
        cases = [
            (
                "safe-push-closed-pr",
                lambda: self.worker_output_action(action_id="live-target:safe-push-closed"),
                "CLOSED",
                "target_not_open:CLOSED",
            ),
            (
                "merge-closed-pr",
                lambda: self.review_gate_action(action_id="live-target:merge-closed"),
                "CLOSED",
                "target_not_open:CLOSED",
            ),
            (
                "close-unavailable-issue",
                lambda: self.close_action(action_id="live-target:close-unavailable"),
                None,
                "target_not_open:unknown",
            ),
        ]

        for name, action_factory, gh_state, reason in cases:
            with self.subTest(name=name):
                action = action_factory()
                actions = FakeActions()
                results = self.run_result(self.base_plan(action), gh_state=gh_state, actions=actions)
                self.assert_blocked_before_dispatch(results, action["action_id"], reason, actions)

    def test_closed_pr_review_and_fix_dispatch_skip_terminal_before_spawn(self) -> None:
        cases = (
            ("review-dispatch", self.reviewer_dispatch_action(action_id="stale-review:423"), "CLOSED"),
            ("fix-dispatch", self.review_gate_action(action_id="stale-fix:423"), "MERGED"),
        )
        for name, action, gh_state in cases:
            with self.subTest(name=name):
                actions = FakeActions()
                results = self.run_result(self.base_plan(action), gh_state=gh_state, actions=actions)

                self.assert_blocked_before_dispatch(results, action["action_id"], f"target_not_open:{gh_state}", actions)

    def test_closed_pr_nested_target_mapping_blocks_before_dispatch(self) -> None:
        actions = FakeActions()
        action = self.reviewer_dispatch_action(
            action_id="nested-target:review-pr423",
            target_kind=None,
            target_number=None,
            target={"kind": "PR", "number": 423},
        )

        results = self.run_result(self.base_plan(action), gh_state="CLOSED", actions=actions)

        self.assert_blocked_before_dispatch(results, action["action_id"], "target_not_open:CLOSED", actions)

    def test_closed_pr_review_and_fix_text_targets_block_before_dispatch(self) -> None:
        cases = (
            (
                "review-pr-text",
                self.reviewer_dispatch_action(
                    action_id="completed-marker:review-pr423-architect-r1.log:REVIEW_DONE",
                    target_kind=None,
                    target_number=None,
                    target=None,
                ),
                "CLOSED",
                "target_not_open:CLOSED",
            ),
            (
                "fix-pr-text",
                self.review_gate_action(
                    action_id="completed-marker:fix-pr423-round-1.log:FIX_DONE",
                    target_kind=None,
                    target_number=None,
                    target=None,
                ),
                "MERGED",
                "target_not_open:MERGED",
            ),
        )
        for name, action, gh_state, reason in cases:
            with self.subTest(name=name):
                actions = FakeActions()
                results = self.run_result(self.base_plan(action), gh_state=gh_state, actions=actions)

                self.assert_blocked_before_dispatch(results, action["action_id"], reason, actions)

    def test_closed_issue_design_consensus_spawn_skips_terminal_before_supervisor(self) -> None:
        actions = FakeActions()
        action = self.design_consensus_spawn_action()

        results = self.run_result(self.base_plan(action), gh_state="CLOSED", actions=actions)

        self.assert_blocked_before_dispatch(results, action["action_id"], "target_not_open:CLOSED", actions)

    def test_open_issue_design_consensus_spawn_still_applies(self) -> None:
        action = self.design_consensus_spawn_action(action_id="harness-spawn-intent:phase9-router:104:2:judge-open")

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.base_plan(action), gh_state="OPEN", actions=FakeActions())

        self.assertEqual(results[0].status, "applied")
        launch.assert_called_once()

    def test_terminal_closed_target_block_suppresses_next_tick_retry(self) -> None:
        actions = FakeActions()
        action = self.reviewer_dispatch_action(action_id="stale-review:423:dedupe")
        plan = self.base_plan(action)

        first = self.run_result(plan, gh_state="CLOSED", actions=actions)
        second = self.run_result(plan, gh_state="CLOSED", actions=actions)

        self.assertEqual(first[0].status, "blocked")
        self.assertEqual(first[0].reason, "target_not_open:CLOSED")
        self.assertEqual(second[0].status, "skipped")
        self.assertEqual(second[0].reason, "duplicate")
        self.assertEqual(actions.calls, [])

    def test_safe_push_missing_or_invalid_preconditions_block_at_runner_gate_before_helper(self) -> None:
        outside = self.repo / "outside-worktree"
        outside.mkdir()
        cases = [
            (
                "missing-verified-head",
                self.worker_output_action(
                    action_id="safe-push:missing-verified-head",
                    preconditions=["active_controller_owner", "clean_scoped_diff"],
                ),
                "safe_push_missing_precondition:verified_pr_head",
                0,
            ),
            (
                "missing-clean-diff",
                self.worker_output_action(
                    action_id="safe-push:missing-clean-diff",
                    preconditions=["active_controller_owner", "verified_pr_head"],
                ),
                "safe_push_missing_precondition:clean_scoped_diff",
                0,
            ),
            (
                "invalid-head-ref",
                self.worker_output_action(action_id="safe-push:invalid-head-ref", head_ref="-bad"),
                "safe_push_invalid_head_ref",
                0,
            ),
            (
                "missing-worktree",
                self.worker_output_action(action_id="safe-push:missing-worktree", worktree=str(self.repo / ".worktrees" / "missing")),
                "safe_push_worktree_missing",
                0,
            ),
            (
                "outside-worktree",
                self.worker_output_action(action_id="safe-push:outside-worktree", worktree=str(outside)),
                "safe_push_worktree_outside_controller_owned_root",
                0,
            ),
            (
                "dirty-diff",
                self.worker_output_action(action_id="safe-push:dirty-diff"),
                "safe_push_dirty_scoped_diff",
                1,
            ),
        ]

        for name, action, reason, git_diff_code in cases:
            with self.subTest(name=name):
                actions = FakeActions()
                results = self.run_result(self.base_plan(action), git_diff_code=git_diff_code, actions=actions)
                self.assert_blocked_before_dispatch(results, action["action_id"], reason, actions)

    def test_publish_worker_output_action_routes_to_named_helper(self) -> None:
        actions = FakeActions()
        action = self.spawn_action(
            kind="completed-marker",
            action_id="completed-marker:implement.log:IMPLEMENT_DONE:real",
            controller_action="publish_worker_output_from_action",
        )

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "publish_worker_output_from_action")

    def test_publish_review_fix_output_action_commits_pushes_then_dispatches_reviewers(self) -> None:
        worktree = self.repo / ".worktrees" / "iter77-issue-77"
        worktree.mkdir(parents=True, exist_ok=True)
        seen: list[list[str]] = []

        def command_runner(command):
            cmd = [str(part) for part in command]
            seen.append(cmd)
            if cmd[:3] == ["gh", "pr", "view"]:
                return subprocess.CompletedProcess(cmd, 0, '{"headRefName": "refactor/iter77-issue-77"}', "")
            if "status" in cmd and "--porcelain" in cmd:
                return subprocess.CompletedProcess(cmd, 0, " M skills/x.py\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        actions = FakeActions()
        runner = WakeupRunner(self.ctx, actions=actions, command_runner=command_runner)
        action = self.reviewer_dispatch_action(controller_action="publish_review_fix_output_from_action", target_number=77)
        with mock.patch.object(runner, "_review_fix_worktree", return_value=worktree):
            rc = runner._dispatch("publish_review_fix_output_from_action", action)

        self.assertEqual(rc, 0)
        git_subcmds = [cmd[3] for cmd in seen if cmd[0] == "git" and len(cmd) > 3]
        self.assertIn("add", git_subcmds)
        self.assertIn("commit", git_subcmds)
        self.assertEqual(actions.calls[0][0], "safe_push")
        self.assertEqual(actions.calls[-1][0], "dispatch_reviewers")

    def test_publish_review_fix_applied_row_retries_when_fix_worktree_dirty(self) -> None:
        action = self.reviewer_dispatch_action(
            action_id="publish-review-fix-output:77:dirty",
            controller_action="publish_review_fix_output_from_action",
            target_number=77,
            head_sha="a" * 40,
        )
        (self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl").write_text(
            json.dumps({"action_id": action["action_id"], "status": "applied", "reason": "", "kind": "completed-marker"})
            + "\n",
            encoding="utf-8",
        )
        worktree = self.repo / ".worktrees" / "iter77-worker"
        worktree.mkdir(parents=True, exist_ok=True)
        actions = FakeActions()

        def command_runner(command):
            if command[:2] == ["gh", "api"]:
                endpoint = str(command[2]) if len(command) > 2 else ""
                if endpoint == "user":
                    return subprocess.CompletedProcess(command, 0, json.dumps({"login": "current-user"}), "")
                if endpoint.startswith("repos/owner/repo/collaborators/") and endpoint.endswith("/permission"):
                    return subprocess.CompletedProcess(command, 0, json.dumps({"permission": "write"}), "")
                if "/pulls/" in endpoint:
                    return subprocess.CompletedProcess(command, 0, json.dumps({"state": "open"}), "")
                return subprocess.CompletedProcess(command, 0, "{}", "")
            if command[:3] == ["gh", "pr", "view"]:
                if "headRefOid" in command or ".headRefOid" in command:
                    return subprocess.CompletedProcess(command, 0, "a" * 40 + "\n", "")
                if "headRefName" in command:
                    if "--jq" not in command:
                        return subprocess.CompletedProcess(command, 0, json.dumps({"headRefName": "refactor/iter77-worker"}), "")
                    return subprocess.CompletedProcess(command, 0, "refactor/iter77-worker\n", "")
                return subprocess.CompletedProcess(command, 0, "OPEN\n", "")
            if command[:2] == ["git", "-C"] and command[3:] == ["worktree", "list", "--porcelain"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    f"worktree {self.repo}\nbranch refs/heads/auto-refact-dev\n\n"
                    f"worktree {worktree}\nbranch refs/heads/refactor/iter77-worker\n\n",
                    "",
                )
            if command[:2] == ["git", "-C"] and command[3:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, " M skills/x.py\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        runner = WakeupRunner(
            self.ctx,
            plan_loader=lambda _repo: self.base_plan(action),
            actions=actions,
            supervisor=self.supervisor,
            command_runner=command_runner,
        )

        results = runner.run_once()

        self.assertEqual(results[0].status, "applied")
        self.assertEqual([call[0] for call in actions.calls], ["safe_push", "dispatch_reviewers"])

    def test_publish_review_fix_applied_row_retries_when_live_pr_head_unknown(self) -> None:
        action = self.reviewer_dispatch_action(
            action_id="publish-review-fix-output:77:unknown-head",
            controller_action="publish_review_fix_output_from_action",
            target_number=77,
            head_sha="a" * 40,
        )
        (self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl").write_text(
            json.dumps({"action_id": action["action_id"], "status": "applied", "reason": "", "kind": "completed-marker"})
            + "\n",
            encoding="utf-8",
        )
        worktree = self.repo / ".worktrees" / "iter77-worker"
        worktree.mkdir(parents=True, exist_ok=True)

        def command_runner(command):
            if command[:3] == ["gh", "pr", "view"]:
                if "headRefOid" in command or ".headRefOid" in command:
                    return subprocess.CompletedProcess(command, 1, "", "not found")
                if "headRefName" in command:
                    if "--jq" not in command:
                        return subprocess.CompletedProcess(command, 0, json.dumps({"headRefName": "refactor/iter77-worker"}), "")
                    return subprocess.CompletedProcess(command, 0, "refactor/iter77-worker\n", "")
                return subprocess.CompletedProcess(command, 0, "OPEN\n", "")
            if command[:2] == ["gh", "api"]:
                endpoint = str(command[2]) if len(command) > 2 else ""
                if endpoint == "user":
                    return subprocess.CompletedProcess(command, 0, json.dumps({"login": "current-user"}), "")
                if endpoint.startswith("repos/owner/repo/collaborators/") and endpoint.endswith("/permission"):
                    return subprocess.CompletedProcess(command, 0, json.dumps({"permission": "write"}), "")
                if "/pulls/" in endpoint:
                    return subprocess.CompletedProcess(command, 0, json.dumps({"state": "open"}), "")
                return subprocess.CompletedProcess(command, 0, "{}", "")
            if command[:2] == ["git", "-C"] and command[3:] == ["worktree", "list", "--porcelain"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    f"worktree {self.repo}\nbranch refs/heads/auto-refact-dev\n\n"
                    f"worktree {worktree}\nbranch refs/heads/refactor/iter77-worker\n\n",
                    "",
                )
            if command[:2] == ["git", "-C"] and command[3:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, " M skills/x.py\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        actions = FakeActions()
        runner = WakeupRunner(
            self.ctx,
            plan_loader=lambda _repo: self.base_plan(action),
            actions=actions,
            supervisor=self.supervisor,
            command_runner=command_runner,
        )

        results = runner.run_once()

        self.assertEqual(results[0].status, "applied")
        self.assertEqual([call[0] for call in actions.calls], ["safe_push", "dispatch_reviewers"])

    def test_publish_review_fix_applied_row_suppresses_after_clean_worktree(self) -> None:
        action = self.reviewer_dispatch_action(
            action_id="publish-review-fix-output:77:clean",
            controller_action="publish_review_fix_output_from_action",
            target_number=77,
            head_sha="a" * 40,
        )
        (self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl").write_text(
            json.dumps({"action_id": action["action_id"], "status": "applied", "reason": "", "kind": "completed-marker"})
            + "\n",
            encoding="utf-8",
        )
        worktree = self.repo / ".worktrees" / "iter77-worker"
        worktree.mkdir(parents=True, exist_ok=True)
        actions = FakeActions()

        results = self.run_result(self.base_plan(action), implementation_status="", actions=actions)

        self.assertEqual(results[0].status, "skipped")
        self.assertEqual(results[0].reason, "duplicate")
        self.assertEqual(actions.calls, [])

    def test_publish_review_fix_applied_row_suppresses_after_live_head_advanced(self) -> None:
        action = self.reviewer_dispatch_action(
            action_id="publish-review-fix-output:77:advanced",
            controller_action="publish_review_fix_output_from_action",
            target_number=77,
            head_sha="a" * 40,
        )
        (self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl").write_text(
            json.dumps({"action_id": action["action_id"], "status": "applied", "reason": "", "kind": "completed-marker"})
            + "\n",
            encoding="utf-8",
        )
        actions = FakeActions()

        def command_runner(command):
            if command[:3] == ["gh", "pr", "view"] and ("headRefOid" in command or ".headRefOid" in command):
                return subprocess.CompletedProcess(command, 0, "b" * 40 + "\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        runner = WakeupRunner(
            self.ctx,
            plan_loader=lambda _repo: self.base_plan(action),
            actions=actions,
            supervisor=self.supervisor,
            command_runner=command_runner,
        )

        results = runner.run_once()

        self.assertEqual(results[0].status, "skipped")
        self.assertEqual(results[0].reason, "duplicate")
        self.assertEqual(actions.calls, [])

    def test_publish_implementation_output_routes_to_named_helper(self) -> None:
        actions = FakeActions()

        results = self.run_result(
            self.base_plan(self.implementation_output_action()),
            git_diff_code=0,
            implementation_status="M  staged.py\n",
            actions=actions,
        )

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "publish_implementation_output")

    def test_publish_implementation_output_accepts_duplicate_log_marker_with_valid_artifact_marker(self) -> None:
        actions = FakeActions()
        action = self.implementation_output_action()
        source_log = self.repo / action["source_artifact"]
        source_log.write_text(
            "IMPLEMENT_DONE:issue-77:partial\n"
            "worker output\n"
            "IMPLEMENT_DONE:issue-77:ok\n"
            "EXIT=0\n",
            encoding="utf-8",
        )
        (self.repo / ".refactor-loop/runs/implement-issue77.md").write_text(
            "summary\n⟦AI:AUTO-LOOP⟧\nIMPLEMENT_DONE:issue-77:ok\n",
            encoding="utf-8",
        )

        results = self.run_result(
            self.base_plan(action),
            git_diff_code=1,
            implementation_status="M  staged.py\n",
            actions=actions,
        )

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "publish_implementation_output")

    def test_publish_implementation_output_blocks_duplicate_log_marker_without_valid_artifact_marker(self) -> None:
        cases = (
            ("missing", None),
            ("multiple", "IMPLEMENT_DONE:issue-77:ok\nIMPLEMENT_DONE:issue-78:ok\n"),
            ("blocked", "IMPLEMENT_DONE:issue-77:blocked\n"),
        )
        for name, artifact_text in cases:
            with self.subTest(name=name):
                actions = FakeActions()
                action = self.implementation_output_action(action_id=f"publish-implementation:duplicate-{name}")
                source_log = self.repo / action["source_artifact"]
                source_log.write_text(
                    "IMPLEMENT_DONE:issue-77:partial\n"
                    "worker output\n"
                    "IMPLEMENT_DONE:issue-77:ok\n"
                    "EXIT=0\n",
                    encoding="utf-8",
                )
                artifact = self.repo / ".refactor-loop/runs/implement-issue77.md"
                if artifact_text is None:
                    artifact.unlink(missing_ok=True)
                else:
                    artifact.write_text(artifact_text, encoding="utf-8")

                results = self.run_result(
                    self.base_plan(action),
                    git_diff_code=1,
                    implementation_status="M  staged.py\n",
                    actions=actions,
                )

                self.assert_blocked_before_dispatch(
                    results,
                    f"publish-implementation:duplicate-{name}",
                    "clean_exit_marker_missing",
                    actions,
                )

    def test_publish_implementation_output_blocks_before_helper_without_g3_preconditions(self) -> None:
        cases = (
            (
                "bad-marker",
                self.implementation_output_action(
                    action_id="publish-implementation:bad-marker",
                    source_marker="IMPLEMENT_DONE:issue-77:partial",
                ),
                "clean_exit_marker_missing",
                1,
                None,
            ),
            (
                "missing-host-checks",
                self.implementation_output_action(
                    action_id="publish-implementation:missing-host-checks",
                    preconditions=[
                        "active_controller_owner",
                        "clean_exit_source_marker",
                        "canonical_implementation_identity",
                        "fresh_integration_base",
                        "clean_scoped_diff",
                        "single_linked_managed_issue",
                        "worker_authored_pr_artifacts",
                        "no_conflicting_open_implementation_pr",
                    ],
                ),
                "publish_implementation_missing_precondition:host_checks_green",
                1,
                None,
            ),
            (
                "not-managed",
                self.implementation_output_action(action_id="publish-implementation:not-managed"),
                "publish_implementation_target_not_managed",
                1,
                None,
            ),
        )
        for name, action, reason, git_diff_code, duplicate_prs in cases:
            with self.subTest(name=name):
                actions = FakeActions()
                gh_labels = [] if name == "not-managed" else None
                results = self.run_result(
                    self.base_plan(action),
                    git_diff_code=git_diff_code,
                    implementation_status="M  staged.py\n" if name != "empty-diff" else "",
                    duplicate_prs=duplicate_prs,
                    gh_labels=gh_labels,
                    actions=actions,
                )
                self.assert_blocked_before_dispatch(results, action["action_id"], reason, actions)

    def test_publish_implementation_empty_scoped_diff_is_skipped_not_hard_gate(self) -> None:
        actions = FakeActions()
        action = self.implementation_output_action(action_id="publish-implementation:empty-diff")

        results = self.run_result(
            self.base_plan(action),
            git_diff_code=0,
            implementation_status="",
            actions=actions,
        )

        self.assertEqual(results[0].status, "skipped")
        self.assertEqual(results[0].reason, "publish_implementation_empty_scoped_diff")
        self.assertEqual(actions.calls, [])
        pending_path = self.repo / ".refactor-loop/.controller-pending-events.log"
        pending = pending_path.read_text(encoding="utf-8") if pending_path.exists() else ""
        self.assertNotIn("WAKEUP_RUNNER_BLOCKED:publish-implementation:empty-diff", pending)
        ledger_rows = [
            json.loads(line)
            for line in (self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(ledger_rows[-1]["status"], "skipped")
        self.assertEqual(ledger_rows[-1]["reason"], "publish_implementation_empty_scoped_diff")

    def test_publish_implementation_output_blocks_before_helper_without_worker_pr_artifacts(self) -> None:
        actions = FakeActions()
        action = self.implementation_output_action(
            action_id="publish-implementation:missing-pr-artifacts",
            title_file=".refactor-loop/runs/missing-title.txt",
            body_file=".refactor-loop/runs/missing-body.md",
        )

        results = self.run_result(self.base_plan(action), git_diff_code=1, implementation_status="M  staged.py\n", actions=actions)

        self.assert_blocked_before_dispatch(
            results,
            "publish-implementation:missing-pr-artifacts",
            "publish_implementation_title_artifact_missing",
            actions,
        )

    def test_publish_implementation_output_blocks_before_helper_for_malformed_worker_pr_artifacts(self) -> None:
        title = self.repo / ".refactor-loop" / "runs" / "implementation-pr-issue-77-title.txt"
        body = self.repo / ".refactor-loop" / "runs" / "implementation-pr-issue-77-body.md"
        valid_body = (
            "## Changed files\n\n- skills/consensus-loop/scripts/codex_refactor_loop/wakeup_runner.py\n\n"
            "## Test results\n\n- python3 skills/consensus-loop/scripts/test_wakeup_runner.py\n\n"
            "## Deviations\n\n- none\n\n"
            "Closes #77\n\n"
            "⟦AI:AUTO-LOOP⟧\n"
        )
        outside = self.repo / "outside-title.txt"
        outside.write_text("完成 issue #77 的发布契约\n", encoding="utf-8")
        outside_body = self.repo / "outside-body.md"
        outside_body.write_text(valid_body, encoding="utf-8")
        cases = (
            ("outside-title-path", {"title_file": str(outside)}, None, "publish_implementation_title_artifact_invalid_path"),
            ("outside-body-path", {"body_file": str(outside_body)}, None, "publish_implementation_body_artifact_invalid_path"),
            ("placeholder-title", {}, lambda: title.write_text("实现 issue #77\n", encoding="utf-8"), "publish_implementation_title_placeholder"),
            ("multiline-title", {}, lambda: title.write_text("完成 issue #77\n第二行\n", encoding="utf-8"), "publish_implementation_title_artifact_invalid"),
            ("body-content-title", {}, lambda: title.write_text("Closes #77\n", encoding="utf-8"), "publish_implementation_title_contains_body_content"),
            ("sentinel-title", {}, lambda: title.write_text("⟦AI:AUTO-LOOP⟧\n", encoding="utf-8"), "publish_implementation_title_contains_body_content"),
            ("missing-sentinel", {}, lambda: body.write_text(valid_body.replace("\n⟦AI:AUTO-LOOP⟧\n", "\n"), encoding="utf-8"), "publish_implementation_body_sentinel_missing"),
            ("sentinel-not-final", {}, lambda: body.write_text(valid_body + "extra\n", encoding="utf-8"), "publish_implementation_body_sentinel_missing"),
            ("wrong-closes", {}, lambda: body.write_text(valid_body.replace("Closes #77", "Closes #78"), encoding="utf-8"), "publish_implementation_body_closes_mismatch"),
            ("multiple-closes", {}, lambda: body.write_text(valid_body.replace("Closes #77", "Closes #77\nCloses #78"), encoding="utf-8"), "publish_implementation_body_closes_mismatch"),
            ("missing-closes", {}, lambda: body.write_text(valid_body.replace("Closes #77\n\n", ""), encoding="utf-8"), "publish_implementation_body_closes_mismatch"),
            ("missing-section", {}, lambda: body.write_text(valid_body.replace("## Changed files", "## files"), encoding="utf-8"), "publish_implementation_body_required_section_missing"),
            ("placeholder-body", {}, lambda: body.write_text("## issue #77 实现\n\n## Changed files\n\n- x\n\n## Test results\n\n- true\n\n## Deviations\n\n- none\n\nCloses #77\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8"), "publish_implementation_body_placeholder"),
        )
        for name, overrides, mutate, reason in cases:
            with self.subTest(name=name):
                actions = FakeActions()
                action = self.implementation_output_action(action_id=f"publish-implementation:{name}", **overrides)
                if mutate is not None:
                    mutate()
                results = self.run_result(
                    self.base_plan(action),
                    git_diff_code=1,
                    implementation_status="M  staged.py\n",
                    actions=actions,
                )
                self.assert_blocked_before_dispatch(results, action["action_id"], reason, actions)

    def test_publish_implementation_output_allows_existing_open_pr_for_helper_reuse(self) -> None:
        actions = FakeActions()
        action = self.implementation_output_action(action_id="publish-implementation:existing-pr")

        results = self.run_result(
            self.base_plan(action),
            git_diff_code=1,
            implementation_status="M  staged.py\n",
            duplicate_prs=[
                {
                    "number": 99,
                    "baseRefName": "auto-refact-dev",
                    "headRefName": "refactor/iter77-issue-77",
                    "labels": [{"name": labels.MANAGED}],
                    "body": "Closes #77\n",
                }
            ],
            actions=actions,
        )

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "publish_implementation_output")

    def test_wakeup_runner_source_locks_publish_refresh_needed_and_matching_pr_contract(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "wakeup_runner.py").read_text(encoding="utf-8")
        publish_validator = source[source.index("    def _validate_publish_implementation") : source.index("    def _validate_dispatch_reviewers")]
        worktree_validator = source[source.index("    def _validate_implementation_worktree") : source.index("    def _validate_canonical_implementation_identity")]
        self.assertNotIn("publish_implementation_stale_base", publish_validator + worktree_validator)
        self.assertNotIn("merge-base", publish_validator + worktree_validator)
        self.assertNotIn("def _validate_no_duplicate_open_pr", source)
        self.assertIn('["git", "-C", str(worktree), "status", "--porcelain"]', worktree_validator)

    def test_dispatch_consensus_implementation_revalidates_durable_artifact_before_helper(self) -> None:
        actions = FakeActions()
        action = self.consensus_action()

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "dispatch_consensus_implementation")

    def test_issue_decomposition_apply_revalidates_digest_live_parent_and_dispatches_existing_helper(self) -> None:
        actions = FakeActions()
        action = self.issue_decomposition_action()

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls, [("apply_issue_decomposition_plan", ".refactor-loop/runs/decomposition-plan.json")])

    def test_issue_decomposition_apply_rejects_partial_implement_source_marker(self) -> None:
        actions = FakeActions()
        action = self.issue_decomposition_action(
            action_id="completed-marker:implement-issue-403.log:IMPLEMENT_DONE:issue-403:partial:apply_issue_decomposition_plan",
            source_artifact=".refactor-loop/logs/implement-issue-403.log",
            source_marker="IMPLEMENT_DONE:issue-403:partial",
        )
        (self.repo / action["source_artifact"]).write_text(
            "worker wrote a validated IssueDecompositionPlan\n"
            "IMPLEMENT_DONE:issue-403:partial\n"
            "EXIT=0\n",
            encoding="utf-8",
        )

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assert_blocked_before_dispatch(
            results,
            "completed-marker:implement-issue-403.log:IMPLEMENT_DONE:issue-403:partial:apply_issue_decomposition_plan",
            "issue_decomposition_plan_level_judge_source_mismatch",
            actions,
        )

    def test_issue_decomposition_private_kind_dialect_fails_closed(self) -> None:
        cases = (
            ("issue-decomposition-apply", "unsupported_kind:issue-decomposition-apply"),
            ("decompose-apply", "unsupported_kind:decompose-apply"),
        )
        for kind, reason in cases:
            with self.subTest(kind=kind):
                actions = FakeActions()
                action = self.issue_decomposition_action(kind=kind, action_id=f"decompose:private-kind:{kind}")

                results = self.run_result(self.base_plan(action), actions=actions)

                self.assert_blocked_before_dispatch(results, f"decompose:private-kind:{kind}", reason, actions)

    def test_issue_decomposition_digest_and_proof_mismatch_fail_closed(self) -> None:
        for reason, overrides in (
            ("issue_decomposition_digest_mismatch", {"issue_decomposition_plan_digest": "0" * 64}),
            ("issue_decomposition_proof_mismatch", {"issue_decomposition_proof": "wrong proof"}),
        ):
            with self.subTest(reason=reason):
                actions = FakeActions()
                action = self.issue_decomposition_action(action_id=f"decompose:{reason}", **overrides)

                results = self.run_result(self.base_plan(action), actions=actions)

                self.assert_blocked_before_dispatch(results, f"decompose:{reason}", reason, actions)

    def test_issue_decomposition_plan_level_judge_source_mismatch_fails_closed(self) -> None:
        actions = FakeActions()
        action = self.issue_decomposition_action(
            action_id="decompose:plan-level-source-mismatch",
            source_artifact=".refactor-loop/logs/implement-issue-403.log",
            source_marker="IMPLEMENT_DONE:issue-403:partial",
        )
        (self.repo / action["source_artifact"]).write_text("IMPLEMENT_DONE:issue-403:partial\nEXIT=0\n", encoding="utf-8")

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assert_blocked_before_dispatch(
            results,
            "decompose:plan-level-source-mismatch",
            "issue_decomposition_plan_level_judge_source_mismatch",
            actions,
        )

    def test_issue_decomposition_parent_closed_or_unmanaged_fails_closed(self) -> None:
        closed_actions = FakeActions()
        closed = self.issue_decomposition_action(action_id="decompose:closed-parent")
        closed_results = self.run_result(self.base_plan(closed), gh_state="CLOSED", actions=closed_actions)
        self.assert_blocked_before_dispatch(closed_results, "decompose:closed-parent", "target_not_open:CLOSED", closed_actions)

        unmanaged_actions = FakeActions()
        unmanaged = self.issue_decomposition_action(action_id="decompose:unmanaged-parent")
        unmanaged_results = self.run_result(self.base_plan(unmanaged), gh_labels=[], actions=unmanaged_actions)
        self.assert_blocked_before_dispatch(
            unmanaged_results,
            "decompose:unmanaged-parent",
            "issue_decomposition_parent_not_managed",
            unmanaged_actions,
        )

    def test_issue_decomposition_single_parent_sentinel_skips_and_multiple_hits_fail_closed(self) -> None:
        base = self.issue_decomposition_action()
        digest = base["issue_decomposition_plan_digest"]
        single_actions = FakeActions()
        single = self.issue_decomposition_action(action_id="decompose:single-sentinel")
        comments = [{"body": f"tracked\nIssueDecompositionPlan digest: {digest}\n"}]

        single_results = self.run_result(self.base_plan(single), actions=single_actions, issue_comments=comments)

        self.assertEqual(single_results[0].status, "skipped")
        self.assertEqual(single_results[0].reason, "issue_decomposition_duplicate_sentinel")
        self.assertEqual(single_actions.calls, [])

        multiple_actions = FakeActions()
        multiple = self.issue_decomposition_action(action_id="decompose:multiple-sentinels")
        multiple_results = self.run_result(self.base_plan(multiple), actions=multiple_actions, issue_comments=comments * 2)

        self.assert_blocked_before_dispatch(
            multiple_results,
            "decompose:multiple-sentinels",
            "issue_decomposition_multiple_sentinels",
            multiple_actions,
        )

    def test_dispatch_consensus_implementation_blocks_precondition_string_only_projection(self) -> None:
        actions = FakeActions()
        action = self.consensus_action(
            action_id="consensus:string-only",
            preconditions=["active_controller_owner", "live_open_target", "consensus_artifact_present"],
            consensus_artifact="",
            design_decision_path="",
            scope_paths="",
            old_pattern="",
            new_principle="",
        )

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assert_blocked_before_dispatch(
            results,
            "consensus:string-only",
            "consensus_implementation_missing_precondition:durable_consensus_artifact",
            actions,
        )

    def test_dispatch_consensus_implementation_blocks_missing_required_projection_field(self) -> None:
        actions = FakeActions()
        action = self.consensus_action(action_id="consensus:missing-scope", scope_paths="")

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assert_blocked_before_dispatch(
            results,
            "consensus:missing-scope",
            "consensus_implementation_missing_field:scope_paths",
            actions,
        )

    def test_dispatch_consensus_implementation_blocks_design_path_mismatch(self) -> None:
        actions = FakeActions()
        action = self.consensus_action(
            action_id="consensus:design-path-mismatch",
            design_decision_path=".refactor-loop/runs/other.md",
        )

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assert_blocked_before_dispatch(
            results,
            "consensus:design-path-mismatch",
            "consensus_implementation_design_path_mismatch",
            actions,
        )

    def test_dispatch_consensus_implementation_blocks_invalid_durable_artifact(self) -> None:
        outside = self.repo / "outside-consensus.md"
        outside.write_text("META_JUDGE_DONE:consensus:structural\n", encoding="utf-8")
        bad_basename = self.repo / ".refactor-loop/runs/xphase9-issue20-r5-judge.md"
        bad_basename.write_text("META_JUDGE_DONE:consensus:structural\n", encoding="utf-8")
        cases = [
            (
                "outside-runs",
                lambda: self.consensus_action(
                    action_id="consensus:outside-runs",
                    consensus_artifact="outside-consensus.md",
                    design_decision_path="outside-consensus.md",
                ),
                "consensus_artifact_outside_runs",
            ),
            (
                "target-mismatch",
                lambda: self.consensus_action(action_id="consensus:target-mismatch", target_number=21),
                "consensus_artifact_target_mismatch",
            ),
            (
                "identity-mismatch",
                lambda: self.consensus_action(action_id="consensus:identity-mismatch", consensus_round=6),
                "consensus_artifact_identity_mismatch",
            ),
            (
                "bad-basename",
                lambda: self.consensus_action(
                    action_id="consensus:bad-basename",
                    consensus_artifact=".refactor-loop/runs/xphase9-issue20-r5-judge.md",
                    design_decision_path=".refactor-loop/runs/xphase9-issue20-r5-judge.md",
                ),
                "consensus_artifact_identity_mismatch",
            ),
            (
                "missing-marker",
                lambda: self.consensus_action(action_id="consensus:missing-marker"),
                "consensus_artifact_marker_missing",
            ),
        ]
        for name, action_factory, reason in cases:
            with self.subTest(name=name):
                action = action_factory()
                if name == "missing-marker":
                    (self.repo / ".refactor-loop/runs/phase9-issue20-r5-judge.md").write_text(
                        "no consensus marker\n",
                        encoding="utf-8",
                    )
                actions = FakeActions()
                results = self.run_result(self.base_plan(action), actions=actions)
                self.assert_blocked_before_dispatch(results, action["action_id"], reason, actions)

    def test_dispatch_consensus_implementation_redispatches_markerless_implement_log(self) -> None:
        actions = FakeActions()
        action = self.consensus_action(action_id="consensus:existing-log")
        (self.repo / ".refactor-loop/logs/implement-issue-20.log").write_text("worker finished without marker\nEXIT=0\n", encoding="utf-8")

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "dispatch_consensus_implementation")

    def test_dispatch_consensus_implementation_blocks_inflight_implement_log(self) -> None:
        actions = FakeActions()
        action = self.consensus_action(action_id="consensus:inflight-log")
        log = self.repo / ".refactor-loop/logs/implement-issue-20.log"
        log.write_text("worker still running\n", encoding="utf-8")

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assert_blocked_before_dispatch(results, action["action_id"], "consensus_implementation_not_ready:in_flight_implement", actions)
        self.assertTrue(log.exists())

    def test_failed_consensus_implementation_redispatch_clears_log_and_spawn_launches(self) -> None:
        failed_log = self.repo / ".refactor-loop/logs/implement-issue-20.log"
        failed_log.write_text("old failed run\nEXIT=1\n", encoding="utf-8")
        worktree = self.repo / ".worktrees" / "iter20-issue-20"

        class ClearingActions(FakeActions):
            def __init__(inner_self, ctx: LoopContext) -> None:
                super().__init__()
                inner_self.ctx = ctx

            def dispatch_consensus_implementation(inner_self, action: dict) -> int:
                inner_self.calls.append(("dispatch_consensus_implementation", dict(action)))
                failed_log.unlink(missing_ok=True)
                prompt = inner_self.ctx.paths.prompts / "implement-issue-20.md"
                prompt.write_text("fresh prompt\n", encoding="utf-8")
                inner_self.ctx.paths.pending_events.write_text(
                    "2026-06-01T00:00:00Z HARNESS_SPAWN_INTENT "
                    + json.dumps(
                        {
                            "intent_id": "dispatch-consensus-implementation:20",
                            "source": "controller-actions",
                            "route": "dispatch-consensus-implementation",
                            "task_id": "implement-issue-20",
                            "priority": "p1",
                            "command": "spawn-codex",
                            "controller_action": "spawn_codex_harness_background",
                            "cd": str(worktree.resolve()),
                            "prompt": ".refactor-loop/prompts/implement-issue-20.md",
                            "log": ".refactor-loop/logs/implement-issue-20.log",
                            "stall": 5400,
                            "reason": "issue #20 consensus implementation",
                            "run_in_background_required": True,
                            "no_lifecycle_authority": True,
                        },
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return 0

        actions = ClearingActions(self.ctx)
        dispatch_action = self.consensus_action(action_id="consensus:failed-log")
        dispatch_results = self.run_result(self.base_plan(dispatch_action), actions=actions)

        self.assertEqual(dispatch_results[0].status, "applied")
        self.assertFalse(failed_log.exists())

        spawn_plan = self.base_plan(
            {
                "kind": "harness-spawn-intent",
                "action_id": "harness-spawn-intent:dispatch-consensus-implementation:20",
                "runner_authority": "wakeup-runner-396",
                "preconditions": ["active_controller_owner", "source_artifact_contains_evidence", "target_log_absent"],
                "source_artifact": ".refactor-loop/.controller-pending-events.log",
                "source_marker": "HARNESS_SPAWN_INTENT",
                "target_kind": "codex",
                "target_number": None,
                "target": {"kind": "codex", "task_id": "implement-issue-20"},
                "controller_action": "spawn_codex_harness_background",
                "no_generic_command": True,
                "cd": str(worktree),
                "prompt": str(self.repo / ".refactor-loop/prompts/implement-issue-20.md"),
                "log": str(failed_log),
                "stall": 5400,
            }
        )
        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            spawn_results = self.run_result(spawn_plan, actions=FakeActions())

        self.assertEqual(spawn_results[0].status, "applied")
        launch.assert_called_once()
        self.assertEqual(Path(launch.call_args.kwargs["log"]).resolve(), failed_log.resolve())

    def test_spawn_apply_clears_terminal_markerless_implement_log_before_launch(self) -> None:
        log = self.repo / ".refactor-loop/logs/implement-issue-20.log"
        log.write_text("old terminal markerless run\nEXIT=0\n", encoding="utf-8")
        action = self.spawn_action(
            action_id="spawn:implement-issue-20",
            target={"kind": "codex", "task_id": "implement-issue-20"},
            log=str(log),
        )

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.base_plan(action), actions=FakeActions())

        self.assertEqual(results[0].status, "applied")
        self.assertFalse(log.exists())
        launch.assert_called_once()

    def test_spawn_apply_preserves_terminal_blocked_implement_log(self) -> None:
        log = self.repo / ".refactor-loop/logs/implement-issue-20.log"
        log.write_text("IMPLEMENT_DONE:issue-20:blocked\nEXIT=0\n", encoding="utf-8")
        actions = FakeActions()
        action = self.spawn_action(
            action_id="spawn:implement-issue-20",
            target={"kind": "codex", "task_id": "implement-issue-20"},
            log=str(log),
        )

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.base_plan(action), actions=actions)

        self.assert_blocked_before_dispatch(results, action["action_id"], "target_log_exists", actions)
        self.assertTrue(log.exists())
        launch.assert_not_called()

    def test_spawn_apply_preserves_inflight_implement_log(self) -> None:
        log = self.repo / ".refactor-loop/logs/implement-issue-20.log"
        log.write_text("worker still running\n", encoding="utf-8")
        actions = FakeActions()
        action = self.spawn_action(
            action_id="spawn:implement-issue-20",
            target={"kind": "codex", "task_id": "implement-issue-20"},
            log=str(log),
        )

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.base_plan(action), actions=actions)

        self.assert_blocked_before_dispatch(results, action["action_id"], "target_log_exists", actions)
        self.assertTrue(log.exists())
        launch.assert_not_called()

    def test_publish_ready_implementation_routes_to_publish_helper(self) -> None:
        actions = FakeActions()

        results = self.run_result(
            self.base_plan(self.implementation_output_action()),
            actions=actions,
            git_diff_code=1,
            implementation_status="M  staged.py\n",
        )

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "publish_implementation_output")

    def test_clean_implementation_on_stale_base_routes_to_publish_helper_for_fallback(self) -> None:
        actions = FakeActions()

        results = self.run_result(
            self.base_plan(self.implementation_output_action()),
            actions=actions,
            git_diff_code=1,
            implementation_status="M  staged.py\n",
            implementation_base=("old-base", "new-base"),
        )

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "publish_implementation_output")

    def test_publish_implementation_output_allows_missing_matching_pr_before_helper(self) -> None:
        actions = FakeActions()

        def command_runner(command):
            if command[:2] == ["gh", "api"]:
                endpoint = str(command[2]) if len(command) > 2 else ""
                if "/issues/" in endpoint:
                    return subprocess.CompletedProcess(command, 0, json.dumps({"state": "open"}), "")
                return subprocess.CompletedProcess(command, 0, "{}", "")
            if command[:3] == ["gh", "issue", "view"]:
                return subprocess.CompletedProcess(command, 0, json.dumps({"labels": [{"name": labels.MANAGED}], "body": ""}), "")
            if command[:4] == ["gh", "pr", "list", "--state"]:
                return subprocess.CompletedProcess(command, 0, "[]", "")
            if command[:3] == ["git", "-C", str(self.repo / ".worktrees" / "iter77-issue-77")]:
                if command[3:] == ["rev-parse", "--abbrev-ref", "HEAD"]:
                    return subprocess.CompletedProcess(command, 0, "refactor/iter77-issue-77\n", "")
                if command[3:] == ["status", "--porcelain"]:
                    return subprocess.CompletedProcess(command, 0, "M  staged.py\n", "")
                if command[3:] == ["diff", "HEAD", "--quiet"]:
                    return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        runner = WakeupRunner(
            self.ctx,
            plan_loader=lambda _repo: self.base_plan(self.implementation_output_action()),
            actions=actions,
            supervisor=self.supervisor,
            command_runner=command_runner,
        )

        results = runner.run_once()

        self.assertEqual(results[0].status, "applied")
        self.assertEqual([call[0] for call in actions.calls], ["publish_implementation_output"])

    def test_dispatch_reviewers_routes_to_named_helper_after_pr_target_validation(self) -> None:
        actions = FakeActions()

        results = self.run_result(self.base_plan(self.reviewer_dispatch_action()), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "dispatch_reviewers")

    def test_review_evidence_redispatch_routes_to_named_helper_after_pr_target_validation(self) -> None:
        actions = FakeActions()
        action = self.reviewer_dispatch_action(
            kind="review-evidence-redispatch",
            action_id="review-evidence-redispatch:77:" + "a" * 40,
            source_artifact="wakeup-plan",
            source_marker="review-evidence-redispatch",
            head_sha="b" * 40,
            stale_review_roles=["architect", "tests"],
            preconditions=[
                "active_controller_owner",
                "live_open_target_if_present",
                "missing_or_stale_reviewer_head_evidence",
            ],
        )

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "dispatch_reviewers")
        self.assertEqual(actions.calls[0][1]["stale_review_roles"], ["architect", "tests"])

    def test_stale_review_dispatch_applied_row_retries_when_evidence_still_stale(self) -> None:
        for role in ("architect", "tests"):
            (self.repo / ".refactor-loop/prompts" / f"review-pr77-{role}-r1.md").write_text(
                "reviewed-head-sha: " + "b" * 40 + "\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/runs" / f"review-pr77-{role}-r1.md").write_text(
                f"---\nverdict: approve\n---\nREVIEW_DONE:77:{role}:approve\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/logs" / f"review-pr77-{role}-r1.log").write_text(
                f"REVIEW_DONE:77:{role}:approve\nEXIT=0\n",
                encoding="utf-8",
            )
        action = self.reviewer_dispatch_action(
            kind="review-evidence-redispatch",
            action_id="review-evidence-redispatch:77:" + "a" * 40,
            source_artifact="wakeup-plan",
            source_marker="review-evidence-redispatch",
            head_sha="a" * 40,
            stale_review_roles=["architect", "tests"],
            preconditions=[
                "active_controller_owner",
                "live_open_target_if_present",
                "missing_or_stale_reviewer_head_evidence",
            ],
        )
        (self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl").write_text(
            json.dumps({"action_id": action["action_id"], "status": "applied", "reason": "", "kind": "review-evidence-redispatch"})
            + "\n",
            encoding="utf-8",
        )
        actions = FakeActions()

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "dispatch_reviewers")

    def test_stale_review_dispatch_applied_row_suppresses_after_target_roles_current(self) -> None:
        for role in ("architect", "tests"):
            (self.repo / ".refactor-loop/prompts" / f"review-pr77-{role}-r2.md").write_text(
                "reviewed-head-sha: " + "a" * 40 + "\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/runs" / f"review-pr77-{role}-r2.md").write_text(
                f"---\nverdict: approve\n---\nREVIEW_DONE:77:{role}:approve\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop/logs" / f"review-pr77-{role}-r2.log").write_text(
                f"REVIEW_DONE:77:{role}:approve\nEXIT=0\n",
                encoding="utf-8",
            )
        action = self.reviewer_dispatch_action(
            kind="review-evidence-redispatch",
            action_id="review-evidence-redispatch:77:" + "a" * 40,
            source_artifact="wakeup-plan",
            source_marker="review-evidence-redispatch",
            head_sha="a" * 40,
            stale_review_roles=["architect", "tests"],
            preconditions=[
                "active_controller_owner",
                "live_open_target_if_present",
                "missing_or_stale_reviewer_head_evidence",
            ],
        )
        (self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl").write_text(
            json.dumps({"action_id": action["action_id"], "status": "applied", "reason": "", "kind": "review-evidence-redispatch"})
            + "\n",
            encoding="utf-8",
        )
        actions = FakeActions()

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual(results[0].status, "skipped")
        self.assertEqual(results[0].reason, "duplicate")
        self.assertEqual(actions.calls, [])

    def test_stale_review_dispatch_applied_row_suppresses_after_live_head_advanced(self) -> None:
        action = self.reviewer_dispatch_action(
            kind="review-evidence-redispatch",
            action_id="review-evidence-redispatch:77:" + "a" * 40,
            source_artifact="wakeup-plan",
            source_marker="review-evidence-redispatch",
            head_sha="a" * 40,
            stale_review_roles=["architect", "tests"],
            preconditions=[
                "active_controller_owner",
                "live_open_target_if_present",
                "missing_or_stale_reviewer_head_evidence",
            ],
        )
        (self.repo / ".refactor-loop/state/wakeup-runner-ledger.jsonl").write_text(
            json.dumps({"action_id": action["action_id"], "status": "applied", "reason": "", "kind": "review-evidence-redispatch"})
            + "\n",
            encoding="utf-8",
        )
        actions = FakeActions()

        def command_runner(command):
            if command[:3] == ["gh", "pr", "view"] and ".headRefOid" in command:
                return subprocess.CompletedProcess(command, 0, "b" * 40 + "\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        runner = WakeupRunner(
            self.ctx,
            plan_loader=lambda _repo: self.base_plan(action),
            actions=actions,
            supervisor=self.supervisor,
            command_runner=command_runner,
        )

        results = runner.run_once()

        self.assertEqual(results[0].status, "skipped")
        self.assertEqual(results[0].reason, "duplicate")
        self.assertEqual(actions.calls, [])

    def test_dispatch_reviewers_blocks_missing_or_non_pr_target_before_helper(self) -> None:
        cases = (
            (
                "missing-target",
                self.reviewer_dispatch_action(
                    action_id="dispatch-reviewers:missing-target",
                    target_kind=None,
                    target_number=None,
                    target=None,
                ),
                "dispatch_reviewers_target_missing",
            ),
            (
                "issue-target",
                self.reviewer_dispatch_action(
                    action_id="dispatch-reviewers:issue-target",
                    target_kind="issue",
                    target_number=77,
                    target={"kind": "issue", "number": 77},
                ),
                "dispatch_reviewers_target_missing",
            ),
        )
        for name, action, reason in cases:
            with self.subTest(name=name):
                actions = FakeActions()
                results = self.run_result(self.base_plan(action), actions=actions)
                self.assert_blocked_before_dispatch(results, action["action_id"], reason, actions)

    def test_fix_done_dispatch_commits_and_pushes_fix_output_before_review(self) -> None:
        # Headless gap fix: a FIX_DONE re-review must first commit+push the fix
        # codex's uncommitted worktree output, else reviewers re-review the stale
        # head forever and the reject never converges.
        worktree = self.repo / ".worktrees" / "iter77-issue-77"
        worktree.mkdir(parents=True, exist_ok=True)
        seen: list[list[str]] = []

        def command_runner(command):
            cmd = [str(part) for part in command]
            seen.append(cmd)
            if cmd[:3] == ["gh", "pr", "view"]:
                return subprocess.CompletedProcess(cmd, 0, '{"headRefName": "refactor/iter77-issue-77"}', "")
            if "status" in cmd and "--porcelain" in cmd:
                return subprocess.CompletedProcess(cmd, 0, " M skills/x.py\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        actions = FakeActions()
        runner = WakeupRunner(self.ctx, actions=actions, command_runner=command_runner)
        with mock.patch.object(runner, "_review_fix_worktree", return_value=worktree):
            rc = runner._dispatch("dispatch_reviewers", self.reviewer_dispatch_action(target_number=77))

        self.assertEqual(rc, 0)
        git_subcmds = [cmd[3] for cmd in seen if cmd[0] == "git" and len(cmd) > 3]
        self.assertIn("add", git_subcmds)
        self.assertIn("commit", git_subcmds)
        self.assertEqual(len([call for call in actions.calls if call[0] == "safe_push"]), 1)
        self.assertEqual(actions.calls[-1][0], "dispatch_reviewers")

    def test_fix_done_dispatch_clean_worktree_skips_commit_and_reviews(self) -> None:
        # A clean fix worktree (no uncommitted changes) is a no-op: re-review
        # directly without an empty commit or push.
        worktree = self.repo / ".worktrees" / "iter77-issue-77"
        worktree.mkdir(parents=True, exist_ok=True)
        seen: list[list[str]] = []

        def command_runner(command):
            cmd = [str(part) for part in command]
            seen.append(cmd)
            if cmd[:3] == ["gh", "pr", "view"]:
                return subprocess.CompletedProcess(cmd, 0, '{"headRefName": "refactor/iter77-issue-77"}', "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        actions = FakeActions()
        runner = WakeupRunner(self.ctx, actions=actions, command_runner=command_runner)
        with mock.patch.object(runner, "_review_fix_worktree", return_value=worktree):
            rc = runner._dispatch("dispatch_reviewers", self.reviewer_dispatch_action(target_number=77))

        self.assertEqual(rc, 0)
        git_subcmds = [cmd[3] for cmd in seen if cmd[0] == "git" and len(cmd) > 3]
        self.assertNotIn("commit", git_subcmds)
        self.assertEqual([call for call in actions.calls if call[0] == "safe_push"], [])
        self.assertEqual(actions.calls[-1][0], "dispatch_reviewers")

    def test_release_rollup_routes_to_named_helper_after_event_body_validation(self) -> None:
        actions = FakeActions()

        results = self.run_result(self.base_plan(self.release_rollup_action()), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "open_release_rollup_pr_from_action")

    def test_release_rollup_current_open_pr_suppresses_second_tick_retry(self) -> None:
        first_actions = FakeActions()
        action = self.release_rollup_action()

        first = self.run_result(self.base_plan(action), actions=first_actions)

        self.assertEqual(first[0].status, "applied")
        self.assertEqual([call[0] for call in first_actions.calls], ["open_release_rollup_pr_from_action"])
        second_actions = FakeActions()
        second = self.run_result(
            self.base_plan(action),
            actions=second_actions,
            open_rollup_prs=[
                {
                    "number": 123,
                    "headRefName": "rollup/abc123",
                    "headRefOid": "abc123",
                }
            ],
        )

        self.assertEqual(
            [(result.action_id, result.status, result.reason) for result in second],
            [(action["action_id"], "skipped", "duplicate")],
        )
        self.assertEqual(second_actions.calls, [])

    def test_release_rollup_body_spawn_renders_prompt_and_does_not_open_pr(self) -> None:
        action = self.release_rollup_body_action()
        actions = FakeActions()

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "render_release_rollup_body_prompt")
        self.assertEqual(len(actions.calls), 1)
        launch.assert_called_once()
        self.assertFalse((self.repo / ".refactor-loop/runs/release-rollup-pr-body.md").exists())

    def test_release_rollup_body_spawn_blocks_when_body_already_exists(self) -> None:
        body = self.repo / ".refactor-loop/runs/release-rollup-pr-body.md"
        body.write_text("existing\n", encoding="utf-8")
        actions = FakeActions()

        results = self.run_result(self.base_plan(self.release_rollup_body_action()), actions=actions)

        self.assert_blocked_before_dispatch(results, "release-rollup-body:abc123", "release_rollup_body_exists", actions)

    def test_release_rollup_body_spawn_blocks_invalid_narrow_allowlist_inputs_before_dispatch(self) -> None:
        cases = (
            (
                "missing-event-precondition",
                {"preconditions": ["active_controller_owner", "source_artifact_contains_evidence", "target_log_absent", "target_body_absent"]},
                "release_rollup_body_missing_precondition:release_rollup_event",
            ),
            (
                "missing-body-absent-precondition",
                {"preconditions": ["active_controller_owner", "source_artifact_contains_evidence", "release_rollup_event", "target_log_absent"]},
                "release_rollup_body_missing_precondition:target_body_absent",
            ),
            ("missing-event", {"event": None}, "release_rollup_body_event_missing"),
            ("blank-integration-sha", {"event": {"integration_sha": "   "}}, "release_rollup_body_integration_sha_missing"),
            ("body-outside-runs", {"body_file": ".refactor-loop/state/release-rollup-pr-body.md"}, "release_rollup_body_output_outside_runs"),
            (
                "prompt-mismatch",
                {"prompt": str(self.repo / ".refactor-loop/prompts/other.md")},
                "release_rollup_body_prompt_mismatch",
            ),
        )
        for name, overrides, reason in cases:
            with self.subTest(name=name):
                actions = FakeActions()
                action = self.release_rollup_body_action(action_id=f"release-rollup-body:{name}", **overrides)

                results = self.run_result(self.base_plan(action), actions=actions)

                self.assert_blocked_before_dispatch(results, action["action_id"], reason, actions)

    def test_implementation_pr_artifact_repair_spawn_renders_prompt_and_does_not_publish(self) -> None:
        action = self.implementation_pr_artifact_repair_action()
        actions = FakeActions()

        with mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", return_value=0) as launch:
            results = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "render_implementation_pr_artifact_repair_prompt")
        self.assertEqual(len(actions.calls), 1)
        launch.assert_called_once()
        self.assertFalse((self.repo / ".refactor-loop/runs/implementation-pr-issue-77-title.txt").exists())
        self.assertFalse((self.repo / ".refactor-loop/runs/implementation-pr-issue-77-body.md").exists())

    def test_implementation_pr_artifact_repair_spawn_blocks_invalid_narrow_allowlist_inputs_before_dispatch(self) -> None:
        cases = (
            (
                "missing-artifact-precondition",
                {
                    "preconditions": [
                        "active_controller_owner",
                        "clean_exit_source_marker",
                        "target_log_absent",
                        "publish_implementation_output_status_only",
                    ]
                },
                "implementation_pr_artifact_repair_missing_precondition:implementation_pr_artifacts_missing_or_invalid",
            ),
            ("missing-issue", {"issue_number": None}, "implementation_pr_artifact_repair_issue_missing"),
            ("bad-cluster", {"cluster_id": "../bad"}, "implementation_pr_artifact_repair_cluster_invalid"),
            ("title-outside-runs", {"title_file": ".refactor-loop/state/implementation-pr-issue-77-title.txt"}, "implementation_pr_artifact_repair_title_file_outside_runs"),
            ("body-mismatch", {"body_file": ".refactor-loop/runs/other-body.md"}, "implementation_pr_artifact_repair_body_file_mismatch"),
            (
                "prompt-mismatch",
                {"prompt": str(self.repo / ".refactor-loop/prompts/other.md")},
                "implementation_pr_artifact_repair_prompt_mismatch",
            ),
        )
        for name, overrides, reason in cases:
            with self.subTest(name=name):
                actions = FakeActions()
                action = self.implementation_pr_artifact_repair_action(
                    action_id=f"implementation-pr-artifacts:{name}",
                    **overrides,
                )

                results = self.run_result(self.base_plan(action), actions=actions)

                self.assert_blocked_before_dispatch(results, action["action_id"], reason, actions)

    def test_release_rollup_blocks_missing_event_fields_before_helper(self) -> None:
        body = self.repo / ".refactor-loop/runs/release-rollup-pr-body.md"
        cases = (
            (
                "missing-precondition",
                self.release_rollup_action(
                    action_id="rollup:missing-precondition",
                    preconditions=["active_controller_owner", "source_artifact_contains_evidence"],
                ),
                "release_rollup_missing_precondition:release_rollup_event",
            ),
            (
                "missing-event",
                self.release_rollup_action(action_id="rollup:missing-event", event=None),
                "release_rollup_event_missing",
            ),
            (
                "missing-sha",
                self.release_rollup_action(action_id="rollup:missing-sha", event={"integration_sha": ""}),
                "release_rollup_integration_sha_missing",
            ),
            (
                "missing-body",
                self.release_rollup_action(action_id="rollup:missing-body", body_file=".refactor-loop/runs/missing-rollup.md"),
                "release_rollup_body_missing",
            ),
        )
        self.assertTrue(body.is_file())
        for name, action, reason in cases:
            with self.subTest(name=name):
                actions = FakeActions()
                results = self.run_result(self.base_plan(action), actions=actions)
                self.assert_blocked_before_dispatch(results, action["action_id"], reason, actions)

    def test_rollup_auto_merge_routes_to_named_helper_after_narrow_validation(self) -> None:
        actions = FakeActions()

        results = self.run_result(self.base_plan(self.rollup_auto_merge_action()), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "auto_merge_release_rollup_pr_from_action")

    def test_rollup_auto_merge_blocks_non_rollup_or_wrong_base_before_dispatch(self) -> None:
        cases = (
            ("wrong-head", {"head_ref": "refactor/iter88-work"}, "rollup_auto_merge_invalid_head_ref"),
            ("wrong-base", {"base_ref": "auto-refact-dev"}, "rollup_auto_merge_base_mismatch"),
            (
                "missing-check-precondition",
                {"preconditions": ["active_controller_owner", "live_open_target", "rollup_head_prefix", "review_base_target", "rollup_auto_merge_enabled"]},
                "rollup_auto_merge_missing_precondition:required_checks_green_exact_head",
            ),
        )
        for name, overrides, reason in cases:
            with self.subTest(name=name):
                actions = FakeActions()
                action = self.rollup_auto_merge_action(action_id=f"rollup-auto:{name}", **overrides)

                results = self.run_result(self.base_plan(action), actions=actions)

                self.assert_blocked_before_dispatch(results, action["action_id"], reason, actions)

    def test_wakeup_runner_continues_after_blocked_non_spawn_lifecycle_action(self) -> None:
        blocked_lifecycle = self.implementation_output_action(
            action_id="publish-implementation:missing-verified-head-before-reviewer-dispatch",
            head_ref="",
        )
        reviewer_dispatch = self.reviewer_dispatch_action(action_id="dispatch-reviewers-after-blocked-lifecycle")
        actions = FakeActions()

        results = self.run_result(
            self.batch_plan([blocked_lifecycle, reviewer_dispatch], dispatch_required=1, deficit=1),
            actions=actions,
        )

        self.assertEqual([result.action_id for result in results], [blocked_lifecycle["action_id"], reviewer_dispatch["action_id"]])
        self.assertEqual([result.status for result in results], ["blocked", "applied"])
        self.assertEqual([call[0] for call in actions.calls], ["dispatch_reviewers"])
        self.assert_blocked_ledger(blocked_lifecycle["action_id"], "publish_implementation_invalid_head_ref")

    def test_close_managed_item_from_drop_marker_routes_to_named_helper(self) -> None:
        actions = FakeActions()
        action = self.spawn_action(
            kind="completed-marker",
            action_id="completed-marker:judge.log:META_RESOLVED:drop:no-action",
            controller_action="close_managed_item_from_drop_marker",
            source_artifact="drop-marker-artifact",
            source_marker="META_RESOLVED:drop:no-action",
            target_kind="issue",
            target_number=53,
            target={"kind": "issue", "number": 53},
            preconditions=["active_controller_owner", "live_open_target", "live_managed_target"],
        )

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual(actions.calls[0][0], "close_managed_item_from_drop_marker")

    def test_phase9_reflector_drop_log_routes_to_close_helper_with_clean_marker_evidence(self) -> None:
        actions = FakeActions()
        marker = "META_RESOLVED:drop:no-actionable-framing-after-4-rounds"
        log = self.repo / ".refactor-loop/logs/phase9-issue554-r4-reflector.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            "raw prose is diagnostic only\n"
            f"{marker}\n"
            "EXIT=0\n"
            "DONE_AT=2026-06-06T02:33:09Z\n",
            encoding="utf-8",
        )
        action = self.close_action(
            action_id="completed-marker:phase9-issue554-r4-reflector.log:" + marker,
            source_artifact=".refactor-loop/logs/phase9-issue554-r4-reflector.log",
            source_marker=marker,
            target_number=554,
            target={"kind": "issue", "number": 554},
        )

        results = self.run_result(self.base_plan(action), actions=actions)

        self.assertEqual(results[0].status, "applied")
        self.assertEqual([call[0] for call in actions.calls], ["close_managed_item_from_drop_marker"])
        helper_action = actions.calls[0][1]
        self.assertEqual(helper_action["source_artifact"], ".refactor-loop/logs/phase9-issue554-r4-reflector.log")
        self.assertEqual(helper_action["source_marker"], marker)
        self.assertEqual(helper_action["target_number"], 554)

    def test_zero_code_implementation_completion_routes_to_close_helper_after_empty_diff_revalidation(self) -> None:
        actions = FakeActions()
        marker = "IMPLEMENT_DONE:issue-77:ok"
        action = self.close_action(
            action_id="completed-marker:implement-issue-77.log:" + marker,
            preconditions=[
                "active_controller_owner",
                "clean_exit_source_marker",
                "live_open_target",
                "live_managed_target",
                "zero_code_implementation_completion",
            ],
            source_artifact=".refactor-loop/logs/implement-issue-77.log",
            source_marker=marker,
            target_number=77,
            target={"kind": "issue", "number": 77},
            zero_code_completion_proof=self.write_zero_code_completion_artifacts(issue=77),
        )
        log = self.repo / ".refactor-loop/logs/implement-issue-77.log"
        log.write_text("worker artifact: 0 LOC no source changes\n" + marker + "\nEXIT=0\n", encoding="utf-8")
        (self.repo / ".worktrees" / "iter77-issue-77").mkdir(parents=True, exist_ok=True)

        results = self.run_result(
            self.base_plan(action),
            git_diff_code=0,
            implementation_status="",
            implementation_issue=77,
            actions=actions,
        )

        self.assertEqual(results[0].status, "applied")
        self.assertEqual([call[0] for call in actions.calls], ["close_managed_item_from_drop_marker"])
        helper_action = actions.calls[0][1]
        self.assertEqual(helper_action["source_marker"], marker)
        self.assertIn("zero_code_implementation_completion", helper_action["preconditions"])

    def test_zero_code_implementation_close_blocks_when_diff_not_empty(self) -> None:
        actions = FakeActions()
        marker = "IMPLEMENT_DONE:issue-77:ok"
        action = self.close_action(
            action_id="completed-marker:implement-issue-77.log:" + marker,
            preconditions=[
                "active_controller_owner",
                "clean_exit_source_marker",
                "live_open_target",
                "live_managed_target",
                "zero_code_implementation_completion",
            ],
            source_artifact=".refactor-loop/logs/implement-issue-77.log",
            source_marker=marker,
            target_number=77,
            target={"kind": "issue", "number": 77},
            zero_code_completion_proof=self.write_zero_code_completion_artifacts(issue=77),
        )
        log = self.repo / ".refactor-loop/logs/implement-issue-77.log"
        log.write_text(marker + "\nEXIT=0\n", encoding="utf-8")
        (self.repo / ".worktrees" / "iter77-issue-77").mkdir(parents=True, exist_ok=True)

        results = self.run_result(
            self.base_plan(action),
            git_diff_code=1,
            implementation_status="M  changed.py\n",
            implementation_issue=77,
            actions=actions,
        )

        self.assert_blocked_before_dispatch(
            results,
            "completed-marker:implement-issue-77.log:" + marker,
            "close_managed_drop_zero_code_not_empty_scoped_diff:publish_ready:",
            actions,
        )

    def test_close_managed_item_from_drop_marker_blocks_non_managed_open_target_before_helper(self) -> None:
        actions = FakeActions()
        action = self.close_action(action_id="close-managed-item:non-managed")

        results = self.run_result(self.base_plan(action), gh_labels=[], actions=actions)

        self.assert_blocked_before_dispatch(
            results,
            "close-managed-item:non-managed",
            "close_managed_drop_target_not_managed",
            actions,
        )

    def test_idempotency_ledger_suppresses_duplicate_apply(self) -> None:
        plan = self.base_plan(self.spawn_action())
        with mock.patch("codex_refactor_loop.processes.subprocess.Popen") as popen:
            first = self.run_result(plan)
        (self.repo / ".refactor-loop/logs/task.log").write_text("SPAWN\n", encoding="utf-8")
        second = self.run_result(plan)

        self.assertEqual(first[0].status, "applied")
        self.assertEqual(second[0].status, "skipped")
        popen.assert_called_once()

    def test_wakeup_runner_tick_status_line_format(self) -> None:
        out = StringIO()
        with redirect_stdout(out):
            _log_tick_status("wakeup-runner", "dispatched spawn:1")

        self.assertRegex(
            out.getvalue().strip(),
            r"^\[[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\] wakeup-runner: tick dispatched spawn:1$",
        )

    def test_reconcile_tick_wrapper_preserves_wakeup_runner_results(self) -> None:
        runner = mock.Mock(spec=WakeupRunner)
        runner.run_once.return_value = [RunnerResult("action-1", "applied")]

        self.assertEqual([RunnerResult("action-1", "applied")], run_wakeup_runner_reconcile_tick(runner))
        runner.run_once.assert_called_once_with()

    def test_refactor_loop_host_env_is_not_production_topology_ssot(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "wakeup_runner.py").read_text(encoding="utf-8")

        self.assertNotIn('".refactor-loop/host.env"', source)
        self.assertIn("LoopContext.load", source)

    def test_close_managed_item_source_regression_revalidates_managed_label_before_dispatch(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "wakeup_runner.py").read_text(encoding="utf-8")
        method = source[source.index("    def _validate_close_managed_drop") : source.index("    def _validate_review_gate")]
        self.assertIn('"live_managed_target"', method)
        self.assertIn('"labels,body"', source)
        self.assertIn("labels.normalize_label_set", source)
        self.assertIn("labels.MANAGED", source)
        self.assertNotIn('"crnd:lifecycle:managed"', method)
        self.assertLess(source.index("return self._validate_close_managed_drop(action)"), source.index("return self.actions.close_managed_item_from_drop_marker(dict(action))"))

    def test_plan_file_is_dry_run_only(self) -> None:
        plan_path = self.repo / "plan.json"
        plan_path.write_text(json.dumps(self.base_plan(self.spawn_action())), encoding="utf-8")

        exit_code = wakeup_runner_main(["--once", "--repo-root", str(self.repo), "--plan-file", str(plan_path)])

        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
