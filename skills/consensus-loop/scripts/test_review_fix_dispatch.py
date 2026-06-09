#!/usr/bin/env python3
"""Behavior tests for review-fix dispatch rendering."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import patch

import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.controller_actions import ControllerActions
from codex_refactor_loop.review_fix_dispatch import (
    ReviewFixDispatchSpec,
    ReviewThreadCompletionError,
    ReviewThreadCompletionEvidence,
    validate_review_thread_completion,
)


class ReviewFixDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="review-fix-dispatch-test-"))
        (self.tmp / ".refactor-loop").mkdir(parents=True)
        (self.tmp / ".refactor-loop" / "logs").mkdir(parents=True)
        (self.tmp / ".refactor-loop" / "runs").mkdir(parents=True)
        (self.tmp / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.tmp / ".config" / "consensus-rnd" / "host.env").write_text(
            f'export REPO_ROOT="{self.tmp}"\n'
            'export GH_REPO_SLUG="owner/repo"\n'
            'export INTEGRATION_BRANCH="dev"\n'
            'export REVIEW_BASE_BRANCH="dev"\n',
            encoding="utf-8",
        )
        self.actions = ControllerActions(
            LoopContext.load(
                repo_root=self.tmp,
                skill_root=SCRIPT_DIR.parent,
                env={
                    "REPO_ROOT": str(self.tmp),
                    "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env",
                },
            )
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_spec_for_round_uses_canonical_runs_report_path(self) -> None:
        spec = ReviewFixDispatchSpec.for_round(269, 1)

        self.assertEqual(spec.fix_output_path, ".refactor-loop/runs/fix-pr269-round-1-report.md")
        self.assertEqual(spec.prompt_path, ".refactor-loop/prompts/fixes/fix-pr269-round-1.md")
        self.assertEqual(spec.log_path, ".refactor-loop/logs/fix-pr269-round-1.log")
        self.assertEqual(spec.as_render_env()["FIX_OUTPUT_PATH"], ".refactor-loop/runs/fix-pr269-round-1-report.md")

    def test_validate_rejects_root_report_and_path_escape(self) -> None:
        invalid = (
            "FIX_REPORT.md",
            "./FIX_REPORT.md",
            "/tmp/FIX_REPORT.md",
            ".refactor-loop/../FIX_REPORT.md",
            ".refactor-loop/logs/fix-pr269-round-1-report.md",
            ".refactor-loop/runs/fix-pr269-r1.md",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    ReviewFixDispatchSpec.validate_fix_output_path(value)

    def test_controller_render_review_fix_prompt_injects_fix_output_path(self) -> None:
        with patch("codex_refactor_loop.controller_actions.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                ["gh"],
                0,
                stdout=json.dumps(
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "reviewThreads": {
                                        "nodes": [],
                                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    }
                                }
                            }
                        }
                    }
                ),
                stderr="",
            )
            spec = self.actions.render_review_fix_prompt(
                269,
                1,
                env={
                    "PR_NUMBER": "269",
                    "PR_TITLE": "Review fix render",
                    "FIX_ROUND": "1",
                    "MAX_FIX_ROUNDS": "3",
                    "BASE_BRANCH": "dev",
                    "HEAD_BRANCH": "impl/issue269",
                    "REVIEW_ARCHITECT_PATH": ".refactor-loop/runs/review-pr269-architect-r1.md",
                    "REVIEW_TESTS_PATH": ".refactor-loop/runs/review-pr269-tests-r1.md",
                    "REVIEW_QUALITY_PATH": ".refactor-loop/runs/review-pr269-quality-r1.md",
                    "AUDIT_PATH": ".refactor-loop/runs/audit.md",
                    "IMPLEMENT_SUMMARY_PATH": ".refactor-loop/runs/implement.md",
                    "PROJECT_RULES": "CLAUDE.md",
                    "HOST_REFACTOR_COMMENT_POLICY": "self-doc-comment",
                },
            )

        self.assertEqual(spec.fix_output_path, ".refactor-loop/runs/fix-pr269-round-1-report.md")
        prompt = self.tmp / ".refactor-loop" / "prompts" / "fixes" / "fix-pr269-round-1.md"
        rendered = prompt.read_text(encoding="utf-8")
        self.assertIn(".refactor-loop/runs/fix-pr269-round-1-report.md", rendered)
        self.assertNotIn("${FIX_OUTPUT_PATH}", rendered)
        self.assertTrue(prompt.exists())

    def test_controller_render_review_fix_prompt_headless_binds_all_template_values(self) -> None:
        for role in ("architect", "tests", "quality"):
            (self.tmp / ".refactor-loop" / "runs" / f"review-pr269-{role}-r1.md").write_text(
                f"---\nverdict: reject\n---\nREVIEW_DONE:269:{role}:reject\n",
                encoding="utf-8",
            )
            (self.tmp / ".refactor-loop" / "logs" / f"review-pr269-{role}-r1.log").write_text(
                f"REVIEW_DONE:269:{role}:reject\nEXIT=0\n",
                encoding="utf-8",
            )
            (self.tmp / ".refactor-loop" / "runs" / f"review-pr269-{role}-r2.md").write_text(
                f"---\nverdict: reject\n---\nREVIEW_DONE:269:{role}:reject\n",
                encoding="utf-8",
            )
            (self.tmp / ".refactor-loop" / "logs" / f"review-pr269-{role}-r2.log").write_text(
                f"REVIEW_DONE:269:{role}:reject\nEXIT=0\n",
                encoding="utf-8",
            )

        def fake_run(argv, cwd, capture_output, text, check):
            if argv[:4] == ["gh", "api", "graphql", "-f"]:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    stdout=json.dumps(
                        {
                            "data": {
                                "repository": {
                                    "pullRequest": {
                                        "reviewThreads": {
                                            "nodes": [],
                                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                                        }
                                    }
                                }
                            }
                        }
                    ),
                    stderr="",
                )
            self.assertEqual(argv[:3], ["gh", "pr", "view"])
            self.assertIn("269", argv)
            self.assertIn("--repo", argv)
            self.assertIn("example/repo", argv)
            self.assertEqual(argv[-2:], ["--json", "title,headRefName,baseRefName"])
            return subprocess.CompletedProcess(
                argv,
                0,
                '{"title":"Fix render env","headRefName":"fix/review","baseRefName":"main"}',
                "",
            )

        actions = ControllerActions(
            LoopContext.load(
                repo_root=self.tmp,
                skill_root=SCRIPT_DIR.parent,
                env={
                    "REPO_ROOT": str(self.tmp),
                    "GH_REPO_SLUG": "example/repo",
                },
            )
        )
        with mock.patch("codex_refactor_loop.controller_actions.subprocess.run", side_effect=fake_run):
            spec = actions.render_review_fix_prompt(269, 2)

        rendered = (self.tmp / spec.prompt_path).read_text(encoding="utf-8")
        self.assertNotIn("${", rendered)
        self.assertIn("PR **269** (`Fix render env`). Round **2** of max **3**", rendered)
        self.assertIn("origin/main...origin/fix/review", rendered)
        self.assertIn("- `.refactor-loop/runs/review-pr269-architect-r2.md`", rendered)
        self.assertIn("- `.refactor-loop/runs/review-pr269-tests-r2.md`", rendered)
        self.assertIn("- `.refactor-loop/runs/review-pr269-quality-r2.md`", rendered)
        self.assertIn("# Fix report for PR 269 round 2", rendered)

    def test_controller_render_review_fix_prompt_uses_latest_complete_log_when_artifact_missing(self) -> None:
        for role in ("architect", "tests", "quality"):
            (self.tmp / ".refactor-loop" / "runs" / f"review-pr269-{role}-r1.md").write_text(
                f"---\nverdict: reject\n---\nREVIEW_DONE:269:{role}:reject\n",
                encoding="utf-8",
            )
            (self.tmp / ".refactor-loop" / "logs" / f"review-pr269-{role}-r1.log").write_text(
                f"REVIEW_DONE:269:{role}:reject\nEXIT=0\n",
                encoding="utf-8",
            )
            (self.tmp / ".refactor-loop" / "logs" / f"review-pr269-{role}-r3.log").write_text(
                f"REVIEW_DONE:269:{role}:reject\nEXIT=0\n",
                encoding="utf-8",
            )

        with mock.patch.object(
            self.actions,
            "gh",
            return_value=subprocess.CompletedProcess(
                ["gh"],
                0,
                '{"title":"Fallback","headRefName":"head","baseRefName":"base"}',
                "",
            ),
        ):
            spec = self.actions.render_review_fix_prompt(269, 1)

        rendered = (self.tmp / spec.prompt_path).read_text(encoding="utf-8")
        self.assertNotIn("${", rendered)
        self.assertIn("- `.refactor-loop/logs/review-pr269-architect-r3.log`", rendered)
        self.assertIn("- `.refactor-loop/logs/review-pr269-tests-r3.log`", rendered)
        self.assertIn("- `.refactor-loop/logs/review-pr269-quality-r3.log`", rendered)

    def test_controller_render_review_fix_prompt_writes_review_thread_seed(self) -> None:
        with patch("codex_refactor_loop.controller_actions.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                ["gh"],
                0,
                stdout=json.dumps(
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "reviewThreads": {
                                        "nodes": [{"id": "PRRT_kwDOExample", "isResolved": False}],
                                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    }
                                }
                            }
                        }
                    }
                ),
                stderr="",
            )
            self.actions.render_review_fix_prompt(269, 1)

        seed = self.tmp / ".refactor-loop" / "state" / "review-thread-completion" / "pr269.json"
        self.assertTrue(seed.exists())
        data = json.loads(seed.read_text(encoding="utf-8"))
        self.assertEqual(
            data,
            {
                "review_thread_driven": True,
                "thread_id": "PRRT_kwDOExample",
                "replied": False,
                "resolved": False,
                "source": "live-pr-review-thread",
            },
        )

    def test_controller_render_review_fix_prompt_removes_stale_seed_when_no_unresolved_threads_remain(self) -> None:
        state_dir = self.tmp / ".refactor-loop" / "state" / "review-thread-completion"
        state_dir.mkdir(parents=True)
        seed = state_dir / "pr269.json"
        seed.write_text(
            json.dumps(
                {
                    "review_thread_driven": True,
                    "thread_id": "",
                    "replied": False,
                    "resolved": False,
                    "source": "live-pr-review-thread-unknown",
                }
            ),
            encoding="utf-8",
        )
        with patch("codex_refactor_loop.controller_actions.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                ["gh"],
                0,
                stdout=json.dumps(
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "reviewThreads": {
                                        "nodes": [],
                                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    }
                                }
                            }
                        }
                    }
                ),
                stderr="",
            )
            self.actions.render_review_fix_prompt(269, 1)

        self.assertFalse(seed.exists())

    def test_controller_render_review_fix_prompt_writes_unknown_seed_on_review_thread_lookup_failure(self) -> None:
        with patch("codex_refactor_loop.controller_actions.subprocess.run") as run:
            run.side_effect = (
                subprocess.CompletedProcess(
                    ["gh"],
                    0,
                    stdout='{"title":"Lookup failure","headRefName":"head","baseRefName":"base"}',
                    stderr="",
                ),
                subprocess.CompletedProcess(["gh"], 1, stdout="", stderr="boom"),
            )
            self.actions.render_review_fix_prompt(269, 1)

        seed = self.tmp / ".refactor-loop" / "state" / "review-thread-completion" / "pr269.json"
        self.assertTrue(seed.exists())
        data = json.loads(seed.read_text(encoding="utf-8"))
        self.assertEqual(
            data,
            {
                "review_thread_driven": True,
                "thread_id": "",
                "replied": False,
                "resolved": False,
                "source": "live-pr-review-thread-unknown",
            },
        )

    def test_controller_render_review_fix_prompt_paginates_review_thread_seed(self) -> None:
        responses = [
            subprocess.CompletedProcess(
                ["gh"],
                0,
                stdout='{"title":"Paginated","headRefName":"head","baseRefName":"base"}',
                stderr="",
            ),
            subprocess.CompletedProcess(
                ["gh"],
                0,
                stdout=json.dumps(
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "reviewThreads": {
                                        "nodes": [{"id": "PRRT_kwDOOther", "isResolved": True}],
                                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor1"},
                                    }
                                }
                            }
                        }
                    }
                ),
                stderr="",
            ),
            subprocess.CompletedProcess(
                ["gh"],
                0,
                stdout=json.dumps(
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "reviewThreads": {
                                        "nodes": [{"id": "PRRT_kwDOExample", "isResolved": False}],
                                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    }
                                }
                            }
                        }
                    }
                ),
                stderr="",
            ),
        ]
        with patch("codex_refactor_loop.controller_actions.subprocess.run", side_effect=responses) as run:
            self.actions.render_review_fix_prompt(269, 1)

        seed = self.tmp / ".refactor-loop" / "state" / "review-thread-completion" / "pr269.json"
        self.assertTrue(seed.exists())
        rendered_calls = [" ".join(str(arg) for arg in call.args[0]) for call in run.call_args_list]
        self.assertTrue(any("after=cursor1" in call for call in rendered_calls))

    def test_review_thread_completion_ignores_non_thread_driven_fix(self) -> None:
        validate_review_thread_completion(
            ReviewThreadCompletionEvidence(
                review_thread_driven=False,
                replied=False,
                resolved=False,
            )
        )

    def test_review_thread_completion_accepts_replied_and_resolved_original_thread(self) -> None:
        validate_review_thread_completion(
            ReviewThreadCompletionEvidence(
                review_thread_driven=True,
                thread_id="PRRT_kwDOExample",
                replied=True,
                resolved=True,
            )
        )

    def test_review_thread_completion_accepts_explicit_escalation(self) -> None:
        validate_review_thread_completion(
            ReviewThreadCompletionEvidence(
                review_thread_driven=True,
                thread_id="PRRT_kwDOExample",
                replied=False,
                resolved=False,
                escalation_evidence="META_RESOLVED:escalate-human:conflicting-review-thread",
            )
        )

    def test_review_thread_completion_rejects_malformed_escalation_evidence(self) -> None:
        with self.assertRaisesRegex(ReviewThreadCompletionError, "escalation evidence"):
            validate_review_thread_completion(
                ReviewThreadCompletionEvidence(
                    review_thread_driven=True,
                    thread_id="PRRT_kwDOExample",
                    replied=False,
                    resolved=False,
                    escalation_evidence="anything nonempty",
                )
            )

    def test_review_thread_completion_blocks_missing_original_thread_evidence(self) -> None:
        with self.assertRaisesRegex(ReviewThreadCompletionError, "thread_id"):
            validate_review_thread_completion(
                ReviewThreadCompletionEvidence(
                    review_thread_driven=True,
                    replied=True,
                    resolved=True,
                )
            )

    def test_review_thread_completion_blocks_unreplied_or_unresolved_thread(self) -> None:
        blocked = (
            ReviewThreadCompletionEvidence(
                review_thread_driven=True,
                thread_id="PRRT_kwDOExample",
                replied=False,
                resolved=True,
            ),
            ReviewThreadCompletionEvidence(
                review_thread_driven=True,
                thread_id="PRRT_kwDOExample",
                replied=True,
                resolved=False,
            ),
        )
        for evidence in blocked:
            with self.subTest(replied=evidence.replied, resolved=evidence.resolved):
                with self.assertRaises(ReviewThreadCompletionError):
                    validate_review_thread_completion(evidence)

    def test_controller_completion_path_fails_closed_for_open_review_thread(self) -> None:
        with self.assertRaises(ReviewThreadCompletionError):
            self.actions.validate_review_fix_completion(
                ReviewThreadCompletionEvidence(
                    review_thread_driven=True,
                    thread_id="PRRT_kwDOExample",
                    replied=True,
                    resolved=False,
                )
            )


if __name__ == "__main__":
    unittest.main()
