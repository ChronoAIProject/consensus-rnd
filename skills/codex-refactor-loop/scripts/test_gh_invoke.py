#!/usr/bin/env python3
"""Behavior tests for shared GitHub CLI argv shaping."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Sequence
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext  # noqa: E402
from codex_refactor_loop.controller_actions import ControllerActions  # noqa: E402
from codex_refactor_loop.gh_invoke import build_gh_argv  # noqa: E402
from codex_refactor_loop.pr_checks import PrChecksProjection  # noqa: E402
from codex_refactor_loop.wakeup_runner import WakeupRunner  # noqa: E402


class GhInvokeHelperTests(unittest.TestCase):
    def test_helper_never_adds_repo_to_api_calls(self) -> None:
        cases = (
            ["gh", "api", "repos/owner/repo/pulls/31"],
            ["gh", "api", "repos/owner/repo/commits/abc123/check-runs", "--paginate", "--slurp"],
        )
        for command in cases:
            with self.subTest(command=command):
                shaped = build_gh_argv("owner/repo", command)
                self.assertEqual(command, shaped)
                self.assertNotIn("--repo", shaped)

    def test_helper_adds_repo_after_supported_pr_and_issue_subcommands(self) -> None:
        cases = (
            (
                ["gh", "pr", "view", "31", "--json", "mergeable,isDraft"],
                ["gh", "pr", "view", "31", "--repo", "owner/repo", "--json", "mergeable,isDraft"],
            ),
            (
                ["gh", "pr", "list", "--head", "feature", "--json", "number"],
                ["gh", "pr", "list", "--repo", "owner/repo", "--head", "feature", "--json", "number"],
            ),
            (
                ["gh", "issue", "view", "53", "--json", "labels,body"],
                ["gh", "issue", "view", "53", "--repo", "owner/repo", "--json", "labels,body"],
            ),
            (
                ["gh", "pr", "ready", "31"],
                ["gh", "pr", "ready", "31", "--repo", "owner/repo"],
            ),
            (
                ["gh", "pr", "merge", "31", "--squash", "--delete-branch"],
                ["gh", "pr", "merge", "31", "--repo", "owner/repo", "--squash", "--delete-branch"],
            ),
        )
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(expected, build_gh_argv("owner/repo", command))

    def test_helper_preserves_existing_repo_flag(self) -> None:
        command = ["gh", "pr", "view", "31", "--repo", "other/repo", "--json", "state"]
        self.assertEqual(command, build_gh_argv("owner/repo", command))

    def test_helper_does_not_shape_non_gh_or_without_slug(self) -> None:
        self.assertEqual(["git", "status"], build_gh_argv("owner/repo", ["git", "status"]))
        self.assertEqual(["gh", "pr", "view", "31"], build_gh_argv("", ["gh", "pr", "view", "31"]))


class GhInvokeCallSiteCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="gh-invoke-test-"))
        (self.tmp / ".refactor-loop" / "state").mkdir(parents=True)
        (self.tmp / ".refactor-loop" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\n'
            'export GH_REPO_SLUG="owner/repo"\n'
            'export INTEGRATION_BRANCH="canonical-integration"\n'
            'export REVIEW_BASE_BRANCH="canonical-review"\n',
            encoding="utf-8",
        )
        self.ctx = LoopContext.load(repo_root=self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_wakeup_runner_run_command_exact_argv(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            calls.append(list(command))
            return subprocess.CompletedProcess(command, 0, "", "")

        runner = WakeupRunner(self.ctx)
        with mock.patch("codex_refactor_loop.wakeup_runner.subprocess.run", side_effect=fake_run):
            runner._run_command(["gh", "api", "repos/owner/repo/pulls/31"])
            runner._run_command(["gh", "api", "repos/owner/repo/commits/abc123/check-runs", "--paginate", "--slurp"])
            runner._run_command(["gh", "pr", "view", "31", "--json", "mergeable,isDraft"])
            runner._run_command(["gh", "pr", "list", "--head", "feature", "--json", "number"])
            runner._run_command(["gh", "issue", "view", "53", "--json", "labels,body"])
            runner._run_command(["gh", "pr", "view", "31", "--repo", "other/repo", "--json", "state"])

        self.assertEqual(
            calls,
            [
                ["gh", "api", "repos/owner/repo/pulls/31"],
                ["gh", "api", "repos/owner/repo/commits/abc123/check-runs", "--paginate", "--slurp"],
                ["gh", "pr", "view", "31", "--repo", "owner/repo", "--json", "mergeable,isDraft"],
                ["gh", "pr", "list", "--repo", "owner/repo", "--head", "feature", "--json", "number"],
                ["gh", "issue", "view", "53", "--repo", "owner/repo", "--json", "labels,body"],
                ["gh", "pr", "view", "31", "--repo", "other/repo", "--json", "state"],
            ],
        )

    def test_controller_actions_gh_exact_argv(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            calls.append(list(command))
            return subprocess.CompletedProcess(command, 0, "", "")

        actions = ControllerActions(self.ctx)
        with mock.patch("codex_refactor_loop.controller_actions.subprocess.run", side_effect=fake_run):
            actions.gh(["pr", "view", "31", "--json", "mergeable,isDraft"], check=False)
            actions.gh(["pr", "list", "--head", "feature", "--json", "number"], check=False)
            actions.gh(["issue", "view", "53", "--json", "labels,body"], check=False)
            actions.gh(["pr", "ready", "31"], check=False)
            actions.gh(["pr", "merge", "31", "--squash", "--delete-branch"], check=False)
            actions.gh(["pr", "view", "31", "--repo", "other/repo", "--json", "state"], check=False)

        self.assertEqual(
            calls,
            [
                ["gh", "pr", "view", "31", "--repo", "owner/repo", "--json", "mergeable,isDraft"],
                ["gh", "pr", "list", "--repo", "owner/repo", "--head", "feature", "--json", "number"],
                ["gh", "issue", "view", "53", "--repo", "owner/repo", "--json", "labels,body"],
                ["gh", "pr", "ready", "31", "--repo", "owner/repo"],
                ["gh", "pr", "merge", "31", "--repo", "owner/repo", "--squash", "--delete-branch"],
                ["gh", "pr", "view", "31", "--repo", "other/repo", "--json", "state"],
            ],
        )

    def test_pr_checks_api_exact_argv(self) -> None:
        calls: list[list[str]] = []

        def runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
            calls.append(list(command))
            if command == ["gh", "api", "repos/owner/repo/pulls/31"]:
                return subprocess.CompletedProcess(command, 0, json.dumps({"head": {"sha": "abc123"}}), "")
            if command == ["gh", "api", "repos/owner/repo/commits/abc123/check-runs", "--paginate", "--slurp"]:
                return subprocess.CompletedProcess(command, 0, json.dumps({"check_runs": []}), "")
            return subprocess.CompletedProcess(command, 99, "", "unexpected command")

        status = PrChecksProjection(runner=runner).check_pr("owner/repo", 31)

        self.assertTrue(status.ok)
        self.assertEqual(
            calls,
            [
                ["gh", "api", "repos/owner/repo/pulls/31"],
                ["gh", "api", "repos/owner/repo/commits/abc123/check-runs", "--paginate", "--slurp"],
            ],
        )


class GhInvokeSourceRegressionTests(unittest.TestCase):
    def test_single_helper_exists_and_api_never_gets_repo_flag(self) -> None:
        helper = (SCRIPT_DIR / "codex_refactor_loop" / "gh_invoke.py").read_text(encoding="utf-8")
        self.assertIn("def build_gh_argv", helper)
        self.assertIn('REPO_SCOPED_SUBCOMMANDS = frozenset({"pr", "issue"})', helper)
        self.assertIn('if full[1] not in REPO_SCOPED_SUBCOMMANDS:', helper)
        self.assertNotIn('"api"', helper.split("REPO_SCOPED_SUBCOMMANDS", 1)[1].split("\n", 1)[0])
        self.assertEqual(["gh", "api", "repos/owner/repo/pulls/1"], build_gh_argv("owner/repo", ["gh", "api", "repos/owner/repo/pulls/1"]))

    def test_call_sites_use_shared_helper_without_inline_pre_subcommand_repo_insertion(self) -> None:
        wakeup = (SCRIPT_DIR / "codex_refactor_loop" / "wakeup_runner.py").read_text(encoding="utf-8")
        controller = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        pr_checks = (SCRIPT_DIR / "codex_refactor_loop" / "pr_checks.py").read_text(encoding="utf-8")
        for source in (wakeup, controller, pr_checks):
            self.assertIn("build_gh_argv", source)
        for source in (wakeup, controller):
            self.assertNotIn('full[1:1] = ["--repo"', source)
            self.assertNotIn('full[1:1] = [\'--repo\'', source)
            self.assertNotIn('full.insert(1, "--repo")', source)


if __name__ == "__main__":
    unittest.main()
