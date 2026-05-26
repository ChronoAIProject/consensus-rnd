#!/usr/bin/env python3
"""Behavior tests for ensure_project_rules_fixed_points.py."""

from __future__ import annotations

import json
import os
import re
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

# Refactor (iter3/skill-human-label-taxonomy):
#   Old: 四个 Human label(含两个 🆘),no-gap/escalation 判定散落
#   New principle: 恰好两个 active Human label;causes 移到 reason surface(#15 structural 共识)
CANONICAL_HUMAN_LABELS = {"🤖 human:auto-推进", "👤 human:需-maintainer-决策"}  # refactor helper, no behavior change
NON_AUTO_HUMAN_LABEL = "👤 human:需-maintainer-决策"  # refactor helper, no behavior change
REMOVED_HUMAN_LABELS = {"🆘 human:卡死", "🆘 human:卡死-需-rework"}  # refactor helper, no behavior change

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

    # Refactor (iter3/skill-github-post-contract):
    #   Old: 宽泛 all-prompts direct-post 主张
    #   New: 两组明确 roster + 可枚举行为测试(#13 structural 共识)
    def test_github_post_contract_matches_prompt_roster(self) -> None:
        prompts_root = SKILL_ROOT / "prompts"
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        direct_post_prompts = {
            "solver-minimal.md",
            "solver-structural.md",
            "solver-delete.md",
            "meta-judge.md",
            "reviewer-architect.md",
            "reviewer-quality.md",
            "reviewer-tests.md",
            "review-fix.md",
            "design-issue-reply.md",
            "triage-external-issue.md",
        }
        marker_only_prompts = {
            "audit.md",
            "design-issue-body.md",
            "implement.md",
            "verify.md",
            "remote-ci-fix.md",
            "test-add.md",
        }
        prompt_inventory = {path.name for path in prompts_root.glob("*.md")} - {"_github-post-rules.md"}

        self.assertEqual(prompt_inventory, direct_post_prompts | marker_only_prompts)
        self.assertFalse(direct_post_prompts & marker_only_prompts)
        self.assertIn("Direct-post prompts", skill_text)
        self.assertIn("Marker/artifact-only prompts", skill_text)
        self.assertNotIn("所有 prompts 末尾都有 `## GitHub post (强制)`", skill_text)

        for prompt_name in sorted(direct_post_prompts):
            text = (prompts_root / prompt_name).read_text(encoding="utf-8")
            with self.subTest(prompt=prompt_name, contract="direct-post"):
                self.assertIn("## GitHub post", text)
                self.assertIn("_github-post-rules.md", text)

        for prompt_name in sorted(marker_only_prompts):
            text = (prompts_root / prompt_name).read_text(encoding="utf-8")
            with self.subTest(prompt=prompt_name, contract="marker-only"):
                self.assertNotIn("## GitHub post", text)
                self.assertIn("AI 内容标识符", text)
                self.assertIn("⟦AI:AUTO-LOOP⟧", text)


class ContributingDocSourceRegressionTests(unittest.TestCase):
    def read_contributing(self) -> str:
        return (REPO_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")

    def test_contributing_doc_exists_with_stable_anchors(self) -> None:
        contributing = REPO_ROOT / "CONTRIBUTING.md"
        self.assertTrue(contributing.exists())
        text = contributing.read_text(encoding="utf-8")
        anchors = (
            "#development-flow",
            "#issues",
            "#commits",
            "#pull-requests",
            "#skill-changes",
            "#style-and-format",
            "#ai-generated-content",
            "#policy-boundaries",
        )

        for anchor in anchors:
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, text)

    def test_contributing_doc_links_authoritative_owners(self) -> None:
        text = self.read_contributing()
        required_literals = (
            "CLAUDE.md",
            "README.md",
            "skills/codex-refactor-loop/SKILL.md",
            "#26",
            "#31",
            "#32",
            "#20",
            "#17",
            ".version-bump.json",
            "superpowers:writing-skills",
        )

        for literal in required_literals:
            with self.subTest(literal=literal):
                self.assertIn(literal, text)

    def test_contributing_doc_carries_machine_grep_literals(self) -> None:
        text = self.read_contributing()
        required_literals = (
            "feat(skill):",
            "fix(skill):",
            "refactor(skill):",
            "docs(skill):",
            "chore:",
            "⟦AI:AUTO-LOOP⟧",
            "auto-loop-triage",
            "refactor-design-needed",
            "phase9-auto-solve",
            "frontmatter",
            "Use when",
            "REFERENCE.md",
            "scripts/",
            "prompts/",
        )

        for literal in required_literals:
            with self.subTest(literal=literal):
                self.assertIn(literal, text)

    def test_contributing_doc_does_not_redefine_other_policy(self) -> None:
        text = self.read_contributing()
        forbidden_literals = (
            "2 approve",
            "unanimous approve",
            "consensus-rnd-ci",
            "contract-tests",
            "manifest-version-sync",
            "lint-advisory",
            "workflow_dispatch",
            "git tag",
            "gh release create",
            "npm publish",
            "C#",
            ".NET",
            "proto",
            "branch protection command",
        )

        for literal in forbidden_literals:
            with self.subTest(literal=literal):
                self.assertNotIn(literal, text)


