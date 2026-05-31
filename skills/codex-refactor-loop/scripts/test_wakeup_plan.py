#!/usr/bin/env python3
"""Behavior tests for consensus-rnd-cli wakeup-plan prioritized next-action output."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
SKILL_ROOT = SCRIPT_PATH.parents[1]
WAKEUP_PLAN = SKILL_ROOT / "scripts" / "consensus-rnd-cli"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from codex_refactor_loop import labels as label_catalog  # noqa: E402
from codex_refactor_loop.workflow_stages import assert_stage_slug  # noqa: E402


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
                cmd1="$1"
                cmd2="$2"
                label=""
                while [[ "$#" -gt 0 ]]; do
                  if [[ "$1" == "--label" ]]; then
                    label="$2"
                    break
                  fi
                  shift
                done
                if [[ -n "${WAKEUP_PLAN_GH_QUERY_LOG:-}" && "$args" == *" list "* && -n "$label" ]]; then
                  printf '%s %s\n' "$cmd1" "$label" >> "$WAKEUP_PLAN_GH_QUERY_LOG"
                fi
                if [[ "$cmd1 $cmd2" == "issue list" ]]; then
                  case "$fixture" in
                    managed_dual_read)
                      case "$label" in
                        crnd:lifecycle:managed)
                          printf '[{"number":81,"title":"canonical issue","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:implementing"},{"name":"crnd:human:auto"}]}]\n'
                          ;;
                        auto-loop)
                          printf '[{"number":81,"title":"canonical issue","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:implementing"},{"name":"crnd:human:auto"}]},{"number":82,"title":"legacy issue","labels":[{"name":"auto-loop"},{"name":"🔧 phase:fixing"},{"name":"🤖 human:codex"}]}]\n'
                          ;;
                        phase9-auto-solve|refactor-design-needed)
                          printf '[]\n'
                          ;;
                        *)
                          printf '[]\n'
                          ;;
                      esac
                      ;;
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
                    represented_parent)
                      printf '[{"number":239,"title":"represented parent","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:implementing"},{"name":"crnd:human:auto"}]}]\n'
                      ;;
                    pr_open_parent)
                      printf '[{"number":239,"title":"parent issue with open PR","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:pr-open"},{"name":"crnd:human:auto"}]}]\n'
                      ;;
                    non_action_statuses)
                      printf '[{"number":40,"title":"blocked issue","labels":[{"name":"auto-loop"},{"name":"⏸️ phase:blocked"}]},{"number":41,"title":"merged issue","labels":[{"name":"auto-loop"},{"name":"🎉 phase:merged"}]},{"number":44,"title":"parent issue with child PR","labels":[{"name":"auto-loop"},{"name":"crnd:phase:pr-open"}]}]\n'
                      ;;
                    *)
                      printf '[]\n'
                      ;;
                  esac
                  exit 0
                fi
                if [[ "$cmd1 $cmd2" == "pr list" ]]; then
                  case "$fixture" in
                    managed_dual_read)
                      case "$label" in
                        crnd:lifecycle:managed)
                          printf '[{"number":91,"title":"canonical PR","headRefName":"impl/canonical","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
                          ;;
                        auto-loop)
                          printf '[{"number":91,"title":"canonical PR","headRefName":"impl/canonical","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
                          ;;
                        phase9-auto-solve)
                          printf '[]\n'
                          ;;
                        refactor-design-needed)
                          printf '[{"number":92,"title":"legacy PR","headRefName":"impl/legacy","labels":[{"name":"refactor-design-needed"},{"name":"🔍 phase:design-solving"},{"name":"🤖 human:auto-推进"}]}]\n'
                          ;;
                        *)
                          printf '[]\n'
                          ;;
                      esac
                      ;;
                    unpushed|unpushed_fetch_fail|unpushed_no_ahead|unpushed_no_remote|unpushed_no_worktree)
                      printf '[{"number":77,"title":"worker output PR","headRefName":"refactor/iter77-worker","labels":[{"name":"auto-loop"},{"name":"👀 phase:reviewing"}]}]\n'
                      ;;
                    unpushed_head_dash)
                      printf '[{"number":78,"title":"unsafe dash head","headRefName":"-bad","labels":[{"name":"auto-loop"},{"name":"👀 phase:reviewing"}]}]\n'
                      ;;
                    unpushed_head_space)
                      printf '[{"number":79,"title":"unsafe space head","headRefName":"bad ref","labels":[{"name":"auto-loop"},{"name":"👀 phase:reviewing"}]}]\n'
                      ;;
                    unpushed_head_control)
                      printf '[{"number":80,"title":"unsafe control head","headRefName":"bad\\u0001ref","labels":[{"name":"auto-loop"},{"name":"👀 phase:reviewing"}]}]\n'
                      ;;
                    ci_red)
                      printf '[{"number":31,"title":"red PR","labels":[{"name":"auto-loop"},{"name":"⚙️ phase:ci-running"}]}]\n'
                      ;;
                    represented_parent)
                      printf '[{"number":255,"title":"child PR","headRefName":"impl/issue239","body":"Closes #239","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
                      ;;
                    pr_open_parent)
                      printf '[]\n'
                      ;;
                    milestone)
                      printf '[{"number":30,"title":"milestone PR","labels":[{"name":"auto-loop"},{"name":"🎯 milestone"},{"name":"👀 phase:reviewing"}]}]\n'
                      ;;
                    non_action_statuses)
                      printf '[{"number":42,"title":"non-red CI PR","labels":[{"name":"auto-loop"},{"name":"⚙️ phase:ci-running"}]},{"number":43,"title":"merged PR","labels":[{"name":"auto-loop"},{"name":"🎉 phase:merged"}]}]\n'
                      ;;
                    *)
                      printf '[]\n'
                      ;;
                  esac
                  exit 0
                fi
                if [[ "$cmd1 $cmd2" == "pr checks" ]]; then
                  if [[ "$fixture" == "ci_red" && "$args" == *"31"* ]]; then
                    printf '[{"bucket":"fail"}]\n'
                  else
                    printf '[]\n'
                  fi
                  exit 0
                fi
                if [[ "$cmd1 $cmd2" == "issue view" || "$cmd1 $cmd2" == "pr view" ]]; then
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
                "WAKEUP_PLAN_GH_QUERY_LOG": str(self.repo / "gh-query-labels.log"),
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

    def test_review_done_completed_marker_remains_policy_free(self) -> None:
        # Refactor (iter203/issue-203): Old pattern: controller decisions were
        # split across peek, wakeup-plan, phase9-router, and concurrency. New
        # principle: keep REVIEW_DONE as a completed marker, without adding
        # review-gate readiness facts or lifecycle action vocabulary.
        self.write_completed_log("review-pr123-architect-r1.log", "REVIEW_DONE:123:architect:approve")

        plan = self.run_plan()

        action = plan["actions"][0]
        self.assertEqual(action["kind"], "completed-marker")
        self.assertEqual(action["phase"], "review-gate")
        self.assertEqual(action["actor"], "controller-or-fix-codex")
        self.assertEqual(action["item"], "PR #123")
        self.assertNotIn("consensus", action)
        self.assertNotIn("merge_command", action)
        self.assertNotIn("controller_action", action)
        self.assertNotIn("review-gate-readiness", action)
        self.assertNotIn("review_gate_actions", action)

    def test_review_gate_source_does_not_add_readiness_or_action_vocabulary(self) -> None:
        wakeup_source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py").read_text(encoding="utf-8")

        for token in (
            "review-gate-readiness",
            "review_gate_actions",
            "merge_command",
            "MERGE_READY",
            "WAIT_EXPLICIT_APPROVAL",
            "gh pr merge",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, wakeup_source)

    def test_unpushed_worker_output_routes_before_completed_marker_ci_and_existing_issue(self) -> None:
        self.write_completed_log("fix-pr77-r3.log", "FIX_DONE")

        plan = self.run_plan(fixture="unpushed")

        self.assertEqual(plan["actions"][0]["kind"], "unpushed-worker-output")
        self.assertEqual(plan["actions"][0]["item"], "PR #77")
        self.assertEqual(plan["actions"][0]["line"], "UNPUSHED_WORKER_OUTPUT:77:2")
        self.assertEqual(plan["actions"][0]["head_ref"], "refactor/iter77-worker")
        self.assertEqual(plan["actions"][0]["controller_action"], "safe_push")
        self.assertTrue(plan["actions"][0]["no_lifecycle_authority"])
        self.assertNotIn("suggested_command", plan["actions"][0])
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

    def test_unpushed_worker_output_ignores_unsafe_head_ref_before_worktree_git_probes(self) -> None:
        for fixture in ("unpushed_head_dash", "unpushed_head_space", "unpushed_head_control"):
            with self.subTest(fixture=fixture):
                plan = self.run_plan(fixture=fixture)
                command_log = self.repo / "git-commands.log"
                commands = command_log.read_text(encoding="utf-8").splitlines() if command_log.exists() else []

                self.assertNotIn("unpushed-worker-output", [action["kind"] for action in plan["actions"]])
                self.assertFalse(any("rev-parse --verify HEAD" in command for command in commands))
                self.assertFalse(any("rev-list --count" in command for command in commands))

    def test_unpushed_worker_output_uses_only_allowlisted_git_topology_probes(self) -> None:
        self.run_plan(fixture="unpushed")

        commands = (self.repo / "git-commands.log").read_text(encoding="utf-8").splitlines()
        repo_root = str(self.repo.resolve())
        worktree = f"{repo_root}/.worktrees/pr77"
        allowed_commands = (
            f"-C {repo_root} fetch origin --quiet",
            f"-C {repo_root} worktree list --porcelain",
            f"-C {worktree} rev-parse --verify HEAD",
            f"-C {worktree} rev-parse --verify refs/remotes/origin/refactor/iter77-worker",
            f"-C {worktree} rev-list --count refs/remotes/origin/refactor/iter77-worker..HEAD",
        )
        self.assertEqual(commands, list(allowed_commands))
        for command in commands:
            with self.subTest(command=command):
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
        self.assertEqual(plan["actions"][0]["actor"], "remote-ci-fix-codex")
        kinds = [action["kind"] for action in plan["actions"]]
        self.assertLess(kinds.index("ci-red"), kinds.index("no-gap-violation"))

    def test_ci_red_check_projection_requests_triage_fields_without_remote_ci_done_marker(self) -> None:
        plan = self.run_plan(fixture="ci_red")

        self.assertEqual(plan["actions"][0]["kind"], "ci-red")
        self.assertEqual(plan["actions"][0]["actor"], "remote-ci-fix-codex")
        self.assertNotIn("REMOTE_CI_DONE", json.dumps(plan))

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

    def test_load_github_items_queries_canonical_and_legacy_managed_labels_once(self) -> None:
        plan = self.run_plan(fixture="managed_dual_read")

        existing_items = [action["item"] for action in plan["actions"] if action["kind"] == "existing-issue"]
        self.assertEqual(existing_items, ["issue #81", "issue #82", "PR #91", "PR #92"])
        query_log = (self.repo / "gh-query-labels.log").read_text(encoding="utf-8").splitlines()
        expected = [
            f"{kind} {label}"
            for kind in ("issue", "pr")
            for label in label_catalog.query_labels_for(label_catalog.MANAGED)
        ]
        self.assertEqual(query_log, expected)

    def test_existing_issue_skips_non_action_statuses_but_preserves_red_ci(self) -> None:
        plan = self.run_plan(fixture="non_action_statuses")

        self.assertEqual([action for action in plan["actions"] if action["kind"] == "existing-issue"], [])
        self.assertNotIn(
            {"id": "#44", "kind": "issue", "phase": label_catalog.PHASE_PR_OPEN, "expected": 0},
            plan["concurrency"]["expected_breakdown"],
        )

        red_plan = self.run_plan(fixture="ci_red")
        by_kind = {action["kind"]: action for action in red_plan["actions"]}
        self.assertEqual(by_kind["ci-red"]["item"], "PR #31")
        self.assertEqual([action for action in red_plan["actions"] if action["kind"] == "existing-issue"], [])

    def test_represented_parent_issue_routes_and_expected_workers_belong_to_child_pr(self) -> None:
        plan = self.run_plan(fixture="represented_parent")

        actions = [action for action in plan["actions"] if action["kind"] == "existing-issue"]
        self.assertEqual([action["item"] for action in actions], ["PR #255"])
        self.assertEqual(
            plan["concurrency"]["expected_breakdown"],
            [{"expected": 1, "id": "#255", "kind": "pr", "phase": label_catalog.PHASE_REVIEWING}],
        )

    def test_pr_open_parent_issue_is_non_action_with_zero_expected_workers(self) -> None:
        plan = self.run_plan(fixture="pr_open_parent")

        self.assertEqual([action for action in plan["actions"] if action["kind"] == "existing-issue"], [])
        self.assertEqual(plan["concurrency"]["expected_from_active_tasks"], 0)
        self.assertEqual(plan["concurrency"]["expected_breakdown"], [])

    def test_github_action_queries_only_open_auto_loop_items(self) -> None:
        source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py").read_text(encoding="utf-8")

        self.assertIn('"--state", "open"', source)
        self.assertNotIn('"--state", "closed"', source)
        self.assertNotIn('"--state", "merged"', source)

    def test_audit_fallback_when_latest_audit_is_not_none_zero(self) -> None:
        plan = self.run_plan()

        self.assertEqual(plan["actions"], [])
        self.assertEqual(plan["recommendation"], "RECOMMEND:audit")

    def test_wakeup_plan_does_not_require_local_maintainer_directive_directory(self) -> None:
        directive_dir = self.repo / ".refactor-loop" / "runs" / "maintainer-directives"
        self.assertFalse(directive_dir.exists())

        plan = self.run_plan()

        self.assertEqual(
            plan["authorization"],
            "skills/codex-refactor-loop/authorizations/runtime-exceptions.md#maintainer-directive-wakeup-plan-script",
        )
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

    def test_all_wakeup_actions_emit_registered_phase_slugs(self) -> None:
        (self.repo / ".refactor-loop" / ".controller-pending-events.log").unlink()
        heartbeats = self.repo / ".refactor-loop" / "heartbeats"
        for path in heartbeats.glob("*.ts"):
            path.unlink()
        (self.repo / ".refactor-loop" / ".concurrency-alert.log").write_text(
            "[2026-05-29T00:00:00Z] P0 no-gap-violation: fixture\n",
            encoding="utf-8",
        )
        (self.repo / ".refactor-loop" / ".concurrency-alert.log").unlink()
        self.write_completed_log("implement-issue20.log", "IMPLEMENT_DONE")

        for fixture in ("ci_red", "milestone", "existing", "empty"):
            with self.subTest(fixture=fixture):
                plan = self.run_plan(fixture=fixture)
                for action in plan["actions"]:
                    assert_stage_slug(action["phase"])

        plan = self.run_plan(fixture="ci_red")
        by_kind = {action["kind"]: action for action in plan["actions"]}
        self.assertEqual(by_kind["completed-marker"]["phase"], "publish")
        self.assertEqual(by_kind["completed-marker"]["route"], "publish-or-review-gate")
        self.assertEqual(by_kind["bootstrap"]["phase"], "bootstrap")
        self.assertEqual(by_kind["bootstrap"]["route"], "daemon-health")
        self.assertEqual(by_kind["wake-source"]["phase"], "bootstrap")
        self.assertEqual(by_kind["wake-source"]["route"], "wake-source")

        (self.repo / ".refactor-loop" / ".concurrency-alert.log").write_text(
            "[2026-05-29T00:00:00Z] P0 no-gap-violation: fixture\n",
            encoding="utf-8",
        )
        plan = self.run_plan(fixture="ci_red")
        by_kind = {action["kind"]: action for action in plan["actions"]}
        self.assertEqual(by_kind["no-gap-violation"]["phase"], "work-intake")
        self.assertEqual(by_kind["no-gap-violation"]["route"], "no-gap-repair")

    def test_host_stages_are_status_projection_only_after_validation(self) -> None:
        (self.repo / "prompts").mkdir(exist_ok=True)
        (self.repo / "prompts" / "host.md").write_text("host prompt\n", encoding="utf-8")
        (self.repo / "workflow.json").write_text(
            json.dumps(
                {
                    "stages": [
                        {
                            "slug": "host:qa",
                            "title": "QA",
                            "contract": "Host status projection only.",
                            "detail_anchor": "host-qa",
                        }
                    ],
                    "events": [{"name": "host:template-ready", "stage": "host:qa", "status": "host:queued"}],
                }
            ),
            encoding="utf-8",
        )
        (self.repo / ".refactor-loop" / "host.env").write_text(
            f"REPO_ROOT={self.repo}\nGH_REPO_SLUG=owner/repo\nCODEX_FLOOR=5\nHOST_WORKFLOW_SPEC=workflow.json\n",
            encoding="utf-8",
        )

        plan = self.run_plan()

        host_actions = [action for action in plan["actions"] if action["kind"] == "host-workflow-event"]
        self.assertEqual(len(host_actions), 1)
        self.assertEqual(host_actions[0]["phase"], "host:qa")
        self.assertEqual(host_actions[0]["route"], "host-workflow-status-projection")
        self.assertTrue(host_actions[0]["no_lifecycle_authority"])

    def test_invalid_host_workflow_spec_is_noop_error_reason(self) -> None:
        (self.repo / "workflow.json").write_text(json.dumps({"events": [{"name": "host:x", "stage": "missing"}]}), encoding="utf-8")
        (self.repo / ".refactor-loop" / "host.env").write_text(
            f"REPO_ROOT={self.repo}\nGH_REPO_SLUG=owner/repo\nCODEX_FLOOR=5\nHOST_WORKFLOW_SPEC=workflow.json\n",
            encoding="utf-8",
        )

        plan = self.run_plan()

        errors = [action for action in plan["actions"] if action["kind"] == "host-workflow-spec-invalid"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["phase"], "bootstrap")
        self.assertEqual(errors[0]["route"], "host-workflow-spec")
        self.assertTrue(errors[0]["no_lifecycle_authority"])


if __name__ == "__main__":
    unittest.main()
