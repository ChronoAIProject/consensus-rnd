#!/usr/bin/env python3
"""Behavior tests for the release commit producer."""

from __future__ import annotations

import json
import os
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


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "REPO_ROOT": str(repo), "CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"}
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH.with_name("consensus-rnd-cli")), *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def write_host_env(repo: Path, review_base: str = "dev", integration: str = "integration") -> None:
    env_path = repo / ".config" / "consensus-rnd" / "host.env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(
        f"export REPO_ROOT={repo}\n"
        f"export REVIEW_BASE_BRANCH={review_base}\n"
        f"export INTEGRATION_BRANCH={integration}\n",
        encoding="utf-8",
    )


def write_release_gate_host_env(repo: Path, *, release_auto_enable: str = "true") -> Path:
    env_path = repo / ".config" / "consensus-rnd" / "host.env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(
        f"export RELEASE_AUTO_ENABLE={release_auto_enable}\n"
        "export REVIEW_BASE_BRANCH=dev\n"
        "export INTEGRATION_BRANCH=integration\n"
        "export GH_REPO_SLUG=owner/repo\n",
        encoding="utf-8",
    )
    return env_path


def write_host_env_without_review_base(repo: Path) -> None:
    env_path = repo / ".config" / "consensus-rnd" / "host.env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(
        f"export REPO_ROOT={repo}\n"
        "export INTEGRATION_BRANCH=integration\n",
        encoding="utf-8",
    )


