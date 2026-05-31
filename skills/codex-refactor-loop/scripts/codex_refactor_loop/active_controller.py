"""Single active-controller lease for cross-device controller writes."""

from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Sequence

from .context import LoopContext


DEFAULT_ACTIVE_CONTROLLER_REF = "refs/heads/crnd/active-controller"
DEFAULT_ACTIVE_CONTROLLER_TTL_SECONDS = 1800
LEASE_BLOB_PATH = "active-controller.json"
ZERO_SHA = "0" * 40


@dataclass(frozen=True)
class ActiveControllerLease:
    owner_device: str
    lease_id: str
    acquired_at: str
    expires_at: str
    renewed_at: str
    repo: str
    reason: str
    source_issue: str

    @classmethod
    def from_json(cls, data: object) -> "ActiveControllerLease | None":
        if not isinstance(data, dict):
            return None
        required = ("owner_device", "lease_id", "acquired_at", "expires_at", "renewed_at", "repo", "reason", "source_issue")
        if not all(isinstance(data.get(key), str) and data.get(key) for key in required):
            return None
        return cls(**{key: str(data[key]) for key in required})

    def to_json(self) -> dict[str, str]:
        return {
            "owner_device": self.owner_device,
            "lease_id": self.lease_id,
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
            "renewed_at": self.renewed_at,
            "repo": self.repo,
            "reason": self.reason,
            "source_issue": self.source_issue,
        }

    def expired(self, now: datetime | None = None) -> bool:
        parsed = _parse_time(self.expires_at)
        if parsed is None:
            return True
        return parsed <= (now or _now())


@dataclass(frozen=True)
class LeaseDecision:
    allowed: bool
    status: str
    action: str
    owner_device: str
    lease_id: str = ""
    expires_at: str = ""
    reason: str = ""

    def raise_if_denied(self) -> None:
        if not self.allowed:
            raise RuntimeError(f"active controller lease denied for {self.action}: {self.status} owner={self.owner_device}")


@dataclass(frozen=True)
class _LeaseRead:
    lease: ActiveControllerLease | None
    commit_sha: str | None
    status: str


