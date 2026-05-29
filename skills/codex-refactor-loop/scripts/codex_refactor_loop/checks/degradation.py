"""Static degradation checks for the codex-refactor-loop skill.

The skill degradation watch is intentionally a single read-only checker. It has
no daemon lifecycle, no source mutation path, no GitHub lifecycle authority, and
no worker dispatch API. Runtime monitoring is owned by concurrency_monitor.py,
which may call the legacy checker script and report failures as local alerts
only.
"""

from __future__ import annotations

import argparse
import json
import sys
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..context import LoopContext


# Refactor (issue160-p3-checks):
#   Old: scripts/check_skill_degradation.py owned the static issue #66 drift
#   gate as a top-level script.
#   New: expose the same read-only checks from codex_refactor_loop.checks while
#   preserving every marker, required check name, artifact path, and narrow
#   allowlist literal for future CLI import.

CHECK_NAME = "skill-degradation"
SKILL_RELATIVE = Path("skills/codex-refactor-loop")
SCRIPT_RELATIVE = SKILL_RELATIVE / "scripts"
CI_WORKFLOW = Path(".github/workflows/consensus-rnd-ci.yml")
RELEASE_WORKFLOW = Path(".github/workflows/release.yml")
JUDGE_ARTIFACT = ".refactor-loop/runs/phase9-issue66-r8-judge.md"
ALERT_LOG = ".refactor-loop/.degradation-alert.log"
PENDING_EVENTS = ".refactor-loop/.controller-pending-events.log"
INTERVAL_ENV = "DEGRADATION_WATCH_INTERVAL_SECONDS"

FORBIDDEN_RUNTIME_FILES = (
    SCRIPT_RELATIVE / "degradation_watchdog.py",
    SCRIPT_RELATIVE / "degradation_checks.py",
    SCRIPT_RELATIVE / "skill_degradation_watchdog.py",
    SCRIPT_RELATIVE / "skill_degradation_daemon.py",
)

FORBIDDEN_SURFACE_PATTERNS = (
    re.compile(r"\b" + "Degradation" + r"Check\b"),
    re.compile(r"\b" + "SkillDegradation" + r"CheckV1\b"),
    re.compile(r"\b" + "WorkUnit" + r"V2\b"),
    re.compile(r"\b" + "Controller" + r"Event\b"),
    re.compile(r"\b" + "Controller" + r"Command\b"),
    re.compile(r"\b" + "Controller" + r"Orchestrator\b"),
    re.compile(r"\bplugin\s+registry\b", re.IGNORECASE),
    re.compile(r"\bevent\s+envelope\b", re.IGNORECASE),
    re.compile(r"\bstandalone\s+watchdog\b", re.IGNORECASE),
    re.compile(r"\bauto[-_ ]?fix\b", re.IGNORECASE),
    re.compile(r"\bauto[-_ ]?clean\b", re.IGNORECASE),
)

CHECKED_SURFACE_FILES = (
    SKILL_RELATIVE / "SKILL.md",
    SKILL_RELATIVE / "host.env.example",
    SCRIPT_RELATIVE / "codex_refactor_loop" / "release" / "gate.py",
    SCRIPT_RELATIVE / "codex_refactor_loop" / "release" / "required_checks.py",
    SCRIPT_RELATIVE / "codex_refactor_loop" / "monitors" / "concurrency.py",
    SCRIPT_RELATIVE / "codex_refactor_loop" / "peek.py",
    CI_WORKFLOW,
    RELEASE_WORKFLOW,
)

DOC_FORBIDDEN_CONTEXT = (
    "Forbidden actions",
    "Forbidden:",
    "forbidden:",
    "do not introduce",
    "must not introduce",
    "must not create",
    "no ",
    "No ",
    "拒绝",
    "禁止",
    "rejecting",
    "rejects",
    "rejected",
)

REQUIRED_SKILL_MARKERS = (
    "## Named runtime exception — skill degradation watch(per #66)",
    JUDGE_ARTIFACT,
    "run `consensus-rnd-cli check-degradation`",
    "write `.refactor-loop/.degradation-alert.log`",
    "append existing-format pending events",
    "source mutation",
    "git reset/rebase/merge/push",
    "GitHub issue/PR/body/label lifecycle mutation",
    "codex dispatch",
    "standalone daemon creation",
    "WorkUnit/schema/envelope changes",
    "protocol/" + "plugin " + "registry",
    "auto-" + "clean root garbage",
    "auto-" + "fix API",
)

