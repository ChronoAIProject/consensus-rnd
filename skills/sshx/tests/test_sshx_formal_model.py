"""The Lean model under ``skills/sshx/formal`` stays coupled to ``SKILL.md`` clause by clause.

Mechanical couplings, none of which judges English semantics:

1. ``SKILL.md`` is split into atomic clauses (every sentence, bullet item, and table row
   outside fenced blocks). Every clause must be quoted verbatim, or contain a quoted
   fragment, in exactly one ``-- SKILL[kind]: "..."`` trace line of a Lean module. ``kind``
   is one of a closed set, and the trace must be followed within a few lines by a Lean
   declaration (or, for ``prose``, by a ``-- why:`` justification). Coverage is a ratchet
   held by ``MINIMUM_COVERAGE``; the goal is ``1.0``.
2. Every truth-table row of the contract must be traced in ``Sshx/Tables.lean``.
3. No module may contain ``sorry``, ``axiom``, ``native_decide``, or a proposition defined
   as bare ``True``.
4. When ``lake`` is installed the package must build. When ``lake`` is absent the build test
   is skipped with a visible reason; it never passes vacuously. The build resolves the pinned
   ``trureturing`` dependency (and Mathlib through it); ``skills/sshx/formal/README.md`` says
   how to reuse an already built checkout instead of cloning.
"""

