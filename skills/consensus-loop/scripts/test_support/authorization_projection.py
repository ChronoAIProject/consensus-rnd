"""Projection helpers for authorization source-regression tests."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class MarkdownProjection:
    anchors: frozenset[str]
    headings: frozenset[str]
    bullet_fields: frozenset[str]
    links: frozenset[str]


@dataclass(frozen=True)
class PythonProjection:
    function_names: frozenset[str]
    class_names: frozenset[str]
    attribute_names: frozenset[str]
    imported_modules: frozenset[str]
    imported_names: frozenset[str]
    assigned_names: frozenset[str]
    string_literals: frozenset[str]
    env_get_names: frozenset[str]
    dict_keys: frozenset[str]
    set_members: dict[str, frozenset[str]]


def project_markdown(text: str) -> MarkdownProjection:
    anchors = frozenset(re.findall(r'<a id="([^"]+)"></a>', text))
    headings = frozenset(line.strip() for line in text.splitlines() if line.startswith("#"))
    bullet_fields = frozenset(
        match.group(1)
        for match in re.finditer(r"(?m)^- ([A-Za-z0-9_ -]+):", text)
    )
    links = frozenset(re.findall(r"\]\(([^)]+)\)", text))
    return MarkdownProjection(
        anchors=anchors,
        headings=headings,
        bullet_fields=bullet_fields,
        links=links,
    )


def project_python(text: str) -> PythonProjection:
    tree = ast.parse(text)
    function_names: set[str] = set()
    class_names: set[str] = set()
    attribute_names: set[str] = set()
    imported_modules: set[str] = set()
    imported_names: set[str] = set()
    assigned_names: set[str] = set()
    string_literals: set[str] = set()
    env_get_names: set[str] = set()
    dict_keys: set[str] = set()
    set_members: dict[str, set[str]] = {}

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function_names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            class_names.add(node.name)
        elif isinstance(node, ast.Attribute):
            attribute_names.add(node.attr)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.add(node.module or "")
            for alias in node.names:
                imported_names.add(alias.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned_names.add(target.id)
                    values = _literal_string_members(node.value)
                    if values:
                        set_members[target.id] = set(values)
                elif isinstance(target, ast.Tuple):
                    for element in target.elts:
                        if isinstance(element, ast.Name):
                            assigned_names.add(element.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assigned_names.add(node.target.id)
            values = _literal_string_members(node.value)
            if values:
                set_members[node.target.id] = set(values)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            string_literals.add(node.value)
        elif isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    dict_keys.add(key.value)
        if _is_os_environ_get(node):
            env_name = _first_string_arg(node)
            if env_name:
                env_get_names.add(env_name)

    return PythonProjection(
        function_names=frozenset(function_names),
        class_names=frozenset(class_names),
        attribute_names=frozenset(attribute_names),
        imported_modules=frozenset(imported_modules),
        imported_names=frozenset(imported_names),
        assigned_names=frozenset(assigned_names),
        string_literals=frozenset(string_literals),
        env_get_names=frozenset(env_get_names),
        dict_keys=frozenset(dict_keys),
        set_members={key: frozenset(value) for key, value in set_members.items()},
    )


def _literal_string_members(node: ast.AST | None) -> frozenset[str]:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "frozenset" and node.args:
        return _literal_string_members(node.args[0])
    if not isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        return frozenset()
    values: set[str] = set()
    for element in node.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            values.add(element.value)
    return frozenset(values)


def _is_os_environ_get(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "environ"
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "os"
    )


def _first_string_arg(node: ast.Call) -> str:
    if not node.args:
        return ""
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return ""
