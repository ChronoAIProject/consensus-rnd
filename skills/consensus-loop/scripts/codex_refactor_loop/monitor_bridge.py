"""Read-stdin filter for controller daemon-event Monitor bridges."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Sequence
from typing import TextIO


TAIL_HEADER_RE = re.compile(r"^==> .+ <==$")
RUNNER_BLOCKER_RE = re.compile(
    r"\bWAKEUP_RUNNER_BLOCKED:[^\s]*:(?:target_log_exists|target_not_open)(?::|\s|$)"
)
ZERO_STREAK_RE = re.compile(r'(?:"zero_streak"\s*:\s*|\bzero_streak=)(-?\d+)')


def raw_p0_zero_streak(line: str) -> int | None:
    match = ZERO_STREAK_RE.search(line)
    if match is None:
        return None
    return int(match.group(1))


def should_forward_line(line: str) -> bool:
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


def filtered_lines(lines: Iterable[str]) -> list[str]:
    return [line.rstrip("\n") for line in lines if should_forward_line(line)]


def filter_stream(source: TextIO, destination: TextIO) -> None:
    for line in source:
        if should_forward_line(line):
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
