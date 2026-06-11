#!/usr/bin/env python3
"""Behavior tests for narrow controller tick supervisor."""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from codex_refactor_loop.projections import ProjectionRequest, SharedControllerProjection, _FreshnessSource
from codex_refactor_loop.context import LoopContext
from codex_refactor_loop.supervisor import (
    COMMENT_MONITOR_HANDLER_CONTRACT,
    CommentMonitorTickHandler,
    ControllerTickSupervisor,
    FORBIDDEN_CONTRACT_FIELDS,
    LegacyDaemonModeGuard,
    TickHandlerContract,
    TickHandlerResult,
    build_legacy_guard,
)
from codex_refactor_loop.workqueue import FORBIDDEN_PAYLOAD_FIELDS, KeyOnlyWorkQueue, TickWorkItem


@dataclass
class RecordingHandler:
    name: str
    calls: list[str]

    def handle(self, *, item: TickWorkItem, projection: SharedControllerProjection) -> TickHandlerResult:
        self.calls.append(f"{item.handler}:{item.key}:{projection.repo_root}")
        return TickHandlerResult(item.handler, item.key, "handled")


@dataclass
class FixedResultHandler:
    name: str
    status: str
    reason: str

    def handle(self, *, item: TickWorkItem, projection: SharedControllerProjection) -> TickHandlerResult:
        return TickHandlerResult(item.handler, item.key, self.status, self.reason)


def fake_projection(_request: ProjectionRequest) -> SharedControllerProjection:
    return SharedControllerProjection(
        repo_root="/repo",
        generated_at="2026-06-06T00:00:00Z",
        request=ProjectionRequest(),
        managed_work=None,
        daemon_fleet=None,
        statusline={},
        workqueue_keys=(),
    )


def fake_stale_managed_work_projection(_request: ProjectionRequest) -> SharedControllerProjection:
    return _projection_with_freshness(
        _request,
        _FreshnessSource("managed_work_snapshot", True, "cache:stale", 640.0, 0.0, True),
    )


def fake_failed_managed_work_projection(_request: ProjectionRequest) -> SharedControllerProjection:
    return _projection_with_freshness(
        _request,
        _FreshnessSource("managed_work_snapshot", False, "github-error", None, 0.0),
    )


def fake_fresh_managed_work_projection(_request: ProjectionRequest) -> SharedControllerProjection:
    return _projection_with_freshness(
        _request,
        _FreshnessSource("managed_work_snapshot", True, "cache:fresh", 12.0, 288.0),
    )


def _projection_with_freshness(_request: ProjectionRequest, *sources: _FreshnessSource) -> SharedControllerProjection:
    projection = fake_projection(_request)
    return SharedControllerProjection(
        repo_root=projection.repo_root,
        generated_at=projection.generated_at,
        request=projection.request,
        managed_work=projection.managed_work,
        daemon_fleet=projection.daemon_fleet,
        statusline=projection.statusline,
        workqueue_keys=projection.workqueue_keys,
        freshness_sources=sources,
    )


def valid_tick_handler_contract_payload() -> dict[str, object]:
    return {
        "handler": "comment-monitor",
        "required_projection_sources": ["managed_work_snapshot"],
        "delegated_helper": "run_comment_monitor_reconcile_tick",
        "replaced_legacy_daemon_target": "comment-monitor",
        "net_deletion_target": "restart.py daemon target comment-monitor",
    }


