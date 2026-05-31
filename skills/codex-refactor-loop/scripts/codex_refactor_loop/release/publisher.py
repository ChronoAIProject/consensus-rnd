"""Controller-internal release publisher."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Callable, Sequence

from ..state import write_json
from .gate import isoformat, load_host_env
from .publish_preflight import PublishPreflightResult, ReleasePublishPreflight, load_manifest_targets
from .required_checks import ReleaseRequiredChecksProjection
from .versions import parse_semver_full


Runner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class ReleasePublishResult:
    """Result of the controller-owned release publication primitive.

    Refactor (iter217/issue-217):
      Old pattern: release.yml 保留 tag/release mutation,无法可靠读本地 runtime fact,绕过 release-gate decider-only 边界
      New principle: controller-only publication:新增 ReleasePublishPreflight+ReleasePublisher 替代 workflow 发布权;release.yml 降为 read-only preview(contents:read,禁 gh release create)。严格按 plan 'Concrete plan' 逐条改。
    """

    published: bool
    reasons: tuple[str, ...]
    tag: str
    target_ref: str
    version: str
    release_url: str
    result_path: Path


class ReleasePublisher:
    """Publish a preflight-approved release from the controller boundary only.

    Refactor (iter334/issue-334):
      Old pattern: fresh manifest-bump commits could be released before exact-SHA checks were green.
      New principle: after safe push, gate the same fresh SHA with ReleaseRequiredChecksProjection before release creation.

    Refactor (iter217/issue-217):
      Old pattern: release.yml 保留 tag/release mutation,无法可靠读本地 runtime fact,绕过 release-gate decider-only 边界
      New principle: controller-only publication:新增 ReleasePublishPreflight+ReleasePublisher 替代 workflow 发布权;release.yml 降为 read-only preview(contents:read,禁 gh release create)。严格按 plan 'Concrete plan' 逐条改。
    """

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
        self.preflight = preflight or ReleasePublishPreflight(repo_root)
        self.now = now or (lambda: datetime.now(timezone.utc))

    @property
    def result_path(self) -> Path:
        return self.repo_root / ".refactor-loop/state/release-publish-result.json"

    def publish(
        self,
        *,
        candidate_path: str | Path = ".refactor-loop/state/release-candidate.json",
        target_ref: str,
    ) -> ReleasePublishResult:
        # Refactor (iter217/issue-217):
        #   Old pattern: release.yml 保留 tag/release mutation,无法可靠读本地 runtime fact,绕过 release-gate decider-only 边界
        #   New principle: controller-only publication:新增 ReleasePublishPreflight+ReleasePublisher 替代 workflow 发布权;release.yml 降为 read-only preview(contents:read,禁 gh release create)。严格按 plan 'Concrete plan' 逐条改。
        result = self.preflight.validate(candidate_path=candidate_path, target_ref=target_ref)
        if not result.allowed:
            return self._denied(result)

        version = result.version
        tag = f"v{version}"
        bump = self._run(["python3", ".github/scripts/bump_version.py", "--version", version])
        self._ensure_success(bump, "bump_version")
        paths = [target["relative"] for target in load_manifest_targets(self.repo_root)]
        add = self._run(["git", "add", ".version-bump.json", *paths])
        self._ensure_success(add, "git add")
        commit = self._run(["git", "commit", "-m", f"Release {tag}"])
        self._ensure_success(commit, "git commit")
        # Refactor (fix/publisher-tag-target): Old pattern: publisher committed
        # synchronized release manifests, pushed HEAD, then created the release tag
        # on the preflight candidate ref whose manifests could still be old.
        # New principle: the release tag target is the freshly committed manifest
        # bump SHA, so tag version and mapped manifest versions are coupled.
        release_target_ref = self._current_head_sha()
        release_push_started_at = self.now()
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

    def _denied(self, result: PublishPreflightResult) -> ReleasePublishResult:
        return ReleasePublishResult(
            published=False,
            reasons=result.reasons,
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
        # Refactor (iter217/issue-217):
        #   Old pattern: release.yml kept tag/release mutation authority and
        #   could not reliably read local runtime facts.
        #   New principle: controller-only publication owns release mutation;
        #   release.yml is a read-only preview.
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

    def _ensure_fresh_release_commit_checks_green(self, release_target_ref: str, *, since: datetime) -> None:
        repo_slug = self._repo_slug()
        projection = ReleaseRequiredChecksProjection(
            runner=self._run_check_command,
            now=self.now,
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
