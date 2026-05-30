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
PEEK = SKILL_ROOT / "scripts" / "consensus-rnd-cli"


class PeekStatusLensBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.fakebin = self.root / "fakebin"
        self.logs = self.root / ".refactor-loop" / "logs"
        self.runs = self.root / ".refactor-loop" / "runs"
        self.fakebin.mkdir(parents=True)
        self.logs.mkdir(parents=True)
        self.runs.mkdir(parents=True)
        self.write_fake_git()
        self.write_fake_gh()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_fake_git(self, *, fail: bool = False) -> None:
        git = self.fakebin / "git"
        if fail:
            git.write_text("#!/usr/bin/env bash\nprintf 'unexpected git call\\n' >&2\nexit 42\n", encoding="utf-8")
            git.chmod(0o755)
            return
        git.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                args="$*"
                if [[ "$args" == *"fetch origin --quiet"* ]]; then exit 0; fi
                if [[ "$args" == *"worktree list --porcelain"* ]]; then
                  if [[ "${PEEK_TEST_UNPUSHED:-}" == "1" ]]; then
                    printf 'worktree %s/.worktrees/pr%s\nbranch refs/heads/refactor/iter%s-worker\n\n' "$REPO_ROOT" "$PEEK_TEST_PR" "$PEEK_TEST_PR"
                  fi
                  exit 0
                fi
                if [[ "$args" == *"rev-parse --verify HEAD"* ]]; then printf 'local-sha\n'; exit 0; fi
                if [[ "$args" == *"rev-parse --verify refs/remotes/origin/refactor/iter"* ]]; then printf 'remote-sha\n'; exit 0; fi
                if [[ "$args" == *"rev-list --count refs/remotes/origin/refactor/iter"* ]]; then printf '3\n'; exit 0; fi
                if [[ "$1" == "rev-parse" ]]; then printf '%s\\n' "$REPO_ROOT"; exit 0; fi
                exit 0
                """
            ).lstrip(),
            encoding="utf-8",
        )
        git.chmod(0o755)

    def write_fake_gh(self, *, fail: bool = False) -> None:
        gh = self.fakebin / "gh"
        if fail:
            gh.write_text("#!/usr/bin/env bash\nprintf 'unexpected gh call\\n' >&2\nexit 43\n", encoding="utf-8")
            gh.chmod(0o755)
            return
        gh.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                args="$*"
                pr="${PEEK_TEST_PR:-}"
                if [[ "$1 $2" == "issue list" ]]; then
                  if [[ "${PEEK_TEST_MILESTONE_FIXTURES:-}" == "1" ]]; then
                    if [[ "$args" == *"--label 🎯 milestone"* ]]; then
                      printf '[{"number":20,"title":"milestone issue","labels":[{"name":"auto-loop"},{"name":"🎯 milestone"},{"name":"🔍 phase:design-solving"}]}]\n'
                      exit 0
                    fi
                    if [[ "$args" == *"--jq"* ]]; then
                      printf '  • #10 labels=[🔍 phase:design-solving] — ordinary issue\n'
                      exit 0
                    fi
                    printf '[{"number":10,"title":"ordinary issue","labels":[{"name":"auto-loop"},{"name":"🔍 phase:design-solving"}]}]\n'
                    exit 0
                  fi
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
                  if [[ "${PEEK_TEST_MILESTONE_FIXTURES:-}" == "1" ]]; then
                    if [[ "$args" == *"--label 🎯 milestone"* ]]; then
                      printf '[{"number":30,"title":"milestone PR","labels":[{"name":"auto-loop"},{"name":"🎯 milestone"},{"name":"👀 phase:reviewing"}]}]\n'
                      exit 0
                    fi
                    if [[ "$args" == *"--state closed"* || "$args" == *"--state merged"* ]]; then
                      printf '[]\n'
                      exit 0
                    fi
                  fi
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
                  if [[ "${PEEK_TEST_UNPUSHED:-}" == "1" ]]; then
                    printf '[{"number":%s,"title":"stub PR","author":{"login":"alice"},"updatedAt":"2026-05-29T23:00:00Z","headRefName":"refactor/iter%s-worker","labels":[{"name":"auto-loop"},{"name":"👀 phase:reviewing"}]}]\\n' "$pr" "$pr"
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
                  elif [[ "$args" == *"--json author,updatedAt"* ]]; then
                    printf '{"author":{"login":"alice"},"updatedAt":"2026-05-29T23:00:00Z"}\\n'
                  else
                    printf 'CLEAN\\n'
                  fi
                  exit 0
                fi
                if [[ "$1 $2" == "api user" ]]; then
                  printf '{"login":"alice"}\\n'
                  exit 0
                fi
                printf '[]\\n'
                exit 0
                """
            ).lstrip(),
            encoding="utf-8",
        )
        gh.chmod(0o755)

    def run_peek(
        self,
        args: list[str] | None = None,
        *,
        pr: int | None = None,
        milestone_fixtures: bool = False,
        unpushed: bool = False,
    ) -> subprocess.CompletedProcess[str]:
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
        if milestone_fixtures:
            env["PEEK_TEST_MILESTONE_FIXTURES"] = "1"
        if unpushed:
            env["PEEK_TEST_UNPUSHED"] = "1"
        return subprocess.run(
            [sys.executable, str(PEEK), "peek", *(args or [])],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_peek_help_is_bounded_and_does_not_load_live_status(self) -> None:
        self.write_fake_git(fail=True)
        self.write_fake_gh(fail=True)

        result = self.run_peek(["--help"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)
        self.assertIn("status lens", result.stdout)
        self.assertNotIn("═══════════════", result.stdout)
        self.assertNotIn("▍", result.stdout)
        self.assertFalse((self.root / ".refactor-loop" / "phase9-router-ledger.jsonl").exists())
        self.assertFalse((self.root / ".refactor-loop" / "state" / "statusline-snapshot.json").exists())

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
        self.assertIn("Consensus-rnd Phase design-consensus router / pending events", result.stdout)
        self.assertIn('"key":"k1"', result.stdout)
        self.assertIn("phase9-router-fallback unknown marker fact", result.stdout)
        self.assertNotIn("推荐下一步", result.stdout)
        self.assertNotIn("→ implement codex", result.stdout)

    def test_peek_shows_degradation_alert_tail_read_only(self) -> None:
        alert = self.root / ".refactor-loop" / ".degradation-alert.log"
        alert.parent.mkdir(parents=True, exist_ok=True)
        alert.write_text("[2026-05-27T00:00:00Z] skill-degradation-alert returncode=1\n", encoding="utf-8")

        result = self.run_peek()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Skill degradation alerts", result.stdout)
        self.assertIn("skill-degradation-alert returncode=1", result.stdout)
        self.assertEqual(alert.read_text(encoding="utf-8"), "[2026-05-27T00:00:00Z] skill-degradation-alert returncode=1\n")

    def test_peek_review_merge_readiness_uses_tail_only_review_done(self) -> None:
        # Refactor (iter205/issue-205):
        #   Old pattern: dogfood 实测的运维经验(audit 并行撞 iteration 号、新 role prompt 漏注册 marker contract、review verdict grep log tail 误判、daemon 恢复手 kill)只靠 agent 记忆,没落进 skill 合同与机械验证。
        #   New principle: 把四条经验写回局部合同:SKILL.md 增 #205 反面规则段(audit 同一时刻单 active iteration、新 role prompt 必同步 marker inventory、review verdict 权威源优先 review artifact frontmatter、daemon 恢复只走 restart-daemons);audit.md 渲染后 ITERATION 空则 fail-closed;peek.py 局部优先读 review artifact frontmatter verdict;配套 source-regression + behavior test。不新增跨模块抽象层。
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

    def test_peek_review_merge_readiness_prefers_artifact_frontmatter_verdict(self) -> None:
        # Refactor (iter205/issue-205):
        #   Old pattern: dogfood 实测的运维经验(audit 并行撞 iteration 号、新 role prompt 漏注册 marker contract、review verdict grep log tail 误判、daemon 恢复手 kill)只靠 agent 记忆,没落进 skill 合同与机械验证。
        #   New principle: 把四条经验写回局部合同:SKILL.md 增 #205 反面规则段(audit 同一时刻单 active iteration、新 role prompt 必同步 marker inventory、review verdict 权威源优先 review artifact frontmatter、daemon 恢复只走 restart-daemons);audit.md 渲染后 ITERATION 空则 fail-closed;peek.py 局部优先读 review artifact frontmatter verdict;配套 source-regression + behavior test。不新增跨模块抽象层。
        pr = 124
        for role in ("architect", "tests", "quality"):
            (self.logs / f"review-pr{pr}-{role}-r1.log").write_text(
                f"quoted prompt REVIEW_DONE:{pr}:{role}:approve\n"
                f"REVIEW_DONE:{pr}:{role}:reject\n"
                "EXIT=0\n",
                encoding="utf-8",
            )
            (self.runs / f"review-pr{pr}-{role}-r1.md").write_text(
                "---\n"
                f"pr: {pr}\n"
                f"role: {role}\n"
                "verdict: approve\n"
                "---\n"
                "\n"
                f"Body may quote REVIEW_DONE:{pr}:{role}:reject without authority.\n",
                encoding="utf-8",
            )

        result = self.run_peek(pr=pr)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MERGE_READY approve=3 comment=0 reject=0", result.stdout)
        self.assertNotIn("reject=3", result.stdout)

    def test_peek_displays_unpushed_worker_output_as_status_only(self) -> None:
        result = self.run_peek(pr=77, unpushed=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("▍Unpushed worker output:", result.stdout)
        self.assertIn("UNPUSHED_WORKER_OUTPUT:77:3", result.stdout)
        self.assertIn("head=refactor/iter77-worker", result.stdout)
        self.assertIn("controller_action=safe_push", result.stdout)
        self.assertIn("no_lifecycle_authority=true", result.stdout)
        self.assertNotIn("consensus-rnd-cli safe-push", result.stdout)
        self.assertNotIn("safe-push origin refactor/iter77-worker", result.stdout)
        self.assertNotIn('"actions"', result.stdout)
        self.assertNotIn('"schema": "wakeup-plan"', result.stdout)

    def test_peek_counts_codex_via_canonical_monitor_cli(self) -> None:
        text = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "peek.py").read_text(encoding="utf-8")

        self.assertIn('"concurrency", "--count-only"', text)
        self.assertIn('"concurrency", "--list-codex"', text)
        self.assertNotIn("ps -ef | awk", text)
        self.assertNotIn("ps -eo command= | awk", text)

    def test_peek_lists_milestone_items_before_ordinary_open_issues(self) -> None:
        result = self.run_peek(milestone_fixtures=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("▍Milestone (优先) issues:", result.stdout)
        self.assertIn("issue #20", result.stdout)
        self.assertIn("PR #30", result.stdout)
        milestone_index = result.stdout.index("▍Milestone (优先) issues:")
        ordinary_index = result.stdout.index("▍Open auto-loop issues:")
        self.assertLess(milestone_index, ordinary_index)
        self.assertLess(result.stdout.index("issue #20"), result.stdout.index("#10 labels="))


if __name__ == "__main__":
    unittest.main()
