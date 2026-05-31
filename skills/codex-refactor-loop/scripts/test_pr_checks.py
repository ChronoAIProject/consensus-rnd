#!/usr/bin/env python3
"""Behavior tests for the PR-head Checks API projection."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.pr_checks import PrChecksProjection  # noqa: E402


class PrChecksProjectionTests(unittest.TestCase):
    def run_projection(self, responses: dict[tuple[str, ...], subprocess.CompletedProcess[str]]):
        calls: list[list[str]] = []

        def runner(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
            calls.append(list(cmd))
            return responses.get(tuple(cmd), subprocess.CompletedProcess(list(cmd), 99, "", "unexpected command"))

        return PrChecksProjection(runner=runner).check_pr("owner/repo", 31), calls

    def test_reads_pull_head_then_paginated_slurp_check_runs(self) -> None:
        responses = {
            ("gh", "api", "repos/owner/repo/pulls/31"): subprocess.CompletedProcess(
                ["gh"], 0, json.dumps({"head": {"sha": "abc123"}}), ""
            ),
            ("gh", "api", "repos/owner/repo/commits/abc123/check-runs", "--paginate", "--slurp"): subprocess.CompletedProcess(
                ["gh"],
                0,
                json.dumps(
                    [
                        {
                            "check_runs": [
                                {
                                    "name": "unit",
                                    "status": "completed",
                                    "conclusion": "success",
                                    "html_url": "https://checks/unit",
                                    "started_at": "2026-05-31T00:00:00Z",
                                    "completed_at": "2026-05-31T00:01:00Z",
                                },
                                {
                                    "name": "lint",
                                    "status": "completed",
                                    "conclusion": "failure",
                                    "details_url": "https://checks/lint",
                                },
                                {
                                    "name": "deploy",
                                    "status": "queued",
                                    "conclusion": None,
                                },
                            ]
                        }
                    ]
                ),
                "",
            ),
        }

        status, calls = self.run_projection(responses)

        self.assertTrue(status.ok)
        self.assertEqual("abc123", status.head_sha)
        self.assertEqual(
            calls,
            [
                ["gh", "api", "repos/owner/repo/pulls/31"],
                ["gh", "api", "repos/owner/repo/commits/abc123/check-runs", "--paginate", "--slurp"],
            ],
        )
        self.assertEqual([run.name for run in status.runs], ["unit", "lint", "deploy"])
        self.assertEqual([run.bucket for run in status.runs], ["pass", "fail", "pending"])
        self.assertEqual(status.runs[1].link, "https://checks/lint")
        data = status.as_dict()
        self.assertEqual(data["bucket_counts"], {"pass": 1, "fail": 1, "pending": 1})
        self.assertEqual(set(data["checks"][0]), {"name", "bucket", "state", "link", "conclusion", "status", "started_at", "completed_at"})

    def test_flat_slurp_pages_are_accepted(self) -> None:
        responses = {
            ("gh", "api", "repos/owner/repo/pulls/31"): subprocess.CompletedProcess(
                ["gh"], 0, json.dumps({"head": {"sha": "abc123"}}), ""
            ),
            ("gh", "api", "repos/owner/repo/commits/abc123/check-runs", "--paginate", "--slurp"): subprocess.CompletedProcess(
                ["gh"], 0, json.dumps([[{"name": "matrix", "status": "completed", "conclusion": "neutral"}]]), ""
            ),
        }

        status, _calls = self.run_projection(responses)

        self.assertTrue(status.ok)
        self.assertEqual(status.runs[0].bucket, "skipping")

    def test_fails_closed_on_api_json_and_head_sha_errors(self) -> None:
        cases = (
            ("pull_api_failure", subprocess.CompletedProcess(["gh"], 1, "", "nope"), None),
            ("invalid_pull_json", subprocess.CompletedProcess(["gh"], 0, "{", ""), None),
            ("missing_head_sha", subprocess.CompletedProcess(["gh"], 0, json.dumps({"head": {}}), ""), None),
            (
                "checks_api_failure",
                subprocess.CompletedProcess(["gh"], 0, json.dumps({"head": {"sha": "abc123"}}), ""),
                subprocess.CompletedProcess(["gh"], 2, "", "no checks"),
            ),
            (
                "invalid_checks_json",
                subprocess.CompletedProcess(["gh"], 0, json.dumps({"head": {"sha": "abc123"}}), ""),
                subprocess.CompletedProcess(["gh"], 0, "{", ""),
            ),
            (
                "invalid_checks_json",
                subprocess.CompletedProcess(["gh"], 0, json.dumps({"head": {"sha": "abc123"}}), ""),
                subprocess.CompletedProcess(["gh"], 0, json.dumps({"not_check_runs": []}), ""),
            ),
        )
        for reason, pull_response, check_response in cases:
            with self.subTest(reason=reason):
                responses = {("gh", "api", "repos/owner/repo/pulls/31"): pull_response}
                if check_response is not None:
                    responses[("gh", "api", "repos/owner/repo/commits/abc123/check-runs", "--paginate", "--slurp")] = check_response
                status, _calls = self.run_projection(responses)
                self.assertFalse(status.ok)
                self.assertEqual(reason, status.reason)
                self.assertEqual((), status.runs)


class PrChecksSourceRegressionTests(unittest.TestCase):
    def test_production_source_uses_rest_projection_not_legacy_or_lifecycle_surfaces(self) -> None:
        production_dir = SCRIPT_DIR / "codex_refactor_loop"
        texts = {
            path.relative_to(production_dir).as_posix(): path.read_text(encoding="utf-8")
            for path in production_dir.rglob("*.py")
        }
        joined = "\n".join(texts.values())
        self.assertNotIn('"pr", "checks"', joined)
        self.assertNotIn("gh pr checks", joined)
        self.assertNotIn("GraphQL", joined)
        self.assertNotIn("gh issue create", texts["pr_checks.py"])
        self.assertNotIn("gh pr create", texts["pr_checks.py"])
        self.assertNotIn("git push", texts["pr_checks.py"])
        self.assertIn('f"repos/{repo_slug}/pulls/{pr}"', texts["pr_checks.py"])
        self.assertIn('f"repos/{repo_slug}/commits/{head_sha}/check-runs"', texts["pr_checks.py"])
        self.assertIn('"--paginate", "--slurp"', texts["pr_checks.py"])


if __name__ == "__main__":
    unittest.main()

# Refactor (issue-297): pr-checks REST projection + open_design_issue wrapper; controller 裸 gh/git recipe 已收口。
