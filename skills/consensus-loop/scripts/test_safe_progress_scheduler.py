#!/usr/bin/env python3
"""Behavior tests for safe progress risk admission."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.safe_progress_scheduler import (
    classify_dispatch_payload,
    classify_wakeup_action,
    project_wakeup_actions,
    validate_runner_action,
    write_blocked_queue,
)


class SafeProgressSchedulerTests(unittest.TestCase):
    def test_legacy_spawn_action_without_declared_risk_is_low_auto(self) -> None:
        action = {
            "kind": "harness-spawn-intent",
            "action_id": "spawn:1",
            "controller_action": "spawn_codex_harness_background",
            "no_lifecycle_authority": True,
        }

        decision = classify_wakeup_action(action)

        self.assertEqual("low", decision.risk_tier)
        self.assertEqual("auto", decision.execution_policy)
        self.assertTrue(decision.executable)

    def test_known_non_spawn_action_is_medium_cautious(self) -> None:
        action = {
            "kind": "completed-marker",
            "action_id": "completed:1",
            "controller_action": "publish_implementation_output",
            "no_lifecycle_authority": True,
        }

        decision = classify_wakeup_action(action)

        self.assertEqual("medium", decision.risk_tier)
        self.assertEqual("cautious", decision.execution_policy)
        self.assertTrue(decision.executable)

    def test_forbidden_nested_field_is_high_blocked(self) -> None:
        action = {
            "kind": "completed-marker",
            "action_id": "unsafe:1",
            "controller_action": "publish_implementation_output",
            "target": {"kind": "issue", "number": 1, "gh": "issue close 1"},
            "no_lifecycle_authority": True,
        }

        decision = classify_wakeup_action(action)

        self.assertEqual("high", decision.risk_tier)
        self.assertEqual("blocked", decision.execution_policy)
        self.assertFalse(decision.executable)
        self.assertIn("target.gh", decision.forbidden_fields)
        self.assertIsNotNone(decision.blocked_item)

    def test_projection_removes_high_actions_and_keeps_low_medium_executable(self) -> None:
        projection = project_wakeup_actions(
            [
                {
                    "kind": "harness-spawn-intent",
                    "action_id": "low",
                    "controller_action": "spawn_codex_harness_background",
                    "no_lifecycle_authority": True,
                },
                {
                    "kind": "completed-marker",
                    "action_id": "medium",
                    "controller_action": "review_gate",
                    "no_lifecycle_authority": True,
                },
                {
                    "kind": "completed-marker",
                    "action_id": "high",
                    "controller_action": "review_gate",
                    "argv": ["gh", "pr", "merge"],
                    "no_lifecycle_authority": True,
                },
            ],
            now="2026-06-07T00:00:00Z",
        )

        self.assertEqual(["low", "medium"], [action["action_id"] for action in projection.actions])
        self.assertEqual("auto", projection.actions[0]["execution_policy"])
        self.assertEqual("cautious", projection.actions[1]["execution_policy"])
        self.assertEqual(1, len(projection.blocked_queue))
        self.assertEqual("high", projection.blocked_queue[0]["action_id"])
        self.assertTrue(projection.blocked_queue[0]["no_lifecycle_authority"])

    def test_dispatch_payload_classifies_legacy_mutable_as_medium_and_forbidden_as_high(self) -> None:
        medium = classify_dispatch_payload({"cd": "/repo/.worktrees/fix-pr1", "prompt": "/p", "log": "/l"}, task_id="fix-pr1-round-1")
        high = classify_dispatch_payload({"cd": "/repo/.worktrees/x", "prompt": "/p", "log": "/l", "gh": "issue close"}, task_id="surprise")

        self.assertEqual("medium", medium.risk_tier)
        self.assertEqual("cautious", medium.execution_policy)
        self.assertEqual("high", high.risk_tier)
        self.assertFalse(high.executable)

    def test_runner_validation_requires_cautious_for_medium(self) -> None:
        action = {
            "kind": "completed-marker",
            "action_id": "medium",
            "controller_action": "review_gate",
            "risk_tier": "medium",
            "execution_policy": "auto",
            "no_lifecycle_authority": True,
        }

        self.assertEqual("medium_requires_cautious_execution_policy", validate_runner_action(action))

    def test_write_blocked_queue_uses_fixed_skill_private_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            path = write_blocked_queue(
                repo,
                [
                    {
                        "schema": "safe-progress-blocked-queue-item",
                        "work_unit_id": "issue-1",
                        "source": "test",
                        "risk_tier": "high",
                        "blocker_reason": "forbidden_fields:argv",
                        "unblock_evidence_required": "metadata change",
                        "last_evaluated_at": "2026-06-07T00:00:00Z",
                        "next_eligible_evaluation_condition": "next tick",
                        "action_id": "high",
                        "target": {"kind": "issue", "number": 1},
                        "controller_action": "review_gate",
                        "no_lifecycle_authority": True,
                    }
                ],
                now="2026-06-07T00:00:00Z",
            )

            self.assertEqual(repo / ".refactor-loop/state/safe-progress-blocked-queue.json", path)
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("safe-progress-blocked-queue", document["schema"])
            self.assertEqual("safe_progress_scheduler", document["owner"])
            self.assertEqual(1, document["counters"]["high"])
            self.assertTrue(document["no_lifecycle_authority"])


if __name__ == "__main__":
    unittest.main()