class ControllerTickSupervisorTests(unittest.TestCase):
    def test_supervisor_dispatches_named_handlers_only(self) -> None:
        queue = KeyOnlyWorkQueue()
        queue.enqueue("phase9-router", "issue/553")
        queue.enqueue("unknown-handler", "issue/553")
        handler = RecordingHandler("phase9-router", [])

        supervisor = ControllerTickSupervisor(
            handlers=(handler,),
            queue=queue,
            projection_loader=fake_projection,
            legacy_guard=LegacyDaemonModeGuard(supervisor_enabled=True, legacy_daemon_names=()),
        )

        with mock.patch("sys.stderr") as stderr:
            result = supervisor.tick()

        self.assertEqual(("phase9-router:issue/553:/repo",), tuple(handler.calls))
        self.assertEqual(("handled",), tuple(item.status for item in result.processed))
        self.assertEqual(("unknown-handler",), tuple(item.handler for item in result.skipped))
        self.assertEqual(("unknown-handler",), tuple(item.reason for item in result.skipped))
        stderr.write.assert_called_once()
        diagnostic = stderr.write.call_args.args[0]
        self.assertIn("CONTROLLER_TICK_SUPERVISOR_SKIP", diagnostic)
        self.assertIn("handler=unknown-handler", diagnostic)
        self.assertIn("key=issue/553", diagnostic)
        self.assertIn("reason='unknown-handler'", diagnostic)
        self.assertIn("processed=1", diagnostic)
        self.assertIn("skipped=1", diagnostic)
        self.assertIn("drained=2", diagnostic)

    def test_legacy_daemon_mode_guard_prevents_same_target_second_system(self) -> None:
        queue = KeyOnlyWorkQueue()
        queue.enqueue("comment-monitor", "issue/553")
        handler = RecordingHandler("comment-monitor", [])

        supervisor = ControllerTickSupervisor(
            handlers=(handler,),
            queue=queue,
            projection_loader=fake_projection,
            legacy_guard=LegacyDaemonModeGuard(
                supervisor_enabled=True,
                legacy_daemon_names=("comment-monitor",),
            ),
        )

        with mock.patch("sys.stderr") as stderr:
            result = supervisor.tick()

        self.assertEqual([], handler.calls)
        self.assertEqual((), result.processed)
        self.assertIn("conflicts with legacy daemon target=comment-monitor", result.skipped[0].reason)
        stderr.write.assert_called_once()
        diagnostic = stderr.write.call_args.args[0]
        self.assertIn("CONTROLLER_TICK_SUPERVISOR_SKIP", diagnostic)
        self.assertIn("handler=comment-monitor", diagnostic)
        self.assertIn("key=issue/553", diagnostic)
        self.assertIn("conflicts with legacy daemon target=comment-monitor", diagnostic)
        self.assertIn("processed=0", diagnostic)
        self.assertIn("skipped=1", diagnostic)
        self.assertIn("drained=1", diagnostic)

    def test_default_legacy_mode_denies_migrated_handler(self) -> None:
        guard = LegacyDaemonModeGuard(supervisor_enabled=False, legacy_daemon_names=())

        with self.assertRaises(RuntimeError):
            guard.assert_supervisor_allowed("phase9-router")

    def test_legacy_guard_uses_restart_helper_canonical_daemon_names(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controller-tick-guard-") as tmp:
            root = Path(tmp)
            repo = root / "repo"
            skill = root / "skill"
            host_env = repo / ".config" / "consensus-rnd" / "host.env"
            host_env.parent.mkdir(parents=True)
            (skill / "scripts").mkdir(parents=True)
            host_env.write_text(
                f'export REPO_ROOT="{repo}"\n'
                'export GH_REPO_SLUG="example/repo"\n'
                'export CONTROLLER_TICK_SUPERVISOR_ENABLE="true"\n',
                encoding="utf-8",
            )
            ctx = LoopContext.load(
                repo_root=repo,
                skill_root=skill,
                env={"CONSENSUS_RND_HOST_ENV": str(host_env)},
            )

            guard = build_legacy_guard(ctx)

            with self.assertRaisesRegex(RuntimeError, "phase9_router_daemon"):
                guard.assert_supervisor_allowed("phase9-router")

    def test_legacy_guard_uses_supervisor_enabled_restart_inventory_for_migrated_target(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controller-tick-guard-") as tmp:
            root = Path(tmp)
            repo = root / "repo"
            skill = root / "skill"
            host_env = repo / ".config" / "consensus-rnd" / "host.env"
            host_env.parent.mkdir(parents=True)
            (skill / "scripts").mkdir(parents=True)
            host_env.write_text(
                f'export REPO_ROOT="{repo}"\n'
                'export GH_REPO_SLUG="example/repo"\n'
                'export MAINTAINER_WHITELIST="maintainer"\n'
                'export CONTROLLER_TICK_SUPERVISOR_ENABLE="true"\n',
                encoding="utf-8",
            )
            ctx = LoopContext.load(
                repo_root=repo,
                skill_root=skill,
                env={"CONSENSUS_RND_HOST_ENV": str(host_env)},
            )

            guard = build_legacy_guard(ctx)

            guard.assert_supervisor_allowed("comment-monitor")
            with self.assertRaisesRegex(RuntimeError, "phase9_router_daemon"):
                guard.assert_supervisor_allowed("phase9-router")

    def test_supervisor_cannot_dispatch_command_payload_items(self) -> None:
        queue = KeyOnlyWorkQueue()

        for forbidden in sorted(FORBIDDEN_PAYLOAD_FIELDS):
            with self.subTest(forbidden=forbidden):
                with self.assertRaises(ValueError):
                    queue.enqueue_json({"handler": "phase9-router", "key": "issue/553", forbidden: "x"})

        self.assertTrue(queue.empty())

    def test_internal_daemon_entrypoint_uses_empty_key_only_queue(self) -> None:
        queue = KeyOnlyWorkQueue()
        supervisor = ControllerTickSupervisor(
            handlers=(),
            queue=queue,
            projection_loader=fake_projection,
            legacy_guard=LegacyDaemonModeGuard(supervisor_enabled=True, legacy_daemon_names=()),
        )

        result = supervisor.tick()

        self.assertEqual((), result.processed)
        self.assertEqual((), result.skipped)
        self.assertTrue(queue.empty())

    def test_handler_backoff_result_is_returned_as_non_action_skip_with_diagnostic(self) -> None:
        queue = KeyOnlyWorkQueue()
        queue.enqueue("phase9-router", "issue/578")
        handler = FixedResultHandler("phase9-router", "backoff", "projection-stale")

        supervisor = ControllerTickSupervisor(
            handlers=(handler,),
            queue=queue,
            projection_loader=fake_projection,
            legacy_guard=LegacyDaemonModeGuard(supervisor_enabled=True, legacy_daemon_names=()),
        )

        with mock.patch("sys.stderr") as stderr:
            result = supervisor.tick()

        self.assertEqual((), result.processed)
        self.assertEqual(("backoff",), tuple(item.status for item in result.skipped))
        self.assertEqual(("projection-stale",), tuple(item.reason for item in result.skipped))
        diagnostic = stderr.write.call_args.args[0]
        self.assertIn("CONTROLLER_TICK_SUPERVISOR_SKIP", diagnostic)
        self.assertIn("handler=phase9-router", diagnostic)
        self.assertIn("status=backoff", diagnostic)
        self.assertIn("reason='projection-stale'", diagnostic)

    def test_handler_noop_result_is_returned_as_non_action_skip_with_diagnostic(self) -> None:
        queue = KeyOnlyWorkQueue()
        queue.enqueue("comment-monitor", "issue/578")
        handler = FixedResultHandler("comment-monitor", "noop", "nothing-to-do")

        supervisor = ControllerTickSupervisor(
            handlers=(handler,),
            queue=queue,
            projection_loader=fake_projection,
            legacy_guard=LegacyDaemonModeGuard(supervisor_enabled=True, legacy_daemon_names=()),
        )

        with mock.patch("sys.stderr") as stderr:
            result = supervisor.tick()

        self.assertEqual((), result.processed)
        self.assertEqual(("noop",), tuple(item.status for item in result.skipped))
        self.assertEqual(("nothing-to-do",), tuple(item.reason for item in result.skipped))
        diagnostic = stderr.write.call_args.args[0]
        self.assertIn("CONTROLLER_TICK_SUPERVISOR_SKIP", diagnostic)
        self.assertIn("handler=comment-monitor", diagnostic)
        self.assertIn("status=noop", diagnostic)
        self.assertIn("reason='nothing-to-do'", diagnostic)

    def test_tick_handler_contract_rejects_command_and_lifecycle_fields(self) -> None:
        valid = valid_tick_handler_contract_payload()
        for forbidden in sorted(FORBIDDEN_CONTRACT_FIELDS):
            with self.subTest(forbidden=forbidden):
                with self.assertRaisesRegex(ValueError, "cannot carry authority fields"):
                    TickHandlerContract.from_mapping({**valid, forbidden: "x"})

        self.assertEqual(COMMENT_MONITOR_HANDLER_CONTRACT, TickHandlerContract.from_mapping(valid))

    def test_tick_handler_contract_rejects_unexpected_fields(self) -> None:
        payload = {**valid_tick_handler_contract_payload(), "unexpected": "x"}

        with self.assertRaisesRegex(ValueError, "unexpected fields=\\['unexpected'\\]"):
            TickHandlerContract.from_mapping(payload)

    def test_tick_handler_contract_rejects_malformed_required_projection_sources(self) -> None:
        invalid_sources: tuple[object, ...] = (
            "managed_work_snapshot",
            ["managed_work_snapshot", 42],
        )

        for required_projection_sources in invalid_sources:
            with self.subTest(required_projection_sources=required_projection_sources):
                payload = {
                    **valid_tick_handler_contract_payload(),
                    "required_projection_sources": required_projection_sources,
                }

                with self.assertRaisesRegex(ValueError, "requires string required_projection_sources"):
                    TickHandlerContract.from_mapping(payload)

    def test_tick_handler_contract_rejects_missing_identity_fields(self) -> None:
        for identity_field in (
            "handler",
            "delegated_helper",
            "replaced_legacy_daemon_target",
            "net_deletion_target",
        ):
            with self.subTest(identity_field=identity_field):
                payload = valid_tick_handler_contract_payload()
                payload.pop(identity_field)

                with self.assertRaisesRegex(ValueError, "requires non-empty string identity fields"):
                    TickHandlerContract.from_mapping(payload)

    def test_tick_handler_contract_rejects_empty_identity_fields(self) -> None:
        for identity_field in (
            "handler",
            "delegated_helper",
            "replaced_legacy_daemon_target",
            "net_deletion_target",
        ):
            with self.subTest(identity_field=identity_field):
                payload = {**valid_tick_handler_contract_payload(), identity_field: ""}

                with self.assertRaisesRegex(ValueError, "requires non-empty string identity fields"):
                    TickHandlerContract.from_mapping(payload)

    def test_comment_monitor_handler_delegates_existing_tick_for_fresh_projection(self) -> None:
        queue = KeyOnlyWorkQueue()
        queue.enqueue("comment-monitor", "maintainer-comments")
        monitor = mock.Mock()
        handler = CommentMonitorTickHandler(monitor)

        supervisor = ControllerTickSupervisor(
            handlers=(handler,),
            queue=queue,
            projection_loader=fake_fresh_managed_work_projection,
            legacy_guard=LegacyDaemonModeGuard(supervisor_enabled=True, legacy_daemon_names=()),
        )

        result = supervisor.tick()

        monitor.tick.assert_called_once_with()
        self.assertEqual(("handled",), tuple(item.status for item in result.processed))
        self.assertEqual(("run_comment_monitor_reconcile_tick",), tuple(item.reason for item in result.processed))
        self.assertEqual((), result.skipped)

    def test_comment_monitor_handler_backs_off_without_executing_on_stale_projection(self) -> None:
        queue = KeyOnlyWorkQueue()
        queue.enqueue("comment-monitor", "maintainer-comments")
        monitor = mock.Mock()
        handler = CommentMonitorTickHandler(monitor)

        supervisor = ControllerTickSupervisor(
            handlers=(handler,),
            queue=queue,
            projection_loader=fake_stale_managed_work_projection,
            legacy_guard=LegacyDaemonModeGuard(supervisor_enabled=True, legacy_daemon_names=()),
        )

        with mock.patch("sys.stderr") as stderr:
            result = supervisor.tick()

        monitor.tick.assert_not_called()
        self.assertEqual((), result.processed)
        self.assertEqual(("backoff",), tuple(item.status for item in result.skipped))
        self.assertIn("projection-stale:managed_work_snapshot", result.skipped[0].reason)
        diagnostic = stderr.write.call_args.args[0]
        self.assertIn("status=backoff", diagnostic)
        self.assertIn("projection-stale:managed_work_snapshot", diagnostic)

    def test_comment_monitor_handler_blocks_without_executing_when_projection_source_missing(self) -> None:
        queue = KeyOnlyWorkQueue()
        queue.enqueue("comment-monitor", "maintainer-comments")
        monitor = mock.Mock()
        handler = CommentMonitorTickHandler(monitor)

        supervisor = ControllerTickSupervisor(
            handlers=(handler,),
            queue=queue,
            projection_loader=fake_projection,
            legacy_guard=LegacyDaemonModeGuard(supervisor_enabled=True, legacy_daemon_names=()),
        )

        with mock.patch("sys.stderr") as stderr:
            result = supervisor.tick()

        monitor.tick.assert_not_called()
        self.assertEqual((), result.processed)
        self.assertEqual(("blocked",), tuple(item.status for item in result.skipped))
        self.assertEqual(("projection-missing:managed_work_snapshot",), tuple(item.reason for item in result.skipped))
        diagnostic = stderr.write.call_args.args[0]
        self.assertIn("status=blocked", diagnostic)
        self.assertIn("projection-missing:managed_work_snapshot", diagnostic)

    def test_comment_monitor_handler_blocks_without_executing_when_projection_source_failed(self) -> None:
        queue = KeyOnlyWorkQueue()
        queue.enqueue("comment-monitor", "maintainer-comments")
        monitor = mock.Mock()
        handler = CommentMonitorTickHandler(monitor)

        supervisor = ControllerTickSupervisor(
            handlers=(handler,),
            queue=queue,
            projection_loader=fake_failed_managed_work_projection,
            legacy_guard=LegacyDaemonModeGuard(supervisor_enabled=True, legacy_daemon_names=()),
        )

        with mock.patch("sys.stderr") as stderr:
            result = supervisor.tick()

        monitor.tick.assert_not_called()
        self.assertEqual((), result.processed)
        self.assertEqual(("blocked",), tuple(item.status for item in result.skipped))
        self.assertEqual(("projection-failed:managed_work_snapshot:github-error",), tuple(item.reason for item in result.skipped))
        diagnostic = stderr.write.call_args.args[0]
        self.assertIn("status=blocked", diagnostic)
        self.assertIn("projection-failed:managed_work_snapshot:github-error", diagnostic)


if __name__ == "__main__":
    unittest.main()
