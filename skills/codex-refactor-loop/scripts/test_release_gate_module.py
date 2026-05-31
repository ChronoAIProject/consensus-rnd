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

from codex_refactor_loop import labels as label_catalog
from codex_refactor_loop import restart
from codex_refactor_loop.release import gate


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def executable_source(source: str) -> str:
    return "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))


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
        "skills/codex-refactor-loop/VERSION.json",
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
        self.label_results: dict[str, list[dict[str, int]]] = {}

    def __call__(self, cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        self.commands.append(cmd)
        if cmd[:2] == ["gh", "api"]:
            created = gate.isoformat(NOW)
            runs = [
                {"id": index + 1, "created_at": created, "started_at": created, "completed_at": created, "conclusion": "success", "status": "completed", "name": name}
                for index, name in enumerate(("contract-tests", "manifest-version-sync", "skill-degradation"))
            ]
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"check_runs": runs}), stderr="")
        if len(cmd) >= 2 and cmd[0] == "gh" and cmd[2:3] == ["list"]:
            label = cmd[cmd.index("--label") + 1]
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(self.label_results.get(label, [])), stderr="")
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
            self.assertEqual(decision["to_version"], gate.next_release_version(current_version, "patch"))
            self.assertEqual(decision["bump_type"], "patch")
            self.assertEqual(list(decision["signals"].keys()), list(gate.SIGNAL_NAMES))
            candidate = read_json(repo / ".refactor-loop/state/release-candidate.json")
            self.assertIsInstance(candidate, dict)
            assert isinstance(candidate, dict)
            self.assertEqual(candidate["schema"], "decision-artifact-only/v2")
            self.assertEqual(candidate["decision_artifact"], ".refactor-loop/state/release-decision.json")
            self.assertEqual(candidate["host_opt_in"], "RELEASE_AUTO_ENABLE=true")
            self.assertEqual(candidate["lifecycle_owner"], "controller")
            self.assertEqual(candidate["publish_preflight"], "controller-release-publish-preflight")
            self.assertIn("target_ref", candidate)
            self.assertIn("expires_at", candidate)
            self.assertIn("required_signals", candidate)
            self.assertIn("decision_digest", candidate)
            self.assertIn("consensus-rnd-cli release-gate", candidate["next_step_hint"])
            self.assertNotIn("release.yml", candidate["next_step_hint"])
            self.assertFalse((repo / ".refactor-loop/.controller-pending-events.log").exists())
            self.assertFalse((repo / ".refactor-loop/dispatch-queue").exists())

    def test_live_signal_parity_for_required_checks_labels_and_heartbeats(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_live_state(repo)
            runner = FakeRunner()
            release_gate = gate.AutoReleaseGate(repo, now=lambda: NOW, runner=runner)
            old_env = {key: gate.os.environ.get(key) for key in ("GH_REPO_SLUG", "REVIEW_BASE_BRANCH", "INTEGRATION_BRANCH", "HOST_GITHUB_RELEASE_REQUIRED_CHECKS")}
            try:
                gate.os.environ["GH_REPO_SLUG"] = "owner/repo"
                gate.os.environ["REVIEW_BASE_BRANCH"] = "review-base"
                gate.os.environ["INTEGRATION_BRANCH"] = "integration-branch"
                gate.os.environ["HOST_GITHUB_RELEASE_REQUIRED_CHECKS"] = "contract-tests,manifest-version-sync,skill-degradation"
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
            self.assertIn(label_catalog.PHASE_BLOCKED, labels)
            self.assertIn(label_catalog.HUMAN_MAINTAINER_DECISION, labels)
            heartbeat_signal = stability.signals["fresh_heartbeats"]
            self.assertEqual(heartbeat_signal["source"], "heartbeats/*.ts")
            self.assertEqual(sum(1 for value in heartbeat_signal["heartbeats"].values() if value), 6)
            self.assertTrue(heartbeat_signal["heartbeats"]["closed_label_reconciler"])

    def test_live_release_gate_fails_closed_when_auto_release_lacks_host_required_checks(self) -> None:
        for value in (None, ""):
            with self.subTest(value=value), copy_repo_fixture() as tmp:
                repo = Path(tmp) / "repo"
                write_live_state(repo)
                runner = FakeRunner()
                release_gate = gate.AutoReleaseGate(repo, now=lambda: NOW, runner=runner)
                keys = ("GH_REPO_SLUG", "REVIEW_BASE_BRANCH", "INTEGRATION_BRANCH", "RELEASE_AUTO_ENABLE", "HOST_GITHUB_RELEASE_REQUIRED_CHECKS")
                old_env = {key: gate.os.environ.get(key) for key in keys}
                try:
                    gate.os.environ["GH_REPO_SLUG"] = "owner/repo"
                    gate.os.environ["REVIEW_BASE_BRANCH"] = "review-base"
                    gate.os.environ["INTEGRATION_BRANCH"] = "integration-branch"
                    gate.os.environ["RELEASE_AUTO_ENABLE"] = "true"
                    if value is None:
                        gate.os.environ.pop("HOST_GITHUB_RELEASE_REQUIRED_CHECKS", None)
                    else:
                        gate.os.environ["HOST_GITHUB_RELEASE_REQUIRED_CHECKS"] = value

                    stability = release_gate.compute_stability(min_recent_merges=1)
                finally:
                    for key, previous in old_env.items():
                        if previous is None:
                            gate.os.environ.pop(key, None)
                        else:
                            gate.os.environ[key] = previous

                self.assertFalse(stability.ready)
                signal = stability.signals["required_checks_recent_green"]
                self.assertFalse(signal["passed"])
                self.assertEqual(signal["source"], "host.env")
                self.assertEqual(
                    signal["reason"],
                    "required_checks_recent_green:missing_host_required_release_checks",
                )
                self.assertFalse(any(cmd[:2] == ["gh", "api"] for cmd in runner.commands))

    def test_release_gate_blocks_on_legacy_blocked_and_human_labels(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            write_live_state(repo)
            runner = FakeRunner()
            runner.label_results["⏸️ phase:blocked"] = [{"number": 10}]
            runner.label_results["👤 human:需-maintainer-决策"] = [{"number": 11}]
            release_gate = gate.AutoReleaseGate(repo, now=lambda: NOW, runner=runner)
            old_env = {key: gate.os.environ.get(key) for key in ("GH_REPO_SLUG", "REVIEW_BASE_BRANCH", "INTEGRATION_BRANCH", "HOST_GITHUB_RELEASE_REQUIRED_CHECKS")}
            try:
                gate.os.environ["GH_REPO_SLUG"] = "owner/repo"
                gate.os.environ["REVIEW_BASE_BRANCH"] = "review-base"
                gate.os.environ["INTEGRATION_BRANCH"] = "integration-branch"
                gate.os.environ["HOST_GITHUB_RELEASE_REQUIRED_CHECKS"] = "contract-tests,manifest-version-sync,skill-degradation"
                stability = release_gate.compute_stability(min_recent_merges=1)
            finally:
                for key, value in old_env.items():
                    if value is None:
                        gate.os.environ.pop(key, None)
                    else:
                        gate.os.environ[key] = value

            self.assertFalse(stability.ready)
            blocked_signal = stability.signals["no_open_blocked_pr"]
            human_signal = stability.signals["no_human_decision_label"]
            self.assertFalse(blocked_signal["passed"])
            self.assertEqual(blocked_signal["reason"], "no_open_blocked_pr:label_present:⏸️ phase:blocked")
            self.assertFalse(human_signal["passed"])
            self.assertEqual(human_signal["reason"], "no_human_decision_label:failed")
            self.assertEqual(human_signal["issue"]["reason"], "label_present:👤 human:需-maintainer-决策")

    def test_host_env_reads_canonical_refactor_loop_file_and_ignores_root(self) -> None:
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
            self.assertNotIn("REVIEW_BASE_BRANCH", loaded)
            self.assertEqual(loaded["INTEGRATION_BRANCH"], "integration")

    def test_host_env_locator_prefers_explicit_host_owned_file_without_legacy_file(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            explicit = repo / ".config/consensus-rnd/host.env"
            explicit.parent.mkdir(parents=True, exist_ok=True)
            explicit.write_text(
                "export RELEASE_AUTO_ENABLE=true\nexport REVIEW_BASE_BRANCH=explicit-review\n",
                encoding="utf-8",
            )
            self.assertFalse((repo / ".refactor-loop/host.env").exists())

            loaded = gate.load_host_env(repo, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})

            self.assertEqual(loaded["RELEASE_AUTO_ENABLE"], "true")
            self.assertEqual(loaded["REVIEW_BASE_BRANCH"], "explicit-review")

    def test_release_gate_source_uses_shared_host_env_parser(self) -> None:
        source = (SCRIPT_PATH.parent / "codex_refactor_loop/release/gate.py").read_text(encoding="utf-8")
        publish_preflight_source = (SCRIPT_PATH.parent / "codex_refactor_loop/release/publish_preflight.py").read_text(encoding="utf-8")
        self.assertIn("from ..context import HostEnvLocator, parse_host_env", source)
        self.assertIn("HostEnvLocator.resolve", source)
        self.assertNotIn('repo_root / ".refactor-loop" / "host.env"', source)
        self.assertNotIn('repo_root / ".refactor-loop" / "host.env"', publish_preflight_source)
        self.assertNotIn('repo_root / "host.env"', source)
        self.assertNotIn("def parse_host_env_value", source)

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
            self.assertTrue(all(value is False for value in signal["heartbeats"].values()))
            self.assertEqual(set(signal["heartbeats"]), set(gate.DAEMON_NAMES))

    def test_source_has_no_release_lifecycle_or_daemon_event_authority(self) -> None:
        source = (SCRIPT_PATH.parent / "codex_refactor_loop/release/gate.py").read_text(encoding="utf-8")
        cli_source = (SCRIPT_PATH.parent / "codex_refactor_loop/cli.py").read_text(encoding="utf-8")
        executable_source = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
        self.assertIn("skills/codex-refactor-loop/authorizations/runtime-exceptions.md#autonomous-release-gate-56", source)
        self.assertIn("decision-artifact-only", source)
        self.assertNotIn('["git"', executable_source)
        self.assertNotIn('"git"', executable_source)
        self.assertNotIn("release.commits", source)
        self.assertNotIn("write_release_commits", source)
        self.assertNotIn("collect_release_commits", source)
        self.assertNotIn("release_gate_with_pre_gate_commits", cli_source)
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
                self.assertNotIn(forbidden, executable_source)

    def test_required_check_names_and_daemon_heartbeat_allowlist_are_stable(self) -> None:
        self.assertEqual(gate.REQUIRED_CHECKS({"HOST_GITHUB_RELEASE_REQUIRED_CHECKS": "contract-tests,manifest-version-sync,skill-degradation"}), ("contract-tests", "manifest-version-sync", "skill-degradation"))
        self.assertEqual(gate.HEARTBEAT_FRESH_SECONDS, 90)
        self.assertEqual(gate.DAEMON_NAMES, restart.restart_managed_daemon_names())
        self.assertEqual(6, len(gate.DAEMON_NAMES))
        self.assertIn("closed_label_reconciler", gate.DAEMON_NAMES)

    def test_fresh_heartbeats_requires_each_restart_managed_daemon(self) -> None:
        with copy_repo_fixture() as tmp:
            repo = Path(tmp) / "repo"
            heartbeat_dir = repo / ".refactor-loop/heartbeats"
            heartbeat_dir.mkdir(parents=True, exist_ok=True)
            fresh = int(NOW.timestamp())
            for name in gate.DAEMON_NAMES:
                if name != "closed_label_reconciler":
                    (heartbeat_dir / f"{name}.ts").write_text(f"{fresh}\n", encoding="utf-8")
            (heartbeat_dir / "extra-observer.ts").write_text(f"{fresh}\n", encoding="utf-8")
            release_gate = gate.AutoReleaseGate(repo, now=lambda: NOW, runner=FakeRunner())

            signal = release_gate.fresh_heartbeats()

            self.assertFalse(signal["passed"])
            self.assertFalse(signal["heartbeats"]["closed_label_reconciler"])
            self.assertTrue(signal["heartbeats"]["extra-observer"])

            (heartbeat_dir / "closed_label_reconciler.ts").write_text(f"{fresh}\n", encoding="utf-8")
            signal = release_gate.fresh_heartbeats()

            self.assertTrue(signal["passed"])
            self.assertTrue(all(signal["heartbeats"][name] for name in restart.restart_managed_daemon_names()))

    def test_daemon_name_source_contract_uses_restart_projection(self) -> None:
        restart_source = (SCRIPT_PATH.parent / "codex_refactor_loop/restart.py").read_text(encoding="utf-8")
        release_source = (SCRIPT_PATH.parent / "codex_refactor_loop/release/gate.py").read_text(encoding="utf-8")
        wakeup_source = (SCRIPT_PATH.parent / "codex_refactor_loop/wakeup_plan.py").read_text(encoding="utf-8")

        self.assertIn("def restart_managed_daemon_names(", restart_source)
        self.assertIn("return tuple(name for name, _command in DAEMON_COMMANDS)", restart_source)
        self.assertNotIn("RESTART_MANAGED_DAEMON_NAMES", restart_source)
        release_executable = executable_source(release_source)
        wakeup_executable = executable_source(wakeup_source)
        self.assertIn("DAEMON_NAMES = restart_managed_daemon_names()", release_executable)
        self.assertIn("required_names = restart_managed_daemon_names()", release_executable)
        self.assertNotIn('DAEMON_NAMES = (\n    "concurrency_monitor"', release_executable)
        self.assertIn("restart_managed_daemon_names()", wakeup_source)
        self.assertNotIn("EXPECTED_DAEMONS", wakeup_executable)

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
