"""Test-owned data-only inventory for restart-managed controller daemons."""

from __future__ import annotations


CONTROLLER_RUNTIME_INVENTORY = (
    {
        "daemon_name": "concurrency_monitor",
        "owner_module": "codex_refactor_loop.monitors.concurrency",
        "cli_command": "concurrency --daemon",
        "tick_import_path": "codex_refactor_loop.monitors.concurrency.run_concurrency_reconcile_tick",
        "authority_note": "observability and dispatch-queue worker spawn-intent projection only",
        "test_owner": "skills/codex-refactor-loop/scripts/test_concurrency_monitor.py",
    },
    {
        "daemon_name": "comment-monitor",
        "owner_module": "codex_refactor_loop.monitors.comment",
        "cli_command": "comment-monitor --daemon",
        "tick_import_path": "codex_refactor_loop.monitors.comment.run_comment_monitor_reconcile_tick",
        "authority_note": "maintainer comment observation plus owner-gated acknowledgement banner",
        "test_owner": "skills/codex-refactor-loop/scripts/test_comment_monitor.py",
    },
    {
        "daemon_name": "codex-progress-reporter",
        "owner_module": "codex_refactor_loop.monitors.progress",
        "cli_command": "progress-reporter --daemon",
        "tick_import_path": "codex_refactor_loop.monitors.progress.run_progress_reporter_reconcile_tick",
        "authority_note": "worker log observation and configured global status comment patch",
        "test_owner": "skills/codex-refactor-loop/scripts/test_progress_reporter.py",
    },
    {
        "daemon_name": "dev_sync_daemon",
        "owner_module": "codex_refactor_loop.sync.dev",
        "cli_command": "dev-sync --daemon",
        "tick_import_path": "codex_refactor_loop.sync.dev.run_dev_sync_reconcile_tick",
        "authority_note": "narrow integration sync detector and #53 operation artifact executor",
        "test_owner": "skills/codex-refactor-loop/scripts/test_sync_dev.py",
    },
    {
        "daemon_name": "phase9_router_daemon",
        "owner_module": "codex_refactor_loop.phase9.router",
        "cli_command": "phase9-router --daemon",
        "tick_import_path": "codex_refactor_loop.phase9.router.run_phase9_router_reconcile_tick",
        "authority_note": "deterministic design-consensus route projection",
        "test_owner": "skills/codex-refactor-loop/scripts/test_phase9_router_daemon.py",
    },
    {
        "daemon_name": "closed_label_reconciler",
        "owner_module": "codex_refactor_loop.closed_label_reconciler",
        "cli_command": "closed-label-reconciler --daemon",
        "tick_import_path": "codex_refactor_loop.closed_label_reconciler.run_closed_label_reconciler_reconcile_tick",
        "authority_note": "closed managed item terminal phase-label reconciliation only",
        "test_owner": "skills/codex-refactor-loop/scripts/test_closed_label_reconciler.py",
    },
    {
        "daemon_name": "wakeup_runner_daemon",
        "owner_module": "codex_refactor_loop.wakeup_runner",
        "cli_command": "wakeup-runner --daemon",
        "tick_import_path": "codex_refactor_loop.wakeup_runner.run_wakeup_runner_reconcile_tick",
        "authority_note": "mechanical apply of evidence-bound closed wakeup-plan actions",
        "test_owner": "skills/codex-refactor-loop/scripts/test_wakeup_runner.py",
    },
)


__all__ = ("CONTROLLER_RUNTIME_INVENTORY",)
