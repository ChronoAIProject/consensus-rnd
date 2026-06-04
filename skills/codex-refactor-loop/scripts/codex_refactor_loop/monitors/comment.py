"""Maintainer comment monitor daemon."""

from __future__ import annotations

import json
import os
import argparse
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ..active_controller import require_active_controller, write_active_controller_status
from ..context import LoopContext, LoopContextError
from ..heartbeat import DaemonHeartbeatLease
from ..managed_work_snapshot import load_open_managed_work_snapshot


AI_SENTINEL = "⟦AI:AUTO-LOOP⟧"
ITEM_UPDATED_STATE_KEY = "_item_updated"
CONTROLLER_PREFIXES = (
    "## 🤖",
    "## 📊",
    "## 📢",
    "## 📎",
    "## ✅",
    "## 🆘",
    "## 🎉",
    "## 🔄",
    "## ⏸️",
    "## 🔍",
    "## 🛠️",
    "## 🚀",
    "## 👀",
    "## 🔧",
    "## ⚙️",
    "## Phase ",
    "## Studio ",
    "## Workflow ",
    "## iter",
)


class CommentMonitor:
    def __init__(self, ctx: LoopContext, *, state_file: Path | None = None, interval: int | None = None) -> None:
        self.ctx = ctx
        self.repo = ctx.gh_repo_slug
        if not self.repo:
            raise RuntimeError("FATAL: GH_REPO_SLUG is unset and gh repo view failed")
        whitelist = ctx.host_env.get("MAINTAINER_WHITELIST")
        if not whitelist:
            raise RuntimeError("FATAL: MAINTAINER_WHITELIST is unset; comment-monitor fails closed")
        self.maintainers = {item for item in whitelist.replace(",", " ").split() if item}
        self.state_file = state_file or ctx.paths.refactor_loop / "comment-monitor-state.json"
        self.interval = int(interval or os.environ.get("COMMENT_MONITOR_INTERVAL") or "30")
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.state_file.exists():
            self.state_file.write_text("{}\n", encoding="utf-8")
        self.heartbeat = DaemonHeartbeatLease("comment-monitor", ctx.repo_root)

    def run_forever(self) -> int:
        while True:
            self.tick()
            self.heartbeat.beat()
            self.heartbeat.sleep_with_lease(self.interval)

    def tick(self) -> None:
        self._poll_once()

    def _poll_once(self) -> None:
        for number, updated_at in self._search_active().items():
            if not self._should_fetch_comments(number, updated_at):
                continue
            ok, comments = self._comments_with_status(number)
            for comment in comments:
                self.handle_comment(number, comment)
            if ok:
                self.mark_item_updated(number, updated_at)

    def _search_active(self) -> dict[str, str]:
        active: dict[str, str] = {}
        lookback = _lookback_minimum_updated_at()
        snapshot = load_open_managed_work_snapshot(self.ctx)
        if not snapshot.loaded_ok:
            return active
        for row in snapshot.items:
            number = str(row.get("number") or "")
            updated_at = str(row.get("updated_at") or "")
            if not number or not updated_at:
                continue
            if lookback and updated_at < lookback:
                continue
            if number not in active or updated_at > active[number]:
                active[number] = updated_at
        return dict(sorted(active.items(), key=lambda item: int(item[0]) if item[0].isdigit() else item[0]))

    def targets(self) -> list[str]:
        numbers: set[str] = set()
        snapshot = load_open_managed_work_snapshot(self.ctx)
        if not snapshot.loaded_ok:
            return []
        for item in snapshot.items:
            number = str(item.get("number") or "")
            if number:
                numbers.add(number)
        return sorted(numbers, key=lambda item: int(item) if item.isdigit() else item)

    def comments(self, number: str) -> Iterable[dict[str, object]]:
        return self._comments_with_status(number)[1]

    def _comments_with_status(self, number: str) -> tuple[bool, list[dict[str, object]]]:
        nodes = self._gh_api_json(f"repos/{self.repo}/issues/{number}/comments?per_page=20")
        if not isinstance(nodes, list):
            return False, []
        comments = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            user = node.get("user") if isinstance(node.get("user"), dict) else {}
            comments.append(
                {
                    "id": node.get("id"),
                    "author": user.get("login") or "",
                    "body": node.get("body") or "",
                    "created_at": node.get("created_at") or "",
                }
            )
        return True, comments

    def handle_comment(self, number: str, comment: Mapping[str, object]) -> None:
        comment_id = str(comment.get("id") or "")
        if not comment_id or self.seen(comment_id):
            return
        author = str(comment.get("author") or "")
        body = str(comment.get("body") or "")
        first_line = body.splitlines()[0] if body.splitlines() else ""
        if is_controller_post(first_line, body):
            self.mark_seen(comment_id)
            return
        if author not in self.maintainers:
            self.mark_seen(comment_id)
            print(f"new-outsider-comment: {number} {author} {comment_id} (skipped reply per security gate)", flush=True)
            return
        decision = require_active_controller(self.ctx, "comment-monitor-write")
        write_active_controller_status(self.ctx, decision)
        if not decision.allowed:
            print(f"active_controller=noop:not-owner comment-monitor {number} {comment_id} owner={decision.owner_device}", flush=True)
            return
        react = self.gh_api([f"repos/{self.repo}/issues/comments/{comment_id}/reactions", "-X", "POST", "-f", "content=eyes"], check=False)
        if react.returncode == 0:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            print(f"new-team-comment: {number} {author} {comment_id} eyes-reacted-at={ts}", flush=True)
            with self.ctx.paths.pending_events.open("a", encoding="utf-8") as handle:
                handle.write(f"{ts} new-team-comment {number} {author} {comment_id}\n")
            self.post_banner(number, author, comment_id, body)
        else:
            first = (react.stderr or react.stdout).splitlines()[0] if (react.stderr or react.stdout).splitlines() else ""
            print(f"new-team-comment: {number} {author} {comment_id} eyes-react-FAILED: {first}", flush=True)
        self.mark_seen(comment_id)

    def post_banner(self, number: str, author: str, comment_id: str, body: str) -> None:
        excerpt = (body.splitlines()[0] if body.splitlines() else "")[:80]
        banner_body = f"""## 📊 状态 — 已收到 maintainer 评论(daemon 识别)

| 维度 | 值 |
|---|---|
| 触发评论 | id={comment_id} author={author} |
| 评论摘要 | {excerpt} |
| daemon 反应 | 👀 eyes react 已加 |
| 下一步 | controller 下次 wakeup(≤25 min)读 daemon log → 派 fresh codex round(maintainer-reply-resets-the-round)→ 更新本卡片 |
| **是否需要人介入** | ❌ 否(自动响应中) |

🤖 comment-monitor daemon

{AI_SENTINEL}
"""
        with _temp_body_file(banner_body) as body_file:
            issue = self.gh(["issue", "comment", number, "--body-file", str(body_file)], check=False)
            if issue.returncode == 0:
                print(f"daemon-banner-posted: {number} {comment_id} {_first_url(issue.stdout)}", flush=True)
                return
            pr = self.gh(["pr", "comment", number, "--body-file", str(body_file)], check=False)
            if pr.returncode == 0:
                print(f"daemon-banner-posted: {number} {comment_id} {_first_url(pr.stdout)}", flush=True)
            else:
                first = (pr.stderr or pr.stdout).splitlines()[0] if (pr.stderr or pr.stdout).splitlines() else ""
                print(f"daemon-banner-FAILED: {number} {comment_id} {first}", flush=True)

    def seen(self, comment_id: str) -> bool:
        return comment_id in self._state()

    def mark_seen(self, comment_id: str) -> None:
        state = self._state()
        state[comment_id] = "seen"
        self._write_state(state)

    def _should_fetch_comments(self, number: str, updated_at: str) -> bool:
        last_updated_at = self._last_updated_at()
        previous = last_updated_at.get(number)
        return not previous or updated_at > previous

    def mark_item_updated(self, number: str, updated_at: str) -> None:
        state = self._state()
        item_updated = state.get(ITEM_UPDATED_STATE_KEY)
        if not isinstance(item_updated, dict):
            item_updated = {}
        item_updated[str(number)] = updated_at
        state[ITEM_UPDATED_STATE_KEY] = item_updated
        self._write_state(state)

    def _last_updated_at(self) -> dict[str, str]:
        raw = self._state().get(ITEM_UPDATED_STATE_KEY)
        if not isinstance(raw, dict):
            return {}
        return {str(key): str(value) for key, value in raw.items() if isinstance(value, str)}

    def _write_state(self, state: dict[str, object]) -> None:
        tmp = self.state_file.with_name(f".{self.state_file.name}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_file)

    def _state(self) -> dict[str, object]:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        return data if isinstance(data, dict) else {}

    def gh(self, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return _run(["gh", *args, "--repo", self.repo], self.ctx.repo_root, check=check)

    def gh_api(self, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return _run(["gh", "api", *args], self.ctx.repo_root, check=check)

    def _gh_api_json(self, endpoint: str) -> object:
        result = self.gh_api([endpoint], check=False)
        if result.returncode != 0 or not result.stdout.strip():
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return None


def is_controller_post(first_line: str, body: str) -> bool:
    if AI_SENTINEL in body or "Generated with Claude Code" in body:
        return True
    return first_line.startswith(CONTROLLER_PREFIXES)


def _run(command: Sequence[str], cwd: Path, *, check: bool) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"{command[0]} failed")
    return result


class _temp_body_file:
    def __init__(self, body: str) -> None:
        self.body = body
        self.path: Path | None = None

    def __enter__(self) -> Path:
        import tempfile

        fd, name = tempfile.mkstemp(prefix="comment-monitor-", suffix=".md")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(self.body)
        self.path = Path(name)
        return self.path

    def __exit__(self, *_exc: object) -> None:
        if self.path:
            self.path.unlink(missing_ok=True)


def _first_url(text: str) -> str:
    for part in text.split():
        if part.startswith("https://"):
            return part
    return ""


def _lookback_search_fragment() -> str:
    raw = os.environ.get("COMMENT_MONITOR_LOOKBACK", "").strip()
    if not raw:
        return ""
    if raw.startswith("updated:"):
        return raw
    return f"updated:>={raw}"


def _lookback_minimum_updated_at() -> str:
    fragment = _lookback_search_fragment()
    if not fragment.startswith("updated:>="):
        return ""
    value = fragment.removeprefix("updated:>=").strip()
    if not value:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return f"{value}T00:00:00Z"
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run maintainer comment monitor")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--daemon", action="store_true", help="run persistently")
    mode.add_argument("--once", action="store_true", help="run one tick and exit")
    parser.parse_args(argv)
    try:
        ctx = LoopContext.load(cwd=os.getcwd())
        monitor = CommentMonitor(ctx)
    except (LoopContextError, RuntimeError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    if os.environ.get("TEST_NO_LOOP") == "1":
        monitor.tick()
        return 0
    return monitor.run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
