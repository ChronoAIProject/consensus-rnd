#!/usr/bin/env python3
"""Behavior tests for wakeup_plan.py prioritized next-action output."""

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
WAKEUP_PLAN = SKILL_ROOT / "scripts" / "wakeup_plan.py"


class WakeupPlanBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.logs = self.repo / ".refactor-loop" / "logs"
        self.fakebin = self.repo / "fakebin"
        self.logs.mkdir(parents=True)
        self.fakebin.mkdir()
        (self.repo / ".refactor-loop" / "host.env").write_text(
            "REPO_ROOT=/tmp/repo\nGH_REPO_SLUG=owner/repo\nCODEX_FLOOR=5\n",
            encoding="utf-8",
        )
        (self.repo / ".refactor-loop" / ".controller-pending-events.log").write_text("", encoding="utf-8")
        self.write_fresh_heartbeats()
        self.write_fake_gh()
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
                  printf 'bash /skill/spawn-codex.sh --cd %s/.worktrees/task-%s --log %s/.refactor-loop/logs/task-%s.log\n' "$repo" "$i" "$repo" "$i"
                  printf 'bash -c echo /skill/spawn-codex.sh --cd %s/.worktrees/task-%s\n' "$repo" "$i"
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
            }
        )
        result = subprocess.run(
            ["python3", str(WAKEUP_PLAN), "--repo-root", str(self.repo)],
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

    def test_audit_fallback_when_latest_audit_is_not_none_zero(self) -> None:
        plan = self.run_plan()

        self.assertEqual(plan["actions"], [])
        self.assertEqual(plan["recommendation"], "RECOMMEND:audit")

    def test_all_empty_after_audit_none_zero_outputs_concurrency_low(self) -> None:
        (self.logs / "audit-iter-8.log").write_text(
            "AUDIT_DONE:none:0\nEXIT=0\n",
            encoding="utf-8",
        )

        plan = self.run_plan()

        self.assertEqual(plan["actions"], [])
        self.assertEqual(plan["recommendation"], "CONCURRENCY_LOW:no-work-after-audit-none")

    def test_daemon_health_reports_stale_and_missing_with_restart_hint(self) -> None:
        heartbeats = self.repo / ".refactor-loop" / "heartbeats"
        for path in heartbeats.glob("*.ts"):
            path.unlink()
        stale = int(time.time()) - 120
        (heartbeats / "concurrency_monitor.ts").write_text(str(stale), encoding="utf-8")

        plan = self.run_plan()

        health = plan["daemon_health"]
        self.assertEqual(health["recommendation"], "restart-daemons.sh")
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

    def test_fixed_point_outputs_concurrency_low_without_fake_hard_gate_work(self) -> None:
        (self.logs / "audit-iter-8.log").write_text(
            "AUDIT_DONE:none:0\nEXIT=0\n",
            encoding="utf-8",
        )

        plan, stdout = self.run_plan_with_stdout(ps_count=0)

        self.assertEqual(plan["concurrency"]["deficit"], 5)
        self.assertEqual(plan["recommendation"], "CONCURRENCY_LOW:no-work-after-audit-none")
        self.assertFalse(plan["hard_gate"]["active"])
        self.assertEqual(plan["hard_gate"]["reason"], "CONCURRENCY_LOW:no-work-after-audit-none")
        self.assertNotIn("HARD_GATE:dispatch_required=", stdout)


if __name__ == "__main__":
    unittest.main()
