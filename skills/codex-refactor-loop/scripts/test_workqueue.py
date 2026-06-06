#!/usr/bin/env python3
"""Behavior tests for key-only controller tick workqueue."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.workqueue import KeyOnlyWorkQueue, TickWorkItem


class KeyOnlyWorkQueueTests(unittest.TestCase):
    def test_queue_accepts_only_handler_and_key(self) -> None:
        item = TickWorkItem.from_json({"handler": "phase9-router", "key": "issue/553"})
        self.assertEqual({"handler": "phase9-router", "key": "issue/553"}, item.to_json())

        for forbidden in ("argv", "shell", "env", "git", "gh", "executor", "lifecycle_authority"):
            with self.subTest(forbidden=forbidden):
                with self.assertRaises(ValueError):
                    TickWorkItem.from_json({"handler": "phase9-router", "key": "issue/553", forbidden: "x"})

        with self.assertRaises(ValueError):
            TickWorkItem.from_json({"handler": "phase9-router", "key": "issue/553", "reason": "payload"})

    def test_queue_is_fifo_and_deduplicates_by_handler_key(self) -> None:
        queue = KeyOnlyWorkQueue()

        self.assertTrue(queue.enqueue("phase9-router", "issue/553"))
        self.assertFalse(queue.enqueue("phase9-router", "issue/553"))
        self.assertTrue(queue.enqueue("comment-monitor", "issue/553"))

        self.assertEqual(("phase9-router:issue/553", "comment-monitor:issue/553"), queue.keys())
        first = queue.dequeue()
        self.assertEqual(("phase9-router", "issue/553"), (first.handler, first.key))

    def test_invalid_key_tokens_fail_closed(self) -> None:
        queue = KeyOnlyWorkQueue()
        for value in ("", "bad key", "../escape", "x" * 200):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    queue.enqueue("phase9-router", value)


if __name__ == "__main__":
    unittest.main()
