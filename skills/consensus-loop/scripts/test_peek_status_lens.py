#!/bin/sh
"exec" "python3" "$0" "$@"

from __future__ import annotations

import json
import os
import json
import io
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


SCRIPT_PATH = Path(__file__).resolve()
SKILL_ROOT = SCRIPT_PATH.parents[1]
REPO_ROOT = SCRIPT_PATH.parents[3]
PEEK = SKILL_ROOT / "scripts" / "consensus-rnd-cli"
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from codex_refactor_loop import peek


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
        self.git_should_fail = False
        self.gh_should_fail = False
        self.write_fake_git()
        self.write_fake_gh()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_fake_git(self, *, fail: bool = False) -> None:
        git = self.fakebin / "git"
        self.git_should_fail = fail
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
                  if [[ "${PEEK_TEST_STALE_WORKTREE:-}" == "1" ]]; then
                    printf 'worktree %s/.worktrees/impl-issue332\nbranch refs/heads/impl/issue332-peek-fact-only\n\n' "$REPO_ROOT"
                  fi
                  exit 0
                fi
                if [[ "$args" == *"ls-remote --exit-code --heads origin impl/issue332-peek-fact-only"* ]]; then exit 2; fi
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
        self.gh_should_fail = fail
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
                  if [[ "${PEEK_TEST_CLOSED_LABEL_FIXTURES:-}" == "1" && "$args" == *"--state closed"* ]]; then
                    if [[ "$args" != *"--label crnd:lifecycle:managed"* ]]; then
                      printf 'dirty closed query must prove managed membership: %s\n' "$args" >&2
                      exit 45
                    fi
                    if [[ "$args" == *'--search label:"crnd:phase:reviewing"'* ]]; then
                      printf '[{"number":301,"state":"CLOSED","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]},{"number":302,"state":"CLOSED","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:closed"},{"name":"crnd:human:auto"}]},{"number":305,"state":"CLOSED","labels":[{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
                      exit 0
                    fi
                    if [[ "$args" == *'--search label:"crnd:lifecycle:stuck"'* ]]; then
                      printf '[{"number":301,"state":"CLOSED","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:lifecycle:stuck"},{"name":"crnd:human:auto"}]}]\n'
                      exit 0
                    fi
                    if [[ "$args" == *"--label crnd:lifecycle:managed"* ]]; then
                      if [[ "$args" != *"--limit 20"* ]]; then
                        printf 'closed managed query must use bounded recent window: %s\n' "$args" >&2
                        exit 44
                      fi
                      printf '[{"number":302,"state":"CLOSED","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:closed"},{"name":"crnd:human:auto"}]}]\n'
                      exit 0
                    fi
                    printf '[]\n'
                    exit 0
                  fi
                  if [[ "${PEEK_TEST_PR_OPEN_ISSUE:-}" == "1" ]]; then
                    if [[ "$args" == *"--label crnd:lifecycle:managed"* ]]; then
                      printf '[{"number":239,"title":"parent issue","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:pr-open"},{"name":"crnd:human:auto"}]}]\n'
                      exit 0
                    fi
                    printf '[]\n'
                    exit 0
                  fi
                  if [[ "${PEEK_TEST_REPRESENTED_PARENT:-}" == "1" ]]; then
                    if [[ "$args" == *"--label crnd:lifecycle:managed"* ]]; then
                      printf '[{"number":239,"title":"represented parent","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:implementing"},{"name":"crnd:human:auto"}]}]\n'
                      exit 0
                    fi
                    printf '[]\n'
                    exit 0
                  fi
                  if [[ "${PEEK_TEST_MILESTONE_FIXTURES:-}" == "1" ]]; then
                    if [[ "$args" == *"--label crnd:milestone:current"* ]]; then
                      printf '[{"number":20,"title":"milestone issue","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:milestone:current"},{"name":"crnd:phase:design-solving"},{"name":"crnd:human:auto"}]}]\n'
                      exit 0
                    fi
                    if [[ "$args" == *"--jq"* ]]; then
                      printf '  • #10 labels=[crnd:phase:design-solving] — ordinary issue\n'
                      exit 0
                    fi
                    printf '[{"number":10,"title":"ordinary issue","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:design-solving"},{"name":"crnd:human:auto"}]}]\n'
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
                  if [[ "${PEEK_TEST_CLOSED_LABEL_FIXTURES:-}" == "1" && "$args" == *"--state closed"* ]]; then
                    if [[ "$args" != *"--label crnd:lifecycle:managed"* ]]; then
                      printf 'dirty closed query must prove managed membership: %s\n' "$args" >&2
                      exit 45
                    fi
                    if [[ "$args" == *'--search label:"crnd:phase:fixing"'* ]]; then
                      printf '[{"number":303,"state":"CLOSED","mergedAt":null,"labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:fixing"},{"name":"crnd:human:auto"}]},{"number":306,"state":"CLOSED","mergedAt":null,"labels":[{"name":"crnd:phase:fixing"},{"name":"crnd:human:auto"}]}]\n'
                      exit 0
                    fi
                    if [[ "$args" == *'--search label:"crnd:lifecycle:stuck"'* ]]; then
                      printf '[{"number":303,"state":"CLOSED","mergedAt":null,"labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:fixing"},{"name":"crnd:lifecycle:stuck"},{"name":"crnd:human:auto"}]}]\n'
                      exit 0
                    fi
                    if [[ "$args" == *"--label crnd:lifecycle:managed"* ]]; then
                      if [[ "$args" != *"--limit 20"* ]]; then
                        printf 'closed managed query must use bounded recent window: %s\n' "$args" >&2
                        exit 44
                      fi
                      printf '[{"number":304,"state":"CLOSED","mergedAt":null,"labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:closed"},{"name":"crnd:human:auto"}]}]\n'
                      exit 0
                    fi
                    printf '[]\n'
                    exit 0
                  fi
                  if [[ "${PEEK_TEST_MILESTONE_FIXTURES:-}" == "1" ]]; then
                    if [[ "$args" == *"--label crnd:milestone:current"* ]]; then
                      printf '[{"number":30,"title":"milestone PR","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:milestone:current"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
                      exit 0
                    fi
                    if [[ "$args" == *"--state closed"* || "$args" == *"--state merged"* ]]; then
                      printf '[]\n'
                      exit 0
                    fi
                  fi
                  if [[ "$args" == *"--state merged"* ]]; then
                    exit 0
                  fi
                  if [[ "$args" == *"--state closed"* ]]; then
                    printf '[]\\n'
                    exit 0
                  fi
                  if [[ "${PEEK_TEST_REPRESENTED_PARENT:-}" == "1" ]]; then
                    if [[ "$args" == *"--label crnd:lifecycle:managed"* ]]; then
                      printf '[{"number":255,"title":"child PR","headRefName":"impl/issue239","body":"Closes #239","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
                      exit 0
                    fi
                    printf '[]\n'
                    exit 0
                  fi
                  if [[ "${PEEK_TEST_MISSING_LINK_PR:-}" == "1" ]]; then
                    if [[ "$args" == *"--label crnd:lifecycle:managed"* ]]; then
                      printf '[{"number":256,"title":"missing link PR","headRefName":"impl/missing","body":"","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
                      exit 0
                    fi
                    printf '[]\n'
                    exit 0
                  fi
                  if [[ -z "$pr" ]]; then
                    if [[ "$args" == *"--jq"* ]]; then exit 0; fi
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
                    printf '[{"number":%s,"title":"stub PR","headRefName":"refactor/iter%s-worker","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\\n' "$pr" "$pr"
                    exit 0
                  fi
                  printf '[{"number":%s,"title":"stub PR","labels":[]}]\\n' "$pr"
                  exit 0
                fi
                if [[ "$1" == "api" ]]; then
                  if [[ "$3" == "--paginate" && "$4" == "--slurp" ]]; then
                    printf '[{"check_runs":[{"name":"unit","status":"completed","conclusion":"success"},{"name":"lint","status":"completed","conclusion":"success"},{"name":"types","status":"completed","conclusion":"success"}]}]\n'
                    exit 0
                  fi
                  printf '{"head":{"sha":"peek-sha"}}\n'
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
        stale_worktree: bool = False,
        pr_open_issue: bool = False,
        represented_parent: bool = False,
        missing_link_pr: bool = False,
        closed_label_fixtures: bool = False,
        host_work_language: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.pop("CONSENSUS_RND_HOST_ENV", None)
        env.pop("HOST_WORK_LANGUAGE", None)
        env.update(
            {
                "REPO_ROOT": str(self.root),
                "PATH": f"{self.fakebin}{os.pathsep}{env.get('PATH', '')}",
                "GH_REPO_SLUG": "owner/repo",
            }
        )
        env.pop("CONSENSUS_RND_HOST_ENV", None)
        if pr is not None:
            env["PEEK_TEST_PR"] = str(pr)
        if milestone_fixtures:
            env["PEEK_TEST_MILESTONE_FIXTURES"] = "1"
        if unpushed:
            env["PEEK_TEST_UNPUSHED"] = "1"
        if stale_worktree:
            env["PEEK_TEST_STALE_WORKTREE"] = "1"
        if pr_open_issue:
            env["PEEK_TEST_PR_OPEN_ISSUE"] = "1"
        if represented_parent:
            env["PEEK_TEST_REPRESENTED_PARENT"] = "1"
        if missing_link_pr:
            env["PEEK_TEST_MISSING_LINK_PR"] = "1"
        if closed_label_fixtures:
            env["PEEK_TEST_CLOSED_LABEL_FIXTURES"] = "1"
        if host_work_language is not None:
            env["HOST_WORK_LANGUAGE"] = host_work_language
        self.write_managed_work_snapshot(
            pr=pr,
            milestone_fixtures=milestone_fixtures,
            unpushed=unpushed,
            pr_open_issue=pr_open_issue,
            represented_parent=represented_parent,
            missing_link_pr=missing_link_pr,
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("os.getcwd", return_value=str(self.root)):
                with mock.patch("codex_refactor_loop.context.subprocess.run", side_effect=self.fake_subprocess_run):
                    with mock.patch("codex_refactor_loop.peek.subprocess.run", side_effect=self.fake_subprocess_run):
                        with mock.patch("codex_refactor_loop.pr_checks.subprocess.run", side_effect=self.fake_subprocess_run):
                            with mock.patch("codex_refactor_loop.wakeup_plan.subprocess.run", side_effect=self.fake_subprocess_run):
                                with redirect_stdout(stdout), redirect_stderr(stderr):
                                    try:
                                        returncode = peek.main(args or [])
                                    except SystemExit as exc:
                                        returncode = int(exc.code or 0) if isinstance(exc.code, int) else 1
        return subprocess.CompletedProcess(
            [sys.executable, str(PEEK), "peek", *(args or [])],
            returncode,
            stdout.getvalue(),
            stderr.getvalue(),
        )

    def fake_subprocess_run(self, command, **_kwargs):
        argv = [str(part) for part in command]
        if argv[:2] == ["git", "rev-parse"] and "--show-toplevel" in argv:
            return subprocess.CompletedProcess(argv, 0, f"{self.root}\n", "")
        if argv and argv[0] == "git" and "-C" in argv:
            return self.fake_git(argv)
        if argv and argv[0] == "gh":
            return self.fake_gh(argv)
        if len(argv) >= 3 and argv[0] == sys.executable and argv[2] == "concurrency":
            if "--count-only" in argv:
                return subprocess.CompletedProcess(argv, 0, "0\n", "")
            if "--list-codex" in argv:
                return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def fake_git(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        if self.git_should_fail:
            self.fail(f"unexpected git call: {' '.join(argv)}")
        args_start = argv.index("-C") + 2 if "-C" in argv else 1
        args = " ".join(argv[args_start:])
        if "fetch origin --quiet" in args:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if "worktree list --porcelain" in args:
            stdout = ""
            if os.environ.get("PEEK_TEST_UNPUSHED") == "1":
                stdout += (
                    f"worktree {self.root}/.worktrees/pr{os.environ.get('PEEK_TEST_PR')}\n"
                    f"branch refs/heads/refactor/iter{os.environ.get('PEEK_TEST_PR')}-worker\n\n"
                )
            if os.environ.get("PEEK_TEST_STALE_WORKTREE") == "1":
                stdout += f"worktree {self.root}/.worktrees/impl-issue332\nbranch refs/heads/impl/issue332-peek-fact-only\n\n"
            return subprocess.CompletedProcess(argv, 0, stdout, "")
        if "ls-remote --exit-code --heads origin impl/issue332-peek-fact-only" in args:
            return subprocess.CompletedProcess(argv, 2, "", "")
        if "rev-parse --verify HEAD" in args:
            return subprocess.CompletedProcess(argv, 0, "local-sha\n", "")
        if "rev-parse --verify refs/remotes/origin/refactor/iter" in args:
            return subprocess.CompletedProcess(argv, 0, "remote-sha\n", "")
        if "rev-list --count refs/remotes/origin/refactor/iter" in args:
            return subprocess.CompletedProcess(argv, 0, "3\n", "")
        if "rev-parse" in args:
            return subprocess.CompletedProcess(argv, 0, f"{self.root}\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def fake_gh(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        if self.gh_should_fail:
            self.fail(f"unexpected gh call: {' '.join(argv)}")
        args = " ".join(argv)
        pr = os.environ.get("PEEK_TEST_PR", "")
        if argv[1:3] == ["issue", "list"]:
            return subprocess.CompletedProcess(argv, 0, self.fake_issue_list(args), "")
        if argv[1:3] == ["issue", "view"]:
            stdout = "OPEN\n" if "--json state" in args else '{"comments":[]}\n'
            return subprocess.CompletedProcess(argv, 0, stdout, "")
        if argv[1:3] == ["pr", "list"]:
            return subprocess.CompletedProcess(argv, 0, self.fake_pr_list(args, pr), "")
        if argv[1:3] == ["pr", "view"]:
            stdout = '{"comments":[]}\n' if "--json comments" in args else "CLEAN\n"
            return subprocess.CompletedProcess(argv, 0, stdout, "")
        if argv[1:2] == ["api"]:
            if "check-runs" in args:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    '[{"check_runs":[{"name":"unit","status":"completed","conclusion":"success"},{"name":"lint","status":"completed","conclusion":"success"},{"name":"types","status":"completed","conclusion":"success"}]}]\n',
                    "",
                )
            return subprocess.CompletedProcess(argv, 0, '{"head":{"sha":"peek-sha"}}\n', "")
        return subprocess.CompletedProcess(argv, 0, "[]\n", "")

    def fake_issue_list(self, args: str) -> str:
        if os.environ.get("PEEK_TEST_CLOSED_LABEL_FIXTURES") == "1" and "--state closed" in args:
            if "--label crnd:lifecycle:managed" not in args:
                self.fail(f"dirty closed query must prove managed membership: {args}")
            if '--search label:"crnd:phase:reviewing"' in args:
                return '[{"number":301,"state":"CLOSED","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]},{"number":302,"state":"CLOSED","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:closed"},{"name":"crnd:human:auto"}]},{"number":305,"state":"CLOSED","labels":[{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
            if '--search label:"crnd:lifecycle:stuck"' in args:
                return '[{"number":301,"state":"CLOSED","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:lifecycle:stuck"},{"name":"crnd:human:auto"}]}]\n'
            if "--label crnd:lifecycle:managed" in args and "--search" not in args:
                if "--limit 20" not in args:
                    self.fail(f"closed managed query must use bounded recent window: {args}")
                return '[{"number":302,"state":"CLOSED","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:closed"},{"name":"crnd:human:auto"}]}]\n'
            return "[]\n"
        if os.environ.get("PEEK_TEST_PR_OPEN_ISSUE") == "1":
            if "--label crnd:lifecycle:managed" in args:
                return '[{"number":239,"title":"parent issue","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:pr-open"},{"name":"crnd:human:auto"}]}]\n'
            return "[]\n"
        if os.environ.get("PEEK_TEST_REPRESENTED_PARENT") == "1":
            if "--label crnd:lifecycle:managed" in args:
                return '[{"number":239,"title":"represented parent","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:implementing"},{"name":"crnd:human:auto"}]}]\n'
            return "[]\n"
        if os.environ.get("PEEK_TEST_MILESTONE_FIXTURES") == "1":
            if "--label crnd:milestone:current" in args:
                return '[{"number":20,"title":"milestone issue","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:milestone:current"},{"name":"crnd:phase:design-solving"},{"name":"crnd:human:auto"}]}]\n'
            if "--jq" in args:
                return "  • #10 labels=[crnd:phase:design-solving] — ordinary issue\n"
            return '[{"number":10,"title":"ordinary issue","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:design-solving"},{"name":"crnd:human:auto"}]}]\n'
        return "[]\n"

    def fake_pr_list(self, args: str, pr: str) -> str:
        if os.environ.get("PEEK_TEST_CLOSED_LABEL_FIXTURES") == "1" and "--state closed" in args:
            if "--label crnd:lifecycle:managed" not in args:
                self.fail(f"dirty closed query must prove managed membership: {args}")
            if '--search label:"crnd:phase:fixing"' in args:
                return '[{"number":303,"state":"CLOSED","mergedAt":null,"labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:fixing"},{"name":"crnd:human:auto"}]},{"number":306,"state":"CLOSED","mergedAt":null,"labels":[{"name":"crnd:phase:fixing"},{"name":"crnd:human:auto"}]}]\n'
            if '--search label:"crnd:lifecycle:stuck"' in args:
                return '[{"number":303,"state":"CLOSED","mergedAt":null,"labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:fixing"},{"name":"crnd:lifecycle:stuck"},{"name":"crnd:human:auto"}]}]\n'
            if "--label crnd:lifecycle:managed" in args and "--search" not in args:
                if "--limit 20" not in args:
                    self.fail(f"closed managed query must use bounded recent window: {args}")
                return '[{"number":304,"state":"CLOSED","mergedAt":null,"labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:closed"},{"name":"crnd:human:auto"}]}]\n'
            return "[]\n"
        if os.environ.get("PEEK_TEST_MILESTONE_FIXTURES") == "1":
            if "--label crnd:milestone:current" in args:
                return '[{"number":30,"title":"milestone PR","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:milestone:current"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
            if "--state closed" in args or "--state merged" in args:
                return "[]\n"
        if "--state merged" in args:
            return ""
        if "--state closed" in args:
            return "[]\n"
        if os.environ.get("PEEK_TEST_REPRESENTED_PARENT") == "1":
            if "--label crnd:lifecycle:managed" in args:
                return '[{"number":255,"title":"child PR","headRefName":"impl/issue239","body":"Closes #239","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
            return "[]\n"
        if os.environ.get("PEEK_TEST_MISSING_LINK_PR") == "1":
            if "--label crnd:lifecycle:managed" in args:
                return '[{"number":256,"title":"missing link PR","headRefName":"impl/missing","body":"","labels":[{"name":"crnd:lifecycle:managed"},{"name":"crnd:phase:reviewing"},{"name":"crnd:human:auto"}]}]\n'
            return "[]\n"
        if not pr:
            return "" if "--jq" in args else "[]\n"
        if "--jq .[].number" in args or "--jq '.[].number'" in args:
            return f"{pr}\n"
        if "--jq .[]" in args or "--jq '.[]'" in args:
            return f'{{"number":{pr},"title":"stub PR"}}\n'
        if os.environ.get("PEEK_TEST_UNPUSHED") == "1":
            return f'[{{"number":{pr},"title":"stub PR","headRefName":"refactor/iter{pr}-worker","labels":[{{"name":"crnd:lifecycle:managed"}},{{"name":"crnd:phase:reviewing"}},{{"name":"crnd:human:auto"}}]}}]\n'
        return f'[{{"number":{pr},"title":"stub PR","labels":[]}}]\n'

    def write_managed_work_snapshot(
        self,
        *,
        pr: int | None = None,
        milestone_fixtures: bool = False,
        unpushed: bool = False,
        pr_open_issue: bool = False,
        represented_parent: bool = False,
        missing_link_pr: bool = False,
    ) -> None:
        items: list[dict[str, object]] = []

        def issue(number: int, title: str, labels: list[str]) -> None:
            items.append({"kind": "issue", "number": number, "title": title, "labels": labels, "state": "open", "updated_at": "2026-06-05T00:00:00Z"})

        def pr_item(number: int, title: str, labels: list[str], *, head_ref: str = "", body: str = "") -> None:
            items.append(
                {
                    "kind": "PR",
                    "number": number,
                    "title": title,
                    "labels": labels,
                    "head_ref": head_ref or None,
                    "head_sha": "peek-sha",
                    "body": body,
                    "state": "open",
                    "updated_at": "2026-06-05T00:00:00Z",
                }
            )

        if pr_open_issue:
            issue(239, "parent issue", ["crnd:lifecycle:managed", "crnd:phase:pr-open", "crnd:human:auto"])
        if represented_parent:
            issue(239, "represented parent", ["crnd:lifecycle:managed", "crnd:phase:implementing", "crnd:human:auto"])
            pr_item(255, "child PR", ["crnd:lifecycle:managed", "crnd:phase:reviewing", "crnd:human:auto"], head_ref="impl/issue239", body="Closes #239")
        if missing_link_pr:
            pr_item(256, "missing link PR", ["crnd:lifecycle:managed", "crnd:phase:reviewing", "crnd:human:auto"], head_ref="impl/missing")
        if milestone_fixtures:
            issue(20, "milestone issue", ["auto-loop", "🎯 milestone", "🔍 phase:design-solving"])
            issue(10, "ordinary issue", ["auto-loop", "🔍 phase:design-solving"])
            pr_item(30, "milestone PR", ["auto-loop", "🎯 milestone", "👀 phase:reviewing"])
        if pr is not None:
            labels = ["auto-loop", "👀 phase:reviewing"] if unpushed else []
            head_ref = f"refactor/iter{pr}-worker" if unpushed else ""
            pr_item(pr, "stub PR", labels, head_ref=head_ref)

        path = self.root / ".refactor-loop" / "state" / "managed-work-snapshot.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"fetched_at_epoch": time.time(), "items": items}) + "\n", encoding="utf-8")

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

    def test_peek_activity_timeline_merges_existing_tick_pending_and_ledger_facts(self) -> None:
        (self.logs / "concurrency-monitor.log").write_text(
            "[2026-06-01T00:00:01Z] concurrency: tick skip:graphql-backoff remaining=unknown\n",
            encoding="utf-8",
        )
        (self.root / ".refactor-loop" / ".controller-pending-events.log").write_text(
            "2026-06-01T00:00:02Z DISPATCH_BACKOFF:graphql-headroom-low\n",
            encoding="utf-8",
        )
        (self.root / ".refactor-loop" / "phase9-router-ledger.jsonl").write_text(
            json.dumps(
                {
                    "key": "491-4-judge",
                    "marker": "SOLVER_DONE:triplet",
                    "dispatched_at": "2026-06-01T00:00:03Z",
                    "route": "solver_triplet_to_judge",
                    "target_actor": "judge",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        result = self.run_peek()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("▍Activity timeline (read-only facts):", result.stdout)
        self.assertIn("2026-06-01T00:00:01Z concurrency: skip:graphql-backoff remaining=unknown", result.stdout)
        self.assertIn("2026-06-01T00:00:02Z pending-events: DISPATCH_BACKOFF:graphql-headroom-low", result.stdout)
        self.assertIn("2026-06-01T00:00:03Z phase9-ledger: key=491-4-judge route=solver_triplet_to_judge target=judge marker=SOLVER_DONE:triplet", result.stdout)
        self.assertNotIn("controller_action", result.stdout)
        self.assertNotIn("routing authorization", result.stdout)

    def test_peek_reuses_holistic_status_summary_renderer(self) -> None:
        result = self.run_peek(represented_parent=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("▍Holistic status:", result.stdout)
        self.assertIn("workers actual=", result.stdout)
        self.assertIn("issue #239 reason=represented-by-open-pr", result.stdout)
        source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "peek.py").read_text(encoding="utf-8")
        self.assertIn("render_peek_summary", source)
        self.assertIn("collect_holistic_status", source)

    def test_peek_does_not_render_degradation_alert_tail(self) -> None:
        alert = self.root / ".refactor-loop" / ".degradation-alert.log"
        alert.parent.mkdir(parents=True, exist_ok=True)
        alert.write_text("[2026-05-27T00:00:00Z] skill-degradation-alert returncode=1\n", encoding="utf-8")

        result = self.run_peek()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Skill degradation alerts", result.stdout)
        self.assertNotIn("skill-degradation-alert returncode=1", result.stdout)
        self.assertEqual(alert.read_text(encoding="utf-8"), "[2026-05-27T00:00:00Z] skill-degradation-alert returncode=1\n")

    def test_peek_does_not_render_review_gate_merge_decisions(self) -> None:
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
        self.assertIn("▍Consensus-rnd Phase design-consensus router / pending events:", result.stdout)
        self.assertIn("▍Open auto-loop PRs:", result.stdout)
        self.assertIn("PR #123 [CLEAN] CI: fail=0 pending=0 pass=3", result.stdout)
        self.assertIn("▍Unpushed worker output:", result.stdout)
        self.assertNotIn("Mergeable PRs", result.stdout)
        self.assertNotIn("MERGE_READY", result.stdout)
        self.assertNotIn("WAIT_EXPLICIT_APPROVAL", result.stdout)
        self.assertNotIn("gh pr merge", result.stdout)

    def test_peek_source_has_no_review_gate_merge_decision_surface(self) -> None:
        source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "peek.py").read_text(encoding="utf-8")

        for token in (
            "_mergeable_prs",
            "_latest_complete_review_round",
            "extract_review_verdict",
            "Mergeable PRs",
            "MERGE_READY",
            "WAIT_EXPLICIT_APPROVAL",
            "gh pr merge",
        ):
            with self.subTest(token=token):
                self.assertNotIn(token, source)

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

    def test_peek_stale_worktree_reports_fact_without_cleanup_command(self) -> None:
        result = self.run_peek(stale_worktree=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Stale worktree (remote branch missing; cleanup is controller-owned)", result.stdout)
        self.assertIn(f"path={self.root}/.worktrees/impl-issue332", result.stdout)
        self.assertIn("branch=impl/issue332-peek-fact-only", result.stdout)
        self.assertIn("remote_missing=true", result.stdout)
        self.assertIn("cleanup_owner=controller-runbook", result.stdout)
        self.assertIn("no_lifecycle_authority=true", result.stdout)
        for token in ("git worktree remove", "--force", "git branch -D", "&&", "suggested_command"):
            with self.subTest(token=token):
                self.assertNotIn(token, result.stdout)

    def test_peek_source_has_no_stale_worktree_lifecycle_cleanup_command(self) -> None:
        source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "peek.py").read_text(encoding="utf-8")
        executable_source = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))

        for token in ("git worktree remove", "--force", "git branch -D", "suggested_command"):
            with self.subTest(token=token):
                self.assertNotIn(token, executable_source)
        self.assertIn("_stale_worktrees", source)
        self.assertIn('"worktree", "list", "--porcelain"', source)
        self.assertIn('"ls-remote", "--exit-code", "--heads"', source)
        self.assertIn("no_lifecycle_authority", source)

    def test_peek_closed_label_projection_has_no_remediation_text(self) -> None:
        text = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "peek.py").read_text(encoding="utf-8")

        self.assertIn("plan_closed_reconcile_candidate", text)
        self.assertIn("closed_reconcile_candidate_queries", text)
        self.assertIn("terminal=", text)
        self.assertNotIn("controller should clean up", text)
        self.assertNotIn("gh issue edit", text)
        self.assertNotIn("gh pr edit", text)

    def test_peek_stale_label_lens_uses_dirty_candidate_projection(self) -> None:
        result = self.run_peek(closed_label_fixtures=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("closed issue #301", result.stdout)
        self.assertIn("closed pr #303", result.stdout)
        self.assertNotIn("closed issue #302", result.stdout)
        self.assertNotIn("closed pr #304", result.stdout)
        self.assertNotIn("closed issue #305", result.stdout)
        self.assertNotIn("closed pr #306", result.stdout)
        self.assertNotIn("closed managed query must use bounded recent window", result.stderr)
        self.assertNotIn("dirty closed query must prove managed membership", result.stderr)

    def test_peek_counts_codex_via_canonical_monitor_cli(self) -> None:
        text = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "peek.py").read_text(encoding="utf-8")

        self.assertIn('"concurrency", "--count-only"', text)
        self.assertIn('"concurrency", "--list-codex"', text)
        self.assertNotIn("ps -ef | awk", text)
        self.assertNotIn("ps -eo command= | awk", text)

    def test_peek_activity_timeline_is_status_lens_only(self) -> None:
        text = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "peek.py").read_text(encoding="utf-8")
        copy_text = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "runtime_copy.py").read_text(encoding="utf-8")

        self.assertIn("_activity_timeline", text)
        self.assertIn("Activity timeline (read-only facts)", copy_text)
        self.assertIn("_phase9_ledger_facts", text)
        self.assertIn("_pending_event_facts", text)
        self.assertNotIn("routing authorization", text)
        self.assertNotIn("controller_action", text[text.index("def _activity_timeline") : text.index("def _maintainer_comments")])

    def test_peek_lists_milestone_items_before_ordinary_open_issues(self) -> None:
        result = self.run_peek(milestone_fixtures=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("▍Milestone (priority) issues:", result.stdout)
        self.assertIn("issue #20", result.stdout)
        self.assertIn("PR #30", result.stdout)
        milestone_index = result.stdout.index("▍Milestone (priority) issues:")
        ordinary_index = result.stdout.index("▍Open auto-loop issues:")
        self.assertLess(milestone_index, ordinary_index)
        self.assertLess(result.stdout.index("issue #20"), result.stdout.index("#10 labels="))

    def test_peek_zh_work_language_preserves_milestone_heading(self) -> None:
        result = self.run_peek(milestone_fixtures=True, host_work_language="zh")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("▍Milestone (优先) issues:", result.stdout)

    def test_peek_keeps_projection_backed_linkage_mismatch_lens(self) -> None:
        source = (SKILL_ROOT / "scripts" / "codex_refactor_loop" / "peek.py").read_text(encoding="utf-8")

        result = self.run_peek(missing_link_pr=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Issue/PR linkage mismatch", result.stdout)
        self.assertIn("PR #256 has no `Closes #N` parent link", result.stdout)
        self.assertIn("ManagedWorkProjection", source)

    def test_peek_drift_treats_pr_open_parent_issue_as_non_action(self) -> None:
        result = self.run_peek(pr_open_issue=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("issue #239 label=crnd:phase:pr-open but 0 codex", result.stdout)
        self.assertNotIn("label=crnd:phase:pr-open but 0 codex", result.stdout)
        self.assertNotIn("has no `Closes #N` parent link", result.stdout)

    def test_peek_drift_skips_represented_parent_issue(self) -> None:
        result = self.run_peek(represented_parent=True)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("issue #239 label=crnd:phase:implementing but 0 codex", result.stdout)
        self.assertIn("pr #255 label=crnd:phase:reviewing but 0 codex", result.stdout)


if __name__ == "__main__":
    unittest.main()
