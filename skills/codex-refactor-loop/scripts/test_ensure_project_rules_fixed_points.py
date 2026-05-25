#!/usr/bin/env python3
"""Behavior tests for ensure_project_rules_fixed_points.py."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ensure_project_rules_fixed_points import (
    CANONICAL_BODY,
    CANONICAL_HASH,
    END_MARKER,
    OLD_CANONICAL_BODY,
    ProjectRulesFixedPointEnsurer,
    START_RE,
    sha256_text,
)

SCRIPT_PATH = Path(__file__).with_name("ensure_project_rules_fixed_points.py")
SKILL_ROOT = SCRIPT_PATH.parents[1]
REPO_ROOT = SCRIPT_PATH.parents[3]

PROMPTS_WITH_MANDATORY_PROJECT_RULES_INPUT = (
    "audit.md",
    "design-issue-reply.md",
    "implement.md",
    "remote-ci-fix.md",
    "review-fix.md",
    "reviewer-architect.md",
    "solver-delete.md",
    "solver-minimal.md",
    "solver-structural.md",
    "test-add.md",
    "triage-external-issue.md",
    "verify.md",
)


class ProjectRulesFixedPointEnsurerTests(unittest.TestCase):
    # Refactor (iter1/host-claude-md-fixed-points):
    #   Old pattern: host 的 PROJECT_RULES/CLAUDE.md 不保证基础不动点(泛化理论)在场,跑 loop 时基础理论未被可靠加载
    #   New principle: Phase 0 ProjectRulesFixedPointEnsurer 幂等向 $PROJECT_RULES 写入带 sentinel 的 managed 不动点区块(consensus:minimal,不覆盖 host 已有内容)
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.rules = self.repo / "CLAUDE.md"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def ensure(self, project_rules: str = "CLAUDE.md") -> str:
        return ProjectRulesFixedPointEnsurer(str(self.repo), project_rules).ensure()

    def run_cli(self, env_updates: dict[str, str] | None = None, *, clear_rules_env: bool = True) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        if clear_rules_env:
            env.pop("REPO_ROOT", None)
            env.pop("PROJECT_RULES", None)
        if env_updates:
            env.update(env_updates)
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_cli_default_project_rules_writes_and_reports_status(self) -> None:
        self.rules.write_text("# Host rules\nExisting text.\n", encoding="utf-8")

        first = self.run_cli({"REPO_ROOT": str(self.repo)})

        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stdout, "PROJECT_RULES_FIXED_POINT:updated\n")
        self.assertEqual(first.stderr, "")
        self.assertIn(CANONICAL_BODY, self.rules.read_text(encoding="utf-8"))

        second = self.run_cli({"REPO_ROOT": str(self.repo)})

        self.assertEqual(second.returncode, 0)
        self.assertEqual(second.stdout, "PROJECT_RULES_FIXED_POINT:already-current\n")
        self.assertEqual(second.stderr, "")

    def test_cli_explicit_project_rules_targets_nested_file(self) -> None:
        nested_rules = self.repo / "docs" / "RULES.md"
        nested_rules.parent.mkdir()
        nested_rules.write_text("# Nested host rules\n", encoding="utf-8")

        result = self.run_cli({"REPO_ROOT": str(self.repo), "PROJECT_RULES": "docs/RULES.md"})

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "PROJECT_RULES_FIXED_POINT:updated\n")
        self.assertEqual(result.stderr, "")
        self.assertIn(CANONICAL_BODY, nested_rules.read_text(encoding="utf-8"))
        self.assertFalse(self.rules.exists())

    def test_cli_missing_repo_root_fails_closed_without_modifying_files(self) -> None:
        original = "# Host rules\n"
        self.rules.write_text(original, encoding="utf-8")

        result = self.run_cli()

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("PROJECT_RULES_FIXED_POINT_ERROR: REPO_ROOT is required", result.stderr)
        self.assertEqual(self.rules.read_text(encoding="utf-8"), original)

    def test_first_append_adds_one_managed_block(self) -> None:
        self.rules.write_text("# Host rules\nExisting text.\n", encoding="utf-8")

        status = self.ensure()

        text = self.rules.read_text(encoding="utf-8")
        self.assertEqual(status, "updated")
        self.assertTrue(text.startswith("# Host rules\nExisting text.\n\n\n"))
        self.assertEqual(text.count("consensus-rnd:foundational-invariants:start"), 1)
        self.assertEqual(text.count(END_MARKER), 1)
        self.assertIn(f"sha256={CANONICAL_HASH}", text)
        self.assertIn(CANONICAL_BODY, text)

    def test_repeated_ensure_is_byte_stable(self) -> None:
        self.rules.write_text("# Host rules\n", encoding="utf-8")
        self.ensure()
        once = self.rules.read_bytes()

        status = self.ensure()

        self.assertEqual(status, "already-current")
        self.assertEqual(self.rules.read_bytes(), once)

    def test_preserves_content_outside_managed_block(self) -> None:
        prefix = "# Host rules\nKeep this.\n"
        suffix = "\n## Host extension\nKeep that.\n"
        block = ProjectRulesFixedPointEnsurer(str(self.repo))._managed_block()
        self.rules.write_text(prefix + "\n\n" + block + suffix, encoding="utf-8")

        self.ensure()

        text = self.rules.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(prefix))
        self.assertTrue(text.endswith(suffix))

    def test_missing_rules_file_is_refused(self) -> None:
        with self.assertRaisesRegex(Exception, "does not exist"):
            self.ensure()

    def test_empty_rules_file_is_refused(self) -> None:
        self.rules.write_text("", encoding="utf-8")

        with self.assertRaisesRegex(Exception, "empty"):
            self.ensure()

    def test_unreadable_rules_file_is_refused(self) -> None:
        self.rules.write_text("# Host rules\n", encoding="utf-8")
        self.rules.chmod(0)
        try:
            with self.assertRaisesRegex(Exception, "unreadable"):
                self.ensure()
        finally:
            self.rules.chmod(0o600)

    def test_path_escape_is_refused(self) -> None:
        self.rules.write_text("# Host rules\n", encoding="utf-8")

        with self.assertRaisesRegex(Exception, "must not contain|escapes"):
            ProjectRulesFixedPointEnsurer(str(self.repo), "../CLAUDE.md")

    def test_absolute_path_outside_repo_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as outside_tmp:
            outside_rules = Path(outside_tmp) / "CLAUDE.md"
            outside_rules.write_text("# Outside host rules\n", encoding="utf-8")

            with self.assertRaisesRegex(Exception, "escapes REPO_ROOT"):
                ProjectRulesFixedPointEnsurer(str(self.repo), str(outside_rules))

    def test_duplicate_marker_is_refused_without_changes(self) -> None:
        block = ProjectRulesFixedPointEnsurer(str(self.repo))._managed_block()
        original = "# Host rules\n\n" + block + "\n\n" + block
        self.rules.write_text(original, encoding="utf-8")

        with self.assertRaisesRegex(Exception, "duplicate"):
            self.ensure()

        self.assertEqual(self.rules.read_text(encoding="utf-8"), original)

    def test_unpaired_marker_is_refused_without_changes(self) -> None:
        original = "# Host rules\n\n<!-- consensus-rnd:foundational-invariants:start version=1 sha256=" + ("0" * 64) + " -->\n"
        self.rules.write_text(original, encoding="utf-8")

        with self.assertRaisesRegex(Exception, "missing or unbalanced"):
            self.ensure()

        self.assertEqual(self.rules.read_text(encoding="utf-8"), original)

    def test_manual_edit_inside_block_fails_closed(self) -> None:
        block = ProjectRulesFixedPointEnsurer(str(self.repo))._managed_block()
        original = "# Host rules\n\n" + block.replace("FI-007 删除优先", "FI-007 手工改动")
        self.rules.write_text(original, encoding="utf-8")

        with self.assertRaisesRegex(Exception, "hash mismatch"):
            self.ensure()

        self.assertEqual(self.rules.read_text(encoding="utf-8"), original)

    def test_hash_valid_unknown_block_version_fails_closed(self) -> None:
        unknown_body = "## 共识研发不动点（由 consensus-rnd 管理）\n\n- FI-999 未知版本。\n"
        unknown_hash = sha256_text(unknown_body)
        unknown_block = (
            f"<!-- consensus-rnd:foundational-invariants:start version=1 sha256={unknown_hash} -->\n"
            f"{unknown_body}"
            f"{END_MARKER}"
        )
        original = "# Host rules\n\n" + unknown_block
        self.rules.write_text(original, encoding="utf-8")

        with self.assertRaisesRegex(Exception, "unknown managed block version"):
            self.ensure()

        self.assertEqual(self.rules.read_text(encoding="utf-8"), original)

    def test_known_old_hash_upgrades_only_managed_block(self) -> None:
        old_hash = sha256_text(OLD_CANONICAL_BODY)
        old_block = (
            f"<!-- consensus-rnd:foundational-invariants:start version=1 sha256={old_hash} -->\n"
            f"{OLD_CANONICAL_BODY}"
            f"{END_MARKER}"
        )
        original = "# Host rules\n\n" + old_block + "\n\n## Host extension\n"
        self.rules.write_text(original, encoding="utf-8")

        status = self.ensure()

        text = self.rules.read_text(encoding="utf-8")
        self.assertEqual(status, "updated")
        self.assertTrue(text.startswith("# Host rules\n\n"))
        self.assertTrue(text.endswith("\n\n## Host extension\n"))
        self.assertIn(CANONICAL_BODY, text)
        self.assertEqual(START_RE.search(text).group(1), CANONICAL_HASH)


class ProjectRulesPromptContractTests(unittest.TestCase):
    # Refactor (iter1/host-claude-md-fixed-points):
    #   Old pattern: actor prompts could regress to hardcoded $REPO_ROOT/CLAUDE.md as the mandatory rules input while helper tests still passed
    #   New principle: source-regression coverage keeps actor prompts wired to $REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}
    def test_actor_prompts_keep_project_rules_as_mandatory_rules_input(self) -> None:
        prompts_root = SKILL_ROOT / "prompts"

        for prompt_name in PROMPTS_WITH_MANDATORY_PROJECT_RULES_INPUT:
            prompt = prompts_root / prompt_name
            text = prompt.read_text(encoding="utf-8")
            with self.subTest(prompt=prompt_name):
                self.assertIn("$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}", text)

        for prompt in sorted(prompts_root.glob("*.md")):
            text = prompt.read_text(encoding="utf-8")
            with self.subTest(no_hardcoded_rules_input=prompt.name):
                self.assertNotIn("$REPO_ROOT/CLAUDE.md", text)

    def test_phase0_runtime_contract_names_resolved_project_rules_target(self) -> None:
        skill_text = (REPO_ROOT / "skills" / "codex-refactor-loop" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("ProjectRulesFixedPointEnsurer(强制,先于任何 actor 派发)", skill_text)
        self.assertIn("$REPO_ROOT/${PROJECT_RULES:-CLAUDE.md}", skill_text)
        self.assertIn("helper 退出非 0 → bootstrap fail closed", skill_text)


class WorkUnitV1SourceRegressionTests(unittest.TestCase):
    # Refactor (iter2/cluster-007-work-unit-contract-schema):
    #   Old pattern: work-unit state contract existed only as prose, so migration/envelope terms could re-enter the skill unnoticed
    #   New principle: source-regression coverage keeps WorkUnitV1 v1 containers authoritative and blocks premature work_units_* migration surface
    def render_work_unit_template(self, *, work_unit_id: str | None, cluster_id: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / "template.md"
            output = Path(tmp) / "rendered.md"
            template.write_text(
                "primary={{work_unit_id}}\nlegacy={{cluster_id}}\nunresolved={{work_unit_id}}\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "REPO_ROOT": str(REPO_ROOT),
                    "CLUSTER_ID": cluster_id,
                    "ITERATION": "2",
                    "WORKTREE_PATH": "/tmp/worktree",
                    "BRANCH": "refactor/test",
                    "OLD_PATTERN": "old",
                    "NEW_PRINCIPLE": "new",
                    "SCOPE_PATHS": "skills/codex-refactor-loop",
                    "VERIFICATION_HINTS": "render test",
                }
            )
            if work_unit_id is None:
                env.pop("WORK_UNIT_ID", None)
            else:
                env["WORK_UNIT_ID"] = work_unit_id

            script = f'source "{SKILL_ROOT / "scripts" / "controller_lib.sh"}"; render_template "$TEMPLATE" "$OUTPUT"'
            result = subprocess.run(
                ["bash", "-lc", script],
                env={**env, "TEMPLATE": str(template), "OUTPUT": str(output)},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            return output.read_text(encoding="utf-8")

    def test_work_unit_v1_contract_markers_are_present(self) -> None:
        reference_text = (SKILL_ROOT / "REFERENCE.md").read_text(encoding="utf-8")
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        implement_prompt = (SKILL_ROOT / "prompts" / "implement.md").read_text(encoding="utf-8")
        verify_prompt = (SKILL_ROOT / "prompts" / "verify.md").read_text(encoding="utf-8")
        controller_lib = (SKILL_ROOT / "scripts" / "controller_lib.sh").read_text(encoding="utf-8")
        combined = "\n".join([reference_text, skill_text, implement_prompt, verify_prompt, controller_lib])

        required_markers = (
            "WorkUnitV1",
            "work_unit_schema_version",
            "work_unit_id == id == cluster_id == legacy_cluster_id",
            "WORK_UNIT_ID=$CLUSTER_ID",
            "must not fabricate `cluster_id` or",
            "`legacy_cluster_id`",
            "s/\\{\\{work_unit_id\\}\\}/($ENV{WORK_UNIT_ID} || $ENV{CLUSTER_ID})/ge",
        )

        for marker in required_markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, combined)

    def test_work_unit_v1_forbidden_migration_surface_is_absent(self) -> None:
        checked_paths = [
            SKILL_ROOT / "REFERENCE.md",
            SKILL_ROOT / "SKILL.md",
            SKILL_ROOT / "prompts" / "triage-external-issue.md",
            SKILL_ROOT / "prompts" / "implement.md",
            SKILL_ROOT / "prompts" / "verify.md",
            SKILL_ROOT / "prompts" / "meta-judge.md",
        ]
        forbidden_tokens = tuple(f"work_units_{name}" for name in ("planned", "active", "done", "failed")) + (
            "WorkUnit" + "EnvelopeV1",
            "WorkUnit" + "ProducerV1",
            "work_unit_" + "producer.py",
            "producer " + "registry",
        )

        for path in checked_paths:
            text = path.read_text(encoding="utf-8")
            for token in forbidden_tokens:
                with self.subTest(path=path.name, token=token):
                    self.assertNotIn(token, text)

    def test_render_template_prefers_work_unit_id_over_cluster_alias(self) -> None:
        rendered = self.render_work_unit_template(work_unit_id="unit-123", cluster_id="cluster-007")

        self.assertIn("primary=unit-123", rendered)
        self.assertIn("legacy=cluster-007", rendered)
        self.assertNotIn("{{work_unit_id}}", rendered)

    def test_render_template_falls_back_to_cluster_id_when_work_unit_id_is_unset(self) -> None:
        rendered = self.render_work_unit_template(work_unit_id=None, cluster_id="cluster-007")

        self.assertIn("primary=cluster-007", rendered)
        self.assertNotIn("{{work_unit_id}}", rendered)

    def test_v1_producer_contract_markers_are_present(self) -> None:
        reference_text = (SKILL_ROOT / "REFERENCE.md").read_text(encoding="utf-8")
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        triage_prompt = (SKILL_ROOT / "prompts" / "triage-external-issue.md").read_text(encoding="utf-8")
        combined = "\n".join([reference_text, skill_text, triage_prompt])

        required_markers = (
            "Producers in v1",
            "- `audit`",
            "- `manual-issue`",
            "kind: audit-cluster",
            "kind: manual-work-unit",
            "producer: audit",
            "producer: manual-issue",
            "source_ref: .refactor-loop/runs/audit-iter-N.md#<cluster-id>",
            "source_ref: gh-issue-<N>",
            "source_ref: gh-issue-${ISSUE_NUMBER}",
            "Work-unit production (audit default)",
        )

        for marker in required_markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, combined)

    def test_manual_issue_reshape_requires_work_unit_v1_fields_without_audit_aliases(self) -> None:
        triage_prompt = (SKILL_ROOT / "prompts" / "triage-external-issue.md").read_text(encoding="utf-8")

        required_markers = (
            "work_unit_id: issue-${ISSUE_NUMBER}",
            "kind: manual-work-unit",
            "producer: manual-issue",
            "source_ref: gh-issue-${ISSUE_NUMBER}",
            "scope_paths",
            "problem / invariant text",
            "verification_hints",
            "\u4e0d\u5199 `cluster_id` \u6216 `legacy_cluster_id`",
        )

        for marker in required_markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, triage_prompt)

    def test_triage_prompt_drops_old_refactor_only_and_docs_tooling_gates(self) -> None:
        triage_prompt = (SKILL_ROOT / "prompts" / "triage-external-issue.md").read_text(encoding="utf-8")

        forbidden_markers = (
            "\u5c5e\u4e8e\u672c refactor loop \u8303\u7574(\u8fdd\u53cd PROJECT_RULES/AGENTS \u6761\u6b3e)",
            "\u4e0d\u662f docs-only \u6216 tooling-only",
            "docs-only \u2014 \u4ec5\u6587\u6863\u95ee\u9898",
            "tooling-only \u2014 CLI / build / IDE \u95ee\u9898",
        )

        for marker in forbidden_markers:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, triage_prompt)

    def test_audit_prompt_remains_raw_artifact_contract(self) -> None:
        audit_prompt = (SKILL_ROOT / "prompts" / "audit.md").read_text(encoding="utf-8")

        for marker in ("producer: audit", "WorkUnitV1", "manual-issue"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, audit_prompt)


class NamingPolicySourceRegressionTests(unittest.TestCase):
    # Refactor (iter2/cluster-010-rename-alias-strategy):
    #   Old pattern: public copy could drift back toward a mandatory rename or grow a duplicate alias identity
    #   New principle: Consensus R&D is the product identity while codex-refactor-loop remains the only installed skill entrypoint
    def test_consensus_identity_keeps_stable_skill_entrypoint_without_alias_surface(self) -> None:
        skill_files = sorted(path.relative_to(REPO_ROOT).as_posix() for path in (REPO_ROOT / "skills").glob("*/SKILL.md"))
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        reference_text = (SKILL_ROOT / "REFERENCE.md").read_text(encoding="utf-8")
        readme_text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        public_copy = "\n".join([readme_text, skill_text, reference_text])

        self.assertEqual(skill_files, ["skills/codex-refactor-loop/SKILL.md"])
        self.assertIn("name: codex-refactor-loop", skill_text)
        self.assertIn("Consensus R&D", public_copy)
        self.assertIn("stable installed skill entrypoint", reference_text)
        self.assertIn("不新增重复 alias skill", readme_text)

        forbidden_markers = (
            "SkillIdentityV1",
            "name: consensus-rnd-loop",
            "name: codex-consensus-loop",
            "aliases:",
            "alias:",
        )
        for marker in forbidden_markers:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, public_copy)


if __name__ == "__main__":
    unittest.main()
