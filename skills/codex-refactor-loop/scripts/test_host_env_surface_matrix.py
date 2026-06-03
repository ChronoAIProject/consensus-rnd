#!/usr/bin/env python3
"""Source-regression tests for the host.env surface matrix."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
SKILL_ROOT = SCRIPT_PATH.parents[1]
SKILL_MD = SKILL_ROOT / "SKILL.md"
HOST_ENV_EXAMPLE = SKILL_ROOT / "host.env.example"
PROMPTS_DIR = SKILL_ROOT / "prompts"
SCRIPTS_DIR = SKILL_ROOT / "scripts"

MATRIX_HEADING = "### Host env surface matrix"
MATRIX_COLUMNS = [
    "Variable",
    "Category",
    "Owner",
    "Default/example",
    "Missing/empty behavior",
    "Consumer",
    "Test owner",
]
ALLOWED_CATEGORIES = {
    "required",
    "defaulted",
    "optional-noop",
    "conditional-fail-closed",
    "prompt-empty-infer",
    "compatibility",
}
LOCATOR_ONLY_VARIABLES = {"CONSENSUS_RND_HOST_ENV"}
TEMPLATE_CATEGORY_HEADERS = {
    "required",
    "defaulted",
    "optional-empty-or-noop / compatibility / conditional-fail-closed",
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_skill_matrix() -> dict[str, dict[str, str]]:
    text = read(SKILL_MD)
    _, found, after_heading = text.partition(MATRIX_HEADING)
    if not found:
        raise AssertionError(f"missing {MATRIX_HEADING}")
    lines = after_heading.splitlines()
    header_index = next((index for index, line in enumerate(lines) if line.startswith("| Variable |")), None)
    if header_index is None:
        raise AssertionError("missing host matrix table header")
    header = split_table_row(lines[header_index])
    if header != MATRIX_COLUMNS:
        raise AssertionError(f"unexpected host matrix columns: {header}")

    rows: dict[str, dict[str, str]] = {}
    for line in lines[header_index + 2 :]:
        if not line.startswith("|"):
            break
        cells = split_table_row(line)
        if len(cells) != len(MATRIX_COLUMNS):
            raise AssertionError(f"host matrix row does not have {len(MATRIX_COLUMNS)} cells: {line}")
        row = dict(zip(MATRIX_COLUMNS, cells))
        variable_cell = row["Variable"]
        match = re.fullmatch(r"`\$(?P<key>[A-Z0-9_]+)`", variable_cell)
        if not match:
            raise AssertionError(f"invalid variable cell: {variable_cell}")
        key = match.group("key")
        if key in rows:
            raise AssertionError(f"duplicate host matrix variable: {key}")
        rows[key] = row
    return rows


def parse_host_env_example() -> tuple[dict[str, dict[str, str]], set[str]]:
    exports: dict[str, dict[str, str]] = {}
    section = ""
    sections: set[str] = set()
    export_re = re.compile(r'^export\s+(?P<key>[A-Z0-9_]+)="(?P<value>[^"]*)"')
    for line in read(HOST_ENV_EXAMPLE).splitlines():
        header_match = re.match(r"^# ─── (?P<section>.+?) ─", line)
        if header_match:
            section = header_match.group("section").strip()
            sections.add(section)
            continue
        export_match = export_re.match(line)
        if export_match:
            key = export_match.group("key")
            exports[key] = {"value": export_match.group("value"), "section": section}
    return exports, sections


class HostEnvSurfaceMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = parse_skill_matrix()
        self.exports, self.template_sections = parse_host_env_example()

    def test_surface_matrix_refactor_self_doc_matches_current_contract(self) -> None:
        expected_tokens = (
            "Refactor (iter1/issue-170)",
            "host.env contract facts were split across prose tables and",
            "categories, defaults, consumers, and test ownership",
            "SKILL.md owns one host.env surface matrix",
            "host.env.example is",
            "a copyable template view",
            "tests mechanically derive exported keys",
            "prompt placeholders",
            "runtime literal anchors",
        )
        for path in (SKILL_MD, HOST_ENV_EXAMPLE):
            text = read(path)
            with self.subTest(path=path.name):
                for token in expected_tokens:
                    self.assertIn(token, text)
                self.assertNotIn("host.env.example documented only a subset", text)

    def test_skill_matrix_rows_are_complete_and_unique(self) -> None:
        self.assertEqual(set(self.template_sections), TEMPLATE_CATEGORY_HEADERS)
        self.assertNotIn("GH_REPO", self.rows)
        for key, row in self.rows.items():
            with self.subTest(key=key):
                for column in MATRIX_COLUMNS:
                    self.assertTrue(row[column], f"{key} missing {column}")
                self.assertIn(row["Category"], ALLOWED_CATEGORIES)
                self.assertNotRegex(row["Owner"], r":\d+")
                self.assertNotRegex(row["Consumer"], r":\d+")
                self.assertNotRegex(row["Test owner"], r":\d+")

    def test_host_env_example_exports_match_skill_matrix(self) -> None:
        self.assertEqual(set(self.exports), set(self.rows) - LOCATOR_ONLY_VARIABLES)
        self.assertNotIn("GH_REPO", self.exports)
        self.assertEqual("required", self.rows["GH_REPO_SLUG"]["Category"])
        self.assertIn("preferred slug", self.rows["GH_REPO_SLUG"]["Missing/empty behavior"])
        self.assertEqual("required", self.rows["INTEGRATION_BRANCH"]["Category"])
        self.assertEqual("required", self.rows["REVIEW_BASE_BRANCH"]["Category"])
        self.assertIn("fail closed when missing or empty", self.rows["INTEGRATION_BRANCH"]["Missing/empty behavior"])
        self.assertIn("fail closed when missing or empty", self.rows["REVIEW_BASE_BRANCH"]["Missing/empty behavior"])
        self.assertIn("required", self.exports["INTEGRATION_BRANCH"]["section"])
        self.assertIn("required", self.exports["REVIEW_BASE_BRANCH"]["section"])
        self.assertNotEqual("auto-refact-dev", self.exports["INTEGRATION_BRANCH"]["value"])
        self.assertNotEqual("dev", self.exports["REVIEW_BASE_BRANCH"]["value"])
        self.assertEqual("compatibility", self.rows["GH_OWNER"]["Category"])
        self.assertEqual("compatibility", self.rows["GH_REPO_NAME"]["Category"])
        self.assertIn("required", self.exports["GH_REPO_SLUG"]["section"])
        self.assertIn("optional-empty-or-noop", self.exports["GH_OWNER"]["section"])
        self.assertIn("optional-empty-or-noop", self.exports["GH_REPO_NAME"]["section"])

    def test_defaults_and_missing_behaviors_match(self) -> None:
        cases = {
            "RELEASE_AUTO_ENABLE": ("false", "false or empty exits 0 with noop reason"),
            "HOST_GITHUB_RELEASE_REQUIRED_CHECKS": ("ci,lint,typecheck", "missing_host_required_release_checks"),
            "UPDATE_CHECK_ENABLE": ("false", "disabled update-check state"),
            "UPDATE_CHECK_INTERVAL_SECONDS": ("21600", "fresh local update-check state"),
            "UPDATE_CHECK_TIMEOUT_SECONDS": ("5", "failures write unknown state"),
            "CODEX_FLOOR": ("5", "hard min `2`"),
            "ACTIVE_CONTROLLER_DEVICE_ID": ("", "single-device local-owner noop"),
            "ACTIVE_CONTROLLER_TTL_SECONDS": ("1800", "expired lease may be acquired by another device"),
            "COMMENT_MONITOR_INTERVAL": ("30", "unchanged `updatedAt` items skip comments queries"),
            "COMMENT_MONITOR_LOOKBACK": ("", "empty adds no lookback filter"),
            "RELEASE_ROLLUP_COOLDOWN_SECONDS": ("21600", "same integration SHA"),
        }
        for key, (default, behavior) in cases.items():
            with self.subTest(key=key):
                self.assertEqual(default, self.exports[key]["value"])
                if default:
                    self.assertIn(f"`{default}`", self.rows[key]["Default/example"])
                self.assertIn(behavior, self.rows[key]["Missing/empty behavior"])

        locator = self.rows["CONSENSUS_RND_HOST_ENV"]
        self.assertEqual("required", locator["Category"])
        self.assertEqual("HostEnvLocator", locator["Owner"])
        self.assertEqual("LoopContext locator", locator["Consumer"])
        self.assertIn("required for host fact loading", locator["Missing/empty behavior"])
        self.assertIn("no `.refactor-loop/host.env` fallback is read", locator["Missing/empty behavior"])
        self.assertIn("not host production config schema", locator["Missing/empty behavior"])
        self.assertIn("test_loop_context.py", locator["Test owner"])
        self.assertNotIn("CONSENSUS_RND_HOST_ENV", self.exports)

        self.assertEqual("optional-noop", self.rows["ACTIVE_CONTROLLER_DEVICE_ID"]["Category"])
        self.assertEqual("optional-noop", self.rows["UPDATE_CHECK_ENABLE"]["Category"])
        self.assertEqual("defaulted", self.rows["UPDATE_CHECK_INTERVAL_SECONDS"]["Category"])
        self.assertEqual("defaulted", self.rows["UPDATE_CHECK_TIMEOUT_SECONDS"]["Category"])
        self.assertEqual("defaulted", self.rows["ACTIVE_CONTROLLER_TTL_SECONDS"]["Category"])
        self.assertNotIn("ACTIVE_CONTROLLER_REF", self.rows)
        self.assertNotIn("ACTIVE_CONTROLLER_REF", self.exports)
        self.assertEqual("optional-noop", self.rows["COMMENT_MONITOR_LOOKBACK"]["Category"])
        self.assertIn("GitHub `updated:` search qualifier", self.rows["COMMENT_MONITOR_LOOKBACK"]["Missing/empty behavior"])
        self.assertIn("all devices upgraded", read(HOST_ENV_EXAMPLE))
        self.assertIn("Mixed old/new versions are not safe for multi-device mode", read(SKILL_MD))
        self.assertIn("active-controller lease ref is a code-owned singleton constant", read(SKILL_MD))

        whitelist = self.rows["MAINTAINER_WHITELIST"]
        self.assertEqual("conditional-fail-closed", whitelist["Category"])
        self.assertIn("comment-monitor/direct-mention intake", whitelist["Missing/empty behavior"])
        self.assertIn("fails closed", whitelist["Missing/empty behavior"])

        host_rows = {
            key: row
            for key, row in self.rows.items()
            if key.startswith("HOST_") and key != "HOST_REFACTOR_COMMENT_POLICY"
        }
        self.assertGreaterEqual(len(host_rows), 7)
        for key, row in host_rows.items():
            with self.subTest(key=key):
                if key == "HOST_WORKFLOW_SPEC":
                    self.assertEqual("", self.exports[key]["value"])
                    self.assertEqual("optional-noop", row["Category"])
                    self.assertIn("built-in behavior", row["Missing/empty behavior"])
                elif key == "HOST_GITHUB_RELEASE_REQUIRED_CHECKS":
                    self.assertEqual("defaulted", row["Category"])
                    self.assertIn("exact GitHub check-run names", row["Missing/empty behavior"])
                    self.assertIn("host-owned comma-separated exact check-run names", row["Default/example"])
                    self.assertEqual("ci,lint,typecheck", self.exports[key]["value"])
                else:
                    self.assertEqual("", self.exports[key]["value"])
                    self.assertEqual("prompt-empty-infer", row["Category"])
                    self.assertRegex(row["Missing/empty behavior"], r"infer|mirror|match|omit|diff")
        self.assertIn("do not invent a host language default", self.rows["HOST_CODE_FENCE_LANG"]["Missing/empty behavior"])
        self.assertIn("do not invent protobuf", self.rows["HOST_PROTO_POLICY"]["Missing/empty behavior"])

    def test_refactor_comment_policy_is_defaulted_and_registered(self) -> None:
        key = "HOST_REFACTOR_COMMENT_POLICY"
        self.assertIn(key, self.rows)
        self.assertIn(key, self.exports)

        row = self.rows[key]
        self.assertEqual("defaulted", row["Category"])
        self.assertEqual("prompt templates", row["Owner"])
        self.assertEqual("prompt templates", row["Consumer"])
        self.assertIn("`none`", row["Default/example"])
        self.assertEqual("none", self.exports[key]["value"])
        self.assertIn("missing/empty/default normalizes to `none`", row["Missing/empty behavior"])
        self.assertIn("rationale belongs in external artifacts", row["Missing/empty behavior"])
        self.assertIn("explicit `self-doc-comment` is downstream compatibility opt-in", row["Missing/empty behavior"])
        self.assertIn("source English-only", row["Missing/empty behavior"])
        self.assertIn("invalid and fail-closed", row["Missing/empty behavior"])
        self.assertIn("test_refactor_comment_policy_prompt_contract.py", row["Test owner"])
        self.assertIn("test_source_language_policy.py", row["Test owner"])
        self.assertIn("defaulted", self.exports[key]["section"])

        text = "\n".join(
            [
                read(SKILL_MD),
                read(HOST_ENV_EXAMPLE),
                *(read(prompt) for prompt in PROMPTS_DIR.glob("*.md")),
            ]
        )
        self.assertIn("${HOST_REFACTOR_COMMENT_POLICY}", text)
        self.assertIn("HOST_REFACTOR_COMMENT_POLICY=\"none\"", read(HOST_ENV_EXAMPLE))
        for alias in ("HOST_SOURCE_COMMENT_POLICY", "HOST_REFACTOR_SELF_DOC_POLICY"):
            with self.subTest(alias=alias):
                self.assertNotIn(alias, text)
                self.assertNotIn(alias, self.rows)

    def test_prompt_host_placeholders_are_registered(self) -> None:
        placeholders: set[str] = set()
        for prompt in PROMPTS_DIR.glob("*.md"):
            placeholders.update(re.findall(r"\$\{(HOST_[A-Z0-9_]+)\}", read(prompt)))

        self.assertGreaterEqual(len(placeholders), 6)
        self.assertLessEqual(placeholders, set(self.rows))
        for key in placeholders:
            with self.subTest(key=key):
                if key == "HOST_REFACTOR_COMMENT_POLICY":
                    self.assertEqual("defaulted", self.rows[key]["Category"])
                else:
                    self.assertEqual("prompt-empty-infer", self.rows[key]["Category"])

        rejected_aliases = {
            "HOST_LANGUAGE",
            "HOST_FRAMEWORK",
            "HOST_TEST_FRAMEWORK",
            "HOST_SCHEMA_LANGUAGE",
            "HOST_DEFAULT_BRANCH",
            "HOST_SOURCE_COMMENT_POLICY",
            "HOST_REFACTOR_SELF_DOC_POLICY",
        }
        all_prompt_text = "\n".join(read(prompt) for prompt in PROMPTS_DIR.glob("*.md"))
        for alias in rejected_aliases:
            with self.subTest(alias=alias):
                self.assertNotIn(alias, all_prompt_text)
                self.assertNotIn(alias, self.rows)

    def test_each_consumer_has_existing_test_owner(self) -> None:
        existing_tests = {path.name for path in SCRIPTS_DIR.glob("test_*.py")}
        existing_tests.add("source-regression")
        for key, row in self.rows.items():
            owners = [owner.strip(" `") for owner in row["Test owner"].split(",")]
            with self.subTest(key=key):
                self.assertTrue(owners)
                self.assertTrue(any(owner in existing_tests for owner in owners), row["Test owner"])

        anchors = {
            "REPO_ROOT": "test_loop_context.py",
            "GH_REPO_SLUG": "test_loop_context.py",
            "RELEASE_AUTO_ENABLE": "test_auto_release_gate.py",
            "MAINTAINER_WHITELIST": "test_comment_monitor.py",
            "CODEX_FLOOR": "test_concurrency_monitor.py",
            "CONSENSUS_RND_HOST_ENV": "test_loop_context.py",
        }
        for key, test_file in anchors.items():
            with self.subTest(key=key):
                self.assertIn(test_file, self.rows[key]["Test owner"])

    def test_high_risk_runtime_literals_remain_aligned(self) -> None:
        release_gate = read(SCRIPTS_DIR / "codex_refactor_loop" / "release" / "gate.py")
        comment_monitor = read(SCRIPTS_DIR / "codex_refactor_loop" / "monitors" / "comment.py")
        progress_monitor = read(SCRIPTS_DIR / "codex_refactor_loop" / "monitors" / "progress.py")
        concurrency_monitor = read(SCRIPTS_DIR / "codex_refactor_loop" / "monitors" / "concurrency.py")
        sync_dev = read(SCRIPTS_DIR / "codex_refactor_loop" / "sync" / "dev.py")
        active_controller = read(SCRIPTS_DIR / "codex_refactor_loop" / "active_controller.py")

        self.assertIn('host_env.get("RELEASE_AUTO_ENABLE") != "true"', release_gate)
        self.assertIn("auto-release noop: RELEASE_AUTO_ENABLE is not true in host.env", release_gate)
        self.assertIn("empty GH_REPO_SLUG, REVIEW_BASE_BRANCH, or INTEGRATION_BRANCH", release_gate)
        self.assertIn("MAINTAINER_WHITELIST is unset; comment-monitor fails closed", comment_monitor)
        self.assertIn('os.environ.get("COMMENT_MONITOR_LOOKBACK", "")', comment_monitor)
        self.assertIn('os.environ.get("COMMENT_MONITOR_INTERVAL")', comment_monitor)
        self.assertIn('return f"updated:>={raw}"', comment_monitor)
        for key in ("STATE_DIR", "STATE_FILE", "LOG_DIR", "PROMPTS_DIR", "PROGRESS_REPORTER_INTERVAL"):
            with self.subTest(unregistered=key):
                self.assertNotIn(key, self.rows)
                self.assertNotIn(key, self.exports)
                self.assertNotIn(key, read(SKILL_MD))
                self.assertNotIn(key, read(HOST_ENV_EXAMPLE))
                self.assertNotIn(key, progress_monitor)
                self.assertNotIn(key, comment_monitor)
        self.assertNotIn('os.environ.get("INTERVAL"', progress_monitor)
        self.assertNotIn('os.environ.get("INTERVAL"', comment_monitor)
        self.assertNotIn('os.environ.get("STATE_FILE"', comment_monitor)
        self.assertNotIn('os.environ.get("STATE_FILE"', progress_monitor)
        self.assertIn('os.environ.get("CODEX_FLOOR", "5")', concurrency_monitor)
        self.assertIn("return max(2, floor)", concurrency_monitor)
        self.assertIn('env.get("UPDATE_CHECK_ENABLE")', read(SCRIPTS_DIR / "codex_refactor_loop" / "update_check.py"))
        self.assertNotIn("DEGRADATION_WATCH_INTERVAL_SECONDS", concurrency_monitor)
        self.assertNotIn("DEGRADATION_WATCH_TIMEOUT_SECONDS", concurrency_monitor)
        self.assertIn("DEFAULT_RELEASE_ROLLUP_COOLDOWN_SECONDS = 21600", sync_dev)
        self.assertIn("DEFAULT_RELEASE_ROLLUP_MIN_COMMITS = 1", sync_dev)
        self.assertIn("missing required host branch env", sync_dev)
        self.assertNotIn("DEFAULT_INTEGRATION_BRANCH", sync_dev)
        self.assertNotIn("DEFAULT_REVIEW_BASE_BRANCH", sync_dev)
        self.assertNotIn('get("INTEGRATION")', sync_dev)
        self.assertNotIn('get("REVIEW_BASE")', sync_dev)
        self.assertNotIn('get("WORKTREE"', sync_dev)
        self.assertIn('env.get("ACTIVE_CONTROLLER_DEVICE_ID", "")', active_controller)
        self.assertIn("lease_ref=DEFAULT_ACTIVE_CONTROLLER_REF", active_controller)
        self.assertNotIn('env.get("ACTIVE_CONTROLLER_REF"', active_controller)
        self.assertIn('env.get("ACTIVE_CONTROLLER_TTL_SECONDS"', active_controller)


if __name__ == "__main__":
    unittest.main()
