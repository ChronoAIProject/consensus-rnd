#!/usr/bin/env python3
"""Behavior tests for controller_lib.sh human-label helper."""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]
CONTROLLER_LIB = REPO_ROOT / "skills" / "codex-refactor-loop" / "scripts" / "controller_lib.sh"
REPO_SLUG_LIB = REPO_ROOT / "skills" / "codex-refactor-loop" / "scripts" / "repo_slug.sh"
HUMAN_LABEL = "👤 human:需-maintainer-决策"
VALID_MARKER = "META_RESOLVED:escalate-human:human-label-semantics-guard"


class ControllerLibHumanLabelPrHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.controller = self.root / "controller_lib.sh"
        self.gh_log = self.root / "gh.log"
        self.directive_dir = self.root / ".refactor-loop" / "runs" / "maintainer-directives"
        self.directive_dir.mkdir(parents=True)
        shutil.copy2(CONTROLLER_LIB, self.controller)
        shutil.copy2(REPO_SLUG_LIB, self.root / "repo_slug.sh")

        fake_gh = self.root / "gh"
        fake_gh.write_text(
            '#!/bin/bash\n'
            'echo "$@" >> "$FAKE_GH_LOG"\n',
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
                "HUMAN_LABEL_SOURCE_MARKER": marker_env,
            }
        )
        return subprocess.run(
            ["bash", "-c", 'source "$CONTROLLER_LIB"; apply_human_label_or_skip "$@"', "bash", *args],
            env={**env, "CONTROLLER_LIB": str(self.controller)},
            text=True,
            capture_output=True,
            check=False,
        )

    def write_directive(self, name: str, body: str) -> None:
        (self.directive_dir / name).write_text(body, encoding="utf-8")

    def gh_calls(self) -> list[str]:
        if not self.gh_log.exists():
            return []
        return self.gh_log.read_text(encoding="utf-8").splitlines()

    def assert_gh_not_called(self) -> None:
        self.assertEqual(self.gh_calls(), [])

    def assert_human_label_applied_once(self) -> None:
        self.assertEqual(self.gh_calls(), [f"pr edit 55 --repo test-owner/test-repo --add-label {HUMAN_LABEL}"])

    def test_apply_human_label_skips_when_directive_matches_pr(self) -> None:
        self.write_directive(
            "2026-05-26-pr.md",
            "Maintainer already authorized this path for PR #55.\n",
        )

        result = self.run_helper("55", VALID_MARKER, "human-label-semantics-guard")

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("skip-label", result.stdout)
        self.assert_gh_not_called()

    def test_apply_human_label_accepts_meta_resolved_marker_for_pr(self) -> None:
        result = self.run_helper("55", VALID_MARKER, "human-label-semantics-guard")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_human_label_applied_once()

    def test_apply_human_label_when_directives_dir_absent(self) -> None:
        """Helper handles missing maintainer-directives directory gracefully."""
        self.directive_dir.rmdir()

        result = self.run_helper("55", "META_RESOLVED:escalate-human:reason", "reason")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_human_label_applied_once()

    def test_apply_human_label_applies_when_directive_unrelated(self) -> None:
        self.write_directive(
            "2026-05-26-other.md",
            "Maintainer directive for a different PR and unrelated topic.\n",
        )

        result = self.run_helper("55", VALID_MARKER, "human-label-semantics-guard")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_human_label_applied_once()

    def test_apply_human_label_no_substring_false_match(self) -> None:
        """Directive containing PR #555 must NOT match helper run for PR 55."""
        self.write_directive(
            "2026-05-26-pr-555.md",
            "Maintainer directive for PR #555 covers a different path.\n",
        )

        result = self.run_helper("55", VALID_MARKER, "human-label-semantics-guard")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_human_label_applied_once()

    def test_apply_human_label_skips_when_topic_in_directive_body(self) -> None:
        self.write_directive(
            "2026-05-26-topic.md",
            "The human-label-semantics-guard route is covered by maintainer directive.\n",
        )

        result = self.run_helper("55", VALID_MARKER, "human-label-semantics-guard")

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("skip-label", result.stdout)
        self.assert_gh_not_called()

    def test_apply_human_label_partial_topic_not_authorized(self) -> None:
        """Directive containing a topic fragment must not authorize the full topic."""
        self.write_directive(
            "2026-05-26-topic-fragment.md",
            "Maintainer directive mentions only the fragment concur.\n",
        )

        result = self.run_helper("55", "META_RESOLVED:escalate-human:concurrency", "concurrency")

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
        self.controller = self.root / "controller_lib.sh"
        self.gh_log = self.root / "gh.log"
        self.git_log = self.root / "git.log"
        shutil.copy2(CONTROLLER_LIB, self.controller)
        shutil.copy2(REPO_SLUG_LIB, self.root / "repo_slug.sh")
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

    def _write_fake_gh(self, *, merge_exit: int, fact_json: str, body: str = "Closes #145\n", head: str = "refactor/issue145") -> None:
        fake_gh = self.root / "gh"
        fake_gh.write_text(
            f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_GH_LOG"
if [[ "$1 $2 $3" == "pr view 55" && "$*" == *"--json body"* ]]; then
  printf '%s\\n' {json.dumps(body)}
  exit 0
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
                "RECENT_PR_MERGE_RETRY_SLEEP_SECONDS": "0",
            }
        )
        return subprocess.run(
            ["bash", "-c", 'source "$CONTROLLER_LIB"; merge_pr "$@"', "bash", *args],
            env={**env, "CONTROLLER_LIB": str(self.controller)},
            text=True,
            capture_output=True,
            check=False,
        )

    def gh_calls(self) -> list[str]:
        if not self.gh_log.exists():
            return []
        return self.gh_log.read_text(encoding="utf-8").splitlines()

    def recent_merges(self) -> dict[str, object]:
        return json.loads((self.root / ".refactor-loop/state/recent-pr-merges.json").read_text(encoding="utf-8"))

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
        self.assertIn("pr merge 55 --repo test-owner/test-repo --admin --squash --delete-branch", calls)
        self.assertIn("pr edit 55 --repo test-owner/test-repo --remove-label 🚀 phase:pr-open --remove-label 👀 phase:reviewing --remove-label 🔧 phase:fixing --remove-label ⏸️ phase:blocked --remove-label auto-loop-stuck --remove-label 👤 human:需-maintainer-决策 --remove-label 🆘 human:卡死 --remove-label 🆘 human:卡死-需-rework --add-label 🎉 phase:merged", calls)
        joined_calls = "\n".join(calls)
        self.assertIn("issue close 145 --repo test-owner/test-repo --reason completed --comment", joined_calls)
        self.assertIn("Auto-merged via PR #55", joined_calls)

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
