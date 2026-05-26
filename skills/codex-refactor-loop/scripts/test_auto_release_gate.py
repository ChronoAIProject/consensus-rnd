#!/usr/bin/env python3
"""Behavior tests for auto_release_gate.py."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]
NOW = datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc)
sys.path.insert(0, str(SCRIPT_PATH.parent))

from auto_release_gate import AutoReleaseGate, CommitInfo, classify_bump


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


class FakeRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        self.commands.append(cmd)
        if cmd[:3] == ["git", "tag", "--list"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["git", "log"] and "--format=%H%x00%s%x00%b%x1e" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="abc123\x00fix: fixture\x00\x1e", stderr="")
        if cmd[:2] == ["git", "log"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def copy_repo_fixture() -> tempfile.TemporaryDirectory[str]:
    tmp = tempfile.TemporaryDirectory()
    repo = Path(tmp.name) / "repo"
    paths = [
        ".version-bump.json",
        "package.json",
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        ".codex-plugin/plugin.json",
        ".cursor-plugin/plugin.json",
        "gemini-extension.json",
    ]
    for relative in paths:
        source = REPO_ROOT / relative
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "fix: fixture"],
        cwd=repo,
        check=True,
    )
    return tmp


def write_opt_in(repo: Path, enabled: bool = True) -> None:
    (repo / "host.env").write_text(f"export RELEASE_AUTO_ENABLE={'true' if enabled else 'false'}\n", encoding="utf-8")


def write_green_signals(repo: Path) -> None:
    write_json(
        repo / ".refactor-loop/state/auto-release-signals.json",
        {"signals": {name: True for name in (
            "required_checks_recent_green",
            "no_open_blocked_pr",
            "no_human_decision_label",
            "no_phase8_reject_churn",
            "p0_alert_streak_ok",
            "recent_pr_merges_min",
            "fresh_heartbeats",
            "no_unresolved_human_escalation",
        )}},
    )


class AutoReleaseGateBehaviorTests(unittest.TestCase):
    def test_no_opt_in_exits_noop(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            env = {**os.environ, "REPO_ROOT": str(repo)}
            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH.with_name("auto_release_gate.py"))],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("RELEASE_AUTO_ENABLE is not true", result.stdout)
            self.assertFalse((repo / ".refactor-loop/state/release-decision.json").exists())

    def test_stability_signals_all_green_yields_ready(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_green_signals(repo)
            gate = AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())
            score = gate.compute_stability()

            self.assertTrue(score.ready)
            self.assertEqual(score.score, 100)

    def test_unstable_signal_blocks_release(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_green_signals(repo)
            data = read_json(repo / ".refactor-loop/state/auto-release-signals.json")
            assert isinstance(data, dict)
            data["signals"]["fresh_heartbeats"] = False
            write_json(repo / ".refactor-loop/state/auto-release-signals.json", data)
            gate = AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())
            score = gate.compute_stability()

            self.assertFalse(score.ready)
            self.assertLess(score.score, 100)

    def test_commit_prefix_breaking_change_yields_major(self) -> None:
        commits = [CommitInfo("a", "feat!: replace contract", "")]
        self.assertEqual(classify_bump(commits), "major")

    def test_commit_prefix_feat_yields_minor(self) -> None:
        commits = [CommitInfo("a", "fix: repair", ""), CommitInfo("b", "feat: add gate", "")]
        self.assertEqual(classify_bump(commits), "minor")

    def test_commit_prefix_fix_yields_patch(self) -> None:
        commits = [CommitInfo("a", "fix: repair", "")]
        self.assertEqual(classify_bump(commits), "patch")

    def test_min_interval_blocks_too_frequent_release(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_green_signals(repo)
            write_json(
                repo / ".refactor-loop/state/release-decision.json",
                {"applied_at": (NOW - timedelta(minutes=30)).isoformat().replace("+00:00", "Z")},
            )
            gate = AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())
            decision = gate.decide_release(gate.compute_stability(), min_interval_hours=2)

            self.assertFalse(decision["ready"])
            self.assertIn("min_interval", decision["blocked_reasons"])
            self.assertEqual(decision["from_version"], decision["to_version"])

    def test_dry_run_writes_decision_no_bump(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_opt_in(repo)
            write_green_signals(repo)
            before = (repo / "package.json").read_text(encoding="utf-8")
            env = {**os.environ, "REPO_ROOT": str(repo)}
            result = subprocess.run(
                [sys.executable, str(SCRIPT_PATH.with_name("auto_release_gate.py"))],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((repo / ".refactor-loop/state/release-decision.json").exists())
            self.assertEqual((repo / "package.json").read_text(encoding="utf-8"), before)

    def test_apply_bumps_and_pushes_version_files(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_green_signals(repo)
            runner = FakeRunner()
            gate = AutoReleaseGate(repo, now=lambda: NOW, runner=runner)
            decision = gate.decide_release(gate.compute_stability(), min_interval_hours=2)

            gate.apply_release(decision)

            versions = set()
            mapping = read_json(repo / ".version-bump.json")
            assert isinstance(mapping, dict)
            for item in mapping["files"]:
                data = read_json(repo / item["path"])
                current = data
                for part in item["field"].split("."):
                    current = current[int(part)] if isinstance(current, list) else current[part]
                versions.add(current)
            self.assertEqual(len(versions), 1)
            self.assertIn(["git", "push", "origin", "HEAD"], runner.commands)


if __name__ == "__main__":
    unittest.main()
