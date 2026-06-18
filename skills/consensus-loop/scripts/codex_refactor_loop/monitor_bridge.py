"""Read-stdin filter for controller daemon-event Monitor bridges."""

from __future__ import annotations

import argparse
import re
import sys
from collections import deque
from collections.abc import Iterable, Sequence
from typing import TextIO


RECENT_KEY_LIMIT = 128
TAIL_HEADER_RE = re.compile(r"^==> .+ <==$")
RUNNER_BLOCKER_RE = re.compile(
    r"\bWAKEUP_RUNNER_BLOCKED:[^\s]*:(?:target_log_exists|target_not_open)(?::|\s|$)"
)
ZERO_STREAK_RE = re.compile(r'(?:"zero_streak"\s*:\s*|\bzero_streak=)(-?\d+)')
LEADING_TIMESTAMP_RE = re.compile(
    r"^(?:"
    r"\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\]\s*"
    r"|"
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\s+"
    r")"
)


def raw_p0_zero_streak(line: str) -> int | None:
    match = ZERO_STREAK_RE.search(line)
    if match is None:
        return None
    return int(match.group(1))


def should_forward_line(line: str) -> bool:
    return _should_forward_line_stateless(line)


def _should_forward_line_stateless(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if TAIL_HEADER_RE.match(stripped):
        return False
    if "HARD_GATE:dispatch_required" in stripped:
        return False
    if "concurrency-alert P0 no-gap-violation" in stripped:
        return False
    if RUNNER_BLOCKER_RE.search(stripped):
        return False
    if "P0 no-gap-violation" in stripped:
        zero_streak = raw_p0_zero_streak(stripped)
        if zero_streak is None or zero_streak < 1:
            return True
        return zero_streak == 1 or zero_streak % 5 == 0
    return True


def _recent_key_for_line(line: str) -> str | None:
    output_line = line.rstrip("\n")
    stripped = output_line.strip()
    if stripped.startswith("DEV_SYNC_PENDING:"):
        return f"pending:{output_line}"
    if "P0 no-gap-violation" in stripped:
        stable_body = LEADING_TIMESTAMP_RE.sub("", stripped, count=1)
        return f"raw-p0:{stable_body}"
    return None


class MonitorBridgeFilter:
    def __init__(self, recent_key_limit: int = RECENT_KEY_LIMIT) -> None:
        self._recent_key_limit = recent_key_limit
        self._recent_keys: deque[str] = deque()
        self._recent_key_set: set[str] = set()

    def should_forward(self, line: str) -> bool:
        if not _should_forward_line_stateless(line):
            return False

        recent_key = _recent_key_for_line(line)
        if recent_key is None:
            return True
        return self._remember_recent_key(recent_key)

    def _remember_recent_key(self, key: str) -> bool:
        if key in self._recent_key_set:
            return False

        self._recent_keys.append(key)
        self._recent_key_set.add(key)
        while len(self._recent_keys) > self._recent_key_limit:
            old_key = self._recent_keys.popleft()
            self._recent_key_set.discard(old_key)
        return True


def filtered_lines(lines: Iterable[str]) -> list[str]:
    monitor_filter = MonitorBridgeFilter()
    return [line.rstrip("\n") for line in lines if monitor_filter.should_forward(line)]


def filter_stream(source: TextIO, destination: TextIO) -> None:
    monitor_filter = MonitorBridgeFilter()
    for line in source:
        if monitor_filter.should_forward(line):
            destination.write(line.rstrip("\n") + "\n")
            destination.flush()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="monitor-bridge-filter",
        description="filter daemon-event Monitor bridge stdin",
    )
    parser.parse_args(argv)
    filter_stream(sys.stdin, sys.stdout)
    return 0
