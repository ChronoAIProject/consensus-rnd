#!/usr/bin/env python3
"""Source-regression: new tests must not add localized prose detectors."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__)
REPO_ROOT = SCRIPT_PATH.parents[3]
BASE_REF = "origin/auto-refact-dev"

# Refactor (issue106/nl-prose-detector-anchors):
#   Old pattern: source-regression tests used localized prose literals as detector anchors, so
#   normal wording edits or language cleanup could break machine checks and encourage brittle text pinning.
#   New principle: detector tests must key on protocol tokens, sentinels, heading ids, and structured
#   context; this guard prevents newly changed tests from adding back localized prose detector tokens.
FORBIDDEN_NEW_TEST_TOKENS = (
    "事实源唯一",
    "GitHub 是系统状态唯一显示面",
    "ensure all 5 daemons",
    "ensure all 5 restart-helper-managed daemons",
    "AI 内容标识符",
    "缺少必备字面",
    "缺少 Refactor self-doc",
    "历史 bilingual 规则的位置",
    "不能用 `env $(grep ... host.env)`",
    "禁止** 裸 `nohup python3 <daemon> &`",
    "Source files are English-only; external user-facing artifacts are 中文 by default",
    "No mandatory parallel English section",
)


def changed_test_files() -> list[Path]:
    diff_result = subprocess.run(
        ["git", "diff", "--name-only", BASE_REF, "--", "skills/codex-refactor-loop/scripts/test_*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    untracked_result = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", "skills/codex-refactor-loop/scripts/test_*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if diff_result.returncode != 0 or untracked_result.returncode != 0:
        return []
    paths = set(diff_result.stdout.splitlines()) | set(untracked_result.stdout.splitlines())
    return [REPO_ROOT / line for line in sorted(paths) if line]


class NoNewProseDetectionTests(unittest.TestCase):
    def test_new_or_modified_tests_do_not_add_prose_detection_tokens(self) -> None:
        offenders: list[str] = []
        for path in changed_test_files():
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
