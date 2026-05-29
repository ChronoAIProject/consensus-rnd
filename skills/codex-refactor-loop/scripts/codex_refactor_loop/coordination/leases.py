"""Git-ref CAS leases for multi-device loop coordination.

The lease ref payload is the only authoritative cross-device state. GitHub
comments or labels may mirror it for humans, but callers must never read them
as lease truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Sequence

from ..context import LoopContext, LoopContextError


LEASE_SCHEMA = "LoopLease"
LEASE_REF_PREFIX = "refs/heads/auto-loop/leases"
ALLOWED_SCOPES = frozenset({"work-claim", "singleton"})
NULL_SHA = "0" * 40
CLOCK_SKEW_SECONDS = 60


@dataclass(frozen=True)
class LeaseRecord:
    schema: str
    scope: str
    key_hash: str
    owner_device_id: str
    token: str
    target: str
    reason: str
    acquired_at: str
    renewed_at: str
    expires_at: str
    expected_previous_sha: str
    ref: str
    sha: str

    @classmethod
    def from_json(cls, payload: dict[str, object], *, ref: str, sha: str) -> "LeaseRecord":
        return cls(
            schema=str(payload.get("schema") or ""),
            scope=str(payload.get("scope") or ""),
            key_hash=str(payload.get("key_hash") or ""),
            owner_device_id=str(payload.get("owner_device_id") or ""),
            token=str(payload.get("token") or ""),
            target=str(payload.get("target") or ""),
            reason=str(payload.get("reason") or ""),
            acquired_at=str(payload.get("acquired_at") or ""),
            renewed_at=str(payload.get("renewed_at") or ""),
            expires_at=str(payload.get("expires_at") or ""),
            expected_previous_sha=str(payload.get("expected_previous_sha") or ""),
            ref=ref,
            sha=sha,
        )

    def to_payload(self) -> dict[str, str]:
        return {
            "schema": self.schema,
            "scope": self.scope,
            "key_hash": self.key_hash,
            "owner_device_id": self.owner_device_id,
            "token": self.token,
            "target": self.target,
            "reason": self.reason,
            "acquired_at": self.acquired_at,
            "renewed_at": self.renewed_at,
            "expires_at": self.expires_at,
            "expected_previous_sha": self.expected_previous_sha,
        }

    def expired(self, now: datetime | None = None, *, skew_seconds: int = CLOCK_SKEW_SECONDS) -> bool:
        parsed = _parse_utc(self.expires_at)
        if parsed is None:
            return True
        now = now or datetime.now(timezone.utc)
        return parsed < now - timedelta(seconds=skew_seconds)


@dataclass(frozen=True)
class LeaseToken:
    scope: str
    key: str
    token: str
    ref: str


@dataclass(frozen=True)
class LeaseDecision:
    acquired: bool
    reason: str
    token: LeaseToken | None = None
    record: LeaseRecord | None = None
    current: LeaseRecord | None = None

    @property
    def owner_device_id(self) -> str | None:
        record = self.record or self.current
        return record.owner_device_id if record else None


class GitRefLeaseRegistry:
    """Lease registry backed by git ref compare-and-swap.

    Local tests exercise `git update-ref <ref> <new> <old>` directly. A live
    GitHub-backed registry can use `git push --force-with-lease=<ref>:<old>`
    through `use_remote=True`, still restricted to the lease namespace.
    """

    def __init__(
        self,
        ctx: LoopContext,
        *,
        command_runner: Callable[[Sequence[str], str | bytes | None], subprocess.CompletedProcess[str]] | None = None,
        before_cas_hook: Callable[[str, str], None] | None = None,
        now_provider: Callable[[], datetime] | None = None,
        use_remote: bool | None = None,
        remote: str = "origin",
    ) -> None:
        if ctx.multi_device_coordination and not ctx.device_id:
            raise LoopContextError("multi-device coordination requires a valid AUTO_LOOP_DEVICE_ID")
        self.ctx = ctx
        self.repo_root = ctx.repo_root
        self.device_id = ctx.device_id
        self.command_runner = command_runner
        self.before_cas_hook = before_cas_hook
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.use_remote = bool(use_remote)
        self.remote = remote
        self._lock = threading.Lock()

    def enabled(self) -> bool:
        return self.ctx.multi_device_coordination

    def acquire(
        self,
        scope: str,
        key: str,
        ttl_seconds: int | None = None,
        reason: str = "",
        target: str = "",
    ) -> LeaseDecision:
        if not self.enabled():
            return LeaseDecision(True, "multi-device-disabled")
        scope = _validate_scope(scope)
        ref = self.ref_for(scope, key)
        current = self.current(scope, key)
        now = self.now_provider()
        ttl = ttl_seconds or self.ctx.lease_ttl_seconds
        if current and not current.expired(now):
            if current.owner_device_id != self.device_id:
                return LeaseDecision(False, f"leased-by:{current.owner_device_id}", current=current)
            return self._write_record(
                scope=scope,
                key=key,
                ref=ref,
                old_sha=current.sha,
                token=current.token,
                acquired_at=current.acquired_at,
                ttl_seconds=ttl,
                reason=reason or current.reason,
                target=target or current.target,
                decision_reason="renewed",
                current=current,
            )
        return self._write_record(
            scope=scope,
            key=key,
            ref=ref,
            old_sha=current.sha if current else NULL_SHA,
            token=uuid.uuid4().hex,
            acquired_at=_format_utc(now),
            ttl_seconds=ttl,
            reason=reason,
            target=target,
            decision_reason="expired-takeover" if current else "claimed",
            current=current,
        )

    def renew(self, token: LeaseToken, ttl_seconds: int | None = None) -> LeaseDecision:
        if not self.enabled():
            return LeaseDecision(True, "multi-device-disabled", token=token)
        current = self.current(token.scope, token.key)
        if current is None:
            return LeaseDecision(False, "missing-lease")
        if current.owner_device_id != self.device_id or current.token != token.token:
            return LeaseDecision(False, f"leased-by:{current.owner_device_id}", current=current)
        return self._write_record(
            scope=token.scope,
            key=token.key,
            ref=token.ref,
            old_sha=current.sha,
            token=current.token,
            acquired_at=current.acquired_at,
            ttl_seconds=ttl_seconds or self.ctx.lease_ttl_seconds,
            reason=current.reason,
            target=current.target,
            decision_reason="renewed",
            current=current,
        )

    def release(self, token: LeaseToken) -> LeaseDecision:
        if not self.enabled():
            return LeaseDecision(True, "multi-device-disabled", token=token)
        current = self.current(token.scope, token.key)
        if current is None:
            return LeaseDecision(True, "missing-lease")
        if current.owner_device_id != self.device_id or current.token != token.token:
            return LeaseDecision(False, f"leased-by:{current.owner_device_id}", current=current)
        if not self._update_ref(token.ref, "", current.sha):
            return LeaseDecision(False, "cas-lost", current=self.current(token.scope, token.key))
        return LeaseDecision(True, "released", token=token, current=current)

    def current(self, scope: str, key: str) -> LeaseRecord | None:
        scope = _validate_scope(scope)
        ref = self.ref_for(scope, key)
        rev = self._git(["rev-parse", "--verify", ref], check=False)
        if rev.returncode != 0:
            return None
        sha = rev.stdout.strip()
        shown = self._git(["show", f"{sha}:lease.json"], check=False)
        if shown.returncode != 0:
            return None
        try:
            payload = json.loads(shown.stdout)
        except json.JSONDecodeError:
            return None
        record = LeaseRecord.from_json(payload, ref=ref, sha=sha)
        if record.schema != LEASE_SCHEMA or record.scope != scope or record.key_hash != self.key_hash(scope, key):
            return None
        return record

    def list_records(self, limit: int = 20) -> list[LeaseRecord]:
        result = self._git(["for-each-ref", f"{LEASE_REF_PREFIX}/", "--format=%(refname) %(objectname)"], check=False)
        if result.returncode != 0:
            return []
        records: list[LeaseRecord] = []
        for line in result.stdout.splitlines():
            ref, _, sha = line.partition(" ")
            if not ref or not sha:
                continue
            shown = self._git(["show", f"{sha}:lease.json"], check=False)
            if shown.returncode != 0:
                continue
            try:
                payload = json.loads(shown.stdout)
            except json.JSONDecodeError:
                continue
            record = LeaseRecord.from_json(payload, ref=ref, sha=sha)
            if record.schema == LEASE_SCHEMA and record.scope in ALLOWED_SCOPES:
                records.append(record)
        return sorted(records, key=lambda item: item.expires_at)[:limit]

    @staticmethod
    def key_hash(scope: str, key: str) -> str:
        if not key or "\n" in key or "\x00" in key:
            raise ValueError("lease key must be non-empty and single-line")
        return hashlib.sha256(f"{scope}:{key}".encode("utf-8")).hexdigest()

    @classmethod
    def ref_for(cls, scope: str, key: str) -> str:
        scope = _validate_scope(scope)
        return f"{LEASE_REF_PREFIX}/{scope}/{cls.key_hash(scope, key)}"

    def _write_record(
        self,
        *,
        scope: str,
        key: str,
        ref: str,
        old_sha: str,
        token: str,
        acquired_at: str,
        ttl_seconds: int,
        reason: str,
        target: str,
        decision_reason: str,
        current: LeaseRecord | None,
    ) -> LeaseDecision:
        if not self.device_id:
            return LeaseDecision(False, "missing-device-id", current=current)
        now = self.now_provider()
        renewed_at = _format_utc(now)
        payload = {
            "schema": LEASE_SCHEMA,
            "scope": scope,
            "key_hash": self.key_hash(scope, key),
            "owner_device_id": self.device_id,
            "token": token,
            "target": target,
            "reason": reason,
            "acquired_at": acquired_at,
            "renewed_at": renewed_at,
            "expires_at": _format_utc(now + timedelta(seconds=ttl_seconds)),
            "expected_previous_sha": old_sha,
        }
        new_sha = self._lease_commit(payload)
        if self.before_cas_hook:
            self.before_cas_hook(ref, old_sha)
        if not self._update_ref(ref, new_sha, old_sha):
            return LeaseDecision(False, "cas-lost", current=self.current(scope, key))
        record = LeaseRecord.from_json(payload, ref=ref, sha=new_sha)
        return LeaseDecision(True, decision_reason, token=LeaseToken(scope, key, token, ref), record=record, current=current)

    def _lease_commit(self, payload: dict[str, str]) -> str:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        blob = self._git(["hash-object", "-w", "--stdin"], input=data).stdout.strip()
        tree_input = f"100644 blob {blob}\tlease.json\n"
        tree = self._git(["mktree"], input=tree_input).stdout.strip()
        message = f"auto-loop lease {payload['scope']} {payload['key_hash']}"
        return self._git(["commit-tree", tree, "-m", message]).stdout.strip()

    def _update_ref(self, ref: str, new_sha: str, old_sha: str) -> bool:
        if not ref.startswith(f"{LEASE_REF_PREFIX}/"):
            raise ValueError(f"ref outside lease namespace: {ref}")
        with self._lock:
            if self.use_remote:
                result = self._push_ref(ref, new_sha, old_sha)
            elif new_sha:
                result = self._git(["update-ref", ref, new_sha, old_sha], check=False)
            else:
                result = self._git(["update-ref", "-d", ref, old_sha], check=False)
        return result.returncode == 0

    def _push_ref(self, ref: str, new_sha: str, old_sha: str) -> subprocess.CompletedProcess[str]:
        if not ref.startswith(f"{LEASE_REF_PREFIX}/"):
            raise ValueError(f"ref outside lease namespace: {ref}")
        if not new_sha:
            return self._git(["push", self.remote, f":{ref}", f"--force-with-lease={ref}:{old_sha}"], check=False)
        return self._git(
            ["push", self.remote, f"{new_sha}:{ref}", f"--force-with-lease={ref}:{old_sha}"],
            check=False,
        )

    def _git(
        self,
        args: Sequence[str],
        *,
        input: str | bytes | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        if self.command_runner:
            result = self.command_runner(["git", "-C", str(self.repo_root), *args], input)
        else:
            env = dict(os.environ)
            env.setdefault("GIT_AUTHOR_NAME", "consensus-rnd lease")
            env.setdefault("GIT_AUTHOR_EMAIL", "lease@consensus-rnd.local")
            env.setdefault("GIT_COMMITTER_NAME", "consensus-rnd lease")
            env.setdefault("GIT_COMMITTER_EMAIL", "lease@consensus-rnd.local")
            result = subprocess.run(
                ["git", "-C", str(self.repo_root), *args],
                input=input,
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed")
        return result


class LeaseGate:
    """Thin call-site gate exposing only the authorized lease scopes."""

    def __init__(self, registry: GitRefLeaseRegistry) -> None:
        self.registry = registry

    @classmethod
    def from_context(cls, ctx: LoopContext) -> "LeaseGate":
        return cls(GitRefLeaseRegistry(ctx))

    def work_claim(self, key: str, *, reason: str = "", target: str = "") -> LeaseDecision:
        return self.registry.acquire("work-claim", key, self.registry.ctx.lease_ttl_seconds, reason, target)

    def singleton(self, key: str, *, reason: str = "", target: str = "") -> LeaseDecision:
        return self.registry.acquire("singleton", key, self.registry.ctx.lease_ttl_seconds, reason, target)

    def renew(self, token: LeaseToken) -> LeaseDecision:
        return self.registry.renew(token)

    def release(self, token: LeaseToken) -> LeaseDecision:
        return self.registry.release(token)


def lease_projection_comment(record: LeaseRecord) -> str:
    """Return an optional human projection; callers must not read it as truth."""
    return (
        "## 🤖 lease projection\n\n"
        f"scope={record.scope} owner={record.owner_device_id} expires_at={record.expires_at}\n\n"
        f"<!-- consensus-rnd:lease-projection ref={record.ref} sha={record.sha} -->\n"
        "⟦AI:AUTO-LOOP⟧\n"
    )


def _validate_scope(scope: str) -> str:
    if scope not in ALLOWED_SCOPES:
        raise ValueError(f"unsupported lease scope: {scope}")
    return scope


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="inspect or acquire git-ref CAS leases")
    sub = parser.add_subparsers(dest="command", required=True)
    current = sub.add_parser("current")
    current.add_argument("scope", choices=sorted(ALLOWED_SCOPES))
    current.add_argument("key")
    acquire = sub.add_parser("acquire")
    acquire.add_argument("scope", choices=sorted(ALLOWED_SCOPES))
    acquire.add_argument("key")
    acquire.add_argument("--reason", default="")
    acquire.add_argument("--target", default="")
    args = parser.parse_args(argv)
    try:
        ctx = LoopContext.load(cwd=os.getcwd())
        registry = GitRefLeaseRegistry(ctx)
        if args.command == "current":
            record = registry.current(args.scope, args.key)
            print(json.dumps(record.to_payload() if record else None, sort_keys=True))
            return 0
        if args.command == "acquire":
            decision = registry.acquire(args.scope, args.key, reason=args.reason, target=args.target)
            payload = {
                "acquired": decision.acquired,
                "reason": decision.reason,
                "owner_device_id": decision.owner_device_id,
            }
            print(json.dumps(payload, sort_keys=True))
            return 0 if decision.acquired else 1
    except (LoopContextError, RuntimeError, ValueError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
