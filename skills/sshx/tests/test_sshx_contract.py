import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / "skills" / "sshx" / "SKILL.md"
README = ROOT / "README.md"
GEMINI = ROOT / "GEMINI.md"
CI = ROOT / ".github" / "workflows" / "consensus-rnd-ci.yml"
BASELINE_ARTIFACT = ROOT / ".refactor-loop" / "runs" / "baseline-issue342-sshx.md"

THINKING_VERDICTS = {"propose", "revise", "reject", "abstain"}


class ContractFailure(ValueError):
    pass


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


<<<<<<< HEAD
# Fix (remote-ci/contract-tests): keep both #483 helper groups after a
# conflict-marker regression made this test module fail before discovery.
def completed_worker_verdict(
    *,
    process_exited: bool,
    exit_code: int | None,
    result_artifact: dict[str, object] | None,
    allowed_verdicts: set[str],
) -> str:
    if not process_exited:
        raise ContractFailure("worker carrier still running")
    if exit_code != 0:
        raise ContractFailure("worker carrier did not exit 0")
    if result_artifact is None:
        raise ContractFailure("missing result_ref artifact")
    if set(result_artifact) != {"conclusion", "log_ref"}:
        raise ContractFailure("result_ref is not an SshxResultEnvelope")
    if not result_artifact["log_ref"]:
        raise ContractFailure("missing log_ref")
    conclusion = result_artifact["conclusion"]
    if not isinstance(conclusion, dict):
        raise ContractFailure("missing conclusion")
    verdict = conclusion.get("verdict")
    if verdict not in allowed_verdicts:
        raise ContractFailure("invalid conclusion.verdict")
    return str(verdict)


=======
>>>>>>> origin/auto-refact-dev
def flight_blocks_mutation(flight: dict[str, object], work_target: str) -> bool:
    return flight.get("work_target") == work_target and flight.get("status") in {"in-flight", "retrying"}


def has_terminal_completion(flight: dict[str, object]) -> bool:
    return (
        flight.get("status") == "terminal"
        and bool(flight.get("result_envelope_ref"))
        and bool(flight.get("completion_sentinel_ref"))
    )


