#!/usr/bin/env python3
"""Deprecated detached-spawn tombstone.

Use post_banner.py, then run spawn-codex.sh via a harness-tracked Bash
background task. This file intentionally hard-fails before any spawn path.
"""

from __future__ import annotations

import sys


# Refactor (iter3/skill-contract-test-suite):
#   Old pattern: this wrapper could detach codex with banner side effects from an untracked Python entrypoint.
#   New principle: the deprecated path is an executable tombstone; callers must use post_banner.py plus harness-tracked spawn-codex.sh.
def main() -> int:
    sys.stderr.write(
        "FATAL: spawn_with_banner.py is deprecated; use post_banner.py + "
        "harness-tracked spawn-codex.sh\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