class Phase8MergePolicySourceRegressionTests(unittest.TestCase):
    # Refactor (iter3/skill-merge-policy): Old pattern: unanimous-approve merge gate + Phase 8 文案矛盾  New principle: 固定真值表 reject=0 && approve>=1 → MERGE;comment 是 advisory(#26 minimal option B 共识)
    def read_skill(self) -> str:
        return (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    def read_reference(self) -> str:
        return (SKILL_ROOT / "REFERENCE.md").read_text(encoding="utf-8")

    def phase8_docs(self) -> str:
        return "\n".join([self.read_skill(), self.read_reference()])

    def test_phase8_docs_define_option_a_truth_table(self) -> None:
        docs = self.phase8_docs()

        required_markers = (
            "`MERGE`",
            "`MERGE_WITH_COMMENTS`",
            "`WAIT_EXPLICIT_APPROVAL`",
            "`FIX`",
            "`WAIT_OR_REDISPATCH`",
            "`reject=0`, `approve=R`, `comment=0`",
            "`reject=0`, `approve>=1`, `comment>=1`, `approve+comment=R`",
            "`reject=0`, `approve=0`, `comment=R`",
            "`reject>=1`",
            "missing role, duplicate/unknown verdict, no `EXIT=0`, stale head SHA, CI pending/fail, or non-mergeable PR",
        )

        for marker in required_markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, docs)

    def test_phase8_docs_do_not_use_unanimous_approve_as_merge_gate(self) -> None:
        docs = self.phase8_docs()

        forbidden_gate_terms = (
            "unanimous approve",
            "unanimous-approve consensus",
            "All approve except 1 comment",
            "2 approve + 1 comment",
            "partial-comment",
        )

        for term in forbidden_gate_terms:
            with self.subTest(term=term):
                self.assertNotIn(term, docs)

    def test_peek_uses_option_a_threshold(self) -> None:
        peek = (SKILL_ROOT / "scripts" / "peek.sh").read_text(encoding="utf-8")

        self.assertIn('hint="→ latest complete reviewer round: reject=0 + approve>=1 => merge; all-comment => WAIT_EXPLICIT_APPROVAL"', peek)
        self.assertIn('if [ "$reject" = "0" ] && [ "$approve" -ge 1 ]; then', peek)
        self.assertNotIn('"$approve" -ge 2', peek)
        self.assertNotIn("≥2 approve", peek)

    def test_peek_all_comment_round_is_not_merge_ready(self) -> None:
        peek = (SKILL_ROOT / "scripts" / "peek.sh").read_text(encoding="utf-8")

        self.assertIn('elif [ "$reject" = "0" ] && [ "$approve" = "0" ] && [ "$comment" -ge 1 ]; then', peek)
        self.assertIn("WAIT_EXPLICIT_APPROVAL", peek)
        self.assertIn("do not merge", peek)

    def test_review_fix_blocks_only_on_reject(self) -> None:
        review_fix = (SKILL_ROOT / "prompts" / "review-fix.md").read_text(encoding="utf-8")

        self.assertIn("blocking demands come only from `reject` reviewer evidence", review_fix)
        self.assertIn("Comments are context: read them and surface them in the report, but do not treat them as mandatory fix demands", review_fix)
        self.assertNotIn("For each `reject` AND each `comment`, extract", review_fix)
        self.assertNotIn("unanimous approve", review_fix)

    def test_reviewer_prompts_force_must_fix_to_reject(self) -> None:
        prompt_names = ("reviewer-architect.md", "reviewer-tests.md", "reviewer-quality.md")

        for prompt_name in prompt_names:
            text = (SKILL_ROOT / "prompts" / prompt_name).read_text(encoding="utf-8")
            with self.subTest(prompt=prompt_name):
                self.assertIn("verdict: approve | comment | reject", text)
                self.assertIn("In-scope must-fix-before-merge findings must be `reject`", text)
                self.assertIn("Out-of-scope, non-flippable, or advisory findings must be `comment`", text)

    def test_no_shared_phase8_policy_module_added(self) -> None:
        self.assertFalse((SKILL_ROOT / "scripts" / "phase8_review_policy.py").exists())

    def test_controller_lib_stays_post_decision_lifecycle_primitive(self) -> None:
        controller_lib = (SKILL_ROOT / "scripts" / "controller_lib.sh").read_text(encoding="utf-8")
        reference = self.read_reference()

        self.assertIn("merge_pr()", controller_lib)
        self.assertIn("gh pr merge", controller_lib)
        self.assertIn("post-decision lifecycle primitive", reference)
        self.assertIn("already decided `MERGE` or `MERGE_WITH_COMMENTS`", reference)
        for forbidden in ("REVIEW_DONE", "MERGE_WITH_COMMENTS", "WAIT_EXPLICIT_APPROVAL", "approve>=", "reject=0"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, controller_lib)


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

    def test_v1_operational_tokens_are_stable_and_not_renamed(self) -> None:
        # Refactor (iter2/cluster-009-marker-label-compat-migration):
        #   Old pattern: marker/label 命名与 refactor 外壳耦合,无显式稳定契约
        #   New principle: minimal docs+test 固化 marker/label 为稳定 v1 operational tokens(保持现状,不重命名);不引入 OperationalNamePolicyV1(#5 structural 共识)
        reference_text = (SKILL_ROOT / "REFERENCE.md").read_text(encoding="utf-8")
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        combined = "\n".join([reference_text, skill_text])

        required_markers = (
            "Stable v1 operational tokens",
            "stable v1 operational names",
            "[refactor-design]",
            "refactor-design-needed",
            "auto-loop",
            "phase9-auto-solve",
            "auto-loop-resume",
            "refactor/iterN-<cluster-id>",
            ".refactor-loop/.../<cluster-id>",
            "IMPLEMENT_DONE:${CLUSTER_ID}",
            "VERIFY_DONE:${CLUSTER_ID}",
            "SOLVER_DONE",
            "META_JUDGE_DONE",
            "does not rename, dual-write, or add aliases",
        )

        for marker in required_markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, combined)

        forbidden_tokens = (
            "work-unit-design-needed",
            "[work-unit-design]",
            "WORK_UNIT_DONE",
            "IMPLEMENT_DONE:${WORK_UNIT_ID}",
            "VERIFY_DONE:${WORK_UNIT_ID}",
            "work-unit/iter",
        )

        for token in forbidden_tokens:
            with self.subTest(token=token):
                self.assertNotIn(token, combined)