def commit(repo: Path, message: str, body: str = "") -> str:
    target = repo / "file.txt"
    target.write_text(target.read_text(encoding="utf-8") + message + "\n", encoding="utf-8")
    git_ok(repo, "add", ".")
    full_message = [message]
    if body:
        full_message.extend(["-m", body])
    git_ok(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", *full_message)
    return git_ok(repo, "rev-parse", "HEAD")


def init_repo(tag_release: bool = True) -> tempfile.TemporaryDirectory[str]:
    tmp = tempfile.TemporaryDirectory()
    repo = Path(tmp.name) / "repo"
    repo.mkdir()
    git_ok(repo, "init", "-q")
    (repo / "file.txt").write_text("base\n", encoding="utf-8")
    git_ok(repo, "add", ".")
    git_ok(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "chore: base")
    if tag_release:
        git_ok(repo, "tag", "v1.0.0")
    return tmp


def write_stale_release_commits(repo: Path) -> tuple[Path, dict[str, object]]:
    # Refactor (fix/pr236-failclosed-coverage): Old pattern: release-commits failure coverage only exercised invalid target refs. New principle: producer tests exercise each fail-closed git branch with stale state already present.
    fixture_path = repo / commits.RELEASE_COMMITS_RELATIVE_PATH
    fixture = {"commits": [{"sha": "fixture", "subject": "fix: keep fixture", "body": ""}]}
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    return fixture_path, fixture


def add_origin_ref(repo: Path, branch: str = "dev") -> None:
    remote = repo.parent / "origin.git"
    git_ok(repo.parent, "init", "--bare", "-q", str(remote))
    git_ok(repo, "branch", "-M", branch)
    git_ok(repo, "remote", "add", "origin", str(remote))
    git_ok(repo, "push", "-q", "--tags", "-u", "origin", branch)


class ReleaseCommitsProducerTests(unittest.TestCase):
    def test_write_release_commits_writes_new_commits_since_latest_tag(self) -> None:
        with init_repo() as tmp:
            repo = Path(tmp) / "repo"
            fix_sha = commit(repo, "fix: repair release", "Detailed body")
            feat_sha = commit(repo, "feat: add producer")

            output = commits.write_release_commits(repo, target_ref="HEAD")

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

            output = commits.write_release_commits(repo, target_ref="HEAD")

            data = read_json(output)
            self.assertIsInstance(data, dict)
            assert isinstance(data, dict)
            self.assertEqual(data["commits"], [])

    def test_release_commits_cli_overwrites_fixture_with_git_derived_commits(self) -> None:
        with init_repo() as tmp:
            repo = Path(tmp) / "repo"
            write_host_env(repo)
            fixture_path = repo / ".refactor-loop/state/release-commits.json"
            fixture_path.parent.mkdir(parents=True, exist_ok=True)
            fixture_path.write_text(
                json.dumps({"commits": [{"sha": "fixture", "subject": "fix: stale fixture", "body": ""}]}),
                encoding="utf-8",
            )
            fix_sha = commit(repo, "fix: cli fact source", "Body from git")
            add_origin_ref(repo, "dev")

            result = run_cli(repo, "release-commits", "--target-ref", "origin/dev")

            self.assertEqual(0, result.returncode, result.stderr)
            data = read_json(fixture_path)
            self.assertEqual(data, {"commits": [{"sha": fix_sha, "subject": "fix: cli fact source", "body": "Body from git"}]})
            self.assertIn("release commits artifact written", result.stdout)

    def test_release_commits_cli_fails_closed_without_overwriting_fixture(self) -> None:
        with init_repo() as tmp:
            repo = Path(tmp) / "repo"
            write_host_env(repo)
            fixture_path, fixture = write_stale_release_commits(repo)

            result = run_cli(repo, "release-commits", "--target-ref", "missing-ref", "--no-fetch-tags")

            self.assertEqual(1, result.returncode)
            self.assertEqual(fixture, read_json(fixture_path))
            self.assertIn("target ref does not exist", result.stderr)

    def test_release_commits_cli_fails_closed_without_latest_release_tag(self) -> None:
        with init_repo(tag_release=False) as tmp:
            repo = Path(tmp) / "repo"
            write_host_env(repo)
            fixture_path, fixture = write_stale_release_commits(repo)

            result = run_cli(repo, "release-commits", "--target-ref", "HEAD", "--no-fetch-tags")

            self.assertEqual(1, result.returncode)
            self.assertEqual(fixture, read_json(fixture_path))
            self.assertIn("no latest release tag found", result.stderr)

    def test_release_commits_cli_fails_closed_when_tag_fetch_fails(self) -> None:
        with init_repo() as tmp:
            repo = Path(tmp) / "repo"
            write_host_env(repo)
            fixture_path, fixture = write_stale_release_commits(repo)

            result = run_cli(repo, "release-commits", "--target-ref", "HEAD")

            self.assertEqual(1, result.returncode)
            self.assertEqual(fixture, read_json(fixture_path))
            self.assertIn("origin", result.stderr)

    def test_release_commits_cli_fails_closed_when_git_log_fails(self) -> None:
        with init_repo() as tmp:
            repo = Path(tmp) / "repo"
            write_host_env(repo)
            fixture_path, fixture = write_stale_release_commits(repo)

            result = run_cli(repo, "release-commits", "--target-ref", "HEAD", "--since-ref", "missing-release-tag", "--no-fetch-tags")

            self.assertEqual(1, result.returncode)
            self.assertEqual(fixture, read_json(fixture_path))
            self.assertIn("missing-release-tag..HEAD", result.stderr)

    def test_release_commits_cli_uses_manifest_version_tag_when_describe_finds_no_tag(self) -> None:
        with init_repo(tag_release=False) as tmp:
            repo = Path(tmp) / "repo"
            write_host_env(repo)
            (repo / "package.json").write_text(json.dumps({"version": "2.0.0"}), encoding="utf-8")
            (repo / ".version-bump.json").write_text(
                json.dumps({"files": [{"path": "package.json", "field": "version"}]}),
                encoding="utf-8",
            )
            git_ok(repo, "add", ".")
            git_ok(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "chore: add version manifest")
            unrelated_tree = git_ok(repo, "rev-parse", "HEAD^{tree}")
            unrelated_release = git_ok(
                repo,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit-tree",
                unrelated_tree,
                "-m",
                "release anchor",
            )
            git_ok(repo, "tag", "v2.0.0", unrelated_release)
            self.assertNotEqual(0, git(repo, "describe", "--tags", "--abbrev=0").returncode)
            feature_sha = commit(repo, "feat: fallback release")

            result = run_cli(repo, "release-commits", "--target-ref", "HEAD", "--no-fetch-tags")

            self.assertEqual(0, result.returncode, result.stderr)
            data = read_json(repo / ".refactor-loop/state/release-commits.json")
            self.assertIsInstance(data, dict)
            assert isinstance(data, dict)
            self.assertIn(
                {"sha": feature_sha, "subject": "feat: fallback release", "body": ""},
                data["commits"],
            )

    def test_release_commits_prefers_reachable_release_subject_over_unmerged_tag(self) -> None:
        with init_repo(tag_release=False) as tmp:
            repo = Path(tmp) / "repo"
            write_host_env(repo)
            (repo / "package.json").write_text(json.dumps({"version": "1.0.0-beta.10"}), encoding="utf-8")
            (repo / ".version-bump.json").write_text(
                json.dumps({"files": [{"path": "package.json", "field": "version"}]}),
                encoding="utf-8",
            )
            git_ok(repo, "add", ".")
            git_ok(
                repo,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "Release v1.0.0-beta.10 (#595)",
            )
            feature_sha = commit(repo, "fix: beta 11 candidate")
            unmerged_tree = git_ok(repo, "rev-parse", "HEAD^{tree}")
            unmerged_release = git_ok(
                repo,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit-tree",
                unmerged_tree,
                "-m",
                "Release v1.0.0-beta.10",
            )
            git_ok(repo, "tag", "v1.0.0-beta.10", unmerged_release)
            self.assertNotEqual(0, git(repo, "merge-base", "--is-ancestor", "v1.0.0-beta.10", "HEAD").returncode)

            output = commits.write_release_commits(repo, target_ref="HEAD", fetch_tags=False)

            data = read_json(output)
            self.assertEqual(data, {"commits": [{"sha": feature_sha, "subject": "fix: beta 11 candidate", "body": ""}]})

    def test_release_commits_cli_fails_closed_without_review_base_branch_env(self) -> None:
        with init_repo() as tmp:
            repo = Path(tmp) / "repo"
            write_host_env_without_review_base(repo)
            fixture_path, fixture = write_stale_release_commits(repo)

            result = run_cli(repo, "release-commits", "--no-fetch-tags")

            self.assertEqual(1, result.returncode)
            self.assertEqual(fixture, read_json(fixture_path))
            self.assertIn("missing required host branch env: REVIEW_BASE_BRANCH", result.stderr)

    def test_release_commits_cli_uses_host_review_base_when_target_ref_omitted(self) -> None:
        with init_repo() as tmp:
            repo = Path(tmp) / "repo"
            fix_sha = commit(repo, "fix: host review branch")
            add_origin_ref(repo, "trunk-review")
            write_host_env(repo, review_base="trunk-review")

            result = run_cli(repo, "release-commits", "--no-fetch-tags")

            self.assertEqual(0, result.returncode, result.stderr)
            data = read_json(repo / ".refactor-loop/state/release-commits.json")
            self.assertEqual(data, {"commits": [{"sha": fix_sha, "subject": "fix: host review branch", "body": ""}]})

    def test_release_gate_cli_does_not_rewrite_release_commits_artifact(self) -> None:
        with init_repo() as tmp:
            repo = Path(tmp) / "repo"
            fixture_path = repo / ".refactor-loop/state/release-commits.json"
            fixture = {"commits": [{"sha": "fixture", "subject": "fix: keep gate consumer only", "body": ""}]}
            fixture_path.parent.mkdir(parents=True, exist_ok=True)
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
            host_env = write_release_gate_host_env(repo)
            commit(repo, "fix: should not be projected by release-gate")

            score = run_cli(repo, "release-gate", "--score-only")
            self.assertEqual(0, score.returncode, score.stderr)
            self.assertEqual(fixture, read_json(fixture_path))

            no_opt_in_env = host_env.read_text(encoding="utf-8").replace("RELEASE_AUTO_ENABLE=true", "RELEASE_AUTO_ENABLE=false")
            host_env.write_text(no_opt_in_env, encoding="utf-8")
            noop = run_cli(repo, "release-gate")
            self.assertEqual(0, noop.returncode, noop.stderr)
            self.assertEqual(fixture, read_json(fixture_path))

    def test_source_regression_keeps_producer_controller_side_and_gate_git_free(self) -> None:
        producer_source = (SCRIPT_PATH.parent / "codex_refactor_loop/release/commits.py").read_text(encoding="utf-8")
        gate_source = (SCRIPT_PATH.parent / "codex_refactor_loop/release/gate.py").read_text(encoding="utf-8")
        cli_source = (SCRIPT_PATH.parent / "codex_refactor_loop/cli.py").read_text(encoding="utf-8")
        gate_executable_source = "\n".join(line for line in gate_source.splitlines() if not line.lstrip().startswith("#"))
        skill_source = (SCRIPT_PATH.parents[1] / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("RELEASE_COMMITS_RELATIVE_PATH", producer_source)
        self.assertIn("def write_release_commits(", producer_source)
        self.assertIn("run_git", producer_source)
        self.assertIn("write_json", producer_source)
        self.assertIn("latest_release_ref", producer_source)
        self.assertIn("missing required host branch env: REVIEW_BASE_BRANCH", producer_source)
        self.assertNotIn("DEFAULT_REVIEW_BASE_BRANCH", producer_source)
        self.assertNotIn("origin/dev", producer_source)
        self.assertIn('"release-commits": CommandSpec(', cli_source)
        self.assertIn('("read-git", "write-artifact")', cli_source)
        self.assertNotIn("release_gate_with_pre_gate_commits", cli_source)
        self.assertNotIn("write_release_commits", cli_source)
        self.assertNotIn('["git"', gate_executable_source)
        self.assertNotIn('"git"', gate_executable_source)
        self.assertNotIn("collect_release_commits", gate_source)
        self.assertNotIn("write_release_commits", gate_source)
        for token in (
            "consensus-rnd-cli release-commits --target-ref origin/$REVIEW_BASE_BRANCH",
            "Allowed: read git by fetching tags, describing the latest release tag, resolving the target ref, and logging the release range",
            "Forbidden: no gh, push, merge, reset, tag, release, lifecycle mutation, or inline execution inside release-gate",
            "Fact source: local git tags and refs",
            "Verification: behavior and source-regression coverage in test_release_commits.py and test_cli_command_router.py",
            "Release-gate only reads `.refactor-loop/state/release-commits.json`; it does not run git",
        ):
            with self.subTest(token=token):
                self.assertIn(token, skill_source)


if __name__ == "__main__":
    unittest.main()
