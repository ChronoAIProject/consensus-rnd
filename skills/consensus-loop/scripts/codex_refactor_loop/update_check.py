"""Notify-only consensus-loop update check probe."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from .context import LoopContext, LoopContextError
from .release.versions import compare_semver
from .state import read_json, write_json


MANIFEST_RELATIVE = "skills/consensus-loop/VERSION.json"
STATE_RELATIVE = ".refactor-loop/state/update-check.json"
DEFAULT_INTERVAL_SECONDS = 21600
DEFAULT_TIMEOUT_SECONDS = 5


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def env_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def env_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value or "")
    except ValueError:
        return default
    return parsed if parsed > 0 else default


@dataclass(frozen=True)
class GitHubReleaseVersion:
    version: str
    source: str
    release_url: str | None = None


class UpdateCheckProbe:
    """Read local version and GitHub release/tag state, then write local notice state.

    """

    def __init__(
        self,
        ctx: LoopContext,
        *,
        now: Callable[[], datetime] = utc_now,
        runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.ctx = ctx
        self.now = now
        self.runner = runner or self._run

    @property
    def state_path(self) -> Path:
        return self.ctx.paths.state / "update-check.json"

    @property
    def manifest_path(self) -> Path:
        return self.ctx.skill_root / "VERSION.json"

    def maybe_run(self, *, startup: bool = False) -> dict[str, Any]:
        env = self.ctx.host_env
        now = self.now()
        interval = env_int(env.get("UPDATE_CHECK_INTERVAL_SECONDS"), DEFAULT_INTERVAL_SECONDS)
        if not env_bool(env.get("UPDATE_CHECK_ENABLE")):
            return self._write_state(
                status="disabled",
                checked_at=now,
                reason="UPDATE_CHECK_ENABLE is not true in host.env",
                startup=startup,
                interval_seconds=interval,
            )
        previous = read_json(self.state_path, {})
        if not startup and isinstance(previous, dict):
            checked_at = parse_time(previous.get("checked_at"))
            if checked_at is not None and now - checked_at < timedelta(seconds=interval):
                return previous
        try:
            manifest = self._load_manifest()
            latest = self._latest_release_version(
                manifest["repository"],
                timeout=env_int(env.get("UPDATE_CHECK_TIMEOUT_SECONDS"), DEFAULT_TIMEOUT_SECONDS),
            )
            comparison = compare_semver(latest.version, manifest["version"])
            return self._write_state(
                status="ok",
                checked_at=now,
                local_version=manifest["version"],
                latest_version=latest.version,
                update_available=comparison > 0,
                update_source=latest.source,
                release_url=latest.release_url,
                startup=startup,
                interval_seconds=interval,
            )
        except Exception as exc:
            return self._write_state(
                status="unknown",
                checked_at=now,
                reason=f"{type(exc).__name__}: {exc}",
                startup=startup,
                interval_seconds=interval,
            )

    def _load_manifest(self) -> dict[str, str]:
        raw = read_json(self.manifest_path, None)
        if not isinstance(raw, dict):
            raise RuntimeError("VERSION.json is missing or invalid")
        expected = {
            "schema": "consensus-loop-version",
            "release_source": "github-release-then-tag",
            "install_hint": "host-owned",
        }
        for key, value in expected.items():
            if raw.get(key) != value:
                raise RuntimeError(f"VERSION.json {key} mismatch")
        version = raw.get("version")
        repository = raw.get("repository")
        if not isinstance(version, str) or not version:
            raise RuntimeError("VERSION.json version missing")
        if not isinstance(repository, str) or "/" not in repository:
            raise RuntimeError("VERSION.json repository missing")
        return {"version": version, "repository": repository}

    def _latest_release_version(self, repository: str, *, timeout: int) -> GitHubReleaseVersion:
        try:
            release = self._gh_api(f"repos/{repository}/releases/latest", timeout=timeout)
            tag_name = release.get("tag_name") if isinstance(release, dict) else None
            html_url = release.get("html_url") if isinstance(release, dict) else None
            version = normalize_tag_version(tag_name)
            if version:
                return GitHubReleaseVersion(version=version, source="github-release", release_url=html_url if isinstance(html_url, str) else None)
        except RuntimeError:
            pass
        tags = self._gh_api(f"repos/{repository}/tags", timeout=timeout)
        first_tag = tags[0] if isinstance(tags, list) and tags else None
        tag_name = first_tag.get("name") if isinstance(first_tag, dict) else None
        version = normalize_tag_version(tag_name)
        if version:
            return GitHubReleaseVersion(version=version, source="github-tag", release_url=f"https://github.com/{repository}/releases/tag/v{version}")
        raise RuntimeError("no release or tag version found")

    def _gh_api(self, endpoint: str, *, timeout: int) -> Any:
        result = self.runner(["gh", "api", endpoint])
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "gh api failed").strip())
        try:
            return json.loads(result.stdout or "null")
        except json.JSONDecodeError as exc:
            raise RuntimeError("invalid gh api JSON") from exc

    def _run(self, cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        timeout = env_int(self.ctx.host_env.get("UPDATE_CHECK_TIMEOUT_SECONDS"), DEFAULT_TIMEOUT_SECONDS)
        return subprocess.run(list(cmd), cwd=self.ctx.repo_root, capture_output=True, text=True, check=False, timeout=timeout)

    def _write_state(
        self,
        *,
        status: str,
        checked_at: datetime,
        reason: str | None = None,
        local_version: str | None = None,
        latest_version: str | None = None,
        update_available: bool | None = None,
        update_source: str | None = None,
        release_url: str | None = None,
        startup: bool = False,
        interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": status,
            "checked_at": isoformat(checked_at),
            "startup": startup,
            "interval_seconds": interval_seconds,
            "manifest": MANIFEST_RELATIVE,
            "authority": ["read-source", "read-gh", "write-state"],
            "apply": "host-owned",
        }
        if reason:
            payload["reason"] = reason
        if local_version:
            payload["local_version"] = local_version
        if latest_version:
            payload["latest_version"] = latest_version
        if update_available is not None:
            payload["update_available"] = update_available
        if update_source:
            payload["update_source"] = update_source
        if release_url:
            payload["release_url"] = release_url
        write_json(self.state_path, payload)
        return payload


def normalize_tag_version(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate.startswith("v"):
        candidate = candidate[1:]
    try:
        compare_semver(candidate, candidate)
    except ValueError:
        return None
    return candidate


def maybe_run_update_check(ctx: LoopContext | None = None, *, startup: bool = False) -> dict[str, Any]:
    if ctx is None:
        ctx = LoopContext.load(cwd=os.getcwd())
    return UpdateCheckProbe(ctx).maybe_run(startup=startup)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--startup", action="store_true", help="record this as a startup probe")
    args = parser.parse_args(argv)
    try:
        result = maybe_run_update_check(startup=args.startup)
    except LoopContextError as exc:
        sys.stderr.write(f"FATAL: {exc}\n")
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
