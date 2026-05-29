#!/usr/bin/env python3
"""Compatibility entrypoint for the package wakeup-plan command."""

from __future__ import annotations

from codex_refactor_loop.wakeup_plan import main


if __name__ == "__main__":
    raise SystemExit(main())
