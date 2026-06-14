"""GitHub CLI call accounting for consensus-loop."""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
DEFAULT_ARTIFACT_RELATIVE = Path(".refactor-loop") / "state" / "gh-usage.jsonl"
SECONDARY_STATE_RELATIVE = Path(".refactor-loop") / "state"
DEFAULT_RETENTION_LINES = 20_000
DEFAULT_WINDOW_MINUTES = 60
DEFAULT_SECONDARY_BACKOFF_JITTER_MAX_SECONDS = 5.0
GRAPHQL_COMMANDS = {
    "issue",
    "pr",
    "search",
}
REST_COMMANDS = {
    "api",
    "auth",
    "label",
    "release",
    "repo",
    "run",
    "workflow",
}
OPTIONS_WITH_VALUE = {
    "-F",
    "-H",
    "-X",
    "-f",
    "-q",
    "--cache",
    "--field",
    "--header",
    "--hostname",
    "--input",
    "--jq",
    "--method",
    "--preview",
    "--raw-field",
    "--template",
}


@dataclass(frozen=True)
class GhUsageRecord:
    ts: str
    source: str
    subcommand: str
    pool: str
    exit_code: int
    count: int = 1
    schema: int = SCHEMA_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "ts": self.ts,
            "source": self.source,
            "subcommand": self.subcommand,
            "pool": self.pool,
            "exit_code": self.exit_code,
            "count": self.count,
        }


def ghwrap_dir(skill_root: Path | None = None) -> Path:
    root = skill_root or Path(__file__).resolve().parents[2]
    return root / "scripts" / "ghwrap"


def usage_path_for_repo(repo_root: Path) -> Path:
    return repo_root / DEFAULT_ARTIFACT_RELATIVE


def default_usage_path(env: Mapping[str, str] | None = None, cwd: Path | None = None) -> Path:
    source_env = os.environ if env is None else env
    repo_root = _repo_root(source_env, cwd=cwd)
    explicit = source_env.get("CRND_GH_USAGE_PATH")
    if explicit:
        bounded = _repo_contained_path(explicit, repo_root=repo_root)
        if bounded is not None:
            return bounded
    return usage_path_for_repo(repo_root)


def secondary_state_dir_for_repo(env: Mapping[str, str] | None = None, cwd: Path | None = None) -> Path:
    source_env = os.environ if env is None else env
    repo_root = _repo_root(source_env, cwd=cwd)
    bounded = _repo_contained_path(str(repo_root / SECONDARY_STATE_RELATIVE), repo_root=repo_root)
    return bounded or (repo_root / SECONDARY_STATE_RELATIVE)


def accounting_env(
    env: Mapping[str, str] | None = None,
    *,
    skill_root: Path | None = None,
    repo_root: Path | None = None,
    source: str | None = None,
    force_source: bool = False,
) -> dict[str, str]:
    result = dict(os.environ if env is None else env)
    shim = str(ghwrap_dir(skill_root))
    path_parts = [part for part in result.get("PATH", "").split(os.pathsep) if part and part != shim]
    result["PATH"] = os.pathsep.join([shim, *path_parts])
    if repo_root is not None:
        bounded = None
        if result.get("CRND_GH_USAGE_PATH"):
            bounded = _repo_contained_path(result["CRND_GH_USAGE_PATH"], repo_root=repo_root)
        result["CRND_GH_USAGE_PATH"] = str(bounded or usage_path_for_repo(repo_root))
    if source and (force_source or not result.get("CRND_GH_SOURCE")):
        result["CRND_GH_SOURCE"] = source
    return result


def _repo_root(env: Mapping[str, str], *, cwd: Path | None = None) -> Path:
    raw = env.get("REPO_ROOT")
    return Path(raw).expanduser().resolve() if raw else (cwd or Path.cwd()).resolve()


def _repo_contained_path(raw: str, *, repo_root: Path) -> Path | None:
    root = repo_root.expanduser().resolve()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def activate_controller_accounting(*, skill_root: Path | None = None) -> None:
    os.environ.update(accounting_env(skill_root=skill_root, source="controller"))


