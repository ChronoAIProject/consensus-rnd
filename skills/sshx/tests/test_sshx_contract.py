import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / "skills" / "sshx" / "SKILL.md"
README = ROOT / "README.md"
GEMINI = ROOT / "GEMINI.md"
CI = ROOT / ".github" / "workflows" / "consensus-rnd-ci.yml"
BASELINE_ARTIFACT = ROOT / ".refactor-loop" / "runs" / "baseline-issue342-sshx.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"---\n(?P<body>.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise AssertionError("missing frontmatter")
    result: dict[str, str] = {}
    for line in match.group("body").splitlines():
        key, value = line.split(": ", 1)
        result[key] = value
    return result


class SshxContractTests(unittest.TestCase):
    def test_sshx_frontmatter_contract(self) -> None:
        meta = frontmatter(read(SKILL))
        self.assertEqual(set(meta), {"name", "description"})
        self.assertEqual(meta["name"], "sshx")
        self.assertTrue(meta["description"].startswith("Use when"))
        self.assertLessEqual(len(meta["description"]), 1024)

    def test_sshx_required_anchors(self) -> None:
        text = read(SKILL)
        for anchor in [
            "## Trigger",
            "## InlineConsensusProtocol",
            "## IsolationMode",
            "## Design Truth Table",
            "## Review Truth Table",
            "## Fix Or Done",
            "## Boundaries",
            "## Baseline Failure Mode",
            "## Verification",
        ]:
            self.assertIn(anchor, text)
        self.assertIn(
            "`intake`\n2. `choose_isolation_mode`\n3. `thinking_triplet`\n4. `meta_judge`\n5. `implement`\n6. `review_triplet`\n7. `fix_or_done`",
            text,
        )

    def test_sshx_isolation_modes(self) -> None:
        text = read(SKILL)
        mode_lines = re.findall(r"^\d+\. `(actor-isolated|sealed-transcript|abstain)`$", text, re.MULTILINE)
        self.assertEqual(mode_lines, ["actor-isolated", "sealed-transcript", "abstain"])
        self.assertIn("`sealed-transcript` is a degraded fallback", text)
        self.assertIn("auditable weak isolation, not strict peer invisibility", text)
        self.assertIn(
            "`abstain` is required when strict peer invisibility is mandatory and `actor-isolated` is unavailable",
            text,
        )

    def test_sshx_design_truth_table(self) -> None:
        text = read(SKILL)
        for row in [
            "| unanimous actionable plan | `implement` |",
            "| close disagreement with compatible plans | `meta-layer convergence` |",
            "| bounded true stall | `abstain/escalate with options` |",
            "| any attempt to use one perspective as consensus | `reject fake consensus` |",
        ]:
            self.assertIn(row, text)
        self.assertIn("stop with options instead of inventing agreement", text)

    def test_sshx_review_truth_table(self) -> None:
        text = read(SKILL)
        for row in [
            "| any explicit reject | `fix` |",
            "| no reject and at least one approve | `done with advisory surfaced` |",
            "| all comment and no approve | `explicit user decision or another bounded review pass` |",
        ]:
            self.assertIn(row, text)
        self.assertIn("Advisory comments do not count as approval", text)
        self.assertIn("A reject blocks done", text)

    def test_sshx_no_runtime_control_plane_leakage(self) -> None:
        text = read(SKILL)
        for forbidden_boundary in [
            "helper scripts",
            "daemons",
            "`consensus-rnd-cli`",
            "GitHub lifecycle operations",
            "git lifecycle operations",
            "labels",
            "release authority",
            "a public marker family",
            "`.refactor-loop/host.env` as a production source of truth",
            "`codex-refactor-loop` internal prompts or scripts as an implementation dependency",
        ]:
            self.assertIn(forbidden_boundary, text)
        self.assertIn("It must not add or depend on", text)
        self.assertIn("does not grant permission to commit, push, merge", text)

    def test_sshx_baseline_evidence_is_source_owned(self) -> None:
        text = read(SKILL)
        for failure_mode in [
            "single-threaded advice presented as enough for consensus",
            "no required isolation declaration for peer perspectives",
            "no fixed thinking truth table",
            "no same-shape review gate before done",
            "only need inline consensus",
        ]:
            self.assertIn(failure_mode, text)
        self.assertIn("source-owned contract or test evidence", text)
        self.assertIn("Do not track `.refactor-loop/` runtime artifacts", text)
        self.assertFalse(BASELINE_ARTIFACT.exists())

    def test_sshx_docs_and_ci_discovery(self) -> None:
        self.assertRegex(read(README), r"\| `sshx` \|")
        self.assertIn("轻量 inline 共识方法论", read(README))
        self.assertIn("`sshx`", read(GEMINI))
        self.assertIn("inline consensus", read(GEMINI))
        self.assertIn("python3 -m unittest discover -s skills/sshx/tests -p 'test_*.py'", read(CI))


if __name__ == "__main__":
    unittest.main()
