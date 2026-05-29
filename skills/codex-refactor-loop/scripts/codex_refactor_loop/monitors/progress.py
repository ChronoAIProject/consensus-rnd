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

from ..context import LoopContext, LoopContextError
from ..heartbeat import DaemonHeartbeatLease


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
        self.interval = int(interval or os.environ.get("INTERVAL", "600"))
        self.state_dir = Path(os.environ.get("STATE_DIR", str(ctx.paths.refactor_loop)))
        self.state_file = Path(os.environ.get("STATE_FILE", str(self.state_dir / "codex-progress-state.json")))
        self.log_dir = Path(os.environ.get("LOG_DIR", str(self.state_dir / "logs")))
        self.prompts_dir = Path(os.environ.get("PROMPTS_DIR", str(self.state_dir / "prompts")))
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
        for log in sorted(self.log_dir.glob("*.log")):
            base = log.stem
            if base.startswith("audit-iter-") or base.startswith("remote-ci-"):
                continue
            self.post_or_update(base, log)

    def parse_target(self, base: str) -> str:
        for pattern in (r"^review-pr([0-9]+).*", r"^fix-pr([0-9]+).*", r"^phase9-issue([0-9]+).*"):
            match = re.match(pattern, base)
            if match:
                return match.group(1)
        prompt = self.prompts_dir / f"{base}.md"
        if prompt.is_file():
            matches = re.findall(r"#[0-9]+", prompt.read_text(encoding="utf-8", errors="replace"))
            if matches:
                return matches[-1].lstrip("#")
        return ""

    def parse_kind(self, target: str) -> str:
        return "pr" if self.gh(["pr", "view", target, "--json", "number"], check=False).returncode == 0 else "issue"

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
        tail_block = extract_tail(log)
        if finished == "failed":
            status_line = f"❌ 失败; 已跑 {elapsed_min} min"
            delete_note = "codex 已非零退出;保留此 comment 直到 controller 处理失败。"
        else:
            status_line = f"⏳ 进行中; 已跑 {elapsed_min} min"
            delete_note = "自动更新每 10 分钟;edit-in-place 不堆评论;codex EXIT=0 后此 comment 自动删除。"
        body = f"""## 📊 codex 进展 {base} ({status_line})

```
{tail_block}
```

> {delete_note}
🤖 controller progress reporter

{AI_SENTINEL}
"""
        return body

    def post_or_update(self, base: str, log: Path) -> None:
        state = self._state()
        item = state.get(base, {}) if isinstance(state.get(base), dict) else {}
        target = str(item.get("target") or "")
        if not target:
            target = self.parse_target(base)
            if not target:
                self.log_msg(f"skip {base}: no target")
                return
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
            return
        prev_finished = str(item.get("finished") or "")
        needs_delete_retry = finished == "true" and cid not in (None, "", "null", 0, "0")
        if finished == prev_finished and not needs_delete_retry and finished in ("true", "failed"):
            return
        if finished == "true" and cid not in (None, "", "null", 0, "0"):
            delete = self.gh_api(["-X", "DELETE", f"repos/{self.repo}/issues/comments/{cid}"], check=False)
            if delete.returncode == 0:
                self.log_msg(f"deleted progress comment for {base} (finished, cid={cid} was={kind} #{target})")
                self._state_set(base, target, kind, 0, "deleted", "true")
            else:
                exists = self.gh_api([f"repos/{self.repo}/issues/comments/{cid}"], check=False)
                if exists.returncode != 0:
                    self.log_msg(f"comment {cid} for {base} already 404; marking finished")
                    self._state_set(base, target, kind, 0, "gone", "true")
                else:
                    self.log_msg(f"FAIL delete comment {cid} for {base}; comment still exists, retry next tick")
            return
        body = self.build_body(base, log, finished)
        cur_md5 = hash_body(body)
        if cur_md5 == prev_md5 and finished == prev_finished:
            return
        with temp_body_file(body) as body_file:
            if cid in (None, "", "null"):
                url = self._create_comment(kind, target, body_file)
                new_cid = _comment_id(url)
                if new_cid:
                    if not url_for_kind(url, kind):
                        kind = "issue" if kind == "pr" else "pr"
                    self._state_set(base, target, kind, int(new_cid), cur_md5, finished)
                    self.log_msg(f"created progress comment for {base} → {kind} #{target} (cid={new_cid})")
                else:
                    self.log_msg(f"FAIL to create comment for {base} → {kind} #{target}")
            else:
                patch = self.gh_api(["-X", "PATCH", f"repos/{self.repo}/issues/comments/{cid}", "-F", f"body=@{body_file}"], check=False)
                if patch.returncode == 0:
                    self._state_set(base, target, kind, cid, cur_md5, finished)
                    self.log_msg(f"edited progress comment for {base} (cid={cid}, finished={finished})")
                else:
                    self.log_msg(f"FAIL to edit comment {cid} for {base}; will retry next tick")

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

    def gh(self, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return _run(["gh", *args, "--repo", self.repo], self.ctx.repo_root, check=check)

    def gh_api(self, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return _run(["gh", "api", *args], self.ctx.repo_root, check=check)

    @staticmethod
    def log_msg(message: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"[{ts}] {message}", file=sys.stderr)


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run codex progress reporter")
    parser.add_argument("--exit-status")
    parser.add_argument("--hash-body", action="store_true")
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
    if os.environ.get("TEST_NO_LOOP") == "1":
        reporter.tick()
        return 0
    return reporter.run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
