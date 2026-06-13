#!/usr/bin/env python3
"""Behavior tests for daemon-event Monitor bridge filtering."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.monitor_bridge import filtered_lines, should_forward_line


CLI = SCRIPT_DIR / "consensus-rnd-cli"


class MonitorBridgeFilterTests(unittest.TestCase):
    def test_filter_drops_tail_headers_blank_lines_and_known_wakeup_noise(self) -> None:
        lines = [
            "==> .refactor-loop/.controller-pending-events.log <==\n",
            "\n",
            "   \n",
            "2026-06-13T00:00:00Z HARD_GATE:dispatch_required=3:actual=0 expected=3\n",
            "2026-06-13T00:00:01Z concurrency-alert P0 no-gap-violation: 0 codex with 1 active task(s)\n",
            "2026-06-13T00:00:02Z WAKEUP_RUNNER_BLOCKED:spawn:target_log_exists\n",
            "2026-06-13T00:00:03Z WAKEUP_RUNNER_BLOCKED:harness-spawn-intent:closed:target_not_open:CLOSED\n",
        ]

        self.assertEqual([], filtered_lines(lines))

    def test_filter_forwards_unknown_and_substantive_daemon_events(self) -> None:
        lines = [
            "2026-06-13T00:00:00Z SOLVER_DONE:issue-890:minimal:propose\n",
            "2026-06-13T00:00:01Z WAIT:single-active-audit:dispatch_required=0:blocked_deficit=2\n",
            "2026-06-13T00:00:02Z WAKEUP_RUNNER_BLOCKED:spawn:permission_denied\n",
        ]

        self.assertEqual([line.rstrip("\n") for line in lines], filtered_lines(lines))

    def test_raw_p0_forwarding_is_first_line_and_every_fifth_streak_only(self) -> None:
        cases = {
            -1: True,
            0: True,
            1: True,
            2: False,
            4: False,
            5: True,
            6: False,
            10: True,
        }
        for zero_streak, expected in cases.items():
            with self.subTest(zero_streak=zero_streak):
                line = (
                    "[2026-06-13T00:00:00Z] P0 no-gap-violation: 0 codex with 1 active task(s) "
                    f'| detail={{"zero_streak": {zero_streak}, "severity": "P0"}}'
                )
                self.assertEqual(expected, should_forward_line(line))

    def test_raw_p0_with_missing_or_unparseable_streak_fails_open(self) -> None:
        lines = [
            "[2026-06-13T00:00:00Z] P0 no-gap-violation: no detail",
            '[2026-06-13T00:00:01Z] P0 no-gap-violation | detail={"zero_streak": "not-int"}',
            "[2026-06-13T00:00:02Z] P0 no-gap-violation zero_streak=not-int",
        ]

        for line in lines:
            with self.subTest(line=line):
                self.assertTrue(should_forward_line(line))

    def test_cli_filters_stdin(self) -> None:
        payload = "\n".join(
            [
                "==> .refactor-loop/.concurrency-alert.log <==",
                "[2026-06-13T00:00:00Z] P0 no-gap-violation | detail={\"zero_streak\": 1}",
                "[2026-06-13T00:01:00Z] P0 no-gap-violation | detail={\"zero_streak\": 2}",
                "2026-06-13T00:02:00Z SOLVER_DONE:issue-890:structural:propose",
                "",
            ]
        )

        result = subprocess.run(
            [sys.executable, str(CLI), "monitor-bridge-filter"],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            "\n".join(
                [
                    "[2026-06-13T00:00:00Z] P0 no-gap-violation | detail={\"zero_streak\": 1}",
                    "2026-06-13T00:02:00Z SOLVER_DONE:issue-890:structural:propose",
                    "",
                ]
            ),
            result.stdout,
        )


if __name__ == "__main__":
    unittest.main()
