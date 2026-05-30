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
        self.assertEqual(set(self.exports), set(self.rows))
        self.assertNotIn("GH_REPO", self.exports)
        self.assertEqual("required", self.rows["GH_REPO_SLUG"]["Category"])
        self.assertIn("preferred slug", self.rows["GH_REPO_SLUG"]["Missing/empty behavior"])
        self.assertEqual("compatibility", self.rows["GH_OWNER"]["Category"])
        self.assertEqual("compatibility", self.rows["GH_REPO_NAME"]["Category"])
        self.assertIn("required", self.exports["GH_REPO_SLUG"]["section"])
        self.assertIn("optional-empty-or-noop", self.exports["GH_OWNER"]["section"])
        self.assertIn("optional-empty-or-noop", self.exports["GH_REPO_NAME"]["section"])

    def test_defaults_and_missing_behaviors_match(self) -> None:
        cases = {
            "RELEASE_AUTO_ENABLE": ("false", "false or empty exits 0 with noop reason"),
            "CODEX_FLOOR": ("5", "hard min `2`"),
            "RELEASE_ROLLUP_COOLDOWN_SECONDS": ("21600", "same integration SHA"),
        }
        for key, (default, behavior) in cases.items():
            with self.subTest(key=key):
                self.assertEqual(default, self.exports[key]["value"])
                self.assertIn(f"`{default}`", self.rows[key]["Default/example"])
                self.assertIn(behavior, self.rows[key]["Missing/empty behavior"])

        whitelist = self.rows["MAINTAINER_WHITELIST"]
        self.assertEqual("conditional-fail-closed", whitelist["Category"])
        self.assertIn("comment-monitor/direct-mention intake", whitelist["Missing/empty behavior"])
        self.assertIn("fails closed", whitelist["Missing/empty behavior"])

        host_rows = {key: row for key, row in self.rows.items() if key.startswith("HOST_")}
        self.assertGreaterEqual(len(host_rows), 7)
        for key, row in host_rows.items():
            with self.subTest(key=key):
                self.assertEqual("", self.exports[key]["value"])
                if key == "HOST_WORKFLOW_SPEC":
                    self.assertEqual("optional-noop", row["Category"])
                    self.assertIn("built-in behavior", row["Missing/empty behavior"])
                else:
                    self.assertEqual("prompt-empty-infer", row["Category"])
                    self.assertRegex(row["Missing/empty behavior"], r"infer|mirror|match|omit|diff")
        self.assertIn("do not invent a host language default", self.rows["HOST_CODE_FENCE_LANG"]["Missing/empty behavior"])
        self.assertIn("do not invent protobuf", self.rows["HOST_PROTO_POLICY"]["Missing/empty behavior"])

    def test_prompt_host_placeholders_are_registered(self) -> None:
        placeholders: set[str] = set()
        for prompt in PROMPTS_DIR.glob("*.md"):
            placeholders.update(re.findall(r"\$\{(HOST_[A-Z0-9_]+)\}", read(prompt)))

        self.assertGreaterEqual(len(placeholders), 6)
        self.assertLessEqual(placeholders, set(self.rows))
        for key in placeholders:
            with self.subTest(key=key):
                self.assertEqual("prompt-empty-infer", self.rows[key]["Category"])

        rejected_aliases = {
            "HOST_LANGUAGE",
            "HOST_FRAMEWORK",
            "HOST_TEST_FRAMEWORK",
            "HOST_SCHEMA_LANGUAGE",
            "HOST_DEFAULT_BRANCH",
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
        }
        for key, test_file in anchors.items():
            with self.subTest(key=key):
                self.assertIn(test_file, self.rows[key]["Test owner"])

    def test_high_risk_runtime_literals_remain_aligned(self) -> None:
        release_gate = read(SCRIPTS_DIR / "codex_refactor_loop" / "release" / "gate.py")
        comment_monitor = read(SCRIPTS_DIR / "codex_refactor_loop" / "monitors" / "comment.py")
        concurrency_monitor = read(SCRIPTS_DIR / "codex_refactor_loop" / "monitors" / "concurrency.py")
        sync_dev = read(SCRIPTS_DIR / "codex_refactor_loop" / "sync" / "dev.py")

        self.assertIn('host_env.get("RELEASE_AUTO_ENABLE") != "true"', release_gate)
        self.assertIn("auto-release noop: RELEASE_AUTO_ENABLE is not true in host.env", release_gate)
        self.assertIn("empty GH_REPO_SLUG, REVIEW_BASE_BRANCH, or INTEGRATION_BRANCH", release_gate)
        self.assertIn("MAINTAINER_WHITELIST is unset; comment-monitor fails closed", comment_monitor)
        self.assertIn('os.environ.get("CODEX_FLOOR", "5")', concurrency_monitor)
        self.assertIn("return max(2, floor)", concurrency_monitor)
        self.assertNotIn("DEGRADATION_WATCH_INTERVAL_SECONDS", concurrency_monitor)
        self.assertNotIn("DEGRADATION_WATCH_TIMEOUT_SECONDS", concurrency_monitor)
        self.assertIn("DEFAULT_RELEASE_ROLLUP_COOLDOWN_SECONDS = 21600", sync_dev)
        self.assertIn("DEFAULT_RELEASE_ROLLUP_MIN_COMMITS = 1", sync_dev)


if __name__ == "__main__":
    unittest.main()
