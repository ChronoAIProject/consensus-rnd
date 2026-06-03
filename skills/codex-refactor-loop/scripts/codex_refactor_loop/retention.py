"""Compatibility alias for RuntimeRetention."""

from __future__ import annotations

from typing import Sequence

from .runtime_retention import main as runtime_retention_main


def main(argv: Sequence[str] | None = None) -> int:
    return runtime_retention_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
