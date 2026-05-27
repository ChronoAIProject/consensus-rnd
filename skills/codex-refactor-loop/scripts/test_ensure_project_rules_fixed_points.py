#!/usr/bin/env python3
"""Behavior tests for ensure_project_rules_fixed_points.py."""

from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


class ScriptHygieneBehaviorTests(unittest.TestCase):
    def clean_github_env(self, updates: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        for key in ("GH_REPO_SLUG", "GH_REPO", "GH_OWNER", "GH_REPO_NAME"):
            env.pop(key, None)
        if updates:
            env.update(updates)
        return env

    def run_repo_slug_function(self, command: str, env_updates: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        script = f'source "{SKILL_ROOT / "scripts" / "repo_slug.sh"}"; {command}'
        return subprocess.run(
            ["bash", "-c", script],
            env=self.clean_github_env(env_updates),
            capture_output=True,
            text=True,
            check=False,
        )

    def run_progress_function(
        self,
        function_name: str,
        command: str,
        *,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        progress = SKILL_ROOT / "scripts" / "codex-progress-reporter.sh"
        lines = progress.read_text(encoding="utf-8").splitlines()
        start = lines.index(f"{function_name}() {{")
        end = next(index for index in range(start + 1, len(lines)) if lines[index] == "}")
        with tempfile.TemporaryDirectory() as tmp:
            func_file = Path(tmp) / f"{function_name}.sh"
            func_file.write_text("\n".join(lines[start : end + 1]) + "\n", encoding="utf-8")
            script = f'source "{func_file}"; {command}'
            return subprocess.run(
                ["bash", "-c", script],
                env=env,
                input=input_text,
                capture_output=True,
                text=True,
                check=False,
            )

    def run_progress_harness(
        self,
        command: str,
        *,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        progress = SKILL_ROOT / "scripts" / "codex-progress-reporter.sh"
        lines = progress.read_text(encoding="utf-8").splitlines()
        start = next(index for index, line in enumerate(lines) if line.startswith("log_msg()"))
        end = lines.index("# 主 loop")
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "progress_harness.sh"
            harness.write_text("\n".join(lines[start:end]) + "\n", encoding="utf-8")
            script = f'source "{harness}"; {command}'
            return subprocess.run(
                ["bash", "-c", script],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_resolve_github_repo_slug_env_branches(self) -> None:
        cases = [
            ("explicit_slug", {"GH_REPO_SLUG": "owner/repo"}, 0, "owner/repo\n", ""),
            ("legacy_repo_slug", {"GH_REPO": "legacy/repo"}, 0, "legacy/repo\n", ""),
            ("owner_and_name", {"GH_OWNER": "octo", "GH_REPO_NAME": "project"}, 0, "octo/project\n", ""),
            ("missing_required", {}, 2, "", "FATAL: GH_REPO_SLUG is unset and gh repo view failed"),
            ("invalid_bare_slug", {"GH_REPO_SLUG": "repo-only"}, 2, "", "FATAL: GH_REPO_SLUG must be OWNER/REPO"),
        ]

        for name, env_updates, expected_code, expected_stdout, expected_stderr in cases:
            with self.subTest(case=name):
                result = self.run_repo_slug_function("resolve_github_repo_slug 0 1", env_updates)

                self.assertEqual(result.returncode, expected_code, result.stderr)
                self.assertEqual(result.stdout, expected_stdout)
                self.assertIn(expected_stderr, result.stderr)

    def test_set_gh_repo_args_mutates_exports_and_populates_array(self) -> None:
        command = (
            "set_gh_repo_args 0 1 || exit $?; "
            "printf 'slug=%s\\n' \"$GH_REPO_SLUG\"; "
            "printf 'argc=%s\\n' \"${#gh_repo_args[@]}\"; "
            "printf 'arg=%s\\n' \"${gh_repo_args[@]}\"; "
            "bash -c 'printf \"child=%s\\n\" \"$GH_REPO_SLUG\"'"
        )

        result = self.run_repo_slug_function(command, {"GH_REPO_SLUG": "owner/repo"})

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["slug=owner/repo", "argc=2", "arg=--repo", "arg=owner/repo", "child=owner/repo"])

    def test_set_gh_repo_args_empty_invalid_and_gh_view_fallback(self) -> None:
        empty = self.run_repo_slug_function(
            "set_gh_repo_args 0 0 || exit $?; "
            "printf 'slug=<%s>\\n' \"$GH_REPO_SLUG\"; "
            "printf 'argc=%s\\n' \"${#gh_repo_args[@]}\"; "
            "bash -c 'printf \"child=<%s>\\n\" \"$GH_REPO_SLUG\"'",
        )
        self.assertEqual(empty.returncode, 0, empty.stderr)
        self.assertEqual(empty.stdout.splitlines(), ["slug=<>", "argc=0", "child=<>"])

        invalid = self.run_repo_slug_function("set_gh_repo_args 0 1", {"GH_REPO_SLUG": "repo-only"})
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stdout, "")
        self.assertIn("FATAL: GH_REPO_SLUG must be OWNER/REPO", invalid.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            fakebin = Path(tmp)
            gh = fakebin / "gh"
            gh.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ \"$1 $2 $3\" == \"repo view --json\" ]]; then\n"
                "  printf 'fallback/slug\\n'\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)
            env = self.clean_github_env()
            env["PATH"] = f"{fakebin}{os.pathsep}{env.get('PATH', '')}"
            fallback = subprocess.run(
                [
                    "bash",
                    "-c",
                    f'source "{SKILL_ROOT / "scripts" / "repo_slug.sh"}"; '
                    "set_gh_repo_args 1 1 || exit $?; "
                    "printf 'slug=%s\\n' \"$GH_REPO_SLUG\"; "
                    "printf 'argc=%s\\n' \"${#gh_repo_args[@]}\"; "
                    "printf 'arg=%s\\n' \"${gh_repo_args[@]}\"",
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(fallback.returncode, 0, fallback.stderr)
        self.assertEqual(fallback.stdout.splitlines(), ["slug=fallback/slug", "argc=2", "arg=--repo", "arg=fallback/slug"])

    def test_python_github_repo_slug_env_branches(self) -> None:
        from repo_config import github_repo_slug

        cases = [
            ("explicit_slug", {"GH_REPO_SLUG": "owner/repo"}, "owner/repo"),
            ("legacy_repo_slug", {"GH_REPO": "legacy/repo"}, "legacy/repo"),
            ("owner_and_name", {"GH_OWNER": "octo", "GH_REPO_NAME": "project"}, "octo/project"),
            ("owner_and_bare_repo_fallback", {"GH_OWNER": "octo", "GH_REPO": "project"}, "octo/project"),
            ("missing", {}, None),
        ]

        for name, env_updates, expected in cases:
            with self.subTest(case=name):
                with patch.dict(os.environ, env_updates, clear=True):
                    self.assertEqual(github_repo_slug(), expected)

    def test_progress_reporter_exit_status_reads_only_terminal_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures = {
                "in_flight": "start\nEXIT=0\nstill\nrunning\nno\nterminal\nmarker\n",
                "exit_ok": "start\nwork\nEXIT=0\nDONE_AT=now\n",
                "exit_failed": "start\nwork\nEXIT=17\nDONE_AT=now\n",
            }
            expected = {
                "in_flight": "in_flight\n",
                "exit_ok": "exit_ok\n",
                "exit_failed": "exit_failed\n",
            }

            for name, text in fixtures.items():
                path = root / f"{name}.log"
                path.write_text(text, encoding="utf-8")
                with self.subTest(case=name):
                    result = self.run_progress_function("exit_status", f'exit_status "{path}"')

                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, expected[name])

    def test_progress_reporter_exit_failed_keeps_comment_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / ".refactor-loop"
            log_dir = state_dir / "logs"
            fakebin = root / "fakebin"
            state_dir.mkdir()
            log_dir.mkdir()
            fakebin.mkdir()
            (state_dir / "codex-progress-state.json").write_text("{}\n", encoding="utf-8")
            log_path = log_dir / "fix-pr47-round2.log"
            log_path.write_text(
                "start\n"
                "working\n"
                "important failure context\n"
                "EXIT=17\n",
                encoding="utf-8",
            )
            calls_path = root / "gh-calls.log"
            body_path = root / "created-body.md"
            gh = fakebin / "gh"
            gh.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$*\" >> \"$GH_CALLS\"\n"
                "if [[ \"$1 $2\" == \"pr view\" ]]; then\n"
                "  exit 0\n"
                "fi\n"
                "if [[ \"$1 $2\" == \"pr comment\" ]]; then\n"
                "  body_file=''\n"
                "  while [[ $# -gt 0 ]]; do\n"
                "    if [[ \"$1\" == \"--body-file\" ]]; then\n"
                "      body_file=\"$2\"\n"
                "      break\n"
                "    fi\n"
                "    shift\n"
                "  done\n"
                "  cp \"$body_file\" \"$GH_BODY_CAPTURE\"\n"
                "  printf 'https://github.com/owner/repo/pull/47#issuecomment-24680\\n'\n"
                "  exit 0\n"
                "fi\n"
                "if [[ \"$1 $2 $3\" == \"api -X DELETE\" ]]; then\n"
                "  exit 0\n"
                "fi\n"
                "if [[ \"$1 $2 $3\" == \"api -X PATCH\" ]]; then\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            gh.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "GH_BODY_CAPTURE": str(body_path),
                    "GH_CALLS": str(calls_path),
                    "PATH": f"{fakebin}{os.pathsep}{env.get('PATH', '')}",
                    "REPO": "owner/repo",
                    "REPO_ROOT": str(root),
                    "STATE_DIR": str(state_dir),
                    "STATE_FILE": str(state_dir / "codex-progress-state.json"),
                    "LOG_DIR": str(log_dir),
                    "PROMPTS_DIR": str(state_dir / "prompts"),
                }
            )

            result = self.run_progress_harness(
                f'post_or_update "fix-pr47-round2" "{log_path}"; post_or_update "fix-pr47-round2" "{log_path}"',
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            calls = calls_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(sum("pr comment 47" in call for call in calls), 1, calls)
            self.assertFalse(any("api -X DELETE" in call for call in calls), calls)
            self.assertFalse(any("api -X PATCH" in call for call in calls), calls)

            body = body_path.read_text(encoding="utf-8")
            self.assertIn("失败", body)
            self.assertIn("controller progress reporter", body)
            self.assertIn("⟦AI:AUTO-LOOP⟧", body)

            state = json.loads((state_dir / "codex-progress-state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["fix-pr47-round2"]["finished"], "failed")
            self.assertEqual(state["fix-pr47-round2"]["comment_id"], 24680)

    def test_progress_reporter_hash_body_uses_md5_and_md5sum_fallbacks(self) -> None:
        body = "stable body\nwith unicode sentinel: ⟦AI:AUTO-LOOP⟧\n"
        expected = hashlib.md5(body.encode("utf-8")).hexdigest()

        with tempfile.TemporaryDirectory() as tmp:
            fakebin = Path(tmp) / "md5"
            fakebin.mkdir()
            md5 = fakebin / "md5"
            md5.write_text(
                f"#!/usr/bin/env bash\n{sys.executable} -c 'import hashlib, sys; print(hashlib.md5(sys.stdin.buffer.read()).hexdigest())'\n",
                encoding="utf-8",
            )
            md5sum = fakebin / "md5sum"
            md5sum.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
            md5.chmod(0o755)
            md5sum.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{fakebin}{os.pathsep}{env.get('PATH', '')}"

            result = self.run_progress_function("hash_body", "hash_body", input_text=body, env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), expected)

        with tempfile.TemporaryDirectory() as tmp:
            fakebin = Path(tmp) / "md5sum"
            fakebin.mkdir()
            md5sum = fakebin / "md5sum"
            md5sum.write_text(
                f"#!/usr/bin/env bash\n{sys.executable} -c 'import hashlib, sys; print(hashlib.md5(sys.stdin.buffer.read()).hexdigest() + \"  -\")'\n",
                encoding="utf-8",
            )
            md5sum.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{fakebin}{os.pathsep}/usr/bin{os.pathsep}/bin"

            result = self.run_progress_function("hash_body", "hash_body", input_text=body, env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), expected)


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
            "meta-reflector-stalled.md",
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

    # Refactor (iter213/cluster-213-006-delete-solver-defer-escape):
    #   Old pattern: delete solver forbids defer, then defines Deferrable and asks for a tracking issue creation suggestion(prompt 内部矛盾,且 gh issue create 后被禁)
    #   New principle: delete solver 单 terminal vocabulary:delete/collapse/abstain/escalate;无 deferred side-channel、无 issue-create 命令建议;'not now' map 到 abstain/false-positive,lifecycle 决策归 controller/maintainer。
    def test_delete_solver_has_no_defer_side_channel_or_issue_create_suggestion(self) -> None:
        solver_delete = (SKILL_ROOT / "prompts" / "solver-delete.md").read_text(encoding="utf-8")
        scan_text = "\n".join(
            line
            for line in solver_delete.splitlines()
            if "Refactor (iter213/cluster-213-006-delete-solver-defer-escape)" not in line
            and "Old pattern:" not in line
            and "New principle:" not in line
        )

        forbidden_tokens = (
            "Deferrable",
            "Tracking issue (if defer)",
            "tracking issue creation suggestion",
            "gh issue create command suggestion",
            "moving cluster to \"deferred\"",
        )
        for token in forbidden_tokens:
            with self.subTest(token=token):
                self.assertNotIn(token, scan_text)
        self.assertIn("Either delete/collapse now", scan_text)
        self.assertIn("Lifecycle decisions stay with controller/maintainer.", scan_text)


class RootMarkdownClosureSourceRegressionTests(unittest.TestCase):
    # Refactor (iter214/cluster-214-003-root-md-surface-leak):
    #   Old pattern: Root contains durable Markdown files outside the explicit root .md allowlist(CONTRIBUTING.md / IMPROVEMENT-BACKLOG.md 违反 CLAUDE.md 根目录 .md 收口子句)。
    #   New principle: Root Markdown remains limited to documented allowlist;extra durable docs move under their owning skill/docs surface or are deleted。
    def test_root_durable_document_surface_matches_claude_md_allowlist(self) -> None:
        allowed_root_documents = {"CLAUDE.md", "README.md", "AGENTS.md", "LICENSE", "GEMINI.md", "CHANGELOG.md"}
        allowed_root_markdown = {name for name in allowed_root_documents if name.endswith(".md")}

        root_markdown = {path.name for path in REPO_ROOT.glob("*.md")}
        root_durable_docs = {path.name for path in REPO_ROOT.iterdir() if path.name in allowed_root_documents}

        self.assertEqual(root_markdown - allowed_root_markdown, set())
        self.assertTrue(root_durable_docs <= allowed_root_documents)


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
    # Refactor (iter4/concurrency-auto-topup):
    #   Old pattern: monitor 只 alert,actual<floor 等 LLM controller 下次 wakeup 才派
    #   New principle: monitor 自动从 dispatch-queue 消费,与 daemon-first 哲学 align。
    def test_concurrency_monitor_auto_topup_is_queue_scoped(self) -> None:
        monitor_text = (SKILL_ROOT / "scripts" / "concurrency_monitor.py").read_text(encoding="utf-8")

        self.assertIn("no-gap-violation", monitor_text)
        self.assertIn("expected > 0 and actual == 0", monitor_text)
        self.assertIn("dispatch-queue", monitor_text)
        self.assertIn("DISPATCH_FIRED:", monitor_text)
        self.assertIn("CONCURRENCY_LOW:actual=", monitor_text)
        self.assertIn('os.environ.get("CODEX_FLOOR"', monitor_text)
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

    def test_skill_documents_monitor_queue_topup_and_controller_step_1_5(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        reference_text = (SKILL_ROOT / "REFERENCE.md").read_text(encoding="utf-8")

        self.assertIn("dispatch-queue 非空时自动派发", skill_text)
        self.assertIn("低于预期数就继续派发", skill_text)
        self.assertIn("controller 每次 wakeup 的 step 1.5", skill_text)
        self.assertIn("必须在任何 `ScheduleWakeup` 之前执行", skill_text)
        self.assertIn("FLOOR=$(( ${CODEX_FLOOR:-5} < 2 ? 2 : ${CODEX_FLOOR:-5} ))", skill_text)
        self.assertNotIn("low 规则:`actual < expected/2`", skill_text)
        self.assertNotIn("codex-floor-deficit", skill_text)
        self.assertNotIn("ACTIVE <= 2", skill_text)
        self.assertIn("[concurrency floor details](REFERENCE.md#concurrency-floor-details)", skill_text)
        self.assertIn("Dispatch queue protocol", reference_text)
        self.assertIn("DISPATCH_FIRED:<task-id>:<priority>:<reason>", reference_text)
        self.assertIn("CONCURRENCY_LOW:actual=N expected=M queue=0", reference_text)
        self.assertEqual(reference_text.count("**判定脚本**(controller wakeup step 1.5):"), 1)

    def test_skill_named_exception_documents_concurrency_monitor_auto_topup(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

        heading = "## Named runtime exception — concurrency_monitor auto-topup(per #57)"
        self.assertIn(heading, skill_text)
        start = skill_text.index(heading)
        end = skill_text.index("## Spawn Contract", start)
        paragraph = skill_text[start:end]

        for required in (
            "narrow allowlist",
            "No lifecycle authority",
            "top_up_from_dispatch_queue",
            "DISPATCH_FIRED",
            "CONCURRENCY_LOW",
            "maintainer-directive equivalence",
        ):
            with self.subTest(required=required):
                self.assertIn(required, paragraph)


class WorktreeLocationConventionTests(unittest.TestCase):
    # Refactor (iter4/skill-worktree-inside-repo): Old pattern: sibling `<repo>-wt-<name>/`. New principle: inside `<repo>/.worktrees/<name>/` + gitignored.
    def test_safe_worktree_creates_inside_repo(self) -> None:
        text = (SKILL_ROOT / "scripts" / "controller_lib.sh").read_text(encoding="utf-8")

        self.assertIn('WT_PATH="${REPO_ROOT}/.worktrees/iter${iter}-${cluster}"', text)
        self.assertIn('mkdir -p "${REPO_ROOT}/.worktrees"', text)
        self.assertIn("Refactor (iter4/skill-worktree-inside-repo)", text)

    def test_gitignore_has_worktrees_entry(self) -> None:
        text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("/.worktrees/", text.splitlines())

    def test_dev_sync_daemon_uses_inside_path(self) -> None:
        text = (SKILL_ROOT / "scripts" / "dev_sync_daemon.py").read_text(encoding="utf-8")

        self.assertIn("独立 worktree:$REPO_ROOT/.worktrees/dev-sync", text)
        self.assertIn('MAIN_REPO / ".worktrees" / "dev-sync"', text)
        self.assertIn("Refactor (iter4/skill-worktree-inside-repo)", text)
        self.assertNotIn('f"{MAIN_REPO}-wt-dev-sync"', text)

    def test_peek_sh_skip_list_covers_both_dev_sync_layouts(self):
        """#50 共识:peek.sh case 必须 skip 历史 sibling 和新 inside dev-sync。
        防 PR #50 后人改 peek.sh 漏掉一种导致 dev-sync 被误算成 stale worktree 清理。"""
        src = (REPO_ROOT / "skills/codex-refactor-loop/scripts/peek.sh").read_text()
        # 历史 sibling 兼容(已 merge 但未清理的旧 worktree)
        self.assertIn('"$(basename "$REPO_ROOT")-wt-dev-sync"', src,
            "peek.sh skip list 漏 sibling dev-sync(PR #50 前的历史路径)")
        # 新 inside 路径
        self.assertIn('"dev-sync"', src,
            "peek.sh skip list 漏 inside dev-sync(PR #50 后的路径)")

    def test_no_sibling_worktree_pattern_in_active_scripts(self) -> None:
        allowed = {
            "scripts/peek.sh",
        }
        offenders: list[str] = []
        for path in sorted((SKILL_ROOT / "scripts").glob("*")):
            if not path.is_file():
                continue
            if path.name.startswith("test_"):
                continue
            rel = path.relative_to(SKILL_ROOT).as_posix()
            if rel in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            if "${REPO_ROOT}-wt-" in text:
                offenders.append(rel)

        self.assertEqual(offenders, [])

    def test_reference_md_documents_inside_convention(self) -> None:
        text = (SKILL_ROOT / "REFERENCE.md").read_text(encoding="utf-8")

        self.assertIn("`$REPO_ROOT/.worktrees/<name>/`", text)
        self.assertIn("`/.worktrees/`", text)
        self.assertIn("所有 daemon/codex/implement worktree 都在 `$REPO_ROOT/.worktrees/` 内", text)


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


class HumanLabelSemanticsTests(unittest.TestCase):
    # Refactor (iter4/human-label-semantics-guard): Old pattern: label 当 architect reject workaround. New principle: 严语义 + reflector self-check + controller helper guard + source-regression test.
    def read_rel(self, rel: str) -> str:
        return (REPO_ROOT / rel).read_text(encoding="utf-8")

    def test_skill_md_has_strict_human_label_semantics_section(self) -> None:
        skill = self.read_rel("skills/codex-refactor-loop/SKILL.md")

        self.assertIn("## `👤 human:需-maintainer-决策` 严格语义(强制)", skill)
        for token in (
            "Apply only when",
            "DO NOT apply when",
            "禁止",
            "architect/quality reviewer",
            "needs Phase 9 artifact",
            "maintainer-directive",
            "绕路工具",
        ):
            with self.subTest(token=token):
                self.assertIn(token, skill)

    def test_reflector_prompts_include_maintainer_directive_self_check(self) -> None:
        reflector_prompts = sorted((SKILL_ROOT / "prompts").glob("meta-reflector*.md"))
        self.assertGreaterEqual(len(reflector_prompts), 1)
        combined_prompts = "\n".join(path.read_text(encoding="utf-8") for path in reflector_prompts)
        controller_lib = self.read_rel("skills/codex-refactor-loop/scripts/controller_lib.sh")
        combined = combined_prompts + "\n" + controller_lib

        for token in (
            "META_RESOLVED:escalate-human",
            "maintainer already authorized",
            ".refactor-loop/runs/maintainer-directives/",
            "META_RESOLVED:re-design:reframe-with-maintainer-directive",
            "apply_human_label_or_skip",
        ):
            with self.subTest(token=token):
                self.assertIn(token, combined)

    def test_meta_reflector_prompt_has_phase9_no_framing_drop_route(self) -> None:
        # Refactor (iter210/reflector-third-escape-route):
        #   Old pattern: stalled Phase 9 with unchanged solver text could only re-design toward a missing directive artifact.
        #   New principle: source-regression keeps phase9-no-framing mapped to drop while escalate-human remains physical intervention only.
        prompt = self.read_rel("skills/codex-refactor-loop/prompts/meta-reflector-stalled.md")

        for token in (
            "Does the Phase 9 evidence show no actionable framing after 3+ unchanged solver rounds?",
            "META_RESOLVED:drop:no-actionable-framing-after-N-rounds",
            "no-actionable-framing",
            "phase9-no-framing",
            "false-positive/wontfix cases and for phase9-no-framing cases",
            "Do not use `drop` to bypass architect/quality rejects",
            "escalate-human 仍是 maintainer physical intervention 唯一出口",
        ):
            with self.subTest(token=token):
                self.assertIn(token, prompt)

        self.assertLess(
            prompt.index("Does the Phase 9 evidence show no actionable framing after 3+ unchanged solver rounds?"),
            prompt.index("If any of answers 1-3 is yes"),
        )
        self.assertIn("If answer 4 is yes, do not emit `META_RESOLVED:escalate-human` or `META_RESOLVED:re-design`.", prompt)

    def test_meta_reflector_phase9_no_framing_evidence_routes_to_drop(self) -> None:
        # Refactor (iter210/reflector-third-escape-route):
        #   Old pattern: behavior for unchanged Phase 9 stall evidence still fell through to re-design/escalate-human.
        #   New principle: representative no-framing evidence maps to drop, not another human/directive loop.
        prompt = self.read_rel("skills/codex-refactor-loop/prompts/meta-reflector-stalled.md")
        evidence = {
            "convergence_round": 4,
            "solver_verdict_texts": [
                "no solvable framing remains; delete is unsafe, minimal is not actionable, structural has no concrete boundary",
                "no solvable framing remains; delete is unsafe, minimal is not actionable, structural has no concrete boundary",
                "no solvable framing remains; delete is unsafe, minimal is not actionable, structural has no concrete boundary",
            ],
            "maintainer_input_since_last_round": False,
            "distinct_solvable_framing": False,
        }

        unchanged_solver_text = len(set(evidence["solver_verdict_texts"])) == 1
        prompt_authorizes_no_framing_drop = all(
            token in prompt
            for token in (
                "Does the Phase 9 evidence show no actionable framing after 3+ unchanged solver rounds?",
                "there is no maintainer input",
                "no distinct solvable framing remains",
                "If answer 4 is yes, do not emit `META_RESOLVED:escalate-human` or `META_RESOLVED:re-design`.",
            )
        )

        if (
            prompt_authorizes_no_framing_drop
            and evidence["convergence_round"] >= 3
            and unchanged_solver_text
            and not evidence["maintainer_input_since_last_round"]
            and not evidence["distinct_solvable_framing"]
        ):
            route = f"META_RESOLVED:drop:no-actionable-framing-after-{evidence['convergence_round']}-rounds"
        else:
            route = "META_RESOLVED:re-design:reframe-with-maintainer-directive"

        self.assertEqual(route, "META_RESOLVED:drop:no-actionable-framing-after-4-rounds")
        self.assertNotIn("META_RESOLVED:re-design", route)
        self.assertNotIn("META_RESOLVED:escalate-human", route)

    def test_no_active_script_unconditionally_applies_human_label(self) -> None:
        scripts = [
            path
            for pattern in ("*.sh", "*.py")
            for path in (SKILL_ROOT / "scripts").glob(pattern)
            if not path.name.startswith("test_")
        ]
        bare_add_label = re.compile(r"gh\s+pr\s+edit\b.*--add-label\s+[\"'][^\"']*👤 human:需-maintainer-决策")
        offenders: list[str] = []

        for path in scripts:
            in_helper = False
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if path.name == "controller_lib.sh" and line.startswith("apply_human_label_or_skip()"):
                    in_helper = True
                if in_helper and line == "}":
                    in_helper = False
                    continue
                if bare_add_label.search(line) and not in_helper:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_no}:{line.strip()}")

        self.assertEqual(offenders, [])

    def test_maintainer_directive_artifact_pattern_documented(self) -> None:
        reference = self.read_rel("skills/codex-refactor-loop/REFERENCE.md")

        for token in (
            ".refactor-loop/runs/maintainer-directives/<date>-<topic>.md",
            "Phase 9 artifact",
            "architect",
            "maintainer-directive artifact",
            "replacement path",
        ):
            with self.subTest(token=token):
                self.assertIn(token, reference)

    def test_session_2026_05_26_misuse_recorded(self) -> None:
        reference = self.read_rel("skills/codex-refactor-loop/REFERENCE.md")

        for token in (
            "Historical anti-pattern:`👤 human:需-maintainer-决策` 误用 (2026-05-26)",
            "PR #47/#48/#50/#52",
            "architect codex",
            "option C",
            "issue #54",
            "label 严语义 + helper 守护",
        ):
            with self.subTest(token=token):
                self.assertIn(token, reference)


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
            worktree = repo / ".worktrees" / "dev-sync"
            sibling = root / "sibling"
            repo.mkdir()
            worktree.mkdir(parents=True)
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
            (root / "repo_slug.sh").write_text((SKILL_ROOT / "scripts" / "repo_slug.sh").read_text(encoding="utf-8"), encoding="utf-8")
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
            wt = repo / ".worktrees" / "dev-sync"
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
            wt.parent.mkdir(parents=True)
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
            (root / "repo_slug.sh").write_text((SKILL_ROOT / "scripts" / "repo_slug.sh").read_text(encoding="utf-8"), encoding="utf-8")
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


class AutonomousReleaseGateContractTests(unittest.TestCase):
    """#56 r2 consensus autonomous release gate contract."""

    def read_skill(self) -> str:
        return (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    def read_gate_source(self) -> str:
        return (SKILL_ROOT / "scripts" / "auto_release_gate.py").read_text(encoding="utf-8")

    def autonomous_release_gate_section(self) -> str:
        text = self.read_skill()
        match = re.search(
            r"## Named runtime exception — autonomous release gate\(per #56\)(.*?)\n## Wakeup Skeleton",
            text,
            flags=re.S,
        )
        self.assertIsNotNone(match)
        assert match is not None
        return match.group(1)

    def test_skill_documents_autonomous_release_gate_title(self) -> None:
        self.assertIn("## Named runtime exception — autonomous release gate(per #56)", self.read_skill())

    def test_skill_documents_opt_in_gate_literal(self) -> None:
        text = self.read_skill()
        self.assertIn("$RELEASE_AUTO_ENABLE=true", text)
        self.assertIn("opt-in", text)

    def test_skill_rejects_mandatory_per_release_authorization(self) -> None:
        text = self.read_skill()
        forbidden_patterns = (
            r"mandatory per-release\s+maintainer emoji react",
            r"mandatory per-release\s+approval issue",
            r"mandatory per-release\s+release-candidate\.json authorization",
        )
        for pattern in forbidden_patterns:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, text))

    def test_skill_pins_named_exception_boundaries(self) -> None:
        text = self.read_skill()
        self.assertIn("host-agnostic", text)
        self.assertIn("no lifecycle authority", text)

    def test_skill_documents_fail_closed_release_gate(self) -> None:
        text = self.read_skill()
        self.assertIn("fail-closed", text)

    def test_skill_documents_decision_artifact_only_release_boundary(self) -> None:
        section = self.autonomous_release_gate_section()
        self.assertIn("**禁止** decider 直接 bump/commit/push", section)
        self.assertIn("decision-artifact-only", section)

    def test_auto_release_gate_source_has_no_direct_lifecycle_calls(self) -> None:
        source = self.read_gate_source()
        forbidden = (
            "subprocess.run(['git', 'push']",
            'subprocess.run(["git", "push"]',
            '["git"',
            "bump_version",
        )
        for needle in forbidden:
            with self.subTest(needle=needle):
                self.assertNotIn(needle, source)

# Refactor (hygiene/634a608-followup): 删 stranded paragraph tests,对应 CLAUDE.md philosophy-only rewrite,段落已不存在
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

        progress = self.read_rel("skills/codex-refactor-loop/scripts/codex-progress-reporter.sh")
        self.assertRegex(progress, r"\n⟦AI:AUTO-LOOP⟧\n")
        self.assertIn("controller progress reporter", progress)

    def test_controller_generated_close_comment_uses_final_independent_sentinel(self) -> None:
        text = self.read_rel("skills/codex-refactor-loop/scripts/controller_lib.sh")

        self.assertIn("--comment \"$close_comment\"", text)
        self.assertIn("printf '✅ Auto-merged via PR #%s。\\n\\n⟦AI:AUTO-LOOP⟧'", text)
        self.assertNotIn("Auto-merged via PR #${pr}。⟦AI:AUTO-LOOP⟧", text)

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
        shell_helper = self.read_rel("skills/codex-refactor-loop/scripts/repo_slug.sh")
        self.assertIn('gh_repo_args=(--repo "$GH_REPO_SLUG")', shell_helper)
        for rel in ("skills/codex-refactor-loop/scripts/controller_lib.sh", "skills/codex-refactor-loop/scripts/peek.sh", "skills/codex-refactor-loop/scripts/triage-monitor.sh"):
            with self.subTest(script=rel):
                self.assertIn("repo_slug.sh", self.read_rel(rel))

    def test_repo_slug_resolution_is_shared_across_shell_and_python_scripts(self) -> None:
        shell_helper = self.read_rel("skills/codex-refactor-loop/scripts/repo_slug.sh")
        python_helper = self.read_rel("skills/codex-refactor-loop/scripts/repo_config.py")

        self.assertIn("resolve_github_repo_slug()", shell_helper)
        self.assertIn("set_gh_repo_args()", shell_helper)
        self.assertIn("def github_repo_slug()", python_helper)
        self.assertIn("GH_REPO_SLUG", python_helper)
        self.assertIn("GH_OWNER", python_helper)
        self.assertIn("GH_REPO_NAME", python_helper)

        for rel in (
            "skills/codex-refactor-loop/scripts/comment-monitor.sh",
            "skills/codex-refactor-loop/scripts/codex-progress-reporter.sh",
            "skills/codex-refactor-loop/scripts/controller_lib.sh",
            "skills/codex-refactor-loop/scripts/peek.sh",
            "skills/codex-refactor-loop/scripts/triage-monitor.sh",
        ):
            text = self.read_rel(rel)
            with self.subTest(shell=rel):
                self.assertIn("repo_slug.sh", text)
                self.assertNotIn('${GH_OWNER:+$GH_OWNER/}${GH_REPO_NAME:-${GH_REPO:-}}', text)

        for rel in (
            "skills/codex-refactor-loop/scripts/concurrency_monitor.py",
            "skills/codex-refactor-loop/scripts/post_banner.py",
        ):
            text = self.read_rel(rel)
            with self.subTest(python=rel):
                self.assertIn("from repo_config import github_repo_slug", text)
                self.assertNotIn('os.environ.get("GH_OWNER")', text)
                self.assertNotIn('os.environ.get("GH_REPO_NAME")', text)

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

    # Refactor (iter209/cluster-209-004-tombstone-cleanup):
    #   Old pattern: Deprecated executable tombstone remains as a checked-in production script and the reference preserves a historical tombstone note.
    #   New principle: Deprecated wrapper/tombstone files and historical policy stubs are deleted; active docs point directly at the supported replacement (post_banner.py + harness-tracked spawn-codex.sh).
    def test_deprecated_spawn_with_banner_tombstone_is_not_retained(self) -> None:
        self.assertFalse((REPO_ROOT / "skills/codex-refactor-loop/scripts/spawn_with_banner.py").exists())
        active_docs = "\n".join(self.read_rel(rel) for rel in (
            "skills/codex-refactor-loop/SKILL.md",
            "skills/codex-refactor-loop/REFERENCE.md",
            "skills/codex-refactor-loop/scripts/post_banner.py",
        ))
        self.assertIn("post_banner.py", active_docs)
        self.assertIn("spawn-codex.sh", active_docs)
        self.assertIn("反模式", active_docs)
        self.assertNotIn("spawn_with_banner.py", active_docs)
        self.assertNotIn("Historical tombstone", active_docs)

    def test_phase9_language_policy_allowlist_is_narrow(self) -> None:
        checked = self.rel_paths("skills/codex-refactor-loop/SKILL.md", "skills/codex-refactor-loop/prompts/*.md")
        forbidden_patterns = ("Bilingual rule", "双语强制", "Bilingual EN+ZH", "## English", "Recommended framing (English)")

        for path in checked:
            rel = path.relative_to(REPO_ROOT).as_posix()
            text = path.read_text(encoding="utf-8")
            for pattern in forbidden_patterns:
                with self.subTest(path=rel, pattern=pattern):
                    self.assertNotIn(pattern, text)

        skill_text = self.read_rel("skills/codex-refactor-loop/SKILL.md")
        self.assertIn("Source files are English-only; external user-facing artifacts are 中文 by default", skill_text)
        self.assertIn("No mandatory parallel English section", skill_text)

    def test_no_active_github_post_writer_reference_remains(self) -> None:
        checked = self.rel_paths("skills/codex-refactor-loop/SKILL.md", "skills/codex-refactor-loop/prompts/*.md")
        self.assert_absent("github-post-writer", checked)
        reference = self.read_rel("skills/codex-refactor-loop/REFERENCE.md")
        self.assertNotIn("Historical tombstone", reference)
        self.assertNotIn("prompts/github-post-writer.md", reference)

    def test_state_json_is_not_documented_as_phase_source_of_truth(self) -> None:
        skill_text = self.read_rel("skills/codex-refactor-loop/SKILL.md")

        self.assertIn(".refactor-loop/state.json` is a resumability index and debug ledger", skill_text)
        self.assertNotIn(".refactor-loop/state.json` tells the controller what can be resumed", skill_text)
        self.assertLess(skill_text.index(".refactor-loop/logs/*` tells the controller"), skill_text.index(".refactor-loop/state.json` is a resumability index"))

    def test_progress_reporter_clean_exit_hash_and_sentinel_contract(self) -> None:
        text = self.read_rel("skills/codex-refactor-loop/scripts/codex-progress-reporter.sh")

        self.assertIn('echo "exit_ok"', text)
        self.assertIn('echo "exit_failed"', text)
        self.assertIn('echo "in_flight"', text)
        self.assertIn('[ "$(exit_status "$1")" = "exit_ok" ]', text)
        self.assertNotIn('grep -q "^EXIT="', text)
        self.assertIn("hash_body()", text)
        self.assertIn("command -v md5", text)
        self.assertIn("command -v md5sum", text)
        self.assertIn("hashlib.md5", text)
        self.assertNotIn('cur_md5=$(echo "$body" | md5)', text)
        self.assertIn("codex 已非零退出;保留此 comment", text)

    def test_progress_reporter_orphan_delete_retry_contract(self) -> None:
        # Source-regression for the maintainer-directive at
        # .refactor-loop/runs/maintainer-directives/2026-05-27-progress-reporter-orphan-delete.md
        # (re: issue #69 — 30 orphan progress comments accumulated after old daemon marked
        # finished=true even when the GitHub DELETE call failed). The fix must:
        #   1. Only mark finished=true when DELETE confirmed (success or 404).
        #   2. Treat finished=true + non-zero comment_id as an orphan that needs delete retry.
        #   3. Carry the old-/new-pattern Refactor comment so the rationale stays in source.
        #   4. Expose a documented TEST_NO_LOOP=1 seam so the behavior test can source the daemon.
        text = self.read_rel("skills/codex-refactor-loop/scripts/codex-progress-reporter.sh")
        skill_text = self.read_rel("skills/codex-refactor-loop/SKILL.md")
        directive_text = self.read_rel(".refactor-loop/runs/maintainer-directives/2026-05-27-progress-reporter-orphan-delete.md")

        self.assertIn("Refactor (issue-69/orphan-delete-retry)", text)
        self.assertIn("needs_delete_retry", text)
        self.assertIn("comment still exists, retry next tick", text)
        self.assertIn("already 404; marking finished", text)
        self.assertIn('TEST_NO_LOOP', text)
        # The bug-introducing line that unconditionally marked finished=true on any DELETE
        # outcome must no longer be present.
        self.assertNotIn('marking finished anyway', text)

        # The test seam is a runtime surface and must carry the CLAUDE.md-required explicit
        # allowed / forbidden / fact-source / verification contract in the skill-owned contract.
        self.assertIn("## Named runtime surface — codex-progress-reporter TEST_NO_LOOP(per #69)", skill_text)
        self.assertIn("Allowed", skill_text)
        self.assertIn("Forbidden", skill_text)
        self.assertIn("Fact source", skill_text)
        self.assertIn("Verification", skill_text)
        self.assertIn("test_codex_progress_reporter_orphan.sh", skill_text)
        self.assertIn(".refactor-loop/codex-progress-state.json", skill_text)
        self.assertIn(".refactor-loop/logs/*.log", skill_text)
        self.assertIn(".refactor-loop/runs/maintainer-directives/2026-05-27-progress-reporter-orphan-delete.md", skill_text)
        self.assertIn("production daemon startup", skill_text)
        self.assertIn("must not set `TEST_NO_LOOP`", skill_text)

        self.assertIn("post_or_update` 的 terminal skip 条件增加 orphan 例外", directive_text)
        self.assertIn("新增 `TEST_NO_LOOP=1` source-time test seam", directive_text)
        self.assertNotIn("tick 顶部增加 **orphan sweep**", directive_text)

        # The accompanying behavior test must exist alongside the daemon.
        test_path = SKILL_ROOT / "scripts" / "test_codex_progress_reporter_orphan.sh"
        self.assertTrue(test_path.is_file(),
                        f"behavior test missing: {test_path}")
        test_text = test_path.read_text(encoding="utf-8")
        for required in (
            "test_delete_success_first_attempt",
            "test_delete_fail_keeps_state_for_retry",
            "test_delete_fail_but_404_marks_gone",
            "test_orphan_state_retried_on_next_tick",
        ):
            with self.subTest(test=required):
                self.assertIn(required, test_text)

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


class Phase9RouterConvergeGuardSourceRegressionTests(unittest.TestCase):
    """Phase 9 router daemon must enforce judge-only source + monotonic round on converge dispatch."""

    DAEMON_PATH = SKILL_ROOT / "scripts" / "phase9_router_daemon.py"

    def test_dispatch_meta_judge_routes_requires_judge_role_and_monotonic_round(self) -> None:
        src = self.DAEMON_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "Refactor (iter5/skill-converge-source-and-monotonic-guard)",
            src,
            "refactor self-documentation must remain attached to converge guard",
        )
        self.assertIn('if marker.role != "judge":', src,
                      "converge dispatch must require judge-role source log")
        self.assertIn("if target_round <= marker.round:", src,
                      "converge dispatch must require strictly greater target round")

    def test_directly_handled_mirrors_converge_guards(self) -> None:
        src = self.DAEMON_PATH.read_text(encoding="utf-8")
        self.assertGreaterEqual(
            src.count('if marker.role != "judge":'),
            2,
            "judge-role guard must apply in both _dispatch_meta_judge_routes and _directly_handled",
        )
        self.assertGreaterEqual(
            src.count("target_round <= marker.round"),
            2,
            "monotonic round guard must apply in both _dispatch_meta_judge_routes and _directly_handled",
        )


if __name__ == "__main__":
    unittest.main()
