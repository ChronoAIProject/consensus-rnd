#!/usr/bin/env python3
"""Behavior tests for consensus-rnd-cli wakeup-plan prioritized next-action output."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
SKILL_ROOT = SCRIPT_PATH.parents[1]
WAKEUP_PLAN = SKILL_ROOT / "scripts" / "consensus-rnd-cli"


class WakeupPlanBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.logs = self.repo / ".refactor-loop" / "logs"
        self.fakebin = self.repo / "fakebin"
        self.logs.mkdir(parents=True)
        self.fakebin.mkdir()
        (self.repo / ".refactor-loop" / "host.env").write_text(
            f"REPO_ROOT={self.repo}\nGH_REPO_SLUG=owner/repo\nCODEX_FLOOR=5\n",
            encoding="utf-8",
        )
        (self.repo / ".refactor-loop" / ".controller-pending-events.log").write_text("", encoding="utf-8")
        self.write_fresh_heartbeats()
        self.write_fake_gh()
        self.write_fake_git()
        self.write_fake_ps()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_fake_gh(self) -> None:
        gh = self.fakebin / "gh"
        gh.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                fixture="${WAKEUP_PLAN_GH_FIXTURE:-empty}"
                args="$*"
                if [[ "$1 $2" == "issue list" ]]; then
                  case "$fixture" in
                    milestone)
                      printf '[{"number":20,"title":"milestone issue","labels":[{"name":"auto-loop"},{"name":"🎯 milestone"},{"name":"🔍 phase:design-solving"}]},{"number":10,"title":"ordinary issue","labels":[{"name":"auto-loop"},{"name":"🔧 phase:fixing"}]}]\n'
                      ;;
                    existing)
                      printf '[{"number":10,"title":"ordinary issue","labels":[{"name":"auto-loop"},{"name":"🔧 phase:fixing"}]}]\n'
                      ;;
                    many_active)
                      printf '['
                      for i in 1 2 3 4 5 6; do
                        [[ "$i" != "1" ]] && printf ','
                        printf '{"number":%s,"title":"active issue %s","labels":[{"name":"auto-loop"},{"name":"🔧 phase:fixing"}]}' "$i" "$i"
                      done
                      printf ']\n'
                      ;;
                    *)
                      printf '[]\n'
                      ;;
                  esac
                  exit 0
                fi
                if [[ "$1 $2" == "pr list" ]]; then
                  case "$fixture" in
                    unpushed|unpushed_fetch_fail|unpushed_no_ahead|unpushed_no_remote|unpushed_no_worktree)
                      printf '[{"number":77,"title":"worker output PR","headRefName":"refactor/iter77-worker","labels":[{"name":"auto-loop"},{"name":"👀 phase:reviewing"}]}]\n'
                      ;;
                    ci_red)
                      printf '[{"number":31,"title":"red PR","labels":[{"name":"auto-loop"},{"name":"⚙️ phase:ci-running"}]}]\n'
                      ;;
                    milestone)
                      printf '[{"number":30,"title":"milestone PR","labels":[{"name":"auto-loop"},{"name":"🎯 milestone"},{"name":"👀 phase:reviewing"}]}]\n'
                      ;;
                    *)
                      printf '[]\n'
                      ;;
                  esac
                  exit 0
                fi
                if [[ "$1 $2" == "pr checks" ]]; then
                  if [[ "$fixture" == "ci_red" && "$args" == *"31"* ]]; then
                    printf '[{"bucket":"fail"}]\n'
                  else
                    printf '[]\n'
                  fi
                  exit 0
                fi
                if [[ "$1 $2" == "issue view" || "$1 $2" == "pr view" ]]; then
                  printf '{"comments":[]}\n'
                  exit 0
                fi
                printf '[]\n'
                exit 0
                """
            ).lstrip(),
            encoding="utf-8",
        )
        gh.chmod(0o755)

    def write_fake_git(self) -> None:
        git = self.fakebin / "git"
        git.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                fixture="${WAKEUP_PLAN_GH_FIXTURE:-empty}"
                if [[ -n "${WAKEUP_PLAN_GIT_LOG:-}" ]]; then
                  printf '%s\n' "$*" >> "$WAKEUP_PLAN_GIT_LOG"
                fi
                if [[ "$*" == *"fetch origin --quiet"* ]]; then
                  [[ "$fixture" == "unpushed_fetch_fail" ]] && exit 42
                  exit 0
                fi
                if [[ "$*" == *"worktree list --porcelain"* ]]; then
                  [[ "$fixture" == "unpushed_no_worktree" ]] && exit 0
                  printf 'worktree %s/.worktrees/pr77\nbranch refs/heads/refactor/iter77-worker\n\n' "$WAKEUP_PLAN_REPO_ROOT"
                  exit 0
                fi
                if [[ "$*" == *"rev-parse --verify HEAD"* ]]; then
                  printf 'local-sha\n'
                  exit 0
                fi
                if [[ "$*" == *"rev-parse --verify refs/remotes/origin/refactor/iter77-worker"* ]]; then
                  [[ "$fixture" == "unpushed_no_remote" ]] && exit 1
                  printf 'remote-sha\n'
                  exit 0
                fi
                if [[ "$*" == *"rev-list --count refs/remotes/origin/refactor/iter77-worker..HEAD"* ]]; then
                  if [[ "$fixture" == "unpushed_no_ahead" ]]; then
                    printf '0\n'
                  else
                    printf '2\n'
                  fi
                  exit 0
                fi
                exit 0
                """
            ).lstrip(),
            encoding="utf-8",
        )
        git.chmod(0o755)

    def write_fake_ps(self) -> None:
        ps = self.fakebin / "ps"
        ps.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                count="${WAKEUP_PLAN_PS_COUNT:-5}"
                repo="${WAKEUP_PLAN_REPO_ROOT:?missing repo}"
                i=0
                while [[ "$i" -lt "$count" ]]; do
                  printf 'python3 /skill/consensus-rnd-cli spawn-codex --cd %s/.worktrees/task-%s --log %s/.refactor-loop/logs/task-%s.log\n' "$repo" "$i" "$repo" "$i"
                  printf 'bash -c echo /skill/consensus-rnd-cli spawn-codex --cd %s/.worktrees/task-%s\n' "$repo" "$i"
                  i=$((i + 1))
                done
                """
            ).lstrip(),
            encoding="utf-8",
        )
        ps.chmod(0o755)

    def write_fresh_heartbeats(self) -> None:
        heartbeats = self.repo / ".refactor-loop" / "heartbeats"
        heartbeats.mkdir(parents=True, exist_ok=True)
        now = str(int(time.time()))
        for name in (
            "concurrency_monitor",
            "comment-monitor",
            "codex-progress-reporter",
            "dev_sync_daemon",
            "phase9_router_daemon",
        ):
            (heartbeats / f"{name}.ts").write_text(now, encoding="utf-8")

    def run_plan(self, *, fixture: str = "empty", ps_count: int = 5) -> dict:
        return self.run_plan_with_stdout(fixture=fixture, ps_count=ps_count)[0]

    def run_plan_with_stdout(self, *, fixture: str = "empty", ps_count: int = 5) -> tuple[dict, str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.fakebin}{os.pathsep}{env.get('PATH', '')}",
                "CODEX_FLOOR": "5",
                "GH_REPO_SLUG": "owner/repo",
                "WAKEUP_PLAN_GH_FIXTURE": fixture,
                "WAKEUP_PLAN_PS_COUNT": str(ps_count),
                "WAKEUP_PLAN_REPO_ROOT": str(self.repo.resolve()),
                "WAKEUP_PLAN_GIT_LOG": str(self.repo / "git-commands.log"),
            }
        )
        result = subprocess.run(
            ["python3", str(WAKEUP_PLAN), "wakeup-plan", "--repo-root", str(self.repo)],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        json_text = result.stdout
        if "\nHARD_GATE:" in json_text:
            json_text = json_text.split("\nHARD_GATE:", 1)[0]
        return json.loads(json_text), result.stdout

    def write_completed_log(self, name: str, marker: str) -> None:
        (self.logs / name).write_text(
            f"prompt echo {marker}:<placeholder>\n"
            "body\n"
            f"{marker}:real\n"
            "EXIT=0\n",
            encoding="utf-8",
        )

    def test_completed_marker_routes_before_ci_red(self) -> None:
        self.write_completed_log("implement-issue20.log", "IMPLEMENT_DONE")

        plan = self.run_plan(fixture="ci_red")

        self.assertEqual(plan["actions"][0]["kind"], "completed-marker")
        self.assertEqual(plan["actions"][0]["actor"], "controller")
        self.assertIn("IMPLEMENT_DONE:real", plan["actions"][0]["marker"])

    def test_unpushed_worker_output_routes_before_completed_marker_ci_and_existing_issue(self) -> None:
        self.write_completed_log("fix-pr77-r3.log", "FIX_DONE")

        plan = self.run_plan(fixture="unpushed")

        self.assertEqual(plan["actions"][0]["kind"], "unpushed-worker-output")
        self.assertEqual(plan["actions"][0]["item"], "PR #77")
        self.assertEqual(plan["actions"][0]["line"], "UNPUSHED_WORKER_OUTPUT:77:2")
        self.assertEqual(plan["actions"][0]["head_ref"], "refactor/iter77-worker")
        self.assertIn("safe-push origin refactor/iter77-worker", plan["actions"][0]["suggested_command"])
        kinds = [action["kind"] for action in plan["actions"]]
        self.assertLess(kinds.index("unpushed-worker-output"), kinds.index("completed-marker"))
        self.assertLess(kinds.index("unpushed-worker-output"), kinds.index("existing-issue"))

    def test_unpushed_worker_output_fetch_failure_fails_closed(self) -> None:
        plan = self.run_plan(fixture="unpushed_fetch_fail")

        self.assertNotIn("unpushed-worker-output", [action["kind"] for action in plan["actions"]])

    def test_unpushed_worker_output_requires_open_auto_loop_pr_local_worktree_remote_ref_and_ahead(self) -> None:
        for fixture in ("empty", "unpushed_no_worktree", "unpushed_no_remote", "unpushed_no_ahead"):
            with self.subTest(fixture=fixture):
                plan = self.run_plan(fixture=fixture)
                self.assertNotIn("unpushed-worker-output", [action["kind"] for action in plan["actions"]])

    def test_unpushed_worker_output_uses_only_allowlisted_git_topology_probes(self) -> None:
        self.run_plan(fixture="unpushed")

        commands = (self.repo / "git-commands.log").read_text(encoding="utf-8").splitlines()
        allowed_fragments = (
            "-C",
            "fetch origin --quiet",
            "worktree list --porcelain",
            "rev-parse --verify HEAD",
            "rev-parse --verify refs/remotes/origin/refactor/iter77-worker",
            "rev-list --count refs/remotes/origin/refactor/iter77-worker..HEAD",
        )
        self.assertTrue(any("fetch origin --quiet" in command for command in commands))
        self.assertTrue(any("worktree list --porcelain" in command for command in commands))
        self.assertTrue(any("rev-list --count refs/remotes/origin/refactor/iter77-worker..HEAD" in command for command in commands))
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(any(fragment in command for fragment in allowed_fragments))
                self.assertNotRegex(command, r"\b(push|commit|checkout|switch|reset|rebase|merge|tag)\b")
                self.assertNotRegex(command, r"\b(add|remove|prune)\b")

    def test_ci_red_routes_before_no_gap(self) -> None:
        (self.repo / ".refactor-loop" / ".concurrency-alert.log").write_text(
            "[2026-05-29T00:00:00Z] P0 no-gap-violation: fixture\n",
            encoding="utf-8",
        )

        plan = self.run_plan(fixture="ci_red")

        self.assertEqual(plan["actions"][0]["kind"], "ci-red")
        self.assertEqual(plan["actions"][0]["item"], "PR #31")
        kinds = [action["kind"] for action in plan["actions"]]
        self.assertLess(kinds.index("ci-red"), kinds.index("no-gap-violation"))

    def test_no_gap_routes_before_milestone(self) -> None:
        (self.repo / ".refactor-loop" / ".concurrency-alert.log").write_text(
            "[2026-05-29T00:00:00Z] P0 no-gap-violation: fixture\n",
            encoding="utf-8",
        )

        plan = self.run_plan(fixture="milestone")

        kinds = [action["kind"] for action in plan["actions"]]
        self.assertEqual(kinds[0], "no-gap-violation")
        self.assertLess(kinds.index("no-gap-violation"), kinds.index("existing-issue"))

    def test_milestone_labeled_items_route_before_ordinary_existing_issue(self) -> None:
        plan = self.run_plan(fixture="milestone")

        actions = [action for action in plan["actions"] if action["kind"] == "existing-issue"]
        self.assertGreaterEqual(len(actions), 3)
        self.assertEqual(actions[0]["item"], "issue #20")
        self.assertEqual(actions[1]["item"], "PR #30")
        self.assertTrue(actions[0]["milestone"])
        self.assertFalse(actions[-1]["milestone"])

    def test_existing_issue_routes_before_audit_fallback(self) -> None:
        plan = self.run_plan(fixture="existing")

        self.assertEqual(plan["actions"][0]["kind"], "existing-issue")
        self.assertEqual(plan["actions"][0]["item"], "issue #10")
        self.assertNotEqual(plan.get("recommendation"), "RECOMMEND:audit")

    def test_github_action_queries_only_open_auto_loop_items(self) -> None:
        source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py").read_text(encoding="utf-8")

        self.assertIn('"--state", "open"', source)
        self.assertNotIn('"--state", "closed"', source)
        self.assertNotIn('"--state", "merged"', source)

    def test_audit_fallback_when_latest_audit_is_not_none_zero(self) -> None:
        plan = self.run_plan()

        self.assertEqual(plan["actions"], [])
        self.assertEqual(plan["recommendation"], "RECOMMEND:audit")

    def test_all_empty_after_audit_none_zero_still_recommends_audit(self) -> None:
        (self.logs / "audit-iter-8.log").write_text(
            "AUDIT_DONE:none:0\nEXIT=0\n",
            encoding="utf-8",
        )

        plan = self.run_plan()

        self.assertEqual(plan["actions"], [])
        self.assertEqual(plan["recommendation"], "RECOMMEND:audit")

    def test_daemon_health_ignores_solver_text_without_ts_heartbeat(self) -> None:
        heartbeats = self.repo / ".refactor-loop" / "heartbeats"
        for path in heartbeats.glob("*.ts"):
            path.unlink()
        (heartbeats / "solver-output.log").write_text(str(int(time.time())), encoding="utf-8")

        plan = self.run_plan()

        health = plan["daemon_health"]
        self.assertTrue(all(item["name"] != "solver-output" for item in health["items"]))
        self.assertTrue(any(item["name"] == "concurrency_monitor" and item["status"] == "missing" for item in health["items"]))

    def test_daemon_health_reports_stale_and_missing_with_restart_hint(self) -> None:
        heartbeats = self.repo / ".refactor-loop" / "heartbeats"
        for path in heartbeats.glob("*.ts"):
            path.unlink()
        stale = int(time.time()) - 120
        (heartbeats / "concurrency_monitor.ts").write_text(str(stale), encoding="utf-8")

        plan = self.run_plan()

        health = plan["daemon_health"]
        self.assertEqual(health["recommendation"], "consensus-rnd-cli restart-daemons")
        self.assertTrue(any(item["name"] == "concurrency_monitor" and item["status"] == "stale" for item in health["items"]))
        self.assertTrue(any(item["status"] == "missing" for item in health["items"]))

    def test_deficit_calculates_from_floor_when_actual_below_target(self) -> None:
        plan, stdout = self.run_plan_with_stdout(ps_count=2)

        self.assertEqual(plan["concurrency"]["actual"], 2)
        self.assertEqual(plan["concurrency"]["floor"], 5)
        self.assertEqual(plan["concurrency"]["target"], 5)
        self.assertEqual(plan["concurrency"]["deficit"], 3)
        self.assertEqual(plan["hard_gate"]["dispatch_required"], 3)
        self.assertIn("HARD_GATE:dispatch_required=3", stdout)

    def test_deficit_uses_expected_active_tasks_when_above_floor(self) -> None:
        plan, stdout = self.run_plan_with_stdout(fixture="many_active", ps_count=1)

        self.assertEqual(plan["concurrency"]["expected_from_active_tasks"], 6)
        self.assertEqual(plan["concurrency"]["target"], 6)
        self.assertEqual(plan["concurrency"]["deficit"], 5)
        self.assertIn("HARD_GATE:dispatch_required=5", stdout)

    def test_no_hard_gate_when_actual_meets_target(self) -> None:
        plan, stdout = self.run_plan_with_stdout(ps_count=5)

        self.assertEqual(plan["concurrency"]["deficit"], 0)
        self.assertFalse(plan["hard_gate"]["active"])
        self.assertNotIn("HARD_GATE:dispatch_required=", stdout)

    def test_fixed_point_keeps_hard_gate_and_audit_fallback(self) -> None:
        (self.logs / "audit-iter-8.log").write_text(
            "AUDIT_DONE:none:0\nEXIT=0\n",
            encoding="utf-8",
        )

        plan, stdout = self.run_plan_with_stdout(ps_count=0)

        self.assertEqual(plan["concurrency"]["deficit"], 5)
        self.assertEqual(plan["recommendation"], "RECOMMEND:audit")
        self.assertTrue(plan["hard_gate"]["active"])
        self.assertIsNone(plan["hard_gate"]["reason"])
        self.assertIn("HARD_GATE:dispatch_required=5", stdout)


if __name__ == "__main__":
    unittest.main()
