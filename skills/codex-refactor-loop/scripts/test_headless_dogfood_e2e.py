#!/usr/bin/env python3
"""Headless e2e behavior tests for router, wakeup-plan, and wakeup-runner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import labels as label_catalog  # noqa: E402
from codex_refactor_loop.context import LoopContext  # noqa: E402
from codex_refactor_loop.phase9.router import Phase9Router  # noqa: E402
from codex_refactor_loop.wakeup_plan import build_plan  # noqa: E402
from codex_refactor_loop.wakeup_runner import WakeupRunner  # noqa: E402


@dataclass(frozen=True)
class FakeOwnerDecision:
    allowed: bool = True
    status: str = "owner"


class FakeControllerActions:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def dispatch_consensus_implementation(self, action: dict) -> int:
        self.calls.append(("dispatch_consensus_implementation", dict(action)))
        return 0

    def merge_pr(self, target: str) -> int:
        self.calls.append(("merge_pr", target))
        return 0


class HeadlessDogfoodFixture:
    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.logs = self.repo / ".refactor-loop" / "logs"
        self.runs = self.repo / ".refactor-loop" / "runs"
        self.prompts = self.repo / ".refactor-loop" / "prompts"
        self.state = self.repo / ".refactor-loop" / "state"
        self.fakebin = self.repo / "fakebin"
        self.issues: dict[int, dict[str, object]] = {}
        self.prs: dict[int, dict[str, object]] = {}
        self.ps_lines: list[str] = []
        self.spawn_counts: dict[str, int] = {}
        self.tool_calls: list[list[str]] = []
        self.actions = FakeControllerActions()

    def __enter__(self) -> "HeadlessDogfoodFixture":
        for path in (
            self.logs,
            self.runs,
            self.prompts,
            self.state,
            self.fakebin,
            self.repo / ".config" / "consensus-rnd",
            self.repo / ".refactor-loop" / "heartbeats",
        ):
            path.mkdir(parents=True, exist_ok=True)
        (self.repo / ".config" / "consensus-rnd" / "host.env").write_text(
            "\n".join(
                (
                    f'REPO_ROOT="{self.repo}"',
                    "GH_REPO_SLUG=owner/repo",
                    "INTEGRATION_BRANCH=auto-refact-dev",
                    "REVIEW_BASE_BRANCH=dev",
                    "CODEX_FLOOR=2",
                    "META_ESCALATION_STUCK_HOURS=999999",
                    f'CODEX_REFACTOR_LOOP_SKILL_ROOT="{SKILL_ROOT}"',
                    "",
                )
            ),
            encoding="utf-8",
        )
        (self.repo / ".refactor-loop" / ".controller-pending-events.log").write_text("", encoding="utf-8")
        (self.repo / ".version-bump.json").write_text(
            json.dumps({"files": [{"path": "package.json", "field": "version"}]}),
            encoding="utf-8",
        )
        (self.repo / "package.json").write_text(json.dumps({"version": "1.0.0-beta.1"}), encoding="utf-8")
        self._write_fake_tools()
        self.env = {
            "REPO_ROOT": str(self.repo),
            "GH_REPO_SLUG": "owner/repo",
            "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env",
            "CODEX_REFACTOR_LOOP_SKILL_ROOT": str(SKILL_ROOT),
            "META_ESCALATION_STUCK_HOURS": "999999",
            "PATH": f"{self.fakebin}:{os.environ.get('PATH', '')}",
        }
        self.ctx = LoopContext.load(repo_root=self.repo, env=self.env)
        self.patches = [
            mock.patch("codex_refactor_loop.phase9.router.require_active_controller", return_value=FakeOwnerDecision()),
            mock.patch("codex_refactor_loop.phase9.router.write_active_controller_status", lambda _ctx, _decision: None),
            mock.patch("codex_refactor_loop.wakeup_runner.require_active_controller", return_value=FakeOwnerDecision()),
            mock.patch("codex_refactor_loop.wakeup_runner.write_active_controller_status", lambda _ctx, _decision: None),
            mock.patch("codex_refactor_loop.wakeup_runner.PrMergeReadinessProjection", lambda runner=None: self),
            mock.patch("codex_refactor_loop.wakeup_runner.launch_spawn_codex_supervisor", side_effect=self.fake_spawn_supervisor),
            mock.patch.dict(os.environ, self.env, clear=False),
        ]
        for patch in self.patches:
            patch.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def _write_fake_tools(self) -> None:
        for name in ("gh", "git", "ps", "codex"):
            path = self.fakebin / name
            path.write_text(f"#!/usr/bin/env bash\nprintf '{name} fake should be patched, not executed\\n' >&2\nexit 97\n", encoding="utf-8")
            path.chmod(0o755)

    def check_pr(self, _slug: str, pr_number: int):
        runs = [type("Run", (), {"bucket": "pass", "name": "ci", "link": ""})()]
        return type(
            "Checks",
            (),
            {
                "ok": True,
                "reason": "",
                "head_sha": self.pr_head_sha(pr_number),
                "required_failed": (),
                "required_pending": (),
                "missing_required": (),
                "advisory_failed": (),
                "advisory_pending": (),
                "runs": runs,
            },
        )()

    def pr_head_sha(self, number: int) -> str:
        return str(self.prs.get(number, {}).get("headRefOid") or "")

    def add_issue(self, number: int, *, phase: str, title: str = "target", state: str = "OPEN", milestone: bool = False) -> None:
        labels = [label_catalog.MANAGED, phase, label_catalog.HUMAN_AUTO]
        if milestone:
            labels.append(label_catalog.MILESTONE_CURRENT)
        self.issues[number] = {"number": number, "title": title, "state": state, "labels": labels, "body": f"Issue body {number}"}

    def add_pr(self, number: int, *, state: str = "OPEN", head_sha: str = "abc1234") -> None:
        self.prs[number] = {
            "number": number,
            "title": f"PR {number}",
            "state": state,
            "labels": [label_catalog.MANAGED, label_catalog.PHASE_REVIEWING, label_catalog.HUMAN_AUTO],
            "headRefName": f"refactor/iter{number}-worker",
            "headRefOid": head_sha,
            "body": f"Closes #{number}",
            "mergeable": "MERGEABLE",
            "isDraft": False,
        }

    def router(self) -> Phase9Router:
        router = Phase9Router(ctx=self.ctx)
        router._spawn_codex_in_flight = lambda _log_path: False  # type: ignore[method-assign]
        return router

    def tick_router(self) -> None:
        with mock.patch("codex_refactor_loop.phase9.router.subprocess.run", side_effect=self.fake_subprocess_run):
            self.router().tick()

    def plan(self) -> dict:
        with mock.patch("codex_refactor_loop.wakeup_plan.subprocess.run", side_effect=self.fake_subprocess_run):
            return build_plan(self.repo)

    def run_runner(self):
        with mock.patch("codex_refactor_loop.wakeup_plan.subprocess.run", side_effect=self.fake_subprocess_run):
            runner = WakeupRunner(
                self.ctx,
                plan_loader=lambda repo: build_plan(repo),
                actions=self.actions,
                command_runner=self.fake_command_runner,
            )
            return runner.run_once()

    def fake_command_runner(self, command) -> subprocess.CompletedProcess[str]:
        return self.fake_subprocess_run(list(command), cwd=self.repo)

    def fake_spawn_supervisor(self, *, repo_root: Path, cd: Path, prompt: Path, log: Path, stall: int, env: dict[str, str]) -> int:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("spawned\n", encoding="utf-8")
        self.spawn_counts[str(log)] = self.spawn_counts.get(str(log), 0) + 1
        return 0

    def fake_subprocess_run(self, command, cwd=None, capture_output=True, text=True, check=False, env=None, timeout=None, **kwargs):
        args = [str(part) for part in command]
        self.tool_calls.append(args)
        if not args:
            return subprocess.CompletedProcess(args, 1, "", "")
        if args[0] == "gh":
            return self._fake_gh(args)
        if args[0] == "git":
            return self._fake_git(args)
        if args[0] == "ps":
            return subprocess.CompletedProcess(args, 0, "\n".join(self.ps_lines) + ("\n" if self.ps_lines else ""), "")
        return subprocess.CompletedProcess(args, 97, "", f"unexpected command: {args}")

    def _fake_gh(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        if args[:3] == ["gh", "issue", "list"]:
            rows = []
            for issue in self.issues.values():
                if str(issue.get("state")).upper() != "OPEN":
                    continue
                rows.append({"number": issue["number"], "title": issue["title"], "labels": [{"name": name} for name in issue["labels"]]})
            return subprocess.CompletedProcess(args, 0, json.dumps(rows), "")
        if args[:3] == ["gh", "pr", "list"]:
            rows = []
            for pr in self.prs.values():
                if str(pr.get("state")).upper() != "OPEN":
                    continue
                rows.append(
                    {
                        "number": pr["number"],
                        "title": pr["title"],
                        "labels": [{"name": name} for name in pr["labels"]],
                        "headRefName": pr["headRefName"],
                        "headRefOid": pr["headRefOid"],
                        "body": pr["body"],
                    }
                )
            return subprocess.CompletedProcess(args, 0, json.dumps(rows), "")
        if args[:2] == ["gh", "api"] and len(args) >= 3:
            return self._fake_gh_api(args)
        if args[:3] == ["gh", "pr", "view"]:
            number = int(args[3])
            pr = self.prs.get(number, {})
            if "--json" in args and "body,headRefName,headRefOid" in args:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    json.dumps({"body": pr.get("body", ""), "headRefName": pr.get("headRefName", ""), "headRefOid": pr.get("headRefOid", "")}),
                    "",
                )
            if "--jq" in args and ".headRefOid" in args:
                return subprocess.CompletedProcess(args, 0, str(pr.get("headRefOid") or ""), "")
            if "--json" in args and "mergeable,isDraft" in args:
                return subprocess.CompletedProcess(args, 0, json.dumps({"mergeable": pr.get("mergeable"), "isDraft": pr.get("isDraft")}), "")
        return subprocess.CompletedProcess(args, 97, "", f"unexpected gh command: {args}")

    def _fake_gh_api(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        path = args[2]
        if path.startswith("repos/owner/repo/issues?state=open"):
            rows = []
            for issue in self.issues.values():
                if str(issue.get("state")).upper() == "OPEN":
                    rows.append({"number": issue["number"], "title": issue["title"], "updated_at": "2026-06-05T00:00:00Z", "labels": [{"name": name} for name in issue["labels"]]})
            for pr in self.prs.values():
                if str(pr.get("state")).upper() == "OPEN":
                    rows.append({"number": pr["number"], "title": pr["title"], "updated_at": "2026-06-05T00:00:00Z", "pull_request": {"url": "https://api.github.test/pr"}, "labels": [{"name": name} for name in pr["labels"]]})
            return subprocess.CompletedProcess(args, 0, json.dumps(rows), "")
        if path.startswith("repos/owner/repo/issues/") and path.endswith("/comments?per_page=20"):
            return subprocess.CompletedProcess(args, 0, "[]", "")
        if path.startswith("repos/owner/repo/issues/"):
            number = int(path.rsplit("/", 1)[1])
            issue = self.issues.get(number)
            if issue is None:
                return subprocess.CompletedProcess(args, 1, "", "missing issue")
            payload = {"number": number, "title": issue["title"], "body": issue["body"], "state": str(issue["state"]).lower()}
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")
        if path.startswith("repos/owner/repo/pulls/"):
            number = int(path.rsplit("/", 1)[1])
            pr = self.prs.get(number)
            if pr is None:
                return subprocess.CompletedProcess(args, 1, "", "missing pr")
            return subprocess.CompletedProcess(args, 0, json.dumps({"number": number, "state": str(pr["state"]).lower(), "merged": False}), "")
        if path.startswith("repos/owner/repo/milestones"):
            return subprocess.CompletedProcess(args, 0, "[]", "")
        return subprocess.CompletedProcess(args, 97, "", f"unexpected gh api: {path}")

    def _fake_git(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        if "worktree" in args and "list" in args:
            return subprocess.CompletedProcess(args, 0, "", "")
        if "fetch" in args:
            return subprocess.CompletedProcess(args, 0, "", "")
        if any(token in args for token in ("rev-parse", "rev-list", "merge-base", "diff")):
            return subprocess.CompletedProcess(args, 1, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def pending_events(self) -> str:
        return (self.repo / ".refactor-loop" / ".controller-pending-events.log").read_text(encoding="utf-8")

    def router_ledger_rows(self) -> list[dict[str, object]]:
        path = self.repo / ".refactor-loop" / "phase9-router-ledger.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def runner_ledger_rows(self) -> list[dict[str, object]]:
        path = self.state / "wakeup-runner-ledger.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def assert_no_real_lifecycle_helper(self) -> None:
        rendered = "\n".join(" ".join(call) for call in self.tool_calls)
        forbidden_commands = (
            ("gh", "issue", "close"),
            ("gh", "issue", "edit"),
            ("gh", "pr", "merge"),
            ("gh", "pr", "close"),
            ("gh", "pr", "edit"),
            ("git", "push"),
        )
        for forbidden in forbidden_commands:
            self.testcase.assertNotIn(" ".join(forbidden), rendered)

    def spawn_next_harness_intent(self) -> str:
        plan = self.plan()
        actions = [action for action in plan["actions"] if action.get("controller_action") == "spawn_codex_harness_background"]
        self.testcase.assertTrue(actions, plan)
        action = actions[0]
        self.testcase.assertEqual("harness-spawn-intent", action["kind"])
        self.testcase.assertEqual(".refactor-loop/.controller-pending-events.log", action["source_artifact"])
        self.testcase.assertEqual("wakeup-runner-396", action["runner_authority"])
        results = self.run_runner()
        self.testcase.assertTrue([result for result in results if result.status == "applied"], results)
        log = str(action["log"])
        return log

    def complete_solver_triplet(self, issue: int, round_no: int = 1) -> None:
        for _attempt in range(3):
            missing = [
                role
                for role in ("minimal", "structural", "delete")
                if not (self.logs / f"phase9-issue{issue}-r{round_no}-{role}.log").exists()
            ]
            if not missing:
                break
            self.spawn_next_harness_intent()
        for role in ("minimal", "structural", "delete"):
            log = self.logs / f"phase9-issue{issue}-r{round_no}-{role}.log"
            self.testcase.assertTrue(log.exists(), f"missing spawned solver log for {role}")
            log.write_text(f"SOLVER_DONE:{role}:propose:ok\nEXIT=0\n", encoding="utf-8")
        ledger_keys = {str(row.get("key") or "") for row in self.router_ledger_rows()}
        for role in ("minimal", "structural", "delete"):
            self.testcase.assertIn(f"{issue}-{round_no}-{role}", ledger_keys)

    def complete_judge(
        self,
        issue: int,
        round_no: int = 1,
        *,
        scope_paths: tuple[str, ...] = ("skills/codex-refactor-loop/scripts/test_headless_dogfood_e2e.py",),
    ) -> None:
        self.tick_router()
        log = self.spawn_next_harness_intent()
        self.testcase.assertTrue(log.endswith(f"phase9-issue{issue}-r{round_no}-judge.log"))
        self.write_consensus_artifact(issue, round_no, scope_paths=scope_paths)
        Path(log).write_text("META_JUDGE_DONE:consensus:hybrid\nEXIT=0\n", encoding="utf-8")

    def write_consensus_artifact(self, issue: int, round_no: int, *, scope_paths: tuple[str, ...]) -> Path:
        artifact = self.runs / f"phase9-issue{issue}-r{round_no}-judge.md"
        artifact.write_text(
            "\n".join(
                (
                    "---",
                    f"issue: {issue}",
                    f"convergence_round: {round_no}",
                    "decision: consensus",
                    "---",
                    "## Decision",
                    "达成共识。",
                    "",
                    "## If consensus",
                    "- Implement plan (structured fields read by wakeup-plan from this judge artifact only, not from solver artifacts or prompt-body free text):",
                    "  - scope_paths:",
                    *[f"    - {path}" for path in scope_paths],
                    "  - old_pattern: isolated unit coverage only",
                    "  - new_principle: headless dogfood e2e coverage",
                    "  - verification_hints: python3 -m unittest skills/codex-refactor-loop/scripts/test_headless_dogfood_e2e.py",
                    f"- Implementation owner: dispatch implement codex with cluster_id=issue-{issue}, design_decision_path=.refactor-loop/runs/phase9-issue{issue}-r{round_no}-judge.md",
                    "",
                    "⟦AI:AUTO-LOOP⟧",
                    "META_JUDGE_DONE:consensus:hybrid",
                    "",
                )
            ),
            encoding="utf-8",
        )
        return artifact

    def write_review_evidence(self, pr: int, *, head_sha: str) -> None:
        for role in ("architect", "tests", "quality"):
            (self.runs / f"review-pr{pr}-{role}-r1.md").write_text(
                f"verdict: approve\nreviewed_head_sha: {head_sha}\nREVIEW_DONE:{pr}:{role}:approve\n",
                encoding="utf-8",
            )
            (self.logs / f"review-pr{pr}-{role}-r1.log").write_text(
                f"REVIEW_DONE:{pr}:{role}:approve\nEXIT=0\n",
                encoding="utf-8",
            )


class HeadlessDogfoodE2ETests(unittest.TestCase):
    def test_managed_design_issue_reaches_dispatch_consensus_implementation_without_controller_interaction(self) -> None:
        with HeadlessDogfoodFixture(self) as fixture:
            fixture.add_issue(496, phase=label_catalog.PHASE_DESIGN_SOLVING, title="dogfood target")

            fixture.tick_router()
            self.assertIn("HARNESS_SPAWN_INTENT", fixture.pending_events())
            fixture.complete_solver_triplet(496)
            fixture.complete_judge(496)

            plan = fixture.plan()
            executable = [
                action for action in plan["actions"]
                if action.get("controller_action") == "dispatch_consensus_implementation" and not action.get("status_only")
            ]
            self.assertEqual(1, len(executable))
            self.assertEqual(496, executable[0]["target_number"])
            self.assertEqual("completed-marker:phase9-issue496-r1-judge.log:META_JUDGE_DONE:consensus:hybrid", executable[0]["action_id"])
            self.assertEqual("wakeup-runner-396", executable[0]["runner_authority"])
            self.assertEqual(".refactor-loop/runs/phase9-issue496-r1-judge.md", executable[0]["consensus_artifact"])
            self.assertEqual(
                "- skills/codex-refactor-loop/scripts/test_headless_dogfood_e2e.py",
                executable[0]["scope_paths"],
            )

            results = fixture.run_runner()

            self.assertEqual("applied", results[0].status)
            self.assertEqual("dispatch_consensus_implementation", fixture.actions.calls[0][0])
            self.assertEqual(496, fixture.actions.calls[0][1]["target_number"])
            self.assertIn(
                {"action_id": executable[0]["action_id"], "status": "applied", "reason": ""},
                [
                    {"action_id": row.get("action_id"), "status": row.get("status"), "reason": row.get("reason")}
                    for row in fixture.runner_ledger_rows()
                ],
            )
            fixture.assert_no_real_lifecycle_helper()

    def test_closed_design_issue_does_not_redispatch_after_reload(self) -> None:
        with HeadlessDogfoodFixture(self) as fixture:
            fixture.add_issue(496, phase=label_catalog.PHASE_DESIGN_SOLVING, state="CLOSED")

            fixture.tick_router()
            first_pending = fixture.pending_events()
            fixture.tick_router()
            second_pending = fixture.pending_events()

            self.assertEqual(first_pending, second_pending)
            self.assertEqual("", first_pending)
            self.assertEqual([], fixture.router_ledger_rows())
            self.assertEqual([], [action for action in fixture.plan()["actions"] if not action.get("status_only")])
            fixture.assert_no_real_lifecycle_helper()

    def test_overlapping_scope_paths_serialize_dispatch_and_reload_preserves_runner_ledger(self) -> None:
        with HeadlessDogfoodFixture(self) as fixture:
            fixture.add_issue(330, phase=label_catalog.PHASE_IMPLEMENTING, milestone=True)
            fixture.add_issue(331, phase=label_catalog.PHASE_IMPLEMENTING, milestone=True)
            fixture.write_consensus_artifact(330, 1, scope_paths=("skills/codex-refactor-loop/scripts/codex_refactor_loop/wakeup_plan.py",))
            fixture.write_consensus_artifact(331, 1, scope_paths=("skills/codex-refactor-loop/scripts/codex_refactor_loop/wakeup_plan.py",))
            (fixture.logs / "phase9-issue330-r1-judge.log").write_text("META_JUDGE_DONE:consensus:hybrid\nEXIT=0\n", encoding="utf-8")
            (fixture.logs / "phase9-issue331-r1-judge.log").write_text("META_JUDGE_DONE:consensus:hybrid\nEXIT=0\n", encoding="utf-8")

            plan = fixture.plan()
            dispatches = [action for action in plan["actions"] if action.get("controller_action") == "dispatch_consensus_implementation"]
            executable = [action for action in dispatches if not action.get("status_only")]
            waiting = [action for action in dispatches if action.get("suppressed_reason") == "scope_conflict_waiting"]
            self.assertEqual(1, len(executable))
            self.assertGreaterEqual(len(waiting), 1)
            self.assertEqual({330, 331}, {action["target_number"] for action in dispatches})
            self.assertTrue(all("runner_authority" not in action for action in waiting))
            self.assertTrue(all(action["scope_paths"] == executable[0]["scope_paths"] for action in waiting))

            first = fixture.run_runner()
            second = fixture.run_runner()

            self.assertEqual("applied", first[0].status)
            self.assertEqual("skipped", second[0].status)
            self.assertEqual(1, len([call for call in fixture.actions.calls if call[0] == "dispatch_consensus_implementation"]))
            self.assertEqual(
                ["applied", "skipped"],
                [
                    row.get("status")
                    for row in fixture.runner_ledger_rows()
                    if row.get("action_id") == executable[0]["action_id"]
                ],
            )
            fixture.assert_no_real_lifecycle_helper()

    def test_batch_and_per_task_lock_do_not_duplicate_spawn(self) -> None:
        with HeadlessDogfoodFixture(self) as fixture:
            fixture.add_issue(496, phase=label_catalog.PHASE_DESIGN_SOLVING)
            fixture.tick_router()
            self.assertEqual(3, fixture.pending_events().count("HARNESS_SPAWN_INTENT"))

            first_results = fixture.run_runner()
            second_results = fixture.run_runner()
            third_results = fixture.run_runner()

            self.assertEqual(2, len([result for result in first_results if result.status == "applied"]))
            self.assertEqual(1, len([result for result in second_results if result.status == "applied"]))
            self.assertEqual([], third_results)
            self.assertEqual(3, len(fixture.spawn_counts))
            self.assertTrue(all(count == 1 for count in fixture.spawn_counts.values()))
            self.assertEqual(
                3,
                len(
                    [
                        row
                        for row in fixture.runner_ledger_rows()
                        if str(row.get("action_id") or "").startswith("harness-spawn-intent:")
                    ]
                ),
            )
            fixture.assert_no_real_lifecycle_helper()

    def test_review_gate_merge_projection_calls_fake_named_helper_only(self) -> None:
        with HeadlessDogfoodFixture(self) as fixture:
            fixture.add_pr(77, head_sha="abc1234")
            fixture.write_review_evidence(77, head_sha="abc1234")

            plan = fixture.plan()
            review_actions = [action for action in plan["actions"] if action.get("controller_action") == "review_gate"]
            self.assertTrue(review_actions)
            results = fixture.run_runner()

            self.assertEqual("applied", results[0].status)
            self.assertEqual([("merge_pr", "77")], fixture.actions.calls)
            self.assertEqual(review_actions[0]["action_id"], results[0].action_id)
            self.assertEqual("wakeup-runner-396", review_actions[0]["runner_authority"])
            fixture.assert_no_real_lifecycle_helper()


if __name__ == "__main__":
    unittest.main()