REQUIRED_DETAILED_REFERENCE_MARKERS = (
    "skill degradation watch(per #66)",
    JUDGE_ARTIFACT,
    ALERT_LOG,
    PENDING_EVENTS,
    INTERVAL_ENV,
    "consensus-rnd-cli check-degradation --static",
    "existing-format pending event",
    "no source mutation",
)

REQUIRED_HOST_ENV_MARKERS = (
    INTERVAL_ENV,
    "DEGRADATION_WATCH_TIMEOUT_SECONDS",
    "DEGRADATION_ALERT_TAIL_LINES",
    ALERT_LOG,
)

REQUIRED_MONITOR_MARKERS = (
    INTERVAL_ENV,
    "DEGRADATION_ALERT_LOG",
    "run_skill_degradation_check",
    "maybe_run_skill_degradation_watch",
    "skill-degradation-alert",
    "check-degradation",
)

REQUIRED_PEEK_MARKERS = (
    ALERT_LOG,
    "DEGRADATION_ALERT_TAIL_LINES",
    "Skill degradation alerts",
)

REQUIRED_CI_MARKERS = (
    "skill-degradation:",
    "name: skill-degradation",
    "consensus-rnd-cli check-degradation --static",
)

REQUIRED_RELEASE_MARKERS = (
    "release-required-checks",
    "workflow_run:",
    "github.event.workflow_run.head_branch == 'dev'",
    "github.event.workflow_run.head_sha",
)

REQUIRED_RELEASE_PROJECTION_MARKERS = (
    '"skill-degradation"',
    '"contract-tests"',
    '"manifest-version-sync"',
    "REQUIRED_RELEASE_CHECKS",
)

REQUIRED_RELEASE_GATE_MARKERS = (
    "ReleaseRequiredChecksProjection",
    "checks-api-projection",
)


@dataclass(frozen=True)
class Finding:
    check: str
    path: str
    message: str
    severity: str = "error"

    def as_dict(self) -> dict[str, str]:
        return {
            "check": self.check,
            "path": self.path,
            "message": self.message,
            "severity": self.severity,
        }


