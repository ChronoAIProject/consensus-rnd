#!/usr/bin/env python3
"""Behavior tests for concurrency_monitor dispatch queue auto-topup."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
CLI = SCRIPT_DIR / "consensus-rnd-cli"


class ConcurrencyMonitorDispatchQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.old_env = os.environ.copy()
        os.environ.pop("CONSENSUS_RND_HOST_ENV", None)
        os.environ["REPO_ROOT"] = str(self.repo)
        os.environ["CODEX_FLOOR"] = "2"
        os.environ.pop("CONSENSUS_RND_HOST_ENV", None)
        from codex_refactor_loop.context import LoopContext
        from codex_refactor_loop import labels as label_catalog
        from codex_refactor_loop.monitors import concurrency as concurrency_module
        from codex_refactor_loop.monitors.concurrency import ConcurrencyMonitor
        from codex_refactor_loop.wakeup_plan import harness_spawn_intent_line_digest
        self.module = concurrency_module
        self.harness_spawn_intent_line_digest = harness_spawn_intent_line_digest
        self.labels = label_catalog
        self.ctx = LoopContext.load(repo_root=self.repo, env=os.environ)
        self.monitor = ConcurrencyMonitor(self.ctx)
        self.refactor_loop = self.repo / ".refactor-loop"

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)
        self.tmp.cleanup()

    def reload_monitor(self):
        self.ctx = self.ctx.__class__.load(repo_root=self.repo, env=os.environ)
        self.monitor = self.monitor.__class__(self.ctx)
        return self.monitor

    def write_dispatch(
        self,
        priority: str,
        task_id: str,
        reason: str | None = None,
        *,
        include_task_id: bool = True,
        cd: Path | None = None,
    ) -> Path:
        priority_dir = self.refactor_loop / "dispatch-queue" / priority
        priority_dir.mkdir(parents=True, exist_ok=True)
        prompt = self.refactor_loop / "prompts" / f"{task_id}.md"
        log = self.refactor_loop / "logs" / f"{task_id}.log"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        log.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("prompt\n", encoding="utf-8")
        if cd is None:
            cd = self.repo if task_id.startswith(self.module.MAIN_READONLY_DISPATCH_PREFIXES) else self.repo / ".worktrees" / task_id
        payload = {
            "cd": str(cd),
            "prompt": str(prompt),
            "log": str(log),
            "stall": 5400,
            "queued_at": "2026-05-26T07:25:00Z",
            "reason": reason or f"{task_id} needed",
        }
        if include_task_id:
            payload["task_id"] = task_id
        path = priority_dir / f"{task_id}.dispatch.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def fake_popen(self, calls: list[list[str]]):
        def _fake_popen(cmd: list[str], **_: object) -> object:
            calls.append(cmd)
            return object()

        return _fake_popen

    def harness_spawn_intents(self) -> list[dict]:
        events = self.refactor_loop / ".controller-pending-events.log"
        if not events.exists():
            return []
        intents = []
        for line in events.read_text(encoding="utf-8").splitlines():
            if " HARNESS_SPAWN_INTENT " not in line:
                continue
            intents.append(json.loads(line.split(" HARNESS_SPAWN_INTENT ", 1)[1]))
        return intents

    def append_phase9_harness_spawn_intent(
        self,
        task_id: str,
        *,
        ts: str = "2026-05-26T07:29:00Z",
        intent_id: str | None = None,
        log: str | None = None,
        source: str = "phase9-router",
    ) -> dict[str, object]:
        intent = {
            "intent_id": intent_id or f"phase9-router:{task_id}",
            "source": source,
            "route": "solver_triplet_to_judge",
            "task_id": task_id,
            "priority": "p1",
            "command": "spawn-codex",
            "controller_action": "spawn_codex_harness_background",
            "cd": str(self.repo),
            "prompt": f".refactor-loop/prompts/phase9/{task_id}.md",
            "log": log or f".refactor-loop/logs/{task_id}.log",
            "stall": 5400,
            "reason": f"issue #{task_id.split('-r', 1)[0].removeprefix('phase9-issue')} dispatch",
            "queued_at": ts,
            "run_in_background_required": True,
            "no_lifecycle_authority": True,
        }
        pending = self.refactor_loop / ".controller-pending-events.log"
        pending.parent.mkdir(parents=True, exist_ok=True)
        with pending.open("a", encoding="utf-8") as handle:
            handle.write(f"{ts} HARNESS_SPAWN_INTENT {json.dumps(intent, sort_keys=True)}\n")
        return intent

    def design_issue_item(self, issue: int) -> dict:
        return {
            "number": issue,
            "kind": "issue",
            "phase": self.labels.PHASE_DESIGN_SOLVING,
            "human": self.labels.HUMAN_AUTO,
            "labels": [self.labels.MANAGED, self.labels.PHASE_DESIGN_SOLVING, self.labels.HUMAN_AUTO],
            "body": "",
            "head_ref": "",
            "is_draft": False,
            "state": "open",
        }

    def write_applied_issue_decomposition_evidence(
        self,
        *,
        issue: int = 537,
        round_no: int = 6,
        marker: str = "META_JUDGE_DONE:consensus:decompose:real",
        ledger_rows: tuple[tuple[str, str], ...] = (("applied", ""),),
    ) -> tuple[str, str]:
        from codex_refactor_loop.context import LoopContext
        from codex_refactor_loop.issue_decomposition import issue_decomposition_plan_file_digest

        runs = self.refactor_loop / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        consensus = f".refactor-loop/runs/phase9-issue{issue}-r{round_no}-judge.md"
        child_one = f".refactor-loop/runs/issue-{issue}-decomposition/child-one.md"
        child_two = f".refactor-loop/runs/issue-{issue}-decomposition/child-two.md"
        for path, scope, non_goals in (
            (child_one, "First bounded scope", "No parent lifecycle mutation"),
            (child_two, "Second bounded scope", "No public issue factory"),
        ):
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text(
                "## child\n\n"
                f"Parent issue: #{issue}\n"
                f"Source consensus artifact: {Path(consensus).name}\n"
                f"Scope: {scope}\n"
                f"Non-goals: {non_goals}\n\n"
                "<details>\n<summary>内联 artifact 1: decision.md</summary>\n\n"
                "```markdown\nraw decision\n```\n\n</details>\n\n"
                "⟦AI:AUTO-LOOP⟧\n",
                encoding="utf-8",
            )
        parent_comment = f".refactor-loop/runs/issue-{issue}-decomposition/parent-comment.md"
        (self.repo / parent_comment).write_text(f"Parent issue: #{issue}\n\nChildren opened.\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        plan_path = f".refactor-loop/runs/issue-{issue}-decomposition/plan.json"
        (self.repo / plan_path).write_text(
            json.dumps(
                {
                    "schema": "IssueDecompositionPlan",
                    "parent_issue": issue,
                    "source_consensus_artifact": consensus,
                    "children": [
                        {
                            "slug": "first-child",
                            "title": "First child",
                            "scope": "First bounded scope",
                            "non_goals": "No parent lifecycle mutation",
                            "body_artifact_path": child_one,
                        },
                        {
                            "slug": "second-child",
                            "title": "Second child",
                            "scope": "Second bounded scope",
                            "non_goals": "No public issue factory",
                            "body_artifact_path": child_two,
                        },
                    ],
                    "parent_update": {"comment_artifact_path": parent_comment},
                }
            ),
            encoding="utf-8",
        )
        ctx = LoopContext.load(repo_root=self.repo, env=os.environ)
        digest = issue_decomposition_plan_file_digest(ctx, plan_path)
        (self.repo / consensus).write_text("META_JUDGE_DONE:consensus:decompose\n", encoding="utf-8")
        ledger = self.refactor_loop / "state" / "wakeup-runner-ledger.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        action_id = f"completed-marker:phase9-issue{issue}-r{round_no}-judge.log:{marker}"
        ledger.write_text(
            "".join(
                json.dumps(
                    {
                        "action_id": action_id,
                        "kind": "completed-marker",
                        "reason": reason,
                        "status": status,
                    },
                    sort_keys=True,
                )
                + "\n"
                for status, reason in ledger_rows
            ),
            encoding="utf-8",
        )
        return plan_path, digest

    def assert_dispatch_rejected(self, task_id: str, reason: str, calls: list[list[str]]) -> None:
        self.assertEqual(calls, [])
        self.assertFalse((self.refactor_loop / "dispatch-queue" / "p0" / f"{task_id}.dispatch.json").exists())
        rejected = self.refactor_loop / "dispatch-rejected" / f"{task_id}.json"
        self.assertTrue(rejected.exists())
        payload = json.loads(rejected.read_text(encoding="utf-8"))
        self.assertEqual(payload["reject_reason"], reason)
        self.assertEqual(payload["priority"], "p0")
        self.assertIn("source_dispatch_file", payload)
        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn(f"DISPATCH_REJECTED:{task_id}:p0:main-worktree-cd:{reason}", events)

    def test_monitor_dispatches_from_queue_when_below_floor(self) -> None:
        self.write_dispatch("p1", "fix-pr44-round-3")
        self.write_dispatch("p1", "audit-iter-5")
        calls: list[list[str]] = []

        with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=0):
                self.monitor.top_up_from_dispatch_queue(actual=0, floor=2)

        self.assertEqual(calls, [])
        self.assertEqual(len(self.harness_spawn_intents()), 2)
        self.assertEqual(list((self.refactor_loop / "dispatch-queue" / "p1").glob("*.dispatch.json")), [])
        archived = sorted((self.refactor_loop / "dispatch-dispatched").glob("*.json"))
        self.assertEqual([p.name for p in archived], ["audit-iter-5.json", "fix-pr44-round-3.json"])

    def test_dispatch_queue_rejects_high_risk_payload_and_continues_to_low(self) -> None:
        unsafe = self.write_dispatch("p0", "unsafe-task")
        payload = json.loads(unsafe.read_text(encoding="utf-8"))
        payload["argv"] = ["gh", "issue", "close", "1"]
        unsafe.write_text(json.dumps(payload), encoding="utf-8")
        self.write_dispatch("p0", "audit-iter-5")

        with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=0):
            self.monitor.top_up_from_dispatch_queue(actual=0, floor=2)

        self.assertEqual([intent["task_id"] for intent in self.harness_spawn_intents()], ["audit-iter-5"])
        rejected = self.refactor_loop / "dispatch-rejected" / "unsafe-task.json"
        self.assertTrue(rejected.exists())
        rejected_payload = json.loads(rejected.read_text(encoding="utf-8"))
        self.assertIn("forbidden_fields:argv", rejected_payload["reject_reason"])
        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("DISPATCH_REJECTED:unsafe-task:p0:safe-progress:forbidden_fields:argv", events)
        self.assertIn("DISPATCH_INTENT:audit-iter-5:p0", events)

    def test_dispatch_queue_limits_medium_payloads_per_tick(self) -> None:
        self.write_dispatch("p0", "fix-pr44-round-1")
        self.write_dispatch("p0", "fix-pr45-round-1")
        self.write_dispatch("p0", "audit-iter-5")

        with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=0):
            self.monitor.top_up_from_dispatch_queue(actual=0, floor=3)

        intents = self.harness_spawn_intents()
        self.assertEqual(["audit-iter-5", "fix-pr44-round-1"], sorted(intent["task_id"] for intent in intents))
        self.assertTrue((self.refactor_loop / "dispatch-queue" / "p0" / "fix-pr45-round-1.dispatch.json").exists())
        dispatched_fix = json.loads((self.refactor_loop / "dispatch-dispatched" / "fix-pr44-round-1.json").read_text(encoding="utf-8"))
        self.assertEqual("medium", dispatched_fix["risk_tier"])
        self.assertEqual("cautious", dispatched_fix["execution_policy"])

    # Refactor (impl/issue191-single-active-controller): Old pattern: any
    # device-local concurrency monitor could dispatch and archive queue entries.
    # New principle: non-owner monitors preserve queue files and write no
    # DISPATCH_INTENT side effect.
    def test_non_owner_does_not_dispatch_archive_or_write_dispatch_fired(self) -> None:
        dispatch = self.write_dispatch("p1", "fix-pr44-round-3")
        calls: list[list[str]] = []
        decision = mock.Mock(allowed=False, owner_device="device-a", status="not-owner", action="concurrency-dispatch", lease_id="", expires_at="")

        with mock.patch("codex_refactor_loop.monitors.concurrency.require_active_controller", return_value=decision):
            with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
                fired = self.monitor.dispatch_one_from_queue()

        self.assertIsNone(fired)
        self.assertEqual(calls, [])
        self.assertTrue(dispatch.exists())
        self.assertFalse((self.refactor_loop / "dispatch-dispatched").exists())
        events = self.refactor_loop / ".controller-pending-events.log"
        self.assertFalse(events.exists())

    def test_floor_remains_local_code_floor_without_cross_device_aggregation(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "monitors" / "concurrency.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("CODEX_FLOOR"', source)
        self.assertNotIn("REMOTE_CODEX_FLOOR", source)
        self.assertNotIn("cross_device_floor", source)

    def test_hotfix_prefix_is_not_a_mutable_dispatch_surface(self) -> None:
        from codex_refactor_loop import safe_progress_scheduler

        self.assertNotIn("hotfix-", self.module.MUTABLE_DISPATCH_PREFIXES)
        self.assertNotIn("hotfix-", safe_progress_scheduler.MUTABLE_DISPATCH_PATTERNS)

    def test_compute_expected_suppresses_empty_scoped_diff_implementation_completion(self) -> None:
        (self.refactor_loop / "logs").mkdir(parents=True, exist_ok=True)
        (self.repo / ".worktrees" / "iter581-issue-581").mkdir(parents=True)
        (self.refactor_loop / "logs" / "implement-issue-581.log").write_text(
            "no code change required\nIMPLEMENT_DONE:issue-581:ok\nEXIT=0\n",
            encoding="utf-8",
        )
        items = [
            {
                "number": 581,
                "kind": "issue",
                "phase": self.labels.PHASE_IMPLEMENTING,
                "human": self.labels.HUMAN_AUTO,
                "labels": [self.labels.MANAGED, self.labels.PHASE_IMPLEMENTING, self.labels.HUMAN_AUTO],
                "body": "",
                "head_ref": "",
                "is_draft": False,
                "state": "open",
            }
        ]

        def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            if command[-2:] == ["--abbrev-ref", "HEAD"]:
                return subprocess.CompletedProcess(command, 0, "refactor/iter581-issue-581\n", "")
            if command[-3:] == ["merge-base", "HEAD", "origin/integration"]:
                return subprocess.CompletedProcess(command, 0, "old-base\n", "")
            if command[-2:] == ["--verify", "origin/integration"]:
                return subprocess.CompletedProcess(command, 0, "new-base\n", "")
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[-2:] == ["diff", "--quiet"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 1, "", "unexpected command")

        expected, breakdown = self.monitor.compute_expected(
            items,
            integration_branch="integration",
            command_runner=runner,
        )

        self.assertEqual(expected, 0)
        self.assertEqual(breakdown, [])

    def test_compute_expected_suppresses_publish_ready_implementation_completion(self) -> None:
        (self.refactor_loop / "logs").mkdir(parents=True, exist_ok=True)
        (self.repo / ".worktrees" / "iter581-issue-581").mkdir(parents=True)
        (self.refactor_loop / "logs" / "implement-issue-581.log").write_text(
            "implementation ready for publish\nIMPLEMENT_DONE:issue-581:ok\nEXIT=0\n",
            encoding="utf-8",
        )
        items = [
            {
                "number": 581,
                "kind": "issue",
                "phase": self.labels.PHASE_IMPLEMENTING,
                "human": self.labels.HUMAN_AUTO,
                "labels": [self.labels.MANAGED, self.labels.PHASE_IMPLEMENTING, self.labels.HUMAN_AUTO],
                "body": "",
                "head_ref": "",
                "is_draft": False,
                "state": "open",
            }
        ]

        def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            if command[-2:] == ["--abbrev-ref", "HEAD"]:
                return subprocess.CompletedProcess(command, 0, "refactor/iter581-issue-581\n", "")
            if command[-3:] == ["merge-base", "HEAD", "origin/integration"]:
                return subprocess.CompletedProcess(command, 0, "base\n", "")
            if command[-2:] == ["--verify", "origin/integration"]:
                return subprocess.CompletedProcess(command, 0, "base\n", "")
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, "M  touched.py\n", "")
            return subprocess.CompletedProcess(command, 1, "", "unexpected command")

        expected, breakdown = self.monitor.compute_expected(
            items,
            integration_branch="integration",
            command_runner=runner,
        )

        self.assertEqual(expected, 0)
        self.assertEqual(breakdown, [])

    def test_compute_expected_suppresses_applied_decomposition_parent_tracking_issue(self) -> None:
        _plan_path, digest = self.write_applied_issue_decomposition_evidence(issue=537, round_no=6)

        def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            if command == ["gh", "issue", "view", "537", "--json", "comments"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps({"comments": [{"body": f"IssueDecompositionPlan digest: {digest}\n⟦AI:AUTO-LOOP⟧\n"}]}),
                    "",
                )
            return subprocess.CompletedProcess(command, 1, "", "unexpected command")

        expected, breakdown = self.monitor.compute_expected(
            [self.design_issue_item(537)],
            command_runner=runner,
        )

        self.assertEqual(expected, 0)
        self.assertEqual(breakdown, [])

    def test_compute_expected_suppresses_terminal_phase9_consensus_issue(self) -> None:
        (self.refactor_loop / "logs").mkdir(parents=True, exist_ok=True)
        (self.refactor_loop / "logs" / "phase9-issue620-r2-judge.log").write_text(
            "META_JUDGE_DONE:consensus:false-positive\nEXIT=0\n",
            encoding="utf-8",
        )

        expected, breakdown = self.monitor.compute_expected([self.design_issue_item(620)])

        self.assertEqual(expected, 0)
        self.assertEqual(breakdown, [])

    def test_compute_expected_suppresses_hybrid_applied_duplicate_decomposition_parent(self) -> None:
        _plan_path, digest = self.write_applied_issue_decomposition_evidence(
            issue=537,
            round_no=1,
            marker="META_JUDGE_DONE:consensus:hybrid-A+B:bounded decomposition only; stage0 first, later runtime/gate/headless cleanup as child design issues",
            ledger_rows=(("applied", ""), ("skipped", "duplicate")),
        )

        def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            if command == ["gh", "issue", "view", "537", "--json", "comments"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps({"comments": [{"body": f"IssueDecompositionPlan digest: {digest}\n⟦AI:AUTO-LOOP⟧\n"}]}),
                    "",
                )
            return subprocess.CompletedProcess(command, 1, "", "unexpected command")

        expected, breakdown = self.monitor.compute_expected(
            [self.design_issue_item(537)],
            command_runner=runner,
        )

        self.assertEqual(expected, 0)
        self.assertEqual(breakdown, [])

    def test_compute_expected_keeps_only_duplicate_decomposition_parent_tracking_issue(self) -> None:
        _plan_path, digest = self.write_applied_issue_decomposition_evidence(
            issue=537,
            round_no=6,
            ledger_rows=(("skipped", "duplicate"),),
        )

        def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            if command == ["gh", "issue", "view", "537", "--json", "comments"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps({"comments": [{"body": f"IssueDecompositionPlan digest: {digest}\n⟦AI:AUTO-LOOP⟧\n"}]}),
                    "",
                )
            return subprocess.CompletedProcess(command, 1, "", "unexpected command")

        expected, breakdown = self.monitor.compute_expected(
            [self.design_issue_item(537)],
            command_runner=runner,
        )

        self.assertEqual(expected, 1)
        self.assertEqual(
            breakdown,
            [{"id": "#537", "kind": "issue", "phase": self.labels.PHASE_DESIGN_SOLVING, "expected": 1}],
        )

    def test_compute_expected_keeps_unapplied_decomposition_parent_tracking_issue(self) -> None:
        self.write_applied_issue_decomposition_evidence(issue=537, round_no=6)
        (self.refactor_loop / "state" / "wakeup-runner-ledger.jsonl").unlink()

        def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            if command == ["gh", "issue", "view", "537", "--json", "comments"]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps({"comments": [{"body": "IssueDecompositionPlan digest: present-but-not-applied\n"}]}),
                    "",
                )
            return subprocess.CompletedProcess(command, 1, "", "unexpected command")

        expected, breakdown = self.monitor.compute_expected(
            [self.design_issue_item(537)],
            command_runner=runner,
        )

        self.assertEqual(expected, 1)
        self.assertEqual(
            breakdown,
            [{"id": "#537", "kind": "issue", "phase": self.labels.PHASE_DESIGN_SOLVING, "expected": 1}],
        )

    # Refactor (iter6/issue-133):
    #   Old pattern: concurrency_monitor passed queue payload[cd] straight to consensus-rnd-cli spawn-codex --cd, letting a mutable task run in the repo-root/main worktree
    #   New principle: structural consensus: dispatch queue mutable-prefix cwd guard, no shared workspace policy. See .refactor-loop/runs/phase9-issue133-r4-judge.md
    def test_rejects_mutable_dispatch_to_repo_root(self) -> None:
        self.write_dispatch("p0", "fix-pr44-round-3", cd=self.repo)
        calls: list[list[str]] = []

        with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            fired = self.monitor.dispatch_one_from_queue()

        self.assertIsNone(fired)
        self.assert_dispatch_rejected("fix-pr44-round-3", "repo-root-cd", calls)

    # Refactor (iter6/issue-133):
    #   Old pattern: concurrency_monitor passed queue payload[cd] straight to consensus-rnd-cli spawn-codex --cd, letting a mutable task run in the repo-root/main worktree
    #   New principle: structural consensus: dispatch queue mutable-prefix cwd guard, no shared workspace policy. See .refactor-loop/runs/phase9-issue133-r4-judge.md
    def test_rejects_dispatch_missing_cd(self) -> None:
        path = self.write_dispatch("p0", "fix-pr44-round-3")
        payload = json.loads(path.read_text(encoding="utf-8"))
        del payload["cd"]
        path.write_text(json.dumps(payload), encoding="utf-8")
        calls: list[list[str]] = []

        with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            fired = self.monitor.dispatch_one_from_queue()

        self.assertIsNone(fired)
        self.assert_dispatch_rejected("fix-pr44-round-3", "missing-cd", calls)

    # Refactor (iter6/issue-133):
    #   Old pattern: concurrency_monitor passed queue payload[cd] straight to consensus-rnd-cli spawn-codex --cd, letting a mutable task run in the repo-root/main worktree
    #   New principle: structural consensus: dispatch queue mutable-prefix cwd guard, no shared workspace policy. See .refactor-loop/runs/phase9-issue133-r4-judge.md
    def test_rejects_dispatch_relative_cd(self) -> None:
        self.write_dispatch("p0", "fix-pr44-round-3", cd=Path(".worktrees/fix-pr44-round-3"))
        calls: list[list[str]] = []

        with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            fired = self.monitor.dispatch_one_from_queue()

        self.assertIsNone(fired)
        self.assert_dispatch_rejected("fix-pr44-round-3", "relative-cd", calls)

    # Refactor (iter6/issue-133):
    #   Old pattern: concurrency_monitor passed queue payload[cd] straight to consensus-rnd-cli spawn-codex --cd, letting a mutable task run in the repo-root/main worktree
    #   New principle: structural consensus: dispatch queue mutable-prefix cwd guard, no shared workspace policy. See .refactor-loop/runs/phase9-issue133-r4-judge.md
    def test_rejects_dispatch_cd_outside_repo(self) -> None:
        outside_repo = self.repo.parent / "outside-repo"
        self.write_dispatch("p0", "fix-pr44-round-3", cd=outside_repo)
        calls: list[list[str]] = []

        with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            fired = self.monitor.dispatch_one_from_queue()

        self.assertIsNone(fired)
        self.assert_dispatch_rejected("fix-pr44-round-3", "outside-repo", calls)

    # Refactor (iter6/issue-133):
    #   Old pattern: concurrency_monitor passed queue payload[cd] straight to consensus-rnd-cli spawn-codex --cd, letting a mutable task run in the repo-root/main worktree
    #   New principle: structural consensus: dispatch queue mutable-prefix cwd guard, no shared workspace policy. See .refactor-loop/runs/phase9-issue133-r4-judge.md
    def test_rejects_mutable_dispatch_inside_repo_but_outside_worktrees(self) -> None:
        self.write_dispatch("p0", "fix-pr44-round-3", cd=self.repo / "tmp" / "fix-pr44-round-3")
        calls: list[list[str]] = []

        with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            fired = self.monitor.dispatch_one_from_queue()

        self.assertIsNone(fired)
        self.assert_dispatch_rejected("fix-pr44-round-3", "outside-worktrees", calls)

    # Refactor (iter6/issue-133):
    #   Old pattern: concurrency_monitor passed queue payload[cd] straight to consensus-rnd-cli spawn-codex --cd, letting a mutable task run in the repo-root/main worktree
    #   New principle: structural consensus: dispatch queue mutable-prefix cwd guard, no shared workspace policy. See .refactor-loop/runs/phase9-issue133-r4-judge.md
    def test_rejects_mutable_dispatch_to_worktrees_root(self) -> None:
        self.write_dispatch("p0", "fix-pr44-round-3", cd=self.repo / ".worktrees")
        calls: list[list[str]] = []

        with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            fired = self.monitor.dispatch_one_from_queue()

        self.assertIsNone(fired)
        self.assert_dispatch_rejected("fix-pr44-round-3", "worktrees-root-cd", calls)

    # Refactor (iter6/issue-133):
    #   Old pattern: concurrency_monitor passed queue payload[cd] straight to consensus-rnd-cli spawn-codex --cd, letting a mutable task run in the repo-root/main worktree
    #   New principle: structural consensus: dispatch queue mutable-prefix cwd guard, no shared workspace policy. See .refactor-loop/runs/phase9-issue133-r4-judge.md
    def test_accepts_mutable_dispatch_inside_worktrees(self) -> None:
        worktree = self.repo / ".worktrees" / "fix-pr44-round-3"
        self.write_dispatch("p0", "fix-pr44-round-3", cd=worktree)
        calls: list[list[str]] = []

        with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            fired = self.monitor.dispatch_one_from_queue()

        self.assertEqual(fired, ("fix-pr44-round-3", "p0", "fix-pr44-round-3 needed"))
        self.assertEqual(calls, [])
        intents = self.harness_spawn_intents()
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0]["command"], "spawn-codex")
        self.assertEqual(intents[0]["controller_action"], "spawn_codex_harness_background")
        self.assertEqual(intents[0]["cd"], ".worktrees/fix-pr44-round-3")
        self.assertNotIn("argv", intents[0])
        self.assertNotIn("shell", intents[0])
        self.assertTrue((self.refactor_loop / "dispatch-dispatched" / "fix-pr44-round-3.json").exists())
        self.assertFalse((self.refactor_loop / "dispatch-rejected").exists())

    # Refactor (iter6/issue-133):
    #   Old pattern: concurrency_monitor passed queue payload[cd] straight to consensus-rnd-cli spawn-codex --cd, letting a mutable task run in the repo-root/main worktree
    #   New principle: structural consensus: dispatch queue mutable-prefix cwd guard, no shared workspace policy. See .refactor-loop/runs/phase9-issue133-r4-judge.md
    def test_allows_main_readonly_dispatch_prefixes(self) -> None:
        task_ids = (
            "audit-iter-5",
            "phase9-issue133-r4-minimal",
            "solver-issue133-r4-delete",
            "meta-judge-issue133-r4",
            "review-pr44-tests",
            "reviewer-pr44-quality-r2",
        )

        for task_id in task_ids:
            with self.subTest(task_id=task_id):
                self.write_dispatch("p0", task_id, cd=self.repo)
                calls: list[list[str]] = []

                with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
                    fired = self.monitor.dispatch_one_from_queue()

                self.assertEqual(fired, (task_id, "p0", f"{task_id} needed"))
                self.assertEqual(calls, [])
                self.assertEqual(self.harness_spawn_intents()[-1]["intent_id"], f"dispatch:{task_id}")
                self.assertTrue((self.refactor_loop / "dispatch-dispatched" / f"{task_id}.json").exists())

    def test_rejects_main_readonly_prefix_near_misses_at_repo_root(self) -> None:
        task_ids = (
            "phase9-issueX-r4-minimal",
            "phase9-issue133-r4-architect",
            "solver-issue133-r4-judge",
            "meta-judge-issue133-round-4",
            "reviewish-pr44-tests",
        )
        for task_id in task_ids:
            with self.subTest(task_id=task_id):
                self.write_dispatch("p0", task_id, cd=self.repo)
                calls: list[list[str]] = []

                with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
                    fired = self.monitor.dispatch_one_from_queue()

                self.assertIsNone(fired)
                self.assert_dispatch_rejected(task_id, "repo-root-cd", calls)

    # Refactor (iter6/issue-133):
    #   Old pattern: concurrency_monitor passed queue payload[cd] straight to consensus-rnd-cli spawn-codex --cd, letting a mutable task run in the repo-root/main worktree
    #   New principle: structural consensus: dispatch queue mutable-prefix cwd guard, no shared workspace policy. See .refactor-loop/runs/phase9-issue133-r4-judge.md
    def test_rejected_dispatch_does_not_block_next_queue_item(self) -> None:
        self.write_dispatch("p0", "fix-pr44-round-3", cd=self.repo)
        self.write_dispatch("p0", "fix-pr44-round-4", cd=self.repo / ".worktrees" / "fix-pr44-round-4")
        calls: list[list[str]] = []

        with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            fired = self.monitor.dispatch_one_from_queue()

        self.assertEqual(fired, ("fix-pr44-round-4", "p0", "fix-pr44-round-4 needed"))
        self.assertEqual(calls, [])
        self.assertEqual(len(self.harness_spawn_intents()), 1)
        self.assertTrue((self.refactor_loop / "dispatch-rejected" / "fix-pr44-round-3.json").exists())
        self.assertTrue((self.refactor_loop / "dispatch-dispatched" / "fix-pr44-round-4.json").exists())
        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("DISPATCH_REJECTED:fix-pr44-round-3:p0:main-worktree-cd:repo-root-cd", events)
        self.assertIn("DISPATCH_INTENT:fix-pr44-round-4:p0:fix-pr44-round-4 needed", events)

    def test_monitor_respects_priority_order(self) -> None:
        self.write_dispatch("p2", "p2-task")
        self.write_dispatch("p1", "p1-task")
        self.write_dispatch("p0", "p0-task")
        calls: list[list[str]] = []

        with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            self.monitor.dispatch_one_from_queue()

        self.assertEqual(calls, [])
        self.assertEqual(self.harness_spawn_intents()[0]["prompt"], ".refactor-loop/prompts/p0-task.md")
        self.assertFalse((self.refactor_loop / "dispatch-queue" / "p0" / "p0-task.dispatch.json").exists())
        self.assertTrue((self.refactor_loop / "dispatch-queue" / "p1" / "p1-task.dispatch.json").exists())

    def test_monitor_does_not_overshoot_floor(self) -> None:
        for i in range(5):
            self.write_dispatch("p1", f"task-{i}")
        calls: list[list[str]] = []

        with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            self.monitor.top_up_from_dispatch_queue(actual=2, floor=2)

        self.assertEqual(calls, [])
        remaining = list((self.refactor_loop / "dispatch-queue" / "p1").glob("*.dispatch.json"))
        self.assertEqual(len(remaining), 5)

    def test_monitor_does_not_emit_hard_gate_when_expected_zero_and_queue_empty(self) -> None:
        with mock.patch.object(self.monitor, "list_auto_loop_issues", return_value=[]):
            with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=0):
                self.monitor.tick()

        self.assertFalse((self.refactor_loop / ".controller-pending-events.log").exists())

    def test_tick_empty_queue_zero_expected_writes_owner_local_status_snapshot_only(self) -> None:
        with mock.patch.object(self.monitor, "list_auto_loop_issues", return_value=[]):
            with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=0):
                self.module.run_concurrency_reconcile_tick(self.monitor)

        snapshot_path = self.refactor_loop / "state" / "statusline-snapshot.json"
        self.assertTrue(snapshot_path.exists())
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        self.assertEqual(snapshot["actual"], 0)
        self.assertEqual(snapshot["expected"], 0)
        self.assertEqual(snapshot["floor"], 2)
        self.assertEqual(snapshot["p0_streak"], 0)
        self.assertFalse((self.refactor_loop / "state.json").exists())
        self.assertFalse((self.refactor_loop / "controller-runtime-registry.json").exists())
        self.assertFalse((self.refactor_loop / ".controller-pending-events.log").exists())

    def test_tick_queue_empty_active_audit_writes_wait_event(self) -> None:
        active_audit = (
            f"python3 /skill/consensus-rnd-cli spawn-codex --cd {self.repo} "
            f"--prompt {self.refactor_loop}/prompts/audit-iter-8.md "
            f"--log {self.refactor_loop}/logs/audit-iter-8.log"
        )
        with mock.patch.object(self.monitor, "list_auto_loop_issues", return_value=[]):
            with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=1):
                with mock.patch.object(self.monitor, "list_in_flight_codex_lines", return_value=[active_audit]):
                    self.monitor.tick()

        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn(
            "WAIT:single-active-audit:dispatch_required=0:actual=1 expected=0 queue=0 blocked_deficit=1",
            events,
        )
        self.assertNotIn("HARD_GATE:dispatch_required=1", events)

    def test_tick_queue_empty_zero_expected_no_active_audit_does_not_write_hard_gate(self) -> None:
        with mock.patch.object(self.monitor, "list_auto_loop_issues", return_value=[]):
            with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=1):
                with mock.patch.object(self.monitor, "list_in_flight_codex_lines", return_value=[]):
                    self.monitor.tick()

        self.assertFalse((self.refactor_loop / ".controller-pending-events.log").exists())

    def test_tick_actionable_work_bypasses_single_active_audit_wait(self) -> None:
        active_audit = (
            f"python3 /skill/consensus-rnd-cli spawn-codex --cd {self.repo} "
            f"--prompt {self.refactor_loop}/prompts/audit-iter-8.md "
            f"--log {self.refactor_loop}/logs/audit-iter-8.log"
        )
        items = [
            {
                "number": 277,
                "kind": "issue",
                "phase": "crnd:phase:fixing",
                "human": "crnd:human:auto",
                "labels": ["crnd:lifecycle:managed", "crnd:phase:fixing", "crnd:human:auto"],
                "state": "open",
            }
        ]

        with mock.patch.object(self.monitor, "list_auto_loop_issues", return_value=items):
            with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=1):
                with mock.patch.object(self.monitor, "list_in_flight_codex_lines", return_value=[active_audit]):
                    self.monitor.tick()

        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("HARD_GATE:dispatch_required=1:actual=1 expected=1 queue=0", events)
        self.assertNotIn("WAIT:single-active-audit", events)

    def test_tick_fresh_phase9_intent_reduces_queue_empty_hard_gate_pressure(self) -> None:
        items = [
            self.design_issue_item(330),
            self.design_issue_item(331),
            self.design_issue_item(332),
        ]
        self.append_phase9_harness_spawn_intent("phase9-issue330-r4-minimal")

        with mock.patch.object(self.monitor, "list_auto_loop_issues", return_value=items):
            with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=1):
                with mock.patch.object(self.module.time, "time", return_value=1_779_780_600):
                    self.monitor.tick()

        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn(
            "HARD_GATE:dispatch_required=1:actual=1 expected=3 queue=0 transient_supply=1 uncovered_deficit=1",
            events,
        )
        self.assertNotIn("HARD_GATE:dispatch_required=2:actual=1 expected=3 queue=0", events)

    def test_tick_fresh_phase9_intents_can_eliminate_queue_empty_hard_gate_pressure(self) -> None:
        items = [
            self.design_issue_item(330),
            self.design_issue_item(331),
            self.design_issue_item(332),
        ]
        self.append_phase9_harness_spawn_intent("phase9-issue330-r4-minimal")
        self.append_phase9_harness_spawn_intent("phase9-issue331-r4-minimal")

        with mock.patch.object(self.monitor, "list_auto_loop_issues", return_value=items):
            with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=1):
                with mock.patch.object(self.module.time, "time", return_value=1_779_780_600):
                    self.monitor.tick()

        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertNotIn("HARD_GATE:dispatch_required=", events)

    def test_hard_gate_transient_supply_rejects_stale_malformed_logged_claimed_and_terminal_blocked_intents(self) -> None:
        cases = (
            ("stale", lambda task_id: self.append_phase9_harness_spawn_intent(task_id, ts="2026-05-26T07:00:00Z")),
            (
                "malformed",
                lambda _task_id: (
                    self.refactor_loop / ".controller-pending-events.log"
                ).write_text("2026-05-26T07:29:00Z HARNESS_SPAWN_INTENT {bad-json\n", encoding="utf-8"),
            ),
            (
                "logged",
                lambda task_id: (
                    self.append_phase9_harness_spawn_intent(task_id),
                    (self.refactor_loop / "logs" / f"{task_id}.log").write_text("already targeted\n", encoding="utf-8"),
                ),
            ),
            (
                "claimed",
                lambda task_id: (
                    self.append_phase9_harness_spawn_intent(task_id),
                    (self.refactor_loop / "locks" / "spawn-tasks").mkdir(parents=True, exist_ok=True),
                    (self.refactor_loop / "locks" / "spawn-tasks" / f"{task_id}.lock").write_text("claimed\n", encoding="utf-8"),
                ),
            ),
            (
                "terminal-blocked",
                lambda task_id: (
                    self.append_phase9_harness_spawn_intent(task_id, intent_id=f"phase9-router:333:4:minimal"),
                    (self.refactor_loop / ".controller-pending-events.log").write_text(
                        (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
                        + "WAKEUP_RUNNER_BLOCKED:harness-spawn-intent:phase9-router:333:4:minimal:target_not_open:CLOSED\n",
                        encoding="utf-8",
                    ),
                ),
            ),
        )
        for case, setup in cases:
            with self.subTest(case=case):
                (self.refactor_loop / ".controller-pending-events.log").unlink(missing_ok=True)
                (self.refactor_loop / "phase9-router-ledger.jsonl").unlink(missing_ok=True)
                task_id = "phase9-issue333-r4-minimal"
                (self.refactor_loop / "logs").mkdir(parents=True, exist_ok=True)
                (self.refactor_loop / "locks" / "spawn-tasks").mkdir(parents=True, exist_ok=True)
                (self.refactor_loop / "logs" / f"{task_id}.log").unlink(missing_ok=True)
                (self.refactor_loop / "locks" / "spawn-tasks" / f"{task_id}.lock").unlink(missing_ok=True)
                setup(task_id)

                supply = self.monitor.hard_gate_transient_supply(
                    breakdown=[{"id": "#333", "kind": "issue", "phase": self.labels.PHASE_DESIGN_SOLVING, "expected": 1}],
                    target=3,
                    actual=1,
                    queue_empty=True,
                    now=1_779_780_600,
                )

                self.assertEqual(supply.supply, 0)

    def test_archived_invalid_harness_spawn_intent_no_longer_blocks_hard_gate_supply(self) -> None:
        malformed_intent = self.append_phase9_harness_spawn_intent("phase9-issue333-r4-minimal")
        malformed_intent.pop("queued_at")
        line = "2026-05-26T07:29:00Z HARNESS_SPAWN_INTENT " + json.dumps(malformed_intent, sort_keys=True)
        pending = self.refactor_loop / ".controller-pending-events.log"
        pending.write_text(line + "\n", encoding="utf-8")

        blocked = self.monitor.hard_gate_transient_supply(
            breakdown=[{"id": "#333", "kind": "issue", "phase": self.labels.PHASE_DESIGN_SOLVING, "expected": 1}],
            target=3,
            actual=0,
            queue_empty=True,
            now=1_779_780_600,
        )

        self.assertEqual(0, blocked.supply)
        self.assertEqual("malformed-harness-spawn-intent:missing-queued_at", blocked.blocked_reason)

        pending.write_text(
            line
            + "\n"
            + "WAKEUP_RUNNER_ARCHIVED_INVALID_HARNESS_SPAWN_INTENT:"
            + str(malformed_intent["intent_id"])
            + ":missing-queued_at\n",
            encoding="utf-8",
        )

        unblocked = self.monitor.hard_gate_transient_supply(
            breakdown=[{"id": "#333", "kind": "issue", "phase": self.labels.PHASE_DESIGN_SOLVING, "expected": 1}],
            target=3,
            actual=0,
            queue_empty=True,
            now=1_779_780_600,
        )

        self.assertIsNone(unblocked.blocked_reason)
        self.assertEqual(0, unblocked.supply)

        pending.write_text(
            line
            + "\n"
            + "WAKEUP_RUNNER_ARCHIVED_INVALID_HARNESS_SPAWN_INTENT:"
            + self.harness_spawn_intent_line_digest(line)
            + ":missing-queued_at\n",
            encoding="utf-8",
        )
        digest_unblocked = self.monitor.hard_gate_transient_supply(
            breakdown=[{"id": "#333", "kind": "issue", "phase": self.labels.PHASE_DESIGN_SOLVING, "expected": 1}],
            target=3,
            actual=0,
            queue_empty=True,
            now=1_779_780_600,
        )

        self.assertIsNone(digest_unblocked.blocked_reason)

    def test_tick_terminal_implement_result_does_not_trigger_no_gap_expected_worker(self) -> None:
        items = [
            {
                "number": 581,
                "kind": "issue",
                "phase": self.labels.PHASE_IMPLEMENTING,
                "human": self.labels.HUMAN_AUTO,
                "labels": [self.labels.MANAGED, self.labels.PHASE_IMPLEMENTING, self.labels.HUMAN_AUTO],
                "state": "open",
            }
        ]
        logs = self.refactor_loop / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "implement-issue-581.log").write_text(
            "worker completed no-op\nIMPLEMENT_DONE:issue-581:partial\nEXIT=0\n",
            encoding="utf-8",
        )

        with mock.patch.object(self.monitor, "list_auto_loop_issues", return_value=items):
            with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=0):
                self.monitor.tick()

        self.assertFalse((self.refactor_loop / ".controller-pending-events.log").exists())

    def test_tick_publish_ready_implement_result_does_not_trigger_hard_gate(self) -> None:
        items = [
            {
                "number": 581,
                "kind": "issue",
                "phase": self.labels.PHASE_IMPLEMENTING,
                "human": self.labels.HUMAN_AUTO,
                "labels": [self.labels.MANAGED, self.labels.PHASE_IMPLEMENTING, self.labels.HUMAN_AUTO],
                "state": "open",
            }
        ]
        logs = self.refactor_loop / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (self.repo / ".worktrees" / "iter581-issue-581").mkdir(parents=True)
        (logs / "implement-issue-581.log").write_text(
            "implementation ready for publish\nIMPLEMENT_DONE:issue-581:ok\nEXIT=0\n",
            encoding="utf-8",
        )

        def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            if command[-2:] == ["--abbrev-ref", "HEAD"]:
                return subprocess.CompletedProcess(command, 0, "refactor/iter581-issue-581\n", "")
            if command[-2:] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, "M  touched.py\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(self.monitor, "run", side_effect=runner):
            with mock.patch.object(self.monitor, "list_auto_loop_issues", return_value=items):
                with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=0):
                    self.monitor.tick()

        self.assertFalse((self.refactor_loop / ".controller-pending-events.log").exists())
        snapshot = json.loads((self.refactor_loop / "state" / "statusline-snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["expected"], 0)

    def test_tick_terminal_implement_result_does_not_suppress_design_expected_worker(self) -> None:
        items = [
            {
                "number": 537,
                "kind": "issue",
                "phase": self.labels.PHASE_DESIGN_SOLVING,
                "human": self.labels.HUMAN_AUTO,
                "labels": [self.labels.MANAGED, self.labels.PHASE_DESIGN_SOLVING, self.labels.HUMAN_AUTO],
                "state": "open",
            }
        ]
        logs = self.refactor_loop / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "implement-issue-537.log").write_text(
            "old implementation no-op\nIMPLEMENT_DONE:issue-537:partial\nEXIT=0\n",
            encoding="utf-8",
        )

        with mock.patch.object(self.monitor, "list_auto_loop_issues", return_value=items):
            with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=0):
                self.monitor.tick()

        alert = (self.refactor_loop / ".concurrency-alert.log").read_text(encoding="utf-8")
        self.assertIn("P0 no-gap-violation", alert)
        snapshot = json.loads((self.refactor_loop / "state" / "statusline-snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["expected"], 1)

    def test_tick_zero_expected_actionable_work_bypasses_single_active_audit_wait(self) -> None:
        active_audit = (
            f"python3 /skill/consensus-rnd-cli spawn-codex --cd {self.repo} "
            f"--prompt {self.refactor_loop}/prompts/audit-iter-8.md "
            f"--log {self.refactor_loop}/logs/audit-iter-8.log"
        )
        items = [
            {
                "number": 277,
                "kind": "issue",
                "phase": "crnd:phase:consensus-reached",
                "human": "crnd:human:auto",
                "labels": ["crnd:lifecycle:managed", "crnd:phase:consensus-reached", "crnd:human:auto"],
                "state": "open",
            }
        ]

        with mock.patch.object(self.monitor, "list_auto_loop_issues", return_value=items):
            with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=1):
                with mock.patch.object(self.monitor, "list_in_flight_codex_lines", return_value=[active_audit]):
                    self.monitor.tick()

        self.assertFalse((self.refactor_loop / ".controller-pending-events.log").exists())

    def test_tick_queued_work_bypasses_single_active_audit_wait_and_consumes_queue(self) -> None:
        active_audit = (
            f"python3 /skill/consensus-rnd-cli spawn-codex --cd {self.repo} "
            f"--prompt {self.refactor_loop}/prompts/audit-iter-8.md "
            f"--log {self.refactor_loop}/logs/audit-iter-8.log"
        )
        self.write_dispatch("p1", "fix-pr294-round-3")
        calls: list[list[str]] = []
        counts = [1, 2]

        with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            with mock.patch.object(self.monitor, "list_auto_loop_issues", return_value=[]):
                with mock.patch.object(self.monitor, "count_in_flight_codex", side_effect=lambda: counts.pop(0)):
                    with mock.patch.object(self.monitor, "list_in_flight_codex_lines", return_value=[active_audit]):
                        self.monitor.tick()

        self.assertEqual(calls, [])
        self.assertFalse((self.refactor_loop / "dispatch-queue" / "p1" / "fix-pr294-round-3.dispatch.json").exists())
        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("DISPATCH_INTENT:fix-pr294-round-3:p1:fix-pr294-round-3 needed", events)
        self.assertNotIn("WAIT:single-active-audit", events)
        self.assertNotIn("HARD_GATE:dispatch_required=", events)

    def test_monitor_archives_dispatched_json_with_timestamp(self) -> None:
        self.write_dispatch("p0", "fix-pr44-round-3", reason="PR #44 r3 fix needed")
        calls: list[list[str]] = []

        with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            self.monitor.dispatch_one_from_queue()

        archive = self.refactor_loop / "dispatch-dispatched" / "fix-pr44-round-3.json"
        self.assertTrue(archive.exists())
        payload = json.loads(archive.read_text(encoding="utf-8"))
        self.assertEqual(payload["task_id"], "fix-pr44-round-3")
        self.assertEqual(payload["priority"], "p0")
        self.assertEqual(payload["dispatch_state"], "harness-intent")
        self.assertEqual(payload["intent_id"], "dispatch:fix-pr44-round-3")
        self.assertRegex(payload["dispatch_at"], r"^20\d\d-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("DISPATCH_INTENT:fix-pr44-round-3:p0:PR #44 r3 fix needed", events)

    def test_tick_p0_no_gap_with_queued_dispatch_fires_topup(self) -> None:
        self.reload_monitor()
        self.write_dispatch("p0", "fix-pr57-round-1-a")
        self.write_dispatch("p0", "fix-pr57-round-1-b")
        calls: list[list[str]] = []
        counts = [0, 1, 2]

        with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            with mock.patch.object(self.monitor, "count_in_flight_codex", side_effect=lambda: counts.pop(0)):
                with mock.patch.object(
                    self.monitor,
                    "list_auto_loop_issues",
                    return_value=[{"number": 57, "kind": "pr", "phase": self.labels.PHASE_FIXING, "human": self.labels.HUMAN_AUTO}],
                ):
                    self.monitor.tick()

        self.assertEqual(calls, [])
        self.assertEqual(len(self.harness_spawn_intents()), 1)
        self.assertTrue((self.refactor_loop / "dispatch-queue" / "p0" / "fix-pr57-round-1-b.dispatch.json").exists())
        alert = (self.refactor_loop / ".concurrency-alert.log").read_text(encoding="utf-8")
        self.assertIn("P0 no-gap-violation", alert)
        state = json.loads((self.refactor_loop / ".concurrency-monitor-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["zero_streak"], 1)
        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("DISPATCH_INTENT:fix-pr57-round-1-a:p0:fix-pr57-round-1-a needed", events)
        self.assertNotIn("DISPATCH_INTENT:fix-pr57-round-1-b:p0:fix-pr57-round-1-b needed", events)

    # Refactor (fix/pr242-narrow-allowlist-and-nonowner-test): Old: tick()
    # only proved the owner/default-local queue top-up path. New: non-owner
    # ticks keep read-only alert/status behavior but cannot dispatch or archive.
    def test_tick_non_owner_p0_no_gap_does_not_dispatch_archive_or_write_dispatch_fired(self) -> None:
        os.environ["DEGRADATION_WATCH_INTERVAL_SECONDS"] = "0"
        self.reload_monitor()
        dispatch_a = self.write_dispatch("p0", "fix-pr57-round-1-a")
        dispatch_b = self.write_dispatch("p0", "fix-pr57-round-1-b")
        calls: list[list[str]] = []
        decision = mock.Mock(
            allowed=False,
            owner_device="device-a",
            status="not-owner",
            action="concurrency-tick",
            lease_id="",
            expires_at="",
        )

        with mock.patch("codex_refactor_loop.monitors.concurrency.require_active_controller", return_value=decision):
            with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
                with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=0):
                    with mock.patch.object(
                        self.monitor,
                        "list_auto_loop_issues",
                        return_value=[{"number": 57, "kind": "pr", "phase": self.labels.PHASE_FIXING, "human": self.labels.HUMAN_AUTO}],
                    ):
                        self.monitor.tick()

        self.assertEqual(calls, [])
        self.assertTrue(dispatch_a.exists())
        self.assertTrue(dispatch_b.exists())
        self.assertFalse((self.refactor_loop / "dispatch-dispatched").exists())
        alert = (self.refactor_loop / ".concurrency-alert.log").read_text(encoding="utf-8")
        self.assertIn("P0 no-gap-violation", alert)
        snapshot = json.loads((self.refactor_loop / "state" / "statusline-snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["actual"], 0)
        self.assertEqual(snapshot["expected"], 1)
        self.assertEqual(snapshot["p0_streak"], 1)
        status = json.loads((self.refactor_loop / "state" / "active-controller-status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["active_controller"], "noop:not-owner")
        events = (self.refactor_loop / ".controller-pending-events.log").read_text(encoding="utf-8")
        self.assertIn("concurrency-alert P0 no-gap-violation", events)
        self.assertNotIn("DISPATCH_FIRED", events)

    def test_tick_below_floor_with_non_empty_queue_dispatches(self) -> None:
        for i in range(3):
            self.write_dispatch("p1", f"floor-task-{i}")
        calls: list[list[str]] = []
        counts = [2, 3, 4]
        os.environ["CODEX_FLOOR"] = "4"
        self.reload_monitor()

        with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            with mock.patch.object(self.monitor, "count_in_flight_codex", side_effect=lambda: counts.pop(0)):
                with mock.patch.object(self.monitor, "list_auto_loop_issues", return_value=[]):
                    self.monitor.tick()

        self.assertEqual(calls, [])
        self.assertEqual(len(self.harness_spawn_intents()), 2)
        remaining = list((self.refactor_loop / "dispatch-queue" / "p1").glob("*.dispatch.json"))
        self.assertEqual(len(remaining), 1)

    # Refactor (fix/pr242-narrow-allowlist-and-nonowner-test): Old: below-floor
    # tick coverage only asserted owner dispatch. New: non-owner below-floor
    # queue repair is read-only and leaves the dispatch queue untouched.
    def test_tick_non_owner_below_floor_with_non_empty_queue_does_not_top_up(self) -> None:
        for i in range(3):
            self.write_dispatch("p1", f"floor-task-{i}")
        calls: list[list[str]] = []
        os.environ["CODEX_FLOOR"] = "4"
        os.environ["DEGRADATION_WATCH_INTERVAL_SECONDS"] = "0"
        self.reload_monitor()
        decision = mock.Mock(
            allowed=False,
            owner_device="device-a",
            status="not-owner",
            action="concurrency-tick",
            lease_id="",
            expires_at="",
        )

        with mock.patch("codex_refactor_loop.monitors.concurrency.require_active_controller", return_value=decision):
            with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
                with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=2):
                    with mock.patch.object(self.monitor, "list_auto_loop_issues", return_value=[]):
                        with mock.patch.object(self.monitor, "top_up_from_dispatch_queue", wraps=self.monitor.top_up_from_dispatch_queue) as top_up:
                            self.monitor.tick()

        top_up.assert_not_called()
        self.assertEqual(calls, [])
        remaining = list((self.refactor_loop / "dispatch-queue" / "p1").glob("*.dispatch.json"))
        self.assertEqual(len(remaining), 3)
        self.assertFalse((self.refactor_loop / "dispatch-dispatched").exists())
        self.assertFalse((self.refactor_loop / ".controller-pending-events.log").exists())
        snapshot = json.loads((self.refactor_loop / "state" / "statusline-snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["actual"], 2)
        self.assertEqual(snapshot["expected"], 0)
        status = json.loads((self.refactor_loop / "state" / "active-controller-status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["active_controller"], "noop:not-owner")

    # Refactor (fix/pr242-narrow-allowlist-and-nonowner-test): Old: empty-queue
    # deficit tests only covered owner hard-gate emission. New: non-owners
    # preserve status snapshots without enqueueing controller write requests.
    def test_tick_non_owner_below_floor_with_empty_queue_does_not_write_hard_gate(self) -> None:
        os.environ["CODEX_FLOOR"] = "4"
        os.environ["DEGRADATION_WATCH_INTERVAL_SECONDS"] = "0"
        self.reload_monitor()
        decision = mock.Mock(
            allowed=False,
            owner_device="device-a",
            status="not-owner",
            action="concurrency-tick",
            lease_id="",
            expires_at="",
        )

        with mock.patch("codex_refactor_loop.monitors.concurrency.require_active_controller", return_value=decision):
            with mock.patch.object(self.monitor, "count_in_flight_codex", return_value=2):
                with mock.patch.object(self.monitor, "list_auto_loop_issues", return_value=[]):
                    self.monitor.tick()

        self.assertFalse((self.refactor_loop / ".controller-pending-events.log").exists())
        snapshot = json.loads((self.refactor_loop / "state" / "statusline-snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["actual"], 2)
        self.assertEqual(snapshot["expected"], 0)
        status = json.loads((self.refactor_loop / "state" / "active-controller-status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["active_controller"], "noop:not-owner")

    def test_tick_dispatches_toward_expected_count_not_just_floor(self) -> None:
        """When expected > floor, tick fires deficit toward expected (not just floor)."""
        self.reload_monitor()
        for i in range(4):
            self.write_dispatch("p1", f"expected-task-{i}")
        active_items = [
            {"number": i, "kind": "issue", "phase": self.labels.PHASE_FIXING, "human": self.labels.HUMAN_AUTO}
            for i in range(4)
        ]
        calls: list[list[str]] = []
        counts = [2, 3, 4]

        with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            with mock.patch.object(self.monitor, "count_in_flight_codex", side_effect=lambda: counts.pop(0)):
                with mock.patch.object(self.monitor, "list_auto_loop_issues", return_value=active_items):
                    self.monitor.tick()

        self.assertEqual(calls, [])
        self.assertEqual(len(self.harness_spawn_intents()), 2)
        remaining = list((self.refactor_loop / "dispatch-queue" / "p1").glob("*.dispatch.json"))
        self.assertEqual(len(remaining), 2)

    def test_dispatch_json_without_stall_uses_default_5400(self) -> None:
        """A dispatch JSON missing the `stall` field falls back to --stall 5400 on launch."""
        dispatch = self.write_dispatch("p1", "default-stall-task")
        payload = json.loads(dispatch.read_text(encoding="utf-8"))
        del payload["stall"]
        dispatch.write_text(json.dumps(payload), encoding="utf-8")
        calls: list[list[str]] = []

        with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            self.monitor.dispatch_one_from_queue()

        self.assertEqual(calls, [])
        self.assertEqual(self.harness_spawn_intents()[0]["stall"], 5400)

    def test_configured_floor_invalid_falls_back(self) -> None:
        os.environ["CODEX_FLOOR"] = "abc"
        self.reload_monitor()

        self.assertEqual(self.monitor.configured_floor(), 5)

    def test_configured_floor_below_minimum_clamps(self) -> None:
        os.environ["CODEX_FLOOR"] = "0"
        self.reload_monitor()

        self.assertEqual(self.monitor.configured_floor(), 2)

    def test_archive_collision_writes_timestamp_suffix(self) -> None:
        self.write_dispatch("p0", "collision-task")
        dispatched = self.refactor_loop / "dispatch-dispatched"
        dispatched.mkdir(parents=True, exist_ok=True)
        (dispatched / "collision-task.json").write_text("{}\n", encoding="utf-8")
        calls: list[list[str]] = []

        with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            with mock.patch.object(self.module, "utc_ts", return_value="2026-05-26T08:09:10Z"):
                self.monitor.dispatch_one_from_queue()

        self.assertTrue((dispatched / "collision-task.json").exists())
        suffixed = dispatched / "collision-task-20260526T080910Z.json"
        self.assertTrue(suffixed.exists())
        payload = json.loads(suffixed.read_text(encoding="utf-8"))
        self.assertEqual(payload["task_id"], "collision-task")

    def test_dispatch_one_derives_task_id_from_filename(self) -> None:
        self.write_dispatch("p2", "filename-task", include_task_id=False)
        calls: list[list[str]] = []

        with mock.patch.object(self.module.subprocess, "Popen", side_effect=self.fake_popen(calls)):
            fired = self.monitor.dispatch_one_from_queue()

        self.assertEqual(fired, ("filename-task", "p2", "filename-task needed"))
        self.assertEqual(calls, [])
        archive = self.refactor_loop / "dispatch-dispatched" / "filename-task.json"
        self.assertTrue(archive.exists())
        payload = json.loads(archive.read_text(encoding="utf-8"))
        self.assertEqual(payload["task_id"], "filename-task")

    def test_dispatch_intent_append_failure_preserves_queue_file(self) -> None:
        dispatch = self.write_dispatch("p1", "retry-task")

        with mock.patch.object(self.monitor, "write_pending_event", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.monitor.dispatch_one_from_queue()

        self.assertTrue(dispatch.exists())
        self.assertFalse((self.refactor_loop / "dispatch-dispatched").exists())

    def test_concurrency_monitor_source_has_no_direct_nohup_spawn_codex(self) -> None:
        source = (SCRIPT_DIR / "codex_refactor_loop" / "monitors" / "concurrency.py").read_text(encoding="utf-8")
        self.assertIn('"command": "spawn-codex"', source)
        self.assertNotIn('"nohup"', source)
        self.assertNotIn("start_new_session", source)

    # Refactor (iter4/skill-count-cli-canonical): Old pattern: controller ran
    # ps | grep manually and consensus-rnd-cli spawn-codex reimplemented count_in_flight_codex,
    # making drift from the daemon algorithm likely.
    # New principle: expose `--count-only` / `--list-codex` so the controller
    # can directly reuse the daemon's canonical algorithm
    # (per 2026-05-26 maintainer-directive).
    def test_count_only_cli_prints_canonical_in_flight_codex_count(self) -> None:
        import io
        fake_ps = (
            f"bash consensus-rnd-cli spawn-codex --cd {self.repo} --prompt /tmp/a.md --log /tmp/a.log\n"
            f"bash -c consensus-rnd-cli spawn-codex --cd {self.repo} --prompt /tmp/a.md --log /tmp/a.log\n"
            f"bash consensus-rnd-cli spawn-codex --cd {self.repo} --prompt /tmp/b.md --log /tmp/b.log\n"
            "bash consensus-rnd-cli spawn-codex --cd /Users/other-host/repo --prompt /tmp/c.md --log /tmp/c.log\n"
        )
        captured = io.StringIO()
        with mock.patch.object(
            self.module.subprocess,
            "run",
            return_value=mock.Mock(stdout=fake_ps, returncode=0),
        ), mock.patch.object(sys, "stdout", captured):
            exit_code = self.module.main(["--count-only"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(captured.getvalue().strip(), "2")

    def test_list_codex_cli_prints_one_supervisor_per_line(self) -> None:
        import io
        fake_ps = (
            f"bash consensus-rnd-cli spawn-codex --cd {self.repo} --prompt /tmp/a.md --log /tmp/a.log\n"
            f"bash -c consensus-rnd-cli spawn-codex --cd {self.repo} --prompt /tmp/a.md --log /tmp/a.log\n"
            f"bash consensus-rnd-cli spawn-codex --cd {self.repo} --prompt /tmp/b.md --log /tmp/b.log\n"
        )
        captured = io.StringIO()
        with mock.patch.object(
            self.module.subprocess,
            "run",
            return_value=mock.Mock(stdout=fake_ps, returncode=0),
        ), mock.patch.object(sys, "stdout", captured):
            exit_code = self.module.main(["--list-codex"])

        self.assertEqual(exit_code, 0)
        lines = [line for line in captured.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertIn("consensus-rnd-cli spawn-codex", line)
            self.assertIn(str(self.repo), line)
            self.assertNotIn(" -c ", line)

    def test_relative_cd_in_process_table_counts_against_repo_root(self) -> None:
        import io
        fake_ps = (
            "bash consensus-rnd-cli spawn-codex --cd . --prompt .refactor-loop/prompts/a.md --log .refactor-loop/logs/a.log\n"
            "bash consensus-rnd-cli spawn-codex --cd .worktrees/task --prompt .refactor-loop/prompts/b.md --log .refactor-loop/logs/b.log\n"
            "bash consensus-rnd-cli spawn-codex --cd ../outside --prompt /tmp/c.md --log /tmp/c.log\n"
        )
        captured = io.StringIO()
        with mock.patch.object(
            self.module.subprocess,
            "run",
            return_value=mock.Mock(stdout=fake_ps, returncode=0),
        ), mock.patch.object(sys, "stdout", captured):
            exit_code = self.module.main(["--count-only"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(captured.getvalue().strip(), "2")

    # Refactor (cli-readonly-fallback): Old pattern: --count-only / --list-codex /
    # --once raised RuntimeError at module load when REPO_ROOT was unset, even
    # though SKILL.md mandates them as canonical CLI for controllers (which
    # often invoke from a repo dir without sourcing host.env first). New
    # principle: read-only CLI flags auto-set ALLOW_GIT_ROOT_FALLBACK so the
    # CLI works without env priming; daemon mode (no flag) stays strict.
    def test_readonly_cli_flags_auto_allow_git_root_fallback_when_repo_root_unset(self) -> None:
        import subprocess as real_subprocess
        real_subprocess.run(["git", "init", "-q"], cwd=str(self.repo), check=True)
        env = {k: v for k, v in os.environ.items() if k != "REPO_ROOT"}
        env.pop("ALLOW_GIT_ROOT_FALLBACK", None)
        env["PATH"] = os.environ.get("PATH", "")

        for flag in ("--count-only", "--list-codex"):
            with self.subTest(flag=flag):
                result = real_subprocess.run(
                    [sys.executable, str(CLI), "concurrency", flag],
                    cwd=str(self.repo),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    msg=f"{flag} should not fail without REPO_ROOT; stderr={result.stderr[:300]}",
                )

    def test_daemon_mode_without_repo_root_still_fails_closed(self) -> None:
        import subprocess as real_subprocess
        env = {k: v for k, v in os.environ.items() if k != "REPO_ROOT"}
        env.pop("ALLOW_GIT_ROOT_FALLBACK", None)
        env["PATH"] = os.environ.get("PATH", "")

        result = real_subprocess.run(
            [sys.executable, str(CLI), "concurrency", "--daemon"],
            cwd="/tmp",
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("REPO_ROOT is unset", result.stderr)

    # Refactor (impl/issue235-delete-downstream-watch): Old pattern: downstream concurrency ticks ran check-degradation against host roots. New principle: plugin-installed hosts have no degradation runtime watch, alert log, or pending event.
    def test_downstream_plugin_installed_concurrency_once_has_no_degradation_watch_surface(self) -> None:
        import subprocess as real_subprocess
        fake_bin = self.repo / "bin"
        fake_bin.mkdir()
        fake_gh = fake_bin / "gh"
        fake_gh.write_text("#!/bin/sh\nprintf '[]\\n'\n", encoding="utf-8")
        fake_gh.chmod(0o755)
        env = os.environ.copy()
        env["REPO_ROOT"] = str(self.repo)
        env["CODEX_FLOOR"] = "2"
        env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

        result = real_subprocess.run(
            [sys.executable, str(CLI), "concurrency", "--once"],
            cwd=str(self.repo),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.refactor_loop / ".degradation-alert.log").exists())
        pending_events = self.refactor_loop / ".controller-pending-events.log"
        if pending_events.exists():
            self.assertNotIn("skill-degradation-alert", pending_events.read_text(encoding="utf-8"))
        self.assertFalse((self.repo / "skills").exists())


class SnapshotDaemonHealthFieldTests(unittest.TestCase):
    """Producer side of the statusline daemon-health extension.

    Daemon heartbeat staleness is collected by concurrency_monitor and surfaced
    in the snapshot, so the consumer (consensus-rnd-cli statusline) does not need to enumerate
    daemons itself. Discovery is dynamic via heartbeat file presence.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.old_env = os.environ.copy()
        os.environ.pop("CONSENSUS_RND_HOST_ENV", None)
        os.environ["REPO_ROOT"] = str(self.repo)
        os.environ["CODEX_FLOOR"] = "2"
        from codex_refactor_loop.context import LoopContext
        from codex_refactor_loop.monitors import concurrency as concurrency_module
        from codex_refactor_loop.monitors.concurrency import ConcurrencyMonitor
        self.module = concurrency_module
        os.environ.pop("CONSENSUS_RND_HOST_ENV", None)
        self.ctx = LoopContext.load(repo_root=self.repo, env=os.environ)
        self.monitor = ConcurrencyMonitor(self.ctx)
        self.heartbeats = self.repo / ".refactor-loop" / "heartbeats"
        self.heartbeats.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)
        self.tmp.cleanup()

    def _write_heartbeat(self, name: str, age_seconds: int, now: float) -> None:
        (self.heartbeats / f"{name}.ts").write_text(str(int(now - age_seconds)))

    def test_read_daemon_heartbeats_fresh_is_not_stale(self) -> None:
        now = 1_000_000.0
        self._write_heartbeat("concurrency_monitor", 10, now)
        result = self.monitor.read_daemon_heartbeats(now=now)
        self.assertEqual(result["concurrency_monitor"]["age_seconds"], 10)
        self.assertFalse(result["concurrency_monitor"]["stale"])

    def test_read_daemon_heartbeats_old_is_stale(self) -> None:
        now = 1_000_000.0
        self._write_heartbeat("dev_sync_daemon", 200, now)
        result = self.monitor.read_daemon_heartbeats(now=now)
        self.assertTrue(result["dev_sync_daemon"]["stale"])
        self.assertEqual(result["dev_sync_daemon"]["age_seconds"], 200)

    def test_read_daemon_heartbeats_malformed_is_stale(self) -> None:
        (self.heartbeats / "comment-monitor.ts").write_text("not-a-number\n")
        result = self.monitor.read_daemon_heartbeats(now=1_000_000.0)
        self.assertTrue(result["comment-monitor"]["stale"])
        self.assertIsNone(result["comment-monitor"]["age_seconds"])
        self.assertEqual(
            [{"age_seconds": None, "name": "comment-monitor", "reason": "heartbeat-malformed"}],
            self.monitor.stale_daemon_projection(result),
        )

    def test_read_daemon_heartbeats_missing_dir_returns_empty(self) -> None:
        # Wipe heartbeats dir; ensure no crash and empty result.
        import shutil

        shutil.rmtree(self.heartbeats)
        self.assertEqual(self.monitor.read_daemon_heartbeats(now=1_000_000.0), {})

    def test_read_daemon_heartbeats_discovers_dynamically(self) -> None:
        # New daemon name not in any hard-coded list should appear automatically.
        now = 1_000_000.0
        self._write_heartbeat("future_daemon", 5, now)
        result = self.monitor.read_daemon_heartbeats(now=now)
        self.assertIn("future_daemon", result)

    def test_snapshot_includes_daemons_map_and_counts(self) -> None:
        from datetime import datetime, timezone

        now_dt = datetime(2026, 5, 26, 19, 0, 0, tzinfo=timezone.utc)
        now_ts = now_dt.timestamp()
        self._write_heartbeat("concurrency_monitor", 5, now_ts)
        self._write_heartbeat("comment-monitor", 5, now_ts)
        self._write_heartbeat("dev_sync_daemon", 300, now_ts)  # stale
        self.monitor.write_statusline_snapshot(
            actual=7,
            expected=5,
            p0_streak=0,
            last_p0_at=None,
            open_pr_count=2,
            open_issue_count=4,
            now=now_dt,
        )
        snap_path = self.repo / ".refactor-loop" / "state" / "statusline-snapshot.json"
        payload = json.loads(snap_path.read_text())
        self.assertEqual(payload["daemons_total"], 3)
        self.assertEqual(payload["daemons_healthy"], 2)
        self.assertIn("daemons", payload)
        self.assertTrue(payload["daemons"]["dev_sync_daemon"]["stale"])
        self.assertFalse(payload["daemons"]["concurrency_monitor"]["stale"])
        self.assertEqual(
            [{"age_seconds": 300, "name": "dev_sync_daemon", "reason": "heartbeat-stale"}],
            payload["stale_daemons"],
        )

    def test_snapshot_with_no_heartbeats_writes_empty_daemons(self) -> None:
        from datetime import datetime, timezone

        # Ensure no heartbeats present.
        for hb in self.heartbeats.glob("*.ts"):
            hb.unlink()
        self.monitor.write_statusline_snapshot(
            actual=3,
            expected=3,
            p0_streak=0,
            last_p0_at=None,
            open_pr_count=0,
            open_issue_count=0,
            now=datetime(2026, 5, 26, 19, 0, 0, tzinfo=timezone.utc),
        )
        snap_path = self.repo / ".refactor-loop" / "state" / "statusline-snapshot.json"
        payload = json.loads(snap_path.read_text())
        self.assertEqual(payload["daemons"], {})
        self.assertEqual(payload["daemons_total"], 0)
        self.assertEqual(payload["daemons_healthy"], 0)
        self.assertEqual(payload["stale_daemons"], [])


if __name__ == "__main__":
    unittest.main()

# Refactor (issue-277): single_active_audit_boundary 全分支测试覆盖见上(empty/open-actionable/queued/no-audit/expected>0)。
