"""The Lean model under ``skills/sshx/formal`` stays coupled to ``SKILL.md``.

Two mechanical couplings, neither of which judges English semantics:

1. every ``-- SKILL: "..."`` trace line in a Lean module must be a verbatim substring of
   ``SKILL.md``, and every truth-table row of the contract must be traced in
   ``Sshx/Tables.lean``; editing a modeled clause therefore fails this suite until the
   model is revisited;
2. when ``lake`` is installed the model must build with no ``sorry``, ``axiom``, or
   ``native_decide``. When ``lake`` is absent the build test is skipped with a visible
   reason; it never passes vacuously. The build resolves the pinned ``trureturing``
   dependency (and Mathlib through it); ``skills/sshx/formal/README.md`` says how to
   reuse an already built checkout instead of cloning.
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / "skills" / "sshx" / "SKILL.md"
FORMAL = ROOT / "skills" / "sshx" / "formal"
MODULES = sorted((FORMAL / "Sshx").rglob("*.lean"))
TRACE_PATTERN = re.compile(r'^-- SKILL: "(.+)"$', re.MULTILINE)
TRUTH_TABLE_HEADINGS = ("## Design Truth Table", "## Review Truth Table", "## Termination Truth Table")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def trace_quotes(module: Path) -> list[str]:
    return TRACE_PATTERN.findall(read(module))


def truth_table_rows(text: str, heading: str) -> list[str]:
    start = text.index(heading)
    end = text.index("\n## ", start + 1)
    body = text[start:end]
    return [line for line in body.splitlines() if line.startswith("| ") and not line.startswith("| Inputs") and not line.startswith("|---")]


class SshxFormalModelTests(unittest.TestCase):
    def test_every_module_traces_the_contract_verbatim(self) -> None:
        skill = read(SKILL)
        self.assertGreaterEqual(len(MODULES), 14)
        self.assertTrue(any(module.parent.name == "Semantics" for module in MODULES))
        for module in MODULES:
            quotes = trace_quotes(module)
            with self.subTest(module=module.name):
                self.assertTrue(quotes, f"{module.name} carries no `-- SKILL:` trace")
                for quote in quotes:
                    self.assertIn(quote, skill, f"stale trace in {module.name}: {quote!r}")

    def test_every_truth_table_row_is_traced(self) -> None:
        skill = read(SKILL)
        traced = set(trace_quotes(FORMAL / "Sshx" / "Tables.lean"))
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

    def test_model_builds_when_lake_is_installed(self) -> None:
        lake = shutil.which("lake")
        if lake is None:
            self.skipTest("SKIP formal build: `lake` not installed (install elan to enable)")
        completed = subprocess.run(
            [lake, "build"],
            cwd=FORMAL,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertNotIn("error:", completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