class SkillDriftChecker:
    def __init__(self, repo_root: Path | None = None, *, ctx: LoopContext | None = None) -> None:
        if ctx is not None:
            self.ctx = ctx
            self.repo_root = ctx.repo_root.resolve()
            return
        if repo_root is None:
            raise ValueError("repo_root or ctx is required")
        self.ctx = None
        self.repo_root = repo_root.resolve()

    def run_static(self) -> list[Finding]:
        findings: list[Finding] = []
        findings.extend(self.required_files_exist())
        findings.extend(self.forbidden_runtime_files_absent())
        findings.extend(self.skill_named_exception_present())
        findings.extend(self.reference_contract_present())
        findings.extend(self.host_env_surface_present())
        findings.extend(self.ci_job_present())
        findings.extend(self.release_workflow_required_check_present())
        findings.extend(self.release_gate_required_check_present())
        findings.extend(self.monitor_hook_present())
        findings.extend(self.peek_status_present())
        findings.extend(self.forbidden_surfaces_absent())
        findings.extend(self.checker_is_read_only())
        return findings

    def required_files_exist(self) -> list[Finding]:
        findings: list[Finding] = []
        expected = CHECKED_SURFACE_FILES + (SCRIPT_RELATIVE / "consensus-rnd-cli",)
        for relative in expected:
            if not (self.repo_root / relative).exists():
                findings.append(Finding("required-file", str(relative), "required file is missing"))
        return findings

    def forbidden_runtime_files_absent(self) -> list[Finding]:
        findings: list[Finding] = []
        for relative in FORBIDDEN_RUNTIME_FILES:
            if (self.repo_root / relative).exists():
                findings.append(
                    Finding(
                        "forbidden-runtime-file",
                        str(relative),
                        "standalone degradation runtime surface is forbidden by issue 66 consensus",
                    )
                )
        scripts = self.repo_root / SCRIPT_RELATIVE
        if scripts.exists():
            for path in sorted(scripts.rglob("*degradation*")):
                if "__pycache__" in path.parts:
                    continue
                if path.name == "test_check_skill_degradation.py" or path == scripts / "codex_refactor_loop" / "checks" / "degradation.py":
                    continue
                findings.append(
                    Finding(
                        "forbidden-runtime-file",
                        self.relative(path),
                        "only consensus-rnd-cli check-degradation may expose the degradation surface",
                    )
                )
        return findings

    def skill_named_exception_present(self) -> list[Finding]:
        return self.require_markers(
            SKILL_RELATIVE / "SKILL.md",
            REQUIRED_SKILL_MARKERS,
            "skill-named-exception",
        )

    def reference_contract_present(self) -> list[Finding]:
        return self.require_markers(
            SKILL_RELATIVE / "SKILL.md",
            REQUIRED_DETAILED_REFERENCE_MARKERS,
            "reference-contract",
        )

    def host_env_surface_present(self) -> list[Finding]:
        return self.require_markers(
            SKILL_RELATIVE / "host.env.example",
            REQUIRED_HOST_ENV_MARKERS,
            "host-env-surface",
        )

    def ci_job_present(self) -> list[Finding]:
        return self.require_markers(CI_WORKFLOW, REQUIRED_CI_MARKERS, "ci-job")

    def release_workflow_required_check_present(self) -> list[Finding]:
        findings = self.require_markers(RELEASE_WORKFLOW, REQUIRED_RELEASE_MARKERS, "release-workflow")
        text = self.read(RELEASE_WORKFLOW)
        if "push:" in text:
            findings.append(
                Finding(
                    "release-workflow",
                    str(RELEASE_WORKFLOW),
                    "release workflow must not trigger directly on push@dev",
                )
            )
        if "checks.listForRef" in text or "const required" in text:
            findings.append(
                Finding(
                    "release-workflow",
                    str(RELEASE_WORKFLOW),
                    "release workflow must use the shared required-check projection",
                )
            )
        projection_text = self.read(SCRIPT_RELATIVE / "codex_refactor_loop" / "release" / "required_checks.py")
        for marker in REQUIRED_RELEASE_PROJECTION_MARKERS:
            if projection_text and marker not in projection_text:
                findings.append(
                    Finding(
                        "release-workflow",
                        str(SCRIPT_RELATIVE / "codex_refactor_loop" / "release" / "required_checks.py"),
                        f"shared release required-check projection missing {marker}",
                    )
                )
        if projection_text and '"skill-degradation"' not in projection_text:
            findings.append(
                Finding(
                    "release-workflow",
                    str(SCRIPT_RELATIVE / "codex_refactor_loop" / "release" / "required_checks.py"),
                    "shared release required checks must include skill-degradation",
                )
            )
        return findings

    def release_gate_required_check_present(self) -> list[Finding]:
        findings = self.require_markers(
            SCRIPT_RELATIVE / "codex_refactor_loop" / "release" / "gate.py",
            REQUIRED_RELEASE_GATE_MARKERS,
            "release-gate",
        )
        text = self.read(SCRIPT_RELATIVE / "codex_refactor_loop" / "release" / "gate.py")
        projection_text = self.read(SCRIPT_RELATIVE / "codex_refactor_loop" / "release" / "required_checks.py")
        if projection_text and '"skill-degradation"' not in projection_text:
            findings.append(
                Finding(
                    "release-gate",
                    str(SCRIPT_RELATIVE / "codex_refactor_loop" / "release" / "required_checks.py"),
                    "required_checks_recent_green must require skill-degradation",
                )
            )
        if text and "gh\", \"run\", \"list" in text:
            findings.append(
                Finding(
                    "release-gate",
                    str(SCRIPT_RELATIVE / "codex_refactor_loop" / "release" / "gate.py"),
                    "release gate must not read workflow-run names for required checks",
                )
            )
        return findings

    def monitor_hook_present(self) -> list[Finding]:
        findings = self.require_markers(
            SCRIPT_RELATIVE / "codex_refactor_loop" / "monitors" / "concurrency.py",
            REQUIRED_MONITOR_MARKERS,
            "monitor-hook",
        )
        text = self.read(SCRIPT_RELATIVE / "codex_refactor_loop" / "monitors" / "concurrency.py")
        if not text:
            return findings
        if "subprocess" + ".run(" not in text or "check-degradation" not in text:
            findings.append(
                Finding(
                    "monitor-hook",
                    str(SCRIPT_RELATIVE / "codex_refactor_loop" / "monitors" / "concurrency.py"),
                    "monitor hook must invoke the single CLI checker operation",
                )
            )
        for forbidden in ("Popen", "launch_dispatch", "top_up_from_dispatch_queue"):
            hook_body = extract_function_body(text, "run_skill_degradation_check")
            if forbidden in hook_body:
                findings.append(
                    Finding(
                        "monitor-hook",
                        str(SCRIPT_RELATIVE / "codex_refactor_loop" / "monitors" / "concurrency.py"),
                        f"degradation hook must not use {forbidden}",
                    )
                )
        return findings

    def peek_status_present(self) -> list[Finding]:
        return self.require_markers(SCRIPT_RELATIVE / "codex_refactor_loop" / "peek.py", REQUIRED_PEEK_MARKERS, "peek-status")

    def forbidden_surfaces_absent(self) -> list[Finding]:
        findings: list[Finding] = []
        for relative in CHECKED_SURFACE_FILES + (SCRIPT_RELATIVE / "codex_refactor_loop" / "checks" / "degradation.py",):
            path = self.repo_root / relative
            if not path.exists() or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for line_number, line in enumerate(text.splitlines(), start=1):
                if self.line_is_allowed_forbidden_context(line):
                    continue
                for pattern in FORBIDDEN_SURFACE_PATTERNS:
                    if pattern.search(line):
                        findings.append(
                            Finding(
                                "forbidden-surface",
                                f"{relative}:{line_number}",
                                f"forbidden expansion surface matched {pattern.pattern!r}",
                            )
                        )
        return findings

    def checker_is_read_only(self) -> list[Finding]:
        findings: list[Finding] = []
        relative = SCRIPT_RELATIVE / "codex_refactor_loop" / "checks" / "degradation.py"
        text = "\n".join(
            line
            for line in self.read(relative).splitlines()
            if "checker-read-only" not in line
            and "forbidden_patterns" not in line
            and not line.strip().startswith('r"')
            and '"subprocess" + ".' not in line
        )
        if not text:
            return findings
        forbidden_patterns = (
            r"\bsubprocess\.",
            r"\bos\.system\b",
            r"\bwrite_text\b",
            r"\bopen\([^)]*['\"]w",
            r"\bopen\([^)]*['\"]a",
            r"\bunlink\(",
            r"\brename\(",
            r"\breplace\(",
        )
        for pattern in forbidden_patterns:
            if re.search(pattern, text):
                findings.append(
                    Finding(
                        "checker-read-only",
                        str(relative),
                        f"checker contains forbidden mutating/runtime pattern {pattern}",
                    )
                )
        return findings

    def require_markers(self, relative: Path, markers: Iterable[str], check: str) -> list[Finding]:
        text = self.read(relative)
        if not text:
            return [Finding(check, str(relative), "required file is missing or empty")]
        findings: list[Finding] = []
        for marker in markers:
            if marker not in text:
                findings.append(Finding(check, str(relative), f"missing marker: {marker}"))
        return findings

    def read(self, relative: Path) -> str:
        path = self.repo_root / relative
        if not path.exists() or not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.repo_root))
        except ValueError:
            return str(path)

    @staticmethod
    def required_array_contains(text: str, name: str) -> bool:
        match = re.search(r"const\s+required\s*=\s*\[(?P<body>[^\]]+)\]", text)
        return bool(match and f'"{name}"' in match.group("body"))

    @staticmethod
    def line_is_allowed_forbidden_context(line: str) -> bool:
        if any(marker in line for marker in DOC_FORBIDDEN_CONTEXT):
            return True
        if line.lstrip().startswith(("REQUIRED_", '"')):
            return True
        if "FORBIDDEN_" in line:
            return True
        if "re.compile" in line:
            return True
        return False


