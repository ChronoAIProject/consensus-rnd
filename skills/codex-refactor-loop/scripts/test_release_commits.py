#!/usr/bin/env python3
"""Behavior tests for the release commit producer."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
sys.path.insert(0, str(SCRIPT_PATH.parent))

from codex_refactor_loop.release import commits


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def git_ok(repo: Path, *args: str) -> str:
    result = git(repo, *args)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def commit(repo: Path, message: str, body: str = "") -> str:
    target = repo / "file.txt"
    target.write_text(target.read_text(encoding="utf-8") + message + "\n", encoding="utf-8")
    git_ok(repo, "add", ".")
    full_message = [message]
    if body:
        full_message.extend(["-m", body])
    git_ok(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", *full_message)
    return git_ok(repo, "rev-parse", "HEAD")


def init_repo() -> tempfile.TemporaryDirectory[str]:
    tmp = tempfile.TemporaryDirectory()
    repo = Path(tmp.name) / "repo"
    repo.mkdir()
    git_ok(repo, "init", "-q")
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    git_ok(repo, "add", ".")
    git_ok(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "chore: base")
    git_ok(repo, "tag", "v1.0.0")
    return tmp


class ReleaseCommitsProducerTests(unittest.TestCase):
    def test_write_release_commits_writes_new_commits_since_latest_tag(self) -> None:
        with init_repo() as tmp:
            repo = Path(tmp) / "repo"
            fix_sha = commit(repo, "fix: repair release", "Detailed body")
            feat_sha = commit(repo, "feat: add producer")

            output = commits.write_release_commits(repo, review_base_branch="dev")

            self.assertEqual(output, (repo / ".refactor-loop/state/release-commits.json").resolve())
            data = read_json(output)
            self.assertIsInstance(data, dict)
            assert isinstance(data, dict)
            self.assertEqual(
                data["commits"],
                [
                    {"sha": fix_sha, "subject": "fix: repair release", "body": "Detailed body"},
                    {"sha": feat_sha, "subject": "feat: add producer", "body": ""},
                ],
            )

    def test_write_release_commits_writes_empty_commits_when_no_new_commit_exists(self) -> None:
        with init_repo() as tmp:
            repo = Path(tmp) / "repo"

            output = commits.write_release_commits(repo, review_base_branch="dev")

            data = read_json(output)
            self.assertIsInstance(data, dict)
            assert isinstance(data, dict)
            self.assertEqual(data["commits"], [])

    def test_source_regression_keeps_producer_controller_side_and_gate_git_free(self) -> None:
        producer_source = (SCRIPT_PATH.parent / "codex_refactor_loop/release/commits.py").read_text(encoding="utf-8")
        gate_source = (SCRIPT_PATH.parent / "codex_refactor_loop/release/gate.py").read_text(encoding="utf-8")
        cli_source = (SCRIPT_PATH.parent / "codex_refactor_loop/cli.py").read_text(encoding="utf-8")
        gate_executable_source = "\n".join(line for line in gate_source.splitlines() if not line.lstrip().startswith("#"))

        self.assertIn("Refactor (impl/issue232-release-commits-producer)", producer_source)
        self.assertIn("one-shot pre-gate producer reads git", producer_source)
        self.assertIn("def write_release_commits(", producer_source)
        self.assertIn("release_gate_with_pre_gate_commits", cli_source)
        self.assertIn("controller-facing command runs the git-reading producer before entering the git-free release decider", cli_source)
        self.assertNotIn('["git"', gate_executable_source)
        self.assertNotIn('"git"', gate_executable_source)
        self.assertNotIn("collect_release_commits", gate_source)
        self.assertNotIn("write_release_commits", gate_source)


if __name__ == "__main__":
    unittest.main()
