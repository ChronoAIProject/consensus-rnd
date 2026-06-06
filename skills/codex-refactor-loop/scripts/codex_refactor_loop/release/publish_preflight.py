"""Controller-only release publication preflight."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from ..state import read_json
from .coordinates import validate_coordinate_policy
from .gate import isoformat, load_host_env, parse_time, resolve_field
from .required_checks import required_release_checks
from .versions import compare_semver


REQUIRED_CANDIDATE_SCHEMA = "decision-artifact-only/v2"
PUBLISH_PREFLIGHT_NAME = "controller-release-publish-preflight"


@dataclass(frozen=True)
class PublishPreflightResult:
    """Release publication authorization result.

    """

    allowed: bool
    reasons: tuple[str, ...]
    candidate: dict[str, Any]
    decision: dict[str, Any]
    candidate_path: Path
    decision_path: Path
    target_ref: str
    version: str
    candidate_digest: str


class ReleasePublishPreflight:
    """Validate controller release publication inputs before mutation.

    """

    def __init__(
        self,
        repo_root: Path,
        *,
        now: Callable[[], datetime] | None = None,
        max_age_minutes: int = 120,
    ) -> None:
        self.repo_root = repo_root
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.max_age_minutes = max_age_minutes

    def validate(
        self,
        *,
        candidate_path: str | Path = ".refactor-loop/state/release-candidate.json",
        target_ref: str,
        manifest_version: str | None = None,
    ) -> PublishPreflightResult:
        reasons: list[str] = []
        candidate_file, candidate_path_error = self._resolve_repo_path(candidate_path)
        if candidate_path_error:
            reasons.append(candidate_path_error)
        raw_candidate = None if candidate_path_error else read_json(candidate_file, None)
        candidate = raw_candidate if isinstance(raw_candidate, dict) else {}
        if not candidate:
            reasons.append("missing_candidate")

        decision_file = self._decision_path(candidate)
        raw_decision = read_json(decision_file, None) if decision_file is not None else None
        decision = raw_decision if isinstance(raw_decision, dict) else {}
        if not decision:
            reasons.append("missing_decision")
            decision_file = self.repo_root / ".refactor-loop/state/release-decision.json" if decision_file is None else decision_file

        self._validate_candidate_schema(candidate, reasons)
        self._validate_decision(candidate, decision, reasons)
        self._validate_host_opt_in(reasons)
        self._validate_time(candidate, reasons)
        self._validate_target_ref(candidate, target_ref, reasons)
        version = manifest_version if manifest_version is not None else self._current_manifest_version(reasons)
        self._validate_version(candidate, decision, version, reasons)
        self._validate_required_signals(candidate, decision, reasons)
        self._validate_decision_digest(candidate, decision, reasons)
        digest = canonical_digest(candidate) if candidate else ""

        return PublishPreflightResult(
            allowed=not reasons,
            reasons=tuple(reasons),
            candidate=candidate,
            decision=decision,
            candidate_path=candidate_file,
            decision_path=decision_file or (self.repo_root / ".refactor-loop/state/release-decision.json"),
            target_ref=str(candidate.get("target_ref") or target_ref or ""),
            version=str(candidate.get("to_version") or version or ""),
            candidate_digest=digest,
        )

    def _resolve_repo_path(self, path: str | Path) -> tuple[Path, str | None]:
        candidate = Path(path)
        if candidate.is_absolute():
            return candidate, "candidate_path_absolute"
        if ".." in candidate.parts:
            return self.repo_root / candidate, "candidate_path_outside_repo"
        resolved = (self.repo_root / candidate).resolve()
        try:
            resolved.relative_to(self.repo_root.resolve())
        except ValueError:
            return resolved, "candidate_path_outside_repo"
        return resolved, None

    def _decision_path(self, candidate: dict[str, Any]) -> Path | None:
        raw = candidate.get("decision_artifact")
        if not isinstance(raw, str) or not raw:
            return None
        path = Path(raw)
        if path.is_absolute() or ".." in path.parts:
            return None
        return self.repo_root / path

    def _validate_candidate_schema(self, candidate: dict[str, Any], reasons: list[str]) -> None:
        required = (
            "target_ref",
            "expires_at",
            "required_signals",
            "decision_digest",
            "publish_preflight",
            "lifecycle_owner",
        )
        if candidate.get("schema") != REQUIRED_CANDIDATE_SCHEMA:
            reasons.append("old_candidate_schema")
        missing = [field for field in required if field not in candidate]
        if missing:
            reasons.append("missing_candidate_fields:" + ",".join(missing))
        if candidate.get("publish_preflight") != PUBLISH_PREFLIGHT_NAME:
            reasons.append("publish_preflight_mismatch")
        if candidate.get("lifecycle_owner") != "controller":
            reasons.append("lifecycle_owner_not_controller")

    def _validate_decision(self, candidate: dict[str, Any], decision: dict[str, Any], reasons: list[str]) -> None:
        if candidate.get("ready") is not True:
            reasons.append("candidate_not_ready")
        if decision and decision.get("ready") is not True:
            reasons.append("decision_not_ready")
        if decision and candidate.get("to_version") != decision.get("to_version"):
            reasons.append("candidate_decision_version_mismatch")
        if decision and candidate.get("from_version") != decision.get("from_version"):
            reasons.append("candidate_decision_from_version_mismatch")
        if (
            decision
            and candidate.get("coordinate_policy") is not None
            and decision.get("coordinate_policy") is not None
            and candidate.get("coordinate_policy") != decision.get("coordinate_policy")
        ):
            reasons.append("candidate_decision_coordinate_policy_mismatch")

    def _validate_host_opt_in(self, reasons: list[str]) -> None:
        host_env = load_host_env(self.repo_root)
        if host_env.get("RELEASE_AUTO_ENABLE") != "true":
            reasons.append("host_opt_in_not_true")

    def _validate_time(self, candidate: dict[str, Any], reasons: list[str]) -> None:
        now = self.now()
        generated_at = parse_time(candidate.get("generated_at"))
        expires_at = parse_time(candidate.get("expires_at"))
        if generated_at is None:
            reasons.append("candidate_generated_at_invalid")
        elif generated_at > now or now - generated_at > timedelta(minutes=self.max_age_minutes):
            reasons.append("candidate_stale")
        if expires_at is None:
            reasons.append("candidate_expires_at_invalid")
        elif expires_at <= now:
            reasons.append("candidate_expired")

    def _validate_target_ref(self, candidate: dict[str, Any], target_ref: str, reasons: list[str]) -> None:
        expected = str(candidate.get("target_ref") or "")
        if not expected:
            reasons.append("target_ref_missing")
        elif expected != target_ref:
            reasons.append("target_ref_mismatch")

    def _current_manifest_version(self, reasons: list[str]) -> str:
        try:
            targets = load_manifest_targets(self.repo_root)
            versions = {target["version"] for target in targets}
        except Exception as exc:
            reasons.append(f"manifest_version_unreadable:{exc}")
            return ""
        if len(versions) != 1:
            reasons.append("manifest_versions_not_synchronized")
            return ""
        return next(iter(versions))

    def _validate_version(self, candidate: dict[str, Any], decision: dict[str, Any], version: str, reasons: list[str]) -> None:
        to_version = candidate.get("to_version")
        if not isinstance(to_version, str) or not to_version:
            reasons.append("candidate_version_missing")
            return
        from_version = candidate.get("from_version")
        if not isinstance(from_version, str) or not from_version:
            reasons.append("candidate_from_version_missing")
            return
        try:
            versions_match = compare_semver(version, from_version) == 0
        except ValueError:
            versions_match = False
        if not versions_match:
            reasons.append("manifest_version_mismatch")
        if decision and decision.get("to_version") != to_version:
            reasons.append("decision_version_mismatch")
        coordinate_reason = validate_coordinate_policy(
            from_version,
            to_version,
            candidate.get("bump_type"),
            candidate.get("coordinate_policy"),
            decision.get("coordinate_policy") if decision else None,
        )
        if coordinate_reason is not None:
            reasons.append(coordinate_reason)

    def _validate_required_signals(self, candidate: dict[str, Any], decision: dict[str, Any], reasons: list[str]) -> None:
        signals = candidate.get("required_signals")
        if not isinstance(signals, dict):
            reasons.append("required_signals_invalid")
            return
        for name, signal in signals.items():
            if not isinstance(signal, dict) or signal.get("passed") is not True:
                reasons.append(f"required_signal_red:{name}")
        checks = required_release_checks(load_host_env(self.repo_root))
        if not checks:
            reasons.append("missing_host_required_release_checks")
            return
        missing = [name for name in checks if not _required_check_is_green(signals, name)]
        if missing:
            reasons.append("required_check_red:" + ",".join(missing))
        decision_signals = decision.get("signals") if isinstance(decision, dict) else None
        if isinstance(decision_signals, dict):
            for name, signal in decision_signals.items():
                if not isinstance(signal, dict) or signal.get("passed") is not True:
                    reasons.append(f"decision_signal_red:{name}")

    def _validate_decision_digest(self, candidate: dict[str, Any], decision: dict[str, Any], reasons: list[str]) -> None:
        expected = candidate.get("decision_digest")
        if not isinstance(expected, str) or not expected:
            reasons.append("decision_digest_missing")
            return
        if canonical_digest(decision) != expected:
            reasons.append("decision_digest_mismatch")


def _required_check_is_green(signals: dict[str, Any], name: str) -> bool:
    required = signals.get("required_checks_recent_green")
    if not isinstance(required, dict) or required.get("passed") is not True:
        return False
    branches = required.get("branches")
    if isinstance(branches, dict):
        return bool(branches) and all(isinstance(checks, dict) and checks.get(name) is True for checks in branches.values())
    checks = required.get("checks")
    if isinstance(checks, dict):
        return checks.get(name) is True
    return False


def load_manifest_targets(repo_root: Path) -> list[dict[str, Any]]:
    mapping = read_json(repo_root / ".version-bump.json", {})
    files = mapping.get("files") if isinstance(mapping, dict) else None
    if not isinstance(files, list):
        raise RuntimeError(".version-bump.json: expected top-level files list")
    targets: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict):
            raise RuntimeError(".version-bump.json: files entries must be objects")
        relative = item.get("path")
        field = item.get("field")
        if not isinstance(relative, str) or not isinstance(field, str):
            raise RuntimeError(".version-bump.json: files entries require path and field")
        data = read_json(repo_root / relative, None)
        version = resolve_field(data, field)
        targets.append({"relative": relative, "field": field, "version": version})
    return targets


def canonical_digest(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def default_candidate_expiry(now: datetime) -> str:
    return isoformat(now + timedelta(minutes=120))


__all__ = [
    "PUBLISH_PREFLIGHT_NAME",
    "REQUIRED_CANDIDATE_SCHEMA",
    "PublishPreflightResult",
    "ReleasePublishPreflight",
    "canonical_digest",
    "default_candidate_expiry",
    "load_manifest_targets",
]