import re
import shutil
import subprocess
import unittest
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / "skills" / "sshx" / "SKILL.md"
FORMAL = ROOT / "skills" / "sshx" / "formal"
MODULES = sorted((FORMAL / "Sshx").rglob("*.lean"))
TRACE_PATTERN = re.compile(r'^-- SKILL\[(?P<kind>[a-z]+)\]: "(?P<quote>.+)"$', re.MULTILINE)
UNTAGGED_TRACE_PATTERN = re.compile(r'^-- SKILL: "', re.MULTILINE)
TRACE_KINDS = frozenset({"def", "guard", "inv", "thm", "policy", "ref", "prose"})
DECLARATION_PATTERN = re.compile(
    r"^(?:noncomputable |private |protected )*(?:def|theorem|lemma|structure|inductive|abbrev|instance|alias|example)\b"
)
WHY_PATTERN = re.compile(r"^-- why: \S")
TRACE_WINDOW = 8
TRUTH_TABLE_HEADINGS = ("## Design Truth Table", "## Review Truth Table", "## Termination Truth Table")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+(?=[A-Z`\d(])")
MINIMUM_COVERAGE = 1.0


@dataclass(frozen=True)
class Clause:
    section: str
    text: str


@dataclass(frozen=True)
class Trace:
    module: str
    line: int
    kind: str
    quote: str


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def clauses(text: str) -> list[Clause]:
    """Every sentence, bullet item, and table row of the contract outside fenced blocks."""
    out: list[Clause] = []
    section = "(preamble)"
    in_fence = False
    for para in text.split("\n\n"):
        for line in para.splitlines():
            if line.startswith("```"):
                in_fence = not in_fence
        if in_fence or para.strip().startswith("```"):
            continue
        stripped = para.strip()
        if not stripped or stripped.startswith("---"):
            continue
        if stripped.startswith("#"):
            section = stripped.splitlines()[0]
            continue
        lines = stripped.splitlines()
        if all(line.startswith("|") for line in lines):
            for line in lines:
                if line.startswith("| Inputs") or line.startswith("|---"):
                    continue
                out.append(Clause(section, line.strip()))
            continue
        if all(line.startswith("- ") or line.startswith("  ") or re.match(r"^\d+\. ", line) for line in lines):
            for line in lines:
                if line.startswith("- ") or re.match(r"^\d+\. ", line):
                    out.append(Clause(section, line.strip()))
            continue
        flat = " ".join(line.strip() for line in lines)
        for sentence in SENTENCE_SPLIT.split(flat):
            sentence = sentence.strip()
            if sentence:
                out.append(Clause(section, sentence))
    return out


def traces(module: Path) -> list[Trace]:
    source = read(module)
    found: list[Trace] = []
    for match in TRACE_PATTERN.finditer(source):
        line = source.count("\n", 0, match.start()) + 1
        found.append(Trace(module.name, line, match.group("kind"), match.group("quote")))
    return found


def all_traces() -> list[Trace]:
    return [trace for module in MODULES for trace in traces(module)]


def trace_is_anchored(module: Path, trace: Trace) -> bool:
    lines = read(module).splitlines()
    window = lines[trace.line : trace.line + TRACE_WINDOW]
    if trace.kind == "prose":
        return any(WHY_PATTERN.match(line) for line in window[:2])
    return any(DECLARATION_PATTERN.match(line) for line in window)


def coverage(clause_list: list[Clause], trace_list: list[Trace]) -> tuple[list[Clause], list[Clause]]:
    covered: list[Clause] = []
    uncovered: list[Clause] = []
    for clause in clause_list:
        if any(trace.quote in clause.text for trace in trace_list):
            covered.append(clause)
        else:
            uncovered.append(clause)
    return covered, uncovered


def truth_table_rows(text: str, heading: str) -> list[str]:
    start = text.index(heading)
    end = text.index("\n## ", start + 1)
    body = text[start:end]
    return [line for line in body.splitlines() if line.startswith("| ") and not line.startswith("| Inputs") and not line.startswith("|---")]


class SshxFormalModelTests(unittest.TestCase):
    def test_every_trace_is_tagged_verbatim_and_anchored(self) -> None:
        skill = read(SKILL)
        self.assertGreaterEqual(len(MODULES), 14)
        self.assertTrue(any(module.parent.name == "Semantics" for module in MODULES))
        seen: dict[str, Trace] = {}
        for module in MODULES:
            source = read(module)
            with self.subTest(module=module.name):
                self.assertIsNone(UNTAGGED_TRACE_PATTERN.search(source), f"untagged `-- SKILL:` trace in {module.name}")
                for trace in traces(module):
                    self.assertIn(trace.kind, TRACE_KINDS, f"unknown trace kind in {module.name}:{trace.line}")
                    self.assertIn(trace.quote, skill, f"stale trace in {module.name}:{trace.line}: {trace.quote!r}")
                    self.assertTrue(trace_is_anchored(module, trace), f"unanchored trace in {module.name}:{trace.line}")
                    self.assertNotIn(trace.quote, seen, f"duplicate trace {trace.quote!r} in {module.name} and {seen.get(trace.quote)}")
                    seen[trace.quote] = trace

    def test_clause_coverage_ratchet(self) -> None:
        clause_list = clauses(read(SKILL))
        covered, uncovered = coverage(clause_list, all_traces())
        ratio = len(covered) / len(clause_list)
        by_section: dict[str, int] = {}
        for clause in uncovered:
            by_section[clause.section] = by_section.get(clause.section, 0) + 1
        summary = ", ".join(f"{section}: {count}" for section, count in by_section.items())
        self.assertGreaterEqual(
            ratio,
            MINIMUM_COVERAGE,
            f"clause coverage {ratio:.1%} below ratchet {MINIMUM_COVERAGE:.0%}; uncovered by section: {summary}",
        )

    def test_every_truth_table_row_is_traced(self) -> None:
        skill = read(SKILL)
        traced = {trace.quote for trace in traces(FORMAL / "Sshx" / "Tables.lean")}
        for heading in TRUTH_TABLE_HEADINGS:
            rows = truth_table_rows(skill, heading)
            with self.subTest(table=heading):
                self.assertTrue(rows)
                for row in rows:
                    self.assertIn(row, traced, f"untraced row under {heading}: {row}")

    def test_model_has_no_escape_hatches(self) -> None:
        for module in MODULES:
            source = read(module)
            with self.subTest(module=module.name):
                self.assertIsNone(re.search(r"\bsorry\b", source))
                self.assertIsNone(re.search(r"\bnative_decide\b", source))
                self.assertIsNone(re.search(r"^axiom\b", source, re.MULTILINE))
                self.assertIsNone(re.search(r":\s*Prop\s*:=\s*True\s*$", source, re.MULTILINE), "a proposition defined as bare True")

    def test_model_builds_when_lake_is_installed(self) -> None:
        lake = shutil.which("lake")
        if lake is None:
            self.skipTest("SKIP formal build: `lake` not installed (install elan to enable)")
        completed = subprocess.run(
            [lake, "build"],
            cwd=FORMAL,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertNotIn("error:", completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
