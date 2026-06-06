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
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest import mock


SCRIPT_PATH = Path(__file__).resolve()
SKILL_ROOT = SCRIPT_PATH.parents[1]
WAKEUP_PLAN = SKILL_ROOT / "scripts" / "consensus-rnd-cli"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from codex_refactor_loop import labels as label_catalog  # noqa: E402
from codex_refactor_loop.context import LoopContext  # noqa: E402
from codex_refactor_loop.issue_decomposition import issue_decomposition_plan_file_digest  # noqa: E402
from codex_refactor_loop.managed_work_snapshot import ManagedWorkSnapshotResult  # noqa: E402
from codex_refactor_loop.restart import restart_managed_daemon_names  # noqa: E402
from codex_refactor_loop.workflow_stages import assert_stage_slug  # noqa: E402
from codex_refactor_loop.wakeup_plan import (  # noqa: E402
    GhItem,
    _revive_stale_redispatchable_implement_log,
    close_projection_actions,
    force_revive_stuck_implements,
    completed_marker_actions,
    consensus_implementation_fields,
    consensus_implementation_suppressed_reason,
    existing_issue_actions,
    has_dispatchable_action,
    load_github_items_with_status,
    marker_from_completed_log,
    meta_escalation_stuck_seconds,
    repository_stalled_meta_reflector_actions,
    rebase_resolve_actions,
    rebase_resolve_completed_marker_actions,
    release_countdown_actions,
    release_rollup_actions,
    restore_hard_gate_for_dispatchable_actions,
    resolve_repo_root,
    stale_revival_seconds,
    suppress_stale_unexecutable_actions,
)
from test_support.authorization_projection import project_python  # noqa: E402


def wakeup_plan_projection():
    source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py").read_text(encoding="utf-8")
    return project_python(source)


def completed_marker_action(plan: dict, prefix: str) -> dict:
    return next(
        item
        for item in plan["actions"]
        if str(item.get("action_id") or "").startswith(prefix)
    )


class WakeupPlanBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.logs = self.repo / ".refactor-loop" / "logs"
        self.fakebin = self.repo / "fakebin"
        self.logs.mkdir(parents=True)
        self.fakebin.mkdir()
        (self.repo / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.repo / ".config" / "consensus-rnd" / "host.env").write_text(
            f"REPO_ROOT={self.repo}\nGH_REPO_SLUG=owner/repo\nCODEX_FLOOR=5\nINTEGRATION_BRANCH=auto-refact-dev\n",
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

    def test_rebase_resolve_actions_project_conflicting_managed_pr(self) -> None:
        item = GhItem(
            kind="PR",
            number=77,
            title="stale",
            labels=(label_catalog.MANAGED, label_catalog.PHASE_REVIEWING),
            head_ref="refactor/iter77-stale",
            head_sha="abc123",
            mergeable="CONFLICTING",
        )
        ctx = mock.Mock(host_env={"INTEGRATION_BRANCH": "auto-refact-dev"})
        with mock.patch("codex_refactor_loop.wakeup_plan.git_text") as git_text_mock:
            git_text_mock.side_effect = [
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="head\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="base\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="oldbase\n", stderr=""),
            ]
            actions = rebase_resolve_actions(self.repo, ctx, [item], monitor=None)
        action = actions[0]
        self.assertEqual("dispatch_pr_rebase_resolve", action["controller_action"])
        self.assertEqual("PR", action["target_kind"])
        self.assertEqual(77, action["target_number"])
        self.assertEqual("wakeup-runner-396", action["runner_authority"])
        self.assertTrue(action["no_generic_command"])

    def test_rebase_resolve_actions_fetch_live_mergeability_for_snapshot_pr(self) -> None:
        item = GhItem(
            kind="PR",
            number=77,
            title="stale",
            labels=(label_catalog.MANAGED, label_catalog.PHASE_REVIEWING),
            head_ref="refactor/iter77-stale",
            head_sha="abc123",
            mergeable="",
            merge_state_status="",
        )
        ctx = mock.Mock(host_env={"INTEGRATION_BRANCH": "auto-refact-dev"})
        with (
            mock.patch(
                "codex_refactor_loop.wakeup_plan.run_json",
                return_value={
                    "mergeable": "CONFLICTING",
                    "mergeStateStatus": "DIRTY",
                    "headRefOid": "abc123",
                },
            ) as run_json_mock,
            mock.patch("codex_refactor_loop.wakeup_plan.git_text") as git_text_mock,
        ):
            git_text_mock.side_effect = [
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="head\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="base\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="oldbase\n", stderr=""),
            ]
            actions = rebase_resolve_actions(self.repo, ctx, [item], monitor=None)
        run_json_mock.assert_called_once_with(
            ["gh", "pr", "view", "77", "--json", "mergeable,mergeStateStatus,headRefOid"],
            cwd=self.repo,
        )
        action = actions[0]
        self.assertEqual("dispatch_pr_rebase_resolve", action["controller_action"])
        self.assertFalse(action.get("status_only", False))
        self.assertEqual("CONFLICTING", action["mergeable"])
        self.assertEqual("DIRTY", action["mergeStateStatus"])
        self.assertEqual("abc123", action["head_sha"])

    def test_rebase_resolve_actions_suppress_in_flight_resolve(self) -> None:
        item = GhItem(
            kind="PR",
            number=77,
            title="stale",
            labels=(label_catalog.MANAGED, label_catalog.PHASE_REVIEWING),
            head_ref="refactor/iter77-stale",
            head_sha="abc123",
            mergeable="CONFLICTING",
        )
        (self.logs / "rebase-resolve-pr77-r1.log").write_text("worker running\n", encoding="utf-8")
        ctx = mock.Mock(host_env={"INTEGRATION_BRANCH": "auto-refact-dev"})
        actions = rebase_resolve_actions(self.repo, ctx, [item], monitor=None)
        self.assertEqual("rebase_resolve_in_flight", actions[0]["reason"])
        self.assertTrue(actions[0]["status_only"])

    def test_rebase_resolve_completed_marker_projects_commit_push_action(self) -> None:
        log = self.logs / "rebase-resolve-pr77-r1.log"
        log.write_text("resolved\nREBASE_RESOLVE_DONE:77:ok\nEXIT=0\n", encoding="utf-8")
        worktree = self.repo / ".worktrees" / "iter77-stale"
        worktree.mkdir(parents=True)
        porcelain = f"worktree {worktree}\nbranch refs/heads/refactor/iter77-stale\n"
        item = GhItem(
            kind="PR",
            number=77,
            title="stale",
            labels=(label_catalog.MANAGED, label_catalog.PHASE_REVIEWING),
            head_ref="refactor/iter77-stale",
            head_sha="abc123",
        )
        git_dir = self.repo / ".git" / "worktrees" / "iter77-stale"
        git_dir.mkdir(parents=True)
        (git_dir / "MERGE_HEAD").write_text("base\n", encoding="utf-8")

        def fake_git(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
            if command == ["git", "-C", str(self.repo), "worktree", "list", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0, stdout=porcelain, stderr="")
            if command == ["git", "-C", str(worktree), "rev-parse", "--git-dir"]:
                return subprocess.CompletedProcess(command, 0, stdout=str(git_dir) + "\n", stderr="")
            if command == ["git", "-C", str(worktree), "diff", "--name-only", "--diff-filter=U"]:
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="unexpected")

        with mock.patch("codex_refactor_loop.wakeup_plan.git_text", side_effect=fake_git):
            actions = rebase_resolve_completed_marker_actions(self.repo, [item])
        action = actions[0]
        self.assertEqual("commit_push_resolved_pr_rebase", action["controller_action"])
        self.assertEqual("REBASE_RESOLVE_DONE:77:ok", action["source_marker"])
        self.assertEqual(str(worktree), action["worktree"])

    def test_wakeup_plan_projects_conflicting_stale_base_pr_dispatch_as_executable(self) -> None:
        plan = self.run_plan(fixture="stale_base_conflicting_pr")

        action = next(item for item in plan["actions"] if item.get("controller_action") == "dispatch_pr_rebase_resolve")
        self.assertEqual("stale-base-conflicting-pr", action["kind"])
        self.assertEqual("PR", action["target_kind"])
        self.assertEqual(177, action["target_number"])
        self.assertEqual("CONFLICTING", action["mergeable"])
        self.assertEqual("DIRTY", action["mergeStateStatus"])
        self.assertEqual("stale-head-sha", action["head_sha"])
        self.assertFalse(action.get("status_only", False))
        self.assertEqual("wakeup-runner-396", action["runner_authority"])
        self.assertTrue(action["no_generic_command"])

    def test_wakeup_plan_stale_rebase_done_clean_worktree_projects_fresh_dispatch(self) -> None:
        log = self.logs / "rebase-resolve-pr177-r1.log"
        log.write_text("resolved\nREBASE_RESOLVE_DONE:177:ok\nEXIT=0\n", encoding="utf-8")
        self.write_rebase_resolve_worktree_state(merge_head=False)

        plan = self.run_plan(fixture="stale_base_done_clean")

        commit_pushes = [
            item
            for item in plan["actions"]
            if item.get("controller_action") == "commit_push_resolved_pr_rebase"
            and item.get("target_number") == 177
            and not item.get("status_only", False)
        ]
        self.assertEqual([], commit_pushes)
        action = next(
            item
            for item in plan["actions"]
            if item.get("controller_action") == "dispatch_pr_rebase_resolve"
            and item.get("target_number") == 177
            and not item.get("status_only", False)
        )
        self.assertEqual("stale-base-conflicting-pr", action["kind"])
        self.assertEqual("CONFLICTING", action["mergeable"])

    def test_wakeup_plan_projects_rebase_done_only_for_resolved_in_progress_merge(self) -> None:
        log = self.logs / "rebase-resolve-pr177-r1.log"
        log.write_text("resolved\nREBASE_RESOLVE_DONE:177:ok\nEXIT=0\n", encoding="utf-8")
        self.write_rebase_resolve_worktree_state(merge_head=True, unmerged_paths=())

        plan = self.run_plan(fixture="stale_base_done_resolved_merge")

        action = next(
            item
            for item in plan["actions"]
            if item.get("controller_action") == "commit_push_resolved_pr_rebase"
            and item.get("target_number") == 177
            and not item.get("status_only", False)
        )
        self.assertEqual("completed-marker", action["kind"])
        self.assertEqual("REBASE_RESOLVE_DONE:177:ok", action["source_marker"])
        self.assertEqual("PR", action["target_kind"])
        self.assertEqual(177, action["target_number"])
        self.assertFalse(action.get("status_only", False))
        self.assertEqual("wakeup-runner-396", action["runner_authority"])
        self.assertTrue(action["no_generic_command"])

    def test_wakeup_plan_rebase_done_with_unmerged_paths_never_projects_commit_push(self) -> None:
        log = self.logs / "rebase-resolve-pr177-r1.log"
        log.write_text("resolved\nREBASE_RESOLVE_DONE:177:ok\nEXIT=0\n", encoding="utf-8")
        self.write_rebase_resolve_worktree_state(merge_head=True, unmerged_paths=("skills/example.py",))

        plan = self.run_plan(fixture="stale_base_done_unmerged")

        commit_pushes = [
            item
            for item in plan["actions"]
            if item.get("controller_action") == "commit_push_resolved_pr_rebase"
            and item.get("target_number") == 177
            and not item.get("status_only", False)
        ]
        self.assertEqual([], commit_pushes)

    def test_wakeup_plan_keeps_branch_current_rebase_resolve_status_only(self) -> None:
        plan = self.run_plan(fixture="stale_base_branch_current")

        action = next(
            item
            for item in plan["actions"]
            if item.get("kind") == "stale-base-conflicting-pr" and item.get("reason") == "branch_already_contains_base"
        )
        self.assertEqual("PR", action["target_kind"])
        self.assertEqual(177, action["target_number"])
        self.assertTrue(action["status_only"])
        self.assertTrue(action["no_lifecycle_authority"])
        self.assertNotIn("runner_authority", action)
        self.assertNotIn("no_generic_command", action)

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
                cmd3="$3"
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
                    gh_failure)
                      exit 42
                      ;;
                    open_issue_330)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":330,"title":"open target","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:implementing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    open_issue_20)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":20,"title":"open target","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:implementing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    closed_issue_20)
                      printf '[]\n'
                      ;;
                    local_iter_branch_issue20_stale_base)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":20,"title":"open target","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:implementing"},{"name":"crnd:human:auto"},{"name":"crnd:milestone:current"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    closing_pr_issue20|local_iter_branch_issue20|remote_iter_branch_issue20)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":20,"title":"open target","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:implementing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    consensus_issue_330)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":330,"title":"consensus target","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:consensus-reached"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    open_issue_331)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":331,"title":"different open target","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:implementing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    open_issues_330_331_332)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":330,"title":"first target","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:implementing"},{"name":"crnd:human:auto"}]},{"number":331,"title":"overlap target","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:implementing"},{"name":"crnd:human:auto"}]},{"number":332,"title":"disjoint target","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:implementing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    open_issue_453)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":453,"title":"solver target","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:design-solving"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    open_issue_403)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":403,"title":"decompose target","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:design-solving"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    open_issue_53)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":53,"title":"drop target","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:design-solving"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    open_issue_54)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":54,"title":"judge target","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:design-solving"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    open_issue_449)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":449,"title":"consensus target","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:consensus-reached"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    ci_red_issue20)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":20,"title":"open target","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:implementing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    managed_canonical)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":81,"title":"canonical issue","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:implementing"},{"name":"crnd:human:auto"}]},{"number":82,"title":"second canonical issue","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:fixing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    milestone)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":20,"title":"milestone issue","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:milestone:current"},{"name":"crnd:phase:design-solving"},{"name":"crnd:human:auto"}]},{"number":10,"title":"ordinary issue","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:fixing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    existing)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":10,"title":"ordinary issue","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:fixing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    transition_sort)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":60,"title":"unknown issue","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:fixing"},{"name":"crnd:human:auto"}]},{"number":61,"title":"positive issue","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:fixing"},{"name":"crnd:human:auto"}]},{"number":62,"title":"classifier issue","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:fixing"},{"name":"crnd:human:auto"}]},{"number":63,"title":"confident classifier issue","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:fixing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    many_active)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '['
                        for i in 1 2 3 4 5 6; do
                          [[ "$i" != "1" ]] && printf ','
                          printf '{"number":%s,"title":"active issue %s","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:fixing"},{"name":"crnd:human:auto"}]}' "$i" "$i"
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
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":40,"title":"blocked issue","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:blocked"},{"name":"crnd:human:auto"}]},{"number":41,"title":"merged issue","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:merged"},{"name":"crnd:human:auto"}]},{"number":44,"title":"parent issue with child PR","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:pr-open"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    repository_stalled)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":506,"title":"old design issue","updatedAt":"2026-05-01T00:00:00Z","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:design-solving"},{"name":"crnd:human:auto"}]},{"number":507,"title":"old implementation issue","updatedAt":"2026-05-02T00:00:00Z","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:implementing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    repository_fresh)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":506,"title":"fresh design issue","updatedAt":"2099-05-01T00:00:00Z","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:design-solving"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    repository_human_decision)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":506,"title":"human decision issue","updatedAt":"2026-05-01T00:00:00Z","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:design-solving"},{"name":"crnd:human:maintainer-decision"}]}]\n'
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
                    gh_failure)
                      exit 42
                      ;;
                    managed_canonical)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":91,"title":"canonical PR","headRefName":"impl/canonical","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]},{"number":92,"title":"second canonical PR","headRefName":"impl/second","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:design-solving"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    unpushed|unpushed_fetch_fail|unpushed_no_ahead|unpushed_no_remote|unpushed_no_worktree)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":77,"title":"worker output PR","headRefName":"refactor/iter77-worker","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    unpushed_head_dash)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":78,"title":"unsafe dash head","headRefName":"-bad","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    unpushed_head_space)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":79,"title":"unsafe space head","headRefName":"bad ref","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    unpushed_head_control)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":80,"title":"unsafe control head","headRefName":"bad\\u0001ref","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    ci_red)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":31,"title":"red PR","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:ci-running"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    ci_red_issue20)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":31,"title":"red PR","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:ci-running"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    open_pr_123)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":123,"title":"open PR target","headRefName":"impl/pr123","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    stale_base_conflicting_pr|stale_base_branch_current|stale_base_done_clean|stale_base_done_resolved_merge|stale_base_done_unmerged)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":177,"title":"stale-base PR","headRefName":"refactor/iter177-stale","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    open_pr_480)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":480,"title":"wedged review PR","headRefName":"impl/pr480","headRefOid":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    open_pr_77|review_thread_unresolved|review_thread_unresolved_unrelated|review_thread_unresolved_outdated|review_thread_resolved|review_thread_paginated_unresolved|review_thread_graphql_failure|review_thread_malformed|review_thread_pull_request_null|review_thread_page_info_null|review_thread_node_malformed)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":77,"title":"open PR target","headRefName":"impl/pr77","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    closing_pr_issue20)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":320,"title":"closing PR","headRefName":"refactor/iter20-issue-20","body":"Closes #20","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    early_pr_issue20|local_iter_branch_issue20|local_iter_branch_issue20_stale_base)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":320,"title":"early PR","headRefName":"refactor/iter20-issue-20","headRefOid":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","body":"Closes #20","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
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
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":30,"title":"milestone PR","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:milestone:current"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    non_action_statuses)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":42,"title":"non-red CI PR","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:ci-running"},{"name":"crnd:human:auto"}]},{"number":43,"title":"merged PR","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:merged"},{"name":"crnd:human:auto"}]}]\n'
                      else
                        printf '[]\n'
                      fi
                      ;;
                    repository_stalled)
                      if [[ "$label" == "crnd:lifecycle:managed" ]]; then
                        printf '[{"number":536,"title":"old review PR","updatedAt":"2026-05-03T00:00:00Z","headRefName":"refactor/iter506-issue-506","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
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
                  if [[ "$api_path" == "graphql" ]]; then
                    if [[ -n "${WAKEUP_PLAN_GH_QUERY_LOG:-}" ]]; then
                      printf 'api graphql %s\n' "$args" >> "$WAKEUP_PLAN_GH_QUERY_LOG"
                    fi
                    case "$fixture" in
                      review_thread_unresolved)
                        printf '{"data":{"repository":{"pullRequest":{"reviewThreads":{"nodes":[{"id":"PRRT_kwDOExample","isResolved":false,"isOutdated":false}],"pageInfo":{"hasNextPage":false,"endCursor":null}}}}}}\n'
                        ;;
                      review_thread_unresolved_unrelated)
                        printf '{"data":{"repository":{"pullRequest":{"reviewThreads":{"nodes":[{"id":"PRRT_kwDOOther","isResolved":false,"isOutdated":false}],"pageInfo":{"hasNextPage":false,"endCursor":null}}}}}}\n'
                        ;;
                      review_thread_unresolved_outdated)
                        printf '{"data":{"repository":{"pullRequest":{"reviewThreads":{"nodes":[{"id":"PRRT_kwDOExample","isResolved":false,"isOutdated":true}],"pageInfo":{"hasNextPage":false,"endCursor":null}}}}}}\n'
                        ;;
                      review_thread_resolved)
                        printf '{"data":{"repository":{"pullRequest":{"reviewThreads":{"nodes":[{"id":"PRRT_kwDOExample","isResolved":true,"isOutdated":false}],"pageInfo":{"hasNextPage":false,"endCursor":null}}}}}}\n'
                        ;;
                      review_thread_paginated_unresolved)
                        if [[ "$args" == *"after=cursor1"* ]]; then
                          printf '{"data":{"repository":{"pullRequest":{"reviewThreads":{"nodes":[{"id":"PRRT_kwDOExample","isResolved":false,"isOutdated":false}],"pageInfo":{"hasNextPage":false,"endCursor":null}}}}}}\n'
                        else
                          printf '{"data":{"repository":{"pullRequest":{"reviewThreads":{"nodes":[{"id":"PRRT_kwDOOther","isResolved":true,"isOutdated":false}],"pageInfo":{"hasNextPage":true,"endCursor":"cursor1"}}}}}}\n'
                        fi
                        ;;
                      review_thread_graphql_failure)
                        exit 42
                        ;;
                      review_thread_malformed)
                        printf '{"data":{"repository":{"pullRequest":{"reviewThreads":{"nodes":null,"pageInfo":{"hasNextPage":false,"endCursor":null}}}}}}\n'
                        ;;
                      review_thread_pull_request_null)
                        printf '{"data":{"repository":{"pullRequest":null}}}\n'
                        ;;
                      review_thread_page_info_null)
                        printf '{"data":{"repository":{"pullRequest":{"reviewThreads":{"nodes":[],"pageInfo":null}}}}}\n'
                        ;;
                      review_thread_node_malformed)
                        printf '{"data":{"repository":{"pullRequest":{"reviewThreads":{"nodes":[null],"pageInfo":{"hasNextPage":false,"endCursor":null}}}}}}\n'
                        ;;
                      *)
                        printf '{"data":{"repository":{"pullRequest":{"reviewThreads":{"nodes":[],"pageInfo":{"hasNextPage":false,"endCursor":null}}}}}}\n'
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
                if [[ "$cmd1 $cmd2" == "pr view" && "$cmd3" == "177" ]]; then
                  printf '{"mergeable":"CONFLICTING","mergeStateStatus":"DIRTY","headRefOid":"stale-head-sha"}\n'
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

    def write_managed_work_snapshot_fixture(self, fixture: str) -> None:
        path = self.repo / ".refactor-loop" / "state" / "managed-work-snapshot.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if fixture == "gh_failure":
            path.unlink(missing_ok=True)
            return
        path.write_text(
            json.dumps(
                {
                    "schema": "managed-work-snapshot",
                    "fetched_at_epoch": time.time(),
                    "items": self.managed_work_snapshot_items(fixture),
                    "not_live_state_fact_source": True,
                    "not_host_production_ssot": True,
                    "no_lifecycle_authority": True,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def managed_work_snapshot_items(self, fixture: str) -> list[dict[str, object]]:
        def issue(number: int, title: str, labels: list[str], *, updated_at: str = "2026-06-05T00:00:00Z") -> dict[str, object]:
            return {"kind": "issue", "number": number, "title": title, "labels": labels, "state": "open", "updated_at": updated_at}

        def pr(
            number: int,
            title: str,
            labels: list[str],
            *,
            head_ref: str = "",
            head_sha: str = "",
            body: str = "",
            updated_at: str = "2026-06-05T00:00:00Z",
        ) -> dict[str, object]:
            return {
                "kind": "PR",
                "number": number,
                "title": title,
                "labels": labels,
                "head_ref": head_ref or None,
                "head_sha": head_sha,
                "body": body,
                "state": "open",
                "updated_at": updated_at,
            }

        managed = label_catalog.MANAGED
        auto = label_catalog.HUMAN_AUTO
        fixing = label_catalog.PHASE_FIXING
        reviewing = label_catalog.PHASE_REVIEWING
        ci_running = label_catalog.PHASE_CI_RUNNING
        issue_rows: dict[str, list[dict[str, object]]] = {
            "open_issue_330": [issue(330, "open target", [managed, label_catalog.PHASE_IMPLEMENTING, auto])],
            "open_issue_20": [issue(20, "open target", [managed, label_catalog.PHASE_IMPLEMENTING, auto])],
            "local_iter_branch_issue20_stale_base": [issue(20, "open target", [managed, label_catalog.PHASE_IMPLEMENTING, auto, label_catalog.MILESTONE_CURRENT])],
            "local_iter_branch_issue20": [issue(20, "open target", [managed, label_catalog.PHASE_IMPLEMENTING, auto])],
            "remote_iter_branch_issue20": [issue(20, "open target", [managed, label_catalog.PHASE_IMPLEMENTING, auto])],
            "open_issue_331": [issue(331, "different open target", [managed, label_catalog.PHASE_IMPLEMENTING, auto])],
            "open_issues_330_331_332": [
                issue(330, "first target", [managed, label_catalog.PHASE_IMPLEMENTING, auto]),
                issue(331, "overlap target", [managed, label_catalog.PHASE_IMPLEMENTING, auto]),
                issue(332, "disjoint target", [managed, label_catalog.PHASE_IMPLEMENTING, auto]),
            ],
            "open_issue_453": [issue(453, "solver target", [managed, label_catalog.PHASE_DESIGN_SOLVING, auto])],
            "open_issue_403": [issue(403, "decompose target", [managed, label_catalog.PHASE_DESIGN_SOLVING, auto])],
            "open_issue_53": [issue(53, "drop target", [managed, label_catalog.PHASE_DESIGN_SOLVING, auto])],
            "open_issue_54": [issue(54, "judge target", [managed, label_catalog.PHASE_DESIGN_SOLVING, auto])],
            "open_issue_449": [issue(449, "consensus target", [managed, label_catalog.PHASE_CONSENSUS_REACHED, auto])],
            "ci_red_issue20": [issue(20, "open target", [managed, label_catalog.PHASE_IMPLEMENTING, auto])],
            "consensus_issue_330": [issue(330, "consensus target", [managed, label_catalog.PHASE_CONSENSUS_REACHED, auto])],
            "managed_canonical": [
                issue(81, "canonical issue", [managed, label_catalog.PHASE_IMPLEMENTING, auto]),
                issue(82, "second canonical issue", [managed, fixing, auto]),
            ],
            "milestone": [
                issue(20, "milestone issue", [managed, label_catalog.MILESTONE_CURRENT, label_catalog.PHASE_DESIGN_SOLVING, auto]),
                issue(10, "ordinary issue", [managed, fixing, auto]),
            ],
            "existing": [issue(10, "ordinary issue", [managed, fixing, auto])],
            "transition_sort": [
                issue(60, "unknown issue", [managed, fixing, auto]),
                issue(61, "positive issue", [managed, fixing, auto]),
                issue(62, "classifier issue", [managed, fixing, auto]),
                issue(63, "confident classifier issue", [managed, fixing, auto]),
            ],
            "many_active": [issue(number, f"active issue {number}", [managed, fixing, auto]) for number in range(1, 7)],
            "represented_parent": [issue(239, "represented parent", [managed, label_catalog.PHASE_IMPLEMENTING, auto])],
            "pr_open_parent": [issue(239, "parent issue with open PR", [managed, label_catalog.PHASE_PR_OPEN, auto])],
            "non_action_statuses": [
                issue(40, "blocked issue", [managed, label_catalog.PHASE_BLOCKED, auto]),
                issue(41, "merged issue", [managed, label_catalog.PHASE_MERGED, auto]),
                issue(44, "parent issue with child PR", [managed, label_catalog.PHASE_PR_OPEN, auto]),
            ],
            "repository_stalled": [
                issue(506, "old design issue", [managed, label_catalog.PHASE_DESIGN_SOLVING, auto], updated_at="2026-05-01T00:00:00Z"),
                issue(507, "old implementation issue", [managed, label_catalog.PHASE_IMPLEMENTING, auto], updated_at="2026-05-02T00:00:00Z"),
            ],
            "repository_fresh": [
                issue(506, "fresh design issue", [managed, label_catalog.PHASE_DESIGN_SOLVING, auto], updated_at="2099-05-01T00:00:00Z"),
            ],
            "repository_human_decision": [
                issue(
                    506,
                    "human decision issue",
                    [managed, label_catalog.PHASE_DESIGN_SOLVING, label_catalog.HUMAN_MAINTAINER_DECISION],
                    updated_at="2026-05-01T00:00:00Z",
                ),
            ],
        }
        pr_rows: dict[str, list[dict[str, object]]] = {
            "local_iter_branch_issue20_stale_base": [
                pr(320, "closing PR", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="refactor/iter20-issue-20", body="Closes #20"),
            ],
            "local_iter_branch_issue20": [
                pr(320, "closing PR", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="refactor/iter20-issue-20", body="Closes #20"),
            ],
            "managed_canonical": [
                pr(91, "canonical PR", [managed, reviewing, auto], head_ref="impl/canonical"),
                pr(92, "second canonical PR", [managed, label_catalog.PHASE_DESIGN_SOLVING, auto], head_ref="impl/second"),
            ],
            "unpushed": [pr(77, "worker output PR", [managed, reviewing, auto], head_ref="refactor/iter77-worker")],
            "unpushed_fetch_fail": [pr(77, "worker output PR", [managed, reviewing, auto], head_ref="refactor/iter77-worker")],
            "unpushed_no_ahead": [pr(77, "worker output PR", [managed, reviewing, auto], head_ref="refactor/iter77-worker")],
            "unpushed_no_remote": [pr(77, "worker output PR", [managed, reviewing, auto], head_ref="refactor/iter77-worker")],
            "unpushed_no_worktree": [pr(77, "worker output PR", [managed, reviewing, auto], head_ref="refactor/iter77-worker")],
            "unpushed_head_dash": [pr(78, "unsafe dash head", [managed, reviewing, auto], head_ref="-bad")],
            "unpushed_head_space": [pr(79, "unsafe space head", [managed, reviewing, auto], head_ref="bad ref")],
            "unpushed_head_control": [pr(80, "unsafe control head", [managed, reviewing, auto], head_ref="bad\u0001ref")],
            "ci_red": [pr(31, "red PR", [managed, ci_running, auto], head_sha="ci-red-sha")],
            "ci_red_issue20": [pr(31, "red PR", [managed, ci_running, auto], head_sha="ci-red-sha")],
            "open_pr_123": [pr(123, "open PR target", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="impl/pr123")],
            "stale_base_conflicting_pr": [
                pr(177, "stale-base PR", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="refactor/iter177-stale")
            ],
            "stale_base_done_clean": [
                pr(177, "stale-base PR", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="refactor/iter177-stale")
            ],
            "stale_base_done_resolved_merge": [
                pr(177, "stale-base PR", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="refactor/iter177-stale")
            ],
            "stale_base_done_unmerged": [
                pr(177, "stale-base PR", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="refactor/iter177-stale")
            ],
            "stale_base_branch_current": [
                pr(177, "stale-base PR", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="refactor/iter177-stale")
            ],
            "open_pr_480": [
                pr(480, "wedged review PR", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="impl/pr480", head_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
            ],
            "open_pr_77": [pr(77, "open PR target", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="impl/pr77")],
            "review_thread_unresolved": [pr(77, "open PR target", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="impl/pr77")],
            "review_thread_unresolved_unrelated": [pr(77, "open PR target", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="impl/pr77")],
            "review_thread_unresolved_outdated": [pr(77, "open PR target", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="impl/pr77")],
            "review_thread_resolved": [pr(77, "open PR target", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="impl/pr77")],
            "review_thread_paginated_unresolved": [pr(77, "open PR target", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="impl/pr77")],
            "review_thread_graphql_failure": [pr(77, "open PR target", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="impl/pr77")],
            "review_thread_malformed": [pr(77, "open PR target", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="impl/pr77")],
            "review_thread_pull_request_null": [pr(77, "open PR target", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="impl/pr77")],
            "review_thread_page_info_null": [pr(77, "open PR target", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="impl/pr77")],
            "review_thread_node_malformed": [pr(77, "open PR target", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="impl/pr77")],
            "closing_pr_issue20": [
                issue(20, "open target", [managed, label_catalog.PHASE_IMPLEMENTING, auto]),
                pr(320, "closing PR", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="refactor/iter20-issue-20", body="Closes #20"),
            ],
            "represented_parent": [pr(255, "child PR", [managed, label_catalog.PHASE_REVIEWING, auto], head_ref="impl/issue239", body="Closes #239")],
            "milestone": [pr(30, "milestone PR", [managed, label_catalog.MILESTONE_CURRENT, reviewing, auto])],
            "non_action_statuses": [
                pr(42, "non-red CI PR", [managed, ci_running, auto]),
                pr(43, "merged PR", [managed, label_catalog.PHASE_MERGED, auto]),
            ],
            "repository_stalled": [
                pr(
                    536,
                    "old review PR",
                    [managed, label_catalog.PHASE_REVIEWING, auto],
                    head_ref="refactor/iter506-issue-506",
                    updated_at="2026-05-03T00:00:00Z",
                ),
            ],
        }
        return [*issue_rows.get(fixture, []), *pr_rows.get(fixture, [])]

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
                  if [[ "$fixture" == "local_iter_branch_issue20" || "$fixture" == "local_iter_branch_issue20_stale_base" ]]; then
                    printf 'worktree %s/.worktrees/iter20-issue-20\nbranch refs/heads/refactor/iter20-issue-20\n\n' "$WAKEUP_PLAN_REPO_ROOT"
                    exit 0
                  fi
                  if [[ "$fixture" == "stale_base_done_clean" || "$fixture" == "stale_base_done_resolved_merge" || "$fixture" == "stale_base_done_unmerged" ]]; then
                    printf 'worktree %s/.worktrees/iter177-stale\nbranch refs/heads/refactor/iter177-stale\n\n' "$WAKEUP_PLAN_REPO_ROOT"
                    exit 0
                  fi
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
                if [[ "$*" == *"rev-parse --verify refs/heads/refactor/iter20-issue-20"* ]]; then
                  [[ "$fixture" == "local_iter_branch_issue20" || "$fixture" == "local_iter_branch_issue20_stale_base" ]] && printf 'local-iter-sha\n' && exit 0
                  exit 1
                fi
                if [[ "$*" == *"rev-parse --verify refs/remotes/origin/refactor/iter20-issue-20"* ]]; then
                  [[ "$fixture" == "local_iter_branch_issue20" || "$fixture" == "local_iter_branch_issue20_stale_base" || "$fixture" == "remote_iter_branch_issue20" ]] && printf 'remote-iter-sha\n' && exit 0
                  exit 1
                fi
                if [[ "$*" == *"rev-parse --verify origin/refactor/iter177-stale"* ]]; then
                  [[ "$fixture" == "stale_base_conflicting_pr" || "$fixture" == "stale_base_branch_current" || "$fixture" == "stale_base_done_clean" || "$fixture" == "stale_base_done_resolved_merge" || "$fixture" == "stale_base_done_unmerged" ]] && printf 'stale-head-sha\n' && exit 0
                  exit 1
                fi
                if [[ "$*" == *"rev-parse --verify origin/auto-refact-dev"* ]]; then
                  if [[ "$fixture" == "stale_base_conflicting_pr" || "$fixture" == "stale_base_branch_current" || "$fixture" == "stale_base_done_clean" || "$fixture" == "stale_base_done_resolved_merge" || "$fixture" == "stale_base_done_unmerged" ]]; then
                    printf 'base-sha\n'
                    exit 0
                  fi
                fi
                if [[ "$*" == *"merge-base origin/refactor/iter177-stale origin/auto-refact-dev"* ]]; then
                  if [[ "$fixture" == "stale_base_conflicting_pr" || "$fixture" == "stale_base_done_clean" || "$fixture" == "stale_base_done_resolved_merge" || "$fixture" == "stale_base_done_unmerged" ]]; then
                    printf 'old-base-sha\n'
                    exit 0
                  fi
                  if [[ "$fixture" == "stale_base_branch_current" ]]; then
                    printf 'base-sha\n'
                    exit 0
                  fi
                  exit 1
                fi
                if [[ "$*" == *".worktrees/iter177-stale"* && "$*" == *"rev-parse --git-dir"* ]]; then
                  printf '%s/.git/worktrees/iter177-stale\n' "$WAKEUP_PLAN_REPO_ROOT"
                  exit 0
                fi
                if [[ "$*" == *".worktrees/iter177-stale"* && "$*" == *"diff --name-only --diff-filter=U"* ]]; then
                  if [[ -f "$WAKEUP_PLAN_REPO_ROOT/.refactor-loop/state/rebase-unmerged-paths.txt" ]]; then
                    cat "$WAKEUP_PLAN_REPO_ROOT/.refactor-loop/state/rebase-unmerged-paths.txt"
                  fi
                  exit 0
                fi
                if [[ "$*" == *"rev-parse --verify refs/heads/refactor/iter"* ]]; then
                  exit 1
                fi
                if [[ "$*" == *"rev-parse --verify refs/remotes/origin/refactor/iter"* ]]; then
                  exit 1
                fi
                if [[ "$*" == *".worktrees/iter20-issue-20"* && "$*" == *"rev-parse --abbrev-ref HEAD"* ]]; then
                  [[ "$fixture" == "local_iter_branch_issue20" ]] && printf 'refactor/iter20-issue-20\n' && exit 0
                  [[ "$fixture" == "local_iter_branch_issue20_stale_base" ]] && printf 'refactor/iter20-issue-20\n' && exit 0
                  exit 1
                fi
                if [[ "$*" == *".worktrees/iter20-issue-20"* && "$*" == *"merge-base HEAD origin/auto-refact-dev"* ]]; then
                  if [[ "$fixture" == "local_iter_branch_issue20_stale_base" ]]; then
                    printf 'old-base\n'
                    exit 0
                  fi
                  [[ "$fixture" == "local_iter_branch_issue20" ]] && printf 'fresh-base\n' && exit 0
                  exit 1
                fi
                if [[ "$*" == *".worktrees/iter20-issue-20"* && "$*" == *"rev-parse --verify origin/auto-refact-dev"* ]]; then
                  if [[ "$fixture" == "local_iter_branch_issue20_stale_base" ]]; then
                    printf 'new-base\n'
                    exit 0
                  fi
                  [[ "$fixture" == "local_iter_branch_issue20" ]] && printf 'fresh-base\n' && exit 0
                  exit 1
                fi
                if [[ "$*" == *"rev-list --count refs/remotes/origin/refactor/iter77-worker..HEAD"* ]]; then
                  if [[ "$fixture" == "unpushed_no_ahead" ]]; then
                    printf '0\n'
                  else
                    printf '2\n'
                  fi
                  exit 0
                fi
                if [[ "$*" == *"rev-list --count refs/remotes/origin/refactor/iter20-issue-20..HEAD"* ]]; then
                  [[ "$fixture" == "local_iter_branch_issue20" || "$fixture" == "local_iter_branch_issue20_stale_base" ]] && printf '2\n' && exit 0
                  exit 1
                fi
                if [[ "$*" == *"diff --quiet"* ]]; then
                  [[ "$fixture" == "local_iter_branch_issue20" || "$fixture" == "local_iter_branch_issue20_stale_base" ]] && exit 1
                  exit 0
                fi
                if [[ "$*" == *"rev-parse --verify refs/remotes/origin/integration"* ]]; then
                  [[ "$fixture" == "release_rollup_refs_fail" ]] && exit 42
                  if [[ "$fixture" == "release_rollup_moved" ]]; then
                    printf 'current-integration-sha\n'
                  elif [[ "$fixture" == release_rollup* ]]; then
                    printf 'integration-sha\n'
                  fi
                  exit 0
                fi
                if [[ "$*" == *"rev-parse --verify refs/remotes/origin/review"* ]]; then
                  [[ "$fixture" == "release_rollup_refs_fail" ]] && exit 42
                  if [[ "$fixture" == "release_rollup_same_sha" ]]; then
                    printf 'integration-sha\n'
                  elif [[ "$fixture" == release_rollup* ]]; then
                    printf 'review-sha\n'
                  fi
                  exit 0
                fi
                if [[ "$*" == *"rev-list --count refs/remotes/origin/review..refs/remotes/origin/integration"* ]]; then
                  [[ "$fixture" == "release_rollup_refs_fail" ]] && exit 42
                  if [[ "$fixture" == "release_rollup_no_ahead" ]]; then
                    printf '0\n'
                  elif [[ "$fixture" == release_rollup* ]]; then
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
        self.write_managed_work_snapshot_fixture(fixture)
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.fakebin}{os.pathsep}{env.get('PATH', '')}",
                "CODEX_FLOOR": "5",
                "GH_REPO_SLUG": "owner/repo",
                "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env",
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
        self.write_managed_work_snapshot_fixture(fixture)
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.fakebin}{os.pathsep}{env.get('PATH', '')}",
                "CODEX_FLOOR": "5",
                "GH_REPO_SLUG": "owner/repo",
                "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env",
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

    def write_issue_decomposition_artifacts(self, *, issue: int = 403, round_no: int = 6) -> tuple[str, str]:
        runs = self.repo / ".refactor-loop" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        consensus = f".refactor-loop/runs/phase9-issue{issue}-r{round_no}-judge.md"
        child_one = ".refactor-loop/runs/decompose-child-one.md"
        child_two = ".refactor-loop/runs/decompose-child-two.md"
        for path, scope, non_goals in (
            (child_one, "First bounded scope", "No parent lifecycle mutation"),
            (child_two, "Second bounded scope", "No public issue factory"),
        ):
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
        parent_comment = ".refactor-loop/runs/decompose-parent-comment.md"
        (self.repo / parent_comment).write_text(f"Parent issue: #{issue}\n\nChildren opened.\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        plan_path = ".refactor-loop/runs/decomposition-plan.json"
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
        ctx = LoopContext.load(repo_root=self.repo, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})
        digest = issue_decomposition_plan_file_digest(ctx, plan_path)
        (self.repo / consensus).write_text(
            "---\nissue: 403\ncluster: issue-403\nconvergence_round: 6\ndecision: consensus\n---\n\n"
            "## If consensus\n"
            '- controller_action="apply_issue_decomposition_plan"\n'
            f"- issue_decomposition_plan_path: {plan_path}\n"
            f"- issue_decomposition_plan_digest: {digest}\n"
            f"- issue_decomposition_proof: plan {plan_path} digest {digest} reached consensus for parent issue #{issue}\n\n"
            "META_JUDGE_DONE:consensus:decompose\n",
            encoding="utf-8",
        )
        return plan_path, digest

    def write_markerless_clean_log(self, name: str) -> Path:
        path = self.logs / name
        path.write_text("worker chatter with no standalone marker\nEXIT=0\n", encoding="utf-8")
        return path

    def write_rebase_resolve_worktree_state(self, *, merge_head: bool, unmerged_paths: tuple[str, ...] = ()) -> None:
        worktree = self.repo / ".worktrees" / "iter177-stale"
        worktree.mkdir(parents=True, exist_ok=True)
        git_dir = self.repo / ".git" / "worktrees" / "iter177-stale"
        git_dir.mkdir(parents=True, exist_ok=True)
        if merge_head:
            (git_dir / "MERGE_HEAD").write_text("base\n", encoding="utf-8")
        else:
            (git_dir / "MERGE_HEAD").unlink(missing_ok=True)
        unmerged = self.repo / ".refactor-loop" / "state" / "rebase-unmerged-paths.txt"
        unmerged.parent.mkdir(parents=True, exist_ok=True)
        unmerged.write_text("".join(f"{path}\n" for path in unmerged_paths), encoding="utf-8")

    def write_run_artifact(self, stem: str, *lines: str) -> Path:
        path = self.repo / ".refactor-loop" / "runs" / f"{stem}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join([*lines, ""]), encoding="utf-8")
        return path

    def write_implementation_pr_artifacts(self, issue: int = 20, cluster: str = "issue-20") -> tuple[Path, Path]:
        runs = self.repo / ".refactor-loop" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        title = runs / f"implementation-pr-{cluster}-title.txt"
        body = runs / f"implementation-pr-{cluster}-body.md"
        title.write_text(f"完成 issue #{issue} 的发布契约\n", encoding="utf-8")
        body.write_text(
            "## 修改文件\n\n- skills/codex-refactor-loop/scripts/codex_refactor_loop/wakeup_plan.py\n\n"
            "## 测试结果\n\n- python3 skills/codex-refactor-loop/scripts/test_wakeup_plan.py\n\n"
            "## deviation 记录\n\n- none\n\n"
            f"Closes #{issue}\n\n"
            "⟦AI:AUTO-LOOP⟧\n",
            encoding="utf-8",
        )
        return title, body

    def set_log_mtime(self, name: str, mtime: float) -> None:
        os.utime(self.logs / name, (mtime, mtime))

    def write_consensus_artifact(
        self,
        issue: int = 20,
        round_no: int = 5,
        *,
        frontmatter: str = "decision: consensus",
        include_if_consensus: bool = True,
        include_owner: bool = True,
        design_decision_path: str | None = None,
        scope_paths: str | None = None,
        old_pattern: str | None = "consensus to implement used solver fallback",
        new_principle: str | None = "project implementation only from the consensus judge artifact",
        verification_hints: str | None = "python3 -m unittest skills/codex-refactor-loop/scripts/test_wakeup_plan.py",
        marker: str = "META_JUDGE_DONE:consensus:structural",
    ) -> Path:
        runs = self.repo / ".refactor-loop" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        artifact = runs / f"phase9-issue{issue}-r{round_no}-judge.md"
        rel = artifact.relative_to(self.repo).as_posix()
        owner_path = design_decision_path if design_decision_path is not None else rel
        if_consensus_lines: list[str] = []
        if include_if_consensus:
            owner_line = (
                f"- Implementation owner: dispatch implement codex with cluster_id=issue-{issue}, design_decision_path={owner_path}"
                if include_owner
                else ""
            )
            scope_value = scope_paths if scope_paths is not None else (
                "- skills/codex-refactor-loop/scripts/codex_refactor_loop/wakeup_plan.py\n"
                "- skills/codex-refactor-loop/scripts/codex_refactor_loop/wakeup_runner.py"
            )
            if_consensus_lines = [
                "## If consensus",
                "- Chosen framing: structural",
                "- Implement plan:",
                "  - scope_paths:",
                *[f"    {line}" for line in scope_value.splitlines()],
                f"  - old_pattern: {old_pattern or ''}",
                f"  - new_principle: {new_principle or ''}",
                f"  - verification_hints: {verification_hints or ''}",
            ]
            if owner_line:
                if_consensus_lines.append(owner_line)
        body_lines = [
            "---",
            f"issue: {issue}",
            f"convergence_round: {round_no}",
            frontmatter,
            "---",
            "",
            "## Decision",
            "Durable source must be checked before implementation dispatch.",
            "",
            *if_consensus_lines,
            "",
            marker,
            "",
        ]
        artifact.write_text("\n".join(body_lines), encoding="utf-8")
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

    def append_release_rollup_event(self, **overrides: object) -> dict[str, object]:
        event: dict[str, object] = {
            "integration_branch": "integration",
            "review_base_branch": "review",
            "integration_sha": "integration-sha",
            "review_base_sha": "review-sha",
            "ahead_count": 2,
            "reason": "integration-ahead-review-base-without-open-rollup-pr",
        }
        event.update(overrides)
        pending = self.repo / ".refactor-loop" / ".controller-pending-events.log"
        with pending.open("a", encoding="utf-8") as handle:
            handle.write("DEV_SYNC_PENDING:release-rollup-needed:" + json.dumps(event, sort_keys=True) + "\n")
        return event

    def assert_harness_spawn_intent_invalid(self, expected_reason: str, **overrides: object) -> None:
        (self.repo / ".refactor-loop" / ".controller-pending-events.log").write_text("", encoding="utf-8")
        self.append_harness_spawn_intent(**overrides)

        plan = self.run_plan()

        action = plan["actions"][0]
        self.assertEqual(action["kind"], "harness-spawn-intent-invalid")
        self.assertEqual(action["reason"], expected_reason)

    def harness_spawn_actions(self, plan: dict) -> list[dict]:
        return [action for action in plan["actions"] if action["kind"] == "harness-spawn-intent"]

    def action_index(self, plan: dict, predicate: object) -> int:
        for index, action in enumerate(plan["actions"]):
            if predicate(action):
                return index
        self.fail(f"missing action in plan: {json.dumps(plan['actions'], sort_keys=True)}")

    def assert_before_all_new_work_spawns(self, plan: dict, action_index: int) -> None:
        spawn_indexes = [
            index
            for index, action in enumerate(plan["actions"])
            if action.get("controller_action") == "spawn_codex_harness_background"
            and action.get("kind") == "harness-spawn-intent"
        ]
        self.assertGreaterEqual(len(spawn_indexes), 1)
        self.assertLess(action_index, min(spawn_indexes))

    def test_harness_spawn_intent_accepts_only_spawn_codex_string_command(self) -> None:
        self.append_harness_spawn_intent()

        plan = self.run_plan(fixture="open_issue_330")

        action = next(item for item in plan["actions"] if item["kind"] == "harness-spawn-intent")
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

    def test_review_gate_completed_marker_routes_before_new_work_spawn_intents(self) -> None:
        self.append_harness_spawn_intent(
            intent_id="new-work-330",
            task_id="new-work-330",
            prompt=".refactor-loop/prompts/new-work-330.md",
            log=".refactor-loop/logs/new-work-330.log",
        )
        self.append_harness_spawn_intent(
            intent_id="new-work-331",
            task_id="new-work-331",
            prompt=".refactor-loop/prompts/new-work-331.md",
            log=".refactor-loop/logs/new-work-331.log",
        )
        self.write_completed_log("review-pr123-architect-r1.log", "REVIEW_DONE:123:architect:reject")

        plan = self.run_plan(fixture="open_pr_123", ps_count=0)

        review_index = self.action_index(
            plan,
            lambda action: action.get("controller_action") == "review_gate"
            and action.get("target_kind") == "PR"
            and action.get("target_number") == 123
            and not action.get("status_only"),
        )
        self.assert_before_all_new_work_spawns(plan, review_index)

    def test_publish_implementation_completed_marker_routes_before_new_work_spawn_intents(self) -> None:
        self.append_harness_spawn_intent(
            intent_id="new-work-330",
            task_id="new-work-330",
            prompt=".refactor-loop/prompts/new-work-330.md",
            log=".refactor-loop/logs/new-work-330.log",
        )
        self.append_harness_spawn_intent(
            intent_id="new-work-331",
            task_id="new-work-331",
            prompt=".refactor-loop/prompts/new-work-331.md",
            log=".refactor-loop/logs/new-work-331.log",
        )
        (self.logs / "implement-issue20.log").write_text(
            "IMPLEMENT_DONE:issue-20:ok\nEXIT=0\n",
            encoding="utf-8",
        )
        (self.repo / ".worktrees" / "iter20-issue-20").mkdir(parents=True)
        self.write_implementation_pr_artifacts(issue=20, cluster="issue-20")

        plan = self.run_plan(fixture="local_iter_branch_issue20", ps_count=0)

        publish_index = self.action_index(
            plan,
            lambda action: action.get("controller_action") == "publish_implementation_output"
            and action.get("target_kind") == "issue"
            and action.get("target_number") == 20
            and not action.get("status_only"),
        )
        self.assert_before_all_new_work_spawns(plan, publish_index)

    def test_wakeup_plan_priority_order_keeps_existing_front_of_queue_routes(self) -> None:
        pending = self.repo / ".refactor-loop" / ".controller-pending-events.log"
        pending.write_text("2026-05-31T00:00:00Z maintainer comment on PR #31\n", encoding="utf-8")
        self.append_harness_spawn_intent(
            intent_id="new-work-ci",
            task_id="new-work-ci",
            prompt=".refactor-loop/prompts/new-work-ci.md",
            log=".refactor-loop/logs/new-work-ci.log",
        )
        (self.repo / ".refactor-loop" / ".concurrency-alert.log").write_text(
            "[2026-05-29T00:00:00Z] P0 no-gap-violation: fixture\n",
            encoding="utf-8",
        )

        plan = self.run_plan(fixture="ci_red", ps_count=0)

        kinds = [action["kind"] for action in plan["actions"]]
        self.assertLess(kinds.index("maintainer-comment"), kinds.index("ci-red"))
        self.assertLess(kinds.index("ci-red"), kinds.index("no-gap-violation"))
        self.assertLess(kinds.index("no-gap-violation"), kinds.index("harness-spawn-intent"))
        ci_action = next(action for action in plan["actions"] if action["kind"] == "ci-red")
        self.assertTrue(ci_action["status_only"])

    def test_status_only_completed_marker_keeps_completed_marker_priority_class(self) -> None:
        self.append_harness_spawn_intent(
            intent_id="new-work-330",
            task_id="new-work-330",
            prompt=".refactor-loop/prompts/new-work-330.md",
            log=".refactor-loop/logs/new-work-330.log",
        )
        self.write_completed_log("implement-issue330.log", "IMPLEMENT_DONE:issue-330:ok")

        plan = self.run_plan(fixture="open_issue_330", ps_count=0)

        publish_index = self.action_index(
            plan,
            lambda action: action.get("controller_action") == "publish_implementation_output"
            and action.get("status_only")
            and action.get("target_number") == 330,
        )
        self.assert_before_all_new_work_spawns(plan, publish_index)

    def test_harness_spawn_intent_accepts_absolute_repo_contained_cd(self) -> None:
        self.append_harness_spawn_intent(cd=str(self.repo.resolve()))

        plan = self.run_plan(fixture="open_issue_330")

        action = next(item for item in plan["actions"] if item["kind"] == "harness-spawn-intent")
        self.assertEqual(action["kind"], "harness-spawn-intent")
        self.assertEqual(action["cd"], str(self.repo.resolve()))

    def test_harness_spawn_intent_rejects_absolute_cd_outside_repo(self) -> None:
        self.append_harness_spawn_intent(cd="/tmp/outside-consensus-rnd")

        plan = self.run_plan()

        action = plan["actions"][0]
        self.assertEqual(action["kind"], "harness-spawn-intent-invalid")
        self.assertIn("invalid-path:cd escapes REPO_ROOT", action["reason"])

    def test_harness_spawn_intent_rejects_argv_command_array(self) -> None:
        self.append_harness_spawn_intent(command=["consensus-rnd-cli", "spawn-codex"])

        plan = self.run_plan()

        action = plan["actions"][0]
        self.assertEqual(action["kind"], "harness-spawn-intent-invalid")
        self.assertEqual(action["reason"], "command-not-spawn-codex")

    def test_harness_spawn_intent_rejects_generic_command_fields(self) -> None:
        forbidden_fields = (
            "argv",
            "args",
            "shell",
            "cmd",
            "command_line",
            "commands",
            "env",
            "git",
            "gh",
            "executor",
            "lifecycle_authority",
            "lifecycle_owner",
            "target_ref",
        )
        for field in forbidden_fields:
            with self.subTest(field=field):
                (self.repo / ".refactor-loop" / ".controller-pending-events.log").write_text("", encoding="utf-8")
                self.append_harness_spawn_intent(intent_id=f"bad-{field}", **{field: "forbidden"})

                plan = self.run_plan()

                action = next(item for item in plan["actions"] if item["kind"] == "harness-spawn-intent-invalid")
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

                action = next(item for item in plan["actions"] if item["kind"] == "harness-spawn-intent-invalid")
                self.assertEqual(action["kind"], "harness-spawn-intent-invalid")
                self.assertEqual(action["reason"], expected_reason)

    def test_harness_spawn_intent_rejects_missing_path_fields_and_bad_path(self) -> None:
        for field in ("cd", "prompt", "log"):
            with self.subTest(field=field):
                self.assert_harness_spawn_intent_invalid(f"missing-{field}", **{field: ""})

        self.assert_harness_spawn_intent_invalid(
            "invalid-path:cd escapes REPO_ROOT: '../outside'",
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
            plan = self.run_plan(fixture="open_issue_330")
        finally:
            os.environ.pop("WAKEUP_PLAN_PS_EXTRA", None)

        actions = [action for action in plan["actions"] if action["kind"] == "harness-spawn-intent"]
        self.assertEqual([action["intent_id"] for action in actions], ["duplicate"])

    def test_harness_spawn_intent_retries_after_failed_target_log(self) -> None:
        self.append_harness_spawn_intent(
            intent_id="failed-log-retry",
            task_id="phase9-issue453-r1-minimal",
            log=".refactor-loop/logs/phase9-issue453-r1-minimal.log",
        )
        (self.logs / "phase9-issue453-r1-minimal.log").write_text(
            "SPAWN_FAILED=codex missing\nEXIT=127\n",
            encoding="utf-8",
        )

        plan = self.run_plan(fixture="open_issue_453")

        self.assertEqual([action["intent_id"] for action in self.harness_spawn_actions(plan)], ["failed-log-retry"])

    def test_harness_spawn_intent_suppresses_terminal_closed_blocked_marker(self) -> None:
        self.append_harness_spawn_intent(intent_id="closed-intent")
        pending = self.repo / ".refactor-loop" / ".controller-pending-events.log"
        with pending.open("a", encoding="utf-8") as handle:
            handle.write("2026-05-31T00:00:01Z WAKEUP_RUNNER_BLOCKED:harness-spawn-intent:closed-intent:target_not_open:CLOSED\n")

        plan = self.run_plan()

        self.assertEqual(self.harness_spawn_actions(plan), [])

    def test_harness_spawn_intent_suppresses_terminal_merged_blocked_marker(self) -> None:
        self.append_harness_spawn_intent(intent_id="merged-intent")
        pending = self.repo / ".refactor-loop" / ".controller-pending-events.log"
        with pending.open("a", encoding="utf-8") as handle:
            handle.write("2026-05-31T00:00:01Z WAKEUP_RUNNER_BLOCKED:harness-spawn-intent:merged-intent:target_not_open:MERGED\n")

        plan = self.run_plan(fixture="open_issue_330")

        self.assertEqual(self.harness_spawn_actions(plan), [])

    def test_harness_spawn_intent_keeps_retryable_blocked_reason(self) -> None:
        self.append_harness_spawn_intent(intent_id="retryable-intent")
        pending = self.repo / ".refactor-loop" / ".controller-pending-events.log"
        with pending.open("a", encoding="utf-8") as handle:
            handle.write("2026-05-31T00:00:01Z WAKEUP_RUNNER_BLOCKED:harness-spawn-intent:retryable-intent:target_not_open:unknown\n")

        plan = self.run_plan(fixture="open_issue_330")

        self.assertEqual([action["intent_id"] for action in self.harness_spawn_actions(plan)], ["retryable-intent"])

    def test_harness_spawn_intent_suppresses_when_open_managed_read_model_excludes_target(self) -> None:
        self.append_harness_spawn_intent(
            intent_id="closed-read-model-target",
            task_id="issue #330",
        )

        plan = self.run_plan(fixture="open_issue_331")

        self.assertEqual(self.harness_spawn_actions(plan), [])

    def test_harness_spawn_intent_suppresses_terminal_design_solver_target(self) -> None:
        self.append_harness_spawn_intent(
            intent_id="terminal-design-target",
            route="design_consensus_issue_intake",
            task_id="phase9-issue330-r1-minimal",
            log=".refactor-loop/logs/phase9-issue330-r1-minimal.log",
        )

        plan = self.run_plan(fixture="consensus_issue_330")

        self.assertEqual(self.harness_spawn_actions(plan), [])

    def test_harness_spawn_intent_suppresses_consensus_implementation_with_open_closing_pr(self) -> None:
        self.write_consensus_artifact()
        self.append_harness_spawn_intent(
            intent_id="dispatch-consensus-implementation:20",
            task_id="implement-issue-20",
            route="dispatch-consensus-implementation",
            log=".refactor-loop/logs/implement-issue-20.log",
        )

        plan = self.run_plan(fixture="closing_pr_issue20")

        actions = self.harness_spawn_actions(plan)
        self.assertEqual(1, len(actions))
        action = actions[0]
        self.assertTrue(action["status_only"])
        self.assertEqual("open_closing_pr", action["suppressed_reason"])
        self.assertEqual("spawn_codex_harness_background", action["controller_action"])
        self.assertNotIn("runner_authority", action)
        self.assertNotIn("no_generic_command", action)

    def test_harness_spawn_intent_keeps_open_consensus_implementation_dispatchable(self) -> None:
        self.write_consensus_artifact()
        self.append_harness_spawn_intent(
            intent_id="dispatch-consensus-implementation:20",
            task_id="implement-issue-20",
            route="dispatch-consensus-implementation",
            log=".refactor-loop/logs/implement-issue-20.log",
        )

        plan = self.run_plan(fixture="open_issue_20")

        actions = self.harness_spawn_actions(plan)
        self.assertEqual(1, len(actions))
        action = actions[0]
        self.assertNotIn("status_only", action)
        self.assertNotIn("suppressed_reason", action)
        self.assertEqual("spawn_codex_harness_background", action["controller_action"])
        self.assertEqual("dispatch-consensus-implementation:20", action["intent_id"])

    def test_harness_spawn_intent_suppresses_when_open_managed_read_model_is_empty(self) -> None:
        self.append_harness_spawn_intent(
            intent_id="empty-read-model-target",
            task_id="issue #330",
        )

        plan = self.run_plan()

        self.assertEqual(self.harness_spawn_actions(plan), [])

    def test_harness_spawn_intent_keeps_open_read_model_target(self) -> None:
        self.append_harness_spawn_intent(
            intent_id="open-read-model-target",
            task_id="issue #330",
        )

        plan = self.run_plan(fixture="open_issue_330")

        self.assertEqual([action["intent_id"] for action in self.harness_spawn_actions(plan)], ["open-read-model-target"])

    def test_harness_spawn_intent_keeps_target_when_read_model_load_fails(self) -> None:
        self.append_harness_spawn_intent(
            intent_id="read-model-failed-target",
            task_id="issue #330",
        )

        plan = self.run_plan(fixture="gh_failure")

        self.assertEqual([action["intent_id"] for action in self.harness_spawn_actions(plan)], ["read-model-failed-target"])

    def test_harness_spawn_intent_keeps_unresolved_target(self) -> None:
        self.append_harness_spawn_intent(
            intent_id="unresolved-target",
            task_id="custom-worker",
            reason="spawn worker for opaque target",
        )

        plan = self.run_plan(fixture="open_issue_331")

        self.assertEqual([action["intent_id"] for action in self.harness_spawn_actions(plan)], ["unresolved-target"])

    def test_wakeup_plan_uses_concurrency_monitor_for_spawn_intent_in_flight_detection(self) -> None:
        projection = wakeup_plan_projection()

        self.assertIn("_canonical_in_flight_for_log", projection.function_names)
        self.assertIn("list_in_flight_codex_lines", projection.attribute_names)
        self.assertNotIn("ps", projection.string_literals)
        self.assertNotIn("_spawn_codex_in_flight_for_log", projection.function_names)

    def test_harness_spawn_intent_target_extraction_owner_contract_is_anchored(self) -> None:
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        wakeup_source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py").read_text(encoding="utf-8")

        self.assertIn(
            "`scripts/codex_refactor_loop/wakeup_plan.py` | read-only stale-target extraction for `HARNESS_SPAWN_INTENT` fields `task_id`, `intent_id`, `source`, `route`, and `reason`",
            skill,
        )
        self.assertIn("`PR #<N>`, `issue #<N>`, `phase9-issue<N>-r<R>-<role>`, `review-pr<N>-<role>-r<R>`, and `fix-pr<N>-(r|round-)<R>`", skill)
        self.assertIn("role charset `[A-Za-z][A-Za-z0-9_-]*`", skill)
        self.assertIn("no canonical write authority", skill)
        self.assertIn("legacy free-text read only for stale closed/merged target suppression", skill)
        self.assertIn("unresolved targets fail open", skill)
        self.assertIn("test_wakeup_plan.py", skill)
        for token in (
            "HARNESS_SPAWN_TARGET_TEXT_PATTERNS",
            r"(?i)\bPR\s*#([1-9][0-9]*)\b",
            r"(?i)\bissue\s*#([1-9][0-9]*)\b",
            "phase9-",
            "review-",
            "fix-",
            '("task_id", "intent_id", "source", "route", "reason")',
            'TERMINAL_HARNESS_SPAWN_INTENT_BLOCKED_REASONS = {"target_not_open:CLOSED", "target_not_open:MERGED"}',
        ):
            with self.subTest(token=token):
                self.assertIn(token, wakeup_source)

    def test_completed_marker_routes_before_ci_red(self) -> None:
        self.write_completed_log("implement-issue20.log", "IMPLEMENT_DONE")

        plan = self.run_plan(fixture="ci_red_issue20")

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

    def test_completed_marker_accepts_sentinel_marker_before_completion_summary(self) -> None:
        log = self.logs / "phase9-issue449-r2-judge.log"
        log.write_text(
            "diff context\n"
            "⟦AI:AUTO-LOOP⟧\n"
            "`META_JUDGE_DONE:consensus:minimal:delete .refactor-loop host.env runtime fallback`\n"
            "tokens used\n"
            "1,234\n"
            "completion summary\n"
            "EXIT=0\n"
            "DONE_AT=2026-06-02T12:00:00Z\n",
            encoding="utf-8",
        )

        self.assertEqual(
            "META_JUDGE_DONE:consensus:minimal:delete .refactor-loop host.env runtime fallback",
            marker_from_completed_log(log),
        )

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

    def test_completed_marker_suppresses_closed_target_from_open_managed_read_model(self) -> None:
        self.write_completed_log("review-pr467-architect-r1.log", "REVIEW_DONE:467:architect:approve")

        plan = self.run_plan(fixture="open_issue_331")

        rendered = json.dumps(plan, sort_keys=True)
        self.assertNotIn("completed-marker:review-pr467-architect-r1.log", rendered)
        self.assertNotIn("REVIEW_DONE:467:architect:approve", rendered)

    def test_completed_marker_keeps_open_target_from_open_managed_read_model(self) -> None:
        self.write_completed_log("fix-pr77-r3.log", "FIX_DONE")

        plan = self.run_plan(fixture="open_pr_77")

        action = completed_marker_action(plan, "completed-marker:fix-pr77-r3")
        self.assertEqual(action["kind"], "completed-marker")
        self.assertEqual(action["target_kind"], "PR")
        self.assertEqual(action["target_number"], 77)
        self.assertFalse(action.get("status_only"))
        self.assertEqual(action["runner_authority"], "wakeup-runner-396")

    def test_implement_done_recovered_from_run_artifact_when_log_markerless(self) -> None:
        # An implement worker can exit clean (EXIT=0) but emit IMPLEMENT_DONE only
        # into its run artifact, not the log tail (codex stdout marker placement is
        # not reliable). The publish predicate must still detect completion via the
        # run artifact, mirroring the review verdict artifact-first pattern.
        log = self.logs / "implement-issue-421.log"
        log.write_text(
            "worker chatter, no standalone marker here\nEXIT=0\nDONE_AT=2026-06-03T19:06:35Z\n",
            encoding="utf-8",
        )
        runs = self.repo / ".refactor-loop" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        (runs / "implement-issue-421.md").write_text(
            "## summary\nimplemented\n\n## SCOPE_EXTEND 记录\n- none.\n\n⟦AI:AUTO-LOOP⟧\nIMPLEMENT_DONE:issue-421:ok\n",
            encoding="utf-8",
        )
        # log-only scan misses it (markerless log)
        self.assertIsNone(marker_from_completed_log(log))
        # completed_marker_actions recovers it via artifact fallback -> publish action
        actions = completed_marker_actions(self.repo)
        pub = [a for a in actions if a.get("marker") == "IMPLEMENT_DONE:issue-421:ok"]
        self.assertTrue(pub, "expected publish action recovered from implement run artifact")
        self.assertEqual(pub[0]["phase"], "publish")

    def test_implement_artifact_fallback_scoped_to_clean_exit_implement_logs(self) -> None:
        # Fallback is scoped: a non-implement log, or an implement log without
        # EXIT=0, must not pull an IMPLEMENT_DONE marker from any artifact.
        runs = self.repo / ".refactor-loop" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        (self.logs / "audit-iter-9.log").write_text("chatter\nEXIT=0\n", encoding="utf-8")
        (runs / "audit-iter-9.md").write_text("\nIMPLEMENT_DONE:issue-9:ok\n", encoding="utf-8")
        (self.logs / "implement-issue-777.log").write_text("crashed\nEXIT=1\n", encoding="utf-8")
        (runs / "implement-issue-777.md").write_text("\nIMPLEMENT_DONE:issue-777:ok\n", encoding="utf-8")
        actions = completed_marker_actions(self.repo)
        recovered = [a for a in actions if str(a.get("marker", "")).startswith("IMPLEMENT_DONE")]
        self.assertEqual(recovered, [], "scoped fallback must not fire for non-implement or unclean logs")

    def test_markerless_clean_implement_without_artifact_does_not_project_publish(self) -> None:
        self.write_markerless_clean_log("implement-issue-421.log")
        gh_items = [
            GhItem(
                "issue",
                421,
                "markerless implement",
                (label_catalog.MANAGED, label_catalog.PHASE_IMPLEMENTING, label_catalog.HUMAN_AUTO),
            )
        ]
        actions = completed_marker_actions(self.repo, open_targets={("issue", 421)}, gh_items=gh_items)

        self.assertFalse([a for a in actions if a.get("controller_action") == "publish_implementation_output"])

    def test_markerless_clean_implement_keeps_existing_marker_paths(self) -> None:
        self.write_completed_log("implement-issue-422.log", "IMPLEMENT_DONE:issue-422:ok")
        self.write_markerless_clean_log("implement-issue-423.log")
        self.write_run_artifact("implement-issue-423", "IMPLEMENT_DONE:issue-423:ok")
        self.write_implementation_pr_artifacts(issue=422, cluster="issue-422")
        self.write_implementation_pr_artifacts(issue=423, cluster="issue-423")

        actions = completed_marker_actions(
            self.repo,
            open_targets={("issue", 422), ("issue", 423)},
            gh_items=[
                GhItem(
                    "issue",
                    422,
                    "clean marker",
                    (label_catalog.MANAGED, label_catalog.PHASE_IMPLEMENTING, label_catalog.HUMAN_AUTO),
                ),
                GhItem(
                    "issue",
                    423,
                    "artifact marker",
                    (label_catalog.MANAGED, label_catalog.PHASE_IMPLEMENTING, label_catalog.HUMAN_AUTO),
                ),
            ],
        )
        markers = {a.get("marker") for a in actions}

        self.assertIn("IMPLEMENT_DONE:issue-422:ok:real", markers)
        self.assertIn("IMPLEMENT_DONE:issue-423:ok", markers)

    def test_markerless_clean_implement_with_artifact_still_requires_open_target(self) -> None:
        self.write_markerless_clean_log("implement-issue-421.log")
        self.write_run_artifact("implement-issue-421", "IMPLEMENT_DONE:issue-421:ok")
        not_open_actions = completed_marker_actions(
            self.repo,
            open_targets=set(),
            gh_items=[
                GhItem(
                    "issue",
                    421,
                    "closed markerless implement",
                    (label_catalog.MANAGED, label_catalog.PHASE_IMPLEMENTING, label_catalog.HUMAN_AUTO),
                )
            ],
        )
        not_managed_actions = completed_marker_actions(
            self.repo,
            open_targets=set(),
            gh_items=[
                GhItem(
                    "issue",
                    421,
                    "unmanaged markerless implement",
                    (label_catalog.PHASE_IMPLEMENTING, label_catalog.HUMAN_AUTO),
                )
            ],
        )

        self.assertFalse([a for a in not_open_actions if a.get("controller_action") == "publish_implementation_output"])
        self.assertFalse([a for a in not_managed_actions if a.get("controller_action") == "publish_implementation_output"])

    def test_wakeup_plan_source_regression_has_shared_reader_only_implement_marker_detection(self) -> None:
        source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py").read_text(encoding="utf-8")

        self.assertIn("read_worker_terminal_marker(log_path).marker", source)
        self.assertNotIn("_synthetic_markerless_implement_marker", source)
        self.assertNotIn('return f"IMPLEMENT_DONE:issue-{issue}:ok"', source)
        self.assertNotIn("_canonical_markerless_implement_has_output", source)

    def test_solver_done_recovered_from_run_artifact_when_log_markerless(self) -> None:
        log = self.write_markerless_clean_log("phase9-issue505-r1-minimal.log")
        self.write_run_artifact("phase9-issue505-r1-minimal", "SOLVER_DONE:minimal:artifact:summary")

        self.assertIsNone(marker_from_completed_log(log))
        actions = completed_marker_actions(self.repo, open_targets={("issue", 505)})
        recovered = [a for a in actions if a.get("marker") == "SOLVER_DONE:minimal:artifact:summary"]

        self.assertTrue(recovered, "expected completed-marker action recovered from solver run artifact")
        self.assertEqual(recovered[0]["phase"], "design-consensus")
        self.assertEqual(recovered[0]["target_kind"], "issue")
        self.assertEqual(recovered[0]["target_number"], 505)

    def test_judge_done_recovered_from_run_artifact_when_log_markerless(self) -> None:
        log = self.write_markerless_clean_log("phase9-issue505-r2-judge.log")
        self.write_run_artifact("phase9-issue505-r2-judge", "META_JUDGE_DONE:converge:round-3:artifact")

        self.assertIsNone(marker_from_completed_log(log))
        actions = completed_marker_actions(self.repo, open_targets={("issue", 505)})
        recovered = [a for a in actions if a.get("marker") == "META_JUDGE_DONE:converge:round-3:artifact"]

        self.assertTrue(recovered, "expected completed-marker action recovered from judge run artifact")
        self.assertEqual(recovered[0]["phase"], "design-consensus")

    def test_review_done_recovered_from_run_artifact_when_log_markerless(self) -> None:
        log = self.write_markerless_clean_log("review-pr480-quality-r3.log")
        self.write_run_artifact(
            "review-pr480-quality-r3",
            "---",
            "verdict: approve",
            "---",
            "head_sha: " + "a" * 40,
            "REVIEW_DONE:480:quality:approve",
        )

        self.assertIsNone(marker_from_completed_log(log))
        actions = completed_marker_actions(
            self.repo,
            open_targets={("PR", 480)},
            gh_items=[
                GhItem(
                    kind="PR",
                    number=480,
                    title="open PR",
                    labels=("crnd:lifecycle:managed", "crnd:phase:reviewing", "crnd:human:auto"),
                    head_ref="impl/pr480",
                    head_sha="a" * 40,
                )
            ],
        )
        recovered = [a for a in actions if a.get("marker") == "REVIEW_DONE:480:quality:approve"]

        self.assertTrue(recovered, "expected completed-marker action recovered from review run artifact")
        self.assertEqual(recovered[0]["controller_action"], "review_gate")
        self.assertEqual(recovered[0]["head_sha"], "a" * 40)

    def test_solver_judge_artifact_fallback_requires_clean_exit_and_artifact_marker(self) -> None:
        runs = self.repo / ".refactor-loop" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        (self.logs / "phase9-issue506-r1-minimal.log").write_text("crashed\nEXIT=1\n", encoding="utf-8")
        (runs / "phase9-issue506-r1-minimal.md").write_text("SOLVER_DONE:minimal:artifact:summary\n", encoding="utf-8")
        self.write_markerless_clean_log("phase9-issue507-r1-judge.log")
        self.write_run_artifact(
            "phase9-issue507-r1-judge",
            "body embeds META_JUDGE_DONE:converge:round-2:artifact but not standalone",
        )

        actions = completed_marker_actions(self.repo, open_targets={("issue", 506), ("issue", 507)})
        markers = {a.get("marker") for a in actions}

        self.assertNotIn("SOLVER_DONE:minimal:artifact:summary", markers)
        self.assertNotIn("META_JUDGE_DONE:converge:round-2:artifact", markers)

    def test_wakeup_plan_source_regression_uses_shared_worker_marker_reader(self) -> None:
        source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py").read_text(encoding="utf-8")

        for required in (
            "from codex_refactor_loop.worker_markers import",
            "read_worker_terminal_marker(log_path)",
            "marker.source == \"log\"",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)

    def test_stale_publish_implementation_marker_is_status_only_without_canonical_worktree(self) -> None:
        (self.logs / "implement-issue20.log").write_text(
            "IMPLEMENT_DONE:issue-20:ok\nEXIT=0\n",
            encoding="utf-8",
        )

        plan = self.run_plan(fixture="open_issue_20")

        action = next(item for item in plan["actions"] if item["action_id"].startswith("completed-marker:implement-issue20"))
        self.assertEqual(action["controller_action"], "publish_implementation_output")
        self.assertTrue(action["status_only"])
        self.assertEqual(action["suppressed_reason"], "implementation_worktree_missing")
        self.assertNotIn("runner_authority", action)
        self.assertNotIn("no_generic_command", action)

    def test_publish_implementation_marker_with_verified_local_head_remains_executable(self) -> None:
        (self.repo / ".worktrees" / "iter20-issue-20").mkdir(parents=True)
        self.write_implementation_pr_artifacts()
        (self.logs / "implement-issue20.log").write_text(
            "IMPLEMENT_DONE:issue-20:ok\nEXIT=0\n",
            encoding="utf-8",
        )

        plan = self.run_plan(fixture="local_iter_branch_issue20")

        action = next(item for item in plan["actions"] if item["action_id"].startswith("completed-marker:implement-issue20"))
        self.assertEqual(action["controller_action"], "publish_implementation_output")
        self.assertNotIn("status_only", action)
        self.assertEqual(action["head_ref"], "refactor/iter20-issue-20")
        self.assertEqual(Path(action["worktree"]).resolve(), (self.repo / ".worktrees/iter20-issue-20").resolve())
        self.assertEqual(action["runner_authority"], "wakeup-runner-396")
        self.assertEqual(action["title_file"], ".refactor-loop/runs/implementation-pr-issue-20-title.txt")
        self.assertEqual(action["body_file"], ".refactor-loop/runs/implementation-pr-issue-20-body.md")
        self.assertIn("canonical_implementation_identity", action["preconditions"])
        self.assertIn("fresh_integration_base", action["preconditions"])
        self.assertIn("worker_authored_pr_artifacts", action["preconditions"])
        self.assertIn("no_conflicting_open_implementation_pr", action["preconditions"])
        self.assertEqual(action["target_pr_number"], 320)
        self.assertNotIn("verified_pr_head", action["preconditions"])

    def test_publish_implementation_marker_without_pr_artifacts_is_status_only(self) -> None:
        (self.repo / ".worktrees" / "iter20-issue-20").mkdir(parents=True)
        (self.logs / "implement-issue20.log").write_text(
            "IMPLEMENT_DONE:issue-20:ok\nEXIT=0\n",
            encoding="utf-8",
        )

        plan = self.run_plan(fixture="local_iter_branch_issue20")

        action = next(item for item in plan["actions"] if item["action_id"].startswith("completed-marker:implement-issue20"))
        self.assertTrue(action["status_only"])
        self.assertEqual(action["suppressed_reason"], "implementation_pr_title_artifact_missing")
        self.assertNotIn("runner_authority", action)

    def test_publish_implementation_marker_with_malformed_pr_artifacts_is_status_only(self) -> None:
        worktree = self.repo / ".worktrees" / "iter20-issue-20"
        worktree.mkdir(parents=True)
        title, body = self.write_implementation_pr_artifacts()
        (self.logs / "implement-issue20.log").write_text(
            "IMPLEMENT_DONE:issue-20:ok\nEXIT=0\n",
            encoding="utf-8",
        )
        valid_body = body.read_text(encoding="utf-8")
        cases = (
            ("placeholder-title", lambda: title.write_text("实现 issue #20\n", encoding="utf-8"), "implementation_pr_title_placeholder"),
            ("multiline-title", lambda: title.write_text("完成 issue #20\n第二行\n", encoding="utf-8"), "implementation_pr_title_artifact_invalid"),
            ("body-content-title", lambda: title.write_text("Closes #20\n", encoding="utf-8"), "implementation_pr_title_contains_body_content"),
            ("sentinel-title", lambda: title.write_text("⟦AI:AUTO-LOOP⟧\n", encoding="utf-8"), "implementation_pr_title_contains_body_content"),
            ("missing-sentinel", lambda: body.write_text(valid_body.replace("\n⟦AI:AUTO-LOOP⟧\n", "\n"), encoding="utf-8"), "implementation_pr_body_sentinel_missing"),
            ("sentinel-not-final", lambda: body.write_text(valid_body + "extra\n", encoding="utf-8"), "implementation_pr_body_sentinel_missing"),
            ("wrong-closes", lambda: body.write_text(valid_body.replace("Closes #20", "Closes #21"), encoding="utf-8"), "implementation_pr_body_closes_mismatch"),
            ("multiple-closes", lambda: body.write_text(valid_body.replace("Closes #20", "Closes #20\nCloses #21"), encoding="utf-8"), "implementation_pr_body_closes_mismatch"),
            ("missing-closes", lambda: body.write_text(valid_body.replace("Closes #20\n\n", ""), encoding="utf-8"), "implementation_pr_body_closes_mismatch"),
            ("missing-section", lambda: body.write_text(valid_body.replace("## deviation 记录", "## deviation"), encoding="utf-8"), "implementation_pr_body_required_section_missing"),
            ("placeholder-body", lambda: body.write_text("## issue #20 实现\n\n## 修改文件\n\n- x\n\n## 测试结果\n\n- true\n\n## deviation 记录\n\n- none\n\nCloses #20\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8"), "implementation_pr_body_placeholder"),
        )
        for name, mutate, reason in cases:
            with self.subTest(name=name):
                self.write_implementation_pr_artifacts()
                mutate()
                plan = self.run_plan(fixture="local_iter_branch_issue20")
                action = next(item for item in plan["actions"] if item["action_id"].startswith("completed-marker:implement-issue20"))
                self.assertTrue(action["status_only"])
                self.assertEqual(action["suppressed_reason"], reason)
                self.assertNotIn("runner_authority", action)

    def test_publish_implementation_projection_suppresses_outside_pr_artifact_path(self) -> None:
        worktree = self.repo / ".worktrees" / "iter20-issue-20"
        worktree.mkdir(parents=True)
        title, body = self.write_implementation_pr_artifacts()
        (self.logs / "implement-issue20.log").write_text(
            "IMPLEMENT_DONE:issue-20:ok\nEXIT=0\n",
            encoding="utf-8",
        )
        outside = self.repo / "outside-title.txt"
        outside.write_text(title.read_text(encoding="utf-8"), encoding="utf-8")
        action = {
            "kind": "completed-marker",
            "action_id": "completed-marker:implement-issue20.log:IMPLEMENT_DONE:issue-20:ok",
            "controller_action": "publish_implementation_output",
            "target_kind": "issue",
            "target_number": 20,
            "source_artifact": ".refactor-loop/logs/implement-issue20.log",
            "source_marker": "IMPLEMENT_DONE:issue-20:ok",
            "head_ref": "refactor/iter20-issue-20",
            "title_file": str(outside),
            "body_file": body.relative_to(self.repo).as_posix(),
        }

        with mock.patch("codex_refactor_loop.wakeup_plan._worktrees_by_branch", return_value={"refactor/iter20-issue-20": worktree}):
            with mock.patch("codex_refactor_loop.wakeup_plan.classify_implement_attempt", return_value=mock.Mock(redispatch=False, in_flight=False)):
                suppress_stale_unexecutable_actions(
                    [action],
                    repo_root=self.repo,
                    gh_items=[
                        GhItem(
                            "issue",
                            20,
                            "open target",
                            (label_catalog.MANAGED, label_catalog.PHASE_IMPLEMENTING, label_catalog.HUMAN_AUTO),
                        )
                    ],
                    gh_items_loaded=True,
                )

        self.assertTrue(action["status_only"])
        self.assertEqual(action["suppressed_reason"], "implementation_pr_title_artifact_invalid_path")

    def test_publish_implementation_projection_suppresses_outside_pr_body_artifact_path(self) -> None:
        worktree = self.repo / ".worktrees" / "iter20-issue-20"
        worktree.mkdir(parents=True)
        title, body = self.write_implementation_pr_artifacts()
        (self.logs / "implement-issue20.log").write_text(
            "IMPLEMENT_DONE:issue-20:ok\nEXIT=0\n",
            encoding="utf-8",
        )
        outside = self.repo / "outside-body.md"
        outside.write_text(body.read_text(encoding="utf-8"), encoding="utf-8")
        action = {
            "kind": "completed-marker",
            "action_id": "completed-marker:implement-issue20.log:IMPLEMENT_DONE:issue-20:ok",
            "controller_action": "publish_implementation_output",
            "target_kind": "issue",
            "target_number": 20,
            "source_artifact": ".refactor-loop/logs/implement-issue20.log",
            "source_marker": "IMPLEMENT_DONE:issue-20:ok",
            "head_ref": "refactor/iter20-issue-20",
            "title_file": title.relative_to(self.repo).as_posix(),
            "body_file": str(outside),
        }

        with mock.patch("codex_refactor_loop.wakeup_plan._worktrees_by_branch", return_value={"refactor/iter20-issue-20": worktree}):
            with mock.patch("codex_refactor_loop.wakeup_plan.classify_implement_attempt", return_value=mock.Mock(redispatch=False, in_flight=False)):
                suppress_stale_unexecutable_actions(
                    [action],
                    repo_root=self.repo,
                    gh_items=[
                        GhItem(
                            "issue",
                            20,
                            "open target",
                            (label_catalog.MANAGED, label_catalog.PHASE_IMPLEMENTING, label_catalog.HUMAN_AUTO),
                        )
                    ],
                    gh_items_loaded=True,
                )

        self.assertTrue(action["status_only"])
        self.assertEqual(action["suppressed_reason"], "implementation_pr_body_artifact_invalid_path")

    def test_clean_implementation_marker_with_stale_base_stays_publishable_without_redispatch_churn(self) -> None:
        self.write_consensus_artifact()
        (self.repo / ".worktrees" / "iter20-issue-20").mkdir(parents=True)
        self.write_implementation_pr_artifacts()
        log = self.logs / "implement-issue20.log"
        log.write_text("IMPLEMENT_DONE:issue-20:ok\nEXIT=0\n", encoding="utf-8")

        plan = self.run_plan(fixture="local_iter_branch_issue20_stale_base")

        publish = next(item for item in plan["actions"] if str(item.get("action_id") or "").startswith("completed-marker:implement-issue20"))
        self.assertFalse(publish.get("status_only"))
        self.assertEqual(publish["controller_action"], "publish_implementation_output")
        self.assertEqual(publish["head_ref"], "refactor/iter20-issue-20")
        self.assertEqual(Path(publish["worktree"]).resolve(), (self.repo / ".worktrees/iter20-issue-20").resolve())
        self.assertEqual(publish["runner_authority"], "wakeup-runner-396")
        self.assertIn("no_conflicting_open_implementation_pr", publish["preconditions"])
        self.assertEqual(publish["target_pr_number"], 320)
        self.assertTrue(log.exists())
        self.assertFalse(
            any(
                item.get("controller_action") == "dispatch_consensus_implementation"
                and item.get("target_number") == 20
                and not item.get("status_only")
                for item in plan["actions"]
            )
        )

    def test_completed_marker_without_target_is_status_only_when_open_managed_read_model_is_loaded(self) -> None:
        self.write_completed_log("implement-worker.log", "IMPLEMENT_DONE")

        plan = self.run_plan(fixture="open_issue_331")

        action = next(item for item in plan["actions"] if item["action_id"].startswith("completed-marker:implement-worker"))
        self.assertEqual(action["kind"], "completed-marker")
        self.assertIsNone(action["target_kind"])
        self.assertIsNone(action["target_number"])
        self.assertTrue(action["status_only"])
        self.assertEqual(action["suppressed_reason"], "implementation_head_ref_missing")
        self.assertNotIn("runner_authority", action)

    def test_completed_marker_keeps_legacy_projection_without_open_managed_read_model(self) -> None:
        self.write_completed_log("review-pr467-architect-r1.log", "REVIEW_DONE:467:architect:approve")

        actions = completed_marker_actions(self.repo)

        action = next(item for item in actions if item["action_id"].startswith("completed-marker:review-pr467"))
        self.assertEqual(action["target_kind"], "PR")
        self.assertEqual(action["target_number"], 467)
        self.assertFalse(action.get("status_only"))

    def test_completed_marker_uses_repo_local_host_env_when_outer_locator_points_elsewhere(self) -> None:
        self.write_completed_log("review-pr468-architect-r1.log", "REVIEW_DONE:468:architect:approve")
        with tempfile.TemporaryDirectory(prefix="outer-host-env-") as other_raw:
            other = Path(other_raw)
            (other / ".config" / "consensus-rnd").mkdir(parents=True)
            outer_host_env = other / ".config" / "consensus-rnd" / "host.env"
            outer_host_env.write_text(
                f"REPO_ROOT={other}\nGH_REPO_SLUG=outer/repo\n",
                encoding="utf-8",
            )

            with mock.patch.dict(os.environ, {"CONSENSUS_RND_HOST_ENV": str(outer_host_env)}):
                actions = completed_marker_actions(self.repo)

        action = next(item for item in actions if item["action_id"].startswith("completed-marker:review-pr468"))
        self.assertEqual(action["target_kind"], "PR")
        self.assertEqual(action["target_number"], 468)
        self.assertFalse(action.get("status_only"))

    def test_completed_marker_keeps_latest_non_design_target_marker_only(self) -> None:
        self.write_completed_log("fix-pr77-r2.log", "FIX_DONE")
        self.write_completed_log("fix-pr77-r3.log", "FIX_DONE")
        self.set_log_mtime("fix-pr77-r2.log", 100.0)
        self.set_log_mtime("fix-pr77-r3.log", 200.0)

        plan = self.run_plan(fixture="open_pr_77")

        rendered = json.dumps(plan, sort_keys=True)
        self.assertNotIn("completed-marker:fix-pr77-r2.log", rendered)
        action = completed_marker_action(plan, "completed-marker:fix-pr77-r3")
        self.assertEqual(action["controller_action"], "dispatch_reviewers")
        self.assertEqual(action["target_kind"], "PR")
        self.assertEqual(action["target_number"], 77)

    def test_completed_marker_keeps_marker_without_target_when_latest_filter_cannot_key_it(self) -> None:
        self.write_completed_log("implement-worker-old.log", "IMPLEMENT_DONE")
        self.write_completed_log("implement-worker-new.log", "IMPLEMENT_DONE")

        actions = completed_marker_actions(self.repo)

        action_ids = {action["action_id"] for action in actions}
        self.assertTrue(any(action_id.startswith("completed-marker:implement-worker-old.log") for action_id in action_ids))
        self.assertTrue(any(action_id.startswith("completed-marker:implement-worker-new.log") for action_id in action_ids))

    def test_decompose_consensus_without_structured_apply_proof_visible_only_as_generic_completed_marker(self) -> None:
        self.write_completed_log("phase9-issue403-r6-judge.log", "META_JUDGE_DONE:consensus:decompose")

        plan = self.run_plan(fixture="open_issue_403")

        action = plan["actions"][0]
        self.assertEqual(action["kind"], "completed-marker")
        self.assertEqual(action["phase"], "design-consensus")
        self.assertEqual(action["actor"], "design-consensus-router-or-controller")
        self.assertEqual(action["marker"], "META_JUDGE_DONE:consensus:decompose:real")
        self.assertNotEqual(action.get("controller_action"), "apply_issue_decomposition_plan")
        self.assertNotIn("IssueDecompositionPlan", json.dumps(action))
        self.assertNotIn("decomposition-plan", json.dumps(action))

    def test_clean_plan_level_judge_artifact_emits_exact_issue_decomposition_named_action(self) -> None:
        plan_path, digest = self.write_issue_decomposition_artifacts()
        self.write_completed_log("phase9-issue403-r6-judge.log", "META_JUDGE_DONE:consensus:decompose")

        plan = self.run_plan(fixture="open_issue_403")

        action = plan["actions"][0]
        self.assertEqual(action["kind"], "completed-marker")
        self.assertEqual(action["controller_action"], "apply_issue_decomposition_plan")
        self.assertNotEqual(action.get("kind"), "issue-decomposition-apply")
        self.assertEqual(action["target_kind"], "issue")
        self.assertEqual(action["target_number"], 403)
        self.assertEqual(action["issue_decomposition_plan_path"], plan_path)
        self.assertEqual(action["issue_decomposition_plan_digest"], digest)
        self.assertIn(plan_path, action["issue_decomposition_proof"])
        self.assertIn(digest, action["issue_decomposition_proof"])
        self.assertEqual(
            action["preconditions"],
            [
                "active_controller_owner",
                "clean_exit_source_marker",
                "durable_consensus_artifact",
                "issue_decomposition_plan_digest_match",
                "live_parent_open_tracking",
                "github_sentinel_idempotency_owner",
            ],
        )
        rendered = json.dumps(action, sort_keys=True)
        for forbidden in (
            '"kind": "issue-decomposition-apply"',
            '"gh"',
            '"git"',
            '"cmd"',
            '"shell"',
            '"executor"',
            '"lifecycle_authority"',
            '"lifecycle_owner"',
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, rendered)

    def test_issue_decomposition_has_no_private_action_dialect_or_public_commands(self) -> None:
        source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py").read_text(encoding="utf-8")
        plan = self.run_plan()
        rendered = json.dumps(plan, sort_keys=True)

        self.assertEqual(plan["mode"], "closed-action-projection")
        self.assertTrue(plan["no_lifecycle_authority"])
        for token in (
            '"kind": "issue-decomposition-apply"',
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

        plan = self.run_plan(fixture="open_pr_123")

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

    def test_meta_resolved_drop_completed_marker_for_open_target_is_status_only(self) -> None:
        (self.logs / "issue53-judge-drop.log").write_text(
            "raw prose is diagnostic only\n"
            "META_RESOLVED:drop:no-action\n"
            "EXIT=0\n",
            encoding="utf-8",
        )

        plan = self.run_plan(fixture="open_issue_53")

        action = plan["actions"][0]
        self.assertEqual(action["kind"], "completed-marker")
        self.assertEqual(action["controller_action"], "close_managed_item_from_drop_marker")
        self.assertTrue(action["status_only"])
        self.assertTrue(action["no_lifecycle_authority"])
        self.assertEqual(action["suppressed_reason"], "live_open_target")
        self.assertNotIn("runner_authority", action)
        self.assertNotIn("no_generic_command", action)
        self.assertIn("clean_exit_source_marker", action["preconditions"])
        self.assertEqual(action["source_artifact"], ".refactor-loop/logs/issue53-judge-drop.log")
        self.assertEqual(action["source_marker"], "META_RESOLVED:drop:no-action")
        self.assertEqual(action["target_kind"], "issue")
        self.assertEqual(action["target_number"], 53)
        self.assertEqual(action["target"], {"kind": "issue", "number": 53})

    def test_meta_resolved_drop_completed_marker_for_closed_target_projects_close_helper(self) -> None:
        (self.logs / "issue53-judge-drop.log").write_text(
            "raw prose is diagnostic only\n"
            "META_RESOLVED:drop:no-action\n"
            "EXIT=0\n",
            encoding="utf-8",
        )

        plan = self.run_plan(fixture="open_issue_54")

        action = next(item for item in plan["actions"] if item["action_id"].startswith("completed-marker:issue53-judge-drop"))
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

    def test_non_drop_meta_resolved_is_status_only_for_phase9_router(self) -> None:
        self.write_completed_log("judge-issue54.log", "META_RESOLVED:continue")

        plan = self.run_plan(fixture="open_issue_54")

        action = plan["actions"][0]
        self.assertNotEqual(action["controller_action"], "dispatch_design_consensus")
        self.assertTrue(action["status_only"])
        self.assertTrue(action["no_lifecycle_authority"])

    def test_solver_triplet_completed_marker_is_status_only_for_phase9_router(self) -> None:
        for role in ("minimal", "structural", "delete"):
            self.write_completed_log(f"phase9-issue453-r1-{role}.log", f"SOLVER_DONE:{role}:ready")
            self.set_log_mtime(f"phase9-issue453-r1-{role}.log", {"delete": 100.0, "structural": 200.0, "minimal": 300.0}[role])

        plan = self.run_plan(fixture="open_issue_453")

        action = next(
            item for item in plan["actions"]
            if item["action_id"].startswith("completed-marker:phase9-issue453-r1-minimal")
        )
        self.assertEqual(action["kind"], "completed-marker")
        self.assertNotEqual(action["controller_action"], "dispatch_design_consensus")
        self.assertTrue(action["status_only"])
        self.assertTrue(action["no_lifecycle_authority"])
        self.assertNotIn("runner_authority", action)
        self.assertNotIn("no_generic_command", action)
        self.assertIn("clean_exit_source_marker", action["preconditions"])
        self.assertEqual(action["source_artifact"], ".refactor-loop/logs/phase9-issue453-r1-minimal.log")
        self.assertEqual(action["source_marker"], "SOLVER_DONE:minimal:ready:real")
        self.assertEqual(action["target_kind"], "issue")
        self.assertEqual(action["target_number"], 453)
        self.assertEqual(action["target"], {"kind": "issue", "number": 453})
        for forbidden in ("argv", "shell", "cmd", "command_line", "commands", "env", "git", "gh", "executor"):
            self.assertNotIn(forbidden, action)

    def test_design_consensus_completed_marker_keeps_latest_round_only(self) -> None:
        for role in ("minimal", "structural", "delete"):
            self.write_completed_log(f"phase9-issue453-r1-{role}.log", f"SOLVER_DONE:{role}:ready")
            self.set_log_mtime(f"phase9-issue453-r1-{role}.log", 100.0)
        self.write_completed_log("phase9-issue453-r2-judge.log", "META_JUDGE_DONE:continue")
        self.set_log_mtime("phase9-issue453-r2-judge.log", 200.0)
        self.write_completed_log("phase9-issue453-r3-minimal.log", "SOLVER_DONE:minimal:ready")
        self.write_completed_log("phase9-issue453-r3-structural.log", "SOLVER_DONE:structural:ready")
        self.set_log_mtime("phase9-issue453-r3-minimal.log", 300.0)
        self.set_log_mtime("phase9-issue453-r3-structural.log", 400.0)

        plan = self.run_plan(fixture="open_issue_453")

        rendered = json.dumps(plan, sort_keys=True)
        self.assertNotIn("phase9-issue453-r1-minimal.log", rendered)
        self.assertNotIn("phase9-issue453-r1-structural.log", rendered)
        self.assertNotIn("phase9-issue453-r1-delete.log", rendered)
        self.assertNotIn("phase9-issue453-r2-judge.log", rendered)
        self.assertNotIn("SOLVER_DONE:minimal:ready:real", rendered)
        action = next(
            item for item in plan["actions"]
            if item["action_id"].startswith("completed-marker:phase9-issue453-r3-structural")
        )
        self.assertNotEqual(action["controller_action"], "dispatch_design_consensus")
        self.assertTrue(action["status_only"])
        self.assertEqual(action["target_kind"], "issue")
        self.assertEqual(action["target_number"], 453)

    def test_design_consensus_latest_round_filter_stacks_with_closed_target_filter(self) -> None:
        self.write_completed_log("phase9-issue330-r1-minimal.log", "SOLVER_DONE:minimal:ready")
        self.write_completed_log("phase9-issue330-r2-judge.log", "META_JUDGE_DONE:continue")
        self.set_log_mtime("phase9-issue330-r1-minimal.log", 100.0)
        self.set_log_mtime("phase9-issue330-r2-judge.log", 200.0)

        plan = self.run_plan(fixture="open_issue_331")

        rendered = json.dumps(plan, sort_keys=True)
        self.assertNotIn("phase9-issue330-r1-minimal.log", rendered)
        self.assertNotIn("phase9-issue330-r2-judge.log", rendered)
        self.assertNotIn("dispatch_design_consensus", rendered)

    def test_solver_triplet_completed_marker_suppressed_for_terminal_design_target(self) -> None:
        for role in ("minimal", "structural", "delete"):
            self.write_completed_log(f"phase9-issue330-r1-{role}.log", f"SOLVER_DONE:{role}:ready")

        plan = self.run_plan(fixture="consensus_issue_330")

        rendered = json.dumps(plan, sort_keys=True)
        self.assertNotIn("dispatch_design_consensus", rendered)
        actions = [
            action
            for action in plan["actions"]
            if action.get("source_artifact", "").startswith(".refactor-loop/logs/phase9-issue330-r1-")
        ]
        self.assertTrue(actions)
        self.assertTrue(all(action.get("status_only") is True for action in actions))
        self.assertTrue(all("runner_authority" not in action for action in actions))

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
        (self.repo / ".refactor-loop" / "host.env").unlink(missing_ok=True)
        host_env = self.repo / ".config" / "consensus-rnd" / "host.env"
        host_env.parent.mkdir(parents=True, exist_ok=True)
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

        plan = self.run_plan(fixture="open_pr_77")

        action = completed_marker_action(plan, "completed-marker:fix-pr77-r3")
        self.assertEqual(action["kind"], "completed-marker")
        self.assertEqual(action["controller_action"], "dispatch_reviewers")
        self.assertEqual(action["runner_authority"], "wakeup-runner-396")
        self.assertTrue(action["no_generic_command"])
        self.assertNotIn("status_only", action)
        self.assertEqual(action["target_kind"], "PR")
        self.assertEqual(action["target_number"], 77)
        self.assertEqual(action["target"], {"kind": "PR", "number": 77})
        self.assertIn("clean_exit_source_marker", action["preconditions"])
        self.assertIn("review_thread_completion_evidence", action["preconditions"])
        for forbidden in ("argv", "shell", "cmd", "command_line", "commands", "env", "git", "gh", "executor"):
            self.assertNotIn(forbidden, action)

    def test_reviewing_pr_with_missing_reviewer_heads_projects_dispatch_reviewers(self) -> None:
        for role, verdict in (("architect", "approve"), ("tests", "approve"), ("quality", "comment")):
            (self.repo / ".refactor-loop" / "runs").mkdir(parents=True, exist_ok=True)
            (self.repo / ".refactor-loop" / "runs" / f"review-pr480-{role}-r1.md").write_text(
                f"---\nverdict: {verdict}\n---\nREVIEW_DONE:480:{role}:{verdict}\n",
                encoding="utf-8",
            )
            (self.logs / f"review-pr480-{role}-r1.log").write_text(
                f"REVIEW_DONE:480:{role}:{verdict}\nEXIT=0\n",
                encoding="utf-8",
            )

        plan = self.run_plan(fixture="open_pr_480")

        action = next(item for item in plan["actions"] if item["kind"] == "review-evidence-redispatch")
        self.assertEqual(action["controller_action"], "dispatch_reviewers")
        self.assertEqual(action["target_kind"], "PR")
        self.assertEqual(action["target_number"], 480)
        self.assertNotIn("head_sha", action)
        self.assertEqual(action["action_id"], "review-evidence-redispatch:480:" + "a" * 40)
        self.assertEqual(action["stale_review_roles"], ["architect", "tests", "quality"])
        self.assertIn("missing_or_stale_reviewer_head_evidence", action["preconditions"])
        self.assertEqual(action["runner_authority"], "wakeup-runner-396")
        self.assertTrue(action["no_generic_command"])
        self.assertNotIn("status_only", action)

    def test_reviewing_pr_with_stale_reviewer_head_projects_dispatch_reviewers(self) -> None:
        stale = "b" * 40
        for role, verdict in (("architect", "approve"), ("tests", "approve"), ("quality", "comment")):
            (self.repo / ".refactor-loop" / "runs").mkdir(parents=True, exist_ok=True)
            (self.repo / ".refactor-loop" / "runs" / f"review-pr480-{role}-r1.md").write_text(
                f"---\nhead_sha: {stale if role == 'architect' else 'a' * 40}\nverdict: {verdict}\n---\nREVIEW_DONE:480:{role}:{verdict}\n",
                encoding="utf-8",
            )
            (self.logs / f"review-pr480-{role}-r1.log").write_text(
                f"REVIEW_DONE:480:{role}:{verdict}\nEXIT=0\n",
                encoding="utf-8",
            )

        plan = self.run_plan(fixture="open_pr_480")

        action = next(item for item in plan["actions"] if item["kind"] == "review-evidence-redispatch")
        self.assertEqual(action["target_number"], 480)
        self.assertNotIn("head_sha", action)
        self.assertEqual(action["stale_review_roles"], ["architect"])
        self.assertNotIn("status_only", action)

    def test_review_redispatch_next_round_intent_projects_despite_stale_exit_zero_r1_log(self) -> None:
        stale = "b" * 40
        for role, verdict in (("architect", "approve"), ("tests", "approve"), ("quality", "comment")):
            (self.repo / ".refactor-loop" / "runs").mkdir(parents=True, exist_ok=True)
            (self.repo / ".refactor-loop" / "prompts").mkdir(parents=True, exist_ok=True)
            (self.repo / ".refactor-loop" / "runs" / f"review-pr480-{role}-r1.md").write_text(
                f"---\nhead_sha: {stale if role == 'architect' else 'a' * 40}\nverdict: {verdict}\n---\nREVIEW_DONE:480:{role}:{verdict}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop" / "prompts" / f"review-pr480-{role}-r1.md").write_text(
                f"head_sha: {stale if role == 'architect' else 'a' * 40}\n",
                encoding="utf-8",
            )
            (self.logs / f"review-pr480-{role}-r1.log").write_text(
                f"head_sha: {stale if role == 'architect' else 'a' * 40}\nREVIEW_DONE:480:{role}:{verdict}\nEXIT=0\n",
                encoding="utf-8",
            )
        self.append_harness_spawn_intent(
            intent_id="dispatch-reviewers:480:architect:r2",
            source="controller-actions",
            route="dispatch-reviewers",
            task_id="review-pr480-architect-r2",
            cd=str(self.repo.resolve()),
            prompt=".refactor-loop/prompts/review-pr480-architect-r2.md",
            log=".refactor-loop/logs/review-pr480-architect-r2.log",
            reason="review PR #480 as architect",
        )
        (self.repo / ".refactor-loop" / "prompts" / "review-pr480-architect-r2.md").write_text(
            f"head_sha: {'a' * 40}\n",
            encoding="utf-8",
        )

        plan = self.run_plan(fixture="open_pr_480", ps_count=0)

        spawn = next(item for item in plan["actions"] if item["kind"] == "harness-spawn-intent")
        self.assertEqual(spawn["intent_id"], "dispatch-reviewers:480:architect:r2")
        self.assertEqual(spawn["controller_action"], "spawn_codex_harness_background")
        self.assertEqual(spawn["cd"], str(self.repo.resolve()))
        self.assertEqual(spawn["log"], str((self.logs / "review-pr480-architect-r2.log").resolve()))
        self.assertNotEqual(spawn["log"], str((self.logs / "review-pr480-architect-r1.log").resolve()))

    def test_reviewing_pr_with_prompt_bound_valid_heads_does_not_redispatch_reviewers(self) -> None:
        for role, verdict in (("architect", "approve"), ("tests", "approve"), ("quality", "comment")):
            (self.repo / ".refactor-loop" / "runs").mkdir(parents=True, exist_ok=True)
            (self.repo / ".refactor-loop" / "prompts").mkdir(parents=True, exist_ok=True)
            (self.repo / ".refactor-loop" / "prompts" / f"review-pr480-{role}-r1.md").write_text(
                f"head_sha: {'a' * 40}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop" / "runs" / f"review-pr480-{role}-r1.md").write_text(
                f"---\nverdict: {verdict}\n---\nREVIEW_DONE:480:{role}:{verdict}\n",
                encoding="utf-8",
            )
            (self.logs / f"review-pr480-{role}-r1.log").write_text(
                f"REVIEW_DONE:480:{role}:{verdict}\nEXIT=0\n",
                encoding="utf-8",
            )

        plan = self.run_plan(fixture="open_pr_480")

        self.assertNotIn("review-evidence-redispatch", json.dumps(plan, sort_keys=True))

    def test_review_done_completed_marker_projects_prompt_bound_reviewed_head(self) -> None:
        for role, verdict in (("architect", "approve"), ("tests", "approve"), ("quality", "comment")):
            (self.repo / ".refactor-loop" / "runs").mkdir(parents=True, exist_ok=True)
            (self.repo / ".refactor-loop" / "prompts").mkdir(parents=True, exist_ok=True)
            (self.repo / ".refactor-loop" / "prompts" / f"review-pr480-{role}-r1.md").write_text(
                f"head_sha: {'a' * 40}\n",
                encoding="utf-8",
            )
            (self.repo / ".refactor-loop" / "runs" / f"review-pr480-{role}-r1.md").write_text(
                f"---\nverdict: {verdict}\n---\nREVIEW_DONE:480:{role}:{verdict}\n",
                encoding="utf-8",
            )
            (self.logs / f"review-pr480-{role}-r1.log").write_text(
                f"REVIEW_DONE:480:{role}:{verdict}\nEXIT=0\n",
                encoding="utf-8",
            )

        plan = self.run_plan(fixture="open_pr_480")

        action = next(item for item in plan["actions"] if item["controller_action"] == "review_gate")
        self.assertEqual(action["target_kind"], "PR")
        self.assertEqual(action["target_number"], 480)
        self.assertEqual(action["head_sha"], "a" * 40)
        self.assertNotIn("review-evidence-redispatch", json.dumps(plan, sort_keys=True))

    def test_fix_done_without_review_thread_artifact_ignores_unrelated_unresolved_threads(self) -> None:
        self.write_completed_log("fix-pr77-r3.log", "FIX_DONE")

        plan = self.run_plan(fixture="review_thread_unresolved")

        action = completed_marker_action(plan, "completed-marker:fix-pr77-r3")
        self.assertEqual(action["kind"], "completed-marker")
        self.assertEqual(action["controller_action"], "dispatch_reviewers")
        self.assertEqual(action["runner_authority"], "wakeup-runner-396")
        self.assertNotIn("status_only", action)

    def test_fix_done_with_unresolved_original_review_thread_blocks_dispatch_reviewers(self) -> None:
        completion_dir = self.repo / ".refactor-loop" / "state" / "review-thread-completion"
        completion_dir.mkdir(parents=True)
        (completion_dir / "pr77.json").write_text(
            json.dumps(
                {
                    "review_thread_driven": True,
                    "thread_id": "PRRT_kwDOExample",
                    "replied": True,
                    "resolved": True,
                }
            ),
            encoding="utf-8",
        )
        self.write_completed_log("fix-pr77-r3.log", "FIX_DONE")

        plan = self.run_plan(fixture="review_thread_unresolved")

        action = completed_marker_action(plan, "completed-marker:fix-pr77-r3")
        self.assertEqual(action["kind"], "completed-marker")
        self.assertEqual(action["route"], "review-thread-completion-gate")
        self.assertTrue(action["status_only"])
        self.assertTrue(action["no_lifecycle_authority"])
        self.assertIn("review_thread_completion_incomplete", action["blocked_reason"])
        self.assertIn("review_thread_completion_evidence", action["preconditions"])
        self.assertNotIn("controller_action", action)
        self.assertNotIn("runner_authority", action)
        self.assertNotIn("no_generic_command", action)

    def test_fix_done_review_thread_completion_artifact_allows_dispatch_reviewers(self) -> None:
        completion_dir = self.repo / ".refactor-loop" / "state" / "review-thread-completion"
        completion_dir.mkdir(parents=True)
        (completion_dir / "pr77.json").write_text(
            json.dumps(
                {
                    "review_thread_driven": True,
                    "thread_id": "PRRT_kwDOExample",
                    "replied": True,
                    "resolved": True,
                }
            ),
            encoding="utf-8",
        )
        self.write_completed_log("fix-pr77-r3.log", "FIX_DONE")

        plan = self.run_plan(fixture="review_thread_resolved")

        action = completed_marker_action(plan, "completed-marker:fix-pr77-r3")
        self.assertEqual(action["kind"], "completed-marker")
        self.assertEqual(action["controller_action"], "dispatch_reviewers")
        self.assertEqual(action["runner_authority"], "wakeup-runner-396")
        self.assertIn("review_thread_completion_evidence", action["preconditions"])
        self.assertNotIn("status_only", action)

    def test_fix_done_review_thread_completion_artifact_does_not_bypass_live_unresolved_thread(self) -> None:
        completion_dir = self.repo / ".refactor-loop" / "state" / "review-thread-completion"
        completion_dir.mkdir(parents=True)
        (completion_dir / "pr77.json").write_text(
            json.dumps(
                {
                    "review_thread_driven": True,
                    "thread_id": "PRRT_kwDOExample",
                    "replied": True,
                    "resolved": True,
                }
            ),
            encoding="utf-8",
        )
        self.write_completed_log("fix-pr77-r3.log", "FIX_DONE")

        plan = self.run_plan(fixture="review_thread_unresolved")

        action = completed_marker_action(plan, "completed-marker:fix-pr77-r3")
        self.assertEqual(action["route"], "review-thread-completion-gate")
        self.assertTrue(action["status_only"])
        self.assertNotIn("controller_action", action)

    def test_fix_done_explicit_escalation_allows_unresolved_review_thread(self) -> None:
        completion_dir = self.repo / ".refactor-loop" / "state" / "review-thread-completion"
        completion_dir.mkdir(parents=True)
        (self.logs / "judge-pr77-r1.log").write_text(
            "META_RESOLVED:escalate-human:conflicting-review-thread\nEXIT=0\n",
            encoding="utf-8",
        )
        (completion_dir / "pr77.json").write_text(
            json.dumps(
                {
                    "review_thread_driven": True,
                    "thread_id": "PRRT_kwDOExample",
                    "replied": False,
                    "resolved": False,
                    "escalation_evidence": "META_RESOLVED:escalate-human:conflicting-review-thread",
                }
            ),
            encoding="utf-8",
        )
        self.write_completed_log("fix-pr77-r3.log", "FIX_DONE")

        plan = self.run_plan(fixture="review_thread_unresolved")

        action = completed_marker_action(plan, "completed-marker:fix-pr77-r3")
        self.assertEqual(action["controller_action"], "dispatch_reviewers")
        self.assertEqual(action["runner_authority"], "wakeup-runner-396")
        self.assertNotIn("status_only", action)

    def test_fix_done_local_escalation_without_clean_marker_source_blocks_unresolved_review_thread(self) -> None:
        completion_dir = self.repo / ".refactor-loop" / "state" / "review-thread-completion"
        completion_dir.mkdir(parents=True)
        (completion_dir / "pr77.json").write_text(
            json.dumps(
                {
                    "review_thread_driven": True,
                    "thread_id": "PRRT_kwDOExample",
                    "replied": False,
                    "resolved": False,
                    "escalation_evidence": "META_RESOLVED:escalate-human:conflicting-review-thread",
                }
            ),
            encoding="utf-8",
        )
        self.write_completed_log("fix-pr77-r3.log", "FIX_DONE")

        plan = self.run_plan(fixture="review_thread_unresolved")

        action = completed_marker_action(plan, "completed-marker:fix-pr77-r3")
        self.assertEqual(action["route"], "review-thread-completion-gate")
        self.assertTrue(action["status_only"])
        self.assertNotIn("controller_action", action)

    def test_fix_done_blocks_when_original_review_thread_live_state_is_unknown(self) -> None:
        completion_dir = self.repo / ".refactor-loop" / "state" / "review-thread-completion"
        completion_dir.mkdir(parents=True)
        for fixture in (
            "review_thread_graphql_failure",
            "review_thread_malformed",
            "review_thread_pull_request_null",
            "review_thread_page_info_null",
            "review_thread_node_malformed",
        ):
            with self.subTest(fixture=fixture):
                self.logs.joinpath("fix-pr77-r3.log").unlink(missing_ok=True)
                (completion_dir / "pr77.json").write_text(
                    json.dumps(
                        {
                            "review_thread_driven": True,
                            "thread_id": "PRRT_kwDOExample",
                            "replied": True,
                            "resolved": True,
                        }
                    ),
                    encoding="utf-8",
                )
                self.write_completed_log("fix-pr77-r3.log", "FIX_DONE")

                plan = self.run_plan(fixture=fixture)

                action = completed_marker_action(plan, "completed-marker:fix-pr77-r3")
                self.assertEqual(action["route"], "review-thread-completion-gate")
                self.assertTrue(action["status_only"])
                self.assertNotIn("controller_action", action)

    def test_fix_done_blocks_unresolved_outdated_original_review_thread(self) -> None:
        completion_dir = self.repo / ".refactor-loop" / "state" / "review-thread-completion"
        completion_dir.mkdir(parents=True)
        (completion_dir / "pr77.json").write_text(
            json.dumps(
                {
                    "review_thread_driven": True,
                    "thread_id": "PRRT_kwDOExample",
                    "replied": True,
                    "resolved": True,
                }
            ),
            encoding="utf-8",
        )
        self.write_completed_log("fix-pr77-r3.log", "FIX_DONE")

        plan = self.run_plan(fixture="review_thread_unresolved_outdated")

        action = completed_marker_action(plan, "completed-marker:fix-pr77-r3")
        self.assertEqual(action["route"], "review-thread-completion-gate")
        self.assertTrue(action["status_only"])
        self.assertNotIn("controller_action", action)

    def test_fix_done_checks_paginated_original_review_thread_before_dispatch_reviewers(self) -> None:
        completion_dir = self.repo / ".refactor-loop" / "state" / "review-thread-completion"
        completion_dir.mkdir(parents=True)
        (completion_dir / "pr77.json").write_text(
            json.dumps(
                {
                    "review_thread_driven": True,
                    "thread_id": "PRRT_kwDOExample",
                    "replied": True,
                    "resolved": True,
                }
            ),
            encoding="utf-8",
        )
        self.write_completed_log("fix-pr77-r3.log", "FIX_DONE")

        plan = self.run_plan(fixture="review_thread_paginated_unresolved")

        action = completed_marker_action(plan, "completed-marker:fix-pr77-r3")
        self.assertEqual(action["route"], "review-thread-completion-gate")
        self.assertTrue(action["status_only"])
        self.assertNotIn("controller_action", action)
        query_log = (self.repo / "gh-query-labels.log").read_text(encoding="utf-8")
        self.assertIn("api graphql", query_log)
        self.assertIn("after=cursor1", query_log)

    def test_fix_done_blocks_when_original_review_thread_repo_slug_is_missing(self) -> None:
        completion_dir = self.repo / ".refactor-loop" / "state" / "review-thread-completion"
        completion_dir.mkdir(parents=True)
        (completion_dir / "pr77.json").write_text(
            json.dumps(
                {
                    "review_thread_driven": True,
                    "thread_id": "PRRT_kwDOExample",
                    "replied": True,
                    "resolved": True,
                }
            ),
            encoding="utf-8",
        )
        (self.repo / ".config" / "consensus-rnd" / "host.env").write_text(
            f"REPO_ROOT={self.repo}\nCODEX_FLOOR=5\n",
            encoding="utf-8",
        )
        self.write_completed_log("fix-pr77-r3.log", "FIX_DONE")

        plan = self.run_plan_with_env({"GH_REPO_SLUG": ""}, fixture="review_thread_unresolved")[0]

        action = completed_marker_action(plan, "completed-marker:fix-pr77-r3")
        self.assertEqual(action["route"], "review-thread-completion-gate")
        self.assertTrue(action["status_only"])
        self.assertNotIn("controller_action", action)

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
        (self.logs / "implement-issue20.log").write_text(
            "IMPLEMENT_DONE:issue-20:ok\nEXIT=0\n",
            encoding="utf-8",
        )
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
            "open_release_rollup_pr_from_action",
        ):
            with self.subTest(helper=helper):
                self.assertIn(helper, executable)
                self.assertNotIn("status_only", executable[helper])
                self.assertTrue(executable[helper]["no_generic_command"])
                for forbidden in ("argv", "shell", "cmd", "commands", "env", "git", "gh", "executor"):
                    self.assertNotIn(forbidden, executable[helper])
        publish_action = next(
            action for action in plan["actions"]
            if action.get("controller_action") == "publish_implementation_output"
        )
        self.assertTrue(publish_action["status_only"])
        self.assertEqual(publish_action["suppressed_reason"], "implementation_worktree_missing")
        self.assertNotIn("runner_authority", publish_action)
        consensus_action = executable["dispatch_consensus_implementation"]
        self.assertEqual(consensus_action["consensus_artifact"], artifact.relative_to(self.repo).as_posix())
        self.assertEqual(consensus_action["design_decision_path"], artifact.relative_to(self.repo).as_posix())
        self.assertEqual(consensus_action["consensus_issue"], 20)
        self.assertEqual(consensus_action["consensus_round"], 5)
        self.assertIn("wakeup_plan.py", consensus_action["scope_paths"])
        self.assertIn("wakeup_runner.py", consensus_action["scope_paths"])
        self.assertIn("durable_consensus_artifact", consensus_action["preconditions"])

    def test_release_rollup_missing_body_projects_body_worker_before_pr_helper(self) -> None:
        event = self.append_release_rollup_event()

        plan = self.run_plan(fixture="release_rollup")

        rollup_actions = [action for action in plan["actions"] if action["kind"] == "release-rollup-needed"]
        self.assertEqual(len(rollup_actions), 1)
        action = rollup_actions[0]
        self.assertEqual(action["action_id"], "release-rollup-body:integration-sha")
        self.assertEqual(action["actor"], "release-rollup-body")
        self.assertEqual(action["controller_action"], "spawn_codex_harness_background")
        self.assertEqual(action["capability"], "release-rollup-body")
        self.assertEqual(action["event"], event)
        self.assertEqual(action["body_file"], ".refactor-loop/runs/release-rollup-pr-body.md")
        self.assertEqual(action["target"], {"kind": "codex", "task_id": "release-rollup-body"})
        self.assertIn("target_body_absent", action["preconditions"])
        self.assertNotIn("argv", action)
        self.assertNotIn("gh", action)
        self.assertNotIn("open_release_rollup_pr_from_action", [item.get("controller_action") for item in rollup_actions])

    def test_release_rollup_projection_keeps_latest_event_per_integration_sha(self) -> None:
        self.append_release_rollup_event(reason="old", ahead_count=1)
        self.append_release_rollup_event(reason="latest", ahead_count=2)

        actions = release_rollup_actions(self.repo)

        self.assertEqual(1, len(actions))
        self.assertEqual(actions[0]["event"]["reason"], "latest")
        self.assertEqual(actions[0]["event"]["ahead_count"], 2)

    def test_release_rollup_projection_requires_current_remote_integration_sha_and_ahead(self) -> None:
        stale_cases = (
            "release_rollup_no_ahead",
            "release_rollup_moved",
            "release_rollup_same_sha",
        )
        for fixture in stale_cases:
            with self.subTest(fixture=fixture):
                (self.repo / ".refactor-loop" / ".controller-pending-events.log").write_text("", encoding="utf-8")
                self.append_release_rollup_event()

                plan = self.run_plan(fixture=fixture)

                self.assertFalse([action for action in plan["actions"] if action["kind"] == "release-rollup-needed"])

    def test_release_rollup_projection_fails_open_when_local_ref_probe_fails(self) -> None:
        self.append_release_rollup_event()

        plan = self.run_plan(fixture="release_rollup_refs_fail")

        actions = [action for action in plan["actions"] if action["kind"] == "release-rollup-needed"]
        self.assertEqual(1, len(actions))
        self.assertEqual(actions[0]["event"]["integration_sha"], "integration-sha")

    def test_consensus_marker_after_exit_zero_with_harness_done_at_projects_implementation(self) -> None:
        artifact = self.write_consensus_artifact(issue=449, round_no=2)
        self.write_completed_log("phase9-issue449-r2-judge.log", "META_JUDGE_DONE:consensus:minimal")
        with (self.logs / "phase9-issue449-r2-judge.log").open("a", encoding="utf-8") as handle:
            handle.write("DONE_AT=2026-06-02T12:00:00Z\n")

        plan = self.run_plan(fixture="open_issue_449")

        action = next(
            item for item in plan["actions"]
            if item.get("controller_action") == "dispatch_consensus_implementation"
        )
        self.assertEqual(action["consensus_artifact"], artifact.relative_to(self.repo).as_posix())
        self.assertEqual(action["consensus_issue"], 449)
        self.assertEqual(action["consensus_round"], 2)
        self.assertIn("durable_consensus_artifact", action["preconditions"])
        self.assertEqual(action["cluster_id"], "issue-449")
        self.assertIn("project implementation only from the consensus judge artifact", action["new_principle"])

    def test_consensus_implementation_readiness_suppresses_closed_issue(self) -> None:
        self.write_consensus_artifact()
        self.write_completed_log("phase9-issue20-r5-judge.log", "META_JUDGE_DONE:consensus:structural")

        plan = self.run_plan(fixture="closed_issue_20")

        actions = [
            item for item in plan["actions"]
            if item.get("controller_action") == "dispatch_consensus_implementation"
        ]
        self.assertEqual([], [item for item in actions if not item.get("status_only")])

    def test_consensus_implementation_readiness_suppresses_open_closing_pr(self) -> None:
        self.write_consensus_artifact()
        self.write_completed_log("phase9-issue20-r5-judge.log", "META_JUDGE_DONE:consensus:structural")

        plan = self.run_plan(fixture="closing_pr_issue20")

        action = next(
            item for item in plan["actions"]
            if item.get("controller_action") == "dispatch_consensus_implementation"
        )
        self.assertTrue(action["status_only"])
        self.assertEqual("open_closing_pr", action["suppressed_reason"])
        self.assertFalse(action["consensus_implementation_ready"])

    def test_consensus_implementation_readiness_suppresses_remote_iter_branch_only(self) -> None:
        self.write_consensus_artifact()
        self.write_completed_log("phase9-issue20-r5-judge.log", "META_JUDGE_DONE:consensus:structural")

        plan = self.run_plan(fixture="remote_iter_branch_issue20")

        action = next(
            item for item in plan["actions"]
            if item.get("controller_action") == "dispatch_consensus_implementation"
        )
        self.assertTrue(action["status_only"])
        self.assertEqual("remote_iter_branch", action["suppressed_reason"])

    def test_consensus_implementation_readiness_redispatches_markerless_local_attempt(self) -> None:
        self.write_consensus_artifact()
        self.write_completed_log("phase9-issue20-r5-judge.log", "META_JUDGE_DONE:consensus:structural")
        (self.repo / ".worktrees" / "iter20-issue-20").mkdir(parents=True)
        (self.logs / "implement-issue-20.log").write_text("old output\nEXIT=0\n", encoding="utf-8")

        plan = self.run_plan(fixture="open_issue_20")

        action = next(
            item for item in plan["actions"]
            if item.get("controller_action") == "dispatch_consensus_implementation"
        )
        self.assertNotIn("status_only", action)
        self.assertTrue(action["consensus_implementation_ready"])

    def test_consensus_implementation_readiness_does_not_suppress_stale_pending_intent(self) -> None:
        action = {
            "target_kind": "issue",
            "target_number": 20,
            "iteration": "20",
            "cluster_id": "issue-20",
        }
        self.append_harness_spawn_intent(
            intent_id="dispatch-consensus-implementation:20",
            task_id="implement-issue-20",
            route="dispatch-consensus-implementation",
            log=".refactor-loop/logs/implement-issue-20.log",
        )

        reason = consensus_implementation_suppressed_reason(action, self.repo, monitor=None)

        self.assertIsNone(reason)

    def test_consensus_implementation_readiness_suppresses_pending_intent_with_worktree(self) -> None:
        action = {
            "target_kind": "issue",
            "target_number": 20,
            "iteration": "20",
            "cluster_id": "issue-20",
        }
        (self.repo / ".worktrees" / "iter20-issue-20").mkdir(parents=True)
        self.append_harness_spawn_intent(
            intent_id="dispatch-consensus-implementation:20",
            task_id="implement-issue-20",
            route="dispatch-consensus-implementation",
            log=".refactor-loop/logs/implement-issue-20.log",
        )

        reason = consensus_implementation_suppressed_reason(action, self.repo, monitor=None)

        self.assertEqual("pending_implement_intent", reason)

    def test_consensus_implementation_readiness_suppresses_worktree_log_pending_and_in_flight(self) -> None:
        cases = (
            ("pending", "pending_implement_intent"),
            ("in-flight", "in_flight_implement"),
        )
        for name, reason in cases:
            with self.subTest(name=name):
                self.tmp.cleanup()
                self.setUp()
                self.write_consensus_artifact()
                self.write_completed_log("phase9-issue20-r5-judge.log", "META_JUDGE_DONE:consensus:structural")
                env_updates: dict[str, str] = {}
                if name == "worktree":
                    (self.repo / ".worktrees" / "iter20-issue-20").mkdir(parents=True)
                elif name == "log":
                    (self.logs / "implement-issue-20.log").write_text("", encoding="utf-8")
                elif name == "pending":
                    (self.repo / ".worktrees" / "iter20-issue-20").mkdir(parents=True)
                    self.append_harness_spawn_intent(
                        intent_id="dispatch-consensus-implementation:20",
                        task_id="implement-issue-20",
                        route="dispatch-consensus-implementation",
                        log=".refactor-loop/logs/implement-issue-20.log",
                    )
                elif name == "in-flight":
                    env_updates["WAKEUP_PLAN_PS_EXTRA"] = (
                        f"python3 /skill/consensus-rnd-cli spawn-codex --cd {self.repo}/.worktrees/iter20-issue-20 "
                        f"--log {self.repo}/.refactor-loop/logs/implement-issue-20.log"
                    )

                plan, _stdout = self.run_plan_with_env(env_updates, fixture="open_issue_20")

                action = next(
                    item for item in plan["actions"]
                    if item.get("controller_action") == "dispatch_consensus_implementation"
                )
                self.assertTrue(action["status_only"])
                self.assertEqual(reason, action["suppressed_reason"])

    def test_consensus_implementation_readiness_in_flight_fails_open_without_monitor(self) -> None:
        action = {
            "target_kind": "issue",
            "target_number": 20,
            "iteration": "20",
            "cluster_id": "issue-20",
        }

        reason = consensus_implementation_suppressed_reason(action, self.repo, monitor=None)

        self.assertIsNone(reason)

    def test_consensus_implementation_readiness_fresh_open_issue_dispatches_once(self) -> None:
        self.write_consensus_artifact()
        self.write_completed_log("phase9-issue20-r5-judge.log", "META_JUDGE_DONE:consensus:structural")

        plan = self.run_plan(fixture="open_issue_20")

        actions = [
            item for item in plan["actions"]
            if item.get("controller_action") == "dispatch_consensus_implementation"
            and not item.get("status_only")
        ]
        self.assertEqual(1, len(actions))
        self.assertTrue(actions[0]["consensus_implementation_ready"])
        self.assertIn("consensus_implementation_ready", actions[0]["preconditions"])
        self.assertNotIn("suppressed_reason", actions[0])

    def test_consensus_implementation_scope_conflicts_serialize_overlapping_dispatches(self) -> None:
        self.write_consensus_artifact(
            issue=330,
            round_no=1,
            scope_paths=(
                "- skills/codex-refactor-loop/scripts/codex_refactor_loop/wakeup_plan.py\n"
                "- skills/codex-refactor-loop/scripts/test_wakeup_plan.py"
            ),
        )
        self.write_completed_log("phase9-issue330-r1-judge.log", "META_JUDGE_DONE:consensus:structural")
        self.write_consensus_artifact(
            issue=331,
            round_no=1,
            scope_paths="skills/codex-refactor-loop/scripts/codex_refactor_loop/wakeup_plan.py",
        )
        self.write_completed_log("phase9-issue331-r1-judge.log", "META_JUDGE_DONE:consensus:structural")
        self.write_consensus_artifact(
            issue=332,
            round_no=1,
            scope_paths="skills/codex-refactor-loop/scripts/codex_refactor_loop/controller_actions.py",
        )
        self.write_completed_log("phase9-issue332-r1-judge.log", "META_JUDGE_DONE:consensus:structural")

        plan = self.run_plan(fixture="open_issues_330_331_332")

        actions = [
            item for item in plan["actions"]
            if item.get("controller_action") == "dispatch_consensus_implementation"
        ]
        by_issue = {item["target_number"]: item for item in actions}
        executable = {item["target_number"] for item in actions if not item.get("status_only")}
        waiting = [item for item in actions if item.get("suppressed_reason") == "scope_conflict_waiting"]
        self.assertEqual(1, len(waiting))
        self.assertEqual({332}, executable - {330, 331})
        self.assertEqual(1, len(executable.intersection({330, 331})))
        self.assertIn(waiting[0]["target_number"], {330, 331})
        self.assertFalse(waiting[0]["consensus_implementation_ready"])
        self.assertNotIn("runner_authority", waiting[0])
        self.assertFalse(by_issue[332].get("status_only"))

    def test_consensus_projection_accepts_verdict_consensus_frontmatter(self) -> None:
        artifact = self.write_consensus_artifact(issue=451, round_no=3, frontmatter="verdict: consensus")
        self.write_completed_log("phase9-issue451-r3-judge.log", "META_JUDGE_DONE:consensus:structural")

        actions = completed_marker_actions(self.repo)

        action = next(
            item for item in actions
            if item.get("controller_action") == "dispatch_consensus_implementation"
        )
        self.assertEqual(artifact.relative_to(self.repo).as_posix(), action["consensus_artifact"])
        self.assertEqual("issue-451", action["cluster_id"])
        self.assertFalse(action.get("status_only"))

    def test_consensus_projection_allows_empty_optional_verification_hints(self) -> None:
        self.write_consensus_artifact(issue=452, round_no=3, verification_hints="")
        self.write_completed_log("phase9-issue452-r3-judge.log", "META_JUDGE_DONE:consensus:structural")

        actions = completed_marker_actions(self.repo)

        action = next(
            item for item in actions
            if item.get("controller_action") == "dispatch_consensus_implementation"
        )
        self.assertEqual("", action["verification_hints"])
        self.assertIn("wakeup_plan.py", action["scope_paths"])
        self.assertFalse(action.get("status_only"))

    def test_consensus_completed_marker_without_durable_artifact_is_not_executable(self) -> None:
        self.write_completed_log("phase9-issue20-r5-judge.log", "META_JUDGE_DONE:consensus:structural")

        plan = self.run_plan(fixture="open_issue_20")

        action = next(item for item in plan["actions"] if item["action_id"].startswith("completed-marker:phase9-issue20"))
        self.assertTrue(action["status_only"])
        self.assertNotIn("runner_authority", action)
        self.assertNotIn("consensus_artifact", action)

    def test_consensus_projection_fail_closed_for_invalid_judge_artifact_shapes(self) -> None:
        cases = (
            ("missing-scope", {"scope_paths": ""}),
            ("missing-old-pattern", {"old_pattern": None}),
            ("missing-new-principle", {"new_principle": None}),
            ("missing-owner", {"include_owner": False}),
            ("missing-if-consensus", {"include_if_consensus": False}),
            ("bad-design-path", {"design_decision_path": ".refactor-loop/runs/other.md"}),
            ("non-consensus-frontmatter", {"frontmatter": "decision: converge"}),
            ("missing-marker", {"marker": "META_JUDGE_DONE:converge:round-3"}),
        )
        for name, kwargs in cases:
            with self.subTest(name=name):
                self.write_consensus_artifact(issue=330, round_no=4, **kwargs)
                self.write_completed_log("phase9-issue330-r4-judge.log", "META_JUDGE_DONE:consensus:structural")

                plan = self.run_plan(fixture="open_issue_330")

                projected = [
                    action for action in plan["actions"]
                    if action.get("controller_action") == "dispatch_consensus_implementation"
                    and not action.get("status_only")
                ]
                self.assertEqual([], projected)

    def test_consensus_projection_rejects_log_artifact_identity_mismatch(self) -> None:
        self.write_consensus_artifact(issue=330, round_no=4)
        log = self.logs / "phase9-issue330-r5-judge.log"
        log.write_text("META_JUDGE_DONE:consensus:structural\nEXIT=0\n", encoding="utf-8")

        fields = consensus_implementation_fields(self.repo, log, "issue #330")

        self.assertEqual({}, fields)

    def test_consensus_projection_does_not_read_solver_artifact_fallback(self) -> None:
        self.write_consensus_artifact(issue=330, round_no=4, scope_paths="")
        solver = self.repo / ".refactor-loop/runs/phase9-issue330-r4-structural.md"
        solver.write_text(
            "scope_paths:\n- skills/codex-refactor-loop/scripts/codex_refactor_loop/wakeup_plan.py\n",
            encoding="utf-8",
        )
        self.write_completed_log("phase9-issue330-r4-judge.log", "META_JUDGE_DONE:consensus:structural")

        actions = completed_marker_actions(self.repo)

        projected = [
            action for action in actions
            if action.get("controller_action") == "dispatch_consensus_implementation"
            and not action.get("status_only")
        ]
        self.assertEqual([], projected)

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

    def test_milestone_implementation_issue_does_not_project_unstructured_consensus_artifact(self) -> None:
        self.write_consensus_artifact(old_pattern=None)

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

        projected = [item for item in actions if item.get("kind") == "consensus-implementation-ready"]
        self.assertEqual([], projected)

    def test_wakeup_plan_source_locks_consensus_projection_to_judge_artifact_only(self) -> None:
        projection = wakeup_plan_projection()
        for required in (
            "CONSENSUS_JUDGE_LOG_RE",
            "CONSENSUS_JUDGE_ARTIFACT_RE",
            "_frontmatter_is_consensus",
            "_extract_implementation_owner",
            "_extract_structured_consensus_field",
            "_consensus_projection_from_artifact",
        ):
            with self.subTest(required=required):
                self.assertIn(required, projection.assigned_names | projection.function_names)
        self.assertNotIn("_extract_solver_scope_paths", projection.function_names)
        self.assertNotIn("phase9-issue{issue_match.group(1)}-r{issue_match.group(2)}-{role}.md", projection.string_literals)

    def test_wakeup_plan_source_locks_consensus_implementation_scope_conflict_serialization(self) -> None:
        projection = wakeup_plan_projection()
        for required in (
            "serialize_conflicting_consensus_implementation_actions",
            "_normalized_consensus_scope_paths",
            "_scope_paths_overlap",
        ):
            with self.subTest(required=required):
                self.assertIn(required, projection.function_names)
        self.assertIn("scope_conflict_waiting", projection.string_literals)

    def test_wakeup_plan_source_locks_named_g1_g3_helper_allowlist(self) -> None:
        projection = wakeup_plan_projection()
        self.assertGreaterEqual(
            projection.set_members["RUNNER_NAMED_HELPER_ACTIONS"],
            {
                "dispatch_consensus_implementation",
                "publish_implementation_output",
                "dispatch_reviewers",
                "open_release_rollup_pr_from_action",
            },
        )
        self.assertNotIn("HeadlessLifecycleAction", projection.class_names)
        self.assertNotIn("headless_actions", projection.assigned_names | projection.function_names)

    def test_wakeup_plan_source_locks_reviewer_head_redispatch_contract(self) -> None:
        projection = wakeup_plan_projection()
        for token in ("dispatch_reviewers", "missing_or_stale_reviewer_head_evidence", "review-evidence-redispatch"):
            with self.subTest(token=token):
                self.assertIn(token, projection.string_literals)
        snapshot_source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "managed_work_snapshot.py").read_text(encoding="utf-8")
        for token in ("gh\",", "api\",", "graphql", "body", "headRefName", "headRefOid"):
            with self.subTest(snapshot_token=token):
                self.assertIn(token, snapshot_source)
        caller_source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py").read_text(encoding="utf-8")
        self.assertNotIn("issue list", caller_source)
        self.assertNotIn("pr list", caller_source)
        self.assertIn("review_evidence_redispatch_actions", projection.function_names)
        self.assertIn("review-evidence-redispatch", projection.set_members["EXECUTABLE_ACTION_KINDS"])
        source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "wakeup_plan.py").read_text(encoding="utf-8")
        function_source = source[
            source.index("def review_evidence_redispatch_actions") : source.index("\ndef phase_from_marker", source.index("def review_evidence_redispatch_actions"))
        ]
        self.assertIn('"action_id": f"review-evidence-redispatch:{item.number}:{item.head_sha}"', function_source)
        self.assertNotIn('"head_sha"', function_source)

    def test_wakeup_plan_source_locks_stale_unexecutable_status_only_suppression(self) -> None:
        projection = wakeup_plan_projection()
        for token in (
            "publish_implementation_output",
            "close_managed_item_from_drop_marker",
            "implementation_worktree_missing",
            "implementation_head_ref_missing",
            "no_conflicting_open_implementation_pr",
            "status_only",
        ):
            with self.subTest(token=token):
                self.assertIn(token, projection.string_literals)
        self.assertIn("suppress_stale_unexecutable_actions", projection.function_names)

    def test_wakeup_plan_source_locks_clean_ok_stale_base_publish_recovery_not_redispatch(self) -> None:
        projection = wakeup_plan_projection()
        self.assertIn("_publish_recoverable_stale_base_implement", projection.function_names)
        self.assertIn("stale_base", projection.string_literals)
        self.assertIn("publish_ready", projection.string_literals)

    def test_wakeup_plan_source_locks_terminal_design_consensus_gate(self) -> None:
        projection = wakeup_plan_projection()

        for token in (
            "PHASE_CONSENSUS_REACHED",
            "PHASE_IMPLEMENTING",
            "PHASE_MERGED",
            "PHASE_CLOSED",
        ):
            with self.subTest(token=token):
                self.assertIn(token, projection.attribute_names)
        for token in (
            "DESIGN_CONSENSUS_TERMINAL_PHASES",
            "_terminal_design_consensus_targets",
            "_is_design_consensus_solver_dispatch_intent",
            "_design_consensus_marker_is_router_owned",
        ):
            with self.subTest(token=token):
                self.assertIn(token, projection.assigned_names | projection.function_names | projection.string_literals)
        self.assertIn("status_only", projection.string_literals)
        for forbidden in ("gh issue edit", "gh issue close", "gh pr merge", "git push", "git commit"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, projection.string_literals)

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
        projection = wakeup_plan_projection()
        self.assertIn("PrChecksProjection", projection.imported_names)
        self.assertNotIn("pr", projection.set_members.get("LEGACY_PR_CHECKS_COMMAND", frozenset()))
        self.assertNotIn("checks", projection.set_members.get("LEGACY_PR_CHECKS_COMMAND", frozenset()))

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
        projection = wakeup_plan_projection()

        self.assertNotIn("no-gap-violation", projection.set_members["EXECUTABLE_ACTION_KINDS"])
        self.assertNotIn("existing-issue", projection.set_members["EXECUTABLE_ACTION_KINDS"])
        self.assertNotIn("dispatch_next_step_worker", projection.set_members["RUNNER_NAMED_HELPER_ACTIONS"])

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

    def test_release_countdown_fail_soft_when_version_manifest_is_absent_with_open_milestone(self) -> None:
        (self.repo / ".version-bump.json").unlink()

        plan = self.run_plan(fixture="default_milestones")

        actions = [action for action in plan["actions"] if action["kind"] == "release-countdown"]
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action["activation"], "default-goal")
        self.assertEqual(action["goal"]["milestone"], {"number": 1, "title": "Soon", "due_on": "2026-06-15T00:00:00Z"})
        self.assertIsNone(action["goal"]["release"])
        self.assertTrue(action["status_only"])
        self.assertTrue(action["no_lifecycle_authority"])
        self.assertFalse((self.repo / ".refactor-loop/state/release-decision.json").exists())
        self.assertFalse((self.repo / ".refactor-loop/state/release-candidate.json").exists())

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

    def test_repository_stalled_meta_reflector_projects_single_spawn_only_action(self) -> None:
        plan = self.run_plan(fixture="repository_stalled")

        actions = [action for action in plan["actions"] if action["kind"] == "repository-stalled-meta-reflector"]
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action["controller_action"], "spawn_codex_harness_background")
        self.assertEqual(action["runner_authority"], "wakeup-runner-396")
        self.assertTrue(action["no_lifecycle_authority"])
        self.assertTrue(action["no_generic_command"])
        self.assertEqual(action["source_artifact"], "github-open-managed-items")
        self.assertEqual(action["source_marker"], "meta-escalation-long-stuck:24")
        self.assertEqual(action["threshold_hours"], "24")
        self.assertEqual(action["stale_revival_hours"], "3")
        self.assertTrue(action["run_in_background_required"])
        self.assertEqual(Path(action["prompt"]).name, "meta-reflector-repository-stalled.md")
        self.assertEqual(Path(action["log"]).name, "meta-reflector-repository-stalled.log")
        self.assertEqual(action["target"], {"kind": "codex", "task_id": "meta-reflector-repository-stalled"})
        self.assertEqual(action["preconditions"], ["active_controller_owner", "live_open_targets", "long_stuck_threshold_exceeded", "recommendation_only"])
        self.assertEqual([item["number"] for item in action["stalled_items"]], [506, 507, 536])
        pr_item = action["stalled_items"][2]
        self.assertEqual(pr_item["kind"], "PR")
        self.assertEqual(pr_item["number"], 536)
        self.assertEqual(pr_item["title"], "old review PR")
        self.assertEqual(pr_item["phase"], "review-gate")
        rendered = json.dumps(action, sort_keys=True)
        for forbidden in (
            "IssueDecompositionPlan",
            "apply_issue_decomposition_plan",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, rendered)
        for forbidden_key in (
            "lifecycle_authority",
            "lifecycle_owner",
            "argv",
            "shell",
            "commands",
            "executor",
            "gh",
            "git",
        ):
            with self.subTest(forbidden_key=forbidden_key):
                self.assertNotIn(f'"{forbidden_key}"', rendered)
        for forbidden_key in ("cmd", "env"):
            with self.subTest(forbidden_key=forbidden_key):
                self.assertNotIn(f'"{forbidden_key}"', rendered)
        self.assertTrue(has_dispatchable_action([action]))

    def test_repository_stalled_meta_reflector_suppresses_fresh_human_and_duplicate_pending(self) -> None:
        fresh = self.run_plan(fixture="repository_fresh")
        self.assertEqual([action for action in fresh["actions"] if action["kind"] == "repository-stalled-meta-reflector"], [])

        human = self.run_plan(fixture="repository_human_decision")
        self.assertEqual([action for action in human["actions"] if action["kind"] == "repository-stalled-meta-reflector"], [])

        pending = self.repo / ".refactor-loop" / ".controller-pending-events.log"
        pending.write_text("repository-stalled-meta-reflector already queued\n", encoding="utf-8")
        duplicate = self.run_plan(fixture="repository_stalled")
        self.assertEqual([action for action in duplicate["actions"] if action["kind"] == "repository-stalled-meta-reflector"], [])

    def test_repository_stalled_meta_reflector_waits_for_specific_executable_action(self) -> None:
        self.append_harness_spawn_intent(intent_id="specific-work", task_id="issue #506")

        plan = self.run_plan(fixture="repository_stalled")

        self.assertEqual([action for action in plan["actions"] if action["kind"] == "repository-stalled-meta-reflector"], [])
        self.assertEqual([action["intent_id"] for action in self.harness_spawn_actions(plan)], ["specific-work"])

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

    def test_load_github_items_queries_canonical_managed_label_once(self) -> None:
        plan = self.run_plan(fixture="managed_canonical")

        existing_items = [action["item"] for action in plan["actions"] if action["kind"] == "existing-issue"]
        self.assertEqual(existing_items, ["issue #81", "issue #82", "PR #91", "PR #92"])
        query_log = (self.repo / "gh-query-labels.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(query_log, ["api milestones"])
        snapshot = json.loads((self.repo / ".refactor-loop/state/managed-work-snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(len(snapshot["items"]), 4)

    def test_positive_open_managed_snapshot_fixtures_use_only_canonical_loop_labels(self) -> None:
        positive_fixtures = (
            "managed_canonical",
            "milestone",
            "existing",
            "transition_sort",
            "many_active",
            "non_action_statuses",
            "unpushed",
            "unpushed_fetch_fail",
            "unpushed_no_ahead",
            "unpushed_no_remote",
            "unpushed_no_worktree",
            "unpushed_head_dash",
            "unpushed_head_space",
            "unpushed_head_control",
            "ci_red",
            "ci_red_issue20",
        )

        for fixture in positive_fixtures:
            with self.subTest(fixture=fixture):
                rows = self.managed_work_snapshot_items(fixture)
                self.assertGreater(len(rows), 0)
                for row in rows:
                    labels = tuple(str(label) for label in row["labels"])
                    residue = [label for label in labels if not label_catalog.is_loop_owned(label)]
                    self.assertEqual(residue, [], f"{fixture} uses historical residue labels in a positive open-managed fixture")
                    self.assertIn(label_catalog.MANAGED, labels)
                    valid, errors = label_catalog.validate_exactly_one_phase_human(labels)
                    self.assertTrue(valid, f"{fixture} has invalid canonical labels for {row['kind']} #{row['number']}: {errors}")

    def test_load_github_items_logs_unavailable_managed_work_snapshot(self) -> None:
        snapshot = ManagedWorkSnapshotResult((), False, "unavailable", "graphql-headroom-low", 901)
        output = StringIO()
        with mock.patch.dict(os.environ, {"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}):
            with mock.patch("codex_refactor_loop.wakeup_plan.load_open_managed_work_snapshot", return_value=snapshot):
                with redirect_stderr(output):
                    items, loaded_ok = load_github_items_with_status(self.repo)

        self.assertEqual(items, [])
        self.assertFalse(loaded_ok)
        self.assertIn(
            "managed-work-snapshot-unavailable caller=wakeup-plan.load-github-items reason=graphql-headroom-low "
            "source=unavailable age_seconds=901 items=0 target=projection-open-managed",
            output.getvalue(),
        )

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

        self.assertIn("load_open_managed_work_snapshot(ctx)", source)
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

        plan, stdout = self.run_plan_with_stdout(fixture="open_issue_330", ps_count=0, active_audit=True)

        self.assertTrue(any(action["kind"] == "harness-spawn-intent" for action in plan["actions"]))
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

        plan = self.run_plan(fixture="ci_red_issue20")
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
        (self.repo / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.repo / ".config" / "consensus-rnd" / "host.env").write_text(
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
        (self.repo / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.repo / ".config" / "consensus-rnd" / "host.env").write_text(
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

        plan = self.run_plan(fixture="open_issue_330")

        self.assertEqual(plan["schema"], "wakeup-plan")
        self.assertEqual(plan["mode"], "closed-action-projection")
        self.assertTrue(plan["no_lifecycle_authority"])
        self.assertEqual(plan["apply_authority"], "wakeup-runner-396-only")
        action = next(item for item in plan["actions"] if item["kind"] == "harness-spawn-intent")
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


class StaleRevivalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.logs = self.repo / ".refactor-loop" / "logs"
        self.logs.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_partial(self, issue: int) -> Path:
        log = self.logs / f"implement-issue-{issue}.log"
        log.write_text(
            f"working...\nIMPLEMENT_DONE:issue-{issue}:partial\nEXIT=0\n", encoding="utf-8"
        )
        return log

    def test_default_threshold_is_three_hours(self) -> None:
        prev = os.environ.pop("STALE_REVIVAL_HOURS", None)
        try:
            self.assertEqual(3 * 3600.0, stale_revival_seconds())
        finally:
            if prev is not None:
                os.environ["STALE_REVIVAL_HOURS"] = prev

    def test_env_override_changes_threshold(self) -> None:
        prev = os.environ.get("STALE_REVIVAL_HOURS")
        try:
            os.environ["STALE_REVIVAL_HOURS"] = "1"
            self.assertEqual(3600.0, stale_revival_seconds())
            os.environ["STALE_REVIVAL_HOURS"] = "bad"
            self.assertEqual(3 * 3600.0, stale_revival_seconds())
            os.environ["STALE_REVIVAL_HOURS"] = "0"
            self.assertEqual(3 * 3600.0, stale_revival_seconds())
        finally:
            if prev is None:
                os.environ.pop("STALE_REVIVAL_HOURS", None)
            else:
                os.environ["STALE_REVIVAL_HOURS"] = prev

    def test_meta_escalation_threshold_defaults_and_normalizes_above_stale_revival(self) -> None:
        prev_meta = os.environ.get("META_ESCALATION_STUCK_HOURS")
        prev_stale = os.environ.get("STALE_REVIVAL_HOURS")
        try:
            os.environ.pop("META_ESCALATION_STUCK_HOURS", None)
            os.environ.pop("STALE_REVIVAL_HOURS", None)
            self.assertEqual(24 * 3600.0, meta_escalation_stuck_seconds())

            os.environ["META_ESCALATION_STUCK_HOURS"] = "bad"
            self.assertEqual(24 * 3600.0, meta_escalation_stuck_seconds())

            os.environ["META_ESCALATION_STUCK_HOURS"] = "0"
            self.assertEqual(24 * 3600.0, meta_escalation_stuck_seconds())

            os.environ["META_ESCALATION_STUCK_HOURS"] = "2"
            os.environ["STALE_REVIVAL_HOURS"] = "5"
            self.assertEqual(5 * 3600.0, meta_escalation_stuck_seconds())
        finally:
            if prev_meta is None:
                os.environ.pop("META_ESCALATION_STUCK_HOURS", None)
            else:
                os.environ["META_ESCALATION_STUCK_HOURS"] = prev_meta
            if prev_stale is None:
                os.environ.pop("STALE_REVIVAL_HOURS", None)
            else:
                os.environ["STALE_REVIVAL_HOURS"] = prev_stale

    def test_stale_partial_implement_log_is_revived(self) -> None:
        log = self._write_partial(421)
        revived = _revive_stale_redispatchable_implement_log(log, now=time.time() + 10 * 3600)
        self.assertTrue(revived)
        self.assertFalse(log.exists())

    def test_fresh_partial_implement_log_is_not_revived(self) -> None:
        log = self._write_partial(421)
        revived = _revive_stale_redispatchable_implement_log(log, now=time.time())
        self.assertFalse(revived)
        self.assertTrue(log.exists())

    def test_clean_ok_stale_base_implement_log_is_not_revived_for_churn(self) -> None:
        worktree = (self.repo / ".worktrees" / "iter421-issue-421").resolve()
        worktree.mkdir(parents=True)
        log = self.logs / "implement-issue-421.log"
        log.write_text("IMPLEMENT_DONE:issue-421:ok\nEXIT=0\n", encoding="utf-8")

        def fake_git(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
            if command == ["git", "-C", str(worktree), "rev-parse", "--abbrev-ref", "HEAD"]:
                return subprocess.CompletedProcess(command, 0, "refactor/iter421-issue-421\n", "")
            if command == ["git", "-C", str(worktree), "merge-base", "HEAD", "origin/auto-refact-dev"]:
                return subprocess.CompletedProcess(command, 0, "old-base\n", "")
            if command == ["git", "-C", str(worktree), "rev-parse", "--verify", "origin/auto-refact-dev"]:
                return subprocess.CompletedProcess(command, 0, "new-base\n", "")
            if command == ["git", "-C", str(worktree), "diff", "HEAD", "--quiet"]:
                return subprocess.CompletedProcess(command, 1, "", "")
            return subprocess.CompletedProcess(command, 1, "", "unexpected")

        with mock.patch.dict(os.environ, {"INTEGRATION_BRANCH": "auto-refact-dev"}):
            with mock.patch("codex_refactor_loop.wakeup_plan.git_text", side_effect=fake_git):
                revived = _revive_stale_redispatchable_implement_log(log, now=time.time() + 10 * 3600)

        self.assertFalse(revived)
        self.assertTrue(log.exists())

    def _write_inflight(self, issue: int) -> Path:
        # no terminal EXIT line => classify() == in_flight (e.g. a codex/supervisor
        # killed mid-run, the cut-off-daemon wedge)
        log = self.logs / f"implement-issue-{issue}.log"
        log.write_text(
            "codex\nFocused suite 仍未结束，当前还是正常通过输出。\n", encoding="utf-8"
        )
        return log

    def test_dead_inflight_log_revived_when_old_and_no_live_process(self) -> None:
        log = self._write_inflight(421)
        revived = _revive_stale_redispatchable_implement_log(log, now=time.time() + 100 * 3600)
        self.assertTrue(revived)
        self.assertFalse(log.exists())

    def test_inflight_log_not_revived_when_fresh(self) -> None:
        log = self._write_inflight(421)
        revived = _revive_stale_redispatchable_implement_log(log, now=time.time())
        self.assertFalse(revived)
        self.assertTrue(log.exists())

    def test_inflight_log_not_revived_when_live_process_present(self) -> None:
        log = self._write_inflight(421)

        class _LiveMonitor:
            def list_in_flight_codex_lines(self_inner) -> list[str]:
                return [f"spawn-codex --log {log} --stall 5400"]

        revived = _revive_stale_redispatchable_implement_log(
            log, now=time.time() + 100 * 3600, monitor=_LiveMonitor()
        )
        self.assertFalse(revived)
        self.assertTrue(log.exists())

    def test_force_revives_fresh_partial_without_age_wait(self) -> None:
        # manual trigger: a just-finished partial (age 0) is NOT revived automatically
        # but force=True clears it immediately.
        log = self._write_partial(421)
        self.assertFalse(_revive_stale_redispatchable_implement_log(log, now=time.time()))
        self.assertTrue(log.exists())
        self.assertTrue(_revive_stale_redispatchable_implement_log(log, now=time.time(), force=True))
        self.assertFalse(log.exists())

    def test_force_does_not_clear_inflight_without_monitor_proof(self) -> None:
        # force has no age gate, so an in_flight log must be proven dead by a
        # live-process check; with no monitor it is left alone (never orphan a codex).
        log = self._write_inflight(421)
        self.assertFalse(_revive_stale_redispatchable_implement_log(log, force=True, monitor=None))
        self.assertTrue(log.exists())

    def test_force_clears_inflight_when_monitor_proves_not_live(self) -> None:
        log = self._write_inflight(421)

        class _IdleMonitor:
            def list_in_flight_codex_lines(self_inner) -> list[str]:
                return []

        self.assertTrue(
            _revive_stale_redispatchable_implement_log(log, force=True, monitor=_IdleMonitor())
        )
        self.assertFalse(log.exists())

    def test_force_revive_stuck_implements_scans_and_reports(self) -> None:
        p493 = self._write_partial(493)
        p494 = self._write_partial(494)
        # an in_flight log (no terminal EXIT) with no monitor proof is left alone
        inflight = self._write_inflight(490)
        revived = force_revive_stuck_implements(self.repo, monitor=None)
        names = {r["log"] for r in revived}
        self.assertIn("implement-issue-493.log", names)
        self.assertIn("implement-issue-494.log", names)
        self.assertNotIn("implement-issue-490.log", names)
        self.assertFalse(p493.exists())
        self.assertFalse(p494.exists())
        self.assertTrue(inflight.exists())


if __name__ == "__main__":
    unittest.main()
