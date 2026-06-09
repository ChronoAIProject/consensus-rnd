"""Patrol-private analysis gate for candidate signals."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .context import LoopContext
from .processes import ProcessSupervisor


PATROL_ANALYSIS_PROMPT = "patrol-analysis.md"
# Compatibility name: this is a total wall-clock timeout, not a log-idle window.
PATROL_ANALYSIS_STALL_SECONDS = 5400
PATROL_ANALYSIS_CODEX_HOME = "patrol-analysis-codex-home"
PATROL_ANALYSIS_CWD = "patrol-analysis-cwd"
PATROL_ANALYSIS_ENV_ALLOWLIST = frozenset(
    {
        "HOME",
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "TMPDIR",
        "PYTHONIOENCODING",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
    }
)
PATROL_ANALYSIS_ENV_DENY_TOKENS = (
    "GH",
    "GITHUB",
    "GIT_",
    "TOKEN",
    "AUTH",
    "CREDENTIAL",
    "PASSWORD",
    "SECRET",
    "COOKIE",
    "SSH",
    "KEY",
)


@dataclass(frozen=True)
class PatrolCandidateSignal:
    kind: str
    source: str
    summary: str
    severity: str
    evidence: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "source": self.source,
            "summary": self.summary,
            "severity": self.severity,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class PatrolAnalysisDecision:
    is_real_issue: bool
    summary: str
    severity: str
    root_cause: str
    recommendation: str
    rationale: str

    @classmethod
    def from_json(cls, data: object) -> "PatrolAnalysisDecision":
        if not isinstance(data, dict):
            raise ValueError("patrol analysis decision must be a JSON object")
        raw_real_issue = data.get("is_real_issue")
        if not isinstance(raw_real_issue, bool):
            raise ValueError("patrol analysis decision missing boolean is_real_issue")
        fields: dict[str, str] = {}
        for field in ("summary", "severity", "root_cause", "recommendation", "rationale"):
            value = data.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"patrol analysis decision missing non-empty {field}")
            fields[field] = value.strip()
        return cls(is_real_issue=raw_real_issue, **fields)


class CodexPatrolAnalysisProvider:
    def __init__(
        self,
        ctx: LoopContext,
        *,
        supervisor: ProcessSupervisor | None = None,
        command_builder: Callable[[Path], Sequence[str]] | None = None,
    ) -> None:
        self.ctx = ctx
        self.supervisor = supervisor or ProcessSupervisor()
        self.command_builder = command_builder or _default_codex_command

    def analyze(self, signal: PatrolCandidateSignal) -> PatrolAnalysisDecision:
        prompt = _render_analysis_prompt(self.ctx, signal)
        prompt_path = _analysis_prompt_path(self.ctx, signal)
        log_path = _analysis_log_path(self.ctx, signal)
        output_path = _analysis_output_path(self.ctx, signal)
        analysis_cwd = _analysis_cwd(self.ctx)
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        analysis_cwd.mkdir(parents=True, exist_ok=True)
        (self.ctx.paths.runs / PATROL_ANALYSIS_CODEX_HOME).mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        exit_code = self.supervisor.supervise(
            self.command_builder(output_path),
            stdin=prompt_path,
            log=log_path,
            stall=PATROL_ANALYSIS_STALL_SECONDS,
            env=patrol_analysis_env(self.ctx),
            cwd=analysis_cwd,
        )
        if exit_code != 0:
            raise RuntimeError(f"patrol analysis failed: source={signal.source} exit={exit_code} log={log_path}")
        return load_patrol_analysis_decision(output_path)


def load_patrol_analysis_decision(path: Path) -> PatrolAnalysisDecision:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"patrol analysis decision read failed: path={path} reason={exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"patrol analysis decision JSON malformed: path={path} reason={exc}") from exc
    try:
        return PatrolAnalysisDecision.from_json(data)
    except ValueError as exc:
        raise RuntimeError(f"patrol analysis decision invalid: path={path} reason={exc}") from exc


def _render_analysis_prompt(ctx: LoopContext, signal: PatrolCandidateSignal) -> str:
    template_path = _skill_root() / "prompts" / PATROL_ANALYSIS_PROMPT
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"patrol analysis prompt missing: path={template_path} reason={exc}") from exc
    payload = json.dumps(signal.to_json(), indent=2, sort_keys=True)
    return template.replace("${PATROL_CANDIDATE_SIGNAL_JSON}", payload).replace(
        "${PATROL_ANALYSIS_OUTPUT_PATH}",
        str(_analysis_output_path(ctx, signal)),
    )


def _analysis_prompt_path(ctx: LoopContext, signal: PatrolCandidateSignal) -> Path:
    return ctx.paths.prompts / "patrol-analysis" / f"{_signal_id(signal)}.md"


def _analysis_log_path(ctx: LoopContext, signal: PatrolCandidateSignal) -> Path:
    return ctx.paths.logs / f"patrol-analysis-{_signal_id(signal)}.log"


def _analysis_output_path(ctx: LoopContext, signal: PatrolCandidateSignal) -> Path:
    return ctx.paths.runs / "patrol-analysis" / f"{_signal_id(signal)}.json"


def _analysis_cwd(ctx: LoopContext) -> Path:
    return ctx.paths.runs / PATROL_ANALYSIS_CWD


def _signal_id(signal: PatrolCandidateSignal) -> str:
    payload = json.dumps(signal.to_json(), sort_keys=True, separators=(",", ":"))
    import hashlib

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def patrol_analysis_env(ctx: LoopContext) -> dict[str, str]:
    source = ctx.env_for_subprocess()
    env = {name: value for name, value in source.items() if name in PATROL_ANALYSIS_ENV_ALLOWLIST and not _is_denied_env_name(name)}
    env["HOME"] = str(ctx.paths.runs / PATROL_ANALYSIS_CODEX_HOME)
    env["CODEX_HOME"] = env["HOME"]
    env["REPO_ROOT"] = str(ctx.repo_root)
    env["NO_COLOR"] = "1"
    if "PATH" not in env and "PATH" in os.environ:
        env["PATH"] = os.environ["PATH"]
    return env


def _is_denied_env_name(name: str) -> bool:
    upper = name.upper()
    return any(token in upper for token in PATROL_ANALYSIS_ENV_DENY_TOKENS)


def _default_codex_command(output_path: Path) -> Sequence[str]:
    return (
        "codex",
        "exec",
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--output-last-message",
        str(output_path),
        "-",
    )


def _skill_root() -> Path:
    return Path(__file__).resolve().parents[2]