def resolve_real_gh(argv0: str, env: Mapping[str, str] | None = None) -> str | None:
    source_env = os.environ if env is None else env
    shim_dir = Path(argv0).resolve().parent
    filtered = []
    for part in source_env.get("PATH", "").split(os.pathsep):
        if not part:
            continue
        try:
            path_dir = Path(part).resolve()
            if path_dir == shim_dir or _is_ghwrap_shim_dir(path_dir):
                continue
        except OSError:
            pass
        filtered.append(part)
    found = shutil.which("gh", path=os.pathsep.join(filtered))
    if found and Path(found).resolve() != Path(argv0).resolve():
        return found
    return None


def _is_ghwrap_shim_dir(path_dir: Path) -> bool:
    candidate = path_dir / "gh"
    if path_dir.name != "ghwrap" or not candidate.exists():
        return False
    try:
        head = candidate.read_text(encoding="utf-8", errors="replace")[:2048]
    except OSError:
        return False
    return "gh_accounting" in head and "run_real_gh" in head


def run_real_gh(argv: Sequence[str], *, argv0: str) -> int:
    real_gh = resolve_real_gh(argv0)
    if real_gh is None:
        sys.stderr.write("ghwrap: real gh not found after removing shim directory from PATH\n")
        return 127
    _maybe_apply_secondary_backoff()
    try:
        proc = subprocess.Popen([real_gh, *argv], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = proc.communicate()
    except OSError as exc:
        sys.stderr.write(f"ghwrap: failed to exec real gh: {exc}\n")
        return 127
    sys.stdout.buffer.write(stdout)
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(stderr)
    sys.stderr.buffer.flush()
    _maybe_record_secondary_backoff(stdout, stderr)
    return int(proc.returncode or 0)


def _maybe_apply_secondary_backoff() -> None:
    try:
        from .secondary_mutation_backoff import currently_backing_off

        state_dir = secondary_state_dir_for_repo()
        backoff = currently_backing_off(state_dir)
        if not backoff.active:
            return
        now = time.time()
        jitter = random.uniform(0.0, _secondary_backoff_jitter_max(os.environ))
        sleep_seconds = max(0.0, float(backoff.until_epoch) - now) + jitter
        sys.stderr.write(f"ghwrap: secondary-backoff sleep={sleep_seconds:.3f}s until={int(backoff.until_epoch)}\n")
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    except Exception:
        return


def _maybe_record_secondary_backoff(stdout: bytes, stderr: bytes) -> None:
    try:
        from .secondary_mutation_backoff import (
            record_backoff_from_gh_output,
            record_generic_secondary_backoff_from_gh_output,
        )

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        state_dir = secondary_state_dir_for_repo()
        recorded = record_backoff_from_gh_output(state_dir, stdout_text, stderr_text, env=os.environ)
        if recorded is None:
            record_generic_secondary_backoff_from_gh_output(state_dir, stdout_text, stderr_text, env=os.environ)
    except Exception:
        return


def _secondary_backoff_jitter_max(env: Mapping[str, str]) -> float:
    raw = str(env.get("CRND_GH_SECONDARY_BACKOFF_JITTER_MAX_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_SECONDARY_BACKOFF_JITTER_MAX_SECONDS
    try:
        parsed = float(raw)
    except ValueError:
        return DEFAULT_SECONDARY_BACKOFF_JITTER_MAX_SECONDS
    if parsed < 0:
        return 0.0
    return min(parsed, 60.0)


def classify_subcommand(argv: Sequence[str]) -> str:
    if not argv:
        return ""
    if argv[0] == "api":
        return "api"
    return " ".join(argv[:2])


def classify_pool(argv: Sequence[str]) -> str:
    if not argv:
        return "unknown"
    command = argv[0]
    if command == "api":
        endpoint = api_endpoint(argv[1:])
        if endpoint == "graphql" or endpoint.endswith("/graphql"):
            return "graphql"
        return "rest_core"
    if command in GRAPHQL_COMMANDS:
        return "graphql"
    if command in REST_COMMANDS:
        return "rest_core"
    return "unknown"


def api_endpoint(args: Sequence[str]) -> str:
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in OPTIONS_WITH_VALUE:
            skip_next = True
            continue
        if arg.startswith("--") and "=" not in arg:
            continue
        if arg.startswith("-"):
            continue
        return arg.lstrip("/")
    return ""


def build_record(argv: Sequence[str], exit_code: int, env: Mapping[str, str] | None = None) -> GhUsageRecord:
    source_env = os.environ if env is None else env
    return GhUsageRecord(
        ts=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        source=source_env.get("CRND_GH_SOURCE") or "unknown",
        subcommand=classify_subcommand(argv),
        pool=classify_pool(argv),
        exit_code=exit_code,
    )


def append_record(record: GhUsageRecord, path: Path | None = None, *, max_lines: int | None = None) -> None:
    target = path or default_usage_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.to_json(), sort_keys=True, separators=(",", ":")) + "\n")
    retain_usage_lines(target, max_lines=max_lines)


def record_gh_call(argv: Sequence[str], exit_code: int) -> None:
    max_lines = _retention_lines(os.environ)
    append_record(build_record(argv, exit_code), max_lines=max_lines)


def retain_usage_lines(path: Path, *, max_lines: int | None = None) -> None:
    limit = DEFAULT_RETENTION_LINES if max_lines is None else max_lines
    if limit <= 0:
        return
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    if len(lines) <= limit:
        return
    path.write_text("\n".join(lines[-limit:]) + "\n", encoding="utf-8")


def _retention_lines(env: Mapping[str, str]) -> int:
    raw = env.get("CRND_GH_USAGE_MAX_LINES")
    if raw is None:
        return DEFAULT_RETENTION_LINES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_RETENTION_LINES
    if 1 <= value <= DEFAULT_RETENTION_LINES:
        return value
    return DEFAULT_RETENTION_LINES


def load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return records
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("schema") == SCHEMA_VERSION:
            records.append(item)
    return records


def aggregate_records(records: Iterable[Mapping[str, Any]], *, window_minutes: int = DEFAULT_WINDOW_MINUTES) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=window_minutes)
    all_records = list(records)
    rolling = [record for record in all_records if _parse_ts(record.get("ts")) >= cutoff]
    return {
        "schema": SCHEMA_VERSION,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_minutes": window_minutes,
        "total": _aggregate_bucket(all_records),
        "rolling": _aggregate_bucket(rolling),
    }


