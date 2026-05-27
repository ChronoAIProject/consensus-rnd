#!/bin/sh
"exec" "python3" "$0" "$@"

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
SKILL_ROOT = SCRIPT_PATH.parents[1]
REPO_ROOT = SCRIPT_PATH.parents[3]
PEEK = SKILL_ROOT / "scripts" / "peek.sh"


class PeekStatusLensBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.fakebin = self.root / "fakebin"
        self.logs = self.root / ".refactor-loop" / "logs"
        self.fakebin.mkdir(parents=True)
        self.logs.mkdir(parents=True)
        self.write_fake_git()
        self.write_fake_gh()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_fake_git(self) -> None:
        git = self.fakebin / "git"
        git.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                case "$1" in
                  fetch) exit 0 ;;
                  worktree) exit 0 ;;
                  rev-parse) printf '%s\\n' "$REPO_ROOT"; exit 0 ;;
                  *) exit 0 ;;
                esac
                """
            ),
            encoding="utf-8",
        )
        git.chmod(0o755)

    def write_fake_gh(self) -> None:
        gh = self.fakebin / "gh"
        gh.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                args="$*"
                pr="${PEEK_TEST_PR:-}"
                if [[ "$1 $2" == "issue list" ]]; then
                  printf '[]\\n'
                  exit 0
                fi
                if [[ "$1 $2" == "issue view" ]]; then
                  if [[ "$args" == *"--json state"* ]]; then
                    printf 'OPEN\\n'
                  else
                    printf '{"comments":[]}\\n'
                  fi
                  exit 0
                fi
                if [[ "$1 $2" == "pr list" ]]; then
                  if [[ -z "$pr" ]]; then
                    if [[ "$args" == *"--jq"* ]]; then exit 0; fi
                    printf '[]\\n'
                    exit 0
                  fi
                  if [[ "$args" == *"--state merged"* ]]; then
                    exit 0
                  fi
                  if [[ "$args" == *"--state closed"* ]]; then
                    printf '[]\\n'
                    exit 0
                  fi
                  if [[ "$args" == *"--jq .[].number"* || "$args" == *"--jq '.[].number'"* ]]; then
                    printf '%s\\n' "$pr"
                    exit 0
                  fi
                  if [[ "$args" == *"--jq .[]"* || "$args" == *"--jq '.[]'"* ]]; then
                    printf '{"number":%s,"title":"stub PR"}\\n' "$pr"
                    exit 0
                  fi
                  printf '[{"number":%s,"title":"stub PR","labels":[]}]\\n' "$pr"
                  exit 0
                fi
                if [[ "$1 $2" == "pr checks" ]]; then
                  if [[ "$args" == *'bucket=="fail"'* || "$args" == *'bucket==\\"fail\\"'* ]]; then printf '0\\n'; exit 0; fi
                  if [[ "$args" == *'bucket=="pending"'* || "$args" == *'bucket==\\"pending\\"'* ]]; then printf '0\\n'; exit 0; fi
                  if [[ "$args" == *'bucket=="pass"'* || "$args" == *'bucket==\\"pass\\"'* ]]; then printf '3\\n'; exit 0; fi
                  printf '0\\n'
                  exit 0
                fi
                if [[ "$1 $2" == "pr view" ]]; then
                  if [[ "$args" == *"--json comments"* ]]; then
                    printf '{"comments":[]}\\n'
                  else
                    printf 'CLEAN\\n'
                  fi
                  exit 0
                fi
                printf '[]\\n'
                exit 0
                """
            ),
            encoding="utf-8",
        )
        gh.chmod(0o755)

    def run_peek(self, *, pr: int | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update(
            {
                "REPO_ROOT": str(self.root),
                "PATH": f"{self.fakebin}{os.pathsep}{env.get('PATH', '')}",
                "GH_REPO_SLUG": "owner/repo",
            }
        )
        if pr is not None:
            env["PEEK_TEST_PR"] = str(pr)
        return subprocess.run(
            ["bash", str(PEEK)],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_peek_does_not_surface_generic_body_echo_markers(self) -> None:
        (self.logs / "phase9-issue87-r9-judge.log").write_text(
            "prompt echo META_JUDGE_DONE:converge:round-9:body-echo\n"
            "real work\n"
            "META_JUDGE_DONE:consensus:structural:real\n"
            "EXIT=0\n",
            encoding="utf-8",
        )

        result = self.run_peek()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("round-9:body-echo", result.stdout)
        self.assertNotIn("marker → 推荐下一步", result.stdout)
        self.assertNotIn("→ implement codex", result.stdout)
        self.assertNotIn("→ re-spawn", result.stdout)

    def test_peek_shows_phase9_ledger_and_pending_as_facts(self) -> None:
        (self.root / ".refactor-loop" / "phase9-router-ledger.jsonl").write_text(
            '{"key":"k1","marker":"SOLVER_DONE:minimal:propose","log_path":"a.log"}\n',
            encoding="utf-8",
        )
        (self.root / ".refactor-loop" / ".controller-pending-events.log").write_text(
            "phase9-router-fallback unknown marker fact\n",
            encoding="utf-8",
        )

        result = self.run_peek()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Phase 9 router / pending events", result.stdout)
        self.assertIn('"key":"k1"', result.stdout)
        self.assertIn("phase9-router-fallback unknown marker fact", result.stdout)
        self.assertNotIn("推荐下一步", result.stdout)
        self.assertNotIn("→ implement codex", result.stdout)

    def test_peek_review_merge_readiness_uses_tail_only_review_done(self) -> None:
        pr = 123
        (self.logs / f"review-pr{pr}-architect-r1.log").write_text(
            f"REVIEW_DONE:{pr}:architect:approve\nEXIT=0\n",
            encoding="utf-8",
        )
        (self.logs / f"review-pr{pr}-tests-r1.log").write_text(
            f"REVIEW_DONE:{pr}:tests:approve\nEXIT=0\n",
            encoding="utf-8",
        )
        body_lines = "\n".join(f"body filler {i}" for i in range(40))
        (self.logs / f"review-pr{pr}-quality-r1.log").write_text(
            f"prompt echo REVIEW_DONE:{pr}:quality:approve\n"
            f"{body_lines}\n"
            f"REVIEW_DONE:{pr}:quality:comment\n"
            "EXIT=0\n",
            encoding="utf-8",
        )

        result = self.run_peek(pr=pr)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MERGE_READY approve=2 comment=1 reject=0", result.stdout)
        self.assertNotIn("MERGE_READY approve=3 comment=0 reject=0", result.stdout)


if __name__ == "__main__":
    unittest.main()
