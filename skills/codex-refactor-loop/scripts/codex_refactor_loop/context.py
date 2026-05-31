"""Host context loading for codex-refactor-loop controller helpers."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping


class LoopContextError(RuntimeError):
    """Raised when host context cannot be loaded safely."""


_ASSIGNMENT_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


@dataclass(frozen=True)
class HostEnvLocation:
    """Resolved location of the loop runtime injection file."""

    path: Path


@dataclass(frozen=True)
class LoopPaths:
    """Stable host artifact paths used by the loop."""

    refactor_loop: Path
    logs: Path
    prompts: Path
    runs: Path
    state: Path
    dispatch_queue: Path
    dispatch_dispatched: Path
    dispatch_rejected: Path
    heartbeats: Path
    pending_events: Path
    statusline_snapshot: Path
    recent_pr_merges: Path


@dataclass(frozen=True)
class LoopContext:
    """Resolved host repository and skill context.

    Refactor (iter219/issue-219):
      Old pattern: host 无法按 GitHub 模板自定义事件流/工作流/issue/prompt;workflow vocabulary 是闭集硬编码
      New principle: 引入 data-only HostWorkflowSpec(HOST_WORKFLOW_SPEC,repo-relative JSON)+ WorkflowInvariantValidator;空/未设=built-in 行为;host 只能在 host: 命名空间加 data,不能覆盖 built-in/降共识闸/夺 lifecycle authority。严格按 plan 'Concrete plan' 逐条改,首版 scope 受限。

    Refactor (iter202/issue-202): Old pattern: durable artifact(ledger log_path、pending-event JSON log_path、meta-judge/reflector evidence、dev-sync resolver prompt、DEV_SYNC_REQUEST marker)写入 host absolute repo/worktree/log path,违反 CLAUDE.md R24『artifact 路径相对 $REPO_ROOT,不引入具体 host 事实』。
    New principle: 分层 durable-text-path vs execution-path:写入时所有 durable artifact/prompt/marker 只存 repo-relative POSIX text;读取或传 subprocess 时由 LoopContext.repo_root/rel_path 解析回 absolute;spawn-codex --cd/--add-dir/--prompt/--log 与 Popen argv 仍用 absolute(execution boundary 非 durable truth)。配套 behavior(写入存相对、读取解析绝对)+ source-regression(无 host absolute prefix)测试。不改 daemon lifecycle authority,不加规则例外。

    Refactor (iter316/issue-316):
      Old pattern: host runtime facts were parsed in wakeup_plan/release-gate/sync/controller_actions/heartbeat and read legacy aliases plus root host.env.
      New principle: LoopContext/context.py is the single shared host.env parser; read only CONSENSUS_RND_HOST_ENV or legacy .refactor-loop/host.env; no root host.env or unlisted aliases.
    """

    repo_root: Path
    skill_root: Path
    gh_repo_slug: str | None
    paths: LoopPaths
    host_env: dict[str, str]
    host_workflow_spec_path: str = ""
    read_only: bool = False
    repo_root_source: str = "env"

    @classmethod
    def load(
        cls,
        *,
        repo_root: str | Path | None = None,
        skill_root: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        read_only: bool = False,
        allow_git_root_fallback: bool | None = None,
        cwd: str | Path | None = None,
    ) -> "LoopContext":
        source_env = dict(os.environ if env is None else env)
        resolved_skill_root = Path(skill_root or source_env.get("CODEX_REFACTOR_LOOP_SKILL_ROOT") or Path(__file__).resolve().parents[2]).resolve()
        repo, repo_source = _resolve_repo_root(
            explicit=repo_root,
            env=source_env,
            read_only=read_only,
            allow_git_root_fallback=allow_git_root_fallback,
            cwd=cwd,
        )
        host_env = _load_host_env(repo, source_env, cwd)
        merged = {**source_env, **host_env}
        if repo_source == "host.env":
            merged["REPO_ROOT"] = str(repo)
        elif "REPO_ROOT" in host_env:
            _validate_repo_override(repo, host_env["REPO_ROOT"], "host.env")

        slug = _github_repo_slug(merged)
        paths = _paths(repo)
        return cls(
            repo_root=repo,
            skill_root=resolved_skill_root,
            gh_repo_slug=slug,
            paths=paths,
            host_env=host_env,
            host_workflow_spec_path=str(merged.get("HOST_WORKFLOW_SPEC") or ""),
            read_only=read_only,
            repo_root_source=repo_source,
        )

    def env_for_subprocess(self) -> dict[str, str]:
        result = dict(os.environ)
        result.update(self.host_env)
        result["REPO_ROOT"] = str(self.repo_root)
        if self.gh_repo_slug:
            result["GH_REPO_SLUG"] = self.gh_repo_slug
        return result

    def durable_artifact_path(self, path: Path) -> str:
        """Return repo-relative POSIX durable text for a host artifact path."""
        try:
            return path.resolve().relative_to(self.repo_root.resolve()).as_posix()
        except ValueError as exc:
            raise LoopContextError(f"artifact path points outside REPO_ROOT: {path}") from exc

    def artifact_execution_path(self, text: str) -> Path:
        """Resolve stored repo-relative durable text back to an execution path."""
        pure = PurePosixPath(text)
        if not text or pure.is_absolute() or "\\" in text or ".." in pure.parts:
            raise LoopContextError(f"artifact path must be repo-relative POSIX text: {text!r}")
        path = (self.repo_root / Path(*pure.parts)).resolve()
        try:
            path.relative_to(self.repo_root.resolve())
        except ValueError as exc:
            raise LoopContextError(f"artifact path escapes REPO_ROOT: {text!r}") from exc
        return path


def _resolve_repo_root(
    *,
    explicit: str | Path | None,
    env: Mapping[str, str],
    read_only: bool,
    allow_git_root_fallback: bool | None,
    cwd: str | Path | None,
) -> tuple[Path, str]:
    if explicit:
        return _existing_dir(explicit, "repo_root"), "arg"
    env_root = env.get("REPO_ROOT")
    if env_root:
        return _existing_dir(env_root, "REPO_ROOT"), "env"
    host_env_repo = _host_env_repo_root_from_cwd(env, cwd)
    if host_env_repo is not None:
        return host_env_repo, "host.env"
    fallback_allowed = env.get("ALLOW_GIT_ROOT_FALLBACK") == "1" if allow_git_root_fallback is None else allow_git_root_fallback
    if fallback_allowed:
        if not read_only:
            raise LoopContextError("ALLOW_GIT_ROOT_FALLBACK is only allowed for read-only commands")
        root = _git_root(cwd)
        if root:
            return root, "git"
    raise LoopContextError(
        "REPO_ROOT is unset; source a host-owned consensus-rnd host.env or set "
        "CONSENSUS_RND_HOST_ENV, or set ALLOW_GIT_ROOT_FALLBACK=1 for read-only use"
    )


class HostEnvLocator:
    """Locate only the consensus-rnd loop runtime injection file."""

    EXPLICIT_ENV = "CONSENSUS_RND_HOST_ENV"
    LEGACY_PATHS = (Path(".refactor-loop") / "host.env",)

    @classmethod
    def resolve(cls, repo_root: Path, env: Mapping[str, str], cwd: Path | None = None) -> HostEnvLocation | None:
        root = repo_root.resolve()
        raw_explicit = env.get(cls.EXPLICIT_ENV)
        if raw_explicit is not None:
            # Refactor (iter1/issue-310):
            #   Old pattern: host.env lookup made .refactor-loop/host.env look like the host production fact home.
            #   New principle: CONSENSUS_RND_HOST_ENV is only a locator for a host-owned loop runtime injection file; .refactor-loop/host.env stays a compatibility read.
            return HostEnvLocation(
                path=cls._resolve_explicit(root, raw_explicit),
            )

        for relative in cls.LEGACY_PATHS:
            path = (root / relative).resolve()
            if path.is_file():
                return HostEnvLocation(path=path)
        return None

    @classmethod
    def _resolve_explicit(cls, repo_root: Path, raw_value: str) -> Path:
        raw = raw_value.strip()
        if not raw:
            raise LoopContextError(f"{cls.EXPLICIT_ENV} must not be empty")
        candidate = Path(raw).expanduser()
        if any(part == ".." for part in candidate.parts):
            raise LoopContextError(f"{cls.EXPLICIT_ENV} must not contain '..': {raw_value}")
        path = (candidate if candidate.is_absolute() else repo_root / candidate).resolve()
        try:
            path.relative_to(repo_root)
        except ValueError as exc:
            raise LoopContextError(f"{cls.EXPLICIT_ENV} must point inside REPO_ROOT: {raw_value}") from exc
        if not path.is_file():
            raise LoopContextError(f"{cls.EXPLICIT_ENV} is not a readable file: {raw_value}")
        return path


def _host_env_repo_root_from_cwd(env: Mapping[str, str], cwd: str | Path | None) -> Path | None:
    base = Path(cwd or os.getcwd())
    location = HostEnvLocator.resolve(base.resolve(), env, base)
    if location is None:
        return None
    values = parse_host_env(location.path)
    raw_root = values.get("REPO_ROOT")
    if raw_root:
        return _existing_dir(raw_root, "host.env REPO_ROOT")
    return base.resolve()


def _existing_dir(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise LoopContextError(f"{label} is not a readable directory: {value}")
    return path


def _git_root(cwd: str | Path | None) -> Path | None:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    raw = result.stdout.strip()
    if not raw:
        return None
    return _existing_dir(raw, "git root")


def _load_host_env(repo_root: Path, env: Mapping[str, str], cwd: str | Path | None) -> dict[str, str]:
    location = HostEnvLocator.resolve(repo_root, env, Path(cwd) if cwd is not None else None)
    if location is not None:
        return parse_host_env(location.path)
    return {}


def parse_host_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ASSIGNMENT_RE.match(stripped)
        if not match:
            continue
        key, raw_value = match.groups()
        try:
            parsed = shlex.split(raw_value, posix=True)
        except ValueError as exc:
            raise LoopContextError(f"invalid host.env assignment for {key}: {exc}") from exc
        values[key] = parsed[0] if parsed else ""
    return values


def _validate_repo_override(repo_root: Path, raw_override: str, source: str) -> None:
    override = Path(raw_override).expanduser().resolve()
    if override != repo_root:
        raise LoopContextError(f"{source} REPO_ROOT override points outside resolved repo root: {raw_override}")


def _github_repo_slug(env: Mapping[str, str]) -> str | None:
    slug = env.get("GH_REPO_SLUG")
    if slug:
        if "/" not in slug:
            raise LoopContextError(f"GH_REPO_SLUG must be OWNER/REPO; got {slug!r}")
        return slug
    repo = env.get("GH_REPO")
    if repo and "/" in repo:
        return repo
    owner = env.get("GH_OWNER")
    name = env.get("GH_REPO_NAME") or repo
    if owner and name:
        return f"{owner}/{name}"
    return None


def _paths(repo_root: Path) -> LoopPaths:
    # Refactor (iter1/issue-310):
    #   Old pattern: .refactor-loop paths could be read as host production configuration or ledger authority.
    #   New principle: .refactor-loop is only the skill-private runtime home while callers keep the historical Path contract.
    refactor_loop = repo_root / ".refactor-loop"
    state = refactor_loop / "state"
    return LoopPaths(
        refactor_loop=refactor_loop,
        logs=refactor_loop / "logs",
        prompts=refactor_loop / "prompts",
        runs=refactor_loop / "runs",
        state=state,
        dispatch_queue=refactor_loop / "dispatch-queue",
        dispatch_dispatched=refactor_loop / "dispatch-dispatched",
        dispatch_rejected=refactor_loop / "dispatch-rejected",
        heartbeats=refactor_loop / "heartbeats",
        pending_events=refactor_loop / ".controller-pending-events.log",
        statusline_snapshot=state / "statusline-snapshot.json",
        recent_pr_merges=state / "recent-pr-merges.json",
    )
