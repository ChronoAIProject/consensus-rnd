"""Codex progress reporter daemon."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ..active_controller import require_active_controller, write_active_controller_status
from ..context import LoopContext, LoopContextError
from ..github_budget import graphql_headroom_ok
from ..heartbeat import DaemonHeartbeatLease
from ..holistic_status import collect as collect_holistic_status
from ..holistic_status import render_markdown as render_holistic_markdown
from ..secondary_mutation_backoff import (
    currently_backing_off,
    record_backoff_from_gh_output,
    record_content_creation_backoff,
)


AI_SENTINEL = "⟦AI:AUTO-LOOP⟧"


def exit_status(log: Path) -> str:
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-5:]
    except OSError:
        return "in_flight"
    for line in reversed(lines):
        match = re.fullmatch(r"EXIT=([0-9]+)", line)
        if match:
            return "exit_ok" if match.group(1) == "0" else "exit_failed"
    return "in_flight"


def hash_body(body: str) -> str:
    return hashlib.md5(body.encode("utf-8")).hexdigest()


class ProgressReporter:
    def __init__(self, ctx: LoopContext, *, interval: int | None = None) -> None:
        self.ctx = ctx
        self.repo = ctx.gh_repo_slug
        if not self.repo:
            raise RuntimeError("FATAL: GH_REPO_SLUG is unset and gh repo view failed")
        self.interval = int(interval or 600)
        self.state_dir = ctx.paths.refactor_loop
        self.state_file = self.state_dir / "codex-progress-state.json"
        self.log_dir = self.state_dir / "logs"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if not self.state_file.exists():
            self.state_file.write_text("{}\n", encoding="utf-8")
        self.heartbeat = DaemonHeartbeatLease("codex-progress-reporter", ctx.repo_root, singleton=True)

    def run_forever(self) -> int:
        with self.heartbeat.daemon_lifetime():
            self.heartbeat.beat()
            while True:
                self.log_msg("tick")
                self.heartbeat.run_tick(lambda: run_progress_reporter_reconcile_tick(self))
                self.heartbeat.sleep_with_lease(self.interval)

    def tick(self) -> None:
        if not graphql_headroom_ok(cwd=self.ctx.repo_root, env=self.ctx.env_for_subprocess()):
            self.log_tick_status("skip:graphql-backoff remaining=unknown")
            return
        backoff = currently_backing_off(self.ctx.paths.state)
        if backoff.active:
            self.log_tick_status(
                "skip:secondary-mutation-backoff "
                f"mutation={backoff.mutation or 'unknown'} until_epoch={int(backoff.until_epoch)}"
            )
            return
        for log in sorted(self.log_dir.glob("*.log")):
            base = log.stem
            if base.startswith("audit-iter-") or base.startswith("remote-ci-"):
                continue
            self.record_worker_log_status(base, log)
        self.sync_global_status_card()

    def sync_global_status_card(self) -> None:
        config = self._global_status_config()
        if not config["enabled"]:
            self.log_msg(f"global-dashboard-status-card=noop:{config['reason']}")
            return
        decision = require_active_controller(self.ctx, "global-dashboard-status-card")
        write_active_controller_status(self.ctx, decision)
        if not decision.allowed:
            self.log_msg(f"global-dashboard-status-card=noop:not-owner owner={decision.owner_device}")
            return
        state = self._state()
        key = "__global_dashboard_status_card__"
        item = state.get(key, {}) if isinstance(state.get(key), dict) else {}
        now = int(time.time())
        interval = int(config["interval_seconds"])
        last_synced_at = _safe_int(item.get("last_synced_at"))
        if last_synced_at is not None and now - last_synced_at < interval:
            self.log_msg("global-dashboard-status-card=noop:interval")
            return
        body = self.build_global_status_body()
        cur_md5 = hash_body(body)
        if cur_md5 == str(item.get("last_md5") or ""):
            self._state_set_global_status(
                key,
                target=str(config["issue_number"]),
                cid=str(config["comment_id"]),
                md5=cur_md5,
                synced_at=now,
            )
            self.log_msg("global-dashboard-status-card=noop:same-hash")
            return
        with temp_body_file(body) as body_file:
            patch = self.gh_api(
                ["-X", "PATCH", f"repos/{self.repo}/issues/comments/{config['comment_id']}", "-F", f"body=@{body_file}"],
                check=False,
            )
        if _valid_fixed_comment_patch(patch, int(config["comment_id"])):
            self._state_set_global_status(
                key,
                target=str(config["issue_number"]),
                cid=str(config["comment_id"]),
                md5=cur_md5,
                synced_at=now,
            )
            self.log_msg(
                "global-dashboard-status-card=patched "
                f"issue=#{config['issue_number']} comment_id={config['comment_id']}"
            )
        else:
            recorded = record_backoff_from_gh_output(
                self.ctx.paths.state,
                patch.stdout,
                patch.stderr,
                env=self.ctx.env_for_subprocess(),
            )
            if recorded is not None:
                self.log_msg(
                    "global-dashboard-status-card=secondary-backoff "
                    f"comment_id={config['comment_id']} mutation={recorded.mutation} until_epoch={int(recorded.until_epoch)}"
                )
                return
            record_content_creation_backoff(self.ctx, "global-dashboard-status-card", patch)
            self.log_msg(f"global-dashboard-status-card=FAIL patch comment_id={config['comment_id']}")

    def build_global_status_body(self) -> str:
        markdown = render_holistic_markdown(collect_holistic_status(self.ctx)).rstrip()
        return f"{markdown}\n\n🤖 controller global dashboard status card\n\n{AI_SENTINEL}\n"

    def _global_status_config(self) -> dict[str, object]:
        env = self.ctx.env_for_subprocess()
        enabled = _truthy(env.get("HOST_HOLISTIC_STATUS_ENABLE", "false"))
        issue_number = str(env.get("HOST_HOLISTIC_STATUS_ISSUE_NUMBER", "")).strip()
        comment_id = str(env.get("HOST_HOLISTIC_STATUS_COMMENT_ID", "")).strip()
        interval = _safe_int(env.get("HOST_HOLISTIC_STATUS_INTERVAL_SECONDS"))
        if interval is None or interval <= 0:
            interval = 600
        if not enabled:
            return {"enabled": False, "reason": "disabled", "interval_seconds": interval}
        if not issue_number or not issue_number.isdigit():
            return {"enabled": False, "reason": "missing-issue-number", "interval_seconds": interval}
        if not comment_id or not comment_id.isdigit():
            return {"enabled": False, "reason": "missing-comment-id", "interval_seconds": interval}
        return {
            "enabled": True,
            "reason": "enabled",
            "issue_number": int(issue_number),
            "comment_id": int(comment_id),
            "interval_seconds": interval,
        }

    def is_zombie(self, log: Path) -> bool:
        if exit_status(log) == "exit_ok":
            return False
        try:
            return time.time() - log.stat().st_mtime > 1800
        except OSError:
            return False

    def record_worker_log_status(self, base: str, log: Path) -> None:
        status = exit_status(log)
        if status == "in_flight" and self.is_zombie(log):
            self.log_msg(f"skip zombie log {base} (no EXIT, mtime > 30 min)")
            return
        self.log_msg(f"worker-log-status {base}: {status}")

    def _state(self) -> dict[str, object]:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        return data if isinstance(data, dict) else {}

    def _state_set_global_status(self, key: str, *, target: str, cid: str, md5: str, synced_at: int) -> None:
        state = self._state()
        state[key] = {
            "target": target,
            "kind": "issue-comment",
            "comment_id": cid,
            "last_md5": md5,
            "last_synced_at": synced_at,
        }
        tmp = self.state_file.with_name(f".{self.state_file.name}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_file)

    def gh_api(self, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return _run(["gh", "api", *args], self.ctx.repo_root, check=check)

    @staticmethod
    def log_msg(message: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"[{ts}] {message}", file=sys.stderr)

    @staticmethod
    def log_tick_status(action: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"[{ts}] progress-reporter: tick {action}", flush=True)


def run_progress_reporter_reconcile_tick(reporter: ProgressReporter) -> None:
    reporter.tick()


@contextmanager
def temp_body_file(body: str):
    import tempfile

    fd, name = tempfile.mkstemp(prefix=f"codex-progress-{os.getpid()}-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        yield Path(name)
    finally:
        Path(name).unlink(missing_ok=True)


def _run(command: Sequence[str], cwd: Path, *, check: bool) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"{command[0]} failed")
    return result


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _safe_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _valid_fixed_comment_patch(result: subprocess.CompletedProcess[str], expected_comment_id: int) -> bool:
    if result.returncode != 0 or not result.stdout.strip():
        return False
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    try:
        comment_id = int(str(payload.get("id") or ""))
    except ValueError:
        return False
    if comment_id != expected_comment_id:
        return False
    url = str(payload.get("html_url") or "")
    return not url or f"issuecomment-{expected_comment_id}" in url


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run codex progress reporter")
    parser.add_argument("--exit-status")
    parser.add_argument("--hash-body", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--daemon", action="store_true", help="run persistently")
    mode.add_argument("--once", action="store_true", help="run one tick and exit")
    args = parser.parse_args(argv)
    if args.exit_status:
        print(exit_status(Path(args.exit_status)))
        return 0
    if args.hash_body:
        print(hash_body(sys.stdin.read()))
        return 0
    try:
        ctx = LoopContext.load(cwd=os.getcwd())
        reporter = ProgressReporter(ctx)
    except (LoopContextError, RuntimeError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    if args.once or os.environ.get("TEST_NO_LOOP") == "1":
        run_progress_reporter_reconcile_tick(reporter)
        return 0
    return reporter.run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
