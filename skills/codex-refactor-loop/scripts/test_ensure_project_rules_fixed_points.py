#!/usr/bin/env python3
"""Project-rules fixed point and phase-4 runtime source contracts."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
SCRIPT_DIR = SCRIPT_PATH.parent
SKILL_ROOT = SCRIPT_PATH.parents[1]
REPO_ROOT = SCRIPT_PATH.parents[3]
CLI = SCRIPT_DIR / "consensus-rnd-cli"

sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.project_rules import (
    CANONICAL_BODY,
    CANONICAL_HASH,
    END_MARKER,
    OLD_CANONICAL_BODY,
    ProjectRulesFixedPointEnsurer,
    START_RE,
    sha256_text,
)


class ProjectRulesFixedPointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="project-rules-test-"))
        self.rules = self.tmp / "CLAUDE.md"
        self.rules.write_text("# Host rules\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ensure_appends_managed_block_and_is_idempotent(self) -> None:
        ensurer = ProjectRulesFixedPointEnsurer(str(self.tmp))
        self.assertEqual("updated", ensurer.ensure())
        text = self.rules.read_text(encoding="utf-8")
        self.assertIn(CANONICAL_BODY, text)
        self.assertIn(CANONICAL_HASH, text)
        self.assertEqual("already-current", ensurer.ensure())

    def test_refuses_manual_edit_inside_managed_block(self) -> None:
        ensurer = ProjectRulesFixedPointEnsurer(str(self.tmp))
        ensurer.ensure()
        self.rules.write_text(self.rules.read_text(encoding="utf-8").replace("FI-007 删除优先", "FI-007 edited"), encoding="utf-8")
        with self.assertRaisesRegex(Exception, "hash mismatch"):
            ensurer.ensure()

    def test_cli_operation_runs_from_single_entrypoint(self) -> None:
        env = os.environ.copy()
        env["REPO_ROOT"] = str(self.tmp)
        result = subprocess.run(
            [sys.executable, str(CLI), "ensure-project-rules"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("PROJECT_RULES_FIXED_POINT:updated", result.stdout)

    def test_hash_helpers_are_stable(self) -> None:
        self.assertEqual(CANONICAL_HASH, sha256_text(CANONICAL_BODY))
        self.assertNotEqual(CANONICAL_HASH, sha256_text(OLD_CANONICAL_BODY))
        self.assertRegex(START_RE.pattern, "sha256")
        self.assertIn("consensus-rnd:foundational-invariants:end", END_MARKER)


class RuntimeShellRemovalSourceTests(unittest.TestCase):
    def test_no_runtime_shell_scripts_remain(self) -> None:
        scripts = SKILL_ROOT / "scripts"
        shell_files = sorted(path.name for path in scripts.glob("*.sh"))
        self.assertEqual([], shell_files)

    def test_legacy_top_level_runtime_wrappers_are_removed(self) -> None:
        removed = (
            "codex_loop.py",
            "spawn-codex.sh",
            "peek.sh",
            "restart-daemons.sh",
            "statusline.sh",
            "comment-monitor.sh",
            "codex-progress-reporter.sh",
            "controller_lib.sh",
            "repo_slug.sh",
            "daemon_heartbeat.sh",
            "log_retention.sh",
            "concurrency_monitor.py",
            "dev_sync_daemon.py",
            "phase9_router_daemon.py",
            "auto_release_gate.py",
            "post_banner.py",
            "apply_integration_sync_request.py",
            "apply_triage_decision.py",
            "check_skill_degradation.py",
            "check_manifest_version_sync.py",
            "triage_decisions.py",
            "integration_sync_requests.py",
            "wakeup_plan.py",
            "daemon_heartbeat.py",
            "repo_config.py",
        )
        for name in removed:
            with self.subTest(name=name):
                self.assertFalse((SKILL_ROOT / "scripts" / name).exists())

    def test_skill_and_prompts_do_not_call_shell_runtime(self) -> None:
        checked = [SKILL_ROOT / "SKILL.md", SKILL_ROOT / "host.env.example", *sorted((SKILL_ROOT / "prompts").glob("*.md"))]
        forbidden = (
            "spawn-codex.sh",
            "peek.sh",
            "restart-daemons.sh",
            "statusline.sh",
            "comment-monitor.sh",
            "codex-progress-reporter.sh",
            "controller_lib.sh",
            "repo_slug.sh",
            "daemon_heartbeat.sh",
            "log_retention.sh",
        )
        for path in checked:
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                with self.subTest(path=path.name, token=token):
                    self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main()