def _aggregate_bucket(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_source: Counter[str] = Counter()
    by_pool: Counter[str] = Counter()
    by_subcommand: Counter[str] = Counter()
    exit_codes: Counter[str] = Counter()
    for record in records:
        count = _count(record)
        by_source[str(record.get("source") or "unknown")] += count
        by_pool[str(record.get("pool") or "unknown")] += count
        by_subcommand[str(record.get("subcommand") or "")] += count
        exit_codes[str(record.get("exit_code"))] += count
    return {
        "calls": sum(by_source.values()),
        "by_source": dict(sorted(by_source.items())),
        "by_pool": dict(sorted(by_pool.items())),
        "by_subcommand": dict(sorted(by_subcommand.items())),
        "by_exit_code": dict(sorted(exit_codes.items())),
    }


def _count(record: Mapping[str, Any]) -> int:
    try:
        value = int(record.get("count", 1))
    except (TypeError, ValueError):
        return 1
    return value if value > 0 else 1


def _parse_ts(value: object) -> datetime:
    if not isinstance(value, str):
        return datetime.fromtimestamp(0, timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def render_summary(summary: Mapping[str, Any], *, limit: int = 12) -> str:
    lines = [
        f"gh usage stats (window={summary.get('window_minutes')}m)",
        "",
        _render_bucket("total", summary.get("total", {}), limit=limit),
        "",
        _render_bucket("rolling", summary.get("rolling", {}), limit=limit),
    ]
    return "\n".join(lines).rstrip() + "\n"


def _render_bucket(name: str, raw: object, *, limit: int) -> str:
    bucket = raw if isinstance(raw, Mapping) else {}
    lines = [f"{name}: calls={bucket.get('calls', 0)}"]
    for key in ("by_pool", "by_source", "by_subcommand"):
        values = bucket.get(key)
        if not isinstance(values, Mapping) or not values:
            lines.append(f"  {key}: none")
            continue
        ranked = sorted(values.items(), key=lambda item: (-int(item[1]), str(item[0])))[:limit]
        lines.append(f"  {key}: " + ", ".join(f"{label}={count}" for label, count in ranked))
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="read gh usage accounting from local runtime state")
    parser.add_argument("--json", action="store_true", help="emit machine-readable aggregate JSON")
    parser.add_argument("--window-minutes", type=int, default=DEFAULT_WINDOW_MINUTES)
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args(argv)
    summary = aggregate_records(load_records(default_usage_path()), window_minutes=args.window_minutes)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(render_summary(summary, limit=args.limit), end="")
    return 0