def resolve_abnormal_codex_exit(flight: dict[str, object], fallback_available: bool) -> str:
    if has_terminal_completion(flight):
        return "complete"
    retry_budget = int(flight.get("retry_budget", 0))
    attempt = int(flight.get("attempt", 0))
    if flight.get("worker_mode") == "codex-cli" and attempt < retry_budget:
        return "retry-same-carrier"
    if fallback_available:
        return "fallback-isolated-token-subagent"
    return "abstain"


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
            "## Result Envelope",
            "## Worker Completion Contract",
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

    def test_sshx_worker_flight_record_contract(self) -> None:
        text = read(SKILL)
        self.assertIn("`SshxWorkerFlightRecord`", text)
        self.assertIn("Every worker dispatch must create a prompt-level `SshxWorkerFlightRecord`", text)
        self.assertIn("worker result record must reference the matching `flight_id` through `worker_flight_ref`", text)
        for field in [
            "`flight_id`",
            "`stage`",
            "`role`",
            "`worker_mode`",
            "`worker_carrier`",
            "`work_target`",
            "`status`",
            "`retry_budget`",
            "`attempt`",
            "`result_envelope_ref`",
            "`completion_sentinel_ref`",
        ]:
            self.assertIn(field, text)
        self.assertIn("`status` is one of `in-flight`, `retrying`, `terminal`, or `abstained`", text)
        self.assertIn("`retry_budget` is a finite integer decided before the first launch", text)

    def test_sshx_in_flight_worker_blocks_caller_mutation(self) -> None:
        text = read(SKILL)
        self.assertIn("While any `SshxWorkerFlightRecord` for the same `work_target` is `in-flight` or `retrying`", text)
        self.assertIn("the caller is read-only for that target", text)
        self.assertIn("must not mutate files, Git state, GitHub state", text)
        self.assertIn("must not take over the same `work_target`", text)

        in_flight = {"work_target": "skills/sshx/SKILL.md", "status": "in-flight"}
        retrying = {"work_target": "skills/sshx/SKILL.md", "status": "retrying"}
        terminal = {"work_target": "skills/sshx/SKILL.md", "status": "terminal"}
        other_target = {"work_target": "README.md", "status": "in-flight"}

        self.assertTrue(flight_blocks_mutation(in_flight, "skills/sshx/SKILL.md"))
        self.assertTrue(flight_blocks_mutation(retrying, "skills/sshx/SKILL.md"))
        self.assertFalse(flight_blocks_mutation(terminal, "skills/sshx/SKILL.md"))
        self.assertFalse(flight_blocks_mutation(other_target, "skills/sshx/SKILL.md"))

    def test_sshx_completion_requires_envelope_and_worker_sentinel(self) -> None:
        text = read(SKILL)
        self.assertIn("recognized only when the caller has both a terminal `SshxResultEnvelope`", text)
        self.assertIn("worker-owned `completion_sentinel_ref` recorded on the matching flight", text)
        for forbidden_evidence in [
            "`pgrep`",
            "process-table snapshots",
            "log marker strings",
            "empty `git status` output",
        ]:
            self.assertIn(forbidden_evidence, text)
        self.assertIn("are never completion evidence", text)

        self.assertTrue(
            has_terminal_completion(
                {
                    "status": "terminal",
                    "result_envelope_ref": "artifacts/sshx/worker-result.json",
                    "completion_sentinel_ref": "artifacts/sshx/worker.done",
                }
            )
        )
        self.assertFalse(
            has_terminal_completion(
                {
                    "status": "terminal",
                    "result_envelope_ref": "artifacts/sshx/worker-result.json",
                    "completion_sentinel_ref": "",
                }
            )
        )
        self.assertFalse(
            has_terminal_completion(
                {
                    "status": "terminal",
                    "result_envelope_ref": "",
                    "completion_sentinel_ref": "artifacts/sshx/worker.done",
                }
            )
        )

    def test_sshx_abnormal_codex_exit_retries_falls_back_or_abstains(self) -> None:
        text = read(SKILL)
        self.assertIn("If `codex-cli` exits abnormally without both the terminal envelope and the completion sentinel", text)
        self.assertIn("consume the finite same-carrier retry budget", text)
        self.assertIn("fall back to `isolated-token-subagent` when available", text)
        self.assertIn("the result is `abstain`", text)
        self.assertIn("must not implement, repair, or otherwise mutate the same `work_target` itself", text)

        self.assertEqual(
            resolve_abnormal_codex_exit(
                {
                    "worker_mode": "codex-cli",
                    "status": "retrying",
                    "retry_budget": 2,
                    "attempt": 1,
                    "result_envelope_ref": "",
                    "completion_sentinel_ref": "",
                },
                fallback_available=False,
            ),
            "retry-same-carrier",
        )
        self.assertEqual(
            resolve_abnormal_codex_exit(
                {
                    "worker_mode": "codex-cli",
                    "status": "retrying",
                    "retry_budget": 2,
                    "attempt": 2,
                    "result_envelope_ref": "",
                    "completion_sentinel_ref": "",
                },
                fallback_available=True,
            ),
            "fallback-isolated-token-subagent",
        )
        self.assertEqual(
            resolve_abnormal_codex_exit(
                {
                    "worker_mode": "codex-cli",
                    "status": "retrying",
                    "retry_budget": 2,
                    "attempt": 2,
                    "result_envelope_ref": "",
                    "completion_sentinel_ref": "",
                },
                fallback_available=False,
            ),
            "abstain",
        )

    def test_sshx_no_context_pollution_contract(self) -> None:
        text = read(SKILL)
        self.assertIn("The caller context must not carry worker full reasoning or same-round peer outputs", text)
        for allowed_context in [
            "intake inputs and constraints",
            "dispatch briefs sent to each worker",
            "`SshxResultEnvelope.conclusion` values, including verdicts and explicitly surfaced blockers",
            "`SshxResultEnvelope.log_ref` artifact references",
            "final reports that aggregate conclusions only",
        ]:
            self.assertIn(allowed_context, text)
        for removed_escape_hatch in [
            "worker summaries, verdicts, and explicitly surfaced blockers",
            "meta-judge synthesis",
            "final summary and report",
        ]:
            self.assertNotIn(removed_escape_hatch, text)
        self.assertIn(
            "Same-round thinking workers must not see one another's outputs before their own verdicts are returned",
            text,
        )
        self.assertIn(
            "If worker isolation is unavailable, exit through `abstain` instead of degrading the protocol into single-context roleplay",
            text,
        )

    def test_sshx_result_envelope_contract(self) -> None:
        text = read(SKILL)
        self.assertIn("## Result Envelope", text)
        self.assertIn("`SshxResultEnvelope` is a prompt-level record, not a runtime API", text)
        self.assertIn(
            "Every caller-carried result from `thinking_triplet_workers`, `meta_judge`, `implementation_worker`, `review_triplet_workers`, and `fix_or_done` must use exactly these top-level fields",
            text,
        )
        self.assertIn("`conclusion`: compact structured result consumed by the caller", text)
        self.assertIn("`log_ref`: artifact reference for the non-inline worker", text)
        for forbidden_inline in [
            "process logs",
            "step-by-step reasoning",
            "raw transcripts",
            "debug output",
            "same-round peer output",
        ]:
            self.assertIn(forbidden_inline, text)
        self.assertIn("Logs are not inline in caller context", text)
        self.assertIn("Final reports aggregate `conclusion` values only", text)
        self.assertIn("produce the final report from conclusions only while preserving `log_ref` references", text)
        self.assertIn("process logs stay behind `log_ref`", text)
        self.assertIn("without inlining logs", text)

    def test_sshx_worker_completion_contract(self) -> None:
        text = read(SKILL)
        self.assertIn("## Worker Completion Contract", text)
        self.assertIn("worker carrier process has exited with status `0`", text)
        self.assertIn("caller-assigned `result_ref` artifact exists", text)
        self.assertIn("parses as a valid `SshxResultEnvelope`", text)
        self.assertIn("`conclusion.verdict`", text)
        self.assertIn("A worker is not done while its carrier process is still running", text)
        for diagnostic_surface in [
            "stdout",
            "stderr",
            "raw transcripts",
            "final text",
            "prompt echoes",
            "`log_ref` content",
            "log tails",
        ]:
            self.assertIn(diagnostic_surface, text)
        self.assertIn("diagnostic only", text)
        self.assertIn("must not participate in done detection or verdict routing", text)
        self.assertIn("placeholder verdicts", text)
        self.assertIn("fail closed", text)

    def test_sshx_worker_completion_ignores_log_only_fake_markers(self) -> None:
        log_only_fake_marker = {
            "stdout": "IMPL_DONE VERDICT:propose",
            "stderr": "VERDICT:approve",
            "raw_transcript": "final answer says done",
            "log_tail": "REVIEW_DONE:approve",
        }
        with self.assertRaisesRegex(ContractFailure, "missing result_ref artifact"):
            completed_worker_verdict(
                process_exited=True,
                exit_code=0,
                result_artifact=None,
                allowed_verdicts=THINKING_VERDICTS,
            )
        self.assertIn("VERDICT:propose", repr(log_only_fake_marker))

    def test_sshx_running_process_is_not_done_even_with_valid_artifact(self) -> None:
        valid_artifact = {
            "conclusion": {"verdict": "propose", "decision": "use artifact authority"},
            "log_ref": "artifacts/sshx/minimal.log",
        }
        with self.assertRaisesRegex(ContractFailure, "still running"):
            completed_worker_verdict(
                process_exited=False,
                exit_code=None,
                result_artifact=valid_artifact,
                allowed_verdicts=THINKING_VERDICTS,
            )

    def test_sshx_exit_zero_and_valid_artifact_is_done(self) -> None:
        valid_artifact = {
            "conclusion": {"verdict": "propose", "decision": "use artifact authority"},
            "log_ref": "artifacts/sshx/minimal.log",
        }
        verdict = completed_worker_verdict(
            process_exited=True,
            exit_code=0,
            result_artifact=valid_artifact,
            allowed_verdicts=THINKING_VERDICTS,
        )
        self.assertEqual(verdict, "propose")

    def test_sshx_verdict_only_comes_from_conclusion_verdict(self) -> None:
        valid_artifact = {
            "conclusion": {"verdict": "reject", "decision": "artifact wins"},
            "log_ref": "artifacts/sshx/worker.log#VERDICT:propose",
        }
        verdict = completed_worker_verdict(
            process_exited=True,
            exit_code=0,
            result_artifact=valid_artifact,
            allowed_verdicts=THINKING_VERDICTS,
        )
        self.assertEqual(verdict, "reject")

    def test_sshx_invalid_result_envelopes_fail_closed(self) -> None:
        invalid_artifacts = [
            ({"log_ref": "artifacts/sshx/worker.log"}, "SshxResultEnvelope"),
            ({"conclusion": {"verdict": "propose"}}, "SshxResultEnvelope"),
            ({"conclusion": {}, "log_ref": "artifacts/sshx/worker.log"}, "invalid"),
            ({"conclusion": {"verdict": "TODO"}, "log_ref": "artifacts/sshx/worker.log"}, "invalid"),
            ({"conclusion": {"verdict": "maybe"}, "log_ref": "artifacts/sshx/worker.log"}, "invalid"),
            ({"conclusion": {"verdict": "propose"}, "log_ref": ""}, "missing log_ref"),
        ]
        for artifact, reason in invalid_artifacts:
            with self.subTest(artifact=artifact):
                with self.assertRaisesRegex(ContractFailure, reason):
                    completed_worker_verdict(
                        process_exited=True,
                        exit_code=0,
                        result_artifact=artifact,
                        allowed_verdicts=THINKING_VERDICTS,
                    )

    def test_sshx_result_envelope_caller_context_excludes_inline_logs(self) -> None:
        caller_carried_transcript = {
            "thinking_triplet_workers": [
                {
                    "role": "minimal",
                    "worker_flight_ref": "flight-thinking-minimal",
                    "verdict": "propose",
                    "conclusion": {
                        "decision": "use the smallest contract edit",
                        "goal_gap": "none",
                    },
                    "log_ref": "artifacts/sshx/minimal.log",
                }
            ],
            "meta_judge": {
                "conclusion": {
                    "exit": "implement",
                    "decision": "all worker conclusions agree",
                },
                "log_ref": "artifacts/sshx/meta-judge.log",
            },
            "implementation_worker": {
                "worker_flight_ref": "flight-implementation",
                "conclusion": {
                    "changed_files": ["skills/sshx/SKILL.md"],
                    "tests": ["python3 -m unittest discover -s skills/sshx/tests -p 'test_*.py'"],
                },
                "log_ref": "artifacts/sshx/implementation.log",
            },
            "review_triplet_workers": [
                {
                    "role": "tests",
                    "worker_flight_ref": "flight-review-tests",
                    "verdict": "approve",
                    "conclusion": {"coverage": "contract locks envelope-only output"},
                    "log_ref": "artifacts/sshx/review-tests.log",
                }
            ],
            "fix_or_done": {
                "conclusion": {"exit": "done with advisory surfaced"},
                "log_ref": "artifacts/sshx/fix-or-done.log",
            },
        }

        def assert_envelope(node: dict[str, object]) -> None:
            self.assertIn("conclusion", node)
            self.assertIn("log_ref", node)
            self.assertNotIn("summary", node)
            self.assertNotIn("report", node)
            self.assertNotIn("synthesis", node)

        for worker in caller_carried_transcript["thinking_triplet_workers"]:
            assert_envelope(worker)
        assert_envelope(caller_carried_transcript["meta_judge"])
        assert_envelope(caller_carried_transcript["implementation_worker"])
        for reviewer in caller_carried_transcript["review_triplet_workers"]:
            assert_envelope(reviewer)
        assert_envelope(caller_carried_transcript["fix_or_done"])

        rendered = repr(caller_carried_transcript)
        for inline_log_phrase in [
            "worker reasoning:",
            "raw transcript:",
            "debug log body:",
            "step-by-step reasoning:",
            "same-round peer output:",
        ]:
            self.assertNotIn(inline_log_phrase, rendered)

    def test_sshx_transcript_worker_flights_shape(self) -> None:
        text = read(SKILL)
        for transcript_line in [
            "worker_flights:",
            "  - flight_id:",
            "    stage:",
            "    role:",
            "    worker_mode:",
            "    worker_carrier:",
            "    work_target:",
            "    status:",
            "    retry_budget:",
            "    attempt:",
            "    result_envelope_ref:",
            "    completion_sentinel_ref:",
            "    worker_flight_ref:",
        ]:
            self.assertIn(transcript_line, text)

        transcript = {
            "worker_flights": [
                {
                    "flight_id": "flight-implementation",
                    "stage": "implementation_worker",
                    "role": "implementation",
                    "worker_mode": "codex-cli",
                    "worker_carrier": "codex-cli",
                    "work_target": "skills/sshx/SKILL.md",
                    "status": "terminal",
                    "retry_budget": 2,
                    "attempt": 1,
                    "result_envelope_ref": "artifacts/sshx/implementation-result.json",
                    "completion_sentinel_ref": "artifacts/sshx/implementation.done",
                }
            ],
            "implementation_worker": {
                "worker_flight_ref": "flight-implementation",
                "conclusion": {"changed_files": ["skills/sshx/SKILL.md"]},
                "log_ref": "artifacts/sshx/implementation.log",
            },
        }
        self.assertEqual(
            set(transcript["worker_flights"][0]),
            {
                "flight_id",
                "stage",
                "role",
                "worker_mode",
                "worker_carrier",
                "work_target",
                "status",
                "retry_budget",
                "attempt",
                "result_envelope_ref",
                "completion_sentinel_ref",
            },
        )
        self.assertEqual(
            transcript["implementation_worker"]["worker_flight_ref"],
            transcript["worker_flights"][0]["flight_id"],
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
