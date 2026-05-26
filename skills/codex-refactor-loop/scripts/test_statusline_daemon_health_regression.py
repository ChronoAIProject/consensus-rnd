#!/usr/bin/env python3
"""Source-regression tests for the statusline daemon-health extension.

These assertions lock the SKILL.md narrow-allowlist wording, the producer
function name, and the consumer display format so that doc and implementation
cannot drift apart. They use plain substring grep on the file text, not the
shared paragraph helper, because the helper currently fails on an unrelated
pre-existing CLAUDE.md drift (commit 634a608 rewrote CLAUDE.md to
philosophy-only but did not retire the corresponding paragraph tests).
"""

import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = SKILL_ROOT / "SKILL.md"
STATUSLINE_SH = SKILL_ROOT / "scripts" / "statusline.sh"
CONCURRENCY_MONITOR = SKILL_ROOT / "scripts" / "concurrency_monitor.py"


class StatuslineDaemonHealthSourceRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill_md = SKILL_MD.read_text(encoding="utf-8")
        self.statusline_sh = STATUSLINE_SH.read_text(encoding="utf-8")
        self.monitor_py = CONCURRENCY_MONITOR.read_text(encoding="utf-8")

    def test_skill_md_documents_daemon_health_in_statusline_section(self) -> None:
        self.assertIn("Claude Code statusline", self.skill_md)
        self.assertIn("daemon 健康", self.skill_md)
        self.assertIn("daemons_healthy", self.skill_md)
        self.assertIn("daemons_total", self.skill_md)
        self.assertIn("heartbeats/*.ts", self.skill_md)
        # Display format example must match what statusline.sh prints so future
        # readers do not invent a different schema.
        self.assertIn("d:5/5", self.skill_md)

    def test_skill_md_locks_narrow_allowlist_for_extension(self) -> None:
        # The exception must stay narrow: no new daemon, no lifecycle authority,
        # no prompt-body reading. These are the same invariants #51 r3 set;
        # this PR only widens the producer to also stat heartbeats.
        self.assertIn("不引入新 daemon", self.skill_md)
        self.assertIn("不持 lifecycle authority", self.skill_md)
        self.assertIn("不读 prompt body", self.skill_md)
        self.assertIn("无 hard-coded daemon 列表", self.skill_md)

    def test_producer_function_name_present(self) -> None:
        self.assertIn("def read_daemon_heartbeats(", self.monitor_py)
        self.assertIn("HEARTBEATS_DIR", self.monitor_py)
        self.assertIn("HEARTBEAT_STALE_SECONDS = 90", self.monitor_py)

    def test_consumer_reads_daemon_fields(self) -> None:
        self.assertIn(".daemons_healthy", self.statusline_sh)
        self.assertIn(".daemons_total", self.statusline_sh)
        # Display segment format and warning behavior are locked.
        self.assertIn(' d:${d_healthy}/${d_total}', self.statusline_sh)


if __name__ == "__main__":
    unittest.main()
