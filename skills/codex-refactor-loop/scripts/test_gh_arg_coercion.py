#!/usr/bin/env python3
"""Regression: ControllerActions.gh must coerce non-str args (e.g. int PR number).

Old pattern: gh() did `["gh", *args]` then `full[3].startswith("-")`, so a caller
passing an int (raw PR number) crashed with AttributeError before any gh process
ran. New principle: gh() coerces every arg to str at the boundary.
"""

from __future__ import annotations

import inspect
import unittest
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent


class GhArgCoercionTests(unittest.TestCase):
    def test_gh_helper_coerces_args_to_str(self) -> None:
        import sys

        sys.path.insert(0, str(SCRIPT_ROOT))
        from codex_refactor_loop.controller_actions import ControllerActions

        src = inspect.getsource(ControllerActions.gh)
        self.assertIn(
            "str(a) for a in args",
            src,
            "gh() must coerce every arg to str so int callers do not crash on full[3].startswith",
        )

    def test_gh_does_not_index_raw_args_before_coercion(self) -> None:
        import sys

        sys.path.insert(0, str(SCRIPT_ROOT))
        from codex_refactor_loop.controller_actions import ControllerActions

        src = inspect.getsource(ControllerActions.gh)
        self.assertNotIn('full = ["gh", *args]', src)


if __name__ == "__main__":
    unittest.main()
