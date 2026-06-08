"""Read-only cross-instance write admission for managed loop targets."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Mapping, Sequence

from . import labels
from .banners import AUTO_LOOP_SENTINEL
from .context import LoopContext
from .monitors.comment import CONTROLLER_PREFIXES


DEFAULT_STAND_DOWN_WINDOW_SECONDS = 7200
AdmissionStatus = Literal["allowed", "stand_down", "unavailable"]
SignalSource = Literal["comment", "label", "unavailable"]
Runner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class CrossInstanceAdmission:
    status: AdmissionStatus
    reason: str
    other_login: str = ""
    source: SignalSource = "unavailable"
    created_at: str = ""


def check_cross_instance_admission(
    ctx: LoopContext,
    kind: str,
    number: int | str,
    current_login: str,
    now: datetime,
    *,
    runner: Runner | None = None,
) -> CrossInstanceAdmission:
    """Return deny-only cross-instance admission for one GitHub issue/PR target."""

    normalized_kind = kind.lower()
    if normalized_kind == "pull_request":
        normalized_kind = "pr"
    if normalized_kind not in {"issue", "pr"}:
        return CrossInstanceAdmission("unavailable", f"unsupported_kind:{kind}")
    target = str(number).strip()
    if not target.isdigit() or int(target) <= 0:
        return CrossInstanceAdmission("unavailable", f"invalid_target:{normalized_kind}:{number}")
    login = current_login.strip()
    if not login:
        return CrossInstanceAdmission("unavailable", "missing_current_login")
    run = runner or _default_runner
    window = _stand_down_window_seconds(ctx.host_env)
    cutoff = now.astimezone(timezone.utc).timestamp() - window
    targets = [(normalized_kind, target)]
    if normalized_kind == "pr":
        linked = _linked_managed_issue(ctx, target, run)
        if linked:
            targets.append(("issue", linked))

    for target_kind, target_number in targets:
        result = _check_one_target(ctx, target_kind, target_number, login, cutoff, run)
        if result.status != "allowed":
            return result
    return CrossInstanceAdmission("allowed", "no_fresh_other_instance_signal")


def _check_one_target(
    ctx: LoopContext,
    kind: str,
    number: str,
    current_login: str,
    cutoff_epoch: float,
    runner: Runner,
) -> CrossInstanceAdmission:
    comments_result = runner(_view_command(kind, number, "comments"), ctx.repo_root)
    if comments_result.returncode != 0:
        return CrossInstanceAdmission("unavailable", f"comments_unavailable:{kind}:{number}")
    try:
        comments_payload = json.loads(comments_result.stdout or "{}")
    except json.JSONDecodeError:
        return CrossInstanceAdmission("unavailable", f"comments_invalid_json:{kind}:{number}")
    comments = comments_payload.get("comments") if isinstance(comments_payload, dict) else None
    if not isinstance(comments, list):
        return CrossInstanceAdmission("unavailable", f"comments_invalid_json:{kind}:{number}")
    for comment in comments:
        signal = _comment_signal(comment, current_login, cutoff_epoch)
        if signal is not None:
            return signal

    timeline_result = runner(_timeline_command(ctx, kind, number), ctx.repo_root)
    if timeline_result.returncode != 0:
        return CrossInstanceAdmission("unavailable", f"timeline_unavailable:{kind}:{number}")
    try:
        timeline = json.loads(timeline_result.stdout or "[]")
    except json.JSONDecodeError:
        return CrossInstanceAdmission("unavailable", f"timeline_invalid_json:{kind}:{number}")
    events = timeline if isinstance(timeline, list) else timeline.get("timelineItems") if isinstance(timeline, dict) else None
    if not isinstance(events, list):
        return CrossInstanceAdmission("unavailable", f"timeline_invalid_json:{kind}:{number}")
    for event in events:
        signal = _label_signal(event, current_login, cutoff_epoch)
        if signal is not None:
            return signal
    return CrossInstanceAdmission("allowed", "no_fresh_other_instance_signal")


def _comment_signal(comment: object, current_login: str, cutoff_epoch: float) -> CrossInstanceAdmission | None:
    if not isinstance(comment, Mapping):
        return None
    created_at = str(comment.get("createdAt") or comment.get("created_at") or "")
    if not _fresh(created_at, cutoff_epoch):
        return None
    author = _login(comment.get("author") or comment.get("user") or comment.get("actor"))
    if not author or author == current_login:
        return None
    body = str(comment.get("body") or "")
    if AUTO_LOOP_SENTINEL not in body:
        return None
    first = _first_nonempty_line(body)
    if not first.startswith(CONTROLLER_PREFIXES):
        return None
    return CrossInstanceAdmission(
        "stand_down",
        f"fresh_other_instance_comment:{author}",
        other_login=author,
        source="comment",
        created_at=created_at,
    )


def _label_signal(event: object, current_login: str, cutoff_epoch: float) -> CrossInstanceAdmission | None:
    if not isinstance(event, Mapping):
        return None
    event_type = str(event.get("event") or event.get("__typename") or event.get("type") or "").lower()
    if event_type not in {"labeled", "unlabeled", "labeledevent", "unlabeledevent"}:
        return None
    created_at = str(event.get("createdAt") or event.get("created_at") or "")
    if not _fresh(created_at, cutoff_epoch):
        return None
    actor = _login(event.get("actor") or event.get("user"))
    if not actor or actor == current_login:
        return None
    label_name = _label_name(event.get("label"))
    if labels.normalize_label_set([label_name]).canonical:
        return CrossInstanceAdmission(
            "stand_down",
            f"fresh_other_instance_label:{actor}",
            other_login=actor,
            source="label",
            created_at=created_at,
        )
    return None


def _linked_managed_issue(ctx: LoopContext, pr_number: str, runner: Runner) -> str:
    result = runner(_view_command("pr", pr_number, "body"), ctx.repo_root)
    if result.returncode != 0:
        return ""
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return ""
    body = str(payload.get("body") or "") if isinstance(payload, Mapping) else ""
    if "Closes #" not in body and "closes #" not in body:
        return ""
    import re

    matches = re.findall(r"(?im)\bCloses\s+#([1-9][0-9]*)\b", body)
    if len(matches) != 1:
        return ""
    issue = matches[0]
    label_result = runner(_view_command("issue", issue, "labels"), ctx.repo_root)
    if label_result.returncode != 0:
        return ""
    try:
        issue_payload = json.loads(label_result.stdout or "{}")
    except json.JSONDecodeError:
        return ""
    raw_labels = issue_payload.get("labels") if isinstance(issue_payload, Mapping) else None
    if not isinstance(raw_labels, list):
        return ""
    names = [item.get("name") for item in raw_labels if isinstance(item, Mapping)]
    return issue if labels.MANAGED in labels.normalize_label_set(names).canonical else ""


def _view_command(kind: str, number: str, fields: str) -> list[str]:
    return ["gh", kind, "view", number, "--json", fields]


def _timeline_command(ctx: LoopContext, kind: str, number: str) -> list[str]:
    if not ctx.gh_repo_slug:
        return ["gh", "api", ""]
    owner, _, repo = ctx.gh_repo_slug.partition("/")
    endpoint = "issues" if kind == "issue" else "issues"
    return [
        "gh",
        "api",
        f"repos/{owner}/{repo}/{endpoint}/{number}/timeline",
        "-H",
        "Accept: application/vnd.github+json",
    ]


def _stand_down_window_seconds(host_env: Mapping[str, str]) -> int:
    raw = str(host_env.get("CROSS_INSTANCE_STAND_DOWN_WINDOW_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_STAND_DOWN_WINDOW_SECONDS
    try:
        parsed = int(raw)
    except ValueError:
        return DEFAULT_STAND_DOWN_WINDOW_SECONDS
    return parsed if parsed > 0 else DEFAULT_STAND_DOWN_WINDOW_SECONDS


def _fresh(created_at: str, cutoff_epoch: float) -> bool:
    parsed = _parse_time(created_at)
    return parsed is not None and parsed.timestamp() >= cutoff_epoch


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _login(value: object) -> str:
    return str(value.get("login") or "").strip() if isinstance(value, Mapping) else ""


def _label_name(value: object) -> str:
    return str(value.get("name") or "").strip() if isinstance(value, Mapping) else str(value or "").strip()


def _first_nonempty_line(value: str) -> str:
    for line in value.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _default_runner(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), cwd=str(cwd), capture_output=True, text=True, check=False)
