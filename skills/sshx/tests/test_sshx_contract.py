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


def without_refactor_comments(text: str) -> str:
    return re.sub(r"<!--\nRefactor .*?\n-->\n", "", text, flags=re.DOTALL)


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
            "## Goal Contract",
            "## InlineConsensusProtocol",
            "## Worker Delegation",
            "## No Context Pollution",
            "## Design Truth Table",
            "## Implementation Worker",
            "## Review Truth Table",
            "## Fix Or Done",
            "## Boundaries",
            "## Baseline Failure Mode",
            "## Verification",
        ]:
            self.assertIn(anchor, text)
        self.assertIn(
            "`intake` (write `GoalArtifact` and normalize the goal)\n2. `choose_worker_mode`\n3. `thinking_triplet_workers`\n4. `meta_judge`\n5. `implementation_worker`\n6. `review_triplet_workers`\n7. `fix_or_done`",
            text,
        )

    def test_sshx_goal_contract_source_regression(self) -> None:
        text = read(SKILL)
        self.assertIn("## Goal Contract", text)
        self.assertIn("`GoalArtifact` is a prompt-level record, not a runtime API", text)
        self.assertIn("It is written during `intake` before worker mode selection or any worker dispatch", text)
        for field in [
            "`raw_user_input`",
            "`normalized_goal`",
            "`constraints`",
            "`success_criteria`",
            "`iteration_question`",
        ]:
            self.assertIn(field, text)
        self.assertIn("The user's current input is the only source for the goal", text)
        self.assertIn(
            "must not discover or infer the goal from `codex-refactor-loop` milestones, release state, `host.env`, GitHub issues, GitHub pull requests, labels, branches, or any other external lifecycle surface",
            text,
        )

    def test_sshx_goal_iteration_behavior_contract(self) -> None:
        text = read(SKILL)
        self.assertIn("`intake` (write `GoalArtifact` and normalize the goal)", text)
        self.assertIn(
            "Each `visible_inputs` value must include the same `GoalArtifact.normalized_goal` and must not include same-round peer outputs",
            text,
        )
        self.assertIn(
            "`revise` must name the goal gap and a next iteration question; it must not open an unrelated design search",
            text,
        )
        self.assertIn(
            "The convergence question must be \"what still differs from `GoalArtifact`?\"",
            text,
        )
        self.assertIn("Do not generalize the convergence pass beyond that goal gap", text)
        self.assertIn(
            "ask what still differs from `GoalArtifact`, apply the smallest change that addresses that blocking goal gap",
            text,
        )
        self.assertIn("next_iteration_question:", text)

    def test_sshx_worker_modes(self) -> None:
        text = read(SKILL)
        contract_text = without_refactor_comments(text)
        mode_lines = re.findall(r"^\d+\. `(codex-cli|isolated-token-subagent|abstain)`$", text, re.MULTILINE)
        self.assertEqual(mode_lines, ["codex-cli", "isolated-token-subagent", "abstain"])
        self.assertIn("`WorkerDelegationContract`", text)
        self.assertIn("`codex-cli` is the default worker carrier after a non-mutating capability check", text)
        self.assertIn("`isolated-token-subagent` is the fallback when `codex-cli` is unavailable", text)
        self.assertIn(
            "`abstain` is required when neither `codex-cli` nor `isolated-token-subagent` is available",
            text,
        )
        self.assertIn("Do not self-apply the triplet inside the caller context", text)
        self.assertNotIn("sealed-transcript", contract_text)
        self.assertNotIn("actor-isolated", contract_text)

    def test_sshx_no_context_pollution_contract(self) -> None:
        text = read(SKILL)
        self.assertIn("The caller context must not carry worker full reasoning or same-round peer outputs", text)
        for allowed_context in [
            "intake inputs and constraints",
            "dispatch briefs sent to each worker",
            "worker summaries, verdicts, and explicitly surfaced blockers",
            "meta-judge synthesis",
            "final summary and report",
        ]:
            self.assertIn(allowed_context, text)
        self.assertIn(
            "Same-round thinking workers must not see one another's outputs before their own verdicts are returned",
            text,
        )
        self.assertIn(
            "If worker isolation is unavailable, exit through `abstain` instead of degrading the protocol into single-context roleplay",
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
        self.assertIn("Allowed worker carriers are limited to `codex-cli` and `isolated-token-subagent`", text)
        self.assertIn("not as controller authority", text)

    def test_sshx_baseline_evidence_is_source_owned(self) -> None:
        text = read(SKILL)
        for failure_mode in [
            "prompt-only self-application where worker reasoning lives in the caller context",
            "transcript-based pseudo-isolation presented as enough for independent workers",
            "single-threaded advice presented as enough for consensus",
            "no required worker mode declaration for peer perspectives",
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
        self.assertIn("轻量 worker-delegated inline 共识方法论", read(README))
        self.assertIn("`sshx`", read(GEMINI))
        self.assertIn("worker-delegated inline consensus", read(GEMINI))
        self.assertIn("python3 -m unittest discover -s skills/sshx/tests -p 'test_*.py'", read(CI))


if __name__ == "__main__":
    unittest.main()
