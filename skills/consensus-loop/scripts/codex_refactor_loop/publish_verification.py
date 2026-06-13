"""Helper-private publish implementation verification evidence."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .processes import run_fixed_host_command


VERIFY_VERSION = 1
VERIFY_COMMANDS = ("BUILD_CMD", "TEST_CMD")


@dataclass(frozen=True)
class PublishVerificationIdentity:
    issue: str
    action: str
    head_ref: str
    worktree: Path
    head_sha: str
    command_digest: str

    def to_json(self) -> dict[str, str]:
        return {
            "issue": self.issue,
            "action": self.action,
            "head_ref": self.head_ref,
            "worktree": str(self.worktree.resolve()),
            "head_sha": self.head_sha,
            "command_digest": self.command_digest,
        }


@dataclass(frozen=True)
class PublishVerificationResult:
    status: str
    reason: str
    evidence_file: Path

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def command_digest(env: Mapping[str, str], names: Sequence[str] = VERIFY_COMMANDS) -> str:
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(env.get(name) or "").strip().encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return digest.hexdigest()


def evidence_path(repo_root: Path, issue: str, head_ref: str) -> Path:
    safe_head = head_ref.replace("/", "__")
    return repo_root / ".refactor-loop" / "state" / "publish-verification" / f"issue-{issue}-{safe_head}.json"


def verify_or_run(
    *,
    repo_root: Path,
    worktree: Path,
    issue: str,
    action: str,
    head_ref: str,
    head_sha: str,
    env: Mapping[str, str],
) -> PublishVerificationResult:
    identity = PublishVerificationIdentity(
        issue=issue,
        action=action,
        head_ref=head_ref,
        worktree=worktree.resolve(),
        head_sha=head_sha,
        command_digest=command_digest(env),
    )
    path = evidence_path(repo_root, issue, head_ref)
    if _evidence_matches(path, identity):
        return PublishVerificationResult("ok", "reused", path)
    payload: dict[str, Any] = {
        "version": VERIFY_VERSION,
        **identity.to_json(),
        "commands": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(path, payload)
    for command_name in VERIFY_COMMANDS:
        command = str(env.get(command_name) or "").strip()
        if not command:
            payload["status"] = "failed"
            payload["reason"] = f"missing-{command_name}"
            _write_json(path, payload)
            return PublishVerificationResult("failed", payload["reason"], path)
        log = path.with_name(f"{path.stem}-{command_name}.log")
        exit_code = run_fixed_host_command(command, cwd=worktree, env=env, log=log)
        command_record = {
            "name": command_name,
            "command_sha256": _string_digest(command),
            "exit": exit_code,
            "log": _repo_relative(repo_root, log),
            "exit_marker": _log_has_exit_zero(log),
        }
        payload["commands"].append(command_record)
        if exit_code != 0 or not command_record["exit_marker"]:
            payload["status"] = "failed"
            payload["reason"] = f"{command_name}-failed:{exit_code}"
            _write_json(path, payload)
            return PublishVerificationResult("failed", payload["reason"], path)
        _write_json(path, payload)
    payload["status"] = "ok"
    payload["reason"] = "verified"
    _write_json(path, payload)
    return PublishVerificationResult("ok", "verified", path)


def _evidence_matches(path: Path, identity: PublishVerificationIdentity) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return False
    expected = identity.to_json()
    for key, value in expected.items():
        if payload.get(key) != value:
            return False
    commands = payload.get("commands")
    if not isinstance(commands, list) or len(commands) != len(VERIFY_COMMANDS):
        return False
    by_name = {item.get("name"): item for item in commands if isinstance(item, dict)}
    for name in VERIFY_COMMANDS:
        item = by_name.get(name)
        if not isinstance(item, dict) or item.get("exit") != 0 or item.get("exit_marker") is not True:
            return False
    return True


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _log_has_exit_zero(path: Path) -> bool:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-5:]
    except OSError:
        return False
    return any(line == "EXIT=0" for line in lines)


def _string_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _repo_relative(repo_root: Path, path: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


__all__ = [
    "PublishVerificationIdentity",
    "PublishVerificationResult",
    "command_digest",
    "evidence_path",
    "verify_or_run",
]
