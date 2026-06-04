#!/usr/bin/env python3
"""Guard against suite-level daemon checks using host-wide process tables."""

from __future__ import annotations

import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
SKILL_MD = SKILL_ROOT / "SKILL.md"
IMPLEMENT_PROMPT = SKILL_ROOT / "prompts" / "implement.md"
VERIFY_PROMPT = SKILL_ROOT / "prompts" / "verify.md"


class DaemonLeakGuardTests(unittest.TestCase):
    def test_suite_guard_does_not_use_host_wide_process_table(self) -> None:
        source = Path(__file__).read_text(encoding="utf-8")
        forbidden = (
            "subprocess" + ".run",
            "ps" + " -eo",
            '["' + "ps" + '"',
            "pid=," + "command=",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_daemon_leak_contract_is_helper_local_not_suite_process_table(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (SKILL_MD, IMPLEMENT_PROMPT, VERIFY_PROMPT)
        )
        self.assertIn("No suite-level host-wide process-table daemon guard", combined)
        self.assertIn("helper-local fact source", combined)
        self.assertIn("fast / hermetic / behavior-first", combined)
        self.assertIn("ps" + " -eo " + "pid=," + "command=", combined)


if __name__ == "__main__":
    unittest.main()
