#!/usr/bin/env python3
"""Behavior tests for codex_refactor_loop.release.gate."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[3]
NOW = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
sys.path.insert(0, str(SCRIPT_PATH.parent))

from codex_refactor_loop.release import gate


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


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
    return tmp


def write_green_fixture_signals(repo: Path) -> None:
    write_json(
        repo / ".refactor-loop/state/auto-release-signals.json",
        {"signals": {name: True for name in gate.SIGNAL_NAMES}},
    )
    write_json(
        repo / ".refactor-loop/state/release-commits.json",
        {"commits": [{"sha": "abc123", "subject": "fix: package release gate", "body": ""}]},
    )


def write_live_state(repo: Path) -> None:
    state = repo / ".refactor-loop/state"
    heartbeat_dir = repo / ".refactor-loop/heartbeats"
    heartbeat_dir.mkdir(parents=True, exist_ok=True)
    fresh = int(NOW.timestamp())
    for name in gate.DAEMON_NAMES:
        (heartbeat_dir / f"{name}.ts").write_text(f"{fresh}\n", encoding="utf-8")
    write_json(state / "phase8-review-state.json", {"max_consecutive_reject_rounds": 0})
    write_json(state / "meta-resolutions.json", {"unresolved_escalate_human": []})
    write_json(state / "recent-pr-merges.json", {"count": 1})
    write_json(repo / ".refactor-loop/.concurrency-monitor-state.json", {"zero_streak": 0})


class FakeRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        self.commands.append(cmd)
        if cmd[:2] == ["gh", "api"]:
            created = gate.isoformat(NOW)
            runs = [
                {"id": index + 1, "created_at": created, "started_at": created, "completed_at": created, "conclusion": "success", "status": "completed", "name": name}
                for index, name in enumerate(gate.REQUIRED_CHECKS)
            ]
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"check_runs": runs}), stderr="")
        if len(cmd) >= 2 and cmd[0] == "gh" and cmd[2:3] == ["list"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")
        return subprocess.CompletedProcess(cmd, 99, stdout="", stderr="unexpected command")


class ReleaseGateModuleTests(unittest.TestCase):
    def test_semver_parser_accepts_prerelease_and_build_metadata(self) -> None:
        for version in ("1.0.0", "1.0.0-beta.1", "1.0.0-rc.1+build.5"):
            with self.subTest(version=version):
                self.assertEqual(gate.parse_semver(version), (1, 0, 0))

    def test_semver_parser_rejects_invalid_versions(self) -> None:
        for version in ("1.0", "1.0.0.0", "1.0.0-", "v1.0.0", ""):
            with self.subTest(version=version):
                with self.assertRaisesRegex(ValueError, f"invalid semver: {re.escape(version)}"):
                    gate.parse_semver(version)

    def test_semver_bump_drops_prerelease_and_build_metadata(self) -> None:
        self.assertEqual(gate.bump_semver("1.0.0-beta.1", "patch"), "1.0.1")
        self.assertEqual(gate.bump_semver("1.0.0-rc.1+build.5", "minor"), "1.1.0")

    def test_green_fixture_decision_and_dispatch_artifacts_keep_schema(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_green_fixture_signals(repo)
            release_gate = gate.AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())

            stability = release_gate.compute_stability(min_recent_merges=0)
            decision = release_gate.decide_release(stability, min_interval_hours=2)
            release_gate.dispatch_release(decision)

            self.assertTrue(decision["ready"])
            # Version-agnostic: from_version tracks the real package.json version
            # (dispatch is artifact-only, no bump), surviving every release.
            current_version = read_json(repo / "package.json")["version"]
            self.assertEqual(decision["from_version"], current_version)
            self.assertEqual(decision["to_version"], gate.bump_semver(current_version, "patch"))
            self.assertEqual(decision["bump_type"], "patch")
            self.assertEqual(list(decision["signals"].keys()), list(gate.SIGNAL_NAMES))
            candidate = read_json(repo / ".refactor-loop/state/release-candidate.json")
            self.assertIsInstance(candidate, dict)
            assert isinstance(candidate, dict)
            self.assertEqual(candidate["schema"], "decision-artifact-only/v1")
            self.assertEqual(candidate["decision_artifact"], ".refactor-loop/state/release-decision.json")
            self.assertEqual(candidate["host_opt_in"], "RELEASE_AUTO_ENABLE=true")
            self.assertEqual(candidate["lifecycle_owner"], "controller-or-release.yml")
            self.assertIn("consensus-rnd-cli release-gate", candidate["next_step_hint"])
            self.assertFalse((repo / ".refactor-loop/.controller-pending-events.log").exists())
            self.assertFalse((repo / ".refactor-loop/dispatch-queue").exists())

    def test_live_signal_parity_for_required_checks_labels_and_heartbeats(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_live_state(repo)
            runner = FakeRunner()
            release_gate = gate.AutoReleaseGate(repo, now=lambda: NOW, runner=runner)
            old_env = {key: gate.os.environ.get(key) for key in ("GH_REPO_SLUG", "REVIEW_BASE_BRANCH", "INTEGRATION_BRANCH")}
            try:
                gate.os.environ["GH_REPO_SLUG"] = "owner/repo"
                gate.os.environ["REVIEW_BASE_BRANCH"] = "review-base"
                gate.os.environ["INTEGRATION_BRANCH"] = "integration-branch"
                stability = release_gate.compute_stability(min_recent_merges=1)
            finally:
                for key, value in old_env.items():
                    if value is None:
                        gate.os.environ.pop(key, None)
                    else:
                        gate.os.environ[key] = value

            self.assertTrue(stability.ready)
            required_runs = [cmd for cmd in runner.commands if cmd[:2] == ["gh", "api"]]
            self.assertEqual([cmd[2] for cmd in required_runs], [
                "repos/owner/repo/commits/review-base/check-runs",
                "repos/owner/repo/commits/integration-branch/check-runs",
            ])
            for cmd in required_runs:
                self.assertIn("--paginate", cmd)
            label_commands = [cmd for cmd in runner.commands if cmd[:2] in (["gh", "issue"], ["gh", "pr"]) and cmd[2:3] == ["list"]]
            labels = [cmd[cmd.index("--label") + 1] for cmd in label_commands]
            self.assertIn("⏸️ phase:blocked", labels)
            self.assertIn("👤 human:需-maintainer-决策", labels)
            heartbeat_signal = stability.signals["fresh_heartbeats"]
            self.assertEqual(heartbeat_signal["source"], "heartbeats/*.ts")
            self.assertEqual(sum(1 for value in heartbeat_signal["heartbeats"].values() if value), 5)

    def test_host_env_precedence_and_opt_in_literals_are_unchanged(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            (repo / "host.env").write_text(
                "export RELEASE_AUTO_ENABLE=false\nexport REVIEW_BASE_BRANCH=root-review\n",
                encoding="utf-8",
            )
            nested = repo / ".refactor-loop/host.env"
            nested.parent.mkdir(parents=True, exist_ok=True)
            nested.write_text(
                "export RELEASE_AUTO_ENABLE=true\nexport INTEGRATION_BRANCH=integration\n",
                encoding="utf-8",
            )

            loaded = gate.load_host_env(repo)

            self.assertEqual(loaded["RELEASE_AUTO_ENABLE"], "true")
            self.assertEqual(loaded["REVIEW_BASE_BRANCH"], "root-review")
        self.assertEqual(loaded["INTEGRATION_BRANCH"], "integration")

    def test_fresh_heartbeats_reads_ts_files_and_rejects_legacy_state_only(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_json(
                repo / ".refactor-loop/state/daemon-heartbeats.json",
                {name: gate.isoformat(NOW) for name in gate.DAEMON_NAMES},
            )
            release_gate = gate.AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())

            signal = release_gate.fresh_heartbeats()

            self.assertFalse(signal["passed"])
            self.assertEqual(signal["source"], "heartbeats/*.ts")
            self.assertEqual(signal["heartbeats"], {})

    def test_source_has_no_release_lifecycle_or_daemon_event_authority(self) -> None:
        source = (SCRIPT_PATH.parent / "codex_refactor_loop/release/gate.py").read_text(encoding="utf-8")
        self.assertIn(".refactor-loop/runs/phase9-issue56-r2-judge.md", source)
        self.assertIn("decision-artifact-only", source)
        for forbidden in (
            "dispatch_queue(",
            "pending_events(",
            ".controller-pending-events.log",
            ".dispatch.json",
            "gh pr create",
            "gh pr merge",
            "gh issue close",
            "gh release",
            "git commit",
            "git push",
            "git tag",
            "bump_version",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_required_check_names_and_daemon_heartbeat_allowlist_are_stable(self) -> None:
        self.assertEqual(gate.REQUIRED_CHECKS, ("contract-tests", "manifest-version-sync", "skill-degradation"))
        self.assertEqual(gate.HEARTBEAT_FRESH_SECONDS, 90)
        self.assertEqual(gate.DAEMON_NAMES, (
            "concurrency_monitor",
            "codex-progress-reporter",
            "comment-monitor",
            "dev_sync_daemon",
            "phase9_router_daemon",
        ))

    def test_blocked_release_does_not_write_artifacts(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_green_fixture_signals(repo)
            data = read_json(repo / ".refactor-loop/state/auto-release-signals.json")
            self.assertIsInstance(data, dict)
            assert isinstance(data, dict)
            data["signals"]["fresh_heartbeats"] = False
            write_json(repo / ".refactor-loop/state/auto-release-signals.json", data)
            release_gate = gate.AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())
            stability = release_gate.compute_stability(min_recent_merges=0)
            decision = release_gate.decide_release(stability, min_interval_hours=2)

            self.assertFalse(decision["ready"])
            self.assertIn("fresh_heartbeats", decision["blocked_reasons"])
            with self.assertRaises(RuntimeError):
                release_gate.dispatch_release(decision)
            self.assertFalse((repo / ".refactor-loop/state/release-decision.json").exists())
            self.assertFalse((repo / ".refactor-loop/state/release-candidate.json").exists())


if __name__ == "__main__":
    unittest.main()
