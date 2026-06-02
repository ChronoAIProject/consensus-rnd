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
from codex_refactor_loop.restart import restart_managed_daemon_names  # noqa: E402
from codex_refactor_loop.workflow_stages import assert_stage_slug  # noqa: E402
from codex_refactor_loop.wakeup_plan import (  # noqa: E402
    GhItem,
    close_projection_actions,
    existing_issue_actions,
    has_dispatchable_action,
    marker_from_completed_log,
    release_countdown_actions,
    restore_hard_gate_for_dispatchable_actions,
    resolve_repo_root,
)


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
        state_dir = self.repo / ".refactor-loop" / "state"
        state_dir.mkdir()
        (state_dir / "auto-release-signals.json").write_text(json.dumps({"recent_pr_merges": 0}), encoding="utf-8")
        (self.repo / ".version-bump.json").write_text(
            json.dumps({"files": [{"path": "package.json", "field": "version"}]}),
            encoding="utf-8",
        )
        (self.repo / "package.json").write_text(json.dumps({"version": "1.2.3-beta.4"}), encoding="utf-8")
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
                api_path="$2"
                api_flag1="$3"
                api_flag2="$4"
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
                      if [[ "$label" == "auto-loop" ]]; then
                        printf '[{"number":20,"title":"milestone issue","labels":[{"name":"auto-loop"},{"name":"🎯 milestone"},{"name":"🔍 phase:design-solving"}]},{"number":10,"title":"ordinary issue","labels":[{"name":"auto-loop"},{"name":"🔧 phase:fixing"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    existing)
                      if [[ "$label" == "auto-loop" ]]; then
                        printf '[{"number":10,"title":"ordinary issue","labels":[{"name":"auto-loop"},{"name":"🔧 phase:fixing"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    transition_sort)
                      if [[ "$label" == "auto-loop" ]]; then
                        printf '[{"number":60,"title":"unknown issue","labels":[{"name":"auto-loop"},{"name":"🔧 phase:fixing"}]},{"number":61,"title":"positive issue","labels":[{"name":"auto-loop"},{"name":"🔧 phase:fixing"}]},{"number":62,"title":"classifier issue","labels":[{"name":"auto-loop"},{"name":"🔧 phase:fixing"}]},{"number":63,"title":"confident classifier issue","labels":[{"name":"auto-loop"},{"name":"🔧 phase:fixing"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    many_active)
                      if [[ "$label" == "auto-loop" ]]; then
                        printf '['
                        for i in 1 2 3 4 5 6; do
                          [[ "$i" != "1" ]] && printf ','
                          printf '{"number":%s,"title":"active issue %s","labels":[{"name":"auto-loop"},{"name":"🔧 phase:fixing"}]}' "$i" "$i"
                        done
                        printf ']\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    represented_parent)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":239,"title":"represented parent","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:implementing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    pr_open_parent)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":239,"title":"parent issue with open PR","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:pr-open"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    non_action_statuses)
                      if [[ "$label" == "auto-loop" ]]; then
                        printf '[{"number":40,"title":"blocked issue","labels":[{"name":"auto-loop"},{"name":"⏸️ phase:blocked"}]},{"number":41,"title":"merged issue","labels":[{"name":"auto-loop"},{"name":"🎉 phase:merged"}]},{"number":44,"title":"parent issue with child PR","labels":[{"name":"auto-loop"},{"name":"crnd:phase:pr-open"}]}]\n'
                      else
                        printf '[]\n'
                      fi
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
                      if [[ "$label" == "auto-loop" ]]; then
                        printf '[{"number":77,"title":"worker output PR","headRefName":"refactor/iter77-worker","labels":[{"name":"auto-loop"},{"name":"👀 phase:reviewing"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    unpushed_head_dash)
                      if [[ "$label" == "auto-loop" ]]; then
                        printf '[{"number":78,"title":"unsafe dash head","headRefName":"-bad","labels":[{"name":"auto-loop"},{"name":"👀 phase:reviewing"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    unpushed_head_space)
                      if [[ "$label" == "auto-loop" ]]; then
                        printf '[{"number":79,"title":"unsafe space head","headRefName":"bad ref","labels":[{"name":"auto-loop"},{"name":"👀 phase:reviewing"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    unpushed_head_control)
                      if [[ "$label" == "auto-loop" ]]; then
                        printf '[{"number":80,"title":"unsafe control head","headRefName":"bad\\u0001ref","labels":[{"name":"auto-loop"},{"name":"👀 phase:reviewing"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    ci_red)
                      if [[ "$label" == "auto-loop" ]]; then
                        printf '[{"number":31,"title":"red PR","labels":[{"name":"auto-loop"},{"name":"⚙️ phase:ci-running"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    represented_parent)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":255,"title":"child PR","headRefName":"impl/issue239","body":"Closes #239","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    pr_open_parent)
                      printf '[]\n'
                      ;;
                    milestone)
                      if [[ "$label" == "auto-loop" ]]; then
                        printf '[{"number":30,"title":"milestone PR","labels":[{"name":"auto-loop"},{"name":"🎯 milestone"},{"name":"👀 phase:reviewing"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    non_action_statuses)
                      if [[ "$label" == "auto-loop" ]]; then
                        printf '[{"number":42,"title":"non-red CI PR","labels":[{"name":"auto-loop"},{"name":"⚙️ phase:ci-running"}]},{"number":43,"title":"merged PR","labels":[{"name":"auto-loop"},{"name":"🎉 phase:merged"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    *)
                      printf '[]\n'
                      ;;
                  esac
                  exit 0
                fi
                if [[ "$cmd1" == "api" ]]; then
                  if [[ -n "${WAKEUP_PLAN_GH_QUERY_LOG:-}" && "$api_path" == repos/owner/repo/milestones* ]]; then
                    printf 'api milestones\n' >> "$WAKEUP_PLAN_GH_QUERY_LOG"
                  fi
                  if [[ "$api_path" == repos/owner/repo/milestones* ]]; then
                    case "$fixture" in
                      default_milestones)
                        printf '[{"number":3,"title":"No due","due_on":null},{"number":2,"title":"Later","due_on":"2026-07-01T00:00:00Z"},{"number":1,"title":"Soon","due_on":"2026-06-15T00:00:00Z"}]\n'
                        ;;
                      default_milestone_tie)
                        printf '[{"number":8,"title":"No due high","due_on":null},{"number":4,"title":"No due low","due_on":null}]\n'
                        ;;
                      *)
                        printf '[]\n'
                        ;;
                    esac
                    exit 0
                  fi
                  if [[ "$api_flag1" == "--paginate" && "$api_flag2" == "--slurp" ]]; then
                    if [[ "$fixture" == "ci_red" && "$api_path" == "repos/owner/repo/commits/ci-red-sha/check-runs" ]]; then
                      printf '[{"check_runs":[{"name":"unit","status":"completed","conclusion":"failure","html_url":"https://checks/unit"},{"name":"lint","status":"completed","conclusion":"success","html_url":"https://checks/lint"}]}]\n'
                    else
                      printf '[{"check_runs":[]}]\n'
                    fi
                    exit 0
                  fi
                  if [[ "$api_path" == "repos/owner/repo/pulls/31" ]]; then
                    printf '{"head":{"sha":"ci-red-sha"}}\n'
                    exit 0
                  fi
                  printf '{"head":{"sha":"empty-sha"}}\n'
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
                if [[ -n "${WAKEUP_PLAN_PS_EXTRA:-}" ]]; then
                  printf '%s\n' "$WAKEUP_PLAN_PS_EXTRA"
                fi
                if [[ "${WAKEUP_PLAN_ACTIVE_AUDIT:-0}" == "1" ]]; then
                  audit_iter="${WAKEUP_PLAN_AUDIT_ITER:-8}"
                  printf 'python3 /skill/consensus-rnd-cli spawn-codex --cd %s --prompt %s/.refactor-loop/prompts/audit-iter-%s.md --log %s/.refactor-loop/logs/audit-iter-%s.log\n' "$repo" "$repo" "$audit_iter" "$repo" "$audit_iter"
                fi
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
        for name in restart_managed_daemon_names():
            (heartbeats / f"{name}.ts").write_text(now, encoding="utf-8")

    def run_plan(self, *, fixture: str = "empty", ps_count: int = 5, active_audit: bool = False) -> dict:
        return self.run_plan_with_stdout(fixture=fixture, ps_count=ps_count, active_audit=active_audit)[0]

    def run_plan_with_stdout(
        self,
        *,
        fixture: str = "empty",
        ps_count: int = 5,
        active_audit: bool = False,
    ) -> tuple[dict, str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.fakebin}{os.pathsep}{env.get('PATH', '')}",
                "CODEX_FLOOR": "5",
                "GH_REPO_SLUG": "owner/repo",
                "WAKEUP_PLAN_GH_FIXTURE": fixture,
                "WAKEUP_PLAN_PS_COUNT": str(ps_count),
                "WAKEUP_PLAN_ACTIVE_AUDIT": "1" if active_audit else "0",
                "WAKEUP_PLAN_REPO_ROOT": str(self.repo.resolve()),
                "WAKEUP_PLAN_GIT_LOG": str(self.repo / "git-commands.log"),
                "WAKEUP_PLAN_GH_QUERY_LOG": str(self.repo / "gh-query-labels.log"),
            }
        )
        if "WAKEUP_PLAN_PS_EXTRA" in os.environ:
            env["WAKEUP_PLAN_PS_EXTRA"] = os.environ["WAKEUP_PLAN_PS_EXTRA"]
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

    def run_plan_with_env(
        self,
        env_updates: dict[str, str],
        *,
        fixture: str = "empty",
        ps_count: int = 5,
        active_audit: bool = False,
    ) -> tuple[dict, str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.fakebin}{os.pathsep}{env.get('PATH', '')}",
                "CODEX_FLOOR": "5",
                "GH_REPO_SLUG": "owner/repo",
                "WAKEUP_PLAN_GH_FIXTURE": fixture,
                "WAKEUP_PLAN_PS_COUNT": str(ps_count),
                "WAKEUP_PLAN_ACTIVE_AUDIT": "1" if active_audit else "0",
                "WAKEUP_PLAN_REPO_ROOT": str(self.repo.resolve()),
                "WAKEUP_PLAN_GIT_LOG": str(self.repo / "git-commands.log"),
                "WAKEUP_PLAN_GH_QUERY_LOG": str(self.repo / "gh-query-labels.log"),
            }
        )
        env.update(env_updates)
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

    def write_dispatch(self, priority: str, task_id: str) -> Path:
        priority_dir = self.repo / ".refactor-loop" / "dispatch-queue" / priority
        priority_dir.mkdir(parents=True, exist_ok=True)
        dispatch = priority_dir / f"{task_id}.dispatch.json"
        dispatch.write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "cd": str(self.repo / ".worktrees" / task_id),
                    "prompt": str(self.repo / ".refactor-loop" / "prompts" / f"{task_id}.md"),
                    "log": str(self.logs / f"{task_id}.log"),
                    "stall": 5400,
                    "queued_at": "2026-05-26T07:25:00Z",
                    "reason": f"{task_id} needed",
                }
            ),
            encoding="utf-8",
        )
        return dispatch

    def write_completed_log(self, name: str, marker: str) -> None:
        (self.logs / name).write_text(
            f"prompt echo {marker}:<placeholder>\n"
            "body\n"
            f"{marker}:real\n"
            "EXIT=0\n",
            encoding="utf-8",
        )

    def write_consensus_artifact(self, issue: int = 20, round_no: int = 5) -> Path:
        runs = self.repo / ".refactor-loop" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        artifact = runs / f"phase9-issue{issue}-r{round_no}-judge.md"
        artifact.write_text(
            textwrap.dedent(
                f"""\
                ---
                issue: {issue}
                verdict: consensus
                ---

                ## PROJECT_RULES clause violated
                Durable source must be checked before implementation dispatch.

                ## Concrete plan
                - `skills/codex-refactor-loop/scripts/codex_refactor_loop/wakeup_plan.py`: project durable consensus artifact fields.
                - `skills/codex-refactor-loop/scripts/codex_refactor_loop/wakeup_runner.py`: revalidate the consensus artifact before dispatch.

                META_JUDGE_DONE:consensus:structural
                """
            ),
            encoding="utf-8",
        )
        return artifact

    def append_harness_spawn_intent(self, **overrides: object) -> dict[str, object]:
        intent: dict[str, object] = {
            "intent_id": "phase9-router:330:4:judge",
            "source": "phase9-router",
            "route": "solver_triplet_to_judge",
            "task_id": "phase9-issue330-r4-judge",
            "priority": "p1",
            "command": "spawn-codex",
            "controller_action": "spawn_codex_harness_background",
            "cd": ".",
            "prompt": ".refactor-loop/prompts/phase9/phase9-issue330-r4-judge.md",
            "log": ".refactor-loop/logs/phase9-issue330-r4-judge.log",
            "stall": 3600,
            "reason": "test intent",
            "queued_at": "2026-05-31T00:00:00Z",
            "run_in_background_required": True,
            "no_lifecycle_authority": True,
        }
        intent.update(overrides)
        pending = self.repo / ".refactor-loop" / ".controller-pending-events.log"
        with pending.open("a", encoding="utf-8") as handle:
            handle.write(f"2026-05-31T00:00:00Z HARNESS_SPAWN_INTENT {json.dumps(intent, sort_keys=True)}\n")
        return intent

    def append_raw_harness_spawn_intent(self, payload: str) -> None:
        pending = self.repo / ".refactor-loop" / ".controller-pending-events.log"
        with pending.open("a", encoding="utf-8") as handle:
            handle.write(f"2026-05-31T00:00:00Z HARNESS_SPAWN_INTENT {payload}\n")

    def assert_harness_spawn_intent_invalid(self, expected_reason: str, **overrides: object) -> None:
        (self.repo / ".refactor-loop" / ".controller-pending-events.log").write_text("", encoding="utf-8")
        self.append_harness_spawn_intent(**overrides)

        plan = self.run_plan()

        action = plan["actions"][0]
        self.assertEqual(action["kind"], "harness-spawn-intent-invalid")
        self.assertEqual(action["reason"], expected_reason)

    def test_harness_spawn_intent_accepts_only_spawn_codex_string_command(self) -> None:
        self.append_harness_spawn_intent()

        plan = self.run_plan()

        action = plan["actions"][0]
        self.assertEqual(action["kind"], "harness-spawn-intent")
        self.assertEqual(action["command"], "spawn-codex")
        self.assertEqual(action["controller_action"], "spawn_codex_harness_background")
        self.assertEqual(action["cd"], str(self.repo.resolve()))
        self.assertEqual(action["prompt"], str((self.repo / ".refactor-loop/prompts/phase9/phase9-issue330-r4-judge.md").resolve()))
        self.assertEqual(action["log"], str((self.repo / ".refactor-loop/logs/phase9-issue330-r4-judge.log").resolve()))
        self.assertTrue(action["run_in_background_required"])
        self.assertTrue(action["no_lifecycle_authority"])
        self.assertNotIn("argv", action)
        self.assertNotIn("shell", action)

    def test_harness_spawn_intent_rejects_argv_command_array(self) -> None:
        self.append_harness_spawn_intent(command=["consensus-rnd-cli", "spawn-codex"])

        plan = self.run_plan()

        action = plan["actions"][0]
        self.assertEqual(action["kind"], "harness-spawn-intent-invalid")
        self.assertEqual(action["reason"], "command-not-spawn-codex")

    def test_harness_spawn_intent_rejects_generic_command_fields(self) -> None:
        forbidden_fields = ("argv", "args", "shell", "cmd", "commands", "env", "git", "gh", "executor", "target_ref")
        for field in forbidden_fields:
            with self.subTest(field=field):
                (self.repo / ".refactor-loop" / ".controller-pending-events.log").write_text("", encoding="utf-8")
                self.append_harness_spawn_intent(intent_id=f"bad-{field}", **{field: "forbidden"})

                plan = self.run_plan()

                action = plan["actions"][0]
                self.assertEqual(action["kind"], "harness-spawn-intent-invalid")
                self.assertEqual(action["reason"], f"forbidden-fields:{field}")

    def test_harness_spawn_intent_rejects_malformed_json_non_object_and_missing_id(self) -> None:
        cases = (
            ("{not-json", "invalid-json"),
            ("[]", "intent-not-object"),
            (json.dumps({"command": "spawn-codex"}), "missing-intent-id"),
        )
        for payload, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                (self.repo / ".refactor-loop" / ".controller-pending-events.log").write_text("", encoding="utf-8")
                self.append_raw_harness_spawn_intent(payload)

                plan = self.run_plan()

                action = plan["actions"][0]
                self.assertEqual(action["kind"], "harness-spawn-intent-invalid")
                self.assertEqual(action["reason"], expected_reason)

    def test_harness_spawn_intent_rejects_missing_path_fields_and_bad_path(self) -> None:
        for field in ("cd", "prompt", "log"):
            with self.subTest(field=field):
                self.assert_harness_spawn_intent_invalid(f"missing-{field}", **{field: ""})

        self.assert_harness_spawn_intent_invalid(
            "invalid-path:artifact path must be repo-relative POSIX text: '../outside'",
            cd="../outside",
        )

    def test_harness_spawn_intent_rejects_invalid_stall_and_required_flags(self) -> None:
        cases = (
            ("controller-action-not-spawn-codex-background", {"controller_action": "spawn_codex_now"}),
            ("invalid-stall", {"stall": "not-an-int"}),
            ("invalid-stall", {"stall": 0}),
            ("missing-background-requirement", {"run_in_background_required": False}),
            ("missing-no-lifecycle-authority", {"no_lifecycle_authority": False}),
        )
        for expected_reason, overrides in cases:
            with self.subTest(expected_reason=expected_reason):
                self.assert_harness_spawn_intent_invalid(expected_reason, **overrides)

    def test_harness_spawn_intent_dedupes_and_filters_existing_or_in_flight_log(self) -> None:
        self.append_harness_spawn_intent(intent_id="duplicate")
        self.append_harness_spawn_intent(intent_id="duplicate")
        self.append_harness_spawn_intent(
            intent_id="existing-log",
            task_id="existing-log",
            log=".refactor-loop/logs/existing-log.log",
        )
        (self.logs / "existing-log.log").write_text("already exists\n", encoding="utf-8")
        self.append_harness_spawn_intent(
            intent_id="in-flight",
            task_id="in-flight",
            log=".refactor-loop/logs/in-flight.log",
        )
        os.environ["WAKEUP_PLAN_PS_EXTRA"] = (
            f"python3 /skill/consensus-rnd-cli spawn-codex --cd {self.repo.resolve()} "
            f"--prompt {self.repo.resolve()}/.refactor-loop/prompts/in-flight.md "
            f"--log {self.repo.resolve()}/.refactor-loop/logs/in-flight.log"
        )
        try:
            plan = self.run_plan()
        finally:
            os.environ.pop("WAKEUP_PLAN_PS_EXTRA", None)

        actions = [action for action in plan["actions"] if action["kind"] == "harness-spawn-intent"]
        self.assertEqual([action["intent_id"] for action in actions], ["duplicate"])

    def test_wakeup_plan_uses_concurrency_monitor_for_spawn_intent_in_flight_detection(self) -> None:
        wakeup_source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py").read_text(encoding="utf-8")

        self.assertIn("monitor.list_in_flight_codex_lines()", wakeup_source)
        self.assertNotIn('["ps", "-eo", "command="]', wakeup_source)
        self.assertNotIn("def _spawn_codex_in_flight_for_log", wakeup_source)

    def test_completed_marker_routes_before_ci_red(self) -> None:
        self.write_completed_log("implement-issue20.log", "IMPLEMENT_DONE")

        plan = self.run_plan(fixture="ci_red")

        self.assertEqual(plan["actions"][0]["kind"], "completed-marker")
        self.assertEqual(plan["actions"][0]["actor"], "controller")
        self.assertIn("IMPLEMENT_DONE:real", plan["actions"][0]["marker"])

    def test_completed_marker_requires_standalone_final_marker_line(self) -> None:
        valid = self.logs / "implement-issue20.log"
        valid.write_text(
            "prompt echo IMPLEMENT_DONE:<status>\n"
            "> IMPLEMENT_DONE:quoted\n"
            "controller saw IMPLEMENT_DONE:embedded prose\n"
            "IMPLEMENT_DONE:ok\n"
            "EXIT=0\n",
            encoding="utf-8",
        )
        self.assertEqual("IMPLEMENT_DONE:ok", marker_from_completed_log(valid))

        invalid = self.logs / "implement-issue21.log"
        invalid.write_text(
            "prompt echo IMPLEMENT_DONE:<status>\n"
            "> IMPLEMENT_DONE:quoted\n"
            "controller saw IMPLEMENT_DONE:embedded prose\n"
            "grep output: IMPLEMENT_DONE:grep\n"
            "EXIT=0\n",
            encoding="utf-8",
        )
        self.assertIsNone(marker_from_completed_log(invalid))

        stale_marker = self.logs / "implement-issue22.log"
        stale_marker.write_text(
            "IMPLEMENT_DONE:ok\n"
            "later raw worker prose\n"
            "EXIT=0\n",
            encoding="utf-8",
        )
        self.assertIsNone(marker_from_completed_log(stale_marker))

        valid.unlink()
        invalid.unlink()
        actions = self.run_plan()["actions"]
        self.assertFalse([action for action in actions if action["kind"] == "completed-marker"])

    def test_completed_marker_payload_does_not_include_raw_log_tail(self) -> None:
        (self.logs / "implement-worker.log").write_text(
            "target issue #999 in raw prose only\n"
            "raw reviewer prose that must not be relayed\n"
            "IMPLEMENT_DONE:ok\n"
            "EXIT=0\n",
            encoding="utf-8",
        )

        plan = self.run_plan()

        action = plan["actions"][0]
        rendered = json.dumps(action, sort_keys=True)
        self.assertEqual(action["kind"], "completed-marker")
        self.assertIsNone(action["item"])
        self.assertNotIn("999", rendered)
        self.assertNotIn("raw reviewer prose", rendered)
        self.assertNotIn("target issue", rendered)

    def test_decompose_consensus_visible_only_as_generic_completed_marker(self) -> None:
        self.write_completed_log("phase9-issue403-r6-judge.log", "META_JUDGE_DONE:consensus:decompose")

        plan = self.run_plan()

        action = plan["actions"][0]
        self.assertEqual(action["kind"], "completed-marker")
        self.assertEqual(action["phase"], "design-consensus")
        self.assertEqual(action["actor"], "design-consensus-router-or-controller")
        self.assertEqual(action["marker"], "META_JUDGE_DONE:consensus:decompose:real")
        self.assertNotEqual(action.get("controller_action"), "apply_issue_decomposition_plan")
        self.assertNotIn("IssueDecompositionPlan", json.dumps(action))
        self.assertNotIn("issue-decomposition", json.dumps(action))
        self.assertNotIn("decomposition-plan", json.dumps(action))

    def test_issue_decomposition_does_not_extend_wakeup_plan_public_projection(self) -> None:
        source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py").read_text(encoding="utf-8")
        plan = self.run_plan()
        rendered = json.dumps(plan, sort_keys=True)

        self.assertEqual(plan["mode"], "closed-action-projection")
        self.assertTrue(plan["no_lifecycle_authority"])
        for token in (
            "IssueDecompositionPlan",
            "issue-decomposition",
            "decomposition-plan",
            "apply_issue_decomposition_plan",
            "gh issue create",
            "gh issue edit",
            "gh issue close",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, source)
                self.assertNotIn(token, rendered)
        self.assertNotIn("suggested_command", rendered)

    def test_review_done_completed_marker_is_closed_projection_not_standalone_policy(self) -> None:
        head_sha = "a" * 40
        (self.logs / "review-pr123-architect-r1.log").write_text(
            f"head_sha: {head_sha}\n"
            "body\n"
            "REVIEW_DONE:123:architect:approve\n"
            "EXIT=0\n",
            encoding="utf-8",
        )

        plan = self.run_plan()

        action = plan["actions"][0]
        self.assertEqual(action["kind"], "completed-marker")
        self.assertEqual(action["phase"], "review-gate")
        self.assertEqual(action["actor"], "controller-or-fix-codex")
        self.assertEqual(action["item"], "PR #123")
        self.assertEqual(action["controller_action"], "review_gate")
        self.assertEqual(action["head_sha"], head_sha)
        self.assertEqual(action["runner_authority"], "wakeup-runner-396")
        self.assertTrue(action["no_generic_command"])
        self.assertNotIn("consensus", action)
        self.assertNotIn("merge_command", action)
        self.assertNotIn("review-gate-readiness", action)
        self.assertNotIn("review_gate_actions", action)

    def test_meta_resolved_drop_completed_marker_projects_close_helper(self) -> None:
        (self.logs / "issue53-judge-drop.log").write_text(
            "raw prose is diagnostic only\n"
            "META_RESOLVED:drop:no-action\n"
            "EXIT=0\n",
            encoding="utf-8",
        )

        plan = self.run_plan()

        action = plan["actions"][0]
        self.assertEqual(action["kind"], "completed-marker")
        self.assertEqual(action["controller_action"], "close_managed_item_from_drop_marker")
        self.assertEqual(action["runner_authority"], "wakeup-runner-396")
        self.assertTrue(action["no_generic_command"])
        self.assertIn("clean_exit_source_marker", action["preconditions"])
        self.assertEqual(action["source_artifact"], ".refactor-loop/logs/issue53-judge-drop.log")
        self.assertEqual(action["source_marker"], "META_RESOLVED:drop:no-action")
        self.assertEqual(action["target_kind"], "issue")
        self.assertEqual(action["target_number"], 53)
        self.assertEqual(action["target"], {"kind": "issue", "number": 53})
        self.assertNotIn("status_only", action)

    def test_non_drop_meta_resolved_still_routes_design_consensus(self) -> None:
        self.write_completed_log("judge-issue54.log", "META_RESOLVED:continue")

        plan = self.run_plan()

        action = plan["actions"][0]
        self.assertEqual(action["controller_action"], "dispatch_design_consensus")
        self.assertTrue(action["status_only"])
        self.assertTrue(action["no_lifecycle_authority"])

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

    def test_resolve_repo_root_uses_loop_context_without_private_cwd_default(self) -> None:
        old_env = os.environ.copy()
        try:
            os.environ.clear()
            with self.assertRaisesRegex(Exception, "REPO_ROOT is unset"):
                resolve_repo_root(None)
            self.assertEqual(self.repo.resolve(), resolve_repo_root(str(self.repo)))
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_wakeup_plan_source_has_no_private_host_env_parser(self) -> None:
        wakeup_source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py").read_text(encoding="utf-8")
        self.assertNotIn("def read_host_env", wakeup_source)
        self.assertNotIn("Path.cwd().resolve()", wakeup_source)
        self.assertIn("LoopContext.load(repo_root=arg_root", wakeup_source)

    def test_wakeup_plan_bootstrap_uses_explicit_host_env_locator_not_legacy_path(self) -> None:
        (self.repo / ".refactor-loop" / "host.env").unlink()
        host_env = self.repo / ".config" / "consensus-rnd" / "host.env"
        host_env.parent.mkdir(parents=True)
        host_env.write_text(
            f"REPO_ROOT={self.repo}\nGH_REPO_SLUG=owner/repo\nCODEX_FLOOR=5\n",
            encoding="utf-8",
        )

        plan, _stdout = self.run_plan_with_env({"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})

        self.assertNotIn(
            "bootstrap",
            [action["kind"] for action in plan["actions"] if action.get("reason", "").startswith("missing")],
        )
        self.assertNotIn("bootstrap-missing", json.dumps(plan))
        self.assertNotIn(".refactor-loop/host.env", json.dumps(plan))

    def test_wakeup_plan_source_has_no_legacy_host_env_bootstrap_authority(self) -> None:
        source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py").read_text(encoding="utf-8")

        self.assertNotIn("missing .refactor-loop/host.env", source)
        self.assertNotIn('repo_root / ".refactor-loop" / "host.env"', source)
        self.assertIn("ctx.host_env", source)

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

    def test_unimplemented_dispatch_projections_are_status_only(self) -> None:
        self.write_completed_log("fix-pr77-r3.log", "FIX_DONE")

        plan = self.run_plan(fixture="ci_red")
        existing_plan = self.run_plan(fixture="existing")

        by_kind = {action["kind"]: action for action in plan["actions"]}
        by_kind["existing-issue"] = [action for action in existing_plan["actions"] if action["kind"] == "existing-issue"][0]
        for kind in ("ci-red", "existing-issue"):
            with self.subTest(kind=kind):
                self.assertTrue(by_kind[kind]["status_only"])
                self.assertTrue(by_kind[kind]["no_lifecycle_authority"])
                self.assertNotIn("runner_authority", by_kind[kind])
                self.assertNotIn("no_generic_command", by_kind[kind])
        self.assertTrue(str(by_kind["ci-red"]["controller_action"]).startswith("dispatch_"))
        self.assertNotIn("controller_action", by_kind["existing-issue"])

    def test_fix_done_completed_marker_projects_executable_dispatch_reviewers(self) -> None:
        self.write_completed_log("fix-pr77-r3.log", "FIX_DONE")

        plan = self.run_plan()

        action = next(item for item in plan["actions"] if item["action_id"].startswith("completed-marker:fix-pr77-r3"))
        self.assertEqual(action["kind"], "completed-marker")
        self.assertEqual(action["controller_action"], "dispatch_reviewers")
        self.assertEqual(action["runner_authority"], "wakeup-runner-396")
        self.assertTrue(action["no_generic_command"])
        self.assertNotIn("status_only", action)
        self.assertEqual(action["target_kind"], "PR")
        self.assertEqual(action["target_number"], 77)
        self.assertEqual(action["target"], {"kind": "PR", "number": 77})
        self.assertIn("clean_exit_source_marker", action["preconditions"])
        for forbidden in ("argv", "shell", "cmd", "command_line", "commands", "env", "git", "gh", "executor"):
            self.assertNotIn(forbidden, action)

    def test_runner_named_helper_projection_remains_executable(self) -> None:
        plan = self.run_plan(fixture="unpushed")

        action = plan["actions"][0]
        self.assertEqual(action["kind"], "unpushed-worker-output")
        self.assertEqual(action["controller_action"], "safe_push")
        self.assertNotIn("status_only", action)
        self.assertEqual(action["runner_authority"], "wakeup-runner-396")
        self.assertTrue(action["no_generic_command"])

    def test_named_g1_g3_helpers_remain_executable_without_generic_command_fields(self) -> None:
        artifact = self.write_consensus_artifact()
        self.write_completed_log("phase9-issue20-r5-judge.log", "META_JUDGE_DONE:consensus:structural")
        self.write_completed_log("implement-issue20.log", "IMPLEMENT_DONE:ok")
        (self.repo / ".refactor-loop/runs").mkdir(parents=True, exist_ok=True)
        (self.repo / ".refactor-loop/runs/release-rollup-pr-body.md").write_text(
            "## rollup\n\nbody\n\n⟦AI:AUTO-LOOP⟧\n",
            encoding="utf-8",
        )
        event = {
            "integration_branch": "integration",
            "review_base_branch": "review",
            "integration_sha": "abc123",
            "review_base_sha": "def456",
            "ahead_count": 1,
            "reason": "integration-ahead-review-base-without-open-rollup-pr",
        }
        with (self.repo / ".refactor-loop/.controller-pending-events.log").open("a", encoding="utf-8") as handle:
            handle.write("DEV_SYNC_PENDING:release-rollup-needed:" + json.dumps(event, sort_keys=True) + "\n")

        plan = self.run_plan(fixture="milestone")

        executable = {
            action["controller_action"]: action
            for action in plan["actions"]
            if action.get("runner_authority") == "wakeup-runner-396"
        }
        for helper in (
            "dispatch_consensus_implementation",
            "publish_implementation_output",
            "open_release_rollup_pr_from_action",
        ):
            with self.subTest(helper=helper):
                self.assertIn(helper, executable)
                self.assertNotIn("status_only", executable[helper])
                self.assertTrue(executable[helper]["no_generic_command"])
                for forbidden in ("argv", "shell", "cmd", "commands", "env", "git", "gh", "executor"):
                    self.assertNotIn(forbidden, executable[helper])
        consensus_action = executable["dispatch_consensus_implementation"]
        self.assertEqual(consensus_action["consensus_artifact"], artifact.relative_to(self.repo).as_posix())
        self.assertEqual(consensus_action["design_decision_path"], artifact.relative_to(self.repo).as_posix())
        self.assertEqual(consensus_action["consensus_issue"], 20)
        self.assertEqual(consensus_action["consensus_round"], 5)
        self.assertIn("wakeup_plan.py", consensus_action["scope_paths"])
        self.assertIn("wakeup_runner.py", consensus_action["scope_paths"])
        self.assertIn("durable_consensus_artifact", consensus_action["preconditions"])

    def test_consensus_completed_marker_without_durable_artifact_is_not_executable(self) -> None:
        self.write_completed_log("phase9-issue20-r5-judge.log", "META_JUDGE_DONE:consensus:structural")

        plan = self.run_plan()

        action = next(item for item in plan["actions"] if item["action_id"].startswith("completed-marker:phase9-issue20"))
        self.assertTrue(action["status_only"])
        self.assertNotIn("runner_authority", action)
        self.assertNotIn("consensus_artifact", action)

    def test_milestone_implementation_issue_projects_latest_durable_consensus_artifact(self) -> None:
        artifact = self.write_consensus_artifact()

        actions = existing_issue_actions(
            [
                GhItem(
                    kind="issue",
                    number=20,
                    title="implementation issue",
                    labels=(label_catalog.MANAGED, label_catalog.PHASE_IMPLEMENTING, label_catalog.HUMAN_AUTO, label_catalog.MILESTONE_CURRENT),
                )
            ],
            repo_root=self.repo,
        )

        action = next(item for item in actions if item["kind"] == "consensus-implementation-ready")
        self.assertEqual(action["controller_action"], "dispatch_consensus_implementation")
        self.assertEqual(action["consensus_artifact"], artifact.relative_to(self.repo).as_posix())
        self.assertEqual(action["target_kind"], "issue")
        self.assertEqual(action["target_number"], 20)
        self.assertIn("durable_consensus_artifact", action["preconditions"])
        projected = [item for item in [action] if not item.get("status_only")]
        self.assertEqual(1, len(projected))

    def test_wakeup_plan_source_locks_named_g1_g3_helper_allowlist(self) -> None:
        source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py").read_text(encoding="utf-8")
        for helper in (
            "dispatch_consensus_implementation",
            "publish_implementation_output",
            "dispatch_reviewers",
            "open_release_rollup_pr_from_action",
        ):
            with self.subTest(helper=helper):
                self.assertIn(helper, source)
        self.assertNotIn("HeadlessLifecycleAction", source)
        self.assertNotIn("headless_actions", source)

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

    # Refactor (issue-275): Old pattern: remote CI routing depended on inline shell poller marker text. New principle: behavior tests assert wakeup-plan emits structured ci-red actions without marker coupling.
    def test_ci_red_check_projection_requests_triage_fields_without_remote_ci_done_marker(self) -> None:
        plan = self.run_plan(fixture="ci_red")

        self.assertEqual(plan["actions"][0]["kind"], "ci-red")
        self.assertEqual(plan["actions"][0]["actor"], "remote-ci-fix-codex")
        self.assertEqual(plan["actions"][0]["check_names"], ["unit"])
        self.assertEqual(plan["actions"][0]["head_sha"], "ci-red-sha")
        self.assertNotIn("REMOTE_CI_DONE", json.dumps(plan))

    def test_ci_red_uses_pr_checks_projection_without_legacy_pr_checks_command(self) -> None:
        plan = self.run_plan(fixture="ci_red")

        self.assertEqual(plan["actions"][0]["kind"], "ci-red")
        source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py").read_text(encoding="utf-8")
        self.assertIn("PrChecksProjection", source)
        self.assertNotIn('"pr", "checks"', source)

    def test_no_gap_routes_before_milestone(self) -> None:
        (self.repo / ".refactor-loop" / ".concurrency-alert.log").write_text(
            "[2026-05-29T00:00:00Z] P0 no-gap-violation: fixture\n",
            encoding="utf-8",
        )

        plan = self.run_plan(fixture="milestone")

        kinds = [action["kind"] for action in plan["actions"]]
        self.assertEqual(kinds[0], "no-gap-violation")
        self.assertLess(kinds.index("no-gap-violation"), kinds.index("existing-issue"))

    def test_no_gap_projection_is_status_only_until_runnable_spawn_contract_exists(self) -> None:
        (self.repo / ".refactor-loop" / ".concurrency-alert.log").write_text(
            "[2026-05-29T00:00:00Z] P0 no-gap-violation: fixture\n",
            encoding="utf-8",
        )

        plan = self.run_plan()

        action = [item for item in plan["actions"] if item["kind"] == "no-gap-violation"][0]
        self.assertTrue(action["status_only"])
        self.assertTrue(action["no_lifecycle_authority"])
        self.assertNotIn("controller_action", action)
        self.assertNotIn("runner_authority", action)
        self.assertNotIn("no_generic_command", action)
        self.assertNotIn("prompt", action)
        self.assertNotIn("log", action)
        self.assertFalse(has_dispatchable_action([action]))

        hard_gate = {
            "active": False,
            "reason": "single_active_audit_in_flight",
            "blocked_deficit": 4,
            "dispatch_required": 0,
        }
        concurrency = {"deficit": 4, "hard_gate": hard_gate}
        restore_hard_gate_for_dispatchable_actions(concurrency, [action])

        self.assertFalse(hard_gate["active"])
        self.assertEqual(hard_gate["reason"], "single_active_audit_in_flight")
        self.assertEqual(hard_gate["blocked_deficit"], 4)
        self.assertEqual(hard_gate["dispatch_required"], 0)

    def test_existing_design_issue_projection_is_status_only_until_router_intent_exists(self) -> None:
        action = existing_issue_actions(
            [
                GhItem(
                    kind="issue",
                    number=416,
                    title="design issue",
                    labels=(label_catalog.MANAGED, label_catalog.PHASE_DESIGN_SOLVING, label_catalog.HUMAN_AUTO),
                )
            ],
            repo_root=self.repo,
        )[0]

        self.assertEqual(action["kind"], "existing-issue")
        self.assertEqual(action["phase"], "design-consensus")
        self.assertEqual(action["route"], "design-consensus-status")
        self.assertTrue(action["status_only"])
        self.assertTrue(action["no_lifecycle_authority"])
        self.assertNotIn("controller_action", action)
        projected = close_projection_actions([action])[0]
        self.assertTrue(projected["status_only"])
        self.assertNotIn("runner_authority", projected)
        self.assertNotIn("no_generic_command", projected)
        self.assertFalse(has_dispatchable_action([action]))

        hard_gate = {
            "active": False,
            "reason": "single_active_audit_in_flight",
            "blocked_deficit": 4,
            "dispatch_required": 0,
        }
        concurrency = {"deficit": 4, "hard_gate": hard_gate}
        restore_hard_gate_for_dispatchable_actions(concurrency, [action])

        self.assertFalse(hard_gate["active"])
        self.assertEqual(hard_gate["reason"], "single_active_audit_in_flight")
        self.assertEqual(hard_gate["blocked_deficit"], 4)
        self.assertEqual(hard_gate["dispatch_required"], 0)

    def test_wakeup_plan_source_does_not_make_dispatch_next_step_worker_executable(self) -> None:
        source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py").read_text(encoding="utf-8")

        self.assertNotIn('"no-gap-violation",\n    "existing-issue"', source)
        self.assertNotIn('closed.setdefault("controller_action", "dispatch_next_step_worker")', source)

    def test_milestone_labeled_items_route_before_ordinary_existing_issue(self) -> None:
        plan = self.run_plan(fixture="milestone")

        actions = [action for action in plan["actions"] if action["kind"] == "existing-issue"]
        self.assertGreaterEqual(len(actions), 3)
        self.assertEqual(actions[0]["item"], "issue #20")
        self.assertEqual(actions[1]["item"], "PR #30")
        self.assertTrue(actions[0]["milestone"])
        self.assertFalse(actions[-1]["milestone"])

    def test_release_countdown_default_goal_ignores_current_milestone_as_explicit_target(self) -> None:
        old_env = os.environ.copy()
        os.environ.pop("GH_REPO_SLUG", None)
        os.environ.pop("GH_REPO", None)
        os.environ.pop("GH_OWNER", None)
        os.environ.pop("GH_REPO_NAME", None)
        calls: list[Path] = []

        def scorer(repo_root: Path) -> dict:
            calls.append(repo_root)
            return {
                "from_version": "1.2.3-beta.4",
                "to_version": "1.2.3-beta.4",
                "stability_score": 0,
                "ready": False,
                "signals": {},
                "blocked_reasons": ["no_commits_since_last_release"],
            }

        no_target = [
            GhItem("issue", 10, "ordinary", (label_catalog.MANAGED, label_catalog.PHASE_IMPLEMENTING, label_catalog.HUMAN_AUTO)),
            GhItem("issue", 20, "current", (label_catalog.MANAGED, label_catalog.PHASE_IMPLEMENTING, label_catalog.HUMAN_AUTO, label_catalog.MILESTONE_CURRENT)),
        ]

        try:
            actions = release_countdown_actions(self.repo, no_target, scorer=scorer)

            self.assertEqual(calls, [self.repo])
            self.assertEqual(len(actions), 1)
            self.assertEqual(actions[0]["activation"], "default-goal")
            self.assertEqual(actions[0]["targets"], [])
            self.assertIsNone(actions[0]["goal"]["milestone"])
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_release_countdown_projects_release_gate_score_without_dispatch_authority(self) -> None:
        calls: list[Path] = []

        def scorer(repo_root: Path) -> dict:
            calls.append(repo_root)
            return {
                "from_version": "1.2.3-beta.4",
                "to_version": "1.2.3-beta.5",
                "stability_score": 75,
                "ready": False,
                "signals": {
                    "required_checks_recent_green": {"passed": False, "reason": "pending_required_checks"},
                    "fresh_heartbeats": {"passed": True},
                },
                "blocked_reasons": ["required_checks_recent_green", "min_interval"],
            }

        items = [
            GhItem(
                "issue",
                344,
                "release target",
                (
                    label_catalog.MANAGED,
                    label_catalog.PHASE_IMPLEMENTING,
                    label_catalog.HUMAN_AUTO,
                    label_catalog.MILESTONE_CURRENT,
                    label_catalog.MILESTONE_RELEASE_TARGET,
                ),
            ),
            GhItem("PR", 345, "ordinary PR", (label_catalog.MANAGED, label_catalog.PHASE_REVIEWING, label_catalog.HUMAN_AUTO)),
        ]

        actions = release_countdown_actions(self.repo, items, scorer=scorer)

        self.assertEqual(calls, [self.repo])
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action["kind"], "release-countdown")
        self.assertEqual(action["phase"], "publish")
        self.assertEqual(action["actor"], "controller")
        self.assertEqual(action["route"], "release-countdown-status")
        self.assertEqual(action["activation"], "explicit-target")
        self.assertTrue(action["status_only"])
        self.assertTrue(action["no_lifecycle_authority"])
        self.assertEqual(action["source"], "release-gate")
        self.assertEqual(action["targets"], [{"kind": "issue", "number": 344, "item": "issue #344", "title": "release target"}])
        self.assertIsNone(action["goal"]["milestone"])
        self.assertEqual(
            action["goal"]["release"],
            {
                "from_version": "1.2.3-beta.4",
                "to_version": "1.2.3-beta.5",
                "countdown_to_version": "1.2.3-beta.5",
                "stability_score": 75,
                "ready": False,
                "passed_signals": 1,
                "total_signals": 2,
                "red_signals": ["required_checks_recent_green"],
                "blocked_reasons": ["required_checks_recent_green", "min_interval"],
                "source": "release-gate",
            },
        )
        self.assertEqual(action["from_version"], "1.2.3-beta.4")
        self.assertEqual(action["to_version"], "1.2.3-beta.5")
        self.assertEqual(action["stability_score"], 75)
        self.assertFalse(action["ready"])
        self.assertEqual(action["red_signals"], ["required_checks_recent_green"])
        self.assertEqual(action["blocked_reasons"], ["required_checks_recent_green", "min_interval"])
        self.assertFalse(has_dispatchable_action(actions))

    def test_release_countdown_explicit_release_target_beats_default_goal_and_skips_milestone_read(self) -> None:
        def scorer(_: Path) -> dict:
            return {
                "from_version": "1.2.3-beta.4",
                "to_version": "1.2.3-beta.5",
                "stability_score": 100,
                "ready": True,
                "signals": {"fresh_heartbeats": {"passed": True}},
                "blocked_reasons": [],
            }

        actions = release_countdown_actions(
            self.repo,
            [
                GhItem(
                    "issue",
                    344,
                    "release target",
                    (
                        label_catalog.MANAGED,
                        label_catalog.PHASE_IMPLEMENTING,
                        label_catalog.HUMAN_AUTO,
                        label_catalog.MILESTONE_RELEASE_TARGET,
                    ),
                )
            ],
            scorer=scorer,
        )

        self.assertEqual(actions[0]["activation"], "explicit-target")
        self.assertEqual(actions[0]["targets"][0]["number"], 344)
        self.assertIsNone(actions[0]["goal"]["milestone"])
        self.assertFalse((self.repo / "gh-query-labels.log").exists())

    def test_release_countdown_default_goal_uses_nearest_due_open_milestone(self) -> None:
        plan = self.run_plan(fixture="default_milestones")

        actions = [action for action in plan["actions"] if action["kind"] == "release-countdown"]
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action["activation"], "default-goal")
        self.assertEqual(action["targets"], [])
        self.assertEqual(action["goal"]["milestone"], {"number": 1, "title": "Soon", "due_on": "2026-06-15T00:00:00Z"})
        self.assertEqual(action["goal"]["release"]["countdown_to_version"], action["to_version"])
        self.assertEqual(action["goal"]["release"]["total_signals"], 8)
        self.assertIn("api milestones", (self.repo / "gh-query-labels.log").read_text(encoding="utf-8"))

    def test_release_countdown_fail_soft_when_version_manifest_has_no_files_list(self) -> None:
        (self.repo / ".version-bump.json").write_text(json.dumps({"version": "0.0.0"}), encoding="utf-8")

        plan = self.run_plan(fixture="default_milestones")

        actions = [action for action in plan["actions"] if action["kind"] == "release-countdown"]
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action["activation"], "default-goal")
        self.assertEqual(action["goal"]["milestone"], {"number": 1, "title": "Soon", "due_on": "2026-06-15T00:00:00Z"})
        self.assertIsNone(action["goal"]["release"])
        self.assertIsNone(action["from_version"])
        self.assertIsNone(action["to_version"])
        self.assertFalse(action["ready"])
        self.assertEqual(action["red_signals"], [])
        self.assertEqual(action["blocked_reasons"], [])
        self.assertIn("api milestones", (self.repo / "gh-query-labels.log").read_text(encoding="utf-8"))

    def test_release_countdown_fail_soft_when_mapped_manifest_versions_are_not_synchronized(self) -> None:
        (self.repo / ".version-bump.json").write_text(
            json.dumps(
                {
                    "files": [
                        {"path": "package.json", "field": "version"},
                        {"path": "other-package.json", "field": "version"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        (self.repo / "other-package.json").write_text(json.dumps({"version": "9.9.9"}), encoding="utf-8")

        plan = self.run_plan(fixture="existing")

        by_kind = {action["kind"]: action for action in plan["actions"]}
        self.assertEqual(by_kind["existing-issue"]["item"], "issue #10")
        self.assertIsNone(by_kind["release-countdown"]["goal"]["release"])
        self.assertFalse(has_dispatchable_action([by_kind["release-countdown"]]))

    def test_release_countdown_default_goal_falls_back_to_release_only_without_open_milestone(self) -> None:
        old_env = os.environ.copy()
        os.environ.pop("GH_REPO_SLUG", None)
        os.environ.pop("GH_REPO", None)
        os.environ.pop("GH_OWNER", None)
        os.environ.pop("GH_REPO_NAME", None)
        calls: list[Path] = []

        def scorer(repo_root: Path) -> dict:
            calls.append(repo_root)
            return {
                "from_version": "1.2.3-beta.4",
                "to_version": "1.2.3-beta.5",
                "stability_score": 50,
                "ready": False,
                "signals": {
                    "fresh_heartbeats": {"passed": True},
                    "required_checks_recent_green": {"passed": False},
                },
                "blocked_reasons": ["required_checks_recent_green"],
            }

        try:
            actions = release_countdown_actions(self.repo, [], scorer=scorer)

            self.assertEqual(calls, [self.repo])
            self.assertEqual(len(actions), 1)
            self.assertEqual(actions[0]["activation"], "default-goal")
            self.assertIsNone(actions[0]["goal"]["milestone"])
            self.assertEqual(actions[0]["goal"]["release"]["passed_signals"], 1)
            self.assertEqual(actions[0]["goal"]["release"]["total_signals"], 2)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_release_countdown_default_goal_is_status_only_and_non_dispatchable(self) -> None:
        actions = release_countdown_actions(
            self.repo,
            [],
            scorer=lambda _: {
                "from_version": "1.2.3-beta.4",
                "to_version": "1.2.3-beta.4",
                "stability_score": 0,
                "ready": False,
                "signals": {},
                "blocked_reasons": ["no_commits_since_last_release"],
            },
        )

        self.assertEqual(actions[0]["kind"], "release-countdown")
        self.assertTrue(actions[0]["status_only"])
        self.assertTrue(actions[0]["no_lifecycle_authority"])
        self.assertNotIn("command", actions[0])
        self.assertNotIn("controller_action", actions[0])
        self.assertFalse(has_dispatchable_action(actions))

    def test_release_countdown_default_goal_scorer_fallback_runs_once(self) -> None:
        calls: list[Path] = []

        def scorer(repo_root: Path) -> dict:
            calls.append(repo_root)
            return {
                "from_version": "1.2.3-beta.4",
                "to_version": "1.2.3-beta.5",
                "stability_score": 25,
                "ready": False,
                "signals": {},
                "blocked_reasons": ["blocked"],
            }

        release_countdown_actions(self.repo, [], scorer=scorer)

        self.assertEqual(calls, [self.repo])

    def test_release_countdown_status_does_not_change_existing_issue_order(self) -> None:
        def scorer(_: Path) -> dict:
            return {
                "from_version": "1.2.3-beta.4",
                "to_version": "1.2.3-beta.4",
                "stability_score": 100,
                "ready": False,
                "signals": {},
                "blocked_reasons": ["no_commits_since_last_release"],
            }

        items = [
            GhItem(
                "issue",
                20,
                "current release target",
                (
                    label_catalog.MANAGED,
                    label_catalog.PHASE_DESIGN_SOLVING,
                    label_catalog.HUMAN_AUTO,
                    label_catalog.MILESTONE_CURRENT,
                    label_catalog.MILESTONE_RELEASE_TARGET,
                ),
            ),
            GhItem("issue", 10, "ordinary", (label_catalog.MANAGED, label_catalog.PHASE_FIXING, label_catalog.HUMAN_AUTO)),
        ]

        combined = release_countdown_actions(self.repo, items, scorer=scorer) + existing_issue_actions(items, self.repo)
        combined.sort(key=lambda action: action["priority"])

        existing = [action for action in combined if action["kind"] == "existing-issue"]
        self.assertEqual([action["item"] for action in existing], ["issue #20", "issue #10"])

    def test_existing_issue_routes_before_audit_fallback(self) -> None:
        plan, stdout = self.run_plan_with_stdout(fixture="existing")

        self.assertEqual(plan["actions"][0]["kind"], "existing-issue")
        self.assertEqual(plan["actions"][0]["item"], "issue #10")
        self.assertIsNone(plan.get("recommendation"))
        self.assertNotIn("RECOMMEND:audit", stdout)

    def test_audit_fallback_only_when_no_actionable_issue_or_pr_exists(self) -> None:
        plan, stdout = self.run_plan_with_stdout(fixture="empty")

        self.assertEqual([action["kind"] for action in plan["actions"]], ["release-countdown"])
        self.assertEqual(plan["actions"][0]["activation"], "default-goal")
        self.assertIsNone(plan["actions"][0]["goal"]["milestone"])
        self.assertEqual(plan["recommendation"], "RECOMMEND:audit")
        self.assertIn("RECOMMEND:audit", stdout)

    def write_transition_assessment(self, number: int, transition_type: str, confidence: float) -> None:
        path = self.repo / ".refactor-loop" / "runs" / "transition-assessments" / f"issue-{number}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "transition_type": transition_type,
                    "confidence": confidence,
                    "evidence_refs": [f".refactor-loop/runs/issue-{number}.md"],
                    "classifier_surface_delta": ["classifier delta"] if transition_type in {"positive-discovery", "classifier-shift"} else [],
                    "ledger_delta": [],
                    "formal_delta": [],
                    "record_growth_delta": [],
                    "net_positive_signal": transition_type == "positive-discovery",
                    "notes": "",
                    "producer": "manual-issue",
                    "source_ref": f"gh-issue-{number}",
                    "work_unit_id": f"issue-{number}",
                }
            ),
            encoding="utf-8",
        )

    def test_existing_issue_transition_bucket_sorts_before_kind_and_number(self) -> None:
        self.write_transition_assessment(61, "positive-discovery", 0.1)
        self.write_transition_assessment(62, "classifier-shift", 0.2)
        self.write_transition_assessment(63, "classifier-shift", 0.9)

        plan = self.run_plan(fixture="transition_sort")

        actions = [action for action in plan["actions"] if action["kind"] == "existing-issue"]
        self.assertEqual(
            [action["item"] for action in actions],
            ["issue #61", "issue #63", "issue #62", "issue #60"],
        )

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
        expected.append("api milestones")
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

        self.assertEqual([action["kind"] for action in plan["actions"]], ["release-countdown"])
        self.assertEqual(plan["recommendation"], "RECOMMEND:audit")

    def test_wakeup_plan_does_not_require_local_maintainer_directive_directory(self) -> None:
        directive_dir = self.repo / ".refactor-loop" / "runs" / "maintainer-directives"
        self.assertFalse(directive_dir.exists())

        plan = self.run_plan()

        self.assertEqual(
            plan["authorization"],
            "skills/codex-refactor-loop/authorizations/runtime-exceptions.md#wakeup-runner-396",
        )
        self.assertEqual(plan["recommendation"], "RECOMMEND:audit")

    def test_all_empty_after_audit_none_zero_still_recommends_audit(self) -> None:
        (self.logs / "audit-iter-8.log").write_text(
            "AUDIT_DONE:none:0\nEXIT=0\n",
            encoding="utf-8",
        )

        plan = self.run_plan()

        self.assertEqual([action["kind"] for action in plan["actions"]], ["release-countdown"])
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
        self.assertTrue(any(item["name"] == "closed_label_reconciler" and item["status"] == "missing" for item in health["items"]))

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

    def test_daemon_health_reports_closed_label_reconciler_missing(self) -> None:
        (self.repo / ".refactor-loop" / "heartbeats" / "closed_label_reconciler.ts").unlink()

        plan = self.run_plan()

        health = plan["daemon_health"]
        self.assertFalse(health["ok"])
        self.assertTrue(
            any(
                item["name"] == "closed_label_reconciler" and item["status"] == "missing"
                for item in health["items"]
            )
        )

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

    def test_single_active_audit_boundary_reports_wait_not_positive_hard_gate(self) -> None:
        plan, stdout = self.run_plan_with_stdout(ps_count=0, active_audit=True)

        self.assertEqual(plan["concurrency"]["actual"], 1)
        self.assertEqual(plan["concurrency"]["deficit"], 4)
        self.assertEqual(plan["recommendation"], "WAIT:single-active-audit")
        self.assertFalse(plan["hard_gate"]["active"])
        self.assertEqual(plan["hard_gate"]["dispatch_required"], 0)
        self.assertEqual(plan["hard_gate"]["reason"], "single_active_audit_in_flight")
        self.assertEqual(plan["hard_gate"]["blocked_deficit"], 4)
        self.assertEqual(plan["hard_gate"]["boundary_task_id"], "audit-iter-8")
        self.assertNotIn("HARD_GATE:dispatch_required=4", stdout)

    def test_no_active_audit_after_audit_done_none_still_recommends_audit(self) -> None:
        (self.logs / "audit-iter-8.log").write_text(
            "AUDIT_DONE:none:0\nEXIT=0\n",
            encoding="utf-8",
        )

        plan, stdout = self.run_plan_with_stdout(ps_count=0)

        self.assertEqual(plan["recommendation"], "RECOMMEND:audit")
        self.assertTrue(plan["hard_gate"]["active"])
        self.assertEqual(plan["hard_gate"]["dispatch_required"], 5)
        self.assertEqual(plan["hard_gate"]["reason"], None)
        self.assertIn("HARD_GATE:dispatch_required=5", stdout)

    def test_open_or_queued_work_bypasses_single_audit_wait(self) -> None:
        plan, stdout = self.run_plan_with_stdout(fixture="existing", ps_count=0, active_audit=True)

        self.assertEqual(plan["actions"][0]["kind"], "existing-issue")
        self.assertTrue(plan["hard_gate"]["active"])
        self.assertEqual(plan["hard_gate"]["dispatch_required"], 4)
        self.assertEqual(plan["hard_gate"]["reason"], None)
        self.assertNotEqual(plan.get("recommendation"), "WAIT:single-active-audit")
        self.assertIn("HARD_GATE:dispatch_required=4", stdout)

    def test_harness_spawn_intent_bypasses_single_audit_wait(self) -> None:
        self.append_harness_spawn_intent()

        plan, stdout = self.run_plan_with_stdout(ps_count=0, active_audit=True)

        self.assertEqual(plan["actions"][0]["kind"], "harness-spawn-intent")
        self.assertTrue(has_dispatchable_action(plan["actions"]))
        self.assertTrue(plan["hard_gate"]["active"])
        self.assertEqual(plan["hard_gate"]["dispatch_required"], 4)
        self.assertEqual(plan["hard_gate"]["reason"], None)
        self.assertNotEqual(plan.get("recommendation"), "WAIT:single-active-audit")
        self.assertIn("HARD_GATE:dispatch_required=4", stdout)

    def test_queued_work_bypasses_single_audit_wait(self) -> None:
        self.write_dispatch("p1", "fix-pr294-round-3")

        plan, stdout = self.run_plan_with_stdout(ps_count=0, active_audit=True)

        self.assertEqual(plan["concurrency"]["actual"], 1)
        self.assertEqual(plan["concurrency"]["deficit"], 4)
        self.assertTrue(plan["hard_gate"]["active"])
        self.assertEqual(plan["hard_gate"]["dispatch_required"], 4)
        self.assertEqual(plan["hard_gate"]["reason"], None)
        self.assertNotEqual(plan.get("recommendation"), "WAIT:single-active-audit")
        self.assertIn("HARD_GATE:dispatch_required=4", stdout)

    def test_expected_active_work_bypasses_single_audit_wait(self) -> None:
        plan, stdout = self.run_plan_with_stdout(fixture="many_active", ps_count=0, active_audit=True)

        self.assertEqual(plan["concurrency"]["expected_from_active_tasks"], 6)
        self.assertEqual(plan["concurrency"]["actual"], 1)
        self.assertEqual(plan["concurrency"]["deficit"], 5)
        self.assertTrue(plan["hard_gate"]["active"])
        self.assertEqual(plan["hard_gate"]["dispatch_required"], 5)
        self.assertEqual(plan["hard_gate"]["reason"], None)
        self.assertNotEqual(plan.get("recommendation"), "WAIT:single-active-audit")
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
        for forbidden in ("labels", "assignees", "milestones", "spawn", "merge"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, host_actions[0])

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

    def test_wakeup_plan_is_closed_action_projection_for_runner(self) -> None:
        self.append_harness_spawn_intent()

        plan = self.run_plan()

        self.assertEqual(plan["schema"], "wakeup-plan")
        self.assertEqual(plan["mode"], "closed-action-projection")
        self.assertTrue(plan["no_lifecycle_authority"])
        self.assertEqual(plan["apply_authority"], "wakeup-runner-396-only")
        action = plan["actions"][0]
        self.assertEqual(action["kind"], "harness-spawn-intent")
        for field in (
            "action_id",
            "runner_authority",
            "preconditions",
            "source_marker",
            "source_artifact",
            "target_kind",
            "target",
            "controller_action",
            "no_generic_command",
        ):
            with self.subTest(field=field):
                self.assertIn(field, action)
        self.assertEqual(action["runner_authority"], "wakeup-runner-396")
        self.assertTrue(action["no_generic_command"])

    def test_status_only_actions_cannot_apply(self) -> None:
        plan = self.run_plan()

        release = [action for action in plan["actions"] if action["kind"] == "release-countdown"][0]
        self.assertTrue(release["status_only"])
        self.assertTrue(release["no_lifecycle_authority"])
        self.assertNotIn("runner_authority", release)
        self.assertNotIn("no_generic_command", release)


if __name__ == "__main__":
    unittest.main()
