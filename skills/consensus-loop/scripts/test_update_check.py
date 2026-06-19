#!/usr/bin/env python3
"""Behavior tests for notify-only update-check probe."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.update_check import UpdateCheckProbe, load_version_manifest


NOW = datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc)


class UpdateCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="update-check-test-"))
        self.repo = self.tmp / "repo"
        self.skill = self.tmp / "skill"
        (self.repo / ".refactor-loop").mkdir(parents=True)
        (self.skill).mkdir(parents=True)
        shutil.copy2(REPO_ROOT / "skills/consensus-loop/VERSION.json", self.skill / "VERSION.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_host_env(self, body: str) -> None:
        (self.repo / ".config" / "consensus-rnd").mkdir(parents=True, exist_ok=True)
        (self.repo / ".config/consensus-rnd/host.env").write_text(
            f'export REPO_ROOT="{self.repo}"\n' + body,
            encoding="utf-8",
        )

    def ctx(self) -> LoopContext:
        return LoopContext.load(repo_root=self.repo, skill_root=self.skill, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})

    def read_state(self) -> dict[str, object]:
        return json.loads((self.repo / ".refactor-loop/state/update-check.json").read_text(encoding="utf-8"))

    def test_disabled_writes_noop_state_and_does_not_call_github(self) -> None:
        self.write_host_env('export UPDATE_CHECK_ENABLE="false"\n')
        calls: list[list[str]] = []

        probe = UpdateCheckProbe(self.ctx(), now=lambda: NOW, runner=lambda cmd: calls.append(list(cmd)) or SimpleNamespace(returncode=1, stdout="", stderr="called"))
        result = probe.maybe_run(startup=True)

        self.assertEqual("disabled", result["status"])
        self.assertIn("UPDATE_CHECK_ENABLE", result["reason"])
        self.assertEqual([], calls)
        self.assertEqual("disabled", self.read_state()["status"])

    def test_release_newer_than_local_sets_update_available(self) -> None:
        self.write_host_env('export UPDATE_CHECK_ENABLE="true"\nexport UPDATE_CHECK_INTERVAL_SECONDS="21600"\n')

        def runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
            self.assertEqual(["gh", "api", "repos/ChronoAIProject/consensus-rnd/releases/latest"], cmd)
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"tag_name": "v1.0.0-rc.1", "html_url": "https://example/release"}), "")

        result = UpdateCheckProbe(self.ctx(), now=lambda: NOW, runner=runner).maybe_run(startup=True)

        self.assertEqual("ok", result["status"])
        self.assertTrue(result["update_available"])
        self.assertEqual("1.0.0-rc.1", result["latest_version"])
        self.assertEqual("github-release", result["update_source"])
        self.assertEqual("https://example/release", result["release_url"])

    def test_release_failure_falls_back_to_tags(self) -> None:
        self.write_host_env('export UPDATE_CHECK_ENABLE="true"\n')
        calls: list[list[str]] = []

        def runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
            calls.append(list(cmd))
            if cmd == ["gh", "api", "repos/ChronoAIProject/consensus-rnd/releases/latest"]:
                return subprocess.CompletedProcess(cmd, 1, "", "not found")
            self.assertEqual(["gh", "api", "repos/ChronoAIProject/consensus-rnd/tags"], cmd)
            return subprocess.CompletedProcess(cmd, 0, json.dumps([{"name": "v1.0.0-rc.1"}]), "")

        result = UpdateCheckProbe(self.ctx(), now=lambda: NOW, runner=runner).maybe_run(startup=True)

        self.assertEqual("ok", result["status"])
        self.assertEqual("github-tag", result["update_source"])
        self.assertEqual("1.0.0-rc.1", result["latest_version"])
        self.assertTrue(result["update_available"])
        self.assertEqual(
            [
                ["gh", "api", "repos/ChronoAIProject/consensus-rnd/releases/latest"],
                ["gh", "api", "repos/ChronoAIProject/consensus-rnd/tags"],
            ],
            calls,
        )

    def test_github_error_writes_unknown_and_does_not_raise(self) -> None:
        self.write_host_env('export UPDATE_CHECK_ENABLE="true"\n')

        result = UpdateCheckProbe(
            self.ctx(),
            now=lambda: NOW,
            runner=lambda cmd: subprocess.CompletedProcess(cmd, 1, "", "network down"),
        ).maybe_run(startup=True)

        self.assertEqual("unknown", result["status"])
        self.assertIn("network down", result["reason"])
        self.assertEqual("unknown", self.read_state()["status"])

    def test_valid_manifest_helper_returns_version_and_repository(self) -> None:
        manifest = load_version_manifest(self.skill / "VERSION.json")

        self.assertEqual("ChronoAIProject/consensus-rnd", manifest["repository"])
        self.assertRegex(manifest["version"], r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
        self.assertEqual({"version", "repository"}, set(manifest))

    def test_invalid_manifest_fails_before_update_check_runner(self) -> None:
        self.write_host_env('export UPDATE_CHECK_ENABLE="true"\n')
        (self.skill / "VERSION.json").write_text(
            json.dumps(
                {
                    "schema": "wrong",
                    "version": "1.0.0",
                    "repository": "ChronoAIProject/consensus-rnd",
                    "release_source": "github-release-then-tag",
                    "install_hint": "host-owned",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        calls: list[list[str]] = []

        result = UpdateCheckProbe(
            self.ctx(),
            now=lambda: NOW,
            runner=lambda cmd: calls.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0, "{}", ""),
        ).maybe_run(startup=True)

        self.assertEqual("unknown", result["status"])
        self.assertIn("VERSION.json schema mismatch", result["reason"])
        self.assertEqual([], calls)

    def test_manual_probe_reuses_fresh_state_before_interval(self) -> None:
        self.write_host_env('export UPDATE_CHECK_ENABLE="true"\nexport UPDATE_CHECK_INTERVAL_SECONDS="21600"\n')
        state_path = self.repo / ".refactor-loop/state/update-check.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text(
            json.dumps({"status": "ok", "checked_at": "2026-05-31T11:00:00Z", "latest_version": "1.0.0"}) + "\n",
            encoding="utf-8",
        )
        calls: list[list[str]] = []

        result = UpdateCheckProbe(
            self.ctx(),
            now=lambda: NOW,
            runner=lambda cmd: calls.append(list(cmd)) or subprocess.CompletedProcess(cmd, 1, "", "called"),
        ).maybe_run(startup=False)

        self.assertEqual("ok", result["status"])
        self.assertEqual([], calls)

    def test_version_manifest_is_data_only(self) -> None:
        manifest = json.loads((REPO_ROOT / "skills/consensus-loop/VERSION.json").read_text(encoding="utf-8"))
        self.assertEqual(
            {
                "schema",
                "version",
                "repository",
                "release_source",
                "install_hint",
            },
            set(manifest),
        )
        serialized = json.dumps(manifest, sort_keys=True)
        for forbidden in ("/Users/", "curl ", "bash ", "install.sh", "https://"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