class ConcurrencyFloorSourceRegressionTests(unittest.TestCase):
    # Refactor (iter3/skill-concurrency-floor-enforcement):
    #   Old pattern: concurrency_monitor 有误导性 low-threshold 路径,CODEX_FLOOR 强制职责不清
    #   New principle: monitor 保持 no-gap-only;删 stale low-threshold 路径;CODEX_FLOOR 补给仅 controller wakeup step 1.5;SKILL 澄清职责(#14 delete 共识)
    def test_concurrency_monitor_is_no_gap_only(self) -> None:
        monitor_text = (SKILL_ROOT / "scripts" / "concurrency_monitor.py").read_text(encoding="utf-8")

        self.assertIn("no-gap-violation", monitor_text)
        self.assertIn("expected > 0 and actual == 0", monitor_text)
        for forbidden in (
            "MIN_PARALLEL",
            "codex-floor-deficit",
            "floor-deficit",
            "codex-concurrency-low",
            "low_streak",
            "actual < threshold",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, monitor_text)
        self.assertNotIn('os.environ.get("CODEX_FLOOR"', monitor_text)

    def test_skill_assigns_floor_to_controller_step_1_5_only(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        reference_text = (SKILL_ROOT / "REFERENCE.md").read_text(encoding="utf-8")

        self.assertIn("concurrency_monitor.py` 只做 no-gap sentinel", skill_text)
        self.assertIn("controller 每次 wakeup 的 step 1.5", skill_text)
        self.assertIn("必须在任何 `ScheduleWakeup` 之前执行", skill_text)
        self.assertIn("FLOOR=$(( ${CODEX_FLOOR:-5} < 2 ? 2 : ${CODEX_FLOOR:-5} ))", skill_text)
        self.assertNotIn("low 规则:`actual < expected/2`", skill_text)
        self.assertNotIn("codex-floor-deficit", skill_text)
        self.assertNotIn("ACTIVE <= 2", skill_text)
        self.assertIn("[concurrency floor details](REFERENCE.md#concurrency-floor-details)", skill_text)
        self.assertEqual(reference_text.count("**判定脚本**(controller wakeup step 1.5):"), 1)


class HumanLabelTaxonomySourceRegressionTests(unittest.TestCase):
    # Refactor (iter3/skill-human-label-taxonomy):
    #   Old: 四个 Human label(含两个 🆘),no-gap/escalation 判定散落
    #   New principle: 恰好两个 active Human label;causes 移到 reason surface(#15 structural 共识)
    def skill_text(self) -> str:
        return (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    def label_group_2(self) -> str:
        text = (SKILL_ROOT / "REFERENCE.md").read_text(encoding="utf-8")
        start = text.index("### Label 组 2 — Human")
        end = text.index("### Bootstrap", start)
        return text[start:end]

    def bootstrap_block(self) -> str:
        text = (SKILL_ROOT / "REFERENCE.md").read_text(encoding="utf-8")
        start = text.index("# 创建所有 human label")
        end = text.index("### 转移时刻代码模板", start)
        return text[start:end]

    def test_human_label_taxonomy_has_single_non_auto_label(self) -> None:
        label_group = self.label_group_2()
        bootstrap = self.bootstrap_block()

        for label in CANONICAL_HUMAN_LABELS:
            with self.subTest(canonical=label):
                self.assertIn(label, label_group)
                self.assertIn(f'gh label create "{label}"', bootstrap)

        self.assertEqual(label_group.count("| `🤖 human:auto-推进` |"), 1)
        self.assertEqual(label_group.count("| `👤 human:需-maintainer-决策` |"), 1)
        self.assertEqual(bootstrap.count("gh label create"), 2)

        for label in REMOVED_HUMAN_LABELS:
            with self.subTest(removed=label):
                self.assertNotIn(label, label_group)
                self.assertNotIn(f'gh label create "{label}"', bootstrap)

    def test_human_escalation_routes_use_reason_surface(self) -> None:
        skill = self.skill_text()
        reference = (SKILL_ROOT / "REFERENCE.md").read_text(encoding="utf-8")
        route_start = skill.index("Policy:the loop continues")
        route_end = skill.index("## Hard rules", route_start)
        route_table = skill[route_start:route_end]
        meta_start = reference.index("## Meta-layer escalation")
        meta_end = reference.index("<a id=\"label-bootstrap-loops\"></a>", meta_start)
        meta_layer = reference[meta_start:meta_end]
        combined = "\n".join([route_table, meta_layer])

        self.assertIn("META_RESOLVED:escalate-human", combined)
        self.assertIn(NON_AUTO_HUMAN_LABEL, combined)
        for token in ("reason", "banner", "PushNotification", "ci-stuck"):
            with self.subTest(reason_surface=token):
                self.assertIn(token, combined)
        for label in REMOVED_HUMAN_LABELS:
            with self.subTest(removed=label):
                self.assertNotIn(f"label `{label}`", combined)
                self.assertNotIn(f"`{label}` + PushNotification", combined)

    def test_monitor_waiting_predicate_only_accepts_maintainer_decision(self) -> None:
        sys.path.insert(0, str(SKILL_ROOT / "scripts"))
        old_env = os.environ.copy()
        os.environ.setdefault("REPO_ROOT", str(REPO_ROOT))
        try:
            import importlib
            import concurrency_monitor

            monitor = importlib.reload(concurrency_monitor)
            base = {"number": 1, "kind": "issue", "phase": "🔍 phase:design-solving"}

            expected, _ = monitor.compute_expected([{**base, "human": NON_AUTO_HUMAN_LABEL}])
            self.assertEqual(expected, 0)

            for label in REMOVED_HUMAN_LABELS:
                with self.subTest(removed=label):
                    expected, _ = monitor.compute_expected([{**base, "human": label}])
                    self.assertEqual(expected, 1)
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_controller_cleanup_removes_removed_human_labels_without_producing_them(self) -> None:
        controller_lib = (SKILL_ROOT / "scripts" / "controller_lib.sh").read_text(encoding="utf-8")

        for label in REMOVED_HUMAN_LABELS | {NON_AUTO_HUMAN_LABEL}:
            with self.subTest(cleanup_label=label):
                self.assertIn(f'--remove-label "{label}"', controller_lib)

        for line in controller_lib.splitlines():
            with self.subTest(line=line):
                self.assertFalse("--add-label" in line and "🆘 human:" in line)

    def test_peek_hints_do_not_recommend_emergency_human_label(self) -> None:
        peek = (SKILL_ROOT / "scripts" / "peek.sh").read_text(encoding="utf-8")

        self.assertIn('META_RESOLVED:escalate-human:*) hint="→ label 👤 + reason banner + push notify" ;;', peek)
        self.assertNotIn("label 🆘 + push notify", peek)

        for line in peek.splitlines():
            if "🆘" in line:
                with self.subTest(legacy_line=line):
                    self.assertTrue(
                        "startswith(\"🆘\")" in line
                        or "lstrip().startswith(('## 📊', '## 🤖', '## ✅', '## 🆘'))" in line
                        or "Old: 四个 Human label(含两个 🆘)" in line
                    )


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


class ScriptHygieneSourceRegressionTests(unittest.TestCase):
    # Refactor (iter3/skill-hygiene-scripts):
    #   Old: script hygiene bugs hid in git worktree metadata and shell eval quoting paths.
    #   New principle: deterministic source/fixture tests cover worktree merge detection, argv label cleanup, and log reuse safety.
    def run_git(self, repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=check,
        )

    def test_spawn_codex_refuses_unfinished_existing_log_without_truncating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompt = root / "prompt.md"
            log = root / "codex.log"
            prompt.write_text("say hello\n", encoding="utf-8")
            original_log = "SPAWN: old run\npartial output without terminal marker\n"
            log.write_text(original_log, encoding="utf-8")

            result = subprocess.run(
                [
                    "bash",
                    str(SKILL_ROOT / "scripts" / "spawn-codex.sh"),
                    "--cd",
                    str(root),
                    "--prompt",
                    str(prompt),
                    "--log",
                    str(log),
                    "--stall",
                    "1",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 3)
            self.assertIn("refusing to reuse unfinished log without EXIT=", result.stderr)
            self.assertEqual(log.read_text(encoding="utf-8"), original_log)
            self.assertNotIn("--overwrite-finished-log", (SKILL_ROOT / "scripts" / "spawn-codex.sh").read_text(encoding="utf-8"))

    def test_dev_sync_resolver_in_flight_is_scoped_to_this_repo_and_skips_shell_wrappers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            worktree = root / "repo-wt-dev-sync"
            sibling = root / "sibling"
            repo.mkdir()
            worktree.mkdir()
            sibling.mkdir()
            env = os.environ.copy()
            env.update(
                {
                    "REPO_ROOT": str(repo),
                    "WORKTREE": str(worktree),
                    "PYTHONPATH": str(SKILL_ROOT / "scripts"),
                    "REPO": str(repo),
                    "WORKTREE": str(worktree),
                    "SIBLING": str(sibling),
                }
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    """
import os
import types
import dev_sync_daemon

def check(line):
    dev_sync_daemon.run = lambda cmd: types.SimpleNamespace(stdout=line + "\\n")
    return dev_sync_daemon.codex_resolve_in_flight()

repo = os.environ["REPO"]
wt = os.environ["WORKTREE"]
sibling = os.environ["SIBLING"]
print(check(f"bash {repo}/.claude/skills/codex-refactor-loop/scripts/spawn-codex.sh --cd {wt} --log {repo}/.refactor-loop/logs/dev-sync-codex-1.log"))
print(check(f"bash {sibling}/.claude/skills/codex-refactor-loop/scripts/spawn-codex.sh --log {sibling}/.refactor-loop/logs/dev-sync-codex-1.log"))
print(check(f"bash -c {repo}/.claude/skills/codex-refactor-loop/scripts/spawn-codex.sh --log {repo}/.refactor-loop/logs/dev-sync-codex-1.log"))
""",
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), ["True", "False", "False"])

    def test_triage_monitor_state_helpers_recover_legacy_entries_and_advance_by_log_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = root / ".refactor-loop" / "triage-monitor-state.json"
            log_file = root / ".refactor-loop" / "logs" / "triage-issue-42.log"
            prompt_file = root / ".claude" / "skills" / "codex-refactor-loop" / "prompts" / "triage-external-issue.md"
            skill_root = root / ".claude" / "skills" / "codex-refactor-loop"
            spawn_file = skill_root / "scripts" / "spawn-codex.sh"
            state_file.parent.mkdir(parents=True)
            log_file.parent.mkdir(parents=True)
            prompt_file.parent.mkdir(parents=True)
            spawn_file.parent.mkdir(parents=True)
            state_file.write_text('{"42":"2026-01-01T00:00:00Z"}\n', encoding="utf-8")
            (skill_root / "SKILL.md").write_text("---\nname: codex-refactor-loop\n---\n", encoding="utf-8")
            prompt_file.write_text("Issue ${ISSUE_NUMBER}\nAuthor: maintainer\n", encoding="utf-8")
            spawn_file.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            triage_source = (SKILL_ROOT / "scripts" / "triage-monitor.sh").read_text(encoding="utf-8")
            triage_prefix = triage_source.split('log "triage-monitor started: interval=${INTERVAL}s"', 1)[0]
            triage_lib = root / "triage-monitor-functions.sh"
            triage_lib.write_text(triage_prefix, encoding="utf-8")
            scenario = root / "scenario.sh"
            scenario.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
source "$TRIAGE_LIB"
printf 'legacy=%s retries=%s\\n' "$(state_status 42)" "$(state_retries 42)"
set_state 42 claimed 0 0 "$LOG_FILE"
printf 'claimed=%s log=%s\\n' "$(state_status 42)" "$(state_log_file 42)"
printf 'partial_spawn=%s partial_exit=%s\\n' "$(log_has_spawn_or_exit_marker "$LOG_FILE" && echo yes || echo no)" "$(log_has_exit_marker "$LOG_FILE" && echo yes || echo no)"
printf 'SPAWN: prompt=x log=y cd=z stall=1s\\n' > "$LOG_FILE"
printf 'spawn=%s exit=%s\\n' "$(log_has_spawn_or_exit_marker "$LOG_FILE" && echo yes || echo no)" "$(log_has_exit_marker "$LOG_FILE" && echo yes || echo no)"
printf 'noise\\nEXIT=0\\nDONE_AT=now\\n' >> "$LOG_FILE"
printf 'done=%s\\n' "$(log_has_exit_marker "$LOG_FILE" && echo yes || echo no)"
TRIAGE_RETRY_BACKOFF_SECONDS=7
mark_failed_retry 42 0 "$LOG_FILE" missing-spawn-marker >/dev/null
jq -r '"failed=" + .["42"].status + " retries=" + (.["42"].retries|tostring) + " next=" + ((.["42"].next_attempt > 0)|tostring)' "$STATE_FILE"
""",
                encoding="utf-8",
            )
            scenario.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "REPO_ROOT": str(root),
                    "CODEX_REFACTOR_LOOP_SKILL_ROOT": str(skill_root),
                    "STATE_FILE": str(state_file),
                    "LOG_FILE": str(log_file),
                    "TRIAGE_LIB": str(triage_lib),
                    "TRIAGE_MAX_RETRIES": "3",
                    "TRIAGE_RETRY_BACKOFF_SECONDS": "300",
                }
            )
            result = subprocess.run(
                ["bash", str(scenario)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    "legacy=failed retries=0",
                    f"claimed=claimed log={log_file}",
                    "partial_spawn=no partial_exit=no",
                    "spawn=yes exit=no",
                    "done=yes",
                    "failed=failed retries=1 next=true",
                ],
            )

    def test_triage_monitor_loop_dispatches_state_machine_branches_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            skill_scripts = root / ".claude" / "skills" / "codex-refactor-loop" / "scripts"
            skill_prompts = root / ".claude" / "skills" / "codex-refactor-loop" / "prompts"
            logs = root / ".refactor-loop" / "logs"
            fakebin.mkdir()
            skill_scripts.mkdir(parents=True)
            skill_prompts.mkdir(parents=True)
            logs.mkdir(parents=True)

            (skill_prompts / "triage-external-issue.md").write_text(
                "Issue ${ISSUE_NUMBER}\nAuthor: maintainer\n",
                encoding="utf-8",
            )
            (root / ".claude" / "skills" / "codex-refactor-loop" / "SKILL.md").write_text(
                "---\nname: codex-refactor-loop\n---\n",
                encoding="utf-8",
            )
            (logs / "triage-issue-14-attempt-1.log").write_text("SPAWN: old\nEXIT=0\n", encoding="utf-8")
            (logs / "triage-issue-15-attempt-1.log").write_text("SPAWN: old\n", encoding="utf-8")

            state_file = root / ".refactor-loop" / "triage-monitor-state.json"
            state_file.write_text(
                json.dumps(
                    {
                        "10": {
                            "status": "claimed",
                            "retries": 0,
                            "next_attempt": 4102444800,
                            "log": str(logs / "triage-issue-10-attempt-1.log"),
                        },
                        "11": {
                            "status": "claimed",
                            "retries": 0,
                            "next_attempt": 0,
                            "log": str(logs / "triage-issue-11-attempt-1.log"),
                        },
                        "12": {
                            "status": "failed",
                            "retries": 1,
                            "next_attempt": 0,
                            "log": str(logs / "triage-issue-12-attempt-1.log"),
                        },
                        "14": {
                            "status": "spawned",
                            "retries": 0,
                            "next_attempt": 0,
                            "log": str(logs / "triage-issue-14-attempt-1.log"),
                        },
                        "15": {
                            "status": "claimed",
                            "retries": 0,
                            "next_attempt": 4102444800,
                            "log": str(logs / "triage-issue-15-attempt-1.log"),
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            gh = fakebin / "gh"
            gh.write_text(
                """#!/usr/bin/env bash
if [[ "$1" == "issue" && "$2" == "list" ]]; then
  printf '10 alice\\n11 bob\\n12 carol\\n13 dave\\n14 erin\\n15 frank\\n'
  exit 0
fi
exit 64
""",
                encoding="utf-8",
            )
            gh.chmod(0o755)

            spawn = skill_scripts / "spawn-codex.sh"
            spawn.write_text(
                """#!/usr/bin/env bash
set -euo pipefail
log=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --log) log="$2"; shift 2 ;;
    *) shift ;;
  esac
