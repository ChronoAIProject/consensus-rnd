"""Controller-internal release publisher."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Literal, Sequence

from ..state import write_json
from .gate import isoformat, load_host_env, resolve_field
from .publish_preflight import PublishPreflightResult, ReleasePublishPreflight, load_manifest_targets
from .required_checks import ReleaseRequiredChecksProjection, required_release_checks
from .versions import parse_semver_full


Runner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]
HEX_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class ReleasePublishResult:
    """Result of the controller-owned release publication primitive.

    """

    published: bool
    reasons: tuple[str, ...]
    tag: str
    target_ref: str
    version: str
    release_url: str
    result_path: Path


ReleasePublicationPhase = Literal["first_run", "already_bumped"]


@dataclass(frozen=True)
class ReleasePublicationState:
    """Private publication classifier."""

    phase: ReleasePublicationPhase
    version: str
    tag: str
    release_target_ref: str | None
    skip_bump_commit: bool
    push_release_commit: bool
    reason: str


@dataclass(frozen=True)
class RemoteReleaseProof:
    ref: str
    sha: str


class ReleasePublisher:
    """Publish a preflight-approved release from the controller boundary only."""

    def __init__(
        self,
        repo_root: Path,
        *,
        runner: Runner | None = None,
        preflight: ReleasePublishPreflight | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.runner = runner or run_command
        self.now = now or (lambda: datetime.now(timezone.utc))
        if preflight is None:
            preflight = ReleasePublishPreflight(repo_root, runner=self.runner, now=self.now)
        self.preflight = preflight

    @property
    def result_path(self) -> Path:
        return self.repo_root / ".refactor-loop/state/release-publish-result.json"

    def publish(
        self,
        *,
        candidate_path: str | Path = ".refactor-loop/state/release-candidate.json",
        target_ref: str,
    ) -> ReleasePublishResult:
        result = self.preflight.validate(candidate_path=candidate_path, target_ref=target_ref)
        state = self._inspect_publication_state(result)
        if not result.allowed and not state.skip_bump_commit:
            return self._denied(result, state.reason)

        version = state.version
        tag = state.tag
        if not state.skip_bump_commit:
            bump = self._run(["python3", ".github/scripts/bump_version.py", "--version", version])
            self._ensure_success(bump, "bump_version")
            paths = [target["relative"] for target in load_manifest_targets(self.repo_root)]
            add = self._run(["git", "add", ".version-bump.json", *paths])
            self._ensure_success(add, "git add")
            commit = self._run(["git", "commit", "-m", self._release_bump_subject(version)])
            self._ensure_success(commit, "git commit")
        release_target_ref = state.release_target_ref or self._current_head_sha()
        release_push_started_at = None if state.skip_bump_commit else self.now()
        if state.push_release_commit:
            self._safe_push()
        self._ensure_fresh_release_commit_checks_green(release_target_ref, since=release_push_started_at)
        release_command = ["gh", "release", "create", tag, "--target", release_target_ref, "--generate-notes"]
        if parse_semver_full(version).prerelease:
            release_command.append("--prerelease")
        release = self._run(release_command)
        self._ensure_success(release, "gh release create")
        release_url = release.stdout.strip().splitlines()[-1] if release.stdout.strip() else ""
        payload = {
            "tag": tag,
            "target_ref": release_target_ref,
            "version": version,
            "published_at": isoformat(self.now()),
            "release_url": release_url,
            "candidate_digest": result.candidate_digest,
        }
        write_json(self.result_path, payload)
        return ReleasePublishResult(
            published=True,
            reasons=(),
            tag=tag,
            target_ref=release_target_ref,
            version=version,
            release_url=release_url,
            result_path=self.result_path,
        )

    def _inspect_publication_state(self, result: PublishPreflightResult) -> ReleasePublicationState:
        version = result.version
        tag = f"v{version}" if version else ""
        if result.allowed and result.already_bumped_reentry:
            remote_state = self._remote_already_bumped_reentry_state(version, tag)
            if remote_state is not None:
                return remote_state
            return ReleasePublicationState(
                phase="already_bumped",
                version=version,
                tag=tag,
                release_target_ref=None,
                skip_bump_commit=True,
                push_release_commit=True,
                reason="preflight_already_bumped_reentry",
            )
        if result.allowed:
            remote_state = self._remote_already_bumped_reentry_state(version, tag)
            if remote_state is not None:
                return remote_state
            return ReleasePublicationState(
                phase="first_run",
                version=version,
                tag=tag,
                release_target_ref=None,
                skip_bump_commit=False,
                push_release_commit=True,
                reason="preflight_allowed",
            )
        if not self._is_only_manifest_version_mismatch(result):
            return ReleasePublicationState(
                phase="first_run",
                version=version,
                tag=tag,
                release_target_ref=None,
                skip_bump_commit=False,
                push_release_commit=True,
                reason="preflight_denied",
            )
        if not version:
            return ReleasePublicationState(
                phase="first_run",
                version=version,
                tag=tag,
                release_target_ref=None,
                skip_bump_commit=False,
                push_release_commit=True,
                reason="missing_version",
            )
        remote_state = self._remote_already_bumped_reentry_state(version, tag)
        if remote_state is not None:
            return remote_state
        manifest_versions = self._mapped_manifest_versions()
        if not manifest_versions or manifest_versions != {version}:
            reason = "remote_release_reentry_unavailable" if self._integration_branch() else "mapped_manifests_not_to_version"
            return ReleasePublicationState(
                phase="first_run",
                version=version,
                tag=tag,
                release_target_ref=None,
                skip_bump_commit=False,
                push_release_commit=True,
                reason=reason,
            )
        expected_subject = self._release_bump_subject(version)
        if self._current_commit_subject() != expected_subject:
            reason = "remote_release_reentry_unavailable" if self._integration_branch() else "release_head_subject_mismatch"
            return ReleasePublicationState(
                phase="first_run",
                version=version,
                tag=tag,
                release_target_ref=None,
                skip_bump_commit=False,
                push_release_commit=True,
                reason=reason,
            )
        return ReleasePublicationState(
            phase="already_bumped",
            version=version,
            tag=tag,
            release_target_ref=None,
            skip_bump_commit=True,
            push_release_commit=True,
            reason="already_bumped_reentry",
        )

    def _is_only_manifest_version_mismatch(self, result: PublishPreflightResult) -> bool:
        return result.reasons == ("manifest_version_mismatch",)

    def _mapped_manifest_versions(self) -> set[str]:
        versions = {str(target["version"]) for target in load_manifest_targets(self.repo_root)}
        return versions

    def _remote_already_bumped_reentry_state(self, version: str, tag: str) -> ReleasePublicationState | None:
        branch = self._integration_branch()
        if not branch:
            return None
        fetch = self._run(["git", "fetch", "origin", branch])
        if fetch.returncode != 0:
            return None
        branch_proof = self._remote_reentry_proof(f"origin/{branch}")
        state = self._remote_already_bumped_state_from_proof(branch_proof, version, tag)
        if state is not None:
            return state
        for proof in self._remote_rollup_release_proofs():
            state = self._remote_already_bumped_state_from_proof(proof, version, tag)
            if state is not None:
                return state
        return None

    def _remote_already_bumped_state_from_proof(
        self, proof: RemoteReleaseProof | None, version: str, tag: str
    ) -> ReleasePublicationState | None:
        if proof is None:
            return None
        subject = self._run(["git", "show", "-s", "--format=%s", proof.ref])
        if subject.returncode != 0 or subject.stdout.strip() != self._release_bump_subject(version):
            return None
        if self._remote_mapped_manifest_versions(proof.ref) != {version}:
            return None
        return ReleasePublicationState(
            phase="already_bumped",
            version=version,
            tag=tag,
            release_target_ref=proof.sha,
            skip_bump_commit=True,
            push_release_commit=False,
            reason="remote_already_bumped_reentry",
        )

    def _remote_reentry_proof(self, ref: str) -> RemoteReleaseProof | None:
        sha = self._rev_parse_ref(ref)
        if not sha:
            return None
        return RemoteReleaseProof(ref=ref, sha=sha)

    def _remote_rollup_release_proofs(self) -> list[RemoteReleaseProof]:
        listing = self._run(["git", "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/rollup"])
        if listing.returncode != 0:
            return []
        proofs: list[RemoteReleaseProof] = []
        for ref in sorted(line.strip() for line in listing.stdout.splitlines() if line.strip()):
            if not ref.startswith("origin/rollup/"):
                continue
            suffix = ref.rsplit("/", 1)[-1]
            if not HEX_SHA_RE.fullmatch(suffix):
                continue
            resolved = self._rev_parse_ref(ref)
            if resolved == suffix:
                proofs.append(RemoteReleaseProof(ref=ref, sha=resolved))
        return proofs

    def _rev_parse_ref(self, ref: str) -> str | None:
        rev_parse = self._run(["git", "rev-parse", ref])
        if rev_parse.returncode != 0:
            return None
        sha = rev_parse.stdout.strip()
        return sha or None

    def _remote_mapped_manifest_versions(self, remote_ref: str) -> set[str]:
        versions: set[str] = set()
        try:
            targets = load_manifest_targets(self.repo_root)
        except Exception:
            return set()
        for target in targets:
            relative = str(target["relative"])
            show = self._run(["git", "show", f"{remote_ref}:{relative}"])
            if show.returncode != 0:
                return set()
            try:
                data: Any = json.loads(show.stdout)
                versions.add(str(resolve_field(data, str(target["field"]))))
            except Exception:
                return set()
        return versions

    def _integration_branch(self) -> str:
        host_env = load_host_env(self.repo_root)
        return (host_env.get("INTEGRATION_BRANCH") or os.environ.get("INTEGRATION_BRANCH") or "").strip()

    def _release_bump_subject(self, version: str) -> str:
        return f"Release v{version}"

    def _current_commit_subject(self) -> str:
        result = self._run(["git", "show", "-s", "--format=%s", "HEAD"])
        self._ensure_success(result, "git show -s --format=%s HEAD")
        return result.stdout.strip()

    def _denied(self, result: PublishPreflightResult, state_reason: str) -> ReleasePublishResult:
        reasons = result.reasons
        if state_reason == "remote_release_reentry_unavailable":
            reasons = (*reasons, state_reason)
        return ReleasePublishResult(
            published=False,
            reasons=reasons,
            tag=f"v{result.version}" if result.version else "",
            target_ref=result.target_ref,
            version=result.version,
            release_url="",
            result_path=self.result_path,
        )

    def _run(self, cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return self.runner(cmd, self.repo_root)

    def _current_head_sha(self) -> str:
        result = self._run(["git", "rev-parse", "HEAD"])
        self._ensure_success(result, "git rev-parse HEAD")
        sha = result.stdout.strip()
        if not sha:
            raise RuntimeError("git rev-parse HEAD returned an empty release target")
        return sha

    def _safe_push(self) -> None:
        fetch = self._run(["git", "fetch", "origin", "HEAD"])
        self._ensure_success(fetch, "git fetch")
        behind = self._run(["git", "rev-list", "--count", "HEAD..origin/HEAD"])
        self._ensure_success(behind, "git rev-list")
        try:
            behind_count = int((behind.stdout or "0").strip() or "0")
        except ValueError:
            behind_count = 0
        if behind_count > 0:
            raise RuntimeError(f"safe push refused: local release commit is behind origin/HEAD by {behind_count}")
        push = self._run(["git", "push", "origin", "HEAD"])
        self._ensure_success(push, "git push")

    def _ensure_fresh_release_commit_checks_green(self, release_target_ref: str, *, since: datetime | None) -> None:
        repo_slug = self._repo_slug()
        projection = ReleaseRequiredChecksProjection(
            runner=self._run_check_command,
            now=self.now,
            required_checks=required_release_checks(load_host_env(self.repo_root)),
        )
        status = projection.check_ref(repo_slug, release_target_ref, since=since, wait_seconds=0)
        if not status.passed:
            raise RuntimeError(f"fresh release commit required checks are not green: {status.reason or 'unknown'}")

    def _run_check_command(self, cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return self._run(cmd)

    def _repo_slug(self) -> str:
        host_env = load_host_env(self.repo_root)
        repo_slug = (host_env.get("GH_REPO_SLUG") or os.environ.get("GH_REPO_SLUG") or "").strip()
        if not repo_slug:
            raise RuntimeError("GH_REPO_SLUG is required for fresh release commit check gate")
        return repo_slug

    def _ensure_success(self, result: subprocess.CompletedProcess[str], label: str) -> None:
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"{label} failed")


def run_command(cmd: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, check=False)


__all__ = ["ReleasePublishResult", "ReleasePublisher", "run_command"]