def extract_function_body(text: str, name: str) -> str:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if re.match(rf"def\s+{re.escape(name)}\s*\(", line):
            start = index
            break
    if start is None:
        return ""
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("def ") and body:
            break
        body.append(line)
    return "\n".join(body)


def discover_repo_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / SKILL_RELATIVE / "SKILL.md").exists():
            return candidate
    raise RuntimeError("could not discover repo root containing skills/codex-refactor-loop/SKILL.md")


def run_static_check(repo_root: Path | None = None, *, ctx: LoopContext | None = None) -> list[Finding]:
    return SkillDriftChecker(repo_root, ctx=ctx).run_static()


def findings_payload(findings: list[Finding]) -> dict[str, object]:
    return {"ok": not findings, "findings": [finding.as_dict() for finding in findings]}


def format_findings(findings: list[Finding], *, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(findings_payload(findings), indent=2, sort_keys=True)
    if not findings:
        return "skill-degradation: ok"
    lines = [f"skill-degradation: {len(findings)} finding(s)"]
    lines.extend(f"{finding.severity}: {finding.check}: {finding.path}: {finding.message}" for finding in findings)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static", action="store_true", help="run static degradation checks")
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = args.repo_root or discover_repo_root(Path.cwd())
        findings = run_static_check(root)
    except Exception as exc:
        sys.stderr.write(f"skill-degradation: {exc}\n")
        return 2
    print(format_findings(findings, as_json=args.json))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
