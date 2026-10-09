"""Automatic identity no-skill baseline (2026-10-04, before runner edits):
Invoking the existing runner with only --stage implementation and --work-target
in a temporary SSHX_HOME returned 64, empty stdout, and USAGE_ERROR: missing
--flight-id; no run root was created. The fresh-launch behavior tests below
replace that observed prerequisite with runner allocation at launch.

ARCH-1 no-skill baseline (2026-10-04, before this repair's edits): direct runner
invocation with the existing fake codex fixture and SSHX_HOME=relative returned
1 with empty stdout and RUN_DIR_UNAVAILABLE. Correcting the root but supplying
--attempt 2 without an ID returned 64 with empty stdout. The existing formal
runnerAttempt nevertheless projected protocol retries + 1 without a bound-ID
premise. The composed test below exercises the pending-to-bound recovery; Lean
checks that both retry paths consume the same finite allowance without reset.
"""

import json
import os
import re
import select
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
VALID_ID = "0123456789abcdef01234567"
RUNNER = ROOT / "skills" / "sshx" / "scripts" / "run-codex-worker.sh"
SKILL = ROOT / "skills" / "sshx" / "SKILL.md"
SPEC = ROOT / "skills" / "sshx" / "CODEX_WORKER_SPEC.md"
TIMESTAMPED_LINE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z  \S")

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
  sleep_success) sleep "${FAKE_SLEEP_SECONDS:-1}"; write_result; write_sentinel ;;
  nonzero_with_artifacts) write_result; write_sentinel; exit 9 ;;
  nothing) ;;
  invalid_json) printf '%s' '{"conclusion":' > "$result_ref"; write_sentinel ;;
  extra_key) printf '%s\n' '{"conclusion":{"verdict":"propose"},"log_ref":"fake-log","notes":true}' > "$result_ref"; write_sentinel ;;
  missing_key) printf '%s\n' '{"conclusion":{"verdict":"propose"}}' > "$result_ref"; write_sentinel ;;
  empty_log_ref) printf '%s\n' '{"conclusion":{"verdict":"propose"},"log_ref":""}' > "$result_ref"; write_sentinel ;;
  conclusion_string) printf '%s\n' '{"conclusion":"propose","log_ref":"fake-log"}' > "$result_ref"; write_sentinel ;;
  log_ref_number) printf '%s\n' '{"conclusion":{"verdict":"propose"},"log_ref":1}' > "$result_ref"; write_sentinel ;;
  verdict_number) printf '%s\n' '{"conclusion":{"verdict":1},"log_ref":"fake-log"}' > "$result_ref"; write_sentinel ;;
  minimum_envelope)
    envelope=$(printf '%s\n' "$brief" | sed -n 's/^Minimum structurally accepted envelope shape: //p')
    [ -n "$envelope" ] || exit 94
    printf '%s\n' "$envelope" > "$result_ref.tmp"
    mv "$result_ref.tmp" "$result_ref"
    write_sentinel
    ;;
  bad_verdict) verdict=unexpected; write_result; write_sentinel ;;
  missing_sentinel) write_result ;;
  stdout_only) printf '%s\n' '{"conclusion":{"verdict":"propose"},"log_ref":"fake-log"}'; printf '%s\n' 'completion marker' ;;
  log_marker) write_result; printf '%s\n' 'completion.sentinel exists' >&2 ;;
  diagnostic_result) printf '%s\n' '{"conclusion":{"verdict":"propose"},"log_ref":"fake-log"}' > "$last_message" ;;
  carrier_exit_write_failure) write_result; write_sentinel; mkdir "$run_dir/carrier.exit.tmp" ;;
  artifacts_then_wait) write_result; write_sentinel; printf '%s\n' ready > "$FAKE_READY"; command cat "$FAKE_RELEASE" >/dev/null; printf '%s\n' exited > "$FAKE_EXITED" ;;
  interrupt_wait) printf '%s\n' "$$" > "$FAKE_CARRIER_PID"; printf '%s\n' ready > "$FAKE_READY"; command cat "$FAKE_RELEASE" >/dev/null ;;
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
        self.real_jq = Path(subprocess.run(["/bin/sh", "-c", "command -v jq"], check=True, capture_output=True, text=True).stdout.strip())
        (self.bin_dir / "jq").symlink_to(self.real_jq)
        self.counter = 0

    def tearDown(self) -> None:
        self.temp_context.cleanup()

    def next_flight(self) -> str:
        self.counter += 1
        return f"{int(time.time()):08x}{self.counter:016x}"

    def command(self, flight_id: str, *, attempt: str = "1", stage: str = "thinking", work_target: str | None = None, sandbox: str = "workspace-write") -> list[str]:
        return self.command_for_runner(RUNNER, flight_id, attempt=attempt, stage=stage, work_target=work_target, sandbox=sandbox)

    def command_for_runner(self, runner: Path, flight_id: str, *, attempt: str = "1", stage: str = "thinking", work_target: str | None = None, sandbox: str = "workspace-write") -> list[str]:
        return ["/bin/bash", str(runner), "--flight-id", flight_id, "--attempt", attempt, "--stage", stage, "--work-target", work_target or str(ROOT), "--sandbox", sandbox]

    def environment(self, mode: str = "success", **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update({"PATH": f"{self.bin_dir}:/bin:/usr/bin", "SSHX_HOME": str(self.temp_dir / "sshx"), "FAKE_MODE": mode})
        env.update(extra)
        return env

    def expected_run_dir(self, flight_id: str, attempt: str = "1", base: Path | None = None) -> Path:
        return (base or self.temp_dir / "sshx") / flight_id / f"attempt-{attempt}"

    def run_worker(self, mode: str = "success", *, flight_id: str | None = None, attempt: str = "1", stage: str = "thinking", extra_env: dict[str, str] | None = None, env: dict[str, str] | None = None) -> RunResult:
        selected = flight_id or self.next_flight()
        process = subprocess.run(self.command(selected, attempt=attempt, stage=stage), input="Perform the assigned worker task.\n", capture_output=True, text=True, env=env or self.environment(mode, **(extra_env or {})), timeout=10)
        return RunResult(process, self.expected_run_dir(selected, attempt))

    def install_jq_wrapper(self) -> None:
        wrapper = self.bin_dir / "jq"
        wrapper.unlink()
        wrapper.write_text(
            "#!/bin/bash\n"
            "set -u\n"
            "case \" $* \" in *\" -n \"*)\n"
            "  count=0\n"
            "  [ ! -f \"$FAKE_JQ_STATE\" ] || count=$(command cat \"$FAKE_JQ_STATE\")\n"
            "  count=$((count + 1))\n"
            "  printf '%s\\n' \"$count\" > \"$FAKE_JQ_STATE\"\n"
            "  case \"$FAKE_JQ_RENDER_MODE\" in\n"
            "    primary_fail) [ \"$count\" -ne 1 ] || exit 71 ;;\n"
            "    primary_empty) [ \"$count\" -ne 1 ] || exit 0 ;;\n"
            "    double_fail) exit 72 ;;\n"
            "    double_empty) exit 0 ;;\n"
            "    finish_wait) printf '%s\\n' ready > \"$FAKE_READY\"; command cat \"$FAKE_RELEASE\" >/dev/null ;;\n"
            "  esac\n"
            "esac\n"
            "exec \"$REAL_JQ\" \"$@\"\n"
        )
        wrapper.chmod(0o755)

    def install_mv_observer(self) -> None:
        wrapper = self.bin_dir / "mv"
        wrapper.write_text(
            "#!/bin/bash\n"
            "for arg in \"$@\"; do\n"
            "  case \"$arg\" in *status.json.tmp) [ -s \"$arg\" ] || printf '%s\\n' empty-status-move > \"$FAKE_MV_MARKER\" ;; esac\n"
            "done\n"
            "exec /bin/mv \"$@\"\n"
        )
        wrapper.chmod(0o755)

    def install_skeleton_render_failing_jq(self) -> None:
        wrapper = self.bin_dir / "jq"
        wrapper.unlink()
        wrapper.write_text(
            "#!/bin/bash\n"
            "for arg in \"$@\"; do\n"
            "  case \"$arg\" in '{conclusion:'*) exit 73 ;; esac\n"
            "done\n"
            "exec \"$REAL_JQ\" \"$@\"\n"
        )
        wrapper.chmod(0o755)

    def assert_terminal(self, result: RunResult, reason: str, expected_code: int | None = None) -> None:
        expected_code = 0 if reason == "COMPLETE" else 1 if expected_code is None else expected_code
        self.assertEqual(result.process.returncode, expected_code, result.process.stderr)
        self.assertIsNotNone(result.status)
        assert result.status is not None
        self.assertEqual(result.status["reason_code"], reason)
        self.assertEqual(result.status["status"], "COMPLETE" if reason == "COMPLETE" else "NOT_COMPLETE")
        self.assertNotEqual(result.process.stdout, result.run_dir.joinpath("status.json").read_text())
        receipt = json.loads(result.process.stdout)
        self.assertEqual(receipt["flight_id"], result.status["flight_id"])
        self.assertEqual(receipt["attempt"], result.status["attempt"])
        self.assertEqual(receipt["run_dir"], str(result.run_dir))
        self.assertEqual(result.process.stdout.count("\n"), 1)

    def assert_timestamped_progress(self, progress: str) -> None:
        lines = progress.splitlines()
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertRegex(line, TIMESTAMPED_LINE)

    def test_success_has_complete_status_and_fixed_artifacts(self) -> None:
        result = self.run_worker()
        self.assert_terminal(result, "COMPLETE")
        for name in ["brief.md", "worker.stdout.log", "worker.stderr.log", "last-message.txt", "result.json", "completion.sentinel", "carrier.exit", "status.json"]:
            self.assertTrue((result.run_dir / name).is_file(), name)
        self.assertEqual(result.status["verdict"], "propose")
        self.assertEqual(result.status["carrier_exit"], 0)

    def test_terminal_status_has_context_and_timing_fields(self) -> None:
        result = self.run_worker()
        self.assert_terminal(result, "COMPLETE")
        assert result.status is not None
        self.assertEqual(result.status["status"], "COMPLETE")
        self.assertRegex(result.status["started_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertRegex(result.status["finished_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertIsInstance(result.status["duration_seconds"], int)
        self.assertEqual(result.status["work_target"], str(ROOT))
        self.assertEqual(result.status["sandbox"], "workspace-write")
        self.assertEqual(result.status["brief_ref"], str(result.run_dir / "brief.md"))
        self.assertNotIn("RUNNING", result.run_dir.joinpath("status.json").read_text())

    def test_sandbox_defaults_to_danger_full_access_and_accepts_explicit_values(self) -> None:
        flight = self.next_flight()
        default_command = self.command(flight)[:-2]
        process = subprocess.run(default_command, input="brief\n", capture_output=True, text=True, env=self.environment(), timeout=10)
        result = RunResult(process, self.expected_run_dir(flight))
        self.assert_terminal(result, "COMPLETE")
        assert result.status is not None
        self.assertEqual(result.status["sandbox"], "danger-full-access")
        explicit = self.next_flight()
        process = subprocess.run(self.command(explicit, sandbox="danger-full-access"), input="brief\n", capture_output=True, text=True, env=self.environment(), timeout=10)
        result = RunResult(process, self.expected_run_dir(explicit))
        self.assert_terminal(result, "COMPLETE")
        assert result.status is not None
        self.assertEqual(result.status["sandbox"], "danger-full-access")

    def test_status_contract_source_regression(self) -> None:
        spec = re.sub(r"\s+", " ", SPEC.read_text())
        self.assertNotIn('status: "RUNNING"', spec)
        self.assertNotIn("`RUNNING` never means success or completion", spec)
        self.assertIn("Exit code is the sole authority", spec)
        self.assertIn("terminal, machine-readable projection", spec)
        self.assertIn("human-readable streaming log", spec)
        self.assertIn("not guaranteed to be parseable", spec)
        self.assertIn("stdout contains only the structured launch receipt", spec)
        for field in ["started_at", "finished_at", "duration_seconds", "work_target", "sandbox", "brief_ref"]:
            self.assertIn(f"`{field}`", spec)

    def test_trap_contract_source_regression(self) -> None:
        trap_lines = [line.strip() for line in RUNNER.read_text().splitlines() if line.strip().startswith("trap")]
        self.assertIn("trap finish EXIT", trap_lines)
        self.assertIn("trap interrupt INT TERM", trap_lines)
        self.assertIn("trap - EXIT", trap_lines)
        self.assertIn("trap '' INT TERM", trap_lines)
        self.assertNotIn("trap - EXIT INT TERM", trap_lines)

    def test_terminal_status_publish_failure_installs_no_projection(self) -> None:
        result = self.run_worker("projection_collision", extra_env={"FAKE_COLLISION_TARGET": "status.json.tmp", "FAKE_COLLISION_SHAPE": "directory", "FAKE_OUTSIDE": str(self.temp_dir / "unused")})
        self.assertEqual(result.process.returncode, 1, result.process.stderr)
        self.assertIsNone(result.status)
        self.assertIn("INTERNAL_ERROR", result.process.stderr)

    def test_primary_status_render_failure_publishes_internal_error_fallback(self) -> None:
        for mode in ["primary_fail", "primary_empty"]:
            with self.subTest(mode=mode):
                self.install_jq_wrapper()
                state = self.temp_dir / f"jq-{mode}-state"
                result = self.run_worker(
                    extra_env={"REAL_JQ": str(self.real_jq), "FAKE_JQ_STATE": str(state), "FAKE_JQ_RENDER_MODE": mode},
                )
                self.assert_terminal(result, "INTERNAL_ERROR")
                self.assertGreater(result.run_dir.joinpath("status.json").stat().st_size, 0)

    def test_double_status_render_failure_never_installs_empty_status(self) -> None:
        for mode in ["double_fail", "double_empty"]:
            with self.subTest(mode=mode):
                self.install_jq_wrapper()
                self.install_mv_observer()
                state = self.temp_dir / f"jq-{mode}-state"
                mv_marker = self.temp_dir / f"mv-{mode}-marker"
                result = self.run_worker(
                    extra_env={"REAL_JQ": str(self.real_jq), "FAKE_JQ_STATE": str(state), "FAKE_JQ_RENDER_MODE": mode, "FAKE_MV_MARKER": str(mv_marker)},
                )
                self.assertEqual(result.process.returncode, 1, result.process.stderr)
                self.assertIsNone(result.status)
                self.assertFalse(result.run_dir.joinpath("status.json").exists())
                self.assertFalse(mv_marker.exists(), "empty status temporary file was passed to mv")
                self.assertIn("cannot render failure status", result.process.stderr)

    def test_signal_during_finish_cannot_interrupt_terminal_publication(self) -> None:
        self.install_jq_wrapper()
        flight = self.next_flight()
        ready = self.temp_dir / "finish-ready"
        release = self.temp_dir / "finish-release"
        state = self.temp_dir / "jq-finish-state"
        os.mkfifo(ready)
        os.mkfifo(release)
        process = subprocess.Popen(
            self.command(flight),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.environment(
                REAL_JQ=str(self.real_jq),
                FAKE_JQ_STATE=str(state),
                FAKE_JQ_RENDER_MODE="finish_wait",
                FAKE_READY=str(ready),
                FAKE_RELEASE=str(release),
            ),
        )
        with ready.open() as ready_signal:
            self.assertEqual(ready_signal.read().strip(), "ready")
        os.kill(process.pid, signal.SIGTERM)
        with release.open("w") as release_signal:
            release_signal.write("release\n")
        stdout, stderr = process.communicate(timeout=10)
        self.assert_terminal(
            RunResult(subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr), self.expected_run_dir(flight)),
            "COMPLETE",
        )

    def run_interrupted_runner(self, signal_number: int) -> RunResult:
        flight = self.next_flight()
        ready = self.temp_dir / f"ready-{flight}"
        release = self.temp_dir / f"release-{flight}"
        carrier_pid_ref = self.temp_dir / f"carrier-{flight}.pid"
        os.mkfifo(ready)
        os.mkfifo(release)
        process = subprocess.Popen(
            self.command(flight),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.environment("interrupt_wait", FAKE_READY=str(ready), FAKE_RELEASE=str(release), FAKE_CARRIER_PID=str(carrier_pid_ref)),
        )
        with ready.open() as ready_signal:
            self.assertEqual(ready_signal.read().strip(), "ready")
        carrier_pid = int(carrier_pid_ref.read_text())
        os.kill(process.pid, signal_number)
        with release.open("w") as release_signal:
            release_signal.write("release\n")
        stdout, stderr = process.communicate(timeout=10)
        result = RunResult(subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr), self.expected_run_dir(flight))
        self.assert_terminal(result, "INTERRUPTED")
        with self.assertRaises(ProcessLookupError):
            os.kill(carrier_pid, 0)
        return result

    def test_sigterm_to_runner_writes_interrupted_terminal_status(self) -> None:
        self.run_interrupted_runner(signal.SIGTERM)

    def test_sigint_to_runner_writes_interrupted_terminal_status(self) -> None:
        self.run_interrupted_runner(signal.SIGINT)

    def test_time_lookup_failure_is_diagnostic_only(self) -> None:
        date = self.bin_dir / "date"
        date.write_text("#!/bin/bash\nexit 1\n")
        date.chmod(0o755)
        result = self.run_worker()
        self.assert_terminal(result, "COMPLETE")
        self.assertIsNone(result.status["started_at"])
        self.assertIsNone(result.status["finished_at"])
        self.assertIsNone(result.status["duration_seconds"])

    def test_nonzero_carrier_wins_even_with_complete_artifacts(self) -> None:
        result = self.run_worker("nonzero_with_artifacts")
        self.assert_terminal(result, "CARRIER_EXIT_NONZERO")
        self.assertEqual(result.status["carrier_exit"], 9)
        self.assertRegex(result.status["started_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertRegex(result.status["finished_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertIsInstance(result.status["duration_seconds"], int)

    def test_started_carrier_exit_127_is_nonzero_not_launch_failure(self) -> None:
        result = self.run_worker("exit_127")
        self.assert_terminal(result, "CARRIER_EXIT_NONZERO")
        self.assertEqual(result.status["carrier_exit"], 127)

    def test_healthy_wait_boundaries_do_not_end_a_zero_retry_flight(self) -> None:
        # retry_budget is caller transcript policy, not a runner timeout option.
        # Lean separately checks zero-budget wait/recovery routing on actual allowed/step.
        flight = self.next_flight()
        ready = self.temp_dir / "healthy-ready"
        release = self.temp_dir / "healthy-release"
        os.mkfifo(ready)
        os.mkfifo(release)
        ready_fd = os.open(ready, os.O_RDWR | os.O_NONBLOCK)
        release_fd = os.open(release, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(os.close, ready_fd)
        process = subprocess.Popen(
            self.command(flight), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
            env=self.environment("interrupt_wait", FAKE_READY=str(ready),
                                 FAKE_RELEASE=str(release),
                                 FAKE_CARRIER_PID=str(self.temp_dir / "healthy-pid")),
        )
        try:
            assert process.stdout is not None
            readable, _, _ = select.select([process.stdout], [], [], 10)
            self.assertTrue(readable, "watchdog: no launch receipt")
            self.assertEqual(json.loads(process.stdout.readline())["flight_id"], flight)
            readable, _, _ = select.select([ready_fd], [], [], 10)
            self.assertTrue(readable, "watchdog: carrier never opened")
            self.assertEqual(os.read(ready_fd, 64).strip(), b"ready")
            for _ in range(3):
                with self.assertRaises(subprocess.TimeoutExpired):
                    process.wait(timeout=0.05)  # finite observation, not a production stop
                self.assertFalse((self.expected_run_dir(flight) / "status.json").exists())
            os.write(release_fd, b"release\n")
            os.close(release_fd)
            release_fd = -1
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(stdout, "")
            # The fake really ends without artifacts: failure now, never during waiting.
            self.assertEqual(process.returncode, 1)
            status = json.loads((self.expected_run_dir(flight) / "status.json").read_text())
            self.assertEqual(status["status"], "NOT_COMPLETE")
            self.assertEqual(status["reason_code"], "RESULT_MISSING")
            self.assert_timestamped_progress(stderr)
        finally:
            if release_fd >= 0:
                os.write(release_fd, b"release\n")
                os.close(release_fd)
            if process.poll() is None:
                process.terminate()  # verification watchdog cleanup only
            process.communicate(timeout=10)

    def test_runner_waits_until_carrier_exits_after_artifacts_appear(self) -> None:
        flight = self.next_flight()
        ready = self.temp_dir / "carrier-ready"
        release = self.temp_dir / "carrier-release"
        exited = self.temp_dir / "carrier-exited"
        os.mkfifo(ready)
        os.mkfifo(release)
        process = subprocess.Popen(
            self.command(flight),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.environment("artifacts_then_wait", FAKE_READY=str(ready), FAKE_RELEASE=str(release), FAKE_EXITED=str(exited)),
        )
        assert process.stdout is not None
        readable, _, _ = select.select([process.stdout], [], [], 10)
        self.assertTrue(readable, "no launch receipt became readable")
        receipt_line = process.stdout.readline()
        self.assertEqual(json.loads(receipt_line)["flight_id"], flight)
        with ready.open() as ready_signal:
            self.assertEqual(ready_signal.read().strip(), "ready")
        status_existed_while_running = self.expected_run_dir(flight).joinpath("status.json").exists()
        runner_returned_while_carrier_live = process.poll() is not None
        with release.open("w") as release_signal:
            release_signal.write("release\n")
        stdout_tail, stderr = process.communicate(timeout=10)
        stdout = receipt_line + stdout_tail
        self.assertEqual(stdout_tail, "")
        self.assertFalse(status_existed_while_running)
        self.assertFalse(runner_returned_while_carrier_live, "runner returned before the live carrier exited")
        self.assertEqual(exited.read_text().strip(), "exited")
        result = RunResult(subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr), self.expected_run_dir(flight))
        self.assert_terminal(result, "COMPLETE")
        self.assert_timestamped_progress(stderr)
        self.assertIn("carrier exited rc=0", stderr)
        self.assertIn("status        COMPLETE", stderr)
        self.assertIn("reason_code   COMPLETE", stderr)
        self.assertIn("verdict       propose", stderr)
        self.assertRegex(stderr, r"duration\s+\d+s")

    def test_missing_result_is_not_complete(self) -> None:
        self.assert_terminal(self.run_worker("nothing"), "RESULT_MISSING")

    def test_invalid_or_truncated_json_is_rejected(self) -> None:
        self.assert_terminal(self.run_worker("invalid_json"), "ENVELOPE_INVALID")

    def test_envelope_shape_and_log_ref_are_strict(self) -> None:
        for mode in ["extra_key", "missing_key", "empty_log_ref"]:
            with self.subTest(mode=mode):
                self.assert_terminal(self.run_worker(mode), "ENVELOPE_INVALID")

    def test_runner_rendered_minimum_envelope_passes_production_validator(self) -> None:
        expected_conclusions = {
            "thinking": {"verdict": "propose"},
            "review": {"verdict": "approve"},
            "termination": {"verdict": "abstain"},
            "implementation": {},
        }
        for stage, expected_conclusion in expected_conclusions.items():
            with self.subTest(stage=stage):
                result = self.run_worker("minimum_envelope", stage=stage)
                self.assert_terminal(result, "COMPLETE")
                envelope = json.loads(result.run_dir.joinpath("result.json").read_text())
                self.assertEqual(envelope["conclusion"], expected_conclusion)
                self.assertEqual(envelope["log_ref"], str(result.run_dir / "worker.stdout.log"))
                brief = result.run_dir.joinpath("brief.md").read_text()
                self.assertEqual(brief.count("Minimum structurally accepted envelope shape: "), 1)
                self.assertIn("must populate \"conclusion\" with the complete structured result", brief)
                self.assertIn("must not submit this skeletal example unchanged", brief)

    def test_minimum_envelope_render_failure_does_not_launch_carrier(self) -> None:
        self.install_skeleton_render_failing_jq()
        result = self.run_worker(extra_env={"REAL_JQ": str(self.real_jq)})
        self.assert_terminal(result, "INTERNAL_ERROR")
        self.assertNotIn("carrier starting", result.process.stderr)
        self.assertIn("INTERNAL_ERROR: cannot render minimum envelope shape", result.process.stderr)
        self.assertNotIn(
            "Minimum structurally accepted envelope shape:",
            result.run_dir.joinpath("brief.md").read_text(),
        )

    def test_envelope_field_types_are_strict(self) -> None:
        for mode in ["conclusion_string", "log_ref_number", "verdict_number"]:
            with self.subTest(mode=mode):
                self.assert_terminal(self.run_worker(mode, stage="thinking"), "ENVELOPE_INVALID")

    def test_stage_verdict_requires_exact_set_member(self) -> None:
        for stage, verdict in [("thinking", "unexpected"), ("thinking", "propose|revise"), ("review", "approve|comment"), ("termination", "satisfied|abstain")]:
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
                    outside = self.temp_dir / self.next_flight()
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
                    outside = self.temp_dir / self.next_flight()
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

    def test_default_root_is_dot_sshx_under_home_when_sshx_home_is_unset(self) -> None:
        flight = self.next_flight()
        env = self.environment(); env.pop("SSHX_HOME"); env["HOME"] = str(self.temp_dir); env["LC_ALL"] = "C.UTF-8"
        process = subprocess.run(self.command(flight), input="brief\n", capture_output=True, text=True, env=env, timeout=10)
        run_dir = self.expected_run_dir(flight, base=self.temp_dir / ".sshx")
        self.assert_terminal(RunResult(process, run_dir), "COMPLETE")
        self.assertEqual(stat.S_IMODE((self.temp_dir / ".sshx").stat().st_mode), 0o700)
        self.assertEqual(json.loads(run_dir.joinpath("status.json").read_text())["run_dir"], str(run_dir))

    def test_empty_sshx_home_falls_back_to_home_and_trailing_slashes_are_stripped(self) -> None:
        for label, env_home in [("empty SSHX_HOME", ""), ("trailing slash HOME", None)]:
            with self.subTest(case=label):
                flight = self.next_flight()
                env = self.environment(); env["HOME"] = str(self.temp_dir) + ("/" if env_home is None else "")
                if env_home is None: env.pop("SSHX_HOME")
                else: env["SSHX_HOME"] = env_home
                process = subprocess.run(self.command(flight), input="brief\n", capture_output=True, text=True, env=env, timeout=10)
                self.assert_terminal(RunResult(process, self.expected_run_dir(flight, base=self.temp_dir / ".sshx")), "COMPLETE")

    def test_missing_home_and_sshx_home_is_unavailable_with_diagnostic(self) -> None:
        flight = self.next_flight()
        env = self.environment(); env.pop("SSHX_HOME"); env.pop("HOME", None)
        process = subprocess.run(self.command(flight), input="brief\n", capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(process.returncode, 1); self.assertIn("RUN_DIR_UNAVAILABLE", process.stderr)
        self.assertIn("neither SSHX_HOME nor HOME is set", process.stderr)
        self.assertNotIn("carrier starting", process.stderr)

    def test_run_root_is_created_on_first_use_with_private_mode(self) -> None:
        fresh_root = self.temp_dir / "fresh-root"
        flight = self.next_flight()
        env = self.environment(); env["SSHX_HOME"] = str(fresh_root)
        self.assertFalse(fresh_root.exists())
        process = subprocess.run(self.command(flight), input="brief\n", capture_output=True, text=True, env=env, timeout=10)
        self.assert_terminal(RunResult(process, self.expected_run_dir(flight, base=fresh_root)), "COMPLETE")
        self.assertEqual(stat.S_IMODE(fresh_root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((fresh_root / flight).stat().st_mode), 0o700)

    def test_symlink_run_root_is_rejected_without_writing_through_it(self) -> None:
        target = self.temp_dir / "target"; target.mkdir()
        link = self.temp_dir / "root-link"; link.symlink_to(target, target_is_directory=True)
        dangling = self.temp_dir / "dangling-link"; dangling.symlink_to(self.temp_dir / "missing-target", target_is_directory=True)
        for label, root in [("resolving", link), ("dangling", dangling)]:
            with self.subTest(root=label):
                flight = self.next_flight()
                env = self.environment(); env["SSHX_HOME"] = str(root)
                process = subprocess.run(self.command(flight), input="brief\n", capture_output=True, text=True, env=env, timeout=10)
                self.assertEqual(process.returncode, 1); self.assertIn("RUN_DIR_UNAVAILABLE", process.stderr)
                self.assertIn("run root from SSHX_HOME must be a non-symlink writable directory", process.stderr)
                self.assertNotIn("carrier starting", process.stderr)
        self.assertEqual(list(target.iterdir()), [])
        self.assertFalse((self.temp_dir / "missing-target").exists())

    def test_run_root_validation_is_fail_closed(self) -> None:
        unwritable = self.temp_dir / "unwritable"; unwritable.mkdir(); unwritable.chmod(0o500)
        not_directory = self.temp_dir / "not-directory"; not_directory.write_text("not a directory\n")
        values = ["relative", str(self.temp_dir / "missing-parent" / "root"), str(unwritable), str(unwritable / "child"), str(not_directory)]
        for value in values:
            with self.subTest(value=value):
                env = self.environment(); env["SSHX_HOME"] = value
                process = subprocess.run(self.command(self.next_flight()), input="brief\n", capture_output=True, text=True, env=env, timeout=10)
                self.assertEqual(process.returncode, 1); self.assertIn("RUN_DIR_UNAVAILABLE", process.stderr)
                self.assertNotIn("carrier starting", process.stderr)
        unwritable.chmod(0o700)
        self.assertFalse((self.temp_dir / "missing-parent").exists())

    def fresh_command(self) -> list[str]:
        return ["/bin/bash", str(RUNNER), "--stage", "implementation", "--work-target", str(ROOT)]

    def test_first_launch_allocates_before_blocked_input_and_retries_keep_identity(self) -> None:
        process = subprocess.Popen(self.fresh_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.environment())
        assert process.stdout is not None
        readable, _, _ = select.select([process.stdout], [], [], 10)
        self.assertTrue(readable, "receipt must precede stdin EOF")
        receipt_line = process.stdout.readline()
        receipt = json.loads(receipt_line)
        self.assertRegex(receipt["flight_id"], r"^[0-9a-f]{24}$")
        self.assertEqual(receipt["attempt"], 1)
        self.assertIsNone(process.poll())
        tail, stderr = process.communicate("brief\n", timeout=10)
        self.assertEqual(tail, "")
        self.assertEqual(process.returncode, 0, stderr)
        first = Path(receipt["run_dir"])
        self.assertEqual(json.loads((first / "status.json").read_text())["reason_code"], "COMPLETE")
        retry = self.run_worker(flight_id=receipt["flight_id"], attempt="2", stage="implementation")
        self.assert_terminal(retry, "COMPLETE")
        self.assertNotEqual(first, retry.run_dir)
        original_status = (retry.run_dir / "status.json").read_bytes()
        collision = self.run_worker(flight_id=receipt["flight_id"], attempt="2", stage="implementation")
        self.assertEqual(collision.process.returncode, 1)
        self.assertIn("RUN_DIR_COLLISION", collision.process.stderr)
        self.assertEqual(json.loads(collision.process.stdout), json.loads(retry.process.stdout))
        self.assertEqual((retry.run_dir / "status.json").read_bytes(), original_status)

    def test_parallel_first_launches_allocate_unique_identities(self) -> None:
        before = int(time.time())
        processes = [subprocess.Popen(self.fresh_command(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.environment()) for _ in range(6)]
        receipts = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stderr)
            receipt = json.loads(stdout)
            receipts.append(receipt)
            self.assertEqual(receipt["attempt"], 1)
            self.assertLessEqual(before, int(receipt["flight_id"][:8], 16))
            self.assertLessEqual(int(receipt["flight_id"][:8], 16), int(time.time()))
        self.assertEqual(len({receipt["flight_id"] for receipt in receipts}), len(processes))
        self.assertEqual(len({receipt["run_dir"] for receipt in receipts}), len(processes))

    def test_pre_receipt_failure_then_pending_and_bound_retries(self) -> None:
        failed = subprocess.run(
            self.fresh_command(), input="brief\n", capture_output=True, text=True,
            env=self.environment(SSHX_HOME="relative"), timeout=10,
        )
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(failed.stdout, "")
        self.assertIn("RUN_DIR_UNAVAILABLE", failed.stderr)
        self.assertFalse((self.temp_dir / "sshx").exists())

        # Retry the same pending record with no identity options. Carrier launch
        # succeeds; incomplete worker artifacts require the subsequent bound retry.
        pending = subprocess.run(
            self.fresh_command(), input="brief\n", capture_output=True, text=True,
            env=self.environment("missing_sentinel"), timeout=10,
        )
        receipt = json.loads(pending.stdout)
        self.assertRegex(receipt["flight_id"], r"^[0-9a-f]{24}$")
        self.assertEqual(receipt["attempt"], 1)
        first = RunResult(pending, Path(receipt["run_dir"]))
        self.assert_terminal(first, "SENTINEL_MISSING")
        assert first.status is not None
        self.assertEqual(first.status["carrier_exit"], 0)
        first_status = (first.run_dir / "status.json").read_bytes()
        first_result = (first.run_dir / "result.json").read_bytes()

        bound = self.run_worker(flight_id=receipt["flight_id"], attempt="2", stage="implementation")
        self.assert_terminal(bound, "COMPLETE")
        bound_receipt = json.loads(bound.process.stdout)
        self.assertEqual(bound_receipt["flight_id"], receipt["flight_id"])
        self.assertEqual(bound_receipt["attempt"], 2)
        self.assertNotEqual(first.run_dir, bound.run_dir)
        self.assertEqual((first.run_dir / "status.json").read_bytes(), first_status)
        self.assertEqual((first.run_dir / "result.json").read_bytes(), first_result)

    def test_fresh_receipt_survives_post_allocation_failures(self) -> None:
        cases = [
            self.environment(SSHX_HOME=str(self.temp_dir / "missing-parent" / "runs")),
            self.environment("nonzero_with_artifacts"),
            self.environment("projection_collision", FAKE_COLLISION_TARGET="status.json.tmp", FAKE_COLLISION_SHAPE="directory", FAKE_OUTSIDE=str(self.temp_dir / "unused")),
        ]
        for env in cases:
            with self.subTest(env=env["FAKE_MODE"]):
                process = subprocess.run(self.fresh_command(), input="brief\n", capture_output=True, text=True, env=env, timeout=10)
                self.assertEqual(process.returncode, 1, process.stderr)
                receipt = json.loads(process.stdout)
                self.assertEqual(receipt["attempt"], 1)
                query = subprocess.run(["/bin/bash", str(RUNNER), "--project-paths", "--flight-id", receipt["flight_id"], "--attempt", "1"], capture_output=True, text=True, env=env, check=True)
                self.assertEqual(receipt, json.loads(query.stdout))

    def test_first_launch_preallocation_failures_have_no_receipt(self) -> None:
        no_home = self.environment()
        no_home.pop("HOME", None)
        no_home.pop("SSHX_HOME")
        no_parser = self.environment(PATH=str(self.temp_dir))
        for env in [no_home, no_parser, self.environment(SSHX_HOME="relative")]:
            process = subprocess.run(self.fresh_command(), input="brief\n", capture_output=True, text=True, env=env, timeout=10)
            self.assertEqual(process.returncode, 1)
            self.assertEqual(process.stdout, "")
        for arguments in [["--flight-id", VALID_ID], ["--attempt", "1"], ["--flight-id", "", "--attempt", "1"], ["--new-flight-id"]]:
            process = subprocess.run(self.fresh_command() + arguments, input="brief\n", capture_output=True, text=True, env=self.environment(), timeout=10)
            self.assertEqual(process.returncode, 64, process.stderr)
            self.assertEqual(process.stdout, "")
        self.assertFalse((self.temp_dir / "sshx").exists())

    def test_run_hierarchy_rejects_symlinked_flight_directory_before_launch(self) -> None:
        outside = self.temp_dir / "outside"; outside.mkdir()
        flight = self.next_flight()
        (self.temp_dir / "sshx").mkdir()
        (self.temp_dir / "sshx" / flight).symlink_to(outside, target_is_directory=True)
        process = subprocess.run(self.command(flight), input="brief\n", capture_output=True, text=True, env=self.environment(), timeout=10)
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertIn("RUN_DIR_UNAVAILABLE", process.stderr)
        self.assertEqual(list(outside.iterdir()), [])

    def test_concurrent_flight_paths_are_disjoint(self) -> None:
        a, b = self.next_flight(), self.next_flight()
        p1 = subprocess.Popen(self.command(a), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.environment())
        p2 = subprocess.Popen(self.command(b), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.environment())
        out1, err1 = p1.communicate("brief a\n", timeout=10); out2, err2 = p2.communicate("brief b\n", timeout=10)
        self.assertEqual(p1.returncode, 0, err1); self.assertEqual(p2.returncode, 0, err2)
        self.assertEqual(json.loads(out1)["flight_id"], a)
        self.assertEqual(json.loads(out2)["flight_id"], b)
        status_a = json.loads(self.expected_run_dir(a).joinpath("status.json").read_text())
        status_b = json.loads(self.expected_run_dir(b).joinpath("status.json").read_text())
        self.assertNotEqual(status_a["run_dir"], status_b["run_dir"])

    def test_symlinks_cannot_impersonate_worker_artifacts(self) -> None:
        self.assert_terminal(self.run_worker("symlink_result"), "RESULT_MISSING")
        self.assert_terminal(self.run_worker("symlink_sentinel"), "SENTINEL_MISSING")

    def test_missing_jq_fails_without_parser_fallback(self) -> None:
        flight = self.next_flight(); empty = self.temp_dir / "empty-path"; empty.mkdir()
        env = self.environment(); env["PATH"] = str(empty)
        process = subprocess.run(self.command(flight), input="brief\n", capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(process.returncode, 1); self.assertIn("PARSER_UNAVAILABLE", process.stderr); self.assertFalse(self.expected_run_dir(flight).exists())

    def test_combined_failure_reports_parser_before_run_dir(self) -> None:
        flight = self.next_flight(); empty = self.temp_dir / "empty-path-precedence"; empty.mkdir()
        env = self.environment(); env["PATH"] = str(empty); env["SSHX_HOME"] = "relative"
        process = subprocess.run(self.command(flight), input="brief\n", capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(process.returncode, 1); self.assertIn("PARSER_UNAVAILABLE", process.stderr); self.assertNotIn("RUN_DIR_UNAVAILABLE", process.stderr)

    def test_codex_preflight_reports_launch_failed(self) -> None:
        flight = self.next_flight()
        no_codex = self.temp_dir / "no-codex"; no_codex.mkdir(); (no_codex / "jq").symlink_to((self.bin_dir / "jq").resolve())
        env = self.environment(); env["PATH"] = f"{no_codex}:/bin:/usr/bin"
        process = subprocess.run(self.command(flight), input="brief\n", capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertEqual(process.stdout, "")
        self.assertIn("LAUNCH_FAILED", process.stderr)
        self.assertFalse(self.expected_run_dir(flight).exists())

    def test_argument_validation_rejects_missing_duplicate_unknown_and_invalid_values(self) -> None:
        valid = self.command(VALID_ID)
        cases = [valid[:8] + valid[10:], valid + ["--stage", "thinking"], valid + ["--unknown", "value"], self.command("../escape"), self.command("bad/id"), self.command("csa-0909-think-worth"), self.command(VALID_ID.upper()), self.command(VALID_ID[:-1]), self.command(VALID_ID + "0"), self.command(VALID_ID[:-1] + "g"), self.command(VALID_ID, attempt="0"), self.command(VALID_ID, attempt="one"), self.command(VALID_ID, stage="other"), self.command(VALID_ID, sandbox="read-only"), self.command(VALID_ID, sandbox=""), self.command(VALID_ID, work_target="relative")]
        for command in cases:
            process = subprocess.run(command, input="brief\n", capture_output=True, text=True, env=self.environment(), timeout=10)
            self.assertEqual(process.returncode, 64, process.stderr); self.assertIn("USAGE_ERROR", process.stderr)
            flight_id = command[command.index("--flight-id") + 1]
            self.assertFalse(self.expected_run_dir(flight_id).exists())
            self.assertNotIn("carrier starting", process.stderr)

    def test_flight_id_dot_is_rejected(self) -> None:
        process = subprocess.run(self.command("."), input="brief\n", capture_output=True, text=True, env=self.environment(), timeout=10)
        self.assertEqual(process.returncode, 64); self.assertIn("USAGE_ERROR", process.stderr)

    def test_control_characters_cannot_enter_streamed_path_fields(self) -> None:
        spec = re.sub(r"\s+", " ", SPEC.read_text())
        self.assertIn("`work-target` is absolute and contains neither LF (`0x0A`) nor CR (`0x0D`)", spec)
        self.assertIn("The run root must be absolute and contain neither LF nor CR", spec)
        self.assertIn("The run root and the runner-created flight directory are rejected when symbolic links", spec)
        locales = ["C", "C.UTF-8", "en_US.UTF-8"]
        for locale in locales:
            for character in ["\n", "\r"]:
                with self.subTest(locale=locale, field="work_target", character=repr(character)):
                    flight = self.next_flight()
                    process = subprocess.run(
                        self.command(flight, work_target=f"/tmp/a{character}b"), input="brief\n", capture_output=True,
                        text=True, env=self.environment(LC_ALL=locale), timeout=10,
                    )
                    self.assertEqual(process.returncode, 64); self.assertIn("USAGE_ERROR", process.stderr)
                    self.assertFalse(self.expected_run_dir(flight).exists())
            for character in ["\n", "\r"]:
                with self.subTest(locale=locale, field="SSHX_HOME", character=repr(character)):
                    control_tmp = self.temp_dir / f"tmp-{locale.replace('.', '_')}-{ord(character):x}-{self.counter}{character}path"; control_tmp.mkdir()
                    flight = self.next_flight()
                    process = subprocess.run(
                        self.command(flight), input="brief\n", capture_output=True, text=True,
                        env=self.environment(LC_ALL=locale, SSHX_HOME=str(control_tmp)), timeout=10,
                    )
                    self.assertEqual(process.returncode, 1); self.assertIn("RUN_DIR_UNAVAILABLE", process.stderr)
                    self.assertFalse(self.expected_run_dir(flight, base=control_tmp).exists())

        legal_samples = [
            ("space", "a b"), ("Chinese", "中文"), ("Japanese", "日本語"),
            ("single quote", "a'b"), ("double quote", 'a"b'), ("command syntax", "$(echo unsafe)"),
            ("backslash", r"a\\b"), ("emoji", "🚀"), ("ZWJ emoji", "👩‍💻"),
            ("ZWNJ", "\u200c"), ("TAB", "\t"), ("C1", "\u0085"),
            ("Unicode line separator", "\u2028"),
        ]
        for locale in locales:
            for label, character in legal_samples:
                with self.subTest(locale=locale, sample=label):
                    flight = self.next_flight()
                    process = subprocess.run(
                        self.command(flight, work_target=f"/tmp/a{character}b"), input="brief\n", capture_output=True,
                        text=True, env=self.environment(LC_ALL=locale), timeout=10,
                    )
                    self.assertEqual(process.returncode, 0, process.stderr)
                    self.assertEqual(json.loads(self.expected_run_dir(flight).joinpath("status.json").read_text())["status"], "COMPLETE")

    def test_legal_samples_pass_in_sshx_home(self) -> None:
        legal_samples = [
            ("space", "a b"), ("Chinese", "中文"), ("Japanese", "日本語"),
            ("single quote", "a'b"), ("double quote", 'a"b'), ("command syntax", "$(echo unsafe)"),
            ("backslash", r"a\\b"), ("emoji", "🚀"), ("ZWJ emoji", "👩‍💻"),
            ("ZWNJ", "\u200c"), ("TAB", "\t"), ("C1", "\u0085"),
            ("Unicode line separator", "\u2028"),
        ]
        for locale in ["C", "C.UTF-8", "en_US.UTF-8"]:
            for label, character in legal_samples:
                with self.subTest(locale=locale, sample=label):
                    control_tmp = self.temp_dir / f"legal-{locale.replace('.', '_')}-{self.counter}-{label}-{character}"
                    control_tmp.mkdir()
                    flight = self.next_flight()
                    process = subprocess.run(
                        self.command(flight), input="brief\n", capture_output=True, text=True,
                        env=self.environment(LC_ALL=locale, SSHX_HOME=str(control_tmp)), timeout=10,
                    )
                    self.assertEqual(process.returncode, 0, process.stderr)
                    self.assertEqual(json.loads(self.expected_run_dir(flight, base=control_tmp).joinpath("status.json").read_text())["status"], "COMPLETE")

    def test_control_character_guard_mutation_is_locale_sensitive(self) -> None:
        mutated = self.temp_dir / "mutated-runner.sh"
        source = RUNNER.read_text()
        replacements = [
            (
                'case "$work_target" in *$\'\\n\'*|*$\'\\r\'*) usage_error "--work-target must not contain LF or CR"; return 1 ;; esac',
                'case "$work_target" in *[[:cntrl:]]*) usage_error "--work-target must not contain control characters"; return 1 ;; esac',
            ),
            (
                'case "$run_root" in *$\'\\n\'*|*$\'\\r\'*) reason=RUN_DIR_UNAVAILABLE; return 1 ;; esac',
                'case "$run_root" in *[[:cntrl:]]*) reason=RUN_DIR_UNAVAILABLE; return 1 ;; esac',
            ),
        ]
        for anchor, replacement in replacements:
            self.assertEqual(source.count(anchor), 1, f"mutation anchor must appear exactly once: {anchor}")
        mutated_source = source
        for anchor, replacement in replacements:
            mutated_source = mutated_source.replace(anchor, replacement)
        self.assertNotEqual(mutated_source, source, "mutation must change the runner source")
        mutated.write_text(mutated_source); mutated.chmod(0o755)
        outcomes = []
        for locale in ["C", "C.UTF-8"]:
            flight = self.next_flight()
            process = subprocess.run(
                self.command_for_runner(mutated, flight, work_target="/tmp/a\u0085b"), input="brief\n",
                capture_output=True, text=True, env=self.environment(LC_ALL=locale), timeout=10,
            )
            outcomes.append((locale, process.returncode))
        self.assertEqual(outcomes, [("C", 0), ("C.UTF-8", 64)])

    def test_receipt_delivery_failure_prevents_directory_and_carrier(self) -> None:
        flight = self.next_flight()
        target = self.temp_dir / "read-only-stdout"
        target.write_text("unchanged")
        with target.open("rb") as stdout:
            process = subprocess.run(self.command(flight), input="brief\n", stdout=stdout, stderr=subprocess.PIPE, text=True, env=self.environment(), timeout=10)
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertIn("INTERNAL_ERROR", process.stderr)
        self.assertFalse(self.expected_run_dir(flight).exists())
        self.assertEqual(target.read_text(), "unchanged")

    def test_closed_receipt_pipe_fails_without_creating_run_root(self) -> None:
        reader, writer = os.pipe()
        os.close(reader)
        try:
            process = subprocess.run(self.fresh_command(), stdin=subprocess.DEVNULL, stdout=writer, stderr=subprocess.PIPE, text=True, env=self.environment(), timeout=10)
        finally:
            os.close(writer)
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertIn("INTERNAL_ERROR", process.stderr)
        self.assertFalse((self.temp_dir / "sshx").exists())

    def test_pretty_status_rendering_source_regression(self) -> None:
        runner = RUNNER.read_text()
        self.assertNotIn('"$jq_path" -cn', runner)
        self.assertGreaterEqual(runner.count('"$jq_path" -n'), 3)
        result = self.run_worker("nothing")
        self.assertGreater(len(result.run_dir.joinpath("status.json").read_text().splitlines()), 1)

    def test_runner_verdict_sets_match_skill_contract(self) -> None:
        text = SKILL.read_text()
        thinking = text.split("## Thinking Panel", 1)[1].split("## Design Truth Table", 1)[0].split("Each seat returns one of:", 1)[1]
        review = text.split("## Review Triplet", 1)[1].split("## Review Truth Table", 1)[0].split("Each reviewer returns one of:", 1)[1]
        termination = text.split("## Termination Gate", 1)[1].split("## Termination Truth Table", 1)[0].split("Each termination seat returns one of:", 1)[1]
        expected = {
            "thinking": set(re.findall(r"^- `([^`]+)`$", thinking, re.MULTILINE)),
            "review": set(re.findall(r"^- `([^`]+)`$", review, re.MULTILINE)),
            "termination": set(re.findall(r"^- `([^`]+)`$", termination, re.MULTILINE)),
        }
        declaration = re.search(r"stage_verdict_specs='([^']*)'", RUNNER.read_text())
        assert declaration is not None
        runner_sets = {}
        for entry in declaration.group(1).split(";"):
            name, values = entry.split("=", 1); runner_sets[name] = set(values.split("|")) if values else set()
        self.assertEqual(set(runner_sets), {*expected, "implementation"})
        for stage, verdicts in expected.items():
            self.assertEqual(runner_sets[stage], verdicts)
        self.assertEqual(runner_sets["implementation"], set())
        for stage, verdicts in expected.items():
            for verdict in verdicts: self.assert_terminal(self.run_worker(stage=stage, extra_env={"FAKE_VERDICT": verdict}), "COMPLETE")
        self.assert_terminal(self.run_worker(stage="implementation", extra_env={"FAKE_VERDICT": "other"}), "COMPLETE")

    def test_runner_never_produces_worker_owned_artifacts(self) -> None:
        result = self.run_worker("nothing")
        self.assert_terminal(result, "RESULT_MISSING")
        for name in ["result.json", "completion.sentinel"]:
            with self.subTest(name=name):
                with self.assertRaises(FileNotFoundError):
                    os.lstat(result.run_dir / name)


if __name__ == "__main__":
    unittest.main()
