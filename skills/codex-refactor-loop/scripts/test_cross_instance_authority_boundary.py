#!/usr/bin/env python3
"""Source-regression tests for cross-instance deny-only admission boundaries."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.controller_actions import ControllerActions
from codex_refactor_loop.cross_instance_stand_down import check_cross_instance_admission


class CrossInstanceAuthorityBoundaryTests(unittest.TestCase):
    def test_stand_down_and_provenance_remain_deny_only_not_lifecycle_authority(self) -> None:
        controller = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        projection = (SCRIPT_DIR / "codex_refactor_loop" / "cross_instance_stand_down.py").read_text(encoding="utf-8")

        self.assertIn("check_cross_instance_admission", projection)
        self.assertIn("CrossInstanceAdmission", projection)
        self.assertIn("return None", controller[controller.index("def _require_item_write_admission_or_return") : controller.index("def _cross_instance_runner")])
        self.assertIn("PUSH_OWNERSHIP_BLOCKED", controller)
        self.assertIn("local_admission_evidence_only_not_durable_claim", controller)
        self.assertIn("branch_pr_author_mismatch", controller)

    def test_stand_down_admission_writes_no_claim_or_lease_artifact(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="cross-instance-boundary-"))
        try:
            (tmp / ".config" / "consensus-rnd").mkdir(parents=True)
            (tmp / ".config" / "consensus-rnd" / "host.env").write_text(
                f'export REPO_ROOT="{tmp}"\nexport GH_REPO_SLUG="owner/repo"\n',
                encoding="utf-8",
            )
            ctx = LoopContext.load(repo_root=tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})

            def runner(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
                if command[:3] == ["gh", "issue", "view"] and "comments" in command:
                    return subprocess.CompletedProcess(command, 0, json.dumps({"comments": []}), "")
                if command[:2] == ["gh", "api"]:
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        json.dumps(
                            [
                                {
                                    "event": "labeled",
                                    "created_at": "2026-06-09T00:59:00Z",
                                    "actor": {"login": "other-user"},
                                    "label": {"name": "crnd:phase:future-not-in-local-catalog"},
                                }
                            ]
                        ),
                        "",
                    )
                raise AssertionError(f"unexpected write or read command: {command}")

            result = check_cross_instance_admission(
                ctx,
                "issue",
                77,
                "current-user",
                datetime(2026, 6, 9, 1, 0, tzinfo=timezone.utc),
                runner=runner,
            )

            self.assertEqual("stand_down", result.status)
            artifact_paths = [path.relative_to(tmp).as_posix() for path in tmp.rglob("*") if path.is_file()]
            self.assertEqual([".config/consensus-rnd/host.env"], artifact_paths)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_branch_provenance_schema_is_local_evidence_not_owner_claim_or_lease(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="cross-instance-provenance-"))
        try:
            (tmp / ".config" / "consensus-rnd").mkdir(parents=True)
            (tmp / ".config" / "consensus-rnd" / "host.env").write_text(
                f'export REPO_ROOT="{tmp}"\nexport GH_REPO_SLUG="owner/repo"\n'
                'export INTEGRATION_BRANCH="integration-branch"\n'
                'export REVIEW_BASE_BRANCH="review-base"\n',
                encoding="utf-8",
            )
            ctx = LoopContext.load(repo_root=tmp, env={"CONSENSUS_RND_HOST_ENV": ".config/consensus-rnd/host.env"})
            actions = ControllerActions(ctx)
            branch = "refactor/iter699-cross-instance"

            actions._write_branch_provenance(branch=branch, worktree=tmp, issue="699", base_sha="abc123")
            payload = json.loads((ctx.paths.state / "branch-provenance" / "refactor__iter699-cross-instance.json").read_text(encoding="utf-8"))

            self.assertEqual(
                {
                    "authority",
                    "base_sha",
                    "branch",
                    "created_at",
                    "github_login",
                    "issue",
                    "owner_device",
                    "worktree",
                },
                set(payload),
            )
            self.assertEqual("local_admission_evidence_only_not_durable_claim", payload["authority"])
            self.assertNotIn("claim", set(payload) - {"authority"})
            for forbidden in ("lease", "lifecycle_authority", "takeover_permit", "per_work_owner", "owner_scope"):
                with self.subTest(forbidden=forbidden):
                    self.assertNotIn(forbidden, payload)
                    self.assertNotIn(forbidden, json.dumps(payload, sort_keys=True))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_release_publication_surface_is_not_cross_instance_gated(self) -> None:
        controller = (SCRIPT_DIR / "codex_refactor_loop" / "controller_actions.py").read_text(encoding="utf-8")
        publish_release = controller[
            controller.index("    def publish_release_candidate") : controller.index("    def post_status_banner")
        ]
        self.assertNotIn("_require_item_write_admission_or_return", publish_release)
        self.assertNotIn("_require_branch_push_admission_or_return", publish_release)


if __name__ == "__main__":
    unittest.main()
