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

from ..active_controller import require_active_controller, write_active_controller_status
from ..context import LoopContext, LoopContextError
from ..heartbeat import DaemonHeartbeatLease
from .. import labels as label_catalog
from ..ownership import GitHubWorkOwnership, WorkTarget


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
        self.state_file = state_file or Path(os.environ.get("STATE_FILE", ctx.paths.refactor_loop / "comment-monitor-state.json"))
        self.interval = int(interval or os.environ.get("COMMENT_MONITOR_INTERVAL") or os.environ.get("INTERVAL", "30"))
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
        # Refactor (fix/comment-monitor-only-new): Old pattern: every tick fetched
        # recent comments for every managed item. New principle: use the search
        # node updatedAt as the per-item freshness gate before spending a comments
        # query.
        # Refactor (fix/pr200-comment-ownership): Old pattern: comment targets
        # were reduced to bare numbers, so PR comments were checked as issues.
        # New principle: carry issue/pr identity into the ownership gate.
        for (kind, number), updated_at in self._search_active().items():
            if not self._should_fetch_comments(kind, number, updated_at):
                continue
            ok, comments = self._comments_with_status(number)
            for comment in comments:
                self.handle_comment(kind, number, comment)
            if ok:
                self.mark_item_updated(kind, number, updated_at)

    def _search_active(self) -> dict[tuple[str, str], str]:
        active: dict[tuple[str, str], str] = {}
        for query_label in label_catalog.query_labels_for(label_catalog.MANAGED):
            search_query = f'repo:{self.repo} is:open label:"{query_label}" {_lookback_search_fragment()}'.strip()
            data = self._graphql_json(
                """
                query($searchQuery: String!) {
                  search(query: $searchQuery, type: ISSUE, first: 100) {
                    nodes {
                      ... on Issue {
                        __typename
                        number
                        updatedAt
                      }
                      ... on PullRequest {
                        __typename
                        number
                        updatedAt
                      }
                    }
                  }
                }
                """,
                {"searchQuery": search_query},
            )
            nodes = (((data.get("data") or {}).get("search") or {}).get("nodes") or []) if isinstance(data, dict) else []
            if not isinstance(nodes, list):
                continue
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                number = str(node.get("number") or "")
                updated_at = str(node.get("updatedAt") or "")
                if not number or not updated_at:
                    continue
                kind = "pr" if node.get("__typename") == "PullRequest" else "issue"
                key = (kind, number)
                if key not in active or updated_at > active[key]:
                    active[key] = updated_at
        return dict(sorted(active.items(), key=lambda item: (int(item[0][1]) if item[0][1].isdigit() else item[0][1], item[0][0])))

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
        return self._comments_with_status(number)[1]

    def _comments_with_status(self, number: str) -> tuple[bool, list[dict[str, object]]]:
        result = self._gh_graphql(
            """
            query($owner: String!, $name: String!, $number: Int!) {
              repository(owner: $owner, name: $name) {
                issueOrPullRequest(number: $number) {
                  ... on Issue {
                    comments(last: 20) {
                      nodes {
                        databaseId
                        author { login }
                        body
                        createdAt
                      }
                    }
                  }
                  ... on PullRequest {
                    comments(last: 20) {
                      nodes {
                        databaseId
                        author { login }
                        body
                        createdAt
                      }
                    }
                  }
                }
              }
            }
            """,
            {"number": int(number)},
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False, []
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False, []
        if not isinstance(data, dict):
            return False, []
        item = (((data.get("data") or {}).get("repository") or {}).get("issueOrPullRequest") or {}) if isinstance(data, dict) else {}
        nodes = (((item.get("comments") or {}).get("nodes") or []) if isinstance(item, dict) else [])
        if not isinstance(nodes, list):
            return False, []
        comments = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            comments.append(
                {
                    "id": node.get("databaseId"),
                    "author": ((node.get("author") or {}).get("login") if isinstance(node.get("author"), dict) else ""),
                    "body": node.get("body") or "",
                    "created_at": node.get("createdAt") or "",
                }
            )
        return True, comments

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
        ownership = GitHubWorkOwnership(self.repo, cwd=self.ctx.repo_root)
        decision = ownership.decide(WorkTarget(kind, int(number)))
        # Refactor (iter/issue-193):
        #   Old pattern: maintainer comments triggered reactions/banners from
        #   any node that saw the event first.
        #   New principle: fresh foreign author.login targets are not marked
        #   seen and produce no GitHub side effects.
        if not decision.allowed:
            print(f"new-team-comment: {number} {author} {comment_id} skipped-ownership:{decision.reason}", flush=True)
            return
        # Refactor (fix/pr200-ownership-r15): Old pattern: stale takeover
        # comments could react/banner/mark-seen before the visible takeover
        # explanation.  New principle: post the ownership notice before any
        # maintainer-comment side effect, and fail closed if it cannot post.
        if decision.reason == "stale-takeover" and not ownership.post_takeover_notice(decision):
            print(f"new-team-comment: {number} {author} {comment_id} skipped-ownership-notice-failed", flush=True)
            return
        # Refactor (impl/issue191-single-active-controller): Old pattern:
        # comment monitors on multiple devices could react and post banners for
        # the same maintainer comment. New principle: GitHub comment mutations
        # are active-controller-owner-only; non-owners stay read-only.
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

    def _should_fetch_comments(self, kind: str, number: str, updated_at: str) -> bool:
        last_updated_at = self._last_updated_at()
        previous = last_updated_at.get(self._item_key(kind, number))
        return not previous or updated_at > previous

    def mark_item_updated(self, kind: str, number: str, updated_at: str) -> None:
        state = self._state()
        item_updated = state.get(ITEM_UPDATED_STATE_KEY)
        if not isinstance(item_updated, dict):
            item_updated = {}
        item_updated[self._item_key(kind, number)] = updated_at
        state[ITEM_UPDATED_STATE_KEY] = item_updated
        self._write_state(state)

    def _last_updated_at(self) -> dict[str, str]:
        raw = self._state().get(ITEM_UPDATED_STATE_KEY)
        if not isinstance(raw, dict):
            return {}
        return {str(key): str(value) for key, value in raw.items() if isinstance(value, str)}

    def _item_key(self, kind: str, number: str) -> str:
        return f"{kind}:{number}"

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

    def _graphql_json(self, query: str, variables: Mapping[str, object]) -> dict[str, object]:
        result = self._gh_graphql(query, variables)
        if result.returncode != 0 or not result.stdout.strip():
            return {}
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _gh_graphql(self, query: str, variables: Mapping[str, object]) -> subprocess.CompletedProcess[str]:
        owner, name = self.repo.split("/", 1)
        args: list[str] = ["graphql", "-f", f"query={query}", "-F", f"owner={owner}", "-F", f"name={name}"]
        for key, value in variables.items():
            args.extend(["-F", f"{key}={value}"])
        return self.gh_api(args, check=False)


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
