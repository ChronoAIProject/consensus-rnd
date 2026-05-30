#!/usr/bin/env python3
"""Behavior tests for Python controller actions."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop import labels
from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.controller_actions import ControllerActions
from codex_refactor_loop.release.publisher import ReleasePublishResult


class ControllerActionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="controller-actions-test-"))
        (self.tmp / ".refactor-loop" / "state").mkdir(parents=True)
        (self.tmp / ".refactor-loop" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\nexport GH_REPO_SLUG="owner/repo"\n',
            encoding="utf-8",
        )
        self.actions = ControllerActions(LoopContext.load(repo_root=self.tmp))
        self.pr_body = self.tmp / "pr-body.md"
        self.pr_body.write_text("## 🤖 PR ready\n\n自包含正文。\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_record_recent_pr_merge_writes_rolling_artifact(self) -> None:
        facts = {
            "number": 7,
            "mergedAt": "2026-05-29T00:00:00Z",
            "mergeCommit": {"oid": "abc123"},
            "baseRefName": "dev",
            "headRefName": "feature",
        }
        with mock.patch.object(self.actions, "gh", return_value=mock.Mock(returncode=0, stdout=json.dumps(facts), stderr="")):
            self.actions.record_recent_pr_merge("7")
        data = json.loads((self.tmp / ".refactor-loop" / "state" / "recent-pr-merges.json").read_text(encoding="utf-8"))
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["merges"][0]["sha"], "abc123")

    # Refactor (impl/issue191-single-active-controller): Old pattern:
    # lifecycle helpers could mutate GitHub/git from any controller device. New
    # principle: non-owner helpers fail closed before gh/git mutations.
    def test_non_owner_lifecycle_helpers_fail_closed_before_gh_or_git(self) -> None:
        decision = mock.Mock(allowed=False, owner_device="device-a", status="not-owner", action="controller", lease_id="", expires_at="")
        with mock.patch("codex_refactor_loop.controller_actions.require_active_controller", return_value=decision):
            with mock.patch.object(self.actions, "gh", side_effect=AssertionError("gh mutation should not be called")):
                with mock.patch.object(self.actions, "git", side_effect=AssertionError("git mutation should not be called")):
                    self.assertEqual(3, self.actions.merge_pr("7"))
                    self.assertEqual(3, self.actions.safe_push())
                    self.assertEqual(3, self.actions.safe_sync_main())
                    self.assertEqual(3, self.actions.apply_human_label_or_skip("7", "META_RESOLVED:escalate-human:reason"))
                    self.assertEqual(3, self.actions.apply_triage_decision_marker("TRIAGE_DECISION_DONE:53:reject:.refactor-loop/runs/x.json"))
                    with self.assertRaisesRegex(RuntimeError, "active_controller=noop:not-owner"):
                        self.actions.open_pr_with_label("title", str(self.pr_body), head="branch")
                    with self.assertRaisesRegex(RuntimeError, "active_controller=noop:not-owner"):
                        self.actions.open_release_rollup_pr_from_pending_event("{}", str(self.pr_body))
                    with self.assertRaisesRegex(RuntimeError, "active_controller=noop:not-owner"):
                        self.actions.publish_release_candidate(target_ref="abc")

        status = json.loads((self.tmp / ".refactor-loop" / "state" / "active-controller-status.json").read_text(encoding="utf-8"))
        self.assertEqual("noop:not-owner", status["active_controller"])

    def test_triage_apply_marker_rejects_unbounded_paths(self) -> None:
        self.assertEqual(2, self.actions.apply_triage_decision_marker("TRIAGE_DECISION_DONE:x:accept:/tmp/out.json"))

    def test_triage_apply_marker_accepts_valid_marker_through_internal_apply_path(self) -> None:
        runs = self.tmp / ".refactor-loop" / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        comment = runs / "triage-comment.md"
        comment.write_text("comment\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        decision = runs / "triage-issue-53.json"
        decision.write_text(
            json.dumps(
                {
                    "schema": "ManualIssueTriageDecision",
                    "issue_number": 53,
                    "verdict": "reject",
                    "body_artifact_path": "",
                    "comment_artifact_path": ".refactor-loop/runs/triage-comment.md",
                    "add_labels": [],
                    "remove_labels": [labels.TRIAGE_PENDING],
                    "sentinel_present": True,
                    "lifecycle_owner": "controller",
                    "lifecycle_authority": False,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        calls: list[list[str]] = []

        def fake_gh(args: list[str], *, repo: Path, repo_slug: str | None = None) -> subprocess.CompletedProcess[str]:
            self.assertEqual(self.tmp.resolve(), repo)
            self.assertEqual("owner/repo", repo_slug)
            calls.append(args)
            return subprocess.CompletedProcess(["gh", *args], 0, "", "")

        marker = "TRIAGE_DECISION_DONE:53:reject:.refactor-loop/runs/triage-issue-53.json"
        with mock.patch("codex_refactor_loop.triage.current_labels", lambda _config, _issue: ["auto-loop-triage"]):
            with mock.patch("codex_refactor_loop.triage.run_gh", fake_gh):
                with mock.patch(
                    "codex_refactor_loop.controller_actions.subprocess.run",
                    side_effect=AssertionError("controller marker path must not shell through apply-triage"),
                ):
                    self.assertEqual(0, self.actions.apply_triage_decision_marker(marker))

        self.assertEqual(
            calls,
            [
                ["issue", "comment", "53", "--body-file", str(comment.resolve())],
                ["issue", "edit", "53", "--remove-label", "auto-loop-triage"],
            ],
        )
        applied = json.loads(
            (runs / "triage-decisions-applied" / "triage-issue-53.applied.json").read_text(encoding="utf-8")
        )
        self.assertEqual("applied", applied["status"])
        self.assertEqual("reject", applied["reason"])

    def test_open_release_rollup_pr_uses_throwaway_head_and_preserves_integration_ref(self) -> None:
        event = {
            "integration_branch": "auto-refact-dev",
            "review_base_branch": "dev",
            "integration_sha": "abc123",
        }
        git_calls: list[list[str]] = []
        gh_calls: list[list[str]] = []

        def fake_git(args: list[str], *, check: bool = True) -> mock.Mock:
            git_calls.append(args)
            if args[:4] == ["ls-remote", "--exit-code", "--heads", "origin"]:
                return mock.Mock(returncode=0, stdout="abc123\trefs/heads/auto-refact-dev\n", stderr="")
            if args == ["push", "origin", "abc123:refs/heads/rollup/abc123"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            return mock.Mock(returncode=1, stdout="", stderr="unexpected git call")

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:2] == ["pr", "create"]:
                return mock.Mock(returncode=0, stdout="https://github.com/owner/repo/pull/77\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.actions, "git", side_effect=fake_git), mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            pr_num, _url = self.actions.open_release_rollup_pr_from_pending_event(json.dumps(event), str(self.pr_body))

        self.assertEqual(77, pr_num)
        self.assertIn(["push", "origin", "abc123:refs/heads/rollup/abc123"], git_calls)
        create_call = next(call for call in gh_calls if call[:2] == ["pr", "create"])
        self.assertIn("--head", create_call)
        self.assertEqual("rollup/abc123", create_call[create_call.index("--head") + 1])
        self.assertNotEqual("auto-refact-dev", create_call[create_call.index("--head") + 1])

    def test_open_release_rollup_pr_fails_closed_before_push_or_pr_create(self) -> None:
        cases = (
            ("invalid-json", "{not-json", []),
            ("non-object", "[]", []),
            ("missing-sha", json.dumps({"integration_branch": "auto-refact-dev", "review_base_branch": "dev"}), []),
            (
                "unsafe-sha",
                json.dumps({"integration_branch": "auto-refact-dev", "review_base_branch": "dev", "integration_sha": "abc/123"}),
                [],
            ),
            (
                "missing-remote-branch",
                json.dumps({"integration_branch": "auto-refact-dev", "review_base_branch": "dev", "integration_sha": "abc123"}),
                [mock.Mock(returncode=2, stdout="", stderr="not found")],
            ),
            (
                "stale-sha",
                json.dumps({"integration_branch": "auto-refact-dev", "review_base_branch": "dev", "integration_sha": "abc123"}),
                [mock.Mock(returncode=0, stdout="def456\trefs/heads/auto-refact-dev\n", stderr="")],
            ),
        )
        for name, event_json, git_results in cases:
            with self.subTest(name=name):
                git_calls: list[list[str]] = []
                gh_calls: list[list[str]] = []

                def fake_git(args: list[str], *, check: bool = True) -> mock.Mock:
                    git_calls.append(args)
                    if git_results:
                        return git_results.pop(0)
                    return mock.Mock(returncode=1, stdout="", stderr="unexpected git call")

                def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
                    gh_calls.append(args)
                    return mock.Mock(returncode=0, stdout="", stderr="")

                with mock.patch.object(self.actions, "git", side_effect=fake_git), mock.patch.object(self.actions, "gh", side_effect=fake_gh):
                    with self.assertRaises(RuntimeError):
                        self.actions.open_release_rollup_pr_from_pending_event(event_json, str(self.pr_body))

                self.assertFalse(any(call[:1] == ["push"] for call in git_calls), git_calls)
                self.assertFalse(any(call[:2] == ["pr", "create"] for call in gh_calls), gh_calls)

    def test_open_release_rollup_pr_rejects_bad_body_before_git_push_or_pr_create(self) -> None:
        event = {
            "integration_branch": "auto-refact-dev",
            "review_base_branch": "dev",
            "integration_sha": "abc123",
        }
        bad_body = self.tmp / "bad-rollup-body.md"
        bad_body.write_text("## 🤖 rollup\n\n授权:.refactor-loop/runs/phase9-issue192-r1-judge.md\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        git_calls: list[list[str]] = []
        gh_calls: list[list[str]] = []

        def fake_git(args: list[str], *, check: bool = True) -> mock.Mock:
            git_calls.append(args)
            return mock.Mock(returncode=1, stdout="", stderr="unexpected git call")

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.actions, "git", side_effect=fake_git), mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            with self.assertRaisesRegex(RuntimeError, "local .refactor-loop artifact path"):
                self.actions.open_release_rollup_pr_from_pending_event(json.dumps(event), str(bad_body))

        self.assertFalse(any(call[:1] == ["push"] for call in git_calls), git_calls)
        self.assertFalse(any(call[:2] == ["pr", "create"] for call in gh_calls), gh_calls)

    def test_open_release_rollup_pr_failed_push_does_not_create_pr(self) -> None:
        event = {
            "integration_branch": "auto-refact-dev",
            "review_base_branch": "dev",
            "integration_sha": "abc123",
        }
        git_calls: list[list[str]] = []
        gh_calls: list[list[str]] = []

        def fake_git(args: list[str], *, check: bool = True) -> mock.Mock:
            git_calls.append(args)
            if args[:4] == ["ls-remote", "--exit-code", "--heads", "origin"]:
                return mock.Mock(returncode=0, stdout="abc123\trefs/heads/auto-refact-dev\n", stderr="")
            if args == ["push", "origin", "abc123:refs/heads/rollup/abc123"]:
                return mock.Mock(returncode=1, stdout="", stderr="push failed")
            return mock.Mock(returncode=1, stdout="", stderr="unexpected git call")

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.actions, "git", side_effect=fake_git), mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            with self.assertRaisesRegex(RuntimeError, "push failed"):
                self.actions.open_release_rollup_pr_from_pending_event(json.dumps(event), str(self.pr_body))

        self.assertIn(["push", "origin", "abc123:refs/heads/rollup/abc123"], git_calls)
        self.assertFalse(any(call[:2] == ["pr", "create"] for call in gh_calls), gh_calls)

    def test_open_pr_with_label_fails_closed_before_create_for_path_only_authority(self) -> None:
        bad_body = self.tmp / "bad-pr-body.md"
        bad_body.write_text("## 🤖 PR ready\n\n授权:.refactor-loop/runs/phase9-issue192-r1-judge.md\n\n⟦AI:AUTO-LOOP⟧\n", encoding="utf-8")
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            return mock.Mock(returncode=0, stdout="https://github.com/owner/repo/pull/77\n", stderr="")

        with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            with self.assertRaisesRegex(RuntimeError, "local .refactor-loop artifact path"):
                self.actions.open_pr_with_label("title", str(bad_body), head="refactor/branch")

        self.assertFalse(any(call[:2] == ["pr", "create"] for call in gh_calls), gh_calls)

    def test_open_pr_with_label_accepts_self_contained_body(self) -> None:
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:2] == ["pr", "create"]:
                return mock.Mock(returncode=0, stdout="https://github.com/owner/repo/pull/77\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            pr_num, _url = self.actions.open_pr_with_label("title", str(self.pr_body), head="refactor/branch")

        self.assertEqual(77, pr_num)
        self.assertTrue(any(call[:2] == ["pr", "create"] for call in gh_calls), gh_calls)
        edit_call = next(call for call in gh_calls if call[:2] == ["pr", "edit"])
        self.assertEqual("77", edit_call[2])
        self.assertEqual(
            ",".join((labels.MANAGED, labels.PHASE_REVIEWING, labels.HUMAN_AUTO)),
            edit_call[edit_call.index("--add-label") + 1],
        )
        self.assertNotIn("auto-loop", edit_call)
        self.assertNotIn("🚀 phase:pr-open", edit_call)
        self.assertFalse(any(call[:2] == ["issue", "edit"] for call in gh_calls), gh_calls)

    def test_open_pr_with_label_moves_linked_parent_issue_to_pr_open(self) -> None:
        self.pr_body.write_text(
            "## 🤖 PR ready\n\nSelf-contained body.\n\nCloses #239\n\n⟦AI:AUTO-LOOP⟧\n",
            encoding="utf-8",
        )
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:2] == ["pr", "create"]:
                return mock.Mock(returncode=0, stdout="https://github.com/owner/repo/pull/77\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            pr_num, _url = self.actions.open_pr_with_label("title", str(self.pr_body), head="refactor/branch")

        self.assertEqual(77, pr_num)
        issue_edit = next(call for call in gh_calls if call[:3] == ["issue", "edit", "239"])
        removed = [issue_edit[index + 1] for index, value in enumerate(issue_edit) if value == "--remove-label"]
        self.assertIn(labels.PHASE_IMPLEMENTING, removed)
        self.assertIn(labels.HUMAN_MAINTAINER_DECISION, removed)
        self.assertIn(labels.STUCK, removed)
        self.assertEqual(
            ",".join((labels.PHASE_PR_OPEN, labels.HUMAN_AUTO, labels.MANAGED)),
            issue_edit[issue_edit.index("--add-label") + 1],
        )

    def test_open_pr_with_label_does_not_guess_when_body_closes_multiple_issues(self) -> None:
        self.pr_body.write_text(
            "## 🤖 PR ready\n\nSelf-contained body.\n\nCloses #239\nCloses #240\n\n⟦AI:AUTO-LOOP⟧\n",
            encoding="utf-8",
        )
        gh_calls: list[list[str]] = []

        def fake_gh(args: list[str], *, check: bool = True) -> mock.Mock:
            gh_calls.append(args)
            if args[:2] == ["pr", "create"]:
                return mock.Mock(returncode=0, stdout="https://github.com/owner/repo/pull/77\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.actions, "gh", side_effect=fake_gh):
            pr_num, _url = self.actions.open_pr_with_label("title", str(self.pr_body), head="refactor/branch")

        self.assertEqual(77, pr_num)
        self.assertFalse(any(call[:2] == ["issue", "edit"] for call in gh_calls), gh_calls)

    def test_publish_release_candidate_requires_explicit_or_env_target_ref(self) -> None:
        with mock.patch.dict("codex_refactor_loop.controller_actions.os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "RELEASE_TARGET_REF is required"):
                self.actions.publish_release_candidate()

    def test_publish_release_candidate_uses_release_target_ref_env_when_omitted(self) -> None:
        result = ReleasePublishResult(
            published=True,
            reasons=(),
            tag="v2.0.0",
            target_ref="env-ref",
            version="2.0.0",
            release_url="https://github.test/release/v2.0.0",
            result_path=self.tmp / ".refactor-loop/state/release-publish-result.json",
        )
        publisher = mock.Mock()
        publisher.publish.return_value = result

        with mock.patch.dict("codex_refactor_loop.controller_actions.os.environ", {"RELEASE_TARGET_REF": "env-ref"}, clear=True):
            with mock.patch("codex_refactor_loop.controller_actions.ReleasePublisher", return_value=publisher) as publisher_type:
                actual = self.actions.publish_release_candidate()

        self.assertIs(actual, result)
        publisher_type.assert_called_once_with(self.actions.ctx.repo_root)
        publisher.publish.assert_called_once_with(
            candidate_path=".refactor-loop/state/release-candidate.json",
            target_ref="env-ref",
        )

    def test_publish_release_candidate_forwards_explicit_target_ref_and_candidate_path(self) -> None:
        result = ReleasePublishResult(
            published=True,
            reasons=(),
            tag="v2.0.0",
            target_ref="explicit-ref",
            version="2.0.0",
            release_url="https://github.test/release/v2.0.0",
            result_path=self.tmp / ".refactor-loop/state/release-publish-result.json",
        )
        publisher = mock.Mock()
        publisher.publish.return_value = result

        with mock.patch.dict("codex_refactor_loop.controller_actions.os.environ", {"RELEASE_TARGET_REF": "env-ref"}, clear=True):
            with mock.patch("codex_refactor_loop.controller_actions.ReleasePublisher", return_value=publisher) as publisher_type:
                actual = self.actions.publish_release_candidate(
                    candidate_path=".refactor-loop/state/custom-candidate.json",
                    target_ref="explicit-ref",
                )

        self.assertIs(actual, result)
        publisher_type.assert_called_once_with(self.actions.ctx.repo_root)
        publisher.publish.assert_called_once_with(
            candidate_path=".refactor-loop/state/custom-candidate.json",
            target_ref="explicit-ref",
        )

    def write_host_workflow_spec(self, data: dict) -> ControllerActions:
        (self.tmp / "workflow.json").write_text(json.dumps(data), encoding="utf-8")
        ctx = LoopContext.load(
            repo_root=self.tmp,
            env={"REPO_ROOT": str(self.tmp), "GH_REPO_SLUG": "owner/repo", "HOST_WORKFLOW_SPEC": "workflow.json"},
        )
        return ControllerActions(ctx)

    def valid_host_prompt_spec(self) -> dict:
        (self.tmp / "prompts").mkdir(exist_ok=True)
        (self.tmp / "prompts" / "host-render.md").write_text(
            "Host ${HOST_NAME} handles {{work_unit_id}} from {{cluster_id}}.\n",
            encoding="utf-8",
        )
        return {"prompt_bindings": {"host:render": "prompts/host-render.md"}}

    def test_render_template_resolves_host_prompt_binding_from_valid_workflow_spec(self) -> None:
        actions = self.write_host_workflow_spec(self.valid_host_prompt_spec())
        output = self.tmp / "rendered.md"

        actions.render_template(
            "host:render",
            str(output),
            env={"HOST_NAME": "example-host", "WORK_UNIT_ID": "issue-219", "CLUSTER_ID": "cluster-219"},
        )

        self.assertEqual(output.read_text(encoding="utf-8"), "Host example-host handles issue-219 from cluster-219.\n")

    def test_render_template_rejects_unknown_host_prompt_binding(self) -> None:
        actions = self.write_host_workflow_spec(self.valid_host_prompt_spec())
        output = self.tmp / "rendered.md"

        with self.assertRaisesRegex(RuntimeError, "unknown host prompt binding: host:missing"):
            actions.render_template("host:missing", str(output))

        self.assertFalse(output.exists())

    def test_render_template_rejects_invalid_host_workflow_spec(self) -> None:
        actions = self.write_host_workflow_spec({"prompt_bindings": {"host:render": "../outside.md"}})
        output = self.tmp / "rendered.md"

        with self.assertRaisesRegex(RuntimeError, "prompt binding path must be repo-relative POSIX text"):
            actions.render_template("host:render", str(output))

        self.assertFalse(output.exists())


class ControllerActionsSourceRegressionTests(unittest.TestCase):
    def test_required_lifecycle_helpers_exist(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        for needle in ("merge_pr", "open_pr_with_label", "open_release_rollup_pr_from_pending_event", "safe_worktree", "record_recent_pr_merge", "apply_triage_decision_marker", "render_template"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        self.assertIn("validate_self_contained_github_body", text)

    def test_legacy_dev_sync_request_controller_apply_is_removed(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        self.assertNotIn("apply_dev_sync_request_marker", text)
        self.assertNotIn("DEV_SYNC_REQUEST:", text)
        self.assertNotIn("apply-sync", text)

    def test_rollup_helper_uses_throwaway_head_ref(self) -> None:
        text = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        self.assertIn('rollup_head = f"rollup/{integration_sha}"', text)
        self.assertIn('f"{integration_sha}:refs/heads/{rollup_head}"', text)
        self.assertNotIn('head=integration_branch', text)


if __name__ == "__main__":
    unittest.main()
