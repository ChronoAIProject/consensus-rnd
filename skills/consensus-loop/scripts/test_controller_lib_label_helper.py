#!/usr/bin/env python3
"""Behavior tests for codex_refactor_loop/controller_actions.py human-label helper."""

from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]
sys.path.insert(0, str(SCRIPT_PATH.parent))

from codex_refactor_loop import labels
from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.controller_actions import ControllerActions

HUMAN_LABEL = labels.HUMAN_MAINTAINER_DECISION
VALID_MARKER = "META_RESOLVED:escalate-human:human-label-semantics-guard"


def write_host_env(root: Path) -> str:
    path = root / ".config" / "consensus-rnd" / "host.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'export REPO_ROOT="{root}"\n'
        'export GH_REPO_SLUG="test-owner/test-repo"\n'
        'export INTEGRATION_BRANCH="integration-branch"\n'
        'export REVIEW_BASE_BRANCH="review-base"\n',
        encoding="utf-8",
    )
    return ".config/consensus-rnd/host.env"


def flattened_gh_command(args: list[str]) -> str:
    return " ".join(args)


class ControllerLibHumanLabelPrHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.gh_log = self.root / "gh.log"
        self.directive_dir = self.root / ".refactor-loop" / "runs" / "maintainer-directives"

        fake_gh = self.root / "gh"
        fake_gh.write_text(
            '#!/bin/bash\n'
            'echo "$@" >> "$FAKE_GH_LOG"\n'
            'if [[ "$1 $2" == "api user" ]]; then printf \'%s\\n\' \'{"login":"controller-bot"}\'; exit 0; fi\n'
            'if [[ "$1 $2" == "api repos/test-owner/test-repo/collaborators/controller-bot/permission" ]]; then printf \'%s\\n\' \'{"permission":"write"}\'; exit 0; fi\n'
            'if [[ "$1 $2 $3" == "pr view 55" && "$*" == *"--json comments"* ]]; then printf \'%s\\n\' \'{"comments":[]}\'; exit 0; fi\n'
            'if [[ "$1 $2" == "api repos/test-owner/test-repo/issues/55/comments?per_page=100" ]]; then printf \'%s\\n\' \'[]\'; exit 0; fi\n'
            'if [[ "$1 $2" == "api repos/test-owner/test-repo/issues/55/timeline?per_page=100" ]]; then printf \'%s\\n\' \'[]\'; exit 0; fi\n',
            encoding="utf-8",
        )
        fake_gh.chmod(0o755)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_helper(self, *args: str, marker_env: str = "") -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.root}{os.pathsep}{env['PATH']}",
                "REPO_ROOT": str(self.root),
                "FAKE_GH_LOG": str(self.gh_log),
                "GH_REPO_SLUG": "test-owner/test-repo",
                "GH_OWNER": "",
                "GH_REPO_NAME": "",
                "GH_REPO": "",
                "CONSENSUS_RND_HOST_ENV": write_host_env(self.root),
                "HUMAN_LABEL_SOURCE_MARKER": marker_env,
            }
        )
        stdout = StringIO()
        stderr = StringIO()
        old_env = os.environ.copy()
        try:
            os.environ.clear()
            os.environ.update(env)
            actions = ControllerActions(LoopContext.load(env=env, cwd=self.root))
            pr_number = args[0] if len(args) > 0 else ""
            source_marker = args[1] if len(args) > 1 else ""
            reason = args[2] if len(args) > 2 else ""
            with redirect_stdout(stdout), redirect_stderr(stderr):
                returncode = actions.apply_human_label_or_skip(pr_number, source_marker, reason)
        finally:
            os.environ.clear()
            os.environ.update(old_env)
        return subprocess.CompletedProcess(
            ["controller-internal", "apply_human_label_or_skip", *args],
            returncode,
            stdout.getvalue(),
            stderr.getvalue(),
        )

    def write_directive(self, name: str, body: str) -> None:
        self.directive_dir.mkdir(parents=True, exist_ok=True)
        (self.directive_dir / name).write_text(body, encoding="utf-8")

    def gh_calls(self) -> list[str]:
        if not self.gh_log.exists():
            return []
        return self.gh_log.read_text(encoding="utf-8").splitlines()

    def gh_mutation_calls(self) -> list[str]:
        return [
            call
            for call in self.gh_calls()
            if call
            not in {
                "api user",
                "api repos/test-owner/test-repo/collaborators/controller-bot/permission",
                "pr view 55 --repo test-owner/test-repo --json body",
                "pr view 55 --repo test-owner/test-repo --json comments",
                "api repos/test-owner/test-repo/issues/55/timeline -H Accept: application/vnd.github+json",
                "api repos/test-owner/test-repo/issues/55/comments?per_page=100 --paginate --slurp",
                "api repos/test-owner/test-repo/issues/55/timeline?per_page=100 -H Accept: application/vnd.github+json --paginate --slurp",
            }
        ]

    def assert_gh_not_called(self) -> None:
        self.assertEqual(self.gh_calls(), [])

    def assert_human_label_applied_once(self) -> None:
        self.assertEqual(self.gh_mutation_calls(), [f"pr edit 55 --repo test-owner/test-repo --add-label {HUMAN_LABEL}"])

    def test_apply_human_label_accepts_meta_resolved_marker_for_pr(self) -> None:
        result = self.run_helper("55", VALID_MARKER, "human-label-semantics-guard")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_human_label_applied_once()

    def test_apply_human_label_when_directives_dir_absent(self) -> None:
        result = self.run_helper("55", "META_RESOLVED:escalate-human:reason", "reason")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_human_label_applied_once()

    def test_apply_human_label_applies_when_local_directive_unrelated(self) -> None:
        self.write_directive(
            "2026-05-26-other.md",
            "Maintainer directive for a different PR and unrelated topic.\n",
        )

        result = self.run_helper("55", VALID_MARKER, "human-label-semantics-guard")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_human_label_applied_once()

    def test_apply_human_label_applies_when_local_directive_mentions_pr(self) -> None:
        self.write_directive(
            "2026-05-26-pr.md",
            "Maintainer already authorized this path for PR #55.\n",
        )

        result = self.run_helper("55", VALID_MARKER, "human-label-semantics-guard")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_human_label_applied_once()

    def test_apply_human_label_applies_when_local_directive_mentions_topic(self) -> None:
        self.write_directive(
            "2026-05-26-topic.md",
            "The human-label-semantics-guard route is covered by maintainer directive.\n",
        )

        result = self.run_helper("55", VALID_MARKER, "human-label-semantics-guard")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_human_label_applied_once()

    def test_apply_human_label_missing_arg_returns_2(self) -> None:
        result = self.run_helper("")

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("apply_human_label_or_skip: missing pr_number", result.stderr)
        self.assert_gh_not_called()

    def test_apply_human_label_rejects_missing_marker(self) -> None:
        result = self.run_helper("55")

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("ERROR: apply_human_label_or_skip requires META_RESOLVED:escalate-human marker source", result.stderr)
        self.assert_gh_not_called()

    def test_apply_human_label_rejects_meta_judge_marker(self) -> None:
        result = self.run_helper("55", "META_JUDGE_DONE:escalate:philosophy:tier-boundary", "tier-boundary")

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("ERROR: apply_human_label_or_skip requires META_RESOLVED:escalate-human marker source", result.stderr)
        self.assert_gh_not_called()

    def test_apply_human_label_rejects_fix_blocked_marker(self) -> None:
        result = self.run_helper("55", "FIX_BLOCKED:55:round-2:human-decision:rename-api", "rename-api")

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("ERROR: apply_human_label_or_skip requires META_RESOLVED:escalate-human marker source", result.stderr)
        self.assert_gh_not_called()

    def test_apply_human_label_accepts_meta_resolved_marker_from_env(self) -> None:
        result = self.run_helper("55", "human-label-semantics-guard", marker_env=VALID_MARKER)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_human_label_applied_once()


class ControllerLibRecentMergeProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.gh_log = self.root / "gh.log"
        self.git_log = self.root / "git.log"
        self._write_fake_gh(merge_exit=0, fact_json=json.dumps({
            "number": 55,
            "mergedAt": "2026-05-29T01:02:03Z",
            "mergeCommit": {"oid": "abc123"},
            "baseRefName": "auto-refact-dev",
            "headRefName": "refactor/issue145",
        }))
        fake_git = self.root / "git"
        fake_git.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" >> \"$FAKE_GIT_LOG\"\n"
            "if [[ \"$*\" == \"-C $REPO_ROOT worktree list --porcelain\" ]]; then\n"
            "  printf 'worktree %s/wt\\nbranch refs/heads/refactor/issue145\\n' \"$REPO_ROOT\"\n"
            "fi\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o755)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_fake_gh(
        self,
        *,
        merge_exit: int,
        fact_json: str,
        body: str = "Closes #145\n",
        head: str = "refactor/issue145",
        is_draft: str = "false",
        ready_exit: int = 0,
    ) -> None:
        fake_gh = self.root / "gh"
        fake_gh.write_text(
            f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_GH_LOG"
if [[ "$1 $2" == "api user" ]]; then
  printf '%s\\n' '{{"login":"controller-bot"}}'
  exit 0
fi
if [[ "$1 $2" == "api repos/test-owner/test-repo/collaborators/controller-bot/permission" ]]; then
  printf '%s\\n' '{{"permission":"write"}}'
  exit 0
fi
if [[ "$1 $2 $3" == "pr view 55" && "$*" == *"--json comments"* ]]; then
  printf '%s\\n' '{{"comments":[]}}'
  exit 0
fi
if [[ "$1 $2" == "api repos/test-owner/test-repo/issues/55/timeline" ]]; then
  printf '%s\\n' '[]'
  exit 0
fi
if [[ "$1 $2 $3" == "pr view 55" && "$*" == *"--json labels,body"* ]]; then
  printf '%s\\n' '{{"labels":[{{"name":"crnd:lifecycle:managed"}}],"body":""}}'
  exit 0
fi
if [[ "$1 $2 $3" == "pr view 55" && "$*" == *"--json body"* ]]; then
  printf '%s\\n' {json.dumps(json.dumps({"body": body}))}
  exit 0
fi
if [[ "$1 $2 $3" == "issue view 145" && "$*" == *"--json comments"* ]]; then
  printf '%s\\n' '{{"comments":[]}}'
  exit 0
fi
if [[ "$1 $2" == "api repos/test-owner/test-repo/issues/145/timeline" ]]; then
  printf '%s\\n' '[]'
  exit 0
fi
if [[ "$1 $2 $3" == "issue view 145" && "$*" == *"--json labels"* ]]; then
  printf '%s\\n' '{{"labels":[{{"name":"crnd:lifecycle:managed"}}]}}'
  exit 0
fi
if [[ "$1 $2 $3" == "pr view 55" && "$*" == *"--json changedFiles"* ]]; then
  printf '%s\\n' '{{"changedFiles":1}}'
  exit 0
fi
if [[ "$1 $2 $3" == "pr view 55" && "$*" == *"--json isDraft"* ]]; then
  printf '%s\\n' {json.dumps(is_draft)}
  exit 0
fi
if [[ "$1 $2 $3" == "pr view 55" && "$*" == *"--json labels,body"* ]]; then
  printf '%s\\n' {json.dumps(json.dumps({"labels": [{"name": "crnd:lifecycle:managed"}], "body": body}))}
  exit 0
fi
if [[ "$1 $2 $3" == "pr ready 55" ]]; then
  exit {ready_exit}
fi
if [[ "$1 $2 $3" == "pr merge 55" ]]; then
  printf 'merge output\\n'
  exit {merge_exit}
fi
if [[ "$1 $2 $3" == "pr view 55" && "$*" == *"--json number,mergedAt,mergeCommit,baseRefName,headRefName"* ]]; then
  printf '%s\\n' {json.dumps(fact_json)}
  exit 0
fi
if [[ "$1 $2 $3" == "pr view 55" && "$*" == *"--json headRefName"* ]]; then
  printf '%s\\n' {json.dumps(head)}
  exit 0
fi
exit 0
""",
            encoding="utf-8",
        )
        fake_gh.chmod(0o755)

    def run_merge_pr(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.root}{os.pathsep}{env['PATH']}",
                "REPO_ROOT": str(self.root),
                "FAKE_GH_LOG": str(self.gh_log),
                "FAKE_GIT_LOG": str(self.git_log),
                "GH_REPO_SLUG": "test-owner/test-repo",
                "GH_OWNER": "",
                "GH_REPO_NAME": "",
                "GH_REPO": "",
                "CONSENSUS_RND_HOST_ENV": write_host_env(self.root),
                "RECENT_PR_MERGE_RETRY_SLEEP_SECONDS": "0",
            }
        )
        stdout = StringIO()
        stderr = StringIO()
        old_env = os.environ.copy()
        try:
            os.environ.clear()
            os.environ.update(env)
            actions = ControllerActions(LoopContext.load(env=env, cwd=self.root))
            pr = args[0] if len(args) > 0 else ""
            issue = args[1] if len(args) > 1 else ""
            with redirect_stdout(stdout), redirect_stderr(stderr):
                try:
                    returncode = actions.merge_pr(pr, issue)
                except RuntimeError as exc:
                    print(str(exc), file=sys.stderr)
                    returncode = 2
        finally:
            os.environ.clear()
            os.environ.update(old_env)
        return subprocess.CompletedProcess(
            ["controller-internal", "merge_pr", *args],
            returncode,
            stdout.getvalue(),
            stderr.getvalue(),
        )

    def gh_calls(self) -> list[str]:
        if not self.gh_log.exists():
            return []
        return self.gh_log.read_text(encoding="utf-8").splitlines()

    def recent_merges(self) -> dict[str, object]:
        return json.loads((self.root / ".refactor-loop/state/recent-pr-merges.json").read_text(encoding="utf-8"))

    def write_recent_merges(self, entries: list[dict[str, object]]) -> None:
        path = self.root / ".refactor-loop/state/recent-pr-merges.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "count": len(entries),
                    "window_hours": 2,
                    "updated_at": self.iso_utc(datetime.now(timezone.utc)),
                    "merges": entries,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def iso_utc(value: datetime) -> str:
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def test_merge_pr_records_recent_merge_after_success(self) -> None:
        result = self.run_merge_pr("55")

        self.assertEqual(result.returncode, 0, result.stderr)
        artifact = self.recent_merges()
        self.assertEqual(artifact["count"], 1)
        self.assertEqual(artifact["window_hours"], 2)
        merges = artifact["merges"]
        self.assertIsInstance(merges, list)
        assert isinstance(merges, list)
        self.assertEqual(merges[0]["pr"], 55)
        self.assertEqual(merges[0]["sha"], "abc123")
        self.assertEqual(merges[0]["merged_at"], "2026-05-29T01:02:03Z")
        calls = self.gh_calls()
        self.assertIn("pr view 55 --repo test-owner/test-repo --json isDraft --jq .isDraft", calls)
        self.assertNotIn("pr ready 55 --repo test-owner/test-repo", calls)
        self.assertIn("pr merge 55 --repo test-owner/test-repo --squash --delete-branch", calls)
        expected_pr_edit = ["pr", "edit", "55", "--repo", "test-owner/test-repo"]
        for label in (
            *labels.labels_for_group("phase"),
            labels.HUMAN_MAINTAINER_DECISION,
            labels.STUCK,
        ):
            expected_pr_edit.extend(["--remove-label", label])
        expected_pr_edit.extend(["--add-label", labels.PHASE_MERGED])
        self.assertIn(flattened_gh_command(expected_pr_edit), calls)

        expected_issue_edit = ["issue", "edit", "145", "--repo", "test-owner/test-repo"]
        for label in (
            *labels.labels_for_group("phase"),
            labels.HUMAN_AUTO,
            labels.HUMAN_MAINTAINER_DECISION,
            labels.STUCK,
        ):
            expected_issue_edit.extend(["--remove-label", label])
        expected_issue_edit.extend(["--add-label", labels.PHASE_MERGED])
        self.assertIn(flattened_gh_command(expected_issue_edit), calls)
        joined_calls = "\n".join(calls)
        self.assertIn("issue close 145 --repo test-owner/test-repo --reason completed --comment", joined_calls)
        self.assertIn("Auto-merged via PR #55", joined_calls)

    def test_merge_pr_prunes_expired_dedupes_current_and_counts_window(self) -> None:
        now = datetime.now(timezone.utc)
        self.write_recent_merges(
            [
                {
                    "pr": 41,
                    "sha": "expired456",
                    "merged_at": self.iso_utc(now - timedelta(hours=3)),
                    "base_ref": "auto-refact-dev",
                    "head_ref": "refactor/old",
                },
                {
                    "pr": 42,
                    "sha": "recent789",
                    "merged_at": self.iso_utc(now - timedelta(minutes=5)),
                    "base_ref": "auto-refact-dev",
                    "head_ref": "refactor/recent",
                },
                {
                    "pr": 55,
                    "sha": "abc123",
                    "merged_at": self.iso_utc(now - timedelta(minutes=1)),
                    "base_ref": "auto-refact-dev",
                    "head_ref": "refactor/issue145-duplicate",
                },
            ]
        )

        result = self.run_merge_pr("55", "145")

        self.assertEqual(result.returncode, 0, result.stderr)
        artifact = self.recent_merges()
        self.assertEqual(artifact["count"], 2)
        self.assertEqual(artifact["window_hours"], 2)
        merges = artifact["merges"]
        self.assertIsInstance(merges, list)
        assert isinstance(merges, list)
        self.assertEqual(
            [(entry["pr"], entry["sha"]) for entry in merges],
            [(42, "recent789"), (55, "abc123")],
        )
        self.assertEqual(merges[-1]["merged_at"], "2026-05-29T01:02:03Z")

    def test_merge_pr_dedupes_existing_same_pr_and_sha(self) -> None:
        self.write_recent_merges(
            [
                {
                    "pr": 55,
                    "sha": "abc123",
                    "merged_at": self.iso_utc(datetime.now(timezone.utc) - timedelta(minutes=10)),
                    "base_ref": "auto-refact-dev",
                    "head_ref": "refactor/previous-duplicate",
                }
            ]
        )

        result = self.run_merge_pr("55", "145")

        self.assertEqual(result.returncode, 0, result.stderr)
        artifact = self.recent_merges()
        self.assertEqual(artifact["count"], 1)
        merges = artifact["merges"]
        self.assertIsInstance(merges, list)
        assert isinstance(merges, list)
        self.assertEqual(len(merges), 1)
        self.assertEqual(merges[0]["pr"], 55)
        self.assertEqual(merges[0]["sha"], "abc123")
        self.assertEqual(merges[0]["merged_at"], "2026-05-29T01:02:03Z")
        self.assertEqual(merges[0]["head_ref"], "refactor/issue145")

    def test_merge_pr_does_not_record_or_cleanup_when_merge_fails(self) -> None:
        self._write_fake_gh(merge_exit=1, fact_json="{}")

        result = self.run_merge_pr("55", "145")

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse((self.root / ".refactor-loop/state/recent-pr-merges.json").exists())
        calls = "\n".join(self.gh_calls())
        self.assertIn("pr merge 55", calls)
        self.assertNotIn("pr edit 55", calls)
        self.assertNotIn("issue close 145", calls)
        self.assertNotIn("worktree remove", self.git_log.read_text(encoding="utf-8") if self.git_log.exists() else "")

    def test_merge_pr_marks_draft_ready_before_merge(self) -> None:
        self._write_fake_gh(
            merge_exit=0,
            fact_json=json.dumps({
                "number": 55,
                "mergedAt": "2026-05-29T01:02:03Z",
                "mergeCommit": {"oid": "abc123"},
                "baseRefName": "auto-refact-dev",
                "headRefName": "refactor/issue145",
            }),
            is_draft="true",
        )

        result = self.run_merge_pr("55", "145")

        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.gh_calls()
        ready_call = "pr ready 55 --repo test-owner/test-repo"
        merge_call = "pr merge 55 --repo test-owner/test-repo --squash --delete-branch"
        self.assertIn(ready_call, calls)
        self.assertIn(merge_call, calls)
        self.assertLess(calls.index(ready_call), calls.index(merge_call))

    def test_merge_pr_ready_failure_fails_closed_before_projection_or_cleanup(self) -> None:
        self._write_fake_gh(merge_exit=0, fact_json="{}", is_draft="true", ready_exit=7)

        result = self.run_merge_pr("55", "145")

        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertFalse((self.root / ".refactor-loop/state/recent-pr-merges.json").exists())
        calls = "\n".join(self.gh_calls())
        self.assertIn("pr ready 55", calls)
        self.assertNotIn("pr merge 55", calls)
        self.assertNotIn("pr edit 55", calls)
        self.assertNotIn("issue close 145", calls)
        self.assertNotIn("worktree remove", self.git_log.read_text(encoding="utf-8") if self.git_log.exists() else "")

    def test_merge_pr_projection_failure_returns_nonzero(self) -> None:
        self._write_fake_gh(merge_exit=0, fact_json=json.dumps({
            "number": 55,
            "mergedAt": "",
            "mergeCommit": {},
            "baseRefName": "auto-refact-dev",
            "headRefName": "refactor/issue145",
        }))

        result = self.run_merge_pr("55", "145")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("recent-pr-merges projection failed", result.stderr)
        self.assertIn("recover by writing .refactor-loop/state/recent-pr-merges.json", result.stderr)
        self.assertFalse((self.root / ".refactor-loop/state/recent-pr-merges.json").exists())
        calls = "\n".join(self.gh_calls())
        self.assertIn("pr merge 55", calls)
        self.assertNotIn("pr edit 55", calls)
        self.assertNotIn("issue close 145", calls)


if __name__ == "__main__":
    unittest.main()
