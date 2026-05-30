#!/usr/bin/env python3
"""Project-rules fixed point and publish runtime source contracts."""

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
    ProjectRulesFixedPointProbe,
    ProjectRulesPatchArtifact,
    START_RE,
    sha256_text,
)


class ProjectRulesFixedPointTests(unittest.TestCase):
    # Refactor (iter218/issue-218):
    #   Old pattern: ensure-project-rules 是 public CLI 默认写 host policy 文件($PROJECT_RULES),违反 skill 无 host 改动权边界
    #   New principle: 改为 read-only check-project-rules probe + patch artifact:probe 只读判 sentinel block,非 current 写 .refactor-loop/runs/ patch 并 fail-closed 不派 actor;删 ensure-project-rules/_atomic_write,不引入 PROJECT_RULES_WRITE_ENABLE。严格按 plan 逐条改。
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="project-rules-test-"))
        self.rules = self.tmp / "CLAUDE.md"
        self.rules.write_text("# Host rules\n", encoding="utf-8")
        self.artifact = self.tmp / ".refactor-loop" / "runs" / "project-rules-fixed-point.patch"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_probe_current_exits_zero_without_modifying_project_rules(self) -> None:
        block = self._managed_block(CANONICAL_BODY)
        self.rules.write_text(f"# Host rules\n\n{block}", encoding="utf-8")
        before = self.rules.read_bytes()
        before_mtime = self.rules.stat().st_mtime_ns

        report = ProjectRulesFixedPointProbe(str(self.tmp)).inspect()

        self.assertTrue(report.is_current)
        self.assertEqual("current", report.reason)
        self.assertEqual(before, self.rules.read_bytes())
        self.assertEqual(before_mtime, self.rules.stat().st_mtime_ns)
        self.assertFalse(self.artifact.exists())

    def test_probe_missing_block_writes_patch_artifact_and_leaves_target_unchanged(self) -> None:
        before = self.rules.read_bytes()
        report = ProjectRulesFixedPointProbe(str(self.tmp)).inspect()

        self.assertTrue(report.needs_patch)
        self.assertEqual("missing", report.reason)
        artifact = ProjectRulesPatchArtifact(self.tmp).write(report)
        self.assertEqual(self.artifact, artifact)
        patch = artifact.read_text(encoding="utf-8")
        self.assertIn("--- a/CLAUDE.md", patch)
        self.assertIn("+++ b/CLAUDE.md", patch)
        self.assertIn("+## 共识研发不动点（由 consensus-rnd 管理）", patch)
        self.assertIn("+- FI-007 删除优先；废弃路径直接移除，除非 host 规则明确要求迁移期兼容。", patch)
        self.assertEqual(before, self.rules.read_bytes())

    def test_probe_known_old_block_writes_replacement_patch_without_overwrite(self) -> None:
        old_text = f"# Host rules\n\n{self._managed_block(OLD_CANONICAL_BODY)}"
        self.rules.write_text(old_text, encoding="utf-8")
        report = ProjectRulesFixedPointProbe(str(self.tmp)).inspect()

        self.assertTrue(report.needs_patch)
        self.assertEqual("known-old", report.reason)
        artifact = ProjectRulesPatchArtifact(self.tmp).write(report)
        patch = artifact.read_text(encoding="utf-8")
        self.assertIn("- FI-001 AI 产物默认不可信；进入主线前必须经过独立检查。", patch)
        self.assertIn("+- FI-007 删除优先；废弃路径直接移除，除非 host 规则明确要求迁移期兼容。", patch)
        self.assertEqual(old_text, self.rules.read_text(encoding="utf-8"))

    def test_probe_tampered_block_fails_closed_without_apply(self) -> None:
        self.rules.write_text(f"# Host rules\n\n{self._managed_block(CANONICAL_BODY)}", encoding="utf-8")
        tampered = self.rules.read_text(encoding="utf-8").replace("FI-007 删除优先", "FI-007 edited")
        self.rules.write_text(tampered, encoding="utf-8")

        report = ProjectRulesFixedPointProbe(str(self.tmp)).inspect()

        self.assertEqual("blocked", report.status)
        self.assertEqual("tampered", report.reason)
        self.assertIn("hash mismatch", report.detail)
        self.assertIsNone(report.proposed_text)
        self.assertEqual(tampered, self.rules.read_text(encoding="utf-8"))
        self.assertFalse(self.artifact.exists())

    def test_cli_operation_runs_from_single_entrypoint(self) -> None:
        env = os.environ.copy()
        env["REPO_ROOT"] = str(self.tmp)
        result = subprocess.run(
            [sys.executable, str(CLI), "check-project-rules"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("PROJECT_RULES_FIXED_POINT:patch-required", result.stdout)
        self.assertIn("artifact=.refactor-loop/runs/project-rules-fixed-point.patch", result.stdout)
        self.assertIn("reason=missing", result.stdout)
        self.assertEqual("# Host rules\n", self.rules.read_text(encoding="utf-8"))
        self.assertTrue(self.artifact.is_file())

    def test_ensure_project_rules_command_is_removed_and_no_writer_remains(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CLI), "ensure-project-rules"],
            env={**os.environ.copy(), "REPO_ROOT": str(self.tmp)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("unknown command: ensure-project-rules", result.stderr)
        source = (SCRIPT_DIR / "codex_refactor_loop" / "project_rules.py").read_text(encoding="utf-8")
        runtime_source = "\n".join(line for line in source.splitlines() if "Refactor (iter218/issue-218)" not in line and "Old pattern:" not in line and "New principle:" not in line)
        self.assertNotIn("_atomic_write", runtime_source)
        self.assertNotIn("tempfile", source)
        self.assertNotIn("os.replace", source)
        self.assertNotIn("PROJECT_RULES_WRITE_ENABLE", runtime_source)
        self.assertNotIn("ProjectRulesFixedPointEnsurer", runtime_source)

    def test_hash_helpers_are_stable(self) -> None:
        self.assertEqual(CANONICAL_HASH, sha256_text(CANONICAL_BODY))
        self.assertNotEqual(CANONICAL_HASH, sha256_text(OLD_CANONICAL_BODY))
        self.assertRegex(START_RE.pattern, "sha256")
        self.assertIn("consensus-rnd:foundational-invariants:end", END_MARKER)

    def _managed_block(self, body: str) -> str:
        return (
            f"<!-- consensus-rnd:foundational-invariants:start version=1 sha256={sha256_text(body)} -->\n"
            f"{body}"
            f"{END_MARKER}"
        )


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

    def test_refactor_self_doc_reject_wording_is_policy_gated(self) -> None:
        checked = [SKILL_ROOT / "SKILL.md", *sorted((SKILL_ROOT / "prompts").glob("*.md"))]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in checked)

        self.assertIn("HOST_REFACTOR_COMMENT_POLICY", combined)
        self.assertIn("self-doc-comment", combined)
        self.assertIn("none", combined)
        self.assertNotIn(
            "Code self-documents refactors with the host-required refactor comment format when touching source.",
            combined,
        )
        self.assertNotIn(
            "Code self-documents the refactor** — every refactored type/method gets a 3-5 line comment",
            combined,
        )
        self.assertNotIn(
            "missing/illegible self-doc on a major refactor, or scope creep into unrelated cleanup",
            combined,
        )
        self.assertNotIn(
            "缺失任何一处且无合理 not-applicable 说明 → 标记缺陷。\n- 检查改动是否真正消除了",
            combined,
        )


class MultiNodeOwnershipSourceTests(unittest.TestCase):
    def test_issue193_requires_github_native_author_updated_at_contract(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                REPO_ROOT / "CLAUDE.md",
                SKILL_ROOT / "SKILL.md",
                SCRIPT_DIR / "codex_refactor_loop" / "ownership.py",
            )
        )
        for required in (
            "author.login",
            "updatedAt",
            "3 hours",
            "comments and labels are visibility only",
            ".refactor-loop/runs/ is per-node internal state",
        ):
            with self.subTest(required=required):
                self.assertIn(required, combined)

    def test_issue193_forbids_device_lease_claim_authority_primitives(self) -> None:
        checked = [*(SCRIPT_DIR / "codex_refactor_loop").rglob("*.py")]
        forbidden = (
            "AUTO_LOOP_NODE_ID",
            "DEVICE_ID",
            "refs/heads/auto-loop/leases",
            "GitRefLeaseRegistry",
            "DeviceLeaseDaemon",
            ".refactor-loop/device-claims",
            "WorkUnitClaim",
            "claimed:<device>",
        )
        for path in checked:
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                with self.subTest(path=path.relative_to(REPO_ROOT), token=token):
                    self.assertNotIn(token, text)

        docs = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8") + "\n" + (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        for token in forbidden:
            with self.subTest(doc_forbidden_token=token):
                self.assertIn(token, docs)

    def test_issue193_stale_takeover_side_effect_paths_require_visible_notice(self) -> None:
        checked = {
            "ownership.py": SCRIPT_DIR / "codex_refactor_loop" / "ownership.py",
            "monitors/concurrency.py": SCRIPT_DIR / "codex_refactor_loop" / "monitors" / "concurrency.py",
            "phase9/router.py": SCRIPT_DIR / "codex_refactor_loop" / "phase9" / "router.py",
            "sync/executor.py": SCRIPT_DIR / "codex_refactor_loop" / "sync" / "executor.py",
            "wakeup_plan.py": SCRIPT_DIR / "codex_refactor_loop" / "wakeup_plan.py",
        }
        for label, path in checked.items():
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=label):
                if label == "wakeup_plan.py":
                    self.assertIn("requires_takeover_notice", text)
                else:
                    self.assertIn("post_takeover_notice", text)
                self.assertIn("stale-takeover", text)


if __name__ == "__main__":
    unittest.main()
