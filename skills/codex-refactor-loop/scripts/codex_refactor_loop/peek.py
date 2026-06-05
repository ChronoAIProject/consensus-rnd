"""Read-only controller status lens."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from . import labels as label_catalog
from .closed_phase_labels import (
    closed_reconcile_candidate_queries,
    item_matches_closed_reconcile_query,
    plan_closed_reconcile_candidate,
)
from .context import LoopContext, LoopContextError
from .holistic_status import collect as collect_holistic_status
from .holistic_status import render_peek_summary
from .pr_checks import PrChecksProjection
from .work_items import ManagedWorkProjection
from .workflow_stages import format_stage
from .wakeup_plan import load_github_items, unpushed_worker_output_actions


class PeekStatusLens:
    def __init__(self, ctx: LoopContext) -> None:
        self.ctx = ctx

    def render(self) -> str:
        lines: list[str] = []
        lines.append(f"═══════════════ peek {datetime.now(timezone.utc).strftime('%H:%M:%SZ')} ═══════════════")
        lines.extend(["", *self._holistic_summary()])
        lines.extend(["", "▍Activity timeline (read-only facts):"])
        lines.extend(self._activity_timeline(limit=12))
        lines.extend(["", "▍🚨 maintainer comments (read first — missed read = controller bug):"])
        lines.extend(self._maintainer_comments())
        lines.extend(["", f"▍Active codex: {self._count_loop_codex()}"])
        active = self._list_loop_codex()
        if active:
            lines.extend(f"  • {item}" for item in sorted(active))
        lines.extend(["", f"▍{format_stage('design-consensus')} router / pending events:", "  ledger tail:"])
        lines.extend(_prefixed_tail(self.ctx.paths.refactor_loop / "phase9-router-ledger.jsonl", 10, "    "))
        lines.append("  pending events tail:")
        lines.extend(_prefixed_tail(self.ctx.paths.pending_events, 10, "    "))
        lines.extend(["", "▍Milestone (优先) issues:"])
        lines.extend(self._milestone_items())
        lines.extend(["", "▍Open auto-loop PRs:"])
        lines.extend(self._open_prs())
        lines.extend(["", "▍Unpushed worker output:"])
        lines.extend(self._unpushed_worker_output())
        lines.extend(["", "▍Monitor zero_streak (last 10 ticks):"])
        lines.extend(self._zero_streak())
        lines.extend(["", "▍Stale labels (CLOSED but still carrying in-flight phase labels):"])
        lines.extend(self._stale_labels())
        lines.extend(["", "▍Issue/PR linkage mismatch:"])
        lines.extend(self._linkage_mismatch())
        lines.extend(["", "▍Spawn drop (N solvers complete but judge was not dispatched):"])
        lines.extend(self._spawn_drop())
        lines.extend(["", "▍Drift (label vs codex mismatch):"])
        lines.extend(self._drift())
        lines.extend(["", "▍Stale worktree (remote branch missing; cleanup is controller-owned):"])
        lines.extend(self._stale_worktrees())
        lines.extend(["", "▍Stuck too long (>6h without maintainer reply; consider 4h reflector re-evaluation):"])
        lines.extend(self._stuck_too_long())
        lines.extend(["", "▍Open auto-loop issues:"])
        lines.extend(self._open_issues())
        lines.extend(["", "═══════════════════════════════════════════════════"])
        return "\n".join(lines) + "\n"

    def _holistic_summary(self) -> list[str]:
        try:
            return render_peek_summary(collect_holistic_status(self.ctx))
        except Exception as exc:
            return ["▍Holistic status:", f"  unavailable: {exc}"]

    def _activity_timeline(self, *, limit: int) -> list[str]:
        events: list[tuple[str, str, str]] = []
        for path in sorted(self.ctx.paths.logs.glob("*.log")):
            events.extend(_unified_tick_events(path))
        events.extend(_pending_event_facts(self.ctx.paths.pending_events))
        events.extend(_phase9_ledger_facts(self.ctx.paths.refactor_loop / "phase9-router-ledger.jsonl"))
        if not events:
            return ["  (none)"]
        events.sort(key=lambda item: item[0])
        return [f"  • {ts} {source}: {message}" for ts, source, message in events[-limit:]]

    def _maintainer_comments(self) -> list[str]:
        output: list[str] = []
        issues = self._list_by_any_label("issue", label_catalog.query_labels_for(label_catalog.MANAGED), "number")
        prs = self._list_by_any_label("pr", label_catalog.query_labels_for(label_catalog.MANAGED), "number")
        targets = [("i", str(item.get("number"))) for item in issues if isinstance(item, dict)]
        targets.extend(("p", str(item.get("number"))) for item in prs if isinstance(item, dict))
        now = datetime.now(timezone.utc)
        for kind, num in targets:
            data = self.gh_json([("issue" if kind == "i" else "pr"), "view", num, "--json", "comments"], {})
            comments = data.get("comments", []) if isinstance(data, dict) else []
            non_ai = [
                c for c in comments
                if isinstance(c, dict)
                and "⟦AI:AUTO-LOOP⟧" not in str(c.get("body") or "")
                and not str(c.get("body") or "").lstrip().startswith(("## 📊", "## 🤖", "## ✅", "## 🆘"))
                and not str((c.get("author") or {}).get("login") if isinstance(c.get("author"), dict) else "").endswith("[bot]")
            ]
            if not non_ai:
                continue
            last = max(non_ai, key=lambda c: str(c.get("createdAt") or ""))
            ai_reply = [
                c for c in comments
                if isinstance(c, dict)
                and str(c.get("createdAt") or "") > str(last.get("createdAt") or "")
                and ("⟦AI:AUTO-LOOP⟧" in str(c.get("body") or "") or str(c.get("body") or "").lstrip().startswith(("## 📊", "## 🤖", "## ✅", "## 🆘")))
            ]
            ts = _parse_time(last.get("createdAt"))
            if not ts:
                continue
            delta_h = (now - ts).total_seconds() / 3600
            if delta_h > 12 and ai_reply:
                continue
            flag = "⏰ no-AI-reply" if not ai_reply else ""
            author = (last.get("author") or {}).get("login", "?") if isinstance(last.get("author"), dict) else "?"
            body = str(last.get("body") or "").replace("\n", " ")[:200]
            output.append(f"  {flag} [{author}] {num} {kind} ({delta_h:.1f}h ago): {body}")
        return output

    def _count_loop_codex(self) -> int:
        result = subprocess.run([sys.executable, str(self.ctx.skill_root / "scripts" / "consensus-rnd-cli"), "concurrency", "--count-only"], cwd=str(self.ctx.repo_root), capture_output=True, text=True, check=False)
        try:
            return int(result.stdout.strip() or "0")
        except ValueError:
            return 0

    def _list_loop_codex(self) -> list[str]:
        result = subprocess.run([sys.executable, str(self.ctx.skill_root / "scripts" / "consensus-rnd-cli"), "concurrency", "--list-codex"], cwd=str(self.ctx.repo_root), capture_output=True, text=True, check=False)
        names = []
        for line in result.stdout.splitlines():
            match = re.search(r"--log [^ ]*/([^ /]+)\.log", line)
            names.append(match.group(1) if match else line.strip())
        return [name for name in names if name]

    def _milestone_items(self) -> list[str]:
        lines = []
        for kind, gh_kind in (("issue", "issue"), ("PR", "pr")):
            for item in self._list_by_any_label(gh_kind, label_catalog.query_labels_for(label_catalog.MILESTONE_CURRENT), "number,title,labels"):
                if isinstance(item, dict):
                    labels = [str(label.get("name", "")) for label in item.get("labels", []) if isinstance(label, dict)]
                    projection = label_catalog.normalize_label_set(labels)
                    if label_catalog.MILESTONE_CURRENT not in projection.canonical:
                        continue
                    visible = self._visible_loop_labels(labels)
                    title = str(item.get("title") or "")[:55]
                    lines.append(f"  • {kind} #{item.get('number')} labels=[{', '.join(visible)}] — {title}")
        return lines

    def _open_prs(self) -> list[str]:
        lines = []
        for item in self._list_by_any_label("pr", label_catalog.query_labels_for(label_catalog.MANAGED), "number,title"):
            if not isinstance(item, dict):
                continue
            num = str(item.get("number"))
            fail, pending, passed = self._checks(num)
            state = self.gh_text(["pr", "view", num, "--json", "mergeStateStatus", "--jq", ".mergeStateStatus"]).strip()
            lines.append(f"  • PR #{num} [{state}] CI: fail={fail} pending={pending} pass={passed} — {str(item.get('title') or '')[:60]}")
        return lines

    def _unpushed_worker_output(self) -> list[str]:
        out = []
        for action in unpushed_worker_output_actions(self.ctx.repo_root, load_github_items(self.ctx.repo_root)):
            out.append(
                "  ⚠️ "
                f"{action['line']} head={action['head_ref']} ahead={action['ahead_count']} "
                f"worktree={action['worktree']} controller_action={action['controller_action']} "
                f"no_lifecycle_authority={str(action['no_lifecycle_authority']).lower()}"
            )
        return out

    def _zero_streak(self) -> list[str]:
        lines = _tail(self.ctx.paths.logs / "concurrency-monitor.log", 10)
        values = []
        for line in lines:
            match = re.search(r"zero_streak=([0-9]+)", line)
            if match:
                values.append(int(match.group(1)))
        out = []
        if values:
            out.append(f"  max: zero_streak={max(values)}")
            out.append(f"  current: zero_streak={values[-1]}")
        return out

    def _stale_labels(self) -> list[str]:
        out = []
        seen: set[tuple[str, int]] = set()
        for kind in ("issue", "pr"):
            for query in closed_reconcile_candidate_queries(kind, "closed"):
                rows = self._list_by_any_label(
                    query.kind,
                    (query.managed_label,),
                    "number,state,labels",
                    state=query.state,
                    limit=query.limit,
                    search=f'label:"{query.dirty_label}"' if query.dirty_label else None,
                )
                for item in rows:
                    if not isinstance(item, dict):
                        continue
                    if not item_matches_closed_reconcile_query(kind, item, query):
                        continue
                    plan = plan_closed_reconcile_candidate(kind, item)
                    if not plan:
                        continue
                    key = (plan.kind, plan.number)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(
                        f"  ⚠️ closed {kind} #{plan.number} terminal={plan.terminal_phase} "
                        f"add={','.join(plan.add_labels) or '-'} remove={','.join(plan.remove_labels) or '-'}"
                    )
        return out

    def _linkage_mismatch(self) -> list[str]:
        return list(self._managed_work_projection().linkage_mismatches())

    def _spawn_drop(self) -> list[str]:
        out = []
        for minimal in self.ctx.paths.runs.glob("phase9-issue*-r*-minimal.md"):
            base = minimal.name.removesuffix("-minimal.md")
            match = re.match(r"phase9-issue([0-9]+)-r([0-9]+)", base)
            if not match:
                continue
            issue, round_num = match.groups()
            if (self.ctx.paths.runs / f"{base}-structural.md").is_file() and (self.ctx.paths.runs / f"{base}-delete.md").is_file() and not (self.ctx.paths.logs / f"{base}-judge.log").is_file():
                state = self.gh_text(["issue", "view", issue, "--json", "state", "--jq", ".state"]).strip()
                if state == "OPEN":
                    out.append(f"  ⚠️ issue #{issue} r{round_num} 3 solvers done but judge log absent (redispatch judge)")
        return out

    def _drift(self) -> list[str]:
        active = set()
        now = datetime.now().timestamp()
        for log in self.ctx.paths.logs.glob("*.log"):
            try:
                if now - log.stat().st_mtime > 600:
                    continue
                if any(line.startswith("EXIT=") for line in _tail(log, 20)):
                    continue
                active.add(log.stem)
            except OSError:
                continue
        out = []
        represented = self._managed_work_projection().represented_issue_numbers()
        for kind in ("issue", "pr"):
            for item in self._list_by_any_label(kind, label_catalog.query_labels_for(label_catalog.MANAGED), "number,labels"):
                if not isinstance(item, dict):
                    continue
                labels = [str(label.get("name", "")) for label in item.get("labels", []) if isinstance(label, dict)]
                phase = label_catalog.normalize_label_set(labels).phase or ""
                num = str(item.get("number"))
                if kind == "issue" and _safe_int(num) in represented:
                    continue
                if phase and label_catalog.phase_expected_workers(phase) == 0:
                    continue
                if phase and not any(f"pr{num}" in log or f"issue{num}" in log for log in active):
                    out.append(f"  ⚠️ {kind} #{num} label={phase} but 0 codex referencing it")
        return out

    def _managed_work_projection(self) -> ManagedWorkProjection:
        items = []
        for item in load_github_items(self.ctx.repo_root):
            items.append(
                {
                    "kind": item.kind,
                    "number": item.number,
                    "labels": item.labels,
                    "body": item.body,
                    "state": "open",
                    "title": item.title,
                }
            )
        return ManagedWorkProjection(items)

    def _stale_worktrees(self) -> list[str]:
        out = []
        result = subprocess.run(["git", "-C", str(self.ctx.repo_root), "worktree", "list", "--porcelain"], capture_output=True, text=True, check=False)
        current: Path | None = None
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                current = Path(line.removeprefix("worktree "))
                continue
            if not line.startswith("branch ") or current is None:
                continue
            base = current.name
            if base in (self.ctx.repo_root.name, f"{self.ctx.repo_root.name}-wt-dev-sync", "dev-sync"):
                continue
            branch = line.removeprefix("branch refs/heads/")
            remote = subprocess.run(["git", "-C", str(self.ctx.repo_root), "ls-remote", "--exit-code", "--heads", "origin", branch], capture_output=True, text=True, check=False)
            if remote.returncode != 0:
                out.append(
                    f"  ⚠️ path={current} branch={branch} remote_missing=true "
                    "cleanup_owner=controller-runbook no_lifecycle_authority=true"
                )
        return out

    def _stuck_too_long(self) -> list[str]:
        out = []
        now = datetime.now(timezone.utc)
        for item in self._list_by_any_label("issue", label_catalog.query_labels_for(label_catalog.STUCK), "number,title"):
            if not isinstance(item, dict):
                continue
            num = str(item.get("number"))
            data = self.gh_json(["issue", "view", num, "--json", "comments"], {})
            comments = data.get("comments", []) if isinstance(data, dict) else []
            non_ai = [c for c in comments if isinstance(c, dict) and "⟦AI:AUTO-LOOP⟧" not in str(c.get("body") or "")]
            if not non_ai:
                continue
            last = max(non_ai, key=lambda c: str(c.get("createdAt") or ""))
            ts = _parse_time(last.get("createdAt"))
            if ts and (now - ts).total_seconds() / 3600 > 6:
                out.append(f"  ⚠️ #{num} last maintainer comment {(now - ts).total_seconds() / 3600:.1f}h ago — {str(item.get('title') or '')[:50]}")
        return out

    def _open_issues(self) -> list[str]:
        out = []
        for item in self._list_by_any_label("issue", label_catalog.query_labels_for(label_catalog.MANAGED), "number,title,labels"):
            if not isinstance(item, dict):
                continue
            labels = [str(label.get("name", "")) for label in item.get("labels", []) if isinstance(label, dict)]
            visible = self._visible_loop_labels(labels)
            out.append(f"  • #{item.get('number')} labels=[{', '.join(visible)}] — {str(item.get('title') or '')[:55]}")
        return out

    def _visible_loop_labels(self, labels: list[str]) -> list[str]:
        projection = label_catalog.normalize_label_set(labels)
        return sorted(projection.canonical)

    def _list_by_any_label(
        self,
        kind: str,
        query_labels: Sequence[str],
        json_fields: str,
        *,
        state: str = "open",
        limit: str | None = None,
        search: str | None = None,
    ) -> list[dict]:
        rows: list[dict] = []
        seen: set[int] = set()
        for query_label in query_labels:
            command = [kind, "list", "--label", query_label, "--state", state]
            if search:
                command += ["--search", search]
            if limit:
                command += ["--limit", limit]
            command += ["--json", json_fields]
            for item in self.gh_json(command, []):
                if not isinstance(item, dict):
                    continue
                try:
                    number = int(item.get("number"))
                except (TypeError, ValueError):
                    rows.append(item)
                    continue
                if number in seen:
                    continue
                seen.add(number)
                rows.append(item)
        return rows

    def _checks(self, pr_num: str) -> tuple[int, int, int]:
        if not self.ctx.gh_repo_slug:
            return 0, 0, 0
        status = PrChecksProjection(cwd=self.ctx.repo_root).check_pr(self.ctx.gh_repo_slug, pr_num)
        if not status.ok:
            return 0, 0, 0
        buckets = [item.bucket for item in status.runs]
        return buckets.count("fail"), buckets.count("pending"), buckets.count("pass")

    def gh_text(self, args: Sequence[str]) -> str:
        command = ["gh", *args]
        if self.ctx.gh_repo_slug:
            command.extend(["--repo", self.ctx.gh_repo_slug])
        result = subprocess.run(command, cwd=str(self.ctx.repo_root), capture_output=True, text=True, check=False)
        return result.stdout

    def gh_json(self, args: Sequence[str], default):
        text = self.gh_text(args)
        try:
            parsed = json.loads(text or "null")
        except Exception:
            return default
        return default if parsed is None else parsed

def _tail(path: Path, count: int) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:]
    except OSError:
        return []


def _prefixed_tail(path: Path, count: int, prefix: str) -> list[str]:
    return [prefix + line for line in _tail(path, count)]


_UNIFIED_TICK_RE = re.compile(r"^\[(?P<ts>[^\]]+)\]\s+(?P<daemon>[A-Za-z0-9_.-]+):\s+tick\s+(?P<action>.+)$")
_ISO_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _unified_tick_events(path: Path) -> list[tuple[str, str, str]]:
    events: list[tuple[str, str, str]] = []
    for line in _tail(path, 80):
        match = _UNIFIED_TICK_RE.fullmatch(line)
        if not match:
            continue
        events.append((match.group("ts"), match.group("daemon"), match.group("action")))
    return events


def _pending_event_facts(path: Path) -> list[tuple[str, str, str]]:
    events: list[tuple[str, str, str]] = []
    for line in _tail(path, 40):
        ts, rest = _split_ts_line(line)
        if not rest:
            continue
        events.append((ts, "pending-events", rest))
    return events


def _phase9_ledger_facts(path: Path) -> list[tuple[str, str, str]]:
    events: list[tuple[str, str, str]] = []
    for line in _tail(path, 40):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        ts = str(entry.get("dispatched_at") or "")
        if not _ISO_TS_RE.fullmatch(ts):
            continue
        key = str(entry.get("key") or "?")
        route = str(entry.get("route") or entry.get("dispatch_state") or "dispatch")
        marker = str(entry.get("marker") or "")
        target = str(entry.get("target_actor") or "")
        detail = f"key={key} route={route}"
        if target:
            detail += f" target={target}"
        if marker:
            detail += f" marker={marker}"
        events.append((ts, "phase9-ledger", detail))
    return events


def _split_ts_line(line: str) -> tuple[str, str]:
    head, sep, tail = line.partition(" ")
    if sep and _ISO_TS_RE.fullmatch(head):
        return head, tail.strip()
    return "0000-00-00T00:00:00Z", line.strip()


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.rstrip("Z")).replace(tzinfo=timezone.utc) if value.endswith("Z") else datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="consensus-rnd-cli peek",
        description="render the human-readable codex-refactor-loop status lens",
    )
    parser.parse_args(argv)
    try:
        ctx = LoopContext.load(read_only=True, allow_git_root_fallback=True, cwd=os.getcwd())
    except LoopContextError as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 2
    subprocess.run(["git", "-C", str(ctx.repo_root), "fetch", "origin", "--quiet"], capture_output=True, text=True, check=False)
    print(PeekStatusLens(ctx).render(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
