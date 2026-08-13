import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / "skills" / "sshx" / "scripts" / "run-codex-worker.sh"
SKILL = ROOT / "skills" / "sshx" / "SKILL.md"

FAKE_CODEX = r'''#!/bin/bash
set -u
last_message=
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) last_message=$2; shift 2 ;;
    -C|--sandbox) shift 2 ;;
    exec|--json|--skip-git-repo-check|-) shift ;;
    *) exit 97 ;;
  esac
done
brief=$(command cat)
run_dir=$(dirname "$last_message")
result_ref="$run_dir/result.json"
sentinel_ref="$run_dir/completion.sentinel"
verdict=${FAKE_VERDICT:-propose}
write_result() { printf '{"conclusion":{"verdict":"%s"},"log_ref":"fake-log"}\n' "$verdict" > "$result_ref.tmp"; mv "$result_ref.tmp" "$result_ref"; }
write_sentinel() { printf '%s\n' complete > "$sentinel_ref.tmp"; mv "$sentinel_ref.tmp" "$sentinel_ref"; }
printf '%s\n' 'fake last message' > "$last_message"
case "${FAKE_MODE:-success}" in
  success) write_result; write_sentinel ;;
  nonzero_with_artifacts) write_result; write_sentinel; exit 9 ;;
  nothing) ;;
  invalid_json) printf '%s' '{"conclusion":' > "$result_ref"; write_sentinel ;;
  extra_key) printf '%s\n' '{"conclusion":{"verdict":"propose"},"log_ref":"fake-log","notes":true}' > "$result_ref"; write_sentinel ;;
  missing_key) printf '%s\n' '{"conclusion":{"verdict":"propose"}}' > "$result_ref"; write_sentinel ;;
  empty_log_ref) printf '%s\n' '{"conclusion":{"verdict":"propose"},"log_ref":""}' > "$result_ref"; write_sentinel ;;
  bad_verdict) verdict=unexpected; write_result; write_sentinel ;;
  missing_sentinel) write_result ;;
  stdout_only) printf '%s\n' '{"conclusion":{"verdict":"propose"},"log_ref":"fake-log"}'; printf '%s\n' 'completion marker' ;;
  log_marker) write_result; printf '%s\n' 'completion.sentinel exists' >&2 ;;
  diagnostic_result) printf '%s\n' '{"conclusion":{"verdict":"propose"},"log_ref":"fake-log"}' > "$last_message" ;;
  carrier_exit_write_failure) write_result; write_sentinel; mkdir "$run_dir/carrier.exit.tmp" ;;
  artifacts_then_wait) write_result; write_sentinel; printf '%s\n' ready > "$FAKE_READY"; sleep 0.4; printf '%s\n' exited > "$FAKE_EXITED" ;;
  invalid_verdict_missing_sentinel) verdict=unexpected; write_result ;;
  exit_127) exit 127 ;;
  projection_collision)
    write_result; write_sentinel
    collision_target="$run_dir/$FAKE_COLLISION_TARGET"
    case "$FAKE_COLLISION_SHAPE" in
      directory) mkdir "$collision_target" ;;
      fifo) mkfifo "$collision_target" ;;
      regular) printf '%s\n' stale > "$collision_target" ;;
      symlink) ln -s "$FAKE_OUTSIDE" "$collision_target" ;;
      *) exit 95 ;;
    esac
    ;;
  symlink_result) write_result; rm -f "$result_ref"; printf '%s\n' '{}' > "$run_dir/real-result.json"; ln -s "$run_dir/real-result.json" "$result_ref"; ;;
  symlink_sentinel) write_result; printf '%s\n' complete > "$run_dir/real-sentinel"; ln -s "$run_dir/real-sentinel" "$sentinel_ref" ;;
  *) exit 98 ;;
esac
case "$brief" in *"Result envelope: $result_ref"*"Completion sentinel: $sentinel_ref"*) ;; *) exit 96 ;; esac
'''


class RunResult:
    def __init__(self, process: subprocess.CompletedProcess[str], run_dir: Path) -> None:
        self.process = process
        self.run_dir = run_dir
        status_path = run_dir / "status.json"
        self.status = json.loads(status_path.read_text()) if status_path.is_file() and not status_path.is_symlink() else None


class CodexWorkerRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_context = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.temp_context.name)
        self.bin_dir = self.temp_dir / "bin"
        self.bin_dir.mkdir()
        self.fake_codex = self.bin_dir / "codex"
        self.fake_codex.write_text(FAKE_CODEX)
        self.fake_codex.chmod(0o755)
        jq = subprocess.run(["/bin/sh", "-c", "command -v jq"], check=True, capture_output=True, text=True).stdout.strip()
        (self.bin_dir / "jq").symlink_to(jq)
        self.counter = 0

    def tearDown(self) -> None:
        self.temp_context.cleanup()

    def next_flight(self, prefix: str = "flight") -> str:
        self.counter += 1
        return f"{prefix}-{self.counter}"

    def command(self, flight_id: str, *, attempt: str = "1", stage: str = "thinking", work_target: str | None = None, sandbox: str = "workspace-write") -> list[str]:
        return ["/bin/bash", str(RUNNER), "--flight-id", flight_id, "--attempt", attempt, "--stage", stage, "--work-target", work_target or str(ROOT), "--sandbox", sandbox]

    def environment(self, mode: str = "success", **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update({"PATH": f"{self.bin_dir}:/bin:/usr/bin", "TMPDIR": str(self.temp_dir), "FAKE_MODE": mode})
        env.update(extra)
        return env

    def expected_run_dir(self, flight_id: str, attempt: str = "1", base: Path | None = None) -> Path:
        return (base or self.temp_dir) / "consensus-rnd" / "sshx" / flight_id / f"attempt-{attempt}"

    def run_worker(self, mode: str = "success", *, flight_id: str | None = None, attempt: str = "1", stage: str = "thinking", extra_env: dict[str, str] | None = None, env: dict[str, str] | None = None) -> RunResult:
        selected = flight_id or self.next_flight()
        process = subprocess.run(self.command(selected, attempt=attempt, stage=stage), input="Perform the assigned worker task.\n", capture_output=True, text=True, env=env or self.environment(mode, **(extra_env or {})), timeout=10)
        return RunResult(process, self.expected_run_dir(selected, attempt))

    def assert_terminal(self, result: RunResult, reason: str, expected_code: int | None = None) -> None:
        expected_code = 0 if reason == "COMPLETE" else 1 if expected_code is None else expected_code
        self.assertEqual(result.process.returncode, expected_code, result.process.stderr)
        self.assertIsNotNone(result.status)
        assert result.status is not None
        self.assertEqual(result.status["reason_code"], reason)
        self.assertEqual(result.status["status"], "COMPLETE" if reason == "COMPLETE" else "NOT_COMPLETE")
        self.assertEqual(result.process.stdout, result.run_dir.joinpath("status.json").read_text())

    def test_success_has_complete_status_and_fixed_artifacts(self) -> None:
        result = self.run_worker()
        self.assert_terminal(result, "COMPLETE")
        for name in ["brief.md", "worker.stdout.log", "worker.stderr.log", "last-message.txt", "result.json", "completion.sentinel", "carrier.exit", "status.json"]:
            self.assertTrue((result.run_dir / name).is_file(), name)
        self.assertEqual(result.status["verdict"], "propose")
        self.assertEqual(result.status["carrier_exit"], 0)

    def test_nonzero_carrier_wins_even_with_complete_artifacts(self) -> None:
        result = self.run_worker("nonzero_with_artifacts")
        self.assert_terminal(result, "CARRIER_EXIT_NONZERO")
        self.assertEqual(result.status["carrier_exit"], 9)

    def test_started_carrier_exit_127_is_nonzero_not_launch_failure(self) -> None:
        result = self.run_worker("exit_127")
        self.assert_terminal(result, "CARRIER_EXIT_NONZERO")
        self.assertEqual(result.status["carrier_exit"], 127)

    def test_runner_waits_until_carrier_exits_after_artifacts_appear(self) -> None:
        flight = self.next_flight("foreground-wait")
        ready = self.temp_dir / "carrier-ready"
        exited = self.temp_dir / "carrier-exited"
        os.mkfifo(ready)
        process = subprocess.Popen(
            self.command(flight),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.environment("artifacts_then_wait", FAKE_READY=str(ready), FAKE_EXITED=str(exited)),
        )
        with ready.open() as ready_signal:
            self.assertEqual(ready_signal.read().strip(), "ready")
        self.assertIsNone(process.poll(), "runner returned before the live carrier exited")
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(exited.read_text().strip(), "exited")
        self.assert_terminal(
            RunResult(subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr), self.expected_run_dir(flight)),
            "COMPLETE",
        )

    def test_missing_result_is_not_complete(self) -> None:
        self.assert_terminal(self.run_worker("nothing"), "RESULT_MISSING")

    def test_invalid_or_truncated_json_is_rejected(self) -> None:
        self.assert_terminal(self.run_worker("invalid_json"), "ENVELOPE_INVALID")

    def test_envelope_shape_and_log_ref_are_strict(self) -> None:
        for mode in ["extra_key", "missing_key", "empty_log_ref"]:
            with self.subTest(mode=mode):
                self.assert_terminal(self.run_worker(mode), "ENVELOPE_INVALID")

    def test_stage_verdict_requires_exact_set_member(self) -> None:
        for stage, verdict in [("thinking", "unexpected"), ("thinking", "propose|revise"), ("review", "approve|comment")]:
            with self.subTest(stage=stage, verdict=verdict):
                self.assert_terminal(self.run_worker(stage=stage, extra_env={"FAKE_VERDICT": verdict}), "VERDICT_INVALID")

    def test_verdict_validation_precedes_missing_sentinel(self) -> None:
        self.assert_terminal(self.run_worker("invalid_verdict_missing_sentinel"), "VERDICT_INVALID")

    def test_missing_sentinel_is_not_complete(self) -> None:
        self.assert_terminal(self.run_worker("missing_sentinel"), "SENTINEL_MISSING")

    def test_fail_closed_default_is_used_on_runner_write_failure(self) -> None:
        self.assert_terminal(self.run_worker("carrier_exit_write_failure"), "INTERNAL_ERROR")

    def test_status_projection_rejects_non_regular_targets(self) -> None:
        for target in ["status.json", "status.json.tmp"]:
            for shape in ["directory", "fifo", "symlink_directory", "symlink_file", "symlink_dangling"]:
                with self.subTest(target=target, shape=shape):
                    outside = self.temp_dir / self.next_flight("outside-status")
                    if shape == "symlink_file":
                        outside.write_text("unchanged\n")
                    elif shape == "symlink_directory":
                        outside.mkdir()
                    result = self.run_worker(
                        "projection_collision",
                        extra_env={"FAKE_COLLISION_TARGET": target, "FAKE_COLLISION_SHAPE": "symlink" if shape.startswith("symlink") else shape, "FAKE_OUTSIDE": str(outside)},
                    )
                    self.assertEqual(result.process.returncode, 1, result.process.stderr)
                    self.assertIn("INTERNAL_ERROR", result.process.stderr)
                    self.assertIsNone(result.status)
                    if shape == "symlink_directory":
                        self.assertEqual(list(outside.iterdir()), [])
                    elif shape == "symlink_file":
                        self.assertEqual(outside.read_text(), "unchanged\n")
                    else:
                        self.assertFalse(outside.exists())

    def test_carrier_exit_projection_rejects_non_regular_targets(self) -> None:
        for target in ["carrier.exit", "carrier.exit.tmp"]:
            for shape in ["directory", "fifo", "symlink_directory", "symlink_file", "symlink_dangling"]:
                with self.subTest(target=target, shape=shape):
                    outside = self.temp_dir / self.next_flight("outside-carrier")
                    if shape == "symlink_file":
                        outside.write_text("unchanged\n")
                    elif shape == "symlink_directory":
                        outside.mkdir()
                    result = self.run_worker(
                        "projection_collision",
                        extra_env={"FAKE_COLLISION_TARGET": target, "FAKE_COLLISION_SHAPE": "symlink" if shape.startswith("symlink") else shape, "FAKE_OUTSIDE": str(outside)},
                    )
                    self.assert_terminal(result, "INTERNAL_ERROR")
                    if shape == "symlink_directory":
                        self.assertEqual(list(outside.iterdir()), [])
                    elif shape == "symlink_file":
                        self.assertEqual(outside.read_text(), "unchanged\n")
                    else:
                        self.assertFalse(outside.exists())

    def test_runner_owned_projection_replaces_regular_targets(self) -> None:
        for target in ["carrier.exit", "carrier.exit.tmp", "status.json", "status.json.tmp"]:
            with self.subTest(target=target):
                result = self.run_worker(
                    "projection_collision",
                    extra_env={"FAKE_COLLISION_TARGET": target, "FAKE_COLLISION_SHAPE": "regular", "FAKE_OUTSIDE": str(self.temp_dir / "unused")},
                )
                self.assert_terminal(result, "COMPLETE")

    def test_diagnostic_last_message_is_not_completion_evidence(self) -> None:
        self.assert_terminal(self.run_worker("diagnostic_result"), "RESULT_MISSING")

    def test_diagnostic_surfaces_are_not_completion_evidence(self) -> None:
        stdout_only = self.run_worker("stdout_only")
        self.assert_terminal(stdout_only, "RESULT_MISSING")
        log_marker = self.run_worker("log_marker")
        self.assert_terminal(log_marker, "SENTINEL_MISSING")

    def test_default_tmpdir_is_usable_without_environment_override(self) -> None:
        flight = self.next_flight("default-tmp")
        env = self.environment(); env.pop("TMPDIR")
        run_dir = self.expected_run_dir(flight, base=Path("/tmp"))
        try:
            process = subprocess.run(self.command(flight), input="brief\n", capture_output=True, text=True, env=env, timeout=10)
            self.assert_terminal(RunResult(process, self.expected_run_dir(flight, base=Path("/tmp"))), "COMPLETE")
        finally:
            shutil.rmtree(run_dir.parent, ignore_errors=True)

    def test_explicit_symlink_tmpdir_is_rejected(self) -> None:
        target = self.temp_dir / "target"; target.mkdir()
        link = self.temp_dir / "tmp-link"; link.symlink_to(target, target_is_directory=True)
        env = self.environment(); env["TMPDIR"] = str(link)
        process = subprocess.run(self.command(self.next_flight("tmp-link")), input="brief\n", capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(process.returncode, 1)
        self.assertIn("RUN_DIR_UNAVAILABLE", process.stderr)

    def test_tmpdir_validation_is_fail_closed(self) -> None:
        unwritable = self.temp_dir / "unwritable"; unwritable.mkdir(); unwritable.chmod(0o500)
        values = ["relative", str(self.temp_dir / "missing"), str(unwritable)]
        for value in values:
            env = self.environment(); env["TMPDIR"] = value
            process = subprocess.run(self.command(self.next_flight("bad-tmp")), input="brief\n", capture_output=True, text=True, env=env, timeout=10)
            self.assertEqual(process.returncode, 1); self.assertIn("RUN_DIR_UNAVAILABLE", process.stderr)
        unwritable.chmod(0o700)

    def test_existing_attempt_directory_is_collision_without_reuse(self) -> None:
        flight = self.next_flight("collision")
        run_dir = self.expected_run_dir(flight); run_dir.mkdir(parents=True)
        marker = run_dir / "owned-by-earlier-attempt"; marker.write_text("unchanged\n")
        process = self.run_worker(flight_id=flight)
        self.assertEqual(process.process.returncode, 1)
        self.assertIn("RUN_DIR_COLLISION", process.process.stderr)
        self.assertEqual(marker.read_text(), "unchanged\n")

    def test_run_hierarchy_rejects_symlinks_before_launch(self) -> None:
        for component in ["consensus-rnd", "sshx", "flight"]:
            with self.subTest(component=component):
                child = Path(tempfile.mkdtemp()); outside = child / "outside"; outside.mkdir()
                try:
                    if component == "consensus-rnd": (child / "consensus-rnd").symlink_to(outside, target_is_directory=True)
                    else:
                        (child / "consensus-rnd").mkdir()
                        if component == "sshx": (child / "consensus-rnd" / "sshx").symlink_to(outside, target_is_directory=True)
                        else:
                            (child / "consensus-rnd" / "sshx").mkdir(); (child / "consensus-rnd" / "sshx" / "symlink-flight").symlink_to(outside, target_is_directory=True)
                    env = self.environment(); env["TMPDIR"] = str(child)
                    process = subprocess.run(self.command("symlink-flight"), input="brief\n", capture_output=True, text=True, env=env, timeout=10)
                    self.assertEqual(process.returncode, 1, process.stderr)
                    self.assertIn("RUN_DIR_UNAVAILABLE", process.stderr)
                    self.assertEqual(list(outside.iterdir()), [])
                finally: shutil.rmtree(child, ignore_errors=True)

    def test_concurrent_flight_paths_are_disjoint(self) -> None:
        a, b = self.next_flight("parallel-a"), self.next_flight("parallel-b")
        p1 = subprocess.Popen(self.command(a), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.environment())
        p2 = subprocess.Popen(self.command(b), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.environment())
        out1, err1 = p1.communicate("brief a\n", timeout=10); out2, err2 = p2.communicate("brief b\n", timeout=10)
        self.assertEqual(p1.returncode, 0, err1); self.assertEqual(p2.returncode, 0, err2)
        self.assertNotEqual(json.loads(out1)["run_dir"], json.loads(out2)["run_dir"])

    def test_symlinks_cannot_impersonate_worker_artifacts(self) -> None:
        self.assert_terminal(self.run_worker("symlink_result"), "RESULT_MISSING")
        self.assert_terminal(self.run_worker("symlink_sentinel"), "SENTINEL_MISSING")

    def test_missing_jq_fails_without_parser_fallback(self) -> None:
        flight = self.next_flight("no-parser"); empty = self.temp_dir / "empty-path"; empty.mkdir()
        env = self.environment(); env["PATH"] = str(empty)
        process = subprocess.run(self.command(flight), input="brief\n", capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(process.returncode, 1); self.assertIn("PARSER_UNAVAILABLE", process.stderr); self.assertFalse(self.expected_run_dir(flight).exists())

    def test_codex_preflight_reports_launch_failed(self) -> None:
        flight = self.next_flight("launch")
        no_codex = self.temp_dir / "no-codex"; no_codex.mkdir(); (no_codex / "jq").symlink_to((self.bin_dir / "jq").resolve())
        env = self.environment(); env["PATH"] = f"{no_codex}:/bin:/usr/bin"
        process = subprocess.run(self.command(flight), input="brief\n", capture_output=True, text=True, env=env, timeout=10)
        result = RunResult(process, self.expected_run_dir(flight))
        self.assert_terminal(result, "LAUNCH_FAILED")

    def test_argument_validation_rejects_missing_duplicate_unknown_and_invalid_values(self) -> None:
        valid = self.command("valid")
        cases = [valid[:-2], valid + ["--stage", "thinking"], valid + ["--unknown", "value"], self.command("../escape"), self.command("bad/id"), self.command("valid", attempt="0"), self.command("valid", attempt="one"), self.command("valid", stage="other"), self.command("valid", sandbox="danger-full-access"), self.command("valid", work_target="relative")]
        for command in cases:
            process = subprocess.run(command, input="brief\n", capture_output=True, text=True, env=self.environment(), timeout=10)
            self.assertEqual(process.returncode, 64, process.stderr); self.assertIn("USAGE_ERROR", process.stderr)

    def test_flight_id_dot_is_rejected(self) -> None:
        process = subprocess.run(self.command("."), input="brief\n", capture_output=True, text=True, env=self.environment(), timeout=10)
        self.assertEqual(process.returncode, 64); self.assertIn("USAGE_ERROR", process.stderr)

    def test_stdout_publication_failure_is_internal_error(self) -> None:
        flight = self.next_flight("stdout-failure")
        with subprocess.Popen(self.command(flight), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.environment()) as process:
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write("brief\n"); process.stdin.close(); process.stdout.close(); process.wait(timeout=10)
            self.assertEqual(process.returncode, 1)
        status = json.loads(self.expected_run_dir(flight).joinpath("status.json").read_text())
        self.assertEqual(status["reason_code"], "INTERNAL_ERROR")

    def test_runner_verdict_sets_match_skill_contract(self) -> None:
        text = SKILL.read_text()
        thinking = text.split("## Thinking Panel", 1)[1].split("## Design Truth Table", 1)[0].split("Each seat returns one of:", 1)[1]
        review = text.split("## Review Triplet", 1)[1].split("## Review Truth Table", 1)[0].split("Each reviewer returns one of:", 1)[1]
        expected = {"thinking": set(re.findall(r"^- `([^`]+)`$", thinking, re.MULTILINE)), "review": set(re.findall(r"^- `([^`]+)`$", review, re.MULTILINE))}
        declaration = re.search(r"stage_verdict_specs='([^']*)'", RUNNER.read_text())
        assert declaration is not None
        runner_sets = {}
        for entry in declaration.group(1).split(";"):
            name, values = entry.split("=", 1); runner_sets[name] = set(values.split("|")) if values else set()
        self.assertEqual(runner_sets["thinking"], expected["thinking"]); self.assertEqual(runner_sets["review"], expected["review"]); self.assertEqual(runner_sets["implementation"], set())
        for stage, verdicts in expected.items():
            for verdict in verdicts: self.assert_terminal(self.run_worker(stage=stage, extra_env={"FAKE_VERDICT": verdict}), "COMPLETE")
        self.assert_terminal(self.run_worker(stage="implementation", extra_env={"FAKE_VERDICT": "other"}), "COMPLETE")

    def test_runner_never_produces_worker_owned_artifacts(self) -> None:
        result = self.run_worker("nothing")
        self.assert_terminal(result, "RESULT_MISSING")
        self.assertFalse((result.run_dir / "result.json").exists()); self.assertFalse((result.run_dir / "completion.sentinel").exists())


if __name__ == "__main__":
    unittest.main()
