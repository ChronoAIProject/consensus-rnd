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
from typing import Mapping, Sequence

from ..active_controller import require_active_controller, write_active_controller_status
from ..context import LoopContext, LoopContextError
from ..github_budget import graphql_headroom_ok
from ..heartbeat import DaemonHeartbeatLease
from ..holistic_status import collect as collect_holistic_status
from ..holistic_status import render_markdown as render_holistic_markdown


AI_SENTINEL = "⟦AI:AUTO-LOOP⟧"
PROGRESS_COMMENT_MUTATION_BUDGET_PER_TICK = 4
PROGRESS_REVIEW_TARGET_RE = re.compile(r"^review-pr([0-9]+)-[A-Za-z][A-Za-z0-9_-]*-r[0-9]+$")
PROGRESS_FIX_TARGET_RE = re.compile(r"^fix-pr([0-9]+)-(?:r[0-9]+|round[0-9]+|round-[0-9]+)(?:-[A-Za-z0-9._-]+)?$")
PROGRESS_PHASE9_TARGET_RE = re.compile(r"^phase9-issue([0-9]+)-r[0-9]+-(?:minimal|structural|delete|judge|reflector)$")


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


def extract_tail(log: Path) -> str:
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-30:-5] if len(lines) >= 30 else lines[:25])


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
        self.prompts_dir = self.state_dir / "prompts"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if not self.state_file.exists():
            self.state_file.write_text("{}\n", encoding="utf-8")
        self.heartbeat = DaemonHeartbeatLease("codex-progress-reporter", ctx.repo_root)

    def run_forever(self) -> int:
        while True:
            self.log_msg("tick")
            self.tick()
            self.heartbeat.beat()
            self.heartbeat.sleep_with_lease(self.interval)

    def tick(self) -> None:
        if not graphql_headroom_ok(cwd=self.ctx.repo_root, env=self.ctx.env_for_subprocess()):
            self.log_tick_status("skip:graphql-backoff remaining=unknown")
            return
        remaining_mutations = PROGRESS_COMMENT_MUTATION_BUDGET_PER_TICK
        mutation_backoff = False
        for log in sorted(self.log_dir.glob("*.log")):
            if remaining_mutations <= 0:
                self.log_tick_status("skip:comment-mutation-budget-exhausted")
                break
            base = log.stem
            if base.startswith("audit-iter-") or base.startswith("remote-ci-"):
                continue
            result = self.post_or_update(base, log)
            if result in {"mutated", "mutation_failed"}:
                remaining_mutations -= 1
            if result == "mutation_failed":
                mutation_backoff = True
                self.log_tick_status(f"skip:comment-mutation-failure-backoff base={base}")
                break
        if remaining_mutations > 0 and not mutation_backoff:
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
        if patch.returncode == 0:
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

    def parse_target(self, base: str) -> str:
        for pattern in (PROGRESS_REVIEW_TARGET_RE, PROGRESS_FIX_TARGET_RE, PROGRESS_PHASE9_TARGET_RE):
            match = pattern.fullmatch(base)
            if match:
                return match.group(1)
        prompt = self.prompts_dir / f"{base}.md"
        if prompt.is_file():
            matches = re.findall(r"#[0-9]+", prompt.read_text(encoding="utf-8", errors="replace"))
            if matches:
                return matches[-1].lstrip("#")
        return ""

    def parse_kind(self, target: str) -> str:
        result = self.gh_api([f"repos/{self.repo}/issues/{target}"], check=False)
        if result.returncode != 0 or not result.stdout.strip():
            return "issue"
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return "issue"
        if isinstance(payload, dict) and isinstance(payload.get("pull_request"), dict):
            return "pr"
        return "issue"

    def is_zombie(self, log: Path) -> bool:
        if exit_status(log) == "exit_ok":
            return False
        try:
            return time.time() - log.stat().st_mtime > 1800
        except OSError:
            return False

    def build_body(self, base: str, log: Path, finished: str) -> str:
        try:
            elapsed_min = int((time.time() - log.stat().st_ctime) / 60)
        except OSError:
            elapsed_min = 0
        if finished == "failed":
            status_line = f"❌ 失败; 已跑 {elapsed_min} min"
            details = f"""异常诊断 tail (non-zero EXIT only):

```
{extract_tail(log)}
```"""
            delete_note = "codex 已非零退出;保留此 comment 直到 controller 处理失败。"
        else:
            status_line = f"⏳ 进行中; 已跑 {elapsed_min} min"
            details = f"""Task id: `{base}`
Log: `{log.name}`
Raw log tail is intentionally omitted while the worker is still running."""
            delete_note = "自动更新每 10 分钟;edit-in-place 不堆评论;codex EXIT=0 后此 comment 自动删除。"
        body = f"""## 📊 codex 进展 {base} ({status_line})

{details}

> {delete_note}
🤖 controller progress reporter

{AI_SENTINEL}
"""
        return body

    def post_or_update(self, base: str, log: Path) -> str:
        decision = require_active_controller(self.ctx, "progress-reporter-write")
        write_active_controller_status(self.ctx, decision)
        if not decision.allowed:
            self.log_msg(f"active_controller=noop:not-owner progress-reporter {base} owner={decision.owner_device}")
            return "noop"
        state = self._state()
        item = state.get(base, {}) if isinstance(state.get(base), dict) else {}
        target = str(item.get("target") or "")
        if not target:
            target = self.parse_target(base)
            if not target:
                self.log_msg(f"skip {base}: no target")
                return "noop"
            kind = self.parse_kind(target)
            cid: str | int | None = None
            prev_md5 = ""
        else:
            kind = str(item.get("kind") or "issue")
            cid = item.get("comment_id")
            prev_md5 = str(item.get("last_md5") or "")
        status = exit_status(log)
        finished = "true" if status == "exit_ok" else "failed" if status == "exit_failed" else "false"
        if finished == "false" and self.is_zombie(log):
            self.log_msg(f"skip zombie log {base} (no EXIT, mtime > 30 min)")
            return "noop"
        prev_finished = str(item.get("finished") or "")
        needs_delete_retry = finished == "true" and cid not in (None, "", "null", 0, "0")
        if finished == prev_finished and not needs_delete_retry and finished in ("true", "failed"):
            return "noop"
        if finished == "true" and cid not in (None, "", "null", 0, "0"):
            delete = self.gh_api(["-X", "DELETE", f"repos/{self.repo}/issues/comments/{cid}"], check=False)
            if delete.returncode == 0:
                self.log_msg(f"deleted progress comment for {base} (finished, cid={cid} was={kind} #{target})")
                self._state_set(base, target, kind, 0, "deleted", "true")
                return "mutated"
            else:
                exists = self.gh_api([f"repos/{self.repo}/issues/comments/{cid}"], check=False)
                if exists.returncode != 0:
                    self.log_msg(f"comment {cid} for {base} already 404; marking finished")
                    self._state_set(base, target, kind, 0, "gone", "true")
                    return "mutation_failed"
                else:
                    self.log_msg(f"FAIL delete comment {cid} for {base}; comment still exists, retry next tick")
                    return "mutation_failed"
        body = self.build_body(base, log, finished)
        cur_md5 = hash_body(body)
        if cur_md5 == prev_md5 and finished == prev_finished:
            return "noop"
        with temp_body_file(body) as body_file:
            if cid in (None, "", "null"):
                url = self._create_comment(kind, target, body_file)
                new_cid = _comment_id(url)
                if new_cid:
                    if not url_for_kind(url, kind):
                        kind = "issue" if kind == "pr" else "pr"
                    self._state_set(base, target, kind, int(new_cid), cur_md5, finished)
                    self.log_msg(f"created progress comment for {base} → {kind} #{target} (cid={new_cid})")
                    return "mutated"
                else:
                    self.log_msg(f"FAIL to create comment for {base} → {kind} #{target}")
                    return "mutation_failed"
            else:
                patch = self.gh_api(["-X", "PATCH", f"repos/{self.repo}/issues/comments/{cid}", "-F", f"body=@{body_file}"], check=False)
                if patch.returncode == 0:
                    self._state_set(base, target, kind, cid, cur_md5, finished)
                    self.log_msg(f"edited progress comment for {base} (cid={cid}, finished={finished})")
                    return "mutated"
                else:
                    self.log_msg(f"FAIL to edit comment {cid} for {base}; will retry next tick")
                    return "mutation_failed"
        return "noop"

    def _create_comment(self, kind: str, target: str, body_file: Path) -> str:
        first = self.gh([kind, "comment", target, "--body-file", str(body_file)], check=False)
        if first.returncode == 0 and first.stdout.strip():
            return first.stdout.strip().splitlines()[-1]
        other_kind = "issue" if kind == "pr" else "pr"
        second = self.gh([other_kind, "comment", target, "--body-file", str(body_file)], check=False)
        return second.stdout.strip().splitlines()[-1] if second.returncode == 0 and second.stdout.strip() else ""

    def _state(self) -> dict[str, object]:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        return data if isinstance(data, dict) else {}

    def _state_set(self, key: str, target: str, kind: str, cid: object, md5: str, finished: str) -> None:
        state = self._state()
        state[key] = {
            "target": target,
            "kind": kind,
            "comment_id": cid,
            "last_md5": md5,
            "finished": finished,
        }
        tmp = self.state_file.with_name(f".{self.state_file.name}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_file)

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

    def gh(self, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return _run(["gh", *args, "--repo", self.repo], self.ctx.repo_root, check=check)

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


def _comment_id(url: str) -> str:
    match = re.search(r"issuecomment-([0-9]+)", url)
    return match.group(1) if match else ""


def url_for_kind(url: str, kind: str) -> bool:
    return f"/{'pull' if kind == 'pr' else 'issues'}/" in url


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _safe_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


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
        reporter.tick()
        return 0
    return reporter.run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