done
printf '%s\\n' "$ISSUE_NUMBER" >> "$REPO_ROOT/.refactor-loop/spawn-invocations.txt"
case "$ISSUE_NUMBER" in
  12|13) printf 'SPAWN: fake issue %s\\n' "$ISSUE_NUMBER" > "$log" ;;
  *) exit 65 ;;
esac
""",
                encoding="utf-8",
            )
            spawn.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}{os.pathsep}{env['PATH']}",
                    "REPO_ROOT": str(root),
                    "CODEX_REFACTOR_LOOP_SKILL_ROOT": str(root / ".claude" / "skills" / "codex-refactor-loop"),
                    "GH_REPO_SLUG": "owner/repo",
                    "TRIAGE_MONITOR_ONCE": "1",
                    "TRIAGE_MONITOR_TEST_WAIT_SPAWN": "1",
                    "TRIAGE_RETRY_BACKOFF_SECONDS": "1",
                    "TRIAGE_MAX_RETRIES": "3",
                }
            )
            result = subprocess.run(
                ["bash", str(SKILL_ROOT / "scripts" / "triage-monitor.sh")],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(state["10"]["status"], "claimed")
            self.assertEqual(state["10"]["next_attempt"], 4102444800)
            self.assertEqual(state["11"]["status"], "failed")
            self.assertEqual(state["11"]["retries"], 1)
            self.assertEqual(state["12"]["status"], "spawned")
            self.assertEqual(state["12"]["retries"], 1)
            self.assertEqual(state["13"]["status"], "spawned")
            self.assertEqual(state["14"]["status"], "done")
            self.assertEqual(state["15"]["status"], "spawned")
            self.assertEqual((root / ".refactor-loop" / "spawn-invocations.txt").read_text(encoding="utf-8").splitlines(), ["12", "13"])
            self.assertIn("failed: triage issue #11 attempt 1/3: missing-spawn-marker", result.stdout)
            self.assertIn("done: triage codex for issue #14", result.stdout)

    def test_dev_sync_merge_in_progress_detects_linked_worktree_gitdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            wt = root / "repo-wt-dev-sync"
            repo.mkdir()
            self.run_git(repo, "init")
            self.run_git(repo, "config", "user.email", "test@example.invalid")
            self.run_git(repo, "config", "user.name", "Test User")
            self.run_git(repo, "checkout", "-b", "dev")
            (repo / "conflict.txt").write_text("base\n", encoding="utf-8")
            self.run_git(repo, "add", "conflict.txt")
            self.run_git(repo, "commit", "-m", "base")
            self.run_git(repo, "branch", "auto-refact-dev")
            (repo / "conflict.txt").write_text("dev\n", encoding="utf-8")
            self.run_git(repo, "commit", "-am", "dev change")
            self.run_git(repo, "worktree", "add", "--detach", str(wt), "auto-refact-dev")
            (wt / "conflict.txt").write_text("integration\n", encoding="utf-8")
            self.run_git(wt, "commit", "-am", "integration change")
            merge = self.run_git(wt, "merge", "dev", check=False)

            self.assertNotEqual(merge.returncode, 0)
            self.assertTrue((wt / ".git").is_file())
            self.assertFalse((wt / ".git" / "MERGE_HEAD").exists())

            env = os.environ.copy()
            env.update(
                {
                    "REPO_ROOT": str(repo),
                    "WORKTREE": str(wt),
                    "PYTHONPATH": str(SKILL_ROOT / "scripts"),
                    "WT": str(wt),
                }
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import os; from pathlib import Path; import dev_sync_daemon; "
                    "print(dev_sync_daemon.merge_in_progress(Path(os.environ['WT'])))",
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "True")

    def test_sweep_stale_labels_passes_quoted_space_label_as_single_argv(self) -> None:
        label = 'quote "space" label'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fakebin = root / "bin"
            fakebin.mkdir()
            argv_log = root / "gh-argv.jsonl"
            controller_copy = root / "controller_lib.sh"
            controller_text = (SKILL_ROOT / "scripts" / "controller_lib.sh").read_text(encoding="utf-8")
            controller_text = controller_text.replace(
                "import json, sys",
                "import json, os, sys",
            ).replace(
                "stale = ['🚀 phase:pr-open',",
                "stale = [os.environ['TEST_STALE_LABEL'], '🚀 phase:pr-open',",
            )
            controller_copy.write_text(controller_text, encoding="utf-8")
            gh = fakebin / "gh"
            gh.write_text(
                """#!/usr/bin/env bash
