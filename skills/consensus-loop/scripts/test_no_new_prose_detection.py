#!/usr/bin/env python3
"""Source-regression: new tests must not add localized prose detectors."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__)
REPO_ROOT = SCRIPT_PATH.parents[3]
BASE_REF = "origin/auto-refact-dev"

# Refactor (iter326/issue-106):
#   Old pattern: source-regression tests literally matched Chinese/English prose, so switching languages broke them wholesale
#   New principle: no detection-invariant schema suffix; use stable protocol tokens (markers/labels/anchors/sentinels/script names),
#   explicit anchors, source-regression guard against future localized prose detectors (Consensus-rnd Phase design-consensus r1 consensus:structural)
FORBIDDEN_NEW_TEST_TOKENS = (
    "事实源唯一",
    "GitHub 是系统状态唯一显示面",
    "## `👤 human:需-maintainer-决策` 严格语义(强制)",
    "ensure all 6 daemons",
    "ensure all 6 restart-helper-managed daemons",
    "AI 内容标识符",
    "缺少必备字面",
    "缺少 Refactor self-doc",
    "历史 bilingual 规则的位置",
    "不能用 `env $(grep ... host.env)`",
    "禁止** 裸 `nohup python3 <daemon> &`",
    "GitHub issue/PR/commit/design artifacts remain 中文 by default",
    "No mandatory parallel English section",
    "iter5/prompt-gh-ban-marker-only",
    "lifecycle / label ",
    "cron/launchd-only helper invariant",
    "**Producer**",
    "**Consumer**",
    "**Install one-liner**",
    "**Uninstall one-liner**",
    "**无新 daemon**",
    "**手动一行,无 installer script**",
)


def changed_test_files() -> list[Path]:
    diff_result = subprocess.run(
        ["git", "diff", "--name-only", BASE_REF, "--", "skills/consensus-loop/scripts/test_*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    untracked_result = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", "skills/consensus-loop/scripts/test_*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if diff_result.returncode != 0:
        raise AssertionError(
            f"failed to compute changed test files against {BASE_REF}: {diff_result.stderr.strip()}"
        )
    if untracked_result.returncode != 0:
        raise AssertionError(f"failed to compute untracked test files: {untracked_result.stderr.strip()}")
    paths = set(diff_result.stdout.splitlines()) | set(untracked_result.stdout.splitlines())
    return [REPO_ROOT / line for line in sorted(paths) if line]


class NoNewProseDetectionTests(unittest.TestCase):
    def test_new_or_modified_tests_do_not_add_prose_detection_tokens(self) -> None:
        offenders: list[str] = []
        changed_paths = changed_test_files()
        if SCRIPT_PATH in changed_paths:
            self.assertGreater(
                len([path for path in changed_paths if path != SCRIPT_PATH]),
                0,
                "this PR changed the prose-detector guard but scanned no other changed test files",
            )
        for path in changed_paths:
            if not path.exists():
                continue
            if path.name == SCRIPT_PATH.name:
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            text = path.read_text(encoding="utf-8")
            for token in FORBIDDEN_NEW_TEST_TOKENS:
                if token in text:
                    offenders.append(f"{rel}: {token}")

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
