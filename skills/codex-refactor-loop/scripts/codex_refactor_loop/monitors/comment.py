"""Maintainer comment monitor daemon."""

from __future__ import annotations

import json
import os
import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ..context import LoopContext, LoopContextError
from ..heartbeat import DaemonHeartbeatLease
from .. import labels as label_catalog
from ..ownership import GitHubWorkOwnership, WorkTarget


AI_SENTINEL = "⟦AI:AUTO-LOOP⟧"
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
        self.state_file = state_file or Path(os.environ.get("STATE_FILE", ctx.paths.refactor_loop / "comment-monitor-state.json"))
        self.interval = int(interval or os.environ.get("INTERVAL", "30"))
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
        for kind, number in self.targets():
            for comment in self.comments(number):
                self.handle_comment(kind, number, comment)

    def targets(self) -> list[tuple[str, str]]:
        targets: set[tuple[str, str]] = set()
        for kind in ("issue", "pr"):
            for query_label in label_catalog.query_labels_for(label_catalog.MANAGED):
                result = self.gh(
                    [kind, "list", "--state", "open", "--label", query_label, "--json", "number", "-q", ".[].number"],
                    check=False,
                )
                if result.returncode == 0:
                    targets.update((kind, line.strip()) for line in result.stdout.splitlines() if line.strip())
        return sorted(targets, key=lambda item: (int(item[1]) if item[1].isdigit() else item[1], item[0]))

    def comments(self, number: str) -> Iterable[dict[str, object]]:
        result = self.gh_api([f"repos/{self.repo}/issues/{number}/comments", "--jq", ".[] | {id, author: .user.login, body, created_at}"], check=False)
        if result.returncode != 0 or not result.stdout.strip():
            return []
        rows = []
        for line in result.stdout.splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def handle_comment(self, kind: str, number: str, comment: Mapping[str, object]) -> None:
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
        # Refactor (fix/pr200-comment-ownership): Old pattern: comment targets
        # were reduced to bare numbers, so PR comments were checked as issues.
        # New principle: carry issue/pr identity into the ownership gate.
        decision = GitHubWorkOwnership(self.repo, cwd=self.ctx.repo_root).decide(WorkTarget(kind, int(number)))
        # Refactor (iter/issue-193):
        #   Old pattern: maintainer comments triggered reactions/banners from
        #   any node that saw the event first.
        #   New principle: fresh foreign author.login targets are not marked
        #   seen and produce no GitHub side effects.
        if not decision.allowed:
            print(f"new-team-comment: {number} {author} {comment_id} skipped-ownership:{decision.reason}", flush=True)
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