python3 - "$@" <<'PY'
import json, os, sys
with open(os.environ["GH_ARGV_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\\n")
PY
if [[ "$1" == "issue" && "$2" == "list" ]]; then
  python3 - <<'PY'
import json
print(json.dumps([{"number": 42, "labels": [{"name": 'quote "space" label'}]}]))
PY
  exit 0
fi
if [[ "$1" == "pr" && "$2" == "list" ]]; then
  printf '[]\\n'
  exit 0
fi
exit 0
""",
                encoding="utf-8",
            )
            gh.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}{os.pathsep}{env['PATH']}",
                    "REPO_ROOT": str(root),
                    "GH_REPO_SLUG": "owner/repo",
                    "GH_ARGV_LOG": str(argv_log),
                    "TEST_STALE_LABEL": label,
                }
            )
            result = subprocess.run(
                ["bash", "-c", f'source "{controller_copy}"; sweep_stale_labels'],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            calls = [__import__("json").loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()]
            edit_call = next(call for call in calls if call[:3] == ["issue", "edit", "42"])
            remove_index = edit_call.index("--remove-label")
            self.assertEqual(edit_call[remove_index + 1], label)
            self.assertEqual(edit_call.count(label), 1)
            self.assertNotIn("eval", (SKILL_ROOT / "scripts" / "controller_lib.sh").read_text(encoding="utf-8"))

class SkillRootContractSourceRegressionTests(unittest.TestCase):
    """Source and behavior regressions for the host-agnostic skill root contract."""

    def read_rel(self, rel: str) -> str:
        return (REPO_ROOT / rel).read_text(encoding="utf-8")

    def test_dev_sync_daemon_self_locates_skill_root_inline(self) -> None:
        text = self.read_rel("skills/codex-refactor-loop/scripts/dev_sync_daemon.py")

        self.assertIn("def skill_root() -> Path:", text)
        self.assertIn("CODEX_REFACTOR_LOOP_SKILL_ROOT", text)
        self.assertIn("Path(__file__).resolve().parents[1]", text)
        self.assertIn('root / "SKILL.md"', text)
        self.assertIn('root / "scripts" / "spawn-codex.sh"', text)
        self.assertIn('root / "prompts"', text)
        self.assertIn("invalid codex-refactor-loop skill root", text)
        self.assertIn('SPAWN_CODEX = SKILL_ROOT / "scripts" / "spawn-codex.sh"', text)
        self.assertNotIn('SPAWN_CODEX = MAIN_REPO / ".claude"', text)
        self.assertNotIn(".claude/skills/codex-refactor-loop/scripts/dev_sync_daemon.py", text)
        self.assertIn(
            "# Refactor (iter3/skill-skill-root-contract): Old pattern: .claude/skills hardcoded lookup. New principle: self-locate from this script path, with optional validated CODEX_REFACTOR_LOOP_SKILL_ROOT override.",
            text,
        )

    def test_dev_sync_daemon_uses_valid_specific_skill_root_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            override = root / "override-skill"
            (override / "scripts").mkdir(parents=True)
            (override / "prompts").mkdir()
            repo.mkdir()
            (override / "SKILL.md").write_text("---\nname: codex-refactor-loop\n---\n", encoding="utf-8")
            spawn = override / "scripts" / "spawn-codex.sh"
            spawn.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            spawn.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "REPO_ROOT": str(repo),
                    "CODEX_REFACTOR_LOOP_SKILL_ROOT": str(override),
                    "PYTHONPATH": str(SKILL_ROOT / "scripts"),
                }
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import dev_sync_daemon; "
                        "print(dev_sync_daemon.SKILL_ROOT); "
                        "print(dev_sync_daemon.SPAWN_CODEX)"
                    ),
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [str(override.resolve()), str((override / "scripts" / "spawn-codex.sh").resolve())],
            )
            self.assertEqual(result.stderr, "")

    def test_triage_monitor_self_locates_before_state_mutation(self) -> None:
        text = self.read_rel("skills/codex-refactor-loop/scripts/triage-monitor.sh")

        self.assertIn("resolve_skill_root()", text)
        self.assertIn('CODEX_REFACTOR_LOOP_SKILL_ROOT', text)
        self.assertIn('${BASH_SOURCE[0]}', text)
        self.assertIn('resolved_skill_root="$(resolve_skill_root)"', text)
        self.assertIn('TRIAGE_PROMPT_TEMPLATE="$resolved_skill_root/prompts/triage-external-issue.md"', text)
        self.assertIn('SPAWN_CODEX="$resolved_skill_root/scripts/spawn-codex.sh"', text)
        self.assertIn("CODEX_REFACTOR_LOOP_SKILL_ROOT_PRINT", text)
        self.assertIn('"$TRIAGE_PROMPT_TEMPLATE"', text)
        self.assertIn('nohup bash "$SPAWN_CODEX"', text)
        self.assertIn(
            "# Refactor (iter3/skill-skill-root-contract): Old pattern: .claude/skills hardcoded lookup. New principle: self-locate from this script path, with optional validated CODEX_REFACTOR_LOOP_SKILL_ROOT override.",
            text,
        )
        self.assertLess(text.index('resolved_skill_root="$(resolve_skill_root)"'), text.index('STATE_FILE="$REPO_ROOT/.refactor-loop/triage-monitor-state.json"'))
        self.assertLess(text.index("CODEX_REFACTOR_LOOP_SKILL_ROOT_PRINT"), text.index('STATE_FILE="$REPO_ROOT/.refactor-loop/triage-monitor-state.json"'))
        self.assertLess(text.index('resolved_skill_root="$(resolve_skill_root)"'), text.index('[ -f "$STATE_FILE" ] || echo "{}" > "$STATE_FILE"'))
        self.assertLess(text.index('resolved_skill_root="$(resolve_skill_root)"'), text.index('jq --arg n "$issue"'))
        self.assertNotIn('$REPO_ROOT/.claude/skills/codex-refactor-loop/prompts/triage-external-issue.md', text)
        self.assertNotIn('$REPO_ROOT/.claude/skills/codex-refactor-loop/scripts/spawn-codex.sh', text)

    def test_triage_monitor_default_self_location_uses_bash_source_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            installed = Path(tmp) / "skills" / "codex-refactor-loop"
            scripts = installed / "scripts"
            prompts = installed / "prompts"
            scripts.mkdir(parents=True)
            prompts.mkdir()
            (installed / "SKILL.md").write_text("---\nname: codex-refactor-loop\n---\n", encoding="utf-8")
            triage = scripts / "triage-monitor.sh"
            triage.write_text((SKILL_ROOT / "scripts" / "triage-monitor.sh").read_text(encoding="utf-8"), encoding="utf-8")
            triage.chmod(0o755)
            spawn = scripts / "spawn-codex.sh"
            spawn.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            spawn.chmod(0o755)
            (prompts / "triage-external-issue.md").write_text("Issue ${ISSUE_NUMBER}\n", encoding="utf-8")

            env = os.environ.copy()
            env.pop("CODEX_REFACTOR_LOOP_SKILL_ROOT", None)
            env["CODEX_REFACTOR_LOOP_SKILL_ROOT_PRINT"] = "1"
            result = subprocess.run(
                ["bash", str(triage)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, f"{installed.resolve()}\n")
            self.assertEqual(result.stderr, "")
            self.assertFalse((installed / ".refactor-loop").exists())

    def test_invalid_specific_skill_root_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            invalid_skill = root / "invalid-skill"
            fakebin = root / "bin"
            repo.mkdir()
            invalid_skill.mkdir()
            fakebin.mkdir()
            state_file = repo / ".refactor-loop" / "triage-monitor-state.json"
            gh = fakebin / "gh"
            gh.write_text("#!/usr/bin/env bash\nprintf '42 alice\\n'\n", encoding="utf-8")
            gh.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fakebin}{os.pathsep}{env['PATH']}",
                    "REPO_ROOT": str(repo),
                    "CODEX_REFACTOR_LOOP_SKILL_ROOT": str(invalid_skill),
                    "GH_REPO_SLUG": "owner/repo",
                    "TRIAGE_MONITOR_ONCE": "1",
                    "PYTHONPATH": str(SKILL_ROOT / "scripts"),
                }
            )
            triage = subprocess.run(
                ["bash", str(SKILL_ROOT / "scripts" / "triage-monitor.sh")],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(triage.returncode, 0)
            self.assertIn("invalid codex-refactor-loop skill root", triage.stderr)
            self.assertFalse(state_file.exists())

            dev_sync = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import dev_sync_daemon",
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(dev_sync.returncode, 0)
            self.assertIn("invalid codex-refactor-loop skill root", dev_sync.stderr)
            self.assertNotIn(".claude/skills/codex-refactor-loop", dev_sync.stderr + dev_sync.stdout)

    def test_no_shared_locator_or_generic_env_contract(self) -> None:
        self.assertFalse((SKILL_ROOT / "scripts" / "skill_root.py").exists())
        self.assertFalse((SKILL_ROOT / "scripts" / "skill-root.sh").exists())
        self.assertNotIn("CODEX_REFACTOR_LOOP_SKILL_ROOT", self.read_rel("skills/codex-refactor-loop/host.env.example"))

        runtime_texts = {
            "dev_sync_daemon.py": self.read_rel("skills/codex-refactor-loop/scripts/dev_sync_daemon.py"),
            "triage-monitor.sh": self.read_rel("skills/codex-refactor-loop/scripts/triage-monitor.sh"),
        }
        for name, text in runtime_texts.items():
            with self.subTest(runtime=name):
                self.assertNotIn("import skill_root", text)
                self.assertNotIn("source skill-root.sh", text)
                self.assertNotIn("${SKILL_ROOT", text)
                self.assertNotIn("$SKILL_ROOT", text)
                self.assertNotIn('os.environ.get("SKILL_ROOT"', text)

    def test_active_skill_launch_dispatch_docs_are_skill_relative(self) -> None:
        checked = [
            SKILL_ROOT / "SKILL.md",
            SKILL_ROOT / "REFERENCE.md",
            SKILL_ROOT / "scripts" / "dev_sync_daemon.py",
            SKILL_ROOT / "scripts" / "triage-monitor.sh",
        ]
        for path in checked:
            rel = path.relative_to(REPO_ROOT).as_posix()
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=rel):
                self.assertNotIn(".claude/skills/codex-refactor-loop/scripts/", text)
                self.assertNotIn(".claude/skills/codex-refactor-loop/prompts/", text)

        skill_text = self.read_rel("skills/codex-refactor-loop/SKILL.md")
        self.assertIn("## Skill Root Contract", skill_text)
        self.assertIn("`<skill-root>` means the installed `skills/codex-refactor-loop` directory", skill_text)
        self.assertIn("Runtime scripts self-locate", skill_text)
        self.assertIn("`CODEX_REFACTOR_LOOP_SKILL_ROOT` is optional", skill_text)
        self.assertIn("<skill-root>/scripts/peek.sh", skill_text)
        self.assertIn("<skill-root>/scripts/spawn-codex.sh", skill_text)


class AuditDerivedHygieneExceptionTests(unittest.TestCase):
    """Source regressions for the PR #48 audit-derived hygiene exception."""

    def clause_text(self) -> str:
        lines = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8").splitlines()
        return "\n".join(lines[38:45])

    def test_hygiene_exception_clause_requires_host_agnostic(self) -> None:
        self.assertIn("host-agnostic", self.clause_text())

    def test_hygiene_exception_clause_requires_source_regression(self) -> None:
        clause = self.clause_text()
        self.assertTrue("源回归" in clause or "source-regression test" in clause)

    def test_hygiene_exception_clause_requires_per_item_marking(self) -> None:
        clause = self.clause_text()
        self.assertIn("FIX_REPORT 逐项标", clause)
        self.assertIn("✓", clause)
        self.assertIn("⏭", clause)
        self.assertIn("❌", clause)

    def test_hygiene_exception_clause_requires_refactor_self_doc(self) -> None:
        self.assertIn("Refactor (iterN/cluster): Old pattern: ... New principle: ...", self.clause_text())


# Refactor (iter3/skill-contract-test-suite):
#   Old pattern: skill contract regressions were documented in prompts/SKILL text but not enforced by the host TEST_CMD.
#   New principle: a contiguous source-regression suite makes those contracts fail under the dogfood TEST_CMD without adding a new runner or scanner abstraction.
class SkillContractSourceRegressionTests(unittest.TestCase):
    """Issue #16 consensus skill contract source-regression suite.

    Keep this contiguous in the sole direct test file until the split threshold:
    second real test file, this class >250 LOC, whole file >750 LOC, or scanner
    helpers needed by multiple independent classes/files.
    """

    def read_rel(self, rel: str) -> str:
        return (REPO_ROOT / rel).read_text(encoding="utf-8")

    def rel_paths(self, *patterns: str) -> list[Path]:
        return [path for pattern in patterns for path in sorted(REPO_ROOT.glob(pattern))]

    def assert_absent(self, needle: str, paths: list[Path], allowlist: tuple[str, ...] = ()) -> None:
        for path in paths:
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in allowlist:
                continue
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=rel, needle=needle):
                self.assertNotIn(needle, text)

    def test_active_prompt_post_rules_locators_are_skill_relative(self) -> None:
        prompt_names = (
            "reviewer-quality.md",
            "solver-structural.md",
            "design-issue-reply.md",
            "solver-delete.md",
            "solver-minimal.md",
            "review-fix.md",
            "reviewer-architect.md",
            "reviewer-tests.md",
            "meta-judge.md",
            "_github-post-rules.md",
        )
        for name in prompt_names:
            path = SKILL_ROOT / "prompts" / name
            text = path.read_text(encoding="utf-8")
            with self.subTest(prompt=name):
                self.assertNotIn(".claude/skills/codex-refactor-loop/prompts/_github-post-rules.md", text)
                self.assertNotIn(".claude/skills/codex-refactor-loop/scripts/comment-monitor.sh", text)
        for name in prompt_names[:-1]:
            with self.subTest(prompt=name, expected="post-rules"):
                self.assertIn("本 skill 的 `prompts/_github-post-rules.md`", (SKILL_ROOT / "prompts" / name).read_text(encoding="utf-8"))
        self.assertIn("本 skill 的 `scripts/comment-monitor.sh`", self.read_rel("skills/codex-refactor-loop/prompts/_github-post-rules.md"))

    def test_spawn_with_banner_cli_hard_fails_as_tombstone(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "scripts" / "spawn_with_banner.py")],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            "FATAL: spawn_with_banner.py is deprecated; use post_banner.py + "
            "harness-tracked spawn-codex.sh\n",
        )

    def test_spawned_prompts_and_banner_builders_keep_final_independent_sentinel(self) -> None:
        prompt_paths = [p for p in sorted((SKILL_ROOT / "prompts").glob("*.md")) if p.name != "_github-post-rules.md"]
        for path in prompt_paths:
            text = path.read_text(encoding="utf-8")
            with self.subTest(prompt=path.name):
                self.assertIn("末尾独立一行", text)
                self.assertIn("⟦AI:AUTO-LOOP⟧", text)

        for rel in ("skills/codex-refactor-loop/scripts/post_banner.py", "skills/codex-refactor-loop/scripts/comment-monitor.sh"):
            text = self.read_rel(rel)
            with self.subTest(builder=rel):
                self.assertRegex(text, r"\n⟦AI:AUTO-LOOP⟧\n")
                self.assertIn("--body-file", text)

    def test_github_repo_contract_uses_slug_not_bare_owner_repo_api_paths(self) -> None:
        checked = self.rel_paths(
            "skills/codex-refactor-loop/SKILL.md", "skills/codex-refactor-loop/host.env.example",
            "skills/codex-refactor-loop/prompts/*.md", "skills/codex-refactor-loop/scripts/*.sh",
            "skills/codex-refactor-loop/scripts/*.py",
        )
        checked = [path for path in checked if path.name != Path(__file__).name]

        self.assert_absent("repos/$GH_OWNER/$GH_REPO", checked)
        host_env = self.read_rel("skills/codex-refactor-loop/host.env.example")
        self.assertIn('export GH_REPO_SLUG="your-org/your-repo"', host_env)
        self.assertNotIn("export GH_REPO=", host_env)
        for rel in ("skills/codex-refactor-loop/scripts/controller_lib.sh", "skills/codex-refactor-loop/scripts/peek.sh", "skills/codex-refactor-loop/scripts/triage-monitor.sh"):
            with self.subTest(script=rel):
                self.assertIn('gh_repo_args=(--repo "$GH_REPO_SLUG")', self.read_rel(rel))

    def test_optional_ci_guards_are_conditioned_on_non_empty_value(self) -> None:
        checked = self.rel_paths("skills/codex-refactor-loop/SKILL.md", "skills/codex-refactor-loop/prompts/*.md", "skills/codex-refactor-loop/scripts/*.sh")
        for path in checked:
            text = path.read_text(encoding="utf-8")
            rel = path.relative_to(REPO_ROOT).as_posix()
            with self.subTest(path=rel):
                self.assertNotRegex(text, r"bash\s+\$REPO_ROOT/\$CI_GUARDS")
                self.assertNotRegex(text, r"bash\s+\$CI_GUARDS")
                self.assertNotIn("$CI_GUARDS &&", text)

        contract_text = "\n".join(
            self.read_rel(rel)
            for rel in ("skills/codex-refactor-loop/SKILL.md", "skills/codex-refactor-loop/prompts/verify.md", "skills/codex-refactor-loop/scripts/controller_lib.sh")
        )
        self.assertGreaterEqual(contract_text.count('[ -n "${CI_GUARDS:-}" ]'), 3)
        self.assertIn("guards skipped: CI_GUARDS unset", contract_text)

    def test_daemon_start_examples_source_host_env_before_exec(self) -> None:
        skill_text = self.read_rel("skills/codex-refactor-loop/SKILL.md")
        reference_text = self.read_rel("skills/codex-refactor-loop/REFERENCE.md")
        host_env_text = self.read_rel("skills/codex-refactor-loop/host.env.example")
        daemon_names = ("concurrency_monitor.py", "codex-progress-reporter.sh", "comment-monitor.sh", "dev_sync_daemon.py", "triage-monitor.sh")

        self.assertIn("bash -c 'source .refactor-loop/host.env && exec", skill_text)
        self.assertIn("[daemon command bodies](REFERENCE.md#daemon-command-bodies)", skill_text)
        self.assertIn("bash -c 'source host.env && exec ...'", host_env_text)
        for daemon in daemon_names:
            with self.subTest(daemon=daemon):
                self.assertIn(daemon, skill_text)
                self.assertIn(daemon, reference_text)
        self.assertIn("禁止** 裸 `nohup python3 <daemon> &`", reference_text)
        self.assertIn("不能用 `env $(grep ... host.env)`", host_env_text)

    def test_label_taxonomy_matches_bootstrap_and_script_usage(self) -> None:
        skill_text = self.read_rel("skills/codex-refactor-loop/SKILL.md")
        reference_text = self.read_rel("skills/codex-refactor-loop/REFERENCE.md")
        controller_lib = self.read_rel("skills/codex-refactor-loop/scripts/controller_lib.sh")
        monitor_text = self.read_rel("skills/codex-refactor-loop/scripts/concurrency_monitor.py")
        expected_phase = ("🔍 phase:design-solving", "✅ phase:consensus-reached", "🛠️ phase:implementing", "🚀 phase:pr-open", "👀 phase:reviewing", "🔧 phase:fixing", "⚙️ phase:ci-running", "🎉 phase:merged", "⏸️ phase:blocked")
        expected_human = tuple(sorted(CANONICAL_HUMAN_LABELS))

        for label in expected_phase + expected_human:
            with self.subTest(label=label):
                self.assertIn(label, skill_text)
                self.assertIn(label, reference_text)
        self.assertIn("[label bootstrap loops](REFERENCE.md#label-bootstrap-loops)", skill_text)
        self.assertIn('gh label create "$l" --color "5319e7"', reference_text)
        for label in expected_human:
            with self.subTest(human_bootstrap=label):
                self.assertIn(f'gh label create "{label}"', reference_text)

        for label in ("🚀 phase:pr-open", "👀 phase:reviewing", "🔧 phase:fixing", "🛠️ phase:implementing"):
            with self.subTest(controller_label=label):
                self.assertIn(label, controller_lib)
        for label in ("🔍 phase:design-solving", "👀 phase:reviewing", "🛠️ phase:implementing"):
            with self.subTest(monitor_label=label):
                self.assertIn(label, monitor_text)

    def test_spawn_with_banner_is_hard_failing_tombstone_not_mainline_surface(self) -> None:
        tombstone = self.read_rel("skills/codex-refactor-loop/scripts/spawn_with_banner.py")

        self.assertIn("Deprecated detached-spawn tombstone", tombstone)
        self.assertIn("return 2", tombstone)
        self.assertNotIn("subprocess.Popen", tombstone)
        self.assertNotIn("start_new_session", tombstone)
        self.assertNotIn("SPAWN_CODEX", tombstone)

        active_docs = "\n".join(self.read_rel(rel) for rel in (
            "skills/codex-refactor-loop/SKILL.md",
            "skills/codex-refactor-loop/REFERENCE.md",
            "skills/codex-refactor-loop/scripts/post_banner.py",
        ))
        self.assertIn("post_banner.py", active_docs)
        self.assertIn("spawn-codex.sh", active_docs)
        self.assertIn("反模式", active_docs)

    def test_phase9_language_policy_allowlist_is_narrow(self) -> None:
        allowlist = {
            "skills/codex-refactor-loop/SKILL.md", "skills/codex-refactor-loop/prompts/audit.md",
            "skills/codex-refactor-loop/prompts/design-issue-body.md", "skills/codex-refactor-loop/prompts/design-issue-reply.md",
            "skills/codex-refactor-loop/prompts/meta-judge.md", "skills/codex-refactor-loop/prompts/solver-delete.md",
            "skills/codex-refactor-loop/prompts/solver-minimal.md", "skills/codex-refactor-loop/prompts/solver-structural.md",
        }
        checked = self.rel_paths("skills/codex-refactor-loop/SKILL.md", "skills/codex-refactor-loop/prompts/*.md")
        patterns = ("Bilingual rule", "双语强制", "## English", "Recommended framing (English)")

        for path in checked:
            rel = path.relative_to(REPO_ROOT).as_posix()
            text = path.read_text(encoding="utf-8")
            for pattern in patterns:
                if pattern not in text:
                    continue
                with self.subTest(path=rel, pattern=pattern):
                    self.assertIn(rel, allowlist)

        skill_text = self.read_rel("skills/codex-refactor-loop/SKILL.md")
        self.assertIn("Source files are English-only; external user-facing artifacts are 中文 by default", skill_text)
        self.assertIn("No mandatory parallel English section", skill_text)

    def test_non_controller_prompts_keep_git_and_lifecycle_boundaries(self) -> None:
        controller_owned = {"_github-post-rules.md", "remote-ci-fix.md", "triage-external-issue.md"}
        forbidden = ("git commit", "git push", "git checkout", "gh pr create", "gh pr merge", "gh issue close")

        for path in sorted((SKILL_ROOT / "prompts").glob("*.md")):
            if path.name in controller_owned:
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            for token in forbidden:
                for line in lines:
                    if token not in line:
                        continue
                    with self.subTest(prompt=path.name, token=token, line=line):
                        self.assertRegex(line, r"禁止|不可调|Do NOT|do not")

    def test_disabled_test_escape_hatches_are_not_recommended(self) -> None:
        checked = self.rel_paths("skills/codex-refactor-loop/prompts/*.md", "skills/codex-refactor-loop/SKILL.md")
        recommendation_patterns = (
            r"(建议|可以|允许|recommend|use).{0,24}`?\[Skip\]`?",
            r"pytest\.mark\.skip",
            r"#\[ignore\]",
            r"(建议|可以|允许|recommend|use).{0,24}Category\",\"Manual",
        )

        for path in checked:
            rel = path.relative_to(REPO_ROOT).as_posix()
            text = path.read_text(encoding="utf-8")
            for pattern in recommendation_patterns:
                with self.subTest(path=rel, pattern=pattern):
                    self.assertIsNone(re.search(pattern, text))

    # Refactor (iter3/skill-host-language-policy): Old: 写死 C#/.NET/proto 默认  New: 6 个 HOST_* 可选空默认,host.env 注入(#20 structural 共识)
    def test_host_language_policy_uses_exact_set_a_without_aliases(self) -> None:
        canonical = {
            "HOST_TEST_FILE_GLOBS",
            "HOST_TEST_NAMING_RULE",
            "HOST_COMMENT_RULE",
            "HOST_CODE_FENCE_LANG",
            "HOST_PROTO_POLICY",
            "HOST_ARCHITECTURE_GREP_CHECKS",
        }
        rejected_aliases = {
            "HOST_TEST_LAYOUT_GLOB",
            "HOST_TEST_LAYOUT_GLOBS",
            "HOST_TEST_FILE_NAMING",
            "HOST_COMMENT_STYLE",
            "HOST_COMMENT_POLICY",
            "HOST_CODE_LANGUAGE",
            "HOST_EXAMPLE_FENCE",
            "HOST_TEST_DISABLE_POLICY",
            "HOST_DEPENDENCY_MANIFEST_GLOBS",
        }
        checked = self.rel_paths(
            "skills/codex-refactor-loop/SKILL.md",
            "skills/codex-refactor-loop/host.env.example",
            "skills/codex-refactor-loop/prompts/*.md",
        )
        host_env = self.read_rel("skills/codex-refactor-loop/host.env.example")
        skill_text = self.read_rel("skills/codex-refactor-loop/SKILL.md")

        self.assertEqual(set(re.findall(r"^export (HOST_[A-Z0-9_]+)=\"\"", host_env, re.MULTILINE)), canonical)
        for name in canonical:
            with self.subTest(canonical=name):
                self.assertIn(f"| `${name}` |", skill_text)

        combined = "\n".join(path.read_text(encoding="utf-8") for path in checked)
        for alias in rejected_aliases:
            with self.subTest(alias=alias):
                self.assertNotIn(alias, combined)

    def test_host_language_policy_replaces_old_prompt_defaults(self) -> None:
        scoped_prompts = {
            "test-add.md": ("HOST_TEST_FILE_GLOBS", "HOST_TEST_NAMING_RULE", "HOST_COMMENT_RULE", "HOST_CODE_FENCE_LANG"),
            "design-issue-body.md": ("HOST_CODE_FENCE_LANG", "HOST_PROTO_POLICY"),
            "implement.md": ("HOST_COMMENT_RULE", "HOST_PROTO_POLICY"),
            "reviewer-architect.md": ("HOST_COMMENT_RULE", "HOST_ARCHITECTURE_GREP_CHECKS", "HOST_PROTO_POLICY"),
            "reviewer-tests.md": ("HOST_TEST_FILE_GLOBS", "HOST_TEST_NAMING_RULE", "HOST_PROTO_POLICY"),
            "verify.md": ("HOST_COMMENT_RULE", "HOST_PROTO_POLICY"),
        }
        forbidden_defaults = (
            "test/**/*.cs",
            "*Tests.cs",
            "<TypeName>Tests.cs",
            "```csharp",
            "C#",
            ".NET",
            "Directory.Packages.props",
            "NuGet",
            "Protobuf",
            "如改 proto，必须本地重生成",
            "if the diff touches `.proto`",
            "Pure DTO / record proto fields exempt",
        )
        host_comment = "Refactor (iter3/skill-host-language-policy): Old: 写死 C#/.NET/proto 默认  New: 6 个 HOST_* 可选空默认,host.env 注入(#20 structural 共识)"

        for prompt_name, required_names in scoped_prompts.items():
            text = (SKILL_ROOT / "prompts" / prompt_name).read_text(encoding="utf-8")
            scan_text = "\n".join(line for line in text.splitlines() if host_comment not in line)
            with self.subTest(prompt=prompt_name, marker="refactor-comment"):
                self.assertIn(host_comment, text)
            for required in required_names:
                with self.subTest(prompt=prompt_name, required=required):
                    self.assertIn(required, text)
            for forbidden in forbidden_defaults:
                with self.subTest(prompt=prompt_name, forbidden=forbidden):
                    self.assertNotIn(forbidden, scan_text)

        prompt_text = "\n".join(
            line
            for name in scoped_prompts
            for line in (SKILL_ROOT / "prompts" / name).read_text(encoding="utf-8").splitlines()
            if host_comment not in line
        )
        self.assertIsNone(re.search(r"(?<!HOST_)\bproto\b", prompt_text))
        self.assertIsNone(re.search(r"\.proto\b", prompt_text))


if __name__ == "__main__":
    unittest.main()
