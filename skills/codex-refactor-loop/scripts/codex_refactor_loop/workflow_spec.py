"""Validated host workflow vocabulary for codex-refactor-loop."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from .workflow_stages import WORKFLOW_STAGES, WorkflowStage


class WorkflowSpecError(ValueError):
    """Raised when HOST_WORKFLOW_SPEC cannot be trusted."""


HOST_NAME_RE = re.compile(r"^host:[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
BUILTIN_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
FORBIDDEN_FIELD_NAMES = {
    "add_label",
    "add_labels",
    "argv",
    "args",
    "assignee",
    "assignees",
    "branch",
    "checkout",
    "close",
    "cmd",
    "command",
    "commands",
    "commit",
    "create_issue",
    "create_pr",
    "delete_branch",
    "edit_label",
    "exec",
    "executor",
    "force_push",
    "git",
    "import",
    "label",
    "labels",
    "merge",
    "milestone",
    "push",
    "rebase",
    "remove_label",
    "remove_labels",
    "reset",
    "shell",
    "tag",
}
RESERVED_NAMES = {
    "audit",
    "manual-issue",
    "audit-cluster",
    "manual-work-unit",
    "issue-219",
    "phase9",
    "phase9-router",
    "minimal",
    "structural",
    "delete",
    "judge",
    "reflector",
    *(stage.slug for stage in WORKFLOW_STAGES),
}
FIXED_MARKER_FAMILIES = ("SOLVER_DONE", "META_JUDGE_DONE", "META_RESOLVED")


@dataclass(frozen=True)
class HostWorkflowEvent:
    name: str
    stage: str
    status: str = ""
    actor: str = "controller"


@dataclass(frozen=True)
class HostWorkflowRole:
    name: str
    prompt_binding: str = ""


@dataclass(frozen=True)
class HostConsensusPolicy:
    name: str
    solver_roles: tuple[str, ...]
    judge_role: str
    peer_output_isolation: bool
    marker_families: tuple[str, ...]
    stage: str = "design-consensus"


@dataclass(frozen=True)
class HostIssueIntakeMapping:
    name: str
    work_unit_kind: str
    producer: str
    stage: str
    prompt_binding: str = ""


@dataclass(frozen=True)
class ValidatedWorkflowSpec:
    source_path: Path | None
    stages: tuple[WorkflowStage, ...]
    events: tuple[HostWorkflowEvent, ...]
    work_unit_kinds: tuple[str, ...]
    roles: tuple[HostWorkflowRole, ...]
    prompt_bindings: Mapping[str, str]
    consensus_policies: tuple[HostConsensusPolicy, ...]
    issue_intake_mappings: tuple[HostIssueIntakeMapping, ...]
    builtin: bool = False

    def stage_for_event(self, event_name: str) -> str | None:
        for event in self.events:
            if event.name == event_name:
                return event.stage
        return None

    def prompt_binding_path(self, name: str) -> str | None:
        return self.prompt_bindings.get(name)


class HostWorkflowSpec:
    def __init__(self, data: Mapping[str, Any], *, source_path: Path, repo_root: Path) -> None:
        self.data = dict(data)
        self.source_path = source_path
        self.repo_root = repo_root

    def validate(self) -> ValidatedWorkflowSpec:
        return WorkflowInvariantValidator(self.repo_root, self.source_path).validate(self.data)


class WorkflowInvariantValidator:
    ALLOWED_TOP_LEVEL = {
        "events",
        "stages",
        "work_unit_kinds",
        "roles",
        "prompt_bindings",
        "consensus_policies",
        "issue_intake_mappings",
    }

    def __init__(self, repo_root: Path, source_path: Path | None = None) -> None:
        self.repo_root = repo_root.resolve()
        self.source_path = source_path

    def validate(self, data: Mapping[str, Any]) -> ValidatedWorkflowSpec:
        if not isinstance(data, Mapping):
            raise WorkflowSpecError("HostWorkflowSpec must be a JSON object")
        self._reject_forbidden_fields(data)
        unknown = sorted(set(data) - self.ALLOWED_TOP_LEVEL)
        if unknown:
            raise WorkflowSpecError(f"unknown HostWorkflowSpec fields: {','.join(unknown)}")

        stages = self._parse_stages(data.get("stages", []))
        prompt_bindings = self._parse_prompt_bindings(data.get("prompt_bindings", {}))
        roles = self._parse_roles(data.get("roles", []), prompt_bindings)
        work_unit_kinds = self._parse_work_unit_kinds(data.get("work_unit_kinds", []))
        events = self._parse_events(data.get("events", []), stages)
        policies = self._parse_consensus_policies(data.get("consensus_policies", []), roles, stages)
        intakes = self._parse_issue_intake_mappings(data.get("issue_intake_mappings", []), work_unit_kinds, stages, prompt_bindings)
        return ValidatedWorkflowSpec(
            source_path=self.source_path,
            stages=tuple(stages),
            events=tuple(events),
            work_unit_kinds=tuple(work_unit_kinds),
            roles=tuple(roles),
            prompt_bindings=dict(prompt_bindings),
            consensus_policies=tuple(policies),
            issue_intake_mappings=tuple(intakes),
            builtin=False,
        )

    def _reject_forbidden_fields(self, value: Any, path: str = "") -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if str(key) in FORBIDDEN_FIELD_NAMES:
                    raise WorkflowSpecError(f"forbidden lifecycle or command field: {path}{key}")
                self._reject_forbidden_fields(child, f"{path}{key}.")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                self._reject_forbidden_fields(child, f"{path}{index}.")

    def _parse_stages(self, raw: Any) -> list[WorkflowStage]:
        stages: list[WorkflowStage] = []
        seen: set[str] = set()
        for item in _items(raw, "stages"):
            slug = _string_field(item, "slug")
            self._assert_host_name(slug, "stage slug")
            if slug in seen:
                raise WorkflowSpecError(f"duplicate host stage: {slug}")
            seen.add(slug)
            stages.append(
                WorkflowStage(
                    slug=slug,
                    title=_string_field(item, "title"),
                    contract=_string_field(item, "contract"),
                    detail_anchor=str(item.get("detail_anchor") or slug.replace(":", "-")),
                    legacy_number=-1,
                )
            )
        return stages

    def _parse_prompt_bindings(self, raw: Any) -> dict[str, str]:
        if isinstance(raw, Mapping):
            iterable = [{"name": key, "path": value} for key, value in raw.items()]
        else:
            iterable = _items(raw, "prompt_bindings")
        bindings: dict[str, str] = {}
        for item in iterable:
            name = _string_field(item, "name")
            self._assert_host_name(name, "prompt binding")
            if name in bindings:
                raise WorkflowSpecError(f"duplicate prompt binding: {name}")
            bindings[name] = self._repo_relative_file(_string_field(item, "path"), "prompt binding path")
        return bindings

    def _parse_roles(self, raw: Any, prompt_bindings: Mapping[str, str]) -> list[HostWorkflowRole]:
        roles: list[HostWorkflowRole] = []
        seen: set[str] = set()
        for item in _items(raw, "roles"):
            name = _string_field(item, "name")
            self._assert_host_name(name, "role")
            if name in seen:
                raise WorkflowSpecError(f"duplicate host role: {name}")
            prompt_binding = str(item.get("prompt_binding") or "")
            if prompt_binding:
                self._assert_host_name(prompt_binding, "role prompt binding")
                if prompt_binding not in prompt_bindings:
                    raise WorkflowSpecError(f"unknown role prompt binding: {prompt_binding}")
            seen.add(name)
            roles.append(HostWorkflowRole(name=name, prompt_binding=prompt_binding))
        return roles

    def _parse_work_unit_kinds(self, raw: Any) -> list[str]:
        kinds: list[str] = []
        seen: set[str] = set()
        for item in _items(raw, "work_unit_kinds"):
            name = _string_field(item, "name") if isinstance(item, Mapping) else _string_value(item, "work_unit_kinds")
            self._assert_host_name(name, "work unit kind")
            if name in seen:
                raise WorkflowSpecError(f"duplicate work unit kind: {name}")
            seen.add(name)
            kinds.append(name)
        return kinds

    def _parse_events(self, raw: Any, stages: Iterable[WorkflowStage]) -> list[HostWorkflowEvent]:
        stage_slugs = {stage.slug for stage in WORKFLOW_STAGES}
        stage_slugs.update(stage.slug for stage in stages)
        events: list[HostWorkflowEvent] = []
        seen: set[str] = set()
        for item in _items(raw, "events"):
            name = _string_field(item, "name")
            self._assert_host_name(name, "event")
            if name in seen:
                raise WorkflowSpecError(f"duplicate host event: {name}")
            stage = _string_field(item, "stage")
            if stage not in stage_slugs:
                raise WorkflowSpecError(f"unknown event stage: {stage}")
            status = str(item.get("status") or "")
            if status:
                self._assert_host_name(status, "event status")
            actor = str(item.get("actor") or "controller")
            events.append(HostWorkflowEvent(name=name, stage=stage, status=status, actor=actor))
            seen.add(name)
        return events

    def _parse_consensus_policies(
        self,
        raw: Any,
        roles: Iterable[HostWorkflowRole],
        stages: Iterable[WorkflowStage],
    ) -> list[HostConsensusPolicy]:
        role_names = {role.name for role in roles}
        stage_slugs = {stage.slug for stage in WORKFLOW_STAGES}
        stage_slugs.update(stage.slug for stage in stages)
        policies: list[HostConsensusPolicy] = []
        for item in _items(raw, "consensus_policies"):
            name = _string_field(item, "name")
            self._assert_host_name(name, "consensus policy")
            solver_roles = tuple(_string_value(value, "solver_roles") for value in _list_field(item, "solver_roles"))
            if len(set(solver_roles)) < 3:
                raise WorkflowSpecError("consensus policy requires at least three independent solvers")
            for role in solver_roles:
                if role not in role_names:
                    raise WorkflowSpecError(f"unknown consensus solver role: {role}")
            judge_role = _string_field(item, "judge_role")
            if judge_role not in role_names:
                raise WorkflowSpecError(f"unknown consensus judge role: {judge_role}")
            if judge_role in solver_roles:
                raise WorkflowSpecError("consensus judge must be independent from solvers")
            if item.get("peer_output_isolation") is not True:
                raise WorkflowSpecError("consensus policy must require peer_output_isolation")
            marker_families = tuple(_string_value(value, "marker_families") for value in _list_field(item, "marker_families"))
            if marker_families != FIXED_MARKER_FAMILIES:
                raise WorkflowSpecError("consensus policy marker families must stay fixed")
            stage = str(item.get("stage") or "design-consensus")
            if stage not in stage_slugs:
                raise WorkflowSpecError(f"unknown consensus policy stage: {stage}")
            policies.append(
                HostConsensusPolicy(
                    name=name,
                    solver_roles=solver_roles,
                    judge_role=judge_role,
                    peer_output_isolation=True,
                    marker_families=marker_families,
                    stage=stage,
                )
            )
        return policies

    def _parse_issue_intake_mappings(
        self,
        raw: Any,
        work_unit_kinds: Iterable[str],
        stages: Iterable[WorkflowStage],
        prompt_bindings: Mapping[str, str],
    ) -> list[HostIssueIntakeMapping]:
        kind_names = set(work_unit_kinds)
        stage_slugs = {stage.slug for stage in WORKFLOW_STAGES}
        stage_slugs.update(stage.slug for stage in stages)
        mappings: list[HostIssueIntakeMapping] = []
        for item in _items(raw, "issue_intake_mappings"):
            name = _string_field(item, "name")
            self._assert_host_name(name, "issue intake mapping")
            work_unit_kind = _string_field(item, "work_unit_kind")
            if work_unit_kind not in kind_names:
                raise WorkflowSpecError(f"unknown intake work unit kind: {work_unit_kind}")
            producer = _string_field(item, "producer")
            self._assert_host_name(producer, "issue intake producer")
            stage = _string_field(item, "stage")
            if stage not in stage_slugs:
                raise WorkflowSpecError(f"unknown intake stage: {stage}")
            prompt_binding = str(item.get("prompt_binding") or "")
            if prompt_binding and prompt_binding not in prompt_bindings:
                raise WorkflowSpecError(f"unknown intake prompt binding: {prompt_binding}")
            mappings.append(
                HostIssueIntakeMapping(
                    name=name,
                    work_unit_kind=work_unit_kind,
                    producer=producer,
                    stage=stage,
                    prompt_binding=prompt_binding,
                )
            )
        return mappings

    def _assert_host_name(self, value: str, label: str) -> None:
        if value in RESERVED_NAMES or BUILTIN_NAME_RE.fullmatch(value):
            raise WorkflowSpecError(f"{label} cannot overwrite built-in vocabulary: {value}")
        if not HOST_NAME_RE.fullmatch(value):
            raise WorkflowSpecError(f"{label} must use host: namespace: {value}")

    def _repo_relative_file(self, text: str, label: str) -> str:
        pure = PurePosixPath(text)
        if not text or pure.is_absolute() or "\\" in text or ".." in pure.parts:
            raise WorkflowSpecError(f"{label} must be repo-relative POSIX text")
        raw_path = self.repo_root / Path(*pure.parts)
        if raw_path.is_symlink():
            target = raw_path.resolve()
            try:
                target.relative_to(self.repo_root)
            except ValueError as exc:
                raise WorkflowSpecError(f"{label} symlink escapes REPO_ROOT") from exc
        path = raw_path.resolve()
        try:
            path.relative_to(self.repo_root)
        except ValueError as exc:
            raise WorkflowSpecError(f"{label} escapes REPO_ROOT") from exc
        if not path.is_file():
            raise WorkflowSpecError(f"{label} does not exist: {text}")
        return pure.as_posix()


class WorkflowSpecLoader:
    _cache: dict[tuple[Path, int, int], ValidatedWorkflowSpec] = {}

    @classmethod
    def load(cls, ctx: Any) -> ValidatedWorkflowSpec:
        raw = str(getattr(ctx, "host_workflow_spec_path", "") or "").strip()
        if not raw:
            return builtin_workflow_spec()
        path = _repo_relative_path(getattr(ctx, "repo_root"), raw, "HOST_WORKFLOW_SPEC")
        stat = path.stat()
        key = (path, stat.st_mtime_ns, stat.st_size)
        cached = cls._cache.get(key)
        if cached is not None:
            return cached
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise WorkflowSpecError(f"HOST_WORKFLOW_SPEC malformed JSON: {exc}") from exc
        spec = HostWorkflowSpec(data, source_path=path, repo_root=getattr(ctx, "repo_root")).validate()
        cls._cache = {key: spec}
        return spec


def builtin_workflow_spec() -> ValidatedWorkflowSpec:
    return ValidatedWorkflowSpec(
        source_path=None,
        stages=(),
        events=(),
        work_unit_kinds=("audit-cluster", "manual-work-unit"),
        roles=(
            HostWorkflowRole("minimal", "solver-minimal"),
            HostWorkflowRole("structural", "solver-structural"),
            HostWorkflowRole("delete", "solver-delete"),
            HostWorkflowRole("judge", "meta-judge"),
            HostWorkflowRole("reflector", "meta-reflector-stalled"),
        ),
        prompt_bindings={
            "solver-minimal": "prompts/solver-minimal.md",
            "solver-structural": "prompts/solver-structural.md",
            "solver-delete": "prompts/solver-delete.md",
            "meta-judge": "prompts/meta-judge.md",
            "meta-reflector-stalled": "prompts/meta-reflector-stalled.md",
        },
        consensus_policies=(
            HostConsensusPolicy(
                name="design-consensus",
                solver_roles=("minimal", "structural", "delete"),
                judge_role="judge",
                peer_output_isolation=True,
                marker_families=FIXED_MARKER_FAMILIES,
                stage="design-consensus",
            ),
        ),
        issue_intake_mappings=(
            HostIssueIntakeMapping(
                name="manual-issue",
                work_unit_kind="manual-work-unit",
                producer="manual-issue",
                stage="design-consensus",
                prompt_binding="",
            ),
        ),
        builtin=True,
    )


def merged_with_builtin(host_spec: ValidatedWorkflowSpec) -> ValidatedWorkflowSpec:
    if host_spec.builtin:
        return host_spec
    builtin = builtin_workflow_spec()
    return replace(
        host_spec,
        stages=host_spec.stages,
        work_unit_kinds=(*builtin.work_unit_kinds, *host_spec.work_unit_kinds),
        roles=(*builtin.roles, *host_spec.roles),
        prompt_bindings={**builtin.prompt_bindings, **dict(host_spec.prompt_bindings)},
        consensus_policies=(*builtin.consensus_policies, *host_spec.consensus_policies),
        issue_intake_mappings=(*builtin.issue_intake_mappings, *host_spec.issue_intake_mappings),
    )


def load_validated_workflow_spec(ctx: Any) -> ValidatedWorkflowSpec:
    return merged_with_builtin(WorkflowSpecLoader.load(ctx))


def _repo_relative_path(repo_root: Path, text: str, label: str) -> Path:
    pure = PurePosixPath(text)
    if not text or pure.is_absolute() or "\\" in text or ".." in pure.parts:
        raise WorkflowSpecError(f"{label} must be repo-relative POSIX text")
    repo = Path(repo_root).resolve()
    raw_path = repo / Path(*pure.parts)
    if raw_path.is_symlink():
        target = raw_path.resolve()
        try:
            target.relative_to(repo)
        except ValueError as exc:
            raise WorkflowSpecError(f"{label} symlink escapes REPO_ROOT") from exc
    path = raw_path.resolve()
    try:
        path.relative_to(repo)
    except ValueError as exc:
        raise WorkflowSpecError(f"{label} escapes REPO_ROOT") from exc
    if not path.is_file():
        raise WorkflowSpecError(f"{label} does not exist: {text}")
    return path


def _items(raw: Any, label: str) -> list[Any]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise WorkflowSpecError(f"{label} must be a list")
    return raw


def _string_field(item: Any, field: str) -> str:
    if not isinstance(item, Mapping):
        raise WorkflowSpecError(f"{field} entry must be an object")
    return _string_value(item.get(field), field)


def _string_value(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise WorkflowSpecError(f"{label} must be a non-empty string")
    return value


def _list_field(item: Any, field: str) -> list[Any]:
    if not isinstance(item, Mapping):
        raise WorkflowSpecError(f"{field} entry must be an object")
    value = item.get(field)
    if not isinstance(value, list):
        raise WorkflowSpecError(f"{field} must be a list")
    return value