class ActiveControllerLeaseStore:
    def __init__(
        self,
        repo_root: Path,
        *,
        owner_device: str = "",
        lease_ref: str = DEFAULT_ACTIVE_CONTROLLER_REF,
        ttl_seconds: int = DEFAULT_ACTIVE_CONTROLLER_TTL_SECONDS,
        repo_slug: str = "",
        source_issue: str = "191",
        command_runner: Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.owner_device = owner_device.strip()
        self.lease_ref = lease_ref.strip() or DEFAULT_ACTIVE_CONTROLLER_REF
        self.ttl_seconds = ttl_seconds if ttl_seconds > 0 else DEFAULT_ACTIVE_CONTROLLER_TTL_SECONDS
        self.repo_slug = repo_slug
        self.source_issue = source_issue
        self._run = command_runner or self._default_runner

    @classmethod
    def from_context(cls, ctx: LoopContext) -> "ActiveControllerLeaseStore":
        env = ctx.env_for_subprocess()
        try:
            ttl = int(env.get("ACTIVE_CONTROLLER_TTL_SECONDS", str(DEFAULT_ACTIVE_CONTROLLER_TTL_SECONDS)))
        except ValueError:
            ttl = DEFAULT_ACTIVE_CONTROLLER_TTL_SECONDS
        return cls(
            ctx.repo_root,
            owner_device=env.get("ACTIVE_CONTROLLER_DEVICE_ID", ""),
            lease_ref=DEFAULT_ACTIVE_CONTROLLER_REF,
            ttl_seconds=ttl,
            repo_slug=ctx.gh_repo_slug or "",
        )

    def read(self) -> ActiveControllerLease | None:
        return self._read_remote().lease if self.enabled else self._local_lease("read")

    def try_acquire(self, owner: str, ttl_seconds: int) -> LeaseDecision:
        if not self.enabled:
            return self._local_decision("acquire")
        owner = owner.strip()
        if not owner:
            return LeaseDecision(False, "missing-owner", "acquire", "")
        current = self._read_remote()
        if current.status == "ambiguous":
            return LeaseDecision(False, "ambiguous", "acquire", "")
        now = _now()
        if current.lease and not current.lease.expired(now) and current.lease.owner_device != owner:
            return LeaseDecision(
                False,
                "not-owner",
                "acquire",
                current.lease.owner_device,
                current.lease.lease_id,
                current.lease.expires_at,
            )
        lease_id = current.lease.lease_id if current.lease and current.lease.owner_device == owner else str(uuid.uuid4())
        acquired_at = current.lease.acquired_at if current.lease and current.lease.owner_device == owner else _format_time(now)
        lease = ActiveControllerLease(
            owner_device=owner,
            lease_id=lease_id,
            acquired_at=acquired_at,
            renewed_at=_format_time(now),
            expires_at=_format_time(now + timedelta(seconds=max(1, ttl_seconds))),
            repo=self.repo_slug,
            reason="single-active-controller",
            source_issue=self.source_issue,
        )
        if not self._write_remote(lease, current.commit_sha):
            refreshed = self._read_remote().lease
            owner_device = refreshed.owner_device if refreshed else ""
            return LeaseDecision(False, "cas-conflict", "acquire", owner_device, refreshed.lease_id if refreshed else "")
        return LeaseDecision(True, "owner", "acquire", owner, lease.lease_id, lease.expires_at)

    def renew(self, lease_id: str) -> LeaseDecision:
        if not self.enabled:
            return self._local_decision("renew")
        current = self._read_remote()
        if current.lease is None:
            return LeaseDecision(False, "missing-lease", "renew", "")
        if current.lease.lease_id != lease_id or current.lease.owner_device != self.owner_device:
            return LeaseDecision(False, "not-owner", "renew", current.lease.owner_device, current.lease.lease_id, current.lease.expires_at)
        if current.lease.expired():
            return LeaseDecision(False, "expired", "renew", current.lease.owner_device, current.lease.lease_id, current.lease.expires_at)
        now = _now()
        lease = ActiveControllerLease(
            owner_device=current.lease.owner_device,
            lease_id=current.lease.lease_id,
            acquired_at=current.lease.acquired_at,
            renewed_at=_format_time(now),
            expires_at=_format_time(now + timedelta(seconds=self.ttl_seconds)),
            repo=current.lease.repo,
            reason=current.lease.reason,
            source_issue=current.lease.source_issue,
        )
        if not self._write_remote(lease, current.commit_sha):
            return LeaseDecision(False, "cas-conflict", "renew", current.lease.owner_device, current.lease.lease_id)
        return LeaseDecision(True, "owner", "renew", lease.owner_device, lease.lease_id, lease.expires_at)

    def is_owner(self) -> bool:
        return self.require_owner("is-owner").allowed

    def require_owner(self, action: str) -> LeaseDecision:
        if not self.enabled:
            return self._local_decision(action)
        decision = self.try_acquire(self.owner_device, self.ttl_seconds)
        return LeaseDecision(
            decision.allowed,
            decision.status,
            action,
            decision.owner_device,
            decision.lease_id,
            decision.expires_at,
            decision.reason,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.owner_device)

    def _read_remote(self) -> _LeaseRead:
        fetch = self._git(["fetch", "origin", self.lease_ref])
        if fetch.returncode != 0:
            exists = self._git(["ls-remote", "--exit-code", "--heads", "origin", self.lease_ref])
            if exists.returncode not in (0, 2):
                return _LeaseRead(None, None, "ambiguous")
            return _LeaseRead(None, None, "missing")
        rev = self._git(["rev-parse", "--verify", "FETCH_HEAD^{commit}"])
        if rev.returncode != 0 or not rev.stdout.strip():
            return _LeaseRead(None, None, "ambiguous")
        commit_sha = rev.stdout.strip()
        show = self._git(["show", f"{commit_sha}:{LEASE_BLOB_PATH}"])
        if show.returncode != 0:
            return _LeaseRead(None, commit_sha, "missing-blob")
        try:
            lease = ActiveControllerLease.from_json(json.loads(show.stdout))
        except json.JSONDecodeError:
            lease = None
        return _LeaseRead(lease, commit_sha, "ok" if lease else "invalid")

    def _write_remote(self, lease: ActiveControllerLease, old_sha: str | None) -> bool:
        blob = self._git(["hash-object", "-w", "--stdin"], input_text=json.dumps(lease.to_json(), sort_keys=True) + "\n")
        if blob.returncode != 0 or not blob.stdout.strip():
            return False
        tree_input = f"100644 blob {blob.stdout.strip()}\t{LEASE_BLOB_PATH}\n"
        tree = self._git(["mktree"], input_text=tree_input)
        if tree.returncode != 0 or not tree.stdout.strip():
            return False
        commit = self._git(
            [
                "-c",
                "user.name=codex-refactor-loop",
                "-c",
                "user.email=codex-refactor-loop@example.invalid",
                "commit-tree",
                tree.stdout.strip(),
                "-m",
                "Update active controller lease",
            ]
        )
        if commit.returncode != 0 or not commit.stdout.strip():
            return False
        expected = old_sha or ""
        push = self._git(
            [
                "push",
                "origin",
                f"{commit.stdout.strip()}:{self.lease_ref}",
                f"--force-with-lease={self.lease_ref}:{expected}",
            ]
        )
        return push.returncode == 0

    def _git(self, args: Sequence[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        return self._run(["git", "-C", str(self.repo_root), *args], self.repo_root) if input_text is None else subprocess.run(
            ["git", "-C", str(self.repo_root), *args],
            cwd=str(self.repo_root),
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
        )

    @staticmethod
    def _default_runner(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(list(command), cwd=str(cwd), capture_output=True, text=True, check=False)

    def _local_lease(self, action: str) -> ActiveControllerLease:
        now = _now()
        return ActiveControllerLease(
            owner_device="local-single-device",
            lease_id="local-single-device",
            acquired_at=_format_time(now),
            renewed_at=_format_time(now),
            expires_at=_format_time(now + timedelta(seconds=self.ttl_seconds)),
            repo=self.repo_slug,
            reason=f"default-single-device-noop:{action}",
            source_issue=self.source_issue,
        )

    def _local_decision(self, action: str) -> LeaseDecision:
        lease = self._local_lease(action)
        return LeaseDecision(True, "local-owner", action, lease.owner_device, lease.lease_id, lease.expires_at)


def require_active_controller(ctx: LoopContext, action: str) -> LeaseDecision:
    return ActiveControllerLeaseStore.from_context(ctx).require_owner(action)


def write_active_controller_status(ctx: LoopContext, decision: LeaseDecision) -> None:
    path = ctx.paths.state / "active-controller-status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "active_controller": "owner" if decision.allowed else f"noop:{decision.status}",
        "action": decision.action,
        "owner_device": decision.owner_device,
        "lease_id": decision.lease_id,
        "expires_at": decision.expires_at,
        "updated_at": _format_time(_now()),
    }
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
