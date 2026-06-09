"""Import-time test isolation from the host's ambient environment.

`unittest discover` imports every ``test_*.py`` during collection before running
any test, so popping the host-injected variables here sanitizes ``os.environ``
for the whole suite. Without this, a daemon-spawned worker that runs the full
``TEST_CMD`` from inside a git worktree inherits the host locator (e.g.
``CONSENSUS_RND_HOST_ENV=.config/consensus-rnd/host.env``, a relative path).
Temp-repo ``LoopContext.load(repo_root=tmp)`` calls then resolve that locator to
``tmp/.config/consensus-rnd/host.env`` — absent in the worktree/temp repo — and
raise a false ``LoopContextError: ... is not a readable file``. That env leak
turned hundreds of passing tests into errors and blocked every implement worker
from reaching ``IMPLEMENT_DONE:...:ok`` (full suite never green -> permanent
``:partial`` -> no PR). The module name sorts first so the sanitization runs
before any other test module's import-time code reads the environment.

This file deliberately mutates the test process environment only; it never
touches host config or production runtime (daemons do not import test modules).
"""

from __future__ import annotations

import os
import unittest

# Host facts are injected by host-owned host.env and must never be inherited by
# the isolated, temp-repo-based test suite. Keep this aligned with the host.env
# surface matrix in SKILL.md.
HOST_INJECTED_ENV_VARS = (
    "CONSENSUS_RND_HOST_ENV",
    "REPO_ROOT",
    "GH_REPO_SLUG",
    "GH_OWNER",
    "GH_REPO",
    "GH_REPO_NAME",
    "BUILD_CMD",
    "TEST_CMD",
    "INTEGRATION_BRANCH",
    "REVIEW_BASE_BRANCH",
    "PROJECT_RULES",
    "RELEASE_AUTO_ENABLE",
    "HOST_GITHUB_RELEASE_REQUIRED_CHECKS",
    "UPDATE_CHECK_ENABLE",
    "UPDATE_CHECK_INTERVAL_SECONDS",
    "UPDATE_CHECK_TIMEOUT_SECONDS",
    "CODEX_FLOOR",
    "STALE_REVIVAL_HOURS",
    "ACTIVE_CONTROLLER_DEVICE_ID",
    "ACTIVE_CONTROLLER_TTL_SECONDS",
    "HOST_REFACTOR_COMMENT_POLICY",
    "CI_GUARDS",
    "HOST_WORKFLOW_SPEC",
    "SOURCE_GLOBS",
    "MAINTAINER_WHITELIST",
    "COMMENT_MONITOR_INTERVAL",
    "COMMENT_MONITOR_LOOKBACK",
    "RELEASE_AUTO_MIN_MERGES",
    "RELEASE_AUTO_MIN_INTERVAL_HOURS",
    "RELEASE_ROLLUP_MIN_COMMITS",
    "RELEASE_ROLLUP_COOLDOWN_SECONDS",
    "HOST_TEST_FILE_GLOBS",
    "HOST_TEST_NAMING_RULE",
    "HOST_COMMENT_RULE",
    "HOST_CODE_FENCE_LANG",
    "HOST_PROTO_POLICY",
    "HOST_ARCHITECTURE_GREP_CHECKS",
)


def sanitize_host_env() -> list[str]:
    removed = [name for name in HOST_INJECTED_ENV_VARS if name in os.environ]
    for name in removed:
        os.environ.pop(name, None)
    return removed


# Run at import time (collection phase), before any test method executes.
_REMOVED_AT_IMPORT = sanitize_host_env()


class HostEnvIsolationTests(unittest.TestCase):
    def test_host_injected_env_is_not_inherited_during_tests(self) -> None:
        for name in HOST_INJECTED_ENV_VARS:
            with self.subTest(var=name):
                self.assertNotIn(name, os.environ, f"{name} leaked into the test process")

    def test_locator_is_in_the_sanitized_set(self) -> None:
        self.assertIn("CONSENSUS_RND_HOST_ENV", HOST_INJECTED_ENV_VARS)
